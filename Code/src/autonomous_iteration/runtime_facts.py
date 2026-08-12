"""Read-only projection of authoritative runtime and configuration facts."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from core.config import LLMSettings
from metadata import (
    CheckpointStatus,
    ProjectImprovementPolicy,
    RuntimeExecutionMode,
)


class RuntimeFactProjection(BaseModel):
    """Secret-free facts resolved from existing runtime owners.

    This projection does not grant authority and is not a completion outcome.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str
    project_path: str | None = None
    execution_mode: RuntimeExecutionMode | None = None
    checkpoint_status: CheckpointStatus = CheckpointStatus.DISABLED
    project_improvement_policy: ProjectImprovementPolicy | None = None
    configuration_complete: bool
    missing_configuration_fields: tuple[str, ...] = ()


class RuntimeFactResolver:
    """Resolve a deterministic, read-only fact projection without answering."""

    def __init__(self, settings: LLMSettings):
        if not isinstance(settings, LLMSettings):
            raise TypeError("RuntimeFactResolver requires validated LLMSettings")
        self._settings = settings

    def resolve(
        self,
        *,
        project_path: str | Path | None = None,
        execution_mode: RuntimeExecutionMode | None = None,
        checkpoint_status: CheckpointStatus = CheckpointStatus.DISABLED,
        project_improvement_policy: ProjectImprovementPolicy | None = None,
    ) -> RuntimeFactProjection:
        resolved_project_path = None
        if project_path is not None:
            raw_path = str(project_path).strip()
            if not raw_path:
                raise ValueError("project_path must be non-empty when provided")
            candidate_path = Path(raw_path).expanduser()
            if not candidate_path.is_absolute():
                raise ValueError("project_path must be absolute when provided")
            resolved_project_path = str(candidate_path.resolve(strict=False))

        missing = tuple(self._settings.missing_fields())
        return RuntimeFactProjection(
            provider=self._settings.provider,
            model=self._settings.model,
            project_path=resolved_project_path,
            execution_mode=execution_mode,
            checkpoint_status=checkpoint_status,
            project_improvement_policy=(
                project_improvement_policy.model_copy(deep=True)
                if project_improvement_policy is not None
                else None
            ),
            configuration_complete=not missing,
            missing_configuration_fields=missing,
        )
