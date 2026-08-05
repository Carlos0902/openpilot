from __future__ import annotations

from stage11_session_constraint_offline import (
    ARM_COMPACT_WITH_STATE,
    ARM_COMPACT_WITHOUT_STATE,
    FIXTURE_SIZES,
    run_offline_campaign,
)


def test_session_constraint_three_arm_campaign_is_provider_free_and_passes_quality_gates() -> None:
    result = run_offline_campaign()

    assert result["status"] == "passed"
    assert result["provider_calls"] == 0
    assert result["network_calls"] == 0
    assert result["project_mutations"] == 0
    assert set(result["cases"][0]["arms"]) == {
        "full_truth",
        ARM_COMPACT_WITHOUT_STATE,
        ARM_COMPACT_WITH_STATE,
    }
    assert result["metrics"]["active_constraint_recall"] == 1.0
    assert result["metrics"]["assistant_authority_acceptance"] == 0.0
    assert result["metrics"]["confirmation_before_activation"] is True
    assert result["metrics"]["rejection_keeps_inactive"] is True
    assert result["metrics"]["revocation_leaves_tombstone"] is True
    assert result["metrics"]["identity_guards_fail_closed"] is True
    assert result["metrics"]["assistant_noise_keeps_state_hash"] is True
    assert result["metrics"]["assistant_noise_changes_prompt_hash"] is True


def test_session_constraint_compact_prompt_is_bounded_across_fixture_sizes() -> None:
    result = run_offline_campaign()

    for size in FIXTURE_SIZES:
        observation = next(case for case in result["cases"] if case["message_count"] == size)
        assert observation["arms"][ARM_COMPACT_WITHOUT_STATE]["prompt_chars"] <= 2_200
        assert observation["arms"][ARM_COMPACT_WITH_STATE]["prompt_chars"] <= 2_200
        assert observation["arms"][ARM_COMPACT_WITH_STATE]["constraint_candidate_kept"] is True
