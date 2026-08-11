from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_tool_admission import (
    ProviderToolBudgetUsage,
    admit_provider_mutation_tool_call,
)
from core.provider_tool_definitions import build_provider_tool_definitions
from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
)
from metadata import (
    ProviderCodeArtifactReference,
    RuntimeBudgetMetadata,
    ToolContractMetadata,
)
from tools.file_patch_writer import FILE_PATCH_WRITER_DEFINITION


class _Executor:
    def __init__(self) -> None:
        self.called = False

    def __call__(self, _input) -> None:
        self.called = True
        raise AssertionError("admission must not execute tools")


class _Registry:
    def __init__(self) -> None:
        command_definition = ToolDefinition(
            name="command_executor",
            display_name="command_executor",
            description="Run exact validation",
            permission_level=PermissionLevel.HIGH,
            capabilities=[ToolCapability.SHELL_EXECUTION],
            contract_metadata=ToolContractMetadata(
                tool_name="command_executor",
                input_metadata_type="ToolInputMetadata",
                output_metadata_type="ToolResultMetadata",
                required_input_fields=["command"],
            ),
        )
        self.definitions = {
            "file_patch_writer": FILE_PATCH_WRITER_DEFINITION,
            "command_executor": command_definition,
        }
        self.executors = {
            name: _Executor() for name in self.definitions
        }

    def get(self, name: str):
        return self.definitions.get(name)

    def get_executor(self, name: str):
        return self.executors.get(name)


def _reference_payload(**updates):
    payload = {
        "kind": "code_artifact",
        "source_id": "project-generator",
        "provider_call_id": "provider-generator",
        "sha256": "a" * 64,
        "bytes": 12,
        "chars": 12,
        "language": "python",
    }
    payload.update(updates)
    return payload


def _call(arguments: dict) -> LLMToolCall:
    return LLMToolCall(
        id="provider-writer",
        function=LLMToolFunctionCall(
            name="file_patch_writer",
            arguments=json.dumps(arguments),
        ),
    )


def _admit(
    arguments: dict,
    *,
    registry: _Registry | None = None,
    **updates,
):
    values = {
        "task_id": "task",
        "session_id": "session",
        "round_index": 1,
        "ordinal": 1,
        "registry": registry or _Registry(),
        "budget": RuntimeBudgetMetadata(),
        "prior_usage": ProviderToolBudgetUsage(),
        "user_confirmed": True,
        "allow_mutations": True,
        "write_scope": ["app.py"],
        "project_path": None,
        "validation_command": "python -m compileall -q app.py",
    }
    values.update(updates)
    return admit_provider_mutation_tool_call(_call(arguments), **values)


def test_patch_writer_schema_exposes_artifact_reference_alternative() -> None:
    definition = build_provider_tool_definitions(
        _Registry(),
        ["file_patch_writer"],
    )[0]
    parameters = definition.function.parameters

    assert parameters["properties"]["artifact_ref"] == {"type": "object"}
    add_symbol = next(
        requirement
        for requirement in parameters["allOf"]
        if requirement["if"].get("properties", {}).get("operation_kind")
        == {"const": "add_symbol"}
    )
    assert add_symbol["then"]["anyOf"] == [
        {"required": ["generated_unit"]},
        {"required": ["artifact_ref"]},
    ]
    assert parameters["additionalProperties"] is False


def test_typed_artifact_reference_is_admitted_without_execution() -> None:
    registry = _Registry()

    admission = _admit(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "artifact_ref": _reference_payload(),
        },
        registry=registry,
    )

    assert admission.status == "admitted"
    assert admission.selection is not None
    assert isinstance(
        admission.tool_call.input_metadata.artifact_ref,
        ProviderCodeArtifactReference,
    )
    assert admission.tool_call.input_metadata.generated_unit is None
    assert registry.get_executor("file_patch_writer").called is False
    assert registry.get_executor("command_executor").called is False


def test_generated_unit_remains_a_valid_add_symbol_input() -> None:
    admission = _admit(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "generated_unit": "def generated():\n    return True\n",
        }
    )

    assert admission.status == "admitted"


def test_add_symbol_without_body_or_reference_is_blocked() -> None:
    admission = _admit(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
        }
    )

    assert admission.status == "blocked"
    assert admission.tool_error is not None
    assert admission.tool_error.error_type == "MissingRequiredInput"
    assert "artifact_ref" in admission.tool_error.error_message
    assert "generated_unit" in admission.tool_error.error_message


@pytest.mark.parametrize(
    "artifact_ref",
    [
        _reference_payload(kind="file_artifact"),
        _reference_payload(sha256="0" * 63),
        _reference_payload(extra="not-allowed"),
        "not-an-object",
    ],
)
def test_invalid_artifact_reference_fails_before_admission(artifact_ref) -> None:
    with pytest.raises(ValidationError):
        _admit(
            {
                "file_path": "app.py",
                "operation_kind": "add_symbol",
                "artifact_ref": artifact_ref,
            }
        )


def test_artifact_reference_does_not_replace_mutation_authority() -> None:
    admission = admit_provider_mutation_tool_call(
        _call(
            {
                "file_path": "app.py",
                "operation_kind": "add_symbol",
                "artifact_ref": _reference_payload(),
            }
        ),
        task_id="task",
        session_id="session",
        round_index=1,
        ordinal=1,
        registry=_Registry(),
        budget=RuntimeBudgetMetadata(),
        prior_usage=ProviderToolBudgetUsage(),
        user_confirmed=False,
        allow_mutations=True,
        write_scope=["app.py"],
        project_path=None,
        validation_command="python -m compileall -q app.py",
    )

    assert admission.status == "blocked"
    assert admission.tool_error is not None
    assert admission.tool_error.error_type == "UserConfirmationRequired"


@pytest.mark.parametrize(
    ("updates", "error_type"),
    [
        ({"allow_mutations": False}, "PermissionDenied"),
        ({"write_scope": ["other.py"]}, "ProviderToolWriteScopeViolation"),
        ({"validation_command": None}, "ProviderToolValidationViolation"),
    ],
)
def test_artifact_reference_cannot_replace_other_mutation_boundaries(
    updates: dict,
    error_type: str,
) -> None:
    admission = _admit(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "artifact_ref": _reference_payload(),
        },
        **updates,
    )

    assert admission.status == "blocked"
    assert admission.tool_error is not None
    assert admission.tool_error.error_type == error_type
