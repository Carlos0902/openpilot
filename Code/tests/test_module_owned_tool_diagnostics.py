from __future__ import annotations

from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from autonomous_iteration.task_models import Task
from metadata import FailureMetadata, ResultStatus, TextArtifactMetadata, ToolInputMetadata, ToolResultMetadata
from runtime_diagnostics import DiagnosticRecorder
from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks


class _FakeLLM:
    pass


def _autopilot(tmp_path) -> IntelligentAutopilot:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path / "diagnostics"))
    autopilot = IntelligentAutopilot(
        _FakeLLM(),
        log_file=tmp_path / "autopilot.jsonl",
        runtime_diagnostics_hooks=hooks,
    )
    autopilot.session_id = "module-tool-session"
    return autopilot


def _successful_project_state_result() -> ToolResultMetadata:
    return ToolResultMetadata(
        tool_name="project_state_reader",
        status=ResultStatus.SUCCESS,
        result=TextArtifactMetadata(content="project state loaded"),
    )


def _trajectory_events(autopilot: IntelligentAutopilot) -> list[dict]:
    hooks = autopilot.runtime_diagnostics_hooks
    assert hooks is not None
    run = hooks.recorder.load_run("module-tool-session")
    assert run is not None
    return hooks.recorder.load_trajectory_events(run.run_id)


def test_module_owned_project_state_reader_persists_started_and_succeeded_events(tmp_path) -> None:
    autopilot = _autopilot(tmp_path)
    task = Task(id="improvement-task", description="Read the current project state")
    tool_input = ToolInputMetadata(
        tool_name="project_state_reader",
        project_path=str(tmp_path),
    )

    result = autopilot._execute_module_owned_tool(
        task=task,
        step_id="read-project-state",
        tool_name="project_state_reader",
        input_metadata=tool_input,
        executor=lambda _metadata: _successful_project_state_result(),
    )

    expected_call_id = result.call_id
    assert expected_call_id is not None
    assert expected_call_id.startswith("improvement-task:read-project-state:")
    assert result.success is True
    assert result.call_id == expected_call_id
    assert result.tool_context is not None
    assert result.tool_context.call_id == expected_call_id
    assert result.tool_context.task_id == task.id
    assert result.tool_context.step_id == "read-project-state"
    assert result.tool_context.project_path == str(tmp_path)
    assert result.tool_context.attributes["execution_route"] == "module_owned"

    events = _trajectory_events(autopilot)
    assert [event["event_type"] for event in events] == ["tool_called", "tool_succeeded"]
    assert all(event["payload"]["correlation"]["execution_id"] == expected_call_id for event in events)
    assert events[0]["payload_kind"] == "tool_call"
    assert events[0]["payload"]["tool_context"]["call_id"] == expected_call_id
    assert events[-1]["payload_kind"] == "tool_execution_envelope"
    assert events[-1]["payload"]["tool_context"]["call_id"] == expected_call_id


def test_module_owned_project_state_reader_persists_tool_failed_as_terminal_event(tmp_path) -> None:
    autopilot = _autopilot(tmp_path)
    task = Task(id="improvement-task", description="Read the current project state")
    tool_input = ToolInputMetadata(
        tool_name="project_state_reader",
        project_path=str(tmp_path),
    )

    def fail_reader(_metadata: ToolInputMetadata) -> ToolResultMetadata:
        raise RuntimeError("state reader exploded")

    result = autopilot._execute_module_owned_tool(
        task=task,
        step_id="read-project-state",
        tool_name="project_state_reader",
        input_metadata=tool_input,
        executor=fail_reader,
    )

    expected_call_id = result.call_id
    assert expected_call_id is not None
    assert expected_call_id.startswith("improvement-task:read-project-state:")
    assert result.success is False
    assert result.call_id == expected_call_id
    assert result.tool_context is not None
    assert result.tool_context.call_id == expected_call_id

    events = _trajectory_events(autopilot)
    assert [event["event_type"] for event in events] == ["tool_called", "tool_failed"]
    terminal = events[-1]
    assert terminal["payload_kind"] == "tool_error"
    assert terminal["payload"]["call_id"] == expected_call_id
    assert terminal["payload"]["tool_name"] == "project_state_reader"
    assert terminal["payload"]["error_type"] == "RuntimeError"
    assert terminal["payload"]["error_message"] == "state reader exploded"
    assert terminal["payload"]["tool_context"]["call_id"] == expected_call_id
    assert terminal["payload"]["correlation"]["execution_id"] == expected_call_id


def test_module_owned_failed_result_cannot_emit_tool_succeeded(tmp_path) -> None:
    autopilot = _autopilot(tmp_path)
    task = Task(id="improvement-task", description="Read the current project state")

    result = autopilot._execute_module_owned_tool(
        task=task,
        step_id="read-project-state",
        tool_name="project_state_reader",
        input_metadata=ToolInputMetadata(
            tool_name="project_state_reader",
            project_path=str(tmp_path),
        ),
        executor=lambda _metadata: ToolResultMetadata(
            tool_name="project_state_reader",
            status=ResultStatus.FAIL,
            failure=FailureMetadata(
                error_type="ProjectStateUnavailable",
                error_message="state evidence is unavailable",
                recovery_strategy="refresh project inventory",
                details={"source": "project_state_reader"},
            ),
        ),
    )

    assert result.success is False
    assert result.failure is not None
    assert result.failure.details == {"source": "project_state_reader"}
    events = _trajectory_events(autopilot)
    assert [event["event_type"] for event in events] == ["tool_called", "tool_failed"]
    assert events[-1]["payload"]["failure"]["recovery_strategy"] == "refresh project inventory"
    assert events[-1]["payload"]["failure"]["details"] == {"source": "project_state_reader"}
