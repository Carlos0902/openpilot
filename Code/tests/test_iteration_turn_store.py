from __future__ import annotations

import json

import pytest

from autonomous_iteration.iteration_turn_store import (
    IterationTurnConflictError,
    IterationTurnSecretError,
    IterationTurnStore,
)
from metadata import (
    ConversationIdentity,
    DurableArtifactReference,
    IterationAuthorityState,
    IterationControlCursor,
    IterationTurnRecordMetadata,
    PreTaskState,
    RootDecisionBudget,
    SessionIngressState,
)


def _artifact(kind: str) -> DurableArtifactReference:
    return DurableArtifactReference(
        artifact_id=f"artifact-{kind}",
        kind=kind,
        integrity_checksum="sha256:" + "a" * 64,
        bytes=1,
    )


def _identity(*, run_id: str = "run-1", turn_index: int = 1) -> ConversationIdentity:
    return ConversationIdentity(
        conversation_id="conversation-1",
        run_id=run_id,
        turn_index=turn_index,
        project_root="/tmp/project",
    )


def _record(*, generation: int = 1) -> IterationTurnRecordMetadata:
    return IterationTurnRecordMetadata(
        record_id=f"record-{generation}",
        identity=_identity(),
        pre_task_state=PreTaskState(
            user_message_id="message-user-1",
            user_input_ref=_artifact("user-input"),
            session_authority_hash="sha256:" + "b" * 64,
        ),
        generation=generation,
        cursor=IterationControlCursor(
            authority_state=IterationAuthorityState(
                reason="No side effect was requested.",
                authority_hash="sha256:" + "c" * 64,
            )
        ),
        root_budget=RootDecisionBudget(),
    )


def test_turn_store_round_trips_integrity_bound_generations(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)

    first = store.save(_record())
    second = store.save(_record(generation=2), expected_generation=1)

    assert first.integrity_digest.startswith("sha256:")
    assert second.generation == 2
    assert store.load("conversation-1", "run-1", first.record_id) == first
    assert store.load_latest("conversation-1", "run-1") == second


def test_turn_store_rejects_stale_and_duplicate_generations(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)
    store.save(_record())

    with pytest.raises(IterationTurnConflictError, match="expected 0"):
        store.save(_record(generation=2), expected_generation=0)
    with pytest.raises(IterationTurnConflictError, match="advance"):
        store.save(_record(generation=1), expected_generation=1)


def test_turn_store_falls_back_when_latest_record_is_corrupt(tmp_path) -> None:
    warnings: list[str] = []
    store = IterationTurnStore(tmp_path, warning_sink=warnings.append)
    first = store.save(_record())
    second = store.save(_record(generation=2), expected_generation=1)
    latest_path = tmp_path / "conversation-1" / "run-1" / "records" / f"{second.record_id}.json"
    latest_path.write_text("{}", encoding="utf-8")

    assert store.load_latest("conversation-1", "run-1") == first
    assert any("falling back" in warning for warning in warnings)


def test_turn_store_artifacts_are_checksum_bound_and_reject_secrets(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)
    reference = store.save_artifact(
        "conversation-1",
        "run-1",
        kind="response_payload",
        payload={"message_id": "message-assistant-1", "content": "hello"},
    )

    assert store.load_artifact("conversation-1", "run-1", reference) == {
        "message_id": "message-assistant-1",
        "content": "hello",
    }
    wrong_kind = reference.model_copy(update={"kind": "canonical_initial_task"})
    assert store.load_artifact("conversation-1", "run-1", wrong_kind) is None
    with pytest.raises(IterationTurnSecretError, match="api_key"):
        store.save_artifact(
            "conversation-1",
            "run-1",
            kind="response_payload",
            payload={"api_key": "secret"},
        )


def test_turn_store_artifacts_are_content_addressed_and_idempotent(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)
    payload = {"message_id": "message-assistant-1", "content": "hello"}

    first = store.save_artifact(
        "conversation-1",
        "run-1",
        kind="response_payload",
        payload=payload,
    )
    second = store.save_artifact(
        "conversation-1",
        "run-1",
        kind="response_payload",
        payload=payload,
    )

    assert second == first
    assert first.artifact_id == first.integrity_checksum.removeprefix("sha256:")


def test_session_ingress_store_uses_compare_and_swap_and_checksum(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)
    ingress = SessionIngressState(identity=_identity(run_id="run-0", turn_index=0))

    revision = store.save_ingress(ingress, expected_revision=0)
    restored, restored_revision = store.load_ingress("conversation-1")

    assert revision == 1
    assert restored == ingress
    assert restored_revision == 1
    with pytest.raises(IterationTurnConflictError, match="ingress revision"):
        store.save_ingress(ingress, expected_revision=0)


def test_ingress_checksum_tampering_fails_closed(tmp_path) -> None:
    warnings: list[str] = []
    store = IterationTurnStore(tmp_path, warning_sink=warnings.append)
    ingress = SessionIngressState(identity=_identity(run_id="run-0", turn_index=0))
    store.save_ingress(ingress, expected_revision=0)
    path = tmp_path / "conversation-1" / "session_ingress.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"]["identity"]["project_root"] = "/tmp/other"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert store.load_ingress("conversation-1") == (None, 0)
    assert any("checksum" in warning for warning in warnings)
    with pytest.raises(IterationTurnConflictError, match="unreadable"):
        store.save_ingress(ingress, expected_revision=0)
