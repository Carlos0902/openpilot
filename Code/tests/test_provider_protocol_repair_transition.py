from __future__ import annotations

import pytest

from core.provider_protocol_repair_transition import (
    ProviderProtocolRepairAction,
    ProviderProtocolRepairError,
    provider_protocol_repair_transition,
)


def _transition(**overrides):
    values = {
        "model_repair_enabled": True,
        "repeated_protocol_attempt": False,
        "repairable_protocol_failure": True,
        "protocol_failure_count": 1,
        "round_index": 1,
        "max_rounds": 3,
    }
    values.update(overrides)
    return provider_protocol_repair_transition(**values)


def test_repairable_failure_requests_one_repair_round() -> None:
    decision = _transition()

    assert decision.action is ProviderProtocolRepairAction.REQUEST_REPAIR
    assert decision.error_code is None


def test_disabled_repair_policy_continues_without_repair() -> None:
    decision = _transition(model_repair_enabled=False)

    assert decision.action is ProviderProtocolRepairAction.NONE


def test_nonrepairable_failure_continues_without_repair() -> None:
    decision = _transition(repairable_protocol_failure=False)

    assert decision.action is ProviderProtocolRepairAction.NONE


@pytest.mark.parametrize(
    "overrides",
    [
        {"repeated_protocol_attempt": True},
        {"protocol_failure_count": 2},
    ],
)
def test_repeated_or_exhausted_protocol_repair_fails(overrides) -> None:
    decision = _transition(**overrides)

    assert decision.action is ProviderProtocolRepairAction.FAIL
    assert decision.error_code == "ProviderToolProtocolRepairExhausted"


def test_last_round_without_repair_budget_fails() -> None:
    decision = _transition(round_index=3, max_rounds=3)

    assert decision.action is ProviderProtocolRepairAction.FAIL
    assert decision.error_code == "ProviderToolProtocolRepairBudgetUnavailable"


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_repair_enabled": 1},
        {"repeated_protocol_attempt": "yes"},
        {"repairable_protocol_failure": None},
        {"protocol_failure_count": -1},
        {"protocol_failure_count": 4, "max_rounds": 3},
        {"round_index": 0},
        {"max_rounds": 0},
        {"round_index": 4, "max_rounds": 3},
    ],
)
def test_transition_rejects_invalid_state_facts(overrides) -> None:
    with pytest.raises(ProviderProtocolRepairError):
        _transition(**overrides)
