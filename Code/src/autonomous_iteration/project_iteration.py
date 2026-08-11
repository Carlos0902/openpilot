"""Project iteration helpers for IntelligentAutopilot."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from metadata import (
    ProjectImprovementPolicy,
    ProjectImprovementPolicySource,
    ProjectImprovementRequirement,
)


class ProjectIterationHelper:
    """Small adapter for project iteration context and configuration."""

    def __init__(self, logger: Any | None = None, session_id_getter: Any | None = None) -> None:
        self.logger = logger
        self.session_id_getter = session_id_getter

    def readme_environment_context(self, environment_payload: dict[str, Any]) -> dict[str, Any]:
        if not environment_payload:
            return {}
        packages = environment_payload.get("detected_packages") or []
        result = {
            "virtual_environment": ".venv",
            "python_executable": environment_payload.get("python_executable"),
            "python_version": environment_payload.get("python_version"),
            "dependencies": ", ".join(packages) if packages else "No third-party Python packages detected",
        }
        stack_preset = environment_payload.get("stack_preset")
        if isinstance(stack_preset, dict):
            result["stack_preset"] = (
                f"r{stack_preset.get('revision', 1)} {stack_preset.get('architecture', 'project_native')}; "
                f"frontend={stack_preset.get('frontend_language', 'best_fit_for_surface')}; "
                f"backend={stack_preset.get('backend_language', 'python')}; "
                f"ui={stack_preset.get('ui_strategy', 'evaluate_user_facing_ui')}"
            )
        self._log("readme_environment_context", {"has_payload": True}, {"dependencies": result["dependencies"]})
        return result

    def project_environment_context(
        self,
        project_path: Path | None,
        environments: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if not project_path:
            return {}
        try:
            result = environments.get(str(project_path.resolve()), {})
        except OSError:
            result = environments.get(str(project_path), {})
        self._log(
            "project_environment_context",
            {"project_path": str(project_path)},
            {"found": bool(result)},
        )
        return result

    def resolve_project_improvement_iterations(self, autopilot: Any, goal: str, project_path: str | Path) -> bool:
        """Resolve optional per-project improvement count using the owning autopilot."""
        if (
            not autopilot.prompt_for_project_improvement_iterations
            or autopilot._project_improvement_iterations_prompted
        ):
            return autopilot.required_successful_improvements > 0

        autopilot._project_improvement_iterations_prompted = True

        try:
            from ui.question_ui import QuestionUI

            question_ui = QuestionUI(autopilot.console)
            live = getattr(autopilot.enhanced_ui, "live", None) if autopilot.enhanced_ui else None
            live_was_started = bool(live and getattr(live, "is_started", False))
            if live_was_started:
                live.stop()
            try:
                iterations = question_ui.ask_integer(
                    "improvement_iterations",
                    "这次项目生成后，要执行几轮真实代码改进迭代？",
                    title="Project Improvement Iterations",
                    description=(
                        f"项目路径: {Path(project_path).expanduser()}\n"
                        "0 表示只生成并验证项目；1-5 表示继续完成对应次数的可验证代码升级。"
                    ),
                    default=autopilot.required_successful_improvements,
                    min_value=0,
                    max_value=5,
                )
            finally:
                if live_was_started:
                    live.start(refresh=True)
        except Exception as exc:
            if autopilot.enhanced_ui:
                autopilot.enhanced_ui.log_activity(
                    "warning",
                    f"Could not ask improvement iterations; using {autopilot.required_successful_improvements}: {exc}",
                )
            self._log(
                "resolve_project_improvement_iterations",
                {"goal": goal, "project_path": str(project_path)},
                {"used_default": True, "iterations": autopilot.required_successful_improvements},
                success=False,
                error=str(exc),
            )
            return autopilot.required_successful_improvements > 0

        minimum_attempts = self.minimum_attempt_budget(iterations)
        attempts = max(int(getattr(autopilot, "max_iteration_attempts", 0) or 0), minimum_attempts)
        if iterations <= 0:
            policy = ProjectImprovementPolicy(
                requirement=ProjectImprovementRequirement.DISABLED,
                source=ProjectImprovementPolicySource.USER_SELECTED,
                required_accepted_transactions=0,
                max_accepted_transactions=0,
                max_attempts=0,
            )
        else:
            policy = ProjectImprovementPolicy(
                requirement=ProjectImprovementRequirement.REQUIRED,
                source=ProjectImprovementPolicySource.USER_SELECTED,
                required_accepted_transactions=iterations,
                max_accepted_transactions=iterations,
                max_attempts=attempts,
            )
        autopilot.project_improvement_policy = policy
        autopilot.required_successful_improvements = policy.max_accepted_transactions
        autopilot.enable_iterative_improvement = policy.enabled
        autopilot.max_iteration_attempts = policy.max_attempts
        autopilot.iterative_improvement.required_successful_improvements = policy.max_accepted_transactions
        autopilot.iterative_improvement.max_iteration_attempts = policy.max_attempts
        if autopilot.enhanced_ui:
            autopilot.enhanced_ui.log_activity(
                "info",
                f"Project improvement iterations set to {iterations}; attempt budget {autopilot.max_iteration_attempts}",
            )
            autopilot.enhanced_ui.set_current_task_state(
                title="Project Improvement Setup",
                details=f"Improvement iterations for this project: {iterations}",
                status="completed" if iterations else "skipped",
            )
        self._log(
            "resolve_project_improvement_iterations",
            {"goal": goal, "project_path": str(project_path)},
            {"iterations": iterations},
        )
        return iterations > 0

    @staticmethod
    def minimum_attempt_budget(required_successful_improvements: int) -> int:
        if required_successful_improvements <= 0:
            return 0
        repair_retry_buffer = max(2, math.ceil(required_successful_improvements * 0.5))
        return required_successful_improvements + repair_retry_buffer

    def _log(
        self,
        source_name: str,
        input_summary: Any,
        output_summary: Any,
        *,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        if not self.logger or not hasattr(self.logger, "log_structured_event"):
            return
        session_id = self.session_id_getter() if self.session_id_getter else "unknown"
        self.logger.log_structured_event(
            source_type="module",
            source_name=f"autonomous_iteration.project_iteration.{source_name}",
            phase="project_iteration",
            event_type="module_completed" if success else "module_failed",
            session_id=session_id or "unknown",
            turn_id=1,
            success=success,
            input_summary=input_summary,
            output_summary=output_summary,
            error=error,
        )
