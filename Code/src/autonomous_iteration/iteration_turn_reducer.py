"""Single state-transition owner for pre-task iteration turn records."""

from __future__ import annotations

from metadata import (
    ActiveTaskBinding,
    AssistantLedgerCommitState,
    AssistantTurnCommit,
    CompletedResponseOutcome,
    CompletionObligation,
    ControlledStopOutcome,
    GroundingDecision,
    IterationBoundary,
    IterationControlCursor,
    IterationPendingProviderRequest,
    IterationTurnRecordMetadata,
    NoTaskBinding,
    PreparedTaskBinding,
    ResponseCandidate,
    RootDecisionBudget,
)


class IterationTurnTransitionError(ValueError):
    """Raised when a proposed pre-task transition is not legal from its source."""


class IterationTurnReducer:
    """Apply validated, generation-advancing pre-task state transitions."""

    @classmethod
    def commit_assistant(cls, record: IterationTurnRecordMetadata) -> IterationTurnRecordMetadata:
        if record.assistant_commit.state != AssistantLedgerCommitState.PENDING:
            raise IterationTurnTransitionError("assistant commit transition requires pending state")
        return cls._validated_copy(
            record,
            record_id=f"{record.record_id}-assistant-committed",
            boundary=IterationBoundary.TURN_RESPONSE_DURABLE,
            assistant_commit=record.assistant_commit.model_copy(
                update={"state": AssistantLedgerCommitState.COMMITTED}
            ),
        )

    @classmethod
    def prepare_response(
        cls,
        record: IterationTurnRecordMetadata,
        *,
        cursor: IterationControlCursor,
        candidate: ResponseCandidate,
        grounding: GroundingDecision,
        outcome: CompletedResponseOutcome,
        assistant_commit: AssistantTurnCommit,
    ) -> IterationTurnRecordMetadata:
        if record.outcome is not None:
            raise IterationTurnTransitionError("response preparation requires an incomplete turn")
        if record.response_candidate is not None and (
            record.response_candidate != candidate or record.grounding_decision != grounding
        ):
            raise IterationTurnTransitionError("response preparation differs from durable candidate")
        if not isinstance(record.task_binding, NoTaskBinding):
            raise IterationTurnTransitionError("task-bound turn cannot prepare a response")
        if assistant_commit.state != AssistantLedgerCommitState.PENDING:
            raise IterationTurnTransitionError("response preparation requires pending assistant commit")
        return cls._validated_copy(
            record,
            record_id=f"{record.record_id}-response-pending",
            boundary=IterationBoundary.COMPLETION_APPROVED,
            cursor=cursor,
            response_candidate=candidate,
            grounding_decision=grounding,
            outcome=outcome,
            assistant_commit=assistant_commit,
        )

    @classmethod
    def record_response_candidate(
        cls,
        record: IterationTurnRecordMetadata,
        *,
        cursor: IterationControlCursor,
        candidate: ResponseCandidate,
        grounding: GroundingDecision,
        obligations: tuple[CompletionObligation, ...],
    ) -> IterationTurnRecordMetadata:
        if record.response_candidate is not None or record.outcome is not None:
            raise IterationTurnTransitionError("response candidate is already durable")
        return cls._validated_copy(
            record,
            record_id=f"{record.record_id}-candidate",
            boundary=IterationBoundary.COMPLETION_CANDIDATE_RECORDED,
            cursor=cursor,
            obligations=obligations,
            response_candidate=candidate,
            grounding_decision=grounding,
        )

    @classmethod
    def complete_evidence(
        cls,
        record: IterationTurnRecordMetadata,
        *,
        cursor: IterationControlCursor,
        obligations: tuple[CompletionObligation, ...],
        grounding: GroundingDecision,
    ) -> IterationTurnRecordMetadata:
        if not isinstance(record.task_binding, ActiveTaskBinding):
            raise IterationTurnTransitionError("evidence completion requires an active task binding")
        if record.response_candidate is None or record.outcome is not None:
            raise IterationTurnTransitionError("evidence completion requires an open response candidate")
        if grounding.response_hash != record.response_candidate.response_hash:
            raise IterationTurnTransitionError("evidence grounding differs from response candidate")
        if grounding.status != "approved" or any(item.is_blocking for item in obligations):
            raise IterationTurnTransitionError("evidence completion requires approved closed obligations")
        return cls._validated_copy(
            record,
            record_id=f"{record.record_id}-evidence-complete",
            boundary=IterationBoundary.COMPLETION_APPROVED,
            cursor=cursor,
            obligations=obligations,
            grounding_decision=grounding,
            task_binding=NoTaskBinding(),
        )

    @classmethod
    def request_provider(
        cls,
        record: IterationTurnRecordMetadata,
        *,
        request: IterationPendingProviderRequest,
        root_budget: RootDecisionBudget,
    ) -> IterationTurnRecordMetadata:
        if record.cursor.pending_provider_request is not None:
            raise IterationTurnTransitionError("provider request is already pending")
        cls._validate_budget_progress(record.root_budget, root_budget)
        if (
            root_budget.root_provider_calls_used
            != record.root_budget.root_provider_calls_used + 1
            or root_budget.decision_rounds_used != record.root_budget.decision_rounds_used + 1
        ):
            raise IterationTurnTransitionError(
                "provider request must consume one root call and decision round"
            )
        cursor = record.cursor.model_copy(
            update={
                "decision_ordinal": record.cursor.decision_ordinal + 1,
                "phase": "ground_response",
                "pending_provider_request": request,
            }
        )
        return cls._validated_copy(
            record,
            record_id=f"{record.record_id}-request-{request.request_ordinal}",
            boundary=IterationBoundary.DECISION_REQUESTED,
            cursor=cursor,
            root_budget=root_budget,
        )

    @classmethod
    def finish_provider_request(
        cls,
        record: IterationTurnRecordMetadata,
        *,
        root_budget: RootDecisionBudget,
        progress_signature: str,
    ) -> IterationTurnRecordMetadata:
        if record.cursor.pending_provider_request is None:
            raise IterationTurnTransitionError("provider observation requires a pending request")
        cls._validate_budget_progress(record.root_budget, root_budget)
        cursor = record.cursor.model_copy(
            update={
                "pending_provider_request": None,
                "decision_progress_signature": progress_signature,
            }
        )
        return cls._validated_copy(
            record,
            record_id=f"{record.record_id}-observed",
            boundary=IterationBoundary.DECISION_RECORDED,
            cursor=cursor,
            root_budget=root_budget,
        )

    @classmethod
    def stop(
        cls,
        record: IterationTurnRecordMetadata,
        *,
        outcome: ControlledStopOutcome,
    ) -> IterationTurnRecordMetadata:
        if record.outcome is not None:
            raise IterationTurnTransitionError("turn already has a terminal outcome")
        cursor = record.cursor.model_copy(
            update={
                "phase": "stopped",
                "current_disposition": "controlled_stop",
                "pending_provider_request": None,
            }
        )
        return cls._validated_copy(
            record,
            record_id=f"{record.record_id}-stopped",
            boundary=IterationBoundary.DECISION_RECORDED,
            cursor=cursor,
            outcome=outcome,
        )

    @classmethod
    def prepare_task(
        cls,
        record: IterationTurnRecordMetadata,
        binding: PreparedTaskBinding,
    ) -> IterationTurnRecordMetadata:
        if not isinstance(record.task_binding, NoTaskBinding):
            raise IterationTurnTransitionError("task preparation requires an unbound turn")
        if record.outcome is not None:
            raise IterationTurnTransitionError("completed turn cannot prepare a task")
        return cls._validated_copy(
            record,
            record_id=f"{record.record_id}-task-prepared",
            task_binding=binding,
        )

    @classmethod
    def activate_task(
        cls,
        record: IterationTurnRecordMetadata,
        binding: ActiveTaskBinding,
    ) -> IterationTurnRecordMetadata:
        source = record.task_binding
        if not isinstance(source, PreparedTaskBinding):
            raise IterationTurnTransitionError("task activation requires prepared state")
        expected = source.model_dump(exclude={"state"})
        actual = binding.model_dump(exclude={"state", "checkpoint_id", "checkpoint_digest"})
        if actual != expected:
            raise IterationTurnTransitionError("active task binding changed prepared task identity")
        return cls._validated_copy(
            record,
            record_id=f"{record.record_id}-active",
            task_binding=binding,
        )

    @staticmethod
    def _validated_copy(
        record: IterationTurnRecordMetadata,
        *,
        record_id: str,
        **updates,
    ) -> IterationTurnRecordMetadata:
        candidate = record.model_copy(
            update={
                "record_id": record_id,
                "generation": record.generation + 1,
                "integrity_digest": "",
                **updates,
            }
        )
        return IterationTurnRecordMetadata.model_validate(candidate.model_dump(mode="python"))

    @staticmethod
    def _validate_budget_progress(
        before: RootDecisionBudget,
        after: RootDecisionBudget,
    ) -> None:
        fields = (
            "decision_rounds_used",
            "root_provider_calls_used",
            "response_completion_tokens_used",
            "grounding_repairs_used",
            "decomposition_calls_used",
            "no_progress_rounds",
        )
        if any(getattr(after, field) < getattr(before, field) for field in fields):
            raise IterationTurnTransitionError("root decision budget usage cannot decrease")


__all__ = ["IterationTurnReducer", "IterationTurnTransitionError"]
