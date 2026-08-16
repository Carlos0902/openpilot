from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from autonomous_iteration.task_models import Task, TaskExecutionContext, TaskExecutionResult, TaskStatus
from core.openpilot_log import OpenPilotLogger
from core.tool_event_loop import ToolEventLoopRunner
from autonomous_iteration.agents.tool_planning_executor import ToolPlanningTaskExecutor
from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from autonomous_iteration.planning_surface import (
    CapabilityExposure,
    CapabilitySourceKind,
    PlanningSurfaceCard,
    PlanningSurfaceCatalog,
    PlanningSurfaceSelector,
    StaticCapabilityCardProvider,
    ToolCapabilityCardProvider,
)
from autonomous_iteration.runtime_controller import EditGuard, FileSelector, RuntimeVerifier, StateUpdater, ToolRouter
from autonomous_iteration.tool_io import ExecutionToolIO
from core.exceptions import ErrorCategory, InvalidLLMResponseError, LLMProviderError
from metadata import AgentPhase, CodeArtifactMetadata, DecisionNeedMetadata, FileArtifactMetadata, ReasoningDecisionComplexity, ReasoningMode, ResolutionPlanMetadata, ResultStatus, RuntimeStateMetadata, TaskResultMetadata, TextArtifactMetadata, ToolErrorMetadata, ToolInputMetadata, ToolResultMetadata
from tools.bug_fix_tool import BUG_FIX_TOOL_DEFINITION
from tools.command_tool import COMMAND_EXECUTOR_DEFINITION
from tools.code_generator import CODE_GENERATOR_DEFINITION
from tools.file_reader import FILE_READER_DEFINITION
from tools.file_writer import FILE_WRITER_DEFINITION
from tools.file_delete_tool import FILE_DELETE_TOOL_DEFINITION
from tools.environment_fix_tool import ENVIRONMENT_FIX_TOOL_DEFINITION
from tools.llm_summarizer import LLM_SUMMARIZER_DEFINITION
from tools.multi_file_reader import MULTI_FILE_READER_DEFINITION
from tools.code_editor import CODE_EDITOR_DEFINITION
from tools.code_unit_generator import CODE_UNIT_GENERATOR_DEFINITION
from tools.file_patch_writer import FILE_PATCH_WRITER_DEFINITION
from tools.readme_tool import README_TOOL_DEFINITION
from tools.task_classifier import TASK_CLASSIFIER_DEFINITION
from tools.web_searcher import WEB_SEARCHER_DEFINITION


class FakeLLM:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.requests = []
        self._index = 0

    def complete(self, request):
        self.requests.append(request)
        payload = self.payload
        if isinstance(payload, list):
            payload = payload[min(self._index, len(payload) - 1)]
            self._index += 1
        if isinstance(payload, str):
            return SimpleNamespace(parsed_json=None, content=payload)
        return SimpleNamespace(parsed_json=payload, content=json.dumps(payload))


class InvalidJSONLLM:
    def __init__(self) -> None:
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        raise InvalidLLMResponseError("LLM returned invalid JSON after repair attempts.")


class TransportFailureLLM:
    def __init__(self) -> None:
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        raise LLMProviderError("incomplete chunked read", category=ErrorCategory.NETWORK, retryable=True)


class FakeToolRegistry:
    def get(self, tool_name):
        if tool_name == "file_reader":
            return FILE_READER_DEFINITION
        if tool_name == "multi_file_reader":
            return MULTI_FILE_READER_DEFINITION
        if tool_name == "command_executor":
            return COMMAND_EXECUTOR_DEFINITION
        if tool_name == "code_generator":
            return CODE_GENERATOR_DEFINITION
        if tool_name == "code_unit_generator":
            return CODE_UNIT_GENERATOR_DEFINITION
        if tool_name == "code_editor":
            return CODE_EDITOR_DEFINITION
        if tool_name == "file_writer":
            return FILE_WRITER_DEFINITION
        if tool_name == "file_patch_writer":
            return FILE_PATCH_WRITER_DEFINITION
        if tool_name == "readme_tool":
            return README_TOOL_DEFINITION
        if tool_name == "web_searcher":
            return WEB_SEARCHER_DEFINITION
        if tool_name == "bug_fix_tool":
            return BUG_FIX_TOOL_DEFINITION
        if tool_name == "environment_fix_tool":
            return ENVIRONMENT_FIX_TOOL_DEFINITION
        if tool_name == "file_delete_tool":
            return FILE_DELETE_TOOL_DEFINITION
        return None

    def get_executor(self, tool_name):
        if tool_name in {
            "code_generator",
            "code_unit_generator",
            "code_editor",
            "file_writer",
            "file_patch_writer",
            "command_executor",
            "code_executor",
            "file_reader",
            "multi_file_reader",
        }:
            return lambda *_args, **_kwargs: None
        return None

    def list_all(self):
        return [
            FILE_READER_DEFINITION,
            MULTI_FILE_READER_DEFINITION,
            COMMAND_EXECUTOR_DEFINITION,
            WEB_SEARCHER_DEFINITION,
            CODE_GENERATOR_DEFINITION,
            CODE_UNIT_GENERATOR_DEFINITION,
            CODE_EDITOR_DEFINITION,
            FILE_WRITER_DEFINITION,
            FILE_PATCH_WRITER_DEFINITION,
            README_TOOL_DEFINITION,
            FILE_DELETE_TOOL_DEFINITION,
            BUG_FIX_TOOL_DEFINITION,
            ENVIRONMENT_FIX_TOOL_DEFINITION,
            LLM_SUMMARIZER_DEFINITION,
            TASK_CLASSIFIER_DEFINITION,
        ]


class FakeToolExecutor:
    def __init__(self) -> None:
        self.selections = []

    def execute_single(self, selection, context=None):
        self.selections.append(selection)
        if selection.tool_name == "code_generator":
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="code_generator",
                    status=ResultStatus.SUCCESS,
                    result=CodeArtifactMetadata(code="print('ok')", language="python"),
                ),
                error=None,
                execution_time_ms=10,
            )
        if selection.tool_name == "file_writer":
            payload = selection.input_metadata.to_params()
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="file_writer",
                    status=ResultStatus.SUCCESS,
                    result=FileArtifactMetadata(file_path=payload["file_path"], content=payload["content"]),
                ),
                error=None,
                execution_time_ms=5,
            )
        if selection.tool_name == "code_unit_generator":
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="code_unit_generator",
                    status=ResultStatus.SUCCESS,
                    result=CodeArtifactMetadata(
                        code="def added():\n    return 2",
                        language="python",
                        attributes={"operation_kind": "add_symbol", "symbol_name": "added"},
                    ),
                ),
                error=None,
                execution_time_ms=10,
            )
        if selection.tool_name == "code_editor":
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="code_editor",
                    status=ResultStatus.SUCCESS,
                    result=CodeArtifactMetadata(
                        code="def change():\n    return 2",
                        language="python",
                        attributes={
                            "operation_kind": "modify_symbol",
                            "symbol_name": "change",
                            "line_start": 1,
                            "line_end": 2,
                            "patch": {
                                "operation_kind": "modify_symbol",
                                "replacement_text": "def change():\n    return 2",
                                "line_start": 1,
                                "line_end": 2,
                            },
                        },
                    ),
                ),
                error=None,
                execution_time_ms=10,
            )
        if selection.tool_name == "file_patch_writer":
            payload = selection.input_metadata.to_params()
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="file_patch_writer",
                    status=ResultStatus.SUCCESS,
                    result=FileArtifactMetadata(file_path=payload["file_path"], attributes={"changed_ranges": [{"line_start": 1, "line_end": 2}]}),
                ),
                error=None,
                execution_time_ms=5,
            )
        if selection.tool_name == "command_executor":
            payload = selection.input_metadata.to_params()
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="command_executor",
                    status=ResultStatus.SUCCESS,
                    result=TextArtifactMetadata(content="command ok", attributes=payload),
                ),
                error=None,
                execution_time_ms=5,
            )
        if selection.tool_name == "code_executor":
            payload = selection.input_metadata.to_params()
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="code_executor",
                    status=ResultStatus.SUCCESS,
                    result=TextArtifactMetadata(content="code ok", attributes=payload),
                ),
                error=None,
                execution_time_ms=5,
            )
        if selection.tool_name == "multi_file_reader":
            payload = selection.input_metadata.to_params()
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="multi_file_reader",
                    status=ResultStatus.SUCCESS,
                    result=FileArtifactMetadata(
                        file_path=str(payload.get("directory_path") or ""),
                        files=list(payload.get("file_paths") or []),
                        content="combined",
                    ),
                ),
                error=None,
                execution_time_ms=5,
            )
        if selection.tool_name == "file_reader":
            payload = selection.input_metadata.to_params()
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="file_reader",
                    status=ResultStatus.SUCCESS,
                    result=FileArtifactMetadata(
                        file_path=str(payload.get("file_path") or ""),
                        content="file content",
                    ),
                ),
                error=None,
                execution_time_ms=5,
            )
        return SimpleNamespace(
            success=False,
            output_metadata=None,
            error=SimpleNamespace(error_type="UnknownTool", error_message="unknown tool", recoverable=True),
            execution_time_ms=1,
        )


class AssistantCodeToolExecutor(FakeToolExecutor):
    def execute_single(self, selection, context=None):
        if selection.tool_name == "code_generator":
            self.selections.append(selection)
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="code_generator",
                    status=ResultStatus.SUCCESS,
                    result=CodeArtifactMetadata(
                        code='"""OpenPilot Assistant main file."""\n\n\ndef main():\n    print("ok")\n',
                        language="python",
                    ),
                ),
                error=None,
                execution_time_ms=10,
            )
        return super().execute_single(selection, context)


class InvalidMultiFileReaderExecutor(FakeToolExecutor):
    def execute_single(self, selection, context=None):
        if selection.tool_name == "multi_file_reader":
            self.selections.append(selection)
            return SimpleNamespace(
                success=False,
                output_metadata=None,
                error=SimpleNamespace(
                    error_type="ValueError",
                    error_message="multi_file_reader requires file_paths or directory_path",
                    recoverable=False,
                ),
                execution_time_ms=1,
            )
        return super().execute_single(selection, context)


class TimeoutThenSuccessExecutor(FakeToolExecutor):
    def __init__(self) -> None:
        super().__init__()
        self._timed_out = False

    def execute_single(self, selection, context=None):
        if selection.tool_name == "code_generator" and not self._timed_out:
            self._timed_out = True
            self.selections.append(selection)
            return SimpleNamespace(
                success=False,
                output_metadata=None,
                error=SimpleNamespace(
                    error_type="LLMTimeoutError",
                    error_message="provider read operation timed out",
                    recoverable=True,
                    retry_recommended=True,
                ),
                execution_time_ms=1,
            )
        return super().execute_single(selection, context)


class TimeoutUntilLocalFallbackExecutor(FakeToolExecutor):
    def execute_single(self, selection, context=None):
        if selection.tool_name == "code_generator":
            self.selections.append(selection)
            prompt_context = selection.input_metadata.prompt_context
            if not prompt_context.get("local_fallback_after_provider_failure"):
                return SimpleNamespace(
                    success=False,
                    output_metadata=None,
                    error=SimpleNamespace(
                        error_type="LLMTimeoutError",
                        error_message="provider read operation timed out",
                        recoverable=True,
                        retry_recommended=True,
                    ),
                    execution_time_ms=1,
                )
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="code_generator",
                    status=ResultStatus.SUCCESS,
                    result=CodeArtifactMetadata(
                        code="print('local fallback')",
                        language="python",
                        attributes={"generation_mode": "local_fallback"},
                    ),
                ),
                error=None,
                execution_time_ms=1,
            )
        return super().execute_single(selection, context)


class FakeRuntime:
    def __init__(self, tmp_path, payload) -> None:
        self.session_id = "session"
        self.logger = OpenPilotLogger(tmp_path / "tool_planning.jsonl")
        self.llm_client = FakeLLM(payload)
        self.tool_registry = FakeToolRegistry()
        self.tool_executor = FakeToolExecutor()
        self.enhanced_ui = None
        self.tool_io = ExecutionToolIO(self.logger, lambda: self.session_id)
        self._project_environments = {
            str(tmp_path): {
                "project_path": str(tmp_path),
                "command_cwd": str(tmp_path),
                "command_env": {"VIRTUAL_ENV": str(tmp_path / ".venv")},
                "python_command": str(tmp_path / ".venv" / "bin" / "python"),
                "pip_command": str(tmp_path / ".venv" / "bin" / "pip"),
            }
        }
        self._last_git_snapshot = {
            "kind": "git_snapshot",
            "project_path": str(tmp_path),
            "commit_hash": "abc1234",
            "created": True,
        }

    def _format_tools_for_llm(self, tools):
        return "No tools"

    def _format_planning_surface(self, tools, **kwargs):
        return self.tool_io.format_planning_surface(tools, **kwargs)

    def _resolve_chained_metadata(self, tool_name, input_metadata, last_output, last_code_output):
        return self.tool_io.resolve_chained_metadata(tool_name, input_metadata, last_output, last_code_output)

    def _map_reason_to_enum(self, reason_text):
        return self.tool_io.map_reason_to_enum(reason_text)

    def _sanitize_tool_metadata(self, input_metadata):
        return self.tool_io.sanitize_tool_metadata(input_metadata)

    def _environment_for_tool_input(self, input_metadata):
        return next(iter(self._project_environments.values()))

    def _apply_project_command_context(self, tool_name, input_metadata):
        if tool_name != "command_executor":
            return input_metadata
        environment = self._environment_for_tool_input(input_metadata)
        return input_metadata.model_copy(
            update={
                "cwd": environment["command_cwd"],
                "env": environment["command_env"],
            }
        )


class FakeUI:
    def __init__(self) -> None:
        self.events = []

    def append_tool_event(self, event) -> None:
        self.events.append(event.to_json_dict() if hasattr(event, "to_json_dict") else event)

    def set_current_task_state(self, **_kwargs) -> None:
        return None


class BrokenEventHookUI(FakeUI):
    def append_tool_event(self, event) -> None:
        raise RuntimeError("ui hook failed")


def _context(task: Task) -> TaskExecutionContext:
    return TaskExecutionContext(task=task, parent_context={"goal": "build app"}, shared_state={}, execution_history=[])


def test_tool_planning_executor_success_and_chained_file_writer(tmp_path) -> None:
    task = Task(id="task", description="Generate and write app")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "code_generation",
                    "question": "generate code",
                    "attributes": {"task_description": "make app"},
                },
                {
                    "need_type": "file_write",
                    "question": "write file",
                    "target_path": "app.py",
                },
            ]
        },
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert result.result_metadata.result.attributes["all_tools_succeeded"] is True
    assert result.result_metadata.result.attributes["tool_results"][1]["input_metadata"]["content"] == "print('ok')"
    assert result.result_metadata.result.attributes["tool_results"][1]["tool_context"]["git_snapshot"]["commit_hash"] == "abc1234"
    assert runtime.tool_executor.selections[1].input_metadata.to_params() == {
        "file_path": "app.py",
        "content": "print('ok')",
        "operation_kind": "create_file",
    }
    payloads = [
        json.loads(line)["payload"]
        for line in (tmp_path / "tool_planning.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(payload.get("source_type") == "agent" for payload in payloads)
    assert any(payload.get("source_name") == "autonomous_iteration.agents.tool_planning_executor" for payload in payloads)


def test_inspect_subtask_does_not_downgrade_mutating_root_runtime_state(tmp_path) -> None:
    task = Task(
        id="inspect",
        description="Inspect calculator.py before implementing the fix",
        kind="inspect",
        tags=["inspect"],
    )
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    state = RuntimeStateMetadata(goal="Fix calculator.py and run tests")
    runtime.runtime_controller = SimpleNamespace(
        state=state,
        router=ToolRouter(runtime.tool_registry),
        edit_guard=EditGuard(),
        file_selector=FileSelector(),
        state_updater=StateUpdater(),
        verifier=RuntimeVerifier(),
    )
    executor = ToolPlanningTaskExecutor(runtime)
    executor._active_task = task
    executor._active_task_id = task.id
    executor._active_task_description = task.description
    executor._active_goal = state.goal
    executor._active_context = TaskExecutionContext(
        task=task,
        parent_context={"goal": state.goal, "project_path": str(tmp_path)},
    )

    _controller, _router, planning_state = executor._planning_runtime_state({})

    assert planning_state.execution_mode.value == "mutation_allowed"
    assert "runtime_mode:read_only_analysis" not in planning_state.assumptions


def test_partially_blocked_required_plan_cannot_complete_after_read_succeeds(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    target.write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    task = Task(
        id="implement",
        description="Modify calculator.py to raise ValueError on zero",
        kind="implement",
        write_files=[str(target)],
    )
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {"need_type": "file_read", "question": "inspect target", "target_path": str(target)},
                {
                    "need_type": "file_write",
                    "question": "write fixed target",
                    "target_path": str(target),
                    "attributes": {"content": "def divide(a, b):\n    if b == 0:\n        raise ValueError('zero')\n    return a / b\n"},
                },
            ]
        },
    )
    state = RuntimeStateMetadata.model_validate(
        {
            "goal": "Read-only review",
            "execution_mode": "read_only",
            "execution_mode_source": "user_constraint",
        }
    )
    runtime.runtime_controller = SimpleNamespace(
        state=state,
        router=ToolRouter(runtime.tool_registry),
        edit_guard=EditGuard(),
        file_selector=FileSelector(),
        state_updater=StateUpdater(),
        verifier=RuntimeVerifier(),
    )
    guard_events = []
    runtime.runtime_diagnostics_hooks = SimpleNamespace(
        on_guard_decision=lambda **kwargs: guard_events.append(kwargs)
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status != TaskStatus.COMPLETED
    assert runtime.tool_executor.selections == []
    assert state.guard_history
    assert state.guard_history[-1].approved is False
    assert state.guard_history[-1].attributes["need_type"] == "file_write"
    assert guard_events[0]["decision"].approved is False
    assert target.read_text(encoding="utf-8") == "def divide(a, b):\n    return a / b\n"


def test_implement_task_cannot_complete_with_only_read_evidence(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    target.write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    task = Task(
        id="implement",
        description="Modify calculator.py",
        kind="implement",
        write_files=[str(target)],
    )
    runtime = FakeRuntime(
        tmp_path,
        {"decision_needs": [{"need_type": "file_read", "question": "inspect target", "target_path": str(target)}]},
    )

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.FAILED
    assert "no observed file mutation" in (result.error or "").lower()


def test_code_symbol_modify_synthesizes_one_patch_writer_when_model_omits_file_write(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    target.write_text("def change():\n    return 1\n", encoding="utf-8")
    task = Task(
        id="implement",
        description="Modify change function",
        kind="implement",
        write_files=[str(target)],
    )
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "code_symbol_modify",
                    "question": "Modify change function",
                    "target_path": str(target),
                    "operation_kind": "modify_symbol",
                    "symbol_name": "change",
                    "symbol_type": "function",
                }
            ]
        },
    )

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == [
        "code_editor",
        "file_patch_writer",
    ]
    assert result.attributes["observed_modified_files"] == [str(target)]


def test_code_symbol_modify_does_not_duplicate_explicit_patch_writer(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    target.write_text("def change():\n    return 1\n", encoding="utf-8")
    task = Task(id="implement", description="Modify change function", kind="implement", write_files=[str(target)])
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "code_symbol_modify",
                    "question": "Generate replacement",
                    "target_path": str(target),
                    "operation_kind": "modify_symbol",
                    "symbol_name": "change",
                },
                {
                    "need_type": "file_write",
                    "question": "Apply replacement",
                    "target_path": str(target),
                    "operation_kind": "modify_symbol",
                    "symbol_name": "change",
                },
            ]
        },
    )

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == [
        "code_editor",
        "file_patch_writer",
    ]


def test_validate_task_cannot_complete_without_command_evidence(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    target.write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    task = Task(
        id="validate",
        description="Run calculator tests",
        kind="validate",
        validation_command="python -m pytest test_calculator.py",
    )
    runtime = FakeRuntime(
        tmp_path,
        {"decision_needs": [{"need_type": "file_read", "question": "inspect target", "target_path": str(target)}]},
    )

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.FAILED
    assert "no observed validation command" in (result.error or "").lower()


def test_validate_task_rejects_successful_substitute_command(tmp_path) -> None:
    task = Task(
        id="validate",
        description="Run calculator tests",
        kind="validate",
        validation_command="python -m pytest -q",
    )
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "command_check",
                    "question": "compile instead of testing",
                    "command": "python -m compileall .",
                }
            ]
        },
    )

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.FAILED
    assert "required validation command" in (result.error or "").lower()


def test_validate_task_accepts_argument_equivalent_command_whitespace(tmp_path) -> None:
    task = Task(
        id="validate",
        description="Run calculator tests",
        kind="validate",
        validation_command="python -m pytest -q",
    )
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "command_check",
                    "question": "run the required tests",
                    "command": "python   -m pytest   -q",
                }
            ]
        },
    )

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED


def test_validate_task_without_typed_command_cannot_complete_from_arbitrary_command(tmp_path) -> None:
    task = Task(id="validate", description="Validate calculator", kind="validate")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "command_check",
                    "question": "compile calculator",
                    "command": "python -m compileall .",
                }
            ]
        },
    )

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.FAILED
    assert "missing its required validation_command" in (result.error or "")


def test_new_subtask_clears_only_prior_no_progress_block(tmp_path) -> None:
    task = Task(
        id="validate",
        description="Run tests",
        kind="validate",
        validation_command="python -m pytest -q",
    )
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "command_check",
                    "question": "Run tests",
                    "command": "python -m pytest -q",
                    "attributes": {"cwd": str(tmp_path)},
                }
            ]
        },
    )
    state = RuntimeStateMetadata(goal="Fix calculator")
    state.no_progress_rounds = 3
    state.block("no new runtime facts after repeated tool results")
    runtime.runtime_controller = SimpleNamespace(
        state=state,
        router=ToolRouter(runtime.tool_registry),
        edit_guard=EditGuard(),
        file_selector=FileSelector(),
        state_updater=StateUpdater(),
        verifier=RuntimeVerifier(),
    )

    result = ToolPlanningTaskExecutor(runtime).execute_task(
        task,
        TaskExecutionContext(
            task=task,
            parent_context={"goal": state.goal, "project_path": str(tmp_path)},
        ),
    )

    assert result.status == TaskStatus.COMPLETED
    assert state.no_progress_rounds == 0
    assert state.phase != AgentPhase.BLOCKED


def test_task_context_defaults_use_explicit_project_path_instead_of_external_cwd(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    task = Task(id="validate", description="Run tests", kind="validate")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)
    executor._active_context = TaskExecutionContext(
        task=task,
        parent_context={
            "goal": "Fix project",
            "project_path": str(project),
            "cwd": str(outside),
        },
    )

    defaults = executor._context_default_attributes()

    assert defaults["project_path"] == str(project.resolve())
    assert defaults["cwd"] == str(project.resolve())


def test_autopilot_resume_rebinds_project_context_for_relative_tool_paths(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    received: dict[str, object] = {}
    autopilot = object.__new__(IntelligentAutopilot)
    autopilot.use_enhanced_ui = False
    autopilot.enhanced_ui = None
    autopilot.tracker = None
    autopilot.runtime_controller = SimpleNamespace(
        resume=lambda run_id, checkpoint_id, context, mode: received.update(
            run_id=run_id,
            checkpoint_id=checkpoint_id,
            context=context,
            mode=mode,
        )
        or {"success": True}
    )

    result = autopilot.resume("run-1", "checkpoint-1", {"project_path": str(project)})

    assert result["success"] is True
    assert autopilot._current_execution_context["project_path"] == str(project)
    assert received["context"] == autopilot._current_execution_context


def test_project_inference_prefers_explicit_restored_project_over_relative_result_path(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    autopilot = object.__new__(IntelligentAutopilot)
    autopilot._current_execution_context = {"project_path": str(project)}

    inferred = autopilot._infer_project_path_from_files("Fix calculator.py", ["calculator.py"])

    assert inferred == project.resolve()


def test_subtask_write_scope_resolves_relative_planned_file_against_project_root(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "calculator.py"
    target.write_text("value = 1\n", encoding="utf-8")
    task = Task(
        id="implement",
        description="Modify calculator.py",
        kind="implement",
        write_files=["calculator.py"],
    )
    executor = ToolPlanningTaskExecutor(FakeRuntime(project, {"decision_needs": []}))
    executor._active_task = task
    executor._active_context = TaskExecutionContext(
        task=task,
        parent_context={"goal": "Fix calculator", "project_path": str(project)},
    )

    executor._enforce_subtask_write_scope(
        DecisionNeedMetadata(
            need_type="file_write",
            question="Apply the fix",
            target_path=str(target.resolve()),
        )
    )


def test_implement_subtask_without_planned_write_files_rejects_mutation(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    target.write_text("value = 1\n", encoding="utf-8")
    task = Task(id="implement", description="Modify calculator.py", kind="implement")
    executor = ToolPlanningTaskExecutor(FakeRuntime(tmp_path, {"decision_needs": []}))
    executor._active_task = task
    executor._active_context = TaskExecutionContext(
        task=task,
        parent_context={"goal": "Fix calculator", "project_path": str(tmp_path)},
    )

    try:
        executor._enforce_subtask_write_scope(
            DecisionNeedMetadata(
                need_type="file_write",
                question="Apply the fix",
                target_path=str(target),
            )
        )
    except Exception as exc:
        assert "write scope" in str(exc).lower()
    else:
        raise AssertionError("Expected missing write scope to reject mutation")


def test_validate_subtask_rejects_model_proposed_file_write(tmp_path) -> None:
    target = tmp_path / "test_calculator.py"
    target.write_text("def test_one():\n    assert True\n", encoding="utf-8")
    task = Task(id="validate", description="Run tests", kind="validate", validation_command="pytest")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "file_write",
                    "question": "Rewrite the test",
                    "target_path": str(target),
                    "attributes": {"content": "def test_one():\n    assert False\n"},
                }
            ]
        },
    )

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.FAILED
    assert "subtask write scope" in (result.error or "").lower()
    assert runtime.tool_executor.selections == []
    assert target.read_text(encoding="utf-8") == "def test_one():\n    assert True\n"


def test_inspect_subtask_invalid_json_fallback_reads_without_mutating_target(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    tests = tmp_path / "test_calculator.py"
    original = "def divide(a, b):\n    return a / b\n"
    target.write_text(original, encoding="utf-8")
    tests.write_text("def test_divide():\n    assert True\n", encoding="utf-8")
    task = Task(
        id="inspect",
        description="Read calculator.py and inspect the failing test before making changes.",
        kind="inspect",
        read_files=[str(target), str(tests)],
    )
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    runtime.llm_client = InvalidJSONLLM()

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == [
        "file_reader",
        "file_reader",
    ]
    assert target.read_text(encoding="utf-8") == original


def test_inspect_subtask_drops_mutation_and_validation_needs_from_mixed_plan(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    tests = tmp_path / "test_calculator.py"
    original = "def divide(a, b):\n    return a / b\n"
    target.write_text(original, encoding="utf-8")
    tests.write_text("def test_divide():\n    assert True\n", encoding="utf-8")
    task = Task(
        id="inspect",
        description="Inspect calculator and its tests",
        kind="inspect",
        read_files=[str(target), str(tests)],
    )
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {"need_type": "file_read", "question": "read code", "target_path": str(target)},
                {"need_type": "file_read", "question": "read tests", "target_path": str(tests)},
                {
                    "need_type": "code_symbol_modify",
                    "question": "modify divide",
                    "target_path": str(target),
                    "operation_kind": "modify_symbol",
                    "symbol_name": "divide",
                },
                {"need_type": "command_check", "question": "run pytest", "command": "python -m pytest -q"},
            ]
        },
    )

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == [
        "file_reader",
        "file_reader",
    ]
    assert target.read_text(encoding="utf-8") == original


def test_validation_kind_is_recognized_when_description_starts_with_run(tmp_path) -> None:
    task = Task(id="validate", description="Run test_calculator.py", kind="validate")
    executor = ToolPlanningTaskExecutor(FakeRuntime(tmp_path, {"decision_needs": []}))
    executor._active_task = task

    assert executor._looks_like_validation_task(task.description) is True


def test_execution_state_changed_files_excludes_unobserved_planned_writes(tmp_path) -> None:
    target = str(tmp_path / "calculator.py")
    task = Task(id="implement", description="Modify calculator", write_files=[target])
    result = TaskExecutionResult(task_id=task.id, status=TaskStatus.COMPLETED)

    state = IntelligentAutopilot._execution_state_metadata([task], [result], [[task.id]])

    assert state.changed_files == []


def test_written_file_collection_consumes_observed_mutation_evidence(tmp_path) -> None:
    target = str(tmp_path / "calculator.py")
    result = TaskExecutionResult(
        task_id="implement",
        status=TaskStatus.COMPLETED,
        attributes={"observed_modified_files": [target]},
    )
    runtime = object.__new__(IntelligentAutopilot)

    assert runtime._collect_written_files([result]) == [target]


def test_tool_planning_reroutes_generated_python_away_from_requirements(tmp_path) -> None:
    task = Task(id="task", description="Create assistant core")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "code_generation",
                    "question": "generate assistant",
                    "attributes": {"task_description": "Create the main assistant Python file"},
                },
                {
                    "need_type": "file_write",
                    "question": "write generated assistant",
                    "target_path": str(tmp_path / "requirements.txt"),
                },
            ]
        },
    )
    runtime.tool_executor = AssistantCodeToolExecutor()
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    writer_input = runtime.tool_executor.selections[1].input_metadata
    assert writer_input.file_path == str(tmp_path / "assistant.py")
    assert writer_input.content.startswith('"""OpenPilot Assistant')
    assert result.result_metadata.result.attributes["all_tools_succeeded"] is True


def test_tool_planning_defaults_missing_question_and_normalizes_readme_path(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path, {})
    executor = ToolPlanningTaskExecutor(runtime)

    tool_requests = executor._parse_decision_needs(
        SimpleNamespace(
            parsed_json={
                "decision_needs": [
                    {
                        "need_type": "readme_generation",
                        "target_path": str(tmp_path / "README.md"),
                        "attributes": {"content": "# Usage\n"},
                    }
                ]
            },
            content="",
        )
    )

    assert tool_requests[0]["tool_name"] == "readme_tool"
    assert tool_requests[0]["reason"] == f"readme_generation: {tmp_path / 'README.md'}"
    assert tool_requests[0]["input_metadata"]["project_path"] == str(tmp_path)


def test_tool_planning_moves_tool_input_fields_for_bug_fix_need(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path, {})
    executor = ToolPlanningTaskExecutor(runtime)

    tool_requests = executor._parse_decision_needs(
        SimpleNamespace(
            parsed_json={
                "decision_needs": [
                    {
                        "need_type": "bug_fix_tool",
                        "question": "Fix the failing assistant test.",
                        "command": "python test_assistant.py",
                        "file_paths": ["assistant.py", "test_assistant.py"],
                        "timeout": 30,
                        "max_iterations": 5,
                    }
                ]
            },
            content="",
        )
    )

    assert tool_requests[0]["tool_name"] == "bug_fix_tool"
    assert tool_requests[0]["input_metadata"]["file_paths"] == ["assistant.py", "test_assistant.py"]
    assert tool_requests[0]["input_metadata"]["max_iterations"] == 5
    assert tool_requests[0]["timeout_override"] == 30


def test_tool_planning_executor_chains_code_unit_to_patch_writer(tmp_path) -> None:
    task = Task(id="task", description="Add helper to app")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "code_unit_generate",
                    "question": "generate helper function",
                    "target_path": "app.py",
                    "operation_kind": "add_symbol",
                    "symbol_name": "added",
                    "symbol_type": "function",
                    "attributes": {"task_description": "add helper"},
                },
                {
                    "need_type": "file_write",
                    "question": "insert helper function",
                    "target_path": "app.py",
                    "operation_kind": "add_symbol",
                },
            ]
        },
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == [
        "code_unit_generator",
        "file_patch_writer",
    ]
    patch_input = runtime.tool_executor.selections[1].input_metadata.to_params()
    assert patch_input["generated_unit"] == "def added():\n    return 2"
    assert patch_input["operation_kind"] == "add_symbol"


def test_tool_event_loop_recovers_from_text_language_code_generator(tmp_path) -> None:
    task = Task(id="task", description="Design personal assistant")
    runtime = FakeRuntime(
        tmp_path,
        [
            {
                "decision_needs": [
                    {
                        "need_type": "code_generation",
                        "question": "write design prose",
                        "attributes": {"task_description": "outline design", "language": "text"},
                    }
                ]
            },
            {
                "decision_needs": [
                    {
                        "need_type": "file_write",
                        "question": "write design note",
                        "target_path": "DESIGN.md",
                        "attributes": {"content": "# Design\n"},
                    }
                ]
            },
        ],
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert result.error is None
    attrs = result.result_metadata.result.attributes
    assert attrs["all_tools_succeeded"] is True
    assert len(runtime.llm_client.requests) == 2
    assert attrs["tool_results"][0]["success"] is False
    assert attrs["tool_results"][0]["error"].startswith("Unsupported language")
    assert attrs["tool_results"][1]["tool"] == "file_writer"
    loop = attrs["tool_loop"]
    assert loop["recoverable_errors"][0]["error_type"] == "UnsupportedLanguage"
    assert any(event["event_type"] == "error" for event in loop["events"])


def test_tool_event_loop_retries_recoverable_code_generator_timeout(tmp_path) -> None:
    task = Task(id="task", description="Generate app")
    decision = {
        "decision_needs": [
            {
                "need_type": "code_generation",
                "question": "generate app",
                "attributes": {"task_description": "make app", "language": "python"},
            },
            {
                "need_type": "file_write",
                "question": "write app",
                "target_path": str(tmp_path / "app.py"),
            },
        ]
    }
    runtime = FakeRuntime(tmp_path, [decision, decision])
    runtime.tool_executor = TimeoutThenSuccessExecutor()
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == [
        "code_generator",
        "code_generator",
        "file_writer",
    ]
    assert len(runtime.llm_client.requests) == 1
    loop = result.result_metadata.result.attributes["tool_loop"]
    assert loop["recoverable_errors"][0]["error_type"] == "LLMTimeoutError"
    assert "bounded request" in loop["recoverable_errors"][0]["suggested_recovery"]
    assert loop["retry_count"] == 1
    assert loop["fallback_count"] == 0
    entries = [
        json.loads(line)
        for line in (tmp_path / "tool_planning.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(entry["event_type"] == "tool_loop_direct_retry_scheduled" for entry in entries)


def test_tool_event_loop_uses_local_code_fallback_after_repeated_provider_timeout(tmp_path) -> None:
    task = Task(id="task", description="Generate app")
    decision = {
        "decision_needs": [
            {
                "need_type": "code_generation",
                "question": "generate app",
                "attributes": {"task_description": "make app", "language": "python"},
            },
            {
                "need_type": "file_write",
                "question": "write app",
                "target_path": str(tmp_path / "app.py"),
            },
        ]
    }
    runtime = FakeRuntime(tmp_path, decision)
    runtime.tool_executor = TimeoutUntilLocalFallbackExecutor()
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == [
        "code_generator",
        "code_generator",
        "code_generator",
        "file_writer",
    ]
    fallback_selection = runtime.tool_executor.selections[2]
    assert fallback_selection.input_metadata.prompt_context["local_fallback_after_provider_failure"] is True
    writer_selection = runtime.tool_executor.selections[3]
    assert writer_selection.input_metadata.content == "print('local fallback')"
    assert len(runtime.llm_client.requests) == 1
    loop = result.result_metadata.result.attributes["tool_loop"]
    assert loop["retry_count"] == 1
    assert loop["fallback_count"] == 1
    entries = [
        json.loads(line)
        for line in (tmp_path / "tool_planning.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(entry["event_type"] == "tool_loop_local_fallback_scheduled" for entry in entries)


def test_tool_event_loop_emits_lifecycle_events_to_ui_hook(tmp_path) -> None:
    task = Task(id="task", description="Generate and write app")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "code_generation",
                    "question": "generate code",
                    "attributes": {"task_description": "make app"},
                },
                {
                    "need_type": "file_write",
                    "question": "write file",
                    "target_path": "app.py",
                },
            ]
        },
    )
    runtime.enhanced_ui = FakeUI()
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    ui_events = runtime.enhanced_ui.events
    assert [event["event_type"] for event in ui_events[:3]] == ["pending", "running", "completed"]
    assert ui_events[0]["call_id"] == "task:r1:c1"
    assert ui_events[0]["tool_context"]["call_id"] == "task:r1:c1"
    assert any(event["tool_name"] == "file_writer" and event["event_type"] == "completed" for event in ui_events)


def test_tool_event_loop_emits_recoverable_error_to_ui_hook(tmp_path) -> None:
    task = Task(id="task", description="Design personal assistant")
    runtime = FakeRuntime(
        tmp_path,
        [
            {
                "decision_needs": [
                    {
                        "need_type": "code_generation",
                        "question": "write design prose",
                        "attributes": {"task_description": "outline design", "language": "text"},
                    }
                ]
            },
            {
                "decision_needs": [
                    {
                        "need_type": "file_write",
                        "question": "write design note",
                        "target_path": "DESIGN.md",
                        "attributes": {"content": "# Design\n"},
                    }
                ]
            },
        ],
    )
    runtime.enhanced_ui = FakeUI()
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    error_events = [event for event in runtime.enhanced_ui.events if event["event_type"] == "error"]
    assert error_events
    assert error_events[0]["tool_name"] == "code_generator"
    assert error_events[0]["recoverable"] is True
    assert error_events[0]["tool_error"]["error_type"] == "UnsupportedLanguage"


def test_tool_event_loop_ui_hook_failure_does_not_fail_task(tmp_path) -> None:
    task = Task(id="task", description="Generate app")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "code_generation",
                    "question": "generate code",
                    "attributes": {"task_description": "make app"},
                }
            ]
        },
    )
    runtime.enhanced_ui = BrokenEventHookUI()
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    entries = [
        json.loads(line)
        for line in (tmp_path / "tool_planning.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(entry.get("event_type") == "tool_event_ui_hook_failed" for entry in entries)


def test_tool_event_loop_normalizes_command_executor_execute_mode(tmp_path) -> None:
    task = Task(id="task", description="Run command")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "command_check",
                    "question": "run command",
                    "command": "python main.py",
                    "attributes": {"mode": "execute"},
                }
            ]
        },
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert runtime.tool_executor.selections[0].input_metadata.mode == "automatic"
    assert runtime.tool_executor.selections[0].input_metadata.cwd == str(tmp_path)
    assert runtime.tool_executor.selections[0].input_metadata.env["VIRTUAL_ENV"] == str(tmp_path / ".venv")
    loop = result.result_metadata.result.attributes["tool_loop"]
    assert loop["tool_contexts"][0]["cwd"] == str(tmp_path)
    assert loop["tool_contexts"][0]["python_command"].endswith("/.venv/bin/python")


def test_tool_planning_executor_routes_decision_needs_through_tool_router(tmp_path) -> None:
    task = Task(id="task", description="Run tests")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "command_check",
                    "question": "verify generated project",
                    "command": "pytest",
                    "attributes": {"mode": "automatic"},
                }
            ]
        },
    )
    state = RuntimeStateMetadata(goal="build app")
    runtime.runtime_controller = SimpleNamespace(
        state=state,
        router=ToolRouter(runtime.tool_registry),
        edit_guard=EditGuard(),
        file_selector=FileSelector(),
        state_updater=StateUpdater(),
        verifier=RuntimeVerifier(),
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert runtime.tool_executor.selections[0].tool_name == "command_executor"
    assert runtime.tool_executor.selections[0].input_metadata.command == "pytest"
    assert state.tool_history[0]["tool_name"] == "command_executor"


def test_tool_loop_does_not_execute_file_mutation_when_prepared_checkpoint_fails(tmp_path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("before", encoding="utf-8")
    task = Task(id="task", description="Update note")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "file_write",
                    "question": "update note",
                    "target_path": str(target),
                    "operation_kind": "file_replace",
                    "attributes": {"content": "after", "overwrite": True},
                }
            ]
        },
    )
    state = RuntimeStateMetadata(goal="Update note")
    prepared_calls = []
    runtime.runtime_controller = SimpleNamespace(
        state=state,
        router=ToolRouter(runtime.tool_registry),
        edit_guard=EditGuard(),
        file_selector=FileSelector(),
        state_updater=StateUpdater(),
        verifier=RuntimeVerifier(),
        prepare_tool_call=lambda tool_call, selection: prepared_calls.append((tool_call.call_id, selection.tool_name)) or False,
        observe_tool_result=lambda *_args: True,
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.FAILED
    assert "prepared checkpoint was not durable" in result.error
    assert prepared_calls == [("task:r1:c1", "file_writer")]
    assert runtime.tool_executor.selections == []
    assert target.read_text(encoding="utf-8") == "before"


def test_tool_loop_exposes_later_validation_command_before_mutation_checkpoint(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    target.write_text("value = 1\n", encoding="utf-8")
    task = Task(id="task", description="Update and validate calculator", write_files=[str(target)])
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "file_write",
                    "question": "update calculator",
                    "target_path": str(target),
                    "operation_kind": "file_replace",
                    "attributes": {"content": "value = 2\n", "overwrite": True},
                },
                {
                    "need_type": "command_check",
                    "question": "run calculator tests",
                    "command": "python -m pytest -q test_calculator.py",
                    "attributes": {"cwd": str(tmp_path), "timeout": 30},
                },
                {
                    "need_type": "command_check",
                    "question": "run calculator lint",
                    "command": "python -m compileall -q calculator.py",
                    "attributes": {"cwd": str(tmp_path), "timeout": 30},
                },
            ]
        },
    )
    state = RuntimeStateMetadata(goal="Update calculator")
    pending_plans = []
    runtime.runtime_controller = SimpleNamespace(
        state=state,
        router=ToolRouter(runtime.tool_registry),
        edit_guard=EditGuard(),
        file_selector=FileSelector(),
        state_updater=StateUpdater(),
        verifier=RuntimeVerifier(),
        set_pending_verification=lambda plan: pending_plans.append(plan),
        prepare_tool_call=lambda *_args: False,
        observe_tool_result=lambda *_args: True,
    )

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.FAILED
    assert pending_plans
    assert pending_plans[0].commands == [
        "python -m pytest -q test_calculator.py",
        "python -m compileall -q calculator.py",
    ]
    assert [spec.cwd for spec in pending_plans[0].command_specs] == [
        str(tmp_path),
        str(tmp_path),
    ]


def test_validate_subtask_drops_commands_outside_exact_validation_contract(tmp_path) -> None:
    task = Task(
        id="validate-pytest",
        description="Run pytest only",
        kind="validate",
        validation_command="python -m pytest -q",
    )
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "command_check",
                    "question": "run required pytest",
                    "command": "python -m pytest -q",
                },
                {
                    "need_type": "command_check",
                    "question": "run later compile check",
                    "command": "python -m compileall -q calculator.py",
                },
            ]
        },
    )

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [
        selection.input_metadata.command for selection in runtime.tool_executor.selections
    ] == ["python -m pytest -q"]
    assert runtime.llm_client.requests[0].reasoning_policy.mode == ReasoningMode.DISABLED


def test_general_task_keeps_provider_reasoning_default(tmp_path) -> None:
    task = Task(id="general", description="Resolve an ambiguous project issue")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})

    ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert (
        runtime.llm_client.requests[0].reasoning_policy.mode
        == ReasoningMode.PROVIDER_DEFAULT
    )
    assert runtime.llm_client.requests[0].trace_info["reasoning_complexity"] == "standard"


def test_multi_target_implementation_keeps_provider_reasoning_default(tmp_path) -> None:
    task = Task(
        id="multi-write",
        description="Coordinate two related edits",
        kind="implement",
        read_files=[str(tmp_path / "a.py"), str(tmp_path / "b.py")],
        write_files=[str(tmp_path / "a.py"), str(tmp_path / "b.py")],
    )
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})

    ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert (
        runtime.llm_client.requests[0].reasoning_policy.mode
        == ReasoningMode.PROVIDER_DEFAULT
    )
    assert runtime.llm_client.requests[0].trace_info["reasoning_complexity"] == "complex"


def test_multi_target_implementation_uses_typed_complexity_route(tmp_path) -> None:
    task = Task(
        id="multi-write-complexity",
        description="Coordinate three related edits",
        kind="implement",
        read_files=[str(tmp_path / "a.py"), str(tmp_path / "b.py"), str(tmp_path / "c.py")],
        write_files=[str(tmp_path / "a.py"), str(tmp_path / "b.py")],
    )
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)

    assert executor._reasoning_complexity_for_task(task) is ReasoningDecisionComplexity.COMPLEX


def test_implement_subtask_drops_future_validation_commands(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    target.write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    task = Task(
        id="implement-divide",
        description="Modify only divide",
        kind="implement",
        read_files=[str(target)],
        write_files=[str(target)],
    )
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "code_symbol_modify",
                    "question": "modify divide",
                    "target_path": str(target),
                    "operation_kind": "modify_symbol",
                    "symbol_name": "divide",
                },
                {
                    "need_type": "file_write",
                    "question": "apply divide change",
                    "target_path": str(target),
                    "operation_kind": "modify_symbol",
                },
                {
                    "need_type": "command_check",
                    "question": "run future pytest",
                    "command": "python -m pytest -q",
                },
            ]
        },
    )

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert "command_executor" not in [
        selection.tool_name for selection in runtime.tool_executor.selections
    ]


def test_tool_planning_executor_normalizes_top_level_code_need_fields(tmp_path) -> None:
    task = Task(id="task", description="Validate generated assistant")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "code_execution",
                    "question": "run generated smoke test",
                    "code": "print('assistant ok')",
                    "language": "python",
                    "timeout": 5,
                }
            ]
        },
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert runtime.tool_executor.selections[0].tool_name == "code_executor"
    assert runtime.tool_executor.selections[0].input_metadata.code == "print('assistant ok')"
    assert runtime.tool_executor.selections[0].timeout_override == 5
    entries = [
        json.loads(line)
        for line in (tmp_path / "tool_planning.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(entry["event_type"] == "decision_need_normalized" and entry["level"] == "DEBUG" for entry in entries)


def test_tool_planning_executor_normalizes_top_level_file_write_content(tmp_path) -> None:
    task = Task(id="task", description="Write generated file")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "file_write",
                    "question": "write generated file",
                    "target_path": "assistant.py",
                    "content": "print('assistant ok')",
                    "overwrite": True,
                }
            ]
        },
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert runtime.tool_executor.selections[0].tool_name == "file_writer"
    assert runtime.tool_executor.selections[0].input_metadata.content == "print('assistant ok')"
    assert runtime.tool_executor.selections[0].input_metadata.overwrite is True


def test_tool_planning_executor_normalizes_null_candidate_paths(tmp_path) -> None:
    task = Task(id="task", description="Generate assistant")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "code_generation",
                    "question": "Generate the Python assistant code",
                    "target_path": None,
                    "candidate_paths": None,
                    "query": None,
                    "command": None,
                    "risk_level": "low",
                    "attributes": {"language": "python", "task_description": "print hello"},
                }
            ]
        },
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert runtime.tool_executor.selections[0].tool_name == "code_generator"
    entries = [
        json.loads(line)
        for line in (tmp_path / "tool_planning.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        entry["event_type"] == "decision_need_normalized"
        and "candidate_paths:null_to_default" in entry["payload"]["output_summary"]["normalized_fields"]
        for entry in entries
    )


def test_tool_planning_executor_schema_error_is_structured_and_logged(tmp_path) -> None:
    task = Task(id="task", description="Validate generated assistant")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "code_execution",
                    "question": "run generated smoke test",
                    "surprise": "not a tool field",
                }
            ]
        },
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.FAILED
    assert result.result_metadata.failure.error_type == "DecisionNeedValidationError"
    assert result.result_metadata.failure.details["failed_tool"] == "tool_planning_executor"
    assert "surprise" in result.result_metadata.failure.details["invalid_fields"]
    entries = [
        json.loads(line)
        for line in (tmp_path / "tool_planning.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(entry["event_type"] == "decision_need_schema_error" and entry["level"] == "ERROR" for entry in entries)


def test_tool_planning_executor_rejects_old_tool_calls_protocol(tmp_path) -> None:
    task = Task(id="task", description="Use available tools")
    runtime = FakeRuntime(
        tmp_path,
        {"tool_calls": [{"tool_name": "file_writer", "reason": "write output", "input_metadata": {"file_path": "note.md"}}]},
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.FAILED
    assert result.error == "Tool planning requires decomposition after empty decision_needs plan"
    assert result.result_metadata.failure.error_type == "DecisionNeedResolutionError"
    assert result.result_metadata.failure.details["problem_signal"]["category"] == "tool_contract"
    assert result.result_metadata.failure.details["resolution_plan"]["strategy"] == "direct_retry"
    assert len(runtime.llm_client.requests) == 2


def test_tool_event_loop_missing_required_field_is_recoverable(tmp_path) -> None:
    task = Task(id="task", description="Generate code")
    runtime = FakeRuntime(
        tmp_path,
        [
            {
                "decision_needs": [
                    {
                        "need_type": "file_write",
                        "question": "write generated code",
                        "target_path": "app.py",
                    }
                ]
            },
            {
                "decision_needs": [
                    {
                        "need_type": "file_write",
                        "question": "write generated code",
                        "target_path": "app.py",
                        "attributes": {"content": "print('ok')"},
                    }
                ]
            },
        ],
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    loop = result.result_metadata.result.attributes["tool_loop"]
    assert loop["recoverable_errors"][0]["error_type"] == "MissingRequiredInput"


def test_tool_router_blocks_incomplete_directory_need_before_tool_call(tmp_path) -> None:
    task = Task(id="task", description="Validate files")
    state = RuntimeStateMetadata(goal="validate files")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "project_structure",
                    "question": "read project files",
                    "attributes": {"pattern": "*.py"},
                }
            ]
        },
    )
    runtime.runtime_controller = SimpleNamespace(
        state=state,
        router=ToolRouter(runtime.tool_registry),
        edit_guard=EditGuard(),
        file_selector=FileSelector(),
        state_updater=StateUpdater(),
        verifier=RuntimeVerifier(),
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.FAILED
    assert result.error == "Tool planning requires decomposition after empty decision_needs plan"
    assert result.result_metadata.failure.details["problem_signal"]["category"] == "tool_contract"
    assert "read project files" in state.unknowns


def test_tool_event_loop_execution_value_error_can_recover(tmp_path) -> None:
    task = Task(id="task", description="Validate files")
    runtime = FakeRuntime(
        tmp_path,
        [
            {
                "decision_needs": [
                    {
                        "need_type": "project_structure",
                        "question": "read project files",
                        "target_path": str(tmp_path),
                    }
                ]
            },
            {
                "decision_needs": [
                    {
                        "need_type": "file_write",
                        "question": "record validation",
                        "target_path": "validation.md",
                        "attributes": {"content": "ok"},
                    }
                ]
            },
        ],
    )
    runtime.tool_executor = InvalidMultiFileReaderExecutor()
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    loop = result.result_metadata.result.attributes["tool_loop"]
    assert loop["recoverable_errors"][0]["error_type"] == "ValueError"
    assert loop["recoverable_errors"][0]["recoverable"] is True
    assert runtime.tool_executor.selections[-1].tool_name == "file_writer"


def test_directory_discovery_plan_does_not_send_directory_to_file_reader(tmp_path) -> None:
    task = Task(id="task", description="Document and test project")
    (tmp_path / "main.py").write_text("print('ok')", encoding="utf-8")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "command_check",
                    "question": "What files and directories exist in the project folder?",
                    "command": f"ls -la {tmp_path}",
                    "attributes": {"mode": "automatic"},
                },
                {
                    "need_type": "file_read",
                    "question": "If there are existing Python files, read them to understand the current assistant code.",
                    "target_path": str(tmp_path),
                },
            ]
        },
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == [
        "command_executor",
        "multi_file_reader",
    ]
    assert runtime.tool_executor.selections[1].input_metadata.directory_path == str(tmp_path)
    assert runtime.tool_executor.selections[1].input_metadata.pattern == "*"


def test_tool_event_loop_reports_file_reader_directory_contract_error(tmp_path) -> None:
    task = Task(id="task", description="Read a project directory incorrectly")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)
    executor._parse_decision_needs = lambda _response: [  # type: ignore[method-assign]
        {
            "tool_name": "file_reader",
            "reason": "read directory",
            "input_metadata": {"file_path": str(tmp_path)},
        }
    ]

    result = ToolEventLoopRunner(executor, max_steps=1).run(task, "prompt")

    assert result.success is False
    assert result.tool_results[0]["tool"] == "file_reader"
    assert result.tool_results[0]["call_id"] == "task:r1:c1"
    assert "expected a file path" in result.tool_results[0]["error"]
    assert "multi_file_reader" in result.tool_results[0]["suggested_recovery"]
    assert result.loop_metadata.final_error is not None
    assert result.loop_metadata.final_error.details["tool_name"] == "file_reader"
    assert result.loop_metadata.final_error.details["call_id"] == "task:r1:c1"


def test_tool_event_loop_applies_dynamic_completion_budget_and_records_evidence(tmp_path) -> None:
    task = Task(id="task", description="Inspect project")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    state = RuntimeStateMetadata(goal="inspect project", phase=AgentPhase.EXECUTE)
    runtime.runtime_controller = SimpleNamespace(state=state)
    executor = ToolPlanningTaskExecutor(runtime)
    executor._parse_decision_needs = lambda _response: []  # type: ignore[method-assign]

    result = ToolEventLoopRunner(executor, max_steps=5).run(task, "prompt")

    assert result.success is True
    request = runtime.llm_client.requests[0]
    assert request.max_tokens == 2000
    assert request.trace_info["completion_budget"] == {
        "purpose": "tool_event_decision",
        "round_index": 1,
        "calls_remaining": 5,
        "static_ceiling": 2000,
        "dynamic_limit": 2000,
        "total_remaining": 12000,
    }


def test_tool_event_loop_consumes_provider_completion_usage_once(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    state = RuntimeStateMetadata(goal="inspect project", phase=AgentPhase.EXECUTE)
    runtime.runtime_controller = SimpleNamespace(state=state)
    runner = ToolEventLoopRunner(ToolPlanningTaskExecutor(runtime))

    state.budget.consume_tool_event_completion(2000)
    runner._reconcile_completion_usage(
        state.budget,
        SimpleNamespace(usage={"completion_tokens": 321}, provider_details={}),
        reserved=2000,
    )
    state.budget.consume_tool_event_completion(2000)
    runner._reconcile_completion_usage(
        state.budget,
        SimpleNamespace(
            usage={"completion_tokens": 321},
            provider_details={"recovery_replay": True},
        ),
        reserved=2000,
    )

    assert state.budget.tool_event_completion_tokens_used == 321


def test_tool_event_loop_reconciles_invalid_json_failure_usage_and_grants_length_recovery(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    state = RuntimeStateMetadata(goal="inspect project", phase=AgentPhase.EXECUTE)
    runtime.runtime_controller = SimpleNamespace(state=state)
    runner = ToolEventLoopRunner(ToolPlanningTaskExecutor(runtime))
    error = InvalidLLMResponseError(
        "truncated JSON",
        response_text='{"decision_needs": [',
        usage={"completion_tokens": 1709, "completion_tokens_details": {"reasoning_tokens": 1600}},
        finish_reason="length",
    )

    state.budget.consume_tool_event_completion(2000)
    runner._reconcile_completion_failure(state.budget, error, reserved=2000)

    assert state.budget.tool_event_completion_tokens_used == 1709
    assert state.budget.tool_event_completion_recovery_bonus == 400


def test_tool_event_loop_refunds_empty_invalid_response_without_usage(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    state = RuntimeStateMetadata(goal="inspect project", phase=AgentPhase.EXECUTE)
    runtime.runtime_controller = SimpleNamespace(state=state)
    runner = ToolEventLoopRunner(ToolPlanningTaskExecutor(runtime))
    error = InvalidLLMResponseError("empty JSON response", response_text="")

    state.budget.consume_tool_event_completion(2000)
    runner._reconcile_completion_failure(state.budget, error, reserved=2000)

    assert state.budget.tool_event_completion_tokens_used == 0
    assert state.budget.tool_event_completion_recovery_bonus == 0


def test_tool_event_loop_limits_json_repair_to_one_provider_attempt(tmp_path) -> None:
    class RepairAwareLLM:
        def __init__(self) -> None:
            self.max_retries = []

        def complete(self, request, max_retries=3):
            self.max_retries.append(max_retries)
            return SimpleNamespace(request=request)

    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    runtime.llm_client = RepairAwareLLM()
    runner = ToolEventLoopRunner(ToolPlanningTaskExecutor(runtime))

    runner._complete_tool_event_request(SimpleNamespace())

    assert runtime.llm_client.max_retries == [1]


def _recovery_prompt_error(input_metadata: ToolInputMetadata) -> ToolErrorMetadata:
    return ToolErrorMetadata(
        session_id="session-observation-mask",
        task_id="task-observation-mask",
        step_id="step-observation-mask",
        call_id="call-observation-mask",
        tool_name="code_editor",
        error_type="RecoverableToolError",
        error_message="The tool input must be revised.",
        recoverable=True,
        suggested_recovery="Keep the target and operation semantics, then revise the payload.",
        input_metadata=input_metadata,
    )


def _recovery_errors_payload(prompt: str) -> list[dict]:
    marker = "Recoverable errors:\n"
    assert marker in prompt
    return json.loads(prompt.split(marker, 1)[1])


def _expected_observation_mask(value) -> dict[str, object]:
    if isinstance(value, str):
        serialized = value
    else:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return {
        "observation_masked": True,
        "chars": len(serialized),
        "sha256": f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}",
    }


def test_tool_event_recovery_prompt_masks_large_observation_fields_and_preserves_control_fields(
    tmp_path,
) -> None:
    large_code = "def generated():\n    return 'code'\n" * 320
    large_content = "generated file content\n" * 500
    large_prompt_context = {
        "prior_tool_output": "TRACE-LINE\n" * 900,
        "nested": {"diagnosis": "DIAGNOSIS\n" * 300},
    }
    large_stdout = "pytest progress and captured output\n" * 500
    input_metadata = ToolInputMetadata(
        tool_name="code_editor",
        file_path=str(tmp_path / "calculator.py"),
        command="python -m pytest -q",
        operation_kind="modify_symbol",
        symbol_name="divide",
        symbol_type="function",
        mode="automatic",
        system_prompt="Never weaken the original execution authority. " * 100,
        instruction="Preserve the exact task and validation contract. " * 100,
        code=large_code,
        content=large_content,
        prompt_context=large_prompt_context,
        stdout=large_stdout,
    )
    runner = ToolEventLoopRunner(
        ToolPlanningTaskExecutor(FakeRuntime(tmp_path, {"decision_needs": []}))
    )

    prompt = runner._build_recovery_prompt(
        "initial prompt",
        [_recovery_prompt_error(input_metadata)],
    )

    projected = _recovery_errors_payload(prompt)[0]["input_metadata"]
    assert projected["file_path"] == str(tmp_path / "calculator.py")
    assert projected["command"] == "python -m pytest -q"
    assert projected["operation_kind"] == "modify_symbol"
    assert projected["symbol_name"] == "divide"
    assert projected["symbol_type"] == "function"
    assert projected["mode"] == "automatic"
    assert projected["system_prompt"] == input_metadata.system_prompt
    assert projected["instruction"] == input_metadata.instruction
    assert projected["code"] == _expected_observation_mask(large_code)
    assert projected["content"] == _expected_observation_mask(large_content)
    assert projected["prompt_context"] == _expected_observation_mask(large_prompt_context)
    assert projected["stdout"] == _expected_observation_mask(large_stdout)
    assert large_code not in prompt
    assert large_content not in prompt
    assert large_stdout not in prompt


def test_tool_event_recovery_prompt_uses_safe_allowlist_without_leaking_or_hashing_unknown_fields(
    tmp_path,
) -> None:
    short_env_secret = "env-secret-7f3a"
    short_task_secret = "task-secret-8b4c"
    short_attribute_secret = "attribute-secret-9d5e"
    short_runtime_secret = "runtime-secret-0a6f"
    nested_validation_secret = "validation-secret-1b7g"
    huge_unknown_value = "UNLISTED-PRIVATE-CONTEXT-" * 500
    large_code = "sensitive generated code\n" * 500
    long_system_prompt = "Preserve the authoritative system boundary. " * 100
    long_instruction = "Preserve the exact recovery operation. " * 100
    input_metadata = ToolInputMetadata.from_mapping(
        "code_editor",
        {
            "file_path": str(tmp_path / "calculator.py"),
            "command": "python -m pytest -q",
            "operation_kind": "modify_symbol",
            "symbol_name": "divide",
            "symbol_type": "function",
            "mode": "automatic",
            "system_prompt": long_system_prompt,
            "instruction": long_instruction,
            "validation_context": {
                "command": "python -m pytest -q",
                "env": {"API_TOKEN": nested_validation_secret},
            },
            "env": {"API_TOKEN": short_env_secret},
            "context": huge_unknown_value,
            "task_description": short_task_secret,
            "attribute_token": short_attribute_secret,
            "huge_unlisted_field": huge_unknown_value,
            "_runtime_token": short_runtime_secret,
            "code": large_code,
        },
    )
    runner = ToolEventLoopRunner(
        ToolPlanningTaskExecutor(FakeRuntime(tmp_path, {"decision_needs": []}))
    )

    prompt = runner._build_recovery_prompt(
        "initial prompt",
        [_recovery_prompt_error(input_metadata)],
    )

    projected = _recovery_errors_payload(prompt)[0]["input_metadata"]
    assert projected["file_path"] == str(tmp_path / "calculator.py")
    assert projected["command"] == "python -m pytest -q"
    assert projected["operation_kind"] == "modify_symbol"
    assert projected["symbol_name"] == "divide"
    assert projected["symbol_type"] == "function"
    assert projected["mode"] == "automatic"
    assert projected["system_prompt"] == long_system_prompt
    assert projected["instruction"] == long_instruction
    assert projected["validation_context"]["command"] == "python -m pytest -q"
    assert projected["validation_context"]["env"] == {"redacted": True}
    assert projected["code"] == _expected_observation_mask(large_code)
    assert {"env", "context", "task_description", "attributes", "runtime_handles"}.isdisjoint(
        projected
    )
    for private_value in (
        short_env_secret,
        short_task_secret,
        short_attribute_secret,
        short_runtime_secret,
        nested_validation_secret,
        huge_unknown_value,
    ):
        assert private_value not in prompt
        assert hashlib.sha256(private_value.encode("utf-8")).hexdigest() not in prompt


def test_tool_event_recovery_prompt_keeps_short_observation_fields_unmasked(tmp_path) -> None:
    input_metadata = ToolInputMetadata(
        tool_name="code_editor",
        file_path=str(tmp_path / "calculator.py"),
        code="return 1",
        content="small replacement",
        prompt_context={"latest_result": "2 passed"},
        stdout="2 passed",
    )
    runner = ToolEventLoopRunner(
        ToolPlanningTaskExecutor(FakeRuntime(tmp_path, {"decision_needs": []}))
    )

    prompt = runner._build_recovery_prompt(
        "initial prompt",
        [_recovery_prompt_error(input_metadata)],
    )

    projected = _recovery_errors_payload(prompt)[0]["input_metadata"]
    assert projected["code"] == "return 1"
    assert projected["content"] == "small replacement"
    assert projected["prompt_context"] == {"latest_result": "2 passed"}
    assert projected["stdout"] == "2 passed"


def test_tool_event_recovery_prompt_masking_is_stable_and_does_not_mutate_source(tmp_path) -> None:
    input_metadata = ToolInputMetadata(
        tool_name="code_editor",
        file_path=str(tmp_path / "calculator.py"),
        operation_kind="modify_symbol",
        content="replacement\n" * 1000,
        prompt_context={"z": "last" * 1000, "a": "first" * 1000},
    )
    error = _recovery_prompt_error(input_metadata)
    source_before = error.model_dump(mode="json")
    runner = ToolEventLoopRunner(
        ToolPlanningTaskExecutor(FakeRuntime(tmp_path, {"decision_needs": []}))
    )

    first = runner._build_recovery_prompt("initial prompt", [error])
    second = runner._build_recovery_prompt("initial prompt", [error])

    assert first.encode("utf-8") == second.encode("utf-8")
    assert error.model_dump(mode="json") == source_before


def test_tool_event_recovery_prompt_large_observation_increment_is_bounded(tmp_path) -> None:
    initial_prompt = "initial planning prompt"
    huge = "0123456789abcdef" * 20_000
    errors = [
        _recovery_prompt_error(
            ToolInputMetadata(
                tool_name="code_editor",
                file_path=str(tmp_path / f"target-{index}.py"),
                command="python -m pytest -q",
                operation_kind="modify_symbol",
                symbol_name=f"symbol_{index}",
                mode="automatic",
                code=huge,
                content=huge,
                prompt_context={"observation": huge},
                stdout=huge,
            )
        )
        for index in range(3)
    ]
    runner = ToolEventLoopRunner(
        ToolPlanningTaskExecutor(FakeRuntime(tmp_path, {"decision_needs": []}))
    )

    prompt = runner._build_recovery_prompt(initial_prompt, errors)

    assert len(prompt) - len(initial_prompt) <= 8_192
    assert huge not in prompt


def test_tool_event_loop_reports_invented_intermediate_file_with_recovery(tmp_path) -> None:
    task = Task(id="task", description="Read hallucinated subtask plan")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)
    invented_path = tmp_path / "results" / "openpilot" / "subtask_0.md"
    executor._parse_decision_needs = lambda _response: [  # type: ignore[method-assign]
        {
            "tool_name": "file_reader",
            "reason": "read invented plan",
            "input_metadata": {"file_path": str(invented_path)},
        }
    ]

    result = ToolEventLoopRunner(executor, max_steps=1).run(task, "prompt")

    assert result.success is False
    assert result.tool_results[0]["tool"] == "file_reader"
    assert "File not found" in result.tool_results[0]["error"]
    assert "shared execution history" in result.tool_results[0]["suggested_recovery"]
    assert result.loop_metadata.final_error is not None
    assert result.loop_metadata.final_error.details["error_type"] == "InventedIntermediateFile"


def test_tool_event_loop_rejects_generated_placeholder_file_content(tmp_path) -> None:
    task = Task(id="task", description="Write placeholder")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)
    executor._parse_decision_needs = lambda _response: [  # type: ignore[method-assign]
        {
            "tool_name": "file_writer",
            "reason": "write placeholder",
            "input_metadata": {
                "file_path": str(tmp_path / "assistant.py"),
                "content": "PLACEHOLDER - will be replaced with actual generated code",
            },
        }
    ]

    result = ToolEventLoopRunner(executor, max_steps=1).run(task, "prompt")

    assert result.success is False
    assert result.tool_results[0]["tool"] == "file_writer"
    assert "generated placeholder" in result.tool_results[0]["error"]
    assert "Regenerate real content" in result.tool_results[0]["suggested_recovery"]
    assert result.loop_metadata.recoverable_errors[0].error_type == "GeneratedPlaceholderContent"


def test_tool_event_loop_rejects_chinese_placeholder_file_content(tmp_path) -> None:
    task = Task(id="task", description="Write placeholder")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)
    executor._parse_decision_needs = lambda _response: [  # type: ignore[method-assign]
        {
            "tool_name": "file_writer",
            "reason": "write placeholder",
            "input_metadata": {
                "file_path": str(tmp_path / "assistant.py"),
                "content": "# 代码将由code_generation生成后填充，此处占位",
            },
        }
    ]

    result = ToolEventLoopRunner(executor, max_steps=1).run(task, "prompt")

    assert result.success is False
    assert result.tool_results[0]["tool"] == "file_writer"
    assert "generated placeholder" in result.tool_results[0]["error"]


def test_tool_event_loop_allows_config_placeholder_inside_substantive_code(tmp_path) -> None:
    task = Task(id="task", description="Write generated app")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)
    content = "API_KEY = 'YOUR_API_KEY_PLACEHOLDER'\n\ndef main():\n    print('ok')\n"
    executor._parse_decision_needs = lambda _response: [  # type: ignore[method-assign]
        {
            "tool_name": "file_writer",
            "reason": "write implementation",
            "input_metadata": {
                "file_path": str(tmp_path / "assistant.py"),
                "content": content,
            },
        }
    ]

    result = ToolEventLoopRunner(executor, max_steps=1).run(task, "prompt")

    assert result.success is True
    assert result.tool_results[0]["success"] is True


def test_tool_event_loop_auto_verifies_file_writer_when_runtime_state_is_active(tmp_path) -> None:
    task = Task(id="task", description="Generate and write app")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "file_write",
                    "question": "write app file",
                    "target_path": "app.py",
                    "attributes": {"content": "print('ok')"},
                }
            ]
        },
    )
    state = RuntimeStateMetadata(goal="build app", phase=AgentPhase.EXECUTE)
    runtime.runtime_controller = SimpleNamespace(
        state=state,
        edit_guard=EditGuard(),
        file_selector=FileSelector(),
        state_updater=StateUpdater(),
        verifier=RuntimeVerifier(),
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == ["file_writer", "command_executor"]
    assert "app.py --help" in runtime.tool_executor.selections[1].input_metadata.command
    assert runtime.tool_executor.selections[1].input_metadata.timeout == 5
    attrs = result.result_metadata.result.attributes
    assert attrs["tool_results"][-1]["tool"] == "command_executor"
    assert state.verification_status == "passed"
    assert state.phase == AgentPhase.SUMMARIZE


def test_tool_event_loop_guards_mutating_command_executor(tmp_path) -> None:
    task = Task(id="task", description="Create project directory")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "command_check",
                    "question": "create directory",
                    "command": "mkdir generated",
                    "attributes": {"mode": "automatic"},
                }
            ]
        },
    )
    state = RuntimeStateMetadata(goal="create project", phase=AgentPhase.EXECUTE)
    runtime.runtime_controller = SimpleNamespace(
        state=state,
        edit_guard=EditGuard(),
        file_selector=FileSelector(),
        state_updater=StateUpdater(),
        verifier=RuntimeVerifier(),
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == ["command_executor", "command_executor"]
    assert runtime.tool_executor.selections[1].input_metadata.command == "pytest"
    assert state.planned_edits
    assert state.planned_edits[0].target_files == [str(tmp_path)]
    assert any(event.get("event_type") == "edit_guard" and event.get("approved") for event in state.tool_history)
    assert state.verification_status == "passed"
    assert state.phase == AgentPhase.SUMMARIZE


def test_tool_prompt_describes_required_any_of_contract(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})

    prompt = runtime.tool_io.format_tools_for_llm([MULTI_FILE_READER_DEFINITION])

    assert "one of: file_paths or directory_path [required]" in prompt


def test_tool_planning_executor_invalid_or_empty_plan_returns_failed_result(tmp_path) -> None:
    task = Task(id="task", description="Do impossible thing")
    executor = ToolPlanningTaskExecutor(FakeRuntime(tmp_path, {"decision_needs": []}))

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.FAILED
    assert result.error == "Tool planning requires decomposition after empty decision_needs plan"
    assert result.result_metadata.failure.error_type == "DecisionNeedResolutionError"
    assert result.result_metadata.failure.details["problem_signal"]["category"] == "planning_gap"
    assert result.result_metadata.failure.details["difficulty_assessment"]["level"] in {"simple", "moderate"}


def test_tool_planning_executor_read_only_empty_plan_with_evidence_summarizes(tmp_path) -> None:
    task = Task(
        id="task",
        description="请梳理 CLI 入口到主执行运行时的核心链路",
        kind="codebase_understanding",
        tags=["analysis"],
    )
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    state = RuntimeStateMetadata(goal=task.description, phase=AgentPhase.UNDERSTAND_PROJECT)
    state.add_fact("Observed CLI entrypoint in Code/src/ui/enhanced_cli.py")
    state.add_candidate_file("Code/src/ui/enhanced_cli.py", "file_read evidence")
    runtime.runtime_controller = SimpleNamespace(
        state=state,
        router=ToolRouter(runtime.tool_registry),
        edit_guard=EditGuard(),
        file_selector=FileSelector(),
        state_updater=StateUpdater(),
        verifier=RuntimeVerifier(),
    )
    context = TaskExecutionContext(
        task=task,
        parent_context={"goal": task.description, "project_path": str(tmp_path)},
        shared_state={},
        execution_history=[],
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, context)

    assert result.status == TaskStatus.COMPLETED
    assert runtime.tool_executor.selections == []
    assert state.phase == AgentPhase.SUMMARIZE
    assert state.completion_reason == "read-only analysis has enough evidence to synthesize"


def test_tool_planning_executor_read_only_empty_plan_without_evidence_uses_safe_inspection_fallback(tmp_path) -> None:
    task = Task(
        id="task",
        description="请梳理 CLI 入口到主执行运行时的核心链路",
        kind="codebase_understanding",
        tags=["analysis"],
    )
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    runtime.runtime_controller = SimpleNamespace(
        state=RuntimeStateMetadata(goal=task.description, phase=AgentPhase.UNDERSTAND_PROJECT),
        router=ToolRouter(runtime.tool_registry),
        edit_guard=EditGuard(),
        file_selector=FileSelector(),
        state_updater=StateUpdater(),
        verifier=RuntimeVerifier(),
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert len(runtime.tool_executor.selections) == 1
    assert runtime.tool_executor.selections[0].tool_name in {"file_reader", "multi_file_reader"}


def test_tool_planning_fallback_state_inherits_context_project_path(tmp_path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    task = Task(id="task", description="Inspect project structure", kind="codebase_understanding", tags=["analysis"])
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    controller = SimpleNamespace(
        state=None,
        router=ToolRouter(runtime.tool_registry),
        edit_guard=EditGuard(),
        file_selector=FileSelector(),
        state_updater=StateUpdater(),
        verifier=RuntimeVerifier(),
    )
    runtime.runtime_controller = controller
    executor = ToolPlanningTaskExecutor(runtime)
    executor._active_task = task
    executor._active_task_id = task.id
    executor._active_task_description = task.description
    executor._active_goal = task.description
    executor._active_context = TaskExecutionContext(
        task=task,
        parent_context={"goal": task.description, "project_path": str(project_dir)},
        shared_state={},
        execution_history=[],
    )

    requests = executor._route_decision_needs(
        {
            "decision_needs": [
                {
                    "need_type": "project_structure",
                    "question": "Inspect the repository root",
                }
            ]
        }
    )

    assert requests
    assert controller.state is not None
    assert f"Project path: {project_dir.resolve(strict=False)}" in controller.state.known_facts
    assert str(project_dir.resolve(strict=False)) in controller.state.candidate_files
    assert requests[0]["input_metadata"]["project_path"] == str(project_dir.resolve(strict=False))


def test_tool_planning_executor_empty_plan_retry_can_recover(tmp_path) -> None:
    task = Task(id="task", description="Do impossible thing")
    runtime = FakeRuntime(
        tmp_path,
        [
            {"decision_needs": []},
            {
                "decision_needs": [
                    {
                        "need_type": "command_check",
                        "question": "run smoke validation",
                        "command": "python -m compileall .",
                    }
                ]
            },
        ],
    )
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert len(runtime.llm_client.requests) == 2
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == ["command_executor"]
    entries = [
        json.loads(line)
        for line in (tmp_path / "tool_planning.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(entry["event_type"] == "problem_resolution_planned" for entry in entries)
    assert any(entry["event_type"] == "decision_need_empty_plan_retry_recovered" for entry in entries)


def test_tool_planning_executor_falls_back_for_unroutable_actionable_plan(tmp_path) -> None:
    package = tmp_path / "assistant"
    package.mkdir()
    target_file = package / "core.py"
    target_file.write_text(
        "class Assistant:\n"
        "    def respond(self, text):\n"
        "        return text\n",
        encoding="utf-8",
    )
    task = Task(id="task", description="Implement core assistant logic and command parsing loop")
    runtime = FakeRuntime(
        tmp_path,
        {
            "decision_needs": [
                {
                    "need_type": "assistant_logic",
                    "question": "decide how to implement the assistant core",
                }
            ]
        },
    )
    executor = ToolPlanningTaskExecutor(runtime)
    context = TaskExecutionContext(
        task=task,
        parent_context={"goal": f"build app in '{tmp_path}'"},
        shared_state={},
        execution_history=[],
    )

    result = executor.execute_task(task, context)

    assert result.status == TaskStatus.COMPLETED
    selections = runtime.tool_executor.selections
    assert [selection.tool_name for selection in selections] == [
        "code_generator",
        "file_writer",
        "command_executor",
    ]
    writer_input = selections[1].input_metadata.to_params()
    assert writer_input["file_path"] == str(target_file)
    assert writer_input["operation_kind"] == "file_replace"
    assert writer_input["content"] == "print('ok')"
    entries = [
        json.loads(line)
        for line in (tmp_path / "tool_planning.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(entry["event_type"] == "decision_need_fallback_plan" for entry in entries)


def test_tool_planning_executor_falls_back_after_invalid_json_repair_exhaustion(tmp_path) -> None:
    task = Task(id="task", description="Implement app.py and validate it")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    runtime.llm_client = InvalidJSONLLM()
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == [
        "code_generator",
        "file_writer",
        "command_executor",
    ]
    entries = [
        json.loads(line)
        for line in (tmp_path / "tool_planning.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    fallback_entries = [entry for entry in entries if entry["event_type"] == "decision_need_fallback_plan"]
    assert fallback_entries
    assert "invalid JSON" in fallback_entries[0]["payload"]["input_summary"]["reason"]


def test_tool_planning_executor_falls_back_after_transport_failure(tmp_path) -> None:
    task = Task(id="task", description="Implement app.py with README documentation")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    runtime.llm_client = TransportFailureLLM()
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == [
        "code_generator",
        "file_writer",
        "command_executor",
    ]


def test_tool_planning_executor_validation_fallback_does_not_regenerate_code(tmp_path) -> None:
    task = Task(id="task", description="Test: validate the generated app")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    runtime.llm_client = TransportFailureLLM()
    executor = ToolPlanningTaskExecutor(runtime)

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == ["command_executor"]
    command = runtime.tool_executor.selections[0].input_metadata.command
    assert command == f"python -m compileall {tmp_path}"


def test_validation_fallback_runs_exact_task_validation_command(tmp_path) -> None:
    task = Task(
        id="validate",
        description="Run pytest for calculator",
        kind="validate",
        validation_command="python -m pytest -q",
    )
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    runtime.llm_client = InvalidJSONLLM()

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert [selection.input_metadata.command for selection in runtime.tool_executor.selections] == [
        "python -m pytest -q"
    ]


def test_completion_evidence_accepts_bound_venv_rewrite_of_requested_command(tmp_path) -> None:
    task = Task(
        id="validate",
        description="Run pytest",
        kind="validate",
        validation_command="python -m pytest -q",
    )
    venv_python = str(tmp_path / ".venv" / "bin" / "python")

    error = ToolPlanningTaskExecutor._completion_evidence_error(
        task,
        [
            {
                "success": True,
                "tool": "command_executor",
                "input_metadata": {
                    "command": f"{venv_python} -m pytest -q",
                    "requested_command": "python -m pytest -q",
                    "effective_interpreter": venv_python,
                    "environment_id": "env:test",
                },
            }
        ],
        observed_modified_files=[],
    )

    assert error is None


def test_typed_validation_fallback_without_original_command_fails_closed(tmp_path) -> None:
    task = Task(id="validate", description="Run the requested validation", kind="validate")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    runtime.llm_client = InvalidJSONLLM()

    result = ToolPlanningTaskExecutor(runtime).execute_task(task, _context(task))

    assert result.status == TaskStatus.FAILED
    assert runtime.tool_executor.selections == []


def test_tool_planning_executor_validation_fallback_prefers_runtime_command(tmp_path) -> None:
    task = Task(id="task", description="Validate generated assistant")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    runtime._project_environments[str(tmp_path)]["run_command"] = "python app.py --smoke"
    executor = ToolPlanningTaskExecutor(runtime)
    context = TaskExecutionContext(
        task=task,
        parent_context={"goal": "build app", "project_path": str(tmp_path), "run_command": "python app.py --smoke"},
        shared_state={},
        execution_history=[],
    )

    result = executor.execute_task(task, context)

    assert result.status == TaskStatus.COMPLETED
    assert [selection.tool_name for selection in runtime.tool_executor.selections] == [
        "command_executor",
        "command_executor",
    ]
    assert runtime.tool_executor.selections[0].input_metadata.command == "python app.py --smoke"
    assert runtime.tool_executor.selections[1].input_metadata.command == f"python -m compileall {tmp_path}"


def test_tool_planning_executor_inherits_project_root_for_hallucinated_workspace_need(tmp_path) -> None:
    task = Task(id="task", description="Inspect the current project root")
    runtime = FakeRuntime(tmp_path, {})
    executor = ToolPlanningTaskExecutor(runtime)
    executor._active_context = TaskExecutionContext(
        task=task,
        parent_context={"goal": "analyze runtime", "cwd": str(tmp_path)},
        shared_state={},
        execution_history=[],
    )

    tool_requests = executor._parse_decision_needs(
        SimpleNamespace(
            parsed_json={
                "decision_needs": [
                    {
                        "need_type": "project_structure",
                        "question": "inspect current project structure",
                        "target_path": "/workspace/openpilot",
                    }
                ]
            },
            content="",
        )
    )

    assert tool_requests[0]["tool_name"] == "multi_file_reader"
    assert tool_requests[0]["input_metadata"]["directory_path"] == str(tmp_path)
    assert tool_requests[0]["input_metadata"]["project_path"] == str(tmp_path)
    assert tool_requests[0]["input_metadata"]["pattern"] == "sketch.json"


def test_tool_planning_executor_bad_json_returns_failed_result(tmp_path) -> None:
    task = Task(id="task", description="Do impossible thing")
    executor = ToolPlanningTaskExecutor(FakeRuntime(tmp_path, "{bad-json"))

    result = executor.execute_task(task, _context(task))

    assert result.status == TaskStatus.FAILED
    assert "Failed to parse LLM response as JSON" in result.error


def test_tool_planning_prompt_uses_history_and_forbids_invented_subtask_files(tmp_path) -> None:
    task = Task(id="task", description="Create project based on subtask 0 requirements")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)
    context = TaskExecutionContext(
        task=task,
        parent_context={"goal": "build app"},
        shared_state={},
        execution_history=[
            {
                "task_id": "previous",
                "description": "Clarify requirements",
                "status": "completed",
                "result_summary": "Use a small Python CLI app.",
            }
        ],
    )

    planning_surface = executor._planning_surface_for_prompt(task.description, "build app", context=context)
    prompt = executor._build_tool_plan_prompt(task.description, "build app", planning_surface, context)

    assert "Previous Task Results" in prompt
    assert "Use a small Python CLI app" in prompt
    assert "Need Catalog" in prompt
    assert "Core Capability Cards" in prompt
    assert "Deferred Capability Cards" in prompt
    assert "Never invent or read intermediate files such as subtask_0.md" in prompt
    assert "Do not emit null" in prompt
    assert "Available Tools" not in prompt
    assert "Input metadata:" not in prompt
    assert '"symbol_name": "optional' not in prompt


def test_tool_planning_history_is_a_bounded_latest_delta_view(tmp_path) -> None:
    task = Task(id="task", description="Continue implementation")
    executor = ToolPlanningTaskExecutor(FakeRuntime(tmp_path, {"decision_needs": []}))
    history = []
    for index in range(8):
        history.append(
            {
                "task_id": f"task-{index}",
                "description": f"Task {index} " + ("description " * 80),
                "status": "failed" if index in {5, 7} else "completed",
                "failure_type": "ToolExecutionFailed" if index in {5, 7} else None,
                "error": (f"error-marker-{index} " * 80) if index in {5, 7} else None,
                "result_summary": f"result-marker-{index} " * 80,
                "observed_paths": [
                    str(tmp_path / "src" / f"module_{index}.py"),
                    str(tmp_path / "shared.py"),
                ],
            }
        )
    context = TaskExecutionContext(
        task=task,
        parent_context={"goal": "repair project"},
        shared_state={},
        execution_history=history,
    )

    rendered = executor._execution_history_summary(context)
    view = json.loads(rendered)

    assert len(rendered) <= 900
    assert view["status_counts"] == {"completed": 6, "failed": 2}
    assert view["latest_change"]["task_id"] == "task-7"
    assert "error-marker-7" in view["latest_change"]["error_message"]
    assert view["latest_change"]["failure_type"] == "ToolExecutionFailed"
    assert "result-marker-6" not in rendered
    assert str(tmp_path / "shared.py") in view["evidence_paths"]
    assert view["evidence_paths"].count(str(tmp_path / "shared.py")) == 1
    assert len(view["recent_status"]) <= 5


def test_tool_planning_surface_for_read_only_task_uses_only_core_cards(tmp_path) -> None:
    task = Task(id="task", description="梳理 CLI 入口到主执行运行时的核心链路")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)

    planning_surface = executor._planning_surface_for_prompt(task.description, "analyze runtime flow", context=_context(task))

    assert "File evidence" in planning_surface
    assert "Project structure evidence" in planning_surface
    assert "Command validation" in planning_surface
    assert "External research" in planning_surface
    assert "New file generation" not in planning_surface
    assert "Existing code modification" not in planning_surface
    assert "Documentation delivery" not in planning_surface
    assert "Guarded deletion" not in planning_surface
    assert "Runtime or environment repair" not in planning_surface
    assert "llm_summarizer" not in planning_surface
    assert "task_classifier" not in planning_surface


def test_tool_plan_prompt_includes_read_only_notice_for_analysis_task(tmp_path) -> None:
    task = Task(id="task", description="梳理 CLI 入口到主执行运行时的核心链路", tags=["readonly", "understanding"])
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)
    context = _context(task)

    planning_surface = executor._planning_surface_for_prompt(task.description, "analyze runtime flow", context=context)
    prompt = executor._build_tool_plan_prompt(task.description, "analyze runtime flow", planning_surface, context)

    assert "Read-only task mode" in prompt
    assert "Do not emit file_write, file_delete, code generation, bug_fix, repair, or mutating command needs." in prompt
    assert "Prefer file_read, project_structure, and safe validation evidence." in prompt


def test_tool_planning_surface_adds_deferred_cards_for_create_and_docs_tasks(tmp_path) -> None:
    task = Task(id="task", description="Implement app.py with README documentation")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)

    planning_surface = executor._planning_surface_for_prompt(task.description, "build app", context=_context(task))

    assert "New file generation" in planning_surface
    assert "Documentation delivery" in planning_surface
    assert "Existing code modification" not in planning_surface


def test_tool_planning_surface_adds_repair_card_for_runtime_failure_tasks(tmp_path) -> None:
    task = Task(id="task", description="Repair runtime import failure and broken venv startup")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)

    planning_surface = executor._planning_surface_for_prompt(task.description, "restore startup", context=_context(task))

    assert "Runtime or environment repair" in planning_surface
    assert '"need_type":"bug_fix"' in planning_surface


def test_planning_surface_catalog_merges_tool_and_future_skill_providers() -> None:
    skill_card = PlanningSurfaceCard(
        card_id="skill_test_development",
        title="Test-development skill",
        source_kind=CapabilitySourceKind.SKILL_FUTURE,
        exposure=CapabilityExposure.DEFERRED,
        need_types=("project_structure", "command_check"),
        summary="Use a future skill procedure for test-development investigation.",
        required_fields_hint="task goal and project path",
        example_need={"need_type": "project_structure", "target_path": "/abs/path/project"},
        trigger_terms=("test development", "测试开发"),
        backing_refs=("skill:test_development",),
    )

    catalog = PlanningSurfaceCatalog.from_providers(
        [
            ToolCapabilityCardProvider([FILE_READER_DEFINITION, MULTI_FILE_READER_DEFINITION]),
            StaticCapabilityCardProvider([skill_card]),
        ]
    )
    selection = PlanningSurfaceSelector().select(
        catalog,
        task_description="改进测试开发流程，先做 test development investigation",
        goal="improve agent testing",
    )

    assert catalog.get("file_evidence") is not None
    assert catalog.get("skill_test_development") is skill_card
    assert selection.deferred_cards == (skill_card,)
    assert "Test-development skill" in selection.render()


def test_tool_planning_surface_accepts_runtime_future_skill_provider(tmp_path) -> None:
    skill_card = PlanningSurfaceCard(
        card_id="skill_bug_investigation",
        title="Bug investigation skill",
        source_kind="skill_future",
        exposure="deferred",
        need_types=("file_read", "project_structure", "command_check"),
        summary="Use a future skill procedure to find root causes before fixes.",
        required_fields_hint="problem statement and evidence scope",
        example_need={"need_type": "project_structure", "target_path": "/abs/path/project"},
        trigger_terms=("root cause", "本质问题"),
        backing_refs=("skill:bug_investigation",),
    )
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    runtime.planning_surface_providers = [StaticCapabilityCardProvider([skill_card])]
    task = Task(id="task", description="分析 bug 的 root cause，不要只修表象")
    executor = ToolPlanningTaskExecutor(runtime)

    planning_surface = executor._planning_surface_for_prompt(task.description, "find 本质问题", context=_context(task))

    assert "Bug investigation skill" in planning_surface
    assert "skill:bug_investigation" not in planning_surface
    assert "Input metadata:" not in planning_surface


def test_tool_planning_retry_prompt_uses_incremental_capability_surface(tmp_path) -> None:
    task = Task(id="task", description="Modify the existing CLI entrypoint to fix command parsing")
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)
    executor._active_task_description = task.description
    executor._active_goal = "fix CLI"
    executor._active_context = _context(task)
    signal = executor._problem_signal_for_empty_plan({"decision_needs": []})

    prompt = executor._empty_plan_retry_prompt(
        {"decision_needs": []},
        signal,
        executor._assess_problem_difficulty(signal, executor._judge_problem(signal)),
        ResolutionPlanMetadata(strategy="direct_retry", max_attempts=2, acceptance_check="recover"),
    )

    assert "Planning Surface" in prompt
    assert "Existing code modification" in prompt
    assert "Available Tools" not in prompt
    assert "Input metadata:" not in prompt


def test_tool_planning_prompt_budget_is_significantly_smaller_than_legacy_full_tool_prompt(tmp_path) -> None:
    runtime = IntelligentAutopilot(FakeLLM({"decision_needs": []}), log_file=tmp_path / "autopilot.jsonl")
    executor = runtime.tool_planning_task_executor
    task = Task(id="task", description="请梳理从 CLI 入口到主执行运行时的核心链路，并指出关键模块之间的关系。")
    context = TaskExecutionContext(task=task, parent_context={"goal": "analyze runtime"}, shared_state={}, execution_history=[])
    tools = runtime.tool_registry.list_all()
    tools_description = runtime.tool_io.format_tools_for_llm(tools)
    planning_surface = executor._planning_surface_for_prompt(task.description, "analyze runtime", context=context)

    legacy_prompt = f"""You are an AI assistant that selects and sequences tools to accomplish tasks.

Task: {task.description}
Overall Goal: analyze runtime
Previous Task Results:
No previous task results.

Available Tools:
{tools_description}

Generate a JSON plan with decision_needs. The runtime ToolRouter is the only component
allowed to map needs to concrete tools using budget, risk, and permission checks.

Output ONLY valid JSON in this format:
{{
  "decision_needs": [
    {{
      "need_type": "code_file_create",
      "question": "create the main project file",
      "target_path": "/absolute/path/to/file.py",
      "operation_kind": "create_file",
      "attributes": {{"language": "python"}}
    }}
  ]
}}

Allowed need_type values:
file_read, project_structure, web_search, command_check, file_write, file_delete, code_file_create,
directory_generate, code_unit_generate, code_symbol_modify, code_patch, code_generation,
code_execution, readme_generation.

Optional fields may include: target_path, operation_kind, target_scope, symbol_name,
symbol_type, insertion_hint, patch_mode, candidate_paths, query, command, risk_level,
attributes. Omit unknown or unavailable optional fields. Do not emit null.

Important:
- Previous task outputs are provided above in Previous Task Results. Use that shared history directly.
- Never invent or read intermediate files such as subtask_0.md, subtask_1.md, requirements.md, or plan.md unless they appear in previous tool outputs or the user explicitly requested them.
- If previous task results are absent or failed, infer sensible defaults from the original goal instead of reading a made-up plan file.
- For project creation, use directory_generate/code_file_create/file_write directly and create the needed files in the target directory.
- Always distinguish create_file, add_symbol, modify_symbol, and code_patch before selecting needs.
- For new code files or generated project files, emit code_file_create or directory_generate, then file_write with operation_kind create_file.
- For adding a function/class to an existing file, emit file_read, then code_unit_generate with operation_kind add_symbol, then file_write with operation_kind add_symbol so ToolRouter uses file_patch_writer.
- For modifying an existing function/class, emit file_read, then code_symbol_modify or code_patch with operation_kind modify_symbol, then file_write with operation_kind modify_symbol so ToolRouter uses file_patch_writer.
- For deleting an existing file, emit file_read or project_structure first for evidence, then file_delete with operation_kind delete_file so ToolRouter uses file_delete_tool.
- Do not plan code_generator + file_writer for edits to existing functions/classes.
- code_generator only supports executable code languages: python, shell, bash. Never use language "text"
- For design, outline, planning, or prose-only tasks, either return planning metadata through an appropriate text/documentation tool or write Markdown/text with file_writer/readme_tool
- For completed project/code deliveries, emit a readme_generation need after file_write to create README.md with run instructions
- Autopilot will run hard validation and autonomous-iteration improvement analysis after project delivery
- Provide actual values for all parameters, do not use null or placeholders
- If you need to pass output from one tool to another, generate the content directly in the first tool
- For command_executor, input_metadata.mode must be one of: dry_run, interactive, automatic
- For project commands, use mode "automatic" and do not use source/activate/cd/export; OpenPilot injects the target cwd and virtual environment from metadata
"""
    new_prompt = executor._build_tool_plan_prompt(task.description, "analyze runtime", planning_surface, context)

    assert len(new_prompt) <= len(legacy_prompt) * 0.55
    assert "Input metadata:" not in new_prompt
    assert "Available Tools" not in new_prompt
    assert "Need Catalog" in new_prompt
    assert "Do not invent nested directories or filenames under that root without evidence." in new_prompt


def test_tool_planning_prompt_includes_current_project_context(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path, {"decision_needs": []})
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(id="task", description="Analyze the CLI runtime flow")
    context = TaskExecutionContext(
        task=task,
        parent_context={"goal": "analyze runtime", "cwd": str(tmp_path)},
        shared_state={},
        execution_history=[],
    )

    prompt = executor._build_tool_plan_prompt(
        task.description,
        "analyze runtime",
        executor._planning_surface_for_prompt(task.description, "analyze runtime", context=context),
        context,
    )

    assert f"Project root: {tmp_path}" in prompt


def test_intelligent_autopilot_normalizes_execution_context_and_propagates_parent_context(tmp_path, monkeypatch) -> None:
    autopilot = IntelligentAutopilot(FakeLLM({"decision_needs": []}), log_file=tmp_path / "autopilot.jsonl")
    monkeypatch.chdir(tmp_path)

    normalized = autopilot._normalize_execution_context({})

    assert normalized["cwd"] == str(tmp_path.resolve())
    autopilot.session_id = "session"
    autopilot._current_execution_context = {**normalized, "run_command": "pytest"}

    parent_context = autopilot._task_parent_context("analyze runtime")

    assert parent_context["goal"] == "analyze runtime"
    assert parent_context["cwd"] == str(tmp_path.resolve())
    assert parent_context["run_command"] == "pytest"


def test_intelligent_autopilot_execution_history_payload_carries_observed_paths(tmp_path) -> None:
    autopilot = IntelligentAutopilot(FakeLLM({"decision_needs": []}), log_file=tmp_path / "autopilot.jsonl")
    task = Task(id="task", description="Inspect CLI files")
    result = TaskExecutionResult(
        task_id=task.id,
        status=TaskStatus.COMPLETED,
        result_metadata=TaskResultMetadata(
            task_id=task.id,
            status=ResultStatus.SUCCESS,
            result=TextArtifactMetadata(
                content="completed",
                attributes={
                    "final_output": {
                        "files": [str(tmp_path / "src" / "ui" / "cli.py")],
                        "content": f"# Source: {tmp_path / 'src' / 'ui' / 'cli.py'}",
                    },
                    "tool_results": [
                        {
                            "tool_name": "multi_file_reader",
                            "input_metadata": {"directory_path": str(tmp_path), "project_path": str(tmp_path)},
                            "output": {
                                "files": [str(tmp_path / "pyproject.toml")],
                                "sketch_files": [str(tmp_path / "sketch.json")],
                            },
                        }
                    ],
                    "all_tools_succeeded": True,
                },
            ),
        ),
    )

    history = autopilot._execution_history_payload([task], [result])

    assert history[0]["observed_paths"]
    assert str(tmp_path / "src" / "ui" / "cli.py") in history[0]["observed_paths"]
    assert str(tmp_path / "pyproject.toml") in history[0]["observed_paths"]
    assert history[0]["result_summary"]["all_tools_succeeded"] is True


def test_intelligent_autopilot_execute_task_proxy_uses_tool_planning_agent(tmp_path) -> None:
    class FakeAgent:
        def execute_task(self, task, context):
            return TaskExecutionResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                result_metadata=TaskResultMetadata(
                    task_id=task.id,
                    status=ResultStatus.SUCCESS,
                    result=TextArtifactMetadata(content="proxied", attributes={"proxied": True}),
                ),
                duration=0.0,
            )

    class MinimalLLM:
        pass

    autopilot = IntelligentAutopilot(MinimalLLM(), log_file=tmp_path / "autopilot.jsonl")
    autopilot.tool_planning_task_executor = FakeAgent()
    task = Task(id="task", description="Proxy task")

    result = autopilot._execute_task(task, _context(task))

    assert result.status == TaskStatus.COMPLETED
    assert result.result_metadata.result.get("proxied") is True


def test_provider_result_completion_helper_builds_bounded_success_result() -> None:
    task = Task(id="task-helper-success", description="Read README")
    final_text = "done" * 200
    result = ToolPlanningTaskExecutor._build_provider_task_result(
        task,
        SimpleNamespace(success=True, final_response=SimpleNamespace(content=final_text)),
        0.25,
        [{"round": 1}],
        {"provider_tool_execution": True, "rounds_used": 2},
    )

    assert result.status == TaskStatus.COMPLETED
    assert result.result_summary == final_text[:500]
    assert result.result_metadata is not None
    assert result.result_metadata.result.content == final_text


def test_provider_result_completion_helper_handles_empty_final_response() -> None:
    task = Task(id="task-helper-empty", description="Read README")
    result = ToolPlanningTaskExecutor._build_provider_task_result(
        task,
        SimpleNamespace(success=True, final_response=None),
        0.1,
        [],
        {},
    )

    assert result.status == TaskStatus.COMPLETED
    assert result.result_summary == ""
    assert result.result_metadata is not None
    assert result.result_metadata.result.content == ""


def test_provider_result_completion_helper_preserves_failure_evidence() -> None:
    task = Task(id="task-helper-failure", description="Read README")
    result = ToolPlanningTaskExecutor._build_provider_task_result(
        task,
        SimpleNamespace(
            success=False,
            error_message="provider stopped before completion",
            rounds_used=1,
            final_response=SimpleNamespace(finish_reason="length"),
            evidence_coverage=SimpleNamespace(to_json_dict=lambda: {"required": 1, "covered": 0}),
        ),
        0.5,
        [{"round": 1}],
        {"provider_tool_execution": True},
    )

    assert result.status == TaskStatus.FAILED
    assert result.result_metadata is not None
    failure = result.result_metadata.failure
    assert failure is not None
    assert failure.details["runner_error"] == "provider stopped before completion"
    assert failure.details["provider_stop_reason"] == "length"
    assert failure.details["evidence_coverage"] == {"required": 1, "covered": 0}


def test_provider_result_completion_helper_accepts_mapping_failure_evidence() -> None:
    task = Task(id="task-helper-mapping", description="Read README")
    result = ToolPlanningTaskExecutor._build_provider_task_result(
        task,
        SimpleNamespace(success=False, error_message="mapping evidence failure", evidence_coverage={"required": 2, "covered": 1}),
        0.2,
        [],
        {},
    )

    assert result.status == TaskStatus.FAILED
    assert result.result_metadata is not None
    assert result.result_metadata.failure.details["evidence_coverage"] == {"required": 2, "covered": 1}
