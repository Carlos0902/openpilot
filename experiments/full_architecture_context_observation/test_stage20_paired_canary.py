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
from stage18_provider_shadow import build_shadow_request, run_provider_shadow
from stage20_paired_canary import (
    CanaryPurpose,
    CanaryStopError,
    CurrentProjectionEvidence,
    PairedCanaryCase,
    TreatmentProjectionEvidence,
    run_paired_canary,
)


def _hash(char: str) -> str:
    return "sha256:" + char * 64


def _manifest():
    settings = LLMSettings(
        OPENPILOT_LLM_API_KEY="test-secret",
        OPENPILOT_LLM_PROVIDER="fake-provider",
        OPENPILOT_LLM_BASE_URL="https://proxy.invalid/v1",
        OPENPILOT_LLM_MODEL="custom-model",
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="generic-openai-compatible",
    )
    readiness = assess_provider_readiness(
        settings,
        flags=ExperimentFlags(treatment_enabled=True),
        budget_policy=RollingSummaryBudgetPolicy(
            static_cap_tokens=128,
            required_reserve_tokens=8,
            recent_suffix_reserve_tokens=8,
            response_schema_reserve_tokens=8,
            completion_reserve_tokens=8,
        ),
        token_counter=ProviderTokenCounter(
            tokenizer=object(), tokenizer_id="test-tokenizer", model="custom-model"
        ),
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


def _shadow(*, usage=None):
    source = "verified fact: divide rejects zero denominator\n" * 20
    request = build_shadow_request(
        manifest=_manifest(),
        source_candidate_ids=("dialog:1", "tool:2"),
        source_fingerprint=_hash("f"),
        current_source_fingerprint=_hash("f"),
        source_text=source,
        original_chars=len(source),
        requested_prompt_tokens=512,
        used_prompt_tokens=64,
        purpose="context_compaction",
        execution_id="execution-1",
        attempt_id="attempt-1",
        request_ordinal=1,
    )
    response = LLMResponse(
        content="summary",
        parsed_json={
            "goal_delta": "preserve denominator validation",
            "verified_facts": ["divide rejects zero denominator"],
            "decisions": ["keep deterministic fallback"],
            "open_issues": [],
            "evidence_ids": ["dialog:1"],
            "next_action": "run pytest",
        },
        model="custom-model",
        provider="fake-provider",
        usage=usage or {"prompt_tokens": 200, "completion_tokens": 30, "total_tokens": 230},
        finish_reason="stop",
    )
    return run_provider_shadow(
        request,
        transport=lambda llm_request: response,
        count_tokens=lambda text: max(1, len(text) // 20),
    )


def _case(index: int, *, treatment=None, used=True, mutation_paths=None):
    source_hash = _hash("a")
    constraint_hash = _hash("c")
    treatment = treatment or _shadow()
    return PairedCanaryCase(
        pair_id=f"pair-{index}",
        purpose=list(CanaryPurpose)[index],
        source_envelope_hash=source_hash,
        constraint_hash=constraint_hash,
        required_candidate_ids=["constraint:write-scope"],
        current=CurrentProjectionEvidence(
            source_envelope_hash=source_hash,
            constraint_hash=constraint_hash,
            prompt_tokens=100,
            retained_required_candidate_ids=["constraint:write-scope"],
            provenance_valid=True,
            verification_passed=True,
            quality_passed=True,
            mutation_paths=[],
        ),
        treatment=TreatmentProjectionEvidence(
            source_envelope_hash=source_hash,
            constraint_hash=constraint_hash,
            observation=treatment,
            prompt_tokens=80 if used else 100,
            used_in_prompt=used,
            summary_compaction_id=(
                treatment.summary_record.compaction_id if used and treatment.summary_record else None
            ),
            retained_required_candidate_ids=["constraint:write-scope"],
            provenance_valid=True,
            verification_passed=True,
            quality_passed=True,
            mutation_paths=mutation_paths or [],
        ),
    )


def test_three_purpose_canary_reports_savings_but_never_admits_global_rollout() -> None:
    result = run_paired_canary([_case(0), _case(1), _case(2)])

    assert result.status == "passed"
    assert result.purpose_coverage == sorted(p.value for p in CanaryPurpose)
    assert result.current_prompt_tokens == 300
    assert result.treatment_prompt_tokens == 240
    assert result.token_reduction_tokens == 60
    assert result.token_reduction_ratio == pytest.approx(0.2)
    assert result.provider_calls == 3
    assert result.accepted_summary_count == 3
    assert result.rollout_admitted is False
    assert result.global_default_changed is False


def test_unknown_usage_fallback_is_visible_and_does_not_count_as_treatment_use() -> None:
    fallback = _shadow(usage={"completion_tokens": 30})
    result = run_paired_canary(
        [
            _case(0, treatment=fallback, used=False),
            _case(1),
            _case(2),
        ]
    )

    assert result.status == "passed"
    assert result.fallback_count == 1
    assert result.unknown_usage_count == 1
    assert result.treatment_prompt_tokens == 260


def test_required_state_or_mutation_failure_stops_canary() -> None:
    bad_required = _case(0)
    bad_required.treatment.retained_required_candidate_ids = []
    with pytest.raises(CanaryStopError, match="required"):
        run_paired_canary([bad_required, _case(1), _case(2)])

    bad_mutation = _case(0, mutation_paths=["calculator.py"])
    with pytest.raises(CanaryStopError, match="mutation"):
        run_paired_canary([bad_mutation, _case(1), _case(2)])


def test_source_or_constraint_mismatch_stops_before_aggregation() -> None:
    bad = _case(0)
    bad.treatment.source_envelope_hash = _hash("z")
    with pytest.raises(CanaryStopError, match="source_envelope"):
        run_paired_canary([bad, _case(1), _case(2)])


def test_kill_switch_and_purpose_coverage_are_fail_closed() -> None:
    with pytest.raises(CanaryStopError, match="kill switch"):
        run_paired_canary([_case(0), _case(1), _case(2)], kill_switch_armed=False)
    with pytest.raises(CanaryStopError, match="purpose"):
        run_paired_canary([_case(0), _case(1)])
