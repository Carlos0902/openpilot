import json
from pathlib import Path
from .evaluator import evaluate
from .runner import run

ROOT = Path(__file__).parent

def test_corpus_has_thirty_tasks_and_unique_ids():
    corpus = json.loads((ROOT / "corpus.json").read_text())
    assert len(corpus["tasks"]) == 30
    assert len({t["id"] for t in corpus["tasks"]}) == 30

def test_evaluator_rejects_malformed_and_unknown_need():
    task = {"id": "x", "stratum": "single_file_local_fix"}
    assert evaluate(task, None)["malformed"]
    assert not evaluate(task, {"decision_needs": [{"need_type": "shell_everything"}]} )["plan_legal"]

def test_runner_is_two_arm_and_missing_recordings_are_failures():
    rows = run(ROOT / "corpus.json")
    assert len(rows) == 60
    assert {r["arm"] for r in rows} == {"control", "treatment"}
    assert all(r["malformed"] for r in rows)
