"""Native HTTP transports for providers that are not OpenAI-compatible.

This module intentionally owns one HTTP attempt and response normalization only.
The LLM client remains the authority for retry, JSON repair, caching, and
completion evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from core.exceptions import ErrorCategory, LLMProviderError
from core.reasoning import UnsupportedReasoningPolicyError, render_reasoning_transport
from metadata import ReasoningTransportFamily, ResolvedReasoningPolicy


class NativeTransportUnsupportedError(ValueError):
    """Raised when the native route does not cover a transport family."""


@dataclass(frozen=True)
class NativeTransportRequest:
    url: str
    headers: dict[str, str]
    payload: dict[str, Any]


def _append_path(base_url: str, suffix: str) -> str:
    parsed = urlsplit(str(base_url or "").rstrip("/"))
    path = parsed.path.rstrip("/") + "/" + suffix.lstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def _json_instruction() -> str:
    return (
        "Return only valid JSON without markdown fences, explanations, or extra text. "
        "Start with { or [ and end with } or ]."
    )


def _append_system_instruction(existing: str | None, instruction: str) -> str:
    if existing and existing.strip():
        return f"{existing.rstrip()}\n\n{instruction}"
    return instruction


def _normalized_response(
    *,
    message: Any,
    usage: Mapping[str, Any],
    model: str,
    finish_reason: str | None,
    response_id: str | None = None,
    provider_details: dict[str, Any] | None = None,
) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=message),
                finish_reason=finish_reason,
            )
        ],
        usage=dict(usage),
        model=model,
        id=response_id,
        created=None,
        provider_details=provider_details or {},
    )


class NativeLLMTransport:
    family: ReasoningTransportFamily
    adapter_id: str

    def build_request(
        self,
        settings: Any,
        request: Any,
        resolved: ResolvedReasoningPolicy,
    ) -> NativeTransportRequest:
        raise NotImplementedError

    def normalize_response(self, raw: Mapping[str, Any], *, model: str) -> Any:
        raise NotImplementedError

    def send_once(
        self,
        settings: Any,
        request: Any,
        resolved: ResolvedReasoningPolicy,
        *,
        trust_env: bool = True,
    ) -> Any:
        built = self.build_request(settings, request, resolved)
        client_kwargs: dict[str, Any] = {
            "timeout": settings.timeout_seconds,
            "follow_redirects": True,
        }
        if not trust_env:
            client_kwargs["trust_env"] = False
        try:
            with httpx.Client(**client_kwargs) as client:
                response = client.post(built.url, headers=built.headers, json=built.payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMProviderError(
                f"native transport network failure: {exc}",
                retryable=True,
                category=(
                    ErrorCategory.TIMEOUT
                    if isinstance(exc, httpx.TimeoutException)
                    else ErrorCategory.NETWORK
                ),
            ) from exc

        if response.status_code >= 400:
            category = _http_error_category(response.status_code)
            raise LLMProviderError(
                f"native provider returned HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
                retryable=category in {ErrorCategory.RETRYABLE, ErrorCategory.NETWORK},
                category=category,
            )
        try:
            raw = response.json()
        except ValueError as exc:
            raise LLMProviderError(
                "native provider returned a non-JSON response",
                status_code=response.status_code,
                category=ErrorCategory.TERMINAL,
            ) from exc
        if not isinstance(raw, Mapping):
            raise LLMProviderError(
                "native provider response must be a JSON object",
                status_code=response.status_code,
                category=ErrorCategory.TERMINAL,
            )
        return self.normalize_response(raw, model=settings.model)


class AnthropicMessagesTransport(NativeLLMTransport):
    family = ReasoningTransportFamily.ANTHROPIC_MESSAGES
    adapter_id = "anthropic-messages"

    def build_request(
        self,
        settings: Any,
        request: Any,
        resolved: ResolvedReasoningPolicy,
    ) -> NativeTransportRequest:
        if request.tools:
            raise UnsupportedReasoningPolicyError(
                "native provider tool transport is not implemented in this phase"
            )
        system_parts = [message.content for message in request.messages if message.role == "system"]
        messages = [
            {"role": "user" if message.role == "user" else "assistant", "content": message.content}
            for message in request.messages
            if message.role != "system"
        ]
        if request.response_format == "json_object":
            system_parts.append(_json_instruction())
        budget = resolved.effective_token_budget
        max_tokens = request.max_tokens or max(4096, (budget or 0) + 1024)
        if budget is not None and budget < 1024:
            raise UnsupportedReasoningPolicyError(
                "Anthropic thinking budget_tokens must be at least 1024"
            )
        if budget is not None and budget >= max_tokens:
            raise UnsupportedReasoningPolicyError(
                "Anthropic thinking budget_tokens must leave room below max_tokens"
            )
        payload: dict[str, Any] = {
            "model": settings.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        payload.update(render_reasoning_transport(resolved))
        return NativeTransportRequest(
            url=_append_path(settings.base_url, "messages"),
            headers={
                "content-type": "application/json",
                "x-api-key": settings.api_key or "",
                "anthropic-version": "2023-06-01",
            },
            payload=payload,
        )

    def normalize_response(self, raw: Mapping[str, Any], *, model: str) -> Any:
        content = raw.get("content")
        if not isinstance(content, list):
            raise LLMProviderError(
                "Anthropic response is missing a content block list",
                category=ErrorCategory.VALIDATION,
            )
        usage = raw.get("usage")
        if not isinstance(usage, Mapping):
            usage = {}
        return _normalized_response(
            message=content,
            usage=usage,
            model=str(raw.get("model") or model),
            finish_reason=(str(raw["stop_reason"]) if raw.get("stop_reason") is not None else None),
            response_id=str(raw["id"]) if raw.get("id") is not None else None,
            provider_details={"native_transport": self.adapter_id},
        )


class GeminiGenerateContentTransport(NativeLLMTransport):
    family = ReasoningTransportFamily.GOOGLE_GENERATE_CONTENT
    adapter_id = "gemini-generate-content"

    def build_request(
        self,
        settings: Any,
        request: Any,
        resolved: ResolvedReasoningPolicy,
    ) -> NativeTransportRequest:
        if request.tools:
            raise UnsupportedReasoningPolicyError(
                "native provider tool transport is not implemented in this phase"
            )
        system_parts = [message.content for message in request.messages if message.role == "system"]
        contents = [
            {
                "role": "model" if message.role == "assistant" else "user",
                "parts": [{"text": message.content}],
            }
            for message in request.messages
            if message.role != "system"
        ]
        generation_config: dict[str, Any] = {}
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.max_tokens is not None:
            generation_config["maxOutputTokens"] = request.max_tokens
        if request.response_format == "json_object":
            generation_config["responseMimeType"] = "application/json"
        reasoning_fields = render_reasoning_transport(resolved).get("generation_config")
        if isinstance(reasoning_fields, Mapping):
            for key, value in reasoning_fields.items():
                if key == "thinking_config":
                    generation_config["thinkingConfig"] = value
                else:
                    generation_config[key] = value
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        return NativeTransportRequest(
            url=_append_path(settings.base_url, f"models/{settings.model}:generateContent"),
            headers={
                "content-type": "application/json",
                "x-goog-api-key": settings.api_key or "",
            },
            payload=payload,
        )

    def normalize_response(self, raw: Mapping[str, Any], *, model: str) -> Any:
        candidates = raw.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise LLMProviderError(
                "Gemini response is missing candidates",
                category=ErrorCategory.VALIDATION,
            )
        candidate = candidates[0]
        if not isinstance(candidate, Mapping):
            raise LLMProviderError(
                "Gemini candidate must be an object",
                category=ErrorCategory.VALIDATION,
            )
        content = candidate.get("content", {})
        parts = content.get("parts") if isinstance(content, Mapping) else None
        if not isinstance(parts, list):
            raise LLMProviderError(
                "Gemini response is missing content parts",
                category=ErrorCategory.VALIDATION,
            )
        usage = raw.get("usageMetadata")
        if not isinstance(usage, Mapping):
            usage = {}
        return _normalized_response(
            message=parts,
            usage=usage,
            model=str(raw.get("modelVersion") or model),
            finish_reason=(
                str(candidate["finishReason"])
                if candidate.get("finishReason") is not None
                else None
            ),
            provider_details={"native_transport": self.adapter_id},
        )


_NATIVE_TRANSPORTS: dict[ReasoningTransportFamily, NativeLLMTransport] = {
    ReasoningTransportFamily.ANTHROPIC_MESSAGES: AnthropicMessagesTransport(),
    ReasoningTransportFamily.GOOGLE_GENERATE_CONTENT: GeminiGenerateContentTransport(),
}


def get_native_transport(family: ReasoningTransportFamily) -> NativeLLMTransport:
    try:
        return _NATIVE_TRANSPORTS[family]
    except KeyError as exc:
        raise NativeTransportUnsupportedError(
            f"no native transport for family {family.value}"
        ) from exc


def _http_error_category(status_code: int) -> ErrorCategory:
    if status_code in {401, 403}:
        return ErrorCategory.AUTH
    if status_code == 429 or status_code >= 500:
        return ErrorCategory.RETRYABLE
    return ErrorCategory.TERMINAL
