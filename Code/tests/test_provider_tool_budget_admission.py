from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from core.provider_tool_admission import (
    ProviderToolBudgetDecision,
    ProviderToolBudgetUsage,
    ProviderToolResourceUsage,
    provider_tool_budget_decision,
    provider_tool_resource_usage,
)
from core.tool_contracts import ToolCapability
from metadata import RuntimeBudgetMetadata, ToolInputMetadata


def _definition(*capabilities) -> SimpleNamespace:
    return SimpleNamespace(capabilities=list(capabilities))


def test_resource_usage_classifies_read_edit_create_and_validation() -> None:
    read = provider_tool_resource_usage(
        _definition(ToolCapability.FILE_READ),
        ToolInputMetadata(tool_name="file_reader", file_path="README.md"),
    )
    edit = provider_tool_resource_usage(
        _definition(ToolCapability.FILE_WRITE),
        ToolInputMetadata(
            tool_name="file_patch_writer",
            file_path="app.py",
            operation_kind="modify_symbol",
        ),
    )
    create = provider_tool_resource_usage(
        _definition(ToolCapability.FILE_WRITE),
        ToolInputMetadata(
            tool_name="file_writer",
            file_path="new.py",
            operation_kind="create_file",
        ),
    )
    validation = provider_tool_resource_usage(
        _definition(ToolCapability.SHELL_EXECUTION),
        ToolInputMetadata(tool_name="command_executor", command="python -m compileall ."),
        validation_command="python -m compileall .",
    )

    assert read == ProviderToolResourceUsage(reads=1)
    assert edit == ProviderToolResourceUsage(edits=1)
    assert create == ProviderToolResourceUsage(creates=1)
    assert validation == ProviderToolResourceUsage(validation=1)


def test_budget_allows_exact_remaining_boundary() -> None:
    budget = RuntimeBudgetMetadata(
        max_tool_calls=2,
        max_file_reads=2,
        tool_calls_used=1,
        file_reads_used=1,
    )

    decision = provider_tool_budget_decision(
        budget,
        ProviderToolResourceUsage(reads=1),
        prior=ProviderToolBudgetUsage(),
    )

    assert decision.status == "admitted"
    assert decision.reason_code == "within_budget"


@pytest.mark.parametrize(
    ("budget_updates", "usage", "prior", "reason_code"),
    [
        ({"max_tool_calls": 1, "tool_calls_used": 1}, {}, {}, "tool_calls_exhausted"),
        ({"max_file_reads": 1}, {"reads": 1}, {"reads": 1, "calls": 0}, "file_reads_exhausted"),
        ({"max_file_edits": 1}, {"edits": 1}, {"edits": 1, "calls": 0}, "file_edits_exhausted"),
        ({"max_file_creates": 1}, {"creates": 1}, {"creates": 1, "calls": 0}, "file_creates_exhausted"),
        (
            {"max_verification_attempts": 1},
            {"validation": 1},
            {"validation": 1, "calls": 0},
            "validation_exhausted",
        ),
    ],
)
def test_budget_decision_reports_typed_exhaustion(
    budget_updates, usage, prior, reason_code
) -> None:
    budget = RuntimeBudgetMetadata(**budget_updates)

    decision = provider_tool_budget_decision(
        budget,
        ProviderToolResourceUsage(**usage),
        prior=ProviderToolBudgetUsage(**prior),
    )

    assert decision == ProviderToolBudgetDecision(
        status="blocked",
        reason_code=reason_code,
        usage=ProviderToolResourceUsage(**usage),
    )


def test_resource_usage_rejects_negative_or_multi_call_values() -> None:
    with pytest.raises(ValidationError):
        ProviderToolResourceUsage(reads=-1)
    with pytest.raises(ValidationError):
        ProviderToolResourceUsage(calls=2)
    assert ProviderToolBudgetUsage(calls=2).calls == 2


def test_budget_decision_rejects_contradictory_status_and_reason() -> None:
    with pytest.raises(ValidationError):
        ProviderToolBudgetDecision(
            status="admitted",
            reason_code="file_reads_exhausted",
            usage=ProviderToolResourceUsage(reads=1),
        )
