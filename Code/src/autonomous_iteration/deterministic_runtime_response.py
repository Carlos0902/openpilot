"""Zero-provider response completion for authoritative runtime facts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from autonomous_iteration.iteration_turn_commit import (
    IterationTurnCommitter,
    assistant_payload_hash,
)
from autonomous_iteration.iteration_turn_reducer import IterationTurnReducer
from autonomous_iteration.iteration_turn_store import (
    IterationTurnConflictError,
    IterationTurnStore,
)
from autonomous_iteration.runtime_facts import RuntimeFactProjection
from metadata import (
    AssistantLedgerCommitState,
    AssistantTurnCommit,
    CompletedResponseOutcome,
    CompletionObligation,
    CompletionObligationKind,
    CompletionObligationStatus,
    GroundingDecision,
    GroundingStatus,
    IterationAuthorityState,
    IterationControlCursor,
    IterationDisposition,
    IterationPhase,
    IterationTurnRecordMetadata,
    PreTaskState,
    ResponseCandidate,
    ResponseClaim,
    RootDecisionBudget,
    SessionIngressState,
    SessionTurn,
)


@dataclass(frozen=True)
class DeterministicRuntimeResponse:
    content: str
    record: IterationTurnRecordMetadata
    ingress: SessionIngressState
    replayed: bool

    @property
    def core_success(self) -> None:
        return None

    @property
    def verification_applicable(self) -> bool:
        return False

    @property
    def project_improvement_requested(self) -> bool:
        return False


class DeterministicRuntimeResponseController:
    """Complete recognized runtime-fact questions without model or tool calls."""

    def __init__(
        self,
        store: IterationTurnStore,
        *,
        after_write: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.after_write = after_write

    def try_complete(
        self,
        goal: str,
        *,
        ingress: SessionIngressState,
        facts: RuntimeFactProjection,
    ) -> DeterministicRuntimeResponse | None:
        requested = self._requested_fact_keys(goal)
        if not requested:
            return None
        self._persist_ingress(ingress)
        user_turn = next(
            (
                turn
                for turn in reversed(ingress.turns)
                if turn.role == "user" and turn.identity.run_id == ingress.identity.run_id
            ),
            None,
        )
        if user_turn is None or user_turn.identity != ingress.identity or user_turn.content != goal:
            raise IterationTurnConflictError("runtime response requires the current durable user turn")
        latest = self.store.load_latest(
            ingress.identity.conversation_id,
            ingress.identity.run_id,
        )
        durable_initial: IterationTurnRecordMetadata | None = None
        if latest is not None:
            if latest.assistant_commit.state != AssistantLedgerCommitState.NONE:
                return self._recover(latest, ingress=ingress)
            durable_initial = latest

        values = self._fact_values(facts)
        selected = [(key, values[key]) for key in requested]
        content = "\n".join(f"{self._labels()[key]}: {value}" for key, value in selected)
        fact_evidence = {
            key: self._hash({"fact": key, "value": value}) for key, value in selected
        }
        obligations = tuple(
            CompletionObligation(
                obligation_id=f"runtime-fact:{key}",
                kind=CompletionObligationKind.RUNTIME_FACT,
                status=CompletionObligationStatus.SATISFIED,
                evidence_refs=(f"runtime-fact:{key}:{fact_evidence[key]}",),
            )
            for key, _value in selected
        )
        user_ref = self.store.save_artifact(
            ingress.identity.conversation_id,
            ingress.identity.run_id,
            kind="user_input",
            payload={
                "message_id": user_turn.message_id,
                "turn_index": user_turn.identity.turn_index,
                "content": user_turn.content,
            },
        )
        authority_hash = self._hash(
            {
                "ceiling": "response_only",
                "session_authority_hash": ingress.session_constraints.authority_hash,
            }
        )
        proposed_initial = IterationTurnRecordMetadata(
            record_id=self._stable_id("turn", ingress, user_turn.message_id),
            identity=ingress.identity,
            pre_task_state=PreTaskState(
                user_message_id=user_turn.message_id,
                user_input_ref=user_ref,
                session_authority_revision=ingress.session_constraints.revision,
                session_authority_hash=ingress.session_constraints.authority_hash,
                runtime_fact_hash=self._hash(facts.model_dump(mode="json")),
            ),
            cursor=IterationControlCursor(
                authority_state=IterationAuthorityState(
                    reason="Only a deterministic runtime-fact response is authorized.",
                    authority_hash=authority_hash,
                ),
            ),
            root_budget=RootDecisionBudget(),
            obligations=obligations,
        )
        if durable_initial is not None:
            durable_payload = durable_initial.to_json_dict()
            proposed_payload = proposed_initial.to_json_dict()
            for transient_field in ("created_at", "integrity_digest"):
                durable_payload.pop(transient_field, None)
                proposed_payload.pop(transient_field, None)
            if durable_payload != proposed_payload:
                raise IterationTurnConflictError(
                    "durable runtime response initialization differs from current facts"
                )
            initial = durable_initial
        else:
            initial = self.store.save(proposed_initial, expected_generation=0)
            self._after_write("initial_record")

        message_id = self._stable_id("assistant", ingress, user_turn.message_id)
        turn_index = ingress.identity.turn_index + 1
        payload = {"message_id": message_id, "turn_index": turn_index, "content": content}
        response_ref = self.store.save_artifact(
            ingress.identity.conversation_id,
            ingress.identity.run_id,
            kind="response_payload",
            payload=payload,
        )
        self._after_write("response_artifact")
        response_hash = assistant_payload_hash(payload)
        claims = tuple(
            ResponseClaim(
                claim_id=f"runtime:{key}",
                claim_hash=fact_evidence[key],
                source_class="runtime",
            )
            for key, _value in selected
        )
        candidate = ResponseCandidate(
            candidate_id=self._stable_id("candidate", ingress, user_turn.message_id),
            response_ref=response_ref,
            response_hash=response_hash,
            claims=claims,
        )
        obligation_ids = tuple(item.obligation_id for item in obligations)
        grounding = GroundingDecision(
            response_hash=response_hash,
            status=GroundingStatus.APPROVED,
            obligation_ids=obligation_ids,
            satisfied_obligation_ids=obligation_ids,
            candidate_claim_coverage="complete",
            evidence_refs=tuple(item.evidence_refs[0] for item in obligations),
        )
        outcome = CompletedResponseOutcome(
            response_ref=response_ref,
            response_hash=response_hash,
            grounding_decision_hash=grounding.canonical_hash,
        )
        pending = IterationTurnReducer.prepare_response(
            initial,
            cursor=initial.cursor.model_copy(
                update={
                    "phase": IterationPhase.COMPLETE,
                    "current_disposition": IterationDisposition.COMPLETE_RESPONSE,
                    "completion_candidate_hash": response_hash,
                }
            ),
            candidate=candidate,
            grounding=grounding,
            outcome=outcome,
            assistant_commit=AssistantTurnCommit(
                state=AssistantLedgerCommitState.PENDING,
                message_id=message_id,
                turn_index=turn_index,
                payload_ref=response_ref,
                payload_hash=response_hash,
            ),
        )
        result = IterationTurnCommitter(
            self.store,
            after_write=self.after_write,
        ).commit(pending)
        restored_ingress, _revision = self.store.load_ingress(ingress.identity.conversation_id)
        if restored_ingress is None:
            raise IterationTurnConflictError("assistant response committed without durable ingress")
        return DeterministicRuntimeResponse(
            content=result.payload,
            record=result.record,
            ingress=restored_ingress,
            replayed=result.replayed,
        )

    def _recover(
        self,
        latest: IterationTurnRecordMetadata,
        *,
        ingress: SessionIngressState,
    ) -> DeterministicRuntimeResponse:
        if latest.assistant_commit.state == AssistantLedgerCommitState.PENDING:
            result = IterationTurnCommitter(self.store).commit(latest)
            restored, _revision = self.store.load_ingress(ingress.identity.conversation_id)
            if restored is None:
                raise IterationTurnConflictError("assistant recovery lost durable ingress")
            return DeterministicRuntimeResponse(result.payload, result.record, restored, True)
        if latest.assistant_commit.state != AssistantLedgerCommitState.COMMITTED:
            raise IterationTurnConflictError("runtime response recovery found an incomplete decision")
        commit = latest.assistant_commit
        payload = self.store.load_artifact(
            latest.identity.conversation_id,
            latest.identity.run_id,
            commit.payload_ref,
        )
        if payload is None or assistant_payload_hash(payload) != commit.payload_hash:
            raise IterationTurnConflictError("committed runtime response artifact is unavailable")
        if (
            payload.get("message_id") != commit.message_id
            or payload.get("turn_index") != commit.turn_index
            or not isinstance(payload.get("content"), str)
        ):
            raise IterationTurnConflictError("committed runtime response payload identity differs")
        restored, _revision = self.store.load_ingress(ingress.identity.conversation_id)
        if restored is None:
            raise IterationTurnConflictError("committed runtime response ingress is unavailable")
        expected_turn = SessionTurn(
            identity=latest.identity.model_copy(update={"turn_index": commit.turn_index}),
            message_id=commit.message_id,
            role="assistant",
            content=payload["content"],
        )
        committed_turn = next(
            (turn for turn in restored.turns if turn.message_id == commit.message_id),
            None,
        )
        if committed_turn != expected_turn:
            raise IterationTurnConflictError(
                "committed runtime response differs from durable assistant ingress"
            )
        return DeterministicRuntimeResponse(payload["content"], latest, restored, True)

    def _persist_ingress(self, ingress: SessionIngressState) -> None:
        persisted, revision = self.store.load_ingress(ingress.identity.conversation_id)
        if persisted == ingress:
            return
        if persisted is not None:
            if (
                persisted.turns[: len(ingress.turns)] == ingress.turns
                and persisted.identity.conversation_id == ingress.identity.conversation_id
                and persisted.identity.run_id == ingress.identity.run_id
                and persisted.identity.project_root == ingress.identity.project_root
                and persisted.session_constraints.authority_hash
                == ingress.session_constraints.authority_hash
            ):
                return
            prefix = ingress.turns[: len(persisted.turns)]
            if prefix != persisted.turns:
                raise IterationTurnConflictError("session ingress history differs from durable ledger")
        self.store.save_ingress(ingress, expected_revision=revision)

    @staticmethod
    def _requested_fact_keys(goal: str) -> tuple[str, ...]:
        text = " ".join(str(goal or "").casefold().split())
        keys: list[str] = []
        if (
            (
                "模型" in text
                and (
                    ("你" in text and any(item in text for item in ("用", "使用", "是")))
                    or "当前模型" in text
                    or "openpilot 模型" in text
                )
            )
            or any(
                marker in text
                for marker in (
                    "what model are you",
                    "which model are you",
                    "model are you using",
                    "current model",
                    "openpilot model",
                )
            )
        ):
            keys.append("model")
        if any(
            marker in text
            for marker in (
                "你用什么 provider",
                "你使用哪个 provider",
                "当前提供商",
                "what provider are you",
                "which provider are you",
                "current provider",
            )
        ):
            keys.append("provider")
        if any(
            marker in text
            for marker in ("当前项目路径", "current project path", "current working directory")
        ):
            keys.append("project_path")
        if any(
            marker in text
            for marker in (
                "配置完整吗",
                "配置好了吗",
                "is configuration complete",
                "are you configured",
            )
        ):
            keys.append("configuration_complete")
        return tuple(dict.fromkeys(keys))

    @staticmethod
    def _fact_values(facts: RuntimeFactProjection) -> dict[str, str]:
        return {
            "model": facts.model,
            "provider": facts.provider,
            "project_path": facts.project_path or "not set",
            "configuration_complete": "yes" if facts.configuration_complete else "no",
        }

    @staticmethod
    def _labels() -> dict[str, str]:
        return {
            "model": "Model",
            "provider": "Provider",
            "project_path": "Project path",
            "configuration_complete": "Configuration complete",
        }

    @classmethod
    def _stable_id(cls, prefix: str, ingress: SessionIngressState, message_id: str) -> str:
        digest = cls._hash(
            {
                "conversation_id": ingress.identity.conversation_id,
                "run_id": ingress.identity.run_id,
                "message_id": message_id,
                "purpose": prefix,
            }
        ).removeprefix("sha256:")
        return f"{prefix}-{digest[:32]}"

    @staticmethod
    def _hash(payload: dict) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _after_write(self, boundary: str) -> None:
        if self.after_write is not None:
            self.after_write(boundary)


__all__ = ["DeterministicRuntimeResponse", "DeterministicRuntimeResponseController"]
