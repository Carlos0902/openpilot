from __future__ import annotations

import pytest

from stage7c_provider_attempt_telemetry import (
    AttemptStatus,
    DownstreamValidation,
    ProviderAttemptTelemetryStop,
    RecoveryAction,
    build_attempt_receipt,
    receipt_from_diagnostic_events,
    replay_receipt,
    require_settleable_usage,
)


REQUEST_HASH = "v2:sha256:" + "a" * 64
CONTEXT_HASH = "sha256:" + "b" * 64
TURN_HASH = "sha256:" + "c" * 64


def test_complete_response_retains_usage_reasoning_finish_and_identity() -> None:
    receipt = build_attempt_receipt(
        {
            "status": "responded",
            "transport_attempted": True,
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
                "completion_tokens_details": {"reasoning_tokens": 30},
            },
            "finish_reason": "stop",
            "content": '{"task": {}}',
            "downstream_validation": "passed",
        },
        attempt_id="attempt-1",
        execution_id="execution-1",
        request_ordinal=1,
        request_hash=REQUEST_HASH,
        provider="demo-provider",
        model="demo-model",
        endpoint="https://api.example.test:443/v1",
        request_context_hash=CONTEXT_HASH,
        session_turn_source_hash=TURN_HASH,
    )

    assert receipt.status == AttemptStatus.RESPONDED
    assert receipt.transport_attempted is True
    assert receipt.endpoint == "https://api.example.test/v1"
    assert receipt.usage.usage_observed is True
    assert receipt.usage.input_tokens == 100
    assert receipt.usage.output_tokens == 40
    assert receipt.usage.total_tokens == 140
    assert receipt.reasoning_tokens == 30
    assert receipt.finish_reason == "stop"
    assert receipt.downstream_validation == DownstreamValidation.PASSED
    assert receipt.response_hash is not None
    assert require_settleable_usage(receipt).total_tokens == 140


def test_length_failure_preserves_partial_response_and_recovery_metadata() -> None:
    receipt = build_attempt_receipt(
        {
            "status": "failed",
            "usage": {
                "input_tokens": 80,
                "output_tokens": 40,
                "total_tokens": 120,
                "completion_tokens_details": {"reasoning_tokens": 35},
            },
            "finish_reason": "length",
            "response_text": '{"task": [',
            "json_repair_attempts": 1,
            "transport_retry_count": 0,
            "error_type": "InvalidLLMResponseError",
            "recovery_action": "json_repair",
        },
        attempt_id="attempt-length",
        execution_id="execution-length",
        request_ordinal=2,
        request_hash=REQUEST_HASH,
    )

    assert receipt.status == AttemptStatus.FAILED
    assert receipt.finish_reason_observed is True
    assert receipt.finish_reason == "length"
    assert receipt.json_repair_attempts == 1
    assert receipt.error_type == "InvalidLLMResponseError"
    assert receipt.response_hash is not None
    assert require_settleable_usage(receipt).output_tokens == 40


def test_partial_or_unknown_usage_is_never_zero_filled_and_stops_reconciliation() -> None:
    receipt = build_attempt_receipt(
        {
            "status": "failed",
            "usage": {"completion_tokens": 12},
            "finish_reason": "timeout",
            "error_type": "TimeoutError",
        },
        attempt_id="attempt-unknown",
        execution_id="execution-unknown",
        request_ordinal=3,
        request_hash=REQUEST_HASH,
    )

    assert receipt.usage.usage_observed is False
    assert receipt.usage.input_tokens is None
    assert receipt.usage.output_tokens is None
    assert receipt.usage.total_tokens is None
    assert receipt.usage.raw_usage == {"completion_tokens": 12}
    with pytest.raises(ProviderAttemptTelemetryStop, match="unknown provider usage"):
        require_settleable_usage(receipt)


def test_pretransport_block_has_no_provider_usage_and_replay_has_no_transport() -> None:
    blocked = build_attempt_receipt(
        {
            "status": "pretransport_blocked",
            "transport_attempted": False,
            "recovery_action": "stop",
        },
        attempt_id="attempt-blocked",
        execution_id="execution-blocked",
        request_ordinal=4,
        request_hash=REQUEST_HASH,
    )
    assert blocked.status == AttemptStatus.PRETRANSPORT_BLOCKED
    assert blocked.transport_attempted is False
    assert blocked.usage.usage_observed is False

    original = build_attempt_receipt(
        {
            "status": "responded",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            "finish_reason": "stop",
            "content": "ok",
        },
        attempt_id="attempt-original",
        execution_id="execution-original",
        request_ordinal=1,
        request_hash=REQUEST_HASH,
    )
    replayed = replay_receipt(
        original,
        attempt_id="attempt-replay",
        execution_id="execution-replay",
        request_ordinal=2,
    )
    assert replayed.status == AttemptStatus.REPLAYED
    assert replayed.transport_attempted is False
    assert replayed.request_hash == original.request_hash
    assert replayed.response_hash == original.response_hash
    assert replayed.recovery_action == RecoveryAction.REPLAYED
    assert replayed.recovery_source_attempt_id == original.attempt_id


def test_usage_mismatch_and_reasoning_over_output_fail_closed() -> None:
    with pytest.raises(ProviderAttemptTelemetryStop, match="total mismatch"):
        build_attempt_receipt(
            {
                "status": "responded",
                "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 99},
            },
            attempt_id="attempt-mismatch",
            execution_id="execution-mismatch",
            request_ordinal=5,
            request_hash=REQUEST_HASH,
        )

    with pytest.raises(ValueError, match="reasoning tokens"):
        build_attempt_receipt(
            {
                "status": "responded",
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 2,
                    "total_tokens": 7,
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            },
            attempt_id="attempt-reasoning",
            execution_id="execution-reasoning",
            request_ordinal=6,
            request_hash=REQUEST_HASH,
        )


def test_response_without_finish_reason_is_explicitly_unobserved() -> None:
    receipt = build_attempt_receipt(
        {
            "status": "responded",
            "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        },
        attempt_id="attempt-no-finish",
        execution_id="execution-no-finish",
        request_ordinal=7,
        request_hash=REQUEST_HASH,
    )
    assert receipt.finish_reason is None
    assert receipt.finish_reason_observed is False


def test_diagnostic_event_pair_projects_the_proxy_request_hash_and_failure_usage() -> None:
    request = {
        "event_type": "llm_requested",
        "payload": {
            "correlation": {"execution_id": "llm_1"},
            "trace_info": {
                "request_hash": REQUEST_HASH,
                "request_ordinal": 4,
            },
        },
    }
    failed = {
        "event_type": "llm_failed",
        "payload": {
            "correlation": {"execution_id": "llm_1"},
            "failure": {
                "error_type": "InvalidLLMResponseError",
                "recoverable": True,
                "retry_recommended": True,
                "details": {
                    "provider_attempt": {
                        "request_hash": REQUEST_HASH,
                        "usage": {
                            "prompt_tokens": 20,
                            "completion_tokens": 8,
                            "total_tokens": 28,
                            "completion_tokens_details": {"reasoning_tokens": 6},
                        },
                        "finish_reason": "length",
                        "transport_attempted": True,
                        "provider": "demo",
                        "model": "demo-model",
                    }
                },
            },
        },
    }

    receipt = receipt_from_diagnostic_events(
        request,
        failed,
        request_context_hash=CONTEXT_HASH,
        session_turn_source_hash=TURN_HASH,
    )

    assert receipt.status == AttemptStatus.FAILED
    assert receipt.attempt_id == "llm_1"
    assert receipt.request_ordinal == 4
    assert receipt.request_hash == REQUEST_HASH
    assert receipt.reasoning_tokens == 6
    assert receipt.finish_reason == "length"
    assert receipt.request_context_hash == CONTEXT_HASH
    assert receipt.session_turn_source_hash == TURN_HASH
