from __future__ import annotations

from memory.rolling_compaction import (
    RollingSummaryAdapter,
    RollingSummaryAttemptEvidence,
    RollingSummaryFallbackReason,
    RollingSummaryRequest,
)
from metadata import ContextCompactionRecord


def _payload() -> dict[str, object]:
    return {
        "goal_delta": "diagnosis narrowed to the divide path",
        "verified_facts": ["test failure reproduced"],
        "decisions": ["keep the public API unchanged"],
        "open_issues": ["run the required validation command"],
        "evidence_ids": ["dialog-1", "dialog-2"],
        "next_action": "apply the smallest fix",
    }


def _request(
    *,
    payload: object | None = None,
    usage: dict[str, object] | None = None,
    usage_observed: bool = True,
    finish_reason: str | None = "stop",
    current_source_fingerprint: str | None = None,
    source_candidate_ids: tuple[str, ...] = ("dialog-1", "dialog-2"),
    max_summary_tokens: int = 80,
    original_chars: int = 1_000,
) -> RollingSummaryRequest:
    return RollingSummaryRequest(
        source_candidate_ids=source_candidate_ids,
        source_fingerprint="sha256:" + "a" * 64,
        previous_summary_fingerprint="sha256:" + "b" * 64,
        provider_payload=_payload() if payload is None else payload,
        attempt=RollingSummaryAttemptEvidence(
            usage=(
                {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60}
                if usage is None
                else usage
            ),
            usage_observed=usage_observed,
            finish_reason=finish_reason,
        ),
        max_summary_tokens=max_summary_tokens,
        original_chars=original_chars,
        current_source_fingerprint=current_source_fingerprint,
    )


def test_adapter_builds_source_linked_rolling_record_without_provider_calls() -> None:
    adapter = RollingSummaryAdapter(count_tokens=lambda text: len(text.split()))

    result = adapter.build(_request())

    assert result.record is not None
    assert result.fallback is None
    assert isinstance(result.record, ContextCompactionRecord)
    assert result.record.algorithm == "llm_rolling_summary_v1"
    assert result.record.source_candidate_ids == ["dialog-1", "dialog-2"]
    assert result.record.previous_summary_fingerprint == "sha256:" + "b" * 64
    assert result.record.summary_token_count is not None
    assert result.record.summary_token_count <= result.record.summary_token_limit


def test_request_freezes_source_candidate_ids_and_rejects_duplicates() -> None:
    request = _request()
    assert isinstance(request.source_candidate_ids, tuple)
    assert request.source_candidate_ids == ("dialog-1", "dialog-2")

    duplicate = _request(source_candidate_ids=("dialog-1", "dialog-1"))
    result = RollingSummaryAdapter(count_tokens=lambda text: len(text.split())).build(duplicate)
    assert result.record is None
    assert result.fallback is not None
    assert result.fallback.reason == RollingSummaryFallbackReason.INVALID_SOURCE


def test_no_eligible_source_is_a_typed_noop() -> None:
    request = _request(source_candidate_ids=())

    result = RollingSummaryAdapter(count_tokens=lambda text: len(text.split())).build(request)

    assert result.record is None
    assert result.fallback is not None
    assert result.fallback.reason == RollingSummaryFallbackReason.NO_ELIGIBLE_SOURCE


def test_unknown_usage_fails_closed() -> None:
    request = _request(usage=None, usage_observed=False)

    result = RollingSummaryAdapter(count_tokens=lambda text: len(text.split())).build(request)

    assert result.record is None
    assert result.fallback is not None
    assert result.fallback.reason == RollingSummaryFallbackReason.UNKNOWN_USAGE


def test_stale_source_falls_back_before_accepting_provider_payload() -> None:
    request = _request(current_source_fingerprint="sha256:" + "c" * 64)

    result = RollingSummaryAdapter(count_tokens=lambda text: len(text.split())).build(request)

    assert result.record is None
    assert result.fallback is not None
    assert result.fallback.reason == RollingSummaryFallbackReason.STALE_SOURCE


def test_invalid_and_overlong_payloads_are_typed_fallbacks() -> None:
    adapter = RollingSummaryAdapter(count_tokens=lambda text: len(text.split()))

    invalid = adapter.build(_request(payload={"evidence_ids": ["dialog-1"]}))
    assert invalid.record is None
    assert invalid.fallback is not None
    assert invalid.fallback.reason == RollingSummaryFallbackReason.INVALID_PAYLOAD

    overlong = adapter.build(
        _request(
            payload={**_payload(), "goal_delta": "word " * 20},
            max_summary_tokens=5,
        )
    )
    assert overlong.record is None
    assert overlong.fallback is not None
    assert overlong.fallback.reason == RollingSummaryFallbackReason.OVERLONG_SUMMARY


def test_truncated_finish_reason_is_not_accepted_as_a_complete_summary() -> None:
    request = _request(finish_reason="length")

    result = RollingSummaryAdapter(count_tokens=lambda text: len(text.split())).build(request)

    assert result.record is None
    assert result.fallback is not None
    assert result.fallback.reason == RollingSummaryFallbackReason.TRUNCATED_OUTPUT


def test_no_compression_gain_falls_back_to_deterministic_source_view() -> None:
    request = _request(original_chars=1)

    result = RollingSummaryAdapter(count_tokens=lambda text: len(text.split())).build(request)

    assert result.record is None
    assert result.fallback is not None
    assert result.fallback.reason == RollingSummaryFallbackReason.NO_COMPRESSION_GAIN
