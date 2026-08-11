"""Explicit provider adapters for reasoning request and response semantics.

The business-facing contract is :class:`metadata.ReasoningPolicy`.  Adapters
are selected only from the versioned capability profile that was configured at
the settings boundary; this module never infers capabilities from a hostname
or model string.
"""

from __future__ import annotations

from typing import Any, Mapping

from metadata import (
    ReasoningCapabilityProfileId,
    ReasoningEffort,
    ReasoningMode,
    ReasoningUsageObservation,
    ResolvedReasoningPolicy,
)


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    if hasattr(source, "model_dump"):
        try:
            return source.model_dump().get(key, default)
        except Exception:
            pass
    return getattr(source, key, default)


def _nested(source: Any, *keys: str) -> Any:
    value = source
    for key in keys:
        value = _value(value, key)
        if value is None:
            return None
    return value


def _has_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value)
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _iter_content_parts(message: Any) -> list[Any]:
    content = _value(message, "content")
    if isinstance(content, list):
        return content
    if isinstance(content, Mapping):
        parts = _value(content, "parts")
        if isinstance(parts, list):
            return parts
    candidates = _value(message, "candidates")
    if isinstance(candidates, list):
        parts: list[Any] = []
        for candidate in candidates:
            parts.extend(_iter_content_parts(_value(candidate, "content", {})))
        return parts
    return []


def _contains_reasoning_content(message: Any) -> bool:
    for field_name in ("reasoning_content", "reasoning", "thinking"):
        if _has_nonempty(_value(message, field_name)):
            return True
    for part in _iter_content_parts(message):
        part_type = _value(part, "type")
        if part_type in {"thinking", "redacted_thinking", "reasoning"}:
            return True
        if _value(part, "thought") is True or _value(part, "thoughtSummary") is not None:
            return True
    return False


def _observation(
    *,
    message: Any,
    usage: Any,
    finish_reason: str | None,
    visible_content: str,
    reasoning_tokens: int | None = None,
    reasoning_tokens_source: str | None = None,
) -> ReasoningUsageObservation:
    return ReasoningUsageObservation(
        reasoning_tokens=reasoning_tokens,
        reasoning_tokens_source=reasoning_tokens_source,
        reasoning_content_present=_contains_reasoning_content(message),
        visible_content_empty=not bool((visible_content or "").strip()),
        finish_reason=finish_reason,
    )


class ReasoningTransportAdapter:
    """Small provider-specific policy/observation surface."""

    adapter_id = "generic"
    version = "v1"

    def render(self, resolved: ResolvedReasoningPolicy) -> dict[str, object]:
        return {}

    def observe(
        self,
        *,
        message: Any,
        usage: Any,
        finish_reason: str | None,
        visible_content: str,
    ) -> ReasoningUsageObservation:
        return _observation(
            message=message,
            usage=usage,
            finish_reason=finish_reason,
            visible_content=visible_content,
        )


class OpenAIChatReasoningAdapter(ReasoningTransportAdapter):
    adapter_id = "openai-chat"

    def render(self, resolved: ResolvedReasoningPolicy) -> dict[str, object]:
        if resolved.effective_mode == ReasoningMode.DISABLED:
            return {"reasoning_effort": "none"}
        if resolved.effective_mode == ReasoningMode.ENABLED and resolved.effective_effort:
            return {"reasoning_effort": resolved.effective_effort.value}
        return {}

    def observe(self, **kwargs: Any) -> ReasoningUsageObservation:
        usage = kwargs.get("usage") or {}
        details = _nested(usage, "completion_tokens_details")
        source = "completion_tokens_details.reasoning_tokens"
        tokens = _value(details, "reasoning_tokens") if details is not None else None
        if tokens is None:
            details = _nested(usage, "output_tokens_details")
            source = "output_tokens_details.reasoning_tokens"
            tokens = _value(details, "reasoning_tokens") if details is not None else None
        return _observation(
            **kwargs,
            reasoning_tokens=tokens if isinstance(tokens, int) else None,
            reasoning_tokens_source=source if isinstance(tokens, int) else None,
        )


class OpenAIChatNoReasoningAdapter(OpenAIChatReasoningAdapter):
    """OpenAI Chat Completions lane with an explicit no-reasoning contract.

    Several pre-reasoning OpenAI models reject ``reasoning_effort`` entirely.
    The profile therefore represents disabled reasoning by omission, while the
    resolver rejects explicit enabled reasoning before transport.
    """

    adapter_id = "openai-chat-no-reasoning"

    def render(self, resolved: ResolvedReasoningPolicy) -> dict[str, object]:
        return {}


class DeepSeekChatReasoningAdapter(OpenAIChatReasoningAdapter):
    adapter_id = "deepseek-chat"

    def render(self, resolved: ResolvedReasoningPolicy) -> dict[str, object]:
        if resolved.effective_mode == ReasoningMode.PROVIDER_DEFAULT:
            return {}
        thinking_type = (
            "disabled" if resolved.effective_mode == ReasoningMode.DISABLED else "enabled"
        )
        rendered: dict[str, object] = {"extra_body": {"thinking": {"type": thinking_type}}}
        if resolved.effective_mode == ReasoningMode.ENABLED and resolved.effective_effort:
            rendered["reasoning_effort"] = resolved.effective_effort.value
        return rendered


class AnthropicMessagesReasoningAdapter(ReasoningTransportAdapter):
    adapter_id = "anthropic-messages"

    def render(self, resolved: ResolvedReasoningPolicy) -> dict[str, object]:
        if resolved.effective_mode == ReasoningMode.DISABLED:
            return {"thinking": {"type": "disabled"}}
        if resolved.effective_mode != ReasoningMode.ENABLED:
            return {}
        if resolved.effective_token_budget is not None:
            return {
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": resolved.effective_token_budget,
                }
            }
        effort = (resolved.effective_effort or ReasoningEffort.MEDIUM).value
        return {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }

    def observe(self, **kwargs: Any) -> ReasoningUsageObservation:
        usage = kwargs.get("usage") or {}
        details = _nested(usage, "output_tokens_details")
        tokens = _value(details, "thinking_tokens") if details is not None else None
        return _observation(
            **kwargs,
            reasoning_tokens=tokens if isinstance(tokens, int) else None,
            reasoning_tokens_source=(
                "output_tokens_details.thinking_tokens" if isinstance(tokens, int) else None
            ),
        )


class GeminiGenerateContentReasoningAdapter(ReasoningTransportAdapter):
    adapter_id = "gemini-generate-content"

    def render(self, resolved: ResolvedReasoningPolicy) -> dict[str, object]:
        if resolved.effective_mode == ReasoningMode.DISABLED:
            config = {"thinkingBudget": 0}
        elif resolved.effective_token_budget is not None:
            config = {"thinkingBudget": resolved.effective_token_budget}
        elif resolved.effective_mode == ReasoningMode.ENABLED:
            config = {"thinkingLevel": (resolved.effective_effort or ReasoningEffort.MEDIUM).value}
        else:
            return {}
        return {"generation_config": {"thinking_config": config}}

    def observe(self, **kwargs: Any) -> ReasoningUsageObservation:
        usage = kwargs.get("usage") or {}
        usage_metadata = _value(usage, "usageMetadata") or usage
        tokens = _value(usage_metadata, "thoughtsTokenCount")
        source = "thoughtsTokenCount"
        if tokens is None:
            tokens = _value(usage_metadata, "thoughts_token_count")
            source = "thoughts_token_count"
        return _observation(
            **kwargs,
            reasoning_tokens=tokens if isinstance(tokens, int) else None,
            reasoning_tokens_source=source if isinstance(tokens, int) else None,
        )


REASONING_TRANSPORT_ADAPTERS: dict[ReasoningCapabilityProfileId, ReasoningTransportAdapter] = {
    ReasoningCapabilityProfileId.GENERIC_OPENAI_COMPATIBLE: ReasoningTransportAdapter(),
    ReasoningCapabilityProfileId.OPENAI_CHAT_KNOWN: OpenAIChatReasoningAdapter(),
    ReasoningCapabilityProfileId.OPENAI_CHAT_NO_REASONING_KNOWN: OpenAIChatNoReasoningAdapter(),
    ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN: DeepSeekChatReasoningAdapter(),
    ReasoningCapabilityProfileId.ANTHROPIC_MESSAGES_KNOWN: AnthropicMessagesReasoningAdapter(),
    ReasoningCapabilityProfileId.GEMINI_GENERATE_CONTENT_KNOWN: GeminiGenerateContentReasoningAdapter(),
}


def get_reasoning_transport_adapter(
    profile_id: ReasoningCapabilityProfileId,
) -> ReasoningTransportAdapter:
    try:
        return REASONING_TRANSPORT_ADAPTERS[profile_id]
    except KeyError as exc:
        raise ValueError(f"no reasoning transport adapter for profile {profile_id}") from exc


def observe_reasoning_response(
    *,
    profile_id: ReasoningCapabilityProfileId,
    message: Any,
    usage: Any,
    finish_reason: str | None,
    visible_content: str,
) -> ReasoningUsageObservation:
    return get_reasoning_transport_adapter(profile_id).observe(
        message=message,
        usage=usage,
        finish_reason=finish_reason,
        visible_content=visible_content,
    )
