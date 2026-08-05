"""Offline admission contract for the full-session compact canary.

Stage 5A deliberately has no Provider execution path.  It validates the
feature flag, reversible runtime controls, independent usage accounts, and
hash-locked evidence needed by a later, separately reviewed canary stage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


HERE = Path(__file__).resolve().parent
DEFAULT_PROTOCOL_PATH = HERE / "STAGE12_SESSION_COMPACT_CANARY_ADMISSION_V1.json"


class AdmissionError(ValueError):
    """The canary admission contract or its evidence is invalid."""


class ProjectionFeatureFlag(str, Enum):
    DISABLED = "disabled"
    CANARY_ENABLED = "canary_enabled"


class RuntimeKillSwitch(str, Enum):
    ARMED = "armed"
    ENGAGED = "engaged"


class ProjectionPolicy(str, Enum):
    CURRENT = "current"
    COMPACT = "compact"


class UsageRoute(str, Enum):
    PRIMARY = "primary"
    FALLBACK = "fallback"


class UsageAccountingContract(BaseModel):
    """Two non-overlapping ledgers for treatment and fallback requests."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    primary_account_id: str = Field(min_length=1)
    fallback_account_id: str = Field(min_length=1)
    unknown_usage_action: Literal["stop"] = "stop"
    cross_account_reconciliation_allowed: Literal[False] = False

    @model_validator(mode="after")
    def _accounts_are_independent(self) -> "UsageAccountingContract":
        if self.primary_account_id == self.fallback_account_id:
            raise ValueError("primary and fallback usage accounts must differ")
        return self


class CanaryHardCaps(BaseModel):
    """Hard admission limits shared by every later canary transport."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    per_arm_tokens: int = Field(gt=0)
    campaign_tokens: int = Field(gt=0)
    per_arm_calls: int = Field(gt=0)
    campaign_calls: int = Field(gt=0)
    per_arm_wall_clock_seconds: int = Field(gt=0)
    campaign_wall_clock_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def _arm_fits_campaign(self) -> "CanaryHardCaps":
        if self.per_arm_tokens > self.campaign_tokens:
            raise ValueError("per-arm token cap cannot exceed campaign cap")
        if self.per_arm_calls > self.campaign_calls:
            raise ValueError("per-arm call cap cannot exceed campaign cap")
        if self.per_arm_wall_clock_seconds > self.campaign_wall_clock_seconds:
            raise ValueError("per-arm wall-clock cap cannot exceed campaign cap")
        return self


class EnvironmentReceipt(BaseModel):
    """Read-only environment fact; it never authorizes setup or transport."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    readiness: Literal["ready", "not_ready"]
    executable_path: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    read_only_preflight_authorized: bool
    provider_transport_authorized: Literal[False] = False
    environment_setup_authorized: Literal[False] = False
    network_authorized: Literal[False] = False


class HashLocks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_path: str = Field(min_length=1)
    fixture_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    stage9_result_path: str = Field(min_length=1)
    stage9_result_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class NoProviderPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_transport_allowed: Literal[False] = False
    network_allowed: Literal[False] = False
    project_mutation_allowed: Literal[False] = False


class SessionCompactCanaryAdmission(BaseModel):
    """Experiment-owned control value; it is not production runtime authority."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    contract_version: Literal["1"] = "1"
    canary_id: str = Field(min_length=1)
    projection_feature_flag: ProjectionFeatureFlag
    runtime_kill_switch: RuntimeKillSwitch
    primary_projection: ProjectionPolicy
    fallback_projection: ProjectionPolicy
    usage_accounting: UsageAccountingContract
    hard_caps: CanaryHardCaps
    environment_receipt: EnvironmentReceipt
    hash_locks: HashLocks
    preflight: NoProviderPreflight

    @model_validator(mode="after")
    def _projection_is_reversible(self) -> "SessionCompactCanaryAdmission":
        if self.primary_projection != ProjectionPolicy.COMPACT:
            raise ValueError("the canary treatment must be compact")
        if self.fallback_projection != ProjectionPolicy.CURRENT:
            raise ValueError("the canary fallback must remain current")
        return self


class UsageObservation(BaseModel):
    """One observed Provider response assigned to exactly one canary ledger."""

    model_config = ConfigDict(extra="forbid")

    execution_id: str = Field(min_length=1)
    route: UsageRoute
    account_id: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    usage_observed: Literal[True] = True

    @model_validator(mode="after")
    def _total_reconciles(self) -> "UsageObservation":
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("usage total does not reconcile")
        return self


class UsageReservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str = Field(min_length=1)
    route: UsageRoute
    account_id: str = Field(min_length=1)
    reserved_tokens: int = Field(gt=0)


class CanaryUsageLedger(BaseModel):
    """Apply-once reservation ledger enforcing caps before transport."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    accounting: UsageAccountingContract
    hard_caps: CanaryHardCaps
    reservations: list[UsageReservation] = Field(default_factory=list)
    observations: list[UsageObservation] = Field(default_factory=list)

    def reserve(
        self,
        *,
        execution_id: str,
        route: UsageRoute,
        reserved_tokens: int,
    ) -> "CanaryUsageLedger":
        if execution_id in {item.execution_id for item in self.reservations}:
            raise AdmissionError("usage reservation execution ID already exists")
        if len(self.reservations) >= self.hard_caps.per_arm_calls:
            raise AdmissionError("provider call hard cap reached before transport")
        if reserved_tokens > self.hard_caps.per_arm_tokens:
            raise AdmissionError("per-arm token hard cap exceeded before transport")
        settled_ids = {item.execution_id for item in self.observations}
        settled = sum(item.total_tokens for item in self.observations)
        held = sum(
            item.reserved_tokens
            for item in self.reservations
            if item.execution_id not in settled_ids
        )
        if settled + held + reserved_tokens > self.hard_caps.campaign_tokens:
            raise AdmissionError("campaign token hard cap exceeded before transport")
        account_id = (
            self.accounting.primary_account_id
            if route == UsageRoute.PRIMARY
            else self.accounting.fallback_account_id
        )
        reservation = UsageReservation(
            execution_id=execution_id,
            route=route,
            account_id=account_id,
            reserved_tokens=reserved_tokens,
        )
        return self.model_copy(update={"reservations": [*self.reservations, reservation]})

    def reconcile(
        self,
        observation: UsageObservation | None,
        *,
        execution_id: str,
    ) -> "CanaryUsageLedger":
        if observation is None:
            raise AdmissionError("unknown usage stops the canary")
        matches = [item for item in self.reservations if item.execution_id == execution_id]
        if len(matches) != 1 or observation.execution_id != execution_id:
            raise AdmissionError("usage has no unique reservation")
        reservation = matches[0]
        if observation.route != reservation.route or observation.account_id != reservation.account_id:
            raise AdmissionError("usage does not match its reserved route and account")
        if observation.total_tokens > reservation.reserved_tokens:
            raise AdmissionError("observed usage exceeded its reservation")
        if execution_id in {item.execution_id for item in self.observations}:
            raise AdmissionError("usage execution ID was already reconciled")
        return self.model_copy(update={"observations": [*self.observations, observation]})

    def enforce_wall_clock(self, elapsed_seconds: float) -> None:
        if (
            elapsed_seconds < 0
            or elapsed_seconds > self.hard_caps.per_arm_wall_clock_seconds
        ):
            raise AdmissionError("wall-clock hard cap exceeded")


class CanaryCampaignLedger(BaseModel):
    """Cross-arm ledger enforcing campaign-wide caps before transport.

    ``CanaryUsageLedger`` remains useful for one isolated arm.  A paired
    campaign needs a separate owner because summing two arm ledgers after the
    fact cannot prevent the second arm from crossing the shared call/token
    ceiling.  This experiment-only value keeps the arm assignment typed and
    preserves independent primary/fallback accounts.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    accounting: UsageAccountingContract
    hard_caps: CanaryHardCaps
    reservations: list[UsageReservation] = Field(default_factory=list)
    observations: list[UsageObservation] = Field(default_factory=list)
    reservation_arms: dict[str, ProjectionPolicy] = Field(default_factory=dict)

    def _arm_value(self, arm: ProjectionPolicy | str) -> ProjectionPolicy:
        try:
            return ProjectionPolicy(arm)
        except (TypeError, ValueError) as exc:
            raise AdmissionError(f"unknown canary arm: {arm}") from exc

    def reserve(
        self,
        *,
        execution_id: str,
        arm: ProjectionPolicy | str,
        route: UsageRoute,
        reserved_tokens: int,
    ) -> "CanaryCampaignLedger":
        arm_value = self._arm_value(arm)
        if execution_id in self.reservation_arms:
            raise AdmissionError("usage reservation execution ID already exists")
        if len(self.reservations) >= self.hard_caps.campaign_calls:
            raise AdmissionError("campaign call hard cap reached before transport")
        arm_reservations = [
            item
            for item in self.reservations
            if self.reservation_arms.get(item.execution_id) == arm_value
        ]
        if len(arm_reservations) >= self.hard_caps.per_arm_calls:
            raise AdmissionError("per-arm call hard cap reached before transport")
        if reserved_tokens > self.hard_caps.per_arm_tokens:
            raise AdmissionError("per-arm token hard cap exceeded before transport")
        settled_ids = {item.execution_id for item in self.observations}
        campaign_settled = sum(item.total_tokens for item in self.observations)
        campaign_held = sum(
            item.reserved_tokens
            for item in self.reservations
            if item.execution_id not in settled_ids
        )
        if campaign_settled + campaign_held + reserved_tokens > self.hard_caps.campaign_tokens:
            raise AdmissionError("campaign token hard cap exceeded before transport")
        arm_settled = sum(
            item.total_tokens
            for item in self.observations
            if self.reservation_arms.get(item.execution_id) == arm_value
        )
        arm_held = sum(
            item.reserved_tokens
            for item in arm_reservations
            if item.execution_id not in settled_ids
        )
        if arm_settled + arm_held + reserved_tokens > self.hard_caps.per_arm_tokens:
            raise AdmissionError("per-arm token hard cap exceeded before transport")
        account_id = (
            self.accounting.primary_account_id
            if route == UsageRoute.PRIMARY
            else self.accounting.fallback_account_id
        )
        reservation = UsageReservation(
            execution_id=execution_id,
            route=route,
            account_id=account_id,
            reserved_tokens=reserved_tokens,
        )
        return self.model_copy(
            update={
                "reservations": [*self.reservations, reservation],
                "reservation_arms": {**self.reservation_arms, execution_id: arm_value},
            }
        )

    def reconcile(
        self,
        observation: UsageObservation | None,
        *,
        arm: ProjectionPolicy | str,
        execution_id: str | None = None,
    ) -> "CanaryCampaignLedger":
        if observation is None:
            raise AdmissionError("unknown usage stops the canary")
        arm_value = self._arm_value(arm)
        observed_id = execution_id or observation.execution_id
        if observation.execution_id != observed_id:
            raise AdmissionError("usage execution ID does not match reconciliation ID")
        if self.reservation_arms.get(observed_id) != arm_value:
            raise AdmissionError("usage arm does not match its reservation")
        matches = [item for item in self.reservations if item.execution_id == observed_id]
        if len(matches) != 1:
            raise AdmissionError("usage has no unique reservation")
        reservation = matches[0]
        if observation.route != reservation.route or observation.account_id != reservation.account_id:
            raise AdmissionError("usage does not match its reserved route and account")
        if observation.total_tokens > reservation.reserved_tokens:
            raise AdmissionError("observed usage exceeded its reservation")
        if observed_id in {item.execution_id for item in self.observations}:
            raise AdmissionError("usage execution ID was already reconciled")
        return self.model_copy(update={"observations": [*self.observations, observation]})

    def enforce_wall_clock(
        self,
        *,
        arm: ProjectionPolicy | str,
        arm_elapsed_seconds: float,
        campaign_elapsed_seconds: float,
    ) -> None:
        self._arm_value(arm)
        if (
            arm_elapsed_seconds < 0
            or arm_elapsed_seconds > self.hard_caps.per_arm_wall_clock_seconds
        ):
            raise AdmissionError("per-arm wall-clock hard cap exceeded")
        if (
            campaign_elapsed_seconds < 0
            or campaign_elapsed_seconds > self.hard_caps.campaign_wall_clock_seconds
        ):
            raise AdmissionError("campaign wall-clock hard cap exceeded")


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _locked_path(protocol_path: Path, raw_path: str) -> Path:
    root = protocol_path.resolve().parent
    candidate = (root / raw_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise AdmissionError("hash-locked evidence escapes the experiment root") from exc
    if not candidate.is_file():
        raise AdmissionError(f"hash-locked evidence is missing: {raw_path}")
    return candidate


def summarize_usage(
    observations: list[UsageObservation],
    accounting: UsageAccountingContract,
) -> dict[str, dict[str, int]]:
    """Reconcile primary and current-fallback usage without pooling them."""

    execution_ids = [item.execution_id for item in observations]
    if len(execution_ids) != len(set(execution_ids)):
        raise AdmissionError("usage execution IDs must be unique")
    expected_accounts = {
        UsageRoute.PRIMARY: accounting.primary_account_id,
        UsageRoute.FALLBACK: accounting.fallback_account_id,
    }
    totals = {
        route.value: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for route in UsageRoute
    }
    for item in observations:
        if item.account_id != expected_accounts[item.route]:
            raise AdmissionError(f"{item.route.value} usage was assigned to the wrong account")
        bucket = totals[item.route.value]
        bucket["requests"] += 1
        bucket["input_tokens"] += item.input_tokens
        bucket["output_tokens"] += item.output_tokens
        bucket["total_tokens"] += item.total_tokens
    return totals


def admission_stop_reasons(admission: SessionCompactCanaryAdmission) -> list[str]:
    """Evaluate only reversible runtime controls; hashes are preflight evidence."""

    reasons: list[str] = []
    if admission.projection_feature_flag != ProjectionFeatureFlag.CANARY_ENABLED:
        reasons.append("projection_feature_flag_disabled")
    if admission.runtime_kill_switch != RuntimeKillSwitch.ARMED:
        reasons.append("runtime_kill_switch_engaged")
    receipt = admission.environment_receipt
    if receipt.readiness != "ready":
        reasons.append("environment_not_ready")
    if not receipt.read_only_preflight_authorized:
        reasons.append("environment_preflight_not_authorized")
    return reasons


def run_no_provider_preflight(
    protocol_path: Path = DEFAULT_PROTOCOL_PATH,
) -> dict[str, Any]:
    """Validate admission evidence without exposing a Provider transport hook."""

    resolved_protocol = protocol_path.resolve()
    try:
        admission = SessionCompactCanaryAdmission.model_validate_json(
            resolved_protocol.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise AdmissionError(f"invalid canary protocol: {exc}") from exc

    locks = admission.hash_locks
    fixture_path = _locked_path(resolved_protocol, locks.fixture_path)
    stage9_path = _locked_path(resolved_protocol, locks.stage9_result_path)
    if _sha256_file(fixture_path) != locks.fixture_sha256:
        raise AdmissionError("fixture hash mismatch")
    if _sha256_file(stage9_path) != locks.stage9_result_sha256:
        raise AdmissionError("Stage 9 result hash mismatch")

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if (
        fixture.get("projection_scope") != "full_session_prompt"
        or fixture.get("primary_projection") != admission.primary_projection.value
        or fixture.get("fallback_projection") != admission.fallback_projection.value
    ):
        raise AdmissionError("fixture projection contract mismatch")
    stage9 = json.loads(stage9_path.read_text(encoding="utf-8"))
    if not (
        stage9.get("status") == "completed"
        and stage9.get("eligible") is True
        and stage9.get("independent_safety_audit") == "conditional_go"
    ):
        raise AdmissionError("Stage 9 prerequisite is not a conditional GO")

    # Preserve the workspace-owned venv path even when its interpreter is a
    # legitimate symlink to the base Python installation.
    executable = Path(
        os.path.abspath(resolved_protocol.parent / admission.environment_receipt.executable_path)
    )
    workspace_root = HERE.parents[1]
    try:
        executable.relative_to(workspace_root)
    except ValueError as exc:
        raise AdmissionError("environment executable escapes the workspace") from exc
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise AdmissionError("environment executable is not ready")

    stop_reasons = admission_stop_reasons(admission)
    return {
        "canary_id": admission.canary_id,
        "status": "passed" if not stop_reasons else "stopped",
        "controls_admitted": not stop_reasons,
        # Echo the locked typed control values so downstream gates can verify
        # the preflight result itself, rather than trusting only their inputs.
        "projection_feature_flag": admission.projection_feature_flag.value,
        "runtime_kill_switch": admission.runtime_kill_switch.value,
        "provider_execution_admitted": False,
        "provider_calls": 0,
        "network_calls": 0,
        "project_mutations": 0,
        "protocol_sha256": _sha256_file(resolved_protocol),
        "fixture_sha256": _sha256_file(fixture_path),
        "stage9_result_sha256": _sha256_file(stage9_path),
        "primary_usage_account": admission.usage_accounting.primary_account_id,
        "fallback_usage_account": admission.usage_accounting.fallback_account_id,
        "environment_id": admission.environment_receipt.environment_id,
        "environment_executable": str(executable),
        "hard_caps": admission.hard_caps.model_dump(mode="json"),
        "stop_reasons": stop_reasons,
        "next_stage": "separately_reviewed_provider_canary",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    args = parser.parse_args()
    print(json.dumps(run_no_provider_preflight(args.protocol), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
