"""Fail-closed readiness and manifest contracts for the real-provider campaign.

This module is experiment-owned on purpose.  It describes what a treatment
attempt is allowed to do; it does not create an LLM client, send a request, or
change the production context builder.  A later shadow/canary stage must
present one of these manifests before it may call a provider.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.llm import normalized_provider_endpoint
from core.reasoning import select_reasoning_capability_profile
from core.token_counting import ProviderTokenCounter
from memory.compaction_summary import calculate_summary_budget


READINESS_SCHEMA_VERSION = "real_provider_readiness_v1"
MANIFEST_SCHEMA_VERSION = "real_provider_rolling_summary_manifest_v1"
SUMMARY_SCHEMA_VERSION = "context_compaction_summary_v1"
SUMMARY_ADAPTER_VERSION = "llm_rolling_summary_v1"
ATTEMPT_CONTRACT_VERSION = "provider_attempt_receipt_v1"
_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"


class ExperimentArm(str, Enum):
    CURRENT = "current"
    TREATMENT = "treatment"


class ReadinessBlockerCode(str, Enum):
    MISSING_CREDENTIALS = "missing_credentials"
    INVALID_ENDPOINT = "invalid_endpoint"
    MISSING_MODEL = "missing_model"
    PROFILE_NOT_EXPLICIT = "profile_not_explicit"
    TOKENIZER_UNAVAILABLE = "tokenizer_unavailable"
    INVALID_BUDGET = "invalid_budget"
    INVALID_SCHEMA = "invalid_schema"
    KILL_SWITCH_NOT_ARMED = "kill_switch_not_armed"
    NON_READ_ONLY = "non_read_only"


class ManifestValidationError(ValueError):
    """A canary manifest cannot prove that its provider boundary is safe."""


class ReadinessBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ReadinessBlockerCode
    detail: str = Field(min_length=1, max_length=240)


class ExperimentFlags(BaseModel):
    """Feature flags with safe defaults and no provider-specific fields."""

    model_config = ConfigDict(extra="forbid")

    treatment_enabled: bool = False
    kill_switch_armed: bool = True
    read_only: bool = True


class RollingSummaryBudgetPolicy(BaseModel):
    """Independent summary budget policy for one experiment arm."""

    model_config = ConfigDict(extra="forbid")

    static_cap_tokens: int = Field(ge=1)
    required_reserve_tokens: int = Field(default=0, ge=0)
    recent_suffix_reserve_tokens: int = Field(default=0, ge=0)
    response_schema_reserve_tokens: int = Field(default=0, ge=0)
    completion_reserve_tokens: int = Field(default=0, ge=0)
    dynamic_policy_version: str = Field(default="dynamic_reserve_v1", min_length=1)

    def dynamic_summary_limit(
        self,
        *,
        requested_prompt_tokens: int,
        used_prompt_tokens: int,
    ) -> int:
        """Return the summary slot after required and response reserves."""

        return calculate_summary_budget(
            static_cap_tokens=self.static_cap_tokens,
            requested_prompt_tokens=requested_prompt_tokens,
            used_prompt_tokens=used_prompt_tokens,
            required_reserve_tokens=self.required_reserve_tokens,
            recent_suffix_reserve_tokens=self.recent_suffix_reserve_tokens,
            response_schema_reserve_tokens=(
                self.response_schema_reserve_tokens + self.completion_reserve_tokens
            ),
        )


class ProviderReadiness(BaseModel):
    """Credential-free provider readiness result used by experiment gates."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = READINESS_SCHEMA_VERSION
    ready: bool
    treatment_admissible: bool
    provider: str
    endpoint: str
    model: str
    provider_profile_id: str | None = None
    provider_profile_version: str | None = None
    tokenizer_id: str
    tokenizer_available: bool
    summary_schema_version: str = SUMMARY_SCHEMA_VERSION
    summary_adapter_version: str = SUMMARY_ADAPTER_VERSION
    attempt_contract_version: str = ATTEMPT_CONTRACT_VERSION
    budget_policy: RollingSummaryBudgetPolicy
    flags: ExperimentFlags
    blockers: list[ReadinessBlocker] = Field(default_factory=list)

    @property
    def blocker_codes(self) -> frozenset[ReadinessBlockerCode]:
        return frozenset(blocker.code for blocker in self.blockers)

    @model_validator(mode="after")
    def _readiness_matches_blockers(self) -> "ProviderReadiness":
        if self.ready != (not self.blockers):
            raise ValueError("ready must be true exactly when no readiness blockers exist")
        if self.treatment_admissible and not (
            self.ready
            and self.flags.treatment_enabled
            and self.flags.kill_switch_armed
            and self.flags.read_only
        ):
            raise ValueError("treatment_admissible is inconsistent with flags/readiness")
        return self


class RollingSummaryExperimentManifest(BaseModel):
    """Immutable source/provider binding for one shadow or canary attempt."""

    model_config = ConfigDict(extra="forbid")

    manifest_version: str = MANIFEST_SCHEMA_VERSION
    experiment_id: str = Field(min_length=1)
    arm: ExperimentArm
    provider: str
    endpoint: str
    model: str
    provider_profile_id: str
    provider_profile_version: str
    tokenizer_id: str
    summary_schema_version: str = SUMMARY_SCHEMA_VERSION
    summary_adapter_version: str = SUMMARY_ADAPTER_VERSION
    attempt_contract_version: str = ATTEMPT_CONTRACT_VERSION
    budget_policy: RollingSummaryBudgetPolicy
    flags: ExperimentFlags
    source_envelope_hash: str = Field(pattern=_HASH_PATTERN)
    session_turn_source_hash: str = Field(pattern=_HASH_PATTERN)
    constraint_hash: str = Field(pattern=_HASH_PATTERN)
    task_input_hash: str = Field(pattern=_HASH_PATTERN)
    completion_policy_hash: str = Field(pattern=_HASH_PATTERN)
    manifest_hash: str = Field(pattern=_HASH_PATTERN)


def _canonical_manifest_hash(manifest: RollingSummaryExperimentManifest) -> str:
    payload = manifest.model_dump(mode="json", exclude={"manifest_hash"})
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_manifest_hash(manifest: RollingSummaryExperimentManifest) -> bool:
    """Verify that the manifest has not been edited after construction."""

    return manifest.manifest_hash == _canonical_manifest_hash(manifest)


def _endpoint_identity(base_url: Any) -> tuple[str, ReadinessBlocker | None]:
    raw = str(base_url or "").strip()
    try:
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("endpoint must be an http(s) URL with a host")
        # Accessing ``port`` catches malformed values such as ``:abc``.
        _ = parsed.port
        endpoint = normalized_provider_endpoint(raw)
    except (TypeError, ValueError, UnicodeError) as exc:
        return "", ReadinessBlocker(
            code=ReadinessBlockerCode.INVALID_ENDPOINT,
            detail=f"provider endpoint is invalid: {type(exc).__name__}",
        )
    return endpoint, None


def assess_provider_readiness(
    settings: Any,
    *,
    flags: ExperimentFlags | None = None,
    budget_policy: RollingSummaryBudgetPolicy,
    token_counter: ProviderTokenCounter | None = None,
) -> ProviderReadiness:
    """Assess a provider without constructing a client or making network calls."""

    active_flags = flags or ExperimentFlags()
    blockers: list[ReadinessBlocker] = []
    endpoint, endpoint_blocker = _endpoint_identity(getattr(settings, "base_url", ""))
    if endpoint_blocker is not None:
        blockers.append(endpoint_blocker)

    provider = str(getattr(settings, "provider", "") or "").strip()
    model = str(getattr(settings, "model", "") or "").strip()
    if not model:
        blockers.append(
            ReadinessBlocker(
                code=ReadinessBlockerCode.MISSING_MODEL,
                detail="provider model is empty",
            )
        )
    if not str(getattr(settings, "api_key", "") or "").strip():
        blockers.append(
            ReadinessBlocker(
                code=ReadinessBlockerCode.MISSING_CREDENTIALS,
                detail="provider credentials are unavailable",
            )
        )

    profile_id: str | None = None
    profile_version: str | None = None
    explicit_profile = getattr(settings, "reasoning_capability_profile", None)
    if explicit_profile is not None:
        try:
            profile = select_reasoning_capability_profile(settings)
            profile_id = profile.profile_id.value
            profile_version = profile.version
        except (TypeError, ValueError, AttributeError):
            blockers.append(
                ReadinessBlocker(
                    code=ReadinessBlockerCode.PROFILE_NOT_EXPLICIT,
                    detail="configured reasoning capability profile could not be resolved",
                )
            )
    elif active_flags.treatment_enabled:
        blockers.append(
            ReadinessBlocker(
                code=ReadinessBlockerCode.PROFILE_NOT_EXPLICIT,
                detail="Treatment requires an explicit versioned reasoning profile",
            )
        )

    counter = token_counter or ProviderTokenCounter.from_settings(settings)
    tokenizer_id = str(getattr(counter, "tokenizer_id", "unavailable") or "unavailable")
    tokenizer_available = bool(getattr(counter, "available", False))
    if active_flags.treatment_enabled and not tokenizer_available:
        blockers.append(
            ReadinessBlocker(
                code=ReadinessBlockerCode.TOKENIZER_UNAVAILABLE,
                detail="Treatment requires exact local provider token counting",
            )
        )

    try:
        # Validate the policy by exercising the same helper used at assembly.
        budget_policy.dynamic_summary_limit(
            requested_prompt_tokens=budget_policy.static_cap_tokens,
            used_prompt_tokens=0,
        )
    except (TypeError, ValueError):
        blockers.append(
            ReadinessBlocker(
                code=ReadinessBlockerCode.INVALID_BUDGET,
                detail="summary budget policy is not valid",
            )
        )
    if budget_policy.dynamic_policy_version != "dynamic_reserve_v1":
        blockers.append(
            ReadinessBlocker(
                code=ReadinessBlockerCode.INVALID_BUDGET,
                detail="unsupported dynamic summary budget policy version",
            )
        )
    if SUMMARY_SCHEMA_VERSION != "context_compaction_summary_v1":
        blockers.append(
            ReadinessBlocker(
                code=ReadinessBlockerCode.INVALID_SCHEMA,
                detail="unsupported rolling summary schema version",
            )
        )
    if active_flags.treatment_enabled and not active_flags.kill_switch_armed:
        blockers.append(
            ReadinessBlocker(
                code=ReadinessBlockerCode.KILL_SWITCH_NOT_ARMED,
                detail="Treatment requires the kill switch to remain armed",
            )
        )
    if active_flags.treatment_enabled and not active_flags.read_only:
        blockers.append(
            ReadinessBlocker(
                code=ReadinessBlockerCode.NON_READ_ONLY,
                detail="rolling-summary experiment must be read-only",
            )
        )

    ready = not blockers
    treatment_admissible = ready and active_flags.treatment_enabled
    return ProviderReadiness(
        ready=ready,
        treatment_admissible=treatment_admissible,
        provider=provider,
        endpoint=endpoint,
        model=model,
        provider_profile_id=profile_id,
        provider_profile_version=profile_version,
        tokenizer_id=tokenizer_id,
        tokenizer_available=tokenizer_available,
        budget_policy=budget_policy,
        flags=active_flags,
        blockers=blockers,
    )


def build_experiment_manifest(
    readiness: ProviderReadiness,
    *,
    experiment_id: str,
    arm: ExperimentArm,
    source_envelope_hash: str,
    session_turn_source_hash: str,
    constraint_hash: str,
    task_input_hash: str,
    completion_policy_hash: str,
) -> RollingSummaryExperimentManifest:
    """Bind one arm to immutable source and provider identity."""

    if arm is ExperimentArm.TREATMENT and not readiness.treatment_admissible:
        codes = ", ".join(sorted(code.value for code in readiness.blocker_codes)) or "unknown"
        raise ManifestValidationError(f"Treatment is not admissible: {codes}")
    profile_id = readiness.provider_profile_id or "not-selected"
    profile_version = readiness.provider_profile_version or "not-selected"
    provisional = RollingSummaryExperimentManifest(
        experiment_id=experiment_id,
        arm=arm,
        provider=readiness.provider,
        endpoint=readiness.endpoint,
        model=readiness.model,
        provider_profile_id=profile_id,
        provider_profile_version=profile_version,
        tokenizer_id=readiness.tokenizer_id,
        budget_policy=readiness.budget_policy,
        flags=readiness.flags,
        source_envelope_hash=source_envelope_hash,
        session_turn_source_hash=session_turn_source_hash,
        constraint_hash=constraint_hash,
        task_input_hash=task_input_hash,
        completion_policy_hash=completion_policy_hash,
        manifest_hash="sha256:" + "0" * 64,
    )
    return provisional.model_copy(update={"manifest_hash": _canonical_manifest_hash(provisional)})


__all__ = [
    "ATTEMPT_CONTRACT_VERSION",
    "ExperimentArm",
    "ExperimentFlags",
    "ManifestValidationError",
    "ProviderReadiness",
    "ReadinessBlocker",
    "ReadinessBlockerCode",
    "RollingSummaryBudgetPolicy",
    "RollingSummaryExperimentManifest",
    "assess_provider_readiness",
    "build_experiment_manifest",
    "verify_manifest_hash",
]
