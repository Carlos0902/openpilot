from __future__ import annotations

import pytest

from core.provider_tool_attempt_ledger import (
    ProviderToolAttemptLedger,
    ProviderToolAttemptLedgerError,
)
from core.provider_tool_roundtrip_contracts import ProviderToolAttempt


def _attempt(
    *,
    signature="a" * 64,
    provider_call_id="provider-1",
    success=True,
    error_type=None,
    duplicate_of=None,
):
    return ProviderToolAttempt(
        signature=signature,
        tool_name="file_reader",
        provider_call_id=provider_call_id,
        round_index=1,
        success=success,
        error_type=error_type,
        duplicate_of=duplicate_of,
    )


def test_ledger_records_first_attempt_and_signature_owner() -> None:
    ledger = ProviderToolAttemptLedger()
    attempt = _attempt()

    ledger.record(attempt)

    assert ledger.attempts == (attempt,)
    assert ledger.previous_for_signature(attempt.signature) == attempt
    assert ledger.provider_call_seen("provider-1") is True


def test_ledger_records_duplicate_against_first_signature_owner() -> None:
    ledger = ProviderToolAttemptLedger()
    first = _attempt()
    ledger.record(first)

    duplicate = ledger.record_duplicate(
        signature=first.signature,
        tool_name="file_reader",
        provider_call_id="provider-2",
        round_index=2,
        error_type="ProviderToolDuplicateAttempt",
    )

    assert duplicate.success is False
    assert duplicate.duplicate_of == "provider-1"
    assert ledger.previous_for_signature(first.signature) == first
    assert ledger.attempts == (first, duplicate)


def test_failed_first_attempt_still_owns_duplicate_identity() -> None:
    ledger = ProviderToolAttemptLedger()
    failed = _attempt(
        success=False,
        error_type="ProviderToolScopeViolation",
    )
    ledger.record(failed)

    duplicate = ledger.record_duplicate(
        signature=failed.signature,
        tool_name="file_reader",
        provider_call_id="provider-2",
        round_index=2,
        error_type="ProviderToolDuplicateAttempt",
    )

    assert duplicate.duplicate_of == failed.provider_call_id


def test_ledger_rejects_repeated_signature_without_duplicate_lineage() -> None:
    ledger = ProviderToolAttemptLedger()
    ledger.record(_attempt())

    with pytest.raises(ProviderToolAttemptLedgerError):
        ledger.record(_attempt(provider_call_id="provider-2"))


@pytest.mark.parametrize(
    "attempt",
    [
        _attempt(
            provider_call_id="provider-2",
            success=False,
            error_type="ProviderToolDuplicateAttempt",
            duplicate_of="missing-provider",
        ),
        _attempt(
            signature="b" * 64,
            provider_call_id="provider-2",
            success=False,
            error_type="ProviderToolDuplicateAttempt",
            duplicate_of="provider-1",
        ),
    ],
)
def test_ledger_rejects_invalid_duplicate_lineage(attempt) -> None:
    ledger = ProviderToolAttemptLedger()
    ledger.record(_attempt())

    with pytest.raises(ProviderToolAttemptLedgerError):
        ledger.record(attempt)


def test_ledger_rejects_reused_provider_call_id() -> None:
    ledger = ProviderToolAttemptLedger()
    ledger.record(_attempt())

    with pytest.raises(ProviderToolAttemptLedgerError):
        ledger.record(
            _attempt(
                signature="b" * 64,
                success=False,
                error_type="ProviderToolFailure",
            )
        )


def test_ledger_rejects_non_attempt_values() -> None:
    with pytest.raises(ProviderToolAttemptLedgerError):
        ProviderToolAttemptLedger().record({"success": True})


def test_ledger_accepts_exact_limit_and_rejects_overflow() -> None:
    ledger = ProviderToolAttemptLedger(max_attempts=2)
    ledger.record(_attempt())
    ledger.record(
        _attempt(
            signature="b" * 64,
            provider_call_id="provider-2",
        )
    )

    assert len(ledger) == 2
    with pytest.raises(ProviderToolAttemptLedgerError):
        ledger.record(
            _attempt(
                signature="c" * 64,
                provider_call_id="provider-3",
            )
        )


@pytest.mark.parametrize("max_attempts", [0, -1, 1.0, True, 1025])
def test_ledger_rejects_invalid_limits(max_attempts) -> None:
    with pytest.raises(ProviderToolAttemptLedgerError):
        ProviderToolAttemptLedger(max_attempts=max_attempts)
