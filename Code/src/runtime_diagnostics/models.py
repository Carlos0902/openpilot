"""Local persistence models for runtime diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class RunRecord(BaseModel):
    """Durable header for one task run."""

    run_id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str = ""
    session_id: str = ""
    source: str = ""
    raw_input: str = ""
    goal: str = ""
    route: str = ""
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    final_status: str = "running"
    completion_reason: str = ""
    success: bool | None = None


class EventRecord(BaseModel):
    """Chronological event within one run trajectory."""

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    sequence: int
    event_type: str
    idempotency_key: str = ""
    task_id: str = ""
    session_id: str = ""
    phase: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    summary: str = ""
    payload_kind: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class ArtifactRecord(BaseModel):
    """Large payload retained outside the main event stream."""

    artifact_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    kind: str
    path: str
    content_type: str = "text/plain"
    bytes: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    source_event_id: str = ""


class RunSummaryRecord(BaseModel):
    """Lightweight per-run summary for quick human review."""

    run_id: str
    task_id: str = ""
    session_id: str = ""
    source: str = ""
    route: str = ""
    goal: str = ""
    raw_input_preview: str = ""
    started_at: str = ""
    finished_at: str | None = None
    success: bool | None = None
    final_status: str = "running"
    completion_reason: str = ""
    event_count: int = 0
    tool_called_count: int = 0
    tool_succeeded_count: int = 0
    tool_failed_count: int = 0
    verification_state_changes: int = 0
    phase_changes: int = 0
    artifact_count: int = 0
    last_phase: str = ""
    verification_status: str = ""


class DiagnosticRecord(BaseModel):
    """One persisted diagnostic record.

    The canonical evidence remains the embedded ProblemSignalMetadata and
    ProblemJudgmentMetadata JSON payloads. This wrapper only adds persistence
    metadata for JSONL storage.
    """

    record_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    task_id: str = ""
    signal: dict[str, Any]
    judgment: dict[str, Any] | None = None

    @property
    def category(self) -> str:
        return str(self.signal.get("category") or "unknown")

    @property
    def severity(self) -> str:
        if not self.judgment:
            return "unjudged"
        return str(self.judgment.get("severity") or "unknown")
