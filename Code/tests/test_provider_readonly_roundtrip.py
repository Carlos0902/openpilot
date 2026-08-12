from __future__ import annotations

from types import SimpleNamespace

import pytest

from autonomous_iteration.runtime_controller import StateUpdater
from autonomous_iteration.task_models import Task
from core.llm import LLMMessage, LLMResponse, LLMToolCall, LLMToolFunctionCall
from core.provider_readonly_roundtrip import (
    ProviderReadonlyRoundTripRunner,
    ProviderReadonlyRoundTripResult,
)
from core.provider_tool_definitions import build_provider_tool_definitions
from core.tool_contracts import PermissionLevel, ToolCapability, ToolDefinition
from metadata import (
    ResultStatus,
    RuntimeStateMetadata,
    TextArtifactMetadata,
    ToolContractMetadata,
    ToolResultMetadata,
)
from tools.tool_registry import ToolRegistry


class _TokenCounter:
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


class _Executor:
    def __init__(self):
        self.calls = []

    def execute_single(self, selection, context=None):
        self.calls.append(selection)
        return SimpleNamespace(
            success=True,
            output_metadata=ToolResultMetadata(
                tool_name=selection.tool_name,
                status=ResultStatus.SUCCESS,
                result=TextArtifactMetadata(content="README contents"),
            ),
            error=None,
        )


class _Owner:
    def __init__(self, runtime):
        self.runtime = runtime

    def _session_id(self):
        return "session-readonly-roundtrip"

    def _log(self, *args, **kwargs):
        return None

    def _show_tool_running(self, *args, **kwargs):
        return None

    def _show_tool_result(self, *args, **kwargs):
        return None

    def _log_tool_start(self, *args, **kwargs):
        return None

    def _log_tool_complete(self, *args, **kwargs):
        return None

    def _summarize_metadata_output(self, value):
        return value

    def _reasoning_policy_for_task(self, _task):
        return None


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="file_reader",
            display_name="file_reader",
            description="Read a file",
            capabilities=[ToolCapability.FILE_READ],
            permission_level=PermissionLevel.LOW,
            contract_metadata=ToolContractMetadata(
                tool_name="file_reader",
                input_metadata_type="ToolInputMetadata",
                output_metadata_type="ToolResultMetadata",
                required_input_fields=["file_path"],
            ),
        ),
        lambda _input: None,
    )
    return registry


def _runtime(llm, executor):
    controller = SimpleNamespace(
        state=RuntimeStateMetadata(goal="Read README"),
        state_updater=StateUpdater(),
        replay_tool_result=lambda *_args: None,
        prepare_tool_call=lambda *_args: True,
        observe_tool_result=lambda *_args: True,
    )
    return SimpleNamespace(
        llm_client=llm,
        tool_registry=_registry(),
        tool_executor=executor,
        runtime_controller=controller,
        runtime_diagnostics_hooks=None,
        enhanced_ui=None,
        logger=SimpleNamespace(log_event=lambda *args, **kwargs: None),
        _project_environments={},
        _sanitize_tool_metadata=lambda value: value,
    )


def _tool_response(call_id="provider-read-1"):
    return LLMResponse(
        content="",
        tool_calls=[
            LLMToolCall(
                id=call_id,
                function=LLMToolFunctionCall(
                    name="file_reader",
                    arguments='{"file_path":"README.md"}',
                ),
            )
        ],
        model="test-model",
        provider="test-provider",
        finish_reason="tool_calls",
    )


def test_readonly_roundtrip_executes_one_read_and_continues(monkeypatch):
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(),
            LLMResponse(
                content="README inspected.",
                model="test-model",
                provider="test-provider",
                finish_reason="stop",
            ),
        ]
    )
    executor = _Executor()
    runtime = _runtime(llm, executor)
    runner = ProviderReadonlyRoundTripRunner(
        _Owner(runtime),
        Task(id="task-readonly-roundtrip", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        read_scope=["README.md"],
        max_rounds=2,
    )

    result = runner.run([LLMMessage(role="user", content="Read README.md")])

    assert isinstance(result, ProviderReadonlyRoundTripResult)
    assert result.success is True, result.error_message
    assert len(executor.calls) == 1
    assert len(llm.requests) == 2
    assert llm.requests[0].tool_choice == "required"
    assert llm.requests[1].messages[-1].tool_call_id == "provider-read-1"


def test_readonly_roundtrip_rejects_mutation_tools_at_entry():
    runtime = _runtime(_LLM([]), _Executor())
    with pytest.raises(ValueError, match="read-only"):
        ProviderReadonlyRoundTripRunner(
            _Owner(runtime),
            Task(id="task-readonly-mutation", description="write"),
            tools=[],
            allow_mutations=True,
        )


def test_readonly_roundtrip_reports_empty_provider_response(monkeypatch):
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            LLMResponse(
                content="",
                model="test-model",
                provider="test-provider",
                finish_reason="stop",
            )
        ]
    )
    runtime = _runtime(llm, _Executor())
    result = ProviderReadonlyRoundTripRunner(
        _Owner(runtime),
        Task(id="task-empty-response", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=1,
    ).run([LLMMessage(role="user", content="Read README.md")])

    assert result.success is False
    assert result.error_message == "ProviderToolEmptyResponse"
    assert result.rounds_used == 1
