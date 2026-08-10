from __future__ import annotations

import json

from core.config import LLMSettings
from core.llm import LLMResponse
from metadata import ReasoningCapabilityProfileId

from experiments.full_architecture_context_observation.stage_h8r2bl_read_only_confirmation_matrix import (
    MATRIX_CASES,
    run_read_only_confirmation_matrix,
)


class FakeMatrixClient:
    def __init__(self, *_args, **_kwargs) -> None:
        self.requests = []

    def complete(self, request, **_kwargs):
        self.requests.append(request)
        case_id = request.trace_info["case_id"]
        case = next(item for item in MATRIX_CASES if item.case_id == case_id)
        payload = {
            fact.response_key: fact.marker.split("=", 1)[1] for fact in case.facts
        }
        prompt_tokens = sum(len(message.content.split()) for message in request.messages)
        return LLMResponse(
            content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            parsed_json=payload,
            model="fake-deepseek-v4-flash",
            provider="openai-compatible",
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 40,
                "total_tokens": prompt_tokens + 40,
            },
            finish_reason="stop",
        )


def _settings() -> LLMSettings:
    return LLMSettings(
        _env_file=None,
        provider="openai-compatible",
        base_url="https://api.deepseek.com",
        api_key="test-key",
        model="fake-deepseek-v4-flash",
        temperature=0.0,
        transport_retries=0,
        reasoning_capability_profile=ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
    )


def test_read_only_confirmation_matrix_mock_is_body_free(tmp_path) -> None:
    result = run_read_only_confirmation_matrix(
        output_root=tmp_path / "bl",
        settings_factory=_settings,
        client_factory=lambda _settings: FakeMatrixClient(),
    )

    assert result["status"] == "passed"
    assert result["case_count"] == 3
    assert result["side_effects"]["provider_calls"] == 6
    assert result["aggregate_usage"]["prompt_token_delta"] > 0
    assert result["aggregate_usage"]["total_token_delta"] > 0
    assert all(result["invariants"].values())
    assert all(case["status"] == "passed" for case in result["cases"])
    for case in result["cases"]:
        raw_usage = case["arms"]["raw"]["attempt"]["usage"]
        reusable_usage = case["arms"]["reusable"]["attempt"]["usage"]
        assert reusable_usage["prompt_tokens"] < raw_usage["prompt_tokens"]
        assert case["arms"]["raw"]["quality"]["all_facts_covered"] is True
        assert case["arms"]["reusable"]["quality"]["all_facts_covered"] is True

    receipt = tmp_path / "bl" / "aggregate" / "receipt.json"
    encoded = receipt.read_text(encoding="utf-8")
    payload = json.loads(encoded)
    assert payload["receipt_hash"] == result["receipt_hash"]
    assert "prompt_text" not in encoded
    assert "\"content\"" not in encoded
    assert "summary_text" not in encoded
    assert "response_content" not in encoded
    assert "raw_response" not in encoded
    assert "low-value-" not in encoded
    assert "scoped_target=calculator.py" not in encoded
    assert "calculator.py" not in encoded
    assert "file_reader" not in encoded
    assert "evidence_ids_only" not in encoded
    assert "sk-" not in encoded


def test_read_only_confirmation_matrix_blocks_without_settings(tmp_path) -> None:
    calls = {"client_factory": 0}

    def missing_settings() -> LLMSettings:
        raise ValueError("missing test credential")

    def client_factory(_settings: LLMSettings):
        calls["client_factory"] += 1
        return FakeMatrixClient()

    result = run_read_only_confirmation_matrix(
        output_root=tmp_path / "bl-blocked",
        settings_factory=missing_settings,
        client_factory=client_factory,
    )

    assert result["status"] == "blocked"
    assert result["side_effects"]["provider_calls"] == 0
    assert result["side_effects"]["provider_transport_attempted"] is False
    assert result["provider_descriptor"]["credential_serialized"] is False
    assert calls["client_factory"] == 0
    assert all(case["arms"] == {} for case in result["cases"])
