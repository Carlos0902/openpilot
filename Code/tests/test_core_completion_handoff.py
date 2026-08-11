from __future__ import annotations

from types import SimpleNamespace

import pytest

from autonomous_iteration.runtime_controller import (
    AgentRuntimeController,
    _RuntimeSessionExecutor,
)
from autonomous_iteration.core_completion_handoff import (
    compose_overall_success,
    evaluate_core_completion_handoff,
    runtime_state_source_hash,
)
from autonomous_iteration.core_completion_package import build_core_completion_package
from metadata import (
    AgentPhase,
    CheckpointBoundary,
    CoreAcceptanceAuthority,
    CoreAcceptanceDecision,
    CoreAcceptanceStatus,
    CoreCompletionReadinessReason,
    CoreCompletionPackageBuildStatus,
    CoreCompletionPackageView,
    DurableArtifactReference,
    PostCoreEligibilityReason,
    ProjectImprovementPolicy,
    ProjectImprovementRequirement,
    ProjectImprovementStatus,
    RuntimeCheckpointMetadata,
    RuntimeFinalizationCursor,
    RuntimeFinalizationStage,
    RuntimeReportMetadata,
    RuntimeStateMetadata,
    SessionConstraintAuthority,
    SessionConstraintCategory,
    SessionConstraintEntry,
    SessionConstraintSourceKind,
    SessionConstraintState,
    SessionConstraintValue,
    SessionExecutionCursor,
    SessionSemanticSnapshot,
    SessionStage,
    SessionTaskResult,
    TaskGraphNodeMetadata,
)


def _artifact(kind: str = "runtime_report") -> DurableArtifactReference:
    return DurableArtifactReference(
        artifact_id=f"{kind}-1",
        kind=kind,
        integrity_checksum="sha256:" + "a" * 64,
        bytes=128,
    )


def _state(**updates: object) -> RuntimeStateMetadata:
    state = RuntimeStateMetadata(
        goal="Repair calculator",
        phase=AgentPhase.SUMMARIZE,
        core_success=True,
        verification_status="passed",
        modified_files=["/workspace/project/calculator.py"],
        project_improvement_policy=ProjectImprovementPolicy(
            requirement=ProjectImprovementRequirement.OPTIONAL,
            target_successes=1,
            max_attempts=1,
        ),
        completion_reason="runtime session completed",
    )
    return state.model_copy(update=updates)


def _cursor(**updates: object) -> SessionExecutionCursor:
    cursor = SessionExecutionCursor(
        stage=SessionStage.COMPLETED,
        plan_hash="sha256:plan",
        semantic=SessionSemanticSnapshot(task_type="coding", risk_level="low"),
        original_task=TaskGraphNodeMetadata(
            task_id="root-task",
            description="Repair calculator",
            expected_outputs=["calculator.py passes tests"],
        ),
        tasks=[
            TaskGraphNodeMetadata(
                task_id="task-1",
                description="Repair calculator",
                write_files=["/workspace/project/calculator.py"],
                validation_command="pytest -q",
            )
        ],
        execution_order=["task-1"],
        next_task_index=1,
        results=[
            SessionTaskResult(
                task_id="task-1",
                status="completed",
                summary_text="repair verified",
                observed_modified_files=["/workspace/project/calculator.py"],
            )
        ],
    )
    return cursor.model_copy(update=updates)


def _finalized_sources(
    *,
    state: RuntimeStateMetadata | None = None,
    cursor: SessionExecutionCursor | None = None,
    side_effect_state: str = "none",
    pending_verification: object | None = None,
    project_fingerprint: dict[str, object] | None = None,
) -> tuple[RuntimeCheckpointMetadata, RuntimeReportMetadata]:
    state = state or _state()
    source_hash = runtime_state_source_hash(state)
    report_artifact = _artifact()
    finalization = RuntimeFinalizationCursor(
        finalization_id="finalization-1",
        stage=RuntimeFinalizationStage.RUN_FINALIZED,
        outcome="success",
        report_source_hash=source_hash,
        report_artifact=report_artifact,
        run_finalized_event_id="event-finalized",
    )
    checkpoint = RuntimeCheckpointMetadata(
        checkpoint_id="checkpoint-final",
        generation=5,
        run_id="run-1",
        root_task_id="root-task",
        session_id="session-1",
        checkpoint_reason="runtime finalization completed",
        safe_boundary=CheckpointBoundary.RUNTIME_FINALIZED,
        runtime_state=state,
        last_durable_event_id="event-finalized",
        last_durable_event_sequence=9,
        side_effect_state=side_effect_state,
        pending_verification=pending_verification,
        session_cursor=cursor or _cursor(),
        finalization_cursor=finalization,
        project_fingerprint=project_fingerprint
        or {
            "project_root": "/workspace/project",
            "cwd": "/workspace/project",
            "interpreter": "/workspace/project/.venv/bin/python",
            "environment_id": "environment-1",
            "target_file_hashes": {
                "/workspace/project/calculator.py": "sha256:" + "b" * 64,
            },
        },
        integrity_checksum="sha256:" + "c" * 64,
    )
    report = RuntimeReportMetadata(
        goal=state.goal,
        state_hash=source_hash,
        phase=state.phase,
        completion_reason=state.completion_reason,
        core_success=state.core_success,
        project_improvement_policy=state.project_improvement_policy,
        project_improvement_status=state.project_improvement_status,
        project_improvement_failure=state.project_improvement_failure,
        known_facts=list(state.known_facts),
        unresolved_questions=list(state.unknowns),
        modified_files=list(state.modified_files),
        verification_status=state.verification_status,
        diagnostic_conflicts=list(state.diagnostic_conflicts),
        diagnostic_risks=list(state.diagnostic_risks),
        diagnostic_decisions=list(state.diagnostic_decisions),
        core_acceptance_decisions=list(state.core_acceptance_decisions),
        residual_risks=[],
    )
    return checkpoint, report


def test_complete_finalized_sources_are_ready_and_eligible() -> None:
    checkpoint, report = _finalized_sources()

    view = evaluate_core_completion_handoff(checkpoint=checkpoint, report=report)

    assert view.ready is True
    assert view.readiness_reason == CoreCompletionReadinessReason.READY
    assert view.post_core_eligible is True
    assert view.eligibility_reason == PostCoreEligibilityReason.ELIGIBLE
    assert view.source_references is not None
    assert view.source_references.checkpoint_id == checkpoint.checkpoint_id
    assert view.source_references.report_artifact == checkpoint.finalization_cursor.report_artifact
    assert view.source_references.task_result_ids == ("task-1",)
    assert view.source_references.modified_files == ("/workspace/project/calculator.py",)

    restored = type(view).model_validate_json(view.model_dump_json())
    assert restored == view
    assert restored.source_references is not checkpoint.project_fingerprint


@pytest.mark.parametrize(
    ("checkpoint_update", "state_update", "cursor", "reason"),
    [
        ({}, {"core_success": False}, None, CoreCompletionReadinessReason.CORE_INCOMPLETE),
        ({}, {}, _cursor(tasks=[], execution_order=[], next_task_index=0, results=[]), CoreCompletionReadinessReason.EMPTY_TASK_PLAN),
        ({}, {}, _cursor(stage=SessionStage.TASK_EXECUTION), CoreCompletionReadinessReason.TASK_RESULTS_INCOMPLETE),
        ({}, {"verification_status": "failed"}, None, CoreCompletionReadinessReason.VERIFICATION_INCOMPLETE),
        ({"side_effect_state": "indeterminate"}, {}, None, CoreCompletionReadinessReason.SIDE_EFFECT_UNRESOLVED),
    ],
)
def test_incomplete_or_indeterminate_core_sources_fail_closed(
    checkpoint_update: dict[str, object],
    state_update: dict[str, object],
    cursor: SessionExecutionCursor | None,
    reason: CoreCompletionReadinessReason,
) -> None:
    checkpoint, report = _finalized_sources(state=_state(**state_update), cursor=cursor)
    if checkpoint_update:
        checkpoint = checkpoint.model_copy(update=checkpoint_update)

    view = evaluate_core_completion_handoff(checkpoint=checkpoint, report=report)

    assert view.ready is False
    assert view.readiness_reason == reason
    assert view.post_core_eligible is False
    assert view.eligibility_reason == PostCoreEligibilityReason.CORE_NOT_READY
    assert view.source_references is None


def test_response_evidence_and_read_only_analysis_are_never_eligible() -> None:
    evidence_state = _state(
        task_purpose="response_evidence",
        execution_mode="read_only",
        core_success=None,
        modified_files=[],
        verification_status="not_required",
    )
    checkpoint, report = _finalized_sources(state=evidence_state)
    evidence_view = evaluate_core_completion_handoff(checkpoint=checkpoint, report=report)

    assert evidence_view.readiness_reason == CoreCompletionReadinessReason.NOT_PROJECT_TASK
    assert evidence_view.post_core_eligible is False

    analysis_state = _state(
        execution_mode="read_only",
        modified_files=[],
        verification_status="not_required",
    )
    checkpoint, report = _finalized_sources(
        state=analysis_state,
        cursor=_cursor(
            tasks=[
                TaskGraphNodeMetadata(
                    task_id="task-1",
                    description="Inspect project",
                    task_kind="analysis",
                )
            ],
            results=[SessionTaskResult(task_id="task-1", status="completed")],
        ),
    )
    analysis_view = evaluate_core_completion_handoff(checkpoint=checkpoint, report=report)

    assert analysis_view.ready is True
    assert analysis_view.post_core_eligible is False
    assert analysis_view.eligibility_reason == PostCoreEligibilityReason.READ_ONLY_OR_NO_OUTPUT


def test_report_or_state_identity_conflict_fails_closed() -> None:
    checkpoint, report = _finalized_sources()
    stale_report = report.model_copy(update={"state_hash": "sha256:" + "d" * 64})

    view = evaluate_core_completion_handoff(checkpoint=checkpoint, report=stale_report)

    assert view.readiness_reason == CoreCompletionReadinessReason.REPORT_SOURCE_MISMATCH


def test_final_event_and_project_file_identity_must_match_sources() -> None:
    checkpoint, report = _finalized_sources()
    event_mismatch = checkpoint.model_copy(update={"last_durable_event_id": "other-event"})
    assert (
        evaluate_core_completion_handoff(checkpoint=event_mismatch, report=report).readiness_reason
        == CoreCompletionReadinessReason.FINALIZATION_INCOMPLETE
    )

    missing_file_hash = checkpoint.model_copy(
        update={
            "project_fingerprint": checkpoint.project_fingerprint.model_copy(
                update={"target_file_hashes": {}}
            )
        }
    )
    assert (
        evaluate_core_completion_handoff(checkpoint=missing_file_hash, report=report).readiness_reason
        == CoreCompletionReadinessReason.PROJECT_IDENTITY_INCOMPLETE
    )


def test_source_reference_projection_fails_closed_at_exact_bound() -> None:
    files = [f"/workspace/project/file-{index}.py" for index in range(513)]
    state = _state(modified_files=files)
    cursor = _cursor(
        results=[
            SessionTaskResult(
                task_id="task-1",
                status="completed",
                observed_modified_files=files,
            )
        ]
    )
    checkpoint, report = _finalized_sources(
        state=state,
        cursor=cursor,
        project_fingerprint={
            "project_root": "/workspace/project",
            "cwd": "/workspace/project",
            "interpreter": "/workspace/project/.venv/bin/python",
            "environment_id": "environment-1",
            "target_file_hashes": {path: "sha256:" + "b" * 64 for path in files},
        },
    )

    view = evaluate_core_completion_handoff(checkpoint=checkpoint, report=report)

    assert view.readiness_reason == CoreCompletionReadinessReason.SOURCE_BOUNDS_EXCEEDED


def test_active_acceptance_requires_typed_pass_or_authorized_waiver() -> None:
    acceptance = SessionConstraintEntry(
        constraint_id="acceptance-1",
        constraint_key="acceptance",
        category=SessionConstraintCategory.GOAL_ACCEPTANCE,
        value=SessionConstraintValue(acceptance_criteria=["all calculator tests pass"]),
        statement="All calculator tests pass.",
        source_kind=SessionConstraintSourceKind.USER_MESSAGE,
        source_id="message-1",
        source_turn_index=1,
        source_hash="sha256:" + "e" * 64,
        authority=SessionConstraintAuthority.EXPLICIT_USER,
    )
    constraints = SessionConstraintState(
        session_id="session-1",
        processed_through_turn=1,
        entries=[acceptance],
    )
    checkpoint, report = _finalized_sources(state=_state(session_constraints=constraints))

    unresolved = evaluate_core_completion_handoff(checkpoint=checkpoint, report=report)
    assert unresolved.readiness_reason == CoreCompletionReadinessReason.ACCEPTANCE_UNRESOLVED

    passed_state = _state(
        session_constraints=constraints,
        core_acceptance_decisions=[
            CoreAcceptanceDecision(
                acceptance_id="acceptance-1",
                status=CoreAcceptanceStatus.PASSED,
                authority=CoreAcceptanceAuthority.RUNTIME_VERIFICATION,
                source_ref="validation:pytest-q",
                evidence_refs=("tool-result:test-1",),
            )
        ],
    )
    checkpoint, report = _finalized_sources(state=passed_state)
    assert evaluate_core_completion_handoff(checkpoint=checkpoint, report=report).ready is True

    waived_state = _state(
        session_constraints=constraints,
        core_acceptance_decisions=[
            CoreAcceptanceDecision(
                acceptance_id="acceptance-1",
                status=CoreAcceptanceStatus.WAIVED,
                authority=CoreAcceptanceAuthority.USER,
                source_ref="message:waiver-1",
                waiver_reason_code="user_explicitly_waived",
            )
        ],
    )
    checkpoint, report = _finalized_sources(state=waived_state)
    assert evaluate_core_completion_handoff(checkpoint=checkpoint, report=report).ready is True


def test_acceptance_contract_rejects_provider_or_unbound_waiver() -> None:
    with pytest.raises(ValueError, match="waiver authority"):
        CoreAcceptanceDecision(
            acceptance_id="acceptance-1",
            status=CoreAcceptanceStatus.WAIVED,
            authority=CoreAcceptanceAuthority.RUNTIME_VERIFICATION,
            source_ref="provider:text",
            waiver_reason_code="user_explicitly_waived",
        )
    with pytest.raises(ValueError, match="evidence"):
        CoreAcceptanceDecision(
            acceptance_id="acceptance-1",
            status=CoreAcceptanceStatus.PASSED,
            authority=CoreAcceptanceAuthority.RUNTIME_VERIFICATION,
            source_ref="validation:pytest-q",
        )


@pytest.mark.parametrize(
    ("requirement", "status", "core_success", "expected"),
    [
        (ProjectImprovementRequirement.DISABLED, ProjectImprovementStatus.NOT_REQUESTED, True, True),
        (ProjectImprovementRequirement.OPTIONAL, ProjectImprovementStatus.FAILED, True, True),
        (ProjectImprovementRequirement.OPTIONAL, ProjectImprovementStatus.INTERRUPTED, True, True),
        (ProjectImprovementRequirement.REQUIRED, ProjectImprovementStatus.SUCCEEDED, True, True),
        (ProjectImprovementRequirement.REQUIRED, ProjectImprovementStatus.FAILED, True, False),
        (ProjectImprovementRequirement.REQUIRED, ProjectImprovementStatus.SKIPPED, True, False),
        (ProjectImprovementRequirement.OPTIONAL, ProjectImprovementStatus.SUCCEEDED, False, False),
    ],
)
def test_overall_success_composes_core_policy_and_stage_without_rewriting_core(
    requirement: ProjectImprovementRequirement,
    status: ProjectImprovementStatus,
    core_success: bool,
    expected: bool,
) -> None:
    policy = (
        ProjectImprovementPolicy(requirement="disabled", target_successes=0, max_attempts=0)
        if requirement == ProjectImprovementRequirement.DISABLED
        else ProjectImprovementPolicy(requirement=requirement, target_successes=1, max_attempts=1)
    )

    assert compose_overall_success(
        core_success=core_success,
        policy=policy,
        improvement_status=status,
    ) is expected


def test_disabled_policy_is_ready_but_not_eligible() -> None:
    state = _state(
        project_improvement_policy=ProjectImprovementPolicy(
            requirement="disabled", target_successes=0, max_attempts=0
        )
    )
    checkpoint, report = _finalized_sources(state=state)

    view = evaluate_core_completion_handoff(checkpoint=checkpoint, report=report)

    assert view.ready is True
    assert view.post_core_eligible is False
    assert view.eligibility_reason == PostCoreEligibilityReason.POLICY_DISABLED


def test_integration_lane_composes_required_skipped_stage_without_rewriting_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENPILOT_CORE_POST_CORE_INTEGRATION", "1")
    state = _state(
        project_improvement_policy=ProjectImprovementPolicy(
            requirement="required", target_successes=1, max_attempts=1
        ),
        project_improvement_status="skipped",
    )
    checkpoint, report = _finalized_sources(state=state)
    controller = AgentRuntimeController(SimpleNamespace(tool_registry=None))
    controller._last_persisted_checkpoint = checkpoint

    result = controller._runtime_result(
        state,
        {"success": True, "core_success": True},
        report=report,
        emit_task_finished=False,
    )

    assert result["core_success"] is True
    assert result["project_improvement_status"] == "skipped"
    assert result["overall_success"] is False
    assert result["success"] is False
    assert result["core_completion_handoff"]["ready"] is True
    assert result["core_completion_package_build"]["status"] == "built"
    assert result["core_completion_package"]["package_id"].startswith("sha256:")


def test_integration_lane_defers_legacy_improvement_before_verified_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENPILOT_CORE_POST_CORE_INTEGRATION", "1")
    state = _state()
    runtime = SimpleNamespace(
        runtime_controller=SimpleNamespace(state=state),
        project_improvement_policy=state.project_improvement_policy,
        enable_iterative_improvement=True,
        required_successful_improvements=1,
        enhanced_ui=None,
        _finalize_project_readme=lambda _goal, _results: (_ for _ in ()).throw(
            AssertionError("post-processing must wait for verified handoff scope")
        ),
        _collect_written_files=lambda _results: ["/workspace/project/calculator.py"],
        _infer_project_path_from_files=lambda _goal, _files: "/workspace/project",
        _run_iterative_improvement=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy improvement must not run")
        ),
    )

    _, written_files, project_path, improvement = _RuntimeSessionExecutor(
        runtime
    )._finalize_project_outputs("Repair calculator", [], True)

    assert written_files == ["/workspace/project/calculator.py"]
    assert project_path == "/workspace/project"
    assert improvement is None


def test_unique_package_builder_is_deterministic_and_source_linked() -> None:
    checkpoint, report = _finalized_sources()

    first = build_core_completion_package(checkpoint=checkpoint, report=report)
    second = build_core_completion_package(checkpoint=checkpoint, report=report)

    assert first.status == CoreCompletionPackageBuildStatus.BUILT
    assert first.package is not None
    assert second.package == first.package
    assert first.package.package_id.startswith("sha256:")
    assert first.package.source_references.checkpoint_id == checkpoint.checkpoint_id
    assert first.package.goal == "Repair calculator"
    assert first.package.execution_mode == "mutation_allowed"
    assert first.package.verification_status == "passed"
    assert first.package.verification_commands == ("pytest -q",)
    assert first.package.covered_task_ids == ("task-1",)
    assert first.package.modified_files == ("/workspace/project/calculator.py",)

    restored = CoreCompletionPackageView.model_validate_json(
        first.package.model_dump_json()
    )
    assert restored == first.package


def test_package_builder_rejects_unready_or_ineligible_sources_without_partial_package() -> None:
    checkpoint, report = _finalized_sources(state=_state(core_success=False))
    unready = build_core_completion_package(checkpoint=checkpoint, report=report)
    assert unready.status == CoreCompletionPackageBuildStatus.CORE_NOT_READY
    assert unready.package is None

    checkpoint, report = _finalized_sources(
        state=_state(
            execution_mode="read_only",
            modified_files=[],
            verification_status="not_required",
        ),
        cursor=_cursor(
            tasks=[
                TaskGraphNodeMetadata(
                    task_id="task-1",
                    description="Inspect project",
                    task_kind="analysis",
                )
            ],
            results=[SessionTaskResult(task_id="task-1", status="completed")],
        ),
    )
    ineligible = build_core_completion_package(checkpoint=checkpoint, report=report)
    assert ineligible.status == CoreCompletionPackageBuildStatus.POST_CORE_INELIGIBLE
    assert ineligible.package is None


def test_package_cross_branch_json_contract_rejects_unknown_or_changed_source() -> None:
    checkpoint, report = _finalized_sources()
    built = build_core_completion_package(checkpoint=checkpoint, report=report)
    assert built.package is not None
    payload = built.package.model_dump(mode="json")
    payload["integration_branch_claim"] = "second truth"

    with pytest.raises(ValueError):
        CoreCompletionPackageView.model_validate(payload)

    stale_report = report.model_copy(update={"state_hash": "sha256:" + "f" * 64})
    stale = build_core_completion_package(checkpoint=checkpoint, report=stale_report)
    assert stale.status == CoreCompletionPackageBuildStatus.CORE_NOT_READY
    assert stale.package is None
