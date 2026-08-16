"""Agent for LLM tool planning and tool-call execution."""

from __future__ import annotations

import json
import hashlib
import re
import shlex
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from autonomous_iteration.runtime_controller import ToolRouter, apply_read_only_runtime_mode, is_read_only_analysis_goal
from autonomous_iteration.task_models import Task, TaskExecutionContext, TaskExecutionResult, TaskStatus
from core.config import ProviderToolExecutionBudget, ProviderToolExecutionBudgetProfile
from core.llm import LLMMessage, LLMRequest
from core.provider_tool_roundtrip import ProviderToolRoundTripRunner, build_provider_tool_definitions
from core.reasoning import reasoning_policy_for_decision
from core.tool_event_loop import ToolEventLoopRunner
from memory.context_assembly import build_context_llm_request
from memory.session_constraints import session_constraint_prompt_text
from metadata import (
    AgentPhase,
    ContextRequestPurpose,
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    DecisionNeedMetadata,
    DifficultyAssessmentMetadata,
    FailureMetadata,
    ProblemJudgmentMetadata,
    ProblemSignalMetadata,
    ResultStatus,
    ResolutionPlanMetadata,
    ReasoningDecisionComplexity,
    ReasoningPolicy,
    RuntimeStateMetadata,
    SessionConstraintState,
    SessionIngressState,
    TaskResultMetadata,
    TextArtifactMetadata,
    ToolInputMetadata,
)
from core.tool_contracts import ToolCapability
from tools.mutation_descriptor import FILE_MUTATION_TOOLS


NEED_ATTRIBUTE_FIELDS = {
    "code",
    "content",
    "text",
    "language",
    "cwd",
    "env",
    "timeout",
    "mode",
    "project_path",
    "file_path",
    "file_paths",
    "directory_path",
    "pattern",
    "max_files",
    "recursive",
    "max_total_chars",
    "read_mode",
    "encoding",
    "create_dirs",
    "overwrite",
    "run_command",
    "test_command",
    "task_description",
    "operation_kind",
    "target_scope",
    "symbol_name",
    "symbol_type",
    "insertion_hint",
    "patch_mode",
    "generated_unit",
    "replacement_text",
    "patch",
    "line_start",
    "line_end",
}

DEFAULTABLE_DECISION_NEED_FIELDS = {
    "phase",
    "candidate_paths",
    "attributes",
    "cost_hint",
    "risk_level",
    "target_path",
    "operation_kind",
    "target_scope",
    "symbol_name",
    "symbol_type",
    "insertion_hint",
    "patch_mode",
    "query",
    "command",
    "decision_to_unlock",
    "expected_state_change",
}

ACTIONABLE_FALLBACK_TERMS = {
    "add",
    "build",
    "check",
    "code",
    "create",
    "develop",
    "document",
    "fix",
    "generate",
    "implement",
    "integrate",
    "modify",
    "readme",
    "refactor",
    "repair",
    "run",
    "test",
    "update",
    "validate",
    "verify",
    "write",
    "创建",
    "修复",
    "实现",
    "开发",
    "生成",
    "编写",
    "验证",
}

MUTATING_OR_EXECUTING_NEED_TYPES = {
    "bug_fix",
    "bug_fix_tool",
    "code_execution",
    "code_file_create",
    "code_generation",
    "code_patch",
    "code_symbol_modify",
    "code_unit_generate",
    "command_check",
    "directory_generate",
    "file_delete",
    "delete_file",
    "documentation",
    "file_write",
    "fix_bug",
    "generate_code",
    "generate_code_unit",
    "readme",
    "readme_generation",
    "remove_file",
    "repair",
    "smoke_test",
    "test",
    "verify_command",
    "write_file",
}

FILE_MUTATION_NEED_TYPES = {
    "bug_fix",
    "bug_fix_tool",
    "code_file_create",
    "code_generation",
    "code_patch",
    "code_symbol_modify",
    "code_unit_generate",
    "delete_file",
    "directory_generate",
    "documentation",
    "file_delete",
    "file_write",
    "fix_bug",
    "generate_code",
    "generate_code_unit",
    "modify_symbol",
    "readme",
    "readme_generation",
    "remove_file",
    "repair",
    "write_file",
}

INSPECTION_NEED_TYPES = {
    "file_read",
    "inspect_file",
    "multi_file_read",
    "project_structure",
    "read_directory",
    "read_file",
    "reference_search",
    "research",
    "web_search",
}

_EXECUTION_HISTORY_PROMPT_MAX_CHARS = 900
_EXECUTION_HISTORY_RECENT_STATUS_LIMIT = 5
_EXECUTION_HISTORY_EVIDENCE_PATH_LIMIT = 8


class DecisionNeedValidationError(ValueError):
    """Raised when an LLM decision need cannot be normalized into metadata."""

    def __init__(self, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


class DecisionNeedResolutionError(ValueError):
    """Raised when planning recovery needs task decomposition instead of another empty plan."""

    def __init__(self, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


class ToolPlanningTaskExecutor:
    """Execute one task by asking the LLM for a tool plan and running it."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def guard_preselected_tool_call(
        self,
        task: Task,
        tool_call: Any,
        selection: ToolSelection,
    ) -> Any | None:
        """Apply the standard edit guard to a caller-selected tool invocation."""
        return ToolEventLoopRunner(self)._guard_project_state_change_if_needed(
            task,
            tool_call,
            selection,
        )

    def _reasoning_complexity_for_task(
        self,
        task: Task | None = None,
    ) -> ReasoningDecisionComplexity:
        active_task = task or getattr(self, "_active_task", None)
        routine = False
        if active_task is not None:
            task_kind = str(active_task.kind).strip().lower()
            read_files = list(active_task.read_files or [])
            write_files = list(active_task.write_files or [])
            if task_kind in {
                "inspect",
                "inspection",
                "analysis",
                "investigate",
                "codebase_understanding",
            }:
                routine = bool(read_files) and not write_files
            elif task_kind in {"validate", "validation", "verify", "verification"}:
                routine = bool(str(active_task.validation_command or "").strip())
            elif task_kind in {
                "implement",
                "implementation",
                "modify",
                "edit",
                "write",
            }:
                routine = len(write_files) == 1 and len(read_files) <= 2
                if not routine and (len(write_files) > 1 or len(read_files) > 2):
                    return ReasoningDecisionComplexity.COMPLEX
        return (
            ReasoningDecisionComplexity.ROUTINE
            if routine
            else ReasoningDecisionComplexity.STANDARD
        )

    def _reasoning_policy_for_task(self, task: Task | None = None) -> ReasoningPolicy:
        return reasoning_policy_for_decision(
            getattr(self.runtime.llm_client, "settings", None),
            self._reasoning_complexity_for_task(task),
        )

    @staticmethod
    def _task_support_context_files(task: Task) -> list[str]:
        return [
            str(path).strip()
            for path in getattr(task, "support_context_files", []) or []
            if str(path).strip()
        ]

    @staticmethod
    def _resolved_project_path(raw_path: str, project_path: str) -> Path:
        root = Path(project_path).expanduser().resolve(strict=False)
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = root / path
        return path.resolve(strict=False)

    @staticmethod
    def _path_is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True

    def _support_context_candidates_for_task(
        self,
        task: Task,
        *,
        project_path: str,
        source_order_start: int,
    ) -> tuple[list[ContextCandidate], str | None]:
        support_files = self._task_support_context_files(task)
        if not support_files:
            return [], None
        if not str(project_path or "").strip():
            return [], "Provider support_context_files require a concrete project_path."

        root = Path(project_path).expanduser().resolve(strict=False)
        candidates: list[ContextCandidate] = []
        for index, raw_path in enumerate(support_files, start=1):
            resolved = self._resolved_project_path(raw_path, str(root))
            if not self._path_is_relative_to(resolved, root):
                return [], f"Provider support_context_files path escapes project_path: {raw_path}"
            if not resolved.is_file():
                return [], f"Provider support_context_files path is not an existing file: {raw_path}"
            file_sha256 = "sha256:" + hashlib.sha256(resolved.read_bytes()).hexdigest()
            path_hash = "sha256:" + hashlib.sha256(
                str(resolved).encode("utf-8")
            ).hexdigest()
            candidates.append(
                ContextCandidate(
                    candidate_id=f"provider-support-context:{task.id}:{index}",
                    kind=ContextCandidateKind.RUNTIME_EVIDENCE,
                    source_id=f"task:{task.id}:support_context_files:{index}",
                    content=(
                        f"support_context_file={raw_path}; "
                        "role=read_only_reference_metadata; "
                        "routing=not_required_read_before_write; "
                        "read_authority=false; "
                        "write_allowed=false; "
                        "validation_authority=false; "
                        "payload_omitted=true; "
                        f"resolved_path_sha256={path_hash}; "
                        f"file_sha256={file_sha256}."
                    ),
                    role="system",
                    retention=ContextCandidateRetention.REQUIRED,
                    priority=96,
                    source_order=source_order_start + index - 1,
                    truncation=ContextCandidateTruncation.FORBIDDEN,
                    trust=ContextCandidateTrust.OBSERVED,
                    freshness=ContextCandidateFreshness.CURRENT,
                )
            )
        return candidates, None

    @staticmethod
    def _provider_task_prompt_candidates(
        *,
        task_id: str,
        system_prompt: str,
        user_prompt: str,
    ) -> list[ContextCandidate]:
        return [
            ContextCandidate(
                candidate_id=f"provider-task-system:{task_id}",
                kind=ContextCandidateKind.INSTRUCTION,
                source_id=f"task:{task_id}:provider-system-prompt",
                content=system_prompt,
                role="system",
                retention=ContextCandidateRetention.REQUIRED,
                priority=100,
                source_order=0,
                truncation=ContextCandidateTruncation.FORBIDDEN,
                trust=ContextCandidateTrust.AUTHORITATIVE,
                freshness=ContextCandidateFreshness.CURRENT,
            ),
            ContextCandidate(
                candidate_id=f"provider-task-user:{task_id}",
                kind=ContextCandidateKind.TASK,
                source_id=f"task:{task_id}:provider-user-prompt",
                content=user_prompt,
                role="user",
                retention=ContextCandidateRetention.REQUIRED,
                priority=100,
                source_order=1,
                truncation=ContextCandidateTruncation.FORBIDDEN,
                trust=ContextCandidateTrust.AUTHORITATIVE,
                freshness=ContextCandidateFreshness.CURRENT,
            ),
        ]

    def execute_task(self, task: Task, context: TaskExecutionContext) -> TaskExecutionResult:
        """Execute a single task by generating and executing tool calls."""
        start_time = datetime.now()
        self._log(
            "task_executor_started",
            input_summary={"task_id": task.id, "description": task.description},
            success=None,
        )

        try:
            self._active_task_id = task.id
            self._active_task_description = task.description
            self._active_task = task
            goal = context.parent_context.get("goal", "")
            self._active_goal = goal
            self._active_context = context
            self._empty_plan_retry_attempted = False
            self._reset_subtask_local_no_progress_block(task)
            planning_surface = self._planning_surface_for_prompt(task.description, goal, context=context)
            prompt = self._build_tool_plan_prompt(task.description, goal, planning_surface, context)

            self.runtime.logger.log_event(
                "llm_tool_planning",
                {"task_id": task.id, "task_description": task.description},
                session_id=self._session_id(),
                turn_id=1,
                level="INFO",
            )
            self._log(
                "llm_tool_planning_started",
                input_summary={"task_id": task.id, "goal": goal},
                success=None,
            )

            loop_result = ToolEventLoopRunner(self).run(task, prompt)
            tool_results = loop_result.tool_results
            last_output = loop_result.last_output
            all_tools_succeeded = loop_result.success
            observed_modified_files = self._observed_modified_files(tool_results)
            completion_error = self._completion_evidence_error(
                task,
                tool_results,
                observed_modified_files=observed_modified_files,
            ) if all_tools_succeeded else None
            all_succeeded = all_tools_succeeded and completion_error is None
            output = {
                "task_id": task.id,
                "description": task.description,
                "status": "completed" if all_succeeded else "failed",
                "tool_results": tool_results,
                "tool_loop": loop_result.loop_metadata.to_json_dict(),
                "all_tools_succeeded": all_tools_succeeded,
                "completion_evidence_satisfied": completion_error is None,
                "observed_modified_files": observed_modified_files,
                "final_output": last_output,
            }
            duration = (datetime.now() - start_time).total_seconds()
            tool_error_msg = self._build_tool_error(tool_results)
            error_msg = None if all_succeeded else (completion_error or tool_error_msg or loop_result.error_message)
            failure_details = {"tool_loop": loop_result.loop_metadata.to_json_dict()}
            final_failure = loop_result.loop_metadata.final_error
            if completion_error:
                diagnostics = getattr(self.runtime, "runtime_diagnostics_hooks", None)
                if diagnostics and hasattr(diagnostics, "on_log_event"):
                    diagnostics.on_log_event(
                        task_id=task.id,
                        session_id=self._session_id(),
                        source_type="agent",
                        source_name="autonomous_iteration.agents.tool_planning_executor",
                        phase="task_completion",
                        event_type="task_completion_rejected",
                        success=False,
                        output_summary={
                            "planned_write_files": list(task.write_files),
                            "observed_modified_files": observed_modified_files,
                            "validation_command": task.validation_command,
                        },
                        error=completion_error,
                    )
                final_failure = FailureMetadata(
                    error_type="CompletionEvidenceMissing",
                    error_message=completion_error,
                    recoverable=False,
                    details={
                        "task_kind": task.kind,
                        "planned_write_files": list(task.write_files),
                        "observed_modified_files": observed_modified_files,
                        "validation_command": task.validation_command,
                    },
                )
            if final_failure:
                failure_details.update(final_failure.details or {})

            result = TaskExecutionResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED if all_succeeded else TaskStatus.FAILED,
                result_metadata=TaskResultMetadata(
                    task_id=task.id,
                    status=ResultStatus.SUCCESS if all_succeeded else ResultStatus.FAIL,
                    result=TextArtifactMetadata(content="completed", attributes=output) if all_succeeded else None,
                    failure=FailureMetadata(
                        error_type=final_failure.error_type
                        if final_failure
                        else "ToolExecutionFailed",
                        error_message=error_msg or "Tool execution failed",
                        details=failure_details,
                    )
                    if not all_succeeded
                    else None,
                    duration=duration,
                ),
                error=error_msg,
                duration=duration,
                attributes={
                    "start_time": start_time.isoformat(),
                    "end_time": datetime.now().isoformat(),
                    "tool_count": len(tool_results),
                    "observed_modified_files": observed_modified_files,
                },
            )
            self._log(
                "task_executor_completed",
                output_summary={
                    "task_id": task.id,
                    "tool_count": len(tool_results),
                    "success": all_succeeded,
                },
                success=all_succeeded,
                duration_ms=int(duration * 1000),
            )
            return result

        except Exception as exc:
            duration = (datetime.now() - start_time).total_seconds()
            failure_details = getattr(exc, "details", {}) if isinstance(getattr(exc, "details", {}), dict) else {}
            exc_context = getattr(exc, "context", None)
            if isinstance(exc_context, dict):
                failure_details.update({key: value for key, value in exc_context.items() if value is not None})
            failure_details.setdefault("task_id", task.id)
            failure_details.setdefault("task_description", task.description)
            failure_details.setdefault("failed_tool", "tool_planning_executor")
            failure_details.setdefault("failure_stage", "Tool Planning")
            result = TaskExecutionResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(exc),
                duration=duration,
                result_metadata=TaskResultMetadata(
                    task_id=task.id,
                    status=ResultStatus.FAIL,
                    failure=FailureMetadata(
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        details=failure_details,
                    ),
                    duration=duration,
                ),
                attributes={
                    "start_time": start_time.isoformat(),
                    "end_time": datetime.now().isoformat(),
                },
            )
            self.runtime.logger.log_event(
                "task_failed",
                {
                    "task_id": task.id,
                    "description": task.description,
                    "error": str(exc),
                    "duration": duration,
                    "details": failure_details,
                },
                session_id=self._session_id(),
                turn_id=1,
                level="ERROR",
            )
            self._log(
                "task_executor_failed",
                output_summary={"task_id": task.id},
                success=False,
                error=str(exc),
                duration_ms=int(duration * 1000),
            )
            return result

    def execute_provider_tool_task(
        self,
        task: Task,
        context: TaskExecutionContext,
        *,
        tool_names: list[str],
        user_confirmed: bool = False,
        allow_mutations: bool = False,
        max_rounds: int | None = None,
        initial_context_candidates: list[ContextCandidate] | None = None,
    ) -> TaskExecutionResult:
        """Run an explicitly enabled provider-native task entry point.

        The existing JSON planner remains the default. This method is the
        guarded route for a real provider canary: the settings flag must be
        enabled, the caller must provide an explicit tool allowlist, and
        mutation tools require both an explicit code-level opt-in and user
        confirmation.
        """
        started_at = datetime.now()
        settings = getattr(getattr(self.runtime, "llm_client", None), "settings", None)
        enabled = bool(getattr(settings, "provider_tool_execution_enabled", False))
        try:
            budget_profile = ProviderToolExecutionBudget.for_profile(
                getattr(
                    settings,
                    "provider_tool_execution_budget_profile",
                    ProviderToolExecutionBudgetProfile.CANARY,
                )
            )
        except (TypeError, ValueError) as exc:
            budget_profile = None
            budget_profile_error = str(exc)
        else:
            budget_profile_error = None

        def failure_result(error_type: str, message: str, *, details: dict[str, Any] | None = None) -> TaskExecutionResult:
            duration = (datetime.now() - started_at).total_seconds()
            failure = FailureMetadata(
                error_type=error_type,
                error_message=message,
                recoverable=False,
                details={"task_id": task.id, **(details or {})},
            )
            return TaskExecutionResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=message,
                duration=duration,
                result_metadata=TaskResultMetadata(
                    task_id=task.id,
                    status=ResultStatus.FAIL,
                    failure=failure,
                    duration=duration,
                ),
                attributes={"provider_tool_execution": True, "provider_tool_execution_enabled": enabled},
            )

        if not enabled:
            return failure_result(
                "ProviderToolExecutionDisabled",
                "Provider-native task execution is disabled; enable the explicit canary flag first.",
            )
        if budget_profile is None:
            return failure_result(
                "ProviderToolBudgetProfileInvalid",
                f"Invalid provider-native budget profile: {budget_profile_error}",
            )
        if budget_profile.profile is ProviderToolExecutionBudgetProfile.REAL_READ_ONLY and allow_mutations:
            return failure_result(
                "ProviderToolBudgetProfileMutationConflict",
                "The real_read_only provider budget profile cannot be used for mutation tasks.",
            )
        if allow_mutations and budget_profile.profile is not ProviderToolExecutionBudgetProfile.REAL_MUTATION:
            return failure_result(
                "ProviderToolBudgetProfileMutationRequired",
                "Provider-native mutation tasks require the explicit real_mutation budget profile.",
            )
        configured_rounds = int(getattr(settings, "provider_tool_execution_max_rounds", 3) or 3)
        if max_rounds is not None:
            if isinstance(max_rounds, bool) or not isinstance(max_rounds, int):
                return failure_result(
                    "ProviderToolMaxRoundsInvalid",
                    "Provider-native max_rounds must be an integer within the selected budget profile.",
                )
            requested_rounds = max_rounds
            if requested_rounds < 1 or requested_rounds > budget_profile.max_rounds:
                return failure_result(
                    "ProviderToolMaxRoundsExceedsBudget",
                    f"max_rounds must be between 1 and {budget_profile.max_rounds} for the selected budget profile.",
                    details={"requested_max_rounds": requested_rounds, "profile_max_rounds": budget_profile.max_rounds},
                )
            effective_rounds = requested_rounds
        else:
            effective_rounds = min(max(1, configured_rounds), budget_profile.max_rounds)
        normalized_tools = [str(name or "").strip() for name in tool_names if str(name or "").strip()]
        if not normalized_tools:
            return failure_result("ProviderToolAllowlistRequired", "Provider-native tasks require a non-empty tool allowlist.")

        projection_flag = getattr(
            settings,
            "provider_tool_initial_context_projection_enabled",
            None,
        )
        mutation_projection_flag = bool(
            getattr(settings, "provider_tool_initial_context_mutation_enabled", False)
        )
        task_support_context_files = self._task_support_context_files(task)
        projected_context_requested = bool(initial_context_candidates) or bool(task_support_context_files)
        if projected_context_requested:
            if allow_mutations and not mutation_projection_flag:
                return failure_result(
                    "ProviderMutationInitialContextProjectionDisabled",
                    "Mutation tasks require the separate explicit mutation projection flag.",
                )
            if not allow_mutations and projection_flag is False:
                return failure_result(
                    "ProviderInitialContextProjectionDisabled",
                    "Typed initial-context candidates require the explicit read-only projection flag.",
                )

        registry = getattr(self.runtime, "tool_registry", None)
        if registry is None:
            return failure_result("ProviderToolRegistryMissing", "Provider-native task execution requires a tool registry.")
        mutation_tools = []
        for tool_name in normalized_tools:
            definition = registry.get(tool_name) if hasattr(registry, "get") else None
            capabilities = set(getattr(definition, "capabilities", []) or []) if definition is not None else set()
            if tool_name in FILE_MUTATION_TOOLS or ToolCapability.FILE_WRITE in capabilities or ToolCapability.FILE_DELETE in capabilities:
                mutation_tools.append(tool_name)
        if mutation_tools and (not allow_mutations or not user_confirmed):
            return failure_result(
                "ProviderMutationConfirmationRequired",
                "Provider-native mutation tasks require explicit allow_mutations and user_confirmed=True.",
                details={"mutation_tools": mutation_tools},
            )

        try:
            tools = build_provider_tool_definitions(registry, normalized_tools)
            goal = str(getattr(context, "parent_context", {}).get("goal", "") or "")
            project_path = str(
                getattr(context, "parent_context", {}).get("project_path", "")
                or getattr(context, "shared_state", {}).get("project_path", "")
                or ""
            )
            read_scope = list(task.read_files or []) if task.read_files else None
            write_scope = list(task.write_files or []) if allow_mutations else None
            if allow_mutations and (not read_scope or not write_scope):
                return failure_result(
                    "ProviderTaskScopeMissing",
                    "Provider-native mutation tasks require explicit non-empty read_files and write_files scopes.",
                )
            if allow_mutations and str(getattr(task, "kind", "") or "").lower() not in {"implement", "repair", "modify", "edit", "write"}:
                return failure_result(
                    "ProviderMutationTaskKindInvalid",
                    "Provider-native mutation tasks require an implementation task kind.",
                )
            constraint_state = self._session_constraints_from_context(context)
            constraint_text = session_constraint_prompt_text(constraint_state) if constraint_state else ""
            controller = getattr(self.runtime, "runtime_controller", None)
            if controller is None:
                return failure_result("ProviderRuntimeControllerMissing", "Provider-native task execution requires a runtime controller.")
            if getattr(controller, "state", None) is None:
                controller.state = RuntimeStateMetadata(goal=task.description)
            budget_updates = {
                "tool_event_completion_outcome_feedback_enabled": bool(
                    getattr(
                        settings,
                        "provider_tool_completion_outcome_feedback_enabled",
                        False,
                    )
                ),
            }
            if budget_profile.profile in {
                ProviderToolExecutionBudgetProfile.REAL_READ_ONLY,
                ProviderToolExecutionBudgetProfile.REAL_MUTATION,
            }:
                budget_updates.update(
                    {
                        "max_tool_calls": budget_profile.max_tool_calls,
                        "max_file_reads": budget_profile.max_file_reads,
                        "max_file_edits": budget_profile.max_file_edits,
                        "max_file_creates": budget_profile.max_file_creates,
                        "max_verification_attempts": budget_profile.max_verification_attempts,
                        "max_tool_event_completion_tokens": budget_profile.total_completion_tokens,
                        "tool_event_completion_ceiling": budget_profile.completion_ceiling,
                        "tool_event_completion_floor": budget_profile.completion_floor,
                    }
                )
            controller.state.budget = controller.state.budget.model_copy(update=budget_updates)
            if constraint_state is not None:
                controller.state.session_constraints = constraint_state.model_copy(deep=True)
            apply_read_only_runtime_mode(
                controller.state,
                task.description,
                tags=list(getattr(task, "tags", []) or []),
                task_type=str(getattr(task, "kind", "") or ""),
            )
            if hasattr(controller, "_active_task_id"):
                controller._active_task_id = task.id
            system_prompt = (
                "You are executing a bounded project task through typed tools. "
                "Use the available tools when evidence is needed, obey all constraints, "
                "and finish with a concise result. Do not invent paths or claim a tool succeeded "
                "without its returned evidence."
            )
            if allow_mutations:
                system_prompt += (
                    " For this mutation task, use file_reader for inspection only; do not use "
                    "command_executor for reading. command_executor is reserved for the exact "
                    "typed validation_command after the scoped mutation. Follow this bounded "
                    "workflow exactly: read each authorized source once, then issue one scoped "
                    "file_writer or file_patch_writer call for the authorized target, then run "
                    "the exact validation command. A file_reader result is complete when "
                    "evidence_status=complete and projection_status=inline or "
                    "projection_status=bounded_window; "
                    "for bounded_window, trust the exact declared read_window and must not reread "
                    "that path. Treat bounded_preview as partial only. Do not repeat a completed read."
                    " For command_executor, pass the validation command exactly as declared; provide "
                    "cwd as a separate field and never add cd, shell chaining, pipes, redirection, "
                    "or substitutions."
                )
            user_prompt = f"Task: {task.description}\nOverall goal: {goal}\nProject path: {project_path}"
            if read_scope:
                user_prompt = (
                    f"{user_prompt}\nExplicit read_files scope (authoritative; do not read outside): "
                    f"{json.dumps(read_scope, ensure_ascii=False)}"
                )
            if constraint_text:
                user_prompt = f"{user_prompt}\n{constraint_text}"
            effective_initial_context_candidates = initial_context_candidates
            support_context_candidate_count = 0
            if task_support_context_files:
                support_context_candidates, support_context_error = self._support_context_candidates_for_task(
                    task,
                    project_path=project_path,
                    source_order_start=2,
                )
                if support_context_error:
                    return failure_result(
                        "ProviderSupportContextInvalid",
                        support_context_error,
                        details={"support_context_files": list(task_support_context_files)},
                    )
                support_context_candidate_count = len(support_context_candidates)
                effective_initial_context_candidates = [
                    *self._provider_task_prompt_candidates(
                        task_id=task.id,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                    ),
                    *support_context_candidates,
                    *(initial_context_candidates or []),
                ]
            roundtrip = ProviderToolRoundTripRunner(
                self,
                task,
                tools=tools,
                max_rounds=effective_rounds,
                user_confirmed=user_confirmed,
                allow_mutations=allow_mutations,
                max_tokens=budget_profile.completion_ceiling,
                read_scope=read_scope,
                write_scope=write_scope,
                project_path=project_path,
                validation_command=(task.validation_command if allow_mutations else None),
                validation_cwd=project_path,
                context_max_prompt_tokens=budget_profile.context_max_prompt_tokens,
                initial_context_candidates=effective_initial_context_candidates,
                bounded_read_windows=list(getattr(task, "read_windows", []) or []),
            ).run(
                [
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=user_prompt),
                ]
            )
        except Exception as exc:
            return failure_result("ProviderToolTaskSetupFailed", str(exc))

        duration = (datetime.now() - started_at).total_seconds()
        loop_payload = [loop.loop_metadata.to_json_dict() for loop in roundtrip.tool_loop_results]
        feedback_enabled = bool(
            getattr(
                roundtrip,
                "outcome_feedback_enabled",
                controller.state.budget.tool_event_completion_outcome_feedback_enabled,
            )
        )
        reasoning_complexity = getattr(
            roundtrip,
            "reasoning_complexity",
            self._reasoning_complexity_for_task(task),
        )
        reasoning_mode = getattr(roundtrip, "reasoning_mode", None)
        output = {
            "provider_tool_execution": True,
            "rounds_used": roundtrip.rounds_used,
            "tool_loops": loop_payload,
            "final_response": roundtrip.final_response.content if roundtrip.final_response else "",
            "evidence_coverage": roundtrip.evidence_coverage.to_json_dict(),
            "request_diagnostics": [
                dict(item) for item in getattr(roundtrip, "request_diagnostics", ())
            ],
            "budget_diagnostics": [
                dict(item) for item in getattr(roundtrip, "budget_diagnostics", ())
            ],
            "handoff_diagnostics": [
                dict(item) for item in getattr(roundtrip, "handoff_diagnostics", ())
            ],
            "provider": roundtrip.final_response.provider if roundtrip.final_response else "",
            "model": roundtrip.final_response.model if roundtrip.final_response else "",
            "budget_profile": budget_profile.profile.value,
            "execution_mode": "real_mutation" if allow_mutations else "real_read_only",
            "allow_mutations": bool(allow_mutations),
            "user_confirmed": bool(user_confirmed),
            "support_context_files": list(task_support_context_files),
            "support_context_candidate_count": support_context_candidate_count,
            "outcome_feedback_enabled": feedback_enabled,
            "reasoning_complexity": (
                reasoning_complexity.value
                if hasattr(reasoning_complexity, "value")
                else str(reasoning_complexity)
            ),
            "reasoning_mode": (
                reasoning_mode.value
                if hasattr(reasoning_mode, "value")
                else None
            ),
            "requested_max_rounds": max_rounds,
            "effective_max_rounds": effective_rounds,
            "budget_limits": {
                "context_max_prompt_tokens": budget_profile.context_max_prompt_tokens,
                "completion_ceiling": budget_profile.completion_ceiling,
                "outcome_feedback_enabled": feedback_enabled,
                "total_completion_tokens": budget_profile.total_completion_tokens,
                "max_rounds": budget_profile.max_rounds,
                "max_tool_calls": budget_profile.max_tool_calls,
                "max_file_reads": budget_profile.max_file_reads,
                "max_file_edits": budget_profile.max_file_edits,
                "max_file_creates": budget_profile.max_file_creates,
                "max_verification_attempts": budget_profile.max_verification_attempts,
            },
            "attempts": [
                {
                    "signature": attempt.signature,
                    "tool_name": attempt.tool_name,
                    "provider_call_id": attempt.provider_call_id,
                    "round_index": attempt.round_index,
                    "success": attempt.success,
                    "error_type": attempt.error_type,
                    "duplicate_of": attempt.duplicate_of,
                }
                for attempt in roundtrip.attempts
            ],
        }
        output["budget_contract_sha256"] = "sha256:" + hashlib.sha256(
            json.dumps(output["budget_limits"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return self._build_provider_task_result(
            task,
            roundtrip,
            duration,
            loop_payload,
            output,
        )

    @staticmethod
    def _build_provider_task_result(
        task: Task,
        roundtrip: Any,
        duration: float,
        loop_payload: list[dict[str, Any]],
        output: dict[str, Any],
    ) -> TaskExecutionResult:
        """Build a typed result from already-computed provider round-trip evidence."""
        success = bool(getattr(roundtrip, "success", False))
        if not success:
            error_message = str(getattr(roundtrip, "error_message", "") or "Provider-native task failed.")
            coverage = getattr(roundtrip, "evidence_coverage", None)
            if hasattr(coverage, "to_json_dict"):
                coverage = coverage.to_json_dict()
            elif isinstance(coverage, Mapping):
                coverage = dict(coverage)
            else:
                coverage = {}
            failure = FailureMetadata(
                error_type="ProviderToolTaskFailed",
                error_message=error_message,
                recoverable=False,
                details={
                    "rounds_used": int(getattr(roundtrip, "rounds_used", 0) or 0),
                    "tool_loops": loop_payload,
                    "provider_stop_reason": (
                        getattr(getattr(roundtrip, "final_response", None), "finish_reason", None)
                    ),
                    "runner_error": error_message,
                    "evidence_coverage": coverage,
                },
            )
            return TaskExecutionResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=error_message,
                duration=duration,
                result_metadata=TaskResultMetadata(
                    task_id=task.id,
                    status=ResultStatus.FAIL,
                    failure=failure,
                    duration=duration,
                ),
                attributes=dict(output),
            )
        final_response = getattr(roundtrip, "final_response", None)
        final_text = str(getattr(final_response, "content", "") or "")
        return TaskExecutionResult(
            task_id=task.id,
            status=TaskStatus.COMPLETED,
            duration=duration,
            result_metadata=TaskResultMetadata(
                task_id=task.id,
                status=ResultStatus.SUCCESS,
                result=TextArtifactMetadata(content=final_text, attributes=dict(output)),
                duration=duration,
            ),
            result_summary=final_text[:500],
            attributes=dict(output),
        )

    def _build_tool_plan_prompt(
        self,
        task_description: str,
        goal: str,
        planning_surface: str,
        context: TaskExecutionContext | None = None,
    ) -> str:
        history = self._execution_history_summary(context)
        project_context = self._project_context_summary(context)
        project_section = f"Current Project Context:\n{project_context}\n" if project_context else ""
        constraint_state = self._session_constraints_from_context(context)
        constraint_prompt = session_constraint_prompt_text(constraint_state) if constraint_state else ""
        constraint_section = f"{constraint_prompt}\n" if constraint_prompt else ""
        read_only_notice = self._read_only_notice(task_description, goal, context)
        read_only_section = f"{read_only_notice}\n" if read_only_notice else ""
        return f"""You are an AI assistant that plans decision_needs for tasks.
Do not choose tools. The runtime ToolRouter maps decision_needs to concrete tools under budget, path, risk, and permission checks.

Task: {task_description}
Overall Goal: {goal}
{constraint_section}{project_section}Previous Task Results:
{history}
{read_only_section}

Planning Surface:
{planning_surface}

Output ONLY valid JSON in this format:
{{
  "decision_needs": [
    {{
      "need_type": "code_file_create",
      "question": "create the main project file",
      "target_path": "/absolute/path/to/file.py",
      "operation_kind": "create_file",
      "attributes": {{"language": "python"}}
    }}
  ]
}}

Allowed need_type values:
file_read, project_structure, web_search, command_check, file_write, file_delete, code_file_create,
directory_generate, code_unit_generate, code_symbol_modify, code_patch, code_generation,
code_execution, readme_generation, bug_fix, repair.

Optional fields may include: target_path, operation_kind, target_scope, symbol_name,
symbol_type, insertion_hint, patch_mode, candidate_paths, query, command, risk_level,
attributes. Omit unknown or unavailable optional fields. Do not emit null.

Important:
- Use latest_change and evidence_paths in Previous Task Results; never invent plan files.
- Never invent or read intermediate files such as subtask_0.md, subtask_1.md, requirements.md, or plan.md unless previous results or the user explicitly mention them.
- When Current Project Context gives a project root, prefer paths already observed in Previous Task Results or directory-sketch evidence. Do not invent nested directories or filenames under that root without evidence.
- Prefer evidence before mutation: inspect files/directories first, then mutate with concrete target paths.
- For new code files, use code_file_create or directory_generate, then file_write with operation_kind create_file.
- For existing-file additions, use file_read, then code_unit_generate with add_symbol, then file_write with add_symbol semantics.
- For existing-file edits, use file_read, then code_symbol_modify or code_patch with modify_symbol, then file_write with modify_symbol semantics.
- For deletion, gather evidence first, then use file_delete with operation_kind delete_file.
- Code-generation needs only support executable code languages: python, shell, bash. Never plan language "text".
- For docs-only delivery, use readme_generation or text/file writing needs instead of code generation.
- After completed code/project delivery, emit readme_generation for run instructions when a README is still needed.
- Provide values only. Do not emit null, placeholders, or tool_calls.
- If a later need depends on generated content, assume the first need produces the content directly for routing.
- For command-style validation, keep mode compatible with automatic/dry_run/interactive semantics and never plan source/activate/cd/export wrappers.
"""

    @staticmethod
    def _session_constraints_from_context(
        context: TaskExecutionContext | None,
    ) -> SessionConstraintState | None:
        if context is None:
            return None
        states: list[SessionConstraintState] = []
        for container in (
            getattr(context, "parent_context", {}),
            getattr(context, "shared_state", {}),
        ):
            if not isinstance(container, dict):
                continue
            if "session_constraints" in container:
                state = container.get("session_constraints")
                if not isinstance(state, SessionConstraintState):
                    raise TypeError("session_constraints must be a validated SessionConstraintState")
                ingress = container.get("session_ingress_state")
                if ingress is not None and not isinstance(ingress, SessionIngressState):
                    raise TypeError("session_ingress_state must be a validated SessionIngressState")
                if isinstance(ingress, SessionIngressState) and ingress.session_constraints != state:
                    raise ValueError("session ingress and constraint state differ")
                states.append(state)
                continue
            ingress = container.get("session_ingress_state")
            if ingress is not None and not isinstance(ingress, SessionIngressState):
                raise TypeError("session_ingress_state must be a validated SessionIngressState")
            if isinstance(ingress, SessionIngressState):
                states.append(ingress.session_constraints)
        if not states:
            return None
        if len({state.canonical_hash for state in states}) != 1:
            raise ValueError("conflicting session constraint states in task context")
        return states[0]

    def _planning_surface_for_prompt(
        self,
        task_description: str,
        goal: str,
        *,
        context: TaskExecutionContext | None = None,
        retry_reason: str = "",
        signal: ProblemSignalMetadata | None = None,
        plan_data: dict[str, Any] | None = None,
    ) -> str:
        history = self._execution_history_summary(context)
        formatter = getattr(self.runtime, "_format_planning_surface", None)
        tools = self.runtime.tool_registry.list_all() if getattr(self.runtime, "tool_registry", None) else []
        if callable(formatter):
            return formatter(
                tools,
                task_description=task_description,
                goal=goal,
                history_text=history,
                retry_reason=retry_reason,
                signal=signal,
                plan_data=plan_data,
                capability_card_providers=list(getattr(self.runtime, "planning_surface_providers", []) or []),
            )
        tool_io = getattr(self.runtime, "tool_io", None)
        if tool_io and hasattr(tool_io, "format_planning_surface"):
            return tool_io.format_planning_surface(
                tools,
                task_description=task_description,
                goal=goal,
                history_text=history,
                retry_reason=retry_reason,
                signal=signal,
                plan_data=plan_data,
                capability_card_providers=list(getattr(self.runtime, "planning_surface_providers", []) or []),
            )
        tools_description = self.runtime._format_tools_for_llm(tools)
        return f"Legacy planning surface fallback:\n{tools_description}"

    def _execution_history_summary(self, context: TaskExecutionContext | None) -> str:
        if context is None:
            return "No previous task results."
        history = context.execution_history or context.shared_state.get("previous_task_results") or []
        if not history:
            return "No previous task results."
        normalized = [item if isinstance(item, dict) else {"result_summary": str(item)} for item in history]
        status_counts: dict[str, int] = {}
        for item in normalized:
            status = str(item.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1

        latest = normalized[-1]
        latest_change = {
            "task_id": self._bounded_history_text(latest.get("task_id"), 100),
            "description": self._bounded_history_text(latest.get("description"), 180),
            "status": self._bounded_history_text(latest.get("status"), 40),
            "failure_type": self._bounded_history_text(latest.get("failure_type"), 100),
            "error_message": self._bounded_history_text(latest.get("error"), 260),
            "result_summary": self._bounded_history_text(latest.get("result_summary"), 320),
        }
        latest_change = {key: value for key, value in latest_change.items() if value}
        view: dict[str, Any] = {
            "status_counts": status_counts,
            "recent_status": [
                {
                    "task_id": self._bounded_history_text(item.get("task_id"), 100),
                    "status": self._bounded_history_text(item.get("status"), 40),
                }
                for item in normalized[-_EXECUTION_HISTORY_RECENT_STATUS_LIMIT:]
            ],
            "latest_change": latest_change,
            "evidence_paths": self._recent_history_evidence_paths(normalized),
        }
        return self._fit_execution_history_view(view)

    def _fit_execution_history_view(self, view: dict[str, Any]) -> str:
        def render() -> str:
            return json.dumps(view, ensure_ascii=False, separators=(",", ":"))

        while len(render()) > _EXECUTION_HISTORY_PROMPT_MAX_CHARS and len(view["evidence_paths"]) > 1:
            view["evidence_paths"].pop()
        while len(render()) > _EXECUTION_HISTORY_PROMPT_MAX_CHARS and len(view["recent_status"]) > 1:
            view["recent_status"].pop(0)
        for key, minimum in (("result_summary", 80), ("error_message", 80), ("description", 80)):
            while len(render()) > _EXECUTION_HISTORY_PROMPT_MAX_CHARS and key in view["latest_change"]:
                value = str(view["latest_change"][key])
                if len(value) <= minimum:
                    view["latest_change"].pop(key)
                    break
                view["latest_change"][key] = self._bounded_history_text(value, max(minimum, len(value) // 2))
        while len(render()) > _EXECUTION_HISTORY_PROMPT_MAX_CHARS and view["evidence_paths"]:
            view["evidence_paths"].pop()
        return render()

    def _recent_history_evidence_paths(self, history: list[dict[str, Any]]) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        for item in reversed(history):
            observed = item.get("observed_paths") or []
            if not isinstance(observed, list):
                continue
            for raw_path in reversed(observed):
                path = self._bounded_history_text(raw_path, 260)
                if not path or path in seen:
                    continue
                seen.add(path)
                paths.append(path)
                if len(paths) >= _EXECUTION_HISTORY_EVIDENCE_PATH_LIMIT:
                    return paths
        return paths

    def _bounded_history_text(self, value: Any, max_chars: int) -> str:
        if value in (None, "", [], {}):
            return ""
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        else:
            text = str(value)
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 1)].rstrip() + "…"

    def _project_context_summary(self, context: TaskExecutionContext | None) -> str:
        defaults = self._context_default_attributes(context)
        project_path = str(defaults.get("project_path") or "").strip()
        cwd = str(defaults.get("cwd") or "").strip()
        lines: list[str] = []
        if project_path:
            lines.append(f"- Project root: {project_path}")
        if cwd and cwd != project_path:
            lines.append(f"- Working directory: {cwd}")
        if not lines:
            return ""
        return "\n".join(lines)

    def _parse_decision_needs(self, llm_response: Any) -> list[dict[str, Any]]:
        try:
            plan_data = (
                llm_response.parsed_json
                if isinstance(getattr(llm_response, "parsed_json", None), dict)
                else json.loads(llm_response.content)
            )
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse LLM response as JSON: {exc}") from exc
        tool_requests = self._route_decision_needs(plan_data)
        if not tool_requests:
            raw_needs = plan_data.get("decision_needs", []) if isinstance(plan_data, dict) else []
            active_task = getattr(self, "_active_task", None)
            task_kind = str(getattr(active_task, "kind", "") or "").strip().lower()
            contract_filtered_kinds = {
                "inspect",
                "inspection",
                "analysis",
                "investigate",
                "codebase_understanding",
                "validate",
                "validation",
                "verify",
                "verification",
                "implement",
                "implementation",
                "modify",
                "edit",
                "write",
            }
            if (
                isinstance(raw_needs, list)
                and raw_needs
                and task_kind in contract_filtered_kinds
            ):
                validation_command = str(
                    getattr(active_task, "validation_command", "") or ""
                ).strip()
                reason = "Model-proposed decision needs exceeded the current subtask contract."
                if task_kind in {"validate", "validation", "verify", "verification"}:
                    proposed_types = {
                        str(item.get("need_type") or "").lower().replace("-", "_")
                        for item in raw_needs
                        if isinstance(item, dict)
                    }
                    if proposed_types & FILE_MUTATION_NEED_TYPES:
                        reason = "Subtask write scope forbids mutation for validation tasks."
                    elif not validation_command:
                        reason = "Validation subtask is missing its required validation_command."
                    elif "command_check" in proposed_types:
                        reason = "Model did not preserve the required validation command."
                    else:
                        reason = "No observed validation command matched the task contract."
                raise DecisionNeedResolutionError(
                    reason,
                    {
                        "failed_tool": "tool_planning_executor",
                        "failure_stage": "Tool Planning",
                        "task_kind": task_kind,
                        "dropped_decision_need_count": len(raw_needs),
                    },
                )
            if self._empty_decision_needs_can_synthesize(plan_data):
                return []
            fallback_requests = self._fallback_tool_requests(plan_data=plan_data)
            if fallback_requests:
                return fallback_requests
            signal = self._problem_signal_for_empty_plan(plan_data)
            judgment = self._judge_problem(signal)
            difficulty = self._assess_problem_difficulty(signal, judgment)
            resolution = self._resolution_plan_for_problem(signal, judgment, difficulty)
            self._log_problem_resolution(signal, judgment, difficulty, resolution)
            retry_requests = self._retry_empty_decision_plan(
                plan_data=plan_data,
                signal=signal,
                judgment=judgment,
                difficulty=difficulty,
                resolution=resolution,
            )
            if retry_requests:
                return retry_requests
            self._log(
                "decision_need_empty_plan",
                input_summary={
                    "task_id": getattr(self, "_active_task_id", "unknown"),
                    "decision_need_count": len(plan_data.get("decision_needs", [])) if isinstance(plan_data, dict) else 0,
                },
                success=False,
                error="Tool planning requires decomposition after empty decision_needs plan",
                level="ERROR",
            )
            raise DecisionNeedResolutionError(
                "Tool planning requires decomposition after empty decision_needs plan",
                {
                    "failed_tool": "tool_planning_executor",
                    "failure_stage": "Tool Planning",
                    "problem_signal": signal.to_json_dict(),
                    "problem_judgment": judgment.to_json_dict(),
                    "difficulty_assessment": difficulty.to_json_dict(),
                    "resolution_plan": resolution.to_json_dict(),
                },
            )
        return tool_requests

    def _fallback_tool_requests(
        self,
        *,
        plan_data: dict[str, Any] | None = None,
        reason: str = "",
    ) -> list[dict[str, Any]]:
        fallback_plan = self._fallback_decision_plan(plan_data or {"decision_needs": []})
        if fallback_plan is None:
            return []
        fallback_requests = self._route_decision_needs(fallback_plan)
        if not fallback_requests:
            return []
        self._log(
            "decision_need_fallback_plan",
            input_summary={
                "task_id": getattr(self, "_active_task_id", "unknown"),
                "decision_need_count": len((plan_data or {}).get("decision_needs", [])),
                "reason": reason,
            },
            output_summary={
                "fallback_need_count": len(fallback_plan.get("decision_needs", [])),
                "tool_request_count": len(fallback_requests),
            },
            success=True,
            level="WARNING",
        )
        return fallback_requests

    def _problem_signal_for_empty_plan(self, plan_data: dict[str, Any]) -> ProblemSignalMetadata:
        task_description = str(getattr(self, "_active_task_description", "") or "")
        goal = str(getattr(self, "_active_goal", "") or "")
        raw_needs = plan_data.get("decision_needs", []) if isinstance(plan_data, dict) else []
        category = "planning_gap" if raw_needs == [] else "tool_contract"
        evidence = [
            f"task:{task_description[:500]}",
            f"goal:{goal[:500]}",
            f"decision_needs_count:{len(raw_needs) if isinstance(raw_needs, list) else 'invalid'}",
        ]
        if isinstance(plan_data, dict) and "tool_calls" in plan_data:
            evidence.append("old_tool_calls_protocol_present")
            category = "tool_contract"
        return ProblemSignalMetadata(
            signal_source="tool_planning",
            category=category,
            message="LLM returned no routable decision_needs for an actionable task.",
            evidence=evidence,
            task_id=str(getattr(self, "_active_task_id", "") or ""),
            tool_name="tool_planning_executor",
            target_files=[str(path) for path in [self._infer_target_file(task_description, goal, None)] if path is not None],
            raw_payload=plan_data if isinstance(plan_data, dict) else {"raw": str(plan_data)},
        )

    def _judge_problem(self, signal: ProblemSignalMetadata) -> ProblemJudgmentMetadata:
        user_visible = self._looks_actionable(str(getattr(self, "_active_task_description", "") or ""))
        return ProblemJudgmentMetadata(
            is_problem=True,
            severity="blocking" if user_visible else "warning",
            requires_fix=True,
            user_visible=user_visible,
            recommended_repair_kind="recover_tool_plan" if signal.category == "planning_gap" else "repair_tool_protocol",
            confidence=0.86 if user_visible else 0.68,
            reason="A task cannot execute without at least one routable decision_need.",
        )

    def _assess_problem_difficulty(
        self,
        signal: ProblemSignalMetadata,
        judgment: ProblemJudgmentMetadata,
    ) -> DifficultyAssessmentMetadata:
        task_description = str(getattr(self, "_active_task_description", "") or "")
        goal = str(getattr(self, "_active_goal", "") or "")
        factors: list[str] = []
        level = "simple"
        recommended_task_count = 1
        if self._looks_like_validation_task(task_description):
            factors.append("validation task can use deterministic command fallback")
            level = "simple"
        elif signal.target_files:
            factors.append("target file is known")
            level = "simple"
        else:
            level = "moderate"
            recommended_task_count = 2
            factors.append("no target file or project path was confidently inferred")
        if any(term in f"{task_description}\n{goal}".lower() for term in ("frontend", "backend", "multi-file", "architecture", "全栈", "前后端")):
            level = "hard"
            recommended_task_count = 3
            factors.append("multi-surface or architecture-level wording")
        needs_decomposition = level in {"moderate", "hard"} and not self._looks_like_validation_task(task_description)
        return DifficultyAssessmentMetadata(
            level=level,
            needs_decomposition=needs_decomposition,
            blocking_factors=factors,
            recommended_task_count=recommended_task_count,
        )

    def _resolution_plan_for_problem(
        self,
        signal: ProblemSignalMetadata,
        judgment: ProblemJudgmentMetadata,
        difficulty: DifficultyAssessmentMetadata,
    ) -> ResolutionPlanMetadata:
        task_description = str(getattr(self, "_active_task_description", "") or "")
        if self._looks_like_validation_task(task_description) and signal.category == "planning_gap":
            strategy = "deterministic_fallback"
            acceptance_check = "A command_executor validation command is produced."
        elif difficulty.needs_decomposition:
            strategy = "decompose"
            acceptance_check = "A smaller task graph is produced for inspection, repair, and validation."
        else:
            strategy = "direct_retry"
            acceptance_check = "A retry produces at least one routable decision_need."
        return ResolutionPlanMetadata(
            strategy=strategy,
            target_tasks=[signal.task_id] if signal.task_id else [],
            max_attempts=2,
            acceptance_check=acceptance_check,
        )

    def _log_problem_resolution(
        self,
        signal: ProblemSignalMetadata,
        judgment: ProblemJudgmentMetadata,
        difficulty: DifficultyAssessmentMetadata,
        resolution: ResolutionPlanMetadata,
    ) -> None:
        self._log(
            "problem_resolution_planned",
            input_summary={"signal": signal.to_json_dict()},
            output_summary={
                "judgment": judgment.to_json_dict(),
                "difficulty": difficulty.to_json_dict(),
                "resolution": resolution.to_json_dict(),
            },
            success=True,
            level="WARNING",
        )

    def _retry_empty_decision_plan(
        self,
        *,
        plan_data: dict[str, Any],
        signal: ProblemSignalMetadata,
        judgment: ProblemJudgmentMetadata,
        difficulty: DifficultyAssessmentMetadata,
        resolution: ResolutionPlanMetadata,
    ) -> list[dict[str, Any]]:
        del judgment
        if resolution.strategy == "deterministic_fallback":
            return []
        if getattr(self, "_empty_plan_retry_attempted", False):
            return []
        self._empty_plan_retry_attempted = True
        prompt = self._empty_plan_retry_prompt(plan_data, signal, difficulty, resolution)
        try:
            response = self.runtime.llm_client.complete(
                build_context_llm_request(
                    self.runtime.llm_client,
                    purpose=ContextRequestPurpose.TOOL_PLAN_RETRY,
                    messages=[LLMMessage(role="user", content=prompt)],
                    response_format="json_object",
                    temperature=0.0,
                    max_tokens=2000,
                    timeout_seconds=30.0,
                    transport_retries=0,
                    reasoning_policy=self._reasoning_policy_for_task(),
                )
            )
            retry_data = (
                response.parsed_json
                if isinstance(getattr(response, "parsed_json", None), dict)
                else json.loads(str(getattr(response, "content", "")))
            )
        except Exception as exc:
            self._log(
                "decision_need_empty_plan_retry_failed",
                input_summary={"task_id": signal.task_id, "reason": str(exc)},
                success=False,
                error=str(exc),
                level="WARNING",
            )
            return []
        try:
            requests = self._route_decision_needs(retry_data)
        except Exception as exc:
            self._log(
                "decision_need_empty_plan_retry_unroutable",
                input_summary={"task_id": signal.task_id, "reason": str(exc)},
                success=False,
                error=str(exc),
                level="WARNING",
            )
            return []
        if requests:
            self._log(
                "decision_need_empty_plan_retry_recovered",
                input_summary={"task_id": signal.task_id},
                output_summary={"tool_request_count": len(requests)},
                success=True,
                level="WARNING",
            )
            return requests
        fallback = self._fallback_tool_requests(plan_data=retry_data, reason="empty decision_needs retry was unroutable")
        return fallback

    def _empty_plan_retry_prompt(
        self,
        plan_data: dict[str, Any],
        signal: ProblemSignalMetadata,
        difficulty: DifficultyAssessmentMetadata,
        resolution: ResolutionPlanMetadata,
    ) -> str:
        task_description = str(getattr(self, "_active_task_description", "") or "")
        goal = str(getattr(self, "_active_goal", "") or "")
        planning_surface = self._planning_surface_for_prompt(
            task_description,
            goal,
            context=getattr(self, "_active_context", None),
            retry_reason="empty_or_unroutable_decision_needs",
            signal=signal,
            plan_data=plan_data,
        )
        constraint_state = self._session_constraints_from_context(getattr(self, "_active_context", None))
        constraint_prompt = session_constraint_prompt_text(constraint_state) if constraint_state else ""
        constraint_section = f"{constraint_prompt}\n\n" if constraint_prompt else ""
        return (
            "The previous tool-planning response was empty or unroutable. "
            "Return JSON only with a non-empty decision_needs array.\n\n"
            f"Task: {task_description}\n"
            f"Goal: {goal}\n"
            f"{constraint_section}"
            f"Planning Surface:\n{planning_surface}\n\n"
            f"Problem signal: {json.dumps(signal.to_json_dict(), ensure_ascii=False)}\n"
            f"Difficulty: {json.dumps(difficulty.to_json_dict(), ensure_ascii=False)}\n"
            f"Resolution strategy: {resolution.strategy}\n"
            f"Previous plan: {json.dumps(plan_data, ensure_ascii=False, default=str)[:4000]}\n\n"
            "Minimum acceptable decision_needs examples:\n"
            "- For validation: [{\"need_type\":\"command_check\",\"question\":\"run validation\",\"command\":\"python -m compileall <project>\"}]\n"
            "- For implementation: code_generation then file_write then command_check.\n"
            "- For inspection: project_structure or file_read with a concrete target_path.\n"
            "Do not return tool_calls. Do not return an empty decision_needs array."
        )

    def _fallback_decision_plan(self, plan_data: dict[str, Any]) -> dict[str, Any] | None:
        """Build a conservative tool plan when the LLM plan is actionable but unroutable."""
        if not isinstance(plan_data, dict):
            return None

        task_description = str(getattr(self, "_active_task_description", "") or "").strip()
        goal = str(getattr(self, "_active_goal", "") or "").strip()
        project_path = self._infer_project_path(task_description, goal)
        target_file = self._infer_target_file(task_description, goal, project_path)
        needs: list[dict[str, Any]] = []
        active_task = getattr(self, "_active_task", None)
        task_kind = str(getattr(active_task, "kind", "") or "").strip().lower()

        if task_kind in {"inspect", "inspection", "analysis", "investigate", "codebase_understanding"}:
            read_paths: list[Path] = []
            for raw_path in getattr(active_task, "read_files", []) or []:
                candidate = Path(str(raw_path)).expanduser()
                if not candidate.is_absolute() and project_path is not None:
                    candidate = project_path / candidate
                candidate = candidate.resolve(strict=False)
                if candidate not in read_paths:
                    read_paths.append(candidate)
            if not read_paths and target_file is not None:
                read_paths.append(target_file.resolve(strict=False))
            for read_path in read_paths:
                needs.append(
                    {
                        "need_type": "file_read",
                        "question": f"Inspect {read_path} without modifying it",
                        "target_path": str(read_path),
                    }
                )
            if not needs and project_path is not None:
                needs.append(
                    {
                        "need_type": "project_structure",
                        "question": f"Inspect project structure under {project_path}",
                        "target_path": str(project_path),
                    }
                )
            return {"decision_needs": needs, "goal": goal} if needs else None

        if task_kind in {"validate", "validation", "verify", "test"}:
            validation_command = str(getattr(active_task, "validation_command", "") or "").strip()
            if not validation_command:
                return None
            needs.append(
                {
                    "need_type": "command_check",
                    "question": f"Run the task's required validation: {validation_command}",
                    "command": validation_command,
                    "attributes": {
                        "mode": "automatic",
                        "test_command": validation_command,
                        "timeout": 30,
                    },
                }
            )
            return {"decision_needs": needs, "goal": goal}

        if not self._should_generate_fallback_plan(plan_data):
            return None

        if self._looks_like_validation_task(task_description):
            if project_path is None:
                return None
            for command in self._fallback_validation_commands(project_path):
                needs.append(
                    {
                        "need_type": "command_check",
                        "question": f"Validate project with: {command}",
                        "command": command,
                        "attributes": {
                            "mode": "automatic",
                            "test_command": command,
                            "timeout": 30,
                        },
                    }
                )
            return {"decision_needs": needs, "goal": goal}

        if self._looks_like_documentation_only_task(task_description):
            readme_project = project_path or (target_file.parent if target_file else None)
            if readme_project:
                needs.append(
                    {
                        "need_type": "readme_generation",
                        "question": f"Generate documentation for: {task_description}",
                        "target_path": str(readme_project),
                        "attributes": {
                            "project_path": str(readme_project),
                            "goal": goal,
                        },
                    }
                )
                return {"decision_needs": needs, "goal": goal}

        if target_file is None:
            return None

        operation_kind = "file_replace" if target_file.exists() else "create_file"
        prompt_context = self._fallback_prompt_context(task_description, goal, target_file, project_path, operation_kind)
        needs.append(
            {
                "need_type": "code_generation",
                "question": f"Generate implementation for: {task_description}",
                "operation_kind": operation_kind,
                "attributes": {
                    "task_description": self._fallback_code_task_description(task_description, target_file, operation_kind),
                    "language": "python",
                    "operation_kind": operation_kind,
                    "prompt_context": prompt_context,
                },
            }
        )
        needs.append(
            {
                "need_type": "file_write",
                "question": f"Write generated implementation to {target_file}",
                "target_path": str(target_file),
                "operation_kind": operation_kind,
                "attributes": {
                    "operation_kind": operation_kind,
                    "encoding": "utf-8",
                    "create_dirs": True,
                },
            }
        )
        validation_root = project_path or target_file.parent
        needs.append(
            {
                "need_type": "command_check",
                "question": f"Validate generated Python files under {validation_root}",
                "command": f"python -m compileall {validation_root}",
                "attributes": {
                    "mode": "automatic",
                    "test_command": f"python -m compileall {validation_root}",
                },
            }
        )
        return {"decision_needs": needs, "goal": goal}

    def _fallback_validation_commands(self, project_path: Path) -> list[str]:
        commands: list[str] = []
        context = getattr(self, "_active_context", None)
        containers: list[dict[str, Any]] = []
        if context is not None:
            for raw in (getattr(context, "parent_context", {}), getattr(context, "shared_state", {})):
                if isinstance(raw, dict):
                    containers.append(raw)
                    validation_context = raw.get("validation_context")
                    if isinstance(validation_context, dict):
                        containers.append(validation_context)
        for container in containers:
            for key in ("run_command", "test_command", "validation_command"):
                raw = container.get(key)
                if raw and str(raw).strip():
                    commands.append(str(raw).strip())
        environment = (getattr(self.runtime, "_project_environments", {}) or {}).get(str(project_path))
        if isinstance(environment, dict):
            for key in ("run_command", "test_command"):
                raw = environment.get(key)
                if raw and str(raw).strip():
                    commands.append(str(raw).strip())
        commands.append(f"python -m compileall {project_path}")
        return list(dict.fromkeys(commands))[:2]

    def _should_generate_fallback_plan(self, plan_data: dict[str, Any]) -> bool:
        task_description = str(getattr(self, "_active_task_description", "") or "")
        if not self._looks_actionable(task_description):
            return False

        raw_needs = plan_data.get("decision_needs", [])
        if not raw_needs:
            return self._infer_target_file(task_description, str(getattr(self, "_active_goal", "") or ""), None) is not None

        actionable_need_seen = False
        for raw_need in raw_needs:
            if not isinstance(raw_need, dict):
                continue
            need_type = str(raw_need.get("need_type") or "").lower().replace("-", "_")
            if need_type in MUTATING_OR_EXECUTING_NEED_TYPES:
                actionable_need_seen = True
                continue
            if need_type not in {
                "file_read",
                "inspect_file",
                "multi_file_read",
                "project_structure",
                "read_directory",
                "read_file",
                "reference_search",
                "research",
                "web_search",
            }:
                actionable_need_seen = True
        return actionable_need_seen

    def _looks_actionable(self, task_description: str) -> bool:
        lowered = task_description.lower()
        return any(term in lowered or term in task_description for term in ACTIONABLE_FALLBACK_TERMS)

    def _looks_like_documentation_task(self, task_description: str) -> bool:
        lowered = task_description.lower()
        return any(term in lowered for term in ("readme", "documentation", "docs")) or any(
            term in task_description for term in ("文档", "说明")
        )

    def _looks_like_documentation_only_task(self, task_description: str) -> bool:
        lowered = task_description.lower()
        implementation_terms = (
            "build",
            "code",
            "create app",
            "create script",
            "develop",
            "implement",
            "write code",
            "write script",
        )
        chinese_implementation_terms = ("创建程序", "创建脚本", "实现", "开发", "编写程序", "编写脚本")
        return self._looks_like_documentation_task(task_description) and not (
            any(term in lowered for term in implementation_terms)
            or any(term in task_description for term in chinese_implementation_terms)
        )

    def _looks_like_validation_task(self, task_description: str) -> bool:
        active_task = getattr(self, "_active_task", None)
        if str(getattr(active_task, "kind", "") or "").lower() in {
            "validate",
            "validation",
            "verify",
            "test",
        }:
            return True
        if str(getattr(active_task, "validation_command", "") or "").strip():
            return True
        lowered = task_description.lower().lstrip()
        return lowered.startswith(("check", "run", "test", "validate", "verify")) or task_description.lstrip().startswith(
            ("检查", "测试", "验证")
        )

    def _fallback_code_task_description(self, task_description: str, target_file: Path, operation_kind: str) -> str:
        if operation_kind == "file_replace" and target_file.exists():
            return (
                f"Produce full replacement source for {target_file}. "
                f"Preserve useful existing behavior and implement this task: {task_description}"
            )
        return f"Produce complete source for {target_file}. Implement this task: {task_description}"

    def _fallback_prompt_context(
        self,
        task_description: str,
        goal: str,
        target_file: Path,
        project_path: Path | None,
        operation_kind: str,
    ) -> dict[str, Any]:
        existing_content = ""
        if target_file.exists() and target_file.is_file():
            try:
                existing_content = target_file.read_text(encoding="utf-8")[:12000]
            except OSError:
                existing_content = ""
        return {
            "fallback_planning": True,
            "operation_kind": operation_kind,
            "target_file": str(target_file),
            "project_path": str(project_path or target_file.parent),
            "goal": goal,
            "task_description": task_description,
            "existing_file_content": existing_content,
            "quality_rubric": [
                "Keep the generated file syntactically valid and runnable.",
                "Preserve existing useful behavior when replacing an existing file.",
                "Use local placeholders or graceful degradation for missing external credentials.",
            ],
        }

    def _infer_project_path(self, task_description: str, goal: str) -> Path | None:
        context_path = self._context_project_path()
        if context_path is not None:
            return context_path

        environments = getattr(self.runtime, "_project_environments", {}) or {}
        for raw_project in environments:
            if raw_project:
                return Path(str(raw_project)).expanduser()

        for path in self._extract_paths(f"{task_description}\n{goal}"):
            candidate = path.parent if path.suffix else path
            if candidate.exists():
                return candidate.expanduser()
        return None

    def _context_project_path(self) -> Path | None:
        context = getattr(self, "_active_context", None)
        if context is None:
            return None
        for container in (getattr(context, "parent_context", {}), getattr(context, "shared_state", {})):
            if not isinstance(container, dict):
                continue
            for key in ("project_path", "cwd", "target_dir", "output_dir"):
                raw = container.get(key)
                if raw:
                    return Path(str(raw)).expanduser()
        return None

    def _reset_subtask_local_no_progress_block(self, task: Task) -> None:
        controller = getattr(self.runtime, "runtime_controller", None)
        state = getattr(controller, "state", None)
        if not isinstance(state, RuntimeStateMetadata):
            return
        if (
            state.phase != AgentPhase.BLOCKED
            or state.completion_reason != "no new runtime facts after repeated tool results"
        ):
            return
        task_kind = str(task.kind or "").strip().lower()
        state.phase = (
            AgentPhase.VERIFY
            if task_kind in {"validate", "validation", "verify", "test"}
            else AgentPhase.DIAGNOSE
            if task_kind in {"inspect", "inspection", "analysis", "investigate"}
            else AgentPhase.EXECUTE
        )
        state.completion_reason = ""
        state.no_progress_rounds = 0

    def _context_default_attributes(self, context: TaskExecutionContext | None = None) -> dict[str, Any]:
        active_context = context or getattr(self, "_active_context", None)
        if active_context is None:
            return {}
        defaults: dict[str, Any] = {}
        project_root = ""
        for container in (getattr(active_context, "parent_context", {}), getattr(active_context, "shared_state", {})):
            if not isinstance(container, dict):
                continue
            raw_root = container.get("project_path") or container.get("cwd") or container.get("target_dir") or container.get("output_dir")
            if raw_root and not project_root:
                project_root = str(Path(str(raw_root)).expanduser().resolve(strict=False))
            raw_cwd = container.get("cwd")
            if raw_cwd and "cwd" not in defaults:
                defaults["cwd"] = str(Path(str(raw_cwd)).expanduser().resolve(strict=False))
            for key in ("run_command", "test_command", "validation_command"):
                raw = container.get(key)
                if raw and key not in defaults:
                    defaults[key] = str(raw).strip()
        if project_root:
            defaults.setdefault("project_path", project_root)
            cwd = str(defaults.get("cwd") or "").strip()
            try:
                Path(cwd).resolve(strict=False).relative_to(Path(project_root).resolve(strict=False))
            except (OSError, ValueError):
                defaults["cwd"] = project_root
            else:
                defaults.setdefault("cwd", project_root)
        return defaults

    def _planning_runtime_state(self, plan_data: dict[str, Any]) -> tuple[Any | None, ToolRouter, RuntimeStateMetadata]:
        controller = getattr(self.runtime, "runtime_controller", None)
        router = getattr(controller, "router", None)
        state = getattr(controller, "state", None)
        if router is None:
            router = ToolRouter(getattr(self.runtime, "tool_registry", None))
        if state is None:
            state = RuntimeStateMetadata(
                goal=str(
                    plan_data.get("goal")
                    or getattr(self, "_active_goal", "")
                    or getattr(self, "_active_task_description", "")
                    or "tool planning"
                )
            )
            if controller is not None:
                controller.state = state
        self._seed_runtime_state_from_context(state)
        if str(getattr(state.execution_mode_source, "value", state.execution_mode_source)) == "default":
            apply_read_only_runtime_mode(
                state,
                str(getattr(self, "_active_goal", "") or state.goal),
                tags=[],
                task_type="",
            )
        return controller, router, state

    def _seed_runtime_state_from_context(self, state: RuntimeStateMetadata) -> None:
        defaults = self._context_default_attributes()
        project_path = str(defaults.get("project_path") or "").strip()
        cwd = str(defaults.get("cwd") or "").strip()
        if project_path:
            state.add_fact(f"Project path: {project_path}")
            state.add_candidate_file(project_path, "project_path inherited from task context")
        if cwd and cwd != project_path:
            state.add_fact(f"Working directory: {cwd}")

    def _empty_decision_needs_can_synthesize(self, plan_data: dict[str, Any]) -> bool:
        if not isinstance(plan_data, dict):
            return False
        if "tool_calls" in plan_data:
            return False
        raw_needs = plan_data.get("decision_needs", [])
        if raw_needs != []:
            return False
        _controller, _router, state = self._planning_runtime_state(plan_data)
        if "runtime_mode:read_only_analysis" not in state.assumptions:
            return False
        if not self._state_has_read_only_synthesis_evidence(state):
            return False
        state.phase = AgentPhase.SUMMARIZE
        state.completion_reason = "read-only analysis has enough evidence to synthesize"
        self._log(
            "decision_need_empty_plan_synthesized",
            input_summary={
                "task_id": getattr(self, "_active_task_id", "unknown"),
                "decision_need_count": 0,
            },
            output_summary={"phase": str(state.phase), "completion_reason": state.completion_reason},
            success=True,
            level="INFO",
        )
        return True

    def _state_has_read_only_synthesis_evidence(self, state: RuntimeStateMetadata) -> bool:
        for fact in state.known_facts:
            normalized = str(fact or "").strip()
            if not normalized:
                continue
            if normalized.startswith("Project path:"):
                continue
            if normalized.startswith("Working directory:"):
                continue
            if normalized == "Task classified as read-only analysis; gather evidence before any mutation.":
                continue
            return True
        if state.selected_files or state.path_resolutions or state.resolved_questions:
            return True
        for _path, evidence_list in state.candidate_files.items():
            if any("project_path" not in str(evidence or "") for evidence in evidence_list):
                return True
        for event in state.tool_history:
            event_type = str(event.get("event_type") or "")
            if event_type and event_type not in {"runtime_guard"}:
                return True
        return False

    def _infer_target_file(self, task_description: str, goal: str, project_path: Path | None) -> Path | None:
        text = f"{task_description}\n{goal}"
        for path in self._extract_paths(text):
            if path.suffix:
                return path.expanduser()

        project_path = project_path or self._infer_project_path(task_description, goal)
        if project_path is None:
            return None

        hinted_name = self._filename_hint(task_description)
        if hinted_name:
            hinted_path = Path(hinted_name)
            return hinted_path if hinted_path.is_absolute() else project_path / hinted_path

        if self._looks_like_documentation_task(task_description):
            return project_path / "README.md"

        python_files = self._candidate_python_files(project_path)
        if python_files:
            return max(python_files, key=lambda path: self._score_python_file(path, task_description, goal, project_path))
        return project_path / "main.py"

    def _extract_paths(self, text: str) -> list[Path]:
        paths: list[Path] = []
        for quoted in re.findall(r"['\"](/[^'\"]+)['\"]", text):
            paths.append(Path(quoted))
        for raw in re.findall(r"(?<![\w.-])/(?:[^\s'\"<>|]+/?)+", text):
            cleaned = raw.rstrip(".,;:，。；：)")
            path = Path(cleaned)
            if path not in paths:
                paths.append(path)
        return paths

    def _filename_hint(self, task_description: str) -> str | None:
        match = re.search(r"([\w./-]+\.(?:py|md|ya?ml|json|toml|txt|sh))", task_description)
        return match.group(1) if match else None

    def _candidate_python_files(self, project_path: Path) -> list[Path]:
        if not project_path.exists() or not project_path.is_dir():
            return []
        excluded_parts = {".git", ".venv", "__pycache__", "node_modules", "site-packages"}
        return [
            path
            for path in project_path.rglob("*.py")
            if path.is_file() and not any(part in excluded_parts for part in path.parts)
        ]

    def _score_python_file(self, path: Path, task_description: str, goal: str, project_path: Path) -> int:
        lowered = f"{task_description}\n{goal}".lower()
        path_text = path.as_posix().lower()
        name = path.name.lower()
        stem = path.stem.lower()
        score = 0
        if name == "__init__.py":
            score -= 20
        if name in lowered:
            score += 30
        if stem and stem in lowered:
            score += 18
        if any(term in lowered for term in ("core", "logic", "module", "class")) and stem in {"core", "assistant", "app"}:
            score += 14
        if any(term in lowered for term in ("main", "entry", "cli", "loop", "command")) and stem in {
            "main",
            "cli",
            "app",
            "assistant",
        }:
            score += 10
        if "assistant" in lowered and "assistant" in path_text:
            score += 8
        try:
            depth = len(path.relative_to(project_path).parts)
        except ValueError:
            depth = len(path.parts)
        score -= depth
        return score

    def _route_decision_needs(self, plan_data: dict[str, Any]) -> list[dict[str, Any]]:
        raw_needs = plan_data.get("decision_needs", [])
        if not raw_needs:
            return []
        task = getattr(self, "_active_task", None)
        task_kind = str(getattr(task, "kind", "") or "").strip().lower()
        if task_kind in {"inspect", "inspection", "analysis", "investigate", "codebase_understanding"}:
            allowed_needs: list[Any] = []
            dropped_need_types: list[str] = []
            for raw_need in raw_needs:
                need_type = (
                    str(raw_need.get("need_type") or "").lower().replace("-", "_")
                    if isinstance(raw_need, dict)
                    else "invalid"
                )
                if need_type in INSPECTION_NEED_TYPES:
                    allowed_needs.append(raw_need)
                else:
                    dropped_need_types.append(need_type)
            if dropped_need_types:
                self._log(
                    "decision_need_dropped_for_subtask_purpose",
                    input_summary={
                        "task_id": getattr(task, "id", "unknown"),
                        "task_kind": task_kind,
                        "dropped_need_types": dropped_need_types,
                    },
                    output_summary={"retained_need_count": len(allowed_needs)},
                    success=False,
                    error="Inspect subtask proposed needs outside its read-only purpose.",
                    level="WARNING",
                )
            raw_needs = allowed_needs
            if not raw_needs:
                return []
        raw_needs = self._filter_needs_for_task_contract(raw_needs, task, task_kind)
        if not raw_needs:
            return []
        _controller, router, state = self._planning_runtime_state(plan_data)

        tool_requests: list[dict[str, Any]] = []
        for index, raw_need in enumerate(raw_needs):
            if not isinstance(raw_need, dict):
                continue
            normalized_need, normalized_fields = self._normalize_raw_decision_need(raw_need)
            try:
                need = DecisionNeedMetadata.model_validate(normalized_need)
            except ValidationError as exc:
                raise self._decision_need_validation_failure(raw_need, normalized_need, index, exc) from exc
            self._enforce_subtask_write_scope(need)
            if normalized_fields:
                self._log(
                    "decision_need_normalized",
                    input_summary={
                        "task_id": getattr(self, "_active_task_id", "unknown"),
                        "need_index": index,
                        "need_type": need.need_type,
                    },
                    output_summary={"normalized_fields": normalized_fields},
                    success=True,
                    level="DEBUG",
                )
            selections = router.route(state, need)
            if state.guard_history and not state.guard_history[-1].approved:
                guard = state.guard_history[-1]
                if guard.attributes.get("question") == need.question:
                    self._record_guard_decision_event(guard)
                    details = {
                        "failed_tool": str(guard.attributes.get("tool_name") or "tool_router"),
                        "failure_stage": "Tool Routing Guard",
                        "need_type": need.need_type,
                        "guard_decision": guard.to_json_dict(),
                        "required_need_blocked": True,
                    }
                    self._log(
                        "decision_need_blocked",
                        input_summary={
                            "task_id": getattr(self, "_active_task_id", "unknown"),
                            "need_type": need.need_type,
                            "tool_name": guard.attributes.get("tool_name"),
                        },
                        output_summary={"guard_decision": guard.to_json_dict()},
                        success=False,
                        error=guard.reason,
                        level="ERROR",
                    )
                    raise DecisionNeedResolutionError(
                        f"Required decision need was blocked: {guard.reason}",
                        details,
                    )
            for selection in selections:
                tool_requests.append(
                    {
                        "tool_name": selection.tool_name,
                        "reason": need.question,
                        "input_metadata": selection.input_metadata.to_params(),
                        "timeout_override": selection.timeout_override,
                    }
                )
            if self._should_synthesize_patch_writer(raw_needs, index, need):
                writer_need = DecisionNeedMetadata(
                    need_type="file_write",
                    question=f"Apply generated symbol replacement to {need.target_path}",
                    phase=need.phase,
                    target_path=need.target_path,
                    operation_kind=need.operation_kind or "modify_symbol",
                    target_scope=need.target_scope,
                    symbol_name=need.symbol_name,
                    symbol_type=need.symbol_type,
                    insertion_hint=need.insertion_hint,
                    patch_mode=need.patch_mode,
                    decision_to_unlock=need.decision_to_unlock,
                    expected_state_change=need.expected_state_change,
                    cost_hint=need.cost_hint,
                    risk_level=need.risk_level,
                    attributes=dict(need.attributes),
                )
                writer_selections = router.route(state, writer_need)
                if state.guard_history and not state.guard_history[-1].approved:
                    guard = state.guard_history[-1]
                    if guard.attributes.get("question") == writer_need.question:
                        self._record_guard_decision_event(guard)
                        raise DecisionNeedResolutionError(
                            f"Required synthesized file write was blocked: {guard.reason}",
                            {
                                "failed_tool": str(guard.attributes.get("tool_name") or "file_patch_writer"),
                                "failure_stage": "Tool Routing Guard",
                                "need_type": writer_need.need_type,
                                "guard_decision": guard.to_json_dict(),
                                "required_need_blocked": True,
                                "synthesized_from": need.need_type,
                            },
                        )
                if not writer_selections:
                    raise DecisionNeedResolutionError(
                        "Code symbol modification could not be paired with a durable file write.",
                        {
                            "failed_tool": "file_patch_writer",
                            "failure_stage": "Tool Routing",
                            "need_type": writer_need.need_type,
                            "synthesized_from": need.need_type,
                        },
                    )
                self._log(
                    "decision_need_write_synthesized",
                    input_summary={
                        "task_id": getattr(self, "_active_task_id", "unknown"),
                        "source_need_type": need.need_type,
                        "target_path": need.target_path,
                    },
                    output_summary={"tool_name": "file_patch_writer"},
                    success=True,
                    level="INFO",
                )
                for selection in writer_selections:
                    tool_requests.append(
                        {
                            "tool_name": selection.tool_name,
                            "reason": writer_need.question,
                            "input_metadata": selection.input_metadata.to_params(),
                            "timeout_override": selection.timeout_override,
                        }
                    )
        return tool_requests

    def _filter_needs_for_task_contract(
        self,
        raw_needs: list[Any],
        task: Task | None,
        task_kind: str,
    ) -> list[Any]:
        if task is None:
            return raw_needs
        expected_validation = str(getattr(task, "validation_command", "") or "").strip()
        expected_argv = self._normalized_command_argv(expected_validation) if expected_validation else None
        filtered: list[Any] = []
        dropped: list[str] = []
        validation_task = task_kind in {"validate", "validation", "verify", "verification"}
        implementation_task = task_kind in {"implement", "implementation", "modify", "edit", "write"}
        for raw_need in raw_needs:
            if not isinstance(raw_need, dict):
                filtered.append(raw_need)
                continue
            need_type = str(raw_need.get("need_type") or "").lower().replace("-", "_")
            command = str(raw_need.get("command") or "").strip()
            keep = True
            if validation_task:
                keep = (
                    need_type == "command_check"
                    and expected_argv is not None
                    and self._normalized_command_argv(command) == expected_argv
                )
            elif implementation_task and not expected_validation and need_type == "command_check":
                keep = False
            if keep:
                filtered.append(raw_need)
            else:
                dropped.append(need_type or "invalid")
        if dropped:
            self._log(
                "decision_need_dropped_for_subtask_contract",
                input_summary={
                    "task_id": getattr(task, "id", "unknown"),
                    "task_kind": task_kind,
                    "validation_command": expected_validation,
                    "dropped_need_types": dropped,
                },
                output_summary={"retained_need_count": len(filtered)},
                success=False,
                error="Decision needs exceeded the current subtask contract.",
                level="WARNING",
            )
        return filtered

    def _record_guard_decision_event(self, decision: Any) -> None:
        diagnostics = getattr(self.runtime, "runtime_diagnostics_hooks", None)
        if diagnostics and hasattr(diagnostics, "on_guard_decision"):
            diagnostics.on_guard_decision(
                task_id=str(getattr(self, "_active_task_id", "") or ""),
                session_id=self._session_id(),
                decision=decision,
            )

    @staticmethod
    def _should_synthesize_patch_writer(
        raw_needs: list[Any],
        current_index: int,
        need: DecisionNeedMetadata,
    ) -> bool:
        need_type = need.need_type.lower().replace("-", "_")
        if need_type not in {"code_symbol_modify", "code_patch", "modify_symbol"}:
            return False
        target = str(need.target_path or need.attributes.get("file_path") or "").strip()
        if not target:
            return False
        for index, raw in enumerate(raw_needs):
            if index == current_index or not isinstance(raw, dict):
                continue
            candidate_type = str(raw.get("need_type") or "").lower().replace("-", "_")
            if candidate_type not in {"file_write", "write_file"}:
                continue
            attrs = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
            candidate_target = str(raw.get("target_path") or attrs.get("file_path") or "").strip()
            if candidate_target == target:
                return False
        return True

    def _enforce_subtask_write_scope(self, need: DecisionNeedMetadata) -> None:
        task = getattr(self, "_active_task", None)
        if task is None:
            return
        need_type = need.need_type.lower().replace("-", "_")
        if need_type not in FILE_MUTATION_NEED_TYPES:
            return
        task_kind = str(getattr(task, "kind", "") or "").lower()
        planned_writes = [str(path) for path in getattr(task, "write_files", []) or [] if str(path).strip()]
        target = str(need.target_path or need.attributes.get("file_path") or "").strip()
        reason = ""
        if task_kind in {"inspect", "inspection", "validate", "validation", "verify", "test"}:
            reason = f"Subtask write scope forbids mutation for task kind '{task_kind}'."
        elif task_kind in {"implement", "repair"} and not planned_writes:
            reason = f"Subtask write scope is required for mutation task kind '{task_kind}'."
        elif planned_writes and target:
            project_root = self._context_project_path()

            def normalize_scoped_path(raw_path: str) -> str:
                candidate = Path(raw_path).expanduser()
                if not candidate.is_absolute() and project_root is not None:
                    candidate = project_root / candidate
                return str(candidate.resolve(strict=False))

            normalized_target = normalize_scoped_path(target)
            normalized_allowed = {normalize_scoped_path(path) for path in planned_writes}
            if normalized_target not in normalized_allowed:
                reason = f"Subtask write scope does not include target file: {target}"
        if not reason:
            return
        self._log(
            "decision_need_write_scope_blocked",
            input_summary={
                "task_id": getattr(task, "id", "unknown"),
                "task_kind": task_kind,
                "need_type": need.need_type,
                "target_path": target,
                "planned_write_files": planned_writes,
            },
            success=False,
            error=reason,
            level="ERROR",
        )
        raise DecisionNeedResolutionError(
            reason,
            {
                "failed_tool": "tool_planning_executor",
                "failure_stage": "Subtask Write Scope",
                "need_type": need.need_type,
                "target_path": target,
                "planned_write_files": planned_writes,
            },
        )

    @staticmethod
    def _observed_modified_files(tool_results: list[dict[str, Any]]) -> list[str]:
        observed: list[str] = []
        for item in tool_results:
            if not item.get("success") or item.get("tool") not in {
                "file_writer",
                "file_patch_writer",
                "file_delete_tool",
            }:
                continue
            input_metadata = item.get("input_metadata") or {}
            file_path = str(input_metadata.get("file_path") or "").strip()
            if file_path and file_path not in observed:
                observed.append(file_path)
        return observed

    @staticmethod
    def _normalized_command_argv(command: str) -> tuple[str, ...] | None:
        try:
            return tuple(shlex.split(str(command or "").strip()))
        except ValueError:
            return None

    @staticmethod
    def _completion_evidence_error(
        task: Task,
        tool_results: list[dict[str, Any]],
        *,
        observed_modified_files: list[str],
    ) -> str | None:
        if task.write_files and not observed_modified_files:
            return "Task planned file writes but has no observed file mutation evidence."
        task_kind = str(task.kind or "").lower()
        requires_validation = bool(task.validation_command) or task_kind in {
            "validate",
            "validation",
            "verify",
            "test",
        }
        expected_command = str(task.validation_command or "").strip()
        if requires_validation and not expected_command:
            return "Validation task is missing its required validation_command contract."
        if expected_command:
            expected_argv = ToolPlanningTaskExecutor._normalized_command_argv(expected_command)
            matched = False
            for item in tool_results:
                if not item.get("success") or item.get("tool") not in {"command_executor", "code_executor"}:
                    continue
                input_metadata = item.get("input_metadata") or {}
                actual_command = str(input_metadata.get("command") or "").strip()
                requested_command = str(input_metadata.get("requested_command") or actual_command).strip()
                if expected_argv is not None and ToolPlanningTaskExecutor._normalized_command_argv(requested_command) == expected_argv:
                    matched = True
                    break
            if not matched:
                return (
                    "Validation task has no observed validation command evidence matching "
                    f"required validation command: {expected_command}"
                )
        return None

    def _normalize_raw_decision_need(self, raw_need: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        normalized = dict(raw_need)
        normalized_fields: list[str] = []
        for key in list(normalized):
            if normalized[key] is None and key in DEFAULTABLE_DECISION_NEED_FIELDS:
                normalized.pop(key)
                normalized_fields.append(f"{key}:null_to_default")

        attributes = normalized.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        else:
            attributes = dict(attributes)

        allowed_fields = set(DecisionNeedMetadata.model_fields)
        tool_input_fields = set(ToolInputMetadata.model_fields)
        moved_fields: list[str] = []
        for key in list(normalized):
            if key in allowed_fields:
                continue
            if key in NEED_ATTRIBUTE_FIELDS or key in tool_input_fields:
                attributes.setdefault(key, normalized.pop(key))
                moved_fields.append(key)
        inherited_fields: list[str] = []
        for key, value in self._context_default_attributes().items():
            if value is None or value == "" or key in attributes:
                continue
            attributes[key] = value
            inherited_fields.append(key)
        if moved_fields or attributes:
            normalized["attributes"] = attributes
        normalized_fields.extend(f"{key}:moved_to_attributes" for key in moved_fields)
        normalized_fields.extend(f"{key}:inherited_from_context" for key in inherited_fields)
        if not str(normalized.get("question") or "").strip():
            normalized["question"] = self._default_need_question(normalized)
            normalized_fields.append("question:defaulted")
        return normalized, normalized_fields

    def _read_only_notice(
        self,
        task_description: str,
        goal: str,
        context: TaskExecutionContext | None = None,
    ) -> str:
        task = context.task if context is not None else getattr(self, "_active_task", None)
        tags = list(getattr(task, "tags", []) or [])
        task_type = str(getattr(task, "kind", "") or "")
        if not is_read_only_analysis_goal(f"{task_description}\n{goal}", tags=tags, task_type=task_type):
            return ""
        return (
            "Read-only task mode:\n"
            "- Analysis only.\n"
            "- Do not emit file_write, file_delete, code generation, bug_fix, repair, or mutating command needs.\n"
            "- Prefer file_read, project_structure, and safe validation evidence."
        )

    def _default_need_question(self, normalized_need: dict[str, Any]) -> str:
        need_type = str(normalized_need.get("need_type") or "tool_need")
        target = (
            normalized_need.get("target_path")
            or normalized_need.get("query")
            or normalized_need.get("command")
            or ""
        )
        if target:
            return f"{need_type}: {target}"
        return f"Handle {need_type}"

    def _decision_need_validation_failure(
        self,
        raw_need: dict[str, Any],
        normalized_need: dict[str, Any],
        index: int,
        exc: ValidationError,
    ) -> DecisionNeedValidationError:
        invalid_fields = [
            ".".join(str(part) for part in error.get("loc", ()))
            for error in exc.errors()
            if error.get("loc")
        ]
        raw_summary = self._safe_need_summary(raw_need)
        details = {
            "task_id": getattr(self, "_active_task_id", "unknown"),
            "need_index": index,
            "invalid_fields": invalid_fields,
            "raw_need_summary": raw_summary,
            "normalized_keys": sorted(str(key) for key in normalized_need),
            "failed_tool": "tool_planning_executor",
            "failure_stage": "Tool Planning",
        }
        self._log(
            "decision_need_schema_error",
            input_summary={"task_id": details["task_id"], "need_index": index, "raw_need": raw_summary},
            output_summary={"invalid_fields": invalid_fields},
            success=False,
            error=str(exc),
            level="ERROR",
        )
        return DecisionNeedValidationError(
            f"Decision need schema validation failed at index {index}: {str(exc)}",
            details,
        )

    def _safe_need_summary(self, raw_need: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for key, value in raw_need.items():
            if isinstance(value, str) and len(value) > 200:
                summary[key] = f"{value[:200]}..."
            elif isinstance(value, (dict, list)):
                summary[key] = f"<{type(value).__name__}:{len(value)}>"
            else:
                summary[key] = value
        return summary

    def _show_tool_running(
        self,
        task: Task,
        tool_name: str,
        input_payload: dict[str, Any],
        reason_text: str,
        index: int,
        total: int,
    ) -> None:
        if not self.runtime.enhanced_ui:
            return
        tool_status = f"Task: {task.description[:80]}\n"
        tool_status += f"Tool Execution: {index + 1}/{total}\n"
        tool_status += f"Tool: {tool_name}\n"
        if tool_name == "code_generator":
            task_desc = input_payload.get("task_description", "")
            tool_status += "Action: Generating code\n"
            tool_status += f"Request: {task_desc[:120]}\n"
            tool_status += f"Language: {input_payload.get('language', 'unknown')}"
        elif tool_name == "file_writer":
            file_path = input_payload.get("file_path", "unknown")
            content_len = len(input_payload.get("content", ""))
            tool_status += "Action: Writing file\n"
            tool_status += f"Path: {file_path}\n"
            tool_status += f"Size: {content_len} characters"
        elif tool_name == "code_executor":
            tool_status += "Action: Executing code\n"
            tool_status += f"Language: {input_payload.get('language', 'unknown')}"
        else:
            tool_status += f"Action: {reason_text[:120]}"
        self.runtime.enhanced_ui.set_current_task_state(
            title=f"Tool {index + 1}/{total}: {tool_name}",
            details=tool_status,
            status="running",
        )

    def _show_tool_result(self, tool_name: str, exec_result: Any) -> None:
        if not self.runtime.enhanced_ui:
            return
        result_status = f"Tool: {tool_name}\n"
        if exec_result.success:
            result_status += "Status: Success\n"
            output = exec_result.output_metadata.result if exec_result.output_metadata else None
            if tool_name == "file_writer" and output:
                result_status += "File written successfully"
            elif tool_name == "code_generator" and isinstance(output, dict):
                result_status += f"Generated {len(output.get('code', ''))} characters of code"
        else:
            result_status += "Status: Failed\n"
            if exec_result.error:
                result_status += f"Error: {exec_result.error.error_message[:160]}"
        self.runtime.enhanced_ui.set_current_task_state(
            title=f"Tool result: {tool_name}",
            details=result_status,
            status="completed" if exec_result.success else "failed",
        )
        time.sleep(0.5)

    def _log_tool_start(self, task: Task, tool_name: str, input_payload: dict[str, Any]) -> None:
        log_params = self.runtime._sanitize_tool_metadata(input_payload)
        if tool_name == "code_generator":
            log_params["task_description_length"] = len(log_params.get("task_description", ""))
        self.runtime.logger.log_event(
            "tool_execution_start",
            {
                "task_id": task.id,
                "tool": tool_name,
                "input_metadata_summary": log_params,
            },
            session_id=self._session_id(),
            turn_id=1,
            level="INFO",
        )

    def _log_tool_complete(self, task: Task, tool_name: str, exec_result: Any, log_output: dict[str, Any]) -> None:
        self.runtime.logger.log_event(
            "tool_executed",
            {
                "task_id": task.id,
                "tool": tool_name,
                "success": exec_result.success,
                "error": exec_result.error.error_message if exec_result.error else None,
                "output": log_output,
                "execution_time_ms": exec_result.execution_time_ms if hasattr(exec_result, "execution_time_ms") else None,
            },
            session_id=self._session_id(),
            turn_id=1,
            level="INFO" if exec_result.success else "ERROR",
        )

    def _summarize_metadata_output(self, output: Any) -> dict[str, Any]:
        if not output:
            return {}
        if not isinstance(output, dict):
            return {"output_type": type(output).__name__}
        log_output = output.copy()
        if "code" in log_output:
            log_output["code_length"] = len(log_output["code"])
            log_output["code_preview"] = log_output["code"][:200]
        if "content" in log_output:
            log_output["content_length"] = len(log_output["content"])
        return log_output

    def _build_tool_error(self, tool_results: list[dict[str, Any]]) -> str | None:
        failed_tools = [item for item in tool_results if not item["success"]]
        if not failed_tools:
            return None
        error_parts = [f"{len(failed_tools)} tool(s) failed:"]
        for failed in failed_tools:
            input_metadata = failed.get("input_metadata") if isinstance(failed.get("input_metadata"), dict) else {}
            failure_bits = [
                f"tool={failed.get('tool') or 'unknown'}",
                f"call={failed.get('call_id') or 'unknown'}",
            ]
            if input_metadata.get("file_path"):
                failure_bits.append(f"file_path={input_metadata['file_path']}")
            if input_metadata.get("directory_path"):
                failure_bits.append(f"directory_path={input_metadata['directory_path']}")
            if failed.get("suggested_recovery"):
                failure_bits.append(f"recovery={failed['suggested_recovery']}")
            error_parts.append(f"\n  - {'; '.join(failure_bits)}; error={failed.get('error')}")
        return "".join(error_parts)

    def _session_id(self) -> str:
        return getattr(self.runtime, "session_id", None) or "unknown"

    def _log(
        self,
        event_type: str,
        *,
        success: bool | None = None,
        duration_ms: int | None = None,
        input_summary: Any | None = None,
        output_summary: Any | None = None,
        error: str | None = None,
        level: str | None = None,
    ) -> None:
        logger = getattr(self.runtime, "logger", None)
        if not logger or not hasattr(logger, "log_structured_event"):
            return
        logger.log_structured_event(
            source_type="agent",
            source_name="autonomous_iteration.agents.tool_planning_executor",
            phase="task_execution",
            event_type=event_type,
            session_id=self._session_id(),
            turn_id=1,
            success=success,
            duration_ms=duration_ms,
            input_summary=input_summary,
            output_summary=output_summary,
            error=error,
            level=level,
        )
