"""Run token-aware builder-adjacent reusable prompt-use opt-in canary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from memory.compaction_reuse import (
    ReusableCompactionSemanticFact,
    preflight_reusable_compaction_prompt_use,
    simulate_reusable_compaction_prompt_use,
)
from memory.context_builder import MemoryContextBuilder
from metadata import ContextAssemblyPolicy, ContextCandidate

from experiments.full_architecture_context_observation.stage_h8r2at_provider_summary_shadow import (
    _write_receipt,
)
from experiments.full_architecture_context_observation.stage_h8r2av_multi_window_summary_quality import (
    canonical_hash,
)
from experiments.full_architecture_context_observation.stage_h8r2az_reusable_summary_artifact import (
    ZERO_SIDE_EFFECTS,
)
from experiments.full_architecture_context_observation.stage_h8r2bh_builder_sourced_simulation import (
    SECRET_SUMMARY_BODY,
    _admission_for,
    _binding_for_sources,
    _build_context,
    _candidate_digest,
    _select_builder_candidates,
    _sha256_text,
)


SCHEMA = "phase-h8r2bi-token-aware-opt-in-canary-v1"


class _WhitespaceTokenCounter:
    """Deterministic offline token counter for canary accounting."""

    available = True
    tokenizer_id = "offline-whitespace-token-counter-v1"
    model = "offline-builder-canary"

    def count_text(self, text: str) -> int:
        return len(str(text).split())


def run_token_aware_opt_in_canary(*, output_root: Path) -> dict[str, Any]:
    with TemporaryDirectory(prefix="h8r2bi-builder-") as tmp:
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

    counter = _WhitespaceTokenCounter()
    token_policy = ContextAssemblyPolicy(
        max_prompt_chars=5000,
        max_prompt_tokens=1200,
        reserved_prompt_tokens=100,
    )
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
        preflight = preflight_reusable_compaction_prompt_use(
            binding=binding,
            candidates=candidates,
            policy=token_policy,
            renderer=MemoryContextBuilder._render_candidates,
            admission=_admission_for(binding, recent_ids),
            semantic_facts=facts,
            required_candidate_ids=required_ids,
            recent_suffix_ids=recent_ids,
            expected_artifact_integrity_checksum=binding.artifact.integrity_checksum,
            preflight_id="h8r2bi-token-aware-preflight",
            token_counter=counter,
        )
        simulation = simulate_reusable_compaction_prompt_use(
            binding=binding,
            candidates=candidates,
            policy=token_policy,
            renderer=MemoryContextBuilder._render_candidates,
            preflight=preflight,
            simulation_id="token_aware_opt_in_pass",
            token_counter=counter,
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
        "char_delta_positive": (pass_case.get("prompt_char_delta") or 0) > 0,
        "token_delta_positive": (pass_case.get("prompt_token_delta") or 0) > 0,
        "token_counts_present": (
            pass_case.get("raw_final_prompt_tokens") is not None
            and pass_case.get("reusable_final_prompt_tokens") is not None
        ),
        "tokenizer_metadata_present": (
            pass_case.get("token_count_method") == "provider_tokenizer"
            and pass_case.get("tokenizer_id") == counter.tokenizer_id
            and pass_case.get("token_model") == counter.model
        ),
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
        "claim_boundary": "token_aware_builder_adjacent_opt_in_no_production_prompt_mutation",
        "builder_prompt_hash": builder_prompt_hash,
        "builder_context_request_hash": builder_context.get("context_request_hash"),
        "token_policy": token_policy.model_dump(mode="json"),
        "tokenizer": {
            "token_count_method": pass_case.get("token_count_method"),
            "tokenizer_id": pass_case.get("tokenizer_id"),
            "model": pass_case.get("token_model"),
            "provider_usage": False,
            "billing_claim": False,
        },
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
    result = run_token_aware_opt_in_canary(output_root=args.output_root)
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
