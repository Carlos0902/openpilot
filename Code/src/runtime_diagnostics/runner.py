"""Task-pool runner for real-task-driven diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from core.openpilot_log import OpenPilotLogger
from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks
from runtime_diagnostics.raw_task import RawTaskInput
from runtime_diagnostics.recorder import DiagnosticRecorder
from runtime_diagnostics.task_pool import load_raw_tasks


ExecutorFn = Callable[[str, dict[str, Any]], dict[str, Any]]


class TaskPoolRunResult(BaseModel):
    task_id: str
    source: str
    success: bool
    started_at: str
    finished_at: str
    error: str | None = None
    result_summary: dict[str, Any] = Field(default_factory=dict)


class RuntimeTaskPoolRunner:
    """Run a list of RawTaskInput tasks through a provided executor."""

    def __init__(self, executor: ExecutorFn, *, recorder: DiagnosticRecorder | None = None):
        self.executor = executor
        self.recorder = recorder or DiagnosticRecorder()

    def run_path(self, path: str | Path) -> list[TaskPoolRunResult]:
        return self.run_tasks(load_raw_tasks(path))

    def run_tasks(self, tasks: list[RawTaskInput]) -> list[TaskPoolRunResult]:
        results: list[TaskPoolRunResult] = []
        for task in tasks:
            results.append(self.run_task(task))
        return results

    def run_task(self, task: RawTaskInput) -> TaskPoolRunResult:
        started_at = datetime.now(UTC).isoformat()
        context = {
            "task_id": task.task_id,
            "source": task.source,
            "attachments": task.attachments,
            "tags": task.tags,
            **task.context,
        }
        self.recorder.record_run(
            {
                "event": "task_pool_item_started",
                "task_id": task.task_id,
                "source": task.source,
                "tags": task.tags,
                "started_at": started_at,
            }
        )
        try:
            result = self.executor(task.raw_input, context)
            success = bool(result.get("success")) if isinstance(result, dict) else bool(result)
            result_summary = _result_summary(result)
            run_result = TaskPoolRunResult(
                task_id=task.task_id,
                source=task.source,
                success=success,
                started_at=started_at,
                finished_at=datetime.now(UTC).isoformat(),
                result_summary=result_summary,
                error=None if success else str(result.get("error") or result.get("failure_reason") or "task failed") if isinstance(result, dict) else None,
            )
        except Exception as exc:
            run_result = TaskPoolRunResult(
                task_id=task.task_id,
                source=task.source,
                success=False,
                started_at=started_at,
                finished_at=datetime.now(UTC).isoformat(),
                error=str(exc),
                result_summary={},
            )
        self.recorder.record_run(
            {
                "event": "task_pool_item_finished",
                "task_id": task.task_id,
                "source": task.source,
                "success": run_result.success,
                "error": run_result.error,
                "result_summary": run_result.result_summary,
                "started_at": run_result.started_at,
                "finished_at": run_result.finished_at,
            }
        )
        return run_result


def build_autopilot_executor(
    *,
    llm_client: Any,
    console: Any | None = None,
    logger: OpenPilotLogger | None = None,
    recorder: DiagnosticRecorder | None = None,
    use_enhanced_ui: bool = False,
    **autopilot_kwargs: Any,
) -> ExecutorFn:
    """Build an executor that runs tasks through IntelligentAutopilot.

    This intentionally reuses the normal runtime path. Diagnostics hooks are
    injected explicitly and do not rely on environment-variable activation.
    """
    recorder = recorder or DiagnosticRecorder()
    hooks = RuntimeDiagnosticsHooks(recorder)

    def _execute(goal: str, context: dict[str, Any]) -> dict[str, Any]:
        autopilot = IntelligentAutopilot(
            llm_client=llm_client,
            console=console,
            logger=logger,
            use_enhanced_ui=use_enhanced_ui,
            runtime_diagnostics_hooks=hooks,
            **autopilot_kwargs,
        )
        return autopilot.execute(goal, context=context)

    return _execute


def _result_summary(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"success": bool(result)}
    summary: dict[str, Any] = {"success": bool(result.get("success"))}
    for key in ("goal", "failure_stage", "failed_tool", "failure_reason"):
        value = result.get(key)
        if value not in (None, "", [], {}):
            summary[key] = value
    runtime_report = result.get("runtime_report")
    if isinstance(runtime_report, dict):
        for key in ("phase", "verification_status", "completion_reason"):
            value = runtime_report.get(key)
            if value not in (None, "", [], {}):
                summary[f"runtime_report.{key}"] = value
    return summary
