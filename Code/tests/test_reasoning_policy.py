from __future__ import annotations

import pytest
from pydantic import ValidationError
from types import SimpleNamespace

from core.config import LLMSettings
from core.llm import LLMClient, LLMMessage, LLMRequest, normalized_provider_endpoint
from core.reasoning import (
    UnsupportedReasoningPolicyError,
    render_reasoning_transport,
    reasoning_policy_for_decision,
    resolve_reasoning_policy,
    routine_tool_reasoning_policy,
    select_reasoning_capability_profile,
)
from memory.context_assembly import build_context_llm_request
from metadata import ContextRequestPurpose
from metadata import (
    ReasoningEffort,
    ReasoningCapabilityProfileId,
    ReasoningDecisionComplexity,
    ReasoningMode,
    ReasoningPolicy,
    ReasoningResolution,
    PendingLLMRequest,
    LLMRequestHashVersion,
    UnsupportedReasoningBehavior,
)


def _settings(**updates) -> LLMSettings:
    values = {
        "OPENPILOT_LLM_API_KEY": "test-key",
        "OPENPILOT_LLM_PROVIDER": "openai-compatible",
        "OPENPILOT_LLM_BASE_URL": "https://proxy.invalid/v1",
        "OPENPILOT_LLM_MODEL": "custom-model",
    }
    values.update(updates)
    return LLMSettings(**values)


def test_reasoning_policy_is_strict_and_defaults_to_provider_behavior() -> None:
    policy = ReasoningPolicy()

    assert policy.mode == ReasoningMode.PROVIDER_DEFAULT
    assert policy.effort is None
    assert policy.token_budget is None
    with pytest.raises(ValidationError):
        ReasoningPolicy.model_validate({"mode": "disabled", "effort": "low"})
    with pytest.raises(ValidationError):
        ReasoningPolicy.model_validate({"mode": "enabled", "unknown": True})


def test_routine_tool_reasoning_mode_is_typed_and_ab_overridable() -> None:
    default_settings = _settings()
    baseline_settings = _settings(
        OPENPILOT_TOOL_EVENT_REASONING_MODE="provider_default"
    )

    assert default_settings.tool_event_reasoning_mode == ReasoningMode.DISABLED
    assert routine_tool_reasoning_policy(default_settings).mode == ReasoningMode.DISABLED
    assert (
        routine_tool_reasoning_policy(baseline_settings).mode
        == ReasoningMode.PROVIDER_DEFAULT
    )
    with pytest.raises(ValidationError):
        _settings(OPENPILOT_TOOL_EVENT_REASONING_MODE="enabled")


def test_provider_tool_completion_outcome_feedback_flag_is_typed_and_default_off() -> None:
    default_settings = _settings()
    enabled_settings = _settings(
        OPENPILOT_PROVIDER_TOOL_COMPLETION_OUTCOME_FEEDBACK_ENABLED="true"
    )

    assert default_settings.provider_tool_completion_outcome_feedback_enabled is False
    assert enabled_settings.provider_tool_completion_outcome_feedback_enabled is True


def test_decision_complexity_routes_reasoning_without_changing_completion_budget() -> None:
    settings = _settings(
        OPENPILOT_TOOL_EVENT_REASONING_MODE="disabled",
        OPENPILOT_LLM_BASE_URL="https://api.deepseek.com/v1",
        OPENPILOT_LLM_MODEL="deepseek-v4-flash",
    )

    routine = reasoning_policy_for_decision(
        settings,
        ReasoningDecisionComplexity.ROUTINE,
    )
    standard = reasoning_policy_for_decision(
        settings,
        ReasoningDecisionComplexity.STANDARD,
    )
    complex_policy = reasoning_policy_for_decision(
        settings,
        ReasoningDecisionComplexity.COMPLEX,
    )

    assert routine.mode == ReasoningMode.DISABLED
    assert standard.mode == ReasoningMode.PROVIDER_DEFAULT
    assert complex_policy.mode == ReasoningMode.PROVIDER_DEFAULT
    assert standard == complex_policy
    assert routine.token_budget is None
    assert standard.token_budget is None


def test_nonroutine_complexity_route_is_explicit_provider_default() -> None:
    """The standard/complex route is a typed provider-default baseline."""
    settings = _settings(OPENPILOT_TOOL_EVENT_REASONING_MODE="disabled")

    policy = reasoning_policy_for_decision(
        settings,
        ReasoningDecisionComplexity.COMPLEX,
    )

    assert policy.mode == ReasoningMode.PROVIDER_DEFAULT


def test_provider_default_tool_reasoning_remains_provider_default_for_complex_task() -> None:
    settings = _settings(OPENPILOT_TOOL_EVENT_REASONING_MODE="provider_default")

    for complexity in (
        ReasoningDecisionComplexity.STANDARD,
        ReasoningDecisionComplexity.COMPLEX,
    ):
        policy = reasoning_policy_for_decision(settings, complexity)
        assert policy.mode == ReasoningMode.PROVIDER_DEFAULT


def test_llm_request_hash_version_migrates_legacy_and_validates_v2_prefix() -> None:
    legacy = PendingLLMRequest(task_id="task", request_ordinal=1, request_hash="sha256:old")
    current = PendingLLMRequest(
        task_id="task",
        request_ordinal=1,
        request_hash="v2:sha256:" + "a" * 64,
        hash_version=LLMRequestHashVersion.PROVIDER_BOUND_V2,
    )

    assert legacy.hash_version == LLMRequestHashVersion.LEGACY_UNBOUND_V1
    assert current.hash_version == LLMRequestHashVersion.PROVIDER_BOUND_V2
    with pytest.raises(ValidationError):
        PendingLLMRequest(
            task_id="task",
            request_ordinal=1,
            request_hash="sha256:unbound",
            hash_version=LLMRequestHashVersion.PROVIDER_BOUND_V2,
        )


def test_llm_request_default_policy_is_transport_neutral() -> None:
    request = LLMRequest(messages=[LLMMessage(role="user", content="hello")])
    settings = _settings()

    resolved = resolve_reasoning_policy(request.reasoning_policy, settings)

    assert resolved.effective_mode == ReasoningMode.PROVIDER_DEFAULT
    assert resolved.resolution == ReasoningResolution.OMITTED
    assert resolved.profile_id == "generic-openai-compatible"
    assert render_reasoning_transport(resolved) == {}


def test_structured_provider_default_maps_to_disabled_for_known_profile() -> None:
    settings = _settings(
        OPENPILOT_LLM_BASE_URL="https://api.deepseek.com/v1",
        OPENPILOT_LLM_MODEL="deepseek-v4-flash",
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="deepseek-chat-known",
    )

    resolved = resolve_reasoning_policy(
        ReasoningPolicy(mode=ReasoningMode.PROVIDER_DEFAULT),
        settings,
        structured_output=True,
    )

    assert resolved.effective_mode == ReasoningMode.DISABLED
    assert resolved.resolution == ReasoningResolution.MAPPED
    assert render_reasoning_transport(resolved) == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }


def test_structured_provider_default_stays_omitted_without_disable_capability() -> None:
    settings = _settings(
        OPENPILOT_LLM_BASE_URL="https://proxy.invalid/v1",
        OPENPILOT_LLM_MODEL="custom-model",
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="generic-openai-compatible",
    )

    resolved = resolve_reasoning_policy(
        ReasoningPolicy(mode=ReasoningMode.PROVIDER_DEFAULT),
        settings,
        structured_output=True,
    )

    assert resolved.effective_mode == ReasoningMode.PROVIDER_DEFAULT
    assert resolved.resolution == ReasoningResolution.OMITTED


def test_unknown_provider_rejects_explicit_reasoning_instead_of_guessing() -> None:
    settings = _settings()
    policy = ReasoningPolicy(mode=ReasoningMode.ENABLED, effort=ReasoningEffort.HIGH)

    with pytest.raises(UnsupportedReasoningPolicyError):
        resolve_reasoning_policy(policy, settings)


def test_generic_provider_can_explicitly_fall_back_to_provider_default() -> None:
    settings = _settings()
    policy = ReasoningPolicy(
        mode=ReasoningMode.DISABLED,
        unsupported_behavior=UnsupportedReasoningBehavior.PROVIDER_DEFAULT,
    )

    resolved = resolve_reasoning_policy(policy, settings)

    assert resolved.effective_mode == ReasoningMode.PROVIDER_DEFAULT
    assert resolved.resolution == ReasoningResolution.OMITTED
    assert render_reasoning_transport(resolved) == {}


def test_unsupported_reasoning_token_budget_is_not_recorded_as_effective() -> None:
    settings = _settings(
        OPENPILOT_LLM_BASE_URL="https://api.openai.com/v1",
        OPENPILOT_LLM_MODEL="gpt-5.6-terra",
    )
    strict = ReasoningPolicy(
        mode=ReasoningMode.ENABLED,
        effort=ReasoningEffort.HIGH,
        token_budget=123,
    )
    fallback = strict.model_copy(
        update={"unsupported_behavior": UnsupportedReasoningBehavior.PROVIDER_DEFAULT}
    )

    with pytest.raises(UnsupportedReasoningPolicyError, match="token budget"):
        resolve_reasoning_policy(strict, settings)
    resolved = resolve_reasoning_policy(fallback, settings)
    assert resolved.effective_mode == ReasoningMode.PROVIDER_DEFAULT
    assert resolved.effective_token_budget is None
    assert render_reasoning_transport(resolved) == {}


def test_provider_endpoint_identity_preserves_non_default_port_without_credentials() -> None:
    assert normalized_provider_endpoint("http://user:secret@localhost:8000/v1") == (
        "http://localhost:8000/v1"
    )
    assert normalized_provider_endpoint("http://localhost:9000/v1") != (
        normalized_provider_endpoint("http://localhost:8000/v1")
    )
    assert normalized_provider_endpoint("https://api.example.com:443/v1") == (
        "https://api.example.com/v1"
    )


def test_profile_selection_requires_explicit_profile_and_never_guesses_from_model() -> None:
    official = _settings(
        OPENPILOT_LLM_BASE_URL="https://api.openai.com/v1",
        OPENPILOT_LLM_MODEL="gpt-5.6-terra",
    )
    explicit = _settings(
        OPENPILOT_LLM_BASE_URL="https://api.openai.com/v1",
        OPENPILOT_LLM_MODEL="custom-model-name",
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="openai-chat-known",
    )

    assert (
        select_reasoning_capability_profile(official).profile_id
        == ReasoningCapabilityProfileId.GENERIC_OPENAI_COMPATIBLE
    )
    assert select_reasoning_capability_profile(official).version == "v1"
    assert (
        select_reasoning_capability_profile(explicit).profile_id
        == ReasoningCapabilityProfileId.OPENAI_CHAT_KNOWN
    )
    assert select_reasoning_capability_profile(explicit).version == "v1"


def test_reasoning_capability_profile_override_is_typed_at_config_boundary() -> None:
    configured = _settings(
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="generic-openai-compatible"
    )

    assert configured.reasoning_capability_profile == (
        ReasoningCapabilityProfileId.GENERIC_OPENAI_COMPATIBLE
    )
    with pytest.raises(ValidationError):
        _settings(OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="invented-profile")


def test_openai_chat_profile_renders_supported_effort() -> None:
    settings = _settings(
        OPENPILOT_LLM_PROVIDER="openai",
        OPENPILOT_LLM_BASE_URL="https://api.openai.com/v1",
        OPENPILOT_LLM_MODEL="gpt-5.6-terra",
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="openai-chat-known",
    )
    policy = ReasoningPolicy(mode=ReasoningMode.ENABLED, effort=ReasoningEffort.LOW)

    resolved = resolve_reasoning_policy(policy, settings)

    assert resolved.resolution == ReasoningResolution.EXACT
    assert resolved.effective_effort == ReasoningEffort.LOW
    assert render_reasoning_transport(resolved) == {"reasoning_effort": "low"}


def test_deepseek_profile_maps_low_to_documented_high() -> None:
    settings = _settings(
        OPENPILOT_LLM_PROVIDER="deepseek",
        OPENPILOT_LLM_BASE_URL="https://api.deepseek.com",
        OPENPILOT_LLM_MODEL="deepseek-v4-pro",
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="deepseek-chat-known",
    )
    policy = ReasoningPolicy(
        mode=ReasoningMode.ENABLED,
        effort=ReasoningEffort.LOW,
        unsupported_behavior=UnsupportedReasoningBehavior.CLAMP,
    )

    resolved = resolve_reasoning_policy(policy, settings)

    assert resolved.resolution == ReasoningResolution.MAPPED
    assert resolved.effective_effort == ReasoningEffort.HIGH
    assert render_reasoning_transport(resolved) == {
        "reasoning_effort": "high",
        "extra_body": {"thinking": {"type": "enabled"}},
    }


def test_deepseek_profile_renders_explicit_non_thinking_mode() -> None:
    settings = _settings(
        OPENPILOT_LLM_PROVIDER="deepseek",
        OPENPILOT_LLM_BASE_URL="https://api.deepseek.com/v1",
        OPENPILOT_LLM_MODEL="deepseek-v4-flash",
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="deepseek-chat-known",
    )

    resolved = resolve_reasoning_policy(
        ReasoningPolicy(mode=ReasoningMode.DISABLED),
        settings,
    )

    assert resolved.resolution == ReasoningResolution.EXACT
    assert render_reasoning_transport(resolved) == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }


def test_llm_client_default_policy_does_not_add_transport_fields(monkeypatch) -> None:
    client = LLMClient(_settings(), enable_cache=False)
    captured = []
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())

    def fake_complete(_client, payload, **_kwargs):
        captured.append(payload)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
            usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 1}),
            model="custom-model",
            id="response-1",
            created=1,
        )

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_complete)

    client.complete(LLMRequest(messages=[LLMMessage(role="user", content="hello")]))

    assert "reasoning_effort" not in captured[0]
    assert "extra_body" not in captured[0]


def test_llm_client_renders_deepseek_policy_into_transport(monkeypatch) -> None:
    client = LLMClient(
        _settings(
            OPENPILOT_LLM_PROVIDER="deepseek",
            OPENPILOT_LLM_BASE_URL="https://api.deepseek.com",
            OPENPILOT_LLM_MODEL="deepseek-v4-pro",
            OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="deepseek-chat-known",
        ),
        enable_cache=False,
    )
    captured = []
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())

    def fake_complete(_client, payload, **_kwargs):
        captured.append(payload)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
            usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 1}),
            model="deepseek-v4-pro",
            id="response-1",
            created=1,
        )

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_complete)
    request = LLMRequest(
        messages=[LLMMessage(role="user", content="hello")],
        reasoning_policy=ReasoningPolicy(mode=ReasoningMode.DISABLED),
    )

    client.complete(request)

    assert captured[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_llm_client_disables_known_profile_default_for_structured_json(monkeypatch) -> None:
    client = LLMClient(
        _settings(
            OPENPILOT_LLM_PROVIDER="deepseek",
            OPENPILOT_LLM_BASE_URL="https://api.deepseek.com",
            OPENPILOT_LLM_MODEL="deepseek-v4-flash",
            OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="deepseek-chat-known",
        ),
        enable_cache=False,
    )
    captured = []
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())

    def fake_complete(_client, payload, **_kwargs):
        captured.append(payload)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"ok":true}'),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 1}),
            model="deepseek-v4-flash",
            id="response-structured-default",
            created=1,
        )

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_complete)

    response = client.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="Return JSON")],
            response_format="json_object",
        )
    )

    assert response.parsed_json == {"ok": True}
    assert captured[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_cache_identity_binds_provider_model_and_effective_policy() -> None:
    generic = LLMClient(_settings())
    openai = LLMClient(
        _settings(
            OPENPILOT_LLM_PROVIDER="openai",
            OPENPILOT_LLM_BASE_URL="https://api.openai.com/v1",
            OPENPILOT_LLM_MODEL="gpt-5.6-terra",
            OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="openai-chat-known",
        )
    )
    plain = LLMRequest(messages=[LLMMessage(role="user", content="same")])
    low = plain.model_copy(
        update={
            "reasoning_policy": ReasoningPolicy(
                mode=ReasoningMode.ENABLED,
                effort=ReasoningEffort.LOW,
            )
        }
    )

    assert generic._make_cache_key(plain) != openai._make_cache_key(plain)
    assert openai._make_cache_key(plain) != openai._make_cache_key(low)
    assert openai._make_cache_key(low).startswith("v2:sha256:")


def test_cache_identity_binds_non_default_endpoint_port() -> None:
    request = LLMRequest(messages=[LLMMessage(role="user", content="same")])
    first = LLMClient(
        _settings(OPENPILOT_LLM_BASE_URL="http://localhost:8000/v1"),
        enable_cache=False,
    )
    second = LLMClient(
        _settings(OPENPILOT_LLM_BASE_URL="http://localhost:9000/v1"),
        enable_cache=False,
    )

    assert first._make_cache_key(request) != second._make_cache_key(request)


def test_context_request_builder_preserves_reasoning_policy() -> None:
    client = SimpleNamespace(
        settings=SimpleNamespace(
            context_max_prompt_tokens=4096,
            context_reserved_prompt_tokens=128,
            tokenizer_path=None,
            model="custom-model",
        )
    )
    policy = ReasoningPolicy(
        mode=ReasoningMode.DISABLED,
        unsupported_behavior=UnsupportedReasoningBehavior.PROVIDER_DEFAULT,
    )

    request = build_context_llm_request(
        client,
        messages=[LLMMessage(role="user", content="decide")],
        purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
        reasoning_policy=policy,
    )

    assert request.reasoning_policy == policy
