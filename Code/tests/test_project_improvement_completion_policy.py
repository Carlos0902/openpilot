from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

import metadata
from autonomous_iteration.project_improvement_runtime import ProjectImprovementRuntime
from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from autonomous_iteration.models import EvaluationResult
from autonomous_iteration.runtime_controller import AgentRuntimeController, _RuntimeSessionExecutor
from autonomous_iteration.core_completion_handoff import compose_overall_success
from autonomous_iteration.task_models import Task, TaskDecompositionResult, TaskExecutionResult, TaskStatus
from core.exceptions import ContextAssemblyBudgetError, OpenPilotError
from metadata import (
    FileArtifactMetadata,
    FailureMetadata,
    ResultStatus,
    RuntimeStateMetadata,
    ProjectImprovementPolicy,
    ProjectImprovementStatus,
    ToolExecutionEnvelopeMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
)
from ui.enhanced_cli import _runtime_options_from_args


def _requirement_type():
    """Resolve the intended public enum without breaking test collection first."""
    return getattr(metadata, "ProjectImprovementRequirement")


def _policy_type():
    return getattr(metadata, "ProjectImprovementPolicy")


def _policy(value: str):
    requirement = _requirement_type()(value)
    if value == "disabled":
        return _policy_type()(
            requirement=requirement,
            source="runtime_config",
            target_successes=0,
            max_attempts=0,
        )
    return _policy_type()(
        requirement=requirement,
        source="user_selected" if value == "required" else "automatic_default",
        target_successes=1,
        max_attempts=3,
    )


def _completed_decomposition() -> TaskDecompositionResult:
    task = Task(id="core-task", description="Build the requested project")
    task.mark_completed({"ok": True})
    return TaskDecompositionResult(
        original_task=Task(id="root", description="Build project"),
        subtasks=[task],
        task_graph_summary="one core task",
        decomposition_rationale="fixture",
        estimated_total_effort=1.0,
    )


def _failed_improvement_bridge(policy: str) -> IntelligentAutopilot:
    autopilot = IntelligentAutopilot.__new__(IntelligentAutopilot)
    autopilot.project_improvement_policy = _policy(policy)
    autopilot._build_prompt_context = lambda **_kwargs: {}
    autopilot._dashboard_stage_id = lambda _stage: None
    envelope = ToolExecutionEnvelopeMetadata(
        tool_name="project_improvement_tool",
        step_id="project_improvement",
        status=ResultStatus.FAIL,
        success=False,
        input_metadata=ToolInputMetadata(tool_name="project_improvement_tool"),
        failure=FailureMetadata(
            error_type="InvalidLLMResponseError",
            error_message="bounded delta invalid",
            details={"finish_reason": "stop"},
        ),
    )
    autopilot._execute_project_improvement_agent_tool = lambda **_kwargs: envelope
    return autopilot


def _validated_evaluation() -> EvaluationResult:
    return EvaluationResult(
        validation_passed=True,
        runnable=True,
        has_blocking_bugs=False,
        summary="validated",
    )


def test_required_project_improvement_bridge_preserves_failed_envelope() -> None:
    autopilot = _failed_improvement_bridge("required")

    with pytest.raises(OpenPilotError, match="bounded delta invalid") as exc:
        autopilot._analyze_project_improvements(
            goal="Improve",
            project_path=Path("/tmp/project"),
            written_files=["/tmp/project/app.py"],
            run_command="pytest",
            readme_path=Path("/tmp/project/README.md"),
            completed_iteration=0,
            evaluation=_validated_evaluation(),
        )

    assert exc.value.context["failed_tool"] == "project_improvement_tool"
    assert exc.value.context["tool_failure"]["error_type"] == "InvalidLLMResponseError"


def test_optional_project_improvement_bridge_keeps_controlled_fallback() -> None:
    autopilot = _failed_improvement_bridge("optional")

    result = autopilot._analyze_project_improvements(
        goal="Improve",
        project_path=Path("/tmp/project"),
        written_files=["/tmp/project/app.py"],
        run_command="pytest",
        readme_path=Path("/tmp/project/README.md"),
        completed_iteration=0,
        evaluation=_validated_evaluation(),
    )

    assert result["source"] == "fallback"
    assert result["fallback_reason"] == "bounded delta invalid"


class _CompletionRuntime:
    def __init__(self, tmp_path: Path, *, policy: str, improvement_behavior) -> None:
        self.project_improvement_policy = _policy(policy)
        self.required_successful_improvements = 1
        self.stats = {
            "success": False,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "start_time": None,
            "end_time": None,
        }
        self.enhanced_ui = None
        self._improvement_behavior = improvement_behavior
        self.improvement_calls = 0
        self.project_path = tmp_path
        self.written_files = [str(tmp_path / "app.py")]

    def _finalize_project_readme(self, goal, results):
        return None

    def _collect_written_files(self, results):
        return list(self.written_files)

    def _infer_project_path_from_files(self, goal, written_files):
        return self.project_path

    def _run_iterative_improvement(self, **kwargs):
        self.improvement_calls += 1
        if isinstance(self._improvement_behavior, BaseException):
            raise self._improvement_behavior
        return self._improvement_behavior

    @staticmethod
    def _format_iteration_failure(improvement_result):
        return improvement_result.get("failure_reason") or "project improvement failed"


def _returned_improvement_failure() -> dict:
    return {
        "success": False,
        "status": "failed",
        "failure_stage": "Project Improvement",
        "failed_tool": "project_improvement_tool",
        "failure_reason": "candidate did not pass its acceptance check",
        "completed_improvements": 0,
        "required_improvements": 1,
    }


def _finalize_and_score(runtime: _CompletionRuntime):
    executor = _RuntimeSessionExecutor(runtime)
    decomposition = _completed_decomposition()
    core_result = TaskExecutionResult(task_id="core-task", status=TaskStatus.COMPLETED)
    _, _, _, improvement_outcome = executor._finalize_project_outputs(
        "Build project",
        [core_result],
        True,
    )
    overall_success, warning = executor._update_stats(decomposition, improvement_outcome)
    return decomposition, core_result, improvement_outcome, overall_success, warning


def test_completion_policy_is_typed_and_accepts_only_disabled_optional_required() -> None:
    policy_type = _requirement_type()

    assert [member.value for member in policy_type] == ["disabled", "optional", "required"]
    adapter = TypeAdapter(policy_type)
    assert adapter.validate_python("disabled") == policy_type.DISABLED
    assert adapter.validate_python("optional") == policy_type.OPTIONAL
    assert adapter.validate_python("required") == policy_type.REQUIRED
    with pytest.raises(ValidationError):
        adapter.validate_python("best_effort")


def test_runtime_state_persists_and_validates_completion_policy() -> None:
    state = RuntimeStateMetadata(
        goal="Build project",
        project_improvement_policy=_policy("optional"),
    )

    assert state.project_improvement_policy.requirement == "optional"
    assert state.project_improvement_policy.target_successes == 1
    assert state.to_json_dict()["project_improvement_policy"]["requirement"] == "optional"
    with pytest.raises(ValidationError):
        RuntimeStateMetadata(
            goal="Build project",
            project_improvement_policy={
                "requirement": "best_effort",
                "source": "runtime_config",
                "target_successes": 1,
                "max_attempts": 3,
            },
        )


def test_cli_runtime_options_distinguish_automatic_optional_from_user_required() -> None:
    automatic = _runtime_options_from_args(
        SimpleNamespace(improvement_iterations=None),
        project_prompt_default=False,
    )
    explicit = _runtime_options_from_args(
        SimpleNamespace(improvement_iterations=2),
        project_prompt_default=False,
    )
    disabled = _runtime_options_from_args(
        SimpleNamespace(improvement_iterations=0),
        project_prompt_default=False,
    )

    assert automatic.project_improvement_policy.requirement == "optional"
    assert automatic.project_improvement_policy.source == "automatic_default"
    assert explicit.project_improvement_policy.requirement == "required"
    assert explicit.project_improvement_policy.source == "user_selected"
    assert disabled.project_improvement_policy.requirement == "disabled"


def test_disabled_policy_does_not_attempt_project_improvement(tmp_path) -> None:
    runtime = _CompletionRuntime(
        tmp_path,
        policy="disabled",
        improvement_behavior=_returned_improvement_failure(),
    )
    executor = _RuntimeSessionExecutor(runtime)

    _, _, _, outcome = executor._finalize_project_outputs(
        "Build project",
        [TaskExecutionResult(task_id="core-task", status=TaskStatus.COMPLETED)],
        True,
    )

    assert outcome is None
    assert runtime.improvement_calls == 0


def test_optional_returned_improvement_failure_preserves_core_success_and_outcome(tmp_path) -> None:
    runtime = _CompletionRuntime(
        tmp_path,
        policy="optional",
        improvement_behavior=_returned_improvement_failure(),
    )

    decomposition, core_result, outcome, overall_success, warning = _finalize_and_score(runtime)

    assert overall_success is True
    assert warning == "candidate did not pass its acceptance check"
    assert outcome["success"] is False
    assert outcome["failure_stage"] == "Project Improvement"
    assert core_result.status == TaskStatus.COMPLETED
    assert decomposition.subtasks[0].status == TaskStatus.COMPLETED
    assert runtime.stats["tasks_completed"] == 1
    assert runtime.stats["tasks_failed"] == 0


def test_optional_context_budget_failure_is_normalized_to_warning(tmp_path) -> None:
    runtime = _CompletionRuntime(
        tmp_path,
        policy="optional",
        improvement_behavior=ContextAssemblyBudgetError(["project-evidence"]),
    )

    decomposition, core_result, outcome, overall_success, warning = _finalize_and_score(runtime)

    assert overall_success is True
    assert warning == "Required context cannot fit within the configured prompt budget"
    assert outcome["success"] is False
    assert outcome["status"] == "interrupted"
    assert outcome["error_type"] == "ContextAssemblyBudgetError"
    assert outcome["failure_stage"] == "Project Improvement"
    assert core_result.status == TaskStatus.COMPLETED
    assert decomposition.subtasks[0].status == TaskStatus.COMPLETED


@pytest.mark.parametrize(
    "improvement_behavior, expected_reason",
    [
        (_returned_improvement_failure(), "candidate did not pass its acceptance check"),
        (
            ContextAssemblyBudgetError(["project-evidence"]),
            "Required context cannot fit within the configured prompt budget",
        ),
    ],
)
def test_required_improvement_failure_fails_overall_without_rewriting_core_result(
    tmp_path,
    improvement_behavior,
    expected_reason,
) -> None:
    runtime = _CompletionRuntime(
        tmp_path,
        policy="required",
        improvement_behavior=improvement_behavior,
    )

    decomposition, core_result, outcome, overall_success, warning = _finalize_and_score(runtime)

    assert overall_success is False
    assert warning == expected_reason
    assert outcome["success"] is False
    assert core_result.status == TaskStatus.COMPLETED
    assert core_result.error is None
    assert decomposition.subtasks[0].status == TaskStatus.COMPLETED
    assert decomposition.subtasks[0].error is None
    assert runtime.stats["tasks_completed"] == 1
    assert runtime.stats["tasks_failed"] == 0


class _RecordingHooks:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def on_log_event(self, **event):
        self.events.append(event)


class _RaisingIterationAgent:
    @staticmethod
    def run_project_pipeline(**kwargs):
        raise ContextAssemblyBudgetError(["large-project-context"])


class _ProjectImprovementAutopilot:
    def __init__(self) -> None:
        self.enable_iterative_improvement = True
        self.required_successful_improvements = 1
        self.max_iteration_attempts = 2
        self.session_id = "session"
        self.enhanced_ui = None
        self.logger = None
        self.runtime_diagnostics_hooks = _RecordingHooks()
        self.iterative_improvement = _RaisingIterationAgent()

    @staticmethod
    def _resolve_project_improvement_iterations(goal, project_path) -> bool:
        return True

    @staticmethod
    def _sync_project_environment(**kwargs):
        return ToolExecutionEnvelopeMetadata(
            tool_name="project_environment_tool",
            step_id="environment",
            status=ResultStatus.SUCCESS,
            success=True,
            input_metadata=ToolInputMetadata(tool_name="project_environment_tool"),
            output_metadata=ToolResultMetadata(
                tool_name="project_environment_tool",
                status=ResultStatus.SUCCESS,
                result=FileArtifactMetadata(
                    file_path="environment.json",
                    attributes={"run_command": "python app.py"},
                ),
            ),
        )

    @staticmethod
    def _handle_iteration_progress(event, payload) -> None:
        return None

    @staticmethod
    def _dashboard_stage_id(stage_key):
        return None


def test_project_improvement_runtime_normalizes_exception_and_emits_terminal_trajectory(tmp_path) -> None:
    autopilot = _ProjectImprovementAutopilot()
    runtime = ProjectImprovementRuntime(autopilot)

    result = runtime.run(
        goal="Improve project",
        project_path=tmp_path,
        written_files=[str(tmp_path / "app.py")],
    )

    assert result["success"] is False
    assert result["status"] == "interrupted"
    assert result["error_type"] == "ContextAssemblyBudgetError"
    assert result["failure_stage"] == "Project Improvement"
    assert result["failure_reason"] == "Required context cannot fit within the configured prompt budget"
    terminal_events = [
        event
        for event in autopilot.runtime_diagnostics_hooks.events
        if event["event_type"] in {"pipeline_failed", "pipeline_finished"}
    ]
    assert [event["event_type"] for event in terminal_events] == [
        "pipeline_failed",
        "pipeline_finished",
    ]
    assert all(event["success"] is False for event in terminal_events)
    assert terminal_events[-1]["output_summary"]["error_type"] == "ContextAssemblyBudgetError"


def test_required_improvement_failure_is_preserved_in_result_and_runtime_state(tmp_path) -> None:
    runtime = _CompletionRuntime(
        tmp_path,
        policy="required",
        improvement_behavior=ContextAssemblyBudgetError(["project-evidence"]),
    )
    executor = _RuntimeSessionExecutor(runtime)
    decomposition = _completed_decomposition()
    core_result = TaskExecutionResult(task_id="core-task", status=TaskStatus.COMPLETED)
    _, _, _, outcome = executor._finalize_project_outputs("Build project", [core_result], True)
    overall_success, warning = executor._update_stats(decomposition, outcome)

    result = executor._build_result(
        goal="Build project",
        semantic=None,
        decomposition=decomposition,
        results=[core_result],
        readme_result=None,
        improvement_result=outcome,
        success=overall_success,
        iteration_error_msg=warning,
        include_final_result=False,
    )
    state = RuntimeStateMetadata(goal="Build project", project_improvement_policy=_policy("required"))
    controller = SimpleNamespace(
        _emit_runtime_phase_change=lambda *args: None,
        _emit_verification_state_change=lambda *args: None,
    )
    AgentRuntimeController._absorb_session_result(controller, state, result)

    assert result["core_success"] is True
    assert result["error_type"] == "ContextAssemblyBudgetError"
    assert result["failure_reason"] == "Required context cannot fit within the configured prompt budget"
    assert state.core_success is True
    assert state.project_improvement_status == "interrupted"
    assert state.completion_reason == result["failure_reason"]


def test_new_automatic_policy_uses_zero_hard_one_accepted_one_attempt() -> None:
    policy = ProjectImprovementPolicy()

    assert policy.requirement == "optional"
    assert policy.source == "automatic_default"
    assert policy.required_accepted_transactions == 0
    assert policy.max_accepted_transactions == 1
    assert policy.max_attempts == 1
    assert policy.target_successes == 1


@pytest.mark.parametrize(
    ("payload", "required", "maximum", "attempts"),
    [
        (
            {
                "requirement": "optional",
                "source": "automatic_default",
                "target_successes": 2,
                "max_attempts": 4,
            },
            0,
            2,
            4,
        ),
        (
            {
                "requirement": "required",
                "source": "user_selected",
                "target_successes": 2,
                "max_attempts": 4,
            },
            2,
            2,
            4,
        ),
        (
            {
                "requirement": "disabled",
                "source": "runtime_config",
                "target_successes": 0,
                "max_attempts": 0,
            },
            0,
            0,
            0,
        ),
    ],
)
def test_legacy_policy_counts_migrate_source_sensitively(
    payload: dict[str, object],
    required: int,
    maximum: int,
    attempts: int,
) -> None:
    policy = ProjectImprovementPolicy.model_validate(payload)

    assert policy.required_accepted_transactions == required
    assert policy.max_accepted_transactions == maximum
    assert policy.max_attempts == attempts
    assert policy.target_successes == maximum


def test_new_and_legacy_policy_count_conflicts_are_rejected() -> None:
    with pytest.raises(ValidationError, match="policy_count_conflict"):
        ProjectImprovementPolicy(
            requirement="optional",
            target_successes=2,
            required_accepted_transactions=0,
            max_accepted_transactions=1,
            max_attempts=2,
        )
    with pytest.raises(ValidationError, match="optional project improvement requires zero"):
        ProjectImprovementPolicy(
            requirement="optional",
            required_accepted_transactions=1,
            max_accepted_transactions=1,
            max_attempts=1,
        )
    with pytest.raises(ValidationError, match="at least required"):
        ProjectImprovementPolicy(
            requirement="required",
            required_accepted_transactions=2,
            max_accepted_transactions=1,
            max_attempts=2,
        )
    with pytest.raises(ValidationError, match="literal integers"):
        ProjectImprovementPolicy(
            requirement="optional",
            required_accepted_transactions=False,
            max_accepted_transactions=True,
            max_attempts=1,
        )


def test_new_stage_statuses_round_trip_and_accepted_satisfies_required_policy() -> None:
    adapter = TypeAdapter(ProjectImprovementStatus)
    expected = {
        "reviewing",
        "planned",
        "executing",
        "validating",
        "value_evaluating",
        "rolling_back",
        "accepted",
        "no_worthwhile_improvement",
        "attempts_exhausted",
        "neutral",
        "negative",
        "inconclusive",
        "rolled_back",
        "reconciliation_required",
    }

    assert {adapter.validate_python(value).value for value in expected} == expected
    policy = ProjectImprovementPolicy(
        requirement="required",
        required_accepted_transactions=1,
        max_accepted_transactions=1,
        max_attempts=1,
    )
    assert compose_overall_success(
        core_success=True,
        policy=policy,
        improvement_status=ProjectImprovementStatus.ACCEPTED,
    ) is True
