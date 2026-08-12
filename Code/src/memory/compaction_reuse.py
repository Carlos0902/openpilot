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
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from memory.compaction_summary import source_candidate_binding_hash
from metadata import (
    ContextAssemblyPolicy,
    ContextAssemblyStatus,
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
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


class ReusableCompactionSemanticFact(BaseModel):
    """One deterministic fact required before a reusable prompt-use trial."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    fact_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence_candidate_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _fact_is_bounded(self) -> "ReusableCompactionSemanticFact":
        if not " ".join(self.text.split()):
            raise ValueError("semantic fact text cannot be empty")
        if len(self.evidence_candidate_ids) != len(set(self.evidence_candidate_ids)):
            raise ValueError("semantic fact evidence IDs must be unique")
        return self


class ReusableCompactionPromptUsePreflightStatus(str, Enum):
    PASSED = "passed"
    REJECTED = "rejected"


class ReusableCompactionPromptUseRejectionReason(str, Enum):
    ADMISSION_NOT_ADMITTED = "admission_not_admitted"
    ADMISSION_BINDING_MISMATCH = "admission_binding_mismatch"
    ARTIFACT_KIND_MISMATCH = "artifact_kind_mismatch"
    ARTIFACT_INTEGRITY_MISMATCH = "artifact_integrity_mismatch"
    SOURCE_BINDING_HASH_MISSING = "source_binding_hash_missing"
    SOURCE_CANDIDATE_IDS_MISMATCH = "source_candidate_ids_mismatch"
    SOURCE_BINDING_HASH_MISMATCH = "source_binding_hash_mismatch"
    REQUIRED_CANDIDATE_OMITTED = "required_candidate_omitted"
    RECENT_SUFFIX_OMITTED = "recent_suffix_omitted"
    SEMANTIC_FACTS_MISSING = "semantic_facts_missing"
    SEMANTIC_EVIDENCE_MISMATCH = "semantic_evidence_mismatch"
    SEMANTIC_FACT_MISSING = "semantic_fact_missing"
    TRIAL_ASSEMBLY_NOT_READY = "trial_assembly_not_ready"
    TRIAL_SUMMARY_NOT_SELECTED = "trial_summary_not_selected"
    TRIAL_SOURCE_NOT_GOVERNED = "trial_source_not_governed"
    TRIAL_REQUIRED_CANDIDATE_OMITTED = "trial_required_candidate_omitted"
    TRIAL_RECENT_SUFFIX_OMITTED = "trial_recent_suffix_omitted"


class ReusableCompactionPromptUsePreflight(BaseModel):
    """Body-free dry-run evidence before reusable summary prompt use."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)

    preflight_id: str = Field(min_length=1)
    status: ReusableCompactionPromptUsePreflightStatus
    rejection_reasons: list[ReusableCompactionPromptUseRejectionReason] = Field(
        default_factory=list
    )
    source_candidate_ids: list[str] = Field(min_length=1)
    source_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_binding_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    required_candidate_ids: list[str] = Field(default_factory=list)
    recent_suffix_ids: list[str] = Field(default_factory=list)
    semantic_fact_ids: list[str] = Field(default_factory=list)
    semantic_evidence_candidate_ids: list[str] = Field(default_factory=list)
    artifact_id: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    artifact_integrity_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    generated_summary_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    trial_summary_candidate_id: str = Field(min_length=1)
    trial_assembly_status: str | None = None
    trial_selected_candidate_ids: list[str] = Field(default_factory=list)
    trial_replaced_source_candidate_ids: list[str] = Field(default_factory=list)
    trial_prompt_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    trial_final_prompt_chars: int | None = Field(default=None, ge=0)
    used_in_prompt: bool = False

    @model_validator(mode="after")
    def _preflight_is_consistent(self) -> "ReusableCompactionPromptUsePreflight":
        for label, values in (
            ("rejection reasons", self.rejection_reasons),
            ("source candidate IDs", self.source_candidate_ids),
            ("required candidate IDs", self.required_candidate_ids),
            ("recent suffix IDs", self.recent_suffix_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"preflight {label} must be unique")
        if self.status == ReusableCompactionPromptUsePreflightStatus.PASSED:
            if self.rejection_reasons:
                raise ValueError("passed preflight cannot carry rejection reasons")
            if set(self.trial_replaced_source_candidate_ids) != set(self.source_candidate_ids):
                raise ValueError("passed preflight must replace every source candidate")
        elif not self.rejection_reasons:
            raise ValueError("rejected preflight requires a rejection reason")
        if self.used_in_prompt:
            raise ValueError("preflight is dry-run only and cannot enter the prompt")
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


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def preflight_reusable_compaction_prompt_use(
    *,
    binding: ContextCompactionBinding | Mapping[str, Any],
    candidates: Sequence[ContextCandidate | Mapping[str, Any]],
    policy: ContextAssemblyPolicy | Mapping[str, Any],
    renderer: Callable[[list[ContextCandidate]], str],
    admission: ContextCompactionReuseAdmission | Mapping[str, Any] | None = None,
    semantic_facts: Sequence[ReusableCompactionSemanticFact | Mapping[str, Any]] = (),
    required_candidate_ids: Sequence[str] = (),
    recent_suffix_ids: Sequence[str] = (),
    expected_artifact_integrity_checksum: str | None = None,
    preflight_id: str | None = None,
    summary_candidate_id: str | None = None,
    token_counter: Any | None = None,
) -> ReusableCompactionPromptUsePreflight:
    """Dry-run whether a reusable compaction may safely enter a future prompt."""

    validated_binding = (
        binding
        if isinstance(binding, ContextCompactionBinding)
        else ContextCompactionBinding.model_validate(binding)
    )
    validated_candidates = [
        item if isinstance(item, ContextCandidate) else ContextCandidate.model_validate(item)
        for item in candidates
    ]
    validated_policy = (
        policy
        if isinstance(policy, ContextAssemblyPolicy)
        else ContextAssemblyPolicy.model_validate(policy)
    )
    facts = [
        item
        if isinstance(item, ReusableCompactionSemanticFact)
        else ReusableCompactionSemanticFact.model_validate(item)
        for item in semantic_facts
    ]
    source_ids = list(validated_binding.record.source_candidate_ids)
    required_ids = list(required_candidate_ids)
    recent_ids = list(recent_suffix_ids)
    reasons: list[ReusableCompactionPromptUseRejectionReason] = []

    def add_reason(reason: ReusableCompactionPromptUseRejectionReason) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if len(required_ids) != len(set(required_ids)):
        add_reason(ReusableCompactionPromptUseRejectionReason.REQUIRED_CANDIDATE_OMITTED)
    if len(recent_ids) != len(set(recent_ids)):
        add_reason(ReusableCompactionPromptUseRejectionReason.RECENT_SUFFIX_OMITTED)
    if validated_binding.artifact.kind != "context_compaction":
        add_reason(ReusableCompactionPromptUseRejectionReason.ARTIFACT_KIND_MISMATCH)
    if (
        expected_artifact_integrity_checksum is not None
        and expected_artifact_integrity_checksum != validated_binding.artifact.integrity_checksum
    ):
        add_reason(ReusableCompactionPromptUseRejectionReason.ARTIFACT_INTEGRITY_MISMATCH)
    if not validated_binding.source_binding_hash:
        add_reason(ReusableCompactionPromptUseRejectionReason.SOURCE_BINDING_HASH_MISSING)

    admission_value = None
    if admission is None:
        add_reason(ReusableCompactionPromptUseRejectionReason.ADMISSION_NOT_ADMITTED)
    else:
        admission_value = (
            admission
            if isinstance(admission, ContextCompactionReuseAdmission)
            else ContextCompactionReuseAdmission.model_validate(admission)
        )
        if admission_value.status != ContextCompactionReuseAdmissionStatus.ADMITTED:
            add_reason(ReusableCompactionPromptUseRejectionReason.ADMISSION_NOT_ADMITTED)
        if (
            admission_value.artifact_id != validated_binding.artifact.artifact_id
            or admission_value.source_candidate_ids != source_ids
            or admission_value.source_binding_hash != validated_binding.source_binding_hash
        ):
            add_reason(ReusableCompactionPromptUseRejectionReason.ADMISSION_BINDING_MISMATCH)

    by_id = {candidate.candidate_id: candidate for candidate in validated_candidates}
    source_candidates = [by_id[source_id] for source_id in source_ids if source_id in by_id]
    if len(source_candidates) != len(source_ids):
        add_reason(ReusableCompactionPromptUseRejectionReason.SOURCE_CANDIDATE_IDS_MISMATCH)
    elif validated_binding.source_binding_hash and (
        source_candidate_binding_hash(source_candidates) != validated_binding.source_binding_hash
    ):
        add_reason(ReusableCompactionPromptUseRejectionReason.SOURCE_BINDING_HASH_MISMATCH)

    candidate_ids = set(by_id)
    if not set(required_ids).issubset(candidate_ids):
        add_reason(ReusableCompactionPromptUseRejectionReason.REQUIRED_CANDIDATE_OMITTED)
    if not set(recent_ids).issubset(candidate_ids) or set(recent_ids).intersection(source_ids):
        add_reason(ReusableCompactionPromptUseRejectionReason.RECENT_SUFFIX_OMITTED)
    if not facts:
        add_reason(ReusableCompactionPromptUseRejectionReason.SEMANTIC_FACTS_MISSING)
    summary_text = _normalize_text(validated_binding.record.summary)
    semantic_evidence_ids: list[str] = []
    for fact in facts:
        if not set(fact.evidence_candidate_ids).issubset(set(source_ids)):
            add_reason(ReusableCompactionPromptUseRejectionReason.SEMANTIC_EVIDENCE_MISMATCH)
        for evidence_id in fact.evidence_candidate_ids:
            if evidence_id not in semantic_evidence_ids:
                semantic_evidence_ids.append(evidence_id)
        if _normalize_text(fact.text) not in summary_text:
            add_reason(ReusableCompactionPromptUseRejectionReason.SEMANTIC_FACT_MISSING)

    trial_selected_ids: list[str] = []
    trial_replaced_ids: list[str] = []
    trial_status: str | None = None
    trial_prompt_hash: str | None = None
    trial_chars: int | None = None
    summary_id = summary_candidate_id or f"reuse-compaction:{validated_binding.record.compaction_id}"
    if len(source_candidates) == len(source_ids):
        summary_candidate = ContextCandidate(
            candidate_id=summary_id,
            kind=ContextCandidateKind.ARTIFACT,
            source_id=validated_binding.record.source_fingerprint,
            content=validated_binding.record.summary,
            retention=ContextCandidateRetention.PREFERRED,
            priority=99,
            source_order=500,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            trust=ContextCandidateTrust.DERIVED,
            freshness=ContextCandidateFreshness.CURRENT,
            compacted_candidate_ids=source_ids,
        )
        try:
            from memory.context_assembly import ContextAssembler

            trial = ContextAssembler(
                renderer=lambda _payload: "",
                token_counter=token_counter,
            ).assemble_candidates(
                [*validated_candidates, summary_candidate],
                policy=validated_policy,
                renderer=renderer,
            )
            trial_status = str(
                getattr(trial.selection.assembly_status, "value", trial.selection.assembly_status)
            )
            trial_selected_ids = [candidate.candidate_id for candidate in trial.selected_candidates]
            trial_prompt_hash = _sha256_text(trial.prompt_text)
            trial_chars = len(trial.prompt_text)
            decision_by_id = {
                decision.candidate_id: decision
                for decision in trial.selection.candidate_decisions
            }
            summary_decision = decision_by_id.get(summary_id)
            if trial.selection.assembly_status != ContextAssemblyStatus.READY:
                add_reason(ReusableCompactionPromptUseRejectionReason.TRIAL_ASSEMBLY_NOT_READY)
            if summary_decision is None or summary_decision.action != "kept":
                add_reason(ReusableCompactionPromptUseRejectionReason.TRIAL_SUMMARY_NOT_SELECTED)
            for source_id in source_ids:
                decision = decision_by_id.get(source_id)
                if (
                    decision is not None
                    and decision.action == "omitted"
                    and decision.reason == "compacted"
                    and decision.governed_by_candidate_id == summary_id
                ):
                    trial_replaced_ids.append(source_id)
                else:
                    add_reason(ReusableCompactionPromptUseRejectionReason.TRIAL_SOURCE_NOT_GOVERNED)
            for required_id in required_ids:
                decision = decision_by_id.get(required_id)
                if decision is None or decision.action != "kept":
                    add_reason(ReusableCompactionPromptUseRejectionReason.TRIAL_REQUIRED_CANDIDATE_OMITTED)
            for recent_id in recent_ids:
                decision = decision_by_id.get(recent_id)
                if decision is None or decision.action != "kept":
                    add_reason(ReusableCompactionPromptUseRejectionReason.TRIAL_RECENT_SUFFIX_OMITTED)
        except Exception:
            add_reason(ReusableCompactionPromptUseRejectionReason.TRIAL_ASSEMBLY_NOT_READY)

    status = (
        ReusableCompactionPromptUsePreflightStatus.REJECTED
        if reasons
        else ReusableCompactionPromptUsePreflightStatus.PASSED
    )
    return ReusableCompactionPromptUsePreflight(
        preflight_id=preflight_id or f"preflight:{validated_binding.record.compaction_id}",
        status=status,
        rejection_reasons=reasons,
        source_candidate_ids=source_ids,
        source_fingerprint=validated_binding.record.source_fingerprint,
        source_binding_hash=validated_binding.source_binding_hash or _ZERO_HASH,
        required_candidate_ids=required_ids,
        recent_suffix_ids=recent_ids,
        semantic_fact_ids=[fact.fact_id for fact in facts],
        semantic_evidence_candidate_ids=semantic_evidence_ids,
        artifact_id=validated_binding.artifact.artifact_id,
        artifact_kind=validated_binding.artifact.kind,
        artifact_integrity_checksum=validated_binding.artifact.integrity_checksum,
        generated_summary_fingerprint=_sha256_text(validated_binding.record.summary),
        trial_summary_candidate_id=summary_id,
        trial_assembly_status=trial_status,
        trial_selected_candidate_ids=trial_selected_ids,
        trial_replaced_source_candidate_ids=trial_replaced_ids,
        trial_prompt_hash=trial_prompt_hash,
        trial_final_prompt_chars=trial_chars,
        used_in_prompt=False,
    )


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
    "ReusableCompactionPromptUsePreflight",
    "ReusableCompactionPromptUsePreflightStatus",
    "ReusableCompactionPromptUseRejectionReason",
    "ReusableCompactionSemanticFact",
    "admit_reusable_compaction_candidate",
    "build_checkpoint_compaction_reuse_shadow_provider",
    "build_compaction_reuse_shadow_provider",
    "preflight_reusable_compaction_prompt_use",
    "source_binding_hash_from_shadow_payload",
]
