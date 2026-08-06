from __future__ import annotations

import json

from rich.console import Console

from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from autonomous_iteration.task_models import (
    Task,
    TaskDecompositionResult,
    TaskExecutionContext,
    TaskExecutionResult,
    TaskPriority,
    TaskStatus,
)
from core.openpilot_log import OpenPilotLogger
from metadata import FailureMetadata, ResultStatus, TaskResultMetadata


class FakeTaskDecomposer:
    def __init__(self, *, fail_order: bool = False, order: list[str] | None = None) -> None:
        self.fail_order = fail_order
        self.order = order

    def build_task_graph(self, tasks):
        return {"tasks": tasks}

    def get_execution_order(self, task_graph):
        if self.fail_order:
            raise ValueError("cycle")
        return self.order or [task.id for task in task_graph["tasks"]]

    def decompose(self, task_description, context=None, parent_task_id=None):
        original = Task(id=parent_task_id or "original", description=task_description)
        subtasks = [
            Task(id="inspect", description="Inspect failed planning context", parent_id=original.id, kind="inspect"),
            Task(
                id="repair",
                description="Repair by smaller tool plan",
                parent_id=original.id,
                kind="repair",
                dependencies=["inspect"],
                write_files=["app.py"],
            ),
            Task(
                id="validate",
                description="Validate repaired task",
                parent_id=original.id,
                kind="validate",
                dependencies=["repair"],
                validation_command="python -m compileall .",
            ),
        ]
        return TaskDecompositionResult(
            original_task=original,
            subtasks=subtasks,
            task_graph_summary="inspect -> repair -> validate",
            decomposition_rationale="Split hard planning gap into smaller tasks.",
            estimated_total_effort=3.0,
        )


class FakeEnhancedUI:
    def __init__(self) -> None:
        self.activities: list[tuple[str, str]] = []
        self.graph_updates: list[dict] = []
        self.current_updates: list[dict] = []

    def log_activity(self, level, message):
        self.activities.append((level, message))

    def set_task_graph_state(self, **kwargs):
        self.graph_updates.append(kwargs)

    def set_current_task_state(self, **kwargs):
        self.current_updates.append(kwargs)


class FakeRuntime:
    _execute_tasks = IntelligentAutopilot._execute_tasks
    _dashboard_task_items = IntelligentAutopilot._dashboard_task_items
    _execute_tasks_enhanced_ui = IntelligentAutopilot._execute_tasks_enhanced_ui
    _execute_tasks_standard = IntelligentAutopilot._execute_tasks_standard
    _log_task_execution_event = IntelligentAutopilot._log_task_execution_event
    _implicit_dependencies_for_task = IntelligentAutopilot._implicit_dependencies_for_task
    _blocking_dependency = IntelligentAutopilot._blocking_dependency
    _blocked_task_result = IntelligentAutopilot._blocked_task_result
    _execution_history_payload = IntelligentAutopilot._execution_history_payload
    _history_result_summary = IntelligentAutopilot._history_result_summary
    _history_observed_paths = IntelligentAutopilot._history_observed_paths
    _task_parent_context = IntelligentAutopilot._task_parent_context

    def __init__(self, tmp_path, *, enhanced: bool = False, fail_order: bool = False) -> None:
        self.console = Console(record=True, width=100)
        self.logger = OpenPilotLogger(tmp_path / "runtime_task_execution.jsonl")
        self.session_id = "session"
        self.use_enhanced_ui = enhanced
        self.enhanced_ui = FakeEnhancedUI()
        self.task_decomposer = FakeTaskDecomposer(fail_order=fail_order)
        self.stats = {"tasks_completed": 0, "tasks_failed": 0}
        self.executed: list[str] = []
        self.raise_for: set[str] = set()
        self.fail_for: set[str] = set()
        self.decompose_for: set[str] = set()

    def _execute_task(self, task: Task, context: TaskExecutionContext) -> TaskExecutionResult:
        self.executed.append(task.id)
        if task.id in self.raise_for:
            raise RuntimeError("boom")
        if task.id in self.decompose_for:
            failure = FailureMetadata(
                error_type="DecisionNeedResolutionError",
                error_message="Tool planning requires decomposition after empty decision_needs plan",
                details={
                    "failed_tool": "tool_planning_executor",
                    "failure_stage": "Tool Planning",
                    "problem_signal": {"category": "planning_gap", "task_id": task.id},
                    "difficulty_assessment": {"level": "hard", "needs_decomposition": True},
                    "resolution_plan": {"strategy": "decompose", "target_tasks": [task.id], "max_attempts": 2},
                },
            )
            return TaskExecutionResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=failure.error_message,
                result_metadata=TaskResultMetadata(task_id=task.id, status=ResultStatus.FAIL, failure=failure),
                duration=0.1,
            )
        if task.id in self.fail_for:
            failure = FailureMetadata(
                error_type="DecisionNeedValidationError",
                error_message="Decision need schema validation failed",
                details={"failed_tool": "tool_planning_executor", "failure_stage": "Tool Planning"},
            )
            return TaskExecutionResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=failure.error_message,
                result_metadata=TaskResultMetadata(task_id=task.id, status=ResultStatus.FAIL, failure=failure),
                duration=0.1,
            )
        return TaskExecutionResult(
            task_id=task.id,
            status=TaskStatus.COMPLETED,
            result={"ok": task.description},
            duration=0.1,
        )


def _tasks() -> list[Task]:
    return [
        Task(id="a", description="First task", priority=TaskPriority.HIGH, estimated_effort=1.0),
        Task(id="b", description="Second task", priority=TaskPriority.MEDIUM, estimated_effort=1.0),
    ]


def test_runtime_standard_task_execution_uses_graph_order(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.task_decomposer = FakeTaskDecomposer(order=["b", "a"])
    tasks = _tasks()

    results = IntelligentAutopilot._execute_tasks(runtime, tasks, "goal")

    assert runtime.executed == ["b", "a"]
    assert [result.status for result in results] == [TaskStatus.COMPLETED, TaskStatus.COMPLETED]
    assert all(task.status == TaskStatus.COMPLETED for task in tasks)
    assert runtime.stats["tasks_completed"] == 2


def test_runtime_task_execution_falls_back_to_sequential_order(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path, fail_order=True)
    tasks = _tasks()

    IntelligentAutopilot._execute_tasks(runtime, tasks, "goal")

    assert runtime.executed == ["a", "b"]
    assert "executing sequentially" in runtime.console.export_text()


def test_runtime_enhanced_task_execution_converts_task_exception_to_failed_result(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path, enhanced=True)
    runtime.raise_for = {"a"}
    tasks = _tasks()

    results = IntelligentAutopilot._execute_tasks(runtime, tasks, "goal")

    assert results[0].status == TaskStatus.FAILED
    assert "Task execution exception" in results[0].error
    assert tasks[0].status == TaskStatus.FAILED
    assert runtime.enhanced_ui.current_updates[-1]["status"] == "completed"


def test_runtime_enhanced_task_execution_preserves_failed_result_metadata(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path, enhanced=True)
    runtime.fail_for = {"a"}
    tasks = _tasks()

    results = IntelligentAutopilot._execute_tasks(runtime, tasks, "goal")

    assert results[0].status == TaskStatus.FAILED
    assert tasks[0].status == TaskStatus.FAILED
    assert tasks[0].result.failure.details["failed_tool"] == "tool_planning_executor"


def test_runtime_blocks_dependency_after_failed_previous_task(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path, enhanced=True)
    runtime.fail_for = {"a"}
    tasks = [
        Task(id="a", description="Clarify requirements", priority=TaskPriority.HIGH),
        Task(
            id="b",
            description="Create project based on subtask 0 requirements",
            priority=TaskPriority.MEDIUM,
        ),
    ]

    results = IntelligentAutopilot._execute_tasks(runtime, tasks, "goal")

    assert runtime.executed == ["a"]
    assert [result.status for result in results] == [TaskStatus.FAILED, TaskStatus.FAILED]
    assert tasks[1].status == TaskStatus.BLOCKED
    assert "Blocked because task a failed" in results[1].error


def test_runtime_decomposes_hard_planning_gap_before_final_failure(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path)
    runtime.decompose_for = {"a"}
    tasks = [Task(id="a", description="Implement complex frontend/backend architecture")]

    results = IntelligentAutopilot._execute_tasks(runtime, tasks, "goal")

    assert results[0].status == TaskStatus.COMPLETED
    assert tasks[0].status == TaskStatus.COMPLETED
    assert runtime.executed == ["a", "inspect", "repair", "validate"]
    assert results[0].attributes["problem_resolution"]["resolution_strategy"] == "decompose"
    events = [
        json.loads(line)
        for line in (tmp_path / "runtime_task_execution.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["event_type"] == "task_problem_decomposition_started" for event in events)
    assert any(
        event["event_type"] == "task_problem_decomposition_completed" and event["payload"]["success"]
        for event in events
    )


def test_runtime_dashboard_items_marks_running_task(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path)
    items = IntelligentAutopilot._dashboard_task_items(runtime, _tasks(), running_task_id="a")

    assert items[0]["status"] == "running"
    assert items[1]["status"] == "pending"


def test_task_graph_file_locks_serialize_same_file_writes() -> None:
    tasks = [
        Task(id="write-a", description="Write app", write_files=["app.py"]),
        Task(id="write-b", description="Rewrite app", write_files=["./app.py"]),
    ]

    batches = IntelligentAutopilot._execution_batches_with_file_locks(tasks, ["write-a", "write-b"])

    assert batches == [["write-a"], ["write-b"]]


def test_task_graph_file_locks_allow_parallel_reads() -> None:
    tasks = [
        Task(id="read-a", description="Inspect app", read_files=["app.py"]),
        Task(id="read-b", description="Inspect docs", read_files=["README.md"]),
    ]

    batches = IntelligentAutopilot._execution_batches_with_file_locks(tasks, ["read-a", "read-b"])

    assert batches == [["read-a", "read-b"]]


def test_task_graph_validation_waits_for_write_tasks() -> None:
    tasks = [
        Task(id="validate", description="Validate app", kind="validate", validation_command="pytest"),
        Task(id="write", description="Write app", write_files=["app.py"]),
    ]

    batches = IntelligentAutopilot._execution_batches_with_file_locks(tasks, ["validate", "write"])
    _nodes, edges = IntelligentAutopilot._task_graph_metadata(tasks)

    assert batches == [["write"], ["validate"]]
    assert any(edge.from_task == "write" and edge.to_task == "validate" and edge.edge_type == "validates" for edge in edges)
