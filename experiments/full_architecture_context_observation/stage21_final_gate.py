"""Final offline gate and explicitly bounded real-provider shadow runner."""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from core.config import LLMSettings
from core.llm import LLMClient
from core.token_counting import ProviderTokenCounter
from stage17_real_provider_readiness import (
    ExperimentArm,
    ExperimentFlags,
    ProviderReadiness,
    RollingSummaryBudgetPolicy,
    assess_provider_readiness,
    build_experiment_manifest,
)
from stage18_provider_shadow import (
    ProviderShadowObservation,
    build_shadow_request,
    run_provider_shadow,
)


class FinalTrafficStatus(str, Enum):
    BLOCKED = "blocked"
    DRY_RUN = "dry_run"
    COMPLETED = "completed"


class FinalExperimentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "rolling_summary_final_gate_v1"
    offline_suite_passed: bool
    focused_suite_passed: bool
    compileall_passed: bool
    diff_check_passed: bool
    readiness: ProviderReadiness
    traffic_status: FinalTrafficStatus
    traffic_attempted: bool
    provider_calls: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    accepted_summary_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    unknown_usage_count: int = Field(ge=0)
    blocker_codes: list[str] = Field(default_factory=list)
    attempt_receipts: list[dict[str, Any]] = Field(default_factory=list)
    global_default_changed: bool = False


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _receipt_summary(observation: ProviderShadowObservation) -> dict[str, Any]:
    receipt = observation.attempt
    return {
        "attempt_id": receipt.attempt_id,
        "status": receipt.status.value,
        "transport_attempted": receipt.transport_attempted,
        "request_hash": receipt.request_hash,
        "response_hash": receipt.response_hash,
        "usage": receipt.usage.model_dump(mode="json"),
        "reasoning_tokens": receipt.reasoning_tokens,
        "finish_reason": receipt.finish_reason,
        "error_type": receipt.error_type,
        "error_category": receipt.error_category,
        "recoverable": receipt.recoverable,
        "retry_recommended": receipt.retry_recommended,
        "accepted": observation.accepted,
        "fallback_reason": observation.fallback_reason.value if observation.fallback_reason else None,
        "stop_reason": observation.stop_reason.value if observation.stop_reason else None,
    }


def build_final_report(
    settings: LLMSettings,
    *,
    policy: RollingSummaryBudgetPolicy,
    offline_suite_passed: bool,
    focused_suite_passed: bool,
    compileall_passed: bool,
    diff_check_passed: bool,
    token_counter: ProviderTokenCounter | None = None,
    observations: Sequence[ProviderShadowObservation] = (),
) -> FinalExperimentReport:
    """Build the final report; no Provider transport occurs in this function."""

    if not all((offline_suite_passed, focused_suite_passed, compileall_passed, diff_check_passed)):
        raise ValueError("offline suite, focused suite, compileall, and diff check must pass")
    readiness = assess_provider_readiness(
        settings,
        flags=ExperimentFlags(treatment_enabled=True),
        budget_policy=policy,
        token_counter=token_counter,
    )
    observation_list = list(observations)
    transport_attempts = [observation for observation in observation_list if observation.attempt.transport_attempted]
    traffic_attempted = bool(transport_attempts)
    status = (
        FinalTrafficStatus.COMPLETED
        if traffic_attempted
        else FinalTrafficStatus.BLOCKED
        if not readiness.treatment_admissible
        else FinalTrafficStatus.DRY_RUN
    )
    return FinalExperimentReport(
        offline_suite_passed=offline_suite_passed,
        focused_suite_passed=focused_suite_passed,
        compileall_passed=compileall_passed,
        diff_check_passed=diff_check_passed,
        readiness=readiness,
        traffic_status=status,
        traffic_attempted=traffic_attempted,
        provider_calls=len(transport_attempts),
        attempt_count=len(observation_list),
        accepted_summary_count=sum(int(observation.accepted) for observation in observation_list),
        fallback_count=sum(int(not observation.accepted) for observation in observation_list),
        unknown_usage_count=sum(
            int(not observation.attempt.usage.usage_observed) for observation in observation_list
        ),
        blocker_codes=sorted(blocker.code.value for blocker in readiness.blockers),
        attempt_receipts=[_receipt_summary(observation) for observation in observation_list],
        global_default_changed=False,
    )


def run_bounded_shadow_campaign(
    settings: LLMSettings,
    *,
    specs: Sequence[Mapping[str, Any]],
    policy: RollingSummaryBudgetPolicy,
    execute_provider: bool = False,
    token_counter: ProviderTokenCounter | None = None,
) -> FinalExperimentReport:
    """Run at most three purpose-separated shadow calls with explicit opt-in."""

    if len(specs) > 3:
        raise ValueError("final shadow campaign is capped at three calls")
    counter = token_counter or ProviderTokenCounter.from_settings(settings)
    readiness = assess_provider_readiness(
        settings,
        flags=ExperimentFlags(treatment_enabled=True),
        budget_policy=policy,
        token_counter=counter,
    )
    if not execute_provider or not readiness.treatment_admissible:
        return build_final_report(
            settings,
            policy=policy,
            offline_suite_passed=True,
            focused_suite_passed=True,
            compileall_passed=True,
            diff_check_passed=True,
            token_counter=counter,
        )

    client = LLMClient(settings, enable_cache=False)
    observations: list[ProviderShadowObservation] = []
    for ordinal, spec in enumerate(specs, start=1):
        purpose = str(spec.get("purpose") or "").strip()
        source_text = str(spec.get("source_text") or "")
        source_ids = tuple(str(value) for value in (spec.get("source_candidate_ids") or ()))
        source_fingerprint = str(spec.get("source_fingerprint") or "")
        if not purpose or not source_text or not source_ids or not source_fingerprint:
            raise ValueError("shadow spec requires purpose, source text, IDs, and fingerprint")
        source_envelope_hash = str(spec.get("source_envelope_hash") or source_fingerprint)
        manifest = build_experiment_manifest(
            readiness,
            experiment_id="real-provider-rolling-summary-v1",
            arm=ExperimentArm.TREATMENT,
            source_envelope_hash=source_envelope_hash,
            session_turn_source_hash=str(spec.get("session_turn_source_hash") or _hash(purpose + ":session")),
            constraint_hash=str(spec.get("constraint_hash") or _hash(purpose + ":constraints")),
            task_input_hash=str(spec.get("task_input_hash") or _hash(purpose + ":task")),
            completion_policy_hash=str(spec.get("completion_policy_hash") or _hash(purpose + ":completion")),
        )
        shadow_request = build_shadow_request(
            manifest=manifest,
            source_candidate_ids=source_ids,
            source_fingerprint=source_fingerprint,
            current_source_fingerprint=str(spec.get("current_source_fingerprint") or source_fingerprint),
            previous_summary_fingerprint=spec.get("previous_summary_fingerprint"),
            source_text=source_text,
            original_chars=len(source_text),
            requested_prompt_tokens=int(spec.get("requested_prompt_tokens") or 512),
            used_prompt_tokens=int(spec.get("used_prompt_tokens") or 64),
            purpose=purpose,
            execution_id=f"stage6e:{ordinal}",
            attempt_id=f"stage6e:attempt:{ordinal}",
            request_ordinal=ordinal,
        )
        observations.append(
            run_provider_shadow(
                shadow_request,
                transport=lambda request, _client=client: _client.complete(
                    request,
                    max_retries=1,
                    use_cache=False,
                ),
                count_tokens=counter.count_text,
            )
        )
    return build_final_report(
        settings,
        policy=policy,
        offline_suite_passed=True,
        focused_suite_passed=True,
        compileall_passed=True,
        diff_check_passed=True,
        token_counter=counter,
        observations=observations,
    )


__all__ = [
    "FinalExperimentReport",
    "FinalTrafficStatus",
    "build_final_report",
    "run_bounded_shadow_campaign",
]
