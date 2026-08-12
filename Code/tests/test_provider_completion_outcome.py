from __future__ import annotations

import pytest

from core.llm import LLMResponse, LLMToolCall, LLMToolFunctionCall
from core.provider_completion_outcome import (
    ProviderCompletionOutcome,
    ProviderCompletionOutcomeError,
    provider_completion_outcome,
)


def _response(*, content="done", tool_calls=None, finish_reason="stop"):
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        model="model",
        provider="provider",
        finish_reason=finish_reason,
    )


def _call():
    return LLMToolCall(
        id="call-1",
        function=LLMToolFunctionCall(name="file_reader", arguments="{}"),
    )


def test_outcome_classifies_normal_completion() -> None:
    assert provider_completion_outcome(_response()) is ProviderCompletionOutcome.NORMAL


def test_outcome_classifies_tool_progress_before_content() -> None:
    assert provider_completion_outcome(
        _response(content="", tool_calls=[_call()])
    ) is ProviderCompletionOutcome.TOOL_PROGRESS


@pytest.mark.parametrize("finish_reason", ["length", "max_tokens", "LENGTH"])
def test_outcome_classifies_truncation(finish_reason: str) -> None:
    assert provider_completion_outcome(
        _response(content="partial", finish_reason=finish_reason)
    ) is ProviderCompletionOutcome.TRUNCATED


def test_outcome_classifies_empty_response() -> None:
    assert provider_completion_outcome(_response(content="   ")) is ProviderCompletionOutcome.EMPTY_RESPONSE


@pytest.mark.parametrize(
    "response",
    [object(), _response(finish_reason=None)],
)
def test_outcome_rejects_invalid_response_facts(response) -> None:
    with pytest.raises(ProviderCompletionOutcomeError):
        provider_completion_outcome(response)
