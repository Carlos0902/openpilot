"""Paired task-arm coordination over disposable SWE-bench environments."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Protocol

from .contracts import ExperimentArm, ExperimentResult
from .runner import AgentAdapter, ExperimentRunner


@dataclass(frozen=True)
class SWEbenchScreenTask:
    """The only task content that may reach a task arm is public instruction text."""

    instance_id: str
    instruction: str

    def __post_init__(self) -> None:
        if not self.instance_id.strip() or not self.instruction.strip():
            raise ValueError("SWE-bench screen task needs public identity and instruction")


class SWEbenchAgentEnvironment(Protocol):
    def collect_patch(self) -> str: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class PairedSWEbenchResult:
    task_id: str
    run_order: tuple[ExperimentArm, ExperimentArm]
    arm_results: dict[ExperimentArm, ExperimentResult]
    paired_improvement: bool
    paired_regression: bool
    hypothesis_evidence_eligible: bool = False


class PairedSWEbenchRunner:
    """Run a fixed pair order and keep the evaluator blind to the arm identity."""

    def __init__(
        self,
        *,
        environment_factory: Callable[[SWEbenchScreenTask], SWEbenchAgentEnvironment],
        agent_factory: Callable[[SWEbenchAgentEnvironment], AgentAdapter],
        controller_factory: Callable[[], Any],
        evaluate_patch: Callable[..., dict[str, Any]],
    ) -> None:
        self._environment_factory = environment_factory
        self._agent_factory = agent_factory
        self._controller_factory = controller_factory
        self._evaluate_patch = evaluate_patch

    def run(
        self,
        *,
        task: SWEbenchScreenTask,
        run_order: tuple[ExperimentArm, ExperimentArm],
    ) -> PairedSWEbenchResult:
        if set(run_order) != {ExperimentArm.ORDINARY, ExperimentArm.ACTIVE}:
            raise ValueError("each SWE-bench pair must run each arm exactly once")
        results: dict[ExperimentArm, ExperimentResult] = {}
        for arm in run_order:
            environment = self._environment_factory(task)
            result: ExperimentResult | None = None
            try:
                result = ExperimentRunner(
                    agent_factory=lambda: self._agent_factory(environment),
                    controller_factory=self._controller_factory,
                ).run(task=task.instruction, arm=arm)
                model_patch = environment.collect_patch()
                evaluation = self._evaluate_patch(
                    instance_id=task.instance_id,
                    model_patch=model_patch,
                )
                result = replace(result, evaluation=evaluation)
            except Exception as error:
                if result is None:
                    raise
                result = replace(
                    result,
                    evaluation={
                        "verified_task_success": False,
                        "evaluation_complete": False,
                        "failure_type": type(error).__name__,
                    },
                )
            finally:
                try:
                    environment.close()
                except Exception as error:
                    if result is None:
                        raise
                    result = replace(
                        result,
                        evaluation={
                            "verified_task_success": False,
                            "evaluation_complete": False,
                            "failure_type": type(error).__name__,
                        },
                        cleanup_succeeded=False,
                    )
                else:
                    if result is not None:
                        result = replace(result, cleanup_succeeded=True)
            assert result is not None
            results[arm] = result

        ordinary_success = bool(
            results[ExperimentArm.ORDINARY].evaluation.get("verified_task_success")
        )
        active_success = bool(
            results[ExperimentArm.ACTIVE].evaluation.get("verified_task_success")
        )
        return PairedSWEbenchResult(
            task_id=task.instance_id,
            run_order=run_order,
            arm_results=results,
            paired_improvement=active_success and not ordinary_success,
            paired_regression=ordinary_success and not active_success,
        )
