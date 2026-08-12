from __future__ import annotations

from types import SimpleNamespace

import pytest

from autonomous_iteration.runtime_controller import StateUpdater
from autonomous_iteration.task_models import Task
from core.llm import (
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
    LLMToolFunction,
    LLMToolFunctionCall,
)
from core.provider_mutation_roundtrip import (
    ProviderMutationRoundTripError,
    ProviderMutationRoundTripRunner,
)
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import RuntimeStateMetadata, ToolLoopMetadata


class _Counter:
    available = True
    tokenizer_id = "test-tokenizer"
    model = "test-model"

    def count_text(self, text: str) -> int:
        return len(text)


class _LLM:
    def __init__(self, responses):
        self.settings = SimpleNamespace(
            context_max_prompt_tokens=4096,
            context_reserved_prompt_tokens=0,
            model="test-model",
        )
        self.responses = list(responses)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class _Owner:
    def __init__(self, runtime):
        self.runtime = runtime

    def _session_id(self):
        return "session-mutation-roundtrip"

    def _reasoning_policy_for_task(self, _task):
        return None

    def _log(self, *args, **kwargs):
        return None


def _call(name: str, call_id: str, arguments: str) -> LLMToolCall:
    return LLMToolCall(
        id=call_id,
        function=LLMToolFunctionCall(name=name, arguments=arguments),
    )


def _tool(name: str) -> LLMToolDefinition:
    return LLMToolDefinition(
        function=LLMToolFunction(
            name=name,
            description=name,
            parameters={"type": "object"},
        )
    )


def _runtime(llm):
    return SimpleNamespace(
        llm_client=llm,
        runtime_controller=SimpleNamespace(
            state=RuntimeStateMetadata(goal="mutate app"),
            state_updater=StateUpdater(),
        ),
        tool_registry=SimpleNamespace(),
    )


def _loop_result(provider_call_id: str, tool: str, input_metadata: dict, result: dict):
    return ToolEventLoopRunResult(
        success=True,
        tool_results=[
            {
                "provider_call_id": provider_call_id,
                "success": True,
                "tool": tool,
                "input_metadata": input_metadata,
                "result": result,
            }
        ],
        last_output=None,
        loop_metadata=ToolLoopMetadata(
            session_id="session-mutation-roundtrip",
            task_id="task",
            status="completed",
            success=True,
        ),
    )


def test_mutation_roundtrip_requires_explicit_authority(monkeypatch):
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _Counter(),
    )
    with pytest.raises(ProviderMutationRoundTripError, match="opt-in"):
        ProviderMutationRoundTripRunner(
            _Owner(_runtime(_LLM([]))),
            Task(id="task", description="mutate app"),
            tools=[_tool("file_patch_writer")],
            write_scope=["app.py"],
            validation_command="python -m py_compile app.py",
            authorized_post_processing_write_scope=["app.py.index.json"],
        )


def test_mutation_roundtrip_stops_after_admitted_patch_for_validation_follow_up(monkeypatch):
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _Counter(),
    )
    mutation_response = LLMResponse(
        content="",
        tool_calls=[
            _call(
                "file_patch_writer",
                "provider-patch-1",
                '{"file_path":"app.py","operation_kind":"replace_file","patch":"*** Begin Patch"}',
            )
        ],
        model="test-model",
        provider="test-provider",
        finish_reason="tool_calls",
    )
    llm = _LLM([mutation_response])
    runner = ProviderMutationRoundTripRunner(
        _Owner(_runtime(llm)),
        Task(id="task", description="mutate app"),
        tools=[_tool("file_patch_writer"), _tool("command_executor")],
        write_scope=["app.py"],
        validation_command="python -m py_compile app.py",
        authorized_post_processing_write_scope=["app.py.index.json"],
        user_confirmed=True,
        allow_mutations=True,
        max_rounds=1,
    )

    monkeypatch.setattr(
        "core.provider_mutation_roundtrip.partition_provider_tool_calls",
        lambda calls, **_kwargs: SimpleNamespace(new_calls=tuple(calls), duplicate_blocks=()),
    )
    monkeypatch.setattr(
        "core.provider_mutation_roundtrip.admit_provider_tool_calls",
        lambda calls, **_kwargs: [
            SimpleNamespace(
                status="admitted",
                provider_call_id=calls[0].id,
                tool_call=SimpleNamespace(tool_name=calls[0].function.name),
            )
        ],
    )
    mutation = SimpleNamespace(
        loop_result=_loop_result(
            "provider-patch-1",
            "file_patch_writer",
            {"file_path": "app.py", "operation_kind": "replace_file"},
            {"bytes_written": 1, "attributes": {"changed_ranges": []}},
        ),
        receipt={"status": "mutation_applied", "file_path": "app.py"},
        wire_messages=(),
    )
    monkeypatch.setattr(
        "core.provider_mutation_roundtrip.execute_provider_mutation_round",
        lambda *_args, **_kwargs: mutation,
    )

    result = runner.run([LLMMessage(role="user", content="Apply the patch")])

    assert result.completed is True
    assert result.mutation is mutation
    assert result.error_message is None
    assert result.rounds_used == 1
    assert len(result.attempts) == 1
    assert llm.requests[0].tool_choice == "required"
