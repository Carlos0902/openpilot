from __future__ import annotations

from autonomous_iteration.bounded_model_response import BoundedModelResponseController
from autonomous_iteration.checkpoint_store import RuntimeCheckpointStore
from autonomous_iteration.evidence_escalation import (
    EvidenceEscalationController,
    EvidenceEscalationError,
    EvidenceEscalationFailureCode,
)
from autonomous_iteration.iteration_turn_store import IterationTurnStore
from autonomous_iteration.runtime_facts import RuntimeFactProjection
from core.llm import LLMResponse
from metadata import ConversationIdentity, ProjectFingerprint, SessionIngressState, SessionTurn


class _Client:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def complete(self, _request, **_kwargs) -> LLMResponse:
        return LLMResponse(
            content="json",
            parsed_json=self.payload,
            model="test-model",
            provider="test-provider",
            usage={"completion_tokens": 20},
        )


def _ingress(content: str) -> SessionIngressState:
    identity = ConversationIdentity(
        conversation_id="conversation-1",
        run_id="run-1",
        turn_index=1,
        project_root="/tmp/project",
    )
    return SessionIngressState(
        identity=identity,
        turns=[SessionTurn(identity=identity, message_id="message-user-1", role="user", content=content)],
    )


def _facts() -> RuntimeFactProjection:
    return RuntimeFactProjection(
        provider="test-provider",
        model="test-model",
        project_path="/tmp/project",
        configuration_complete=True,
    )


def _materialized(tmp_path):
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    question = "What is in this repository?"
    result = BoundedModelResponseController(
        turn_store,
        _Client({"response": "The repository contains app.py.", "claims": [{"text": "The repository contains app.py."}]}),
    ).complete(question, ingress=_ingress(question), facts=_facts())
    controller = EvidenceEscalationController(turn_store, checkpoint_store)
    fingerprint = ProjectFingerprint(project_root="/tmp/project", git_head="abc123", environment_id="env-1")
    active, needs = controller.materialize_read_only_task(
        result.record,
        current_ingress=result.ingress,
        project_fingerprint=fingerprint,
    )
    return turn_store, checkpoint_store, controller, active, needs


def test_open_obligation_becomes_read_only_need_and_task(tmp_path) -> None:
    _turn_store, checkpoint_store, _controller, active, needs = _materialized(tmp_path)
    assert len(needs) == 1
    assert needs[0].need_type == "project_structure"
    assert needs[0].attributes["read_only"] is True
    assert active.task_binding.state == "active"
    checkpoint = checkpoint_store.load_latest("run-1")
    assert checkpoint.runtime_state.execution_mode == "read_only"
    assert checkpoint.runtime_state.project_improvement_policy.requirement == "disabled"
    assert checkpoint.mutation_class == "read_only"


def test_current_external_obligation_becomes_web_search_need(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    question = "What is the latest weather?"
    result = BoundedModelResponseController(
        turn_store,
        _Client(
            {
                "response": "The latest weather is sunny.",
                "claims": [{"text": "The latest weather is sunny."}],
            }
        ),
    ).complete(question, ingress=_ingress(question), facts=_facts())
    needs = EvidenceEscalationController(turn_store, checkpoint_store).decision_needs(result.record)

    assert len(needs) == 1
    assert needs[0].need_type == "web_search"
    assert needs[0].query == "The latest weather is sunny."
    assert needs[0].attributes["source_class"] == "current_external"


def test_manifest_order_and_identity_are_integrity_bound(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    question = "What is in this repository?"
    result = BoundedModelResponseController(
        turn_store,
        _Client({"response": "The repository contains app.py. The repository contains tests.", "claims": [{"text": "The repository contains app.py."}, {"text": "The repository contains tests."}]}),
    ).complete(question, ingress=_ingress(question), facts=_facts())
    candidate = result.record.response_candidate
    manifest = turn_store.load_artifact("conversation-1", "run-1", candidate.claim_manifest_ref)
    reordered = turn_store.save_artifact(
        "conversation-1", "run-1", kind="response_claim_manifest", payload={"claims": list(reversed(manifest["claims"]))}
    )
    tampered = result.record.model_copy(update={"response_candidate": candidate.model_copy(update={"claim_manifest_ref": reordered})})
    controller = EvidenceEscalationController(turn_store, RuntimeCheckpointStore(tmp_path / "checkpoints"))
    try:
        controller.decision_needs(tampered)
    except EvidenceEscalationError as exc:
        assert exc.code == EvidenceEscalationFailureCode.CANDIDATE_INVALID
    else:
        raise AssertionError("tampered manifest was accepted")


def test_response_only_authority_cannot_materialize(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    question = "Why is the sky blue?"
    result = BoundedModelResponseController(
        turn_store,
        _Client({"response": "The repository contains app.py.", "claims": [{"text": "The repository contains app.py."}]}),
    ).complete(question, ingress=_ingress(question), facts=_facts())
    controller = EvidenceEscalationController(turn_store, RuntimeCheckpointStore(tmp_path / "checkpoints"))
    try:
        controller.materialize_read_only_task(
            result.record,
            current_ingress=result.ingress,
            project_fingerprint=ProjectFingerprint(project_root="/tmp/project"),
        )
    except EvidenceEscalationError as exc:
        assert exc.code == EvidenceEscalationFailureCode.AUTHORITY_STALE
    else:
        raise AssertionError("response-only authority was upgraded")
