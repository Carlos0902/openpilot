from __future__ import annotations

import json

import pytest

from experiments.full_architecture_context_observation.stage_h8r2al_openai_readiness import (
    ZERO_SIDE_EFFECTS,
    build_readiness,
    canonical_hash,
    validate_readiness,
)


class _UnavailableCounter:
    available = False
    tokenizer_id = "unavailable"
    model = "gpt-4o-mini"


class _AvailableCounter:
    available = True
    tokenizer_id = "tiktoken:o200k_base"
    model = "gpt-4o-mini"


def _settings(monkeypatch, *, key: str | None = None):
    from experiments.full_architecture_context_observation import stage_h8r2al_openai_readiness as readiness

    if key is None:
        monkeypatch.delenv("OPENPILOT_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENPILOT_OPENAI_API_KEY", key)
    return readiness._settings()


def test_missing_openai_credential_is_typed_blocked_without_transport(monkeypatch) -> None:
    settings = _settings(monkeypatch)
    receipt = build_readiness(settings=settings, tokenizer=_UnavailableCounter())
    validate_readiness(receipt)
    assert receipt["status"] == "typed_blocked"
    assert receipt["blockers"] == ["tokenizer_unavailable", "missing_credentials"]
    assert receipt["side_effects"] == ZERO_SIDE_EFFECTS


def test_openai_readiness_never_reuses_deepseek_environment(monkeypatch) -> None:
    monkeypatch.delenv("OPENPILOT_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENPILOT_LLM_API_KEY", "deepseek-only-synthetic")
    settings = _settings(monkeypatch)
    receipt = build_readiness(settings=settings, tokenizer=_AvailableCounter())
    validate_readiness(receipt)
    assert receipt["status"] == "typed_blocked"
    assert receipt["blockers"] == ["missing_credentials"]
    assert receipt["side_effects"]["provider_calls"] == 0


def test_openai_credential_is_process_only_and_not_serialized(monkeypatch) -> None:
    settings = _settings(monkeypatch, key="synthetic-openai-key")
    receipt = build_readiness(settings=settings, tokenizer=_AvailableCounter())
    validate_readiness(receipt)
    assert receipt["status"] == "passed"
    assert receipt["provider"]["credential_present"] is True
    encoded = json.dumps(receipt, ensure_ascii=False)
    assert "synthetic-openai-key" not in encoded
    assert "api_key" not in encoded


def test_openai_readiness_uses_no_reasoning_profile_for_gpt4o_mini(monkeypatch) -> None:
    settings = _settings(monkeypatch, key="synthetic-openai-key")
    receipt = build_readiness(settings=settings, tokenizer=_AvailableCounter())
    assert receipt["provider"]["capability_profile"] == "openai-chat-no-reasoning-known:v1"
    assert receipt["reasoning"] == {"requested_mode": "disabled", "resolved_mode": "disabled"}


def test_readiness_hash_tamper_is_rejected(monkeypatch) -> None:
    settings = _settings(monkeypatch)
    receipt = build_readiness(settings=settings, tokenizer=_UnavailableCounter())
    receipt["blockers"] = []
    with pytest.raises(ValueError, match="hash"):
        validate_readiness(receipt)
