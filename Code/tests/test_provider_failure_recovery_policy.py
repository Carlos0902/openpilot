from __future__ import annotations

import pytest

from core.provider_failure_recovery_policy import (
    ProviderFailureRecoveryAction,
    ProviderFailureRecoveryError,
    provider_failure_recovery_policy,
)
from core.tool_contracts import ToolCapability


def _policy(**overrides):
    values = {
        "failure_type": "RuntimeError",
        "failure_recoverable": True,
        "failed_tools": [],
        "tool_capabilities": {},
        "model_repair_enabled": False,
    }
    values.update(overrides)
    return provider_failure_recovery_policy(**values)


def test_no_failure_does_not_request_recovery() -> None:
    decision = _policy(failure_type=None)

    assert decision.action is ProviderFailureRecoveryAction.NONE
    assert decision.error_code is None


@pytest.mark.parametrize(
    "failure_type",
    [
        "InvalidToolArguments",
        "MissingRequiredInput",
        "MissingRequiredInputGroup",
        "ProviderToolScopeViolation",
        "ToolConfirmationRequired",
        "UserConfirmationRequired",
        "UnknownTool",
    ],
)
def test_recoverable_admission_failure_can_continue(
    failure_type: str,
) -> None:
    decision = _policy(failure_type=failure_type)

    assert decision.action is ProviderFailureRecoveryAction.CONTINUE
    assert decision.error_code is None


def test_model_repairable_protocol_failure_is_separate_action() -> None:
    decision = _policy(
        failure_type="InvalidToolArguments",
        model_repair_enabled=True,
    )

    assert decision.action is ProviderFailureRecoveryAction.REPAIR
    assert decision.error_code is None


def test_nonrecoverable_admission_failure_is_terminal() -> None:
    decision = _policy(
        failure_type="UnknownTool",
        failure_recoverable=False,
    )

    assert decision.action is ProviderFailureRecoveryAction.FAIL
    assert decision.error_code == "ProviderToolAdmissionFailure"


def test_recoverable_read_only_execution_failure_can_continue() -> None:
    decision = _policy(
        failure_type="FileNotFound",
        failed_tools=["file_reader"],
        tool_capabilities={
            "file_reader": [ToolCapability.FILE_READ],
        },
    )

    assert decision.action is ProviderFailureRecoveryAction.CONTINUE


@pytest.mark.parametrize(
    "capabilities",
    [
        [ToolCapability.FILE_WRITE],
        [ToolCapability.FILE_READ, ToolCapability.SHELL_EXECUTION],
        [],
    ],
)
def test_recoverable_unsafe_execution_failure_is_terminal(capabilities) -> None:
    decision = _policy(
        failure_type="RuntimeError",
        failed_tools=["tool"],
        tool_capabilities={"tool": capabilities},
    )

    assert decision.action is ProviderFailureRecoveryAction.FAIL
    assert decision.error_code == "ProviderToolExecutionFailure"


@pytest.mark.parametrize(
    "failure_type",
    [
        "CheckpointPrepareFailed",
        "CheckpointObservationFailed",
        "IndeterminateSideEffect",
        "MutationVerificationFailed",
    ],
)
def test_checkpoint_and_mutation_boundaries_are_terminal(failure_type: str) -> None:
    decision = _policy(
        failure_type=failure_type,
        failed_tools=["file_reader"],
        tool_capabilities={"file_reader": [ToolCapability.FILE_READ]},
    )

    assert decision.action is ProviderFailureRecoveryAction.FAIL


@pytest.mark.parametrize(
    "overrides",
    [
        {"failure_recoverable": "yes"},
        {"failed_tools": "file_reader"},
        {"tool_capabilities": []},
        {"model_repair_enabled": 1},
        {
            "failed_tools": ["missing"],
            "tool_capabilities": {},
        },
    ],
)
def test_policy_rejects_invalid_failure_facts(overrides) -> None:
    with pytest.raises(ProviderFailureRecoveryError):
        _policy(**overrides)
