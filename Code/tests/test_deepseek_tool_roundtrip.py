from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from core.config import LLMSettings
from core.deepseek_roundtrip import (
    DeepSeekToolRoundTripError,
    append_deepseek_tool_round_trip,
)
from core.llm import (
    LLMClient,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
    LLMToolFunction,
    LLMToolFunctionCall,
    LLMToolResult,
    render_llm_message,
)
from core.reasoning import resolve_reasoning_policy
from core.provider_tool_roundtrip import ProviderToolRoundTripRunner
from metadata import ReasoningCapabilityProfileId, ReasoningMode, ReasoningPolicy


def _settings() -> LLMSettings:
    return LLMSettings(
        OPENPILOT_LLM_API_KEY="test-key",
        OPENPILOT_LLM_PROVIDER="deepseek",
        OPENPILOT_LLM_BASE_URL="https://api.deepseek.com/v1",
        OPENPILOT_LLM_MODEL="deepseek-v4-flash",
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="deepseek-chat-known",
    )


def _tool() -> LLMToolDefinition:
    return LLMToolDefinition(
        function=LLMToolFunction(
            name="get_weather",
            description="Get weather",
            parameters={"type": "object", "properties": {"city": {"type": "string"}}},
        )
    )


def _assistant_response() -> LLMResponse:
    return LLMResponse(
        content="",
        reasoning_content="I should call get_weather.",
        tool_calls=[
            LLMToolCall(
                id="call_1",
                function=LLMToolFunctionCall(
                    name="get_weather",
                    arguments='{"city":"Hangzhou"}',
                ),
            )
        ],
        model="deepseek-v4-flash",
        provider="deepseek",
        finish_reason="tool_calls",
    )


def _round_trip_runner_for_policy(
    policy: ReasoningPolicy | None,
    *,
    settings: LLMSettings | None = None,
) -> ProviderToolRoundTripRunner:
    """Build the smallest runner fixture for continuation-policy decisions."""

    settings = _settings() if settings is None else settings
    owner = SimpleNamespace(
        runtime=SimpleNamespace(llm_client=SimpleNamespace(settings=settings)),
        _reasoning_policy_for_task=lambda _task: policy,
    )
    runner = ProviderToolRoundTripRunner.__new__(ProviderToolRoundTripRunner)
    runner.owner = owner
    runner.runtime = owner.runtime
    runner.task = SimpleNamespace()
    runner._finalization_pending = False
    return runner


def test_message_contract_preserves_deepseek_assistant_fields() -> None:
    message = LLMMessage(
        role="assistant",
        content="",
        reasoning_content="plan",
        tool_calls=[_assistant_response().tool_calls[0]],
    )

    assert render_llm_message(message) == {
        "role": "assistant",
        "content": "",
        "reasoning_content": "plan",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Hangzhou"}'},
            }
        ],
    }


def test_message_contract_rejects_tool_fields_on_user_messages() -> None:
    with pytest.raises(ValidationError):
        LLMMessage(
            role="user",
            content="hello",
            reasoning_content="must not be here",
        )
    with pytest.raises(ValidationError):
        LLMMessage(role="tool", content="ok")


def test_deepseek_round_trip_appends_reasoning_assistant_and_matching_tool_results() -> None:
    messages = [LLMMessage(role="user", content="Weather in Hangzhou?")]
    next_messages = append_deepseek_tool_round_trip(
        messages,
        _assistant_response(),
        [LLMToolResult(tool_call_id="call_1", content="Cloudy 7~13°C")],
    )

    assert messages == [LLMMessage(role="user", content="Weather in Hangzhou?")]
    assert next_messages[1].role == "assistant"
    assert next_messages[1].reasoning_content == "I should call get_weather."
    assert next_messages[1].tool_calls[0].id == "call_1"
    assert next_messages[2] == LLMMessage(
        role="tool",
        content="Cloudy 7~13°C",
        tool_call_id="call_1",
    )
    assert render_llm_message(next_messages[1]) == {
        "role": "assistant",
        "content": "",
        "reasoning_content": "I should call get_weather.",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"city":"Hangzhou"}',
                },
            }
        ],
    }
    assert render_llm_message(next_messages[2]) == {
        "role": "tool",
        "content": "Cloudy 7~13°C",
        "tool_call_id": "call_1",
    }


@pytest.mark.parametrize(
    "results",
    [
        [],
        [LLMToolResult(tool_call_id="unknown", content="bad")],
        [
            LLMToolResult(tool_call_id="call_1", content="one"),
            LLMToolResult(tool_call_id="call_1", content="duplicate"),
        ],
    ],
)
def test_deepseek_round_trip_rejects_missing_unknown_or_duplicate_results(results) -> None:
    with pytest.raises(DeepSeekToolRoundTripError):
        append_deepseek_tool_round_trip([], _assistant_response(), results)


def test_deepseek_round_trip_rejects_tool_call_without_reasoning_content() -> None:
    response = _assistant_response().model_copy(update={"reasoning_content": None})
    with pytest.raises(DeepSeekToolRoundTripError, match="reasoning_content"):
        append_deepseek_tool_round_trip([], response, [LLMToolResult(tool_call_id="call_1", content="ok")])


def test_deepseek_round_trip_allows_missing_reasoning_when_explicitly_disabled() -> None:
    response = _assistant_response().model_copy(update={"reasoning_content": None})
    messages = append_deepseek_tool_round_trip(
        [],
        response,
        [LLMToolResult(tool_call_id="call_1", content="ok")],
        require_reasoning_content=False,
    )
    assert messages[0].role == "assistant"
    assert messages[0].reasoning_content is None
    assert messages[1].tool_call_id == "call_1"


def test_provider_default_allows_tool_continuation_without_reasoning_content() -> None:
    response = _assistant_response().model_copy(update={"reasoning_content": None})
    runner = _round_trip_runner_for_policy(
        ReasoningPolicy(mode=ReasoningMode.PROVIDER_DEFAULT)
    )

    assert runner._requires_reasoning_content(response) is False
    messages = append_deepseek_tool_round_trip(
        [],
        response,
        [LLMToolResult(tool_call_id="call_1", content="ok")],
        require_reasoning_content=runner._requires_reasoning_content(response),
    )
    assert messages[0].reasoning_content is None
    assert messages[1].tool_call_id == "call_1"


def test_provider_default_preserves_reasoning_content_when_provider_returns_it() -> None:
    runner = _round_trip_runner_for_policy(
        ReasoningPolicy(mode=ReasoningMode.PROVIDER_DEFAULT)
    )

    assert runner._requires_reasoning_content(_assistant_response()) is True


def test_finalization_pending_uses_disabled_continuation_semantics() -> None:
    response = _assistant_response().model_copy(update={"reasoning_content": None})
    runner = _round_trip_runner_for_policy(
        ReasoningPolicy(mode=ReasoningMode.PROVIDER_DEFAULT)
    )
    runner._finalization_pending = True

    assert runner._requires_reasoning_content(response) is False


def test_missing_reasoning_policy_or_settings_fails_closed() -> None:
    response = _assistant_response().model_copy(update={"reasoning_content": None})
    missing_policy = _round_trip_runner_for_policy(None)
    missing_settings = _round_trip_runner_for_policy(
        ReasoningPolicy(mode=ReasoningMode.PROVIDER_DEFAULT),
        settings=None,
    )
    missing_settings.runtime.llm_client.settings = None

    assert missing_policy._requires_reasoning_content(response) is True
    assert missing_settings._requires_reasoning_content(response) is True


@pytest.mark.parametrize(
    "mode",
    [ReasoningMode.ENABLED, ReasoningMode.ADAPTIVE],
)
def test_explicit_reasoning_requires_content_for_tool_continuation(
    mode: ReasoningMode,
) -> None:
    response = _assistant_response().model_copy(update={"reasoning_content": None})
    runner = _round_trip_runner_for_policy(ReasoningPolicy(mode=mode))

    assert runner._requires_reasoning_content(response) is True
    with pytest.raises(DeepSeekToolRoundTripError, match="reasoning_content"):
        append_deepseek_tool_round_trip(
            [],
            response,
            [LLMToolResult(tool_call_id="call_1", content="ok")],
            require_reasoning_content=runner._requires_reasoning_content(response),
        )


def test_llm_client_sends_tools_and_normalizes_reasoning_tool_calls(monkeypatch) -> None:
    client = LLMClient(_settings(), enable_cache=False)
    captured: list[dict] = []
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())

    def fake_complete(_client, payload, **_kwargs):
        captured.append(payload)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        reasoning_content="plan",
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                type="function",
                                function=SimpleNamespace(
                                    name="get_weather",
                                    arguments='{"city":"Hangzhou"}',
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 5}),
            model="deepseek-v4-flash",
            id="response-1",
            created=1,
        )

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_complete)
    request = LLMRequest(
        messages=[LLMMessage(role="user", content="Weather in Hangzhou?")],
        tools=[_tool()],
        reasoning_policy=ReasoningPolicy(mode=ReasoningMode.ENABLED),
    )

    result = client.complete(request, max_retries=1, use_cache=False)

    assert captured[0]["tools"] == [_tool().model_dump(mode="json")]
    assert result.reasoning_content == "plan"
    assert result.tool_calls[0].function.name == "get_weather"
    assert result.provider_details["reasoning_observation"]["reasoning_content_present"] is True


def test_llm_client_normalizes_mapping_tool_call_with_object_function(monkeypatch) -> None:
    client = LLMClient(_settings(), enable_cache=False)
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())

    def fake_complete(_client, _payload, **_kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message={
                        "content": "",
                        "reasoning_content": "plan",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": SimpleNamespace(
                                    name="get_weather",
                                    arguments='{"city":"Hangzhou"}',
                                ),
                            }
                        ],
                    },
                    finish_reason="tool_calls",
                )
            ],
            usage={},
            model="deepseek-v4-flash",
        )

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_complete)
    result = client.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="Weather")],
            tools=[_tool()],
            reasoning_policy=ReasoningPolicy(mode=ReasoningMode.ENABLED),
        ),
        max_retries=1,
        use_cache=False,
    )

    assert result.tool_calls[0].function.arguments == '{"city":"Hangzhou"}'


def test_llm_client_streams_and_reassembles_deepseek_tool_call(monkeypatch) -> None:
    client = LLMClient(_settings(), enable_cache=False)
    captured: list[dict] = []
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())

    def fake_stream(_client, payload, **_kwargs):
        captured.append(payload)
        return [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content="",
                            reasoning_content="plan ",
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call_1",
                                    type="function",
                                    function=SimpleNamespace(
                                        name="get_weather",
                                        arguments='{"city":',
                                    ),
                                )
                            ],
                        ),
                        finish_reason=None,
                    )
                ],
                usage=None,
                model="deepseek-v4-flash",
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content="",
                            reasoning_content="call weather",
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id=None,
                                    type="function",
                                    function=SimpleNamespace(
                                        name=None,
                                        arguments='"Hangzhou"}',
                                    ),
                                )
                            ],
                        ),
                        finish_reason="tool_calls",
                    )
                ],
                usage={},
                model="deepseek-v4-flash",
            ),
        ]

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_stream)
    events = []
    result = client.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="Weather")],
            tools=[_tool()],
            reasoning_policy=ReasoningPolicy(mode=ReasoningMode.ENABLED),
        ),
        max_retries=1,
        use_cache=False,
        stream_callback=events.append,
    )

    assert captured[0]["stream"] is True
    assert captured[0]["tools"] == [_tool().model_dump(mode="json")]
    assert result.reasoning_content == "plan call weather"
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].function.arguments == '{"city":"Hangzhou"}'
    assert result.provider_details["tool_call_count"] == 1


def test_llm_client_sends_required_tool_choice(monkeypatch) -> None:
    client = LLMClient(_settings(), enable_cache=False)
    captured: list[dict] = []
    monkeypatch.setattr(client, "_make_openai_client", lambda: object())

    def fake_complete(_client, payload, **_kwargs):
        captured.append(payload)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        reasoning_content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id="call_required",
                                type="function",
                                function=SimpleNamespace(
                                    name="get_weather",
                                    arguments='{"city":"Hangzhou"}',
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage={},
            model="deepseek-v4-flash",
        )

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_complete)
    result = client.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="Weather")],
            tools=[_tool()],
            tool_choice="required",
            reasoning_policy=ReasoningPolicy(mode=ReasoningMode.ENABLED),
        ),
        max_retries=1,
        use_cache=False,
    )

    assert captured[0]["tool_choice"] == "required"
    assert result.tool_calls[0].function.name == "get_weather"


def test_tool_round_trip_fields_change_resolved_cache_identity() -> None:
    settings = _settings()
    client = LLMClient(settings, enable_cache=False)
    policy = ReasoningPolicy(mode=ReasoningMode.ENABLED)
    base = LLMRequest(
        messages=[LLMMessage(role="user", content="Weather")],
        reasoning_policy=policy,
    )
    with_tool = base.model_copy(update={"tools": [_tool()]})

    assert client._make_cache_key(base) != client._make_cache_key(with_tool)
    assert resolve_reasoning_policy(policy, settings).profile_id == ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN
