from __future__ import annotations

from itertools import count

import pytest

from autonomous_iteration.enhancement_completion_budget import (
    EnhancementCompletionBudgetCoordinator,
)
from metadata import (
    ContextRequestPurpose,
    EnhancementCompletionBudgetPolicy,
    EnhancementCompletionComplexity,
    EnhancementCompletionDecisionValue,
    EnhancementCompletionPurposeLimit,
    EnhancementCompletionRequest,
    EnhancementCompletionRequirement,
    RuntimeBudgetMetadata,
)


ENHANCEMENT_PURPOSES = {
    ContextRequestPurpose.PROJECT_IMPROVEMENT,
    ContextRequestPurpose.ITERATION_GOAL,
    ContextRequestPurpose.ITERATION_TASK_DESIGN,
    ContextRequestPurpose.CODE_GENERATION,
    ContextRequestPurpose.CODE_EDIT,
}
_LOGICAL_KEY_COUNTER = count(1)


def _policy(*, total_tokens: int = 10_000) -> EnhancementCompletionBudgetPolicy:
    return EnhancementCompletionBudgetPolicy(
        total_tokens=total_tokens,
        recovery_step=300,
        purpose_limits={
            ContextRequestPurpose.PROJECT_IMPROVEMENT: EnhancementCompletionPurposeLimit(
                floor=500,
                ceiling=min(1_500, total_tokens),
            ),
            ContextRequestPurpose.ITERATION_GOAL: EnhancementCompletionPurposeLimit(
                floor=400,
                ceiling=min(1_200, total_tokens),
            ),
            ContextRequestPurpose.ITERATION_TASK_DESIGN: EnhancementCompletionPurposeLimit(
                floor=700,
                ceiling=min(2_200, total_tokens),
            ),
            ContextRequestPurpose.CODE_GENERATION: EnhancementCompletionPurposeLimit(
                floor=1_000,
                ceiling=min(3_500, total_tokens),
            ),
            ContextRequestPurpose.CODE_EDIT: EnhancementCompletionPurposeLimit(
                floor=400,
                ceiling=min(1_600, total_tokens),
            ),
        },
    )


def _request(
    purpose: ContextRequestPurpose,
    *,
    complexity: EnhancementCompletionComplexity = EnhancementCompletionComplexity.STANDARD,
    prompt_tokens: int = 1_000,
    remaining_calls: int = 4,
    remaining_value: EnhancementCompletionDecisionValue = EnhancementCompletionDecisionValue.NORMAL,
    requirement: EnhancementCompletionRequirement = EnhancementCompletionRequirement.OPTIONAL,
    recovery_of: str | None = None,
    logical_key: str | None = None,
) -> EnhancementCompletionRequest:
    return EnhancementCompletionRequest(
        purpose=purpose,
        complexity=complexity,
        prompt_tokens=prompt_tokens,
        remaining_calls=remaining_calls,
        remaining_value=remaining_value,
        requirement=requirement,
        recovery_of=recovery_of,
        logical_key=logical_key or f"test:{purpose.value}:{next(_LOGICAL_KEY_COUNTER)}",
    )


def _budget(*, total_tokens: int = 10_000) -> RuntimeBudgetMetadata:
    return RuntimeBudgetMetadata(enhancement_completion_policy=_policy(total_tokens=total_tokens))


def test_enhancement_completion_contract_is_strict_typed_and_round_trips() -> None:
    policy = _policy()
    request = _request(
        ContextRequestPurpose.ITERATION_TASK_DESIGN,
        complexity=EnhancementCompletionComplexity.COMPLEX,
        prompt_tokens=3_200,
        remaining_calls=3,
        remaining_value=EnhancementCompletionDecisionValue.HIGH,
        requirement=EnhancementCompletionRequirement.REQUIRED,
    )

    assert set(policy.purpose_limits) == ENHANCEMENT_PURPOSES
    assert EnhancementCompletionBudgetPolicy.model_validate_json(policy.model_dump_json()) == policy
    assert EnhancementCompletionRequest.model_validate_json(request.model_dump_json()) == request

    with pytest.raises(ValueError):
        EnhancementCompletionRequest(
            purpose=ContextRequestPurpose.TEXT_SUMMARIZATION,
            complexity=EnhancementCompletionComplexity.STANDARD,
            prompt_tokens=1_000,
            remaining_calls=1,
            remaining_value=EnhancementCompletionDecisionValue.NORMAL,
            requirement=EnhancementCompletionRequirement.OPTIONAL,
        )
    with pytest.raises(ValueError):
        EnhancementCompletionRequest(
            purpose=ContextRequestPurpose.PROJECT_IMPROVEMENT,
            complexity="extreme",
            prompt_tokens=1_000,
            remaining_calls=1,
            remaining_value=EnhancementCompletionDecisionValue.NORMAL,
            requirement=EnhancementCompletionRequirement.OPTIONAL,
        )


def test_historical_runtime_budget_without_enhancement_fields_uses_safe_defaults() -> None:
    payload = RuntimeBudgetMetadata().model_dump(mode="python")
    for key in list(payload):
        if key.startswith("enhancement_completion_"):
            payload.pop(key)

    restored = RuntimeBudgetMetadata.model_validate(payload)

    assert restored.enhancement_completion_tokens_reserved == 0
    assert restored.enhancement_completion_tokens_used == 0
    assert restored.enhancement_completion_reservations == {}
    assert restored.enhancement_completion_reconciliations == {}


def test_historical_four_purpose_policy_migrates_code_edit_limit() -> None:
    payload = EnhancementCompletionBudgetPolicy().model_dump(mode="json")
    payload["purpose_limits"].pop(ContextRequestPurpose.CODE_EDIT.value, None)

    restored = EnhancementCompletionBudgetPolicy.model_validate(payload)

    assert restored.purpose_limits[ContextRequestPurpose.CODE_EDIT] == (
        EnhancementCompletionPurposeLimit(floor=400, ceiling=1_600)
    )


def test_runtime_budget_rejects_corrupt_enhancement_ledger_and_aggregates() -> None:
    budget = _budget()
    coordinator = EnhancementCompletionBudgetCoordinator(budget)
    reservation = coordinator.reserve(
        _request(ContextRequestPurpose.PROJECT_IMPROVEMENT, logical_key="stable")
    )
    assert reservation is not None
    payload = budget.model_dump(mode="python")

    mismatched_key = dict(payload)
    mismatched_key["enhancement_completion_reservations"] = {
        "wrong": reservation.model_dump(mode="python")
    }
    with pytest.raises(ValueError, match="logical_key"):
        RuntimeBudgetMetadata.model_validate(mismatched_key)

    mismatched_counter = dict(payload)
    mismatched_counter["enhancement_completion_tokens_reserved"] = 0
    with pytest.raises(ValueError, match="reserved counter"):
        RuntimeBudgetMetadata.model_validate(mismatched_counter)
def test_policy_requires_exact_enhancement_purposes_and_valid_floor_ceiling() -> None:
    limits = dict(_policy().purpose_limits)
    limits.pop(ContextRequestPurpose.CODE_GENERATION)
    with pytest.raises(ValueError, match="purpose"):
        EnhancementCompletionBudgetPolicy(total_tokens=10_000, purpose_limits=limits)

    limits = dict(_policy().purpose_limits)
    with pytest.raises(ValueError, match="floor"):
        limits[ContextRequestPurpose.PROJECT_IMPROVEMENT] = EnhancementCompletionPurposeLimit(
            floor=1_501,
            ceiling=1_500,
        )


def test_all_purposes_reserve_from_one_shared_total_and_honor_bounds() -> None:
    budget = _budget(total_tokens=10_000)
    coordinator = EnhancementCompletionBudgetCoordinator(budget)
    reservations = [
        coordinator.reserve(
            _request(
                purpose,
                complexity=EnhancementCompletionComplexity.STANDARD,
                remaining_calls=4 - index,
                requirement=EnhancementCompletionRequirement.REQUIRED,
            )
        )
        for index, purpose in enumerate(
            (
                ContextRequestPurpose.PROJECT_IMPROVEMENT,
                ContextRequestPurpose.ITERATION_GOAL,
                ContextRequestPurpose.ITERATION_TASK_DESIGN,
                ContextRequestPurpose.CODE_GENERATION,
            )
        )
    ]

    assert all(reservation is not None for reservation in reservations)
    for reservation in reservations:
        assert reservation is not None
        purpose_limit = budget.enhancement_completion_policy.purpose_limits[reservation.purpose]
        assert purpose_limit.floor <= reservation.max_tokens <= purpose_limit.ceiling
    assert budget.enhancement_completion_tokens_reserved == sum(
        reservation.max_tokens for reservation in reservations if reservation is not None
    )


def test_allocation_uses_decision_value_and_complexity_not_linear_round_decay() -> None:
    budget = _budget()
    coordinator = EnhancementCompletionBudgetCoordinator(budget)
    low_value_early = coordinator.reserve(
        _request(
            ContextRequestPurpose.ITERATION_TASK_DESIGN,
            complexity=EnhancementCompletionComplexity.ROUTINE,
            remaining_calls=6,
            remaining_value=EnhancementCompletionDecisionValue.LOW,
        )
    )
    assert low_value_early is not None
    coordinator.reconcile(low_value_early, actual_tokens=100, finish_reason="stop")
    high_value_late = coordinator.reserve(
        _request(
            ContextRequestPurpose.ITERATION_TASK_DESIGN,
            complexity=EnhancementCompletionComplexity.COMPLEX,
            prompt_tokens=4_000,
            remaining_calls=2,
            remaining_value=EnhancementCompletionDecisionValue.HIGH,
            requirement=EnhancementCompletionRequirement.REQUIRED,
        )
    )

    assert high_value_late is not None
    assert high_value_late.max_tokens > low_value_early.max_tokens


def test_optional_request_is_denied_when_shared_remaining_cannot_meet_its_floor() -> None:
    budget = _budget(total_tokens=1_100)
    coordinator = EnhancementCompletionBudgetCoordinator(budget)
    first = coordinator.reserve(
        _request(
            ContextRequestPurpose.CODE_GENERATION,
            requirement=EnhancementCompletionRequirement.REQUIRED,
            remaining_calls=1,
        )
    )

    assert first is not None
    assert 1_000 <= first.max_tokens <= 1_100
    assert coordinator.reserve(
        _request(ContextRequestPurpose.PROJECT_IMPROVEMENT)
    ) is None


def test_reconcile_replaces_reservation_with_actual_usage_and_refunds_unused_tokens() -> None:
    budget = _budget()
    coordinator = EnhancementCompletionBudgetCoordinator(budget)
    reservation = coordinator.reserve(
        _request(ContextRequestPurpose.PROJECT_IMPROVEMENT)
    )
    assert reservation is not None
    originally_reserved = reservation.max_tokens

    reconciliation = coordinator.reconcile(
        reservation,
        actual_tokens=321,
        finish_reason="stop",
    )

    assert reconciliation.reserved_tokens == originally_reserved
    assert reconciliation.actual_tokens == 321
    assert reconciliation.refunded_tokens == originally_reserved - 321
    assert budget.enhancement_completion_tokens_reserved == 0
    assert budget.enhancement_completion_tokens_used == 321
    assert budget.enhancement_completion_tokens_remaining == 10_000 - 321


def test_unknown_usage_preserves_reservation_even_when_response_is_empty() -> None:
    budget = _budget()
    coordinator = EnhancementCompletionBudgetCoordinator(budget)
    reservation = coordinator.reserve(
        _request(ContextRequestPurpose.PROJECT_IMPROVEMENT)
    )
    assert reservation is not None

    reconciliation = coordinator.reconcile(
        reservation,
        actual_tokens=None,
        finish_reason=None,
        response_empty=True,
    )

    assert reconciliation.usage_known is False
    assert reconciliation.refunded_tokens == 0
    assert budget.enhancement_completion_tokens_reserved == reservation.max_tokens
    assert budget.enhancement_completion_tokens_used == 0
    assert budget.enhancement_completion_tokens_remaining == 10_000 - reservation.max_tokens


def test_explicit_zero_usage_refunds_an_empty_response_reservation() -> None:
    budget = _budget()
    coordinator = EnhancementCompletionBudgetCoordinator(budget)
    reservation = coordinator.reserve(
        _request(ContextRequestPurpose.PROJECT_IMPROVEMENT)
    )
    assert reservation is not None

    reconciliation = coordinator.reconcile(
        reservation,
        actual_tokens=0,
        finish_reason=None,
        response_empty=True,
    )

    assert reconciliation.usage_known is True
    assert reconciliation.refunded_tokens == reservation.max_tokens
    assert budget.enhancement_completion_tokens_reserved == 0
    assert budget.enhancement_completion_tokens_used == 0
    assert budget.enhancement_completion_tokens_remaining == 10_000


def test_length_finish_allows_exactly_one_bounded_recovery_for_that_reservation() -> None:
    budget = _budget()
    coordinator = EnhancementCompletionBudgetCoordinator(budget)
    initial = coordinator.reserve(
        _request(
            ContextRequestPurpose.ITERATION_TASK_DESIGN,
            complexity=EnhancementCompletionComplexity.ROUTINE,
            remaining_value=EnhancementCompletionDecisionValue.LOW,
        )
    )
    assert initial is not None
    coordinator.reconcile(
        initial,
        actual_tokens=initial.max_tokens,
        finish_reason="length",
    )

    recovery = coordinator.reserve(
        _request(
            ContextRequestPurpose.ITERATION_TASK_DESIGN,
            complexity=EnhancementCompletionComplexity.ROUTINE,
            remaining_value=EnhancementCompletionDecisionValue.LOW,
            recovery_of=initial.reservation_id,
        )
    )
    assert recovery is not None
    assert recovery.max_tokens > initial.max_tokens
    assert recovery.max_tokens <= min(
        budget.enhancement_completion_policy.purpose_limits[
            ContextRequestPurpose.ITERATION_TASK_DESIGN
        ].ceiling,
        initial.max_tokens + budget.enhancement_completion_policy.recovery_step,
    )
    coordinator.reconcile(
        recovery,
        actual_tokens=recovery.max_tokens,
        finish_reason="length",
    )

    assert coordinator.reserve(
        _request(
            ContextRequestPurpose.ITERATION_TASK_DESIGN,
            recovery_of=initial.reservation_id,
        )
    ) is None


def test_non_length_failure_does_not_authorize_recovery_expansion() -> None:
    budget = _budget()
    coordinator = EnhancementCompletionBudgetCoordinator(budget)
    initial = coordinator.reserve(
        _request(ContextRequestPurpose.PROJECT_IMPROVEMENT)
    )
    assert initial is not None
    coordinator.reconcile(initial, actual_tokens=200, finish_reason="error")

    assert coordinator.reserve(
        _request(
            ContextRequestPurpose.PROJECT_IMPROVEMENT,
            recovery_of=initial.reservation_id,
        )
    ) is None


def test_same_logical_call_reservation_is_idempotent_across_checkpoint_roundtrip() -> None:
    budget = _budget()
    coordinator = EnhancementCompletionBudgetCoordinator(budget)
    request = _request(
        ContextRequestPurpose.ITERATION_TASK_DESIGN,
        logical_key="iteration:2:task_design",
    )

    first = coordinator.reserve(request)
    second = coordinator.reserve(request)
    reserved_after_first_process = budget.enhancement_completion_tokens_reserved
    restored_budget = RuntimeBudgetMetadata.model_validate_json(budget.model_dump_json())
    restored = EnhancementCompletionBudgetCoordinator(restored_budget).reserve(request)

    assert first is not None
    assert second == first
    assert restored == first
    assert budget.enhancement_completion_tokens_reserved == first.max_tokens
    assert reserved_after_first_process == first.max_tokens
    assert restored_budget.enhancement_completion_tokens_reserved == first.max_tokens


def test_reconcile_is_idempotent_for_the_same_reservation_id() -> None:
    budget = _budget()
    coordinator = EnhancementCompletionBudgetCoordinator(budget)
    reservation = coordinator.reserve(
        _request(
            ContextRequestPurpose.PROJECT_IMPROVEMENT,
            logical_key="iteration:0:analysis",
        )
    )
    assert reservation is not None

    first = coordinator.reconcile(reservation, actual_tokens=321, finish_reason="stop")
    used_after_first = budget.enhancement_completion_tokens_used
    second = coordinator.reconcile(reservation, actual_tokens=321, finish_reason="stop")

    assert second == first
    assert used_after_first == 321
    assert budget.enhancement_completion_tokens_used == 321
    assert budget.enhancement_completion_tokens_reserved == 0
