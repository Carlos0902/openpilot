from __future__ import annotations

from core.config import LLMSettings
from core.token_counting import ProviderTokenCounter

from stage17_real_provider_readiness import ExperimentFlags, RollingSummaryBudgetPolicy
from stage21_final_gate import (
    FinalTrafficStatus,
    build_final_report,
    run_bounded_shadow_campaign,
)


def _settings(profile=None, api_key="test-key") -> LLMSettings:
    values = {
        "OPENPILOT_LLM_API_KEY": api_key,
        "OPENPILOT_LLM_PROVIDER": "fake-provider",
        "OPENPILOT_LLM_BASE_URL": "https://proxy.invalid/v1",
        "OPENPILOT_LLM_MODEL": "custom-model",
    }
    if profile is not None:
        values["OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE"] = profile
    return LLMSettings(**values)


def _policy() -> RollingSummaryBudgetPolicy:
    return RollingSummaryBudgetPolicy(
        static_cap_tokens=64,
        required_reserve_tokens=4,
        recent_suffix_reserve_tokens=4,
        response_schema_reserve_tokens=4,
        completion_reserve_tokens=4,
    )


def _specs():
    return [
        {
            "purpose": purpose,
            "source_candidate_ids": ("dialog:1", "tool:2"),
            "source_fingerprint": "sha256:" + char * 64,
            "source_text": "verified fact: denominator is guarded\n" * 20,
        }
        for purpose, char in (
            ("context_compaction", "a"),
            ("goal_plan", "b"),
            ("tool_event_decision", "c"),
        )
    ]


def test_final_report_is_blocked_when_profile_is_missing_and_has_no_transport() -> None:
    report = build_final_report(
        _settings(),
        policy=_policy(),
        offline_suite_passed=True,
        focused_suite_passed=True,
        compileall_passed=True,
        diff_check_passed=True,
        token_counter=ProviderTokenCounter(
            tokenizer=object(), tokenizer_id="test-tokenizer", model="custom-model"
        ),
    )

    assert report.traffic_status is FinalTrafficStatus.BLOCKED
    assert report.traffic_attempted is False
    assert "profile_not_explicit" in report.blocker_codes
    assert report.global_default_changed is False
    assert "api_key" not in report.model_dump_json()


def test_dry_run_with_explicit_profile_stays_transport_free() -> None:
    report = run_bounded_shadow_campaign(
        _settings(profile="generic-openai-compatible"),
        specs=_specs(),
        policy=_policy(),
        execute_provider=False,
        token_counter=ProviderTokenCounter(
            tokenizer=object(), tokenizer_id="test-tokenizer", model="custom-model"
        ),
    )

    assert report.traffic_status is FinalTrafficStatus.DRY_RUN
    assert report.traffic_attempted is False
    assert report.attempt_count == 0
    assert report.global_default_changed is False


def test_final_report_rejects_missing_static_checks() -> None:
    try:
        build_final_report(
            _settings(profile="generic-openai-compatible"),
            policy=_policy(),
            offline_suite_passed=False,
            focused_suite_passed=True,
            compileall_passed=True,
            diff_check_passed=True,
            token_counter=ProviderTokenCounter(
                tokenizer=object(), tokenizer_id="test-tokenizer", model="custom-model"
            ),
        )
    except ValueError as exc:
        assert "offline suite" in str(exc)
    else:
        raise AssertionError("missing static checks must stop final gate")
