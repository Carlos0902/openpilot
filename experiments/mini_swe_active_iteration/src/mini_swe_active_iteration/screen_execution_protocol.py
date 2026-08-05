"""Strict pre-execution contract for the 12-task core-benefit screen."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

from .contracts import BudgetLimits
from .core_benefit_screen import CoreBenefitScreenStageManifest
from .provider import SharedProviderSpec


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_sha256(value: str, *, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256")


class PairArmOrder(_StrictModel):
    task_id: str
    order: str

    @model_validator(mode="after")
    def validate_order(self) -> "PairArmOrder":
        if not self.task_id.strip() or self.order not in {
            "active_first",
            "ordinary_first",
        }:
            raise ValueError("unsupported pair arm order")
        return self


class ScreenExecutionProtocol(_StrictModel):
    schema_version: str
    protocol_id: str
    lifecycle: str
    stage_manifest_sha256: str
    pool_sha256: str
    task_ids: tuple[str, ...]
    arms: tuple[str, ...]
    provider: SharedProviderSpec
    provider_fingerprint: str
    common_budget: dict[str, int | float]
    model_retry_attempts: int
    pair_arm_orders: tuple[PairArmOrder, ...]
    provider_transport_failure_policy: str
    timeout_policy: str
    output_directory: str
    controller_prompt_sha256: str
    bash_tool_schema_sha256: str
    runner_sha256: str
    evaluator_sha256: str
    task_arm_provider_execution_authorized: bool
    production_execution_authorized: bool
    hypothesis_evidence_eligible: bool
    outcomes_generated: bool

    @model_validator(mode="after")
    def validate_protocol(self) -> "ScreenExecutionProtocol":
        if (
            self.schema_version != "1.0"
            or self.lifecycle != "exploratory_screen"
            or not self.protocol_id.strip()
            or self.arms != ("ordinary", "active_iteration")
            or len(self.task_ids) != 12
            or len(set(self.task_ids)) != 12
            or any(not task_id.strip() for task_id in self.task_ids)
            or self.model_retry_attempts != 1
            or self.provider_transport_failure_policy
            != "fail_closed_count_in_denominator"
            or self.timeout_policy != "fail_closed_count_in_denominator"
            or self.production_execution_authorized
            or self.hypothesis_evidence_eligible
            or self.outcomes_generated
        ):
            raise ValueError("screen execution protocol has an unsafe invariant")
        if Path(self.output_directory).is_absolute() or ".." in Path(
            self.output_directory
        ).parts:
            raise ValueError("output_directory must be a safe relative path")
        for field in (
            "stage_manifest_sha256",
            "pool_sha256",
            "provider_fingerprint",
            "controller_prompt_sha256",
            "bash_tool_schema_sha256",
            "runner_sha256",
            "evaluator_sha256",
        ):
            _require_sha256(getattr(self, field), field=field)
        BudgetLimits(**self.common_budget)
        if tuple(order.task_id for order in self.pair_arm_orders) != self.task_ids:
            raise ValueError("pair orders must exactly follow the frozen task order")
        if sum(order.order == "active_first" for order in self.pair_arm_orders) != 6:
            raise ValueError("protocol requires six active-first task pairs")
        if sum(order.order == "ordinary_first" for order in self.pair_arm_orders) != 6:
            raise ValueError("protocol requires six ordinary-first task pairs")
        return self


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_file_fingerprint(paths: tuple[Path, ...]) -> str:
    """Hash exact source files by relative name and content hash."""

    payload = {
        path.name: _sha256(path)
        for path in paths
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def execution_runner_fingerprint(package_root: Path) -> str:
    source = package_root / "src" / "mini_swe_active_iteration"
    air_runner = (
        source / "exploratory_runtime.py",
        source / "exploratory_pair_runner.py",
    )
    if all(path.is_file() for path in air_runner):
        return _canonical_file_fingerprint(
            (
                source / "adapter.py",
                source / "controller.py",
                source / "mini_agent.py",
                source / "runner.py",
                *air_runner,
                source / "swebench_network_runner.py",
            )
        )
    return _canonical_file_fingerprint(
        (
            source / "adapter.py",
            source / "controller.py",
            source / "mini_agent.py",
            source / "runner.py",
            source / "swebench_agent_environment.py",
            source / "swebench_task_runner.py",
        )
    )


def evaluator_fingerprint(package_root: Path) -> str:
    return _sha256(
        package_root
        / "src"
        / "mini_swe_active_iteration"
        / "swebench_network_runner.py"
    )


def validate_screen_execution_protocol_bindings(
    *,
    protocol: ScreenExecutionProtocol,
    stage_manifest_path: Path,
) -> tuple[str, ...]:
    """Verify the frozen protocol points to exactly one Stage A manifest."""

    if stage_manifest_path.is_symlink() or not stage_manifest_path.is_file():
        return ("missing_or_symlink_stage_manifest",)
    try:
        manifest = CoreBenefitScreenStageManifest.model_validate_json(
            stage_manifest_path.read_text()
        )
    except ValueError:
        return ("invalid_stage_manifest",)
    errors: list[str] = []
    if protocol.stage_manifest_sha256 != _sha256(stage_manifest_path):
        errors.append("stage_manifest_sha256_mismatch")
    if protocol.pool_sha256 != manifest.pool_sha256:
        errors.append("pool_sha256_mismatch")
    if protocol.task_ids != tuple(entry.instance_id for entry in manifest.entries):
        errors.append("task_ids_mismatch")
    if protocol.provider_fingerprint != protocol.provider.fingerprint():
        errors.append("provider_fingerprint_mismatch")
    return tuple(errors)
