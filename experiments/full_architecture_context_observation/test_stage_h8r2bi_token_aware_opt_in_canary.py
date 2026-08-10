from __future__ import annotations

import json

from experiments.full_architecture_context_observation.stage_h8r2bh_builder_sourced_simulation import (
    SECRET_SUMMARY_BODY,
)
from experiments.full_architecture_context_observation.stage_h8r2bi_token_aware_opt_in_canary import (
    run_token_aware_opt_in_canary,
)


def test_token_aware_opt_in_canary_is_body_free(tmp_path) -> None:
    result = run_token_aware_opt_in_canary(output_root=tmp_path / "bi")

    assert result["status"] == "passed"
    assert all(result["invariants"].values())
    case = result["cases"][0]
    assert case["simulation_id"] == "token_aware_opt_in_pass"
    assert case["status"] == "passed"
    assert case["prompt_char_delta"] > 0
    assert case["prompt_token_delta"] > 0
    assert case["token_count_method"] == "provider_tokenizer"
    assert case["tokenizer_id"] == "offline-whitespace-token-counter-v1"
    assert case["token_model"] == "offline-builder-canary"
    assert case["used_in_prompt"] is False

    receipt = tmp_path / "bi" / "aggregate" / "receipt.json"
    encoded = receipt.read_text(encoding="utf-8")
    payload = json.loads(encoded)
    assert payload["receipt_hash"] == result["receipt_hash"]
    assert payload["tokenizer"]["provider_usage"] is False
    assert payload["tokenizer"]["billing_claim"] is False
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
