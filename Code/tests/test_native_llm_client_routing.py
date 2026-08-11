from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.config import LLMSettings
from core.llm import LLMClient, LLMMessage, LLMRequest
from core.reasoning import UnsupportedReasoningPolicyError


def _settings(**updates) -> LLMSettings:
    values = {
        "OPENPILOT_LLM_API_KEY": "test-key",
        "OPENPILOT_LLM_PROVIDER": "anthropic",
        "OPENPILOT_LLM_BASE_URL": "https://api.anthropic.com/v1",
        "OPENPILOT_LLM_MODEL": "claude-sonnet",
        "OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE": "anthropic-messages-known",
    }
    values.update(updates)
    return LLMSettings(**values)


def _response(content: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        usage={"input_tokens": 2, "output_tokens": 1},
        model="claude-sonnet",
        id="native-response",
        created=None,
        provider_details={"native_transport": "anthropic-messages"},
    )


def test_llm_client_routes_native_profile_without_openai_client(monkeypatch) -> None:
    client = LLMClient(_settings(), enable_cache=False)
    transport = SimpleNamespace(send_once=lambda *_args, **_kwargs: _response())
    monkeypatch.setattr(
        "core.llm.get_native_transport",
        lambda _family: transport,
        raising=False,
    )
    monkeypatch.setattr(
        client,
        "_make_openai_client",
        lambda **_kwargs: pytest.fail("native routing must not create an OpenAI client"),
    )

    result = client.complete(
        LLMRequest(messages=[LLMMessage(role="user", content="hello")])
    )

    assert result.content == "ok"
    assert result.provider_details["native_transport"] == "anthropic-messages"


def test_native_profile_rejects_streaming_before_transport(monkeypatch) -> None:
    client = LLMClient(_settings(), enable_cache=False)
    monkeypatch.setattr(
        "core.llm.get_native_transport",
        lambda _family: SimpleNamespace(send_once=lambda *_args, **_kwargs: _response()),
        raising=False,
    )
    monkeypatch.setattr(
        client,
        "_make_openai_client",
        lambda **_kwargs: pytest.fail("native streaming must reject before client creation"),
    )

    with pytest.raises(
        UnsupportedReasoningPolicyError,
        match="non-streaming calls only",
    ):
        client.complete(
            LLMRequest(messages=[LLMMessage(role="user", content="hello")]),
            stream_callback=lambda _event: None,
        )
