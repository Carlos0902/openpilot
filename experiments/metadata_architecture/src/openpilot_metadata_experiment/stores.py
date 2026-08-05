"""Small in-memory stores used to test identity and relationship semantics."""

from __future__ import annotations

from collections.abc import Iterable

from .models import EntityRef, FactEnvelope, RelationKind, Relationship


class FactStore:
    def __init__(self, facts: Iterable[FactEnvelope] = ()) -> None:
        self._facts: dict[tuple[str, int], FactEnvelope] = {}
        for fact in facts:
            self.add(fact)

    def add(self, fact: FactEnvelope) -> None:
        key = (fact.fact_id, fact.revision)
        if key in self._facts:
            raise ValueError(f"fact revision already exists: {fact.fact_id}@{fact.revision}")
        self._facts[key] = fact.model_copy(deep=True)

    def resolve(self, reference: EntityRef) -> FactEnvelope:
        revision = reference.revision
        if revision == "latest":
            revisions = [candidate for fact_id, candidate in self._facts if fact_id == reference.fact_id]
            if not revisions:
                raise KeyError(f"unknown fact: {reference.fact_id}")
            revision = max(revisions)
        fact = self._facts.get((reference.fact_id, revision))
        if fact is None:
            raise KeyError(f"unknown fact revision: {reference.fact_id}@{revision}")
        return fact.model_copy(deep=True)


class RelationshipStore:
    def __init__(self, facts: FactStore) -> None:
        self._facts = facts
        self._relationships: dict[str, Relationship] = {}

    def add(self, relationship: Relationship) -> None:
        if relationship.relation_id in self._relationships:
            raise ValueError(f"relationship already exists: {relationship.relation_id}")
        self._facts.resolve(relationship.source)
        self._facts.resolve(relationship.target)
        self._relationships[relationship.relation_id] = relationship.model_copy(deep=True)

    def targets(self, source_fact_id: str, kind: RelationKind) -> list[EntityRef]:
        return [
            relationship.target.model_copy(deep=True)
            for relationship in self._relationships.values()
            if relationship.source.fact_id == source_fact_id and relationship.kind == kind
        ]

