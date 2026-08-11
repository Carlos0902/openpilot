"""Bounded provider tool-result and continuation-message projection."""

from __future__ import annotations

import json
from collections.abc import Sequence

from core.llm import LLMMessage, LLMResponse, LLMToolResult
from core.provider_tool_batch_admission import (
    MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE,
)
from core.provider_tool_duplicate_partition import ProviderToolDuplicateBlock
from core.provider_tool_result_payload import MAX_PROVIDER_TOOL_RESULT_CHARS

MAX_PROVIDER_WIRE_TOOL_RESULT_CHARS = MAX_PROVIDER_TOOL_RESULT_CHARS


class ProviderToolWireExchangeError(ValueError):
    """Raised when a provider continuation cannot preserve bounded identity."""


def duplicate_block_to_tool_result(
    block: ProviderToolDuplicateBlock,
) -> LLMToolResult:
    """Project one typed duplicate block without exposing internal state."""

    if not isinstance(block, ProviderToolDuplicateBlock):
        raise ProviderToolWireExchangeError(
            "block must be ProviderToolDuplicateBlock"
        )
    payload = {
        "success": False,
        "tool": block.tool_name,
        "error_type": block.error_type,
        "error": block.error_message,
        "previous_call_id": block.previous_provider_call_id,
        "suggested_recovery": block.suggested_recovery,
    }
    content = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(content) > MAX_PROVIDER_WIRE_TOOL_RESULT_CHARS:
        raise ProviderToolWireExchangeError(
            "duplicate result exceeds the provider wire character limit"
        )
    return LLMToolResult(
        tool_call_id=block.provider_call_id,
        content=content,
    )


def provider_tool_wire_exchange(
    response: LLMResponse,
    tool_results: Sequence[LLMToolResult],
) -> tuple[LLMMessage, ...]:
    """Return one assistant call message followed by exact matching results."""

    if not isinstance(response, LLMResponse):
        raise ProviderToolWireExchangeError("response must be LLMResponse")
    if not isinstance(tool_results, Sequence) or isinstance(
        tool_results,
        (str, bytes),
    ):
        raise ProviderToolWireExchangeError(
            "tool_results must be a bounded sequence"
        )
    calls = tuple(response.tool_calls)
    if len(calls) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE:
        raise ProviderToolWireExchangeError(
            "provider response exceeds the static tool-call limit"
        )
    if len(tool_results) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE:
        raise ProviderToolWireExchangeError(
            "provider tool results exceed the static call limit"
        )
    results = tuple(tool_results)
    if any(not isinstance(result, LLMToolResult) for result in results):
        raise ProviderToolWireExchangeError(
            "tool_results must contain only LLMToolResult values"
        )

    call_ids = [call.id for call in calls]
    result_ids = [result.tool_call_id for result in results]
    if len(call_ids) != len(set(call_ids)):
        raise ProviderToolWireExchangeError(
            "provider response call IDs must be unique"
        )
    if len(result_ids) != len(set(result_ids)):
        raise ProviderToolWireExchangeError(
            "provider tool-result IDs must be unique"
        )
    if set(call_ids) != set(result_ids):
        raise ProviderToolWireExchangeError(
            "provider tool results must match every response call exactly"
        )
    if any(
        len(result.content) > MAX_PROVIDER_WIRE_TOOL_RESULT_CHARS
        for result in results
    ):
        raise ProviderToolWireExchangeError(
            "provider tool result exceeds the wire character limit"
        )

    result_by_id = {result.tool_call_id: result for result in results}
    exchange = [
        LLMMessage(
            role="assistant",
            content=response.content or "",
            reasoning_content=response.reasoning_content,
            tool_calls=list(calls),
        )
    ]
    exchange.extend(
        LLMMessage(
            role="tool",
            content=result_by_id[call.id].content,
            tool_call_id=call.id,
        )
        for call in calls
    )
    return tuple(exchange)


__all__ = [
    "MAX_PROVIDER_WIRE_TOOL_RESULT_CHARS",
    "ProviderToolWireExchangeError",
    "duplicate_block_to_tool_result",
    "provider_tool_wire_exchange",
]
