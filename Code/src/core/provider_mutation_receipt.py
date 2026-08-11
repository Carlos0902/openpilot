"""Project one bounded body-free receipt from provider mutation evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.provider_tool_roundtrip_contracts import MAX_PROVIDER_TOOL_ATTEMPTS
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import ProviderCodeArtifactReference, metadata_summary
from tools.mutation_descriptor import FILE_MUTATION_TOOLS

MAX_PROVIDER_MUTATION_RECEIPT_CHANGED_RANGES = 8
MAX_PROVIDER_MUTATION_RECEIPT_PATH_CHARS = 4096
MAX_PROVIDER_MUTATION_RECEIPT_COMMAND_CHARS = 8192
MAX_PROVIDER_MUTATION_RECEIPT_OPERATION_CHARS = 64
MAX_PROVIDER_MUTATION_RECEIPT_TOOL_CHARS = 128
MAX_PROVIDER_MUTATION_RECEIPT_LINE = 10_000_000
MAX_PROVIDER_MUTATION_RECEIPT_BYTES = 1_000_000_000

_ARTIFACT_REFERENCE_FIELDS = (
    "kind",
    "source_id",
    "provider_call_id",
    "sha256",
    "bytes",
    "chars",
    "language",
)


class ProviderMutationReceiptError(ValueError):
    """Raised when mutation evidence cannot be projected safely."""


def provider_mutation_receipt(
    loop_result: ToolEventLoopRunResult,
    *,
    validation_command: str,
) -> dict[str, Any] | None:
    """Return the first successful mutation as bounded post-write evidence."""

    command = _bounded_string(
        validation_command,
        label="validation_command",
        max_chars=MAX_PROVIDER_MUTATION_RECEIPT_COMMAND_CHARS,
        preserve=True,
    )
    if not isinstance(loop_result, ToolEventLoopRunResult):
        raise ProviderMutationReceiptError(
            "loop_result must be ToolEventLoopRunResult"
        )
    tool_results = loop_result.tool_results
    if not isinstance(tool_results, list):
        raise ProviderMutationReceiptError(
            "tool_results must be a bounded list"
        )
    if len(tool_results) > MAX_PROVIDER_TOOL_ATTEMPTS:
        raise ProviderMutationReceiptError(
            "tool_results exceed the provider tool attempt limit"
        )
    for index, item in enumerate(tool_results):
        if not isinstance(item, Mapping):
            raise ProviderMutationReceiptError(
                f"tool_results[{index}] must be a mapping"
            )

    for item in tool_results:
        if item.get("success") is not True:
            continue
        if item.get("tool") not in FILE_MUTATION_TOOLS:
            continue
        return _project_mutation_item(item, validation_command=command)
    return None


def _project_mutation_item(
    item: Mapping[str, Any],
    *,
    validation_command: str,
) -> dict[str, Any]:
    tool = _bounded_string(
        item.get("tool"),
        label="tool",
        max_chars=MAX_PROVIDER_MUTATION_RECEIPT_TOOL_CHARS,
    )
    input_metadata = item.get("input_metadata")
    if not isinstance(input_metadata, Mapping):
        raise ProviderMutationReceiptError(
            "input_metadata must be a mapping for a successful mutation"
        )
    file_path = _bounded_string(
        input_metadata.get("file_path"),
        label="file_path",
        max_chars=MAX_PROVIDER_MUTATION_RECEIPT_PATH_CHARS,
    )
    operation_kind = _bounded_string(
        input_metadata.get("operation_kind"),
        label="operation_kind",
        max_chars=MAX_PROVIDER_MUTATION_RECEIPT_OPERATION_CHARS,
    )

    raw_result = item.get("result")
    summarized_result = (
        raw_result if isinstance(raw_result, Mapping) else metadata_summary(raw_result)
    )
    if not isinstance(summarized_result, Mapping):
        raise ProviderMutationReceiptError(
            "result must be a mapping for a successful mutation"
        )
    attributes = summarized_result.get("attributes", {})
    if not isinstance(attributes, Mapping):
        raise ProviderMutationReceiptError("result attributes must be a mapping")

    return {
        "status": "mutation_applied",
        "tool": tool,
        "file_path": file_path,
        "operation_kind": operation_kind,
        "artifact_ref": _artifact_reference(input_metadata.get("artifact_ref")),
        "bytes_written": _bytes_written(summarized_result.get("bytes_written")),
        "changed_ranges": _changed_ranges(attributes.get("changed_ranges", [])),
        "validation_command": validation_command,
    }


def _bounded_string(
    value: Any,
    *,
    label: str,
    max_chars: int,
    preserve: bool = False,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderMutationReceiptError(
            f"{label} must be a non-empty string"
        )
    if len(value) > max_chars:
        raise ProviderMutationReceiptError(
            f"{label} exceeds the receipt character limit"
        )
    return value if preserve else value.strip()


def _artifact_reference(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, ProviderCodeArtifactReference):
        candidate = value.model_dump(mode="python")
    elif isinstance(value, Mapping):
        candidate = {
            key: value[key]
            for key in _ARTIFACT_REFERENCE_FIELDS
            if key in value
        }
    else:
        raise ProviderMutationReceiptError(
            "artifact_ref must be a provider code artifact reference"
        )
    try:
        reference = ProviderCodeArtifactReference.model_validate(candidate)
    except ValueError as exc:
        raise ProviderMutationReceiptError(
            "artifact_ref is not a valid provider code artifact reference"
        ) from exc
    return reference.model_dump(mode="python")


def _bytes_written(value: Any) -> int | None:
    if value is None:
        return None
    if (
        type(value) is not int
        or value < 0
        or value > MAX_PROVIDER_MUTATION_RECEIPT_BYTES
    ):
        raise ProviderMutationReceiptError(
            "bytes_written must be a bounded non-negative integer"
        )
    return value


def _changed_ranges(value: Any) -> list[dict[str, int]]:
    if not isinstance(value, list):
        raise ProviderMutationReceiptError("changed_ranges must be a list")
    projected: list[dict[str, int]] = []
    for index, entry in enumerate(
        value[:MAX_PROVIDER_MUTATION_RECEIPT_CHANGED_RANGES]
    ):
        if not isinstance(entry, Mapping):
            raise ProviderMutationReceiptError(
                f"changed_ranges[{index}] must be a mapping"
            )
        line_start = entry.get("line_start")
        line_end = entry.get("line_end")
        if (
            type(line_start) is not int
            or type(line_end) is not int
            or line_start < 1
            or line_end < line_start
            or line_end > MAX_PROVIDER_MUTATION_RECEIPT_LINE
        ):
            raise ProviderMutationReceiptError(
                f"changed_ranges[{index}] must contain bounded line_start/line_end"
            )
        projected.append(
            {"line_start": line_start, "line_end": line_end}
        )
    return projected


__all__ = [
    "MAX_PROVIDER_MUTATION_RECEIPT_CHANGED_RANGES",
    "MAX_PROVIDER_MUTATION_RECEIPT_COMMAND_CHARS",
    "MAX_PROVIDER_MUTATION_RECEIPT_PATH_CHARS",
    "ProviderMutationReceiptError",
    "provider_mutation_receipt",
]
