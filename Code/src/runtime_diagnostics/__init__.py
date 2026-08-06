"""Runtime diagnostics for real-task-driven problem discovery."""

from importlib import import_module

from runtime_diagnostics.raw_task import RawTaskInput
from runtime_diagnostics.collector import (
    collect_from_failure,
    collect_from_runtime_state,
    collect_from_tool_error,
    suspicious_success_signal,
)
from runtime_diagnostics.judge import judge_signal
from runtime_diagnostics.recorder import DiagnosticRecorder
from runtime_diagnostics.summarizer import (
    DiagnosticSummary,
    RepeatedSignalSummary,
    TaskSummary,
    render_summary_markdown,
    summarize_records,
    write_summary_json,
    write_summary_markdown,
)

from runtime_diagnostics.task_pool import load_raw_tasks
from runtime_diagnostics.models import RunRecord, EventRecord, ArtifactRecord, RunSummaryRecord

__all__ = [
    "RawTaskInput",
    "collect_from_failure",
    "collect_from_runtime_state",
    "collect_from_tool_error",
    "suspicious_success_signal",
    "judge_signal",
    "DiagnosticRecorder",
    "DiagnosticSummary",
    "RepeatedSignalSummary",
    "TaskSummary",
    "render_summary_markdown",
    "summarize_records",
    "write_summary_json",
    "write_summary_markdown",
    "load_raw_tasks",
    "RuntimeTaskPoolRunner",
    "TaskPoolRunResult",
    "build_autopilot_executor",
    "RunRecord",
    "EventRecord",
    "ArtifactRecord",
    "RunSummaryRecord",
    "TrajectoryLLMClientProxy",
]


def __getattr__(name: str):
    if name in {"RuntimeTaskPoolRunner", "TaskPoolRunResult", "build_autopilot_executor"}:
        module = import_module("runtime_diagnostics.runner")
        return getattr(module, name)
    if name == "TrajectoryLLMClientProxy":
        module = import_module("runtime_diagnostics.llm_proxy")
        return getattr(module, name)
    raise AttributeError(f"module 'runtime_diagnostics' has no attribute {name!r}")
