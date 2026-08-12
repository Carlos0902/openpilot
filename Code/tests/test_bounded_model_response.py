from __future__ import annotations

import pytest

from autonomous_iteration.bounded_model_response import (
    BoundedModelResponseController,
    BoundedResponseError,
    BoundedResponseFailureCode,
)
from autonomous_iteration.iteration_turn_store import IterationTurnStore
from autonomous_iteration.runtime_facts import RuntimeFactProjection
from core.llm import LLMResponse, LLMToolCall, LLMToolFunctionCall
from metadata import ConversationIdentity, SessionIngressState, SessionTurn


class _FakeClient:
    def __init__(self, responses: list[LLMResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls = []

    def complete(self, request, **kwargs):
        self.calls.append((request, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(payload, *, tool_calls=None, completion_tokens=20) -> LLMResponse:
    return LLMResponse(
        content="not relied on" if isinstance(payload, dict) else str(payload),
        parsed_json=payload if isinstance(payload, dict) else None,
        model="test-model",
        provider="test-provider",
        usage=(
            {"completion_tokens": completion_tokens}
            if completion_tokens is not None
            else {}
        ),
        tool_calls=tool_calls or [],
    )


def _ingress(*, content: str = "Why does the sky look blue?") -> SessionIngressState:
    identity = ConversationIdentity(
        conversation_id="conversation-1",
        run_id="run-1",
        turn_index=1,
        project_root="/tmp/project",
    )
    return SessionIngressState(
        identity=identity,
        turns=[
            SessionTurn(
                identity=identity,
                message_id="message-user-1",
                role="user",
                content=content,
            )
        ],
    )


def _facts() -> RuntimeFactProjection:
    return RuntimeFactProjection(
        provider="test-provider",
        model="test-model",
        project_path="/tmp/project",
        configuration_complete=True,
    )


def test_bounded_response_uses_zero_tools_and_commits_grounded_payload(tmp_path) -> None:
    client = _FakeClient(
        [_response({"response": "The sky looks blue due to light scattering.", "claims": [{"text": "The sky looks blue due to light scattering."}]})]
    )
    controller = BoundedModelResponseController(IterationTurnStore(tmp_path), client)

    result = controller.complete(
        "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
    )

    assert result.content == "The sky looks blue due to light scattering."
    assert result.evidence_required is False
    assert result.record.assistant_commit.state == "committed"
    assert result.record.task_binding.state == "none"
    assert result.record.root_budget.root_provider_calls_used == 1
    request, kwargs = client.calls[0]
    assert request.tools == []
    assert request.tool_choice is None
    assert request.response_format == "json_object"
    assert kwargs == {"max_retries": 1, "use_cache": False}


def test_invalid_claim_coverage_gets_exactly_one_bounded_repair(tmp_path) -> None:
    client = _FakeClient(
        [
            _response({"response": "Complete answer.", "claims": [{"text": "Partial"}]}),
            _response({"response": "Complete answer.", "claims": [{"text": "Complete answer."}]}),
        ]
    )
    controller = BoundedModelResponseController(IterationTurnStore(tmp_path), client)

    result = controller.complete(
        "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
    )

    assert result.content == "Complete answer."
    assert len(client.calls) == 2
    assert "Replace the invalid prior output completely" in client.calls[1][0].messages[0].content
    assert result.record.root_budget.root_provider_calls_used == 2
    assert result.record.root_budget.grounding_repairs_used == 1


def test_tool_calls_are_rejected_and_never_executed(tmp_path) -> None:
    tool_call = LLMToolCall(
        id="call-1",
        function=LLMToolFunctionCall(name="file_writer", arguments='{"path":"x"}'),
    )
    client = _FakeClient(
        [
            _response({}, tool_calls=[tool_call]),
            _response({}, tool_calls=[tool_call]),
        ]
    )
    controller = BoundedModelResponseController(IterationTurnStore(tmp_path), client)

    with pytest.raises(BoundedResponseError) as caught:
        controller.complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )

    assert caught.value.code == BoundedResponseFailureCode.PROVIDER_TOOL_CALL_REJECTED
    assert caught.value.record is not None
    assert caught.value.record.outcome.outcome == "failed"
    assert caught.value.record.cursor.phase == "stopped"
    assert len(client.calls) == 2
    assert all(call[0].tools == [] for call in client.calls)


def test_project_claim_becomes_typed_evidence_need_without_display(tmp_path) -> None:
    client = _FakeClient(
        [
            _response(
                {
                    "response": "The repository contains app.py.",
                    "claims": [{"text": "The repository contains app.py."}],
                }
            )
        ]
    )
    controller = BoundedModelResponseController(IterationTurnStore(tmp_path), client)

    result = controller.complete(
        "What is in this repository?",
        ingress=_ingress(content="What is in this repository?"),
        facts=_facts(),
    )

    assert result.content is None
    assert result.evidence_required is True
    assert result.record.cursor.phase == "evidence"
    assert result.record.grounding_decision.status == "evidence_required"
    assert result.record.assistant_commit.state == "none"
    assert len(result.ingress.turns) == 1


def test_current_turn_that_exceeds_context_budget_fails_before_provider(tmp_path) -> None:
    client = _FakeClient([])
    controller = BoundedModelResponseController(
        IterationTurnStore(tmp_path), client, max_context_chars=100
    )

    with pytest.raises(BoundedResponseError) as caught:
        controller.complete("x" * 1000, ingress=_ingress(content="x" * 1000), facts=_facts())

    assert caught.value.code == BoundedResponseFailureCode.CONTEXT_BUDGET_EXCEEDED
    assert client.calls == []


def test_unknown_usage_is_conservatively_charged_to_request_ceiling(tmp_path) -> None:
    client = _FakeClient(
        [
            _response({"response": "Complete.", "claims": [{"text": "partial"}]}, completion_tokens=None),
            _response({"response": "Complete.", "claims": [{"text": "Complete."}]}, completion_tokens=None),
        ]
    )
    controller = BoundedModelResponseController(IterationTurnStore(tmp_path), client)

    result = controller.complete(
        "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
    )

    assert result.record.root_budget.response_completion_tokens_used == 4000
    assert [call[0].max_tokens for call in client.calls] == [2000, 2000]


def test_provider_exception_becomes_redacted_controlled_stop(tmp_path) -> None:
    client = _FakeClient([RuntimeError("api_key=secret-value")])
    controller = BoundedModelResponseController(IterationTurnStore(tmp_path), client)

    with pytest.raises(BoundedResponseError) as caught:
        controller.complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )

    assert caught.value.code == BoundedResponseFailureCode.PROVIDER_CONTRACT_INVALID
    assert "secret-value" not in str(caught.value)
    assert caught.value.record is not None
    assert caught.value.record.outcome.outcome == "failed"


def test_runtime_claim_classification_requires_fact_semantics_not_name_only() -> None:
    source = BoundedModelResponseController._classify_claim(
        "Test Provider was founded long ago.",
        ingress=_ingress(),
        facts=_facts(),
    )
    runtime_source = BoundedModelResponseController._classify_claim(
        "The current provider is test-provider.",
        ingress=_ingress(),
        facts=_facts(),
    )

    assert source == "stable_knowledge"
    assert runtime_source == "runtime"


def test_context_limits_must_be_positive(tmp_path) -> None:
    with pytest.raises(ValueError, match="positive"):
        BoundedModelResponseController(
            IterationTurnStore(tmp_path), _FakeClient([]), max_turns=0
        )
