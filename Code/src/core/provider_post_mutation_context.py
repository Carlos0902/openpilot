"""Build the narrow provider context used after one observed mutation."""

from __future__ import annotations

import json
from typing import Any

from core.llm import LLMMessage
from core.provider_mutation_receipt import (
    ProviderMutationReceiptError,
    provider_mutation_receipt,
)
from core.provider_tool_admission import MAX_PROVIDER_TOOL_ARGUMENT_CHARS
from core.provider_tool_batch_admission import MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE
from core.provider_tool_roundtrip_contracts import MAX_PROVIDER_ROUND_TRIP_MESSAGES
from core.provider_tool_result_payload import MAX_PROVIDER_TOOL_RESULT_CHARS
from core.tool_event_loop import ToolEventLoopRunResult

MAX_PROVIDER_POST_MUTATION_RECEIPT_CHARS = 16_384


class ProviderPostMutationContextError(ValueError):
    """Raised when a narrow post-mutation context cannot be built safely."""


def provider_post_mutation_messages(
    messages: Any,
    loop_result: ToolEventLoopRunResult,
    *,
    validation_command: str,
    wire_messages: Any = None,
) -> list[LLMMessage]:
    """Return bounded system/user context, one receipt, and mutation wire evidence."""

    source_messages = _message_list(messages, label="messages")
    retained_by_role: dict[str, LLMMessage] = {}
    for message in source_messages:
        if message.role not in {"system", "user"}:
            continue
        retained_by_role.setdefault(message.role, message.model_copy(deep=True))
        if set(retained_by_role) == {"system", "user"}:
            break
    if "user" not in retained_by_role:
        raise ProviderPostMutationContextError(
            "post-mutation context requires one user message"
        )
    retained = [
        retained_by_role[role]
        for role in ("system", "user")
        if role in retained_by_role
    ]

    try:
        receipt = provider_mutation_receipt(
            loop_result,
            validation_command=validation_command,
        )
    except ProviderMutationReceiptError as exc:
        raise ProviderPostMutationContextError(str(exc)) from exc
    if receipt is None:
        raise ProviderPostMutationContextError(
            "post-mutation context requires a successful mutation receipt"
        )
    try:
        receipt_text = json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProviderPostMutationContextError(
            "mutation receipt must be JSON serializable"
        ) from exc
    if len(receipt_text) > MAX_PROVIDER_POST_MUTATION_RECEIPT_CHARS:
        raise ProviderPostMutationContextError(
            "mutation receipt exceeds the post-mutation context limit"
        )

    wire = _validated_wire_messages(
        [] if wire_messages is None else wire_messages,
    )
    if len(retained) + 1 + len(wire) > MAX_PROVIDER_ROUND_TRIP_MESSAGES:
        raise ProviderPostMutationContextError(
            "post-mutation context exceeds the provider message limit"
        )

    instruction = (
        "The declared mutation has been applied. Do not regenerate code or repeat "
        "the write. Use command_executor exactly once with the required validation "
        "command, then report the evidence. Bounded mutation receipt: "
        f"{receipt_text}"
    )
    return [
        *retained,
        LLMMessage(role="user", content=instruction),
        *wire,
    ]


def _message_list(value: Any, *, label: str) -> list[LLMMessage]:
    if not isinstance(value, list):
        raise ProviderPostMutationContextError(
            f"{label} must be a bounded list"
        )
    if len(value) > MAX_PROVIDER_ROUND_TRIP_MESSAGES:
        raise ProviderPostMutationContextError(
            f"{label} exceed the provider message limit"
        )
    for index, message in enumerate(value):
        if not isinstance(message, LLMMessage):
            raise ProviderPostMutationContextError(
                f"{label}[{index}] must be LLMMessage"
            )
    return value


def _validated_wire_messages(value: Any) -> list[LLMMessage]:
    wire = _message_list(value, label="wire_messages")
    if len(wire) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE + 1:
        raise ProviderPostMutationContextError(
            "wire_messages exceed the provider message limit"
        )
    if not wire:
        return []
    if wire[0].role != "assistant" or any(
        message.role != "tool" for message in wire[1:]
    ):
        raise ProviderPostMutationContextError(
            "wire_messages[0] must be assistant followed only by tool messages"
        )

    assistant = wire[0]
    calls = list(assistant.tool_calls)
    tools = wire[1:]
    if not calls or len(calls) != len(tools):
        raise ProviderPostMutationContextError(
            "wire_messages require matching tool-call identities"
        )
    call_ids = [call.id for call in calls]
    result_ids = [message.tool_call_id for message in tools]
    if call_ids != result_ids or len(call_ids) != len(set(call_ids)):
        raise ProviderPostMutationContextError(
            "wire_messages require matching tool-call identities"
        )
    for index, call in enumerate(calls):
        raw_arguments = call.function.arguments
        if len(raw_arguments) > MAX_PROVIDER_TOOL_ARGUMENT_CHARS:
            raise ProviderPostMutationContextError(
                f"wire_messages[0].tool_calls[{index}] arguments exceed the limit"
            )
        try:
            arguments = json.loads(raw_arguments)
        except (RecursionError, TypeError, ValueError) as exc:
            raise ProviderPostMutationContextError(
                f"wire_messages[0].tool_calls[{index}] arguments must be JSON"
            ) from exc
        if not isinstance(arguments, dict):
            raise ProviderPostMutationContextError(
                f"wire_messages[0].tool_calls[{index}] arguments must be an object"
            )
        if _contains_generated_unit(arguments):
            raise ProviderPostMutationContextError(
                "wire_messages must not retain generated_unit bodies"
            )
    for index, message in enumerate(tools, start=1):
        if len(message.content) > MAX_PROVIDER_TOOL_RESULT_CHARS:
            raise ProviderPostMutationContextError(
                f"wire_messages[{index}] exceeds the tool-result character limit"
            )

    sanitized_assistant = assistant.model_copy(
        deep=True,
        update={"content": "", "reasoning_content": None},
    )
    return [
        sanitized_assistant,
        *(message.model_copy(deep=True) for message in tools),
    ]


def _contains_generated_unit(value: Any) -> bool:
    pending = [value]
    visited = 0
    while pending:
        item = pending.pop()
        visited += 1
        if visited > 1_024:
            raise ProviderPostMutationContextError(
                "wire tool arguments exceed the bounded item limit"
            )
        if isinstance(item, dict):
            if "generated_unit" in item:
                return True
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


__all__ = [
    "MAX_PROVIDER_POST_MUTATION_RECEIPT_CHARS",
    "ProviderPostMutationContextError",
    "provider_post_mutation_messages",
]
