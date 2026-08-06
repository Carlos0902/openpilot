"""Autonomous Iteration pipeline composition."""

from __future__ import annotations

from typing import Any, Callable

from autonomous_iteration.agents.context_loader import ContextLoaderAgent
from autonomous_iteration.agents.goal_maker import GoalMakerAgent
from autonomous_iteration.agents.task_decomposer import TaskDecomposerAgent
from autonomous_iteration.agents.task_designer import TaskDesignerAgent
from autonomous_iteration.agents.task_executor import TaskExecutorAgent
from metadata import (
    DerivedContextProjection,
    ReasoningDecisionComplexity,
    SessionConstraintState,
    SessionIngressState,
)


class AutonomousIterationPipeline:
    """Compose the five instruction-defined autonomous iteration agents."""

    stage_names = [
        "Context Loader",
        "Project Diagnosis",
        "Goal Maker",
        "Task Designer",
        "Task Decomposer",
        "Task Executor",
    ]

    def __init__(
        self,
        *,
        context_loader: ContextLoaderAgent,
        goal_maker: GoalMakerAgent,
        task_designer: TaskDesignerAgent,
        task_decomposer: TaskDecomposerAgent,
    ) -> None:
        self.context_loader = context_loader
        self.goal_maker = goal_maker
        self.task_designer = task_designer
        self.task_decomposer = task_decomposer

    def load_context(
        self,
        goal: str,
        project_path: Any,
        iteration: int,
        *,
        session_constraints: SessionConstraintState | None = None,
        session_ingress_state: SessionIngressState | None = None,
    ) -> dict[str, Any]:
        return self.context_loader.run(
            goal,
            project_path,
            iteration,
            session_constraints=session_constraints,
            session_ingress_state=session_ingress_state,
        )

    def make_goals(
        self,
        project_state: Any,
        evaluation: Any,
        improvement_report: dict[str, Any],
        completed_iteration: int,
        *,
        session_constraints: SessionConstraintState | None = None,
        session_ingress_state: SessionIngressState | None = None,
        context_projection: DerivedContextProjection | None = None,
        reasoning_complexity: ReasoningDecisionComplexity | None = None,
    ) -> list[Any]:
        return self.goal_maker.run(
            project_state,
            evaluation,
            improvement_report,
            completed_iteration,
            session_constraints=session_constraints,
            session_ingress_state=session_ingress_state,
            context_projection=context_projection,
            reasoning_complexity=reasoning_complexity,
        )

    def design_tasks(
        self,
        project_state: Any,
        goal: Any,
        improvement_report: dict[str, Any],
        completed_iteration: int,
        *,
        session_constraints: SessionConstraintState | None = None,
        session_ingress_state: SessionIngressState | None = None,
        context_projection: DerivedContextProjection | None = None,
    ) -> list[Any]:
        return self.task_designer.run(
            project_state,
            goal,
            improvement_report,
            completed_iteration,
            session_constraints=session_constraints,
            session_ingress_state=session_ingress_state,
            context_projection=context_projection,
        )

    def decompose_tasks(self, tasks: list[Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.task_decomposer.run(tasks, context=context or {})

    def execute_task(
        self,
        executor: Callable[..., Any],
        iteration: int,
        evaluation: Any,
        actions: list[str],
        improvement_report: dict[str, Any],
        is_repair: bool,
    ) -> Any:
        return TaskExecutorAgent(executor).run(iteration, evaluation, actions, improvement_report, is_repair)
