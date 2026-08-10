"""Agent runtime state contracts for phase-driven execution."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from metadata.base import JsonValue, MetadataBase, MetadataKind, json_safe
from metadata.project import TaskGraphNodeMetadata
from metadata.results import FailureMetadata
from metadata.tooling import ToolInputMetadata


class AgentPhase(str, Enum):
    """High-level runtime phases used to drive tool and edit decisions."""

    UNDERSTAND_TASK = "understand_task"
    UNDERSTAND_PROJECT = "understand_project"
    DIAGNOSE = "diagnose"
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    RECOVER = "recover"
    REPLAN = "replan"
    ASK_USER = "ask_user"
    SUMMARIZE = "summarize"
    BLOCKED = "blocked"


class RuntimeExecutionMode(str, Enum):
    """Root-task execution authority persisted across subtasks and resume."""

    READ_ONLY = "read_only"
    MUTATION_ALLOWED = "mutation_allowed"


class RuntimeExecutionModeSource(str, Enum):
    """Evidence source that established the root-task execution mode."""

    USER_CONSTRAINT = "user_constraint"
    ROOT_TASK_CARD = "root_task_card"
    ROOT_GOAL = "root_goal"
    DEFAULT = "default"
    LEGACY_ASSUMPTION = "legacy_assumption"


class ToolEventCompletionOutcome(str, Enum):
    """Typed response outcome that may inform the next completion budget."""

    NORMAL = "normal"
    TOOL_PROGRESS = "tool_progress"
    EMPTY_RESPONSE = "empty_response"
    TRUNCATED = "truncated"
    NO_PROGRESS = "no_progress"


class ProviderBudgetDiagnostic(BaseModel):
    """Strict per-attempt budget evidence emitted by the provider tool runner."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    round_index: int = Field(ge=1)
    requested_limit: int = Field(ge=1)
    reserved_tokens: int = Field(ge=1)
    actual_completion_tokens: int | None = Field(default=None, ge=0)
    usage_known: bool
    budget_tokens_used_before: int = Field(ge=0)
    budget_tokens_used_after: int = Field(ge=0)
    budget_tokens_remaining_before: int = Field(ge=0)
    budget_tokens_remaining_after: int = Field(ge=0)
    recovery_bonus_before: int = Field(ge=0)
    recovery_bonus_after: int = Field(ge=0)
    finish_reason: str | None = None
    outcome: ToolEventCompletionOutcome | None = None
    provider_cap_hit: bool
    outcome_feedback_enabled: bool
    provider_attempt_failed: bool = False
    error_type: str | None = None

    @model_validator(mode="after")
    def _facts_are_consistent(self) -> "ProviderBudgetDiagnostic":
        if self.requested_limit != self.reserved_tokens:
            raise ValueError("requested and reserved completion tokens must match")
        if self.usage_known != (self.actual_completion_tokens is not None):
            raise ValueError("usage-known state must match actual completion token presence")
        if (
            self.actual_completion_tokens is not None
            and self.actual_completion_tokens > self.reserved_tokens
        ):
            raise ValueError("actual completion usage cannot exceed the reserved amount")
        if self.provider_attempt_failed:
            if not self.error_type:
                raise ValueError("failed provider attempts require an error type")
        return self


class SessionConstraintCategory(str, Enum):
    """Typed categories that may persist for one runtime conversation."""

    WRITE_SCOPE = "write_scope"
    VALIDATION_COMMAND = "validation_command"
    API_COMPATIBILITY = "api_compatibility"
    GOAL_ACCEPTANCE = "goal_acceptance"
    EXECUTION_MODE = "execution_mode"


class SessionConstraintStatus(str, Enum):
    """Lifecycle state of a constraint snapshot entry."""

    ACTIVE = "active"
    REVOKED = "revoked"


class SessionConstraintProposalStatus(str, Enum):
    """Proposal state before it is projected into the active ledger."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class SessionConstraintSourceKind(str, Enum):
    """Typed source kind for a session constraint's evidence lineage."""

    USER_MESSAGE = "user_message"
    USER_CONFIRMATION = "user_confirmation"
    TYPED_TASK = "typed_task"


class SessionConstraintAuthority(str, Enum):
    """Authority that allowed a constraint to enter the active ledger."""

    EXPLICIT_USER = "explicit_user"
    USER_CONFIRMED = "user_confirmed"
    TYPED_TASK = "typed_task"


class SessionConstraintViolationCode(str, Enum):
    """Typed final-gate denial reasons for active session constraints."""

    WRITE_SCOPE = "write_scope"
    READ_ONLY = "read_only"
    VALIDATION_COMMAND = "validation_command"
    PROJECT_ROOT_MISMATCH = "project_root_mismatch"


class SessionConstraintLimits(BaseModel):
    """Typed hard bounds for conversation-scoped constraint state.

    The limits are part of the state contract so checkpoint restore and replay
    apply the same fail-closed bounds as live ingress. Missing limits in older
    checkpoints are migrated to these conservative defaults by Pydantic.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    max_pending_proposals: int = Field(default=32, ge=1, le=256)
    max_pending_serialized_chars: int = Field(default=32_768, ge=1_024, le=1_000_000)
    max_active_entries: int = Field(default=16, ge=1, le=128)
    max_revoked_tombstones: int = Field(default=32, ge=1, le=256)
    max_active_serialized_chars: int = Field(default=32_768, ge=1_024, le=1_000_000)
    max_revoked_serialized_chars: int = Field(default=32_768, ge=1_024, le=1_000_000)
    max_entries_serialized_chars: int = Field(default=65_536, ge=1_024, le=2_000_000)
    max_value_serialized_chars: int = Field(default=8_192, ge=128, le=1_000_000)
    max_scope_paths: int = Field(default=64, ge=1, le=512)
    max_validation_commands: int = Field(default=16, ge=1, le=128)
    max_acceptance_criteria: int = Field(default=32, ge=1, le=256)
    max_value_item_chars: int = Field(default=512, ge=1, le=16_384)


class SessionConstraintValue(BaseModel):
    """One typed value variant; statement text never controls execution."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    allowed_files: list[str] = Field(default_factory=list)
    forbidden_files: list[str] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)
    api_compatibility: Literal["preserve_existing_api"] | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    execution_mode: RuntimeExecutionMode | None = None

    @model_validator(mode="after")
    def _exactly_one_typed_variant(self) -> "SessionConstraintValue":
        variants = [
            bool(self.allowed_files or self.forbidden_files),
            bool(self.validation_commands),
            self.api_compatibility is not None,
            bool(self.acceptance_criteria),
            self.execution_mode is not None,
        ]
        if sum(variants) != 1:
            raise ValueError("session constraint value requires exactly one typed variant")
        if any(not str(item).strip() for item in (*self.allowed_files, *self.forbidden_files)):
            raise ValueError("session write-scope paths must be non-empty")
        if any(not str(item).strip() for item in self.validation_commands):
            raise ValueError("session validation commands must be non-empty")
        if any(not str(item).strip() for item in self.acceptance_criteria):
            raise ValueError("session acceptance criteria must be non-empty")
        return self


def _validate_session_constraint_category(
    category: SessionConstraintCategory,
    value: SessionConstraintValue,
) -> None:
    expected = {
        SessionConstraintCategory.WRITE_SCOPE: bool(value.allowed_files or value.forbidden_files),
        SessionConstraintCategory.VALIDATION_COMMAND: bool(value.validation_commands),
        SessionConstraintCategory.API_COMPATIBILITY: value.api_compatibility is not None,
        SessionConstraintCategory.GOAL_ACCEPTANCE: bool(value.acceptance_criteria),
        SessionConstraintCategory.EXECUTION_MODE: value.execution_mode is not None,
    }
    if not expected[category]:
        raise ValueError("session constraint category does not match typed value")


def _serialized_chars(value: Any) -> int:
    """Return deterministic UTF-8 JSON size for a typed value or model list."""

    if isinstance(value, list):
        payload = [item.model_dump(mode="json") if isinstance(item, BaseModel) else item for item in value]
    elif isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(encoded.encode("utf-8"))


def _validate_constraint_value_limits(
    value: SessionConstraintValue,
    limits: SessionConstraintLimits,
) -> None:
    """Apply variant-specific hard bounds before a state is accepted."""

    if _serialized_chars(value) > limits.max_value_serialized_chars:
        raise ValueError("session constraint value serialized size quota exceeded")
    scope_paths = [*value.allowed_files, *value.forbidden_files]
    if len(scope_paths) > limits.max_scope_paths:
        raise ValueError("session constraint scope path quota exceeded")
    if len(value.validation_commands) > limits.max_validation_commands:
        raise ValueError("session constraint validation command quota exceeded")
    if len(value.acceptance_criteria) > limits.max_acceptance_criteria:
        raise ValueError("session constraint acceptance criterion quota exceeded")
    items = [*scope_paths, *value.validation_commands, *value.acceptance_criteria]
    if any(len(str(item)) > limits.max_value_item_chars for item in items):
        raise ValueError("session constraint value item size quota exceeded")


class SessionConstraintProposal(BaseModel):
    """Source-linked proposal that has no runtime effect by itself."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    proposal_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    constraint_key: str = Field(min_length=1)
    category: SessionConstraintCategory
    value: SessionConstraintValue
    statement: str = Field(min_length=1, max_length=512)
    source_kind: SessionConstraintSourceKind
    source_id: str = Field(min_length=1)
    source_turn_index: int = Field(ge=1)
    source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: SessionConstraintProposalStatus = SessionConstraintProposalStatus.PROPOSED
    supersedes_proposal_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _proposal_is_source_bound(self) -> "SessionConstraintProposal":
        _validate_session_constraint_category(self.category, self.value)
        if self.supersedes_proposal_id == self.proposal_id:
            raise ValueError("session constraint proposal cannot supersede itself")
        return self

    @property
    def controls_runtime(self) -> bool:
        """Proposals are never authority, including confirmed proposals."""

        return False


class SessionConstraintEntry(BaseModel):
    """One active or revoked, source-linked session constraint snapshot."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    constraint_id: str = Field(min_length=1)
    constraint_key: str = Field(min_length=1)
    category: SessionConstraintCategory
    value: SessionConstraintValue
    status: SessionConstraintStatus = SessionConstraintStatus.ACTIVE
    statement: str = Field(min_length=1, max_length=512)
    source_kind: SessionConstraintSourceKind
    source_id: str = Field(min_length=1)
    source_turn_index: int = Field(ge=1)
    source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authority: SessionConstraintAuthority
    supersedes_constraint_id: str | None = Field(default=None, min_length=1)
    confirmed_at_turn: int | None = Field(default=None, ge=1)
    revoked_at_turn: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _entry_is_source_bound_and_legal(self) -> "SessionConstraintEntry":
        _validate_session_constraint_category(self.category, self.value)
        if self.supersedes_constraint_id == self.constraint_id:
            raise ValueError("session constraint entry cannot supersede itself")
        if self.confirmed_at_turn is not None and self.confirmed_at_turn < self.source_turn_index:
            raise ValueError("confirmed_at_turn cannot precede source_turn_index")
        if self.status == SessionConstraintStatus.REVOKED:
            if self.revoked_at_turn is None:
                raise ValueError("revoked session constraint requires revoked_at_turn")
            if self.revoked_at_turn < self.source_turn_index:
                raise ValueError("revoked_at_turn cannot precede source_turn_index")
        elif self.revoked_at_turn is not None:
            raise ValueError("active session constraint cannot have revoked_at_turn")
        return self

    @property
    def is_active(self) -> bool:
        return self.status == SessionConstraintStatus.ACTIVE

    def required_context_text(self) -> str:
        """Bounded model-facing rendering; it is not an execution authority."""

        if not self.is_active:
            return ""
        payload = {
            "constraint_id": self.constraint_id,
            "constraint_key": self.constraint_key,
            "category": self.category,
            "value": self.value.model_dump(mode="json"),
            "source_id": self.source_id,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SessionConstraintState(BaseModel):
    """Runtime-owned snapshot of the latest per-key session constraints."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    session_id: str = ""
    project_root: str = ""
    revision: int = Field(default=0, ge=0)
    processed_through_turn: int = Field(default=0, ge=0)
    entries: list[SessionConstraintEntry] = Field(default_factory=list)
    limits: SessionConstraintLimits = Field(default_factory=SessionConstraintLimits)

    @model_validator(mode="after")
    def _snapshot_is_unique_and_cursor_bound(self) -> "SessionConstraintState":
        ids = [entry.constraint_id for entry in self.entries]
        keys = [entry.constraint_key for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("session constraint IDs must be unique")
        if len(keys) != len(set(keys)):
            raise ValueError("session constraint key must be unique in one snapshot")
        if self.entries and not self.session_id.strip():
            raise ValueError("session constraint state with entries requires session_id")
        if any(entry.source_turn_index > self.processed_through_turn for entry in self.entries):
            raise ValueError("session constraint source turn exceeds processed cursor")
        active_count = sum(entry.is_active for entry in self.entries)
        revoked_count = len(self.entries) - active_count
        if active_count > self.limits.max_active_entries:
            raise ValueError("active session constraint entry quota exceeded")
        if revoked_count > self.limits.max_revoked_tombstones:
            raise ValueError("revoked session constraint tombstone quota exceeded")
        active_entries = [entry for entry in self.entries if entry.is_active]
        revoked_entries = [entry for entry in self.entries if not entry.is_active]
        if _serialized_chars(active_entries) > self.limits.max_active_serialized_chars:
            raise ValueError("active session constraint serialized size quota exceeded")
        if _serialized_chars(revoked_entries) > self.limits.max_revoked_serialized_chars:
            raise ValueError("revoked session constraint serialized size quota exceeded")
        serialized = _serialized_chars(self.entries)
        if serialized > self.limits.max_entries_serialized_chars:
            raise ValueError("session constraint entries serialized size quota exceeded")
        for entry in self.entries:
            _validate_constraint_value_limits(entry.value, self.limits)
        return self

    @property
    def active_entries(self) -> list[SessionConstraintEntry]:
        return [entry for entry in self.entries if entry.is_active]

    @property
    def revoked_entries(self) -> list[SessionConstraintEntry]:
        return [entry for entry in self.entries if not entry.is_active]

    @property
    def canonical_hash(self) -> str:
        """Hash the complete persisted snapshot, including the ingress cursor."""

        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @property
    def authority_hash(self) -> str:
        """Hash constraint authority independently of the conversation cursor.

        ``processed_through_turn`` advances for ordinary user/assistant turns and
        is already covered by the session-turn ledger.  It must not change the
        identity of the active constraint projection or make a downstream
        context view stale when no constraint fact changed.  Keep the complete
        ``canonical_hash`` for persisted snapshot/replay evidence; this derived
        hash is the stable source identity for model-facing constraints.
        """

        payload = self.model_dump(mode="json")
        payload.pop("processed_through_turn", None)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ConversationIdentity(BaseModel):
    """Stable conversation identity plus the current execution/run cursor."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    conversation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    turn_index: int = Field(ge=0)
    project_root: str = Field(min_length=1)


class SessionTurn(BaseModel):
    """One raw turn accepted by the session ingress owner."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    identity: ConversationIdentity
    message_id: str = Field(min_length=1)
    role: Literal["user", "assistant"]
    content: str = Field(max_length=64_000)


class SessionIngressState(BaseModel):
    """Conversation-scoped ingress state, separate from long-term memory."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    identity: ConversationIdentity
    turns: list[SessionTurn] = Field(default_factory=list)
    pending_proposals: list[SessionConstraintProposal] = Field(default_factory=list)
    session_constraints: SessionConstraintState = Field(default_factory=SessionConstraintState)

    @model_validator(mode="before")
    @classmethod
    def _default_constraint_state(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        identity = migrated.get("identity") or {}
        identity_conversation_id = (
            identity.get("conversation_id")
            if isinstance(identity, dict)
            else getattr(identity, "conversation_id", "")
        )
        identity_project_root = (
            identity.get("project_root")
            if isinstance(identity, dict)
            else getattr(identity, "project_root", "")
        )
        if "session_constraints" not in migrated and identity_conversation_id:
            migrated["session_constraints"] = {
                "session_id": str(identity_conversation_id or ""),
                "project_root": str(identity_project_root or ""),
            }
        return migrated

    @model_validator(mode="after")
    def _ingress_state_is_identity_bound(self) -> "SessionIngressState":
        if self.session_constraints.session_id and self.session_constraints.session_id != self.identity.conversation_id:
            raise ValueError("session ingress and constraint state conversation identity differ")
        if self.session_constraints.project_root and self.session_constraints.project_root != self.identity.project_root:
            raise ValueError("session ingress and constraint state project identity differ")
        message_ids = [turn.message_id for turn in self.turns]
        if len(message_ids) != len(set(message_ids)):
            raise ValueError("session ingress message IDs must be unique")
        proposal_ids = [proposal.proposal_id for proposal in self.pending_proposals]
        if len(proposal_ids) != len(set(proposal_ids)):
            raise ValueError("session ingress proposal IDs must be unique")
        limits = self.session_constraints.limits
        if len(self.pending_proposals) > limits.max_pending_proposals:
            raise ValueError("pending session constraint proposals quota exceeded")
        if _serialized_chars(self.pending_proposals) > limits.max_pending_serialized_chars:
            raise ValueError("pending session constraint proposals serialized size quota exceeded")
        for proposal in self.pending_proposals:
            _validate_constraint_value_limits(proposal.value, limits)
        if any(proposal.session_id != self.identity.conversation_id for proposal in self.pending_proposals):
            raise ValueError("session ingress proposal conversation identity differs")
        if any(
            turn.identity.conversation_id != self.identity.conversation_id
            or turn.identity.project_root != self.identity.project_root
            for turn in self.turns
        ):
            raise ValueError("session ingress turn identity differs")
        return self


class ProjectImprovementRequirement(str, Enum):
    """Whether post-core project improvement controls top-level completion."""

    DISABLED = "disabled"
    OPTIONAL = "optional"
    REQUIRED = "required"


class ProjectImprovementPolicySource(str, Enum):
    """Authority that selected the project-improvement completion policy."""

    AUTOMATIC_DEFAULT = "automatic_default"
    RUNTIME_CONFIG = "runtime_config"
    USER_SELECTED = "user_selected"
    GOAL_ACCEPTANCE = "goal_acceptance"
    LEGACY_CONFIG = "legacy_config"


class ProjectImprovementStatus(str, Enum):
    """Observed lifecycle outcome for the post-core improvement stage."""

    NOT_REQUESTED = "not_requested"
    SKIPPED = "skipped"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ProjectImprovementPolicy(BaseModel):
    """Owned runtime policy for bounded post-core project improvement."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    requirement: ProjectImprovementRequirement = ProjectImprovementRequirement.OPTIONAL
    source: ProjectImprovementPolicySource = ProjectImprovementPolicySource.AUTOMATIC_DEFAULT
    target_successes: int = Field(default=2, ge=0)
    max_attempts: int = Field(default=4, ge=0)

    @model_validator(mode="after")
    def _counts_match_requirement(self) -> "ProjectImprovementPolicy":
        if self.requirement == ProjectImprovementRequirement.DISABLED:
            if self.target_successes != 0 or self.max_attempts != 0:
                raise ValueError("disabled project improvement requires zero targets and attempts")
            return self
        if self.target_successes <= 0:
            raise ValueError("enabled project improvement requires at least one target success")
        if self.max_attempts < self.target_successes:
            raise ValueError("max_attempts must be at least target_successes")
        return self

    @property
    def enabled(self) -> bool:
        return self.requirement != ProjectImprovementRequirement.DISABLED

    @property
    def controls_top_level_success(self) -> bool:
        return self.requirement == ProjectImprovementRequirement.REQUIRED


class VerificationStatus(str, Enum):
    """Verification state used by completion and recovery decisions."""

    NOT_STARTED = "not_started"
    REQUIRED = "required"
    PASSED = "passed"
    FAILED = "failed"
    NOT_REQUIRED = "not_required"


class CheckpointStatus(str, Enum):
    """Availability/durability state exposed by the runtime controller."""

    DISABLED = "disabled"
    PENDING = "pending"
    DURABLE = "durable"
    UNAVAILABLE = "unavailable"
    FALLBACK_AVAILABLE = "fallback_available"


class CheckpointBoundary(str, Enum):
    """Registered durable runtime boundaries; controller strings are not authoritative."""

    TASK_NORMALIZED = "task_normalized"
    ROUTE_SELECTED = "route_selected"
    DECOMPOSITION_RECORDED = "decomposition_recorded"
    CONTEXT_ASSEMBLED = "context_assembled"
    SUBTASK_RESULT_APPLIED = "subtask_result_applied"
    LLM_REQUEST_PREPARED = "llm_request_prepared"
    LLM_RESPONSE_OBSERVED = "llm_response_observed"
    TOOL_CALL_PREPARED = "tool_call_prepared"
    TOOL_RESULT_OBSERVED = "tool_result_observed"
    TOOL_RESULT_APPLIED = "tool_result_applied"
    VERIFICATION_REQUIRED = "verification_required"
    VERIFICATION_APPLIED = "verification_applied"
    CONTROLLED_STOP = "controlled_stop"
    RUNTIME_STATE_COMPLETED = "runtime_state_completed"
    RUNTIME_REPORT_PERSISTED = "runtime_report_persisted"
    RUNTIME_FINALIZED = "runtime_finalized"


class CheckpointFaultPoint(str, Enum):
    """Runtime-only deterministic injection points around a durable checkpoint write."""

    BEFORE_DURABLE_WRITE = "before_durable_write"
    AFTER_DURABLE_WRITE = "after_durable_write"


class FinalizationFaultPoint(str, Enum):
    """Runtime-only deterministic injection points in durable finalization."""

    AFTER_STATE_COMPLETED = "after_state_completed"
    AFTER_REPORT_PERSISTED = "after_report_persisted"
    AFTER_RUN_FINALIZED = "after_run_finalized"
    AFTER_FINAL_CHECKPOINT_DURABLE = "after_final_checkpoint_durable"


class RecoveryStatus(str, Enum):
    """Operational recovery state owned by one runtime state."""

    NOT_REQUIRED = "not_required"
    ASSESSMENT_PENDING = "assessment_pending"
    RESUME_READY = "resume_ready"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    REPLAN_REQUIRED = "replan_required"
    WAITING_RETRY = "waiting_retry"
    WAITING_USER = "waiting_user"
    RESUMING = "resuming"
    RECOVERED = "recovered"
    RECOVERY_FAILED = "recovery_failed"
    UNRECOVERABLE = "unrecoverable"
    ABANDONED = "abandoned"


class Recoverability(str, Enum):
    """Preflight conclusion about whether one checkpoint can safely continue."""

    RECOVERABLE_NOW = "recoverable_now"
    RECOVERABLE_AFTER_ACTION = "recoverable_after_action"
    NOT_RECOVERABLE = "not_recoverable"
    ALREADY_COMPLETE = "already_complete"


class RecoveryMode(str, Enum):
    """Typed continuation method selected by resume preflight."""

    RETURN_COMPLETED = "return_completed"
    FINALIZE_FROM_CHECKPOINT = "finalize_from_checkpoint"
    EXACT_RESUME = "exact_resume"
    RECONCILE_THEN_RESUME = "reconcile_then_resume"
    REPLAN_FROM_CHECKPOINT = "replan_from_checkpoint"
    RETRY_FROM_CHECKPOINT = "retry_from_checkpoint"
    USER_ASSISTED_RESUME = "user_assisted_resume"
    NONE = "none"


class RecoveryAutomationPolicy(str, Enum):
    """Maximum authority granted to the recovery executor."""

    AUTOMATIC_ALLOWED = "automatic_allowed"
    APPROVAL_REQUIRED = "approval_required"
    MANUAL_ONLY = "manual_only"
    FORBIDDEN = "forbidden"


class RecoveryReasonCode(str, Enum):
    """Stable control reasons; human explanation must not drive branching."""

    CHECKPOINT_VALID = "checkpoint_valid"
    CHECKPOINT_ALREADY_COMPLETE = "checkpoint_already_complete"
    FINALIZATION_INCOMPLETE = "finalization_incomplete"
    CHECKPOINT_MISSING = "checkpoint_missing"
    CHECKPOINT_CORRUPT = "checkpoint_corrupt"
    RUN_NOT_FOUND = "run_not_found"
    RUN_LEASE_ACTIVE = "run_lease_active"
    PREVIOUS_CHECKPOINT_AVAILABLE = "previous_checkpoint_available"
    ROOT_TASK_MISMATCH = "root_task_mismatch"
    PROJECT_ROOT_MISMATCH = "project_root_mismatch"
    PROJECT_DRIFT_CONFLICTING = "project_drift_conflicting"
    RECOVERY_BUDGET_EXHAUSTED = "recovery_budget_exhausted"
    PENDING_FILE_MUTATION = "pending_file_mutation"
    INDETERMINATE_SIDE_EFFECT = "indeterminate_side_effect"
    MISSING_TYPED_TOOL_INPUT = "missing_typed_tool_input"
    EXTERNAL_WRITE_WITHOUT_PROBE = "external_write_without_probe"
    UNSUPPORTED_RUNTIME_MODE = "unsupported_runtime_mode"
    MISSING_STAGE_CURSOR = "missing_stage_cursor"
    PERMISSION_REQUIRED = "permission_required"
    RECONCILIATION_FAILED = "reconciliation_failed"
    VERIFICATION_UNAVAILABLE = "verification_unavailable"
    VERIFICATION_FAILED = "verification_failed"
    LEGACY_BLOCKED = "legacy_blocked"
    LEGACY_LLM_IDENTITY_UNBOUND = "legacy_llm_identity_unbound"


class LLMRequestHashVersion(str, Enum):
    """Semantic identity generation used for provider replay safety."""

    LEGACY_UNBOUND_V1 = "legacy_unbound_v1"
    PROVIDER_BOUND_V2 = "provider_bound_v2"


class RecoveryFallbackAction(str, Enum):
    """Safe action offered when the selected recovery mode cannot run now."""

    RETRY_LATER = "retry_later"
    REQUEST_USER_INPUT = "request_user_input"
    REQUEST_BUDGET_EXTENSION = "request_budget_extension"
    REQUEST_MANUAL_RECONCILIATION = "request_manual_reconciliation"
    USE_PREVIOUS_VALID_CHECKPOINT = "use_previous_valid_checkpoint"
    EXPORT_RECOVERY_BUNDLE = "export_recovery_bundle"
    OFFER_NEW_LINKED_RUN = "offer_new_linked_run"
    TERMINATE_PRESERVING_EVIDENCE = "terminate_preserving_evidence"
    NONE = "none"


class RecoveryBlocker(BaseModel):
    """Strict blocker value owned by one resume assessment."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    reason_code: RecoveryReasonCode
    resolvable: bool
    requires_user_action: bool = False
    evidence_refs: list[str] = Field(default_factory=list)
    required_action: RecoveryFallbackAction = RecoveryFallbackAction.NONE
    target_ref: str | None = None


class RecoveryFallback(BaseModel):
    """Strict fallback value owned by one resume assessment."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    action: RecoveryFallbackAction
    reason_code: RecoveryReasonCode
    requires_user_authorization: bool = False
    preserve_original_run: bool = True
    new_run_allowed: bool = False
    recovery_bundle_ref: str | None = None
    instructions: str = ""


class ContextRequestPurpose(str, Enum):
    """Auditable production purpose for one assembled LLM request."""

    UNSPECIFIED = "unspecified"
    SLOT_GENERATION = "slot_generation"
    SLOT_LANGUAGE_REPAIR = "slot_language_repair"
    SEMANTIC_GOAL = "semantic_goal"
    SEMANTIC_PLAN_STEP = "semantic_plan_step"
    TOOL_EVENT_DECISION = "tool_event_decision"
    TOOL_PLAN_RETRY = "tool_plan_retry"
    TASK_COMPLEXITY = "task_complexity"
    TASK_DECOMPOSITION = "task_decomposition"
    ITERATION_GOAL = "iteration_goal"
    ITERATION_TASK_DESIGN = "iteration_task_design"
    PROJECT_IMPROVEMENT = "project_improvement"
    RUNTIME_OUTPUT_EVALUATION = "runtime_output_evaluation"
    CODE_GENERATION = "code_generation"
    TEXT_FILE_GENERATION = "text_file_generation"
    CODE_UNIT_GENERATION = "code_unit_generation"
    CODE_EDIT = "code_edit"
    BUG_FIX = "bug_fix"
    MEMORY_COMPRESSION = "memory_compression"
    TEXT_SUMMARIZATION = "text_summarization"
    WEB_QUERY_GENERATION = "web_query_generation"
    WEB_LINK_SELECTION = "web_link_selection"
    WEB_CLEANUP = "web_cleanup"


class EnhancementCompletionComplexity(str, Enum):
    ROUTINE = "routine"
    STANDARD = "standard"
    COMPLEX = "complex"


class EnhancementCompletionDecisionValue(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class EnhancementCompletionRequirement(str, Enum):
    OPTIONAL = "optional"
    REQUIRED = "required"


class EnhancementCompletionPurposeLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    floor: int = Field(ge=1)
    ceiling: int = Field(ge=1)

    @model_validator(mode="after")
    def _floor_fits_ceiling(self) -> "EnhancementCompletionPurposeLimit":
        if self.floor > self.ceiling:
            raise ValueError("completion floor must not exceed ceiling")
        return self


def _default_enhancement_purpose_limits() -> dict[ContextRequestPurpose, EnhancementCompletionPurposeLimit]:
    return {
        ContextRequestPurpose.PROJECT_IMPROVEMENT: EnhancementCompletionPurposeLimit(floor=500, ceiling=1500),
        ContextRequestPurpose.ITERATION_GOAL: EnhancementCompletionPurposeLimit(floor=400, ceiling=1200),
        ContextRequestPurpose.ITERATION_TASK_DESIGN: EnhancementCompletionPurposeLimit(floor=700, ceiling=2200),
        ContextRequestPurpose.CODE_GENERATION: EnhancementCompletionPurposeLimit(floor=1000, ceiling=3500),
        ContextRequestPurpose.CODE_EDIT: EnhancementCompletionPurposeLimit(floor=400, ceiling=1600),
    }


class EnhancementCompletionBudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_tokens: int = Field(default=12000, ge=1)
    recovery_step: int = Field(default=300, ge=0)
    purpose_limits: dict[ContextRequestPurpose, EnhancementCompletionPurposeLimit] = Field(
        default_factory=_default_enhancement_purpose_limits
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_four_purpose_policy(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        raw_limits = value.get("purpose_limits")
        if not isinstance(raw_limits, dict):
            return value
        normalized = {
            key.value if isinstance(key, ContextRequestPurpose) else str(key)
            for key in raw_limits
        }
        legacy = {
            ContextRequestPurpose.PROJECT_IMPROVEMENT.value,
            ContextRequestPurpose.ITERATION_GOAL.value,
            ContextRequestPurpose.ITERATION_TASK_DESIGN.value,
            ContextRequestPurpose.CODE_GENERATION.value,
        }
        if normalized != legacy:
            return value
        migrated_limits = dict(raw_limits)
        migrated_limits[ContextRequestPurpose.CODE_EDIT.value] = {
            "floor": 400,
            "ceiling": 1600,
        }
        return {**value, "purpose_limits": migrated_limits}

    @model_validator(mode="after")
    def _validate_purposes(self) -> "EnhancementCompletionBudgetPolicy":
        expected = {
            ContextRequestPurpose.PROJECT_IMPROVEMENT,
            ContextRequestPurpose.ITERATION_GOAL,
            ContextRequestPurpose.ITERATION_TASK_DESIGN,
            ContextRequestPurpose.CODE_GENERATION,
            ContextRequestPurpose.CODE_EDIT,
        }
        if set(self.purpose_limits) != expected:
            raise ValueError("enhancement completion policy requires the exact five purposes")
        if any(limit.ceiling > self.total_tokens for limit in self.purpose_limits.values()):
            raise ValueError("purpose ceiling must fit shared total")
        return self


class EnhancementCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_key: str = Field(min_length=1)
    purpose: ContextRequestPurpose
    complexity: EnhancementCompletionComplexity = EnhancementCompletionComplexity.STANDARD
    prompt_tokens: int = Field(default=0, ge=0)
    remaining_calls: int = Field(default=1, ge=1)
    remaining_value: EnhancementCompletionDecisionValue = EnhancementCompletionDecisionValue.NORMAL
    requirement: EnhancementCompletionRequirement = EnhancementCompletionRequirement.OPTIONAL
    recovery_of: str | None = None

    @model_validator(mode="after")
    def _purpose_is_enhancement(self) -> "EnhancementCompletionRequest":
        if self.purpose not in _default_enhancement_purpose_limits():
            raise ValueError("request purpose is not governed by the enhancement completion budget")
        return self


class EnhancementCompletionReservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reservation_id: str = Field(min_length=1)
    logical_key: str = Field(min_length=1)
    purpose: ContextRequestPurpose
    max_tokens: int = Field(ge=1)
    recovery_of: str | None = None


class EnhancementCompletionReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reservation_id: str
    reserved_tokens: int = Field(ge=0)
    actual_tokens: int | None = Field(default=None, ge=0)
    refunded_tokens: int = Field(ge=0)
    usage_known: bool
    finish_reason: str | None = None


class RuntimeBudgetMetadata(MetadataBase):
    """Mutable runtime budgets that bound exploration and recovery loops."""

    kind: Literal[MetadataKind.RUNTIME_BUDGET] = MetadataKind.RUNTIME_BUDGET
    max_tool_calls: int = 20
    max_file_reads: int = 30
    max_file_edits: int = 3
    max_file_creates: int = 20
    max_verification_attempts: int = 3
    max_recovery_rounds: int = 3
    max_replan_rounds: int = 3
    max_tool_event_completion_tokens: int = Field(default=12000, ge=1)
    tool_event_completion_ceiling: int = Field(default=2000, ge=1)
    tool_event_completion_floor: int = Field(default=800, ge=1)
    tool_event_completion_recovery_step: int = Field(default=400, ge=0)
    tool_calls_used: int = 0
    file_reads_used: int = 0
    file_edits_used: int = 0
    file_creates_used: int = 0
    verification_attempts_used: int = 0
    recovery_rounds_used: int = 0
    replan_rounds_used: int = 0
    tool_event_completion_tokens_used: int = Field(default=0, ge=0)
    tool_event_completion_recovery_bonus: int = Field(default=0, ge=0)
    tool_event_completion_outcome_feedback_enabled: bool = False
    tool_event_completion_last_outcome: ToolEventCompletionOutcome | None = None
    enhancement_completion_policy: EnhancementCompletionBudgetPolicy = Field(
        default_factory=EnhancementCompletionBudgetPolicy
    )
    enhancement_completion_tokens_reserved: int = Field(default=0, ge=0)
    enhancement_completion_tokens_used: int = Field(default=0, ge=0)
    enhancement_completion_length_recovery_limits: dict[str, int] = Field(default_factory=dict)
    enhancement_completion_length_recovery_used: list[str] = Field(default_factory=list)
    enhancement_completion_reservations: dict[str, EnhancementCompletionReservation] = Field(
        default_factory=dict
    )
    enhancement_completion_reconciliations: dict[
        str, EnhancementCompletionReconciliation
    ] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _tool_event_completion_bounds_are_consistent(self) -> "RuntimeBudgetMetadata":
        if self.tool_event_completion_floor > self.tool_event_completion_ceiling:
            raise ValueError("tool event completion floor must not exceed ceiling")
        if self.tool_event_completion_ceiling > self.max_tool_event_completion_tokens:
            raise ValueError("tool event completion ceiling must fit total budget")
        reservations = self.enhancement_completion_reservations
        reconciliations = self.enhancement_completion_reconciliations
        if any(key != item.logical_key for key, item in reservations.items()):
            raise ValueError("enhancement reservation ledger key must match logical_key")
        reservation_ids = [item.reservation_id for item in reservations.values()]
        if len(reservation_ids) != len(set(reservation_ids)):
            raise ValueError("enhancement reservation IDs must be unique")
        if any(key != item.reservation_id for key, item in reconciliations.items()):
            raise ValueError("enhancement reconciliation ledger key must match reservation_id")
        reservations_by_id = {
            item.reservation_id: item for item in reservations.values()
        }
        for reconciliation in reconciliations.values():
            reservation = reservations_by_id.get(reconciliation.reservation_id)
            if reservation is None:
                raise ValueError("enhancement reconciliation must reference a reservation")
            if reconciliation.reserved_tokens != reservation.max_tokens:
                raise ValueError("enhancement reconciliation reserved amount changed")
            if reconciliation.usage_known != (reconciliation.actual_tokens is not None):
                raise ValueError("enhancement reconciliation usage-known state is inconsistent")
            if (
                reconciliation.actual_tokens is not None
                and reconciliation.actual_tokens > reconciliation.reserved_tokens
            ):
                raise ValueError("enhancement actual usage exceeds its reservation")
        expected_reserved = sum(
            reservation.max_tokens
            for reservation in reservations.values()
            if not (
                (reconciliation := reconciliations.get(reservation.reservation_id))
                and reconciliation.usage_known
            )
        )
        expected_used = sum(
            int(item.actual_tokens or 0)
            for item in reconciliations.values()
            if item.usage_known
        )
        if self.enhancement_completion_tokens_reserved != expected_reserved:
            raise ValueError("enhancement reserved counter does not match its ledger")
        if self.enhancement_completion_tokens_used != expected_used:
            raise ValueError("enhancement used counter does not match its ledger")
        recovery_limits = self.enhancement_completion_length_recovery_limits
        if not set(recovery_limits).issubset(set(reservation_ids)):
            raise ValueError("enhancement recovery limit references an unknown reservation")
        if len(self.enhancement_completion_length_recovery_used) != len(
            set(self.enhancement_completion_length_recovery_used)
        ):
            raise ValueError("enhancement recovery use IDs must be unique")
        if not set(self.enhancement_completion_length_recovery_used).issubset(
            set(recovery_limits)
        ):
            raise ValueError("enhancement recovery use requires a recorded length limit")
        return self

    @property
    def tool_calls_remaining(self) -> int:
        return max(0, self.max_tool_calls - self.tool_calls_used)

    @property
    def file_reads_remaining(self) -> int:
        return max(0, self.max_file_reads - self.file_reads_used)

    @property
    def file_edits_remaining(self) -> int:
        return max(0, self.max_file_edits - self.file_edits_used)

    @property
    def file_creates_remaining(self) -> int:
        return max(0, self.max_file_creates - self.file_creates_used)

    @property
    def verification_attempts_remaining(self) -> int:
        return max(0, self.max_verification_attempts - self.verification_attempts_used)

    @property
    def recovery_rounds_remaining(self) -> int:
        return max(0, self.max_recovery_rounds - self.recovery_rounds_used)

    @property
    def replan_rounds_remaining(self) -> int:
        return max(0, self.max_replan_rounds - self.replan_rounds_used)

    @property
    def tool_event_completion_tokens_remaining(self) -> int:
        return max(0, self.max_tool_event_completion_tokens - self.tool_event_completion_tokens_used)

    @property
    def enhancement_completion_tokens_remaining(self) -> int:
        return max(
            0,
            self.enhancement_completion_policy.total_tokens
            - self.enhancement_completion_tokens_reserved
            - self.enhancement_completion_tokens_used,
        )

    def tool_event_completion_limit(self, *, round_index: int, calls_remaining: int) -> int:
        remaining = self.tool_event_completion_tokens_remaining
        if remaining <= 0:
            return 0
        round_index = max(1, int(round_index))
        calls_remaining = max(1, int(calls_remaining))
        decayed_ceiling = max(
            self.tool_event_completion_floor,
            self.tool_event_completion_ceiling
            - (round_index - 1) * self.tool_event_completion_recovery_step,
        )
        fair_share = remaining // calls_remaining
        if remaining >= self.tool_event_completion_floor:
            fair_share = max(self.tool_event_completion_floor, fair_share)
        recovery_bonus = self.tool_event_completion_recovery_bonus
        return min(remaining, decayed_ceiling + recovery_bonus, fair_share + recovery_bonus)

    def consume_tool_event_completion(self, tokens: int) -> None:
        self.tool_event_completion_tokens_used += max(0, int(tokens))
        self.tool_event_completion_recovery_bonus = 0

    def grant_tool_event_completion_recovery(self, tokens: int) -> None:
        self.tool_event_completion_recovery_bonus = max(
            self.tool_event_completion_recovery_bonus,
            max(0, int(tokens)),
        )

    def observe_tool_event_outcome(self, outcome: ToolEventCompletionOutcome) -> None:
        """Record one typed outcome and optionally preserve a recovery reserve.

        The feedback lane is explicitly opt-in so historical budget behavior and
        experiment baselines remain unchanged until a task selects it. Empty or
        truncated responses receive one bounded recovery step; ordinary progress
        and no-progress outcomes never create unbounded budget.
        """

        self.tool_event_completion_last_outcome = outcome
        if not self.tool_event_completion_outcome_feedback_enabled:
            return
        if outcome in {
            ToolEventCompletionOutcome.EMPTY_RESPONSE,
            ToolEventCompletionOutcome.TRUNCATED,
        }:
            self.grant_tool_event_completion_recovery(
                self.tool_event_completion_recovery_step
            )

    def reconcile_tool_event_completion(self, *, reserved: int, actual: int) -> None:
        self.tool_event_completion_tokens_used = max(
            0,
            self.tool_event_completion_tokens_used - max(0, int(reserved)) + max(0, int(actual)),
        )

    def has_tool_budget(self, *, reads: int = 0, edits: int = 0, creates: int = 0) -> bool:
        return (
            self.tool_calls_used < self.max_tool_calls
            and self.file_reads_used + reads <= self.max_file_reads
            and self.file_edits_used + edits <= self.max_file_edits
            and self.file_creates_used + creates <= self.max_file_creates
        )

    def consume_tool_call(
        self,
        *,
        file_read: bool = False,
        file_edit: bool = False,
        file_create: bool = False,
    ) -> None:
        self.tool_calls_used += 1
        if file_read:
            self.file_reads_used += 1
        if file_edit:
            self.file_edits_used += 1
        if file_create:
            self.file_creates_used += 1

    def consume_verification_attempt(self) -> None:
        self.verification_attempts_used += 1

    def consume_recovery_round(self) -> None:
        self.recovery_rounds_used += 1

    def consume_replan_round(self) -> None:
        self.replan_rounds_used += 1

    def exhausted_reasons(self) -> list[str]:
        reasons: list[str] = []
        if self.tool_calls_used >= self.max_tool_calls:
            reasons.append("tool call budget exhausted")
        if self.file_reads_used >= self.max_file_reads:
            reasons.append("file read budget exhausted")
        if self.file_edits_used >= self.max_file_edits:
            reasons.append("file edit budget exhausted")
        if self.file_creates_used >= self.max_file_creates:
            reasons.append("file create budget exhausted")
        if self.verification_attempts_used >= self.max_verification_attempts:
            reasons.append("verification budget exhausted")
        if self.recovery_rounds_used >= self.max_recovery_rounds:
            reasons.append("recovery budget exhausted")
        if self.replan_rounds_used >= self.max_replan_rounds:
            reasons.append("replan budget exhausted")
        return reasons


class ContextCandidateKind(str, Enum):
    """Typed source semantics for one model-facing context projection."""

    INSTRUCTION = "instruction"
    USER_INPUT = "user_input"
    DIALOG = "dialog"
    PROJECT_FILE = "project_file"
    MEMORY = "memory"
    ENVIRONMENT = "environment"
    TASK = "task"
    CONSTRAINT = "constraint"
    TOOL_SCHEMA = "tool_schema"
    RUNTIME_EVIDENCE = "runtime_evidence"
    ARTIFACT = "artifact"
    PREVIOUS_OUTPUT = "previous_output"
    RESEARCH_RESULT = "research_result"


class ContextCandidateRetention(str, Enum):
    """Whether a candidate may be omitted under input-budget pressure."""

    REQUIRED = "required"
    PREFERRED = "preferred"
    OPTIONAL = "optional"


class ContextCandidateTruncation(str, Enum):
    """Allowed content projection when a complete candidate does not fit."""

    FORBIDDEN = "forbidden"
    HEAD = "head"
    TAIL = "tail"


class ContextCandidateTrust(str, Enum):
    """Typed authority of one candidate projection, not its source fact."""

    AUTHORITATIVE = "authoritative"
    DIRECT = "direct"
    OBSERVED = "observed"
    RETRIEVED = "retrieved"
    DERIVED = "derived"
    UNVERIFIED = "unverified"


class ContextCandidateFreshness(str, Enum):
    """Producer-owned freshness classification for one projection."""

    CURRENT = "current"
    HISTORICAL = "historical"
    STALE = "stale"
    UNKNOWN = "unknown"


class ContextAssemblyStatus(str, Enum):
    """Whether the selected projection is legal to submit to a provider."""

    READY = "ready"
    BUDGET_INSUFFICIENT = "budget_insufficient"
    GOVERNANCE_BLOCKED = "governance_blocked"


class ContextCompactionSummary(BaseModel):
    """Strict, non-authoritative slots emitted by an LLM compactor."""

    model_config = ConfigDict(extra="forbid")

    goal_delta: str = Field(default="", max_length=240)
    verified_facts: list[str] = Field(default_factory=list, max_length=8)
    decisions: list[str] = Field(default_factory=list, max_length=8)
    open_issues: list[str] = Field(default_factory=list, max_length=8)
    evidence_ids: list[str] = Field(min_length=1, max_length=16)
    next_action: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def _summary_has_bounded_signal(self) -> "ContextCompactionSummary":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("compaction summary evidence IDs must be unique")
        if not any(
            (
                self.goal_delta.strip(),
                self.verified_facts,
                self.decisions,
                self.open_issues,
                self.next_action.strip(),
            )
        ):
            raise ValueError("compaction summary must not be empty")
        return self


class ContextCompactionProviderStatus(str, Enum):
    """Whether a provider-derived compact projection passed its adapter boundary."""

    NOT_ATTEMPTED = "not_attempted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ContextCompactionSelectionStatus(str, Enum):
    """Whether the builder selected one compact projection for the Prompt."""

    SELECTED = "selected"
    NOT_SELECTED = "not_selected"
    NOT_EVALUATED = "not_evaluated"


class ContextCompactionSelectionOutcome(str, Enum):
    """Body-free explanation of the final compact trial outcome."""

    NOT_APPLICABLE = "not_applicable"
    GENERATED_SELECTED = "generated_selected"
    GENERATED_FIT_REJECTED = "generated_fit_rejected"
    GENERATED_RECENT_SUFFIX_DISPLACED = "generated_recent_suffix_displaced"
    GENERATED_OBSERVED_ONLY = "generated_observed_only"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"
    DETERMINISTIC_BOUND = "deterministic_bound"
    SINK_FAILED = "sink_failed"


class ContextCompactionFallbackReason(str, Enum):
    """Typed reason a provider-derived compact view did not become authority."""

    BUDGET_ZERO = "budget_zero"
    REQUEST_OR_PROVIDER_FAILURE = "request_or_provider_failure"
    PROVIDER_OUTPUT_TRUNCATED = "provider_output_truncated"
    INVALID_ADAPTER_RESULT = "invalid_adapter_result"
    SOURCE_FINGERPRINT_MISMATCH = "source_fingerprint_mismatch"
    SUMMARY_NOT_FIT_ATOMICALLY = "summary_not_fit_atomically"
    RECENT_SUFFIX_DISPLACED = "recent_suffix_displaced"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"
    ARTIFACT_SINK_FAILURE = "artifact_sink_failure"


class ContextCompactionAttempt(BaseModel):
    """Body-free evidence for one compact attempt and its builder outcome."""

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
        validate_assignment=True,
    )

    attempt_ordinal: StrictInt = Field(ge=1)
    source_candidate_ids: list[str] = Field(min_length=1)
    source_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_chars: StrictInt = Field(default=0, ge=0)
    algorithm: Literal[
        "deterministic_dialog_extract_v1",
        "deterministic_observation_mask_v1",
        "llm_rolling_summary_v1",
    ]
    provider_status: ContextCompactionProviderStatus = (
        ContextCompactionProviderStatus.NOT_ATTEMPTED
    )
    selection_status: ContextCompactionSelectionStatus = (
        ContextCompactionSelectionStatus.NOT_EVALUATED
    )
    fallback_reason: ContextCompactionFallbackReason | None = None
    summary_token_limit: StrictInt | None = Field(default=None, ge=1)
    summary_token_count: StrictInt | None = Field(default=None, ge=0)
    usage_complete: StrictBool | None = None
    finish_reason: str | None = Field(default=None, min_length=1, max_length=64)
    provider_prompt_tokens: StrictInt | None = Field(default=None, ge=0)
    provider_completion_tokens: StrictInt | None = Field(default=None, ge=0)
    provider_total_tokens: StrictInt | None = Field(default=None, ge=0)
    adapter_fallback_reason: str | None = Field(default=None, min_length=1, max_length=64)
    generated_candidate_id: str | None = Field(default=None, min_length=1)
    generated_summary_fingerprint: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    generated_summary_chars: StrictInt | None = Field(default=None, ge=1)
    trial_assembly_status: ContextAssemblyStatus | None = None
    trial_candidate_decision: ContextCandidateDecision | None = None
    displaced_candidate_ids: list[str] = Field(default_factory=list)
    selection_outcome: ContextCompactionSelectionOutcome = (
        ContextCompactionSelectionOutcome.NOT_APPLICABLE
    )
    artifact_sink_status: Literal[
        "not_attempted", "persisted", "failed", "observed_only"
    ] = "not_attempted"
    artifact_binding: bool = False
    used_in_prompt: bool = False

    @model_validator(mode="after")
    def _outcome_is_consistent(self) -> "ContextCompactionAttempt":
        if len(self.source_candidate_ids) != len(set(self.source_candidate_ids)):
            raise ValueError("compaction attempt source candidate IDs must be unique")
        if len(self.displaced_candidate_ids) != len(set(self.displaced_candidate_ids)):
            raise ValueError("displaced candidate IDs must be unique")
        if self.algorithm != "llm_rolling_summary_v1" and self.provider_status == (
            ContextCompactionProviderStatus.ACCEPTED
        ):
            raise ValueError("deterministic compaction cannot be provider accepted")
        if self.algorithm != "llm_rolling_summary_v1" and any(
            value is not None
            for value in (self.summary_token_limit, self.summary_token_count)
        ):
            raise ValueError("summary token evidence requires an LLM compaction attempt")
        if self.summary_token_count is not None and self.summary_token_limit is not None:
            if self.summary_token_count > self.summary_token_limit:
                raise ValueError("summary token count exceeds its limit")
        provider_tokens = (
            self.provider_prompt_tokens,
            self.provider_completion_tokens,
            self.provider_total_tokens,
        )
        has_provider_tokens = any(value is not None for value in provider_tokens)
        has_complete_provider_tokens = all(value is not None for value in provider_tokens)
        if self.provider_status == ContextCompactionProviderStatus.NOT_ATTEMPTED:
            if self.finish_reason is not None or self.usage_complete is not None or has_provider_tokens:
                raise ValueError("unattempted compaction cannot carry provider evidence")
        else:
            if self.usage_complete is True and not has_complete_provider_tokens:
                raise ValueError("usage_complete=true requires all provider token fields")
            if self.provider_status == ContextCompactionProviderStatus.ACCEPTED:
                if self.finish_reason is None:
                    raise ValueError("accepted provider compaction attempts require a finish reason")
                if not has_complete_provider_tokens or self.usage_complete is not True:
                    raise ValueError("accepted provider compaction attempts require complete usage")
        if self.selection_status == ContextCompactionSelectionStatus.NOT_SELECTED:
            if (
                self.fallback_reason is None
                and self.selection_outcome
                != ContextCompactionSelectionOutcome.GENERATED_OBSERVED_ONLY
            ):
                raise ValueError("not_selected compaction attempt requires a fallback")
            if self.artifact_sink_status == "persisted":
                raise ValueError("not_selected compaction cannot persist an artifact")
            if self.used_in_prompt or self.artifact_binding:
                raise ValueError("not_selected compaction cannot enter the prompt or bind an artifact")
        if self.selection_status == ContextCompactionSelectionStatus.NOT_EVALUATED:
            if self.artifact_sink_status != "not_attempted" or self.artifact_binding or self.used_in_prompt:
                raise ValueError("not_evaluated compaction cannot carry sink or prompt evidence")
        if self.selection_status == ContextCompactionSelectionStatus.SELECTED:
            if self.fallback_reason == ContextCompactionFallbackReason.ARTIFACT_SINK_FAILURE:
                raise ValueError("sink failure cannot select a compaction")
            if self.artifact_sink_status == "failed":
                raise ValueError("failed artifact sink cannot select a compaction")
            if self.artifact_sink_status != "persisted" or not self.artifact_binding or not self.used_in_prompt:
                raise ValueError("selected compaction requires persisted prompt-bound artifact evidence")
        if self.artifact_sink_status == "failed" and (
            self.fallback_reason != ContextCompactionFallbackReason.ARTIFACT_SINK_FAILURE
            or self.selection_status != ContextCompactionSelectionStatus.NOT_SELECTED
        ):
            raise ValueError("failed artifact sink requires an unselected sink-failure outcome")
        if self.artifact_sink_status == "observed_only":
            if self.selection_outcome != ContextCompactionSelectionOutcome.GENERATED_OBSERVED_ONLY:
                raise ValueError("observed-only sink status requires observed-only outcome")
            if self.artifact_binding or self.used_in_prompt:
                raise ValueError("observed-only compaction cannot bind or enter the prompt")
        if self.used_in_prompt and not self.artifact_binding:
            raise ValueError("prompt-used compaction requires an artifact binding")
        if self.artifact_binding and self.artifact_sink_status != "persisted":
            raise ValueError("artifact binding requires a persisted sink")
        if self.selection_outcome == ContextCompactionSelectionOutcome.GENERATED_SELECTED:
            if self.algorithm != "llm_rolling_summary_v1" or self.selection_status != (
                ContextCompactionSelectionStatus.SELECTED
            ):
                raise ValueError("generated-selected outcome requires a selected LLM attempt")
            if self.provider_status != ContextCompactionProviderStatus.ACCEPTED:
                raise ValueError("generated-selected outcome requires provider acceptance")
            if not self.used_in_prompt or not self.artifact_binding:
                raise ValueError("generated-selected outcome requires prompt/artifact evidence")
            if not self.generated_candidate_id or not self.generated_summary_fingerprint:
                raise ValueError("generated-selected outcome requires generated summary evidence")
        if self.selection_outcome == ContextCompactionSelectionOutcome.GENERATED_OBSERVED_ONLY:
            if self.algorithm != "llm_rolling_summary_v1" or self.artifact_sink_status != "observed_only":
                raise ValueError("observed-only outcome requires a generated summary observation")
            if self.provider_status != ContextCompactionProviderStatus.ACCEPTED:
                raise ValueError("observed-only outcome requires provider acceptance")
            if not self.generated_candidate_id or not self.generated_summary_fingerprint:
                raise ValueError("observed-only outcome requires generated summary evidence")
        if self.selection_outcome in {
            ContextCompactionSelectionOutcome.GENERATED_FIT_REJECTED,
            ContextCompactionSelectionOutcome.GENERATED_RECENT_SUFFIX_DISPLACED,
        }:
            if self.algorithm != "llm_rolling_summary_v1":
                raise ValueError("generated rejection outcomes require an LLM compaction attempt")
            if self.provider_status != ContextCompactionProviderStatus.ACCEPTED:
                raise ValueError("generated rejection outcomes require provider acceptance")
        if self.selection_outcome == ContextCompactionSelectionOutcome.GENERATED_RECENT_SUFFIX_DISPLACED:
            if not self.displaced_candidate_ids:
                raise ValueError("recent-suffix displacement requires displaced candidate IDs")
        elif self.displaced_candidate_ids:
            raise ValueError("displaced candidate IDs require a recent-suffix outcome")
        if self.selection_outcome in {
            ContextCompactionSelectionOutcome.DETERMINISTIC_BOUND,
            ContextCompactionSelectionOutcome.DETERMINISTIC_FALLBACK,
        } and self.algorithm == "llm_rolling_summary_v1":
            raise ValueError("deterministic outcomes require a deterministic compaction attempt")
        if self.selection_outcome == ContextCompactionSelectionOutcome.SINK_FAILED:
            if self.selection_status != ContextCompactionSelectionStatus.NOT_SELECTED:
                raise ValueError("sink-failed outcome must be unselected")
            if self.artifact_sink_status != "failed":
                raise ValueError("sink-failed outcome requires a failed sink")
        if any(
            value is not None
            for value in (
                self.generated_candidate_id,
                self.generated_summary_fingerprint,
                self.generated_summary_chars,
            )
        ) and self.algorithm != "llm_rolling_summary_v1":
            raise ValueError("generated summary evidence requires an LLM compaction attempt")
        return self


class ContextCompactionRecord(BaseModel):
    """Source-linked deterministic compact projection persisted by a run."""

    model_config = ConfigDict(extra="forbid")

    compaction_id: str = Field(min_length=1)
    source_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_candidate_ids: list[str] = Field(min_length=1)
    algorithm: Literal[
        "deterministic_dialog_extract_v1",
        "deterministic_observation_mask_v1",
        "llm_rolling_summary_v1",
    ]
    summary: str = Field(min_length=1)
    original_chars: int = Field(ge=1)
    compacted_chars: int = Field(ge=1)
    summary_payload: ContextCompactionSummary | None = None
    summary_token_limit: int | None = Field(default=None, ge=1)
    summary_token_count: int | None = Field(default=None, ge=0)
    previous_summary_fingerprint: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def _source_ids_and_sizes_are_consistent(self) -> "ContextCompactionRecord":
        if len(self.source_candidate_ids) != len(set(self.source_candidate_ids)):
            raise ValueError("compaction source candidate IDs must be unique")
        if self.compacted_chars != len(self.summary):
            raise ValueError("compacted_chars must match summary length")
        if self.compacted_chars >= self.original_chars:
            raise ValueError("compaction summary must be smaller than its sources")
        if self.algorithm == "llm_rolling_summary_v1":
            if self.summary_payload is None:
                raise ValueError("llm compaction requires summary_payload")
            if self.summary_token_limit is None or self.summary_token_count is None:
                raise ValueError("llm compaction requires summary token evidence")
            if self.summary_token_count > self.summary_token_limit:
                raise ValueError("summary_token_count exceeds summary_token_limit")
            unknown_evidence = set(self.summary_payload.evidence_ids) - set(
                self.source_candidate_ids
            )
            if unknown_evidence:
                raise ValueError("summary evidence must reference source candidates")
        elif any(
            value is not None
            for value in (
                self.summary_payload,
                self.summary_token_limit,
                self.summary_token_count,
                self.previous_summary_fingerprint,
            )
        ):
            raise ValueError("deterministic compaction cannot carry llm summary evidence")
        return self


class ContextCompactionReuseAdmissionStatus(str, Enum):
    """Shadow admission state for a reusable compact projection."""

    ADMITTED = "admitted"
    REJECTED = "rejected"


class ContextCompactionReuseShadowFailureReason(str, Enum):
    """Typed reason a default-off reusable-compaction shadow could not report."""

    PROVIDER_EXCEPTION = "provider_exception"
    PROVIDER_EMPTY = "provider_empty"
    INVALID_PROVIDER_RESULT = "invalid_provider_result"


class ContextCompactionReuseShadowFailure(BaseModel):
    """Body-free diagnostic for a shadow-provider fallback."""

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
        validate_assignment=True,
    )

    failure_id: str = Field(min_length=1)
    reason: ContextCompactionReuseShadowFailureReason
    exception_type: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$",
    )
    strict_sources: StrictBool = False
    fallback_applied: StrictBool = True

    @model_validator(mode="after")
    def _failure_evidence_is_bounded(self) -> "ContextCompactionReuseShadowFailure":
        if self.reason == ContextCompactionReuseShadowFailureReason.PROVIDER_EMPTY:
            if self.exception_type is not None:
                raise ValueError("empty shadow provider result cannot carry exception type")
        elif self.reason in {
            ContextCompactionReuseShadowFailureReason.PROVIDER_EXCEPTION,
            ContextCompactionReuseShadowFailureReason.INVALID_PROVIDER_RESULT,
        } and self.exception_type is None:
            raise ValueError("provider failure evidence requires exception type")
        if self.strict_sources:
            raise ValueError("strict shadow failures must be raised, not returned as fallback evidence")
        if not self.fallback_applied:
            raise ValueError("shadow failure evidence must record fallback_applied")
        return self


class ContextCompactionReuseRejectionReason(str, Enum):
    """Typed fail-closed reasons for reusable compact projections."""

    SOURCE_CANDIDATE_IDS_MISMATCH = "source_candidate_ids_mismatch"
    SOURCE_FINGERPRINT_MISMATCH = "source_fingerprint_mismatch"
    SOURCE_BINDING_HASH_MISMATCH = "source_binding_hash_mismatch"
    REQUIRED_CANDIDATE_IDS_MISMATCH = "required_candidate_ids_mismatch"
    RECENT_SUFFIX_IDS_MISMATCH = "recent_suffix_ids_mismatch"
    SESSION_CONSTRAINTS_HASH_MISMATCH = "session_constraints_hash_mismatch"
    ARTIFACT_KIND_MISMATCH = "artifact_kind_mismatch"
    ARTIFACT_INTEGRITY_MISMATCH = "artifact_integrity_mismatch"
    ARTIFACT_CONTRACT_INVALID = "artifact_contract_invalid"
    TRIAL_PROJECTION_NOT_READY = "trial_projection_not_ready"
    TRIAL_SUMMARY_NOT_SELECTED = "trial_summary_not_selected"
    TRIAL_REQUIRED_CANDIDATE_OMITTED = "trial_required_candidate_omitted"
    TRIAL_RECENT_SUFFIX_OMITTED = "trial_recent_suffix_omitted"


class ContextCompactionReuseAdmission(BaseModel):
    """Body-free shadow evidence for considering a reusable summary artifact.

    The current contract is deliberately shadow-only.  It may prove that an
    artifact would have passed source/admission checks, but it cannot authorize
    prompt use.  A future prompt-using contract must add a separate reviewed
    transition rather than flipping ``used_in_prompt`` here.
    """

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
        validate_assignment=True,
    )

    admission_id: str = Field(min_length=1)
    status: ContextCompactionReuseAdmissionStatus
    rejection_reason: ContextCompactionReuseRejectionReason | None = None
    source_candidate_ids: list[str] = Field(min_length=1)
    source_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_binding_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    required_candidate_ids: list[str] = Field(default_factory=list)
    recent_suffix_ids: list[str] = Field(default_factory=list)
    session_constraints_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    artifact_id: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    artifact_integrity_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    generated_summary_fingerprint: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    used_in_prompt: StrictBool = False

    @model_validator(mode="after")
    def _shadow_admission_is_consistent(self) -> "ContextCompactionReuseAdmission":
        for label, values in (
            ("source candidate IDs", self.source_candidate_ids),
            ("required candidate IDs", self.required_candidate_ids),
            ("recent suffix IDs", self.recent_suffix_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"reuse admission {label} must be unique")
        if self.status == ContextCompactionReuseAdmissionStatus.ADMITTED:
            if self.rejection_reason is not None:
                raise ValueError("admitted reuse admission cannot carry a rejection reason")
            if self.artifact_kind != "context_compaction":
                raise ValueError("admitted reuse admission requires context_compaction artifact kind")
            if self.generated_summary_fingerprint is None:
                raise ValueError("admitted reuse admission requires summary fingerprint")
        elif self.rejection_reason is None:
            raise ValueError("rejected reuse admission requires a rejection reason")
        if self.used_in_prompt:
            raise ValueError("reuse admission is shadow-only and cannot enter the prompt")
        return self


class ContextQualityIssueCode(str, Enum):
    """Deterministic offline quality issue vocabulary for assembled context."""

    ASSEMBLY_NOT_READY = "assembly_not_ready"
    CHARACTER_BUDGET_EXCEEDED = "character_budget_exceeded"
    EXPECTED_CANDIDATE_MISSING = "expected_candidate_missing"
    FORBIDDEN_CANDIDATE_SELECTED = "forbidden_candidate_selected"
    DECISION_COVERAGE_INCOMPLETE = "decision_coverage_incomplete"
    REQUIRED_CANDIDATE_UNREPRESENTED = "required_candidate_unrepresented"
    DUPLICATE_SELECTED_CONTENT = "duplicate_selected_content"
    GOVERNANCE_LINK_BROKEN = "governance_link_broken"
    DIALOG_NOT_RECENT_SUFFIX = "dialog_not_recent_suffix"
    COMPACTION_LINK_BROKEN = "compaction_link_broken"


class ContextQualityExpectation(BaseModel):
    """Fixture-authored expected inclusions and exclusions for one assembly."""

    model_config = ConfigDict(extra="forbid")

    expected_selected_candidate_ids: list[str] = Field(default_factory=list)
    expected_omitted_candidate_ids: list[str] = Field(default_factory=list)
    require_ready: bool = True
    require_recent_dialog_suffix: bool = True

    @model_validator(mode="after")
    def _candidate_expectations_are_consistent(self) -> "ContextQualityExpectation":
        selected = self.expected_selected_candidate_ids
        omitted = self.expected_omitted_candidate_ids
        if len(selected) != len(set(selected)) or len(omitted) != len(set(omitted)):
            raise ValueError("context quality candidate expectations must be unique")
        if set(selected) & set(omitted):
            raise ValueError("context quality candidate cannot be selected and omitted")
        return self


class ContextQualityEvaluation(BaseModel):
    """Ephemeral deterministic result of one offline context quality check."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    passed: bool
    issue_codes: list[ContextQualityIssueCode] = Field(default_factory=list)
    issue_candidate_ids: dict[str, list[str]] = Field(default_factory=dict)
    selected_candidate_ids: list[str] = Field(default_factory=list)
    omitted_candidate_ids: list[str] = Field(default_factory=list)
    character_budget_utilization: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _pass_state_matches_issues(self) -> "ContextQualityEvaluation":
        if self.passed != (not self.issue_codes):
            raise ValueError("context quality pass state must match issue codes")
        if len(self.issue_codes) != len(set(self.issue_codes)):
            raise ValueError("context quality issue codes must be unique")
        issue_values = {
            str(getattr(code, "value", code)) for code in self.issue_codes
        }
        if set(self.issue_candidate_ids) != issue_values:
            raise ValueError("context quality issue evidence must match issue codes")
        return self


class ContextCandidate(BaseModel):
    """Strict runtime value offered by a source adapter for assembly."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    candidate_id: str = Field(min_length=1)
    kind: ContextCandidateKind
    source_id: str | None = None
    content: str
    role: Literal["system", "user", "assistant", "tool"] = "user"
    retention: ContextCandidateRetention = ContextCandidateRetention.PREFERRED
    priority: int = Field(default=50, ge=0, le=100)
    source_order: int = Field(default=0, ge=0)
    truncation: ContextCandidateTruncation = ContextCandidateTruncation.HEAD
    trust: ContextCandidateTrust = ContextCandidateTrust.UNVERIFIED
    freshness: ContextCandidateFreshness = ContextCandidateFreshness.UNKNOWN
    conflict_key: str | None = Field(default=None, min_length=1)
    compacted_candidate_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _content_is_not_empty(self) -> "ContextCandidate":
        if not self.content.strip():
            raise ValueError("context candidate requires non-empty content")
        if self.compacted_candidate_ids:
            if self.kind != ContextCandidateKind.ARTIFACT:
                raise ValueError("only artifact candidates may compact source candidates")
            if self.truncation != ContextCandidateTruncation.FORBIDDEN:
                raise ValueError("context compaction candidates must forbid truncation")
            if len(self.compacted_candidate_ids) != len(set(self.compacted_candidate_ids)):
                raise ValueError("compacted candidate IDs must be unique")
            if self.candidate_id in self.compacted_candidate_ids:
                raise ValueError("compaction candidate cannot compact itself")
        return self


class ContextAssemblyPolicy(BaseModel):
    """Typed budget and strategy controlling one candidate selection pass."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    policy_id: Literal["retention_priority_order_v1"] = "retention_priority_order_v1"
    purpose: ContextRequestPurpose = ContextRequestPurpose.UNSPECIFIED
    max_prompt_chars: int = Field(ge=1)
    max_prompt_tokens: int | None = Field(default=None, ge=1)
    reserved_prompt_tokens: int = Field(default=0, ge=0)
    govern_exact_duplicates: bool = True
    omit_stale_candidates: bool = True
    resolve_explicit_conflicts: bool = True

    @model_validator(mode="after")
    def _reserve_fits_prompt_budget(self) -> "ContextAssemblyPolicy":
        if self.reserved_prompt_tokens and self.max_prompt_tokens is None:
            raise ValueError("reserved_prompt_tokens requires max_prompt_tokens")
        if (
            self.max_prompt_tokens is not None
            and self.reserved_prompt_tokens >= self.max_prompt_tokens
        ):
            raise ValueError("reserved_prompt_tokens must be smaller than max_prompt_tokens")
        return self


class ContextCandidateDecision(BaseModel):
    """Owned evidence for how one typed candidate was projected."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    candidate_id: str = Field(min_length=1)
    kind: ContextCandidateKind
    source_id: str | None = None
    retention: ContextCandidateRetention
    trust: ContextCandidateTrust = ContextCandidateTrust.UNVERIFIED
    freshness: ContextCandidateFreshness = ContextCandidateFreshness.UNKNOWN
    action: Literal["kept", "partially_kept", "omitted"]
    reason: Literal[
        "within_budget",
        "prompt_budget",
        "duplicate",
        "stale",
        "conflict_precedence",
        "governance_conflict",
        "compacted",
    ]
    governed_by_candidate_id: str | None = None
    original_chars: int = Field(default=0, ge=0)
    selected_chars: int = Field(default=0, ge=0)
    original_tokens: int | None = Field(default=None, ge=0)
    selected_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _sizes_match_action(self) -> "ContextCandidateDecision":
        if (self.original_tokens is None) != (self.selected_tokens is None):
            raise ValueError("candidate token evidence must be complete or absent")
        if self.action == "kept" and self.selected_chars != self.original_chars:
            raise ValueError("kept candidate must preserve its complete content")
        if self.action == "partially_kept" and not 0 < self.selected_chars < self.original_chars:
            raise ValueError("partially kept candidate requires a smaller non-empty projection")
        if self.action == "omitted" and self.selected_chars != 0:
            raise ValueError("omitted candidate cannot retain content")
        if self.reason in {"duplicate", "conflict_precedence", "compacted"}:
            if not self.governed_by_candidate_id:
                raise ValueError("governed omission requires the winning candidate ID")
        elif self.governed_by_candidate_id is not None:
            raise ValueError("winning candidate ID is only valid for governed omission")
        return self


class ContextSectionDecision(BaseModel):
    """One section-level keep, partial-keep, or omission decision."""

    model_config = ConfigDict(extra="forbid")

    section: Literal[
        "system_prompt",
        "session_constraints",
        "dialog_context",
        "related_files",
        "related_memories",
        "environment_context",
    ]
    action: Literal["kept", "partially_kept", "omitted", "empty"]
    reason: Literal["within_budget", "prompt_budget", "source_governance", "empty"]
    original_items: int = Field(default=0, ge=0)
    selected_items: int = Field(default=0, ge=0)
    original_chars: int = Field(default=0, ge=0)
    selected_chars: int = Field(default=0, ge=0)


class ContextSelectionMetadata(MetadataBase):
    """Auditable prompt-context budget and selection boundary."""

    kind: Literal[MetadataKind.CONTEXT_SELECTION] = MetadataKind.CONTEXT_SELECTION
    strategy: Literal[
        "priority_then_recency_v1",
        "retention_priority_order_v1",
    ] = "priority_then_recency_v1"
    request_purpose: ContextRequestPurpose = ContextRequestPurpose.UNSPECIFIED
    budget_unit: Literal["characters", "tokens"] = "characters"
    max_prompt_chars: int = Field(ge=1)
    max_prompt_tokens: int | None = Field(default=None, ge=1)
    requested_prompt_tokens: int | None = Field(default=None, ge=1)
    reserved_prompt_tokens: int = Field(default=0, ge=0)
    remaining_prompt_tokens: int | None = Field(default=None, ge=0)
    original_prompt_chars: int = Field(default=0, ge=0)
    final_prompt_chars: int = Field(default=0, ge=0)
    original_prompt_tokens: int | None = Field(default=None, ge=0)
    final_prompt_tokens: int | None = Field(default=None, ge=0)
    token_count_method: Literal["provider_tokenizer", "unavailable"] = "unavailable"
    tokenizer_id: str = ""
    model: str = ""
    truncated: bool = False
    section_decisions: list[ContextSectionDecision] = Field(default_factory=list)
    dialog_messages_total: int = Field(default=0, ge=0)
    dialog_messages_selected: int = Field(default=0, ge=0)
    dialog_start_index: int = Field(default=0, ge=0)
    oldest_selected_dialog_timestamp: str | None = None
    latest_dialog_timestamp: str | None = None
    assembly_status: ContextAssemblyStatus = ContextAssemblyStatus.READY
    candidate_decisions: list[ContextCandidateDecision] = Field(default_factory=list)
    compaction_attempts: list[ContextCompactionAttempt] = Field(default_factory=list)
    compaction_reuse_admissions: list[ContextCompactionReuseAdmission] = Field(default_factory=list)
    compaction_reuse_shadow_failures: list[ContextCompactionReuseShadowFailure] = Field(
        default_factory=list
    )
    omitted_required_candidate_ids: list[str] = Field(default_factory=list)
    governance_blocked_candidate_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _token_budget_evidence_is_complete(self) -> "ContextSelectionMetadata":
        if self.budget_unit == "tokens":
            if (
                self.max_prompt_tokens is None
                or self.original_prompt_tokens is None
                or self.final_prompt_tokens is None
                or self.token_count_method != "provider_tokenizer"
                or not self.tokenizer_id
                or not self.model
            ):
                raise ValueError("token budget requires complete provider tokenizer evidence")
            if self.final_prompt_tokens > self.max_prompt_tokens:
                raise ValueError("final prompt exceeds max_prompt_tokens")
            if self.requested_prompt_tokens is not None:
                if self.max_prompt_tokens != self.requested_prompt_tokens - self.reserved_prompt_tokens:
                    raise ValueError("prompt token budget arithmetic is inconsistent")
                if self.remaining_prompt_tokens != self.max_prompt_tokens - self.final_prompt_tokens:
                    raise ValueError("prompt token budget arithmetic is inconsistent")
            elif self.reserved_prompt_tokens or self.remaining_prompt_tokens is not None:
                raise ValueError("prompt token reserve requires requested_prompt_tokens")
        elif (
            self.requested_prompt_tokens is not None
            or self.reserved_prompt_tokens
            or self.remaining_prompt_tokens is not None
        ):
            raise ValueError("prompt token envelope requires token budget mode")
        decision_ids = [decision.candidate_id for decision in self.candidate_decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("candidate decision IDs must be unique")
        admission_ids = [admission.admission_id for admission in self.compaction_reuse_admissions]
        if len(admission_ids) != len(set(admission_ids)):
            raise ValueError("compaction reuse admission IDs must be unique")
        failure_ids = [failure.failure_id for failure in self.compaction_reuse_shadow_failures]
        if len(failure_ids) != len(set(failure_ids)):
            raise ValueError("compaction reuse shadow failure IDs must be unique")
        if self.assembly_status == ContextAssemblyStatus.READY and (
            self.omitted_required_candidate_ids or self.governance_blocked_candidate_ids
        ):
            raise ValueError("ready assembly cannot omit required candidates")
        if (
            self.assembly_status == ContextAssemblyStatus.BUDGET_INSUFFICIENT
            and not self.omitted_required_candidate_ids
        ):
            raise ValueError("budget_insufficient assembly requires omitted required candidates")
        if (
            self.assembly_status == ContextAssemblyStatus.BUDGET_INSUFFICIENT
            and self.governance_blocked_candidate_ids
        ):
            raise ValueError("budget_insufficient assembly cannot contain governance blockers")
        if self.assembly_status == ContextAssemblyStatus.GOVERNANCE_BLOCKED:
            if not self.governance_blocked_candidate_ids:
                raise ValueError("governance_blocked assembly requires blocked candidates")
            if not set(self.governance_blocked_candidate_ids).issubset(
                self.omitted_required_candidate_ids
            ):
                raise ValueError("governance blockers must identify omitted required candidates")
        elif self.governance_blocked_candidate_ids:
            raise ValueError("governance blockers require governance_blocked status")
        return self


class ContextAssemblyResult(BaseModel):
    """Derived typed output returned by one context assembly pass."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    prompt_text: str
    selected_candidates: list[ContextCandidate] = Field(default_factory=list)
    selection: ContextSelectionMetadata

    @model_validator(mode="after")
    def _selected_candidates_match_decisions(self) -> "ContextAssemblyResult":
        selected_ids = {candidate.candidate_id for candidate in self.selected_candidates}
        decision_ids = {
            decision.candidate_id
            for decision in self.selection.candidate_decisions
            if decision.action != "omitted"
        }
        if selected_ids != decision_ids:
            raise ValueError("selected candidates must match non-omitted candidate decisions")
        return self


class DerivedContextProjection(BaseModel):
    """Selected-only context view derived from one ContextLoader assembly.

    Raw turns and source stores remain authoritative.  This nested value carries
    the exact bounded candidates selected for downstream purpose adapters plus
    the hashes needed to reject stale or cross-session reuse.
    """

    model_config = ConfigDict(extra="forbid")

    context_request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    session_turn_source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    session_constraints_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dialog_candidates: list[ContextCandidate] = Field(default_factory=list)
    artifact_candidates: list[ContextCandidate] = Field(default_factory=list)
    projection_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class EditPlanMetadata(MetadataBase):
    """Evidence-backed edit request approved before any write operation."""

    kind: Literal[MetadataKind.EDIT_PLAN] = MetadataKind.EDIT_PLAN
    subgoal: str
    target_files: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    allowed_changes: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)
    risk_level: str = "medium"
    verification: list[str] = Field(default_factory=list)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class VerificationCommandSpec(BaseModel):
    """One ordered verification command with its execution boundary."""

    model_config = ConfigDict(extra="forbid")

    command: str
    cwd: str | None = None
    mode: str = "automatic"
    timeout: int | None = Field(default=None, ge=1)


class VerificationPlanMetadata(MetadataBase):
    """Verification plan selected after a write or other risky action."""

    kind: Literal[MetadataKind.VERIFICATION_PLAN] = MetadataKind.VERIFICATION_PLAN
    reason: str
    commands: list[str] = Field(default_factory=list)
    command_specs: list[VerificationCommandSpec] = Field(default_factory=list)
    next_command_index: int = Field(default=0, ge=0)
    completed_commands: list[str] = Field(default_factory=list)
    target_files: list[str] = Field(default_factory=list)
    fallback_checks: list[str] = Field(default_factory=list)
    required: bool = True
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _command_progress_is_a_contiguous_prefix(self) -> "VerificationPlanMetadata":
        if not self.command_specs and self.commands:
            object.__setattr__(
                self,
                "command_specs",
                [
                    VerificationCommandSpec(
                        command=command,
                        cwd=str(self.attributes.get("cwd")) if self.attributes.get("cwd") else None,
                        mode=str(self.attributes.get("mode") or "automatic"),
                        timeout=(int(self.attributes["timeout"]) if self.attributes.get("timeout") else None),
                    )
                    for command in self.commands
                ],
            )
        if self.command_specs and [spec.command for spec in self.command_specs] != self.commands:
            raise ValueError("verification command_specs must match commands in order")
        if self.next_command_index > len(self.commands):
            raise ValueError("verification next_command_index exceeds commands")
        if self.completed_commands != self.commands[: self.next_command_index]:
            raise ValueError("completed verification commands must be a contiguous prefix")
        return self


class PathIntentMetadata(MetadataBase):
    """Path interpretation request before any path-sensitive action runs."""

    kind: Literal[MetadataKind.PATH_INTENT] = MetadataKind.PATH_INTENT
    project_root: str = ""
    raw_path: str = ""
    intent_kind: str = "existing_file"
    operation: str = "read"
    path_source: str = "runtime"
    evidence: list[str] = Field(default_factory=list)
    candidate_paths: list[str] = Field(default_factory=list)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_source(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("source"), str):
            value = dict(value)
            value.setdefault("path_source", value.pop("source"))
        return value


class PathResolutionMetadata(MetadataBase):
    """Deterministic result of grounding a path against project truth."""

    kind: Literal[MetadataKind.PATH_RESOLUTION] = MetadataKind.PATH_RESOLUTION
    project_root: str = ""
    raw_path: str = ""
    resolved_path: str = ""
    status: str = "resolved"
    confidence: float = 1.0
    correction_rule: str = ""
    candidate_paths: list[str] = Field(default_factory=list)
    intent_kind: str = "existing_file"
    operation: str = "read"
    path_source: str = "runtime"
    inside_project: bool = True
    exists_verified: bool = False
    parent_exists: bool = False
    used_sketch: bool = False
    used_file_index: bool = False
    reason: str = ""
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_source(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("source"), str):
            value = dict(value)
            value.setdefault("path_source", value.pop("source"))
        return value


class DecisionNeedMetadata(MetadataBase):
    """Model- or controller-raised information need consumed by ToolRouter."""

    kind: Literal[MetadataKind.DECISION_NEED] = MetadataKind.DECISION_NEED
    need_type: str
    question: str
    phase: AgentPhase = AgentPhase.UNDERSTAND_TASK
    target_path: str | None = None
    candidate_paths: list[str] = Field(default_factory=list)
    operation_kind: str | None = None
    target_scope: str | None = None
    symbol_name: str | None = None
    symbol_type: str | None = None
    insertion_hint: str | None = None
    patch_mode: str | None = None
    query: str | None = None
    command: str | None = None
    decision_to_unlock: str | None = None
    expected_state_change: str | None = None
    cost_hint: str = "low"
    risk_level: str = "low"
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class ToolDecisionMetadata(MetadataBase):
    """Explanation for a routed tool selection."""

    kind: Literal[MetadataKind.TOOL_DECISION] = MetadataKind.TOOL_DECISION
    need_type: str
    question: str
    selected_tool: str
    phase: AgentPhase = AgentPhase.UNDERSTAND_TASK
    reason: str
    alternatives_considered: list[str] = Field(default_factory=list)
    expected_state_change: str | None = None
    risk_level: str = "low"
    cost_hint: str = "low"
    requires_confirmation: bool = False
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class GuardDecisionMetadata(MetadataBase):
    """Deterministic approval result for an edit plan."""

    kind: Literal[MetadataKind.GUARD_DECISION] = MetadataKind.GUARD_DECISION
    approved: bool
    reason: str
    risk_level: str = "medium"
    blocked_files: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    required_verification: list[str] = Field(default_factory=list)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class RuntimeStateMetadata(MetadataBase):
    """Explicit task state that drives phase transitions and tool decisions."""

    kind: Literal[MetadataKind.RUNTIME_STATE] = MetadataKind.RUNTIME_STATE
    goal: str
    execution_mode: RuntimeExecutionMode = RuntimeExecutionMode.MUTATION_ALLOWED
    execution_mode_source: RuntimeExecutionModeSource = RuntimeExecutionModeSource.DEFAULT
    execution_mode_reason: str = "No root-level read-only constraint was identified."
    recovery_status: RecoveryStatus = RecoveryStatus.NOT_REQUIRED
    recovery_reason_code: RecoveryReasonCode | None = None
    active_resume_attempt_id: str | None = None
    phase: AgentPhase = AgentPhase.UNDERSTAND_TASK
    known_facts: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    resolved_questions: list[str] = Field(default_factory=list)
    path_intents: list[PathIntentMetadata] = Field(default_factory=list)
    path_resolutions: list[PathResolutionMetadata] = Field(default_factory=list)
    candidate_files: dict[str, list[str]] = Field(default_factory=dict)
    selected_files: dict[str, list[str]] = Field(default_factory=dict)
    planned_edits: list[EditPlanMetadata] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)
    tool_history: list[dict[str, JsonValue]] = Field(default_factory=list)
    decision_history: list[ToolDecisionMetadata] = Field(default_factory=list)
    guard_history: list[GuardDecisionMetadata] = Field(default_factory=list)
    verification_status: VerificationStatus = VerificationStatus.NOT_STARTED
    risk_level: str = "low"
    core_success: bool | None = None
    project_improvement_policy: ProjectImprovementPolicy = Field(
        default_factory=ProjectImprovementPolicy
    )
    project_improvement_status: ProjectImprovementStatus = ProjectImprovementStatus.NOT_REQUESTED
    project_improvement_failure: str | None = None
    session_constraints: SessionConstraintState = Field(default_factory=SessionConstraintState)
    budget: RuntimeBudgetMetadata = Field(default_factory=RuntimeBudgetMetadata)
    replan_count: int = 0
    no_progress_rounds: int = 0
    completion_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_execution_mode(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        assumptions = migrated.get("assumptions") or []
        if (
            "execution_mode" not in migrated
            and isinstance(assumptions, list)
            and "runtime_mode:read_only_analysis" in assumptions
        ):
            migrated["execution_mode"] = RuntimeExecutionMode.READ_ONLY.value
            migrated["execution_mode_source"] = RuntimeExecutionModeSource.LEGACY_ASSUMPTION.value
            migrated.setdefault(
                "execution_mode_reason",
                "Migrated from legacy runtime_mode:read_only_analysis assumption.",
            )
        return migrated

    def add_fact(self, fact: str) -> None:
        fact = fact.strip()
        if fact and fact not in self.known_facts:
            self.known_facts.append(fact)

    def add_unknown(self, question: str) -> None:
        question = question.strip()
        if question and question not in self.unknowns:
            self.unknowns.append(question)

    def resolve_unknown(self, question: str) -> None:
        if question in self.unknowns:
            self.unknowns.remove(question)
        question = question.strip()
        if question and question not in self.resolved_questions:
            self.resolved_questions.append(question)

    def add_assumption(self, assumption: str) -> None:
        assumption = assumption.strip()
        if assumption and assumption not in self.assumptions:
            self.assumptions.append(assumption)

    def record_path_intent(self, intent: PathIntentMetadata) -> None:
        self.path_intents.append(intent)

    def record_path_resolution(self, resolution: PathResolutionMetadata) -> None:
        self.path_resolutions.append(resolution)

    def add_candidate_file(self, file_path: str, evidence: str) -> None:
        if not file_path:
            return
        evidence_list = self.candidate_files.setdefault(file_path, [])
        if evidence and evidence not in evidence_list:
            evidence_list.append(evidence)

    def select_file(self, file_path: str, evidence: str) -> None:
        if not file_path:
            return
        self.add_candidate_file(file_path, evidence)
        evidence_list = self.selected_files.setdefault(file_path, [])
        if evidence and evidence not in evidence_list:
            evidence_list.append(evidence)

    def add_modified_file(self, file_path: str) -> None:
        if file_path and file_path not in self.modified_files:
            self.modified_files.append(file_path)

    def record_tool_event(self, event: dict[str, JsonValue]) -> None:
        safe_event = json_safe(event)
        self.tool_history.append(safe_event if isinstance(safe_event, dict) else {"event": safe_event})

    def record_tool_decision(self, decision: ToolDecisionMetadata) -> None:
        self.decision_history.append(decision)

    def record_guard_decision(self, decision: GuardDecisionMetadata) -> None:
        self.guard_history.append(decision)

    def request_replan(self, reason: str) -> None:
        if self.budget.replan_rounds_used >= self.budget.max_replan_rounds:
            self.block("replan budget exhausted")
            return
        self.replan_count += 1
        self.budget.consume_replan_round()
        self.phase = AgentPhase.REPLAN
        self.add_unknown(reason)

    def block(self, reason: str) -> None:
        self.phase = AgentPhase.BLOCKED
        self.completion_reason = reason


class ProjectFingerprint(BaseModel):
    """Non-secret project identity used to detect drift before resume."""

    model_config = ConfigDict(extra="forbid")

    project_root: str
    git_repository: str | None = None
    git_head: str | None = None
    git_branch: str | None = None
    git_dirty: bool | None = None
    git_status_hash: str | None = None
    target_file_hashes: dict[str, str] = Field(default_factory=dict)
    expected_target_file_hashes: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    interpreter: str | None = None
    environment_id: str | None = None


class ObservedFileMutationResult(BaseModel):
    """Minimal durable result needed to apply or reconcile a file mutation."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    file_path: str | None = None
    error_type: str | None = None
    error_message: str | None = None


class SessionStage(str, Enum):
    """Durable position inside the monolithic runtime session."""

    DECOMPOSITION_RECORDED = "decomposition_recorded"
    TASK_EXECUTION = "task_execution"
    TASKS_EXECUTED = "tasks_executed"
    RESULT_ASSEMBLY = "result_assembly"
    COMPLETED = "completed"


class SessionSemanticSnapshot(BaseModel):
    """Minimal semantic facts needed after decomposition without another LLM call."""

    model_config = ConfigDict(extra="forbid")

    task_type: str
    risk_level: str
    required_resources: list[str] = Field(default_factory=list)
    expected_deliverables: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SessionBootstrapCursor(BaseModel):
    """Hash-only restart marker before a durable decomposition exists."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["standard", "enhanced_ui"] = "standard"
    goal_hash: str


class SessionTaskResult(BaseModel):
    """Strict completed task result retained by the owning session cursor."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: Literal["completed", "failed", "blocked", "cancelled"]
    summary_text: str = ""
    error: str | None = None
    duration: float | None = Field(default=None, ge=0.0)
    observed_modified_files: list[str] = Field(default_factory=list)


class SessionExecutionCursor(BaseModel):
    """Owned checkpoint value identifying the exact remaining session work."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["standard", "enhanced_ui"] = "standard"
    stage: SessionStage
    plan_hash: str
    semantic: SessionSemanticSnapshot
    original_task: TaskGraphNodeMetadata
    tasks: list[TaskGraphNodeMetadata]
    execution_order: list[str]
    next_task_index: int = Field(default=0, ge=0)
    results: list[SessionTaskResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def _cursor_is_contiguous_and_references_one_plan(self) -> "SessionExecutionCursor":
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("session cursor task ids must be unique")
        if len(self.execution_order) != len(task_ids) or set(self.execution_order) != set(task_ids):
            raise ValueError("execution_order must contain every session task exactly once")
        if self.next_task_index > len(self.execution_order):
            raise ValueError("next_task_index exceeds execution_order")
        result_ids = [result.task_id for result in self.results]
        if len(result_ids) != len(set(result_ids)) or not set(result_ids).issubset(set(task_ids)):
            raise ValueError("session results must uniquely reference planned tasks")
        required_result_ids = set(self.execution_order[: self.next_task_index])
        if not required_result_ids.issubset(set(result_ids)):
            raise ValueError("session cursor requires a result for every task before next_task_index")
        return self


class DurableArtifactReference(BaseModel):
    """Checksum-verified artifact owned by one checkpointed run."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    kind: str
    integrity_checksum: str
    bytes: int = Field(ge=0)


class ContextCompactionBinding(BaseModel):
    """Bind one compact projection record to its checksum artifact."""

    model_config = ConfigDict(extra="forbid")

    record: ContextCompactionRecord
    artifact: DurableArtifactReference
    source_binding_hash: str = ""

    @model_validator(mode="after")
    def _artifact_is_context_compaction(self) -> "ContextCompactionBinding":
        if self.artifact.kind != "context_compaction":
            raise ValueError("artifact kind must be context_compaction")
        if self.source_binding_hash:
            digest = self.source_binding_hash.removeprefix("sha256:")
            if (
                not self.source_binding_hash.startswith("sha256:")
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError("source_binding_hash must be empty or a sha256 digest")
        return self


class RuntimeFinalizationStage(str, Enum):
    """Monotonic durable stages for turning completed state into one run result."""

    STATE_COMPLETED = "state_completed"
    REPORT_PERSISTED = "report_persisted"
    RUN_FINALIZED = "run_finalized"


class RuntimePromptContextSnapshot(BaseModel):
    """Checkpoint-owned binding to one exact bounded model-facing context."""

    model_config = ConfigDict(extra="forbid")

    context_id: str
    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prompt_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selection: ContextSelectionMetadata
    context_artifact: DurableArtifactReference
    compaction_bindings: list[ContextCompactionBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _artifact_is_prompt_context(self) -> "RuntimePromptContextSnapshot":
        if self.context_artifact.kind != "prompt_context":
            raise ValueError("context_artifact kind must be prompt_context")
        return self


class RuntimeFinalizationCursor(BaseModel):
    """Owned finalization progress and evidence nested in a runtime checkpoint."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    finalization_id: str
    stage: RuntimeFinalizationStage
    outcome: Literal["success", "failed"]
    report_source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    report_artifact: DurableArtifactReference | None = None
    run_finalized_event_id: str | None = None

    @model_validator(mode="after")
    def _stage_requires_evidence(self) -> "RuntimeFinalizationCursor":
        if self.stage == RuntimeFinalizationStage.STATE_COMPLETED:
            if self.report_artifact is not None or self.run_finalized_event_id is not None:
                raise ValueError("state_completed cannot contain later-stage evidence")
        elif self.stage == RuntimeFinalizationStage.REPORT_PERSISTED:
            if self.report_artifact is None or self.run_finalized_event_id is not None:
                raise ValueError("report_persisted requires report artifact evidence only")
        elif self.report_artifact is None or not self.run_finalized_event_id:
            raise ValueError("run_finalized requires report artifact and run finalized event evidence")
        return self


class PendingLLMRequest(BaseModel):
    """Hash-only marker for a provider request that may be in flight."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    request_ordinal: int = Field(ge=1)
    request_hash: str
    hash_version: LLMRequestHashVersion = LLMRequestHashVersion.LEGACY_UNBOUND_V1

    @model_validator(mode="after")
    def _hash_matches_version(self) -> "PendingLLMRequest":
        if self.hash_version == LLMRequestHashVersion.PROVIDER_BOUND_V2 and not self.request_hash.startswith(
            "v2:sha256:"
        ):
            raise ValueError("provider_bound_v2 request hash must use the v2:sha256 prefix")
        return self


class LLMReplayEntry(BaseModel):
    """Observed response that can be replayed without another provider call."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    request_ordinal: int = Field(ge=1)
    request_hash: str
    hash_version: LLMRequestHashVersion = LLMRequestHashVersion.LEGACY_UNBOUND_V1
    response_artifact: DurableArtifactReference

    @model_validator(mode="after")
    def _hash_matches_version(self) -> "LLMReplayEntry":
        if self.hash_version == LLMRequestHashVersion.PROVIDER_BOUND_V2 and not self.request_hash.startswith(
            "v2:sha256:"
        ):
            raise ValueError("provider_bound_v2 request hash must use the v2:sha256 prefix")
        return self


class ReadToolReplayEntry(BaseModel):
    """Observed local read result keyed to one deterministic tool call."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    step_id: str
    call_id: str
    tool_name: Literal["file_reader", "multi_file_reader"]
    input_hash: str
    result_artifact: DurableArtifactReference
    applied: bool = False


class RuntimeCheckpointMetadata(MetadataBase):
    """Durable runtime state captured at one explicitly resumable boundary."""

    kind: Literal[MetadataKind.RUNTIME_CHECKPOINT] = MetadataKind.RUNTIME_CHECKPOINT
    checkpoint_id: str
    generation: int = Field(ge=1)
    run_id: str
    root_task_id: str
    session_id: str
    resume_attempt_id: str | None = None
    resume_source_checkpoint_id: str | None = None
    checkpoint_reason: str
    safe_boundary: CheckpointBoundary
    runtime_state: RuntimeStateMetadata
    # Conversation ingress is a bounded session owner snapshot.  The nested
    # runtime_state.session_constraints remains the execution authority; this
    # field preserves turn identity and pending proposals across resume.
    session_ingress_state: SessionIngressState | None = None
    last_durable_event_id: str | None = None
    last_durable_event_sequence: int = Field(default=0, ge=0)
    subtask_id: str | None = None
    step_id: str | None = None
    execution_id: str | None = None
    side_effect_state: Literal["none", "prepared", "observed", "applied", "indeterminate"] = "none"
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_input_hash: str | None = None
    tool_input: ToolInputMetadata | None = None
    observed_file_result: ObservedFileMutationResult | None = None
    observed_failure: FailureMetadata | None = None
    pending_verification: VerificationPlanMetadata | None = None
    session_cursor: SessionExecutionCursor | None = None
    session_bootstrap: SessionBootstrapCursor | None = None
    pending_llm_request: PendingLLMRequest | None = None
    llm_replay_entries: list[LLMReplayEntry] = Field(default_factory=list)
    read_tool_replay_entries: list[ReadToolReplayEntry] = Field(default_factory=list)
    prompt_context_snapshot: RuntimePromptContextSnapshot | None = None
    finalization_cursor: RuntimeFinalizationCursor | None = None
    mutation_class: Literal["none", "read_only", "idempotent", "mutating", "externally_indeterminate"] = "none"
    project_fingerprint: ProjectFingerprint
    git_snapshot_ref: str | None = None
    runtime_version: str = ""
    integrity_checksum: str = ""

    @model_validator(mode="after")
    def _finalization_boundary_matches_cursor(self) -> "RuntimeCheckpointMetadata":
        expected = {
            CheckpointBoundary.RUNTIME_STATE_COMPLETED: RuntimeFinalizationStage.STATE_COMPLETED,
            CheckpointBoundary.RUNTIME_REPORT_PERSISTED: RuntimeFinalizationStage.REPORT_PERSISTED,
            CheckpointBoundary.RUNTIME_FINALIZED: RuntimeFinalizationStage.RUN_FINALIZED,
        }
        expected_stage = expected.get(self.safe_boundary)
        if expected_stage is not None:
            if self.finalization_cursor is None or self.finalization_cursor.stage != expected_stage:
                raise ValueError("finalization checkpoint boundary requires the matching cursor stage")
        if (
            self.safe_boundary == CheckpointBoundary.CONTEXT_ASSEMBLED
            and self.prompt_context_snapshot is None
        ):
            raise ValueError("context_assembled boundary requires prompt_context_snapshot")
        if (
            self.session_ingress_state is not None
            and self.session_ingress_state.session_constraints != self.runtime_state.session_constraints
        ):
            raise ValueError("checkpoint ingress constraints must match runtime execution constraints")
        if (
            self.session_ingress_state is not None
            and self.session_ingress_state.identity.run_id != self.session_id
        ):
            raise ValueError("checkpoint session identity differs from ingress run identity")
        return self


class RuntimeResumeDecisionMetadata(MetadataBase):
    """Explainable preflight decision for one explicit resume request."""

    kind: Literal[MetadataKind.RUNTIME_RESUME_DECISION] = MetadataKind.RUNTIME_RESUME_DECISION
    checkpoint_id: str
    run_id: str
    root_task_id: str
    session_id: str
    resume_attempt_id: str
    decision: Literal["exact_resume", "reconcile_then_resume", "replan", "blocked"]
    recoverability: Recoverability
    recovery_mode: RecoveryMode
    automation_policy: RecoveryAutomationPolicy
    reason_code: RecoveryReasonCode
    safe_boundary: str
    project_drift: list[str] = Field(default_factory=list)
    budget_remaining: dict[str, int] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    blockers: list[RecoveryBlocker] = Field(default_factory=list)
    fallback: RecoveryFallback | None = None
    next_checkpoint_id: str | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0)
    reason: str
    next_action: str

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_decision(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        if all(
            field in migrated
            for field in ("recoverability", "recovery_mode", "automation_policy", "reason_code")
        ):
            return migrated
        decision = str(migrated.get("decision") or "blocked")
        mappings: dict[str, tuple[str, str, str, str, str]] = {
            "exact_resume": (
                Recoverability.RECOVERABLE_NOW.value,
                RecoveryMode.EXACT_RESUME.value,
                RecoveryAutomationPolicy.AUTOMATIC_ALLOWED.value,
                RecoveryReasonCode.CHECKPOINT_VALID.value,
                RecoveryFallbackAction.NONE.value,
            ),
            "reconcile_then_resume": (
                Recoverability.RECOVERABLE_NOW.value,
                RecoveryMode.RECONCILE_THEN_RESUME.value,
                RecoveryAutomationPolicy.AUTOMATIC_ALLOWED.value,
                RecoveryReasonCode.PENDING_FILE_MUTATION.value,
                RecoveryFallbackAction.NONE.value,
            ),
            "replan": (
                Recoverability.RECOVERABLE_NOW.value,
                RecoveryMode.REPLAN_FROM_CHECKPOINT.value,
                RecoveryAutomationPolicy.AUTOMATIC_ALLOWED.value,
                RecoveryReasonCode.CHECKPOINT_VALID.value,
                RecoveryFallbackAction.NONE.value,
            ),
            "blocked": (
                Recoverability.RECOVERABLE_AFTER_ACTION.value,
                RecoveryMode.USER_ASSISTED_RESUME.value,
                RecoveryAutomationPolicy.MANUAL_ONLY.value,
                RecoveryReasonCode.LEGACY_BLOCKED.value,
                RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION.value,
            ),
        }
        recoverability, recovery_mode, automation_policy, reason_code, fallback_action = mappings.get(
            decision,
            mappings["blocked"],
        )
        migrated.setdefault("recoverability", recoverability)
        migrated.setdefault("recovery_mode", recovery_mode)
        migrated.setdefault("automation_policy", automation_policy)
        migrated.setdefault("reason_code", reason_code)
        migrated.setdefault(
            "fallback",
            {
                "action": fallback_action,
                "reason_code": reason_code,
                "requires_user_authorization": decision == "blocked",
            },
        )
        return migrated

    @model_validator(mode="after")
    def _control_fields_are_consistent(self) -> "RuntimeResumeDecisionMetadata":
        if self.recoverability == Recoverability.NOT_RECOVERABLE:
            if self.recovery_mode != RecoveryMode.NONE:
                raise ValueError("not_recoverable requires recovery_mode=none")
            if self.fallback is None or not self.fallback.preserve_original_run:
                raise ValueError("not_recoverable requires an evidence-preserving fallback")
        if self.recoverability == Recoverability.ALREADY_COMPLETE and self.recovery_mode != RecoveryMode.RETURN_COMPLETED:
            raise ValueError("already_complete requires return_completed mode")
        if (
            self.recoverability == Recoverability.RECOVERABLE_AFTER_ACTION
            and self.automation_policy == RecoveryAutomationPolicy.AUTOMATIC_ALLOWED
        ):
            raise ValueError("automatic_allowed cannot bypass a required recovery action")
        if self.automation_policy == RecoveryAutomationPolicy.AUTOMATIC_ALLOWED and any(
            blocker.requires_user_action for blocker in self.blockers
        ):
            raise ValueError("automatic_allowed cannot include a user-action blocker")
        legacy_decision_by_mode = {
            RecoveryMode.RETURN_COMPLETED: "exact_resume",
            RecoveryMode.FINALIZE_FROM_CHECKPOINT: "exact_resume",
            RecoveryMode.EXACT_RESUME: "exact_resume",
            RecoveryMode.RECONCILE_THEN_RESUME: "reconcile_then_resume",
            RecoveryMode.REPLAN_FROM_CHECKPOINT: "replan",
            RecoveryMode.RETRY_FROM_CHECKPOINT: "exact_resume",
            RecoveryMode.USER_ASSISTED_RESUME: "blocked",
            RecoveryMode.NONE: "blocked",
        }
        expected_legacy_decision = (
            "blocked"
            if self.recoverability in {
                Recoverability.RECOVERABLE_AFTER_ACTION,
                Recoverability.NOT_RECOVERABLE,
            }
            else legacy_decision_by_mode[self.recovery_mode]
        )
        if self.decision != expected_legacy_decision:
            raise ValueError(
                f"legacy decision {self.decision!r} contradicts recovery_mode {self.recovery_mode!r}"
            )
        return self


class RuntimeReportMetadata(MetadataBase):
    """Final auditable runtime report derived from runtime state."""

    kind: Literal[MetadataKind.RUNTIME_REPORT] = MetadataKind.RUNTIME_REPORT
    goal: str
    state_hash: str = ""
    phase: AgentPhase
    recovery_status: RecoveryStatus = RecoveryStatus.NOT_REQUIRED
    recovery_reason_code: RecoveryReasonCode | None = None
    active_resume_attempt_id: str | None = None
    completion_reason: str | None = None
    core_success: bool | None = None
    project_improvement_policy: ProjectImprovementPolicy = Field(
        default_factory=ProjectImprovementPolicy
    )
    project_improvement_status: ProjectImprovementStatus = ProjectImprovementStatus.NOT_REQUESTED
    project_improvement_failure: str | None = None
    known_facts: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    path_resolutions: list[PathResolutionMetadata] = Field(default_factory=list)
    selected_files: dict[str, list[str]] = Field(default_factory=dict)
    modified_files: list[str] = Field(default_factory=list)
    planned_edits: list[EditPlanMetadata] = Field(default_factory=list)
    verification_status: VerificationStatus = VerificationStatus.NOT_STARTED
    risk_level: str = "low"
    tool_decisions: list[ToolDecisionMetadata] = Field(default_factory=list)
    tool_history: list[dict[str, JsonValue]] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)
