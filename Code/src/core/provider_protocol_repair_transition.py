"""Derive bounded model-visible protocol-repair actions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.provider_tool_roundtrip_contracts import MAX_PROVIDER_ROUND_TRIP_ROUNDS


class ProviderProtocolRepairAction(str, Enum):
    """Next action after a repairable provider protocol failure."""

    NONE = "none"
    REQUEST_REPAIR = "request_repair"
    FAIL = "fail"


class ProviderProtocolRepairErrorCode(str, Enum):
    """Stable terminal protocol-repair codes."""

    EXHAUSTED = "ProviderToolProtocolRepairExhausted"
    BUDGET_UNAVAILABLE = "ProviderToolProtocolRepairBudgetUnavailable"


class ProviderProtocolRepairError(ValueError):
    """Raised when protocol-repair state facts are invalid."""


@dataclass(frozen=True)
class ProviderProtocolRepairTransition:
    """Derived repair action with no retry side effect."""

    action: ProviderProtocolRepairAction
    error_code: ProviderProtocolRepairErrorCode | None


def provider_protocol_repair_transition(
    *,
    model_repair_enabled: bool,
    repeated_protocol_attempt: bool,
    repairable_protocol_failure: bool,
    protocol_failure_count: int,
    round_index: int,
    max_rounds: int,
) -> ProviderProtocolRepairTransition:
    """Return one bounded repair action from explicit protocol facts."""

    _literal_bool(model_repair_enabled, "model_repair_enabled")
    _literal_bool(repeated_protocol_attempt, "repeated_protocol_attempt")
    _literal_bool(repairable_protocol_failure, "repairable_protocol_failure")
    if type(max_rounds) is not int or not 1 <= max_rounds <= MAX_PROVIDER_ROUND_TRIP_ROUNDS:
        raise ProviderProtocolRepairError("max_rounds must be within provider bounds")
    if type(round_index) is not int or not 1 <= round_index <= max_rounds:
        raise ProviderProtocolRepairError("round_index must be within round bounds")
    if type(protocol_failure_count) is not int or not 0 <= protocol_failure_count <= max_rounds:
        raise ProviderProtocolRepairError(
            "protocol_failure_count must be within round bounds"
        )
    if not model_repair_enabled or not repairable_protocol_failure:
        return _none()
    if repeated_protocol_attempt or protocol_failure_count > 1:
        return _failure(ProviderProtocolRepairErrorCode.EXHAUSTED)
    if round_index >= max_rounds:
        return _failure(ProviderProtocolRepairErrorCode.BUDGET_UNAVAILABLE)
    return ProviderProtocolRepairTransition(
        action=ProviderProtocolRepairAction.REQUEST_REPAIR,
        error_code=None,
    )


def _literal_bool(value: object, label: str) -> None:
    if type(value) is not bool:
        raise ProviderProtocolRepairError(f"{label} must be a literal boolean")


def _none() -> ProviderProtocolRepairTransition:
    return ProviderProtocolRepairTransition(
        action=ProviderProtocolRepairAction.NONE,
        error_code=None,
    )


def _failure(
    error_code: ProviderProtocolRepairErrorCode,
) -> ProviderProtocolRepairTransition:
    return ProviderProtocolRepairTransition(
        action=ProviderProtocolRepairAction.FAIL,
        error_code=error_code,
    )


__all__ = [
    "ProviderProtocolRepairAction",
    "ProviderProtocolRepairError",
    "ProviderProtocolRepairErrorCode",
    "ProviderProtocolRepairTransition",
    "provider_protocol_repair_transition",
]
