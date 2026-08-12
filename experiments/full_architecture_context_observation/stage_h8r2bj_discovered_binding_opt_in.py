"""Run discovered persisted-binding reusable prompt-use opt-in canary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Sequence

from memory.compaction_reuse import (
    ReusableCompactionSemanticFact,
    build_checkpoint_compaction_reuse_shadow_provider,
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
    ContextSelectionMetadata,
    DurableArtifactReference,
    RuntimePromptContextSnapshot,
)

ZERO_SIDE_EFFECTS = {
    "provider_transport_attempted": False,
    "provider_calls": 0,
    "network_side_effects": 0,
    "project_mutations": 0,
    "memory_mutations": 0,
    "writer_actions": 0,
    "command_actions": 0,
    "verification_runs": 0,
}


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
from experiments.full_architecture_context_observation.stage_h8r2bh_builder_sourced_simulation import (
    _candidate_digest,
    _enum_value,
    _sha256_text,
)
from experiments.full_architecture_context_observation.stage_h8r2bi_token_aware_opt_in_canary import (
    _WhitespaceTokenCounter,
)


SCHEMA = "phase-h8r2bj-discovered-binding-opt-in-v1"


def _seed_dialog(short_memory: ShortMemory, count: int = 8) -> None:
    for index in range(count):
        short_memory.add_message(
            "assistant",
            (
                f"Decision {index}: preserve calculator API and validation command. "
                f"Path evidence {index}: calculator.py remains the scoped target. "
                + (f"supporting-token-{index} " * 80)
            ),
        )


def _compaction_sink(record: dict[str, Any]) -> DurableArtifactReference:
    artifact_id = f"h8r2bj-artifact-{record['compaction_id']}"
    return DurableArtifactReference(
        artifact_id=artifact_id,
        kind="context_compaction",
        integrity_checksum=canonical_hash(record),
        bytes=len(json.dumps(record, ensure_ascii=False, sort_keys=True)),
    )


def _build_contexts(tmp_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    short_memory = ShortMemory(repo_path=tmp_root / "short")
    _seed_dialog(short_memory)
    raw_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_root / "raw-memory"),
        max_prompt_chars=20000,
    )
    raw_context = raw_builder.build(
        "discovered binding opt-in raw",
        include_environment=False,
        limit=8,
        system_prompt="Required: preserve calculator API and validation command.",
    )
    compact_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_root / "compact-memory"),
        max_prompt_chars=4000,
    )
    compact_builder.set_checkpoint_handlers(compaction_sink=_compaction_sink)
    compact_context = compact_builder.build(
        "discovered binding opt-in compact",
        include_environment=False,
        limit=8,
        system_prompt="Required: preserve calculator API and validation command.",
    )
    return raw_context, compact_context


def _snapshot_from_context(context: dict[str, Any], *, context_id: str) -> RuntimePromptContextSnapshot:
    prompt_text = str(context.get("prompt_text") or "")
    prompt_hash = _sha256_text(prompt_text)
    return RuntimePromptContextSnapshot(
        context_id=context_id,
        request_hash=str(context.get("context_request_hash") or ""),
        prompt_hash=prompt_hash,
        selection=ContextSelectionMetadata.model_validate(context["context_selection"]),
        context_artifact=DurableArtifactReference(
            artifact_id=f"{context_id}-prompt-context",
            kind="prompt_context",
            integrity_checksum=canonical_hash({"prompt_hash": prompt_hash}),
            bytes=len(prompt_text.encode("utf-8")),
        ),
        compaction_bindings=[
            ContextCompactionBinding.model_validate(item)
            for item in context.get("context_compactions") or []
        ],
    )


def _shadow_payload_from_raw_context(
    *,
    raw_context: dict[str, Any],
    candidates: list[ContextCandidate],
    source_fingerprint: str | None = None,
    source_candidate_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": "memory-context-compaction-reuse-shadow-v1",
        "context_request_hash": raw_context.get("context_request_hash"),
        "session_turn_source_hash": raw_context.get("session_turn_source_hash") or "",
        "session_constraints_hash": raw_context.get("session_constraints_hash") or "",
        "prompt_hash": _sha256_text(str(raw_context.get("prompt_text") or "")),
        "candidate_digests": [_candidate_digest(candidate) for candidate in candidates],
        "selected_candidate_ids": [candidate.candidate_id for candidate in candidates],
        "assembly_status": "ready",
    }
    # The admission contract binds the persisted record to the current source
    # content, not merely to candidate IDs.  Keep this experiment payload
    # body-free while carrying the binding produced by the compact builder.
    if source_fingerprint:
        source_ids = list(source_candidate_ids or [candidate.candidate_id for candidate in candidates])
        payload["source_fingerprint_by_candidate_ids"] = {
            json.dumps(source_ids, ensure_ascii=False, separators=(",", ":")): source_fingerprint
        }
    return payload


def _optional_hash(value: Any) -> str | None:
    text = str(value or "")
    return text if text.startswith("sha256:") else None


def _select_required_and_recent(
    candidates: list[ContextCandidate],
    *,
    binding: ContextCompactionBinding,
) -> tuple[list[str], list[str]]:
    source_ids = set(binding.record.source_candidate_ids)
    required_ids = [
        candidate.candidate_id
        for candidate in candidates
        if candidate.retention == ContextCandidateRetention.REQUIRED
    ]
    dialog_candidates = [
        candidate
        for candidate in candidates
        if _enum_value(candidate.kind) == "dialog"
        and candidate.candidate_id not in source_ids
    ]
    recent_ids = [candidate.candidate_id for candidate in dialog_candidates[-2:]]
    return required_ids, recent_ids


def _semantic_facts_from_binding(binding: ContextCompactionBinding) -> list[ReusableCompactionSemanticFact]:
    source_ids = list(binding.record.source_candidate_ids)
    summary_lines = [
        " ".join(line.removeprefix("-").split())
        for line in binding.record.summary.splitlines()
        if line.strip().startswith("-")
    ]
    if not summary_lines:
        summary_lines = [" ".join(binding.record.summary.split())]
    facts: list[ReusableCompactionSemanticFact] = []
    for index, text in enumerate(summary_lines[:2]):
        facts.append(
            ReusableCompactionSemanticFact(
                fact_id=f"fact-discovered-{index + 1}",
                text=text,
                evidence_candidate_ids=[source_ids[min(index, len(source_ids) - 1)]],
            )
        )
    return facts


def run_discovered_binding_opt_in_canary(*, output_root: Path) -> dict[str, Any]:
    with TemporaryDirectory(prefix="h8r2bj-discovered-") as tmp:
        raw_context, compact_context = _build_contexts(Path(tmp))
    raw_candidates = [
        ContextCandidate.model_validate(item)
        for item in raw_context.get("selected_context_candidates") or []
    ]
    compact_bindings = [
        ContextCompactionBinding.model_validate(item)
        for item in compact_context.get("context_compactions") or []
    ]
    binding = compact_bindings[0] if compact_bindings else None
    required_ids: list[str] = []
    recent_ids: list[str] = []
    admissions: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    if binding is not None:
        required_ids, recent_ids = _select_required_and_recent(
            raw_candidates,
            binding=binding,
        )
        snapshot = _snapshot_from_context(compact_context, context_id="h8r2bj-compact-context")
        provider = build_checkpoint_compaction_reuse_shadow_provider(
            snapshot,
            required_candidate_ids_by_compaction_id={
                binding.record.compaction_id: required_ids,
            },
            recent_suffix_ids_by_compaction_id={
                binding.record.compaction_id: recent_ids,
            },
            session_constraints_hash=_optional_hash(
                raw_context.get("session_constraints_hash")
            ),
        )
        raw_shadow_payload = _shadow_payload_from_raw_context(
            raw_context=raw_context,
            candidates=raw_candidates,
            source_fingerprint=binding.record.source_fingerprint,
            source_candidate_ids=binding.record.source_candidate_ids,
        )
        discovered = provider(raw_shadow_payload)
        admissions = [admission.model_dump(mode="json") for admission in discovered]
        admitted = next(
            (
                admission
                for admission in discovered
                if admission.status == "admitted"
                and admission.artifact_id == binding.artifact.artifact_id
            ),
            None,
        )
        if admitted is not None:
            counter = _WhitespaceTokenCounter()
            policy = ContextAssemblyPolicy(
                max_prompt_chars=20000,
                max_prompt_tokens=1600,
                reserved_prompt_tokens=100,
            )
            preflight = preflight_reusable_compaction_prompt_use(
                binding=binding,
                candidates=raw_candidates,
                policy=policy,
                renderer=MemoryContextBuilder._render_candidates,
                admission=admitted,
                semantic_facts=_semantic_facts_from_binding(binding),
                required_candidate_ids=required_ids,
                recent_suffix_ids=recent_ids,
                expected_artifact_integrity_checksum=binding.artifact.integrity_checksum,
                preflight_id="h8r2bj-discovered-preflight",
                token_counter=counter,
            )
            simulation = simulate_reusable_compaction_prompt_use(
                binding=binding,
                candidates=raw_candidates,
                policy=policy,
                renderer=MemoryContextBuilder._render_candidates,
                preflight=preflight,
                simulation_id="discovered_binding_opt_in_pass",
                token_counter=counter,
            )
            cases.append(simulation.model_dump(mode="json"))

    pass_case = cases[0] if cases else {}
    binding_digest = (
        {
            "compaction_id": binding.record.compaction_id,
            "source_candidate_ids": list(binding.record.source_candidate_ids),
            "source_fingerprint": binding.record.source_fingerprint,
            "source_binding_hash": binding.source_binding_hash,
            "artifact_id": binding.artifact.artifact_id,
            "artifact_integrity_checksum": binding.artifact.integrity_checksum,
            "summary_fingerprint": _sha256_text(binding.record.summary),
        }
        if binding is not None
        else None
    )
    invariants = {
        "raw_builder_candidates_available": bool(raw_candidates),
        "compact_builder_produced_one_binding": len(compact_bindings) == 1,
        "persisted_binding_has_source_hash": bool(
            binding is not None and binding.source_binding_hash
        ),
        "discovery_admitted_binding": any(
            admission.get("status") == "admitted"
            and admission.get("artifact_id")
            == (binding.artifact.artifact_id if binding is not None else "")
            for admission in admissions
        ),
        "discovery_is_shadow_only": all(
            admission.get("used_in_prompt") is False for admission in admissions
        ),
        "simulation_passed": pass_case.get("status") == "passed",
        "char_delta_positive": (pass_case.get("prompt_char_delta") or 0) > 0,
        "token_delta_positive": (pass_case.get("prompt_token_delta") or 0) > 0,
        "simulation_replaces_discovered_sources": set(
            pass_case.get("replaced_source_candidate_ids", [])
        )
        == set(binding.record.source_candidate_ids if binding is not None else []),
        "simulation_keeps_required_and_recent": (
            set(pass_case.get("retained_required_candidate_ids", [])) == set(required_ids)
            and set(pass_case.get("retained_recent_suffix_ids", [])) == set(recent_ids)
        ),
        "simulation_is_dry_run": pass_case.get("used_in_prompt") is False,
    }
    result = {
        "schema": SCHEMA,
        "status": "passed" if all(invariants.values()) else "needs_followup",
        "claim_boundary": "discovered_persisted_binding_opt_in_no_production_prompt_mutation",
        "raw_builder_prompt_hash": _sha256_text(str(raw_context.get("prompt_text") or "")),
        "compact_builder_prompt_hash": _sha256_text(
            str(compact_context.get("prompt_text") or "")
        ),
        "raw_context_request_hash": raw_context.get("context_request_hash"),
        "compact_context_request_hash": compact_context.get("context_request_hash"),
        "raw_selected_candidate_ids": [
            candidate.candidate_id for candidate in raw_candidates
        ],
        "candidate_digests": [_candidate_digest(candidate) for candidate in raw_candidates],
        "binding_digest": binding_digest,
        "required_candidate_ids": required_ids,
        "recent_suffix_ids": recent_ids,
        "admissions": admissions,
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
    result = run_discovered_binding_opt_in_canary(output_root=args.output_root)
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
