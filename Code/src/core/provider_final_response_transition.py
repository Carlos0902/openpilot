"""Classify provider completion versus tool or finalization failure."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.llm import LLMResponse


class ProviderFinalResponseAction(str, Enum):
    """Next action after one provider response arrives."""

    COMPLETE = "complete"
    EXECUTE_TOOLS = "execute_tools"
    FAIL = "fail"


class ProviderFinalResponseErrorCode(str, Enum):
    """Stable terminal codes for invalid finalization responses."""

    FINALIZATION_TOOL_CALL = "ProviderToolFinalizationToolCall"
    FINALIZATION_EMPTY = "ProviderToolFinalizationEmpty"
    FINALIZATION_REASONING_EXHAUSTED = (
        "ProviderToolFinalizationReasoningExhausted"
    )


class ProviderFinalResponseError(ValueError):
    """Raised when final-response transition inputs are invalid."""


@dataclass(frozen=True)
class ProviderFinalResponseTransition:
    """Mutually exclusive response action with finalization state."""

    action: ProviderFinalResponseAction
    error_code: ProviderFinalResponseErrorCode | None
    finalization_pending: bool


def provider_final_response_transition(
    response: LLMResponse,
    *,
    finalization_pending: bool,
) -> ProviderFinalResponseTransition:
    """Classify one response before any tool execution begins."""

    if not isinstance(response, LLMResponse):
        raise ProviderFinalResponseError("response must be LLMResponse")
    if type(finalization_pending) is not bool:
        raise ProviderFinalResponseError(
            "finalization_pending must be a literal boolean"
        )

    if response.tool_calls:
        if finalization_pending:
            return _failure(
                ProviderFinalResponseErrorCode.FINALIZATION_TOOL_CALL
            )
        return ProviderFinalResponseTransition(
            action=ProviderFinalResponseAction.EXECUTE_TOOLS,
            error_code=None,
            finalization_pending=False,
        )
    if finalization_pending and not response.content.strip():
        return _failure(_empty_finalization_error(response))
    return ProviderFinalResponseTransition(
        action=ProviderFinalResponseAction.COMPLETE,
        error_code=None,
        finalization_pending=False,
    )


def _empty_finalization_error(
    response: LLMResponse,
) -> ProviderFinalResponseErrorCode:
    finish_reason = str(response.finish_reason or "").strip().lower()
    if finish_reason not in {"length", "max_tokens"}:
        return ProviderFinalResponseErrorCode.FINALIZATION_EMPTY
    usage = response.usage
    if not isinstance(usage, dict):
        return ProviderFinalResponseErrorCode.FINALIZATION_EMPTY
    completion_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    details = usage.get("completion_tokens_details")
    reasoning_tokens = (
        details.get("reasoning_tokens") if isinstance(details, dict) else None
    )
    if (
        type(completion_tokens) is int
        and completion_tokens > 0
        and type(reasoning_tokens) is int
        and reasoning_tokens >= completion_tokens
    ):
        return ProviderFinalResponseErrorCode.FINALIZATION_REASONING_EXHAUSTED
    return ProviderFinalResponseErrorCode.FINALIZATION_EMPTY


def _failure(
    error_code: ProviderFinalResponseErrorCode,
) -> ProviderFinalResponseTransition:
    return ProviderFinalResponseTransition(
        action=ProviderFinalResponseAction.FAIL,
        error_code=error_code,
        finalization_pending=False,
    )


__all__ = [
    "ProviderFinalResponseAction",
    "ProviderFinalResponseError",
    "ProviderFinalResponseErrorCode",
    "ProviderFinalResponseTransition",
    "provider_final_response_transition",
]
