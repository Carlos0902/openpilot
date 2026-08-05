"""LLM client wrapper that records trajectory evidence for key model calls."""

from __future__ import annotations

import json
import hashlib
import inspect
import time
from typing import Any, Callable
from types import SimpleNamespace
from uuid import uuid4

from core.exceptions import ErrorCategory, classify_error
from core.llm import LLMRequest, LLMResponse, normalized_provider_endpoint
from core.reasoning import resolve_reasoning_policy
from metadata import FailureMetadata, LLMRequestMetadata, LLMResponseMetadata
from metadata.base import json_safe
from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks


class TrajectoryLLMClientProxy:
    """Proxy an LLM client and emit metadata-first trajectory evidence."""

    def __init__(
        self,
        client: Any,
        *,
        hooks: RuntimeDiagnosticsHooks,
        task_id_getter: Callable[[], str] | None = None,
        session_id_getter: Callable[[], str] | None = None,
        phase_getter: Callable[[], str] | None = None,
        goal_getter: Callable[[], str] | None = None,
        recovery_handler: Any | None = None,
    ) -> None:
        self._client = client
        self._hooks = hooks
        self._task_id_getter = task_id_getter
        self._session_id_getter = session_id_getter
        self._phase_getter = phase_getter
        self._goal_getter = goal_getter
        self._recovery_handler = recovery_handler
        self._request_ordinals: dict[str, int] = {}

    def set_recovery_handler(self, handler: Any | None) -> None:
        self._recovery_handler = handler

    def reset_recovery_ordinals(self) -> None:
        self._request_ordinals.clear()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def complete(
        self,
        request: LLMRequest,
        max_retries: int = 3,
        use_cache: bool = True,
        stream_callback=None,
    ) -> LLMResponse:
        task_id = self._value(self._task_id_getter)
        session_id = self._value(self._session_id_getter)
        phase = self._value(self._phase_getter)
        goal = self._value(self._goal_getter)
        call_id = f"llm_{uuid4().hex}"
        request_ordinal = self._request_ordinals.get(task_id, 0) + 1
        self._request_ordinals[task_id] = request_ordinal
        settings = getattr(self._client, "settings", None) or SimpleNamespace()
        resolved_reasoning = resolve_reasoning_policy(request.reasoning_policy, settings)
        request_hash = self._request_hash(
            request,
            settings=settings,
            resolved_reasoning=resolved_reasoning,
        )

        request_diagnostics = self._request_diagnostics(request)
        request_metadata = LLMRequestMetadata(
            task=goal or self._request_task(request),
            purpose=self._request_purpose(request),
            context_selection=request.context_selection,
            reasoning_policy=request.reasoning_policy,
            resolved_reasoning_policy=resolved_reasoning,
            trace_info={
                **self._request_trace_info(request),
                # The provider-bound request hash is audit evidence for
                # attempt/replay correlation; it is never sent as prompt data.
                "request_hash": request_hash,
                "request_ordinal": request_ordinal,
                "diagnostics": request_diagnostics,
            },
        )
        self._hooks.on_llm_requested(
            request_metadata=request_metadata,
            task_id=task_id,
            session_id=session_id,
            phase=phase,
            call_id=call_id,
            request_snapshot=request.model_dump(mode="python"),
        )

        handler = self._recovery_handler
        replay = getattr(handler, "replay_llm_response", None)
        replayed_response = (
            replay(task_id, request_ordinal, request_hash)
            if callable(replay)
            else None
        )
        if replayed_response is not None:
            self._record_response(
                replayed_response,
                task_id=task_id,
                session_id=session_id,
                phase=phase,
                call_id=call_id,
                duration_ms=0,
                recovery_replay=True,
                request_hash=request_hash,
                request_ordinal=request_ordinal,
            )
            return replayed_response

        prepare = getattr(handler, "prepare_llm_request", None)
        if callable(prepare) and not prepare(task_id, request_ordinal, request_hash):
            raise RuntimeError("LLM request was not sent because its prepared checkpoint was not durable")

        started_at = time.monotonic()
        try:
            response = self._complete_client(
                request,
                max_retries=max_retries,
                use_cache=use_cache,
                stream_callback=stream_callback,
            )
        except Exception as exc:
            exception_context = getattr(exc, "context", None)
            if not isinstance(exception_context, dict):
                exception_context = getattr(exc, "details", None)
            if not isinstance(exception_context, dict):
                exception_context = {}
            retry_history = exception_context.get("transport_retry_history")
            if not isinstance(retry_history, list):
                retry_history = []
            category = getattr(exc, "category", None)
            provider_attempt = {
                "attempt_id": call_id,
                "request_ordinal": request_ordinal,
                "request_hash": request_hash,
                "provider": str(getattr(settings, "provider", "") or ""),
                "model": str(getattr(settings, "model", "") or ""),
                "endpoint": normalized_provider_endpoint(
                    str(getattr(settings, "base_url", "") or "")
                ),
                "transport_attempted": True,
                "usage": dict(getattr(exc, "usage", {}) or {}),
                "finish_reason": getattr(exc, "finish_reason", None),
                "response_length": len(str(getattr(exc, "response_text", "") or "")),
                "json_repair_attempts": int(exception_context.get("json_repair_attempts") or 0),
                "transport_retry_count": max(0, len(retry_history) - 1),
                "error_category": str(getattr(category, "value", category) or ""),
            }
            self._hooks.on_llm_failed(
                failure=FailureMetadata(
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    recoverable=self._is_recoverable_llm_error(exc),
                    retry_recommended=self._is_recoverable_llm_error(exc),
                    details={
                        "trace_info": self._request_trace_info(request),
                        "diagnostics": request_diagnostics,
                        "response_format": request.response_format,
                        "provider_attempt": provider_attempt,
                    },
                ),
                task_id=task_id,
                session_id=session_id,
                phase=phase,
                call_id=call_id,
                failed_response_text=str(getattr(exc, "response_text", "") or ""),
            )
            raise

        observe = getattr(handler, "observe_llm_response", None)
        if callable(observe) and not observe(task_id, request_ordinal, request_hash, response):
            raise RuntimeError("LLM response was not applied because its observed checkpoint was not durable")
        duration_ms = int((time.monotonic() - started_at) * 1000)
        self._record_response(
            response,
            task_id=task_id,
            session_id=session_id,
            phase=phase,
            call_id=call_id,
            duration_ms=duration_ms,
            recovery_replay=False,
            request_hash=request_hash,
            request_ordinal=request_ordinal,
        )
        return response

    def _complete_client(
        self,
        request: LLMRequest,
        *,
        max_retries: int,
        use_cache: bool,
        stream_callback: Any,
    ) -> LLMResponse:
        complete = self._client.complete
        parameters = inspect.signature(complete).parameters
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        optional = {
            "max_retries": max_retries,
            "use_cache": use_cache,
            "stream_callback": stream_callback,
        }
        kwargs = {
            name: value
            for name, value in optional.items()
            if accepts_kwargs or name in parameters
        }
        return complete(request, **kwargs)

    def _record_response(
        self,
        response: LLMResponse,
        *,
        task_id: str,
        session_id: str,
        phase: str,
        call_id: str,
        duration_ms: int,
        recovery_replay: bool,
        request_hash: str,
        request_ordinal: int,
    ) -> None:
        settings = getattr(self._client, "settings", None)
        usage = getattr(response, "usage", None)
        provider_details = getattr(response, "provider_details", None)
        parsed_json = getattr(response, "parsed_json", None)
        content = str(getattr(response, "content", "") or "")
        response_metadata = LLMResponseMetadata(
            model=str(getattr(response, "model", "") or getattr(settings, "model", "") or ""),
            provider=str(
                getattr(response, "provider", "")
                or getattr(settings, "provider", "")
                or ""
            ),
            usage=json_safe(usage) if isinstance(json_safe(usage), dict) else {},
            finish_reason=getattr(response, "finish_reason", None),
            provider_details={
                **self._json_dict(provider_details),
                "duration_ms": duration_ms,
                "attempt_id": call_id,
                "request_ordinal": request_ordinal,
                "request_hash": request_hash,
                "provider_endpoint": normalized_provider_endpoint(
                    str(getattr(settings, "base_url", "") or "")
                ),
                "attempt_status": "replayed" if recovery_replay else "responded",
                "content_length": len(content),
                "parsed_json_present": parsed_json is not None,
                "parsed_json_root_type": type(parsed_json).__name__ if parsed_json is not None else None,
                "recovery_replay": recovery_replay,
            },
        )
        self._hooks.on_llm_responded(
            response_metadata=response_metadata,
            task_id=task_id,
            session_id=session_id,
            phase=phase,
            call_id=call_id,
            response_content=content,
            parsed_json=parsed_json,
        )

    @staticmethod
    def _request_hash(
        request: LLMRequest,
        *,
        settings: Any | None = None,
        resolved_reasoning=None,
    ) -> str:
        settings = settings or SimpleNamespace()
        resolved = resolved_reasoning or resolve_reasoning_policy(request.reasoning_policy, settings)
        provider_endpoint = normalized_provider_endpoint(
            str(getattr(settings, "base_url", "") or "")
        )
        provider_request = request.model_dump(mode="json")
        # Trace data is audit-only and never reaches the provider transport.
        # Excluding it keeps checkpoint replay stable when a durable completion
        # reservation is reconstructed after process restart.
        provider_request.pop("trace_info", None)
        provider_request.pop("context_selection", None)
        payload = {
            "hash_version": "provider_bound_v2",
            "request": provider_request,
            "provider": str(getattr(settings, "provider", "") or ""),
            "provider_endpoint": provider_endpoint,
            "model": str(getattr(settings, "model", "") or ""),
            "resolved_reasoning": resolved.model_dump(mode="json"),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"v2:sha256:{hashlib.sha256(encoded).hexdigest()}"

    def _value(self, getter: Callable[[], str] | None) -> str:
        if getter is None:
            return ""
        try:
            return str(getter() or "")
        except Exception:
            return ""

    def _request_diagnostics(self, request: LLMRequest) -> dict[str, Any]:
        messages = list(request.messages or [])
        message_lengths = [len(str(getattr(message, "content", "") or "")) for message in messages]
        settings = getattr(self._client, "settings", None)
        timeout_seconds = request.timeout_seconds or getattr(settings, "timeout_seconds", None)
        return {
            "message_count": len(messages),
            "prompt_chars": sum(message_lengths),
            "max_message_chars": max(message_lengths) if message_lengths else 0,
            "response_format": request.response_format,
            "max_tokens": request.max_tokens,
            "timeout_seconds": timeout_seconds,
            "transport_retries": request.transport_retries,
            "model": str(getattr(settings, "model", "") or ""),
            "provider": str(getattr(settings, "provider", "") or ""),
            "reasoning_profile": (
                f"{resolved.profile_id}:{resolved.profile_version}"
                if (resolved := resolve_reasoning_policy(request.reasoning_policy, settings or SimpleNamespace()))
                else ""
            ),
            "reasoning_resolution": resolved.resolution.value,
        }

    def _request_trace_info(self, request: LLMRequest) -> dict[str, Any]:
        trace_info = json_safe(dict(request.trace_info or {}))
        return trace_info if isinstance(trace_info, dict) else {}

    def _request_purpose(self, request: LLMRequest) -> str:
        trace_info = self._request_trace_info(request)
        for key in ("purpose", "semantic_task", "tool", "task", "operation", "step_id"):
            value = trace_info.get(key)
            if value not in (None, ""):
                return str(value)
        return str(request.response_format)

    def _request_task(self, request: LLMRequest) -> str:
        trace_info = self._request_trace_info(request)
        if trace_info.get("task") not in (None, ""):
            return str(trace_info["task"])
        if not request.messages:
            return ""
        user_messages = [message.content for message in request.messages if message.role == "user" and message.content]
        if not user_messages:
            return request.messages[0].content[:240] if request.messages[0].content else ""
        return user_messages[-1][:240]

    def _is_recoverable_llm_error(self, exc: Exception) -> bool:
        return classify_error(exc) in {
            ErrorCategory.NETWORK,
            ErrorCategory.TIMEOUT,
            ErrorCategory.RETRYABLE,
        }

    def _json_dict(self, value: Any) -> dict[str, Any]:
        safe = json_safe(value)
        return safe if isinstance(safe, dict) else {}

    def dump_json_text(self, value: Any) -> str:
        return json.dumps(json_safe(value), ensure_ascii=False, indent=2, sort_keys=True)
