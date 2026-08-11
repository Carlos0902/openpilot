"""Classify one exact provider validation execution without contradictory flags."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

from core.provider_tool_roundtrip_contracts import MAX_PROVIDER_TOOL_ATTEMPTS
from core.tool_event_loop import ToolEventLoopRunResult
from core.validation_command import (
    MAX_VALIDATION_COMMAND_CHARS,
    validation_commands_match,
)
from metadata import metadata_summary


class ProviderValidationObservation(str, Enum):
    """Mutually exclusive evidence from one required validation command."""

    NOT_OBSERVED = "not_observed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ProviderValidationObservationError(ValueError):
    """Raised when exact validation evidence is malformed or contradictory."""


def provider_validation_observation(
    loop_result: ToolEventLoopRunResult,
    *,
    validation_command: str,
) -> ProviderValidationObservation:
    """Return the single exact validation outcome observed in one tool loop."""

    expected = _validation_command(validation_command)
    if not isinstance(loop_result, ToolEventLoopRunResult):
        raise ProviderValidationObservationError(
            "loop_result must be ToolEventLoopRunResult"
        )
    tool_results = loop_result.tool_results
    if not isinstance(tool_results, list):
        raise ProviderValidationObservationError(
            "tool_results must be a bounded list"
        )
    if len(tool_results) > MAX_PROVIDER_TOOL_ATTEMPTS:
        raise ProviderValidationObservationError(
            "tool_results exceed the provider tool attempt limit"
        )
    for index, item in enumerate(tool_results):
        if not isinstance(item, Mapping):
            raise ProviderValidationObservationError(
                f"tool_results[{index}] must be a mapping"
            )

    exact_results = [
        item
        for item in tool_results
        if item.get("tool") == "command_executor"
        and _matches_expected_command(item, expected)
    ]
    if not exact_results:
        return ProviderValidationObservation.NOT_OBSERVED
    if len(exact_results) != 1:
        raise ProviderValidationObservationError(
            "the exact validation command must execute exactly once"
        )
    return _validation_outcome(exact_results[0])


def _validation_command(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderValidationObservationError(
            "validation_command must be a non-empty string"
        )
    if len(value) > MAX_VALIDATION_COMMAND_CHARS:
        raise ProviderValidationObservationError(
            "validation_command exceeds the provider character limit"
        )
    return value


def _matches_expected_command(
    item: Mapping[str, Any],
    expected: str,
) -> bool:
    input_metadata = item.get("input_metadata")
    if not isinstance(input_metadata, Mapping):
        return False
    actual = input_metadata.get("requested_command") or input_metadata.get("command")
    return validation_commands_match(expected, actual)


def _validation_outcome(
    item: Mapping[str, Any],
) -> ProviderValidationObservation:
    item_success = item.get("success")
    if type(item_success) is not bool:
        raise ProviderValidationObservationError(
            "exact validation outcome requires literal item success"
        )
    if not item_success:
        return ProviderValidationObservation.FAILED

    result = metadata_summary(item.get("result"))
    if not isinstance(result, Mapping):
        raise ProviderValidationObservationError(
            "exact validation outcome requires a result mapping"
        )
    result_success = result.get("success")
    exit_code = result.get("exit_code")
    if type(result_success) is not bool or type(exit_code) is not int:
        raise ProviderValidationObservationError(
            "exact validation outcome requires literal success and exit_code"
        )
    if not result_success or exit_code != 0:
        return ProviderValidationObservation.FAILED
    return ProviderValidationObservation.SUCCEEDED


__all__ = [
    "ProviderValidationObservation",
    "ProviderValidationObservationError",
    "provider_validation_observation",
]
