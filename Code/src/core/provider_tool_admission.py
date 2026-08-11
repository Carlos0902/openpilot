"""Typed outcomes for provider-native tool admission.

Provider wire calls are requests, not execution authority. This module defines
the strict admitted/blocked result shape while reusing the existing project
tool-call, selection, failure, and error contracts.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from core.llm import LLMToolCall
from core.provider_tool_definitions import MAX_PROVIDER_FIELDS_PER_TOOL
from metadata import (
    FailureMetadata,
    ToolCallMetadata,
    ToolErrorMetadata,
    ToolInputMetadata,
)
from tools.tool_selection import ToolSelection

MAX_PROVIDER_TOOL_ARGUMENT_CHARS = 200_000


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


def decode_provider_tool_arguments(
    call: LLMToolCall,
) -> tuple[dict[str, Any], str | None]:
    """Decode one provider argument payload without accepting non-object JSON."""

    raw = str(call.function.arguments or "").strip()
    if not raw:
        return {}, None
    if len(raw) > MAX_PROVIDER_TOOL_ARGUMENT_CHARS:
        return {}, (
            "Provider tool arguments exceed "
            f"{MAX_PROVIDER_TOOL_ARGUMENT_CHARS} characters."
        )
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, (
            f"Tool {call.function.name} arguments are not valid JSON: {exc.msg}."
        )
    if not isinstance(decoded, dict):
        return {}, f"Tool {call.function.name} arguments must be a JSON object."
    return decoded, None


def provider_tool_contract_error(
    definition: Any,
    input_metadata: ToolInputMetadata,
) -> str | None:
    """Return the first missing typed contract requirement, if any."""

    contract = getattr(definition, "contract_metadata", None)
    if contract is None:
        return None
    declared_fields = {
        str(field)
        for field in [
            *(getattr(contract, "required_input_fields", []) or []),
            *[
                field
                for group in (getattr(contract, "required_any_of", []) or [])
                for field in group
            ],
            *[
                field
                for requirement in (
                    getattr(contract, "conditional_requirements", []) or []
                )
                if isinstance(requirement, dict)
                for field in [
                    *(requirement.get("when", {}) or {}).keys(),
                    *(requirement.get("required", []) or []),
                    *[
                        field
                        for group in (
                            requirement.get("required_any_of", []) or []
                        )
                        for field in group
                    ],
                ]
            ],
        ]
    }
    if len(declared_fields) > MAX_PROVIDER_FIELDS_PER_TOOL:
        return (
            "Provider tool contracts may declare at most "
            f"{MAX_PROVIDER_FIELDS_PER_TOOL} input fields."
        )
    missing = [
        field
        for field in (getattr(contract, "required_input_fields", []) or [])
        if not getattr(input_metadata, field, None)
    ]
    if missing:
        return (
            f"Missing required input field(s) for {input_metadata.tool_name}: "
            f"{', '.join(missing)}"
        )

    required_any_of = getattr(contract, "required_any_of", []) or []
    if required_any_of and not _any_required_group_present(
        input_metadata, required_any_of
    ):
        readable = _readable_required_groups(required_any_of)
        return (
            f"Missing required input for {input_metadata.tool_name}: "
            f"provide {readable}"
        )

    for requirement in getattr(contract, "conditional_requirements", []) or []:
        if not isinstance(requirement, dict):
            continue
        when = requirement.get("when")
        if not isinstance(when, dict) or not when:
            continue
        if not _condition_matches(input_metadata, when):
            continue
        boundary = ", ".join(f"{key}={value}" for key, value in when.items())
        conditional_missing = [
            str(field)
            for field in (requirement.get("required", []) or [])
            if not getattr(input_metadata, str(field), None)
        ]
        if conditional_missing:
            return (
                f"Missing required input field(s) for {input_metadata.tool_name} "
                f"when {boundary}: {', '.join(conditional_missing)}"
            )
        conditional_any_of = requirement.get("required_any_of", []) or []
        if conditional_any_of and not _any_required_group_present(
            input_metadata, conditional_any_of
        ):
            return (
                f"Missing required input for {input_metadata.tool_name} when "
                f"{boundary}: provide "
                f"{_readable_required_groups(conditional_any_of)}"
            )
    return None


def _any_required_group_present(
    input_metadata: ToolInputMetadata,
    groups: list[list[str]],
) -> bool:
    return any(
        all(
            getattr(input_metadata, str(field), None) not in (None, "", [], {})
            for field in group
        )
        for group in groups
    )


def _readable_required_groups(groups: list[list[str]]) -> str:
    return " or ".join(
        " + ".join(str(field) for field in group) for group in groups
    )


def _condition_matches(
    input_metadata: ToolInputMetadata,
    when: dict[str, Any],
) -> bool:
    for field, expected in when.items():
        actual = getattr(input_metadata, str(field), None)
        if isinstance(expected, str):
            if str(actual or "").strip().lower() != expected.strip().lower():
                return False
        elif actual != expected:
            return False
    return True


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
    "MAX_PROVIDER_TOOL_ARGUMENT_CHARS",
    "ProviderToolAdmission",
    "ProviderToolAdmissionError",
    "decode_provider_tool_arguments",
    "provider_tool_contract_error",
    "provider_tool_error",
]
