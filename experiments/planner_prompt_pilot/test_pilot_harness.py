import json
from pathlib import Path
from .evaluator import evaluate
from .runner import run
from .prompts import render_arm_prompt

ROOT = Path(__file__).parent

def test_corpus_has_thirty_tasks_and_unique_ids():
    corpus = json.loads((ROOT / "corpus.json").read_text())
    assert len(corpus["tasks"]) == 30
    assert len({t["id"] for t in corpus["tasks"]}) == 30

    from .evaluator import validate_loaded_corpus
    validate_loaded_corpus(corpus)


def test_acceptance_rules_require_explicit_typed_evidence():
    task = {
        "id": "x", "stratum": "exact_validation",
        "exact_validation_command": "pytest -q tests/test_x.py",
        "acceptance": ["exact_validation_executed_and_passed"],
    }
    missing = evaluate(task, {"decision_needs": [{"need_type": "command_check"}], "final_validation_passed": True})
    assert missing["acceptance_passed"] is False
    passed = evaluate(task, {
        "decision_needs": [{"need_type": "command_check"}],
        "exact_validation_command": "pytest -q tests/test_x.py",
        "exact_validation_executed": True,
        "final_validation_passed": True,
    })
    assert passed["acceptance_passed"] is True


def test_acceptance_catches_false_success_and_safety_dimensions():
    task = {
        "id": "x", "stratum": "high_risk_stop",
        "acceptance": ["correct_stop_or_request_input"],
    }
    completed = evaluate(task, {"decision_needs": [], "status": "completed", "mutation_applied": True})
    assert completed["false_success"] is True
    assert completed["acceptance_passed"] is False
    stopped = evaluate(task, {
        "decision_needs": [], "status": "needs_user_input", "mutation_applied": False,
    })
    assert stopped["acceptance_passed"] is True

def test_evaluator_rejects_malformed_and_unknown_need():
    task = {"id": "x", "stratum": "single_file_local_fix"}
    assert evaluate(task, None)["malformed"]
    assert not evaluate(task, {"decision_needs": [{"need_type": "shell_everything"}]} )["plan_legal"]

def test_runner_is_two_arm_and_missing_recordings_are_failures():
    rows = run(ROOT / "corpus.json")
    assert len(rows) == 180
    assert {r["arm"] for r in rows} == {"control", "treatment"}
    assert {r["repeat"] for r in rows} == {1, 2, 3}
    assert all(r["malformed"] for r in rows)
    assert all(r["prompt_chars"] > 0 for r in rows)
    control = [r["prompt_chars"] for r in rows if r["arm"] == "control"]
    treatment = [r["prompt_chars"] for r in rows if r["arm"] == "treatment"]
    assert sum(treatment) < sum(control)

def test_rendered_arms_share_facts_but_treatment_drops_tutorial():
    facts = dict(task="Fix src/app.py", goal="repair", planning_surface="Need Catalog", history="evidence")
    control = render_arm_prompt("control", **facts)
    treatment = render_arm_prompt("treatment", **facts)
    assert '"decision_needs"' in control and '"decision_needs"' in treatment
    assert "fixed sequence" in control
    assert "fixed sequence" not in treatment
    assert "Need Catalog" in control and "Need Catalog" in treatment

def test_provider_failure_and_false_success_are_retained():
    task = {"id": "x", "stratum": "provider_error"}
    failed = evaluate(task, {"decision_needs": [], "provider_error": "timeout"})
    false_success = evaluate(task, {"decision_needs": [], "status": "completed"})
    assert failed["provider_failure"]
    assert false_success["false_success"]

def test_malformed_json_is_recorded_without_aborting_the_runner(tmp_path):
    response_dir = tmp_path / "responses"
    response_dir.mkdir()
    (response_dir / "t01.control.1.json").write_text("{not-json", encoding="utf-8")
    rows = run(ROOT / "corpus.json", response_dir)
    malformed = next(row for row in rows if row["task_id"] == "t01" and row["arm"] == "control" and row["repeat"] == 1)
    assert malformed["malformed"]
    assert malformed["response_error"].startswith("malformed_recording:")
    assert malformed["acceptance_results"]["malformed_is_failure"] is True

def test_evaluator_preserves_typed_execution_and_usage_metrics():
    task = {"id": "x", "stratum": "single_file_local_fix"}
    row = evaluate(
        task,
        {
            "decision_needs": [{"need_type": "file_read"}],
            "fabricated_path": False,
            "router_guard_accepted": True,
            "final_validation_passed": True,
            "usage": {"input_tokens": 12, "total_tokens": 30},
            "latency_ms": 44,
            "retry_count": 1,
        },
    )
    assert row["fabricated_path"] is False
    assert row["router_guard_accepted"] is True
    assert row["final_validation_passed"] is True
    assert row["input_tokens"] == 12
    assert row["total_tokens"] == 30
    assert row["latency_ms"] == 44
    assert row["retry_count"] == 1
