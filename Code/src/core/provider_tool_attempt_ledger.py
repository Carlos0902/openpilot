"""Bounded runtime ledger for provider tool attempts."""

from __future__ import annotations

from core.provider_tool_roundtrip_contracts import (
    MAX_PROVIDER_TOOL_ATTEMPTS,
    ProviderToolAttempt,
)


class ProviderToolAttemptLedgerError(ValueError):
    """Raised when attempt history would become ambiguous or unbounded."""


class ProviderToolAttemptLedger:
    """Own first-signature and provider-call lineage for one round trip."""

    def __init__(self, *, max_attempts: int = MAX_PROVIDER_TOOL_ATTEMPTS) -> None:
        if (
            type(max_attempts) is not int
            or max_attempts < 1
            or max_attempts > MAX_PROVIDER_TOOL_ATTEMPTS
        ):
            raise ProviderToolAttemptLedgerError(
                "max_attempts must be a positive integer within the static limit"
            )
        self._max_attempts = max_attempts
        self._attempts: list[ProviderToolAttempt] = []
        self._first_by_signature: dict[str, ProviderToolAttempt] = {}
        self._provider_call_ids: set[str] = set()

    @property
    def attempts(self) -> tuple[ProviderToolAttempt, ...]:
        return tuple(self._attempts)

    @property
    def remaining(self) -> int:
        return self._max_attempts - len(self._attempts)

    def __len__(self) -> int:
        return len(self._attempts)

    def previous_for_signature(
        self,
        signature: str,
    ) -> ProviderToolAttempt | None:
        return self._first_by_signature.get(signature)

    def provider_call_seen(self, provider_call_id: str) -> bool:
        return provider_call_id in self._provider_call_ids

    def record(self, attempt: ProviderToolAttempt) -> None:
        if not isinstance(attempt, ProviderToolAttempt):
            raise ProviderToolAttemptLedgerError(
                "attempt must be ProviderToolAttempt"
            )
        if len(self._attempts) >= self._max_attempts:
            raise ProviderToolAttemptLedgerError(
                "provider tool attempt ledger is full"
            )
        if attempt.provider_call_id in self._provider_call_ids:
            raise ProviderToolAttemptLedgerError(
                "provider call IDs must be unique across one round trip"
            )

        previous = self._first_by_signature.get(attempt.signature)
        if attempt.duplicate_of is None:
            if previous is not None:
                raise ProviderToolAttemptLedgerError(
                    "repeated signatures require explicit duplicate lineage"
                )
        elif (
            previous is None
            or attempt.duplicate_of != previous.provider_call_id
        ):
            raise ProviderToolAttemptLedgerError(
                "duplicate attempts must reference the first matching signature"
            )

        self._attempts.append(attempt)
        self._provider_call_ids.add(attempt.provider_call_id)
        if previous is None:
            self._first_by_signature[attempt.signature] = attempt

    def record_duplicate(
        self,
        *,
        signature: str,
        tool_name: str,
        provider_call_id: str,
        round_index: int,
        error_type: str,
    ) -> ProviderToolAttempt:
        previous = self._first_by_signature.get(signature)
        if previous is None:
            raise ProviderToolAttemptLedgerError(
                "cannot record a duplicate without an earlier matching signature"
            )
        attempt = ProviderToolAttempt(
            signature=signature,
            tool_name=tool_name,
            provider_call_id=provider_call_id,
            round_index=round_index,
            success=False,
            error_type=error_type,
            duplicate_of=previous.provider_call_id,
        )
        self.record(attempt)
        return attempt


__all__ = [
    "ProviderToolAttemptLedger",
    "ProviderToolAttemptLedgerError",
]
