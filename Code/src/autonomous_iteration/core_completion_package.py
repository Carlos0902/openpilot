"""Unique builder for the post-core Core Completion Package derived view."""

from __future__ import annotations

import hashlib
import json

from autonomous_iteration.core_completion_handoff import evaluate_core_completion_handoff
from metadata import (
    CoreCompletionBudgetSummary,
    CoreCompletionPackageBuildResult,
    CoreCompletionPackageBuildStatus,
    CoreCompletionPackageView,
    RuntimeCheckpointMetadata,
    RuntimeReportMetadata,
)


def _package_id(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_core_completion_package(
    *,
    checkpoint: RuntimeCheckpointMetadata | None,
    report: RuntimeReportMetadata | None,
) -> CoreCompletionPackageBuildResult:
    """Build one deterministic package only from ready, eligible core sources."""

    handoff = evaluate_core_completion_handoff(checkpoint=checkpoint, report=report)
    if not handoff.ready:
        return CoreCompletionPackageBuildResult(
            status=CoreCompletionPackageBuildStatus.CORE_NOT_READY,
            handoff=handoff,
        )
    if not handoff.post_core_eligible:
        return CoreCompletionPackageBuildResult(
            status=CoreCompletionPackageBuildStatus.POST_CORE_INELIGIBLE,
            handoff=handoff,
        )
    if checkpoint is None or handoff.source_references is None:
        raise AssertionError("eligible handoff must retain its validated checkpoint sources")

    state = checkpoint.runtime_state
    cursor = checkpoint.session_cursor
    if cursor is None:
        raise AssertionError("eligible handoff must retain its validated session cursor")
    verification_commands = tuple(
        dict.fromkeys(
            command
            for task in cursor.tasks
            if (command := str(task.validation_command or "").strip())
        )
    )
    package_payload: dict[str, object] = {
        "schema_version": "core-completion-package-v1",
        "ready": True,
        "post_core_eligible": True,
        "source_references": handoff.source_references.model_dump(mode="json"),
        "goal": state.goal,
        "execution_mode": state.execution_mode,
        "verification_status": state.verification_status,
        "project_root": checkpoint.project_fingerprint.project_root,
        "environment_id": checkpoint.project_fingerprint.environment_id,
        "covered_task_ids": handoff.source_references.task_result_ids,
        "modified_files": handoff.source_references.modified_files,
        "acceptance_decision_ids": handoff.source_references.acceptance_decision_ids,
        "verification_commands": verification_commands,
        "diagnostic_decision_ids": tuple(
            item.decision_id for item in state.diagnostic_decisions
        ),
        "budget_summary": CoreCompletionBudgetSummary(
            tool_calls_used=state.budget.tool_calls_used,
            file_reads_used=state.budget.file_reads_used,
            file_edits_used=state.budget.file_edits_used,
            file_creates_used=state.budget.file_creates_used,
            verification_attempts_used=state.budget.verification_attempts_used,
            recovery_rounds_used=state.budget.recovery_rounds_used,
            replan_rounds_used=state.budget.replan_rounds_used,
            provider_completion_tokens_used=state.budget.tool_event_completion_tokens_used,
        ).model_dump(mode="json"),
    }
    try:
        package = CoreCompletionPackageView(
            package_id=_package_id(package_payload),
            **package_payload,
        )
    except ValueError:
        return CoreCompletionPackageBuildResult(
            status=CoreCompletionPackageBuildStatus.SOURCE_PROJECTION_INVALID,
            handoff=handoff,
        )
    return CoreCompletionPackageBuildResult(
        status=CoreCompletionPackageBuildStatus.BUILT,
        handoff=handoff,
        package=package,
    )


__all__ = ["build_core_completion_package"]
