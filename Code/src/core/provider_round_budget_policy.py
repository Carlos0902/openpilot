"""Derive bounded per-round provider call and result budgets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_PROVIDER_ROUND_CALLS = 4
MIN_PROVIDER_RESULT_CHARS = 640
MAX_PROVIDER_RESULT_CHARS = 1_600
TOKENS_PER_TOOL_RESULT_RESERVE = 512


class ProviderRoundBudgetError(ValueError):
    """Raised when per-round provider budget facts are invalid."""


@dataclass(frozen=True)
class ProviderRoundBudget:
    """Immutable per-round call and result-character budget."""

    max_calls: int
    tool_result_char_budget: int


def provider_round_budget(
    *,
    prompt_budget_tokens: int,
    response_call_count: int,
    remaining_prompt_tokens: int | None = None,
) -> ProviderRoundBudget:
    """Return the bounded budget used to admit/project one provider round."""

    _positive_int(prompt_budget_tokens, "prompt_budget_tokens")
    _positive_int(response_call_count, "response_call_count")
    if remaining_prompt_tokens is not None:
        _nonnegative_int(remaining_prompt_tokens, "remaining_prompt_tokens")

    call_count = min(response_call_count, MAX_PROVIDER_ROUND_CALLS)
    if remaining_prompt_tokens is None:
        max_calls = min(2, call_count)
    else:
        max_calls = min(
            call_count,
            MAX_PROVIDER_ROUND_CALLS,
            max(1, remaining_prompt_tokens // TOKENS_PER_TOOL_RESULT_RESERVE),
        )
    result_budget = min(
        MAX_PROVIDER_RESULT_CHARS,
        prompt_budget_tokens // call_count,
        max(MIN_PROVIDER_RESULT_CHARS, MAX_PROVIDER_RESULT_CHARS // call_count),
    )
    if remaining_prompt_tokens is not None:
        result_budget = min(
            result_budget,
            max(
                MIN_PROVIDER_RESULT_CHARS,
                remaining_prompt_tokens * 4 // call_count,
            ),
        )
    return ProviderRoundBudget(
        max_calls=max(1, max_calls),
        tool_result_char_budget=max(MIN_PROVIDER_RESULT_CHARS, result_budget),
    )


def _positive_int(value: Any, label: str) -> None:
    if type(value) is not int or value < 1:
        raise ProviderRoundBudgetError(
            f"{label} must be a positive integer"
        )


def _nonnegative_int(value: Any, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ProviderRoundBudgetError(
            f"{label} must be a non-negative integer"
        )


__all__ = [
    "MAX_PROVIDER_ROUND_CALLS",
    "MAX_PROVIDER_RESULT_CHARS",
    "MIN_PROVIDER_RESULT_CHARS",
    "ProviderRoundBudget",
    "ProviderRoundBudgetError",
    "TOKENS_PER_TOOL_RESULT_RESERVE",
    "provider_round_budget",
]
