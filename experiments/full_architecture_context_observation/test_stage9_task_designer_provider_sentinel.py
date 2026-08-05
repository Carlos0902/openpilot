from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import stage9_task_designer_provider_sentinel as sentinel
import stage8_task_designer_context_campaign as stage8
from stage9_task_designer_scenario_gate import build_scenario_fixtures


def _record(
    tmp_path: Path,
    item: dict,
    *,
    passed: bool = True,
    total_tokens: int = 10_000,
) -> dict:
    project_root = tmp_path / f"project-{item['ordinal']}"
    project_root.mkdir()
    calculator = project_root / "calculator.py"
    calculator.write_text(
        'def divide(numerator, denominator):\n'
        '    """Return a quotient. Raises ValueError when denominator is zero."""\n'
        '    if denominator == 0:\n'
        '        raise ValueError("denominator cannot be zero")\n'
        '    return numerator / denominator\n',
        encoding="utf-8",
    )
    start_snapshot = sentinel.capture_enhancement_start_snapshot(project_root)
    protocol = sentinel.load_protocol()
    contract = protocol["scenario_candidate_contracts"][item["scenario_id"]][
        item["arm"]
    ]
    return {
        **item,
        "provider_identity": protocol["provider_identity"],
        "source_fingerprint": protocol["scenarios"][item["scenario_id"]][
            "frozen_source_fingerprint"
        ],
        "goal_hash": protocol["scenarios"][item["scenario_id"]][
            "frozen_goal_hash"
        ],
        "runtime_contract_hash": "sha256:runtime-contract",
        "projection_policy": item["arm"],
        "quality_gate": {
            "passed": passed,
            "checks": {
                "core_success": passed,
                "verification_passed": passed,
                "improvement_succeeded": passed,
                "required_commands": passed,
                "mutation_scope": passed,
            },
        },
        "project_root": str(project_root),
        "modified_paths": [str(calculator)],
        "observed_project_mutations": {
            "all_changed_paths": [str(calculator)],
            "modified_paths": [str(calculator)],
            "added_paths": [],
            "deleted_paths": [],
            "before_truncated": False,
            "after_truncated": False,
            "symlink_paths": [],
        },
        "observed_enhancement_mutations": {
            "all_changed_paths": [str(calculator)],
            "modified_paths": [str(calculator)],
            "added_paths": [],
            "deleted_paths": [],
            "before_truncated": False,
            "after_truncated": False,
            "symlink_paths": [],
            "snapshot_missing": False,
            "start_capture_count": 1,
            "start_observation_count": 1,
            "start_consistent": True,
        },
        "runtime_owned_mutations": {
            "added_paths": [],
            "deleted_paths": [],
            "modified_paths": [],
            "all_changed_paths": [],
        },
        "user_owned_mutations": {
            "added_paths": [],
            "deleted_paths": [],
            "modified_paths": [str(calculator)],
            "all_changed_paths": [str(calculator)],
        },
        "mutation_classification_failures": [],
        "producer_validation": {
            "passed": True,
            "raw_descriptor_failures": [],
            "expected_artifact_count": 0,
            "artifacts": [],
            "failures": [],
        },
        "calculator_path": str(calculator),
        "overall_usage_coverage": 1.0,
        "unknown_failed_usage_count": 0,
        "transport_retry_count": 0,
        "guard_observation": {
            "default_max_completion_tokens": 4096,
            "defaulted_max_completion_request_count": 1,
            "effective_max_completion_tokens": [4096],
            "logical_complete_calls_seen": 1,
            "usage_censored": False,
            "unsettled_reserved_tokens": 0,
            "reservation_overrun_tokens": 0,
            "blocked_requests_before_transport": 0,
            "reservation_admission_failed": False,
            "blocked_reservation_tokens": 0,
            "blocking_limits": [],
        },
        "usage": {"lifecycle": {"total_tokens": total_tokens}},
        "task_designer": {
            "request_count": 1,
            "usage_observed": True,
            "recovery_count": 0,
            "failed_attempt_count": 0,
            "reasoning_mode": "disabled",
            "omitted_required_candidate_ids": [],
            "required_partial_count": 0,
            "candidate_decisions": [
                {"candidate_id": "stage9:sentinel", "action": "kept"}
            ],
        },
        "task_designer_context_intervention": {
            "scenario_id": item["scenario_id"],
            "projection_policy": item["arm"],
            "source_fingerprint": protocol["scenarios"][item["scenario_id"]][
                "frozen_source_fingerprint"
            ],
            "goal_hash": protocol["scenarios"][item["scenario_id"]][
                "frozen_goal_hash"
            ],
            "runtime_contract_hashes": ["sha256:runtime-contract"],
            "sentinel_contract": contract,
            "sentinel_contract_passed": True,
            "sentinel_candidate_ids": ["stage9:sentinel"],
            "enhancement_start_snapshot_capture_count": 1,
            "enhancement_start_snapshot_observation_count": 1,
            "enhancement_start_snapshot_consistent": True,
            "enhancement_start_project_snapshot": start_snapshot,
            "enhancement_start_snapshot_fingerprint": sentinel._sha256(
                start_snapshot
            ),
            "expected_runtime_artifact_manifest": [],
        },
    }


def test_protocol_freezes_six_arm_crossed_sentinel_and_hard_budgets() -> None:
    protocol = sentinel.load_protocol()

    sentinel.validate_protocol(protocol)

    assert sentinel.build_schedule(protocol) == [
        {"ordinal": 1, "pair": 1, "position": 1, "scenario_id": "strongly_related_diagnosis", "arm": "current"},
        {"ordinal": 2, "pair": 1, "position": 2, "scenario_id": "strongly_related_diagnosis", "arm": "compact"},
        {"ordinal": 3, "pair": 2, "position": 1, "scenario_id": "partial_shared_criterion", "arm": "compact"},
        {"ordinal": 4, "pair": 2, "position": 2, "scenario_id": "partial_shared_criterion", "arm": "current"},
        {"ordinal": 5, "pair": 3, "position": 1, "scenario_id": "relevant_iteration_memory", "arm": "current"},
        {"ordinal": 6, "pair": 3, "position": 2, "scenario_id": "relevant_iteration_memory", "arm": "compact"},
    ]
    assert protocol["token_limits"] == {
        "per_arm_hard": 30_000,
        "per_pair_hard": 60_000,
        "provider_sentinel_hard": 180_000,
    }
    assert [
        item["observed_complete_tokens"]
        for item in protocol["prior_paid_diagnostics"]
    ] == [13_167, 13_373]
    assert protocol["cache_enabled"] is False
    assert protocol["execution"]["transport_retries"] == 0
    assert protocol["execution"]["length_recoveries"] == 0
    assert protocol["common_interventions"] == {
        "default_max_completion_tokens": 4096,
        "semantics": (
            "provider-neutral experiment fallback applied only when a typed "
            "request omits max_tokens"
        ),
    }
    assert sentinel.task_designer_observation is stage8._task_designer_observation


def test_stage8_observation_is_augmented_with_candidate_decisions() -> None:
    events = [
        {
            "event_type": "llm_requested",
            "payload": {
                "correlation": {"execution_id": "call-1"},
                "context_selection": {
                    "request_purpose": "iteration_task_design",
                    "final_prompt_tokens": 100,
                    "candidate_decisions": [
                        {"candidate_id": "stage9:sentinel", "action": "kept"}
                    ],
                },
                "trace_info": {
                    "completion_budget": {
                        "reserved_tokens": 700,
                        "remaining_tokens": 9000,
                    }
                },
                "reasoning_policy": {"mode": "disabled"},
            },
        },
        {
            "event_type": "llm_responded",
            "payload": {
                "correlation": {"execution_id": "call-1"},
                "response_metadata": {
                    "usage": {"input_tokens": 120, "output_tokens": 20}
                },
            },
        },
    ]

    observed = sentinel.task_designer_observation_with_decisions(events)

    assert observed["usage_observed"] is True
    assert observed["candidate_decisions"] == [
        {"candidate_id": "stage9:sentinel", "action": "kept"}
    ]


def test_preflight_is_read_only_and_has_zero_provider_calls(tmp_path: Path) -> None:
    before = set(tmp_path.iterdir())

    result = sentinel.preflight(output_dir=tmp_path / "not-created")

    assert set(tmp_path.iterdir()) == before
    assert result["provider_calls"] == 0
    assert result["offline_gate_passed"] is True
    assert result["execute_requires_explicit_flag"] is True
    assert result["runner_integration_ready"] is True
    assert result["core_pre_enhancement_baseline"]["exact_sentence_present"] is False
    assert result["initial_spend"]["prior_paid_campaign_total"] == 26_540
    assert result["initial_spend"]["remaining_pair_tokens"]["1"] == 33_460
    assert result["initial_spend"]["remaining_campaign_tokens"] == 153_460


def test_protocol_rejects_prior_paid_diagnostic_hash_drift() -> None:
    protocol = sentinel.load_protocol()
    protocol["prior_paid_diagnostics"][0]["campaign_record"]["sha256"] = (
        "sha256:tampered"
    )

    with pytest.raises(sentinel.ProviderSentinelError, match="hash mismatch"):
        sentinel.validate_protocol(protocol)


def test_build_run_command_uses_dynamic_remaining_hard_cap(tmp_path: Path) -> None:
    protocol = sentinel.load_protocol()
    schedule = sentinel.build_schedule(protocol)
    first = _record(tmp_path, schedule[0], total_tokens=25_000)

    command = sentinel.build_run_command(
        tmp_path / "next",
        schedule[1],
        protocol,
        records=[first],
    )

    assert command[command.index("--max-provider-tokens") + 1] == "8460"
    assert command[command.index("--default-max-completion-tokens") + 1] == "4096"
    assert command[command.index("--task-designer-context-arm") + 1] == "compact"
    assert command[command.index("--task-designer-scenario-id") + 1] == (
        "strongly_related_diagnosis"
    )
    assert command[command.index("--task-designer-source-protocol") + 1] == str(
        sentinel.PROTOCOL_PATH
    )

    assert sentinel.remaining_arm_hard_cap([], schedule[0], protocol) == 30_000
    assert sentinel.evaluate_spend_limits([], protocol)["cumulative_pair_totals"] == {
        "1": 26_540
    }


def test_build_run_command_uses_actual_parent_protocol_path(tmp_path: Path) -> None:
    protocol = sentinel.load_protocol()
    protocol_path = tmp_path / "custom-stage9.json"
    protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
    item = sentinel.build_schedule(protocol)[0]

    command = sentinel.build_run_command(
        tmp_path / "arm",
        item,
        protocol,
        records=[],
        protocol_path=protocol_path,
    )

    assert command[command.index("--task-designer-source-protocol") + 1] == str(
        protocol_path.resolve()
    )


def test_execute_rejects_parent_protocol_object_path_split(tmp_path: Path) -> None:
    protocol = sentinel.load_protocol()
    protocol["status"] = "different-parent-object"

    with pytest.raises(sentinel.ProviderSentinelError, match="protocol.*differ"):
        sentinel.execute_campaign(
            output_dir=tmp_path / "must-not-create",
            protocol=protocol,
            protocol_path=sentinel.PROTOCOL_PATH,
            run_arm=lambda *args: pytest.fail("arm must not start"),
        )

    assert not (tmp_path / "must-not-create").exists()


def test_default_subprocess_adapter_stops_after_stubbed_first_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        run_dir = Path(command[command.index("--output-dir") + 1])
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=2)

    monkeypatch.setattr(sentinel.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sentinel,
        "build_integrated_run_record",
        lambda run_dir, *, schedule_item, protocol, code_snapshot: _record(
            tmp_path, schedule_item, passed=False
        ),
    )

    with pytest.raises(sentinel.ProviderSentinelStopped, match="quality_gate_failed"):
        sentinel.execute_campaign(output_dir=tmp_path / "campaign")

    assert len(calls) == 1
    assert "--max-provider-tokens" in calls[0][0]


def test_scope_overlays_only_diagnosis_and_memory_and_synchronizes_goal(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = build_scenario_fixtures()["partial_shared_criterion"]
    project = tmp_path / "live-project"
    project.mkdir()
    calculator = project / "calculator.py"
    calculator.write_text("def divide(a, b): return a / b\n", encoding="utf-8")
    live_state = fixture.project_state.model_copy(
        update={
            "project_path": str(project),
            "safe_target_files": [str(calculator)],
            "file_summaries": [{"path": str(calculator), "preview": "LIVE"}],
            "validation_context": {
                "validation_passed": True,
                "required_commands": ["LIVE VALIDATION"],
                "product_intent": {"experience_type": "live-intent"},
            },
        }
    )
    live_report = {
        "diagnosis": {"summary": "LIVE DIAGNOSIS"},
        "prompt_context": {
            "allowed_write_files": [str(calculator)],
            "required_validation_commands": ["LIVE VALIDATION"],
            "product_intent": {"experience_type": "live-intent"},
        },
        "live_only": "preserved",
    }
    seen = []
    expected_boundary = sentinel.capture_enhancement_start_snapshot(project)

    def builder(**kwargs):
        seen.append(kwargs)
        calculator.write_text("builder mutation\n", encoding="utf-8")
        return [
            SimpleNamespace(
                candidate_id="stage9:partial-sentinel",
                content="S9_PARTIAL_DIAGNOSIS S9_SELECTED_METRIC",
            )
        ]

    class Agent:
        def _goal_from_candidate(self, selected_candidate, report, evaluation):
            raise AssertionError("original goal resolver should be patched")

    agent = Agent()
    monkeypatch.setattr(sentinel.iteration_agent_module, "build_iteration_task_design_candidates", builder)

    with sentinel.task_designer_scenario_scope("compact", fixture, agent=agent) as descriptor:
        resolved = agent._goal_from_candidate(None, {}, None)
        sentinel.iteration_agent_module.build_iteration_task_design_candidates(
            project_state=live_state,
            goal=resolved,
            improvement_report=live_report,
            completed_iteration=0,
        )

    assert resolved == fixture.goal
    overlaid = seen[0]
    assert overlaid["project_state"].project_path == str(project)
    assert overlaid["project_state"].safe_target_files == [str(calculator)]
    assert overlaid["project_state"].file_summaries[0]["preview"] == "LIVE"
    assert overlaid["project_state"].validation_context["required_commands"] == ["LIVE VALIDATION"]
    assert overlaid["project_state"].memory_records == fixture.project_state.memory_records
    assert overlaid["improvement_report"]["diagnosis"] == fixture.improvement_report["diagnosis"]
    assert overlaid["improvement_report"]["prompt_context"] == live_report["prompt_context"]
    assert overlaid["improvement_report"]["live_only"] == "preserved"
    assert overlaid["projection_policy"] == "compact"
    assert descriptor["source_fingerprint"] == sentinel.scenario_overlay_fingerprint(
        fixture
    )
    assert descriptor["goal_hash"] == fixture.goal_hash
    assert len(descriptor["runtime_contract_hashes"]) == 1
    assert descriptor["runtime_contract_hash"] == descriptor[
        "runtime_contract_hashes"
    ][0]
    assert descriptor["sentinel_contract_passed"] is True
    assert descriptor["sentinel_candidate_ids"]
    assert descriptor["enhancement_start_project_snapshot"]["files"] == (
        expected_boundary["files"]
    )
    assert descriptor["enhancement_start_snapshot_capture_count"] == 1
    assert descriptor["enhancement_start_snapshot_observation_count"] == 1
    assert descriptor["enhancement_start_snapshot_consistent"] is True
    assert descriptor["expected_runtime_artifact_manifest"] == []


def test_scope_reuses_identical_boundary_but_rejects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = build_scenario_fixtures()["partial_shared_criterion"]
    project = tmp_path / "project"
    project.mkdir()
    calculator = project / "calculator.py"
    calculator.write_text("before\n", encoding="utf-8")
    state = fixture.project_state.model_copy(
        update={"project_path": str(project), "safe_target_files": [str(calculator)]}
    )
    builder = lambda **kwargs: [
        SimpleNamespace(
            candidate_id="stage9:partial-sentinel",
            content="S9_PARTIAL_DIAGNOSIS S9_SELECTED_METRIC",
        )
    ]
    monkeypatch.setattr(
        sentinel.iteration_agent_module,
        "build_iteration_task_design_candidates",
        builder,
    )
    agent = SimpleNamespace(_goal_from_candidate=lambda *args: fixture.goal)

    with sentinel.task_designer_scenario_scope("compact", fixture, agent=agent) as descriptor:
        wrapped = sentinel.iteration_agent_module.build_iteration_task_design_candidates
        kwargs = {
            "project_state": state,
            "goal": fixture.goal,
            "improvement_report": fixture.improvement_report,
            "completed_iteration": 0,
        }
        wrapped(**kwargs)
        wrapped(**kwargs)
        assert descriptor["enhancement_start_snapshot_capture_count"] == 1
        assert descriptor["enhancement_start_snapshot_observation_count"] == 2
        calculator.write_text("drift\n", encoding="utf-8")
        with pytest.raises(sentinel.ProviderSentinelError, match="snapshot changed"):
            wrapped(**kwargs)
        assert descriptor["enhancement_start_snapshot_consistent"] is False


@pytest.mark.parametrize("invalid", ["truncated", "symlink_paths"])
def test_scope_rejects_untrustworthy_boundary_before_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    fixture = build_scenario_fixtures()["partial_shared_criterion"]
    project = tmp_path / "project"
    project.mkdir()
    state = fixture.project_state.model_copy(update={"project_path": str(project)})
    snapshot = {
        "project_root": str(project.resolve()),
        "files": {},
        "truncated": invalid == "truncated",
        "symlink_paths": [str(project / "link")] if invalid == "symlink_paths" else [],
    }
    monkeypatch.setattr(sentinel, "capture_enhancement_start_snapshot", lambda path: snapshot)
    monkeypatch.setattr(
        sentinel.iteration_agent_module,
        "build_iteration_task_design_candidates",
        lambda **kwargs: pytest.fail("production builder must not run"),
    )
    agent = SimpleNamespace(_goal_from_candidate=lambda *args: fixture.goal)

    with sentinel.task_designer_scenario_scope("compact", fixture, agent=agent):
        with pytest.raises(sentinel.ProviderSentinelError, match="untrustworthy"):
            sentinel.iteration_agent_module.build_iteration_task_design_candidates(
                project_state=state,
                goal=fixture.goal,
                improvement_report=fixture.improvement_report,
                completed_iteration=0,
            )


def test_canonical_quality_rejects_escape_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    calculator = root / "calculator.py"
    calculator.write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")

    assert sentinel.validate_modified_paths(root, [calculator]) == []
    assert "modified_path_outside_project" in sentinel.validate_modified_paths(root, [outside])

    calculator.unlink()
    calculator.symlink_to(outside)
    assert "modified_path_is_symlink" in sentinel.validate_modified_paths(root, [calculator])


def test_arm_gate_uses_observed_hash_diff_not_runtime_self_report(tmp_path: Path) -> None:
    protocol = sentinel.load_protocol()
    item = sentinel.build_schedule(protocol)[0]
    record = _record(tmp_path, item)
    unrelated = Path(record["project_root"]) / "unrelated.py"
    unrelated.write_text("changed\n", encoding="utf-8")
    record["observed_enhancement_mutations"]["all_changed_paths"].append(str(unrelated))
    record["observed_enhancement_mutations"]["added_paths"].append(str(unrelated))
    record["modified_paths"] = [str(unrelated)]

    reasons = sentinel.arm_stop_reasons(record, protocol)

    assert "observed_user_mutation_scope_mismatch" in reasons


def test_arm_gate_ignores_whole_run_core_artifacts_but_not_enhancement_metadata(
    tmp_path: Path,
) -> None:
    protocol = sentinel.load_protocol()
    item = sentinel.build_schedule(protocol)[0]
    record = _record(tmp_path, item)
    metadata = Path(record["project_root"]) / ".openpilot" / "project_stack.json"
    metadata.parent.mkdir()
    metadata.write_text("{}", encoding="utf-8")
    record["observed_project_mutations"]["all_changed_paths"].append(str(metadata))
    assert sentinel.arm_stop_reasons(record, protocol) == []

    record["observed_enhancement_mutations"]["all_changed_paths"].append(str(metadata))
    record["observed_enhancement_mutations"]["added_paths"].append(str(metadata))
    assert "runtime_owned_mutation_invalid" in sentinel.arm_stop_reasons(
        record, protocol
    )


def _production_runtime_artifacts(project_root: Path) -> tuple[Path, Path]:
    from memory.project_index import ProjectIndexManager

    manager = ProjectIndexManager(project_root)
    manager.update_directory_sketch(project_root)
    return (
        project_root / "sketch.json",
        manager.index_file_for(project_root / "calculator.py"),
    )


def _expected_manifest(project_root: Path) -> list[dict]:
    from run_observation import capture_bounded_project_snapshot

    return sentinel.expected_runtime_artifact_manifest(
        capture_bounded_project_snapshot(project_root)
    )


@pytest.mark.parametrize(
    ("mutation_update", "expected"),
    [
        ({"all_changed_paths": []}, "raw_all_changed_union_mismatch"),
        ({"added_paths": ["CALCULATOR"]}, "raw_mutation_category_overlap"),
        ({"modified_paths": ["CALCULATOR", "CALCULATOR"]}, "raw_mutation_duplicate"),
        ({"modified_paths": ["NONCANONICAL"], "all_changed_paths": ["NONCANONICAL"]}, "raw_mutation_path_noncanonical"),
        ({"modified_paths": ["OUTSIDE"], "all_changed_paths": ["OUTSIDE"]}, "raw_mutation_path_outside_project"),
    ],
)
def test_raw_mutation_descriptor_attacks_fail_closed(
    tmp_path: Path, mutation_update: dict, expected: str
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    calculator = root / "calculator.py"
    calculator.write_text("code\n", encoding="utf-8")
    values = {
        "CALCULATOR": str(calculator.resolve()),
        "NONCANONICAL": str(root / "nested" / ".." / "calculator.py"),
        "OUTSIDE": str((tmp_path / "outside.py").resolve()),
    }
    mutations = {
        "added_paths": [],
        "deleted_paths": [],
        "modified_paths": [str(calculator.resolve())],
        "all_changed_paths": [str(calculator.resolve())],
    }
    mutations.update(
        {
            key: [values.get(item, item) for item in value]
            for key, value in mutation_update.items()
        }
    )

    failures = sentinel.validate_raw_mutation_descriptor(root, mutations)

    assert expected in failures


def test_expected_runtime_manifest_is_frozen_from_start_snapshot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "calculator.py").write_text("code\n", encoding="utf-8")
    sketch, index = _production_runtime_artifacts(root)

    manifest = _expected_manifest(root)

    assert {item["path"] for item in manifest} >= {
        str(sketch.resolve()),
        str(index.resolve()),
    }
    assert {item["artifact_type"] for item in manifest} == {
        "directory_sketch",
        "file_content_index",
    }


def test_runtime_owned_mutation_classification_is_strict_and_preserves_raw_diff(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    calculator = root / "calculator.py"
    calculator.write_text("code\n", encoding="utf-8")
    readme = root / "README.md"
    readme.write_text("docs\n", encoding="utf-8")
    sketch, _ = _production_runtime_artifacts(root)
    index = root / ".openpilot" / "file_indexes" / "README.md.index.json"
    mutations = {
        "added_paths": [],
        "deleted_paths": [],
        "modified_paths": [str(calculator), str(sketch), str(index)],
        "all_changed_paths": [str(calculator), str(sketch), str(index)],
    }

    classified = sentinel.classify_enhancement_mutations(
        root, mutations, expected_artifacts=_expected_manifest(root)
    )

    assert classified["failures"] == []
    assert classified["user_owned_mutations"]["all_changed_paths"] == [
        str(calculator.resolve())
    ]
    assert classified["runtime_owned_mutations"]["all_changed_paths"] == sorted(
        [str(sketch.resolve()), str(index.resolve())]
    )
    assert mutations["all_changed_paths"] == [str(calculator), str(sketch), str(index)]


def test_arm_gate_accepts_only_verified_runtime_updates_plus_calculator(
    tmp_path: Path,
) -> None:
    protocol = sentinel.load_protocol()
    item = sentinel.build_schedule(protocol)[0]
    record = _record(tmp_path, item)
    root = Path(record["project_root"])
    sketch = root / "sketch.json"
    index = root / ".openpilot" / "file_indexes" / "calculator.py.index.json"
    sketch, index = _production_runtime_artifacts(root)
    record["observed_enhancement_mutations"]["modified_paths"].extend(
        [str(sketch), str(index)]
    )
    record["observed_enhancement_mutations"]["all_changed_paths"].extend(
        [str(sketch), str(index)]
    )
    start_snapshot = sentinel.capture_enhancement_start_snapshot(root)
    expected_manifest = sentinel.expected_runtime_artifact_manifest(start_snapshot)
    classified = sentinel.classify_enhancement_mutations(
        root,
        record["observed_enhancement_mutations"],
        expected_artifacts=expected_manifest,
    )
    intervention = record["task_designer_context_intervention"]
    intervention["enhancement_start_project_snapshot"] = start_snapshot
    intervention["enhancement_start_snapshot_fingerprint"] = sentinel._sha256(
        start_snapshot
    )
    intervention["expected_runtime_artifact_manifest"] = expected_manifest
    record["runtime_owned_mutations"] = classified["runtime_owned_mutations"]
    record["user_owned_mutations"] = classified["user_owned_mutations"]
    record["mutation_classification_failures"] = classified["failures"]
    record["producer_validation"] = classified["producer_validation"]

    assert sentinel.arm_stop_reasons(record, protocol) == []


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("unknown_openpilot", "unknown_openpilot_path"),
        ("bad_json", "runtime_owned_json_invalid"),
        ("bad_kind", "runtime_owned_kind_mismatch"),
        ("bad_source", "runtime_owned_source_mismatch"),
        ("bad_root", "runtime_owned_project_root_mismatch"),
        ("bad_relative", "runtime_owned_relative_path_invalid"),
        ("bad_mapping", "runtime_owned_relative_path_mismatch"),
        ("bad_file_path", "runtime_owned_file_path_mismatch"),
        ("symlink", "raw_mutation_path_noncanonical"),
    ],
)
def test_runtime_owned_mutation_classification_fails_closed(
    tmp_path: Path, case: str, expected: str
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "README.md"
    target.write_text("docs\n", encoding="utf-8")
    (root / "calculator.py").write_text("code\n", encoding="utf-8")
    _production_runtime_artifacts(root)
    index = root / ".openpilot" / "file_indexes" / "README.md.index.json"
    expected_manifest = _expected_manifest(root)
    if case == "unknown_openpilot":
        index = root / ".openpilot" / "unknown.json"
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text("{}", encoding="utf-8")
    elif case == "bad_json":
        index.write_text("not json", encoding="utf-8")
    elif case == "symlink":
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        index.unlink()
        index.symlink_to(outside)
    else:
        payload = json.loads(index.read_text(encoding="utf-8"))
        if case == "bad_kind":
            payload["kind"] = "other"
        elif case == "bad_source":
            payload["source"]["source_name"] = "attacker"
        elif case == "bad_root":
            payload["project_root"] = str(tmp_path)
        elif case == "bad_relative":
            payload["relative_path"] = "../README.md"
        elif case == "bad_mapping":
            payload["relative_path"] = "other.md"
        elif case == "bad_file_path":
            payload["file_path"] = str(tmp_path / "outside.md")
        index.write_text(json.dumps(payload), encoding="utf-8")
    mutations = {
        "added_paths": [],
        "deleted_paths": [],
        "modified_paths": [str(index)],
        "all_changed_paths": [str(index)],
    }

    classified = sentinel.classify_enhancement_mutations(
        root, mutations, expected_artifacts=expected_manifest
    )

    assert any(expected in failure for failure in classified["failures"])


@pytest.mark.parametrize(
    ("attack", "expected"),
    [
        ("added_runtime", "runtime_owned_add_delete_forbidden"),
        ("not_frozen", "runtime_owned_not_expected_at_start"),
        ("unknown_field", "runtime_owned_typed_schema_invalid"),
        ("coerced_type", "runtime_owned_typed_schema_invalid"),
        ("forged_hash", "runtime_owned_content_sha256_mismatch"),
        ("forged_size", "runtime_owned_byte_size_mismatch"),
        ("forged_lines", "runtime_owned_line_count_mismatch"),
        ("sketch_extra", "runtime_owned_sketch_files_mismatch"),
    ],
)
def test_runtime_owned_producer_validation_rejects_content_self_report_attacks(
    tmp_path: Path, attack: str, expected: str
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    calculator = root / "calculator.py"
    calculator.write_text("one\ntwo\n", encoding="utf-8")
    sketch, index = _production_runtime_artifacts(root)
    expected_manifest = _expected_manifest(root)
    path = sketch if attack == "sketch_extra" else index
    if attack == "not_frozen":
        expected_manifest = [item for item in expected_manifest if item["path"] != str(index.resolve())]
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if attack == "unknown_field":
            payload["attacker"] = True
        elif attack == "coerced_type":
            payload["byte_size"] = str(payload["byte_size"])
        elif attack == "forged_hash":
            payload["content_sha256"] = "0" * 64
        elif attack == "forged_size":
            payload["byte_size"] += 1
        elif attack == "forged_lines":
            payload["line_count"] += 1
        elif attack == "sketch_extra":
            payload["files"]["attacker.py"] = {"arbitrary": True}
        path.write_text(json.dumps(payload), encoding="utf-8")
    category = "added_paths" if attack == "added_runtime" else "modified_paths"
    mutations = {
        "added_paths": [str(path.resolve())] if category == "added_paths" else [],
        "deleted_paths": [],
        "modified_paths": [str(path.resolve())] if category == "modified_paths" else [],
        "all_changed_paths": [str(path.resolve())],
    }

    classified = sentinel.classify_enhancement_mutations(
        root, mutations, expected_artifacts=expected_manifest
    )

    assert any(expected in failure for failure in classified["failures"])
    assert classified["producer_validation"]["passed"] is False


def test_arm_gate_rejects_expected_manifest_tampering(tmp_path: Path) -> None:
    protocol = sentinel.load_protocol()
    item = sentinel.build_schedule(protocol)[0]
    record = _record(tmp_path, item)
    record["task_designer_context_intervention"][
        "expected_runtime_artifact_manifest"
    ] = [
        {
            "path": str(Path(record["project_root"]) / "calculator.py"),
            "artifact_type": "file_content_index",
            "relative_path": "calculator.py",
        }
    ]

    assert "expected_runtime_artifact_manifest_mismatch" in sentinel.arm_stop_reasons(
        record, protocol
    )


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        ({"snapshot_missing": True}, "enhancement_snapshot_missing"),
        ({"start_capture_count": 0}, "enhancement_snapshot_capture_invalid"),
        ({"start_capture_count": 2}, "enhancement_snapshot_capture_invalid"),
        ({"start_consistent": False}, "enhancement_snapshot_inconsistent"),
        ({"before_truncated": True}, "enhancement_snapshot_truncated"),
        ({"after_truncated": True}, "enhancement_snapshot_truncated"),
        ({"symlink_paths": ["/tmp/link"]}, "enhancement_snapshot_symlink"),
    ],
)
def test_arm_gate_fails_closed_on_invalid_enhancement_snapshot(
    tmp_path: Path, update: dict, reason: str
) -> None:
    protocol = sentinel.load_protocol()
    item = sentinel.build_schedule(protocol)[0]
    record = _record(tmp_path, item)
    record["observed_enhancement_mutations"].update(update)

    assert reason in sentinel.arm_stop_reasons(record, protocol)


def test_docstring_quality_requires_valueerror_and_denominator(tmp_path: Path) -> None:
    calculator = tmp_path / "calculator.py"
    calculator.write_text(
        'def divide(a, b):\n    """Raises ValueError when denominator is zero."""\n    return a / b\n',
        encoding="utf-8",
    )
    assert sentinel.validate_divide_docstring(calculator) == []

    calculator.write_text('def divide(a, b):\n    """Divide numbers."""\n    return a / b\n', encoding="utf-8")
    assert "divide_docstring_contract_missing" in sentinel.validate_divide_docstring(calculator)

    calculator.write_text(
        'def divide(a, b):\n    """Raises ValueError if denominator is zero."""\n    return a / b\n',
        encoding="utf-8",
    )
    assert "divide_docstring_contract_missing" in sentinel.validate_divide_docstring(
        calculator
    )


def test_first_failed_arm_stops_campaign_without_running_second(tmp_path: Path) -> None:
    calls = []

    def run_arm(item, run_dir, protocol):
        calls.append(item)
        return _record(tmp_path, item, passed=False)

    with pytest.raises(sentinel.ProviderSentinelStopped, match="quality_gate_failed"):
        sentinel.execute_campaign(
            output_dir=tmp_path / "campaign",
            run_arm=run_arm,
        )

    assert [item["ordinal"] for item in calls] == [1]
    run_dir = (
        tmp_path
        / "campaign"
        / "01_p1_strongly_related_diagnosis_current"
    )
    persisted_record = json.loads(
        (run_dir / "campaign_record.json").read_text(encoding="utf-8")
    )
    state = json.loads(
        (tmp_path / "campaign" / "campaign_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted_record["ordinal"] == 1
    assert state["status"] == "stopped"
    assert len(state["records"]) == 1
    assert "quality_gate_failed" in state["stop_reasons"]
    assert state["spend"]["formal_campaign_total"] == 10_000
    assert state["spend"]["prior_paid_campaign_total"] == 26_540
    assert state["spend"]["cumulative_campaign_total"] == 36_540


def test_completed_stub_campaign_persists_all_records_and_final_spend(
    tmp_path: Path,
) -> None:
    def run_arm(item, run_dir, protocol):
        return _record(tmp_path, item, total_tokens=9_000)

    result = sentinel.execute_campaign(
        output_dir=tmp_path / "completed-campaign",
        run_arm=run_arm,
    )
    state = json.loads(
        (tmp_path / "completed-campaign" / "campaign_state.json").read_text(
            encoding="utf-8"
        )
    )

    assert result["eligible"] is True
    assert state["status"] == "completed"
    assert len(state["records"]) == 6
    assert state["spend"]["formal_campaign_total"] == 54_000
    assert state["spend"]["cumulative_campaign_total"] == 80_540
    assert state["stop_reasons"] == []
    assert len(
        list((tmp_path / "completed-campaign").glob("*/campaign_record.json"))
    ) == 6


def test_pair_and_campaign_spend_are_checked_from_observed_usage(tmp_path: Path) -> None:
    protocol = sentinel.load_protocol()
    records = []
    for item in sentinel.build_schedule(protocol)[:2]:
        records.append(_record(tmp_path, item, total_tokens=30_001))

    spend = sentinel.evaluate_spend_limits(records, protocol)

    assert "pair_1_hard_limit_exceeded" in spend["hard_failures"]
    assert spend["formal_campaign_total"] == 60_002
    assert spend["cumulative_campaign_total"] == 86_542


def test_arm_gate_rejects_sentinel_tampering_and_per_arm_overspend(tmp_path: Path) -> None:
    protocol = sentinel.load_protocol()
    item = sentinel.build_schedule(protocol)[0]
    record = _record(tmp_path, item, total_tokens=30_001)
    record["task_designer_context_intervention"]["sentinel_contract_passed"] = False

    reasons = sentinel.arm_stop_reasons(record, protocol)

    assert "candidate_sentinel_contract_failed" in reasons
    assert "per_arm_token_hard_limit_exceeded" in reasons

    record["task_designer_context_intervention"]["sentinel_contract_passed"] = True
    record["task_designer"]["candidate_decisions"][0]["action"] = "partially_kept"
    assert "candidate_sentinel_not_fully_kept" in sentinel.arm_stop_reasons(
        record, protocol
    )


@pytest.mark.parametrize(
    ("guard_update", "reason"),
    [
        ({"default_max_completion_tokens": 2048}, "guard_default_completion_mismatch"),
        ({"effective_max_completion_tokens": []}, "guard_effective_completion_missing"),
        ({"usage_censored": True}, "guard_usage_censored"),
        ({"unsettled_reserved_tokens": 1}, "guard_unsettled_reservation"),
        ({"reservation_overrun_tokens": 1}, "guard_reservation_overrun"),
        ({"blocked_requests_before_transport": 1}, "guard_request_blocked"),
        ({"reservation_admission_failed": True}, "guard_reservation_admission_failed"),
        ({"blocked_reservation_tokens": 1}, "guard_reservation_admission_failed"),
        (
            {"blocking_limits": ["request_token_reservation_blocked"]},
            "guard_reservation_admission_failed",
        ),
    ],
)
def test_arm_gate_rejects_guard_uncertainty_or_reservation_failure(
    tmp_path: Path, guard_update: dict, reason: str
) -> None:
    protocol = sentinel.load_protocol()
    item = sentinel.build_schedule(protocol)[0]
    record = _record(tmp_path, item)
    record["guard_observation"] = {
        "default_max_completion_tokens": 4096,
        "defaulted_max_completion_request_count": 1,
        "effective_max_completion_tokens": [4096],
        "logical_complete_calls_seen": 1,
        "usage_censored": False,
        "unsettled_reserved_tokens": 0,
        "reservation_overrun_tokens": 0,
        "blocked_requests_before_transport": 0,
        "reservation_admission_failed": False,
        "blocked_reservation_tokens": 0,
        "blocking_limits": [],
        **guard_update,
    }

    assert reason in sentinel.arm_stop_reasons(record, protocol)


def test_pair_gate_rejects_runtime_contract_drift(tmp_path: Path) -> None:
    protocol = sentinel.load_protocol()
    first, second = sentinel.build_schedule(protocol)[:2]
    records = [_record(tmp_path, first), _record(tmp_path, second)]
    records[1]["runtime_contract_hash"] = "sha256:drifted"

    assert sentinel.pair_stop_reasons(records, pair=1) == [
        "runtime_contract_hash_mismatch"
    ]


def test_stage8_code_snapshot_roots_cover_stage9_runner_and_protocol() -> None:
    from stage7_campaign import REPO_ROOT, _excluded

    stage8_protocol = stage8.load_campaign_protocol()
    excludes = stage8_protocol["code_snapshot_excludes"]
    roots = [
        (REPO_ROOT / root).resolve()
        for root in stage8_protocol["code_snapshot_roots"]
    ]
    for path in (Path(sentinel.__file__).resolve(), sentinel.PROTOCOL_PATH.resolve()):
        relative = path.relative_to(REPO_ROOT)
        assert any(path.is_relative_to(root) for root in roots)
        assert _excluded(relative, excludes) is False
