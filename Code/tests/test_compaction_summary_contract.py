from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import pytest
from pydantic import ValidationError

from memory.compaction_summary import (
    CompactionSummaryValidationError,
    calculate_summary_budget,
    validate_summary_payload,
)
from metadata import ContextCompactionRecord, ContextCompactionSummary


def _payload() -> dict[str, object]:
    return {
        "goal_delta": "diagnosis narrowed to the divide path",
        "verified_facts": ["test failure reproduced"],
        "decisions": ["keep the public API unchanged"],
        "open_issues": ["run the required validation command"],
        "evidence_ids": ["dialog-1", "dialog-2"],
        "next_action": "apply the smallest fix",
    }


def test_llm_summary_payload_is_strict_and_source_linked() -> None:
    summary = validate_summary_payload(
        _payload(),
        source_candidate_ids=["dialog-1", "dialog-2"],
        max_summary_tokens=80,
        count_tokens=lambda text: len(text.split()),
    )

    assert isinstance(summary, ContextCompactionSummary)
    assert summary.evidence_ids == ["dialog-1", "dialog-2"]


def test_summary_rejects_authority_fields_and_unknown_evidence() -> None:
    payload = {**_payload(), "write_files": ["calculator.py"]}
    with pytest.raises(CompactionSummaryValidationError, match="unknown fields"):
        validate_summary_payload(
            payload,
            source_candidate_ids=["dialog-1", "dialog-2"],
            max_summary_tokens=80,
            count_tokens=lambda text: len(text.split()),
        )

    with pytest.raises(CompactionSummaryValidationError, match="evidence"):
        validate_summary_payload(
            {**_payload(), "evidence_ids": ["goal-domain-id"]},
            source_candidate_ids=["dialog-1", "dialog-2"],
            max_summary_tokens=80,
            count_tokens=lambda text: len(text.split()),
        )


def test_summary_rejects_empty_or_over_budget_output() -> None:
    with pytest.raises(CompactionSummaryValidationError, match="empty"):
        validate_summary_payload(
            {"goal_delta": "", "evidence_ids": ["dialog-1"]},
            source_candidate_ids=["dialog-1"],
            max_summary_tokens=80,
            count_tokens=lambda text: len(text.split()),
        )

    with pytest.raises(CompactionSummaryValidationError, match="budget"):
        validate_summary_payload(
            {**_payload(), "goal_delta": "word " * 20},
            source_candidate_ids=["dialog-1", "dialog-2"],
            max_summary_tokens=5,
            count_tokens=lambda text: len(text.split()),
        )


def test_summary_budget_never_goes_negative_and_reserves_required_slots() -> None:
    assert calculate_summary_budget(
        static_cap_tokens=256,
        requested_prompt_tokens=2_000,
        used_prompt_tokens=1_200,
        required_reserve_tokens=500,
        recent_suffix_reserve_tokens=200,
        response_schema_reserve_tokens=100,
    ) == 0
    assert calculate_summary_budget(
        static_cap_tokens=256,
        requested_prompt_tokens=2_000,
        used_prompt_tokens=600,
        required_reserve_tokens=200,
        recent_suffix_reserve_tokens=100,
        response_schema_reserve_tokens=100,
    ) == 256


def test_legacy_deterministic_compaction_record_remains_readable() -> None:
    record = ContextCompactionRecord(
        compaction_id="legacy-1",
        source_fingerprint="sha256:" + "a" * 64,
        source_candidate_ids=["dialog-1"],
        algorithm="deterministic_observation_mask_v1",
        summary="masked observation",
        original_chars=100,
        compacted_chars=18,
    )

    assert record.summary_payload is None


def test_llm_compaction_record_requires_structured_payload() -> None:
    with pytest.raises(ValidationError, match="summary_payload"):
        ContextCompactionRecord(
            compaction_id="llm-1",
            source_fingerprint="sha256:" + "a" * 64,
            source_candidate_ids=["dialog-1"],
            algorithm="llm_rolling_summary_v1",
            summary="{}",
            original_chars=100,
            compacted_chars=2,
        )


@pytest.mark.parametrize(
    "history_length,relevance,purpose",
    product(
        ("short", "medium", "long"),
        ("strong", "partial", "irrelevant"),
        ("iteration_goal", "iteration_task_design", "code_generation", "bug_fix"),
    ),
)
def test_offline_quality_matrix_accepts_bounded_valid_summary(
    history_length: str,
    relevance: str,
    purpose: str,
) -> None:
    fixture = json.loads(
        Path(__file__).with_name("fixtures").joinpath(
            "compaction_summary_quality_cases.json"
        ).read_text()
    )
    assert history_length in fixture["history_lengths"]
    assert relevance in fixture["relevance"]
    assert purpose in fixture["purposes"]

    summary = validate_summary_payload(
        _payload(),
        source_candidate_ids=["dialog-1", "dialog-2"],
        max_summary_tokens=80,
        count_tokens=lambda text: len(text.split()),
    )
    assert set(summary.evidence_ids) <= {"dialog-1", "dialog-2"}


def test_unknown_summary_usage_is_fail_closed() -> None:
    with pytest.raises(CompactionSummaryValidationError, match="unknown"):
        validate_summary_payload(
            _payload(),
            source_candidate_ids=["dialog-1", "dialog-2"],
            max_summary_tokens=80,
            count_tokens=lambda text: len(text.split()),
            usage_observed=False,
        )
