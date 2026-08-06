"""Independent reasoning/completion strategy experiment for rolling summaries."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.config import LLMSettings
from core.llm import LLMClient, LLMRequest
from core.token_counting import ProviderTokenCounter
from metadata import ReasoningCapabilityProfileId, ReasoningMode, ReasoningPolicy
from stage17_real_provider_readiness import (
    ExperimentArm,
    ExperimentFlags,
    RollingSummaryBudgetPolicy,
    assess_provider_readiness,
    build_experiment_manifest,
)
from stage18_provider_shadow import (
    ProviderShadowObservation,
    build_shadow_request,
    run_provider_shadow,
)


class ReasoningStrategyId(str, Enum):
    DEFAULT_128 = "default_128"
    DISABLED_128 = "disabled_128"
    DEFAULT_256 = "default_256"
    ENABLED_HIGH_128 = "enabled_high_128"


class ReasoningOutcome(str, Enum):
    SUMMARY_AVAILABLE = "summary_available"
    REASONING_EXHAUSTED_COMPLETION = "reasoning_exhausted_completion"
    COMPLETION_CEILING_OR_SCHEMA_TRUNCATION = "completion_ceiling_or_schema_truncation"
    UNKNOWN_USAGE_OR_FINISH = "unknown_usage_or_finish"
    INVALID_SUMMARY = "invalid_summary"
    PROVIDER_ERROR = "provider_error"
    PRETRANSPORT_BLOCKED = "pretransport_blocked"


class ReasoningStrategySpec(BaseModel):
    """One explicit capability/policy/ceiling combination."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: ReasoningStrategyId
    capability_profile: ReasoningCapabilityProfileId
    capability_profile_version: str = Field(min_length=1)
    reasoning_policy: ReasoningPolicy
    summary_cap_tokens: int = Field(ge=1)


class ReasoningStrategyObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: ReasoningStrategySpec
    observation: ProviderShadowObservation
    outcome: ReasoningOutcome


class ReasoningStrategyCampaignResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "reasoning_strategy_campaign_v1"
    status: Literal["dry_run", "completed", "blocked"]
    strategy_ids: list[str]
    observations: list[ReasoningStrategyObservation] = Field(default_factory=list)
    blocker_codes: list[str] = Field(default_factory=list)


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def classify_reasoning_observation(
    spec: ReasoningStrategySpec,
    observation: ProviderShadowObservation,
) -> ReasoningOutcome:
    """Classify only typed attempt signals; never infer from model names."""

    if observation.accepted:
        return ReasoningOutcome.SUMMARY_AVAILABLE
    receipt = observation.attempt
    if observation.stop_reason is not None:
        if observation.stop_reason.value == "pretransport_blocked":
            return ReasoningOutcome.PRETRANSPORT_BLOCKED
        if observation.stop_reason.value == "provider_exception":
            usage = receipt.usage
            if (
                receipt.finish_reason == "length"
                and usage.usage_observed
                and usage.output_tokens == spec.summary_cap_tokens
                and receipt.reasoning_tokens == usage.output_tokens
            ):
                return ReasoningOutcome.REASONING_EXHAUSTED_COMPLETION
            return ReasoningOutcome.PROVIDER_ERROR
        return ReasoningOutcome.PRETRANSPORT_BLOCKED
    if not receipt.usage.usage_observed or not receipt.finish_reason_observed:
        return ReasoningOutcome.UNKNOWN_USAGE_OR_FINISH
    if (
        receipt.finish_reason == "length"
        and receipt.usage.output_tokens == spec.summary_cap_tokens
        and receipt.reasoning_tokens == receipt.usage.output_tokens
    ):
        return ReasoningOutcome.REASONING_EXHAUSTED_COMPLETION
    if receipt.finish_reason == "length":
        return ReasoningOutcome.COMPLETION_CEILING_OR_SCHEMA_TRUNCATION
    return ReasoningOutcome.INVALID_SUMMARY


def run_reasoning_strategy_campaign(
    settings: LLMSettings,
    *,
    specs: Sequence[ReasoningStrategySpec],
    source_text: str,
    source_candidate_ids: Sequence[str],
    source_fingerprint: str,
    execute_provider: bool = False,
    transport_factory: Callable[[LLMSettings], Callable[[LLMRequest], Any]] | None = None,
    token_counter: ProviderTokenCounter | None = None,
    count_tokens: Callable[[str], int] | None = None,
) -> ReasoningStrategyCampaignResult:
    """Run a maximum-four strategy comparison with fixed source and schema."""

    if len(specs) > 4:
        raise ValueError("reasoning strategy campaign is capped at four calls")
    strategy_ids = [spec.strategy_id.value for spec in specs]
    if len(strategy_ids) != len(set(strategy_ids)):
        raise ValueError("reasoning strategy IDs must be unique")
    if not execute_provider:
        return ReasoningStrategyCampaignResult(status="dry_run", strategy_ids=strategy_ids)

    counter = token_counter or ProviderTokenCounter.from_settings(settings)
    token_count_fn = count_tokens or counter.count_text
    observations: list[ReasoningStrategyObservation] = []
    for ordinal, spec in enumerate(specs, start=1):
        strategy_settings = settings.model_copy(
            update={"reasoning_capability_profile": spec.capability_profile}
        )
        policy = RollingSummaryBudgetPolicy(
            static_cap_tokens=spec.summary_cap_tokens,
            required_reserve_tokens=8,
            recent_suffix_reserve_tokens=8,
            response_schema_reserve_tokens=8,
            completion_reserve_tokens=8,
        )
        readiness = assess_provider_readiness(
            strategy_settings,
            flags=ExperimentFlags(treatment_enabled=True),
            budget_policy=policy,
            token_counter=counter,
        )
        if not readiness.treatment_admissible:
            return ReasoningStrategyCampaignResult(
                status="blocked",
                strategy_ids=strategy_ids,
                blocker_codes=sorted(code.value for code in readiness.blockers),
            )
        manifest = build_experiment_manifest(
            readiness,
            experiment_id="reasoning-strategy-v1",
            arm=ExperimentArm.TREATMENT,
            source_envelope_hash=source_fingerprint,
            session_turn_source_hash=_hash("reasoning:session"),
            constraint_hash=_hash("reasoning:constraints"),
            task_input_hash=_hash("reasoning:task"),
            completion_policy_hash=_hash(f"reasoning:completion:{spec.summary_cap_tokens}"),
            reasoning_policy=spec.reasoning_policy,
        )
        request = build_shadow_request(
            manifest=manifest,
            source_candidate_ids=tuple(source_candidate_ids),
            source_fingerprint=source_fingerprint,
            current_source_fingerprint=source_fingerprint,
            source_text=source_text,
            original_chars=len(source_text),
            requested_prompt_tokens=spec.summary_cap_tokens + 256,
            used_prompt_tokens=64,
            purpose="context_compaction",
            execution_id=f"reasoning:{spec.strategy_id.value}",
            attempt_id=f"reasoning:{spec.strategy_id.value}:attempt",
            request_ordinal=ordinal,
        )
        if transport_factory is not None:
            transport = transport_factory(strategy_settings)
        else:
            client = LLMClient(strategy_settings, enable_cache=False)
            transport = lambda llm_request, _client=client: _client.complete(
                llm_request,
                max_retries=1,
                use_cache=False,
            )
        observation = run_provider_shadow(
            request,
            transport=transport,
            count_tokens=token_count_fn,
        )
        observations.append(
            ReasoningStrategyObservation(
                strategy=spec,
                observation=observation,
                outcome=classify_reasoning_observation(spec, observation),
            )
        )
    return ReasoningStrategyCampaignResult(
        status="completed",
        strategy_ids=strategy_ids,
        observations=observations,
    )


__all__ = [
    "ReasoningOutcome",
    "ReasoningStrategyCampaignResult",
    "ReasoningStrategyId",
    "ReasoningStrategyObservation",
    "ReasoningStrategySpec",
    "classify_reasoning_observation",
    "run_reasoning_strategy_campaign",
]
