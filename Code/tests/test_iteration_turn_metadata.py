from __future__ import annotations

import pytest
from pydantic import ValidationError

from metadata import (
    ActiveTaskBinding,
    AssistantLedgerCommitState,
    AssistantTurnCommit,
    CompletedProjectTaskOutcome,
    CompletedResponseOutcome,
    CompletionObligation,
    CompletionObligationKind,
    CompletionObligationStatus,
    ConversationIdentity,
    DurableArtifactReference,
    GroundingDecision,
    GroundingStatus,
    IterationAuthorityCeiling,
    IterationAuthoritySource,
    IterationAuthorityState,
    IterationBoundary,
    IterationCompletionScope,
    IterationControlCursor,
    IterationDisposition,
    IterationOutcome,
    IterationPhase,
    IterationStopReason,
    IterationTurnRecordMetadata,
    NoTaskBinding,
    PreparedTaskBinding,
    PreTaskState,
    ResponseCandidate,
    RootDecisionBudget,
    WaiverAuthority,
)


def _artifact(kind: str = "response_payload") -> DurableArtifactReference:
    return DurableArtifactReference(
        artifact_id=f"artifact-{kind}",
        kind=kind,
        integrity_checksum="sha256:" + "a" * 64,
        bytes=42,
    )


def _identity() -> ConversationIdentity:
    return ConversationIdentity(
        conversation_id="conversation-1",
        run_id="run-1",
        turn_index=1,
        project_root="/tmp/project",
    )


def _authority() -> IterationAuthorityState:
    return IterationAuthorityState(
        ceiling=IterationAuthorityCeiling.RESPONSE_ONLY,
        source=IterationAuthoritySource.RUNTIME_DEFAULT,
        reason="No side effect was requested.",
        revision=0,
        authority_hash="sha256:" + "b" * 64,
    )


def _cursor(
    *,
    open_obligation_ids: tuple[str, ...] = (),
    phase: IterationPhase = IterationPhase.UNDERSTAND_TASK,
    disposition: IterationDisposition = IterationDisposition.COMPLETE_RESPONSE,
) -> IterationControlCursor:
    return IterationControlCursor(
        phase=phase,
        current_disposition=disposition,
        authority_state=_authority(),
        open_obligation_ids=open_obligation_ids,
    )


def _record(**updates) -> IterationTurnRecordMetadata:
    values = {
        "record_id": "record-1",
        "identity": _identity(),
        "pre_task_state": PreTaskState(
            user_message_id="message-user-1",
            user_input_ref=_artifact("user_input"),
            session_authority_hash="sha256:" + "9" * 64,
        ),
        "cursor": _cursor(),
        "root_budget": RootDecisionBudget(),
        "task_binding": NoTaskBinding(),
    }
    values.update(updates)
    return IterationTurnRecordMetadata(**values)


def test_iteration_turn_record_defaults_to_pretask_response_only_authority() -> None:
    record = _record()

    assert record.boundary == IterationBoundary.ITERATION_INITIALIZED
    assert record.cursor.authority_state.ceiling == IterationAuthorityCeiling.RESPONSE_ONLY
    assert record.task_binding.state == "none"
    assert record.outcome is None
    assert record.core_success is None


def test_mutation_eligibility_requires_current_confirmation_lineage() -> None:
    with pytest.raises(ValidationError, match="confirmation"):
        IterationAuthorityState(
            ceiling=IterationAuthorityCeiling.MUTATION_ELIGIBLE,
            source=IterationAuthoritySource.USER_CONFIRMATION,
            reason="User requested a write.",
            revision=2,
            authority_hash="sha256:" + "c" * 64,
        )

    authority = IterationAuthorityState(
        ceiling=IterationAuthorityCeiling.MUTATION_ELIGIBLE,
        source=IterationAuthoritySource.USER_CONFIRMATION,
        reason="User confirmed the scoped write.",
        revision=2,
        authority_hash="sha256:" + "c" * 64,
        confirmation_message_id="message-user-1",
        confirmation_turn_index=1,
    )

    assert authority.ceiling == IterationAuthorityCeiling.MUTATION_ELIGIBLE


def test_root_decision_budget_rejects_over_consumption() -> None:
    with pytest.raises(ValidationError, match="provider calls"):
        RootDecisionBudget(max_root_provider_calls=1, root_provider_calls_used=2)


def test_completion_obligation_closure_and_waiver_matrix() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        CompletionObligation(
            obligation_id="runtime-model",
            kind=CompletionObligationKind.RUNTIME_FACT,
            status=CompletionObligationStatus.SATISFIED,
        )

    with pytest.raises(ValidationError, match="cannot be waived"):
        CompletionObligation(
            obligation_id="permission",
            kind=CompletionObligationKind.PERMISSION,
            status=CompletionObligationStatus.WAIVED,
            waiver_authority=WaiverAuthority.USER,
            waiver_reason="skip it",
        )

    waived = CompletionObligation(
        obligation_id="acceptance",
        kind=CompletionObligationKind.ACCEPTANCE,
        status=CompletionObligationStatus.WAIVED,
        waiver_authority=WaiverAuthority.USER,
        waiver_reason="User explicitly waived this optional criterion.",
    )

    assert waived.is_closed is True


def test_grounding_approval_requires_complete_claim_coverage_and_no_open_obligation() -> None:
    with pytest.raises(ValidationError, match="approved grounding"):
        GroundingDecision(
            response_hash="sha256:" + "d" * 64,
            status=GroundingStatus.APPROVED,
            obligation_ids=("runtime-model",),
            open_obligation_ids=("runtime-model",),
            candidate_claim_coverage="incomplete",
            unbound_claim_ids=("claim-1",),
        )

    decision = GroundingDecision(
        response_hash="sha256:" + "d" * 64,
        status=GroundingStatus.APPROVED,
        obligation_ids=("runtime-model",),
        satisfied_obligation_ids=("runtime-model",),
        candidate_claim_coverage="complete",
    )

    assert decision.status == GroundingStatus.APPROVED

    waived_decision = GroundingDecision(
        response_hash="sha256:" + "d" * 64,
        status=GroundingStatus.APPROVED,
        obligation_ids=("acceptance",),
        waived_obligation_ids=("acceptance",),
        candidate_claim_coverage="complete",
    )
    assert waived_decision.status == GroundingStatus.APPROVED


def test_assistant_commit_requires_stable_payload_identity() -> None:
    with pytest.raises(ValidationError, match="payload"):
        AssistantTurnCommit(state=AssistantLedgerCommitState.PENDING)

    commit = AssistantTurnCommit(
        state=AssistantLedgerCommitState.COMMITTED,
        message_id="message-assistant-1",
        turn_index=2,
        payload_ref=_artifact(),
        payload_hash="sha256:" + "e" * 64,
    )

    assert commit.state == AssistantLedgerCommitState.COMMITTED


def test_task_bindings_require_content_addressed_snapshot_and_active_checkpoint() -> None:
    with pytest.raises(ValidationError):
        PreparedTaskBinding(
            task_id="task-1",
            state_digest="sha256:" + "f" * 64,
            authority_revision=1,
            authority_hash="sha256:" + "1" * 64,
        )

    prepared = PreparedTaskBinding(
        task_id="task-1",
        state_digest="sha256:" + "f" * 64,
        snapshot_ref=_artifact("canonical_initial_task"),
        snapshot_hash="sha256:" + "2" * 64,
        authority_revision=1,
        authority_hash="sha256:" + "1" * 64,
    )
    active = ActiveTaskBinding(
        **prepared.model_dump(exclude={"state"}),
        checkpoint_id="checkpoint-1",
        checkpoint_digest="sha256:" + "3" * 64,
    )

    assert prepared.state == "prepared"
    assert active.state == "active"


def test_record_rejects_cursor_obligation_drift() -> None:
    obligation = CompletionObligation(
        obligation_id="runtime-model",
        kind=CompletionObligationKind.RUNTIME_FACT,
    )

    with pytest.raises(ValidationError, match="open obligation"):
        _record(obligations=(obligation,), cursor=_cursor())


def test_optional_open_obligation_does_not_block_the_cursor() -> None:
    optional = CompletionObligation(
        obligation_id="optional-context",
        kind=CompletionObligationKind.ACCEPTANCE,
        required=False,
    )

    record = _record(obligations=(optional,), cursor=_cursor())

    assert record.obligations[0].is_closed is False
    assert record.obligations[0].is_blocking is False


def test_response_only_outcome_cannot_bind_an_active_task() -> None:
    outcome = CompletedResponseOutcome(
        completion_scope=IterationCompletionScope.RESPONSE_ONLY,
        response_ref=_artifact(),
        response_hash="sha256:" + "4" * 64,
        grounding_decision_hash="sha256:" + "5" * 64,
    )
    active = ActiveTaskBinding(
        task_id="task-1",
        state_digest="sha256:" + "f" * 64,
        snapshot_ref=_artifact("canonical_initial_task"),
        snapshot_hash="sha256:" + "2" * 64,
        authority_revision=1,
        authority_hash="sha256:" + "1" * 64,
        checkpoint_id="checkpoint-1",
        checkpoint_digest="sha256:" + "3" * 64,
    )

    with pytest.raises(ValidationError, match="response-only"):
        _record(outcome=outcome, task_binding=active)


def test_completed_response_requires_approved_grounding() -> None:
    response_ref = _artifact()
    response_hash = "sha256:" + "4" * 64
    candidate = ResponseCandidate(
        candidate_id="candidate-1",
        response_ref=response_ref,
        response_hash=response_hash,
    )
    grounding = GroundingDecision(
        response_hash=response_hash,
        status=GroundingStatus.REPAIR_REQUIRED,
        candidate_claim_coverage="incomplete",
    )
    outcome = CompletedResponseOutcome(
        response_ref=response_ref,
        response_hash=response_hash,
        grounding_decision_hash=grounding.canonical_hash,
    )

    with pytest.raises(ValidationError, match="approved grounding"):
        _record(
            cursor=_cursor(phase=IterationPhase.COMPLETE),
            response_candidate=candidate,
            grounding_decision=grounding,
            outcome=outcome,
        )


def test_completed_response_round_trips_without_project_success() -> None:
    response_ref = _artifact()
    response_hash = "sha256:" + "4" * 64
    grounding = GroundingDecision(
        response_hash=response_hash,
        status=GroundingStatus.APPROVED,
        candidate_claim_coverage="complete",
    )
    outcome = CompletedResponseOutcome(
        completion_scope=IterationCompletionScope.RESPONSE_ONLY,
        response_ref=response_ref,
        response_hash=response_hash,
        grounding_decision_hash=grounding.canonical_hash,
    )
    commit = AssistantTurnCommit(
        state=AssistantLedgerCommitState.COMMITTED,
        message_id="message-assistant-1",
        turn_index=2,
        payload_ref=response_ref,
        payload_hash=response_hash,
    )
    record = _record(
        boundary=IterationBoundary.TURN_RESPONSE_DURABLE,
        cursor=_cursor(phase=IterationPhase.COMPLETE),
        response_candidate=ResponseCandidate(
            candidate_id="candidate-1",
            response_ref=response_ref,
            response_hash=response_hash,
        ),
        grounding_decision=grounding,
        outcome=outcome,
        assistant_commit=commit,
    )

    restored = IterationTurnRecordMetadata.model_validate(record.to_json_dict())

    assert restored == record
    assert restored.outcome.outcome == IterationOutcome.COMPLETED
    assert restored.core_success is None


def test_completed_project_outcome_round_trips_by_active_checkpoint_reference() -> None:
    active = ActiveTaskBinding(
        task_id="task-1",
        state_digest="sha256:" + "f" * 64,
        snapshot_ref=_artifact("canonical_initial_task"),
        snapshot_hash="sha256:" + "2" * 64,
        authority_revision=1,
        authority_hash="sha256:" + "1" * 64,
        checkpoint_id="checkpoint-1",
        checkpoint_digest="sha256:" + "3" * 64,
    )
    outcome = CompletedProjectTaskOutcome(
        runtime_report_ref=_artifact("runtime_report"),
        runtime_report_hash="sha256:" + "6" * 64,
    )
    record = _record(
        cursor=_cursor(
            phase=IterationPhase.COMPLETE,
            disposition=IterationDisposition.FORM_SINGLE_TASK,
        ),
        task_binding=active,
        outcome=outcome,
    )

    restored = IterationTurnRecordMetadata.model_validate(record.to_json_dict())

    assert isinstance(restored.task_binding, ActiveTaskBinding)
    assert isinstance(restored.outcome, CompletedProjectTaskOutcome)
    assert restored.core_success is None


def test_controlled_stop_reason_is_typed() -> None:
    assert IterationStopReason.NO_PROGRESS.value == "no_progress"
