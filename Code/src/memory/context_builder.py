"""Collect and render structured memory context for agents."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Literal, Sequence

from core.exceptions import (
    ContextAssemblyBudgetError,
    ContextAssemblyGovernanceError,
    ContextSourceError,
)
from core.token_counting import ProviderTokenCounter
from memory.agents.memory_vault_agent import MemoryVaultAgent
from memory.context_assembly import ContextAssembler
from memory.memory_models import MemoryRecord, MemoryType
from memory.memory_store import MemoryStore
from memory.project_manager import ProjectManager
from memory.short_memory import ShortMemory
from memory.session_constraints import (
    build_session_constraint_candidate,
    session_constraint_projection,
)
from memory.session_dialog import session_turn_ledger_hash
from memory.rolling_compaction import (
    RollingSummaryFallbackReason,
    RollingSummaryRequest,
    RollingSummaryResult,
    provider_attempt_evidence_complete,
)
from memory.compaction_summary import (
    calculate_summary_budget,
    source_candidate_binding_hash,
    source_candidate_fingerprint,
)
from metadata import (
    ContextAssemblyPolicy,
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ContextCompactionAttempt,
    ContextCompactionBinding,
    ContextCompactionFallbackReason,
    ContextCompactionProviderStatus,
    ContextCompactionRecord,
    ContextCompactionReuseAdmission,
    ContextCompactionReuseShadowFailure,
    ContextCompactionReuseShadowFailureReason,
    ContextCompactionSelectionOutcome,
    ContextCompactionSelectionStatus,
    ContextSelectionMetadata,
    DurableArtifactReference,
    SessionConstraintState,
    SessionIngressState,
)


DEFAULT_MAX_PROMPT_CHARS = 16_000
DEFAULT_MAX_PROMPT_TOKENS = 4_096
MIN_PROMPT_CHARS = 256
MEMORY_CONTEXT_ADAPTER_VERSION = "typed_memory_candidates_segmented_compaction_v5"
ROLLING_SUMMARY_RESPONSE_SCHEMA_RESERVE_TOKENS = 64


def _source_candidate_ids_key(candidate_ids: Sequence[str]) -> str:
    """Canonical key for body-free source-fingerprint evidence."""

    return json.dumps(list(candidate_ids), ensure_ascii=False, separators=(",", ":"))


class MemoryContextBuilder:
    """Collect dialog, memory, project, and environment context for assembly."""

    DEFAULT_MEMORY_TYPES = [
        MemoryType.USER,
        MemoryType.FEEDBACK,
        MemoryType.PROJECT,
        MemoryType.REFERENCE,
        MemoryType.LONG_TERM,
        MemoryType.SHORT_TERM,
    ]

    def __init__(
        self,
        *,
        short_memory: ShortMemory | None = None,
        memory_store: MemoryStore | None = None,
        memory_vault_agent: MemoryVaultAgent | None = None,
        project_manager: ProjectManager | None = None,
        max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
        max_prompt_tokens: int | None = None,
        token_counter: ProviderTokenCounter | None = None,
        rolling_summary_enabled: bool = False,
        rolling_summary_adapter: Callable[[RollingSummaryRequest], RollingSummaryResult]
        | None = None,
        rolling_summary_request_factory: Callable[
            [tuple[ContextCandidate, ...], int], RollingSummaryRequest | None
        ]
        | None = None,
        rolling_summary_token_limit: int = 256,
        compaction_reuse_shadow_provider: Callable[
            [dict[str, Any]],
            list[ContextCompactionReuseAdmission | Mapping[str, Any]] | None,
        ]
        | None = None,
    ) -> None:
        self.short_memory = short_memory or ShortMemory()
        self.memory_store = memory_store or MemoryStore()
        self.memory_vault_agent = memory_vault_agent
        self.project_manager = project_manager
        self.max_prompt_chars = max(MIN_PROMPT_CHARS, int(max_prompt_chars))
        self.max_prompt_tokens = int(max_prompt_tokens) if max_prompt_tokens is not None else None
        self.token_counter = token_counter
        self.rolling_summary_enabled = bool(rolling_summary_enabled)
        self.rolling_summary_adapter = rolling_summary_adapter
        self.rolling_summary_request_factory = rolling_summary_request_factory
        self.rolling_summary_token_limit = max(1, int(rolling_summary_token_limit))
        self.compaction_reuse_shadow_provider = compaction_reuse_shadow_provider
        self.context_assembler = ContextAssembler(
            renderer=self._prompt_text,
            token_counter=token_counter,
        )
        self._context_snapshot_sink: Callable[[str, dict[str, Any]], Any] | None = None
        self._context_replay_provider: Callable[[str], dict[str, Any] | None] | None = None
        self._context_compaction_sink: (
            Callable[[dict[str, Any]], DurableArtifactReference | None] | None
        ) = None

    def set_checkpoint_handlers(
        self,
        *,
        snapshot_sink: Callable[[str, dict[str, Any]], Any] | None = None,
        replay_provider: Callable[[str], dict[str, Any] | None] | None = None,
        compaction_sink: (
            Callable[[dict[str, Any]], DurableArtifactReference | None] | None
        ) = None,
    ) -> None:
        """Bind optional controller-owned persistence and replay handlers."""
        self._context_snapshot_sink = snapshot_sink
        self._context_replay_provider = replay_provider
        self._context_compaction_sink = compaction_sink

    def build(
        self,
        query: str,
        *,
        project_path: str | Path | None = None,
        include_environment: bool = True,
        limit: int = 10,
        system_prompt: str = "",
        max_prompt_chars: int | None = None,
        max_prompt_tokens: int | None = None,
        session_constraints: SessionConstraintState | None = None,
        session_ingress_state: SessionIngressState | None = None,
        project_index_mode: Literal["refresh", "read_only"] = "refresh",
        strict_sources: bool = False,
    ) -> dict[str, Any]:
        """Collect memory context and delegate deterministic selection."""
        if project_index_mode not in {"refresh", "read_only"}:
            raise ValueError(f"Unsupported project index mode: {project_index_mode}")
        if session_ingress_state is not None and not isinstance(
            session_ingress_state,
            SessionIngressState,
        ):
            raise TypeError("session_ingress_state must be SessionIngressState")
        if session_ingress_state is not None:
            ingress_constraints = session_ingress_state.session_constraints
            if (
                session_constraints is not None
                and session_constraints.canonical_hash != ingress_constraints.canonical_hash
            ):
                raise ValueError("session ingress and explicit constraints differ")
            session_constraints = ingress_constraints
        query = query.strip()
        system_prompt = system_prompt.strip()
        project_path_obj = Path(project_path).expanduser() if project_path else None
        if project_path_obj is not None:
            try:
                canonical_project_root = project_path_obj.resolve(strict=False)
            except OSError as exc:
                raise ValueError("project identity cannot be canonicalized") from exc
            if session_ingress_state is not None:
                try:
                    ingress_project_root = Path(
                        session_ingress_state.identity.project_root
                    ).expanduser().resolve(strict=False)
                except OSError as exc:
                    raise ValueError("session ingress project identity cannot be canonicalized") from exc
                if ingress_project_root != canonical_project_root:
                    raise ValueError("session ingress project identity mismatch")
            if session_constraints is not None and session_constraints.project_root:
                try:
                    constraint_project_root = Path(
                        session_constraints.project_root
                    ).expanduser().resolve(strict=False)
                except OSError as exc:
                    raise ValueError("session constraint project identity cannot be canonicalized") from exc
                if constraint_project_root != canonical_project_root:
                    raise ValueError("session constraint project identity mismatch")
        effective_max_chars = self.max_prompt_chars if max_prompt_chars is None else max(
            MIN_PROMPT_CHARS,
            int(max_prompt_chars),
        )
        requested_max_tokens = self.max_prompt_tokens if max_prompt_tokens is None else int(max_prompt_tokens)
        session_constraints_hash = (
            session_constraints.canonical_hash if session_constraints is not None else ""
        )
        session_turn_source_hash = (
            self._session_ingress_turn_ledger_hash(session_ingress_state)
            if session_ingress_state is not None
            else ""
        )
        request_hash = self.context_assembler.request_hash(
            {
                "adapter_version": MEMORY_CONTEXT_ADAPTER_VERSION,
                "query": query,
                "project_path": str(project_path_obj) if project_path_obj else "",
                "include_environment": bool(include_environment),
                "limit": int(limit),
                "system_prompt": system_prompt,
                "session_constraints_hash": session_constraints_hash,
                "session_ingress_turn_ledger_hash": session_turn_source_hash,
                "rolling_summary_enabled": self.rolling_summary_enabled,
                "rolling_summary_token_limit": self.rolling_summary_token_limit,
            },
            max_prompt_chars=effective_max_chars,
            max_prompt_tokens=requested_max_tokens,
        )
        if self._context_replay_provider is not None:
            replayed = self._context_replay_provider(request_hash)
            if isinstance(replayed, dict):
                return copy.deepcopy(replayed)

        project_manager = self.project_manager
        if project_manager is None and project_path_obj is not None:
            project_manager = ProjectManager(project_path_obj)

        related_files = []
        if project_manager is not None and project_path_obj is not None and project_path_obj.exists():
            if project_index_mode == "refresh":
                project_manager.update(project_path_obj)
            related_files = project_manager.search(query or project_path_obj.name, limit=limit)

        payload = {
            "query": query,
            "project_path": str(project_path_obj) if project_path_obj else "",
            "system_prompt": system_prompt,
            "dialog_context": (
                self._session_ingress_dialog_context(session_ingress_state, limit)
                if session_ingress_state is not None
                else self._dialog_context(limit)
            ),
            "related_memories": self._related_memories(
                query,
                limit,
                project_path_obj,
                strict_sources=strict_sources,
            ),
            "related_files": related_files,
            "environment_context": (
                self._environment_context(
                    project_path_obj,
                    strict_sources=strict_sources,
                )
                if include_environment
                else []
            ),
            "session_constraints": session_constraints,
        }
        candidates, sources = self._typed_candidates(payload)
        assembly = self.context_assembler.assemble_candidates(
            candidates,
            policy=ContextAssemblyPolicy(
                max_prompt_chars=effective_max_chars,
                max_prompt_tokens=(
                    requested_max_tokens
                    if requested_max_tokens is not None and requested_max_tokens > 0
                    else None
                ),
            ),
            renderer=self._render_candidates,
        )
        candidates, sources, assembly, compaction_bindings = self._compact_dialog_prefix(
            candidates,
            sources,
            assembly,
            policy=ContextAssemblyPolicy(
                max_prompt_chars=effective_max_chars,
                max_prompt_tokens=(
                    requested_max_tokens
                    if requested_max_tokens is not None and requested_max_tokens > 0
                    else None
                ),
            ),
            strict_sources=strict_sources,
        )
        if assembly.selection.assembly_status != ContextAssemblyStatus.READY:
            if (
                assembly.selection.assembly_status
                == ContextAssemblyStatus.GOVERNANCE_BLOCKED
            ):
                raise ContextAssemblyGovernanceError(
                    assembly.selection.governance_blocked_candidate_ids
            )
            raise ContextAssemblyBudgetError(
                assembly.selection.omitted_required_candidate_ids
            )
        assembly = self._apply_compaction_reuse_shadow(
            candidates=candidates,
            assembly=assembly,
            request_hash=request_hash,
            session_turn_source_hash=session_turn_source_hash,
            session_constraints_hash=session_constraints_hash,
            strict_sources=strict_sources,
        )
        selected = self._compatibility_payload(
            payload,
            candidates=candidates,
            sources=sources,
            assembly=assembly,
            compaction_bindings=compaction_bindings,
            request_hash=request_hash,
            session_turn_source_hash=session_turn_source_hash,
            session_constraints_hash=session_constraints_hash,
        )
        if self._context_snapshot_sink is not None:
            try:
                self._context_snapshot_sink(request_hash, copy.deepcopy(selected))
            except Exception as exc:
                if strict_sources:
                    raise ContextSourceError("context_snapshot", exc) from exc
        return selected

    def _compact_dialog_prefix(
        self,
        candidates: list[ContextCandidate],
        sources: dict[str, tuple[str, Any]],
        initial: ContextAssemblyResult,
        *,
        policy: ContextAssemblyPolicy,
        strict_sources: bool = False,
    ) -> tuple[
        list[ContextCandidate],
        dict[str, tuple[str, Any]],
        ContextAssemblyResult,
        list[ContextCompactionBinding],
    ]:
        """Replace a budget-limited older dialog prefix with a durable summary."""
        if (
            self._context_compaction_sink is None
            or initial.selection.assembly_status != ContextAssemblyStatus.READY
        ):
            return candidates, sources, initial, []
        compaction_attempts: list[ContextCompactionAttempt] = []
        dialog_candidates = [
            candidate
            for candidate in candidates
            if candidate.kind == ContextCandidateKind.DIALOG
        ]
        decision_by_id = {
            decision.candidate_id: decision
            for decision in initial.selection.candidate_decisions
        }
        initially_kept_ids = {
            candidate_id
            for candidate_id, decision in decision_by_id.items()
            if decision.action == "kept"
        }
        compacted_ids = [
            candidate.candidate_id
            for candidate in dialog_candidates
            if candidate.role == "assistant"
            if decision_by_id[candidate.candidate_id].action != "kept"
            and decision_by_id[candidate.candidate_id].reason == "prompt_budget"
        ]
        if len(compacted_ids) < 2:
            return candidates, sources, initial, []

        summary_token_limit = self._dynamic_rolling_summary_budget(
            initial=initial,
            dialog_candidates=dialog_candidates,
            compacted_ids=compacted_ids,
            policy=policy,
        )

        minimum_recent_messages = 2
        while (
            compacted_ids
            and len(compacted_ids)
            <= len(dialog_candidates) - minimum_recent_messages
        ):
            compacted_set = set(compacted_ids)
            compacted_sources = [
                candidate
                for candidate in dialog_candidates
                if candidate.candidate_id in compacted_set
            ]
            try:
                record = self._dialog_compaction_record(compacted_sources)
            except ValueError as exc:
                if strict_sources:
                    raise ContextSourceError("context_compaction", exc) from exc
                return candidates, sources, self._with_compaction_attempts(initial, compaction_attempts), []
            deterministic_record = record

            # LLM-assisted summaries are an opt-in derived view.  The factory
            # owns provider invocation and may return no request; the adapter
            # owns validation.  Any mismatch or fallback keeps this exact
            # deterministic source record and therefore cannot widen authority.
            provider_status = ContextCompactionProviderStatus.NOT_ATTEMPTED
            provider_fallback_reason: ContextCompactionFallbackReason | None = None
            adapter_fallback_reason: str | None = None
            provider_attempt = None
            provider_failure_usage: dict[str, Any] | None = None
            provider_failure_finish_reason: str | None = None
            if (
                self.rolling_summary_enabled
                and self.rolling_summary_adapter is not None
                and self.rolling_summary_request_factory is not None
            ):
                if summary_token_limit <= 0:
                    provider_fallback_reason = ContextCompactionFallbackReason.BUDGET_ZERO
                else:
                    try:
                        request = self.rolling_summary_request_factory(
                            tuple(compacted_sources),
                            summary_token_limit,
                        )
                    except Exception as exc:
                        request = None
                        provider_status = ContextCompactionProviderStatus.REJECTED
                        raw_failure_usage = getattr(exc, "usage", None)
                        provider_failure_usage = (
                            dict(raw_failure_usage)
                            if isinstance(raw_failure_usage, Mapping)
                            else {}
                        )
                        provider_failure_finish_reason = getattr(exc, "finish_reason", None)
                        normalized_finish_reason = str(
                            provider_failure_finish_reason or ""
                        ).strip().lower()
                        provider_fallback_reason = (
                            ContextCompactionFallbackReason.PROVIDER_OUTPUT_TRUNCATED
                            if normalized_finish_reason in {"length", "max_tokens"}
                            else ContextCompactionFallbackReason.REQUEST_OR_PROVIDER_FAILURE
                        )
                    if request is None and provider_status == ContextCompactionProviderStatus.NOT_ATTEMPTED:
                        provider_status = ContextCompactionProviderStatus.REJECTED
                        provider_fallback_reason = (
                            ContextCompactionFallbackReason.REQUEST_OR_PROVIDER_FAILURE
                        )
                    elif request is not None:
                        provider_attempt = request.attempt
                        try:
                            rolling_result = self.rolling_summary_adapter(request)
                        except Exception:
                            rolling_result = None
                            provider_status = ContextCompactionProviderStatus.REJECTED
                            provider_fallback_reason = (
                                ContextCompactionFallbackReason.INVALID_ADAPTER_RESULT
                            )
                        if rolling_result is not None:
                            rolling_record = rolling_result.record
                            expected_source_ids = [
                                candidate.candidate_id for candidate in compacted_sources
                            ]
                            if (
                                rolling_result.accepted
                                and rolling_record is not None
                                and provider_attempt_evidence_complete(provider_attempt)
                                and rolling_record.source_candidate_ids == expected_source_ids
                                and rolling_record.source_fingerprint == record.source_fingerprint
                                and rolling_record.original_chars == record.original_chars
                                and rolling_record.compacted_chars < rolling_record.original_chars
                            ):
                                record = rolling_record
                                provider_status = ContextCompactionProviderStatus.ACCEPTED
                                provider_fallback_reason = None
                            elif rolling_result.accepted:
                                provider_status = ContextCompactionProviderStatus.REJECTED
                                provider_fallback_reason = (
                                    ContextCompactionFallbackReason.INVALID_ADAPTER_RESULT
                                    if not provider_attempt_evidence_complete(provider_attempt)
                                    else ContextCompactionFallbackReason.SOURCE_FINGERPRINT_MISMATCH
                                )
                            else:
                                provider_status = ContextCompactionProviderStatus.REJECTED
                                provider_fallback_reason = (
                                    ContextCompactionFallbackReason.INVALID_ADAPTER_RESULT
                                )
                                adapter_fallback_reason = (
                                    rolling_result.fallback.reason.value
                                    if rolling_result.fallback is not None
                                    else None
                                )
            def build_trial(current_record: ContextCompactionRecord):
                current_candidate = ContextCandidate(
                    candidate_id=f"compaction:{current_record.compaction_id}",
                    kind=ContextCandidateKind.ARTIFACT,
                    source_id=current_record.source_fingerprint,
                    content=current_record.summary,
                    retention=ContextCandidateRetention.PREFERRED,
                    priority=99,
                    source_order=500,
                    truncation=ContextCandidateTruncation.FORBIDDEN,
                    trust=ContextCandidateTrust.DERIVED,
                    freshness=ContextCandidateFreshness.CURRENT,
                    compacted_candidate_ids=current_record.source_candidate_ids,
                )
                current_candidates = [*candidates, current_candidate]
                current_trial = self.context_assembler.assemble_candidates(
                    current_candidates,
                    policy=policy,
                    renderer=self._render_candidates,
                )
                current_decisions = {
                    decision.candidate_id: decision
                    for decision in current_trial.selection.candidate_decisions
                }
                current_decision = current_decisions.get(current_candidate.candidate_id)
                current_kept = (
                    current_trial.selection.assembly_status == ContextAssemblyStatus.READY
                    and current_decision is not None
                    and current_decision.action == "kept"
                )
                current_newly_limited = [
                    candidate.candidate_id
                    for candidate in dialog_candidates
                    if candidate.candidate_id not in compacted_set
                    and candidate.candidate_id in initially_kept_ids
                    and current_decisions.get(candidate.candidate_id) is not None
                    and current_decisions[candidate.candidate_id].action != "kept"
                ]
                return (
                    current_candidate,
                    current_candidates,
                    current_trial,
                    current_decisions,
                    current_kept,
                    current_newly_limited,
                )

            (
                compaction_candidate,
                trial_candidates,
                trial,
                trial_decisions,
                compaction_kept,
                newly_limited,
            ) = build_trial(record)
            generated_record = record if record.algorithm == "llm_rolling_summary_v1" else None
            if generated_record is not None and not compaction_kept:
                generated_fallback_reason = ContextCompactionFallbackReason.SUMMARY_NOT_FIT_ATOMICALLY
            elif generated_record is not None and newly_limited:
                generated_fallback_reason = ContextCompactionFallbackReason.RECENT_SUFFIX_DISPLACED
            else:
                generated_fallback_reason = None

            def append_attempt(
                *,
                attempt_record: ContextCompactionRecord,
                provider: ContextCompactionProviderStatus,
                selection: ContextCompactionSelectionStatus,
                fallback: ContextCompactionFallbackReason | None,
                sink_status: Literal[
                    "not_attempted", "persisted", "failed", "observed_only"
                ] = "not_attempted",
                attempt_evidence: Any = None,
                trial_status: ContextAssemblyStatus | None = None,
                trial_decision: Any = None,
                displaced_ids: list[str] | None = None,
                selection_outcome: ContextCompactionSelectionOutcome = (
                    ContextCompactionSelectionOutcome.NOT_APPLICABLE
                ),
                artifact_binding: bool = False,
                used_in_prompt: bool = False,
                adapter_fallback_reason: str | None = None,
                provider_usage: dict[str, Any] | None = None,
                provider_finish_reason: str | None = None,
            ) -> None:
                generated = (
                    attempt_record.algorithm == "llm_rolling_summary_v1"
                    and provider == ContextCompactionProviderStatus.ACCEPTED
                )
                observed_usage = (
                    provider_usage
                    if provider_usage is not None
                    else (
                        dict(attempt_evidence.usage or {})
                        if attempt_evidence is not None
                        and isinstance(getattr(attempt_evidence, "usage", None), Mapping)
                        and getattr(attempt_evidence, "usage", None) is not None
                        else None
                    )
                )
                observed_finish_reason = (
                    provider_finish_reason
                    if provider_finish_reason is not None
                    else (
                        getattr(attempt_evidence, "finish_reason", None)
                        if attempt_evidence is not None
                        else None
                    )
                )
                if observed_finish_reason is not None:
                    observed_finish_reason = str(observed_finish_reason).strip() or None
                usage_complete = None
                def nonnegative_int(key: str) -> int | None:
                    if not isinstance(observed_usage, Mapping):
                        return None
                    value = observed_usage.get(key)
                    return (
                        value
                        if isinstance(value, int)
                        and not isinstance(value, bool)
                        and value >= 0
                        else None
                    )

                if observed_usage is not None:
                    usage_complete = all(
                        nonnegative_int(key) is not None
                        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    )
                    if attempt_evidence is not None:
                        usage_complete = bool(
                            getattr(attempt_evidence, "usage_observed", False)
                            and usage_complete
                        )
                compaction_attempts.append(
                    ContextCompactionAttempt(
                        attempt_ordinal=len(compaction_attempts) + 1,
                        source_candidate_ids=list(attempt_record.source_candidate_ids),
                        source_fingerprint=attempt_record.source_fingerprint,
                        source_chars=attempt_record.original_chars,
                        algorithm=attempt_record.algorithm,
                        provider_status=provider,
                        selection_status=selection,
                        fallback_reason=fallback,
                        summary_token_limit=attempt_record.summary_token_limit,
                        summary_token_count=attempt_record.summary_token_count,
                        usage_complete=usage_complete,
                        finish_reason=observed_finish_reason,
                        provider_prompt_tokens=(
                            nonnegative_int("prompt_tokens")
                        ),
                        provider_completion_tokens=(
                            nonnegative_int("completion_tokens")
                        ),
                        provider_total_tokens=(
                            nonnegative_int("total_tokens")
                        ),
                        adapter_fallback_reason=adapter_fallback_reason,
                        generated_candidate_id=(
                            f"compaction:{attempt_record.compaction_id}" if generated else None
                        ),
                        generated_summary_fingerprint=(
                            "sha256:"
                            + hashlib.sha256(attempt_record.summary.encode("utf-8")).hexdigest()
                            if generated
                            else None
                        ),
                        generated_summary_chars=(
                            attempt_record.compacted_chars if generated else None
                        ),
                        trial_assembly_status=trial_status,
                        trial_candidate_decision=trial_decision,
                        displaced_candidate_ids=list(displaced_ids or []),
                        selection_outcome=selection_outcome,
                        artifact_sink_status=sink_status,
                        artifact_binding=artifact_binding,
                        used_in_prompt=used_in_prompt,
                    )
                )

            if generated_record is not None and generated_fallback_reason is not None:
                append_attempt(
                    attempt_record=generated_record,
                    provider=provider_status,
                    selection=ContextCompactionSelectionStatus.NOT_SELECTED,
                    fallback=generated_fallback_reason,
                    attempt_evidence=provider_attempt,
                    trial_status=trial.selection.assembly_status,
                    trial_decision=trial_decisions.get(compaction_candidate.candidate_id),
                    displaced_ids=newly_limited,
                    selection_outcome=(
                        ContextCompactionSelectionOutcome.GENERATED_RECENT_SUFFIX_DISPLACED
                        if generated_fallback_reason
                        == ContextCompactionFallbackReason.RECENT_SUFFIX_DISPLACED
                        else ContextCompactionSelectionOutcome.GENERATED_FIT_REJECTED
                    ),
                    adapter_fallback_reason=(
                        adapter_fallback_reason
                        or (
                            provider_fallback_reason.value
                            if provider_fallback_reason is not None
                            else None
                        )
                    ),
                    provider_usage=provider_failure_usage,
                    provider_finish_reason=provider_failure_finish_reason,
                )
                record = deterministic_record
                (
                    compaction_candidate,
                    trial_candidates,
                    trial,
                    trial_decisions,
                    compaction_kept,
                    newly_limited,
                ) = build_trial(record)
            elif generated_record is None and provider_fallback_reason is not None:
                append_attempt(
                    attempt_record=deterministic_record.model_copy(
                        update={
                            "algorithm": "llm_rolling_summary_v1",
                            "summary_token_limit": (
                                summary_token_limit if summary_token_limit > 0 else None
                            ),
                        }
                    ),
                    provider=provider_status,
                    selection=ContextCompactionSelectionStatus.NOT_EVALUATED,
                    fallback=provider_fallback_reason,
                    attempt_evidence=provider_attempt,
                    trial_status=None,
                    selection_outcome=ContextCompactionSelectionOutcome.NOT_APPLICABLE,
                    adapter_fallback_reason=(
                        adapter_fallback_reason
                        or (
                            provider_fallback_reason.value
                            if provider_fallback_reason is not None
                            else None
                        )
                    ),
                    provider_usage=provider_failure_usage,
                    provider_finish_reason=provider_failure_finish_reason,
                )

            if compaction_kept and not newly_limited:
                reference = None
                sink_failed = False
                sink_exception = False
                try:
                    reference = self._context_compaction_sink(
                        record.model_dump(mode="json")
                    )
                    if reference is None:
                        sink_failed = True
                    else:
                        binding = ContextCompactionBinding(
                            record=record,
                            artifact=reference,
                            source_binding_hash=source_candidate_binding_hash(
                                compacted_sources
                            ),
                        )
                except Exception as exc:
                    sink_failed = True
                    sink_exception = True
                    if strict_sources:
                        raise ContextSourceError("context_compaction", exc) from exc
                if sink_failed:
                    if (
                        not sink_exception
                        and reference is None
                        and generated_record is not None
                        and generated_fallback_reason is None
                    ):
                        append_attempt(
                            attempt_record=record,
                            provider=provider_status,
                            selection=ContextCompactionSelectionStatus.NOT_SELECTED,
                            fallback=None,
                            sink_status="observed_only",
                            attempt_evidence=provider_attempt,
                            trial_status=trial.selection.assembly_status,
                            trial_decision=trial_decisions.get(compaction_candidate.candidate_id),
                            selection_outcome=ContextCompactionSelectionOutcome.GENERATED_OBSERVED_ONLY,
                        )
                    else:
                        append_attempt(
                            attempt_record=record,
                            provider=(
                                provider_status
                                if generated_record is not None and generated_fallback_reason is None
                                else ContextCompactionProviderStatus.NOT_ATTEMPTED
                            ),
                            selection=ContextCompactionSelectionStatus.NOT_SELECTED,
                            fallback=ContextCompactionFallbackReason.ARTIFACT_SINK_FAILURE,
                            sink_status="failed",
                            attempt_evidence=(
                                provider_attempt
                                if generated_record is not None and generated_fallback_reason is None
                                else None
                            ),
                            trial_status=trial.selection.assembly_status,
                            trial_decision=trial_decisions.get(compaction_candidate.candidate_id),
                            selection_outcome=ContextCompactionSelectionOutcome.SINK_FAILED,
                        )
                    return (
                        candidates,
                        sources,
                        self._with_compaction_attempts(initial, compaction_attempts),
                        [],
                    )
                append_attempt(
                    attempt_record=record,
                    provider=(
                        provider_status
                        if generated_record is not None and generated_fallback_reason is None
                        else ContextCompactionProviderStatus.NOT_ATTEMPTED
                    ),
                    selection=ContextCompactionSelectionStatus.SELECTED,
                    fallback=(
                        None
                        if generated_record is not None and generated_fallback_reason is None
                        else (
                            provider_fallback_reason
                            if provider_fallback_reason is not None
                            else ContextCompactionFallbackReason.DETERMINISTIC_FALLBACK
                        )
                    ),
                    sink_status="persisted",
                    attempt_evidence=(
                        provider_attempt
                        if generated_record is not None and generated_fallback_reason is None
                        else None
                    ),
                    trial_status=trial.selection.assembly_status,
                    trial_decision=trial_decisions.get(compaction_candidate.candidate_id),
                    selection_outcome=(
                        ContextCompactionSelectionOutcome.GENERATED_SELECTED
                        if generated_record is not None and generated_fallback_reason is None
                        else (
                            ContextCompactionSelectionOutcome.DETERMINISTIC_FALLBACK
                            if provider_fallback_reason is not None
                            else ContextCompactionSelectionOutcome.DETERMINISTIC_BOUND
                        )
                    ),
                    artifact_binding=True,
                    used_in_prompt=True,
                )
                trial.selection.compaction_attempts = list(compaction_attempts)
                trial_sources = dict(sources)
                trial_sources[compaction_candidate.candidate_id] = (
                    "context_compactions",
                    binding.model_dump(mode="json"),
                )
                return trial_candidates, trial_sources, trial, [binding]
            if not newly_limited:
                append_attempt(
                    attempt_record=record,
                    provider=ContextCompactionProviderStatus.NOT_ATTEMPTED,
                    selection=ContextCompactionSelectionStatus.NOT_SELECTED,
                    fallback=ContextCompactionFallbackReason.DETERMINISTIC_FALLBACK,
                    trial_status=trial.selection.assembly_status,
                    trial_decision=trial_decisions.get(compaction_candidate.candidate_id),
                    selection_outcome=ContextCompactionSelectionOutcome.DETERMINISTIC_FALLBACK,
                )
                return (
                    candidates,
                    sources,
                    self._with_compaction_attempts(initial, compaction_attempts),
                    [],
                )
            compacted_ids.extend(
                candidate.candidate_id
                for candidate in dialog_candidates
                if candidate.candidate_id in set(newly_limited)
                and candidate.candidate_id not in compacted_set
            )
            compacted_ids = [
                candidate.candidate_id
                for candidate in dialog_candidates
                if candidate.candidate_id in set(compacted_ids)
            ]
        return candidates, sources, self._with_compaction_attempts(initial, compaction_attempts), []

    @staticmethod
    def _source_fingerprint_shadow_index(
        candidates: Sequence[ContextCandidate],
        attempts: Sequence[ContextCompactionAttempt],
    ) -> dict[str, str]:
        """Expose exact source fingerprints without crossing the body boundary.

        Existing compaction attempts are authoritative.  For a context that has
        not compacted during this build, expose bounded contiguous assistant
        dialog windows so a checkpoint-owned artifact can be compared against
        the current source view.  The provider receives only hashes and IDs.
        """

        index = {
            _source_candidate_ids_key(attempt.source_candidate_ids): attempt.source_fingerprint
            for attempt in attempts
        }
        assistant_dialog = [
            candidate
            for candidate in candidates
            if candidate.kind == ContextCandidateKind.DIALOG
            and candidate.role == "assistant"
        ]
        max_window = 64
        for start in range(len(assistant_dialog)):
            for end in range(start + 2, min(len(assistant_dialog), start + max_window) + 1):
                window = assistant_dialog[start:end]
                source_ids = [candidate.candidate_id for candidate in window]
                index.setdefault(
                    _source_candidate_ids_key(source_ids),
                    source_candidate_fingerprint(window),
                )
        return index

    def _apply_compaction_reuse_shadow(
        self,
        *,
        candidates: list[ContextCandidate],
        assembly: ContextAssemblyResult,
        request_hash: str,
        session_turn_source_hash: str,
        session_constraints_hash: str,
        strict_sources: bool = False,
    ) -> ContextAssemblyResult:
        """Attach body-free reusable-compaction shadow admissions.

        This hook is deliberately default-off and selection-only.  It receives
        hashes and IDs, not prompt or candidate bodies, and it may only append
        ``ContextCompactionReuseAdmission`` diagnostics.  It cannot add a
        candidate, bind an artifact, govern source omissions, or change the
        prompt text.
        """

        if self.compaction_reuse_shadow_provider is None:
            return assembly
        selected_ids = [candidate.candidate_id for candidate in assembly.selected_candidates]
        provider_payload = {
            "schema": "memory-context-compaction-reuse-shadow-v1",
            "context_request_hash": request_hash,
            "session_turn_source_hash": session_turn_source_hash,
            "session_constraints_hash": session_constraints_hash,
            "prompt_hash": "sha256:" + hashlib.sha256(
                assembly.prompt_text.encode("utf-8")
            ).hexdigest(),
            "candidate_digests": [
                self._candidate_shadow_digest(candidate) for candidate in candidates
            ],
            "selected_candidate_ids": selected_ids,
            "assembly_status": str(
                getattr(
                    assembly.selection.assembly_status,
                    "value",
                    assembly.selection.assembly_status,
                )
            ),
            "existing_compaction_attempts": len(assembly.selection.compaction_attempts),
            "existing_compaction_reuse_admissions": len(
                assembly.selection.compaction_reuse_admissions
            ),
            "source_fingerprint_by_candidate_ids": self._source_fingerprint_shadow_index(
                candidates,
                assembly.selection.compaction_attempts,
            ),
        }
        try:
            raw_admissions = self.compaction_reuse_shadow_provider(provider_payload)
        except Exception as exc:
            if strict_sources:
                raise ContextSourceError("context_compaction_reuse", exc) from exc
            return self._append_compaction_reuse_shadow_failure(
                assembly,
                reason=ContextCompactionReuseShadowFailureReason.PROVIDER_EXCEPTION,
                exception_type=type(exc).__name__,
            )
        if raw_admissions is None or raw_admissions == []:
            return self._append_compaction_reuse_shadow_failure(
                assembly,
                reason=ContextCompactionReuseShadowFailureReason.PROVIDER_EMPTY,
            )
        if not isinstance(raw_admissions, (list, tuple)):
            exc = TypeError("shadow provider must return a list or tuple")
            if strict_sources:
                raise ContextSourceError("context_compaction_reuse", exc) from exc
            return self._append_compaction_reuse_shadow_failure(
                assembly,
                reason=ContextCompactionReuseShadowFailureReason.INVALID_PROVIDER_RESULT,
                exception_type=type(exc).__name__,
            )
        try:
            admissions = [
                item
                if isinstance(item, ContextCompactionReuseAdmission)
                else ContextCompactionReuseAdmission.model_validate(item)
                for item in raw_admissions
            ]
        except Exception as exc:
            if strict_sources:
                raise ContextSourceError("context_compaction_reuse", exc) from exc
            return self._append_compaction_reuse_shadow_failure(
                assembly,
                reason=ContextCompactionReuseShadowFailureReason.INVALID_PROVIDER_RESULT,
                exception_type=type(exc).__name__,
            )
        if not admissions:
            return self._append_compaction_reuse_shadow_failure(
                assembly,
                reason=ContextCompactionReuseShadowFailureReason.PROVIDER_EMPTY,
            )
        try:
            selection_payload = assembly.selection.model_dump(mode="python")
            selection_payload["compaction_reuse_admissions"] = [
                *selection_payload.get("compaction_reuse_admissions", []),
                *[admission.model_dump(mode="python") for admission in admissions],
            ]
            selection = ContextSelectionMetadata.model_validate(selection_payload)
            return ContextAssemblyResult(
                prompt_text=assembly.prompt_text,
                selected_candidates=list(assembly.selected_candidates),
                selection=selection,
            )
        except Exception as exc:
            if strict_sources:
                raise ContextSourceError("context_compaction_reuse", exc) from exc
            return self._append_compaction_reuse_shadow_failure(
                assembly,
                reason=ContextCompactionReuseShadowFailureReason.INVALID_PROVIDER_RESULT,
                exception_type=type(exc).__name__,
            )

    @staticmethod
    def _append_compaction_reuse_shadow_failure(
        assembly: ContextAssemblyResult,
        *,
        reason: ContextCompactionReuseShadowFailureReason,
        exception_type: str | None = None,
    ) -> ContextAssemblyResult:
        """Attach body-free shadow fallback evidence without changing authority."""

        failure = ContextCompactionReuseShadowFailure(
            failure_id=f"compaction-reuse-shadow:{reason.value}",
            reason=reason,
            exception_type=exception_type,
            strict_sources=False,
            fallback_applied=True,
        )
        selection_payload = assembly.selection.model_dump(mode="python")
        selection_payload["compaction_reuse_shadow_failures"] = [
            *selection_payload.get("compaction_reuse_shadow_failures", []),
            failure.model_dump(mode="python"),
        ]
        return ContextAssemblyResult(
            prompt_text=assembly.prompt_text,
            selected_candidates=list(assembly.selected_candidates),
            selection=ContextSelectionMetadata.model_validate(selection_payload),
        )

    @staticmethod
    def _with_compaction_attempts(
        assembly: ContextAssemblyResult,
        attempts: list[ContextCompactionAttempt],
    ) -> ContextAssemblyResult:
        """Attach body-free builder outcomes to the existing selection value."""

        if not attempts:
            return assembly
        assembly.selection.compaction_attempts = list(attempts)
        return assembly

    @staticmethod
    def _candidate_shadow_digest(candidate: ContextCandidate) -> dict[str, Any]:
        """Return a reusable-admission-safe candidate view without body text."""

        return {
            "candidate_id": candidate.candidate_id,
            "kind": str(getattr(candidate.kind, "value", candidate.kind)),
            "source_id": candidate.source_id,
            "role": candidate.role,
            "retention": str(getattr(candidate.retention, "value", candidate.retention)),
            "trust": str(getattr(candidate.trust, "value", candidate.trust)),
            "freshness": str(getattr(candidate.freshness, "value", candidate.freshness)),
            "truncation": str(getattr(candidate.truncation, "value", candidate.truncation)),
            "source_order": candidate.source_order,
            "content_sha256": "sha256:" + hashlib.sha256(
                candidate.content.encode("utf-8")
            ).hexdigest(),
            "compacted_candidate_ids": list(candidate.compacted_candidate_ids),
        }

    def _dynamic_rolling_summary_budget(
        self,
        *,
        initial: ContextAssemblyResult,
        dialog_candidates: list[ContextCandidate],
        compacted_ids: list[str],
        policy: ContextAssemblyPolicy,
    ) -> int:
        """Compute a bounded summary slot from the current prompt envelope.

        The static cap remains the ceiling.  Dynamic accounting is enabled only
        when the same exact provider tokenizer used by ContextAssembler is
        available; otherwise the caller keeps the static-cap compatibility path.
        """

        if policy.max_prompt_tokens is None or self.token_counter is None or not self.token_counter.available:
            return self.rolling_summary_token_limit
        used_prompt_tokens = initial.selection.final_prompt_tokens
        if used_prompt_tokens is None:
            return self.rolling_summary_token_limit
        recent_suffix = [
            candidate
            for candidate in dialog_candidates
            if candidate.candidate_id not in set(compacted_ids)
        ][-2:]
        try:
            recent_suffix_tokens = sum(
                int(self.token_counter.count_text(candidate.content))
                for candidate in recent_suffix
            )
            return calculate_summary_budget(
                static_cap_tokens=self.rolling_summary_token_limit,
                requested_prompt_tokens=int(policy.max_prompt_tokens),
                used_prompt_tokens=int(used_prompt_tokens),
                required_reserve_tokens=int(policy.reserved_prompt_tokens),
                recent_suffix_reserve_tokens=recent_suffix_tokens,
                response_schema_reserve_tokens=ROLLING_SUMMARY_RESPONSE_SCHEMA_RESERVE_TOKENS,
            )
        except (TypeError, ValueError):
            # A tokenizer or metadata boundary failure must not widen the
            # derived summary budget.  Keep the deterministic source path.
            return 0

    @classmethod
    def _dialog_compaction_record(
        cls,
        candidates: list[ContextCandidate],
    ) -> ContextCompactionRecord:
        if not candidates or any(candidate.role != "assistant" for candidate in candidates):
            raise ValueError("observation compaction requires assistant dialog sources")
        fingerprint = source_candidate_fingerprint(candidates)
        original_chars = sum(len(candidate.content) for candidate in candidates)
        heading = f"Earlier dialog ({len(candidates)} messages):"
        primary_lines: list[str] = []
        secondary_lines: list[str] = []
        signal_pattern = re.compile(
            r"\b(?:must|required?|requirement|decision|constraint|goal|command|error|"
            r"failed?|failure|exception|exit\s+code|result|next|verify|validation|path|"
            r"file|permission|budget|retry|recovery|blocked|warning)\b|"
            r"必须|应该|需求|决定|目标|约束|命令|错误|失败|异常|结果|验证|路径|权限|预算|恢复|下一步",
            re.IGNORECASE,
        )
        marker_pattern = re.compile(
            r"\b[A-Za-z][A-Za-z0-9_:-]{1,64}=[A-Za-z0-9_./:-]+"
        )

        def project_signal_line(line: str) -> str:
            markers: list[str] = []
            for match in marker_pattern.finditer(line):
                marker = match.group(0).rstrip(".,;:，；。")
                if marker and marker not in markers:
                    markers.append(marker)
                if len(markers) >= 4:
                    break
            if markers:
                return " ".join(markers)
            return line

        for candidate in candidates:
            content_lines = [
                " ".join(line.split())
                for line in candidate.content.splitlines()
                if line.strip()
            ]
            if not content_lines:
                continue
            signals = [
                project_signal_line(line)
                for line in content_lines
                if signal_pattern.search(line) or marker_pattern.search(line)
            ]
            chosen = signals or [
                project_signal_line(line) for line in content_lines[:1]
            ]
            primary_lines.append(chosen[0])
            if len(chosen) > 1 and chosen[-1] != chosen[0]:
                secondary_lines.append(chosen[-1])

        sampled_lines = primary_lines[:2]
        if len(primary_lines) > 2:
            sampled_lines.extend(primary_lines[-2:])
        sampled_lines.extend(secondary_lines)
        signal_lines: list[str] = []
        for line in sampled_lines:
            if line not in signal_lines and len(signal_lines) < 4:
                signal_lines.append(line)

        marker = (
            f"[observation masked: source_chars={original_chars}; "
            f"{fingerprint[:23]}...]"
        )
        maximum = min(240, original_chars - 1)
        available = max(1, maximum - len(heading) - len(marker) - len(signal_lines) - 1)
        per_line = max(12, available // max(1, len(signal_lines)) - 2)
        projected_lines = []
        for line in signal_lines:
            projected = line
            if len(projected) > per_line:
                projected = projected[: max(1, per_line - 3)].rstrip() + "..."
            projected_lines.append(f"- {projected}")
        summary = "\n".join([heading, *projected_lines, marker])
        if len(summary) > maximum:
            summary = summary[: max(1, maximum - len(marker) - 1)].rstrip() + "\n" + marker
        return ContextCompactionRecord(
            compaction_id=fingerprint.removeprefix("sha256:")[:24],
            source_fingerprint=fingerprint,
            source_candidate_ids=[candidate.candidate_id for candidate in candidates],
            algorithm="deterministic_observation_mask_v1",
            summary=summary,
            original_chars=original_chars,
            compacted_chars=len(summary),
        )

    def _typed_candidates(
        self,
        payload: dict[str, Any],
    ) -> tuple[list[ContextCandidate], dict[str, tuple[str, Any]]]:
        """Adapt owned source values into stable, model-facing candidates."""
        candidates: list[ContextCandidate] = []
        sources: dict[str, tuple[str, Any]] = {}

        def add(candidate: ContextCandidate, section: str, source: Any) -> None:
            candidates.append(candidate)
            sources[candidate.candidate_id] = (section, source)

        system_prompt = str(payload.get("system_prompt") or "")
        if system_prompt:
            add(
                ContextCandidate(
                    candidate_id="legacy:system_prompt",
                    kind=ContextCandidateKind.INSTRUCTION,
                    source_id="memory_context_builder:system_prompt",
                    content=system_prompt,
                    role="system",
                    retention=ContextCandidateRetention.REQUIRED,
                    priority=100,
                    source_order=0,
                    truncation=ContextCandidateTruncation.FORBIDDEN,
                    trust=ContextCandidateTrust.AUTHORITATIVE,
                    freshness=ContextCandidateFreshness.CURRENT,
                ),
                "system_prompt",
                system_prompt,
            )

        session_constraints = payload.get("session_constraints")
        if isinstance(session_constraints, SessionConstraintState):
            candidate = build_session_constraint_candidate(session_constraints)
            if candidate is not None:
                add(
                    candidate,
                    "session_constraints",
                    session_constraint_projection(session_constraints),
                )

        dialog = list(payload.get("dialog_context") or [])
        for index, item in enumerate(dialog):
            attributes = item.get("attributes")
            attributes = attributes if isinstance(attributes, dict) else {}
            source_id = str(
                item.get("message_id")
                or attributes.get("message_id")
                or item.get("timestamp")
                or ""
            )
            identity = self._stable_id(
                str(item.get("role") or ""),
                source_id,
                str(item.get("content") or ""),
            )
            add(
                ContextCandidate(
                    candidate_id=f"dialog:{identity}",
                    kind=ContextCandidateKind.DIALOG,
                    source_id=source_id or identity,
                    content=f"{str(item.get('role') or 'user').upper()}: {item.get('content', '')}",
                    role=(
                        item.get("role")
                        if item.get("role") in {"system", "user", "assistant"}
                        else "user"
                    ),
                    retention=ContextCandidateRetention.PREFERRED,
                    priority=max(1, 100 - (len(dialog) - index - 1)),
                    source_order=1_000 + index,
                    truncation=ContextCandidateTruncation.HEAD,
                    trust=ContextCandidateTrust.DIRECT,
                    freshness=ContextCandidateFreshness.CURRENT,
                ),
                "dialog_context",
                item,
            )

        for index, item in enumerate(payload.get("related_files") or []):
            path = str(item.get("path") or item.get("name") or index)
            candidate_id = f"project_file:{self._stable_id(path)}"
            add(
                ContextCandidate(
                    candidate_id=candidate_id,
                    kind=ContextCandidateKind.PROJECT_FILE,
                    source_id=path,
                    content=f"- {path}: {item.get('description', '')}",
                    retention=ContextCandidateRetention.PREFERRED,
                    priority=self._score_priority(item.get("score"), default=50),
                    source_order=2_000 + index,
                    truncation=ContextCandidateTruncation.HEAD,
                    trust=ContextCandidateTrust.OBSERVED,
                    freshness=ContextCandidateFreshness.CURRENT,
                ),
                "related_files",
                item,
            )

        for index, item in enumerate(payload.get("related_memories") or []):
            source_id = str(item.get("id") or self._stable_id(str(item.get("content") or index)))
            add(
                ContextCandidate(
                    candidate_id=f"memory:{source_id}",
                    kind=ContextCandidateKind.MEMORY,
                    source_id=source_id,
                    content=f"- [{item.get('type', '')}] {item.get('content', '')}",
                    retention=ContextCandidateRetention.PREFERRED,
                    priority=self._score_priority(
                        item.get("score", item.get("confidence")),
                        default=50,
                    ),
                    source_order=3_000 + index,
                    truncation=ContextCandidateTruncation.HEAD,
                    trust=ContextCandidateTrust.RETRIEVED,
                    freshness=ContextCandidateFreshness.HISTORICAL,
                ),
                "related_memories",
                item,
            )

        environment = list(payload.get("environment_context") or [])
        for index, item in enumerate(environment):
            source_id = str(item.get("id") or self._stable_id(str(item.get("content") or index)))
            add(
                ContextCandidate(
                    candidate_id=f"environment:{source_id}",
                    kind=ContextCandidateKind.ENVIRONMENT,
                    source_id=source_id,
                    content=f"- {item.get('content', '')}",
                    retention=ContextCandidateRetention.OPTIONAL,
                    priority=max(1, 100 - (len(environment) - index - 1)),
                    source_order=4_000 + index,
                    truncation=ContextCandidateTruncation.HEAD,
                    trust=ContextCandidateTrust.OBSERVED,
                    freshness=ContextCandidateFreshness.HISTORICAL,
                ),
                "environment_context",
                item,
            )
        return candidates, sources

    def _compatibility_payload(
        self,
        payload: dict[str, Any],
        *,
        candidates: list[ContextCandidate],
        sources: dict[str, tuple[str, Any]],
        assembly: ContextAssemblyResult,
        compaction_bindings: list[ContextCompactionBinding],
        request_hash: str,
        session_turn_source_hash: str = "",
        session_constraints_hash: str = "",
    ) -> dict[str, Any]:
        """Project typed decisions back into the historical section-shaped view."""
        decisions = {
            decision.candidate_id: decision
            for decision in assembly.selection.candidate_decisions
        }
        selected_by_id = {
            candidate.candidate_id: candidate
            for candidate in assembly.selected_candidates
        }
        selected: dict[str, Any] = {
            "query": payload.get("query", ""),
            "project_path": payload.get("project_path", ""),
            "system_prompt": "",
            "dialog_context": [],
            "related_files": [],
            "related_memories": [],
            "environment_context": [],
            "context_compactions": [],
            # Typed, selected-only projection for downstream purpose adapters.
            # Raw ingress and source stores remain the authority; this field is
            # only the exact result of this assembly pass.
            "selected_context_candidates": [],
            "context_request_hash": request_hash,
            "session_turn_source_hash": session_turn_source_hash,
            "session_constraints_hash": session_constraints_hash,
        }
        if isinstance(payload.get("session_constraints"), SessionConstraintState):
            selected["session_constraints"] = []
        for candidate in candidates:
            decision = decisions[candidate.candidate_id]
            if decision.action == "omitted":
                continue
            section, source = sources[candidate.candidate_id]
            if section == "system_prompt":
                selected[section] = source
            else:
                selected[section].append(copy.deepcopy(source))

        section_decisions = []
        for section in (
            "system_prompt",
            "session_constraints",
            "dialog_context",
            "related_files",
            "related_memories",
            "environment_context",
        ):
            section_candidates = [
                candidate
                for candidate in candidates
                if sources[candidate.candidate_id][0] == section
            ]
            section_selected = [
                selected_by_id[candidate.candidate_id]
                for candidate in section_candidates
                if candidate.candidate_id in selected_by_id
            ]
            section_actions = [decisions[candidate.candidate_id].action for candidate in section_candidates]
            governed = any(
                decisions[candidate.candidate_id].reason
                in {
                    "duplicate",
                    "stale",
                    "conflict_precedence",
                    "governance_conflict",
                    "compacted",
                }
                for candidate in section_candidates
            )
            if not section_candidates:
                action, reason = "empty", "empty"
            elif all(item == "kept" for item in section_actions):
                action, reason = "kept", "within_budget"
            elif all(item == "omitted" for item in section_actions):
                action = "omitted"
                reason = "source_governance" if governed else "prompt_budget"
            else:
                action = "partially_kept"
                reason = "source_governance" if governed else "prompt_budget"
            section_decisions.append(
                {
                    "section": section,
                    "action": action,
                    "reason": reason,
                    "original_items": len(section_candidates),
                    "selected_items": len(section_selected),
                    "original_chars": len(self._render_candidates(section_candidates)),
                    "selected_chars": len(self._render_candidates(section_selected)),
                }
            )

        dialog = list(payload.get("dialog_context") or [])
        selected_dialog = selected["dialog_context"]
        first_selected = (
            next(
                (
                    index
                    for index, item in enumerate(dialog)
                    if item is selected_dialog[0] or item == selected_dialog[0]
                ),
                len(dialog),
            )
            if selected_dialog
            else len(dialog)
        )
        selection_payload = assembly.selection.model_dump(mode="json")
        selection_payload.update(
            {
                "section_decisions": section_decisions,
                "dialog_messages_total": len(dialog),
                "dialog_messages_selected": len(selected_dialog),
                "dialog_start_index": first_selected,
                "oldest_selected_dialog_timestamp": (
                    str(selected_dialog[0].get("timestamp")) if selected_dialog else None
                ),
                "latest_dialog_timestamp": (
                    str(dialog[-1].get("timestamp")) if dialog else None
                ),
            }
        )
        selected["prompt_text"] = assembly.prompt_text
        selected["selected_context_candidates"] = [
            candidate.model_dump(mode="json")
            for candidate in assembly.selected_candidates
        ]
        if compaction_bindings:
            selected["context_compactions"] = [
                binding.model_dump(mode="json") for binding in compaction_bindings
            ]
        selected["context_selection"] = ContextSelectionMetadata.model_validate(
            selection_payload
        ).to_json_dict()
        return selected

    @staticmethod
    def _render_candidates(candidates: list[ContextCandidate]) -> str:
        sections = []
        groups = (
            (ContextCandidateKind.INSTRUCTION, "## System Prompt", "\n"),
            (ContextCandidateKind.CONSTRAINT, "## Active Session Constraints", "\n"),
            (ContextCandidateKind.ARTIFACT, "## Earlier Dialog Summary", "\n"),
            (ContextCandidateKind.DIALOG, "## Dialog Context", "\n\n"),
            (ContextCandidateKind.PROJECT_FILE, "## Related Files", "\n"),
            (ContextCandidateKind.MEMORY, "## Related Memories", "\n"),
            (ContextCandidateKind.ENVIRONMENT, "## Environment Context", "\n"),
        )
        for kind, heading, separator in groups:
            content = [
                candidate.content
                for candidate in candidates
                if str(getattr(candidate.kind, "value", candidate.kind)) == kind.value
            ]
            if content:
                sections.append(heading + "\n" + separator.join(content))
        return "\n\n".join(sections)

    @staticmethod
    def _stable_id(*parts: str) -> str:
        encoded = "\x1f".join(parts).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:20]

    @staticmethod
    def _score_priority(value: Any, *, default: int) -> int:
        try:
            score = float(value)
        except (TypeError, ValueError):
            return default
        if score <= 1:
            score *= 100
        return max(0, min(100, int(round(score))))

    def _dialog_context(self, limit: int) -> list[dict[str, Any]]:
        messages = self.short_memory.get_context(limit=limit)
        return [
            {
                "role": message.role,
                "content": message.content,
                "timestamp": str(message.timestamp),
                "attributes": dict(message.attributes),
            }
            for message in messages
        ]

    @staticmethod
    def _session_ingress_turn_ledger_hash(
        state: SessionIngressState,
    ) -> str:
        """Return a stable digest for one raw, conversation-scoped turn ledger."""
        return session_turn_ledger_hash(state)

    @staticmethod
    def _session_ingress_dialog_context(
        state: SessionIngressState,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Project raw ingress turns without reading or mutating short memory."""
        bounded_limit = max(0, int(limit))
        turns = state.turns[-bounded_limit:] if bounded_limit else list(state.turns)
        return [
            {
                "role": turn.role,
                "content": turn.content,
                "message_id": turn.message_id,
                "timestamp": f"turn:{turn.identity.turn_index}",
                "attributes": {
                    "source": "session_ingress",
                    "message_id": turn.message_id,
                    "conversation_id": turn.identity.conversation_id,
                    "run_id": turn.identity.run_id,
                    "turn_index": turn.identity.turn_index,
                    "project_root": turn.identity.project_root,
                },
            }
            for turn in turns
        ]

    def _related_memories(
        self,
        query: str,
        limit: int,
        project_path: Path | None = None,
        *,
        strict_sources: bool = False,
    ) -> list[dict[str, Any]]:
        if not query:
            return []
        if self.memory_vault_agent is not None:
            try:
                reminders = self.memory_vault_agent.remind(query, limit=limit)
                if reminders:
                    payloads = [self._vault_memory_payload(item) for item in reminders]
                    return [
                        item for item in payloads
                        if self._memory_payload_matches_project(item, project_path)
                    ][:limit]
            except Exception as exc:
                if strict_sources:
                    raise ContextSourceError("memory_vault", exc) from exc
                pass
        try:
            result = self.memory_store.query(
                query,
                memory_types=self.DEFAULT_MEMORY_TYPES,
                limit=limit,
            )
        except Exception as exc:
            if strict_sources:
                raise ContextSourceError("related_memories", exc) from exc
            return []
        payloads = [self._memory_record_payload(memory) for memory in result.memories]
        return [
            item for item in payloads
            if self._memory_payload_matches_project(item, project_path)
        ][:limit]

    def _environment_context(
        self,
        project_path: Path | None,
        *,
        strict_sources: bool = False,
    ) -> list[dict[str, Any]]:
        try:
            memories = self.memory_store.load_all(MemoryType.SHORT_TERM)
        except Exception as exc:
            if strict_sources:
                raise ContextSourceError("environment_context", exc) from exc
            return []

        env_memories = [
            memory
            for memory in memories
            if "project_environment" in memory.tags
            and self._memory_payload_matches_project(
                self._memory_record_payload(memory),
                project_path,
            )
        ][-3:]
        return [self._memory_record_payload(memory) for memory in env_memories]

    @staticmethod
    def _memory_payload_matches_project(
        memory: dict[str, Any],
        project_path: Path | None,
    ) -> bool:
        if project_path is None:
            return True
        attributes = memory.get("attributes") if isinstance(memory.get("attributes"), dict) else {}
        raw_project_path = str(attributes.get("project_path") or "").strip()
        try:
            canonical_project = project_path.expanduser().resolve(strict=False)
        except OSError:
            return False
        if raw_project_path:
            try:
                return Path(raw_project_path).expanduser().resolve(strict=False) == canonical_project
            except OSError:
                return False
        memory_type = str(memory.get("type") or "")
        if memory_type in {
            MemoryType.USER.value,
            MemoryType.FEEDBACK.value,
            MemoryType.LONG_TERM.value,
            MemoryType.REFERENCE.value,
        }:
            return True
        canonical_text = str(canonical_project).casefold()
        tags = {str(tag).strip().casefold() for tag in memory.get("tags") or [] if str(tag).strip()}
        return canonical_text in tags

    @staticmethod
    def _memory_record_payload(memory: MemoryRecord) -> dict[str, Any]:
        return {
            "id": memory.id,
            "type": memory.memory_type.value,
            "content": memory.content,
            "tags": memory.tags,
            "confidence": memory.confidence,
            "attributes": memory.attributes,
        }

    @staticmethod
    def _vault_memory_payload(memory: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": memory.get("id", ""),
            "type": memory.get("type", MemoryType.PROJECT.value),
            "content": memory.get("content", ""),
            "tags": list(memory.get("tags") or []),
            "confidence": float(memory.get("confidence", 0.5)),
            "attributes": dict(memory.get("attributes") or {}),
            "score": float(memory.get("score", 0.0)),
        }

    @staticmethod
    def _prompt_text(payload: dict[str, Any]) -> str:
        sections = []
        if payload.get("system_prompt"):
            sections.append("## System Prompt\n" + str(payload["system_prompt"]))

        if payload["dialog_context"]:
            lines = [
                f"{item['role'].upper()}: {item['content']}"
                for item in payload["dialog_context"]
            ]
            sections.append("## Dialog Context\n" + "\n\n".join(lines))

        if payload["related_files"]:
            lines = [
                f"- {item['path']}: {item['description']}"
                for item in payload["related_files"]
            ]
            sections.append("## Related Files\n" + "\n".join(lines))

        if payload["related_memories"]:
            lines = [
                f"- [{item['type']}] {item['content']}"
                for item in payload["related_memories"]
            ]
            sections.append("## Related Memories\n" + "\n".join(lines))

        if payload["environment_context"]:
            lines = [f"- {item['content']}" for item in payload["environment_context"]]
            sections.append("## Environment Context\n" + "\n".join(lines))

        return "\n\n".join(sections)
