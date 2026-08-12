from __future__ import annotations

import json

import pytest

from core.llm import LLMMessage, LLMToolCall, LLMToolFunctionCall
from core.provider_historical_tool_compaction import (
    MAX_PROVIDER_HISTORICAL_TOOL_RESULT_CHARS,
    ProviderHistoricalToolCompactionError,
    compact_provider_historical_tool_messages,
)
from core.provider_tool_roundtrip_contracts import MAX_PROVIDER_ROUND_TRIP_MESSAGES


def _tool_call(call_id: str) -> LLMToolCall:
    return LLMToolCall(
        id=call_id,
        function=LLMToolFunctionCall(
            name="file_reader",
            arguments='{"file_path":"README.md"}',
        ),
    )


def test_compaction_shrinks_only_historical_tool_results() -> None:
    old_payload = {
        "success": True,
        "tool": "file_reader",
        "result": {
            "kind": "file_artifact",
            "file_path": "README.md",
            "content": "old evidence " * 2_000,
            "lines_read": 2_000,
            "total_lines": 2_000,
            "truncated": False,
        },
    }
    latest_content = json.dumps(
        {"success": True, "result": {"preview": "latest evidence"}},
        separators=(",", ":"),
    )
    messages = [
        LLMMessage(role="user", content="Read files"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[_tool_call("old-call")],
        ),
        LLMMessage(
            role="tool",
            content=json.dumps(old_payload, ensure_ascii=False),
            tool_call_id="old-call",
        ),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[_tool_call("latest-call")],
        ),
        LLMMessage(role="tool", content=latest_content, tool_call_id="latest-call"),
    ]

    compacted = compact_provider_historical_tool_messages(messages)

    assert compacted[2].tool_call_id == "old-call"
    assert len(compacted[2].content) <= MAX_PROVIDER_HISTORICAL_TOOL_RESULT_CHARS
    assert compacted[4].tool_call_id == "latest-call"
    assert compacted[4].content == latest_content
    assert len(compacted) == len(messages)


def test_compaction_converts_invalid_json_to_bounded_failure_payload() -> None:
    messages = [
        LLMMessage(role="user", content="Read files"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[_tool_call("old-call")],
        ),
        LLMMessage(
            role="tool",
            content="not-json " * 2_000,
            tool_call_id="old-call",
        ),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[_tool_call("latest-call")],
        ),
        LLMMessage(role="tool", content='{"success":true}', tool_call_id="latest-call"),
    ]

    compacted = compact_provider_historical_tool_messages(messages)
    payload = json.loads(compacted[2].content)

    assert payload["success"] is False
    assert payload["truncated"] is True
    assert len(compacted[2].content) <= MAX_PROVIDER_HISTORICAL_TOOL_RESULT_CHARS


def test_compaction_does_not_rewrite_tool_results_after_latest_tool_call() -> None:
    messages = [
        LLMMessage(role="assistant", content="no tools yet"),
        LLMMessage(role="tool", content="not-json " * 2_000, tool_call_id="orphan"),
    ]

    compacted = compact_provider_historical_tool_messages(messages)

    assert compacted[1].content == messages[1].content


def test_compaction_deep_copies_messages_and_rejects_unbounded_collections() -> None:
    messages = [LLMMessage(role="user", content="Read files")]
    compacted = compact_provider_historical_tool_messages(messages)
    assert compacted[0] is not messages[0]

    too_many = [
        LLMMessage(role="user", content=str(index))
        for index in range(MAX_PROVIDER_ROUND_TRIP_MESSAGES + 1)
    ]
    with pytest.raises(
        ProviderHistoricalToolCompactionError,
        match="message limit",
    ):
        compact_provider_historical_tool_messages(too_many)


@pytest.mark.parametrize(
    "messages",
    [
        (LLMMessage(role="user", content="Read files"),),
        ["not-a-message"],
    ],
)
def test_compaction_rejects_non_list_or_invalid_messages(messages) -> None:
    with pytest.raises(ProviderHistoricalToolCompactionError):
        compact_provider_historical_tool_messages(messages)
