from __future__ import annotations

from types import SimpleNamespace

from core.tool_event_emitter import ToolEventEmitter
from metadata import ToolInputMetadata


def _emitter() -> ToolEventEmitter:
    return ToolEventEmitter(
        SimpleNamespace(
            tool_registry=None,
            _project_environments={},
        )
    )


def test_provider_identity_is_preserved_when_tool_call_is_created() -> None:
    emitter = _emitter()
    input_metadata = ToolInputMetadata(
        tool_name="file_reader",
        file_path="README.md",
    )

    tool_call = emitter.create_tool_call(
        session_id="session",
        task_id="task",
        step_id="step",
        call_id="task:r1:c1",
        provider_call_id="provider-call-1",
        provider_executed=True,
        tool_name="file_reader",
        input_metadata=input_metadata,
        tool_context=None,
    )

    assert tool_call.call_id == "task:r1:c1"
    assert tool_call.provider_call_id == "provider-call-1"
    assert tool_call.provider_executed is True


def test_provider_execution_provenance_is_inherited_by_emitted_events() -> None:
    emitter = _emitter()
    input_metadata = ToolInputMetadata(
        tool_name="file_reader",
        file_path="README.md",
    )
    tool_call = emitter.create_tool_call(
        session_id="session",
        task_id="task",
        step_id="step",
        call_id="task:r1:c1",
        provider_call_id="provider-call-1",
        provider_executed=True,
        tool_name="file_reader",
        input_metadata=input_metadata,
        tool_context=None,
    )

    event = emitter.emit(
        task_id="task",
        tool_call=tool_call,
        event_type="running",
        status="running",
        input_metadata=input_metadata,
    )

    assert event.tool_call is tool_call
    assert event.provider_executed is True


def test_local_tool_events_remain_non_provider_by_default() -> None:
    emitter = _emitter()
    input_metadata = ToolInputMetadata(
        tool_name="file_reader",
        file_path="README.md",
    )
    tool_call = emitter.create_tool_call(
        session_id="session",
        task_id="task",
        step_id="step",
        call_id="task:r1:c1",
        tool_name="file_reader",
        input_metadata=input_metadata,
        tool_context=None,
    )

    event = emitter.emit(
        task_id="task",
        tool_call=tool_call,
        event_type="running",
        status="running",
        input_metadata=input_metadata,
    )

    assert tool_call.provider_call_id is None
    assert tool_call.provider_executed is False
    assert event.provider_executed is False
