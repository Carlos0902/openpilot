from __future__ import annotations

import pytest

from core.provider_round_budget_policy import (
    ProviderRoundBudgetError,
    provider_round_budget,
)


def test_budget_defaults_to_two_calls_without_remaining_prompt_fact() -> None:
    budget = provider_round_budget(
        prompt_budget_tokens=4096,
        response_call_count=3,
        remaining_prompt_tokens=None,
    )

    assert budget.max_calls == 2
    assert budget.tool_result_char_budget == 640


def test_budget_caps_calls_by_remaining_prompt_reserve() -> None:
    budget = provider_round_budget(
        prompt_budget_tokens=4096,
        response_call_count=4,
        remaining_prompt_tokens=1024,
    )

    assert budget.max_calls == 2
    assert budget.tool_result_char_budget == 640


def test_budget_caps_result_chars_by_call_count_and_global_limit() -> None:
    budget = provider_round_budget(
        prompt_budget_tokens=12_000,
        response_call_count=4,
        remaining_prompt_tokens=12_000,
    )

    assert budget.max_calls == 4
    assert budget.tool_result_char_budget == 640


@pytest.mark.parametrize(
    "kwargs",
    [
        {"prompt_budget_tokens": 0, "response_call_count": 1},
        {"prompt_budget_tokens": -1, "response_call_count": 1},
        {"prompt_budget_tokens": 4096, "response_call_count": 0},
        {"prompt_budget_tokens": 4096, "response_call_count": -1},
        {"prompt_budget_tokens": 4096, "response_call_count": 1, "remaining_prompt_tokens": -1},
        {"prompt_budget_tokens": True, "response_call_count": 1},
        {"prompt_budget_tokens": 4096, "response_call_count": True},
    ],
)
def test_budget_rejects_invalid_numeric_facts(kwargs) -> None:
    with pytest.raises(ProviderRoundBudgetError):
        provider_round_budget(**kwargs)


def test_budget_rejects_non_integer_remaining_prompt_fact() -> None:
    with pytest.raises(ProviderRoundBudgetError, match="remaining_prompt_tokens"):
        provider_round_budget(
            prompt_budget_tokens=4096,
            response_call_count=1,
            remaining_prompt_tokens="1024",
        )
