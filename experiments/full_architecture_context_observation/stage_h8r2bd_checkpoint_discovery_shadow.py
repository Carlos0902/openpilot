"""Run the checkpoint compaction discovery shadow gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from memory.compaction_reuse import (
    build_checkpoint_compaction_reuse_shadow_provider,
    source_binding_hash_from_shadow_payload,
)
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


SCHEMA = "phase-h8r2bd-checkpoint-discovery-shadow-v1"
SECRET_SUMMARY_BODY = "h8r2bd secret checkpoint summary body"


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed_dialog(builder: MemoryContextBuilder, count: int = 6) -> None:
    for index in range(count):
        builder.short_memory.add_message(
            "assistant",
            f"h8r2bd-dialog-body-{index}-" + (str(index) * 80),
        )


def _context_digest(context: dict[str, Any]) -> dict[str, Any]:
    candidates = context.get("selected_context_candidates") or []
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
        "context_compaction_count": len(context.get("context_compactions") or []),
        "compaction_reuse_admissions": list(
            (context.get("context_selection") or {}).get("compaction_reuse_admissions")
            or []
        ),
    }


def _capture_shadow_payload(builder: MemoryContextBuilder) -> tuple[dict[str, Any], dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def capture(payload: dict[str, Any]) -> list[Any]:
        captured.append(payload)
        return []

    builder.compaction_reuse_shadow_provider = capture
    context = builder.build(
        "checkpoint discovery shadow",
        include_environment=False,
        limit=6,
        system_prompt="Preserve checkpoint reuse boundaries.",
    )
    if not captured:
        raise ValueError("builder did not call the shadow provider")
    builder.compaction_reuse_shadow_provider = None
    return context, captured[0]


def _source_ids_from_payload(payload: dict[str, Any]) -> list[str]:
    source_ids = [
        item["candidate_id"]
        for item in payload.get("candidate_digests", [])
        if item.get("kind") == "dialog"
    ][:2]
    if len(source_ids) < 2:
        raise ValueError("fixture did not expose two dialog candidates")
    return source_ids


def _binding_for_sources(
    source_ids: list[str],
    suffix: str,
    *,
    source_fingerprint: str,
) -> ContextCompactionBinding:
    """Build a checkpoint fixture from the builder's authoritative source view."""

    if not source_fingerprint.startswith("sha256:"):
        raise ValueError("fixture source fingerprint must be a sha256 digest")
    summary = f"{SECRET_SUMMARY_BODY} {suffix}"
    record = ContextCompactionRecord(
        compaction_id=f"h8r2bd-compact-{suffix}",
        source_fingerprint=source_fingerprint,
        source_candidate_ids=source_ids,
        algorithm="deterministic_observation_mask_v1",
        summary=summary,
        original_chars=200,
        compacted_chars=len(summary),
    )
    return ContextCompactionBinding(
        record=record,
        artifact=DurableArtifactReference(
            artifact_id=f"h8r2bd-artifact-{suffix}",
            kind="context_compaction",
            integrity_checksum=canonical_hash({"artifact": f"h8r2bd-artifact-{suffix}"}),
            bytes=123,
        ),
    )


def _snapshot_from_baseline(
    baseline: dict[str, Any],
    baseline_digest: dict[str, Any],
    bindings: list[ContextCompactionBinding],
) -> RuntimePromptContextSnapshot:
    return RuntimePromptContextSnapshot(
        context_id="h8r2bd-context-1",
        request_hash=str(baseline_digest["context_request_hash"]),
        prompt_hash=str(baseline_digest["prompt_hash"]),
        selection=ContextSelectionMetadata.model_validate(baseline["context_selection"]),
        context_artifact=DurableArtifactReference(
            artifact_id="h8r2bd-prompt-context-1",
            kind="prompt_context",
            integrity_checksum=canonical_hash(
                {"prompt_hash": baseline_digest["prompt_hash"]}
            ),
            bytes=int(baseline_digest["prompt_chars"]),
        ),
        compaction_bindings=bindings,
    )


def run_checkpoint_discovery_shadow_gate(*, output_root: Path) -> dict[str, Any]:
    short_memory = ShortMemory(repo_path=output_root / "fixture_short_memory")
    baseline_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(output_root / "fixture_memory_baseline"),
        max_prompt_chars=1200,
    )
    _seed_dialog(baseline_builder)
    baseline = baseline_builder.build(
        "checkpoint discovery shadow",
        include_environment=False,
        limit=6,
        system_prompt="Preserve checkpoint reuse boundaries.",
    )
    capture_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(output_root / "fixture_memory_capture"),
        max_prompt_chars=1200,
    )
    _, shadow_payload = _capture_shadow_payload(capture_builder)
    source_ids = _source_ids_from_payload(shadow_payload)
    source_binding_hash = source_binding_hash_from_shadow_payload(shadow_payload, source_ids)
    if source_binding_hash is None:
        raise ValueError("source binding hash could not be computed")
    source_fingerprints = shadow_payload.get("source_fingerprint_by_candidate_ids")
    source_fingerprint_key = json.dumps(source_ids, ensure_ascii=False, separators=(",", ":"))
    source_fingerprint = (
        source_fingerprints.get(source_fingerprint_key)
        if isinstance(source_fingerprints, dict)
        else None
    )
    if not isinstance(source_fingerprint, str):
        raise ValueError("authoritative fixture source fingerprint is missing")

    bindings = [
        _binding_for_sources(source_ids, "admit", source_fingerprint=source_fingerprint),
        _binding_for_sources(source_ids, "missing", source_fingerprint=source_fingerprint),
        _binding_for_sources(source_ids, "stale", source_fingerprint=source_fingerprint),
        _binding_for_sources(source_ids, "checksum", source_fingerprint=source_fingerprint),
    ]
    baseline_digest = _context_digest(baseline)
    snapshot = _snapshot_from_baseline(baseline, baseline_digest, bindings)
    provider = build_checkpoint_compaction_reuse_shadow_provider(
        snapshot,
        source_binding_hashes={
            "h8r2bd-compact-admit": source_binding_hash,
            "h8r2bd-compact-stale": "sha256:" + "8" * 64,
            "h8r2bd-compact-checksum": source_binding_hash,
        },
        recent_suffix_ids_by_compaction_id={
            binding.record.compaction_id: source_ids[-1:] for binding in bindings
        },
        expected_artifact_integrity_checksums={
            "h8r2bd-artifact-checksum": "sha256:" + "9" * 64
        },
    )
    shadow_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(output_root / "fixture_memory_shadow"),
        max_prompt_chars=1200,
        compaction_reuse_shadow_provider=provider,
    )
    shadow = shadow_builder.build(
        "checkpoint discovery shadow",
        include_environment=False,
        limit=6,
        system_prompt="Preserve checkpoint reuse boundaries.",
    )
    shadow_digest = _context_digest(shadow)
    admissions = shadow_digest["compaction_reuse_admissions"]
    reasons = [item.get("rejection_reason") for item in admissions]
    invariants = {
        "request_hash_unchanged": (
            baseline_digest["context_request_hash"] == shadow_digest["context_request_hash"]
        ),
        "prompt_hash_unchanged": baseline_digest["prompt_hash"] == shadow_digest["prompt_hash"],
        "selected_candidates_unchanged": (
            baseline_digest["selected_candidate_digests"]
            == shadow_digest["selected_candidate_digests"]
        ),
        "context_compactions_unchanged": (
            baseline_digest["context_compaction_count"]
            == shadow_digest["context_compaction_count"]
        ),
        "admission_matrix_expected": [
            item.get("status") for item in admissions
        ]
        == ["admitted", "rejected", "rejected", "rejected"]
        and reasons
        == [
            None,
            "artifact_contract_invalid",
            "source_binding_hash_mismatch",
            "artifact_integrity_mismatch",
        ],
        "all_shadow_not_prompt_used": bool(admissions)
        and all(item.get("used_in_prompt") is False for item in admissions),
    }
    result = {
        "schema": SCHEMA,
        "status": (
            "passed"
            if len(admissions) == 4 and all(invariants.values())
            else "needs_followup"
        ),
        "claim_boundary": "default_off_checkpoint_discovery_shadow_no_prompt_use",
        "baseline": baseline_digest,
        "shadow": shadow_digest,
        "source_ids": list(source_ids),
        "source_binding_hash": source_binding_hash,
        "snapshot_binding_digests": [
            {
                "compaction_id": binding.record.compaction_id,
                "artifact_id": binding.artifact.artifact_id,
                "source_candidate_ids": list(binding.record.source_candidate_ids),
                "summary_fingerprint": _sha256_text(binding.record.summary),
                "artifact_integrity_checksum": binding.artifact.integrity_checksum,
            }
            for binding in bindings
        ],
        "captured_shadow_payload_hash": canonical_hash(shadow_payload),
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
    result = run_checkpoint_discovery_shadow_gate(output_root=args.output_root)
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
