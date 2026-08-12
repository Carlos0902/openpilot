"""Single state-transition owner for pre-task iteration turn records."""

from __future__ import annotations

from metadata import (
    ActiveTaskBinding,
    AssistantLedgerCommitState,
    AssistantTurnCommit,
    CompletedResponseOutcome,
    GroundingDecision,
    IterationBoundary,
    IterationControlCursor,
    IterationTurnRecordMetadata,
    NoTaskBinding,
    PreparedTaskBinding,
    ResponseCandidate,
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
        if record.outcome is not None or record.response_candidate is not None:
            raise IterationTurnTransitionError("response preparation requires an incomplete turn")
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


__all__ = ["IterationTurnReducer", "IterationTurnTransitionError"]
