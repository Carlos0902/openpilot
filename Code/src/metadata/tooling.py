"""Tool orchestration metadata contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from metadata.artifacts import (
    CodeArtifactMetadata,
    CommandArtifactMetadata,
    EmbeddingArtifactMetadata,
    FileArtifactMetadata,
    SearchArtifactMetadata,
    TextArtifactMetadata,
    FileReadMode,
    FileReadWindow,
)
from metadata.base import JsonValue, MetadataBase, MetadataKind
from metadata.project import EnvironmentOperation
from metadata.results import FailureMetadata, ToolResultMetadata


class FileReadWindowSpec(FileReadWindow):
    """Typed task declaration for a bounded source window sufficient for evidence."""

    file_path: str


class ToolInputMetadata(MetadataBase):
    """Strict tool input payload used instead of free-form params."""

    kind: Literal[MetadataKind.TOOL_INPUT] = MetadataKind.TOOL_INPUT
    tool_name: str = ""

    # File/text/code fields
    file_path: str | None = None
    file_paths: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    directory_path: str | None = None
    pattern: str | None = None
    content: str | None = None
    text: str | None = None
    code: str | None = None
    language: str | None = None
    encoding: str | None = None
    max_size_mb: int | float | None = None
    read_mode: FileReadMode | None = None
    max_lines: int | None = None
    offset: int | None = None
    max_total_chars: int | None = None
    recursive: bool | None = None
    max_files: int | None = None
    create_dirs: bool | None = None
    overwrite: bool | None = None
    operation_kind: str | None = None
    target_scope: str | None = None
    symbol_name: str | None = None
    symbol_type: str | None = None
    insertion_hint: str | None = None
    patch_mode: str | None = None
    generated_unit: str | None = None
    # Provider-native code artifact handoff. This is lineage only; it never
    # authorizes a write or widens the task scope.
    artifact_ref: dict[str, JsonValue] | None = None
    replacement_text: str | None = None
    patch: dict[str, JsonValue] | None = None
    line_start: int | None = None
    line_end: int | None = None

    # LLM/code/search/command fields
    task_description: str | None = None
    task: str | None = None
    task_type: str | None = None
    context: str | None = None
    prompt_context: dict[str, JsonValue] = Field(default_factory=dict)
    instruction: str | None = None
    max_tokens: int | None = None
    query: str | None = None
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    use_cache: bool | None = None
    max_results: int | None = None
    max_pages: int | None = None
    max_page_chars: int | None = None
    max_search_attempts: int | None = None
    search_budget_seconds: int | None = None
    max_redirect_depth: int | None = None
    max_redirect_pages: int | None = None
    max_redirect_candidates: int | None = None
    time_range: str | None = None
    safe_search: str | None = None
    follow_redirects: bool | None = None
    llm_cleanup: bool | None = None
    cleanup_instruction: str | None = None
    command: str | None = None
    requested_command: str | None = None
    effective_interpreter: str | None = None
    environment_id: str | None = None
    mode: str | None = None
    timeout: int | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    stdout: str | None = None
    stderr: str | None = None
    warnings: list[str] = Field(default_factory=list)
    warning_check_required: bool | None = None
    warning_check_result: dict[str, JsonValue] | None = None
    max_iterations: int | None = None
    continuation_iterations: int | None = None
    fix_instruction: str | None = None

    # Project/autonomous iteration fields
    project_path: str | None = None
    project_summary: str | None = None
    goal: str | None = None
    written_files: list[str] = Field(default_factory=list)
    entry_files: list[str] = Field(default_factory=list)
    run_command: str | None = None
    env_name: str | None = None
    environment_operation: EnvironmentOperation | None = None
    install: bool | None = None
    readme_path: str | None = None
    memory_query: str | None = None
    validation_context: dict[str, JsonValue] = Field(default_factory=dict)
    validation_result: dict[str, JsonValue] = Field(default_factory=dict)
    stack_preset_update: dict[str, JsonValue] = Field(default_factory=dict)
    memory_context: dict[str, JsonValue] = Field(default_factory=dict)
    # Digest of the authoritative session-turn ledger used to derive any
    # model-facing dialog candidates. Raw ingress remains a runtime handle.
    session_turn_source_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    iteration: int | None = None
    include_environment: bool | None = None
    limit: int | None = None
    system_prompt: str | None = None
    environment: str | dict[str, JsonValue] | None = None
    freshness: str | None = None
    requires_user_input: bool | None = None
    risk_level: str | None = None
    setup_commands: list[str] = Field(default_factory=list)
    step_id: str | None = None
    tbs: str | None = None
    test_command: str | None = None

    # Test/service injection lives outside model-harness protocol.
    runtime_handles: dict[str, Any] = Field(default_factory=dict, exclude=True)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @classmethod
    def from_mapping(cls, tool_name: str, values: dict[str, Any]) -> "ToolInputMetadata":
        runtime_handles = {key: value for key, value in values.items() if str(key).startswith("_")}
        public_values = {key: value for key, value in values.items() if not str(key).startswith("_")}
        public_values = cls._normalize_input_aliases(public_values)
        public_values.pop("tool_name", None)

        known_fields = set(cls.model_fields)
        known_values = {key: value for key, value in public_values.items() if key in known_fields}
        extra_values = {str(key): value for key, value in public_values.items() if key not in known_fields}

        if extra_values:
            attributes = known_values.get("attributes") if isinstance(known_values.get("attributes"), dict) else {}
            known_values["attributes"] = {**attributes, **extra_values}
        return cls(tool_name=tool_name, runtime_handles=runtime_handles, **known_values)

    @staticmethod
    def _normalize_input_aliases(values: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(values)
        aliases = {
            "create_intermediate": "create_dirs",
            "create_intermediates": "create_dirs",
            "parents": "create_dirs",
        }
        for source, target in aliases.items():
            if source in normalized:
                normalized.setdefault(target, normalized[source])
                normalized.pop(source, None)
        return normalized

    def to_params(self) -> dict[str, Any]:
        """Return public tool fields plus runtime handles for implementation internals."""
        data = self.model_dump(exclude_none=True, exclude={"kind", "schema_version", "source", "correlation", "created_at", "annotations", "runtime_handles"})
        data.pop("tool_name", None)
        attributes = data.pop("attributes", None)
        data = {key: value for key, value in data.items() if value not in ({}, [])}
        if isinstance(attributes, dict):
            for key, value in attributes.items():
                data.setdefault(str(key), value)
        data.update(self.runtime_handles)
        return data


class ToolSelectionMetadata(MetadataBase):
    kind: Literal[MetadataKind.TOOL_SELECTION] = MetadataKind.TOOL_SELECTION
    step_id: str
    tool_name: str
    reason: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    input_metadata: ToolInputMetadata
    requires_confirmation: bool = False
    timeout_override: int | None = None
    fallback_tools: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class ToolContractMetadata(MetadataBase):
    kind: Literal[MetadataKind.TOOL_CONTRACT] = MetadataKind.TOOL_CONTRACT
    tool_name: str
    input_metadata_type: str
    output_metadata_type: str
    required_input_fields: list[str] = Field(default_factory=list)
    required_any_of: list[list[str]] = Field(default_factory=list)
    conditional_requirements: list[dict[str, JsonValue]] = Field(default_factory=list)
    input_defaults: dict[str, JsonValue] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    permission_level: str = "medium"


class ToolChainMetadata(MetadataBase):
    kind: Literal[MetadataKind.TOOL_CHAIN] = MetadataKind.TOOL_CHAIN
    tool_results: list[ToolResultMetadata] = Field(default_factory=list)
    final_result: ToolResultMetadata | None = None


class ToolContextMetadata(MetadataBase):
    """Runtime context attached to a tool event without changing tool inputs."""

    kind: Literal[MetadataKind.TOOL_CONTEXT] = MetadataKind.TOOL_CONTEXT
    session_id: str = ""
    task_id: str = ""
    step_id: str = ""
    call_id: str = ""
    project_path: str = ""
    cwd: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    python_command: str = ""
    pip_command: str = ""
    git_snapshot: dict[str, JsonValue] | None = None
    permission_required: bool = False
    safety_notes: list[str] = Field(default_factory=list)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class ToolCallMetadata(MetadataBase):
    """One requested tool call inside a typed tool event loop."""

    kind: Literal[MetadataKind.TOOL_CALL] = MetadataKind.TOOL_CALL
    session_id: str
    task_id: str
    step_id: str
    call_id: str
    # External provider identity is retained separately from the project-owned
    # lifecycle call_id. It is a wire correlation value, never an authority
    # for permissions, checkpoints, or budget accounting.
    provider_call_id: str | None = Field(default=None, min_length=1)
    tool_name: str
    input_metadata: ToolInputMetadata
    tool_context: ToolContextMetadata | None = None
    status: str = "pending"
    reason: str = ""
    provider_executed: bool = False
    recoverable: bool = True
    round_index: int = 1
    event_index: int = 0


class ToolErrorMetadata(MetadataBase):
    """Recoverable or terminal tool protocol/execution error."""

    kind: Literal[MetadataKind.TOOL_ERROR] = MetadataKind.TOOL_ERROR
    session_id: str
    task_id: str
    step_id: str
    call_id: str
    provider_call_id: str | None = Field(default=None, min_length=1)
    tool_name: str
    error_type: str
    error_message: str
    recoverable: bool = True
    suggested_recovery: str = ""
    failure: FailureMetadata | None = None
    input_metadata: ToolInputMetadata | None = None
    tool_context: ToolContextMetadata | None = None
    provider_executed: bool = False
    round_index: int = 1
    event_index: int = 0


class ToolEventMetadata(MetadataBase):
    """Lifecycle event for a tool call in the event loop."""

    kind: Literal[MetadataKind.TOOL_EVENT] = MetadataKind.TOOL_EVENT
    session_id: str
    task_id: str
    step_id: str
    call_id: str
    tool_name: str
    event_type: str
    status: str
    input_metadata: ToolInputMetadata | None = None
    output_metadata: ToolResultMetadata | None = None
    tool_context: ToolContextMetadata | None = None
    tool_call: ToolCallMetadata | None = None
    tool_error: ToolErrorMetadata | None = None
    failure: FailureMetadata | None = None
    recoverable: bool = True
    provider_executed: bool = False
    round_index: int = 1
    event_index: int = 0


class ToolLoopMetadata(MetadataBase):
    """Complete typed event-loop trace for one task."""

    kind: Literal[MetadataKind.TOOL_LOOP] = MetadataKind.TOOL_LOOP
    session_id: str
    task_id: str
    status: str
    success: bool
    rounds_used: int = 0
    max_rounds: int = 5
    events: list[ToolEventMetadata] = Field(default_factory=list)
    tool_invocations: list[ToolCallMetadata] = Field(default_factory=list)
    recoverable_errors: list[ToolErrorMetadata] = Field(default_factory=list)
    tool_contexts: list[ToolContextMetadata] = Field(default_factory=list)
    # Explicit control-flow counters make recovery evidence auditable by
    # downstream receipts.  They are derived by the runner, never inferred
    # from the number of successful provider responses.
    retry_count: int = Field(default=0, ge=0)
    fallback_count: int = Field(default=0, ge=0)
    final_output: ToolResultMetadata | None = None
    final_error: FailureMetadata | None = None
    provider_executed: bool = False


def artifact_to_tool_input(tool_name: str, artifact: Any) -> ToolInputMetadata:
    """Route a previous artifact into the next tool's input by metadata kind."""
    if isinstance(artifact, ToolResultMetadata):
        artifact = artifact.result
    if isinstance(artifact, CodeArtifactMetadata):
        code = artifact.code or artifact.content
        attrs = artifact.attributes if isinstance(artifact.attributes, dict) else {}
        if tool_name == "file_patch_writer":
            if attrs.get("patch") or attrs.get("replacement_text"):
                return ToolInputMetadata(
                    tool_name=tool_name,
                    patch=attrs.get("patch") if isinstance(attrs.get("patch"), dict) else None,
                    replacement_text=str(attrs.get("replacement_text") or code),
                    operation_kind=str(attrs.get("operation_kind") or "modify_symbol"),
                    symbol_name=str(attrs.get("symbol_name") or "") or None,
                    symbol_type=str(attrs.get("symbol_type") or "") or None,
                    line_start=attrs.get("line_start") if isinstance(attrs.get("line_start"), int) else None,
                    line_end=attrs.get("line_end") if isinstance(attrs.get("line_end"), int) else None,
                )
            return ToolInputMetadata(
                tool_name=tool_name,
                generated_unit=code,
                operation_kind=str(attrs.get("operation_kind") or "add_symbol"),
                symbol_name=str(attrs.get("symbol_name") or "") or None,
                symbol_type=str(attrs.get("symbol_type") or "") or None,
                insertion_hint=str(attrs.get("insertion_hint") or "") or None,
            )
        if tool_name in {"code_executor", "code_reviewer"}:
            return ToolInputMetadata(tool_name=tool_name, code=code, language=artifact.language)
        return ToolInputMetadata(tool_name=tool_name, content=code)
    if isinstance(artifact, FileArtifactMetadata):
        return ToolInputMetadata(tool_name=tool_name, file_path=artifact.file_path, content=artifact.content, text=artifact.content)
    if isinstance(artifact, TextArtifactMetadata):
        return ToolInputMetadata(tool_name=tool_name, content=artifact.content, text=artifact.content)
    if isinstance(artifact, CommandArtifactMetadata):
        return ToolInputMetadata(tool_name=tool_name, text=artifact.stdout or artifact.stderr)
    if isinstance(artifact, SearchArtifactMetadata):
        return ToolInputMetadata(tool_name=tool_name, text=artifact.research_summary)
    if isinstance(artifact, EmbeddingArtifactMetadata):
        return ToolInputMetadata(tool_name=tool_name, query=artifact.query)
    if isinstance(artifact, dict):
        if isinstance(artifact.get("code"), str):
            if tool_name in {"code_executor", "code_reviewer"}:
                return ToolInputMetadata(tool_name=tool_name, code=artifact.get("code"), language=artifact.get("language"))
            return ToolInputMetadata(
                tool_name=tool_name,
                content=artifact.get("code"),
            )
        if isinstance(artifact.get("content"), str):
            return ToolInputMetadata(tool_name=tool_name, content=artifact.get("content"), text=artifact.get("content"))
        return ToolInputMetadata.from_mapping(tool_name, artifact)
    return ToolInputMetadata(tool_name=tool_name, text=str(artifact) if artifact is not None else None)
