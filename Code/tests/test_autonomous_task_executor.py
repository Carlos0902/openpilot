from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from autonomous_iteration.models import EvaluationResult, IterationResult
from autonomous_iteration.improvement_context import ImprovementContextHelper
from autonomous_iteration.task_models import Task, TaskExecutionContext, TaskExecutionResult, TaskStatus
from autonomous_iteration.task_executor import AutonomousTaskExecutor
from core.openpilot_log import OpenPilotLogger
from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from metadata import (
    FailureMetadata,
    FileArtifactMetadata,
    ResultStatus,
    RuntimeBudgetMetadata,
    TaskResultMetadata,
    TextArtifactMetadata,
    ToolExecutionEnvelopeMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
    ValidationIssueMetadata,
    payload_to_artifact,
)


class FakeRuntime:
    def __init__(self, tmp_path: Path, *, code_results: list[dict] | None = None) -> None:
        self.tmp_path = tmp_path
        self.enhanced_ui = None
        self.session_id = "session"
        self.logger = OpenPilotLogger(tmp_path / "task_executor.jsonl")
        self.calls: list[dict] = []
        self.code_results = list(code_results or [])
        self.environment_success = True
        self.environment_failures_before_success = 0
        self.environment_error = "env failed"
        self.environment_errors: list[str] = []
        self.review_result = {"success": True, "result": {"approved": True}}
        self.review_results: list[dict] = []
        self.readme_result = {"success": True, "result": {"file_path": str(tmp_path / "README.md")}}
        self.write_result = {"success": True, "result": {"file_path": str(tmp_path / "app.py")}}
        self._project_improvement_actions: list[str] = []
        self.improvement_context = ImprovementContextHelper(
            environment_context_getter=lambda path: {"run_command": "python app.py"} if path else {},
            logger=self.logger,
            session_id_getter=lambda: self.session_id,
        )

    def _select_iteration_target_file(self, written_files, actions):
        return self.improvement_context.select_iteration_target_file(written_files, actions)

    def _dashboard_stage_id(self, stage_key):
        return None

    def _set_dashboard_task_status(self, task_id, status):
        return None

    def _short_dashboard_text(self, value, limit=140):
        text = " ".join(str(value or "").split())
        return text if len(text) <= limit else text[: limit - 3] + "..."

    def _build_prompt_context(self, **kwargs):
        return self.improvement_context.build_prompt_context(**kwargs)

    def _prompt_context_layer_summary(self, prompt_context):
        return self.improvement_context.prompt_context_layer_summary(prompt_context)

    def _readme_environment_context(self, environment_payload):
        return {"dependencies": ", ".join(environment_payload.get("detected_packages", []))}

    def _sync_project_environment(self, **kwargs):
        self.calls.append({"tool": "project_environment_tool", **kwargs})
        if self.environment_errors:
            return _tool_envelope(
                "project_environment_tool",
                {"success": False, "error": self.environment_errors.pop(0)},
                kwargs.get("input_metadata"),
            )
        if self.environment_failures_before_success > 0:
            self.environment_failures_before_success -= 1
            return _tool_envelope("project_environment_tool", {"success": False, "error": self.environment_error}, kwargs.get("input_metadata"))
        if not self.environment_success:
            return _tool_envelope("project_environment_tool", {"success": False, "error": self.environment_error}, kwargs.get("input_metadata"))
        return _tool_envelope(
            "project_environment_tool",
            {
                "success": True,
                "result": {
                "run_command": "python app.py",
                "setup_commands": ["python -m venv .venv"],
                "detected_packages": ["rich"],
            },
            },
            kwargs.get("input_metadata"),
        )

    def _execute_fast_tool(self, **kwargs):
        self.calls.append(kwargs)
        tool_name = kwargs["tool_name"]
        if tool_name == "code_generator":
            if self.code_results:
                return _tool_envelope(tool_name, self.code_results.pop(0), kwargs.get("input_metadata"))
            return _tool_envelope(tool_name, {"success": True, "result": {"code": "print('improved')\n"}, "status": "completed"}, kwargs.get("input_metadata"))
        if tool_name == "code_editor":
            return _tool_envelope(
                tool_name,
                {
                    "success": True,
                    "result": {
                        "file_path": str(self.tmp_path / "app.py"),
                        "operation_kind": "modify_symbol",
                        "symbol_name": "add",
                        "replacement_text": "def add(a, b):\n    return a + b\n",
                    },
                    "status": "completed",
                },
                kwargs.get("input_metadata"),
            )
        if tool_name == "file_writer":
            return _tool_envelope(tool_name, self.write_result, kwargs.get("input_metadata"))
        if tool_name == "code_reviewer":
            if self.review_results:
                return _tool_envelope(tool_name, self.review_results.pop(0), kwargs.get("input_metadata"))
            return _tool_envelope(tool_name, self.review_result, kwargs.get("input_metadata"))
        if tool_name == "readme_tool":
            return _tool_envelope(tool_name, self.readme_result, kwargs.get("input_metadata"))
        raise AssertionError(f"unexpected tool {tool_name}")


def _tool_envelope(tool_name: str, data: dict, input_metadata: ToolInputMetadata | None = None) -> ToolExecutionEnvelopeMetadata:
    success = bool(data.get("success"))
    result_payload = data.get("result")
    output_metadata = (
        ToolResultMetadata(tool_name=tool_name, status=ResultStatus.SUCCESS, result=payload_to_artifact(tool_name, result_payload, input_metadata))
        if success
        else None
    )
    failure = None if success else FailureMetadata(error_type=str(data.get("error_type") or "ToolError"), error_message=str(data.get("error") or f"{tool_name} failed"))
    return ToolExecutionEnvelopeMetadata(
        tool_name=tool_name,
        step_id=str(data.get("step_id") or tool_name),
        status=ResultStatus.SUCCESS if success else (ResultStatus.TIMEOUT if str(data.get("status")) == "timeout" else ResultStatus.FAIL),
        success=success,
        input_metadata=input_metadata or ToolInputMetadata(tool_name=tool_name),
        output_metadata=output_metadata,
        failure=failure,
        timeout_override=data.get("timeout_override"),
    )


def test_runtime_applies_project_command_context_to_command_metadata(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runtime = object.__new__(IntelligentAutopilot)
    runtime._project_environments = {
        str(project.resolve()): {
            "project_path": str(project),
            "operation": "preflight",
            "readiness": "ready",
            "environment_id": "env:test-project",
            "command_cwd": str(project),
            "command_env": {"VIRTUAL_ENV": str(project / ".venv"), "PATH": f"{project / '.venv' / 'bin'}:/usr/bin"},
            "python_command": str(project / ".venv" / "bin" / "python"),
            "pip_command": str(project / ".venv" / "bin" / "pip"),
        }
    }

    metadata = ToolInputMetadata.from_mapping(
        "command_executor",
        {
            "command": "python main.py",
            "cwd": str(project),
            "env": {"PYGAME_HIDE_SUPPORT_PROMPT": "1", "PATH": "/usr/bin"},
        },
    )

    updated = runtime._apply_project_command_context("command_executor", metadata)

    assert updated.cwd == str(project)
    assert updated.command == shlex.join([str(project / ".venv" / "bin" / "python"), "main.py"])
    assert updated.requested_command == "python main.py"
    assert updated.effective_interpreter == str(project / ".venv" / "bin" / "python")
    assert updated.environment_id == "env:test-project"
    assert updated.env["VIRTUAL_ENV"] == str(project / ".venv")
    assert updated.env["PATH"].startswith(str(project / ".venv" / "bin"))
    assert updated.env["PYGAME_HIDE_SUPPORT_PROMPT"] == "1"


def test_runtime_does_not_bind_unknown_environment_snapshot(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runtime = object.__new__(IntelligentAutopilot)
    runtime._project_environments = {
        str(project.resolve()): {
            "project_path": str(project),
            "readiness": "unknown",
            "python_command": str(project / ".venv" / "bin" / "python"),
            "command_cwd": str(project),
        }
    }
    metadata = ToolInputMetadata.from_mapping(
        "command_executor",
        {"command": "python -m pytest -q", "cwd": str(project)},
    )

    updated = runtime._apply_project_command_context("command_executor", metadata)

    assert updated.command == "python -m pytest -q"
    assert updated.environment_id is None


def test_validation_environment_setup_denial_blocks_before_sync(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "test_app.py").write_text("import pytest\n", encoding="utf-8")
    runtime = object.__new__(IntelligentAutopilot)
    runtime.auto_approve = False
    runtime._project_environments = {}
    runtime._confirm_environment_setup = lambda *_args, **_kwargs: False
    runtime._sync_project_environment = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("setup must not run after denial")
    )
    task = Task(
        id="validate",
        description="Run tests",
        kind="validate",
        validation_command="python -m pytest -q",
    )
    context = TaskExecutionContext(
        task=task,
        parent_context={"goal": "Validate project", "project_path": str(project)},
    )

    error = runtime._ensure_environment_for_task(task, context)

    assert "approval" in (error or "").lower()
    assert runtime._project_environments == {}


def test_validation_attaches_existing_ready_environment_without_setup(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    python = project / ".venv" / "bin" / "python"
    pip = project / ".venv" / "bin" / "pip"
    python.parent.mkdir(parents=True)
    python.write_text("not executed", encoding="utf-8")
    pip.write_text("not executed", encoding="utf-8")
    runtime = object.__new__(IntelligentAutopilot)
    runtime.auto_approve = False
    runtime._project_environments = {}
    runtime._sync_project_environment = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("ready environment must attach without setup")
    )
    task = Task(
        id="validate",
        description="Compile project",
        kind="validate",
        validation_command="python -m compileall -q app.py",
    )
    context = TaskExecutionContext(
        task=task,
        parent_context={"goal": "Validate project", "project_path": str(project)},
    )

    error = runtime._ensure_environment_for_task(task, context)

    assert error is None
    environment = runtime._project_environments[str(project.resolve())]
    assert environment["readiness"] == "ready"
    assert environment["python_executable"] == str(python)


def test_session_environment_preparation_selects_validation_before_execution() -> None:
    runtime = object.__new__(IntelligentAutopilot)
    observed: list[str] = []
    runtime._task_parent_context = lambda goal: {"goal": goal, "project_path": "/tmp/project"}
    runtime._ensure_environment_for_task = lambda task, _context: observed.append(task.id) or None
    tasks = [
        Task(id="implement", description="Edit app", kind="implement", write_files=["app.py"]),
        Task(
            id="validate",
            description="Run tests",
            kind="validate",
            validation_command="python -m pytest -q",
        ),
    ]

    runtime._prepare_session_environment(tasks, "Improve app")

    assert observed == ["validate"]


def test_direct_environment_sync_denial_has_zero_project_side_effects(tmp_path) -> None:
    project = tmp_path / "not-created"
    runtime = object.__new__(IntelligentAutopilot)
    runtime.auto_approve = False
    runtime.enhanced_ui = None
    runtime._confirm_environment_setup = lambda *_args, **_kwargs: False
    task = Task(id="environment", description="Prepare environment")

    result = runtime._sync_project_environment(
        task=task,
        step_id="environment_setup",
        project_path=project,
        written_files=[],
        entry_files=[],
        run_command="python -m pytest -q",
    )

    assert result.success is False
    assert result.failure.error_type == "EnvironmentSetupApprovalRequired"
    assert not project.exists()


def test_collect_written_files_reads_typed_tool_result_metadata(tmp_path) -> None:
    runtime = object.__new__(IntelligentAutopilot)
    output = FileArtifactMetadata(file_path=str(tmp_path / "app.py"))
    result = TaskExecutionResult(
        task_id="task",
        status=TaskStatus.COMPLETED,
        result_metadata=TaskResultMetadata(
            task_id="task",
            status=ResultStatus.SUCCESS,
            result=TextArtifactMetadata(
                content="completed",
                attributes={
                    "tool_results": [
                        {
                            "tool": "file_writer",
                            "success": True,
                            "result": output,
                            "input_metadata": {"file_path": str(tmp_path / "fallback.py")},
                        }
                    ]
                },
            ),
        ),
    )

    assert runtime._collect_written_files([result]) == [str(tmp_path / "app.py")]


def test_collect_written_files_falls_back_to_input_metadata_file_path(tmp_path) -> None:
    runtime = object.__new__(IntelligentAutopilot)
    result = TaskExecutionResult(
        task_id="task",
        status=TaskStatus.COMPLETED,
        result_metadata=TaskResultMetadata(
            task_id="task",
            status=ResultStatus.SUCCESS,
            result=TextArtifactMetadata(
                content="completed",
                attributes={
                    "tool_results": [
                        {
                            "tool": "file_writer",
                            "success": True,
                            "result": {},
                            "input_metadata": {"file_path": str(tmp_path / "fallback.py")},
                        }
                    ]
                },
            ),
        ),
    )

    assert runtime._collect_written_files([result]) == [str(tmp_path / "fallback.py")]


class FakeLLM:
    pass


def _evaluation() -> EvaluationResult:
    return EvaluationResult(
        validation_passed=True,
        runnable=True,
        has_blocking_bugs=False,
        summary="ok",
    )


def test_autonomous_task_executor_success_path_and_logs(tmp_path) -> None:
    app = tmp_path / "app.py"
    app.write_text("print('old')\n", encoding="utf-8")
    runtime = FakeRuntime(tmp_path)
    executor = AutonomousTaskExecutor(runtime)

    result = executor.execute_improvement(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(app)],
        run_command="",
        readme_path=tmp_path / "README.md",
        iteration=1,
        evaluation=_evaluation(),
        actions=["Improve app.py"],
        improvement_report={"summary": "report"},
        is_repair=False,
    )

    assert result.success is True
    assert result.failed_tool is None
    assert [call["tool_name"] if "tool_name" in call else call["tool"] for call in runtime.calls] == [
        "code_generator",
        "file_writer",
        "project_environment_tool",
        "code_reviewer",
    ]
    events = [json.loads(line) for line in (tmp_path / "task_executor.jsonl").read_text(encoding="utf-8").splitlines()]
    payloads = [event["payload"] for event in events]
    assert any(payload["source_type"] == "agent" and payload["source_name"] == "autonomous_iteration.task_executor" for payload in payloads)


def test_code_improvement_does_not_run_unrequested_readme_postprocessing(tmp_path) -> None:
    app = tmp_path / "app.py"
    app.write_text("print('old')\n", encoding="utf-8")
    runtime = FakeRuntime(tmp_path)
    runtime.readme_result = {"success": False, "error": "README is outside task scope"}

    result = AutonomousTaskExecutor(runtime).execute_improvement(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(app)],
        run_command="",
        readme_path=tmp_path / "README.md",
        iteration=1,
        evaluation=_evaluation(),
        actions=["Improve app.py"],
        improvement_report={
            "designed_tasks": [
                {
                    "description": "Improve only app.py",
                    "target_files": [str(app)],
                    "acceptance_criteria": ["app.py contains the improvement"],
                }
            ]
        },
        is_repair=False,
    )

    assert result.success is True
    assert "readme_tool" not in {
        call.get("tool_name") for call in runtime.calls if "tool_name" in call
    }


def test_improvement_code_generation_uses_authoritative_full_file_for_symbol_edit(tmp_path) -> None:
    target = tmp_path / "app.py"
    current_code = "".join(f"VALUE_{index} = {index}\n" for index in range(800))
    current_code += "\ndef add(a, b):\n    return a - b\n"
    target.write_text(current_code, encoding="utf-8")
    runtime = FakeRuntime(tmp_path)
    enhancement_budget = RuntimeBudgetMetadata()
    runtime._enhancement_runtime_budget = lambda: enhancement_budget
    executor = AutonomousTaskExecutor(runtime)

    result = executor.execute_code_generation_for_improvement(
        task=Task(
            id="improve-add",
            description="Fix the add function.",
            kind="repair",
            read_files=[str(target)],
            write_files=[str(target)],
        ),
        iteration=1,
        target_file=target,
        improvement_prompt="Fix the add function without rewriting unrelated code.",
        simplified=False,
        prompt_context={
            "project_context": {
                "target_file": str(target),
                "current_code_context": "VALUE_0 = 0\n[... omitted ...]\ndef add(a, b):\n",
            },
            "tool_task": "Modify the add function so it returns a + b.",
            "acceptance_criteria": ["add(2, 3) returns 5"],
        },
    )

    call = runtime.calls[-1]
    params = call["input_metadata"].to_params()
    assert result.success is True
    assert call["tool_name"] == "code_editor"
    assert params["operation_kind"] == "modify_symbol"
    assert params["symbol_name"] == "add"
    assert params["code"] == current_code
    assert params["_runtime_budget"] is enhancement_budget


def test_autonomous_task_executor_routes_readme_task_to_documentation_writer(tmp_path) -> None:
    app = tmp_path / "app.py"
    readme = tmp_path / "README.md"
    app.write_text("print('old')\n", encoding="utf-8")
    readme.write_text("# Game\n", encoding="utf-8")
    runtime = FakeRuntime(tmp_path)

    result = AutonomousTaskExecutor(runtime).execute_improvement(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(app), str(readme)],
        run_command="",
        readme_path=readme,
        iteration=1,
        evaluation=_evaluation(),
        actions=["在README.md中添加完整的控制键说明和游戏规则简介"],
        improvement_report={
            "summary": "report",
            "must_implement_next": ["README包含完整的控制键说明"],
            "designed_tasks": [
                {
                    "description": "在README.md中添加完整的控制键说明和游戏规则简介",
                    "target_files": [str(readme)],
                    "acceptance_criteria": ["README包含完整的控制键说明"],
                }
            ],
        },
        is_repair=False,
    )

    assert result.success is True
    tool_names = [call["tool_name"] if "tool_name" in call else call["tool"] for call in runtime.calls]
    assert tool_names == ["file_writer"]
    assert runtime.calls[0]["input_metadata"].file_path == str(readme)


def test_autonomous_task_executor_creates_planned_frontend_file(tmp_path) -> None:
    app = tmp_path / "assistant.py"
    app.write_text("print('assistant')\n", encoding="utf-8")
    runtime = FakeRuntime(tmp_path)

    result = AutonomousTaskExecutor(runtime).execute_improvement(
        goal="Build personal assistant",
        project_path=tmp_path,
        written_files=[str(app)],
        run_command="",
        readme_path=tmp_path / "README.md",
        iteration=1,
        evaluation=_evaluation(),
        actions=["Create the browser interface"],
        improvement_report={
            "summary": "Browser UI is missing.",
            "must_implement_next": ["Expose reminder controls in the browser UI."],
            "designed_tasks": [
                {
                    "description": "Create the planned browser user interface",
                    "target_files": ["index.html"],
                    "acceptance_criteria": ["Expose reminder controls in the browser UI."],
                }
            ],
            "stack_preset": {
                "delivery_surface": "browser",
                "frontend_language": "html_css_javascript",
                "backend_language": "python",
            },
            "ui_iteration_contract": {
                "assessment_required": True,
                "implementation_required_for_user_facing_change": True,
            },
        },
        is_repair=False,
    )

    assert result.success is True
    assert result.changed_files == [str(tmp_path / "index.html")]
    writer = runtime.calls[0]
    assert writer["tool_name"] == "file_writer"
    assert writer["input_metadata"].file_path == str(tmp_path / "index.html")
    assert "<!doctype html>" in writer["input_metadata"].content


def test_autonomous_task_executor_scopes_repair_to_designed_validation_target(tmp_path) -> None:
    assistant = tmp_path / "assistant.py"
    personal = tmp_path / "personal_assistant.py"
    assistant.write_text("{{code_generator_output}}\n", encoding="utf-8")
    personal.write_text("print('ready')\n", encoding="utf-8")
    runtime = FakeRuntime(tmp_path)
    evaluation = EvaluationResult(
        validation_passed=False,
        runnable=False,
        has_blocking_bugs=True,
        summary="Project validation failed with 2 blocking issue(s).",
        validation_issues=[
            ValidationIssueMetadata(
                category="code_quality",
                severity="blocking",
                message="Generated content in personal_assistant.py still contains template placeholders.",
                recommended_action="Replace placeholder content in personal_assistant.py with real implementation.",
                target_files=[str(personal)],
                issue_fingerprint="code_quality:personal",
                recommended_repair_kind="replace_generated_placeholder",
            ),
            ValidationIssueMetadata(
                category="code_quality",
                severity="blocking",
                message="Generated content in assistant.py still contains template placeholders.",
                recommended_action="Replace placeholder content in assistant.py with real implementation.",
                target_files=[str(assistant)],
                issue_fingerprint="code_quality:assistant",
                recommended_repair_kind="replace_generated_placeholder",
            ),
        ],
    )

    result = AutonomousTaskExecutor(runtime).execute_improvement(
        goal="Build personal assistant",
        project_path=tmp_path,
        written_files=[str(personal), str(assistant)],
        run_command="",
        readme_path=tmp_path / "README.md",
        iteration=1,
        evaluation=evaluation,
        actions=["Fix the validation failure in assistant.py"],
        improvement_report={
            "repair": True,
            "designed_tasks": [
                {
                    "description": "Fix the validation failure in assistant.py",
                    "target_files": [str(assistant)],
                    "acceptance_criteria": ["assistant.py has no generated placeholder"],
                }
            ],
            "must_implement_next": ["assistant.py has no generated placeholder"],
        },
        is_repair=True,
    )

    assert result.success is True
    code_call = next(call for call in runtime.calls if call.get("tool_name") == "code_generator")
    assert str(assistant) in code_call["input_metadata"].context


def test_autonomous_task_executor_does_not_retry_timeout_with_unknown_usage(tmp_path) -> None:
    app = tmp_path / "app.py"
    app.write_text("print('old')\n", encoding="utf-8")
    runtime = FakeRuntime(
        tmp_path,
        code_results=[
            {"success": False, "error": "timeout", "error_type": "timeout", "status": "timeout", "timeout_override": 1},
            {"success": False, "error": "timeout", "error_type": "timeout", "status": "timeout", "timeout_override": 1},
            {"success": True, "result": {"code": "print('surgical')\n"}, "status": "completed"},
        ],
    )

    result = AutonomousTaskExecutor(runtime).execute_improvement(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(app)],
        run_command="",
        readme_path=tmp_path / "README.md",
        iteration=2,
        evaluation=_evaluation(),
        actions=["Improve app.py"],
        improvement_report={},
        is_repair=False,
    )

    assert result.success is False
    assert [item["mode"] for item in result.retry_history] == ["full"]
    assert result.retry_attempted is False


def test_target_symbol_matching_uses_identifier_boundaries_and_goal_evidence(
    tmp_path,
) -> None:
    executor = AutonomousTaskExecutor(FakeRuntime(tmp_path))
    source = (
        "def add(left, right):\n    return left + right\n\n"
        "def divide(numerator, denominator):\n    return numerator / denominator\n"
    )

    symbol = executor._infer_target_symbol(
        source,
        {
            "agent_instruction": "Improve the project without adding unrelated code.",
            "acceptance_criteria": [
                "divide raises ValueError when denominator is zero"
            ],
        },
    )

    assert symbol == "divide"


def test_target_symbol_prefers_typed_iteration_goal_over_imperative_add_verb(
    tmp_path,
) -> None:
    executor = AutonomousTaskExecutor(FakeRuntime(tmp_path))
    source = (
        "def add(left, right):\n    return left + right\n\n"
        "def divide(numerator, denominator):\n    return numerator / denominator\n"
    )

    symbol = executor._infer_target_symbol(
        source,
        {
            "iteration_goal": "Document divide's denominator-zero contract.",
            "tool_task": "Add a concise docstring to the divide function.",
            "acceptance_criteria": ["divide documents ValueError"],
        },
    )

    assert symbol == "divide"


def test_retry_requires_observed_length_finish_reason(tmp_path) -> None:
    executor = AutonomousTaskExecutor(FakeRuntime(tmp_path))
    unknown_timeout = ToolExecutionEnvelopeMetadata(
        tool_name="code_editor",
        step_id="edit",
        status=ResultStatus.TIMEOUT,
        success=False,
        input_metadata=ToolInputMetadata(tool_name="code_editor"),
        failure=FailureMetadata(
            error_type="LLMTimeoutError",
            error_message="Request timed out.",
            details={},
        ),
    )
    observed_length = unknown_timeout.model_copy(
        update={
            "status": ResultStatus.FAIL,
            "failure": FailureMetadata(
                error_type="InvalidLLMResponseError",
                error_message="response truncated",
                details={
                    "provider_attempt": {
                        "finish_reason": "length",
                        "usage": {"total_tokens": 900},
                    }
                },
            ),
        }
    )

    assert executor.should_retry_code_generation_attempt(unknown_timeout) is False
    assert executor.should_retry_code_generation_attempt(observed_length) is True


def test_autonomous_task_executor_retries_product_intent_reviewer_rejection(tmp_path) -> None:
    app = tmp_path / "app.py"
    app.write_text("print('old')\n", encoding="utf-8")
    runtime = FakeRuntime(
        tmp_path,
        code_results=[
            {"success": True, "result": {"code": "import curses\n\ndef main(stdscr):\n    pass\n"}, "status": "completed"},
            {"success": True, "result": {"code": "import pygame\npygame.init()\n"}, "status": "completed"},
        ],
    )
    runtime.review_results = [
        {
            "success": True,
            "result": {
                "approved": False,
                "warnings": ["Product intent drift: standalone GUI intent was replaced by terminal-only interaction."],
                "suggestions": ["Regenerate while preserving product intent."],
                "rejection_categories": ["product_intent_drift"],
            },
        },
        {"success": True, "result": {"approved": True, "warnings": [], "suggestions": [], "rejection_categories": []}},
    ]

    result = AutonomousTaskExecutor(runtime).execute_improvement(
        goal="Build an interactive visual game",
        project_path=tmp_path,
        written_files=[str(app)],
        run_command="",
        readme_path=tmp_path / "README.md",
        iteration=1,
        evaluation=_evaluation(),
        actions=["Fix runtime issue without changing product intent."],
        improvement_report={},
        is_repair=True,
    )

    assert result.success is True
    assert result.retry_attempted is True
    assert any(item["mode"] == "product_intent_retry" for item in result.retry_history)
    code_retry_calls = [
        call for call in runtime.calls
        if call.get("tool_name") == "code_generator" and call["input_metadata"].prompt_context.get("reviewer_rejection")
    ]
    assert code_retry_calls


@pytest.mark.parametrize(
    ("setup", "failed_tool", "reason_part"),
    [
        (lambda runtime, app: app.unlink(), "task_file_resolver", "No related project file"),
        (lambda runtime, app: runtime.code_results.append({"success": True, "result": {"code": app.read_text(encoding="utf-8")}}), "code_generator", "did not change"),
        (lambda runtime, app: runtime.code_results.append({"success": True, "result": {"code": "def broken(:\n"}}), "code_generator", "syntax error"),
        (lambda runtime, app: setattr(runtime, "write_result", {"success": False, "error": "write failed"}), "file_writer", "write failed"),
        (lambda runtime, app: setattr(runtime, "review_result", {"success": True, "result": {"approved": False, "suggestions": ["not good"]}}), "code_reviewer", "not good"),
    ],
)
def test_autonomous_task_executor_failure_semantics(tmp_path, setup, failed_tool, reason_part) -> None:
    app = tmp_path / "app.py"
    app.write_text("print('old')\n", encoding="utf-8")
    runtime = FakeRuntime(tmp_path)
    setup(runtime, app)

    result = AutonomousTaskExecutor(runtime).execute_improvement(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(app)],
        run_command="",
        readme_path=tmp_path / "README.md",
        iteration=1,
        evaluation=_evaluation(),
        actions=["Improve app.py"],
        improvement_report={},
        is_repair=False,
    )

    assert result.success is False
    assert result.failure_stage == "Task Executor"
    assert result.failed_tool == failed_tool
    assert reason_part in (result.failure_reason or "")


def test_explicitly_targeted_readme_postprocessing_remains_required(tmp_path) -> None:
    app = tmp_path / "app.py"
    readme = tmp_path / "README.md"
    app.write_text("print('old')\n", encoding="utf-8")
    readme.write_text("# App\n", encoding="utf-8")
    runtime = FakeRuntime(tmp_path)
    runtime.readme_result = {"success": False, "error": "readme failed"}

    result = AutonomousTaskExecutor(runtime).execute_improvement(
        goal="Improve app and its documentation",
        project_path=tmp_path,
        written_files=[str(app), str(readme)],
        run_command="",
        readme_path=readme,
        iteration=1,
        evaluation=_evaluation(),
        actions=["Improve app.py and synchronize README.md"],
        improvement_report={
            "designed_tasks": [
                {
                    "description": "Improve app.py and synchronize README.md",
                    "target_files": [str(app), str(readme)],
                    "acceptance_criteria": ["README describes the improved app"],
                }
            ]
        },
        is_repair=False,
    )

    assert result.success is False
    assert result.failed_tool == "readme_tool"
    assert "readme failed" in (result.failure_reason or "")


def test_autonomous_task_executor_environment_failure_stage(tmp_path) -> None:
    app = tmp_path / "app.py"
    app.write_text("print('old')\n", encoding="utf-8")
    runtime = FakeRuntime(tmp_path)
    runtime.environment_success = False

    result = AutonomousTaskExecutor(runtime).execute_improvement(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(app)],
        run_command="",
        readme_path=tmp_path / "README.md",
        iteration=1,
        evaluation=_evaluation(),
        actions=["Improve app.py"],
        improvement_report={},
        is_repair=False,
    )

    assert result.success is False
    assert result.failure_stage == "Environment Setup"
    assert result.failed_tool == "project_environment_tool"
    assert "env failed" in (result.failure_reason or "")


def test_autonomous_task_executor_repairs_environment_before_failing(tmp_path) -> None:
    app = tmp_path / "app.py"
    app.write_text("print('old')\n", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("rich\n\"\"\"\n", encoding="utf-8")
    runtime = FakeRuntime(tmp_path)
    runtime.environment_failures_before_success = 1
    runtime.environment_error = (
        f"ERROR: Invalid requirement: '\"\"\"': Expected package name at the start of dependency specifier\n"
        "    \"\"\"\n"
        f"    ^ (from line 2 of {requirements})\n"
    )

    result = AutonomousTaskExecutor(runtime).execute_improvement(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(app)],
        run_command="",
        readme_path=tmp_path / "README.md",
        iteration=1,
        evaluation=_evaluation(),
        actions=["Improve app.py"],
        improvement_report={},
        is_repair=False,
    )

    assert result.success is True
    assert requirements.read_text(encoding="utf-8") == "rich\n"
    assert len([call for call in runtime.calls if call.get("tool") == "project_environment_tool"]) == 2


def test_autonomous_task_executor_iterates_environment_repairs_until_sync_succeeds(tmp_path) -> None:
    app = tmp_path / "app.py"
    app.write_text("print('old')\n", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    secondary_requirements = tmp_path / "requirements-dev.txt"
    requirements.write_text('rich\n"""\n', encoding="utf-8")
    secondary_requirements.write_text('pytest\n"""\n', encoding="utf-8")
    runtime = FakeRuntime(tmp_path)
    runtime.environment_errors = [
        (
            f"ERROR: Invalid requirement: '\"\"\"': Expected package name at the start of dependency specifier\n"
            f"    ^ (from line 2 of {requirements})\n"
        ),
        (
            f"ERROR: Invalid requirement: '\"\"\"': Expected package name at the start of dependency specifier\n"
            f"    ^ (from line 2 of {secondary_requirements})\n"
        ),
    ]

    result = AutonomousTaskExecutor(runtime).execute_improvement(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(app)],
        run_command="",
        readme_path=tmp_path / "README.md",
        iteration=1,
        evaluation=_evaluation(),
        actions=["Improve app.py"],
        improvement_report={},
        is_repair=False,
    )

    assert result.success is True
    assert requirements.read_text(encoding="utf-8") == "rich\n"
    assert secondary_requirements.read_text(encoding="utf-8") == "pytest\n"
    assert len([call for call in runtime.calls if call.get("tool") == "project_environment_tool"]) == 3


def test_autonomous_task_executor_resolver_failure_for_missing_target(tmp_path) -> None:
    target = tmp_path / "missing.py"
    runtime = FakeRuntime(tmp_path)

    result = AutonomousTaskExecutor(runtime).execute_improvement(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(target)],
        run_command="",
        readme_path=tmp_path / "README.md",
        iteration=1,
        evaluation=_evaluation(),
        actions=["Improve missing.py"],
        improvement_report={},
        is_repair=False,
    )

    assert result.success is False
    assert result.failed_tool == "task_file_resolver"
    assert "No related project file" in (result.failure_reason or "")


def test_autonomous_task_executor_creates_nested_planned_frontend_file(tmp_path) -> None:
    app = tmp_path / "assistant.py"
    app.write_text("print('assistant')\n", encoding="utf-8")
    runtime = FakeRuntime(tmp_path)
    target = tmp_path / "templates" / "index.html"
    runtime.write_result = {"success": True, "result": {"file_path": str(target)}}

    result = AutonomousTaskExecutor(runtime).execute_improvement(
        goal="Improve browser UI",
        project_path=tmp_path,
        written_files=[str(app)],
        run_command="python assistant.py",
        readme_path=tmp_path / "README.md",
        iteration=3,
        evaluation=_evaluation(),
        actions=["Enhance the frontend template (templates/index.html) with a polished chat interface."],
        improvement_report={
            "designed_tasks": [
                {
                    "description": "Enhance the frontend template (templates/index.html) with a polished chat interface.",
                    "acceptance_criteria": ["templates/index.html contains the browser chat UI."],
                    "target_files": [],
                }
            ],
            "must_implement_next": ["templates/index.html contains the browser chat UI."],
        },
        is_repair=False,
    )

    writer_call = next(call for call in runtime.calls if call.get("tool_name") == "file_writer")
    params = writer_call["input_metadata"].to_params()
    assert result.success is True
    assert result.changed_files == [str(target)]
    assert params["file_path"] == str(target)
    assert params["create_dirs"] is True


def test_autonomous_task_executor_routes_blocking_startup_repair_kinds_to_bug_fix(tmp_path) -> None:
    evaluation = EvaluationResult(
        validation_passed=False,
        runnable=False,
        has_blocking_bugs=True,
        summary="Project validation failed.",
        validation_errors=["Port conflict prevents startup."],
        validation_issues=[
            ValidationIssueMetadata(
                category="environment",
                severity="blocking",
                message="Direct startup failed because the selected port is occupied.",
                recommended_action="Select an available fallback port.",
                recommended_repair_kind="make_web_port_configurable",
                target_files=[str(tmp_path / "server.py")],
            )
        ],
    )

    assert AutonomousTaskExecutor(FakeRuntime(tmp_path))._should_use_bug_fix(evaluation) is True


def test_intelligent_autopilot_apply_project_improvement_proxy_uses_task_executor(tmp_path) -> None:
    class FakeExecutor:
        def execute_improvement(self, **kwargs):
            return IterationResult(
                iteration=kwargs["iteration"],
                validation_passed=True,
                completed_successful_iteration=False,
                applied_actions=kwargs["actions"],
                changed_files=[],
                success=True,
            )

    autopilot = IntelligentAutopilot(FakeLLM(), log_file=tmp_path / "autopilot.jsonl")
    autopilot.autonomous_task_executor = FakeExecutor()

    result = autopilot._apply_project_improvement(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(tmp_path / "app.py")],
        run_command="",
        readme_path=tmp_path / "README.md",
        iteration=3,
        evaluation=_evaluation(),
        actions=["Improve app.py"],
        improvement_report={},
    )

    assert result.success is True
    assert result.iteration == 3
