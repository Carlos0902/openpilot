from __future__ import annotations

import pytest

from autonomous_iteration.deterministic_runtime_response import (
    DeterministicRuntimeResponseController,
)
from autonomous_iteration.iteration_turn_store import IterationTurnStore
from autonomous_iteration.iteration_turn_store import IterationTurnConflictError
from autonomous_iteration.runtime_facts import RuntimeFactProjection
from metadata import ConversationIdentity, SessionIngressState, SessionTurn


def _ingress() -> SessionIngressState:
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
                content="你使用的是什么模型？",
            )
        ],
    )


def _facts() -> RuntimeFactProjection:
    return RuntimeFactProjection(
        provider="openai",
        model="gpt-5.6",
        project_path="/tmp/project",
        configuration_complete=True,
    )


def test_runtime_fact_question_completes_without_provider_or_task(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)

    response = DeterministicRuntimeResponseController(store).try_complete(
        "你使用的是什么模型？", ingress=_ingress(), facts=_facts()
    )

    assert response is not None
    assert response.content == "Model: gpt-5.6"
    assert response.core_success is None
    assert response.verification_applicable is False
    assert response.project_improvement_requested is False
    assert response.record.core_success is None
    assert response.record.task_binding.state == "none"
    assert response.record.assistant_commit.state == "committed"
    assert response.ingress.turns[-1].content == response.content
    assert response.ingress.turns[-1].role == "assistant"


def test_runtime_fact_completion_replays_same_durable_message(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)
    controller = DeterministicRuntimeResponseController(store)
    first = controller.try_complete("你使用的是什么模型？", ingress=_ingress(), facts=_facts())

    second = controller.try_complete("你使用的是什么模型？", ingress=_ingress(), facts=_facts())

    assert first is not None and second is not None
    assert second.replayed is True
    assert second.content == first.content
    assert second.record == first.record
    assert len(second.ingress.turns) == 2


def test_unrecognized_goal_does_not_claim_the_unified_fast_completion(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)

    response = DeterministicRuntimeResponseController(store).try_complete(
        "Fix calculator.py", ingress=_ingress(), facts=_facts()
    )

    assert response is None
    assert store.load_ingress("conversation-1") == (None, 0)


def test_model_recommendation_question_is_not_misclassified_as_runtime_fact(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)

    response = DeterministicRuntimeResponseController(store).try_complete(
        "我该用什么模型写这个项目？", ingress=_ingress(), facts=_facts()
    )

    assert response is None


def test_committed_replay_rejects_divergent_assistant_ingress(tmp_path) -> None:
    store = IterationTurnStore(tmp_path)
    controller = DeterministicRuntimeResponseController(store)
    first = controller.try_complete("你使用的是什么模型？", ingress=_ingress(), facts=_facts())
    assert first is not None
    persisted, revision = store.load_ingress("conversation-1")
    assert persisted is not None
    altered = persisted.turns[-1].model_copy(update={"content": "different"})
    store.save_ingress(
        persisted.model_copy(update={"turns": [persisted.turns[0], altered]}),
        expected_revision=revision,
    )

    with pytest.raises(IterationTurnConflictError, match="durable assistant ingress"):
        controller.try_complete("你使用的是什么模型？", ingress=_ingress(), facts=_facts())


@pytest.mark.parametrize(
    "fault_boundary",
    [
        "initial_record",
        "response_artifact",
        "pending_record",
        "assistant_ingress",
        "committed_record",
    ],
)
def test_runtime_fact_completion_recovers_every_write_without_provider(
    tmp_path, fault_boundary
) -> None:
    store = IterationTurnStore(tmp_path)
    fired = False

    def fail_once(boundary: str) -> None:
        nonlocal fired
        if boundary == fault_boundary and not fired:
            fired = True
            raise RuntimeError(f"crash after {boundary}")

    with pytest.raises(RuntimeError, match="crash after"):
        DeterministicRuntimeResponseController(store, after_write=fail_once).try_complete(
            "你使用的是什么模型？", ingress=_ingress(), facts=_facts()
        )

    recovered = DeterministicRuntimeResponseController(store).try_complete(
        "你使用的是什么模型？", ingress=_ingress(), facts=_facts()
    )

    assert recovered is not None
    assert recovered.content == "Model: gpt-5.6"
    assert recovered.record.assistant_commit.state == "committed"
    assert [turn.role for turn in recovered.ingress.turns] == ["user", "assistant"]
