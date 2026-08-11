from __future__ import annotations

from types import SimpleNamespace

from autonomous_iteration.runtime_controller import StateUpdater
from autonomous_iteration.task_models import Task
from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_tool_batch_admission import admit_provider_tool_calls
from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
)
from core.tool_event_loop import ToolEventLoopRunner
from metadata import (
    FailureMetadata,
    ResultStatus,
    RuntimeStateMetadata,
    TextArtifactMetadata,
    ToolContractMetadata,
    ToolResultMetadata,
)
from tools.tool_registry import ToolRegistry


class _Owner:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.logs = []

    def _session_id(self) -> str:
        return "session"

    def _log(self, *args, **kwargs) -> None:
        self.logs.append((args, kwargs))

    def _show_tool_running(self, *args, **kwargs) -> None:
        return None

    def _show_tool_result(self, *args, **kwargs) -> None:
        return None

    def _log_tool_start(self, *args, **kwargs) -> None:
        return None

    def _log_tool_complete(self, *args, **kwargs) -> None:
        return None

    def _summarize_metadata_output(self, output_metadata):
        return output_metadata


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    definition = ToolDefinition(
        name="file_reader",
        display_name="file_reader",
        description="test provider reader",
        capabilities=[ToolCapability.FILE_READ],
        permission_level=PermissionLevel.LOW,
        contract_metadata=ToolContractMetadata(
            tool_name="file_reader",
            input_metadata_type="ToolInputMetadata",
            output_metadata_type="ToolResultMetadata",
            required_input_fields=["file_path"],
            capabilities=[ToolCapability.FILE_READ.value],
            permission_level=PermissionLevel.LOW.value,
        ),
    )
    registry.register(definition, lambda _input: None)
    return registry


def _call(arguments: str = '{"file_path":"README.md"}') -> LLMToolCall:
    return LLMToolCall(
        id="provider-call-1",
        function=LLMToolFunctionCall(
            name="file_reader",
            arguments=arguments,
        ),
    )


def _result(*, success: bool = True):
    if success:
        return SimpleNamespace(
            success=True,
            output_metadata=ToolResultMetadata(
                tool_name="file_reader",
                status=ResultStatus.SUCCESS,
                result=TextArtifactMetadata(content="ok"),
            ),
            error=None,
        )
    return SimpleNamespace(
        success=False,
        output_metadata=None,
        error=FailureMetadata(
            error_type="SyntheticFailure",
            error_message="synthetic provider execution failure",
            recoverable=True,
            retry_recommended=True,
        ),
    )


def _runtime(executor, *, observe=None):
    state = RuntimeStateMetadata(goal="provider read execution")
    controller = SimpleNamespace(
        state=state,
        state_updater=StateUpdater(),
        replay_tool_result=lambda *_args: None,
        prepare_tool_call=lambda *_args: True,
        observe_tool_result=observe or (lambda *_args: True),
    )
    return SimpleNamespace(
        tool_registry=_registry(),
        tool_executor=executor,
        runtime_controller=controller,
        runtime_diagnostics_hooks=None,
        _project_environments={},
    )


def _admissions(runtime, *, task_id: str, call: LLMToolCall | None = None):
    return admit_provider_tool_calls(
        [call or _call()],
        task_id=task_id,
        session_id="session",
        round_index=1,
        registry=runtime.tool_registry,
        budget=runtime.runtime_controller.state.budget,
        read_scope=["README.md"],
    )


def test_readonly_bridge_executes_admitted_call_and_accounts_state() -> None:
    executed = []

    class Executor:
        def execute_single(self, selection, context=None):
            executed.append(selection)
            return _result()

    runtime = _runtime(Executor())
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_tool_calls(
        Task(id="task-1", description="read"),
        _admissions(runtime, task_id="task-1"),
    )

    assert result.success is True
    assert len(executed) == 1
    assert result.loop_metadata.provider_executed is True
    assert [event.event_type for event in result.loop_metadata.events] == [
        "pending",
        "running",
        "completed",
    ]
    assert all(event.provider_executed for event in result.loop_metadata.events)
    assert result.tool_results[0]["provider_call_id"] == "provider-call-1"
    assert runtime.runtime_controller.state.budget.tool_calls_used == 1
    assert runtime.runtime_controller.state.budget.file_reads_used == 1


def test_readonly_bridge_never_executes_blocked_admission() -> None:
    class Executor:
        def execute_single(self, selection, context=None):
            raise AssertionError("blocked provider call must not execute")

    runtime = _runtime(Executor())
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_tool_calls(
        Task(id="task-2", description="read"),
        _admissions(
            runtime,
            task_id="task-2",
            call=_call(arguments='{"file_path":'),
        ),
    )

    assert result.success is False
    assert result.loop_metadata.events[-1].event_type == "error"
    error = result.loop_metadata.events[-1].tool_error
    assert error.provider_call_id == "provider-call-1"
    assert result.loop_metadata.final_error.details["provider_call_id"] == (
        "provider-call-1"
    )
    assert runtime.runtime_controller.state.budget.tool_calls_used == 0


def test_readonly_bridge_observation_failure_does_not_apply_state() -> None:
    runtime = _runtime(
        SimpleNamespace(execute_single=lambda *_args, **_kwargs: _result()),
        observe=lambda *_args: False,
    )
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_tool_calls(
        Task(id="task-3", description="read"),
        _admissions(runtime, task_id="task-3"),
    )

    assert result.success is False
    assert result.loop_metadata.final_error.error_type == (
        "CheckpointObservationFailed"
    )
    assert result.loop_metadata.final_error.details["provider_call_id"] == (
        "provider-call-1"
    )
    assert runtime.runtime_controller.state.budget.tool_calls_used == 0


def test_readonly_bridge_preserves_provider_id_on_execution_failure() -> None:
    runtime = _runtime(
        SimpleNamespace(
            execute_single=lambda *_args, **_kwargs: _result(success=False)
        )
    )
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_tool_calls(
        Task(id="task-4", description="read"),
        _admissions(runtime, task_id="task-4"),
    )

    assert result.success is False
    error = result.loop_metadata.recoverable_errors[0]
    assert error.provider_call_id == "provider-call-1"
    assert error.failure.details["provider_call_id"] == "provider-call-1"
    assert runtime.runtime_controller.state.budget.tool_calls_used == 1
