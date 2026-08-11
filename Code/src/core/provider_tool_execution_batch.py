"""Preflight provider admissions before any execution lifecycle mutation."""

from __future__ import annotations

from typing import Any

from core.provider_tool_admission import ProviderToolAdmission
from core.provider_tool_batch_admission import (
    MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE,
)
from tools.mutation_descriptor import FILE_MUTATION_TOOLS


class ProviderToolExecutionBatchError(ValueError):
    """Raised when an admitted provider batch is unsafe to execute."""


def validated_readonly_provider_execution_batch(
    admissions: Any,
    *,
    task_id: str,
    session_id: str,
    round_index: int,
) -> tuple[ProviderToolAdmission, ...]:
    """Return one bounded identity-consistent batch with no admitted mutation."""

    return validated_provider_execution_batch(
        admissions,
        task_id=task_id,
        session_id=session_id,
        round_index=round_index,
        allow_mutations=False,
    )


def validated_provider_execution_batch(
    admissions: Any,
    *,
    task_id: str,
    session_id: str,
    round_index: int,
    allow_mutations: bool,
) -> tuple[ProviderToolAdmission, ...]:
    """Return one bounded identity-consistent admitted execution batch."""

    if not isinstance(task_id, str) or not task_id.strip():
        raise ProviderToolExecutionBatchError("task_id must be a non-empty string")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ProviderToolExecutionBatchError(
            "session_id must be a non-empty string"
        )
    if type(round_index) is not int or round_index < 1:
        raise ProviderToolExecutionBatchError(
            "round_index must be a positive integer"
        )
    if type(allow_mutations) is not bool:
        raise ProviderToolExecutionBatchError(
            "allow_mutations must be a literal boolean"
        )
    if not isinstance(admissions, (list, tuple)):
        raise ProviderToolExecutionBatchError(
            "provider admissions must be a bounded list or tuple"
        )
    if len(admissions) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE:
        raise ProviderToolExecutionBatchError(
            "provider admissions exceed the per-response call limit"
        )
    if any(not isinstance(item, ProviderToolAdmission) for item in admissions):
        raise ProviderToolExecutionBatchError(
            "provider admissions must contain ProviderToolAdmission values"
        )

    provider_ids = [item.provider_call_id for item in admissions]
    project_ids = [item.project_call_id for item in admissions]
    if len(provider_ids) != len(set(provider_ids)):
        raise ProviderToolExecutionBatchError(
            "provider admissions must have unique provider call IDs"
        )
    if len(project_ids) != len(set(project_ids)):
        raise ProviderToolExecutionBatchError(
            "provider admissions must have unique project call IDs"
        )
    if any(
        item.tool_call.task_id != task_id
        or item.tool_call.session_id != session_id
        or item.tool_call.round_index != round_index
        for item in admissions
    ):
        raise ProviderToolExecutionBatchError(
            "provider admissions must match the current task, session, and round"
        )
    if any(
        item.status == "admitted"
        and (
            item.selection is None
            or item.selection.tool_name != item.tool_call.tool_name
        )
        for item in admissions
    ):
        raise ProviderToolExecutionBatchError(
            "admitted provider call and selection must name the same tool"
        )
    if any(
        item.status == "admitted"
        and (
            item.tool_call.tool_name in FILE_MUTATION_TOOLS
            or (
                item.selection is not None
                and item.selection.tool_name in FILE_MUTATION_TOOLS
            )
        )
        for item in admissions
    ) and not allow_mutations:
        raise ProviderToolExecutionBatchError(
            "read-only provider execution batch cannot contain mutation admissions"
        )
    return tuple(admissions)


__all__ = [
    "ProviderToolExecutionBatchError",
    "validated_provider_execution_batch",
    "validated_readonly_provider_execution_batch",
]
