from __future__ import annotations

import json

from experiments.full_architecture_context_observation.stage_h8r2az_reusable_summary_artifact import (
    admit_reusable_summary_artifact,
    body_free_artifact_receipt,
    build_balanced_reuse_fixture,
)
from experiments.full_architecture_context_observation.stage_h8r2ba_production_binding_shadow import (
    build_shadow_selection_metadata,
    run_production_binding_shadow_gate,
)


def test_shadow_selection_metadata_records_reuse_without_prompt_authority() -> None:
    snapshot, artifact_model = build_balanced_reuse_fixture()
    artifact = {
        **body_free_artifact_receipt(artifact_model),
        "kind": "context_compaction",
    }
    admitted = admit_reusable_summary_artifact(artifact_model, snapshot=snapshot)
    rejected = {
        **admitted,
        "status": "rejected",
        "reason": "source_fingerprint_mismatch",
        "trial_projection": None,
    }

    selection = build_shadow_selection_metadata(
        artifact=artifact,
        admissions=[admitted, rejected],
        max_prompt_chars=snapshot.spec.max_prompt_chars,
    )
    payload = selection.model_dump(mode="json")

    assert payload["compaction_reuse_admissions"][0]["status"] == "admitted"
    assert payload["compaction_reuse_admissions"][0]["rejection_reason"] is None
    assert payload["compaction_reuse_admissions"][1]["status"] == "rejected"
    assert payload["compaction_reuse_admissions"][1]["rejection_reason"] == (
        "source_fingerprint_mismatch"
    )
    assert all(
        item["used_in_prompt"] is False
        for item in payload["compaction_reuse_admissions"]
    )
    assert payload["compaction_attempts"] == []
    assert payload["candidate_decisions"] == []


def test_production_binding_shadow_gate_writes_body_free_receipt(tmp_path) -> None:
    result = run_production_binding_shadow_gate(output_root=tmp_path / "ba")

    assert result["status"] == "passed"
    admissions = result["context_selection"]["compaction_reuse_admissions"]
    assert len(admissions) == 10
    assert admissions[0]["status"] == "admitted"
    assert [item["status"] for item in admissions[1:]] == ["rejected"] * 9
    assert all(item["used_in_prompt"] is False for item in admissions)
    assert result["context_selection"]["candidate_decisions"] == []
    assert result["context_selection"]["compaction_attempts"] == []

    receipt = tmp_path / "ba" / "aggregate" / "receipt.json"
    encoded = receipt.read_text(encoding="utf-8")
    payload = json.loads(encoded)
    assert payload["receipt_hash"] == result["receipt_hash"]
    assert "prompt_text" not in encoded
    assert "summary_text" not in encoded
    assert "summary_payload" not in encoded
    assert "response_content" not in encoded
    assert "raw_response" not in encoded
    assert "source_snapshot" not in encoded
    assert "balanced-history preserves" not in encoded
    assert "retain confirmed constraints" not in encoded
    assert "sk-" not in encoded
