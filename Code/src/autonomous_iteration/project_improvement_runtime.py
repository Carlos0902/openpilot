"""Project improvement runtime owned by the autonomous_iteration module."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from autonomous_iteration.models import EvaluationResult, IterationResult
from autonomous_iteration.enhancement_completion_budget import EnhancementCompletionBudgetCoordinator
from autonomous_iteration.task_models import Task, TaskPriority
from autonomous_iteration.tool.project_improvement_tool import project_state_reader_executor
from memory.context_projection import (
    build_derived_context_projection,
)
from metadata import (
    DerivedContextProjection,
    EnhancementCompletionRequirement,
    FailureMetadata,
    ResultStatus,
    RuntimeBudgetMetadata,
    ToolExecutionEnvelopeMetadata,
    ToolInputMetadata,
    SessionConstraintState,
    SessionIngressState,
)
from tools.environment_fix_tool import environment_fix_tool_executor, summarize_environment_failure


class ProjectImprovementRuntime:
    """Bridge IntelligentAutopilot runtime services into the iteration pipeline."""

    MAX_ENVIRONMENT_REPAIR_ATTEMPTS = 3

    STAGES = {
        "environment": "Environment Setup",
        "context_loader": "Context Loader",
        "goal_maker": "Goal Maker",
        "task_designer": "Task Designer",
        "task_decomposer": "Task Decomposer",
        "task_executor": "Task Executor",
        "modification_evaluator": "Modification Evaluator",
    }

    def __init__(self, autopilot: Any) -> None:
        self.autopilot = autopilot
        self._active_pipeline_task_id = ""

    def run(
        self,
        *,
        goal: str,
        project_path: str | Path,
        written_files: list[str],
        run_command: str = "",
        readme_path: str | Path | None = None,
        session_constraints: SessionConstraintState | None = None,
        session_ingress_state: SessionIngressState | None = None,
    ) -> dict[str, Any] | None:
        """Run the instruction-defined autonomous iteration pipeline."""
        if not self.autopilot.enable_iterative_improvement or not written_files:
            return None

        if not self.autopilot._resolve_project_improvement_iterations(goal, project_path):
            return None

        project_path = Path(project_path).expanduser()
        readme_path = Path(readme_path).expanduser() if readme_path else project_path / "README.md"
        self._active_pipeline_task_id = f"{self.autopilot.session_id or 'session'}:project_improvement_runtime:{uuid.uuid4().hex[:8]}"
        budget_resolver = getattr(self.autopilot, "_enhancement_runtime_budget", None)
        runtime_budget = budget_resolver() if callable(budget_resolver) else getattr(
            self.autopilot.iterative_improvement,
            "runtime_budget",
            RuntimeBudgetMetadata(),
        )
        self.autopilot.iterative_improvement.runtime_budget = runtime_budget
        self.autopilot.iterative_improvement.enhancement_budget = EnhancementCompletionBudgetCoordinator(
            runtime_budget
        )
        improvement_policy = getattr(self.autopilot, "project_improvement_policy", None)
        self.autopilot.iterative_improvement.enhancement_requirement = (
            EnhancementCompletionRequirement.REQUIRED
            if bool(
                getattr(improvement_policy, "controls_top_level_success", False)
            )
            else EnhancementCompletionRequirement.OPTIONAL
        )

        self._log("pipeline_start", {"goal": goal, "project_path": str(project_path)}, {"written_files": len(written_files)})
        self._emit_trajectory_event(
            "pipeline_started",
            input_summary={
                "goal": goal,
                "project_path": str(project_path),
                "written_files": written_files,
                "run_command": run_command,
                "readme_path": str(readme_path),
            },
        )
        self._prepare_dashboard(goal)

        environment_result = self._prepare_environment(
            project_path=project_path,
            written_files=written_files,
            run_command=run_command,
            goal=goal,
        )
        if not environment_result.success or environment_result.output is None:
            result = self._environment_failure_result(environment_result, run_command)
            self._log("environment_failed", {"project_path": str(project_path)}, {"reason": result["failure_reason"]}, success=False)
            self._emit_trajectory_event(
                "pipeline_environment_failed",
                input_summary={"project_path": str(project_path)},
                output_summary={
                    "failure_reason": result["failure_reason"],
                    "failed_tool": result["failed_tool"],
                    "failure_stage": result["failure_stage"],
                },
                success=False,
                error=result["failure_reason"],
            )
            return result

        environment_payload = environment_result.output
        run_command = str(environment_payload.get("run_command") or run_command)

        latest_context_projection: DerivedContextProjection | None = None

        def on_progress(event: str, payload: dict[str, Any]) -> None:
            nonlocal latest_context_projection
            if event == "context_loader" and session_ingress_state is not None:
                context_payload = payload.get("context") if isinstance(payload, dict) else None
                if isinstance(context_payload, dict):
                    latest_context_projection = build_derived_context_projection(
                        context_payload,
                        session_ingress_state,
                    )
            self.autopilot._handle_iteration_progress(event, payload)
            self._emit_trajectory_event(
                "pipeline_progress",
                step_id=f"iteration_{payload.get('iteration', '')}" if isinstance(payload, dict) else "",
                input_summary={"event": event},
                output_summary=payload,
            )

        def apply_improvement(
            iteration: int,
            evaluation: EvaluationResult,
            actions: list[str],
            improvement_report: dict[str, Any],
            is_repair: bool,
        ) -> IterationResult:
            return self.autopilot._apply_project_improvement(
                goal=goal,
                project_path=project_path,
                written_files=written_files,
                run_command=run_command,
                readme_path=readme_path,
                iteration=iteration,
                evaluation=evaluation,
                actions=actions,
                improvement_report=improvement_report,
                is_repair=is_repair,
            )

        def analyze_improvements(completed_iteration: int, evaluation: EvaluationResult) -> dict[str, Any]:
            return self.autopilot._analyze_project_improvements(
                goal=goal,
                project_path=project_path,
                written_files=written_files,
                run_command=run_command or evaluation.run_command,
                readme_path=readme_path,
                completed_iteration=completed_iteration,
                evaluation=evaluation,
                session_constraints=session_constraints,
                session_ingress_state=session_ingress_state,
                context_projection=latest_context_projection,
            )

        def read_project_state(evaluation: EvaluationResult, iteration: int) -> dict[str, Any]:
            return self._read_project_state(
                goal=goal,
                project_path=project_path,
                written_files=written_files,
                run_command=run_command or evaluation.run_command,
                readme_path=readme_path,
                evaluation=evaluation,
                iteration=iteration,
            )

        interrupted = False
        try:
            result = self.autopilot.iterative_improvement.run_project_pipeline(
                goal=goal,
                project_path=project_path,
                written_files=written_files,
                run_command=run_command,
                readme_path=readme_path,
                apply_improvement=apply_improvement,
                analyze_improvements=analyze_improvements,
                read_project_state=read_project_state,
                on_progress=on_progress,
                session_constraints=session_constraints,
                session_ingress_state=session_ingress_state,
            )
        except Exception as exc:
            interrupted = True
            error_type = type(exc).__name__
            failure_reason = str(exc) or error_type
            result = {
                "success": False,
                "status": "interrupted",
                "error_type": error_type,
                "failure_stage": "Project Improvement",
                "failed_tool": "project_improvement_runtime",
                "failure_reason": failure_reason,
                "partial_success": False,
                "completed_improvements": 0,
                "required_improvements": self.autopilot.required_successful_improvements,
                "completed_iterations": 0,
                "required_iterations": self.autopilot.required_successful_improvements,
            }
            self._emit_trajectory_event(
                "pipeline_failed",
                input_summary={"goal": goal, "project_path": str(project_path)},
                output_summary={
                    "status": result["status"],
                    "error_type": error_type,
                    "failure_stage": result["failure_stage"],
                    "failure_reason": failure_reason,
                },
                success=False,
                error=failure_reason,
            )

        if not interrupted:
            self._finalize_dashboard(result)
            self._log_project_improvement_result(goal, project_path, written_files, result)
        self._log(
            "pipeline_end",
            {"goal": goal, "project_path": str(project_path)},
            {
                "success": result.get("success"),
                "failure_stage": result.get("failure_stage"),
                "completed_improvements": result.get("completed_improvements"),
            },
            success=bool(result.get("success")),
            error=result.get("failure_reason"),
        )
        self._emit_trajectory_event(
            "pipeline_finished",
            input_summary={"goal": goal, "project_path": str(project_path)},
            output_summary={
                "success": bool(result.get("success")),
                "status": result.get("status"),
                "error_type": result.get("error_type"),
                "failure_stage": result.get("failure_stage"),
                "failed_tool": result.get("failed_tool"),
                "completed_improvements": result.get("completed_improvements"),
                "required_improvements": result.get("required_improvements"),
                "retry_attempted": result.get("retry_attempted"),
            },
            success=bool(result.get("success")),
            error=result.get("failure_reason"),
        )
        return result

    def _prepare_dashboard(self, goal: str) -> None:
        if self.autopilot.enhanced_ui:
            self.autopilot._reset_iteration_dashboard(goal)
            self.autopilot.enhanced_ui.set_current_task_state(
                title="Autonomous Iteration",
                details=f"Improvements applied: 0/{self.autopilot.required_successful_improvements}",
                status="running",
            )

    def _prepare_environment(
        self,
        *,
        project_path: Path,
        written_files: list[str],
        run_command: str,
        goal: str,
    ) -> ToolExecutionEnvelopeMetadata:
        environment_task = Task(
            id=str(uuid.uuid4()),
            description="Prepare project virtual environment",
            priority=TaskPriority.HIGH,
        )
        if self.autopilot.enhanced_ui:
            self.autopilot._ensure_dashboard_iteration(1)
        result = self.autopilot._sync_project_environment(
            task=environment_task,
            step_id="iteration_initial_project_environment_tool",
            project_path=project_path,
            written_files=written_files,
            entry_files=written_files,
            run_command=run_command,
            goal=goal,
            parent_task_id=self.autopilot._dashboard_stage_id("environment"),
        )
        self._log(
            "environment_prepared",
            {"project_path": str(project_path), "written_files": len(written_files)},
            {"success": result.success},
            success=result.success,
            error=result.error_message,
        )
        self._emit_trajectory_event(
            "environment_sync_completed",
            step_id="environment_setup",
            input_summary={"project_path": str(project_path), "written_files": written_files},
            output_summary={"success": result.success},
            success=result.success,
            error=result.error_message,
        )
        if result.success:
            return result

        repair_results: list[ToolExecutionEnvelopeMetadata] = []
        for repair_attempt in range(1, self.MAX_ENVIRONMENT_REPAIR_ATTEMPTS + 1):
            repair_result = self._attempt_environment_repair(
                task=environment_task,
                project_path=project_path,
                environment_result=result,
                repair_attempt=repair_attempt,
                parent_task_id=self.autopilot._dashboard_stage_id("environment"),
            )
            if repair_result is not None:
                repair_results.append(repair_result)
            repair_payload = repair_result.output if repair_result else None
            if not repair_result or not repair_result.success or not getattr(repair_payload, "applied", False):
                break
            result = self.autopilot._sync_project_environment(
                task=environment_task,
                step_id=f"iteration_repaired_project_environment_tool_{repair_attempt}",
                project_path=project_path,
                written_files=written_files,
                entry_files=written_files,
                run_command=run_command,
                goal=goal,
                parent_task_id=self.autopilot._dashboard_stage_id("environment"),
            )
            self._log(
                "environment_retried_after_repair",
                {
                    "project_path": str(project_path),
                    "written_files": len(written_files),
                    "repair_attempt": repair_attempt,
                },
                {"success": result.success},
                success=result.success,
                error=result.error_message,
            )
            self._emit_trajectory_event(
                "environment_sync_retried",
                step_id=f"environment_repair_{repair_attempt}",
                input_summary={"repair_attempt": repair_attempt, "project_path": str(project_path)},
                output_summary={"success": result.success},
                success=result.success,
                error=result.error_message,
            )
            if result.success:
                break
        return self._with_environment_repair_details(result, repair_results)

    def _attempt_environment_repair(
        self,
        *,
        task: Task,
        project_path: Path,
        environment_result: ToolExecutionEnvelopeMetadata,
        repair_attempt: int,
        parent_task_id: str | None,
    ) -> ToolExecutionEnvelopeMetadata | None:
        error_message = environment_result.error_message or "Project environment sync failed."
        input_metadata = ToolInputMetadata.from_mapping(
            "environment_fix_tool",
            {
                "project_path": str(project_path),
                "stderr": error_message,
                "context": error_message,
                "_memory_store": getattr(self.autopilot, "memory_store", None),
            },
        )
        execute_fix = getattr(self.autopilot, "_execute_environment_fix_agent_tool", None)
        if callable(execute_fix):
            repair_result = execute_fix(
                task=task,
                step_id=f"iteration_environment_fix_tool_{repair_attempt}",
                input_metadata=input_metadata,
                parent_task_id=parent_task_id,
            )
        else:
            output_metadata = environment_fix_tool_executor(input_metadata)
            repair_success = output_metadata.status == ResultStatus.SUCCESS
            repair_result = ToolExecutionEnvelopeMetadata(
                tool_name="environment_fix_tool",
                step_id=f"iteration_environment_fix_tool_{repair_attempt}",
                status=output_metadata.status,
                success=repair_success,
                input_metadata=input_metadata,
                output_metadata=output_metadata,
                failure=None if repair_success else output_metadata.failure,
            )
        self._log(
            "environment_repair_attempted",
            {"project_path": str(project_path)},
            {
                "success": repair_result.success,
                "output": repair_result.output.to_json_dict() if repair_result.output else None,
            },
            success=repair_result.success,
            error=repair_result.error_message,
        )
        self._emit_trajectory_event(
            "environment_repair_attempted",
            step_id=f"environment_repair_{repair_attempt}",
            input_summary={"project_path": str(project_path), "repair_attempt": repair_attempt},
            output_summary={
                "success": repair_result.success,
                "repair_output": repair_result.output.to_json_dict() if repair_result.output else None,
            },
            success=repair_result.success,
            error=repair_result.error_message,
        )
        return repair_result

    def _with_environment_repair_details(
        self,
        result: ToolExecutionEnvelopeMetadata,
        repair_results: list[ToolExecutionEnvelopeMetadata],
    ) -> ToolExecutionEnvelopeMetadata:
        if not repair_results:
            return result
        repair_outputs = [repair.output.to_json_dict() if repair.output else None for repair in repair_results]
        retry_history = list(result.retry_history)
        retry_history.extend({"environment_repair": output, "success": result.success} for output in repair_outputs)
        if result.success:
            return result.model_copy(update={"retry_history": retry_history})
        failure = result.failure or FailureMetadata(
            error_type="EnvironmentSetupFailed",
            error_message=result.error_message or "Project environment sync failed.",
        )
        details = dict(failure.details or {})
        details["environment_repairs"] = repair_outputs
        details["environment_repair_attempts"] = len(repair_outputs)
        details["root_cause"] = summarize_environment_failure(result.error_message or "")
        last_repair_output = repair_outputs[-1]
        if isinstance(last_repair_output, dict):
            environment_failure = last_repair_output.get("environment_failure") or {}
            if isinstance(environment_failure, dict):
                details["affected_file"] = environment_failure.get("affected_file")
                details["pip_notices"] = environment_failure.get("pip_notices")
        return result.model_copy(
            update={
                "failure": failure.model_copy(update={"details": details}),
                "retry_history": retry_history,
            }
        )

    def _read_project_state(
        self,
        *,
        goal: str,
        project_path: Path,
        written_files: list[str],
        run_command: str,
        readme_path: Path,
        evaluation: EvaluationResult,
        iteration: int,
    ) -> dict[str, Any]:
        if self.autopilot.enhanced_ui:
            self.autopilot._ensure_dashboard_iteration()
        task = Task(
            id=str(uuid.uuid4()),
            description="Read project state for autonomous iteration",
            priority=TaskPriority.MEDIUM,
        )
        params = {
            "project_path": str(project_path),
            "goal": goal,
            "written_files": written_files,
            "run_command": run_command,
            "readme_path": str(readme_path),
            "memory_query": f"{goal} autonomous iteration {iteration}",
            "validation_context": evaluation.model_dump(),
        }
        execute_reader = getattr(self.autopilot, "_execute_project_state_reader_agent_tool", None)
        if execute_reader is None:
            fallback = project_state_reader_executor(
                ToolInputMetadata.from_mapping("project_state_reader", {**params, "_memory_store": self.autopilot.memory_store})
            )
            payload = fallback.result
            self._log(
                "project_state_read",
                {"iteration": iteration},
                {"source": "direct", "success": payload is not None},
                success=payload is not None,
            )
            self._emit_trajectory_event(
                "project_state_read",
                step_id=f"iteration_{iteration}_project_state",
                input_summary={"project_path": str(project_path), "iteration": iteration, "source": "direct"},
                output_summary={"success": payload is not None},
                success=payload is not None,
            )
            return payload.to_json_dict() if payload else {}

        result = execute_reader(
            task=task,
            step_id=f"iteration_{iteration}_project_state_reader",
            input_metadata=ToolInputMetadata.from_mapping("project_state_reader", params),
            parent_task_id=self.autopilot._dashboard_stage_id("project_state"),
        )
        if result.success and result.output is not None:
            self._log("project_state_read", {"iteration": iteration}, {"source": "tool", "success": True})
            self._emit_trajectory_event(
                "project_state_read",
                step_id=f"iteration_{iteration}_project_state",
                input_summary={"project_path": str(project_path), "iteration": iteration, "source": "tool"},
                output_summary={"success": True},
                success=True,
            )
            return result.output.to_json_dict()

        fallback = project_state_reader_executor(
            ToolInputMetadata.from_mapping("project_state_reader", {**params, "_memory_store": self.autopilot.memory_store})
        )
        payload = fallback.result
        self._log(
            "project_state_read",
            {"iteration": iteration},
            {"source": "fallback", "success": payload is not None},
            success=payload is not None,
            error=result.error_message,
        )
        self._emit_trajectory_event(
            "project_state_read",
            step_id=f"iteration_{iteration}_project_state",
            input_summary={"project_path": str(project_path), "iteration": iteration, "source": "fallback"},
            output_summary={"success": payload is not None},
            success=payload is not None,
            error=result.error_message,
        )
        return payload.to_json_dict() if payload else {}

    def _environment_failure_result(self, environment_result: ToolExecutionEnvelopeMetadata, run_command: str) -> dict[str, Any]:
        reason = environment_result.error_message or "Project environment sync failed."
        repair_details = environment_result.failure.details if environment_result.failure else {}
        root_cause = repair_details.get("root_cause") if isinstance(repair_details, dict) else None
        display_reason = str(root_cause or reason)
        evaluation = EvaluationResult(
            validation_passed=False,
            runnable=False,
            has_blocking_bugs=True,
            summary="Project environment setup failed.",
            validation_errors=[display_reason],
            warnings=[],
            run_command=run_command,
            recommended_actions=["Fix the project environment setup failure."],
            next_iteration_goal=f"Fix project environment setup: {display_reason}",
        )
        required = self.autopilot.required_successful_improvements
        return {
            "success": False,
            "partial_success": False,
            "completed_improvements": 0,
            "required_improvements": required,
            "completed_iterations": 0,
            "required_iterations": required,
            "attempts_used": 0,
            "max_iteration_attempts": self.autopilot.max_iteration_attempts,
            "validation": evaluation,
            "evaluation": evaluation,
            "evaluations": [evaluation],
            "iterations": [],
            "improvement_report": {},
            "project_state": None,
            "project_states": [],
            "iteration_goals": [],
            "designed_tasks": [],
            "mind_notes": [],
            "autonomous_iteration": None,
            "failure_stage": self.STAGES["environment"],
            "failed_iteration": 0,
            "failed_tool": "project_environment_tool",
            "failure_reason": display_reason,
            "environment_repair": repair_details.get("environment_repair") if isinstance(repair_details, dict) else None,
            "retry_attempted": False,
            "retry_history": [],
            "last_successful_iteration": 0,
            "remaining_goals": [],
        }

    def _finalize_dashboard(self, result: dict[str, Any]) -> None:
        if not self.autopilot.enhanced_ui:
            return
        evaluation = result["validation"]
        validation_passed = bool(evaluation.validation_passed)
        failure_stage = result.get("failure_stage")
        if not result["success"] and failure_stage == self.STAGES["task_executor"]:
            evaluation_status = "pending"
        elif not result["success"] and failure_stage == self.STAGES["modification_evaluator"]:
            evaluation_status = "failed"
        else:
            evaluation_status = "completed" if validation_passed else "failed"
        result_status = "completed" if result["success"] else ("warning" if result.get("partial_success") else "failed")
        self.autopilot._set_dashboard_task_status(self.autopilot._dashboard_stage_id("evaluation"), evaluation_status)
        if not result["success"]:
            self.autopilot._finish_active_operations(self.autopilot._format_iteration_failure(result))
            details = (
                f"Iteration: {result.get('failed_iteration') or 'unknown'}\n"
                f"Stage: {result.get('failure_stage') or 'unknown'}\n"
                f"Tool: {result.get('failed_tool') or 'unknown'}\n"
                f"Reason: {result.get('failure_reason') or 'No failure reason reported'}\n"
                f"Retry attempted: {'yes' if result.get('retry_attempted') else 'no'}\n"
                f"Improvements applied: {result['completed_improvements']}/{result['required_improvements']}\n"
                f"Validation passed: {evaluation.validation_passed}\n"
                f"Blocking issues: {len(evaluation.validation_errors)}"
            )
        else:
            details = (
                f"Improvements applied: {result['completed_improvements']}/{result['required_improvements']}\n"
                f"Validation passed: {evaluation.validation_passed}\n"
                f"Blocking issues: {len(evaluation.validation_errors)}"
            )
        self.autopilot.enhanced_ui.set_current_task_state(
            title="Autonomous Iteration complete" if result["success"] else "Autonomous Iteration stopped",
            details=details,
            status=result_status,
        )

    def _log_project_improvement_result(
        self,
        goal: str,
        project_path: Path,
        written_files: list[str],
        result: dict[str, Any],
    ) -> None:
        evaluation = result["validation"]
        self.autopilot.logger.log_event(
            "project_iterative_improvement",
            {
                "goal": goal,
                "project_path": str(project_path),
                "written_files": written_files,
                "validation": evaluation.model_dump(),
                "completed_improvements": result["completed_improvements"],
                "required_improvements": result["required_improvements"],
                "completed_iterations": result["completed_iterations"],
                "required_iterations": result["required_iterations"],
                "improvement_report": result.get("improvement_report", {}),
                "iterations": [
                    item.model_dump() if hasattr(item, "model_dump") else item
                    for item in result["iterations"]
                ],
                "partial_success": result.get("partial_success", False),
                "failure_stage": result.get("failure_stage"),
                "failed_iteration": result.get("failed_iteration"),
                "failed_tool": result.get("failed_tool"),
                "failure_reason": result.get("failure_reason"),
                "retry_attempted": result.get("retry_attempted", False),
                "retry_history": result.get("retry_history", []),
                "last_successful_iteration": result.get("last_successful_iteration"),
                "remaining_goals": result.get("remaining_goals", []),
            },
            session_id=self.autopilot.session_id or "unknown",
            turn_id=1,
        )

    def _log(
        self,
        source_name: str,
        input_summary: Any,
        output_summary: Any,
        *,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        logger = getattr(self.autopilot, "logger", None)
        if not logger or not hasattr(logger, "log_structured_event"):
            return
        logger.log_structured_event(
            source_type="module",
            source_name=f"autonomous_iteration.project_improvement_runtime.{source_name}",
            phase="project_improvement_runtime",
            event_type="module_completed" if success else "module_failed",
            session_id=self.autopilot.session_id or "unknown",
            turn_id=1,
            success=success,
            input_summary=input_summary,
            output_summary=output_summary,
            error=error,
            annotations={"stages": self.STAGES},
        )

    def _emit_trajectory_event(
        self,
        event_type: str,
        *,
        step_id: str = "",
        input_summary: Any | None = None,
        output_summary: Any | None = None,
        success: bool | None = None,
        error: str | None = None,
    ) -> None:
        hooks = getattr(self.autopilot, "runtime_diagnostics_hooks", None)
        if not hooks:
            return
        hooks.on_log_event(
            task_id=self._active_pipeline_task_id,
            session_id=str(self.autopilot.session_id or ""),
            step_id=step_id,
            source_type="module",
            source_name="autonomous_iteration.project_improvement_runtime",
            phase="project_improvement_runtime",
            event_type=event_type,
            success=success,
            input_summary=input_summary,
            output_summary=output_summary,
            error=error,
            annotations={"module": "project_improvement_runtime"},
        )
