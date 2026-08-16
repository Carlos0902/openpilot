from __future__ import annotations

import hashlib
import json

import pytest

from core.exceptions import ContextSourceError, InvalidLLMResponseError
from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.rolling_compaction import (
    RollingSummaryAdapter,
    RollingSummaryAttemptEvidence,
    RollingSummaryRequest,
)
from memory.short_memory import ShortMemory
from metadata import (
    ContextCompactionReuseAdmission,
    ContextCompactionReuseAdmissionStatus,
    ContextCompactionReuseRejectionReason,
    DurableArtifactReference,
)


class _CountingTokenCounter:
    available = True
    tokenizer_id = "test-char-v1"
    model = "test"

    def count_text(self, text: str) -> int:
        return max(1, (len(text) + 3) // 4)


def _builder(
    tmp_path,
    *,
    enabled: bool,
    adapter=None,
    factory=None,
    max_chars: int = 620,
    max_prompt_tokens: int | None = None,
    reuse_provider=None,
):
    return MemoryContextBuilder(
        short_memory=ShortMemory(repo_path=tmp_path / "short"),
        memory_store=MemoryStore(tmp_path / "memory"),
        max_prompt_chars=max_chars,
        max_prompt_tokens=max_prompt_tokens,
        token_counter=_CountingTokenCounter() if max_prompt_tokens is not None else None,
        rolling_summary_enabled=enabled,
        rolling_summary_adapter=adapter,
        rolling_summary_request_factory=factory,
        rolling_summary_token_limit=80,
        compaction_reuse_shadow_provider=reuse_provider,
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
                usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                usage_observed=usage_observed,
                finish_reason="stop",
            ),
            max_summary_tokens=max_summary_tokens,
            original_chars=deterministic.original_chars,
        )

    return factory


def _reuse_admission(admission_id: str = "reuse-1") -> ContextCompactionReuseAdmission:
    return ContextCompactionReuseAdmission(
        admission_id=admission_id,
        status=ContextCompactionReuseAdmissionStatus.ADMITTED,
        source_candidate_ids=["dialog-1", "dialog-2"],
        source_fingerprint="sha256:" + "1" * 64,
        source_binding_hash="sha256:" + "2" * 64,
        required_candidate_ids=["system"],
        recent_suffix_ids=["dialog-7", "dialog-8"],
        session_constraints_hash="sha256:" + "3" * 64,
        artifact_id="artifact-1",
        artifact_kind="context_compaction",
        artifact_integrity_checksum="sha256:" + "4" * 64,
        generated_summary_fingerprint="sha256:" + "5" * 64,
    )


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
    attempts = context["context_selection"]["compaction_attempts"]
    assert attempts[0]["provider_status"] == "not_attempted"
    assert attempts[0]["selection_status"] == "selected"
    assert attempts[0]["fallback_reason"] == "deterministic_fallback"
    assert context["context_selection"]["compaction_reuse_admissions"] == []


def test_compaction_reuse_shadow_provider_is_default_off(tmp_path) -> None:
    builder = _builder(tmp_path, enabled=False)
    _seed_dialog(builder)

    context = builder.build(
        "no reuse shadow provider",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    assert context["context_selection"]["compaction_reuse_admissions"] == []


def test_compaction_reuse_shadow_provider_adds_body_free_metadata_without_prompt_change(
    tmp_path,
) -> None:
    baseline = _builder(tmp_path, enabled=False)
    _seed_dialog(baseline)
    baseline_context = baseline.build(
        "reuse shadow",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )
    seen_payloads: list[dict] = []

    def provider(payload):
        seen_payloads.append(payload)
        return [
            _reuse_admission(),
            {
                **_reuse_admission("reuse-2").model_dump(mode="python"),
                "status": ContextCompactionReuseAdmissionStatus.REJECTED,
                "rejection_reason": (
                    ContextCompactionReuseRejectionReason.SOURCE_FINGERPRINT_MISMATCH
                ),
            },
        ]

    builder = _builder(tmp_path, enabled=False, reuse_provider=provider)
    builder.short_memory = baseline.short_memory
    context = builder.build(
        "reuse shadow",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    assert seen_payloads
    encoded_payload = json.dumps(seen_payloads[0], sort_keys=True)
    assert "prompt_text" not in encoded_payload
    assert "dialog-0-" not in encoded_payload
    assert "content_sha256" in encoded_payload
    assert "source_fingerprint_by_candidate_ids" in encoded_payload
    assert context["prompt_text"] == baseline_context["prompt_text"]
    assert context["selected_context_candidates"] == baseline_context["selected_context_candidates"]
    assert context["context_compactions"] == baseline_context["context_compactions"]
    admissions = context["context_selection"]["compaction_reuse_admissions"]
    assert [item["status"] for item in admissions] == ["admitted", "rejected"]
    assert admissions[0]["used_in_prompt"] is False
    assert admissions[1]["rejection_reason"] == "source_fingerprint_mismatch"
    assert context["context_request_hash"] == baseline_context["context_request_hash"]


def test_compaction_reuse_shadow_provider_failure_is_fail_closed(tmp_path) -> None:
    def provider(_payload):
        raise RuntimeError("shadow admission unavailable")

    builder = _builder(tmp_path, enabled=False, reuse_provider=provider)
    _seed_dialog(builder)

    context = builder.build(
        "reuse shadow failure",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    assert context["context_selection"]["compaction_reuse_admissions"] == []
    failures = context["context_selection"]["compaction_reuse_shadow_failures"]
    assert failures == [
        {
            "failure_id": "compaction-reuse-shadow:provider_exception",
            "reason": "provider_exception",
            "exception_type": "RuntimeError",
            "strict_sources": False,
            "fallback_applied": True,
        }
    ]

    with pytest.raises(ContextSourceError, match="context_compaction_reuse"):
        builder.build(
            "reuse shadow failure",
            include_environment=False,
            limit=8,
            system_prompt="Preserve recent dialog.",
            strict_sources=True,
        )


@pytest.mark.parametrize(
    ("provider_result", "expected_reason"),
    [
        (None, "provider_empty"),
        ([], "provider_empty"),
        ([{"status": "admitted"}], "invalid_provider_result"),
    ],
)
def test_compaction_reuse_shadow_provider_empty_or_malformed_is_typed(
    tmp_path,
    provider_result,
    expected_reason,
) -> None:
    builder = _builder(
        tmp_path,
        enabled=False,
        reuse_provider=lambda _payload: provider_result,
    )
    _seed_dialog(builder)

    context = builder.build(
        "reuse shadow typed fallback",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    assert context["context_selection"]["compaction_reuse_admissions"] == []
    failure = context["context_selection"]["compaction_reuse_shadow_failures"][0]
    assert failure["reason"] == expected_reason
    assert failure["fallback_applied"] is True
    assert "shadow admission unavailable" not in json.dumps(context["context_selection"])


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
    attempts = context["context_selection"]["compaction_attempts"]
    assert any(item["provider_status"] == "accepted" for item in attempts)
    assert any(item["selection_status"] == "selected" for item in attempts)
    assert any(
        item["provider_status"] == "accepted" and item["selection_status"] == "selected"
        and item["fallback_reason"] is None
        for item in attempts
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
    attempts = builder.build(
        "rolling fallback evidence",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )["context_selection"]["compaction_attempts"]
    assert attempts[0]["provider_status"] == "rejected"
    assert attempts[0]["fallback_reason"] == "invalid_adapter_result"
    assert attempts[0]["usage_complete"] is False
    assert attempts[-1]["selection_status"] == "selected"


def test_rolling_summary_provider_cap_failure_preserves_usage_and_finish_evidence(tmp_path) -> None:
    def factory(_candidates, _max_summary_tokens: int):
        raise InvalidLLMResponseError(
            "summary response was truncated",
            response_text='{"goal_delta":"partial"}',
            usage={"prompt_tokens": 2522, "completion_tokens": 256, "total_tokens": 2778},
            finish_reason="length",
        )

    builder = _builder(
        tmp_path,
        enabled=True,
        adapter=RollingSummaryAdapter(count_tokens=lambda text: len(text.split())),
        factory=factory,
    )
    _seed_dialog(builder)
    persisted: list[dict] = []
    builder.set_checkpoint_handlers(compaction_sink=_persisted_sink(persisted))

    context = builder.build(
        "provider cap evidence",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    attempts = context["context_selection"]["compaction_attempts"]
    provider_attempt = attempts[0]
    assert provider_attempt["provider_status"] == "rejected"
    assert provider_attempt["fallback_reason"] == "provider_output_truncated"
    assert provider_attempt["summary_token_limit"] == 80
    assert provider_attempt["usage_complete"] is True
    assert provider_attempt["provider_prompt_tokens"] == 2522
    assert provider_attempt["provider_completion_tokens"] == 256
    assert provider_attempt["provider_total_tokens"] == 2778
    assert provider_attempt["finish_reason"] == "length"
    assert attempts[-1]["selection_status"] == "selected"
    assert persisted[0]["algorithm"] == "deterministic_observation_mask_v1"


def test_rolling_summary_invalid_usage_fails_closed_without_metadata_crash(tmp_path) -> None:
    def factory(candidates, max_summary_tokens: int) -> RollingSummaryRequest:
        deterministic = MemoryContextBuilder._dialog_compaction_record(list(candidates))
        source_ids = tuple(deterministic.source_candidate_ids)
        return RollingSummaryRequest(
            source_candidate_ids=source_ids,
            source_fingerprint=deterministic.source_fingerprint,
            provider_payload={
                "goal_delta": "compact history",
                "verified_facts": ["linked"],
                "decisions": ["keep suffix"],
                "open_issues": [],
                "evidence_ids": list(source_ids[:2]),
                "next_action": "continue",
            },
            attempt=RollingSummaryAttemptEvidence(
                usage={"prompt_tokens": -1, "completion_tokens": 20, "total_tokens": 19},
                usage_observed=True,
                finish_reason="stop",
            ),
            max_summary_tokens=max_summary_tokens,
            original_chars=deterministic.original_chars,
        )

    builder = _builder(
        tmp_path,
        enabled=True,
        adapter=RollingSummaryAdapter(count_tokens=lambda text: len(text.split())),
        factory=factory,
    )
    _seed_dialog(builder)
    persisted: list[dict] = []
    builder.set_checkpoint_handlers(compaction_sink=_persisted_sink(persisted))

    context = builder.build(
        "invalid usage evidence",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    attempts = context["context_selection"]["compaction_attempts"]
    assert attempts[0]["provider_status"] == "rejected"
    assert attempts[0]["fallback_reason"] == "invalid_adapter_result"
    assert attempts[0]["usage_complete"] is False
    assert attempts[0]["provider_prompt_tokens"] is None
    assert attempts[-1]["selection_status"] == "selected"
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
        assert context["context_selection"]["compaction_attempts"][-1][
            "fallback_reason"
        ] == "artifact_sink_failure"


def test_rolling_summary_sink_none_is_observed_only_not_sink_failure(tmp_path) -> None:
    adapter = RollingSummaryAdapter(count_tokens=lambda text: len(text.split()))
    builder = _builder(
        tmp_path,
        enabled=True,
        adapter=adapter,
        factory=_request_factory(),
    )
    _seed_dialog(builder)
    builder.set_checkpoint_handlers(compaction_sink=lambda _record: None)

    context = builder.build(
        "observation-only sink",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    assert context["context_compactions"] == []
    observed = [
        attempt
        for attempt in context["context_selection"]["compaction_attempts"]
        if attempt["selection_outcome"] == "generated_observed_only"
    ]
    assert observed
    assert observed[-1]["fallback_reason"] is None
    assert observed[-1]["artifact_sink_status"] == "observed_only"
    assert observed[-1]["used_in_prompt"] is False


def test_dynamic_summary_budget_uses_remaining_prompt_slot_and_static_cap(tmp_path) -> None:
    requested_limits: list[int] = []

    def factory(candidates, max_summary_tokens: int) -> RollingSummaryRequest:
        requested_limits.append(max_summary_tokens)
        return _request_factory()(candidates, max_summary_tokens)

    builder = _builder(
        tmp_path,
        enabled=True,
        adapter=RollingSummaryAdapter(count_tokens=lambda text: len(text.split())),
        factory=factory,
        max_prompt_tokens=300,
    )
    _seed_dialog(builder)
    persisted: list[dict] = []
    builder.set_checkpoint_handlers(compaction_sink=_persisted_sink(persisted))

    builder.build(
        "dynamic summary budget",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    assert requested_limits
    assert requested_limits[0] < 80
    assert requested_limits[0] > 0
    assert persisted[0]["algorithm"] == "llm_rolling_summary_v1"


def test_dynamic_summary_budget_zero_skips_factory_and_keeps_deterministic_view(tmp_path) -> None:
    calls: list[int] = []

    def factory(candidates, max_summary_tokens: int) -> RollingSummaryRequest:
        calls.append(max_summary_tokens)
        return _request_factory()(candidates, max_summary_tokens)

    builder = _builder(
        tmp_path,
        enabled=True,
        adapter=RollingSummaryAdapter(count_tokens=lambda text: len(text.split())),
        factory=factory,
        max_prompt_tokens=190,
    )
    _seed_dialog(builder)
    persisted: list[dict] = []
    builder.set_checkpoint_handlers(compaction_sink=_persisted_sink(persisted))

    builder.build(
        "no dynamic summary slot",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    assert calls == []
    assert persisted[0]["algorithm"] == "deterministic_observation_mask_v1"
    attempts = builder.build(
        "no dynamic summary slot evidence",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )["context_selection"]["compaction_attempts"]
    assert attempts[0]["fallback_reason"] == "budget_zero"
