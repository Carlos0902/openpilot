"""Run the planner pilot offline against recorded JSON responses.

No provider, production runtime, filesystem mutation, or permission is exercised.
Use --responses DIR containing <task-id>.<arm>.json to evaluate recordings.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from .evaluator import evaluate, load_json
from .prompts import ARMS

def run(corpus_path: Path, responses: Path | None = None) -> list[dict]:
    corpus = load_json(corpus_path)
    rows = []
    for task in corpus["tasks"]:
        for arm in ARMS:
            path = responses / f"{task['id']}.{arm}.json" if responses else None
            payload = load_json(path) if path and path.exists() else None
            row = evaluate(task, payload)
            row.update(arm=arm, response_file=str(path) if path else None)
            rows.append(row)
    return rows

def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument("--corpus", type=Path, default=Path(__file__).with_name("corpus.json")); p.add_argument("--responses", type=Path); p.add_argument("--output", type=Path)
    args = p.parse_args(); rows = run(args.corpus, args.responses); text = json.dumps({"manifest":"pilot-v1","rows":rows}, indent=2)
    if args.output: args.output.write_text(text + "\n", encoding="utf-8")
    else: print(text)
if __name__ == "__main__": main()
