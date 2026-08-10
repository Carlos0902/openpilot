from __future__ import annotations

import pytest

from core.llm import LLMMessage, LLMResponse, LLMToolCall, LLMToolFunctionCall, LLMToolResult
from core.tool_roundtrip import ToolRoundTripError, append_tool_round_trip


def _response(*, reasoning: str | None = None) -> LLMResponse:
    return LLMResponse(
        content="",
        reasoning_content=reasoning,
        tool_calls=[
            LLMToolCall(
                id="call-openai-1",
                function=LLMToolFunctionCall(name="read_file", arguments='{"path":"cli.py"}'),
            )
        ],
        model="gpt-4o-mini",
        provider="openai-compatible",
        finish_reason="tool_calls",
        usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    )


def test_openai_compatible_round_trip_preserves_call_and_result_wire_shape() -> None:
    original = [LLMMessage(role="user", content="Read cli.py")]
    messages = append_tool_round_trip(
        original,
        _response(),
        [LLMToolResult(tool_call_id="call-openai-1", content="source evidence")],
    )
    assert messages[0] == original[0]
    assert messages[1].role == "assistant"
    assert messages[1].reasoning_content is None
    assert messages[1].tool_calls[0].id == "call-openai-1"
    assert messages[2] == LLMMessage(
        role="tool",
        content="source evidence",
        tool_call_id="call-openai-1",
    )


def test_provider_neutral_round_trip_rejects_id_order_drift() -> None:
    with pytest.raises(ToolRoundTripError, match="match assistant call IDs"):
        append_tool_round_trip(
            [],
            _response(),
            [LLMToolResult(tool_call_id="wrong-id", content="bad")],
        )
