"""Classify provider failures into safe continuation or terminal outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.provider_tool_batch_admission import MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE
from core.tool_contracts import ToolCapability

_ADMISSION_FAILURES = frozenset(
    {
        "InvalidToolArguments",
        "MissingRequiredInput",
        "MissingRequiredInputGroup",
        "PermissionDenied",
        "ProviderToolBlocked",
        "ProviderToolScopeViolation",
        "ToolBudgetExhausted",
        "ToolConfirmationRequired",
        "UnknownTool",
        "UserConfirmationRequired",
    }
)
_MODEL_REPAIR_FAILURES = frozenset(
    {
        "InvalidToolArguments",
        "MissingRequiredInput",
        "MissingRequiredInputGroup",
        "UnknownTool",
    }
)
_TERMINAL_FAILURES = frozenset(
    {
        "CheckpointPrepareFailed",
        "CheckpointObservationFailed",
        "IndeterminateSideEffect",
        "MutationVerificationFailed",
    }
)
_UNSAFE_CAPABILITIES = frozenset(
    {
        ToolCapability.FILE_WRITE.value,
        ToolCapability.FILE_DELETE.value,
        ToolCapability.CODE_EXECUTION.value,
        ToolCapability.SHELL_EXECUTION.value,
    }
)


class ProviderFailureRecoveryAction(str, Enum):
    """Safe next policy for one provider failure observation."""

    NONE = "none"
    CONTINUE = "continue"
    REPAIR = "repair"
    FAIL = "fail"


class ProviderFailureRecoveryError(ValueError):
    """Raised when provider failure facts are malformed or incomplete."""


@dataclass(frozen=True)
class ProviderFailureRecoveryDecision:
    """Derived failure action with stable terminal identity."""

    action: ProviderFailureRecoveryAction
    error_code: str | None


def provider_failure_recovery_policy(
    *,
    failure_type: str | None,
    failure_recoverable: bool,
    failed_tools: Any,
    tool_capabilities: Any,
    model_repair_enabled: bool,
) -> ProviderFailureRecoveryDecision:
    """Return a safe recovery class without executing or retrying anything."""

    if type(failure_recoverable) is not bool:
        raise ProviderFailureRecoveryError(
            "failure_recoverable must be a literal boolean"
        )
    if type(model_repair_enabled) is not bool:
        raise ProviderFailureRecoveryError(
            "model_repair_enabled must be a literal boolean"
        )
    if failure_type is not None and (
        not isinstance(failure_type, str) or not failure_type.strip()
    ):
        raise ProviderFailureRecoveryError(
            "failure_type must be None or a non-empty string"
        )
    if not isinstance(failed_tools, (list, tuple)):
        raise ProviderFailureRecoveryError(
            "failed_tools must be a bounded list or tuple"
        )
    if len(failed_tools) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE:
        raise ProviderFailureRecoveryError("failed_tools exceed the call limit")
    if any(not isinstance(tool, str) or not tool.strip() for tool in failed_tools):
        raise ProviderFailureRecoveryError(
            "failed_tools must contain non-empty strings"
        )
    if len(set(failed_tools)) != len(failed_tools):
        raise ProviderFailureRecoveryError("failed_tools must be unique")
    if not isinstance(tool_capabilities, dict):
        raise ProviderFailureRecoveryError("tool_capabilities must be a mapping")
    if set(failed_tools) - set(tool_capabilities):
        raise ProviderFailureRecoveryError(
            "every failed tool must have capability evidence"
        )
    capabilities_by_tool = {
        tool: _capability_values(tool_capabilities[tool], tool)
        for tool in failed_tools
    }
    if failure_type is None:
        if failed_tools:
            raise ProviderFailureRecoveryError(
                "failed_tools require a failure_type"
            )
        return ProviderFailureRecoveryDecision(
            action=ProviderFailureRecoveryAction.NONE,
            error_code=None,
        )
    if not failure_recoverable:
        return _failure(
            "ProviderToolAdmissionFailure"
            if failure_type in _ADMISSION_FAILURES
            else "ProviderToolExecutionFailure"
        )
    if failure_type in _TERMINAL_FAILURES:
        return _failure("ProviderToolExecutionFailure")
    if failure_type in _ADMISSION_FAILURES:
        if model_repair_enabled and failure_type in _MODEL_REPAIR_FAILURES:
            return ProviderFailureRecoveryDecision(
                action=ProviderFailureRecoveryAction.REPAIR,
                error_code=None,
            )
        return ProviderFailureRecoveryDecision(
            action=ProviderFailureRecoveryAction.CONTINUE,
            error_code=None,
        )
    if not failed_tools:
        return _failure("ProviderToolExecutionFailure")
    if any(
        ToolCapability.FILE_READ.value not in capabilities
        or capabilities.intersection(_UNSAFE_CAPABILITIES)
        for capabilities in capabilities_by_tool.values()
    ):
        return _failure("ProviderToolExecutionFailure")
    return ProviderFailureRecoveryDecision(
        action=ProviderFailureRecoveryAction.CONTINUE,
        error_code=None,
    )


def _capability_values(value: Any, tool_name: str) -> frozenset[str]:
    if not isinstance(value, (list, tuple)):
        raise ProviderFailureRecoveryError(
            f"capabilities for {tool_name} must be a bounded list or tuple"
        )
    if len(value) > 16:
        raise ProviderFailureRecoveryError(
            f"capabilities for {tool_name} exceed the field limit"
        )
    normalized: set[str] = set()
    for capability in value:
        if isinstance(capability, ToolCapability):
            normalized.add(capability.value)
        elif isinstance(capability, str):
            try:
                normalized.add(ToolCapability(capability).value)
            except ValueError as exc:
                raise ProviderFailureRecoveryError(
                    f"unknown capability for {tool_name}"
                ) from exc
        else:
            raise ProviderFailureRecoveryError(
                f"capability for {tool_name} must be typed"
            )
    return frozenset(normalized)


def _failure(error_code: str) -> ProviderFailureRecoveryDecision:
    return ProviderFailureRecoveryDecision(
        action=ProviderFailureRecoveryAction.FAIL,
        error_code=error_code,
    )


__all__ = [
    "ProviderFailureRecoveryAction",
    "ProviderFailureRecoveryDecision",
    "ProviderFailureRecoveryError",
    "provider_failure_recovery_policy",
]
