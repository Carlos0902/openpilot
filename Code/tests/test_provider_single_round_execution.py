from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.llm import LLMResponse, LLMToolCall, LLMToolFunctionCall
from core.provider_code_artifact_ledger import ProviderCodeArtifactLedger
from core.provider_single_round_execution import (
    ProviderExecutedToolRound,
    ProviderSingleRoundExecutionError,
    ProviderSingleRoundStage,
    execute_preflighted_provider_tool_round,
)
from core.provider_single_round_preflight import (
    validated_provider_single_round_inputs,
)
from core.provider_tool_admission import ProviderToolAdmission
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import ToolCallMetadata, ToolInputMetadata, ToolLoopMetadata
from tools.tool_selection import SelectionReason, ToolSelection


def _prepared(
    provider_call_id: str = "provider-reader",
    tool_name: str = "file_reader",
):
    arguments = (
        '{"file_path":"README.md"}'
        if tool_name == "file_reader"
        else (
            '{"file_path":"app.py","operation_kind":"add_symbol",'
            '"generated_unit":"def added():\\n    return True\\n"}'
        )
    )
    call = LLMToolCall(
        id=provider_call_id,
        function=LLMToolFunctionCall(name=tool_name, arguments=arguments),
    )
    response = LLMResponse(
        content="",
        reasoning_content="Use the admitted evidence.",
        tool_calls=[call],
        model="test-model",
        provider="test-provider",
        finish_reason="tool_calls",
    )
    input_metadata = ToolInputMetadata(
        tool_name=tool_name,
        file_path="README.md" if tool_name == "file_reader" else "app.py",
        operation_kind="add_symbol" if tool_name == "file_patch_writer" else None,
        generated_unit=(
            "def added():\n    return True\n"
            if tool_name == "file_patch_writer"
            else None
        ),
    )
    project_call_id = f"project-{provider_call_id}"
    tool_call = ToolCallMetadata(
        session_id="session",
        task_id="task",
        step_id="step-1",
        call_id=project_call_id,
        provider_call_id=provider_call_id,
        tool_name=tool_name,
        input_metadata=input_metadata,
        round_index=1,
    )
    admission = ProviderToolAdmission(
        status="admitted",
        provider_call_id=provider_call_id,
        project_call_id=project_call_id,
        tool_call=tool_call,
        selection=ToolSelection(
            step_id="step-1",
            tool_name=tool_name,
            reason=SelectionReason.ONLY_OPTION,
            input_metadata=input_metadata,
        ),
    )
    ledger = ProviderCodeArtifactLedger()
    return validated_provider_single_round_inputs(
        response,
        [admission],
        round_index=1,
        allow_mutations=tool_name == "file_patch_writer",
        code_artifact_ledger=ledger,
    )


def _loop_result(
    provider_call_id: str,
    *,
    tool_name: str = "file_reader",
) -> ToolEventLoopRunResult:
    return ToolEventLoopRunResult(
        success=True,
        tool_results=[
            {
                "provider_call_id": provider_call_id,
                "call_id": f"project-{provider_call_id}",
                "tool": tool_name,
                "success": True,
                "result": {
                    "kind": "file_artifact",
                    "file_path": "README.md",
                    "content": "bounded evidence",
                    "lines_read": 1,
                    "total_lines": 1,
                    "truncated": False,
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


class _Runner:
    def __init__(self, loop_result: ToolEventLoopRunResult) -> None:
        self.owner = SimpleNamespace(_session_id=lambda: "session")
        self.loop_result = loop_result
        self.calls = []

    def run_provider_tool_calls(self, task, admissions, *, round_index=1):
        self.calls.append(("read", task, admissions, round_index, {}))
        return self.loop_result

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
            (
                "mutation",
                task,
                admissions,
                round_index,
                {
                    "code_artifact_ledger": code_artifact_ledger,
                    "authorized_post_processing_write_scope": (
                        authorized_post_processing_write_scope
                    ),
                },
            )
        )
        return self.loop_result


def test_execution_dispatches_projects_and_composes_one_round() -> None:
    prepared = _prepared()
    runner = _Runner(_loop_result("provider-reader"))

    result = execute_preflighted_provider_tool_round(
        runner,
        SimpleNamespace(id="task"),
        prepared,
    )

    assert isinstance(result, ProviderExecutedToolRound)
    assert result.loop_result is runner.loop_result
    assert len(result.tool_results) == 1
    assert result.tool_results[0].tool_call_id == "provider-reader"
    assert [message.role for message in result.wire_messages] == [
        "assistant",
        "tool",
    ]
    assert [call[0] for call in runner.calls] == ["read"]


def test_execution_forwards_mutation_runtime_inputs() -> None:
    prepared = _prepared("provider-writer", "file_patch_writer")
    runner = _Runner(
        _loop_result("provider-writer", tool_name="file_patch_writer")
    )
    scope = ["app.py.index.json", "sketch.json"]

    execute_preflighted_provider_tool_round(
        runner,
        SimpleNamespace(id="task"),
        prepared,
        authorized_post_processing_write_scope=scope,
    )

    assert runner.calls[0][0] == "mutation"
    assert runner.calls[0][-1] == {
        "code_artifact_ledger": prepared.code_artifact_ledger,
        "authorized_post_processing_write_scope": scope,
    }


def test_execution_rejects_unpreflighted_input_before_dispatch() -> None:
    runner = _Runner(_loop_result("provider-reader"))

    with pytest.raises(ProviderSingleRoundExecutionError) as caught:
        execute_preflighted_provider_tool_round(
            runner,
            SimpleNamespace(id="task"),
            object(),
        )

    assert caught.value.stage is ProviderSingleRoundStage.PREFLIGHT
    assert caught.value.loop_result is None
    assert runner.calls == []


def test_projection_failure_retains_completed_loop_evidence() -> None:
    prepared = _prepared()
    runner = _Runner(_loop_result("provider-reader", tool_name="wrong_tool"))

    with pytest.raises(ProviderSingleRoundExecutionError) as caught:
        execute_preflighted_provider_tool_round(
            runner,
            SimpleNamespace(id="task"),
            prepared,
        )

    assert caught.value.stage is ProviderSingleRoundStage.RESULT_PROJECTION
    assert caught.value.loop_result is runner.loop_result
    assert caught.value.tool_results is None
    assert len(runner.calls) == 1


def test_wire_failure_retains_loop_and_projected_results(monkeypatch) -> None:
    prepared = _prepared()
    runner = _Runner(_loop_result("provider-reader"))

    def fail_wire(*_args, **_kwargs):
        raise ValueError("wire failed")

    monkeypatch.setattr(
        "core.provider_single_round_execution.provider_tool_wire_exchange",
        fail_wire,
    )
    with pytest.raises(ProviderSingleRoundExecutionError) as caught:
        execute_preflighted_provider_tool_round(
            runner,
            SimpleNamespace(id="task"),
            prepared,
        )

    assert caught.value.stage is ProviderSingleRoundStage.WIRE_COMPOSITION
    assert caught.value.loop_result is runner.loop_result
    assert caught.value.tool_results is not None
    assert len(caught.value.tool_results) == 1


def test_execution_failure_redacts_exception_text() -> None:
    prepared = _prepared()
    runner = _Runner(_loop_result("provider-reader"))

    def fail_execution(*_args, **_kwargs):
        raise RuntimeError("authorization=secret-token")

    runner.run_provider_tool_calls = fail_execution
    with pytest.raises(ProviderSingleRoundExecutionError) as caught:
        execute_preflighted_provider_tool_round(
            runner,
            SimpleNamespace(id="task"),
            prepared,
        )

    assert caught.value.stage is ProviderSingleRoundStage.EXECUTION
    assert caught.value.loop_result is None
    assert "secret-token" not in str(caught.value)
