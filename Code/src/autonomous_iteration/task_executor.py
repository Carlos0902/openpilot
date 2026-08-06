"""Task Executor implementation for the Autonomous Iteration module."""

from __future__ import annotations

import ast
import json
import re
import uuid
from html import escape
from pathlib import Path
from typing import Any

from autonomous_iteration.models import EvaluationResult, IterationResult
from autonomous_iteration.task_models import Task, TaskPriority
from core.llm import LLMMessage
from memory.agents.git_manager_agent import GitManagerAgent, GitManagerError
from memory.context_assembly import build_context_llm_request
from metadata import (
    ContextCandidateTruncation,
    ContextRequestPurpose,
    EnhancementCompletionRequirement,
    FailureMetadata,
    GitSnapshotMetadata,
    ResultStatus,
    TaskFileResolutionMetadata,
    TaskFileResolutionRequestMetadata,
    ToolExecutionEnvelopeMetadata,
    ToolInputMetadata,
)
from tools.environment_fix_tool import environment_fix_tool_executor, summarize_environment_failure


class AutonomousTaskExecutor:
    """Execute instruction-defined autonomous iteration subtasks."""

    MAX_ENVIRONMENT_REPAIR_ATTEMPTS = 3

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def execute_improvement(
        self,
        *,
        goal: str,
        project_path: Path,
        written_files: list[str],
        run_command: str,
        readme_path: Path,
        iteration: int,
        evaluation: EvaluationResult,
        actions: list[str],
        improvement_report: dict[str, Any] | None = None,
        is_repair: bool = False,
    ) -> IterationResult:
        """Run the Task Executor stage for one project improvement."""
        self._log_agent(
            "task_executor_started",
            {"iteration": iteration, "actions": actions[:3]},
            {"project_path": str(project_path)},
        )
        task = Task(
            id=str(uuid.uuid4()),
            description=f"Improve project iteration {iteration}",
            priority=TaskPriority.HIGH,
        )
        if self.runtime.enhanced_ui:
            self.runtime._set_dashboard_task_status(self.runtime._dashboard_stage_id("execution"), "running")
            self.runtime._set_dashboard_task_status(getattr(self.runtime, "_dashboard_current_iteration_id", None), "running")
            self.runtime.enhanced_ui.set_current_task_state(
                title=f"{'Repair' if is_repair else 'Improvement'} {iteration}",
                details="\n".join(actions),
                status="running",
            )

        improvement_report = improvement_report or {}
        resolution = self._resolve_task_files(
            task=task,
            iteration=iteration,
            goal=goal,
            project_path=project_path,
            written_files=written_files,
            actions=actions,
            improvement_report=improvement_report,
            evaluation=evaluation,
        )
        if isinstance(resolution, str):
            return self._failure(iteration, actions, resolution, "task_file_resolver")
        if resolution.primary_file is None:
            return self._failure(iteration, actions, "Task file resolver did not select a primary file.", "task_file_resolver")

        target_file = Path(resolution.primary_file.file_path).expanduser()
        edit_kind = resolution.recommended_edit_kind
        readme_update_requested = self._designed_task_targets_file(
            improvement_report,
            project_path=project_path,
            target_file=readme_path,
        )
        authorized_write_files = [str(target_file)]
        if readme_update_requested:
            authorized_write_files.append(str(readme_path))
        task = task.model_copy(
            update={
                "kind": "repair" if is_repair else "implement",
                "read_files": [str(target_file)],
                "write_files": authorized_write_files,
                "validation_command": run_command or evaluation.run_command or "",
            }
        )
        try:
            current_content = target_file.read_text(encoding="utf-8")
        except OSError as exc:
            if resolution.primary_file.relation_source == "planned_target" and not target_file.exists():
                current_content = ""
            else:
                reason = f"Failed to read {target_file}: {exc}"
                return self._failure(iteration, actions, reason, "file_reader")

        if is_repair and self._should_use_bug_fix(evaluation):
            return self._execute_bug_fix(
                task=task,
                iteration=iteration,
                target_file=target_file,
                run_command=run_command or evaluation.run_command,
                evaluation=evaluation,
                actions=actions,
            )

        if not self._uses_python_code_pipeline(target_file, edit_kind):
            return self._execute_text_file_improvement(
                task=task,
                iteration=iteration,
                goal=goal,
                project_path=project_path,
                target_file=target_file,
                current_text=current_content,
                edit_kind=edit_kind,
                evaluation=evaluation,
                actions=actions,
                improvement_report=improvement_report,
            )

        code_result, retry_history = self.run_code_generation_retry_pipeline(
            task=task,
            iteration=iteration,
            goal=goal,
            target_file=target_file,
            current_code=current_content,
            evaluation=evaluation,
            actions=actions,
            improvement_report=improvement_report,
            is_repair=is_repair,
        )
        retry_attempted = len(retry_history) > 1
        if not code_result.success or code_result.output is None:
            failure_reason = self.visible_tool_failure_summary(
                tool="code_generator",
                tool_result=code_result,
                retry_attempted=retry_attempted,
            )
            self.log_iteration_failure(
                iteration=iteration,
                stage="Task Executor",
                tool="code_generator",
                target_file=target_file,
                actions=actions,
                error=failure_reason,
                prompt_length=retry_history[0].get("prompt_length", 0) if retry_history else 0,
                current_code_length=len(current_content),
                retry_attempted=retry_attempted,
                tool_result=code_result,
                retry_history=retry_history,
            )
            return self._failure(
                iteration,
                actions,
                failure_reason,
                "code_generator",
                retry_attempted=retry_attempted,
                retry_history=retry_history,
            )

        improved_code = str(code_result.output.get("code", ""))
        if improved_code.strip() == current_content.strip():
            reason = "Generated improvement did not change the target file."
            return self._failure(
                iteration,
                actions,
                reason,
                "code_generator",
                retry_attempted=retry_attempted,
                retry_history=retry_history,
            )
        if code_result.tool_name != "code_editor":
            try:
                ast.parse(improved_code)
            except SyntaxError as exc:
                reason = f"Generated improvement has syntax error on line {exc.lineno}: {exc.msg}"
                return self._failure(
                    iteration,
                    actions,
                    reason,
                    "code_generator",
                    retry_attempted=retry_attempted,
                    retry_history=retry_history,
                )

        pre_write_snapshot = self._create_git_snapshot(
            project_path=project_path,
            iteration=iteration,
            reason="source_file_write",
            target_files=[str(target_file)],
            stage_key="execution",
        )
        output_attrs = getattr(code_result.output, "attributes", {}) if code_result.output is not None else {}
        use_patch_writer = code_result.tool_name == "code_editor" or output_attrs.get("operation_kind") == "modify_symbol"
        writer_tool = "file_patch_writer" if use_patch_writer else "file_writer"
        writer_payload = {
            "file_path": str(target_file),
            "encoding": "utf-8",
            "create_dirs": True,
            "overwrite": True,
        }
        if use_patch_writer:
            writer_payload.update(
                {
                    "operation_kind": "modify_symbol",
                    "replacement_text": improved_code,
                    "symbol_name": output_attrs.get("symbol_name"),
                    "symbol_type": output_attrs.get("symbol_type"),
                    "line_start": output_attrs.get("line_start"),
                    "line_end": output_attrs.get("line_end"),
                    "patch": output_attrs.get("patch") if isinstance(output_attrs.get("patch"), dict) else None,
                }
            )
        else:
            writer_payload.update({"content": improved_code, "operation_kind": "file_replace"})
        write_result = self.runtime._execute_fast_tool(
            task=task,
            step_id=f"iteration_{iteration}_{writer_tool}",
            tool_name=writer_tool,
            input_metadata=ToolInputMetadata.from_mapping(writer_tool, writer_payload),
            parent_task_id=self.runtime._dashboard_stage_id("execution"),
        )
        self._log_agent(
            "file_writer_completed",
            {"iteration": iteration, "target_file": str(target_file)},
            {"success": write_result.success},
            success=write_result.success,
            error=write_result.error_message,
        )
        if not write_result.success:
            reason = write_result.error_message or "Failed to write improved code."
            return self._failure(
                iteration,
                actions,
                reason,
                "file_writer",
                retry_attempted=retry_attempted,
                retry_history=retry_history,
            )

        environment_result = self.runtime._sync_project_environment(
            task=task,
            step_id=f"iteration_{iteration}_project_environment_tool",
            project_path=project_path,
            written_files=[str(target_file)],
            entry_files=[str(target_file)],
            run_command=run_command,
            goal=goal,
            stack_preset_update=(
                improvement_report.get("stack_preset_update")
                if isinstance(improvement_report.get("stack_preset_update"), dict)
                else None
            ),
            parent_task_id=self.runtime._dashboard_stage_id("environment"),
        )
        self._log_agent(
            "environment_sync_completed",
            {"iteration": iteration, "project_path": str(project_path)},
            {"success": environment_result.success},
            success=environment_result.success,
            error=environment_result.error_message,
        )
        if not environment_result.success:
            repair_results: list[ToolExecutionEnvelopeMetadata] = []
            for repair_attempt in range(1, self.MAX_ENVIRONMENT_REPAIR_ATTEMPTS + 1):
                repair_result = self._attempt_environment_repair(
                    task=task,
                    project_path=project_path,
                    environment_result=environment_result,
                    repair_attempt=repair_attempt,
                )
                if repair_result is not None:
                    repair_results.append(repair_result)
                repair_payload = repair_result.output if repair_result else None
                if not repair_result or not repair_result.success or not getattr(repair_payload, "applied", False):
                    break
                environment_result = self.runtime._sync_project_environment(
                    task=task,
                    step_id=f"iteration_{iteration}_repaired_project_environment_tool_{repair_attempt}",
                    project_path=project_path,
                    written_files=[str(target_file)],
                    entry_files=[str(target_file)],
                    run_command=run_command,
                    goal=goal,
                    parent_task_id=self.runtime._dashboard_stage_id("environment"),
                )
                self._log_agent(
                    "environment_sync_retried_after_repair",
                    {
                        "iteration": iteration,
                        "project_path": str(project_path),
                        "repair_attempt": repair_attempt,
                    },
                    {"success": environment_result.success},
                    success=environment_result.success,
                    error=environment_result.error_message,
                )
                if environment_result.success:
                    break
            environment_result = self._with_environment_repair_details(environment_result, repair_results)
        if not environment_result.success or environment_result.output is None:
            reason = environment_result.error_message or "Project environment sync failed."
            if environment_result.failure and isinstance(environment_result.failure.details, dict):
                reason = str(environment_result.failure.details.get("root_cause") or reason)
            return IterationResult(
                iteration=iteration,
                validation_passed=False,
                completed_successful_iteration=False,
                applied_actions=actions,
                changed_files=[str(target_file)],
                success=False,
                error=reason,
                failure_stage="Environment Setup",
                failed_tool="project_environment_tool",
                failure_reason=reason,
                retry_attempted=retry_attempted,
                retry_history=retry_history,
            )
        environment_payload = environment_result.output
        run_command = str(environment_payload.get("run_command") or run_command)
        task = task.model_copy(update={"validation_command": run_command})

        review_prompt_context = self.runtime._build_prompt_context(
            original_goal=goal,
            project_path=project_path,
            written_files=[str(target_file)],
            run_command=run_command,
            evaluation=evaluation,
            iteration_goal=str(improvement_report.get("next_iteration_goal") or (actions[0] if actions else "")),
            acceptance_criteria=improvement_report.get("must_implement_next") or actions[:2],
            tool_task="Review generated code against safety, correctness, and product-fit rubric.",
            agent_instruction="Code Reviewer context: reject changes that pass syntax but fail the product-fit rubric.",
            target_file=target_file,
            current_code=improved_code,
            code_context=self.budget_code_context(improved_code, max_chars=3000),
            mode="review",
        )
        review_prompt_context["git_safety_context"] = self._git_safety_context(
            project_path=project_path,
            snapshot=pre_write_snapshot,
            target_files=[str(target_file)],
        )
        review_result = self.runtime._execute_fast_tool(
            task=task,
            step_id=f"iteration_{iteration}_code_reviewer",
            tool_name="code_reviewer",
            input_metadata=ToolInputMetadata.from_mapping("code_reviewer", {
                "code": improved_code,
                "language": "python",
                "prompt_context": review_prompt_context,
            }),
            parent_task_id=self.runtime._dashboard_stage_id("execution"),
        )
        self._log_agent(
            "code_review_completed",
            {"iteration": iteration},
            {"success": review_result.success},
            success=review_result.success,
            error=review_result.error_message,
        )
        readme_result = None
        if readme_update_requested:
            readme_result = self.runtime._execute_fast_tool(
                task=task,
                step_id=f"iteration_{iteration}_readme_tool",
                tool_name="readme_tool",
                input_metadata=ToolInputMetadata.from_mapping("readme_tool", {
                    "project_path": str(project_path),
                    "project_summary": f"{goal}\n\nRecent Improvements:\n- " + "\n- ".join(
                        (getattr(self.runtime, "_project_improvement_actions", []) or []) + actions
                    ),
                    "written_files": written_files,
                    "entry_files": [str(target_file)],
                    "run_command": run_command,
                    "setup_commands": environment_payload.get("setup_commands") or [],
                    "environment": self.runtime._readme_environment_context(environment_payload),
                    "overwrite": True,
                }),
                parent_task_id=self.runtime._dashboard_stage_id("execution"),
            )
            self._log_agent(
                "readme_update_completed",
                {"iteration": iteration},
                {"success": readme_result.success},
                success=readme_result.success,
                error=readme_result.error_message,
            )
        else:
            self._log_agent(
                "readme_update_skipped",
                {"iteration": iteration},
                {"reason": "not_in_designed_task_targets"},
            )

        review_payload = review_result.output or {}
        review_approved = bool(review_payload.get("approved", True))
        product_intent_retry_history: list[dict[str, Any]] = []
        if review_result.success and not review_approved and self._is_product_intent_rejection(review_payload):
            retry_result, product_intent_retry_history = self._run_product_intent_retry(
                task=task,
                iteration=iteration,
                goal=goal,
                target_file=target_file,
                rejected_code=improved_code,
                evaluation=evaluation,
                actions=actions,
                improvement_report=improvement_report,
                review_payload=review_payload,
                is_repair=is_repair,
            )
            retry_attempted = True
            retry_history.extend(product_intent_retry_history)
            if retry_result.get("success"):
                improved_code = str(retry_result["code"])
                review_result = retry_result["review_result"]
                write_result = retry_result["write_result"]
                review_payload = review_result.output or {}
                review_approved = bool(review_payload.get("approved", True))
                if readme_update_requested:
                    readme_result = self.runtime._execute_fast_tool(
                        task=task,
                        step_id=f"iteration_{iteration}_product_intent_retry_readme_tool",
                        tool_name="readme_tool",
                        input_metadata=ToolInputMetadata.from_mapping("readme_tool", {
                            "project_path": str(project_path),
                            "project_summary": f"{goal}\n\nRecent Improvements:\n- " + "\n- ".join(
                                (getattr(self.runtime, "_project_improvement_actions", []) or []) + actions
                            ),
                            "written_files": written_files,
                            "entry_files": [str(target_file)],
                            "run_command": run_command,
                            "setup_commands": environment_payload.get("setup_commands") or [],
                            "environment": self.runtime._readme_environment_context(environment_payload),
                            "overwrite": True,
                        }),
                        parent_task_id=self.runtime._dashboard_stage_id("execution"),
                    )

        readme_succeeded = readme_result is None or readme_result.success
        success = review_result.success and review_approved and readme_succeeded
        if success:
            self.runtime._project_improvement_actions = (getattr(self.runtime, "_project_improvement_actions", []) or []) + actions
        if self.runtime.enhanced_ui:
            self.runtime._set_dashboard_task_status(self.runtime._dashboard_stage_id("execution"), "completed" if success else "failed")
        failed_tool = None if success else ("code_reviewer" if (not review_result.success or not review_approved) else "readme_tool")
        failure_reason = None
        if not success:
            failed_result = review_result if failed_tool == "code_reviewer" else readme_result
            if failed_tool == "code_reviewer" and review_result.success and not review_approved:
                suggestions = review_payload.get("suggestions") or review_payload.get("warnings") or []
                failure_reason = "; ".join(str(item) for item in suggestions[:2]) or "Code review rejected the product-fit of the generated code."
            else:
                failure_reason = self.visible_tool_failure_summary(
                    tool=failed_tool or "tool",
                    tool_result=failed_result,
                    retry_attempted=retry_attempted,
                )
        self._log_agent(
            "task_executor_completed",
            {"iteration": iteration},
            {"success": success, "failed_tool": failed_tool},
            success=success,
            error=failure_reason,
        )
        return IterationResult(
            iteration=iteration,
            validation_passed=success,
            completed_successful_iteration=False,
            applied_actions=actions,
            changed_files=[
                str(target_file),
                *([str(readme_path)] if readme_update_requested and readme_succeeded else []),
            ],
            success=success,
            error=failure_reason,
            failure_stage=None if success else "Task Executor",
            failed_tool=failed_tool,
            failure_reason=failure_reason,
            retry_attempted=retry_attempted,
            retry_history=retry_history,
        )

    def _resolve_task_files(
        self,
        *,
        task: Task,
        iteration: int,
        goal: str,
        project_path: Path,
        written_files: list[str],
        actions: list[str],
        improvement_report: dict[str, Any],
        evaluation: EvaluationResult,
    ) -> TaskFileResolutionMetadata | str:
        from autonomous_iteration.tool.task_file_resolver import task_file_resolver_executor

        designed_task = self._primary_designed_task(improvement_report)
        description = str((designed_task or {}).get("description") or (actions[0] if actions else ""))
        validation_issues = [
            issue.to_json_dict() if hasattr(issue, "to_json_dict") else issue
            for issue in (getattr(evaluation, "validation_issues", []) or [])
        ]
        failing_files = self._validation_issue_target_files(evaluation)
        designed_targets = self._string_list((designed_task or {}).get("target_files"))
        scoped_failing_files = self._scope_failing_files_to_task(failing_files, designed_targets)
        if scoped_failing_files:
            failing_files = scoped_failing_files
        self._append_validation_targets_to_dashboard(iteration, failing_files)
        target_hints = self._target_file_hints(designed_task, improvement_report, written_files, failing_files)
        primary_issue = self._primary_bug_fix_issue(evaluation)
        request = TaskFileResolutionRequestMetadata(
            project_path=str(project_path),
            task_description=description,
            acceptance_criteria=self._string_list((designed_task or {}).get("acceptance_criteria") or improvement_report.get("must_implement_next")),
            target_file_hints=target_hints,
            fallback_files=self._string_list(written_files),
            failing_files=failing_files,
            validation_issues=validation_issues,
            issue_category=str(getattr(primary_issue, "category", "") or ""),
            diagnosis=improvement_report.get("diagnosis") if isinstance(improvement_report.get("diagnosis"), dict) else {},
            selected_candidate=improvement_report.get("selected_candidate") if isinstance(improvement_report.get("selected_candidate"), dict) else {},
            goal=goal,
        )
        input_metadata = ToolInputMetadata.from_mapping(
            "task_file_resolver",
            {
                "project_path": str(project_path),
                "task_description": description,
                "goal": goal,
                "file_paths": target_hints,
                "written_files": written_files,
                "prompt_context": {
                    "acceptance_criteria": request.acceptance_criteria,
                    "failing_files": failing_files,
                    "validation_issues": validation_issues,
                    "issue_category": request.issue_category,
                    "diagnosis": request.diagnosis,
                    "selected_candidate": request.selected_candidate,
                    "improvement_report": improvement_report,
                },
                "attributes": {"request_metadata": request.to_json_dict()},
            },
        )
        try:
            if hasattr(self.runtime, "_execute_module_owned_tool"):
                envelope = self.runtime._execute_module_owned_tool(
                    task=task,
                    step_id=f"iteration_{iteration}_task_file_resolver",
                    tool_name="task_file_resolver",
                    input_metadata=input_metadata,
                    executor=task_file_resolver_executor,
                    parent_task_id=self.runtime._dashboard_stage_id("execution"),
                )
                if not envelope.success or envelope.output is None:
                    return envelope.error_message or "Task file resolver failed."
                resolution = envelope.output
            else:
                result = task_file_resolver_executor(input_metadata)
                resolution = result.result
        except Exception as exc:
            return str(exc)
        if not isinstance(resolution, TaskFileResolutionMetadata):
            try:
                resolution = TaskFileResolutionMetadata.model_validate(resolution)
            except Exception:
                return "Task file resolver returned invalid metadata."
        self._append_resolved_file_to_dashboard(iteration, resolution)
        return resolution

    def _execute_text_file_improvement(
        self,
        *,
        task: Task,
        iteration: int,
        goal: str,
        project_path: Path,
        target_file: Path,
        current_text: str,
        edit_kind: str,
        evaluation: EvaluationResult,
        actions: list[str],
        improvement_report: dict[str, Any],
    ) -> IterationResult:
        generated = self._generate_text_file_content(
            goal=goal,
            target_file=target_file,
            current_text=current_text,
            edit_kind=edit_kind,
            evaluation=evaluation,
            actions=actions,
            improvement_report=improvement_report,
        )
        if generated.strip() == current_text.strip():
            return self._failure(iteration, actions, "Generated file content did not change the target file.", "file_content_generator")
        validation_error = self._validate_text_file_content(target_file, generated, edit_kind)
        if validation_error:
            return self._failure(iteration, actions, validation_error, "file_content_generator")
        self._create_git_snapshot(
            project_path=project_path,
            iteration=iteration,
            reason=f"{edit_kind}_file_write",
            target_files=[str(target_file)],
            stage_key="execution",
        )
        write_result = self.runtime._execute_fast_tool(
            task=task,
            step_id=f"iteration_{iteration}_file_writer",
            tool_name="file_writer",
            input_metadata=ToolInputMetadata.from_mapping(
                "file_writer",
                {
                    "file_path": str(target_file),
                    "content": generated,
                    "encoding": "utf-8",
                    "create_dirs": True,
                    "overwrite": True,
                    "operation_kind": "file_replace",
                },
            ),
            parent_task_id=self.runtime._dashboard_stage_id("execution"),
        )
        if not write_result.success:
            reason = write_result.error_message or "Failed to write generated file content."
            return self._failure(iteration, actions, reason, "file_writer")
        if self.runtime.enhanced_ui:
            self.runtime._set_dashboard_task_status(self.runtime._dashboard_stage_id("execution"), "completed")
        self.runtime._project_improvement_actions = (getattr(self.runtime, "_project_improvement_actions", []) or []) + actions
        return IterationResult(
            iteration=iteration,
            validation_passed=True,
            completed_successful_iteration=False,
            applied_actions=actions,
            changed_files=[str(target_file)],
            success=True,
            failure_stage=None,
            failed_tool=None,
        )

    def _generate_text_file_content(
        self,
        *,
        goal: str,
        target_file: Path,
        current_text: str,
        edit_kind: str,
        evaluation: EvaluationResult,
        actions: list[str],
        improvement_report: dict[str, Any],
    ) -> str:
        llm_client = getattr(self.runtime, "llm_client", None)
        if llm_client and hasattr(llm_client, "complete"):
            try:
                response = llm_client.complete(
                    build_context_llm_request(
                        llm_client,
                        purpose=ContextRequestPurpose.TEXT_FILE_GENERATION,
                        messages=[
                            LLMMessage(
                                role="user",
                                content=(
                                    "You are editing a project file for OpenPilot autonomous iteration.\n"
                                    "Return ONLY the complete replacement file content. Do not include explanations.\n"
                                    f"Original goal: {goal}\n"
                                    f"Target file: {target_file.name}\n"
                                    f"Edit kind: {edit_kind}\n"
                                    f"Validation summary: {evaluation.summary}\n"
                                    f"Actions: {actions}\n"
                                    f"Acceptance criteria: {improvement_report.get('must_implement_next') or []}\n\n"
                                    f"Persisted stack preset: {improvement_report.get('stack_preset') or {}}\n"
                                    f"UI iteration contract: {improvement_report.get('ui_iteration_contract') or {}}\n\n"
                                    f"Current file content:\n{current_text}"
                                ),
                            )
                        ],
                        temperature=0.2,
                        user_truncation=ContextCandidateTruncation.FORBIDDEN,
                    ),
                    max_retries=1,
                    use_cache=False,
                )
                content = self._strip_outer_fence(str(response.content or ""))
                if content.strip():
                    return content
            except Exception:
                pass
        return self._fallback_text_file_content(target_file, current_text, actions, improvement_report)

    def _fallback_text_file_content(
        self,
        target_file: Path,
        current_text: str,
        actions: list[str],
        improvement_report: dict[str, Any],
    ) -> str:
        criteria = self._string_list(improvement_report.get("must_implement_next"))
        heading = "Iteration Update"
        if target_file.suffix.lower() in {".md", ".markdown"}:
            lines = [current_text.rstrip(), "", f"## {heading}"]
            lines.extend(f"- {item}" for item in (criteria or actions))
            return "\n".join(lines).rstrip() + "\n"
        if target_file.suffix.lower() == ".html":
            items = "\n".join(f"      <li>{escape(str(item))}</li>" for item in (criteria or actions))
            return (
                "<!doctype html>\n"
                '<html lang="en">\n'
                "  <head>\n"
                '    <meta charset="utf-8">\n'
                '    <meta name="viewport" content="width=device-width, initial-scale=1">\n'
                "    <title>Application</title>\n"
                "  </head>\n"
                "  <body>\n"
                "    <main>\n"
                "      <h1>Application</h1>\n"
                "      <ul>\n"
                f"{items}\n"
                "      </ul>\n"
                "    </main>\n"
                "  </body>\n"
                "</html>\n"
            )
        return current_text.rstrip() + "\n\n" + "\n".join(str(item) for item in (criteria or actions)) + "\n"

    def _validate_text_file_content(self, target_file: Path, content: str, edit_kind: str) -> str:
        suffix = target_file.suffix.lower()
        if suffix == ".json" or edit_kind == "config" and suffix == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                return f"Generated config has JSON syntax error on line {exc.lineno}: {exc.msg}"
        return ""

    def _append_resolved_file_to_dashboard(self, iteration: int, resolution: TaskFileResolutionMetadata) -> None:
        if not getattr(self.runtime, "enhanced_ui", None) or resolution.primary_file is None:
            return
        if not hasattr(self.runtime, "_append_dashboard_stage_child"):
            return
        self.runtime._append_dashboard_stage_child(
            "execution",
            child_id=f"resolved_file_{iteration}",
            description=f"Resolved file: {Path(resolution.primary_file.file_path).name} ({resolution.recommended_edit_kind})",
            kind="result",
        )

    def _append_validation_targets_to_dashboard(self, iteration: int, target_files: list[str]) -> None:
        if not target_files or not getattr(self.runtime, "enhanced_ui", None):
            return
        if not hasattr(self.runtime, "_append_dashboard_stage_child"):
            return
        labels = ", ".join(Path(path).name for path in target_files[:5])
        self.runtime._append_dashboard_stage_child(
            "execution",
            child_id=f"validation_issue_targets_{iteration}",
            description=f"Validation issue target: {labels}",
            kind="result",
        )

    def _uses_python_code_pipeline(self, target_file: Path, edit_kind: str) -> bool:
        return edit_kind == "source_code" and target_file.suffix.lower() == ".py"

    def _primary_designed_task(self, improvement_report: dict[str, Any]) -> dict[str, Any]:
        tasks = improvement_report.get("designed_tasks")
        if isinstance(tasks, list):
            for task in tasks:
                if isinstance(task, dict):
                    return task
        return {}

    def _designed_task_targets_file(
        self,
        improvement_report: dict[str, Any],
        *,
        project_path: Path,
        target_file: Path,
    ) -> bool:
        designed_task = self._primary_designed_task(improvement_report)
        requested_targets = self._string_list(designed_task.get("target_files"))
        if not requested_targets:
            return False
        canonical_target = target_file.expanduser().resolve(strict=False)
        for raw_target in requested_targets:
            candidate = Path(raw_target).expanduser()
            if not candidate.is_absolute():
                candidate = project_path / candidate
            if candidate.resolve(strict=False) == canonical_target:
                return True
        return False

    def _target_file_hints(
        self,
        designed_task: dict[str, Any] | None,
        improvement_report: dict[str, Any],
        written_files: list[str],
        failing_files: list[str] | None = None,
    ) -> list[str]:
        hints = []
        hints.extend(self._string_list(failing_files))
        if isinstance(designed_task, dict):
            hints.extend(self._string_list(designed_task.get("target_files")))
        selected_goal = improvement_report.get("selected_goal")
        if isinstance(selected_goal, dict):
            hints.extend(self._string_list(selected_goal.get("target_files")))
        return self._dedupe_text(hints)

    def _validation_issue_target_files(self, evaluation: EvaluationResult) -> list[str]:
        primary_issue = self._primary_bug_fix_issue(evaluation)
        ordered_issues = []
        if primary_issue is not None:
            ordered_issues.append(primary_issue)
        ordered_issues.extend(
            issue
            for issue in (getattr(evaluation, "validation_issues", []) or [])
            if issue is not primary_issue
        )
        targets: list[str] = []
        for issue in ordered_issues:
            if getattr(issue, "severity", "blocking") != "blocking":
                continue
            targets.extend(self._string_list(getattr(issue, "target_files", []) or []))
        return self._dedupe_text(targets)

    def _scope_failing_files_to_task(self, failing_files: list[str], task_targets: list[str]) -> list[str]:
        if not failing_files or not task_targets:
            return []
        normalized_targets = {self._normalize_path_key(path) for path in task_targets}
        scoped = [
            path for path in failing_files
            if self._normalize_path_key(path) in normalized_targets
        ]
        return self._dedupe_text(scoped)

    @staticmethod
    def _normalize_path_key(path: str) -> str:
        text = str(path or "").strip()
        if not text:
            return ""
        candidate = Path(text).expanduser()
        try:
            return str(candidate.resolve())
        except OSError:
            return candidate.as_posix()

    @staticmethod
    def _strip_outer_fence(text: str) -> str:
        stripped = text.strip()
        if not stripped.startswith("```") or not stripped.endswith("```"):
            return text
        lines = stripped.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip() + "\n"
        return text

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            result = []
            for item in value:
                if isinstance(item, dict):
                    item = item.get("file_path") or item.get("path") or item.get("name")
                text = str(item or "").strip()
                if text:
                    result.append(text)
            return result
        return [str(value)]

    @staticmethod
    def _dedupe_text(items: list[str]) -> list[str]:
        seen = set()
        result = []
        for item in items:
            text = str(item or "").strip()
            if text and text not in seen:
                result.append(text)
                seen.add(text)
        return result

    def _is_product_intent_rejection(self, review_payload: Any) -> bool:
        if not hasattr(review_payload, "get"):
            return False
        categories = [str(item) for item in review_payload.get("rejection_categories") or []]
        warnings = " ".join(str(item) for item in (review_payload.get("warnings") or []) + (review_payload.get("suggestions") or []))
        return "product_intent_drift" in categories or "Product intent drift" in warnings or "Product-fit rubric not satisfied" in warnings

    def _run_product_intent_retry(
        self,
        *,
        task: Task,
        iteration: int,
        goal: str,
        target_file: Path,
        rejected_code: str,
        evaluation: EvaluationResult,
        actions: list[str],
        improvement_report: dict[str, Any],
        review_payload: Any,
        is_repair: bool,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        reviewer_feedback = []
        if hasattr(review_payload, "get"):
            reviewer_feedback = [str(item) for item in (review_payload.get("suggestions") or review_payload.get("warnings") or [])[:5]]
        retry_report = {
            **improvement_report,
            "reviewer_rejection": {
                "category": "product_intent_drift",
                "feedback": reviewer_feedback,
            },
            "must_implement_next": [
                *reviewer_feedback[:3],
                "Regenerate the implementation so it fixes the issue while preserving product intent.",
            ],
        }
        prompt_context = self.build_code_generation_prompt_context(
            goal=goal,
            target_file=target_file,
            current_code=rejected_code,
            evaluation=evaluation,
            actions=actions,
            improvement_report=retry_report,
            is_repair=is_repair,
            simplified=True,
            mode="product_intent_retry",
        )
        prompt_context["reviewer_rejection"] = retry_report["reviewer_rejection"]
        code_result = self.execute_code_generation_for_improvement(
            task=task,
            iteration=iteration,
            target_file=target_file,
            improvement_prompt="Regenerate after product intent drift rejection.",
            simplified=True,
            mode="product_intent_retry",
            prompt_context=prompt_context,
        )
        history = [
            self.code_generation_attempt_summary(
                mode="product_intent_retry",
                prompt=json.dumps(prompt_context, ensure_ascii=False, default=str),
                result=code_result,
                attempt=1,
            )
        ]
        self.append_code_generation_attempt_to_dashboard(iteration, history[0])
        if not code_result.success or code_result.output is None:
            return {"success": False}, history
        retry_code = str(code_result.output.get("code", ""))
        try:
            ast.parse(retry_code)
        except SyntaxError:
            return {"success": False}, history
        pre_retry_snapshot = self._create_git_snapshot(
            project_path=target_file.parent,
            iteration=iteration,
            reason="product_intent_retry_write",
            target_files=[str(target_file)],
            stage_key="execution",
        )
        write_result = self.runtime._execute_fast_tool(
            task=task,
            step_id=f"iteration_{iteration}_product_intent_retry_file_writer",
            tool_name="file_writer",
            input_metadata=ToolInputMetadata.from_mapping("file_writer", {
                "file_path": str(target_file),
                "content": retry_code,
                "encoding": "utf-8",
                "create_dirs": True,
                "overwrite": True,
                "operation_kind": "file_replace",
            }),
            parent_task_id=self.runtime._dashboard_stage_id("execution"),
        )
        if not write_result.success:
            return {"success": False, "write_result": write_result}, history
        retry_prompt_context = {
            **prompt_context,
            "git_safety_context": self._git_safety_context(
                project_path=target_file.parent,
                snapshot=pre_retry_snapshot,
                target_files=[str(target_file)],
            ),
        }
        review_result = self.runtime._execute_fast_tool(
            task=task,
            step_id=f"iteration_{iteration}_product_intent_retry_code_reviewer",
            tool_name="code_reviewer",
            input_metadata=ToolInputMetadata.from_mapping("code_reviewer", {
                "code": retry_code,
                "language": "python",
                "prompt_context": retry_prompt_context,
            }),
            parent_task_id=self.runtime._dashboard_stage_id("execution"),
        )
        return {
            "success": review_result.success,
            "code": retry_code,
            "write_result": write_result,
            "review_result": review_result,
        }, history

    def _should_use_bug_fix(self, evaluation: EvaluationResult) -> bool:
        warning_check = getattr(evaluation, "warning_check_result", None)
        if warning_check and warning_check.requires_fix:
            return True
        issues = list(getattr(evaluation, "validation_issues", []) or [])
        return any(self._issue_is_bug_fixable(issue) for issue in issues)

    def _issue_is_bug_fixable(self, issue: Any) -> bool:
        if getattr(issue, "severity", "blocking") != "blocking":
            return False
        category = str(getattr(issue, "category", "") or "")
        if category in {"runtime_error", "runtime_warning"}:
            return True
        repair_kind = str(getattr(issue, "recommended_repair_kind", "") or "")
        return category in {"environment", "configuration"} and repair_kind in {
            "fix_startup_import_contract",
            "repair_runtime_system_output",
            "make_web_port_configurable",
            "defer_required_secret_validation",
        }

    def _attempt_environment_repair(
        self,
        *,
        task: Task,
        project_path: Path,
        environment_result: ToolExecutionEnvelopeMetadata,
        repair_attempt: int,
    ) -> ToolExecutionEnvelopeMetadata | None:
        error_message = environment_result.error_message or "Project environment sync failed."
        input_metadata = ToolInputMetadata.from_mapping(
            "environment_fix_tool",
            {
                "project_path": str(project_path),
                "stderr": error_message,
                "context": error_message,
                "_memory_store": getattr(self.runtime, "memory_store", None),
            },
        )
        execute_fix = getattr(self.runtime, "_execute_environment_fix_agent_tool", None)
        if callable(execute_fix):
            return execute_fix(
                task=task,
                step_id=f"iteration_environment_fix_tool_{repair_attempt}",
                input_metadata=input_metadata,
                parent_task_id=self.runtime._dashboard_stage_id("environment"),
            )
        output_metadata = environment_fix_tool_executor(input_metadata)
        success = output_metadata.status == ResultStatus.SUCCESS
        return ToolExecutionEnvelopeMetadata(
            tool_name="environment_fix_tool",
            step_id=f"iteration_environment_fix_tool_{repair_attempt}",
            status=output_metadata.status,
            success=success,
            input_metadata=input_metadata,
            output_metadata=output_metadata,
            failure=None if success else output_metadata.failure,
        )

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

    def _execute_bug_fix(
        self,
        *,
        task: Task,
        iteration: int,
        target_file: Path,
        run_command: str,
        evaluation: EvaluationResult,
        actions: list[str],
    ) -> IterationResult:
        warning_check = evaluation.warning_check_result
        primary_issue = self._primary_bug_fix_issue(evaluation)
        fix_instruction = (
            "Fix only the runtime/program execution failure reported by validation. "
            "Do not change product behavior, controls, scoring, UX goals, or unrelated semantics."
        )
        if primary_issue is not None:
            fix_instruction += f"\nValidation issue: {primary_issue.message}"
            if primary_issue.recommended_action:
                fix_instruction += f"\nRecommended action: {primary_issue.recommended_action}"
        if warning_check:
            fix_instruction += f"\nWarning reason: {warning_check.reason}"
            fix_instruction += f"\nRecommended fix: {warning_check.recommended_fix}"
        issue_target_files = self._scope_failing_files_to_task(
            self._validation_issue_target_files(evaluation),
            [str(target_file)],
        ) or [str(target_file)]
        snapshot = self._create_git_snapshot(
            project_path=target_file.parent,
            iteration=iteration,
            reason="bug_fix_tool",
            target_files=issue_target_files,
            stage_key="execution",
        )
        result = self.runtime._execute_fast_tool(
            task=task,
            step_id=f"iteration_{iteration}_bug_fix_tool",
            tool_name="bug_fix_tool",
            input_metadata=ToolInputMetadata.from_mapping(
                "bug_fix_tool",
                {
                    "command": run_command,
                    "cwd": str(target_file.parent),
                    "file_paths": issue_target_files,
                    "timeout": 30,
                    "warning_check_required": bool(warning_check and warning_check.requires_fix),
                    "warning_check_result": warning_check.to_json_dict() if warning_check else None,
                    "fix_instruction": fix_instruction,
                    "attributes": {
                        "git_safety_context": self._git_safety_context(
                            project_path=target_file.parent,
                            snapshot=snapshot,
                            target_files=issue_target_files,
                        ),
                    },
                    "_llm_client": getattr(self.runtime, "llm_client", None),
                },
            ),
            parent_task_id=self.runtime._dashboard_stage_id("execution"),
        )
        if result.success:
            changed_files = self._bug_fix_changed_files(result.output) or issue_target_files or [str(target_file)]
            return IterationResult(
                iteration=iteration,
                validation_passed=True,
                completed_successful_iteration=False,
                applied_actions=actions,
                changed_files=changed_files,
                success=True,
                failure_stage=None,
                failed_tool=None,
            )
        reason = result.error_message or "bug_fix_tool failed to repair the runtime execution issue."
        return self._failure(iteration, actions, reason, "bug_fix_tool")

    def _primary_bug_fix_issue(self, evaluation: EvaluationResult):
        issues = [
            issue
            for issue in (getattr(evaluation, "validation_issues", []) or [])
            if self._issue_is_bug_fixable(issue)
        ]
        blocking = [issue for issue in issues if getattr(issue, "severity", "blocking") == "blocking"]
        return (blocking or issues or [None])[0]

    def _bug_fix_changed_files(self, output: Any) -> list[str]:
        attempts = getattr(output, "attempts", []) or []
        changed: list[str] = []
        for attempt in attempts:
            changed.extend(self._string_list(getattr(attempt, "modified_files", []) or []))
        return self._dedupe_text(changed)

    def run_code_generation_retry_pipeline(
        self,
        *,
        task: Task,
        iteration: int,
        goal: str,
        target_file: Path,
        current_code: str,
        evaluation: EvaluationResult,
        actions: list[str],
        improvement_report: dict[str, Any],
        is_repair: bool,
    ) -> tuple[ToolExecutionEnvelopeMetadata, list[dict[str, Any]]]:
        attempts = [
            (
                "full",
                self.build_code_generation_prompt_context(
                    goal=goal,
                    target_file=target_file,
                    current_code=current_code,
                    evaluation=evaluation,
                    actions=actions,
                    improvement_report=improvement_report,
                    is_repair=is_repair,
                    simplified=False,
                    mode="full",
                ),
            ),
            (
                "compact",
                self.build_code_generation_prompt_context(
                    goal=goal,
                    target_file=target_file,
                    current_code=current_code,
                    evaluation=evaluation,
                    actions=actions,
                    improvement_report=improvement_report,
                    is_repair=is_repair,
                    simplified=True,
                    mode="compact",
                ),
            ),
            (
                "surgical",
                self.build_code_generation_prompt_context(
                    goal=goal,
                    target_file=target_file,
                    current_code=current_code,
                    evaluation=evaluation,
                    actions=actions,
                    improvement_report=improvement_report,
                    is_repair=is_repair,
                    simplified=True,
                    mode="surgical",
                ),
            ),
        ]
        retry_history: list[dict[str, Any]] = []
        last_result: ToolExecutionEnvelopeMetadata | None = None

        for attempt_index, (mode, prompt_context) in enumerate(attempts, 1):
            prompt_length = len(json.dumps(prompt_context, ensure_ascii=False, default=str))
            if attempt_index > 1:
                previous_error = last_result.error_message if last_result else "unknown error"
                self.runtime.logger.log_event(
                    "autonomous_iteration_code_generation_retry",
                    {
                        "iteration": iteration,
                        "tool": "code_generator",
                        "mode": mode,
                        "attempt": attempt_index,
                        "previous_error": previous_error,
                        "prompt_length": prompt_length,
                        "prompt_layers": self.runtime._prompt_context_layer_summary(prompt_context),
                        "selected_actions": actions,
                        "target_file": str(target_file),
                    },
                    session_id=self.runtime.session_id or "unknown",
                    turn_id=1,
                )
                self._log_agent(
                    "code_generation_retry",
                    {"iteration": iteration, "mode": mode, "previous_error": previous_error},
                    {"prompt_length": prompt_length},
                )
                if self.runtime.enhanced_ui:
                    self.runtime.enhanced_ui.set_current_task_state(
                        title=f"Improvement {iteration} retry",
                        details=(
                            f"Retry mode: {mode}\n"
                            f"Prompt length: {prompt_length}\n"
                            f"Previous error: {previous_error}"
                        ),
                        status="running",
                    )

            result = self.execute_code_generation_for_improvement(
                task=task,
                iteration=iteration,
                target_file=target_file,
                improvement_prompt=str(prompt_context.get("tool_task") or (actions[0] if actions else "")),
                prompt_context=prompt_context,
                simplified=(mode == "compact"),
                mode=mode,
            )
            last_result = result
            attempt_summary = self.code_generation_attempt_summary(
                mode=mode,
                prompt=json.dumps(prompt_context, ensure_ascii=False, default=str),
                result=result,
                attempt=attempt_index,
            )
            retry_history.append(attempt_summary)
            self.append_code_generation_attempt_to_dashboard(iteration, attempt_summary)
            if result.success and result.output is not None:
                return result, retry_history
            if not self.should_retry_code_generation_attempt(result):
                return result, retry_history

        if last_result is None:
            last_result = ToolExecutionEnvelopeMetadata(
                tool_name="code_generator",
                step_id=f"iteration_{iteration}_code_generator",
                status="fail",
                success=False,
                input_metadata=ToolInputMetadata(tool_name="code_generator"),
                failure={
                    "kind": "failure",
                    "error_type": "CodeGenerationError",
                    "error_message": "No code generation attempts were executed.",
                },
            )
        return last_result, retry_history

    def build_code_generation_prompt_context(
        self,
        *,
        goal: str,
        target_file: Path,
        current_code: str,
        evaluation: EvaluationResult,
        actions: list[str],
        improvement_report: dict[str, Any],
        is_repair: bool,
        simplified: bool,
        mode: str,
    ) -> dict[str, Any]:
        if mode == "surgical":
            code_context = self.compact_code_context(current_code, actions, max_chars=1000)
            agent_instruction = (
                "Surgical retry: make the smallest complete source replacement that satisfies the iteration goal. "
                "Avoid unrelated rewrites."
            )
        elif simplified:
            code_context = self.compact_code_context(current_code, actions, max_chars=2200)
            agent_instruction = (
                "Compact retry: previous attempt was too expensive or failed. Use only the relevant code context "
                "and preserve existing useful behavior."
            )
        else:
            code_context = self.budget_code_context(current_code, max_chars=7000)
            agent_instruction = (
                "Full improvement: implement the selected autonomous iteration goal while honoring the product-fit rubric."
            )

        acceptance = (
            improvement_report.get("must_implement_next")
            or improvement_report.get("selected_goal", {}).get("acceptance_criteria")
            or actions[:2]
        )
        tool_task = actions[0] if actions else str(improvement_report.get("next_iteration_goal") or "Apply the selected improvement.")
        prompt_context = self.runtime._build_prompt_context(
            original_goal=goal,
            project_path=target_file.parent,
            written_files=[str(target_file)],
            run_command="",
            evaluation=evaluation,
            iteration_goal=str(improvement_report.get("next_iteration_goal") or tool_task),
            acceptance_criteria=[str(item) for item in acceptance[:5]],
            tool_task=tool_task,
            agent_instruction=agent_instruction,
            target_file=target_file,
            current_code=current_code,
            code_context=code_context,
            mode=mode,
        )
        prompt_context["improvement_report_summary"] = {
            "summary": improvement_report.get("summary") or "",
            "opportunities": (improvement_report.get("improvement_opportunities") or [])[:4],
            "recommended_actions": (improvement_report.get("recommended_actions") or [])[:4],
            "blocking_risks": (improvement_report.get("blocking_risks") or [])[:3],
        }
        if isinstance(improvement_report.get("diagnosis"), dict):
            prompt_context["diagnosis"] = improvement_report["diagnosis"]
            diagnosis = improvement_report["diagnosis"]
            if isinstance(diagnosis.get("dependencies"), list):
                prompt_context["dependencies"] = diagnosis["dependencies"]
            if isinstance(diagnosis.get("dependency_strategy"), dict):
                prompt_context["dependency_strategy"] = diagnosis["dependency_strategy"]
        if isinstance(improvement_report.get("selected_candidate"), dict):
            prompt_context["selected_candidate"] = improvement_report["selected_candidate"]
        return prompt_context

    def build_surgical_project_improvement_prompt(
        self,
        *,
        goal: str,
        target_file: Path,
        current_code: str,
        evaluation: EvaluationResult,
        actions: list[str],
        improvement_report: dict[str, Any],
        is_repair: bool,
    ) -> str:
        requirements = improvement_report.get("must_implement_next") or actions[:2]
        code_context = self.compact_code_context(current_code, actions, max_chars=900)
        return (
            "SURGICAL RETRY MODE: previous code-generation attempts timed out or failed.\n"
            "Make the smallest safe complete replacement for this Python file.\n"
            f"Goal: {goal}\n"
            f"Target file: {target_file.name}\n"
            f"Mode: {'repair validation failure' if is_repair else 'single focused improvement'}\n"
            f"Only action: {self.runtime._short_dashboard_text(actions[0] if actions else '', 300)}\n"
            f"Acceptance criteria: {requirements[:3]}\n"
            f"Current validation errors: {evaluation.validation_errors[:2]}\n"
            "Do not add unrelated features. Preserve existing gameplay, controls, score, food, collision, restart/quit, and entry point.\n"
            "Return only full Python source code.\n\n"
            f"Minimal relevant code context:\n{code_context}"
        )

    def code_generation_attempt_summary(
        self,
        *,
        mode: str,
        prompt: str,
        result: ToolExecutionEnvelopeMetadata,
        attempt: int,
    ) -> dict[str, Any]:
        return {
            "attempt": attempt,
            "mode": mode,
            "step_id": result.step_id,
            "prompt_length": len(prompt),
            "status": result.status,
            "success": result.success,
            "duration_seconds": result.duration_seconds,
            "error_type": result.failure.error_type if result.failure else None,
            "error": result.error_message,
            "timeout_override": result.timeout_override,
            "tool_retry_count": result.retry_count,
            "tool_retry_history": result.retry_history,
        }

    def append_code_generation_attempt_to_dashboard(self, iteration: int, attempt: dict[str, Any]) -> None:
        if not self.runtime.enhanced_ui:
            return
        status = "completed" if attempt.get("success") else "failed"
        duration = attempt.get("duration_seconds")
        duration_text = f"; duration={duration:.0f}s" if isinstance(duration, (int, float)) else ""
        error = attempt.get("error") or ""
        self.runtime._append_dashboard_stage_child(
            "execution",
            child_id=f"code_generation_attempt_{iteration}_{attempt.get('mode')}",
            description=(
                f"code_generator {attempt.get('mode')} attempt: "
                f"prompt={attempt.get('prompt_length')} chars; status={attempt.get('status')}"
                f"{duration_text}" + (f"; error={self.runtime._short_dashboard_text(error, 80)}" if error else "")
            ),
            kind="result",
            status=status,
        )

    def should_retry_code_generation_attempt(self, result: ToolExecutionEnvelopeMetadata) -> bool:
        if result.success:
            return False
        failure = result.failure
        details = failure.details if failure and isinstance(failure.details, dict) else {}
        provider_attempt = details.get("provider_attempt")
        if not isinstance(provider_attempt, dict):
            return False
        if provider_attempt.get("finish_reason") != "length":
            return False
        usage = provider_attempt.get("usage")
        if not isinstance(usage, dict):
            return False
        return any(
            usage.get(key) is not None
            for key in (
                "input_tokens",
                "prompt_tokens",
                "output_tokens",
                "completion_tokens",
                "total_tokens",
            )
        )

    def build_project_improvement_prompt(
        self,
        *,
        goal: str,
        target_file: Path,
        current_code: str,
        evaluation: EvaluationResult,
        actions: list[str],
        improvement_report: dict[str, Any],
        is_repair: bool,
        simplified: bool,
    ) -> str:
        report_summary = improvement_report.get("summary") or ""
        report_opportunities = improvement_report.get("improvement_opportunities") or []
        report_recommended_actions = improvement_report.get("recommended_actions") or []
        report_must_implement = improvement_report.get("must_implement_next") or []
        report_blocking_risks = improvement_report.get("blocking_risks") or []
        selected_goal = improvement_report.get("selected_goal") or {}
        designed_tasks = improvement_report.get("designed_tasks") or []
        if simplified:
            compact_requirements = report_must_implement or actions[:2]
            code_context = self.compact_code_context(current_code, actions, max_chars=1800)
            return (
                "COMPACT RETRY MODE: the previous code-generation request timed out.\n"
                "Make the smallest complete replacement for the target Python file.\n"
                f"Original user goal: {goal}\n"
                f"Target file: {target_file.name}\n"
                f"Iteration mode: {'repair blocking validation failure' if is_repair else 'focused improvement'}\n"
                f"Selected action: {self.runtime._short_dashboard_text(actions[0] if actions else '', 500)}\n"
                f"Must satisfy: {compact_requirements[:3]}\n"
                f"Validation errors: {evaluation.validation_errors[:3]}\n"
                "Preserve existing controls, scoring, food, collision/game-over, restart/quit, and runnable entry point.\n"
                "Return only the full replacement Python code.\n\n"
                f"Relevant current code:\n{code_context}"
            )
        observable_requirement = (
            "Fix the blocking validation problem without adding unrelated features."
            if is_repair
            else (
                "Implement one visible, user-observable upgrade from the selected task. "
                "Do not rewrite unrelated parts, remove existing behavior, or simplify the game. "
                "Preserve controls, score display, food, collision/game-over handling, restart/quit behavior, and README run compatibility."
            )
        )
        code_context = self.budget_code_context(current_code, max_chars=9000)
        return (
            "FULL IMPROVEMENT MODE\n"
            f"Improve this Python project for the original user goal: {goal}\n"
            f"Target file: {target_file}\n"
            f"Iteration mode: {'repair blocking validation failure' if is_repair else 'feature/quality improvement'}\n"
            f"Latest validation passed: {evaluation.validation_passed}\n"
            f"Validation errors: {evaluation.validation_errors}\n"
            f"Warnings: {evaluation.warnings[:3]}\n"
            f"Improvement report summary: {report_summary}\n"
            f"Improvement opportunities: {report_opportunities[:4]}\n"
            f"Recommended actions from report: {report_recommended_actions[:3]}\n"
            f"Must implement next: {report_must_implement[:4]}\n"
            f"Blocking risks from report: {report_blocking_risks[:3]}\n"
            f"Selected goal: {selected_goal}\n"
            f"Designed tasks: {designed_tasks[:1]}\n"
            f"Selected actions for this iteration: {actions[:2]}\n"
            f"Implementation requirement: {observable_requirement}\n\n"
            "Return a complete replacement for the target Python file only. "
            "Preserve a runnable entry point and avoid unrelated rewrites.\n\n"
            f"Current code context:\n{code_context}"
        )

    def execute_code_generation_for_improvement(
        self,
        *,
        task: Task,
        iteration: int,
        target_file: Path,
        improvement_prompt: str,
        simplified: bool,
        mode: str | None = None,
        prompt_context: dict[str, Any] | None = None,
    ) -> ToolExecutionEnvelopeMetadata:
        mode = mode or ("compact" if simplified else "full")
        step_prefix = "" if mode == "full" else f"{mode}_"
        prompt_context = prompt_context or {}
        project_context = (
            prompt_context.get("project_context")
            if isinstance(prompt_context.get("project_context"), dict)
            else {}
        )
        try:
            current_code = target_file.read_text(encoding="utf-8")
        except OSError:
            current_code = str(
                project_context.get("current_code_context")
                or prompt_context.get("current_code")
                or ""
            )
        symbol_name = self._infer_target_symbol(current_code, prompt_context)
        tool_name = "code_editor" if symbol_name else "code_generator"
        input_metadata_payload = {
            "task_description": improvement_prompt,
            "language": "python",
            "context": f"Improve {target_file} ({mode} retry mode)",
        }
        if symbol_name:
            input_metadata_payload.update(
                {
                    "file_path": str(target_file),
                    "operation_kind": "modify_symbol",
                    "target_scope": "symbol",
                    "symbol_name": symbol_name,
                    "code": current_code,
                }
            )
            prompt_context = {**prompt_context, "operation_kind": "modify_symbol", "symbol_name": symbol_name}
        else:
            input_metadata_payload["operation_kind"] = "file_replace"
        if prompt_context:
            input_metadata_payload["prompt_context"] = prompt_context
        input_metadata = ToolInputMetadata.from_mapping(tool_name, input_metadata_payload)
        if tool_name in {"code_generator", "code_editor"}:
            budget_resolver = getattr(self.runtime, "_enhancement_runtime_budget", None)
            if callable(budget_resolver):
                improvement_policy = getattr(
                    self.runtime, "project_improvement_policy", None
                )
                input_metadata = input_metadata.model_copy(
                    update={
                        "runtime_handles": {
                            **input_metadata.runtime_handles,
                            "_runtime_budget": budget_resolver(),
                            "_enhancement_requirement": (
                                EnhancementCompletionRequirement.REQUIRED
                                if bool(
                                    getattr(
                                        improvement_policy,
                                        "controls_top_level_success",
                                        False,
                                    )
                                )
                                else EnhancementCompletionRequirement.OPTIONAL
                            ),
                        }
                    }
                )
        result = self.runtime._execute_fast_tool(
            task=task,
            step_id=f"iteration_{iteration}_{step_prefix}{tool_name}",
            tool_name=tool_name,
            input_metadata=input_metadata,
            parent_task_id=self.runtime._dashboard_stage_id("execution"),
        )
        self._log_agent(
            "code_generation_completed",
            {"iteration": iteration, "mode": mode, "target_file": str(target_file), "tool": tool_name},
            {"success": result.success, "status": result.status},
            success=result.success,
            error=result.error_message,
        )
        return result

    def _infer_target_symbol(self, current_code: str, prompt_context: dict[str, Any]) -> str | None:
        if not current_code.strip():
            return None
        explicit = prompt_context.get("symbol_name")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        try:
            tree = ast.parse(current_code)
        except SyntaxError:
            return None
        symbols = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        if not symbols:
            return None
        evidence_layers = (
            prompt_context.get("iteration_goal"),
            prompt_context.get("acceptance_criteria"),
            prompt_context.get("tool_task"),
            prompt_context.get("improvement_report_summary"),
        )
        for evidence in evidence_layers:
            haystack = str(evidence or "").lower()
            if not haystack:
                continue
            matches = []
            for symbol in symbols:
                escaped = re.escape(symbol.lower())
                if re.search(rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])", haystack):
                    matches.append(symbol)
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return None
        return None

    def is_timeout_tool_result(self, result: ToolExecutionEnvelopeMetadata) -> bool:
        if result.success:
            return False
        error_type = result.failure.error_type if result.failure else ""
        error_text = f"{error_type} {result.error_message or ''} {result.status or ''}"
        return "timeout" in error_text.lower()

    def visible_tool_failure_summary(
        self,
        *,
        tool: str,
        tool_result: ToolExecutionEnvelopeMetadata,
        retry_attempted: bool = False,
    ) -> str:
        raw_error = tool_result.error_message or f"{tool} failed."
        timeout = self.is_timeout_tool_result(tool_result)
        timeout_seconds = tool_result.timeout_override
        duration = tool_result.duration_seconds
        if timeout:
            elapsed = timeout_seconds or (f"{duration:.0f}" if isinstance(duration, (int, float)) else None)
            elapsed_text = f" after {elapsed}s" if elapsed else ""
            return f"{tool} timed out{elapsed_text}; compact retry attempted: {'yes' if retry_attempted else 'no'}"
        return str(raw_error)

    def budget_code_context(self, code: str, max_chars: int) -> str:
        if len(code) <= max_chars:
            return code
        head = code[: max_chars // 2]
        tail = code[-max_chars // 2 :]
        return (
            f"{head}\n\n"
            f"# [OpenPilot prompt budget: omitted {len(code) - len(head) - len(tail)} middle chars]\n\n"
            f"{tail}"
        )

    def compact_code_context(self, code: str, actions: list[str], max_chars: int) -> str:
        if len(code) <= max_chars:
            return code
        lines = code.splitlines()
        action_text = " ".join(actions).lower()
        keywords = {"def ", "class ", "if __name__"}
        if "key" in action_text or "control" in action_text or "pause" in action_text or "wasd" in action_text:
            keywords.update({"on_key", "bind", "update", "draw"})
        snippets: list[str] = []
        for idx, line in enumerate(lines):
            if any(keyword in line for keyword in keywords):
                start = max(0, idx - 4)
                end = min(len(lines), idx + 18)
                snippet = "\n".join(lines[start:end])
                if snippet not in snippets:
                    snippets.append(snippet)
        context = "\n\n# ...\n\n".join(snippets)
        if not context:
            context = "\n".join(lines[:80])
        if len(context) > max_chars:
            context = context[:max_chars] + "\n# [OpenPilot compact prompt truncated]"
        return context

    def log_iteration_failure(
        self,
        *,
        iteration: int,
        stage: str,
        tool: str,
        target_file: Path,
        actions: list[str],
        error: str,
        prompt_length: int,
        current_code_length: int,
        retry_attempted: bool,
        tool_result: ToolExecutionEnvelopeMetadata,
        retry_history: list[dict[str, Any]] | None = None,
    ) -> None:
        visible_summary = self.visible_tool_failure_summary(
            tool=tool,
            tool_result=tool_result,
            retry_attempted=retry_attempted,
        )
        self.runtime.logger.log_event(
            "autonomous_iteration_stage_failed",
            {
                "iteration": iteration,
                "stage": stage,
                "tool": tool,
                "target_file": str(target_file),
                "iteration_goal": actions[0] if actions else "",
                "actions": actions,
                "selected_actions": actions,
                "error": visible_summary,
                "error_type": tool_result.failure.error_type if tool_result.failure else None,
                "status": tool_result.status,
                "timeout_override": tool_result.timeout_override,
                "duration_seconds": tool_result.duration_seconds,
                "prompt_length": prompt_length,
                "current_code_length": current_code_length,
                "retry_attempted": retry_attempted,
                "compact_retry_used": retry_attempted,
                "retry_history": retry_history or [],
                "retry_attempts_used": len(retry_history or []),
                "visible_summary": visible_summary,
            },
            session_id=self.runtime.session_id or "unknown",
            turn_id=1,
        )
        self._log_agent(
            "task_executor_failed",
            {"iteration": iteration, "stage": stage, "tool": tool},
            {"error": visible_summary},
            success=False,
            error=visible_summary,
        )
        if self.runtime.enhanced_ui:
            self.runtime._finish_active_operations(visible_summary)
            self.runtime.enhanced_ui.set_current_task_state(
                title=f"{stage} failed",
                details=(
                    f"Iteration: {iteration}\n"
                    f"Stage: {stage}\n"
                    f"Tool: {tool}\n"
                    f"Reason: {visible_summary}\n"
                    f"Retry attempted: {'yes' if retry_attempted else 'no'}\n"
                    f"Attempts used: {len(retry_history or [])}"
                ),
                status="failed",
            )

    def _failure(
        self,
        iteration: int,
        actions: list[str],
        reason: str,
        failed_tool: str,
        *,
        retry_attempted: bool = False,
        retry_history: list[dict[str, Any]] | None = None,
    ) -> IterationResult:
        self._log_agent(
            "task_executor_failed",
            {"iteration": iteration, "failed_tool": failed_tool},
            {"reason": reason},
            success=False,
            error=reason,
        )
        return IterationResult(
            iteration=iteration,
            validation_passed=False,
            completed_successful_iteration=False,
            applied_actions=actions,
            changed_files=[],
            success=False,
            error=reason,
            failure_stage="Task Executor",
            failed_tool=failed_tool,
            failure_reason=reason,
            retry_attempted=retry_attempted,
            retry_history=retry_history or [],
        )

    def _create_git_snapshot(
        self,
        *,
        project_path: Path,
        iteration: int,
        reason: str,
        target_files: list[str],
        stage_key: str = "execution",
    ) -> GitSnapshotMetadata | None:
        try:
            snapshot = GitManagerAgent().snapshot(
                project_path,
                reason=f"iteration_{iteration}_{reason}",
                target_files=target_files,
            )
        except GitManagerError as exc:
            self._log_agent(
                "git_snapshot_unavailable",
                {"iteration": iteration, "project_path": str(project_path), "reason": reason},
                {"error": str(exc)},
                success=False,
                error=str(exc),
            )
            return None
        self.runtime._last_git_snapshot = snapshot.to_json_dict()
        if getattr(self.runtime, "enhanced_ui", None):
            status = "created" if snapshot.created else "skipped"
            suffix = f": {snapshot.commit_hash}" if snapshot.commit_hash else ""
            self.runtime._append_dashboard_stage_child(
                stage_key,
                child_id=f"git_snapshot_{iteration}_{reason}",
                description=f"Safety snapshot {status}{suffix}",
                kind="note",
            )
        return snapshot

    def _git_safety_context(
        self,
        *,
        project_path: Path,
        snapshot: GitSnapshotMetadata | None,
        target_files: list[str],
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "project_path": str(project_path),
            "snapshot_available": bool(snapshot and (snapshot.created or snapshot.skipped) and snapshot.commit_hash),
            "target_files": target_files,
        }
        if snapshot is not None:
            context["snapshot"] = snapshot.to_json_dict()
        try:
            diff_context = GitManagerAgent().diff_context(
                project_path,
                base_ref=snapshot.commit_hash if snapshot and snapshot.commit_hash else "HEAD",
                target_files=target_files,
            )
            context["diff_context"] = diff_context.to_json_dict()
            self.runtime._last_git_diff_context = diff_context.to_json_dict()
        except GitManagerError as exc:
            context["warnings"] = [f"Git diff context unavailable: {exc}"]
        return context

    def _log_agent(
        self,
        event_type: str,
        input_summary: Any,
        output_summary: Any,
        *,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        logger = getattr(self.runtime, "logger", None)
        if not logger or not hasattr(logger, "log_structured_event"):
            return
        logger.log_structured_event(
            source_type="agent",
            source_name="autonomous_iteration.task_executor",
            phase="task_executor",
            event_type=event_type,
            session_id=getattr(self.runtime, "session_id", None) or "unknown",
            turn_id=1,
            success=success,
            input_summary=input_summary,
            output_summary=output_summary,
            error=error,
        )
