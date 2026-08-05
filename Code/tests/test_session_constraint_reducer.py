from __future__ import annotations

import pytest

from memory.session_constraints import (
    activate_constraint_proposal,
    confirm_constraint_proposal,
    extract_constraint_proposals,
    reject_constraint_proposal,
    revoke_constraint,
)
from metadata import (
    SessionConstraintProposalStatus,
    SessionConstraintState,
)


def _message(message_id: str, turn: int, role: str, content: str) -> dict[str, object]:
    return {
        "message_id": message_id,
        "turn_index": turn,
        "role": role,
        "content": content,
    }


def test_extractor_accepts_only_explicit_user_constraints() -> None:
    messages = [
        _message(
            "user-1",
            1,
            "user",
            "Only calculator.py may be modified. Do not modify README.md. "
            "The validation command must be `python -m pytest -q`. "
            "Preserve the existing public API.",
        ),
        _message("assistant-2", 2, "assistant", "Only README.md should be changed."),
        _message("user-3", 3, "user", "Please inspect the failure."),
    ]

    proposals = extract_constraint_proposals(messages, session_id="session-1")

    assert {proposal.constraint_key for proposal in proposals} == {
        "write_scope",
        "validation_command",
        "api_compatibility",
    }
    assert all(proposal.status == SessionConstraintProposalStatus.PROPOSED for proposal in proposals)
    assert all(proposal.source_id == "user-1" for proposal in proposals)
    assert all(proposal.controls_runtime is False for proposal in proposals)


def test_extractor_ignores_vague_or_unstable_sentences_and_missing_source_ids() -> None:
    messages = [
        _message("user-1", 1, "user", "Please be careful with the project."),
        _message("assistant-2", 2, "assistant", "Do not edit README.md."),
        {"turn_index": 3, "role": "user", "content": "Only calculator.py may be modified."},
    ]

    assert extract_constraint_proposals(messages, session_id="session-1") == []


def test_rejected_or_unconfirmed_proposals_do_not_change_state() -> None:
    proposal = extract_constraint_proposals(
        [_message("user-1", 1, "user", "Only calculator.py may be modified.")],
        session_id="session-1",
    )[0]
    state = SessionConstraintState(session_id="session-1")

    unchanged = reject_constraint_proposal(state, proposal)

    assert unchanged == state
    assert unchanged.entries == []


def test_explicit_activation_creates_active_entry_and_updates_revision() -> None:
    proposal = extract_constraint_proposals(
        [_message("user-1", 1, "user", "Only calculator.py may be modified.")],
        session_id="session-1",
    )[0]
    state = SessionConstraintState(session_id="session-1")

    confirmed = confirm_constraint_proposal(proposal)
    active = activate_constraint_proposal(state, confirmed, confirmation_turn=2)

    assert active.revision == 1
    assert active.processed_through_turn == 2
    assert active.active_entries[0].value.allowed_files == ["calculator.py"]


def test_same_key_activation_supersedes_previous_entry_without_reviving_old_value() -> None:
    proposals = extract_constraint_proposals(
        [
            _message("user-1", 1, "user", "Only calculator.py may be modified."),
            _message("user-2", 2, "user", "Only divide.py may be modified."),
        ],
        session_id="session-1",
    )
    state = SessionConstraintState(session_id="session-1")
    first = activate_constraint_proposal(
        state, confirm_constraint_proposal(proposals[0]), confirmation_turn=1
    )
    second = activate_constraint_proposal(
        first, confirm_constraint_proposal(proposals[1]), confirmation_turn=2
    )

    assert len(second.entries) == 1
    assert second.active_entries[0].value.allowed_files == ["divide.py"]
    assert second.active_entries[0].supersedes_constraint_id == first.entries[0].constraint_id


def test_revoke_keeps_tombstone_and_rejects_unknown_or_wrong_session() -> None:
    proposal = extract_constraint_proposals(
        [_message("user-1", 1, "user", "The validation command must be `python -m pytest -q`.")],
        session_id="session-1",
    )[0]
    state = activate_constraint_proposal(
        SessionConstraintState(session_id="session-1"),
        confirm_constraint_proposal(proposal),
        confirmation_turn=1,
    )
    revoked = revoke_constraint(state, "validation_command", turn_index=2)

    assert revoked.active_entries == []
    assert len(revoked.revoked_entries) == 1
    assert revoked.revoked_entries[0].revoked_at_turn == 2

    with pytest.raises(ValueError, match="unknown constraint key"):
        revoke_constraint(revoked, "missing", turn_index=3)
    with pytest.raises(ValueError, match="session"):
        activate_constraint_proposal(
            SessionConstraintState(session_id="session-2"),
            confirm_constraint_proposal(proposal),
            confirmation_turn=2,
        )
