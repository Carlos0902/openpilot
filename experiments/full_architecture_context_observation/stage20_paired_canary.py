"""Small, read-only Current/Treatment paired canary gate."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stage18_provider_shadow import ProviderShadowObservation


class CanaryStopError(RuntimeError):
    """A paired canary failed a safety or evidence gate."""


class CanaryPurpose(str, Enum):
    CONTEXT_COMPACTION = "context_compaction"
    GOAL_PLAN = "goal_plan"
    TOOL_EVENT_DECISION = "tool_event_decision"


_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"


class CurrentProjectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_envelope_hash: str = Field(pattern=_HASH_PATTERN)
    constraint_hash: str = Field(pattern=_HASH_PATTERN)
    prompt_tokens: int = Field(ge=0)
    retained_required_candidate_ids: list[str] = Field(min_length=1)
    provenance_valid: bool
    verification_passed: bool
    quality_passed: bool
    mutation_paths: list[str] = Field(default_factory=list)


class TreatmentProjectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_envelope_hash: str = Field(pattern=_HASH_PATTERN)
    constraint_hash: str = Field(pattern=_HASH_PATTERN)
    observation: ProviderShadowObservation
    prompt_tokens: int = Field(ge=0)
    used_in_prompt: bool = False
    summary_compaction_id: str | None = None
    retained_required_candidate_ids: list[str] = Field(min_length=1)
    provenance_valid: bool
    verification_passed: bool
    quality_passed: bool
    mutation_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _summary_use_matches_observation(self) -> "TreatmentProjectionEvidence":
        if self.used_in_prompt:
            if not self.observation.accepted or self.observation.summary_record is None:
                raise ValueError("a Treatment Prompt projection requires an accepted summary")
            if self.summary_compaction_id != self.observation.summary_record.compaction_id:
                raise ValueError("Treatment summary compaction ID does not match observation")
        elif self.summary_compaction_id is not None:
            raise ValueError("a fallback Treatment projection cannot carry summary identity")
        return self


class PairedCanaryCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_id: str = Field(min_length=1)
    purpose: CanaryPurpose
    source_envelope_hash: str = Field(pattern=_HASH_PATTERN)
    constraint_hash: str = Field(pattern=_HASH_PATTERN)
    required_candidate_ids: list[str] = Field(min_length=1)
    current: CurrentProjectionEvidence
    treatment: TreatmentProjectionEvidence

    @model_validator(mode="after")
    def _pair_sources_match(self) -> "PairedCanaryCase":
        if self.current.source_envelope_hash != self.source_envelope_hash:
            raise ValueError("Current source_envelope hash does not match pair")
        if self.treatment.source_envelope_hash != self.source_envelope_hash:
            raise ValueError("Treatment source_envelope hash does not match pair")
        if self.current.constraint_hash != self.constraint_hash:
            raise ValueError("Current constraint hash does not match pair")
        if self.treatment.constraint_hash != self.constraint_hash:
            raise ValueError("Treatment constraint hash does not match pair")
        if len(self.required_candidate_ids) != len(set(self.required_candidate_ids)):
            raise ValueError("pair required candidate IDs must be unique")
        return self


class PairedCanaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "rolling_summary_paired_canary_v1"
    status: Literal["passed"] = "passed"
    pair_ids: list[str]
    purpose_coverage: list[str]
    current_prompt_tokens: int = Field(ge=0)
    treatment_prompt_tokens: int = Field(ge=0)
    token_reduction_tokens: int
    token_reduction_ratio: float
    provider_calls: int = Field(ge=0)
    accepted_summary_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    unknown_usage_count: int = Field(ge=0)
    rollout_admitted: Literal[False] = False
    global_default_changed: Literal[False] = False


def _require_gate(condition: bool, message: str) -> None:
    if not condition:
        raise CanaryStopError(message)


def run_paired_canary(
    cases: list[PairedCanaryCase],
    *,
    feature_flag_enabled: bool = True,
    kill_switch_armed: bool = True,
    required_purposes: set[CanaryPurpose] | None = None,
) -> PairedCanaryResult:
    """Validate a bounded paired campaign without changing production defaults."""

    _require_gate(feature_flag_enabled, "paired canary feature flag is disabled")
    _require_gate(kill_switch_armed, "paired canary kill switch is not armed")
    required = required_purposes or set(CanaryPurpose)
    _require_gate(bool(cases), "paired canary has no cases")
    pair_ids = [case.pair_id for case in cases]
    _require_gate(len(pair_ids) == len(set(pair_ids)), "paired canary pair IDs are duplicated")
    observed_purposes = {case.purpose for case in cases}
    missing = required - observed_purposes
    _require_gate(not missing, f"paired canary purpose coverage is missing: {sorted(p.value for p in missing)}")

    current_tokens = 0
    treatment_tokens = 0
    provider_calls = 0
    accepted_count = 0
    fallback_count = 0
    unknown_usage_count = 0
    for case in cases:
        _require_gate(
            case.current.source_envelope_hash == case.source_envelope_hash,
            f"pair {case.pair_id} Current source_envelope hash mismatch",
        )
        _require_gate(
            case.treatment.source_envelope_hash == case.source_envelope_hash,
            f"pair {case.pair_id} Treatment source_envelope hash mismatch",
        )
        _require_gate(
            case.current.constraint_hash == case.constraint_hash,
            f"pair {case.pair_id} Current constraint hash mismatch",
        )
        _require_gate(
            case.treatment.constraint_hash == case.constraint_hash,
            f"pair {case.pair_id} Treatment constraint hash mismatch",
        )
        required_ids = set(case.required_candidate_ids)
        _require_gate(
            required_ids.issubset(set(case.current.retained_required_candidate_ids)),
            f"pair {case.pair_id} required state missing in Current",
        )
        _require_gate(
            required_ids.issubset(set(case.treatment.retained_required_candidate_ids)),
            f"pair {case.pair_id} required state missing in Treatment",
        )
        _require_gate(case.current.provenance_valid, f"pair {case.pair_id} Current provenance failed")
        _require_gate(case.treatment.provenance_valid, f"pair {case.pair_id} Treatment provenance failed")
        _require_gate(case.current.verification_passed, f"pair {case.pair_id} Current verification failed")
        _require_gate(case.treatment.verification_passed, f"pair {case.pair_id} Treatment verification failed")
        _require_gate(case.current.quality_passed, f"pair {case.pair_id} Current quality failed")
        _require_gate(case.treatment.quality_passed, f"pair {case.pair_id} Treatment quality failed")
        _require_gate(not case.current.mutation_paths, f"pair {case.pair_id} Current mutation gate failed")
        _require_gate(not case.treatment.mutation_paths, f"pair {case.pair_id} Treatment mutation gate failed")

        current_tokens += case.current.prompt_tokens
        treatment_tokens += case.treatment.prompt_tokens
        provider_calls += int(case.treatment.observation.attempt.transport_attempted)
        accepted_count += int(case.treatment.observation.accepted)
        fallback_count += int(not case.treatment.observation.accepted)
        unknown_usage_count += int(not case.treatment.observation.attempt.usage.usage_observed)

    reduction = current_tokens - treatment_tokens
    ratio = reduction / current_tokens if current_tokens else 0.0
    return PairedCanaryResult(
        pair_ids=pair_ids,
        purpose_coverage=sorted(purpose.value for purpose in observed_purposes),
        current_prompt_tokens=current_tokens,
        treatment_prompt_tokens=treatment_tokens,
        token_reduction_tokens=reduction,
        token_reduction_ratio=ratio,
        provider_calls=provider_calls,
        accepted_summary_count=accepted_count,
        fallback_count=fallback_count,
        unknown_usage_count=unknown_usage_count,
    )


__all__ = [
    "CanaryPurpose",
    "CanaryStopError",
    "CurrentProjectionEvidence",
    "PairedCanaryCase",
    "PairedCanaryResult",
    "TreatmentProjectionEvidence",
    "run_paired_canary",
]
