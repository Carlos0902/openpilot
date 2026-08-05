from __future__ import annotations

from copy import deepcopy

import pytest
from memory.memory_models import MemoryRecord
from metadata import ProjectDiagnosisMetadata

import stage9_task_designer_scenario_gate as stage9


def test_protocol_freezes_offline_then_two_six_arm_provider_phases() -> None:
    protocol = stage9.load_protocol()

    stage9.validate_protocol(protocol)

    assert protocol["campaign_id"] == "stage9-task-designer-scenario-canary-v2"
    assert protocol["phases"]["offline_sentinel"] == {
        "provider_calls": 0,
        "required_scenarios": list(stage9.SCENARIO_IDS),
    }
    assert protocol["phases"]["provider_sentinel"]["runs"] == 6
    assert protocol["phases"]["provider_sentinel"]["requires_offline_pass"] is True
    assert protocol["phases"]["confirmatory"]["runs"] == 6
    assert protocol["phases"]["confirmatory"]["requires_provider_sentinel_pass"] is True
    assert protocol["token_limits"] == {
        "per_arm_hard": 30000,
        "per_pair_hard": 60000,
        "provider_sentinel_hard": 180000,
        "confirmatory_hard": 180000,
        "campaign_hard": 360000,
    }


def test_all_fixtures_round_trip_through_authoritative_models() -> None:
    fixtures = stage9.build_scenario_fixtures()

    assert tuple(fixtures) == stage9.SCENARIO_IDS
    for scenario_id, fixture in fixtures.items():
        diagnosis = ProjectDiagnosisMetadata.model_validate(
            fixture.improvement_report["diagnosis"]
        )
        assert diagnosis.model_dump(mode="json") == fixture.improvement_report["diagnosis"]
        for record in fixture.project_state.memory_records:
            assert "type" in record
            assert "memory_type" not in record
            storage_record = MemoryRecord(
                id=record["id"],
                memory_type=record["type"],
                content=record["content"],
                tags=record["tags"],
                timestamp=record["timestamp"],
                confidence=record["confidence"],
                attributes=record.get("attributes") or {},
            )
            assert stage9.serialize_memory_for_project_state(storage_record) == record
        assert fixture.scenario_id == scenario_id
        assert fixture.source_fingerprint.startswith("sha256:")


def test_positive_scenarios_share_one_executable_docstring_goal_surface() -> None:
    fixtures = stage9.build_scenario_fixtures()
    positive = [fixtures[scenario_id] for scenario_id in stage9.POSITIVE_SCENARIO_IDS]

    assert {fixture.goal.title for fixture in positive} == {
        "Document divide's denominator-zero contract."
    }
    assert {tuple(fixture.goal.acceptance_criteria) for fixture in positive} == {
        (
            "divide has a docstring containing the exact sentence: Raises ValueError when denominator is zero.",
            "calculator.py remains the only modified file.",
            "python -m pytest -q passes.",
            "python -m compileall -q calculator.py succeeds.",
        )
    }
    assert {
        fixture.goal.category for fixture in positive
    } == {"documentation"}


def test_positive_scenarios_vary_relationship_not_delivery_goal() -> None:
    fixtures = stage9.build_scenario_fixtures()
    strong = fixtures["strongly_related_diagnosis"]
    partial = fixtures["partial_shared_criterion"]
    memory = fixtures["relevant_iteration_memory"]

    strong_selected = strong.improvement_report["diagnosis"]["selected_candidate"]
    partial_selected = partial.improvement_report["diagnosis"]["selected_candidate"]
    memory_selected = memory.improvement_report["diagnosis"]["selected_candidate"]

    assert strong_selected["candidate_id"] == strong.goal.id
    assert partial_selected["candidate_id"] != partial.goal.id
    assert set(partial_selected["acceptance_criteria"]) & set(
        partial.goal.acceptance_criteria
    ) == {partial.goal.acceptance_criteria[0]}
    assert memory_selected["candidate_id"] != memory.goal.id
    assert not (
        set(memory_selected["acceptance_criteria"])
        & set(memory.goal.acceptance_criteria)
    )
    assert memory.project_state.memory_records[-1]["attributes"][
        "selected_candidate_id"
    ] == memory.goal.id


def test_partial_shared_criterion_keeps_selected_details_only() -> None:
    report = stage9.run_offline_sentinel()
    compact = report["scenarios"]["partial_shared_criterion"]["compact"]

    assert "S9_PARTIAL_DIAGNOSIS" in compact["rendered_content"]
    assert "S9_SELECTED_METRIC" in compact["rendered_content"]
    assert "S9_UNRELATED_DIMENSION" not in compact["rendered_content"]
    assert "S9_UNRELATED_METRIC" not in compact["rendered_content"]


def test_iteration_memory_fixture_matches_repaired_producer_and_selects_latest() -> None:
    fixture = stage9.build_scenario_fixtures()["relevant_iteration_memory"]
    records = fixture.project_state.memory_records

    assert len(records) == 4
    assert records[0]["tags"] == ["project_environment"]
    for record in records[1:]:
        assert "autonomous_iteration" in record["tags"]
        assert record["attributes"]["project_path"] == "."
        assert "selected_candidate_id" in record["attributes"]
        assert "selected_candidate" in record["attributes"]
        assert record["timestamp"]
        assert "TaskResultMetadata" not in record["content"]

    report = stage9.run_offline_sentinel()
    compact = report["scenarios"]["relevant_iteration_memory"]["compact"]
    assert "S9_LATEST_RELEVANT_ITERATION_RESULT" in compact["rendered_content"]
    assert "S9_OLD_RELEVANT_ITERATION_RESULT" not in compact["rendered_content"]
    assert "S9_NEW_UNRELATED_ITERATION_RESULT" not in compact["rendered_content"]
    assert "S9_ENVIRONMENT_NOISE" not in compact["rendered_content"]


def test_offline_gate_uses_real_assembly_and_reduces_every_scenario() -> None:
    report = stage9.run_offline_sentinel()

    assert report["provider_calls"] == 0
    assert report["passed"] is True
    assert report["token_counter"]["tokenizer_id"] == "stage9-stable-lexical-v1"
    for result in report["scenarios"].values():
        assert result["passed"] is True
        assert result["current"]["contract_hash"] == result["compact"]["contract_hash"]
        assert result["current"]["source_fingerprint"] == result["compact"]["source_fingerprint"]
        assert result["compact"]["final_prompt_tokens"] < result["current"]["final_prompt_tokens"]
        assert result["input_reduction_fraction"] >= 0.10
        for arm in stage9.ARMS:
            assert result[arm]["content_fingerprint"].startswith("sha256:")
            assert result[arm]["candidate_decisions"]
            assert result[arm]["assembly_status"] == "ready"
            assert result[arm]["protected_candidate_failures"] == []


def test_runtime_goal_or_contract_drift_fails_closed() -> None:
    fixture = stage9.build_scenario_fixtures()["strongly_related_diagnosis"]
    drifted_goal = fixture.goal.model_copy(update={"title": "drifted runtime goal"})

    with pytest.raises(stage9.ScenarioGateError, match="runtime goal hash mismatch"):
        stage9.assemble_scenario_arms(fixture, runtime_goal=drifted_goal)

    drifted_state = fixture.project_state.model_copy(
        update={"safe_target_files": ["./unrelated.py"]}
    )
    with pytest.raises(stage9.ScenarioGateError, match="runtime contract hash mismatch"):
        stage9.assemble_scenario_arms(fixture, runtime_project_state=drifted_state)


def test_protocol_or_frozen_fingerprint_tampering_fails_closed() -> None:
    protocol = deepcopy(stage9.load_protocol())
    protocol["scenarios"]["unrelated_diagnosis"]["frozen_source_fingerprint"] = (
        "sha256:" + "0" * 64
    )

    with pytest.raises(stage9.ScenarioGateError, match="frozen source fingerprint mismatch"):
        stage9.run_offline_sentinel(protocol)


def test_committed_offline_report_matches_frozen_runtime_snapshot() -> None:
    runtime = stage9.offline_report_snapshot(stage9.run_offline_sentinel())

    assert stage9.load_offline_report() == runtime
    assert runtime["passed"] is True
    assert runtime["provider_calls"] == 0
