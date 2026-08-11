"""Derive one mutually exclusive transition after provider mutation evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.provider_validation_observation import ProviderValidationObservation


class ProviderMutationTransitionAction(str, Enum):
    """Next mutation-related state-machine action for one completed round."""

    NONE = "none"
    ENTER_VALIDATION = "enter_validation"
    CONTINUE_VALIDATION = "continue_validation"
    REQUEST_FINALIZATION = "request_finalization"
    FAIL = "fail"


class ProviderMutationTransitionErrorCode(str, Enum):
    """Stable terminal codes emitted by mutation transition policy."""

    VALIDATION_FAILED = "ProviderToolValidationFailed"
    VALIDATION_WITHOUT_MUTATION = "ProviderToolValidationWithoutMutation"
    FINALIZATION_BUDGET_UNAVAILABLE = (
        "ProviderToolFinalizationBudgetUnavailable"
    )


class ProviderMutationTransitionError(ValueError):
    """Raised when supplied mutation state facts are contradictory."""


@dataclass(frozen=True)
class ProviderMutationTransition:
    """Derived mutation state with no side effects."""

    action: ProviderMutationTransitionAction
    error_code: ProviderMutationTransitionErrorCode | None
    post_mutation_active: bool
    finalization_pending: bool


def provider_mutation_transition(
    *,
    receipt_available: bool,
    receipt_observed_this_round: bool,
    validation_observation: ProviderValidationObservation,
    round_index: int,
    max_rounds: int,
) -> ProviderMutationTransition:
    """Return one legal mutation/validation/finalization transition."""

    if type(receipt_available) is not bool:
        raise ProviderMutationTransitionError(
            "receipt_available must be a literal boolean"
        )
    if type(receipt_observed_this_round) is not bool:
        raise ProviderMutationTransitionError(
            "receipt_observed_this_round must be a literal boolean"
        )
    if receipt_observed_this_round and not receipt_available:
        raise ProviderMutationTransitionError(
            "a newly observed receipt must also be available"
        )
    if not isinstance(validation_observation, ProviderValidationObservation):
        raise ProviderMutationTransitionError(
            "validation_observation must be ProviderValidationObservation"
        )
    if type(round_index) is not int or round_index < 1:
        raise ProviderMutationTransitionError(
            "round_index must be a positive integer"
        )
    if type(max_rounds) is not int or max_rounds < 1:
        raise ProviderMutationTransitionError(
            "max_rounds must be a positive integer"
        )
    if round_index > max_rounds:
        raise ProviderMutationTransitionError(
            "round_index cannot exceed max_rounds"
        )

    if (
        validation_observation is not ProviderValidationObservation.NOT_OBSERVED
        and not receipt_available
    ):
        return _failure(
            ProviderMutationTransitionErrorCode.VALIDATION_WITHOUT_MUTATION
        )
    if validation_observation is ProviderValidationObservation.FAILED:
        return _failure(ProviderMutationTransitionErrorCode.VALIDATION_FAILED)
    if validation_observation is ProviderValidationObservation.SUCCEEDED:
        if round_index >= max_rounds:
            return _failure(
                ProviderMutationTransitionErrorCode.FINALIZATION_BUDGET_UNAVAILABLE
            )
        return ProviderMutationTransition(
            action=ProviderMutationTransitionAction.REQUEST_FINALIZATION,
            error_code=None,
            post_mutation_active=False,
            finalization_pending=True,
        )
    if receipt_observed_this_round:
        return ProviderMutationTransition(
            action=ProviderMutationTransitionAction.ENTER_VALIDATION,
            error_code=None,
            post_mutation_active=True,
            finalization_pending=False,
        )
    if receipt_available:
        return ProviderMutationTransition(
            action=ProviderMutationTransitionAction.CONTINUE_VALIDATION,
            error_code=None,
            post_mutation_active=True,
            finalization_pending=False,
        )
    return ProviderMutationTransition(
        action=ProviderMutationTransitionAction.NONE,
        error_code=None,
        post_mutation_active=False,
        finalization_pending=False,
    )


def _failure(
    error_code: ProviderMutationTransitionErrorCode,
) -> ProviderMutationTransition:
    return ProviderMutationTransition(
        action=ProviderMutationTransitionAction.FAIL,
        error_code=error_code,
        post_mutation_active=False,
        finalization_pending=False,
    )


__all__ = [
    "ProviderMutationTransition",
    "ProviderMutationTransitionAction",
    "ProviderMutationTransitionError",
    "ProviderMutationTransitionErrorCode",
    "provider_mutation_transition",
]
