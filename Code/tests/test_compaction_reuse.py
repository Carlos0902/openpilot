from __future__ import annotations

import json
import hashlib
from types import SimpleNamespace

import pytest

from memory.compaction_summary import source_candidate_binding_hash
from memory.compaction_reuse import (
    ReusableCompactionArtifactCandidate,
    ReusableCompactionSemanticFact,
    admit_reusable_compaction_candidate,
    build_checkpoint_compaction_reuse_shadow_provider,
    build_compaction_reuse_shadow_provider,
    preflight_reusable_compaction_prompt_use,
    simulate_reusable_compaction_prompt_use,
    source_binding_hash_from_shadow_payload,
)
from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.short_memory import ShortMemory
from metadata import (
    ContextAssemblyPolicy,
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ContextCompactionBinding,
    ContextCompactionRecord,
    ContextCompactionReuseAdmission,
    ContextCompactionReuseAdmissionStatus,
    ContextCompactionReuseRejectionReason,
    ContextSelectionMetadata,
    DurableArtifactReference,
    RuntimePromptContextSnapshot,
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


def _binding(
    summary: str = "secret summary body",
    *,
    source_binding_hash: str = "",
) -> ContextCompactionBinding:
    return ContextCompactionBinding(
        record=_record(summary),
        artifact=DurableArtifactReference(
            artifact_id="artifact-1",
            kind="context_compaction",
            integrity_checksum="sha256:" + "2" * 64,
            bytes=120,
        ),
        source_binding_hash=source_binding_hash,
    )


def _snapshot(binding: ContextCompactionBinding | None = None) -> RuntimePromptContextSnapshot:
    return RuntimePromptContextSnapshot(
        context_id="context-1",
        request_hash="sha256:" + "3" * 64,
        prompt_hash="sha256:" + "6" * 64,
        selection=ContextSelectionMetadata(
            max_prompt_chars=1000,
            original_prompt_chars=120,
            final_prompt_chars=120,
        ),
        context_artifact=DurableArtifactReference(
            artifact_id="prompt-context-1",
            kind="prompt_context",
            integrity_checksum="sha256:" + "d" * 64,
            bytes=120,
        ),
        compaction_bindings=[binding or _binding()],
    )


def _shadow_payload() -> dict:
    payload = {
        "schema": "memory-context-compaction-reuse-shadow-v1",
        "context_request_hash": "sha256:" + "3" * 64,
        "session_turn_source_hash": "sha256:" + "4" * 64,
        "session_constraints_hash": "sha256:" + "5" * 64,
        "prompt_hash": "sha256:" + "6" * 64,
        "candidate_digests": [
            {
                "candidate_id": "dialog-1",
                "kind": "dialog",
                "source_id": "turn-1",
                "role": "assistant",
                "retention": "preferred",
                "trust": "direct",
                "freshness": "current",
                "truncation": "head",
                "source_order": 1,
                "content_sha256": "sha256:" + "a" * 64,
            },
            {
                "candidate_id": "dialog-2",
                "kind": "dialog",
                "source_id": "turn-2",
                "role": "assistant",
                "retention": "required",
                "trust": "direct",
                "freshness": "current",
                "truncation": "head",
                "source_order": 2,
                "content_sha256": "sha256:" + "b" * 64,
            },
            {
                "candidate_id": "required-1",
                "kind": "instruction",
                "source_id": "system",
                "role": "system",
                "retention": "required",
                "trust": "authoritative",
                "freshness": "current",
                "truncation": "forbidden",
                "source_order": 0,
                "content_sha256": "sha256:" + "c" * 64,
            },
        ],
        "selected_candidate_ids": ["dialog-1", "dialog-2", "required-1"],
        "source_fingerprint_by_candidate_ids": {
            json.dumps(["dialog-1", "dialog-2"], separators=(",", ":")): "sha256:"
            + "1" * 64,
        },
    }
    return payload


def _candidate(**updates) -> ReusableCompactionArtifactCandidate:
    payload = _shadow_payload()
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
            payload,
            ["dialog-1", "dialog-2"],
        ),
        "required_candidate_ids": ["required-1"],
        "recent_suffix_ids": ["dialog-2"],
        "session_constraints_hash": "sha256:" + "5" * 64,
        "generated_summary_fingerprint": "sha256:" + "7" * 64,
    }
    values.update(updates)
    return ReusableCompactionArtifactCandidate(**values)


def _preflight_fixture():
    required = ContextCandidate(
        candidate_id="required-1",
        kind=ContextCandidateKind.INSTRUCTION,
        source_id="system",
        content="Must preserve calculator API.",
        role="system",
        retention=ContextCandidateRetention.REQUIRED,
        priority=100,
        source_order=0,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.AUTHORITATIVE,
        freshness=ContextCandidateFreshness.CURRENT,
    )
    source_1 = ContextCandidate(
        candidate_id="dialog-1",
        kind=ContextCandidateKind.DIALOG,
        source_id="turn-1",
        content=(
            "Alpha decision: keep divide behaviour stable while preserving "
            "existing API compatibility and avoiding README changes."
        ),
        role="assistant",
        retention=ContextCandidateRetention.PREFERRED,
        priority=40,
        source_order=10,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.HISTORICAL,
    )
    source_2 = ContextCandidate(
        candidate_id="dialog-2",
        kind=ContextCandidateKind.DIALOG,
        source_id="turn-2",
        content=(
            "Beta validation: run pytest after edits and treat missing validation "
            "as incomplete execution evidence."
        ),
        role="assistant",
        retention=ContextCandidateRetention.PREFERRED,
        priority=40,
        source_order=11,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.HISTORICAL,
    )
    recent = ContextCandidate(
        candidate_id="recent-1",
        kind=ContextCandidateKind.DIALOG,
        source_id="turn-3",
        content="Recent suffix must remain verbatim.",
        role="assistant",
        retention=ContextCandidateRetention.PREFERRED,
        priority=80,
        source_order=20,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.CURRENT,
    )
    sources = [source_1, source_2]
    binding_hash = source_candidate_binding_hash(sources)
    summary = (
        "Reusable summary: Alpha decision keep divide behaviour stable. "
        "Beta validation run pytest after edits."
    )
    record = ContextCompactionRecord(
        compaction_id="preflight-compact-1",
        source_fingerprint="sha256:" + "a" * 64,
        source_candidate_ids=[source.candidate_id for source in sources],
        algorithm="deterministic_observation_mask_v1",
        summary=summary,
        original_chars=sum(len(source.content) for source in sources),
        compacted_chars=len(summary),
    )
    binding = ContextCompactionBinding(
        record=record,
        artifact=DurableArtifactReference(
            artifact_id="preflight-artifact-1",
            kind="context_compaction",
            integrity_checksum="sha256:" + "b" * 64,
            bytes=200,
        ),
        source_binding_hash=binding_hash,
    )
    admission = ContextCompactionReuseAdmission(
        admission_id="preflight-admission-1",
        status=ContextCompactionReuseAdmissionStatus.ADMITTED,
        source_candidate_ids=list(record.source_candidate_ids),
        source_fingerprint=record.source_fingerprint,
        source_binding_hash=binding_hash,
        recent_suffix_ids=["recent-1"],
        artifact_id=binding.artifact.artifact_id,
        artifact_kind=binding.artifact.kind,
        artifact_integrity_checksum=binding.artifact.integrity_checksum,
        generated_summary_fingerprint="sha256:"
        + hashlib.sha256(record.summary.encode("utf-8")).hexdigest(),
        used_in_prompt=False,
    )
    facts = [
        ReusableCompactionSemanticFact(
            fact_id="fact-alpha",
            text="Alpha decision keep divide behaviour stable",
            evidence_candidate_ids=["dialog-1"],
        ),
        ReusableCompactionSemanticFact(
            fact_id="fact-beta",
            text="Beta validation run pytest after edits",
            evidence_candidate_ids=["dialog-2"],
        ),
    ]
    policy = ContextAssemblyPolicy(max_prompt_chars=1000)

    def renderer(candidates: list[ContextCandidate]) -> str:
        return "\n".join(f"[{candidate.candidate_id}] {candidate.content}" for candidate in candidates)

    return {
        "candidates": [required, source_1, source_2, recent],
        "binding": binding,
        "admission": admission,
        "facts": facts,
        "policy": policy,
        "renderer": renderer,
    }


class _WhitespaceTokenCounter:
    available = True
    tokenizer_id = "test-whitespace-v1"
    model = "test-model"

    def count_text(self, text: str) -> int:
        return len(str(text).split())


def test_source_candidate_binding_hash_matches_shadow_payload_shape() -> None:
    sources = [
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
            retention="required",
            trust="direct",
            freshness="current",
            truncation="head",
            source_order=2,
            content="dialog two",
        ),
    ]
    payload = {
        "candidate_digests": [
            {
                "candidate_id": item.candidate_id,
                "kind": item.kind,
                "source_id": item.source_id,
                "role": item.role,
                "retention": item.retention,
                "trust": item.trust,
                "freshness": item.freshness,
                "truncation": item.truncation,
                "source_order": item.source_order,
                "content_sha256": "sha256:"
                + hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
            }
            for item in sources
        ],
    }

    assert source_candidate_binding_hash(sources) == source_binding_hash_from_shadow_payload(
        payload,
        ["dialog-1", "dialog-2"],
    )


def test_reusable_compaction_candidate_from_binding_is_body_free() -> None:
    payload = _shadow_payload()
    source_binding_hash = source_binding_hash_from_shadow_payload(
        payload,
        ["dialog-1", "dialog-2"],
    )
    candidate = ReusableCompactionArtifactCandidate.from_binding(
        _binding("secret summary body"),
        source_binding_hash=source_binding_hash,
        required_candidate_ids=["required-1"],
        recent_suffix_ids=["dialog-2"],
        session_constraints_hash="sha256:" + "5" * 64,
    )

    encoded = candidate.model_dump_json()
    assert candidate.generated_summary_fingerprint is not None
    assert "secret summary body" not in encoded
    assert "summary" not in json.dumps(candidate.model_dump(mode="json")).replace(
        "generated_summary_fingerprint",
        "",
    )


def test_reusable_compaction_candidate_uses_persisted_binding_hash() -> None:
    payload = _shadow_payload()
    source_binding_hash = source_binding_hash_from_shadow_payload(
        payload,
        ["dialog-1", "dialog-2"],
    )
    candidate = ReusableCompactionArtifactCandidate.from_binding(
        _binding("secret summary body", source_binding_hash=source_binding_hash),
        required_candidate_ids=["required-1"],
        recent_suffix_ids=["dialog-2"],
        session_constraints_hash="sha256:" + "5" * 64,
    )

    admission = admit_reusable_compaction_candidate(candidate, payload)

    assert admission.status == "admitted"
    assert admission.source_binding_hash == source_binding_hash


def test_reusable_compaction_candidate_admits_matching_shadow_payload() -> None:
    admission = admit_reusable_compaction_candidate(
        _candidate(),
        _shadow_payload(),
    )

    assert admission.status == "admitted"
    assert admission.rejection_reason is None
    assert admission.used_in_prompt is False


@pytest.mark.parametrize(
    ("update", "payload_update", "reason"),
    [
        (
            {"source_candidate_ids": ["dialog-1", "missing-dialog"]},
            {},
            "source_candidate_ids_mismatch",
        ),
        (
            {"source_binding_hash": "sha256:" + "8" * 64},
            {},
            "source_binding_hash_mismatch",
        ),
        (
            {"source_fingerprint": "sha256:" + "9" * 64},
            {},
            "source_fingerprint_mismatch",
        ),
        (
            {"required_candidate_ids": ["missing-required"]},
            {},
            "required_candidate_ids_mismatch",
        ),
        (
            {"recent_suffix_ids": ["missing-recent"]},
            {},
            "recent_suffix_ids_mismatch",
        ),
        (
            {"session_constraints_hash": "sha256:" + "9" * 64},
            {},
            "session_constraints_hash_mismatch",
        ),
        (
            {"session_constraints_hash": None},
            {},
            "session_constraints_hash_mismatch",
        ),
        (
            {"artifact_kind": "wrong_kind"},
            {},
            "artifact_kind_mismatch",
        ),
        (
            {"expected_artifact_integrity_checksum": "sha256:" + "9" * 64},
            {},
            "artifact_integrity_mismatch",
        ),
    ],
)
def test_reusable_compaction_candidate_rejects_drift(update, payload_update, reason) -> None:
    payload = _shadow_payload()
    payload.update(payload_update)
    admission = admit_reusable_compaction_candidate(
        _candidate(**update),
        payload,
    )

    assert admission.status == "rejected"
    assert admission.rejection_reason == reason
    assert admission.used_in_prompt is False


def test_reusable_compaction_candidate_rejects_unknown_record_algorithm() -> None:
    with pytest.raises(ValueError, match="record algorithm is not supported"):
        _candidate(record_algorithm="unsupported_compaction_v9")


def test_reusable_compaction_shadow_provider_is_builder_callable() -> None:
    provider = build_compaction_reuse_shadow_provider([_candidate()])

    admissions = provider(_shadow_payload())

    assert len(admissions) == 1
    assert admissions[0].status == "admitted"
    assert admissions[0].admission_id == "reuse:1:reuse-candidate-1"


def test_reusable_compaction_shadow_provider_integrates_with_memory_context_builder(
    tmp_path,
) -> None:
    short_memory = ShortMemory(repo_path=tmp_path / "short")
    for index in range(4):
        short_memory.add_message("assistant", f"dialog body {index}")
    baseline = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "baseline-memory"),
        max_prompt_chars=1000,
    ).build(
        "builder adapter",
        include_environment=False,
        limit=4,
        system_prompt="Keep context stable.",
    )

    def provider(payload):
        source_ids = [
            item["candidate_id"]
            for item in payload["candidate_digests"]
            if item["kind"] == "dialog"
        ][:2]
        source_binding_hash = source_binding_hash_from_shadow_payload(payload, source_ids)
        source_fingerprint = payload["source_fingerprint_by_candidate_ids"][
            json.dumps(source_ids, separators=(",", ":"))
        ]
        candidate = ReusableCompactionArtifactCandidate(
            candidate_id="builder-reuse",
            artifact_id="artifact-1",
            artifact_kind="context_compaction",
            artifact_integrity_checksum="sha256:" + "2" * 64,
            record_compaction_id="compact-1",
            record_algorithm="deterministic_observation_mask_v1",
            source_candidate_ids=source_ids,
            source_fingerprint=source_fingerprint,
            source_binding_hash=source_binding_hash,
            recent_suffix_ids=source_ids[-1:],
            generated_summary_fingerprint="sha256:" + "7" * 64,
        )
        return build_compaction_reuse_shadow_provider([candidate])(payload)

    shadow = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "shadow-memory"),
        max_prompt_chars=1000,
        compaction_reuse_shadow_provider=provider,
    ).build(
        "builder adapter",
        include_environment=False,
        limit=4,
        system_prompt="Keep context stable.",
    )

    assert shadow["context_request_hash"] == baseline["context_request_hash"]
    assert shadow["prompt_text"] == baseline["prompt_text"]
    assert shadow["selected_context_candidates"] == baseline["selected_context_candidates"]
    admissions = shadow["context_selection"]["compaction_reuse_admissions"]
    assert len(admissions) == 1
    assert admissions[0]["status"] == "admitted"
    assert admissions[0]["used_in_prompt"] is False


def test_checkpoint_compaction_reuse_provider_admits_matching_snapshot_binding() -> None:
    payload = _shadow_payload()
    source_binding_hash = source_binding_hash_from_shadow_payload(
        payload,
        ["dialog-1", "dialog-2"],
    )
    binding = _binding("secret summary body", source_binding_hash=source_binding_hash)
    provider = build_checkpoint_compaction_reuse_shadow_provider(
        _snapshot(binding),
        required_candidate_ids_by_compaction_id={
            binding.record.compaction_id: ["required-1"]
        },
        recent_suffix_ids_by_compaction_id={binding.record.compaction_id: ["dialog-2"]},
        session_constraints_hash="sha256:" + "5" * 64,
    )

    admissions = provider(payload)

    assert len(admissions) == 1
    assert admissions[0].status == "admitted"
    assert admissions[0].rejection_reason is None
    assert admissions[0].admission_id == "checkpoint-reuse:1:compact-1"
    assert admissions[0].used_in_prompt is False
    encoded = admissions[0].model_dump_json()
    assert "secret summary body" not in encoded


def test_checkpoint_compaction_reuse_provider_rejects_missing_source_hash() -> None:
    binding = _binding("secret summary body")
    provider = build_checkpoint_compaction_reuse_shadow_provider(
        _snapshot(binding),
        source_binding_hashes={},
        required_candidate_ids_by_compaction_id={
            binding.record.compaction_id: ["required-1"]
        },
        recent_suffix_ids_by_compaction_id={binding.record.compaction_id: ["dialog-2"]},
    )

    admissions = provider(_shadow_payload())

    assert len(admissions) == 1
    assert admissions[0].status == "rejected"
    assert admissions[0].rejection_reason == "artifact_contract_invalid"
    assert admissions[0].source_binding_hash == "sha256:" + "0" * 64
    assert admissions[0].used_in_prompt is False
    encoded = admissions[0].model_dump_json()
    assert "secret summary body" not in encoded


def test_checkpoint_compaction_reuse_provider_admits_historical_binding_with_compat_hash() -> None:
    binding = _binding("secret summary body")
    payload = _shadow_payload()
    source_binding_hash = source_binding_hash_from_shadow_payload(
        payload,
        binding.record.source_candidate_ids,
    )
    provider = build_checkpoint_compaction_reuse_shadow_provider(
        _snapshot(binding),
        source_binding_hashes={binding.record.compaction_id: source_binding_hash},
        session_constraints_hash="sha256:" + "5" * 64,
    )

    admissions = provider(payload)

    assert len(admissions) == 1
    assert admissions[0].status == "admitted"
    assert admissions[0].source_binding_hash == source_binding_hash
    assert admissions[0].used_in_prompt is False


def test_checkpoint_compaction_reuse_provider_rejects_conflicting_compat_hash() -> None:
    binding = _binding(
        "secret summary body",
        source_binding_hash=source_binding_hash_from_shadow_payload(
            _shadow_payload(),
            ["dialog-1", "dialog-2"],
        ),
    )
    provider = build_checkpoint_compaction_reuse_shadow_provider(
        _snapshot(binding),
        source_binding_hashes={binding.record.compaction_id: "sha256:" + "8" * 64},
    )

    admissions = provider(_shadow_payload())

    assert len(admissions) == 1
    assert admissions[0].status == "rejected"
    assert admissions[0].rejection_reason == "artifact_contract_invalid"
    assert admissions[0].source_binding_hash == binding.source_binding_hash
    assert admissions[0].used_in_prompt is False


def test_checkpoint_compaction_reuse_provider_rejects_stale_source_hash() -> None:
    binding = _binding()
    provider = build_checkpoint_compaction_reuse_shadow_provider(
        [binding],
        source_binding_hashes={binding.record.compaction_id: "sha256:" + "8" * 64},
    )

    admissions = provider(_shadow_payload())

    assert len(admissions) == 1
    assert admissions[0].status == "rejected"
    assert admissions[0].rejection_reason == "source_binding_hash_mismatch"
    assert admissions[0].used_in_prompt is False


def test_checkpoint_compaction_reuse_provider_rejects_artifact_checksum_drift() -> None:
    binding = _binding()
    payload = _shadow_payload()
    source_binding_hash = source_binding_hash_from_shadow_payload(
        payload,
        binding.record.source_candidate_ids,
    )
    provider = build_checkpoint_compaction_reuse_shadow_provider(
        {"compaction_bindings": [binding.model_dump(mode="json")]},
        source_binding_hashes={binding.record.compaction_id: source_binding_hash},
        expected_artifact_integrity_checksums={
            binding.artifact.artifact_id: "sha256:" + "9" * 64
        },
    )

    admissions = provider(payload)

    assert len(admissions) == 1
    assert admissions[0].status == "rejected"
    assert admissions[0].rejection_reason == "artifact_integrity_mismatch"
    assert admissions[0].used_in_prompt is False


def test_checkpoint_compaction_reuse_provider_reports_malformed_artifact_checksum() -> None:
    payload = _shadow_payload()
    binding = _binding(
        "secret summary body",
        source_binding_hash=source_binding_hash_from_shadow_payload(
            payload,
            ["dialog-1", "dialog-2"],
        ),
    )
    malformed_artifact = binding.artifact.model_copy(
        update={"integrity_checksum": "not-a-sha256"}
    )
    malformed_binding = binding.model_copy(update={"artifact": malformed_artifact})
    provider = build_checkpoint_compaction_reuse_shadow_provider([malformed_binding])

    admissions = provider(payload)

    assert len(admissions) == 1
    assert admissions[0].status == "rejected"
    assert admissions[0].rejection_reason == "artifact_contract_invalid"
    assert admissions[0].artifact_integrity_checksum == "sha256:" + "0" * 64
    assert admissions[0].used_in_prompt is False


def test_prompt_use_preflight_passes_with_admitted_binding_and_semantic_facts() -> None:
    fixture = _preflight_fixture()

    result = preflight_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=fixture["candidates"],
        policy=fixture["policy"],
        renderer=fixture["renderer"],
        admission=fixture["admission"],
        semantic_facts=fixture["facts"],
        required_candidate_ids=["required-1"],
        recent_suffix_ids=["recent-1"],
        expected_artifact_integrity_checksum="sha256:" + "b" * 64,
    )

    assert result.status == "passed"
    assert result.rejection_reasons == []
    assert set(result.trial_replaced_source_candidate_ids) == {"dialog-1", "dialog-2"}
    assert "required-1" in result.trial_selected_candidate_ids
    assert "recent-1" in result.trial_selected_candidate_ids
    assert result.used_in_prompt is False
    encoded = result.model_dump_json()
    assert "Reusable summary" not in encoded
    assert "Alpha decision" not in encoded
    assert "Recent suffix" not in encoded


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda fixture: fixture.__setitem__(
                "admission",
                fixture["admission"].model_copy(
                    update={
                        "status": ContextCompactionReuseAdmissionStatus.REJECTED,
                        "rejection_reason": (
                            ContextCompactionReuseRejectionReason.SOURCE_BINDING_HASH_MISMATCH
                        ),
                    }
                ),
            ),
            "admission_not_admitted",
        ),
        (
            lambda fixture: fixture.__setitem__(
                "candidates",
                [
                    candidate.model_copy(update={"content": "changed source"})
                    if candidate.candidate_id == "dialog-1"
                    else candidate
                    for candidate in fixture["candidates"]
                ],
            ),
            "source_binding_hash_mismatch",
        ),
        (
            lambda fixture: fixture.__setitem__(
                "facts",
                [
                    ReusableCompactionSemanticFact(
                        fact_id="missing-fact",
                        text="Gamma requirement absent from summary",
                        evidence_candidate_ids=["dialog-1"],
                    )
                ],
            ),
            "semantic_fact_missing",
        ),
        (
            lambda fixture: fixture.__setitem__(
                "facts",
                [
                    ReusableCompactionSemanticFact(
                        fact_id="bad-evidence",
                        text="Alpha decision keep divide behaviour stable",
                        evidence_candidate_ids=["recent-1"],
                    )
                ],
            ),
            "semantic_evidence_mismatch",
        ),
        (
            lambda fixture: fixture.__setitem__("required_ids", ["missing-required"]),
            "required_candidate_omitted",
        ),
        (
            lambda fixture: fixture.__setitem__("recent_ids", ["dialog-2"]),
            "recent_suffix_omitted",
        ),
        (
            lambda fixture: fixture.__setitem__(
                "policy",
                ContextAssemblyPolicy(max_prompt_chars=80),
            ),
            "trial_summary_not_selected",
        ),
    ],
)
def test_prompt_use_preflight_rejects_unsafe_cases(mutate, reason) -> None:
    fixture = _preflight_fixture()
    fixture["required_ids"] = ["required-1"]
    fixture["recent_ids"] = ["recent-1"]
    mutate(fixture)

    result = preflight_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=fixture["candidates"],
        policy=fixture["policy"],
        renderer=fixture["renderer"],
        admission=fixture["admission"],
        semantic_facts=fixture["facts"],
        required_candidate_ids=fixture["required_ids"],
        recent_suffix_ids=fixture["recent_ids"],
    )

    assert result.status == "rejected"
    assert reason in result.rejection_reasons
    assert result.used_in_prompt is False


def test_prompt_use_simulation_passes_with_body_free_char_reduction() -> None:
    fixture = _preflight_fixture()
    preflight = preflight_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=fixture["candidates"],
        policy=fixture["policy"],
        renderer=fixture["renderer"],
        admission=fixture["admission"],
        semantic_facts=fixture["facts"],
        required_candidate_ids=["required-1"],
        recent_suffix_ids=["recent-1"],
    )

    result = simulate_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=fixture["candidates"],
        policy=fixture["policy"],
        renderer=fixture["renderer"],
        preflight=preflight,
    )

    assert result.status == "passed"
    assert result.rejection_reasons == []
    assert result.raw_assembly_status == "ready"
    assert result.reusable_assembly_status == "ready"
    assert result.raw_final_prompt_chars is not None
    assert result.reusable_final_prompt_chars is not None
    assert result.raw_final_prompt_chars > result.reusable_final_prompt_chars
    assert result.prompt_char_delta == (
        result.raw_final_prompt_chars - result.reusable_final_prompt_chars
    )
    assert set(result.replaced_source_candidate_ids) == {"dialog-1", "dialog-2"}
    assert result.retained_required_candidate_ids == ["required-1"]
    assert result.retained_recent_suffix_ids == ["recent-1"]
    assert result.used_in_prompt is False
    encoded = result.model_dump_json()
    assert "Reusable summary" not in encoded
    assert "Alpha decision" not in encoded
    assert "Recent suffix" not in encoded


def test_prompt_use_simulation_records_token_accounting_when_requested() -> None:
    fixture = _preflight_fixture()
    counter = _WhitespaceTokenCounter()
    policy = ContextAssemblyPolicy(max_prompt_chars=1000, max_prompt_tokens=300)
    preflight = preflight_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=fixture["candidates"],
        policy=policy,
        renderer=fixture["renderer"],
        admission=fixture["admission"],
        semantic_facts=fixture["facts"],
        required_candidate_ids=["required-1"],
        recent_suffix_ids=["recent-1"],
        token_counter=counter,
    )

    result = simulate_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=fixture["candidates"],
        policy=policy,
        renderer=fixture["renderer"],
        preflight=preflight,
        token_counter=counter,
    )

    assert result.status == "passed"
    assert result.raw_final_prompt_tokens is not None
    assert result.reusable_final_prompt_tokens is not None
    assert result.raw_final_prompt_tokens > result.reusable_final_prompt_tokens
    assert result.prompt_token_delta == (
        result.raw_final_prompt_tokens - result.reusable_final_prompt_tokens
    )
    assert result.token_count_method == "provider_tokenizer"
    assert result.tokenizer_id == "test-whitespace-v1"
    assert result.token_model == "test-model"
    encoded = result.model_dump_json()
    assert "Reusable summary" not in encoded
    assert "Alpha decision" not in encoded


def test_prompt_use_simulation_rejects_non_passed_preflight_without_prompt_hashes() -> None:
    fixture = _preflight_fixture()
    rejected_preflight = preflight_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=fixture["candidates"],
        policy=ContextAssemblyPolicy(max_prompt_chars=80),
        renderer=fixture["renderer"],
        admission=fixture["admission"],
        semantic_facts=fixture["facts"],
        required_candidate_ids=["required-1"],
        recent_suffix_ids=["recent-1"],
    )

    result = simulate_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=fixture["candidates"],
        policy=fixture["policy"],
        renderer=fixture["renderer"],
        preflight=rejected_preflight,
    )

    assert result.status == "rejected"
    assert "preflight_not_passed" in result.rejection_reasons
    assert result.raw_prompt_hash is None
    assert result.reusable_prompt_hash is None
    assert result.used_in_prompt is False


def test_prompt_use_simulation_rejects_source_drift_after_preflight() -> None:
    fixture = _preflight_fixture()
    preflight = preflight_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=fixture["candidates"],
        policy=fixture["policy"],
        renderer=fixture["renderer"],
        admission=fixture["admission"],
        semantic_facts=fixture["facts"],
        required_candidate_ids=["required-1"],
        recent_suffix_ids=["recent-1"],
    )
    drifted = [
        candidate.model_copy(update={"content": "changed source"})
        if candidate.candidate_id == "dialog-1"
        else candidate
        for candidate in fixture["candidates"]
    ]

    result = simulate_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=drifted,
        policy=fixture["policy"],
        renderer=fixture["renderer"],
        preflight=preflight,
    )

    assert result.status == "rejected"
    assert "source_binding_hash_mismatch" in result.rejection_reasons
    assert result.raw_prompt_hash is None
    assert result.reusable_prompt_hash is None


def test_prompt_use_simulation_rejects_when_summary_falls_back_to_sources() -> None:
    fixture = _preflight_fixture()
    preflight = preflight_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=fixture["candidates"],
        policy=fixture["policy"],
        renderer=fixture["renderer"],
        admission=fixture["admission"],
        semantic_facts=fixture["facts"],
        required_candidate_ids=["required-1"],
        recent_suffix_ids=["recent-1"],
    )

    result = simulate_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=fixture["candidates"],
        policy=ContextAssemblyPolicy(max_prompt_chars=80),
        renderer=fixture["renderer"],
        preflight=preflight,
    )

    assert result.status == "rejected"
    assert "summary_not_selected" in result.rejection_reasons
    assert "source_replacement_mismatch" in result.rejection_reasons
    assert result.used_in_prompt is False


def test_prompt_use_simulation_rejects_safe_but_non_beneficial_summary() -> None:
    fixture = _preflight_fixture()
    long_summary = (
        fixture["binding"].record.summary
        + " Extra detail that makes the reusable projection longer than the raw sources."
        * 6
    )
    binding = fixture["binding"].model_copy(
        update={
            "record": fixture["binding"].record.model_copy(
                update={
                    "summary": long_summary,
                    "original_chars": len(long_summary) + 100,
                    "compacted_chars": len(long_summary),
                }
            )
        }
    )
    admission = fixture["admission"].model_copy(
        update={
            "generated_summary_fingerprint": "sha256:"
            + hashlib.sha256(long_summary.encode("utf-8")).hexdigest(),
        }
    )
    facts = [
        ReusableCompactionSemanticFact(
            fact_id="fact-alpha",
            text="Alpha decision keep divide behaviour stable",
            evidence_candidate_ids=["dialog-1"],
        ),
        ReusableCompactionSemanticFact(
            fact_id="fact-beta",
            text="Beta validation run pytest after edits",
            evidence_candidate_ids=["dialog-2"],
        ),
    ]
    preflight = preflight_reusable_compaction_prompt_use(
        binding=binding,
        candidates=fixture["candidates"],
        policy=ContextAssemblyPolicy(max_prompt_chars=2000),
        renderer=fixture["renderer"],
        admission=admission,
        semantic_facts=facts,
        required_candidate_ids=["required-1"],
        recent_suffix_ids=["recent-1"],
    )

    result = simulate_reusable_compaction_prompt_use(
        binding=binding,
        candidates=fixture["candidates"],
        policy=ContextAssemblyPolicy(max_prompt_chars=2000),
        renderer=fixture["renderer"],
        preflight=preflight,
    )

    assert preflight.status == "passed"
    assert result.status == "rejected"
    assert "no_prompt_reduction" in result.rejection_reasons
