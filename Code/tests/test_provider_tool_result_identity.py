from __future__ import annotations

import pytest

from core.tool_event_loop import ToolEventLoopRunner
from metadata import ToolCallMetadata, ToolInputMetadata


@pytest.mark.parametrize("provider_call_id", ["provider-call-1", None])
def test_tool_result_preserves_optional_provider_call_identity(
    provider_call_id,
) -> None:
    runner = object.__new__(ToolEventLoopRunner)
    runner.tool_results = []
    input_metadata = ToolInputMetadata(
        tool_name="file_reader",
        file_path="README.md",
    )
    tool_call = ToolCallMetadata(
        session_id="session",
        task_id="task",
        step_id="step-1",
        call_id="project-call-1",
        provider_call_id=provider_call_id,
        tool_name="file_reader",
        input_metadata=input_metadata,
    )

    runner._append_tool_result(
        tool_call,
        input_metadata,
        True,
        None,
    )

    result = runner.tool_results[0]
    assert result["call_id"] == "project-call-1"
    if provider_call_id is None:
        assert "provider_call_id" not in result
    else:
        assert result["provider_call_id"] == provider_call_id
