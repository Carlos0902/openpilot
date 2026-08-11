from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.config import LLMSettings
from core.llm import (
    LLMClient,
    LLMMessage,
    LLMRequest,
    LLMToolCall,
    LLMToolDefinition,
    LLMToolFunction,
    LLMToolFunctionCall,
)
from core.reasoning import UnsupportedReasoningPolicyError


def _settings() -> LLMSettings:
    return LLMSettings(
        OPENPILOT_LLM_API_KEY="test-key",
        OPENPILOT_LLM_PROVIDER="openai-compatible",
        OPENPILOT_LLM_BASE_URL="https://proxy.invalid/v1",
        OPENPILOT_LLM_MODEL="custom-model",
    )


def _tool() -> LLMToolDefinition:
    return LLMToolDefinition(
        function=LLMToolFunction(
            name="read_file",
            description="Read one file",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
    )


def _provider_response(*, tool_calls: object) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="",
                    reasoning_content="inspect first",
                    tool_calls=tool_calls,
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 12}),
        model="custom-model",
        id="response-tool-call",
        created=1,
    )


def test_llm_client_sends_typed_tools_and_preserves_provider_tool_calls(monkeypatch) -> None:
    client = LLMClient(_settings(), enable_cache=False)
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())

    provider_call = SimpleNamespace(
        id="call-1",
        type="function",
        index=0,
        function=SimpleNamespace(name="read_file", arguments='{"path":"README.md"}'),
    )

    def fake_complete(_client, payload, **_kwargs):
        captured.append(payload)
        return _provider_response(tool_calls=[provider_call])

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_complete)

    response = client.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="Inspect the project")],
            tools=[_tool()],
            tool_choice="auto",
        )
    )

    assert captured[0]["tools"] == [_tool().model_dump(mode="json")]
    assert captured[0]["tool_choice"] == "auto"
    assert response.reasoning_content == "inspect first"
    assert response.tool_calls == [
        LLMToolCall(
            id="call-1",
            index=0,
            function=LLMToolFunctionCall(
                name="read_file",
                arguments='{"path":"README.md"}',
            ),
        )
    ]


def test_cache_identity_includes_tool_contract_and_choice() -> None:
    client = LLMClient(_settings(), enable_cache=False)
    plain = LLMRequest(messages=[LLMMessage(role="user", content="Inspect")])
    automatic = LLMRequest(
        messages=plain.messages,
        tools=[_tool()],
        tool_choice="auto",
    )
    required = automatic.model_copy(update={"tool_choice": "required"})

    assert client._make_cache_key(plain) != client._make_cache_key(automatic)
    assert client._make_cache_key(automatic) != client._make_cache_key(required)


def test_tool_call_responses_are_not_cached(monkeypatch) -> None:
    class RecordingCache:
        def __init__(self) -> None:
            self.puts: list[object] = []

        def get(self, _key):
            return "miss", None

        def put(self, _key, value):
            self.puts.append(value)

    client = LLMClient(_settings(), enable_cache=False)
    client._cache = RecordingCache()
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())
    monkeypatch.setattr(
        client,
        "_create_completion_with_transport_retry",
        lambda *_args, **_kwargs: _provider_response(
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ]
        ),
    )

    response = client.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="Inspect")],
            tools=[_tool()],
        )
    )

    assert response.tool_calls[0].id == "call-1"
    assert client._cache.puts == []


def test_malformed_provider_tool_call_fails_closed(monkeypatch) -> None:
    client = LLMClient(_settings(), enable_cache=False)
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())
    monkeypatch.setattr(
        client,
        "_create_completion_with_transport_retry",
        lambda *_args, **_kwargs: _provider_response(tool_calls={"unexpected": True}),
    )

    with pytest.raises(UnsupportedReasoningPolicyError, match="tool_calls must be a list"):
        client.complete(
            LLMRequest(
                messages=[LLMMessage(role="user", content="Inspect")],
                tools=[_tool()],
            )
        )
