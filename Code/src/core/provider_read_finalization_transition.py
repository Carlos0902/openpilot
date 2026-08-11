"""Derive read-only finalization from bounded evidence and round budget."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProviderReadFinalizationAction(str, Enum):
    """Next read-only finalization action for one completed round."""

    NONE = "none"
    REQUEST_FINALIZATION = "request_finalization"
    FAIL = "fail"


class ProviderReadFinalizationErrorCode(str, Enum):
    """Stable terminal codes for read-only finalization policy."""

    FINALIZATION_BUDGET_UNAVAILABLE = (
        "ProviderToolFinalizationBudgetUnavailable"
    )


class ProviderReadFinalizationError(ValueError):
    """Raised when read-finalization facts are malformed or contradictory."""


@dataclass(frozen=True)
class ProviderReadFinalizationTransition:
    """Derived read finalization state with no side effects."""

    action: ProviderReadFinalizationAction
    error_code: ProviderReadFinalizationErrorCode | None
    finalization_pending: bool
    finalization_requests: int


def provider_read_finalization_transition(
    *,
    page_cap_ready: bool,
    all_scoped_reads_complete: bool,
    read_only_tool_set: bool,
    has_bounded_projection: bool,
    round_made_progress: bool,
    finalization_requests: int,
    round_index: int,
    max_rounds: int,
) -> ProviderReadFinalizationTransition:
    """Return a finalization request only when all read evidence gates pass."""

    _literal_bool(page_cap_ready, "page_cap_ready")
    _literal_bool(all_scoped_reads_complete, "all_scoped_reads_complete")
    _literal_bool(read_only_tool_set, "read_only_tool_set")
    _literal_bool(has_bounded_projection, "has_bounded_projection")
    _literal_bool(round_made_progress, "round_made_progress")
    if type(max_rounds) is not int or max_rounds < 1:
        raise ProviderReadFinalizationError(
            "max_rounds must be a positive integer"
        )
    if type(round_index) is not int or round_index < 1:
        raise ProviderReadFinalizationError(
            "round_index must be a positive integer"
        )
    if round_index > max_rounds:
        raise ProviderReadFinalizationError(
            "round_index cannot exceed max_rounds"
        )
    if (
        type(finalization_requests) is not int
        or finalization_requests < 0
        or finalization_requests > max_rounds
    ):
        raise ProviderReadFinalizationError(
            "finalization_requests must be within round bounds"
        )

    common_ready = (
        finalization_requests == 0
        and all_scoped_reads_complete
        and read_only_tool_set
    )
    should_finalize = common_ready and (
        page_cap_ready
        or (not round_made_progress and has_bounded_projection)
    )
    if not should_finalize:
        return ProviderReadFinalizationTransition(
            action=ProviderReadFinalizationAction.NONE,
            error_code=None,
            finalization_pending=False,
            finalization_requests=finalization_requests,
        )
    if round_index >= max_rounds:
        return ProviderReadFinalizationTransition(
            action=ProviderReadFinalizationAction.FAIL,
            error_code=(
                ProviderReadFinalizationErrorCode.FINALIZATION_BUDGET_UNAVAILABLE
            ),
            finalization_pending=False,
            finalization_requests=finalization_requests,
        )
    return ProviderReadFinalizationTransition(
        action=ProviderReadFinalizationAction.REQUEST_FINALIZATION,
        error_code=None,
        finalization_pending=True,
        finalization_requests=finalization_requests + 1,
    )


def _literal_bool(value: object, label: str) -> None:
    if type(value) is not bool:
        raise ProviderReadFinalizationError(
            f"{label} must be a literal boolean"
        )


__all__ = [
    "ProviderReadFinalizationAction",
    "ProviderReadFinalizationError",
    "ProviderReadFinalizationErrorCode",
    "ProviderReadFinalizationTransition",
    "provider_read_finalization_transition",
]
