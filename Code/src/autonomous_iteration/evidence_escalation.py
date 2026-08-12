"""Read-only evidence escalation and response completion re-entry."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from autonomous_iteration.checkpoint_store import RuntimeCheckpointStore
from autonomous_iteration.iteration_task_materializer import (
    IterationTaskMaterializer,
    TaskMaterializationError,
    TaskMaterializationFailureCode,
)
from autonomous_iteration.iteration_turn_commit import IterationTurnCommitter, assistant_payload_hash
from autonomous_iteration.iteration_turn_reducer import IterationTurnReducer
from autonomous_iteration.iteration_turn_store import IterationTurnStore
from autonomous_iteration.iteration_turn_store import IterationTurnConflictError
from metadata import (
    AgentPhase,
    AssistantLedgerCommitState,
    AssistantTurnCommit,
    CanonicalInitialTaskSnapshot,
    ClaimSourceClass,
    CompletedResponseOutcome,
    CompletionObligation,
    CompletionObligationKind,
    CompletionObligationStatus,
    DecisionNeedMetadata,
    DurableArtifactReference,
    GroundingDecision,
    GroundingStatus,
    IterationDisposition,
    IterationAuthorityCeiling,
    IterationPhase,
    IterationTurnRecordMetadata,
    ProjectFingerprint,
    ProjectImprovementPolicy,
    ProjectImprovementRequirement,
    RuntimeCheckpointMetadata,
    RuntimeStateMetadata,
    RuntimeTaskPurpose,
    SessionIngressState,
    TaskGraphNodeMetadata,
)


class EvidenceEscalationFailureCode(str, Enum):
    CANDIDATE_INVALID = "candidate_invalid"
    SOURCE_INCOMPATIBLE = "source_incompatible"
    ARTIFACT_INVALID = "artifact_invalid"
    CHECKPOINT_INVALID = "checkpoint_invalid"
    PROJECT_STALE = "project_stale"
    EXTERNAL_EVIDENCE_STALE = "external_evidence_stale"
    AUTHORITY_STALE = "authority_stale"


class EvidenceEscalationError(RuntimeError):
    def __init__(self, code: EvidenceEscalationFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class EvidenceReceipt(BaseModel):
    """Task-owned evidence receipt consumed by the pre-task completion gate."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    obligation_id: str = Field(min_length=1)
    source_class: ClaimSourceClass
    artifact_ref: DurableArtifactReference
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    checkpoint_id: str = Field(min_length=1)
    checkpoint_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    project_fingerprint_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    observed_at: datetime


class EvidenceArtifactPayload(BaseModel):
    """Body-bearing observation whose provenance is bound by the receipt/checkpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    obligation_id: str = Field(min_length=1)
    source_class: ClaimSourceClass
    observed_at: datetime
    evidence: dict[str, Any] = Field(min_length=1)


class PendingEvidenceObservation(BaseModel):
    """Artifact marker awaiting one exact task-owned applied checkpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    obligation_id: str = Field(min_length=1)
    source_class: ClaimSourceClass
    artifact_ref: DurableArtifactReference
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    observed_at: datetime
    marker: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    step_id: str = Field(min_length=1)


class EvidenceRuntimeBridge:
    """Bind exact governed tool observations to CRU-2D evidence receipts."""

    def __init__(
        self,
        controller: "EvidenceEscalationController",
        record: IterationTurnRecordMetadata,
        *,
        current_ingress: SessionIngressState,
        current_project_fingerprint: ProjectFingerprint,
    ) -> None:
        if record.task_binding.state != "active":
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                "evidence runtime bridge requires an active task binding",
            )
        self.controller = controller
        self.record = record
        self.current_ingress = current_ingress.model_copy(deep=True)
        self.current_project_fingerprint = current_project_fingerprint.model_copy(deep=True)
        self._expected_needs = {
            str(need.decision_to_unlock): need for need in controller.decision_needs(record)
        }
        self._receipts: dict[str, EvidenceReceipt] = {}
        self._pending: dict[str, PendingEvidenceObservation] = {}

    @property
    def receipts(self) -> tuple[EvidenceReceipt, ...]:
        return tuple(
            self._receipts[obligation.obligation_id]
            for obligation in self.record.obligations
            if obligation.is_blocking and obligation.obligation_id in self._receipts
        )

    def observe(self, selection: Any, execution_result: Any) -> PendingEvidenceObservation:
        input_metadata = getattr(selection, "input_metadata", None)
        attributes = getattr(input_metadata, "attributes", None)
        if not isinstance(attributes, dict):
            attributes = {}
        obligation_id = str(attributes.get("obligation_id") or "").strip()
        source_value = str(attributes.get("source_class") or "").strip()
        need = self._expected_needs.get(obligation_id)
        if (
            need is None
            or attributes.get("read_only") is not True
            or need.decision_to_unlock != obligation_id
            or need.attributes.get("source_class") != source_value
        ):
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.SOURCE_INCOMPATIBLE,
                "tool observation differs from the active evidence obligation",
            )
        try:
            source_class = ClaimSourceClass(source_value)
        except ValueError as exc:
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.SOURCE_INCOMPATIBLE,
                "tool observation has an unsupported evidence source",
            ) from exc
        tool_name = str(getattr(selection, "tool_name", "") or "").strip()
        compatible_tools = (
            {"file_reader", "multi_file_reader"}
            if source_class == ClaimSourceClass.PROJECT
            else {"web_searcher"}
        )
        output = getattr(execution_result, "output_metadata", None)
        if (
            tool_name not in compatible_tools
            or not bool(getattr(execution_result, "success", False))
            or output is None
            or not callable(getattr(output, "to_json_dict", None))
        ):
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.SOURCE_INCOMPATIBLE,
                "evidence obligation was not satisfied by a successful compatible tool",
            )
        if obligation_id in self._pending or obligation_id in self._receipts:
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.ARTIFACT_INVALID,
                "evidence obligation already has an observed artifact",
            )
        observed_at = self.controller._utc(self.controller.now())
        payload_model = EvidenceArtifactPayload(
            obligation_id=obligation_id,
            source_class=source_class,
            observed_at=observed_at,
            evidence={
                "tool_name": tool_name,
                "step_id": str(getattr(selection, "step_id", "") or ""),
                "input": input_metadata.to_json_dict(),
                "output": output.to_json_dict(),
            },
        )
        payload = payload_model.model_dump(mode="json")
        reference = self.controller.turn_store.save_artifact(
            self.record.identity.conversation_id,
            self.record.identity.run_id,
            kind="observed_evidence",
            payload=payload,
        )
        content_hash = self.controller.evidence_payload_hash(payload)
        pending = PendingEvidenceObservation(
            obligation_id=obligation_id,
            source_class=source_class,
            artifact_ref=reference,
            content_hash=content_hash,
            observed_at=observed_at,
            marker=f"evidence:{reference.artifact_id}:{content_hash}",
            tool_name=tool_name,
            step_id=str(getattr(selection, "step_id", "") or ""),
        )
        self._pending[obligation_id] = pending
        return pending

    def bind_checkpoint(
        self,
        pending: PendingEvidenceObservation,
        checkpoint: RuntimeCheckpointMetadata,
    ) -> EvidenceReceipt:
        if self._pending.get(pending.obligation_id) != pending:
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.ARTIFACT_INVALID,
                "evidence observation is not pending for this bridge",
            )
        expected_project_hash = self.controller.project_fingerprint_hash(
            self.current_project_fingerprint
        )
        if (
            checkpoint.run_id != self.record.identity.run_id
            or checkpoint.root_task_id != self.record.task_binding.task_id
            or checkpoint.session_id != self.record.identity.run_id
            or checkpoint.safe_boundary != "tool_result_applied"
            or checkpoint.side_effect_state != "applied"
            or checkpoint.mutation_class != "read_only"
            or checkpoint.tool_name != pending.tool_name
            or checkpoint.step_id != pending.step_id
            or pending.marker not in checkpoint.runtime_state.known_facts
            or checkpoint.session_ingress_state != self.current_ingress
            or self.controller.project_fingerprint_hash(checkpoint.project_fingerprint)
            != expected_project_hash
        ):
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CHECKPOINT_INVALID,
                "evidence checkpoint does not contain the exact artifact marker and task identity",
            )
        receipt = EvidenceReceipt(
            obligation_id=pending.obligation_id,
            source_class=pending.source_class,
            artifact_ref=pending.artifact_ref,
            content_hash=pending.content_hash,
            checkpoint_id=checkpoint.checkpoint_id,
            checkpoint_digest=str(checkpoint.integrity_checksum),
            project_fingerprint_hash=(
                expected_project_hash
                if pending.source_class == ClaimSourceClass.PROJECT
                else None
            ),
            observed_at=pending.observed_at,
        )
        self._receipts[pending.obligation_id] = receipt
        del self._pending[pending.obligation_id]
        return receipt

    def complete(self) -> IterationTurnRecordMetadata:
        if self._pending or set(self._receipts) != set(self._expected_needs):
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CHECKPOINT_INVALID,
                "evidence runtime did not durably bind every required obligation",
            )
        return self.controller.absorb_and_complete(
            self.record,
            current_ingress=self.current_ingress,
            current_project_fingerprint=self.current_project_fingerprint,
            receipts=self.receipts,
        )

    def response_content(self, record: IterationTurnRecordMetadata) -> str:
        return self.controller.response_content(record)


class EvidenceEscalationController:
    """Materialize a read-only evidence task and re-enter the same response gate."""

    def __init__(
        self,
        turn_store: IterationTurnStore,
        checkpoint_store: RuntimeCheckpointStore,
        *,
        now: Callable[[], datetime] | None = None,
        max_external_age: timedelta = timedelta(minutes=5),
        after_write: Callable[[str], None] | None = None,
    ) -> None:
        self.turn_store = turn_store
        self.checkpoint_store = checkpoint_store
        self.now = now or (lambda: datetime.now(UTC))
        self.max_external_age = max_external_age
        self.after_write = after_write

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
            need_type = "project_structure" if source == ClaimSourceClass.PROJECT else "web_search"
            needs.append(
                DecisionNeedMetadata(
                    need_type=need_type,
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
                        **(
                            {"read_only_listing": True}
                            if source == ClaimSourceClass.PROJECT
                            else {}
                        ),
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
            task_purpose=RuntimeTaskPurpose.RESPONSE_EVIDENCE,
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

    def active_task_node(
        self,
        record: IterationTurnRecordMetadata,
    ) -> TaskGraphNodeMetadata:
        snapshot = self._snapshot(record)
        if len(snapshot.task_graph) != 1:
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                "evidence task snapshot must contain exactly one task node",
            )
        return snapshot.task_graph[0].model_copy(deep=True)

    def response_content(self, record: IterationTurnRecordMetadata) -> str:
        candidate = record.response_candidate
        if candidate is None:
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                "evidence completion has no response candidate",
            )
        payload = self.turn_store.load_artifact(
            record.identity.conversation_id,
            record.identity.run_id,
            candidate.response_ref,
        )
        if (
            not self._valid_assistant_payload(payload)
            or assistant_payload_hash(payload) != candidate.response_hash
        ):
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.ARTIFACT_INVALID,
                "evidence response payload is unavailable or checksum-mismatched",
            )
        return str(payload["content"])

    def absorb_and_complete(
        self,
        record: IterationTurnRecordMetadata,
        *,
        current_ingress: SessionIngressState,
        current_project_fingerprint: ProjectFingerprint,
        receipts: tuple[EvidenceReceipt, ...],
    ) -> IterationTurnRecordMetadata:
        latest = self.turn_store.load_latest(
            record.identity.conversation_id,
            record.identity.run_id,
        )
        if latest is not None and latest.record_id != record.record_id:
            resumed = self._resume_durable_completion(
                record,
                latest=latest,
                current_ingress=current_ingress,
                current_project_fingerprint=current_project_fingerprint,
                receipts=receipts,
            )
            if resumed is not None:
                return resumed
        if record.task_binding.state != "active":
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                "evidence absorption requires an active evidence task",
            )
        snapshot = self._snapshot(record)
        try:
            IterationTaskMaterializer(
                self.turn_store,
                self.checkpoint_store,
            ).recover(record, current_ingress=current_ingress)
        except TaskMaterializationError as exc:
            failure_code = (
                EvidenceEscalationFailureCode.AUTHORITY_STALE
                if exc.code
                in {
                    TaskMaterializationFailureCode.AUTHORITY_STALE,
                    TaskMaterializationFailureCode.CONFIRMATION_STALE,
                }
                else EvidenceEscalationFailureCode.CHECKPOINT_INVALID
            )
            raise EvidenceEscalationError(
                failure_code,
                "active evidence task no longer passes authority/checkpoint preflight",
            ) from exc
        receipt_by_obligation = {item.obligation_id: item for item in receipts}
        open_ids = {item.obligation_id for item in record.obligations if item.is_blocking}
        if (
            len(receipt_by_obligation) != len(receipts)
            or set(receipt_by_obligation) != open_ids
        ):
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.SOURCE_INCOMPATIBLE,
                "evidence receipts must uniquely and exactly cover open obligations",
            )
        closed: list[CompletionObligation] = []
        evidence_refs: list[str] = []
        project_hash = self.project_fingerprint_hash(snapshot.initial_checkpoint.project_fingerprint)
        if self.project_fingerprint_hash(current_project_fingerprint) != project_hash:
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.PROJECT_STALE,
                "current project fingerprint differs from the canonical evidence task",
            )
        for obligation in record.obligations:
            if not obligation.is_blocking:
                closed.append(obligation)
                evidence_refs.extend(obligation.evidence_refs)
                continue
            receipt = receipt_by_obligation.get(obligation.obligation_id)
            if receipt is None:
                raise EvidenceEscalationError(
                    EvidenceEscalationFailureCode.SOURCE_INCOMPATIBLE,
                    "every open obligation requires one evidence receipt",
                )
            expected_source = (
                ClaimSourceClass.PROJECT
                if obligation.kind == CompletionObligationKind.PROJECT_FACT
                else ClaimSourceClass.CURRENT_EXTERNAL
            )
            if receipt.source_class != expected_source:
                raise EvidenceEscalationError(
                    EvidenceEscalationFailureCode.SOURCE_INCOMPATIBLE,
                    "evidence receipt source differs from obligation kind",
                )
            payload = self.turn_store.load_artifact(
                record.identity.conversation_id,
                record.identity.run_id,
                receipt.artifact_ref,
            )
            if (
                receipt.artifact_ref.kind != "observed_evidence"
                or payload is None
                or self.evidence_payload_hash(payload) != receipt.content_hash
            ):
                raise EvidenceEscalationError(
                    EvidenceEscalationFailureCode.ARTIFACT_INVALID,
                    "evidence artifact is unavailable or checksum-mismatched",
                )
            try:
                artifact = EvidenceArtifactPayload.model_validate(payload)
            except ValueError as exc:
                raise EvidenceEscalationError(
                    EvidenceEscalationFailureCode.ARTIFACT_INVALID,
                    "evidence artifact payload is not a typed observation",
                ) from exc
            receipt_observed = self._utc(receipt.observed_at)
            artifact_observed = self._utc(artifact.observed_at)
            if (
                artifact.obligation_id != receipt.obligation_id
                or artifact.source_class != receipt.source_class
                or artifact_observed != receipt_observed
            ):
                raise EvidenceEscalationError(
                    EvidenceEscalationFailureCode.ARTIFACT_INVALID,
                    "evidence receipt provenance differs from its artifact",
                )
            checkpoint = self.checkpoint_store.load(record.identity.run_id, receipt.checkpoint_id)
            marker = f"evidence:{receipt.artifact_ref.artifact_id}:{receipt.content_hash}"
            if (
                checkpoint is None
                or checkpoint.integrity_checksum != receipt.checkpoint_digest
                or checkpoint.generation <= snapshot.initial_checkpoint.generation
                or checkpoint.root_task_id != snapshot.initial_checkpoint.root_task_id
                or checkpoint.runtime_state.execution_mode != "read_only"
                or checkpoint.runtime_state.core_success is not None
                or checkpoint.safe_boundary != "tool_result_applied"
                or checkpoint.side_effect_state != "applied"
                or marker not in checkpoint.runtime_state.known_facts
            ):
                raise EvidenceEscalationError(
                    EvidenceEscalationFailureCode.CHECKPOINT_INVALID,
                    "evidence receipt is not owned by a matching read-only task checkpoint",
                )
            checkpoint_ingress = checkpoint.session_ingress_state
            if (
                checkpoint_ingress is None
                or checkpoint_ingress.identity != current_ingress.identity
                or checkpoint_ingress.session_constraints
                != current_ingress.session_constraints
                or checkpoint.runtime_state.session_constraints
                != current_ingress.session_constraints
            ):
                raise EvidenceEscalationError(
                    EvidenceEscalationFailureCode.AUTHORITY_STALE,
                    "evidence checkpoint does not bind current session authority",
                )
            if self.project_fingerprint_hash(checkpoint.project_fingerprint) != project_hash:
                raise EvidenceEscalationError(
                    EvidenceEscalationFailureCode.PROJECT_STALE,
                    "evidence checkpoint project fingerprint differs from canonical task snapshot",
                )
            if expected_source == ClaimSourceClass.PROJECT:
                if checkpoint.tool_name not in {"file_reader", "multi_file_reader"}:
                    raise EvidenceEscalationError(
                        EvidenceEscalationFailureCode.SOURCE_INCOMPATIBLE,
                        "project evidence must originate from a registered project reader",
                    )
                if (
                    receipt.project_fingerprint_hash != project_hash
                ):
                    raise EvidenceEscalationError(
                        EvidenceEscalationFailureCode.PROJECT_STALE,
                        "project evidence fingerprint differs from canonical task snapshot",
                    )
            else:
                if checkpoint.tool_name != "web_searcher":
                    raise EvidenceEscalationError(
                        EvidenceEscalationFailureCode.SOURCE_INCOMPATIBLE,
                        "current-external evidence must originate from the web search tool",
                    )
                age = self.now() - artifact_observed
                if age < timedelta(0) or age > self.max_external_age:
                    raise EvidenceEscalationError(
                        EvidenceEscalationFailureCode.EXTERNAL_EVIDENCE_STALE,
                        "current-external evidence is outside the freshness window",
                    )
            evidence_ref = f"artifact:{receipt.artifact_ref.artifact_id}:{receipt.content_hash}"
            evidence_refs.append(evidence_ref)
            closed.append(
                obligation.model_copy(
                    update={
                        "status": CompletionObligationStatus.SATISFIED,
                        "evidence_refs": (evidence_ref,),
                    }
                )
            )
        required_ids = tuple(item.obligation_id for item in closed if item.required)
        grounding = GroundingDecision(
            response_hash=record.response_candidate.response_hash,
            status=GroundingStatus.APPROVED,
            obligation_ids=required_ids,
            satisfied_obligation_ids=required_ids,
            candidate_claim_coverage="complete",
            evidence_refs=tuple(evidence_refs),
        )
        evidence_complete = IterationTurnReducer.complete_evidence(
            record,
            cursor=record.cursor.model_copy(
                update={
                    "phase": IterationPhase.COMPLETE,
                    "current_disposition": IterationDisposition.COMPLETE_RESPONSE,
                    "open_obligation_ids": (),
                }
            ),
            obligations=tuple(closed),
            grounding=grounding,
        )
        try:
            evidence_complete = self.turn_store.save(
                evidence_complete,
                expected_generation=record.generation,
            )
        except IterationTurnConflictError:
            winner = self.turn_store.load_latest(
                record.identity.conversation_id,
                record.identity.run_id,
            )
            resumed = self._resume_durable_completion(
                record,
                latest=winner,
                current_ingress=current_ingress,
                current_project_fingerprint=current_project_fingerprint,
                receipts=receipts,
            )
            if resumed is None:
                raise
            return resumed
        self._after_write("evidence_complete")
        return self._prepare_and_commit(evidence_complete)

    def _prepare_and_commit(
        self,
        evidence_complete: IterationTurnRecordMetadata,
    ) -> IterationTurnRecordMetadata:
        record = evidence_complete
        grounding = record.grounding_decision
        if record.response_candidate is None or grounding is None:
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                "durable evidence completion lost its response candidate",
            )
        payload = self.turn_store.load_artifact(
            record.identity.conversation_id,
            record.identity.run_id,
            record.response_candidate.response_ref,
        )
        if (
            not self._valid_assistant_payload(payload)
            or assistant_payload_hash(payload) != record.response_candidate.response_hash
        ):
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.ARTIFACT_INVALID,
                "response candidate payload is unavailable after evidence completion",
            )
        outcome = CompletedResponseOutcome(
            response_ref=record.response_candidate.response_ref,
            response_hash=record.response_candidate.response_hash,
            grounding_decision_hash=grounding.canonical_hash,
        )
        pending = IterationTurnReducer.prepare_response(
            record,
            cursor=record.cursor,
            candidate=record.response_candidate,
            grounding=grounding,
            outcome=outcome,
            assistant_commit=AssistantTurnCommit(
                state=AssistantLedgerCommitState.PENDING,
                message_id=payload["message_id"],
                turn_index=payload["turn_index"],
                payload_ref=record.response_candidate.response_ref,
                payload_hash=record.response_candidate.response_hash,
            ),
        )
        return IterationTurnCommitter(
            self.turn_store,
            after_write=self.after_write,
        ).commit(pending).record

    def _resume_durable_completion(
        self,
        source: IterationTurnRecordMetadata,
        *,
        latest: IterationTurnRecordMetadata | None,
        current_ingress: SessionIngressState,
        current_project_fingerprint: ProjectFingerprint,
        receipts: tuple[EvidenceReceipt, ...],
    ) -> IterationTurnRecordMetadata | None:
        if latest is None or latest.response_candidate != source.response_candidate:
            return None
        if latest.task_binding.state != "none" or latest.grounding_decision is None:
            return None
        if latest.grounding_decision.status != GroundingStatus.APPROVED:
            return None
        self._validate_retry_authority(
            source,
            current_ingress,
            current_project_fingerprint=current_project_fingerprint,
        )
        expected_ids = {
            item.obligation_id
            for item in latest.obligations
            if item.kind
            in {
                CompletionObligationKind.PROJECT_FACT,
                CompletionObligationKind.CURRENT_EXTERNAL_FACT,
            }
            and item.required
        }
        receipt_ids = {item.obligation_id for item in receipts}
        receipt_refs = {
            f"artifact:{item.artifact_ref.artifact_id}:{item.content_hash}" for item in receipts
        }
        durable_refs = {
            ref
            for item in latest.obligations
            if item.obligation_id in expected_ids
            for ref in item.evidence_refs
        }
        if (
            len(receipt_ids) != len(receipts)
            or receipt_ids != expected_ids
            or receipt_refs != durable_refs
        ):
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.SOURCE_INCOMPATIBLE,
                "retry receipts differ from the durable evidence completion",
            )
        if latest.assistant_commit.state == AssistantLedgerCommitState.COMMITTED:
            return latest
        if latest.assistant_commit.state == AssistantLedgerCommitState.PENDING:
            return IterationTurnCommitter(
                self.turn_store,
                after_write=self.after_write,
            ).commit(latest).record
        if latest.outcome is None:
            return self._prepare_and_commit(latest)
        return None

    def _validate_retry_authority(
        self,
        source: IterationTurnRecordMetadata,
        current_ingress: SessionIngressState,
        *,
        current_project_fingerprint: ProjectFingerprint,
    ) -> None:
        snapshot = self._snapshot(source)
        constraints = current_ingress.session_constraints
        if (
            current_ingress.identity != source.identity
            or constraints.revision != snapshot.session_authority_revision
            or constraints.authority_hash != snapshot.session_authority_hash
        ):
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.AUTHORITY_STALE,
                "completion retry no longer matches canonical session authority",
            )
        if self.project_fingerprint_hash(current_project_fingerprint) != self.project_fingerprint_hash(
            snapshot.initial_checkpoint.project_fingerprint
        ):
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.PROJECT_STALE,
                "completion retry project fingerprint differs from the evidence task",
            )

    def _after_write(self, boundary: str) -> None:
        if self.after_write is not None:
            self.after_write(boundary)

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
        claims = (
            payload.get("claims")
            if isinstance(payload, dict) and set(payload) == {"claims"}
            else None
        )
        if not isinstance(claims, list) or len(claims) != len(candidate.claims):
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                "response claim manifest is unavailable or incomplete",
            )
        expected_ids = tuple(item.claim_id for item in candidate.claims)
        observed_ids = tuple(
            str(item.get("claim_id") or "") if isinstance(item, dict) else ""
            for item in claims
        )
        if len(set(observed_ids)) != len(observed_ids) or observed_ids != expected_ids:
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                "response claim manifest order or identity differs from candidate",
            )
        for claim, item in zip(candidate.claims, claims, strict=True):
            if not isinstance(item, dict) or set(item) != {
                "claim_id",
                "claim_hash",
                "source_class",
                "text",
            }:
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

    @staticmethod
    def _valid_assistant_payload(payload: Any) -> bool:
        return (
            isinstance(payload, dict)
            and set(payload) == {"message_id", "turn_index", "content"}
            and isinstance(payload["message_id"], str)
            and bool(payload["message_id"])
            and isinstance(payload["turn_index"], int)
            and not isinstance(payload["turn_index"], bool)
            and payload["turn_index"] >= 0
            and isinstance(payload["content"], str)
        )

    def _snapshot(self, record: IterationTurnRecordMetadata) -> CanonicalInitialTaskSnapshot:
        binding = record.task_binding
        payload = self.turn_store.load_artifact(
            record.identity.conversation_id,
            record.identity.run_id,
            binding.snapshot_ref,
        )
        try:
            return CanonicalInitialTaskSnapshot.model_validate(payload)
        except (TypeError, ValueError) as exc:
            raise EvidenceEscalationError(
                EvidenceEscalationFailureCode.CANDIDATE_INVALID,
                "active evidence task snapshot is unavailable",
            ) from exc

    @staticmethod
    def evidence_payload_hash(payload: dict[str, Any]) -> str:
        return EvidenceEscalationController._hash(payload)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def project_fingerprint_hash(fingerprint: ProjectFingerprint) -> str:
        return EvidenceEscalationController._hash(fingerprint.model_dump(mode="json"))

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


__all__ = [
    "EvidenceEscalationController",
    "EvidenceArtifactPayload",
    "EvidenceEscalationError",
    "EvidenceEscalationFailureCode",
    "EvidenceReceipt",
    "EvidenceRuntimeBridge",
    "PendingEvidenceObservation",
]
