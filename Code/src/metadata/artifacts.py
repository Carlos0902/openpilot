"""Artifact metadata payloads exchanged by tools and agents."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from metadata.base import JsonValue, MetadataBase, MetadataKind


class FileReadMode(str, Enum):
    """Typed file-reader modes used by routing and evidence accounting."""

    FULL = "full"
    ADAPTIVE = "adaptive"
    SAMPLE = "sample"
    TAIL = "tail"
    RANGE = "range"
    OFFSET = "offset"


class FileReadWindow(BaseModel):
    """An explicit bounded file-read window carried with one artifact."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    read_mode: FileReadMode
    offset: int = Field(default=0, ge=0)
    max_lines: int = Field(gt=0)


class TextArtifactMetadata(MetadataBase):
    kind: Literal[MetadataKind.TEXT_ARTIFACT] = MetadataKind.TEXT_ARTIFACT
    content: str = ""
    encoding: str = "utf-8"
    title: str = ""
    content_type: str = "text/plain"
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class CodeArtifactMetadata(TextArtifactMetadata):
    kind: Literal[MetadataKind.CODE_ARTIFACT] = MetadataKind.CODE_ARTIFACT
    code: str = ""
    language: str = "python"
    imports: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if not self.code and self.content:
            self.code = self.content
        if not self.content and self.code:
            self.content = self.code


class FileArtifactMetadata(TextArtifactMetadata):
    kind: Literal[MetadataKind.FILE_ARTIFACT] = MetadataKind.FILE_ARTIFACT
    file_path: str = ""
    files: list[str] = Field(default_factory=list)
    size_bytes: int | None = None
    bytes_written: int | None = None
    created: bool | None = None
    file_type: str = ""
    lines_read: int | None = None
    total_lines: int | None = None
    truncated: bool = False
    read_window: FileReadWindow | None = None


class CommandArtifactMetadata(MetadataBase):
    kind: Literal[MetadataKind.COMMAND_ARTIFACT] = MetadataKind.COMMAND_ARTIFACT
    command: str = ""
    success: bool = False
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration: float = 0.0
    risk_assessment: dict[str, JsonValue] | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class SearchArtifactMetadata(MetadataBase):
    kind: Literal[MetadataKind.SEARCH_ARTIFACT] = MetadataKind.SEARCH_ARTIFACT
    query: str = ""
    provider: str = ""
    effective_query: str = ""
    results: list[dict[str, JsonValue]] = Field(default_factory=list)
    count: int = 0
    pages: list[dict[str, JsonValue]] = Field(default_factory=list)
    research_summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    source_notes: list[JsonValue] = Field(default_factory=list)
    follow_up_queries: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class EmbeddingArtifactMetadata(MetadataBase):
    kind: Literal[MetadataKind.EMBEDDING_ARTIFACT] = MetadataKind.EMBEDDING_ARTIFACT
    query: str = ""
    embedding: list[float] = Field(default_factory=list)
    dimension: int = 0
    model: str = ""
    provider: str = ""
    cached: bool = False
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
