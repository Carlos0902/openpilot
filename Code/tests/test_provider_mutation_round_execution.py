from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autonomous_iteration.runtime_controller import StateUpdater
from core.llm import LLMResponse, LLMToolCall, LLMToolFunctionCall
from core.provider_code_artifact_ledger import ProviderCodeArtifactLedger
from core.provider_mutation_round_execution import (
    ProviderMutationRoundExecutionError,
    ProviderMutationRoundStage,
    execute_provider_mutation_round,
)
from core.provider_single_round_preflight import validated_provider_single_round_inputs
from core.provider_tool_admission import ProviderToolAdmission
from core.tool_event_loop import ToolEventLoopRunResult
from core.tool_contracts import PermissionLevel, ToolCapability, ToolDefinition
from metadata import (
    ResultStatus,
    RuntimeStateMetadata,
    TextArtifactMetadata,
    ToolCallMetadata,
    ToolContractMetadata,
    ToolLoopMetadata,
    ToolResultMetadata,
    ToolInputMetadata,
)
from tools.file_patch_writer import FILE_PATCH_WRITER_DEFINITION
from tools.tool_selection import SelectionReason, ToolSelection


class _Registry:
    def get(self, name):
        if name == "file_patch_writer":
            return FILE_PATCH_WRITER_DEFINITION
        return None

    def get_executor(self, name):
        return object() if name == "file_patch_writer" else None


class _Owner:
    def __init__(self, runtime):
        self.runtime = runtime

    def _session_id(self):
        return "session"

    def _log(self, *args, **kwargs):
        return None

    def _show_tool_running(self, *args, **kwargs):
        return None

    def _show_tool_result(self, *args, **kwargs):
        return None

    def _log_tool_start(self, *args, **kwargs):
        return None

    def _log_tool_complete(self, *args, **kwargs):
        return None

    def _summarize_metadata_output(self, value):
        return value


class _Runner:
    def __init__(self, loop_result):
        self.owner = SimpleNamespace(_session_id=lambda: "session")
        self.loop_result = loop_result
        self.calls = []

    def run_provider_mutation_tool_calls(
        self,
        task,
        admissions,
        *,
        round_index=1,
        code_artifact_ledger=None,
        authorized_post_processing_write_scope=None,
    ):
        self.calls.append(
            {
                "task": task,
                "admissions": admissions,
                "round_index": round_index,
                "scope": authorized_post_processing_write_scope,
            }
        )
        return self.loop_result

    def run_provider_tool_calls(self, *args, **kwargs):
        raise AssertionError("mutation round must not dispatch through read-only execution")


def _prepared():
    generated = "def added():\n    return True\n"
    call = LLMToolCall(
        id="provider-writer",
        function=LLMToolFunctionCall(
            name="file_patch_writer",
            arguments=(
                json.dumps(
                    {
                        "file_path": "app.py",
                        "operation_kind": "add_symbol",
                        "generated_unit": generated,
                    }
                )
            ),
        ),
    )
    response = LLMResponse(
        content="",
        reasoning_content="Apply the admitted patch.",
        tool_calls=[call],
        model="test-model",
        provider="test-provider",
        finish_reason="tool_calls",
    )
    input_metadata = ToolInputMetadata(
        tool_name="file_patch_writer",
        file_path="app.py",
        operation_kind="add_symbol",
        generated_unit=generated,
    )
    tool_call = ToolCallMetadata(
        session_id="session",
        task_id="task",
        step_id="step-1",
        call_id="project-writer",
        provider_call_id="provider-writer",
        tool_name="file_patch_writer",
        input_metadata=input_metadata,
        round_index=1,
    )
    admission = ProviderToolAdmission(
        status="admitted",
        provider_call_id="provider-writer",
        project_call_id="project-writer",
        tool_call=tool_call,
        selection=ToolSelection(
            step_id="step-1",
            tool_name="file_patch_writer",
            reason=SelectionReason.ONLY_OPTION,
            input_metadata=input_metadata,
        ),
    )
    return response, [admission]


def _loop_result():
    return ToolEventLoopRunResult(
        success=True,
        tool_results=[
            {
                "provider_call_id": "provider-writer",
                "call_id": "project-writer",
                "tool": "file_patch_writer",
                "success": True,
                "input_metadata": {
                    "file_path": "app.py",
                    "operation_kind": "add_symbol",
                },
                "result": {
                    "bytes_written": 28,
                    "attributes": {
                        "changed_ranges": [{"line_start": 1, "line_end": 2}]
                    },
                },
            }
        ],
        last_output=None,
        loop_metadata=ToolLoopMetadata(
            session_id="session",
            task_id="task",
            status="completed",
            success=True,
        ),
    )


def test_mutation_round_executes_and_projects_body_free_receipt():
    response, admissions = _prepared()
    result = execute_provider_mutation_round(
        _Runner(_loop_result()),
        SimpleNamespace(id="task"),
        response,
        admissions,
        round_index=1,
        validation_command="python -m compileall -q app.py",
        authorized_post_processing_write_scope=["app.py.index.json"],
        code_artifact_ledger=ProviderCodeArtifactLedger(),
    )

    assert result.receipt["status"] == "mutation_applied"
    assert result.receipt["file_path"] == "app.py"
    assert result.receipt["validation_command"] == "python -m compileall -q app.py"
    assert "generated_unit" not in str(result.receipt)


@pytest.mark.parametrize(
    "overrides",
    [
        {"authorized_post_processing_write_scope": []},
        {"validation_command": ""},
        {"duplicate_blocks": [object()]},
    ],
)
def test_mutation_round_rejects_missing_or_conflicting_entry_facts(overrides):
    response, admissions = _prepared()
    values = {
        "round_index": 1,
        "validation_command": "python -m compileall -q app.py",
        "authorized_post_processing_write_scope": ["app.py.index.json"],
    }
    values.update(overrides)
    with pytest.raises(ProviderMutationRoundExecutionError) as caught:
        execute_provider_mutation_round(
            _Runner(_loop_result()),
            SimpleNamespace(id="task"),
            response,
            admissions,
            **values,
        )
    assert caught.value.stage is ProviderMutationRoundStage.INPUT


def test_mutation_round_rejects_read_admission_before_dispatch():
    response, admissions = _prepared()
    read_call = admissions[0].model_copy(
        update={"tool_call": admissions[0].tool_call.model_copy(update={"tool_name": "file_reader"})}
    )
    with pytest.raises(ProviderMutationRoundExecutionError) as caught:
        execute_provider_mutation_round(
            _Runner(_loop_result()),
            SimpleNamespace(id="task"),
            response,
            [read_call],
            round_index=1,
            validation_command="python -m compileall -q app.py",
            authorized_post_processing_write_scope=["app.py.index.json"],
        )
    assert caught.value.stage is ProviderMutationRoundStage.INPUT
