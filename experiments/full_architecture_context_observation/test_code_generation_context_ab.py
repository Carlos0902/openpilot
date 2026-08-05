from __future__ import annotations

from code_generation_context_ab import build_frozen_request, run_counterfactual


def test_frozen_request_reconstructs_the_failed_full_architecture_input() -> None:
    request = build_frozen_request()

    assert len(str(request.prompt_context)) > 30_000
    assert request.prompt_context["operation_kind"] == "file_replace"
    assert "def divide" in request.prompt_context["project_context"]["current_code_context"]
    assert "Existing behavior of imported" in " ".join(request.prompt_context["acceptance_criteria"])


def test_offline_code_generation_counterfactual_passes_budget_and_quality_gates() -> None:
    result = run_counterfactual()

    assert result["legacy"]["assembly_status"] == "budget_insufficient"
    assert result["legacy"]["final_prompt_tokens"] == 0
    assert result["current"]["assembly_status"] == "ready"
    assert result["current"]["omitted_required_candidate_ids"] == []
    assert result["current"]["final_prompt_tokens"] < 3968
    assert result["hard_gates"]["passed"] is True
