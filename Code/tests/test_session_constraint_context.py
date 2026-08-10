from __future__ import annotations

import pytest

from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.short_memory import ShortMemory
from core.exceptions import ContextAssemblyBudgetError
from memory.session_constraints import (
    activate_constraint_proposal,
    build_session_constraint_candidate,
    confirm_constraint_proposal,
    extract_constraint_proposals,
    revoke_constraint,
)
from metadata import (
    ContextCandidateRetention,
    ContextCandidateTruncation,
    ConversationIdentity,
    SessionConstraintState,
    SessionIngressState,
    SessionTurn,
)


def _message(message_id: str, turn: int, content: str) -> dict[str, object]:
    return {
        "message_id": message_id,
        "turn_index": turn,
        "role": "user",
        "content": content,
    }


def _state() -> SessionConstraintState:
    proposals = extract_constraint_proposals(
        [
            _message(
                "user-1",
                1,
                "Only calculator.py may be modified. "
                "The validation command must be `python -m pytest -q`.",
            )
        ],
        session_id="session-1",
    )
    state = SessionConstraintState(session_id="session-1")
    for proposal in proposals:
        state = activate_constraint_proposal(
            state,
            confirm_constraint_proposal(proposal),
            confirmation_turn=1,
        )
    return state


def _ingress_state() -> SessionIngressState:
    identity = ConversationIdentity(
        conversation_id="session-1",
        run_id="run-1",
        turn_index=2,
        project_root="/project",
    )
    return SessionIngressState(
        identity=identity,
        turns=[
            SessionTurn(
                identity=identity.model_copy(update={"turn_index": 1}),
                message_id="user-raw-1",
                role="user",
                content="Inspect the raw dialog source.",
            ),
            SessionTurn(
                identity=identity,
                message_id="assistant-raw-2",
                role="assistant",
                content="I inspected the raw dialog source.",
            ),
        ],
        session_constraints=_state(),
    )


def test_active_session_constraints_project_once_as_required_nontruncatable_candidate() -> None:
    state = _state()

    candidate = build_session_constraint_candidate(state)

    assert candidate is not None
    assert candidate.retention == ContextCandidateRetention.REQUIRED
    assert candidate.truncation == ContextCandidateTruncation.FORBIDDEN
    assert candidate.source_id == state.authority_hash
    assert candidate.compacted_candidate_ids == []
    assert "calculator.py" in candidate.content
    assert "python -m pytest -q" in candidate.content


def test_model_facing_constraint_candidate_is_stable_across_ingress_cursor_noise() -> None:
    state = _state()
    advanced = state.model_copy(update={"processed_through_turn": 50})

    original = build_session_constraint_candidate(state)
    after_noise = build_session_constraint_candidate(advanced)

    assert original is not None and after_noise is not None
    assert state.canonical_hash != advanced.canonical_hash
    assert state.authority_hash == advanced.authority_hash
    assert after_noise.candidate_id == original.candidate_id
    assert after_noise.source_id == original.source_id
    assert after_noise.content == original.content


def test_memory_context_keeps_active_constraints_when_old_dialog_is_omitted() -> None:
    builder = MemoryContextBuilder(max_prompt_chars=1_200)
    builder.short_memory.add_message(
        "user",
        "Only calculator.py may be modified. The validation command must be `python -m pytest -q`.",
    )
    for index in range(8):
        builder.short_memory.add_message(
            "assistant",
            f"Large diagnostic output {index}: " + ("failure details " * 80),
        )

    context = builder.build(
        "repair divide",
        include_environment=False,
        session_constraints=_state(),
    )

    assert "calculator.py" in context["prompt_text"]
    assert "python -m pytest -q" in context["prompt_text"]
    decisions = context["context_selection"]["candidate_decisions"]
    constraint_decisions = [item for item in decisions if item["kind"] == "constraint"]
    assert len(constraint_decisions) == 1
    assert constraint_decisions[0]["action"] == "kept"


def test_session_constraint_state_changes_request_hash_and_replay_identity() -> None:
    builder = MemoryContextBuilder(max_prompt_chars=2_000)
    without_state = builder.build("inspect", include_environment=False)
    with_state = builder.build(
        "inspect",
        include_environment=False,
        session_constraints=_state(),
    )

    assert without_state["context_request_hash"] != with_state["context_request_hash"]


def test_memory_context_projects_ingress_turns_as_source_linked_dialog_without_memory_writes(
    tmp_path,
) -> None:
    short_memory = ShortMemory(repo_path=tmp_path)
    short_memory.add_message("user", "stale short-memory text that must not leak")
    memory_store = MemoryStore(tmp_path / "memory")
    before_files = {
        path.name: path.read_bytes()
        for path in memory_store.data_dir.glob("*")
        if path.is_file()
    }
    state = _ingress_state()
    builder = MemoryContextBuilder(short_memory=short_memory, memory_store=memory_store)

    context = builder.build(
        "inspect",
        include_environment=False,
        session_ingress_state=state,
    )

    assert [item["attributes"]["message_id"] for item in context["dialog_context"]] == [
        "user-raw-1",
        "assistant-raw-2",
    ]
    assert "stale short-memory text" not in context["prompt_text"]
    decisions = context["context_selection"]["candidate_decisions"]
    dialog_decisions = [item for item in decisions if item["kind"] == "dialog"]
    assert {item["source_id"] for item in dialog_decisions} == {
        "user-raw-1",
        "assistant-raw-2",
    }
    constraint_decisions = [item for item in decisions if item["kind"] == "constraint"]
    assert len(constraint_decisions) == 1
    assert constraint_decisions[0]["source_id"] == state.session_constraints.authority_hash
    assert context["dialog_context"][1]["role"] == "assistant"
    after_files = {
        path.name: path.read_bytes()
        for path in memory_store.data_dir.glob("*")
        if path.is_file()
    }
    assert before_files == after_files
    assert [item.content for item in short_memory.get_context()] == [
        "stale short-memory text that must not leak"
    ]


def test_ingress_turn_ledger_is_stable_and_changes_request_hash(tmp_path) -> None:
    memory_store = MemoryStore(tmp_path / "memory")
    builder = MemoryContextBuilder(
        short_memory=ShortMemory(repo_path=tmp_path),
        memory_store=memory_store,
    )
    state = _ingress_state()
    same_snapshot = state.model_copy(deep=True)
    changed = state.model_copy(
        update={
            "turns": [
                *state.turns,
                SessionTurn(
                    identity=state.identity.model_copy(update={"turn_index": 3}),
                    message_id="user-raw-3",
                    role="user",
                    content="A different raw turn changes the ledger.",
                ),
            ],
            "identity": state.identity.model_copy(update={"turn_index": 3}),
        }
    )

    first = builder.build("inspect", include_environment=False, session_ingress_state=state)
    replay = builder.build(
        "inspect",
        include_environment=False,
        session_ingress_state=same_snapshot,
    )
    different = builder.build(
        "inspect",
        include_environment=False,
        session_ingress_state=changed,
    )

    assert first["context_request_hash"] == replay["context_request_hash"]
    assert first["context_request_hash"] != different["context_request_hash"]


def test_ingress_project_identity_mismatch_fails_closed(tmp_path) -> None:
    project_root = tmp_path / "owned-project"
    other_root = tmp_path / "other-project"
    identity = ConversationIdentity(
        conversation_id="session-1",
        run_id="run-1",
        turn_index=0,
        project_root=str(project_root),
    )
    state = SessionIngressState(identity=identity)
    builder = MemoryContextBuilder(
        short_memory=ShortMemory(repo_path=tmp_path),
        memory_store=MemoryStore(tmp_path / "memory"),
    )

    with pytest.raises(ValueError, match="project identity"):
        builder.build(
            "inspect",
            project_path=other_root,
            include_environment=False,
            session_ingress_state=state,
        )


def test_revoked_constraint_is_not_projected_but_tombstone_stays_in_runtime_state() -> None:
    state = revoke_constraint(_state(), "write_scope", turn_index=2)
    candidate = build_session_constraint_candidate(state)

    assert candidate is not None
    assert "calculator.py" not in candidate.content
    assert len(state.revoked_entries) == 1
    assert state.revoked_entries[0].constraint_key == "write_scope"


def test_required_session_constraint_budget_failure_is_explicit() -> None:
    builder = MemoryContextBuilder(max_prompt_chars=256)

    try:
        builder.build(
            "inspect",
            include_environment=False,
            session_constraints=_state(),
        )
    except ContextAssemblyBudgetError as exc:
        assert any("session_constraints" in item for item in exc.context["omitted_required_candidate_ids"])
    else:
        raise AssertionError("required session constraint must not be silently omitted")
