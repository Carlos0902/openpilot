from __future__ import annotations

from types import SimpleNamespace

from core.config import LLMSettings
from core.llm import LLMClient, LLMMessage, LLMRequest


def test_llm_response_records_typed_reasoning_observation(monkeypatch) -> None:
    client = LLMClient(
        LLMSettings(
            OPENPILOT_LLM_API_KEY="test-key",
            OPENPILOT_LLM_PROVIDER="deepseek",
            OPENPILOT_LLM_BASE_URL="https://api.deepseek.com/v1",
            OPENPILOT_LLM_MODEL="deepseek-chat",
            OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="deepseek-chat-known",
        ),
        enable_cache=False,
    )
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())
    monkeypatch.setattr(
        client,
        "_create_completion_with_transport_retry",
        lambda *_args, **_kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="answer",
                        reasoning_content="hidden analysis",
                    ),
                    finish_reason="stop",
                )
            ],
            usage={
                "completion_tokens": 20,
                "completion_tokens_details": {"reasoning_tokens": 12},
            },
            model="deepseek-chat",
            id="reasoning-response",
            created=1,
        ),
    )

    response = client.complete(
        LLMRequest(messages=[LLMMessage(role="user", content="hello")])
    )

    observation = response.provider_details["reasoning_observation"]
    assert observation["reasoning_tokens"] == 12
    assert observation["reasoning_content_present"] is True
    assert observation["visible_content_empty"] is False
    assert observation["finish_reason"] == "stop"
