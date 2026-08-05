from __future__ import annotations

import socket

import pytest

from stage12_session_compact_canary_admission import (
    ProjectionFeatureFlag,
    RuntimeKillSwitch,
)
from stage15_full_session_canary_gate import CanaryGateStop, run_pretransport_gate


def test_pretransport_gate_composes_full_entry_projection_and_campaign_reservations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network used")),
    )

    result = run_pretransport_gate()

    assert result["status"] == "passed"
    assert result["admission"]["status"] == "passed"
    assert result["admission"]["controls_admitted"] is True
    assert result["admission"]["projection_feature_flag"] == "canary_enabled"
    assert result["admission"]["runtime_kill_switch"] == "armed"
    assert result["full_entry"]["production_entry_exercised"] is True
    assert result["full_entry"]["session_constraints_hash"] == result["full_entry"]["checkpoint_constraints_hash"]
    assert result["full_entry"]["session_turn_source_hash"] == result["full_entry"]["checkpoint_turn_source_hash"]
    assert result["projection"]["constraint_recall"] == {"current": 1.0, "compact": 1.0}
    assert result["projection"]["dialog_recall"] == {"current": 1.0, "compact": 1.0}
    assert len(result["projection"]["session_turn_source_hashes"]) == 1
    assert result["ledger"]["reservation_count"] == 4
    assert result["ledger"]["observation_count"] == 0
    assert result["ledger"]["unknown_usage_action"] == "stop"
    assert result["provider_execution_admitted"] is False
    assert result["transport_attempted"] is False
    assert result["provider_calls"] == 0
    assert result["network_calls"] == 0
    assert result["project_mutations"] == 0


@pytest.mark.parametrize(
    ("feature_flag", "kill_switch", "reason"),
    [
        (ProjectionFeatureFlag.DISABLED, RuntimeKillSwitch.ARMED, "projection_feature_flag_disabled"),
        (ProjectionFeatureFlag.CANARY_ENABLED, RuntimeKillSwitch.ENGAGED, "runtime_kill_switch_engaged"),
    ],
)
def test_pretransport_gate_controls_stop_without_admission(
    feature_flag: ProjectionFeatureFlag,
    kill_switch: RuntimeKillSwitch,
    reason: str,
) -> None:
    result = run_pretransport_gate(feature_flag=feature_flag, kill_switch=kill_switch)

    assert result["status"] == "stopped"
    assert result["stop_reasons"] == [reason]
    assert result["provider_execution_admitted"] is False
    assert result["transport_attempted"] is False
    assert "ledger" not in result


def test_pretransport_gate_rejects_preflight_control_value_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stage15_full_session_canary_gate as gate_module

    monkeypatch.setattr(
        gate_module,
        "run_no_provider_preflight",
        lambda: {
            "status": "passed",
            "controls_admitted": True,
            "projection_feature_flag": "disabled",
            "runtime_kill_switch": "armed",
        },
    )

    with pytest.raises(CanaryGateStop, match="preflight controls"):
        run_pretransport_gate()
