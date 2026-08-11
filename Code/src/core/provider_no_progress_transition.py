"""Derive duplicate-only guidance, finalization, and no-progress outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.provider_tool_roundtrip_contracts import MAX_PROVIDER_ROUND_TRIP_ROUNDS


class ProviderNoProgressAction(str, Enum):
    """Next controller action after duplicate/progress evaluation."""

    NONE = "none"
    CONTINUE = "continue"
    REQUEST_MUTATION_GUIDANCE = "request_mutation_guidance"
    REQUEST_FINALIZATION = "request_finalization"
    FAIL = "fail"


class ProviderNoProgressErrorCode(str, Enum):
    """Stable terminal codes for duplicate/no-progress policy."""

    NO_PROGRESS = "ProviderToolNoProgress"
    FINALIZATION_BUDGET_UNAVAILABLE = (
        "ProviderToolFinalizationBudgetUnavailable"
    )


class ProviderNoProgressError(ValueError):
    """Raised when duplicate/progress state facts are invalid."""


@dataclass(frozen=True)
class ProviderNoProgressTransition:
    """Derived counters and flags with no side effects."""

    action: ProviderNoProgressAction
    error_code: ProviderNoProgressErrorCode | None
    mutation_duplicate_guidance_sent: bool
    finalization_pending: bool
    finalization_requests: int
    no_progress_rounds: int
    duplicate_only_rounds: int


def provider_no_progress_transition(
    *,
    duplicate_only_covered: bool,
    mutation_tools_exposed: bool,
    mutation_duplicate_guidance_sent: bool,
    read_only_tool_set: bool,
    finalization_requests: int,
    round_made_progress: bool,
    no_progress_rounds: int,
    max_no_progress_rounds: int,
    duplicate_only_rounds: int,
    round_index: int,
    max_rounds: int,
) -> ProviderNoProgressTransition:
    """Return one legal duplicate/no-progress transition and next counters."""

    _literal_bool(duplicate_only_covered, "duplicate_only_covered")
    _literal_bool(mutation_tools_exposed, "mutation_tools_exposed")
    _literal_bool(
        mutation_duplicate_guidance_sent,
        "mutation_duplicate_guidance_sent",
    )
    _literal_bool(read_only_tool_set, "read_only_tool_set")
    _literal_bool(round_made_progress, "round_made_progress")
    if type(max_rounds) is not int or max_rounds < 1:
        raise ProviderNoProgressError("max_rounds must be a positive integer")
    if type(round_index) is not int or not 1 <= round_index <= max_rounds:
        raise ProviderNoProgressError("round_index must be within round bounds")
    if (
        type(max_no_progress_rounds) is not int
        or max_no_progress_rounds < 1
        or max_no_progress_rounds > MAX_PROVIDER_ROUND_TRIP_ROUNDS
    ):
        raise ProviderNoProgressError(
            "max_no_progress_rounds must be within provider round bounds"
        )
    if (
        type(no_progress_rounds) is not int
        or no_progress_rounds < 0
        or no_progress_rounds >= max_no_progress_rounds
    ):
        raise ProviderNoProgressError(
            "no_progress_rounds must be below the failure threshold"
        )
    if (
        type(finalization_requests) is not int
        or not 0 <= finalization_requests <= max_rounds
    ):
        raise ProviderNoProgressError(
            "finalization_requests must be within round bounds"
        )
    if (
        type(duplicate_only_rounds) is not int
        or not 0 <= duplicate_only_rounds <= max_rounds
    ):
        raise ProviderNoProgressError(
            "duplicate_only_rounds must be within round bounds"
        )

    if (
        duplicate_only_covered
        and mutation_tools_exposed
        and not mutation_duplicate_guidance_sent
    ):
        return _decision(
            ProviderNoProgressAction.REQUEST_MUTATION_GUIDANCE,
            mutation_duplicate_guidance_sent=True,
            finalization_requests=finalization_requests,
            no_progress_rounds=0,
            duplicate_only_rounds=duplicate_only_rounds,
        )
    if duplicate_only_covered and finalization_requests == 0:
        if not read_only_tool_set:
            return _count_no_progress(
                no_progress_rounds=no_progress_rounds,
                max_no_progress_rounds=max_no_progress_rounds,
                mutation_duplicate_guidance_sent=(
                    mutation_duplicate_guidance_sent
                ),
                finalization_requests=finalization_requests,
                duplicate_only_rounds=duplicate_only_rounds,
            )
        next_duplicate_rounds = duplicate_only_rounds + 1
        if round_index >= max_rounds:
            return _decision(
                ProviderNoProgressAction.FAIL,
                error_code=(
                    ProviderNoProgressErrorCode.FINALIZATION_BUDGET_UNAVAILABLE
                ),
                mutation_duplicate_guidance_sent=(
                    mutation_duplicate_guidance_sent
                ),
                finalization_requests=finalization_requests,
                no_progress_rounds=no_progress_rounds,
                duplicate_only_rounds=next_duplicate_rounds,
            )
        return _decision(
            ProviderNoProgressAction.REQUEST_FINALIZATION,
            mutation_duplicate_guidance_sent=mutation_duplicate_guidance_sent,
            finalization_pending=True,
            finalization_requests=1,
            no_progress_rounds=0,
            duplicate_only_rounds=next_duplicate_rounds,
        )
    if round_made_progress:
        return _decision(
            ProviderNoProgressAction.NONE,
            mutation_duplicate_guidance_sent=mutation_duplicate_guidance_sent,
            finalization_requests=finalization_requests,
            no_progress_rounds=0,
            duplicate_only_rounds=duplicate_only_rounds,
        )
    return _count_no_progress(
        no_progress_rounds=no_progress_rounds,
        max_no_progress_rounds=max_no_progress_rounds,
        mutation_duplicate_guidance_sent=mutation_duplicate_guidance_sent,
        finalization_requests=finalization_requests,
        duplicate_only_rounds=duplicate_only_rounds,
    )


def _count_no_progress(
    *,
    no_progress_rounds: int,
    max_no_progress_rounds: int,
    mutation_duplicate_guidance_sent: bool,
    finalization_requests: int,
    duplicate_only_rounds: int,
) -> ProviderNoProgressTransition:
    next_count = no_progress_rounds + 1
    return _decision(
        (
            ProviderNoProgressAction.FAIL
            if next_count >= max_no_progress_rounds
            else ProviderNoProgressAction.CONTINUE
        ),
        error_code=(
            ProviderNoProgressErrorCode.NO_PROGRESS
            if next_count >= max_no_progress_rounds
            else None
        ),
        mutation_duplicate_guidance_sent=mutation_duplicate_guidance_sent,
        finalization_requests=finalization_requests,
        no_progress_rounds=next_count,
        duplicate_only_rounds=duplicate_only_rounds,
    )


def _decision(
    action: ProviderNoProgressAction,
    *,
    mutation_duplicate_guidance_sent: bool,
    finalization_requests: int,
    no_progress_rounds: int,
    duplicate_only_rounds: int,
    finalization_pending: bool = False,
    error_code: ProviderNoProgressErrorCode | None = None,
) -> ProviderNoProgressTransition:
    return ProviderNoProgressTransition(
        action=action,
        error_code=error_code,
        mutation_duplicate_guidance_sent=mutation_duplicate_guidance_sent,
        finalization_pending=finalization_pending,
        finalization_requests=finalization_requests,
        no_progress_rounds=no_progress_rounds,
        duplicate_only_rounds=duplicate_only_rounds,
    )


def _literal_bool(value: object, label: str) -> None:
    if type(value) is not bool:
        raise ProviderNoProgressError(f"{label} must be a literal boolean")


__all__ = [
    "ProviderNoProgressAction",
    "ProviderNoProgressError",
    "ProviderNoProgressErrorCode",
    "ProviderNoProgressTransition",
    "provider_no_progress_transition",
]
