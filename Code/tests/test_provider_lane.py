from __future__ import annotations

import pytest

from core.provider_lane import (
    DEEPSEEK_V4_FLASH_LANE,
    OPENAI_GPT4O_MINI_LANE,
    ProviderLane,
    credential_from_env,
    settings_for_lane,
    validate_lane_settings,
    validate_lane_tokenizer,
)


def test_openai_lane_owns_only_openai_scoped_credentials() -> None:
    assert credential_from_env(
        OPENAI_GPT4O_MINI_LANE,
        {"OPENPILOT_LLM_API_KEY": "deepseek", "OPENAI_API_KEY": "openai"},
    ) == "openai"
    assert credential_from_env(
        OPENAI_GPT4O_MINI_LANE,
        {"OPENPILOT_LLM_API_KEY": "deepseek"},
    ) is None
    assert credential_from_env(
        DEEPSEEK_V4_FLASH_LANE,
        {"OPENPILOT_LLM_API_KEY": "deepseek", "OPENAI_API_KEY": "openai"},
    ) == "deepseek"


def test_lane_settings_identity_is_typed_and_tamper_resistant() -> None:
    settings = settings_for_lane(OPENAI_GPT4O_MINI_LANE, credential="synthetic")
    validate_lane_settings(OPENAI_GPT4O_MINI_LANE, settings)
    settings.model = "gpt-5.1"
    with pytest.raises(ValueError, match="provider lane"):
        validate_lane_settings(OPENAI_GPT4O_MINI_LANE, settings)


def test_lane_tokenizer_identity_is_exact() -> None:
    class Counter:
        available = True
        tokenizer_id = "tiktoken:o200k_base"

    validate_lane_tokenizer(OPENAI_GPT4O_MINI_LANE, Counter())
    Counter.tokenizer_id = "deepseek-official-api-tokenizer"
    with pytest.raises(ValueError, match="tokenizer identity"):
        validate_lane_tokenizer(OPENAI_GPT4O_MINI_LANE, Counter())


def test_lane_rejects_empty_identity() -> None:
    with pytest.raises(ValueError, match="identity"):
        ProviderLane(
            lane_id="",
            provider="openai-compatible",
            endpoint="https://example.invalid",
            model="model",
            capability_profile=OPENAI_GPT4O_MINI_LANE.capability_profile,
            credential_env_names=("KEY",),
            tokenizer_id="counter",
            reasoning_mode=OPENAI_GPT4O_MINI_LANE.reasoning_mode,
            budget_profile=OPENAI_GPT4O_MINI_LANE.budget_profile,
        )
