from __future__ import annotations

import pytest

from core.provider_read_finalization_transition import (
    ProviderReadFinalizationAction,
    ProviderReadFinalizationError,
    provider_read_finalization_transition,
)


def _transition(**overrides):
    values = {
        "page_cap_ready": False,
        "all_scoped_reads_complete": False,
        "read_only_tool_set": True,
        "has_bounded_projection": False,
        "round_made_progress": True,
        "finalization_requests": 0,
        "round_index": 1,
        "max_rounds": 3,
    }
    values.update(overrides)
    return provider_read_finalization_transition(**values)


def test_transition_requests_finalization_after_page_cap_completion() -> None:
    decision = _transition(
        page_cap_ready=True,
        all_scoped_reads_complete=True,
    )

    assert decision.action is ProviderReadFinalizationAction.REQUEST_FINALIZATION
    assert decision.error_code is None
    assert decision.finalization_pending is True
    assert decision.finalization_requests == 1


def test_transition_requests_finalization_after_bounded_no_progress() -> None:
    decision = _transition(
        all_scoped_reads_complete=True,
        has_bounded_projection=True,
        round_made_progress=False,
    )

    assert decision.action is ProviderReadFinalizationAction.REQUEST_FINALIZATION
    assert decision.finalization_requests == 1


@pytest.mark.parametrize("page_cap_ready", [False, True])
def test_transition_reports_budget_failure_when_no_finalization_round_remains(
    page_cap_ready: bool,
) -> None:
    decision = _transition(
        page_cap_ready=page_cap_ready,
        all_scoped_reads_complete=True,
        has_bounded_projection=not page_cap_ready,
        round_made_progress=page_cap_ready,
        round_index=3,
        max_rounds=3,
    )

    assert decision.action is ProviderReadFinalizationAction.FAIL
    assert decision.error_code == "ProviderToolFinalizationBudgetUnavailable"
    assert decision.finalization_pending is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"all_scoped_reads_complete": False, "page_cap_ready": True},
        {
            "all_scoped_reads_complete": True,
            "read_only_tool_set": False,
            "page_cap_ready": True,
        },
        {
            "all_scoped_reads_complete": True,
            "has_bounded_projection": False,
            "round_made_progress": False,
        },
        {
            "all_scoped_reads_complete": True,
            "page_cap_ready": True,
            "finalization_requests": 1,
        },
    ],
)
def test_transition_does_not_request_finalization_without_all_gates(
    overrides,
) -> None:
    decision = _transition(**overrides)

    assert decision.action is ProviderReadFinalizationAction.NONE
    assert decision.finalization_pending is False
    assert decision.finalization_requests == overrides.get(
        "finalization_requests",
        0,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"page_cap_ready": "yes"},
        {"all_scoped_reads_complete": 1},
        {"read_only_tool_set": None},
        {"has_bounded_projection": []},
        {"round_made_progress": "true"},
        {"finalization_requests": -1},
        {"round_index": 0},
        {"max_rounds": 0},
        {"round_index": 4, "max_rounds": 3},
        {"finalization_requests": 4, "max_rounds": 3},
    ],
)
def test_transition_rejects_invalid_state_facts(overrides) -> None:
    with pytest.raises(ProviderReadFinalizationError):
        _transition(**overrides)
