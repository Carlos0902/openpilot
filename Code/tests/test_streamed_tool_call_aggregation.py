from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.config import LLMSettings
from core.llm import LLMClient, LLMStreamEvent
from core.reasoning import UnsupportedReasoningPolicyError


def _client() -> LLMClient:
    return LLMClient(
        LLMSettings(
            OPENPILOT_LLM_API_KEY="test-key",
            OPENPILOT_LLM_PROVIDER="openai-compatible",
            OPENPILOT_LLM_BASE_URL="https://proxy.invalid/v1",
            OPENPILOT_LLM_MODEL="custom-model",
        ),
        enable_cache=False,
    )


def _chunk(*, tool_calls=None, finish_reason=None):
    return SimpleNamespace(
        model="custom-model",
        id="stream-tools",
        created=1,
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                delta=SimpleNamespace(content=None, tool_calls=tool_calls),
            )
        ],
    )


def _fragment(*, index, call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_streamed_tool_call_fragments_are_joined_by_index() -> None:
    client = _client()
    events: list[LLMStreamEvent] = []

    response = client._collect_streaming_completion(
        [
            _chunk(
                tool_calls=[
                    _fragment(
                        index=0,
                        call_id="call-1",
                        name="read_",
                        arguments='{"path":',
                    )
                ]
            ),
            _chunk(
                tool_calls=[
                    _fragment(index=0, name="file", arguments='"README.md"}')
                ]
            ),
            _chunk(finish_reason="tool_calls"),
        ],
        events.append,
    )

    calls = response.choices[0].message.tool_calls
    assert len(calls) == 1
    assert calls[0].id == "call-1"
    assert calls[0].index == 0
    assert calls[0].function.name == "read_file"
    assert calls[0].function.arguments == '{"path":"README.md"}'
    assert events[-1].provider_details == {"tool_call_count": 1}


def test_streamed_tool_calls_are_finalized_in_index_order() -> None:
    client = _client()

    response = client._collect_streaming_completion(
        [
            _chunk(
                tool_calls=[
                    _fragment(index=1, call_id="call-2", name="second", arguments="{}"),
                    _fragment(index=0, call_id="call-1", name="first", arguments="{}"),
                ]
            ),
            _chunk(finish_reason="tool_calls"),
        ],
        lambda _event: None,
    )

    assert [call.id for call in response.choices[0].message.tool_calls] == [
        "call-1",
        "call-2",
    ]


@pytest.mark.parametrize("tool_calls", [{"bad": True}, [_fragment(index=-1)]])
def test_invalid_streamed_tool_call_shape_fails_closed(tool_calls) -> None:
    client = _client()

    with pytest.raises(UnsupportedReasoningPolicyError, match="tool_call"):
        client._collect_streaming_completion(
            [_chunk(tool_calls=tool_calls)],
            lambda _event: None,
        )
