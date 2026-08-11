from __future__ import annotations

import json

import pytest

from core.llm import LLMToolCall, LLMToolFunctionCall
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
from metadata import RuntimeBudgetMetadata, ToolContractMetadata
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


def _inline_admission(*, confirmed: bool = True):
    call = LLMToolCall(
        id="provider-writer",
        function=LLMToolFunctionCall(
            name="file_patch_writer",
            arguments=json.dumps(
                {
                    "file_path": "app.py",
                    "operation_kind": "add_symbol",
                    "generated_unit": "def added():\n    return 2\n",
                }
            ),
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
        user_confirmed=confirmed,
        allow_mutations=True,
        write_scope=["app.py"],
        project_path=None,
        validation_command="python -m compileall -q app.py",
    )


def test_inline_patch_binds_exact_post_processing_scope_to_both_views() -> None:
    admission = _inline_admission()
    scope = [".openpilot/file_indexes/app.py.index.json", "sketch.json"]

    bound = bind_provider_patch_artifact(
        admission,
        authorized_post_processing_write_scope=scope,
    )

    assert bound is not admission
    assert bound.tool_call.input_metadata.generated_unit == (
        "def added():\n    return 2\n"
    )
    assert bound.tool_call.input_metadata.runtime_handles[
        "_post_processing_write_scope"
    ] == tuple(scope)
    assert bound.selection is not None
    assert bound.selection.input_metadata is bound.tool_call.input_metadata


@pytest.mark.parametrize(
    "scope",
    [
        (item for item in ()),
        ["same", "same"],
        [f"path-{index}" for index in range(MAX_PROVIDER_SCOPE_PATHS + 1)],
    ],
)
def test_inline_patch_rejects_invalid_post_processing_scope(scope) -> None:
    admission = _inline_admission()

    with pytest.raises(ProviderPatchArtifactBindingError):
        bind_provider_patch_artifact(
            admission,
            authorized_post_processing_write_scope=scope,
        )

    assert admission.tool_call.input_metadata.runtime_handles == {}


def test_blocked_inline_patch_does_not_validate_or_bind_scope() -> None:
    admission = _inline_admission(confirmed=False)

    bound = bind_provider_patch_artifact(
        admission,
        authorized_post_processing_write_scope=(item for item in ()),
    )

    assert bound is admission
    assert bound.status == "blocked"
