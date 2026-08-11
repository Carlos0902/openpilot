from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.provider_tool_admission import (
    ProviderValidationCommandDecision,
    provider_validation_command_decision,
)
from metadata import ToolInputMetadata


def _input(**updates) -> ToolInputMetadata:
    values = {
        "tool_name": "command_executor",
        "command": "python -m compileall -q src",
    }
    values.update(updates)
    return ToolInputMetadata(**values)


def test_validation_command_admits_exact_argv_and_binds_defaults(tmp_path) -> None:
    decision = provider_validation_command_decision(
        _input(command="python   -m compileall -q src"),
        validation_command="python -m compileall -q src",
        validation_cwd=str(tmp_path),
        validation_commands_used=0,
    )

    assert decision == ProviderValidationCommandDecision(
        status="admitted",
        reason_code="exact_match",
        effective_mode="automatic",
        effective_cwd=str(tmp_path.resolve()),
    )


@pytest.mark.parametrize(
    ("input_updates", "expected", "cwd", "used", "reason_code"),
    [
        ({}, "python -m compileall -q src", None, 1, "duplicate_validation"),
        ({"command": "pytest -q"}, "python -m compileall -q src", None, 0, "command_mismatch"),
        ({"mode": "dry_run"}, "python -m compileall -q src", None, 0, "invalid_mode"),
        ({"cwd": "/tmp/other"}, "python -m compileall -q src", "/tmp/project", 0, "cwd_mismatch"),
        ({"cwd": "/tmp/other"}, "python -m compileall -q src", None, 0, "unexpected_cwd"),
    ],
)
def test_validation_command_reports_typed_rejection(
    input_updates, expected, cwd, used, reason_code
) -> None:
    decision = provider_validation_command_decision(
        _input(**input_updates),
        validation_command=expected,
        validation_cwd=cwd,
        validation_commands_used=used,
    )

    assert decision.status == "blocked"
    assert decision.reason_code == reason_code
    assert decision.effective_mode is None
    assert decision.effective_cwd is None


@pytest.mark.parametrize(
    "command",
    [
        "sh -c 'python -m compileall -q src'",
        "python -m compileall -q src && echo done",
        "python -m compileall -q src | tee output.log",
        "python -m compileall -q src > output.log",
        "python -m compileall -q src $(whoami)",
        "python -m compileall -q 'src",
    ],
)
def test_validation_command_rejects_shell_widening(command) -> None:
    decision = provider_validation_command_decision(
        _input(command=command),
        validation_command="python -m compileall -q src",
        validation_cwd=None,
        validation_commands_used=0,
    )

    assert decision.reason_code == "command_mismatch"


def test_validation_decision_rejects_contradictory_state() -> None:
    with pytest.raises(ValidationError):
        ProviderValidationCommandDecision(
            status="admitted",
            reason_code="command_mismatch",
            effective_mode="automatic",
        )


def test_validation_usage_rejects_negative_value() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        provider_validation_command_decision(
            _input(),
            validation_command="python -m compileall -q src",
            validation_cwd=None,
            validation_commands_used=-1,
        )
