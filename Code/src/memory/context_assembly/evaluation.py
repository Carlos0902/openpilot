"""Deterministic offline quality checks for typed context assembly."""

from __future__ import annotations

from metadata import (
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextCandidate,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextQualityEvaluation,
    ContextQualityExpectation,
    ContextQualityIssueCode,
)


def evaluate_context_quality(
    candidates: list[ContextCandidate],
    result: ContextAssemblyResult,
    expectation: ContextQualityExpectation,
) -> ContextQualityEvaluation:
    """Compare one result with explicit expectations and structural invariants."""
    source = sorted(candidates, key=lambda item: (item.source_order, item.candidate_id))
    source_ids = [candidate.candidate_id for candidate in source]
    selected_ids = [candidate.candidate_id for candidate in result.selected_candidates]
    selected_set = set(selected_ids)
    decisions = {
        decision.candidate_id: decision
        for decision in result.selection.candidate_decisions
    }
    omitted_ids = [
        candidate_id
        for candidate_id in source_ids
        if candidate_id in decisions and decisions[candidate_id].action == "omitted"
    ]
    issues: dict[ContextQualityIssueCode, list[str]] = {}

    def add(code: ContextQualityIssueCode, candidate_ids: list[str] | None = None) -> None:
        issues.setdefault(code, [])
        for candidate_id in candidate_ids or []:
            if candidate_id not in issues[code]:
                issues[code].append(candidate_id)

    if (
        expectation.require_ready
        and result.selection.assembly_status != ContextAssemblyStatus.READY
    ):
        add(ContextQualityIssueCode.ASSEMBLY_NOT_READY)
    if len(result.prompt_text) > result.selection.max_prompt_chars:
        add(ContextQualityIssueCode.CHARACTER_BUDGET_EXCEEDED)

    missing_expected = [
        candidate_id
        for candidate_id in expectation.expected_selected_candidate_ids
        if candidate_id not in selected_set
    ]
    if missing_expected:
        add(ContextQualityIssueCode.EXPECTED_CANDIDATE_MISSING, missing_expected)
    forbidden_selected = [
        candidate_id
        for candidate_id in expectation.expected_omitted_candidate_ids
        if candidate_id in selected_set
    ]
    if forbidden_selected:
        add(ContextQualityIssueCode.FORBIDDEN_CANDIDATE_SELECTED, forbidden_selected)

    if set(source_ids) != set(decisions):
        add(
            ContextQualityIssueCode.DECISION_COVERAGE_INCOMPLETE,
            sorted(set(source_ids) ^ set(decisions)),
        )

    governed_reasons = {"duplicate", "conflict_precedence", "compacted"}
    for candidate in source:
        decision = decisions.get(candidate.candidate_id)
        if decision is None:
            continue
        if (
            candidate.retention == ContextCandidateRetention.REQUIRED
            and decision.action == "omitted"
            and not (
                decision.reason == "duplicate"
                and decision.governed_by_candidate_id in selected_set
            )
        ):
            add(
                ContextQualityIssueCode.REQUIRED_CANDIDATE_UNREPRESENTED,
                [candidate.candidate_id],
            )
        if decision.reason in governed_reasons and (
            not decision.governed_by_candidate_id
            or decision.governed_by_candidate_id not in selected_set
        ):
            add(
                ContextQualityIssueCode.GOVERNANCE_LINK_BROKEN,
                [candidate.candidate_id],
            )

    normalized_selected: dict[tuple[str, str], str] = {}
    for candidate in result.selected_candidates:
        key = (
            str(getattr(candidate.kind, "value", candidate.kind)),
            " ".join(candidate.content.split()),
        )
        if key in normalized_selected:
            add(
                ContextQualityIssueCode.DUPLICATE_SELECTED_CONTENT,
                [normalized_selected[key], candidate.candidate_id],
            )
        else:
            normalized_selected[key] = candidate.candidate_id

    if expectation.require_recent_dialog_suffix:
        dialog_ids = [
            candidate.candidate_id
            for candidate in source
            if candidate.kind == ContextCandidateKind.DIALOG
        ]
        selected_dialog_ids = [
            candidate.candidate_id
            for candidate in result.selected_candidates
            if candidate.kind == ContextCandidateKind.DIALOG
        ]
        expected_suffix = (
            dialog_ids[-len(selected_dialog_ids) :] if selected_dialog_ids else []
        )
        if selected_dialog_ids != expected_suffix:
            add(
                ContextQualityIssueCode.DIALOG_NOT_RECENT_SUFFIX,
                selected_dialog_ids,
            )

    for compactor in result.selected_candidates:
        if not compactor.compacted_candidate_ids:
            continue
        broken = [
            source_id
            for source_id in compactor.compacted_candidate_ids
            if source_id not in decisions
            or decisions[source_id].reason != "compacted"
            or decisions[source_id].governed_by_candidate_id != compactor.candidate_id
        ]
        if broken:
            add(ContextQualityIssueCode.COMPACTION_LINK_BROKEN, broken)

    issue_codes = list(issues)
    return ContextQualityEvaluation(
        passed=not issue_codes,
        issue_codes=issue_codes,
        issue_candidate_ids={code.value: ids for code, ids in issues.items()},
        selected_candidate_ids=selected_ids,
        omitted_candidate_ids=omitted_ids,
        character_budget_utilization=(
            len(result.prompt_text) / result.selection.max_prompt_chars
        ),
    )
