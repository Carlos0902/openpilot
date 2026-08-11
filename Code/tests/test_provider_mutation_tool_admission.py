from __future__ import annotations

import json

import pytest

from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_tool_admission import (
    ProviderToolAdmissionError,
    ProviderToolBudgetUsage,
    admit_provider_mutation_tool_call,
)
from core.tool_contracts import PermissionLevel, ToolCapability, ToolDefinition
from metadata import RuntimeBudgetMetadata, ToolContractMetadata


class _Registry:
    def __init__(self, definitions):
        self.definitions = definitions
        self.executors = {name: _Executor() for name in definitions}

    def get(self, name):
        return self.definitions.get(name)

    def get_executor(self, name):
        return self.executors.get(name)


class _Executor:
    def __init__(self):
        self.called = False

    def __call__(self, _input):
        self.called = True
        raise AssertionError("admission must not execute tools")


def _definition(
    name,
    *,
    capabilities,
    permission=PermissionLevel.MEDIUM,
    required=(),
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
        ),
    )


def _registry(*, include_validation=True, writer="file_patch_writer"):
    definitions = {
        writer: _definition(
            writer,
            capabilities=(ToolCapability.FILE_WRITE,),
            required=("file_path",),
        )
    }
    if include_validation:
        definitions["command_executor"] = _definition(
            "command_executor",
            capabilities=(ToolCapability.SHELL_EXECUTION,),
            required=("command",),
        )
    return _Registry(definitions)


def _call(name="file_patch_writer", path="app.py"):
    return LLMToolCall(
        id="provider-mutation-1",
        function=LLMToolFunctionCall(
            name=name,
            arguments=json.dumps({"file_path": path}),
        ),
    )


def _admit(call=None, registry=None, **updates):
    values = {
        "task_id": "task",
        "session_id": "session",
        "round_index": 1,
        "ordinal": 1,
        "registry": registry or _registry(),
        "budget": RuntimeBudgetMetadata(),
        "prior_usage": ProviderToolBudgetUsage(),
        "user_confirmed": True,
        "allow_mutations": True,
        "write_scope": ["app.py"],
        "project_path": None,
        "validation_command": "python -m compileall -q app.py",
    }
    values.update(updates)
    return admit_provider_mutation_tool_call(call or _call(), **values)


def test_patch_mutation_is_admitted_without_execution() -> None:
    registry = _registry()
    admission = _admit(registry=registry)

    assert admission.status == "admitted"
    assert admission.selection is not None
    assert admission.selection.tool_name == "file_patch_writer"
    assert admission.requires_confirmation is True
    assert registry.get_executor("file_patch_writer").called is False
    assert registry.get_executor("command_executor").called is False


@pytest.mark.parametrize(
    ("updates", "error_type"),
    [
        ({"allow_mutations": False}, "PermissionDenied"),
        ({"user_confirmed": False}, "UserConfirmationRequired"),
        ({"write_scope": []}, "ProviderToolWriteScopeViolation"),
        ({"write_scope": ["other.py"]}, "ProviderToolWriteScopeViolation"),
        ({"validation_command": None}, "ProviderToolValidationViolation"),
        (
            {"registry": _registry(include_validation=False)},
            "ProviderToolValidationViolation",
        ),
        (
            {"budget": RuntimeBudgetMetadata(max_file_edits=0)},
            "ToolBudgetExhausted",
        ),
    ],
)
def test_mutation_authority_failures_block_before_selection(
    updates,
    error_type,
) -> None:
    admission = _admit(**updates)

    assert admission.status == "blocked"
    assert admission.selection is None
    assert admission.tool_error is not None
    assert admission.tool_error.error_type == error_type


def test_non_patch_mutation_tool_is_not_admitted() -> None:
    admission = _admit(
        call=_call("file_writer"),
        registry=_registry(writer="file_writer"),
    )

    assert admission.tool_error is not None
    assert admission.tool_error.error_type == "PermissionDenied"
    assert "patch-only" in admission.tool_error.error_message


@pytest.mark.parametrize("allow_mutations", [1, "true", None])
def test_mutation_opt_in_requires_literal_boolean(allow_mutations) -> None:
    with pytest.raises(ProviderToolAdmissionError):
        _admit(allow_mutations=allow_mutations)
