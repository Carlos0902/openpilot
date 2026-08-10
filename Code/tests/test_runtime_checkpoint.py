from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from autonomous_iteration.checkpoint_store import (
    CheckpointConflictError,
    CheckpointSecretError,
    RuntimeCheckpointStore,
)
from metadata import (
    AgentPhase,
    CheckpointBoundary,
    ConversationIdentity,
    DurableArtifactReference,
    LLMReplayEntry,
    ObservedFileMutationResult,
    PendingLLMRequest,
    RuntimeCheckpointMetadata,
    RuntimeStateMetadata,
    SessionExecutionCursor,
    SessionConstraintAuthority,
    SessionConstraintCategory,
    SessionConstraintEntry,
    SessionConstraintSourceKind,
    SessionConstraintState,
    SessionIngressState,
    SessionTurn,
    SessionConstraintValue,
    SessionSemanticSnapshot,
    SessionStage,
    SessionTaskResult,
    TaskGraphNodeMetadata,
    ToolInputMetadata,
)


def checkpoint(*, checkpoint_id: str = "checkpoint-1", generation: int = 1) -> RuntimeCheckpointMetadata:
    state = RuntimeStateMetadata(goal="Inspect project", phase=AgentPhase.UNDERSTAND_PROJECT)
    state.add_fact("project root resolved")
    state.budget.consume_tool_call(file_read=True)
    return RuntimeCheckpointMetadata(
        checkpoint_id=checkpoint_id,
        generation=generation,
        run_id="run-1",
        root_task_id="task-1",
        session_id="session-1",
        checkpoint_reason="read result applied",
        safe_boundary="tool_result_applied",
        runtime_state=state,
        last_durable_event_id="event-4",
        last_durable_event_sequence=4,
        side_effect_state="applied",
        tool_name="file_reader",
        tool_call_id="call-1",
        tool_input_hash="sha256:input",
        mutation_class="read_only",
        project_fingerprint={
            "project_root": "/workspace/project",
            "git_head": "abc123",
            "target_file_hashes": {"README.md": "sha256:file"},
        },
    )


def test_runtime_checkpoint_round_trips_runtime_state_and_budget() -> None:
    original = checkpoint()

    restored = RuntimeCheckpointMetadata.model_validate(original.to_json_dict())

    assert restored.runtime_state.phase == AgentPhase.UNDERSTAND_PROJECT
    assert restored.runtime_state.known_facts == ["project root resolved"]
    assert restored.runtime_state.budget.tool_calls_used == 1
    assert restored.runtime_state.budget.file_reads_used == 1
    assert restored.side_effect_state == "applied"
    assert restored.mutation_class == "read_only"
    assert restored.safe_boundary == CheckpointBoundary.TOOL_RESULT_APPLIED


def test_runtime_checkpoint_round_trips_active_session_constraint_state() -> None:
    state = RuntimeStateMetadata(
        goal="Repair calculator",
        session_constraints=SessionConstraintState(
            session_id="session-1",
            revision=1,
            processed_through_turn=2,
            entries=[
                SessionConstraintEntry(
                    constraint_id="constraint-1",
                    constraint_key="write_scope",
                    category=SessionConstraintCategory.WRITE_SCOPE,
                    value=SessionConstraintValue(allowed_files=["calculator.py"]),
                    status="active",
                    statement="Only calculator.py may be modified.",
                    source_kind=SessionConstraintSourceKind.USER_CONFIRMATION,
                    source_id="user-1",
                    source_turn_index=1,
                    source_hash="sha256:" + "a" * 64,
                    authority=SessionConstraintAuthority.USER_CONFIRMED,
                )
            ],
        ),
    )
    value = checkpoint().model_copy(update={"runtime_state": state})

    restored = RuntimeCheckpointMetadata.model_validate(value.to_json_dict())

    assert restored.runtime_state.session_constraints.session_id == "session-1"
    assert restored.runtime_state.session_constraints.active_entries[0].value.allowed_files == [
        "calculator.py"
    ]


def test_runtime_checkpoint_round_trips_conversation_ingress_state() -> None:
    runtime_state = RuntimeStateMetadata(
        goal="Repair calculator",
        session_constraints=SessionConstraintState(session_id="conversation-1"),
    )
    ingress = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-1",
            turn_index=1,
            project_root="/workspace/project",
        ),
        turns=[
            SessionTurn(
                identity=ConversationIdentity(
                    conversation_id="conversation-1",
                    run_id="run-1",
                    turn_index=1,
                    project_root="/workspace/project",
                ),
                message_id="message-1",
                role="user",
                content="Inspect calculator.py.",
            )
        ],
        session_constraints=runtime_state.session_constraints,
    )
    value = checkpoint().model_copy(
        update={
            "runtime_state": runtime_state,
            "session_ingress_state": ingress,
            "session_id": "run-1",
        }
    )

    restored = RuntimeCheckpointMetadata.model_validate(value.to_json_dict())

    assert restored.session_ingress_state is not None
    assert restored.session_ingress_state.identity.conversation_id == "conversation-1"
    assert restored.session_ingress_state.turns[0].message_id == "message-1"


def test_runtime_checkpoint_rejects_divergent_ingress_constraints() -> None:
    runtime_state = RuntimeStateMetadata(
        goal="Repair calculator",
        session_constraints=SessionConstraintState(session_id="conversation-1"),
    )
    ingress = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-1",
            turn_index=0,
            project_root="/workspace/project",
        ),
        session_constraints=SessionConstraintState(
            session_id="conversation-1", revision=1, processed_through_turn=1
        ),
    )
    value = checkpoint().model_copy(
        update={"runtime_state": runtime_state, "session_ingress_state": ingress}
    )

    with pytest.raises(ValueError, match="ingress constraints"):
        RuntimeCheckpointMetadata.model_validate(value.to_json_dict())


def test_runtime_checkpoint_rejects_ingress_session_identity_drift() -> None:
    runtime_state = RuntimeStateMetadata(
        goal="Repair calculator",
        session_constraints=SessionConstraintState(session_id="conversation-1"),
    )
    ingress = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="different-run",
            turn_index=0,
            project_root="/workspace/project",
        ),
        session_constraints=runtime_state.session_constraints,
    )
    value = checkpoint().model_copy(
        update={"runtime_state": runtime_state, "session_ingress_state": ingress}
    )

    with pytest.raises(ValueError, match="session identity"):
        RuntimeCheckpointMetadata.model_validate(value.to_json_dict())


def test_runtime_checkpoint_rejects_unregistered_safe_boundary() -> None:
    payload = checkpoint().to_json_dict()
    payload["safe_boundary"] = "anonymous_controller_boundary"

    with pytest.raises(ValueError):
        RuntimeCheckpointMetadata.model_validate(payload)


def test_runtime_checkpoint_round_trips_owned_session_execution_cursor() -> None:
    value = checkpoint().model_copy(
        update={
            "safe_boundary": CheckpointBoundary.SUBTASK_RESULT_APPLIED,
            "session_cursor": SessionExecutionCursor(
                stage=SessionStage.TASK_EXECUTION,
                plan_hash="sha256:plan",
                semantic=SessionSemanticSnapshot(
                    task_type="coding",
                    risk_level="low",
                    confidence=0.9,
                ),
                original_task=TaskGraphNodeMetadata(task_id="root", description="Build app"),
                tasks=[
                    TaskGraphNodeMetadata(task_id="task-1", description="Inspect app"),
                    TaskGraphNodeMetadata(
                        task_id="task-2",
                        description="Fix app",
                        dependencies=["task-1"],
                        write_files=["app.py"],
                        validation_command="pytest -q",
                    ),
                ],
                execution_order=["task-1", "task-2"],
                next_task_index=1,
                results=[
                    SessionTaskResult(
                        task_id="task-1",
                        status="completed",
                        summary_text="inspection complete",
                    )
                ],
            ),
        }
    )

    restored = RuntimeCheckpointMetadata.model_validate(value.to_json_dict())

    assert restored.session_cursor is not None
    assert restored.session_cursor.stage == SessionStage.TASK_EXECUTION
    assert restored.session_cursor.next_task_index == 1
    assert restored.session_cursor.execution_order == ["task-1", "task-2"]
    assert restored.session_cursor.results[0].task_id == "task-1"


def test_session_execution_cursor_rejects_gap_before_next_task() -> None:
    with pytest.raises(ValueError, match="result for every task before next_task_index"):
        SessionExecutionCursor(
            stage=SessionStage.TASK_EXECUTION,
            plan_hash="sha256:plan",
            semantic=SessionSemanticSnapshot(task_type="coding", risk_level="low"),
            original_task=TaskGraphNodeMetadata(task_id="root", description="Build app"),
            tasks=[TaskGraphNodeMetadata(task_id="task-1", description="Inspect app")],
            execution_order=["task-1"],
            next_task_index=1,
            results=[],
        )


def test_checkpoint_store_writes_generation_and_loads_latest(tmp_path) -> None:
    store = RuntimeCheckpointStore(tmp_path)

    saved = store.save(checkpoint())
    loaded = store.load_latest("run-1")

    assert saved.integrity_checksum.startswith("sha256:")
    assert loaded is not None
    assert loaded.checkpoint_id == "checkpoint-1"
    assert loaded.generation == 1
    pointer = json.loads((tmp_path / "run-1" / "latest_checkpoint.json").read_text(encoding="utf-8"))
    assert pointer == {
        "checkpoint_id": "checkpoint-1",
        "generation": 1,
        "integrity_checksum": saved.integrity_checksum,
    }


def test_checkpoint_store_rejects_stale_generation(tmp_path) -> None:
    store = RuntimeCheckpointStore(tmp_path)
    store.save(checkpoint())

    with pytest.raises(CheckpointConflictError, match="generation"):
        store.save(
            checkpoint(checkpoint_id="checkpoint-2", generation=2),
            expected_generation=0,
        )


def test_checkpoint_store_falls_back_when_latest_checkpoint_is_corrupt(tmp_path) -> None:
    warnings: list[str] = []
    store = RuntimeCheckpointStore(tmp_path, warning_sink=warnings.append)
    first = store.save(checkpoint())
    store.save(
        checkpoint(checkpoint_id="checkpoint-2", generation=2),
        expected_generation=1,
    )
    latest_file = tmp_path / "run-1" / "checkpoints" / "checkpoint-2.json"
    latest_file.write_text('{"truncated":', encoding="utf-8")

    loaded = store.load_latest("run-1")

    assert loaded is not None
    assert loaded.checkpoint_id == first.checkpoint_id
    assert any("checkpoint-2" in warning for warning in warnings)


def test_checkpoint_store_advances_generation_after_latest_pointer_is_corrupt(tmp_path) -> None:
    store = RuntimeCheckpointStore(tmp_path)
    store.save(checkpoint())
    pointer = tmp_path / "run-1" / "latest_checkpoint.json"
    pointer.write_text('{"truncated":', encoding="utf-8")

    saved = store.save(
        checkpoint(checkpoint_id="checkpoint-2", generation=2),
        expected_generation=1,
    )

    assert saved.generation == 2
    assert store.load_latest("run-1").checkpoint_id == "checkpoint-2"


def test_checkpoint_store_rejects_checksum_mismatch(tmp_path) -> None:
    store = RuntimeCheckpointStore(tmp_path)
    saved = store.save(checkpoint())
    checkpoint_file = tmp_path / "run-1" / "checkpoints" / "checkpoint-1.json"
    payload = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    payload["checkpoint_reason"] = "tampered"
    checkpoint_file.write_text(json.dumps(payload), encoding="utf-8")

    assert store.load("run-1", saved.checkpoint_id) is None


def test_checkpoint_store_run_lease_allows_only_one_live_writer(tmp_path) -> None:
    first_store = RuntimeCheckpointStore(tmp_path)
    second_store = RuntimeCheckpointStore(tmp_path)

    first_lease = first_store.try_acquire_run_lease("run-1")
    assert first_lease is not None
    assert second_store.try_acquire_run_lease("run-1") is None

    first_store.release_run_lease(first_lease)
    second_lease = second_store.try_acquire_run_lease("run-1")
    assert second_lease is not None
    second_store.release_run_lease(second_lease)


def test_checkpoint_store_run_lease_is_exclusive_across_processes(tmp_path) -> None:
    child_code = textwrap.dedent(
        f"""
        import sys
        from autonomous_iteration.checkpoint_store import RuntimeCheckpointStore

        store = RuntimeCheckpointStore({str(tmp_path)!r})
        lease = store.try_acquire_run_lease("run-1")
        assert lease is not None
        print("ready", flush=True)
        sys.stdin.readline()
        store.release_run_lease(lease)
        """
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    child = subprocess.Popen(
        [sys.executable, "-c", child_code],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "ready"
    store = RuntimeCheckpointStore(tmp_path)

    try:
        assert store.try_acquire_run_lease("run-1") is None
    finally:
        assert child.stdin is not None
        child.stdin.write("release\n")
        child.stdin.flush()
        child.wait(timeout=5)

    assert child.returncode == 0
    lease = store.try_acquire_run_lease("run-1")
    assert lease is not None
    store.release_run_lease(lease)


def test_checkpoint_store_rejects_sensitive_values_in_runtime_state(tmp_path) -> None:
    store = RuntimeCheckpointStore(tmp_path)
    unsafe = checkpoint()
    unsafe.runtime_state.annotations["api_key"] = "must-not-be-persisted"

    with pytest.raises(CheckpointSecretError, match="api_key"):
        store.save(unsafe)

    assert not (tmp_path / "run-1" / "checkpoints" / "checkpoint-1.json").exists()


def test_checkpoint_project_fingerprint_rejects_unknown_control_fields() -> None:
    payload = checkpoint().to_json_dict()
    payload["project_fingerprint"]["untyped_resume_override"] = True

    with pytest.raises(ValueError, match="untyped_resume_override"):
        RuntimeCheckpointMetadata.model_validate(payload)


def test_file_mutation_checkpoint_round_trips_typed_input_and_observed_result() -> None:
    value = checkpoint().model_copy(
        update={
            "safe_boundary": "tool_result_observed",
            "side_effect_state": "observed",
            "mutation_class": "mutating",
            "tool_name": "file_writer",
            "tool_input": ToolInputMetadata(
                tool_name="file_writer",
                file_path="note.txt",
                content="after",
            ),
            "observed_file_result": ObservedFileMutationResult(
                success=True,
                file_path="note.txt",
            ),
        }
    )

    restored = RuntimeCheckpointMetadata.model_validate(value.to_json_dict())

    assert restored.tool_input is not None
    assert restored.tool_input.content == "after"
    assert restored.observed_file_result is not None
    assert restored.observed_file_result.success is True


def test_checkpoint_store_round_trips_checksum_verified_recovery_artifact(tmp_path) -> None:
    store = RuntimeCheckpointStore(tmp_path)

    reference = store.save_recovery_artifact(
        "run-1",
        kind="llm_response",
        payload={"content": "answer", "parsed_json": {"ok": True}},
    )

    assert reference.kind == "llm_response"
    assert reference.integrity_checksum.startswith("sha256:")
    assert store.load_recovery_artifact("run-1", reference) == {
        "content": "answer",
        "parsed_json": {"ok": True},
    }

    artifact_path = tmp_path / "run-1" / "recovery_artifacts" / f"{reference.artifact_id}.json"
    artifact_path.write_text('{"content":"tampered"}', encoding="utf-8")
    assert store.load_recovery_artifact("run-1", reference) is None


def test_runtime_checkpoint_round_trips_llm_replay_ledger() -> None:
    reference = DurableArtifactReference(
        artifact_id="response-1",
        kind="llm_response",
        integrity_checksum="sha256:response",
        bytes=42,
    )
    value = checkpoint().model_copy(
        update={
            "pending_llm_request": PendingLLMRequest(
                task_id="task-1",
                request_ordinal=2,
                request_hash="sha256:request-2",
            ),
            "llm_replay_entries": [
                LLMReplayEntry(
                    task_id="task-1",
                    request_ordinal=1,
                    request_hash="sha256:request-1",
                    response_artifact=reference,
                )
            ],
        }
    )

    restored = RuntimeCheckpointMetadata.model_validate(value.to_json_dict())

    assert restored.pending_llm_request is not None
    assert restored.pending_llm_request.request_ordinal == 2
    assert restored.llm_replay_entries[0].response_artifact.artifact_id == "response-1"
