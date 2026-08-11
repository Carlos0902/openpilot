"""Correlate event-loop outcomes into bounded provider tool results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.llm import LLMResponse, LLMToolResult
from core.provider_tool_batch_admission import (
    MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE,
)
from core.provider_tool_duplicate_partition import ProviderToolDuplicateBlock
from core.provider_tool_result_payload import (
    MAX_PROVIDER_TOOL_RESULT_CHARS,
    MIN_PROVIDER_TOOL_RESULT_CHARS,
    ProviderToolResultPayloadError,
    fit_provider_tool_result_payload,
)
from core.provider_tool_result_projection import (
    ProviderToolResultProjectionError,
    project_provider_tool_result,
)
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import ToolErrorMetadata


class ProviderToolResultBatchError(ValueError):
    """Raised when provider result identity or payload facts are invalid."""


def provider_tool_result_batch(
    response: LLMResponse,
    loop_result: ToolEventLoopRunResult,
    *,
    duplicate_blocks: Sequence[ProviderToolDuplicateBlock] = (),
    declared_window_complete_ids: Sequence[str] = (),
    char_budget: int = MAX_PROVIDER_TOOL_RESULT_CHARS,
) -> tuple[LLMToolResult, ...]:
    """Return one bounded result for every provider call in response order."""

    if not isinstance(response, LLMResponse):
        raise ProviderToolResultBatchError("response must be LLMResponse")
    if not isinstance(loop_result, ToolEventLoopRunResult):
        raise ProviderToolResultBatchError(
            "loop_result must be ToolEventLoopRunResult"
        )
    if (
        type(char_budget) is not int
        or char_budget < MIN_PROVIDER_TOOL_RESULT_CHARS
        or char_budget > MAX_PROVIDER_TOOL_RESULT_CHARS
    ):
        raise ProviderToolResultBatchError(
            "char_budget must be an integer within provider result bounds"
        )

    calls = tuple(response.tool_calls)
    if len(calls) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE:
        raise ProviderToolResultBatchError(
            "provider response exceeds the static tool-call limit"
        )
    call_by_id: dict[str, Any] = {}
    for call in calls:
        if call.id in call_by_id:
            raise ProviderToolResultBatchError(
                "provider response call IDs must be unique"
            )
        call_by_id[call.id] = call

    block_by_id = _validated_duplicate_blocks(
        duplicate_blocks,
        call_by_id=call_by_id,
    )
    declared_ids = _validated_declared_window_ids(
        declared_window_complete_ids,
        call_by_id=call_by_id,
    )
    result_by_id = _provider_results_by_id(
        loop_result.tool_results,
        call_by_id=call_by_id,
    )
    error_by_id = _provider_errors_by_id(
        loop_result.loop_metadata.recoverable_errors,
        call_by_id=call_by_id,
    )
    contradictory_ids = set(block_by_id).intersection(result_by_id)
    if contradictory_ids:
        raise ProviderToolResultBatchError(
            "duplicate-blocked calls cannot also contain execution results"
        )
    if not declared_ids.issubset(result_by_id):
        raise ProviderToolResultBatchError(
            "declared complete windows require an execution result"
        )
    if not set(error_by_id).issubset(result_by_id):
        raise ProviderToolResultBatchError(
            "provider recoverable errors require an execution result"
        )

    projected: list[LLMToolResult] = []
    for call in calls:
        payload = _payload_for_call(
            call,
            item=result_by_id.get(call.id),
            error=error_by_id.get(call.id),
            duplicate_block=block_by_id.get(call.id),
            declared_window_complete=call.id in declared_ids,
        )
        try:
            content = fit_provider_tool_result_payload(
                payload,
                limit=char_budget,
            )
        except ProviderToolResultPayloadError as exc:
            raise ProviderToolResultBatchError(
                "provider tool-result payload failed bounded projection"
            ) from exc
        projected.append(
            LLMToolResult(
                tool_call_id=call.id,
                content=content,
            )
        )
    return tuple(projected)


def _payload_for_call(
    call: Any,
    *,
    item: Mapping[str, Any] | None,
    error: ToolErrorMetadata | None,
    duplicate_block: ProviderToolDuplicateBlock | None,
    declared_window_complete: bool,
) -> dict[str, Any]:
    if duplicate_block is not None:
        return {
            "success": False,
            "tool": duplicate_block.tool_name,
            "error_type": duplicate_block.error_type,
            "error": duplicate_block.error_message,
            "previous_call_id": duplicate_block.previous_provider_call_id,
            "suggested_recovery": duplicate_block.suggested_recovery,
        }
    if item is None:
        return {
            "success": False,
            "tool": call.function.name,
            "error_type": "ProviderToolBatchAborted",
            "error": (
                "The project stopped this batch before executing this call; "
                "retry it in a later round."
            ),
        }

    success = item.get("success")
    if type(success) is not bool:
        raise ProviderToolResultBatchError(
            "provider tool-result success must be a literal boolean"
        )
    tool_name = item.get("tool")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ProviderToolResultBatchError(
            "provider tool-result tool must be a non-empty string"
        )
    if tool_name != call.function.name:
        raise ProviderToolResultBatchError(
            "provider tool-result tool must match the response call"
        )
    if error is not None and success:
        raise ProviderToolResultBatchError(
            "successful provider tool result cannot own a recoverable error"
        )
    if error is not None and error.tool_name != tool_name:
        raise ProviderToolResultBatchError(
            "provider recoverable error tool must match the response call"
        )

    source_id = item.get("call_id")
    if source_id is None:
        source_id = call.id
    if not isinstance(source_id, str) or not source_id.strip():
        raise ProviderToolResultBatchError(
            "provider tool-result project call ID must be a non-empty string"
        )
    try:
        result_projection, artifact_ref = project_provider_tool_result(
            item.get("result"),
            source_id=source_id,
            provider_call_id=call.id,
            declared_window_complete=declared_window_complete,
        )
    except ProviderToolResultProjectionError as exc:
        raise ProviderToolResultBatchError(
            "provider tool result failed artifact projection"
        ) from exc

    payload = {
        "success": success,
        "tool": tool_name,
        "result": result_projection,
        "artifact_ref": artifact_ref,
        "error_type": (
            error.error_type if error is not None else item.get("error_type")
        ),
        "error": (
            error.error_message if error is not None else item.get("error")
        ),
        "suggested_recovery": (
            error.suggested_recovery
            if error is not None
            else item.get("suggested_recovery")
        ),
    }
    return {
        key: value
        for key, value in payload.items()
        if value is not None
    }


def _validated_duplicate_blocks(
    value: Sequence[ProviderToolDuplicateBlock],
    *,
    call_by_id: Mapping[str, Any],
) -> dict[str, ProviderToolDuplicateBlock]:
    blocks = _bounded_sequence(value, field="duplicate_blocks")
    by_id: dict[str, ProviderToolDuplicateBlock] = {}
    for block in blocks:
        if not isinstance(block, ProviderToolDuplicateBlock):
            raise ProviderToolResultBatchError(
                "duplicate_blocks must contain typed duplicate blocks"
            )
        if block.provider_call_id not in call_by_id:
            raise ProviderToolResultBatchError(
                "duplicate block must match a response call"
            )
        if block.provider_call_id in by_id:
            raise ProviderToolResultBatchError(
                "duplicate block provider IDs must be unique"
            )
        call = call_by_id[block.provider_call_id]
        if block.tool_name != call.function.name:
            raise ProviderToolResultBatchError(
                "duplicate block tool must match the response call"
            )
        by_id[block.provider_call_id] = block
    return by_id


def _validated_declared_window_ids(
    value: Sequence[str],
    *,
    call_by_id: Mapping[str, Any],
) -> frozenset[str]:
    items = _bounded_sequence(
        value,
        field="declared_window_complete_ids",
    )
    declared: set[str] = set()
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ProviderToolResultBatchError(
                "declared window IDs must be non-empty strings"
            )
        if item not in call_by_id:
            raise ProviderToolResultBatchError(
                "declared window ID must match a response call"
            )
        if item in declared:
            raise ProviderToolResultBatchError(
                "declared window IDs must be unique"
            )
        declared.add(item)
    return frozenset(declared)


def _provider_results_by_id(
    value: Any,
    *,
    call_by_id: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    items = _bounded_sequence(value, field="loop_result.tool_results")
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise ProviderToolResultBatchError(
                "event-loop tool results must be mappings"
            )
        provider_call_id = item.get("provider_call_id")
        if provider_call_id is None:
            continue
        if not isinstance(provider_call_id, str) or not provider_call_id.strip():
            raise ProviderToolResultBatchError(
                "event-loop provider call IDs must be non-empty strings"
            )
        if provider_call_id not in call_by_id:
            raise ProviderToolResultBatchError(
                "event-loop result provider ID must match a response call"
            )
        if provider_call_id in by_id:
            raise ProviderToolResultBatchError(
                "event-loop result provider IDs must be unique"
            )
        by_id[provider_call_id] = item
    return by_id


def _provider_errors_by_id(
    value: Any,
    *,
    call_by_id: Mapping[str, Any],
) -> dict[str, ToolErrorMetadata]:
    items = _bounded_sequence(value, field="recoverable_errors")
    by_id: dict[str, ToolErrorMetadata] = {}
    for error in items:
        if not isinstance(error, ToolErrorMetadata):
            raise ProviderToolResultBatchError(
                "recoverable_errors must contain ToolErrorMetadata"
            )
        provider_call_id = error.provider_call_id
        if provider_call_id is None:
            continue
        if provider_call_id not in call_by_id:
            raise ProviderToolResultBatchError(
                "recoverable error provider ID must match a response call"
            )
        if provider_call_id in by_id:
            raise ProviderToolResultBatchError(
                "recoverable error provider IDs must be unique"
            )
        by_id[provider_call_id] = error
    return by_id


def _bounded_sequence(value: Any, *, field: str) -> tuple[Any, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ProviderToolResultBatchError(
            f"{field} must be a bounded sequence"
        )
    if len(value) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE:
        raise ProviderToolResultBatchError(
            f"{field} exceeds the static provider call limit"
        )
    return tuple(value)


__all__ = [
    "ProviderToolResultBatchError",
    "provider_tool_result_batch",
]
