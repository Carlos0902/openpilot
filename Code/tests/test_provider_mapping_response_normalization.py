from __future__ import annotations

from types import SimpleNamespace

from core.config import LLMSettings
from core.llm import LLMClient, LLMMessage, LLMRequest


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


def _response(message: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage={},
        model="custom-model",
        id="mapping-response",
        created=1,
    )


def test_llm_client_normalizes_mapping_content_and_diagnostics(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())
    monkeypatch.setattr(
        client,
        "_create_completion_with_transport_retry",
        lambda *_args, **_kwargs: _response(
            {"content": [{"type": "text", "text": "hello"}], "vendor": "x"}
        ),
    )

    response = client.complete(
        LLMRequest(messages=[LLMMessage(role="user", content="hello")])
    )

    assert response.content == "hello"
    assert response.provider_details["content_diagnostics"]["message_field_names"] == [
        "content",
        "vendor",
    ]


def test_llm_client_uses_mapping_fallback_content_field(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())
    monkeypatch.setattr(
        client,
        "_create_completion_with_transport_retry",
        lambda *_args, **_kwargs: _response({"output_text": "fallback"}),
    )

    response = client.complete(
        LLMRequest(messages=[LLMMessage(role="user", content="hello")])
    )

    assert response.content == "fallback"
    assert response.provider_details["content_diagnostics"]["fallback_content_field"] == (
        "output_text"
    )
