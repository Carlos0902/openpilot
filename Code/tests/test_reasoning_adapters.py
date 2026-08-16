from types import SimpleNamespace

import pytest

from core.config import LLMSettings
from core.llm import LLMClient, LLMMessage, LLMRequest
from core.reasoning import UnsupportedReasoningPolicyError
from core.reasoning import (
    observe_reasoning_response,
    render_reasoning_transport,
    resolve_reasoning_policy,
    select_reasoning_capability_profile,
)
from metadata import (
    ReasoningCapabilityProfileId,
    ReasoningEffort,
    ReasoningMode,
    ReasoningPolicy,
)


def _settings(profile: str) -> LLMSettings:
    return LLMSettings(
        OPENPILOT_LLM_API_KEY="test-key",
        OPENPILOT_LLM_PROVIDER="test",
        OPENPILOT_LLM_BASE_URL="https://provider.invalid/v1",
        OPENPILOT_LLM_MODEL="model",
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE=profile,
    )


def test_mainstream_profiles_are_explicit_and_versioned() -> None:
    assert select_reasoning_capability_profile(
        _settings("anthropic-messages-known")
    ).profile_id == ReasoningCapabilityProfileId.ANTHROPIC_MESSAGES_KNOWN
    assert select_reasoning_capability_profile(
        _settings("gemini-generate-content-known")
    ).profile_id == ReasoningCapabilityProfileId.GEMINI_GENERATE_CONTENT_KNOWN


def test_openai_and_deepseek_request_adapters_preserve_existing_wire_shapes() -> None:
    openai_disabled = resolve_reasoning_policy(
        ReasoningPolicy(mode=ReasoningMode.DISABLED),
        _settings("openai-chat-known"),
    )
    assert render_reasoning_transport(openai_disabled) == {"reasoning_effort": "none"}

    deepseek_enabled = resolve_reasoning_policy(
        ReasoningPolicy(mode=ReasoningMode.ENABLED, effort=ReasoningEffort.HIGH),
        _settings("deepseek-chat-known"),
    )
    assert render_reasoning_transport(deepseek_enabled) == {
        "reasoning_effort": "high",
        "extra_body": {"thinking": {"type": "enabled"}},
    }


def test_openai_no_reasoning_profile_omits_unsupported_wire_control() -> None:
    disabled = resolve_reasoning_policy(
        ReasoningPolicy(mode=ReasoningMode.DISABLED),
        _settings("openai-chat-no-reasoning-known"),
    )
    assert disabled.profile_id == ReasoningCapabilityProfileId.OPENAI_CHAT_NO_REASONING_KNOWN
    assert render_reasoning_transport(disabled) == {}
    with pytest.raises(UnsupportedReasoningPolicyError):
        resolve_reasoning_policy(
            ReasoningPolicy(
                mode=ReasoningMode.ENABLED,
                effort=ReasoningEffort.LOW,
            ),
            _settings("openai-chat-no-reasoning-known"),
        )


def test_openai_no_reasoning_payload_has_no_deepseek_or_reasoning_fields(monkeypatch) -> None:
    client = LLMClient(_settings("openai-chat-no-reasoning-known"), enable_cache=False)
    captured: list[dict] = []
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())

    def fake_complete(_client, payload, **_kwargs):
        captured.append(payload)
        from types import SimpleNamespace

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(model_dump=lambda: {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
            model="gpt-4o-mini",
        )

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_complete)
    client.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="Read cli.py")],
            reasoning_policy=ReasoningPolicy(mode=ReasoningMode.DISABLED),
        ),
        max_retries=1,
        use_cache=False,
    )
    assert "reasoning_effort" not in captured[0]
    assert "extra_body" not in captured[0]


def test_anthropic_adapter_renders_adaptive_effort_and_manual_budget() -> None:
    adaptive = resolve_reasoning_policy(
        ReasoningPolicy(mode=ReasoningMode.ENABLED, effort=ReasoningEffort.HIGH),
        _settings("anthropic-messages-known"),
    )
    assert render_reasoning_transport(adaptive) == {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
    }

    manual = resolve_reasoning_policy(
        ReasoningPolicy(mode=ReasoningMode.ENABLED, token_budget=2048),
        _settings("anthropic-messages-known"),
    )
    assert render_reasoning_transport(manual) == {
        "thinking": {"type": "enabled", "budget_tokens": 2048}
    }


def test_gemini_adapter_renders_budget_and_level() -> None:
    disabled = resolve_reasoning_policy(
        ReasoningPolicy(mode=ReasoningMode.DISABLED),
        _settings("gemini-generate-content-known"),
    )
    assert render_reasoning_transport(disabled) == {
        "generation_config": {"thinking_config": {"thinkingBudget": 0}}
    }

    level = resolve_reasoning_policy(
        ReasoningPolicy(mode=ReasoningMode.ENABLED, effort=ReasoningEffort.MEDIUM),
        _settings("gemini-generate-content-known"),
    )
    assert render_reasoning_transport(level) == {
        "generation_config": {"thinking_config": {"thinkingLevel": "medium"}}
    }


def test_reasoning_usage_observation_normalizes_provider_shapes_without_guessing() -> None:
    openai = observe_reasoning_response(
        profile_id=ReasoningCapabilityProfileId.OPENAI_CHAT_KNOWN,
        message=SimpleNamespace(reasoning_content="hidden"),
        usage={
            "completion_tokens_details": {"reasoning_tokens": 12},
        },
        finish_reason="stop",
        visible_content="answer",
    )
    assert openai.reasoning_tokens == 12
    assert openai.reasoning_tokens_source == "completion_tokens_details.reasoning_tokens"
    assert openai.reasoning_content_present is True
    assert openai.visible_content_empty is False

    anthropic = observe_reasoning_response(
        profile_id=ReasoningCapabilityProfileId.ANTHROPIC_MESSAGES_KNOWN,
        message={
            "content": [
                {"type": "thinking", "thinking": "summary"},
                {"type": "text", "text": "answer"},
            ]
        },
        usage={"output_tokens_details": {"thinking_tokens": 7}},
        finish_reason="end_turn",
        visible_content="answer",
    )
    assert anthropic.reasoning_tokens == 7
    assert anthropic.reasoning_content_present is True

    gemini = observe_reasoning_response(
        profile_id=ReasoningCapabilityProfileId.GEMINI_GENERATE_CONTENT_KNOWN,
        message={"candidates": [{"content": {"parts": [{"thought": True}]}}]},
        usage={"usageMetadata": {"thoughtsTokenCount": 5}},
        finish_reason="STOP",
        visible_content="answer",
    )
    assert gemini.reasoning_tokens == 5
    assert gemini.reasoning_tokens_source == "thoughtsTokenCount"


def test_empty_length_response_remains_explicit_failure_evidence() -> None:
    observation = observe_reasoning_response(
        profile_id=ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
        message=SimpleNamespace(reasoning_content="truncated plan"),
        usage={"completion_tokens": 128},
        finish_reason="length",
        visible_content="",
    )
    assert observation.reasoning_tokens is None
    assert observation.reasoning_tokens_source is None
    assert observation.reasoning_content_present is True
    assert observation.visible_content_empty is True
    assert observation.finish_reason == "length"


def test_native_profiles_fail_closed_for_unsupported_streaming_route() -> None:
    client = LLMClient(_settings("anthropic-messages-known"), enable_cache=False)
    with pytest.raises(UnsupportedReasoningPolicyError, match="non-streaming"):
        client.complete(
            LLMRequest(messages=[LLMMessage(role="user", content="hello")]),
            max_retries=1,
            use_cache=False,
            stream_callback=lambda _event: None,
        )
