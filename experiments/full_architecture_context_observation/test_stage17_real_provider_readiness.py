from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import LLMSettings
from core.token_counting import ProviderTokenCounter
from stage17_real_provider_readiness import (
    ExperimentArm,
    ExperimentFlags,
    ManifestValidationError,
    ReadinessBlockerCode,
    RollingSummaryBudgetPolicy,
    build_experiment_manifest,
    assess_provider_readiness,
    verify_manifest_hash,
)


def _settings(**updates) -> LLMSettings:
    values = {
        "OPENPILOT_LLM_API_KEY": "test-secret",
        "OPENPILOT_LLM_PROVIDER": "openai-compatible",
        "OPENPILOT_LLM_BASE_URL": "https://proxy.invalid/v1",
        "OPENPILOT_LLM_MODEL": "custom-model",
        "OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE": "generic-openai-compatible",
    }
    values.update(updates)
    return LLMSettings(**values)


def _counter(available: bool = True) -> ProviderTokenCounter:
    return ProviderTokenCounter(
        tokenizer=object() if available else None,
        tokenizer_id="test-tokenizer" if available else "unavailable",
        model="custom-model",
    )


def _policy() -> RollingSummaryBudgetPolicy:
    return RollingSummaryBudgetPolicy(
        static_cap_tokens=512,
        required_reserve_tokens=64,
        recent_suffix_reserve_tokens=128,
        response_schema_reserve_tokens=32,
        completion_reserve_tokens=64,
    )


def _hash(char: str) -> str:
    return "sha256:" + char * 64


def test_missing_credentials_is_a_typed_blocker_and_does_not_admit_treatment() -> None:
    readiness = assess_provider_readiness(
        _settings(OPENPILOT_LLM_API_KEY=""),
        flags=ExperimentFlags(treatment_enabled=True),
        budget_policy=_policy(),
        token_counter=_counter(),
    )

    assert readiness.ready is False
    assert readiness.treatment_admissible is False
    assert ReadinessBlockerCode.MISSING_CREDENTIALS in readiness.blocker_codes


def test_treatment_requires_explicit_reasoning_profile_and_available_tokenizer() -> None:
    no_profile = assess_provider_readiness(
        _settings(OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE=None),
        flags=ExperimentFlags(treatment_enabled=True),
        budget_policy=_policy(),
        token_counter=_counter(),
    )
    no_tokenizer = assess_provider_readiness(
        _settings(),
        flags=ExperimentFlags(treatment_enabled=True),
        budget_policy=_policy(),
        token_counter=_counter(False),
    )

    assert ReadinessBlockerCode.PROFILE_NOT_EXPLICIT in no_profile.blocker_codes
    assert no_profile.treatment_admissible is False
    assert ReadinessBlockerCode.TOKENIZER_UNAVAILABLE in no_tokenizer.blocker_codes
    assert no_tokenizer.treatment_admissible is False


def test_invalid_endpoint_is_rejected_without_normalizing_credentials_into_identity() -> None:
    readiness = assess_provider_readiness(
        _settings(OPENPILOT_LLM_BASE_URL="not-a-url"),
        flags=ExperimentFlags(treatment_enabled=True),
        budget_policy=_policy(),
        token_counter=_counter(),
    )

    assert readiness.endpoint == ""
    assert ReadinessBlockerCode.INVALID_ENDPOINT in readiness.blocker_codes

    valid = assess_provider_readiness(
        _settings(OPENPILOT_LLM_BASE_URL="https://user:secret@proxy.invalid:8443/v1"),
        flags=ExperimentFlags(treatment_enabled=True),
        budget_policy=_policy(),
        token_counter=_counter(),
    )
    assert "secret" not in valid.endpoint
    assert valid.endpoint == "https://proxy.invalid:8443/v1"


def test_budget_policy_is_bounded_by_static_cap_and_all_reserves() -> None:
    policy = _policy()

    assert policy.dynamic_summary_limit(
        requested_prompt_tokens=2_000,
        used_prompt_tokens=300,
    ) == 512
    assert policy.dynamic_summary_limit(
        requested_prompt_tokens=600,
        used_prompt_tokens=300,
    ) == 12
    assert policy.dynamic_summary_limit(
        requested_prompt_tokens=400,
        used_prompt_tokens=400,
    ) == 0

    with pytest.raises(ValidationError):
        RollingSummaryBudgetPolicy(static_cap_tokens=0)


def test_treatment_manifest_is_stable_source_bound_and_credential_free() -> None:
    readiness = assess_provider_readiness(
        _settings(),
        flags=ExperimentFlags(treatment_enabled=True),
        budget_policy=_policy(),
        token_counter=_counter(),
    )
    manifest = build_experiment_manifest(
        readiness,
        experiment_id="real-provider-rolling-summary-v1",
        arm=ExperimentArm.TREATMENT,
        source_envelope_hash=_hash("a"),
        session_turn_source_hash=_hash("b"),
        constraint_hash=_hash("c"),
        task_input_hash=_hash("d"),
        completion_policy_hash=_hash("e"),
    )

    assert manifest.arm is ExperimentArm.TREATMENT
    assert manifest.manifest_hash.startswith("sha256:")
    assert verify_manifest_hash(manifest) is True
    assert "test-secret" not in manifest.model_dump_json()
    assert manifest.provider_profile_version == "v1"


def test_current_manifest_can_be_built_with_treatment_disabled_but_treatment_cannot() -> None:
    readiness = assess_provider_readiness(
        _settings(),
        flags=ExperimentFlags(),
        budget_policy=_policy(),
        token_counter=_counter(),
    )
    common = {
        "experiment_id": "real-provider-rolling-summary-v1",
        "source_envelope_hash": _hash("a"),
        "session_turn_source_hash": _hash("b"),
        "constraint_hash": _hash("c"),
        "task_input_hash": _hash("d"),
        "completion_policy_hash": _hash("e"),
    }
    current = build_experiment_manifest(readiness, arm=ExperimentArm.CURRENT, **common)
    assert current.arm is ExperimentArm.CURRENT

    with pytest.raises(ManifestValidationError, match="not admissible"):
        build_experiment_manifest(readiness, arm=ExperimentArm.TREATMENT, **common)


def test_readiness_contract_forbids_untyped_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ExperimentFlags(treatment_enabled=False, provider_name="surprise")
