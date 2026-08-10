from __future__ import annotations

import json

from experiments.full_architecture_context_observation.stage_h8r2be_source_binding_hash_persistence import (
    run_source_binding_hash_persistence_gate,
)


def test_source_binding_hash_persistence_gate_is_body_free(tmp_path) -> None:
    result = run_source_binding_hash_persistence_gate(output_root=tmp_path / "be")

    assert result["status"] == "passed"
    assert all(result["invariants"].values())
    assert result["persisted_source_binding_hash"].startswith("sha256:")
    admissions = result["shadow"]["compaction_reuse_admissions"]
    assert [item["status"] for item in admissions] == [
        "admitted",
        "admitted",
        "rejected",
    ]
    assert admissions[2]["rejection_reason"] == "artifact_contract_invalid"
    assert all(item["used_in_prompt"] is False for item in admissions)
    assert result["baseline"]["prompt_hash"] == result["shadow"]["prompt_hash"]
    assert result["baseline"]["context_request_hash"] == result["shadow"][
        "context_request_hash"
    ]

    receipt = tmp_path / "be" / "aggregate" / "receipt.json"
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
    assert "h8r2be-dialog-body-" not in encoded
    assert "Earlier dialog" not in encoded
    assert "sk-" not in encoded
