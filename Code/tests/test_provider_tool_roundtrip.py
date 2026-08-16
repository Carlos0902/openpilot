from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autonomous_iteration.runtime_controller import StateUpdater
from autonomous_iteration.agents.tool_planning_executor import ToolPlanningTaskExecutor
from autonomous_iteration.task_models import Task, TaskExecutionContext, TaskStatus
from core.config import ProviderToolExecutionBudget, ProviderToolExecutionBudgetProfile
from core.llm import (
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
    LLMToolFunction,
    LLMToolFunctionCall,
)
from core.provider_tool_roundtrip import (
    ProviderToolRoundTripRunner,
    build_provider_tool_definitions,
    _module_callsite_candidates,
    _module_callable_candidates,
    _module_import_candidates,
    _module_symbol_candidates,
)
from core.provider_tool_admission import admit_provider_tool_calls
from core.tool_event_loop import ToolEventLoopRunner
from core.tool_contracts import PermissionLevel, ToolCapability, ToolDefinition
from metadata import (
    CodeArtifactMetadata,
    ContextCandidate,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ContextRequestPurpose,
    FailureMetadata,
    FileArtifactMetadata,
    FileReadWindowSpec,
    FileReadWindow,
    ReasoningMode,
    ReasoningPolicy,
    ResultStatus,
    RuntimeBudgetMetadata,
    RuntimeStateMetadata,
    ToolEventCompletionOutcome,
    TextArtifactMetadata,
    ToolContractMetadata,
    ToolResultMetadata,
    UnsupportedReasoningBehavior,
    ToolInputMetadata,
)
from tools.tool_registry import ToolRegistry
from tools.tool_selection import SelectionReason, ToolSelection
from tools.command_tool import COMMAND_EXECUTOR_DEFINITION
from tools.file_patch_writer import FILE_PATCH_WRITER_DEFINITION
from tools.code_unit_generator import (
    CODE_UNIT_GENERATOR_DEFINITION,
    _validate_grounded_python_unit,
)


class _TokenCounter:
    available = True
    tokenizer_id = "test-tokenizer"
    model = "deepseek-v4-flash"

    def count_text(self, text: str) -> int:
        return len(text)


class _LLM:
    def __init__(self, responses) -> None:
        self.settings = SimpleNamespace(
            context_max_prompt_tokens=4096,
            context_reserved_prompt_tokens=128,
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            tokenizer_path="configured",
        )
        self.responses = list(responses)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class _Executor:
    def __init__(self) -> None:
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


class _RecoverableExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute_single(self, selection, context=None):
        self.calls += 1
        offset = int(getattr(selection.input_metadata, "offset", 0) or 0)
        max_lines = getattr(selection.input_metadata, "max_lines", None)
        windowed = offset > 0 or (max_lines is not None and int(max_lines) < 300)
        return SimpleNamespace(
            success=False,
            output_metadata=None,
            error=FailureMetadata(
                error_type="FileReaderDirectoryPath",
                error_message="file_reader expected a file path but received a directory",
                recoverable=True,
                retry_recommended=True,
            ),
        )


class _MixedExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute_single(self, selection, context=None):
        self.calls += 1
        if self.calls == 1:
            return _Executor().execute_single(selection, context)
        return _RecoverableExecutor().execute_single(selection, context)


class _LargeExecutor:
    def execute_single(self, selection, context=None):
        return SimpleNamespace(
            success=True,
            output_metadata=ToolResultMetadata(
                tool_name=selection.tool_name,
                status=ResultStatus.SUCCESS,
                result=TextArtifactMetadata(content="large evidence " * 2_000, title="large.txt"),
            ),
            error=None,
        )


class _CompleteFileExecutor:
    def execute_single(self, selection, context=None):
        return SimpleNamespace(
            success=True,
            output_metadata=ToolResultMetadata(
                tool_name=selection.tool_name,
                status=ResultStatus.SUCCESS,
                result=FileArtifactMetadata(
                    content="complete README",
                    file_path=selection.input_metadata.file_path or "README.md",
                    lines_read=2,
                    total_lines=2,
                    truncated=False,
                ),
            ),
            error=None,
        )


class _LargeCompleteFileExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute_single(self, selection, context=None):
        self.calls += 1
        offset = int(getattr(selection.input_metadata, "offset", 0) or 0)
        max_lines = getattr(selection.input_metadata, "max_lines", None)
        windowed = offset > 0 or (max_lines is not None and int(max_lines) < 300)
        return SimpleNamespace(
            success=True,
            output_metadata=ToolResultMetadata(
                tool_name=selection.tool_name,
                status=ResultStatus.SUCCESS,
                result=FileArtifactMetadata(
                    content=f"source page {self.calls}\n" * 300,
                    file_path=selection.input_metadata.file_path or "README.md",
                    lines_read=300,
                    total_lines=300,
                    truncated=windowed,
                ),
            ),
            error=None,
        )


class _Owner:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def _session_id(self):
        return "session-roundtrip"

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


def _registry(tool_name: str = "file_reader") -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name=tool_name,
            display_name=tool_name,
            description="Read a local file",
            capabilities=[ToolCapability.FILE_READ],
            permission_level=PermissionLevel.LOW,
            contract_metadata=ToolContractMetadata(
                tool_name=tool_name,
                input_metadata_type="ToolInputMetadata",
                output_metadata_type="ToolResultMetadata",
                required_input_fields=["file_path"],
                input_defaults={"read_mode": "full", "encoding": "utf-8"},
            ),
        ),
        lambda _input: None,
    )
    return registry


def _mutation_registry() -> ToolRegistry:
    registry = _registry()
    registry.register(FILE_PATCH_WRITER_DEFINITION, lambda _input: None)
    return registry


def _generator_registry() -> ToolRegistry:
    registry = _registry()
    registry.register(CODE_UNIT_GENERATOR_DEFINITION, lambda _input: None)
    return registry


def _tool_response(*, call_id: str = "ds-read-1") -> LLMResponse:
    return LLMResponse(
        content="",
        reasoning_content="I need to inspect the requested file.",
        tool_calls=[
            LLMToolCall(
                id=call_id,
                function=LLMToolFunctionCall(
                    name="file_reader",
                    arguments='{"file_path":"README.md"}',
                ),
            )
        ],
        model="deepseek-v4-flash",
        provider="deepseek",
        finish_reason="tool_calls",
    )


def _runtime(llm, registry, executor):
    state = RuntimeStateMetadata(goal="Read README")
    runtime = SimpleNamespace(
        llm_client=llm,
        tool_registry=registry,
        tool_executor=executor,
        enhanced_ui=None,
        logger=SimpleNamespace(log_event=lambda *args, **kwargs: None),
        _sanitize_tool_metadata=lambda value: value,
        runtime_controller=SimpleNamespace(
            state=state,
            state_updater=StateUpdater(),
            replay_tool_result=lambda *_args: None,
            prepare_tool_call=lambda *_args: True,
            observe_tool_result=lambda *_args: True,
        ),
        runtime_diagnostics_hooks=None,
        _project_environments={},
    )
    return runtime


def test_roundtrip_runner_preserves_reasoning_and_tool_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(),
            LLMResponse(
                content="README inspected.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    registry = _registry()
    executor = _Executor()
    runtime = _runtime(llm, registry, executor)
    owner = _Owner(runtime)
    tools = build_provider_tool_definitions(registry, ["file_reader"])

    result = ProviderToolRoundTripRunner(
        owner,
        Task(id="task-roundtrip", description="Read README"),
        tools=tools,
        max_rounds=2,
    ).run([LLMMessage(role="user", content="Read README.md")])

    assert result.success is True, result.error_message
    assert len(executor.calls) == 1
    assert len(llm.requests) == 2
    second_messages = llm.requests[1].messages
    assert second_messages[-2].role == "assistant"
    assert second_messages[-2].reasoning_content == "I need to inspect the requested file."
    assert second_messages[-2].tool_calls[0].id == "ds-read-1"
    assert second_messages[-1].role == "tool"
    assert second_messages[-1].tool_call_id == "ds-read-1"
    assert llm.requests[0].tools[0].function.name == "file_reader"
    assert runtime.runtime_controller.state.budget.tool_calls_used == 1


def test_roundtrip_requires_tool_choice_for_tool_phase_requests(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-required-read-1"),
            LLMResponse(
                content="README inspected.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _CompleteFileExecutor())
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-required-tool-choice", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=2,
    ).run([LLMMessage(role="user", content="Read README.md")])

    assert result.success is True, result.error_message
    assert llm.requests[0].tool_choice == "required"
    assert llm.requests[1].tool_choice == "required"
    assert result.request_diagnostics[0]["tool_choice"] == "required"
    assert result.request_diagnostics[1]["tool_choice"] == "required"


def test_roundtrip_allows_explicitly_disabled_deepseek_continuation(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-disabled-read-1").model_copy(
                update={"reasoning_content": None}
            ),
            LLMResponse(
                content="README inspected without provider thinking.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    llm.settings.reasoning_capability_profile = "deepseek-chat-known"
    runtime = _runtime(llm, _registry(), _CompleteFileExecutor())
    owner = _Owner(runtime)
    owner._reasoning_policy_for_task = lambda _task: ReasoningPolicy(
        mode=ReasoningMode.DISABLED,
        unsupported_behavior=UnsupportedReasoningBehavior.PROVIDER_DEFAULT,
    )
    target = tmp_path / "README.md"
    result = ProviderToolRoundTripRunner(
        owner,
        Task(id="task-disabled-deepseek", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=3,
        project_path=str(tmp_path),
        read_scope=[str(target)],
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is True
    assert llm.requests[0].reasoning_policy.mode == ReasoningMode.DISABLED
    assert result.messages[-2].reasoning_content is None


def test_roundtrip_allows_provider_default_deepseek_continuation_without_reasoning(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-provider-default-read-1").model_copy(
                update={"reasoning_content": None}
            ),
            LLMResponse(
                content="README inspected with provider-default continuation.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    llm.settings.reasoning_capability_profile = "deepseek-chat-known"
    runtime = _runtime(llm, _registry(), _CompleteFileExecutor())
    owner = _Owner(runtime)
    owner._reasoning_policy_for_task = lambda _task: ReasoningPolicy(
        mode=ReasoningMode.PROVIDER_DEFAULT,
        unsupported_behavior=UnsupportedReasoningBehavior.PROVIDER_DEFAULT,
    )
    target = tmp_path / "README.md"
    result = ProviderToolRoundTripRunner(
        owner,
        Task(id="task-provider-default-deepseek", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=3,
        project_path=str(tmp_path),
        read_scope=[str(target)],
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is True, result.error_message
    assert llm.requests[0].reasoning_policy.mode == ReasoningMode.PROVIDER_DEFAULT
    assert result.messages[-2].reasoning_content is None


def test_roundtrip_runner_returns_recoverable_admission_error_to_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            LLMResponse(
                content="",
                reasoning_content="I will use an unavailable tool.",
                tool_calls=[
                    LLMToolCall(
                        id="ds-bad-1",
                        function=LLMToolFunctionCall(
                            name="not_registered",
                            arguments="{}",
                        ),
                    )
                ],
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="I can continue without that tool.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _Executor())
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-recover", description="Inspect README"),
        tools=[],
        max_rounds=2,
    ).run([LLMMessage(role="user", content="Inspect README")])

    assert result.success is True
    assert len(llm.requests) == 2
    tool_message = llm.requests[1].messages[-1]
    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "ds-bad-1"
    assert "UnknownTool" in tool_message.content


def test_roundtrip_runner_returns_recoverable_read_execution_error_to_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-dir-1"),
            LLMResponse(
                content="I will retry with a file path.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _RecoverableExecutor())
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-recover-read", description="Inspect the repository"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=2,
    ).run([LLMMessage(role="user", content="Inspect the repository")])

    assert result.success is True
    assert len(llm.requests) == 2
    assert result.tool_loop_results[0].success is False
    assert result.tool_loop_results[0].loop_metadata.recoverable_errors[0].provider_call_id == "ds-dir-1"
    tool_message = llm.requests[1].messages[-1]
    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "ds-dir-1"
    assert "FileReaderDirectoryPath" in tool_message.content


def test_roundtrip_blocks_duplicate_normalized_attempts_and_stops_no_progress(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    repeated_first = _tool_response(call_id="ds-repeat-1")
    repeated_second = _tool_response(call_id="ds-repeat-2")
    llm = _LLM(
        [
            repeated_first,
            repeated_second,
            LLMResponse(
                content="This response must not be requested.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    executor = _RecoverableExecutor()
    runtime = _runtime(llm, _registry(), executor)
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-duplicate", description="Inspect the repository"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=4,
    ).run([LLMMessage(role="user", content="Inspect the repository")])

    assert result.success is False
    assert result.error_message == "ProviderToolNoProgress after 2 round(s)"
    assert len(llm.requests) == 2
    assert executor.calls == 1
    assert "ProviderToolDuplicateAttempt" in result.messages[-1].content
    assert [attempt.error_type for attempt in result.attempts] == [
        "FileReaderDirectoryPath",
        "ProviderToolDuplicateAttempt",
    ]
    assert [item["outcome"] for item in result.budget_diagnostics] == [
        "tool_progress",
        "no_progress",
    ]
    assert runtime.runtime_controller.state.budget.tool_event_completion_last_outcome == "no_progress"


def test_roundtrip_guides_mutation_after_duplicate_complete_reads(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    target = tmp_path / "README.md"
    target.write_text("before\n", encoding="utf-8")
    writer_response = LLMResponse(
        content="",
        reasoning_content="Use the typed writer now.",
        tool_calls=[
            LLMToolCall(
                id="ds-mutation-write",
                function=LLMToolFunctionCall(
                    name="file_patch_writer",
                    arguments=json.dumps(
                        {
                            "file_path": "README.md",
                            "operation_kind": "modify_symbol",
                            "symbol_name": "README",
                            "replacement_text": "after",
                        }
                    ),
                ),
            )
        ],
        model="deepseek-v4-flash",
        provider="deepseek",
        finish_reason="tool_calls",
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-mutation-read-1"),
            _tool_response(call_id="ds-mutation-read-2"),
            writer_response,
            LLMResponse(
                content="Mutation completed.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    llm.settings.context_max_prompt_tokens = 20000
    class _RecordingCompleteFileExecutor(_CompleteFileExecutor):
        def __init__(self) -> None:
            self.calls = []

        def execute_single(self, selection, context=None):
            self.calls.append(selection)
            return super().execute_single(selection, context)

    executor = _RecordingCompleteFileExecutor()
    runtime = _runtime(llm, _mutation_registry(), executor)
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(
            id="task-mutation-guidance",
            description="Read README and apply the bounded mutation",
            kind="implement",
            read_files=[str(target)],
            write_files=[str(target)],
        ),
        tools=build_provider_tool_definitions(
            runtime.tool_registry,
            ["file_reader", "file_patch_writer"],
        ),
        max_rounds=4,
        user_confirmed=True,
        allow_mutations=True,
        read_scope=[str(target)],
        write_scope=[str(target)],
        project_path=str(tmp_path),
    ).run([LLMMessage(role="user", content="Read README, then apply the bounded mutation")])

    assert result.success is True
    assert len(executor.calls) == 2
    assert executor.calls[-1].tool_name == "file_patch_writer"
    assert any(
        message.role == "user"
        and "READ_EVIDENCE_READY" in message.content
        for request in llm.requests
        for message in request.messages
    )
    assert all(
        tool.function.name != "file_reader"
        for tool in llm.requests[2].tools
    )


def test_mutation_route_hides_command_executor_until_writer_succeeds(monkeypatch, tmp_path) -> None:
    """A mutation route must not offer exploratory commands before the writer."""
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    target = tmp_path / "target.py"
    runtime = _runtime(_LLM([]), _mutation_registry(), _Executor())
    from tools.command_tool import COMMAND_EXECUTOR_DEFINITION

    runtime.tool_registry.register(CODE_UNIT_GENERATOR_DEFINITION, lambda _input: None)
    runtime.tool_registry.register(COMMAND_EXECUTOR_DEFINITION, lambda _input: None)
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(
            id="task-route-before-writer",
            description="Generate and apply one bounded test",
            kind="implement",
            read_files=[str(target)],
            write_files=[str(target)],
        ),
        tools=build_provider_tool_definitions(
            runtime.tool_registry,
            ["file_reader", "code_unit_generator", "file_patch_writer", "command_executor"],
        ),
        max_rounds=3,
        project_path=str(tmp_path),
        read_scope=[str(target)],
        write_scope=[str(target)],
        allow_mutations=True,
        user_confirmed=True,
    )
    # Model the already-complete declared read without executing a new tool.
    runner._completed_read_sources[str(target.resolve())] = SimpleNamespace()

    pre_writer_tools = [tool.function.name for tool in runner._tools_for_request()]
    assert "file_reader" not in pre_writer_tools
    assert "code_unit_generator" in pre_writer_tools
    assert "file_patch_writer" in pre_writer_tools
    assert "command_executor" not in pre_writer_tools

    runner._post_mutation_active = True
    assert [tool.function.name for tool in runner._tools_for_request()] == ["command_executor"]


def test_read_only_command_route_keeps_command_executor(monkeypatch, tmp_path) -> None:
    """A task without mutation tools still has its ordinary command surface."""
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    from tools.command_tool import COMMAND_EXECUTOR_DEFINITION

    runtime = _runtime(_LLM([]), _registry(), _Executor())
    runtime.tool_registry.register(COMMAND_EXECUTOR_DEFINITION, lambda _input: None)
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-read-only-command", description="Run a read-only check", kind="analysis"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["command_executor"]),
        max_rounds=1,
        project_path=str(tmp_path),
    )

    assert [tool.function.name for tool in runner._tools_for_request()] == ["command_executor"]


def test_roundtrip_binds_project_root_for_relative_provider_read(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-relative-read"),
            LLMResponse(
                content="The requested file was read.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    executor = _Executor()
    runtime = _runtime(llm, _registry(), executor)
    project = "/tmp/phase36-relative-project"
    task = Task(
        id="task-relative-project-root",
        description="Read the scoped file",
        kind="analysis",
        read_files=[f"{project}/README.md"],
        write_files=[],
    )
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        task,
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=2,
        read_scope=task.read_files,
        project_path=project,
    ).run([LLMMessage(role="user", content="Read the scoped file")])

    assert result.success is True
    assert executor.calls[0].input_metadata.project_path == project


def test_patch_writer_schema_accepts_typed_code_artifact_reference() -> None:
    definitions = build_provider_tool_definitions(
        _mutation_registry(),
        ["file_patch_writer"],
    )
    parameters = definitions[0].function.parameters
    properties = parameters["properties"]
    assert properties["artifact_ref"]["type"] == "object"
    assert any(
        set(option.get("required", [])) == {"generated_unit"}
        or set(option.get("required", [])) == {"artifact_ref"}
        for option in parameters["allOf"][0]["then"]["anyOf"]
    )


def test_command_executor_schema_restricts_mode_enum_and_forbids_standard() -> None:
    registry = _registry()
    registry.register(COMMAND_EXECUTOR_DEFINITION, lambda _input: None)

    definition = build_provider_tool_definitions(registry, ["command_executor"])[0]
    mode_schema = definition.function.parameters["properties"]["mode"]

    assert mode_schema["type"] == "string"
    assert mode_schema["enum"]
    assert "automatic" in mode_schema["enum"]
    assert "standard" not in mode_schema["enum"]


def test_code_artifact_reference_is_registered_and_hash_verified(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), _mutation_registry(), _Executor())),
        Task(id="task-code-artifact-ref", description="Apply generated test"),
        tools=build_provider_tool_definitions(
            _mutation_registry(),
            ["file_patch_writer"],
        ),
        max_rounds=1,
    )
    artifact = CodeArtifactMetadata(
        code="def test_generated():\n    assert True\n",
        language="python",
    )
    reference = runner._register_code_artifact(
        artifact,
        source_id="tool-event-1",
        provider_call_id="provider-call-1",
    )
    resolved = runner._resolve_code_artifact(reference)
    assert resolved == artifact.code

    mismatched = dict(reference)
    mismatched["sha256"] = "0" * 64
    try:
        runner._resolve_code_artifact(mismatched)
    except ValueError as exc:
        assert "artifact" in str(exc).lower()
    else:
        raise AssertionError("hash-mismatched artifact reference must fail closed")


def test_code_artifact_projection_keeps_preview_bounded_and_exposes_handoff(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), _mutation_registry(), _Executor())),
        Task(id="task-code-artifact-projection", description="Apply generated test"),
        tools=build_provider_tool_definitions(
            _mutation_registry(),
            ["file_patch_writer"],
        ),
        max_rounds=1,
    )
    code = "def test_generated():\n    assert True\n" + ("# bounded\n" * 180)
    projected, reference = runner._result_projection(
        CodeArtifactMetadata(code=code, language="python"),
        source_id="tool-event-2",
        provider_call_id="provider-call-2",
    )
    assert reference is not None
    assert len(projected["preview"]) <= 480
    assert "artifact_ref" in projected["artifact_handoff"]
    assert runner._resolve_code_artifact(reference) == code


def test_code_artifact_projection_records_bounded_handoff_diagnostic(monkeypatch) -> None:
    """The receipt must distinguish a generated artifact from its bounded preview."""
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    runtime = _runtime(_LLM([]), _mutation_registry(), _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-code-artifact-diagnostic", description="Apply generated test"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_patch_writer"]),
    )
    code = "def test_generated():\n    assert True\n"
    _projected, reference = runner._result_projection(
        CodeArtifactMetadata(code=code, language="python"),
        source_id="tool-event-diagnostic",
        provider_call_id="provider-call-diagnostic",
    )
    result = runner._result(
        success=True,
        final_response=None,
        messages=[],
        tool_loop_results=[],
        rounds_used=0,
    )

    assert reference is not None
    assert len(result.handoff_diagnostics) == 1
    diagnostic = result.handoff_diagnostics[0]
    assert diagnostic["tool_name"] == "code_unit_generator"
    assert diagnostic["provider_call_id"] == "provider-call-diagnostic"
    assert diagnostic["artifact_ref"]["sha256"] == reference["sha256"]
    assert diagnostic["projection_status"] == "bounded_preview"
    assert code not in json.dumps(diagnostic, ensure_ascii=False)


def test_roundtrip_injects_verified_code_artifact_into_patch_writer(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    code = "def test_generated():\n    assert True\n"
    writer_response = LLMResponse(
        content="",
        reasoning_content="Use the verified generated artifact.",
        tool_calls=[
            LLMToolCall(
                id="ds-artifact-writer",
                function=LLMToolFunctionCall(
                    name="file_patch_writer",
                    arguments=json.dumps(
                        {
                            "file_path": "README.md",
                            "operation_kind": "add_symbol",
                            "artifact_ref": {
                                "kind": "code_artifact",
                                "source_id": "tool-event-3",
                                "provider_call_id": "provider-call-3",
                                "sha256": hashlib.sha256(code.encode()).hexdigest(),
                                "bytes": len(code.encode()),
                                "chars": len(code),
                                "language": "python",
                            },
                        }
                    ),
                ),
            )
        ],
        model="deepseek-v4-flash",
        provider="deepseek",
        finish_reason="tool_calls",
    )
    llm = _LLM(
        [
            writer_response,
            LLMResponse(
                content="Mutation completed.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    executor = _Executor()
    runtime = _runtime(llm, _mutation_registry(), executor)
    owner = _Owner(runtime)
    runner = ProviderToolRoundTripRunner(
        owner,
        Task(
            id="task-artifact-writer",
            description="Apply generated test",
            kind="implement",
            write_files=[str(tmp_path / "README.md")],
        ),
        tools=build_provider_tool_definitions(
            runtime.tool_registry,
            ["file_patch_writer"],
        ),
        max_rounds=2,
        user_confirmed=True,
        allow_mutations=True,
        write_scope=[str(tmp_path / "README.md")],
        project_path=str(tmp_path),
    )
    reference = runner._register_code_artifact(
        CodeArtifactMetadata(code=code, language="python"),
        source_id="tool-event-3",
        provider_call_id="provider-call-3",
    )
    assert reference["sha256"] in runner._code_artifact_ledger
    result = runner.run([LLMMessage(role="user", content="Apply the generated test")])
    assert result.success is True, result.error_message
    assert executor.calls[-1].input_metadata.generated_unit == code


def test_post_mutation_context_is_bounded_and_excludes_generated_unit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    code = "def test_generated():\n    assert True\n" + ("# generated\n" * 300)
    runtime = _runtime(_LLM([]), _mutation_registry(), _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(
            id="task-post-mutation-context",
            description="Apply the generated test",
            kind="implement",
            write_files=[str(tmp_path / "README.md")],
            validation_command="PYTHONPATH=Code/src .venv/bin/python -m pytest -q Code/tests/test_provider_tool_roundtrip.py",
        ),
        tools=build_provider_tool_definitions(
            runtime.tool_registry,
            ["file_patch_writer"],
        ),
        max_rounds=2,
        user_confirmed=True,
        allow_mutations=True,
        write_scope=[str(tmp_path / "README.md")],
        project_path=str(tmp_path),
        validation_command="PYTHONPATH=Code/src .venv/bin/python -m pytest -q Code/tests/test_provider_tool_roundtrip.py",
    )
    reference = runner._register_code_artifact(
        CodeArtifactMetadata(code=code, language="python"),
        source_id="post-mutation-source",
        provider_call_id="post-mutation-generator",
    )
    receipt = runner._mutation_receipt(
        {
            "tool": "file_patch_writer",
            "success": True,
            "input_metadata": {
                "file_path": "README.md",
                "operation_kind": "add_symbol",
                "generated_unit": code,
                "artifact_ref": reference,
            },
            "result": {
                "bytes_written": len(code.encode()),
                "attributes": {"changed_ranges": [{"line_start": 1, "line_end": 4}]},
            },
        }
    )

    assert receipt is not None
    runner._post_mutation_receipt = receipt
    encoded_receipt = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert code not in encoded_receipt
    assert len(encoded_receipt) < 1_200

    messages = runner._post_mutation_messages(
        [
            LLMMessage(role="system", content="Use the typed tools only."),
            LLMMessage(role="user", content="Apply the generated test."),
        ]
    )
    encoded_messages = json.dumps(
        [message.model_dump(mode="json") for message in messages],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert code not in encoded_messages
    assert "PYTHONPATH=Code/src .venv/bin/python -m pytest -q Code/tests/test_provider_tool_roundtrip.py" in encoded_messages
    assert "file_patch_writer" in encoded_messages


def test_post_mutation_context_budget_failure_is_typed(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    code = "def test_generated():\n    assert True\n"
    writer_response = LLMResponse(
        content="",
        reasoning_content="The scoped mutation is ready for validation.",
        tool_calls=[
            LLMToolCall(
                id="post-mutation-writer",
                function=LLMToolFunctionCall(
                    name="file_patch_writer",
                    arguments=json.dumps(
                        {
                            "file_path": "README.md",
                            "operation_kind": "add_symbol",
                            "artifact_ref": {
                                "kind": "code_artifact",
                                "source_id": "post-mutation-source",
                                "provider_call_id": "post-mutation-generator",
                                "sha256": hashlib.sha256(code.encode()).hexdigest(),
                                "bytes": len(code.encode()),
                                "chars": len(code),
                                "language": "python",
                            },
                        }
                    ),
                ),
            )
        ],
        model="deepseek-v4-flash",
        provider="deepseek",
        finish_reason="tool_calls",
    )
    llm = _LLM([writer_response])
    runtime = _runtime(llm, _mutation_registry(), _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(
            id="task-post-mutation-budget",
            description="Apply the generated test",
            kind="implement",
            write_files=[str(tmp_path / "README.md")],
            validation_command="python -m pytest -q",
        ),
        tools=build_provider_tool_definitions(
            runtime.tool_registry,
            ["file_patch_writer"],
        ),
        max_rounds=2,
        user_confirmed=True,
        allow_mutations=True,
        write_scope=[str(tmp_path / "README.md")],
        project_path=str(tmp_path),
        validation_command="python -m pytest -q",
    )
    runner._register_code_artifact(
        CodeArtifactMetadata(code=code, language="python"),
        source_id="post-mutation-source",
        provider_call_id="post-mutation-generator",
    )
    original_builder = __import__(
        "core.provider_tool_roundtrip", fromlist=["build_context_llm_request"]
    ).build_context_llm_request
    calls = 0

    def fail_post_mutation_builder(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            from core.exceptions import ContextAssemblyBudgetError

            raise ContextAssemblyBudgetError(["post_mutation:receipt"])
        return original_builder(*args, **kwargs)

    monkeypatch.setattr(
        "core.provider_tool_roundtrip.build_context_llm_request",
        fail_post_mutation_builder,
    )
    result = runner.run([LLMMessage(role="user", content="Apply the generated test")])

    assert result.success is False
    assert result.error_message == "ProviderToolPostMutationContextBudgetFailure"
    assert len(llm.requests) == 1


def test_post_mutation_continuation_can_run_exact_validation_without_old_history(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    from tools.command_tool import COMMAND_EXECUTOR_DEFINITION

    code = "def test_generated():\n    assert True\n"
    writer_response = LLMResponse(
        content="",
        reasoning_content="The scoped mutation is ready for validation.",
        tool_calls=[
            LLMToolCall(
                id="post-mutation-writer-success",
                function=LLMToolFunctionCall(
                    name="file_patch_writer",
                    arguments=json.dumps(
                        {
                            "file_path": "README.md",
                            "operation_kind": "add_symbol",
                            "artifact_ref": {
                                "kind": "code_artifact",
                                "source_id": "post-mutation-source-success",
                                "provider_call_id": "post-mutation-generator-success",
                                "sha256": hashlib.sha256(code.encode()).hexdigest(),
                                "bytes": len(code.encode()),
                                "chars": len(code),
                                "language": "python",
                            },
                        }
                    ),
                ),
            )
        ],
        model="deepseek-v4-flash",
        provider="deepseek",
        finish_reason="tool_calls",
    )
    validation_command = "python -m pytest -q"
    validation_response = LLMResponse(
        content="",
        reasoning_content="Run the exact validation command now.",
        tool_calls=[
            LLMToolCall(
                id="post-mutation-validation",
                function=LLMToolFunctionCall(
                    name="command_executor",
                    arguments=json.dumps(
                        {"command": validation_command, "mode": "automatic"}
                    ),
                ),
            )
        ],
        model="deepseek-v4-flash",
        provider="deepseek",
        finish_reason="tool_calls",
    )
    llm = _LLM(
        [
            writer_response,
            validation_response,
            LLMResponse(
                content="Validation passed.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    llm.settings.context_max_prompt_tokens = 20_000
    registry = _mutation_registry()
    registry.register(COMMAND_EXECUTOR_DEFINITION, lambda _input: None)
    runtime = _runtime(llm, registry, _Executor())
    owner = _Owner(runtime)
    owner._reasoning_policy_for_task = lambda _task: ReasoningPolicy(
        mode=ReasoningMode.DISABLED,
        unsupported_behavior=UnsupportedReasoningBehavior.PROVIDER_DEFAULT,
    )
    task = Task(
        id="task-post-mutation-validation",
        description="Apply the generated test",
        kind="implement",
        write_files=[str(tmp_path / "README.md")],
        validation_command=validation_command,
    )
    runner = ProviderToolRoundTripRunner(
        owner,
        task,
        tools=build_provider_tool_definitions(
            registry,
            ["file_patch_writer", "command_executor"],
        ),
        max_rounds=3,
        user_confirmed=True,
        allow_mutations=True,
        write_scope=[str(tmp_path / "README.md")],
        project_path=str(tmp_path),
        validation_command=validation_command,
    )
    runner._register_code_artifact(
        CodeArtifactMetadata(code=code, language="python"),
        source_id="post-mutation-source-success",
        provider_call_id="post-mutation-generator-success",
    )

    result = runner.run(
        [
            LLMMessage(role="system", content="Use typed tools only."),
            LLMMessage(role="user", content="Apply the generated test."),
        ]
    )

    assert result.success is True, result.error_message
    assert len(llm.requests) == 3
    assert llm.requests[1].messages[2].role == "user"
    assert validation_command in llm.requests[1].messages[2].content
    assert all(
        code not in message.content
        for request in llm.requests[1:]
        for message in request.messages
    )
    assert all(
        event.input_metadata is None or event.input_metadata.generated_unit is None
        for loop in result.tool_loop_results
        for event in loop.loop_metadata.events
    )


def test_failed_exact_validation_stops_without_exploratory_follow_up(monkeypatch, tmp_path) -> None:
    """A failed exact test after a write must not trigger another provider turn."""
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    from tools.command_tool import COMMAND_EXECUTOR_DEFINITION

    code = "def test_generated():\n    assert True\n"
    validation_command = "python -m pytest -q"
    writer_response = LLMResponse(
        content="",
        reasoning_content="Apply the grounded test.",
        tool_calls=[
            LLMToolCall(
                id="failed-validation-writer",
                function=LLMToolFunctionCall(
                    name="file_patch_writer",
                    arguments=json.dumps(
                        {
                            "file_path": "README.md",
                            "operation_kind": "add_symbol",
                            "artifact_ref": {
                                "kind": "code_artifact",
                                "source_id": "failed-validation-source",
                                "provider_call_id": "failed-validation-generator",
                                "sha256": hashlib.sha256(code.encode()).hexdigest(),
                                "bytes": len(code.encode()),
                                "chars": len(code),
                                "language": "python",
                            },
                        }
                    ),
                ),
            )
        ],
        model="deepseek-v4-flash",
        provider="deepseek",
        finish_reason="tool_calls",
    )
    validation_response = LLMResponse(
        content="",
        reasoning_content="Run the exact validation command.",
        tool_calls=[
            LLMToolCall(
                id="failed-validation-command",
                function=LLMToolFunctionCall(
                    name="command_executor",
                    arguments=json.dumps(
                        {"command": validation_command, "mode": "automatic"}
                    ),
                ),
            )
        ],
        model="deepseek-v4-flash",
        provider="deepseek",
        finish_reason="tool_calls",
    )
    llm = _LLM(
        [
            writer_response,
            validation_response,
            LLMResponse(
                content="This third turn must not be requested.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    llm.settings.context_max_prompt_tokens = 20_000
    registry = _mutation_registry()
    registry.register(COMMAND_EXECUTOR_DEFINITION, lambda _input: None)
    runtime = _runtime(llm, registry, _Executor())
    owner = _Owner(runtime)
    owner._reasoning_policy_for_task = lambda _task: ReasoningPolicy(
        mode=ReasoningMode.DISABLED,
        unsupported_behavior=UnsupportedReasoningBehavior.PROVIDER_DEFAULT,
    )
    runner = ProviderToolRoundTripRunner(
        owner,
        Task(
            id="task-failed-validation-stop",
            description="Stop after failed validation",
            kind="implement",
            write_files=[str(tmp_path / "README.md")],
            validation_command=validation_command,
        ),
        tools=build_provider_tool_definitions(
            registry,
            ["file_patch_writer", "command_executor"],
        ),
        max_rounds=3,
        user_confirmed=True,
        allow_mutations=True,
        write_scope=[str(tmp_path / "README.md")],
        project_path=str(tmp_path),
        validation_command=validation_command,
    )
    runner._register_code_artifact(
        CodeArtifactMetadata(code=code, language="python"),
        source_id="failed-validation-source",
        provider_call_id="failed-validation-generator",
    )

    def fake_run_provider_tool_calls(self, task, admissions, *, round_index=1):
        del self, task, round_index
        call = admissions[0].tool_call
        if call.tool_name == "file_patch_writer":
            item = {
                "provider_call_id": call.provider_call_id,
                "call_id": call.call_id,
                "tool": call.tool_name,
                "success": True,
                "input_metadata": call.input_metadata.to_json_dict(),
                "result": {
                    "bytes_written": len(code.encode()),
                    "attributes": {"changed_ranges": [{"line_start": 1, "line_end": 2}]},
                },
            }
            success = True
        else:
            item = {
                "provider_call_id": call.provider_call_id,
                "call_id": call.call_id,
                "tool": call.tool_name,
                "success": False,
                "input_metadata": call.input_metadata.to_json_dict(),
                "result": {"success": False, "exit_code": 1, "stderr": "pytest failed"},
            }
            success = False
        return SimpleNamespace(
            success=success,
            tool_results=[item],
            loop_metadata=SimpleNamespace(
                recoverable_errors=[],
                events=[],
                tool_invocations=[],
                tool_contexts=[],
            ),
            error_message="pytest failed" if not success else None,
        )

    monkeypatch.setattr(
        "core.provider_tool_roundtrip.ToolEventLoopRunner.run_provider_tool_calls",
        fake_run_provider_tool_calls,
    )
    result = runner.run(
        [
            LLMMessage(role="system", content="Use typed tools only."),
            LLMMessage(role="user", content="Apply the grounded test."),
        ]
    )

    assert result.success is False
    assert result.error_message == "ProviderToolValidationFailed"
    assert len(llm.requests) == 2


def test_roundtrip_uses_owner_projected_initial_context_once(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-initial-context-read"),
            LLMResponse(
                content="The scoped file was inspected.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _Executor())
    project = "/tmp/phase36-initial-context-project"
    candidates = [
        ContextCandidate(
            candidate_id="provider:system",
            kind=ContextCandidateKind.INSTRUCTION,
            content="Use only the declared read scope and return evidence-backed findings.",
            role="system",
            retention=ContextCandidateRetention.REQUIRED,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            trust=ContextCandidateTrust.AUTHORITATIVE,
            priority=100,
            source_order=0,
        ),
        ContextCandidate(
            candidate_id="provider:task",
            kind=ContextCandidateKind.TASK,
            content=f"Read the scoped file. Project root: {project}. Read scope: {project}/README.md",
            retention=ContextCandidateRetention.REQUIRED,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            trust=ContextCandidateTrust.AUTHORITATIVE,
            priority=100,
            source_order=1,
        ),
        ContextCandidate(
            candidate_id="provider:history",
            kind=ContextCandidateKind.DIALOG,
            content="Old historical discussion that may be omitted when the request is assembled.",
            retention=ContextCandidateRetention.OPTIONAL,
            truncation=ContextCandidateTruncation.HEAD,
            trust=ContextCandidateTrust.OBSERVED,
            priority=1,
            source_order=2,
        ),
    ]
    task = Task(
        id="task-initial-context",
        description="Read the scoped file",
        kind="analysis",
        read_files=[f"{project}/README.md"],
        write_files=[],
    )
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        task,
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=2,
        read_scope=task.read_files,
        project_path=project,
        initial_context_candidates=candidates,
    ).run([LLMMessage(role="user", content="This fallback message must not replace the projection.")])

    assert result.success is True
    assert len(llm.requests) == 2
    first_request = llm.requests[0]
    assert first_request.trace_info["context_purpose"] == ContextRequestPurpose.TOOL_EVENT_DECISION.value
    assert first_request.trace_info["provider_tool_schema_tokens"] > 0
    assert first_request.trace_info["reasoning_complexity"] == "standard"
    assert first_request.tools and first_request.tools[0].function.name == "file_reader"
    assert any("Use only the declared read scope" in message.content for message in first_request.messages)
    assert any("Project root" in message.content for message in first_request.messages)
    assert all("fallback message" not in message.content for message in first_request.messages)
    assert any(
        message.role == "assistant"
        and message.tool_calls
        and message.tool_calls[0].id == "ds-initial-context-read"
        for message in llm.requests[1].messages
    )
    assert any(message.role == "tool" for message in llm.requests[1].messages)


def test_provider_mutation_defers_generic_verifier_until_exact_provider_command() -> None:
    state = RuntimeStateMetadata(goal="Implement calculator")
    state.verification_status = "required"
    runtime = SimpleNamespace(
        runtime_controller=SimpleNamespace(state=state, verifier=object()),
        _project_environments={},
    )
    owner = SimpleNamespace(runtime=runtime, _log=lambda *args, **kwargs: None)
    loop = ToolEventLoopRunner(owner)
    task = Task(
        id="task-provider-validation-order",
        description="Implement the scoped fix",
        kind="implement",
        write_files=["/tmp/calculator.py"],
        validation_command="python -m pytest -q tests/test_calculator.py",
    )
    selection = ToolSelection(
        step_id="step_1_1",
        tool_name="file_writer",
        reason=SelectionReason.CAPABILITY_MATCH,
        confidence=1.0,
        input_metadata=ToolInputMetadata.from_mapping(
            "file_writer",
            {
                "file_path": "/tmp/calculator.py",
                "content": "pass\n",
                "operation_kind": "file_replace",
            },
        ),
    )

    assert (
        loop._verify_state_change_if_needed(
            task=task,
            task_id=task.id,
            session_id="session-provider-validation-order",
            source_selection=selection,
            round_index=1,
            last_output=None,
            defer_provider_validation=True,
        )
        is None
    )


def test_roundtrip_normalizes_relative_and_absolute_call_signatures(tmp_path) -> None:
    runtime = _runtime(_LLM([]), _registry(), _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-signature", description="Inspect"),
        tools=[],
        project_path=str(tmp_path),
    )
    relative = LLMToolCall(
        id="relative",
        function=LLMToolFunctionCall(name="file_reader", arguments='{"file_path":"README.md"}'),
    )
    absolute = LLMToolCall(
        id="absolute",
        function=LLMToolFunctionCall(
            name="file_reader",
            arguments=json.dumps({"file_path": str(tmp_path / "README.md")}),
        ),
    )

    assert runner._call_signature(relative) == runner._call_signature(absolute)


def test_roundtrip_blocks_paging_after_complete_file_evidence(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-complete-1"),
            LLMResponse(
                content="",
                reasoning_content="The complete evidence is already available.",
                tool_calls=[
                    LLMToolCall(
                        id="ds-page-1",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments='{"file_path":"README.md","offset":100,"max_lines":20}',
                        ),
                    )
                ],
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="tool_calls",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _CompleteFileExecutor())
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-complete-read", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=3,
        project_path=str(tmp_path),
        max_no_progress_rounds=1,
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is False
    assert result.error_message == "ProviderToolNoProgress after 1 round(s)"
    assert len(llm.requests) == 2
    assert "ProviderToolDuplicateAttempt" in result.messages[-1].content


def test_roundtrip_finalizes_once_after_duplicate_complete_scoped_reads(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-finalize-read-1"),
            LLMResponse(
                content="",
                reasoning_content="The complete file was already returned; I will page it.",
                tool_calls=[
                    LLMToolCall(
                        id="ds-finalize-page-1",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments='{"file_path":"README.md","offset":100,"max_lines":20}',
                        ),
                    )
                ],
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="README.md was read completely and is available as evidence.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _CompleteFileExecutor())
    target = tmp_path / "README.md"
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-finalize-read", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=3,
        project_path=str(tmp_path),
        read_scope=[str(target)],
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is True
    assert result.final_response is not None
    assert result.final_response.content.startswith("README.md was read")
    assert len(llm.requests) == 3
    assert llm.requests[2].tools == []
    assert "final answer" in llm.requests[2].messages[-1].content.lower()
    assert "complete README" in llm.requests[2].messages[-1].content
    assert '"evidence_status":"complete"' in llm.requests[2].messages[-1].content
    assert result.evidence_coverage.completed_read_paths == (str(target.resolve()),)
    assert result.evidence_coverage.finalization_requests == 1


def test_roundtrip_fails_closed_when_finalization_is_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-empty-finalize-1"),
            LLMResponse(
                content="",
                reasoning_content="The complete file was already returned; I will page it.",
                tool_calls=[
                    LLMToolCall(
                        id="ds-empty-finalize-page-1",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments='{"file_path":"README.md","offset":100}',
                        ),
                    )
                ],
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="",
                reasoning_content="No final answer.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _CompleteFileExecutor())
    target = tmp_path / "README.md"
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-empty-finalize", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=3,
        project_path=str(tmp_path),
        read_scope=[str(target)],
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is False
    assert result.error_message == "ProviderToolFinalizationEmpty"
    assert len(llm.requests) == 3
    assert llm.requests[2].tools == []


def test_roundtrip_classifies_reasoning_exhaustion_as_typed_finalization_failure(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-reasoning-empty-1"),
            LLMResponse(
                content="",
                reasoning_content="The complete file was already returned; I will page it.",
                tool_calls=[
                    LLMToolCall(
                        id="ds-reasoning-empty-page-1",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments='{"file_path":"README.md","offset":100}',
                        ),
                    )
                ],
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="length",
                usage={
                    "completion_tokens": 1200,
                    "completion_tokens_details": {"reasoning_tokens": 1200},
                },
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _CompleteFileExecutor())
    target = tmp_path / "README.md"
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-reasoning-empty", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=3,
        project_path=str(tmp_path),
        read_scope=[str(target)],
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is False
    assert result.error_message == "ProviderToolFinalizationReasoningExhausted"


def test_roundtrip_does_not_finalize_when_mutation_capability_is_exposed(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-mutation-boundary-1"),
            LLMResponse(
                content="",
                reasoning_content="I will page the complete file.",
                tool_calls=[
                    LLMToolCall(
                        id="ds-mutation-boundary-page-1",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments='{"file_path":"README.md","offset":100}',
                        ),
                    )
                ],
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="This answer must not be requested.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _CompleteFileExecutor())
    target = tmp_path / "README.md"
    tools = build_provider_tool_definitions(runtime.tool_registry, ["file_reader"])
    tools.append(
        LLMToolDefinition(
            function=LLMToolFunction(
                name="code_editor",
                description="mutate code",
                parameters={"type": "object"},
            )
        )
    )
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-mutation-boundary", description="Read README"),
        tools=tools,
        max_rounds=3,
        max_no_progress_rounds=1,
        project_path=str(tmp_path),
        read_scope=[str(target)],
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is False
    assert result.error_message == "ProviderToolNoProgress after 1 round(s)"
    assert len(llm.requests) == 2
    assert result.evidence_coverage.finalization_requests == 0


def test_roundtrip_rejects_mutation_tools_without_code_level_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    runtime = _runtime(llm, _registry(), _Executor())
    mutation_tool = LLMToolDefinition(
        function=LLMToolFunction(
            name="file_writer",
            description="write a file",
            parameters={"type": "object"},
        )
    )
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-direct-mutation-boundary", description="Write README"),
        tools=[mutation_tool],
        user_confirmed=True,
        max_rounds=2,
    ).run([LLMMessage(role="user", content="Write README")])

    assert result.success is False
    assert result.error_message == "ProviderToolMutationOptInRequired"
    assert llm.requests == []


def test_roundtrip_rejects_mutation_tools_without_user_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    runtime = _runtime(llm, _registry(), _Executor())
    mutation_tool = LLMToolDefinition(
        function=LLMToolFunction(
            name="file_writer",
            description="write a file",
            parameters={"type": "object"},
        )
    )
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-direct-confirmation-boundary", description="Write README"),
        tools=[mutation_tool],
        allow_mutations=True,
        max_rounds=2,
    ).run([LLMMessage(role="user", content="Write README")])

    assert result.success is False
    assert result.error_message == "ProviderToolMutationConfirmationRequired"
    assert llm.requests == []


def test_roundtrip_marks_complete_artifact_and_bounded_projection(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    runtime = _runtime(_LLM([]), _registry(), _CompleteFileExecutor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-projection-status", description="Read README"),
        tools=[],
        project_path=str(tmp_path),
    )
    projected, artifact_ref = runner._result_projection(
        FileArtifactMetadata(
            content="complete README content",
            file_path="README.md",
            lines_read=2,
            total_lines=2,
            truncated=False,
        ),
        source_id="artifact-1",
        provider_call_id="call-1",
    )

    assert artifact_ref is not None
    assert projected["evidence_status"] == "complete"
    assert projected["projection_status"] == "inline"


def test_roundtrip_labels_complete_short_file_as_inline_content(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    runtime = _runtime(_LLM([]), _registry(), _CompleteFileExecutor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-inline-content", description="Read README"),
        tools=[],
        project_path=str(tmp_path),
    )

    projected, _ = runner._result_projection(
        FileArtifactMetadata(
            content="complete README content",
            file_path="README.md",
            lines_read=2,
            total_lines=2,
            truncated=False,
        ),
        source_id="artifact-inline",
        provider_call_id="call-inline",
    )

    assert projected["evidence_status"] == "complete"
    assert projected["projection_status"] == "inline"
    assert projected["content"] == "complete README content"
    assert "preview" not in projected


def test_roundtrip_compacts_complete_file_evidence_without_fake_truncation(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    runtime = _runtime(_LLM([]), _registry(), _CompleteFileExecutor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-compact-evidence", description="Read README"),
        tools=[],
        project_path=str(tmp_path),
    )
    payload = {
        "success": True,
        "tool": "file_reader",
        "result": {
            "kind": "file_artifact",
            "file_path": str(tmp_path / "README.md"),
            "lines_read": 2,
            "total_lines": 2,
            "truncated": False,
            "evidence_status": "complete",
            "projection_status": "bounded_preview",
            "preview": "complete README content " * 40,
        },
        "artifact_ref": {"sha256": "a" * 64, "chars": 2000},
    }

    compacted = json.loads(runner._fit_tool_result_payload(payload, limit=640))

    assert compacted.get("projection_compacted") is True
    assert "truncated" not in compacted
    assert compacted["result"]["evidence_status"] == "complete"
    assert compacted["result"]["projection_status"] == "bounded_preview"
    assert compacted["result"]["truncated"] is False


def test_roundtrip_preserves_declared_window_identity_after_payload_fitting(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    runtime = _runtime(_LLM([]), _registry(), _CompleteFileExecutor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-bounded-window-fit", description="Read the declared window"),
        tools=[],
        project_path=str(tmp_path),
    )
    payload = {
        "success": True,
        "tool": "file_reader",
        "result": {
            "kind": "file_artifact",
            "file_path": str(tmp_path / "source.py"),
            "lines_read": 120,
            "total_lines": 2_000,
            "truncated": True,
            "read_window": {"read_mode": "adaptive", "offset": 1_240, "max_lines": 120},
            "evidence_status": "complete",
            "projection_status": "bounded_window",
            "preview": "def test_anchor():\n    return True\n" * 120,
        },
        "artifact_ref": {"sha256": "a" * 64, "chars": 4_000},
    }

    compacted = json.loads(runner._fit_tool_result_payload(payload, limit=640))

    result = compacted["result"]
    assert result["evidence_status"] == "complete"
    assert result["projection_status"] == "bounded_window"
    assert result["read_window"] == {"read_mode": "adaptive", "offset": 1_240, "max_lines": 120}
    assert result.get("preview") or compacted.get("artifact_ref")
    assert compacted.get("projection_compacted") is not True
    assert compacted.get("display_truncated") is True


def test_roundtrip_binds_validation_cwd_to_disposable_project_root(tmp_path) -> None:
    command = "python -m pytest -q tests/test_calculator.py"
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="command_executor",
            display_name="command_executor",
            description="Run the exact validation command",
            capabilities=[ToolCapability.SHELL_EXECUTION],
            permission_level=PermissionLevel.HIGH,
            contract_metadata=ToolContractMetadata(
                tool_name="command_executor",
                input_metadata_type="ToolInputMetadata",
                output_metadata_type="ToolResultMetadata",
                required_input_fields=["command"],
            ),
        ),
        lambda _input: None,
    )
    runtime = _runtime(_LLM([]), registry, _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-validation-cwd", description="Validate the disposable project"),
        tools=[],
        project_path=str(tmp_path),
        validation_command=command,
        validation_cwd=str(tmp_path),
    )
    admission = admit_provider_tool_calls(
        [
            LLMToolCall(
                id="validation-cwd-call",
                function=LLMToolFunctionCall(
                    name="command_executor",
                    arguments=json.dumps({"command": command, "mode": "automatic"}),
                ),
            )
        ],
        task_id="task-validation-cwd",
        session_id="session-validation-cwd",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(),
        user_confirmed=True,
        project_path=str(tmp_path),
        validation_command=command,
        validation_cwd=str(tmp_path),
    )[0]

    bound = runner._bind_project_path(admission, round_index=1)

    assert bound.status == "admitted"
    assert bound.selection is not None
    assert bound.selection.input_metadata.project_path == str(tmp_path)
    assert bound.selection.input_metadata.cwd == str(tmp_path)


def test_roundtrip_classifies_failed_exact_validation_as_terminal_failure(tmp_path) -> None:
    command = "python -m pytest -q"
    runtime = _runtime(_LLM([]), _registry(), _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-validation-failure", description="Stop after failed validation"),
        tools=[],
        project_path=str(tmp_path),
        validation_command=command,
    )
    loop_result = SimpleNamespace(
        tool_results=[
            {
                "success": False,
                "tool": "command_executor",
                "input_metadata": {
                    "command": command,
                    "requested_command": command,
                    "cwd": str(tmp_path),
                },
                "result": {
                    "success": False,
                    "exit_code": 1,
                },
            }
        ]
    )

    assert runner._validation_failed_in_loop(loop_result) is True


def test_roundtrip_validation_uses_argv_normalization_consistent_with_admission(tmp_path) -> None:
    expected = "python -m pytest -q tests/test_calculator.py"
    provider_rendering = "  python   -m pytest -q tests/test_calculator.py  "
    runtime = _runtime(_LLM([]), _registry(), _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-validation-normalization", description="Normalize validation evidence"),
        tools=[],
        project_path=str(tmp_path),
        validation_command=expected,
    )
    loop_result = SimpleNamespace(
        tool_results=[
            {
                "success": True,
                "tool": "command_executor",
                "input_metadata": {
                    "command": provider_rendering,
                    "requested_command": provider_rendering,
                    "cwd": str(tmp_path),
                },
                "result": {"success": True, "exit_code": 0},
            }
        ]
    )

    assert runner._validation_succeeded_in_loop(loop_result) is True

    loop_result.tool_results[0]["success"] = False
    loop_result.tool_results[0]["result"] = {"success": False, "exit_code": 1}
    assert runner._validation_failed_in_loop(loop_result) is True


def test_roundtrip_binds_generator_to_declared_evidence_and_write_target(tmp_path) -> None:
    source = tmp_path / "Code" / "src" / "core" / "provider_tool_roundtrip.py"
    target = tmp_path / "Code" / "tests" / "test_provider_tool_roundtrip.py"
    outside = tmp_path / "README.md"
    registry = _generator_registry()
    runtime = _runtime(_LLM([]), registry, _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-generator-grounding", description="Add a grounded regression test"),
        tools=[],
        project_path=str(tmp_path),
        read_scope=[str(source)],
        write_scope=[str(target)],
        bounded_read_windows=[
            FileReadWindowSpec(
                file_path=str(source),
                read_mode="adaptive",
                offset=0,
                max_lines=20,
            )
        ],
    )
    runner._completed_page_content[str(source)] = [
        {
            "excerpt": "from core.provider_tool_roundtrip import ProviderToolRoundTripRunner\n",
            "offset": 0,
            "max_lines": 20,
            "read_mode": "adaptive",
        }
    ]
    source_key = runner._declared_window_key(runner.bounded_read_windows[0])
    runner._completed_declared_windows.add(source_key)
    runner._completed_declared_window_attempts[source_key] = SimpleNamespace(
        provider_call_id="read-source"
    )
    # This entry must never be copied into generator context because it is not
    # part of the task's declared read scope.
    runner._completed_page_content[str(outside)] = [
        {
            "excerpt": "from invented.module import NotAllowed\n",
            "offset": 0,
            "max_lines": 20,
            "read_mode": "adaptive",
        }
    ]
    provider_call = runner._prepare_provider_tool_call(
        LLMToolCall(
            id="generator-grounding-call",
            function=LLMToolFunctionCall(
                name="code_unit_generator",
                arguments=json.dumps(
                    {
                        "task_description": "Add one regression test",
                        "language": "python",
                    }
                ),
            ),
        )
    )
    admission = admit_provider_tool_calls(
        [provider_call],
        task_id="task-generator-grounding",
        session_id="session-generator-grounding",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(),
        user_confirmed=True,
        project_path=str(tmp_path),
        read_scope=[str(source)],
        write_scope=[str(target)],
    )[0]

    bound = runner._bind_project_path(admission, round_index=1)

    assert bound.status == "admitted"
    assert bound.selection is not None
    input_metadata = bound.selection.input_metadata
    assert input_metadata.file_path == str(target)
    assert "ProviderToolRoundTripRunner" in (input_metadata.context or "")
    assert "MODULE_SYMBOL_CANDIDATE: ProviderToolRoundTripRunner" in (input_metadata.context or "")
    assert str(source) in (input_metadata.context or "")
    assert "NotAllowed" not in (input_metadata.context or "")
    assert input_metadata.runtime_handles["_generator_grounding_enforced"] is True
    grounding = input_metadata.prompt_context["grounding"]
    assert grounding["mode"] == "declared_read_evidence"
    assert grounding["source_ids"]
    assert str(outside) not in grounding["source_ids"]


def test_roundtrip_preserves_explicit_generator_context_and_target(tmp_path) -> None:
    source = tmp_path / "source.py"
    target = tmp_path / "target.py"
    registry = _generator_registry()
    runtime = _runtime(_LLM([]), registry, _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-generator-explicit", description="Use explicit generator inputs"),
        tools=[],
        project_path=str(tmp_path),
        read_scope=[str(source)],
        write_scope=[str(target)],
    )
    provider_call = runner._prepare_provider_tool_call(
        LLMToolCall(
            id="generator-explicit-call",
            function=LLMToolFunctionCall(
                name="code_unit_generator",
                arguments=json.dumps(
                    {
                        "task_description": "Add one regression test",
                        "language": "python",
                        "file_path": str(target),
                        "context": "explicit provider context",
                    }
                ),
            ),
        )
    )
    admission = admit_provider_tool_calls(
        [provider_call],
        task_id="task-generator-explicit",
        session_id="session-generator-explicit",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(),
        user_confirmed=True,
        project_path=str(tmp_path),
        read_scope=[str(source)],
        write_scope=[str(target)],
    )[0]

    bound = runner._bind_project_path(admission, round_index=1)

    assert bound.status == "admitted"
    assert bound.selection is not None
    assert bound.selection.input_metadata.file_path == str(target)
    assert bound.selection.input_metadata.context == "explicit provider context"


def test_historical_compaction_keeps_bounded_preview_when_lineage_envelope_is_large() -> None:
    runtime = _runtime(_LLM([]), _registry(), _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-historical-preview", description="Read calculator"),
        tools=[],
    )
    payload = {
        "success": True,
        "tool": "file_reader",
        "result": {
            "kind": "file_artifact",
            "file_path": "calculator.py",
            "size_bytes": 68,
            "file_type": "code",
            "lines_read": 5,
            "total_lines": 5,
            "truncated": False,
            "content_type": "text/plain",
            "evidence_status": "complete",
            "projection_status": "inline",
            "preview": "def divide(a, b):\n    return a / b\n",
        },
        "artifact_ref": {
            "kind": "MetadataKind.FILE_ARTIFACT",
            "source_id": "phase35-controlled-mutation-20260807T031819-1-ca104bd6:r1:c1",
            "provider_call_id": "call_00_AQqio46ZEKlVxyBYCqC64143",
            "sha256": "b" * 64,
            "bytes": 68,
            "chars": 68,
            "file_path": "calculator.py",
        },
        "error_type": None,
        "error": None,
        "suggested_recovery": None,
    }

    compacted = json.loads(
        runner._fit_tool_result_payload(
            payload,
            limit=runner._HISTORICAL_TOOL_RESULT_CHARS,
        )
    )

    assert compacted["result"]["truncated"] is False
    assert compacted["result"]["preview"]
    assert "divide" in compacted["result"]["preview"]


def test_roundtrip_allows_new_page_when_complete_artifact_projection_is_bounded(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-bounded-page-1"),
            LLMResponse(
                content="",
                reasoning_content="I need a page because the complete artifact is only a bounded preview.",
                tool_calls=[
                    LLMToolCall(
                        id="ds-bounded-page-2",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments='{"file_path":"README.md","offset":100,"max_lines":20}',
                        ),
                    )
                ],
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="The requested page was read and the file evidence is sufficient.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _LargeCompleteFileExecutor())
    target = tmp_path / "README.md"
    executor = runtime.tool_executor
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-bounded-page", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=5,
        project_path=str(tmp_path),
        read_scope=[str(target)],
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is True
    assert executor.calls == 2
    assert result.evidence_coverage.bounded_projection_paths == (str(target.resolve()),)
    assert result.evidence_coverage.finalization_requests == 0


def test_roundtrip_accepts_declared_bounded_window_as_sufficient_evidence(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    target = tmp_path / "README.md"
    target.write_text("line-0\nline-1\nline-2\nline-3\nline-4\n", encoding="utf-8")

    responses = [
        LLMResponse(
            content="",
            reasoning_content="I will use the declared adaptive window.",
            tool_calls=[
                LLMToolCall(
                    id="ds-declared-window-1",
                    function=LLMToolFunctionCall(
                        name="file_reader",
                        arguments=json.dumps(
                            {
                                "file_path": "README.md",
                                "read_mode": "adaptive",
                                "offset": 3,
                                "max_lines": 2,
                            }
                        ),
                    ),
                )
            ],
            model="deepseek-v4-flash",
            provider="deepseek",
            finish_reason="tool_calls",
        ),
        _tool_response(call_id="ds-declared-window-duplicate"),
        LLMResponse(
            content="The declared bounded evidence is sufficient.",
            model="deepseek-v4-flash",
            provider="deepseek",
            finish_reason="stop",
        ),
    ]
    llm = _LLM(responses)

    class _BoundedExecutor:
        def execute_single(self, selection, context=None):
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="file_reader",
                    status=ResultStatus.SUCCESS,
                    result=FileArtifactMetadata(
                        file_path=str(target),
                        content="line-3\nline-4\n",
                        lines_read=2,
                        total_lines=5,
                        truncated=True,
                        read_window=FileReadWindow(
                            read_mode="adaptive",
                            offset=3,
                            max_lines=2,
                        ),
                    ),
                ),
                error=None,
            )

    runtime = _runtime(llm, _registry(), _BoundedExecutor())
    task = Task(
        id="task-declared-bounded-window",
        description="Read the declared adaptive window",
        read_files=[str(target)],
        read_windows=[
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=3,
                max_lines=2,
            )
        ],
    )
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        task,
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=5,
        project_path=str(tmp_path),
        read_scope=[str(target)],
        bounded_read_windows=task.read_windows,
    ).run([LLMMessage(role="user", content="Read the declared adaptive window")])

    assert result.success is True, result.error_message
    assert result.evidence_coverage.completed_read_paths == (str(target.resolve()),)
    assert result.evidence_coverage.bounded_projection_paths == (str(target.resolve()),)
    assert result.evidence_coverage.page_cap_paths == ()
    assert result.evidence_coverage.finalization_requests == 1
    first_tool_message = next(
        message
        for message in llm.requests[1].messages
        if message.role == "tool" and message.tool_call_id == "ds-declared-window-1"
    )
    projected_payload = json.loads(first_tool_message.content)
    assert projected_payload["result"]["evidence_status"] == "complete"
    assert projected_payload["result"]["projection_status"] == "bounded_window"
    assert projected_payload["result"]["read_window"] == {
        "read_mode": "adaptive",
        "offset": 3,
        "max_lines": 2,
    }


def test_mutation_route_emits_read_evidence_ready_before_next_provider_decision(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    target = tmp_path / "README.md"
    target.write_text("line-0\nline-1\nline-2\nline-3\nline-4\n", encoding="utf-8")
    llm = _LLM(
        [
            LLMResponse(
                content="",
                reasoning_content="I will use the declared window.",
                tool_calls=[
                    LLMToolCall(
                        id="ds-ready-read-1",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments=json.dumps(
                                {
                                    "file_path": "README.md",
                                    "read_mode": "adaptive",
                                    "offset": 3,
                                    "max_lines": 2,
                                }
                            ),
                        ),
                    )
                ],
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="The evidence handoff is clear.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )

    class _BoundedExecutor:
        def execute_single(self, selection, context=None):
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="file_reader",
                    status=ResultStatus.SUCCESS,
                    result=FileArtifactMetadata(
                        file_path=str(target),
                        content="line-3\nline-4\n",
                        lines_read=2,
                        total_lines=5,
                        truncated=True,
                        read_window=FileReadWindow(
                            read_mode="adaptive",
                            offset=3,
                            max_lines=2,
                        ),
                    ),
                ),
                error=None,
            )

    registry = _registry()
    registry.register(CODE_UNIT_GENERATOR_DEFINITION, lambda _input: None)
    registry.register(FILE_PATCH_WRITER_DEFINITION, lambda _input: None)
    runtime = _runtime(llm, registry, _BoundedExecutor())
    task = Task(
        id="task-read-to-mutation-handoff",
        description="Read the declared window and add one bounded test.",
        kind="implement",
        read_files=[str(target)],
        read_windows=[
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=3,
                max_lines=2,
            )
        ],
        write_files=[str(target)],
    )
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        task,
        tools=build_provider_tool_definitions(
            runtime.tool_registry,
            ["file_reader", "code_unit_generator", "file_patch_writer"],
        ),
        max_rounds=3,
        project_path=str(tmp_path),
        read_scope=[str(target)],
        write_scope=[str(target)],
        bounded_read_windows=task.read_windows,
        allow_mutations=True,
        user_confirmed=True,
    ).run([LLMMessage(role="user", content="Read the declared window and add one bounded test.")])

    assert result.success is True
    assert len(llm.requests) == 2
    handoff_messages = [
        message
        for message in llm.requests[1].messages
        if message.role == "user" and "READ_EVIDENCE_READY" in message.content
    ]
    assert len(handoff_messages) == 1
    assert "Do not call file_reader again" in handoff_messages[0].content
    assert str(target.resolve()) in handoff_messages[0].content


def test_result_projection_exposes_declared_window_as_complete_bounded_evidence(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    target = tmp_path / "README.md"
    runtime = _runtime(_LLM([]), _registry(), _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-declared-projection", description="Read the declared window"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        project_path=str(tmp_path),
        bounded_read_windows=[
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=3,
                max_lines=2,
            )
        ],
    )
    call = LLMToolCall(
        id="ds-declared-projection",
        function=LLMToolFunctionCall(
            name="file_reader",
            arguments=json.dumps(
                {
                    "file_path": "README.md",
                    "read_mode": "adaptive",
                    "offset": 3,
                    "max_lines": 2,
                }
            ),
        ),
    )

    projected, _ = runner._result_projection(
        FileArtifactMetadata(
            file_path=str(target),
            content="line-3\nline-4\n",
            lines_read=2,
            total_lines=5,
            truncated=True,
            read_window=FileReadWindow(
                read_mode="adaptive",
                offset=3,
                max_lines=2,
            ),
        ),
        source_id="declared-projection-result",
        provider_call_id=call.id,
        call=call,
    )

    assert projected["evidence_status"] == "complete"
    assert projected["projection_status"] == "bounded_window"
    assert projected["read_window"] == {
        "read_mode": "adaptive",
        "offset": 3,
        "max_lines": 2,
    }


def test_result_projection_keeps_undeclared_partial_page_as_partial_preview(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    target = tmp_path / "README.md"
    runtime = _runtime(_LLM([]), _registry(), _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-undeclared-projection", description="Read a partial page"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        project_path=str(tmp_path),
    )
    call = LLMToolCall(
        id="ds-undeclared-projection",
        function=LLMToolFunctionCall(
            name="file_reader",
            arguments=json.dumps(
                {
                    "file_path": "README.md",
                    "read_mode": "adaptive",
                    "offset": 3,
                    "max_lines": 2,
                }
            ),
        ),
    )

    projected, _ = runner._result_projection(
        FileArtifactMetadata(
            file_path=str(target),
            content="line-3\nline-4\n",
            lines_read=2,
            total_lines=5,
            truncated=True,
            read_window=FileReadWindow(
                read_mode="adaptive",
                offset=3,
                max_lines=2,
            ),
        ),
        source_id="undeclared-projection-result",
        provider_call_id=call.id,
        call=call,
    )

    assert projected["evidence_status"] == "partial"
    assert projected["projection_status"] == "bounded_preview"


def test_declared_window_matching_supports_two_exact_windows_for_one_path(tmp_path) -> None:
    target = tmp_path / "target.py"
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), _registry(), _Executor())),
        Task(id="task-two-windows", description="Read header and body windows"),
        tools=build_provider_tool_definitions(
            _registry(),
            ["file_reader"],
        ),
        project_path=str(tmp_path),
        read_scope=[str(target)],
        bounded_read_windows=[
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=0,
                max_lines=20,
            ),
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=120,
                max_lines=40,
            ),
        ],
    )

    def call(call_id: str, offset: int, max_lines: int) -> LLMToolCall:
        return LLMToolCall(
            id=call_id,
            function=LLMToolFunctionCall(
                name="file_reader",
                arguments=json.dumps(
                    {
                        "file_path": str(target),
                        "read_mode": "adaptive",
                        "offset": offset,
                        "max_lines": max_lines,
                    }
                ),
            ),
        )

    header_call = call("header", 0, 20)
    body_call = call("body", 120, 40)
    wrong_call = call("wrong", 240, 40)

    assert runner._declared_window_mismatch(header_call) is None
    assert runner._declared_window_mismatch(body_call) is None
    assert runner._declared_window_mismatch(wrong_call) is not None
    assert runner._matches_declared_window(
        body_call,
        {
            "read_window": {
                "read_mode": "adaptive",
                "offset": 120,
                "max_lines": 40,
            }
        },
        str(target),
    ) is True


def test_declared_read_completion_waits_for_all_windows_of_one_path(tmp_path) -> None:
    target = tmp_path / "target.py"
    registry = _generator_registry()
    registry.register(FILE_PATCH_WRITER_DEFINITION, lambda _input: None)
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), registry, _Executor())),
        Task(id="task-window-coverage", description="Read header and body windows"),
        tools=build_provider_tool_definitions(
            registry,
            ["file_reader", "code_unit_generator", "file_patch_writer"],
        ),
        project_path=str(tmp_path),
        read_scope=[str(target)],
        bounded_read_windows=[
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=0,
                max_lines=20,
            ),
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=120,
                max_lines=40,
            ),
        ],
        allow_mutations=True,
        user_confirmed=True,
    )
    canonical = str(target.resolve())
    runner._completed_read_sources[canonical] = SimpleNamespace(provider_call_id="header")
    runner._completed_read_projection[canonical] = "bounded_window"
    runner._completed_declared_windows.add(
        runner._declared_window_key(runner.bounded_read_windows[0])
    )

    assert runner._all_scoped_reads_complete() is False
    assert runner._mutation_evidence_ready() is False

    runner._completed_declared_windows.add(
        runner._declared_window_key(runner.bounded_read_windows[1])
    )
    assert runner._all_scoped_reads_complete() is True
    assert runner._mutation_evidence_ready() is True
    coverage = runner._evidence_coverage().to_json_dict()
    assert coverage["completed_declared_windows"] == [
        {
            "file_path": canonical,
            "read_mode": "adaptive",
            "offset": 0,
            "max_lines": 20,
        },
        {
            "file_path": canonical,
            "read_mode": "adaptive",
            "offset": 120,
            "max_lines": 40,
        },
    ]


def test_generator_context_includes_only_exact_declared_windows(tmp_path) -> None:
    target = tmp_path / "Code" / "tests" / "test_target.py"
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), _registry(), _Executor())),
        Task(id="task-window-grounding", description="Ground a generated test"),
        tools=[],
        project_path=str(tmp_path),
        read_scope=[str(target)],
        bounded_read_windows=[
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=0,
                max_lines=20,
            ),
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=120,
                max_lines=40,
            ),
        ],
    )
    canonical = str(target.resolve())
    runner._completed_read_sources[canonical] = SimpleNamespace(provider_call_id="read-1")
    for spec, provider_call_id in zip(
        runner.bounded_read_windows[:2], ("read-header", "read-body")
    ):
        key = runner._declared_window_key(spec)
        runner._completed_declared_windows.add(key)
        runner._completed_declared_window_attempts[key] = SimpleNamespace(
            provider_call_id=provider_call_id
        )
    runner._completed_page_content[canonical] = [
        {
            "excerpt": (
                "import json\n"
                "from core.provider_tool_roundtrip import ProviderToolRoundTripRunner\n"
            ),
            "offset": 0,
            "max_lines": 20,
            "read_mode": "adaptive",
        },
        {
            "excerpt": "def test_generated():\n    return json.dumps({'ok': True})\n",
            "offset": 120,
            "max_lines": 40,
            "read_mode": "adaptive",
        },
        {
            "excerpt": "from invented.module import NotAllowed\n",
            "offset": 240,
            "max_lines": 40,
            "read_mode": "adaptive",
        },
    ]

    context, source_ids = runner._declared_generator_context()

    assert "MODULE_SYMBOL_CANDIDATE: json" in context
    assert "MODULE_SYMBOL_CANDIDATE: ProviderToolRoundTripRunner" in context
    assert "return json.dumps" in context
    assert "NotAllowed" not in context
    assert len(source_ids) == 2
    assert all("page-" in source_id for source_id in source_ids)


def test_grounded_generator_keeps_unbound_fixture_fail_closed() -> None:
    context = """AUTHORITATIVE DECLARED READ EVIDENCE
Use only names, imports, APIs, and paths visible in these sections.
SOURCE_ID: declared-read:test_target.py:header
FILE_PATH: test_target.py
EVIDENCE_STATUS: complete
PROJECTION_STATUS: bounded_window
CONTENT:
import json
"""

    with pytest.raises(ValueError, match="tmp_path"):
        _validate_grounded_python_unit(
            "def test_generated():\n    return tmp_path / 'result.txt'\n",
            context,
        )

    _validate_grounded_python_unit(
        "def test_generated(tmp_path):\n    return tmp_path / 'result.txt'\n",
        context,
    )


def test_grounded_generator_rejects_unknown_pytest_fixture_parameter() -> None:
    context = """AUTHORITATIVE DECLARED READ EVIDENCE
Use only names, imports, APIs, and paths visible in these sections.
SOURCE_ID: declared-read:test_target.py:header
FILE_PATH: test_target.py
EVIDENCE_STATUS: complete
PROJECTION_STATUS: bounded_window
CONTENT:
import json
"""

    with pytest.raises(ValueError, match="fixture:runner"):
        _validate_grounded_python_unit(
            "def test_generated(runner):\n    return json.dumps({'ok': True})\n",
            context,
        )


def test_grounded_generator_allows_ordinary_function_parameters() -> None:
    context = """AUTHORITATIVE DECLARED READ EVIDENCE
Use only names, imports, APIs, and paths visible in these sections.
SOURCE_ID: declared-read:module.py:header
FILE_PATH: module.py
EVIDENCE_STATUS: complete
PROJECTION_STATUS: bounded_window
CONTENT:
import json
"""

    _validate_grounded_python_unit(
        "def transform(value):\n    return value + 1\n",
        context,
    )


def test_grounded_generator_allows_source_visible_fixture_parameter() -> None:
    context = """AUTHORITATIVE DECLARED READ EVIDENCE
Use only names, imports, APIs, and paths visible in these sections.
SOURCE_ID: declared-read:test_target.py:header
FILE_PATH: test_target.py
EVIDENCE_STATUS: complete
PROJECTION_STATUS: bounded_window
CONTENT:
import json
def runner():
    return {'ok': True}
"""

    _validate_grounded_python_unit(
        "def test_generated(runner):\n    return json.dumps(runner())\n",
        context,
    )


def test_duplicate_partition_uses_exact_declared_window_identity(tmp_path) -> None:
    target = tmp_path / "target.py"
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), _registry(), _Executor())),
        Task(id="task-window-duplicate-identity", description="Read two windows"),
        tools=build_provider_tool_definitions(_registry(), ["file_reader"]),
        project_path=str(tmp_path),
        read_scope=[str(target)],
        bounded_read_windows=[
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=0,
                max_lines=20,
            ),
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=120,
                max_lines=40,
            ),
        ],
    )
    canonical = str(target.resolve())

    def call(call_id: str, offset: int, max_lines: int) -> LLMToolCall:
        return LLMToolCall(
            id=call_id,
            function=LLMToolFunctionCall(
                name="file_reader",
                arguments=json.dumps(
                    {
                        "file_path": str(target),
                        "read_mode": "adaptive",
                        "offset": offset,
                        "max_lines": max_lines,
                    }
                ),
            ),
        )

    header = call("header", 0, 20)
    body = call("body", 120, 40)
    wrong = call("wrong", 240, 40)
    header_key = runner._declared_window_key(runner.bounded_read_windows[0])
    runner._completed_read_sources[canonical] = SimpleNamespace(provider_call_id="header")
    runner._completed_read_projection[canonical] = "bounded_window"
    runner._completed_declared_windows.add(header_key)
    runner._completed_declared_window_attempts[header_key] = SimpleNamespace(
        provider_call_id="header"
    )

    new_calls, preblocked = runner._partition_duplicate_calls([body], round_index=2)
    assert new_calls == [body]
    assert preblocked == {}

    new_calls, preblocked = runner._partition_duplicate_calls([header], round_index=2)
    assert new_calls == []
    assert preblocked["header"]["error_type"] == "ProviderToolDuplicateAttempt"
    assert preblocked["header"]["previous_call_id"] == "header"

    new_calls, preblocked = runner._partition_duplicate_calls([wrong], round_index=2)
    assert new_calls == []
    assert preblocked["wrong"]["error_type"] == "ProviderToolBoundedWindowMismatch"


def test_module_symbol_candidates_preserve_complete_multiline_imports() -> None:
    source = """from core.provider_tool_roundtrip import (
    ProviderToolRoundTripRunner,
    ProviderToolEvidenceCoverage,
)
from autonomous_iteration.task_models import (
    Task,
)

class _Owner:
"""

    candidates = _module_symbol_candidates(source)

    assert "ProviderToolRoundTripRunner" in candidates
    assert "ProviderToolEvidenceCoverage" in candidates
    assert "Task" in candidates
    assert "_Owner" in candidates


def test_module_callable_candidates_preserve_bounded_construction_signatures() -> None:
    source = """def _runtime(llm, registry, executor):
    return runtime

class _Owner:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
"""

    candidates = _module_callable_candidates(source)

    assert "_runtime(llm, registry, executor)" in candidates
    assert "_Owner(runtime)" in candidates
    assert all(len(candidate) <= 256 for candidate in candidates)


def test_module_callable_candidates_retain_required_prefix_of_long_runner_constructor() -> None:
    source_lines = Path(__file__).resolve().parents[1].joinpath(
        "src", "core", "provider_tool_roundtrip.py"
    ).read_text(encoding="utf-8").splitlines()
    class_start = source_lines.index("class ProviderToolRoundTripRunner:")
    source = "\n".join(source_lines[class_start : class_start + 45]) + "\n"

    candidates = _module_callable_candidates(source)

    assert "ProviderToolRoundTripRunner(owner, task, *, tools, max_rounds=3)" in candidates
    assert all(len(candidate) <= 256 for candidate in candidates)


def test_module_callsite_candidates_preserve_bounded_helper_composition() -> None:
    source = """from core.provider_tool_roundtrip import ProviderToolRoundTripRunner

class _Owner:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

def _runtime(llm, registry, executor):
    return runtime

def _registry():
    return object()

class _Executor:
    pass

class _LLM:
    def __init__(self, responses):
        self.responses = responses

llm = _LLM([])
runtime = _runtime(llm, _registry(), _Executor())
owner = _Owner(runtime)
"""

    candidates = _module_callsite_candidates(source)

    assert "runtime = _runtime(llm, _registry(), _Executor())" in candidates
    assert "owner = _Owner(runtime)" in candidates
    assert all(len(candidate) <= 256 for candidate in candidates)


def test_module_callsite_candidates_fail_closed_for_unknown_or_unsafe_source() -> None:
    assert _module_callsite_candidates("runtime = _runtime(unknown)\n") == ()
    assert _module_callsite_candidates("runtime = factory.build(llm)\n") == ()
    assert _module_callsite_candidates("runtime = _runtime(llm,\n") == ()
    assert _module_callsite_candidates(
        "runtime = _runtime(llm)\n... [source excerpt clipped; 2 lines omitted]"
    ) == ()


def test_module_callsite_candidates_recover_complete_fragments_from_partial_window() -> None:
    source = """def _registry():
    return object()

class _Executor:
    pass

class _Owner:
    def __init__(self, runtime):
        self.runtime = runtime

def _runtime(llm, registry, executor):
    return runtime

llm = _LLM([])
runtime = _runtime(
    llm,
    _registry(),
    _Executor(),
)
owner = _Owner(runtime)
runner = ProviderToolRoundTripRunner(
"""

    candidates = _module_callsite_candidates(source)

    assert "runtime = _runtime(llm, _registry(), _Executor())" in candidates
    assert "owner = _Owner(runtime)" in candidates
    assert not any(candidate.startswith("runner =") for candidate in candidates)


def test_module_callsite_candidates_stop_at_clipping_marker() -> None:
    source = """def _factory(value):
    return value

value = _factory(1)
...[source excerpt clipped; symbol index follows]
value = _factory(2)
"""

    candidates = _module_callsite_candidates(source)

    assert "value = _factory(1)" in candidates
    assert "value = _factory(2)" not in candidates


def test_module_callsite_candidates_bound_count_and_size() -> None:
    source = "\n".join(
        [
            "def _factory(value):",
            "    return value",
            "value = _factory(1)",
        ]
        + [f"value_{index} = _factory(value)" for index in range(100)]
    )

    candidates = _module_callsite_candidates(source)

    assert len(candidates) <= 32
    assert all(len(candidate) <= 256 for candidate in candidates)


def test_module_callable_candidates_fail_closed_for_incomplete_or_clipped_headers() -> None:
    incomplete = "def _runtime(llm, registry,\n"
    clipped = "def _runtime(llm, registry, executor):\n...[source excerpt clipped; symbol index follows]\n"

    assert _module_callable_candidates(incomplete) == ()
    assert _module_callable_candidates(clipped) == ()


def test_generator_writer_handoff_diagnostic_fits_after_history_compaction(monkeypatch, tmp_path) -> None:
    """The repaired handoff keeps required facts while omitting old wire payloads."""
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), _registry(), _Executor())),
        Task(
            id="task-handoff-budget-diagnostic",
            description="Apply one generated test",
            kind="implement",
            write_files=[str(tmp_path / "Code" / "tests" / "test_target.py")],
        ),
        tools=[],
        project_path=str(tmp_path),
    )
    runner.initial_context_candidates = [
        ContextCandidate(
            candidate_id="handoff:task",
            source_id="handoff-task",
            kind=ContextCandidateKind.TASK,
            content="task and immutable scope facts " * 40,
            role="user",
            retention=ContextCandidateRetention.REQUIRED,
            priority=100,
            source_order=0,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            trust=ContextCandidateTrust.AUTHORITATIVE,
        ),
        ContextCandidate(
            candidate_id="handoff:artifact",
            source_id="handoff-artifact",
            kind=ContextCandidateKind.ARTIFACT,
            content="artifact reference and exact validation facts " * 40,
            role="user",
            retention=ContextCandidateRetention.REQUIRED,
            priority=100,
            source_order=1,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            trust=ContextCandidateTrust.DERIVED,
        ),
    ]
    active_call = LLMToolCall(
        id="active-call",
        function=LLMToolFunctionCall(
            name="file_patch_writer",
            arguments='{"file_path":"target.py"}',
        ),
    )
    dynamic_messages = [
        LLMMessage(role="assistant", content="prior generator decision " * 55),
        LLMMessage(role="tool", content="declared read result " * 90, tool_call_id="read-1"),
        LLMMessage(role="assistant", content="", tool_calls=[active_call]),
        LLMMessage(role="tool", content="artifact handoff result " * 20, tool_call_id="write-1"),
    ]

    candidates, structured_messages = runner._initial_context_with_dynamic_messages(
        dynamic_messages
    )
    dynamic = [
        candidate
        for candidate in candidates
        if candidate.candidate_id.startswith("provider:round-message:")
    ]

    assert len(dynamic) == 2
    assert all(candidate.retention == ContextCandidateRetention.REQUIRED for candidate in dynamic)
    compacted_history = [
        candidate
        for candidate in candidates
        if candidate.candidate_id == "provider:round-history-summary"
    ]
    assert len(compacted_history) == 1
    assert compacted_history[0].retention == ContextCandidateRetention.OPTIONAL

    from memory.context_assembly import build_context_candidate_request

    request = build_context_candidate_request(
        _LLM([]),
        candidates=candidates,
        purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
        structured_messages=structured_messages,
    )
    assert request.context_selection.assembly_status == "ready"
    assert not request.context_selection.omitted_required_candidate_ids


def test_generator_writer_handoff_contract_compacts_superseded_wire_history() -> None:
    """The next handoff must retain only the active tool round as raw wire state."""
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), _registry(), _Executor())),
        Task(id="task-handoff-retention", description="Apply one generated test"),
        tools=[],
    )
    old_payload = "old completed tool payload " * 120
    old_call = LLMToolCall(
        id="old-call",
        function=LLMToolFunctionCall(
            name="file_reader",
            arguments='{"file_path":"old.py"}',
        ),
    )
    active_call = LLMToolCall(
        id="active-call",
        function=LLMToolFunctionCall(
            name="file_patch_writer",
            arguments='{"file_path":"target.py"}',
        ),
    )
    dynamic_messages = [
        LLMMessage(role="assistant", content="", tool_calls=[old_call]),
        LLMMessage(role="tool", content=old_payload, tool_call_id="old-call"),
        LLMMessage(role="assistant", content="", tool_calls=[active_call]),
        LLMMessage(role="tool", content="active artifact reference", tool_call_id="active-call"),
        LLMMessage(role="user", content="READ_EVIDENCE_READY: use the artifact_ref and validate exactly once"),
    ]

    candidates, structured_messages = runner._initial_context_with_dynamic_messages(
        dynamic_messages
    )

    assert any(
        message.role == "assistant"
        and any(call.id == "active-call" for call in message.tool_calls)
        for message in structured_messages
    )
    assert any(
        message.role == "tool" and message.tool_call_id == "active-call"
        for message in structured_messages
    )
    assert not any(old_payload in (message.content or "") for message in structured_messages)
    compacted_history = [
        candidate
        for candidate in candidates
        if candidate.kind == ContextCandidateKind.PREVIOUS_OUTPUT
        and candidate.retention != ContextCandidateRetention.REQUIRED
    ]
    assert compacted_history
    assert all(
        candidate.retention in {
            ContextCandidateRetention.PREFERRED,
            ContextCandidateRetention.OPTIONAL,
        }
        for candidate in compacted_history
    )


def test_generator_writer_handoff_does_not_rewrap_existing_history_summary() -> None:
    """A continuation must preserve one bounded summary instead of summarizing it again."""
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), _registry(), _Executor())),
        Task(id="task-handoff-summary-idempotence", description="Apply one generated test"),
        tools=[],
    )
    active_call = LLMToolCall(
        id="active-summary-call",
        function=LLMToolFunctionCall(
            name="file_patch_writer",
            arguments='{"file_path":"target.py"}',
        ),
    )
    dynamic_messages = [
        LLMMessage(role="assistant", content="old provider decision"),
        LLMMessage(role="tool", content="old provider result", tool_call_id="old-call"),
        LLMMessage(role="assistant", content="", tool_calls=[active_call]),
        LLMMessage(role="tool", content="active artifact reference", tool_call_id="active-summary-call"),
        LLMMessage(role="user", content="READ_EVIDENCE_READY: use the artifact_ref"),
    ]

    first_candidates, first_messages = runner._initial_context_with_dynamic_messages(
        dynamic_messages
    )
    first_summary = next(
        candidate for candidate in first_candidates
        if candidate.candidate_id == "provider:round-history-summary"
    )
    second_candidates, second_messages = runner._initial_context_with_dynamic_messages(
        first_messages
    )
    second_summaries = [
        candidate for candidate in second_candidates
        if candidate.candidate_id == "provider:round-history-summary"
    ]

    assert len(second_summaries) == 1
    assert second_summaries[0].content == first_summary.content
    assert len({candidate.candidate_id for candidate in second_candidates}) == len(second_candidates)
    assert [message.role for message in second_messages] == [
        candidate.role for candidate in second_candidates
    ]
    assert any(
        message.role == "assistant"
        and any(call.id == "active-summary-call" for call in message.tool_calls)
        for message in second_messages
    )


def test_generator_writer_handoff_replays_raw_overflow_shape_with_bounded_active_wire() -> None:
    """The frozen H8-R2T-sized assistant turn remains fit after handoff compaction."""
    llm = _LLM([])
    llm.settings.context_max_prompt_tokens = 12_288
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(llm, _registry(), _Executor())),
        Task(id="task-handoff-raw-shaped", description="Apply one generated test"),
        tools=[],
        context_max_prompt_tokens=12_288,
    )
    runner.initial_context_candidates = [
        ContextCandidate(
            candidate_id="raw-shaped:task",
            source_id="raw-shaped-task",
            kind=ContextCandidateKind.TASK,
            content="Preserve task, scope, artifact, and exact validation facts.",
            role="user",
            retention=ContextCandidateRetention.REQUIRED,
            priority=100,
            source_order=0,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            trust=ContextCandidateTrust.AUTHORITATIVE,
        ),
    ]
    old_messages = [
        LLMMessage(role="assistant", content=f"old-{index}" * 500)
        for index in range(11)
    ]
    active_call = LLMToolCall(
        id="raw-shaped-generator",
        function=LLMToolFunctionCall(
            name="code_unit_generator",
            arguments='{"file_path":"target.py"}',
        ),
    )
    dynamic_messages = [
        *old_messages,
        LLMMessage(role="assistant", content="x" * 10_453, tool_calls=[active_call]),
        LLMMessage(role="tool", content="artifact_ref:generated-test", tool_call_id="raw-shaped-generator"),
    ]

    candidates, structured_messages = runner._initial_context_with_dynamic_messages(
        dynamic_messages
    )

    from memory.context_assembly import build_context_candidate_request

    request = build_context_candidate_request(
        llm,
        candidates=candidates,
        purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
        max_tokens=1_600,
        structured_messages=structured_messages,
    )
    assert request.context_selection.assembly_status == "ready"
    assert not request.context_selection.omitted_required_candidate_ids
    assert any(
        message.role == "assistant"
        and any(call.id == "raw-shaped-generator" for call in message.tool_calls)
        for message in request.messages
    )
    assert any(message.tool_call_id == "raw-shaped-generator" for message in request.messages)


def test_roundtrip_exposes_bounded_context_selection_diagnostics(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-diagnostics-read"),
            LLMResponse(
                content="README inspected.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _Executor())
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-context-diagnostics", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=2,
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is True
    assert len(result.request_diagnostics) == 2
    assert [item["round_index"] for item in result.request_diagnostics] == [1, 2]
    for item in result.request_diagnostics:
        selection = item["context_selection"]
        assert selection["assembly_status"] == "ready"
        assert "candidate_decisions" in selection
        assert item["selected_candidate_ids"]
        assert item["message_content_chars"]


def test_module_callable_candidates_drop_oversized_signatures() -> None:
    parameters = ", ".join(f"value_{index}" for index in range(80))
    source = f"def oversized({parameters}):\n    return None\n"

    assert _module_callable_candidates(source) == ()


def test_structured_candidates_fail_closed_for_incomplete_imports_and_markers() -> None:
    incomplete = """from core.provider_tool_roundtrip import (
    ProviderToolRoundTripRunner,
"""

    assert "ProviderToolRoundTripRunner" not in _module_symbol_candidates(incomplete)
    assert _module_import_candidates(incomplete) == ()

    clipped = """from core.provider_tool_roundtrip import (
    ProviderToolRoundTripRunner,
...[source excerpt clipped; symbol index follows]
"""

    assert _module_symbol_candidates(clipped) == ()
    assert _module_import_candidates(clipped) == ()


def test_generator_context_preserves_structured_candidates_when_excerpt_is_clipped(tmp_path) -> None:
    target = tmp_path / "Code" / "tests" / "test_target.py"
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), _registry(), _Executor())),
        Task(id="task-structured-grounding", description="Use structured evidence"),
        tools=[],
        project_path=str(tmp_path),
        read_scope=[str(target)],
        bounded_read_windows=[
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=0,
                max_lines=320,
            )
        ],
    )
    canonical = str(target.resolve())
    runner._completed_read_sources[canonical] = SimpleNamespace(provider_call_id="header")
    runner._completed_page_content[canonical] = [
        {
            "excerpt": "...[source excerpt clipped; symbol index follows]...",
            "module_import_candidates": ["core.provider_tool_roundtrip"],
            "module_symbol_candidates": [
                "ProviderToolRoundTripRunner",
                "ProviderToolEvidenceCoverage",
                "Task",
                "_Owner",
                "_runtime",
            ],
            "module_callable_candidates": ["_runtime(llm, registry, executor)"],
            "module_callsite_candidates": [
                "runtime = _runtime(llm, _registry(), _Executor())",
                "owner = _Owner(runtime)",
            ],
            "offset": 0,
            "max_lines": 320,
            "read_mode": "adaptive",
        }
    ]
    header_key = runner._declared_window_key(runner.bounded_read_windows[0])
    runner._completed_declared_windows.add(header_key)
    runner._completed_declared_window_attempts[header_key] = SimpleNamespace(
        provider_call_id="header"
    )

    context, source_ids = runner._declared_generator_context()

    assert "MODULE_IMPORT_CANDIDATE: core.provider_tool_roundtrip" in context
    assert "MODULE_SYMBOL_CANDIDATE: ProviderToolRoundTripRunner" in context
    assert "MODULE_SYMBOL_CANDIDATE: _runtime" in context
    assert "MODULE_CALLABLE_CANDIDATE: _runtime(llm, registry, executor)" in context
    assert "MODULE_CALLSITE_HINT: runtime = _runtime(llm, _registry(), _Executor())" in context
    assert "MODULE_CALLSITE_HINT: owner = _Owner(runtime)" in context
    assert "source excerpt clipped" in context
    assert len(source_ids) == 1


def test_record_attempts_projects_bounded_runner_constructor_from_declared_window(tmp_path) -> None:
    source_path = tmp_path / "Code" / "src" / "core" / "provider_tool_roundtrip.py"
    source_path.parent.mkdir(parents=True)
    full_source = Path(__file__).resolve().parents[1].joinpath(
        "src", "core", "provider_tool_roundtrip.py"
    ).read_text(encoding="utf-8")
    source_lines = full_source.splitlines()
    class_start = source_lines.index("class ProviderToolRoundTripRunner:")
    max_lines = 45
    source_path.write_text(full_source, encoding="utf-8")
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), _registry(), _Executor())),
        Task(id="task-constructor-projection", description="Use the runner constructor"),
        tools=build_provider_tool_definitions(_registry(), ["file_reader"]),
        read_scope=[str(source_path)],
        bounded_read_windows=[
            FileReadWindowSpec(
                file_path=str(source_path),
                read_mode="adaptive",
                offset=class_start,
                max_lines=max_lines,
            )
        ],
    )
    call = LLMToolCall(
        id="constructor-window",
        function=LLMToolFunctionCall(
            name="file_reader",
            arguments=json.dumps(
                {
                    "file_path": str(source_path),
                    "read_mode": "adaptive",
                    "offset": class_start,
                    "max_lines": max_lines,
                }
            ),
        ),
    )
    content = "\n".join(source_lines[class_start : class_start + max_lines]) + "\n"
    loop_result = SimpleNamespace(
        tool_results=[
            {
                "provider_call_id": "constructor-window",
                "success": True,
                "result": {
                    "kind": "file_artifact",
                    "file_path": str(source_path),
                    "content": content,
                    "lines_read": max_lines,
                    "total_lines": len(source_lines),
                    "truncated": True,
                    "read_window": {
                        "read_mode": "adaptive",
                        "offset": class_start,
                        "max_lines": max_lines,
                    },
                },
            }
        ],
        loop_metadata=SimpleNamespace(recoverable_errors=[]),
    )

    runner._record_attempts([call], [call], loop_result, round_index=1)

    context, source_ids = runner._declared_generator_context()
    assert "MODULE_CALLABLE_CANDIDATE: ProviderToolRoundTripRunner(owner, task, *, tools, max_rounds=3)" in context
    assert source_ids and f"window-adaptive-{class_start}-{max_lines}" in source_ids[0]


def test_record_attempts_renders_partial_window_callsite_hints(tmp_path) -> None:
    target = tmp_path / "Code" / "tests" / "test_provider_tool_roundtrip.py"
    target.parent.mkdir(parents=True)
    source_lines = Path(__file__).read_text(encoding="utf-8").splitlines()
    header = "\n".join(source_lines[:320]) + "\n"
    target.write_text(header + "\n".join(source_lines[320:]), encoding="utf-8")
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), _registry(), _Executor())),
        Task(id="task-record-partial-callsite", description="Use the declared header"),
        tools=build_provider_tool_definitions(_registry(), ["file_reader"]),
        max_rounds=2,
        read_scope=[str(target)],
        bounded_read_windows=[
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=0,
                max_lines=320,
            )
        ],
    )
    call = LLMToolCall(
        id="partial-header",
        function=LLMToolFunctionCall(
            name="file_reader",
            arguments=json.dumps(
                {
                    "file_path": str(target),
                    "read_mode": "adaptive",
                    "offset": 0,
                    "max_lines": 320,
                }
            ),
        ),
    )
    result = {
        "kind": "file_artifact",
        "file_path": str(target),
        "content": header,
        "lines_read": 320,
        "total_lines": len(source_lines),
        "truncated": True,
        "read_window": {
            "read_mode": "adaptive",
            "offset": 0,
            "max_lines": 320,
        },
    }
    loop_result = SimpleNamespace(
        tool_results=[
            {
                "provider_call_id": "partial-header",
                "success": True,
                "result": result,
            }
        ],
        loop_metadata=SimpleNamespace(recoverable_errors=[]),
    )

    runner._record_attempts([call], [call], loop_result, round_index=1)

    canonical = str(target.resolve())
    page = runner._completed_page_content[canonical][0]
    assert page["module_callsite_candidates"]
    context, source_ids = runner._declared_generator_context()
    assert "MODULE_CALLSITE_HINT: owner = _Owner(runtime)" in context
    assert "MODULE_CALLSITE_HINT: runtime = _runtime(llm, registry, executor)" in context
    assert source_ids == [
        f"declared-read:{canonical}:partial-header:page-1:window-adaptive-0-320"
    ]


def test_record_attempts_preserves_callsite_hints_across_multiwindow_clipping(tmp_path) -> None:
    source_specs = (
        ("Code/src/core/provider_tool_roundtrip.py", 1290, 80),
        ("Code/src/tools/file_reader.py", 160, 70),
        ("Code/tests/test_provider_tool_roundtrip.py", 0, 320),
        ("Code/tests/test_provider_tool_roundtrip.py", 1240, 120),
    )
    source_root = Path(__file__).resolve().parents[2]
    target_root = tmp_path / "project"
    target_paths: dict[str, Path] = {}
    read_scope: list[str] = []
    windows: list[FileReadWindowSpec] = []
    excerpts: list[tuple[Path, int, int, str]] = []
    for relative_path, offset, max_lines in source_specs:
        target = target_root / relative_path
        if relative_path not in target_paths:
            target.parent.mkdir(parents=True, exist_ok=True)
            full_source = (source_root / relative_path).read_text(encoding="utf-8")
            target.write_text(full_source, encoding="utf-8")
            target_paths[relative_path] = target
            read_scope.append(str(target))
        source_lines = target_paths[relative_path].read_text(encoding="utf-8").splitlines()
        excerpts.append((target_paths[relative_path], offset, max_lines, "\n".join(source_lines[offset : offset + max_lines]) + "\n"))
        windows.append(
            FileReadWindowSpec(
                file_path=str(target_paths[relative_path]),
                read_mode="adaptive",
                offset=offset,
                max_lines=max_lines,
            )
        )

    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), _registry(), _Executor())),
        Task(id="task-record-multiwindow-callsite", description="Use all declared windows"),
        tools=build_provider_tool_definitions(_registry(), ["file_reader"]),
        max_rounds=8,
        read_scope=read_scope,
        bounded_read_windows=windows,
    )
    for index, (target, offset, max_lines, content) in enumerate(excerpts):
        call = LLMToolCall(
            id=f"window-{index}",
            function=LLMToolFunctionCall(
                name="file_reader",
                arguments=json.dumps(
                    {
                        "file_path": str(target),
                        "read_mode": "adaptive",
                        "offset": offset,
                        "max_lines": max_lines,
                    }
                ),
            ),
        )
        loop_result = SimpleNamespace(
            tool_results=[
                {
                    "provider_call_id": f"window-{index}",
                    "success": True,
                    "result": {
                        "kind": "file_artifact",
                        "file_path": str(target),
                        "content": content,
                        "lines_read": max_lines,
                        "total_lines": len(target.read_text(encoding="utf-8").splitlines()),
                        "truncated": True,
                        "read_window": {
                            "read_mode": "adaptive",
                            "offset": offset,
                            "max_lines": max_lines,
                        },
                    },
                }
            ],
            loop_metadata=SimpleNamespace(recoverable_errors=[]),
        )
        runner._record_attempts([call], [call], loop_result, round_index=index + 1)

    context, _source_ids = runner._declared_generator_context()
    assert "MODULE_CALLSITE_HINT: owner = _Owner(runtime)" in context
    # The same four declared windows expose a complete, source-linked
    # no-fixture construction path for a small regression test.  Keep this
    # assertion tied to the actual production recording path so a future
    # prompt-fitting change cannot silently leave only declarations while
    # dropping the usable construction recipe.
    assert "MODULE_SYMBOL_CANDIDATE: ProviderToolRoundTripRunner" in context
    assert "MODULE_SYMBOL_CANDIDATE: _Owner" in context
    assert "MODULE_SYMBOL_CANDIDATE: _Executor" in context
    assert "MODULE_SYMBOL_CANDIDATE: _runtime" in context
    assert "MODULE_CALLSITE_HINT: runtime = _runtime(llm, registry, executor)" in context
    assert "MODULE_CALLSITE_HINT: owner = _Owner(runtime)" in context
    # The target error token is task data, not a source-backed Python symbol;
    # the canary task must carry it as a quoted error_type string instead.
    assert "MODULE_SYMBOL_CANDIDATE: ProviderToolBoundedWindowMismatch" not in context
    _validate_grounded_python_unit(
        "def test_generated():\n"
        "    registry = _registry()\n"
        "    executor = _Executor()\n"
        "    runtime = _runtime(None, registry, executor)\n"
        "    owner = _Owner(runtime)\n"
        "    error_type = 'ProviderToolBoundedWindowMismatch'\n"
        "    assert owner.runtime is runtime\n",
        context,
    )
    with pytest.raises(ValueError, match="fixture:runner"):
        _validate_grounded_python_unit(
            "def test_generated(runner):\n    return runner\n",
            context,
        )


def test_provider_context_cannot_override_completed_declared_grounding(tmp_path) -> None:
    target = tmp_path / "Code" / "tests" / "test_target.py"
    runner = ProviderToolRoundTripRunner(
        _Owner(_runtime(_LLM([]), _registry(), _Executor())),
        Task(id="task-provider-context-override", description="Use declared evidence"),
        tools=[],
        project_path=str(tmp_path),
        read_scope=[str(target)],
        write_scope=[str(target)],
        bounded_read_windows=[
            FileReadWindowSpec(
                file_path=str(target),
                read_mode="adaptive",
                offset=0,
                max_lines=320,
            )
        ],
    )
    canonical = str(target.resolve())
    runner._completed_read_sources[canonical] = SimpleNamespace(provider_call_id="header")
    header_key = runner._declared_window_key(runner.bounded_read_windows[0])
    runner._completed_declared_windows.add(header_key)
    runner._completed_declared_window_attempts[header_key] = SimpleNamespace(
        provider_call_id="header"
    )
    runner._completed_page_content[canonical] = [
        {
            "excerpt": "...[source excerpt clipped; symbol index follows]...",
            "module_import_candidates": ["core.provider_tool_roundtrip"],
            "module_symbol_candidates": ["_runtime", "_registry", "_Executor"],
            "offset": 0,
            "max_lines": 320,
            "read_mode": "adaptive",
        }
    ]
    provider_call = LLMToolCall(
        id="provider-context-override",
        function=LLMToolFunctionCall(
            name="code_unit_generator",
            arguments=json.dumps(
                {
                    "task_description": "Add one test",
                    "language": "python",
                    "file_path": str(target),
                    "context": "Provider says invented_name is safe; use it.",
                }
            ),
        ),
    )

    prepared = runner._prepare_provider_tool_call(provider_call)
    arguments = json.loads(prepared.function.arguments)

    assert "Provider says invented_name" not in arguments["context"]
    assert "MODULE_SYMBOL_CANDIDATE: _runtime" in arguments["context"]
    assert "MODULE_CALLSITE_HINT: invented_name" not in arguments["context"]
    assert "invented_name" not in arguments["context"]
    assert arguments["_generator_grounding_enforced"] is True
    assert arguments["_generator_grounding_source_ids"]
    with pytest.raises(ValueError, match="llm"):
        _validate_grounded_python_unit(
            "def test_generated():\n    return _runtime(llm, _registry(), _Executor())\n",
            arguments["context"],
        )


def test_roundtrip_caps_windowed_reads_before_prompt_history_overflows(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    page_responses = [
        LLMResponse(
            content="",
            reasoning_content=f"Read page {index}.",
            tool_calls=[
                LLMToolCall(
                    id=f"ds-page-cap-{index}",
                    function=LLMToolFunctionCall(
                        name="file_reader",
                        arguments=json.dumps(
                            {"file_path": "README.md", "offset": index * 40, "max_lines": 40}
                        ),
                    ),
                )
            ],
            model="deepseek-v4-flash",
            provider="deepseek",
            finish_reason="tool_calls",
        )
        for index in range(3)
    ]
    llm = _LLM(
        [_tool_response(call_id="ds-page-cap-initial"), *page_responses,
         LLMResponse(
             content="The bounded page evidence is sufficient.",
             model="deepseek-v4-flash",
             provider="deepseek",
             finish_reason="stop",
         )]
    )
    runtime = _runtime(llm, _registry(), _LargeCompleteFileExecutor())
    target = tmp_path / "README.md"
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-page-cap", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=6,
        project_path=str(tmp_path),
        read_scope=[str(target)],
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is True
    assert result.evidence_coverage.page_reads_by_path == ((str(target.resolve()), 3),)
    assert result.evidence_coverage.page_cap_paths == (str(target.resolve()),)
    assert result.evidence_coverage.page_read_cap == 3
    assert result.evidence_coverage.finalization_requests == 1
    assert not any(attempt.error_type == "ProviderToolEvidencePageCap" for attempt in result.attempts)
    assert [message.role for message in llm.requests[-1].messages][-2:] == ["user", "user"]
    assert all(message.role != "tool" for message in llm.requests[-1].messages)
    assert llm.requests[-1].tools == []
    assert "source page 2" in llm.requests[-1].messages[-1].content


def test_roundtrip_reports_budget_unavailable_when_page_cap_consumes_last_round(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-page-budget-initial"),
            LLMResponse(
                content="",
                reasoning_content="Read one bounded page.",
                tool_calls=[
                    LLMToolCall(
                        id="ds-page-budget-page",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments='{"file_path":"README.md","offset":40,"max_lines":40}',
                        ),
                    )
                ],
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="tool_calls",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _LargeCompleteFileExecutor())
    target = tmp_path / "README.md"
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-page-budget", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=2,
        project_path=str(tmp_path),
        read_scope=[str(target)],
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is False
    assert result.error_message == "ProviderToolFinalizationBudgetUnavailable"


def test_roundtrip_runner_preserves_mixed_batch_provider_ids_on_recovery(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    first_response = LLMResponse(
        content="",
        reasoning_content="I will inspect both paths.",
        tool_calls=[
            LLMToolCall(
                id="ds-batch-ok",
                function=LLMToolFunctionCall(name="file_reader", arguments='{"file_path":"README.md"}'),
            ),
            LLMToolCall(
                id="ds-batch-recover",
                function=LLMToolFunctionCall(name="file_reader", arguments='{"file_path":"."}'),
            ),
        ],
        model="deepseek-v4-flash",
        provider="deepseek",
        finish_reason="tool_calls",
    )
    llm = _LLM(
        [
            first_response,
            LLMResponse(
                content="The first read succeeded; I will correct the second path.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _MixedExecutor())
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-mixed-batch", description="Inspect both paths"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=2,
    ).run([LLMMessage(role="user", content="Inspect both paths")])

    assert result.success is True
    assert len(llm.requests) == 2
    tool_messages = llm.requests[1].messages[-2:]
    assert [message.tool_call_id for message in tool_messages] == ["ds-batch-ok", "ds-batch-recover"]
    assert '"success":true' in tool_messages[0].content
    assert "FileReaderDirectoryPath" in tool_messages[1].content
    assert result.tool_loop_results[0].loop_metadata.recoverable_errors[0].provider_call_id == "ds-batch-recover"


def test_tool_result_projection_bounds_content_and_keeps_artifact_lineage() -> None:
    runtime = _runtime(_LLM([]), _registry(), _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-projection", description="Read a large file"),
        tools=[],
    )
    projection, artifact_ref = runner._result_projection(
        TextArtifactMetadata(content="x" * 5000, title="large.txt"),
        source_id="project-call-1",
        provider_call_id="provider-call-1",
    )

    assert len(projection["preview"]) <= 480
    assert "content" not in projection
    assert artifact_ref is not None
    assert artifact_ref["source_id"] == "project-call-1"
    assert artifact_ref["provider_call_id"] == "provider-call-1"
    assert artifact_ref["chars"] == 5000


def test_roundtrip_runner_bounds_provider_batch_and_marks_unexecuted_calls(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    first_response = LLMResponse(
        content="",
        reasoning_content="I will inspect the files in a bounded batch.",
        tool_calls=[
            LLMToolCall(
                id=f"ds-fanout-{index}",
                function=LLMToolFunctionCall(name="file_reader", arguments='{"file_path":"README.md"}'),
            )
            for index in range(4)
        ],
        model="deepseek-v4-flash",
        provider="deepseek",
        finish_reason="tool_calls",
    )
    llm = _LLM(
        [
            first_response,
            LLMResponse(
                content="The remaining reads can be handled in a later round.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    executor = _Executor()
    runtime = _runtime(llm, _registry(), executor)
    monkeypatch.setattr(ProviderToolRoundTripRunner, "_max_calls_for_round", lambda *_args: 2)
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-fanout", description="Read files"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=2,
    ).run([LLMMessage(role="user", content="Read files")])

    assert result.success is True
    assert len(executor.calls) == 2
    tool_messages = llm.requests[1].messages[-4:]
    assert [message.tool_call_id for message in tool_messages] == [f"ds-fanout-{index}" for index in range(4)]
    assert "ProviderToolBatchAborted" in tool_messages[2].content
    assert "ProviderToolBatchAborted" in tool_messages[3].content


def test_historical_tool_results_are_compacted_without_changing_ids() -> None:
    runtime = _runtime(_LLM([]), _registry(), _Executor())
    runner = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-history", description="Read files"),
        tools=[],
    )
    old_content = json.dumps(
        {"success": True, "result": {"preview": "old evidence " * 200}},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    latest_content = json.dumps(
        {"success": True, "result": {"preview": "latest evidence"}},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    messages = [
        LLMMessage(role="user", content="Read files"),
        LLMMessage(
            role="assistant",
            content="",
            reasoning_content="old reasoning",
            tool_calls=[
                LLMToolCall(
                    id="old-call",
                    function=LLMToolFunctionCall(name="file_reader", arguments="{}"),
                )
            ],
        ),
        LLMMessage(role="tool", content=old_content, tool_call_id="old-call"),
        LLMMessage(
            role="assistant",
            content="",
            reasoning_content="latest reasoning",
            tool_calls=[
                LLMToolCall(
                    id="latest-call",
                    function=LLMToolFunctionCall(name="file_reader", arguments="{}"),
                )
            ],
        ),
        LLMMessage(role="tool", content=latest_content, tool_call_id="latest-call"),
    ]

    compacted = runner._compact_historical_tool_messages(messages)

    assert compacted[2].tool_call_id == "old-call"
    assert len(compacted[2].content) <= runner._HISTORICAL_TOOL_RESULT_CHARS
    assert compacted[4].tool_call_id == "latest-call"
    assert compacted[4].content == latest_content


def test_roundtrip_large_results_stay_within_context_budget_across_rounds(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-large-1"),
            _tool_response(call_id="ds-large-2"),
            LLMResponse(
                content="The bounded evidence is sufficient.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _LargeExecutor())
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-large-history", description="Read large evidence"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=3,
    ).run([LLMMessage(role="user", content="Read large evidence")])

    assert result.success is True
    assert len(llm.requests) == 3
    assert all(str(request.context_selection.assembly_status) == "ready" for request in llm.requests)
    assert all(len(message.content) <= 1_600 for request in llm.requests[1:] for message in request.messages if message.role == "tool")


def test_roundtrip_runner_stops_on_indeterminate_checkpoint_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-checkpoint-1"),
            LLMResponse(
                content="This response must not be requested.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(
        llm,
        _registry(),
        _Executor(),
    )
    runtime.runtime_controller.observe_tool_result = lambda *_args: False
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-checkpoint", description="Inspect README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=2,
    ).run([LLMMessage(role="user", content="Inspect README")])

    assert result.success is False
    assert len(llm.requests) == 1
    assert result.error_message == (
        "Tool returned, but its result could not be durably recorded before state application."
    )


def test_roundtrip_runner_enforces_bounded_round_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    registry = _registry()
    runtime = _runtime(
        _LLM(
            [
                LLMResponse(
                    content="",
                    reasoning_content="I will inspect the file.",
                    tool_calls=[
                        LLMToolCall(
                            id="ds-read-2",
                            function=LLMToolFunctionCall(
                                name="file_reader",
                                arguments='{"file_path":"README.md"}',
                            ),
                        )
                    ],
                    model="deepseek-v4-flash",
                    provider="deepseek",
                    finish_reason="tool_calls",
                )
            ]
        ),
        registry,
        _Executor(),
    )
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-safe", description="Inspect README"),
        tools=build_provider_tool_definitions(registry, ["file_reader"]),
        max_rounds=1,
    ).run([LLMMessage(role="user", content="Inspect README")])

    assert result.success is False
    assert result.error_message == "provider tool round limit exceeded (1)"


def test_task_executor_provider_entry_is_default_off(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    runtime = _runtime(llm, _registry(), _Executor())
    executor = ToolPlanningTaskExecutor(runtime)
    result = executor.execute_provider_tool_task(
        Task(id="task-disabled", description="Read README"),
        TaskExecutionContext(task=Task(id="task-disabled", description="Read README")),
        tool_names=["file_reader"],
    )

    assert result.status == TaskStatus.FAILED
    assert result.result_metadata.failure.error_type == "ProviderToolExecutionDisabled"
    assert llm.requests == []


def test_task_executor_provider_entry_runs_explicit_read_only_canary(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-task-read-1"),
            LLMResponse(
                content="Read-only canary completed.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _Executor())
    runtime.llm_client.settings.provider_tool_execution_enabled = True
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(id="task-canary", description="Read README.md")
    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(task=task, parent_context={"goal": "Read README.md"}),
        tool_names=["file_reader"],
        max_rounds=2,
    )

    assert result.status == TaskStatus.COMPLETED, (result.error, result.result_metadata.failure if result.result_metadata else None)
    assert result.result_metadata.result.content == "Read-only canary completed."
    assert result.attributes["provider_tool_execution"] is True
    assert len(llm.requests) == 2


def test_task_executor_mutation_prompt_treats_declared_bounded_window_as_complete(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    captured: dict[str, list[LLMMessage]] = {}

    def fake_roundtrip(self, messages):
        captured["messages"] = list(messages)
        return SimpleNamespace(
            success=False,
            error_message="prompt-contract-test-stop",
            final_response=None,
            rounds_used=0,
            tool_loop_results=[],
            evidence_coverage=SimpleNamespace(to_json_dict=lambda: {}),
            attempts=[],
        )

    monkeypatch.setattr(
        "autonomous_iteration.agents.tool_planning_executor.ProviderToolRoundTripRunner.run",
        fake_roundtrip,
    )
    llm = _LLM([])
    llm.settings.provider_tool_execution_enabled = True
    llm.settings.provider_tool_execution_budget_profile = "real_mutation"
    runtime = _runtime(llm, _mutation_registry(), _Executor())
    executor = ToolPlanningTaskExecutor(runtime)
    target = tmp_path / "tests" / "test_calculator.py"
    source = tmp_path / "calculator.py"
    task = Task(
        id="task-bounded-window-prompt-contract",
        description="Modify calculator.py and validate it.",
        kind="implement",
        read_files=[str(source)],
        write_files=[str(target)],
        validation_command="python -m pytest -q tests/test_calculator.py",
    )

    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(
            task=task,
            parent_context={"goal": task.description, "project_path": str(tmp_path)},
        ),
        tool_names=["file_reader"],
        user_confirmed=True,
        allow_mutations=True,
        max_rounds=1,
    )

    assert result.status == TaskStatus.FAILED
    system_prompt = captured["messages"][0].content
    assert "evidence_status=complete" in system_prompt
    assert "projection_status=bounded_window" in system_prompt
    assert "must not reread" in system_prompt


def test_task_executor_requires_projection_flag_for_initial_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    llm.settings.provider_tool_initial_context_projection_enabled = False
    runtime = _runtime(llm, _registry(), _Executor())
    runtime.llm_client.settings.provider_tool_execution_enabled = True
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(id="task-projection-flag", description="Read README.md")
    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(task=task, parent_context={"goal": "Read README.md"}),
        tool_names=["file_reader"],
        max_rounds=2,
        initial_context_candidates=[
            ContextCandidate(
                candidate_id="projection:task",
                kind=ContextCandidateKind.TASK,
                content="Read the declared file only.",
                retention=ContextCandidateRetention.REQUIRED,
                truncation=ContextCandidateTruncation.FORBIDDEN,
                trust=ContextCandidateTrust.AUTHORITATIVE,
            )
        ],
    )

    assert result.status == TaskStatus.FAILED
    assert result.result_metadata.failure.error_type == "ProviderInitialContextProjectionDisabled"
    assert llm.requests == []


def test_task_executor_requires_separate_mutation_projection_flag(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    llm.settings.provider_tool_initial_context_projection_enabled = True
    llm.settings.provider_tool_initial_context_mutation_enabled = False
    llm.settings.provider_tool_execution_budget_profile = "real_mutation"
    runtime = _runtime(llm, _registry(), _Executor())
    runtime.llm_client.settings.provider_tool_execution_enabled = True
    executor = ToolPlanningTaskExecutor(runtime)
    target = tmp_path / "calculator.py"
    test_file = tmp_path / "tests" / "test_calculator.py"
    task = Task(
        id="task-mutation-projection-flag",
        description="Modify calculator.py and validate it.",
        kind="implement",
        read_files=[str(target), str(test_file)],
        write_files=[str(target)],
        validation_command="python -m pytest -q tests/test_calculator.py",
    )
    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(task=task, parent_context={"goal": task.description, "project_path": str(tmp_path)}),
        tool_names=["file_reader"],
        user_confirmed=True,
        allow_mutations=True,
        initial_context_candidates=[
            ContextCandidate(
                candidate_id="projection:mutation-task",
                kind=ContextCandidateKind.TASK,
                content="Apply only the declared calculator change.",
                retention=ContextCandidateRetention.REQUIRED,
                truncation=ContextCandidateTruncation.FORBIDDEN,
                trust=ContextCandidateTrust.AUTHORITATIVE,
            )
        ],
    )

    assert result.status == TaskStatus.FAILED
    assert result.result_metadata.failure.error_type == "ProviderMutationInitialContextProjectionDisabled"
    assert llm.requests == []


def test_task_executor_support_context_projection_is_body_free_and_non_authorizing(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    captured: dict[str, object] = {}

    def fake_init(self, owner, task, **kwargs):
        captured["task"] = task
        captured.update(kwargs)
        return None

    def fake_run(self, messages):
        captured["messages"] = list(messages)
        return SimpleNamespace(
            success=True,
            error_message=None,
            final_response=LLMResponse(
                content="Support context contract checked.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
            rounds_used=0,
            tool_loop_results=[],
            evidence_coverage=SimpleNamespace(to_json_dict=lambda: {}),
            attempts=[],
        )

    monkeypatch.setattr(
        "autonomous_iteration.agents.tool_planning_executor.ProviderToolRoundTripRunner.__init__",
        fake_init,
    )
    monkeypatch.setattr(
        "autonomous_iteration.agents.tool_planning_executor.ProviderToolRoundTripRunner.run",
        fake_run,
    )

    llm = _LLM([])
    llm.settings.provider_tool_execution_enabled = True
    llm.settings.provider_tool_execution_budget_profile = "real_mutation"
    llm.settings.provider_tool_initial_context_mutation_enabled = True
    registry = _mutation_registry()
    registry.register(COMMAND_EXECUTOR_DEFINITION, lambda _input: None)
    runtime = _runtime(llm, registry, _Executor())
    executor = ToolPlanningTaskExecutor(runtime)
    target = tmp_path / "calculator.py"
    support = tmp_path / "tests" / "test_calculator.py"
    target.write_text("def add(a, b):\n    return a + b\n")
    support.parent.mkdir(parents=True)
    support.write_text("support body must not be serialized into prompt candidates")
    task = Task(
        id="task-support-context-projection",
        description="Modify calculator.py and validate it.",
        kind="implement",
        read_files=[str(target)],
        support_context_files=[str(support)],
        write_files=[str(target)],
        validation_command="python -m pytest -q tests/test_calculator.py",
    )

    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(
            task=task,
            parent_context={"goal": task.description, "project_path": str(tmp_path)},
        ),
        tool_names=["file_reader", "file_patch_writer", "command_executor"],
        user_confirmed=True,
        allow_mutations=True,
        max_rounds=1,
    )

    assert result.status == TaskStatus.COMPLETED
    assert captured["read_scope"] == [str(target)]
    assert captured["write_scope"] == [str(target)]
    candidates = list(captured["initial_context_candidates"])
    support_candidates = [
        candidate
        for candidate in candidates
        if str(candidate.candidate_id).startswith("provider-support-context:")
    ]
    assert [candidate.role for candidate in candidates[:2]] == ["system", "user"]
    assert len(support_candidates) == 1
    support_content = support_candidates[0].content
    assert str(support) in support_content
    assert "payload_omitted=true" in support_content
    assert "routing=not_required_read_before_write" in support_content
    assert "read_authority=false" in support_content
    assert "write_allowed=false" in support_content
    assert "support body must not be serialized" not in support_content
    assert result.attributes["support_context_files"] == [str(support)]
    assert result.attributes["support_context_candidate_count"] == 1


def test_task_executor_support_context_requires_mutation_projection_flag(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    llm.settings.provider_tool_execution_enabled = True
    llm.settings.provider_tool_execution_budget_profile = "real_mutation"
    llm.settings.provider_tool_initial_context_mutation_enabled = False
    runtime = _runtime(llm, _mutation_registry(), _Executor())
    executor = ToolPlanningTaskExecutor(runtime)
    target = tmp_path / "calculator.py"
    support = tmp_path / "tests" / "test_calculator.py"
    task = Task(
        id="task-support-context-flag",
        description="Modify calculator.py and validate it.",
        kind="implement",
        read_files=[str(target)],
        support_context_files=[str(support)],
        write_files=[str(target)],
        validation_command="python -m pytest -q tests/test_calculator.py",
    )

    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(task=task, parent_context={"goal": task.description}),
        tool_names=["file_reader"],
        user_confirmed=True,
        allow_mutations=True,
    )

    assert result.status == TaskStatus.FAILED
    assert result.result_metadata.failure.error_type == "ProviderMutationInitialContextProjectionDisabled"
    assert llm.requests == []


def test_task_executor_support_context_does_not_expand_read_scope(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    target = tmp_path / "calculator.py"
    support = tmp_path / "tests" / "test_calculator.py"
    target.write_text("def add(a, b):\n    return a + b\n")
    support.parent.mkdir(parents=True)
    support.write_text("def test_add():\n    assert True\n")
    llm = _LLM(
        [
            LLMResponse(
                content="",
                reasoning_content="I should read the support context file.",
                tool_calls=[
                    LLMToolCall(
                        id="support-read",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments=json.dumps({"file_path": str(support)}),
                        ),
                    )
                ],
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="tool_calls",
            )
        ]
    )
    llm.settings.provider_tool_execution_enabled = True
    llm.settings.provider_tool_execution_budget_profile = "real_mutation"
    llm.settings.provider_tool_initial_context_mutation_enabled = True
    registry = _mutation_registry()
    registry.register(COMMAND_EXECUTOR_DEFINITION, lambda _input: None)
    executor_backend = _Executor()
    runtime = _runtime(llm, registry, executor_backend)
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(
        id="task-support-context-read-scope",
        description="Modify calculator.py and validate it.",
        kind="implement",
        read_files=["calculator.py"],
        support_context_files=["tests/test_calculator.py"],
        write_files=["calculator.py"],
        validation_command="python -m pytest -q tests/test_calculator.py",
    )

    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(
            task=task,
            parent_context={"goal": task.description, "project_path": str(tmp_path)},
        ),
        tool_names=["file_reader", "file_patch_writer", "command_executor"],
        user_confirmed=True,
        allow_mutations=True,
        max_rounds=1,
    )

    assert result.status == TaskStatus.FAILED
    assert "Provider read scope permits only explicit files" in str(result.error)
    assert executor_backend.calls == []
    attempts = result.attributes["attempts"]
    assert attempts[0]["tool_name"] == "file_reader"
    assert attempts[0]["success"] is False


def test_task_executor_rejects_rounds_above_selected_profile(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    runtime = _runtime(llm, _registry(), _Executor())
    runtime.llm_client.settings.provider_tool_execution_enabled = True
    runtime.llm_client.settings.provider_tool_execution_budget_profile = "canary"
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(id="task-round-limit", description="Read README.md")

    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(task=task, parent_context={"goal": "Read README.md"}),
        tool_names=["file_reader"],
        max_rounds=4,
    )

    assert result.status == TaskStatus.FAILED
    assert result.result_metadata.failure.error_type == "ProviderToolMaxRoundsExceedsBudget"
    assert llm.requests == []


def test_task_executor_rejects_non_integral_rounds(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    runtime = _runtime(llm, _registry(), _Executor())
    runtime.llm_client.settings.provider_tool_execution_enabled = True
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(id="task-round-type", description="Read README.md")

    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(task=task, parent_context={"goal": "Read README.md"}),
        tool_names=["file_reader"],
        max_rounds=2.5,
    )

    assert result.status == TaskStatus.FAILED
    assert result.result_metadata.failure.error_type == "ProviderToolMaxRoundsInvalid"
    assert llm.requests == []


def test_task_executor_clamps_configured_rounds_to_profile(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            LLMResponse(
                content="configured rounds are clamped",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            )
        ]
    )
    runtime = _runtime(llm, _registry(), _Executor())
    runtime.llm_client.settings.provider_tool_execution_enabled = True
    runtime.llm_client.settings.provider_tool_execution_budget_profile = "canary"
    runtime.llm_client.settings.provider_tool_execution_max_rounds = 8
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(id="task-round-clamp", description="Read README.md")

    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(task=task, parent_context={"goal": "Read README.md"}),
        tool_names=["file_reader"],
    )

    assert result.status == TaskStatus.COMPLETED
    assert result.attributes["effective_max_rounds"] == 3
    assert result.attributes["requested_max_rounds"] is None


def test_task_executor_preserves_provider_stop_reason_in_failure_details(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    runtime = _runtime(_LLM([_tool_response(call_id="ds-task-stop-1")]), _registry(), _Executor())
    runtime.llm_client.settings.provider_tool_execution_enabled = True
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(id="task-stop-reason", description="Read README.md")

    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(task=task, parent_context={"goal": "Read README.md"}),
        tool_names=["file_reader"],
        max_rounds=1,
    )

    assert result.status == TaskStatus.FAILED
    assert result.result_metadata.failure.details["provider_stop_reason"] == "tool_calls"
    assert result.result_metadata.failure.details["runner_error"] == (
        "provider tool round limit exceeded (1)"
    )


def test_task_executor_provider_entry_enforces_inspection_read_scope(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-scoped-read-1"),
            LLMResponse(
                content="Scoped read completed.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _Executor())
    runtime.llm_client.settings.provider_tool_execution_enabled = True
    executor = ToolPlanningTaskExecutor(runtime)
    target = tmp_path / "README.md"
    task = Task(
        id="task-scoped-canary",
        description="Inspect README.md",
        kind="inspect",
        read_files=[str(target)],
    )
    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(
            task=task,
            parent_context={"goal": "Inspect README.md", "project_path": str(tmp_path)},
        ),
        tool_names=["file_reader"],
        max_rounds=2,
    )

    assert result.status == TaskStatus.COMPLETED
    assert str(target) in llm.requests[0].messages[-1].content


def test_task_executor_blocks_mutation_on_read_only_or_canary_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    llm.settings.provider_tool_execution_enabled = True
    llm.settings.provider_tool_execution_budget_profile = "canary"
    runtime = _runtime(llm, _registry(), _Executor())
    executor = ToolPlanningTaskExecutor(runtime)
    target = tmp_path / "calculator.py"
    test_file = tmp_path / "tests" / "test_calculator.py"
    task = Task(
        id="task-mutation-profile",
        description="Modify calculator.py and validate it.",
        kind="implement",
        read_files=[str(target), str(test_file)],
        write_files=[str(target)],
        validation_command="python -m pytest -q tests/test_calculator.py",
    )
    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(task=task, parent_context={"goal": task.description, "project_path": str(tmp_path)}),
        tool_names=["file_reader"],
        user_confirmed=True,
        allow_mutations=True,
    )
    assert result.status == TaskStatus.FAILED
    assert result.result_metadata.failure.error_type == "ProviderToolBudgetProfileMutationRequired"
    assert llm.requests == []


def test_task_executor_surfaces_evidence_coverage_after_finalization(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-executor-finalize-1"),
            LLMResponse(
                content="",
                reasoning_content="The complete file is already available; I will page it.",
                tool_calls=[
                    LLMToolCall(
                        id="ds-executor-finalize-page-1",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments='{"file_path":"README.md","offset":100}',
                        ),
                    )
                ],
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="README inspected with complete evidence.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    llm.settings.provider_tool_execution_enabled = True
    runtime = _runtime(llm, _registry(), _CompleteFileExecutor())
    executor = ToolPlanningTaskExecutor(runtime)
    target = tmp_path / "README.md"
    task = Task(
        id="task-executor-finalize",
        description="Inspect README.md",
        kind="inspect",
        read_files=[str(target)],
    )

    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(
            task=task,
            parent_context={"goal": "Inspect README.md", "project_path": str(tmp_path)},
        ),
        tool_names=["file_reader"],
        max_rounds=3,
    )

    assert result.status == TaskStatus.COMPLETED
    assert result.attributes["evidence_coverage"]["finalization_requests"] == 1
    assert result.attributes["evidence_coverage"]["duplicate_only_rounds"] == 1


def test_real_read_only_budget_profile_expands_only_explicit_provider_entry(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-real-budget-1"),
            LLMResponse(
                content="Real budget profile completed.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    llm.settings.provider_tool_execution_enabled = True
    llm.settings.provider_tool_execution_budget_profile = "real_read_only"
    runtime = _runtime(llm, _registry(), _Executor())
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(id="task-real-budget", description="Inspect README", kind="analysis")

    result = executor.execute_provider_tool_task(
        task,
        TaskExecutionContext(task=task, parent_context={"goal": "Inspect README"}),
        tool_names=["file_reader"],
        max_rounds=2,
    )

    assert result.status == TaskStatus.COMPLETED
    assert result.attributes["budget_profile"] == "real_read_only"
    assert result.attributes["budget_limits"]["context_max_prompt_tokens"] == 12_288
    assert result.attributes["budget_limits"]["completion_ceiling"] == 4_096
    assert llm.requests[0].max_tokens == 4_096
    assert llm.requests[0].context_selection.requested_prompt_tokens > 4_096
    assert runtime.runtime_controller.state.budget.max_tool_calls == 40
    assert runtime.runtime_controller.state.budget.max_file_reads == 60


def test_provider_executor_propagates_typed_outcome_feedback_to_budget_and_receipt(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-feedback-route-1"),
            LLMResponse(
                content="Feedback route completed.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
            ),
        ]
    )
    llm.settings.provider_tool_execution_enabled = True
    llm.settings.provider_tool_execution_budget_profile = "real_read_only"
    llm.settings.provider_tool_completion_outcome_feedback_enabled = True
    runtime = _runtime(llm, _registry(), _Executor())
    task = Task(id="task-feedback-route", description="Inspect README", kind="analysis")

    result = ToolPlanningTaskExecutor(runtime).execute_provider_tool_task(
        task,
        TaskExecutionContext(task=task, parent_context={"goal": task.description}),
        tool_names=["file_reader"],
        max_rounds=2,
    )

    assert result.status == TaskStatus.COMPLETED
    assert runtime.runtime_controller.state.budget.tool_event_completion_outcome_feedback_enabled is True
    assert result.attributes["outcome_feedback_enabled"] is True
    assert result.attributes["budget_limits"]["outcome_feedback_enabled"] is True
    assert result.attributes["reasoning_complexity"] == "standard"
    assert result.attributes["reasoning_mode"] == "provider_default"
    assert result.attributes["execution_mode"] == "real_read_only"
    assert result.attributes["allow_mutations"] is False
    assert result.attributes["budget_contract_sha256"].startswith("sha256:")


def test_provider_roundtrip_result_binds_outcome_feedback_and_failed_attempt_budget_trace(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    llm.complete = lambda _request: (_ for _ in ()).throw(RuntimeError("transport interrupted"))
    runtime = _runtime(llm, _registry(), _Executor())
    runtime.runtime_controller.state.budget = RuntimeBudgetMetadata(
        max_tool_event_completion_tokens=3_000,
        tool_event_completion_ceiling=1_200,
        tool_event_completion_floor=400,
        tool_event_completion_outcome_feedback_enabled=True,
    )
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-budget-failure", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=1,
        max_tokens=1_200,
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is False
    assert result.outcome_feedback_enabled is True
    assert len(result.budget_diagnostics) == 1
    diagnostic = result.budget_diagnostics[0]
    assert diagnostic["outcome_feedback_enabled"] is True
    assert diagnostic["requested_limit"] == 1_200
    assert diagnostic["reserved_tokens"] == 1_200
    assert diagnostic["actual_completion_tokens"] is None
    assert diagnostic["usage_known"] is False
    assert diagnostic["finish_reason"] is None
    assert diagnostic["outcome"] is None
    assert diagnostic["error_type"] == "RuntimeError"


def test_provider_roundtrip_failed_attempt_preserves_known_error_usage_and_finish(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM([])
    error = RuntimeError("provider returned a capped response with transport error")
    error.usage = {"completion_tokens": 100}
    error.finish_reason = "length"
    llm.complete = lambda _request: (_ for _ in ()).throw(error)
    runtime = _runtime(llm, _registry(), _Executor())
    runtime.runtime_controller.state.budget = RuntimeBudgetMetadata(
        max_tool_event_completion_tokens=3_000,
        tool_event_completion_ceiling=1_200,
        tool_event_completion_floor=400,
        tool_event_completion_outcome_feedback_enabled=True,
    )
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-budget-known-error", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=1,
        max_tokens=1_200,
    ).run([LLMMessage(role="user", content="Read README")])

    diagnostic = result.budget_diagnostics[0]
    assert diagnostic["actual_completion_tokens"] == 100
    assert diagnostic["usage_known"] is True
    assert diagnostic["finish_reason"] == "length"
    assert diagnostic["outcome"] == "truncated"
    assert diagnostic["provider_cap_hit"] is True
    assert runtime.runtime_controller.state.budget.tool_event_completion_tokens_used == 100


def test_provider_budget_profile_values_are_typed_and_explicit() -> None:
    canary = ProviderToolExecutionBudget.for_profile(
        ProviderToolExecutionBudgetProfile.CANARY
    )
    real = ProviderToolExecutionBudget.for_profile("real_read_only")
    mutation = ProviderToolExecutionBudget.for_profile("real_mutation")

    assert canary.context_max_prompt_tokens == 4_096
    assert canary.total_completion_tokens == 12_000
    assert real.context_max_prompt_tokens == 12_288
    assert real.total_completion_tokens == 24_000
    assert real.max_rounds == 8
    assert real.max_tool_calls == 40
    assert real.max_file_reads == 60
    assert real.max_file_edits == 0
    assert real.max_file_creates == 0
    assert real.max_verification_attempts == 0
    assert mutation.profile is ProviderToolExecutionBudgetProfile.REAL_MUTATION
    assert mutation.context_max_prompt_tokens == 12_288
    assert mutation.completion_ceiling == 4_096
    assert mutation.total_completion_tokens == 24_000
    assert mutation.max_rounds == 8
    assert mutation.max_file_edits == 1
    assert mutation.max_file_creates == 0
    assert mutation.max_verification_attempts == 1


def test_provider_runner_reconciles_usage_against_total_completion_budget(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-budget-usage-1").model_copy(
                update={"usage": {"completion_tokens": 400}}
            ),
            LLMResponse(
                content="Done.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
                usage={"completion_tokens": 200},
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _Executor())
    runtime.runtime_controller.state.budget = RuntimeBudgetMetadata(
        max_tool_event_completion_tokens=1_000,
        tool_event_completion_ceiling=800,
        tool_event_completion_floor=200,
    )
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-budget-usage", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=2,
        max_tokens=800,
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is True
    assert [request.max_tokens for request in llm.requests] == [500, 400]
    assert runtime.runtime_controller.state.budget.tool_event_completion_tokens_used == 600


def test_provider_runner_opt_in_outcome_feedback_preserves_truncation_recovery(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: _TokenCounter(),
    )
    llm = _LLM(
        [
            _tool_response(call_id="ds-budget-truncated-1").model_copy(
                update={
                    "finish_reason": "length",
                    "usage": {"completion_tokens": 100},
                }
            ),
            LLMResponse(
                content="Done after bounded recovery.",
                model="deepseek-v4-flash",
                provider="deepseek",
                finish_reason="stop",
                usage={"completion_tokens": 100},
            ),
        ]
    )
    runtime = _runtime(llm, _registry(), _Executor())
    runtime.runtime_controller.state.budget = RuntimeBudgetMetadata(
        max_tool_event_completion_tokens=3_000,
        tool_event_completion_ceiling=1_200,
        tool_event_completion_floor=400,
        tool_event_completion_recovery_step=200,
        tool_event_completion_outcome_feedback_enabled=True,
    )
    result = ProviderToolRoundTripRunner(
        _Owner(runtime),
        Task(id="task-budget-truncated", description="Read README"),
        tools=build_provider_tool_definitions(runtime.tool_registry, ["file_reader"]),
        max_rounds=2,
        max_tokens=1_200,
    ).run([LLMMessage(role="user", content="Read README")])

    assert result.success is True
    assert [request.max_tokens for request in llm.requests] == [1_200, 1_200]
    assert runtime.runtime_controller.state.budget.tool_event_completion_last_outcome == ToolEventCompletionOutcome.NORMAL.value
    assert [item["outcome"] for item in result.budget_diagnostics] == ["truncated", "normal"]
    assert result.budget_diagnostics[0]["requested_limit"] == 1_200
    assert result.budget_diagnostics[0]["recovery_bonus_after"] == 200
    assert result.budget_diagnostics[1]["usage_known"] is True
