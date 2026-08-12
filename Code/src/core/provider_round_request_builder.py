"""Bind an immutable provider round plan to one context-aware LLM request."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.llm import LLMRequest, LLMToolDefinition
from core.provider_round_request_plan import ProviderRoundRequestPlan
from memory.context_assembly import build_context_llm_request
from metadata import ContextRequestPurpose, ReasoningPolicy


class ProviderRoundRequestBuilderError(ValueError):
    """Raised when a request cannot be built without violating its plan."""


def build_provider_round_llm_request(
    llm_client: Any,
    *,
    plan: ProviderRoundRequestPlan,
    tool_definitions: Sequence[LLMToolDefinition],
    purpose: ContextRequestPurpose,
    max_tokens: int | None = None,
    timeout_seconds: float | None = None,
    transport_retries: int | None = None,
    reasoning_policy: ReasoningPolicy | None = None,
    trace_info: dict[str, Any] | None = None,
) -> LLMRequest:
    """Build one request from a plan, without transport or tool execution."""

    if not isinstance(plan, ProviderRoundRequestPlan):
        raise ProviderRoundRequestBuilderError("provider request requires a typed plan")
    tools = tuple(tool_definitions)
    names = tuple(tool.function.name for tool in tools)
    if names != plan.tool_names:
        raise ProviderRoundRequestBuilderError("tool definitions do not match request plan")
    if plan.tool_choice is not None and not tools:
        raise ProviderRoundRequestBuilderError("request plan requires tools but none were supplied")

    request = build_context_llm_request(
        llm_client,
        messages=list(plan.messages),
        purpose=purpose,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        transport_retries=transport_retries,
        reasoning_policy=reasoning_policy,
        trace_info={
            **dict(trace_info or {}),
            "provider_round_max_calls": plan.max_calls,
            "provider_round_tool_result_char_budget": plan.tool_result_char_budget,
        },
    )
    request = request.model_copy(
        update={
            "messages": list(plan.messages),
            "tools": list(tools),
            "tool_choice": plan.tool_choice,
        }
    )
    if tuple(message.model_dump(mode="json") for message in request.messages) != tuple(
        message.model_dump(mode="json") for message in plan.messages
    ):
        raise ProviderRoundRequestBuilderError("context assembly changed planned messages")
    return request


__all__ = ["ProviderRoundRequestBuilderError", "build_provider_round_llm_request"]
