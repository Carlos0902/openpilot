from __future__ import annotations

import json

import pytest

from core.llm import LLMResponse, LLMToolCall, LLMToolFunctionCall
from core.provider_tool_batch_admission import MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE
from core.provider_tool_duplicate_partition import ProviderToolDuplicateBlock
from core.provider_tool_result_batch import (
    ProviderToolResultBatchError,
    provider_tool_result_batch,
)
from core.provider_tool_result_payload import MIN_PROVIDER_TOOL_RESULT_CHARS
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import ToolErrorMetadata, ToolLoopMetadata


def _call(call_id: str, tool_name: str = "file_reader") -> LLMToolCall:
    return LLMToolCall(
        id=call_id,
        function=LLMToolFunctionCall(
            name=tool_name,
            arguments='{"file_path":"README.md"}',
        ),
    )


def _response(*calls: LLMToolCall) -> LLMResponse:
    return LLMResponse(
        content="",
        reasoning_content="I will inspect the requested evidence.",
        tool_calls=list(calls),
        model="test-model",
        provider="test-provider",
        finish_reason="tool_calls",
    )


def _loop_result(
    *tool_results: dict,
    recoverable_errors: list[ToolErrorMetadata] | None = None,
) -> ToolEventLoopRunResult:
    return ToolEventLoopRunResult(
        success=all(item.get("success") is True for item in tool_results),
        tool_results=list(tool_results),
        last_output=None,
        loop_metadata=ToolLoopMetadata(
            session_id="session",
            task_id="task",
            status="completed",
            success=True,
            recoverable_errors=recoverable_errors or [],
        ),
    )


def _result(
    provider_call_id: str,
    *,
    project_call_id: str,
    tool: str = "file_reader",
    success: bool = True,
    result=None,
) -> dict:
    return {
        "provider_call_id": provider_call_id,
        "call_id": project_call_id,
        "tool": tool,
        "success": success,
        "result": result
        if result is not None
        else {
            "kind": "file_artifact",
            "content": f"evidence for {provider_call_id}",
            "file_path": "README.md",
            "lines_read": 1,
            "total_lines": 1,
            "truncated": False,
        },
    }


def test_batch_correlates_out_of_order_loop_results_in_response_order() -> None:
    response = _response(_call("provider-1"), _call("provider-2"))
    loop_result = _loop_result(
        _result("provider-2", project_call_id="project-2"),
        _result("provider-1", project_call_id="project-1"),
    )

    results = provider_tool_result_batch(response, loop_result)

    assert [result.tool_call_id for result in results] == [
        "provider-1",
        "provider-2",
    ]
    first = json.loads(results[0].content)
    assert first["success"] is True
    assert first["tool"] == "file_reader"
    assert first["result"]["evidence_status"] == "complete"
    assert first["artifact_ref"]["source_id"] == "project-1"
    assert first["artifact_ref"]["provider_call_id"] == "provider-1"


def test_batch_marks_missing_execution_as_typed_abort() -> None:
    response = _response(_call("provider-executed"), _call("provider-aborted"))

    results = provider_tool_result_batch(
        response,
        _loop_result(
            _result("provider-executed", project_call_id="project-executed")
        ),
    )

    aborted = json.loads(results[1].content)
    assert aborted == {
        "error": (
            "The project stopped this batch before executing this call; "
            "retry it in a later round."
        ),
        "error_type": "ProviderToolBatchAborted",
        "success": False,
        "tool": "file_reader",
    }


def test_batch_attaches_recoverable_error_by_provider_identity() -> None:
    error = ToolErrorMetadata(
        session_id="session",
        task_id="task",
        step_id="step",
        call_id="project-failed",
        provider_call_id="provider-failed",
        tool_name="file_reader",
        error_type="FileReaderDirectoryPath",
        error_message="Expected a file path.",
        suggested_recovery="Choose a concrete file.",
    )
    item = _result(
        "provider-failed",
        project_call_id="project-failed",
        success=False,
        result={"kind": "tool_error"},
    )

    results = provider_tool_result_batch(
        _response(_call("provider-failed")),
        _loop_result(item, recoverable_errors=[error]),
    )

    payload = json.loads(results[0].content)
    assert payload["success"] is False
    assert payload["error_type"] == "FileReaderDirectoryPath"
    assert payload["error"] == "Expected a file path."
    assert payload["suggested_recovery"] == "Choose a concrete file."


def test_duplicate_block_preempts_missing_execution_result() -> None:
    block = ProviderToolDuplicateBlock(
        provider_call_id="provider-duplicate",
        previous_provider_call_id="provider-original",
        tool_name="file_reader",
        signature="a" * 64,
        round_index=2,
    )

    results = provider_tool_result_batch(
        _response(_call("provider-duplicate")),
        _loop_result(),
        duplicate_blocks=[block],
    )

    payload = json.loads(results[0].content)
    assert payload["error_type"] == "ProviderToolDuplicateAttempt"
    assert payload["previous_call_id"] == "provider-original"


def test_batch_rejects_duplicate_block_with_execution_result() -> None:
    block = ProviderToolDuplicateBlock(
        provider_call_id="provider-duplicate",
        previous_provider_call_id="provider-original",
        tool_name="file_reader",
        signature="a" * 64,
        round_index=2,
    )

    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(_call("provider-duplicate")),
            _loop_result(
                _result(
                    "provider-duplicate",
                    project_call_id="project-duplicate",
                )
            ),
            duplicate_blocks=[block],
        )


def test_declared_window_identity_is_forwarded_to_projection() -> None:
    item = _result(
        "provider-window",
        project_call_id="project-window",
        result={
            "kind": "file_artifact",
            "content": "window evidence " * 100,
            "file_path": "source.py",
            "lines_read": 120,
            "total_lines": 2_000,
            "truncated": True,
            "read_window": {
                "read_mode": "adaptive",
                "offset": 1_240,
                "max_lines": 120,
            },
        },
    )

    results = provider_tool_result_batch(
        _response(_call("provider-window")),
        _loop_result(item),
        declared_window_complete_ids=["provider-window"],
        char_budget=MIN_PROVIDER_TOOL_RESULT_CHARS,
    )

    payload = json.loads(results[0].content)
    assert payload["result"]["evidence_status"] == "complete"
    assert payload["result"]["projection_status"] == "bounded_window"
    assert payload["display_truncated"] is True
    assert "projection_compacted" not in payload


@pytest.mark.parametrize(
    "loop_results",
    [
        [
            _result("provider-1", project_call_id="project-1"),
            _result("provider-1", project_call_id="project-2"),
        ],
        [_result("provider-extra", project_call_id="project-extra")],
    ],
)
def test_batch_rejects_duplicate_or_extra_provider_result_ids(
    loop_results: list[dict],
) -> None:
    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(_call("provider-1")),
            _loop_result(*loop_results),
        )


def test_batch_rejects_tool_name_mismatch() -> None:
    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(_call("provider-1", "file_reader")),
            _loop_result(
                _result(
                    "provider-1",
                    project_call_id="project-1",
                    tool="command_executor",
                )
            ),
        )


@pytest.mark.parametrize("success", [1, 0, "true", None])
def test_batch_requires_literal_result_success(success) -> None:
    item = _result("provider-1", project_call_id="project-1")
    item["success"] = success

    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(_call("provider-1")),
            _loop_result(item),
        )


def test_batch_omits_absent_optional_error_fields() -> None:
    results = provider_tool_result_batch(
        _response(_call("provider-1")),
        _loop_result(_result("provider-1", project_call_id="project-1")),
    )

    payload = json.loads(results[0].content)
    assert "error_type" not in payload
    assert "error" not in payload
    assert "suggested_recovery" not in payload


def test_batch_ignores_ordinary_local_result_maps() -> None:
    results = provider_tool_result_batch(
        _response(_call("provider-1")),
        _loop_result(
            {
                "call_id": "local-call",
                "tool": "file_reader",
                "success": True,
                "result": {"kind": "text_artifact", "content": "local"},
            }
        ),
    )

    payload = json.loads(results[0].content)
    assert payload["error_type"] == "ProviderToolBatchAborted"


def test_batch_accepts_exact_call_limit_and_rejects_overflow() -> None:
    calls = [
        _call(f"provider-{index}")
        for index in range(MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE)
    ]
    items = [
        _result(
            call.id,
            project_call_id=f"project-{index}",
            result={"kind": "command_result", "count": index},
        )
        for index, call in enumerate(calls)
    ]

    results = provider_tool_result_batch(
        _response(*calls),
        _loop_result(*items),
    )

    assert len(results) == MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE

    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(*calls, _call("provider-overflow")),
            _loop_result(*items),
        )


def test_batch_does_not_mutate_event_loop_result_maps() -> None:
    item = _result("provider-1", project_call_id="project-1")
    original = json.loads(json.dumps(item))

    provider_tool_result_batch(
        _response(_call("provider-1")),
        _loop_result(item),
    )

    assert item == original


@pytest.mark.parametrize(
    ("duplicate_blocks", "declared_window_complete_ids"),
    [
        ((block for block in ()), ()),
        ((), (call_id for call_id in ())),
        ([object()], ()),
        ((), [1]),
    ],
)
def test_batch_rejects_unbounded_or_invalid_control_sequences(
    duplicate_blocks,
    declared_window_complete_ids,
) -> None:
    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(_call("provider-1")),
            _loop_result(),
            duplicate_blocks=duplicate_blocks,
            declared_window_complete_ids=declared_window_complete_ids,
        )


def test_batch_rejects_declared_window_id_outside_response() -> None:
    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(_call("provider-1")),
            _loop_result(),
            declared_window_complete_ids=["provider-extra"],
        )


def test_batch_rejects_declared_window_for_missing_execution() -> None:
    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(_call("provider-window")),
            _loop_result(),
            declared_window_complete_ids=["provider-window"],
        )


def test_batch_rejects_recoverable_error_without_result_record() -> None:
    error = ToolErrorMetadata(
        session_id="session",
        task_id="task",
        step_id="step",
        call_id="project-failed",
        provider_call_id="provider-failed",
        tool_name="file_reader",
        error_type="FileReaderFailure",
        error_message="Read failed.",
    )

    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(_call("provider-failed")),
            _loop_result(recoverable_errors=[error]),
        )


@pytest.mark.parametrize(
    "char_budget",
    [MIN_PROVIDER_TOOL_RESULT_CHARS - 1, True, 800.0],
)
def test_empty_batch_still_rejects_invalid_character_budget(char_budget) -> None:
    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(),
            _loop_result(),
            char_budget=char_budget,
        )
