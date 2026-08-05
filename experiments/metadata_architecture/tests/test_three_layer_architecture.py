from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from openpilot_metadata_experiment import (
    EntityRef,
    FactEnvelope,
    FactKind,
    FactStore,
    ImprovementCandidateValue,
    ProjectDiagnosisValue,
    ProjectDiagnosisProjection,
    ProjectionPurpose,
    RelationKind,
    Relationship,
    RelationshipStore,
    run_project_diagnosis_case,
    project_diagnosis_prompt_view,
)


def candidate(*, revision: int = 1, title: str = "Make recovery deterministic") -> FactEnvelope:
    return FactEnvelope(
        fact_id="candidate:recovery",
        revision=revision,
        kind=FactKind.IMPROVEMENT_CANDIDATE,
        owner="project_diagnosis",
        lifecycle="iteration_evidence",
        value=ImprovementCandidateValue(
            title=title,
            dimension="reliability",
            acceptance_criteria=["An interrupted write resumes without replay"],
            evidence_refs=["evidence:checkpoint-test"],
        ),
    )


def diagnosis() -> FactEnvelope:
    return FactEnvelope(
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


def test_fact_revisions_are_immutable_and_latest_is_explicit() -> None:
    store = FactStore()
    store.add(candidate())
    store.add(candidate(revision=2, title="Make recovery exact and deterministic"))

    assert store.resolve(EntityRef(fact_id="candidate:recovery", revision=1)).revision == 1
    assert store.resolve(EntityRef(fact_id="candidate:recovery", revision="latest")).revision == 2

    with pytest.raises(ValueError, match="already exists"):
        store.add(candidate())


def test_relationships_reference_facts_instead_of_copying_values() -> None:
    facts = FactStore([diagnosis(), candidate()])
    relationships = RelationshipStore(facts)
    relationship = Relationship(
        relation_id="selection:iteration-7",
        kind=RelationKind.SELECTS,
        source=EntityRef(fact_id="diagnosis:iteration-7", revision=1),
        target=EntityRef(fact_id="candidate:recovery", revision=1),
        owner="project_diagnosis",
    )

    relationships.add(relationship)

    encoded = json.dumps(relationship.model_dump(mode="json"), sort_keys=True)
    assert "Make recovery deterministic" not in encoded
    assert relationships.targets("diagnosis:iteration-7", RelationKind.SELECTS) == [
        EntityRef(fact_id="candidate:recovery", revision=1)
    ]


def test_projection_is_rebuilt_from_pinned_sources_and_is_not_authoritative() -> None:
    facts = FactStore([diagnosis(), candidate(), candidate(revision=2, title="Newer candidate wording")])
    projection = ProjectDiagnosisProjection(
        projection_id="prompt:iteration-7",
        purpose=ProjectionPurpose.PROMPT,
        diagnosis_ref=EntityRef(fact_id="diagnosis:iteration-7", revision=1),
        candidate_refs=[EntityRef(fact_id="candidate:recovery", revision=1)],
        selected_candidate_ref=EntityRef(fact_id="candidate:recovery", revision=1),
    )

    view = project_diagnosis_prompt_view(projection, facts)

    assert view["selected_candidate"]["title"] == "Make recovery deterministic"
    assert view["source_refs"] == [
        {"fact_id": "diagnosis:iteration-7", "revision": 1},
        {"fact_id": "candidate:recovery", "revision": 1},
    ]
    assert "authoritative" not in view


def test_projection_rejects_a_selected_candidate_outside_its_candidate_set() -> None:
    with pytest.raises(ValidationError, match="selected_candidate_ref"):
        ProjectDiagnosisProjection(
            projection_id="prompt:iteration-7",
            purpose=ProjectionPurpose.PROMPT,
            diagnosis_ref=EntityRef(fact_id="diagnosis:iteration-7", revision=1),
            candidate_refs=[EntityRef(fact_id="candidate:recovery", revision=1)],
            selected_candidate_ref=EntityRef(fact_id="candidate:other", revision=1),
        )


def test_fact_kind_and_value_shape_must_match() -> None:
    with pytest.raises(ValidationError, match="value type"):
        FactEnvelope(
            fact_id="dependency:pytest",
            revision=1,
            kind=FactKind.PROJECT_DEPENDENCY,
            owner="environment_sync",
            lifecycle="durable_project_state",
            value=ImprovementCandidateValue(title="wrong", dimension="wrong"),
        )


def test_project_diagnosis_case_measures_duplication_without_claiming_a_winner() -> None:
    result = run_project_diagnosis_case()

    assert result["baseline"]["authoritative_candidate_copies"] == 3
    assert result["three_layer"]["authoritative_candidate_copies"] == 1
    assert result["three_layer"]["selected_candidate_title"] == "Make recovery deterministic"
    assert result["baseline"]["serialized_bytes"] > 0
    assert result["three_layer"]["serialized_bytes"] > 0
