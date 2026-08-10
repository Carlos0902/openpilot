from __future__ import annotations

import json

from experiments.full_architecture_context_observation.stage_h8r2bj_discovered_binding_opt_in import (
    run_discovered_binding_opt_in_canary,
)


def test_discovered_binding_opt_in_canary_is_body_free(tmp_path) -> None:
    result = run_discovered_binding_opt_in_canary(output_root=tmp_path / "bj")

    assert result["status"] == "passed"
    assert all(result["invariants"].values())
    assert result["binding_digest"]["source_binding_hash"].startswith("sha256:")
    assert result["admissions"][0]["status"] == "admitted"
    assert result["admissions"][0]["used_in_prompt"] is False
    case = result["cases"][0]
    assert case["simulation_id"] == "discovered_binding_opt_in_pass"
    assert case["status"] == "passed"
    assert case["prompt_char_delta"] > 0
    assert case["prompt_token_delta"] > 0
    assert case["used_in_prompt"] is False

    receipt = tmp_path / "bj" / "aggregate" / "receipt.json"
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
    assert "Decision 0:" not in encoded
    assert "supporting-token" not in encoded
    assert "sk-" not in encoded
