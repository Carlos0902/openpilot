"""Route admitted provider batches into the matching execution bridge."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.provider_code_artifact_ledger import ProviderCodeArtifactLedger
from core.provider_tool_admission import ProviderToolAdmission
from core.provider_tool_execution_batch import (
    ProviderToolExecutionBatchError,
    validated_provider_execution_batch,
)
from tools.mutation_descriptor import FILE_MUTATION_TOOLS


class ProviderExecutionDispatchError(ValueError):
    """Raised when an admitted batch cannot be routed safely."""


def dispatch_provider_execution_batch(
    runner: Any,
    task: Any,
    admissions: list[ProviderToolAdmission] | tuple[ProviderToolAdmission, ...],
    *,
    round_index: int,
    allow_mutations: bool,
    code_artifact_ledger: ProviderCodeArtifactLedger | None = None,
    authorized_post_processing_write_scope: Sequence[str] | None = None,
):
    """Dispatch one bounded batch without reinterpreting provider arguments."""

    task_id = str(getattr(task, "id", "unknown"))
    session_id = runner.owner._session_id()
    try:
        batch = validated_provider_execution_batch(
            admissions,
            task_id=task_id,
            session_id=session_id,
            round_index=round_index,
            allow_mutations=allow_mutations,
        )
    except ProviderToolExecutionBatchError as exc:
        raise ProviderExecutionDispatchError(str(exc)) from exc

    has_admitted_mutation = any(
        item.status == "admitted"
        and item.tool_call.tool_name in FILE_MUTATION_TOOLS
        for item in batch
    )
    if has_admitted_mutation:
        return runner.run_provider_mutation_tool_calls(
            task,
            batch,
            round_index=round_index,
            code_artifact_ledger=code_artifact_ledger,
            authorized_post_processing_write_scope=(
                authorized_post_processing_write_scope
            ),
        )
    return runner.run_provider_tool_calls(
        task,
        batch,
        round_index=round_index,
    )


__all__ = [
    "ProviderExecutionDispatchError",
    "dispatch_provider_execution_batch",
]
