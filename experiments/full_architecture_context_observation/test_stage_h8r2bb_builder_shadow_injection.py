from __future__ import annotations

import json

from experiments.full_architecture_context_observation.stage_h8r2bb_builder_shadow_injection import (
    run_builder_shadow_injection_gate,
)


def test_builder_shadow_injection_gate_preserves_prompt_and_writes_body_free_receipt(
    tmp_path,
) -> None:
    result = run_builder_shadow_injection_gate(output_root=tmp_path / "bb")

    assert result["status"] == "passed"
    assert all(result["invariants"].values())
    admissions = result["shadow"]["compaction_reuse_admissions"]
    assert [item["status"] for item in admissions] == ["admitted", "rejected"]
    assert all(item["used_in_prompt"] is False for item in admissions)
    assert result["baseline"]["prompt_hash"] == result["shadow"]["prompt_hash"]
    assert result["baseline"]["context_request_hash"] == result["shadow"][
        "context_request_hash"
    ]
    assert result["baseline"]["selected_candidate_digests"] == result["shadow"][
        "selected_candidate_digests"
    ]

    receipt = tmp_path / "bb" / "aggregate" / "receipt.json"
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
    assert "h8r2bb-dialog-0-" not in encoded
    assert "sk-" not in encoded
