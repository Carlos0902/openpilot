"""Typed, offline projection of provider-attempt diagnostics.

The production diagnostics already persist raw usage, finish reasons and
provider details.  This experiment-only projection gives the next canary a
strict receipt without changing runtime authority or pretending unknown usage
is zero.  It deliberately contains no response body and no credentials.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.llm import normalized_provider_endpoint


class ProviderAttemptTelemetryStop(RuntimeError):
    """A receipt could not prove complete, safe attempt accounting."""


class AttemptStatus(str, Enum):
    RESPONDED = "responded"
    FAILED = "failed"
    REPLAYED = "replayed"
    PRETRANSPORT_BLOCKED = "pretransport_blocked"


class RecoveryAction(str, Enum):
    NONE = "none"
    TRANSPORT_RETRY = "transport_retry"
    JSON_REPAIR = "json_repair"
    FALLBACK = "fallback"
    REPLAYED = "replayed"
    STOP = "stop"


class DownstreamValidation(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    PASSED = "passed"
    FAILED = "failed"


class AttemptUsage(BaseModel):
    """Complete usage or an explicit unknown/partial observation."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    usage_observed: bool = False
    raw_usage: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _usage_is_complete_or_unknown(self) -> "AttemptUsage":
        complete = all(
            value is not None
            for value in (self.input_tokens, self.output_tokens, self.total_tokens)
        )
        if self.usage_observed != complete:
            raise ValueError("usage_observed must match complete input/output/total usage")
        if complete and self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("provider usage total must reconcile with input and output")
        return self


class ProviderAttemptReceipt(BaseModel):
    """One durable, provider-neutral attempt receipt for offline canary analysis."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    request_ordinal: int = Field(ge=1)
    request_hash: str = Field(pattern=r"^(?:v2:)?sha256:[0-9a-f]{64}$")
    status: AttemptStatus
    transport_attempted: bool
    provider: str = ""
    model: str = ""
    endpoint: str = ""
    usage: AttemptUsage = Field(default_factory=AttemptUsage)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    reasoning_usage_observed: bool = False
    finish_reason: str | None = None
    finish_reason_observed: bool = False
    json_repair_attempts: int = Field(default=0, ge=0)
    transport_retry_count: int = Field(default=0, ge=0)
    error_type: str | None = None
    error_category: str | None = None
    recoverable: bool | None = None
    retry_recommended: bool | None = None
    response_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    request_context_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    session_turn_source_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    recovery_action: RecoveryAction = RecoveryAction.NONE
    recovery_source_attempt_id: str | None = None
    downstream_validation: DownstreamValidation = DownstreamValidation.NOT_EVALUATED

    @model_validator(mode="after")
    def _receipt_invariants(self) -> "ProviderAttemptReceipt":
        if self.reasoning_usage_observed != (self.reasoning_tokens is not None):
            raise ValueError("reasoning usage observation must match reasoning token presence")
        if (
            self.reasoning_tokens is not None
            and self.usage.output_tokens is not None
            and self.reasoning_tokens > self.usage.output_tokens
        ):
            raise ValueError("reasoning tokens cannot exceed output tokens")
        if self.finish_reason_observed != (self.finish_reason is not None):
            raise ValueError("finish reason observation must match finish reason presence")
        if self.status in {AttemptStatus.REPLAYED, AttemptStatus.PRETRANSPORT_BLOCKED}:
            if self.transport_attempted:
                raise ValueError("replayed/pretransport attempt cannot claim transport")
        if self.status == AttemptStatus.REPLAYED:
            if self.recovery_action != RecoveryAction.REPLAYED or not self.recovery_source_attempt_id:
                raise ValueError("replayed attempt must link its source receipt")
        if self.status == AttemptStatus.PRETRANSPORT_BLOCKED:
            if self.usage.usage_observed:
                raise ValueError("pretransport block cannot claim provider usage")
        if self.recovery_source_attempt_id == self.attempt_id:
            raise ValueError("recovery receipt cannot point to itself")
        return self


def _hash_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _optional_int(usage: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ProviderAttemptTelemetryStop(f"usage field {key} is not an integer") from exc
    return None


def build_attempt_receipt(
    raw: Mapping[str, Any],
    *,
    attempt_id: str,
    execution_id: str,
    request_ordinal: int,
    request_hash: str,
    provider: str = "",
    model: str = "",
    endpoint: str = "",
    request_context_hash: str | None = None,
    session_turn_source_hash: str | None = None,
) -> ProviderAttemptReceipt:
    """Normalize one success/failure diagnostic payload without zero-filling unknowns."""

    raw_usage = raw.get("usage")
    usage_mapping = dict(raw_usage) if isinstance(raw_usage, Mapping) else {}
    input_tokens = _optional_int(usage_mapping, "input_tokens", "prompt_tokens")
    output_tokens = _optional_int(usage_mapping, "output_tokens", "completion_tokens")
    total_tokens = _optional_int(usage_mapping, "total_tokens")
    complete = all(value is not None for value in (input_tokens, output_tokens, total_tokens))
    if complete and total_tokens != input_tokens + output_tokens:
        raise ProviderAttemptTelemetryStop("provider usage total mismatch")

    reasoning_tokens = None
    details = usage_mapping.get("completion_tokens_details")
    if isinstance(details, Mapping) and details.get("reasoning_tokens") is not None:
        reasoning_tokens = _optional_int(details, "reasoning_tokens")

    content = str(raw.get("content") or raw.get("response_text") or "")
    raw_status = raw.get("status")
    status_value = (
        raw_status.value
        if isinstance(raw_status, AttemptStatus)
        else str(raw_status or AttemptStatus.RESPONDED.value)
    )
    status = AttemptStatus(status_value)
    finish_reason = raw.get("finish_reason")
    normalized_endpoint = normalized_provider_endpoint(endpoint)
    return ProviderAttemptReceipt(
        attempt_id=attempt_id,
        execution_id=execution_id,
        request_ordinal=request_ordinal,
        request_hash=request_hash,
        status=status,
        transport_attempted=bool(
            raw.get(
                "transport_attempted",
                status
                not in {
                    AttemptStatus.REPLAYED,
                    AttemptStatus.PRETRANSPORT_BLOCKED,
                },
            )
        ),
        provider=str(raw.get("provider") or provider),
        model=str(raw.get("model") or model),
        endpoint=normalized_endpoint,
        usage=AttemptUsage(
            input_tokens=input_tokens if complete else None,
            output_tokens=output_tokens if complete else None,
            total_tokens=total_tokens if complete else None,
            usage_observed=complete,
            raw_usage=usage_mapping,
        ),
        reasoning_tokens=reasoning_tokens,
        reasoning_usage_observed=reasoning_tokens is not None,
        finish_reason=(str(finish_reason) if finish_reason is not None else None),
        finish_reason_observed=finish_reason is not None,
        json_repair_attempts=int(raw.get("json_repair_attempts") or 0),
        transport_retry_count=int(raw.get("transport_retry_count") or 0),
        error_type=str(raw.get("error_type")) if raw.get("error_type") else None,
        error_category=str(raw.get("error_category")) if raw.get("error_category") else None,
        recoverable=raw.get("recoverable"),
        retry_recommended=raw.get("retry_recommended"),
        response_hash=_hash_text(content) if content else None,
        request_context_hash=request_context_hash,
        session_turn_source_hash=session_turn_source_hash,
        recovery_action=RecoveryAction(str(raw.get("recovery_action") or RecoveryAction.NONE.value)),
        recovery_source_attempt_id=(
            str(raw.get("recovery_source_attempt_id"))
            if raw.get("recovery_source_attempt_id")
            else None
        ),
        downstream_validation=DownstreamValidation(
            str(raw.get("downstream_validation") or DownstreamValidation.NOT_EVALUATED.value)
        ),
    )


def replay_receipt(
    source: ProviderAttemptReceipt,
    *,
    attempt_id: str,
    execution_id: str,
    request_ordinal: int,
) -> ProviderAttemptReceipt:
    """Create a no-transport replay receipt linked to the original attempt."""

    return source.model_copy(
        update={
            "attempt_id": attempt_id,
            "execution_id": execution_id,
            "request_ordinal": request_ordinal,
            "status": AttemptStatus.REPLAYED,
            "transport_attempted": False,
            "recovery_action": RecoveryAction.REPLAYED,
            "recovery_source_attempt_id": source.attempt_id,
        }
    )


def receipt_from_diagnostic_events(
    request_event: Mapping[str, Any],
    terminal_event: Mapping[str, Any],
    *,
    request_context_hash: str | None = None,
    session_turn_source_hash: str | None = None,
) -> ProviderAttemptReceipt:
    """Project one persisted ``llm_requested`` + terminal event pair."""

    request_payload = request_event.get("payload") or {}
    request_trace = request_payload.get("trace_info") or {}
    request_hash = str(request_trace.get("request_hash") or "")
    if not request_hash:
        metadata = request_payload.get("request_metadata") or {}
        request_hash = str((metadata.get("trace_info") or {}).get("request_hash") or "")
    if not request_hash:
        raise ProviderAttemptTelemetryStop("diagnostic request has no provider-bound request hash")
    request_correlation = request_payload.get("correlation") or {}
    terminal_payload = terminal_event.get("payload") or {}
    terminal_correlation = terminal_payload.get("correlation") or {}
    attempt_id = str(
        terminal_payload.get("call_id")
        or terminal_correlation.get("execution_id")
        or request_correlation.get("execution_id")
        or ""
    )
    if not attempt_id:
        raise ProviderAttemptTelemetryStop("diagnostic terminal event has no attempt ID")

    event_type = str(terminal_event.get("event_type") or "")
    if event_type == "llm_responded":
        metadata = terminal_payload.get("response_metadata") or terminal_payload
        details = metadata.get("provider_details") or {}
        raw = {
            "status": "responded",
            "transport_attempted": True,
            "usage": metadata.get("usage") or {},
            "finish_reason": metadata.get("finish_reason"),
            "provider": metadata.get("provider"),
            "model": metadata.get("model"),
            "endpoint": details.get("provider_endpoint"),
            "downstream_validation": "not_evaluated",
        }
    elif event_type == "llm_failed":
        failure = terminal_payload.get("failure") or terminal_payload
        details = failure.get("details") or {}
        raw = dict(details.get("provider_attempt") or {})
        raw.update(
            {
                "status": "failed",
                "error_type": failure.get("error_type"),
                "recoverable": failure.get("recoverable"),
                "retry_recommended": failure.get("retry_recommended"),
            }
        )
    else:
        raise ProviderAttemptTelemetryStop(f"unsupported terminal event: {event_type}")

    return build_attempt_receipt(
        raw,
        attempt_id=attempt_id,
        execution_id=attempt_id,
        request_ordinal=int(request_trace.get("request_ordinal") or 1),
        request_hash=request_hash,
        request_context_hash=request_context_hash,
        session_turn_source_hash=session_turn_source_hash,
    )


def require_settleable_usage(receipt: ProviderAttemptReceipt) -> AttemptUsage:
    """Return usage suitable for campaign reconciliation or stop fail-closed."""

    if not receipt.usage.usage_observed:
        raise ProviderAttemptTelemetryStop(
            f"unknown provider usage stops attempt reconciliation: {receipt.attempt_id}"
        )
    return receipt.usage


__all__ = [
    "AttemptStatus",
    "AttemptUsage",
    "DownstreamValidation",
    "ProviderAttemptReceipt",
    "ProviderAttemptTelemetryStop",
    "RecoveryAction",
    "build_attempt_receipt",
    "receipt_from_diagnostic_events",
    "replay_receipt",
    "require_settleable_usage",
]
