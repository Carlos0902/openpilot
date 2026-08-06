from __future__ import annotations

import pytest
from core.config import LLMSettings
from core.llm import LLMResponse
from core.token_counting import ProviderTokenCounter
from metadata import (
    ReasoningCapabilityProfileId,
    ReasoningEffort,
    ReasoningMode,
    ReasoningPolicy,
)

from stage22_reasoning_strategy_experiment import (
    ReasoningOutcome,
    ReasoningStrategyId,
    ReasoningStrategySpec,
    run_reasoning_strategy_campaign,
)


def _settings() -> LLMSettings:
    return LLMSettings(
        OPENPILOT_LLM_API_KEY="test-secret",
        OPENPILOT_LLM_PROVIDER="fake-provider",
        OPENPILOT_LLM_BASE_URL="https://proxy.invalid/v1",
        OPENPILOT_LLM_MODEL="deepseek-v4-flash",
    )


def _counter() -> ProviderTokenCounter:
    return ProviderTokenCounter(tokenizer=object(), tokenizer_id="test-tokenizer", model="deepseek-v4-flash")


def _hash(char: str) -> str:
    return "sha256:" + char * 64


def _spec(strategy_id, profile, policy, cap=128):
    return ReasoningStrategySpec(
        strategy_id=strategy_id,
        capability_profile=profile,
        capability_profile_version="v1",
        reasoning_policy=policy,
        summary_cap_tokens=cap,
    )


def _payload():
    return {
        "goal_delta": "preserve denominator validation",
        "verified_facts": ["divide rejects zero denominator"],
        "decisions": ["keep deterministic fallback"],
        "open_issues": [],
        "evidence_ids": ["dialog:1"],
        "next_action": "run pytest",
    }


def test_strategy_campaign_binds_reasoning_policy_to_transport_request() -> None:
    requests = []

    def factory(settings):
        def transport(request):
            requests.append(request)
            return LLMResponse(
                content="summary",
                parsed_json=_payload(),
                model="deepseek-v4-flash",
                provider="fake-provider",
                usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                finish_reason="stop",
            )

        return transport

    disabled = _spec(
        ReasoningStrategyId.DISABLED_128,
        ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
        ReasoningPolicy(mode=ReasoningMode.DISABLED),
    )
    result = run_reasoning_strategy_campaign(
        _settings(),
        specs=[disabled],
        source_text="verified fact: denominator validation\n" * 20,
        source_candidate_ids=("dialog:1", "tool:1"),
        source_fingerprint=_hash("a"),
        execute_provider=True,
        transport_factory=factory,
        token_counter=_counter(),
        count_tokens=lambda text: max(1, len(text) // 20),
    )

    assert result.status == "completed"
    assert result.observations[0].outcome is ReasoningOutcome.SUMMARY_AVAILABLE
    assert requests[0].reasoning_policy.mode is ReasoningMode.DISABLED
    assert requests[0].trace_info["reasoning_policy_version"] == "reasoning_policy_v1"


def test_classifier_separates_reasoning_exhaustion_from_generic_length() -> None:
    def factory(settings):
        def transport(request):
            if request.reasoning_policy.mode is ReasoningMode.PROVIDER_DEFAULT:
                usage = {
                    "prompt_tokens": 100,
                    "completion_tokens": 128,
                    "total_tokens": 228,
                    "completion_tokens_details": {"reasoning_tokens": 128},
                }
            else:
                usage = {
                    "prompt_tokens": 100,
                    "completion_tokens": 128,
                    "total_tokens": 228,
                    "completion_tokens_details": {"reasoning_tokens": 12},
                }
            return LLMResponse(
                content="{",
                parsed_json=None,
                model="deepseek-v4-flash",
                provider="fake-provider",
                usage=usage,
                finish_reason="length",
            )

        return transport

    specs = [
        _spec(
            ReasoningStrategyId.DEFAULT_128,
            ReasoningCapabilityProfileId.GENERIC_OPENAI_COMPATIBLE,
            ReasoningPolicy(mode=ReasoningMode.PROVIDER_DEFAULT),
        ),
        _spec(
            ReasoningStrategyId.DISABLED_128,
            ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
            ReasoningPolicy(mode=ReasoningMode.DISABLED),
        ),
    ]
    result = run_reasoning_strategy_campaign(
        _settings(),
        specs=specs,
        source_text="verified fact: denominator validation\n" * 20,
        source_candidate_ids=("dialog:1", "tool:1"),
        source_fingerprint=_hash("a"),
        execute_provider=True,
        transport_factory=factory,
        token_counter=_counter(),
        count_tokens=lambda text: max(1, len(text) // 20),
    )

    assert result.observations[0].outcome is ReasoningOutcome.REASONING_EXHAUSTED_COMPLETION
    assert result.observations[1].outcome is ReasoningOutcome.COMPLETION_CEILING_OR_SCHEMA_TRUNCATION


def test_dry_run_does_not_construct_transport_and_campaign_is_capped() -> None:
    called = []

    def factory(settings):
        called.append(settings)
        raise AssertionError("dry run must not construct a transport")

    spec = _spec(
        ReasoningStrategyId.DEFAULT_128,
        ReasoningCapabilityProfileId.GENERIC_OPENAI_COMPATIBLE,
        ReasoningPolicy(mode=ReasoningMode.PROVIDER_DEFAULT),
    )
    result = run_reasoning_strategy_campaign(
        _settings(),
        specs=[spec],
        source_text="source\n" * 20,
        source_candidate_ids=("dialog:1",),
        source_fingerprint=_hash("a"),
        execute_provider=False,
        transport_factory=factory,
        token_counter=_counter(),
    )
    assert result.status == "dry_run"
    assert result.observations == []
    assert called == []

    with pytest.raises(ValueError, match="capped"):
        run_reasoning_strategy_campaign(
            _settings(),
            specs=[spec] * 5,
            source_text="source\n" * 20,
            source_candidate_ids=("dialog:1",),
            source_fingerprint=_hash("a"),
            execute_provider=False,
            token_counter=_counter(),
        )
