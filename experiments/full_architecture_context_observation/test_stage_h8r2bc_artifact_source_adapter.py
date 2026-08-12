from __future__ import annotations

import json
import hashlib

from experiments.full_architecture_context_observation.stage_h8r2bc_artifact_source_adapter import (
    SECRET_SUMMARY_BODY,
    run_artifact_source_adapter_gate,
)


def test_artifact_source_adapter_gate_preserves_prompt_and_writes_body_free_receipt(
    tmp_path,
) -> None:
    result = run_artifact_source_adapter_gate(output_root=tmp_path / "bc")

    assert result["status"] == "passed"
    assert all(result["invariants"].values())
    admissions = result["shadow"]["compaction_reuse_admissions"]
    assert [item["status"] for item in admissions] == ["admitted", "rejected"]
    id_only_source_fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(
            {"source_candidate_ids": result["source_ids"]},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert admissions[0]["source_fingerprint"] != id_only_source_fingerprint
    assert admissions[1]["rejection_reason"] == "artifact_integrity_mismatch"
    assert all(item["used_in_prompt"] is False for item in admissions)
    assert result["baseline"]["prompt_hash"] == result["shadow"]["prompt_hash"]
    assert result["baseline"]["context_request_hash"] == result["shadow"][
        "context_request_hash"
    ]

    receipt = tmp_path / "bc" / "aggregate" / "receipt.json"
    encoded = receipt.read_text(encoding="utf-8")
    payload = json.loads(encoded)
    assert payload["receipt_hash"] == result["receipt_hash"]
    assert "prompt_text" not in encoded
    assert "summary_text" not in encoded
    assert "summary_payload" not in encoded
    assert "response_content" not in encoded
    assert "raw_response" not in encoded
    assert "source_payload" not in encoded
    assert "source_snapshot" not in encoded
    assert "h8r2bc-dialog-body-" not in encoded
    assert SECRET_SUMMARY_BODY not in encoded
    assert "sk-" not in encoded
