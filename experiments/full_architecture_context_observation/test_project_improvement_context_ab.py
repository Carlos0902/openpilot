from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
import project_improvement_context_ab as experiment

from project_improvement_context_ab import (
    FrozenInputError,
    HardGateFailure,
    assert_hard_gates,
    load_frozen_inputs,
    load_protocol,
    run_counterfactual,
)


def test_frozen_inputs_merge_project_state_and_later_context_with_verified_hashes() -> None:
    protocol = load_protocol()
    frozen = load_frozen_inputs(protocol)

    assert frozen.project_state.memory_context == frozen.context_loader_context
    assert frozen.completed_iteration == 0
    assert frozen.selected_goal.id == "seed_action_1"
    assert frozen.source_event_sequences == {
        "project_state": 44,
        "context_loader": 45,
        "improvement_report": 48,
        "selected_goal": 51,
    }


def test_frozen_input_hash_mismatch_fails_closed() -> None:
    protocol = deepcopy(load_protocol())
    protocol["source_events"]["project_state"]["payload_sha256"] = "sha256:" + ("0" * 64)

    with pytest.raises(FrozenInputError, match="project_state payload hash mismatch"):
        load_frozen_inputs(protocol)


def test_frozen_tokenizer_unavailable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        experiment.ProviderTokenCounter,
        "from_settings",
        lambda settings: SimpleNamespace(
            available=False,
            tokenizer_id="unavailable",
            model=settings.model,
        ),
    )

    with pytest.raises(FrozenInputError, match="frozen provider tokenizer is unavailable"):
        run_counterfactual(enforce_hard_gates=False)


def test_offline_counterfactual_reproduces_legacy_failure_and_current_selection() -> None:
    result = run_counterfactual(enforce_hard_gates=False)

    assert result["instrumentation_valid"] is True
    assert result["legacy"] == {
        "assembly_status": "budget_insufficient",
        "original_prompt_chars": 96209,
        "original_prompt_tokens": 29342,
        "final_prompt_tokens": 0,
        "omitted_required_candidate_ids": ["legacy:iteration_task_design:message:1"],
    }
    assert result["current"]["assembly_status"] == "ready"
    assert result["current"]["final_prompt_tokens"] == 3967
    assert result["current"]["omitted_required_candidate_ids"] == []
    assert result["current"]["decision_coverage"] == 1.0
    assert result["current"]["required_partial_count"] == 0
    assert result["current"]["selected_candidate_count"] > 0
    assert result["current"]["original_prompt_tokens"] > result["current"]["final_prompt_tokens"]
    assert result["current"]["partial_candidate_count"] > 0


def test_current_selected_required_safety_preserves_authoritative_constraints() -> None:
    result = run_counterfactual(enforce_hard_gates=False)
    gates = result["hard_gates"]

    assert gates["required_non_regression_constraints"] == [
        "Repair runtime, warning, environment, and quality issues without changing the intended user experience.",
        "Preserve delivery surface: project_native.",
        "Preserve runtime mode: best_fit_for_goal.",
    ]
    assert gates["missing_non_regression_constraints"] == []
    assert gates["passed"] is True


def test_hard_gate_failure_rejects_experiment_instead_of_becoming_a_warning() -> None:
    with pytest.raises(HardGateFailure, match="required project-improvement context gate failed"):
        assert_hard_gates(
            {
                "passed": False,
                "missing_non_regression_constraints": ["Preserve delivery surface: project_native."],
            }
        )


def test_missing_required_safety_candidate_rejects_the_experiment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_builder = experiment.build_iteration_task_design_candidates

    def without_safety(**kwargs):
        return [
            candidate
            for candidate in original_builder(**kwargs)
            if candidate.candidate_id != "iteration_task_design:safety"
        ]

    monkeypatch.setattr(
        experiment,
        "build_iteration_task_design_candidates",
        without_safety,
    )

    with pytest.raises(HardGateFailure, match="safety_candidate_selected"):
        run_counterfactual()
