"""Run the reusable compaction prompt-use simulation gate."""

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
    simulate_reusable_compaction_prompt_use,
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


SCHEMA = "phase-h8r2bg-prompt-use-simulation-v1"
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


def _long_source(prefix: str, stable_fact: str) -> str:
    details = " ".join(
        f"{prefix} supporting evidence sentence {index} keeps the historical context explicit."
        for index in range(1, 9)
    )
    return f"{stable_fact}. {details}"


def _build_binding(
    *,
    sources: list[ContextCandidate],
    summary: str,
    artifact_id: str,
    compaction_id: str,
) -> ContextCompactionBinding:
    source_binding_hash = source_candidate_binding_hash(sources)
    record = ContextCompactionRecord(
        compaction_id=compaction_id,
        source_fingerprint=canonical_hash(
            {"source_candidate_ids": [source.candidate_id for source in sources]}
        ),
        source_candidate_ids=[source.candidate_id for source in sources],
        algorithm="deterministic_observation_mask_v1",
        summary=summary,
        original_chars=max(
            sum(len(source.content) for source in sources),
            len(summary) + 1,
        ),
        compacted_chars=len(summary),
    )
    return ContextCompactionBinding(
        record=record,
        artifact=DurableArtifactReference(
            artifact_id=artifact_id,
            kind="context_compaction",
            integrity_checksum=canonical_hash({"artifact": artifact_id}),
            bytes=len(summary.encode("utf-8")),
        ),
        source_binding_hash=source_binding_hash,
    )


def _admission_for(binding: ContextCompactionBinding, recent_ids: list[str]) -> ContextCompactionReuseAdmission:
    return ContextCompactionReuseAdmission(
        admission_id=f"{binding.record.compaction_id}-admission",
        status=ContextCompactionReuseAdmissionStatus.ADMITTED,
        source_candidate_ids=list(binding.record.source_candidate_ids),
        source_fingerprint=binding.record.source_fingerprint,
        source_binding_hash=binding.source_binding_hash,
        recent_suffix_ids=list(recent_ids),
        artifact_id=binding.artifact.artifact_id,
        artifact_kind=binding.artifact.kind,
        artifact_integrity_checksum=binding.artifact.integrity_checksum,
        generated_summary_fingerprint=_sha256_text(binding.record.summary),
        used_in_prompt=False,
    )


def _fixture() -> dict[str, Any]:
    required = ContextCandidate(
        candidate_id="required-1",
        kind=ContextCandidateKind.INSTRUCTION,
        source_id="system",
        content="Must preserve calculator API and write scope constraints.",
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
        content=_long_source(
            "Alpha",
            "Alpha decision: keep divide behaviour stable while preserving existing API compatibility",
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
        content=_long_source(
            "Beta",
            "Beta validation: run pytest after edits and treat missing validation as incomplete execution evidence",
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
    binding = _build_binding(
        sources=sources,
        summary=SECRET_SUMMARY_BODY,
        artifact_id="h8r2bg-artifact-1",
        compaction_id="h8r2bg-compact-1",
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
        "admission": _admission_for(binding, ["recent-1"]),
        "facts": facts,
        "required_ids": ["required-1"],
        "recent_ids": ["recent-1"],
        "policy": ContextAssemblyPolicy(max_prompt_chars=5000),
        "renderer": _renderer,
    }


def _preflight(local: dict[str, Any], *, policy: ContextAssemblyPolicy | None = None):
    return preflight_reusable_compaction_prompt_use(
        binding=local["binding"],
        candidates=local["candidates"],
        policy=policy or local["policy"],
        renderer=local["renderer"],
        admission=local["admission"],
        semantic_facts=local["facts"],
        required_candidate_ids=local["required_ids"],
        recent_suffix_ids=local["recent_ids"],
        expected_artifact_integrity_checksum=local["binding"].artifact.integrity_checksum,
        preflight_id=f"{local['binding'].record.compaction_id}-preflight",
    )


def _run_case(
    fixture: dict[str, Any],
    *,
    case_id: str,
    mutate: Callable[[dict[str, Any]], None] | None = None,
    preflight_policy: ContextAssemblyPolicy | None = None,
    simulation_policy: ContextAssemblyPolicy | None = None,
) -> dict[str, Any]:
    local = dict(fixture)
    local["candidates"] = list(fixture["candidates"])
    local["facts"] = list(fixture["facts"])
    local["required_ids"] = list(fixture["required_ids"])
    local["recent_ids"] = list(fixture["recent_ids"])
    if mutate is not None:
        mutate(local)
    preflight = _preflight(local, policy=preflight_policy)
    simulation = simulate_reusable_compaction_prompt_use(
        binding=local["binding"],
        candidates=local["candidates"],
        policy=simulation_policy or local["policy"],
        renderer=local["renderer"],
        preflight=preflight,
        simulation_id=case_id,
    )
    return simulation.model_dump(mode="json")


def _mutate_source_drift(local: dict[str, Any]) -> None:
    preflight = _preflight(local)
    local["precomputed_preflight"] = preflight
    local["candidates"] = [
        candidate.model_copy(update={"content": "changed source"})
        if candidate.candidate_id == "dialog-1"
        else candidate
        for candidate in local["candidates"]
    ]


def _run_source_drift_case(fixture: dict[str, Any]) -> dict[str, Any]:
    local = dict(fixture)
    local["candidates"] = list(fixture["candidates"])
    local["facts"] = list(fixture["facts"])
    local["required_ids"] = list(fixture["required_ids"])
    local["recent_ids"] = list(fixture["recent_ids"])
    preflight = _preflight(local)
    _mutate_source_drift(local)
    simulation = simulate_reusable_compaction_prompt_use(
        binding=local["binding"],
        candidates=local["candidates"],
        policy=local["policy"],
        renderer=local["renderer"],
        preflight=preflight,
        simulation_id="source_drift",
    )
    return simulation.model_dump(mode="json")


def _run_no_benefit_case(fixture: dict[str, Any]) -> dict[str, Any]:
    local = dict(fixture)
    local["candidates"] = list(fixture["candidates"])
    local["facts"] = list(fixture["facts"])
    local["required_ids"] = list(fixture["required_ids"])
    local["recent_ids"] = list(fixture["recent_ids"])
    sources = [
        candidate
        for candidate in local["candidates"]
        if candidate.candidate_id in {"dialog-1", "dialog-2"}
    ]
    long_summary = (
        SECRET_SUMMARY_BODY
        + " Extra retained detail that is intentionally longer than the raw sources."
        * 24
    )
    binding = _build_binding(
        sources=sources,
        summary=long_summary,
        artifact_id="h8r2bg-artifact-long",
        compaction_id="h8r2bg-compact-long",
    )
    local["binding"] = binding
    local["admission"] = _admission_for(binding, ["recent-1"])
    policy = ContextAssemblyPolicy(max_prompt_chars=6000)
    preflight = _preflight(local, policy=policy)
    simulation = simulate_reusable_compaction_prompt_use(
        binding=binding,
        candidates=local["candidates"],
        policy=policy,
        renderer=local["renderer"],
        preflight=preflight,
        simulation_id="no_benefit",
    )
    return simulation.model_dump(mode="json")


def run_prompt_use_simulation_gate(*, output_root: Path) -> dict[str, Any]:
    fixture = _fixture()
    cases = [
        _run_case(fixture, case_id="pass"),
        _run_case(
            fixture,
            case_id="preflight_rejected",
            preflight_policy=ContextAssemblyPolicy(max_prompt_chars=80),
        ),
        _run_source_drift_case(fixture),
        _run_case(
            fixture,
            case_id="summary_fallback",
            simulation_policy=ContextAssemblyPolicy(max_prompt_chars=80),
        ),
        _run_no_benefit_case(fixture),
    ]
    by_id = {case["simulation_id"]: case for case in cases}
    pass_case = by_id["pass"]
    invariants = {
        "pass_case_passed": pass_case["status"] == "passed",
        "pass_case_reduces_prompt_chars": pass_case["prompt_char_delta"] > 0,
        "pass_case_replaces_all_sources": set(pass_case["replaced_source_candidate_ids"])
        == {"dialog-1", "dialog-2"},
        "pass_case_keeps_required_and_recent": (
            pass_case["retained_required_candidate_ids"] == ["required-1"]
            and pass_case["retained_recent_suffix_ids"] == ["recent-1"]
        ),
        "all_simulations_dry_run": all(case["used_in_prompt"] is False for case in cases),
        "preflight_rejected_fails": "preflight_not_passed"
        in by_id["preflight_rejected"]["rejection_reasons"],
        "source_drift_fails": "source_binding_hash_mismatch"
        in by_id["source_drift"]["rejection_reasons"],
        "summary_fallback_fails": "source_replacement_mismatch"
        in by_id["summary_fallback"]["rejection_reasons"],
        "no_benefit_fails": "no_prompt_reduction"
        in by_id["no_benefit"]["rejection_reasons"],
    }
    result = {
        "schema": SCHEMA,
        "status": "passed" if all(invariants.values()) else "needs_followup",
        "claim_boundary": "default_off_prompt_use_simulation_no_production_prompt_mutation",
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
    result = run_prompt_use_simulation_gate(output_root=args.output_root)
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
