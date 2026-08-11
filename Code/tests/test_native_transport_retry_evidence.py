from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.config import LLMSettings
from core.exceptions import ErrorCategory, LLMProviderError
from core.llm import LLMClient, LLMMessage, LLMRequest
from core.reasoning import resolve_reasoning_policy


def _settings(*, retries: int) -> LLMSettings:
    return LLMSettings(
        OPENPILOT_LLM_API_KEY="test-key",
        OPENPILOT_LLM_PROVIDER="anthropic",
        OPENPILOT_LLM_BASE_URL="https://api.anthropic.com/v1",
        OPENPILOT_LLM_MODEL="claude-sonnet",
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="anthropic-messages-known",
        OPENPILOT_LLM_TRANSPORT_RETRIES=retries,
        OPENPILOT_LLM_RETRY_INITIAL_DELAY=0,
        OPENPILOT_LLM_RETRY_MAX_DELAY=0,
    )


def _request_and_policy(client: LLMClient):
    request = LLMRequest(messages=[LLMMessage(role="user", content="hello")])
    return request, resolve_reasoning_policy(request.reasoning_policy, client.settings)


def _response() -> SimpleNamespace:
    return SimpleNamespace(provider_details={})


def test_native_retry_is_bounded_and_records_redacted_history() -> None:
    client = LLMClient(_settings(retries=1), enable_cache=False)
    attempts = 0

    class FlakyTransport:
        def send_once(self, *_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise LLMProviderError(
                    "temporary failure test-key",
                    retryable=True,
                    category=ErrorCategory.RETRYABLE,
                )
            return _response()

    request, resolved = _request_and_policy(client)
    response = client._create_native_completion_with_transport_retry(
        FlakyTransport(), request, resolved
    )

    assert attempts == 2
    assert response.provider_details["transport_retry_history"] == [
        {
            "attempt": 1,
            "status": "failed",
            "category": "retryable",
            "retryable": True,
            "error_type": "LLMProviderError",
            "error": "temporary failure [REDACTED]",
        },
        {"attempt": 2, "status": "success", "retryable": False},
    ]


def test_native_retry_stops_after_terminal_provider_error() -> None:
    client = LLMClient(_settings(retries=3), enable_cache=False)
    attempts = 0

    class TerminalTransport:
        def send_once(self, *_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise LLMProviderError(
                "invalid request",
                retryable=False,
                category=ErrorCategory.VALIDATION,
            )

    request, resolved = _request_and_policy(client)
    with pytest.raises(LLMProviderError, match="invalid request") as exc_info:
        client._create_native_completion_with_transport_retry(
            TerminalTransport(), request, resolved
        )

    assert attempts == 1
    assert len(exc_info.value.context["transport_retry_history"]) == 1


def test_native_retry_rejects_unbounded_override() -> None:
    client = LLMClient(_settings(retries=0), enable_cache=False)
    request, resolved = _request_and_policy(client)

    with pytest.raises(ValueError, match="between 0 and 5"):
        client._create_native_completion_with_transport_retry(
            SimpleNamespace(send_once=lambda *_args, **_kwargs: _response()),
            request,
            resolved,
            transport_retries=6,
        )


def test_native_network_failure_retries_once_without_environment_proxy(monkeypatch) -> None:
    client = LLMClient(_settings(retries=0), enable_cache=False)
    monkeypatch.setattr(client, "_should_retry_without_env_proxy", lambda _exc: True)
    trust_env_values: list[bool] = []

    class ProxySensitiveTransport:
        def send_once(self, *_args, trust_env=True, **_kwargs):
            trust_env_values.append(trust_env)
            if trust_env:
                raise LLMProviderError(
                    "proxy connection failed",
                    retryable=True,
                    category=ErrorCategory.NETWORK,
                )
            return _response()

    request, resolved = _request_and_policy(client)
    response = client._create_native_completion_with_transport_retry(
        ProxySensitiveTransport(), request, resolved
    )

    assert trust_env_values == [True, False]
    assert response.provider_details["transport_retry_history"][-1] == {
        "attempt": 2,
        "status": "success",
        "retryable": False,
        "trust_env": False,
        "reason": "env_proxy_fallback",
    }


def test_native_proxy_fallback_failure_is_single_and_visible(monkeypatch) -> None:
    client = LLMClient(_settings(retries=1), enable_cache=False)
    monkeypatch.setattr(client, "_should_retry_without_env_proxy", lambda _exc: True)
    trust_env_values: list[bool] = []

    class FailingTransport:
        def send_once(self, *_args, trust_env=True, **_kwargs):
            trust_env_values.append(trust_env)
            raise LLMProviderError(
                "network unavailable",
                retryable=True,
                category=ErrorCategory.NETWORK,
            )

    request, resolved = _request_and_policy(client)
    with pytest.raises(LLMProviderError) as exc_info:
        client._create_native_completion_with_transport_retry(
            FailingTransport(), request, resolved
        )

    assert trust_env_values == [True, True, False]
    assert exc_info.value.context["transport_retry_history"][-1]["reason"] == (
        "env_proxy_fallback"
    )
