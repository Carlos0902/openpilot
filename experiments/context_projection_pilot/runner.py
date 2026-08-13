"""Run the context projection pilot offline; no provider or filesystem mutation."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from .evaluator import evaluate
from .renderer import render_projection

def run(corpus_path: Path, *, repeats: int | None = None) -> list[dict]:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    count = repeats or int(corpus.get("repeats_per_arm", 3))
    rows = []
    for task in corpus["tasks"]:
        for arm in ("control", "treatment"):
            projection = render_projection(task, arm)
            for repeat in range(1, count + 1):
                row = evaluate(task, projection, arm=arm)
                row.update(repeat=repeat, mode=projection["mode"], fallback_used=projection["fallback_used"])
                rows.append(row)
    return rows

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--corpus", type=Path, default=Path(__file__).with_name("corpus.json")); parser.add_argument("--output", type=Path)
    args = parser.parse_args(); payload = {"rows": run(args.corpus)}
    text = json.dumps(payload, indent=2) + "\n"
    if args.output: args.output.write_text(text, encoding="utf-8")
    else: print(text)
if __name__ == "__main__": main()
