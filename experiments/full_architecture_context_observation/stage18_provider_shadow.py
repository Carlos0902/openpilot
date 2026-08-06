"""Provider-neutral shadow caller for the Stage 6B experiment.

The caller deliberately stops at an observation boundary.  It validates a
candidate rolling summary and records the attempt, but it never hands the
candidate to ``MemoryContextBuilder`` or any task executor.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.llm import LLMMessage, LLMRequest, LLMResponse
from memory.rolling_compaction import (
    RollingSummaryAdapter,
    RollingSummaryAttemptEvidence,
    RollingSummaryFallbackReason,
    RollingSummaryRequest,
)
from metadata import ContextCompactionRecord
from stage17_real_provider_readiness import (
    ExperimentArm,
    RollingSummaryExperimentManifest,
    verify_manifest_hash,
)
from stage7c_provider_attempt_telemetry import (
    AttemptStatus,
    ProviderAttemptReceipt,
    build_attempt_receipt,
)


class ShadowStopReason(str, Enum):
    INVALID_MANIFEST = "invalid_manifest"
    MANIFEST_NOT_TREATMENT = "manifest_not_treatment"
    ZERO_SUMMARY_BUDGET = "zero_summary_budget"
    PROVIDER_EXCEPTION = "provider_exception"


class RollingSummaryShadowRequest(BaseModel):
    """Immutable source snapshot and budget inputs for one shadow attempt."""

    model_config = ConfigDict(extra="forbid")

    manifest: RollingSummaryExperimentManifest
    source_candidate_ids: tuple[str, ...] = Field(min_length=1)
    source_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    current_source_fingerprint: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    previous_summary_fingerprint: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    source_text: str = Field(min_length=1)
    original_chars: int = Field(ge=1)
    requested_prompt_tokens: int = Field(ge=1)
    used_prompt_tokens: int = Field(ge=0)
    purpose: str = Field(min_length=1, max_length=120)
    execution_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    request_ordinal: int = Field(ge=1)

    @model_validator(mode="after")
    def _source_size_is_immutable(self) -> "RollingSummaryShadowRequest":
        if self.original_chars != len(self.source_text):
            raise ValueError("original_chars must match source_text")
        if len(self.source_candidate_ids) != len(set(self.source_candidate_ids)):
            raise ValueError("source candidate IDs must be unique")
        if any(not candidate_id.strip() for candidate_id in self.source_candidate_ids):
            raise ValueError("source candidate IDs must be non-empty")
        return self


class ProviderShadowObservation(BaseModel):
    """Durable shadow result whose summary is explicitly not prompt input."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "rolling_summary_shadow_observation_v1"
    manifest_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    used_in_prompt: Literal[False] = False
    accepted: bool
    attempt: ProviderAttemptReceipt
    response_payload: dict[str, Any] | None = None
    summary_record: ContextCompactionRecord | None = None
    fallback_reason: RollingSummaryFallbackReason | None = None
    stop_reason: ShadowStopReason | None = None

    @model_validator(mode="after")
    def _observation_is_consistent(self) -> "ProviderShadowObservation":
        if self.accepted != (self.summary_record is not None):
            raise ValueError("accepted must match summary_record presence")
        if self.accepted and (self.fallback_reason is not None or self.stop_reason is not None):
            raise ValueError("accepted observation cannot carry fallback/stop reasons")
        if not self.accepted and self.summary_record is not None:
            raise ValueError("fallback observation cannot carry a summary record")
        if self.stop_reason is not None and self.fallback_reason is not None:
            raise ValueError("one observation cannot carry both stop and fallback reasons")
        return self


def build_shadow_request(**kwargs: Any) -> RollingSummaryShadowRequest:
    """Construct the validated immutable shadow input boundary."""

    return RollingSummaryShadowRequest.model_validate(kwargs)


def _request_hash(request: RollingSummaryShadowRequest, summary_limit: int) -> str:
    payload = {
        "manifest_hash": request.manifest.manifest_hash,
        "source_candidate_ids": list(request.source_candidate_ids),
        "source_fingerprint": request.source_fingerprint,
        "previous_summary_fingerprint": request.previous_summary_fingerprint,
        "source_text": request.source_text,
        "requested_prompt_tokens": request.requested_prompt_tokens,
        "used_prompt_tokens": request.used_prompt_tokens,
        "summary_limit": summary_limit,
        "purpose": request.purpose,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "v2:sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _observation(
    request: RollingSummaryShadowRequest,
    receipt: ProviderAttemptReceipt,
    *,
    response_payload: Mapping[str, Any] | None = None,
    summary_record: ContextCompactionRecord | None = None,
    fallback_reason: RollingSummaryFallbackReason | None = None,
    stop_reason: ShadowStopReason | None = None,
) -> ProviderShadowObservation:
    return ProviderShadowObservation(
        manifest_hash=request.manifest.manifest_hash,
        accepted=summary_record is not None,
        attempt=receipt,
        response_payload=dict(response_payload) if response_payload is not None else None,
        summary_record=summary_record,
        fallback_reason=fallback_reason,
        stop_reason=stop_reason,
    )


def _pretransport_receipt(
    request: RollingSummaryShadowRequest,
    *,
    request_hash: str,
) -> ProviderAttemptReceipt:
    return build_attempt_receipt(
        {
            "status": AttemptStatus.PRETRANSPORT_BLOCKED.value,
            "transport_attempted": False,
            "recovery_action": "stop",
        },
        attempt_id=request.attempt_id,
        execution_id=request.execution_id,
        request_ordinal=request.request_ordinal,
        request_hash=request_hash,
        provider=request.manifest.provider,
        model=request.manifest.model,
        endpoint=request.manifest.endpoint,
        request_context_hash=request.manifest.manifest_hash,
        session_turn_source_hash=request.source_fingerprint,
    )


def run_provider_shadow(
    request: RollingSummaryShadowRequest,
    *,
    transport: Callable[[LLMRequest], LLMResponse],
    count_tokens: Callable[[str], int],
    adapter: RollingSummaryAdapter | None = None,
) -> ProviderShadowObservation:
    """Run one shadow attempt and return evidence without changing Prompt state."""

    policy = request.manifest.budget_policy
    summary_limit = policy.dynamic_summary_limit(
        requested_prompt_tokens=request.requested_prompt_tokens,
        used_prompt_tokens=request.used_prompt_tokens,
    )
    request_hash = _request_hash(request, summary_limit)

    if not verify_manifest_hash(request.manifest):
        return _observation(
            request,
            _pretransport_receipt(request, request_hash=request_hash),
            stop_reason=ShadowStopReason.INVALID_MANIFEST,
        )
    if request.manifest.arm is not ExperimentArm.TREATMENT or not request.manifest.flags.treatment_enabled:
        return _observation(
            request,
            _pretransport_receipt(request, request_hash=request_hash),
            stop_reason=ShadowStopReason.MANIFEST_NOT_TREATMENT,
        )
    if summary_limit < 1:
        return _observation(
            request,
            _pretransport_receipt(request, request_hash=request_hash),
            stop_reason=ShadowStopReason.ZERO_SUMMARY_BUDGET,
        )

    prompt_payload = {
        "source_candidate_ids": list(request.source_candidate_ids),
        "source_fingerprint": request.source_fingerprint,
        "previous_summary_fingerprint": request.previous_summary_fingerprint,
        "purpose": request.purpose,
        "source_text": request.source_text,
    }
    llm_request = LLMRequest(
        messages=[
            LLMMessage(
                role="system",
                content=(
                    "Summarize only the supplied old context segment. Return a JSON object "
                    "with goal_delta, verified_facts, decisions, open_issues, evidence_ids, "
                    "and next_action. Do not invent evidence or authority."
                ),
            ),
            LLMMessage(
                role="user",
                content=json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True),
            ),
        ],
        response_format="json_object",
        temperature=0.0,
        max_tokens=summary_limit,
        trace_info={
            "experiment_manifest_hash": request.manifest.manifest_hash,
            "shadow_only": True,
            "summary_schema_version": request.manifest.summary_schema_version,
        },
    )
    try:
        response = transport(llm_request)
        if not isinstance(response, LLMResponse):
            raise TypeError("shadow transport must return LLMResponse")
    except Exception as exc:  # transport is untrusted and must not escape the gate
        error_context = getattr(exc, "context", {})
        if not isinstance(error_context, Mapping):
            error_context = {}
        category = getattr(exc, "category", None)
        category_value = getattr(category, "value", None) or (
            str(category) if category is not None else "provider_transport"
        )
        exception_usage = getattr(exc, "usage", None)
        if not isinstance(exception_usage, Mapping):
            exception_usage = error_context.get("usage")
        if not isinstance(exception_usage, Mapping):
            exception_usage = {}
        exception_content = str(getattr(exc, "response_text", "") or "")
        exception_finish_reason = getattr(exc, "finish_reason", None)
        receipt = build_attempt_receipt(
            {
                "status": AttemptStatus.FAILED.value,
                "transport_attempted": True,
                "usage": dict(exception_usage),
                "finish_reason": exception_finish_reason,
                "content": exception_content,
                "error_type": type(exc).__name__,
                "error_category": category_value,
                "recoverable": bool(error_context.get("retryable", False)),
                "retry_recommended": bool(error_context.get("retryable", False)),
                "json_repair_attempts": int(error_context.get("json_repair_attempts") or 0),
            },
            attempt_id=request.attempt_id,
            execution_id=request.execution_id,
            request_ordinal=request.request_ordinal,
            request_hash=request_hash,
            provider=request.manifest.provider,
            model=request.manifest.model,
            endpoint=request.manifest.endpoint,
            request_context_hash=request.manifest.manifest_hash,
            session_turn_source_hash=request.source_fingerprint,
        )
        return _observation(
            request,
            receipt,
            stop_reason=ShadowStopReason.PROVIDER_EXCEPTION,
        )

    receipt = build_attempt_receipt(
        {
            "status": AttemptStatus.RESPONDED.value,
            "transport_attempted": True,
            "usage": response.usage,
            "finish_reason": response.finish_reason,
            "content": response.content,
            "provider": response.provider,
            "model": response.model,
            "endpoint": request.manifest.endpoint,
        },
        attempt_id=request.attempt_id,
        execution_id=request.execution_id,
        request_ordinal=request.request_ordinal,
        request_hash=request_hash,
        provider=request.manifest.provider,
        model=request.manifest.model,
        endpoint=request.manifest.endpoint,
        request_context_hash=request.manifest.manifest_hash,
        session_turn_source_hash=request.source_fingerprint,
    )
    provider_payload = response.parsed_json if isinstance(response.parsed_json, Mapping) else None
    result = (adapter or RollingSummaryAdapter(count_tokens=count_tokens)).build(
        RollingSummaryRequest(
            source_candidate_ids=request.source_candidate_ids,
            source_fingerprint=request.source_fingerprint,
            current_source_fingerprint=request.current_source_fingerprint,
            previous_summary_fingerprint=request.previous_summary_fingerprint,
            provider_payload=provider_payload,
            attempt=RollingSummaryAttemptEvidence(
                usage=receipt.usage.raw_usage,
                usage_observed=receipt.usage.usage_observed,
                finish_reason=receipt.finish_reason,
            ),
            max_summary_tokens=summary_limit,
            original_chars=request.original_chars,
        )
    )
    if result.accepted:
        return _observation(
            request,
            receipt,
            response_payload=provider_payload,
            summary_record=result.record,
        )
    assert result.fallback is not None
    return _observation(
        request,
        receipt,
        response_payload=provider_payload,
        fallback_reason=result.fallback.reason,
    )


__all__ = [
    "ProviderShadowObservation",
    "RollingSummaryShadowRequest",
    "ShadowStopReason",
    "build_shadow_request",
    "run_provider_shadow",
]
