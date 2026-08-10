"""Provider-neutral assistant-tool continuation helpers."""

from __future__ import annotations

from collections.abc import Sequence

from core.llm import LLMMessage, LLMResponse, LLMToolResult


class ToolRoundTripError(ValueError):
    """Raised when a tool continuation would lose provider state."""


def append_tool_round_trip(
    messages: Sequence[LLMMessage],
    response: LLMResponse,
    tool_results: Sequence[LLMToolResult],
    *,
    require_reasoning_content: bool = False,
    reasoning_error_message: str = "assistant tool continuation requires reasoning_content",
) -> list[LLMMessage]:
    """Append one assistant tool-call turn and matching ``role=tool`` results.

    The helper owns only the provider-neutral call/result identity contract.
    Profiles that require provider-specific hidden reasoning state opt into the
    bounded ``require_reasoning_content`` check and may provide their own error
    wording.  The caller's message list is never mutated.
    """

    if not response.tool_calls:
        raise ToolRoundTripError("response contains no tool_calls")
    if require_reasoning_content and not response.reasoning_content:
        raise ToolRoundTripError(reasoning_error_message)

    call_ids = [call.id for call in response.tool_calls]
    if len(set(call_ids)) != len(call_ids):
        raise ToolRoundTripError("assistant tool call IDs must be unique")
    result_ids = [result.tool_call_id for result in tool_results]
    if result_ids != call_ids:
        raise ToolRoundTripError(
            f"tool results must match assistant call IDs in order: expected {call_ids}, got {result_ids}"
        )

    assistant = LLMMessage(
        role="assistant",
        content=response.content,
        reasoning_content=response.reasoning_content,
        tool_calls=list(response.tool_calls),
    )
    return [
        *list(messages),
        assistant,
        *[
            LLMMessage(role="tool", content=result.content, tool_call_id=result.tool_call_id)
            for result in tool_results
        ],
    ]
