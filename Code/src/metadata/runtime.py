"""Runtime metadata contracts for LLM calls, execution contexts, and logs."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metadata.base import JsonValue, MetadataBase, MetadataKind
from metadata.agent_runtime import ContextSelectionMetadata
from metadata.results import FailureMetadata, ResultStatus, TaskResultMetadata, ToolResultMetadata
from metadata.tooling import ToolContextMetadata, ToolEventMetadata, ToolInputMetadata


class ReasoningMode(str, Enum):
    PROVIDER_DEFAULT = "provider_default"
    DISABLED = "disabled"
    ADAPTIVE = "adaptive"
    ENABLED = "enabled"


class ReasoningDecisionComplexity(str, Enum):
    """Provider-neutral complexity of one model decision.

    This is separate from completion reservation complexity so routing
    reasoning cannot silently change the completion budget.
    """

    ROUTINE = "routine"
    STANDARD = "standard"
    COMPLEX = "complex"


class ReasoningEffort(str, Enum):
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class UnsupportedReasoningBehavior(str, Enum):
    REJECT = "reject"
    CLAMP = "clamp"
    PROVIDER_DEFAULT = "provider_default"


class ReasoningResolution(str, Enum):
    EXACT = "exact"
    MAPPED = "mapped"
    CLAMPED = "clamped"
    OMITTED = "omitted"
    UNSUPPORTED = "unsupported"


class ReasoningCapabilityProfileId(str, Enum):
    GENERIC_OPENAI_COMPATIBLE = "generic-openai-compatible"
    OPENAI_CHAT_KNOWN = "openai-chat-known"
    OPENAI_CHAT_NO_REASONING_KNOWN = "openai-chat-no-reasoning-known"
    DEEPSEEK_CHAT_KNOWN = "deepseek-chat-known"
    ANTHROPIC_MESSAGES_KNOWN = "anthropic-messages-known"
    GEMINI_GENERATE_CONTENT_KNOWN = "gemini-generate-content-known"


class ReasoningTransportFamily(str, Enum):
    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    GOOGLE_GENERATE_CONTENT = "google_generate_content"


class ReasoningPolicy(BaseModel):
    """Provider-neutral reasoning intent owned by one LLM request."""

    model_config = ConfigDict(extra="forbid")

    mode: ReasoningMode = ReasoningMode.PROVIDER_DEFAULT
    effort: ReasoningEffort | None = None
    token_budget: int | None = Field(default=None, ge=0)
    unsupported_behavior: UnsupportedReasoningBehavior = UnsupportedReasoningBehavior.REJECT

    @model_validator(mode="after")
    def _mode_fields_are_compatible(self) -> "ReasoningPolicy":
        if self.mode in {ReasoningMode.PROVIDER_DEFAULT, ReasoningMode.DISABLED} and (
            self.effort is not None or self.token_budget is not None
        ):
            raise ValueError("provider_default and disabled reasoning cannot set effort or token_budget")
        return self


class ResolvedReasoningPolicy(BaseModel):
    """Capability-resolved reasoning observation used by transport and audit."""

    model_config = ConfigDict(extra="forbid")

    requested: ReasoningPolicy
    effective_mode: ReasoningMode
    effective_effort: ReasoningEffort | None = None
    effective_token_budget: int | None = Field(default=None, ge=0)
    resolution: ReasoningResolution
    profile_id: ReasoningCapabilityProfileId
    profile_version: str
    transport_family: ReasoningTransportFamily


class ReasoningUsageObservation(BaseModel):
    """Provider-normalized reasoning evidence attached to one LLM response.

    ``None`` for ``reasoning_tokens`` means the provider did not expose a
    trustworthy token count. It is deliberately different from zero so that
    budget and audit code never turns an absent field into a false measurement.
    """

    model_config = ConfigDict(extra="forbid")

    reasoning_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens_source: str | None = None
    reasoning_content_present: bool = False
    visible_content_empty: bool = False
    finish_reason: str | None = None


class LLMRequestMetadata(MetadataBase):
    kind: Literal[MetadataKind.LLM_REQUEST] = MetadataKind.LLM_REQUEST
    task: str | None = None
    purpose: str | None = None
    trace_info: dict[str, JsonValue] = Field(default_factory=dict)
    context_selection: ContextSelectionMetadata | None = None
    reasoning_policy: ReasoningPolicy = Field(default_factory=ReasoningPolicy)
    resolved_reasoning_policy: ResolvedReasoningPolicy | None = None


class LLMResponseMetadata(MetadataBase):
    kind: Literal[MetadataKind.LLM_RESPONSE] = MetadataKind.LLM_RESPONSE
    model: str = ""
    provider: str = ""
    usage: dict[str, JsonValue] = Field(default_factory=dict)
    finish_reason: str | None = None
    provider_details: dict[str, JsonValue] = Field(default_factory=dict)


class ExecutionContextMetadata(MetadataBase):
    kind: Literal[MetadataKind.EXECUTION_CONTEXT] = MetadataKind.EXECUTION_CONTEXT
    execution_id: str
    tool_name: str
    step_id: str
    timeout_seconds: int = 300
    max_retries: int = 3
    permission_level: str = "low"
    depends_on: list[str] = Field(default_factory=list)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class LogEventMetadata(MetadataBase):
    kind: Literal[MetadataKind.LOG_EVENT] = MetadataKind.LOG_EVENT
    source_type: str
    source_name: str
    phase: str
    event_type: str
    success: bool | None = None
    duration_ms: int | None = None
    input_summary: Any | None = None
    output_summary: Any | None = None
    error: str | None = None
    trace_info: dict[str, JsonValue] = Field(default_factory=dict)


class ToolExecutionEnvelopeMetadata(MetadataBase):
    kind: Literal[MetadataKind.TOOL_EXECUTION_ENVELOPE] = MetadataKind.TOOL_EXECUTION_ENVELOPE
    tool_name: str
    step_id: str
    status: ResultStatus
    success: bool
    input_metadata: ToolInputMetadata
    output_metadata: ToolResultMetadata | None = None
    failure: FailureMetadata | None = None
    duration_seconds: float = 0.0
    timeout_override: int | None = None
    attempts_used: int = 1
    retry_count: int = 0
    retry_history: list[dict[str, JsonValue]] = Field(default_factory=list)
    call_id: str | None = None
    tool_context: ToolContextMetadata | None = None
    tool_events: list[ToolEventMetadata] = Field(default_factory=list)

    @property
    def output(self) -> MetadataBase | None:
        return self.output_metadata.result if self.output_metadata else None

    @property
    def error_message(self) -> str | None:
        return self.failure.error_message if self.failure else None


class AgentExecutionMetadata(MetadataBase):
    kind: Literal[MetadataKind.AGENT_EXECUTION] = MetadataKind.AGENT_EXECUTION
    agent_name: str
    status: ResultStatus
    success: bool
    result_metadata: TaskResultMetadata | MetadataBase | None = None
    failure: FailureMetadata | None = None
    duration_seconds: float = 0.0
    tool_invocations: list[ToolExecutionEnvelopeMetadata] = Field(default_factory=list)


class ModuleExecutionMetadata(MetadataBase):
    kind: Literal[MetadataKind.MODULE_EXECUTION] = MetadataKind.MODULE_EXECUTION
    module_name: str
    status: ResultStatus
    success: bool
    result_metadata: TaskResultMetadata | MetadataBase | None = None
    failure: FailureMetadata | None = None
    duration_seconds: float = 0.0
