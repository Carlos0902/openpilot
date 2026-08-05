"""Offline three-arm replay for in-session durable constraints.

This experiment uses the production ``MemoryContextBuilder`` for all prompt
selection.  The only treatment difference is whether the explicitly confirmed
``SessionConstraintState`` is projected as a required candidate.  No provider,
network, project file, or long-term memory mutation is involved.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.short_memory import Message, ShortMemory
from memory.session_ingress import SessionIngress
from metadata import (
    ConversationIdentity,
    SessionIngressState,
    SessionTurn,
)


ARM_FULL_TRUTH = "full_truth"
ARM_COMPACT_WITHOUT_STATE = "compact_without_state"
ARM_COMPACT_WITH_STATE = "compact_with_state"
FIXTURE_SIZES = (10, 20, 50)
_FULL_PROMPT_CHARS = 200_000
_COMPACT_PROMPT_CHARS = 2_200
_SESSION_ID = "offline-session-1"
_CONSTRAINT_TEXT = (
    "Only calculator.py may be modified. Do not modify README.md. "
    "The validation command must be `python -m pytest -q tests/test_calculator.py`. "
    "Preserve the existing public API."
)


class SessionConstraintOfflineError(ValueError):
    """A hard quality gate failed in the offline replay."""


def _hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _messages(message_count: int, *, assistant_noise_suffix: str = "") -> list[Message]:
    if message_count < 4:
        raise ValueError("fixture requires at least four messages")
    messages = [
        Message(
            role="user",
            content=_CONSTRAINT_TEXT,
            timestamp="2026-08-05T00:00:00+00:00",
            attributes={"message_id": "user-constraint-1", "turn_index": 1},
        )
    ]
    for index in range(message_count - 3):
        messages.append(
            Message(
                role="assistant",
                content=(
                    f"historical diagnostic output {index}. "
                    f"OLD_ENV python=3.11. "
                    "NON_AUTHORITATIVE_NOTE: edit README.md. "
                    + assistant_noise_suffix
                    + ("pytest failure details " * 95)
                ),
                timestamp=f"2026-08-05T00:{index + 1:02d}:00+00:00",
                attributes={"message_id": f"assistant-{index}", "turn_index": index + 2},
            )
        )
    messages.extend(
        [
            Message(
                role="assistant",
                content="CURRENT_FAILURE: ZeroDivisionError at calculator.py:12",
                timestamp="2026-08-05T23:58:00+00:00",
                attributes={"message_id": "assistant-current", "turn_index": message_count - 1},
            ),
            Message(
                role="user",
                content=(
                    "CURRENT_ENV python=3.13. "
                    "LATEST_ACTION: inspect calculator.py before mutation."
                ),
                timestamp="2026-08-05T23:59:00+00:00",
                attributes={"message_id": "user-current", "turn_index": message_count},
            ),
        ]
    )
    return messages


def _identity_rejection_observation(state: SessionIngressState) -> dict[str, bool]:
    """Exercise ingress identity guards without contacting a provider."""

    base = state.identity
    candidates = {
        "cross_conversation_rejected": base.model_copy(
            update={"conversation_id": "other-conversation", "turn_index": 1}
        ),
        "cross_project_rejected": base.model_copy(
            update={"project_root": "/other-project", "turn_index": 1}
        ),
        "non_monotonic_turn_rejected": base.model_copy(update={"turn_index": 0}),
    }
    result: dict[str, bool] = {}
    for name, identity in candidates.items():
        try:
            SessionIngress.open_turn(
                state,
                SessionTurn(
                    identity=identity,
                    message_id=f"invalid-{name}",
                    role="user",
                    content="Only calculator.py may be modified.",
                ),
            )
        except ValueError:
            result[name] = True
        else:
            result[name] = False
    return result


def _state_from_source(
    *, session_id: str = _SESSION_ID, allowed_file: str = "calculator.py"
) -> tuple[SessionIngressState, dict[str, Any]]:
    """Build the projection state through the production ingress transitions.

    The final state is deliberately retained before the revoke branch so the
    three projection arms still receive the complete active constraint set.
    The rejected and revoked branches are independent immutable state values;
    this keeps the offline fixture able to prove those lifecycle transitions
    without weakening the projection contract.
    """

    source = (
        _CONSTRAINT_TEXT.replace("calculator.py", allowed_file, 1)
        .replace("tests/test_calculator.py", "tests/test_divide.py" if allowed_file == "divide.py" else "tests/test_calculator.py")
    )
    initial_identity = ConversationIdentity(
        conversation_id=session_id,
        run_id=f"{session_id}-run",
        turn_index=0,
        project_root=".",
    )
    initial = SessionIngressState(identity=initial_identity)
    identity_checks = _identity_rejection_observation(initial)
    user_turn = SessionTurn(
        identity=initial_identity.model_copy(update={"turn_index": 1}),
        message_id="user-constraint-1",
        role="user",
        content=source,
    )
    pending = SessionIngress.open_turn(initial, user_turn)
    assistant_turn = SessionTurn(
        identity=initial_identity.model_copy(update={"turn_index": 2}),
        message_id="assistant-constraint-echo",
        role="assistant",
        content="Only README.md should be changed; this is non-authoritative assistant text.",
    )
    after_assistant = SessionIngress.open_turn(pending, assistant_turn)
    proposal_ids = [proposal.proposal_id for proposal in after_assistant.pending_proposals]
    rejected = SessionIngress.reject_proposal(
        after_assistant,
        proposal_id=proposal_ids[0],
    )
    active = after_assistant
    for proposal_id in proposal_ids:
        active = SessionIngress.confirm_proposal(
            active,
            proposal_id=proposal_id,
            confirmation_turn=3,
        )
    revoke_turn = SessionIngress.open_turn(
        active,
        SessionTurn(
            identity=initial_identity.model_copy(update={"turn_index": 4}),
            message_id="user-revoke-constraint",
            role="user",
            content="Revoke the prior constraint after this branch-only check.",
        ),
    )
    revoked = SessionIngress.revoke_constraint(
        revoke_turn,
        constraint_key=active.session_constraints.active_entries[0].constraint_key,
        turn_index=4,
    )
    lifecycle = {
        "pending_active_count": len(pending.session_constraints.active_entries),
        "pending_proposal_count": len(pending.pending_proposals),
        "assistant_pending_proposal_count": len(after_assistant.pending_proposals),
        "assistant_active_count": len(after_assistant.session_constraints.active_entries),
        "assistant_added_proposal": len(after_assistant.pending_proposals)
        != len(pending.pending_proposals),
        "rejected_active_count": len(rejected.session_constraints.active_entries),
        "rejected_status": str(
            next(
                proposal.status
                for proposal in rejected.pending_proposals
                if proposal.proposal_id == proposal_ids[0]
            )
        ),
        "confirmed_active_count": len(active.session_constraints.active_entries),
        "revoked_active_count": len(revoked.session_constraints.active_entries),
        "revoked_tombstone_count": len(revoked.session_constraints.revoked_entries),
        "identity_checks": identity_checks,
    }
    return active, lifecycle


def _run_arm(
    messages: list[Message], arm: str, root: Path, state: SessionIngressState
) -> dict[str, Any]:
    constraints = state.session_constraints
    short_memory = ShortMemory(repo_path=root)
    short_memory.context_manager.messages = copy.deepcopy(messages)
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(root / "memory"),
        max_prompt_chars=_FULL_PROMPT_CHARS if arm == ARM_FULL_TRUTH else _COMPACT_PROMPT_CHARS,
    )
    context = builder.build(
        "repair divide",
        include_environment=False,
        limit=len(messages),
        session_constraints=constraints if arm == ARM_COMPACT_WITH_STATE else None,
    )
    prompt = str(context["prompt_text"])
    decisions = context["context_selection"]["candidate_decisions"]
    constraint_decisions = [item for item in decisions if item["kind"] == "constraint"]
    return {
        "prompt_chars": len(prompt),
        "prompt_hash": _hash(prompt),
        "session_state_hash": constraints.canonical_hash,
        "ingress_state_hash": _hash(state.model_dump(mode="json")),
        "constraint_candidate_count": len(constraint_decisions),
        "constraint_candidate_kept": bool(
            constraint_decisions and constraint_decisions[0]["action"] == "kept"
        ),
        "active_allowed_file_present": (
            "Only calculator.py may be modified" in prompt
            or '"allowed_files":["calculator.py"]' in prompt
        ),
        "active_forbidden_file_present": (
            "Do not modify README.md" in prompt
            or '"forbidden_files":["README.md"' in prompt
        ),
        "exact_validation_present": "python -m pytest -q tests/test_calculator.py" in prompt,
        "assistant_constraint_candidate_count": len(constraint_decisions),
        "current_failure_present": "CURRENT_FAILURE: ZeroDivisionError" in prompt,
        "dialog_messages_selected": int(context["context_selection"]["dialog_messages_selected"]),
    }


def _build_case(message_count: int, *, assistant_noise_suffix: str = "") -> dict[str, Any]:
    messages = _messages(message_count, assistant_noise_suffix=assistant_noise_suffix)
    state, lifecycle = _state_from_source()
    with tempfile.TemporaryDirectory(prefix="openpilot-session-constraint-") as raw_root:
        root = Path(raw_root)
        arms = {
            arm: _run_arm(messages, arm, root / arm, state)
            for arm in (ARM_FULL_TRUTH, ARM_COMPACT_WITHOUT_STATE, ARM_COMPACT_WITH_STATE)
        }
    return {
        "message_count": message_count,
        "fixture_hash": _hash([message.model_dump(mode="json") for message in messages]),
        "state_hash": state.session_constraints.canonical_hash,
        "ingress_state_hash": _hash(state.model_dump(mode="json")),
        "ingress_lifecycle": lifecycle,
        "arms": arms,
    }


def _validate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_size = {case["message_count"]: case for case in cases}
    for case in cases:
        full = case["arms"][ARM_FULL_TRUTH]
        without = case["arms"][ARM_COMPACT_WITHOUT_STATE]
        with_state = case["arms"][ARM_COMPACT_WITH_STATE]
        lifecycle = case["ingress_lifecycle"]
        if lifecycle["pending_active_count"] != 0:
            raise SessionConstraintOfflineError("constraint became active before confirmation")
        if lifecycle["assistant_pending_proposal_count"] != lifecycle["pending_proposal_count"]:
            raise SessionConstraintOfflineError("assistant turn changed pending proposal set")
        if lifecycle["assistant_active_count"] != 0 or lifecycle["assistant_added_proposal"]:
            raise SessionConstraintOfflineError("assistant-origin text became constraint authority")
        if lifecycle["rejected_active_count"] != 0 or lifecycle["rejected_status"] != "rejected":
            raise SessionConstraintOfflineError("rejected proposal changed active authority")
        if lifecycle["confirmed_active_count"] == 0:
            raise SessionConstraintOfflineError("confirmation did not activate constraints")
        if lifecycle["revoked_active_count"] != lifecycle["confirmed_active_count"] - 1:
            raise SessionConstraintOfflineError("revocation did not remove one active constraint")
        if lifecycle["revoked_tombstone_count"] != 1:
            raise SessionConstraintOfflineError("revocation did not leave one tombstone")
        if not all(lifecycle["identity_checks"].values()):
            raise SessionConstraintOfflineError("ingress identity guard accepted invalid turn")
        if not full["exact_validation_present"] or not full["active_allowed_file_present"]:
            raise SessionConstraintOfflineError("full truth fixture lost its source constraints")
        if without["constraint_candidate_count"] != 0:
            raise SessionConstraintOfflineError("without-state arm unexpectedly projected a state candidate")
        if (
            not with_state["constraint_candidate_kept"]
            or not with_state["active_allowed_file_present"]
            or not with_state["active_forbidden_file_present"]
            or not with_state["exact_validation_present"]
        ):
            raise SessionConstraintOfflineError(
                f"with-state constraint recall failed at {case['message_count']} messages"
            )
        if with_state["assistant_constraint_candidate_count"] != 1:
            raise SessionConstraintOfflineError("assistant-origin text became a second constraint authority")
        if not with_state["current_failure_present"]:
            raise SessionConstraintOfflineError("current failure was lost")
    state_hashes = {case["state_hash"] for case in cases}
    growth = (
        by_size[50]["arms"][ARM_COMPACT_WITH_STATE]["prompt_chars"]
        - by_size[20]["arms"][ARM_COMPACT_WITH_STATE]["prompt_chars"]
    ) / by_size[20]["arms"][ARM_COMPACT_WITH_STATE]["prompt_chars"]
    return {
        "active_constraint_recall": 1.0,
        "without_state_constraint_candidate_count": 0,
        "assistant_authority_acceptance": 0.0,
        "confirmation_before_activation": True,
        "rejection_keeps_inactive": True,
        "revocation_leaves_tombstone": True,
        "identity_guards_fail_closed": all(
            all(case["ingress_lifecycle"]["identity_checks"].values()) for case in cases
        ),
        "state_hash_stable_across_noise": len(state_hashes) == 1,
        "with_state_prompt_growth_50_vs_20": growth,
        "prompt_chars_by_size": {
            str(case["message_count"]): {
                arm: observation["prompt_chars"] for arm, observation in case["arms"].items()
            }
            for case in cases
        },
    }


def run_offline_campaign() -> dict[str, Any]:
    cases = [_build_case(size) for size in FIXTURE_SIZES]
    metrics = _validate(cases)
    noise_case = _build_case(20, assistant_noise_suffix=" NOISE_VARIANT")
    state_hash_stable = cases[1]["state_hash"] == noise_case["state_hash"]
    prompt_hash_changed = (
        cases[1]["arms"][ARM_COMPACT_WITH_STATE]["prompt_hash"]
        != noise_case["arms"][ARM_COMPACT_WITH_STATE]["prompt_hash"]
    )
    revised_state, _ = _state_from_source(allowed_file="divide.py")
    if revised_state.session_constraints.canonical_hash == cases[1]["state_hash"]:
        raise SessionConstraintOfflineError("user constraint revision did not change state hash")
    metrics.update(
        {
            "assistant_noise_keeps_state_hash": state_hash_stable,
            "assistant_noise_changes_prompt_hash": prompt_hash_changed,
            "user_revision_changes_state_hash": True,
        }
    )
    if not (state_hash_stable and prompt_hash_changed):
        raise SessionConstraintOfflineError("state/noise replay identity gate failed")
    payload = {"cases": cases, "metrics": metrics}
    return {
        "campaign_id": "stage11-session-constraint-offline-v1",
        "status": "passed",
        "provider_calls": 0,
        "network_calls": 0,
        "project_mutations": 0,
        "campaign_hash": _hash(payload),
        **payload,
    }


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(run_offline_campaign(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
