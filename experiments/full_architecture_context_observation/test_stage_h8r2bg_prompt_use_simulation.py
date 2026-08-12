from __future__ import annotations

import json

from experiments.full_architecture_context_observation.stage_h8r2bg_prompt_use_simulation import (
    SECRET_SUMMARY_BODY,
    run_prompt_use_simulation_gate,
)


def test_prompt_use_simulation_gate_is_body_free(tmp_path) -> None:
    result = run_prompt_use_simulation_gate(output_root=tmp_path / "bg")

    assert result["status"] == "passed"
    assert all(result["invariants"].values())
    cases = {case["simulation_id"]: case for case in result["cases"]}
    assert cases["pass"]["status"] == "passed"
    assert cases["pass"]["prompt_char_delta"] > 0
    assert cases["preflight_rejected"]["status"] == "rejected"
    assert cases["preflight_rejected"]["raw_prompt_hash"] is None
    assert cases["source_drift"]["status"] == "rejected"
    assert cases["source_drift"]["raw_prompt_hash"] is None
    assert cases["summary_fallback"]["status"] == "rejected"
    assert cases["no_benefit"]["status"] == "rejected"
    assert all(case["used_in_prompt"] is False for case in result["cases"])

    receipt = tmp_path / "bg" / "aggregate" / "receipt.json"
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
