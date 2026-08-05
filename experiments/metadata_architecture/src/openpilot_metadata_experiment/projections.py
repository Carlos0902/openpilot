"""Rebuild disposable consumer views from authoritative fact revisions."""

from __future__ import annotations

from typing import Any

from .models import FactKind, ImprovementCandidateValue, ProjectDiagnosisProjection, ProjectDiagnosisValue
from .stores import FactStore


def project_diagnosis_prompt_view(
    projection: ProjectDiagnosisProjection,
    facts: FactStore,
) -> dict[str, Any]:
    diagnosis = facts.resolve(projection.diagnosis_ref)
    if diagnosis.kind != FactKind.PROJECT_DIAGNOSIS or not isinstance(diagnosis.value, ProjectDiagnosisValue):
        raise ValueError("diagnosis_ref must resolve to a project diagnosis fact")

    candidates: list[dict[str, Any]] = []
    candidate_by_ref: dict[tuple[str, int], dict[str, Any]] = {}
    pinned_refs = [diagnosis.ref]
    for reference in projection.candidate_refs:
        candidate = facts.resolve(reference)
        if candidate.kind != FactKind.IMPROVEMENT_CANDIDATE or not isinstance(
            candidate.value, ImprovementCandidateValue
        ):
            raise ValueError("candidate_refs must resolve to improvement candidate facts")
        payload = candidate.value.model_dump(mode="json", exclude={"value_kind"})
        candidates.append(payload)
        candidate_by_ref[(candidate.fact_id, candidate.revision)] = payload
        pinned_refs.append(candidate.ref)

    selected = None
    if projection.selected_candidate_ref is not None:
        resolved = facts.resolve(projection.selected_candidate_ref)
        selected = candidate_by_ref[(resolved.fact_id, resolved.revision)]

    return {
        "projection_id": projection.projection_id,
        "purpose": projection.purpose,
        "diagnosis": diagnosis.value.model_dump(mode="json", exclude={"value_kind"}),
        "candidates": candidates,
        "selected_candidate": selected,
        "source_refs": [reference.model_dump(mode="json") for reference in pinned_refs],
    }

