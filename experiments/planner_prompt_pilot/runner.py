"""Run the planner pilot offline against recorded JSON responses.

No provider, production runtime, filesystem mutation, or permission is exercised.
Use --responses DIR containing <task-id>.<arm>.json to evaluate recordings.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from .evaluator import evaluate, load_json, validate_loaded_corpus
from .prompts import ARMS, PROMPT_MANIFEST, render_arm_prompt

def run(corpus_path: Path, responses: Path | None = None, *, repeats: int | None = None) -> list[dict]:
    corpus = load_json(corpus_path)
    validate_loaded_corpus(corpus)
    repeats = repeats or int(corpus.get("repeats_per_arm", 3))
    rows = []
    for task in corpus["tasks"]:
        for arm in ARMS:
            for repeat in range(1, repeats + 1):
                path = responses / f"{task['id']}.{arm}.{repeat}.json" if responses else None
                response_error = None
                if path is None:
                    payload = {"_response_error": "missing_recording"}
                    response_error = "missing_recording"
                elif not path.exists():
                    payload = {"_response_error": "missing_recording"}
                    response_error = "missing_recording"
                else:
                    try:
                        payload = load_json(path)
                    except (OSError, ValueError, TypeError) as exc:
                        response_error = f"malformed_recording:{type(exc).__name__}"
                        payload = {"_response_error": response_error}
                row = evaluate(task, payload)
                prompt = render_arm_prompt(
                    arm,
                    task=task["task"],
                    goal=task.get("goal", "Complete the requested task"),
                    planning_surface=task.get("planning_surface", ""),
                    history=task.get("history", "No previous task results."),
                    constraints=task.get("constraints", ""),
                    project_context=task.get("project_context", ""),
                    read_only_notice=task.get("read_only_notice", ""),
                )
                row.update(
                    arm=arm,
                    repeat=repeat,
                    response_file=str(path) if path else None,
                    prompt_chars=len(prompt),
                    response_error=response_error,
                )
                rows.append(row)
    return rows

def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument("--corpus", type=Path, default=Path(__file__).with_name("corpus.json")); p.add_argument("--responses", type=Path); p.add_argument("--output", type=Path)
    args = p.parse_args(); rows = run(args.corpus, args.responses); text = json.dumps({**PROMPT_MANIFEST, "rows":rows}, indent=2)
    if args.output: args.output.write_text(text + "\n", encoding="utf-8")
    else: print(text)
if __name__ == "__main__": main()
