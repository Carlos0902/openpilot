from __future__ import annotations

import pytest

from metadata import (
    RuntimeExecutionMode,
    SessionConstraintAuthority,
    SessionConstraintCategory,
    SessionConstraintEntry,
    SessionConstraintProposal,
    SessionConstraintProposalStatus,
    SessionConstraintSourceKind,
    SessionConstraintState,
    SessionConstraintValue,
    RuntimeStateMetadata,
)


_SOURCE_HASH = "sha256:" + "a" * 64


def _value(category: SessionConstraintCategory) -> SessionConstraintValue:
    if category == SessionConstraintCategory.WRITE_SCOPE:
        return SessionConstraintValue(allowed_files=["calculator.py"], forbidden_files=["README.md"])
    if category == SessionConstraintCategory.VALIDATION_COMMAND:
        return SessionConstraintValue(validation_commands=["python -m pytest -q"])
    if category == SessionConstraintCategory.API_COMPATIBILITY:
        return SessionConstraintValue(api_compatibility="preserve_existing_api")
    if category == SessionConstraintCategory.GOAL_ACCEPTANCE:
        return SessionConstraintValue(acceptance_criteria=["Keep the public divide API unchanged."])
    return SessionConstraintValue(execution_mode=RuntimeExecutionMode.READ_ONLY)


def _entry(
    *,
    constraint_id: str = "constraint-1",
    constraint_key: str = "write_scope",
    category: SessionConstraintCategory = SessionConstraintCategory.WRITE_SCOPE,
    status: str = "active",
    supersedes_constraint_id: str | None = None,
) -> SessionConstraintEntry:
    return SessionConstraintEntry(
        constraint_id=constraint_id,
        constraint_key=constraint_key,
        category=category,
        value=_value(category),
        status=status,
        statement="Only the explicitly named project files may be changed.",
        source_kind=SessionConstraintSourceKind.USER_MESSAGE,
        source_id="dialog:user-1",
        source_turn_index=1,
        source_hash=_SOURCE_HASH,
        authority=SessionConstraintAuthority.EXPLICIT_USER,
        supersedes_constraint_id=supersedes_constraint_id,
        revoked_at_turn=2 if status == "revoked" else None,
    )


def test_session_constraint_values_are_typed_and_round_trip() -> None:
    state = SessionConstraintState(
        session_id="session-1",
        revision=2,
        processed_through_turn=3,
        entries=[_entry()],
    )

    restored = SessionConstraintState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert restored.active_entries[0].value.allowed_files == ["calculator.py"]
    assert restored.canonical_hash.startswith("sha256:")


def test_session_constraint_proposal_is_not_active_authority() -> None:
    proposal = SessionConstraintProposal(
        proposal_id="proposal-1",
        session_id="session-1",
        constraint_key="validation_command",
        category=SessionConstraintCategory.VALIDATION_COMMAND,
        value=_value(SessionConstraintCategory.VALIDATION_COMMAND),
        statement="Run the exact pytest command before completion.",
        source_kind=SessionConstraintSourceKind.USER_MESSAGE,
        source_id="dialog:user-2",
        source_turn_index=2,
        source_hash=_SOURCE_HASH,
        status=SessionConstraintProposalStatus.PROPOSED,
    )

    assert proposal.status == SessionConstraintProposalStatus.PROPOSED
    assert proposal.controls_runtime is False


def test_confirmed_proposal_still_requires_reducer_projection() -> None:
    proposal = SessionConstraintProposal(
        proposal_id="proposal-2",
        session_id="session-1",
        constraint_key="api_compatibility",
        category=SessionConstraintCategory.API_COMPATIBILITY,
        value=_value(SessionConstraintCategory.API_COMPATIBILITY),
        statement="Do not change the existing public API.",
        source_kind=SessionConstraintSourceKind.USER_CONFIRMATION,
        source_id="dialog:user-3",
        source_turn_index=3,
        source_hash=_SOURCE_HASH,
        status=SessionConstraintProposalStatus.CONFIRMED,
    )

    assert proposal.controls_runtime is False


def test_active_entry_requires_source_and_rejects_self_supersession() -> None:
    payload = _entry().model_dump(mode="python")
    payload["source_hash"] = "not-a-hash"
    with pytest.raises(ValueError, match="source_hash"):
        SessionConstraintEntry(**payload)

    with pytest.raises(ValueError, match="supersede itself"):
        _entry(supersedes_constraint_id="constraint-1")


def test_constraint_value_category_must_match_exactly() -> None:
    payload = _entry(category=SessionConstraintCategory.WRITE_SCOPE).model_dump(mode="python")
    payload["category"] = SessionConstraintCategory.VALIDATION_COMMAND
    with pytest.raises(ValueError, match="category"):
        SessionConstraintEntry(**payload)


def test_state_rejects_duplicate_keys_and_preserves_revoked_tombstone() -> None:
    with pytest.raises(ValueError, match="constraint key"):
        SessionConstraintState(
            session_id="session-1",
            revision=2,
            processed_through_turn=2,
            entries=[
                _entry(constraint_id="constraint-1"),
                _entry(constraint_id="constraint-2"),
            ],
        )

    revoked = _entry(
        constraint_id="constraint-2",
        constraint_key="validation_command",
        category=SessionConstraintCategory.VALIDATION_COMMAND,
        status="revoked",
    )
    state = SessionConstraintState(
        session_id="session-1",
        revision=3,
        processed_through_turn=4,
        entries=[_entry(), revoked],
    )

    assert [item.constraint_id for item in state.active_entries] == ["constraint-1"]
    assert [item.constraint_id for item in state.revoked_entries] == ["constraint-2"]
    assert state.revoked_entries[0].revoked_at_turn == 2


def test_revoked_entry_cannot_be_rendered_as_active_constraint() -> None:
    revoked = _entry(
        constraint_id="constraint-3",
        status="revoked",
        constraint_key="write_scope",
    )
    assert revoked.is_active is False
    assert revoked.required_context_text() == ""


def test_runtime_state_owns_session_constraints_and_legacy_payload_defaults_empty() -> None:
    state = RuntimeStateMetadata(
        goal="Repair calculator",
        session_constraints=SessionConstraintState(
            session_id="session-1",
            revision=1,
            processed_through_turn=1,
            entries=[_entry()],
        ),
    )
    restored = RuntimeStateMetadata.model_validate_json(state.model_dump_json())
    assert restored.session_constraints == state.session_constraints

    legacy = RuntimeStateMetadata.model_validate({"goal": "Inspect project"})
    assert legacy.session_constraints.entries == []
