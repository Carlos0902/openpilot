"""Deterministic first-pass judgment for runtime diagnostic signals."""

from __future__ import annotations

from metadata import ProblemJudgmentMetadata, ProblemSignalMetadata


_BLOCKING_CATEGORIES = {"environment", "tool_execution", "verification", "state_transition"}
_REVIEW_CATEGORIES = {"task_understanding", "planning", "tool_routing", "suspicious_success"}


def judge_signal(signal: ProblemSignalMetadata) -> ProblemJudgmentMetadata:
    """Conservatively judge whether a signal is a real diagnostic problem.

    This judgment is deliberately rule-based in Phase 1. It is a triage result,
    not a root-cause conclusion.
    """
    category = signal.category
    evidence_count = len(signal.evidence or [])

    if category in _BLOCKING_CATEGORIES:
        return ProblemJudgmentMetadata(
            is_problem=True,
            severity="blocking" if category in {"environment", "verification"} else "high",
            requires_fix=True,
            user_visible=True,
            recommended_repair_kind=_repair_kind(category),
            confidence=0.75 if evidence_count else 0.6,
            reason=f"{category} signal usually blocks or invalidates reliable task completion.",
        )

    if category in _REVIEW_CATEGORIES:
        return ProblemJudgmentMetadata(
            is_problem=True,
            severity="review",
            requires_fix=False,
            user_visible=True,
            recommended_repair_kind=_repair_kind(category),
            confidence=0.6 if evidence_count else 0.45,
            reason=f"{category} signal needs human review before treating it as a root problem.",
        )

    return ProblemJudgmentMetadata(
        is_problem=True,
        severity="unknown",
        requires_fix=False,
        user_visible=True,
        recommended_repair_kind="human_review",
        confidence=0.4,
        reason="Unknown diagnostic category; preserve evidence for review.",
    )


def _repair_kind(category: str) -> str:
    return {
        "environment": "environment_check",
        "task_understanding": "clarify_or_record_assumption",
        "planning": "planning_review",
        "tool_routing": "routing_review",
        "tool_execution": "tool_failure_review",
        "verification": "verification_review",
        "state_transition": "state_machine_review",
        "suspicious_success": "completion_evidence_review",
    }.get(category, "human_review")
