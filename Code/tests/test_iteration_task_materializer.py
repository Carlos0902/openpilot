from __future__ import annotations

import pytest

from autonomous_iteration.checkpoint_store import RuntimeCheckpointStore
from autonomous_iteration.iteration_task_materializer import (
    IterationTaskMaterializer,
    TaskMaterializationError,
    TaskMaterializationFailureCode,
)
from autonomous_iteration.iteration_turn_store import IterationTurnStore
from metadata import (
    CanonicalInitialTaskSnapshot,
    ConversationIdentity,
    IterationAuthorityCeiling,
    IterationAuthoritySource,
    IterationAuthorityState,
    IterationControlCursor,
    IterationDisposition,
    IterationPhase,
    IterationTurnRecordMetadata,
    PreTaskState,
    RootDecisionBudget,
    RuntimeCheckpointMetadata,
    RuntimeStateMetadata,
    SessionConstraintCategory,
    SessionConstraintProposal,
    SessionConstraintSourceKind,
    SessionConstraintValue,
    SessionIngressState,
    TaskGraphNodeMetadata,
)


def _identity() -> ConversationIdentity:
    return ConversationIdentity(
        conversation_id="conversation-1",
        run_id="run-1",
        turn_index=1,
        project_root="/tmp/project",
    )


def _fixture(turn_store: IterationTurnStore):
    ingress = SessionIngressState(identity=_identity())
    user_ref = turn_store.save_artifact(
        "conversation-1", "run-1", kind="user_input", payload={"content": "Inspect project"}
    )
    authority = IterationAuthorityState(
        reason="Read-only task may be materialized.",
        authority_hash="sha256:" + "a" * 64,
    )
    budget = RootDecisionBudget()
    record = IterationTurnRecordMetadata(
        record_id="record-materialize",
        identity=_identity(),
        pre_task_state=PreTaskState(
            user_message_id="message-user-1",
            user_input_ref=user_ref,
            session_authority_revision=ingress.session_constraints.revision,
            session_authority_hash=ingress.session_constraints.authority_hash,
        ),
        cursor=IterationControlCursor(
            phase=IterationPhase.MATERIALIZE_TASK,
            current_disposition=IterationDisposition.FORM_SINGLE_TASK,
            authority_state=authority,
        ),
        root_budget=budget,
    )
    state = RuntimeStateMetadata(
        goal="Inspect project",
        execution_mode="read_only",
        execution_mode_source="user_constraint",
        execution_mode_reason="Prepared from response-only authority.",
        session_constraints=ingress.session_constraints,
    )
    checkpoint = RuntimeCheckpointMetadata(
        checkpoint_id="initial-task-1",
        generation=1,
        run_id="run-1",
        root_task_id="task-1",
        session_id="run-1",
        checkpoint_reason="initial task materialized",
        safe_boundary="decomposition_recorded",
        runtime_state=state,
        session_ingress_state=ingress,
        project_fingerprint={"project_root": "/tmp/project", "environment_id": "env-1"},
    )
    snapshot = CanonicalInitialTaskSnapshot(
        task_graph=(
            TaskGraphNodeMetadata(
                task_id="task-1",
                description="Inspect project",
                task_kind="inspect",
                read_files=["README.md"],
            ),
        ),
        execution_order=("task-1",),
        initial_checkpoint=checkpoint,
        authority_state=authority,
        root_budget=budget,
        session_authority_revision=ingress.session_constraints.revision,
        session_authority_hash=ingress.session_constraints.authority_hash,
    )
    return ingress, record, snapshot


def test_materializer_commits_prepared_checkpoint_and_active_binding(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    ingress, record, snapshot = _fixture(turn_store)
    turn_store.save(record)

    active = IterationTaskMaterializer(turn_store, checkpoint_store).materialize(
        record, snapshot=snapshot, current_ingress=ingress
    )

    assert active.task_binding.state == "active"
    assert active.task_binding.task_id == "task-1"
    assert active.task_binding.snapshot_hash == snapshot.canonical_hash
    checkpoint = checkpoint_store.load("run-1", "initial-task-1")
    assert checkpoint is not None
    assert active.task_binding.checkpoint_digest == checkpoint.integrity_checksum
    assert turn_store.load_artifact(
        "conversation-1", "run-1", active.task_binding.snapshot_ref
    ) == snapshot.model_dump(mode="json")


@pytest.mark.parametrize("fault_boundary", ["prepared_binding", "initial_checkpoint", "active_binding"])
def test_materializer_recovers_without_regenerating_task(tmp_path, fault_boundary) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    ingress, record, snapshot = _fixture(turn_store)
    turn_store.save(record)
    fired = False

    def fail_once(boundary: str) -> None:
        nonlocal fired
        if boundary == fault_boundary and not fired:
            fired = True
            raise RuntimeError(f"crash after {boundary}")

    with pytest.raises(RuntimeError, match="crash after"):
        IterationTaskMaterializer(
            turn_store, checkpoint_store, after_write=fail_once
        ).materialize(record, snapshot=snapshot, current_ingress=ingress)

    recovered = IterationTaskMaterializer(turn_store, checkpoint_store).materialize(
        record, snapshot=snapshot, current_ingress=ingress
    )

    assert recovered.task_binding.state == "active"
    assert checkpoint_store.load_latest("run-1").generation == 1
    assert turn_store.load_latest("conversation-1", "run-1") == recovered


def test_materializer_rejects_stale_session_authority(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    ingress, record, snapshot = _fixture(turn_store)
    turn_store.save(record)
    stale = ingress.model_copy(
        update={
            "session_constraints": ingress.session_constraints.model_copy(update={"revision": 1})
        }
    )

    with pytest.raises(TaskMaterializationError) as caught:
        IterationTaskMaterializer(turn_store, checkpoint_store).materialize(
            record, snapshot=snapshot, current_ingress=stale
        )

    assert caught.value.code == TaskMaterializationFailureCode.AUTHORITY_STALE
    assert checkpoint_store.load_latest("run-1") is None


def test_prepared_recovery_fails_when_snapshot_is_corrupt(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    ingress, record, snapshot = _fixture(turn_store)
    turn_store.save(record)

    def crash_after_prepared(boundary: str) -> None:
        if boundary == "prepared_binding":
            raise RuntimeError("crash")

    with pytest.raises(RuntimeError):
        IterationTaskMaterializer(
            turn_store, checkpoint_store, after_write=crash_after_prepared
        ).materialize(record, snapshot=snapshot, current_ingress=ingress)
    prepared = turn_store.load_latest("conversation-1", "run-1")
    artifact_path = (
        tmp_path
        / "turns"
        / "conversation-1"
        / "run-1"
        / "artifacts"
        / f"{prepared.task_binding.snapshot_ref.artifact_id}.json"
    )
    artifact_path.write_text("{}", encoding="utf-8")

    with pytest.raises(TaskMaterializationError) as caught:
        IterationTaskMaterializer(turn_store, checkpoint_store).recover(
            prepared, current_ingress=ingress
        )

    assert caught.value.code == TaskMaterializationFailureCode.SNAPSHOT_UNAVAILABLE


def test_checkpoint_before_active_digest_mismatch_fails_closed(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    ingress, record, snapshot = _fixture(turn_store)
    turn_store.save(record)

    def crash_after_checkpoint(boundary: str) -> None:
        if boundary == "initial_checkpoint":
            raise RuntimeError("crash")

    with pytest.raises(RuntimeError):
        IterationTaskMaterializer(
            turn_store, checkpoint_store, after_write=crash_after_checkpoint
        ).materialize(record, snapshot=snapshot, current_ingress=ingress)
    checkpoint_path = (
        tmp_path / "checkpoints" / "run-1" / "checkpoints" / "initial-task-1.json"
    )
    checkpoint_path.write_text("{}", encoding="utf-8")
    prepared = turn_store.load_latest("conversation-1", "run-1")

    with pytest.raises(TaskMaterializationError) as caught:
        IterationTaskMaterializer(turn_store, checkpoint_store).recover(
            prepared, current_ingress=ingress
        )

    assert caught.value.code == TaskMaterializationFailureCode.CHECKPOINT_MISMATCH


def test_active_recovery_revalidates_checkpoint_integrity(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    ingress, record, snapshot = _fixture(turn_store)
    turn_store.save(record)
    active = IterationTaskMaterializer(turn_store, checkpoint_store).materialize(
        record, snapshot=snapshot, current_ingress=ingress
    )
    checkpoint_path = (
        tmp_path / "checkpoints" / "run-1" / "checkpoints" / "initial-task-1.json"
    )
    checkpoint_path.write_text("{}", encoding="utf-8")

    with pytest.raises(TaskMaterializationError) as caught:
        IterationTaskMaterializer(turn_store, checkpoint_store).recover(
            active, current_ingress=ingress
        )

    assert caught.value.code == TaskMaterializationFailureCode.CHECKPOINT_MISMATCH


def test_rejected_lineage_invalidates_prepared_authority_without_hash_change(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    ingress, record, snapshot = _fixture(turn_store)
    turn_store.save(record)
    rejected = SessionConstraintProposal(
        proposal_id="proposal-rejected",
        session_id="conversation-1",
        constraint_key="write_scope",
        category=SessionConstraintCategory.WRITE_SCOPE,
        value=SessionConstraintValue(allowed_files=["README.md"]),
        statement="Only README.md may be modified.",
        source_kind=SessionConstraintSourceKind.USER_MESSAGE,
        source_id="message-user-1",
        source_turn_index=1,
        source_hash="sha256:" + "f" * 64,
        status="rejected",
    )
    current = SessionIngressState.model_validate(
        ingress.model_copy(update={"pending_proposals": [rejected]}).model_dump(mode="python")
    )

    with pytest.raises(TaskMaterializationError) as caught:
        IterationTaskMaterializer(turn_store, checkpoint_store).materialize(
            record, snapshot=snapshot, current_ingress=current
        )

    assert caught.value.code == TaskMaterializationFailureCode.AUTHORITY_STALE


def test_mutation_snapshot_requires_confirmation_in_current_ingress(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    ingress, record, snapshot = _fixture(turn_store)
    authority = IterationAuthorityState(
        ceiling=IterationAuthorityCeiling.MUTATION_ELIGIBLE,
        source=IterationAuthoritySource.USER_CONFIRMATION,
        reason="User confirmed the mutation scope.",
        authority_hash="sha256:" + "c" * 64,
        confirmation_message_id="confirmation-message",
        confirmation_turn_index=1,
    )
    record = IterationTurnRecordMetadata.model_validate(
        record.model_copy(
            update={
                "cursor": record.cursor.model_copy(update={"authority_state": authority})
            }
        ).model_dump(mode="python")
    )
    checkpoint = snapshot.initial_checkpoint.model_copy(
        update={
            "runtime_state": snapshot.initial_checkpoint.runtime_state.model_copy(
                update={"execution_mode": "mutation_allowed"}
            )
        }
    )
    snapshot = CanonicalInitialTaskSnapshot.model_validate(
        snapshot.model_copy(
            update={"authority_state": authority, "initial_checkpoint": checkpoint}
        ).model_dump(mode="python")
    )
    turn_store.save(record)

    with pytest.raises(TaskMaterializationError) as caught:
        IterationTaskMaterializer(turn_store, checkpoint_store).materialize(
            record, snapshot=snapshot, current_ingress=ingress
        )

    assert caught.value.code == TaskMaterializationFailureCode.CONFIRMATION_STALE


def test_concurrent_active_binding_writers_converge(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    ingress, record, snapshot = _fixture(turn_store)
    turn_store.save(record)
    winner = None

    def activate_competitor(boundary: str) -> None:
        nonlocal winner
        if boundary == "initial_checkpoint":
            prepared = turn_store.load_latest("conversation-1", "run-1")
            winner = IterationTaskMaterializer(turn_store, checkpoint_store).recover(
                prepared, current_ingress=ingress
            )

    converged = IterationTaskMaterializer(
        turn_store, checkpoint_store, after_write=activate_competitor
    ).materialize(record, snapshot=snapshot, current_ingress=ingress)

    assert winner is not None
    assert converged == winner
    assert converged.task_binding.state == "active"
