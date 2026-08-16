from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.llm import LLMRequest, LLMResponse
from memory.rolling_summary_factory import build_llm_rolling_summary_request_factory
from metadata import ReasoningMode


def _candidates():
    return (
        SimpleNamespace(candidate_id="dialog-1", content="diagnosed the divide path"),
        SimpleNamespace(candidate_id="dialog-2", content="tests still need to run"),
    )


class _FakeLLM:
    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.requests: list[LLMRequest] = []
        self.complete_kwargs: list[dict[str, object]] = []

    def complete(self, request: LLMRequest, **kwargs) -> LLMResponse:
        self.requests.append(request)
        self.complete_kwargs.append(dict(kwargs))
        return self.response


def _response(**updates) -> LLMResponse:
    values = {
        "content": "{}",
        "parsed_json": {
            "goal_delta": "diagnosis narrowed",
            "verified_facts": ["divide path identified"],
            "decisions": ["keep API unchanged"],
            "open_issues": ["run tests"],
            "evidence_ids": ["dialog-1", "dialog-2"],
            "next_action": "run validation",
        },
        "model": "deepseek-chat",
        "provider": "deepseek",
        "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
        "finish_reason": "stop",
    }
    values.update(updates)
    return LLMResponse(**values)


@pytest.fixture
def patch_request_builder(monkeypatch):
    def fake_build(client, **kwargs):
        client.builder_kwargs = kwargs
        return LLMRequest(
            messages=kwargs["messages"],
            response_format=kwargs["response_format"],
            temperature=kwargs["temperature"],
            max_tokens=kwargs["max_tokens"],
            reasoning_policy=kwargs["reasoning_policy"],
            trace_info=kwargs["trace_info"],
        )

    monkeypatch.setattr(
        "memory.rolling_summary_factory.build_context_llm_request",
        fake_build,
    )


def test_factory_builds_provider_neutral_json_request_and_binds_source(
    patch_request_builder,
) -> None:
    client = _FakeLLM(_response())
    factory = build_llm_rolling_summary_request_factory(client)

    request = factory(_candidates(), 80)

    assert request is not None
    assert request.source_candidate_ids == ("dialog-1", "dialog-2")
    assert request.source_fingerprint.startswith("sha256:")
    assert request.provider_payload["evidence_ids"] == ["dialog-1", "dialog-2"]
    assert request.attempt is not None
    assert request.attempt.usage_observed is True
    assert request.attempt.finish_reason == "stop"
    assert client.requests[0].response_format == "json_object"
    assert client.requests[0].tools == []
    assert client.requests[0].reasoning_policy.mode is ReasoningMode.DISABLED
    assert client.requests[0].temperature == 0.0
    assert client.requests[0].max_tokens == 80
    assert client.complete_kwargs == [{"max_retries": 1}]
    assert "calculator.py" not in client.requests[0].messages[1].content


def test_factory_prefers_parsed_json_and_rejects_empty_or_malformed_payload(
    patch_request_builder,
) -> None:
    valid_client = _FakeLLM(_response(content="not-json"))
    valid = build_llm_rolling_summary_request_factory(valid_client)(_candidates(), 80)
    assert valid is not None
    assert valid.provider_payload["goal_delta"] == "diagnosis narrowed"

    for response in (
        _response(parsed_json=None, content=""),
        _response(parsed_json=None, content="{broken"),
    ):
        client = _FakeLLM(response)
        result = build_llm_rolling_summary_request_factory(client)(_candidates(), 80)
        assert result is not None
        assert result.provider_payload == {}


def test_factory_preserves_unknown_usage_and_finish_reason_for_adapter_gate(
    patch_request_builder,
) -> None:
    for response, expected_usage in (
        (_response(usage={}), False),
        (_response(usage={"prompt_tokens": 1}), False),
    ):
        client = _FakeLLM(response)
        result = build_llm_rolling_summary_request_factory(client)(_candidates(), 80)
        assert result is not None
        assert result.attempt is not None
        assert result.attempt.usage_observed is expected_usage

    client = _FakeLLM(_response(finish_reason="length"))
    result = build_llm_rolling_summary_request_factory(client)(_candidates(), 80)
    assert result is not None
    assert result.attempt is not None
    assert result.attempt.finish_reason == "length"


def test_factory_does_not_call_provider_when_dynamic_cap_is_zero(
    patch_request_builder,
) -> None:
    client = _FakeLLM(_response())
    factory = build_llm_rolling_summary_request_factory(client)

    assert factory(_candidates(), 0) is None
    assert client.requests == []
