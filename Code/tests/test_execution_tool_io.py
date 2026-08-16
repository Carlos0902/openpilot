from __future__ import annotations

from types import SimpleNamespace

import pytest

from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from autonomous_iteration.task_models import Task, TaskPriority
from autonomous_iteration.tool_io import ExecutionToolIO
from core.exceptions import LLMTimeoutError
from core.tool_contracts import ToolDefinition
from metadata import (
    CodeArtifactMetadata,
    FileArtifactMetadata,
    ResultStatus,
    RuntimeExecutionMode,
    RuntimeStateMetadata,
    ToolContractMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
)
from runtime_diagnostics import DiagnosticRecorder
from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks
from tools.code_generator import CODE_GENERATION_LLM_TIMEOUT_SECONDS
from tools.tool_selection import ToolSelection


class FakeLLM:
    def __init__(self, content: str = "```python\nprint('ok')\n```") -> None:
        self.content = content
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return SimpleNamespace(content=self.content)


class FakeUI:
    def __init__(self) -> None:
        self.events = []

    def append_tool_event(self, event) -> None:
        self.events.append(event.to_json_dict() if hasattr(event, "to_json_dict") else event)

    def set_current_task_state(self, **_kwargs) -> None:
        return None


class TimeoutLLM:
    def __init__(self) -> None:
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        raise LLMTimeoutError("provider read timed out", timeout_seconds=request.timeout_seconds)


def test_tool_io_sanitizes_large_payloads_without_private_params() -> None:
    helper = ExecutionToolIO()
    params = {
        "content": "x" * 300,
        "code": "print('ok')",
        "generated_unit": "def generated():\n    return 'ok'\n" * 20,
        "task_description": "build app",
        "_llm_client": object(),
        "file_path": "app.py",
    }

    sanitized = helper.sanitize_tool_metadata(ToolInputMetadata.from_mapping("demo", params))

    assert sanitized["content"] == "<300 chars>"
    assert sanitized["content_length"] == 300
    assert sanitized["content_preview"] == "x" * 200
    assert sanitized["code"] == "<11 chars>"
    assert sanitized["generated_unit"].startswith("<")
    assert sanitized["generated_unit_length"] == len(params["generated_unit"])
    assert "return 'ok'" in sanitized["generated_unit_preview"]
    assert "_llm_client" not in sanitized
    assert sanitized["file_path"] == "app.py"


def test_tool_io_resolves_chained_metadata_for_writer_and_executor() -> None:
    helper = ExecutionToolIO()
    generated = ToolResultMetadata(
        tool_name="code_generator",
        status=ResultStatus.SUCCESS,
        result=CodeArtifactMetadata(code="print('ok')", language="python"),
    )

    writer_metadata = helper.resolve_chained_metadata(
        "file_writer",
        ToolInputMetadata.from_mapping("file_writer", {"file_path": "app.py"}),
        last_output=None,
        last_code_output=generated,
    )
    executor_metadata = helper.resolve_chained_metadata(
        "code_executor",
        ToolInputMetadata.from_mapping("code_executor", {}),
        last_output=None,
        last_code_output=generated,
    )

    assert writer_metadata.content == "print('ok')"
    assert executor_metadata.code == "print('ok')"
    assert executor_metadata.language == "python"


def test_tool_io_reroutes_python_code_away_from_requirements_file(tmp_path) -> None:
    helper = ExecutionToolIO()
    generated = ToolResultMetadata(
        tool_name="code_generator",
        status=ResultStatus.SUCCESS,
        result=CodeArtifactMetadata(
            code='"""OpenPilot Assistant main file."""\n\ndef main():\n    pass\n',
            language="python",
        ),
    )

    writer_metadata = helper.resolve_chained_metadata(
        "file_writer",
        ToolInputMetadata.from_mapping(
            "file_writer",
            {"file_path": str(tmp_path / "requirements.txt"), "operation_kind": "create_file"},
        ),
        last_output=None,
        last_code_output=generated,
    )

    assert writer_metadata.file_path == str(tmp_path / "assistant.py")
    assert writer_metadata.content.startswith('"""OpenPilot Assistant')


def test_tool_io_does_not_chain_file_content_into_command_executor() -> None:
    helper = ExecutionToolIO()
    read_result = ToolResultMetadata(
        tool_name="file_reader",
        status=ResultStatus.SUCCESS,
        result=FileArtifactMetadata(file_path="run.py", content="print('ok')\n"),
    )
    command = "chmod +x run.py"

    resolved = helper.resolve_chained_metadata(
        "command_executor",
        ToolInputMetadata.from_mapping("command_executor", {"command": command, "mode": "automatic"}),
        last_output=read_result,
        last_code_output=None,
    )

    assert resolved.to_params() == {"command": command, "mode": "automatic"}


def test_tool_io_replaces_placeholder_content_with_chained_code() -> None:
    helper = ExecutionToolIO()
    generated = ToolResultMetadata(
        tool_name="code_generator",
        status=ResultStatus.SUCCESS,
        result=CodeArtifactMetadata(code="print('real code')", language="python"),
    )

    writer_metadata = helper.resolve_chained_metadata(
        "file_writer",
        ToolInputMetadata.from_mapping(
            "file_writer",
            {
                "file_path": "assistant.py",
                "content": "PLACEHOLDER - will be replaced with actual generated code",
            },
        ),
        last_output=None,
        last_code_output=generated,
    )

    assert writer_metadata.content == "print('real code')"


def test_tool_io_preserves_substantive_content_with_config_placeholder() -> None:
    helper = ExecutionToolIO()
    generated = ToolResultMetadata(
        tool_name="code_generator",
        status=ResultStatus.SUCCESS,
        result=CodeArtifactMetadata(code="print('real code')", language="python"),
    )
    content = "API_KEY = 'YOUR_API_KEY_PLACEHOLDER'\n"

    writer_metadata = helper.resolve_chained_metadata(
        "file_writer",
        ToolInputMetadata.from_mapping("file_writer", {"file_path": "config.py", "content": content}),
        last_output=None,
        last_code_output=generated,
    )

    assert writer_metadata.content == content


def test_tool_io_replaces_chinese_placeholder_content_with_chained_code() -> None:
    helper = ExecutionToolIO()
    generated = ToolResultMetadata(
        tool_name="code_generator",
        status=ResultStatus.SUCCESS,
        result=CodeArtifactMetadata(code="print('real code')", language="python"),
    )

    writer_metadata = helper.resolve_chained_metadata(
        "file_writer",
        ToolInputMetadata.from_mapping(
            "file_writer",
            {
                "file_path": "assistant.py",
                "content": "# 代码将由code_generation生成后填充，此处占位",
            },
        ),
        last_output=None,
        last_code_output=generated,
    )

    assert writer_metadata.content == "print('real code')"


def test_tool_io_replaces_to_be_filled_content_with_chained_code() -> None:
    helper = ExecutionToolIO()
    generated = ToolResultMetadata(
        tool_name="code_generator",
        status=ResultStatus.SUCCESS,
        result=CodeArtifactMetadata(code="print('real code')", language="python"),
    )

    writer_metadata = helper.resolve_chained_metadata(
        "file_writer",
        ToolInputMetadata.from_mapping(
            "file_writer",
            {
                "file_path": "assistant.py",
                "content": "TO_BE_FILLED_BY_CODEGENERATION",
            },
        ),
        last_output=None,
        last_code_output=generated,
    )

    assert writer_metadata.content == "print('real code')"


def test_tool_io_resolves_tool_selection_dependency_outputs() -> None:
    helper = ExecutionToolIO()
    selection = ToolSelection(
        step_id="write",
        tool_name="file_writer",
        reason="capability_match",
        input_metadata=ToolInputMetadata.from_mapping("file_writer", {"file_path": "app.py"}),
        depends_on=["generate"],
    )

    resolved = helper.resolve_selection_metadata(
        selection,
        {
            "generate": ToolResultMetadata(
                tool_name="code_generator",
                status=ResultStatus.SUCCESS,
                result=CodeArtifactMetadata(code="print('from step')", language="python"),
            )
        },
    )

    assert resolved.input_metadata.file_path == "app.py"
    assert resolved.input_metadata.content == "print('from step')"


def test_tool_io_routes_code_unit_to_patch_writer_not_file_writer() -> None:
    helper = ExecutionToolIO()
    generated = ToolResultMetadata(
        tool_name="code_unit_generator",
        status=ResultStatus.SUCCESS,
        result=CodeArtifactMetadata(
            code="def added():\n    return 2",
            language="python",
            attributes={"operation_kind": "add_symbol", "symbol_name": "added"},
        ),
    )

    patch_metadata = helper.resolve_chained_metadata(
        "file_patch_writer",
        ToolInputMetadata.from_mapping("file_patch_writer", {"file_path": "app.py"}),
        last_output=generated,
        last_code_output=generated,
    )
    writer_metadata = helper.resolve_chained_metadata(
        "file_writer",
        ToolInputMetadata.from_mapping("file_writer", {"file_path": "app.py"}),
        last_output=generated,
        last_code_output=generated,
    )

    assert patch_metadata.generated_unit == "def added():\n    return 2"
    assert patch_metadata.operation_kind == "add_symbol"
    assert writer_metadata.content is None


def test_intelligent_autopilot_tool_io_proxy_matches_helper(tmp_path) -> None:
    autopilot = IntelligentAutopilot(FakeLLM(), log_file=tmp_path / "autopilot.jsonl")
    helper = ExecutionToolIO()
    tool = ToolDefinition(
        name="demo",
        display_name="Demo",
        description="Demo tool",
        contract_metadata=ToolContractMetadata(
            tool_name="demo",
            input_metadata_type="ToolInputMetadata",
            output_metadata_type="ToolResultMetadata",
            required_input_fields=["query"],
        ),
    )

    assert autopilot._sanitize_tool_metadata({"content": "abc"}) == helper.sanitize_tool_metadata({"content": "abc"})
    assert autopilot._format_tools_for_llm([tool]) == helper.format_tools_for_llm([tool])
    assert "Need Catalog" in autopilot._format_planning_surface(
        [tool],
        task_description="Inspect the demo file",
        goal="analyze demo",
    )
    assert autopilot._map_reason_to_enum("best performance") == "best_performance"
    assert autopilot.memory_context_builder is not None
    assert autopilot.iterative_improvement.memory_context_builder is autopilot.memory_context_builder


def test_contextual_code_generator_executor_accepts_tool_input_metadata(tmp_path) -> None:
    llm = FakeLLM()
    autopilot = IntelligentAutopilot(llm, log_file=tmp_path / "autopilot.jsonl")
    executor = autopilot.tool_registry.get_executor("code_generator")

    result = executor(
        ToolInputMetadata.from_mapping(
            "code_generator",
            {"task_description": "write hello world", "language": "python"},
        )
    )

    assert isinstance(result, ToolResultMetadata)
    assert isinstance(result.result, CodeArtifactMetadata)
    assert "print('ok')" in result.result.code
    assert llm.requests


def test_contextual_code_generator_preserves_llm_timeout_for_retry_classification(tmp_path) -> None:
    llm = TimeoutLLM()
    autopilot = IntelligentAutopilot(llm, log_file=tmp_path / "autopilot.jsonl")
    executor = autopilot.tool_registry.get_executor("code_generator")

    with pytest.raises(LLMTimeoutError, match="provider read timed out"):
        executor(
            ToolInputMetadata.from_mapping(
                "code_generator",
                {"task_description": "write hello world", "language": "python"},
            )
        )

    assert llm.requests[0].timeout_seconds == CODE_GENERATION_LLM_TIMEOUT_SECONDS
    assert llm.requests[0].transport_retries == 0


def test_contextual_code_generator_explicit_local_fallback_bypasses_unavailable_llm(tmp_path) -> None:
    llm = TimeoutLLM()
    autopilot = IntelligentAutopilot(llm, log_file=tmp_path / "autopilot.jsonl")
    executor = autopilot.tool_registry.get_executor("code_generator")

    result = executor(
        ToolInputMetadata.from_mapping(
            "code_generator",
            {
                "task_description": "build a runnable scaffold",
                "language": "python",
                "prompt_context": {"local_fallback_after_provider_failure": True},
            },
        )
    )

    assert isinstance(result, ToolResultMetadata)
    assert isinstance(result.result, CodeArtifactMetadata)
    assert result.result.attributes["generation_mode"] == "local_fallback"
    assert "deterministic local fallback scaffold" in result.result.attributes["warning"]
    assert "def main" in result.result.code
    assert llm.requests == []


def test_fast_tool_code_generator_uses_metadata_without_mapping_error(tmp_path) -> None:
    autopilot = IntelligentAutopilot(FakeLLM(), log_file=tmp_path / "autopilot.jsonl")
    autopilot.enhanced_ui = FakeUI()
    task = Task(id="task", description="Generate hello world", priority=TaskPriority.HIGH)

    result = autopilot._execute_fast_tool(
        task=task,
        step_id="test_code_generator",
        tool_name="code_generator",
        input_metadata=ToolInputMetadata.from_mapping(
            "code_generator",
            {"task_description": "write hello world", "language": "python"},
        ),
    )

    assert result.success is True
    assert isinstance(result.output, CodeArtifactMetadata)
    assert "print('ok')" in result.output.code
    assert result.call_id.startswith("task:test_code_generator:")
    assert result.tool_context.call_id == result.call_id
    assert result.tool_context.attributes["execution_route"] == "fast_registry"
    assert [event.event_type for event in result.tool_events] == ["pending", "running", "completed"]
    assert [event["event_type"] for event in autopilot.enhanced_ui.events] == ["pending", "running", "completed"]


@pytest.mark.parametrize(
    ("tool_name", "payload"),
    [
        (
            "file_writer",
            {
                "operation_kind": "file_replace",
                "content": "def value():\n    return 2\n",
                "overwrite": True,
            },
        ),
        (
            "file_patch_writer",
            {
                "operation_kind": "modify_symbol",
                "symbol_name": "value",
                "replacement_text": "def value():\n    return 2",
            },
        ),
    ],
)
def test_fast_mutation_rejects_write_under_read_only_root(tmp_path, tool_name, payload) -> None:
    target = tmp_path / "app.py"
    original = "def value():\n    return 1\n"
    target.write_text(original, encoding="utf-8")
    autopilot = IntelligentAutopilot(FakeLLM(), log_file=tmp_path / "autopilot.jsonl")
    autopilot.runtime_controller.state = RuntimeStateMetadata(
        goal="Inspect the project without changing files",
        execution_mode=RuntimeExecutionMode.READ_ONLY,
    )
    task = Task(
        id=f"read-only-{tool_name}",
        description="Inspect app.py without mutation",
        kind="inspect",
        write_files=[],
        priority=TaskPriority.HIGH,
    )

    result = autopilot._execute_fast_tool(
        task=task,
        step_id=f"blocked_{tool_name}",
        tool_name=tool_name,
        input_metadata=ToolInputMetadata.from_mapping(
            tool_name,
            {"file_path": str(target), **payload},
        ),
    )

    assert result.success is False
    assert target.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("declared_targets", "requested_name"),
    [
        ([], "app.py"),
        (["allowed.py"], "outside.py"),
    ],
)
def test_fast_mutation_rejects_missing_or_out_of_scope_task_target(
    tmp_path,
    declared_targets,
    requested_name,
) -> None:
    requested = tmp_path / requested_name
    autopilot = IntelligentAutopilot(FakeLLM(), log_file=tmp_path / "autopilot.jsonl")
    task = Task(
        id=f"scoped-{requested_name}",
        description="Create the declared implementation file",
        kind="implement",
        write_files=[str(tmp_path / name) for name in declared_targets],
        priority=TaskPriority.HIGH,
    )

    result = autopilot._execute_fast_tool(
        task=task,
        step_id="scoped_file_writer",
        tool_name="file_writer",
        input_metadata=ToolInputMetadata.from_mapping(
            "file_writer",
            {
                "file_path": str(requested),
                "content": "value = 1\n",
                "operation_kind": "create_file",
            },
        ),
    )

    assert result.success is False
    assert not requested.exists()


def test_fast_mutation_cannot_succeed_without_observed_target_diff(tmp_path) -> None:
    target = tmp_path / "app.py"
    unchanged = "value = 1\n"
    target.write_text(unchanged, encoding="utf-8")
    autopilot = IntelligentAutopilot(FakeLLM(), log_file=tmp_path / "autopilot.jsonl")
    observed = []
    autopilot.runtime_controller = SimpleNamespace(
        state=None,
        replay_tool_result=lambda *_args: None,
        prepare_tool_call=lambda *_args: True,
        observe_tool_result=lambda _call, _selection, execution: observed.append(execution) or True,
    )
    task = Task(
        id="no-observed-diff",
        description="Update app.py",
        kind="implement",
        write_files=[str(target)],
        priority=TaskPriority.HIGH,
    )

    result = autopilot._execute_fast_tool(
        task=task,
        step_id="unchanged_file_writer",
        tool_name="file_writer",
        input_metadata=ToolInputMetadata.from_mapping(
            "file_writer",
            {
                "file_path": str(target),
                "content": unchanged,
                "operation_kind": "file_replace",
                "overwrite": True,
            },
        ),
    )

    assert result.success is False
    assert len(observed) == 1
    assert observed[0].success is False
    assert observed[0].error.error_type == "NoObservedFileMutation"
    assert target.read_text(encoding="utf-8") == unchanged


def test_fast_tool_failure_emits_error_event_with_recoverable_flag(tmp_path) -> None:
    autopilot = IntelligentAutopilot(FakeLLM(), log_file=tmp_path / "autopilot.jsonl")
    autopilot.enhanced_ui = FakeUI()
    task = Task(id="task", description="Run missing tool", priority=TaskPriority.HIGH)

    result = autopilot._execute_fast_tool(
        task=task,
        step_id="missing_step",
        tool_name="missing_tool",
        input_metadata=ToolInputMetadata.from_mapping("missing_tool", {}),
    )

    assert result.success is False
    assert result.call_id.startswith("task:missing_step:")
    assert [event.event_type for event in result.tool_events] == ["pending", "running", "error"]
    assert result.tool_events[-1].recoverable == bool(result.failure.recoverable)
    assert autopilot.enhanced_ui.events[-1]["event_type"] == "error"


def test_fast_tool_success_persists_exactly_one_called_and_succeeded_event(tmp_path) -> None:
    recorder = DiagnosticRecorder(tmp_path / "diagnostics")
    hooks = RuntimeDiagnosticsHooks(recorder)
    autopilot = IntelligentAutopilot(
        FakeLLM(),
        log_file=tmp_path / "autopilot.jsonl",
        runtime_diagnostics_hooks=hooks,
    )
    autopilot.session_id = "session-fast-success"
    task = Task(id="task-fast-success", description="Generate hello world", priority=TaskPriority.HIGH)
    run = recorder.ensure_run(str(task.id), session_id=autopilot.session_id)

    result = autopilot._execute_fast_tool(
        task=task,
        step_id="generate",
        tool_name="code_generator",
        input_metadata=ToolInputMetadata.from_mapping(
            "code_generator",
            {"task_description": "write hello world", "language": "python"},
        ),
    )

    events = [
        event
        for event in recorder.load_trajectory_events(run.run_id)
        if event["event_type"] in {"tool_called", "tool_succeeded", "tool_failed"}
    ]
    assert result.success is True
    assert [event["event_type"] for event in events] == ["tool_called", "tool_succeeded"]
    assert all(event["payload"]["correlation"]["execution_id"] == result.call_id for event in events)


def test_fast_tool_failure_persists_exactly_one_called_and_failed_event(tmp_path) -> None:
    recorder = DiagnosticRecorder(tmp_path / "diagnostics")
    hooks = RuntimeDiagnosticsHooks(recorder)
    autopilot = IntelligentAutopilot(
        FakeLLM(),
        log_file=tmp_path / "autopilot.jsonl",
        runtime_diagnostics_hooks=hooks,
    )
    autopilot.session_id = "session-fast-failure"
    task = Task(id="task-fast-failure", description="Run missing tool", priority=TaskPriority.HIGH)
    run = recorder.ensure_run(str(task.id), session_id=autopilot.session_id)

    result = autopilot._execute_fast_tool(
        task=task,
        step_id="missing",
        tool_name="missing_tool",
        input_metadata=ToolInputMetadata.from_mapping("missing_tool", {}),
    )

    events = [
        event
        for event in recorder.load_trajectory_events(run.run_id)
        if event["event_type"] in {"tool_called", "tool_succeeded", "tool_failed"}
    ]
    assert result.success is False
    assert [event["event_type"] for event in events] == ["tool_called", "tool_failed"]
    assert all(event["payload"]["correlation"]["execution_id"] == result.call_id for event in events)


def test_fast_tool_retry_history_does_not_inflate_logical_durable_events(tmp_path, monkeypatch) -> None:
    recorder = DiagnosticRecorder(tmp_path / "diagnostics")
    autopilot = IntelligentAutopilot(
        FakeLLM(),
        log_file=tmp_path / "autopilot.jsonl",
        runtime_diagnostics_hooks=RuntimeDiagnosticsHooks(recorder),
    )
    autopilot.session_id = "session-fast-retry"
    task = Task(id="task-fast-retry", description="Generate after a retry", priority=TaskPriority.HIGH)
    run = recorder.ensure_run(str(task.id), session_id=autopilot.session_id)
    output = ToolResultMetadata(
        tool_name="code_generator",
        status=ResultStatus.SUCCESS,
        result=CodeArtifactMetadata(code="print('ok')", language="python"),
    )
    execution = SimpleNamespace(
        success=True,
        error=None,
        status="success",
        output_metadata=output,
        duration_seconds=0.1,
        attempt_number=2,
        retry_count=1,
    )
    monkeypatch.setattr(
        autopilot,
        "_execute_tool_with_fast_retry",
        lambda _selection: (
            execution,
            [{"attempt": 1, "success": False, "error_type": "TransientError"}],
        ),
    )

    result = autopilot._execute_fast_tool(
        task=task,
        step_id="generate",
        tool_name="code_generator",
        input_metadata=ToolInputMetadata.from_mapping(
            "code_generator",
            {"task_description": "write hello world", "language": "python"},
        ),
    )

    events = [
        event
        for event in recorder.load_trajectory_events(run.run_id)
        if event["event_type"] in {"tool_called", "tool_succeeded", "tool_failed"}
    ]
    assert result.attempts_used == 2
    assert result.retry_count == 1
    assert result.retry_history == [{"attempt": 1, "success": False, "error_type": "TransientError"}]
    assert [event["event_type"] for event in events] == ["tool_called", "tool_succeeded"]


def test_fast_tool_diagnostics_hook_failures_do_not_change_execution(tmp_path) -> None:
    class ThrowingHooks(RuntimeDiagnosticsHooks):
        def __init__(self) -> None:
            super().__init__(DiagnosticRecorder(tmp_path / "throwing-diagnostics"))
            self.started = 0
            self.completed = 0

        def on_tool_started(self, **_kwargs) -> None:
            self.started += 1
            raise RuntimeError("diagnostics start failed")

        def on_tool_completed(self, **_kwargs) -> None:
            self.completed += 1
            raise RuntimeError("diagnostics terminal failed")

        def on_tool_failed(self, _error) -> None:
            raise AssertionError("successful execution must not report tool_failed")

    llm = FakeLLM()
    hooks = ThrowingHooks()
    autopilot = IntelligentAutopilot(
        llm,
        log_file=tmp_path / "autopilot.jsonl",
        runtime_diagnostics_hooks=hooks,
    )

    result = autopilot._execute_fast_tool(
        task=Task(id="task-hook-failure", description="Generate once", priority=TaskPriority.HIGH),
        step_id="generate",
        tool_name="code_generator",
        input_metadata=ToolInputMetadata.from_mapping(
            "code_generator",
            {"task_description": "write hello world", "language": "python"},
        ),
    )

    assert result.success is True
    assert len(llm.requests) == 1
    assert hooks.started == 1
    assert hooks.completed == 1


def test_repeated_fast_tool_step_gets_distinct_invocation_ids(tmp_path) -> None:
    recorder = DiagnosticRecorder(tmp_path / "diagnostics")
    autopilot = IntelligentAutopilot(
        FakeLLM(),
        log_file=tmp_path / "autopilot.jsonl",
        runtime_diagnostics_hooks=RuntimeDiagnosticsHooks(recorder),
    )
    autopilot.session_id = "session-repeated-fast-step"
    task = Task(id="task-repeated-fast-step", description="Generate twice", priority=TaskPriority.HIGH)
    run = recorder.ensure_run(str(task.id), session_id=autopilot.session_id)
    tool_input = ToolInputMetadata.from_mapping(
        "code_generator",
        {"task_description": "write hello world", "language": "python"},
    )

    first = autopilot._execute_fast_tool(
        task=task,
        step_id="generate",
        tool_name="code_generator",
        input_metadata=tool_input,
    )
    second = autopilot._execute_fast_tool(
        task=task,
        step_id="generate",
        tool_name="code_generator",
        input_metadata=tool_input,
    )

    events = [
        event
        for event in recorder.load_trajectory_events(run.run_id)
        if event["event_type"] in {"tool_called", "tool_succeeded", "tool_failed"}
    ]
    assert first.call_id != second.call_id
    assert [event["event_type"] for event in events] == [
        "tool_called",
        "tool_succeeded",
        "tool_called",
        "tool_succeeded",
    ]
