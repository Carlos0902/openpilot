from __future__ import annotations

import pytest

from core.provider_no_progress_transition import (
    ProviderNoProgressAction,
    ProviderNoProgressError,
    provider_no_progress_transition,
)


def _transition(**overrides):
    values = {
        "duplicate_only_covered": False,
        "mutation_tools_exposed": False,
        "mutation_duplicate_guidance_sent": False,
        "read_only_tool_set": True,
        "finalization_requests": 0,
        "round_made_progress": True,
        "no_progress_rounds": 0,
        "max_no_progress_rounds": 2,
        "duplicate_only_rounds": 0,
        "round_index": 1,
        "max_rounds": 3,
    }
    values.update(overrides)
    return provider_no_progress_transition(**values)


def test_duplicate_mutation_requests_guidance_once() -> None:
    decision = _transition(
        duplicate_only_covered=True,
        mutation_tools_exposed=True,
        read_only_tool_set=False,
        round_made_progress=False,
    )

    assert decision.action is ProviderNoProgressAction.REQUEST_MUTATION_GUIDANCE
    assert decision.mutation_duplicate_guidance_sent is True
    assert decision.no_progress_rounds == 0


def test_duplicate_mutation_after_guidance_counts_no_progress() -> None:
    decision = _transition(
        duplicate_only_covered=True,
        mutation_tools_exposed=True,
        mutation_duplicate_guidance_sent=True,
        read_only_tool_set=False,
        round_made_progress=False,
    )

    assert decision.action is ProviderNoProgressAction.CONTINUE
    assert decision.no_progress_rounds == 1


def test_duplicate_read_only_requests_finalization() -> None:
    decision = _transition(
        duplicate_only_covered=True,
        read_only_tool_set=True,
        round_made_progress=False,
    )

    assert decision.action is ProviderNoProgressAction.REQUEST_FINALIZATION
    assert decision.finalization_requests == 1
    assert decision.finalization_pending is True
    assert decision.duplicate_only_rounds == 1
    assert decision.no_progress_rounds == 0


def test_duplicate_read_only_fails_without_finalization_round() -> None:
    decision = _transition(
        duplicate_only_covered=True,
        read_only_tool_set=True,
        round_made_progress=False,
        round_index=3,
        max_rounds=3,
    )

    assert decision.action is ProviderNoProgressAction.FAIL
    assert decision.error_code == "ProviderToolFinalizationBudgetUnavailable"


def test_ordinary_progress_resets_no_progress_counter() -> None:
    decision = _transition(no_progress_rounds=1, round_made_progress=True)

    assert decision.action is ProviderNoProgressAction.NONE
    assert decision.no_progress_rounds == 0


def test_ordinary_no_progress_continues_below_threshold() -> None:
    decision = _transition(round_made_progress=False)

    assert decision.action is ProviderNoProgressAction.CONTINUE
    assert decision.no_progress_rounds == 1
    assert decision.error_code is None


def test_no_progress_threshold_may_exceed_round_budget() -> None:
    decision = _transition(
        round_made_progress=False,
        max_no_progress_rounds=2,
        round_index=1,
        max_rounds=1,
    )

    assert decision.action is ProviderNoProgressAction.CONTINUE
    assert decision.no_progress_rounds == 1


@pytest.mark.parametrize("duplicate_only_covered", [False, True])
def test_no_progress_threshold_is_terminal(
    duplicate_only_covered: bool,
) -> None:
    decision = _transition(
        duplicate_only_covered=duplicate_only_covered,
        read_only_tool_set=not duplicate_only_covered,
        round_made_progress=False,
        no_progress_rounds=1,
    )

    assert decision.action is ProviderNoProgressAction.FAIL
    assert decision.error_code == "ProviderToolNoProgress"
    assert decision.no_progress_rounds == 2


def test_existing_finalization_request_skips_duplicate_finalization() -> None:
    decision = _transition(
        duplicate_only_covered=True,
        read_only_tool_set=True,
        finalization_requests=1,
        round_made_progress=False,
    )

    assert decision.action is ProviderNoProgressAction.CONTINUE
    assert decision.finalization_requests == 1
    assert decision.duplicate_only_rounds == 0
    assert decision.no_progress_rounds == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"duplicate_only_covered": "yes"},
        {"mutation_tools_exposed": 1},
        {"mutation_duplicate_guidance_sent": None},
        {"read_only_tool_set": []},
        {"round_made_progress": "true"},
        {"finalization_requests": -1},
        {"no_progress_rounds": -1},
        {"max_no_progress_rounds": 0},
        {"duplicate_only_rounds": -1},
        {"round_index": 0},
        {"max_rounds": 0},
        {"round_index": 4, "max_rounds": 3},
    ],
)
def test_transition_rejects_invalid_state_facts(overrides) -> None:
    with pytest.raises(ProviderNoProgressError):
        _transition(**overrides)
