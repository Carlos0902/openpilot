"""Run the source-binding hash persistence shadow gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from memory.compaction_reuse import build_checkpoint_compaction_reuse_shadow_provider
from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.short_memory import ShortMemory
from metadata import (
    ContextCompactionBinding,
    ContextCompactionRecord,
    ContextSelectionMetadata,
    DurableArtifactReference,
    RuntimePromptContextSnapshot,
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


SCHEMA = "phase-h8r2be-source-binding-hash-persistence-v1"


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed_dialog(builder: MemoryContextBuilder, count: int = 8) -> None:
    for index in range(count):
        builder.short_memory.add_message(
            "assistant",
            f"h8r2be-dialog-body-{index}-" + (str(index) * 140),
        )


def _compaction_sink(record: dict[str, Any]) -> DurableArtifactReference:
    artifact_id = f"h8r2be-artifact-{record['compaction_id']}"
    return DurableArtifactReference(
        artifact_id=artifact_id,
        kind="context_compaction",
        integrity_checksum=canonical_hash(record),
        bytes=len(json.dumps(record, ensure_ascii=False, sort_keys=True)),
    )


def _context_digest(context: dict[str, Any]) -> dict[str, Any]:
    candidates = context.get("selected_context_candidates") or []
    bindings = [
        ContextCompactionBinding.model_validate(item)
        for item in context.get("context_compactions") or []
    ]
    return {
        "context_request_hash": context.get("context_request_hash"),
        "prompt_hash": _sha256_text(str(context.get("prompt_text", ""))),
        "prompt_chars": len(str(context.get("prompt_text", ""))),
        "selected_candidate_digests": [
            {
                "candidate_id": candidate.get("candidate_id"),
                "kind": candidate.get("kind"),
                "source_id": candidate.get("source_id"),
                "content_sha256": _sha256_text(str(candidate.get("content", ""))),
                "compacted_candidate_ids": list(candidate.get("compacted_candidate_ids") or []),
            }
            for candidate in candidates
        ],
        "context_compaction_binding_digests": [
            {
                "compaction_id": binding.record.compaction_id,
                "artifact_id": binding.artifact.artifact_id,
                "source_candidate_ids": list(binding.record.source_candidate_ids),
                "source_fingerprint": binding.record.source_fingerprint,
                "source_binding_hash": binding.source_binding_hash,
                "artifact_integrity_checksum": binding.artifact.integrity_checksum,
                "record_summary_fingerprint": _sha256_text(binding.record.summary),
            }
            for binding in bindings
        ],
        "compaction_reuse_admissions": list(
            (context.get("context_selection") or {}).get("compaction_reuse_admissions")
            or []
        ),
    }


def _snapshot_from_context(context: dict[str, Any], digest: dict[str, Any]) -> RuntimePromptContextSnapshot:
    return RuntimePromptContextSnapshot(
        context_id="h8r2be-context-1",
        request_hash=str(digest["context_request_hash"]),
        prompt_hash=str(digest["prompt_hash"]),
        selection=ContextSelectionMetadata.model_validate(context["context_selection"]),
        context_artifact=DurableArtifactReference(
            artifact_id="h8r2be-prompt-context-1",
            kind="prompt_context",
            integrity_checksum=canonical_hash({"prompt_hash": digest["prompt_hash"]}),
            bytes=int(digest["prompt_chars"]),
        ),
        compaction_bindings=[
            ContextCompactionBinding.model_validate(item)
            for item in context.get("context_compactions") or []
        ],
    )


def _clone_binding(
    binding: ContextCompactionBinding,
    *,
    suffix: str,
    source_binding_hash: str,
) -> ContextCompactionBinding:
    record = ContextCompactionRecord.model_validate(
        {
            **binding.record.model_dump(mode="json"),
            "compaction_id": f"{binding.record.compaction_id}-{suffix}",
        }
    )
    return ContextCompactionBinding(
        record=record,
        artifact=DurableArtifactReference(
            artifact_id=f"{binding.artifact.artifact_id}-{suffix}",
            kind="context_compaction",
            integrity_checksum=binding.artifact.integrity_checksum,
            bytes=binding.artifact.bytes,
        ),
        source_binding_hash=source_binding_hash,
    )


def run_source_binding_hash_persistence_gate(*, output_root: Path) -> dict[str, Any]:
    short_memory = ShortMemory(repo_path=output_root / "fixture_short_memory")
    baseline_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(output_root / "fixture_memory_baseline"),
        max_prompt_chars=760,
    )
    baseline_builder.set_checkpoint_handlers(compaction_sink=_compaction_sink)
    _seed_dialog(baseline_builder)
    baseline = baseline_builder.build(
        "source binding persistence",
        include_environment=False,
        limit=8,
        system_prompt="Preserve reusable compaction binding identity.",
    )
    baseline_digest = _context_digest(baseline)
    snapshot = _snapshot_from_context(baseline, baseline_digest)
    if not snapshot.compaction_bindings:
        raise ValueError("baseline did not produce a compaction binding")
    persisted_binding = snapshot.compaction_bindings[0]
    if not persisted_binding.source_binding_hash:
        raise ValueError("baseline compaction binding did not persist source_binding_hash")

    historical_binding = _clone_binding(
        persisted_binding,
        suffix="historical",
        source_binding_hash="",
    )
    compatible_snapshot = RuntimePromptContextSnapshot(
        **{
            **snapshot.model_dump(mode="python"),
            "compaction_bindings": [historical_binding],
        }
    )
    compatible_provider = build_checkpoint_compaction_reuse_shadow_provider(
        compatible_snapshot,
        source_binding_hashes={
            historical_binding.record.compaction_id: persisted_binding.source_binding_hash
        },
    )
    compatible_shadow_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(output_root / "fixture_memory_compatible"),
        max_prompt_chars=760,
        compaction_reuse_shadow_provider=compatible_provider,
    )
    compatible_shadow_builder.set_checkpoint_handlers(compaction_sink=_compaction_sink)
    compatible_shadow = compatible_shadow_builder.build(
        "source binding persistence",
        include_environment=False,
        limit=8,
        system_prompt="Preserve reusable compaction binding identity.",
    )

    conflict_binding = _clone_binding(
        persisted_binding,
        suffix="conflict",
        source_binding_hash=persisted_binding.source_binding_hash,
    )
    matrix_snapshot = RuntimePromptContextSnapshot(
        **{
            **snapshot.model_dump(mode="python"),
            "compaction_bindings": [
                persisted_binding,
                historical_binding,
                conflict_binding,
            ],
        }
    )
    matrix_provider = build_checkpoint_compaction_reuse_shadow_provider(
        matrix_snapshot,
        source_binding_hashes={
            historical_binding.record.compaction_id: persisted_binding.source_binding_hash,
            conflict_binding.record.compaction_id: "sha256:" + "8" * 64,
        },
    )
    shadow_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(output_root / "fixture_memory_shadow"),
        max_prompt_chars=760,
        compaction_reuse_shadow_provider=matrix_provider,
    )
    shadow_builder.set_checkpoint_handlers(compaction_sink=_compaction_sink)
    shadow = shadow_builder.build(
        "source binding persistence",
        include_environment=False,
        limit=8,
        system_prompt="Preserve reusable compaction binding identity.",
    )
    shadow_digest = _context_digest(shadow)
    compatible_digest = _context_digest(compatible_shadow)
    admissions = shadow_digest["compaction_reuse_admissions"]
    compatible_admissions = compatible_digest["compaction_reuse_admissions"]
    invariants = {
        "new_binding_persisted_source_binding_hash": (
            bool(persisted_binding.source_binding_hash)
            and persisted_binding.source_binding_hash.startswith("sha256:")
        ),
        "request_hash_unchanged": (
            baseline_digest["context_request_hash"] == shadow_digest["context_request_hash"]
        ),
        "prompt_hash_unchanged": baseline_digest["prompt_hash"] == shadow_digest["prompt_hash"],
        "selected_candidates_unchanged": (
            baseline_digest["selected_candidate_digests"]
            == shadow_digest["selected_candidate_digests"]
        ),
        "context_compactions_preserve_persisted_hash": (
            baseline_digest["context_compaction_binding_digests"]
            == shadow_digest["context_compaction_binding_digests"]
        ),
        "persisted_and_historical_compat_admit": [
            item.get("status") for item in admissions[:2]
        ]
        == ["admitted", "admitted"],
        "conflicting_external_hash_rejects": (
            len(admissions) == 3
            and admissions[2].get("status") == "rejected"
            and admissions[2].get("rejection_reason") == "artifact_contract_invalid"
        ),
        "historical_compat_alone_admits": (
            len(compatible_admissions) == 1
            and compatible_admissions[0].get("status") == "admitted"
        ),
        "all_shadow_not_prompt_used": bool(admissions)
        and all(item.get("used_in_prompt") is False for item in admissions)
        and all(item.get("used_in_prompt") is False for item in compatible_admissions),
    }
    result = {
        "schema": SCHEMA,
        "status": "passed" if all(invariants.values()) else "needs_followup",
        "claim_boundary": "persisted_source_binding_hash_for_checkpoint_discovery_shadow",
        "baseline": baseline_digest,
        "shadow": shadow_digest,
        "compatible_historical_shadow": compatible_digest,
        "persisted_source_binding_hash": persisted_binding.source_binding_hash,
        "snapshot_binding_count": len(matrix_snapshot.compaction_bindings),
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
    result = run_source_binding_hash_persistence_gate(output_root=args.output_root)
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
