"""Body-free reusable compaction artifact admission helpers.

This module is shadow-only: it validates explicit artifact references against
the current context evidence, but never reads an artifact body or authorizes a
summary to enter a model-facing prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metadata import (
    ContextCompactionBinding,
    ContextCompactionReuseAdmission,
    ContextCompactionReuseAdmissionStatus,
    ContextCompactionReuseRejectionReason,
    RuntimePromptContextSnapshot,
)


_ZERO_HASH = "sha256:" + "0" * 64
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMPACTION_ALGORITHMS = frozenset(
    {
        "deterministic_dialog_extract_v1",
        "deterministic_observation_mask_v1",
        "llm_rolling_summary_v1",
    }
)


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_source_key(candidate_ids: Sequence[str]) -> str:
    return json.dumps(list(candidate_ids), ensure_ascii=False, separators=(",", ":"))


class ReusableCompactionArtifactCandidate(BaseModel):
    """Runtime-only, body-free view of a reusable compaction artifact."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    candidate_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    artifact_integrity_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_artifact_integrity_checksum: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    record_compaction_id: str = Field(min_length=1)
    record_algorithm: str = Field(min_length=1)
    source_candidate_ids: list[str] = Field(min_length=1)
    source_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_binding_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    required_candidate_ids: list[str] = Field(default_factory=list)
    recent_suffix_ids: list[str] = Field(default_factory=list)
    session_constraints_hash: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    generated_summary_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )

    @classmethod
    def from_binding(
        cls,
        binding: ContextCompactionBinding,
        *,
        source_binding_hash: str | None = None,
        candidate_id: str | None = None,
        required_candidate_ids: Sequence[str] = (),
        recent_suffix_ids: Sequence[str] = (),
        session_constraints_hash: str | None = None,
        expected_artifact_integrity_checksum: str | None = None,
    ) -> "ReusableCompactionArtifactCandidate":
        effective_hash = source_binding_hash or binding.source_binding_hash
        if not effective_hash:
            raise ValueError("source binding hash is required for reusable compaction")
        return cls(
            candidate_id=candidate_id or f"reuse:{binding.artifact.artifact_id}",
            artifact_id=binding.artifact.artifact_id,
            artifact_kind=binding.artifact.kind,
            artifact_integrity_checksum=binding.artifact.integrity_checksum,
            expected_artifact_integrity_checksum=expected_artifact_integrity_checksum,
            record_compaction_id=binding.record.compaction_id,
            record_algorithm=binding.record.algorithm,
            source_candidate_ids=list(binding.record.source_candidate_ids),
            source_fingerprint=binding.record.source_fingerprint,
            source_binding_hash=effective_hash,
            required_candidate_ids=list(required_candidate_ids),
            recent_suffix_ids=list(recent_suffix_ids),
            session_constraints_hash=session_constraints_hash,
            generated_summary_fingerprint=_sha256_text(binding.record.summary),
        )

    @model_validator(mode="after")
    def _candidate_is_unique(self) -> "ReusableCompactionArtifactCandidate":
        if self.record_algorithm not in _COMPACTION_ALGORITHMS:
            raise ValueError("reusable compaction record algorithm is not supported")
        for label, values in (
            ("source candidate IDs", self.source_candidate_ids),
            ("required candidate IDs", self.required_candidate_ids),
            ("recent suffix IDs", self.recent_suffix_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"reusable compaction {label} must be unique")
        return self


def source_binding_hash_from_shadow_payload(
    shadow_payload: Mapping[str, Any],
    source_candidate_ids: Sequence[str],
) -> str | None:
    """Hash the current body-free source view for the candidate's sources."""

    raw_digests = shadow_payload.get("candidate_digests")
    if not isinstance(raw_digests, Sequence) or isinstance(raw_digests, (str, bytes)):
        return None
    by_id = {
        str(item.get("candidate_id")): item
        for item in raw_digests
        if isinstance(item, Mapping) and item.get("candidate_id") is not None
    }
    source_ids = list(source_candidate_ids)
    if any(source_id not in by_id for source_id in source_ids):
        return None
    ordered = []
    for source_id in source_ids:
        item = by_id[source_id]
        ordered.append(
            {
                "candidate_id": source_id,
                "kind": item.get("kind"),
                "source_id": item.get("source_id"),
                "role": item.get("role"),
                "retention": item.get("retention"),
                "trust": item.get("trust"),
                "freshness": item.get("freshness"),
                "truncation": item.get("truncation"),
                "source_order": item.get("source_order"),
                "content_sha256": item.get("content_sha256"),
            }
        )
    encoded = json.dumps(
        ordered, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def admit_reusable_compaction_candidate(
    candidate: ReusableCompactionArtifactCandidate,
    shadow_payload: Mapping[str, Any],
    *,
    admission_id: str | None = None,
) -> ContextCompactionReuseAdmission:
    """Return one shadow admission or typed rejection for the current context."""

    reason: ContextCompactionReuseRejectionReason | None = None
    selected_ids = {
        item for item in shadow_payload.get("selected_candidate_ids", ()) if isinstance(item, str)
    }
    current_hash = source_binding_hash_from_shadow_payload(
        shadow_payload, candidate.source_candidate_ids
    )
    if candidate.artifact_kind != "context_compaction":
        reason = ContextCompactionReuseRejectionReason.ARTIFACT_KIND_MISMATCH
    elif (
        candidate.expected_artifact_integrity_checksum is not None
        and candidate.expected_artifact_integrity_checksum
        != candidate.artifact_integrity_checksum
    ):
        reason = ContextCompactionReuseRejectionReason.ARTIFACT_INTEGRITY_MISMATCH
    elif current_hash is None:
        reason = ContextCompactionReuseRejectionReason.SOURCE_CANDIDATE_IDS_MISMATCH
    elif current_hash != candidate.source_binding_hash:
        reason = ContextCompactionReuseRejectionReason.SOURCE_BINDING_HASH_MISMATCH
    else:
        fingerprints = shadow_payload.get("source_fingerprint_by_candidate_ids")
        current_fingerprint = (
            str(fingerprints.get(_canonical_source_key(candidate.source_candidate_ids)) or "")
            if isinstance(fingerprints, Mapping)
            else ""
        )
        if current_fingerprint != candidate.source_fingerprint:
            reason = ContextCompactionReuseRejectionReason.SOURCE_FINGERPRINT_MISMATCH
    if reason is None and not set(candidate.required_candidate_ids).issubset(selected_ids):
        reason = ContextCompactionReuseRejectionReason.REQUIRED_CANDIDATE_IDS_MISMATCH
    elif reason is None and not set(candidate.recent_suffix_ids).issubset(selected_ids):
        reason = ContextCompactionReuseRejectionReason.RECENT_SUFFIX_IDS_MISMATCH
    current_constraints_hash = str(shadow_payload.get("session_constraints_hash") or "")
    if reason is None and (candidate.session_constraints_hash or "") != current_constraints_hash:
        reason = ContextCompactionReuseRejectionReason.SESSION_CONSTRAINTS_HASH_MISMATCH

    status = (
        ContextCompactionReuseAdmissionStatus.REJECTED
        if reason is not None
        else ContextCompactionReuseAdmissionStatus.ADMITTED
    )
    return ContextCompactionReuseAdmission(
        admission_id=admission_id or f"reuse:{candidate.candidate_id}",
        status=status,
        rejection_reason=reason,
        source_candidate_ids=list(candidate.source_candidate_ids),
        source_fingerprint=candidate.source_fingerprint,
        source_binding_hash=candidate.source_binding_hash,
        required_candidate_ids=list(candidate.required_candidate_ids),
        recent_suffix_ids=list(candidate.recent_suffix_ids),
        session_constraints_hash=candidate.session_constraints_hash,
        artifact_id=candidate.artifact_id,
        artifact_kind=candidate.artifact_kind,
        artifact_integrity_checksum=candidate.artifact_integrity_checksum,
        generated_summary_fingerprint=candidate.generated_summary_fingerprint,
        used_in_prompt=False,
    )


def build_compaction_reuse_shadow_provider(
    candidates: Sequence[ReusableCompactionArtifactCandidate | Mapping[str, Any]],
) -> Callable[[dict[str, Any]], list[ContextCompactionReuseAdmission]]:
    """Build a pure shadow provider from explicit body-free candidates."""

    frozen = tuple(
        item
        if isinstance(item, ReusableCompactionArtifactCandidate)
        else ReusableCompactionArtifactCandidate.model_validate(dict(item))
        for item in candidates
    )

    def provider(shadow_payload: dict[str, Any]) -> list[ContextCompactionReuseAdmission]:
        return [
            admit_reusable_compaction_candidate(
                candidate,
                shadow_payload,
                admission_id=f"reuse:{index}:{candidate.candidate_id}",
            )
            for index, candidate in enumerate(frozen, start=1)
        ]

    return provider


def build_checkpoint_compaction_reuse_shadow_provider(
    checkpoint_source: (
        RuntimePromptContextSnapshot
        | Sequence[ContextCompactionBinding | Mapping[str, Any]]
        | Mapping[str, Any]
    ),
    *,
    source_binding_hashes: Mapping[str, str] | None = None,
    required_candidate_ids_by_compaction_id: Mapping[str, Sequence[str]] | None = None,
    recent_suffix_ids_by_compaction_id: Mapping[str, Sequence[str]] | None = None,
    session_constraints_hash: str | None = None,
    expected_artifact_integrity_checksums: Mapping[str, str] | None = None,
) -> Callable[[dict[str, Any]], list[ContextCompactionReuseAdmission]]:
    """Build a shadow provider from checkpoint-owned compaction bindings."""

    if isinstance(checkpoint_source, RuntimePromptContextSnapshot):
        raw_bindings = checkpoint_source.compaction_bindings
    elif isinstance(checkpoint_source, Mapping):
        raw_bindings = checkpoint_source.get("compaction_bindings", ())
    else:
        raw_bindings = checkpoint_source
    bindings = tuple(
        item
        if isinstance(item, ContextCompactionBinding)
        else ContextCompactionBinding.model_validate(item)
        for item in raw_bindings
    )
    compatibility_hashes = dict(source_binding_hashes or {})
    required_ids = dict(required_candidate_ids_by_compaction_id or {})
    recent_ids = dict(recent_suffix_ids_by_compaction_id or {})
    expected_checksums = dict(expected_artifact_integrity_checksums or {})

    def provider(shadow_payload: dict[str, Any]) -> list[ContextCompactionReuseAdmission]:
        admissions: list[ContextCompactionReuseAdmission] = []
        for index, binding in enumerate(bindings, start=1):
            compaction_id = binding.record.compaction_id
            admission_id = f"checkpoint-reuse:{index}:{compaction_id}"
            source_hash = binding.source_binding_hash or compatibility_hashes.get(compaction_id)
            required = list(required_ids.get(compaction_id, ()))
            recent = list(recent_ids.get(compaction_id, ()))
            if not source_hash:
                admissions.append(_rejected_admission(binding, admission_id, required, recent, None))
                continue
            if binding.source_binding_hash and compaction_id in compatibility_hashes:
                if binding.source_binding_hash != compatibility_hashes[compaction_id]:
                    admissions.append(_rejected_admission(binding, admission_id, required, recent, None))
                    continue
            try:
                candidate = ReusableCompactionArtifactCandidate.from_binding(
                    binding,
                    source_binding_hash=source_hash,
                    candidate_id=f"checkpoint:{binding.artifact.artifact_id}",
                    required_candidate_ids=required,
                    recent_suffix_ids=recent,
                    session_constraints_hash=session_constraints_hash,
                    expected_artifact_integrity_checksum=expected_checksums.get(
                        binding.artifact.artifact_id
                    ),
                )
                admissions.append(admit_reusable_compaction_candidate(candidate, shadow_payload, admission_id=admission_id))
            except Exception:
                admissions.append(_rejected_admission(binding, admission_id, required, recent, source_hash))
        return admissions

    return provider


def _rejected_admission(
    binding: ContextCompactionBinding,
    admission_id: str,
    required_ids: Sequence[str],
    recent_ids: Sequence[str],
    source_hash: str | None,
) -> ContextCompactionReuseAdmission:
    artifact_checksum = (
        binding.artifact.integrity_checksum
        if _SHA256_RE.fullmatch(binding.artifact.integrity_checksum or "")
        else _ZERO_HASH
    )
    safe_source_hash = source_hash if _SHA256_RE.fullmatch(source_hash or "") else _ZERO_HASH
    return ContextCompactionReuseAdmission(
        admission_id=admission_id,
        status=ContextCompactionReuseAdmissionStatus.REJECTED,
        rejection_reason=ContextCompactionReuseRejectionReason.ARTIFACT_CONTRACT_INVALID,
        source_candidate_ids=list(binding.record.source_candidate_ids),
        source_fingerprint=binding.record.source_fingerprint,
        source_binding_hash=safe_source_hash,
        required_candidate_ids=list(required_ids),
        recent_suffix_ids=list(recent_ids),
        artifact_id=binding.artifact.artifact_id,
        artifact_kind=binding.artifact.kind,
        artifact_integrity_checksum=artifact_checksum,
        generated_summary_fingerprint=_sha256_text(binding.record.summary),
        used_in_prompt=False,
    )


__all__ = [
    "ReusableCompactionArtifactCandidate",
    "admit_reusable_compaction_candidate",
    "build_checkpoint_compaction_reuse_shadow_provider",
    "build_compaction_reuse_shadow_provider",
    "source_binding_hash_from_shadow_payload",
]
