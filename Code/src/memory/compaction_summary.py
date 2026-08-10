"""Strict offline contract helpers for future LLM-assisted compaction.

This module deliberately does not call a Provider or write an artifact. Stage 1
only validates a bounded derived summary before a later stage wires it into the
existing atomic compaction path.
"""

from __future__ import annotations

import json
import hashlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from metadata import ContextCompactionSummary


class CompactionSummaryValidationError(ValueError):
    """A generated summary cannot be used as a derived context projection."""


_SUMMARY_FIELDS = frozenset(
    {
        "goal_delta",
        "verified_facts",
        "decisions",
        "open_issues",
        "evidence_ids",
        "next_action",
    }
)


def source_candidate_fingerprint(candidates: Sequence[Any]) -> str:
    """Return the stable digest shared by deterministic and LLM summaries."""

    source_payload = [
        {
            "candidate_id": str(getattr(candidate, "candidate_id", "")),
            "content": str(getattr(candidate, "content", "")),
        }
        for candidate in candidates
    ]
    encoded = json.dumps(
        source_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _field_value(candidate: Any, key: str, default: Any = None) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(key, default)
    return getattr(candidate, key, default)


def _normalized_value(value: Any) -> Any:
    return getattr(value, "value", value)


def source_candidate_binding_hash(candidates: Sequence[Any]) -> str:
    """Return a body-free digest for the exact source projection view.

    Unlike ``source_candidate_fingerprint``, this hash excludes source bodies and
    binds the candidate identity/ordering/control fields plus the content hash
    used by reusable-compaction admission.
    """

    source_payload = []
    for candidate in candidates:
        content = str(_field_value(candidate, "content", ""))
        source_payload.append(
            {
                "candidate_id": str(_field_value(candidate, "candidate_id", "")),
                "kind": _normalized_value(_field_value(candidate, "kind")),
                "source_id": _field_value(candidate, "source_id"),
                "role": _field_value(candidate, "role"),
                "retention": _normalized_value(_field_value(candidate, "retention")),
                "trust": _normalized_value(_field_value(candidate, "trust")),
                "freshness": _normalized_value(_field_value(candidate, "freshness")),
                "truncation": _normalized_value(_field_value(candidate, "truncation")),
                "source_order": _field_value(candidate, "source_order"),
                "content_sha256": "sha256:" + hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
            }
        )
    encoded = json.dumps(
        source_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_summary_payload(
    payload: Mapping[str, Any],
    *,
    source_candidate_ids: Sequence[str],
    max_summary_tokens: int,
    count_tokens: Callable[[str], int],
    usage_observed: bool = True,
) -> ContextCompactionSummary:
    """Validate one untrusted structured summary without granting authority."""

    if not isinstance(payload, Mapping):
        raise CompactionSummaryValidationError("summary payload must be an object")
    unknown = set(payload) - _SUMMARY_FIELDS
    if unknown:
        raise CompactionSummaryValidationError(
            f"summary contains unknown fields: {sorted(unknown)}"
        )
    if max_summary_tokens < 1:
        raise CompactionSummaryValidationError("summary budget must be positive")
    if not usage_observed:
        raise CompactionSummaryValidationError("summary usage is unknown")
    source_ids = list(source_candidate_ids)
    if len(source_ids) != len(set(source_ids)):
        raise CompactionSummaryValidationError("summary source candidate IDs must be unique")
    try:
        summary = ContextCompactionSummary.model_validate(dict(payload))
    except ValidationError as exc:
        message = str(exc)
        if "empty" in message:
            raise CompactionSummaryValidationError("summary is empty") from exc
        raise CompactionSummaryValidationError("summary schema is invalid") from exc
    if len(summary.evidence_ids) != len(set(summary.evidence_ids)):
        raise CompactionSummaryValidationError("summary evidence IDs must be unique")
    unknown_evidence = set(summary.evidence_ids) - set(source_ids)
    if unknown_evidence:
        raise CompactionSummaryValidationError(
            "summary evidence references unknown source candidates"
        )
    rendered = render_summary_payload(summary)
    token_count = count_tokens(rendered)
    if token_count < 1:
        raise CompactionSummaryValidationError("summary is empty")
    if token_count > max_summary_tokens:
        raise CompactionSummaryValidationError("summary exceeds its token budget")
    return summary


def render_summary_payload(summary: ContextCompactionSummary) -> str:
    """Render a stable, bounded representation for an artifact candidate."""

    return json.dumps(
        summary.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def calculate_summary_budget(
    *,
    static_cap_tokens: int,
    requested_prompt_tokens: int,
    used_prompt_tokens: int,
    required_reserve_tokens: int,
    recent_suffix_reserve_tokens: int,
    response_schema_reserve_tokens: int,
) -> int:
    """Return the bounded summary slot left after non-negotiable reserves."""

    values = {
        "static_cap_tokens": static_cap_tokens,
        "requested_prompt_tokens": requested_prompt_tokens,
        "used_prompt_tokens": used_prompt_tokens,
        "required_reserve_tokens": required_reserve_tokens,
        "recent_suffix_reserve_tokens": recent_suffix_reserve_tokens,
        "response_schema_reserve_tokens": response_schema_reserve_tokens,
    }
    if any(value < 0 for value in values.values()):
        raise ValueError("summary budget inputs cannot be negative")
    if static_cap_tokens == 0:
        raise ValueError("static summary cap must be positive")
    remaining = (
        requested_prompt_tokens
        - used_prompt_tokens
        - required_reserve_tokens
        - recent_suffix_reserve_tokens
        - response_schema_reserve_tokens
    )
    return min(static_cap_tokens, max(0, remaining))


__all__ = [
    "CompactionSummaryValidationError",
    "calculate_summary_budget",
    "render_summary_payload",
    "source_candidate_binding_hash",
    "source_candidate_fingerprint",
    "validate_summary_payload",
]
