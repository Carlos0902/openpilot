"""Explicit task-entry adapter for bounded provider read-only execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from autonomous_iteration.task_models import (
    Task,
    TaskExecutionContext,
    TaskExecutionResult,
    TaskStatus,
)
from core.config import ProviderToolExecutionBudget, ProviderToolExecutionBudgetProfile
from core.provider_readonly_roundtrip import ProviderReadonlyRoundTripRunner
from core.provider_tool_definitions import build_provider_tool_definitions
from core.tool_contracts import ToolCapability
from metadata import FailureMetadata, ResultStatus, TaskResultMetadata, TextArtifactMetadata
from tools.mutation_descriptor import FILE_MUTATION_TOOLS


READONLY_PROVIDER_PROFILES = frozenset(
    {
        ProviderToolExecutionBudgetProfile.CANARY,
        ProviderToolExecutionBudgetProfile.REAL_READ_ONLY,
    }
)


def execute_provider_readonly_task(
    executor: Any,
    task: Task,
    context: TaskExecutionContext,
    *,
    tool_names: Sequence[str],
    max_rounds: int | None = None,
) -> TaskExecutionResult:
    """Run one explicitly enabled provider-native read-only task."""

    started_at = datetime.now()
    settings = getattr(getattr(executor.runtime, "llm_client", None), "settings", None)
    raw_enabled = getattr(settings, "provider_tool_execution_enabled", False)
    enabled = raw_enabled if type(raw_enabled) is bool else False

    def failure(error_type: str, message: str, **details: Any) -> TaskExecutionResult:
        duration = (datetime.now() - started_at).total_seconds()
        return TaskExecutionResult(
            task_id=task.id,
            status=TaskStatus.FAILED,
            error=message,
            duration=duration,
            result_metadata=TaskResultMetadata(
                task_id=task.id,
                status=ResultStatus.FAIL,
                failure=FailureMetadata(
                    error_type=error_type,
                    error_message=message,
                    recoverable=False,
                    details={"task_id": task.id, **details},
                ),
                duration=duration,
            ),
            attributes={
                "provider_tool_execution": True,
                "provider_tool_execution_enabled": enabled,
            },
        )

    if not enabled:
        return failure(
            "ProviderToolExecutionDisabled",
            "Provider-native task execution is disabled; enable the explicit provider flag first.",
        )
    if settings is None:
        return failure("ProviderSettingsMissing", "Provider-native task execution requires LLM settings.")
    if type(raw_enabled) is not bool:
        return failure(
            "ProviderToolExecutionFlagInvalid",
            "provider_tool_execution_enabled must be a literal boolean.",
        )
    if task.write_files:
        return failure(
            "ProviderReadonlyWriteScopeNotAllowed",
            "The read-only provider entry cannot execute tasks with write_files.",
        )
    if not task.read_files:
        return failure(
            "ProviderReadonlyReadScopeRequired",
            "The read-only provider entry requires an explicit non-empty read_files scope.",
        )
    if not isinstance(tool_names, Sequence) or isinstance(tool_names, (str, bytes)):
        return failure("ProviderToolAllowlistInvalid", "Provider-native tool_names must be a bounded sequence.")
    if any(not isinstance(name, str) for name in tool_names):
        return failure("ProviderToolAllowlistInvalid", "Provider-native tool_names must contain only strings.")
    normalized_tools = tuple(name.strip() for name in tool_names)
    if not normalized_tools or any(not name for name in normalized_tools):
        return failure("ProviderToolAllowlistRequired", "Provider-native tasks require a non-empty tool allowlist.")
    if len(normalized_tools) != len(set(normalized_tools)):
        return failure("ProviderToolAllowlistDuplicate", "Provider-native tool_names must be unique.")
    if any(name in FILE_MUTATION_TOOLS for name in normalized_tools):
        return failure("ProviderReadonlyMutationTool", "Mutation tools are not allowed on the read-only provider entry.")

    registry = getattr(executor.runtime, "tool_registry", None)
    if registry is None:
        return failure("ProviderToolRegistryMissing", "Provider-native task execution requires a tool registry.")
    for name in normalized_tools:
        definition = registry.get(name) if hasattr(registry, "get") else None
        capabilities = {
            str(capability.value if hasattr(capability, "value") else capability)
            for capability in (getattr(definition, "capabilities", []) or [])
        }
        if definition is None or ToolCapability.FILE_WRITE.value in capabilities or ToolCapability.FILE_DELETE.value in capabilities:
            return failure("ProviderReadonlyMutationTool", f"Tool is not read-only or is not registered: {name}.")

    try:
        profile = ProviderToolExecutionBudgetProfile(
            getattr(settings, "provider_tool_execution_budget_profile", ProviderToolExecutionBudgetProfile.CANARY)
        )
        budget_profile = ProviderToolExecutionBudget.for_profile(profile)
    except (TypeError, ValueError) as exc:
        return failure("ProviderToolBudgetProfileInvalid", f"Invalid provider-native budget profile: {exc}")
    if profile not in READONLY_PROVIDER_PROFILES:
        return failure(
            "ProviderReadonlyBudgetProfileInvalid",
            "The read-only provider entry requires canary or real_read_only budget profile.",
        )

    configured_rounds = int(getattr(settings, "provider_tool_execution_max_rounds", 3) or 3)
    if max_rounds is None:
        effective_rounds = min(configured_rounds, budget_profile.max_rounds)
    elif isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or not 1 <= max_rounds <= budget_profile.max_rounds:
        return failure(
            "ProviderToolMaxRoundsInvalid",
            f"max_rounds must be between 1 and {budget_profile.max_rounds} for the selected budget profile.",
        )
    else:
        effective_rounds = max_rounds

    controller = getattr(executor.runtime, "runtime_controller", None)
    state = getattr(controller, "state", None)
    if controller is None or state is None:
        return failure("ProviderRuntimeControllerMissing", "Provider-native task execution requires an active runtime controller.")
    if not hasattr(state, "budget") or not hasattr(state.budget, "model_copy"):
        return failure("ProviderRuntimeBudgetMissing", "Provider-native task execution requires a typed runtime budget.")
    project_path = str(
        context.parent_context.get("project_path", "")
        or context.shared_state.get("project_path", "")
        or ""
    ).strip() or None
    state.budget = state.budget.model_copy(
        update={
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

    try:
        tools = build_provider_tool_definitions(registry, normalized_tools)
        roundtrip = ProviderReadonlyRoundTripRunner(
            executor,
            task,
            tools=tools,
            read_scope=task.read_files,
            project_path=project_path,
            max_rounds=effective_rounds,
            max_tokens=budget_profile.completion_ceiling,
            context_max_prompt_tokens=budget_profile.context_max_prompt_tokens,
        ).run([
            # The task entry owns only a minimal user message; richer initial
            # candidate projection is a separately gated follow-up slice.
            executor._provider_task_user_message(task, context)
            if hasattr(executor, "_provider_task_user_message")
            else _task_user_message(task, context),
        ])
    except Exception as exc:
        return failure("ProviderReadonlyTaskSetupFailed", str(exc))

    duration = (datetime.now() - started_at).total_seconds()
    response = roundtrip.final_response
    output = {
        "provider_tool_execution": True,
        "rounds_used": roundtrip.rounds_used,
        "attempt_count": len(roundtrip.attempts),
        "provider": response.provider if response is not None else "",
        "model": response.model if response is not None else "",
    }
    if roundtrip.success:
        content = response.content if response is not None else ""
        return TaskExecutionResult(
            task_id=task.id,
            status=TaskStatus.COMPLETED,
            duration=duration,
            result_metadata=TaskResultMetadata(
                task_id=task.id,
                status=ResultStatus.SUCCESS,
                result=TextArtifactMetadata(content=content, attributes=output),
                duration=duration,
            ),
            attributes=output,
        )
    error_message = roundtrip.error_message or "ProviderReadonlyRoundTripFailed"
    return TaskExecutionResult(
        task_id=task.id,
        status=TaskStatus.FAILED,
        error=error_message,
        duration=duration,
        result_metadata=TaskResultMetadata(
            task_id=task.id,
            status=ResultStatus.FAIL,
            failure=FailureMetadata(
                error_type=error_message.split(":", 1)[0][:128],
                error_message=error_message,
                recoverable=False,
                details=output,
            ),
            duration=duration,
        ),
        attributes=output,
    )


def _task_user_message(task: Task, context: TaskExecutionContext) -> Any:
    from core.llm import LLMMessage

    goal = str(context.parent_context.get("goal", "") or "")
    return LLMMessage(
        role="user",
        content=f"Task: {task.description}\nGoal: {goal}\nRead only from: {list(task.read_files)}",
    )


__all__ = ["execute_provider_readonly_task"]
