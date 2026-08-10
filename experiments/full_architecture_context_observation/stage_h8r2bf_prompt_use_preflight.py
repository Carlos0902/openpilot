"""Run the reusable compaction prompt-use preflight gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from memory.compaction_summary import source_candidate_binding_hash
from memory.compaction_reuse import (
    ReusableCompactionSemanticFact,
    preflight_reusable_compaction_prompt_use,
)
from metadata import (
    ContextAssemblyPolicy,
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ContextCompactionBinding,
    ContextCompactionRecord,
    ContextCompactionReuseAdmission,
    ContextCompactionReuseAdmissionStatus,
    ContextCompactionReuseRejectionReason,
    DurableArtifactReference,
)

from experiments.full_architecture_context_observation.stage_h8r2at_provider_summary_shadow import (
    _write_receipt,
)
from experiments.full_architecture_context_observation.stage_h8r2av_multi_window_summary_quality import (
    canonical_hash,
)
from experiments.full_architecture_context_observation.stage_h8r2az_reusable_summary_artifact import (
    ZERO_SIDE_EFFECTS,
)


SCHEMA = "phase-h8r2bf-prompt-use-preflight-v1"
SECRET_SUMMARY_BODY = (
    "Reusable summary: Alpha decision keep divide behaviour stable. "
    "Beta validation run pytest after edits."
)


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _candidate_digest(candidate: ContextCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "kind": str(candidate.kind),
        "source_id": candidate.source_id,
        "role": candidate.role,
        "retention": str(candidate.retention),
        "trust": str(candidate.trust),
        "freshness": str(candidate.freshness),
        "truncation": str(candidate.truncation),
        "source_order": candidate.source_order,
        "content_sha256": _sha256_text(candidate.content),
    }


def _renderer(candidates: list[ContextCandidate]) -> str:
    return "\n".join(
        f"[{candidate.candidate_id}] {candidate.content}" for candidate in candidates
    )


def _fixture() -> dict[str, Any]:
    required = ContextCandidate(
        candidate_id="required-1",
        kind=ContextCandidateKind.INSTRUCTION,
        source_id="system",
        content="Must preserve calculator API.",
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
            "Alpha decision: keep divide behaviour stable while preserving "
            "existing API compatibility and avoiding README changes."
        ),
        role="assistant",
        retention=ContextCandidateRetention.PREFERRED,
        priority=40,
        source_order=10,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.HISTORICAL,
    )
    source_2 = ContextCandidate(
        candidate_id="dialog-2",
        kind=ContextCandidateKind.DIALOG,
        source_id="turn-2",
        content=(
            "Beta validation: run pytest after edits and treat missing validation "
            "as incomplete execution evidence."
        ),
        role="assistant",
        retention=ContextCandidateRetention.PREFERRED,
        priority=40,
        source_order=11,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.HISTORICAL,
    )
    recent = ContextCandidate(
        candidate_id="recent-1",
        kind=ContextCandidateKind.DIALOG,
        source_id="turn-3",
        content="Recent suffix must remain verbatim.",
        role="assistant",
        retention=ContextCandidateRetention.PREFERRED,
        priority=80,
        source_order=20,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.CURRENT,
    )
    sources = [source_1, source_2]
    source_binding_hash = source_candidate_binding_hash(sources)
    record = ContextCompactionRecord(
        compaction_id="h8r2bf-compact-1",
        source_fingerprint=canonical_hash(
            {"source_candidate_ids": [source.candidate_id for source in sources]}
        ),
        source_candidate_ids=[source.candidate_id for source in sources],
        algorithm="deterministic_observation_mask_v1",
        summary=SECRET_SUMMARY_BODY,
        original_chars=sum(len(source.content) for source in sources),
        compacted_chars=len(SECRET_SUMMARY_BODY),
    )
    binding = ContextCompactionBinding(
        record=record,
        artifact=DurableArtifactReference(
            artifact_id="h8r2bf-artifact-1",
            kind="context_compaction",
            integrity_checksum=canonical_hash({"artifact": "h8r2bf-artifact-1"}),
            bytes=200,
        ),
        source_binding_hash=source_binding_hash,
    )
    admission = ContextCompactionReuseAdmission(
        admission_id="h8r2bf-admission-1",
        status=ContextCompactionReuseAdmissionStatus.ADMITTED,
        source_candidate_ids=list(record.source_candidate_ids),
        source_fingerprint=record.source_fingerprint,
        source_binding_hash=source_binding_hash,
        recent_suffix_ids=["recent-1"],
        artifact_id=binding.artifact.artifact_id,
        artifact_kind=binding.artifact.kind,
        artifact_integrity_checksum=binding.artifact.integrity_checksum,
        generated_summary_fingerprint=_sha256_text(record.summary),
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
    return {
        "candidates": [required, source_1, source_2, recent],
        "binding": binding,
        "admission": admission,
        "facts": facts,
        "policy": ContextAssemblyPolicy(max_prompt_chars=1000),
        "renderer": _renderer,
    }


def _run_case(
    fixture: dict[str, Any],
    *,
    case_id: str,
    mutate: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    local = dict(fixture)
    local["candidates"] = list(fixture["candidates"])
    local["facts"] = list(fixture["facts"])
    local["required_ids"] = ["required-1"]
    local["recent_ids"] = ["recent-1"]
    if mutate is not None:
        mutate(local)
    preflight = preflight_reusable_compaction_prompt_use(
        binding=local["binding"],
        candidates=local["candidates"],
        policy=local["policy"],
        renderer=local["renderer"],
        admission=local["admission"],
        semantic_facts=local["facts"],
        required_candidate_ids=local["required_ids"],
        recent_suffix_ids=local["recent_ids"],
        expected_artifact_integrity_checksum=local["binding"].artifact.integrity_checksum,
        preflight_id=case_id,
    )
    return preflight.model_dump(mode="json")


def run_prompt_use_preflight_gate(*, output_root: Path) -> dict[str, Any]:
    fixture = _fixture()
    cases = [
        _run_case(fixture, case_id="pass"),
        _run_case(
            fixture,
            case_id="admission_rejected",
            mutate=lambda item: item.__setitem__(
                "admission",
                item["admission"].model_copy(
                    update={
                        "status": ContextCompactionReuseAdmissionStatus.REJECTED,
                        "rejection_reason": (
                            ContextCompactionReuseRejectionReason.SOURCE_BINDING_HASH_MISMATCH
                        ),
                    }
                ),
            ),
        ),
        _run_case(
            fixture,
            case_id="source_drift",
            mutate=lambda item: item.__setitem__(
                "candidates",
                [
                    candidate.model_copy(update={"content": "changed source"})
                    if candidate.candidate_id == "dialog-1"
                    else candidate
                    for candidate in item["candidates"]
                ],
            ),
        ),
        _run_case(
            fixture,
            case_id="semantic_missing",
            mutate=lambda item: item.__setitem__(
                "facts",
                [
                    ReusableCompactionSemanticFact(
                        fact_id="missing",
                        text="Gamma requirement absent from summary",
                        evidence_candidate_ids=["dialog-1"],
                    )
                ],
            ),
        ),
        _run_case(
            fixture,
            case_id="semantic_bad_evidence",
            mutate=lambda item: item.__setitem__(
                "facts",
                [
                    ReusableCompactionSemanticFact(
                        fact_id="bad-evidence",
                        text="Alpha decision keep divide behaviour stable",
                        evidence_candidate_ids=["recent-1"],
                    )
                ],
            ),
        ),
        _run_case(
            fixture,
            case_id="recent_omitted",
            mutate=lambda item: item.__setitem__("recent_ids", ["dialog-2"]),
        ),
        _run_case(
            fixture,
            case_id="trial_not_selected",
            mutate=lambda item: item.__setitem__(
                "policy",
                ContextAssemblyPolicy(max_prompt_chars=80),
            ),
        ),
    ]
    by_id = {case["preflight_id"]: case for case in cases}
    invariants = {
        "pass_case_passed": by_id["pass"]["status"] == "passed",
        "pass_case_replaces_all_sources": set(
            by_id["pass"]["trial_replaced_source_candidate_ids"]
        )
        == {"dialog-1", "dialog-2"},
        "all_admissions_dry_run": all(case["used_in_prompt"] is False for case in cases),
        "admission_rejected_fails": "admission_not_admitted"
        in by_id["admission_rejected"]["rejection_reasons"],
        "source_drift_fails": "source_binding_hash_mismatch"
        in by_id["source_drift"]["rejection_reasons"],
        "semantic_missing_fails": "semantic_fact_missing"
        in by_id["semantic_missing"]["rejection_reasons"],
        "semantic_bad_evidence_fails": "semantic_evidence_mismatch"
        in by_id["semantic_bad_evidence"]["rejection_reasons"],
        "recent_omitted_fails": "recent_suffix_omitted"
        in by_id["recent_omitted"]["rejection_reasons"],
        "trial_not_selected_fails": "trial_summary_not_selected"
        in by_id["trial_not_selected"]["rejection_reasons"],
    }
    result = {
        "schema": SCHEMA,
        "status": "passed" if all(invariants.values()) else "needs_followup",
        "claim_boundary": "default_off_prompt_use_preflight_no_prompt_mutation",
        "candidate_digests": [
            _candidate_digest(candidate) for candidate in fixture["candidates"]
        ],
        "binding_digest": {
            "compaction_id": fixture["binding"].record.compaction_id,
            "source_candidate_ids": list(fixture["binding"].record.source_candidate_ids),
            "source_fingerprint": fixture["binding"].record.source_fingerprint,
            "source_binding_hash": fixture["binding"].source_binding_hash,
            "artifact_id": fixture["binding"].artifact.artifact_id,
            "artifact_integrity_checksum": fixture["binding"].artifact.integrity_checksum,
            "summary_fingerprint": _sha256_text(fixture["binding"].record.summary),
        },
        "semantic_fact_ids": [fact.fact_id for fact in fixture["facts"]],
        "cases": cases,
        "invariants": invariants,
        "side_effects": (
            dict(ZERO_SIDE_EFFECTS)
            | {
                "fixture_files_written": True,
                "provider_transport_attempted": False,
                "provider_calls": 0,
            }
        ),
        "secret_handling": {"credential_present": False, "serialized": False},
    }
    result["receipt_hash"] = canonical_hash(result)
    _write_receipt(output_root / "aggregate" / "receipt.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run_prompt_use_preflight_gate(output_root=args.output_root)
    print(
        json.dumps(
            {
                "status": result["status"],
                "receipt_hash": result["receipt_hash"],
                "invariants": result["invariants"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
