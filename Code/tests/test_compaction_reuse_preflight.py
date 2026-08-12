from __future__ import annotations

import hashlib

import pytest

from memory.compaction_reuse import (
    ReusableCompactionPromptUsePreflightStatus,
    ReusableCompactionPromptUseRejectionReason,
    ReusableCompactionSemanticFact,
    preflight_reusable_compaction_prompt_use,
)
from memory.compaction_summary import source_candidate_binding_hash
from metadata import (
    ContextAssemblyPolicy,
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ContextCompactionBinding,
    ContextCompactionReuseAdmission,
    ContextCompactionReuseAdmissionStatus,
    ContextCompactionRecord,
    DurableArtifactReference,
)


def _fixture():
    required = ContextCandidate(
        candidate_id="required-1",
        kind=ContextCandidateKind.INSTRUCTION,
        source_id="system",
        content="Must preserve the existing API.",
        role="system",
        retention=ContextCandidateRetention.REQUIRED,
        priority=100,
        source_order=0,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.AUTHORITATIVE,
        freshness=ContextCandidateFreshness.CURRENT,
    )
    source_1 = ContextCandidate(
        candidate_id="dialog-1",
        kind=ContextCandidateKind.DIALOG,
        source_id="turn-1",
        content=(
            "Alpha decision keep divide behaviour stable while preserving the "
            "existing API compatibility and avoiding unrelated changes."
        ),
        role="assistant",
        retention=ContextCandidateRetention.PREFERRED,
        priority=40,
        source_order=10,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.HISTORICAL,
    )
    source_2 = ContextCandidate(
        candidate_id="dialog-2",
        kind=ContextCandidateKind.DIALOG,
        source_id="turn-2",
        content=(
            "Beta validation run pytest after edits and treat missing validation "
            "as incomplete execution evidence."
        ),
        role="assistant",
        retention=ContextCandidateRetention.PREFERRED,
        priority=40,
        source_order=11,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.HISTORICAL,
    )
    recent = ContextCandidate(
        candidate_id="recent-1",
        kind=ContextCandidateKind.DIALOG,
        source_id="turn-3",
        content="Recent suffix remains verbatim.",
        role="assistant",
        retention=ContextCandidateRetention.PREFERRED,
        priority=80,
        source_order=20,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.CURRENT,
    )
    sources = [source_1, source_2]
    summary = "Alpha decision keep divide behaviour stable. Beta validation run pytest after edits."
    record = ContextCompactionRecord(
        compaction_id="preflight-compact-1",
        source_fingerprint="sha256:" + "a" * 64,
        source_candidate_ids=[source.candidate_id for source in sources],
        algorithm="deterministic_observation_mask_v1",
        summary=summary,
        original_chars=sum(len(source.content) for source in sources),
        compacted_chars=len(summary),
    )
    binding = ContextCompactionBinding(
        record=record,
        artifact=DurableArtifactReference(
            artifact_id="preflight-artifact-1",
            kind="context_compaction",
            integrity_checksum="sha256:" + "b" * 64,
            bytes=200,
        ),
        source_binding_hash=source_candidate_binding_hash(sources),
    )
    admission = ContextCompactionReuseAdmission(
        admission_id="preflight-admission-1",
        status=ContextCompactionReuseAdmissionStatus.ADMITTED,
        source_candidate_ids=list(record.source_candidate_ids),
        source_fingerprint=record.source_fingerprint,
        source_binding_hash=binding.source_binding_hash,
        recent_suffix_ids=["recent-1"],
        artifact_id=binding.artifact.artifact_id,
        artifact_kind=binding.artifact.kind,
        artifact_integrity_checksum=binding.artifact.integrity_checksum,
        generated_summary_fingerprint="sha256:" + hashlib.sha256(summary.encode()).hexdigest(),
        used_in_prompt=False,
    )
    facts = [
        ReusableCompactionSemanticFact(
            fact_id="fact-alpha",
            text="Alpha decision keep divide behaviour stable",
            evidence_candidate_ids=["dialog-1"],
        ),
        ReusableCompactionSemanticFact(
            fact_id="fact-beta",
            text="Beta validation run pytest after edits",
            evidence_candidate_ids=["dialog-2"],
        ),
    ]
    policy = ContextAssemblyPolicy(max_prompt_chars=1000)

    def renderer(candidates: list[ContextCandidate]) -> str:
        return "\n".join(f"[{candidate.candidate_id}] {candidate.content}" for candidate in candidates)

    return {
        "candidates": [required, source_1, source_2, recent],
        "binding": binding,
        "admission": admission,
        "facts": facts,
        "policy": policy,
        "renderer": renderer,
    }


def test_preflight_passes_without_prompt_authority():
    fixture = _fixture()
    result = preflight_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=fixture["candidates"],
        policy=fixture["policy"],
        renderer=fixture["renderer"],
        admission=fixture["admission"],
        semantic_facts=fixture["facts"],
        required_candidate_ids=["required-1"],
        recent_suffix_ids=["recent-1"],
        expected_artifact_integrity_checksum="sha256:" + "b" * 64,
    )
    assert result.status == ReusableCompactionPromptUsePreflightStatus.PASSED
    assert result.rejection_reasons == []
    assert set(result.trial_replaced_source_candidate_ids) == {"dialog-1", "dialog-2"}
    assert "required-1" in result.trial_selected_candidate_ids
    assert "recent-1" in result.trial_selected_candidate_ids
    assert result.used_in_prompt is False
    encoded = result.model_dump_json()
    assert "Reusable summary" not in encoded
    assert "Alpha decision" not in encoded


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("admission", ReusableCompactionPromptUseRejectionReason.ADMISSION_NOT_ADMITTED),
        ("source", ReusableCompactionPromptUseRejectionReason.SOURCE_BINDING_HASH_MISMATCH),
        ("fact", ReusableCompactionPromptUseRejectionReason.SEMANTIC_FACT_MISSING),
        ("evidence", ReusableCompactionPromptUseRejectionReason.SEMANTIC_EVIDENCE_MISMATCH),
        ("required", ReusableCompactionPromptUseRejectionReason.REQUIRED_CANDIDATE_OMITTED),
        ("recent", ReusableCompactionPromptUseRejectionReason.RECENT_SUFFIX_OMITTED),
    ],
)
def test_preflight_rejects_unsafe_inputs(mutation, reason):
    fixture = _fixture()
    if mutation == "admission":
        fixture["admission"] = fixture["admission"].model_copy(
            update={"status": ContextCompactionReuseAdmissionStatus.REJECTED,
                    "rejection_reason": "source_binding_hash_mismatch"}
        )
    elif mutation == "source":
        fixture["candidates"][1] = fixture["candidates"][1].model_copy(
            update={"content": "changed source"}
        )
    elif mutation == "fact":
        fixture["facts"] = [ReusableCompactionSemanticFact(
            fact_id="missing", text="Gamma requirement absent", evidence_candidate_ids=["dialog-1"]
        )]
    elif mutation == "evidence":
        fixture["facts"] = [ReusableCompactionSemanticFact(
            fact_id="bad", text="Alpha decision keep divide behaviour stable", evidence_candidate_ids=["recent-1"]
        )]
    elif mutation == "required":
        fixture["required_candidate_ids"] = ["missing-required"]
    elif mutation == "recent":
        fixture["recent_suffix_ids"] = ["dialog-2"]
    required = fixture.get("required_candidate_ids", ["required-1"])
    recent = fixture.get("recent_suffix_ids", ["recent-1"])
    result = preflight_reusable_compaction_prompt_use(
        binding=fixture["binding"], candidates=fixture["candidates"], policy=fixture["policy"],
        renderer=fixture["renderer"], admission=fixture["admission"], semantic_facts=fixture["facts"],
        required_candidate_ids=required, recent_suffix_ids=recent,
    )
    assert result.status == ReusableCompactionPromptUsePreflightStatus.REJECTED
    assert reason in result.rejection_reasons
    assert result.used_in_prompt is False
