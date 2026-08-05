from __future__ import annotations

import copy

import pytest

from memory.context_projection import (
    DerivedContextProjectionError,
    build_derived_context_projection,
)
from memory.session_dialog import session_turn_ledger_hash
from metadata import (
    ContextCandidate,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateFreshness,
    ContextCandidateTruncation,
    ContextCompactionBinding,
    ContextCompactionRecord,
    ContextSelectionMetadata,
    ConversationIdentity,
    DerivedContextProjection,
    DurableArtifactReference,
    SessionConstraintState,
    SessionIngressState,
    SessionTurn,
)


def _fixture() -> tuple[dict, SessionIngressState]:
    identity = ConversationIdentity(
        conversation_id="projection-test",
        run_id="run-1",
        turn_index=2,
        project_root="/tmp/projection-test",
    )
    constraints = SessionConstraintState(
        session_id=identity.conversation_id,
        project_root=identity.project_root,
    )
    ingress = SessionIngressState(
        identity=identity,
        session_constraints=constraints,
        turns=[
            SessionTurn(
                identity=identity.model_copy(update={"turn_index": 0}),
                message_id="turn-old",
                role="assistant",
                content="old assistant detail",
            ),
            SessionTurn(
                identity=identity.model_copy(update={"turn_index": 1}),
                message_id="turn-current",
                role="user",
                content="keep the active target in scope",
            ),
        ],
    )
    constraint_candidate = ContextCandidate(
        candidate_id="session_constraints:constraint-1",
        kind=ContextCandidateKind.CONSTRAINT,
        source_id="session_constraints:constraint-1",
        content="Keep the active target in scope.",
        retention=ContextCandidateRetention.REQUIRED,
        priority=100,
        source_order=0,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.AUTHORITATIVE,
        freshness=ContextCandidateFreshness.CURRENT,
    )
    dialog_candidate = ContextCandidate(
        candidate_id="session_dialog:turn-current",
        kind=ContextCandidateKind.DIALOG,
        source_id="turn-current",
        content="USER: keep the active target in scope",
        role="user",
        source_order=1,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.CURRENT,
    )
    record = ContextCompactionRecord(
        compaction_id="compact-1",
        source_fingerprint="sha256:" + "1" * 64,
        source_candidate_ids=["session_dialog:turn-old"],
        algorithm="deterministic_dialog_extract_v1",
        summary="Earlier assistant detail was compacted.",
        original_chars=100,
        compacted_chars=len("Earlier assistant detail was compacted."),
    )
    binding = ContextCompactionBinding(
        record=record,
        artifact=DurableArtifactReference(
            artifact_id="artifact-1",
            kind="context_compaction",
            integrity_checksum="sha256:" + "2" * 64,
            bytes=42,
        ),
    )
    artifact_candidate = ContextCandidate(
        candidate_id="compaction:compact-1",
        kind=ContextCandidateKind.ARTIFACT,
        source_id=record.source_fingerprint,
        content=record.summary,
        retention=ContextCandidateRetention.PREFERRED,
        priority=99,
        source_order=2,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.DERIVED,
        freshness=ContextCandidateFreshness.CURRENT,
        compacted_candidate_ids=record.source_candidate_ids,
    )
    selected = [constraint_candidate, dialog_candidate, artifact_candidate]
    selection = ContextSelectionMetadata(
        max_prompt_chars=2_000,
        assembly_status="ready",
        candidate_decisions=[
            {
                "candidate_id": candidate.candidate_id,
                "kind": candidate.kind,
                "source_id": candidate.source_id,
                "retention": candidate.retention,
                "trust": candidate.trust,
                "freshness": candidate.freshness,
                "action": "kept",
                "reason": "within_budget",
                "original_chars": len(candidate.content),
                "selected_chars": len(candidate.content),
            }
            for candidate in selected
        ],
    )
    context = {
        "context_request_hash": "sha256:" + "3" * 64,
        "session_turn_source_hash": session_turn_ledger_hash(ingress),
        "session_constraints_hash": constraints.canonical_hash,
        "context_selection": selection.to_json_dict(),
        "selected_context_candidates": [candidate.model_dump(mode="json") for candidate in selected],
        "context_compactions": [binding.model_dump(mode="json")],
        "dialog_context": [{"message_id": "turn-current", "role": "user", "content": "keep the active target in scope"}],
    }
    return context, ingress


def test_projection_preserves_selected_candidates_and_hides_compacted_raw_turns() -> None:
    context, ingress = _fixture()

    projection = build_derived_context_projection(context, ingress)

    assert isinstance(projection, DerivedContextProjection)
    assert [item.candidate_id for item in projection.dialog_candidates] == [
        "session_dialog:turn-current"
    ]
    assert [item.candidate_id for item in projection.artifact_candidates] == [
        "compaction:compact-1"
    ]
    assert projection.artifact_candidates[0].compacted_candidate_ids == []
    assert "old assistant detail" not in projection.artifact_candidates[0].content


def test_projection_rejects_stale_or_incomplete_selected_view() -> None:
    context, ingress = _fixture()

    stale = copy.deepcopy(context)
    stale["session_turn_source_hash"] = "sha256:" + "9" * 64
    with pytest.raises(DerivedContextProjectionError, match="stale"):
        build_derived_context_projection(stale, ingress)

    incomplete = copy.deepcopy(context)
    incomplete["selected_context_candidates"] = incomplete["selected_context_candidates"][:-1]
    with pytest.raises(DerivedContextProjectionError, match="match selection"):
        build_derived_context_projection(incomplete, ingress)
