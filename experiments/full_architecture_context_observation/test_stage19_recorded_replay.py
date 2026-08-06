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
from stage19_recorded_replay import (
    ReplayArtifactValidationError,
    capture_recorded_artifact,
    replay_recorded_artifact,
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


def _request():
    source = "verified fact: divide rejects zero denominator\n" * 20
    return build_shadow_request(
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


def _response(*, usage=None, finish_reason="stop"):
    return LLMResponse(
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
        finish_reason=finish_reason,
    )


def test_accepted_shadow_artifact_replays_exactly_without_transport() -> None:
    request = _request()
    original = run_provider_shadow(
        request,
        transport=lambda llm_request: _response(),
        count_tokens=lambda text: max(1, len(text) // 20),
    )
    artifact = capture_recorded_artifact(request, original)
    replayed = replay_recorded_artifact(
        artifact,
        count_tokens=lambda text: max(1, len(text) // 20),
    )

    assert replayed.accepted is True
    assert replayed.summary_record.model_dump(mode="json") == original.summary_record.model_dump(mode="json")
    assert replayed.attempt.status.value == "replayed"
    assert replayed.attempt.transport_attempted is False
    assert replayed.attempt.recovery_source_attempt_id == original.attempt.attempt_id
    assert replayed.attempt.request_hash == original.attempt.request_hash
    assert replayed.used_in_prompt is False


def test_fallback_artifact_replays_the_same_unknown_usage_decision() -> None:
    request = _request()
    original = run_provider_shadow(
        request,
        transport=lambda llm_request: _response(usage={"completion_tokens": 30}),
        count_tokens=lambda text: max(1, len(text) // 20),
    )
    artifact = capture_recorded_artifact(request, original)
    replayed = replay_recorded_artifact(
        artifact,
        count_tokens=lambda text: max(1, len(text) // 20),
    )

    assert original.accepted is False
    assert replayed.accepted is False
    assert replayed.fallback_reason == original.fallback_reason
    assert replayed.attempt.status.value == "replayed"
    assert replayed.attempt.usage.usage_observed is False


def test_tampered_source_is_rejected_before_summary_interpretation() -> None:
    request = _request()
    original = run_provider_shadow(
        request,
        transport=lambda llm_request: _response(),
        count_tokens=lambda text: max(1, len(text) // 20),
    )
    artifact = capture_recorded_artifact(request, original)
    tampered_request = artifact.request.model_copy(
        update={"source_text": "x" * artifact.request.original_chars}
    )
    tampered = artifact.model_copy(update={"request": tampered_request})

    with pytest.raises(ReplayArtifactValidationError, match="request hash"):
        replay_recorded_artifact(
            tampered,
            count_tokens=lambda text: max(1, len(text) // 20),
        )


def test_pretransport_and_provider_exception_are_not_replayable() -> None:
    request = _request()
    zero_budget_request = request.model_copy(
        update={"used_prompt_tokens": 9999}
    )
    blocked = run_provider_shadow(
        zero_budget_request,
        transport=lambda llm_request: pytest.fail("must not call transport"),
        count_tokens=lambda text: max(1, len(text) // 20),
    )
    failed = run_provider_shadow(
        request,
        transport=lambda llm_request: (_ for _ in ()).throw(TimeoutError("offline")),
        count_tokens=lambda text: max(1, len(text) // 20),
    )

    with pytest.raises(ReplayArtifactValidationError, match="not replayable"):
        capture_recorded_artifact(zero_budget_request, blocked)
    with pytest.raises(ReplayArtifactValidationError, match="payload"):
        capture_recorded_artifact(request, failed)
