"""Injectable, provider-free validation for rolling context summaries.

The adapter is deliberately a narrow boundary.  It does not call a provider,
persist an artifact, or decide which candidates are authoritative.  A caller
supplies an immutable source snapshot and the already structured provider
attempt; the adapter validates the derived payload and returns either a
source-linked :class:`ContextCompactionRecord` or a typed deterministic
fallback.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from metadata import ContextCompactionRecord

from .compaction_summary import (
    CompactionSummaryValidationError,
    render_summary_payload,
    validate_summary_payload,
)


_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMPLETED_FINISH_REASONS = frozenset(
    {"stop", "end_turn", "complete", "completed", "eos", "success"}
)


class RollingSummaryFallbackReason(str, Enum):
    """Why the deterministic source view must remain in use."""

    NO_ELIGIBLE_SOURCE = "no_eligible_source"
    INVALID_SOURCE = "invalid_source"
    STALE_SOURCE = "stale_source"
    UNKNOWN_USAGE = "unknown_usage"
    UNKNOWN_FINISH_REASON = "unknown_finish_reason"
    TRUNCATED_OUTPUT = "truncated_output"
    INVALID_PAYLOAD = "invalid_payload"
    OVERLONG_SUMMARY = "overlong_summary"
    NO_COMPRESSION_GAIN = "no_compression_gain"


@dataclass(frozen=True)
class RollingSummaryAttemptEvidence:
    """Provider-attempt evidence required before accepting a summary.

    ``usage_observed`` is explicit because an empty/missing usage object is not
    evidence of zero cost.  The adapter only consumes this evidence; recording
    it in run diagnostics remains the caller's responsibility.
    """

    usage: Mapping[str, Any] | None
    usage_observed: bool
    finish_reason: str | None


def provider_attempt_evidence_complete(
    attempt: RollingSummaryAttemptEvidence | None,
) -> bool:
    """Return whether a provider attempt has trustworthy completion evidence."""

    if attempt is None or not attempt.usage_observed or not attempt.usage:
        return False
    usage = attempt.usage
    if not isinstance(usage, Mapping):
        return False
    if any(
        not isinstance(usage.get(key), int)
        or isinstance(usage.get(key), bool)
        or usage.get(key) < 0
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    ):
        return False
    finish_reason = str(attempt.finish_reason or "").strip().lower()
    return finish_reason in _COMPLETED_FINISH_REASONS


@dataclass(frozen=True)
class RollingSummaryRequest:
    """Immutable input snapshot for one rolling-summary validation pass."""

    source_candidate_ids: Sequence[str] = field(default_factory=tuple)
    source_fingerprint: str = ""
    previous_summary_fingerprint: str | None = None
    provider_payload: Mapping[str, Any] | Any = None
    attempt: RollingSummaryAttemptEvidence | None = None
    max_summary_tokens: int = 0
    original_chars: int = 0
    # If supplied, this is the fingerprint observed immediately before
    # assembly.  A mismatch means the immutable provider input is stale.
    current_source_fingerprint: str | None = None

    def __post_init__(self) -> None:
        # Convert caller-owned lists to a tuple at the boundary.  This keeps
        # source identity stable even if the caller mutates its input list
        # after constructing the request.
        try:
            source_ids = tuple(self.source_candidate_ids)
        except TypeError:
            source_ids = ()
        object.__setattr__(self, "source_candidate_ids", source_ids)


@dataclass(frozen=True)
class RollingSummaryFallback:
    """Typed instruction to keep the deterministic source view."""

    reason: RollingSummaryFallbackReason
    source_candidate_ids: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class RollingSummaryResult:
    """Either one validated record or one typed source-view fallback."""

    record: ContextCompactionRecord | None = None
    fallback: RollingSummaryFallback | None = None

    @property
    def accepted(self) -> bool:
        """Whether the derived replacement passed every local gate."""

        return self.record is not None and self.fallback is None


class RollingSummaryAdapter:
    """Validate an injected structured summary without invoking a Provider."""

    def __init__(self, *, count_tokens: Callable[[str], int]) -> None:
        self._count_tokens = count_tokens

    def __call__(self, request: RollingSummaryRequest) -> RollingSummaryResult:
        return self.build(request)

    def build(self, request: RollingSummaryRequest) -> RollingSummaryResult:
        """Build a source-linked rolling record, or return a typed fallback."""

        source_ids = tuple(request.source_candidate_ids)

        if not source_ids:
            return self._fallback(
                RollingSummaryFallbackReason.NO_ELIGIBLE_SOURCE,
                source_ids,
                "no eligible old source candidates were provided",
            )
        if not self._valid_source_ids(source_ids):
            return self._fallback(
                RollingSummaryFallbackReason.INVALID_SOURCE,
                source_ids,
                "source candidate IDs must be non-empty, unique strings",
            )
        if not _FINGERPRINT_RE.fullmatch(request.source_fingerprint):
            return self._fallback(
                RollingSummaryFallbackReason.INVALID_SOURCE,
                source_ids,
                "source fingerprint is missing or invalid",
            )
        if request.previous_summary_fingerprint is not None and not _FINGERPRINT_RE.fullmatch(
            request.previous_summary_fingerprint
        ):
            return self._fallback(
                RollingSummaryFallbackReason.INVALID_SOURCE,
                source_ids,
                "previous summary fingerprint is invalid",
            )
        if (
            request.current_source_fingerprint is not None
            and request.current_source_fingerprint != request.source_fingerprint
        ):
            return self._fallback(
                RollingSummaryFallbackReason.STALE_SOURCE,
                source_ids,
                "source fingerprint changed after the provider input was captured",
            )
        if request.max_summary_tokens < 1 or request.original_chars < 1:
            return self._fallback(
                RollingSummaryFallbackReason.INVALID_SOURCE,
                source_ids,
                "summary budget and source size must be positive",
            )

        attempt = request.attempt
        if attempt is None or not attempt.usage_observed or not attempt.usage:
            return self._fallback(
                RollingSummaryFallbackReason.UNKNOWN_USAGE,
                source_ids,
                "provider usage was not observed; zero cannot be assumed",
            )
        if not isinstance(attempt.usage, Mapping):
            return self._fallback(
                RollingSummaryFallbackReason.UNKNOWN_USAGE,
                source_ids,
                "provider usage was not a structured object",
            )
        if any(
            not isinstance(attempt.usage.get(key), int)
            or isinstance(attempt.usage.get(key), bool)
            or attempt.usage.get(key) < 0
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        ):
            return self._fallback(
                RollingSummaryFallbackReason.UNKNOWN_USAGE,
                source_ids,
                "provider usage was incomplete or invalid",
            )
        finish_reason = (attempt.finish_reason or "").strip().lower()
        if not finish_reason:
            return self._fallback(
                RollingSummaryFallbackReason.UNKNOWN_FINISH_REASON,
                source_ids,
                "provider finish reason was not observed",
            )
        if finish_reason == "length":
            return self._fallback(
                RollingSummaryFallbackReason.TRUNCATED_OUTPUT,
                source_ids,
                "provider output was capped before a complete summary was returned",
            )
        if finish_reason not in _COMPLETED_FINISH_REASONS:
            return self._fallback(
                RollingSummaryFallbackReason.UNKNOWN_FINISH_REASON,
                source_ids,
                f"unsupported provider finish reason: {finish_reason}",
            )
        if not isinstance(request.provider_payload, Mapping):
            return self._fallback(
                RollingSummaryFallbackReason.INVALID_PAYLOAD,
                source_ids,
                "provider payload must be a structured object",
            )

        try:
            summary_payload = validate_summary_payload(
                dict(request.provider_payload),
                source_candidate_ids=source_ids,
                max_summary_tokens=request.max_summary_tokens,
                count_tokens=self._count_tokens,
                usage_observed=True,
            )
        except CompactionSummaryValidationError as exc:
            message = str(exc)
            reason = (
                RollingSummaryFallbackReason.OVERLONG_SUMMARY
                if "budget" in message
                else RollingSummaryFallbackReason.INVALID_PAYLOAD
            )
            return self._fallback(reason, source_ids, message)
        except Exception as exc:  # provider output and tokenizers are untrusted
            return self._fallback(
                RollingSummaryFallbackReason.INVALID_PAYLOAD,
                source_ids,
                f"summary validation failed: {type(exc).__name__}",
            )

        summary = render_summary_payload(summary_payload)
        try:
            summary_token_count = self._count_tokens(summary)
        except Exception as exc:
            return self._fallback(
                RollingSummaryFallbackReason.INVALID_PAYLOAD,
                source_ids,
                f"summary token counting failed: {type(exc).__name__}",
            )
        if not isinstance(summary_token_count, int) or isinstance(summary_token_count, bool):
            return self._fallback(
                RollingSummaryFallbackReason.INVALID_PAYLOAD,
                source_ids,
                "summary token count must be an integer",
            )
        if summary_token_count < 1 or summary_token_count > request.max_summary_tokens:
            reason = (
                RollingSummaryFallbackReason.OVERLONG_SUMMARY
                if summary_token_count > request.max_summary_tokens
                else RollingSummaryFallbackReason.INVALID_PAYLOAD
            )
            return self._fallback(reason, source_ids, "summary token evidence is invalid")
        if len(summary) >= request.original_chars:
            return self._fallback(
                RollingSummaryFallbackReason.NO_COMPRESSION_GAIN,
                source_ids,
                "validated summary is not smaller than its source segment",
            )

        record = ContextCompactionRecord(
            compaction_id=self._compaction_id(
                request.source_fingerprint,
                request.previous_summary_fingerprint,
            ),
            source_fingerprint=request.source_fingerprint,
            source_candidate_ids=list(source_ids),
            algorithm="llm_rolling_summary_v1",
            summary=summary,
            original_chars=request.original_chars,
            compacted_chars=len(summary),
            summary_payload=summary_payload,
            summary_token_limit=request.max_summary_tokens,
            summary_token_count=summary_token_count,
            previous_summary_fingerprint=request.previous_summary_fingerprint,
        )
        return RollingSummaryResult(record=record)

    @staticmethod
    def _valid_source_ids(source_ids: tuple[str, ...]) -> bool:
        valid_values = all(
            isinstance(source_id, str) and bool(source_id.strip())
            for source_id in source_ids
        )
        return valid_values and len(source_ids) == len(set(source_ids))

    @staticmethod
    def _compaction_id(source_fingerprint: str, previous_fingerprint: str | None) -> str:
        seed = json.dumps(
            {
                "algorithm": "llm_rolling_summary_v1",
                "source_fingerprint": source_fingerprint,
                "previous_summary_fingerprint": previous_fingerprint,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "rolling-" + hashlib.sha256(seed).hexdigest()[:24]

    @staticmethod
    def _fallback(
        reason: RollingSummaryFallbackReason,
        source_ids: tuple[str, ...],
        message: str,
    ) -> RollingSummaryResult:
        return RollingSummaryResult(
            fallback=RollingSummaryFallback(
                reason=reason,
                source_candidate_ids=source_ids,
                message=message,
            )
        )


def build_rolling_summary_record(
    request: RollingSummaryRequest,
    *,
    count_tokens: Callable[[str], int],
) -> RollingSummaryResult:
    """Convenience wrapper for dependency injection at a call site."""

    return RollingSummaryAdapter(count_tokens=count_tokens).build(request)


__all__ = [
    "RollingSummaryAdapter",
    "RollingSummaryAttemptEvidence",
    "RollingSummaryFallback",
    "RollingSummaryFallbackReason",
    "RollingSummaryRequest",
    "RollingSummaryResult",
    "build_rolling_summary_record",
    "provider_attempt_evidence_complete",
]
