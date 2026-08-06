from __future__ import annotations

import hashlib
import json

import pytest

from core.exceptions import ContextSourceError
from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.rolling_compaction import (
    RollingSummaryAdapter,
    RollingSummaryAttemptEvidence,
    RollingSummaryRequest,
)
from memory.short_memory import ShortMemory
from metadata import DurableArtifactReference


def _builder(tmp_path, *, enabled: bool, adapter=None, factory=None, max_chars: int = 620):
    return MemoryContextBuilder(
        short_memory=ShortMemory(repo_path=tmp_path / "short"),
        memory_store=MemoryStore(tmp_path / "memory"),
        max_prompt_chars=max_chars,
        rolling_summary_enabled=enabled,
        rolling_summary_adapter=adapter,
        rolling_summary_request_factory=factory,
        rolling_summary_token_limit=80,
    )


def _seed_dialog(builder: MemoryContextBuilder, count: int = 8) -> None:
    for index in range(count):
        builder.short_memory.add_message(
            "assistant",
            f"dialog-{index}-" + (str(index) * 120),
        )


def _persisted_sink(records: list[dict]):
    def persist(record: dict) -> DurableArtifactReference:
        records.append(record)
        checksum = hashlib.sha256(
            json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return DurableArtifactReference(
            artifact_id=f"compaction-{len(records)}",
            kind="context_compaction",
            integrity_checksum=f"sha256:{checksum}",
            bytes=len(json.dumps(record, ensure_ascii=False)),
        )

    return persist


def _request_factory(*, usage_observed: bool = True):
    def factory(candidates, max_summary_tokens: int) -> RollingSummaryRequest:
        deterministic = MemoryContextBuilder._dialog_compaction_record(list(candidates))
        source_ids = tuple(deterministic.source_candidate_ids)
        return RollingSummaryRequest(
            source_candidate_ids=source_ids,
            source_fingerprint=deterministic.source_fingerprint,
            previous_summary_fingerprint=None,
            provider_payload={
                "goal_delta": "compact history",
                "verified_facts": ["linked"],
                "decisions": ["keep suffix"],
                "open_issues": [],
                "evidence_ids": list(source_ids[:2]),
                "next_action": "continue",
            },
            attempt=RollingSummaryAttemptEvidence(
                usage={"completion_tokens": 20},
                usage_observed=usage_observed,
                finish_reason="stop",
            ),
            max_summary_tokens=max_summary_tokens,
            original_chars=deterministic.original_chars,
        )

    return factory


def test_rolling_summary_flag_off_keeps_deterministic_compaction_and_does_not_call_adapter(
    tmp_path,
) -> None:
    calls: list[str] = []

    def adapter(request):
        calls.append("adapter")
        raise AssertionError("disabled rolling summary must not invoke the adapter")

    def factory(*args):
        calls.append("factory")
        raise AssertionError("disabled rolling summary must not build a request")

    builder = _builder(
        tmp_path,
        enabled=False,
        adapter=adapter,
        factory=factory,
    )
    _seed_dialog(builder)
    persisted: list[dict] = []
    builder.set_checkpoint_handlers(compaction_sink=_persisted_sink(persisted))

    context = builder.build(
        "flag off",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    assert calls == []
    assert persisted[0]["algorithm"] == "deterministic_observation_mask_v1"
    assert context["context_compactions"][0]["record"]["algorithm"] == (
        "deterministic_observation_mask_v1"
    )


def test_rolling_summary_record_is_selected_and_persisted_when_enabled(tmp_path) -> None:
    adapter = RollingSummaryAdapter(count_tokens=lambda text: len(text.split()))
    builder = _builder(
        tmp_path,
        enabled=True,
        adapter=adapter,
        factory=_request_factory(),
    )
    _seed_dialog(builder)
    persisted: list[dict] = []
    builder.set_checkpoint_handlers(compaction_sink=_persisted_sink(persisted))

    context = builder.build(
        "rolling summary",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    assert persisted[0]["algorithm"] == "llm_rolling_summary_v1"
    assert context["context_compactions"][0]["record"]["algorithm"] == (
        "llm_rolling_summary_v1"
    )
    assert any(
        candidate["kind"] == "artifact"
        for candidate in context["selected_context_candidates"]
    )


def test_rolling_summary_fallback_restores_deterministic_compaction(tmp_path) -> None:
    adapter = RollingSummaryAdapter(count_tokens=lambda text: len(text.split()))
    builder = _builder(
        tmp_path,
        enabled=True,
        adapter=adapter,
        factory=_request_factory(usage_observed=False),
    )
    _seed_dialog(builder)
    persisted: list[dict] = []
    builder.set_checkpoint_handlers(compaction_sink=_persisted_sink(persisted))

    builder.build(
        "rolling fallback",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    assert persisted[0]["algorithm"] == "deterministic_observation_mask_v1"


@pytest.mark.parametrize("strict", [False, True])
def test_rolling_summary_sink_failure_preserves_existing_strict_boundary(tmp_path, strict: bool) -> None:
    adapter = RollingSummaryAdapter(count_tokens=lambda text: len(text.split()))
    builder = _builder(
        tmp_path,
        enabled=True,
        adapter=adapter,
        factory=_request_factory(),
    )
    _seed_dialog(builder)

    def fail_sink(record):
        raise OSError("artifact sink unavailable")

    builder.set_checkpoint_handlers(compaction_sink=fail_sink)
    if strict:
        with pytest.raises(ContextSourceError, match="context_compaction"):
            builder.build(
                "sink failure",
                include_environment=False,
                limit=8,
                system_prompt="Preserve recent dialog.",
                strict_sources=True,
            )
    else:
        context = builder.build(
            "sink failure",
            include_environment=False,
            limit=8,
            system_prompt="Preserve recent dialog.",
            strict_sources=False,
        )
        assert context["context_compactions"] == []
