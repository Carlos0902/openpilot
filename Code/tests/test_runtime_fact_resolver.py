from pathlib import Path

import pytest

from autonomous_iteration.runtime_facts import RuntimeFactResolver
from core.config import LLMSettings
from metadata import (
    CheckpointStatus,
    ProjectImprovementPolicy,
    ProjectImprovementRequirement,
    RuntimeExecutionMode,
)


def test_runtime_fact_resolver_projects_authoritative_settings_without_secrets(tmp_path: Path) -> None:
    settings = LLMSettings(
        OPENPILOT_LLM_PROVIDER="openai-compatible",
        OPENPILOT_LLM_BASE_URL="https://example.invalid/v1",
        OPENPILOT_LLM_API_KEY="secret-value",
        OPENPILOT_LLM_MODEL="test-model",
    )
    policy = ProjectImprovementPolicy(
        requirement=ProjectImprovementRequirement.DISABLED,
        target_successes=0,
        max_attempts=0,
    )

    facts = RuntimeFactResolver(settings).resolve(
        project_path=tmp_path,
        execution_mode=RuntimeExecutionMode.READ_ONLY,
        checkpoint_status=CheckpointStatus.DURABLE,
        project_improvement_policy=policy,
    )

    assert facts.provider == "openai-compatible"
    assert facts.model == "test-model"
    assert facts.project_path == str(tmp_path.resolve())
    assert facts.execution_mode == RuntimeExecutionMode.READ_ONLY
    assert facts.checkpoint_status == CheckpointStatus.DURABLE
    assert facts.project_improvement_policy == policy
    assert facts.configuration_complete is True
    assert facts.missing_configuration_fields == ()
    assert "secret-value" not in facts.model_dump_json()


def test_runtime_fact_resolver_reports_missing_configuration_without_answering() -> None:
    settings = LLMSettings(
        OPENPILOT_LLM_BASE_URL="",
        OPENPILOT_LLM_API_KEY=None,
    )

    facts = RuntimeFactResolver(settings).resolve()

    assert facts.configuration_complete is False
    assert facts.missing_configuration_fields == (
        "OPENPILOT_LLM_BASE_URL",
        "OPENPILOT_LLM_API_KEY",
    )
    assert facts.project_path is None
    assert facts.execution_mode is None
    assert facts.checkpoint_status == CheckpointStatus.DISABLED
    assert facts.project_improvement_policy is None


def test_runtime_fact_resolver_rejects_ambiguous_or_unvalidated_inputs() -> None:
    with pytest.raises(TypeError, match="validated LLMSettings"):
        RuntimeFactResolver(object())  # type: ignore[arg-type]

    resolver = RuntimeFactResolver(LLMSettings())
    with pytest.raises(ValueError, match="absolute"):
        resolver.resolve(project_path="relative/project")
