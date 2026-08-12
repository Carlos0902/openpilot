"""Read-only evidence admission and task materialization."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from autonomous_iteration.checkpoint_store import RuntimeCheckpointStore
from autonomous_iteration.iteration_task_materializer import IterationTaskMaterializer
from autonomous_iteration.iteration_turn_store import IterationTurnStore
from metadata import (
    AgentPhase,
    CanonicalInitialTaskSnapshot,
    ClaimSourceClass,
    CompletionObligationKind,
    DecisionNeedMetadata,
    IterationAuthorityCeiling,
    IterationTurnRecordMetadata,
    ProjectFingerprint,
    ProjectImprovementPolicy,
    ProjectImprovementRequirement,
    RuntimeCheckpointMetadata,
    RuntimeStateMetadata,
    SessionIngressState,
    TaskGraphNodeMetadata,
)


class EvidenceEscalationFailureCode(str, Enum):
    CANDIDATE_INVALID = "candidate_invalid"
    SOURCE_INCOMPATIBLE = "source_incompatible"
    AUTHORITY_STALE = "authority_stale"


class EvidenceEscalationError(RuntimeError):
    def __init__(self, code: EvidenceEscalationFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class EvidenceEscalationController:
    """Turn an evidence-required response into a bounded read-only task."""

    def __init__(
        self,
        turn_store: IterationTurnStore,
        checkpoint_store: RuntimeCheckpointStore,
    ) -> None:
        self.turn_store = turn_store
        self.checkpoint_store = checkpoint_store

    def decision_needs(self, record: IterationTurnRecordMetadata) -> tuple[DecisionNeedMetadata, ...]:
        manifest = self._claim_manifest(record)
        by_id = {item["claim_id"]: item for item in manifest}
        needs: list[DecisionNeedMetadata] = []
        for obligation in record.obligations:
            if not obligation.is_blocking:
                continue
            claim_id = obligation.obligation_id.removeprefix("ground:")
            claim = by_id.get(claim_id)
            if claim is None:
                raise EvidenceEscalationError(
                    EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                    "open evidence obligation has no integrity-bound claim",
                )
            source = ClaimSourceClass(claim["source_class"])
            expected_kind = {
                ClaimSourceClass.PROJECT: CompletionObligationKind.PROJECT_FACT,
                ClaimSourceClass.CURRENT_EXTERNAL: CompletionObligationKind.CURRENT_EXTERNAL_FACT,
            }.get(source)
            if expected_kind is None or obligation.kind != expected_kind:
                raise EvidenceEscalationError(
                    EvidenceEscalationFailureCode.SOURCE_INCOMPATIBLE,
                    "open obligation is incompatible with its Runtime-owned claim source",
                )
            needs.append(
                DecisionNeedMetadata(
                    need_type=("project_structure" if source == ClaimSourceClass.PROJECT else "web_search"),
                    question=f"Collect evidence for claim: {claim['text']}",
                    phase=AgentPhase.UNDERSTAND_PROJECT,
                    query=claim["text"] if source == ClaimSourceClass.CURRENT_EXTERNAL else None,
                    decision_to_unlock=obligation.obligation_id,
                    expected_state_change="Close the source-compatible response evidence obligation.",
                    risk_level="low",
                    cost_hint="low",
                    attributes={
                        "obligation_id": obligation.obligation_id,
                        "claim_id": claim_id,
                        "source_class": source.value,
                        "read_only": True,
                    },
                )
            )
        if not needs:
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                "response candidate has no open evidence needs",
            )
        return tuple(needs)

    def materialize_read_only_task(
        self,
        record: IterationTurnRecordMetadata,
        *,
        current_ingress: SessionIngressState,
        project_fingerprint: ProjectFingerprint,
    ) -> tuple[IterationTurnRecordMetadata, tuple[DecisionNeedMetadata, ...]]:
        needs = self.decision_needs(record)
        if record.cursor.authority_state.ceiling != IterationAuthorityCeiling.READ_ONLY_ELIGIBLE:
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.AUTHORITY_STALE,
                "response authority does not permit read-only evidence materialization",
            )
        task_id = self._stable_id("evidence-task", record)
        node = TaskGraphNodeMetadata(
            task_id=task_id,
            description="Collect read-only evidence required to ground the response candidate.",
            task_kind="inspect",
            required_inputs=[need.question for need in needs],
            expected_outputs=[str(need.decision_to_unlock) for need in needs],
            can_run_parallel=False,
            tags=["response-evidence", "read-only"],
        )
        runtime_state = RuntimeStateMetadata(
            goal=node.description,
            execution_mode="read_only",
            execution_mode_source="root_goal",
            execution_mode_reason="Evidence escalation cannot exceed response-only authority.",
            phase=AgentPhase.UNDERSTAND_PROJECT,
            unknowns=[need.question for need in needs],
            verification_status="not_required",
            project_improvement_policy=ProjectImprovementPolicy(
                requirement=ProjectImprovementRequirement.DISABLED,
                target_successes=0,
                max_attempts=0,
            ),
            session_constraints=current_ingress.session_constraints,
        )
        checkpoint = RuntimeCheckpointMetadata(
            checkpoint_id=self._stable_id("evidence-initial-checkpoint", record),
            generation=1,
            run_id=record.identity.run_id,
            root_task_id=task_id,
            session_id=record.identity.run_id,
            checkpoint_reason="read-only response evidence task materialized",
            safe_boundary="decomposition_recorded",
            runtime_state=runtime_state,
            session_ingress_state=current_ingress,
            project_fingerprint=project_fingerprint,
            mutation_class="read_only",
        )
        snapshot = CanonicalInitialTaskSnapshot(
            task_graph=(node,),
            execution_order=(task_id,),
            initial_checkpoint=checkpoint,
            authority_state=record.cursor.authority_state,
            root_budget=record.root_budget,
            session_authority_revision=current_ingress.session_constraints.revision,
            session_authority_hash=current_ingress.session_constraints.authority_hash,
        )
        active = IterationTaskMaterializer(
            self.turn_store,
            self.checkpoint_store,
        ).materialize(record, snapshot=snapshot, current_ingress=current_ingress)
        return active, needs

    def _claim_manifest(self, record: IterationTurnRecordMetadata) -> list[dict[str, Any]]:
        candidate = record.response_candidate
        if candidate is None or candidate.claim_manifest_ref is None:
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                "evidence escalation requires a claim manifest",
            )
        payload = self.turn_store.load_artifact(
            record.identity.conversation_id,
            record.identity.run_id,
            candidate.claim_manifest_ref,
        )
        claims = payload.get("claims") if isinstance(payload, dict) and set(payload) == {"claims"} else None
        if not isinstance(claims, list) or len(claims) != len(candidate.claims):
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                "response claim manifest is unavailable or incomplete",
            )
        expected_ids = tuple(item.claim_id for item in candidate.claims)
        observed_ids = tuple(str(item.get("claim_id") or "") if isinstance(item, dict) else "" for item in claims)
        if len(set(observed_ids)) != len(observed_ids) or observed_ids != expected_ids:
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                "response claim manifest order or identity differs from candidate",
            )
        for claim, item in zip(candidate.claims, claims, strict=True):
            if not isinstance(item, dict) or set(item) != {"claim_id", "claim_hash", "source_class", "text"}:
                raise EvidenceEscalationError(
                    EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                    "response claim manifest contains an invalid entry",
                )
            text = item.get("text")
            if (
                not isinstance(text, str)
                or self._hash({"claim": text}) != claim.claim_hash
                or item.get("claim_hash") != claim.claim_hash
                or item.get("source_class") != claim.source_class
            ):
                raise EvidenceEscalationError(
                    EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                    "response claim manifest differs from candidate hashes",
                )
        return claims

    @classmethod
    def _stable_id(cls, prefix: str, record: IterationTurnRecordMetadata) -> str:
        digest = cls._hash(
            {
                "conversation_id": record.identity.conversation_id,
                "run_id": record.identity.run_id,
                "record_id": record.record_id,
                "purpose": prefix,
            }
        ).removeprefix("sha256:")
        return f"{prefix}-{digest[:32]}"

    @staticmethod
    def _hash(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["EvidenceEscalationController", "EvidenceEscalationError", "EvidenceEscalationFailureCode"]
