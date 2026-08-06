from __future__ import annotations

import pytest
from core.config import LLMSettings
from core.llm import LLMResponse
from core.token_counting import ProviderTokenCounter

from stage17_real_provider_readiness import (
    ExperimentArm,
    ExperimentFlags,
    RollingSummaryBudgetPolicy,
    assess_provider_readiness,
    build_experiment_manifest,
)
from stage18_provider_shadow import (
    ShadowStopReason,
    build_shadow_request,
    run_provider_shadow,
)


def _settings() -> LLMSettings:
    return LLMSettings(
        OPENPILOT_LLM_API_KEY="test-secret",
        OPENPILOT_LLM_PROVIDER="fake-provider",
        OPENPILOT_LLM_BASE_URL="https://user:secret@proxy.invalid:8443/v1",
        OPENPILOT_LLM_MODEL="custom-model",
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="generic-openai-compatible",
    )


def _counter() -> ProviderTokenCounter:
    return ProviderTokenCounter(tokenizer=object(), tokenizer_id="test-tokenizer", model="custom-model")


def _hash(char: str) -> str:
    return "sha256:" + char * 64


def _manifest(*, treatment_enabled: bool = True, policy: RollingSummaryBudgetPolicy | None = None):
    readiness = assess_provider_readiness(
        _settings(),
        flags=ExperimentFlags(treatment_enabled=treatment_enabled),
        budget_policy=policy
        or RollingSummaryBudgetPolicy(
            static_cap_tokens=128,
            required_reserve_tokens=8,
            recent_suffix_reserve_tokens=8,
            response_schema_reserve_tokens=8,
            completion_reserve_tokens=8,
        ),
        token_counter=_counter(),
    )
    return build_experiment_manifest(
        readiness,
        experiment_id="real-provider-rolling-summary-v1",
        arm=ExperimentArm.TREATMENT,
        source_envelope_hash=_hash("a"),
        session_turn_source_hash=_hash("b"),
        constraint_hash=_hash("c"),
        task_input_hash=_hash("d"),
        completion_policy_hash=_hash("e"),
    )


def _request(*, manifest=None, current_source_fingerprint=None, used_prompt_tokens=64):
    source = "verified fact: divide rejects zero denominator\n" * 20
    return build_shadow_request(
        manifest=manifest or _manifest(),
        source_candidate_ids=("dialog:1", "tool:2"),
        source_fingerprint=_hash("f"),
        current_source_fingerprint=current_source_fingerprint or _hash("f"),
        previous_summary_fingerprint=None,
        source_text=source,
        original_chars=len(source),
        requested_prompt_tokens=512,
        used_prompt_tokens=used_prompt_tokens,
        purpose="context_compaction",
        execution_id="execution-1",
        attempt_id="attempt-1",
        request_ordinal=1,
    )


def _response(*, usage=None, finish_reason="stop", payload=None):
    payload = payload or {
        "goal_delta": "preserve denominator validation",
        "verified_facts": ["divide rejects zero denominator"],
        "decisions": ["keep deterministic fallback"],
        "open_issues": [],
        "evidence_ids": ["dialog:1"],
        "next_action": "run pytest",
    }
    usage = usage or {"prompt_tokens": 200, "completion_tokens": 30, "total_tokens": 230}
    return LLMResponse(
        content="summary",
        parsed_json=payload,
        model="custom-model",
        provider="fake-provider",
        usage=usage,
        finish_reason=finish_reason,
    )


def test_shadow_validates_but_never_marks_summary_as_prompt_input() -> None:
    calls = []

    def transport(request):
        calls.append(request)
        return _response()

    observation = run_provider_shadow(
        _request(),
        transport=transport,
        count_tokens=lambda text: max(1, len(text) // 20),
    )

    assert observation.accepted is True
    assert observation.used_in_prompt is False
    assert observation.summary_record is not None
    assert observation.attempt.usage.usage_observed is True
    assert observation.attempt.response_hash is not None
    assert observation.attempt.request_hash.startswith("v2:sha256:")
    assert len(calls) == 1
    assert calls[0].response_format == "json_object"
    assert calls[0].max_tokens == 128


def test_partial_usage_and_truncated_output_fall_back_without_zero_filling() -> None:
    partial = run_provider_shadow(
        _request(),
        transport=lambda request: _response(
            usage={"completion_tokens": 30},
        ),
        count_tokens=lambda text: max(1, len(text) // 20),
    )
    truncated = run_provider_shadow(
        _request(),
        transport=lambda request: _response(finish_reason="length"),
        count_tokens=lambda text: max(1, len(text) // 20),
    )

    assert partial.accepted is False
    assert partial.fallback_reason.value == "unknown_usage"
    assert partial.attempt.usage.usage_observed is False
    assert truncated.accepted is False
    assert truncated.fallback_reason.value == "truncated_output"


def test_zero_budget_is_pretransport_blocked_and_stale_source_is_rejected() -> None:
    zero_budget = run_provider_shadow(
        _request(
            manifest=_manifest(
                policy=RollingSummaryBudgetPolicy(
                    static_cap_tokens=16,
                    required_reserve_tokens=8,
                    recent_suffix_reserve_tokens=8,
                    response_schema_reserve_tokens=8,
                    completion_reserve_tokens=8,
                )
            ),
            used_prompt_tokens=512,
        ),
        transport=lambda request: pytest.fail("zero budget must not call transport"),
        count_tokens=lambda text: max(1, len(text) // 20),
    )
    stale = run_provider_shadow(
        _request(current_source_fingerprint=_hash("0")),
        transport=lambda request: _response(),
        count_tokens=lambda text: max(1, len(text) // 20),
    )

    assert zero_budget.accepted is False
    assert zero_budget.stop_reason is ShadowStopReason.ZERO_SUMMARY_BUDGET
    assert zero_budget.attempt.status.value == "pretransport_blocked"
    assert stale.accepted is False
    assert stale.fallback_reason.value == "stale_source"


def test_provider_exception_is_a_failed_receipt_with_typed_stop_reason() -> None:
    observation = run_provider_shadow(
        _request(),
        transport=lambda request: (_ for _ in ()).throw(TimeoutError("offline")),
        count_tokens=lambda text: max(1, len(text) // 20),
    )

    assert observation.accepted is False
    assert observation.stop_reason is ShadowStopReason.PROVIDER_EXCEPTION
    assert observation.attempt.status.value == "failed"
    assert observation.attempt.error_type == "TimeoutError"


def test_provider_validation_exception_preserves_usage_finish_and_response_hash() -> None:
    class ProviderValidationError(Exception):
        usage = {"prompt_tokens": 200, "completion_tokens": 30, "total_tokens": 230}
        finish_reason = "length"
        response_text = "{"  # bounded hash evidence, never persisted as raw artifact
        category = "validation"
        context = {"json_repair_attempts": 1}

    observation = run_provider_shadow(
        _request(),
        transport=lambda request: (_ for _ in ()).throw(ProviderValidationError()),
        count_tokens=lambda text: max(1, len(text) // 20),
    )

    assert observation.attempt.usage.usage_observed is True
    assert observation.attempt.finish_reason == "length"
    assert observation.attempt.response_hash is not None
    assert observation.attempt.json_repair_attempts == 1
    assert observation.attempt.error_category == "validation"


def test_shadow_request_rejects_source_length_mismatch() -> None:
    with pytest.raises(ValueError, match="original_chars"):
        build_shadow_request(
            manifest=_manifest(),
            source_candidate_ids=("dialog:1",),
            source_fingerprint=_hash("f"),
            current_source_fingerprint=_hash("f"),
            previous_summary_fingerprint=None,
            source_text="source",
            original_chars=99,
            requested_prompt_tokens=512,
            used_prompt_tokens=64,
            purpose="context_compaction",
            execution_id="execution-1",
            attempt_id="attempt-1",
            request_ordinal=1,
        )
