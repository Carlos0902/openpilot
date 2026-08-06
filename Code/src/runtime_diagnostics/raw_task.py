"""Raw task-pool input for real-task diagnostics.

RawTaskInput is intentionally not a strict MetadataBase contract. It is the
small external wrapper used by task pools before OpenPilot's existing task
understanding turns user input into TaskCard / route / runtime state objects.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class RawTaskInput(BaseModel):
    """Minimal external task input wrapper.

    This model represents benchmark/manual task-pool rows. It should stay small
    because real user input is often incomplete.
    """

    task_id: str
    source: str = "manual"
    raw_input: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_id", "raw_input")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    def to_task_payload(self) -> dict[str, Any]:
        """Return a plain payload suitable for existing task entry points."""
        return {
            "task_id": self.task_id,
            "source": self.source,
            "goal": self.raw_input,
            "attachments": self.attachments,
            "tags": self.tags,
            "context": self.context,
        }
