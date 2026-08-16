"""Provider-neutral factory for the opt-in rolling summary projection."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from core.llm import LLMMessage
from memory.compaction_summary import source_candidate_fingerprint
from memory.context_assembly.request_builder import build_context_llm_request
from memory.rolling_compaction import RollingSummaryAttemptEvidence, RollingSummaryRequest
from metadata import ContextRequestPurpose, ReasoningMode, ReasoningPolicy
from utils.json_utils import safe_parse_json


_SUMMARY_SYSTEM_PROMPT = """You produce a derived context summary, never an authority record.
Return only a JSON object with exactly these fields:
goal_delta (string), verified_facts (array of strings), decisions (array of strings),
open_issues (array of strings), evidence_ids (array of source evidence IDs),
next_action (string).
Use only the supplied evidence. Do not invent files, permissions, commands, APIs,
user constraints, or validation results. evidence_ids must be copied exactly from
the supplied IDs. Keep every field concise and preserve uncertainty in open_issues."""


def _parsed_payload(response: Any) -> Mapping[str, Any] | None:
    parsed = getattr(response, "parsed_json", None)
    if isinstance(parsed, Mapping):
        return parsed
    raw = str(getattr(response, "content", "") or "").strip()
    if not raw:
        return None
    try:
        parsed = safe_parse_json(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def build_llm_rolling_summary_request_factory(
    llm_client: Any,
    *,
    context_max_prompt_tokens: int | None = None,
    timeout_seconds: float | None = None,
    transport_retries: int = 0,
) -> Callable[[Sequence[Any], int], RollingSummaryRequest | None]:
    """Build an opt-in request factory with no tools or authority fields.

    Provider errors intentionally propagate to the caller's existing
    deterministic fallback boundary.  No retry or alternative action is
    invented here.
    """

    def factory(
        candidates: Sequence[Any],
        max_summary_tokens: int,
    ) -> RollingSummaryRequest | None:
        if max_summary_tokens < 1:
            return None
        source_ids = tuple(str(getattr(candidate, "candidate_id", "")) for candidate in candidates)
        source_fingerprint = source_candidate_fingerprint(candidates)
        source_payload = [
            {
                "evidence_id": source_id,
                "content": str(getattr(candidate, "content", "")),
            }
            for source_id, candidate in zip(source_ids, candidates, strict=True)
        ]
        user_payload = json.dumps(
            {"source_evidence": source_payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        request = build_context_llm_request(
            llm_client,
            messages=[
                LLMMessage(role="system", content=_SUMMARY_SYSTEM_PROMPT),
                LLMMessage(role="user", content=user_payload),
            ],
            purpose=ContextRequestPurpose.MEMORY_COMPRESSION,
            context_max_prompt_tokens=context_max_prompt_tokens,
            response_format="json_object",
            temperature=0.0,
            max_tokens=max_summary_tokens,
            timeout_seconds=timeout_seconds,
            transport_retries=transport_retries,
            trace_info={
                "rolling_summary": True,
                "source_fingerprint": source_fingerprint,
                "source_candidate_ids": list(source_ids),
            },
            reasoning_policy=ReasoningPolicy(mode=ReasoningMode.DISABLED),
        )
        # A summary attempt is one bounded provider decision.  Do not let the
        # generic JSON-repair loop silently turn a capped/invalid response into
        # multiple calls; the adapter's deterministic fallback is the recovery
        # boundary for this derived view.
        response = llm_client.complete(request, max_retries=1)
        usage = dict(getattr(response, "usage", {}) or {})
        usage_observed = all(
            key in usage and isinstance(usage.get(key), int)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        return RollingSummaryRequest(
            source_candidate_ids=source_ids,
            source_fingerprint=source_fingerprint,
            provider_payload=dict(_parsed_payload(response) or {}),
            attempt=RollingSummaryAttemptEvidence(
                usage=usage,
                usage_observed=usage_observed,
                finish_reason=getattr(response, "finish_reason", None),
            ),
            max_summary_tokens=max_summary_tokens,
            original_chars=sum(len(str(getattr(candidate, "content", ""))) for candidate in candidates),
        )

    return factory


__all__ = ["build_llm_rolling_summary_request_factory"]
