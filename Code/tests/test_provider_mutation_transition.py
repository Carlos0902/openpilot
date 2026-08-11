from __future__ import annotations

import pytest

from core.provider_mutation_transition import (
    ProviderMutationTransitionAction,
    ProviderMutationTransitionError,
    provider_mutation_transition,
)
from core.provider_validation_observation import ProviderValidationObservation


def _transition(**overrides):
    values = {
        "receipt_available": False,
        "receipt_observed_this_round": False,
        "validation_observation": ProviderValidationObservation.NOT_OBSERVED,
        "round_index": 1,
        "max_rounds": 3,
    }
    values.update(overrides)
    return provider_mutation_transition(**values)


def test_transition_without_mutation_or_validation_continues_normally() -> None:
    decision = _transition()

    assert decision.action is ProviderMutationTransitionAction.NONE
    assert decision.error_code is None
    assert decision.post_mutation_active is False
    assert decision.finalization_pending is False


def test_new_mutation_receipt_enters_exact_validation_context() -> None:
    decision = _transition(
        receipt_available=True,
        receipt_observed_this_round=True,
    )

    assert decision.action is ProviderMutationTransitionAction.ENTER_VALIDATION
    assert decision.post_mutation_active is True
    assert decision.finalization_pending is False


def test_existing_receipt_keeps_waiting_for_validation() -> None:
    decision = _transition(receipt_available=True)

    assert decision.action is ProviderMutationTransitionAction.CONTINUE_VALIDATION
    assert decision.post_mutation_active is True


def test_failed_exact_validation_is_terminal() -> None:
    decision = _transition(
        receipt_available=True,
        validation_observation=ProviderValidationObservation.FAILED,
    )

    assert decision.action is ProviderMutationTransitionAction.FAIL
    assert decision.error_code == "ProviderToolValidationFailed"


def test_successful_validation_requests_finalization_when_round_remains() -> None:
    decision = _transition(
        receipt_available=True,
        validation_observation=ProviderValidationObservation.SUCCEEDED,
    )

    assert decision.action is ProviderMutationTransitionAction.REQUEST_FINALIZATION
    assert decision.post_mutation_active is False
    assert decision.finalization_pending is True


def test_successful_validation_fails_when_finalization_round_is_unavailable() -> None:
    decision = _transition(
        receipt_available=True,
        validation_observation=ProviderValidationObservation.SUCCEEDED,
        round_index=3,
        max_rounds=3,
    )

    assert decision.action is ProviderMutationTransitionAction.FAIL
    assert decision.error_code == "ProviderToolFinalizationBudgetUnavailable"


def test_validation_without_mutation_receipt_fails_closed() -> None:
    decision = _transition(
        validation_observation=ProviderValidationObservation.SUCCEEDED,
    )

    assert decision.action is ProviderMutationTransitionAction.FAIL
    assert decision.error_code == "ProviderToolValidationWithoutMutation"


@pytest.mark.parametrize(
    "overrides",
    [
        {"receipt_available": "yes"},
        {"receipt_observed_this_round": 1},
        {"receipt_available": False, "receipt_observed_this_round": True},
        {"validation_observation": "succeeded"},
        {"round_index": 0},
        {"max_rounds": 0},
        {"round_index": 4, "max_rounds": 3},
    ],
)
def test_transition_rejects_invalid_state_facts(overrides) -> None:
    with pytest.raises(ProviderMutationTransitionError):
        _transition(**overrides)
