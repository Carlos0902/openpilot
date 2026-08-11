"""Typed outcomes for provider-native tool admission.

Provider wire calls are requests, not execution authority. This module defines
the strict admitted/blocked result shape while reusing the existing project
tool-call, selection, failure, and error contracts.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.llm import LLMToolCall
from core.provider_tool_definitions import MAX_PROVIDER_FIELDS_PER_TOOL
from core.tool_contracts import PermissionLevel, ToolCapability
from core.validation_command import validation_commands_match
from metadata import (
    FailureMetadata,
    RuntimeBudgetMetadata,
    ToolCallMetadata,
    ToolErrorMetadata,
    ToolInputMetadata,
)
from tools.tool_selection import SelectionReason, ToolSelection
from tools.mutation_descriptor import FILE_MUTATION_TOOLS

MAX_PROVIDER_TOOL_ARGUMENT_CHARS = 200_000
MAX_PROVIDER_SCOPE_PATHS = 64


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


class ProviderToolResourceUsage(BaseModel):
    """Resource cost of one provider tool call before admission."""

    model_config = ConfigDict(extra="forbid")

    calls: Literal[1] = 1
    reads: int = Field(default=0, ge=0, le=1)
    edits: int = Field(default=0, ge=0, le=1)
    creates: int = Field(default=0, ge=0, le=1)
    validation: int = Field(default=0, ge=0, le=1)


class ProviderToolBudgetUsage(BaseModel):
    """Usage already admitted inside the current provider batch."""

    model_config = ConfigDict(extra="forbid")

    calls: int = Field(default=0, ge=0)
    reads: int = Field(default=0, ge=0)
    edits: int = Field(default=0, ge=0)
    creates: int = Field(default=0, ge=0)
    validation: int = Field(default=0, ge=0)


class ProviderToolBudgetDecision(BaseModel):
    """Typed runtime-budget decision for one provider tool call."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["admitted", "blocked"]
    reason_code: Literal[
        "within_budget",
        "tool_calls_exhausted",
        "file_reads_exhausted",
        "file_edits_exhausted",
        "file_creates_exhausted",
        "validation_exhausted",
    ]
    usage: ProviderToolResourceUsage

    @model_validator(mode="after")
    def _status_matches_reason(self) -> "ProviderToolBudgetDecision":
        if (self.reason_code == "within_budget") != (self.status == "admitted"):
            raise ValueError("budget decision status must match reason_code")
        return self


class ProviderValidationCommandDecision(BaseModel):
    """Typed admission result for the task-owned exact validation command."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["admitted", "blocked"]
    reason_code: Literal[
        "exact_match",
        "duplicate_validation",
        "command_mismatch",
        "invalid_mode",
        "cwd_mismatch",
        "unexpected_cwd",
    ]
    effective_mode: Literal["automatic", "execute", "run", "exec"] | None = None
    effective_cwd: str | None = None

    @model_validator(mode="after")
    def _status_matches_reason(self) -> "ProviderValidationCommandDecision":
        admitted = self.status == "admitted"
        if admitted != (self.reason_code == "exact_match"):
            raise ValueError("validation decision status must match reason_code")
        if admitted != (self.effective_mode is not None):
            raise ValueError("only admitted validation decisions expose effective mode")
        if not admitted and self.effective_cwd is not None:
            raise ValueError("blocked validation decisions cannot expose effective cwd")
        return self


class ProviderToolPermissionDecision(BaseModel):
    """Typed permission and mutation admission for one registered tool."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["admitted", "blocked"]
    reason_code: Literal[
        "allowed",
        "forbidden",
        "unknown_permission",
        "confirmation_required",
        "mutation_not_allowed",
    ]
    requires_confirmation: bool
    mutating: bool

    @model_validator(mode="after")
    def _status_matches_reason(self) -> "ProviderToolPermissionDecision":
        if (self.status == "admitted") != (self.reason_code == "allowed"):
            raise ValueError("permission decision status must match reason_code")
        if self.reason_code == "confirmation_required" and not self.requires_confirmation:
            raise ValueError("confirmation-required decision must expose that requirement")
        if self.status == "admitted" and self.mutating and not self.requires_confirmation:
            raise ValueError("admitted mutation must retain confirmation evidence")
        return self


def admit_provider_tool_call(
    call: LLMToolCall,
    *,
    task_id: str,
    session_id: str,
    round_index: int,
    ordinal: int,
    registry: Any,
    budget: RuntimeBudgetMetadata,
    prior_usage: ProviderToolBudgetUsage,
    user_confirmed: bool,
    read_scope: Sequence[str] | None,
    project_path: str | None,
    validation_command: str | None = None,
    validation_cwd: str | None = None,
    validation_commands_used: int = 0,
) -> ProviderToolAdmission:
    """Admit one provider-native read-only call without executing it."""

    return _admit_provider_tool_call(
        call,
        task_id=task_id,
        session_id=session_id,
        round_index=round_index,
        ordinal=ordinal,
        registry=registry,
        budget=budget,
        prior_usage=prior_usage,
        user_confirmed=user_confirmed,
        allow_mutations=False,
        mutation_entry=False,
        read_scope=read_scope,
        write_scope=None,
        project_path=project_path,
        validation_command=validation_command,
        validation_cwd=validation_cwd,
        validation_commands_used=validation_commands_used,
    )


def admit_provider_mutation_tool_call(
    call: LLMToolCall,
    *,
    task_id: str,
    session_id: str,
    round_index: int,
    ordinal: int,
    registry: Any,
    budget: RuntimeBudgetMetadata,
    prior_usage: ProviderToolBudgetUsage,
    user_confirmed: bool,
    allow_mutations: bool,
    write_scope: Sequence[str] | None,
    project_path: str | None,
    validation_command: str | None,
    validation_cwd: str | None = None,
    validation_commands_used: int = 0,
) -> ProviderToolAdmission:
    """Admit one patch mutation without executing or validating it."""

    if type(allow_mutations) is not bool:
        raise ProviderToolAdmissionError(
            "allow_mutations must be a literal boolean"
        )
    return _admit_provider_tool_call(
        call,
        task_id=task_id,
        session_id=session_id,
        round_index=round_index,
        ordinal=ordinal,
        registry=registry,
        budget=budget,
        prior_usage=prior_usage,
        user_confirmed=user_confirmed,
        allow_mutations=allow_mutations,
        mutation_entry=True,
        read_scope=None,
        write_scope=write_scope,
        project_path=project_path,
        validation_command=validation_command,
        validation_cwd=validation_cwd,
        validation_commands_used=validation_commands_used,
    )


def _admit_provider_tool_call(
    call: LLMToolCall,
    *,
    task_id: str,
    session_id: str,
    round_index: int,
    ordinal: int,
    registry: Any,
    budget: RuntimeBudgetMetadata,
    prior_usage: ProviderToolBudgetUsage,
    user_confirmed: bool,
    allow_mutations: bool,
    mutation_entry: bool,
    read_scope: Sequence[str] | None,
    write_scope: Sequence[str] | None,
    project_path: str | None,
    validation_command: str | None,
    validation_cwd: str | None,
    validation_commands_used: int,
) -> ProviderToolAdmission:
    """Compose the project-owned boundaries for one provider tool call."""

    if not str(task_id).strip() or not str(session_id).strip():
        raise ProviderToolAdmissionError("task_id and session_id are required")
    if round_index < 1 or ordinal < 1:
        raise ProviderToolAdmissionError("round_index and ordinal must be positive")
    if type(user_confirmed) is not bool:
        raise ProviderToolAdmissionError("user_confirmed must be a literal boolean")
    if type(allow_mutations) is not bool or type(mutation_entry) is not bool:
        raise ProviderToolAdmissionError(
            "mutation admission controls must be literal booleans"
        )
    if validation_commands_used < 0:
        raise ProviderToolAdmissionError(
            "validation_commands_used must be non-negative"
        )

    provider_call_id = call.id
    project_call_id = f"{task_id}:r{round_index}:c{ordinal}"
    tool_name = call.function.name
    arguments, argument_error = decode_provider_tool_arguments(call)
    input_metadata = ToolInputMetadata.from_mapping(tool_name, arguments)
    tool_call = ToolCallMetadata(
        session_id=session_id,
        task_id=task_id,
        step_id=f"step_{round_index}_{ordinal}",
        call_id=project_call_id,
        provider_call_id=provider_call_id,
        tool_name=tool_name,
        input_metadata=input_metadata,
        reason="provider-native tool call",
        round_index=round_index,
    )

    if argument_error:
        return _blocked_provider_admission(
            tool_call,
            error_type="InvalidToolArguments",
            error_message=argument_error,
            recoverable=True,
            suggested_recovery=(
                "Return one JSON object containing the tool arguments."
            ),
        )

    definition = getattr(registry, "get", lambda _name: None)(tool_name)
    executor = getattr(registry, "get_executor", lambda _name: None)(tool_name)
    if definition is None or executor is None:
        return _blocked_provider_admission(
            tool_call,
            error_type="UnknownTool",
            error_message=f"Unknown or unexecutable tool: {tool_name}",
            recoverable=True,
            suggested_recovery=(
                "Choose a registered tool with an executable contract."
            ),
        )

    _apply_provider_input_defaults(definition, input_metadata)
    contract_error = provider_tool_contract_error(definition, input_metadata)
    if contract_error:
        return _blocked_provider_admission(
            tool_call,
            error_type="MissingRequiredInput",
            error_message=contract_error,
            recoverable=True,
            suggested_recovery="Retry with all required typed tool arguments.",
        )

    usage = provider_tool_resource_usage(
        definition,
        input_metadata,
        validation_command=validation_command,
    )
    budget_decision = provider_tool_budget_decision(
        budget,
        usage,
        prior=prior_usage,
    )
    if budget_decision.status == "blocked":
        return _blocked_provider_admission(
            tool_call,
            error_type="ToolBudgetExhausted",
            error_message=(
                "Provider tool call exceeds the remaining runtime budget: "
                f"{budget_decision.reason_code}."
            ),
            recoverable=False,
            suggested_recovery="Replan within the remaining runtime budget.",
        )

    permission = provider_tool_permission_decision(
        tool_name,
        definition,
        user_confirmed=user_confirmed,
        allow_mutations=allow_mutations,
    )
    if permission.status == "blocked":
        confirmation_required = permission.reason_code == "confirmation_required"
        return _blocked_provider_admission(
            tool_call,
            error_type=(
                "UserConfirmationRequired"
                if confirmation_required
                else "PermissionDenied"
            ),
            error_message=(
                f"Provider tool permission blocked {tool_name}: "
                f"{permission.reason_code}."
            ),
            recoverable=confirmation_required,
            retry_recommended=False if confirmation_required else None,
            suggested_recovery=(
                "Obtain explicit user confirmation before retrying this call."
                if confirmation_required
                else "Use a permitted non-mutating tool."
            ),
            requires_confirmation=permission.requires_confirmation,
        )

    if mutation_entry and tool_name != "file_patch_writer":
        return _blocked_provider_admission(
            tool_call,
            error_type="PermissionDenied",
            error_message=(
                "Provider mutation admission is patch-only; "
                "file_patch_writer is required."
            ),
            recoverable=False,
            suggested_recovery="Use the registered patch writer.",
            requires_confirmation=True,
        )

    capabilities = {
        str(getattr(capability, "value", capability))
        for capability in (getattr(definition, "capabilities", []) or [])
    }
    if ToolCapability.FILE_READ.value in capabilities:
        scope_error = provider_read_scope_error(
            input_metadata,
            read_scope or (),
            project_path,
        )
        if scope_error:
            return _blocked_provider_admission(
                tool_call,
                error_type="ProviderToolScopeViolation",
                error_message=scope_error,
                recoverable=True,
                suggested_recovery=(
                    "Use a path from the explicit read_files scope."
                ),
            )

    if permission.mutating:
        scope_error = provider_write_scope_error(
            input_metadata,
            write_scope or (),
            project_path,
        )
        if scope_error:
            return _blocked_provider_admission(
                tool_call,
                error_type="ProviderToolWriteScopeViolation",
                error_message=scope_error,
                recoverable=False,
                suggested_recovery=(
                    "Use a path from the explicit write_files scope."
                ),
                requires_confirmation=True,
            )

    if mutation_entry:
        validation_definition = getattr(
            registry,
            "get",
            lambda _name: None,
        )("command_executor")
        validation_executor = getattr(
            registry,
            "get_executor",
            lambda _name: None,
        )("command_executor")
        if (
            not str(validation_command or "").strip()
            or validation_definition is None
            or validation_executor is None
        ):
            return _blocked_provider_admission(
                tool_call,
                error_type="ProviderToolValidationViolation",
                error_message=(
                    "Provider mutation requires a non-empty task-owned "
                    "validation command and registered command_executor."
                ),
                recoverable=False,
                suggested_recovery=(
                    "Attach the exact validation command and its executor."
                ),
            )

    if tool_name == "command_executor":
        if not str(validation_command or "").strip():
            return _blocked_provider_admission(
                tool_call,
                error_type="ProviderToolValidationViolation",
                error_message=(
                    "command_executor requires a task-owned validation command."
                ),
                recoverable=False,
                suggested_recovery=(
                    "Use the exact typed validation command for this task."
                ),
            )
        validation = provider_validation_command_decision(
            input_metadata,
            validation_command=str(validation_command),
            validation_cwd=validation_cwd,
            validation_commands_used=validation_commands_used,
        )
        if validation.status == "blocked":
            duplicate = validation.reason_code == "duplicate_validation"
            return _blocked_provider_admission(
                tool_call,
                error_type=(
                    "ProviderToolValidationDuplicate"
                    if duplicate
                    else "ProviderToolValidationViolation"
                ),
                error_message=(
                    "Provider validation command blocked: "
                    f"{validation.reason_code}."
                ),
                recoverable=False,
                suggested_recovery=(
                    "Run the exact typed validation command once."
                ),
            )
        input_metadata.mode = validation.effective_mode
        input_metadata.cwd = validation.effective_cwd

    selection = ToolSelection(
        step_id=tool_call.step_id,
        tool_name=tool_name,
        reason=SelectionReason.CAPABILITY_MATCH,
        confidence=1.0,
        input_metadata=input_metadata,
        requires_confirmation=False,
    )
    return ProviderToolAdmission(
        status="admitted",
        provider_call_id=provider_call_id,
        project_call_id=project_call_id,
        tool_call=tool_call,
        selection=selection,
        requires_confirmation=permission.requires_confirmation,
    )


def _apply_provider_input_defaults(
    definition: Any,
    input_metadata: ToolInputMetadata,
) -> None:
    contract = getattr(definition, "contract_metadata", None)
    defaults = getattr(contract, "input_defaults", {}) or {}
    for raw_name, default in defaults.items():
        field_name = str(raw_name)
        current = getattr(
            input_metadata,
            field_name,
            input_metadata.attributes.get(field_name),
        )
        if current not in (None, "", [], {}):
            continue
        if field_name in ToolInputMetadata.model_fields:
            setattr(input_metadata, field_name, deepcopy(default))
        else:
            input_metadata.attributes[field_name] = deepcopy(default)


def _blocked_provider_admission(
    tool_call: ToolCallMetadata,
    *,
    error_type: str,
    error_message: str,
    recoverable: bool,
    retry_recommended: bool | None = None,
    suggested_recovery: str = "",
    requires_confirmation: bool = False,
) -> ProviderToolAdmission:
    return ProviderToolAdmission(
        status="blocked",
        provider_call_id=str(tool_call.provider_call_id),
        project_call_id=tool_call.call_id,
        tool_call=tool_call,
        tool_error=provider_tool_error(
            tool_call,
            error_type=error_type,
            error_message=error_message,
            recoverable=recoverable,
            retry_recommended=retry_recommended,
            suggested_recovery=suggested_recovery,
        ),
        requires_confirmation=requires_confirmation,
    )


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


def provider_tool_resource_usage(
    definition: Any,
    input_metadata: ToolInputMetadata,
    *,
    validation_command: str | None = None,
) -> ProviderToolResourceUsage:
    """Classify one call into the runtime budget dimensions it consumes."""

    capabilities = {
        str(getattr(capability, "value", capability))
        for capability in (getattr(definition, "capabilities", []) or [])
    }
    reads = int(ToolCapability.FILE_READ.value in capabilities)
    writes = bool(
        {
            ToolCapability.FILE_WRITE.value,
            ToolCapability.FILE_DELETE.value,
        }
        & capabilities
    )
    operation = str(input_metadata.operation_kind or "").lower()
    creates = int(
        writes
        and operation in {"create_file", "file_create", "directory_generate"}
    )
    edits = int(writes and not creates)
    validation = int(
        bool(
            validation_command
            and input_metadata.tool_name == "command_executor"
        )
    )
    return ProviderToolResourceUsage(
        reads=reads,
        edits=edits,
        creates=creates,
        validation=validation,
    )


def provider_tool_budget_decision(
    budget: RuntimeBudgetMetadata,
    usage: ProviderToolResourceUsage,
    *,
    prior: ProviderToolBudgetUsage | None = None,
) -> ProviderToolBudgetDecision:
    """Admit only when runtime, batch-prior, and requested usage fit."""

    prior = prior or ProviderToolBudgetUsage()
    checks = [
        (
            budget.tool_calls_used + prior.calls + usage.calls,
            budget.max_tool_calls,
            "tool_calls_exhausted",
        ),
        (
            budget.file_reads_used + prior.reads + usage.reads,
            budget.max_file_reads,
            "file_reads_exhausted",
        ),
        (
            budget.file_edits_used + prior.edits + usage.edits,
            budget.max_file_edits,
            "file_edits_exhausted",
        ),
        (
            budget.file_creates_used + prior.creates + usage.creates,
            budget.max_file_creates,
            "file_creates_exhausted",
        ),
        (
            budget.verification_attempts_used + prior.validation + usage.validation,
            budget.max_verification_attempts,
            "validation_exhausted",
        ),
    ]
    for projected, maximum, reason_code in checks:
        if projected > maximum:
            return ProviderToolBudgetDecision(
                status="blocked",
                reason_code=reason_code,
                usage=usage,
            )
    return ProviderToolBudgetDecision(
        status="admitted",
        reason_code="within_budget",
        usage=usage,
    )


def provider_validation_command_decision(
    input_metadata: ToolInputMetadata,
    *,
    validation_command: str,
    validation_cwd: str | None,
    validation_commands_used: int,
) -> ProviderValidationCommandDecision:
    """Admit one exact typed validation command without shell widening."""

    if validation_commands_used < 0:
        raise ValueError("validation_commands_used must be non-negative")
    if validation_commands_used > 0:
        return _blocked_validation_decision("duplicate_validation")
    if not validation_commands_match(
        validation_command,
        str(input_metadata.command or ""),
    ):
        return _blocked_validation_decision("command_mismatch")

    mode = str(input_metadata.mode or "automatic").strip().lower()
    if mode not in {"automatic", "execute", "run", "exec"}:
        return _blocked_validation_decision("invalid_mode")

    requested_cwd = str(input_metadata.cwd or "").strip()
    if requested_cwd and not validation_cwd:
        return _blocked_validation_decision("unexpected_cwd")
    effective_cwd = (
        _canonical_path(validation_cwd, None) if validation_cwd else None
    )
    if requested_cwd and effective_cwd:
        if _canonical_path(requested_cwd, None) != effective_cwd:
            return _blocked_validation_decision("cwd_mismatch")
    return ProviderValidationCommandDecision(
        status="admitted",
        reason_code="exact_match",
        effective_mode=mode,
        effective_cwd=effective_cwd,
    )


def provider_tool_permission_decision(
    tool_name: str,
    definition: Any,
    *,
    user_confirmed: bool,
    allow_mutations: bool,
) -> ProviderToolPermissionDecision:
    """Fail closed unless typed permission, opt-in, and confirmation allow the call."""

    if type(user_confirmed) is not bool or type(allow_mutations) is not bool:
        raise ValueError("permission inputs must be literal booleans")
    permission = str(
        getattr(getattr(definition, "permission_level", ""), "value", None)
        or getattr(definition, "permission_level", "")
    ).lower()
    known_permissions = {level.value for level in PermissionLevel}
    capabilities = {
        str(getattr(capability, "value", capability))
        for capability in (getattr(definition, "capabilities", []) or [])
    }
    mutating = bool(
        {
            ToolCapability.FILE_WRITE.value,
            ToolCapability.FILE_DELETE.value,
        }
        & capabilities
    ) or tool_name in FILE_MUTATION_TOOLS
    requires_confirmation = permission in {
        PermissionLevel.MEDIUM.value,
        PermissionLevel.HIGH.value,
    } or mutating

    if permission not in known_permissions:
        return ProviderToolPermissionDecision(
            status="blocked",
            reason_code="unknown_permission",
            requires_confirmation=requires_confirmation,
            mutating=mutating,
        )
    if permission == PermissionLevel.FORBIDDEN.value:
        return ProviderToolPermissionDecision(
            status="blocked",
            reason_code="forbidden",
            requires_confirmation=requires_confirmation,
            mutating=mutating,
        )
    if mutating and not allow_mutations:
        return ProviderToolPermissionDecision(
            status="blocked",
            reason_code="mutation_not_allowed",
            requires_confirmation=True,
            mutating=True,
        )
    if requires_confirmation and not user_confirmed:
        return ProviderToolPermissionDecision(
            status="blocked",
            reason_code="confirmation_required",
            requires_confirmation=True,
            mutating=mutating,
        )
    return ProviderToolPermissionDecision(
        status="admitted",
        reason_code="allowed",
        requires_confirmation=requires_confirmation,
        mutating=mutating,
    )


def _blocked_validation_decision(
    reason_code: Literal[
        "duplicate_validation",
        "command_mismatch",
        "invalid_mode",
        "cwd_mismatch",
        "unexpected_cwd",
    ],
) -> ProviderValidationCommandDecision:
    return ProviderValidationCommandDecision(
        status="blocked",
        reason_code=reason_code,
    )


def provider_read_scope_error(
    input_metadata: ToolInputMetadata,
    read_scope: Sequence[str],
    project_path: str | None,
) -> str | None:
    """Require read requests to match an explicit project-contained path set."""

    raw_allowed = [str(path) for path in read_scope if str(path or "").strip()]
    if not raw_allowed:
        return "Provider reads require a non-empty explicit read_files scope."
    if len(raw_allowed) > MAX_PROVIDER_SCOPE_PATHS:
        return f"Provider read scope may contain at most {MAX_PROVIDER_SCOPE_PATHS} paths."
    boundary_error = _scope_path_boundary_error(
        raw_allowed, project_path, "read scope"
    )
    if boundary_error:
        return boundary_error

    requested_raw = _requested_paths(input_metadata, include_project_path=False)
    if not requested_raw:
        return "Provider read did not provide an explicit target path."
    if len(requested_raw) > MAX_PROVIDER_SCOPE_PATHS:
        return f"Provider read request may contain at most {MAX_PROVIDER_SCOPE_PATHS} paths."
    boundary_error = _scope_path_boundary_error(
        requested_raw, project_path, "read request"
    )
    if boundary_error:
        return boundary_error

    allowed = {_canonical_path(path, project_path) for path in raw_allowed}
    requested = [_canonical_path(path, project_path) for path in requested_raw]
    outside = [path for path in requested if path not in allowed]
    if outside:
        return (
            "Provider read scope permits only explicit files; outside path(s): "
            + ", ".join(outside[:4])
        )
    return None


def provider_write_scope_error(
    input_metadata: ToolInputMetadata,
    write_scope: Sequence[str],
    project_path: str | None,
) -> str | None:
    """Require mutation requests to match an explicit project-contained path set."""

    raw_allowed = [str(path) for path in write_scope if str(path or "").strip()]
    if not raw_allowed:
        return "Provider mutation requires a non-empty explicit write_files scope."
    if len(raw_allowed) > MAX_PROVIDER_SCOPE_PATHS:
        return f"Provider write scope may contain at most {MAX_PROVIDER_SCOPE_PATHS} paths."
    boundary_error = _scope_path_boundary_error(
        raw_allowed, project_path, "write scope"
    )
    if boundary_error:
        return boundary_error

    requested_raw = _requested_paths(input_metadata, include_project_path=True)
    if not requested_raw:
        return "Provider mutation did not provide an explicit target path."
    if len(requested_raw) > MAX_PROVIDER_SCOPE_PATHS:
        return f"Provider write request may contain at most {MAX_PROVIDER_SCOPE_PATHS} paths."
    boundary_error = _scope_path_boundary_error(
        requested_raw, project_path, "write request"
    )
    if boundary_error:
        return boundary_error

    allowed = {_canonical_path(path, project_path) for path in raw_allowed}
    requested = [_canonical_path(path, project_path) for path in requested_raw]
    outside = [path for path in requested if path not in allowed]
    if outside:
        return (
            "Provider write scope permits only explicit files; outside path(s): "
            + ", ".join(outside[:4])
        )
    return None


def _requested_paths(
    input_metadata: ToolInputMetadata,
    *,
    include_project_path: bool,
) -> list[str]:
    params = input_metadata.to_params()
    fields = ["file_path", "directory_path"]
    if include_project_path:
        fields.append("project_path")
    requested = [str(params[field]) for field in fields if params.get(field)]
    requested.extend(str(path) for path in (params.get("file_paths") or []) if path)
    return requested


def _canonical_path(raw_path: Any, project_path: str | None) -> str:
    path = Path(str(raw_path or "")).expanduser()
    if not path.is_absolute() and project_path:
        path = Path(project_path).expanduser() / path
    return str(path.resolve(strict=False))


def _scope_path_boundary_error(
    raw_paths: Sequence[str],
    project_path: str | None,
    label: str,
) -> str | None:
    if not project_path:
        return None
    root = Path(project_path).expanduser().resolve(strict=False)
    for raw in raw_paths:
        candidate = Path(str(raw)).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_symlink():
            return f"Provider {label} rejects symlink paths: {candidate}"
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(root):
            return (
                f"Provider {label} rejects paths outside the project root: {resolved}"
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
    "MAX_PROVIDER_SCOPE_PATHS",
    "MAX_PROVIDER_TOOL_ARGUMENT_CHARS",
    "ProviderToolAdmission",
    "ProviderToolAdmissionError",
    "ProviderToolBudgetDecision",
    "ProviderToolBudgetUsage",
    "ProviderToolPermissionDecision",
    "ProviderToolResourceUsage",
    "ProviderValidationCommandDecision",
    "admit_provider_mutation_tool_call",
    "admit_provider_tool_call",
    "decode_provider_tool_arguments",
    "provider_tool_budget_decision",
    "provider_tool_contract_error",
    "provider_tool_error",
    "provider_tool_permission_decision",
    "provider_tool_resource_usage",
    "provider_validation_command_decision",
    "provider_read_scope_error",
    "provider_write_scope_error",
]
