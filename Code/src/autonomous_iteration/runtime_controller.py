"""Phase-driven agent runtime controller and deterministic safeguards."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Literal

from autonomous_iteration.checkpoint_store import RuntimeCheckpointStore
from autonomous_iteration.task_models import (
    Task,
    TaskDecompositionResult,
    TaskExecutionResult,
    TaskPriority,
    TaskStatus,
)
from core.semantic_types import TaskCard
from core.llm import LLMResponse
from core.exceptions import InvalidLLMResponseError, LLMProviderError, LLMTimeoutError
from memory.project_path_resolver import ProjectPathResolver, ground_command_paths_within_project
from memory.session_constraints import session_constraint_violation
from metadata import (
    AgentPhase,
    CheckpointBoundary,
    CheckpointFaultPoint,
    CheckpointStatus,
    ContextCompactionBinding,
    ContextCompactionRecord,
    ContextSelectionMetadata,
    FinalizationFaultPoint,
    DecisionNeedMetadata,
    DurableArtifactReference,
    EditPlanMetadata,
    EnvironmentReadiness,
    FileArtifactMetadata,
    FailureMetadata,
    GuardDecisionMetadata,
    ObservedFileMutationResult,
    LLMReplayEntry,
    LLMRequestHashVersion,
    PendingLLMRequest,
    PathIntentMetadata,
    ProjectImprovementPolicy,
    ProjectImprovementPolicySource,
    ProjectImprovementRequirement,
    ProjectImprovementStatus,
    ProjectFingerprint,
    Recoverability,
    RecoveryAutomationPolicy,
    RecoveryBlocker,
    RecoveryFallback,
    RecoveryFallbackAction,
    RecoveryMode,
    RecoveryReasonCode,
    RecoveryStatus,
    ReadToolReplayEntry,
    RuntimeCheckpointMetadata,
    RuntimeExecutionMode,
    RuntimeExecutionModeSource,
    RuntimeFinalizationCursor,
    RuntimeFinalizationStage,
    RuntimeReportMetadata,
    RuntimePromptContextSnapshot,
    RuntimeResumeDecisionMetadata,
    RuntimeStateMetadata,
    SessionConstraintState,
    SessionConstraintViolationCode,
    SessionIngressState,
    SessionExecutionCursor,
    SessionBootstrapCursor,
    SessionSemanticSnapshot,
    SessionStage,
    SessionTaskResult,
    TaskGraphNodeMetadata,
    ResultStatus,
    ToolDecisionMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
    VerificationStatus,
    VerificationPlanMetadata,
)
from tools.tool_selection import SelectionReason, ToolSelection
from tools.mutation_descriptor import FILE_MUTATION_TOOLS, file_mutation_targets
from utils.path_boundary import resolve_project_path


WRITE_TOOLS = {"file_writer", "file_patch_writer", "file_delete_tool"}
READ_TOOLS = {"file_reader", "multi_file_reader"}
EXECUTION_TOOLS = {"command_executor", "code_executor"}
FILE_CREATE_OPERATIONS = {"create_file", "file_create", "directory_generate"}
NON_EXECUTABLE_FILE_SUFFIXES = {
    ".cfg",
    ".conf",
    ".ini",
    ".json",
    ".md",
    ".rst",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
READ_ONLY_TASK_TAGS = {"analysis", "inspect", "inspection", "investigate", "readonly", "read_only", "understanding"}
READ_ONLY_TASK_TYPES = {"analysis", "inspection", "investigation", "document_summary", "codebase_understanding"}
READ_ONLY_TASK_TERMS = (
    "analy",
    "architecture",
    "explain",
    "inspect",
    "investigate",
    "review",
    "trace",
    "分析",
    "排查",
    "梳理",
    "理解",
    "解释",
    "取证",
)
READ_ONLY_BLOCKED_TOOLS = WRITE_TOOLS | {"bug_fix_tool", "code_generator", "code_unit_generator", "code_editor", "code_executor", "readme_tool"}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "forbidden": 3}
PHASE_SEQUENCE = [
    AgentPhase.UNDERSTAND_TASK,
    AgentPhase.UNDERSTAND_PROJECT,
    AgentPhase.DIAGNOSE,
    AgentPhase.PLAN,
    AgentPhase.EXECUTE,
    AgentPhase.VERIFY,
    AgentPhase.REPLAN,
    AgentPhase.SUMMARIZE,
]


def _project_improvement_policy(runtime: Any) -> ProjectImprovementPolicy:
    """Return the single typed completion policy, with legacy compatibility."""
    policy = getattr(runtime, "project_improvement_policy", None)
    if isinstance(policy, ProjectImprovementPolicy):
        return policy
    if isinstance(policy, dict):
        return ProjectImprovementPolicy.model_validate(policy)
    enabled = bool(getattr(runtime, "enable_iterative_improvement", True))
    targets = int(getattr(runtime, "required_successful_improvements", 0) or 0)
    if not enabled or targets <= 0:
        return ProjectImprovementPolicy(
            requirement=ProjectImprovementRequirement.DISABLED,
            source=ProjectImprovementPolicySource.LEGACY_CONFIG,
            target_successes=0,
            max_attempts=0,
        )
    return ProjectImprovementPolicy(
        requirement=ProjectImprovementRequirement.REQUIRED,
        source=ProjectImprovementPolicySource.LEGACY_CONFIG,
        target_successes=targets,
        max_attempts=max(
            targets,
            int(getattr(runtime, "max_iteration_attempts", targets) or targets),
        ),
    )


def _interrupted_project_improvement(exc: Exception) -> dict[str, Any]:
    return {
        "success": False,
        "status": "interrupted",
        "error_type": type(exc).__name__,
        "failure_stage": "Project Improvement",
        "failed_tool": "project_improvement_runtime",
        "failure_reason": str(exc),
        "retry_attempted": False,
        "retry_history": [],
    }


def _project_improvement_status(
    policy: ProjectImprovementPolicy,
    outcome: dict[str, Any] | None,
) -> ProjectImprovementStatus:
    if not policy.enabled:
        return ProjectImprovementStatus.SKIPPED
    if outcome is None:
        return ProjectImprovementStatus.SKIPPED
    if bool(outcome.get("success")):
        return ProjectImprovementStatus.SUCCEEDED
    if outcome.get("status") == "interrupted":
        return ProjectImprovementStatus.INTERRUPTED
    return ProjectImprovementStatus.FAILED


def is_read_only_analysis_goal(
    goal: str,
    *,
    tags: list[str] | None = None,
    task_type: str = "",
) -> bool:
    normalized_tags = {str(tag or "").strip().lower() for tag in tags or [] if str(tag or "").strip()}
    if normalized_tags.intersection(READ_ONLY_TASK_TAGS):
        return True
    normalized_task_type = str(task_type or "").strip().lower()
    if normalized_task_type in READ_ONLY_TASK_TYPES:
        return True
    lowered_goal = str(goal or "").strip().lower()
    if not lowered_goal:
        return False
    return any(term in lowered_goal for term in READ_ONLY_TASK_TERMS)


def apply_read_only_runtime_mode(
    state: RuntimeStateMetadata,
    goal: str,
    *,
    tags: list[str] | None = None,
    task_type: str = "",
) -> bool:
    if not is_read_only_analysis_goal(goal, tags=tags, task_type=task_type):
        return False
    state.execution_mode = RuntimeExecutionMode.READ_ONLY
    state.execution_mode_source = RuntimeExecutionModeSource.ROOT_GOAL
    state.execution_mode_reason = "Root task was classified as read-only analysis."
    marker = "runtime_mode:read_only_analysis"
    if marker not in state.assumptions:
        state.add_assumption(marker)
    state.add_fact("Task classified as read-only analysis; gather evidence before any mutation.")
    return True


def _state_is_read_only_analysis(state: RuntimeStateMetadata) -> bool:
    return state.execution_mode == RuntimeExecutionMode.READ_ONLY


def _command_looks_mutating(command: str) -> bool:
    lowered = f" {str(command or '').lower()} "
    patterns = (
        " rm ",
        " mv ",
        " cp ",
        " mkdir ",
        " touch ",
        " chmod ",
        " chown ",
        " tee ",
        " pip install ",
        " pip3 install ",
        " npm install ",
        " npm add ",
        " pnpm add ",
        " yarn add ",
        " git checkout ",
        " git switch ",
        " git commit ",
        " git reset ",
        " git clean ",
        " git apply ",
        " git am ",
    )
    return any(pattern in lowered for pattern in patterns) or " >" in lowered or ">>" in lowered or "sed -i" in lowered


def _phase_value(phase: AgentPhase | str) -> str:
    return phase.value if isinstance(phase, AgentPhase) else str(phase)


def _phase_is(phase: AgentPhase | str, expected: AgentPhase) -> bool:
    return _phase_value(phase) == expected.value


def _is_new_file_create(tool_name: str, file_path: Any, operation_kind: Any) -> bool:
    if tool_name != "file_writer":
        return False
    operation = str(operation_kind or "").lower()
    if operation and operation not in FILE_CREATE_OPERATIONS:
        return False
    if not file_path:
        return False
    try:
        return not Path(str(file_path)).expanduser().exists()
    except OSError:
        return False


class RuntimeGuard:
    """Centralized runtime policy for budgets, risk, and confirmation gates."""

    def approve_need(self, state: RuntimeStateMetadata, need: DecisionNeedMetadata, tool_name: str) -> GuardDecisionMetadata:
        if _phase_is(state.phase, AgentPhase.BLOCKED):
            return GuardDecisionMetadata(
                approved=False,
                reason=state.completion_reason or "runtime is blocked",
                risk_level=need.risk_level,
            )

        if self._risk_value(need.risk_level) >= self._risk_value("forbidden"):
            return GuardDecisionMetadata(
                approved=False,
                reason=f"Need '{need.question}' is forbidden risk.",
                risk_level=need.risk_level,
            )

        if self._risk_value(need.risk_level) >= self._risk_value("high"):
            return GuardDecisionMetadata(
                approved=False,
                reason=f"Need '{need.question}' requires user confirmation.",
                risk_level=need.risk_level,
                attributes={"requires_user_confirmation": True, "tool_name": tool_name},
            )

        if _state_is_read_only_analysis(state):
            read_only_block = self._read_only_restriction(state, need, tool_name)
            if read_only_block is not None:
                return read_only_block

        read_count = 1 if tool_name in READ_TOOLS else 0
        file_path = need.target_path or need.attributes.get("file_path")
        operation_kind = need.operation_kind or need.attributes.get("operation_kind")
        create_count = 1 if _is_new_file_create(tool_name, file_path, operation_kind) else 0
        edit_count = 1 if tool_name in WRITE_TOOLS and not create_count else 0
        if not state.budget.has_tool_budget(reads=read_count, edits=edit_count, creates=create_count):
            return GuardDecisionMetadata(
                approved=False,
                reason="; ".join(state.budget.exhausted_reasons()) or "runtime budget exhausted",
                risk_level=need.risk_level,
            )

        return GuardDecisionMetadata(
            approved=True,
            reason="Need is within runtime risk and budget policy.",
            risk_level=need.risk_level,
            attributes={"requires_confirmation": self.requires_confirmation(tool_name, need)},
        )

    def _read_only_restriction(
        self,
        state: RuntimeStateMetadata,
        need: DecisionNeedMetadata,
        tool_name: str,
    ) -> GuardDecisionMetadata | None:
        if tool_name in READ_ONLY_BLOCKED_TOOLS:
            return GuardDecisionMetadata(
                approved=False,
                reason=f"Read-only analysis task cannot use mutation tool '{tool_name}'. Gather evidence first and wait for explicit write mode.",
                risk_level=need.risk_level,
                attributes={"guard_kind": "read_only_analysis", "tool_name": tool_name},
            )
        if tool_name == "command_executor" and _command_looks_mutating(need.command or ""):
            return GuardDecisionMetadata(
                approved=False,
                reason="Read-only analysis task cannot run mutating shell commands.",
                risk_level=need.risk_level,
                attributes={"guard_kind": "read_only_analysis", "command": need.command or ""},
            )
        if self._read_only_file_read_lacks_project_context(state, need, tool_name):
            return GuardDecisionMetadata(
                approved=False,
                reason=(
                    "Read-only analysis file reads require project_path/cwd or prior path evidence before "
                    "reading a model-proposed path."
                ),
                risk_level=need.risk_level,
                attributes={"guard_kind": "read_only_path_grounding", "tool_name": tool_name},
            )
        return None

    def _read_only_file_read_lacks_project_context(
        self,
        state: RuntimeStateMetadata,
        need: DecisionNeedMetadata,
        tool_name: str,
    ) -> bool:
        if tool_name != "file_reader":
            return False
        attrs = need.attributes or {}
        if attrs.get("project_path") or attrs.get("cwd"):
            return False
        raw_path = str(need.target_path or attrs.get("file_path") or "").strip()
        if not raw_path:
            return False
        if raw_path in state.candidate_files or raw_path in state.selected_files:
            return False
        path = Path(raw_path).expanduser()
        if path.is_absolute():
            hallucinated_roots = ("/workspace/openpilot", "/workspace/project", "/openpilot")
            return raw_path.rstrip("/") in hallucinated_roots or raw_path.startswith(tuple(f"{root}/" for root in hallucinated_roots))
        return raw_path not in {".", "./"}

    def requires_confirmation(self, tool_name: str, need: DecisionNeedMetadata, registry: Any | None = None) -> bool:
        if self._risk_value(need.risk_level) >= self._risk_value("high"):
            return True
        if tool_name in WRITE_TOOLS:
            return True
        if registry and hasattr(registry, "get"):
            definition = registry.get(tool_name)
            permission_level = str(getattr(definition, "permission_level", "") or "").lower()
            return permission_level in {"high", "forbidden"}
        return False

    def should_replan(self, state: RuntimeStateMetadata) -> str | None:
        if state.verification_status == "failed":
            return "verification failed"
        if state.budget.exhausted_reasons():
            return "; ".join(state.budget.exhausted_reasons())
        if state.risk_level == "high" and not state.planned_edits:
            return "risk increased before an executable plan exists"
        return None

    def _risk_value(self, risk_level: str | None) -> int:
        return RISK_ORDER.get(str(risk_level or "medium").lower(), 1)


class ToolRouter:
    """Map explicit decision needs to tool selections under budget/risk limits."""

    def __init__(self, registry: Any | None = None, guard: RuntimeGuard | None = None) -> None:
        self.registry = registry
        self.guard = guard or RuntimeGuard()

    def route(self, state: RuntimeStateMetadata, need: DecisionNeedMetadata) -> list[ToolSelection]:
        """Return tool selections for a need, or block the state if routing is unsafe."""
        tool_name = self._tool_for_need(need)
        if not tool_name:
            state.add_unknown(need.question)
            return []

        guard_decision = self.guard.approve_need(state, need, tool_name)
        if not guard_decision.approved:
            guard_decision = guard_decision.model_copy(
                update={
                    "attributes": {
                        **guard_decision.attributes,
                        "need_type": need.need_type,
                        "question": need.question,
                        "tool_name": tool_name,
                        "required": True,
                    }
                }
            )
            state.record_guard_decision(guard_decision)
            if guard_decision.attributes.get("requires_user_confirmation"):
                state.phase = AgentPhase.ASK_USER
                state.completion_reason = guard_decision.reason
            else:
                state.block(guard_decision.reason)
            state.record_tool_event(
                {
                    "event_type": "runtime_guard",
                    "tool_name": tool_name,
                    "approved": False,
                    "reason": guard_decision.reason,
                    "guard_kind": guard_decision.attributes.get("guard_kind", ""),
                }
            )
            return []

        input_metadata = self._input_for_need(state, tool_name, need)
        if input_metadata is None:
            state.add_unknown(need.question)
            return []

        if tool_name in WRITE_TOOLS:
            target_path = need.target_path or need.attributes.get("file_path")
            if target_path:
                evidence = need.decision_to_unlock or need.question
                state.add_candidate_file(str(target_path), f"Decision need target: {evidence}")

        requires_confirmation = bool(guard_decision.attributes.get("requires_confirmation"))
        decision = ToolDecisionMetadata(
            need_type=need.need_type,
            question=need.question,
            selected_tool=tool_name,
            phase=need.phase,
            reason=self._decision_reason(tool_name, need),
            alternatives_considered=self._alternatives_for_need(need),
            expected_state_change=need.expected_state_change,
            risk_level=need.risk_level,
            cost_hint=need.cost_hint,
            requires_confirmation=requires_confirmation,
        )
        state.record_tool_decision(decision)
        return [
            ToolSelection(
                step_id=need.attributes.get("step_id") or f"{_phase_value(need.phase)}_{uuid.uuid4().hex[:8]}",
                tool_name=tool_name,
                reason=SelectionReason.CAPABILITY_MATCH,
                confidence=0.85,
                input_metadata=input_metadata,
                requires_confirmation=requires_confirmation,
                fallback_tools=[],
                depends_on=[],
                timeout_override=need.attributes.get("timeout"),
            )
        ]

    def _tool_for_need(self, need: DecisionNeedMetadata) -> str | None:
        need_type = need.need_type.lower().replace("-", "_")
        if need_type in {"file_read", "read_file", "inspect_file"}:
            if self._looks_like_directory_need(need):
                return "multi_file_reader"
            return "file_reader"
        if need_type in {"project_structure", "directory_read", "read_directory", "multi_file_read"}:
            return "multi_file_reader"
        if need_type in {"web_search", "reference_search", "research"}:
            return "web_searcher"
        if need_type in {"command_check", "smoke_test", "test", "verify_command"}:
            return "command_executor"
        if need_type in {"bug_fix", "bug_fix_tool", "fix_bug", "repair"}:
            return "bug_fix_tool"
        operation_kind = str(need.operation_kind or need.attributes.get("operation_kind") or "").lower()
        if need_type in {"file_delete", "delete_file", "remove_file"}:
            return "file_delete_tool"
        if need_type in {"file_write", "write_file"}:
            if operation_kind in {"add_symbol", "modify_symbol", "code_patch", "code_unit_generate", "code_symbol_modify"}:
                return "file_patch_writer"
            if operation_kind in {"delete_file", "file_delete", "remove_file"}:
                return "file_delete_tool"
            return "file_writer"
        if need_type == "code_file_create" and self._looks_like_non_executable_file_need(need):
            target_path = need.target_path or need.attributes.get("file_path")
            if target_path and Path(str(target_path)).name.lower().startswith("readme"):
                return "readme_tool"
            return "file_writer"
        if need_type in {"code_file_create", "directory_generate"}:
            return "code_generator"
        if need_type in {"code_unit_generate", "generate_code_unit", "add_symbol"}:
            return "code_unit_generator"
        if need_type in {"code_symbol_modify", "code_patch", "modify_symbol"}:
            return "code_editor"
        if need_type in {"code_generation", "generate_code", "code_generator"}:
            if operation_kind in {"add_symbol", "code_unit_generate"}:
                return "code_unit_generator"
            if operation_kind in {"modify_symbol", "code_patch", "code_symbol_modify"}:
                return "code_editor"
            return "code_generator"
        if need_type in {"code_execution", "execute_code", "run_code"}:
            if need.command or need.attributes.get("command"):
                return "command_executor"
            return "code_executor"
        if need_type in {"readme_generation", "readme", "documentation"}:
            return "readme_tool"
        return None

    def _input_for_need(self, state: RuntimeStateMetadata, tool_name: str, need: DecisionNeedMetadata) -> ToolInputMetadata | None:
        attrs = dict(need.attributes)
        project_path = self._project_root_from_attrs(attrs)
        if tool_name == "file_reader":
            file_path = need.target_path or attrs.get("file_path")
            if not file_path:
                return None
            resolved_file_path = self._resolve_single_path(
                state,
                raw_path=file_path,
                project_path=project_path,
                operation="read",
                intent_kind="existing_file",
                source=f"{tool_name}:file_path",
                evidence=[need.question],
            )
            if not resolved_file_path or self._path_is_existing_directory(resolved_file_path):
                return None
            payload = dict(attrs)
            payload["file_path"] = resolved_file_path
            if project_path:
                payload["project_path"] = project_path
            return ToolInputMetadata.from_mapping(tool_name, payload)
        if tool_name == "multi_file_reader":
            payload: dict[str, Any] = dict(attrs)
            if need.candidate_paths:
                payload["file_paths"] = self._resolve_many_paths(
                    state,
                    raw_paths=need.candidate_paths,
                    project_path=project_path,
                    operation="read",
                    intent_kind="existing_file",
                    source=f"{tool_name}:file_paths",
                    evidence=[need.question],
                )
                if not payload["file_paths"]:
                    return None
            elif need.target_path:
                resolved_directory = self._resolve_single_path(
                    state,
                    raw_path=need.target_path,
                    project_path=project_path,
                    operation="read",
                    intent_kind="existing_directory",
                    source=f"{tool_name}:directory_path",
                    evidence=[need.question],
                )
                if not resolved_directory:
                    return None
                payload["directory_path"] = resolved_directory
            elif project_path and self._looks_like_project_root_read_need(need):
                payload["directory_path"] = project_path
            if payload.get("directory_path") and not payload.get("pattern"):
                if self._should_use_directory_sketch(need, payload):
                    payload["pattern"] = "sketch.json"
                    payload.setdefault("max_files", 1)
                else:
                    payload["pattern"] = "*"
            if project_path:
                payload["project_path"] = project_path
            if not payload.get("file_paths") and not payload.get("directory_path"):
                return None
            return ToolInputMetadata.from_mapping(tool_name, payload)
        if tool_name == "web_searcher":
            query = need.query or attrs.get("query") or need.question
            return ToolInputMetadata.from_mapping(tool_name, {"query": query, **attrs})
        if tool_name == "command_executor":
            command = need.command or attrs.get("command")
            if not command:
                return None
            payload = {"command": command, "mode": attrs.get("mode", "automatic"), **attrs}
            cwd = attrs.get("cwd") or project_path
            if cwd and project_path:
                resolved_cwd = self._resolve_single_path(
                    state,
                    raw_path=cwd,
                    project_path=project_path,
                    operation="execute",
                    intent_kind="command_cwd",
                    source=f"{tool_name}:cwd",
                    evidence=[need.question, command],
                )
                if not resolved_cwd:
                    return None
                payload["cwd"] = resolved_cwd
            if project_path:
                resolved_command = self._resolve_command_text(
                    state,
                    command=command,
                    project_path=project_path,
                    source=f"{tool_name}:command",
                    evidence=[need.question, command],
                )
                if not resolved_command:
                    return None
                payload["command"] = resolved_command
                payload["project_path"] = project_path
            return ToolInputMetadata.from_mapping(tool_name, payload)
        if tool_name == "bug_fix_tool":
            command = need.command or attrs.get("command")
            file_paths = attrs.get("file_paths") or need.candidate_paths
            if not command or not file_paths:
                return None
            payload = dict(attrs)
            payload["command"] = command
            if project_path:
                payload["file_paths"] = self._resolve_many_paths(
                    state,
                    raw_paths=file_paths,
                    project_path=project_path,
                    operation="read",
                    intent_kind="existing_file",
                    source=f"{tool_name}:file_paths",
                    evidence=[need.question, command],
                )
                if not payload["file_paths"]:
                    return None
            else:
                payload["file_paths"] = file_paths
            return ToolInputMetadata.from_mapping(tool_name, payload)
        if tool_name == "file_writer":
            file_path = need.target_path or attrs.get("file_path")
            content = attrs.get("content")
            if not file_path:
                return None
            payload = dict(attrs)
            if project_path:
                requested_operation_kind = need.operation_kind or attrs.get("operation_kind")
                intent_kind = "planned_new_file" if str(requested_operation_kind or "").lower() in FILE_CREATE_OPERATIONS else "existing_file"
                resolved_file_path = self._resolve_single_path(
                    state,
                    raw_path=file_path,
                    project_path=project_path,
                    operation="write",
                    intent_kind=intent_kind,
                    source=f"{tool_name}:file_path",
                    evidence=[need.question],
                )
                if not resolved_file_path:
                    return None
                payload["file_path"] = resolved_file_path
            else:
                payload["file_path"] = file_path
            operation_kind = self._file_writer_operation_kind(
                payload["file_path"],
                need.operation_kind or attrs.get("operation_kind"),
                attrs.get("overwrite", True),
            )
            if operation_kind:
                payload["operation_kind"] = operation_kind
            if content is not None:
                payload["content"] = content
            if project_path:
                payload["project_path"] = project_path
            return ToolInputMetadata.from_mapping(tool_name, payload)
        if tool_name == "file_patch_writer":
            file_path = need.target_path or attrs.get("file_path")
            if not file_path:
                return None
            resolved_file_path = self._resolve_single_path(
                state,
                raw_path=file_path,
                project_path=project_path,
                operation="patch",
                intent_kind="existing_file",
                source=f"{tool_name}:file_path",
                evidence=[need.question],
            )
            if not resolved_file_path:
                return None
            payload = {
                "file_path": resolved_file_path,
                "operation_kind": need.operation_kind or attrs.get("operation_kind") or "modify_symbol",
                "target_scope": need.target_scope or attrs.get("target_scope"),
                "symbol_name": need.symbol_name or attrs.get("symbol_name"),
                "symbol_type": need.symbol_type or attrs.get("symbol_type"),
                "insertion_hint": need.insertion_hint or attrs.get("insertion_hint"),
                "patch_mode": need.patch_mode or attrs.get("patch_mode"),
                **attrs,
            }
            if project_path:
                payload["project_path"] = project_path
            return ToolInputMetadata.from_mapping(tool_name, payload)
        if tool_name == "file_delete_tool":
            file_path = need.target_path or attrs.get("file_path")
            if not file_path:
                return None
            resolved_file_path = self._resolve_single_path(
                state,
                raw_path=file_path,
                project_path=project_path,
                operation="delete",
                intent_kind="existing_file",
                source=f"{tool_name}:file_path",
                evidence=[need.question],
            )
            if not resolved_file_path:
                return None
            payload = dict(attrs)
            payload["file_path"] = resolved_file_path
            payload["operation_kind"] = need.operation_kind or attrs.get("operation_kind") or "delete_file"
            if project_path:
                payload["project_path"] = project_path
            return ToolInputMetadata.from_mapping(tool_name, payload)
        if tool_name == "code_generator":
            task_description = attrs.get("task_description") or need.question
            return ToolInputMetadata.from_mapping(
                tool_name,
                {
                    "task_description": task_description,
                    "language": attrs.get("language", "python"),
                    "operation_kind": need.operation_kind or attrs.get("operation_kind") or "file_create",
                    **attrs,
                },
            )
        if tool_name == "code_unit_generator":
            task_description = attrs.get("task_description") or need.question
            file_path = need.target_path or attrs.get("file_path")
            if file_path and project_path:
                resolved_file_path = self._resolve_single_path(
                    state,
                    raw_path=file_path,
                    project_path=project_path,
                    operation="read",
                    intent_kind="existing_file",
                    source=f"{tool_name}:file_path",
                    evidence=[need.question],
                )
                if not resolved_file_path:
                    return None
                file_path = resolved_file_path
            return ToolInputMetadata.from_mapping(
                tool_name,
                {
                    "task_description": task_description,
                    "language": attrs.get("language", "python"),
                    "file_path": file_path,
                    "operation_kind": need.operation_kind or attrs.get("operation_kind") or "add_symbol",
                    "target_scope": need.target_scope or attrs.get("target_scope") or "symbol",
                    "symbol_name": need.symbol_name or attrs.get("symbol_name"),
                    "symbol_type": need.symbol_type or attrs.get("symbol_type"),
                    "insertion_hint": need.insertion_hint or attrs.get("insertion_hint"),
                    **attrs,
                },
            )
        if tool_name == "code_editor":
            task_description = attrs.get("task_description") or need.question
            file_path = need.target_path or attrs.get("file_path")
            if file_path and project_path:
                resolved_file_path = self._resolve_single_path(
                    state,
                    raw_path=file_path,
                    project_path=project_path,
                    operation="read",
                    intent_kind="existing_file",
                    source=f"{tool_name}:file_path",
                    evidence=[need.question],
                )
                if not resolved_file_path:
                    return None
                file_path = resolved_file_path
            return ToolInputMetadata.from_mapping(
                tool_name,
                {
                    "task_description": task_description,
                    "language": attrs.get("language", "python"),
                    "file_path": file_path,
                    "operation_kind": need.operation_kind or attrs.get("operation_kind") or "modify_symbol",
                    "target_scope": need.target_scope or attrs.get("target_scope") or "symbol",
                    "symbol_name": need.symbol_name or attrs.get("symbol_name"),
                    "symbol_type": need.symbol_type or attrs.get("symbol_type"),
                    "patch_mode": need.patch_mode or attrs.get("patch_mode"),
                    **attrs,
                },
            )
        if tool_name == "code_executor":
            code = attrs.get("code")
            if not code:
                return None
            return ToolInputMetadata.from_mapping(
                tool_name,
                {
                    "code": code,
                    "language": attrs.get("language", "python"),
                    **attrs,
                },
            )
        if tool_name == "readme_tool":
            readme_project = need.target_path or attrs.get("project_path")
            if not readme_project:
                return None
            readme_project = self._readme_project_path(readme_project)
            if project_path:
                readme_project = self._resolve_single_path(
                    state,
                    raw_path=readme_project,
                    project_path=project_path,
                    operation="read",
                    intent_kind="existing_directory",
                    source=f"{tool_name}:project_path",
                    evidence=[need.question],
                )
                if not readme_project:
                    return None
            payload = dict(attrs)
            payload["project_path"] = readme_project
            return ToolInputMetadata.from_mapping(tool_name, payload)
        return None

    def _project_root_from_attrs(self, attrs: dict[str, Any]) -> str:
        raw_project = attrs.get("project_path") or attrs.get("cwd") or ""
        if not raw_project:
            return ""
        return str(resolve_project_path(raw_project))

    def _resolve_single_path(
        self,
        state: RuntimeStateMetadata,
        *,
        raw_path: Any,
        project_path: str,
        operation: str,
        intent_kind: str,
        source: str,
        evidence: list[str] | None = None,
    ) -> str | None:
        if not raw_path:
            return None
        if not project_path:
            return str(Path(str(raw_path)).expanduser())
        resolver = ProjectPathResolver(project_path)
        intent = PathIntentMetadata(
            project_root=project_path,
            raw_path=str(raw_path),
            intent_kind=intent_kind,
            operation=operation,
            path_source=source,
            evidence=list(evidence or []),
        )
        state.record_path_intent(intent)
        resolution = resolver.resolve(intent)
        state.record_path_resolution(resolution)
        if resolution.status in {"blocked", "ambiguous"}:
            state.add_unknown(resolution.reason or f"Could not resolve path for {source}")
            return None
        return resolution.resolved_path

    def _resolve_many_paths(
        self,
        state: RuntimeStateMetadata,
        *,
        raw_paths: list[str],
        project_path: str,
        operation: str,
        intent_kind: str,
        source: str,
        evidence: list[str] | None = None,
    ) -> list[str]:
        resolved: list[str] = []
        for raw_path in raw_paths:
            grounded = self._resolve_single_path(
                state,
                raw_path=raw_path,
                project_path=project_path,
                operation=operation,
                intent_kind=intent_kind,
                source=source,
                evidence=evidence,
            )
            if grounded:
                resolved.append(grounded)
        return list(dict.fromkeys(resolved))

    def _resolve_command_text(
        self,
        state: RuntimeStateMetadata,
        *,
        command: str,
        project_path: str,
        source: str,
        evidence: list[str] | None = None,
    ) -> str | None:
        grounded_command, intents, resolutions = ground_command_paths_within_project(
            command,
            project_path,
            source=source,
            evidence=evidence,
        )
        for intent in intents:
            state.record_path_intent(intent)
        for resolution in resolutions:
            state.record_path_resolution(resolution)
        blocking = next((item for item in resolutions if item.status in {"blocked", "ambiguous"}), None)
        if blocking is not None:
            state.add_unknown(blocking.reason or f"Could not resolve command path for {source}")
            return None
        return grounded_command

    def _readme_project_path(self, project_path: Any) -> str:
        path = Path(str(project_path)).expanduser()
        if path.name.lower().startswith("readme") and path.suffix:
            return str(path.parent)
        return str(path)

    def _file_writer_operation_kind(self, file_path: Any, operation_kind: Any, overwrite: Any) -> str | None:
        operation = str(operation_kind).lower() if operation_kind else None
        if overwrite is False:
            return operation
        if (operation is None or operation in {"create_file", "file_create", "directory_generate"}) and Path(str(file_path)).expanduser().exists():
            return "file_replace"
        return operation or "create_file"

    def _requires_confirmation(self, tool_name: str, need: DecisionNeedMetadata) -> bool:
        return self.guard.requires_confirmation(tool_name, need, self.registry)

    def _risk_value(self, risk_level: str | None) -> int:
        return RISK_ORDER.get(str(risk_level or "medium").lower(), 1)

    def _looks_like_directory_need(self, need: DecisionNeedMetadata) -> bool:
        target_path = need.target_path or need.attributes.get("file_path") or need.attributes.get("directory_path")
        if target_path and self._path_is_existing_directory(target_path):
            return True
        question = f"{need.question} {need.decision_to_unlock or ''}".lower()
        directory_tokens = (
            "files and directories",
            "directories exist",
            "what files exist",
            "list directory",
            "directory listing",
            "project folder",
            "project structure",
            "folder contents",
        )
        return any(token in question for token in directory_tokens)

    def _looks_like_project_root_read_need(self, need: DecisionNeedMetadata) -> bool:
        need_type = str(need.need_type or "").lower().replace("-", "_")
        if need_type in {"project_structure", "directory_read", "read_directory", "multi_file_read"}:
            return True
        text = f"{need.question} {need.target_path or ''}".lower()
        return "project" in text and any(term in text for term in ("structure", "directory", "root", "files"))

    def _should_use_directory_sketch(self, need: DecisionNeedMetadata, payload: dict[str, Any]) -> bool:
        if payload.get("file_paths"):
            return False
        need_type = str(need.need_type or "").lower().replace("-", "_")
        if need_type in {"project_structure", "directory_read", "read_directory", "multi_file_read"}:
            return True
        question = f"{need.question} {need.decision_to_unlock or ''}".lower()
        return any(
            token in question
            for token in (
                "project structure",
                "files and directories",
                "directories exist",
                "what files exist",
                "directory listing",
            )
        )

    def _path_is_existing_directory(self, path_value: Any) -> bool:
        try:
            return Path(str(path_value)).expanduser().is_dir()
        except OSError:
            return False

    def _looks_like_non_executable_file_need(self, need: DecisionNeedMetadata) -> bool:
        language = str(need.attributes.get("language") or "").lower()
        if language and language not in {"bash", "python", "shell"}:
            return True
        target_path = need.target_path or need.attributes.get("file_path")
        return bool(target_path and Path(str(target_path)).suffix.lower() in NON_EXECUTABLE_FILE_SUFFIXES)

    def _decision_reason(self, tool_name: str, need: DecisionNeedMetadata) -> str:
        unlock = f" to unlock {need.decision_to_unlock}" if need.decision_to_unlock else ""
        return f"{tool_name} can answer '{need.question}'{unlock} during {_phase_value(need.phase)}."

    def _alternatives_for_need(self, need: DecisionNeedMetadata) -> list[str]:
        need_type = need.need_type.lower().replace("-", "_")
        if need_type in {"file_read", "read_file", "inspect_file"}:
            return ["multi_file_reader"]
        if need_type in {"project_structure", "directory_read", "read_directory", "multi_file_read"}:
            return ["file_reader"]
        if need_type in {"command_check", "smoke_test", "test", "verify_command"}:
            return ["static inspection", "runtime verifier"]
        if need_type in {"file_write", "write_file"}:
            return ["file_patch_writer", "edit plan", "ask user"]
        if need_type in {"file_delete", "delete_file", "remove_file"}:
            return ["file_reader", "edit plan", "ask user"]
        if need_type in {"code_unit_generate", "generate_code_unit", "add_symbol"}:
            return ["code_generator"]
        if need_type in {"code_symbol_modify", "code_patch", "modify_symbol"}:
            return ["code_unit_generator", "code_generator"]
        return []


class FileSelector:
    """Promote evidence-backed candidate files into editable selected files."""

    def select(
        self,
        state: RuntimeStateMetadata,
        file_paths: list[str],
        evidence: dict[str, list[str]] | None = None,
    ) -> list[str]:
        evidence = evidence or {}
        selected: list[str] = []
        for file_path in file_paths:
            file_evidence = list(evidence.get(file_path) or state.candidate_files.get(file_path) or [])
            if not file_evidence:
                state.add_unknown(f"Missing file-selection evidence for {file_path}")
                continue
            for item in file_evidence:
                state.select_file(file_path, item)
            selected.append(file_path)
        return selected


class EditGuard:
    """Deterministically approve or reject edit plans before write operations."""

    def approve(self, state: RuntimeStateMetadata, edit_plan: EditPlanMetadata) -> GuardDecisionMetadata:
        missing_evidence = self._missing_evidence(edit_plan)
        target_count = len(set(edit_plan.target_files))
        budget_kind = str(edit_plan.attributes.get("budget_kind") or "file_edit")
        over_edit_budget = budget_kind != "file_create" and target_count + state.budget.file_edits_used > state.budget.max_file_edits
        over_create_budget = budget_kind == "file_create" and target_count + state.budget.file_creates_used > state.budget.max_file_creates
        unselected = [file_path for file_path in edit_plan.target_files if file_path not in state.selected_files]
        missing_verification = not edit_plan.verification
        high_risk_without_constraints = (
            RISK_ORDER.get(edit_plan.risk_level.lower(), 1) >= RISK_ORDER["high"]
            and not edit_plan.forbidden_changes
        )

        if missing_evidence:
            return GuardDecisionMetadata(
                approved=False,
                reason="Edit plan lacks evidence.",
                risk_level=edit_plan.risk_level,
                blocked_files=edit_plan.target_files,
                required_evidence=missing_evidence,
                required_verification=edit_plan.verification,
            )
        if over_edit_budget or over_create_budget:
            return GuardDecisionMetadata(
                approved=False,
                reason=(
                    "Edit plan exceeds the runtime file create budget."
                    if over_create_budget
                    else "Edit plan exceeds the runtime file edit budget."
                ),
                risk_level=edit_plan.risk_level,
                blocked_files=edit_plan.target_files,
                required_evidence=[],
                required_verification=edit_plan.verification,
            )
        if unselected:
            return GuardDecisionMetadata(
                approved=False,
                reason="Target files must be selected from evidence-backed candidates before editing.",
                risk_level=edit_plan.risk_level,
                blocked_files=unselected,
                required_evidence=[f"select_file:{file_path}" for file_path in unselected],
                required_verification=edit_plan.verification,
            )
        if missing_verification:
            return GuardDecisionMetadata(
                approved=False,
                reason="Edit plan must include verification before write tools can run.",
                risk_level=edit_plan.risk_level,
                blocked_files=edit_plan.target_files,
                required_evidence=[],
                required_verification=["Add a verification command, smoke check, or static check."],
            )
        if high_risk_without_constraints:
            return GuardDecisionMetadata(
                approved=False,
                reason="High-risk edit plans must state forbidden changes.",
                risk_level=edit_plan.risk_level,
                blocked_files=edit_plan.target_files,
                required_evidence=[],
                required_verification=edit_plan.verification,
            )

        return GuardDecisionMetadata(
            approved=True,
            reason="Edit plan is evidence-backed, scoped, and verifiable.",
            risk_level=edit_plan.risk_level,
            required_verification=edit_plan.verification,
        )

    def _missing_evidence(self, edit_plan: EditPlanMetadata) -> list[str]:
        missing: list[str] = []
        if not edit_plan.evidence:
            missing.append("At least one evidence item is required.")
        if not edit_plan.target_files:
            missing.append("At least one target file is required.")
        if not edit_plan.allowed_changes:
            missing.append("Allowed changes must be stated.")
        return missing


class StateUpdater:
    """Absorb tool results into explicit runtime state."""

    def __init__(
        self,
        guard: RuntimeGuard | None = None,
        *,
        state_event_sink: Callable[[RuntimeStateMetadata, str, str], None] | None = None,
        result_applied_sink: Callable[[RuntimeStateMetadata, ToolSelection, Any], None] | None = None,
    ) -> None:
        self.guard = guard or RuntimeGuard()
        self.state_event_sink = state_event_sink
        self.result_applied_sink = result_applied_sink

    def apply_tool_result(
        self,
        state: RuntimeStateMetadata,
        selection: ToolSelection,
        execution_result: Any,
    ) -> RuntimeStateMetadata:
        previous_phase = _phase_value(state.phase)
        previous_verification_status = str(state.verification_status or "")
        progress_before = self._progress_signature(state)
        tool_name = selection.tool_name
        success = bool(getattr(execution_result, "success", False))
        input_params = selection.input_metadata.to_params()
        output_metadata = getattr(execution_result, "output_metadata", None)
        error = getattr(getattr(execution_result, "error", None), "error_message", None)
        file_create = tool_name == "file_writer" and str(input_params.get("operation_kind") or "").lower() in FILE_CREATE_OPERATIONS

        state.budget.consume_tool_call(
            file_read=tool_name in READ_TOOLS,
            file_edit=tool_name in FILE_MUTATION_TOOLS and not file_create,
            file_create=file_create,
        )
        state.record_tool_event(
            {
                "tool_name": tool_name,
                "step_id": selection.step_id,
                "success": success,
                "phase": _phase_value(state.phase),
                "input": input_params,
                "error": error,
            }
        )

        was_verifying = _phase_is(state.phase, AgentPhase.VERIFY)
        if not success:
            state.add_unknown(error or f"{tool_name} failed")
            state.verification_status = "failed" if was_verifying else state.verification_status
            replan_reason = self.guard.should_replan(state)
            if replan_reason and state.budget.replan_rounds_used < state.budget.max_replan_rounds:
                state.request_replan(replan_reason)
            else:
                state.phase = AgentPhase.RECOVER
            self._emit_state_changes(state, previous_phase, previous_verification_status)
            self._emit_result_applied(state, selection, execution_result)
            return state

        matching_decisions = [decision for decision in state.decision_history if decision.selected_tool == tool_name]
        if matching_decisions:
            state.resolve_unknown(matching_decisions[-1].question)
        if tool_name in READ_TOOLS:
            self._absorb_file_read(state, selection, output_metadata)
        elif tool_name in WRITE_TOOLS:
            self._absorb_file_write(state, input_params, output_metadata)
        elif tool_name in EXECUTION_TOOLS:
            self._absorb_execution(state, selection, output_metadata, was_verifying)
        elif tool_name == "web_searcher":
            state.add_fact(f"Reference search completed for: {input_params.get('query', '')}".strip())

        self._update_progress_stop_condition(state, progress_before)
        self._emit_state_changes(state, previous_phase, previous_verification_status)
        self._emit_result_applied(state, selection, execution_result)
        return state

    def _emit_result_applied(
        self,
        state: RuntimeStateMetadata,
        selection: ToolSelection,
        execution_result: Any,
    ) -> None:
        if self.result_applied_sink is not None:
            self.result_applied_sink(state, selection, execution_result)

    def _emit_state_changes(
        self,
        state: RuntimeStateMetadata,
        previous_phase: str,
        previous_verification_status: str,
    ) -> None:
        if self.state_event_sink is None:
            return
        current_phase = _phase_value(state.phase)
        current_verification_status = str(state.verification_status or "")
        if current_phase != previous_phase:
            self.state_event_sink(state, "phase", previous_phase)
        if current_verification_status != previous_verification_status:
            self.state_event_sink(state, "verification", previous_verification_status)

    def next_phase(self, state: RuntimeStateMetadata) -> AgentPhase:
        if _phase_value(state.phase) in {AgentPhase.BLOCKED.value, AgentPhase.RECOVER.value, AgentPhase.SUMMARIZE.value}:
            return state.phase
        if state.modified_files and state.verification_status != "passed":
            return AgentPhase.VERIFY
        try:
            index = [phase.value for phase in PHASE_SEQUENCE].index(_phase_value(state.phase))
        except ValueError:
            return AgentPhase.BLOCKED
        return PHASE_SEQUENCE[min(index + 1, len(PHASE_SEQUENCE) - 1)]

    def _absorb_file_read(self, state: RuntimeStateMetadata, selection: ToolSelection, output_metadata: Any) -> None:
        params = selection.input_metadata.to_params()
        file_paths = list(params.get("file_paths") or [])
        if params.get("file_path"):
            file_paths.append(str(params["file_path"]))
        if params.get("directory_path"):
            state.add_fact(f"Inspected project directory: {params['directory_path']}")

        result = getattr(output_metadata, "result", None)
        files_from_result = getattr(result, "files", None) or []
        file_path_from_result = getattr(result, "file_path", None)
        if file_path_from_result:
            file_paths.append(str(file_path_from_result))
        file_paths.extend(str(item) for item in files_from_result)
        for file_path in dict.fromkeys(file_paths):
            state.add_candidate_file(file_path, f"{selection.tool_name} returned evidence for {file_path}")
        if file_paths:
            state.add_fact(f"Read {len(dict.fromkeys(file_paths))} file path(s) for evidence.")

    def _absorb_file_write(self, state: RuntimeStateMetadata, input_params: dict[str, Any], output_metadata: Any) -> None:
        result = getattr(output_metadata, "result", None)
        file_path = str(getattr(result, "file_path", "") or input_params.get("file_path") or "")
        state.add_modified_file(file_path)
        state.verification_status = "required"
        state.phase = AgentPhase.VERIFY
        state.add_fact(f"Modified file: {file_path}")

    def _absorb_execution(
        self,
        state: RuntimeStateMetadata,
        selection: ToolSelection,
        output_metadata: Any,
        was_verifying: bool,
    ) -> None:
        if was_verifying or _phase_is(state.phase, AgentPhase.VERIFY) or state.verification_status == "required":
            state.budget.consume_verification_attempt()
            state.verification_status = "passed"
            state.phase = AgentPhase.SUMMARIZE
            state.completion_reason = "verification passed"
        elif self._execution_changed_project(state, selection):
            state.verification_status = "required"
            state.phase = AgentPhase.VERIFY
            state.add_fact(f"Project state changed via {selection.tool_name}; verification required.")
        else:
            state.add_fact(f"Executed command via {getattr(output_metadata, 'tool_name', 'tool')}.")

    def _execution_changed_project(self, state: RuntimeStateMetadata, selection: ToolSelection) -> bool:
        if selection.tool_name != "command_executor":
            return False
        target_files = set(selection.input_metadata.to_params().get("file_paths") or [])
        if not target_files:
            target_files = set(selection.input_metadata.to_params().get("files") or [])
        if not target_files:
            command_cwd = selection.input_metadata.to_params().get("cwd")
            target_files = {str(command_cwd or ".")}
        for edit_plan in state.planned_edits:
            if set(edit_plan.target_files).intersection({str(item) for item in target_files}):
                return True
        return False

    def _progress_signature(self, state: RuntimeStateMetadata) -> tuple[int, int, int, int, int]:
        return (
            len(state.known_facts),
            len(state.unknowns),
            len(state.resolved_questions),
            len(state.candidate_files),
            len(state.modified_files),
        )

    def _update_progress_stop_condition(self, state: RuntimeStateMetadata, before: tuple[int, int, int, int, int]) -> None:
        after = self._progress_signature(state)
        if after != before:
            state.no_progress_rounds = 0
            return
        state.no_progress_rounds += 1
        if state.no_progress_rounds >= 3:
            state.block("no new runtime facts after repeated tool results")


class RuntimeVerifier:
    """Select the smallest verification plan after a write operation."""

    def plan(self, state: RuntimeStateMetadata, context: dict[str, Any] | None = None) -> VerificationPlanMetadata:
        context = context or {}
        timeout = None
        command = context.get("test_command")
        if not command:
            python_target = self._python_entry_file(state)
            if python_target:
                python_command = str(context.get("python_command") or "python")
                command = f"{shlex.quote(python_command)} {shlex.quote(python_target)} --help"
                timeout = 5
        command = command or context.get("run_command") or self._pytest_command(context) or "python -m compileall ."
        return VerificationPlanMetadata(
            reason="Write operation requires verification.",
            commands=[command],
            target_files=list(state.modified_files),
            fallback_checks=["static check"] if command != "python -m compileall ." else [],
            attributes={"timeout": timeout} if timeout else {},
        )

    def _pytest_command(self, context: dict[str, Any]) -> str | None:
        project_path = str(context.get("project_path") or context.get("cwd") or "")
        if project_path:
            return "pytest"
        return None

    def _python_entry_file(self, state: RuntimeStateMetadata) -> str | None:
        for file_path in reversed(state.modified_files):
            if str(file_path).endswith(".py"):
                return str(file_path)
        return None


class RuntimeReporter:
    """Build the single auditable report from runtime state."""

    def report(self, state: RuntimeStateMetadata) -> RuntimeReportMetadata:
        residual_risks: list[str] = []
        if state.unknowns:
            residual_risks.append("unresolved runtime questions remain")
        if state.verification_status not in {"passed", "not_required"}:
            residual_risks.append(f"verification status is {state.verification_status}")
        if _phase_value(state.phase) in {AgentPhase.RECOVER.value, AgentPhase.BLOCKED.value}:
            residual_risks.append(f"runtime stopped in {_phase_value(state.phase)}")
        return RuntimeReportMetadata(
            goal=state.goal,
            phase=state.phase,
            recovery_status=state.recovery_status,
            recovery_reason_code=state.recovery_reason_code,
            active_resume_attempt_id=state.active_resume_attempt_id,
            completion_reason=state.completion_reason,
            core_success=state.core_success,
            project_improvement_policy=state.project_improvement_policy,
            project_improvement_status=state.project_improvement_status,
            project_improvement_failure=state.project_improvement_failure,
            known_facts=list(state.known_facts),
            unresolved_questions=list(state.unknowns),
            path_resolutions=list(state.path_resolutions),
            selected_files=dict(state.selected_files),
            modified_files=list(state.modified_files),
            planned_edits=list(state.planned_edits),
            verification_status=state.verification_status,
            risk_level=state.risk_level,
            tool_decisions=list(state.decision_history),
            tool_history=list(state.tool_history),
            residual_risks=residual_risks,
        )


class _RuntimeSessionExecutor:
    """Run the runtime session mechanics owned by AgentRuntimeController."""

    supports_session_cursor = True

    def __init__(
        self,
        runtime: Any,
        *,
        session_cursor_sink: Callable[[SessionExecutionCursor], None] | None = None,
        session_bootstrap_sink: Callable[[SessionBootstrapCursor], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.session_cursor_sink = session_cursor_sink
        self.session_bootstrap_sink = session_bootstrap_sink

    def run(
        self,
        goal: str,
        context: dict[str, Any],
        mode: str = "standard",
        resume_cursor: SessionExecutionCursor | None = None,
        resume_bootstrap: SessionBootstrapCursor | None = None,
    ) -> dict[str, Any]:
        """Run the full execution shell."""
        self._log(
            "session_started",
            input_summary={"goal": goal, "mode": mode},
            success=None,
        )
        if mode == "enhanced_ui":
            if resume_cursor is not None:
                return self._run_standard(
                    goal,
                    context,
                    resume_cursor=resume_cursor,
                    session_mode="enhanced_ui",
                )
            return self._run_enhanced_ui(goal, context, resume_bootstrap=resume_bootstrap)
        if mode == "standard":
            return self._run_standard(
                goal,
                context,
                resume_cursor=resume_cursor,
                resume_bootstrap=resume_bootstrap,
            )
        raise ValueError(f"Unsupported autopilot session mode: {mode}")

    def _run_enhanced_ui(
        self,
        goal: str,
        context: dict[str, Any],
        *,
        resume_bootstrap: SessionBootstrapCursor | None = None,
    ) -> dict[str, Any]:
        runtime = self.runtime
        goal_hash = f"sha256:{hashlib.sha256(goal.encode('utf-8')).hexdigest()}"
        bootstrap = resume_bootstrap or SessionBootstrapCursor(
            mode="enhanced_ui",
            goal_hash=goal_hash,
        )
        if bootstrap.mode != "enhanced_ui" or bootstrap.goal_hash != goal_hash:
            raise ValueError("session bootstrap does not match the requested goal and mode")
        if self.session_bootstrap_sink is not None:
            self.session_bootstrap_sink(bootstrap)
        runtime.tracker.start_tracking()
        stages = [
            "Semantic Analysis",
            "Memory Retrieval",
            "Task Decomposition",
            "Execution",
            "Evaluation",
            "Iteration 1",
            "Iteration 2",
            "Result Assembly",
        ]
        stage_statuses = {stage: "pending" for stage in stages}
        runtime.enhanced_ui.set_task_graph_state(
            goal=goal,
            stages=stages,
            stage_statuses=stage_statuses,
            current_stage="Semantic Analysis",
            tasks=[],
        )
        runtime.enhanced_ui.set_current_task_state(
            title="Semantic Analysis",
            details=f"Goal: {goal[:120]}",
            status="running",
        )

        try:
            stage_statuses["Semantic Analysis"] = "running"
            runtime.enhanced_ui.set_task_graph_state(
                stage_statuses=stage_statuses,
                current_stage="Semantic Analysis",
            )
            with runtime.tracker.track_task("Semantic Analysis", {"goal": goal}):
                semantic = self._analyze_goal(goal, task_id=str(context.get("task_id") or ""))

            stage_statuses["Semantic Analysis"] = "completed"
            runtime.enhanced_ui.set_task_graph_state(stage_statuses=stage_statuses)
            runtime.enhanced_ui.log_activity("success", f"Analysis complete: {semantic.task_type.value}")
            runtime.enhanced_ui.set_current_task_state(
                title="Semantic Analysis",
                details=(
                    f"Task Type: {semantic.task_type.value}\n"
                    f"Risk Level: {semantic.risk_level.value}\n"
                    f"Required Resources: {len(semantic.required_resources)}"
                ),
                status="completed",
            )
            time.sleep(1.5)

            stage_statuses["Memory Retrieval"] = "running"
            runtime.enhanced_ui.set_task_graph_state(
                stage_statuses=stage_statuses,
                current_stage="Memory Retrieval",
            )
            runtime.enhanced_ui.set_current_task_state(
                title="Memory Retrieval",
                details="Searching for relevant past experiences",
                status="running",
            )
            with runtime.tracker.track_task("Memory Retrieval", {"query": goal}):
                memories = runtime.memory_store.query(goal, limit=5)

            stage_statuses["Memory Retrieval"] = "completed"
            runtime.enhanced_ui.set_task_graph_state(stage_statuses=stage_statuses)
            if memories.memories:
                runtime.enhanced_ui.log_activity("success", f"Found {len(memories.memories)} relevant memories")
                memory_info = f"Found {len(memories.memories)} relevant memories:\n\n"
                for i, mem in enumerate(memories.memories[:3], 1):
                    memory_info += f"{i}. [{mem.memory_type.value}] {mem.content[:60]}...\n"
                runtime.enhanced_ui.set_current_task_state(
                    title="Memory Retrieval",
                    details=memory_info,
                    status="completed",
                )
                time.sleep(1.5)
            else:
                runtime.enhanced_ui.log_activity("info", "No relevant memories found")
                runtime.enhanced_ui.set_current_task_state(
                    title="Memory Retrieval",
                    details="No relevant memories found",
                    status="completed",
                )

            self._enrich_context(context, goal, semantic, memories)
            fast_result = runtime._try_simple_code_artifact_fast_path(goal, semantic)
            if fast_result is not None:
                self._log("session_fast_path_completed", output_summary={"mode": "enhanced_ui"}, success=True)
                return fast_result

            stage_statuses["Task Decomposition"] = "running"
            runtime.enhanced_ui.set_task_graph_state(
                stage_statuses=stage_statuses,
                current_stage="Task Decomposition",
            )
            runtime.enhanced_ui.set_current_task_state(
                title="Task Decomposition",
                details="Breaking down task into executable subtasks",
                status="running",
            )
            with runtime.tracker.track_task("Task Decomposition", {"goal": goal}):
                decomposition = runtime.task_decomposer.decompose(
                    task_description=goal,
                    context=context,
                )
            execution_order = self._execution_order(decomposition.subtasks)
            self._emit_cursor(
                self._cursor(
                    semantic=semantic,
                    decomposition=decomposition,
                    execution_order=execution_order,
                    next_task_index=0,
                    results=[],
                    mode="enhanced_ui",
                ).model_copy(update={"stage": SessionStage.DECOMPOSITION_RECORDED})
            )

            stage_statuses["Task Decomposition"] = "completed"
            runtime.enhanced_ui.set_task_graph_state(
                stage_statuses=stage_statuses,
                tasks=runtime._dashboard_task_items(decomposition.subtasks),
            )
            runtime.enhanced_ui.log_activity("success", f"Created {len(decomposition.subtasks)} subtasks")

            breakdown_info = f"Created {len(decomposition.subtasks)} subtasks:\n\n"
            for i, subtask in enumerate(decomposition.subtasks[:5], 1):
                breakdown_info += f"{i}. {subtask.description[:70]}...\n"
            if len(decomposition.subtasks) > 5:
                breakdown_info += f"\n... and {len(decomposition.subtasks) - 5} more tasks"
            runtime.enhanced_ui.set_current_task_state(
                title="Task Decomposition",
                details=breakdown_info,
                status="completed",
            )
            time.sleep(2.0)
            time.sleep(3.0)

            stage_statuses["Execution"] = "running"
            runtime.enhanced_ui.set_task_graph_state(
                stage_statuses=stage_statuses,
                current_stage="Execution",
                tasks=runtime._dashboard_task_items(decomposition.subtasks),
            )
            runtime.enhanced_ui.set_current_task_state(
                title="Execution",
                details=f"Running {len(decomposition.subtasks)} tasks",
                status="running",
            )

            prepare_environment = getattr(runtime, "_prepare_session_environment", None)
            if callable(prepare_environment):
                prepare_environment(decomposition.subtasks, goal)
            results = runtime._execute_tasks(
                decomposition.subtasks,
                goal,
                progress_sink=lambda tasks, order, current_results, next_index: self._emit_cursor(
                    self._cursor(
                        semantic=semantic,
                        decomposition=decomposition.model_copy(update={"subtasks": tasks}),
                        execution_order=order,
                        next_task_index=next_index,
                        results=current_results,
                        mode="enhanced_ui",
                    )
                ),
            )
            all_tasks_completed = all(t.status == TaskStatus.COMPLETED for t in decomposition.subtasks)
            stage_statuses["Execution"] = "completed" if all_tasks_completed else "failed"
            runtime.enhanced_ui.set_task_graph_state(
                stage_statuses=stage_statuses,
                tasks=runtime._dashboard_task_items(decomposition.subtasks),
            )
            readme_result, written_files, project_path, improvement_result = self._finalize_project_outputs(
                goal,
                results,
                all_tasks_completed,
            )

            success, iteration_error_msg = self._update_stats(decomposition, improvement_result)
            execution_failure = self._execution_failure_context(decomposition.subtasks)
            if all_tasks_completed:
                stage_statuses["Result Assembly"] = "running"
                runtime.enhanced_ui.set_task_graph_state(
                    stage_statuses=stage_statuses,
                    current_stage="Result Assembly",
                )
                runtime.enhanced_ui.set_current_task_state(
                    title="Result Assembly",
                    details="Assembling final result",
                    status="running",
                )
                with runtime.tracker.track_task("Result Assembly", {}):
                    runtime.task_decomposer.assemble_results(
                        decomposition.original_task,
                        decomposition.subtasks,
                    )
                stage_statuses["Result Assembly"] = "completed"
                runtime.enhanced_ui.set_task_graph_state(stage_statuses=stage_statuses)
            else:
                stage_statuses["Result Assembly"] = "failed"
                runtime.enhanced_ui.set_task_graph_state(stage_statuses=stage_statuses)
            runtime._stop_tracking_if_owned()
            self._set_enhanced_completion_state(success, readme_result, improvement_result, iteration_error_msg, execution_failure)

            result = self._build_result(
                goal=goal,
                semantic=semantic,
                decomposition=decomposition,
                results=results,
                readme_result=readme_result,
                improvement_result=improvement_result,
                iteration_error_msg=iteration_error_msg,
                success=success,
                include_final_result=False,
                execution_failure=execution_failure,
            )
            self._log(
                "session_completed",
                output_summary={"mode": "enhanced_ui", "success": success, "project_path": str(project_path) if project_path else None},
                success=success,
            )
            return result

        except Exception as exc:
            runtime.tracker.stop_tracking()
            runtime.enhanced_ui.set_current_task_state(
                title="Error",
                details=f"Execution failed: {str(exc)}",
                status="failed",
            )
            self._log("session_failed", success=False, error=str(exc))
            raise

    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(getattr(value, "value", value) or "")

    @classmethod
    def _semantic_snapshot(cls, semantic: Any) -> SessionSemanticSnapshot:
        return SessionSemanticSnapshot(
            task_type=cls._enum_value(getattr(semantic, "task_type", "unknown")),
            risk_level=cls._enum_value(getattr(semantic, "risk_level", "medium")),
            required_resources=[str(item) for item in getattr(semantic, "required_resources", [])],
            expected_deliverables=[str(item) for item in getattr(semantic, "expected_deliverables", [])],
            confidence=float(getattr(semantic, "confidence", 0.0) or 0.0),
        )

    @staticmethod
    def _task_node(task: Task) -> TaskGraphNodeMetadata:
        return TaskGraphNodeMetadata(
            task_id=task.id,
            description=task.description,
            priority=task.priority.value if hasattr(task.priority, "value") else str(task.priority),
            estimated_effort=task.estimated_effort,
            task_kind=task.kind,
            difficulty=task.difficulty,
            required_inputs=list(task.required_inputs),
            expected_outputs=list(task.expected_outputs),
            read_files=list(task.read_files),
            support_context_files=list(task.support_context_files),
            write_files=list(task.write_files),
            dependencies=list(task.dependencies),
            can_run_parallel=task.can_run_parallel,
            validation_command=task.validation_command,
            tags=list(task.tags),
            problem_resolution_depth=int(task.attributes.get("problem_resolution_depth") or 0),
            problem_resolution_parent_task_id=(
                str(task.attributes.get("problem_resolution_parent_task_id"))
                if task.attributes.get("problem_resolution_parent_task_id")
                else None
            ),
        )

    @staticmethod
    def _task_from_node(node: TaskGraphNodeMetadata) -> Task:
        attributes: dict[str, Any] = {}
        if node.problem_resolution_depth:
            attributes["problem_resolution_depth"] = node.problem_resolution_depth
        if node.problem_resolution_parent_task_id:
            attributes["problem_resolution_parent_task_id"] = node.problem_resolution_parent_task_id
        return Task(
            id=node.task_id,
            description=node.description,
            priority=TaskPriority(node.priority),
            estimated_effort=node.estimated_effort,
            kind=node.task_kind,
            difficulty=node.difficulty,
            required_inputs=list(node.required_inputs),
            expected_outputs=list(node.expected_outputs),
            read_files=list(node.read_files),
            support_context_files=list(node.support_context_files),
            write_files=list(node.write_files),
            dependencies=list(node.dependencies),
            can_run_parallel=node.can_run_parallel,
            validation_command=node.validation_command,
            tags=list(node.tags),
            attributes=attributes,
        )

    @staticmethod
    def _session_plan_hash(
        original_task: TaskGraphNodeMetadata,
        tasks: list[TaskGraphNodeMetadata],
        execution_order: list[str],
    ) -> str:
        payload = {
            "original_task": original_task.to_json_dict(),
            "tasks": [task.to_json_dict() for task in tasks],
            "execution_order": execution_order,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def _execution_order(self, tasks: list[Task]) -> list[str]:
        try:
            graph = self.runtime.task_decomposer.build_task_graph(tasks)
            return list(self.runtime.task_decomposer.get_execution_order(graph))
        except (AttributeError, ValueError):
            return [task.id for task in tasks]

    def _result_snapshot(self, result: TaskExecutionResult) -> SessionTaskResult:
        summary_value: Any = result.result_summary
        if summary_value is None:
            summarizer = getattr(self.runtime, "_history_result_summary", None)
            summary_value = summarizer(result) if callable(summarizer) else result.error or ""
        if not isinstance(summary_value, str):
            summary_value = json.dumps(summary_value, ensure_ascii=False, sort_keys=True, default=str)
        observed_paths = list(result.observed_paths)
        if not observed_paths:
            path_reader = getattr(self.runtime, "_history_observed_paths", None)
            if callable(path_reader):
                observed_paths = [str(path) for path in path_reader(result)]
        status = self._enum_value(result.status)
        if result.attributes.get("blocked"):
            status = "blocked"
        return SessionTaskResult(
            task_id=result.task_id,
            status=status,
            summary_text=summary_value,
            error=result.error,
            duration=result.duration,
            observed_modified_files=list(
                dict.fromkeys(
                    [str(path) for path in result.attributes.get("observed_modified_files") or []]
                    + observed_paths
                )
            ),
        )

    def _cursor(
        self,
        *,
        semantic: Any,
        decomposition: TaskDecompositionResult,
        execution_order: list[str],
        next_task_index: int,
        results: list[TaskExecutionResult],
        mode: Literal["standard", "enhanced_ui"] = "standard",
    ) -> SessionExecutionCursor:
        original = self._task_node(decomposition.original_task)
        tasks = [self._task_node(task) for task in decomposition.subtasks]
        return SessionExecutionCursor(
            mode=mode,
            stage=(
                SessionStage.TASKS_EXECUTED
                if next_task_index == len(execution_order)
                else SessionStage.TASK_EXECUTION
            ),
            plan_hash=self._session_plan_hash(original, tasks, execution_order),
            semantic=self._semantic_snapshot(semantic),
            original_task=original,
            tasks=tasks,
            execution_order=list(execution_order),
            next_task_index=next_task_index,
            results=[self._result_snapshot(result) for result in results],
        )

    def _emit_cursor(self, cursor: SessionExecutionCursor) -> None:
        if self.session_cursor_sink is not None:
            self.session_cursor_sink(cursor)

    def _restore_cursor(
        self,
        cursor: SessionExecutionCursor,
    ) -> tuple[Any, TaskDecompositionResult, list[TaskExecutionResult]]:
        if cursor.plan_hash != self._session_plan_hash(
            cursor.original_task,
            cursor.tasks,
            cursor.execution_order,
        ):
            raise ValueError("session cursor plan hash mismatch")
        semantic = SimpleNamespace(
            task_type=SimpleNamespace(value=cursor.semantic.task_type),
            risk_level=SimpleNamespace(value=cursor.semantic.risk_level),
            required_resources=list(cursor.semantic.required_resources),
            expected_deliverables=list(cursor.semantic.expected_deliverables),
            confidence=cursor.semantic.confidence,
        )
        tasks = [self._task_from_node(node) for node in cursor.tasks]
        results: list[TaskExecutionResult] = []
        result_by_id = {result.task_id: result for result in cursor.results}
        for task in tasks:
            saved = result_by_id.get(task.id)
            if saved is None:
                continue
            task.status = TaskStatus.COMPLETED if saved.status == "completed" else TaskStatus.FAILED
            task.error = saved.error
            results.append(
                TaskExecutionResult(
                    task_id=saved.task_id,
                    status=task.status,
                    error=saved.error,
                    duration=saved.duration,
                    result_summary=saved.summary_text,
                    observed_paths=list(saved.observed_modified_files),
                    attributes={"observed_modified_files": list(saved.observed_modified_files)},
                )
            )
        decomposition = TaskDecompositionResult(
            original_task=self._task_from_node(cursor.original_task),
            subtasks=tasks,
            task_graph_summary="restored from durable session cursor",
            decomposition_rationale="restored from durable session cursor",
            estimated_total_effort=sum(task.estimated_effort or 0.0 for task in tasks),
        )
        return semantic, decomposition, results

    def _run_standard(
        self,
        goal: str,
        context: dict[str, Any],
        *,
        resume_cursor: SessionExecutionCursor | None = None,
        resume_bootstrap: SessionBootstrapCursor | None = None,
        session_mode: Literal["standard", "enhanced_ui"] = "standard",
    ) -> dict[str, Any]:
        runtime = self.runtime
        try:
            runtime._show_start_panel(goal)

            if resume_cursor is None:
                goal_hash = f"sha256:{hashlib.sha256(goal.encode('utf-8')).hexdigest()}"
                bootstrap = resume_bootstrap or SessionBootstrapCursor(
                    mode=session_mode,
                    goal_hash=goal_hash,
                )
                if bootstrap.mode != session_mode or bootstrap.goal_hash != goal_hash:
                    raise ValueError("session bootstrap does not match the requested goal and mode")
                if self.session_bootstrap_sink is not None:
                    self.session_bootstrap_sink(bootstrap)
                runtime.console.print("[bold cyan]🧠 Analyzing goal...[/bold cyan]")
                semantic = self._analyze_goal(goal, task_id=str(context.get("task_id") or ""))
                runtime.console.print(f"  • Task type: [cyan]{semantic.task_type.value}[/cyan]")
                runtime.console.print(
                    f"  • Risk level: [{'red' if semantic.risk_level.value == 'high' else 'yellow' if semantic.risk_level.value == 'medium' else 'green'}]{semantic.risk_level.value}[/]"
                )
                runtime.console.print(f"  • Confidence: {semantic.confidence:.2f}")
                runtime.console.print()

                runtime.console.print("[bold cyan]🧠 Retrieving memories...[/bold cyan]")
                memories = runtime.memory_store.query(goal, limit=5)
                if memories.memories:
                    runtime.console.print(f"  • Found {len(memories.memories)} relevant memories")
                    for mem in memories.memories[:3]:
                        runtime.console.print(f"    - [{mem.memory_type.value}] {mem.content[:60]}...")
                else:
                    runtime.console.print("  • No relevant memories found")
                runtime.console.print()

                self._enrich_context(context, goal, semantic, memories)
                fast_result = runtime._try_simple_code_artifact_fast_path(goal, semantic)
                if fast_result is not None:
                    self._log("session_fast_path_completed", output_summary={"mode": "standard"}, success=True)
                    return fast_result

                runtime.console.print("[bold cyan]🔍 Decomposing task...[/bold cyan]")
                decomposition = runtime.task_decomposer.decompose(
                    task_description=goal,
                    context=context,
                )
                execution_order = self._execution_order(decomposition.subtasks)
                initial_cursor = self._cursor(
                    semantic=semantic,
                    decomposition=decomposition,
                    execution_order=execution_order,
                    next_task_index=0,
                    results=[],
                ).model_copy(update={"stage": SessionStage.DECOMPOSITION_RECORDED})
                self._emit_cursor(initial_cursor)

                runtime.console.print(f"  • Original task: {decomposition.original_task.description}")
                runtime.console.print(f"  • Subtasks: {len(decomposition.subtasks)}")
                runtime.console.print(f"  • Estimated effort: {decomposition.estimated_total_effort:.1f} units")
                runtime.console.print()
                runtime._show_task_tree(decomposition)

                runtime.logger.log_event(
                    "task_decomposition",
                    {
                        "goal": goal,
                        "original_task_id": decomposition.original_task.id,
                        "subtask_count": len(decomposition.subtasks),
                        "estimated_effort": decomposition.estimated_total_effort,
                        "rationale": decomposition.decomposition_rationale,
                    },
                    session_id=runtime.session_id,
                    turn_id=1,
                )
                prior_results: list[TaskExecutionResult] = []
                start_index = 0
            else:
                if resume_cursor.mode != session_mode:
                    raise ValueError("session cursor mode does not match requested execution mode")
                semantic, decomposition, prior_results = self._restore_cursor(resume_cursor)
                execution_order = list(resume_cursor.execution_order)
                start_index = resume_cursor.next_task_index
                runtime.console.print(
                    f"[bold cyan]↻ Resuming task {start_index + 1}/{len(execution_order)} from durable cursor[/bold cyan]"
                )

            runtime.console.print("[bold cyan]⚡ Executing tasks...[/bold cyan]")
            prepare_environment = getattr(runtime, "_prepare_session_environment", None)
            if callable(prepare_environment):
                prepare_environment(decomposition.subtasks, goal)
            results = runtime._execute_tasks(
                decomposition.subtasks,
                goal,
                prior_results=prior_results,
                start_index=start_index,
                progress_sink=lambda tasks, order, current_results, next_index: self._emit_cursor(
                    self._cursor(
                        semantic=semantic,
                        decomposition=decomposition.model_copy(update={"subtasks": tasks}),
                        execution_order=order,
                        next_task_index=next_index,
                        results=current_results,
                        mode=session_mode,
                    )
                ),
            )
            all_tasks_completed = all(t.status == TaskStatus.COMPLETED for t in decomposition.subtasks)
            readme_result, written_files, project_path, improvement_result = self._finalize_project_outputs(
                goal,
                results,
                all_tasks_completed,
            )

            final_result = None
            execution_failure = self._execution_failure_context(decomposition.subtasks)
            if all_tasks_completed:
                runtime.console.print()
                runtime.console.print("[bold cyan]📦 Assembling results...[/bold cyan]")
                final_result = runtime.task_decomposer.assemble_results(
                    decomposition.original_task,
                    decomposition.subtasks,
                )

            success, iteration_error_msg = self._update_stats(decomposition, improvement_result)
            runtime._show_completion_summary(decomposition, results)
            if iteration_error_msg:
                runtime.console.print(f"[yellow]Autonomous iteration warning:[/yellow] {iteration_error_msg}")

            result = self._build_result(
                goal=goal,
                semantic=semantic,
                decomposition=decomposition,
                results=results,
                readme_result=readme_result,
                improvement_result=improvement_result,
                iteration_error_msg=iteration_error_msg,
                success=success,
                include_final_result=True,
                final_result=final_result,
                execution_failure=execution_failure,
            )
            self._log(
                "session_completed",
                output_summary={"mode": "standard", "success": success, "project_path": str(project_path) if project_path else None},
                success=success,
            )
            return result

        except Exception as exc:
            runtime.console.print(f"\n[bold red]❌ Execution failed: {exc}[/bold red]")
            runtime.stats["success"] = False
            runtime.stats["end_time"] = datetime.now()
            runtime.logger.log_event(
                "autopilot_failed",
                {
                    "goal": goal,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                session_id=runtime.session_id or "unknown",
                turn_id=1,
            )
            self._log("session_failed", success=False, error=str(exc))
            raise

    def _analyze_goal(self, goal: str, *, task_id: str = "") -> Any:
        runtime = self.runtime
        analyzer = runtime.semantic_analyzer
        try:
            semantic = analyzer.analyze_goal(goal)
            hooks = getattr(runtime, "runtime_diagnostics_hooks", None)
            if hooks:
                task_card = TaskCard(
                    goal=goal,
                    task_type=semantic.task_type,
                    risk_level=semantic.risk_level,
                    required_resources=list(semantic.required_resources),
                    expected_deliverables=list(semantic.expected_deliverables),
                    constraints=[],
                    context={},
                )
                resolved_task_id = str(task_id or getattr(runtime, "session_id", "") or "unknown")
                session_id = str(getattr(runtime, "session_id", "") or resolved_task_id)
                hooks.on_task_card_ready(
                    task_id=resolved_task_id,
                    task_card=task_card,
                    session_id=session_id,
                )
            return semantic
        except (InvalidLLMResponseError, LLMProviderError, LLMTimeoutError) as exc:
            fallback = getattr(analyzer, "fallback_goal_analysis", None)
            if not callable(fallback):
                raise
            semantic = fallback(goal, type(exc).__name__)
            self._log(
                "semantic_analysis_fallback",
                success=True,
                input_summary={"goal": goal, "reason": str(exc)},
                output_summary={"task_type": semantic.task_type.value},
                level="WARNING",
            )
            if runtime.enhanced_ui:
                runtime.enhanced_ui.log_activity(
                    "warning",
                    f"Semantic analysis fallback: {type(exc).__name__}",
                )
            return semantic

    def _enrich_context(self, context: dict[str, Any], goal: str, semantic: Any, memories: Any) -> None:
        context["semantic_analysis"] = semantic.model_dump()
        context["memories"] = [m.model_dump() for m in memories.memories]
        context["goal"] = goal

    def _finalize_project_outputs(
        self,
        goal: str,
        results: list[Any],
        all_tasks_completed: bool,
    ) -> tuple[Any, list[str], Any, dict[str, Any] | None]:
        runtime = self.runtime
        readme_result = runtime._finalize_project_readme(goal, results) if all_tasks_completed else None
        written_files = runtime._collect_written_files(results)
        project_path = runtime._infer_project_path_from_files(goal, written_files) if written_files else None
        can_iterate = bool(all_tasks_completed and project_path and written_files)
        improvement_policy = _project_improvement_policy(runtime)
        self._log(
            "project_iteration_decision",
            input_summary={
                "all_tasks_completed": all_tasks_completed,
                "written_files": written_files,
                "project_path": str(project_path) if project_path else None,
                "enable_iterative_improvement": getattr(runtime, "enable_iterative_improvement", None),
                "required_successful_improvements": getattr(runtime, "required_successful_improvements", None),
            },
            output_summary={
                "will_attempt_iteration": can_iterate and improvement_policy.enabled,
                "project_improvement_policy": improvement_policy.model_dump(mode="json"),
            },
        )
        if can_iterate and improvement_policy.enabled:
            try:
                runtime_controller = getattr(runtime, "runtime_controller", None)
                runtime_state = getattr(runtime_controller, "state", None)
                improvement_result = runtime._run_iterative_improvement(
                    goal=goal,
                    project_path=project_path,
                    written_files=written_files,
                    session_constraints=(
                        runtime_state.session_constraints
                        if runtime_state is not None
                        else None
                    ),
                    session_ingress_state=getattr(self, "_active_session_ingress_state", None),
                    readme_path=(
                        readme_result.output.get("file_path")
                        if readme_result and getattr(readme_result, "output", None) is not None
                        else None
                    ),
                )
            except Exception as exc:
                improvement_result = _interrupted_project_improvement(exc)
            if improvement_result is None:
                self._append_iteration_skip_note("Project improvement skipped: disabled or 0 iterations selected")
        else:
            improvement_result = None
            if not improvement_policy.enabled:
                self._append_iteration_skip_note("Project improvement skipped: disabled by completion policy")
            else:
                self._append_iteration_skip_note(self._iteration_skip_reason(all_tasks_completed, written_files, project_path))
        return readme_result, written_files, project_path, improvement_result

    def _iteration_skip_reason(
        self,
        all_tasks_completed: bool,
        written_files: list[str],
        project_path: Any,
    ) -> str:
        if not all_tasks_completed:
            return "Project improvement skipped: task execution did not complete successfully"
        if not written_files:
            return "Project improvement skipped: no written files detected"
        if not project_path:
            return "Project improvement skipped: project path could not be inferred"
        return "Project improvement skipped"

    def _append_iteration_skip_note(self, reason: str) -> None:
        runtime = self.runtime
        enhanced_ui = getattr(runtime, "enhanced_ui", None)
        if not enhanced_ui or not hasattr(enhanced_ui, "task_graph_state"):
            return
        tasks = list(enhanced_ui.task_graph_state.get("tasks") or [])
        note_id = "project_improvement_skipped"
        if any(task.get("id") == note_id for task in tasks if isinstance(task, dict)):
            return
        tasks.append(
            {
                "id": note_id,
                "description": reason,
                "status": "completed",
                "kind": "note",
            }
        )
        enhanced_ui.set_task_graph_state(tasks=tasks, current_task_id=None)
        if hasattr(enhanced_ui, "log_activity"):
            enhanced_ui.log_activity("info", reason)

    def _update_stats(self, decomposition: Any, improvement_result: dict[str, Any] | None) -> tuple[bool, str | None]:
        runtime = self.runtime
        core_success = all(t.status == TaskStatus.COMPLETED for t in decomposition.subtasks)
        success = core_success
        iteration_error_msg = None
        if improvement_result is not None and not improvement_result.get("success", False):
            iteration_error_msg = runtime._format_iteration_failure(improvement_result)
            if _project_improvement_policy(runtime).controls_top_level_success:
                success = False
        runtime.stats["success"] = success
        runtime.stats["tasks_completed"] = len([t for t in decomposition.subtasks if t.status == TaskStatus.COMPLETED])
        runtime.stats["tasks_failed"] = len([t for t in decomposition.subtasks if t.status == TaskStatus.FAILED])
        runtime.stats["end_time"] = datetime.now()
        return success, iteration_error_msg

    def _set_enhanced_completion_state(
        self,
        success: bool,
        readme_result: Any,
        improvement_result: dict[str, Any] | None,
        iteration_error_msg: str | None,
        execution_failure: dict[str, Any] | None = None,
    ) -> None:
        runtime = self.runtime
        if success:
            success_details = f"Goal completed successfully!\n\nCompleted {runtime.stats['tasks_completed']} tasks"
            if readme_result:
                if readme_result.success and readme_result.output is not None:
                    success_details += f"\nREADME: {readme_result.output.get('file_path')}"
                elif readme_result.error_message:
                    success_details += f"\nREADME generation failed: {readme_result.error_message}"
            if improvement_result:
                if improvement_result.get("validation"):
                    success_details += (
                        f"\nImprovements applied: {improvement_result.get('completed_improvements', 0)}/"
                        f"{improvement_result.get('required_improvements', runtime.required_successful_improvements)}"
                    )
                if iteration_error_msg:
                    success_details += f"\nIteration warning: {iteration_error_msg}"
            runtime.enhanced_ui.set_current_task_state(
                title="Success",
                details=success_details,
                status="completed",
            )
        else:
            failure_details = (
                f"Goal execution failed\n\nCompleted: {runtime.stats['tasks_completed']}, "
                f"Failed: {runtime.stats['tasks_failed']}"
            )
            if iteration_error_msg:
                failure_details = (
                    f"Iteration warning: {iteration_error_msg}\n"
                    f"Completed: {runtime.stats['tasks_completed']}, Failed: {runtime.stats['tasks_failed']}"
                )
            elif execution_failure:
                lines = [
                    execution_failure.get("failure_reason") or "Goal execution failed",
                    "",
                ]
                if execution_failure.get("task_description"):
                    lines.append(f"Task: {execution_failure['task_description']}")
                if execution_failure.get("task_id"):
                    lines.append(f"Task ID: {execution_failure['task_id']}")
                lines.extend(
                    [
                        f"Stage: {execution_failure.get('failure_stage') or 'unknown'}",
                        f"Tool: {execution_failure.get('failed_tool') or 'unknown'}",
                    ]
                )
                if execution_failure.get("failed_call_id"):
                    lines.append(f"Call: {execution_failure['failed_call_id']}")
                if execution_failure.get("failed_step_id"):
                    lines.append(f"Step: {execution_failure['failed_step_id']}")
                if execution_failure.get("file_path"):
                    lines.append(f"File: {execution_failure['file_path']}")
                if execution_failure.get("error_type"):
                    lines.append(f"Error Type: {execution_failure['error_type']}")
                if execution_failure.get("suggested_recovery"):
                    lines.append(f"Recovery: {execution_failure['suggested_recovery']}")
                if execution_failure.get("response_preview"):
                    lines.append(f"Response Preview: {str(execution_failure['response_preview'])[:1000]}")
                failure_details = "\n".join(lines)
            runtime.enhanced_ui.set_current_task_state(
                title="Failed",
                details=failure_details,
                status="failed",
            )

    def _execution_failure_context(self, subtasks: list[Any]) -> dict[str, Any] | None:
        failed_tasks = [
            task
            for task in subtasks
            if getattr(task, "status", None) in {TaskStatus.FAILED, TaskStatus.BLOCKED}
        ]
        if not failed_tasks:
            return None
        task = failed_tasks[0]
        task_result_metadata = getattr(task, "result_metadata", None) or getattr(task, "result", None)
        failure = getattr(task_result_metadata, "failure", None)
        details = getattr(failure, "details", {}) or {}
        tool_loop = details.get("tool_loop") if isinstance(details, dict) else None
        final_error = tool_loop.get("final_error") if isinstance(tool_loop, dict) else None
        final_details = final_error.get("details") if isinstance(final_error, dict) and isinstance(final_error.get("details"), dict) else {}
        input_summary = details.get("input_summary") if isinstance(details, dict) and isinstance(details.get("input_summary"), dict) else {}
        final_input = final_details.get("input_summary") if isinstance(final_details.get("input_summary"), dict) else {}
        tool_name = None
        call_id = None
        step_id = None
        if isinstance(details, dict):
            tool_name = details.get("tool_name") or details.get("failed_tool")
            call_id = details.get("call_id")
            step_id = details.get("step_id")
        if isinstance(final_error, dict):
            tool_name = tool_name or final_details.get("tool_name")
            call_id = call_id or final_details.get("call_id")
            step_id = step_id or final_details.get("step_id")
        if not tool_name and isinstance(tool_loop, dict):
            events = tool_loop.get("events") or []
            for event in reversed(events):
                if isinstance(event, dict) and event.get("event_type") == "error":
                    tool_name = event.get("tool_name")
                    call_id = event.get("call_id")
                    step_id = event.get("step_id")
                    break
        error_type = None
        response_preview = None
        suggested_recovery = None
        file_path = None
        if isinstance(details, dict):
            error_type = (
                details.get("error_type")
                or details.get("failure_error_type")
                or final_details.get("error_type")
                or (final_error.get("error_type") if isinstance(final_error, dict) else None)
            )
            suggested_recovery = (
                details.get("suggested_recovery")
                or final_details.get("suggested_recovery")
                or details.get("recovery_strategy")
                or final_details.get("recovery_strategy")
            )
            response_preview = (
                details.get("response_preview")
                or details.get("response_preview_start")
                or final_details.get("response_preview")
                or final_details.get("response_preview_start")
                or details.get("response_text")
                or final_details.get("response_text")
            )
            file_path = (
                details.get("file_path")
                or final_details.get("file_path")
                or input_summary.get("file_path")
                or final_input.get("file_path")
                or final_details.get("received_path")
            )
        return {
            "task_id": getattr(task, "id", None),
            "task_description": getattr(task, "description", None),
            "failure_stage": (details.get("failure_stage") if isinstance(details, dict) else None) or "Task Executor",
            "failed_tool": tool_name or "tool_event_loop",
            "failed_call_id": call_id,
            "failed_step_id": step_id,
            "failure_reason": getattr(failure, "error_message", None) or getattr(task, "error", None),
            "file_path": file_path,
            "error_type": error_type,
            "suggested_recovery": suggested_recovery,
            "response_preview": response_preview,
        }

    def _build_result(
        self,
        *,
        goal: str,
        semantic: Any,
        decomposition: Any,
        results: list[Any],
        readme_result: Any,
        improvement_result: dict[str, Any] | None,
        iteration_error_msg: str | None,
        success: bool,
        include_final_result: bool,
        final_result: Any | None = None,
        execution_failure: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        runtime = self.runtime
        result = {
            "success": success,
            "core_success": all(
                getattr(task, "status", None) == TaskStatus.COMPLETED
                for task in decomposition.subtasks
            ),
            "goal": goal,
            "semantic_analysis": semantic,
            "decomposition": decomposition,
            "results": results,
            "readme": readme_result,
            "validation": improvement_result.get("validation") if improvement_result else None,
            "evaluation": improvement_result.get("evaluation") if improvement_result else None,
            "completed_improvements": improvement_result.get("completed_improvements", 0) if improvement_result else 0,
            "required_improvements": improvement_result.get("required_improvements", runtime.required_successful_improvements) if improvement_result else runtime.required_successful_improvements,
            "completed_iterations": improvement_result.get("completed_iterations", 0) if improvement_result else 0,
            "required_iterations": improvement_result.get("required_iterations", runtime.required_successful_improvements) if improvement_result else runtime.required_successful_improvements,
            "improvement_report": improvement_result.get("improvement_report", {}) if improvement_result else {},
            "iterations": improvement_result.get("iterations", []) if improvement_result else [],
            "partial_success": improvement_result.get("partial_success", False) if improvement_result else False,
            "iteration_error": iteration_error_msg,
            "project_improvement_policy": _project_improvement_policy(runtime).model_dump(mode="json"),
            "project_improvement_status": _project_improvement_status(
                _project_improvement_policy(runtime), improvement_result
            ).value,
            "failure_stage": (
                improvement_result.get("failure_stage")
                if improvement_result
                else (execution_failure or {}).get("failure_stage")
            ),
            "failed_iteration": improvement_result.get("failed_iteration") if improvement_result else None,
            "failed_tool": (
                improvement_result.get("failed_tool")
                if improvement_result
                else (execution_failure or {}).get("failed_tool")
            ),
            "failed_call_id": (execution_failure or {}).get("failed_call_id"),
            "failed_step_id": (execution_failure or {}).get("failed_step_id"),
            "task_id": (execution_failure or {}).get("task_id"),
            "task_description": (execution_failure or {}).get("task_description"),
            "file_path": (execution_failure or {}).get("file_path"),
            "error_type": (
                improvement_result.get("error_type")
                if improvement_result
                else (execution_failure or {}).get("error_type")
            ),
            "suggested_recovery": (execution_failure or {}).get("suggested_recovery"),
            "response_preview": (execution_failure or {}).get("response_preview"),
            "failure_reason": (
                improvement_result.get("failure_reason")
                if improvement_result
                else (execution_failure or {}).get("failure_reason")
            ),
            "retry_attempted": improvement_result.get("retry_attempted", False) if improvement_result else False,
            "retry_history": improvement_result.get("retry_history", []) if improvement_result else [],
            "remaining_goals": improvement_result.get("remaining_goals", []) if improvement_result else [],
            "stats": runtime.stats,
        }
        if include_final_result:
            result["final_result"] = final_result
        if not success and result.get("failure_reason"):
            self._log(
                "session_failure_context",
                output_summary={
                    "task_id": (execution_failure or {}).get("task_id"),
                    "failure_stage": result.get("failure_stage"),
                    "failed_tool": result.get("failed_tool"),
                    "failed_call_id": result.get("failed_call_id"),
                    "failed_step_id": result.get("failed_step_id"),
                    "file_path": result.get("file_path"),
                    "error_type": result.get("error_type"),
                    "suggested_recovery": result.get("suggested_recovery"),
                    "failure_reason": result.get("failure_reason"),
                },
                success=False,
                error=result.get("failure_reason"),
                level="ERROR",
            )
        return result

    def _log(
        self,
        event_type: str,
        *,
        success: bool | None = None,
        input_summary: Any | None = None,
        output_summary: Any | None = None,
        error: str | None = None,
        level: str | None = None,
    ) -> None:
        logger = getattr(self.runtime, "logger", None)
        if not logger:
            return
        logger.log_structured_event(
            source_type="module",
            source_name="autonomous_iteration.runtime_controller",
            phase="session_execution",
            event_type=event_type,
            session_id=getattr(self.runtime, "session_id", None) or "unknown",
            turn_id=1,
            success=success,
            input_summary=input_summary,
            output_summary=output_summary,
            error=error,
            level=level,
        )


class AgentRuntimeController:
    """High-level phase controller wrapping the existing autopilot execution path."""

    def __init__(
        self,
        runtime: Any,
        *,
        router: ToolRouter | None = None,
        state_updater: StateUpdater | None = None,
        runtime_guard: RuntimeGuard | None = None,
        file_selector: FileSelector | None = None,
        edit_guard: EditGuard | None = None,
        verifier: RuntimeVerifier | None = None,
        reporter: RuntimeReporter | None = None,
        session_executor: Any | None = None,
        checkpoint_store: RuntimeCheckpointStore | Any | None = None,
        checkpoint_fault_injector: Callable[
            [CheckpointFaultPoint, CheckpointBoundary, RuntimeCheckpointMetadata],
            None,
        ]
        | None = None,
        finalization_fault_injector: Callable[
            [FinalizationFaultPoint, RuntimeFinalizationCursor],
            None,
        ]
        | None = None,
    ) -> None:
        self.runtime = runtime
        self.runtime_guard = runtime_guard or RuntimeGuard()
        self.router = router or ToolRouter(getattr(runtime, "tool_registry", None), guard=self.runtime_guard)
        self._active_task_id: str = ""
        self.state_updater = state_updater or StateUpdater(
            self.runtime_guard,
            state_event_sink=self._handle_state_event_change,
            result_applied_sink=self._handle_tool_result_applied,
        )
        self.file_selector = file_selector or FileSelector()
        self.edit_guard = edit_guard or EditGuard()
        self.verifier = verifier or RuntimeVerifier()
        self.reporter = reporter or RuntimeReporter()
        self.session_executor = session_executor or _RuntimeSessionExecutor(
            runtime,
            session_cursor_sink=self._persist_session_cursor,
            session_bootstrap_sink=self._persist_session_bootstrap,
        )
        self.checkpoint_store = checkpoint_store
        self._checkpoint_fault_injector = checkpoint_fault_injector
        self._finalization_fault_injector = finalization_fault_injector
        self.state: RuntimeStateMetadata | None = None
        self._checkpointing_enabled = False
        self._checkpoint_generation = 0
        self._checkpoint_status = CheckpointStatus.DISABLED
        self._checkpoint_run_id = ""
        self._checkpoint_context: dict[str, Any] = {}
        self._resume_attempt_id: str | None = None
        self._resume_source_checkpoint_id: str | None = None
        self._active_tool_checkpoint: dict[str, Any] = {}
        self._pending_verification: VerificationPlanMetadata | None = None
        self._active_session_cursor: SessionExecutionCursor | None = None
        self._active_session_bootstrap: SessionBootstrapCursor | None = None
        self._active_session_ingress_state: SessionIngressState | None = None
        self._pending_llm_request: PendingLLMRequest | None = None
        self._llm_replay_entries: list[LLMReplayEntry] = []
        self._read_tool_replay_entries: list[ReadToolReplayEntry] = []
        self._active_prompt_context_snapshot: RuntimePromptContextSnapshot | None = None
        self._prompt_context_replay_enabled = False
        self._active_finalization_cursor: RuntimeFinalizationCursor | None = None
        self._run_lease_handle: Any | None = None
        llm_client = getattr(runtime, "llm_client", None)
        set_recovery_handler = getattr(llm_client, "set_recovery_handler", None)
        if callable(set_recovery_handler):
            set_recovery_handler(self)
        self._configure_prompt_context_checkpointing()

    def run(self, goal: str, context: dict[str, Any] | None = None, *, mode: str = "standard") -> dict[str, Any]:
        """Run a goal while maintaining explicit runtime state."""
        context = context or {}
        self._active_session_cursor = None
        self._active_session_bootstrap = None
        self._active_session_ingress_state = None
        self._pending_llm_request = None
        self._llm_replay_entries = []
        self._read_tool_replay_entries = []
        self._active_prompt_context_snapshot = None
        self._prompt_context_replay_enabled = False
        self._active_finalization_cursor = None
        self._resume_source_checkpoint_id = None
        reset_llm_ordinals = getattr(getattr(self.runtime, "llm_client", None), "reset_recovery_ordinals", None)
        if callable(reset_llm_ordinals):
            reset_llm_ordinals()
        raw_session_ingress = context.get("session_ingress_state")
        if raw_session_ingress is not None and not isinstance(raw_session_ingress, SessionIngressState):
            raise TypeError("session_ingress_state must be a validated SessionIngressState")
        if isinstance(raw_session_ingress, SessionIngressState):
            for identity_key in ("conversation_id", "session_id"):
                supplied_identity = str(context.get(identity_key) or "").strip()
                if supplied_identity and supplied_identity != raw_session_ingress.identity.conversation_id:
                    raise ValueError("session ingress conversation identity mismatch")
            supplied_project = str(context.get("project_path") or "").strip()
            if supplied_project:
                ingress_project = Path(raw_session_ingress.identity.project_root).expanduser().resolve(strict=False)
                requested_project = Path(supplied_project).expanduser().resolve(strict=False)
                if ingress_project != requested_project:
                    raise ValueError("session ingress project identity mismatch")
        raw_session_constraints = context.get("session_constraints")
        if raw_session_constraints is not None and not isinstance(
            raw_session_constraints, SessionConstraintState
        ):
            raise TypeError("session_constraints must be a validated SessionConstraintState")
        if isinstance(raw_session_constraints, SessionConstraintState):
            requested_session_id = str(
                context.get("conversation_id")
                or context.get("session_id")
                or getattr(self.runtime, "session_id", "")
                or context.get("task_id")
                or ""
            ).strip()
            if (
                raw_session_constraints.entries
                and requested_session_id
                and raw_session_constraints.session_id != requested_session_id
            ):
                raise ValueError("session constraint state belongs to a different session")
            requested_project_root = str(context.get("project_path") or "").strip()
            if raw_session_constraints.project_root and requested_project_root:
                if Path(raw_session_constraints.project_root).expanduser().resolve(strict=False) != Path(
                    requested_project_root
                ).expanduser().resolve(strict=False):
                    raise ValueError("session constraint state belongs to a different project root")
            if requested_project_root and not raw_session_constraints.project_root:
                raw_session_constraints = raw_session_constraints.model_copy(
                    update={"project_root": requested_project_root}
                )
        if isinstance(raw_session_ingress, SessionIngressState):
            if isinstance(raw_session_constraints, SessionConstraintState) and (
                raw_session_ingress.session_constraints != raw_session_constraints
            ):
                raise ValueError("session ingress and runtime constraint state differ")
            raw_session_constraints = raw_session_ingress.session_constraints
            self._active_session_ingress_state = raw_session_ingress.model_copy(deep=True)
        state = RuntimeStateMetadata(
            goal=goal,
            project_improvement_policy=_project_improvement_policy(self.runtime),
            session_constraints=(
                raw_session_constraints.model_copy(deep=True)
                if isinstance(raw_session_constraints, SessionConstraintState)
                else SessionConstraintState()
            ),
            # The ingress snapshot is passed separately to the checkpoint
            # writer; RuntimeStateMetadata remains the execution owner.
        )
        self.state = state
        self._active_task_id = str(context.get("task_id") or getattr(self.runtime, "session_id", "") or "")
        read_only_mode = apply_read_only_runtime_mode(
            state,
            goal,
            tags=[str(tag) for tag in context.get("tags") or []],
            task_type=str(context.get("task_type") or ""),
        )
        state.add_fact(f"User goal captured: {goal}")
        if context.get("project_path"):
            state.add_fact(f"Project path: {context['project_path']}")
            state.add_candidate_file(str(context["project_path"]), "project_path provided by caller")
        state.phase = AgentPhase.UNDERSTAND_PROJECT if context.get("project_path") else AgentPhase.UNDERSTAND_TASK
        self._emit_runtime_phase_change("", _phase_value(state.phase), state.verification_status, state.completion_reason, state)
        self._configure_checkpointing(context, read_only_mode=read_only_mode)
        if self._checkpointing_enabled and self.checkpoint_store is not None and self._checkpoint_run_id:
            acquire_lease = getattr(self.checkpoint_store, "try_acquire_run_lease", None)
            if callable(acquire_lease):
                self._run_lease_handle = acquire_lease(self._checkpoint_run_id)
                if self._run_lease_handle is None:
                    raise RuntimeError("runtime run lease is already active")
        self._persist_checkpoint(
            state,
            reason="task state initialized",
            safe_boundary=CheckpointBoundary.TASK_NORMALIZED,
        )

        try:
            result = self.session_executor.run(goal, context, mode=mode)
        except Exception as exc:
            state.phase = AgentPhase.RECOVER
            state.completion_reason = str(exc)
            self._persist_checkpoint(
                state,
                reason=f"runtime stopped after {type(exc).__name__}",
                safe_boundary=CheckpointBoundary.CONTROLLED_STOP,
            )
            hooks = getattr(self.runtime, "runtime_diagnostics_hooks", None)
            if hooks:
                hooks.on_failure(
                    FailureMetadata(
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        recoverable=False,
                    ),
                    source="runtime_failure",
                    task_id=str(context.get("task_id") or getattr(self.runtime, "session_id", "") or ""),
                )
            self._release_run_lease()
            raise

        if isinstance(result, dict):
            self._absorb_session_result(state, result)
            runtime_result = self._finalize_runtime(state, result)
            self._release_run_lease()
            return runtime_result

        state.phase = AgentPhase.SUMMARIZE
        state.completion_reason = "runtime session returned non-dict result"
        runtime_result = self._finalize_runtime(
            state,
            {"result": result, "success": bool(result)},
        )
        self._release_run_lease()
        return runtime_result

    def resume(
        self,
        run_id: str,
        checkpoint_id: str,
        context: dict[str, Any] | None = None,
        *,
        mode: str = "standard",
    ) -> dict[str, Any]:
        """Resume one explicitly identified read-only checkpoint after preflight."""
        context = dict(context or {})
        store = self.checkpoint_store
        if store is None:
            hooks = getattr(self.runtime, "runtime_diagnostics_hooks", None)
            recorder = getattr(hooks, "recorder", None)
            if recorder is not None:
                store = RuntimeCheckpointStore(recorder.trajectory_dir)
                self.checkpoint_store = store
        if store is None:
            raise ValueError("resume requires a checkpoint store")
        self._run_lease_handle = store.try_acquire_run_lease(run_id)
        if self._run_lease_handle is None:
            return self._resume_active_run(store, run_id, checkpoint_id, context)
        checkpoint = store.load(run_id, checkpoint_id)
        if checkpoint is None:
            runtime_result = self._resume_unavailable_checkpoint(store, run_id, checkpoint_id, context)
            self._release_run_lease()
            return runtime_result

        self._restore_checkpoint_ingress(checkpoint, context)

        self.state = checkpoint.runtime_state.model_copy(deep=True)
        self._active_task_id = checkpoint.root_task_id
        self._checkpointing_enabled = True
        latest_checkpoint = store.load_latest(run_id)
        self._checkpoint_generation = max(
            checkpoint.generation,
            latest_checkpoint.generation if latest_checkpoint is not None else checkpoint.generation,
        )
        self._checkpoint_status = CheckpointStatus.DURABLE
        self._checkpoint_run_id = checkpoint.run_id
        self._checkpoint_context = context
        self._resume_attempt_id = uuid.uuid4().hex
        self._resume_source_checkpoint_id = checkpoint.checkpoint_id
        self._active_session_cursor = (
            checkpoint.session_cursor.model_copy(deep=True)
            if checkpoint.session_cursor is not None
            else None
        )
        self._active_session_bootstrap = (
            checkpoint.session_bootstrap.model_copy(deep=True)
            if checkpoint.session_bootstrap is not None
            else None
        )
        self._pending_llm_request = (
            checkpoint.pending_llm_request.model_copy(deep=True)
            if checkpoint.pending_llm_request is not None
            else None
        )
        self._llm_replay_entries = [
            entry.model_copy(deep=True) for entry in checkpoint.llm_replay_entries
        ]
        self._read_tool_replay_entries = [
            entry.model_copy(deep=True) for entry in checkpoint.read_tool_replay_entries
        ]
        self._active_prompt_context_snapshot = (
            checkpoint.prompt_context_snapshot.model_copy(deep=True)
            if checkpoint.prompt_context_snapshot is not None
            else None
        )
        self._prompt_context_replay_enabled = self._active_prompt_context_snapshot is not None
        self._active_finalization_cursor = (
            checkpoint.finalization_cursor.model_copy(deep=True)
            if checkpoint.finalization_cursor is not None
            else None
        )
        reset_llm_ordinals = getattr(getattr(self.runtime, "llm_client", None), "reset_recovery_ordinals", None)
        if callable(reset_llm_ordinals):
            reset_llm_ordinals()
        setattr(self.runtime, "session_id", checkpoint.session_id)
        if self._active_session_ingress_state is not None:
            setattr(
                self.runtime,
                "conversation_id",
                self._active_session_ingress_state.identity.conversation_id,
            )
        self.state.recovery_status = RecoveryStatus.ASSESSMENT_PENDING
        self.state.recovery_reason_code = None
        self.state.active_resume_attempt_id = self._resume_attempt_id

        decision = self._resume_preflight(checkpoint, context)
        self._apply_resume_assessment(decision)
        self._emit_resume_decision(decision)
        if decision.recoverability in {
            Recoverability.RECOVERABLE_AFTER_ACTION,
            Recoverability.NOT_RECOVERABLE,
        }:
            runtime_result = self._resume_terminal_result(decision, success=False, status="blocked")
            self._release_run_lease()
            return runtime_result
        if decision.recoverability == Recoverability.ALREADY_COMPLETE:
            runtime_result = self._resume_terminal_result(decision, success=True, status="already_completed")
            self._release_run_lease()
            return runtime_result

        self.state.budget.consume_recovery_round()
        context.setdefault("task_id", checkpoint.root_task_id)
        context["run_id"] = checkpoint.run_id
        context["checkpointing_enabled"] = True
        self._checkpoint_context = dict(context)
        if decision.recovery_mode == RecoveryMode.FINALIZE_FROM_CHECKPOINT:
            self.state.recovery_status = RecoveryStatus.RESUMING
            try:
                return self._resume_finalization(checkpoint, decision)
            finally:
                self._release_run_lease()
        if decision.recovery_mode == RecoveryMode.RECONCILE_THEN_RESUME:
            self.state.recovery_status = RecoveryStatus.RESUMING
            try:
                return self._resume_file_mutation(checkpoint, decision, context)
            finally:
                self._release_run_lease()
        self.state.recovery_status = RecoveryStatus.RESUMING
        self._persist_checkpoint(
            self.state,
            reason="resume preflight passed",
            safe_boundary=checkpoint.safe_boundary,
            tool_name=checkpoint.tool_name,
            step_id=checkpoint.step_id,
            mutation_class=checkpoint.mutation_class,
            side_effect_state=checkpoint.side_effect_state,
            session_cursor=checkpoint.session_cursor,
        )
        try:
            if checkpoint.session_cursor is not None:
                result = self.session_executor.run(
                    self.state.goal,
                    context,
                    mode=mode,
                    resume_cursor=checkpoint.session_cursor,
                )
            elif checkpoint.session_bootstrap is not None:
                result = self.session_executor.run(
                    self.state.goal,
                    context,
                    mode=mode,
                    resume_bootstrap=checkpoint.session_bootstrap,
                )
            else:
                result = self.session_executor.run(self.state.goal, context, mode=mode)
        except Exception as exc:
            self.state.phase = AgentPhase.RECOVER
            self.state.recovery_status = RecoveryStatus.RECOVERY_FAILED
            self.state.completion_reason = str(exc)
            self._persist_checkpoint(
                self.state,
                reason=f"resumed runtime stopped after {type(exc).__name__}",
                safe_boundary=CheckpointBoundary.CONTROLLED_STOP,
            )
            self._release_run_lease()
            raise
        if not isinstance(result, dict):
            result = {"result": result, "success": bool(result)}
        self._absorb_session_result(self.state, result)
        self.state.recovery_status = (
            RecoveryStatus.RECOVERED if bool(result.get("success")) else RecoveryStatus.RECOVERY_FAILED
        )
        self._persist_checkpoint(
            self.state,
            reason="resumed runtime session stopped",
            safe_boundary=CheckpointBoundary.CONTROLLED_STOP,
        )
        runtime_result = self._runtime_result(self.state, result)
        runtime_result["resume_decision"] = decision.to_json_dict()
        runtime_result["resume_status"] = "resumed"
        self._release_run_lease()
        return runtime_result

    def _restore_checkpoint_ingress(
        self,
        checkpoint: RuntimeCheckpointMetadata,
        context: dict[str, Any],
    ) -> None:
        """Restore the conversation owner before resume prompt/task construction."""

        checkpoint_ingress = checkpoint.session_ingress_state
        supplied = context.get("session_ingress_state")
        if supplied is not None and not isinstance(supplied, SessionIngressState):
            raise TypeError("session_ingress_state must be a validated SessionIngressState")
        if checkpoint_ingress is not None:
            if supplied is not None and supplied != checkpoint_ingress:
                raise ValueError("resume session ingress does not match checkpoint identity")
            restored = checkpoint_ingress.model_copy(deep=True)
            context.setdefault("session_ingress_state", restored)
            context.setdefault("session_constraints", restored.session_constraints.model_copy(deep=True))
            context.setdefault("conversation_id", restored.identity.conversation_id)
            context.setdefault("run_id", restored.identity.run_id)
            context.setdefault("project_path", restored.identity.project_root)
            self._active_session_ingress_state = restored
            self._sync_runtime_resume_context(context)
            return
        if isinstance(supplied, SessionIngressState):
            runtime_constraints = checkpoint.runtime_state.session_constraints
            if supplied.session_constraints != runtime_constraints:
                raise ValueError("resume session ingress constraints differ from checkpoint runtime state")
            self._active_session_ingress_state = supplied.model_copy(deep=True)
            self._sync_runtime_resume_context(context)

    def _sync_runtime_resume_context(self, context: dict[str, Any]) -> None:
        """Expose restored typed context to the runtime's child-task builder."""

        current = getattr(self.runtime, "_current_execution_context", None)
        if isinstance(current, dict):
            current.update(context)

    def _resume_active_run(
        self,
        store: RuntimeCheckpointStore,
        run_id: str,
        checkpoint_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Fail closed without writing when another process owns the run lease."""
        checkpoint = store.load(run_id, checkpoint_id)
        hooks = getattr(self.runtime, "runtime_diagnostics_hooks", None)
        recorder = getattr(hooks, "recorder", None)
        run = recorder.load_run(run_id) if recorder is not None else None
        if checkpoint is not None:
            self.state = checkpoint.runtime_state.model_copy(deep=True)
            root_task_id = checkpoint.root_task_id
            session_id = checkpoint.session_id
            safe_boundary = checkpoint.safe_boundary
        else:
            root_task_id = str(getattr(run, "task_id", "") or context.get("task_id") or "")
            session_id = str(getattr(run, "session_id", "") or "")
            safe_boundary = "unavailable"
            goal = str(getattr(run, "goal", "") or getattr(run, "raw_input", "") or "Resume active run")
            self.state = RuntimeStateMetadata(
                goal=goal,
                phase=AgentPhase.RECOVER,
                project_improvement_policy=_project_improvement_policy(self.runtime),
            )
        self._resume_attempt_id = uuid.uuid4().hex
        self._checkpoint_status = CheckpointStatus.PENDING
        decision = RuntimeResumeDecisionMetadata(
            checkpoint_id=checkpoint_id,
            run_id=run_id,
            root_task_id=root_task_id,
            session_id=session_id,
            resume_attempt_id=self._resume_attempt_id,
            decision="blocked",
            recoverability=Recoverability.RECOVERABLE_AFTER_ACTION,
            recovery_mode=RecoveryMode.RETRY_FROM_CHECKPOINT,
            automation_policy=RecoveryAutomationPolicy.FORBIDDEN,
            reason_code=RecoveryReasonCode.RUN_LEASE_ACTIVE,
            safe_boundary=safe_boundary,
            evidence_refs=[checkpoint_id],
            blockers=[
                RecoveryBlocker(
                    reason_code=RecoveryReasonCode.RUN_LEASE_ACTIVE,
                    resolvable=True,
                    requires_user_action=False,
                    evidence_refs=[checkpoint_id],
                    required_action=RecoveryFallbackAction.RETRY_LATER,
                )
            ],
            fallback=RecoveryFallback(
                action=RecoveryFallbackAction.RETRY_LATER,
                reason_code=RecoveryReasonCode.RUN_LEASE_ACTIVE,
                preserve_original_run=True,
                instructions="Wait for the active runtime writer to release the run lease.",
            ),
            reason="another runtime process still owns the run lease",
            next_action="retry after the active run stops",
        )
        self._apply_resume_assessment(decision)
        # Do not emit to the shared trajectory while another writer owns the run.
        return self._resume_terminal_result(decision, success=False, status="waiting_retry")

    def _release_run_lease(self) -> None:
        store = self.checkpoint_store
        if store is not None and self._run_lease_handle is not None:
            release_lease = getattr(store, "release_run_lease", None)
            if callable(release_lease):
                release_lease(self._run_lease_handle)
        self._run_lease_handle = None

    def _resume_unavailable_checkpoint(
        self,
        store: RuntimeCheckpointStore,
        run_id: str,
        checkpoint_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a typed assessment when the requested checkpoint cannot be loaded."""
        self._resume_attempt_id = uuid.uuid4().hex
        fallback_checkpoint = store.load_latest(run_id)
        hooks = getattr(self.runtime, "runtime_diagnostics_hooks", None)
        recorder = getattr(hooks, "recorder", None)
        run = recorder.load_run(run_id) if recorder is not None else None

        if fallback_checkpoint is not None and fallback_checkpoint.checkpoint_id != checkpoint_id:
            self.state = fallback_checkpoint.runtime_state.model_copy(deep=True)
            self._active_task_id = fallback_checkpoint.root_task_id
            self._checkpointing_enabled = True
            self._checkpoint_generation = fallback_checkpoint.generation
            self._checkpoint_status = CheckpointStatus.FALLBACK_AVAILABLE
            self._checkpoint_run_id = fallback_checkpoint.run_id
            self._checkpoint_context = dict(context)
            setattr(self.runtime, "session_id", fallback_checkpoint.session_id)
            decision = RuntimeResumeDecisionMetadata(
                checkpoint_id=checkpoint_id,
                run_id=fallback_checkpoint.run_id,
                root_task_id=fallback_checkpoint.root_task_id,
                session_id=fallback_checkpoint.session_id,
                resume_attempt_id=self._resume_attempt_id,
                decision="blocked",
                recoverability=Recoverability.RECOVERABLE_AFTER_ACTION,
                recovery_mode=RecoveryMode.USER_ASSISTED_RESUME,
                automation_policy=RecoveryAutomationPolicy.APPROVAL_REQUIRED,
                reason_code=RecoveryReasonCode.PREVIOUS_CHECKPOINT_AVAILABLE,
                safe_boundary=fallback_checkpoint.safe_boundary,
                evidence_refs=[checkpoint_id, fallback_checkpoint.checkpoint_id],
                blockers=[
                    RecoveryBlocker(
                        reason_code=RecoveryReasonCode.PREVIOUS_CHECKPOINT_AVAILABLE,
                        resolvable=True,
                        requires_user_action=True,
                        evidence_refs=[checkpoint_id, fallback_checkpoint.checkpoint_id],
                        required_action=RecoveryFallbackAction.USE_PREVIOUS_VALID_CHECKPOINT,
                        target_ref=fallback_checkpoint.checkpoint_id,
                    )
                ],
                fallback=RecoveryFallback(
                    action=RecoveryFallbackAction.USE_PREVIOUS_VALID_CHECKPOINT,
                    reason_code=RecoveryReasonCode.PREVIOUS_CHECKPOINT_AVAILABLE,
                    requires_user_authorization=True,
                    instructions="Explicitly resume the previous valid checkpoint generation.",
                ),
                next_checkpoint_id=fallback_checkpoint.checkpoint_id,
                reason="requested checkpoint is invalid; a previous valid generation is available",
                next_action="confirm the previous valid checkpoint before resuming",
            )
        else:
            checkpoint_was_present = store.checkpoint_exists(run_id, checkpoint_id)
            reason_code = (
                RecoveryReasonCode.RUN_NOT_FOUND
                if run is None
                else RecoveryReasonCode.CHECKPOINT_CORRUPT
                if checkpoint_was_present
                else RecoveryReasonCode.CHECKPOINT_MISSING
            )
            root_task_id = str(getattr(run, "task_id", "") or context.get("task_id") or "")
            session_id = str(getattr(run, "session_id", "") or "")
            goal = str(getattr(run, "goal", "") or getattr(run, "raw_input", "") or "Resume unavailable run")
            self.state = RuntimeStateMetadata(
                goal=goal,
                phase=AgentPhase.RECOVER,
                project_improvement_policy=_project_improvement_policy(self.runtime),
            )
            self._active_task_id = root_task_id
            self._checkpointing_enabled = False
            self._checkpoint_status = CheckpointStatus.UNAVAILABLE
            self._checkpoint_run_id = run_id
            self._checkpoint_context = dict(context)
            decision = RuntimeResumeDecisionMetadata(
                checkpoint_id=checkpoint_id,
                run_id=run_id,
                root_task_id=root_task_id,
                session_id=session_id,
                resume_attempt_id=self._resume_attempt_id,
                decision="blocked",
                recoverability=Recoverability.NOT_RECOVERABLE,
                recovery_mode=RecoveryMode.NONE,
                automation_policy=RecoveryAutomationPolicy.FORBIDDEN,
                reason_code=reason_code,
                safe_boundary="unavailable",
                evidence_refs=[checkpoint_id],
                blockers=[
                    RecoveryBlocker(
                        reason_code=reason_code,
                        resolvable=False,
                        evidence_refs=[checkpoint_id],
                        required_action=RecoveryFallbackAction.TERMINATE_PRESERVING_EVIDENCE,
                    )
                ],
                fallback=RecoveryFallback(
                    action=RecoveryFallbackAction.TERMINATE_PRESERVING_EVIDENCE,
                    reason_code=reason_code,
                    preserve_original_run=True,
                    instructions="Preserve available run evidence and do not replay unknown work.",
                ),
                reason="requested run or checkpoint is unavailable",
                next_action="preserve evidence and inspect the requested identity",
            )

        assert self.state is not None
        self.state.recovery_status = RecoveryStatus.ASSESSMENT_PENDING
        self.state.active_resume_attempt_id = self._resume_attempt_id
        self._apply_resume_assessment(decision)
        self._emit_resume_decision(decision)
        return self._resume_terminal_result(decision, success=False, status="recovery_unavailable")

    def _resume_preflight(
        self,
        checkpoint: RuntimeCheckpointMetadata,
        context: dict[str, Any],
    ) -> RuntimeResumeDecisionMetadata:
        assert self.state is not None
        project_root = str(context.get("project_path") or context.get("cwd") or "").strip()
        project_drift: list[str] = []
        project_root_mismatch = False
        is_file_mutation = checkpoint.mutation_class == "mutating" and checkpoint.tool_name in WRITE_TOOLS
        replay_references = [entry.response_artifact for entry in checkpoint.llm_replay_entries]
        legacy_llm_identity_unbound = (
            checkpoint.pending_llm_request is not None
            and checkpoint.pending_llm_request.hash_version
            == LLMRequestHashVersion.LEGACY_UNBOUND_V1
        ) or any(
            entry.hash_version == LLMRequestHashVersion.LEGACY_UNBOUND_V1
            for entry in checkpoint.llm_replay_entries
        )
        replay_references.extend(entry.result_artifact for entry in checkpoint.read_tool_replay_entries)
        if checkpoint.prompt_context_snapshot is not None:
            replay_references.append(checkpoint.prompt_context_snapshot.context_artifact)
            replay_references.extend(
                binding.artifact
                for binding in checkpoint.prompt_context_snapshot.compaction_bindings
            )
        artifact_loader = getattr(self.checkpoint_store, "load_recovery_artifact", None)
        replay_artifacts_valid = not replay_references or (
            callable(artifact_loader)
            and all(
                artifact_loader(checkpoint.run_id, reference) is not None
                for reference in replay_references
            )
        )
        if not project_root:
            project_drift.append("resume request did not provide project_path or cwd")
            project_root_mismatch = True
        elif str(Path(project_root).expanduser().resolve()) != str(
            Path(checkpoint.project_fingerprint.project_root).expanduser().resolve()
        ):
            project_drift.append("project root differs from checkpoint")
            project_root_mismatch = True
        elif not is_file_mutation:
            current_fingerprint = self._build_project_fingerprint(
                project_root=project_root,
                cwd=str(context.get("cwd") or project_root),
            )
            for field_name, label in (
                ("git_repository", "Git repository"),
                ("git_head", "Git HEAD"),
                ("git_branch", "Git branch"),
                ("git_status_hash", "Git worktree status"),
            ):
                expected = getattr(checkpoint.project_fingerprint, field_name)
                current = getattr(current_fingerprint, field_name)
                if expected is not None and expected != current:
                    project_drift.append(f"{label} differs from checkpoint")

        if project_root and not project_root_mismatch:
            expected_environment_id = checkpoint.project_fingerprint.environment_id
            expected_interpreter = checkpoint.project_fingerprint.interpreter
            current_interpreter, current_environment_id = self._checkpoint_environment_binding(project_root)
            requires_legacy_python_environment = (
                not expected_environment_id
                and not expected_interpreter
                and checkpoint.pending_verification is not None
                and any(
                    self._command_requires_python_environment(command)
                    for command in checkpoint.pending_verification.commands
                )
            )
            if (expected_environment_id or expected_interpreter or requires_legacy_python_environment) and (
                not current_environment_id or not current_interpreter
            ):
                project_drift.append("Project environment is not ready for resume")
            else:
                if expected_environment_id and expected_environment_id != current_environment_id:
                    project_drift.append("Python environment identity differs from checkpoint")
                if expected_interpreter and str(Path(expected_interpreter).expanduser().resolve()) != str(
                    Path(str(current_interpreter)).expanduser().resolve()
                ):
                    project_drift.append("Python interpreter differs from checkpoint")

        decision: Literal["exact_resume", "reconcile_then_resume", "replan", "blocked"] = "exact_resume"
        recoverability = Recoverability.RECOVERABLE_NOW
        recovery_mode = RecoveryMode.EXACT_RESUME
        automation_policy = RecoveryAutomationPolicy.AUTOMATIC_ALLOWED
        reason_code = RecoveryReasonCode.CHECKPOINT_VALID
        blockers: list[RecoveryBlocker] = []
        fallback = RecoveryFallback(
            action=RecoveryFallbackAction.NONE,
            reason_code=RecoveryReasonCode.CHECKPOINT_VALID,
        )
        reason = "checkpoint is a supported read-only safe boundary"
        next_action = "continue runtime session"
        requested_task_id = str(context.get("task_id") or "").strip()
        if requested_task_id and requested_task_id != checkpoint.root_task_id:
            decision = "blocked"
            recoverability = Recoverability.RECOVERABLE_AFTER_ACTION
            recovery_mode = RecoveryMode.USER_ASSISTED_RESUME
            automation_policy = RecoveryAutomationPolicy.APPROVAL_REQUIRED
            reason_code = RecoveryReasonCode.ROOT_TASK_MISMATCH
            blockers = [
                RecoveryBlocker(
                    reason_code=reason_code,
                    resolvable=True,
                    requires_user_action=True,
                    evidence_refs=[checkpoint.checkpoint_id],
                    required_action=RecoveryFallbackAction.REQUEST_USER_INPUT,
                    target_ref=requested_task_id,
                )
            ]
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.REQUEST_USER_INPUT,
                reason_code=reason_code,
                requires_user_authorization=True,
                instructions="Select the checkpoint that belongs to the requested root task.",
            )
            reason = "resume request root task does not match the checkpoint"
            next_action = "select the checkpoint for the requested root task"
        elif project_drift:
            decision = "blocked"
            recoverability = Recoverability.RECOVERABLE_AFTER_ACTION
            recovery_mode = RecoveryMode.USER_ASSISTED_RESUME
            automation_policy = RecoveryAutomationPolicy.MANUAL_ONLY
            reason_code = (
                RecoveryReasonCode.PROJECT_ROOT_MISMATCH
                if project_root_mismatch
                else RecoveryReasonCode.PROJECT_DRIFT_CONFLICTING
            )
            blockers = [
                RecoveryBlocker(
                    reason_code=reason_code,
                    resolvable=True,
                    requires_user_action=True,
                    evidence_refs=[checkpoint.checkpoint_id],
                    required_action=RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION,
                    target_ref=project_root or checkpoint.project_fingerprint.project_root,
                )
            ]
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION,
                reason_code=reason_code,
                requires_user_authorization=True,
                instructions="Review project identity and drift before another resume attempt.",
            )
            reason = "project identity does not match the checkpoint"
            next_action = "review project drift"
        elif (
            checkpoint.finalization_cursor is not None
            and checkpoint.finalization_cursor.stage != RuntimeFinalizationStage.RUN_FINALIZED
        ):
            recoverability = Recoverability.RECOVERABLE_NOW
            recovery_mode = RecoveryMode.FINALIZE_FROM_CHECKPOINT
            reason_code = RecoveryReasonCode.FINALIZATION_INCOMPLETE
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.NONE,
                reason_code=reason_code,
            )
            reason = "completed runtime state has an incomplete durable finalization cursor"
            next_action = "continue finalization without re-executing session tasks"
        elif (
            checkpoint.finalization_cursor is not None
            and checkpoint.finalization_cursor.stage == RuntimeFinalizationStage.RUN_FINALIZED
            and checkpoint.safe_boundary == CheckpointBoundary.RUNTIME_FINALIZED
        ):
            recoverability = Recoverability.ALREADY_COMPLETE
            recovery_mode = RecoveryMode.RETURN_COMPLETED
            reason_code = RecoveryReasonCode.CHECKPOINT_ALREADY_COMPLETE
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.NONE,
                reason_code=reason_code,
            )
            reason = "checkpoint contains durable report and run-finalization evidence"
            next_action = "return the persisted completed result"
        elif legacy_llm_identity_unbound:
            decision = "blocked"
            recoverability = Recoverability.NOT_RECOVERABLE
            recovery_mode = RecoveryMode.NONE
            automation_policy = RecoveryAutomationPolicy.APPROVAL_REQUIRED
            reason_code = RecoveryReasonCode.LEGACY_LLM_IDENTITY_UNBOUND
            blockers = [
                RecoveryBlocker(
                    reason_code=reason_code,
                    resolvable=False,
                    evidence_refs=[checkpoint.checkpoint_id],
                    required_action=RecoveryFallbackAction.OFFER_NEW_LINKED_RUN,
                )
            ]
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.OFFER_NEW_LINKED_RUN,
                reason_code=reason_code,
                requires_user_authorization=True,
                preserve_original_run=True,
                new_run_allowed=True,
                instructions="Start a linked run with provider-bound request identity.",
            )
            reason = "checkpoint LLM replay identity is not bound to provider, model, and reasoning policy"
            next_action = "preserve this checkpoint and authorize a linked run if needed"
        elif self.state.budget.recovery_rounds_remaining <= 0:
            decision = "blocked"
            recoverability = Recoverability.RECOVERABLE_AFTER_ACTION
            recovery_mode = RecoveryMode.USER_ASSISTED_RESUME
            automation_policy = RecoveryAutomationPolicy.APPROVAL_REQUIRED
            reason_code = RecoveryReasonCode.RECOVERY_BUDGET_EXHAUSTED
            blockers = [
                RecoveryBlocker(
                    reason_code=reason_code,
                    resolvable=True,
                    requires_user_action=True,
                    evidence_refs=[checkpoint.checkpoint_id],
                    required_action=RecoveryFallbackAction.REQUEST_BUDGET_EXTENSION,
                )
            ]
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.REQUEST_BUDGET_EXTENSION,
                reason_code=reason_code,
                requires_user_authorization=True,
                instructions="Explicitly extend the recovery budget before retrying.",
            )
            reason = "recovery budget exhausted"
            next_action = "request an explicit budget extension"
        elif (
            checkpoint.safe_boundary
            in {CheckpointBoundary.CONTROLLED_STOP, CheckpointBoundary.VERIFICATION_APPLIED}
            and _phase_is(self.state.phase, AgentPhase.SUMMARIZE)
            and (not self.state.modified_files or self.state.verification_status == "passed")
            and checkpoint.pending_verification is None
        ):
            recoverability = Recoverability.ALREADY_COMPLETE
            recovery_mode = RecoveryMode.RETURN_COMPLETED
            reason_code = RecoveryReasonCode.CHECKPOINT_ALREADY_COMPLETE
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.NONE,
                reason_code=reason_code,
            )
            reason = "checkpoint already contains a completed runtime state"
            next_action = "return the completed state without re-execution"
        elif is_file_mutation and checkpoint.side_effect_state == "indeterminate":
            decision = "blocked"
            recoverability = Recoverability.RECOVERABLE_AFTER_ACTION
            recovery_mode = RecoveryMode.RECONCILE_THEN_RESUME
            automation_policy = RecoveryAutomationPolicy.MANUAL_ONLY
            reason_code = RecoveryReasonCode.INDETERMINATE_SIDE_EFFECT
            blockers = [
                RecoveryBlocker(
                    reason_code=reason_code,
                    resolvable=True,
                    requires_user_action=True,
                    evidence_refs=[checkpoint.checkpoint_id],
                    required_action=RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION,
                    target_ref=checkpoint.tool_call_id,
                )
            ]
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION,
                reason_code=reason_code,
                requires_user_authorization=True,
                instructions="Reconcile the pending file side effect before replay.",
            )
            reason = "checkpoint contains an indeterminate side effect"
            next_action = "reconcile the pending action"
        elif is_file_mutation and checkpoint.tool_input is None:
            decision = "blocked"
            recoverability = Recoverability.NOT_RECOVERABLE
            recovery_mode = RecoveryMode.NONE
            automation_policy = RecoveryAutomationPolicy.FORBIDDEN
            reason_code = RecoveryReasonCode.MISSING_TYPED_TOOL_INPUT
            blockers = [
                RecoveryBlocker(
                    reason_code=reason_code,
                    resolvable=False,
                    evidence_refs=[checkpoint.checkpoint_id],
                    required_action=RecoveryFallbackAction.TERMINATE_PRESERVING_EVIDENCE,
                    target_ref=checkpoint.tool_call_id,
                )
            ]
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.TERMINATE_PRESERVING_EVIDENCE,
                reason_code=reason_code,
                preserve_original_run=True,
                instructions="Preserve the checkpoint and inspect the incomplete typed action.",
            )
            reason = "file mutation checkpoint is missing its typed tool input"
            next_action = "inspect the incomplete checkpoint"
        elif is_file_mutation and checkpoint.safe_boundary in {
            CheckpointBoundary.TOOL_CALL_PREPARED,
            CheckpointBoundary.TOOL_RESULT_OBSERVED,
            CheckpointBoundary.TOOL_RESULT_APPLIED,
            CheckpointBoundary.VERIFICATION_REQUIRED,
            CheckpointBoundary.VERIFICATION_APPLIED,
        }:
            decision = "reconcile_then_resume"
            recoverability = Recoverability.RECOVERABLE_NOW
            recovery_mode = RecoveryMode.RECONCILE_THEN_RESUME
            reason_code = RecoveryReasonCode.PENDING_FILE_MUTATION
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.NONE,
                reason_code=reason_code,
            )
            reason = "file mutation checkpoint requires hash reconciliation before continuation"
            next_action = "reconcile the target file and continue with apply or verification"
        elif checkpoint.mutation_class == "externally_indeterminate":
            decision = "blocked"
            recoverability = Recoverability.RECOVERABLE_AFTER_ACTION
            recovery_mode = RecoveryMode.RECONCILE_THEN_RESUME
            automation_policy = RecoveryAutomationPolicy.MANUAL_ONLY
            reason_code = RecoveryReasonCode.EXTERNAL_WRITE_WITHOUT_PROBE
            blockers = [
                RecoveryBlocker(
                    reason_code=reason_code,
                    resolvable=True,
                    requires_user_action=True,
                    evidence_refs=[checkpoint.checkpoint_id],
                    required_action=RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION,
                    target_ref=checkpoint.tool_call_id,
                )
            ]
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION,
                reason_code=reason_code,
                requires_user_authorization=True,
                instructions="Inspect external command side effects before authorizing a new action.",
            )
            reason = "external command checkpoints are not eligible for automatic replay"
            next_action = "inspect command side effects and explicitly authorize a new action"
        elif checkpoint.session_cursor is not None and (
            not replay_artifacts_valid
            or
            checkpoint.safe_boundary
            not in {
                CheckpointBoundary.DECOMPOSITION_RECORDED,
                CheckpointBoundary.CONTEXT_ASSEMBLED,
                CheckpointBoundary.SUBTASK_RESULT_APPLIED,
                CheckpointBoundary.LLM_REQUEST_PREPARED,
                CheckpointBoundary.LLM_RESPONSE_OBSERVED,
                CheckpointBoundary.TOOL_CALL_PREPARED,
                CheckpointBoundary.TOOL_RESULT_OBSERVED,
                CheckpointBoundary.TOOL_RESULT_APPLIED,
            }
            or checkpoint.session_cursor.plan_hash
            != _RuntimeSessionExecutor._session_plan_hash(
                checkpoint.session_cursor.original_task,
                checkpoint.session_cursor.tasks,
                checkpoint.session_cursor.execution_order,
            )
        ):
            decision = "blocked"
            recoverability = Recoverability.NOT_RECOVERABLE
            recovery_mode = RecoveryMode.NONE
            automation_policy = RecoveryAutomationPolicy.FORBIDDEN
            reason_code = RecoveryReasonCode.CHECKPOINT_CORRUPT
            blockers = [
                RecoveryBlocker(
                    reason_code=reason_code,
                    resolvable=False,
                    evidence_refs=[checkpoint.checkpoint_id],
                    required_action=RecoveryFallbackAction.TERMINATE_PRESERVING_EVIDENCE,
                )
            ]
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.TERMINATE_PRESERVING_EVIDENCE,
                reason_code=reason_code,
                preserve_original_run=True,
                instructions="Preserve the checkpoint; its session cursor does not match the durable plan.",
            )
            reason = "session cursor is inconsistent with the durable task plan"
            next_action = "preserve evidence and inspect the cursor"
        elif checkpoint.session_cursor is not None and bool(
            getattr(self.session_executor, "supports_session_cursor", False)
        ):
            decision = "exact_resume"
            recoverability = Recoverability.RECOVERABLE_NOW
            recovery_mode = RecoveryMode.EXACT_RESUME
            automation_policy = RecoveryAutomationPolicy.AUTOMATIC_ALLOWED
            reason_code = RecoveryReasonCode.CHECKPOINT_VALID
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.NONE,
                reason_code=reason_code,
            )
            reason = "checkpoint contains a valid durable session cursor"
            next_action = "continue from the next unconsumed session task"
        elif checkpoint.session_bootstrap is not None and (
            checkpoint.session_bootstrap.goal_hash
            != f"sha256:{hashlib.sha256(self.state.goal.encode('utf-8')).hexdigest()}"
            or checkpoint.safe_boundary
            not in {
                CheckpointBoundary.TASK_NORMALIZED,
                CheckpointBoundary.CONTEXT_ASSEMBLED,
                CheckpointBoundary.LLM_REQUEST_PREPARED,
                CheckpointBoundary.LLM_RESPONSE_OBSERVED,
            }
            or not replay_artifacts_valid
        ):
            decision = "blocked"
            recoverability = Recoverability.NOT_RECOVERABLE
            recovery_mode = RecoveryMode.NONE
            automation_policy = RecoveryAutomationPolicy.FORBIDDEN
            reason_code = RecoveryReasonCode.CHECKPOINT_CORRUPT
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.TERMINATE_PRESERVING_EVIDENCE,
                reason_code=reason_code,
                preserve_original_run=True,
                instructions="Preserve the checkpoint; its bootstrap marker is inconsistent.",
            )
            reason = "session bootstrap is inconsistent with the durable goal"
            next_action = "preserve evidence and inspect the bootstrap marker"
        elif checkpoint.session_bootstrap is not None and bool(
            getattr(self.session_executor, "supports_session_cursor", False)
        ):
            reason = "checkpoint contains a valid session bootstrap and replay ledger"
            next_action = "restart bootstrap stages using observed LLM responses"
        elif "runtime_mode:read_only_analysis" not in self.state.assumptions:
            decision = "blocked"
            recoverability = Recoverability.NOT_RECOVERABLE
            recovery_mode = RecoveryMode.NONE
            automation_policy = RecoveryAutomationPolicy.APPROVAL_REQUIRED
            reason_code = RecoveryReasonCode.UNSUPPORTED_RUNTIME_MODE
            blockers = [
                RecoveryBlocker(
                    reason_code=reason_code,
                    resolvable=False,
                    evidence_refs=[checkpoint.checkpoint_id],
                    required_action=RecoveryFallbackAction.OFFER_NEW_LINKED_RUN,
                )
            ]
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.OFFER_NEW_LINKED_RUN,
                reason_code=reason_code,
                requires_user_authorization=True,
                new_run_allowed=True,
                instructions="Start a separately authorized linked run without claiming exact resume.",
            )
            reason = "checkpoint is not a supported read-only or file-mutation runtime"
            next_action = "start a separately authorized run"
        elif checkpoint.side_effect_state in {"prepared", "indeterminate"}:
            decision = "blocked"
            recoverability = Recoverability.RECOVERABLE_AFTER_ACTION
            recovery_mode = RecoveryMode.RECONCILE_THEN_RESUME
            automation_policy = RecoveryAutomationPolicy.MANUAL_ONLY
            reason_code = RecoveryReasonCode.INDETERMINATE_SIDE_EFFECT
            blockers = [
                RecoveryBlocker(
                    reason_code=reason_code,
                    resolvable=True,
                    requires_user_action=True,
                    evidence_refs=[checkpoint.checkpoint_id],
                    required_action=RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION,
                    target_ref=checkpoint.tool_call_id,
                )
            ]
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION,
                reason_code=reason_code,
                requires_user_authorization=True,
                instructions="Reconcile the pending side effect before resume.",
            )
            reason = "checkpoint contains an indeterminate side effect"
            next_action = "reconcile the pending action"
        elif checkpoint.safe_boundary != CheckpointBoundary.TASK_NORMALIZED:
            decision = "blocked"
            recoverability = Recoverability.NOT_RECOVERABLE
            recovery_mode = RecoveryMode.NONE
            automation_policy = RecoveryAutomationPolicy.APPROVAL_REQUIRED
            reason_code = RecoveryReasonCode.MISSING_STAGE_CURSOR
            blockers = [
                RecoveryBlocker(
                    reason_code=reason_code,
                    resolvable=False,
                    evidence_refs=[checkpoint.checkpoint_id],
                    required_action=RecoveryFallbackAction.OFFER_NEW_LINKED_RUN,
                )
            ]
            fallback = RecoveryFallback(
                action=RecoveryFallbackAction.OFFER_NEW_LINKED_RUN,
                reason_code=reason_code,
                requires_user_authorization=True,
                new_run_allowed=True,
                instructions="Preserve this run and explicitly authorize a linked new run if needed.",
            )
            reason = "checkpoint has no durable session stage cursor for exact continuation"
            next_action = "wait for stage-cursor support or start a new run"

        budget = self.state.budget
        return RuntimeResumeDecisionMetadata(
            checkpoint_id=checkpoint.checkpoint_id,
            run_id=checkpoint.run_id,
            root_task_id=checkpoint.root_task_id,
            session_id=checkpoint.session_id,
            resume_attempt_id=str(self._resume_attempt_id or ""),
            decision=decision,
            recoverability=recoverability,
            recovery_mode=recovery_mode,
            automation_policy=automation_policy,
            reason_code=reason_code,
            safe_boundary=checkpoint.safe_boundary,
            project_drift=project_drift,
            budget_remaining={
                "tool_calls": budget.tool_calls_remaining,
                "file_reads": budget.file_reads_remaining,
                "file_edits": budget.file_edits_remaining,
                "file_creates": budget.file_creates_remaining,
                "verification_attempts": budget.verification_attempts_remaining,
                "recovery_rounds": budget.recovery_rounds_remaining,
                "replan_rounds": budget.replan_rounds_remaining,
            },
            evidence_refs=[checkpoint.checkpoint_id],
            blockers=blockers,
            fallback=fallback,
            reason=reason,
            next_action=next_action,
        )

    def _resume_file_mutation(
        self,
        checkpoint: RuntimeCheckpointMetadata,
        decision: RuntimeResumeDecisionMetadata,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        assert self.state is not None
        assert checkpoint.tool_input is not None
        target_paths = list(checkpoint.project_fingerprint.target_file_hashes)
        current_hashes = self._file_hashes(target_paths)
        stored_hashes = dict(checkpoint.project_fingerprint.target_file_hashes)
        expected_hashes = dict(checkpoint.project_fingerprint.expected_target_file_hashes)
        selection = ToolSelection(
            step_id=str(checkpoint.step_id or "resume_file_mutation"),
            tool_name=str(checkpoint.tool_name),
            reason=SelectionReason.CAPABILITY_MATCH,
            confidence=1.0,
            input_metadata=checkpoint.tool_input.model_copy(deep=True),
        )
        tool_call = SimpleNamespace(
            call_id=str(checkpoint.tool_call_id or f"{checkpoint.root_task_id}:resume"),
            step_id=selection.step_id,
        )
        observed_result = checkpoint.observed_file_result
        execution_result: Any | None = None

        if checkpoint.safe_boundary == CheckpointBoundary.TOOL_CALL_PREPARED:
            if expected_hashes and current_hashes == expected_hashes:
                execution_result = self._synthetic_file_execution_result(selection, success=True)
                self._active_tool_checkpoint = self._checkpoint_action(checkpoint)
                if not self.observe_tool_result(tool_call, selection, execution_result):
                    return self._block_reconciliation(decision, "reconciled result could not be checkpointed")
            elif current_hashes == stored_hashes:
                executor = getattr(self.runtime, "tool_executor", None)
                if executor is None or not hasattr(executor, "execute_single"):
                    return self._block_reconciliation(decision, "file mutation executor is unavailable")
                if not self.prepare_tool_call(tool_call, selection):
                    return self._block_reconciliation(decision, "prepared resume checkpoint could not be persisted")
                execution_result = executor.execute_single(selection, context=None)
                if not self.observe_tool_result(tool_call, selection, execution_result):
                    return self._block_reconciliation(decision, "resumed mutation result could not be persisted")
            else:
                return self._block_reconciliation(
                    decision,
                    "target file differs from both the pre-mutation and expected states",
                    reason_code=RecoveryReasonCode.PROJECT_DRIFT_CONFLICTING,
                )
        elif checkpoint.safe_boundary == CheckpointBoundary.TOOL_RESULT_OBSERVED:
            if observed_result is None or not observed_result.success:
                return self._block_reconciliation(decision, "observed file mutation did not record a successful result")
            if current_hashes != stored_hashes:
                return self._block_reconciliation(
                    decision,
                    "target file drifted after the observed mutation",
                    reason_code=RecoveryReasonCode.PROJECT_DRIFT_CONFLICTING,
                )
            execution_result = self._synthetic_file_execution_result(selection, success=True)
            self._active_tool_checkpoint = self._checkpoint_action(checkpoint)
        elif checkpoint.safe_boundary in {
            CheckpointBoundary.TOOL_RESULT_APPLIED,
            CheckpointBoundary.VERIFICATION_REQUIRED,
            CheckpointBoundary.VERIFICATION_APPLIED,
        }:
            if current_hashes != stored_hashes:
                return self._block_reconciliation(
                    decision,
                    "target file drifted after state application",
                    reason_code=RecoveryReasonCode.PROJECT_DRIFT_CONFLICTING,
                )
        else:
            return self._block_reconciliation(decision, "unsupported file mutation recovery boundary")

        if execution_result is not None:
            if not bool(getattr(execution_result, "success", False)):
                return self._block_reconciliation(decision, "file mutation failed during reconciliation")
            self.state_updater.apply_tool_result(self.state, selection, execution_result)

        if checkpoint.pending_verification is not None:
            self.state.phase = AgentPhase.VERIFY
            self.state.verification_status = "required"
        if self.state.verification_status != "required":
            return self._block_reconciliation(decision, "reconciled file mutation did not require verification")
        plan = checkpoint.pending_verification or self.verifier.plan(self.state, context)
        if not plan.commands:
            return self._block_reconciliation(decision, "verification plan has no executable command")
        executor = getattr(self.runtime, "tool_executor", None)
        if executor is None or not hasattr(executor, "execute_single"):
            return self._block_reconciliation(decision, "verification executor is unavailable")
        self._active_tool_checkpoint = self._checkpoint_action(checkpoint)
        self._active_tool_checkpoint["pending_verification"] = plan.model_copy(deep=True)
        self._pending_verification = plan.model_copy(deep=True)
        verified = True
        for command_index in range(plan.next_command_index, len(plan.commands)):
            current_plan = self._active_tool_checkpoint.get("pending_verification")
            if not isinstance(current_plan, VerificationPlanMetadata):
                break
            spec = current_plan.command_specs[command_index]
            verification_input = ToolInputMetadata.from_mapping(
                "command_executor",
                {
                    "command": spec.command,
                    "cwd": spec.cwd or context.get("project_path") or context.get("cwd"),
                    "mode": spec.mode,
                    "timeout": spec.timeout,
                    "test_command": spec.command,
                },
            )
            if self._command_requires_python_environment(spec.command):
                bind_command_context = getattr(self.runtime, "_apply_project_command_context", None)
                if not callable(bind_command_context):
                    return self._block_reconciliation(
                        decision,
                        "project environment binding is unavailable for resumed Python verification",
                        reason_code=RecoveryReasonCode.PROJECT_DRIFT_CONFLICTING,
                    )
                verification_input = bind_command_context("command_executor", verification_input)
                if not verification_input.environment_id:
                    return self._block_reconciliation(
                        decision,
                        "project environment is not ready for resumed Python verification",
                        reason_code=RecoveryReasonCode.PROJECT_DRIFT_CONFLICTING,
                    )
            verification_selection = ToolSelection(
                step_id=f"{selection.step_id}_verify_resume_{command_index + 1}",
                tool_name="command_executor",
                reason=SelectionReason.CAPABILITY_MATCH,
                confidence=1.0,
                input_metadata=verification_input,
            )
            verification_result = executor.execute_single(verification_selection, context=None)
            self.state_updater.apply_tool_result(self.state, verification_selection, verification_result)
            if not bool(getattr(verification_result, "success", False)):
                verified = False
                break
        verified = verified and self._active_tool_checkpoint.get("pending_verification") is None
        if not verified:
            return self._block_reconciliation(
                decision,
                "file mutation verification failed during recovery",
                reason_code=RecoveryReasonCode.VERIFICATION_FAILED,
            )
        self.state.recovery_status = RecoveryStatus.RECOVERED
        self.state.recovery_reason_code = decision.reason_code
        runtime_result = self._runtime_result(
            self.state,
            {"success": verified, "resume_reconciled": True},
        )
        runtime_result["resume_decision"] = decision.to_json_dict()
        runtime_result["resume_status"] = "resumed" if verified else "blocked"
        return runtime_result

    @staticmethod
    def _synthetic_file_execution_result(selection: ToolSelection, *, success: bool) -> Any:
        file_path = str(selection.input_metadata.file_path or "")
        if success:
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name=selection.tool_name,
                    status=ResultStatus.SUCCESS,
                    result=FileArtifactMetadata(file_path=file_path),
                ),
                error=None,
            )
        failure = FailureMetadata(error_type="ReconciliationFailed", error_message="file reconciliation failed")
        return SimpleNamespace(success=False, output_metadata=None, error=failure)

    @staticmethod
    def _checkpoint_action(checkpoint: RuntimeCheckpointMetadata) -> dict[str, Any]:
        return {
            "tool_name": checkpoint.tool_name,
            "step_id": checkpoint.step_id,
            "call_id": checkpoint.tool_call_id,
            "tool_input": checkpoint.tool_input,
            "observed_file_result": checkpoint.observed_file_result,
            "observed_failure": checkpoint.observed_failure,
            "pending_verification": checkpoint.pending_verification,
            "target_files": list(checkpoint.project_fingerprint.target_file_hashes),
            "expected_target_file_hashes": dict(checkpoint.project_fingerprint.expected_target_file_hashes),
        }

    def _block_reconciliation(
        self,
        decision: RuntimeResumeDecisionMetadata,
        reason: str,
        *,
        reason_code: RecoveryReasonCode = RecoveryReasonCode.RECONCILIATION_FAILED,
    ) -> dict[str, Any]:
        blocked_payload = decision.to_json_dict()
        blocked_payload.update(
            {
                "decision": "blocked",
                "recoverability": Recoverability.RECOVERABLE_AFTER_ACTION,
                "recovery_mode": RecoveryMode.RECONCILE_THEN_RESUME,
                "automation_policy": RecoveryAutomationPolicy.MANUAL_ONLY,
                "reason_code": reason_code,
                "blockers": [
                    RecoveryBlocker(
                        reason_code=reason_code,
                        resolvable=True,
                        requires_user_action=True,
                        evidence_refs=list(decision.evidence_refs),
                        required_action=RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION,
                    ).model_dump(mode="python")
                ],
                "fallback": RecoveryFallback(
                    action=RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION,
                    reason_code=reason_code,
                    requires_user_authorization=True,
                    instructions="Review reconciliation evidence before any replay.",
                ).model_dump(mode="python"),
                "reason": reason,
                "next_action": "review reconciliation evidence before any replay",
            }
        )
        blocked = RuntimeResumeDecisionMetadata.model_validate(blocked_payload)
        self._apply_resume_assessment(blocked)
        self._emit_resume_decision(blocked)
        return self._resume_terminal_result(blocked, success=False, status="blocked")

    def _apply_resume_assessment(self, decision: RuntimeResumeDecisionMetadata) -> None:
        assert self.state is not None
        self.state.recovery_reason_code = decision.reason_code
        self.state.active_resume_attempt_id = decision.resume_attempt_id
        if decision.recoverability == Recoverability.ALREADY_COMPLETE:
            self.state.recovery_status = RecoveryStatus.RECOVERED
        elif decision.recoverability == Recoverability.NOT_RECOVERABLE:
            self.state.recovery_status = RecoveryStatus.UNRECOVERABLE
        elif decision.recoverability == Recoverability.RECOVERABLE_AFTER_ACTION:
            action = decision.fallback.action if decision.fallback is not None else RecoveryFallbackAction.NONE
            self.state.recovery_status = (
                RecoveryStatus.WAITING_RETRY
                if action == RecoveryFallbackAction.RETRY_LATER
                else RecoveryStatus.WAITING_USER
            )
        elif decision.recovery_mode == RecoveryMode.RECONCILE_THEN_RESUME:
            self.state.recovery_status = RecoveryStatus.RECONCILIATION_REQUIRED
        elif decision.recovery_mode == RecoveryMode.REPLAN_FROM_CHECKPOINT:
            self.state.recovery_status = RecoveryStatus.REPLAN_REQUIRED
        else:
            self.state.recovery_status = RecoveryStatus.RESUME_READY

    def _emit_resume_decision(self, decision: RuntimeResumeDecisionMetadata) -> None:
        hooks = getattr(self.runtime, "runtime_diagnostics_hooks", None)
        if hooks and hasattr(hooks, "on_resume_preflight_completed"):
            hooks.on_resume_preflight_completed(decision)

    def _resume_terminal_result(
        self,
        decision: RuntimeResumeDecisionMetadata,
        *,
        success: bool,
        status: str,
    ) -> dict[str, Any]:
        assert self.state is not None
        report = self._load_finalization_report(self._active_finalization_cursor)
        if report is None:
            report = self.reporter.report(self.state)
        return {
            "success": success,
            "goal": self.state.goal,
            "resume_status": status,
            "resume_decision": decision.to_json_dict(),
            "checkpoint_status": self._checkpoint_status,
            "agent_runtime_state": self.state.to_json_dict(),
            "runtime_report": report.to_json_dict(),
            **(
                {
                    "session_ingress_state": self._active_session_ingress_state.model_dump(mode="json")
                }
                if self._active_session_ingress_state is not None
                else {}
            ),
        }

    def _configure_prompt_context_checkpointing(self) -> None:
        builder = getattr(self.runtime, "memory_context_builder", None)
        setter = getattr(builder, "set_checkpoint_handlers", None)
        if callable(setter):
            setter(
                snapshot_sink=self._persist_prompt_context_snapshot,
                replay_provider=self._replay_prompt_context_snapshot,
                compaction_sink=self._persist_context_compaction_artifact,
            )

    def _persist_context_compaction_artifact(
        self,
        payload: dict[str, Any],
    ) -> DurableArtifactReference | None:
        if not self._checkpointing_enabled:
            return None
        if self.state is None or self.checkpoint_store is None or not self._checkpoint_run_id:
            return None
        record = ContextCompactionRecord.model_validate(payload)
        return self.checkpoint_store.save_recovery_artifact(
            self._checkpoint_run_id,
            kind="context_compaction",
            payload=record.model_dump(mode="json"),
        )

    @staticmethod
    def _prompt_text_hash(prompt_text: str) -> str:
        return f"sha256:{hashlib.sha256(prompt_text.encode('utf-8')).hexdigest()}"

    def _persist_prompt_context_snapshot(
        self,
        request_hash: str,
        payload: dict[str, Any],
    ) -> bool:
        if not self._checkpointing_enabled:
            return True
        if self.state is None or self.checkpoint_store is None or not self._checkpoint_run_id:
            self._record_checkpoint_failure(
                CheckpointBoundary.CONTEXT_ASSEMBLED,
                "context snapshot store or runtime state unavailable",
            )
            return False
        try:
            selection = ContextSelectionMetadata.model_validate(payload.get("context_selection"))
            prompt_text = str(payload.get("prompt_text") or "")
            if selection.final_prompt_chars != len(prompt_text):
                raise ValueError("context selection size does not match prompt_text")
            compaction_bindings = [
                ContextCompactionBinding.model_validate(item)
                for item in payload.get("context_compactions") or []
            ]
            for binding in compaction_bindings:
                compacted_payload = self.checkpoint_store.load_recovery_artifact(
                    self._checkpoint_run_id,
                    binding.artifact,
                )
                if compacted_payload != binding.record.model_dump(mode="json"):
                    raise ValueError("context compaction artifact does not match its record")
            reference = self.checkpoint_store.save_recovery_artifact(
                self._checkpoint_run_id,
                kind="prompt_context",
                payload=payload,
            )
            snapshot = RuntimePromptContextSnapshot(
                context_id=uuid.uuid4().hex,
                request_hash=request_hash,
                prompt_hash=self._prompt_text_hash(prompt_text),
                selection=selection,
                context_artifact=reference,
                compaction_bindings=compaction_bindings,
            )
        except Exception as exc:
            self._record_checkpoint_failure(CheckpointBoundary.CONTEXT_ASSEMBLED, str(exc))
            return False
        self._active_prompt_context_snapshot = snapshot
        self._prompt_context_replay_enabled = False
        return self._persist_checkpoint(
            self.state,
            reason="model prompt context assembled",
            safe_boundary=CheckpointBoundary.CONTEXT_ASSEMBLED,
            mutation_class="read_only",
            side_effect_state="observed",
            prompt_context_snapshot=snapshot,
        )

    def _replay_prompt_context_snapshot(self, request_hash: str) -> dict[str, Any] | None:
        snapshot = self._active_prompt_context_snapshot
        if (
            not self._prompt_context_replay_enabled
            or snapshot is None
            or self.checkpoint_store is None
            or not self._checkpoint_run_id
        ):
            return None
        if snapshot.request_hash != request_hash:
            raise RuntimeError(
                "checkpointed prompt context request does not match the resumed request"
            )
        payload = self.checkpoint_store.load_recovery_artifact(
            self._checkpoint_run_id,
            snapshot.context_artifact,
        )
        if payload is None:
            raise RuntimeError("checkpointed prompt context artifact is unavailable or corrupt")
        try:
            selection = ContextSelectionMetadata.model_validate(payload.get("context_selection"))
            compaction_bindings = [
                ContextCompactionBinding.model_validate(item)
                for item in payload.get("context_compactions") or []
            ]
        except ValueError as exc:
            raise RuntimeError("checkpointed prompt context selection is invalid") from exc
        prompt_text = str(payload.get("prompt_text") or "")
        if (
            self._prompt_text_hash(prompt_text) != snapshot.prompt_hash
            or selection != snapshot.selection
            or compaction_bindings != snapshot.compaction_bindings
            or selection.final_prompt_chars != len(prompt_text)
        ):
            raise RuntimeError("checkpointed prompt context evidence does not match its snapshot")
        for binding in compaction_bindings:
            compacted_payload = self.checkpoint_store.load_recovery_artifact(
                self._checkpoint_run_id,
                binding.artifact,
            )
            if compacted_payload != binding.record.model_dump(mode="json"):
                raise RuntimeError("checkpointed context compaction artifact is unavailable or corrupt")
        self._prompt_context_replay_enabled = False
        return payload

    def _configure_checkpointing(self, context: dict[str, Any], *, read_only_mode: bool) -> None:
        requested = bool(context.get("checkpointing_enabled"))
        self._checkpointing_enabled = requested
        self._checkpoint_generation = 0
        self._checkpoint_status = (
            CheckpointStatus.DISABLED if not self._checkpointing_enabled else CheckpointStatus.PENDING
        )
        self._checkpoint_context = dict(context)
        self._checkpoint_run_id = ""
        if not self._checkpointing_enabled:
            return
        hooks = getattr(self.runtime, "runtime_diagnostics_hooks", None)
        recorder = getattr(hooks, "recorder", None)
        run = None
        for key in (
            str(context.get("run_id") or ""),
            self._active_task_id,
            str(getattr(self.runtime, "session_id", "") or ""),
        ):
            if key and recorder is not None:
                run = recorder.load_run(key)
                if run is not None:
                    break
        if run is not None:
            self._checkpoint_run_id = str(run.run_id)
        if self.checkpoint_store is None and recorder is not None:
            self.checkpoint_store = RuntimeCheckpointStore(recorder.trajectory_dir)

    def _persist_checkpoint(
        self,
        state: RuntimeStateMetadata,
        *,
        reason: str,
        safe_boundary: CheckpointBoundary | str,
        tool_name: str | None = None,
        subtask_id: str | None = None,
        step_id: str | None = None,
        mutation_class: str = "none",
        side_effect_state: str = "none",
        call_id: str | None = None,
        tool_input: ToolInputMetadata | None = None,
        observed_file_result: ObservedFileMutationResult | None = None,
        observed_failure: FailureMetadata | None = None,
        pending_verification: VerificationPlanMetadata | None = None,
        session_cursor: SessionExecutionCursor | None = None,
        session_bootstrap: SessionBootstrapCursor | None = None,
        pending_llm_request: PendingLLMRequest | None = None,
        llm_replay_entries: list[LLMReplayEntry] | None = None,
        read_tool_replay_entries: list[ReadToolReplayEntry] | None = None,
        session_ingress_state: SessionIngressState | None = None,
        prompt_context_snapshot: RuntimePromptContextSnapshot | None = None,
        finalization_cursor: RuntimeFinalizationCursor | None = None,
        target_files: list[str] | None = None,
        expected_target_file_hashes: dict[str, str] | None = None,
    ) -> bool:
        if not self._checkpointing_enabled:
            return True
        if self.checkpoint_store is None or not self._checkpoint_run_id:
            self._record_checkpoint_failure(safe_boundary, "checkpoint store or run identity unavailable")
            return False
        generation = self._checkpoint_generation + 1
        active_ingress = session_ingress_state or self._active_session_ingress_state
        session_id = (
            active_ingress.identity.run_id
            if active_ingress is not None
            else str(getattr(self.runtime, "session_id", "") or "")
        )
        project_root = str(
            self._checkpoint_context.get("project_path")
            or self._checkpoint_context.get("cwd")
            or Path.cwd()
        )
        interpreter, environment_id = self._checkpoint_environment_binding(project_root)
        durable_session_cursor = session_cursor or self._active_session_cursor
        durable_session_bootstrap = session_bootstrap or self._active_session_bootstrap
        checkpoint = RuntimeCheckpointMetadata(
            checkpoint_id=uuid.uuid4().hex,
            generation=generation,
            run_id=self._checkpoint_run_id,
            root_task_id=self._active_task_id,
            session_id=session_id,
            resume_attempt_id=self._resume_attempt_id,
            resume_source_checkpoint_id=self._resume_source_checkpoint_id,
            checkpoint_reason=reason,
            safe_boundary=safe_boundary,
            runtime_state=state.model_copy(deep=True),
            session_ingress_state=(
                active_ingress.model_copy(deep=True)
                if active_ingress is not None
                else None
            ),
            subtask_id=subtask_id,
            step_id=step_id,
            tool_name=tool_name,
            tool_call_id=call_id,
            tool_input_hash=self._tool_input_hash(tool_input),
            tool_input=tool_input,
            observed_file_result=observed_file_result,
            observed_failure=observed_failure,
            pending_verification=(
                pending_verification.model_copy(deep=True)
                if pending_verification is not None
                else None
            ),
            session_cursor=(
                durable_session_cursor.model_copy(deep=True)
                if durable_session_cursor is not None
                else None
            ),
            session_bootstrap=(
                durable_session_bootstrap.model_copy(deep=True)
                if durable_session_bootstrap is not None and durable_session_cursor is None
                else None
            ),
            pending_llm_request=(
                pending_llm_request.model_copy(deep=True)
                if pending_llm_request is not None
                else (
                    self._pending_llm_request.model_copy(deep=True)
                    if self._pending_llm_request is not None
                    else None
                )
            ),
            llm_replay_entries=[
                entry.model_copy(deep=True)
                for entry in (
                    llm_replay_entries
                    if llm_replay_entries is not None
                    else self._llm_replay_entries
                )
            ],
            read_tool_replay_entries=[
                entry.model_copy(deep=True)
                for entry in (
                    read_tool_replay_entries
                    if read_tool_replay_entries is not None
                    else self._read_tool_replay_entries
                )
            ],
            prompt_context_snapshot=(
                (prompt_context_snapshot or self._active_prompt_context_snapshot).model_copy(deep=True)
                if (prompt_context_snapshot or self._active_prompt_context_snapshot) is not None
                else None
            ),
            finalization_cursor=(
                (finalization_cursor or self._active_finalization_cursor).model_copy(deep=True)
                if (finalization_cursor or self._active_finalization_cursor) is not None
                else None
            ),
            mutation_class=mutation_class,
            side_effect_state=side_effect_state,
            project_fingerprint=self._build_project_fingerprint(
                project_root=project_root,
                cwd=str(self._checkpoint_context.get("cwd") or Path.cwd()),
                target_files=target_files or [],
                expected_target_file_hashes=expected_target_file_hashes or {},
                interpreter=interpreter,
                environment_id=environment_id,
            ),
        )
        if self._checkpoint_fault_injector is not None:
            self._checkpoint_fault_injector(
                CheckpointFaultPoint.BEFORE_DURABLE_WRITE,
                checkpoint.safe_boundary,
                checkpoint,
            )
        try:
            saved = self.checkpoint_store.save(checkpoint, expected_generation=self._checkpoint_generation)
        except Exception as exc:
            self._record_checkpoint_failure(safe_boundary, str(exc))
            return False
        if self._checkpoint_fault_injector is not None:
            self._checkpoint_fault_injector(
                CheckpointFaultPoint.AFTER_DURABLE_WRITE,
                saved.safe_boundary,
                saved,
            )
        self._checkpoint_generation = saved.generation
        self._checkpoint_status = CheckpointStatus.DURABLE
        hooks = getattr(self.runtime, "runtime_diagnostics_hooks", None)
        if hooks and hasattr(hooks, "on_checkpoint_created"):
            hooks.on_checkpoint_created(saved)
        return True

    @staticmethod
    def _tool_input_hash(tool_input: ToolInputMetadata | None) -> str | None:
        if tool_input is None:
            return None
        encoded = json.dumps(
            tool_input.to_json_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    @staticmethod
    def _file_hashes(file_paths: list[str]) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for raw_path in file_paths:
            path = Path(raw_path).expanduser().resolve()
            if not path.exists():
                hashes[str(path)] = "missing"
            elif not path.is_file():
                hashes[str(path)] = "not_a_file"
            else:
                hashes[str(path)] = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        return hashes

    @classmethod
    def _build_project_fingerprint(
        cls,
        *,
        project_root: str,
        cwd: str,
        target_files: list[str] | None = None,
        expected_target_file_hashes: dict[str, str] | None = None,
        interpreter: str | None = None,
        environment_id: str | None = None,
    ) -> ProjectFingerprint:
        resolved_root = str(Path(project_root).expanduser().resolve())
        resolved_cwd = str(Path(cwd).expanduser().resolve())
        git_repository = None
        git_head = None
        git_branch = None
        git_dirty = None
        git_status_hash = None
        try:
            repository_result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=resolved_root,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if repository_result.returncode == 0:
                git_repository = str(Path(repository_result.stdout.strip()).resolve())
                head_result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=resolved_root,
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                branch_result = subprocess.run(
                    ["git", "branch", "--show-current"],
                    cwd=resolved_root,
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                status_result = subprocess.run(
                    ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
                    cwd=resolved_root,
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                if head_result.returncode == 0:
                    git_head = head_result.stdout.strip() or None
                if branch_result.returncode == 0:
                    git_branch = branch_result.stdout.strip() or None
                if status_result.returncode == 0:
                    status_text = status_result.stdout
                    git_dirty = bool(status_text.strip())
                    git_status_hash = f"sha256:{hashlib.sha256(status_text.encode('utf-8')).hexdigest()}"
        except (OSError, subprocess.SubprocessError):
            pass
        return ProjectFingerprint(
            project_root=resolved_root,
            git_repository=git_repository,
            git_head=git_head,
            git_branch=git_branch,
            git_dirty=git_dirty,
            git_status_hash=git_status_hash,
            target_file_hashes=cls._file_hashes(target_files or []),
            expected_target_file_hashes=dict(expected_target_file_hashes or {}),
            cwd=resolved_cwd,
            interpreter=interpreter,
            environment_id=environment_id,
        )

    def _checkpoint_environment_binding(self, project_root: str) -> tuple[str | None, str | None]:
        environments = getattr(self.runtime, "_project_environments", None)
        if not isinstance(environments, dict):
            return None, None
        environment = environments.get(str(Path(project_root).expanduser().resolve()))
        if not isinstance(environment, dict):
            return None, None
        readiness = environment.get("readiness")
        readiness_value = getattr(readiness, "value", readiness)
        interpreter = str(environment.get("python_executable") or "").strip()
        environment_id = str(environment.get("environment_id") or "").strip()
        if readiness_value != EnvironmentReadiness.READY.value or not interpreter or not environment_id:
            return None, None
        return interpreter, environment_id

    @staticmethod
    def _command_requires_python_environment(command: str) -> bool:
        try:
            argv = shlex.split(str(command or ""))
        except ValueError:
            return False
        if not argv:
            return False
        executable = Path(argv[0]).name.lower()
        return executable in {"python", "python3", "pytest", "py.test", "tox", "nox", "pip", "pip3"}

    @staticmethod
    def _expected_file_hashes(selection: ToolSelection) -> dict[str, str]:
        file_path = str(selection.input_metadata.file_path or "").strip()
        if not file_path:
            return {}
        resolved = str(Path(file_path).expanduser().resolve())
        if selection.tool_name == "file_delete_tool":
            return {resolved: "missing"}
        if selection.tool_name == "file_writer" and selection.input_metadata.content is not None:
            content = str(selection.input_metadata.content).encode("utf-8")
            return {resolved: f"sha256:{hashlib.sha256(content).hexdigest()}"}
        return {}

    @staticmethod
    def _command_may_modify_project(command: str) -> bool:
        padded = f" {str(command or '').lower()} "
        markers = (
            " rm ",
            " rm -",
            " mv ",
            " cp ",
            " mkdir ",
            " touch ",
            " chmod ",
            " chown ",
            " sed -i",
            " tee ",
            " >",
            ">>",
            " pip install",
            " uv add",
            " poetry add",
            " npm install",
            " pnpm add",
            " yarn add",
            " cargo add",
            " go get",
        )
        return any(marker in padded for marker in markers)

    def _checkpointed_mutation_class(self, selection: ToolSelection) -> str | None:
        if selection.tool_name in READ_TOOLS:
            return "read_only"
        if selection.tool_name in FILE_MUTATION_TOOLS:
            return "mutating"
        if selection.tool_name == "command_executor" and self._command_may_modify_project(
            str(selection.input_metadata.command or "")
        ):
            return "externally_indeterminate"
        return None

    def set_pending_verification(self, plan: VerificationPlanMetadata | None) -> None:
        """Retain the next required validation while the current tool plan is in scope."""
        self._pending_verification = plan.model_copy(deep=True) if plan is not None else None

    def _persist_session_bootstrap(self, bootstrap: SessionBootstrapCursor) -> None:
        """Persist the pre-decomposition restart marker without prompt contents."""
        self._active_session_bootstrap = bootstrap.model_copy(deep=True)
        if self.state is None:
            return
        self._persist_checkpoint(
            self.state,
            reason="session bootstrap recorded before semantic/decomposition calls",
            safe_boundary=CheckpointBoundary.TASK_NORMALIZED,
            session_bootstrap=bootstrap,
        )

    def _persist_session_cursor(self, cursor: SessionExecutionCursor) -> None:
        """Persist one exact session position without making the cursor a second runtime owner."""
        self._active_session_cursor = cursor.model_copy(deep=True)
        self._active_session_bootstrap = None
        completed_task_ids = set(cursor.execution_order[: cursor.next_task_index])
        completed_task_ids.add(self._active_task_id)
        self._llm_replay_entries = [
            entry for entry in self._llm_replay_entries if entry.task_id not in completed_task_ids
        ]
        self._read_tool_replay_entries = [
            entry for entry in self._read_tool_replay_entries if entry.task_id not in completed_task_ids
        ]
        if self._pending_llm_request is not None and self._pending_llm_request.task_id in completed_task_ids:
            self._pending_llm_request = None
        if self.state is None:
            return
        boundary = (
            CheckpointBoundary.DECOMPOSITION_RECORDED
            if cursor.stage == SessionStage.DECOMPOSITION_RECORDED
            else CheckpointBoundary.SUBTASK_RESULT_APPLIED
        )
        self._persist_checkpoint(
            self.state,
            reason=f"session cursor advanced to {cursor.stage.value}:{cursor.next_task_index}",
            safe_boundary=boundary,
            subtask_id=(
                cursor.execution_order[cursor.next_task_index - 1]
                if cursor.next_task_index > 0
                else None
            ),
            session_cursor=cursor,
        )

    def prepare_llm_request(self, task_id: str, request_ordinal: int, request_hash: str) -> bool:
        """Persist a hash-only in-flight marker before a provider request is sent."""
        if not self._checkpointing_enabled or (
            self._active_session_cursor is None and self._active_session_bootstrap is None
        ):
            return True
        self._pending_llm_request = PendingLLMRequest(
            task_id=task_id,
            request_ordinal=request_ordinal,
            request_hash=request_hash,
            hash_version=self._llm_request_hash_version(request_hash),
        )
        assert self.state is not None
        return self._persist_checkpoint(
            self.state,
            reason=f"prepared LLM request {task_id}:{request_ordinal}",
            safe_boundary=CheckpointBoundary.LLM_REQUEST_PREPARED,
            subtask_id=task_id,
            mutation_class="read_only",
            side_effect_state="prepared",
        )

    def observe_llm_response(
        self,
        task_id: str,
        request_ordinal: int,
        request_hash: str,
        response: LLMResponse,
    ) -> bool:
        """Persist an observed provider response before session code applies it."""
        if not self._checkpointing_enabled or (
            self._active_session_cursor is None and self._active_session_bootstrap is None
        ):
            return True
        if self.checkpoint_store is None or not self._checkpoint_run_id:
            return False
        reference = self.checkpoint_store.save_recovery_artifact(
            self._checkpoint_run_id,
            kind="llm_response",
            payload=response.model_dump(mode="json"),
        )
        entry = LLMReplayEntry(
            task_id=task_id,
            request_ordinal=request_ordinal,
            request_hash=request_hash,
            hash_version=self._llm_request_hash_version(request_hash),
            response_artifact=reference,
        )
        self._llm_replay_entries = [
            existing
            for existing in self._llm_replay_entries
            if (existing.task_id, existing.request_ordinal) != (task_id, request_ordinal)
        ]
        self._llm_replay_entries.append(entry)
        self._pending_llm_request = None
        assert self.state is not None
        return self._persist_checkpoint(
            self.state,
            reason=f"observed LLM response {task_id}:{request_ordinal}",
            safe_boundary=CheckpointBoundary.LLM_RESPONSE_OBSERVED,
            subtask_id=task_id,
            mutation_class="read_only",
            side_effect_state="observed",
        )

    def replay_llm_response(
        self,
        task_id: str,
        request_ordinal: int,
        request_hash: str,
    ) -> LLMResponse | None:
        """Return one checksum-verified observed response for the identical call position."""
        if self.checkpoint_store is None or not self._checkpoint_run_id:
            return None
        entry = next(
            (
                item
                for item in self._llm_replay_entries
                if item.task_id == task_id
                and item.request_ordinal == request_ordinal
                and item.request_hash == request_hash
                and item.hash_version == LLMRequestHashVersion.PROVIDER_BOUND_V2
            ),
            None,
        )
        if entry is None:
            return None
        payload = self.checkpoint_store.load_recovery_artifact(
            self._checkpoint_run_id,
            entry.response_artifact,
        )
        return LLMResponse.model_validate(payload) if payload is not None else None

    @staticmethod
    def _llm_request_hash_version(request_hash: str) -> LLMRequestHashVersion:
        if str(request_hash or "").startswith("v2:sha256:"):
            return LLMRequestHashVersion.PROVIDER_BOUND_V2
        return LLMRequestHashVersion.LEGACY_UNBOUND_V1

    def prepare_tool_call(self, tool_call: Any, selection: ToolSelection) -> bool:
        """Persist mutation intent before the tool is allowed to execute."""
        pending_validation = (
            self._pending_verification.commands[0]
            if self._pending_verification is not None and self._pending_verification.commands
            else None
        )
        violation = self.session_constraint_violation(
            selection,
            task_validation_command=pending_validation,
        )
        if violation is not None:
            if self.state is not None:
                self.state.record_guard_decision(
                    GuardDecisionMetadata(
                        approved=False,
                        reason=f"session_constraint:{violation.value}",
                        risk_level="high",
                        blocked_files=file_mutation_targets(selection),
                        required_evidence=[self.state.session_constraints.canonical_hash],
                    )
                )
            return False
        mutation_class = self._checkpointed_mutation_class(selection)
        if mutation_class is None or not self._checkpointing_enabled:
            return True
        target_files = file_mutation_targets(selection)
        action = {
            "tool_name": selection.tool_name,
            "step_id": selection.step_id,
            "call_id": str(getattr(tool_call, "call_id", "") or ""),
            "tool_input": selection.input_metadata.model_copy(deep=True),
            "target_files": target_files,
            "expected_target_file_hashes": self._expected_file_hashes(selection),
            "pending_verification": (
                self._pending_verification.model_copy(deep=True)
                if self._pending_verification is not None
                else None
            ),
        }
        self._active_tool_checkpoint = action
        assert self.state is not None
        return self._persist_checkpoint(
            self.state,
            reason=f"prepared state mutation: {selection.tool_name}",
            safe_boundary=CheckpointBoundary.TOOL_CALL_PREPARED,
            mutation_class=mutation_class,
            side_effect_state="prepared",
            **action,
        )

    def session_constraint_violation(
        self,
        selection: ToolSelection,
        *,
        task_validation_command: str | None = None,
    ) -> SessionConstraintViolationCode | None:
        """Check active session constraints before any edit/checkpoint side effect."""

        if self.state is None:
            return None
        constraint_state = self.state.session_constraints
        input_metadata = selection.input_metadata
        requested_command = (
            str(input_metadata.requested_command or "").strip()
            or str(input_metadata.command or "").strip()
            or str(input_metadata.run_command or "").strip()
            or str(input_metadata.test_command or "").strip()
        )
        project_root = (
            str(constraint_state.project_root or "").strip()
            or str(input_metadata.project_path or "").strip()
        )
        state_root = str(constraint_state.project_root or "").strip()
        input_root = str(input_metadata.project_path or "").strip()
        if state_root and input_root:
            if Path(state_root).expanduser().resolve(strict=False) != Path(input_root).expanduser().resolve(strict=False):
                return SessionConstraintViolationCode.PROJECT_ROOT_MISMATCH
        if project_root and project_root != constraint_state.project_root:
            constraint_state = constraint_state.model_copy(update={"project_root": project_root})
        command = requested_command or None
        command_is_mutation = bool(file_mutation_targets(selection)) or (
            selection.tool_name == "command_executor"
            and self._command_may_modify_project(str(command or ""))
        )
        return session_constraint_violation(
            constraint_state,
            target_files=file_mutation_targets(selection),
            command=command,
            task_validation_command=task_validation_command,
            command_is_mutation=command_is_mutation,
        )

    def observe_tool_result(self, tool_call: Any, selection: ToolSelection, execution_result: Any) -> bool:
        """Persist a mutation result before it is applied to runtime state."""
        mutation_class = self._checkpointed_mutation_class(selection)
        if mutation_class is None or not self._checkpointing_enabled:
            return True
        action = dict(self._active_tool_checkpoint)
        action.setdefault("tool_name", selection.tool_name)
        action.setdefault("step_id", selection.step_id)
        action.setdefault("call_id", str(getattr(tool_call, "call_id", "") or ""))
        action.setdefault("tool_input", selection.input_metadata.model_copy(deep=True))
        action.setdefault("target_files", file_mutation_targets(selection))
        action.setdefault("expected_target_file_hashes", self._expected_file_hashes(selection))
        output = getattr(execution_result, "output_metadata", None)
        failure = getattr(execution_result, "error", None)
        if failure is not None and not isinstance(failure, FailureMetadata):
            failure = FailureMetadata(
                error_type=type(failure).__name__,
                error_message=str(getattr(failure, "error_message", failure)),
                recoverable=False,
            )
        result_payload = getattr(output, "result", None)
        action["observed_file_result"] = (
            ObservedFileMutationResult(
                success=bool(getattr(execution_result, "success", False)),
                file_path=str(
                    getattr(result_payload, "file_path", "")
                    or selection.input_metadata.file_path
                    or ""
                )
                or None,
                error_type=str(getattr(failure, "error_type", "") or "") or None,
                error_message=str(getattr(failure, "error_message", "") or "") or None,
            )
            if selection.tool_name in FILE_MUTATION_TOOLS
            else None
        )
        action["observed_failure"] = failure
        if mutation_class == "read_only":
            if self.checkpoint_store is None or not self._checkpoint_run_id:
                return False
            output = getattr(execution_result, "output_metadata", None)
            result_value = getattr(output, "result", None)
            payload = {
                "success": bool(getattr(execution_result, "success", False)),
                "tool_name": selection.tool_name,
                "result": result_value.to_json_dict() if isinstance(result_value, FileArtifactMetadata) else None,
                "failure": failure.to_json_dict() if isinstance(failure, FailureMetadata) else None,
            }
            reference = self.checkpoint_store.save_recovery_artifact(
                self._checkpoint_run_id,
                kind="read_tool_result",
                payload=payload,
            )
            entry = ReadToolReplayEntry(
                task_id=str(getattr(tool_call, "task_id", "") or self._active_task_id),
                step_id=selection.step_id,
                call_id=str(getattr(tool_call, "call_id", "") or ""),
                tool_name=selection.tool_name,
                input_hash=str(self._tool_input_hash(selection.input_metadata) or ""),
                result_artifact=reference,
                applied=False,
            )
            self._read_tool_replay_entries = [
                existing
                for existing in self._read_tool_replay_entries
                if existing.call_id != entry.call_id
            ]
            self._read_tool_replay_entries.append(entry)
        self._active_tool_checkpoint = action
        assert self.state is not None
        return self._persist_checkpoint(
            self.state,
            reason=f"observed state mutation result: {selection.tool_name}",
            safe_boundary=CheckpointBoundary.TOOL_RESULT_OBSERVED,
            mutation_class=mutation_class,
            side_effect_state=(
                "observed"
                if bool(getattr(execution_result, "success", False)) and mutation_class == "mutating"
                else "indeterminate"
            ),
            **action,
        )

    def replay_tool_result(self, tool_call: Any, selection: ToolSelection) -> Any | None:
        """Load one checksum-verified read result for an identical deterministic call."""
        if selection.tool_name not in READ_TOOLS or self.checkpoint_store is None or not self._checkpoint_run_id:
            return None
        call_id = str(getattr(tool_call, "call_id", "") or "")
        input_hash = str(self._tool_input_hash(selection.input_metadata) or "")
        entry = next(
            (
                item
                for item in self._read_tool_replay_entries
                if item.call_id == call_id
                and item.tool_name == selection.tool_name
                and item.input_hash == input_hash
            ),
            None,
        )
        if entry is None:
            return None
        payload = self.checkpoint_store.load_recovery_artifact(
            self._checkpoint_run_id,
            entry.result_artifact,
        )
        if payload is None:
            return None
        failure_payload = payload.get("failure")
        failure = FailureMetadata.model_validate(failure_payload) if isinstance(failure_payload, dict) else None
        result_payload = payload.get("result")
        output = (
            ToolResultMetadata(
                tool_name=selection.tool_name,
                status=ResultStatus.SUCCESS,
                result=FileArtifactMetadata.model_validate(result_payload),
            )
            if isinstance(result_payload, dict)
            else None
        )
        self._active_tool_checkpoint = {
            "tool_name": selection.tool_name,
            "step_id": selection.step_id,
            "call_id": call_id,
            "tool_input": selection.input_metadata.model_copy(deep=True),
        }
        return SimpleNamespace(
            success=bool(payload.get("success")),
            output_metadata=output,
            error=failure,
            recovery_already_applied=entry.applied,
        )

    def _record_checkpoint_failure(self, safe_boundary: str, error: str) -> None:
        self._checkpoint_status = CheckpointStatus.UNAVAILABLE
        hooks = getattr(self.runtime, "runtime_diagnostics_hooks", None)
        if hooks and hasattr(hooks, "on_checkpoint_write_failed"):
            hooks.on_checkpoint_write_failed(
                task_id=self._active_task_id,
                session_id=str(getattr(self.runtime, "session_id", "") or ""),
                safe_boundary=safe_boundary,
                error=error,
            )

    def _absorb_session_result(self, state: RuntimeStateMetadata, result: dict[str, Any]) -> None:
        previous_phase = _phase_value(state.phase)
        previous_verification_status = str(state.verification_status or "")
        success = bool(result.get("success"))
        core_success = bool(result.get("core_success", success))
        state.core_success = core_success
        status = result.get("project_improvement_status")
        if status is not None:
            state.project_improvement_status = ProjectImprovementStatus(status)
        state.project_improvement_failure = str(result.get("iteration_error") or "") or None
        stats = result.get("stats")
        if isinstance(stats, dict):
            for key in ("tasks_completed", "tasks_failed"):
                if key in stats:
                    state.add_fact(f"{key}: {stats[key]}")
        for file_path in result.get("written_files") or result.get("changed_files") or []:
            state.add_modified_file(str(file_path))
        if state.modified_files:
            state.verification_status = "passed" if core_success else "failed"
        state.phase = AgentPhase.SUMMARIZE if success else AgentPhase.RECOVER
        state.completion_reason = (
            "runtime session completed"
            if success
            else str(result.get("error") or result.get("failure_reason") or "runtime session failed")
        )
        if _phase_value(state.phase) != previous_phase:
            self._emit_runtime_phase_change(previous_phase, _phase_value(state.phase), state.verification_status, state.completion_reason, state)
        if str(state.verification_status or "") != previous_verification_status:
            self._emit_verification_state_change(previous_verification_status, str(state.verification_status or ""), _phase_value(state.phase), state.completion_reason, state)

    @staticmethod
    def _report_source_hash(state: RuntimeStateMetadata) -> str:
        """Hash task outcome facts while excluding resume bookkeeping."""
        payload = state.to_json_dict()
        for field_name in (
            "recovery_status",
            "recovery_reason_code",
            "active_resume_attempt_id",
        ):
            payload.pop(field_name, None)
        budget = payload.get("budget")
        if isinstance(budget, dict):
            budget.pop("recovery_rounds_used", None)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def _inject_finalization_fault(
        self,
        point: FinalizationFaultPoint,
        cursor: RuntimeFinalizationCursor,
    ) -> None:
        if self._finalization_fault_injector is not None:
            self._finalization_fault_injector(point, cursor.model_copy(deep=True))

    def _persist_finalization_checkpoint(
        self,
        state: RuntimeStateMetadata,
        cursor: RuntimeFinalizationCursor,
        boundary: CheckpointBoundary,
        reason: str,
    ) -> None:
        self._active_finalization_cursor = cursor
        if not self._persist_checkpoint(
            state,
            reason=reason,
            safe_boundary=boundary,
            finalization_cursor=cursor,
        ):
            raise RuntimeError(f"finalization checkpoint is not durable: {boundary.value}")

    def _persist_runtime_report(
        self,
        state: RuntimeStateMetadata,
        cursor: RuntimeFinalizationCursor,
    ) -> tuple[RuntimeReportMetadata, RuntimeFinalizationCursor]:
        report = self.reporter.report(state).model_copy(
            update={"state_hash": cursor.report_source_hash}
        )
        save_artifact = getattr(self.checkpoint_store, "save_recovery_artifact", None)
        if not callable(save_artifact):
            raise RuntimeError("finalization requires a durable report artifact store")
        reference = save_artifact(
            self._checkpoint_run_id,
            kind="runtime_report",
            payload=report.to_json_dict(),
        )
        persisted = RuntimeFinalizationCursor(
            finalization_id=cursor.finalization_id,
            stage=RuntimeFinalizationStage.REPORT_PERSISTED,
            outcome=cursor.outcome,
            report_source_hash=cursor.report_source_hash,
            report_artifact=reference,
        )
        return report, persisted

    def _load_finalization_report(
        self,
        cursor: RuntimeFinalizationCursor | None,
    ) -> RuntimeReportMetadata | None:
        if cursor is None or cursor.report_artifact is None or self.checkpoint_store is None:
            return None
        load_artifact = getattr(self.checkpoint_store, "load_recovery_artifact", None)
        if not callable(load_artifact):
            return None
        payload = load_artifact(self._checkpoint_run_id, cursor.report_artifact)
        if payload is None:
            return None
        try:
            report = RuntimeReportMetadata.model_validate(payload)
        except ValueError:
            return None
        if report.state_hash != cursor.report_source_hash:
            return None
        return report

    def _record_run_finalization(
        self,
        state: RuntimeStateMetadata,
        session_result: dict[str, Any],
        cursor: RuntimeFinalizationCursor,
    ) -> RuntimeFinalizationCursor:
        hooks = getattr(self.runtime, "runtime_diagnostics_hooks", None)
        if hooks is None:
            raise RuntimeError("finalization requires runtime diagnostic hooks")
        root_task_id = self._active_task_id or str(getattr(self.runtime, "session_id", "") or "")
        hooks.on_runtime_state_updated(state)
        report = self._load_finalization_report(cursor)
        if report is None:
            raise RuntimeError("persisted runtime report is unavailable or invalid")
        if bool(session_result.get("success")) and report.residual_risks:
            hooks.on_suspicious_success(task_id=root_task_id, evidence=list(report.residual_risks))
        event = hooks.on_task_finished(
            task_id=root_task_id,
            success=bool(session_result.get("success")),
            summary={
                "phase": _phase_value(state.phase),
                "verification_status": state.verification_status,
                "modified_files": list(state.modified_files),
                "completion_reason": state.completion_reason,
            },
            session_id=str(getattr(self.runtime, "session_id", "") or ""),
            finalization_id=cursor.finalization_id,
        )
        event_id = str(getattr(event, "event_id", "") or "")
        if not event_id:
            raise RuntimeError("run finalization event was not durably recorded")
        return RuntimeFinalizationCursor(
            finalization_id=cursor.finalization_id,
            stage=RuntimeFinalizationStage.RUN_FINALIZED,
            outcome=cursor.outcome,
            report_source_hash=cursor.report_source_hash,
            report_artifact=cursor.report_artifact,
            run_finalized_event_id=event_id,
        )

    def _finalize_runtime(
        self,
        state: RuntimeStateMetadata,
        session_result: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._checkpointing_enabled:
            return self._runtime_result(state, session_result)
        if self._checkpoint_status == CheckpointStatus.UNAVAILABLE:
            # No durable checkpoint boundary was ever established. Preserve the
            # existing best-effort behavior while reporting that recovery is unavailable.
            return self._runtime_result(state, session_result)
        cursor = RuntimeFinalizationCursor(
            finalization_id=uuid.uuid4().hex,
            stage=RuntimeFinalizationStage.STATE_COMPLETED,
            outcome="success" if bool(session_result.get("success")) else "failed",
            report_source_hash=self._report_source_hash(state),
        )
        self._persist_finalization_checkpoint(
            state,
            cursor,
            CheckpointBoundary.RUNTIME_STATE_COMPLETED,
            "runtime state completed",
        )
        self._inject_finalization_fault(FinalizationFaultPoint.AFTER_STATE_COMPLETED, cursor)
        report, cursor = self._persist_runtime_report(state, cursor)
        self._persist_finalization_checkpoint(
            state,
            cursor,
            CheckpointBoundary.RUNTIME_REPORT_PERSISTED,
            "runtime report persisted",
        )
        self._inject_finalization_fault(FinalizationFaultPoint.AFTER_REPORT_PERSISTED, cursor)
        cursor = self._record_run_finalization(state, session_result, cursor)
        self._active_finalization_cursor = cursor
        self._inject_finalization_fault(FinalizationFaultPoint.AFTER_RUN_FINALIZED, cursor)
        self._persist_finalization_checkpoint(
            state,
            cursor,
            CheckpointBoundary.RUNTIME_FINALIZED,
            "runtime finalization completed",
        )
        self._inject_finalization_fault(
            FinalizationFaultPoint.AFTER_FINAL_CHECKPOINT_DURABLE,
            cursor,
        )
        return self._runtime_result(
            state,
            session_result,
            report=report,
            emit_task_finished=False,
        )

    def _resume_finalization(
        self,
        checkpoint: RuntimeCheckpointMetadata,
        decision: RuntimeResumeDecisionMetadata,
    ) -> dict[str, Any]:
        assert self.state is not None
        cursor = checkpoint.finalization_cursor
        if cursor is None:
            raise RuntimeError("finalization resume requires a finalization cursor")
        source_state = checkpoint.runtime_state.model_copy(deep=True)
        success = cursor.outcome == "success"
        session_result: dict[str, Any] = {"success": success, "resumed_finalization": True}
        report = self._load_finalization_report(cursor)
        if cursor.stage == RuntimeFinalizationStage.STATE_COMPLETED:
            report, cursor = self._persist_runtime_report(source_state, cursor)
            self._persist_finalization_checkpoint(
                self.state,
                cursor,
                CheckpointBoundary.RUNTIME_REPORT_PERSISTED,
                "runtime report persisted during resume",
            )
        if report is None:
            report = self._load_finalization_report(cursor)
        if report is None:
            raise RuntimeError("cannot resume finalization without the persisted report")
        if cursor.stage == RuntimeFinalizationStage.REPORT_PERSISTED:
            cursor = self._record_run_finalization(source_state, session_result, cursor)
        self.state.recovery_status = RecoveryStatus.RECOVERED
        self._persist_finalization_checkpoint(
            self.state,
            cursor,
            CheckpointBoundary.RUNTIME_FINALIZED,
            "runtime finalization completed during resume",
        )
        result = self._runtime_result(
            self.state,
            session_result,
            report=report,
            emit_task_finished=False,
        )
        result["resume_decision"] = decision.to_json_dict()
        result["resume_status"] = "finalized_from_checkpoint"
        return result

    def _runtime_result(
        self,
        state: RuntimeStateMetadata,
        session_result: dict[str, Any],
        *,
        report: RuntimeReportMetadata | None = None,
        emit_task_finished: bool = True,
    ) -> dict[str, Any]:
        report = report or self.reporter.report(state)
        hooks = getattr(self.runtime, "runtime_diagnostics_hooks", None)
        if hooks and emit_task_finished:
            root_task_id = self._active_task_id or str(getattr(self.runtime, "session_id", "") or "")
            hooks.on_runtime_state_updated(state)
            if bool(session_result.get("success")) and report.residual_risks:
                hooks.on_suspicious_success(
                    task_id=root_task_id,
                    evidence=list(report.residual_risks),
                )
            hooks.on_task_finished(
                task_id=root_task_id,
                success=bool(session_result.get("success")),
                summary={
                    "phase": state.phase.value if hasattr(state.phase, "value") else str(state.phase),
                    "verification_status": state.verification_status,
                    "modified_files": list(state.modified_files),
                    "completion_reason": state.completion_reason,
                },
                session_id=str(getattr(self.runtime, "session_id", "") or ""),
            )
        result = {
            "success": bool(session_result.get("success")),
            "goal": state.goal,
            "agent_runtime_state": state.to_json_dict(),
            "runtime_report": report.to_json_dict(),
            "session_result": session_result,
            **(
                {
                    "session_ingress_state": self._active_session_ingress_state.model_dump(mode="json")
                }
                if self._active_session_ingress_state is not None
                else {}
            ),
        }
        for key in (
            "failure_reason",
            "failure_stage",
            "failed_tool",
            "failed_call_id",
            "failed_step_id",
            "task_id",
            "task_description",
            "file_path",
            "error_type",
            "suggested_recovery",
            "response_preview",
        ):
            value = session_result.get(key)
            if value not in (None, "", [], {}):
                result[key] = value
        if self._checkpointing_enabled:
            result["checkpoint_status"] = self._checkpoint_status
        return result

    def _handle_state_event_change(self, state: RuntimeStateMetadata, change_kind: str, previous_value: str) -> None:
        if change_kind == "phase":
            self._emit_runtime_phase_change(
                previous_value,
                _phase_value(state.phase),
                str(state.verification_status or ""),
                state.completion_reason,
                state,
            )
            return
        if change_kind == "verification":
            self._emit_verification_state_change(
                previous_value,
                str(state.verification_status or ""),
                _phase_value(state.phase),
                state.completion_reason,
                state,
            )

    def _handle_tool_result_applied(
        self,
        state: RuntimeStateMetadata,
        selection: ToolSelection,
        execution_result: Any,
    ) -> None:
        if selection.tool_name in READ_TOOLS:
            active_call_id = str(self._active_tool_checkpoint.get("call_id") or "")
            self._read_tool_replay_entries = [
                entry.model_copy(update={"applied": True})
                if entry.call_id == active_call_id
                else entry
                for entry in self._read_tool_replay_entries
            ]
            self._persist_checkpoint(
                state,
                reason=f"read-only tool result applied: {selection.tool_name}",
                safe_boundary=CheckpointBoundary.TOOL_RESULT_APPLIED,
                tool_name=selection.tool_name,
                step_id=selection.step_id,
                mutation_class="read_only",
                side_effect_state="applied",
            )
            return
        if selection.tool_name not in FILE_MUTATION_TOOLS:
            if selection.tool_name == "command_executor" and self._active_tool_checkpoint:
                pending = self._active_tool_checkpoint.get("pending_verification")
                executed_command = str(
                    selection.input_metadata.requested_command
                    or selection.input_metadata.command
                    or ""
                ).strip()
                if isinstance(pending, VerificationPlanMetadata) and pending.next_command_index < len(pending.commands):
                    pending_command = str(pending.commands[pending.next_command_index]).strip()
                    if pending_command and executed_command == pending_command:
                        command_succeeded = bool(getattr(execution_result, "success", False))
                        if command_succeeded:
                            next_index = pending.next_command_index + 1
                            pending = pending.model_copy(
                                update={
                                    "next_command_index": next_index,
                                    "completed_commands": list(pending.commands[:next_index]),
                                }
                            )
                            if next_index < len(pending.commands):
                                state.verification_status = VerificationStatus.REQUIRED
                                self._active_tool_checkpoint["pending_verification"] = pending
                                self._pending_verification = pending.model_copy(deep=True)
                                self._persist_checkpoint(
                                    state,
                                    reason=f"verification command {next_index}/{len(pending.commands)} applied",
                                    safe_boundary=CheckpointBoundary.VERIFICATION_REQUIRED,
                                    mutation_class="mutating",
                                    side_effect_state="applied",
                                    **self._active_tool_checkpoint,
                                )
                                return
                            self._active_tool_checkpoint["pending_verification"] = None
                            self._pending_verification = None
                        else:
                            self._persist_checkpoint(
                                state,
                                reason=f"verification command {pending.next_command_index + 1} failed",
                                safe_boundary=CheckpointBoundary.VERIFICATION_REQUIRED,
                                mutation_class="mutating",
                                side_effect_state="indeterminate",
                                **self._active_tool_checkpoint,
                            )
                            return
            if (
                selection.tool_name == "command_executor"
                and self._active_tool_checkpoint.get("tool_name") == "command_executor"
                and state.verification_status == "required"
            ):
                self._persist_checkpoint(
                    state,
                    reason="external command result applied; verification required",
                    safe_boundary=CheckpointBoundary.TOOL_RESULT_APPLIED,
                    mutation_class="externally_indeterminate",
                    side_effect_state="applied",
                    **self._active_tool_checkpoint,
                )
                return
            if (
                selection.tool_name == "command_executor"
                and self._active_tool_checkpoint
                and state.verification_status == "passed"
            ):
                active_mutation_class = (
                    "externally_indeterminate"
                    if self._active_tool_checkpoint.get("tool_name") == "command_executor"
                    else "mutating"
                )
                self._persist_checkpoint(
                    state,
                    reason="state mutation verification applied",
                    safe_boundary=CheckpointBoundary.VERIFICATION_APPLIED,
                    mutation_class=active_mutation_class,
                    side_effect_state="applied",
                    **self._active_tool_checkpoint,
                )
            return
        action = dict(self._active_tool_checkpoint)
        action.setdefault("tool_name", selection.tool_name)
        action.setdefault("step_id", selection.step_id)
        action.setdefault("tool_input", selection.input_metadata.model_copy(deep=True))
        action.setdefault("target_files", [str(selection.input_metadata.file_path)] if selection.input_metadata.file_path else [])
        action.setdefault("expected_target_file_hashes", self._expected_file_hashes(selection))
        success = bool(getattr(execution_result, "success", False))
        self._persist_checkpoint(
            state,
            reason=f"file mutation result applied: {selection.tool_name}",
            safe_boundary=CheckpointBoundary.TOOL_RESULT_APPLIED,
            mutation_class="mutating",
            side_effect_state="applied" if success else "indeterminate",
            **action,
        )

    def _emit_runtime_phase_change(
        self,
        previous_phase: str,
        phase: str,
        verification_status: str,
        completion_reason: str,
        state: RuntimeStateMetadata,
    ) -> None:
        hooks = getattr(self.runtime, "runtime_diagnostics_hooks", None)
        if not hooks:
            return
        hooks.on_runtime_phase_changed(
            task_id=self._active_task_id or str(getattr(self.runtime, "session_id", "") or ""),
            session_id=str(getattr(self.runtime, "session_id", "") or ""),
            previous_phase=previous_phase,
            phase=phase,
            verification_status=verification_status,
            completion_reason=completion_reason,
            state=state,
        )

    def _emit_verification_state_change(
        self,
        previous_status: str,
        verification_status: str,
        phase: str,
        reason: str,
        state: RuntimeStateMetadata,
    ) -> None:
        hooks = getattr(self.runtime, "runtime_diagnostics_hooks", None)
        if not hooks:
            return
        hooks.on_verification_state_changed(
            task_id=self._active_task_id or str(getattr(self.runtime, "session_id", "") or ""),
            session_id=str(getattr(self.runtime, "session_id", "") or ""),
            previous_status=previous_status,
            verification_status=verification_status,
            phase=phase,
            reason=reason,
            state=state,
        )

    def handle_streamed_need(self, need: DecisionNeedMetadata) -> list[ToolSelection]:
        """Interrupt generation for one need and route it through the runtime state."""
        if self.state is None:
            self.state = RuntimeStateMetadata(goal=need.question or "streamed need")
        self.state.record_tool_event(
            {
                "event_type": "stream_need_interrupt",
                "phase": _phase_value(self.state.phase),
                "need_type": need.need_type,
                "question": need.question,
            }
        )
        return self.router.route(self.state, need)

    def absorb_streamed_tool_result(self, selection: ToolSelection, execution_result: Any) -> RuntimeStateMetadata:
        """Resume generation after a streamed tool result has been absorbed."""
        if self.state is None:
            self.state = RuntimeStateMetadata(goal=selection.step_id)
        self.state_updater.apply_tool_result(self.state, selection, execution_result)
        self.state.record_tool_event(
            {
                "event_type": "stream_need_resume",
                "phase": _phase_value(self.state.phase),
                "tool_name": selection.tool_name,
                "success": bool(getattr(execution_result, "success", False)),
            }
        )
        return self.state
