from __future__ import annotations

import copy

import pytest

from stage10_segmented_compaction_offline import (
    ARM_COMPACT_SEGMENTED,
    ARM_COMPACT_SELECT,
    ARM_CURRENT_FULL,
    FIXTURE_SIZES,
    run_offline_campaign,
)


def test_segmented_offline_campaign_uses_three_arms_and_zero_provider() -> None:
    result = run_offline_campaign()

    assert result["status"] == "passed"
    assert result["provider_calls"] == 0
    assert result["network_calls"] == 0
    assert result["project_mutations"] == 0
    assert result["production_compaction_producer"] == (
        "memory.context_builder.MemoryContextBuilder._dialog_compaction_record"
    )
    assert [case["trajectory_messages"] for case in result["cases"]] == list(
        FIXTURE_SIZES
    )
    for case in result["cases"]:
        assert set(case["arms"]) == {
            ARM_CURRENT_FULL,
            ARM_COMPACT_SELECT,
            ARM_COMPACT_SEGMENTED,
        }
        assert case["arms"][ARM_COMPACT_SEGMENTED]["compaction_algorithm"] == (
            "deterministic_observation_mask_v1"
        )
        assert case["arms"][ARM_COMPACT_SEGMENTED]["compaction_count"] == 1


def test_segmented_offline_campaign_passes_token_and_bounded_growth_gates() -> None:
    result = run_offline_campaign()

    assert result["gates"]["segmented_reduces_current_long_output"] is True
    assert result["gates"]["segmented_growth_50_vs_20_bounded"] is True
    for case in result["cases"]:
        full_chars = case["arms"][ARM_CURRENT_FULL]["prompt_chars"]
        segmented_chars = case["arms"][ARM_COMPACT_SEGMENTED]["prompt_chars"]
        assert (full_chars - segmented_chars) / full_chars >= 0.30
        comparison = case["comparisons"]["segmented_vs_compact_select"]
        select_chars = case["arms"][ARM_COMPACT_SELECT]["prompt_chars"]
        assert comparison["absolute_char_reduction"] == select_chars - segmented_chars
        assert comparison["reduction_fraction"] == (
            select_chars - segmented_chars
        ) / select_chars
    assert result["metrics"]["segmented_growth_50_vs_20"] <= 0.10
    assert set(result["metrics"]["segmented_vs_compact_select"]) == {
        "10",
        "20",
        "50",
    }


@pytest.mark.parametrize("message_count", FIXTURE_SIZES)
def test_segmented_offline_campaign_preserves_semantics_permissions_and_sources(
    message_count: int,
) -> None:
    result = run_offline_campaign()
    case = next(
        item for item in result["cases"] if item["trajectory_messages"] == message_count
    )
    full = case["arms"][ARM_CURRENT_FULL]
    segmented = case["arms"][ARM_COMPACT_SEGMENTED]

    assert segmented["semantic_slots"] == full["semantic_slots"]
    assert all(segmented["semantic_slots"].values())
    assert segmented["permission_signature"] == full["permission_signature"]
    assert segmented["required_candidate_ids"] == full["required_candidate_ids"]
    assert segmented["source_candidate_ids_preserved"] is True
    assert segmented["source_candidate_ids"] == full["source_candidate_ids"]
    assert segmented["old_environment_is_authoritative"] is False
    assert segmented["readme_suggestion_is_authoritative"] is False


def test_segmented_offline_hashes_are_stable_and_source_sensitive() -> None:
    first = run_offline_campaign()
    repeated = run_offline_campaign()
    changed = run_offline_campaign(source_change="changed historical stdout")

    assert first["campaign_input_hash"] == repeated["campaign_input_hash"]
    assert first["campaign_result_hash"] == repeated["campaign_result_hash"]
    assert first["cases"] == repeated["cases"]
    assert first["campaign_input_hash"] != changed["campaign_input_hash"]

    first_case = first["cases"][1]["arms"][ARM_COMPACT_SEGMENTED]
    changed_case = changed["cases"][1]["arms"][ARM_COMPACT_SEGMENTED]
    assert first_case["source_fingerprint"] != changed_case["source_fingerprint"]
    assert first_case["prompt_hash"] != changed_case["prompt_hash"]


def test_offline_analyzer_fails_closed_when_a_hard_gate_is_false(monkeypatch) -> None:
    import stage10_segmented_compaction_offline as module

    original = module._build_case

    def break_required_slot(*args, **kwargs):
        case = copy.deepcopy(original(*args, **kwargs))
        case["arms"][ARM_COMPACT_SEGMENTED]["semantic_slots"][
            "exact_validation_command"
        ] = False
        return case

    monkeypatch.setattr(module, "_build_case", break_required_slot)

    with pytest.raises(module.OfflineSegmentedCompactionError, match="semantic"):
        module.run_offline_campaign()


def test_lineage_gate_rejects_omitted_source_without_artifact_binding(
    monkeypatch,
) -> None:
    import stage10_segmented_compaction_offline as module

    synthetic_context = {
        "context_selection": {
            "candidate_decisions": [
                {
                    "candidate_id": "dialog:bound",
                    "kind": "dialog",
                    "action": "omitted",
                    "reason": "compacted",
                },
                {
                    "candidate_id": "dialog:unbound",
                    "kind": "dialog",
                    "action": "omitted",
                    "reason": "prompt_budget",
                },
            ]
        }
    }
    source_ids, preserved = module._source_lineage(
        synthetic_context,
        [{"source_candidate_ids": ["dialog:bound"]}],
    )
    assert source_ids == ["dialog:bound", "dialog:unbound"]
    assert preserved is False

    original = module._build_case

    def inject_unbound_omission(*args, **kwargs):
        case = copy.deepcopy(original(*args, **kwargs))
        for arm in (ARM_CURRENT_FULL, ARM_COMPACT_SEGMENTED):
            case["arms"][arm]["source_candidate_ids"].append("dialog:unbound")
        case["arms"][ARM_COMPACT_SEGMENTED][
            "source_candidate_ids_preserved"
        ] = False
        return case

    monkeypatch.setattr(module, "_build_case", inject_unbound_omission)
    with pytest.raises(module.OfflineSegmentedCompactionError, match="source lineage"):
        module.run_offline_campaign()
