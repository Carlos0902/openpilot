"""Run the default-off MemoryContextBuilder reusable-summary shadow gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.short_memory import ShortMemory
from metadata import (
    ContextCompactionReuseAdmission,
    ContextCompactionReuseAdmissionStatus,
    ContextCompactionReuseRejectionReason,
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


SCHEMA = "phase-h8r2bb-builder-shadow-injection-v1"


def _seed_dialog(builder: MemoryContextBuilder, count: int = 8) -> None:
    for index in range(count):
        builder.short_memory.add_message(
            "assistant",
            f"h8r2bb-dialog-{index}-" + (str(index) * 120),
        )


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _admission_from_shadow_payload(
    payload: dict[str, Any],
    *,
    admission_id: str,
    status: ContextCompactionReuseAdmissionStatus,
    rejection_reason: ContextCompactionReuseRejectionReason | None = None,
) -> ContextCompactionReuseAdmission:
    source_ids = [
        item["candidate_id"]
        for item in payload.get("candidate_digests", [])
        if item.get("kind") == "dialog"
    ][:2]
    if len(source_ids) < 2:
        source_ids = ["dialog-1", "dialog-2"]
    source_fingerprint = canonical_hash({"source_candidate_ids": source_ids})
    if rejection_reason == ContextCompactionReuseRejectionReason.SOURCE_FINGERPRINT_MISMATCH:
        source_fingerprint = "sha256:" + "9" * 64
    source_binding_hash = canonical_hash(
        {
            "source_candidate_ids": source_ids,
            "session_constraints_hash": payload.get("session_constraints_hash", ""),
        }
    )
    return ContextCompactionReuseAdmission(
        admission_id=admission_id,
        status=status,
        rejection_reason=rejection_reason,
        source_candidate_ids=source_ids,
        source_fingerprint=source_fingerprint,
        source_binding_hash=source_binding_hash,
        required_candidate_ids=[],
        recent_suffix_ids=list(source_ids[-2:]),
        session_constraints_hash=(
            payload["session_constraints_hash"]
            if payload.get("session_constraints_hash")
            else None
        ),
        artifact_id=f"{admission_id}:artifact",
        artifact_kind="context_compaction",
        artifact_integrity_checksum=canonical_hash(
            {"artifact": admission_id, "source_binding_hash": source_binding_hash}
        ),
        generated_summary_fingerprint=canonical_hash(
            {"summary": admission_id, "source_ids": source_ids}
        ),
        used_in_prompt=False,
    )


def _context_digest(context: dict[str, Any]) -> dict[str, Any]:
    candidates = context.get("selected_context_candidates") or []
    return {
        "context_request_hash": context.get("context_request_hash"),
        "prompt_hash": _sha256_text(str(context.get("prompt_text", ""))),
        "prompt_chars": len(str(context.get("prompt_text", ""))),
        "selected_candidate_ids": [
            candidate.get("candidate_id") for candidate in candidates
        ],
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
        "compaction_attempt_count": len(
            (context.get("context_selection") or {}).get("compaction_attempts") or []
        ),
    }


def run_builder_shadow_injection_gate(*, output_root: Path) -> dict[str, Any]:
    builder = MemoryContextBuilder(
        short_memory=ShortMemory(repo_path=output_root / "fixture_short_memory"),
        memory_store=MemoryStore(output_root / "fixture_memory"),
        max_prompt_chars=620,
    )
    _seed_dialog(builder)
    baseline = builder.build(
        "builder reuse shadow",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )
    provider_payloads: list[dict[str, Any]] = []

    def provider(payload: dict[str, Any]) -> list[ContextCompactionReuseAdmission]:
        provider_payloads.append(payload)
        return [
            _admission_from_shadow_payload(
                payload,
                admission_id="h8r2bb:reuse:1",
                status=ContextCompactionReuseAdmissionStatus.ADMITTED,
            ),
            _admission_from_shadow_payload(
                payload,
                admission_id="h8r2bb:reuse:2",
                status=ContextCompactionReuseAdmissionStatus.REJECTED,
                rejection_reason=(
                    ContextCompactionReuseRejectionReason.SOURCE_FINGERPRINT_MISMATCH
                ),
            ),
        ]

    builder.compaction_reuse_shadow_provider = provider
    shadow = builder.build(
        "builder reuse shadow",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )
    baseline_digest = _context_digest(baseline)
    shadow_digest = _context_digest(shadow)
    provider_payload = provider_payloads[0] if provider_payloads else {}
    provider_payload_encoded = json.dumps(
        provider_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    admissions = shadow_digest["compaction_reuse_admissions"]
    invariant = {
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
        "shadow_payload_body_free": (
            "prompt_text" not in provider_payload_encoded
            and "h8r2bb-dialog-0-" not in provider_payload_encoded
        ),
        "all_shadow_admissions_not_prompt_used": all(
            item.get("used_in_prompt") is False for item in admissions
        ),
    }
    result = {
        "schema": SCHEMA,
        "status": (
            "passed"
            if provider_payloads
            and len(admissions) == 2
            and admissions[0]["status"] == "admitted"
            and admissions[1]["status"] == "rejected"
            and all(invariant.values())
            else "needs_followup"
        ),
        "claim_boundary": "default_off_builder_shadow_no_prompt_use",
        "baseline": baseline_digest,
        "shadow": shadow_digest,
        "provider_payload_hash": canonical_hash(provider_payload),
        "invariants": invariant,
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
    result = run_builder_shadow_injection_gate(output_root=args.output_root)
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


__all__ = ["SCHEMA", "run_builder_shadow_injection_gate"]
