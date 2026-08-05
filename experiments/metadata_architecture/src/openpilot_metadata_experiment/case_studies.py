"""Runnable comparisons grounded in current OpenPilot metadata pressure points."""

from __future__ import annotations

import json
from typing import Any

from .models import (
    EntityRef,
    FactEnvelope,
    FactKind,
    ImprovementCandidateValue,
    ProjectDiagnosisProjection,
    ProjectDiagnosisValue,
    ProjectionPurpose,
    RelationKind,
    Relationship,
)
from .projections import project_diagnosis_prompt_view
from .stores import FactStore, RelationshipStore


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _count_key(value: Any, key: str) -> int:
    if isinstance(value, dict):
        return int(key in value) + sum(_count_key(item, key) for item in value.values())
    if isinstance(value, list):
        return sum(_count_key(item, key) for item in value)
    return 0


def run_project_diagnosis_case() -> dict[str, Any]:
    """Compare the current pressure pattern with a reference-and-projection variant.

    Byte size is reported rather than used as an acceptance criterion. For small payloads,
    identity and relationship envelopes can legitimately cost more than copied values.
    """

    candidate_payload = {
        "candidate_id": "candidate:recovery",
        "title": "Make recovery deterministic",
        "dimension": "reliability",
        "acceptance_criteria": ["An interrupted write resumes without replay"],
        "evidence_refs": ["evidence:checkpoint-test"],
    }
    baseline = {
        "diagnosis": {
            "diagnosis_id": "diagnosis:iteration-7",
            "summary": "Recovery is the highest-value reliability gap.",
            "improvement_candidates": [candidate_payload],
            "selected_candidate": candidate_payload,
        },
        "improvement_analysis": {"selected_candidate": candidate_payload},
    }

    diagnosis = FactEnvelope(
        fact_id="diagnosis:iteration-7",
        revision=1,
        kind=FactKind.PROJECT_DIAGNOSIS,
        owner="project_diagnosis",
        lifecycle="iteration_evidence",
        value=ProjectDiagnosisValue(
            project_path="/project",
            iteration=7,
            summary="Recovery is the highest-value reliability gap.",
        ),
    )
    candidate = FactEnvelope(
        fact_id="candidate:recovery",
        revision=1,
        kind=FactKind.IMPROVEMENT_CANDIDATE,
        owner="project_diagnosis",
        lifecycle="iteration_evidence",
        value=ImprovementCandidateValue(
            title="Make recovery deterministic",
            dimension="reliability",
            acceptance_criteria=["An interrupted write resumes without replay"],
            evidence_refs=["evidence:checkpoint-test"],
        ),
    )
    facts = FactStore([diagnosis, candidate])
    relationship = Relationship(
        relation_id="selection:iteration-7",
        kind=RelationKind.SELECTS,
        source=diagnosis.ref,
        target=candidate.ref,
        owner="project_diagnosis",
    )
    relationships = RelationshipStore(facts)
    relationships.add(relationship)
    projection = ProjectDiagnosisProjection(
        projection_id="prompt:iteration-7",
        purpose=ProjectionPurpose.PROMPT,
        diagnosis_ref=diagnosis.ref,
        candidate_refs=[candidate.ref],
        selected_candidate_ref=candidate.ref,
    )
    view = project_diagnosis_prompt_view(projection, facts)
    layered = {
        "facts": [diagnosis.model_dump(mode="json"), candidate.model_dump(mode="json")],
        "relationships": [relationship.model_dump(mode="json")],
        "projection_recipe": projection.model_dump(mode="json"),
    }

    return {
        "case": "project_diagnosis_selection",
        "baseline": {
            "authoritative_candidate_copies": _count_key(baseline, "candidate_id"),
            "serialized_bytes": _json_size(baseline),
        },
        "three_layer": {
            "authoritative_candidate_copies": sum(
                1 for fact in layered["facts"] if fact["kind"] == FactKind.IMPROVEMENT_CANDIDATE
            ),
            "serialized_bytes": _json_size(layered),
            "prompt_view_bytes": _json_size(view),
            "selected_candidate_title": view["selected_candidate"]["title"],
        },
        "caveat": "This case measures duplication and size only; it does not authorize production migration.",
    }
