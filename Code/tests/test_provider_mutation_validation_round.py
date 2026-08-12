from __future__ import annotations

from types import SimpleNamespace

import pytest

from autonomous_iteration.runtime_controller import StateUpdater
from autonomous_iteration.task_models import Task
from core.llm import LLMMessage, LLMResponse, LLMToolCall, LLMToolDefinition, LLMToolFunction, LLMToolFunctionCall
from core.provider_mutation_round_execution import ProviderMutationRoundResult
from core.provider_mutation_validation_round import (
    ProviderMutationValidationRoundError,
    ProviderMutationValidationRoundRunner,
)
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import RuntimeStateMetadata, ToolLoopMetadata


class _LLM:
    def __init__(self, responses):
        self.settings = SimpleNamespace(context_max_prompt_tokens=4096, model="test-model")
        self.responses = list(responses)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class _Owner:
    def __init__(self, runtime):
        self.runtime = runtime

    def _session_id(self):
        return "session-validation"

    def _reasoning_policy_for_task(self, _task):
        return None

    def _log(self, *args, **kwargs):
        return None


def _tool(name: str) -> LLMToolDefinition:
    return LLMToolDefinition(function=LLMToolFunction(name=name, parameters={"type": "object"}))


def _loop(
    success: bool,
    command: str,
    exit_code: int,
    provider_call_id: str = "provider-validation",
) -> ToolEventLoopRunResult:
    return ToolEventLoopRunResult(
        success=success,
        tool_results=[
            {
                "provider_call_id": provider_call_id,
                "tool": "command_executor",
                "success": success,
                "input_metadata": {"requested_command": command, "command": command},
                "result": {"success": success, "exit_code": exit_code},
            }
        ],
        last_output=None,
        loop_metadata=ToolLoopMetadata(
            session_id="session-validation",
            task_id="task",
            status="completed",
            success=success,
        ),
    )


def _runtime(llm):
    from autonomous_iteration.runtime_controller import StateUpdater
    from metadata import RuntimeStateMetadata

    return SimpleNamespace(
        llm_client=llm,
        runtime_controller=SimpleNamespace(
            state=RuntimeStateMetadata(goal="validate"),
            state_updater=StateUpdater(),
        ),
        tool_registry=SimpleNamespace(),
    )


def _mutation(owner) -> ProviderMutationRoundResult:
    loop = ToolEventLoopRunResult(
        success=True,
        tool_results=[
            {
                "provider_call_id": "provider-patch",
                "tool": "file_patch_writer",
                "success": True,
                "input_metadata": {
                    "file_path": "app.py",
                    "operation_kind": "replace_file",
                },
                "result": {
                    "bytes_written": 1,
                    "attributes": {"changed_ranges": []},
                },
            }
        ],
        last_output=None,
        loop_metadata=ToolLoopMetadata(
            session_id="session-validation",
            task_id="task",
            status="completed",
            success=True,
        ),
    )
    return ProviderMutationRoundResult(
        executed=SimpleNamespace(loop_result=loop, wire_messages=()),
        receipt={
            "status": "mutation_applied",
            "file_path": "app.py",
            "operation_kind": "replace_file",
        },
    )


def test_validation_requires_completed_mutation_and_confirmation():
    llm = _LLM([])
    with pytest.raises(ProviderMutationValidationRoundError, match="completed mutation"):
        ProviderMutationValidationRoundRunner(
            _Owner(_runtime(llm)),
            Task(id="task", description="validate"),
            mutation=object(),
            messages=[LLMMessage(role="user", content="Apply")],
            tools=[_tool("command_executor")],
            validation_command="python -m py_compile app.py",
            user_confirmed=True,
        )


def test_validation_round_projects_success_and_finalization_transition(monkeypatch):
    command = "python -m py_compile app.py"
    response = LLMResponse(
        content="",
        tool_calls=[
            LLMToolCall(
                id="provider-validation",
                function=LLMToolFunctionCall(
                    name="command_executor",
                    arguments=f'{{"command":"{command}","requested_command":"{command}"}}',
                ),
            )
        ],
        model="test-model",
        provider="test-provider",
        finish_reason="tool_calls",
    )
    llm = _LLM([response])
    owner = _Owner(_runtime(llm))
    mutation = _mutation(owner)
    monkeypatch.setattr(
        "core.provider_mutation_validation_round.dispatch_provider_execution_batch",
        lambda *_args, **_kwargs: _loop(True, command, 0),
    )
    result = ProviderMutationValidationRoundRunner(
        owner,
        Task(id="task", description="validate"),
        mutation=mutation,
        messages=[LLMMessage(role="user", content="Apply")],
        tools=[_tool("command_executor")],
        validation_command=command,
        user_confirmed=True,
    ).run()

    assert result.observation.value == "succeeded"
    assert result.transition.finalization_pending is True
    assert result.transition.action.value == "request_finalization"
    assert result.rounds_used == 1


def test_validation_round_projects_failed_exact_command(monkeypatch):
    command = "python -m py_compile app.py"
    response = LLMResponse(
        content="",
        tool_calls=[
            LLMToolCall(
                id="provider-validation-failed",
                function=LLMToolFunctionCall(
                    name="command_executor",
                    arguments=f'{{"command":"{command}","requested_command":"{command}"}}',
                ),
            )
        ],
        model="test-model",
        provider="test-provider",
        finish_reason="tool_calls",
    )
    owner = _Owner(_runtime(_LLM([response])))
    monkeypatch.setattr(
        "core.provider_mutation_validation_round.dispatch_provider_execution_batch",
        lambda *_args, **_kwargs: _loop(
            False,
            command,
            1,
            provider_call_id="provider-validation-failed",
        ),
    )
    result = ProviderMutationValidationRoundRunner(
        owner,
        Task(id="task", description="validate"),
        mutation=_mutation(owner),
        messages=[LLMMessage(role="user", content="Apply")],
        tools=[_tool("command_executor")],
        validation_command=command,
        user_confirmed=True,
    ).run()

    assert result.observation.value == "failed"
    assert result.transition.action.value == "fail"
    assert result.transition.error_code.value == "ProviderToolValidationFailed"
