from __future__ import annotations

import socket

import pytest

from stage12_session_compact_canary_admission import (
    ProjectionFeatureFlag,
    RuntimeKillSwitch,
)
from stage13_session_compact_pipeline_offline import (
    OfflinePipelineStop,
    run_offline_pipeline_probe,
)


def test_pipeline_probe_routes_ingress_to_goal_and_task_design_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network used")),
    )

    result = run_offline_pipeline_probe()

    assert result["status"] == "passed"
    assert result["claim_boundary"] == "goal_and_task_projection_only"
    assert result["production_entry_exercised"] is False
    assert result["provider_calls"] == 0
    assert result["network_calls"] == 0
    assert result["project_mutations"] == 0
    assert result["same_source"] is True
    assert result["claim_boundary"] == "goal_and_task_projection_only"
    assert result["production_entry_exercised"] is False
    assert result["session_constraints_active"] > 0
    assert result["arms"]["current"]["constraint_recall"] == 1.0
    assert result["arms"]["compact"]["constraint_recall"] == 1.0
    assert result["arms"]["current"]["dialog_recall"] == 1.0
    assert result["arms"]["compact"]["dialog_recall"] == 1.0
    assert result["arms"]["compact"]["fallback_count"] == 0
    assert result["arms"]["current"]["provider_calls"] == 0
    assert result["arms"]["compact"]["provider_calls"] == 0
    assert (
        result["arms"]["compact"]["requests"][1]["rendered_input_tokens"]
        < result["arms"]["current"]["requests"][1]["rendered_input_tokens"]
    )
    assert {
        item["purpose"]
        for arm in result["arms"].values()
        for item in arm["requests"]
    } == {"iteration_goal", "iteration_task_design"}
    for arm in result["arms"].values():
        for request in arm["requests"]:
            assert request["transport_attempted"] is False
            assert request["usage_observed"] is False
            assert request["rendered_input_tokens"] > 0
            assert request["provider_input_tokens"] is None
            assert request["provider_output_tokens"] is None
            assert request["provider_total_tokens"] is None
            assert request["token_scope"] == "rendered_prompt_only"
            assert request["dialog_recall"] == 1.0
            assert request["dialog_source_ids"] == [
                "stage5b-assistant-ack",
                "stage5b-user-constraint",
            ]
            assert request["session_turn_source_hash"].startswith("sha256:")

    turn_hashes = {
        request["session_turn_source_hash"]
        for arm in result["arms"].values()
        for request in arm["requests"]
    }
    assert len(turn_hashes) == 1


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
def test_pipeline_probe_stops_before_any_request_when_control_disabled(
    feature_flag: ProjectionFeatureFlag,
    kill_switch: RuntimeKillSwitch,
    reason: str,
) -> None:
    result = run_offline_pipeline_probe(
        feature_flag=feature_flag,
        kill_switch=kill_switch,
    )

    assert result["status"] == "stopped"
    assert result["provider_calls"] == 0
    assert result["requests"] == []
    assert result["stop_reasons"] == [reason]


def test_pipeline_probe_falls_back_to_current_on_compact_assembly_failure() -> None:
    result = run_offline_pipeline_probe(compact_projection_failure=True)

    assert result["status"] == "stopped"
    assert result["provider_calls"] == 0
    assert result["arms"]["compact"]["fallback_count"] == 1
    assert result["arms"]["compact"]["effective_projection"] == "current"
    assert "compact_projection_failed" in result["arms"]["compact"]["stop_reasons"]
    assert result["arms"]["compact"]["requests"]
    assert result["arms"]["compact"]["requests"][-1]["projection_policy"] == "current"
    receipt = result["arms"]["compact"]["fallback_receipt"]
    assert receipt["failed_projection"] == "compact"
    assert receipt["effective_projection"] == "current"
    assert receipt["reason_code"] == "compact_projection_failed"
    assert receipt["failed_execution_id"] == "offline:compact:2"
    assert receipt["fallback_execution_id"] == "offline:current:2"
    assert receipt["source_snapshot_hash"] == result["source_snapshot_hash"]
    assert receipt["session_turn_source_hash"] == result["arms"]["compact"]["requests"][-1]["session_turn_source_hash"]
    assert receipt["session_constraints_hash"]
    assert receipt["provider_calls"] == 0
    assert receipt["network_calls"] == 0
    assert receipt["project_mutations"] == 0


def test_pipeline_probe_rejects_provider_transport_injection() -> None:
    with pytest.raises(OfflinePipelineStop, match="provider transport is forbidden"):
        run_offline_pipeline_probe(transport=lambda request: object())
