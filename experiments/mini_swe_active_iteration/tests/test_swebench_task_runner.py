from __future__ import annotations

from dataclasses import dataclass

from mini_swe_active_iteration.contracts import ExperimentArm, Usage
from mini_swe_active_iteration.swebench_task_runner import (
    PairedSWEbenchRunner,
    SWEbenchScreenTask,
)


@dataclass
class _AgentRun:
    messages: tuple[dict, ...] = ()
    commands: tuple[str, ...] = ()
    usage: Usage = Usage(provider_calls=1)
    submission: str = "agent final"
    exit_status: str = "Submitted"
    trajectory: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.trajectory is None:
            self.trajectory = {"trajectory_format": "mini-swe-agent-1.1"}


class _Agent:
    def run(self, *, task: str, controller: object | None = None) -> _AgentRun:
        assert task == "Public task statement"
        return _AgentRun()


class _Environment:
    def __init__(self) -> None:
        self.closed = False

    def collect_patch(self) -> str:
        return "diff --git a/a.py b/a.py\n"

    def close(self) -> None:
        self.closed = True


class _Controller:
    usage = Usage()


def test_paired_runner_uses_frozen_order_and_hides_arm_from_evaluator() -> None:
    environments: list[_Environment] = []
    evaluator_inputs: list[dict[str, str]] = []

    def create_environment(_task: SWEbenchScreenTask) -> _Environment:
        environment = _Environment()
        environments.append(environment)
        return environment

    def evaluate(*, instance_id: str, model_patch: str) -> dict[str, bool]:
        evaluator_inputs.append({"instance_id": instance_id, "model_patch": model_patch})
        return {"verified_task_success": True}

    runner = PairedSWEbenchRunner(
        environment_factory=create_environment,
        agent_factory=lambda _environment: _Agent(),
        controller_factory=_Controller,
        evaluate_patch=evaluate,
    )
    task = SWEbenchScreenTask(
        instance_id="example__issue-1",
        instruction="Public task statement",
    )

    result = runner.run(
        task=task,
        run_order=(ExperimentArm.ACTIVE, ExperimentArm.ORDINARY),
    )

    assert result.run_order == (ExperimentArm.ACTIVE, ExperimentArm.ORDINARY)
    assert tuple(result.arm_results) == (ExperimentArm.ACTIVE, ExperimentArm.ORDINARY)
    assert all(environment.closed for environment in environments)
    assert evaluator_inputs == [
        {"instance_id": "example__issue-1", "model_patch": "diff --git a/a.py b/a.py\n"},
        {"instance_id": "example__issue-1", "model_patch": "diff --git a/a.py b/a.py\n"},
    ]
    assert result.paired_improvement is False
    assert result.paired_regression is False
