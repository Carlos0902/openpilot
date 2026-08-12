from __future__ import annotations

import json

import pytest

from autonomous_iteration.bounded_model_response import (
    BoundedModelResponseController,
    BoundedResponseError,
    BoundedResponseFailureCode,
)
from autonomous_iteration.iteration_turn_store import (
    IterationTurnConflictError,
    IterationTurnStore,
)
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


class _CrashAfterBoundaryStore(IterationTurnStore):
    def __init__(self, root_dir, boundary: str) -> None:
        super().__init__(root_dir)
        self.boundary = boundary
        self.crashed = False

    def save(self, record, *, expected_generation=None):
        saved = super().save(record, expected_generation=expected_generation)
        if not self.crashed and str(saved.boundary) == self.boundary:
            self.crashed = True
            raise RuntimeError(f"crash after {self.boundary}")
        return saved


class _CrashAfterArtifactStore(IterationTurnStore):
    def __init__(self, root_dir, kind: str) -> None:
        super().__init__(root_dir)
        self.kind = kind
        self.crashed = False

    def save_artifact(self, conversation_id, run_id, *, kind, payload):
        reference = super().save_artifact(
            conversation_id,
            run_id,
            kind=kind,
            payload=payload,
        )
        if not self.crashed and kind == self.kind:
            self.crashed = True
            raise RuntimeError(f"crash after {kind} artifact")
        return reference


def _response(payload, *, tool_calls=None, completion_tokens=20) -> LLMResponse:
    return LLMResponse(
        content=(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if isinstance(payload, dict)
            else str(payload)
        ),
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


def test_pending_provider_request_recovery_fails_closed_without_replay(tmp_path) -> None:
    store = _CrashAfterBoundaryStore(tmp_path, "decision_requested")
    client = _FakeClient(
        [_response({"response": "Never sent.", "claims": [{"text": "Never sent."}]})]
    )
    controller = BoundedModelResponseController(store, client)

    with pytest.raises(RuntimeError, match="crash after decision_requested"):
        controller.complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )
    assert client.calls == []

    with pytest.raises(BoundedResponseError) as caught:
        BoundedModelResponseController(store, client).complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )

    assert caught.value.code == BoundedResponseFailureCode.PROVIDER_CONTRACT_INVALID
    assert "cannot be safely replayed" in str(caught.value)
    assert client.calls == []
    assert caught.value.record.cursor.pending_provider_request is None
    assert caught.value.record.outcome.outcome == "failed"


def test_observed_provider_response_recovers_without_provider_replay(tmp_path) -> None:
    store = _CrashAfterBoundaryStore(tmp_path, "decision_recorded")
    initial_client = _FakeClient(
        [
            _response(
                {
                    "response": "The sky looks blue due to scattering.",
                    "claims": [{"text": "The sky looks blue due to scattering."}],
                }
            )
        ]
    )

    with pytest.raises(RuntimeError, match="crash after decision_recorded"):
        BoundedModelResponseController(store, initial_client).complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )
    assert len(initial_client.calls) == 1
    observed = store.load_latest("conversation-1", "run-1")
    assert observed.cursor.observed_provider_response_ref is not None

    replay_client = _FakeClient([])
    result = BoundedModelResponseController(store, replay_client).complete(
        "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
    )

    assert replay_client.calls == []
    assert result.content == "The sky looks blue due to scattering."
    assert result.record.assistant_commit.state == "committed"
    assert result.record.root_budget.root_provider_calls_used == 1


def test_unbound_provider_response_artifact_does_not_authorize_replay(tmp_path) -> None:
    store = _CrashAfterArtifactStore(tmp_path, "provider_response")
    initial_client = _FakeClient(
        [
            _response(
                {
                    "response": "The sky looks blue due to scattering.",
                    "claims": [{"text": "The sky looks blue due to scattering."}],
                }
            )
        ]
    )

    with pytest.raises(RuntimeError, match="crash after provider_response artifact"):
        BoundedModelResponseController(store, initial_client).complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )
    assert len(initial_client.calls) == 1

    replay_client = _FakeClient([])
    with pytest.raises(BoundedResponseError) as caught:
        BoundedModelResponseController(store, replay_client).complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )

    assert caught.value.code == BoundedResponseFailureCode.PROVIDER_CONTRACT_INVALID
    assert replay_client.calls == []
    assert caught.value.record.outcome.outcome == "failed"


def test_invalid_observed_response_uses_only_remaining_repair_call(tmp_path) -> None:
    store = _CrashAfterBoundaryStore(tmp_path, "decision_recorded")
    initial_client = _FakeClient(
        [_response({"response": "Complete answer.", "claims": [{"text": "Partial"}]})]
    )
    with pytest.raises(RuntimeError, match="crash after decision_recorded"):
        BoundedModelResponseController(store, initial_client).complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )

    repair_client = _FakeClient(
        [_response({"response": "Complete answer.", "claims": [{"text": "Complete answer."}]})]
    )
    result = BoundedModelResponseController(store, repair_client).complete(
        "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
    )

    assert len(initial_client.calls) == 1
    assert len(repair_client.calls) == 1
    assert "Replace the invalid prior output completely" in repair_client.calls[0][0].messages[0].content
    assert result.content == "Complete answer."
    assert result.record.root_budget.root_provider_calls_used == 2
    assert result.record.root_budget.grounding_repairs_used == 1


def test_corrupt_observed_response_fails_closed_without_provider_replay(tmp_path) -> None:
    store = _CrashAfterBoundaryStore(tmp_path, "decision_recorded")
    initial_client = _FakeClient(
        [_response({"response": "Complete.", "claims": [{"text": "Complete."}]})]
    )
    with pytest.raises(RuntimeError, match="crash after decision_recorded"):
        BoundedModelResponseController(store, initial_client).complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )
    observed = store.load_latest("conversation-1", "run-1")
    reference = observed.cursor.observed_provider_response_ref
    artifact_path = (
        tmp_path
        / "conversation-1"
        / "run-1"
        / "artifacts"
        / f"{reference.artifact_id}.json"
    )
    artifact_path.write_text("{}", encoding="utf-8")

    replay_client = _FakeClient([])
    with pytest.raises(BoundedResponseError) as caught:
        BoundedModelResponseController(store, replay_client).complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )

    assert caught.value.code == BoundedResponseFailureCode.PROVIDER_CONTRACT_INVALID
    assert replay_client.calls == []
    assert caught.value.record.outcome.outcome == "failed"


def test_approved_candidate_recovery_commits_without_provider_replay(tmp_path) -> None:
    store = _CrashAfterBoundaryStore(tmp_path, "completion_candidate_recorded")
    initial_client = _FakeClient(
        [_response({"response": "Complete.", "claims": [{"text": "Complete."}]})]
    )

    with pytest.raises(RuntimeError, match="crash after completion_candidate_recorded"):
        BoundedModelResponseController(store, initial_client).complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )

    replay_client = _FakeClient([])
    result = BoundedModelResponseController(store, replay_client).complete(
        "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
    )

    assert replay_client.calls == []
    assert result.content == "Complete."
    assert result.record.assistant_commit.state == "committed"


def test_evidence_candidate_recovery_returns_same_candidate_without_provider_replay(
    tmp_path,
) -> None:
    store = _CrashAfterBoundaryStore(tmp_path, "completion_candidate_recorded")
    initial_client = _FakeClient(
        [
            _response(
                {
                    "response": "The repository uses SQLite.",
                    "claims": [{"text": "The repository uses SQLite."}],
                }
            )
        ]
    )

    with pytest.raises(RuntimeError, match="crash after completion_candidate_recorded"):
        BoundedModelResponseController(store, initial_client).complete(
            "What database does this project use?",
            ingress=_ingress(content="What database does this project use?"),
            facts=_facts(),
        )

    replay_client = _FakeClient([])
    result = BoundedModelResponseController(store, replay_client).complete(
        "What database does this project use?",
        ingress=_ingress(content="What database does this project use?"),
        facts=_facts(),
    )

    assert replay_client.calls == []
    assert result.content is None
    assert result.evidence_required is True
    assert result.record.response_candidate is not None


def test_pending_assistant_recovery_commits_without_provider_replay(tmp_path) -> None:
    store = _CrashAfterBoundaryStore(tmp_path, "completion_approved")
    initial_client = _FakeClient(
        [_response({"response": "Complete.", "claims": [{"text": "Complete."}]})]
    )

    with pytest.raises(RuntimeError, match="crash after completion_approved"):
        BoundedModelResponseController(store, initial_client).complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )

    replay_client = _FakeClient([])
    result = BoundedModelResponseController(store, replay_client).complete(
        "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
    )

    assert replay_client.calls == []
    assert result.content == "Complete."
    assert result.record.assistant_commit.state == "committed"


def test_committed_assistant_recovery_validates_ledger_without_provider_replay(
    tmp_path,
) -> None:
    store = IterationTurnStore(tmp_path)
    first = BoundedModelResponseController(
        store,
        _FakeClient(
            [_response({"response": "Complete.", "claims": [{"text": "Complete."}]})]
        ),
    ).complete("Why does the sky look blue?", ingress=_ingress(), facts=_facts())
    persisted, revision = store.load_ingress("conversation-1")
    assert persisted is not None
    corrupted_turn = persisted.turns[-1].model_copy(update={"content": "different"})
    store.save_ingress(
        persisted.model_copy(update={"turns": [persisted.turns[0], corrupted_turn]}),
        expected_revision=revision,
    )

    replay_client = _FakeClient([])
    with pytest.raises(IterationTurnConflictError, match="ledger"):
        BoundedModelResponseController(store, replay_client).complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )

    assert first.record.assistant_commit.state == "committed"
    assert replay_client.calls == []


@pytest.mark.parametrize("limit_delta, succeeds", [(0, True), (-1, False)])
def test_durable_provider_response_character_limit_is_exact(
    tmp_path,
    limit_delta,
    succeeds,
) -> None:
    response = _response(
        {"response": "Complete.", "claims": [{"text": "Complete."}]}
    )
    client = _FakeClient([response])
    controller = BoundedModelResponseController(
        IterationTurnStore(tmp_path / str(limit_delta)),
        client,
    )
    durable_length = len(controller._durable_provider_response_content(response))
    controller._MAX_DURABLE_PROVIDER_RESPONSE_CHARS = durable_length + limit_delta

    if succeeds:
        result = controller.complete(
            "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
        )
        assert result.content == "Complete."
    else:
        with pytest.raises(BoundedResponseError) as caught:
            controller.complete(
                "Why does the sky look blue?", ingress=_ingress(), facts=_facts()
            )
        assert caught.value.code == BoundedResponseFailureCode.PROVIDER_CONTRACT_INVALID
        assert "durable observation limit" in str(caught.value)
    assert len(client.calls) == 1


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


def test_general_project_advice_does_not_grant_read_authority(tmp_path) -> None:
    client = _FakeClient(
        [
            _response(
                {
                    "response": "Break work into small milestones.",
                    "claims": [{"text": "Break work into small milestones."}],
                }
            )
        ]
    )
    controller = BoundedModelResponseController(IterationTurnStore(tmp_path), client)
    goal = "How should I manage a software project?"

    result = controller.complete(
        goal,
        ingress=_ingress(content=goal),
        facts=_facts(),
    )

    assert result.record.cursor.authority_state.ceiling == "response_only"
    assert result.record.cursor.authority_state.source == "runtime_default"


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
