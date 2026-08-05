"""Strict experimental contracts for a fact/relationship/projection architecture."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=True)


class FactKind(str, Enum):
    PROJECT_DIAGNOSIS = "project_diagnosis"
    IMPROVEMENT_CANDIDATE = "improvement_candidate"
    PROJECT_DEPENDENCY = "project_dependency"
    DEPENDENCY_STRATEGY = "dependency_strategy"
    STACK_PRESET = "stack_preset"
    EVIDENCE = "evidence"


class FactLifecycle(str, Enum):
    RUNTIME_ONLY = "runtime_only"
    ITERATION_EVIDENCE = "iteration_evidence"
    CHECKPOINT_SNAPSHOT = "checkpoint_snapshot"
    DURABLE_PROJECT_STATE = "durable_project_state"
    ARTIFACT = "artifact"


class ProjectDiagnosisValue(StrictModel):
    value_kind: Literal["project_diagnosis"] = "project_diagnosis"
    project_path: str
    iteration: int = Field(ge=0)
    summary: str


class ImprovementCandidateValue(StrictModel):
    value_kind: Literal["improvement_candidate"] = "improvement_candidate"
    title: str
    dimension: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class ProjectDependencyValue(StrictModel):
    value_kind: Literal["project_dependency"] = "project_dependency"
    package_name: str
    version: str = ""
    role: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class DependencyStrategyValue(StrictModel):
    value_kind: Literal["dependency_strategy"] = "dependency_strategy"
    preserve_packages: list[str] = Field(default_factory=list)
    recommended_packages: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)


class StackPresetValue(StrictModel):
    value_kind: Literal["stack_preset"] = "stack_preset"
    revision: int = Field(ge=1)
    delivery_surface: str
    architecture: str


class EvidenceValue(StrictModel):
    value_kind: Literal["evidence"] = "evidence"
    evidence_type: str
    locator: str
    summary: str = ""


FactValue = Annotated[
    ProjectDiagnosisValue
    | ImprovementCandidateValue
    | ProjectDependencyValue
    | DependencyStrategyValue
    | StackPresetValue
    | EvidenceValue,
    Field(discriminator="value_kind"),
]


_VALUE_TYPE_BY_KIND: dict[FactKind, type[StrictModel]] = {
    FactKind.PROJECT_DIAGNOSIS: ProjectDiagnosisValue,
    FactKind.IMPROVEMENT_CANDIDATE: ImprovementCandidateValue,
    FactKind.PROJECT_DEPENDENCY: ProjectDependencyValue,
    FactKind.DEPENDENCY_STRATEGY: DependencyStrategyValue,
    FactKind.STACK_PRESET: StackPresetValue,
    FactKind.EVIDENCE: EvidenceValue,
}


class EntityRef(StrictModel):
    """Reference to one immutable revision, or an explicit latest lookup."""

    fact_id: str = Field(min_length=1)
    revision: int | Literal["latest"]


class FactEnvelope(StrictModel):
    """One authoritative, immutable fact revision."""

    fact_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    kind: FactKind
    owner: str = Field(min_length=1)
    lifecycle: FactLifecycle
    value: FactValue

    @model_validator(mode="after")
    def validate_value_matches_kind(self) -> "FactEnvelope":
        expected = _VALUE_TYPE_BY_KIND[FactKind(self.kind)]
        if not isinstance(self.value, expected):
            raise ValueError(
                f"value type {type(self.value).__name__} does not match fact kind {self.kind}; "
                f"expected {expected.__name__}"
            )
        return self

    @property
    def ref(self) -> EntityRef:
        return EntityRef(fact_id=self.fact_id, revision=self.revision)


class RelationKind(str, Enum):
    SELECTS = "selects"
    SUPPORTS = "supports"
    DERIVED_FROM = "derived_from"
    CONSUMES = "consumes"
    SNAPSHOT_OF = "snapshot_of"


class Relationship(StrictModel):
    """An authoritative relationship whose endpoints remain separately owned facts."""

    relation_id: str = Field(min_length=1)
    kind: RelationKind
    source: EntityRef
    target: EntityRef
    owner: str = Field(min_length=1)


class ProjectionPurpose(str, Enum):
    PROMPT = "prompt"
    REPORT = "report"
    UI = "ui"
    GUARD = "guard"


class ProjectDiagnosisProjection(StrictModel):
    """Non-authoritative recipe for a consumer-specific diagnosis view."""

    projection_id: str = Field(min_length=1)
    purpose: ProjectionPurpose
    diagnosis_ref: EntityRef
    candidate_refs: list[EntityRef] = Field(default_factory=list)
    selected_candidate_ref: EntityRef | None = None

    @model_validator(mode="after")
    def selected_candidate_must_be_listed(self) -> "ProjectDiagnosisProjection":
        if self.selected_candidate_ref is not None and self.selected_candidate_ref not in self.candidate_refs:
            raise ValueError("selected_candidate_ref must also appear in candidate_refs")
        return self

