"""Run builder-sourced reusable prompt-use simulation without mutating builder output."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from memory.compaction_summary import source_candidate_binding_hash
from memory.compaction_reuse import (
    ReusableCompactionSemanticFact,
    preflight_reusable_compaction_prompt_use,
    simulate_reusable_compaction_prompt_use,
)
from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.short_memory import ShortMemory
from metadata import (
    ContextAssemblyPolicy,
    ContextCandidate,
    ContextCandidateRetention,
    ContextCompactionBinding,
    ContextCompactionRecord,
    ContextCompactionReuseAdmission,
    ContextCompactionReuseAdmissionStatus,
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


SCHEMA = "phase-h8r2bh-builder-sourced-simulation-v1"
SECRET_SUMMARY_BODY = (
    "Reusable summary: Alpha decision keep divide behaviour stable. "
    "Beta validation run pytest after edits."
)


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _candidate_digest(candidate: ContextCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "kind": _enum_value(candidate.kind),
        "source_id": candidate.source_id,
        "role": candidate.role,
        "retention": _enum_value(candidate.retention),
        "trust": _enum_value(candidate.trust),
        "freshness": _enum_value(candidate.freshness),
        "truncation": _enum_value(candidate.truncation),
        "source_order": candidate.source_order,
        "content_sha256": _sha256_text(candidate.content),
    }


def _long_source(prefix: str, stable_fact: str) -> str:
    details = " ".join(
        f"{prefix} builder-selected context sentence {index} remains available as source evidence."
        for index in range(1, 10)
    )
    return f"{stable_fact}. {details}"


def _build_context(tmp_root: Path) -> dict[str, Any]:
    short_memory = ShortMemory(repo_path=tmp_root / "short")
    short_memory.add_message(
        "assistant",
        _long_source(
            "Alpha",
            "Alpha decision: keep divide behaviour stable while preserving existing API compatibility",
        ),
    )
    short_memory.add_message(
        "assistant",
        _long_source(
            "Beta",
            "Beta validation: run pytest after edits and treat missing validation as incomplete execution evidence",
        ),
    )
    short_memory.add_message("user", "Please continue with the same constraints.")
    short_memory.add_message("assistant", "Recent suffix must remain verbatim.")
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_root / "memory-store"),
        max_prompt_chars=5000,
    )
    return builder.build(
        "builder sourced reusable compaction simulation",
        include_environment=False,
        limit=6,
        system_prompt="Must preserve calculator API and write scope constraints.",
    )


def _binding_for_sources(sources: list[ContextCandidate]) -> ContextCompactionBinding:
    source_binding_hash = source_candidate_binding_hash(sources)
    record = ContextCompactionRecord(
        compaction_id="h8r2bh-builder-compact-1",
        source_fingerprint=canonical_hash(
            {"builder_source_candidate_ids": [source.candidate_id for source in sources]}
        ),
        source_candidate_ids=[source.candidate_id for source in sources],
        algorithm="deterministic_observation_mask_v1",
        summary=SECRET_SUMMARY_BODY,
        original_chars=sum(len(source.content) for source in sources),
        compacted_chars=len(SECRET_SUMMARY_BODY),
    )
    return ContextCompactionBinding(
        record=record,
        artifact=DurableArtifactReference(
            artifact_id="h8r2bh-builder-artifact-1",
            kind="context_compaction",
            integrity_checksum=canonical_hash({"artifact": "h8r2bh-builder-artifact-1"}),
            bytes=len(SECRET_SUMMARY_BODY.encode("utf-8")),
        ),
        source_binding_hash=source_binding_hash,
    )


def _admission_for(binding: ContextCompactionBinding, recent_ids: list[str]) -> ContextCompactionReuseAdmission:
    return ContextCompactionReuseAdmission(
        admission_id="h8r2bh-builder-admission-1",
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


def _select_builder_candidates(candidates: list[ContextCandidate]) -> dict[str, Any]:
    required_ids = [
        candidate.candidate_id
        for candidate in candidates
        if candidate.retention == ContextCandidateRetention.REQUIRED
    ]
    dialog_candidates = [
        candidate
        for candidate in candidates
        if _enum_value(candidate.kind) == "dialog"
    ]
    assistant_sources = [
        candidate for candidate in dialog_candidates if candidate.role == "assistant"
    ]
    source_candidates = assistant_sources[:2]
    recent_candidates = [
        candidate
        for candidate in dialog_candidates
        if candidate.candidate_id not in {source.candidate_id for source in source_candidates}
    ]
    return {
        "required_ids": required_ids,
        "source_candidates": source_candidates,
        "recent_ids": [recent_candidates[-1].candidate_id] if recent_candidates else [],
    }


def run_builder_sourced_simulation_gate(*, output_root: Path) -> dict[str, Any]:
    with TemporaryDirectory(prefix="h8r2bh-builder-") as tmp:
        builder_context = _build_context(Path(tmp))
    candidates = [
        ContextCandidate.model_validate(item)
        for item in builder_context["selected_context_candidates"]
    ]
    selected = _select_builder_candidates(candidates)
    source_candidates = selected["source_candidates"]
    required_ids = selected["required_ids"]
    recent_ids = selected["recent_ids"]
    shape_ready = bool(len(source_candidates) == 2 and required_ids and recent_ids)

    cases: list[dict[str, Any]] = []
    binding_digest: dict[str, Any] | None = None
    if shape_ready:
        binding = _binding_for_sources(source_candidates)
        binding_digest = {
            "compaction_id": binding.record.compaction_id,
            "source_candidate_ids": list(binding.record.source_candidate_ids),
            "source_fingerprint": binding.record.source_fingerprint,
            "source_binding_hash": binding.source_binding_hash,
            "artifact_id": binding.artifact.artifact_id,
            "artifact_integrity_checksum": binding.artifact.integrity_checksum,
            "summary_fingerprint": _sha256_text(binding.record.summary),
        }
        facts = [
            ReusableCompactionSemanticFact(
                fact_id="fact-alpha",
                text="Alpha decision keep divide behaviour stable",
                evidence_candidate_ids=[source_candidates[0].candidate_id],
            ),
            ReusableCompactionSemanticFact(
                fact_id="fact-beta",
                text="Beta validation run pytest after edits",
                evidence_candidate_ids=[source_candidates[1].candidate_id],
            ),
        ]
        policy = ContextAssemblyPolicy(max_prompt_chars=5000)
        preflight = preflight_reusable_compaction_prompt_use(
            binding=binding,
            candidates=candidates,
            policy=policy,
            renderer=MemoryContextBuilder._render_candidates,
            admission=_admission_for(binding, recent_ids),
            semantic_facts=facts,
            required_candidate_ids=required_ids,
            recent_suffix_ids=recent_ids,
            expected_artifact_integrity_checksum=binding.artifact.integrity_checksum,
            preflight_id="h8r2bh-builder-preflight",
        )
        simulation = simulate_reusable_compaction_prompt_use(
            binding=binding,
            candidates=candidates,
            policy=policy,
            renderer=MemoryContextBuilder._render_candidates,
            preflight=preflight,
            simulation_id="builder_sourced_pass",
        )
        cases.append(simulation.model_dump(mode="json"))

    pass_case = cases[0] if cases else {}
    builder_prompt_hash = _sha256_text(str(builder_context.get("prompt_text") or ""))
    invariants = {
        "builder_output_shape_ready": shape_ready,
        "builder_prompt_hash_present": bool(builder_prompt_hash),
        "builder_context_compactions_unchanged": builder_context.get("context_compactions", [])
        == [],
        "simulation_passed": pass_case.get("status") == "passed",
        "simulation_reduces_prompt_chars": (pass_case.get("prompt_char_delta") or 0) > 0,
        "simulation_replaces_builder_sources": set(
            pass_case.get("replaced_source_candidate_ids", [])
        )
        == {candidate.candidate_id for candidate in source_candidates},
        "simulation_keeps_required_and_recent": (
            set(pass_case.get("retained_required_candidate_ids", [])) == set(required_ids)
            and set(pass_case.get("retained_recent_suffix_ids", [])) == set(recent_ids)
        ),
        "simulation_is_dry_run": pass_case.get("used_in_prompt") is False,
    }
    result = {
        "schema": SCHEMA,
        "status": "passed" if all(invariants.values()) else "needs_followup",
        "claim_boundary": "builder_sourced_default_off_simulation_no_builder_prompt_mutation",
        "builder_prompt_hash": builder_prompt_hash,
        "builder_context_request_hash": builder_context.get("context_request_hash"),
        "builder_selected_candidate_ids": [
            candidate.candidate_id for candidate in candidates
        ],
        "candidate_digests": [_candidate_digest(candidate) for candidate in candidates],
        "binding_digest": binding_digest,
        "required_candidate_ids": required_ids,
        "recent_suffix_ids": recent_ids,
        "cases": cases,
        "invariants": invariants,
        "side_effects": (
            dict(ZERO_SIDE_EFFECTS)
            | {
                "fixture_files_written": True,
                "persistent_fixture_files_written": False,
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
    result = run_builder_sourced_simulation_gate(output_root=args.output_root)
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
