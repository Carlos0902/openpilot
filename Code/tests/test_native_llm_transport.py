import pytest

from core.config import LLMSettings
from core.llm import LLMMessage, LLMRequest
from core.native_llm_transport import (
    AnthropicMessagesTransport,
    GeminiGenerateContentTransport,
    NativeTransportUnsupportedError,
    get_native_transport,
)
from core.reasoning import resolve_reasoning_policy
from core.reasoning import UnsupportedReasoningPolicyError
from metadata import (
    ReasoningEffort,
    ReasoningMode,
    ReasoningPolicy,
    ReasoningTransportFamily,
)


def _settings(profile: str, *, base_url: str, model: str) -> LLMSettings:
    return LLMSettings(
        OPENPILOT_LLM_API_KEY="test-key",
        OPENPILOT_LLM_PROVIDER="native-test",
        OPENPILOT_LLM_BASE_URL=base_url,
        OPENPILOT_LLM_MODEL=model,
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE=profile,
    )


def test_anthropic_request_conversion_preserves_system_messages_and_reasoning() -> None:
    settings = _settings(
        "anthropic-messages-known",
        base_url="https://api.anthropic.com/v1",
        model="claude-sonnet-4-6",
    )
    request = LLMRequest(
        messages=[
            LLMMessage(role="system", content="You are concise."),
            LLMMessage(role="user", content="Return JSON."),
        ],
        response_format="json_object",
        max_tokens=2048,
        reasoning_policy=ReasoningPolicy(
            mode=ReasoningMode.ENABLED,
            effort=ReasoningEffort.HIGH,
        ),
    )
    resolved = resolve_reasoning_policy(request.reasoning_policy, settings)

    native = AnthropicMessagesTransport()
    built = native.build_request(settings, request, resolved)

    assert built.url == "https://api.anthropic.com/v1/messages"
    assert built.headers["x-api-key"] == "test-key"
    assert built.headers["anthropic-version"] == "2023-06-01"
    assert built.payload["system"].startswith("You are concise.")
    assert "valid JSON" in built.payload["system"]
    assert built.payload["messages"] == [{"role": "user", "content": "Return JSON."}]
    assert built.payload["thinking"] == {"type": "adaptive"}
    assert built.payload["output_config"] == {"effort": "high"}


def test_gemini_request_conversion_uses_native_camel_case_and_usage_endpoint() -> None:
    settings = _settings(
        "gemini-generate-content-known",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-2.5-flash",
    )
    request = LLMRequest(
        messages=[
            LLMMessage(role="system", content="Use a compact answer."),
            LLMMessage(role="user", content="Hello"),
            LLMMessage(role="assistant", content="Hi"),
        ],
        response_format="json_object",
        max_tokens=512,
        reasoning_policy=ReasoningPolicy(
            mode=ReasoningMode.ENABLED,
            effort=ReasoningEffort.MEDIUM,
        ),
    )
    resolved = resolve_reasoning_policy(request.reasoning_policy, settings)

    built = GeminiGenerateContentTransport().build_request(settings, request, resolved)

    assert built.url.endswith("/models/gemini-2.5-flash:generateContent")
    assert built.headers["x-goog-api-key"] == "test-key"
    assert built.payload["systemInstruction"] == {
        "parts": [{"text": "Use a compact answer."}]
    }
    assert built.payload["contents"] == [
        {"role": "user", "parts": [{"text": "Hello"}]},
        {"role": "model", "parts": [{"text": "Hi"}]},
    ]
    assert built.payload["generationConfig"]["responseMimeType"] == "application/json"
    assert built.payload["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "medium"}


def test_native_response_normalization_keeps_reasoning_blocks_and_usage() -> None:
    anthropic = AnthropicMessagesTransport().normalize_response(
        {
            "id": "msg_1",
            "model": "claude-sonnet-4-6",
            "stop_reason": "end_turn",
            "content": [
                {"type": "thinking", "thinking": "summary"},
                {"type": "text", "text": "answer"},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        },
        model="claude-sonnet-4-6",
    )
    assert anthropic.choices[0].message.content[0]["type"] == "thinking"
    assert anthropic.choices[0].message.content[1]["text"] == "answer"
    assert anthropic.choices[0].finish_reason == "end_turn"
    assert anthropic.usage == {"input_tokens": 10, "output_tokens": 20}

    gemini = GeminiGenerateContentTransport().normalize_response(
        {
            "modelVersion": "gemini-2.5-flash",
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "role": "model",
                        "parts": [
                            {"thought": True, "text": "internal"},
                            {"text": "answer"},
                        ],
                    },
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "thoughtsTokenCount": 5},
        },
        model="gemini-2.5-flash",
    )
    assert gemini.choices[0].message.content[0]["thought"] is True
    assert gemini.choices[0].message.content[1]["text"] == "answer"
    assert gemini.choices[0].finish_reason == "STOP"
    assert gemini.usage == {"promptTokenCount": 10, "thoughtsTokenCount": 5}


def test_anthropic_manual_budget_fails_closed_when_provider_budget_rules_are_violated() -> None:
    settings = _settings(
        "anthropic-messages-known",
        base_url="https://api.anthropic.com/v1",
        model="claude-sonnet-4-5",
    )
    request = LLMRequest(
        messages=[LLMMessage(role="user", content="hello")],
        max_tokens=2048,
        reasoning_policy=ReasoningPolicy(mode=ReasoningMode.ENABLED, token_budget=512),
    )
    resolved = resolve_reasoning_policy(request.reasoning_policy, settings)

    with pytest.raises(UnsupportedReasoningPolicyError, match="at least 1024"):
        AnthropicMessagesTransport().build_request(settings, request, resolved)


def test_native_transport_registry_is_explicit_and_openai_family_is_not_native() -> None:
    assert isinstance(
        get_native_transport(ReasoningTransportFamily.ANTHROPIC_MESSAGES),
        AnthropicMessagesTransport,
    )
    assert isinstance(
        get_native_transport(ReasoningTransportFamily.GOOGLE_GENERATE_CONTENT),
        GeminiGenerateContentTransport,
    )
    with pytest.raises(NativeTransportUnsupportedError):
        get_native_transport(ReasoningTransportFamily.OPENAI_CHAT_COMPLETIONS)


def test_llm_client_routes_native_response_through_existing_json_and_observation_pipeline(
    monkeypatch,
) -> None:
    settings = _settings(
        "anthropic-messages-known",
        base_url="https://api.anthropic.com/v1",
        model="claude-sonnet-4-6",
    )

    class FakeNativeTransport:
        def send_once(self, _settings, _request, _resolved, *, trust_env: bool):
            assert trust_env is True
            return AnthropicMessagesTransport().normalize_response(
                {
                    "id": "msg_2",
                    "model": "claude-sonnet-4-6",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": '{"ok": true}'}],
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                },
                model="claude-sonnet-4-6",
            )

    monkeypatch.setattr("core.llm.get_native_transport", lambda _family: FakeNativeTransport())
    from core.llm import LLMClient

    result = LLMClient(settings, enable_cache=False).complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="Return JSON.")],
            response_format="json_object",
            max_tokens=256,
        ),
        max_retries=1,
        use_cache=False,
    )

    assert result.parsed_json == {"ok": True}
    assert result.provider_details["reasoning_observation"]["visible_content_empty"] is False
