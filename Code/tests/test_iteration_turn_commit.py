from __future__ import annotations

import pytest

from autonomous_iteration.iteration_turn_commit import (
    IterationTurnCommitter,
    assistant_payload_hash,
)
from autonomous_iteration.iteration_turn_store import (
    IterationTurnConflictError,
    IterationTurnStore,
)
from metadata import (
    AssistantLedgerCommitState,
    AssistantTurnCommit,
    CompletedResponseOutcome,
    ConversationIdentity,
    GroundingDecision,
    GroundingStatus,
    IterationAuthorityState,
    IterationControlCursor,
    IterationDisposition,
    IterationPhase,
    IterationTurnRecordMetadata,
    PreTaskState,
    ResponseCandidate,
    RootDecisionBudget,
    SessionIngressState,
    SessionTurn,
)


def _identity(*, turn_index: int) -> ConversationIdentity:
    return ConversationIdentity(
        conversation_id="conversation-1",
        run_id="run-1",
        turn_index=turn_index,
        project_root="/tmp/project",
    )


def _pending_record(store: IterationTurnStore, *, content: str = "durable response"):
    payload = {
        "message_id": "message-assistant-1",
        "turn_index": 2,
        "content": content,
    }
    reference = store.save_artifact(
        "conversation-1",
        "run-1",
        kind="response_payload",
        payload=payload,
    )
    response_hash = assistant_payload_hash(payload)
    grounding = GroundingDecision(
        response_hash=response_hash,
        status=GroundingStatus.APPROVED,
        candidate_claim_coverage="complete",
    )
    return IterationTurnRecordMetadata(
        record_id="record-response-pending",
        identity=_identity(turn_index=1),
        pre_task_state=PreTaskState(
            user_message_id="message-user-1",
            user_input_ref=reference.model_copy(update={"kind": "user_input"}),
            session_authority_hash="sha256:" + "a" * 64,
        ),
        cursor=IterationControlCursor(
            phase=IterationPhase.COMPLETE,
            current_disposition=IterationDisposition.COMPLETE_RESPONSE,
            authority_state=IterationAuthorityState(
                reason="Response-only authority.",
                authority_hash="sha256:" + "b" * 64,
            ),
        ),
        root_budget=RootDecisionBudget(),
        response_candidate=ResponseCandidate(
            candidate_id="candidate-1",
            response_ref=reference,
            response_hash=response_hash,
        ),
        grounding_decision=grounding,
        outcome=CompletedResponseOutcome(
            response_ref=reference,
            response_hash=response_hash,
            grounding_decision_hash=grounding.canonical_hash,
        ),
        assistant_commit=AssistantTurnCommit(
            state=AssistantLedgerCommitState.PENDING,
            message_id=payload["message_id"],
            turn_index=payload["turn_index"],
            payload_ref=reference,
            payload_hash=response_hash,
        ),
    )


def _seed_ingress(store: IterationTurnStore) -> None:
    user_turn = SessionTurn(
        identity=_identity(turn_index=1),
        message_id="message-user-1",
        role="user",
        content="question",
    )
    state = SessionIngressState(identity=_identity(turn_index=1), turns=[user_turn])
    store.save_ingress(state, expected_revision=0)


def test_commit_appends_exact_assistant_turn_and_marks_record_committed(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)
    _seed_ingress(store)
    pending = _pending_record(store)

    result = IterationTurnCommitter(store).commit(pending)
    ingress, revision = store.load_ingress("conversation-1")

    assert result.record.assistant_commit.state == AssistantLedgerCommitState.COMMITTED
    assert result.payload == "durable response"
    assert result.replayed is False
    assert revision == 2
    assert ingress is not None
    assert [(turn.message_id, turn.role, turn.content) for turn in ingress.turns] == [
        ("message-user-1", "user", "question"),
        ("message-assistant-1", "assistant", "durable response"),
    ]


def test_exact_commit_retry_is_idempotent_and_replays_same_payload(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)
    _seed_ingress(store)
    pending = _pending_record(store)
    committer = IterationTurnCommitter(store)
    first = committer.commit(pending)

    second = committer.commit(pending)
    ingress, revision = store.load_ingress("conversation-1")

    assert second.record == first.record
    assert second.payload == first.payload
    assert second.replayed is True
    assert ingress is not None and len(ingress.turns) == 2
    assert revision == 2


def test_same_message_id_with_different_payload_fails_closed(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)
    _seed_ingress(store)
    conflicting = SessionTurn(
        identity=_identity(turn_index=2),
        message_id="message-assistant-1",
        role="assistant",
        content="different response",
    )
    ingress, revision = store.load_ingress("conversation-1")
    assert ingress is not None
    store.save_ingress(
        ingress.model_copy(
            update={"identity": conflicting.identity, "turns": [*ingress.turns, conflicting]}
        ),
        expected_revision=revision,
    )

    with pytest.raises(IterationTurnConflictError, match="different payload"):
        IterationTurnCommitter(store).commit(_pending_record(store))


@pytest.mark.parametrize("fault_boundary", ["pending_record", "assistant_ingress", "committed_record"])
def test_commit_recovers_from_each_durable_write_boundary(tmp_path, fault_boundary) -> None:
    store = IterationTurnStore(tmp_path)
    _seed_ingress(store)
    pending = _pending_record(store)
    fired = False

    def fail_once(boundary: str) -> None:
        nonlocal fired
        if boundary == fault_boundary and not fired:
            fired = True
            raise RuntimeError(f"crash after {boundary}")

    with pytest.raises(RuntimeError, match="crash after"):
        IterationTurnCommitter(store, after_write=fail_once).commit(pending)

    recovered = IterationTurnCommitter(store).commit(pending)
    ingress, _revision = store.load_ingress("conversation-1")

    assert recovered.payload == "durable response"
    assert recovered.record.assistant_commit.state == AssistantLedgerCommitState.COMMITTED
    assert recovered.replayed is True
    assert ingress is not None
    assert [turn.message_id for turn in ingress.turns].count("message-assistant-1") == 1


def test_artifact_record_payload_mismatch_fails_closed(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)
    _seed_ingress(store)
    pending = _pending_record(store)
    mismatched = pending.model_copy(
        update={
            "assistant_commit": pending.assistant_commit.model_copy(
                update={"message_id": "different-message"}
            )
        }
    )

    with pytest.raises(IterationTurnConflictError, match="artifact.*message"):
        IterationTurnCommitter(store).commit(mismatched)


def test_concurrent_terminal_commit_converges_on_same_record(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)
    _seed_ingress(store)
    pending = _pending_record(store)
    winner = None

    def commit_competitor(boundary: str) -> None:
        nonlocal winner
        if boundary == "assistant_ingress":
            winner = IterationTurnCommitter(store).commit(pending)

    converged = IterationTurnCommitter(store, after_write=commit_competitor).commit(pending)

    assert winner is not None
    assert converged.record == winner.record
    assert converged.payload == winner.payload
    assert converged.replayed is True
