"""Typed derived view of one ContextLoader assembly.

Raw session turns remain authoritative.  This value is only a source-linked
projection that lets downstream agents consume the exact selected dialog and
durable compaction artifacts produced by ContextLoader instead of rebuilding a
second, larger history from the ingress ledger.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from memory.session_dialog import session_turn_ledger_hash
from metadata import (
    ContextCandidate,
    ContextAssemblyStatus,
    ContextCompactionBinding,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    DerivedContextProjection,
    SessionIngressState,
)


class DerivedContextProjectionError(ValueError):
    """The ContextLoader-derived view is incomplete or source-inconsistent."""


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _candidate_dialog(item: Mapping[str, Any], source_order: int) -> ContextCandidate:
    # ContextLoader already emitted a validated candidate.  Preserve its
    # identity and source lineage instead of inventing a second ID scheme.
    if "candidate_id" in item and "kind" in item:
        try:
            candidate = ContextCandidate.model_validate(item)
        except Exception as exc:
            raise DerivedContextProjectionError(
                "selected context candidate is invalid"
            ) from exc
        if candidate.kind != ContextCandidateKind.DIALOG:
            raise DerivedContextProjectionError("selected dialog candidate has the wrong kind")
        return candidate.model_copy(update={"source_order": source_order})
    message_id = str(item.get("message_id") or "").strip()
    content = str(item.get("content") or "").strip()
    role = str(item.get("role") or "").strip().lower()
    if not message_id or not content or role not in {"user", "assistant"}:
        raise DerivedContextProjectionError("selected dialog item lacks stable identity, role, or content")
    return ContextCandidate(
        candidate_id=f"derived_dialog:{message_id}",
        kind=ContextCandidateKind.DIALOG,
        source_id=message_id,
        content=f"{role.upper()}: {content}",
        role=role,
        retention=ContextCandidateRetention.PREFERRED,
        priority=70 if role == "user" else 55,
        source_order=source_order,
        truncation=ContextCandidateTruncation.HEAD,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.CURRENT,
    )


def _candidate_artifact(binding: Mapping[str, Any], source_order: int) -> ContextCandidate:
    try:
        validated_binding = ContextCompactionBinding.model_validate(binding)
    except Exception as exc:
        raise DerivedContextProjectionError("context compaction binding is invalid") from exc
    record = validated_binding.record
    compaction_id = record.compaction_id
    source_fingerprint = record.source_fingerprint
    summary = record.summary
    compacted_ids = list(record.source_candidate_ids)
    if not compaction_id or not source_fingerprint or not summary or not compacted_ids:
        raise DerivedContextProjectionError("context compaction record is incomplete")
    return ContextCandidate(
        candidate_id=f"derived_compaction:{compaction_id}",
        kind=ContextCandidateKind.ARTIFACT,
        source_id=source_fingerprint,
        content=summary,
        role="user",
        retention=ContextCandidateRetention.PREFERRED,
        priority=99,
        source_order=source_order,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.DERIVED,
        freshness=ContextCandidateFreshness.CURRENT,
        # The sources compacted upstream are intentionally not reintroduced in
        # this purpose-local candidate set.  Keep the bounded summary as
        # evidence; the binding in the ContextLoader snapshot remains the
        # provenance record.  Supplying the original source IDs here would make
        # the downstream assembler require raw candidates that were omitted.
        compacted_candidate_ids=[],
    )


def build_derived_context_projection(
    context_result: Mapping[str, Any],
    ingress: SessionIngressState,
) -> DerivedContextProjection:
    """Validate and materialize the ContextLoader-selected downstream view."""

    request_hash = str(context_result.get("context_request_hash") or "")
    if not request_hash.startswith("sha256:"):
        raise DerivedContextProjectionError("ContextLoader result has no request hash")
    expected_turn_hash = session_turn_ledger_hash(ingress)
    payload_turn_hash = str(context_result.get("session_turn_source_hash") or "")
    if payload_turn_hash and payload_turn_hash != expected_turn_hash:
        raise DerivedContextProjectionError("ContextLoader turn source hash is stale")
    payload_constraints_hash = str(context_result.get("session_constraints_hash") or "")
    if payload_constraints_hash and payload_constraints_hash != ingress.session_constraints.canonical_hash:
        raise DerivedContextProjectionError("ContextLoader constraint hash is stale")
    selection = context_result.get("context_selection")
    if not isinstance(selection, Mapping):
        raise DerivedContextProjectionError("ContextLoader result has no selection evidence")
    if str(selection.get("assembly_status") or "") != ContextAssemblyStatus.READY.value:
        raise DerivedContextProjectionError("ContextLoader assembly is not ready")
    decisions = [
        item for item in list(selection.get("candidate_decisions") or [])
        if isinstance(item, Mapping)
    ]
    selected_ids = {
        str(item.get("candidate_id"))
        for item in decisions
        if item.get("action") in {"kept", "partially_kept"}
    }
    selected_payload = list(context_result.get("selected_context_candidates") or [])
    if selected_payload:
        selected_candidates: list[ContextCandidate] = []
        for index, item in enumerate(selected_payload, start=1):
            if not isinstance(item, Mapping):
                raise DerivedContextProjectionError(
                    "selected context candidate must be an object"
                )
            try:
                candidate = ContextCandidate.model_validate(item)
            except Exception as exc:
                raise DerivedContextProjectionError(
                    "selected context candidate is invalid"
                ) from exc
            selected_candidates.append(candidate.model_copy(update={"source_order": index}))
        if {candidate.candidate_id for candidate in selected_candidates} != selected_ids:
            raise DerivedContextProjectionError(
                "selected context candidates do not match selection decisions"
            )
        ingress_message_ids = {turn.message_id for turn in ingress.turns}
        if any(
            candidate.source_id not in ingress_message_ids
            for candidate in selected_candidates
            if candidate.kind == ContextCandidateKind.DIALOG
            and candidate.source_id is not None
        ):
            raise DerivedContextProjectionError(
                "selected dialog candidate is not sourced from the active ingress"
            )
        required_constraint_ids = {
            str(item.get("candidate_id"))
            for item in decisions
            if item.get("kind") == ContextCandidateKind.CONSTRAINT.value
            and str(item.get("candidate_id", "")).startswith("session_constraints:")
        }
        if not required_constraint_ids.issubset(selected_ids):
            raise DerivedContextProjectionError(
                "active session constraint was omitted from ContextLoader selection"
            )
        bindings: dict[str, ContextCompactionBinding] = {}
        for raw_binding in list(context_result.get("context_compactions") or []):
            try:
                binding = ContextCompactionBinding.model_validate(raw_binding)
            except Exception as exc:
                raise DerivedContextProjectionError(
                    "context compaction binding is invalid"
                ) from exc
            bindings[binding.record.compaction_id] = binding
        dialog_candidates = [
            candidate
            for candidate in selected_candidates
            if candidate.kind == ContextCandidateKind.DIALOG
        ]
        artifact_candidates = []
        for candidate in selected_candidates:
            if candidate.kind != ContextCandidateKind.ARTIFACT:
                continue
            if candidate.candidate_id.startswith("compaction:"):
                compaction_id = candidate.candidate_id.split(":", 1)[1]
                binding = bindings.get(compaction_id)
                if binding is None or binding.record.summary != candidate.content:
                    raise DerivedContextProjectionError(
                        "selected compaction artifact has no matching binding"
                    )
                if binding.record.source_fingerprint != candidate.source_id:
                    raise DerivedContextProjectionError(
                        "selected compaction artifact source fingerprint mismatch"
                    )
            artifact_candidates.append(
                candidate.model_copy(update={"compacted_candidate_ids": []})
            )
    else:
        # Compatibility with snapshots written before the selected-candidate
        # projection was added.  dialog_context is already selected-only.
        dialog_candidates = [
            _candidate_dialog(item, index)
            for index, item in enumerate(list(context_result.get("dialog_context") or []), start=1)
            if isinstance(item, Mapping)
        ]
        artifact_candidates = [
            _candidate_artifact(item, 500 + index)
            for index, item in enumerate(list(context_result.get("context_compactions") or []), start=1)
            if isinstance(item, Mapping)
        ]
    projection_hash = _hash(
        {
            "context_request_hash": request_hash,
            "session_turn_source_hash": expected_turn_hash,
            "session_constraints_hash": ingress.session_constraints.canonical_hash,
            "dialog": [item.model_dump(mode="json") for item in dialog_candidates],
            "artifacts": [item.model_dump(mode="json") for item in artifact_candidates],
        }
    )
    return DerivedContextProjection(
        context_request_hash=request_hash,
        session_turn_source_hash=expected_turn_hash,
        session_constraints_hash=ingress.session_constraints.canonical_hash,
        dialog_candidates=dialog_candidates,
        artifact_candidates=artifact_candidates,
        projection_hash=projection_hash,
    )


__all__ = [
    "DerivedContextProjection",
    "DerivedContextProjectionError",
    "build_derived_context_projection",
]
