from __future__ import annotations

from types import SimpleNamespace

from core.config import LLMSettings
from core.llm import LLMClient, LLMStreamEvent


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


def _chunk(*, content=None, reasoning_content=None, finish_reason=None):
    return SimpleNamespace(
        model="custom-model",
        id="stream-1",
        created=1,
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                delta=SimpleNamespace(
                    content=content,
                    reasoning_content=reasoning_content,
                ),
            )
        ],
    )


def test_streamed_reasoning_is_preserved_without_becoming_visible_text() -> None:
    client = _client()
    events: list[LLMStreamEvent] = []

    response = client._collect_streaming_completion(
        [
            _chunk(reasoning_content="inspect "),
            _chunk(reasoning_content="first", content="ok"),
            _chunk(finish_reason="stop"),
        ],
        events.append,
    )

    assert response.choices[0].message.content == "ok"
    assert response.choices[0].message.reasoning_content == "inspect first"
    assert [event.text_delta for event in events if event.event_type == "delta"] == [
        "ok"
    ]


def test_stream_without_reasoning_keeps_reasoning_content_absent() -> None:
    client = _client()

    response = client._collect_streaming_completion(
        [_chunk(content="plain"), _chunk(finish_reason="stop")],
        lambda _event: None,
    )

    assert response.choices[0].message.reasoning_content is None
