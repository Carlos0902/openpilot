from __future__ import annotations

import json

import pytest

from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_code_artifact_ledger import ProviderCodeArtifactLedger
from core.provider_patch_artifact_binding import (
    ProviderPatchArtifactBindingError,
    bind_provider_patch_artifact,
)
from core.provider_tool_admission import (
    MAX_PROVIDER_SCOPE_PATHS,
    ProviderToolBudgetUsage,
    admit_provider_mutation_tool_call,
)
from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
)
from metadata import CodeArtifactMetadata, RuntimeBudgetMetadata, ToolContractMetadata
from tools.file_patch_writer import FILE_PATCH_WRITER_DEFINITION


class _Executor:
    def __call__(self, _input) -> None:
        raise AssertionError("binding must not execute tools")


class _Registry:
    def __init__(self) -> None:
        validation = ToolDefinition(
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
            "command_executor": validation,
        }
        self.executors = {name: _Executor() for name in self.definitions}

    def get(self, name: str):
        return self.definitions.get(name)

    def get_executor(self, name: str):
        return self.executors.get(name)


def _admission(
    arguments: dict,
    *,
    user_confirmed: bool = True,
):
    call = LLMToolCall(
        id="provider-writer",
        function=LLMToolFunctionCall(
            name="file_patch_writer",
            arguments=json.dumps(arguments),
        ),
    )
    return admit_provider_mutation_tool_call(
        call,
        task_id="task",
        session_id="session",
        round_index=1,
        ordinal=1,
        registry=_Registry(),
        budget=RuntimeBudgetMetadata(),
        prior_usage=ProviderToolBudgetUsage(),
        user_confirmed=user_confirmed,
        allow_mutations=True,
        write_scope=["app.py"],
        project_path=None,
        validation_command="python -m compileall -q app.py",
    )


def _registered_reference(
    ledger: ProviderCodeArtifactLedger,
    code: str = "def generated():\n    return True\n",
):
    return ledger.register(
        CodeArtifactMetadata(code=code, language="python"),
        source_id="project-generator",
        provider_call_id="provider-generator",
    )


def test_binding_resolves_verified_code_into_both_admission_views() -> None:
    code = "def generated():\n    return True\n"
    ledger = ProviderCodeArtifactLedger()
    reference = _registered_reference(ledger, code)
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "artifact_ref": reference.model_dump(mode="json"),
        }
    )

    bound = bind_provider_patch_artifact(
        admission,
        code_artifact_ledger=ledger,
    )

    assert bound.status == "admitted"
    assert bound.tool_call.input_metadata.generated_unit == code
    assert bound.selection is not None
    assert bound.selection.input_metadata.generated_unit == code
    assert bound.tool_call.input_metadata.artifact_ref == reference
    assert admission.tool_call.input_metadata.generated_unit is None


def test_verified_artifact_replaces_provider_supplied_inline_body() -> None:
    verified = "print('verified')"
    ledger = ProviderCodeArtifactLedger()
    reference = _registered_reference(ledger, verified)
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "artifact_ref": reference.model_dump(mode="json"),
            "generated_unit": "print('provider supplied')",
        }
    )

    bound = bind_provider_patch_artifact(
        admission,
        code_artifact_ledger=ledger,
    )

    assert bound.tool_call.input_metadata.generated_unit == verified
    assert "provider supplied" not in bound.tool_call.input_metadata.generated_unit


def test_binding_carries_exact_post_processing_write_scope() -> None:
    ledger = ProviderCodeArtifactLedger()
    reference = _registered_reference(ledger)
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "artifact_ref": reference.model_dump(mode="json"),
        }
    )
    scope = [f"derived-{index}.json" for index in range(MAX_PROVIDER_SCOPE_PATHS)]

    bound = bind_provider_patch_artifact(
        admission,
        code_artifact_ledger=ledger,
        authorized_post_processing_write_scope=scope,
    )

    expected = tuple(scope)
    assert (
        bound.tool_call.input_metadata.runtime_handles[
            "_post_processing_write_scope"
        ]
        == expected
    )
    assert bound.selection is not None
    assert (
        bound.selection.input_metadata.runtime_handles[
            "_post_processing_write_scope"
        ]
        == expected
    )


def test_inline_writer_without_reference_is_unchanged() -> None:
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "generated_unit": "print('inline')",
        }
    )

    bound = bind_provider_patch_artifact(admission)

    assert bound is admission


def test_blocked_admission_does_not_resolve_or_validate_binding_inputs() -> None:
    ledger = ProviderCodeArtifactLedger()
    reference = _registered_reference(ledger)
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "artifact_ref": reference.model_dump(mode="json"),
        },
        user_confirmed=False,
    )

    bound = bind_provider_patch_artifact(
        admission,
        code_artifact_ledger=object(),
        authorized_post_processing_write_scope=(item for item in ()),
    )

    assert bound is admission
    assert bound.status == "blocked"


def test_admitted_reference_requires_ledger() -> None:
    ledger = ProviderCodeArtifactLedger()
    reference = _registered_reference(ledger)
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "artifact_ref": reference.model_dump(mode="json"),
        }
    )

    with pytest.raises(ProviderPatchArtifactBindingError):
        bind_provider_patch_artifact(admission)


def test_unknown_reference_fails_without_mutating_admission() -> None:
    source_ledger = ProviderCodeArtifactLedger()
    reference = _registered_reference(source_ledger)
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "artifact_ref": reference.model_dump(mode="json"),
        }
    )

    with pytest.raises(ProviderPatchArtifactBindingError):
        bind_provider_patch_artifact(
            admission,
            code_artifact_ledger=ProviderCodeArtifactLedger(),
        )

    assert admission.tool_call.input_metadata.generated_unit is None


@pytest.mark.parametrize(
    "scope",
    [
        (item for item in ()),
        [""],
        ["same", "same"],
        [f"path-{index}" for index in range(MAX_PROVIDER_SCOPE_PATHS + 1)],
    ],
)
def test_admitted_binding_rejects_invalid_post_processing_scope(scope) -> None:
    ledger = ProviderCodeArtifactLedger()
    reference = _registered_reference(ledger)
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "artifact_ref": reference.model_dump(mode="json"),
        }
    )

    with pytest.raises(ProviderPatchArtifactBindingError):
        bind_provider_patch_artifact(
            admission,
            code_artifact_ledger=ledger,
            authorized_post_processing_write_scope=scope,
        )


def test_binding_rejects_invalid_admission_or_ledger_types() -> None:
    with pytest.raises(ProviderPatchArtifactBindingError):
        bind_provider_patch_artifact(object())

    ledger = ProviderCodeArtifactLedger()
    reference = _registered_reference(ledger)
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "artifact_ref": reference.model_dump(mode="json"),
        }
    )
    with pytest.raises(ProviderPatchArtifactBindingError):
        bind_provider_patch_artifact(
            admission,
            code_artifact_ledger=object(),
        )
