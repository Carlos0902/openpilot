"""Strict runtime contracts for provider-native tool round trips."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_PROVIDER_ROUND_TRIP_ROUNDS = 32
MAX_PROVIDER_TOOL_ATTEMPTS = 1024
MAX_PROVIDER_EVIDENCE_PATHS = 64

ProviderPath = Annotated[str, Field(min_length=1, max_length=4096)]
ProviderEvidenceKey = Annotated[str, Field(min_length=1, max_length=512)]


class ProviderDeclaredReadWindow(BaseModel):
    """One exact bounded read window completed during a round trip."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    file_path: ProviderPath
    read_mode: str = Field(min_length=1, max_length=64)
    offset: int = Field(ge=0)
    max_lines: int = Field(ge=1, le=100_000)


class ProviderPageReadCount(BaseModel):
    """Bounded page-read count for one canonical source path."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    file_path: ProviderPath
    count: int = Field(ge=1, le=64)


class ProviderToolAttempt(BaseModel):
    """Bounded outcome evidence for one normalized provider tool attempt."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_name: str = Field(min_length=1, max_length=128)
    provider_call_id: str = Field(min_length=1, max_length=256)
    round_index: int = Field(ge=1, le=MAX_PROVIDER_ROUND_TRIP_ROUNDS)
    success: bool
    error_type: str | None = Field(default=None, min_length=1, max_length=128)
    duplicate_of: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def _outcome_facts_are_consistent(self) -> "ProviderToolAttempt":
        if self.success and self.error_type is not None:
            raise ValueError("successful attempts cannot carry an error type")
        if not self.success and self.error_type is None:
            raise ValueError("failed attempts require an error type")
        if self.duplicate_of is not None:
            if self.success:
                raise ValueError("duplicate attempts cannot be successful")
            if self.duplicate_of == self.provider_call_id:
                raise ValueError("duplicate attempts must reference an earlier call")
        return self


class ProviderToolEvidenceCoverage(BaseModel):
    """Bounded evidence collected across one provider tool round trip."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    completed_read_paths: tuple[ProviderPath, ...] = Field(
        default=(),
        max_length=MAX_PROVIDER_EVIDENCE_PATHS,
    )
    completed_declared_windows: tuple[ProviderDeclaredReadWindow, ...] = Field(
        default=(),
        max_length=MAX_PROVIDER_EVIDENCE_PATHS,
    )
    bounded_projection_paths: tuple[ProviderPath, ...] = Field(
        default=(),
        max_length=MAX_PROVIDER_EVIDENCE_PATHS,
    )
    page_reads_by_path: tuple[ProviderPageReadCount, ...] = Field(
        default=(),
        max_length=MAX_PROVIDER_EVIDENCE_PATHS,
    )
    page_cap_paths: tuple[ProviderPath, ...] = Field(
        default=(),
        max_length=MAX_PROVIDER_EVIDENCE_PATHS,
    )
    page_read_cap: int = Field(default=0, ge=0, le=64)
    observed_evidence_keys: tuple[ProviderEvidenceKey, ...] = Field(
        default=(),
        max_length=MAX_PROVIDER_TOOL_ATTEMPTS,
    )
    duplicate_only_rounds: int = Field(
        default=0,
        ge=0,
        le=MAX_PROVIDER_ROUND_TRIP_ROUNDS,
    )
    finalization_requests: int = Field(
        default=0,
        ge=0,
        le=MAX_PROVIDER_ROUND_TRIP_ROUNDS,
    )

    @model_validator(mode="after")
    def _coverage_sets_are_consistent(self) -> "ProviderToolEvidenceCoverage":
        _require_unique(self.completed_read_paths, "completed read paths")
        _require_unique(
            self.bounded_projection_paths,
            "bounded projection paths",
        )
        _require_unique(self.page_cap_paths, "page-cap paths")
        _require_unique(
            self.observed_evidence_keys,
            "observed evidence keys",
        )
        window_keys = [
            (
                item.file_path,
                item.read_mode,
                item.offset,
                item.max_lines,
            )
            for item in self.completed_declared_windows
        ]
        _require_unique(window_keys, "completed declared windows")
        count_paths = [item.file_path for item in self.page_reads_by_path]
        _require_unique(count_paths, "page-read paths")
        counts = {item.file_path: item.count for item in self.page_reads_by_path}
        for path in self.page_cap_paths:
            if self.page_read_cap < 1 or counts.get(path, 0) < self.page_read_cap:
                raise ValueError(
                    "page-cap paths require an observed count at or above the cap"
                )
        return self


def _require_unique(values, label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


__all__ = [
    "MAX_PROVIDER_EVIDENCE_PATHS",
    "MAX_PROVIDER_ROUND_TRIP_ROUNDS",
    "MAX_PROVIDER_TOOL_ATTEMPTS",
    "ProviderDeclaredReadWindow",
    "ProviderPageReadCount",
    "ProviderToolAttempt",
    "ProviderToolEvidenceCoverage",
]
