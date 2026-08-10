"""Typed provider-lane identity and credential-scope resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from core.config import LLMSettings, ProviderToolExecutionBudgetProfile
from metadata import ReasoningCapabilityProfileId, ReasoningMode


@dataclass(frozen=True)
class ProviderLane:
    """Immutable expected identity for one provider experiment lane."""

    lane_id: str
    provider: str
    endpoint: str
    model: str
    capability_profile: ReasoningCapabilityProfileId
    credential_env_names: tuple[str, ...]
    tokenizer_id: str
    reasoning_mode: ReasoningMode
    budget_profile: ProviderToolExecutionBudgetProfile
    max_rounds: int = 8

    def __post_init__(self) -> None:
        if not self.lane_id.strip() or not self.endpoint.strip() or not self.model.strip():
            raise ValueError("provider lane identity fields must be non-empty")
        if not self.credential_env_names or any(not name.strip() for name in self.credential_env_names):
            raise ValueError("provider lane credential scope must be non-empty")
        if not self.tokenizer_id.strip():
            raise ValueError("provider lane tokenizer identity must be non-empty")
        if self.max_rounds < 1:
            raise ValueError("provider lane max_rounds must be positive")


OPENAI_GPT4O_MINI_LANE = ProviderLane(
    lane_id="openai:gpt-4o-mini:no-reasoning:v1",
    provider="openai-compatible",
    endpoint="https://api.openai.com/v1",
    model="gpt-4o-mini",
    capability_profile=ReasoningCapabilityProfileId.OPENAI_CHAT_NO_REASONING_KNOWN,
    credential_env_names=("OPENPILOT_OPENAI_API_KEY", "OPENAI_API_KEY"),
    tokenizer_id="tiktoken:o200k_base",
    reasoning_mode=ReasoningMode.DISABLED,
    budget_profile=ProviderToolExecutionBudgetProfile.REAL_READ_ONLY,
)


DEEPSEEK_V4_FLASH_LANE = ProviderLane(
    lane_id="deepseek:v4-flash:disabled:v1",
    provider="openai-compatible",
    endpoint="https://api.deepseek.com",
    model="deepseek-v4-flash",
    capability_profile=ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
    credential_env_names=("OPENPILOT_LLM_API_KEY",),
    tokenizer_id="deepseek-official-api-tokenizer",
    reasoning_mode=ReasoningMode.DISABLED,
    budget_profile=ProviderToolExecutionBudgetProfile.REAL_READ_ONLY,
)


def credential_from_env(
    lane: ProviderLane,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Read only credential names explicitly owned by ``lane``."""

    values = os.environ if environ is None else environ
    for name in lane.credential_env_names:
        value = values.get(name)
        if value and value.strip():
            return value
    return None


def settings_for_lane(lane: ProviderLane, *, credential: str | None = None) -> LLMSettings:
    """Build settings from a lane without reading repository ``.env`` files."""

    return LLMSettings(
        _env_file=None,
        provider=lane.provider,
        base_url=lane.endpoint,
        model=lane.model,
        api_key=credential,
        timeout_seconds=45.0,
        temperature=0.0,
        transport_retries=0,
        reasoning_capability_profile=lane.capability_profile,
        tool_event_reasoning_mode=lane.reasoning_mode,
        provider_tool_execution_enabled=True,
        provider_tool_initial_context_projection_enabled=True,
        provider_tool_initial_context_mutation_enabled=False,
        provider_tool_completion_outcome_feedback_enabled=False,
        provider_tool_execution_budget_profile=lane.budget_profile,
        provider_tool_execution_max_rounds=lane.max_rounds,
        context_max_prompt_tokens=12_288,
        context_reserved_prompt_tokens=128,
    )


def validate_lane_settings(lane: ProviderLane, settings: LLMSettings) -> None:
    """Fail closed when settings drift from the lane identity."""

    profile = getattr(settings.reasoning_capability_profile, "value", settings.reasoning_capability_profile)
    mode = getattr(settings.tool_event_reasoning_mode, "value", settings.tool_event_reasoning_mode)
    budget = getattr(settings.provider_tool_execution_budget_profile, "value", settings.provider_tool_execution_budget_profile)
    if (
        settings.provider != lane.provider
        or settings.base_url.rstrip("/") != lane.endpoint.rstrip("/")
        or settings.model != lane.model
        or profile != lane.capability_profile.value
        or mode != lane.reasoning_mode.value
        or budget != lane.budget_profile.value
        or settings.provider_tool_execution_max_rounds != lane.max_rounds
    ):
        raise ValueError(f"settings do not match provider lane {lane.lane_id}")


def validate_lane_tokenizer(lane: ProviderLane, tokenizer: object) -> None:
    """Require an available counter with the exact lane tokenizer identity."""

    if not bool(getattr(tokenizer, "available", False)):
        raise ValueError(f"tokenizer unavailable for provider lane {lane.lane_id}")
    if str(getattr(tokenizer, "tokenizer_id", "")) != lane.tokenizer_id:
        raise ValueError(f"tokenizer identity mismatch for provider lane {lane.lane_id}")
