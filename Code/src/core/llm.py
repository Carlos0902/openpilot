"""Normalized LLM request and response wrapper."""

from __future__ import annotations

import json
import hashlib
import os
import re
import time
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

import httpx
from openai import APITimeoutError, OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.config import LLMSettings
from core.native_llm_transport import get_native_transport
from core.reasoning import (
    UnsupportedReasoningPolicyError,
    observe_reasoning_response,
    render_reasoning_transport,
    resolve_reasoning_policy,
)
from core.exceptions import (
    ContextAssemblyBudgetError,
    ContextAssemblyGovernanceError,
    ErrorCategory,
    InvalidLLMResponseError,
    LLMProviderError,
    LLMTimeoutError,
    classify_error,
)
from metadata import (
    ContextAssemblyStatus,
    ContextSelectionMetadata,
    ReasoningCapabilityProfileId,
    ReasoningPolicy,
    ReasoningTransportFamily,
)
from utils.json_utils import safe_parse_json


def normalized_provider_endpoint(base_url: str) -> str:
    """Return a credential-free endpoint identity while preserving meaningful ports."""

    parsed = urlsplit(str(base_url or ""))
    if not parsed.scheme or not parsed.hostname:
        return ""
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    rendered_host = f"[{host}]" if ":" in host else host
    port = parsed.port
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    authority = rendered_host if port is None or default_port else f"{rendered_host}:{port}"
    return f"{scheme}://{authority}{parsed.path}"


class LLMMessage(BaseModel):
    """A chat message, including provider tool round-trip fields."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    reasoning_content: str | None = None
    tool_calls: list["LLMToolCall"] = Field(default_factory=list)
    tool_call_id: str | None = None

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Keep the legacy compact projection for ordinary messages.

        Round-trip fields are rendered explicitly by :func:`render_llm_message`.
        Omitting absent optional fields here prevents existing context and
        legacy chat adapters that call ``model_dump()`` from paying for absent
        tool state or changing their wire shape, while retaining an explicit
        empty assistant ``content`` required by tool-call wire protocols.
        Callers can still override serialization options explicitly when they
        need a full diagnostic projection.
        """

        compact_defaults = "exclude_defaults" not in kwargs
        kwargs.setdefault("exclude_none", True)
        dumped = super().model_dump(*args, **kwargs)
        if compact_defaults and not dumped.get("tool_calls"):
            dumped.pop("tool_calls", None)
        return dumped

    @model_validator(mode="after")
    def _role_fields_are_valid(self) -> "LLMMessage":
        if self.role in {"system", "user"} and (
            self.reasoning_content is not None or self.tool_calls or self.tool_call_id
        ):
            raise ValueError(f"{self.role} messages cannot carry tool/reasoning fields")
        if self.role == "assistant" and self.tool_call_id is not None:
            raise ValueError("assistant messages cannot carry tool_call_id")
        if self.role == "tool":
            if not self.tool_call_id:
                raise ValueError("tool messages require tool_call_id")
            if self.reasoning_content is not None or self.tool_calls:
                raise ValueError("tool messages cannot carry assistant reasoning/tool fields")
        return self


class LLMToolFunction(BaseModel):
    """Provider-neutral function definition sent in a tools request."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class LLMToolDefinition(BaseModel):
    """One OpenAI-compatible function tool definition."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["function"] = "function"
    function: LLMToolFunction


class LLMToolFunctionCall(BaseModel):
    """Function name and JSON argument string returned by a provider."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    arguments: str = ""


class LLMToolCall(BaseModel):
    """One assistant-side provider tool call."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["function"] = "function"
    function: LLMToolFunctionCall
    index: int | None = Field(default=None, ge=0)


class LLMToolResult(BaseModel):
    """One tool result that must match an assistant call ID."""

    model_config = ConfigDict(extra="forbid")

    tool_call_id: str = Field(min_length=1)
    content: str


def render_llm_message(message: LLMMessage) -> dict[str, Any]:
    """Render a message without leaking internal null/default fields."""

    rendered: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.reasoning_content is not None:
        rendered["reasoning_content"] = message.reasoning_content
    if message.tool_calls:
        rendered["tool_calls"] = [call.model_dump(mode="json", exclude_none=True) for call in message.tool_calls]
    if message.tool_call_id is not None:
        rendered["tool_call_id"] = message.tool_call_id
    return rendered


def render_llm_tools(tools: list[LLMToolDefinition]) -> list[dict[str, Any]]:
    """Render a strict tool definition list for OpenAI-compatible providers."""

    return [tool.model_dump(mode="json") for tool in tools]


class LLMRequest(BaseModel):
    """Provider-neutral chat completion request."""

    messages: list[LLMMessage]
    tools: list[LLMToolDefinition] = Field(default_factory=list)
    tool_choice: Literal["auto", "none", "required"] | None = None
    response_format: Literal["text", "json_object"] = "text"
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    transport_retries: int | None = Field(default=None, ge=0)
    trace_info: dict[str, Any] = Field(default_factory=dict)
    context_selection: ContextSelectionMetadata | None = None
    reasoning_policy: ReasoningPolicy = Field(default_factory=ReasoningPolicy)

    @model_validator(mode="after")
    def _tool_names_are_unique(self) -> "LLMRequest":
        names = [tool.function.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("LLMRequest tools must have unique function names")
        if self.tool_choice in {"auto", "required"} and not self.tools:
            raise ValueError("LLMRequest tool_choice requires tools")
        if self.tool_choice == "none" and not self.tools:
            raise ValueError("LLMRequest tool_choice=none requires tools")
        return self


class LLMResponse(BaseModel):
    """Provider-neutral chat completion response."""

    model_config = ConfigDict(protected_namespaces=())

    content: str
    reasoning_content: str | None = None
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    parsed_json: dict[str, Any] | list[Any] | None = None
    model: str
    provider: str
    usage: dict[str, Any] = Field(default_factory=dict)
    finish_reason: str | None = None
    provider_details: dict[str, Any] = Field(default_factory=dict)


class LLMStreamEvent(BaseModel):
    """Public, UI-safe streaming progress from an LLM request."""

    event_type: Literal["start", "delta", "done", "cache_hit", "retry"] = "delta"
    text_delta: str = ""
    visible_text_preview: str = ""
    chars_received: int = 0
    finish_reason: str | None = None
    provider_details: dict[str, Any] = Field(default_factory=dict)


class LLMClient:
    """OpenAI-compatible chat completion client with caching."""

    def __init__(self, settings: LLMSettings | None = None, enable_cache: bool = True) -> None:
        self.settings = settings or LLMSettings()
        self._cache = None
        if enable_cache:
            from utils.cache import TTLCache
            # Cache responses for 1 hour
            self._cache = TTLCache(ttl_seconds=3600)

    def _make_cache_key(self, request: LLMRequest) -> str:
        """Generate a cache key from the request."""
        temp = request.temperature if request.temperature is not None else self.settings.temperature
        resolved = resolve_reasoning_policy(
            request.reasoning_policy,
            self.settings,
            structured_output=request.response_format == "json_object",
        )
        provider_endpoint = normalized_provider_endpoint(
            str(getattr(self.settings, "base_url", "") or "")
        )
        payload = {
            "hash_version": "provider_bound_v2",
            "provider": str(getattr(self.settings, "provider", "") or ""),
            "provider_endpoint": provider_endpoint,
            "model": str(getattr(self.settings, "model", "") or ""),
            "transport_family": resolved.transport_family,
            "reasoning": resolved.model_dump(mode="json"),
            "profile_version": resolved.profile_version,
            "response_format": request.response_format,
            "temperature": temp,
            "max_tokens": request.max_tokens,
            "messages": [render_llm_message(message) for message in request.messages],
            "tools": render_llm_tools(request.tools),
            "tool_choice": request.tool_choice,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"v2:sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"

    def complete(
        self,
        request: LLMRequest,
        max_retries: int = 3,
        use_cache: bool = True,
        stream_callback: Callable[[LLMStreamEvent], None] | None = None,
    ) -> LLMResponse:
        """Execute a chat completion and normalize the response.

        Args:
            request: The LLM request
            max_retries: Maximum number of retries for invalid JSON (default: 3)
            use_cache: Whether to use cached responses (default: True)

        Returns:
            LLMResponse with parsed content

        Raises:
            InvalidLLMResponseError: If JSON parsing fails after all retries
            LLMTimeoutError: If request times out
            LLMProviderError: If provider returns an error
        """
        if (
            request.context_selection is not None
            and request.context_selection.assembly_status
            != ContextAssemblyStatus.READY
        ):
            if (
                request.context_selection.assembly_status
                == ContextAssemblyStatus.GOVERNANCE_BLOCKED
            ):
                raise ContextAssemblyGovernanceError(
                    request.context_selection.governance_blocked_candidate_ids
                )
            raise ContextAssemblyBudgetError(
                request.context_selection.omitted_required_candidate_ids
            )
        # Check cache first
        if use_cache and self._cache is not None:
            cache_key = self._make_cache_key(request)
            status, cached = self._cache.get(cache_key)
            if status in ('hit', 'stale'):
                self._emit_stream_event(
                    stream_callback,
                    LLMStreamEvent(
                        event_type="cache_hit",
                        visible_text_preview="Using cached response",
                        chars_received=len(getattr(cached, "content", "") or ""),
                        finish_reason=getattr(cached, "finish_reason", None),
                    ),
                )
                return cached

        resolved_reasoning = resolve_reasoning_policy(
            request.reasoning_policy,
            self.settings,
            structured_output=request.response_format == "json_object",
        )
        native_transport = None
        if resolved_reasoning.transport_family != ReasoningTransportFamily.OPENAI_CHAT_COMPLETIONS:
            native_transport = get_native_transport(resolved_reasoning.transport_family)
            if stream_callback is not None:
                raise UnsupportedReasoningPolicyError(
                    "native reasoning transports currently support non-streaming calls only"
                )
        self.settings.require_ready()
        client = self._make_openai_client() if native_transport is None else None

        last_error = None
        repair_messages = list(request.messages)
        for attempt in range(max_retries):
            effective_timeout = request.timeout_seconds or self.settings.timeout_seconds
            payload: dict[str, Any] = {
                "model": self.settings.model,
                "messages": [render_llm_message(message) for message in repair_messages],
                "temperature": request.temperature
                if request.temperature is not None
                else self.settings.temperature,
                "timeout": effective_timeout,
            }
            if request.max_tokens is not None:
                payload["max_tokens"] = request.max_tokens
            if request.response_format == "json_object":
                payload["response_format"] = {"type": "json_object"}
            if request.tools:
                payload["tools"] = render_llm_tools(request.tools)
            if request.tool_choice is not None:
                payload["tool_choice"] = request.tool_choice
            payload.update(render_reasoning_transport(resolved_reasoning))

            self._emit_stream_event(
                stream_callback,
                LLMStreamEvent(
                    event_type="start",
                    visible_text_preview="Waiting for model response",
                    provider_details={"attempt": attempt + 1},
                ),
            )
            transport_kwargs = (
                {"transport_retries": request.transport_retries}
                if request.transport_retries is not None
                else {}
            )
            if native_transport is not None:
                response = self._create_native_completion_with_transport_retry(
                    native_transport,
                    request.model_copy(update={"messages": repair_messages}),
                    resolved_reasoning,
                    **transport_kwargs,
                )
            elif stream_callback is not None:
                response = self._create_streaming_completion_with_transport_retry(
                    client,
                    payload,
                    stream_callback,
                    wall_clock_timeout=effective_timeout,
                    **transport_kwargs,
                )
            else:
                response = self._create_completion_with_transport_retry(
                    client,
                    payload,
                    **transport_kwargs,
                )

            choice = response.choices[0]
            content, content_diagnostics = self._extract_message_content(choice.message)
            reasoning_content = self._extract_message_reasoning_content(choice.message)
            tool_calls = self._extract_message_tool_calls(choice.message)
            parsed_json: dict[str, Any] | list[Any] | None = None

            if tool_calls:
                result = LLMResponse(
                    content=content,
                    reasoning_content=reasoning_content,
                    tool_calls=tool_calls,
                    model=response.model,
                    provider=self.settings.provider,
                    usage=self._usage_metadata(response),
                    finish_reason=choice.finish_reason,
                    provider_details=self._response_metadata(
                        response=response,
                        choice=choice,
                        content=content,
                        content_diagnostics=content_diagnostics,
                        json_repair_attempt=attempt + 1,
                        reasoning_profile_id=resolved_reasoning.profile_id,
                    ),
                )
                result.provider_details["tool_call_count"] = len(tool_calls)
                return result

            if request.response_format == "json_object":
                # Try to extract JSON from markdown code blocks if present
                cleaned_content = self._extract_json_from_content(content)
                # Use safe_parse_json for better error handling and caching
                raw_parsed_json = safe_parse_json(cleaned_content)
                parsed_json, parse_diagnostics = self._normalize_parsed_json(raw_parsed_json)

                if parsed_json is not None:
                    # Success! Return the response
                    usage = self._usage_metadata(response)
                    provider_details = self._response_metadata(
                        response=response,
                        choice=choice,
                        content=content,
                        content_diagnostics=content_diagnostics,
                        json_repair_attempt=attempt + 1,
                        reasoning_profile_id=resolved_reasoning.profile_id,
                    )
                    provider_details.update(
                        {
                            "parsed_from_content": True,
                            "parse_source": "content",
                            "json_repair_attempts": attempt + 1,
                            **parse_diagnostics,
                        }
                    )
                    result = LLMResponse(
                        content=content,
                        reasoning_content=reasoning_content,
                        tool_calls=tool_calls,
                        parsed_json=parsed_json,
                        model=response.model,
                        provider=self.settings.provider,
                        usage=usage,
                        finish_reason=choice.finish_reason,
                        provider_details=provider_details,
                    )

                    # Cache successful response
                    if use_cache and self._cache is not None and self._should_cache_response(result):
                        cache_key = self._make_cache_key(request)
                        self._cache.put(cache_key, result)

                    return result
                else:
                    # JSON parsing failed
                    invalid_type = parse_diagnostics.get("invalid_parsed_json_type")
                    preview = " ".join(content.split())[:300]
                    last_error = self._invalid_json_error(
                        content=content,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        invalid_type=invalid_type,
                        preview=preview,
                        response=response,
                        choice=choice,
                        content_diagnostics=content_diagnostics,
                        parse_diagnostics=parse_diagnostics,
                        reasoning_profile_id=resolved_reasoning.profile_id,
                    )
                    if attempt < max_retries - 1:
                        self._emit_stream_event(
                            stream_callback,
                            LLMStreamEvent(
                                event_type="retry",
                                visible_text_preview="Response was not valid JSON; requesting repair",
                                chars_received=len(content),
                                provider_details={"attempt": attempt + 1},
                            ),
                        )
                        # Add a stronger instruction for the next attempt
                        repair_messages.append(
                            LLMMessage(
                                role="assistant",
                                content=content
                            )
                        )
                        repair_messages.append(
                            LLMMessage(
                                role="user",
                                content="The previous response was not valid JSON. Please return ONLY valid JSON without any markdown formatting, explanations, or extra text. Start with { or [ and end with } or ]."
                            )
                        )
                        continue
                    else:
                        # Last attempt failed, raise error
                        raise last_error
            else:
                # Not JSON mode, return as-is
                usage = self._usage_metadata(response)
                result = LLMResponse(
                    content=content,
                    reasoning_content=reasoning_content,
                    tool_calls=tool_calls,
                    parsed_json=parsed_json,
                    model=response.model,
                    provider=self.settings.provider,
                    usage=usage,
                    finish_reason=choice.finish_reason,
                    provider_details=self._response_metadata(
                        response=response,
                        choice=choice,
                        content=content,
                        content_diagnostics=content_diagnostics,
                        json_repair_attempt=attempt + 1,
                        reasoning_profile_id=resolved_reasoning.profile_id,
                    ),
                )

                # Cache successful response
                if use_cache and self._cache is not None and self._should_cache_response(result):
                    cache_key = self._make_cache_key(request)
                    self._cache.put(cache_key, result)

                return result

        # Should never reach here, but just in case
        raise InvalidLLMResponseError(
            f"LLM returned invalid JSON after {max_retries} attempts."
        )

    def _invalid_json_error(
        self,
        *,
        content: str,
        attempt: int,
        max_retries: int,
        invalid_type: str | None,
        preview: str,
        response: Any,
        choice: Any,
        content_diagnostics: dict[str, Any],
        parse_diagnostics: dict[str, Any],
        reasoning_profile_id: ReasoningCapabilityProfileId,
    ) -> InvalidLLMResponseError:
        error = InvalidLLMResponseError(
            f"LLM returned invalid JSON (attempt {attempt}/{max_retries}; "
            f"parsed_type={invalid_type or 'None'}; preview={preview!r})",
            response_text=content,
            usage=self._usage_metadata(response),
            finish_reason=getattr(choice, "finish_reason", None),
        )
        collapsed = " ".join(content.split())
        provider_details = self._response_metadata(
            response=response,
            choice=choice,
            content=content,
            content_diagnostics=content_diagnostics,
            json_repair_attempt=attempt,
            reasoning_profile_id=reasoning_profile_id,
        )
        error.context.update(
            {
                "response_length": len(content),
                "response_preview_start": collapsed[:500],
                "response_preview_end": collapsed[-500:] if len(collapsed) > 500 else collapsed,
                "finish_reason": getattr(choice, "finish_reason", None),
                "json_repair_attempt": attempt,
                "json_repair_attempts": attempt,
                "max_retries": max_retries,
                "invalid_parsed_json_type": invalid_type,
                "transport_retry_history": provider_details.get("transport_retry_history", []),
                "content_diagnostics": content_diagnostics,
                **parse_diagnostics,
            }
        )
        return error

    def _emit_stream_event(
        self,
        stream_callback: Callable[[LLMStreamEvent], None] | None,
        event: LLMStreamEvent,
    ) -> None:
        if stream_callback is not None:
            stream_callback(event)

    def _normalize_parsed_json(self, value: Any) -> tuple[dict[str, Any] | list[Any] | None, dict[str, Any]]:
        if isinstance(value, (dict, list)):
            return value, {"invalid_parsed_json_type": None}
        if value is None:
            return None, {"invalid_parsed_json_type": None, "parse_failed_cached": True}
        return None, {
            "invalid_parsed_json_type": type(value).__name__,
            "parse_failed_cached": True,
        }

    def _create_streaming_completion_with_transport_retry(
        self,
        client: OpenAI,
        payload: dict[str, Any],
        stream_callback: Callable[[LLMStreamEvent], None],
        *,
        transport_retries: int | None = None,
        wall_clock_timeout: float | None = None,
    ) -> Any:
        streaming_payload = dict(payload)
        streaming_payload["stream"] = True
        transport_kwargs = (
            {"transport_retries": transport_retries}
            if transport_retries is not None
            else {}
        )
        stream = self._create_completion_with_transport_retry(
            client,
            streaming_payload,
            **transport_kwargs,
        )
        try:
            return self._collect_streaming_completion(
                stream,
                stream_callback,
                wall_clock_timeout=wall_clock_timeout,
            )
        except LLMTimeoutError:
            self._close_stream_quietly(stream)
            raise
        except Exception as exc:
            self._close_stream_quietly(stream)
            if isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
                raise LLMTimeoutError(
                    str(exc),
                    timeout_seconds=wall_clock_timeout or self.settings.timeout_seconds,
                ) from exc
            if not self._is_transport_exception(exc):
                raise
            error = LLMProviderError(
                f"{ErrorCategory.NETWORK}: {exc}",
                retryable=True,
                category=ErrorCategory.NETWORK,
            )
            error.context["transport_retry_history"] = getattr(self, "_last_transport_retry_history", [])
            raise error from exc

    def _make_openai_client(self, *, trust_env: bool = True) -> OpenAI:
        kwargs: dict[str, Any] = {
            "api_key": self.settings.api_key,
            "base_url": self.settings.base_url,
            "timeout": self.settings.timeout_seconds,
            "max_retries": 0,
        }
        if not trust_env:
            kwargs["http_client"] = httpx.Client(
                timeout=self.settings.timeout_seconds,
                trust_env=False,
            )
        return OpenAI(**kwargs)

    def _collect_streaming_completion(
        self,
        stream: Any,
        stream_callback: Callable[[LLMStreamEvent], None],
        *,
        wall_clock_timeout: float | None = None,
    ) -> Any:
        from types import SimpleNamespace

        started_at = time.monotonic()
        content_parts: list[str] = []
        finish_reason: str | None = None
        model = self.settings.model
        response_id = None
        created = None
        usage: Any = None
        hidden_reasoning_fields: dict[str, int] = {}
        reasoning_parts: list[str] = []
        tool_call_parts: dict[int, dict[str, Any]] = {}

        for chunk in stream:
            if wall_clock_timeout is not None and time.monotonic() - started_at >= wall_clock_timeout:
                raise LLMTimeoutError(
                    f"Streaming response exceeded wall-clock timeout of {wall_clock_timeout}s.",
                    timeout_seconds=wall_clock_timeout,
                )
            model = str(getattr(chunk, "model", None) or model)
            response_id = getattr(chunk, "id", response_id)
            created = getattr(chunk, "created", created)
            usage = getattr(chunk, "usage", usage)
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            finish_reason = getattr(choice, "finish_reason", None) or finish_reason
            delta = getattr(choice, "delta", None)
            hidden_reasoning_fields = self._merge_hidden_reasoning_fields(
                hidden_reasoning_fields,
                self._hidden_reasoning_field_lengths(delta),
            )
            reasoning_delta = self._stream_delta_reasoning_content(delta)
            if reasoning_delta:
                reasoning_parts.append(reasoning_delta)
            self._merge_stream_delta_tool_calls(tool_call_parts, delta)
            text_delta = self._stream_delta_content(delta)
            if text_delta:
                content_parts.append(text_delta)
                content = "".join(content_parts)
                stream_callback(
                    LLMStreamEvent(
                        event_type="delta",
                        text_delta=text_delta,
                        visible_text_preview=content[-1200:],
                        chars_received=len(content),
                        provider_details={"hidden_reasoning_fields": dict(hidden_reasoning_fields)}
                        if hidden_reasoning_fields
                        else {},
                    )
                )

        content = "".join(content_parts)
        tool_calls = self._finalize_stream_tool_calls(tool_call_parts)
        message = SimpleNamespace(
            content=content,
            reasoning_content="".join(reasoning_parts) or None,
            tool_calls=tool_calls or None,
        )
        choice = SimpleNamespace(message=message, finish_reason=finish_reason)
        response = SimpleNamespace(
            choices=[choice],
            usage=usage,
            model=model,
            id=response_id,
            created=created,
            provider_details={"hidden_reasoning_fields": hidden_reasoning_fields},
        )
        stream_callback(
            LLMStreamEvent(
                event_type="done",
                visible_text_preview=content[-1200:],
                chars_received=len(content),
                finish_reason=finish_reason,
                provider_details={"hidden_reasoning_fields": hidden_reasoning_fields}
                if hidden_reasoning_fields
                else ({"tool_call_count": len(tool_calls)} if tool_calls else {}),
            )
        )
        return response

    def _close_stream_quietly(self, stream: Any) -> None:
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def _stream_delta_content(self, delta: Any) -> str:
        if delta is None:
            return ""
        if isinstance(delta, dict):
            value = delta.get("content")
        else:
            value = getattr(delta, "content", None)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(part for item in value if (part := self._content_part_text(item)))
        return ""

    def _stream_delta_reasoning_content(self, delta: Any) -> str:
        if delta is None:
            return ""
        value = (
            delta.get("reasoning_content")
            if isinstance(delta, dict)
            else getattr(delta, "reasoning_content", None)
        )
        return value if isinstance(value, str) else ""

    def _merge_stream_delta_tool_calls(
        self,
        accumulated: dict[int, dict[str, Any]],
        delta: Any,
    ) -> None:
        raw_calls = (
            delta.get("tool_calls")
            if isinstance(delta, dict)
            else getattr(delta, "tool_calls", None)
        )
        if not raw_calls:
            return
        if not isinstance(raw_calls, (list, tuple)):
            raise UnsupportedReasoningPolicyError(
                "provider streaming tool_calls must be a list"
            )
        for position, raw_call in enumerate(raw_calls):
            if isinstance(raw_call, dict):
                index = raw_call.get("index", position)
                call_id = raw_call.get("id")
                call_type = raw_call.get("type", "function")
                function = raw_call.get("function")
            else:
                index = getattr(raw_call, "index", position)
                call_id = getattr(raw_call, "id", None)
                call_type = getattr(raw_call, "type", "function")
                function = getattr(raw_call, "function", None)
            try:
                index = int(index)
            except (TypeError, ValueError) as exc:
                raise UnsupportedReasoningPolicyError(
                    "provider streaming tool_call index must be a non-negative integer"
                ) from exc
            if index < 0:
                raise UnsupportedReasoningPolicyError(
                    "provider streaming tool_call index must be non-negative"
                )
            entry = accumulated.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "name": "",
                    "arguments": "",
                },
            )
            if call_id:
                entry["id"] = str(call_id)
            if call_type:
                entry["type"] = str(call_type)
            if isinstance(function, dict):
                name = function.get("name")
                arguments = function.get("arguments")
            else:
                name = getattr(function, "name", None)
                arguments = getattr(function, "arguments", None)
            if name:
                entry["name"] += str(name)
            if arguments:
                entry["arguments"] += str(arguments)

    def _finalize_stream_tool_calls(
        self,
        accumulated: dict[int, dict[str, Any]],
    ) -> list[LLMToolCall]:
        calls: list[LLMToolCall] = []
        for index in sorted(accumulated):
            entry = accumulated[index]
            try:
                calls.append(
                    LLMToolCall(
                        id=entry["id"],
                        type=entry["type"],
                        index=index,
                        function=LLMToolFunctionCall(
                            name=entry["name"],
                            arguments=entry["arguments"],
                        ),
                    )
                )
            except Exception as exc:
                raise UnsupportedReasoningPolicyError(
                    f"provider streaming tool_call shape is unsupported: {exc}"
                ) from exc
        return calls

    def _hidden_reasoning_field_lengths(self, delta: Any) -> dict[str, int]:
        fields = ("reasoning_content", "thinking", "reasoning")
        lengths: dict[str, int] = {}
        for field_name in fields:
            if isinstance(delta, dict):
                value = delta.get(field_name)
            else:
                value = getattr(delta, field_name, None)
            if value is None:
                continue
            if isinstance(value, str):
                lengths[field_name] = len(value)
            else:
                lengths[field_name] = len(str(value))
        return lengths

    def _merge_hidden_reasoning_fields(self, current: dict[str, int], update: dict[str, int]) -> dict[str, int]:
        merged = dict(current)
        for key, value in update.items():
            merged[key] = merged.get(key, 0) + value
        return merged

    def _create_completion_with_transport_retry(
        self,
        client: OpenAI,
        payload: dict[str, Any],
        *,
        transport_retries: int | None = None,
    ) -> Any:
        retries = getattr(self.settings, "transport_retries", 0) if transport_retries is None else transport_retries
        attempts = max(0, int(retries)) + 1
        delay = max(0.0, float(getattr(self.settings, "retry_initial_delay", 0.0)))
        max_delay = max(delay, float(getattr(self.settings, "retry_max_delay", delay)))
        last_error: Exception | None = None
        history: list[dict[str, Any]] = []
        self._last_transport_retry_history = history

        for attempt in range(1, attempts + 1):
            try:
                response = client.chat.completions.create(**payload)
                history.append(
                    {
                        "attempt": attempt,
                        "status": "success",
                        "retryable": False,
                    }
                )
                return response
            except APITimeoutError as exc:
                last_error = exc
                category = ErrorCategory.TIMEOUT
                retryable = True
            except OpenAIError as exc:
                last_error = exc
                category = self._classify_provider_error(exc)
                retryable = self._is_retryable_provider_error(exc, category)
            except Exception as exc:
                if not self._is_transport_exception(exc):
                    raise
                last_error = exc
                category = self._classify_provider_error(exc)
                retryable = True

            history.append(
                {
                    "attempt": attempt,
                    "status": "failed",
                    "category": category.value,
                    "retryable": retryable,
                    "error_type": type(last_error).__name__ if last_error else None,
                    "error": str(last_error)[:500] if last_error else "",
                }
            )

            if not retryable or attempt >= attempts:
                break
            if delay > 0:
                time.sleep(min(delay, max_delay))
                delay = min(delay * 2 if delay else 0, max_delay)

        if last_error is not None and self._should_retry_without_env_proxy(last_error):
            direct_attempt = attempts + 1
            try:
                response = self._make_openai_client(trust_env=False).chat.completions.create(**payload)
                history.append(
                    {
                        "attempt": direct_attempt,
                        "status": "success",
                        "retryable": False,
                        "trust_env": False,
                        "reason": "env_proxy_fallback",
                    }
                )
                return response
            except APITimeoutError as exc:
                last_error = exc
                category = ErrorCategory.TIMEOUT
                retryable = False
            except OpenAIError as exc:
                last_error = exc
                category = self._classify_provider_error(exc)
                retryable = False
            except Exception as exc:
                if not self._is_transport_exception(exc):
                    raise
                last_error = exc
                category = self._classify_provider_error(exc)
                retryable = False
            history.append(
                {
                    "attempt": direct_attempt,
                    "status": "failed",
                    "category": category.value,
                    "retryable": retryable,
                    "error_type": type(last_error).__name__ if last_error else None,
                    "error": str(last_error)[:500] if last_error else "",
                    "trust_env": False,
                    "reason": "env_proxy_fallback",
                }
            )

        if isinstance(last_error, (APITimeoutError, httpx.TimeoutException)):
            error = LLMTimeoutError(str(last_error), timeout_seconds=self.settings.timeout_seconds)
            error.context["transport_retry_history"] = history
            raise error from last_error
        if last_error is not None:
            category = self._classify_provider_error(last_error)
            status_code = getattr(last_error, "status_code", None)
            error = LLMProviderError(
                f"{category}: {last_error}",
                status_code=status_code,
                retryable=self._is_retryable_provider_error(last_error, category),
                category=category,
            )
            error.context["transport_retry_history"] = history
            raise error from last_error
        raise LLMProviderError("Provider request failed without an error.", retryable=True, category=ErrorCategory.RETRYABLE)

    def _create_native_completion_with_transport_retry(
        self,
        transport: Any,
        request: LLMRequest,
        resolved_reasoning: Any,
        *,
        transport_retries: int | None = None,
    ) -> Any:
        """Run a native provider attempt while preserving transport evidence."""

        retries = (
            getattr(self.settings, "transport_retries", 0)
            if transport_retries is None
            else transport_retries
        )
        attempts = max(0, int(retries)) + 1
        delay = max(0.0, float(getattr(self.settings, "retry_initial_delay", 0.0)))
        max_delay = max(delay, float(getattr(self.settings, "retry_max_delay", delay)))
        history: list[dict[str, Any]] = []
        self._last_transport_retry_history = history
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = transport.send_once(
                    self.settings,
                    request,
                    resolved_reasoning,
                    trust_env=True,
                )
                history.append(
                    {"attempt": attempt, "status": "success", "retryable": False}
                )
                provider_details = getattr(response, "provider_details", None)
                if isinstance(provider_details, dict):
                    provider_details["transport_retry_history"] = list(history)
                return response
            except LLMProviderError as exc:
                last_error = exc
                category = exc.category
                retryable = bool(exc.context.get("retryable", False))
            except Exception as exc:
                last_error = exc
                category = self._classify_provider_error(exc)
                retryable = category in {
                    ErrorCategory.NETWORK,
                    ErrorCategory.TIMEOUT,
                    ErrorCategory.RETRYABLE,
                }

            history.append(
                {
                    "attempt": attempt,
                    "status": "failed",
                    "category": category.value,
                    "retryable": retryable,
                    "error_type": type(last_error).__name__ if last_error else None,
                    "error": str(last_error)[:500] if last_error else "",
                }
            )
            if not retryable or attempt >= attempts:
                break
            if delay > 0:
                time.sleep(min(delay, max_delay))
                delay = min(delay * 2 if delay else 0, max_delay)

        if last_error is not None and self._should_retry_without_env_proxy(last_error):
            direct_attempt = attempts + 1
            try:
                response = transport.send_once(
                    self.settings,
                    request,
                    resolved_reasoning,
                    trust_env=False,
                )
                history.append(
                    {
                        "attempt": direct_attempt,
                        "status": "success",
                        "retryable": False,
                        "trust_env": False,
                        "reason": "env_proxy_fallback",
                    }
                )
                provider_details = getattr(response, "provider_details", None)
                if isinstance(provider_details, dict):
                    provider_details["transport_retry_history"] = list(history)
                return response
            except Exception as exc:
                last_error = exc
                category = self._classify_provider_error(exc)
                history.append(
                    {
                        "attempt": direct_attempt,
                        "status": "failed",
                        "category": category.value,
                        "retryable": False,
                        "error_type": type(last_error).__name__,
                        "error": str(last_error)[:500],
                        "trust_env": False,
                        "reason": "env_proxy_fallback",
                    }
                )

        if isinstance(last_error, (httpx.TimeoutException, LLMTimeoutError)):
            error = LLMTimeoutError(str(last_error), timeout_seconds=self.settings.timeout_seconds)
            error.context["transport_retry_history"] = history
            raise error from last_error
        if isinstance(last_error, LLMProviderError):
            last_error.context["transport_retry_history"] = history
            raise last_error
        if last_error is not None:
            error = LLMProviderError(
                f"{self._classify_provider_error(last_error)}: {last_error}",
                retryable=False,
                category=self._classify_provider_error(last_error),
            )
            error.context["transport_retry_history"] = history
            raise error from last_error
        raise LLMProviderError(
            "Native provider request failed without an error.",
            retryable=True,
            category=ErrorCategory.RETRYABLE,
        )

    def _should_retry_without_env_proxy(self, exc: Exception) -> bool:
        category = self._classify_provider_error(exc)
        return category == ErrorCategory.NETWORK and any(
            os.environ.get(name)
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
        )

    def _classify_provider_error(self, exc: Exception) -> ErrorCategory:
        if isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
            return ErrorCategory.TIMEOUT
        if self._is_transport_exception(exc):
            return ErrorCategory.NETWORK
        status_code = getattr(exc, "status_code", None)
        if status_code == 429:
            return ErrorCategory.RETRYABLE
        if isinstance(status_code, int) and status_code >= 500:
            return ErrorCategory.RETRYABLE
        if isinstance(status_code, int) and 400 <= status_code < 500:
            if status_code in {401, 403}:
                return ErrorCategory.AUTH
            return ErrorCategory.TERMINAL
        return classify_error(exc)

    def _is_retryable_provider_error(self, exc: Exception, category: ErrorCategory) -> bool:
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            return status_code == 429 or status_code >= 500
        return category in {ErrorCategory.RETRYABLE, ErrorCategory.TIMEOUT, ErrorCategory.NETWORK}

    def _is_transport_exception(self, exc: Exception) -> bool:
        current: BaseException | None = exc
        visited: set[int] = set()
        transport_names = {
            "ConnectError",
            "ConnectTimeout",
            "NetworkError",
            "PoolTimeout",
            "ReadError",
            "ReadTimeout",
            "RemoteProtocolError",
            "TransportError",
            "WriteError",
            "WriteTimeout",
        }
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            if isinstance(current, httpx.TransportError) or type(current).__name__ in transport_names:
                return True
            current = current.__cause__ or current.__context__
        return False

    def _extract_message_content(self, message: Any) -> tuple[str, dict[str, Any]]:
        raw_content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        diagnostics = {
            "content_type": type(raw_content).__name__,
            "content_part_count": len(raw_content) if isinstance(raw_content, list) else None,
            "message_field_names": self._message_field_names(message),
        }
        if isinstance(raw_content, str):
            return raw_content, diagnostics
        if isinstance(raw_content, list):
            parts = [text for part in raw_content if (text := self._content_part_text(part))]
            return "\n".join(parts), diagnostics
        if raw_content is not None:
            return str(raw_content), diagnostics

        for field_name in ("text", "message", "output_text"):
            value = message.get(field_name) if isinstance(message, dict) else getattr(message, field_name, None)
            if isinstance(value, str):
                diagnostics["fallback_content_field"] = field_name
                return value, diagnostics
        return "", diagnostics

    def _extract_message_reasoning_content(self, message: Any) -> str | None:
        value = message.get("reasoning_content") if isinstance(message, dict) else getattr(message, "reasoning_content", None)
        return value if isinstance(value, str) else None

    def _extract_message_tool_calls(self, message: Any) -> list[LLMToolCall]:
        raw_calls = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
        if not raw_calls:
            return []
        if not isinstance(raw_calls, (list, tuple)):
            raise UnsupportedReasoningPolicyError("provider tool_calls must be a list")
        calls: list[LLMToolCall] = []
        for raw_call in raw_calls:
            try:
                if hasattr(raw_call, "model_dump"):
                    raw_call = raw_call.model_dump(mode="json", exclude_none=True)
                elif isinstance(raw_call, dict):
                    raw_call = dict(raw_call)
                else:
                    raw_function = getattr(raw_call, "function", None)
                    raw_call = {
                        "id": getattr(raw_call, "id", None),
                        "type": getattr(raw_call, "type", "function"),
                        "function": raw_function,
                        "index": getattr(raw_call, "index", None),
                    }
                raw_function = raw_call.get("function")
                if hasattr(raw_function, "model_dump"):
                    raw_call["function"] = raw_function.model_dump(
                        mode="json", exclude_none=True
                    )
                elif not isinstance(raw_function, dict):
                    raw_call["function"] = {
                        "name": getattr(raw_function, "name", None),
                        "arguments": getattr(raw_function, "arguments", ""),
                    }
                calls.append(LLMToolCall.model_validate(raw_call))
            except Exception as exc:
                raise UnsupportedReasoningPolicyError(
                    f"provider tool_call shape is unsupported: {exc}"
                ) from exc
        return calls

    def _content_part_text(self, part: Any) -> str:
        if isinstance(part, str):
            return part
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                return text
            if part.get("type") in {"text", "output_text"} and isinstance(part.get("content"), str):
                return str(part["content"])
            return ""
        text = getattr(part, "text", None)
        if isinstance(text, str):
            return text
        content = getattr(part, "content", None)
        if isinstance(content, str):
            return content
        return ""

    def _message_field_names(self, message: Any) -> list[str]:
        if isinstance(message, dict):
            return sorted(str(key) for key in message.keys())
        if hasattr(message, "model_dump"):
            try:
                return sorted(str(key) for key in message.model_dump().keys())
            except Exception:
                pass
        if hasattr(message, "__dict__"):
            return sorted(str(key) for key in vars(message).keys())
        return []

    def _response_metadata(
        self,
        *,
        response: Any,
        choice: Any,
        content: str,
        content_diagnostics: dict[str, Any],
        json_repair_attempt: int,
        reasoning_profile_id: ReasoningCapabilityProfileId,
    ) -> dict[str, Any]:
        finish_reason = getattr(choice, "finish_reason", None)
        metadata = {
            "id": getattr(response, "id", None),
            "created": getattr(response, "created", None),
            "transport_retry_history": getattr(self, "_last_transport_retry_history", []),
            "json_repair_attempt": json_repair_attempt,
            "content_diagnostics": content_diagnostics,
            "empty_length_response": finish_reason == "length" and not content.strip(),
        }
        observation = observe_reasoning_response(
            profile_id=reasoning_profile_id,
            message=getattr(choice, "message", None),
            usage=self._usage_metadata(response),
            finish_reason=finish_reason,
            visible_content=content,
        )
        metadata["reasoning_observation"] = observation.model_dump(mode="json")
        provider_details = getattr(response, "provider_details", None)
        if isinstance(provider_details, dict):
            metadata.update(provider_details)
        return metadata

    def _usage_metadata(self, response: Any) -> dict[str, Any]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {}
        if isinstance(usage, dict):
            return usage
        if hasattr(usage, "model_dump"):
            return usage.model_dump()
        if hasattr(usage, "__dict__"):
            return dict(vars(usage))
        return {}

    def _should_cache_response(self, response: LLMResponse) -> bool:
        if response.tool_calls:
            return False
        if response.finish_reason == "length" and not response.content.strip():
            return False
        return True

    def _extract_json_from_content(self, content: str) -> str:
        """Extract JSON from content, handling markdown code blocks.

        Args:
            content: Raw content from LLM

        Returns:
            Cleaned JSON string
        """
        # Remove markdown code blocks if present
        # Pattern: ```json\n{...}\n``` or ```\n{...}\n```
        content = content.strip()

        # Try to extract from markdown code block
        json_block_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', content)
        if json_block_match:
            return json_block_match.group(1).strip()

        # Try to find JSON object or array
        # Look for content between first { and last } or first [ and last ]
        if '{' in content and '}' in content:
            start = content.find('{')
            end = content.rfind('}') + 1
            return content[start:end]
        elif '[' in content and ']' in content:
            start = content.find('[')
            end = content.rfind(']') + 1
            return content[start:end]

        # Return as-is if no patterns found
        return content
