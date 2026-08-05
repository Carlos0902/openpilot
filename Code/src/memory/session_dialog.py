"""Bounded, source-linked projections of conversation-scoped raw turns."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from metadata import (
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    SessionIngressState,
)


def session_turn_ledger_hash(state: SessionIngressState) -> str:
    """Hash the authoritative identity and raw turn ledger, excluding derived views."""
    payload = {
        "identity": state.identity.model_dump(mode="json"),
        "turns": [turn.model_dump(mode="json") for turn in state.turns],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_session_turn_candidates(
    state: SessionIngressState,
    *,
    expected_conversation_id: str | None = None,
    expected_project_root: str | Path | None = None,
    max_turns: int = 6,
) -> tuple[list[ContextCandidate], str]:
    """Create a bounded DIALOG view without granting authority to raw prose."""
    if not isinstance(state, SessionIngressState):
        raise TypeError("session ingress state must be a validated SessionIngressState")
    if max_turns < 0:
        raise ValueError("max_turns must be non-negative")
    if expected_conversation_id and state.identity.conversation_id != str(expected_conversation_id):
        raise ValueError("session dialog conversation identity mismatch")
    if expected_project_root:
        requested_root = Path(expected_project_root).expanduser().resolve(strict=False)
        ingress_root = Path(state.identity.project_root).expanduser().resolve(strict=False)
        if requested_root != ingress_root:
            raise ValueError("session dialog project identity mismatch")

    seen_ids: set[str] = set()
    previous_turn = -1
    for turn in state.turns:
        if turn.message_id in seen_ids:
            raise ValueError("session dialog message IDs must be unique")
        seen_ids.add(turn.message_id)
        if turn.identity.turn_index <= previous_turn:
            raise ValueError("session dialog turn indexes must increase strictly")
        if turn.identity.turn_index > state.identity.turn_index:
            raise ValueError("session dialog turn exceeds ingress cursor")
        if turn.identity.conversation_id != state.identity.conversation_id:
            raise ValueError("session dialog conversation identity mismatch")
        if turn.identity.project_root != state.identity.project_root:
            raise ValueError("session dialog project identity mismatch")
        previous_turn = turn.identity.turn_index

    digest = session_turn_ledger_hash(state)
    selected_turns = state.turns[-max_turns:] if max_turns else []
    candidates: list[ContextCandidate] = []
    for offset, turn in enumerate(selected_turns):
        role = "assistant" if turn.role == "assistant" else "user"
        retention = (
            ContextCandidateRetention.OPTIONAL
            if role == "assistant"
            else ContextCandidateRetention.PREFERRED
        )
        candidates.append(
            ContextCandidate(
                candidate_id=(
                    "session_dialog:"
                    + hashlib.sha256(
                        f"{state.identity.conversation_id}\x1f{turn.message_id}".encode("utf-8")
                    ).hexdigest()[:20]
                ),
                kind=ContextCandidateKind.DIALOG,
                source_id=turn.message_id,
                content=f"{role.upper()}: {turn.content}",
                role=role,
                retention=retention,
                priority=max(1, 70 - (len(selected_turns) - offset - 1)),
                source_order=1_000 + offset,
                truncation=ContextCandidateTruncation.HEAD,
                trust=ContextCandidateTrust.DIRECT,
                freshness=ContextCandidateFreshness.HISTORICAL,
            )
        )
    return candidates, digest


__all__ = ["build_session_turn_candidates", "session_turn_ledger_hash"]
