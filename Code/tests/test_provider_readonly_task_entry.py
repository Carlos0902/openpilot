from __future__ import annotations

from types import SimpleNamespace

from autonomous_iteration.runtime_controller import StateUpdater
from autonomous_iteration.task_models import Task, TaskExecutionContext, TaskStatus
from autonomous_iteration.agents.tool_planning_executor import ToolPlanningTaskExecutor
from core.config import ProviderToolExecutionBudgetProfile
from core.llm import LLMResponse, LLMToolCall, LLMToolFunctionCall
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
            provider_tool_execution_enabled=False,
            provider_tool_execution_budget_profile=ProviderToolExecutionBudgetProfile.CANARY,
            provider_tool_execution_max_rounds=3,
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


def _registry(*, include_writer: bool = False) -> ToolRegistry:
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
    if include_writer:
        registry.register(
            ToolDefinition(
                name="file_writer",
                display_name="file_writer",
                description="Write a file",
                capabilities=[ToolCapability.FILE_WRITE],
                permission_level=PermissionLevel.HIGH,
                contract_metadata=ToolContractMetadata(
                    tool_name="file_writer",
                    input_metadata_type="ToolInputMetadata",
                    output_metadata_type="ToolResultMetadata",
                    required_input_fields=["file_path", "content"],
                ),
            ),
            lambda _input: None,
        )
    return registry


def _runtime(llm, executor, *, include_writer: bool = False):
    controller = SimpleNamespace(
        state=RuntimeStateMetadata(goal="Read README"),
        state_updater=StateUpdater(),
        replay_tool_result=lambda *_args: None,
        prepare_tool_call=lambda *_args: True,
        observe_tool_result=lambda *_args: True,
    )
    return SimpleNamespace(
        llm_client=llm,
        tool_registry=_registry(include_writer=include_writer),
        tool_executor=executor,
        runtime_controller=controller,
        runtime_diagnostics_hooks=None,
        enhanced_ui=None,
        _sanitize_tool_metadata=lambda value: value,
        logger=SimpleNamespace(
            log_event=lambda *args, **kwargs: None,
            log_structured_event=lambda *args, **kwargs: None,
        ),
        session_id="session-entry",
        _project_environments={},
    )


def _tool_response():
    return LLMResponse(
        content="",
        tool_calls=[
            LLMToolCall(
                id="entry-read-1",
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


def test_provider_readonly_entry_is_default_off(monkeypatch):
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    runtime = _runtime(llm, _Executor())
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(id="entry-disabled", description="Read README", read_files=["README.md"])

    result = executor.execute_provider_readonly_task(
        task,
        TaskExecutionContext(task=task),
        tool_names=["file_reader"],
    )

    assert result.status is TaskStatus.FAILED
    assert result.result_metadata.failure.error_type == "ProviderToolExecutionDisabled"
    assert llm.requests == []


def test_provider_readonly_entry_runs_explicit_canary(monkeypatch):
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(),
            LLMResponse(
                content="Read-only entry completed.",
                model="test-model",
                provider="test-provider",
                finish_reason="stop",
            ),
        ]
    )
    tool_executor = _Executor()
    runtime = _runtime(llm, tool_executor)
    runtime.llm_client.settings.provider_tool_execution_enabled = True
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(id="entry-read", description="Read README", read_files=["README.md"])

    result = executor.execute_provider_readonly_task(
        task,
        TaskExecutionContext(task=task, parent_context={"goal": task.description}),
        tool_names=["file_reader"],
        max_rounds=2,
    )

    assert result.status is TaskStatus.COMPLETED, result.error
    assert result.result_metadata.result.content == "Read-only entry completed."
    assert len(tool_executor.calls) == 1
    assert len(llm.requests) == 2


def test_provider_readonly_entry_rejects_write_scope(monkeypatch):
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    runtime = _runtime(llm, _Executor())
    runtime.llm_client.settings.provider_tool_execution_enabled = True
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(
        id="entry-write-scope",
        description="Modify README",
        read_files=["README.md"],
        write_files=["README.md"],
    )

    result = executor.execute_provider_readonly_task(
        task,
        TaskExecutionContext(task=task),
        tool_names=["file_reader"],
    )

    assert result.status is TaskStatus.FAILED
    assert result.result_metadata.failure.error_type == "ProviderReadonlyWriteScopeNotAllowed"
    assert llm.requests == []


def test_provider_readonly_entry_rejects_mutation_tool(monkeypatch):
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    runtime = _runtime(llm, _Executor(), include_writer=True)
    runtime.llm_client.settings.provider_tool_execution_enabled = True
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(id="entry-writer", description="Write README", read_files=["README.md"])

    result = executor.execute_provider_readonly_task(
        task,
        TaskExecutionContext(task=task),
        tool_names=["file_writer"],
    )

    assert result.status is TaskStatus.FAILED
    assert result.result_metadata.failure.error_type == "ProviderReadonlyMutationTool"
    assert llm.requests == []


def test_provider_readonly_entry_forwards_project_path_for_relative_scope(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    runtime = _runtime(llm, _Executor())
    runtime.llm_client.settings.provider_tool_execution_enabled = True
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(id="entry-project-root", description="Read README", read_files=["README.md"])

    result = executor.execute_provider_readonly_task(
        task,
        TaskExecutionContext(task=task, parent_context={"project_path": str(tmp_path)}),
        tool_names=["file_reader"],
        max_rounds=1,
    )

    # No provider response is supplied; the assertion is about reaching the
    # provider request path with a project-root-aware runner, not completion.
    assert result.status is TaskStatus.FAILED
    assert result.result_metadata.failure.error_type != "ProviderToolTaskSetupFailed"
