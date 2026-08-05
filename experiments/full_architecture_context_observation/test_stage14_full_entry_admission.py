from __future__ import annotations

import socket

import pytest

from stage12_session_compact_canary_admission import (
    ProjectionFeatureFlag,
    RuntimeKillSwitch,
)
from stage14_full_entry_admission import (
    FullEntryAdmissionStop,
    run_full_entry_admission,
)


def test_full_entry_admission_exercises_execute_runtime_and_checkpoint_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network used")),
    )

    result = run_full_entry_admission()

    assert result["status"] == "passed"
    assert result["production_entry_exercised"] is True
    assert result["claim_boundary"] == "execute_runtime_checkpoint_only"
    assert result["session_constraints_hash"] == result["checkpoint_constraints_hash"]
    assert result["session_turn_source_hash"] == result["checkpoint_turn_source_hash"]
    assert result["raw_turn_count"] == 2
    assert result["active_constraint_count"] == 3
    assert result["provider_calls"] == 0
    assert result["network_calls"] == 0
    assert result["project_mutations"] == 0
    assert result["project_files"] == []


@pytest.mark.parametrize(
    ("feature_flag", "kill_switch", "reason"),
    [
        (
            ProjectionFeatureFlag.DISABLED,
            RuntimeKillSwitch.ARMED,
            "projection_feature_flag_disabled",
        ),
        (
            ProjectionFeatureFlag.CANARY_ENABLED,
            RuntimeKillSwitch.ENGAGED,
            "runtime_kill_switch_engaged",
        ),
    ],
)
def test_full_entry_controls_stop_before_execute(
    feature_flag: ProjectionFeatureFlag,
    kill_switch: RuntimeKillSwitch,
    reason: str,
) -> None:
    result = run_full_entry_admission(
        feature_flag=feature_flag,
        kill_switch=kill_switch,
    )

    assert result["status"] == "stopped"
    assert result["production_entry_exercised"] is False
    assert result["stop_reasons"] == [reason]
    assert result["provider_calls"] == 0
    assert result["project_mutations"] == 0


def test_full_entry_rejects_transport_injection() -> None:
    with pytest.raises(FullEntryAdmissionStop, match="transport is forbidden"):
        run_full_entry_admission(transport=lambda request: object())
