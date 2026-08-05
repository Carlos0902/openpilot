from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from metadata import ContextCandidateRetention

import stage8_task_designer_context_campaign as campaign


def _record(pair: int, arm: str, input_tokens: int, prompt_tokens: int) -> dict:
    reservation = 1150 if prompt_tokens >= 3000 else 1000
    return {
        "pair": pair,
        "arm": arm,
        "code_snapshot_sha256": "sha256:fixed",
        "provider_identity": campaign.load_campaign_protocol()["provider_identity"],
        "source_fingerprint": campaign.load_campaign_protocol()[
            "frozen_task_designer_source"
        ]["normalized_source_fingerprint"],
        "projection_policy": arm,
        "quality_gate": {
            "passed": True,
            "signature": "sha256:equal-quality",
            "checks": {"required_commands": True, "mutation_scope": True},
        },
        "quality_equivalence": {
            "signature": "sha256:equal-task-contract",
            "task_designer_contract_passed": True,
            "task_count": 1,
            "goal_ids": ["campaign-divide-docstring"],
            "task_target_basenames": ["calculator.py"],
            "acceptance_criteria": campaign.load_campaign_protocol()[
                "frozen_task_designer_source"
            ]["goal"]["acceptance_criteria"],
            "evidence_roles": ["goal", "safety", "validation", "project_file"],
            "authorized_targets": True,
            "final_modified_file_hashes_descriptive": {
                "calculator.py": f"sha256:{arm}-wording"
            },
        },
        "overall_usage_coverage": 1.0,
        "unknown_failed_usage_count": 0,
        "transport_retry_count": 0,
        "effective_arm_policy_matches": True,
        "memory_baseline_matches": True,
        "effective_completion_policy": (
            campaign.RuntimeBudgetMetadata().enhancement_completion_policy.model_dump(
                mode="json"
            )
        ),
        "task_designer": {
            "request_count": 1,
            "provider_input_tokens": input_tokens,
            "provider_output_tokens": 120,
            "final_prompt_tokens": prompt_tokens,
            "reserved_completion_tokens": reservation,
            "remaining_tokens_after_reservation": 9000,
            "complexity": "routine",
            "remaining_value": "high",
            "remaining_calls": 3,
            "reasoning_mode": "disabled",
            "transport_retries": 0,
            "omitted_required_candidate_ids": [],
            "required_partial_count": 0,
            "usage_observed": True,
        },
        "usage": {
            "enhancement": {"total_tokens": 5000},
            "lifecycle": {"total_tokens": 10000},
        },
    }


def test_protocol_freezes_crossed_order_and_only_projection_policy_varies() -> None:
    protocol = campaign.load_campaign_protocol()

    campaign.validate_campaign_protocol(protocol)

    assert campaign.build_schedule(protocol) == [
        {"ordinal": 1, "pair": 1, "position": 1, "arm": "current"},
        {"ordinal": 2, "pair": 1, "position": 2, "arm": "compact"},
        {"ordinal": 3, "pair": 2, "position": 1, "arm": "compact"},
        {"ordinal": 4, "pair": 2, "position": 2, "arm": "current"},
        {"ordinal": 5, "pair": 3, "position": 1, "arm": "current"},
        {"ordinal": 6, "pair": 3, "position": 2, "arm": "compact"},
    ]
    assert protocol["common_interventions"] == {
        "completion_policy": "production_dynamic_policy",
        "fixed_iteration_goal": "fixed_divide_docstring",
        "memory_baseline": "isolated_empty",
        "task_designer_source": "frozen_verified_v4_payloads",
    }
    assert protocol["task_designer_call_contract"] == {
        "request_count": 1,
        "reservation_derivation": "production_enhancement_completion_budget",
        "reservation_must_match_derived_value": True,
        "prompt_bonus_threshold": 3000,
        "complexity": "routine",
        "remaining_value": "high",
        "remaining_calls": 3,
        "reasoning_mode": "disabled",
        "transport_retries": 0,
        "required_omitted_count": 0,
        "required_partial_count": 0,
    }
    assert protocol["diagnostic_only_runs"][0]["path"] == (
        "runs/stage8_campaign_20260804T084822Z"
    )


def test_frozen_sources_verify_file_and_payload_hashes() -> None:
    protocol = campaign.load_campaign_protocol()

    frozen = campaign.load_frozen_task_designer_source(protocol)

    assert frozen.goal.id == "campaign-divide-docstring"
    assert frozen.completed_iteration == 0
    assert frozen.project_state.project_path == "."
    assert frozen.project_state.safe_target_files == ["./calculator.py"]
    assert "./calculator.py" in {
        summary.get("path") for summary in frozen.project_state.file_summaries
    }
    assert all(
        not str(summary.get("path") or "").startswith(("/var/", "/private/var/"))
        for summary in frozen.project_state.file_summaries
    )
    assert "/openpilot-context-observe-20z64t8k" not in str(
        frozen.project_state.model_dump(mode="json")
    )
    assert len(frozen.project_state.memory_records) == 2
    assert frozen.improvement_report.get("diagnosis")
    assert frozen.source_fingerprint.startswith("sha256:")
    assert frozen.source_event_sequences == {
        "project_state": 47,
        "improvement_report": 52,
    }


def test_frozen_source_hash_mismatch_fails_closed() -> None:
    protocol = deepcopy(campaign.load_campaign_protocol())
    protocol["frozen_task_designer_source"]["project_state"]["payload_sha256"] = (
        "sha256:" + "0" * 64
    )

    with pytest.raises(campaign.FrozenSourceError, match="project_state payload hash mismatch"):
        campaign.load_frozen_task_designer_source(protocol)


def test_intervention_passes_explicit_production_projection_policy(monkeypatch) -> None:
    frozen = campaign.load_frozen_task_designer_source(campaign.load_campaign_protocol())
    seen = []

    def builder(**kwargs):
        seen.append(kwargs)
        return [kwargs["projection_policy"]]

    monkeypatch.setattr(campaign.iteration_agent_module, "build_iteration_task_design_candidates", builder)

    with campaign.task_designer_context_scope("compact", frozen) as descriptor:
        result = campaign.iteration_agent_module.build_iteration_task_design_candidates(
            project_state=SimpleNamespace(),
            goal=SimpleNamespace(),
            improvement_report={},
            completed_iteration=9,
        )

    assert result == ["compact"]
    assert seen == [
        {
            "project_state": frozen.project_state,
            "goal": frozen.goal,
            "improvement_report": frozen.improvement_report,
            "completed_iteration": frozen.completed_iteration,
            "projection_policy": "compact",
        }
    ]
    assert descriptor["source_fingerprint"] == frozen.source_fingerprint
    assert descriptor["projection_policy"] == "compact"


def test_production_policies_share_required_facts_and_compact_optional_evidence() -> None:
    frozen = campaign.load_frozen_task_designer_source(campaign.load_campaign_protocol())
    builder = campaign.iteration_agent_module.build_iteration_task_design_candidates
    common = {
        "project_state": frozen.project_state,
        "goal": frozen.goal,
        "improvement_report": frozen.improvement_report,
        "completed_iteration": frozen.completed_iteration,
    }

    current = builder(**common, projection_policy="current")
    compact = builder(**common, projection_policy="compact")

    current_required = [
        (candidate.candidate_id, candidate.content)
        for candidate in current
        if candidate.retention == ContextCandidateRetention.REQUIRED
    ]
    compact_required = [
        (candidate.candidate_id, candidate.content)
        for candidate in compact
        if candidate.retention == ContextCandidateRetention.REQUIRED
    ]
    assert compact_required == current_required
    assert sum(len(candidate.content) for candidate in compact) < sum(
        len(candidate.content) for candidate in current
    )
    assert any("calculator.py" in candidate.content for candidate in compact)
    assert all(
        "/openpilot-context-observe-20z64t8k" not in candidate.content
        for candidate in current + compact
    )


def test_intervention_records_typed_budget_request_facts(monkeypatch) -> None:
    frozen = campaign.load_frozen_task_designer_source(campaign.load_campaign_protocol())

    class Agent:
        def _complete_json_candidates(self, candidates, **kwargs):
            return {"task": {}}, set()

    agent = Agent()
    with campaign.task_designer_context_scope(
        "compact", frozen, agent=agent
    ) as descriptor:
        agent._complete_json_candidates(
            [],
            purpose=campaign.ContextRequestPurpose.ITERATION_TASK_DESIGN,
            complexity=campaign.EnhancementCompletionComplexity.ROUTINE,
            remaining_calls=3,
            remaining_value=campaign.EnhancementCompletionDecisionValue.HIGH,
        )

    assert descriptor["observed_request_policies"] == [
        {
            "complexity": "routine",
            "remaining_value": "high",
            "remaining_calls": 3,
        }
    ]


def test_analysis_uses_task_designer_input_as_primary_and_requires_all_gates() -> None:
    protocol = campaign.load_campaign_protocol()
    records = []
    for pair in range(1, 4):
        records.extend((_record(pair, "current", 4000, 3968), _record(pair, "compact", 1800, 1768)))

    result = campaign.analyze_campaign_records(records, protocol)

    assert result["eligible"] is True
    assert result["quality_matched_pair_count"] == 3
    assert result["primary_metrics"]["provider_input_tokens"] == {
        "current": 12000,
        "compact": 5400,
        "change_fraction": -0.55,
    }
    assert result["primary_metrics"]["median_pair_input_reduction_fraction"] == 0.55
    assert result["success_thresholds_passed"] is True
    assert result["claim_boundary"] == "fixed_source_full_architecture_mechanism_only"


def test_dynamic_reservation_is_derived_from_prompt_and_typed_request_facts() -> None:
    protocol = campaign.load_campaign_protocol()
    current = _record(1, "current", 3993, 3968)
    compact = _record(1, "compact", 926, 901)

    assert campaign.derive_expected_task_designer_reservation(current, protocol) == 1150
    assert campaign.derive_expected_task_designer_reservation(compact, protocol) == 1000
    assert campaign._call_contract_reasons(current, protocol) == []
    assert campaign._call_contract_reasons(compact, protocol) == []


def test_core_mutation_hash_is_descriptive_and_does_not_exclude_pair() -> None:
    protocol = campaign.load_campaign_protocol()
    records = []
    for pair in range(1, 4):
        current = _record(pair, "current", 4000, 3968)
        compact = _record(pair, "compact", 1800, 1768)
        current["quality_equivalence"]["final_modified_file_hashes_descriptive"] = {
            "calculator.py": f"sha256:current-{pair}"
        }
        compact["quality_equivalence"]["final_modified_file_hashes_descriptive"] = {
            "calculator.py": f"sha256:compact-{pair}"
        }
        records.extend((current, compact))

    assert campaign.analyze_campaign_records(records, protocol)["eligible"] is True


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda r: r.update(source_fingerprint="sha256:different"), "source_fingerprint_mismatch"),
        (lambda r: r["quality_equivalence"].update(task_designer_contract_passed=False), "task_designer_quality_contract_failed"),
        (lambda r: r["quality_equivalence"].update(acceptance_criteria=["weaker"]), "task_designer_quality_contract_failed"),
        (lambda r: r["quality_equivalence"].update(evidence_roles=["goal"]), "task_designer_quality_contract_failed"),
        (lambda r: r["quality_equivalence"].update(task_target_basenames=["test_calculator.py"]), "task_designer_quality_contract_failed"),
        (lambda r: r["task_designer"].update(reserved_completion_tokens=1200), "task_designer_call_contract_mismatch"),
        (lambda r: r["task_designer"].update(usage_observed=False), "task_designer_usage_missing"),
        (lambda r: r["task_designer"].update(omitted_required_candidate_ids=["required"]), "task_designer_required_context_failure"),
        (lambda r: r.update(overall_usage_coverage=0.8), "incomplete_usage_coverage"),
        (lambda r: r.update(unknown_failed_usage_count=1), "unknown_failed_usage"),
        (lambda r: r.update(transport_retry_count=1), "transport_retry_observed"),
        (lambda r: r.update(effective_arm_policy_matches=False), "effective_arm_policy_mismatch"),
        (lambda r: r.update(effective_completion_policy=None), "effective_completion_policy_missing"),
        (lambda r: r.update(memory_baseline_matches=False), "memory_baseline_mismatch"),
        (lambda r: r.update(effective_completion_policy={"different": True}), "effective_completion_policy_mismatch"),
    ],
)
def test_analysis_excludes_pair_on_confound_or_incomplete_evidence(mutation, reason) -> None:
    protocol = campaign.load_campaign_protocol()
    records = []
    for pair in range(1, 4):
        records.extend((_record(pair, "current", 4000, 3968), _record(pair, "compact", 1800, 1768)))
    mutation(records[1])

    result = campaign.analyze_campaign_records(records, protocol)

    assert result["eligible"] is False
    assert reason in result["excluded_pairs"]["1"]


def test_run_command_fixes_dynamic_policy_goal_memory_and_source(tmp_path: Path) -> None:
    command = campaign.build_run_command(tmp_path, "compact", campaign.load_campaign_protocol())

    assert command[command.index("--enhancement-budget-arm") + 1] == "dynamic"
    assert command[command.index("--iteration-goal-mode") + 1] == "fixed_divide_docstring"
    assert command[command.index("--memory-mode") + 1] == "isolated_empty"
    assert command[command.index("--task-designer-context-arm") + 1] == "compact"
    assert "--task-designer-source-protocol" in command
