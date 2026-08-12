from __future__ import annotations

import pytest

from autonomous_iteration.iteration_turn_reducer import (
    IterationTurnReducer,
    IterationTurnTransitionError,
)
from metadata import (
    ActiveTaskBinding,
    ConversationIdentity,
    DurableArtifactReference,
    IterationAuthorityState,
    IterationControlCursor,
    IterationTurnRecordMetadata,
    PreTaskState,
    PreparedTaskBinding,
    RootDecisionBudget,
)


def _artifact(kind: str) -> DurableArtifactReference:
    return DurableArtifactReference(
        artifact_id=f"artifact-{kind}",
        kind=kind,
        integrity_checksum="sha256:" + "a" * 64,
        bytes=1,
    )


def _record() -> IterationTurnRecordMetadata:
    return IterationTurnRecordMetadata(
        record_id="record-1",
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-1",
            turn_index=1,
            project_root="/tmp/project",
        ),
        pre_task_state=PreTaskState(
            user_message_id="message-user-1",
            user_input_ref=_artifact("user_input"),
            session_authority_hash="sha256:" + "b" * 64,
        ),
        cursor=IterationControlCursor(
            authority_state=IterationAuthorityState(
                reason="Response-only authority.",
                authority_hash="sha256:" + "c" * 64,
            )
        ),
        root_budget=RootDecisionBudget(),
    )


def _prepared() -> PreparedTaskBinding:
    return PreparedTaskBinding(
        task_id="task-1",
        state_digest="sha256:" + "d" * 64,
        snapshot_ref=_artifact("canonical_initial_task"),
        snapshot_hash="sha256:" + "e" * 64,
        authority_revision=0,
        authority_hash="sha256:" + "f" * 64,
    )


def test_reducer_owns_prepared_to_active_generation_transition() -> None:
    prepared = IterationTurnReducer.prepare_task(_record(), _prepared())
    active_binding = ActiveTaskBinding(
        **prepared.task_binding.model_dump(exclude={"state"}),
        checkpoint_id="checkpoint-1",
        checkpoint_digest="sha256:" + "1" * 64,
    )

    active = IterationTurnReducer.activate_task(prepared, active_binding)

    assert prepared.generation == 2
    assert prepared.record_id.endswith("-task-prepared")
    assert active.generation == 3
    assert active.record_id.endswith("-active")
    assert active.task_binding == active_binding


def test_reducer_rejects_skipped_or_identity_changing_task_transition() -> None:
    active = ActiveTaskBinding(
        **_prepared().model_dump(exclude={"state"}),
        checkpoint_id="checkpoint-1",
        checkpoint_digest="sha256:" + "1" * 64,
    )
    with pytest.raises(IterationTurnTransitionError, match="prepared"):
        IterationTurnReducer.activate_task(_record(), active)

    prepared_record = IterationTurnReducer.prepare_task(_record(), _prepared())
    changed = active.model_copy(update={"task_id": "other-task"})
    with pytest.raises(IterationTurnTransitionError, match="identity"):
        IterationTurnReducer.activate_task(prepared_record, changed)
