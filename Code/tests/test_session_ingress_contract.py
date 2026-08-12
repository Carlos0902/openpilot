from __future__ import annotations

import pytest

from memory.session_ingress import (
    SessionIngress,
    SessionIngressState,
    SessionTurn,
    validate_resume_identity,
)
from metadata import ConversationIdentity, SessionProjectScopeTransition
from metadata import SessionConstraintLimits
from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot


def _identity(*, conversation_id: str = "conversation-1", run_id: str = "run-1", turn_index: int = 1) -> ConversationIdentity:
    return ConversationIdentity(
        conversation_id=conversation_id,
        run_id=run_id,
        turn_index=turn_index,
        project_root="/project",
    )


def _state() -> SessionIngressState:
    return SessionIngressState(identity=_identity(turn_index=0))


def test_user_turn_creates_pending_proposals_but_not_active_authority() -> None:
    state = _state()
    turn = SessionTurn(
        identity=_identity(),
        message_id="user-1",
        role="user",
        content="Only calculator.py may be modified. The validation command must be `pytest -q`.",
    )

    updated = SessionIngress.open_turn(state, turn)

    assert updated.identity.conversation_id == "conversation-1"
    assert len(updated.pending_proposals) == 2
    assert updated.session_constraints.active_entries == []
    assert updated.turns[0].message_id == "user-1"


def test_pending_proposal_quota_fails_closed() -> None:
    base = _state()
    state = base.model_copy(
        update={
            "session_constraints": base.session_constraints.model_copy(
                update={"limits": SessionConstraintLimits(max_pending_proposals=1)}
            )
        }
    )
    # One user turn yields two proposals, exceeding the typed pending quota.
    with pytest.raises(ValueError, match="pending session constraint proposals"):
        SessionIngress.open_turn(
            state,
            SessionTurn(
                identity=_identity(),
                message_id="user-1",
                role="user",
                content="Only calculator.py may be modified. The validation command must be `pytest -q`.",
            ),
        )


def test_new_same_key_user_proposal_supersedes_older_pending_proposal() -> None:
    state = SessionIngress.open_turn(
        _state(),
        SessionTurn(
            identity=_identity(),
            message_id="user-1",
            role="user",
            content="Only calculator.py may be modified.",
        ),
    )
    old_id = state.pending_proposals[0].proposal_id
    state = SessionIngress.open_turn(
        state,
        SessionTurn(
            identity=_identity(turn_index=2),
            message_id="user-2",
            role="user",
            content="Only divide.py may be modified.",
        ),
    )

    old = next(item for item in state.pending_proposals if item.proposal_id == old_id)
    new = next(item for item in state.pending_proposals if item.proposal_id != old_id)
    assert old.status == "superseded"
    assert new.supersedes_proposal_id == old_id
    with pytest.raises(ValueError, match="not pending"):
        SessionIngress.confirm_proposal(state, proposal_id=old_id, confirmation_turn=3)

    activated = SessionIngress.confirm_proposal(state, proposal_id=new.proposal_id, confirmation_turn=3)
    assert activated.session_constraints.active_entries[0].value.allowed_files == ["divide.py"]


def test_assistant_turn_cannot_create_or_activate_constraints() -> None:
    state = _state()
    turn = SessionTurn(
        identity=_identity(),
        message_id="assistant-1",
        role="assistant",
        content="Only README.md should be changed.",
    )

    updated = SessionIngress.open_turn(state, turn)

    assert updated.pending_proposals == []
    assert updated.session_constraints.active_entries == []


def test_generated_child_project_scope_transition_preserves_turn_provenance(tmp_path) -> None:
    parent = tmp_path / "workspace"
    child = parent / "generated" / "snake"
    state = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-1",
            turn_index=0,
            project_root=str(parent),
        )
    )
    state = SessionIngress.open_turn(
        state,
        SessionTurn(
            identity=state.identity.model_copy(update={"turn_index": 1}),
            message_id="user-1",
            role="user",
            content="Create a snake project.",
        ),
    )

    scoped = SessionIngress.enter_generated_child_project(state, child)

    assert scoped.identity.project_root == str(child.resolve())
    assert scoped.session_constraints.project_root == str(child.resolve())
    assert scoped.turns[0].identity.project_root == str(parent)
    assert scoped.project_scope_transitions[0].source_project_root == str(parent.resolve())
    assert scoped.project_scope_transitions[0].target_project_root == str(child.resolve())
    assert scoped.project_scope_transitions[0].reason == "generated_child_project"


def test_generated_child_project_scope_transition_rejects_sibling(tmp_path) -> None:
    parent = tmp_path / "workspace"
    sibling = tmp_path / "other-project"
    state = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-1",
            turn_index=0,
            project_root=str(parent),
        )
    )

    with pytest.raises(ValueError, match="generated child project"):
        SessionIngress.enter_generated_child_project(state, sibling)

    with pytest.raises(ValueError, match="generated child"):
        SessionProjectScopeTransition(
            source_project_root=str(parent.resolve()),
            target_project_root=str(sibling.resolve()),
            turn_index=0,
        )


def test_confirmation_activates_only_selected_proposal() -> None:
    state = SessionIngress.open_turn(
        _state(),
        SessionTurn(
            identity=_identity(),
            message_id="user-1",
            role="user",
            content="Only calculator.py may be modified.",
        ),
    )
    proposal_id = state.pending_proposals[0].proposal_id

    confirmed = SessionIngress.confirm_proposal(state, proposal_id=proposal_id, confirmation_turn=2)

    assert len(confirmed.session_constraints.active_entries) == 1
    assert confirmed.pending_proposals[0].status == "confirmed"
    assert confirmed.session_constraints.active_entries[0].value.allowed_files == ["calculator.py"]


def test_reject_and_revoke_do_not_leave_active_constraint() -> None:
    state = SessionIngress.open_turn(
        _state(),
        SessionTurn(
            identity=_identity(),
            message_id="user-1",
            role="user",
            content="Only calculator.py may be modified.",
        ),
    )
    proposal_id = state.pending_proposals[0].proposal_id
    rejected = SessionIngress.reject_proposal(state, proposal_id=proposal_id)
    assert rejected.session_constraints.active_entries == []
    assert rejected.pending_proposals[0].status == "rejected"

    active = SessionIngress.confirm_proposal(state, proposal_id=proposal_id, confirmation_turn=2)
    revoked = SessionIngress.revoke_constraint(active, constraint_key="write_scope", turn_index=3)
    assert revoked.session_constraints.active_entries == []
    assert revoked.session_constraints.revoked_entries[0].constraint_key == "write_scope"


def test_ingress_rejects_cross_conversation_project_or_non_monotonic_turn() -> None:
    with pytest.raises(ValueError, match="conversation|identity"):
        SessionIngress.open_turn(
            _state(),
            SessionTurn(
                identity=_identity(conversation_id="other"),
                message_id="user-1",
                role="user",
                content="Only calculator.py may be modified.",
            ),
        )

    state = _state()
    with pytest.raises(ValueError, match="turn"):
        SessionIngress.open_turn(
            state,
            SessionTurn(
                identity=_identity(turn_index=0),
                message_id="user-duplicate",
                role="user",
                content="Only calculator.py may be modified.",
            ),
        )


def test_resume_identity_is_fail_closed() -> None:
    state = _state()
    validate_resume_identity(state, _identity(turn_index=0))

    with pytest.raises(ValueError, match="run|identity"):
        validate_resume_identity(state, _identity(run_id="other-run"))
    with pytest.raises(ValueError, match="conversation|identity"):
        validate_resume_identity(state, _identity(conversation_id="other"))
    with pytest.raises(ValueError, match="project|identity"):
        validate_resume_identity(
            state,
            ConversationIdentity(
                conversation_id="conversation-1",
                run_id="run-1",
                turn_index=1,
                project_root="/other-project",
            ),
        )


def test_autopilot_keeps_conversation_identity_separate_from_run_identity(tmp_path) -> None:
    class FakeLLM:
        pass

    autopilot = IntelligentAutopilot(FakeLLM(), log_file=tmp_path / "autopilot.jsonl")
    captured: dict[str, object] = {}
    autopilot.runtime_controller.run = lambda goal, context, mode="standard": captured.update(context) or {"success": True}

    ingress = _state()
    autopilot.execute(
        "Inspect calculator",
        context={"session_ingress_state": ingress},
    )

    assert captured["conversation_id"] == "conversation-1"
    assert captured["run_id"] == autopilot.session_id
    assert captured["session_constraints"].session_id == "conversation-1"
    assert autopilot.session_id != autopilot.conversation_id


@pytest.mark.parametrize(
    ("context_update", "message"),
    [
        (
            {"conversation_id": "other-conversation"},
            "conversation identity",
        ),
        (
            {"project_path": "other-project"},
            "project identity",
        ),
    ],
)
def test_autopilot_rejects_raw_ingress_identity_conflicts_without_active_constraints(
    tmp_path,
    context_update: dict[str, str],
    message: str,
) -> None:
    """An empty constraint ledger must not weaken raw ingress ownership checks."""

    class FakeLLM:
        pass

    project_root = str(tmp_path / "project")
    ingress = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-1",
            turn_index=0,
            project_root=project_root,
        )
    )
    autopilot = IntelligentAutopilot(FakeLLM(), log_file=tmp_path / "autopilot.jsonl")
    autopilot.runtime_controller.run = lambda *args, **kwargs: pytest.fail(
        "conflicting ingress identity reached runtime"
    )

    context = {
        "session_ingress_state": ingress,
        "conversation_id": ingress.identity.conversation_id,
        "project_path": ingress.identity.project_root,
        **context_update,
    }

    with pytest.raises(ValueError, match=message):
        autopilot.execute("Inspect calculator", context=context)
