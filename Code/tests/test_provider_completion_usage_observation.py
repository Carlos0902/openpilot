from __future__ import annotations

import pytest

from core.llm import LLMResponse
from core.provider_completion_usage_observation import (
    MAX_PROVIDER_COMPLETION_TOKENS,
    ProviderCompletionUsageError,
    provider_completion_tokens,
)


def _response(usage=None) -> LLMResponse:
    values = {
        "content": "done",
        "model": "model",
        "provider": "provider",
        "finish_reason": "stop",
    }
    if usage is not None:
        values["usage"] = usage
    return LLMResponse(**values)


@pytest.mark.parametrize("field", ["completion_tokens", "output_tokens"])
def test_observation_accepts_nonnegative_integer_usage(field: str) -> None:
    assert provider_completion_tokens(_response({field: 0})) == 0
    assert provider_completion_tokens(_response({field: 123})) == 123
    assert provider_completion_tokens(
        _response({field: MAX_PROVIDER_COMPLETION_TOKENS})
    ) == MAX_PROVIDER_COMPLETION_TOKENS


def test_completion_tokens_take_precedence_over_output_tokens() -> None:
    assert provider_completion_tokens(
        _response({"completion_tokens": 12, "output_tokens": 99})
    ) == 12


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"completion_tokens": None},
        {"completion_tokens": True},
        {"completion_tokens": -1},
        {"completion_tokens": 1.5},
        {"completion_tokens": "12"},
        {"completion_tokens": MAX_PROVIDER_COMPLETION_TOKENS + 1},
    ],
)
def test_observation_treats_untrusted_or_out_of_bound_usage_as_unknown(usage) -> None:
    assert provider_completion_tokens(_response(usage)) is None


def test_observation_rejects_non_response_input() -> None:
    with pytest.raises(ProviderCompletionUsageError, match="LLMResponse"):
        provider_completion_tokens(object())
