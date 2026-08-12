"""Bounded zero-tool model response step for pre-task autonomous iteration."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from autonomous_iteration.iteration_turn_commit import (
    IterationTurnCommitter,
    assistant_payload_hash,
)
from autonomous_iteration.iteration_turn_reducer import IterationTurnReducer
from autonomous_iteration.iteration_turn_store import IterationTurnConflictError, IterationTurnStore
from autonomous_iteration.runtime_facts import RuntimeFactProjection
from core.llm import LLMClient, LLMMessage, LLMRequest, LLMResponse
from metadata import (
    AssistantLedgerCommitState,
    AssistantTurnCommit,
    ClaimSourceClass,
    CompletedResponseOutcome,
    CompletionObligation,
    CompletionObligationKind,
    CompletionObligationStatus,
    ControlledStopOutcome,
    GroundingDecision,
    GroundingStatus,
    IterationAuthorityState,
    IterationAuthorityCeiling,
    IterationAuthoritySource,
    IterationControlCursor,
    IterationDisposition,
    IterationPendingProviderRequest,
    IterationPhase,
    IterationOutcome,
    IterationStopReason,
    IterationTurnRecordMetadata,
    PreTaskState,
    ResponseCandidate,
    ResponseClaim,
    RootDecisionBudget,
    SessionIngressState,
)


class BoundedResponseFailureCode(str, Enum):
    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"
    PROVIDER_CONTRACT_INVALID = "provider_contract_invalid"
    PROVIDER_TOOL_CALL_REJECTED = "provider_tool_call_rejected"
    RESPONSE_BUDGET_EXHAUSTED = "response_budget_exhausted"


class BoundedResponseError(RuntimeError):
    def __init__(
        self,
        code: BoundedResponseFailureCode,
        message: str,
        *,
        record: IterationTurnRecordMetadata | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.record = record


class _ModelClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)


class _ModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: str = Field(min_length=1, max_length=64_000)
    claims: tuple[_ModelClaim, ...] = Field(min_length=1, max_length=64)


class BoundedSessionProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str
    run_id: str
    turns: tuple[dict[str, Any], ...]
    required_constraints: tuple[str, ...] = ()
    omitted_turns: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class BoundedModelResponseResult:
    record: IterationTurnRecordMetadata
    ingress: SessionIngressState
    content: str | None
    evidence_required: bool
    evidence_obligation_ids: tuple[str, ...] = ()


class BoundedModelResponseController:
    """Execute at most one initial and one repair request with no tools."""

    def __init__(
        self,
        store: IterationTurnStore,
        client: LLMClient,
        *,
        max_context_chars: int = 12_000,
        max_turns: int = 12,
    ) -> None:
        if max_context_chars <= 0 or max_turns <= 0:
            raise ValueError("bounded response context limits must be positive")
        self.store = store
        self.client = client
        self.max_context_chars = max_context_chars
        self.max_turns = max_turns

    def complete(
        self,
        goal: str,
        *,
        ingress: SessionIngressState,
        facts: RuntimeFactProjection,
    ) -> BoundedModelResponseResult:
        projection = self._project_session(ingress)
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
            raise IterationTurnConflictError("bounded response requires the current user turn")
        if self.store.load_latest(ingress.identity.conversation_id, ingress.identity.run_id) is not None:
            raise IterationTurnConflictError("bounded response run already has durable state")

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
        authority = self._initial_authority(goal, ingress)
        initial = IterationTurnRecordMetadata(
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
                authority_state=authority
            ),
            root_budget=RootDecisionBudget(max_root_provider_calls=2),
        )
        current = self.store.save(initial, expected_generation=0)
        last_failure = BoundedResponseFailureCode.PROVIDER_CONTRACT_INVALID
        repair_preview = ""
        parsed: _ModelResponse | None = None
        for ordinal in (1, 2):
            purpose = "response" if ordinal == 1 else "grounding_repair"
            remaining_tokens = (
                current.root_budget.max_response_completion_tokens
                - current.root_budget.response_completion_tokens_used
            )
            if remaining_tokens <= 0:
                self._raise_failure(
                    current,
                    BoundedResponseFailureCode.RESPONSE_BUDGET_EXHAUSTED,
                    "bounded response token budget was exhausted before repair",
                )
            request = self._request(
                projection,
                facts=facts,
                repair_preview=repair_preview if ordinal == 2 else "",
                max_tokens=min(
                    2000,
                    remaining_tokens,
                ),
            )
            request_payload = {
                "messages": [message.model_dump(mode="json") for message in request.messages],
                "response_format": request.response_format,
                "max_tokens": request.max_tokens,
                "tools": [],
            }
            request_hash = self._hash(request_payload)
            request_ref = self.store.save_artifact(
                ingress.identity.conversation_id,
                ingress.identity.run_id,
                kind="provider_request",
                payload=request_payload,
            )
            requested_budget = current.root_budget.model_copy(
                update={
                    "decision_rounds_used": current.root_budget.decision_rounds_used + 1,
                    "root_provider_calls_used": current.root_budget.root_provider_calls_used + 1,
                    "grounding_repairs_used": (
                        current.root_budget.grounding_repairs_used + (1 if ordinal == 2 else 0)
                    ),
                }
            )
            requested = IterationTurnReducer.request_provider(
                current,
                request=IterationPendingProviderRequest(
                    request_id=self._stable_id(f"request-{ordinal}", ingress, user_turn.message_id),
                    request_ordinal=ordinal,
                    request_hash=request_hash,
                    request_ref=request_ref,
                    purpose=purpose,
                ),
                root_budget=requested_budget,
            )
            requested = self.store.save(requested, expected_generation=current.generation)
            try:
                response = self.client.complete(request, max_retries=1, use_cache=False)
            except Exception:
                self._raise_failure(
                    requested,
                    BoundedResponseFailureCode.PROVIDER_CONTRACT_INVALID,
                    "provider request failed inside the bounded response step",
                )
            if not isinstance(response, LLMResponse):
                self._raise_failure(
                    requested,
                    BoundedResponseFailureCode.PROVIDER_CONTRACT_INVALID,
                    "provider returned an invalid bounded response envelope",
                )
            usage = self._completion_tokens(response, fallback=request.max_tokens or 0)
            observed_budget = requested.root_budget.model_copy(
                update={
                    "response_completion_tokens_used": (
                        requested.root_budget.response_completion_tokens_used + usage
                    )
                }
            )
            if (
                observed_budget.response_completion_tokens_used
                > observed_budget.max_response_completion_tokens
            ):
                self._raise_failure(
                    requested,
                    BoundedResponseFailureCode.RESPONSE_BUDGET_EXHAUSTED,
                    "bounded response token usage exceeded the root budget",
                )
            raw_ref = self.store.save_artifact(
                ingress.identity.conversation_id,
                ingress.identity.run_id,
                kind="provider_response",
                payload={
                    "content": response.content[:64_000],
                    "finish_reason": response.finish_reason,
                    "completion_tokens": usage,
                    "tool_call_count": len(response.tool_calls),
                },
            )
            progress_signature = self._hash(
                {
                    "request_hash": request_hash,
                    "response_ref": raw_ref.model_dump(mode="json"),
                }
            )
            current = IterationTurnReducer.finish_provider_request(
                requested,
                root_budget=observed_budget,
                progress_signature=progress_signature,
            )
            current = self.store.save(current, expected_generation=requested.generation)
            try:
                attempt_failure = BoundedResponseFailureCode.PROVIDER_CONTRACT_INVALID
                if response.tool_calls:
                    attempt_failure = BoundedResponseFailureCode.PROVIDER_TOOL_CALL_REJECTED
                    raise ValueError("provider returned a tool call on a zero-tool request")
                parsed = self._parse_response(response)
                self._validate_claim_coverage(parsed)
                break
            except (ValueError, ValidationError, json.JSONDecodeError) as exc:
                last_failure = attempt_failure
                repair_preview = self._bounded_preview(response.content, exc)
                if ordinal == 2:
                    self._raise_failure(
                        current,
                        last_failure,
                        "provider response remained invalid after one bounded repair",
                    )
        if parsed is None:
            self._raise_failure(current, last_failure, "provider response was unavailable")
        return self._ground_and_commit(
            current,
            parsed=parsed,
            ingress=ingress,
            facts=facts,
            user_message_id=user_turn.message_id,
        )

    def _ground_and_commit(
        self,
        record: IterationTurnRecordMetadata,
        *,
        parsed: _ModelResponse,
        ingress: SessionIngressState,
        facts: RuntimeFactProjection,
        user_message_id: str,
    ) -> BoundedModelResponseResult:
        payload = {
            "message_id": self._stable_id("assistant", ingress, user_message_id),
            "turn_index": ingress.identity.turn_index + 1,
            "content": parsed.response,
        }
        response_ref = self.store.save_artifact(
            ingress.identity.conversation_id,
            ingress.identity.run_id,
            kind="response_payload",
            payload=payload,
        )
        response_hash = assistant_payload_hash(payload)
        claims: list[ResponseClaim] = []
        obligations: list[CompletionObligation] = []
        evidence_required: list[str] = []
        evidence_refs: list[str] = []
        for index, model_claim in enumerate(parsed.claims, start=1):
            source_class = self._classify_claim(model_claim.text, ingress=ingress, facts=facts)
            claim_hash = self._hash({"claim": model_claim.text})
            claim_id = f"claim-{index}-{claim_hash.removeprefix('sha256:')[:12]}"
            claims.append(
                ResponseClaim(
                    claim_id=claim_id,
                    claim_hash=claim_hash,
                    source_class=source_class,
                )
            )
            obligation_id = f"ground:{claim_id}"
            needs_evidence = source_class in {
                ClaimSourceClass.PROJECT,
                ClaimSourceClass.CURRENT_EXTERNAL,
            }
            if needs_evidence:
                evidence_required.append(obligation_id)
                obligations.append(
                    CompletionObligation(
                        obligation_id=obligation_id,
                        kind=(
                            CompletionObligationKind.PROJECT_FACT
                            if source_class == ClaimSourceClass.PROJECT
                            else CompletionObligationKind.CURRENT_EXTERNAL_FACT
                        ),
                    )
                )
            else:
                evidence_ref = self._claim_evidence_ref(
                    model_claim.text,
                    source_class=source_class,
                    ingress=ingress,
                    facts=facts,
                )
                evidence_refs.append(evidence_ref)
                obligations.append(
                    CompletionObligation(
                        obligation_id=obligation_id,
                        kind=(
                            CompletionObligationKind.RUNTIME_FACT
                            if source_class == ClaimSourceClass.RUNTIME
                            else CompletionObligationKind.ACCEPTANCE
                        ),
                        status=CompletionObligationStatus.SATISFIED,
                        evidence_refs=(evidence_ref,),
                    )
                )
        claim_manifest_ref = self.store.save_artifact(
            ingress.identity.conversation_id,
            ingress.identity.run_id,
            kind="response_claim_manifest",
            payload={
                "claims": [
                    {
                        "claim_id": claim.claim_id,
                        "claim_hash": claim.claim_hash,
                        "source_class": claim.source_class,
                        "text": model_claim.text,
                    }
                    for claim, model_claim in zip(claims, parsed.claims, strict=True)
                ]
            },
        )
        candidate = ResponseCandidate(
            candidate_id=self._stable_id("candidate", ingress, user_message_id),
            response_ref=response_ref,
            response_hash=response_hash,
            claims=tuple(claims),
            claim_manifest_ref=claim_manifest_ref,
        )
        obligation_ids = tuple(item.obligation_id for item in obligations)
        satisfied_ids = tuple(item.obligation_id for item in obligations if item.is_closed)
        status = GroundingStatus.EVIDENCE_REQUIRED if evidence_required else GroundingStatus.APPROVED
        grounding = GroundingDecision(
            response_hash=response_hash,
            status=status,
            obligation_ids=obligation_ids,
            satisfied_obligation_ids=satisfied_ids,
            open_obligation_ids=tuple(evidence_required),
            candidate_claim_coverage="complete",
            evidence_refs=tuple(evidence_refs),
        )
        candidate_record = IterationTurnReducer.record_response_candidate(
            record,
            cursor=record.cursor.model_copy(
                update={
                    "phase": IterationPhase.EVIDENCE if evidence_required else IterationPhase.GROUND_RESPONSE,
                    "current_disposition": (
                        IterationDisposition.FORM_SINGLE_TASK
                        if evidence_required
                        else IterationDisposition.COMPLETE_RESPONSE
                    ),
                    "open_obligation_ids": tuple(evidence_required),
                    "completion_candidate_hash": response_hash,
                }
            ),
            candidate=candidate,
            grounding=grounding,
            obligations=tuple(obligations),
        )
        candidate_record = self.store.save(candidate_record, expected_generation=record.generation)
        if evidence_required:
            restored, _revision = self.store.load_ingress(ingress.identity.conversation_id)
            if restored is None:
                raise IterationTurnConflictError("bounded response lost durable ingress")
            return BoundedModelResponseResult(
                record=candidate_record,
                ingress=restored,
                content=None,
                evidence_required=True,
                evidence_obligation_ids=tuple(evidence_required),
            )
        outcome = CompletedResponseOutcome(
            response_ref=response_ref,
            response_hash=response_hash,
            grounding_decision_hash=grounding.canonical_hash,
        )
        pending = IterationTurnReducer.prepare_response(
            candidate_record,
            cursor=candidate_record.cursor.model_copy(update={"phase": IterationPhase.COMPLETE}),
            candidate=candidate,
            grounding=grounding,
            outcome=outcome,
            assistant_commit=AssistantTurnCommit(
                state=AssistantLedgerCommitState.PENDING,
                message_id=payload["message_id"],
                turn_index=payload["turn_index"],
                payload_ref=response_ref,
                payload_hash=response_hash,
            ),
        )
        result = IterationTurnCommitter(self.store).commit(pending)
        restored, _revision = self.store.load_ingress(ingress.identity.conversation_id)
        if restored is None:
            raise IterationTurnConflictError("bounded response lost committed ingress")
        return BoundedModelResponseResult(result.record, restored, result.payload, False)

    def _project_session(self, ingress: SessionIngressState) -> BoundedSessionProjection:
        required_constraints = tuple(
            item.required_context_text()
            for item in ingress.session_constraints.active_entries
            if item.required_context_text()
        )
        constraint_chars = len(self._encode(required_constraints))
        selected: list[dict[str, Any]] = []
        remaining = self.max_context_chars - constraint_chars
        if remaining <= 0:
            raise BoundedResponseError(
                BoundedResponseFailureCode.CONTEXT_BUDGET_EXCEEDED,
                "required session constraints exceed bounded response context",
            )
        for turn in reversed(ingress.turns[-self.max_turns :]):
            item = {
                "message_id": turn.message_id,
                "turn_index": turn.identity.turn_index,
                "role": turn.role,
                "content": turn.content,
            }
            size = len(self._encode(item))
            if size > remaining:
                if not selected:
                    raise BoundedResponseError(
                        BoundedResponseFailureCode.CONTEXT_BUDGET_EXCEEDED,
                        "current user turn exceeds bounded response context",
                    )
                break
            selected.append(item)
            remaining -= size
        selected.reverse()
        return BoundedSessionProjection(
            conversation_id=ingress.identity.conversation_id,
            run_id=ingress.identity.run_id,
            turns=tuple(selected),
            required_constraints=required_constraints,
            omitted_turns=len(ingress.turns) - len(selected),
        )

    @classmethod
    def _initial_authority(
        cls,
        goal: str,
        ingress: SessionIngressState,
    ) -> IterationAuthorityState:
        text = " ".join(goal.casefold().split())
        project_markers = (
            "repository",
            "repo",
            "codebase",
            "project",
            "项目",
            "仓库",
            "代码库",
        )
        project_read_markers = (
            "what is in",
            "what's in",
            "show",
            "list",
            "which file",
            "inspect",
            "read",
            "check",
            "search",
            "find",
            "contain",
            "structure",
            "architecture",
            "本项目",
            "这个项目",
            "当前项目",
            "仓库里",
            "项目里",
            "有什么",
            "有哪些",
            "查看",
            "检查",
            "读取",
            "列出",
            "搜索",
            "查找",
            "结构",
            "架构",
        )
        current_markers = (
            "latest",
            "current",
            "today",
            "right now",
            "最新",
            "当前",
            "今天",
            "现在",
        )
        current_subjects = ("weather", "price", "news", "status", "天气", "价格", "新闻", "状态")
        direct_question = re.match(r"^(?:what|which|who|where|when|how much|多少|什么|哪个|谁|哪里)", text)
        project_read = any(marker in text for marker in project_markers) and any(
            marker in text for marker in project_read_markers
        )
        current_read = any(marker in text for marker in current_markers) or (
            bool(direct_question) and any(marker in text for marker in current_subjects)
        )
        read_eligible = project_read or current_read
        ceiling = (
            IterationAuthorityCeiling.READ_ONLY_ELIGIBLE
            if read_eligible
            else IterationAuthorityCeiling.RESPONSE_ONLY
        )
        source = (
            IterationAuthoritySource.USER_INTENT
            if read_eligible
            else IterationAuthoritySource.RUNTIME_DEFAULT
        )
        return IterationAuthorityState(
            ceiling=ceiling,
            source=source,
            reason=(
                "The user explicitly requested read-only project or current-fact evidence."
                if read_eligible
                else "The bounded response step has response-only authority."
            ),
            authority_hash=cls._hash(
                {
                    "ceiling": ceiling.value,
                    "source": source.value,
                    "session_authority_hash": ingress.session_constraints.authority_hash,
                }
            ),
        )

    def _request(
        self,
        projection: BoundedSessionProjection,
        *,
        facts: RuntimeFactProjection,
        repair_preview: str,
        max_tokens: int,
    ) -> LLMRequest:
        system = (
            "Answer the current user without tools. Return one JSON object with exactly: "
            '{"response": string, "claims": [{"text": string}]}. '
            "Claims, in order, must concatenate to the complete response text; do not omit any factual sentence."
        )
        if repair_preview:
            system += " Replace the invalid prior output completely. Bounded failure: " + repair_preview
        user_payload = {
            "session": projection.model_dump(mode="json"),
            "runtime_facts": facts.model_dump(mode="json"),
        }
        return LLMRequest(
            messages=[
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False, sort_keys=True)),
            ],
            tools=[],
            response_format="json_object",
            max_tokens=max_tokens,
        )

    @staticmethod
    def _parse_response(response: LLMResponse) -> _ModelResponse:
        payload = response.parsed_json
        if not isinstance(payload, dict):
            payload = json.loads(response.content)
        return _ModelResponse.model_validate(payload)

    @classmethod
    def _validate_claim_coverage(cls, response: _ModelResponse) -> None:
        combined = " ".join(item.text for item in response.claims)
        if cls._normalize(combined) != cls._normalize(response.response):
            raise ValueError("model claims do not cover the complete response")

    @staticmethod
    def _classify_claim(
        claim: str,
        *,
        ingress: SessionIngressState,
        facts: RuntimeFactProjection,
    ) -> ClaimSourceClass:
        text = claim.casefold()
        if (
            facts.model.casefold() in text
            and any(marker in text for marker in ("model", "模型"))
        ):
            return ClaimSourceClass.RUNTIME
        if (
            facts.provider.casefold() in text
            and any(marker in text for marker in ("provider", "提供商"))
        ):
            return ClaimSourceClass.RUNTIME
        if (
            facts.project_path
            and facts.project_path.casefold() in text
            and any(marker in text for marker in ("project path", "working directory", "项目路径"))
        ):
            return ClaimSourceClass.RUNTIME
        if any(claim.strip() and claim.strip() in turn.content for turn in ingress.turns if turn.role == "user"):
            return ClaimSourceClass.CONVERSATION
        if any(
            marker in text
            for marker in (
                "repository",
                "repo",
                "codebase",
                "this project",
                "the project",
                "project file",
                "项目",
                "仓库",
                "代码库",
                "项目文件",
                ".py",
                ".ts",
                "/src/",
            )
        ):
            return ClaimSourceClass.PROJECT
        if any(
            marker in text
            for marker in (
                "today",
                "currently",
                "latest",
                "price",
                "weather",
                "今天",
                "当前",
                "最新",
                "价格",
                "天气",
            )
        ):
            return ClaimSourceClass.CURRENT_EXTERNAL
        return ClaimSourceClass.STABLE_KNOWLEDGE

    @classmethod
    def _claim_evidence_ref(
        cls,
        claim: str,
        *,
        source_class: ClaimSourceClass,
        ingress: SessionIngressState,
        facts: RuntimeFactProjection,
    ) -> str:
        if source_class == ClaimSourceClass.RUNTIME:
            return "runtime-facts:" + cls._hash(facts.model_dump(mode="json"))
        if source_class == ClaimSourceClass.CONVERSATION:
            source = next(
                turn.message_id
                for turn in ingress.turns
                if turn.role == "user" and claim.strip() in turn.content
            )
            return f"conversation:{source}"
        return "stable-knowledge-policy:v1"

    @staticmethod
    def _completion_tokens(response: LLMResponse, *, fallback: int) -> int:
        for key in ("completion_tokens", "output_tokens"):
            value = response.usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return fallback

    @staticmethod
    def _bounded_preview(content: str, error: Exception) -> str:
        preview = " ".join(str(content or "").split())[:600]
        preview = re.sub(
            r"(?i)(api[_-]?key|access[_-]?token|password|authorization)\s*[:=]\s*[^,}\s]+",
            r"\1=<redacted>",
            preview,
        )
        return f"{type(error).__name__}: {preview}"

    def _persist_ingress(self, ingress: SessionIngressState) -> None:
        persisted, revision = self.store.load_ingress(ingress.identity.conversation_id)
        if persisted == ingress:
            return
        if persisted is not None and ingress.turns[: len(persisted.turns)] != persisted.turns:
            raise IterationTurnConflictError("session ingress history differs from durable ledger")
        self.store.save_ingress(ingress, expected_revision=revision)

    def _raise_failure(
        self,
        record: IterationTurnRecordMetadata,
        code: BoundedResponseFailureCode,
        message: str,
    ) -> NoReturn:
        failure_ref = self.store.save_artifact(
            record.identity.conversation_id,
            record.identity.run_id,
            kind="bounded_response_failure",
            payload={"code": code.value, "message": message},
        )
        stopped = IterationTurnReducer.stop(
            record,
            outcome=ControlledStopOutcome(
                outcome=IterationOutcome.FAILED,
                stop_reason=IterationStopReason.INTERNAL_FAILURE,
                failure_ref=failure_ref,
            ),
        )
        stopped = self.store.save(stopped, expected_generation=record.generation)
        raise BoundedResponseError(code, message, record=stopped)

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
    def _normalize(value: str) -> str:
        return " ".join(value.split()).casefold()

    @staticmethod
    def _encode(value: Any) -> bytes:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @classmethod
    def _hash(cls, value: Any) -> str:
        return "sha256:" + hashlib.sha256(cls._encode(value)).hexdigest()


__all__ = [
    "BoundedModelResponseController",
    "BoundedModelResponseResult",
    "BoundedResponseError",
    "BoundedResponseFailureCode",
    "BoundedSessionProjection",
]
