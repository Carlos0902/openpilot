"""Strict DeepSeek thinking/tool-call continuation helpers."""

from __future__ import annotations

from collections.abc import Sequence

from core.llm import LLMMessage, LLMResponse, LLMToolResult
from core.tool_roundtrip import ToolRoundTripError, append_tool_round_trip


class DeepSeekToolRoundTripError(ValueError):
    """Raised when a DeepSeek tool continuation would lose provider state."""


def append_deepseek_tool_round_trip(
    messages: Sequence[LLMMessage],
    response: LLMResponse,
    tool_results: Sequence[LLMToolResult],
    *,
    require_reasoning_content: bool = True,
) -> list[LLMMessage]:
    """Append one assistant tool-call turn and its matching tool results.

    DeepSeek thinking requests require the assistant reasoning content and
    tool calls to be sent back unchanged before the ``role=tool`` messages.
    Callers using a capability profile that explicitly disables reasoning may
    set ``require_reasoning_content=False``; the tool-call/result identity
    checks remain mandatory. This helper returns a new list and never mutates
    the caller's history.
    """

    try:
        return append_tool_round_trip(
            messages,
            response,
            tool_results,
            require_reasoning_content=require_reasoning_content,
            reasoning_error_message="DeepSeek tool continuation requires assistant reasoning_content",
        )
    except ToolRoundTripError as exc:
        raise DeepSeekToolRoundTripError(str(exc)) from exc
