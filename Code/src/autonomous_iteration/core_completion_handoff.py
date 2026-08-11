"""Fail-closed core source readiness for the post-core integration boundary."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex

from metadata import (
    ActiveDiagnosticItemStatus,
    AgentPhase,
    CheckpointBoundary,
    CoreAcceptanceStatus,
    CoreCompletionHandoffView,
    CoreCompletionReadinessReason,
    CoreCompletionSourceReferences,
    PostCoreEligibilityReason,
    ProjectImprovementPolicy,
    ProjectImprovementRequirement,
    ProjectImprovementStatus,
    RuntimeCheckpointMetadata,
    RuntimeExecutionMode,
    RuntimeFinalizationStage,
    RuntimeReportMetadata,
    RuntimeStateMetadata,
    RuntimeTaskPurpose,
    SessionConstraintCategory,
    SessionStage,
    VerificationStatus,
)


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _is_sha256(value: str) -> bool:
    digest = str(value or "").removeprefix("sha256:")
    return (
        str(value or "").startswith("sha256:")
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )


def runtime_state_source_hash(state: RuntimeStateMetadata) -> str:
    """Hash task outcome facts while excluding resume-attempt bookkeeping."""

    payload = state.to_json_dict()
    for field_name in (
        "recovery_status",
        "recovery_reason_code",
        "active_resume_attempt_id",
    ):
        payload.pop(field_name, None)
    budget = payload.get("budget")
    if isinstance(budget, dict):
        budget.pop("recovery_rounds_used", None)
    return _canonical_hash(payload)


def core_post_core_integration_enabled() -> bool:
    """Return whether the verified-handoff lane replaces legacy post-core entry."""

    value = str(os.getenv("OPENPILOT_CORE_POST_CORE_INTEGRATION", "0")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _not_ready(reason: CoreCompletionReadinessReason) -> CoreCompletionHandoffView:
    return CoreCompletionHandoffView(
        ready=False,
        readiness_reason=reason,
        post_core_eligible=False,
        eligibility_reason=PostCoreEligibilityReason.CORE_NOT_READY,
    )


def _requires_python_environment(checkpoint: RuntimeCheckpointMetadata) -> bool:
    cursor = checkpoint.session_cursor
    if cursor is None:
        return False
    for task in cursor.tasks:
        command = str(task.validation_command or "").strip()
        try:
            argv = shlex.split(command)
        except ValueError:
            continue
        if argv and Path(argv[0]).name.lower() in {
            "python",
            "python3",
            "pytest",
            "py.test",
            "tox",
            "nox",
            "pip",
            "pip3",
        }:
            return True
    return False


def _required_acceptance_ids(state: RuntimeStateMetadata) -> set[str]:
    return {
        entry.constraint_id
        for entry in state.session_constraints.active_entries
        if entry.category == SessionConstraintCategory.GOAL_ACCEPTANCE
    }


def _acceptance_is_complete(state: RuntimeStateMetadata) -> bool:
    required_ids = _required_acceptance_ids(state)
    if not required_ids:
        return True
    decisions = {
        decision.acceptance_id: decision
        for decision in state.core_acceptance_decisions
    }
    if not required_ids.issubset(decisions):
        return False
    return all(
        decision.status in {CoreAcceptanceStatus.PASSED, CoreAcceptanceStatus.WAIVED}
        for acceptance_id, decision in decisions.items()
        if acceptance_id in required_ids
    )


def _has_blocking_residual_risk(
    state: RuntimeStateMetadata,
    report: RuntimeReportMetadata,
) -> bool:
    if state.unknowns or report.unresolved_questions or report.residual_risks:
        return True
    if any(
        item.status == ActiveDiagnosticItemStatus.OPEN
        for item in state.diagnostic_conflicts
    ):
        return True
    return any(
        item.status == ActiveDiagnosticItemStatus.OPEN and item.blocking
        for item in state.diagnostic_risks
    )


def _report_matches_state(
    state: RuntimeStateMetadata,
    report: RuntimeReportMetadata,
) -> bool:
    return all(
        (
            report.goal == state.goal,
            report.phase == state.phase,
            report.core_success == state.core_success,
            report.verification_status == state.verification_status,
            report.modified_files == state.modified_files,
            report.project_improvement_policy == state.project_improvement_policy,
            report.project_improvement_status == state.project_improvement_status,
            report.core_acceptance_decisions == state.core_acceptance_decisions,
        )
    )


def evaluate_core_completion_handoff(
    *,
    checkpoint: RuntimeCheckpointMetadata | None,
    report: RuntimeReportMetadata | None,
) -> CoreCompletionHandoffView:
    """Derive a bounded handoff view without constructing a completion package."""

    if checkpoint is None or report is None:
        return _not_ready(CoreCompletionReadinessReason.SOURCES_UNAVAILABLE)
    state = checkpoint.runtime_state
    if state.task_purpose != RuntimeTaskPurpose.PROJECT_TASK:
        return _not_ready(CoreCompletionReadinessReason.NOT_PROJECT_TASK)
    if state.core_success is not True:
        return _not_ready(CoreCompletionReadinessReason.CORE_INCOMPLETE)

    cursor = checkpoint.session_cursor
    if cursor is None or not cursor.tasks or not cursor.execution_order:
        return _not_ready(CoreCompletionReadinessReason.EMPTY_TASK_PLAN)
    task_ids = tuple(cursor.execution_order)
    results_by_id = {item.task_id: item for item in cursor.results}
    observed_modified_files = {
        path
        for item in cursor.results
        for path in item.observed_modified_files
    }
    if (
        cursor.stage != SessionStage.COMPLETED
        or cursor.next_task_index != len(task_ids)
        or set(results_by_id) != set(task_ids)
        or any(results_by_id[task_id].status != "completed" for task_id in task_ids)
        or not set(state.modified_files).issubset(observed_modified_files)
        or state.phase != AgentPhase.SUMMARIZE
    ):
        return _not_ready(CoreCompletionReadinessReason.TASK_RESULTS_INCOMPLETE)
    if (
        state.verification_status
        not in {VerificationStatus.PASSED, VerificationStatus.NOT_REQUIRED}
        or checkpoint.pending_verification is not None
    ):
        return _not_ready(CoreCompletionReadinessReason.VERIFICATION_INCOMPLETE)
    if not _acceptance_is_complete(state):
        return _not_ready(CoreCompletionReadinessReason.ACCEPTANCE_UNRESOLVED)
    if checkpoint.side_effect_state in {"prepared", "observed", "indeterminate"}:
        return _not_ready(CoreCompletionReadinessReason.SIDE_EFFECT_UNRESOLVED)

    finalization = checkpoint.finalization_cursor
    if (
        checkpoint.safe_boundary != CheckpointBoundary.RUNTIME_FINALIZED
        or finalization is None
        or finalization.stage != RuntimeFinalizationStage.RUN_FINALIZED
        or finalization.outcome != "success"
        or finalization.report_artifact is None
        or finalization.report_artifact.kind != "runtime_report"
        or not _is_sha256(finalization.report_artifact.integrity_checksum)
        or not finalization.run_finalized_event_id
        or not _is_sha256(checkpoint.integrity_checksum)
        or checkpoint.last_durable_event_id != finalization.run_finalized_event_id
    ):
        return _not_ready(CoreCompletionReadinessReason.FINALIZATION_INCOMPLETE)

    state_hash = runtime_state_source_hash(state)
    if (
        finalization.report_source_hash != state_hash
        or report.state_hash != state_hash
        or not _report_matches_state(state, report)
    ):
        return _not_ready(CoreCompletionReadinessReason.REPORT_SOURCE_MISMATCH)

    fingerprint = checkpoint.project_fingerprint
    project_root = Path(fingerprint.project_root).expanduser().resolve(strict=False)
    project_cwd = Path(str(fingerprint.cwd or "")).expanduser().resolve(strict=False)
    if (
        not fingerprint.project_root
        or not fingerprint.cwd
        or str(project_root) != fingerprint.project_root
        or str(project_cwd) != fingerprint.cwd
        or not project_cwd.is_relative_to(project_root)
        or any(
            not _is_sha256(str(fingerprint.target_file_hashes.get(path, "")))
            for path in state.modified_files
        )
        or any(
            Path(path).expanduser().resolve(strict=False).as_posix() != path
            or not Path(path).is_relative_to(project_root)
            for path in state.modified_files
        )
    ):
        return _not_ready(CoreCompletionReadinessReason.PROJECT_IDENTITY_INCOMPLETE)
    if _requires_python_environment(checkpoint) and (
        not fingerprint.interpreter or not fingerprint.environment_id
    ):
        return _not_ready(CoreCompletionReadinessReason.ENVIRONMENT_IDENTITY_INCOMPLETE)
    if _has_blocking_residual_risk(state, report):
        return _not_ready(CoreCompletionReadinessReason.BLOCKING_RESIDUAL_RISK)
    required_acceptance_ids = _required_acceptance_ids(state)
    if (
        len(task_ids) > 256
        or len(state.modified_files) > 512
        or len(required_acceptance_ids) > 64
        or len(state.modified_files) != len(set(state.modified_files))
        or any(
            not value.strip() or len(value) > 4096
            for value in (*task_ids, *state.modified_files, *required_acceptance_ids)
        )
    ):
        return _not_ready(CoreCompletionReadinessReason.SOURCE_BOUNDS_EXCEEDED)

    references = CoreCompletionSourceReferences(
        run_id=checkpoint.run_id,
        root_task_id=checkpoint.root_task_id,
        checkpoint_id=checkpoint.checkpoint_id,
        checkpoint_checksum=checkpoint.integrity_checksum,
        runtime_state_hash=state_hash,
        report_artifact=finalization.report_artifact,
        run_finalized_event_id=finalization.run_finalized_event_id,
        project_fingerprint_hash=_canonical_hash(fingerprint.model_dump(mode="json")),
        task_result_ids=task_ids,
        acceptance_decision_ids=tuple(
            item.acceptance_id
            for item in state.core_acceptance_decisions
            if item.acceptance_id in required_acceptance_ids
        ),
        modified_files=tuple(state.modified_files),
    )
    if not state.project_improvement_policy.enabled:
        return CoreCompletionHandoffView(
            ready=True,
            readiness_reason=CoreCompletionReadinessReason.READY,
            post_core_eligible=False,
            eligibility_reason=PostCoreEligibilityReason.POLICY_DISABLED,
            source_references=references,
        )
    if (
        state.execution_mode != RuntimeExecutionMode.MUTATION_ALLOWED
        or not state.modified_files
    ):
        return CoreCompletionHandoffView(
            ready=True,
            readiness_reason=CoreCompletionReadinessReason.READY,
            post_core_eligible=False,
            eligibility_reason=PostCoreEligibilityReason.READ_ONLY_OR_NO_OUTPUT,
            source_references=references,
        )
    return CoreCompletionHandoffView(
        ready=True,
        readiness_reason=CoreCompletionReadinessReason.READY,
        post_core_eligible=True,
        eligibility_reason=PostCoreEligibilityReason.ELIGIBLE,
        source_references=references,
    )


def compose_overall_success(
    *,
    core_success: bool,
    policy: ProjectImprovementPolicy,
    improvement_status: ProjectImprovementStatus,
) -> bool:
    """Compose overall success without rewriting either source outcome."""

    if not core_success:
        return False
    if policy.requirement != ProjectImprovementRequirement.REQUIRED:
        return True
    return improvement_status in {
        ProjectImprovementStatus.ACCEPTED,
        ProjectImprovementStatus.SUCCEEDED,
    }


__all__ = [
    "compose_overall_success",
    "core_post_core_integration_enabled",
    "evaluate_core_completion_handoff",
    "runtime_state_source_hash",
]
