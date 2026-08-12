"""Ledger-first commit and recovery for durable assistant responses."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from memory.session_ingress import SessionIngress
from metadata import (
    AssistantLedgerCommitState,
    CompletedResponseOutcome,
    IterationBoundary,
    IterationTurnRecordMetadata,
    SessionTurn,
)

from autonomous_iteration.iteration_turn_store import (
    IterationTurnConflictError,
    IterationTurnStore,
)


def assistant_payload_hash(payload: dict[str, Any]) -> str:
    """Return the canonical identity of the exact display/ledger payload."""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AssistantTurnCommitResult:
    """Exact durable payload plus its terminal turn record."""

    record: IterationTurnRecordMetadata
    payload: str
    replayed: bool


class IterationTurnCommitter:
    """Commit one prepared response without duplicating its assistant turn."""

    def __init__(
        self,
        store: IterationTurnStore,
        *,
        after_write: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.after_write = after_write

    def commit(self, record: IterationTurnRecordMetadata) -> AssistantTurnCommitResult:
        if not isinstance(record.outcome, CompletedResponseOutcome):
            raise IterationTurnConflictError("assistant commit requires a completed response outcome")
        if record.assistant_commit.state != AssistantLedgerCommitState.PENDING:
            raise IterationTurnConflictError("assistant commit requires a pending turn record")

        payload = self._load_and_validate_payload(record)
        latest = self.store.load_latest(
            record.identity.conversation_id,
            record.identity.run_id,
        )
        if latest is not None and latest.assistant_commit.state == AssistantLedgerCommitState.COMMITTED:
            self._validate_same_commit(record, latest)
            committed_payload = self._load_and_validate_payload(latest)
            return AssistantTurnCommitResult(
                record=latest,
                payload=committed_payload["content"],
                replayed=True,
            )

        pending_was_durable = latest is not None and latest.record_id == record.record_id
        if pending_was_durable:
            expected = record.model_copy(update={"integrity_digest": latest.integrity_digest})
            if latest != expected:
                raise IterationTurnConflictError("durable pending assistant record differs from retry")
            durable_pending = latest
        else:
            current_generation = latest.generation if latest is not None else 0
            if record.generation != current_generation + 1:
                raise IterationTurnConflictError(
                    "pending assistant record does not advance the durable generation"
                )
            durable_pending = self.store.save(
                record,
                expected_generation=current_generation,
            )
            self._after_write("pending_record")

        ingress, revision = self.store.load_ingress(record.identity.conversation_id)
        if ingress is None:
            raise IterationTurnConflictError("session ingress is unavailable for assistant commit")
        assistant_turn = SessionTurn(
            identity=record.identity.model_copy(
                update={"turn_index": record.assistant_commit.turn_index}
            ),
            message_id=record.assistant_commit.message_id,
            role="assistant",
            content=payload["content"],
        )
        matching = next(
            (turn for turn in ingress.turns if turn.message_id == assistant_turn.message_id),
            None,
        )
        ingress_was_committed = matching is not None
        if matching is not None:
            if matching != assistant_turn:
                raise IterationTurnConflictError(
                    "assistant message ID is already committed with a different payload"
                )
        else:
            if (
                ingress.identity != record.identity
                or ingress.identity.run_id != assistant_turn.identity.run_id
            ):
                raise IterationTurnConflictError(
                    "session ingress cursor differs from the prepared assistant turn"
                )
            updated = SessionIngress.open_turn(ingress, assistant_turn)
            self.store.save_ingress(updated, expected_revision=revision)
            self._after_write("assistant_ingress")

        committed = durable_pending.model_copy(
            update={
                "record_id": f"{durable_pending.record_id}-assistant-committed",
                "generation": durable_pending.generation + 1,
                "boundary": IterationBoundary.TURN_RESPONSE_DURABLE,
                "assistant_commit": durable_pending.assistant_commit.model_copy(
                    update={"state": AssistantLedgerCommitState.COMMITTED}
                ),
                "integrity_digest": "",
            }
        )
        committed = IterationTurnRecordMetadata.model_validate(
            committed.model_dump(mode="python")
        )
        try:
            saved = self.store.save(
                committed,
                expected_generation=durable_pending.generation,
            )
        except IterationTurnConflictError:
            winner = self.store.load_latest(
                record.identity.conversation_id,
                record.identity.run_id,
            )
            if winner is None or winner.assistant_commit.state != AssistantLedgerCommitState.COMMITTED:
                raise
            self._validate_same_commit(record, winner)
            return AssistantTurnCommitResult(
                record=winner,
                payload=payload["content"],
                replayed=True,
            )
        self._after_write("committed_record")
        return AssistantTurnCommitResult(
            record=saved,
            payload=payload["content"],
            replayed=pending_was_durable or ingress_was_committed,
        )

    def _load_and_validate_payload(
        self,
        record: IterationTurnRecordMetadata,
    ) -> dict[str, Any]:
        commit = record.assistant_commit
        if commit.payload_ref is None:
            raise IterationTurnConflictError("assistant payload reference is missing")
        payload = self.store.load_artifact(
            record.identity.conversation_id,
            record.identity.run_id,
            commit.payload_ref,
        )
        if payload is None:
            raise IterationTurnConflictError("assistant response artifact is unavailable")
        if set(payload) != {"message_id", "turn_index", "content"}:
            raise IterationTurnConflictError("assistant response artifact envelope is invalid")
        if payload["message_id"] != commit.message_id:
            raise IterationTurnConflictError("assistant artifact message identity mismatch")
        if payload["turn_index"] != commit.turn_index:
            raise IterationTurnConflictError("assistant artifact turn identity mismatch")
        if not isinstance(payload["content"], str):
            raise IterationTurnConflictError("assistant artifact content is invalid")
        if assistant_payload_hash(payload) != commit.payload_hash:
            raise IterationTurnConflictError("assistant artifact payload hash mismatch")
        if commit.payload_ref != record.outcome.response_ref:
            raise IterationTurnConflictError("assistant artifact differs from response outcome")
        return payload

    @staticmethod
    def _validate_same_commit(
        pending: IterationTurnRecordMetadata,
        committed: IterationTurnRecordMetadata,
    ) -> None:
        if (
            committed.identity != pending.identity
            or committed.assistant_commit.message_id != pending.assistant_commit.message_id
            or committed.assistant_commit.turn_index != pending.assistant_commit.turn_index
            or committed.assistant_commit.payload_ref != pending.assistant_commit.payload_ref
            or committed.assistant_commit.payload_hash != pending.assistant_commit.payload_hash
        ):
            raise IterationTurnConflictError("committed assistant response differs from retry")

    def _after_write(self, boundary: str) -> None:
        if self.after_write is not None:
            self.after_write(boundary)


__all__ = [
    "AssistantTurnCommitResult",
    "IterationTurnCommitter",
    "assistant_payload_hash",
]
