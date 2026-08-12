from __future__ import annotations

from pathlib import Path

from autonomous_iteration.models import EvaluationResult, IterationResult
from autonomous_iteration.project_improvement_runtime import ProjectImprovementRuntime
from core.openpilot_log import OpenPilotLogger
from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from metadata import (
    ConversationIdentity,
    FailureMetadata,
    ResultStatus,
    SessionIngressState,
    ToolExecutionEnvelopeMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
    payload_to_artifact,
)
from runtime_diagnostics import DiagnosticRecorder
from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks


class FakeIterationAgent:
    def __init__(self) -> None:
        self.callbacks_seen: set[str] = set()
        self.session_ingress_state = None

    def run_project_pipeline(self, **kwargs):
        self.session_ingress_state = kwargs.get("session_ingress_state")
        evaluation = EvaluationResult(
            validation_passed=True,
            runnable=True,
            has_blocking_bugs=False,
            summary="ok",
            run_command=kwargs["run_command"],
        )
        state = kwargs["read_project_state"](evaluation, 0)
        result = kwargs["apply_improvement"](1, evaluation, ["Improve app.py"], {"summary": "report"}, False)
        report = kwargs["analyze_improvements"](0, evaluation)
        kwargs["on_progress"](
            "context_loader",
            {
                "context": {
                    "related_memories": [],
                    "related_files": [],
                    "context_selection": {
                        "kind": "context_selection",
                        "max_prompt_chars": 16_000,
                        "original_prompt_chars": 120,
                        "final_prompt_chars": 120,
                        "truncated": False,
                    },
                },
                "iteration": 0,
            },
        )
        self.callbacks_seen = {"read_project_state", "apply_improvement", "analyze_improvements", "on_progress"}
        return {
            "success": True,
            "partial_success": False,
            "completed_improvements": 1,
            "required_improvements": 1,
            "completed_iterations": 1,
            "required_iterations": 1,
            "attempts_used": 1,
            "max_iteration_attempts": 2,
            "validation": evaluation,
            "evaluation": evaluation,
            "evaluations": [evaluation],
            "iterations": [result],
            "improvement_report": report,
            "project_state": state,
            "project_states": [state],
            "iteration_goals": [],
            "designed_tasks": [],
            "mind_notes": [],
            "autonomous_iteration": None,
            "failure_stage": None,
            "failed_iteration": None,
            "failed_tool": None,
            "failure_reason": None,
            "retry_attempted": False,
            "retry_history": [],
            "last_successful_iteration": 1,
            "remaining_goals": [],
        }


class FakeAutopilot:
    def __init__(
        self,
        tmp_path: Path,
        environment_success: bool = True,
        *,
        environment_failures_before_success: int = 0,
        environment_error: str = "env failed",
        environment_errors: list[str] | None = None,
    ) -> None:
        self.enable_iterative_improvement = True
        self.required_successful_improvements = 1
        self.max_iteration_attempts = 2
        self.enhanced_ui = None
        self.session_id = "session"
        self.logger = OpenPilotLogger(tmp_path / "project_improvement_runtime.jsonl")
        self.iterative_improvement = FakeIterationAgent()
        self.memory_store = None
        self.progress_events: list[str] = []
        self.runtime_diagnostics_hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))
        self.environment_success = environment_success
        self.environment_failures_before_success = environment_failures_before_success
        self.environment_error = environment_error
        self.environment_errors = list(environment_errors or [])
        self.environment_calls = 0

    def _resolve_project_improvement_iterations(self, goal, project_path) -> bool:
        return True

    def _sync_project_environment(self, **kwargs):
        self.environment_calls += 1
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
        return _tool_envelope("project_environment_tool", {"success": True, "result": {"run_command": "python app.py"}}, kwargs.get("input_metadata"))

    def _handle_iteration_progress(self, event, payload) -> None:
        self.progress_events.append(event)

    def _apply_project_improvement(self, **kwargs) -> IterationResult:
        return IterationResult(
            iteration=kwargs["iteration"],
            validation_passed=True,
            completed_successful_iteration=True,
            applied_actions=kwargs["actions"],
            changed_files=[str(kwargs["project_path"] / "app.py")],
            success=True,
        )

    def _analyze_project_improvements(self, **kwargs) -> dict:
        return {"summary": "Need polish."}

    def _execute_fast_tool(self, **kwargs):
        input_metadata = kwargs["input_metadata"]
        params = input_metadata.to_params() if hasattr(input_metadata, "to_params") else input_metadata
        return _tool_envelope(
            "project_state_reader",
            {
                "success": True,
                "result": {
                "project_path": params["project_path"],
                "written_files": params["written_files"],
            },
            },
            input_metadata,
        )

    def _dashboard_stage_id(self, stage_key):
        return None


def _tool_envelope(tool_name: str, data: dict, input_metadata: ToolInputMetadata | None = None) -> ToolExecutionEnvelopeMetadata:
    success = bool(data.get("success"))
    output_metadata = (
        ToolResultMetadata(tool_name=tool_name, status=ResultStatus.SUCCESS, result=payload_to_artifact(tool_name, data.get("result"), input_metadata))
        if success
        else None
    )
    failure = None if success else FailureMetadata(error_type="ToolError", error_message=str(data.get("error") or f"{tool_name} failed"))
    return ToolExecutionEnvelopeMetadata(
        tool_name=tool_name,
        step_id=tool_name,
        status=ResultStatus.SUCCESS if success else ResultStatus.FAIL,
        success=success,
        input_metadata=input_metadata or ToolInputMetadata(tool_name=tool_name),
        output_metadata=output_metadata,
        failure=failure,
    )


class FakeLLM:
    pass


def test_project_improvement_runtime_environment_failure(tmp_path) -> None:
    runtime = ProjectImprovementRuntime(FakeAutopilot(tmp_path, environment_success=False))

    result = runtime.run(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(tmp_path / "app.py")],
    )

    assert result["success"] is False
    assert result["failure_stage"] == "Environment Setup"
    assert result["failed_tool"] == "project_environment_tool"
    assert result["validation"].validation_errors == ["env failed"]


def test_project_improvement_runtime_repairs_invalid_requirements_then_retries(tmp_path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("rich\n\"\"\"\nrequests>=2\n", encoding="utf-8")
    error = (
        "[notice] A new release of pip is available: 25.3 -> 26.1.1\n"
        f"ERROR: Invalid requirement: '\"\"\"': Expected package name at the start of dependency specifier\n"
        "    \"\"\"\n"
        f"    ^ (from line 2 of {requirements})\n"
    )
    autopilot = FakeAutopilot(
        tmp_path,
        environment_failures_before_success=1,
        environment_error=error,
    )
    runtime = ProjectImprovementRuntime(autopilot)

    result = runtime.run(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(tmp_path / "app.py")],
    )

    assert result["success"] is True
    assert autopilot.environment_calls == 2
    assert requirements.read_text(encoding="utf-8") == "rich\nrequests>=2\n"


def test_project_improvement_runtime_iterates_environment_repairs_until_sync_succeeds(tmp_path) -> None:
    requirements = tmp_path / "requirements.txt"
    secondary_requirements = tmp_path / "requirements-dev.txt"
    requirements.write_text('rich\n"""\n', encoding="utf-8")
    secondary_requirements.write_text('pytest\n"""\n', encoding="utf-8")
    invalid_requirement = (
        f"ERROR: Invalid requirement: '\"\"\"': Expected package name at the start of dependency specifier\n"
        f"    ^ (from line 2 of {requirements})\n"
    )
    secondary_invalid_requirement = (
        f"ERROR: Invalid requirement: '\"\"\"': Expected package name at the start of dependency specifier\n"
        f"    ^ (from line 2 of {secondary_requirements})\n"
    )
    autopilot = FakeAutopilot(
        tmp_path,
        environment_errors=[invalid_requirement, secondary_invalid_requirement],
    )
    runtime = ProjectImprovementRuntime(autopilot)

    result = runtime.run(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(tmp_path / "app.py")],
    )

    assert result["success"] is True
    assert autopilot.environment_calls == 3
    assert requirements.read_text(encoding="utf-8") == "rich\n"
    assert secondary_requirements.read_text(encoding="utf-8") == "pytest\n"


def test_project_improvement_runtime_success_callbacks_and_shape(tmp_path) -> None:
    autopilot = FakeAutopilot(tmp_path)
    runtime = ProjectImprovementRuntime(autopilot)

    result = runtime.run(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(tmp_path / "app.py")],
    )

    assert result["success"] is True
    assert result["validation"].validation_passed is True
    assert result["iterations"][0].success is True
    assert autopilot.iterative_improvement.callbacks_seen == {
        "read_project_state",
        "apply_improvement",
        "analyze_improvements",
        "on_progress",
    }
    assert autopilot.progress_events == ["context_loader"]


def test_project_improvement_runtime_forwards_session_ingress_state(tmp_path) -> None:
    autopilot = FakeAutopilot(tmp_path)
    runtime = ProjectImprovementRuntime(autopilot)
    state = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="session",
            run_id="run-1",
            turn_index=0,
            project_root=str(tmp_path),
        )
    )

    runtime.run(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(tmp_path / "app.py")],
        session_ingress_state=state,
    )

    assert autopilot.iterative_improvement.session_ingress_state is state


def test_project_improvement_runtime_records_trajectory_events(tmp_path) -> None:
    autopilot = FakeAutopilot(tmp_path)
    runtime = ProjectImprovementRuntime(autopilot)

    runtime.run(
        goal="Inspect project runtime flow",
        project_path=tmp_path,
        written_files=[str(tmp_path / "app.py")],
    )

    run = autopilot.runtime_diagnostics_hooks.recorder.load_run(autopilot.session_id)
    assert run is not None
    events = autopilot.runtime_diagnostics_hooks.recorder.load_trajectory_events(run.run_id)
    event_types = [event["event_type"] for event in events]
    assert "pipeline_started" in event_types
    assert "environment_sync_completed" in event_types
    assert "pipeline_progress" in event_types
    assert "project_state_read" in event_types
    assert "pipeline_finished" in event_types
    progress_event = next(event for event in events if event["event_type"] == "pipeline_progress")
    assert progress_event["payload"]["annotations"]["module"] == "project_improvement_runtime"
    assert progress_event["payload"]["correlation"]["session_id"] == autopilot.session_id
    assert progress_event["payload"]["output_summary"]["context"]["context_selection"]["kind"] == "context_selection"


def test_intelligent_autopilot_iterative_improvement_proxy_uses_project_improvement_runtime(tmp_path) -> None:
    class FakeProjectImprovementRuntime:
        def __init__(self):
            self.kwargs = None

        def run(self, **kwargs):
            self.kwargs = kwargs
            return {"success": True, "goal": kwargs["goal"]}

    autopilot = IntelligentAutopilot(FakeLLM(), log_file=tmp_path / "autopilot.jsonl")
    proxy = FakeProjectImprovementRuntime()
    autopilot.project_improvement_runtime = proxy
    conversation_id = autopilot.session_id or "conversation-1"
    state = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id=conversation_id,
            run_id="run-1",
            turn_index=0,
            project_root=str(tmp_path),
        )
    )

    result = autopilot._run_iterative_improvement(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(tmp_path / "app.py")],
        session_ingress_state=state,
    )

    assert result == {"success": True, "goal": "Improve project"}
    assert proxy.kwargs["session_ingress_state"] is state


def test_iterative_improvement_scopes_parent_ingress_to_generated_child(tmp_path) -> None:
    class FakeProjectImprovementRuntime:
        def __init__(self):
            self.kwargs = None

        def run(self, **kwargs):
            self.kwargs = kwargs
            return {"success": True}

    parent = tmp_path / "workspace"
    child = parent / "generated" / "snake"
    autopilot = IntelligentAutopilot(FakeLLM(), log_file=tmp_path / "autopilot.jsonl")
    proxy = FakeProjectImprovementRuntime()
    autopilot.project_improvement_runtime = proxy
    state = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id=autopilot.session_id or "conversation-1",
            run_id="run-1",
            turn_index=0,
            project_root=str(parent),
        )
    )

    result = autopilot._run_iterative_improvement(
        goal="Improve generated project",
        project_path=child,
        written_files=[str(child / "app.py")],
        session_ingress_state=state,
    )

    assert result == {"success": True}
    scoped = proxy.kwargs["session_ingress_state"]
    assert scoped.identity.project_root == str(child.resolve())
    assert state.identity.project_root == str(parent)
