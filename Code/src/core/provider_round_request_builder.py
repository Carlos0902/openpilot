"""Bind an immutable provider round plan to one context-aware LLM request."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.llm import LLMMessage, LLMRequest, LLMToolDefinition
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
        messages=_assembly_messages(plan.messages),
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


def _assembly_messages(messages: tuple[LLMMessage, ...]) -> list[LLMMessage]:
    """Project wire-only roles into non-empty assembly candidates.

    The existing context assembler predates provider tool continuations: it
    accepts only system/user/assistant candidates and rejects empty content.
    Assembly therefore receives a bounded diagnostic projection, while the
    returned request is restored to the immutable wire snapshot from ``plan``.
    """

    projected: list[LLMMessage] = []
    for message in messages:
        if message.role == "tool":
            projected.append(
                LLMMessage(
                    role="user",
                    content=(
                        f"[tool_result:{message.tool_call_id}] "
                        f"{message.content or '[empty tool result]'}"
                    ),
                )
            )
            continue
        if message.role == "assistant" and not message.content.strip():
            call_names = ", ".join(
                call.function.name for call in message.tool_calls
            )
            message = message.model_copy(
                update={
                    "content": (
                        f"[assistant tool calls: {call_names}]"
                        if call_names
                        else "[empty assistant response]"
                    )
                }
            )
        elif message.role in {"system", "user"} and not message.content.strip():
            message = message.model_copy(update={"content": "[empty message]"})
        projected.append(message)
    return projected


__all__ = ["ProviderRoundRequestBuilderError", "build_provider_round_llm_request"]
