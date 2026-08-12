from __future__ import annotations

from datetime import UTC, datetime

import pytest

from autonomous_iteration.bounded_model_response import BoundedModelResponseController
from autonomous_iteration.checkpoint_store import RuntimeCheckpointStore
from autonomous_iteration.evidence_escalation import (
    EvidenceEscalationController,
    EvidenceEscalationError,
    EvidenceEscalationFailureCode,
    EvidenceReceipt,
    EvidenceRuntimeBridge,
)
from autonomous_iteration.iteration_turn_store import IterationTurnStore
from autonomous_iteration.runtime_facts import RuntimeFactProjection
from core.llm import LLMResponse
from metadata import (
    ConversationIdentity,
    FileArtifactMetadata,
    ProjectFingerprint,
    ResultStatus,
    RuntimeCheckpointMetadata,
    SessionIngressState,
    SessionTurn,
    ToolInputMetadata,
    ToolResultMetadata,
)
from tools.tool_selection import SelectionReason, ToolSelection


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


def _candidate(turn_store, *, external: bool = False):
    question = "What is the latest weather?" if external else "What is in this repository?"
    answer = "The latest weather is sunny." if external else "The repository contains app.py."
    result = BoundedModelResponseController(
        turn_store,
        _Client({"response": answer, "claims": [{"text": answer}]}),
    ).complete(question, ingress=_ingress(question), facts=_facts())
    assert result.evidence_required is True
    return result


def _materialized(tmp_path, *, external: bool = False):
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    candidate = _candidate(turn_store, external=external)
    controller = EvidenceEscalationController(
        turn_store,
        checkpoint_store,
        now=lambda: datetime(2026, 8, 11, tzinfo=UTC),
    )
    fingerprint = ProjectFingerprint(
        project_root="/tmp/project",
        git_head="abc123",
        environment_id="env-1",
    )
    active, needs = controller.materialize_read_only_task(
        candidate.record,
        current_ingress=candidate.ingress,
        project_fingerprint=fingerprint,
    )
    return turn_store, checkpoint_store, controller, candidate.ingress, active, needs, fingerprint


def _receipt_and_checkpoint(
    turn_store,
    checkpoint_store,
    controller,
    ingress,
    active,
    fingerprint,
    *,
    observed_at: datetime,
):
    obligation = next(item for item in active.obligations if item.is_blocking)
    source_class = "current_external" if obligation.kind == "current_external_fact" else "project"
    payload = {
        "obligation_id": obligation.obligation_id,
        "source_class": source_class,
        "observed_at": observed_at.isoformat(),
        "evidence": {"summary": "source-compatible observed evidence"},
    }
    reference = turn_store.save_artifact(
        "conversation-1",
        "run-1",
        kind="observed_evidence",
        payload=payload,
    )
    content_hash = controller.evidence_payload_hash(payload)
    initial = checkpoint_store.load_latest("run-1")
    state = initial.runtime_state.model_copy(deep=True)
    state.known_facts.append(f"evidence:{reference.artifact_id}:{content_hash}")
    checkpoint = RuntimeCheckpointMetadata(
        checkpoint_id="evidence-checkpoint-2",
        generation=2,
        run_id="run-1",
        root_task_id=initial.root_task_id,
        session_id="run-1",
        checkpoint_reason="read-only evidence observed",
        safe_boundary="tool_result_applied",
        runtime_state=state,
        session_ingress_state=ingress,
        side_effect_state="applied",
        tool_name="web_searcher" if obligation.kind == "current_external_fact" else "file_reader",
        mutation_class="read_only",
        project_fingerprint=fingerprint,
    )
    checkpoint = checkpoint_store.save(checkpoint, expected_generation=1)
    receipt = EvidenceReceipt(
        obligation_id=obligation.obligation_id,
        source_class=source_class,
        artifact_ref=reference,
        content_hash=content_hash,
        checkpoint_id=checkpoint.checkpoint_id,
        checkpoint_digest=checkpoint.integrity_checksum,
        project_fingerprint_hash=(
            controller.project_fingerprint_hash(fingerprint)
            if source_class == "project"
            else None
        ),
        observed_at=observed_at,
    )
    return receipt, checkpoint


def test_open_obligation_becomes_read_only_decision_need_and_task(tmp_path) -> None:
    (
        _turn_store,
        checkpoint_store,
        _controller,
        _ingress_state,
        active,
        needs,
        _fingerprint,
    ) = _materialized(tmp_path)

    assert len(needs) == 1
    assert needs[0].need_type == "project_structure"
    assert needs[0].attributes["read_only"] is True
    assert needs[0].attributes["read_only_listing"] is True
    assert active.task_binding.state == "active"
    checkpoint = checkpoint_store.load_latest("run-1")
    assert checkpoint.runtime_state.execution_mode == "read_only"
    assert checkpoint.runtime_state.core_success is None
    assert checkpoint.runtime_state.project_improvement_policy.requirement == "disabled"
    assert checkpoint.mutation_class == "read_only"


def test_source_compatible_evidence_reenters_same_completion_gate(tmp_path) -> None:
    turn_store, checkpoint_store, controller, ingress, active, _needs, fingerprint = _materialized(
        tmp_path
    )
    receipt, checkpoint = _receipt_and_checkpoint(
        turn_store,
        checkpoint_store,
        controller,
        ingress,
        active,
        fingerprint,
        observed_at=datetime(2026, 8, 11, tzinfo=UTC),
    )

    completed = controller.absorb_and_complete(
        active,
        current_ingress=ingress,
        current_project_fingerprint=fingerprint,
        receipts=(receipt,),
    )

    assert completed.assistant_commit.state == "committed"
    assert completed.task_binding.state == "none"
    assert completed.core_success is None
    assert completed.grounding_decision.status == "approved"
    assert checkpoint.runtime_state.core_success is None
    restored, _revision = turn_store.load_ingress("conversation-1")
    assert restored.turns[-1].content == "The repository contains app.py."


def test_runtime_bridge_binds_exact_tool_observation_to_later_checkpoint(tmp_path) -> None:
    turn_store, checkpoint_store, controller, ingress, active, _needs, fingerprint = _materialized(
        tmp_path
    )
    obligation = next(item for item in active.obligations if item.is_blocking)
    selection = ToolSelection(
        step_id="evidence-read-1",
        tool_name="multi_file_reader",
        reason=SelectionReason.CAPABILITY_MATCH,
        confidence=1.0,
        input_metadata=ToolInputMetadata.from_mapping(
            "multi_file_reader",
            {
                "directory_path": "/tmp/project",
                "obligation_id": obligation.obligation_id,
                "source_class": "project",
                "read_only": True,
            },
        ),
    )
    execution_result = type(
        "ExecutionResult",
        (),
        {
            "success": True,
            "output_metadata": ToolResultMetadata(
                tool_name="multi_file_reader",
                status=ResultStatus.SUCCESS,
                result=FileArtifactMetadata(
                    file_path="/tmp/project",
                    files=["app.py"],
                    content="repository observation",
                ),
            ),
            "error": None,
        },
    )()
    bridge = EvidenceRuntimeBridge(
        controller,
        active,
        current_ingress=ingress,
        current_project_fingerprint=fingerprint,
    )

    pending = bridge.observe(selection, execution_result)
    initial = checkpoint_store.load_latest("run-1")
    state = initial.runtime_state.model_copy(deep=True)
    state.add_fact(pending.marker)
    checkpoint = checkpoint_store.save(
        RuntimeCheckpointMetadata(
            checkpoint_id="runtime-evidence-checkpoint",
            generation=2,
            run_id="run-1",
            root_task_id=initial.root_task_id,
            session_id="run-1",
            checkpoint_reason="real read applied",
            safe_boundary="tool_result_applied",
            runtime_state=state,
            session_ingress_state=ingress,
            side_effect_state="applied",
            tool_name="multi_file_reader",
            step_id=selection.step_id,
            tool_input=selection.input_metadata,
            mutation_class="read_only",
            project_fingerprint=fingerprint,
        ),
        expected_generation=1,
    )

    receipt = bridge.bind_checkpoint(pending, checkpoint)

    assert receipt.obligation_id == obligation.obligation_id
    assert receipt.checkpoint_id == checkpoint.checkpoint_id
    assert bridge.receipts == (receipt,)
    payload = turn_store.load_artifact("conversation-1", "run-1", receipt.artifact_ref)
    assert payload["evidence"]["tool_name"] == "multi_file_reader"
    assert payload["evidence"]["output"]["result"]["files"] == ["app.py"]


def test_runtime_bridge_rejects_checkpoint_without_exact_marker(tmp_path) -> None:
    _turn_store, checkpoint_store, controller, ingress, active, _needs, fingerprint = _materialized(
        tmp_path
    )
    obligation = next(item for item in active.obligations if item.is_blocking)
    selection = ToolSelection(
        step_id="evidence-read-1",
        tool_name="multi_file_reader",
        reason=SelectionReason.CAPABILITY_MATCH,
        confidence=1.0,
        input_metadata=ToolInputMetadata.from_mapping(
            "multi_file_reader",
            {
                "directory_path": "/tmp/project",
                "obligation_id": obligation.obligation_id,
                "source_class": "project",
                "read_only": True,
            },
        ),
    )
    execution_result = type(
        "ExecutionResult",
        (),
        {
            "success": True,
            "output_metadata": ToolResultMetadata(
                tool_name="multi_file_reader",
                status=ResultStatus.SUCCESS,
                result=FileArtifactMetadata(file_path="/tmp/project", content="observation"),
            ),
            "error": None,
        },
    )()
    bridge = EvidenceRuntimeBridge(
        controller,
        active,
        current_ingress=ingress,
        current_project_fingerprint=fingerprint,
    )
    pending = bridge.observe(selection, execution_result)
    initial = checkpoint_store.load_latest("run-1")
    checkpoint = checkpoint_store.save(
        RuntimeCheckpointMetadata(
            checkpoint_id="missing-marker-checkpoint",
            generation=2,
            run_id="run-1",
            root_task_id=initial.root_task_id,
            session_id="run-1",
            checkpoint_reason="invalid read applied",
            safe_boundary="tool_result_applied",
            runtime_state=initial.runtime_state,
            session_ingress_state=ingress,
            side_effect_state="applied",
            tool_name="multi_file_reader",
            step_id=selection.step_id,
            mutation_class="read_only",
            project_fingerprint=fingerprint,
        ),
        expected_generation=1,
    )

    with pytest.raises(EvidenceEscalationError, match="marker"):
        bridge.bind_checkpoint(pending, checkpoint)


def test_mixed_source_obligations_bind_separate_task_checkpoints(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    question = "What is in this repository and what is the latest weather?"
    response = "The repository contains app.py. The latest weather is sunny."
    candidate = BoundedModelResponseController(
        turn_store,
        _Client(
            {
                "response": response,
                "claims": [
                    {"text": "The repository contains app.py."},
                    {"text": "The latest weather is sunny."},
                ],
            }
        ),
    ).complete(question, ingress=_ingress(question), facts=_facts())
    controller = EvidenceEscalationController(
        turn_store,
        checkpoint_store,
        now=lambda: datetime(2026, 8, 11, tzinfo=UTC),
    )
    fingerprint = ProjectFingerprint(
        project_root="/tmp/project",
        git_head="abc123",
        environment_id="env-1",
    )
    active, _needs = controller.materialize_read_only_task(
        candidate.record,
        current_ingress=candidate.ingress,
        project_fingerprint=fingerprint,
    )
    state = checkpoint_store.load_latest("run-1").runtime_state.model_copy(deep=True)
    receipts: list[EvidenceReceipt] = []
    for generation, obligation in enumerate(
        (item for item in active.obligations if item.is_blocking),
        start=2,
    ):
        source = "project" if obligation.kind == "project_fact" else "current_external"
        observed_at = datetime(2026, 8, 11, tzinfo=UTC)
        payload = {
            "obligation_id": obligation.obligation_id,
            "source_class": source,
            "observed_at": observed_at.isoformat(),
            "evidence": {"summary": f"evidence for {source}"},
        }
        reference = turn_store.save_artifact(
            "conversation-1",
            "run-1",
            kind="observed_evidence",
            payload=payload,
        )
        content_hash = controller.evidence_payload_hash(payload)
        state.known_facts.append(f"evidence:{reference.artifact_id}:{content_hash}")
        checkpoint = checkpoint_store.save(
            RuntimeCheckpointMetadata(
                checkpoint_id=f"evidence-checkpoint-{generation}",
                generation=generation,
                run_id="run-1",
                root_task_id=active.task_binding.task_id,
                session_id="run-1",
                checkpoint_reason="source-compatible evidence observed",
                safe_boundary="tool_result_applied",
                runtime_state=state.model_copy(deep=True),
                session_ingress_state=candidate.ingress,
                side_effect_state="applied",
                tool_name="file_reader" if source == "project" else "web_searcher",
                mutation_class="read_only",
                project_fingerprint=fingerprint,
            ),
            expected_generation=generation - 1,
        )
        receipts.append(
            EvidenceReceipt(
                obligation_id=obligation.obligation_id,
                source_class=source,
                artifact_ref=reference,
                content_hash=content_hash,
                checkpoint_id=checkpoint.checkpoint_id,
                checkpoint_digest=checkpoint.integrity_checksum,
                project_fingerprint_hash=(
                    controller.project_fingerprint_hash(fingerprint)
                    if source == "project"
                    else None
                ),
                observed_at=observed_at,
            )
        )

    completed = controller.absorb_and_complete(
        active,
        current_ingress=candidate.ingress,
        current_project_fingerprint=fingerprint,
        receipts=tuple(receipts),
    )

    assert completed.assistant_commit.state == "committed"
    restored, _revision = turn_store.load_ingress("conversation-1")
    assert restored.turns[-1].content == response


def test_stale_project_fingerprint_cannot_close_obligation(tmp_path) -> None:
    turn_store, checkpoint_store, controller, ingress, active, _needs, fingerprint = _materialized(
        tmp_path
    )
    receipt, _checkpoint = _receipt_and_checkpoint(
        turn_store,
        checkpoint_store,
        controller,
        ingress,
        active,
        fingerprint,
        observed_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    stale = receipt.model_copy(update={"project_fingerprint_hash": "sha256:" + "f" * 64})

    with pytest.raises(EvidenceEscalationError) as caught:
        controller.absorb_and_complete(
            active,
            current_ingress=ingress,
            current_project_fingerprint=fingerprint,
            receipts=(stale,),
        )

    assert caught.value.code == EvidenceEscalationFailureCode.PROJECT_STALE


def test_current_project_drift_cannot_close_prior_evidence(tmp_path) -> None:
    turn_store, checkpoint_store, controller, ingress, active, _needs, fingerprint = _materialized(
        tmp_path
    )
    receipt, _checkpoint = _receipt_and_checkpoint(
        turn_store,
        checkpoint_store,
        controller,
        ingress,
        active,
        fingerprint,
        observed_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    drifted = fingerprint.model_copy(update={"git_head": "def456"})

    with pytest.raises(EvidenceEscalationError) as caught:
        controller.absorb_and_complete(
            active,
            current_ingress=ingress,
            current_project_fingerprint=drifted,
            receipts=(receipt,),
        )

    assert caught.value.code == EvidenceEscalationFailureCode.PROJECT_STALE


def test_project_evidence_cannot_use_external_search_checkpoint(tmp_path) -> None:
    turn_store, checkpoint_store, controller, ingress, active, _needs, fingerprint = _materialized(
        tmp_path
    )
    receipt, checkpoint = _receipt_and_checkpoint(
        turn_store,
        checkpoint_store,
        controller,
        ingress,
        active,
        fingerprint,
        observed_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    mismatched = checkpoint.model_copy(
        update={
            "checkpoint_id": "evidence-checkpoint-wrong-source",
            "generation": 3,
            "tool_name": "web_searcher",
            "integrity_checksum": None,
        }
    )
    mismatched = checkpoint_store.save(mismatched, expected_generation=2)
    mismatched_receipt = receipt.model_copy(
        update={
            "checkpoint_id": mismatched.checkpoint_id,
            "checkpoint_digest": mismatched.integrity_checksum,
        }
    )

    with pytest.raises(EvidenceEscalationError) as caught:
        controller.absorb_and_complete(
            active,
            current_ingress=ingress,
            current_project_fingerprint=fingerprint,
            receipts=(mismatched_receipt,),
        )

    assert caught.value.code == EvidenceEscalationFailureCode.SOURCE_INCOMPATIBLE


def test_stale_current_external_evidence_cannot_close_obligation(tmp_path) -> None:
    turn_store, checkpoint_store, controller, ingress, active, _needs, fingerprint = _materialized(
        tmp_path, external=True
    )
    receipt, _checkpoint = _receipt_and_checkpoint(
        turn_store,
        checkpoint_store,
        controller,
        ingress,
        active,
        fingerprint,
        observed_at=datetime(2026, 8, 10, tzinfo=UTC),
    )

    with pytest.raises(EvidenceEscalationError) as caught:
        controller.absorb_and_complete(
            active,
            current_ingress=ingress,
            current_project_fingerprint=fingerprint,
            receipts=(receipt,),
        )

    assert caught.value.code == EvidenceEscalationFailureCode.EXTERNAL_EVIDENCE_STALE


def test_future_current_external_evidence_cannot_close_obligation(tmp_path) -> None:
    turn_store, checkpoint_store, controller, ingress, active, _needs, fingerprint = _materialized(
        tmp_path, external=True
    )
    receipt, _checkpoint = _receipt_and_checkpoint(
        turn_store,
        checkpoint_store,
        controller,
        ingress,
        active,
        fingerprint,
        observed_at=datetime(2026, 8, 11, 0, 1, tzinfo=UTC),
    )

    with pytest.raises(EvidenceEscalationError) as caught:
        controller.absorb_and_complete(
            active,
            current_ingress=ingress,
            current_project_fingerprint=fingerprint,
            receipts=(receipt,),
        )

    assert caught.value.code == EvidenceEscalationFailureCode.EXTERNAL_EVIDENCE_STALE


def test_receipt_cannot_relabel_artifact_observation_time(tmp_path) -> None:
    turn_store, checkpoint_store, controller, ingress, active, _needs, fingerprint = _materialized(
        tmp_path, external=True
    )
    receipt, _checkpoint = _receipt_and_checkpoint(
        turn_store,
        checkpoint_store,
        controller,
        ingress,
        active,
        fingerprint,
        observed_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    relabeled = receipt.model_copy(
        update={"observed_at": datetime(2026, 8, 10, tzinfo=UTC)}
    )

    with pytest.raises(EvidenceEscalationError) as caught:
        controller.absorb_and_complete(
            active,
            current_ingress=ingress,
            current_project_fingerprint=fingerprint,
            receipts=(relabeled,),
        )

    assert caught.value.code == EvidenceEscalationFailureCode.ARTIFACT_INVALID


def test_extra_receipt_cannot_be_smuggled_past_open_obligations(tmp_path) -> None:
    turn_store, checkpoint_store, controller, ingress, active, _needs, fingerprint = _materialized(
        tmp_path
    )
    receipt, _checkpoint = _receipt_and_checkpoint(
        turn_store,
        checkpoint_store,
        controller,
        ingress,
        active,
        fingerprint,
        observed_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    extra = receipt.model_copy(update={"obligation_id": "ground:unowned"})

    with pytest.raises(EvidenceEscalationError) as caught:
        controller.absorb_and_complete(
            active,
            current_ingress=ingress,
            current_project_fingerprint=fingerprint,
            receipts=(receipt, extra),
        )

    assert caught.value.code == EvidenceEscalationFailureCode.SOURCE_INCOMPATIBLE


def test_evidence_checkpoint_must_bind_current_session_authority(tmp_path) -> None:
    turn_store, checkpoint_store, controller, ingress, active, _needs, fingerprint = _materialized(
        tmp_path
    )
    receipt, checkpoint = _receipt_and_checkpoint(
        turn_store,
        checkpoint_store,
        controller,
        ingress,
        active,
        fingerprint,
        observed_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    stale_ingress = ingress.model_copy(deep=True)
    stale_ingress.session_constraints.revision = ingress.session_constraints.revision + 1
    stale_state = checkpoint.runtime_state.model_copy(
        update={"session_constraints": stale_ingress.session_constraints}
    )
    stale_checkpoint = checkpoint.model_copy(
        update={
            "checkpoint_id": "evidence-checkpoint-stale-authority",
            "generation": 3,
            "session_ingress_state": stale_ingress,
            "runtime_state": stale_state,
            "integrity_checksum": None,
        }
    )
    stale_checkpoint = checkpoint_store.save(stale_checkpoint, expected_generation=2)
    stale_receipt = receipt.model_copy(
        update={
            "checkpoint_id": stale_checkpoint.checkpoint_id,
            "checkpoint_digest": stale_checkpoint.integrity_checksum,
        }
    )

    with pytest.raises(EvidenceEscalationError) as caught:
        controller.absorb_and_complete(
            active,
            current_ingress=ingress,
            current_project_fingerprint=fingerprint,
            receipts=(stale_receipt,),
        )

    assert caught.value.code == EvidenceEscalationFailureCode.AUTHORITY_STALE


def test_claim_manifest_order_and_identity_are_integrity_bound(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    result = BoundedModelResponseController(
        turn_store,
        _Client(
            {
                "response": "The repository contains app.py. The repository contains tests.",
                "claims": [
                    {"text": "The repository contains app.py."},
                    {"text": "The repository contains tests."},
                ],
            }
        ),
    ).complete(
        "What is in this repository?",
        ingress=_ingress("What is in this repository?"),
        facts=_facts(),
    )
    candidate = result.record.response_candidate
    manifest = turn_store.load_artifact("conversation-1", "run-1", candidate.claim_manifest_ref)
    reordered_ref = turn_store.save_artifact(
        "conversation-1",
        "run-1",
        kind="response_claim_manifest",
        payload={"claims": list(reversed(manifest["claims"]))},
    )
    tampered = result.record.model_copy(
        update={
            "response_candidate": candidate.model_copy(
                update={"claim_manifest_ref": reordered_ref}
            )
        }
    )

    with pytest.raises(EvidenceEscalationError) as caught:
        EvidenceEscalationController(
            turn_store,
            RuntimeCheckpointStore(tmp_path / "checkpoints"),
        ).decision_needs(tampered)

    assert caught.value.code == EvidenceEscalationFailureCode.CANDIDATE_INVALID


def test_non_evidence_claim_source_cannot_be_routed_as_external_evidence(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    result = _candidate(turn_store)
    candidate = result.record.response_candidate
    claim = candidate.claims[0].model_copy(update={"source_class": "stable_knowledge"})
    manifest = turn_store.load_artifact(
        "conversation-1",
        "run-1",
        candidate.claim_manifest_ref,
    )
    manifest["claims"][0]["source_class"] = "stable_knowledge"
    manifest_ref = turn_store.save_artifact(
        "conversation-1",
        "run-1",
        kind="response_claim_manifest",
        payload=manifest,
    )
    tampered = result.record.model_copy(
        update={
            "response_candidate": candidate.model_copy(
                update={"claims": (claim,), "claim_manifest_ref": manifest_ref}
            )
        }
    )

    with pytest.raises(EvidenceEscalationError) as caught:
        EvidenceEscalationController(
            turn_store,
            RuntimeCheckpointStore(tmp_path / "checkpoints"),
        ).decision_needs(tampered)

    assert caught.value.code == EvidenceEscalationFailureCode.SOURCE_INCOMPATIBLE


@pytest.mark.parametrize(
    "boundary",
    ["evidence_complete", "pending_record", "assistant_ingress", "committed_record"],
)
def test_evidence_completion_recovers_each_durable_write_boundary(
    tmp_path,
    boundary: str,
) -> None:
    turn_store, checkpoint_store, controller, ingress, active, _needs, fingerprint = _materialized(
        tmp_path
    )
    receipt, _checkpoint = _receipt_and_checkpoint(
        turn_store,
        checkpoint_store,
        controller,
        ingress,
        active,
        fingerprint,
        observed_at=datetime(2026, 8, 11, tzinfo=UTC),
    )

    def crash_after_write(observed: str) -> None:
        if observed == boundary:
            raise RuntimeError(f"crash after {boundary}")

    crashing = EvidenceEscalationController(
        turn_store,
        checkpoint_store,
        now=lambda: datetime(2026, 8, 11, tzinfo=UTC),
        after_write=crash_after_write,
    )
    with pytest.raises(RuntimeError, match=boundary):
        crashing.absorb_and_complete(
            active,
            current_ingress=ingress,
            current_project_fingerprint=fingerprint,
            receipts=(receipt,),
        )

    completed = controller.absorb_and_complete(
        active,
        current_ingress=ingress,
        current_project_fingerprint=fingerprint,
        receipts=(receipt,),
    )

    assert completed.assistant_commit.state == "committed"
    restored, _revision = turn_store.load_ingress("conversation-1")
    assistant_turns = [turn for turn in restored.turns if turn.role == "assistant"]
    assert [turn.content for turn in assistant_turns] == ["The repository contains app.py."]


def test_model_project_claim_cannot_upgrade_response_only_user_authority(tmp_path) -> None:
    turn_store = IterationTurnStore(tmp_path / "turns")
    checkpoint_store = RuntimeCheckpointStore(tmp_path / "checkpoints")
    question = "Why is the sky blue?"
    result = BoundedModelResponseController(
        turn_store,
        _Client(
            {
                "response": "The repository contains app.py.",
                "claims": [{"text": "The repository contains app.py."}],
            }
        ),
    ).complete(question, ingress=_ingress(question), facts=_facts())
    controller = EvidenceEscalationController(turn_store, checkpoint_store)

    with pytest.raises(EvidenceEscalationError) as caught:
        controller.materialize_read_only_task(
            result.record,
            current_ingress=result.ingress,
            project_fingerprint=ProjectFingerprint(project_root="/tmp/project"),
        )

    assert caught.value.code == EvidenceEscalationFailureCode.AUTHORITY_STALE
