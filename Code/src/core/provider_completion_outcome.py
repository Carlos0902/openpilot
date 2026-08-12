"""Classify one provider response completion outcome."""

from __future__ import annotations

from enum import Enum

from core.llm import LLMResponse


class ProviderCompletionOutcome(str, Enum):
    """Mutually exclusive provider response completion classes."""

    NORMAL = "normal"
    TOOL_PROGRESS = "tool_progress"
    TRUNCATED = "truncated"
    EMPTY_RESPONSE = "empty_response"


class ProviderCompletionOutcomeError(ValueError):
    """Raised when provider completion facts are invalid."""


def provider_completion_outcome(
    response: LLMResponse,
) -> ProviderCompletionOutcome:
    """Return one bounded completion outcome before continuation routing."""

    if not isinstance(response, LLMResponse):
        raise ProviderCompletionOutcomeError("response must be LLMResponse")
    finish_reason = response.finish_reason
    if finish_reason is None or not isinstance(finish_reason, str):
        raise ProviderCompletionOutcomeError(
            "finish_reason must be a non-empty string"
        )
    normalized_finish = finish_reason.strip().lower()
    if not normalized_finish:
        raise ProviderCompletionOutcomeError(
            "finish_reason must be a non-empty string"
        )
    if normalized_finish in {"length", "max_tokens"}:
        return ProviderCompletionOutcome.TRUNCATED
    if response.tool_calls:
        return ProviderCompletionOutcome.TOOL_PROGRESS
    if not response.content.strip():
        return ProviderCompletionOutcome.EMPTY_RESPONSE
    return ProviderCompletionOutcome.NORMAL


__all__ = [
    "ProviderCompletionOutcome",
    "ProviderCompletionOutcomeError",
    "provider_completion_outcome",
]
