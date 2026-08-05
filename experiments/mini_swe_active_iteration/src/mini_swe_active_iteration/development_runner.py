"""Paired disposable-sandbox runner for exposed development tasks."""

from __future__ import annotations

import random
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .contracts import ExperimentArm, ExperimentResult
from .fixture import DevelopmentTaskFixture
from .runner import AgentAdapter, ExperimentRunner


@dataclass(frozen=True)
class PairedDevelopmentResult:
    task_id: str
    run_order: tuple[ExperimentArm, ...]
    arm_results: dict[ExperimentArm, ExperimentResult]
    paired_improvement: bool
    paired_regression: bool
    hypothesis_evidence_eligible: bool = False


class PairedDevelopmentRunner:
    """Run both arms in fresh sandboxes and evaluate without exposing arm labels."""

    def __init__(
        self,
        *,
        agent_factory: Callable[[Path], AgentAdapter],
        controller_factory: Callable[[], Any],
        random_seed: int,
    ) -> None:
        self._agent_factory = agent_factory
        self._controller_factory = controller_factory
        self._random_seed = random_seed

    def run(self, *, task: DevelopmentTaskFixture) -> PairedDevelopmentResult:
        run_order = [ExperimentArm.ORDINARY, ExperimentArm.ACTIVE]
        random.Random(self._random_seed).shuffle(run_order)
        results: dict[ExperimentArm, ExperimentResult] = {}

        for arm in run_order:
            workspace_path: Path | None = None
            with tempfile.TemporaryDirectory(
                prefix=f"mini-swe-{task.task_id}-"
            ) as temporary_directory:
                workspace_path = Path(temporary_directory).resolve()
                task.materialize_public(workspace_path)
                runner = ExperimentRunner(
                    agent_factory=lambda workspace=workspace_path: (
                        self._agent_factory(workspace)
                    ),
                    controller_factory=self._controller_factory,
                )
                result = runner.run(task=task.instruction, arm=arm)
                task.validate_agent_workspace(workspace_path)
                evaluation = task.evaluate_hidden(workspace=workspace_path)
                final_files = {
                    relative_path: (
                        (workspace_path / relative_path).read_text()
                        if (workspace_path / relative_path).is_file()
                        else None
                    )
                    for relative_path in task.public_files
                }
                result = replace(
                    result,
                    evaluation=evaluation,
                    final_files=final_files,
                )
            cleanup_succeeded = bool(
                workspace_path is not None and not workspace_path.exists()
            )
            results[arm] = replace(
                result,
                cleanup_succeeded=cleanup_succeeded,
            )

        ordinary_success = bool(
            results[ExperimentArm.ORDINARY].evaluation.get(
                "verified_task_success"
            )
        )
        active_success = bool(
            results[ExperimentArm.ACTIVE].evaluation.get(
                "verified_task_success"
            )
        )
        return PairedDevelopmentResult(
            task_id=task.task_id,
            run_order=tuple(run_order),
            arm_results=results,
            paired_improvement=active_success and not ordinary_success,
            paired_regression=ordinary_success and not active_success,
        )
