"""Typed outcomes for provider-native tool admission.

Provider wire calls are requests, not execution authority. This module defines
the strict admitted/blocked result shape while reusing the existing project
tool-call, selection, failure, and error contracts.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from metadata import FailureMetadata, ToolCallMetadata, ToolErrorMetadata
from tools.tool_selection import ToolSelection


class ProviderToolAdmissionError(ValueError):
    """Raised when a provider tool-call batch cannot be admitted safely."""


class ProviderToolAdmission(BaseModel):
    """One provider call after the project-owned admission boundary."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["admitted", "blocked"]
    provider_call_id: str
    project_call_id: str
    tool_call: ToolCallMetadata
    selection: ToolSelection | None = None
    tool_error: ToolErrorMetadata | None = None
    requires_confirmation: bool = False

    @model_validator(mode="after")
    def _status_matches_payload(self) -> "ProviderToolAdmission":
        if self.status == "admitted" and (
            self.selection is None or self.tool_error is not None
        ):
            raise ValueError(
                "admitted provider tool call requires selection and no tool_error"
            )
        if self.status == "blocked" and (
            self.selection is not None or self.tool_error is None
        ):
            raise ValueError(
                "blocked provider tool call requires tool_error and no selection"
            )
        if self.tool_call.provider_call_id != self.provider_call_id:
            raise ValueError("provider call ID must match ToolCallMetadata")
        if self.tool_call.call_id != self.project_call_id:
            raise ValueError("project call ID must match ToolCallMetadata")
        return self


def provider_tool_error(
    tool_call: ToolCallMetadata,
    *,
    error_type: str,
    error_message: str,
    recoverable: bool,
    retry_recommended: bool | None = None,
    suggested_recovery: str = "",
) -> ToolErrorMetadata:
    """Project a provider-bound failure into the existing error contract."""

    retry = recoverable if retry_recommended is None else retry_recommended
    failure = FailureMetadata(
        error_type=error_type,
        error_message=error_message,
        recoverable=recoverable,
        retry_recommended=retry,
        recovery_strategy=suggested_recovery,
        details={
            "tool_name": tool_call.tool_name,
            "call_id": tool_call.call_id,
            "provider_call_id": tool_call.provider_call_id,
        },
    )
    return ToolErrorMetadata(
        session_id=tool_call.session_id,
        task_id=tool_call.task_id,
        step_id=tool_call.step_id,
        call_id=tool_call.call_id,
        provider_call_id=tool_call.provider_call_id,
        tool_name=tool_call.tool_name,
        error_type=error_type,
        error_message=error_message,
        recoverable=recoverable,
        suggested_recovery=suggested_recovery,
        failure=failure,
        input_metadata=tool_call.input_metadata,
        tool_context=tool_call.tool_context,
        round_index=tool_call.round_index,
        event_index=tool_call.event_index,
    )


__all__ = [
    "ProviderToolAdmission",
    "ProviderToolAdmissionError",
    "provider_tool_error",
]
