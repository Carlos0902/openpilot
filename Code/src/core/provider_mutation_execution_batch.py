"""Prepare admitted provider mutations without executing their side effects."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.provider_code_artifact_ledger import ProviderCodeArtifactLedger
from core.provider_patch_artifact_binding import (
    ProviderPatchArtifactBindingError,
    bind_provider_patch_artifact,
)
from core.provider_tool_admission import ProviderToolAdmission
from core.provider_tool_execution_batch import (
    ProviderToolExecutionBatchError,
    validated_provider_execution_batch,
)
from tools.mutation_descriptor import FILE_MUTATION_TOOLS


class ProviderMutationExecutionBatchError(ValueError):
    """Raised when a provider mutation batch cannot be prepared safely."""


def prepare_provider_mutation_execution_batch(
    admissions: Any,
    *,
    task_id: str,
    session_id: str,
    round_index: int,
    code_artifact_ledger: ProviderCodeArtifactLedger | None = None,
    authorized_post_processing_write_scope: Sequence[str] | None = None,
) -> tuple[ProviderToolAdmission, ...]:
    """Bind admitted patch inputs atomically before lifecycle execution."""

    try:
        batch = validated_provider_execution_batch(
            admissions,
            task_id=task_id,
            session_id=session_id,
            round_index=round_index,
            allow_mutations=True,
        )
    except ProviderToolExecutionBatchError as exc:
        raise ProviderMutationExecutionBatchError(str(exc)) from exc

    admitted_mutations = [
        item
        for item in batch
        if item.status == "admitted"
        and item.tool_call.tool_name in FILE_MUTATION_TOOLS
    ]
    if any(
        item.tool_call.tool_name != "file_patch_writer"
        for item in admitted_mutations
    ):
        raise ProviderMutationExecutionBatchError(
            "provider mutation execution supports file_patch_writer only"
        )
    if admitted_mutations and authorized_post_processing_write_scope is None:
        raise ProviderMutationExecutionBatchError(
            "admitted mutation requires an explicit post-processing write scope"
        )

    prepared: list[ProviderToolAdmission] = []
    try:
        for admission in batch:
            if (
                admission.status == "admitted"
                and admission.tool_call.tool_name == "file_patch_writer"
            ):
                admission = bind_provider_patch_artifact(
                    admission,
                    code_artifact_ledger=code_artifact_ledger,
                    authorized_post_processing_write_scope=(
                        authorized_post_processing_write_scope
                    ),
                )
            prepared.append(admission)
    except ProviderPatchArtifactBindingError as exc:
        raise ProviderMutationExecutionBatchError(
            "provider mutation batch could not be prepared"
        ) from exc
    return tuple(prepared)


__all__ = [
    "ProviderMutationExecutionBatchError",
    "prepare_provider_mutation_execution_batch",
]
