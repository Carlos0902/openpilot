from __future__ import annotations

from pathlib import Path

from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.short_memory import ShortMemory


def _builder(tmp_path: Path, provider=None) -> MemoryContextBuilder:
    short_memory = ShortMemory(repo_path=tmp_path / "short")
    for index in range(4):
        short_memory.add_message("assistant", f"old message {index} with validation")
    return MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "memory"),
        max_prompt_chars=1000,
        compaction_reuse_shadow_provider=provider,
    )


def test_builder_shadow_admission_does_not_change_prompt_or_selected_candidates(tmp_path):
    builder = _builder(tmp_path)
    baseline = builder.build(
        "inspect", include_environment=False, limit=4, system_prompt="Keep stable."
    )
    observed: dict[str, object] = {}

    def provider(payload):
        observed.update(payload)
        return []

    builder.compaction_reuse_shadow_provider = provider
    shadow = builder.build(
        "inspect", include_environment=False, limit=4, system_prompt="Keep stable."
    )

    assert shadow["prompt_text"] == baseline["prompt_text"]
    assert shadow["selected_context_candidates"] == baseline["selected_context_candidates"]
    assert shadow["context_request_hash"] == baseline["context_request_hash"]
    assert "candidate_digests" in observed
    assert all("content" not in item for item in observed["candidate_digests"])
    assert "prompt_text" not in observed
    assert shadow["context_selection"]["compaction_reuse_admissions"] == []
    assert len(shadow["context_selection"]["compaction_reuse_shadow_failures"]) == 1
    assert shadow["context_selection"]["compaction_reuse_shadow_failures"][0]["reason"] == "provider_empty"


def test_builder_shadow_provider_admission_is_selection_metadata_only(tmp_path):
    def provider(payload):
        candidate_id = payload["candidate_digests"][0]["candidate_id"]
        return [{
            "admission_id": "reuse:1:test",
            "status": "rejected",
            "rejection_reason": "source_candidate_ids_mismatch",
            "source_candidate_ids": [candidate_id],
            "source_fingerprint": "sha256:" + "1" * 64,
            "source_binding_hash": "sha256:" + "2" * 64,
            "artifact_id": "artifact-1",
            "artifact_kind": "context_compaction",
            "artifact_integrity_checksum": "sha256:" + "3" * 64,
            "used_in_prompt": False,
        }]

    result = _builder(tmp_path, provider).build(
        "inspect", include_environment=False, limit=4, system_prompt="Keep stable."
    )
    assert result["context_selection"]["compaction_reuse_admissions"][0]["status"] == "rejected"
    assert result["context_selection"]["compaction_reuse_admissions"][0]["used_in_prompt"] is False
    assert result["selected_context_candidates"]
    assert result["prompt_text"]
