"""Remove generated-code bodies from retained provider execution evidence."""

from __future__ import annotations

import hashlib
from typing import Any

from core.provider_tool_roundtrip_contracts import MAX_PROVIDER_TOOL_ATTEMPTS
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import (
    ToolCallMetadata,
    ToolErrorMetadata,
    ToolEventMetadata,
    ToolInputMetadata,
    ToolLoopMetadata,
)
from metadata.artifacts import MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_CHARS


class ProviderGeneratedUnitRedactionError(ValueError):
    """Raised when retained provider evidence cannot be redacted safely."""


def redact_provider_generated_units(
    loop_result: ToolEventLoopRunResult,
) -> ToolEventLoopRunResult:
    """Atomically replace generated code with bounded integrity diagnostics."""

    if not isinstance(loop_result, ToolEventLoopRunResult):
        raise ProviderGeneratedUnitRedactionError(
            "loop_result must be ToolEventLoopRunResult"
        )
    tool_results = _bounded_list(loop_result.tool_results, "tool_results")
    loop_metadata = loop_result.loop_metadata
    if not isinstance(loop_metadata, ToolLoopMetadata):
        raise ProviderGeneratedUnitRedactionError(
            "loop_metadata must be ToolLoopMetadata"
        )
    events = _bounded_list(loop_metadata.events, "events")
    invocations = _bounded_list(
        loop_metadata.tool_invocations,
        "tool_invocations",
    )
    errors = _bounded_list(
        loop_metadata.recoverable_errors,
        "recoverable_errors",
    )

    mapping_targets: dict[int, tuple[dict[str, Any], int, str]] = {}
    typed_targets: dict[int, tuple[ToolInputMetadata, int, str]] = {}
    for index, item in enumerate(tool_results):
        if not isinstance(item, dict):
            raise ProviderGeneratedUnitRedactionError(
                f"tool_results[{index}] must be a mapping"
            )
        _collect_input(
            item.get("input_metadata"),
            location=f"tool_results[{index}].input_metadata",
            mapping_targets=mapping_targets,
            typed_targets=typed_targets,
        )
    for index, event in enumerate(events):
        _collect_event(
            event,
            location=f"events[{index}]",
            typed_targets=typed_targets,
        )
    for index, invocation in enumerate(invocations):
        _collect_call(
            invocation,
            location=f"tool_invocations[{index}]",
            typed_targets=typed_targets,
        )
    for index, error in enumerate(errors):
        _collect_error(
            error,
            location=f"recoverable_errors[{index}]",
            typed_targets=typed_targets,
        )

    for input_metadata, chars, digest in mapping_targets.values():
        input_metadata.update(
            {
                "generated_unit": None,
                "generated_unit_chars": chars,
                "generated_unit_sha256": digest,
            }
        )
    for input_metadata, chars, digest in typed_targets.values():
        input_metadata.runtime_handles = {
            **input_metadata.runtime_handles,
            "_generated_unit_chars": chars,
            "_generated_unit_sha256": digest,
        }
        input_metadata.generated_unit = None
    return loop_result


def _bounded_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProviderGeneratedUnitRedactionError(
            f"{label} must be a bounded list"
        )
    if len(value) > MAX_PROVIDER_TOOL_ATTEMPTS:
        raise ProviderGeneratedUnitRedactionError(
            f"{label} exceeds the provider tool attempt limit"
        )
    return value


def _collect_input(
    input_metadata: Any,
    *,
    location: str,
    mapping_targets: dict[int, tuple[dict[str, Any], int, str]] | None,
    typed_targets: dict[int, tuple[ToolInputMetadata, int, str]],
) -> None:
    if input_metadata is None:
        return
    if isinstance(input_metadata, ToolInputMetadata):
        generated_unit = input_metadata.generated_unit
        if generated_unit is None:
            return
        if id(input_metadata) in typed_targets:
            return
        if not isinstance(input_metadata.runtime_handles, dict):
            raise ProviderGeneratedUnitRedactionError(
                f"{location}.runtime_handles must be a mapping"
            )
        chars, digest = _generated_unit_diagnostics(
            generated_unit,
            location=location,
        )
        typed_targets[id(input_metadata)] = (
            input_metadata,
            chars,
            digest,
        )
        return
    if isinstance(input_metadata, dict) and mapping_targets is not None:
        if "generated_unit" not in input_metadata:
            return
        generated_unit = input_metadata["generated_unit"]
        if generated_unit is None:
            return
        if id(input_metadata) in mapping_targets:
            return
        chars, digest = _generated_unit_diagnostics(
            generated_unit,
            location=location,
        )
        mapping_targets[id(input_metadata)] = (
            input_metadata,
            chars,
            digest,
        )
        return
    expected = (
        "a mapping or ToolInputMetadata"
        if mapping_targets is not None
        else "ToolInputMetadata"
    )
    raise ProviderGeneratedUnitRedactionError(
        f"{location} must be {expected}"
    )


def _collect_call(
    tool_call: Any,
    *,
    location: str,
    typed_targets: dict[int, tuple[ToolInputMetadata, int, str]],
) -> None:
    if not isinstance(tool_call, ToolCallMetadata):
        raise ProviderGeneratedUnitRedactionError(
            f"{location} must contain ToolCallMetadata"
        )
    _collect_input(
        tool_call.input_metadata,
        location=f"{location}.input_metadata",
        mapping_targets=None,
        typed_targets=typed_targets,
    )


def _collect_error(
    tool_error: Any,
    *,
    location: str,
    typed_targets: dict[int, tuple[ToolInputMetadata, int, str]],
) -> None:
    if not isinstance(tool_error, ToolErrorMetadata):
        raise ProviderGeneratedUnitRedactionError(
            f"{location} must contain ToolErrorMetadata"
        )
    _collect_input(
        tool_error.input_metadata,
        location=f"{location}.input_metadata",
        mapping_targets=None,
        typed_targets=typed_targets,
    )


def _collect_event(
    event: Any,
    *,
    location: str,
    typed_targets: dict[int, tuple[ToolInputMetadata, int, str]],
) -> None:
    if not isinstance(event, ToolEventMetadata):
        raise ProviderGeneratedUnitRedactionError(
            f"{location} must contain ToolEventMetadata"
        )
    _collect_input(
        event.input_metadata,
        location=f"{location}.input_metadata",
        mapping_targets=None,
        typed_targets=typed_targets,
    )
    if event.tool_call is not None:
        _collect_call(
            event.tool_call,
            location=f"{location}.tool_call",
            typed_targets=typed_targets,
        )
    if event.tool_error is not None:
        _collect_error(
            event.tool_error,
            location=f"{location}.tool_error",
            typed_targets=typed_targets,
        )


def _generated_unit_diagnostics(
    generated_unit: Any,
    *,
    location: str,
) -> tuple[int, str]:
    if not isinstance(generated_unit, str):
        raise ProviderGeneratedUnitRedactionError(
            f"{location}.generated_unit must be a string"
        )
    if len(generated_unit) > MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_CHARS:
        raise ProviderGeneratedUnitRedactionError(
            f"{location}.generated_unit exceeds the provider code artifact limit"
        )
    return (
        len(generated_unit),
        hashlib.sha256(generated_unit.encode("utf-8")).hexdigest(),
    )


__all__ = [
    "ProviderGeneratedUnitRedactionError",
    "redact_provider_generated_units",
]
