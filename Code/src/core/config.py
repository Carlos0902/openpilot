"""Configuration loading for OpenAI-compatible LLM and embedding providers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.exceptions import MissingAPIKeyError
from metadata import ReasoningCapabilityProfileId, ReasoningMode


_CODE_ROOT = Path(__file__).resolve().parents[2]
_REPOSITORY_ROOT = _CODE_ROOT.parent


def _discover_shared_repository_root(repository_root: Path) -> Path:
    """Return the main checkout owning a linked Git worktree, if present."""

    repository_root = repository_root.expanduser().resolve(strict=False)
    git_marker = repository_root / ".git"
    if git_marker.is_dir() or not git_marker.is_file():
        return repository_root
    try:
        marker = git_marker.read_text(encoding="utf-8").strip()
    except OSError:
        return repository_root
    prefix = "gitdir:"
    if not marker.casefold().startswith(prefix):
        return repository_root
    raw_git_dir = marker[len(prefix):].strip()
    if not raw_git_dir:
        return repository_root
    git_dir = Path(raw_git_dir).expanduser()
    if not git_dir.is_absolute():
        git_dir = git_marker.parent / git_dir
    git_dir = git_dir.resolve(strict=False)
    common_dir_file = git_dir / "commondir"
    try:
        raw_common_dir = common_dir_file.read_text(encoding="utf-8").strip()
    except OSError:
        return repository_root
    if not raw_common_dir:
        return repository_root
    common_dir = Path(raw_common_dir).expanduser()
    if not common_dir.is_absolute():
        common_dir = git_dir / common_dir
    common_dir = common_dir.resolve(strict=False)
    return common_dir.parent if common_dir.name == ".git" else repository_root


_SHARED_REPOSITORY_ROOT = _discover_shared_repository_root(_REPOSITORY_ROOT)
_ENV_FILES = tuple(
    dict.fromkeys(
        (
            str(_SHARED_REPOSITORY_ROOT / ".env"),
            str(_REPOSITORY_ROOT / ".env"),
            str(_CODE_ROOT / ".env"),
            ".env",
        )
    )
)


class ProviderToolExecutionBudgetProfile(str, Enum):
    """Explicit budget lanes for provider-native task experiments."""

    CANARY = "canary"
    REAL_READ_ONLY = "real_read_only"
    REAL_MUTATION = "real_mutation"


@dataclass(frozen=True)
class ProviderToolExecutionBudget:
    """Static ceilings used by the provider-native execution entry point."""

    profile: ProviderToolExecutionBudgetProfile
    context_max_prompt_tokens: int
    completion_ceiling: int
    completion_floor: int
    total_completion_tokens: int
    max_rounds: int
    max_tool_calls: int
    max_file_reads: int
    max_file_edits: int
    max_file_creates: int
    max_verification_attempts: int

    @classmethod
    def for_profile(
        cls,
        profile: ProviderToolExecutionBudgetProfile | str,
    ) -> "ProviderToolExecutionBudget":
        normalized = ProviderToolExecutionBudgetProfile(profile)
        if normalized is ProviderToolExecutionBudgetProfile.REAL_READ_ONLY:
            return cls(
                profile=normalized,
                context_max_prompt_tokens=12_288,
                completion_ceiling=4_096,
                completion_floor=2_048,
                total_completion_tokens=24_000,
                max_rounds=8,
                max_tool_calls=40,
                max_file_reads=60,
                max_file_edits=0,
                max_file_creates=0,
                max_verification_attempts=0,
            )
        if normalized is ProviderToolExecutionBudgetProfile.REAL_MUTATION:
            return cls(
                profile=normalized,
                context_max_prompt_tokens=12_288,
                completion_ceiling=4_096,
                completion_floor=2_048,
                total_completion_tokens=24_000,
                max_rounds=8,
                max_tool_calls=40,
                max_file_reads=60,
                max_file_edits=1,
                max_file_creates=0,
                max_verification_attempts=1,
            )
        return cls(
            profile=ProviderToolExecutionBudgetProfile.CANARY,
            context_max_prompt_tokens=4_096,
            completion_ceiling=1_600,
            completion_floor=800,
            total_completion_tokens=12_000,
            max_rounds=3,
            max_tool_calls=20,
            max_file_reads=30,
            max_file_edits=3,
            max_file_creates=20,
            max_verification_attempts=3,
        )


class LLMSettings(BaseSettings):
    """Runtime settings for an OpenAI-compatible chat completion endpoint."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    provider: str = Field(default="openai-compatible", alias="OPENPILOT_LLM_PROVIDER")
    base_url: str = Field(default="https://api.openai.com/v1", alias="OPENPILOT_LLM_BASE_URL")
    api_key: str | None = Field(default=None, alias="OPENPILOT_LLM_API_KEY")
    model: str = Field(default="gpt-4o-mini", alias="OPENPILOT_LLM_MODEL")
    timeout_seconds: float = Field(default=60.0, alias="OPENPILOT_LLM_TIMEOUT_SECONDS")
    temperature: float = Field(default=0.2, alias="OPENPILOT_LLM_TEMPERATURE")
    transport_retries: int = Field(default=2, alias="OPENPILOT_LLM_TRANSPORT_RETRIES")
    retry_initial_delay: float = Field(default=2.0, alias="OPENPILOT_LLM_RETRY_INITIAL_DELAY")
    retry_max_delay: float = Field(default=20.0, alias="OPENPILOT_LLM_RETRY_MAX_DELAY")
    tokenizer_path: str | None = Field(default=None, alias="OPENPILOT_LLM_TOKENIZER_PATH")
    reasoning_capability_profile: ReasoningCapabilityProfileId | None = Field(
        default=None,
        alias="OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE",
    )
    tool_event_reasoning_mode: ReasoningMode = Field(
        default=ReasoningMode.DISABLED,
        alias="OPENPILOT_TOOL_EVENT_REASONING_MODE",
    )
    context_max_prompt_tokens: int = Field(default=4096, gt=0, alias="OPENPILOT_CONTEXT_MAX_PROMPT_TOKENS")
    context_reserved_prompt_tokens: int = Field(
        default=128,
        ge=0,
        alias="OPENPILOT_CONTEXT_RESERVED_PROMPT_TOKENS",
    )
    rolling_summary_enabled: bool = Field(
        default=False,
        alias="OPENPILOT_ROLLING_SUMMARY_ENABLED",
    )
    rolling_summary_token_limit: int = Field(
        default=256,
        gt=0,
        le=4096,
        alias="OPENPILOT_ROLLING_SUMMARY_TOKEN_LIMIT",
    )
    provider_tool_execution_enabled: bool = Field(
        default=False,
        alias="OPENPILOT_PROVIDER_TOOL_EXECUTION_ENABLED",
    )
    provider_tool_initial_context_projection_enabled: bool = Field(
        default=False,
        alias="OPENPILOT_PROVIDER_TOOL_INITIAL_CONTEXT_PROJECTION_ENABLED",
    )
    provider_tool_initial_context_mutation_enabled: bool = Field(
        default=False,
        alias="OPENPILOT_PROVIDER_TOOL_INITIAL_CONTEXT_MUTATION_ENABLED",
    )
    provider_tool_completion_outcome_feedback_enabled: bool = Field(
        default=False,
        alias="OPENPILOT_PROVIDER_TOOL_COMPLETION_OUTCOME_FEEDBACK_ENABLED",
    )
    provider_tool_execution_budget_profile: ProviderToolExecutionBudgetProfile = Field(
        default=ProviderToolExecutionBudgetProfile.CANARY,
        alias="OPENPILOT_PROVIDER_TOOL_EXECUTION_BUDGET_PROFILE",
    )
    provider_tool_execution_max_rounds: int = Field(
        default=3,
        ge=1,
        le=8,
        alias="OPENPILOT_PROVIDER_TOOL_EXECUTION_MAX_ROUNDS",
    )

    @field_validator("tool_event_reasoning_mode")
    @classmethod
    def _routine_reasoning_mode_is_bounded(cls, value: ReasoningMode) -> ReasoningMode:
        if value not in {ReasoningMode.PROVIDER_DEFAULT, ReasoningMode.DISABLED}:
            raise ValueError(
                "tool-event reasoning mode must be provider_default or disabled"
            )
        return value

    def missing_fields(self) -> list[str]:
        """Return required LLM settings that are blank or missing."""

        missing: list[str] = []
        if not self.base_url or not self.base_url.strip():
            missing.append("OPENPILOT_LLM_BASE_URL")
        if not self.api_key or not self.api_key.strip():
            missing.append("OPENPILOT_LLM_API_KEY")
        return missing

    def is_ready(self) -> bool:
        """Return whether settings are complete enough for real LLM calls."""

        return not self.missing_fields()

    def require_ready(self) -> None:
        """Raise if settings are incomplete for a real provider request."""

        missing = self.missing_fields()
        if missing:
            raise MissingAPIKeyError(
                f"Missing LLM configuration: {', '.join(missing)}."
            )


class EmbeddingSettings(BaseSettings):
    """Runtime settings for an OpenAI-compatible embedding endpoint."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    provider: str = Field(default="openai-compatible", alias="OPENPILOT_EMBEDDING_PROVIDER")
    base_url: str | None = Field(default=None, alias="OPENPILOT_EMBEDDING_BASE_URL")
    api_key: str | None = Field(default=None, alias="OPENPILOT_EMBEDDING_API_KEY")
    model: str = Field(default="text-embedding-3-small", alias="OPENPILOT_EMBEDDING_MODEL")
    timeout_seconds: float = Field(default=30.0, alias="OPENPILOT_EMBEDDING_TIMEOUT_SECONDS")

    def __init__(self, **data):
        super().__init__(**data)
        llm_settings = LLMSettings()
        if not self.base_url or not self.base_url.strip():
            self.base_url = llm_settings.base_url
        if not self.api_key or not self.api_key.strip():
            self.api_key = llm_settings.api_key

    def missing_fields(self) -> list[str]:
        """Return required embedding settings that are blank or missing after LLM fallback."""

        missing: list[str] = []
        if not self.base_url or not self.base_url.strip():
            missing.append("OPENPILOT_EMBEDDING_BASE_URL or OPENPILOT_LLM_BASE_URL")
        if not self.api_key or not self.api_key.strip():
            missing.append("OPENPILOT_EMBEDDING_API_KEY or OPENPILOT_LLM_API_KEY")
        return missing

    def is_ready(self) -> bool:
        """Return whether settings are complete enough for real embedding calls."""

        return not self.missing_fields()

    def require_ready(self) -> None:
        """Raise if settings are incomplete for a real embedding provider request."""

        missing = self.missing_fields()
        if missing:
            raise MissingAPIKeyError(
                f"Missing embedding configuration: {', '.join(missing)}."
            )


class ModelHealthSettings(BaseSettings):
    """Startup connectivity probe settings for configured model endpoints."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    enabled: bool = Field(default=True, alias="OPENPILOT_MODEL_HEALTHCHECK_ENABLED")
    timeout_seconds: float = Field(default=5.0, gt=0, le=30, alias="OPENPILOT_MODEL_HEALTHCHECK_TIMEOUT_SECONDS")
