"""Goal Maker agent facade."""

from __future__ import annotations

from typing import Any, Callable


class GoalMakerAgent:
    """Generate improvement goals from context."""

    def __init__(self, delegate: Callable[..., list[Any]]) -> None:
        self.delegate = delegate

    def run(
        self,
        project_state: Any,
        evaluation: Any,
        improvement_report: dict[str, Any],
        completed_iteration: int,
        *,
        session_constraints: Any | None = None,
        session_ingress_state: Any | None = None,
        context_projection: Any | None = None,
        reasoning_complexity: Any | None = None,
    ) -> list[Any]:
        if (
            session_constraints is None
            and session_ingress_state is None
            and context_projection is None
            and reasoning_complexity is None
        ):
            return self.delegate(project_state, evaluation, improvement_report, completed_iteration)
        return self.delegate(
            project_state,
            evaluation,
            improvement_report,
            completed_iteration,
            session_constraints=session_constraints,
            session_ingress_state=session_ingress_state,
            context_projection=context_projection,
            reasoning_complexity=reasoning_complexity,
        )
