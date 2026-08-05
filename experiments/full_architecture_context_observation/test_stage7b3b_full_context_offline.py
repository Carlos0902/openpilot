from __future__ import annotations

import socket

import pytest

from stage12_session_compact_canary_admission import (
    ProjectionFeatureFlag,
    RuntimeKillSwitch,
)
from stage7b3b_full_context_offline import run_full_context_offline_probe


def test_full_context_gate_consumes_one_ingress_projection_without_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network used")),
    )

    result = run_full_context_offline_probe()

    assert result["status"] == "passed"
    assert result["claim_boundary"] == "context_loader_analyzer_goal_task_no_provider"
    assert result["production_entry_exercised"] is True
    assert result["provider_calls"] == 0
    assert result["offline_model_calls"] == 1
    assert result["network_calls"] == 0
    assert result["project_mutations"] == 0
    assert result["memory_mutations"] == 0
    assert result["context_loader_dialog_source_ids"] == result["analyzer_dialog_source_ids"]
    assert result["goal_task_dialog_recall"] == {"current": 1.0, "compact": 1.0}
    assert result["goal_task_constraint_recall"] == {"current": 1.0, "compact": 1.0}
    assert result["session_turn_source_hash"].startswith("sha256:")
    assert result["session_constraints_hash"].startswith("sha256:")
    assert result["lineage_receipts"]["current"] == result["lineage_receipts"]["compact"]
    assert result["lineage_receipts"]["current"]["source_snapshot_hash"] == result["context_request_hash"]
    assert result["lineage_receipts"]["current"]["session_turn_source_hash"] == result["session_turn_source_hash"]
    assert result["lineage_receipts"]["current"]["session_constraints_hash"] == result["session_constraints_hash"]
    assert result["lineage_receipts"]["current"]["project_environment_mode"] == "read_only"
    assert result["lineage_receipts"]["current"]["write_scope_hash"].startswith("sha256:")
    assert result["fallback_receipt"] is None


def test_full_context_gate_emits_typed_fallback_receipt_without_mutation() -> None:
    result = run_full_context_offline_probe(compact_projection_failure=True)

    assert result["status"] == "passed"
    assert result["stop_reasons"] == ["compact_projection_failed"]
    receipt = result["fallback_receipt"]
    assert receipt["failed_projection"] == "compact"
    assert receipt["effective_projection"] == "current"
    assert receipt["source_snapshot_hash"] == result["context_request_hash"]
    assert receipt["session_turn_source_hash"] == result["session_turn_source_hash"]
    assert receipt["session_constraints_hash"] == result["session_constraints_hash"]
    assert receipt["project_environment_mode"] == "read_only"
    assert receipt["write_scope_hash"] == result["lineage_receipts"]["current"]["write_scope_hash"]
    assert result["lineage_receipts"]["current"] == result["lineage_receipts"]["compact"]
    assert receipt["provider_calls"] == 0
    assert receipt["network_calls"] == 0
    assert receipt["project_mutations"] == 0


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
def test_full_context_gate_stops_before_context_loader_when_controls_disabled(
    feature_flag: ProjectionFeatureFlag,
    kill_switch: RuntimeKillSwitch,
    reason: str,
) -> None:
    result = run_full_context_offline_probe(
        feature_flag=feature_flag,
        kill_switch=kill_switch,
    )

    assert result["status"] == "stopped"
    assert result["stop_reasons"] == [reason]
    assert result["production_entry_exercised"] is False
    assert result["provider_calls"] == 0
    assert result["network_calls"] == 0
    assert result["project_mutations"] == 0
