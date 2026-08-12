"""Conversation-scoped ingress for durable session constraints.

The ingress owns turn identity and pending proposals for one conversation. It
does not write long-term memory and it never treats assistant text or an
unconfirmed proposal as runtime authority.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from metadata import (
    ConversationIdentity,
    SessionConstraintProposalStatus,
    SessionConstraintState,
    SessionIngressState,
    SessionProjectScopeTransition,
    SessionTurn,
)
from memory.session_constraints import (
    activate_constraint_proposal,
    confirm_constraint_proposal,
    extract_constraint_proposals,
    reject_constraint_proposal,
    revoke_constraint,
)


def _validated_state(value: SessionIngressState) -> SessionIngressState:
    return SessionIngressState.model_validate(value.model_dump(mode="python"))


def _constraint_state_with_cursor(state: SessionConstraintState, turn_index: int) -> SessionConstraintState:
    return SessionConstraintState.model_validate(
        state.model_copy(update={"processed_through_turn": max(state.processed_through_turn, turn_index)}).model_dump(
            mode="python"
        )
    )


def validate_resume_identity(state: SessionIngressState, identity: ConversationIdentity) -> None:
    """Fail closed unless a checkpoint belongs to this conversation/run/root."""

    if state.identity.conversation_id != identity.conversation_id:
        raise ValueError("conversation identity mismatch during resume")
    if state.identity.run_id != identity.run_id:
        raise ValueError("run identity mismatch during resume")
    if state.identity.project_root != identity.project_root:
        raise ValueError("project identity mismatch during resume")
    if identity.turn_index > state.identity.turn_index:
        raise ValueError("resume turn exceeds the ingress cursor")


class SessionIngress:
    """Pure state transitions for the production conversation owner."""

    @staticmethod
    def open_turn(state: SessionIngressState, turn: SessionTurn) -> SessionIngressState:
        if turn.identity.conversation_id != state.identity.conversation_id:
            raise ValueError("conversation identity mismatch")
        if turn.identity.project_root != state.identity.project_root:
            raise ValueError("project identity mismatch")
        if turn.identity.turn_index <= state.identity.turn_index:
            raise ValueError("turn index must advance monotonically")

        proposals = list(state.pending_proposals)
        if turn.role == "user":
            raw_message = {
                "message_id": turn.message_id,
                "turn_index": turn.identity.turn_index,
                "role": turn.role,
                "content": turn.content,
            }
            fresh_proposals = extract_constraint_proposals(
                [raw_message],
                session_id=state.identity.conversation_id,
            )
            # A newer explicit proposal supersedes only older *pending*
            # proposals for the same key. Confirmed/rejected history remains
            # evidence, while stale pending intent can never be revived.
            for fresh in fresh_proposals:
                previous = [
                    item
                    for item in proposals
                    if item.constraint_key == fresh.constraint_key
                    and item.status == SessionConstraintProposalStatus.PROPOSED
                    and item.source_turn_index < fresh.source_turn_index
                ]
                if previous:
                    latest = max(previous, key=lambda item: item.source_turn_index)
                    fresh = fresh.model_copy(update={"supersedes_proposal_id": latest.proposal_id})
                    proposals = [
                        item.model_copy(update={"status": SessionConstraintProposalStatus.SUPERSEDED})
                        if item in previous
                        else item
                        for item in proposals
                    ]
                proposals.append(fresh)
        constraints = _constraint_state_with_cursor(state.session_constraints, turn.identity.turn_index)
        updated = state.model_copy(
            update={
                "identity": turn.identity,
                "turns": [*state.turns, turn],
                "pending_proposals": proposals,
                "session_constraints": constraints,
            }
        )
        return _validated_state(updated)

    @staticmethod
    def enter_generated_child_project(
        state: SessionIngressState,
        project_root: str | Path,
    ) -> SessionIngressState:
        """Scope a session to one generated descendant without admitting siblings."""

        source_root = Path(state.identity.project_root).expanduser().resolve(strict=False)
        target_root = Path(project_root).expanduser().resolve(strict=False)
        if target_root == source_root:
            return state
        if not target_root.is_relative_to(source_root):
            raise ValueError("generated child project must remain inside the active session project root")
        transition = SessionProjectScopeTransition(
            source_project_root=state.identity.project_root,
            target_project_root=str(target_root),
            turn_index=state.identity.turn_index,
        )
        identity = state.identity.model_copy(update={"project_root": str(target_root)})
        constraints = state.session_constraints.model_copy(update={"project_root": str(target_root)})
        updated = state.model_copy(
            update={
                "identity": identity,
                "session_constraints": constraints,
                "project_scope_transitions": [*state.project_scope_transitions, transition],
            }
        )
        return _validated_state(updated)

    @staticmethod
    def confirm_proposal(
        state: SessionIngressState,
        *,
        proposal_id: str,
        confirmation_turn: int,
    ) -> SessionIngressState:
        proposal = next(
            (item for item in state.pending_proposals if item.proposal_id == proposal_id),
            None,
        )
        if proposal is None:
            raise ValueError("unknown session constraint proposal")
        if proposal.status != SessionConstraintProposalStatus.PROPOSED:
            raise ValueError("session constraint proposal is not pending")
        confirmed = confirm_constraint_proposal(proposal)
        constraints = activate_constraint_proposal(
            state.session_constraints,
            confirmed,
            confirmation_turn=confirmation_turn,
        )
        pending = [
            item.model_copy(update={"status": confirmed.status}) if item.proposal_id == proposal_id else item
            for item in state.pending_proposals
        ]
        updated = state.model_copy(
            update={
                "pending_proposals": pending,
                "session_constraints": constraints,
            }
        )
        return _validated_state(updated)

    @staticmethod
    def reject_proposal(state: SessionIngressState, *, proposal_id: str) -> SessionIngressState:
        proposal = next(
            (item for item in state.pending_proposals if item.proposal_id == proposal_id),
            None,
        )
        if proposal is None:
            raise ValueError("unknown session constraint proposal")
        rejected = reject_constraint_proposal(state.session_constraints, proposal)
        pending = [
            item.model_copy(update={"status": SessionConstraintProposalStatus.REJECTED})
            if item.proposal_id == proposal_id
            else item
            for item in state.pending_proposals
        ]
        return _validated_state(state.model_copy(update={"pending_proposals": pending, "session_constraints": rejected}))

    @staticmethod
    def revoke_constraint(
        state: SessionIngressState,
        *,
        constraint_key: str,
        turn_index: int,
    ) -> SessionIngressState:
        constraints = revoke_constraint(state.session_constraints, constraint_key, turn_index=turn_index)
        return _validated_state(state.model_copy(update={"session_constraints": constraints}))


__all__ = [
    "SessionIngress",
    "SessionIngressState",
    "SessionTurn",
    "validate_resume_identity",
]
