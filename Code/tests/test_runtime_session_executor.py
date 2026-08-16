from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from autonomous_iteration.task_models import Task, TaskDecompositionResult, TaskExecutionResult, TaskStatus
from core.exceptions import LLMProviderError
from core.openpilot_log import OpenPilotLogger
from autonomous_iteration.runtime_controller import _RuntimeSessionExecutor
from metadata import (
    FailureMetadata,
    FileArtifactMetadata,
    ResultStatus,
    TaskResultMetadata,
    ToolExecutionEnvelopeMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
    SessionExecutionCursor,
    SessionSemanticSnapshot,
    SessionStage,
    SessionTaskResult,
    TaskGraphNodeMetadata,
)


class FakeSemantic:
    task_type = SimpleNamespace(value="coding")
    risk_level = SimpleNamespace(value="low")
    required_resources = []
    confidence = 0.9

    def model_dump(self):
        return {"task_type": "coding", "risk_level": "low"}


class FakeMemoryResult:
    memories = []


class FakeTaskDecomposer:
    def __init__(self) -> None:
        self.decompose_called = False
        self.assemble_called = False

    def decompose(self, task_description, context):
        self.decompose_called = True
        task = Task(id="t1", description="Write app.py")
        return TaskDecompositionResult(
            original_task=Task(id="root", description=task_description),
            subtasks=[task],
            task_graph_summary="summary",
            decomposition_rationale="because",
            estimated_total_effort=1.0,
        )

    def assemble_results(self, original_task, subtasks):
        self.assemble_called = True
        return {"summary": original_task.description, "tasks": len(subtasks)}


class FakeMemoryStore:
    def query(self, query, limit=5):
        return FakeMemoryResult()


class FakeSemanticAnalyzer:
    def analyze_goal(self, goal):
        return FakeSemantic()


class FallbackSemanticAnalyzer:
    def __init__(self) -> None:
        self.fallback_calls = []

    def analyze_goal(self, goal):
        raise LLMProviderError("temporary provider failure", retryable=True)

    def fallback_goal_analysis(self, goal, reason):
        self.fallback_calls.append((goal, reason))
        return FakeSemantic()


class FakeTracker:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start_tracking(self):
        self.started = True

    def stop_tracking(self):
        self.stopped = True

    @contextmanager
    def track_task(self, title, attributes):
        yield


class FakeEnhancedUI:
    def __init__(self) -> None:
        self.task_graph_state = {"tasks": [], "current_task_id": None}
        self.graph_updates: list[dict] = []
        self.current_updates: list[dict] = []
        self.activities: list[tuple[str, str]] = []

    def set_task_graph_state(self, **kwargs):
        self.task_graph_state.update(kwargs)
        self.graph_updates.append(kwargs)

    def set_current_task_state(self, **kwargs):
        self.current_updates.append(kwargs)

    def log_activity(self, level, message):
        self.activities.append((level, message))


class FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.console = Console(record=True, width=100)
        self.logger = OpenPilotLogger(tmp_path / "runtime_session.jsonl")
        self.session_id = "session"
        self.stats = {
            "start_time": None,
            "end_time": None,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "success": False,
        }
        self.required_successful_improvements = 2
        self.semantic_analyzer = FakeSemanticAnalyzer()
        self.memory_store = FakeMemoryStore()
        self.task_decomposer = FakeTaskDecomposer()
        self.enhanced_ui = FakeEnhancedUI()
        self.tracker = FakeTracker()
        self.fast_result = None
        self.improvement_result = None
        self.written_files = []
        self.project_path = None

    def _show_start_panel(self, goal):
        self.console.print(f"start {goal}")

    def _show_task_tree(self, decomposition):
        self.console.print("tree")

    def _show_completion_summary(self, decomposition, results):
        self.console.print("summary")

    def _try_simple_code_artifact_fast_path(self, goal, semantic):
        return self.fast_result

    def _dashboard_task_items(self, tasks, running_task_id=None):
        return [{"id": task.id, "status": task.status.value} for task in tasks]

    def _execute_tasks(
        self,
        tasks,
        goal,
        *,
        prior_results=None,
        start_index=0,
        progress_sink=None,
    ):
        results = list(prior_results or [])
        execution_order = [task.id for task in tasks]
        for index, task in enumerate(tasks[start_index:], start=start_index):
            task.mark_completed({"ok": True})
            results.append(TaskExecutionResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                result={"ok": True},
                duration=0.1,
            ))
            if progress_sink is not None:
                progress_sink(tasks, execution_order, results, index + 1)
        return results

    def _finalize_project_readme(self, goal, results):
        return ToolExecutionEnvelopeMetadata(
            tool_name="readme_tool",
            step_id="readme_tool",
            status=ResultStatus.SUCCESS,
            success=True,
            input_metadata=ToolInputMetadata(tool_name="readme_tool"),
            output_metadata=ToolResultMetadata(
                tool_name="readme_tool",
                status=ResultStatus.SUCCESS,
                result=FileArtifactMetadata(file_path="README.md"),
            ),
        )

    def _collect_written_files(self, results):
        return self.written_files

    def _infer_project_path_from_files(self, goal, written_files):
        return self.project_path

    def _run_iterative_improvement(self, **kwargs):
        return self.improvement_result

    def _format_iteration_failure(self, improvement_result):
        return improvement_result.get("failure_reason") or "iteration failed"

    def _stop_tracking_if_owned(self):
        self.tracker.stop_tracking()


class FailingToolLoopRuntime(FakeRuntime):
    def _execute_tasks(
        self,
        tasks,
        goal,
        *,
        prior_results=None,
        start_index=0,
        progress_sink=None,
    ):
        failure = FailureMetadata(
            error_type="ToolLoopExceeded",
            error_message="Tool event loop exceeded. Last unresolved tool error: multi_file_reader (call-1)",
            details={
                "tool_loop": {
                    "final_error": {
                        "details": {
                            "tool_name": "multi_file_reader",
                            "call_id": "call-1",
                        }
                    },
                    "events": [],
                }
            },
        )
        for task in tasks:
            task.mark_failed(failure.error_message)
            task.result = TaskResultMetadata(
                task_id=task.id,
                status=ResultStatus.FAIL,
                failure=failure,
            )
        results = list(prior_results or []) + [
            TaskExecutionResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=failure.error_message,
                result_metadata=task.result,
                duration=0.1,
            )
            for task in tasks[start_index:]
        ]
        if progress_sink is not None:
            progress_sink(tasks, [task.id for task in tasks], results, len(tasks))
        return results


def test_runtime_session_standard_returns_result(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.stats["start_time"] = runtime.stats["end_time"] = __import__("datetime").datetime.now()
    executor = _RuntimeSessionExecutor(runtime)

    result = executor.run("Build app", {}, mode="standard")

    assert result["success"] is True
    assert result["goal"] == "Build app"
    assert result["final_result"] == {"summary": "Build app", "tasks": 1}
    assert result["completed_improvements"] == 0
    assert runtime.task_decomposer.decompose_called is True


def test_runtime_session_task_graph_preserves_support_context_files() -> None:
    task = Task(
        id="task-support-context",
        description="Modify calculator",
        kind="implement",
        read_files=["calculator.py"],
        support_context_files=["tests/test_calculator.py"],
        write_files=["calculator.py"],
        validation_command="python -m pytest -q tests/test_calculator.py",
    )

    node = _RuntimeSessionExecutor._task_node(task)
    restored = _RuntimeSessionExecutor._task_from_node(node)

    assert node.support_context_files == ["tests/test_calculator.py"]
    assert restored.support_context_files == ["tests/test_calculator.py"]
    assert restored.read_files == ["calculator.py"]
    assert restored.write_files == ["calculator.py"]


def test_runtime_session_enhanced_returns_result(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("autonomous_iteration.runtime_controller.time.sleep", lambda seconds: None)
    runtime = FakeRuntime(tmp_path)
    runtime.stats["start_time"] = runtime.stats["end_time"] = __import__("datetime").datetime.now()
    executor = _RuntimeSessionExecutor(runtime)

    result = executor.run("Build app", {}, mode="enhanced_ui")

    assert result["success"] is True
    assert "final_result" not in result
    assert runtime.tracker.started is True
    assert runtime.tracker.stopped is True
    assert runtime.enhanced_ui.current_updates[-1]["title"] == "Success"


def test_runtime_session_enhanced_emits_durable_decomposition_and_task_cursors(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("autonomous_iteration.runtime_controller.time.sleep", lambda seconds: None)
    runtime = FakeRuntime(tmp_path)
    runtime.stats["start_time"] = runtime.stats["end_time"] = __import__("datetime").datetime.now()
    cursor_updates: list[SessionExecutionCursor] = []
    executor = _RuntimeSessionExecutor(runtime, session_cursor_sink=cursor_updates.append)

    result = executor.run("Build app", {}, mode="enhanced_ui")

    assert result["success"] is True
    assert cursor_updates[0].mode == "enhanced_ui"
    assert cursor_updates[0].stage == SessionStage.DECOMPOSITION_RECORDED
    assert cursor_updates[0].next_task_index == 0
    assert cursor_updates[-1].mode == "enhanced_ui"
    assert cursor_updates[-1].stage == SessionStage.TASKS_EXECUTED
    assert cursor_updates[-1].next_task_index == 1


def test_runtime_session_enhanced_resumes_only_unfinished_task_suffix(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.stats["start_time"] = runtime.stats["end_time"] = __import__("datetime").datetime.now()
    executed_task_ids: list[str] = []

    def execute_tasks(tasks, goal, *, prior_results=None, start_index=0, progress_sink=None):
        results = list(prior_results or [])
        order = [task.id for task in tasks]
        for index, task in enumerate(tasks[start_index:], start=start_index):
            executed_task_ids.append(task.id)
            task.mark_completed({"ok": True})
            results.append(TaskExecutionResult(task_id=task.id, status=TaskStatus.COMPLETED))
            if progress_sink is not None:
                progress_sink(tasks, order, results, index + 1)
        return results

    runtime._execute_tasks = execute_tasks
    cursor_updates: list[SessionExecutionCursor] = []
    executor = _RuntimeSessionExecutor(runtime, session_cursor_sink=cursor_updates.append)
    original = TaskGraphNodeMetadata(task_id="root", description="Build app")
    tasks = [
        TaskGraphNodeMetadata(task_id="inspect", description="Inspect app"),
        TaskGraphNodeMetadata(task_id="fix", description="Fix app", dependencies=["inspect"]),
    ]
    order = ["inspect", "fix"]
    cursor = SessionExecutionCursor(
        mode="enhanced_ui",
        stage=SessionStage.TASK_EXECUTION,
        plan_hash=executor._session_plan_hash(original, tasks, order),
        semantic=SessionSemanticSnapshot(task_type="coding", risk_level="low", confidence=0.9),
        original_task=original,
        tasks=tasks,
        execution_order=order,
        next_task_index=1,
        results=[SessionTaskResult(task_id="inspect", status="completed")],
    )

    result = executor.run("Build app", {}, mode="enhanced_ui", resume_cursor=cursor)

    assert result["success"] is True
    assert executed_task_ids == ["fix"]
    assert cursor_updates[-1].mode == "enhanced_ui"
    assert cursor_updates[-1].next_task_index == 2
    assert runtime.task_decomposer.decompose_called is False


def test_runtime_session_fast_path_skips_decomposition(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.fast_result = {"success": True, "fast": True}
    executor = _RuntimeSessionExecutor(runtime)

    result = executor.run("print hello", {}, mode="standard")

    assert result == {"success": True, "fast": True}
    assert runtime.task_decomposer.decompose_called is False


def test_runtime_session_falls_back_when_semantic_llm_is_temporarily_unavailable(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.stats["start_time"] = runtime.stats["end_time"] = __import__("datetime").datetime.now()
    runtime.semantic_analyzer = FallbackSemanticAnalyzer()
    executor = _RuntimeSessionExecutor(runtime)

    result = executor.run("Build app", {}, mode="standard")

    assert result["success"] is True
    assert runtime.semantic_analyzer.fallback_calls == [("Build app", "LLMProviderError")]
    assert any("Semantic analysis fallback" in message for _level, message in runtime.enhanced_ui.activities)


def test_runtime_session_surfaces_autonomous_iteration_failure(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.stats["start_time"] = runtime.stats["end_time"] = __import__("datetime").datetime.now()
    runtime.written_files = [str(tmp_path / "app.py")]
    runtime.project_path = tmp_path
    runtime.improvement_result = {
        "success": False,
        "failure_stage": "Task Executor",
        "failed_iteration": 1,
        "failed_tool": "code_generator",
        "failure_reason": "generation failed",
        "retry_attempted": True,
        "retry_history": [{"attempt": "full"}],
        "remaining_goals": ["Fix app"],
    }
    executor = _RuntimeSessionExecutor(runtime)

    result = executor.run("Build app", {}, mode="standard")

    assert result["success"] is False
    assert result["iteration_error"] == "generation failed"
    assert result["failure_stage"] == "Task Executor"
    assert result["failed_tool"] == "code_generator"
    assert result["retry_history"] == [{"attempt": "full"}]


def test_runtime_session_failed_tool_loop_does_not_assemble_or_report_llm_transport(tmp_path) -> None:
    runtime = FailingToolLoopRuntime(tmp_path)
    runtime.stats["start_time"] = runtime.stats["end_time"] = __import__("datetime").datetime.now()
    executor = _RuntimeSessionExecutor(runtime)

    result = executor.run("Build app", {}, mode="enhanced_ui")

    assert result["success"] is False
    assert runtime.task_decomposer.assemble_called is False
    assert result["failure_stage"] == "Task Executor"
    assert result["failed_tool"] == "multi_file_reader"
    assert result["failed_call_id"] == "call-1"
    assert result["failed_tool"] != "llm_client"


def test_runtime_session_appends_iteration_skip_note_when_no_written_files(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.stats["start_time"] = runtime.stats["end_time"] = __import__("datetime").datetime.now()
    executor = _RuntimeSessionExecutor(runtime)

    result = executor.run("Build app", {}, mode="enhanced_ui")

    assert result["success"] is True
    tasks = runtime.enhanced_ui.task_graph_state["tasks"]
    assert any(task["id"] == "project_improvement_skipped" for task in tasks)
    assert any("no written files detected" in task.get("description", "") for task in tasks)
    assert any("Project improvement skipped" in message for _level, message in runtime.enhanced_ui.activities)


def test_runtime_session_calls_iteration_when_written_files_detected(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.stats["start_time"] = runtime.stats["end_time"] = __import__("datetime").datetime.now()
    runtime.written_files = [str(tmp_path / "app.py")]
    runtime.project_path = tmp_path
    runtime.improvement_result = {
        "success": True,
        "validation": {"ok": True},
        "completed_improvements": 1,
        "required_improvements": 1,
        "completed_iterations": 1,
        "required_iterations": 1,
    }
    executor = _RuntimeSessionExecutor(runtime)

    result = executor.run("Build app", {}, mode="enhanced_ui")

    assert result["success"] is True
    assert result["completed_improvements"] == 1
    assert not any(
        update.get("tasks", [{}])[-1].get("id") == "project_improvement_skipped"
        for update in runtime.enhanced_ui.graph_updates
        if update.get("tasks")
    )


def test_runtime_session_resumes_remaining_subtasks_from_durable_cursor(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.stats["start_time"] = runtime.stats["end_time"] = __import__("datetime").datetime.now()
    executed_task_ids: list[str] = []

    def execute_tasks(tasks, goal, *, prior_results=None, start_index=0, progress_sink=None):
        results = list(prior_results or [])
        order = [task.id for task in tasks]
        for index, task in enumerate(tasks[start_index:], start=start_index):
            executed_task_ids.append(task.id)
            task.mark_completed({"ok": True})
            result = TaskExecutionResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                result={"ok": True},
                duration=0.1,
            )
            results.append(result)
            if progress_sink is not None:
                progress_sink(tasks, order, results, index + 1)
        return results

    runtime._execute_tasks = execute_tasks
    cursor_updates: list[SessionExecutionCursor] = []
    executor = _RuntimeSessionExecutor(runtime, session_cursor_sink=cursor_updates.append)
    original_task = TaskGraphNodeMetadata(task_id="root", description="Build app")
    task_nodes = [
        TaskGraphNodeMetadata(task_id="task-1", description="Inspect app"),
        TaskGraphNodeMetadata(task_id="task-2", description="Fix app", dependencies=["task-1"]),
    ]
    execution_order = ["task-1", "task-2"]
    cursor = SessionExecutionCursor(
        stage=SessionStage.TASK_EXECUTION,
        plan_hash=executor._session_plan_hash(original_task, task_nodes, execution_order),
        semantic=SessionSemanticSnapshot(task_type="coding", risk_level="low", confidence=0.9),
        original_task=original_task,
        tasks=task_nodes,
        execution_order=execution_order,
        next_task_index=1,
        results=[SessionTaskResult(task_id="task-1", status="completed")],
    )

    result = executor.run("Build app", {}, mode="standard", resume_cursor=cursor)

    assert result["success"] is True
    assert executed_task_ids == ["task-2"]
    assert cursor_updates[-1].next_task_index == 2
    assert [item.task_id for item in cursor_updates[-1].results] == ["task-1", "task-2"]
    assert runtime.task_decomposer.decompose_called is False
