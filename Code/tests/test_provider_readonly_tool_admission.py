from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_tool_admission import (
    ProviderToolAdmissionError,
    ProviderToolBudgetUsage,
    admit_provider_tool_call,
)
from core.tool_contracts import PermissionLevel, ToolCapability, ToolDefinition
from metadata import RuntimeBudgetMetadata, ToolContractMetadata


class _Registry:
    def __init__(self, definitions, executors=None):
        self.definitions = definitions
        self.executors = (
            {name: object() for name in definitions}
            if executors is None
            else executors
        )

    def get(self, name):
        return self.definitions.get(name)

    def get_executor(self, name):
        return self.executors.get(name)


def _definition(
    name="file_reader",
    *,
    permission=PermissionLevel.LOW,
    capabilities=(ToolCapability.FILE_READ,),
    required=("file_path",),
    defaults=None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        display_name=name,
        description=f"Execute {name}",
        permission_level=permission,
        capabilities=list(capabilities),
        contract_metadata=ToolContractMetadata(
            tool_name=name,
            input_metadata_type="ToolInputMetadata",
            output_metadata_type="ToolResultMetadata",
            required_input_fields=list(required),
            input_defaults=defaults or {},
        ),
    )


def _call(name="file_reader", arguments=None, call_id="provider-call-1"):
    return LLMToolCall(
        id=call_id,
        function=LLMToolFunctionCall(
            name=name,
            arguments=json.dumps(arguments or {}),
        ),
    )


def _admit(call, registry, **updates):
    values = {
        "task_id": "task",
        "session_id": "session",
        "round_index": 1,
        "ordinal": 1,
        "registry": registry,
        "budget": RuntimeBudgetMetadata(),
        "prior_usage": ProviderToolBudgetUsage(),
        "user_confirmed": False,
        "read_scope": ["README.md"],
        "project_path": None,
    }
    values.update(updates)
    return admit_provider_tool_call(call, **values)


def test_readonly_call_is_admitted_after_all_boundaries() -> None:
    definition = _definition(defaults={"read_mode": "full"})
    admission = _admit(
        _call(arguments={"file_path": "README.md"}),
        _Registry({"file_reader": definition}),
    )

    assert admission.status == "admitted"
    assert admission.provider_call_id == "provider-call-1"
    assert admission.project_call_id == "task:r1:c1"
    assert admission.selection is not None
    assert admission.selection.input_metadata.read_mode == "full"
    assert admission.requires_confirmation is False


@pytest.mark.parametrize(
    ("call", "registry", "error_type"),
    [
        (
            LLMToolCall(
                id="provider-call-1",
                function=LLMToolFunctionCall(
                    name="file_reader",
                    arguments="[]",
                ),
            ),
            _Registry({"file_reader": _definition()}),
            "InvalidToolArguments",
        ),
        (_call("missing"), _Registry({}), "UnknownTool"),
        (
            _call(arguments={}),
            _Registry({"file_reader": _definition()}),
            "MissingRequiredInput",
        ),
    ],
)
def test_protocol_and_contract_failures_are_typed(call, registry, error_type) -> None:
    admission = _admit(call, registry)

    assert admission.status == "blocked"
    assert admission.tool_error is not None
    assert admission.tool_error.error_type == error_type
    assert admission.tool_error.provider_call_id == call.id


def test_registry_definition_without_executor_is_blocked() -> None:
    definition = _definition()
    admission = _admit(
        _call(arguments={"file_path": "README.md"}),
        _Registry({"file_reader": definition}, executors={}),
    )

    assert admission.tool_error is not None
    assert admission.tool_error.error_type == "UnknownTool"


def test_scope_budget_and_confirmation_fail_before_selection() -> None:
    definition = _definition(permission=PermissionLevel.MEDIUM)
    registry = _Registry({"file_reader": definition})
    call = _call(arguments={"file_path": "README.md"})

    unconfirmed = _admit(call, registry)
    out_of_scope = _admit(
        call,
        registry,
        user_confirmed=True,
        read_scope=["other.md"],
    )
    exhausted = _admit(
        call,
        registry,
        user_confirmed=True,
        budget=RuntimeBudgetMetadata(max_tool_calls=0),
    )

    assert unconfirmed.tool_error.error_type == "UserConfirmationRequired"
    assert out_of_scope.tool_error.error_type == "ProviderToolScopeViolation"
    assert exhausted.tool_error.error_type == "ToolBudgetExhausted"
    assert all(item.selection is None for item in [unconfirmed, out_of_scope, exhausted])


def test_mutation_is_blocked_in_readonly_admission() -> None:
    definition = _definition(
        "file_patch_writer",
        permission=PermissionLevel.LOW,
        capabilities=(ToolCapability.FILE_WRITE,),
    )
    admission = _admit(
        _call("file_patch_writer", {"file_path": "app.py"}),
        _Registry({"file_patch_writer": definition}),
        user_confirmed=True,
        read_scope=[],
    )

    assert admission.tool_error is not None
    assert admission.tool_error.error_type == "PermissionDenied"
    assert "mutation_not_allowed" in admission.tool_error.error_message


def test_exact_validation_command_binds_mode_and_cwd(tmp_path) -> None:
    definition = _definition(
        "command_executor",
        permission=PermissionLevel.MEDIUM,
        capabilities=(ToolCapability.SHELL_EXECUTION,),
        required=("command",),
    )
    registry = _Registry({"command_executor": definition})
    command = "python -m compileall -q src"

    admission = _admit(
        _call("command_executor", {"command": command}),
        registry,
        user_confirmed=True,
        read_scope=[],
        validation_command=command,
        validation_cwd=str(tmp_path),
    )

    assert admission.status == "admitted"
    assert admission.selection.input_metadata.mode == "automatic"
    assert admission.selection.input_metadata.cwd == str(tmp_path.resolve())


@pytest.mark.parametrize(
    ("updates", "error_type"),
    [
        ({"validation_command": "python -m compileall src"}, "ProviderToolValidationViolation"),
        (
            {
                "validation_command": "python -m compileall -q src",
                "validation_commands_used": 1,
            },
            "ProviderToolValidationDuplicate",
        ),
    ],
)
def test_validation_command_failures_are_typed(updates, error_type) -> None:
    definition = _definition(
        "command_executor",
        permission=PermissionLevel.MEDIUM,
        capabilities=(ToolCapability.SHELL_EXECUTION,),
        required=("command",),
    )
    admission = _admit(
        _call(
            "command_executor",
            {"command": "python -m compileall -q src"},
        ),
        _Registry({"command_executor": definition}),
        user_confirmed=True,
        read_scope=[],
        **updates,
    )

    assert admission.tool_error is not None
    assert admission.tool_error.error_type == error_type


def test_command_executor_requires_task_owned_validation_command() -> None:
    definition = _definition(
        "command_executor",
        permission=PermissionLevel.MEDIUM,
        capabilities=(ToolCapability.SHELL_EXECUTION,),
        required=("command",),
    )
    admission = _admit(
        _call("command_executor", {"command": "pwd"}),
        _Registry({"command_executor": definition}),
        user_confirmed=True,
        read_scope=[],
    )

    assert admission.tool_error is not None
    assert admission.tool_error.error_type == "ProviderToolValidationViolation"


@pytest.mark.parametrize(
    "updates",
    [
        {"task_id": ""},
        {"session_id": ""},
        {"round_index": 0},
        {"ordinal": 0},
        {"user_confirmed": 1},
    ],
)
def test_single_call_entry_rejects_invalid_identity_or_control_input(updates) -> None:
    with pytest.raises(ProviderToolAdmissionError):
        _admit(
            _call(arguments={"file_path": "README.md"}),
            _Registry({"file_reader": _definition()}),
            **updates,
        )
