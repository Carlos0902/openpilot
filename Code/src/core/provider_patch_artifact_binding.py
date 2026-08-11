"""Bind verified code artifacts into already-admitted patch calls."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.provider_code_artifact_ledger import (
    ProviderCodeArtifactLedger,
    ProviderCodeArtifactLedgerError,
)
from core.provider_tool_admission import (
    MAX_PROVIDER_SCOPE_PATHS,
    ProviderToolAdmission,
)


class ProviderPatchArtifactBindingError(ValueError):
    """Raised when an admitted patch artifact cannot be bound safely."""


def bind_provider_patch_artifact(
    admission: ProviderToolAdmission,
    *,
    code_artifact_ledger: ProviderCodeArtifactLedger | None = None,
    authorized_post_processing_write_scope: Sequence[str] | None = None,
) -> ProviderToolAdmission:
    """Resolve one admitted writer reference without widening its authority."""

    if not isinstance(admission, ProviderToolAdmission):
        raise ProviderPatchArtifactBindingError(
            "admission must be ProviderToolAdmission"
        )
    if admission.status == "blocked":
        return admission

    input_metadata = admission.tool_call.input_metadata
    if (
        admission.tool_call.tool_name != "file_patch_writer"
        or input_metadata.artifact_ref is None
    ):
        return admission
    if not isinstance(code_artifact_ledger, ProviderCodeArtifactLedger):
        raise ProviderPatchArtifactBindingError(
            "admitted patch artifact requires ProviderCodeArtifactLedger"
        )
    scope = _validated_post_processing_scope(
        authorized_post_processing_write_scope
    )
    try:
        generated_unit = code_artifact_ledger.resolve(
            input_metadata.artifact_ref
        )
    except ProviderCodeArtifactLedgerError as exc:
        raise ProviderPatchArtifactBindingError(
            "admitted patch artifact reference could not be resolved"
        ) from exc

    updates: dict[str, Any] = {"generated_unit": generated_unit}
    if scope is not None:
        runtime_handles = dict(input_metadata.runtime_handles)
        runtime_handles["_post_processing_write_scope"] = scope
        updates["runtime_handles"] = runtime_handles
    bound_input = input_metadata.model_copy(update=updates)
    tool_call = admission.tool_call.model_copy(
        update={"input_metadata": bound_input}
    )
    if admission.selection is None:
        raise ProviderPatchArtifactBindingError(
            "admitted patch artifact requires a tool selection"
        )
    selection = admission.selection.model_copy(
        update={"input_metadata": bound_input}
    )
    return admission.model_copy(
        update={
            "tool_call": tool_call,
            "selection": selection,
        }
    )


def _validated_post_processing_scope(
    value: Sequence[str] | None,
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ProviderPatchArtifactBindingError(
            "post-processing write scope must be a bounded sequence"
        )
    if len(value) > MAX_PROVIDER_SCOPE_PATHS:
        raise ProviderPatchArtifactBindingError(
            "post-processing write scope exceeds the provider path limit"
        )
    scope: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ProviderPatchArtifactBindingError(
                "post-processing write scope paths must be non-empty strings"
            )
        if item in scope:
            raise ProviderPatchArtifactBindingError(
                "post-processing write scope paths must be unique"
            )
        scope.append(item)
    return tuple(scope)


__all__ = [
    "ProviderPatchArtifactBindingError",
    "bind_provider_patch_artifact",
]
