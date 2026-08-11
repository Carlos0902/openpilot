from __future__ import annotations

import json

import pytest

from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_code_artifact_ledger import ProviderCodeArtifactLedger
from core.provider_mutation_execution_batch import (
    ProviderMutationExecutionBatchError,
    prepare_provider_mutation_execution_batch,
)
from core.provider_tool_admission import (
    ProviderToolBudgetUsage,
    admit_provider_mutation_tool_call,
)
from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
)
from metadata import (
    CodeArtifactMetadata,
    RuntimeBudgetMetadata,
    ToolContractMetadata,
)
from tools.file_patch_writer import FILE_PATCH_WRITER_DEFINITION


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
        self.executors = {name: object() for name in self.definitions}

    def get(self, name: str):
        return self.definitions.get(name)

    def get_executor(self, name: str):
        return self.executors.get(name)


def _admission(
    arguments: dict,
    *,
    call_id: str = "provider-writer",
    ordinal: int = 1,
    confirmed: bool = True,
):
    call = LLMToolCall(
        id=call_id,
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
        ordinal=ordinal,
        registry=_Registry(),
        budget=RuntimeBudgetMetadata(),
        prior_usage=ProviderToolBudgetUsage(),
        user_confirmed=confirmed,
        allow_mutations=True,
        write_scope=["app.py"],
        project_path=None,
        validation_command="python -m compileall -q app.py",
    )


def _reference(ledger: ProviderCodeArtifactLedger, code: str):
    return ledger.register(
        CodeArtifactMetadata(code=code, language="python"),
        source_id="project-generator",
        provider_call_id="provider-generator",
    )


def _prepare(admissions, **kwargs):
    return prepare_provider_mutation_execution_batch(
        admissions,
        task_id="task",
        session_id="session",
        round_index=1,
        **kwargs,
    )


def test_preparation_binds_scope_for_inline_patch() -> None:
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "generated_unit": "def inline():\n    return True\n",
        }
    )
    scope = ["app.py.index.json", "sketch.json"]

    prepared = _prepare(
        [admission],
        authorized_post_processing_write_scope=scope,
    )

    assert prepared[0] is not admission
    assert prepared[0].tool_call.input_metadata.generated_unit.startswith(
        "def inline"
    )
    assert prepared[0].tool_call.input_metadata.runtime_handles[
        "_post_processing_write_scope"
    ] == tuple(scope)
    assert prepared[0].selection.input_metadata is (
        prepared[0].tool_call.input_metadata
    )


def test_preparation_resolves_artifact_and_binds_scope() -> None:
    code = "def verified():\n    return True\n"
    ledger = ProviderCodeArtifactLedger()
    reference = _reference(ledger, code)
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "artifact_ref": reference.model_dump(mode="json"),
            "generated_unit": "def untrusted():\n    return False\n",
        }
    )

    prepared = _prepare(
        [admission],
        code_artifact_ledger=ledger,
        authorized_post_processing_write_scope=[],
    )

    assert prepared[0].tool_call.input_metadata.generated_unit == code
    assert prepared[0].tool_call.input_metadata.runtime_handles[
        "_post_processing_write_scope"
    ] == ()


def test_preparation_requires_explicit_scope_for_admitted_mutation() -> None:
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "generated_unit": "def inline():\n    return True\n",
        }
    )

    with pytest.raises(
        ProviderMutationExecutionBatchError,
        match="explicit post-processing write scope",
    ):
        _prepare([admission])

    assert admission.tool_call.input_metadata.runtime_handles == {}


def test_preparation_is_atomic_when_later_artifact_is_unknown() -> None:
    inline = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "generated_unit": "def inline():\n    return True\n",
        },
        call_id="provider-inline",
        ordinal=1,
    )
    source_ledger = ProviderCodeArtifactLedger()
    unknown_reference = _reference(
        source_ledger,
        "def unknown():\n    return True\n",
    )
    unresolved = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "artifact_ref": unknown_reference.model_dump(mode="json"),
        },
        call_id="provider-artifact",
        ordinal=2,
    )

    with pytest.raises(
        ProviderMutationExecutionBatchError,
        match="could not be prepared",
    ):
        _prepare(
            [inline, unresolved],
            code_artifact_ledger=ProviderCodeArtifactLedger(),
            authorized_post_processing_write_scope=[],
        )

    assert inline.tool_call.input_metadata.runtime_handles == {}
    assert unresolved.tool_call.input_metadata.generated_unit is None


def test_blocked_mutation_does_not_validate_ledger_or_scope() -> None:
    blocked = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "generated_unit": "def inline():\n    return True\n",
        },
        confirmed=False,
    )

    prepared = _prepare(
        [blocked],
        code_artifact_ledger=object(),
        authorized_post_processing_write_scope=(item for item in ()),
    )

    assert prepared == (blocked,)


def test_preparation_rejects_mismatched_admission_views() -> None:
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "generated_unit": "def inline():\n    return True\n",
        }
    )
    mismatched = admission.model_copy(
        update={
            "selection": admission.selection.model_copy(
                update={"tool_name": "file_reader"}
            )
        }
    )

    with pytest.raises(
        ProviderMutationExecutionBatchError,
        match="same tool",
    ):
        _prepare(
            [mismatched],
            authorized_post_processing_write_scope=[],
        )
