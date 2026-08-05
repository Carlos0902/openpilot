"""Frozen one-task provider development smoke for the mini-SWE experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import yaml
from minisweagent import package_dir as mini_package_dir
from minisweagent.models.utils.actions_toolcall import BASH_TOOL
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .adapter import MiniAgentAdapter
from .contracts import BudgetLimits, ExperimentArm, ExperimentResult, Usage
from .controller import ActiveIterationController, CONTROLLER_INSTRUCTIONS
from .development_runner import PairedDevelopmentRunner
from .fixture import load_development_suite
from .provider import SharedProviderFactory, SharedProviderSpec
from .sandbox import SeatbeltEnvironment


class SmokeDevelopmentProtocol(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    protocol_id: str
    lifecycle: str
    phase: int
    task_ids: tuple[str, ...]
    arms: tuple[str, ...]
    provider: SharedProviderSpec
    provider_fingerprint: str
    common_budget: dict[str, int | float]
    model_retry_attempts: int
    run_order_random_seed: int
    command_timeout_seconds: int
    output_directory: str
    mini_agent_config_sha256: str
    phase0_protocol_sha256: str
    development_task_suite_sha256: str
    controller_prompt_sha256: str
    bash_tool_schema_sha256: str
    runner_fingerprint: str
    artifact_sha256: dict[str, str]
    invalid_prior_trials: dict[str, str] = Field(default_factory=dict)
    provider_execution_authorized: bool
    production_execution_authorized: bool
    hypothesis_evidence_eligible: bool
    outcomes_generated: bool

    @model_validator(mode="after")
    def validate_protocol(self) -> "SmokeDevelopmentProtocol":
        if self.schema_version != "1.0":
            raise ValueError("unsupported smoke protocol schema")
        if self.lifecycle != "development" or self.phase != 0:
            raise ValueError("smoke protocol must remain phase-zero development")
        if self.arms != ("ordinary", "active_iteration"):
            raise ValueError("smoke protocol arms must be ordinary and active")
        if len(self.task_ids) != 1 or len(set(self.task_ids)) != 1:
            raise ValueError("development smoke must contain exactly one task")
        if not self.provider_execution_authorized:
            raise ValueError("smoke protocol must explicitly authorize provider execution")
        if (
            self.production_execution_authorized
            or self.hypothesis_evidence_eligible
            or self.outcomes_generated
        ):
            raise ValueError("smoke protocol cannot authorize production or outcomes")
        if self.model_retry_attempts != 1:
            raise ValueError("development smoke freezes exactly one model attempt")
        if Path(self.output_directory).is_absolute() or ".." in Path(
            self.output_directory
        ).parts:
            raise ValueError("output_directory must be a safe relative path")
        BudgetLimits(**self.common_budget)
        return self

    def canonical_artifact_manifest(self) -> str:
        return json.dumps(
            self.artifact_sha256,
            sort_keys=True,
            separators=(",", ":"),
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def load_smoke_protocol(path: Path) -> SmokeDevelopmentProtocol:
    if path.is_symlink() or not path.is_file():
        raise ValueError("smoke protocol must be a regular non-symlink file")
    return SmokeDevelopmentProtocol.model_validate_json(path.read_text())


def _artifact_path(package_root: Path, relative_path: str) -> Path:
    if relative_path.startswith("docs/"):
        return package_root.parents[1] / relative_path
    return package_root / relative_path


def validate_smoke_protocol_bindings(
    *,
    protocol: SmokeDevelopmentProtocol,
    package_root: Path,
) -> tuple[str, ...]:
    errors: list[str] = []
    for relative_path, expected_hash in protocol.artifact_sha256.items():
        path = _artifact_path(package_root, relative_path)
        if not path.is_file() or path.is_symlink():
            errors.append(f"missing_or_symlink:{relative_path}")
        elif _sha256(path) != expected_hash:
            errors.append(f"hash_mismatch:{relative_path}")

    expected_runner = hashlib.sha256(
        protocol.canonical_artifact_manifest().encode()
    ).hexdigest()
    checks = {
        "runner_fingerprint": (
            protocol.runner_fingerprint,
            expected_runner,
        ),
        "provider_fingerprint": (
            protocol.provider_fingerprint,
            protocol.provider.fingerprint(),
        ),
        "phase0_protocol_sha256": (
            protocol.phase0_protocol_sha256,
            _sha256(package_root / "PHASE0_DEVELOPMENT_PROTOCOL_V1.json"),
        ),
        "development_task_suite_sha256": (
            protocol.development_task_suite_sha256,
            _sha256(package_root / "DEVELOPMENT_TASK_SUITE_V1.json"),
        ),
        "mini_agent_config_sha256": (
            protocol.mini_agent_config_sha256,
            _sha256(Path(mini_package_dir) / "config/default.yaml"),
        ),
        "controller_prompt_sha256": (
            protocol.controller_prompt_sha256,
            hashlib.sha256(CONTROLLER_INSTRUCTIONS.encode()).hexdigest(),
        ),
        "bash_tool_schema_sha256": (
            protocol.bash_tool_schema_sha256,
            _canonical_sha256(BASH_TOOL),
        ),
    }
    errors.extend(
        f"{name}_mismatch"
        for name, (actual, expected) in checks.items()
        if actual != expected
    )
    return tuple(errors)


def _usage_payload(usage: Usage) -> dict[str, int | float]:
    return {
        "provider_calls": usage.provider_calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "tool_calls": usage.tool_calls,
        "iterations": usage.iterations,
        "wall_time_seconds": usage.wall_time_seconds,
    }


def _arm_payload(result: ExperimentResult) -> dict[str, Any]:
    provider_receipts = sum(
        1
        for message in result.messages
        if isinstance(message.get("extra", {}).get("response"), dict)
    )
    controller_receipts = (
        result.trajectory.get("experiment", {})
        .get("active_iteration", {})
        .get("controller_receipts", [])
    )
    total_provider_calls = (
        result.agent_usage.provider_calls
        + result.controller_usage.provider_calls
    )
    return {
        "arm": result.arm.value,
        "exit_status": result.exit_status,
        "submission": result.submission,
        "commands": list(result.commands),
        "agent_usage": _usage_payload(result.agent_usage),
        "controller_usage": _usage_payload(result.controller_usage),
        "total_usage": _usage_payload(result.total_usage),
        "evaluation": result.evaluation,
        "final_files": result.final_files,
        "cleanup_succeeded": result.cleanup_succeeded,
        "false_success": (
            result.exit_status == "Submitted"
            and not bool(result.evaluation.get("verified_task_success"))
        ),
        "agent_provider_receipt_count": provider_receipts,
        "controller_provider_receipt_count": len(controller_receipts),
        "provider_complete": (
            bool(result.exit_status)
            and total_provider_calls > 0
            and provider_receipts == result.agent_usage.provider_calls
            and len(controller_receipts)
            == result.controller_usage.provider_calls
        ),
    }


def _boundary_gate_passed(execution: dict[str, Any]) -> bool:
    return bool(
        execution["source_stable"]
        and all(
            arm["cleanup_succeeded"] and arm["provider_complete"]
            for arm in execution["arms"].values()
        )
    )


def _safety_gate_passed(execution: dict[str, Any]) -> bool:
    """Apply the protocol's noncompensating run and outcome safety checks."""
    return bool(
        _boundary_gate_passed(execution)
        and not execution["paired_regression"]
        and all(
            not arm["false_success"]
            for arm in execution["arms"].values()
        )
    )


@contextmanager
def _provider_environment(*, api_key: str) -> Iterator[None]:
    names_and_values = {
        "OPENAI_API_KEY": api_key,
        "MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT": "1",
    }
    previous = {name: os.environ.get(name) for name in names_and_values}
    os.environ.update(names_and_values)
    try:
        yield
    finally:
        for name, old_value in previous.items():
            if old_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_value


def execute_development_smoke(
    *,
    protocol_path: Path,
    package_root: Path,
    api_key: str,
) -> Path:
    protocol = load_smoke_protocol(protocol_path)
    binding_errors = validate_smoke_protocol_bindings(
        protocol=protocol,
        package_root=package_root,
    )
    if binding_errors:
        raise ValueError(f"smoke protocol binding errors: {binding_errors}")
    if not api_key.strip():
        raise ValueError("provider API key is required")

    output_directory = package_root / protocol.output_directory
    if output_directory.exists():
        raise FileExistsError(
            f"refusing to overwrite smoke output: {output_directory}"
        )

    suite = load_development_suite(
        package_root / "DEVELOPMENT_TASK_SUITE_V1.json"
    )
    tasks_by_id = {task.task_id: task for task in suite.tasks}
    task = tasks_by_id[protocol.task_ids[0]]
    mini_config = yaml.safe_load(
        (Path(mini_package_dir) / "config/default.yaml").read_text()
    )
    agent_options = dict(mini_config["model"])
    agent_options["cost_tracking"] = "ignore_errors"
    provider_factory = SharedProviderFactory(
        spec=protocol.provider,
        agent_model_options=agent_options,
    )
    budget = BudgetLimits(**protocol.common_budget)

    def agent_factory(workspace: Path) -> MiniAgentAdapter:
        return MiniAgentAdapter(
            model=provider_factory.create_agent_model(),
            environment=SeatbeltEnvironment(
                cwd=workspace,
                forbidden_read_roots=(package_root.parents[1],),
                timeout=protocol.command_timeout_seconds,
            ),
            budget_limits=budget,
            agent_kwargs={
                **mini_config["agent"],
                "step_limit": budget.max_iterations or 0,
                "cost_limit": 0.0,
                "wall_time_limit_seconds": int(
                    budget.max_wall_time_seconds or 0
                ),
            },
        )

    def controller_factory() -> ActiveIterationController:
        return ActiveIterationController(
            model=provider_factory.create_controller_model()
        )

    with _provider_environment(api_key=api_key):
        paired = PairedDevelopmentRunner(
            agent_factory=agent_factory,
            controller_factory=controller_factory,
            random_seed=protocol.run_order_random_seed,
        ).run(task=task)

    post_run_binding_errors = validate_smoke_protocol_bindings(
        protocol=protocol,
        package_root=package_root,
    )
    output_directory.mkdir(parents=True)
    for arm, result in paired.arm_results.items():
        (output_directory / f"{task.task_id}.{arm.value}.traj.json").write_text(
            json.dumps(result.trajectory, indent=2, sort_keys=True)
        )

    execution = {
        "schema_version": "1.0",
        "protocol_sha256": _sha256(protocol_path),
        "runner_fingerprint": protocol.runner_fingerprint,
        "provider_fingerprint": protocol.provider_fingerprint,
        "task_id": paired.task_id,
        "run_order": [arm.value for arm in paired.run_order],
        "source_stable": not post_run_binding_errors,
        "post_run_binding_errors": list(post_run_binding_errors),
        "arms": {
            arm.value: _arm_payload(result)
            for arm, result in paired.arm_results.items()
        },
        "paired_improvement": paired.paired_improvement,
        "paired_regression": paired.paired_regression,
        "hypothesis_evidence_eligible": False,
        "production_execution_authorized": False,
    }
    execution_path = output_directory / "execution.json"
    execution_path.write_text(json.dumps(execution, indent=2, sort_keys=True))
    boundary_gate_passed = _boundary_gate_passed(execution)
    safety_gate_passed = _safety_gate_passed(execution)
    summary = {
        "schema_version": "1.0",
        "protocol_sha256": execution["protocol_sha256"],
        "execution_sha256": _sha256(execution_path),
        "task_count": 1,
        "ordinary_verified_success": int(
            bool(
                paired.arm_results[
                    ExperimentArm.ORDINARY
                ].evaluation.get("verified_task_success")
            )
        ),
        "active_verified_success": int(
            bool(
                paired.arm_results[ExperimentArm.ACTIVE].evaluation.get(
                    "verified_task_success"
                )
            )
        ),
        "paired_improvements": int(paired.paired_improvement),
        "paired_regressions": int(paired.paired_regression),
        "boundary_gate_passed": boundary_gate_passed,
        "safety_gate_passed": safety_gate_passed,
        "hypothesis_evidence_eligible": False,
        "production_execution_authorized": False,
    }
    (output_directory / "result_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True)
    )
    return output_directory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path("SMOKE_DEVELOPMENT_PROTOCOL_V1.json"),
    )
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()

    from dotenv import dotenv_values

    package_root = Path(__file__).parents[2]
    values = dotenv_values(args.env_file)
    raw_key = values.get("OPENPILOT_LLM_API_KEY")
    raw_model = values.get("OPENPILOT_LLM_MODEL")
    raw_base_url = values.get("OPENPILOT_LLM_BASE_URL")
    if not isinstance(raw_key, str):
        raise ValueError("OPENPILOT_LLM_API_KEY is missing")
    protocol = load_smoke_protocol(args.protocol)
    expected_model = protocol.provider.model_name.removeprefix("openai/")
    if raw_model != expected_model:
        raise ValueError("environment model does not match frozen protocol")
    if (raw_base_url or "").rstrip("/") != protocol.provider.base_url.rstrip("/"):
        raise ValueError("environment base URL does not match frozen protocol")
    output = execute_development_smoke(
        protocol_path=args.protocol,
        package_root=package_root,
        api_key=raw_key,
    )
    print(output)


if __name__ == "__main__":
    main()
