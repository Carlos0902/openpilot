"""Configuration loading for OpenAI-compatible LLM and embedding providers."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.exceptions import MissingAPIKeyError
from metadata import ReasoningCapabilityProfileId, ReasoningMode


_CODE_ROOT = Path(__file__).resolve().parents[2]
_REPOSITORY_ROOT = _CODE_ROOT.parent
_ENV_FILES = (str(_REPOSITORY_ROOT / ".env"), str(_CODE_ROOT / ".env"), ".env")


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
