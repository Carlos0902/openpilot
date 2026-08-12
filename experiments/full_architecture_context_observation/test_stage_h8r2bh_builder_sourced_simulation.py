from __future__ import annotations

import json

from experiments.full_architecture_context_observation.stage_h8r2bh_builder_sourced_simulation import (
    SECRET_SUMMARY_BODY,
    run_builder_sourced_simulation_gate,
)


def test_builder_sourced_simulation_gate_is_body_free(tmp_path) -> None:
    result = run_builder_sourced_simulation_gate(output_root=tmp_path / "bh")

    assert result["status"] == "passed"
    assert all(result["invariants"].values())
    assert result["builder_prompt_hash"].startswith("sha256:")
    assert result["invariants"]["builder_context_compactions_unchanged"] is True
    case = result["cases"][0]
    assert case["simulation_id"] == "builder_sourced_pass"
    assert case["status"] == "passed"
    assert case["prompt_char_delta"] > 0
    assert case["used_in_prompt"] is False

    receipt = tmp_path / "bh" / "aggregate" / "receipt.json"
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
