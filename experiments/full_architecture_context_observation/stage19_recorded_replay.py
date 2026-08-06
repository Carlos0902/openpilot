"""Offline replay of source-bound provider shadow responses."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from memory.rolling_compaction import (
    RollingSummaryAdapter,
    RollingSummaryAttemptEvidence,
    RollingSummaryFallbackReason,
    RollingSummaryRequest,
)
from metadata import ContextCompactionRecord
from stage17_real_provider_readiness import verify_manifest_hash
from stage18_provider_shadow import (
    ProviderShadowObservation,
    RollingSummaryShadowRequest,
    _request_hash,
)
from stage7c_provider_attempt_telemetry import ProviderAttemptReceipt, replay_receipt


class ReplayArtifactValidationError(ValueError):
    """A captured response cannot be interpreted as an exact replay."""


class RecordedShadowArtifact(BaseModel):
    """Versioned, credential-free response artifact for offline replay."""

    model_config = ConfigDict(extra="forbid")

    artifact_version: str = "rolling_summary_recorded_artifact_v1"
    manifest_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request: RollingSummaryShadowRequest
    attempt: ProviderAttemptReceipt
    response_payload: dict[str, Any]
    expected_accepted: bool
    expected_fallback_reason: RollingSummaryFallbackReason | None = None
    expected_summary_record: ContextCompactionRecord | None = None

    @model_validator(mode="after")
    def _artifact_is_consistent(self) -> "RecordedShadowArtifact":
        if self.manifest_hash != self.request.manifest.manifest_hash:
            raise ValueError("artifact manifest hash does not match request manifest")
        if self.expected_accepted != (self.expected_summary_record is not None):
            raise ValueError("expected acceptance must match expected summary record")
        if self.expected_accepted and self.expected_fallback_reason is not None:
            raise ValueError("accepted artifact cannot carry fallback reason")
        return self


def capture_recorded_artifact(
    request: RollingSummaryShadowRequest,
    observation: ProviderShadowObservation,
) -> RecordedShadowArtifact:
    """Capture only a transported, structured response suitable for replay."""

    if observation.manifest_hash != request.manifest.manifest_hash:
        raise ReplayArtifactValidationError("manifest hash does not match shadow request")
    if not observation.attempt.transport_attempted:
        raise ReplayArtifactValidationError("pre-transport attempts are not replayable")
    if not isinstance(observation.response_payload, Mapping):
        raise ReplayArtifactValidationError("replay requires a structured response payload")
    expected = _request_hash(
        request,
        request.manifest.budget_policy.dynamic_summary_limit(
            requested_prompt_tokens=request.requested_prompt_tokens,
            used_prompt_tokens=request.used_prompt_tokens,
        ),
    )
    if observation.attempt.request_hash != expected:
        raise ReplayArtifactValidationError("shadow attempt request hash is inconsistent")
    return RecordedShadowArtifact(
        manifest_hash=request.manifest.manifest_hash,
        request=request,
        attempt=observation.attempt,
        response_payload=dict(observation.response_payload),
        expected_accepted=observation.accepted,
        expected_fallback_reason=observation.fallback_reason,
        expected_summary_record=observation.summary_record,
    )


def replay_recorded_artifact(
    artifact: RecordedShadowArtifact,
    *,
    count_tokens: Callable[[str], int],
    adapter: RollingSummaryAdapter | None = None,
    execution_id: str | None = None,
    attempt_id: str | None = None,
) -> ProviderShadowObservation:
    """Replay one response without creating a Provider client or transport."""

    request = artifact.request
    if artifact.manifest_hash != request.manifest.manifest_hash:
        raise ReplayArtifactValidationError("artifact/request manifest hash mismatch")
    if not verify_manifest_hash(request.manifest):
        raise ReplayArtifactValidationError("manifest hash verification failed")
    summary_limit = request.manifest.budget_policy.dynamic_summary_limit(
        requested_prompt_tokens=request.requested_prompt_tokens,
        used_prompt_tokens=request.used_prompt_tokens,
    )
    expected_request_hash = _request_hash(request, summary_limit)
    if artifact.attempt.request_hash != expected_request_hash:
        raise ReplayArtifactValidationError("artifact request hash does not match source request")
    replayed_attempt = replay_receipt(
        artifact.attempt,
        attempt_id=attempt_id or f"replay:{artifact.attempt.attempt_id}",
        execution_id=execution_id or f"replay:{artifact.attempt.execution_id}",
        request_ordinal=artifact.attempt.request_ordinal,
    )
    result = (adapter or RollingSummaryAdapter(count_tokens=count_tokens)).build(
        RollingSummaryRequest(
            source_candidate_ids=request.source_candidate_ids,
            source_fingerprint=request.source_fingerprint,
            current_source_fingerprint=request.current_source_fingerprint,
            previous_summary_fingerprint=request.previous_summary_fingerprint,
            provider_payload=artifact.response_payload,
            attempt=RollingSummaryAttemptEvidence(
                usage=artifact.attempt.usage.raw_usage,
                usage_observed=artifact.attempt.usage.usage_observed,
                finish_reason=artifact.attempt.finish_reason,
            ),
            max_summary_tokens=summary_limit,
            original_chars=request.original_chars,
        )
    )
    if result.accepted:
        if not artifact.expected_accepted or result.record is None:
            raise ReplayArtifactValidationError("replay acceptance drifted from captured outcome")
        if artifact.expected_summary_record is None or (
            result.record.model_dump(mode="json")
            != artifact.expected_summary_record.model_dump(mode="json")
        ):
            raise ReplayArtifactValidationError("replay summary record drifted")
        return ProviderShadowObservation(
            manifest_hash=artifact.manifest_hash,
            accepted=True,
            attempt=replayed_attempt,
            response_payload=artifact.response_payload,
            summary_record=result.record,
        )

    if result.fallback is None:
        raise ReplayArtifactValidationError("replay returned neither record nor fallback")
    if artifact.expected_accepted or artifact.expected_fallback_reason != result.fallback.reason:
        raise ReplayArtifactValidationError("replay fallback decision drifted")
    return ProviderShadowObservation(
        manifest_hash=artifact.manifest_hash,
        accepted=False,
        attempt=replayed_attempt,
        response_payload=artifact.response_payload,
        fallback_reason=result.fallback.reason,
    )


__all__ = [
    "RecordedShadowArtifact",
    "ReplayArtifactValidationError",
    "capture_recorded_artifact",
    "replay_recorded_artifact",
]
