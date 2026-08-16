"""Budget and select a deterministic model-facing context projection."""

from __future__ import annotations

import hashlib
import json
import warnings
from enum import StrEnum
from typing import Any, Callable, Mapping, Protocol

from metadata import (
    ContextAssemblyPolicy,
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextCandidate,
    ContextCandidateDecision,
    ContextCandidateFreshness,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ContextSelectionMetadata,
)


CONTEXT_ASSEMBLY_STRATEGY = "retention_priority_order_v1"
TRUNCATION_MARKER = "\n...[truncated by context budget]"
TAIL_TRUNCATION_MARKER = "...[truncated by context budget]\n"


class ContextSection(StrEnum):
    """Sections governed by the current context-selection contract."""

    SYSTEM_PROMPT = "system_prompt"
    DIALOG_CONTEXT = "dialog_context"
    RELATED_FILES = "related_files"
    RELATED_MEMORIES = "related_memories"
    ENVIRONMENT_CONTEXT = "environment_context"


class TokenCounter(Protocol):
    available: bool
    tokenizer_id: str
    model: str

    def count_text(self, text: str) -> int: ...


class ContextAssembler:
    """Apply the current typed selection policy without owning source context."""

    _LIST_SECTIONS = (
        ContextSection.DIALOG_CONTEXT,
        ContextSection.RELATED_FILES,
        ContextSection.RELATED_MEMORIES,
        ContextSection.ENVIRONMENT_CONTEXT,
    )
    _FORWARD_SECTIONS = (
        (ContextSection.RELATED_FILES, "description"),
        (ContextSection.RELATED_MEMORIES, "content"),
        (ContextSection.ENVIRONMENT_CONTEXT, "content"),
    )

    def __init__(
        self,
        *,
        renderer: Callable[[dict[str, Any]], str],
        token_counter: TokenCounter | None = None,
    ) -> None:
        self.renderer = renderer
        self.token_counter = token_counter

    def assemble(
        self,
        payload: Mapping[str, Any],
        *,
        max_prompt_chars: int,
        max_prompt_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Return a bounded copy plus the existing selection evidence contract."""
        warnings.warn(
            "ContextAssembler.assemble(payload) is deprecated; use assemble_candidates",
            DeprecationWarning,
            stacklevel=2,
        )
        max_prompt_tokens = self._effective_token_budget(max_prompt_tokens)
        original = self._copy_payload(payload)
        original_prompt = self.renderer(original)
        original_tokens = self._count_tokens(original_prompt) if max_prompt_tokens is not None else None
        if self._prompt_fits(
            original_prompt,
            max_prompt_chars=max_prompt_chars,
            max_prompt_tokens=max_prompt_tokens,
        ):
            selected = original
        else:
            selected = self._select_payload(
                original,
                max_prompt_chars=max_prompt_chars,
                max_prompt_tokens=max_prompt_tokens,
            )

        final_prompt = self.renderer(selected)
        selected["prompt_text"] = final_prompt
        selected["context_selection"] = self._selection_metadata(
            original,
            selected,
            max_prompt_chars=max_prompt_chars,
            original_prompt_chars=len(original_prompt),
            final_prompt_chars=len(final_prompt),
            original_prompt_tokens=original_tokens,
            final_prompt_tokens=(
                self._count_tokens(final_prompt) if max_prompt_tokens is not None else None
            ),
            max_prompt_tokens=max_prompt_tokens,
        ).to_json_dict()
        return selected

    def assemble_candidates(
        self,
        candidates: list[ContextCandidate],
        *,
        policy: ContextAssemblyPolicy,
        renderer: Callable[[list[ContextCandidate]], str] | None = None,
        _atomic_compaction_pass: bool = False,
    ) -> ContextAssemblyResult:
        """Assemble typed candidates without taking ownership of their sources."""
        candidate_ids = [candidate.candidate_id for candidate in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("context candidate IDs must be unique")

        render = renderer or self._render_candidates
        source_candidates = [candidate.model_copy(deep=True) for candidate in candidates]
        source_candidates.sort(key=self._source_order_key)
        if not _atomic_compaction_pass and any(
            candidate.compacted_candidate_ids for candidate in source_candidates
        ):
            return self._assemble_atomic_compactions(
                source_candidates,
                policy=policy,
                renderer=render,
            )
        requested_prompt_tokens = self._effective_token_budget(policy.max_prompt_tokens)
        max_prompt_tokens = (
            requested_prompt_tokens - policy.reserved_prompt_tokens
            if requested_prompt_tokens is not None
            else None
        )
        original_prompt = render(source_candidates)
        governed_candidates, decision_by_id, governance_blocked_ids = self._govern_candidates(
            source_candidates,
            policy=policy,
            max_prompt_tokens=max_prompt_tokens,
        )
        selected: list[ContextCandidate] = []

        for candidate in sorted(governed_candidates, key=self._selection_order_key):
            complete_selection = self._with_candidate(selected, candidate)
            if self._candidate_prompt_fits(
                render(complete_selection),
                max_prompt_chars=policy.max_prompt_chars,
                max_prompt_tokens=max_prompt_tokens,
            ):
                selected = complete_selection
                projected = candidate
                action = "kept"
                reason = "within_budget"
            else:
                projected = self._fit_typed_candidate(
                    selected,
                    candidate,
                    renderer=render,
                    max_prompt_chars=policy.max_prompt_chars,
                    max_prompt_tokens=max_prompt_tokens,
                )
                if projected is None:
                    action = "omitted"
                    reason = "prompt_budget"
                else:
                    selected = self._with_candidate(selected, projected)
                    action = "partially_kept"
                    reason = "prompt_budget"

            selected_content = projected.content if projected is not None else ""
            decision_by_id[candidate.candidate_id] = ContextCandidateDecision(
                candidate_id=candidate.candidate_id,
                kind=candidate.kind,
                source_id=candidate.source_id,
                retention=candidate.retention,
                trust=candidate.trust,
                freshness=candidate.freshness,
                action=action,
                reason=reason,
                original_chars=len(candidate.content),
                selected_chars=len(selected_content),
                original_tokens=(
                    self._count_tokens(candidate.content) if max_prompt_tokens is not None else None
                ),
                selected_tokens=(
                    self._count_tokens(selected_content) if max_prompt_tokens is not None else None
                ),
            )

        selected.sort(key=self._source_order_key)
        final_prompt = render(selected)
        decisions = [decision_by_id[candidate.candidate_id] for candidate in source_candidates]
        omitted_required_ids = [
            decision.candidate_id
            for decision in decisions
            if decision.retention == ContextCandidateRetention.REQUIRED
            and decision.action == "omitted"
            and decision.reason != "duplicate"
        ]
        if governance_blocked_ids:
            status = ContextAssemblyStatus.GOVERNANCE_BLOCKED
        elif omitted_required_ids:
            status = ContextAssemblyStatus.BUDGET_INSUFFICIENT
        else:
            status = ContextAssemblyStatus.READY
        selection = ContextSelectionMetadata(
            strategy="retention_priority_order_v1",
            request_purpose=policy.purpose,
            budget_unit="tokens" if max_prompt_tokens is not None else "characters",
            max_prompt_chars=policy.max_prompt_chars,
            max_prompt_tokens=max_prompt_tokens,
            requested_prompt_tokens=requested_prompt_tokens,
            reserved_prompt_tokens=(
                policy.reserved_prompt_tokens if requested_prompt_tokens is not None else 0
            ),
            remaining_prompt_tokens=(
                max_prompt_tokens - self._count_tokens(final_prompt)
                if max_prompt_tokens is not None
                else None
            ),
            original_prompt_chars=len(original_prompt),
            final_prompt_chars=len(final_prompt),
            original_prompt_tokens=(
                self._count_tokens(original_prompt) if max_prompt_tokens is not None else None
            ),
            final_prompt_tokens=(
                self._count_tokens(final_prompt) if max_prompt_tokens is not None else None
            ),
            token_count_method=(
                "provider_tokenizer" if max_prompt_tokens is not None else "unavailable"
            ),
            tokenizer_id=(
                self.token_counter.tokenizer_id
                if max_prompt_tokens is not None and self.token_counter is not None
                else ""
            ),
            model=(
                self.token_counter.model
                if max_prompt_tokens is not None and self.token_counter is not None
                else ""
            ),
            truncated=any(decision.action != "kept" for decision in decisions),
            assembly_status=status,
            candidate_decisions=decisions,
            omitted_required_candidate_ids=omitted_required_ids,
            governance_blocked_candidate_ids=governance_blocked_ids,
        )
        return ContextAssemblyResult(
            prompt_text=final_prompt,
            selected_candidates=selected,
            selection=selection,
        )

    def _assemble_atomic_compactions(
        self,
        source_candidates: list[ContextCandidate],
        *,
        policy: ContextAssemblyPolicy,
        renderer: Callable[[list[ContextCandidate]], str],
    ) -> ContextAssemblyResult:
        """Iteratively remove failed compactors and restore their governed sources."""
        working = list(source_candidates)
        removed_decisions: dict[str, ContextCandidateDecision] = {}
        compactor_count = sum(
            bool(candidate.compacted_candidate_ids) for candidate in source_candidates
        )
        for _ in range(compactor_count + 1):
            attempt = self.assemble_candidates(
                working,
                policy=policy,
                renderer=renderer,
                _atomic_compaction_pass=True,
            )
            decision_by_id = {
                decision.candidate_id: decision
                for decision in attempt.selection.candidate_decisions
            }
            failed = [
                candidate
                for candidate in working
                if candidate.compacted_candidate_ids
                and decision_by_id[candidate.candidate_id].action != "kept"
            ]
            if failed:
                for candidate in failed:
                    removed_decisions[candidate.candidate_id] = decision_by_id[
                        candidate.candidate_id
                    ]
                failed_ids = {candidate.candidate_id for candidate in failed}
                working = [
                    candidate
                    for candidate in working
                    if candidate.candidate_id not in failed_ids
                ]
                continue

            if not removed_decisions:
                return attempt
            merged_by_id = {
                **decision_by_id,
                **removed_decisions,
            }
            merged_decisions = [
                merged_by_id[candidate.candidate_id]
                for candidate in source_candidates
            ]
            original_prompt = renderer(source_candidates)
            max_prompt_tokens = self._effective_token_budget(policy.max_prompt_tokens)
            if max_prompt_tokens is not None:
                max_prompt_tokens -= policy.reserved_prompt_tokens
            selection = ContextSelectionMetadata.model_validate(
                {
                    **attempt.selection.model_dump(mode="python"),
                    "original_prompt_chars": len(original_prompt),
                    "original_prompt_tokens": (
                        self._count_tokens(original_prompt)
                        if max_prompt_tokens is not None
                        else None
                    ),
                    "truncated": any(
                        decision.action != "kept" for decision in merged_decisions
                    ),
                    "candidate_decisions": merged_decisions,
                }
            )
            return ContextAssemblyResult(
                prompt_text=attempt.prompt_text,
                selected_candidates=attempt.selected_candidates,
                selection=selection,
            )
        raise RuntimeError("atomic context compaction did not converge")

    def request_hash(
        self,
        request: Mapping[str, Any],
        *,
        max_prompt_chars: int,
        max_prompt_tokens: int | None,
    ) -> str:
        """Fingerprint source request facts and the selection policy for replay."""
        max_prompt_tokens = self._effective_token_budget(max_prompt_tokens)
        payload = {
            **dict(request),
            "max_prompt_chars": int(max_prompt_chars),
            "max_prompt_tokens": max_prompt_tokens,
            "tokenizer_id": (
                self.token_counter.tokenizer_id
                if max_prompt_tokens is not None and self.token_counter is not None
                else ""
            ),
            "strategy": CONTEXT_ASSEMBLY_STRATEGY,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def _effective_token_budget(self, requested: int | None) -> int | None:
        if (
            requested is None
            or requested <= 0
            or self.token_counter is None
            or not self.token_counter.available
        ):
            return None
        return int(requested)

    def _govern_candidates(
        self,
        candidates: list[ContextCandidate],
        *,
        policy: ContextAssemblyPolicy,
        max_prompt_tokens: int | None,
    ) -> tuple[
        list[ContextCandidate],
        dict[str, ContextCandidateDecision],
        list[str],
    ]:
        """Apply deterministic typed governance before budget selection."""
        active = {candidate.candidate_id: candidate for candidate in candidates}
        decisions: dict[str, ContextCandidateDecision] = {}
        blocked_ids: list[str] = []

        compacted_source_ids: set[str] = set()
        for compactor in candidates:
            if not compactor.compacted_candidate_ids:
                continue
            for source_id in compactor.compacted_candidate_ids:
                source = active.get(source_id)
                if source is None:
                    raise ValueError(
                        f"compaction candidate references unknown source {source_id!r}"
                    )
                if source.compacted_candidate_ids:
                    raise ValueError("context compaction candidates cannot be compacted")
                if source.retention == ContextCandidateRetention.REQUIRED:
                    raise ValueError("required context candidate cannot be compacted")
                if source_id in compacted_source_ids:
                    raise ValueError("context candidate cannot be compacted more than once")
                decisions[source_id] = self._governance_omission(
                    source,
                    reason="compacted",
                    governed_by_candidate_id=compactor.candidate_id,
                    max_prompt_tokens=max_prompt_tokens,
                )
                compacted_source_ids.add(source_id)
                active.pop(source_id, None)

        if policy.omit_stale_candidates:
            for candidate in candidates:
                if candidate.freshness != ContextCandidateFreshness.STALE:
                    continue
                decisions[candidate.candidate_id] = self._governance_omission(
                    candidate,
                    reason="stale",
                    max_prompt_tokens=max_prompt_tokens,
                )
                active.pop(candidate.candidate_id, None)
                if candidate.retention == ContextCandidateRetention.REQUIRED:
                    blocked_ids.append(candidate.candidate_id)

        if policy.govern_exact_duplicates:
            duplicate_groups: dict[tuple[str, str], list[ContextCandidate]] = {}
            for candidate in active.values():
                key = (
                    str(getattr(candidate.kind, "value", candidate.kind)),
                    self._normalized_content(candidate.content),
                )
                duplicate_groups.setdefault(key, []).append(candidate)
            for group in duplicate_groups.values():
                if len(group) < 2:
                    continue
                # Provider assistant/tool wire messages are required state,
                # not interchangeable evidence. Two tool results can have
                # identical compact payloads while carrying different
                # ``tool_call_id`` values; deduplicating one would leave an
                # assistant tool-call bundle without its matching result.
                round_trip_group = [
                    candidate
                    for candidate in group
                    if candidate.role == "tool"
                    or (
                        candidate.role == "assistant"
                        and candidate.truncation == ContextCandidateTruncation.FORBIDDEN
                    )
                ]
                if round_trip_group:
                    continue
                winner = max(group, key=self._governance_precedence)
                for candidate in group:
                    if candidate.candidate_id == winner.candidate_id:
                        continue
                    decisions[candidate.candidate_id] = self._governance_omission(
                        candidate,
                        reason="duplicate",
                        governed_by_candidate_id=winner.candidate_id,
                        max_prompt_tokens=max_prompt_tokens,
                    )
                    active.pop(candidate.candidate_id, None)

        if policy.resolve_explicit_conflicts:
            conflict_groups: dict[str, list[ContextCandidate]] = {}
            for candidate in active.values():
                if candidate.conflict_key:
                    conflict_groups.setdefault(candidate.conflict_key, []).append(candidate)
            for group in conflict_groups.values():
                distinct_content = {
                    self._normalized_content(candidate.content) for candidate in group
                }
                if len(group) < 2 or len(distinct_content) < 2:
                    continue
                required = [
                    candidate
                    for candidate in group
                    if candidate.retention == ContextCandidateRetention.REQUIRED
                ]
                if len(required) > 1:
                    for candidate in group:
                        decisions[candidate.candidate_id] = self._governance_omission(
                            candidate,
                            reason="governance_conflict",
                            max_prompt_tokens=max_prompt_tokens,
                        )
                        active.pop(candidate.candidate_id, None)
                    blocked_ids.extend(candidate.candidate_id for candidate in required)
                    continue
                winner = max(group, key=self._governance_precedence)
                for candidate in group:
                    if candidate.candidate_id == winner.candidate_id:
                        continue
                    decisions[candidate.candidate_id] = self._governance_omission(
                        candidate,
                        reason="conflict_precedence",
                        governed_by_candidate_id=winner.candidate_id,
                        max_prompt_tokens=max_prompt_tokens,
                    )
                    active.pop(candidate.candidate_id, None)

        governed = [
            candidate for candidate in candidates if candidate.candidate_id in active
        ]
        return governed, decisions, blocked_ids

    def _governance_omission(
        self,
        candidate: ContextCandidate,
        *,
        reason: str,
        max_prompt_tokens: int | None,
        governed_by_candidate_id: str | None = None,
    ) -> ContextCandidateDecision:
        return ContextCandidateDecision(
            candidate_id=candidate.candidate_id,
            kind=candidate.kind,
            source_id=candidate.source_id,
            retention=candidate.retention,
            trust=candidate.trust,
            freshness=candidate.freshness,
            action="omitted",
            reason=reason,
            governed_by_candidate_id=governed_by_candidate_id,
            original_chars=len(candidate.content),
            selected_chars=0,
            original_tokens=(
                self._count_tokens(candidate.content)
                if max_prompt_tokens is not None
                else None
            ),
            selected_tokens=0 if max_prompt_tokens is not None else None,
        )

    @staticmethod
    def _normalized_content(content: str) -> str:
        return " ".join(content.split())

    @staticmethod
    def _governance_precedence(candidate: ContextCandidate) -> tuple[int, int, int, int, int, str]:
        retention_rank = {
            ContextCandidateRetention.REQUIRED.value: 3,
            ContextCandidateRetention.PREFERRED.value: 2,
            ContextCandidateRetention.OPTIONAL.value: 1,
        }
        trust_rank = {
            ContextCandidateTrust.AUTHORITATIVE.value: 5,
            ContextCandidateTrust.DIRECT.value: 4,
            ContextCandidateTrust.OBSERVED.value: 3,
            ContextCandidateTrust.RETRIEVED.value: 2,
            ContextCandidateTrust.DERIVED.value: 2,
            ContextCandidateTrust.UNVERIFIED.value: 1,
        }
        freshness_rank = {
            ContextCandidateFreshness.CURRENT.value: 4,
            ContextCandidateFreshness.HISTORICAL.value: 3,
            ContextCandidateFreshness.UNKNOWN.value: 2,
            ContextCandidateFreshness.STALE.value: 1,
        }
        return (
            retention_rank[str(getattr(candidate.retention, "value", candidate.retention))],
            trust_rank[str(getattr(candidate.trust, "value", candidate.trust))],
            freshness_rank[str(getattr(candidate.freshness, "value", candidate.freshness))],
            candidate.priority,
            -candidate.source_order,
            candidate.candidate_id,
        )

    @staticmethod
    def _render_candidates(candidates: list[ContextCandidate]) -> str:
        return "\n\n".join(candidate.content for candidate in candidates)

    @staticmethod
    def _source_order_key(candidate: ContextCandidate) -> tuple[int, str]:
        return candidate.source_order, candidate.candidate_id

    @staticmethod
    def _selection_order_key(candidate: ContextCandidate) -> tuple[int, int, int, str]:
        retention_rank = {
            ContextCandidateRetention.REQUIRED.value: 0,
            ContextCandidateRetention.PREFERRED.value: 1,
            ContextCandidateRetention.OPTIONAL.value: 2,
        }
        return (
            retention_rank[str(getattr(candidate.retention, "value", candidate.retention))],
            -candidate.priority,
            candidate.source_order,
            candidate.candidate_id,
        )

    def _with_candidate(
        self,
        selected: list[ContextCandidate],
        candidate: ContextCandidate,
    ) -> list[ContextCandidate]:
        combined = [*selected, candidate]
        combined.sort(key=self._source_order_key)
        return combined

    def _candidate_prompt_fits(
        self,
        prompt_text: str,
        *,
        max_prompt_chars: int,
        max_prompt_tokens: int | None,
    ) -> bool:
        return self._prompt_fits(
            prompt_text,
            max_prompt_chars=max_prompt_chars,
            max_prompt_tokens=max_prompt_tokens,
        )

    def _fit_typed_candidate(
        self,
        selected: list[ContextCandidate],
        candidate: ContextCandidate,
        *,
        renderer: Callable[[list[ContextCandidate]], str],
        max_prompt_chars: int,
        max_prompt_tokens: int | None,
    ) -> ContextCandidate | None:
        truncation = str(getattr(candidate.truncation, "value", candidate.truncation))
        if truncation == ContextCandidateTruncation.FORBIDDEN.value:
            return None

        best: ContextCandidate | None = None
        low = 1
        high = max(1, len(candidate.content) - 1)
        while low <= high:
            midpoint = (low + high) // 2
            projected_content = self._truncate_candidate_content(
                candidate.content,
                midpoint,
                truncation=truncation,
            )
            if not projected_content.strip():
                low = midpoint + 1
                continue
            projected = candidate.model_copy(update={"content": projected_content}, deep=True)
            if self._candidate_prompt_fits(
                renderer(self._with_candidate(selected, projected)),
                max_prompt_chars=max_prompt_chars,
                max_prompt_tokens=max_prompt_tokens,
            ):
                best = projected
                low = midpoint + 1
            else:
                high = midpoint - 1
        return best

    @staticmethod
    def _truncate_candidate_content(text: str, limit: int, *, truncation: str) -> str:
        if len(text) <= limit:
            return text
        if truncation == ContextCandidateTruncation.TAIL.value:
            if limit <= len(TAIL_TRUNCATION_MARKER):
                return TAIL_TRUNCATION_MARKER[:limit]
            return TAIL_TRUNCATION_MARKER + text[-(limit - len(TAIL_TRUNCATION_MARKER)) :]
        if limit <= len(TRUNCATION_MARKER):
            return TRUNCATION_MARKER[:limit]
        return text[: limit - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER

    def _select_payload(
        self,
        payload: dict[str, Any],
        *,
        max_prompt_chars: int,
        max_prompt_tokens: int | None,
    ) -> dict[str, Any]:
        selected = self._copy_payload(payload)
        selected[ContextSection.SYSTEM_PROMPT.value] = ""
        for section in self._LIST_SECTIONS:
            selected[section.value] = []

        system_prompt = str(payload.get(ContextSection.SYSTEM_PROMPT.value) or "")
        if system_prompt:
            selected[ContextSection.SYSTEM_PROMPT.value] = self._fit_system_prompt(
                selected,
                system_prompt,
                max_prompt_chars=max_prompt_chars,
                max_prompt_tokens=max_prompt_tokens,
            )

        dialog_key = ContextSection.DIALOG_CONTEXT.value
        for item in reversed(list(payload.get(dialog_key) or [])):
            candidate = [dict(item), *selected[dialog_key]]
            if self._fits(
                selected,
                dialog_key,
                candidate,
                max_prompt_chars,
                max_prompt_tokens,
            ):
                selected[dialog_key] = candidate
                continue
            if not selected[dialog_key]:
                fitted = self._fit_item(
                    selected,
                    section=dialog_key,
                    item=item,
                    text_field="content",
                    max_prompt_chars=max_prompt_chars,
                    max_prompt_tokens=max_prompt_tokens,
                    prepend=True,
                )
                if fitted is not None:
                    selected[dialog_key] = [fitted]
            break

        for section, text_field in self._FORWARD_SECTIONS:
            key = section.value
            for item in list(payload.get(key) or []):
                candidate = [*selected[key], dict(item)]
                if self._fits(
                    selected,
                    key,
                    candidate,
                    max_prompt_chars,
                    max_prompt_tokens,
                ):
                    selected[key] = candidate
                    continue
                fitted = self._fit_item(
                    selected,
                    section=key,
                    item=item,
                    text_field=text_field,
                    max_prompt_chars=max_prompt_chars,
                    max_prompt_tokens=max_prompt_tokens,
                    prepend=False,
                )
                if fitted is not None:
                    selected[key].append(fitted)
                break
        return selected

    def _count_tokens(self, text: str) -> int:
        if self.token_counter is None or not self.token_counter.available:
            raise RuntimeError("exact provider tokenizer is unavailable")
        return self.token_counter.count_text(text)

    def _prompt_fits(
        self,
        prompt_text: str,
        *,
        max_prompt_chars: int,
        max_prompt_tokens: int | None,
    ) -> bool:
        if len(prompt_text) > max_prompt_chars:
            return False
        return max_prompt_tokens is None or self._count_tokens(prompt_text) <= max_prompt_tokens

    def _fit_system_prompt(
        self,
        selected: dict[str, Any],
        system_prompt: str,
        *,
        max_prompt_chars: int,
        max_prompt_tokens: int | None,
    ) -> str:
        best = ""
        low = 1
        high = len(system_prompt)
        key = ContextSection.SYSTEM_PROMPT.value
        while low <= high:
            midpoint = (low + high) // 2
            candidate = self._truncate_text(system_prompt, midpoint)
            selected[key] = candidate
            if self._prompt_fits(
                self.renderer(selected),
                max_prompt_chars=max_prompt_chars,
                max_prompt_tokens=max_prompt_tokens,
            ):
                best = candidate
                low = midpoint + 1
            else:
                high = midpoint - 1
        selected[key] = ""
        return best

    def _fits(
        self,
        selected: dict[str, Any],
        section: str,
        candidate: list[dict[str, Any]],
        max_prompt_chars: int,
        max_prompt_tokens: int | None,
    ) -> bool:
        previous = selected[section]
        selected[section] = candidate
        try:
            return self._prompt_fits(
                self.renderer(selected),
                max_prompt_chars=max_prompt_chars,
                max_prompt_tokens=max_prompt_tokens,
            )
        finally:
            selected[section] = previous

    def _fit_item(
        self,
        selected: dict[str, Any],
        *,
        section: str,
        item: Mapping[str, Any],
        text_field: str,
        max_prompt_chars: int,
        max_prompt_tokens: int | None,
        prepend: bool,
    ) -> dict[str, Any] | None:
        text = str(item.get(text_field) or "")
        if not text:
            return None

        best: dict[str, Any] | None = None
        low = 1
        high = max(1, len(text) - 1)
        while low <= high:
            midpoint = (low + high) // 2
            candidate_item = dict(item)
            candidate_item[text_field] = self._truncate_text(text, midpoint)
            candidate = (
                [candidate_item, *selected[section]]
                if prepend
                else [*selected[section], candidate_item]
            )
            if self._fits(
                selected,
                section,
                candidate,
                max_prompt_chars,
                max_prompt_tokens,
            ):
                best = candidate_item
                low = midpoint + 1
            else:
                high = midpoint - 1
        return best

    def _selection_metadata(
        self,
        original: dict[str, Any],
        selected: dict[str, Any],
        *,
        max_prompt_chars: int,
        original_prompt_chars: int,
        final_prompt_chars: int,
        original_prompt_tokens: int | None,
        final_prompt_tokens: int | None,
        max_prompt_tokens: int | None,
    ) -> ContextSelectionMetadata:
        decisions = []
        for section in ContextSection:
            key = section.value
            original_items = self._section_item_count(original, section)
            selected_items = self._section_item_count(selected, section)
            original_chars = self._section_prompt_chars(original, section)
            selected_chars = self._section_prompt_chars(selected, section)
            if original_items == 0:
                action = "empty"
                reason = "empty"
            elif selected_items == original_items and selected_chars == original_chars:
                action = "kept"
                reason = "within_budget"
            elif selected_items == 0:
                action = "omitted"
                reason = "prompt_budget"
            else:
                action = "partially_kept"
                reason = "prompt_budget"
            decisions.append(
                {
                    "section": key,
                    "action": action,
                    "reason": reason,
                    "original_items": original_items,
                    "selected_items": selected_items,
                    "original_chars": original_chars,
                    "selected_chars": selected_chars,
                }
            )

        dialog_key = ContextSection.DIALOG_CONTEXT.value
        original_dialog = list(original.get(dialog_key) or [])
        selected_dialog = list(selected.get(dialog_key) or [])
        return ContextSelectionMetadata(
            budget_unit="tokens" if max_prompt_tokens is not None else "characters",
            max_prompt_chars=max_prompt_chars,
            max_prompt_tokens=max_prompt_tokens,
            original_prompt_chars=original_prompt_chars,
            final_prompt_chars=final_prompt_chars,
            original_prompt_tokens=original_prompt_tokens,
            final_prompt_tokens=final_prompt_tokens,
            token_count_method=(
                "provider_tokenizer" if max_prompt_tokens is not None else "unavailable"
            ),
            tokenizer_id=(
                self.token_counter.tokenizer_id
                if max_prompt_tokens is not None and self.token_counter is not None
                else ""
            ),
            model=(
                self.token_counter.model
                if max_prompt_tokens is not None and self.token_counter is not None
                else ""
            ),
            truncated=final_prompt_chars < original_prompt_chars,
            section_decisions=decisions,
            dialog_messages_total=len(original_dialog),
            dialog_messages_selected=len(selected_dialog),
            dialog_start_index=max(0, len(original_dialog) - len(selected_dialog)),
            oldest_selected_dialog_timestamp=(
                self._timestamp(selected_dialog[0]) if selected_dialog else None
            ),
            latest_dialog_timestamp=(
                self._timestamp(original_dialog[-1]) if original_dialog else None
            ),
        )

    def _section_item_count(
        self,
        payload: Mapping[str, Any],
        section: ContextSection,
    ) -> int:
        if section is ContextSection.SYSTEM_PROMPT:
            return int(bool(payload.get(section.value)))
        return len(payload.get(section.value) or [])

    def _section_prompt_chars(
        self,
        payload: Mapping[str, Any],
        section: ContextSection,
    ) -> int:
        isolated = self._copy_payload(payload)
        isolated[ContextSection.SYSTEM_PROMPT.value] = ""
        for list_section in self._LIST_SECTIONS:
            isolated[list_section.value] = []
        isolated[section.value] = payload.get(section.value) or (
            "" if section is ContextSection.SYSTEM_PROMPT else []
        )
        return len(self.renderer(isolated))

    def _copy_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        copied = dict(payload)
        for section in self._LIST_SECTIONS:
            copied[section.value] = [dict(item) for item in payload.get(section.value) or []]
        return copied

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        if limit <= 0:
            return ""
        if limit <= len(TRUNCATION_MARKER):
            return TRUNCATION_MARKER[:limit]
        return text[: limit - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER

    @staticmethod
    def _timestamp(item: Mapping[str, Any]) -> str | None:
        value = item.get("timestamp")
        return str(value) if value not in (None, "") else None
