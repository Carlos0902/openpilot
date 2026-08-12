"""Purpose-specific context projections for autonomous project improvement."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Literal

from autonomous_iteration.models import ImprovementGoal, ProjectStateSnapshot
from memory.session_constraints import build_session_constraint_candidate
from memory.session_dialog import build_session_turn_candidates, session_turn_ledger_hash
from metadata import (
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    SessionConstraintState,
    SessionIngressState,
    DerivedContextProjection,
)


_TASK_DESIGN_SCHEMA = {
    "task": {
        "description": "specific implementation task",
        "target_files": ["path"],
        "acceptance_criteria": ["observable criterion"],
        "risk_notes": ["risk or empty"],
        "evidence_ids": [
            "exact value copied from an [evidence_id=\"...\"] header; a goal ID, "
            "diagnosis candidate ID, task ID, source ID, or ID found inside candidate "
            "content must not be returned as evidence"
        ],
    }
}

_PROJECT_IMPROVEMENT_ANALYSIS_SCHEMA = {
    "changed_signals": ["new or changed signal only"],
    "proposed_actions": ["prioritized concrete action"],
    "next_decision_or_goal": "one focused next decision or goal",
    "must_satisfy": ["observable acceptance point"],
    "blocking_risks": ["risk or empty"],
    "evidence_ids": ["source evidence id"],
    "stack_preset_patch": {},
}

_ITERATION_GOAL_SCHEMA = {
    "summary": "short public goal-selection assessment",
    "goals": [
        {
            "title": "specific goal",
            "category": "feature|ux|robustness|code_quality|documentation",
            "rationale": "public reason",
            "acceptance_criteria": ["observable criterion"],
            "priority": "high|medium|low",
        }
    ],
}

_MEMORY_CONTENT_LIMIT = 360
_MEMORY_ATTRIBUTE_KEYS = (
    "project_path",
    "iteration",
    "selected_candidate",
    "selected_candidate_id",
    "selected_dimension",
    "success",
    "unmet_metrics",
    "detected_packages",
    "installed_packages",
    "dependency_source",
    "run_command",
    "python_version",
)
_TASK_DESIGN_MEMORY_LIMIT = 3
TaskDesignProjectionPolicy = Literal["current", "compact"]


def build_iteration_task_design_candidates(
    *,
    project_state: ProjectStateSnapshot,
    goal: ImprovementGoal,
    improvement_report: Mapping[str, Any],
    completed_iteration: int,
    projection_policy: TaskDesignProjectionPolicy = "current",
    session_constraints: SessionConstraintState | None = None,
    session_ingress_state: SessionIngressState | None = None,
    context_projection: DerivedContextProjection | None = None,
) -> list[ContextCandidate]:
    """Project one Task Designer request into independently governed candidates.

    Source models remain authoritative. This adapter only creates bounded required
    views and source-linked optional evidence for one model request.
    """

    if projection_policy not in {"current", "compact"}:
        raise ValueError(f"Unsupported Task Designer projection policy: {projection_policy}")

    prefix = "iteration_task_design"
    candidates = [
        _required_candidate(
            candidate_id="iteration_task_design:instruction",
            kind=ContextCandidateKind.INSTRUCTION,
            source_id="task_designer:instruction:v1",
            content=(
                "You are OpenPilot's Task Designer Agent. Convert the selected "
                "improvement goal into 1-2 specific implementation tasks. Return "
                "only valid JSON. Preserve the stated safety constraints and do "
                "not silently change the delivery surface, language, or framework. "
                "Evidence IDs must be exact values copied from "
                "[evidence_id=\"...\"] headers. A goal ID, diagnosis candidate ID, "
                "task ID, source ID, or any ID found inside candidate content must not "
                "be returned as evidence."
            ),
            priority=100,
            source_order=0,
        ),
        _required_candidate(
            candidate_id="iteration_task_design:schema",
            kind=ContextCandidateKind.TOOL_SCHEMA,
            source_id="task_designer:output_schema:v1",
            content="Required output schema:\n" + _json_text(_TASK_DESIGN_SCHEMA),
            priority=100,
            source_order=1,
        ),
        _required_candidate(
            candidate_id="iteration_task_design:goal",
            kind=ContextCandidateKind.TASK,
            source_id=f"improvement_goal:{goal.id}",
            content=_goal_projection(goal, completed_iteration),
            priority=100,
            source_order=2,
        ),
        _required_candidate(
            candidate_id="iteration_task_design:safety",
            kind=ContextCandidateKind.CONSTRAINT,
            source_id="improvement_report:prompt_context:safety",
            content=_safety_projection(improvement_report, project_state),
            priority=100,
            source_order=3,
        ),
        _required_candidate(
            candidate_id="iteration_task_design:validation",
            kind=ContextCandidateKind.RUNTIME_EVIDENCE,
            source_id="project_state:validation_summary",
            content=_validation_projection(project_state),
            priority=95,
            source_order=4,
        ),
    ]
    session_constraints = _resolve_session_constraints(session_constraints, session_ingress_state)
    if session_constraints is not None:
        constraint_candidate = build_session_constraint_candidate(session_constraints)
        if constraint_candidate is not None:
            candidates.append(constraint_candidate)

    source_order = 100
    for index, raw_summary in enumerate(project_state.file_summaries):
        summary = raw_summary if isinstance(raw_summary, Mapping) else {}
        if not str(summary.get("preview") or "").strip():
            continue
        path = str(summary.get("path") or summary.get("name") or f"file-{index + 1}")
        candidates.append(
            _optional_candidate(
                candidate_id=_candidate_id("project_file", path, index),
                kind=ContextCandidateKind.PROJECT_FILE,
                source_id=f"project_file:{path}",
                content="Project file evidence:\n" + _json_text(dict(summary)),
                priority=70,
                source_order=source_order,
            )
        )
        source_order += 1

    if project_state.readme_summary.strip():
        candidates.append(
            _optional_candidate(
                candidate_id="iteration_task_design:project_file:readme",
                kind=ContextCandidateKind.PROJECT_FILE,
                source_id=f"project_file:{project_state.project_path}/README.md",
                content="README evidence:\n" + project_state.readme_summary,
                priority=65,
                source_order=source_order,
            )
        )
        source_order += 1

    if projection_policy == "compact":
        source_order = _append_compact_task_design_evidence(
            candidates,
            project_state=project_state,
            goal=goal,
            improvement_report=improvement_report,
            source_order=source_order,
        )
    else:
        for index, raw_record in enumerate(project_state.memory_records[:_TASK_DESIGN_MEMORY_LIMIT]):
            record = raw_record if isinstance(raw_record, Mapping) else {"content": raw_record}
            record_id = str(record.get("id") or f"memory-{index + 1}")
            projected_record = compact_project_memory_record(record)
            candidates.append(
                _optional_candidate(
                    candidate_id=_candidate_id("memory", record_id, index),
                    kind=ContextCandidateKind.MEMORY,
                    source_id=f"memory:{record_id}",
                    content="Historical memory evidence:\n" + _json_text(projected_record),
                    priority=45,
                    source_order=source_order,
                    freshness=ContextCandidateFreshness.HISTORICAL,
                )
            )
            source_order += 1

        diagnosis = improvement_report.get("diagnosis")
        if diagnosis:
            candidates.append(
                _optional_candidate(
                    candidate_id="iteration_task_design:artifact:diagnosis",
                    kind=ContextCandidateKind.ARTIFACT,
                    source_id="improvement_report:diagnosis",
                    content="Additional diagnosis history:\n" + _json_text(diagnosis),
                    priority=35,
                    source_order=source_order,
                    freshness=ContextCandidateFreshness.HISTORICAL,
                )
            )

    _append_session_dialog_candidates(candidates, session_ingress_state, context_projection)
    _append_terminal_output_contract(candidates, prefix=prefix)
    return candidates


def _append_compact_task_design_evidence(
    candidates: list[ContextCandidate],
    *,
    project_state: ProjectStateSnapshot,
    goal: ImprovementGoal,
    improvement_report: Mapping[str, Any],
    source_order: int,
) -> int:
    latest_result = _latest_relevant_iteration_result(project_state.memory_records, goal)
    if latest_result is not None:
        record_index, record = latest_result
        record_id = str(record.get("id") or f"memory-{record_index + 1}")
        candidates.append(
            _optional_candidate(
                candidate_id=_candidate_id("memory", record_id, record_index),
                kind=ContextCandidateKind.MEMORY,
                source_id=f"memory:{record_id}:task_designer_compact:v2",
                content=(
                    "Latest relevant autonomous-iteration task result:\n"
                    + _json_text(compact_project_memory_record(record))
                ),
                priority=45,
                source_order=source_order,
                truncation=ContextCandidateTruncation.FORBIDDEN,
                trust=ContextCandidateTrust.DERIVED,
                freshness=ContextCandidateFreshness.CURRENT,
            )
        )
        source_order += 1

    diagnosis = _compact_task_design_diagnosis(improvement_report, goal)
    if diagnosis:
        candidates.append(
            _optional_candidate(
                candidate_id="iteration_task_design:artifact:diagnosis",
                kind=ContextCandidateKind.ARTIFACT,
                source_id="improvement_report:diagnosis:task_designer_compact:v2",
                content="Goal-relevant diagnosis projection:\n" + _json_text(diagnosis),
                priority=35,
                source_order=source_order,
                truncation=ContextCandidateTruncation.FORBIDDEN,
                trust=ContextCandidateTrust.DERIVED,
                freshness=ContextCandidateFreshness.CURRENT,
            )
        )
        source_order += 1
    return source_order


def _compact_task_design_diagnosis(
    improvement_report: Mapping[str, Any],
    goal: ImprovementGoal,
) -> dict[str, Any]:
    diagnosis = _mapping(improvement_report.get("diagnosis"))
    if not diagnosis:
        return {}
    selected = _mapping(diagnosis.get("selected_candidate"))
    if not selected:
        selected = _mapping(improvement_report.get("selected_candidate"))
    if not _goal_matches_candidate(goal, selected):
        return {}

    projected_selected = {
        "candidate_id": _bounded_text(selected.get("candidate_id"), 120),
        "title": _bounded_text(selected.get("title"), 240),
        "dimension": _bounded_text(selected.get("dimension"), 80),
        "rationale": _bounded_text(selected.get("rationale"), 360),
        "acceptance_criteria": _bounded_strings(selected.get("acceptance_criteria"), 5, 240),
        "target_metrics": _bounded_strings(selected.get("target_metrics"), 5, 160),
        "dependencies": _bounded_strings(selected.get("dependencies"), 5, 160),
        "risks": _bounded_strings(selected.get("risks"), 5, 200),
        "evidence": _bounded_strings(selected.get("evidence"), 5, 240),
    }
    projected_selected = {
        key: value
        for key, value in projected_selected.items()
        if value is not None and value != "" and value != []
    }
    projection: dict[str, Any] = {
        "summary": _bounded_text(diagnosis.get("summary"), 500),
        "selected_candidate": projected_selected,
    }
    target_metric_details = _selected_target_metric_details(diagnosis, selected)
    if target_metric_details:
        projection["target_metric_details"] = target_metric_details
    selected_dimension = str(selected.get("dimension") or "").strip()
    if selected_dimension:
        related_dimension = next(
            (
                _mapping(item)
                for item in list(diagnosis.get("dimension_assessments") or [])
                if isinstance(item, Mapping)
                and str(item.get("dimension") or "").strip() == selected_dimension
            ),
            {},
        )
        if related_dimension:
            projection["dimension_assessment"] = {
                "dimension": _bounded_text(related_dimension.get("dimension"), 80),
                "summary": _bounded_text(related_dimension.get("summary"), 360),
                "gaps": _bounded_strings(related_dimension.get("gaps"), 5, 220),
                "risks": _bounded_strings(related_dimension.get("risks"), 5, 200),
                "evidence": _bounded_strings(related_dimension.get("evidence"), 5, 220),
            }
    if not projection["summary"]:
        projection.pop("summary")
    return projection


def _selected_target_metric_details(
    diagnosis: Mapping[str, Any],
    selected_candidate: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Derive bounded metric details for the selected candidate's exact metric IDs."""

    selected_metric_ids = _bounded_strings(
        selected_candidate.get("target_metrics"),
        5,
        160,
    )
    if not selected_metric_ids:
        return []
    metrics_by_id = {
        str(metric.get("metric_id") or "").strip(): metric
        for raw_metric in list(diagnosis.get("success_metrics") or [])
        if (metric := _mapping(raw_metric))
        and str(metric.get("metric_id") or "").strip()
    }
    projected_metrics: list[dict[str, Any]] = []
    for metric_id in selected_metric_ids:
        metric = metrics_by_id.get(metric_id)
        if metric is None:
            continue
        projected_metric: dict[str, Any] = {
            "metric_id": _bounded_text(metric.get("metric_id"), 160),
            "name": _bounded_text(metric.get("name"), 240),
            "dimension": _bounded_text(metric.get("dimension"), 80),
            "metric_type": _bounded_text(metric.get("metric_type"), 80),
            "target": _bounded_text(metric.get("target"), 360),
            "current_assessment": _bounded_text(
                metric.get("current_assessment"),
                360,
            ),
            "evidence": _bounded_strings(metric.get("evidence"), 5, 240),
        }
        required = metric.get("required")
        if isinstance(required, bool):
            projected_metric["required"] = required
        satisfied = metric.get("satisfied")
        if isinstance(satisfied, bool) or satisfied is None:
            projected_metric["satisfied"] = satisfied
        projected_metrics.append(
            {
                key: value
                for key, value in projected_metric.items()
                if value is not None and value != "" and value != []
            }
        )
    return projected_metrics


def _latest_relevant_iteration_result(
    raw_records: Sequence[Any],
    goal: ImprovementGoal,
) -> tuple[int, Mapping[str, Any]] | None:
    relevant: list[tuple[float, int, Mapping[str, Any]]] = []
    for index, raw_record in enumerate(raw_records):
        record = raw_record if isinstance(raw_record, Mapping) else {}
        record_type = record.get("type")
        if hasattr(record_type, "value"):
            record_type = record_type.value
        if str(record_type or "").strip().lower() not in {
            "project",
            "task",
        }:
            continue
        tags = {str(tag).strip().lower() for tag in list(record.get("tags") or [])}
        if "project_environment" in tags or "autonomous_iteration" not in tags:
            continue
        attributes = _mapping(record.get("attributes"))
        selected_candidate_id = str(attributes.get("selected_candidate_id") or "")
        selected_candidate = str(attributes.get("selected_candidate") or "")
        recorded_goal = str(attributes.get("goal") or "")
        candidate_identity = selected_candidate_id or selected_candidate
        if not (
            _normalized_identity(candidate_identity) in _goal_identities(goal)
            or _normalized_identity(recorded_goal) in _goal_identities(goal)
        ):
            continue
        relevant.append((_timestamp_sort_key(record.get("timestamp")), index, record))
    if not relevant:
        return None
    _, index, record = max(relevant, key=lambda item: (item[0], item[1]))
    return index, record


def _timestamp_sort_key(value: Any) -> float:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _goal_matches_candidate(
    goal: ImprovementGoal,
    candidate: Mapping[str, Any],
) -> bool:
    goal_identities = _goal_identities(goal)
    candidate_identities = {
        _normalized_identity(candidate.get("candidate_id")),
        _normalized_identity(candidate.get("title")),
    }
    if (goal_identities & candidate_identities) - {""}:
        return True
    goal_criteria = {
        _normalized_identity(item) for item in goal.acceptance_criteria if str(item).strip()
    }
    candidate_criteria = {
        _normalized_identity(item)
        for item in _string_list(candidate.get("acceptance_criteria"))
    }
    return bool((goal_criteria & candidate_criteria) - {""})


def _goal_identities(goal: ImprovementGoal) -> set[str]:
    return {
        _normalized_identity(goal.id),
        _normalized_identity(goal.title),
    }


def _normalized_identity(value: Any) -> str:
    return "".join(character.lower() for character in str(value or "") if character.isalnum())


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _bounded_strings(value: Any, count: int, item_limit: int) -> list[str]:
    return [item.strip()[:item_limit] for item in _string_list(value)[:count] if item.strip()]


def compact_project_memory_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a bounded model-facing projection of one authoritative memory record."""

    attributes = record.get("attributes")
    compact_attributes: dict[str, Any] = {}
    if isinstance(attributes, Mapping):
        for key in _MEMORY_ATTRIBUTE_KEYS:
            if key in attributes:
                compact_attributes[key] = _bounded_memory_value(attributes[key])
    projected = {
        "id": str(record.get("id") or ""),
        "type": str(record.get("type") or ""),
        "content": str(record.get("content") or "")[:_MEMORY_CONTENT_LIMIT],
        "tags": [str(tag)[:80] for tag in list(record.get("tags") or [])[:8]],
        "confidence": record.get("confidence"),
    }
    if record.get("timestamp"):
        projected["timestamp"] = str(record["timestamp"])
    if compact_attributes:
        projected["attributes"] = compact_attributes
    return projected


def _bounded_memory_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key)[:80]: _bounded_memory_value(item)
            for key, item in list(value.items())[:12]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_memory_value(item) for item in list(value)[:20]]
    if isinstance(value, str):
        return value[:200]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:200]


def build_project_improvement_analysis_candidates(
    *,
    project_state: ProjectStateSnapshot,
    analysis_context: Mapping[str, Any],
    completed_iteration: int,
    session_constraints: SessionConstraintState | None = None,
    session_ingress_state: SessionIngressState | None = None,
    context_projection: DerivedContextProjection | None = None,
) -> list[ContextCandidate]:
    """Build governed inputs for one project-improvement analysis call."""

    prefix = "project_improvement"
    candidates = [
        _required_candidate(
            candidate_id=f"{prefix}:instruction",
            kind=ContextCandidateKind.INSTRUCTION,
            source_id="project_improvement:instruction:v1",
            content=(
                "You are OpenPilot's Project Improvement Agent. Assess the next "
                "highest-value improvement after hard validation. Return only valid "
                "JSON and preserve all stated safety and product constraints. "
                "Do not propose behavior already present in the supplied project "
                "evidence; choose a concrete remaining gap instead."
            ),
            priority=100,
            source_order=0,
        ),
        _required_candidate(
            candidate_id=f"{prefix}:schema",
            kind=ContextCandidateKind.TOOL_SCHEMA,
            source_id="project_improvement:output_schema:v1",
            content="Required output schema:\n" + _json_text(_PROJECT_IMPROVEMENT_ANALYSIS_SCHEMA),
            priority=100,
            source_order=1,
        ),
        _required_candidate(
            candidate_id=f"{prefix}:task",
            kind=ContextCandidateKind.TASK,
            source_id="project_state:improvement_objective",
            content=_analysis_task_projection(
                project_state,
                analysis_context,
                completed_iteration,
            ),
            priority=100,
            source_order=2,
        ),
        _required_candidate(
            candidate_id=f"{prefix}:safety",
            kind=ContextCandidateKind.CONSTRAINT,
            source_id="analysis_context:prompt_context:safety",
            content=_safety_projection(analysis_context, project_state),
            priority=100,
            source_order=3,
        ),
        _required_candidate(
            candidate_id=f"{prefix}:validation",
            kind=ContextCandidateKind.RUNTIME_EVIDENCE,
            source_id="project_state:validation_summary",
            content=_validation_projection(project_state),
            priority=95,
            source_order=4,
        ),
    ]
    session_constraints = _resolve_session_constraints(session_constraints, session_ingress_state)
    if session_constraints is not None:
        constraint_candidate = build_session_constraint_candidate(session_constraints)
        if constraint_candidate is not None:
            candidates.append(constraint_candidate)
    _append_optional_project_evidence(
        candidates,
        prefix=prefix,
        project_state=project_state,
        context=analysis_context,
    )
    _append_session_dialog_candidates(candidates, session_ingress_state, context_projection)
    _append_terminal_output_contract(candidates, prefix=prefix)
    return candidates


def build_iteration_goal_candidates(
    *,
    project_state: ProjectStateSnapshot,
    improvement_report: Mapping[str, Any],
    completed_iteration: int,
    session_constraints: SessionConstraintState | None = None,
    session_ingress_state: SessionIngressState | None = None,
    context_projection: DerivedContextProjection | None = None,
) -> list[ContextCandidate]:
    """Build governed inputs for one autonomous-iteration Goal Maker call."""

    prefix = "iteration_goal"
    candidates = [
        _required_candidate(
            candidate_id=f"{prefix}:instruction",
            kind=ContextCandidateKind.INSTRUCTION,
            source_id="goal_maker:instruction:v1",
            content=(
                "You are OpenPilot's Goal Maker Agent. Create 1-3 concrete, "
                "evaluable project-improvement goals. Return only valid JSON and "
                "preserve all stated safety and product constraints."
            ),
            priority=100,
            source_order=0,
        ),
        _required_candidate(
            candidate_id=f"{prefix}:schema",
            kind=ContextCandidateKind.TOOL_SCHEMA,
            source_id="goal_maker:output_schema:v1",
            content="Required output schema:\n" + _json_text(_ITERATION_GOAL_SCHEMA),
            priority=100,
            source_order=1,
        ),
        _required_candidate(
            candidate_id=f"{prefix}:task",
            kind=ContextCandidateKind.TASK,
            source_id="improvement_report:goal_selection_task",
            content=_goal_selection_task_projection(
                project_state,
                improvement_report,
                completed_iteration,
            ),
            priority=100,
            source_order=2,
        ),
        _required_candidate(
            candidate_id=f"{prefix}:safety",
            kind=ContextCandidateKind.CONSTRAINT,
            source_id="improvement_report:prompt_context:safety",
            content=_safety_projection(improvement_report, project_state),
            priority=100,
            source_order=3,
        ),
        _required_candidate(
            candidate_id=f"{prefix}:validation",
            kind=ContextCandidateKind.RUNTIME_EVIDENCE,
            source_id="project_state:validation_summary",
            content=_validation_projection(project_state),
            priority=95,
            source_order=4,
        ),
    ]
    session_constraints = _resolve_session_constraints(session_constraints, session_ingress_state)
    if session_constraints is not None:
        constraint_candidate = build_session_constraint_candidate(session_constraints)
        if constraint_candidate is not None:
            candidates.append(constraint_candidate)
    _append_optional_project_evidence(
        candidates,
        prefix=prefix,
        project_state=project_state,
        context=improvement_report,
    )
    _append_session_dialog_candidates(candidates, session_ingress_state, context_projection)
    _append_terminal_output_contract(candidates, prefix=prefix)
    return candidates


def _append_terminal_output_contract(
    candidates: list[ContextCandidate],
    *,
    prefix: str,
) -> None:
    """Ensure evidence history cannot become the provider's assistant prefix."""

    candidates.append(
        _required_candidate(
            candidate_id=f"{prefix}:terminal_output_contract",
            kind=ContextCandidateKind.INSTRUCTION,
            source_id=f"{prefix}:terminal_output_contract:v1",
            content=(
                "Now return exactly one JSON object matching the required output schema. "
                "Do not continue, quote, or imitate any dialog or evidence above."
            ),
            priority=100,
            source_order=10_000,
        )
    )


def _resolve_session_constraints(
    session_constraints: SessionConstraintState | None,
    session_ingress_state: SessionIngressState | None,
) -> SessionConstraintState | None:
    if session_ingress_state is None:
        return session_constraints
    ingress_constraints = session_ingress_state.session_constraints
    if session_constraints is not None and session_constraints.canonical_hash != ingress_constraints.canonical_hash:
        raise ValueError("session ingress and explicit constraints differ")
    return ingress_constraints


def _append_session_dialog_candidates(
    candidates: list[ContextCandidate],
    session_ingress_state: SessionIngressState | None,
    context_projection: DerivedContextProjection | None = None,
) -> None:
    if context_projection is not None:
        if session_ingress_state is None:
            raise ValueError("context projection requires session ingress state")
        if context_projection.session_turn_source_hash != session_turn_ledger_hash(session_ingress_state):
            raise ValueError("context projection turn source hash mismatch")
        if context_projection.session_constraints_hash != session_ingress_state.session_constraints.canonical_hash:
            raise ValueError("context projection constraint hash mismatch")
        base_order = max((candidate.source_order for candidate in candidates), default=0) + 1
        for offset, candidate in enumerate(
            [*context_projection.artifact_candidates, *context_projection.dialog_candidates]
        ):
            candidates.append(candidate.model_copy(update={"source_order": base_order + offset}))
        return
    if session_ingress_state is None:
        return
    dialog_candidates, _ = build_session_turn_candidates(session_ingress_state, max_turns=6)
    base_order = max((candidate.source_order for candidate in candidates), default=0) + 1
    for offset, candidate in enumerate(dialog_candidates):
        candidates.append(candidate.model_copy(update={"source_order": base_order + offset}))


def _required_candidate(
    *,
    candidate_id: str,
    kind: ContextCandidateKind,
    source_id: str,
    content: str,
    priority: int,
    source_order: int,
) -> ContextCandidate:
    return ContextCandidate(
        candidate_id=candidate_id,
        kind=kind,
        source_id=source_id,
        content=content,
        role="user",
        retention=ContextCandidateRetention.REQUIRED,
        priority=priority,
        source_order=source_order,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.CURRENT,
    )


def _optional_candidate(
    *,
    candidate_id: str,
    kind: ContextCandidateKind,
    source_id: str,
    content: str,
    priority: int,
    source_order: int,
    freshness: ContextCandidateFreshness = ContextCandidateFreshness.CURRENT,
    truncation: ContextCandidateTruncation = ContextCandidateTruncation.HEAD,
    trust: ContextCandidateTrust = ContextCandidateTrust.OBSERVED,
) -> ContextCandidate:
    return ContextCandidate(
        candidate_id=candidate_id,
        kind=kind,
        source_id=source_id,
        content=content,
        role="user",
        retention=ContextCandidateRetention.OPTIONAL,
        priority=priority,
        source_order=source_order,
        truncation=truncation,
        trust=trust,
        freshness=freshness,
    )


def _goal_projection(goal: ImprovementGoal, completed_iteration: int) -> str:
    return "Selected improvement goal:\n" + _json_text(
        {
            "completed_successful_improvements": int(completed_iteration),
            "id": goal.id,
            "title": goal.title,
            "category": goal.category,
            "rationale": goal.rationale,
            "acceptance_criteria": list(goal.acceptance_criteria),
            "priority": goal.priority,
        }
    )


def _analysis_task_projection(
    project_state: ProjectStateSnapshot,
    analysis_context: Mapping[str, Any],
    completed_iteration: int,
) -> str:
    selected = _mapping(analysis_context.get("selected_candidate"))
    return "Project-improvement analysis objective:\n" + _json_text(
        {
            "original_goal": project_state.goal,
            "project_path": project_state.project_path,
            "completed_successful_improvements": int(completed_iteration),
            "analysis_summary": str(analysis_context.get("summary") or ""),
            "selected_candidate_title": str(selected.get("title") or ""),
            "selected_candidate_acceptance_criteria": _string_list(
                selected.get("acceptance_criteria")
            )[:5],
        }
    )


def _goal_selection_task_projection(
    project_state: ProjectStateSnapshot,
    improvement_report: Mapping[str, Any],
    completed_iteration: int,
) -> str:
    return "Goal-selection task:\n" + _json_text(
        {
            "original_goal": project_state.goal,
            "project_path": project_state.project_path,
            "completed_successful_improvements": int(completed_iteration),
            "improvement_summary": str(improvement_report.get("summary") or ""),
            "improvement_opportunities": _string_list(
                improvement_report.get("improvement_opportunities")
            )[:5],
            "recommended_actions": _string_list(
                improvement_report.get("recommended_actions")
            )[:5],
            "next_iteration_goal": str(
                improvement_report.get("next_iteration_goal") or ""
            ),
        }
    )


def _safety_projection(
    improvement_report: Mapping[str, Any],
    project_state: ProjectStateSnapshot,
) -> str:
    prompt_context = _mapping(improvement_report.get("prompt_context"))
    report_product_intent = _mapping(prompt_context.get("product_intent"))
    validation_product_intent = _mapping(
        _mapping(project_state.validation_context).get("product_intent")
    )
    legacy_stack_preset = _mapping(prompt_context.get("stack_preset"))
    authoritative_stack_preset = (
        project_state.stack_preset.to_json_dict()
        if project_state.stack_preset is not None
        else {}
    )
    stack_preset = {**legacy_stack_preset, **authoritative_stack_preset}
    constraints = _merge_unique_strings(
        validation_product_intent.get("non_regression_constraints"),
        report_product_intent.get("non_regression_constraints"),
    )
    disallowed = _merge_unique_strings(
        validation_product_intent.get("disallowed_substitutions"),
        report_product_intent.get("disallowed_substitutions"),
    )
    return "Required safety and product constraints:\n" + _json_text(
        {
            "non_regression_constraints": constraints,
            "disallowed_substitutions": disallowed,
            "delivery_surface": str(
                validation_product_intent.get("delivery_surface")
                or report_product_intent.get("delivery_surface")
                or stack_preset.get("delivery_surface")
                or ""
            ),
            "runtime_mode": str(
                validation_product_intent.get("runtime_mode")
                or report_product_intent.get("runtime_mode")
                or ""
            ),
            "architecture": str(stack_preset.get("architecture") or ""),
            "frontend_language": str(stack_preset.get("frontend_language") or ""),
            "backend_language": str(stack_preset.get("backend_language") or ""),
        }
    )


def _validation_projection(project_state: ProjectStateSnapshot) -> str:
    validation = _mapping(project_state.validation_context)
    summary = {
        "validation_passed": validation.get("validation_passed"),
        "summary": str(validation.get("summary") or ""),
        "validation_errors": _string_list(validation.get("validation_errors"))[:5],
        "warnings": _string_list(validation.get("warnings"))[:5],
        "run_command": str(validation.get("run_command") or project_state.run_command or ""),
        "runtime_evidence": [str(item) for item in project_state.runtime_evidence[:5]],
        "test_evidence": [str(item) for item in project_state.test_evidence[:5]],
        "safe_target_files": [str(item) for item in project_state.safe_target_files[:10]],
        "project_file_manifest": [
            str(
                (item if isinstance(item, Mapping) else {}).get("path")
                or (item if isinstance(item, Mapping) else {}).get("name")
                or ""
            )
            for item in project_state.file_summaries[:40]
            if str(
                (item if isinstance(item, Mapping) else {}).get("path")
                or (item if isinstance(item, Mapping) else {}).get("name")
                or ""
            )
        ],
    }
    return "Compact validation and execution summary:\n" + _json_text(summary)


def _append_optional_project_evidence(
    candidates: list[ContextCandidate],
    *,
    prefix: str,
    project_state: ProjectStateSnapshot,
    context: Mapping[str, Any],
) -> None:
    source_order = 100
    for index, raw_summary in enumerate(project_state.file_summaries):
        summary = raw_summary if isinstance(raw_summary, Mapping) else {}
        if not str(summary.get("preview") or "").strip():
            continue
        path = str(summary.get("path") or summary.get("name") or f"file-{index + 1}")
        candidates.append(
            _optional_candidate(
                candidate_id=_scoped_candidate_id(prefix, "project_file", path, index),
                kind=ContextCandidateKind.PROJECT_FILE,
                source_id=f"project_file:{path}",
                content="Project file evidence:\n" + _json_text(dict(summary)),
                priority=70,
                source_order=source_order,
            )
        )
        source_order += 1

    if project_state.readme_summary.strip():
        candidates.append(
            _optional_candidate(
                candidate_id=f"{prefix}:project_file:readme",
                kind=ContextCandidateKind.PROJECT_FILE,
                source_id=f"project_file:{project_state.project_path}/README.md",
                content="README evidence:\n" + project_state.readme_summary,
                priority=65,
                source_order=source_order,
            )
        )
        source_order += 1

    for index, raw_record in enumerate(project_state.memory_records[:_TASK_DESIGN_MEMORY_LIMIT]):
        record = raw_record if isinstance(raw_record, Mapping) else {"content": raw_record}
        record_id = str(record.get("id") or f"memory-{index + 1}")
        projected_record = compact_project_memory_record(record)
        candidates.append(
            _optional_candidate(
                candidate_id=_scoped_candidate_id(prefix, "memory", record_id, index),
                kind=ContextCandidateKind.MEMORY,
                source_id=f"memory:{record_id}",
                content="Historical memory evidence:\n" + _json_text(projected_record),
                priority=45,
                source_order=source_order,
                freshness=ContextCandidateFreshness.HISTORICAL,
            )
        )
        source_order += 1

    diagnosis = context.get("diagnosis")
    if diagnosis:
        candidates.append(
            _optional_candidate(
                candidate_id=f"{prefix}:artifact:diagnosis",
                kind=ContextCandidateKind.ARTIFACT,
                source_id=f"{prefix}:context:diagnosis",
                content="Additional diagnosis history:\n" + _json_text(diagnosis),
                priority=35,
                source_order=source_order,
                freshness=ContextCandidateFreshness.HISTORICAL,
            )
        )


def _candidate_id(kind: str, source_id: str, index: int) -> str:
    return _scoped_candidate_id("iteration_task_design", kind, source_id, index)


def _scoped_candidate_id(prefix: str, kind: str, source_id: str, index: int) -> str:
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{kind}:{index + 1}:{digest}"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _merge_unique_strings(*values: Any) -> list[str]:
    merged: list[str] = []
    for value in values:
        for item in _string_list(value):
            if item not in merged:
                merged.append(item)
    return merged


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
