"""Core metadata contracts for model-harness data exchange."""

from __future__ import annotations

import inspect
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


JsonValue: TypeAlias = Any


class MetadataKind(str, Enum):
    """Canonical metadata categories shared across agents and tools."""

    TEXT_ARTIFACT = "text_artifact"
    CODE_ARTIFACT = "code_artifact"
    FILE_ARTIFACT = "file_artifact"
    COMMAND_ARTIFACT = "command_artifact"
    SEARCH_ARTIFACT = "search_artifact"
    EMBEDDING_ARTIFACT = "embedding_artifact"
    WARNING_ITEM = "warning_item"
    WARNING_CHECK_RESULT = "warning_check_result"
    BUG_FIX_ATTEMPT = "bug_fix_attempt"
    BUG_FIX_RESULT = "bug_fix_result"
    ENVIRONMENT_FAILURE = "environment_failure"
    ENVIRONMENT_FIX_RESULT = "environment_fix_result"
    TASK_ROUTE = "task_route"
    TOOL_INPUT = "tool_input"
    TOOL_SELECTION = "tool_selection"
    TOOL_CONTRACT = "tool_contract"
    TOOL_CHAIN = "tool_chain"
    TOOL_CONTEXT = "tool_context"
    TOOL_CALL = "tool_call"
    TOOL_ERROR = "tool_error"
    TOOL_EVENT = "tool_event"
    TOOL_LOOP = "tool_loop"
    TOOL_RESULT = "tool_result"
    TOOL_EXECUTION_ENVELOPE = "tool_execution_envelope"
    TASK_RESULT = "task_result"
    AGENT_EXECUTION = "agent_execution"
    MODULE_EXECUTION = "module_execution"
    FAILURE = "failure"
    COLLECTED_DATA = "collected_data"
    PROCESSED_DATA = "processed_data"
    PRESENTATION = "presentation"
    PRODUCT_INTENT = "product_intent"
    VALIDATION_ISSUE = "validation_issue"
    PROJECT_OBJECTIVE = "project_objective"
    SUCCESS_METRIC = "success_metric"
    PROJECT_DIMENSION_ASSESSMENT = "project_dimension_assessment"
    PROJECT_DEPENDENCY = "project_dependency"
    DEPENDENCY_STRATEGY = "dependency_strategy"
    PROJECT_STACK_PRESET = "project_stack_preset"
    FILE_CONTENT_SECTION = "file_content_section"
    FILE_CONTENT_INDEX = "file_content_index"
    DIRECTORY_SKETCH = "directory_sketch"
    TASK_FILE_RESOLUTION_REQUEST = "task_file_resolution_request"
    RELATED_PROJECT_FILE = "related_project_file"
    TASK_FILE_RESOLUTION = "task_file_resolution"
    GIT_REPOSITORY = "git_repository"
    GIT_SNAPSHOT = "git_snapshot"
    GIT_DIFF_CONTEXT = "git_diff_context"
    IMPROVEMENT_CANDIDATE = "improvement_candidate"
    PROJECT_DIAGNOSIS = "project_diagnosis"
    REFERENCE_INSIGHT = "reference_insight"
    PROJECT_STATE = "project_state"
    IMPROVEMENT_ANALYSIS = "improvement_analysis"
    ENVIRONMENT_SYNC = "environment_sync"
    AUTONOMY_DECISION = "autonomy_decision"
    PROBLEM_SIGNAL = "problem_signal"
    PROBLEM_JUDGMENT = "problem_judgment"
    DIFFICULTY_ASSESSMENT = "difficulty_assessment"
    RESOLUTION_PLAN = "resolution_plan"
    TASK_GRAPH_NODE = "task_graph_node"
    TASK_GRAPH_EDGE = "task_graph_edge"
    EXECUTION_STATE = "execution_state"
    RUNTIME_BUDGET = "runtime_budget"
    CONTEXT_SELECTION = "context_selection"
    RUNTIME_CHECKPOINT = "runtime_checkpoint"
    RUNTIME_RESUME_DECISION = "runtime_resume_decision"
    RUNTIME_STATE = "runtime_state"
    PATH_INTENT = "path_intent"
    PATH_RESOLUTION = "path_resolution"
    DECISION_NEED = "decision_need"
    EDIT_PLAN = "edit_plan"
    VERIFICATION_PLAN = "verification_plan"
    GUARD_DECISION = "guard_decision"
    TOOL_DECISION = "tool_decision"
    RUNTIME_REPORT = "runtime_report"
    ITERATION_TURN_RECORD = "iteration_turn_record"
    LLM_REQUEST = "llm_request"
    LLM_RESPONSE = "llm_response"
    EXECUTION_CONTEXT = "execution_context"
    LOG_EVENT = "log_event"


class MetadataSource(BaseModel):
    """Source that produced or owns a metadata payload."""

    model_config = ConfigDict(extra="forbid")

    source_type: str = "system"
    source_name: str = "openpilot"


class CorrelationInfo(BaseModel):
    """Stable IDs used to connect metadata across model-harness steps."""

    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None
    turn_id: int | None = None
    task_id: str | None = None
    step_id: str | None = None
    execution_id: str | None = None


class MetadataBase(BaseModel):
    """Base class for every strict OpenPilot metadata payload."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    kind: MetadataKind
    schema_version: Literal["1.0"] = "1.0"
    source: MetadataSource = Field(default_factory=MetadataSource)
    correlation: CorrelationInfo = Field(default_factory=CorrelationInfo)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    annotations: dict[str, JsonValue] = Field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict suitable for logs and model harnesses."""
        payload = self.model_dump(mode="python")
        safe_payload = json_safe(payload)
        return safe_payload if isinstance(safe_payload, dict) else {"value": safe_payload}

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        attributes = getattr(self, "attributes", None)
        if isinstance(attributes, dict):
            return attributes.get(key, default)
        return default

    def __getitem__(self, key: str) -> Any:
        value = self.get(key, None)
        if value is None and key not in self:
            raise KeyError(key)
        return value

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        if hasattr(self, key):
            return True
        attributes = getattr(self, "attributes", None)
        return isinstance(attributes, dict) and key in attributes


def ensure_metadata(value: Any, metadata_type: type[MetadataBase]) -> MetadataBase:
    """Validate a value as a concrete metadata type."""
    if isinstance(value, metadata_type):
        return value
    if isinstance(value, MetadataBase):
        raise TypeError(f"Expected {metadata_type.__name__}, got {type(value).__name__}")
    if isinstance(value, dict):
        return metadata_type.model_validate(value)
    raise TypeError(f"Expected {metadata_type.__name__}, got {type(value).__name__}")


def json_safe(value: Any) -> Any:
    """Return a recursive JSON-safe representation of arbitrary metadata values."""
    return _json_safe(value, set())


def _callable_summary(value: Any) -> str:
    name = getattr(value, "__name__", type(value).__name__)
    return f"<callable:{name}>"


def _json_safe(value: Any, seen: set[int]) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if inspect.ismethod(value) or inspect.isfunction(value) or callable(value):
        return _callable_summary(value)
    if isinstance(value, Enum):
        return _json_safe(value.value, seen)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, MetadataBase):
        object_id = id(value)
        if object_id in seen:
            return f"<recursive:{type(value).__name__}>"
        seen.add(object_id)
        try:
            return _json_safe(value.model_dump(mode="python"), seen)
        finally:
            seen.discard(object_id)
    if isinstance(value, BaseModel):
        object_id = id(value)
        if object_id in seen:
            return f"<recursive:{type(value).__name__}>"
        seen.add(object_id)
        try:
            return _json_safe(value.model_dump(mode="python", exclude={"runtime_handles"}), seen)
        finally:
            seen.discard(object_id)
    if isinstance(value, Mapping):
        object_id = id(value)
        if object_id in seen:
            return "<recursive:dict>"
        seen.add(object_id)
        try:
            return {
                str(key.value if isinstance(key, Enum) else key): _json_safe(item, seen)
                for key, item in value.items()
                if not str(key).startswith("_")
            }
        finally:
            seen.discard(object_id)
    if isinstance(value, (list, tuple, set, frozenset)):
        object_id = id(value)
        if object_id in seen:
            return f"<recursive:{type(value).__name__}>"
        seen.add(object_id)
        try:
            return [_json_safe(item, seen) for item in value]
        finally:
            seen.discard(object_id)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return repr(value)
    return str(value)


def metadata_summary(value: Any) -> Any:
    """Return a compact JSON-safe representation for logging."""
    if isinstance(value, MetadataBase):
        return value.to_json_dict()
    if hasattr(value, "model_dump"):
        return json_safe(value)
    if isinstance(value, Mapping):
        return {str(key): metadata_summary(item) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [metadata_summary(item) for item in list(value)[:20]]
    if isinstance(value, str) and len(value) > 1000:
        return value[:1000] + "...[truncated]"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return json_safe(value)
