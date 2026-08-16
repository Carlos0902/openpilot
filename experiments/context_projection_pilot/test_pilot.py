import json
from pathlib import Path
from .evaluator import evaluate
from .renderer import REQUIRED, render_projection
from .runner import run
from .analysis import analyze_rows

ROOT = Path(__file__).parent

def test_corpus_and_repeated_two_arm_shape():
    corpus = json.loads((ROOT / "corpus.json").read_text())
    assert len(corpus["tasks"]) == 12
    rows = run(ROOT / "corpus.json")
    assert len(rows) == 72
    assert {row["arm"] for row in rows} == {"control", "treatment"}
    assert {row["repeat"] for row in rows} == {1, 2, 3}

def test_treatment_keeps_required_and_drops_unrelated_evidence():
    task = {"id": "x", "stratum": "diagnosis_irrelevant", "relevant_evidence": [], "artifact_status": "valid"}
    control = render_projection(task, "control")
    treatment = render_projection(task, "treatment")
    assert set(REQUIRED).issubset(treatment["selected_fact_ids"])
    assert len(treatment["selected_fact_ids"]) < len(control["selected_fact_ids"])
    assert "memory" not in treatment["selected_fact_ids"]
    assert evaluate(task, treatment, arm="treatment")["context_assembly_failure"] is False

def test_relevant_evidence_is_retained():
    task = {"id": "x", "stratum": "on_demand_body", "relevant_evidence": ["support_body"], "artifact_status": "valid"}
    projection = render_projection(task, "treatment")
    assert "support_body" in projection["selected_fact_ids"]
    assert evaluate(task, projection, arm="treatment")["relevant_evidence_recall"]

def test_corrupt_or_incomplete_artifact_falls_back_current_source_view():
    for status in ("corrupt", "missing_required"):
        task = {"id": "x", "stratum": "compact", "relevant_evidence": ["history"], "artifact_status": status}
        projection = render_projection(task, "treatment")
        row = evaluate(task, projection, arm="treatment")
        assert projection["fallback_used"] and projection["mode"] == "current_source_view"
        assert not projection["ready"] and not row["context_assembly_failure"]
        assert set(REQUIRED + ("history",)) <= set(projection["selected_fact_ids"])

def test_evaluator_rejects_unknown_duplicate_and_missing_required_ids():
    task = {"id": "x", "stratum": "x", "relevant_evidence": [], "artifact_status": "valid"}
    bad = {"selected_fact_ids": ["safety", "safety", "unknown"], "facts": {"safety": "x", "unknown": "x"}, "ready": True}
    row = evaluate(task, bad, arm="treatment")
    assert row["context_assembly_failure"] and not row["evidence_id_legal"]

def test_full_pilot_has_no_deterministic_gate_failures_and_saves_tokens():
    rows = run(ROOT / "corpus.json")
    assert all(not row["context_assembly_failure"] for row in rows)
    control = sum(row["input_chars"] for row in rows if row["arm"] == "control")
    treatment = sum(row["input_chars"] for row in rows if row["arm"] == "treatment")
    assert treatment < control

def test_analysis_reports_mechanism_only_result_and_no_noninferiority_claim():
    report = analyze_rows(run(ROOT / "corpus.json"))
    assert report["decision"] == "mechanism_canary_passed"
    assert report["rows"] == 72
    assert report["deterministic_failure_count"] == 0
    assert report["input_reduction_fraction"] >= 0.20
    assert report["noninferiority_status"] == "not_estimated"
