from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from memory.compaction_reuse import (
    ReusableCompactionArtifactCandidate,
    admit_reusable_compaction_candidate,
    build_checkpoint_compaction_reuse_shadow_provider,
    build_compaction_reuse_shadow_provider,
    source_binding_hash_from_shadow_payload,
)
from memory.compaction_summary import source_candidate_binding_hash
from metadata import (
    ContextCompactionBinding,
    ContextCompactionRecord,
    DurableArtifactReference,
)


def _record(summary: str = "secret summary body") -> ContextCompactionRecord:
    return ContextCompactionRecord(
        compaction_id="compact-1",
        source_fingerprint="sha256:" + "1" * 64,
        source_candidate_ids=["dialog-1", "dialog-2"],
        algorithm="deterministic_observation_mask_v1",
        summary=summary,
        original_chars=200,
        compacted_chars=len(summary),
    )


def _binding(*, source_binding_hash: str = "") -> ContextCompactionBinding:
    return ContextCompactionBinding(
        record=_record(),
        artifact=DurableArtifactReference(
            artifact_id="artifact-1",
            kind="context_compaction",
            integrity_checksum="sha256:" + "2" * 64,
            bytes=120,
        ),
        source_binding_hash=source_binding_hash,
    )


def _payload() -> dict[str, object]:
    sources = [
        ("dialog-1", "turn-1", 1, "dialog one"),
        ("dialog-2", "turn-2", 2, "dialog two"),
    ]
    digests = [
        {
            "candidate_id": candidate_id,
            "kind": "dialog",
            "source_id": source_id,
            "role": "assistant",
            "retention": "preferred",
            "trust": "direct",
            "freshness": "current",
            "truncation": "head",
            "source_order": source_order,
            "content_sha256": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
        }
        for candidate_id, source_id, source_order, content in sources
    ]
    return {
        "candidate_digests": digests,
        "selected_candidate_ids": ["dialog-1", "dialog-2", "required-1"],
        "session_constraints_hash": "sha256:" + "5" * 64,
        "source_fingerprint_by_candidate_ids": {
            json.dumps(["dialog-1", "dialog-2"], separators=(",", ":")): "sha256:"
            + "1" * 64
        },
    }


def _candidate(**updates) -> ReusableCompactionArtifactCandidate:
    payload = _payload()
    values = {
        "candidate_id": "reuse-candidate-1",
        "artifact_id": "artifact-1",
        "artifact_kind": "context_compaction",
        "artifact_integrity_checksum": "sha256:" + "2" * 64,
        "record_compaction_id": "compact-1",
        "record_algorithm": "deterministic_observation_mask_v1",
        "source_candidate_ids": ["dialog-1", "dialog-2"],
        "source_fingerprint": "sha256:" + "1" * 64,
        "source_binding_hash": source_binding_hash_from_shadow_payload(
            payload, ["dialog-1", "dialog-2"]
        ),
        "required_candidate_ids": ["required-1"],
        "recent_suffix_ids": ["dialog-2"],
        "session_constraints_hash": "sha256:" + "5" * 64,
        "generated_summary_fingerprint": "sha256:" + "7" * 64,
    }
    values.update(updates)
    return ReusableCompactionArtifactCandidate(**values)


def test_source_binding_hash_matches_body_free_shadow_shape() -> None:
    candidates = [
        SimpleNamespace(
            candidate_id="dialog-1",
            kind="dialog",
            source_id="turn-1",
            role="assistant",
            retention="preferred",
            trust="direct",
            freshness="current",
            truncation="head",
            source_order=1,
            content="dialog one",
        ),
        SimpleNamespace(
            candidate_id="dialog-2",
            kind="dialog",
            source_id="turn-2",
            role="assistant",
            retention="preferred",
            trust="direct",
            freshness="current",
            truncation="head",
            source_order=2,
            content="dialog two",
        ),
    ]
    payload = _payload()
    assert source_candidate_binding_hash(candidates) == source_binding_hash_from_shadow_payload(
        payload, ["dialog-1", "dialog-2"]
    )


def test_candidate_from_binding_is_body_free() -> None:
    candidate = ReusableCompactionArtifactCandidate.from_binding(
        _binding(source_binding_hash=_candidate().source_binding_hash),
        required_candidate_ids=["required-1"],
        recent_suffix_ids=["dialog-2"],
        session_constraints_hash="sha256:" + "5" * 64,
    )
    encoded = candidate.model_dump_json()
    assert "secret summary body" not in encoded
    assert "summary" not in json.dumps(candidate.model_dump(mode="json")).replace(
        "generated_summary_fingerprint", ""
    )


def test_matching_candidate_is_admitted_but_never_prompt_authority() -> None:
    admission = admit_reusable_compaction_candidate(_candidate(), _payload())
    assert admission.status == "admitted"
    assert admission.rejection_reason is None
    assert admission.used_in_prompt is False


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        ({"source_candidate_ids": ["dialog-1", "missing"]}, "source_candidate_ids_mismatch"),
        ({"source_binding_hash": "sha256:" + "8" * 64}, "source_binding_hash_mismatch"),
        ({"source_fingerprint": "sha256:" + "9" * 64}, "source_fingerprint_mismatch"),
        ({"required_candidate_ids": ["missing-required"]}, "required_candidate_ids_mismatch"),
        ({"recent_suffix_ids": ["missing-recent"]}, "recent_suffix_ids_mismatch"),
        ({"session_constraints_hash": None}, "session_constraints_hash_mismatch"),
        ({"artifact_kind": "wrong_kind"}, "artifact_kind_mismatch"),
        ({"expected_artifact_integrity_checksum": "sha256:" + "9" * 64}, "artifact_integrity_mismatch"),
    ],
)
def test_candidate_rejects_drift(update, reason) -> None:
    admission = admit_reusable_compaction_candidate(_candidate(**update), _payload())
    assert admission.status == "rejected"
    assert admission.rejection_reason == reason
    assert admission.used_in_prompt is False


def test_unknown_algorithm_is_rejected_at_candidate_boundary() -> None:
    with pytest.raises(ValueError, match="record algorithm is not supported"):
        _candidate(record_algorithm="unsupported_compaction_v9")


def test_shadow_provider_is_bounded_and_checkpoint_missing_hash_fails_closed() -> None:
    provider = build_compaction_reuse_shadow_provider([_candidate()])
    admissions = provider(_payload())
    assert len(admissions) == 1
    assert admissions[0].admission_id == "reuse:1:reuse-candidate-1"
    assert admissions[0].used_in_prompt is False

    checkpoint_provider = build_checkpoint_compaction_reuse_shadow_provider(
        [_binding()],
        session_constraints_hash="sha256:" + "5" * 64,
    )
    checkpoint_admissions = checkpoint_provider(_payload())
    assert len(checkpoint_admissions) == 1
    assert checkpoint_admissions[0].status == "rejected"
    assert checkpoint_admissions[0].rejection_reason == "artifact_contract_invalid"
    assert checkpoint_admissions[0].source_binding_hash == "sha256:" + "0" * 64
    assert checkpoint_admissions[0].used_in_prompt is False
