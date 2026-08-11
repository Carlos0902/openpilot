from __future__ import annotations

import pytest

from core.llm import LLMResponse, LLMToolCall, LLMToolFunctionCall
from core.provider_final_response_transition import (
    ProviderFinalResponseAction,
    ProviderFinalResponseError,
    provider_final_response_transition,
)


def _response(
    *,
    content: str = "Finished.",
    tool_calls: list[LLMToolCall] | None = None,
    finish_reason: str = "stop",
    usage=None,
) -> LLMResponse:
    values = dict(
        content=content,
        reasoning_content=None,
        tool_calls=tool_calls or [],
        model="test-model",
        provider="test-provider",
        finish_reason=finish_reason,
    )
    if usage is not None:
        values["usage"] = usage
    return LLMResponse(**values)


def _call() -> LLMToolCall:
    return LLMToolCall(
        id="provider-reader",
        function=LLMToolFunctionCall(
            name="file_reader",
            arguments='{"file_path":"README.md"}',
        ),
    )


def test_transition_completes_non_tool_response() -> None:
    decision = provider_final_response_transition(
        _response(),
        finalization_pending=False,
    )

    assert decision.action is ProviderFinalResponseAction.COMPLETE
    assert decision.error_code is None
    assert decision.finalization_pending is False


def test_transition_executes_tools_only_outside_finalization() -> None:
    decision = provider_final_response_transition(
        _response(content="", tool_calls=[_call()]),
        finalization_pending=False,
    )

    assert decision.action is ProviderFinalResponseAction.EXECUTE_TOOLS
    assert decision.error_code is None


def test_transition_rejects_tool_call_during_finalization() -> None:
    decision = provider_final_response_transition(
        _response(content="", tool_calls=[_call()]),
        finalization_pending=True,
    )

    assert decision.action is ProviderFinalResponseAction.FAIL
    assert decision.error_code == "ProviderToolFinalizationToolCall"


def test_transition_rejects_empty_finalization() -> None:
    decision = provider_final_response_transition(
        _response(content="   "),
        finalization_pending=True,
    )

    assert decision.action is ProviderFinalResponseAction.FAIL
    assert decision.error_code == "ProviderToolFinalizationEmpty"


@pytest.mark.parametrize("finish_reason", ["length", "max_tokens", "MAX_TOKENS"])
def test_transition_identifies_reasoning_exhausted_finalization(
    finish_reason: str,
) -> None:
    decision = provider_final_response_transition(
        _response(
            content="",
            finish_reason=finish_reason,
            usage={
                "completion_tokens": 64,
                "completion_tokens_details": {"reasoning_tokens": 64},
            },
        ),
        finalization_pending=True,
    )

    assert decision.action is ProviderFinalResponseAction.FAIL
    assert decision.error_code == "ProviderToolFinalizationReasoningExhausted"


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"completion_tokens": "64", "completion_tokens_details": {}},
        {"completion_tokens": True, "completion_tokens_details": {"reasoning_tokens": 1}},
        {"completion_tokens": 0, "completion_tokens_details": {"reasoning_tokens": 0}},
        {"completion_tokens": 64, "completion_tokens_details": {"reasoning_tokens": "64"}},
    ],
)
def test_transition_falls_back_to_empty_for_untrusted_usage(usage) -> None:
    decision = provider_final_response_transition(
        _response(content="", finish_reason="length", usage=usage),
        finalization_pending=True,
    )

    assert decision.error_code == "ProviderToolFinalizationEmpty"


@pytest.mark.parametrize(
    ("response", "finalization_pending"),
    [
        (object(), False),
        (_response(), "yes"),
    ],
)
def test_transition_rejects_invalid_entry_facts(
    response,
    finalization_pending,
) -> None:
    with pytest.raises(ProviderFinalResponseError):
        provider_final_response_transition(
            response,
            finalization_pending=finalization_pending,
        )
