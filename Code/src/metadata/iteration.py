"""Conversation-owned contracts for pre-task active iteration control."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metadata.agent_runtime import (
    CheckpointBoundary,
    ConversationIdentity,
    DurableArtifactReference,
    RuntimeCheckpointMetadata,
)
from metadata.base import MetadataBase, MetadataKind
from metadata.project import TaskGraphNodeMetadata


_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


class _StrictValue(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)


class IterationDisposition(str, Enum):
    COMPLETE_RESPONSE = "complete_response"
    ASK_USER = "ask_user"
    RAISE_DECISION_NEED = "raise_decision_need"
    FORM_SINGLE_TASK = "form_single_task"
    DECOMPOSE_TASK = "decompose_task"
    CONTROLLED_STOP = "controlled_stop"


class IterationOutcome(str, Enum):
    COMPLETED = "completed"
    AWAITING_USER = "awaiting_user"
    BLOCKED = "blocked"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class IterationOutcomeKind(str, Enum):
    RESPONSE_COMPLETED = "response_completed"
    PROJECT_TASK_COMPLETED = "project_task_completed"
    AWAITING_USER = "awaiting_user"
    CONTROLLED_STOP = "controlled_stop"


class IterationCompletionScope(str, Enum):
    RESPONSE_ONLY = "response_only"
    PROJECT_TASK = "project_task"


class IterationStopReason(str, Enum):
    COMPLETED = "completed"
    USER_INPUT_REQUIRED = "user_input_required"
    OBLIGATION_UNSATISFIED = "obligation_unsatisfied"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_PROGRESS = "no_progress"
    PERMISSION_DENIED = "permission_denied"
    INTERNAL_FAILURE = "internal_failure"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class IterationPhase(str, Enum):
    UNDERSTAND_TASK = "understand_task"
    GROUND_RESPONSE = "ground_response"
    EVIDENCE = "evidence"
    MATERIALIZE_TASK = "materialize_task"
    COMPLETE = "complete"
    STOPPED = "stopped"


class IterationBoundary(str, Enum):
    ITERATION_INITIALIZED = "iteration_initialized"
    DECISION_REQUESTED = "decision_requested"
    DECISION_RECORDED = "decision_recorded"
    EVIDENCE_NEED_RECORDED = "evidence_need_recorded"
    COMPLETION_CANDIDATE_RECORDED = "completion_candidate_recorded"
    COMPLETION_APPROVED = "completion_approved"
    TURN_RESPONSE_DURABLE = "turn_response_durable"


class IterationAuthorityCeiling(str, Enum):
    RESPONSE_ONLY = "response_only"
    READ_ONLY_ELIGIBLE = "read_only_eligible"
    MUTATION_ELIGIBLE = "mutation_eligible"


class IterationAuthoritySource(str, Enum):
    RUNTIME_DEFAULT = "runtime_default"
    USER_INTENT = "user_intent"
    SESSION_CONSTRAINT = "session_constraint"
    USER_CONFIRMATION = "user_confirmation"
    TASK_CONTRACT = "task_contract"


class CompletionObligationKind(str, Enum):
    RUNTIME_FACT = "runtime_fact"
    PROJECT_FACT = "project_fact"
    CURRENT_EXTERNAL_FACT = "current_external_fact"
    ACCEPTANCE = "acceptance"
    VERIFICATION = "verification"
    SIDE_EFFECT = "side_effect"
    PERMISSION = "permission"
    CONFIRMATION = "confirmation"
    INDETERMINATE_SIDE_EFFECT = "indeterminate_side_effect"


class CompletionObligationStatus(str, Enum):
    OPEN = "open"
    SATISFIED = "satisfied"
    WAIVED = "waived"
    STALE = "stale"
    DISPUTED = "disputed"
    IMPOSSIBLE = "impossible"


class WaiverAuthority(str, Enum):
    USER = "user"
    RUNTIME_POLICY = "runtime_policy"


class GroundingStatus(str, Enum):
    APPROVED = "approved"
    REPAIR_REQUIRED = "repair_required"
    EVIDENCE_REQUIRED = "evidence_required"
    REJECTED = "rejected"


class ClaimSourceClass(str, Enum):
    CONVERSATION = "conversation"
    RUNTIME = "runtime"
    PROJECT = "project"
    CURRENT_EXTERNAL = "current_external"
    STABLE_KNOWLEDGE = "stable_knowledge"


class AssistantLedgerCommitState(str, Enum):
    NONE = "none"
    PENDING = "pending"
    COMMITTED = "committed"


class TaskBindingState(str, Enum):
    NONE = "none"
    PREPARED = "prepared"
    ACTIVE = "active"


class IterationAuthorityState(_StrictValue):
    ceiling: IterationAuthorityCeiling = IterationAuthorityCeiling.RESPONSE_ONLY
    source: IterationAuthoritySource = IterationAuthoritySource.RUNTIME_DEFAULT
    reason: str = Field(min_length=1, max_length=1000)
    revision: int = Field(default=0, ge=0)
    authority_hash: str = Field(pattern=_SHA256_PATTERN)
    confirmation_message_id: str | None = Field(default=None, min_length=1)
    confirmation_turn_index: int | None = Field(default=None, ge=0)
    rejected_or_revoked_ids: tuple[str, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def _mutation_eligibility_has_confirmation(self) -> "IterationAuthorityState":
        if self.ceiling == IterationAuthorityCeiling.MUTATION_ELIGIBLE and (
            not self.confirmation_message_id or self.confirmation_turn_index is None
        ):
            raise ValueError("mutation eligibility requires confirmation lineage")
        if (self.confirmation_message_id is None) != (self.confirmation_turn_index is None):
            raise ValueError("confirmation message and turn index must appear together")
        return self


class PreTaskState(_StrictValue):
    user_message_id: str = Field(min_length=1)
    user_input_ref: DurableArtifactReference
    session_authority_revision: int = Field(default=0, ge=0)
    session_authority_hash: str = Field(pattern=_SHA256_PATTERN)
    runtime_fact_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)


class IterationPendingProviderRequest(_StrictValue):
    request_id: str = Field(min_length=1)
    request_ordinal: int = Field(ge=1)
    request_hash: str = Field(pattern=_SHA256_PATTERN)
    request_ref: DurableArtifactReference
    purpose: Literal["response", "grounding_repair", "decomposition"]

    @model_validator(mode="after")
    def _request_reference_has_expected_kind(self) -> "IterationPendingProviderRequest":
        if self.request_ref.kind != "provider_request":
            raise ValueError("pending provider request reference has the wrong kind")
        return self


class ResponseClaim(_StrictValue):
    claim_id: str = Field(min_length=1, max_length=160)
    claim_hash: str = Field(pattern=_SHA256_PATTERN)
    source_class: ClaimSourceClass


class ResponseCandidate(_StrictValue):
    candidate_id: str = Field(min_length=1)
    response_ref: DurableArtifactReference
    response_hash: str = Field(pattern=_SHA256_PATTERN)
    claims: tuple[ResponseClaim, ...] = Field(default=(), max_length=128)
    claim_manifest_ref: DurableArtifactReference | None = None

    @model_validator(mode="after")
    def _claim_ids_are_unique(self) -> "ResponseCandidate":
        claim_ids = [item.claim_id for item in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("response claim IDs must be unique")
        return self


class CompletionObligation(_StrictValue):
    obligation_id: str = Field(min_length=1, max_length=160)
    kind: CompletionObligationKind
    required: bool = True
    status: CompletionObligationStatus = CompletionObligationStatus.OPEN
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=64)
    waiver_authority: WaiverAuthority | None = None
    waiver_reason: str | None = Field(default=None, max_length=1000)

    @property
    def is_closed(self) -> bool:
        return self.status in {
            CompletionObligationStatus.SATISFIED,
            CompletionObligationStatus.WAIVED,
        }

    @property
    def is_blocking(self) -> bool:
        return self.required and not self.is_closed

    @model_validator(mode="after")
    def _closure_is_evidence_backed(self) -> "CompletionObligation":
        if self.status == CompletionObligationStatus.SATISFIED and not self.evidence_refs:
            raise ValueError("satisfied obligation requires evidence")
        non_waivable = {
            CompletionObligationKind.VERIFICATION,
            CompletionObligationKind.SIDE_EFFECT,
            CompletionObligationKind.PERMISSION,
            CompletionObligationKind.CONFIRMATION,
            CompletionObligationKind.INDETERMINATE_SIDE_EFFECT,
        }
        if self.status == CompletionObligationStatus.WAIVED:
            if self.kind in non_waivable:
                raise ValueError(f"{self.kind} obligation cannot be waived")
            if self.waiver_authority is None or not self.waiver_reason:
                raise ValueError("waived obligation requires typed authority and reason")
        elif self.waiver_authority is not None or self.waiver_reason is not None:
            raise ValueError("waiver facts require waived status")
        return self


class GroundingDecision(_StrictValue):
    response_hash: str = Field(pattern=_SHA256_PATTERN)
    status: GroundingStatus
    obligation_ids: tuple[str, ...] = Field(default=(), max_length=64)
    satisfied_obligation_ids: tuple[str, ...] = Field(default=(), max_length=64)
    waived_obligation_ids: tuple[str, ...] = Field(default=(), max_length=64)
    open_obligation_ids: tuple[str, ...] = Field(default=(), max_length=64)
    candidate_claim_coverage: Literal["complete", "incomplete"]
    unbound_claim_ids: tuple[str, ...] = Field(default=(), max_length=128)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=128)

    @property
    def canonical_hash(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def _approval_is_complete(self) -> "GroundingDecision":
        if self.status == GroundingStatus.APPROVED and (
            self.open_obligation_ids
            or self.unbound_claim_ids
            or self.candidate_claim_coverage != "complete"
            or set(self.satisfied_obligation_ids) & set(self.waived_obligation_ids)
            or set(self.satisfied_obligation_ids) | set(self.waived_obligation_ids)
            != set(self.obligation_ids)
        ):
            raise ValueError("approved grounding requires complete claims and obligations")
        return self


class RootDecisionBudget(_StrictValue):
    max_decision_rounds: int = Field(default=6, ge=1, le=32)
    max_root_provider_calls: int = Field(default=4, ge=0, le=16)
    max_response_completion_tokens: int = Field(default=4000, ge=1, le=32000)
    max_grounding_repairs: int = Field(default=1, ge=0, le=4)
    max_decomposition_calls: int = Field(default=1, ge=0, le=4)
    max_no_progress_rounds: int = Field(default=2, ge=0, le=8)
    decision_rounds_used: int = Field(default=0, ge=0)
    root_provider_calls_used: int = Field(default=0, ge=0)
    response_completion_tokens_used: int = Field(default=0, ge=0)
    grounding_repairs_used: int = Field(default=0, ge=0)
    decomposition_calls_used: int = Field(default=0, ge=0)
    no_progress_rounds: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _usage_fits_limits(self) -> "RootDecisionBudget":
        checks = (
            (self.decision_rounds_used, self.max_decision_rounds, "decision rounds"),
            (self.root_provider_calls_used, self.max_root_provider_calls, "provider calls"),
            (
                self.response_completion_tokens_used,
                self.max_response_completion_tokens,
                "response tokens",
            ),
            (self.grounding_repairs_used, self.max_grounding_repairs, "grounding repairs"),
            (self.decomposition_calls_used, self.max_decomposition_calls, "decomposition calls"),
            (self.no_progress_rounds, self.max_no_progress_rounds, "no-progress rounds"),
        )
        for used, maximum, label in checks:
            if used > maximum:
                raise ValueError(f"{label} usage exceeds its limit")
        return self


class CanonicalInitialTaskSnapshot(_StrictValue):
    """Complete typed input for deterministic initial-checkpoint materialization."""

    protocol_version: Literal["cru-2a-v1"] = "cru-2a-v1"
    canonical_serialization_version: Literal["initial-task-v1"] = "initial-task-v1"
    task_graph: tuple[TaskGraphNodeMetadata, ...] = Field(min_length=1, max_length=7)
    execution_order: tuple[str, ...] = Field(min_length=1, max_length=7)
    initial_checkpoint: RuntimeCheckpointMetadata
    authority_state: IterationAuthorityState
    root_budget: RootDecisionBudget
    session_authority_revision: int = Field(ge=0)
    session_authority_hash: str = Field(pattern=_SHA256_PATTERN)

    @property
    def canonical_hash(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def _snapshot_is_deterministically_materializable(self) -> "CanonicalInitialTaskSnapshot":
        task_ids = [task.task_id for task in self.task_graph]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("canonical task graph IDs must be unique")
        if len(self.execution_order) != len(task_ids) or set(self.execution_order) != set(task_ids):
            raise ValueError("canonical task execution order must contain every task exactly once")
        checkpoint = self.initial_checkpoint
        if checkpoint.root_task_id not in task_ids:
            raise ValueError("initial checkpoint root task is absent from canonical task graph")
        if (
            checkpoint.generation != 1
            or checkpoint.integrity_checksum
            or checkpoint.safe_boundary != CheckpointBoundary.DECOMPOSITION_RECORDED
            or checkpoint.side_effect_state != "none"
        ):
            raise ValueError("canonical initial checkpoint must be an unsigned side-effect-free generation one")
        ingress = checkpoint.session_ingress_state
        if ingress is None:
            raise ValueError("canonical initial checkpoint requires session ingress")
        constraints = ingress.session_constraints
        if (
            constraints.revision != self.session_authority_revision
            or constraints.authority_hash != self.session_authority_hash
        ):
            raise ValueError("canonical snapshot session authority differs from checkpoint ingress")
        if checkpoint.project_fingerprint.project_root != ingress.identity.project_root:
            raise ValueError("canonical snapshot project identity differs")
        return self


class IterationControlCursor(_StrictValue):
    protocol_version: Literal["cru-2a-v1"] = "cru-2a-v1"
    decision_ordinal: int = Field(default=0, ge=0)
    phase: IterationPhase = IterationPhase.UNDERSTAND_TASK
    current_disposition: IterationDisposition | None = None
    authority_state: IterationAuthorityState
    open_obligation_ids: tuple[str, ...] = Field(default=(), max_length=64)
    completion_candidate_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    decision_progress_signature: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    pending_provider_request: IterationPendingProviderRequest | None = None
    observed_provider_response_ref: DurableArtifactReference | None = None

    @model_validator(mode="after")
    def _ids_are_unique(self) -> "IterationControlCursor":
        if len(self.open_obligation_ids) != len(set(self.open_obligation_ids)):
            raise ValueError("open obligation IDs must be unique")
        if (
            self.pending_provider_request is not None
            and self.observed_provider_response_ref is not None
        ):
            raise ValueError("pending request and observed response are mutually exclusive")
        if (
            self.observed_provider_response_ref is not None
            and self.observed_provider_response_ref.kind != "provider_response"
        ):
            raise ValueError("observed provider response reference has the wrong kind")
        if (
            self.observed_provider_response_ref is not None
            and self.decision_progress_signature is None
        ):
            raise ValueError("observed provider response requires a progress signature")
        return self


class AssistantTurnCommit(_StrictValue):
    state: AssistantLedgerCommitState = AssistantLedgerCommitState.NONE
    message_id: str | None = Field(default=None, min_length=1)
    turn_index: int | None = Field(default=None, ge=0)
    payload_ref: DurableArtifactReference | None = None
    payload_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _commit_identity_matches_state(self) -> "AssistantTurnCommit":
        values = (self.message_id, self.turn_index, self.payload_ref, self.payload_hash)
        if self.state == AssistantLedgerCommitState.NONE and any(item is not None for item in values):
            raise ValueError("unprepared assistant commit cannot carry payload identity")
        if self.state != AssistantLedgerCommitState.NONE and any(item is None for item in values):
            raise ValueError("pending or committed assistant commit requires payload identity")
        return self


class NoTaskBinding(_StrictValue):
    state: Literal[TaskBindingState.NONE] = TaskBindingState.NONE


class PreparedTaskBinding(_StrictValue):
    state: Literal[TaskBindingState.PREPARED] = TaskBindingState.PREPARED
    task_id: str = Field(min_length=1)
    state_digest: str = Field(pattern=_SHA256_PATTERN)
    snapshot_ref: DurableArtifactReference
    snapshot_hash: str = Field(pattern=_SHA256_PATTERN)
    authority_revision: int = Field(ge=0)
    authority_hash: str = Field(pattern=_SHA256_PATTERN)


class ActiveTaskBinding(PreparedTaskBinding):
    state: Literal[TaskBindingState.ACTIVE] = TaskBindingState.ACTIVE
    checkpoint_id: str = Field(min_length=1)
    checkpoint_digest: str = Field(pattern=_SHA256_PATTERN)


TaskBinding = Annotated[
    NoTaskBinding | PreparedTaskBinding | ActiveTaskBinding,
    Field(discriminator="state"),
]


class CompletedResponseOutcome(_StrictValue):
    outcome_kind: Literal[IterationOutcomeKind.RESPONSE_COMPLETED] = (
        IterationOutcomeKind.RESPONSE_COMPLETED
    )
    outcome: Literal[IterationOutcome.COMPLETED] = IterationOutcome.COMPLETED
    completion_scope: Literal[IterationCompletionScope.RESPONSE_ONLY] = (
        IterationCompletionScope.RESPONSE_ONLY
    )
    stop_reason: Literal[IterationStopReason.COMPLETED] = IterationStopReason.COMPLETED
    response_ref: DurableArtifactReference
    response_hash: str = Field(pattern=_SHA256_PATTERN)
    grounding_decision_hash: str = Field(pattern=_SHA256_PATTERN)


class CompletedProjectTaskOutcome(_StrictValue):
    outcome_kind: Literal[IterationOutcomeKind.PROJECT_TASK_COMPLETED] = (
        IterationOutcomeKind.PROJECT_TASK_COMPLETED
    )
    outcome: Literal[IterationOutcome.COMPLETED] = IterationOutcome.COMPLETED
    completion_scope: Literal[IterationCompletionScope.PROJECT_TASK] = (
        IterationCompletionScope.PROJECT_TASK
    )
    stop_reason: Literal[IterationStopReason.COMPLETED] = IterationStopReason.COMPLETED
    runtime_report_ref: DurableArtifactReference
    runtime_report_hash: str = Field(pattern=_SHA256_PATTERN)


class AwaitingUserOutcome(_StrictValue):
    outcome_kind: Literal[IterationOutcomeKind.AWAITING_USER] = IterationOutcomeKind.AWAITING_USER
    outcome: Literal[IterationOutcome.AWAITING_USER] = IterationOutcome.AWAITING_USER
    completion_scope: None = None
    stop_reason: Literal[IterationStopReason.USER_INPUT_REQUIRED] = (
        IterationStopReason.USER_INPUT_REQUIRED
    )
    question: str = Field(min_length=1, max_length=4000)


class ControlledStopOutcome(_StrictValue):
    outcome_kind: Literal[IterationOutcomeKind.CONTROLLED_STOP] = IterationOutcomeKind.CONTROLLED_STOP
    outcome: Literal[
        IterationOutcome.BLOCKED,
        IterationOutcome.FAILED,
        IterationOutcome.INTERRUPTED,
        IterationOutcome.CANCELLED,
    ]
    completion_scope: None = None
    stop_reason: IterationStopReason
    failure_ref: DurableArtifactReference | None = None

    @model_validator(mode="after")
    def _reason_matches_outcome(self) -> "ControlledStopOutcome":
        expected = {
            IterationOutcome.INTERRUPTED: IterationStopReason.INTERRUPTED,
            IterationOutcome.CANCELLED: IterationStopReason.CANCELLED,
            IterationOutcome.FAILED: IterationStopReason.INTERNAL_FAILURE,
        }
        required = expected.get(self.outcome)
        if required is not None and self.stop_reason != required:
            raise ValueError("controlled stop reason does not match outcome")
        if self.outcome == IterationOutcome.BLOCKED and self.stop_reason in {
            IterationStopReason.COMPLETED,
            IterationStopReason.INTERRUPTED,
            IterationStopReason.CANCELLED,
        }:
            raise ValueError("blocked outcome requires a blocking stop reason")
        return self


class PreviousOutcomeReference(_StrictValue):
    run_id: str = Field(min_length=1)
    outcome: IterationOutcome
    completion_scope: IterationCompletionScope | None = None
    response_artifact_ref: DurableArtifactReference | None = None
    runtime_report_ref: DurableArtifactReference | None = None
    project_fingerprint_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    evidence_digest: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _reference_matches_scope(self) -> "PreviousOutcomeReference":
        if self.completion_scope == IterationCompletionScope.RESPONSE_ONLY:
            if self.response_artifact_ref is None or self.runtime_report_ref is not None:
                raise ValueError("response-only reference requires only a response artifact")
        if self.completion_scope == IterationCompletionScope.PROJECT_TASK:
            if self.runtime_report_ref is None or self.project_fingerprint_hash is None:
                raise ValueError("project-task reference requires report and project fingerprint")
        if self.outcome != IterationOutcome.COMPLETED and self.completion_scope is not None:
            raise ValueError("non-completed previous outcome cannot have completion scope")
        if self.outcome == IterationOutcome.COMPLETED and self.completion_scope is None:
            raise ValueError("completed previous outcome requires completion scope")
        return self


TurnOutcome = Annotated[
    CompletedResponseOutcome
    | CompletedProjectTaskOutcome
    | AwaitingUserOutcome
    | ControlledStopOutcome,
    Field(discriminator="outcome_kind"),
]


class IterationTurnRecordMetadata(MetadataBase):
    """Durable source of truth for one autonomous pre-task conversation run."""

    kind: Literal[MetadataKind.ITERATION_TURN_RECORD] = MetadataKind.ITERATION_TURN_RECORD
    protocol_version: Literal["cru-2a-v1"] = "cru-2a-v1"
    record_id: str = Field(min_length=1)
    identity: ConversationIdentity
    pre_task_state: PreTaskState
    generation: int = Field(default=1, ge=1)
    boundary: IterationBoundary = IterationBoundary.ITERATION_INITIALIZED
    cursor: IterationControlCursor
    root_budget: RootDecisionBudget
    obligations: tuple[CompletionObligation, ...] = Field(default=(), max_length=64)
    response_candidate: ResponseCandidate | None = None
    grounding_decision: GroundingDecision | None = None
    outcome: TurnOutcome | None = None
    assistant_commit: AssistantTurnCommit = Field(default_factory=AssistantTurnCommit)
    task_binding: TaskBinding = Field(default_factory=NoTaskBinding)
    previous_outcome_ref: PreviousOutcomeReference | None = None
    integrity_digest: str = Field(default="", pattern=r"^(?:|sha256:[0-9a-f]{64})$")

    @property
    def core_success(self) -> None:
        """Response/pre-task records never own project-task success."""

        return None

    @model_validator(mode="after")
    def _record_state_is_consistent(self) -> "IterationTurnRecordMetadata":
        obligation_ids = [item.obligation_id for item in self.obligations]
        if len(obligation_ids) != len(set(obligation_ids)):
            raise ValueError("completion obligation IDs must be unique")
        open_ids = {item.obligation_id for item in self.obligations if item.is_blocking}
        if set(self.cursor.open_obligation_ids) != open_ids:
            raise ValueError("cursor open obligation IDs differ from obligation state")
        if self.outcome is not None and isinstance(self.outcome, CompletedResponseOutcome):
            if not isinstance(self.task_binding, NoTaskBinding):
                raise ValueError("response-only completion cannot bind a task")
            if self.grounding_decision is None:
                raise ValueError("response-only completion requires grounding")
            if self.grounding_decision.status != GroundingStatus.APPROVED:
                raise ValueError("response-only completion requires approved grounding")
            required_ids = {item.obligation_id for item in self.obligations if item.required}
            if set(self.grounding_decision.obligation_ids) != required_ids:
                raise ValueError("grounding obligations differ from required obligations")
            if (
                self.cursor.phase != IterationPhase.COMPLETE
                or self.cursor.current_disposition != IterationDisposition.COMPLETE_RESPONSE
            ):
                raise ValueError("response-only completion requires the complete cursor state")
            if self.response_candidate is None:
                raise ValueError("response-only completion requires a response candidate")
            if (
                self.response_candidate.response_hash != self.outcome.response_hash
                or self.response_candidate.response_ref != self.outcome.response_ref
            ):
                raise ValueError("response candidate differs from response outcome")
            if self.outcome.response_hash != self.grounding_decision.response_hash:
                raise ValueError("response outcome and grounding hashes differ")
            if self.outcome.grounding_decision_hash != self.grounding_decision.canonical_hash:
                raise ValueError("response outcome grounding digest differs")
        if self.outcome is not None and isinstance(self.outcome, CompletedProjectTaskOutcome):
            if not isinstance(self.task_binding, ActiveTaskBinding):
                raise ValueError("project-task completion requires an active task binding")
            if self.cursor.phase != IterationPhase.COMPLETE:
                raise ValueError("project-task completion requires the complete cursor state")
        if self.assistant_commit.state != AssistantLedgerCommitState.NONE:
            if self.assistant_commit.turn_index != self.identity.turn_index + 1:
                raise ValueError("assistant commit turn index must follow the user turn")
        if self.boundary == IterationBoundary.TURN_RESPONSE_DURABLE:
            if not isinstance(self.outcome, CompletedResponseOutcome):
                raise ValueError("durable response boundary requires response completion")
            if self.assistant_commit.state != AssistantLedgerCommitState.COMMITTED:
                raise ValueError("durable response boundary requires committed assistant turn")
            if (
                self.assistant_commit.payload_hash != self.outcome.response_hash
                or self.assistant_commit.payload_ref != self.outcome.response_ref
            ):
                raise ValueError("assistant commit payload differs from response outcome")
        return self


__all__ = [
    "ActiveTaskBinding",
    "AssistantLedgerCommitState",
    "AssistantTurnCommit",
    "AwaitingUserOutcome",
    "CanonicalInitialTaskSnapshot",
    "ClaimSourceClass",
    "CompletedProjectTaskOutcome",
    "CompletedResponseOutcome",
    "CompletionObligation",
    "CompletionObligationKind",
    "CompletionObligationStatus",
    "ControlledStopOutcome",
    "GroundingDecision",
    "GroundingStatus",
    "IterationAuthorityCeiling",
    "IterationAuthoritySource",
    "IterationAuthorityState",
    "IterationBoundary",
    "IterationCompletionScope",
    "IterationControlCursor",
    "IterationDisposition",
    "IterationOutcome",
    "IterationOutcomeKind",
    "IterationPendingProviderRequest",
    "IterationPhase",
    "IterationStopReason",
    "IterationTurnRecordMetadata",
    "NoTaskBinding",
    "PreTaskState",
    "PreparedTaskBinding",
    "PreviousOutcomeReference",
    "ResponseCandidate",
    "ResponseClaim",
    "RootDecisionBudget",
    "TaskBindingState",
    "WaiverAuthority",
]
