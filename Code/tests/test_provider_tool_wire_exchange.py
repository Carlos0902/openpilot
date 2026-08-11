from __future__ import annotations

import json

import pytest

from core.llm import (
    LLMResponse,
    LLMToolCall,
    LLMToolFunctionCall,
    LLMToolResult,
)
from core.provider_tool_batch_admission import (
    MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE,
)
from core.provider_tool_duplicate_partition import ProviderToolDuplicateBlock
from core.provider_tool_wire_exchange import (
    MAX_PROVIDER_WIRE_TOOL_RESULT_CHARS,
    ProviderToolWireExchangeError,
    duplicate_block_to_tool_result,
    provider_tool_wire_exchange,
)


def _call(call_id, *, path="README.md"):
    return LLMToolCall(
        id=call_id,
        function=LLMToolFunctionCall(
            name="file_reader",
            arguments=json.dumps({"file_path": path}),
        ),
    )


def _response(calls):
    return LLMResponse(
        content="",
        reasoning_content="Use the tool evidence.",
        tool_calls=list(calls),
        model="model",
        provider="provider",
        finish_reason="tool_calls",
    )


def _result(call_id, payload=None):
    return LLMToolResult(
        tool_call_id=call_id,
        content=json.dumps(payload or {"success": True}),
    )


def test_duplicate_block_projects_bounded_provider_result() -> None:
    block = ProviderToolDuplicateBlock(
        provider_call_id="provider-2",
        previous_provider_call_id="provider-1",
        tool_name="file_reader",
        signature="a" * 64,
        round_index=2,
    )

    result = duplicate_block_to_tool_result(block)
    payload = json.loads(result.content)

    assert result.tool_call_id == "provider-2"
    assert payload == {
        "success": False,
        "tool": "file_reader",
        "error_type": "ProviderToolDuplicateAttempt",
        "error": "The same normalized tool input was already attempted.",
        "previous_call_id": "provider-1",
        "suggested_recovery": (
            "Choose a new evidence-backed path or finish with existing evidence."
        ),
    }
    assert len(result.content) <= MAX_PROVIDER_WIRE_TOOL_RESULT_CHARS


def test_duplicate_block_projection_rejects_invalid_input() -> None:
    with pytest.raises(ProviderToolWireExchangeError):
        duplicate_block_to_tool_result(object())


def test_wire_exchange_preserves_assistant_fields_and_call_order() -> None:
    calls = (_call("provider-1"), _call("provider-2", path="app.py"))
    response = _response(calls)
    exchange = provider_tool_wire_exchange(
        response,
        (_result("provider-2"), _result("provider-1")),
    )

    assert exchange[0].role == "assistant"
    assert exchange[0].reasoning_content == response.reasoning_content
    assert exchange[0].tool_calls == list(calls)
    assert [message.tool_call_id for message in exchange[1:]] == [
        "provider-1",
        "provider-2",
    ]


def test_wire_exchange_accepts_exact_call_limit() -> None:
    calls = tuple(
        _call(f"provider-{index}")
        for index in range(MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE)
    )
    exchange = provider_tool_wire_exchange(
        _response(calls),
        tuple(_result(call.id) for call in calls),
    )

    assert len(exchange) == MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE + 1


@pytest.mark.parametrize(
    "calls, results",
    [
        ((_call("provider-1"),), ()),
        (
            (_call("provider-1"),),
            (_result("provider-1"), _result("extra")),
        ),
        (
            (_call("provider-1"),),
            (_result("provider-1"), _result("provider-1")),
        ),
        (
            (_call("provider-1"), _call("provider-1")),
            (_result("provider-1"),),
        ),
    ],
)
def test_wire_exchange_rejects_identity_mismatch(calls, results) -> None:
    with pytest.raises(ProviderToolWireExchangeError):
        provider_tool_wire_exchange(_response(calls), results)


def test_wire_exchange_rejects_overlong_tool_result() -> None:
    call = _call("provider-1")
    exact = LLMToolResult(
        tool_call_id=call.id,
        content="x" * MAX_PROVIDER_WIRE_TOOL_RESULT_CHARS,
    )
    overflow = exact.model_copy(
        update={"content": exact.content + "x"},
    )

    assert len(provider_tool_wire_exchange(_response((call,)), (exact,))) == 2
    with pytest.raises(ProviderToolWireExchangeError):
        provider_tool_wire_exchange(_response((call,)), (overflow,))


def test_wire_exchange_rejects_more_than_static_call_limit() -> None:
    calls = tuple(
        _call(f"provider-{index}")
        for index in range(MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE + 1)
    )

    with pytest.raises(ProviderToolWireExchangeError):
        provider_tool_wire_exchange(
            _response(calls),
            tuple(_result(call.id) for call in calls),
        )


@pytest.mark.parametrize(
    "response, results",
    [
        (object(), ()),
        (_response(()), (object(),)),
        (_response(()), (_result("provider-1") for _ in range(1))),
    ],
)
def test_wire_exchange_rejects_invalid_inputs(response, results) -> None:
    with pytest.raises(ProviderToolWireExchangeError):
        provider_tool_wire_exchange(response, results)


def test_wire_exchange_allows_empty_tool_call_response() -> None:
    response = _response(())

    exchange = provider_tool_wire_exchange(response, ())

    assert len(exchange) == 1
    assert exchange[0].role == "assistant"
