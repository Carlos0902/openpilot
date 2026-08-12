from __future__ import annotations

import hashlib
import autonomous_iteration.runtime_controller
import os
import pytest
import shlex
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace
from tools.tool_selection import SelectionReason, ToolSelection

from autonomous_iteration.checkpoint_store import RuntimeCheckpointStore
from autonomous_iteration.runtime_controller import (
    ActiveDiagnosticEvaluator,
    AgentRuntimeController,
    EditGuard,
    FileSelector,
    RuntimeGuard,
    RuntimeReporter,
    StateUpdater,
    ToolRouter,
)
from runtime_diagnostics import DiagnosticRecorder
from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks
from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.short_memory import ShortMemory
from metadata import (
    ActiveDiagnosticConflict,
    ActiveDiagnosticDecision,
    ActiveDiagnosticDecisionKind,
    ActiveDiagnosticItemStatus,
    ActiveDiagnosticRisk,
    ActiveDiagnosticRiskSeverity,
    AgentPhase,
    CheckpointBoundary,
    CheckpointFaultPoint,
    ConversationIdentity,
    ContextCompactionBinding,
    ContextCompactionRecord,
    ContextSelectionMetadata,
    FinalizationFaultPoint,
    DecisionNeedMetadata,
    EditPlanMetadata,
    EnvironmentOperation,
    EnvironmentReadiness,
    EnvironmentSyncMetadata,
    LLMRequestHashVersion,
    PendingLLMRequest,
    FileArtifactMetadata,
    ProjectFingerprint,
    Recoverability,
    RecoveryAutomationPolicy,
    RecoveryFallbackAction,
    RecoveryMode,
    RecoveryReasonCode,
    RecoveryStatus,
    ResultStatus,
    RuntimeCheckpointMetadata,
    RuntimeExecutionMode,
    RuntimeExecutionModeSource,
    RuntimeFinalizationStage,
    RuntimePromptContextSnapshot,
    RuntimeStateMetadata,
    RuntimeTaskPurpose,
    SearchArtifactMetadata,
    SessionConstraintState,
    SessionIngressState,
    SessionTurn,
    SessionExecutionCursor,
    SessionSemanticSnapshot,
    SessionStage,
    SessionTaskResult,
    TaskGraphNodeMetadata,
    ObservedFileMutationResult,
    TextArtifactMetadata,
    VerificationPlanMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
)


def test_durable_tool_input_excludes_runtime_only_handles() -> None:
    input_metadata = ToolInputMetadata.from_mapping(
        "bug_fix_tool",
        {
            "command": "python app.py",
            "_thread_lock": threading.Lock(),
        },
    )

    durable = AgentRuntimeController._durable_tool_input(input_metadata)

    assert durable.command == "python app.py"
    assert durable.runtime_handles == {}


def _attach_ready_test_environment(runtime, project: Path) -> Path:
    project = project.resolve()
    python_executable = project / ".venv" / "bin" / "python"
    environment_id = f"test-env:{project}"
    runtime._project_environments = {
        str(project): {
            "readiness": "ready",
            "environment_id": environment_id,
            "project_path": str(project),
            "command_cwd": str(project),
            "python_executable": str(python_executable),
            "python_command": str(python_executable),
            "pip_executable": str(project / ".venv" / "bin" / "pip"),
            "pip_command": str(project / ".venv" / "bin" / "pip"),
            "command_env": {"VIRTUAL_ENV": str(project / ".venv")},
        }
    }

    def bind_command_context(_tool_name, input_metadata):
        command = str(input_metadata.command or "")
        argv = shlex.split(command)
        updates = {
            "cwd": str(project),
            "env": {**(input_metadata.env or {}), "VIRTUAL_ENV": str(project / ".venv")},
            "environment_id": environment_id,
        }
        if argv and Path(argv[0]).name.lower() in {"python", "python3"}:
            argv[0] = str(python_executable)
            updates.update(
                {
                    "command": shlex.join(argv),
                    "requested_command": command,
                    "effective_interpreter": str(python_executable),
                }
            )
        return input_metadata.model_copy(update=updates)

    runtime._apply_project_command_context = bind_command_context
    return python_executable


def test_project_fingerprint_records_environment_identity(tmp_path) -> None:
    fingerprint = AgentRuntimeController._build_project_fingerprint(
        project_root=str(tmp_path),
        cwd=str(tmp_path),
        interpreter=str(tmp_path / ".venv" / "bin" / "python"),
        environment_id="sha256:environment",
    )

    assert fingerprint.interpreter == str(tmp_path / ".venv" / "bin" / "python")
    assert fingerprint.environment_id == "sha256:environment"


def test_resume_preflight_blocks_when_bound_environment_identity_drifted(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    expected_python = project / ".venv" / "bin" / "python"
    state = RuntimeStateMetadata(goal="Inspect project")
    state.add_assumption("runtime_mode:read_only_analysis")
    checkpoint = RuntimeCheckpointMetadata(
        checkpoint_id="environment-checkpoint",
        generation=1,
        run_id="environment-run",
        root_task_id="environment-task",
        session_id="environment-session",
        checkpoint_reason="task initialized",
        safe_boundary=CheckpointBoundary.TASK_NORMALIZED,
        runtime_state=state,
        project_fingerprint=ProjectFingerprint(
            project_root=str(project),
            cwd=str(project),
            interpreter=str(expected_python),
            environment_id="sha256:expected",
        ),
    )
    current_environment = EnvironmentSyncMetadata(
        operation=EnvironmentOperation.PREFLIGHT,
        readiness=EnvironmentReadiness.READY,
        environment_id="sha256:current",
        project_path=str(project),
        venv_path=str(project / ".venv"),
        python_executable=str(expected_python),
        pip_executable=str(project / ".venv" / "bin" / "pip"),
        command_cwd=str(project),
        python_command=str(expected_python),
    )
    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=None,
        session_id="replacement-session",
        _project_environments={str(project.resolve()): current_environment.to_json_dict()},
    )
    controller = AgentRuntimeController(runtime)
    controller.state = state.model_copy(deep=True)

    decision = controller._resume_preflight(
        checkpoint,
        {"project_path": str(project), "task_id": "environment-task"},
    )

    assert decision.decision == "blocked"
    assert "Python environment identity differs from checkpoint" in decision.project_drift


def test_checkpoint_persists_ready_project_environment_binding(tmp_path) -> None:
    store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=None, session_id="session-1")
    python_executable = _attach_ready_test_environment(runtime, tmp_path)
    controller = AgentRuntimeController(runtime, checkpoint_store=store)
    controller.state = RuntimeStateMetadata(goal="Inspect project")
    controller._checkpointing_enabled = True
    controller._checkpoint_run_id = "run-1"
    controller._active_task_id = "task-1"
    controller._checkpoint_context = {"project_path": str(tmp_path), "cwd": str(tmp_path)}

    assert controller._persist_checkpoint(
        controller.state,
        reason="environment binding test",
        safe_boundary=CheckpointBoundary.TASK_NORMALIZED,
    )
    saved = store.load_latest("run-1")

    assert saved is not None
    assert saved.project_fingerprint.interpreter == str(python_executable)
    assert saved.project_fingerprint.environment_id == f"test-env:{tmp_path.resolve()}"


def test_legacy_python_verification_resume_requires_ready_environment(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = RuntimeStateMetadata(goal="Verify project")
    checkpoint = RuntimeCheckpointMetadata(
        checkpoint_id="legacy-python-checkpoint",
        generation=1,
        run_id="legacy-python-run",
        root_task_id="legacy-python-task",
        session_id="legacy-python-session",
        checkpoint_reason="verification pending",
        safe_boundary=CheckpointBoundary.VERIFICATION_REQUIRED,
        runtime_state=state,
        mutation_class="mutating",
        side_effect_state="applied",
        pending_verification=VerificationPlanMetadata(
            reason="Run required tests",
            commands=["python -m pytest -q"],
        ),
        project_fingerprint=ProjectFingerprint(project_root=str(project), cwd=str(project)),
    )
    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=None,
        session_id="replacement-session",
        _project_environments={},
    )
    controller = AgentRuntimeController(runtime)
    controller.state = state.model_copy(deep=True)

    decision = controller._resume_preflight(
        checkpoint,
        {"project_path": str(project), "task_id": "legacy-python-task"},
    )

    assert decision.decision == "blocked"
    assert "Project environment is not ready for resume" in decision.project_drift


def test_file_mutation_resume_checks_bound_environment_before_verification(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    expected_python = project / ".venv" / "bin" / "python"
    state = RuntimeStateMetadata(goal="Verify mutation")
    checkpoint = RuntimeCheckpointMetadata(
        checkpoint_id="mutation-environment-checkpoint",
        generation=1,
        run_id="mutation-environment-run",
        root_task_id="mutation-environment-task",
        session_id="mutation-environment-session",
        checkpoint_reason="mutation applied",
        safe_boundary=CheckpointBoundary.VERIFICATION_REQUIRED,
        runtime_state=state,
        mutation_class="mutating",
        side_effect_state="applied",
        pending_verification=VerificationPlanMetadata(
            reason="Run required tests",
            commands=["python -m pytest -q"],
        ),
        project_fingerprint=ProjectFingerprint(
            project_root=str(project),
            cwd=str(project),
            interpreter=str(expected_python),
            environment_id="sha256:expected",
        ),
    )
    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=None,
        session_id="replacement-session",
    )
    _attach_ready_test_environment(runtime, project)
    controller = AgentRuntimeController(runtime)
    controller.state = state.model_copy(deep=True)

    decision = controller._resume_preflight(
        checkpoint,
        {"project_path": str(project), "task_id": "mutation-environment-task"},
    )

    assert decision.decision == "blocked"
    assert "Python environment identity differs from checkpoint" in decision.project_drift


def test_resume_blocks_legacy_unbound_llm_identity(tmp_path) -> None:
    state = RuntimeStateMetadata(goal="Inspect project")
    state.add_assumption("runtime_mode:read_only_analysis")
    checkpoint = RuntimeCheckpointMetadata(
        checkpoint_id="legacy-llm-checkpoint",
        generation=1,
        run_id="legacy-llm-run",
        root_task_id="legacy-llm-task",
        session_id="legacy-llm-session",
        checkpoint_reason="provider request prepared",
        safe_boundary=CheckpointBoundary.LLM_REQUEST_PREPARED,
        runtime_state=state,
        pending_llm_request=PendingLLMRequest(
            task_id="legacy-llm-task",
            request_ordinal=1,
            request_hash="sha256:legacy",
            hash_version=LLMRequestHashVersion.LEGACY_UNBOUND_V1,
        ),
        project_fingerprint=ProjectFingerprint(project_root=str(tmp_path), cwd=str(tmp_path)),
    )
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=None, session_id="new")
    controller = AgentRuntimeController(runtime)
    controller.state = state.model_copy(deep=True)

    decision = controller._resume_preflight(
        checkpoint,
        {"project_path": str(tmp_path), "task_id": "legacy-llm-task"},
    )

    assert decision.decision == "blocked"
    assert decision.reason_code == RecoveryReasonCode.LEGACY_LLM_IDENTITY_UNBOUND
    assert decision.fallback.action == RecoveryFallbackAction.OFFER_NEW_LINKED_RUN


@pytest.mark.parametrize(
    "fault_point",
    [
        FinalizationFaultPoint.AFTER_STATE_COMPLETED,
        FinalizationFaultPoint.AFTER_REPORT_PERSISTED,
        FinalizationFaultPoint.AFTER_RUN_FINALIZED,
        FinalizationFaultPoint.AFTER_FINAL_CHECKPOINT_DURABLE,
    ],
)
def test_runtime_finalization_resumes_each_crash_window_exactly_once(tmp_path, fault_point) -> None:
    class InjectedFinalizationCrash(BaseException):
        pass

    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path / "diagnostics"))
    hooks.on_task_received(
        task_id="finalization-task",
        source="test",
        raw_input="Inspect project",
        session_id="finalization-session",
    )
    run = hooks.recorder.load_run("finalization-task")
    assert run is not None
    store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)
    session_calls: list[str] = []

    def run_session(goal, context, mode="standard"):
        session_calls.append(goal)
        return {"success": True, "stats": {"tasks_completed": 1, "tasks_failed": 0}}

    def inject(point, cursor):
        if point == fault_point:
            raise InjectedFinalizationCrash(point.value)

    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=hooks,
        session_id="finalization-session",
    )
    controller = AgentRuntimeController(
        runtime,
        session_executor=SimpleNamespace(run=run_session),
        checkpoint_store=store,
        finalization_fault_injector=inject,
    )

    with pytest.raises(InjectedFinalizationCrash, match=fault_point.value):
        controller.run(
            "Inspect project",
            {
                "task_id": "finalization-task",
                "project_path": str(tmp_path),
                "tags": ["readonly"],
                "task_type": "analysis",
                "checkpointing_enabled": True,
            },
        )
    controller._release_run_lease()
    interrupted = store.load_latest(run.run_id)
    assert interrupted is not None

    replacement = AgentRuntimeController(
        SimpleNamespace(
            tool_registry=None,
            runtime_diagnostics_hooks=hooks,
            session_id="replacement-session",
        ),
        session_executor=SimpleNamespace(
            supports_session_cursor=True,
            run=lambda *_args, **_kwargs: pytest.fail("completed tasks must not be re-executed"),
        ),
        checkpoint_store=store,
    )
    result = replacement.resume(
        run.run_id,
        interrupted.checkpoint_id,
        {"project_path": str(tmp_path), "task_id": "finalization-task"},
    )

    assert result["success"] is True, result
    assert session_calls == ["Inspect project"]
    latest = store.load_latest(run.run_id)
    assert latest is not None
    assert latest.safe_boundary == CheckpointBoundary.RUNTIME_FINALIZED
    assert latest.finalization_cursor is not None
    assert latest.finalization_cursor.stage == RuntimeFinalizationStage.RUN_FINALIZED
    events = hooks.recorder.load_trajectory_events(run.run_id)
    finished = [event for event in events if event["event_type"] == "task_finished"]
    assert len(finished) == 1
    report_artifacts = list((store.root_dir / run.run_id / "recovery_artifacts").glob("*.json"))
    assert len(report_artifacts) == 1


def test_runtime_finalization_recovers_after_real_process_exit(tmp_path) -> None:
    diagnostics_root = tmp_path / "diagnostics"
    run_id_file = tmp_path / "run-id.txt"
    child_code = textwrap.dedent(
        f"""
        import os
        from pathlib import Path
        from types import SimpleNamespace

        from autonomous_iteration.checkpoint_store import RuntimeCheckpointStore
        from autonomous_iteration.runtime_controller import AgentRuntimeController
        from metadata import FinalizationFaultPoint
        from runtime_diagnostics import DiagnosticRecorder
        from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks

        root = Path({str(diagnostics_root)!r})
        hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(root))
        hooks.on_task_received(
            task_id="process-finalization-task",
            source="test",
            raw_input="Inspect project",
            session_id="process-finalization-session",
        )
        run = hooks.recorder.load_run("process-finalization-task")
        Path({str(run_id_file)!r}).write_text(run.run_id, encoding="utf-8")
        store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)

        def inject(point, cursor):
            if point == FinalizationFaultPoint.AFTER_REPORT_PERSISTED:
                os._exit(91)

        runtime = SimpleNamespace(
            tool_registry=None,
            runtime_diagnostics_hooks=hooks,
            session_id="process-finalization-session",
        )
        AgentRuntimeController(
            runtime,
            session_executor=SimpleNamespace(run=lambda *_args, **_kwargs: {{"success": True}}),
            checkpoint_store=store,
            finalization_fault_injector=inject,
        ).run(
            "Inspect project",
            {{
                "task_id": "process-finalization-task",
                "project_path": {str(tmp_path)!r},
                "tags": ["readonly"],
                "task_type": "analysis",
                "checkpointing_enabled": True,
            }},
        )
        """
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    completed = subprocess.run([sys.executable, "-c", child_code], env=environment, check=False)
    assert completed.returncode == 91

    run_id = run_id_file.read_text(encoding="utf-8")
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(diagnostics_root))
    hooks.recorder.attach_existing_run(
        run_id,
        expected_task_id="process-finalization-task",
        expected_session_id="process-finalization-session",
    )
    store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)
    interrupted = store.load_latest(run_id)
    assert interrupted is not None
    assert interrupted.safe_boundary == CheckpointBoundary.RUNTIME_REPORT_PERSISTED
    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=hooks,
        session_id="replacement-session",
    )
    result = AgentRuntimeController(
        runtime,
        session_executor=SimpleNamespace(
            supports_session_cursor=True,
            run=lambda *_args, **_kwargs: pytest.fail("finalization recovery must not rerun tasks"),
        ),
        checkpoint_store=store,
    ).resume(
        run_id,
        interrupted.checkpoint_id,
        {"project_path": str(tmp_path), "task_id": "process-finalization-task"},
    )

    assert result["success"] is True
    latest = store.load_latest(run_id)
    assert latest is not None
    assert latest.safe_boundary == CheckpointBoundary.RUNTIME_FINALIZED
    events = hooks.recorder.load_trajectory_events(run_id)
    assert len([event for event in events if event["event_type"] == "task_finished"]) == 1
def test_runtime_state_serializes_budget_and_phase() -> None:
    state = RuntimeStateMetadata(goal="Refactor runtime")
    state.add_fact("goal understood")
    state.add_unknown("project entrypoint")
    state.budget.consume_tool_call(file_read=True)
    payload = state.to_json_dict()

    assert payload["phase"] == "understand_task"
    assert payload["budget"]["max_tool_calls"] == 20
    assert payload["budget"]["max_file_reads"] == 30
    assert payload["budget"]["max_file_edits"] == 3
    assert payload["budget"]["max_file_creates"] == 20
    assert payload["budget"]["max_verification_attempts"] == 3
    assert payload["budget"]["max_recovery_rounds"] == 3
    assert payload["budget"]["max_replan_rounds"] == 3
    assert payload["budget"]["tool_calls_used"] == 1
    assert payload["known_facts"] == ["goal understood"]
    assert payload["unknowns"] == ["project entrypoint"]
    assert payload["assumptions"] == []
    assert payload["resolved_questions"] == []
    assert payload["decision_history"] == []
    assert payload["execution_mode"] == "mutation_allowed"
    assert payload["execution_mode_source"] == "default"


def test_runtime_state_round_trips_owned_active_diagnostic_facts() -> None:
    state = RuntimeStateMetadata(goal="Diagnose project")
    state.add_diagnostic_conflict(
        ActiveDiagnosticConflict(
            conflict_id="config-version",
            statement="Runtime and lockfile report different versions.",
            evidence_refs=("artifact:runtime", "artifact:lockfile"),
        )
    )
    state.add_diagnostic_risk(
        ActiveDiagnosticRisk(
            risk_id="stale-validation",
            statement="Validation predates the latest mutation.",
            severity=ActiveDiagnosticRiskSeverity.HIGH,
            evidence_refs=("checkpoint:validation",),
            blocking=True,
        )
    )

    restored = RuntimeStateMetadata.model_validate(state.to_json_dict())

    assert restored.diagnostic_conflicts[0].status == ActiveDiagnosticItemStatus.OPEN
    assert restored.diagnostic_risks[0].blocking is True
    assert restored.diagnostic_decisions == []
    assert restored.diagnostic_progress_signature is None


def test_active_diagnostic_conflict_resolution_requires_new_evidence() -> None:
    with pytest.raises(ValueError, match="resolution evidence"):
        ActiveDiagnosticConflict(
            conflict_id="config-version",
            statement="Runtime and lockfile report different versions.",
            evidence_refs=("artifact:runtime", "artifact:lockfile"),
            status=ActiveDiagnosticItemStatus.RESOLVED,
        )


def test_active_diagnostic_decision_history_has_a_hard_append_limit() -> None:
    state = RuntimeStateMetadata(goal="Bound diagnostic history")
    signature = "sha256:" + "d" * 64
    for ordinal in range(1, 129):
        state.record_diagnostic_decision(
            ActiveDiagnosticDecision(
                decision_id=f"decision-{ordinal}",
                ordinal=ordinal,
                kind=ActiveDiagnosticDecisionKind.MEASURE,
                reason="Measure the next bounded fact.",
                need_type="file_read",
                question="Inspect one file.",
                state_signature=signature,
                evidence_changed=ordinal == 1,
            )
        )

    with pytest.raises(ValueError, match="history limit"):
        state.record_diagnostic_decision(
            ActiveDiagnosticDecision(
                decision_id="decision-129",
                ordinal=129,
                kind=ActiveDiagnosticDecisionKind.MEASURE,
                reason="Measure beyond the limit.",
                need_type="file_read",
                question="Inspect another file.",
                state_signature=signature,
                evidence_changed=False,
            )
        )
    assert len(RuntimeStateMetadata.model_validate(state.to_json_dict()).diagnostic_decisions) == 128


def test_active_diagnostic_evaluator_uses_non_compensatory_decision_hierarchy() -> None:
    evaluator = ActiveDiagnosticEvaluator()
    measure = DecisionNeedMetadata(
        need_type="file_read",
        question="Inspect configuration",
        target_path="config.toml",
        cost_hint="low",
    )
    act = DecisionNeedMetadata(
        need_type="file_write",
        question="Patch configuration",
        target_path="config.toml",
        attributes={"content": "enabled = true"},
        cost_hint="high",
    )
    verify = DecisionNeedMetadata(
        need_type="smoke_test",
        question="Run tests",
        command="pytest",
        cost_hint="low",
    )
    recover = DecisionNeedMetadata(
        need_type="repair",
        question="Repair the failed validation",
        command="pytest --lf",
        cost_hint="medium",
    )

    unknown_state = RuntimeStateMetadata(goal="Patch config")
    unknown_state.add_unknown("Which configuration is active?")
    selected = evaluator.choose(unknown_state, (act, measure))
    assert selected.need == measure
    assert selected.decision.kind == ActiveDiagnosticDecisionKind.MEASURE

    verify_state = RuntimeStateMetadata(
        goal="Patch config",
        phase=AgentPhase.VERIFY,
        modified_files=["config.toml"],
        verification_status="required",
    )
    selected = evaluator.choose(verify_state, (measure, verify, act))
    assert selected.need == verify
    assert selected.decision.kind == ActiveDiagnosticDecisionKind.VERIFY

    recover_state = RuntimeStateMetadata(
        goal="Patch config",
        phase=AgentPhase.RECOVER,
        modified_files=["config.toml"],
        verification_status="failed",
    )
    selected = evaluator.choose(recover_state, (measure, recover))
    assert selected.need == recover
    assert selected.decision.kind == ActiveDiagnosticDecisionKind.RECOVER

    blocked_state = RuntimeStateMetadata(goal="Patch config")
    blocked_state.add_diagnostic_risk(
        ActiveDiagnosticRisk(
            risk_id="indeterminate-write",
            statement="The prior write outcome is indeterminate.",
            severity=ActiveDiagnosticRiskSeverity.CRITICAL,
            evidence_refs=("checkpoint:write",),
            blocking=True,
        )
    )
    selected = evaluator.choose(blocked_state, (measure, act))
    assert selected.need is None
    assert selected.decision.kind == ActiveDiagnosticDecisionKind.STOP


def test_active_diagnostic_decision_explains_whether_evidence_changed() -> None:
    evaluator = ActiveDiagnosticEvaluator()
    state = RuntimeStateMetadata(goal="Inspect project")
    need = DecisionNeedMetadata(
        need_type="project_structure",
        question="Inspect project files",
        target_path=".",
    )

    first = evaluator.choose(state, (need,))
    second = evaluator.choose(state, (need,))
    state.add_fact("Project root exists.")
    third = evaluator.choose(state, (need,))

    assert first.decision.evidence_changed is True
    assert "Initial diagnostic state" in first.decision.reason
    assert second.decision.evidence_changed is False
    assert "No new evidence" in second.decision.reason
    assert third.decision.evidence_changed is True
    assert "New evidence" in third.decision.reason
    assert len(state.diagnostic_decisions) == 3


def test_content_change_resets_no_progress_even_when_collection_lengths_match() -> None:
    state = RuntimeStateMetadata(goal="Observe", known_facts=["old value"])
    updater = StateUpdater()
    before = updater._progress_signature(state)
    state.known_facts[0] = "new value"
    state.no_progress_rounds = 2

    updater._update_progress_stop_condition(state, before)

    assert state.no_progress_rounds == 0
    assert state.phase != AgentPhase.BLOCKED


@pytest.mark.parametrize(
    ("fault_point", "checkpoint_is_durable"),
    [
        (CheckpointFaultPoint.BEFORE_DURABLE_WRITE, False),
        (CheckpointFaultPoint.AFTER_DURABLE_WRITE, True),
    ],
)
def test_runtime_controller_has_typed_checkpoint_fault_injection_boundary(
    tmp_path,
    fault_point,
    checkpoint_is_durable,
) -> None:
    injected: list[tuple[CheckpointFaultPoint, CheckpointBoundary]] = []

    def inject(point, boundary, checkpoint):
        injected.append((point, boundary))
        if point == fault_point:
            raise KeyboardInterrupt(f"injected at {point.value}")

    store = RuntimeCheckpointStore(tmp_path)
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=None, session_id="session-1")
    controller = AgentRuntimeController(
        runtime,
        session_executor=SimpleNamespace(run=lambda *_args, **_kwargs: {"success": True}),
        checkpoint_store=store,
        checkpoint_fault_injector=inject,
    )
    controller.state = RuntimeStateMetadata(goal="Inspect project")
    controller._checkpointing_enabled = True
    controller._checkpoint_run_id = "run-1"
    controller._active_task_id = "task-1"
    controller._checkpoint_context = {"project_path": str(tmp_path), "cwd": str(tmp_path)}

    with pytest.raises(KeyboardInterrupt, match=fault_point.value):
        controller._persist_checkpoint(
            controller.state,
            reason="fault injection",
            safe_boundary=CheckpointBoundary.TASK_NORMALIZED,
        )

    assert (fault_point, CheckpointBoundary.TASK_NORMALIZED) in injected
    assert (store.load_latest("run-1") is not None) is checkpoint_is_durable


def test_runtime_state_migrates_legacy_read_only_assumption_to_typed_execution_mode() -> None:
    state = RuntimeStateMetadata.model_validate(
        {
            "goal": "Inspect the runtime",
            "assumptions": ["runtime_mode:read_only_analysis"],
        }
    )

    assert state.execution_mode == RuntimeExecutionMode.READ_ONLY
    assert state.execution_mode_source == RuntimeExecutionModeSource.LEGACY_ASSUMPTION
    assert autonomous_iteration.runtime_controller._state_is_read_only_analysis(state) is True


def test_apply_read_only_runtime_mode_sets_typed_root_policy() -> None:
    state = RuntimeStateMetadata(goal="Inspect the runtime")

    applied = autonomous_iteration.runtime_controller.apply_read_only_runtime_mode(
        state,
        state.goal,
        tags=["analysis"],
        task_type="analysis",
    )

    assert applied is True
    assert state.execution_mode == RuntimeExecutionMode.READ_ONLY
    assert state.execution_mode_source == RuntimeExecutionModeSource.ROOT_GOAL


def test_readme_auto_finalization_is_limited_to_explicit_project_creation_goals() -> None:
    from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot

    assert IntelligentAutopilot._should_auto_finalize_readme("修复 calculator.py 并运行测试") is False
    assert IntelligentAutopilot._should_auto_finalize_readme("Create a new calculator app project") is True


def test_tool_router_routes_information_gaps_and_blocks_on_budget() -> None:
    state = RuntimeStateMetadata(goal="Inspect project")
    router = ToolRouter()

    file_need = DecisionNeedMetadata(
        need_type="file_read",
        question="What does README say?",
        target_path="README.md",
    )
    directory_need = DecisionNeedMetadata(
        need_type="project_structure",
        question="What files exist?",
        target_path=".",
    )
    search_need = DecisionNeedMetadata(
        need_type="web_search",
        question="Find reference",
        query="agent runtime design",
    )
    command_need = DecisionNeedMetadata(
        need_type="smoke_test",
        question="Does it run?",
        command="pytest",
    )
    code_execution_command_need = DecisionNeedMetadata(
        need_type="code_execution",
        question="Run the generated script",
        command="python assistant.py",
    )
    bug_fix_need = DecisionNeedMetadata(
        need_type="bug_fix_tool",
        question="Fix failing smoke test",
        command="python test_assistant.py",
        attributes={"file_paths": ["assistant.py"], "max_iterations": 3},
    )

    assert router.route(state, file_need)[0].tool_name == "file_reader"
    directory_selection = router.route(state, directory_need)[0]
    assert directory_selection.tool_name == "multi_file_reader"
    assert directory_selection.input_metadata.pattern == "sketch.json"
    assert directory_selection.input_metadata.max_files == 1
    assert router.route(state, search_need)[0].tool_name == "web_searcher"
    assert router.route(state, command_need)[0].tool_name == "command_executor"
    assert router.route(state, code_execution_command_need)[0].tool_name == "command_executor"
    bug_fix_selection = router.route(state, bug_fix_need)[0]
    assert bug_fix_selection.tool_name == "bug_fix_tool"
    assert bug_fix_selection.input_metadata.max_iterations == 3
    assert [decision.selected_tool for decision in state.decision_history] == [
        "file_reader",
        "multi_file_reader",
        "web_searcher",
        "command_executor",
        "command_executor",
        "bug_fix_tool",
    ]

    state.budget.tool_calls_used = state.budget.max_tool_calls
    assert router.route(state, file_need) == []
    assert state.phase == AgentPhase.BLOCKED
    assert "budget exhausted" in state.completion_reason


def test_tool_router_blocks_mutation_tools_for_read_only_analysis_tasks() -> None:
    blocked_state = RuntimeStateMetadata(goal="请梳理 CLI 到运行时的核心链路")
    autonomous_iteration.runtime_controller.apply_read_only_runtime_mode(
        blocked_state,
        blocked_state.goal,
        tags=["readonly", "understanding"],
        task_type="analysis",
    )
    router = ToolRouter()

    blocked_selection = router.route(
        blocked_state,
        DecisionNeedMetadata(
            need_type="file_write",
            question="Write notes to README",
            target_path="README.md",
            attributes={"content": "summary"},
        ),
    )
    allowed_state = RuntimeStateMetadata(goal="请梳理 CLI 到运行时的核心链路")
    autonomous_iteration.runtime_controller.apply_read_only_runtime_mode(
        allowed_state,
        allowed_state.goal,
        tags=["readonly", "understanding"],
        task_type="analysis",
    )
    allowed_selection = router.route(
        allowed_state,
        DecisionNeedMetadata(
            need_type="project_structure",
            question="Inspect project structure",
            target_path=".",
        ),
    )

    assert blocked_selection == []
    assert blocked_state.phase == AgentPhase.BLOCKED
    assert "read-only analysis task" in (blocked_state.completion_reason or "").lower()
    assert allowed_selection
    assert allowed_selection[0].tool_name == "multi_file_reader"


def test_tool_router_blocks_mutating_commands_for_read_only_analysis_tasks() -> None:
    state = RuntimeStateMetadata(goal="Inspect runtime path handling")
    autonomous_iteration.runtime_controller.apply_read_only_runtime_mode(
        state,
        state.goal,
        tags=["readonly"],
        task_type="analysis",
    )
    router = ToolRouter()

    selections = router.route(
        state,
        DecisionNeedMetadata(
            need_type="command_check",
            question="Install a dependency and rerun",
            command="pip install rich",
        ),
    )

    assert selections == []
    assert state.phase == AgentPhase.BLOCKED
    assert "mutating shell commands" in (state.completion_reason or "")


def test_tool_router_blocks_ungrounded_file_read_for_read_only_analysis_without_project_context() -> None:
    state = RuntimeStateMetadata(goal="请梳理 CLI 到运行时的核心链路")
    autonomous_iteration.runtime_controller.apply_read_only_runtime_mode(
        state,
        state.goal,
        tags=["readonly", "understanding"],
        task_type="analysis",
    )
    router = ToolRouter()

    selections = router.route(
        state,
        DecisionNeedMetadata(
            need_type="file_read",
            question="Read likely setup file",
            target_path="setup.py",
        ),
    )

    assert selections == []
    assert state.phase == AgentPhase.BLOCKED
    assert "project_path" in (state.completion_reason or "")


def test_tool_router_routes_directory_shaped_file_reads_to_multi_file_reader(tmp_path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    readme = project_dir / "README.md"
    readme.write_text("hello", encoding="utf-8")
    router = ToolRouter()

    directory_state = RuntimeStateMetadata(goal="Inspect project")
    directory_need = DecisionNeedMetadata(
        need_type="file_read",
        question="What files and directories exist in the project folder?",
        target_path=str(project_dir),
    )
    directory_selection = router.route(directory_state, directory_need)[0]

    assert directory_selection.tool_name == "multi_file_reader"
    assert directory_selection.input_metadata.directory_path == str(project_dir)
    assert directory_selection.input_metadata.pattern == "sketch.json"
    assert directory_selection.input_metadata.max_files == 1

    file_state = RuntimeStateMetadata(goal="Inspect file")
    file_need = DecisionNeedMetadata(
        need_type="file_read",
        question="What does README say?",
        target_path=str(readme),
    )
    file_selection = router.route(file_state, file_need)[0]

    assert file_selection.tool_name == "file_reader"
    assert file_selection.input_metadata.file_path == str(readme)


def test_tool_router_routes_non_executable_file_creation_to_writer(tmp_path) -> None:
    router = ToolRouter()
    state = RuntimeStateMetadata(goal="Create project files")
    config_path = tmp_path / "config.json"
    readme_path = tmp_path / "README.md"

    config_selection = router.route(
        state,
        DecisionNeedMetadata(
            need_type="code_file_create",
            question="Create JSON configuration",
            target_path=str(config_path),
            attributes={"language": "json", "content": "{\"enabled\": true}\n"},
        ),
    )[0]
    readme_selection = router.route(
        state,
        DecisionNeedMetadata(
            need_type="code_file_create",
            question="Create README",
            target_path=str(readme_path),
            attributes={"language": "text"},
        ),
    )[0]

    assert config_selection.tool_name == "file_writer"
    assert config_selection.input_metadata.content == "{\"enabled\": true}\n"
    assert readme_selection.tool_name == "readme_tool"
    assert readme_selection.input_metadata.project_path == str(tmp_path)


def test_tool_router_replaces_existing_files_for_idempotent_generation(tmp_path) -> None:
    existing = tmp_path / "config.json"
    existing.write_text("{}", encoding="utf-8")
    router = ToolRouter()
    state = RuntimeStateMetadata(goal="Regenerate project files")

    selection = router.route(
        state,
        DecisionNeedMetadata(
            need_type="file_write",
            question="Write config",
            target_path=str(existing),
            operation_kind="create_file",
            attributes={"content": "{\"ok\": true}", "overwrite": True},
        ),
    )[0]

    assert selection.tool_name == "file_writer"
    assert selection.input_metadata.operation_kind == "file_replace"


def test_tool_router_routes_file_deletion_to_delete_tool(tmp_path) -> None:
    target = tmp_path / "obsolete.py"
    target.write_text("print('remove me')\n", encoding="utf-8")
    router = ToolRouter()
    state = RuntimeStateMetadata(goal="Remove obsolete file")

    selection = router.route(
        state,
        DecisionNeedMetadata(
            need_type="file_delete",
            question="Delete obsolete file after evidence read",
            target_path=str(target),
            operation_kind="delete_file",
        ),
    )[0]

    assert selection.tool_name == "file_delete_tool"
    assert selection.input_metadata.file_path == str(target)
    assert selection.input_metadata.operation_kind == "delete_file"
    assert selection.requires_confirmation is True


def test_tool_router_distinguishes_code_generation_and_symbol_edits() -> None:
    router = ToolRouter()
    state = RuntimeStateMetadata(goal="Patch code")

    add_selection = router.route(
        state,
        DecisionNeedMetadata(
            need_type="code_unit_generate",
            question="Add helper function",
            target_path="app.py",
            operation_kind="add_symbol",
            symbol_name="format_name",
            symbol_type="function",
        ),
    )[0]
    modify_selection = router.route(
        state,
        DecisionNeedMetadata(
            need_type="code_symbol_modify",
            question="Modify helper function",
            target_path="app.py",
            operation_kind="modify_symbol",
            symbol_name="format_name",
            symbol_type="function",
        ),
    )[0]
    patch_selection = router.route(
        state,
        DecisionNeedMetadata(
            need_type="file_write",
            question="Apply generated symbol edit",
            target_path="app.py",
            operation_kind="modify_symbol",
            symbol_name="format_name",
        ),
    )[0]
    create_selection = router.route(
        state,
        DecisionNeedMetadata(
            need_type="code_file_create",
            question="Create new app file",
            target_path="app.py",
            operation_kind="create_file",
        ),
    )[0]

    assert add_selection.tool_name == "code_unit_generator"
    assert add_selection.input_metadata.operation_kind == "add_symbol"
    assert modify_selection.tool_name == "code_editor"
    assert modify_selection.input_metadata.symbol_name == "format_name"
    assert patch_selection.tool_name == "file_patch_writer"
    assert create_selection.tool_name == "code_generator"


def test_runtime_guard_centralizes_risk_budget_and_confirmation_policy() -> None:
    guard = RuntimeGuard()
    state = RuntimeStateMetadata(goal="Risky work")
    high_risk_need = DecisionNeedMetadata(
        need_type="command_check",
        question="Run migration",
        command="python migrate.py",
        risk_level="high",
    )

    decision = guard.approve_need(state, high_risk_need, "command_executor")

    assert decision.approved is False
    assert decision.attributes["requires_user_confirmation"] is True
    assert "user confirmation" in decision.reason

    forbidden = DecisionNeedMetadata(
        need_type="command_check",
        question="Delete project",
        command="rm -rf .",
        risk_level="forbidden",
    )
    decision = guard.approve_need(state, forbidden, "command_executor")
    assert decision.approved is False
    assert "forbidden" in decision.reason


def test_tool_router_moves_high_risk_need_to_ask_user() -> None:
    state = RuntimeStateMetadata(goal="Risky work")
    router = ToolRouter()

    selections = router.route(
        state,
        DecisionNeedMetadata(
            need_type="command_check",
            question="Run migration",
            command="python migrate.py",
            risk_level="high",
        ),
    )

    assert selections == []
    assert state.phase == AgentPhase.ASK_USER
    assert "user confirmation" in state.completion_reason


def test_file_selector_promotes_only_evidence_backed_candidates() -> None:
    state = RuntimeStateMetadata(goal="Patch bug")
    selector = FileSelector()
    state.add_candidate_file("app.py", "traceback references app.py")

    selected = selector.select(state, ["app.py", "guess.py"])

    assert selected == ["app.py"]
    assert state.selected_files["app.py"] == ["traceback references app.py"]
    assert state.unknowns == ["Missing file-selection evidence for guess.py"]


def test_edit_guard_requires_evidence_selection_scope_and_verification() -> None:
    guard = EditGuard()
    state = RuntimeStateMetadata(goal="Patch bug")
    state.add_candidate_file("app.py", "traceback points here")

    no_evidence = EditPlanMetadata(subgoal="Patch", target_files=["app.py"])
    decision = guard.approve(state, no_evidence)
    assert decision.approved is False
    assert "evidence" in decision.reason.lower()

    unselected = EditPlanMetadata(
        subgoal="Patch",
        target_files=["app.py"],
        evidence=["traceback points here"],
        allowed_changes=["Fix failing branch"],
        verification=["pytest"],
    )
    decision = guard.approve(state, unselected)
    assert decision.approved is False
    assert decision.blocked_files == ["app.py"]

    state.select_file("app.py", "traceback points here")
    too_many_files = EditPlanMetadata(
        subgoal="Patch",
        target_files=["app.py", "b.py", "c.py", "d.py"],
        evidence=["traceback points here"],
        allowed_changes=["Fix failing branch"],
        verification=["pytest"],
    )
    decision = guard.approve(state, too_many_files)
    assert decision.approved is False
    assert "budget" in decision.reason.lower()

    approved = EditPlanMetadata(
        subgoal="Patch",
        target_files=["app.py"],
        evidence=["traceback points here"],
        allowed_changes=["Fix failing branch"],
        forbidden_changes=["Do not change public CLI"],
        verification=["pytest"],
    )
    decision = guard.approve(state, approved)
    assert decision.approved is True


def test_file_creation_uses_a_separate_runtime_budget(tmp_path) -> None:
    state = RuntimeStateMetadata(goal="Scaffold project")
    state.budget.file_edits_used = state.budget.max_file_edits
    router = ToolRouter()
    new_file = tmp_path / "new_module.py"

    selections = router.route(
        state,
        DecisionNeedMetadata(
            need_type="file_write",
            question="Create module",
            target_path=str(new_file),
            operation_kind="create_file",
            attributes={"content": "VALUE = 1\n"},
        ),
    )

    assert len(selections) == 1
    assert selections[0].input_metadata.operation_kind == "create_file"

    new_file.write_text("VALUE = 0\n", encoding="utf-8")
    selections = router.route(
        state,
        DecisionNeedMetadata(
            need_type="file_write",
            question="Replace module",
            target_path=str(new_file),
            operation_kind="create_file",
            attributes={"content": "VALUE = 2\n"},
        ),
    )

    assert selections == []


def test_edit_guard_limits_creates_independently_from_edits() -> None:
    guard = EditGuard()
    state = RuntimeStateMetadata(goal="Scaffold project")
    state.budget.file_edits_used = state.budget.max_file_edits
    state.select_file("new_module.py", "scaffold plan")
    create_plan = EditPlanMetadata(
        subgoal="Create module",
        target_files=["new_module.py"],
        evidence=["scaffold plan"],
        allowed_changes=["Create requested module"],
        forbidden_changes=["Do not modify existing files"],
        verification=["python -m compileall ."],
        attributes={"budget_kind": "file_create"},
    )

    assert guard.approve(state, create_plan).approved is True

    state.budget.file_creates_used = state.budget.max_file_creates
    decision = guard.approve(state, create_plan)
    assert decision.approved is False
    assert "create budget" in decision.reason.lower()


def test_state_updater_absorbs_tool_results_and_forces_write_verification() -> None:
    state = RuntimeStateMetadata(goal="Write file", phase=AgentPhase.EXECUTE)
    router = ToolRouter()
    updater = StateUpdater()
    selection = router.route(
        state,
        DecisionNeedMetadata(
            need_type="file_write",
            question="Save app",
            target_path="app.py",
            attributes={"content": "print('ok')"},
        ),
    )[0]
    result = SimpleNamespace(
        success=True,
        output_metadata=ToolResultMetadata(
            tool_name="file_writer",
            status=ResultStatus.SUCCESS,
            result=TextArtifactMetadata(content="written", attributes={"file_path": "app.py"}),
        ),
        error=None,
    )

    updater.apply_tool_result(state, selection, result)

    assert state.phase == AgentPhase.VERIFY
    assert state.verification_status == "required"
    assert "app.py" in state.modified_files
    assert state.budget.file_edits_used == 0
    assert state.budget.file_creates_used == 1
    assert state.tool_history[0]["tool_name"] == "file_writer"


def test_state_updater_moves_successful_verification_to_summary() -> None:
    state = RuntimeStateMetadata(goal="Verify", phase=AgentPhase.VERIFY, modified_files=["app.py"], verification_status="required")
    router = ToolRouter()
    updater = StateUpdater()
    selection = router.route(
        state,
        DecisionNeedMetadata(
            need_type="smoke_test",
            question="Run tests",
            command="pytest",
        ),
    )[0]
    result = SimpleNamespace(
        success=True,
        output_metadata=ToolResultMetadata(
            tool_name="command_executor",
            status=ResultStatus.SUCCESS,
            result=TextArtifactMetadata(content="passed"),
        ),
        error=None,
    )

    updater.apply_tool_result(state, selection, result)

    assert state.phase == AgentPhase.SUMMARIZE
    assert state.verification_status == "passed"
    assert state.completion_reason == "verification passed"


def test_state_updater_replans_after_failed_verification() -> None:
    state = RuntimeStateMetadata(goal="Verify", phase=AgentPhase.VERIFY, modified_files=["app.py"], verification_status="required")
    router = ToolRouter()
    updater = StateUpdater()
    selection = router.route(
        state,
        DecisionNeedMetadata(
            need_type="smoke_test",
            question="Run tests",
            command="pytest",
        ),
    )[0]
    result = SimpleNamespace(
        success=False,
        output_metadata=None,
        error=SimpleNamespace(error_message="pytest failed"),
    )

    updater.apply_tool_result(state, selection, result)

    assert state.phase == AgentPhase.REPLAN
    assert state.verification_status == "failed"
    assert state.replan_count == 1
    assert state.budget.replan_rounds_used == 1
    assert state.diagnostic_risks[0].severity == ActiveDiagnosticRiskSeverity.HIGH
    assert state.diagnostic_risks[0].status == ActiveDiagnosticItemStatus.OPEN


def test_state_updater_blocks_after_repeated_no_progress_results() -> None:
    state = RuntimeStateMetadata(goal="Observe")
    updater = StateUpdater()
    selection = SimpleNamespace(
        tool_name="noop_tool",
        step_id="noop",
        reason="observe",
        input_metadata=SimpleNamespace(to_params=lambda: {}),
    )
    result = SimpleNamespace(success=True, output_metadata=None, error=None)

    updater.apply_tool_result(state, selection, result)
    updater.apply_tool_result(state, selection, result)
    updater.apply_tool_result(state, selection, result)

    assert state.phase == AgentPhase.BLOCKED
    assert state.completion_reason == "no new runtime facts after repeated tool results"


def test_runtime_controller_streamed_need_interrupt_routes_and_resumes_state() -> None:
    runtime = SimpleNamespace(tool_registry=None)
    controller = AgentRuntimeController(runtime, session_executor=SimpleNamespace(run=lambda *_args, **_kwargs: {"success": True}))

    selections = controller.handle_streamed_need(
        DecisionNeedMetadata(
            need_type="file_read",
            question="Inspect app",
            target_path="app.py",
        )
    )

    assert selections[0].tool_name == "file_reader"
    assert controller.state.tool_history[0]["event_type"] == "stream_need_interrupt"

    result = SimpleNamespace(
        success=True,
        output_metadata=ToolResultMetadata(
            tool_name="file_reader",
            status=ResultStatus.SUCCESS,
            result=TextArtifactMetadata(content="app", attributes={"file_path": "app.py"}),
        ),
        error=None,
    )
    state = controller.absorb_streamed_tool_result(selections[0], result)

    assert "app.py" in state.candidate_files
    assert state.tool_history[-1]["event_type"] == "stream_need_resume"


def test_runtime_reporter_summarizes_evidence_changes_and_risks() -> None:
    state = RuntimeStateMetadata(goal="Patch bug", phase=AgentPhase.BLOCKED, verification_status="failed")
    state.add_fact("Observed failing test")
    state.add_unknown("Need failure diagnosis")
    state.select_file("app.py", "test failure references app.py")
    state.add_modified_file("app.py")
    state.completion_reason = "verification failed"
    state.add_diagnostic_risk(
        ActiveDiagnosticRisk(
            risk_id="failed-validation",
            statement="Latest verification failed.",
            severity=ActiveDiagnosticRiskSeverity.HIGH,
            evidence_refs=("tool:pytest",),
            blocking=True,
        )
    )

    report = RuntimeReporter().report(state)

    assert report.goal == "Patch bug"
    assert report.phase == AgentPhase.BLOCKED
    assert report.selected_files == {"app.py": ["test failure references app.py"]}
    assert report.modified_files == ["app.py"]
    assert "unresolved runtime questions remain" in report.residual_risks
    assert "verification status is failed" in report.residual_risks
    assert report.diagnostic_risks == state.diagnostic_risks
    assert "open diagnostic risk: Latest verification failed." in report.residual_risks


def test_agent_runtime_controller_returns_runtime_state_and_report() -> None:
    class FakeSessionRunner:
        def __init__(self) -> None:
            self.calls = []

        def run(self, goal, context, mode="standard"):
            self.calls.append((goal, context, mode))
            return {"success": True, "stats": {"tasks_completed": 1, "tasks_failed": 0}, "written_files": ["app.py"]}

    session_executor = FakeSessionRunner()
    runtime = SimpleNamespace(tool_registry=None)
    controller = AgentRuntimeController(runtime, session_executor=session_executor)

    result = controller.run("Build app", {"project_path": "/tmp/project"}, mode="standard")

    assert result["success"] is True
    assert session_executor.calls == [("Build app", {"project_path": "/tmp/project"}, "standard")]
    state = result["agent_runtime_state"]
    assert state["goal"] == "Build app"
    assert state["phase"] == "summarize"
    assert state["modified_files"] == ["app.py"]
    assert result["runtime_report"]["goal"] == "Build app"
    assert result["runtime_report"]["modified_files"] == ["app.py"]


def test_response_evidence_task_has_no_core_success_or_runtime_report() -> None:
    class FakeSessionRunner:
        def run(self, goal, context, mode="standard"):
            return {
                "success": True,
                "core_success": None,
                "stats": {"tasks_completed": 1, "tasks_failed": 0},
                "written_files": [],
            }

    controller = AgentRuntimeController(
        SimpleNamespace(tool_registry=None),
        session_executor=FakeSessionRunner(),
    )

    result = controller.run(
        "Inspect repository",
        {
            "project_path": "/tmp/project",
            "task_purpose": RuntimeTaskPurpose.RESPONSE_EVIDENCE,
        },
    )

    assert result["success"] is True
    assert result["core_success"] is None
    assert "runtime_report" not in result
    assert result["agent_runtime_state"]["task_purpose"] == "response_evidence"
    assert result["agent_runtime_state"]["execution_mode"] == "read_only"
    assert result["agent_runtime_state"]["project_improvement_policy"]["requirement"] == "disabled"


def test_response_evidence_runtime_attaches_prepared_checkpoint_generation(tmp_path) -> None:
    class FakeSessionRunner:
        def run(self, goal, context, mode="standard"):
            return {"success": True, "core_success": None, "written_files": []}

    run_id = "evidence-run"
    task_id = "evidence-task"
    state = RuntimeStateMetadata(
        goal="Inspect repository",
        task_purpose=RuntimeTaskPurpose.RESPONSE_EVIDENCE,
        execution_mode=RuntimeExecutionMode.READ_ONLY,
        execution_mode_source=RuntimeExecutionModeSource.ROOT_TASK_CARD,
        core_success=None,
    )
    checkpoint = RuntimeCheckpointMetadata(
        checkpoint_id="evidence-initial",
        generation=1,
        run_id=run_id,
        root_task_id=task_id,
        session_id=run_id,
        checkpoint_reason="evidence task materialized",
        safe_boundary=CheckpointBoundary.DECOMPOSITION_RECORDED,
        runtime_state=state,
        mutation_class="read_only",
        project_fingerprint=ProjectFingerprint(project_root=str(tmp_path)),
    )
    store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    checkpoint = store.save(checkpoint, expected_generation=0)
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=None, session_id=run_id)
    controller = AgentRuntimeController(
        runtime,
        session_executor=FakeSessionRunner(),
        checkpoint_store=store,
    )

    result = controller.run(
        state.goal,
        {
            "task_id": task_id,
            "run_id": run_id,
            "project_path": str(tmp_path),
            "checkpointing_enabled": True,
            "prepared_task_checkpoint_id": checkpoint.checkpoint_id,
            "task_purpose": RuntimeTaskPurpose.RESPONSE_EVIDENCE,
        },
    )

    assert result["success"] is True
    assert result["core_success"] is None
    latest = store.load_latest(run_id)
    assert latest is not None
    assert latest.generation == 2
    assert latest.root_task_id == task_id
    assert latest.runtime_state.task_purpose == RuntimeTaskPurpose.RESPONSE_EVIDENCE


def test_response_evidence_controller_binds_bridge_after_applied_tool_checkpoint(tmp_path) -> None:
    class Bridge:
        def __init__(self) -> None:
            self.observed = []
            self.bound = []

        def observe(self, selection, execution_result):
            self.observed.append((selection, execution_result))
            return SimpleNamespace(marker="evidence:artifact:sha256:" + "a" * 64)

        def bind_checkpoint(self, pending, checkpoint):
            self.bound.append((pending, checkpoint))

    bridge = Bridge()
    store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=None,
        session_id="evidence-run",
    )
    controller = AgentRuntimeController(
        runtime,
        checkpoint_store=store,
        evidence_bridge=bridge,
    )
    controller.state = RuntimeStateMetadata(
        goal="Collect response evidence",
        task_purpose=RuntimeTaskPurpose.RESPONSE_EVIDENCE,
        execution_mode=RuntimeExecutionMode.READ_ONLY,
        execution_mode_source=RuntimeExecutionModeSource.ROOT_TASK_CARD,
        core_success=None,
    )
    controller._checkpointing_enabled = True
    controller._checkpoint_run_id = "evidence-run"
    controller._active_task_id = "evidence-task"
    controller._checkpoint_context = {"project_path": str(tmp_path), "cwd": str(tmp_path)}
    selection = ToolSelection(
        step_id="evidence-step",
        tool_name="multi_file_reader",
        reason=SelectionReason.CAPABILITY_MATCH,
        confidence=1.0,
        input_metadata=ToolInputMetadata.from_mapping(
            "multi_file_reader",
            {
                "directory_path": str(tmp_path),
                "obligation_id": "ground:claim-1",
                "source_class": "project",
                "read_only": True,
            },
        ),
    )
    execution_result = SimpleNamespace(
        success=True,
        output_metadata=ToolResultMetadata(
            tool_name="multi_file_reader",
            status=ResultStatus.SUCCESS,
            result=FileArtifactMetadata(file_path=str(tmp_path), content="evidence"),
        ),
        error=None,
    )

    controller._handle_tool_result_applied(controller.state, selection, execution_result)

    latest = store.load_latest("evidence-run")
    assert latest is not None
    assert latest.safe_boundary == CheckpointBoundary.TOOL_RESULT_APPLIED
    assert latest.tool_name == "multi_file_reader"
    assert bridge.observed == [(selection, execution_result)]
    assert bridge.bound[0][1] == latest
    assert bridge.bound[0][0].marker in latest.runtime_state.known_facts


def test_runtime_controller_persists_read_only_safe_boundaries_and_mirrors_events(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path / "diagnostics"))
    hooks.on_task_received(
        task_id="root-checkpoint-task",
        source="test",
        raw_input="Inspect project",
        session_id="checkpoint-session",
    )
    run = hooks.recorder.load_run("root-checkpoint-task")
    assert run is not None
    store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)

    class InspectingSessionRunner:
        def run(self, goal, context, mode="standard"):
            current = store.load_latest(run.run_id)
            assert current is not None
            assert current.safe_boundary == "task_normalized"
            assert current.runtime_state.phase == AgentPhase.UNDERSTAND_PROJECT
            selections = controller.handle_streamed_need(
                DecisionNeedMetadata(
                    need_type="file_read",
                    question="Inspect README",
                    target_path=str(project / "README.md"),
                )
            )
            controller.absorb_streamed_tool_result(
                selections[0],
                SimpleNamespace(
                    success=True,
                    output_metadata=ToolResultMetadata(
                        tool_name="file_reader",
                        status=ResultStatus.SUCCESS,
                        result=TextArtifactMetadata(content="project", attributes={"file_path": "README.md"}),
                    ),
                    error=None,
                ),
            )
            applied = store.load_latest(run.run_id)
            assert applied is not None
            assert applied.safe_boundary == "tool_result_applied"
            assert applied.runtime_state.budget.file_reads_used == 1
            return {"success": True, "stats": {"tasks_completed": 1, "tasks_failed": 0}}

    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("project", encoding="utf-8")
    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=hooks,
        session_id="checkpoint-session",
    )
    controller = AgentRuntimeController(runtime, session_executor=InspectingSessionRunner(), checkpoint_store=store)

    result = controller.run(
        "Inspect project",
        {
            "task_id": "root-checkpoint-task",
            "project_path": str(project),
            "tags": ["readonly"],
            "task_type": "analysis",
            "checkpointing_enabled": True,
        },
    )

    latest = store.load_latest(run.run_id)
    assert latest is not None
    assert latest.safe_boundary == "runtime_finalized"
    assert latest.finalization_cursor is not None
    assert latest.finalization_cursor.stage == RuntimeFinalizationStage.RUN_FINALIZED
    assert latest.generation == 5
    assert latest.runtime_state.phase == AgentPhase.SUMMARIZE
    assert result["checkpoint_status"] == "durable"
    events = hooks.recorder.load_trajectory_events(run.run_id)
    checkpoint_events = [event for event in events if event["event_type"] == "checkpoint_created"]
    assert [event["payload"]["safe_boundary"] for event in checkpoint_events] == [
        "task_normalized",
        "tool_result_applied",
        "runtime_state_completed",
        "runtime_report_persisted",
        "runtime_finalized",
    ]


def test_runtime_controller_checkpoint_persists_ingress_snapshot(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path / "diagnostics"))
    hooks.on_task_received(
        task_id="ingress-checkpoint-task",
        source="test",
        raw_input="Inspect project",
        session_id="run-1",
    )
    run = hooks.recorder.load_run("ingress-checkpoint-task")
    assert run is not None
    store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)
    project = tmp_path / "project"
    project.mkdir()
    identity = ConversationIdentity(
        conversation_id="conversation-1",
        run_id="run-1",
        turn_index=1,
        project_root=str(project),
    )
    ingress = SessionIngressState(
        identity=identity,
        turns=[SessionTurn(identity=identity, message_id="message-1", role="user", content="Inspect project")],
        session_constraints=SessionConstraintState(
            session_id="conversation-1", project_root=str(project), processed_through_turn=1
        ),
    )
    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=hooks,
        session_id="run-1",
    )
    controller = AgentRuntimeController(
        runtime,
        session_executor=SimpleNamespace(run=lambda *args, **kwargs: {"success": True}),
        checkpoint_store=store,
    )

    result = controller.run(
        "Inspect project",
        {
            "task_id": "ingress-checkpoint-task",
            "project_path": str(project),
            "checkpointing_enabled": True,
            "conversation_id": "conversation-1",
            "run_id": "run-1",
            "session_ingress_state": ingress,
        },
    )

    latest = store.load_latest(run.run_id)
    assert result["success"] is True
    assert latest is not None
    assert latest.session_ingress_state is not None
    assert latest.session_ingress_state.turns[0].message_id == "message-1"


@pytest.mark.parametrize(
    ("context_update", "message"),
    [
        ({"conversation_id": "other-conversation"}, "conversation identity"),
        ({"project_path": "other-project"}, "project identity"),
    ],
)
def test_runtime_controller_rejects_raw_ingress_identity_conflicts_without_constraints(
    tmp_path,
    context_update: dict[str, str],
    message: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    ingress = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-1",
            turn_index=0,
            project_root=str(project),
        )
    )
    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=None,
        session_id="run-1",
    )
    controller = AgentRuntimeController(
        runtime,
        session_executor=SimpleNamespace(run=lambda *_args, **_kwargs: {"success": True}),
    )
    context = {
        "conversation_id": ingress.identity.conversation_id,
        "project_path": ingress.identity.project_root,
        "session_ingress_state": ingress,
        **context_update,
    }

    with pytest.raises(ValueError, match=message):
        controller.run("Inspect project", context)


def test_runtime_controller_checkpoint_failure_degrades_without_failing_task(tmp_path) -> None:
    class FailingStore:
        def save(self, *_args, **_kwargs):
            raise OSError("disk unavailable")

    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path / "diagnostics"))
    hooks.on_task_received(
        task_id="checkpoint-failure-task",
        source="test",
        raw_input="Inspect project",
        session_id="checkpoint-failure-session",
    )
    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=hooks,
        session_id="checkpoint-failure-session",
    )
    controller = AgentRuntimeController(
        runtime,
        session_executor=SimpleNamespace(run=lambda *_args, **_kwargs: {"success": True}),
        checkpoint_store=FailingStore(),
    )

    result = controller.run(
        "Inspect project",
        {
            "task_id": "checkpoint-failure-task",
            "project_path": str(tmp_path),
            "tags": ["readonly"],
            "task_type": "analysis",
            "checkpointing_enabled": True,
        },
    )

    assert result["success"] is True
    assert result["checkpoint_status"] == "unavailable"
    run = hooks.recorder.load_run("checkpoint-failure-task")
    assert run is not None
    events = hooks.recorder.load_trajectory_events(run.run_id)
    assert any(event["event_type"] == "checkpoint_write_failed" for event in events)


def _seed_resume_checkpoint(
    tmp_path,
    *,
    safe_boundary="task_normalized",
    phase=AgentPhase.UNDERSTAND_PROJECT,
    recovery_rounds_used=0,
    session_cursor: SessionExecutionCursor | None = None,
):
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path / "diagnostics"))
    hooks.on_task_received(
        task_id="resume-root-task",
        source="test",
        raw_input="Inspect project",
        session_id="original-session",
    )
    run = hooks.recorder.load_run("resume-root-task")
    assert run is not None
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    state = RuntimeStateMetadata(goal="Inspect project", phase=phase)
    state.add_assumption("runtime_mode:read_only_analysis")
    state.budget.tool_calls_used = 2
    state.budget.file_reads_used = 2
    state.budget.recovery_rounds_used = recovery_rounds_used
    if phase == AgentPhase.SUMMARIZE:
        state.completion_reason = "runtime session completed"
    store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)
    saved = store.save(
        RuntimeCheckpointMetadata(
            checkpoint_id="resume-checkpoint",
            generation=1,
            run_id=run.run_id,
            root_task_id="resume-root-task",
            session_id="original-session",
            checkpoint_reason="failure injection",
            safe_boundary=safe_boundary,
            runtime_state=state,
            side_effect_state="applied" if safe_boundary == "tool_result_applied" else "none",
            mutation_class="read_only" if safe_boundary == "tool_result_applied" else "none",
            session_cursor=session_cursor,
            project_fingerprint=ProjectFingerprint(project_root=str(project), cwd=str(project)),
        )
    )
    return hooks, store, run, project, saved


def _session_cursor(*, next_task_index: int = 1) -> SessionExecutionCursor:
    original = TaskGraphNodeMetadata(task_id="root", description="Inspect and fix project")
    tasks = [
        TaskGraphNodeMetadata(task_id="inspect", description="Inspect project"),
        TaskGraphNodeMetadata(task_id="fix", description="Fix project", dependencies=["inspect"]),
    ]
    order = ["inspect", "fix"]
    return SessionExecutionCursor(
        stage=SessionStage.TASK_EXECUTION,
        plan_hash=autonomous_iteration.runtime_controller._RuntimeSessionExecutor._session_plan_hash(
            original,
            tasks,
            order,
        ),
        semantic=SessionSemanticSnapshot(task_type="coding", risk_level="low", confidence=0.9),
        original_task=original,
        tasks=tasks,
        execution_order=order,
        next_task_index=next_task_index,
        results=[SessionTaskResult(task_id="inspect", status="completed")]
        if next_task_index
        else [],
    )


def test_runtime_resume_replays_checkpointed_prompt_context_without_reselection(tmp_path) -> None:
    class ContextProcessExit(BaseException):
        pass

    class ExactCounter:
        available = True
        tokenizer_id = "checkpoint-test-tokenizer"
        model = "checkpoint-test-model"

        @staticmethod
        def count_text(text: str) -> int:
            return len(text.encode("utf-8"))

    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path / "diagnostics"))
    hooks.on_task_received(
        task_id="context-resume-task",
        source="test",
        raw_input="Inspect project",
        session_id="context-session",
    )
    run = hooks.recorder.load_run("context-resume-task")
    assert run is not None
    project = tmp_path / "project"
    project.mkdir()
    short_memory = ShortMemory(repo_path=project)
    for index in range(8):
        short_memory.add_message(
            "assistant",
            f"original checkpointed prompt evidence-{index}-" + (str(index) * 100),
        )
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "memory"),
        token_counter=ExactCounter(),
        max_prompt_tokens=700,
    )
    store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)
    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=hooks,
        memory_context_builder=builder,
        session_id="context-session",
    )

    class CrashingRunner:
        def run(self, goal, context, mode="standard"):
            controller._persist_session_cursor(_session_cursor(next_task_index=0))
            builder.build(
                "stable context request",
                project_path=project,
                include_environment=False,
                system_prompt="fixed system",
            )
            assert controller._prompt_context_replay_enabled is False
            raise ContextProcessExit("after context checkpoint")

    controller = AgentRuntimeController(runtime, session_executor=CrashingRunner(), checkpoint_store=store)
    with pytest.raises(ContextProcessExit, match="after context checkpoint"):
        controller.run(
            "Inspect project",
            {
                "task_id": "context-resume-task",
                "project_path": str(project),
                "tags": ["readonly"],
                "task_type": "analysis",
                "checkpointing_enabled": True,
            },
        )
    controller._release_run_lease()
    interrupted = store.load_latest(run.run_id)
    assert interrupted is not None
    assert interrupted.safe_boundary == CheckpointBoundary.CONTEXT_ASSEMBLED
    assert interrupted.prompt_context_snapshot is not None
    assert interrupted.prompt_context_snapshot.compaction_bindings

    replacement_memory = ShortMemory(repo_path=project)
    replacement_memory.add_message("user", "changed source after process exit")
    replacement_builder = MemoryContextBuilder(
        short_memory=replacement_memory,
        memory_store=MemoryStore(tmp_path / "replacement-memory"),
        token_counter=ExactCounter(),
        max_prompt_tokens=700,
    )
    replayed: list[dict] = []

    class ResumeRunner:
        supports_session_cursor = True

        def run(self, goal, context, mode="standard", resume_cursor=None):
            assert resume_cursor is not None
            replayed.append(
                replacement_builder.build(
                    "stable context request",
                    project_path=project,
                    include_environment=False,
                    system_prompt="fixed system",
                )
            )
            return {"success": True}

    replacement = AgentRuntimeController(
        SimpleNamespace(
            tool_registry=None,
            runtime_diagnostics_hooks=hooks,
            memory_context_builder=replacement_builder,
            session_id="replacement-session",
        ),
        session_executor=ResumeRunner(),
        checkpoint_store=store,
    )
    result = replacement.resume(
        run.run_id,
        interrupted.checkpoint_id,
        {"project_path": str(project), "task_id": "context-resume-task"},
    )

    assert result["success"] is True
    assert len(replayed) == 1
    assert "original checkpointed prompt evidence" in replayed[0]["prompt_text"]
    assert "changed source after process exit" not in replayed[0]["prompt_text"]
    assert replayed[0]["context_compactions"]
    assert replayed[0]["context_selection"]["budget_unit"] == "tokens"
    assert replayed[0]["context_selection"]["tokenizer_id"] == "checkpoint-test-tokenizer"


def test_runtime_resume_blocks_when_prompt_context_artifact_is_corrupt(tmp_path) -> None:
    cursor = _session_cursor(next_task_index=0)
    hooks, store, run, project, saved = _seed_resume_checkpoint(
        tmp_path,
        safe_boundary=CheckpointBoundary.DECOMPOSITION_RECORDED,
        session_cursor=cursor,
    )
    payload = {
        "query": "stable context",
        "project_path": str(project),
        "system_prompt": "",
        "dialog_context": [],
        "related_memories": [],
        "related_files": [],
        "environment_context": [],
        "prompt_text": "stable prompt",
        "context_selection": ContextSelectionMetadata(
            max_prompt_chars=100,
            original_prompt_chars=13,
            final_prompt_chars=13,
        ).to_json_dict(),
    }
    reference = store.save_recovery_artifact(
        run.run_id,
        kind="prompt_context",
        payload=payload,
    )
    snapshot = RuntimePromptContextSnapshot(
        context_id="corrupt-context",
        request_hash="sha256:" + "a" * 64,
        prompt_hash=f"sha256:{hashlib.sha256(b'stable prompt').hexdigest()}",
        selection=ContextSelectionMetadata.model_validate(payload["context_selection"]),
        context_artifact=reference,
    )
    context_checkpoint = store.save(
        saved.model_copy(
            update={
                "checkpoint_id": "context-checkpoint",
                "generation": 2,
                "safe_boundary": CheckpointBoundary.CONTEXT_ASSEMBLED,
                "prompt_context_snapshot": snapshot,
                "integrity_checksum": "",
            }
        ),
        expected_generation=1,
    )
    artifact_path = store.root_dir / run.run_id / "recovery_artifacts" / f"{reference.artifact_id}.json"
    artifact_path.write_text("{}", encoding="utf-8")

    result = AgentRuntimeController(
        SimpleNamespace(
            tool_registry=None,
            runtime_diagnostics_hooks=hooks,
            session_id="replacement-session",
        ),
        session_executor=SimpleNamespace(
            supports_session_cursor=True,
            run=lambda *_args, **_kwargs: pytest.fail("corrupt context must block session replay"),
        ),
        checkpoint_store=store,
    ).resume(
        run.run_id,
        context_checkpoint.checkpoint_id,
        {"project_path": str(project), "task_id": "resume-root-task"},
    )

    assert result["success"] is False
    assert result["resume_decision"]["recoverability"] == "not_recoverable"
    assert result["resume_decision"]["reason_code"] == "checkpoint_corrupt"


def test_runtime_resume_fails_closed_when_v4_prompt_snapshot_does_not_match_v5_request(
    tmp_path,
) -> None:
    cursor = _session_cursor(next_task_index=0)
    hooks, store, run, project, saved = _seed_resume_checkpoint(
        tmp_path,
        safe_boundary=CheckpointBoundary.DECOMPOSITION_RECORDED,
        session_cursor=cursor,
    )
    query = "stable context"
    system_prompt = "fixed system"
    builder = MemoryContextBuilder(
        short_memory=ShortMemory(repo_path=project),
        memory_store=MemoryStore(tmp_path / "changed-memory"),
    )
    builder.short_memory.add_message("user", "changed memory must never be rebuilt")
    v4_request_hash = builder.context_assembler.request_hash(
        {
            "adapter_version": "typed_memory_candidates_quality_v4",
            "query": query,
            "project_path": str(project),
            "include_environment": False,
            "limit": 10,
            "system_prompt": system_prompt,
        },
        max_prompt_chars=builder.max_prompt_chars,
        max_prompt_tokens=builder.max_prompt_tokens,
    )
    prompt_text = "checkpointed v4 prompt"
    selection = ContextSelectionMetadata(
        max_prompt_chars=builder.max_prompt_chars,
        original_prompt_chars=len(prompt_text),
        final_prompt_chars=len(prompt_text),
    )
    payload = {
        "query": query,
        "project_path": str(project),
        "system_prompt": system_prompt,
        "dialog_context": [],
        "related_memories": [],
        "related_files": [],
        "environment_context": [],
        "context_compactions": [],
        "prompt_text": prompt_text,
        "context_selection": selection.to_json_dict(),
    }
    reference = store.save_recovery_artifact(
        run.run_id,
        kind="prompt_context",
        payload=payload,
    )
    snapshot = RuntimePromptContextSnapshot(
        context_id="v4-context",
        request_hash=v4_request_hash,
        prompt_hash=f"sha256:{hashlib.sha256(prompt_text.encode('utf-8')).hexdigest()}",
        selection=selection,
        context_artifact=reference,
    )
    context_checkpoint = store.save(
        saved.model_copy(
            update={
                "checkpoint_id": "v4-context-checkpoint",
                "generation": 2,
                "safe_boundary": CheckpointBoundary.CONTEXT_ASSEMBLED,
                "prompt_context_snapshot": snapshot,
                "integrity_checksum": "",
            }
        ),
        expected_generation=1,
    )
    provider_calls: list[str] = []

    class ResumeRunner:
        supports_session_cursor = True

        def run(self, goal, context, mode="standard", resume_cursor=None):
            assert resume_cursor is not None
            builder.build(
                query,
                project_path=project,
                include_environment=False,
                system_prompt=system_prompt,
            )
            provider_calls.append("provider")
            return {"success": True}

    controller = AgentRuntimeController(
        SimpleNamespace(
            tool_registry=None,
            runtime_diagnostics_hooks=hooks,
            memory_context_builder=builder,
            session_id="replacement-session",
        ),
        session_executor=ResumeRunner(),
        checkpoint_store=store,
    )

    with pytest.raises(RuntimeError, match="prompt context.*request|request.*prompt context"):
        controller.resume(
            run.run_id,
            context_checkpoint.checkpoint_id,
            {"project_path": str(project), "task_id": "resume-root-task"},
        )

    assert provider_calls == []


@pytest.mark.parametrize(
    "algorithm",
    [
        "deterministic_dialog_extract_v1",
        "deterministic_observation_mask_v1",
    ],
)
def test_runtime_resume_blocks_when_context_compaction_artifact_is_corrupt(
    tmp_path,
    algorithm: str,
) -> None:
    cursor = _session_cursor(next_task_index=0)
    hooks, store, run, project, saved = _seed_resume_checkpoint(
        tmp_path,
        safe_boundary=CheckpointBoundary.DECOMPOSITION_RECORDED,
        session_cursor=cursor,
    )
    record = ContextCompactionRecord(
        compaction_id="compaction-corrupt",
        source_fingerprint="sha256:" + "d" * 64,
        source_candidate_ids=["dialog-1", "dialog-2"],
        algorithm=algorithm,
        summary="Earlier dialog.",
        original_chars=100,
        compacted_chars=len("Earlier dialog."),
    )
    compaction_reference = store.save_recovery_artifact(
        run.run_id,
        kind="context_compaction",
        payload=record.model_dump(mode="json"),
    )
    binding = ContextCompactionBinding(
        record=record,
        artifact=compaction_reference,
    )
    selection = ContextSelectionMetadata(
        max_prompt_chars=100,
        original_prompt_chars=13,
        final_prompt_chars=13,
    )
    payload = {
        "query": "stable context",
        "project_path": str(project),
        "system_prompt": "",
        "dialog_context": [],
        "related_memories": [],
        "related_files": [],
        "environment_context": [],
        "context_compactions": [binding.model_dump(mode="json")],
        "prompt_text": "stable prompt",
        "context_selection": selection.to_json_dict(),
    }
    context_reference = store.save_recovery_artifact(
        run.run_id,
        kind="prompt_context",
        payload=payload,
    )
    snapshot = RuntimePromptContextSnapshot(
        context_id="context-with-compaction",
        request_hash="sha256:" + "a" * 64,
        prompt_hash=f"sha256:{hashlib.sha256(b'stable prompt').hexdigest()}",
        selection=selection,
        context_artifact=context_reference,
        compaction_bindings=[binding],
    )
    context_checkpoint = store.save(
        saved.model_copy(
            update={
                "checkpoint_id": "compaction-context-checkpoint",
                "generation": 2,
                "safe_boundary": CheckpointBoundary.CONTEXT_ASSEMBLED,
                "prompt_context_snapshot": snapshot,
                "integrity_checksum": "",
            }
        ),
        expected_generation=1,
    )
    artifact_path = (
        store.root_dir
        / run.run_id
        / "recovery_artifacts"
        / f"{compaction_reference.artifact_id}.json"
    )
    artifact_path.write_text("{}", encoding="utf-8")

    result = AgentRuntimeController(
        SimpleNamespace(
            tool_registry=None,
            runtime_diagnostics_hooks=hooks,
            session_id="replacement-session",
        ),
        session_executor=SimpleNamespace(
            supports_session_cursor=True,
            run=lambda *_args, **_kwargs: pytest.fail(
                "corrupt compaction must block session replay"
            ),
        ),
        checkpoint_store=store,
    ).resume(
        run.run_id,
        context_checkpoint.checkpoint_id,
        {"project_path": str(project), "task_id": "resume-root-task"},
    )

    assert result["success"] is False
    assert result["resume_decision"]["recoverability"] == "not_recoverable"
    assert result["resume_decision"]["reason_code"] == "checkpoint_corrupt"


def test_prompt_context_checkpoint_replays_after_real_process_exit(tmp_path) -> None:
    diagnostics_root = tmp_path / "process-context-diagnostics"
    run_id_file = tmp_path / "process-context-run.txt"
    project = tmp_path / "process-context-project"
    project.mkdir()
    child_code = textwrap.dedent(
        f"""
        import hashlib
        import os
        from pathlib import Path
        from types import SimpleNamespace

        from autonomous_iteration.checkpoint_store import RuntimeCheckpointStore
        from autonomous_iteration.runtime_controller import AgentRuntimeController
        from memory.context_builder import MemoryContextBuilder
        from memory.memory_store import MemoryStore
        from memory.short_memory import ShortMemory
        from metadata import SessionBootstrapCursor
        from runtime_diagnostics import DiagnosticRecorder
        from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks

        diagnostics_root = Path({str(diagnostics_root)!r})
        project = Path({str(project)!r})
        hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(diagnostics_root))
        hooks.on_task_received(
            task_id="process-context-task",
            source="test",
            raw_input="Inspect project",
            session_id="process-context-session",
        )
        run = hooks.recorder.load_run("process-context-task")
        Path({str(run_id_file)!r}).write_text(run.run_id, encoding="utf-8")
        short_memory = ShortMemory(repo_path=project)
        short_memory.add_message("user", "original context from exited process")
        builder = MemoryContextBuilder(
            short_memory=short_memory,
            memory_store=MemoryStore(Path({str(tmp_path)!r}) / "process-context-memory"),
        )
        runtime = SimpleNamespace(
            tool_registry=None,
            runtime_diagnostics_hooks=hooks,
            memory_context_builder=builder,
            session_id="process-context-session",
        )
        store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)

        class Runner:
            def run(self, goal, context, mode="standard"):
                controller._persist_session_bootstrap(
                    SessionBootstrapCursor(
                        mode="standard",
                        goal_hash="sha256:" + hashlib.sha256(goal.encode("utf-8")).hexdigest(),
                    )
                )
                builder.build(
                    "process-stable-context",
                    project_path=project,
                    include_environment=False,
                    system_prompt="fixed system",
                )
                os._exit(92)

        controller = AgentRuntimeController(runtime, session_executor=Runner(), checkpoint_store=store)
        controller.run(
            "Inspect project",
            {{
                "task_id": "process-context-task",
                "project_path": str(project),
                "tags": ["readonly"],
                "task_type": "analysis",
                "checkpointing_enabled": True,
            }},
        )
        """
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    completed = subprocess.run([sys.executable, "-c", child_code], env=environment, check=False)
    assert completed.returncode == 92

    run_id = run_id_file.read_text(encoding="utf-8")
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(diagnostics_root))
    hooks.recorder.attach_existing_run(
        run_id,
        expected_task_id="process-context-task",
        expected_session_id="process-context-session",
    )
    store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)
    interrupted = store.load_latest(run_id)
    assert interrupted is not None
    assert interrupted.safe_boundary == CheckpointBoundary.CONTEXT_ASSEMBLED
    changed_memory = ShortMemory(repo_path=project)
    changed_memory.add_message("user", "changed context in replacement process")
    builder = MemoryContextBuilder(
        short_memory=changed_memory,
        memory_store=MemoryStore(tmp_path / "changed-process-context-memory"),
    )
    replayed: list[dict] = []

    class ResumeRunner:
        supports_session_cursor = True

        def run(self, goal, context, mode="standard", resume_bootstrap=None):
            assert resume_bootstrap is not None
            replayed.append(
                builder.build(
                    "process-stable-context",
                    project_path=project,
                    include_environment=False,
                    system_prompt="fixed system",
                )
            )
            return {"success": True}

    result = AgentRuntimeController(
        SimpleNamespace(
            tool_registry=None,
            runtime_diagnostics_hooks=hooks,
            memory_context_builder=builder,
            session_id="replacement-process-session",
        ),
        session_executor=ResumeRunner(),
        checkpoint_store=store,
    ).resume(
        run_id,
        interrupted.checkpoint_id,
        {"project_path": str(project), "task_id": "process-context-task"},
    )

    assert result["success"] is True
    assert "original context from exited process" in replayed[0]["prompt_text"]
    assert "changed context in replacement process" not in replayed[0]["prompt_text"]


def test_runtime_controller_resumes_with_durable_session_cursor(tmp_path) -> None:
    cursor = _session_cursor()
    hooks, store, run, project, saved = _seed_resume_checkpoint(
        tmp_path,
        safe_boundary=CheckpointBoundary.SUBTASK_RESULT_APPLIED,
        session_cursor=cursor,
    )

    class CursorSessionRunner:
        supports_session_cursor = True

        def __init__(self) -> None:
            self.received_cursor = None

        def run(self, goal, context, mode="standard", resume_cursor=None):
            self.received_cursor = resume_cursor
            return {"success": True, "stats": {"tasks_completed": 2, "tasks_failed": 0}}

    runner = CursorSessionRunner()
    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=hooks,
        session_id="replacement-session",
    )
    controller = AgentRuntimeController(runtime, session_executor=runner, checkpoint_store=store)

    result = controller.resume(
        run.run_id,
        saved.checkpoint_id,
        {"project_path": str(project), "task_id": "resume-root-task"},
    )

    assert result["success"] is True
    assert result["resume_decision"]["recoverability"] == "recoverable_now"
    assert result["resume_decision"]["recovery_mode"] == "exact_resume"
    assert runner.received_cursor is not None
    assert runner.received_cursor.next_task_index == 1


def test_runtime_controller_retries_older_checkpoint_on_latest_generation_with_lineage(tmp_path) -> None:
    cursor = _session_cursor()
    hooks, store, run, project, saved = _seed_resume_checkpoint(
        tmp_path,
        safe_boundary=CheckpointBoundary.SUBTASK_RESULT_APPLIED,
        session_cursor=cursor,
    )
    newer = store.save(
        saved.model_copy(
            update={
                "checkpoint_id": "newer-failed-attempt",
                "generation": 2,
                "checkpoint_reason": "later failed resume attempt",
                "safe_boundary": CheckpointBoundary.CONTROLLED_STOP,
                "integrity_checksum": "",
            }
        ),
        expected_generation=1,
    )
    runner = SimpleNamespace(
        supports_session_cursor=True,
        run=lambda goal, context, mode="standard", resume_cursor=None: {
            "success": True,
            "stats": {"tasks_completed": 2, "tasks_failed": 0},
        },
    )
    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=hooks,
        session_id="replacement-session",
    )

    result = AgentRuntimeController(runtime, session_executor=runner, checkpoint_store=store).resume(
        run.run_id,
        saved.checkpoint_id,
        {"project_path": str(project), "task_id": "resume-root-task"},
    )

    assert result["success"] is True
    latest = store.load_latest(run.run_id)
    assert latest is not None
    assert latest.generation > newer.generation
    assert latest.resume_source_checkpoint_id == saved.checkpoint_id
    assert result["resume_decision"]["recovery_mode"] == "exact_resume"


def test_runtime_controller_replays_read_result_without_reapplying_budget(tmp_path) -> None:
    store = RuntimeCheckpointStore(tmp_path)
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=None, session_id="session-read")
    controller = AgentRuntimeController(
        runtime,
        session_executor=SimpleNamespace(run=lambda *_args, **_kwargs: {"success": True}),
        checkpoint_store=store,
    )
    controller.state = RuntimeStateMetadata(goal="Inspect project")
    controller._checkpointing_enabled = True
    controller._checkpoint_run_id = "run-read"
    controller._active_task_id = "root-read"
    controller._checkpoint_context = {"project_path": str(tmp_path), "cwd": str(tmp_path)}
    controller._active_session_cursor = _session_cursor(next_task_index=0)
    selection = ToolSelection(
        step_id="step_1_1",
        tool_name="file_reader",
        reason=SelectionReason.CAPABILITY_MATCH,
        input_metadata=ToolInputMetadata(tool_name="file_reader", file_path=str(tmp_path / "app.py")),
    )
    tool_call = SimpleNamespace(call_id="inspect:r1:c1")
    execution_result = SimpleNamespace(
        success=True,
        output_metadata=ToolResultMetadata(
            tool_name="file_reader",
            status=ResultStatus.SUCCESS,
            result=FileArtifactMetadata(file_path=str(tmp_path / "app.py"), content="print('ok')"),
        ),
        error=None,
    )

    assert controller.prepare_tool_call(tool_call, selection) is True
    assert controller.observe_tool_result(tool_call, selection, execution_result) is True
    controller.state_updater.apply_tool_result(controller.state, selection, execution_result)
    used_after_apply = controller.state.budget.file_reads_used

    replay = controller.replay_tool_result(tool_call, selection)

    assert replay is not None
    assert replay.success is True
    assert replay.recovery_already_applied is True
    assert replay.output_metadata.result.content == "print('ok')"
    assert controller.state.budget.file_reads_used == used_after_apply == 1
    latest = store.load_latest("run-read")
    assert latest is not None
    assert latest.read_tool_replay_entries[0].applied is True


def test_response_evidence_web_search_checkpoints_without_local_read_replay(tmp_path) -> None:
    store = RuntimeCheckpointStore(tmp_path)
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=None, session_id="session-web")
    controller = AgentRuntimeController(
        runtime,
        session_executor=SimpleNamespace(run=lambda *_args, **_kwargs: {"success": True}),
        checkpoint_store=store,
    )
    controller.state = RuntimeStateMetadata(
        goal="Ground current weather",
        task_purpose=RuntimeTaskPurpose.RESPONSE_EVIDENCE,
    )
    controller._checkpointing_enabled = True
    controller._checkpoint_run_id = "run-web"
    controller._active_task_id = "root-web"
    controller._checkpoint_context = {"project_path": str(tmp_path), "cwd": str(tmp_path)}
    controller._active_session_cursor = _session_cursor(next_task_index=0)
    selection = ToolSelection(
        step_id="step_1_1",
        tool_name="web_searcher",
        reason=SelectionReason.CAPABILITY_MATCH,
        input_metadata=ToolInputMetadata(tool_name="web_searcher", query="常熟天气"),
    )
    tool_call = SimpleNamespace(call_id="weather:r1:c1")
    execution_result = SimpleNamespace(
        success=True,
        output_metadata=ToolResultMetadata(
            tool_name="web_searcher",
            status=ResultStatus.SUCCESS,
            result=SearchArtifactMetadata(
                query="常熟天气",
                provider="wttr_in",
                count=1,
                research_summary="常熟当前26°C。",
            ),
        ),
        error=None,
    )

    assert controller.prepare_tool_call(tool_call, selection) is True
    assert controller.observe_tool_result(tool_call, selection, execution_result) is True

    latest = store.load_latest("run-web")
    assert latest is not None
    assert latest.safe_boundary == CheckpointBoundary.TOOL_RESULT_OBSERVED
    assert latest.read_tool_replay_entries == []


def test_runtime_controller_exact_resume_restores_identity_state_and_budget(tmp_path) -> None:
    hooks, store, run, project, saved = _seed_resume_checkpoint(tmp_path)

    class ResumeSessionRunner:
        calls = 0

        def run(self, goal, context, mode="standard"):
            self.calls += 1
            assert controller.state is not None
            assert controller.state.budget.tool_calls_used == 2
            assert controller.state.budget.file_reads_used == 2
            assert runtime.session_id == "original-session"
            return {"success": True, "stats": {"tasks_completed": 1, "tasks_failed": 0}}

    runner = ResumeSessionRunner()
    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=hooks,
        session_id="new-process-session",
    )
    controller = AgentRuntimeController(runtime, session_executor=runner, checkpoint_store=store)

    result = controller.resume(
        run.run_id,
        saved.checkpoint_id,
        {"project_path": str(project), "task_id": "resume-root-task"},
    )

    assert result["success"] is True
    assert result["resume_decision"]["decision"] == "exact_resume"
    assert result["resume_decision"]["recoverability"] == Recoverability.RECOVERABLE_NOW
    assert result["resume_decision"]["recovery_mode"] == RecoveryMode.EXACT_RESUME
    assert result["resume_decision"]["automation_policy"] == RecoveryAutomationPolicy.AUTOMATIC_ALLOWED
    assert result["resume_decision"]["reason_code"] == RecoveryReasonCode.CHECKPOINT_VALID
    assert result["resume_decision"]["resume_attempt_id"]
    assert result["agent_runtime_state"]["recovery_status"] == RecoveryStatus.RECOVERED
    assert result["runtime_report"]["recovery_status"] == RecoveryStatus.RECOVERED
    assert result["agent_runtime_state"]["budget"]["tool_calls_used"] == 2
    assert result["agent_runtime_state"]["budget"]["file_reads_used"] == 2
    assert result["agent_runtime_state"]["budget"]["recovery_rounds_used"] == 1
    assert runner.calls == 1


def test_runtime_controller_resume_returns_completed_checkpoint_without_reexecution(tmp_path) -> None:
    hooks, store, run, project, saved = _seed_resume_checkpoint(
        tmp_path,
        safe_boundary="controlled_stop",
        phase=AgentPhase.SUMMARIZE,
    )
    runner = SimpleNamespace(run=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")))
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=hooks, session_id="new-session")
    controller = AgentRuntimeController(runtime, session_executor=runner, checkpoint_store=store)

    result = controller.resume(
        run.run_id,
        saved.checkpoint_id,
        {"project_path": str(project), "task_id": "resume-root-task"},
    )

    assert result["success"] is True
    assert result["resume_status"] == "already_completed"
    assert result["resume_decision"]["recoverability"] == Recoverability.ALREADY_COMPLETE
    assert result["resume_decision"]["recovery_mode"] == RecoveryMode.RETURN_COMPLETED
    assert result["agent_runtime_state"]["recovery_status"] == RecoveryStatus.RECOVERED
    assert result["agent_runtime_state"]["budget"]["tool_calls_used"] == 2


def test_runtime_controller_blocks_resume_without_a_session_stage_cursor(tmp_path) -> None:
    hooks, store, run, project, saved = _seed_resume_checkpoint(
        tmp_path,
        safe_boundary="tool_result_applied",
    )
    runner = SimpleNamespace(run=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")))
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=hooks, session_id="new-session")
    controller = AgentRuntimeController(runtime, session_executor=runner, checkpoint_store=store)

    result = controller.resume(
        run.run_id,
        saved.checkpoint_id,
        {"project_path": str(project), "task_id": "resume-root-task"},
    )

    assert result["success"] is False
    assert result["resume_decision"]["decision"] == "blocked"
    assert result["resume_decision"]["recoverability"] == Recoverability.NOT_RECOVERABLE
    assert result["resume_decision"]["recovery_mode"] == RecoveryMode.NONE
    assert result["resume_decision"]["reason_code"] == RecoveryReasonCode.MISSING_STAGE_CURSOR
    assert result["resume_decision"]["fallback"]["action"] == RecoveryFallbackAction.OFFER_NEW_LINKED_RUN
    assert result["agent_runtime_state"]["recovery_status"] == RecoveryStatus.UNRECOVERABLE
    assert "stage cursor" in result["resume_decision"]["reason"]


def test_runtime_controller_blocks_resume_for_wrong_root_task(tmp_path) -> None:
    hooks, store, run, project, saved = _seed_resume_checkpoint(tmp_path)
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=hooks, session_id="new-session")
    controller = AgentRuntimeController(runtime, checkpoint_store=store)

    result = controller.resume(
        run.run_id,
        saved.checkpoint_id,
        {"project_path": str(project), "task_id": "different-root-task"},
    )

    assert result["success"] is False
    assert result["resume_decision"]["decision"] == "blocked"
    assert result["resume_decision"]["recoverability"] == Recoverability.RECOVERABLE_AFTER_ACTION
    assert result["resume_decision"]["reason_code"] == RecoveryReasonCode.ROOT_TASK_MISMATCH
    assert result["resume_decision"]["fallback"]["action"] == RecoveryFallbackAction.REQUEST_USER_INPUT
    assert result["agent_runtime_state"]["recovery_status"] == RecoveryStatus.WAITING_USER
    assert "root task" in result["resume_decision"]["reason"]


def test_runtime_controller_blocks_resume_when_recovery_budget_is_exhausted(tmp_path) -> None:
    hooks, store, run, project, saved = _seed_resume_checkpoint(tmp_path, recovery_rounds_used=3)
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=hooks, session_id="new-session")
    controller = AgentRuntimeController(runtime, checkpoint_store=store)

    result = controller.resume(
        run.run_id,
        saved.checkpoint_id,
        {"project_path": str(project), "task_id": "resume-root-task"},
    )

    assert result["success"] is False
    assert result["resume_decision"]["decision"] == "blocked"
    assert result["resume_decision"]["reason"] == "recovery budget exhausted"
    assert result["resume_decision"]["recoverability"] == Recoverability.RECOVERABLE_AFTER_ACTION
    assert result["resume_decision"]["reason_code"] == RecoveryReasonCode.RECOVERY_BUDGET_EXHAUSTED
    assert result["resume_decision"]["fallback"]["action"] == RecoveryFallbackAction.REQUEST_BUDGET_EXTENSION
    assert result["agent_runtime_state"]["recovery_status"] == RecoveryStatus.WAITING_USER


def test_runtime_controller_offers_previous_valid_checkpoint_when_requested_generation_is_corrupt(tmp_path) -> None:
    hooks, store, run, project, first = _seed_resume_checkpoint(tmp_path)
    second = store.save(
        first.model_copy(
            update={
                "checkpoint_id": "resume-checkpoint-2",
                "generation": 2,
                "integrity_checksum": "",
            }
        ),
        expected_generation=1,
    )
    checkpoint_file = (
        store.root_dir / run.run_id / "checkpoints" / f"{second.checkpoint_id}.json"
    )
    checkpoint_file.write_text('{"truncated":', encoding="utf-8")
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=hooks, session_id="replacement")
    controller = AgentRuntimeController(
        runtime,
        session_executor=SimpleNamespace(
            run=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute"))
        ),
        checkpoint_store=store,
    )

    result = controller.resume(
        run.run_id,
        second.checkpoint_id,
        {"project_path": str(project), "task_id": "resume-root-task"},
    )

    assert result["success"] is False
    assert result["resume_decision"]["recoverability"] == Recoverability.RECOVERABLE_AFTER_ACTION
    assert result["resume_decision"]["reason_code"] == RecoveryReasonCode.PREVIOUS_CHECKPOINT_AVAILABLE
    assert result["resume_decision"]["next_checkpoint_id"] == first.checkpoint_id
    assert result["resume_decision"]["fallback"]["action"] == RecoveryFallbackAction.USE_PREVIOUS_VALID_CHECKPOINT
    assert result["agent_runtime_state"]["recovery_status"] == RecoveryStatus.WAITING_USER


def test_runtime_controller_returns_typed_unrecoverable_state_when_checkpoint_is_missing(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path / "diagnostics"))
    hooks.on_task_received(
        task_id="missing-checkpoint-task",
        source="test",
        raw_input="Resume missing checkpoint",
        session_id="missing-checkpoint-session",
    )
    run = hooks.recorder.load_run("missing-checkpoint-task")
    assert run is not None
    store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=hooks, session_id="replacement")
    controller = AgentRuntimeController(runtime, checkpoint_store=store)

    result = controller.resume(
        run.run_id,
        "does-not-exist",
        {"project_path": str(tmp_path), "task_id": "missing-checkpoint-task"},
    )

    assert result["success"] is False
    assert result["resume_decision"]["recoverability"] == Recoverability.NOT_RECOVERABLE
    assert result["resume_decision"]["reason_code"] == RecoveryReasonCode.CHECKPOINT_MISSING
    assert result["resume_decision"]["fallback"]["action"] == RecoveryFallbackAction.TERMINATE_PRESERVING_EVIDENCE
    assert result["agent_runtime_state"]["recovery_status"] == RecoveryStatus.UNRECOVERABLE


def test_runtime_controller_waits_without_writing_when_original_run_lease_is_active(tmp_path) -> None:
    hooks, store, run, project, saved = _seed_resume_checkpoint(tmp_path)
    active_lease = store.try_acquire_run_lease(run.run_id)
    assert active_lease is not None
    events_before = hooks.recorder.load_trajectory_events(run.run_id, limit=0)
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=hooks, session_id="replacement")
    controller = AgentRuntimeController(
        runtime,
        session_executor=SimpleNamespace(
            run=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute"))
        ),
        checkpoint_store=store,
    )

    try:
        result = controller.resume(
            run.run_id,
            saved.checkpoint_id,
            {"project_path": str(project), "task_id": "resume-root-task"},
        )
    finally:
        store.release_run_lease(active_lease)

    assert result["success"] is False
    assert result["resume_decision"]["reason_code"] == RecoveryReasonCode.RUN_LEASE_ACTIVE
    assert result["resume_decision"]["fallback"]["action"] == RecoveryFallbackAction.RETRY_LATER
    assert result["agent_runtime_state"]["recovery_status"] == RecoveryStatus.WAITING_RETRY
    assert hooks.recorder.load_trajectory_events(run.run_id, limit=0) == events_before


def test_runtime_controller_persists_file_mutation_prepare_observe_apply_boundaries(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path / "diagnostics"))
    hooks.on_task_received(
        task_id="mutation-root-task",
        source="test",
        raw_input="Update note",
        session_id="mutation-session",
    )
    run = hooks.recorder.load_run("mutation-root-task")
    assert run is not None
    project = tmp_path / "project"
    project.mkdir()
    target = project / "note.txt"
    target.write_text("before", encoding="utf-8")
    store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)

    class MutationSessionRunner:
        def run(self, goal, context, mode="standard"):
            selection = ToolSelection(
                step_id="write-step",
                tool_name="file_writer",
                reason=SelectionReason.CAPABILITY_MATCH,
                input_metadata=ToolInputMetadata(
                    tool_name="file_writer",
                    file_path=str(target),
                    content="after",
                    operation_kind="file_replace",
                ),
            )
            tool_call = SimpleNamespace(call_id="write-call", step_id="write-step")
            controller.set_pending_verification(
                VerificationPlanMetadata(
                    reason="task validation",
                    commands=["pytest -q"],
                    target_files=[str(target)],
                )
            )
            assert controller.prepare_tool_call(tool_call, selection) is True
            prepared = store.load_latest(run.run_id)
            assert prepared is not None
            assert prepared.side_effect_state == "prepared"
            assert prepared.pending_verification is not None
            assert prepared.pending_verification.commands == ["pytest -q"]
            assert prepared.project_fingerprint.target_file_hashes[str(target)].startswith("sha256:")
            assert prepared.project_fingerprint.expected_target_file_hashes[str(target)].startswith("sha256:")

            target.write_text("after", encoding="utf-8")
            execution_result = SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="file_writer",
                    status=ResultStatus.SUCCESS,
                    result=FileArtifactMetadata(file_path=str(target)),
                ),
                error=None,
            )
            assert controller.observe_tool_result(tool_call, selection, execution_result) is True
            observed = store.load_latest(run.run_id)
            assert observed is not None
            assert observed.side_effect_state == "observed"
            controller.state_updater.apply_tool_result(controller.state, selection, execution_result)
            applied = store.load_latest(run.run_id)
            assert applied is not None
            assert applied.side_effect_state == "applied"
            assert applied.runtime_state.budget.file_edits_used == 1
            verification_selection = ToolSelection(
                step_id="verify-step",
                tool_name="command_executor",
                reason=SelectionReason.CAPABILITY_MATCH,
                input_metadata=ToolInputMetadata(
                    tool_name="command_executor",
                    command="pytest -q",
                    cwd=str(project),
                ),
            )
            verification_result = SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="command_executor",
                    status=ResultStatus.SUCCESS,
                    result=TextArtifactMetadata(content="verified"),
                ),
                error=None,
            )
            controller.state_updater.apply_tool_result(
                controller.state,
                verification_selection,
                verification_result,
            )
            verified = store.load_latest(run.run_id)
            assert verified is not None
            assert verified.safe_boundary == "verification_applied"
            assert verified.runtime_state.verification_status == "passed"
            assert verified.pending_verification is None
            return {"success": True, "written_files": [str(target)]}

    runtime = SimpleNamespace(
        tool_registry=None,
        runtime_diagnostics_hooks=hooks,
        session_id="mutation-session",
    )
    controller = AgentRuntimeController(runtime, session_executor=MutationSessionRunner(), checkpoint_store=store)

    result = controller.run(
        "Update note",
        {
            "task_id": "mutation-root-task",
            "project_path": str(project),
            "checkpointing_enabled": True,
        },
    )

    assert result["success"] is True
    checkpoints = sorted(
        (store.root_dir / run.run_id / "checkpoints").glob("*.json"),
        key=lambda path: path.stat().st_mtime_ns,
    )
    states = [RuntimeCheckpointMetadata.model_validate_json(path.read_text(encoding="utf-8")).side_effect_state for path in checkpoints]
    assert "prepared" in states
    assert "observed" in states
    assert "applied" in states


def _seed_file_mutation_checkpoint(
    tmp_path,
    *,
    current_content: str,
    boundary: str = "tool_call_prepared",
    pending_verification: VerificationPlanMetadata | None = None,
):
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path / "diagnostics"))
    hooks.on_task_received(
        task_id="file-resume-task",
        source="test",
        raw_input="Update note",
        session_id="file-resume-session",
    )
    run = hooks.recorder.load_run("file-resume-task")
    assert run is not None
    project = tmp_path / "project"
    project.mkdir()
    target = project / "note.txt"
    target.write_text(current_content, encoding="utf-8")
    before_hash = f"sha256:{hashlib.sha256(b'before').hexdigest()}"
    after_hash = f"sha256:{hashlib.sha256(b'after').hexdigest()}"
    state = RuntimeStateMetadata(goal="Update note", phase=AgentPhase.EXECUTE)
    if boundary in {"tool_result_applied", "verification_applied"}:
        state.phase = AgentPhase.SUMMARIZE if boundary == "verification_applied" else AgentPhase.VERIFY
        state.verification_status = "passed" if boundary == "verification_applied" else "required"
        state.add_modified_file(str(target))
        state.budget.consume_tool_call(file_edit=True)
        if boundary == "verification_applied":
            state.budget.consume_verification_attempt()
    store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)
    checkpoint = RuntimeCheckpointMetadata(
        checkpoint_id="file-resume-checkpoint",
        generation=1,
        run_id=run.run_id,
        root_task_id="file-resume-task",
        session_id="file-resume-session",
        checkpoint_reason="injected file crash",
        safe_boundary=boundary,
        runtime_state=state,
        step_id="write-step",
        tool_name="file_writer",
        tool_call_id="write-call",
        tool_input=ToolInputMetadata(
            tool_name="file_writer",
            file_path=str(target),
            content="after",
            operation_kind="file_replace",
        ),
        mutation_class="mutating",
        side_effect_state=(
            "prepared"
            if boundary == "tool_call_prepared"
            else "applied"
            if boundary in {"tool_result_applied", "verification_applied"}
            else "observed"
        ),
        observed_file_result=(
            ObservedFileMutationResult(success=True, file_path=str(target))
            if boundary in {"tool_result_observed", "tool_result_applied", "verification_applied"}
            else None
        ),
        pending_verification=pending_verification,
        project_fingerprint=ProjectFingerprint(
            project_root=str(project),
            cwd=str(project),
            target_file_hashes={str(target): before_hash if boundary == "tool_call_prepared" else after_hash},
            expected_target_file_hashes={str(target): after_hash},
        ),
    )
    saved = store.save(checkpoint)
    return hooks, store, run, project, target, saved


@pytest.mark.parametrize(
    "boundary",
    ["tool_call_prepared", "tool_result_observed", "tool_result_applied"],
)
def test_file_resume_detects_completed_write_without_replaying_and_verifies(tmp_path, boundary) -> None:
    hooks, store, run, project, target, saved = _seed_file_mutation_checkpoint(
        tmp_path,
        current_content="after",
        boundary=boundary,
    )

    class RecordingExecutor:
        def __init__(self):
            self.tools = []

        def execute_single(self, selection, context=None):
            self.tools.append(selection.tool_name)
            assert selection.tool_name == "command_executor"
            return SimpleNamespace(
                success=True,
                output_metadata=ToolResultMetadata(
                    tool_name="command_executor",
                    status=ResultStatus.SUCCESS,
                    result=TextArtifactMetadata(content="verified"),
                ),
                error=None,
            )

    executor = RecordingExecutor()
    runtime = SimpleNamespace(
        tool_registry=None,
        tool_executor=executor,
        runtime_diagnostics_hooks=hooks,
        session_id="new-session",
    )
    _attach_ready_test_environment(runtime, project)
    controller = AgentRuntimeController(runtime, checkpoint_store=store)

    result = controller.resume(
        run.run_id,
        saved.checkpoint_id,
        {
            "project_path": str(project),
            "task_id": "file-resume-task",
            "test_command": "pytest -q",
        },
    )

    assert result["success"] is True
    assert result["resume_decision"]["decision"] == "reconcile_then_resume"
    assert result["resume_decision"]["recoverability"] == Recoverability.RECOVERABLE_NOW
    assert result["resume_decision"]["recovery_mode"] == RecoveryMode.RECONCILE_THEN_RESUME
    assert result["agent_runtime_state"]["recovery_status"] == RecoveryStatus.RECOVERED
    assert executor.tools == ["command_executor"]
    assert target.read_text(encoding="utf-8") == "after"
    budget = result["agent_runtime_state"]["budget"]
    assert budget["file_edits_used"] == 1
    assert budget["verification_attempts_used"] == 1


def test_file_resume_uses_checkpointed_verification_plan_without_cli_context(tmp_path) -> None:
    verification = VerificationPlanMetadata(
        reason="Original task validation command",
        commands=[
            "python -m pytest -q test_calculator.py",
            "python -m compileall -q calculator.py",
        ],
        target_files=["calculator.py"],
        attributes={"timeout": 30},
    )
    hooks, store, run, project, target, saved = _seed_file_mutation_checkpoint(
        tmp_path,
        current_content="after",
        boundary="tool_result_applied",
        pending_verification=verification,
    )
    executed_commands: list[str] = []

    def execute_single(selection, context=None):
        executed_commands.append(str(selection.input_metadata.command))
        return SimpleNamespace(
            success=True,
            output_metadata=ToolResultMetadata(
                tool_name="command_executor",
                status=ResultStatus.SUCCESS,
                result=TextArtifactMetadata(content="verified"),
            ),
            error=None,
        )

    runtime = SimpleNamespace(
        tool_registry=None,
        tool_executor=SimpleNamespace(execute_single=execute_single),
        runtime_diagnostics_hooks=hooks,
        session_id="replacement-session",
    )
    python_executable = _attach_ready_test_environment(runtime, project)

    result = AgentRuntimeController(runtime, checkpoint_store=store).resume(
        run.run_id,
        saved.checkpoint_id,
        {"project_path": str(project), "task_id": "file-resume-task"},
    )

    assert result["success"] is True, result
    assert executed_commands == [
        f"{python_executable} -m pytest -q test_calculator.py",
        f"{python_executable} -m compileall -q calculator.py",
    ]
    latest = store.load_latest(run.run_id)
    assert latest is not None
    assert latest.pending_verification is None


def test_file_resume_continues_at_next_checkpointed_verification_command(tmp_path) -> None:
    verification = VerificationPlanMetadata(
        reason="Resume remaining validation only",
        commands=["python -m pytest -q", "python -m compileall -q calculator.py"],
        next_command_index=1,
        completed_commands=["python -m pytest -q"],
        target_files=["calculator.py"],
        attributes={"cwd": str(tmp_path), "timeout": 30},
    )
    hooks, store, run, project, _target, saved = _seed_file_mutation_checkpoint(
        tmp_path,
        current_content="after",
        boundary="tool_result_applied",
        pending_verification=verification,
    )
    executed_commands: list[str] = []

    def execute_single(selection, context=None):
        executed_commands.append(str(selection.input_metadata.command))
        return SimpleNamespace(
            success=True,
            output_metadata=ToolResultMetadata(
                tool_name="command_executor",
                status=ResultStatus.SUCCESS,
                result=TextArtifactMetadata(content="verified"),
            ),
            error=None,
        )

    runtime = SimpleNamespace(
        tool_registry=None,
        tool_executor=SimpleNamespace(execute_single=execute_single),
        runtime_diagnostics_hooks=hooks,
        session_id="replacement-session",
    )
    python_executable = _attach_ready_test_environment(runtime, project)

    result = AgentRuntimeController(runtime, checkpoint_store=store).resume(
        run.run_id,
        saved.checkpoint_id,
        {"project_path": str(project), "task_id": "file-resume-task"},
    )

    assert result["success"] is True
    assert executed_commands == [f"{python_executable} -m compileall -q calculator.py"]
    latest = store.load_latest(run.run_id)
    assert latest is not None
    assert latest.safe_boundary == CheckpointBoundary.VERIFICATION_APPLIED
    assert latest.pending_verification is None


def test_file_resume_runs_pending_validation_even_after_minimal_verification_checkpoint(tmp_path) -> None:
    verification = VerificationPlanMetadata(
        reason="Original task validation command",
        commands=["python -m pytest -q test_calculator.py"],
        target_files=["calculator.py"],
    )
    hooks, store, run, project, _target, saved = _seed_file_mutation_checkpoint(
        tmp_path,
        current_content="after",
        boundary="verification_applied",
        pending_verification=verification,
    )
    executed_commands: list[str] = []

    def execute_single(selection, context=None):
        executed_commands.append(str(selection.input_metadata.command))
        return SimpleNamespace(
            success=True,
            output_metadata=ToolResultMetadata(
                tool_name="command_executor",
                status=ResultStatus.SUCCESS,
                result=TextArtifactMetadata(content="verified"),
            ),
            error=None,
        )

    runtime = SimpleNamespace(
        tool_registry=None,
        tool_executor=SimpleNamespace(execute_single=execute_single),
        runtime_diagnostics_hooks=hooks,
        session_id="replacement-session",
    )
    python_executable = _attach_ready_test_environment(runtime, project)
    result = AgentRuntimeController(runtime, checkpoint_store=store).resume(
        run.run_id,
        saved.checkpoint_id,
        {"project_path": str(project), "task_id": "file-resume-task"},
    )

    assert result["success"] is True
    assert result["resume_status"] == "resumed"
    assert executed_commands == [f"{python_executable} -m pytest -q test_calculator.py"]
    latest = store.load_latest(run.run_id)
    assert latest is not None
    assert latest.pending_verification is None


def test_file_resume_blocks_external_drift_without_overwriting_user_change(tmp_path) -> None:
    hooks, store, run, project, target, saved = _seed_file_mutation_checkpoint(
        tmp_path,
        current_content="user edit",
    )
    executor = SimpleNamespace(
        execute_single=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute"))
    )
    runtime = SimpleNamespace(
        tool_registry=None,
        tool_executor=executor,
        runtime_diagnostics_hooks=hooks,
        session_id="new-session",
    )
    controller = AgentRuntimeController(runtime, checkpoint_store=store)

    result = controller.resume(
        run.run_id,
        saved.checkpoint_id,
        {"project_path": str(project), "task_id": "file-resume-task"},
    )

    assert result["success"] is False
    assert result["resume_decision"]["decision"] == "blocked"
    assert result["resume_decision"]["recoverability"] == Recoverability.RECOVERABLE_AFTER_ACTION
    assert result["resume_decision"]["reason_code"] == RecoveryReasonCode.PROJECT_DRIFT_CONFLICTING
    assert result["resume_decision"]["fallback"]["action"] == RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION
    assert result["agent_runtime_state"]["recovery_status"] == RecoveryStatus.WAITING_USER
    assert "differs from both" in result["resume_decision"]["reason"]
    assert target.read_text(encoding="utf-8") == "user edit"


def test_external_command_checkpoint_is_not_automatically_replayed(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path / "diagnostics"))
    hooks.on_task_received(
        task_id="command-resume-task",
        source="test",
        raw_input="Install dependency",
        session_id="command-resume-session",
    )
    run = hooks.recorder.load_run("command-resume-task")
    assert run is not None
    project = tmp_path / "project"
    project.mkdir()
    store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)
    saved = store.save(
        RuntimeCheckpointMetadata(
            checkpoint_id="command-checkpoint",
            generation=1,
            run_id=run.run_id,
            root_task_id="command-resume-task",
            session_id="command-resume-session",
            checkpoint_reason="command prepared",
            safe_boundary="tool_call_prepared",
            runtime_state=RuntimeStateMetadata(goal="Install dependency", phase=AgentPhase.EXECUTE),
            tool_name="command_executor",
            tool_call_id="command-call",
            tool_input=ToolInputMetadata(
                tool_name="command_executor",
                command="pip install example",
                cwd=str(project),
            ),
            mutation_class="externally_indeterminate",
            side_effect_state="prepared",
            project_fingerprint=ProjectFingerprint(project_root=str(project), cwd=str(project)),
        )
    )
    runtime = SimpleNamespace(
        tool_registry=None,
        tool_executor=SimpleNamespace(
            execute_single=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute"))
        ),
        runtime_diagnostics_hooks=hooks,
        session_id="new-session",
    )
    controller = AgentRuntimeController(runtime, checkpoint_store=store)

    result = controller.resume(
        run.run_id,
        saved.checkpoint_id,
        {"project_path": str(project), "task_id": "command-resume-task"},
    )

    assert result["success"] is False
    assert result["resume_decision"]["decision"] == "blocked"
    assert result["resume_decision"]["recoverability"] == Recoverability.RECOVERABLE_AFTER_ACTION
    assert result["resume_decision"]["automation_policy"] == RecoveryAutomationPolicy.MANUAL_ONLY
    assert result["resume_decision"]["reason_code"] == RecoveryReasonCode.EXTERNAL_WRITE_WITHOUT_PROBE
    assert result["resume_decision"]["fallback"]["action"] == RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION
    assert result["agent_runtime_state"]["recovery_status"] == RecoveryStatus.WAITING_USER
    assert "not eligible for automatic replay" in result["resume_decision"]["reason"]


def test_read_only_resume_blocks_when_git_worktree_changed(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    tracked = project / "tracked.txt"
    tracked.write_text("before", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True)
    subprocess.run(["git", "add", "tracked.txt"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=project, check=True)
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path / "diagnostics"))
    hooks.on_task_received(
        task_id="git-resume-task",
        source="test",
        raw_input="Inspect project",
        session_id="git-resume-session",
    )
    run = hooks.recorder.load_run("git-resume-task")
    assert run is not None
    state = RuntimeStateMetadata(goal="Inspect project", phase=AgentPhase.UNDERSTAND_PROJECT)
    state.add_assumption("runtime_mode:read_only_analysis")
    store = RuntimeCheckpointStore(hooks.recorder.trajectory_dir)
    saved = store.save(
        RuntimeCheckpointMetadata(
            checkpoint_id="git-resume-checkpoint",
            generation=1,
            run_id=run.run_id,
            root_task_id="git-resume-task",
            session_id="git-resume-session",
            checkpoint_reason="task initialized",
            safe_boundary="task_normalized",
            runtime_state=state,
            project_fingerprint=AgentRuntimeController._build_project_fingerprint(
                project_root=str(project),
                cwd=str(project),
            ),
        )
    )
    tracked.write_text("user change", encoding="utf-8")
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=hooks, session_id="new-session")
    controller = AgentRuntimeController(
        runtime,
        session_executor=SimpleNamespace(
            run=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute"))
        ),
        checkpoint_store=store,
    )

    result = controller.resume(
        run.run_id,
        saved.checkpoint_id,
        {"project_path": str(project), "task_id": "git-resume-task"},
    )

    assert result["success"] is False
    assert result["resume_decision"]["decision"] == "blocked"
    assert "Git worktree status differs from checkpoint" in result["resume_decision"]["project_drift"]
    assert tracked.read_text(encoding="utf-8") == "user change"


def test_subprocess_exit_after_file_write_recovers_without_replaying_write(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "note.txt"
    target.write_text("before", encoding="utf-8")
    store_root = tmp_path / "trajectory"
    child_code = textwrap.dedent(
        f"""
        import hashlib
        import os
        from pathlib import Path
        from autonomous_iteration.checkpoint_store import RuntimeCheckpointStore
        from metadata import AgentPhase, ProjectFingerprint, RuntimeCheckpointMetadata, RuntimeStateMetadata, ToolInputMetadata

        target = Path({str(target)!r})
        before_hash = 'sha256:' + hashlib.sha256(target.read_bytes()).hexdigest()
        after_hash = 'sha256:' + hashlib.sha256(b'after').hexdigest()
        checkpoint = RuntimeCheckpointMetadata(
            checkpoint_id='subprocess-checkpoint',
            generation=1,
            run_id='subprocess-run',
            root_task_id='subprocess-task',
            session_id='subprocess-session',
            checkpoint_reason='before injected process exit',
            safe_boundary='tool_call_prepared',
            runtime_state=RuntimeStateMetadata(goal='Update note', phase=AgentPhase.EXECUTE),
            step_id='write-step',
            tool_name='file_writer',
            tool_call_id='write-call',
            tool_input=ToolInputMetadata(tool_name='file_writer', file_path=str(target), content='after', operation_kind='file_replace'),
            mutation_class='mutating',
            side_effect_state='prepared',
            project_fingerprint=ProjectFingerprint(
                project_root={str(project)!r},
                cwd={str(project)!r},
                target_file_hashes={{str(target): before_hash}},
                expected_target_file_hashes={{str(target): after_hash}},
            ),
        )
        RuntimeCheckpointStore({str(store_root)!r}).save(checkpoint)
        target.write_text('after', encoding='utf-8')
        os._exit(23)
        """
    )
    environment = dict(os.environ)
    source_path = str(Path(__file__).parents[1] / "src")
    environment["PYTHONPATH"] = source_path

    completed = subprocess.run([sys.executable, "-c", child_code], env=environment, check=False)

    assert completed.returncode == 23
    store = RuntimeCheckpointStore(store_root)
    executor_tools = []

    def execute_single(selection, context=None):
        executor_tools.append(selection.tool_name)
        assert selection.tool_name == "command_executor"
        return SimpleNamespace(
            success=True,
            output_metadata=ToolResultMetadata(
                tool_name="command_executor",
                status=ResultStatus.SUCCESS,
                result=TextArtifactMetadata(content="verified"),
            ),
            error=None,
        )

    runtime = SimpleNamespace(
        tool_registry=None,
        tool_executor=SimpleNamespace(execute_single=execute_single),
        runtime_diagnostics_hooks=None,
        session_id="replacement-session",
    )
    _attach_ready_test_environment(runtime, project)
    controller = AgentRuntimeController(runtime, checkpoint_store=store)

    result = controller.resume(
        "subprocess-run",
        "subprocess-checkpoint",
        {
            "project_path": str(project),
            "task_id": "subprocess-task",
            "test_command": "pytest -q",
        },
    )

    assert result["success"] is True
    assert executor_tools == ["command_executor"]
    assert target.read_text(encoding="utf-8") == "after"


def test_subprocess_exit_between_verification_commands_resumes_remaining_suffix(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "calculator.py"
    target.write_text("value = 2\n", encoding="utf-8")
    store_root = tmp_path / "trajectory"
    child_code = textwrap.dedent(
        f"""
        import hashlib
        import os
        from pathlib import Path
        from autonomous_iteration.checkpoint_store import RuntimeCheckpointStore
        from metadata import AgentPhase, ProjectFingerprint, RuntimeCheckpointMetadata, RuntimeStateMetadata, ToolInputMetadata, VerificationPlanMetadata

        target = Path({str(target)!r})
        target_hash = 'sha256:' + hashlib.sha256(target.read_bytes()).hexdigest()
        state = RuntimeStateMetadata(goal='Fix calculator', phase=AgentPhase.VERIFY, verification_status='required')
        state.budget.consume_tool_call(file_edit=True)
        state.budget.consume_verification_attempt()
        checkpoint = RuntimeCheckpointMetadata(
            checkpoint_id='verification-cursor-checkpoint',
            generation=2,
            run_id='verification-cursor-run',
            root_task_id='verification-cursor-task',
            session_id='original-session',
            checkpoint_reason='first verification command applied before process exit',
            safe_boundary='verification_required',
            runtime_state=state,
            step_id='write-step',
            tool_name='file_writer',
            tool_call_id='write-call',
            tool_input=ToolInputMetadata(tool_name='file_writer', file_path=str(target), content='value = 2\\n', operation_kind='file_replace'),
            mutation_class='mutating',
            side_effect_state='applied',
            pending_verification=VerificationPlanMetadata(
                reason='Run both required checks',
                commands=['python -m pytest -q', 'python -m compileall -q calculator.py'],
                next_command_index=1,
                completed_commands=['python -m pytest -q'],
                target_files=[str(target)],
                attributes={{'cwd': {str(project)!r}, 'timeout': 30}},
            ),
            project_fingerprint=ProjectFingerprint(
                project_root={str(project)!r},
                cwd={str(project)!r},
                target_file_hashes={{str(target): target_hash}},
                expected_target_file_hashes={{str(target): target_hash}},
            ),
        )
        RuntimeCheckpointStore({str(store_root)!r}).save(checkpoint)
        os._exit(23)
        """
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")

    completed = subprocess.run([sys.executable, "-c", child_code], env=environment, check=False)

    assert completed.returncode == 23
    executed_commands: list[str] = []

    def execute_single(selection, context=None):
        executed_commands.append(str(selection.input_metadata.command))
        return SimpleNamespace(
            success=True,
            output_metadata=ToolResultMetadata(
                tool_name="command_executor",
                status=ResultStatus.SUCCESS,
                result=TextArtifactMetadata(content="verified"),
            ),
            error=None,
        )

    runtime = SimpleNamespace(
        tool_registry=None,
        tool_executor=SimpleNamespace(execute_single=execute_single),
        runtime_diagnostics_hooks=None,
        session_id="replacement-session",
    )
    python_executable = _attach_ready_test_environment(runtime, project)
    store = RuntimeCheckpointStore(store_root)

    result = AgentRuntimeController(runtime, checkpoint_store=store).resume(
        "verification-cursor-run",
        "verification-cursor-checkpoint",
        {"project_path": str(project), "task_id": "verification-cursor-task"},
    )

    assert result["success"] is True
    assert executed_commands == [f"{python_executable} -m compileall -q calculator.py"]
    assert result["agent_runtime_state"]["budget"]["verification_attempts_used"] == 2
    latest = store.load_latest("verification-cursor-run")
    assert latest is not None
    assert latest.safe_boundary == CheckpointBoundary.VERIFICATION_APPLIED
    assert latest.pending_verification is None


def test_runtime_controller_task_card_event_uses_active_task_id(tmp_path) -> None:
    analyzer = SimpleNamespace(
        analyze_goal=lambda goal: SimpleNamespace(
            task_type="document_summary",
            risk_level="low",
            required_resources=[],
            expected_deliverables=[],
        )
    )
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))
    runtime = SimpleNamespace(
        tool_registry=None,
        semantic_analyzer=analyzer,
        runtime_diagnostics_hooks=hooks,
        session_id="session-123",
    )
    executor = autonomous_iteration.runtime_controller._RuntimeSessionExecutor(runtime)

    semantic = executor._analyze_goal("Summarize project", task_id="task-123")

    assert semantic.task_type == "document_summary"
    run = hooks.recorder.load_run("session-123")
    assert run is not None
    events = hooks.recorder.load_trajectory_events(run.run_id)
    assert events[-1]["event_type"] == "task_card_ready"
    assert events[-1]["payload"]["output_summary"]["task_id"] == "task-123"
    assert events[-1]["payload"]["correlation"]["task_id"] == "task-123"
    assert events[-1]["payload"]["correlation"]["session_id"] == "session-123"


def test_runtime_controller_task_finished_event_uses_active_task_id(tmp_path) -> None:
    class FakeSessionRunner:
        def run(self, goal, context, mode="standard"):
            return {
                "success": True,
                "task_id": "subtask-finished-123",
                "stats": {"tasks_completed": 1, "tasks_failed": 0},
            }

    from runtime_diagnostics import DiagnosticRecorder
    from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks

    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))
    runtime = SimpleNamespace(tool_registry=None, runtime_diagnostics_hooks=hooks, session_id="session-finished-123")
    controller = AgentRuntimeController(runtime, session_executor=FakeSessionRunner())

    result = controller.run("Summarize app", {"project_path": "/tmp/project", "task_id": "root-task-finished-123"}, mode="standard")

    assert result["success"] is True
    run = hooks.recorder.load_run("root-task-finished-123")
    assert run is not None
    events = hooks.recorder.load_trajectory_events(run.run_id)
    finished = [event for event in events if event["event_type"] == "task_finished"][-1]
    assert finished["task_id"] == "root-task-finished-123"
    assert finished["payload"]["correlation"]["task_id"] == "root-task-finished-123"
    assert finished["payload"]["correlation"]["session_id"] == "session-finished-123"
