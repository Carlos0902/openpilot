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
