"""Observe provider completion-token usage without coercing untrusted values."""

from __future__ import annotations

from typing import Any

from core.llm import LLMResponse

MAX_PROVIDER_COMPLETION_TOKENS = 1_000_000


class ProviderCompletionUsageError(ValueError):
    """Raised when completion usage observation receives an invalid response."""


def provider_completion_tokens(response: LLMResponse) -> int | None:
    """Return bounded nonnegative integer completion usage, or unknown."""

    if not isinstance(response, LLMResponse):
        raise ProviderCompletionUsageError("response must be LLMResponse")
    usage = response.usage
    if not isinstance(usage, dict):
        return None
    raw = usage.get("completion_tokens", usage.get("output_tokens"))
    if (
        type(raw) is not int
        or raw < 0
        or raw > MAX_PROVIDER_COMPLETION_TOKENS
    ):
        return None
    return raw


__all__ = [
    "MAX_PROVIDER_COMPLETION_TOKENS",
    "ProviderCompletionUsageError",
    "provider_completion_tokens",
]
