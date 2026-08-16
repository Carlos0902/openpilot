from __future__ import annotations

from types import SimpleNamespace

from autonomous_iteration.runtime_controller import StateUpdater
from autonomous_iteration.task_models import Task
from core.provider_tool_admission import admit_provider_tool_calls
from core.tool_contracts import PermissionLevel, ToolCapability, ToolDefinition
from core.tool_event_loop import ToolEventLoopRunner
from core.llm import LLMToolCall, LLMToolFunctionCall
from metadata import (
    FailureMetadata,
    ResultStatus,
    RuntimeBudgetMetadata,
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
        return "session-1"

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


def _call(name: str = "file_reader", arguments: str = '{"file_path":"README.md"}', call_id: str = "ds-call-1"):
    return LLMToolCall(
        id=call_id,
        function=LLMToolFunctionCall(name=name, arguments=arguments),
    )


def _registry(*, tool_name: str = "file_reader", permission: PermissionLevel = PermissionLevel.LOW) -> ToolRegistry:
    registry = ToolRegistry()
    capabilities = [ToolCapability.FILE_READ]
    required = ["file_path"]
    if tool_name == "file_writer":
        capabilities = [ToolCapability.FILE_WRITE]
        required = ["file_path", "content"]
    definition = ToolDefinition(
        name=tool_name,
        display_name=tool_name,
        description="test provider bridge tool",
        capabilities=capabilities,
        permission_level=permission,
        contract_metadata=ToolContractMetadata(
            tool_name=tool_name,
            input_metadata_type="ToolInputMetadata",
            output_metadata_type="ToolResultMetadata",
            required_input_fields=required,
            capabilities=[capability.value for capability in capabilities],
            permission_level=permission.value,
        ),
    )
    registry.register(definition, lambda _input: None)
    return registry


def _result(tool_name: str = "file_reader", *, success: bool = True):
    if success:
        return SimpleNamespace(
            success=True,
            output_metadata=ToolResultMetadata(
                tool_name=tool_name,
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


def _runtime(registry, executor, *, prepare=None, observe=None):
    state = RuntimeStateMetadata(goal="provider tool execution")
    controller = SimpleNamespace(
        state=state,
        state_updater=StateUpdater(),
        replay_tool_result=lambda *_args: None,
        prepare_tool_call=prepare or (lambda *_args: True),
        observe_tool_result=observe or (lambda *_args: True),
    )
    return SimpleNamespace(
        tool_registry=registry,
        tool_executor=executor,
        runtime_controller=controller,
        runtime_diagnostics_hooks=None,
        _project_environments={},
    )


def test_provider_bridge_executes_admitted_call_and_accounts_state() -> None:
    executed = []

    class Executor:
        def execute_single(self, selection, context=None):
            executed.append(selection)
            return _result()

    runtime = _runtime(_registry(), Executor())
    admissions = admit_provider_tool_calls(
        [_call()],
        task_id="task-1",
        session_id="session-1",
        round_index=1,
        registry=runtime.tool_registry,
        budget=runtime.runtime_controller.state.budget,
    )
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_tool_calls(Task(id="task-1", description="read"), admissions)

    assert result.success is True
    assert len(executed) == 1
    assert result.loop_metadata.provider_executed is True
    assert result.loop_metadata.tool_invocations[0].call_id == "task-1:r1:c1"
    assert result.loop_metadata.tool_invocations[0].provider_call_id == "ds-call-1"
    assert [event.event_type for event in result.loop_metadata.events] == ["pending", "running", "completed"]
    assert all(event.provider_executed for event in result.loop_metadata.events)
    assert result.tool_results[0]["provider_call_id"] == "ds-call-1"
    assert runtime.runtime_controller.state.budget.tool_calls_used == 1
    assert runtime.runtime_controller.state.budget.file_reads_used == 1


def test_provider_bridge_never_executes_blocked_admission() -> None:
    class Executor:
        def execute_single(self, selection, context=None):
            raise AssertionError("blocked provider call must not execute")

    runtime = _runtime(_registry(), Executor())
    admissions = admit_provider_tool_calls(
        [_call(arguments='{"file_path":')],
        task_id="task-2",
        session_id="session-1",
        round_index=1,
        registry=runtime.tool_registry,
        budget=runtime.runtime_controller.state.budget,
    )
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_tool_calls(Task(id="task-2", description="read"), admissions)

    assert result.success is False
    assert result.loop_metadata.events[-1].event_type == "error"
    assert result.loop_metadata.events[-1].tool_error.provider_call_id == "ds-call-1"
    assert result.loop_metadata.final_error.details["provider_call_id"] == "ds-call-1"
    assert runtime.runtime_controller.state.budget.tool_calls_used == 0


def test_provider_bridge_checkpoint_denial_prevents_mutation() -> None:
    executed = []

    class Executor:
        def execute_single(self, selection, context=None):
            executed.append(selection)
            return _result("file_writer")

    runtime = _runtime(
        _registry(tool_name="file_writer", permission=PermissionLevel.MEDIUM),
        Executor(),
        prepare=lambda *_args: False,
    )
    admissions = admit_provider_tool_calls(
        [_call("file_writer", '{"file_path":"note.txt","content":"after"}')],
        task_id="task-3",
        session_id="session-1",
        round_index=1,
        registry=runtime.tool_registry,
        budget=runtime.runtime_controller.state.budget,
        user_confirmed=True,
    )
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_tool_calls(Task(id="task-3", description="write"), admissions)

    assert result.success is False
    assert result.error_message == "Mutation was not executed because its prepared checkpoint was not durable."
    assert result.loop_metadata.final_error.details["provider_call_id"] == "ds-call-1"
    assert executed == []
    assert runtime.runtime_controller.state.budget.tool_calls_used == 0


def test_provider_bridge_observation_failure_does_not_apply_state() -> None:
    runtime = _runtime(_registry(), SimpleNamespace(execute_single=lambda *_args, **_kwargs: _result()), observe=lambda *_args: False)
    admissions = admit_provider_tool_calls(
        [_call()],
        task_id="task-4",
        session_id="session-1",
        round_index=1,
        registry=runtime.tool_registry,
        budget=runtime.runtime_controller.state.budget,
    )
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_tool_calls(Task(id="task-4", description="read"), admissions)

    assert result.success is False
    assert result.loop_metadata.final_error.error_type == "CheckpointObservationFailed"
    assert result.loop_metadata.final_error.details["provider_call_id"] == "ds-call-1"
    assert runtime.runtime_controller.state.budget.tool_calls_used == 0


def test_provider_bridge_preserves_provider_id_on_execution_failure() -> None:
    runtime = _runtime(_registry(), SimpleNamespace(execute_single=lambda *_args, **_kwargs: _result(success=False)))
    admissions = admit_provider_tool_calls(
        [_call()],
        task_id="task-5",
        session_id="session-1",
        round_index=1,
        registry=runtime.tool_registry,
        budget=runtime.runtime_controller.state.budget,
    )
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_tool_calls(Task(id="task-5", description="read"), admissions)

    assert result.success is False
    assert result.loop_metadata.recoverable_errors[0].provider_call_id == "ds-call-1"
    assert result.loop_metadata.recoverable_errors[0].failure.details["provider_call_id"] == "ds-call-1"
    assert runtime.runtime_controller.state.budget.tool_calls_used == 1
