"""Production-shaped shadow evidence for reusable summary admission.

This stage maps the H8-R2AZ reusable-summary admission outcomes into the
production metadata surface: ``ContextSelectionMetadata.compaction_reuse_admissions``.
It remains default-off and shadow-only; no reusable summary candidate is added
to the authority prompt and no ``ContextCompactionBinding`` is created.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from metadata import (
    ContextCompactionReuseAdmission,
    ContextCompactionReuseAdmissionStatus,
    ContextCompactionReuseRejectionReason,
    ContextSelectionMetadata,
)

from experiments.full_architecture_context_observation.stage_h8r2at_provider_summary_shadow import (
    _write_receipt,
)
from experiments.full_architecture_context_observation.stage_h8r2av_multi_window_summary_quality import (
    canonical_hash,
)
from experiments.full_architecture_context_observation.stage_h8r2az_reusable_summary_artifact import (
    ZERO_SIDE_EFFECTS,
    admit_reusable_summary_artifact,
    body_free_artifact_receipt,
    build_balanced_reuse_fixture,
)


SCHEMA = "phase-h8r2ba-production-binding-shadow-v1"

_REJECTION_REASON_MAP = {
    "source_candidate_ids_mismatch": ContextCompactionReuseRejectionReason.SOURCE_CANDIDATE_IDS_MISMATCH,
    "source_fingerprint_mismatch": ContextCompactionReuseRejectionReason.SOURCE_FINGERPRINT_MISMATCH,
    "source_binding_hash_mismatch": ContextCompactionReuseRejectionReason.SOURCE_BINDING_HASH_MISMATCH,
    "required_candidate_ids_mismatch": ContextCompactionReuseRejectionReason.REQUIRED_CANDIDATE_IDS_MISMATCH,
    "recent_suffix_ids_mismatch": ContextCompactionReuseRejectionReason.RECENT_SUFFIX_IDS_MISMATCH,
    "session_constraints_hash_mismatch": ContextCompactionReuseRejectionReason.SESSION_CONSTRAINTS_HASH_MISMATCH,
    "artifact_kind_mismatch": ContextCompactionReuseRejectionReason.ARTIFACT_KIND_MISMATCH,
    "artifact_integrity_mismatch": ContextCompactionReuseRejectionReason.ARTIFACT_INTEGRITY_MISMATCH,
    "artifact_contract_invalid": ContextCompactionReuseRejectionReason.ARTIFACT_CONTRACT_INVALID,
    "trial_projection_not_ready": ContextCompactionReuseRejectionReason.TRIAL_PROJECTION_NOT_READY,
    "trial_summary_not_selected": ContextCompactionReuseRejectionReason.TRIAL_SUMMARY_NOT_SELECTED,
    "trial_required_candidate_omitted": ContextCompactionReuseRejectionReason.TRIAL_REQUIRED_CANDIDATE_OMITTED,
    "trial_recent_suffix_omitted": ContextCompactionReuseRejectionReason.TRIAL_RECENT_SUFFIX_OMITTED,
}


def _shadow_admission(
    *,
    admission_id: str,
    artifact: dict[str, Any],
    admission: dict[str, Any],
) -> ContextCompactionReuseAdmission:
    status = (
        ContextCompactionReuseAdmissionStatus.ADMITTED
        if admission.get("status") == "admitted"
        else ContextCompactionReuseAdmissionStatus.REJECTED
    )
    raw_reason = admission.get("reason")
    rejection_reason = None
    if status == ContextCompactionReuseAdmissionStatus.REJECTED:
        rejection_reason = _REJECTION_REASON_MAP.get(str(raw_reason))
        if rejection_reason is None:
            rejection_reason = ContextCompactionReuseRejectionReason.ARTIFACT_CONTRACT_INVALID
    record = artifact["record"]
    return ContextCompactionReuseAdmission(
        admission_id=admission_id,
        status=status,
        rejection_reason=rejection_reason,
        source_candidate_ids=list(record["source_candidate_ids"]),
        source_fingerprint=str(record["source_fingerprint"]),
        source_binding_hash=str(artifact["source_binding_hash"]),
        required_candidate_ids=list(artifact["required_candidate_ids"]),
        recent_suffix_ids=list(artifact["recent_suffix_ids"]),
        session_constraints_hash=str(artifact["session_constraints_hash"]),
        artifact_id=str(artifact["artifact_id"]),
        artifact_kind=str(artifact["kind"]),
        artifact_integrity_checksum=str(artifact["integrity_hash"]),
        generated_summary_fingerprint=str(artifact["summary_sha256"]),
        used_in_prompt=False,
    )


def build_shadow_selection_metadata(
    *,
    artifact: dict[str, Any],
    admissions: list[dict[str, Any]],
    max_prompt_chars: int,
) -> ContextSelectionMetadata:
    reuse_admissions = [
        _shadow_admission(
            admission_id=f"h8r2ba:reuse:{index}",
            artifact=artifact,
            admission=admission,
        )
        for index, admission in enumerate(admissions, start=1)
    ]
    return ContextSelectionMetadata(
        max_prompt_chars=max_prompt_chars,
        original_prompt_chars=0,
        final_prompt_chars=0,
        compaction_reuse_admissions=reuse_admissions,
    )


def run_production_binding_shadow_gate(*, output_root: Path) -> dict[str, Any]:
    snapshot, artifact_model = build_balanced_reuse_fixture()
    # The AZ experiment uses an experiment-specific artifact kind.  Normalize
    # it through the production adapter before constructing the typed shadow
    # admission; otherwise this stage would falsely prove compatibility for a
    # kind that production admission deliberately rejects.
    raw_artifact = body_free_artifact_receipt(artifact_model)
    artifact = {**raw_artifact, "kind": "context_compaction"}
    admitted = admit_reusable_summary_artifact(artifact_model, snapshot=snapshot)
    # Build rejected cases through the canonical AZ runner so reason names stay
    # aligned without copying prompt/source bodies into this receipt.
    from experiments.full_architecture_context_observation.stage_h8r2az_reusable_summary_artifact import (
        run_reusable_summary_artifact_gate,
    )

    az_shadow = run_reusable_summary_artifact_gate(
        output_root=output_root / "az_source_receipt",
        reuse_count=2,
    )
    admissions = [admitted, *[item["admission"] for item in az_shadow["drift_cases"]]]
    selection = build_shadow_selection_metadata(
        artifact=artifact,
        admissions=admissions,
        max_prompt_chars=snapshot.spec.max_prompt_chars,
    )
    serialized = selection.model_dump(mode="json")
    reuse_admissions = serialized["compaction_reuse_admissions"]
    result = {
        "schema": SCHEMA,
        "status": (
            "passed"
            if reuse_admissions
            and reuse_admissions[0]["status"] == "admitted"
            and all(item["used_in_prompt"] is False for item in reuse_admissions)
            and all(item["status"] == "rejected" for item in reuse_admissions[1:])
            and serialized["candidate_decisions"] == []
            and serialized["compaction_attempts"] == []
            else "needs_followup"
        ),
        "claim_boundary": "production_shaped_shadow_only_no_prompt_binding",
        "metadata_surface": "ContextSelectionMetadata.compaction_reuse_admissions",
        "context_selection": serialized,
        "artifact": artifact,
        "az_source_receipt_hash": az_shadow["receipt_hash"],
        "side_effects": dict(ZERO_SIDE_EFFECTS),
        "secret_handling": {"credential_present": False, "serialized": False},
    }
    result["receipt_hash"] = canonical_hash(result)
    _write_receipt(output_root / "aggregate" / "receipt.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run_production_binding_shadow_gate(output_root=args.output_root)
    print(
        json.dumps(
            {
                "status": result["status"],
                "metadata_surface": result["metadata_surface"],
                "reuse_admission_count": len(
                    result["context_selection"]["compaction_reuse_admissions"]
                ),
                "receipt_hash": result["receipt_hash"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SCHEMA",
    "build_shadow_selection_metadata",
    "run_production_binding_shadow_gate",
]
