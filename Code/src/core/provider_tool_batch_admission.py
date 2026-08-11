"""Bounded provider-native tool-call batch admission."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.llm import LLMToolCall
from core.provider_tool_admission import (
    ProviderToolAdmission,
    ProviderToolAdmissionError,
    ProviderToolBudgetUsage,
    admit_provider_mutation_tool_call,
    admit_provider_tool_call,
    provider_tool_resource_usage,
)
from core.tool_contracts import ToolCapability
from metadata import RuntimeBudgetMetadata
from tools.mutation_descriptor import FILE_MUTATION_TOOLS

MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE = 32


def admit_provider_tool_calls(
    tool_calls: Sequence[LLMToolCall],
    *,
    task_id: str,
    session_id: str,
    round_index: int,
    registry: Any,
    budget: RuntimeBudgetMetadata,
    user_confirmed: bool = False,
    allow_mutations: bool = False,
    read_scope: Sequence[str] | None = None,
    write_scope: Sequence[str] | None = None,
    project_path: str | None = None,
    validation_command: str | None = None,
    validation_cwd: str | None = None,
    validation_commands_used: int = 0,
) -> list[ProviderToolAdmission]:
    """Admit a bounded provider batch without executing any selection."""

    _validate_batch_entry(
        task_id=task_id,
        session_id=session_id,
        round_index=round_index,
        user_confirmed=user_confirmed,
        allow_mutations=allow_mutations,
        validation_commands_used=validation_commands_used,
    )
    calls = list(tool_calls)
    if len(calls) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE:
        raise ProviderToolAdmissionError(
            "provider responses may request at most "
            f"{MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE} tool calls"
        )
    provider_ids = [call.id for call in calls]
    if len(provider_ids) != len(set(provider_ids)):
        raise ProviderToolAdmissionError(
            "provider tool call IDs must be unique within one response"
        )

    admissions: list[ProviderToolAdmission] = []
    prior = ProviderToolBudgetUsage()
    for ordinal, call in enumerate(calls, start=1):
        common = {
            "task_id": task_id,
            "session_id": session_id,
            "round_index": round_index,
            "ordinal": ordinal,
            "registry": registry,
            "budget": budget,
            "prior_usage": prior,
            "user_confirmed": user_confirmed,
            "project_path": project_path,
            "validation_command": validation_command,
            "validation_cwd": validation_cwd,
            "validation_commands_used": (
                validation_commands_used + prior.validation
            ),
        }
        if _provider_call_is_mutating(call, registry):
            admission = admit_provider_mutation_tool_call(
                call,
                allow_mutations=allow_mutations,
                write_scope=write_scope,
                **common,
            )
        else:
            admission = admit_provider_tool_call(
                call,
                read_scope=read_scope,
                **common,
            )
        admissions.append(admission)
        if admission.status == "admitted":
            prior = _accumulate_usage(
                prior,
                admission,
                registry=registry,
                validation_command=validation_command,
            )
    return admissions


def _validate_batch_entry(
    *,
    task_id: str,
    session_id: str,
    round_index: int,
    user_confirmed: bool,
    allow_mutations: bool,
    validation_commands_used: int,
) -> None:
    if not str(task_id).strip() or not str(session_id).strip():
        raise ProviderToolAdmissionError("task_id and session_id are required")
    if round_index < 1:
        raise ProviderToolAdmissionError("round_index must be positive")
    if type(user_confirmed) is not bool or type(allow_mutations) is not bool:
        raise ProviderToolAdmissionError(
            "batch authority controls must be literal booleans"
        )
    if validation_commands_used < 0:
        raise ProviderToolAdmissionError(
            "validation_commands_used must be non-negative"
        )


def _provider_call_is_mutating(call: LLMToolCall, registry: Any) -> bool:
    tool_name = call.function.name
    if tool_name in FILE_MUTATION_TOOLS:
        return True
    definition = getattr(registry, "get", lambda _name: None)(tool_name)
    capabilities = {
        str(getattr(capability, "value", capability))
        for capability in (getattr(definition, "capabilities", []) or [])
    }
    return bool(
        {
            ToolCapability.FILE_WRITE.value,
            ToolCapability.FILE_DELETE.value,
        }
        & capabilities
    )


def _accumulate_usage(
    prior: ProviderToolBudgetUsage,
    admission: ProviderToolAdmission,
    *,
    registry: Any,
    validation_command: str | None,
) -> ProviderToolBudgetUsage:
    definition = getattr(registry, "get", lambda _name: None)(
        admission.tool_call.tool_name
    )
    usage = provider_tool_resource_usage(
        definition,
        admission.tool_call.input_metadata,
        validation_command=validation_command,
    )
    return ProviderToolBudgetUsage(
        calls=prior.calls + usage.calls,
        reads=prior.reads + usage.reads,
        edits=prior.edits + usage.edits,
        creates=prior.creates + usage.creates,
        validation=prior.validation + usage.validation,
    )


__all__ = [
    "MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE",
    "admit_provider_tool_calls",
]
