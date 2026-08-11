"""Bound historical provider tool messages before the next model request."""

from __future__ import annotations

import json
from typing import Any

from core.llm import LLMMessage
from core.provider_tool_result_payload import (
    MAX_PROVIDER_TOOL_RESULT_CHARS,
    MIN_PROVIDER_TOOL_RESULT_CHARS,
    fit_provider_tool_result_payload,
)
from core.provider_tool_roundtrip_contracts import MAX_PROVIDER_ROUND_TRIP_MESSAGES

MAX_PROVIDER_HISTORICAL_TOOL_RESULT_CHARS = 640


class ProviderHistoricalToolCompactionError(ValueError):
    """Raised when historical tool-message compaction cannot stay bounded."""


def compact_provider_historical_tool_messages(
    messages: Any,
    *,
    result_char_limit: int = MAX_PROVIDER_HISTORICAL_TOOL_RESULT_CHARS,
) -> list[LLMMessage]:
    """Compact only tool results older than the latest assistant tool call."""

    source = _validated_messages(messages)
    if (
        type(result_char_limit) is not int
        or result_char_limit < MIN_PROVIDER_TOOL_RESULT_CHARS
        or result_char_limit > MAX_PROVIDER_TOOL_RESULT_CHARS
    ):
        raise ProviderHistoricalToolCompactionError(
            "result_char_limit must be within provider result bounds"
        )

    latest_tool_assistant = max(
        (
            index
            for index, message in enumerate(source)
            if message.role == "assistant" and message.tool_calls
        ),
        default=-1,
    )
    compacted: list[LLMMessage] = []
    for index, message in enumerate(source):
        if message.role != "tool" or index >= latest_tool_assistant:
            compacted.append(message)
            continue
        payload = _parse_tool_payload(message.content)
        try:
            content = fit_provider_tool_result_payload(
                payload,
                limit=result_char_limit,
            )
        except Exception as exc:
            raise ProviderHistoricalToolCompactionError(
                "historical tool result failed bounded projection"
            ) from exc
        compacted.append(message.model_copy(update={"content": content}))
    return compacted


def _validated_messages(value: Any) -> list[LLMMessage]:
    if not isinstance(value, list):
        raise ProviderHistoricalToolCompactionError(
            "messages must be a bounded list"
        )
    if len(value) > MAX_PROVIDER_ROUND_TRIP_MESSAGES:
        raise ProviderHistoricalToolCompactionError(
            "messages exceed the provider message limit"
        )
    for index, message in enumerate(value):
        if not isinstance(message, LLMMessage):
            raise ProviderHistoricalToolCompactionError(
                f"messages[{index}] must be LLMMessage"
            )
    return [message.model_copy(deep=True) for message in value]


def _parse_tool_payload(content: Any) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (TypeError, ValueError, RecursionError):
        return {
            "success": False,
            "tool": "unknown",
            "truncated": True,
            "content": str(content)[:480],
        }
    if isinstance(value, dict):
        if type(value.get("success")) is not bool:
            value = {"success": False, "tool": "unknown", "result": value}
        return value
    return {"success": False, "tool": "unknown", "result": {"value": str(value)}}


__all__ = [
    "MAX_PROVIDER_HISTORICAL_TOOL_RESULT_CHARS",
    "ProviderHistoricalToolCompactionError",
    "compact_provider_historical_tool_messages",
]
