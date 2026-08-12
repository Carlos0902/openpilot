from __future__ import annotations

from memory.compaction_reuse import (
    ReusableCompactionPromptUseSimulationRejectionReason,
    ReusableCompactionPromptUseSimulationStatus,
    preflight_reusable_compaction_prompt_use,
    simulate_reusable_compaction_prompt_use,
)

from test_compaction_reuse_preflight import _fixture


def test_simulation_reports_reduction_without_prompt_authority() -> None:
    fixture = _fixture()
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
    assert result.status == ReusableCompactionPromptUseSimulationStatus.PASSED
    assert result.rejection_reasons == []
    assert result.prompt_char_delta is not None and result.prompt_char_delta > 0
    assert set(result.replaced_source_candidate_ids) == {"dialog-1", "dialog-2"}
    assert result.retained_required_candidate_ids == ["required-1"]
    assert result.retained_recent_suffix_ids == ["recent-1"]
    assert result.used_in_prompt is False


def test_simulation_rejects_unpassed_preflight_without_assembly_side_effects() -> None:
    fixture = _fixture()
    preflight = preflight_reusable_compaction_prompt_use(
        binding=fixture["binding"],
        candidates=fixture["candidates"],
        policy=fixture["policy"],
        renderer=fixture["renderer"],
        admission=None,
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
    assert result.status == ReusableCompactionPromptUseSimulationStatus.REJECTED
    assert (
        ReusableCompactionPromptUseSimulationRejectionReason.PREFLIGHT_NOT_PASSED
        in result.rejection_reasons
    )
    assert result.used_in_prompt is False

