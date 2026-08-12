from __future__ import annotations

import json

from experiments.full_architecture_context_observation.stage_h8r2bf_prompt_use_preflight import (
    SECRET_SUMMARY_BODY,
    run_prompt_use_preflight_gate,
)


def test_prompt_use_preflight_gate_is_body_free(tmp_path) -> None:
    result = run_prompt_use_preflight_gate(output_root=tmp_path / "bf")

    assert result["status"] == "passed"
    assert all(result["invariants"].values())
    cases = {case["preflight_id"]: case for case in result["cases"]}
    assert cases["pass"]["status"] == "passed"
    assert cases["admission_rejected"]["status"] == "rejected"
    assert cases["source_drift"]["status"] == "rejected"
    assert cases["semantic_missing"]["status"] == "rejected"
    assert cases["semantic_bad_evidence"]["status"] == "rejected"
    assert cases["recent_omitted"]["status"] == "rejected"
    assert cases["trial_not_selected"]["status"] == "rejected"
    assert all(case["used_in_prompt"] is False for case in result["cases"])

    receipt = tmp_path / "bf" / "aggregate" / "receipt.json"
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
    assert SECRET_SUMMARY_BODY not in encoded
    assert "Alpha decision:" not in encoded
    assert "Recent suffix" not in encoded
    assert "sk-" not in encoded
