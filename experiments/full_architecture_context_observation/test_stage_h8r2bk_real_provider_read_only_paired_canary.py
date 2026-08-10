from __future__ import annotations

import json

from core.config import LLMSettings
from core.llm import LLMResponse
from metadata import ReasoningCapabilityProfileId

from experiments.full_architecture_context_observation.stage_h8r2bk_real_provider_read_only_paired_canary import (
    run_real_provider_read_only_paired_canary,
)


class FakePairedClient:
    def __init__(self, *_args, **_kwargs) -> None:
        self.requests = []

    def complete(self, request, **_kwargs):
        self.requests.append(request)
        prompt_tokens = sum(len(message.content.split()) for message in request.messages)
        response = {
            "scoped_target": "calculator.py",
            "forbidden_target": "README.md",
            "api_rule": "preserve existing API behavior",
            "validation_command": "python -m pytest -q",
            "confidence": "high",
        }
        return LLMResponse(
            content=json.dumps(response, ensure_ascii=False, sort_keys=True),
            parsed_json=response,
            model="fake-deepseek-v4-flash",
            provider="openai-compatible",
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 42,
                "total_tokens": prompt_tokens + 42,
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


def test_real_provider_read_only_paired_canary_mock_is_body_free(tmp_path) -> None:
    result = run_real_provider_read_only_paired_canary(
        output_root=tmp_path / "bk",
        settings_factory=_settings,
        client_factory=lambda _settings: FakePairedClient(),
    )

    assert result["status"] == "passed"
    assert result["setup_reasons"] == []
    assert all(result["invariants"].values())
    raw_usage = result["arms"]["raw"]["attempt"]["usage"]
    reusable_usage = result["arms"]["reusable"]["attempt"]["usage"]
    assert reusable_usage["prompt_tokens"] < raw_usage["prompt_tokens"]
    assert result["arms"]["raw"]["quality"]["all_facts_covered"] is True
    assert result["arms"]["reusable"]["quality"]["all_facts_covered"] is True
    assert result["side_effects"]["provider_calls"] == 2
    assert result["side_effects"]["writer_actions"] == 0
    assert result["side_effects"]["command_actions"] == 0
    assert result["secret_handling"]["serialized"] is False

    receipt = tmp_path / "bk" / "aggregate" / "receipt.json"
    encoded = receipt.read_text(encoding="utf-8")
    payload = json.loads(encoded)
    assert payload["receipt_hash"] == result["receipt_hash"]
    assert "prompt_text" not in encoded
    assert "\"content\"" not in encoded
    assert "summary_text" not in encoded
    assert "response_content" not in encoded
    assert "raw_response" not in encoded
    assert "Decision 0:" not in encoded
    assert "low-value-observation" not in encoded
    assert "scoped target is calculator.py" not in encoded
    assert "python -m pytest -q" not in encoded
    assert "sk-" not in encoded


def test_real_provider_read_only_paired_canary_blocks_without_settings(tmp_path) -> None:
    calls = {"client_factory": 0}

    def missing_settings() -> LLMSettings:
        raise ValueError("missing test credential")

    def client_factory(_settings: LLMSettings):
        calls["client_factory"] += 1
        return FakePairedClient()

    result = run_real_provider_read_only_paired_canary(
        output_root=tmp_path / "bk-blocked",
        settings_factory=missing_settings,
        client_factory=client_factory,
    )

    assert result["status"] == "blocked"
    assert result["side_effects"]["provider_calls"] == 0
    assert result["side_effects"]["provider_transport_attempted"] is False
    assert result["provider_descriptor"]["credential_serialized"] is False
    assert calls["client_factory"] == 0
