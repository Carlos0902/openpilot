"""Conservative offline analysis for the context projection pilot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from typing import Any


def analyze_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize deterministic gates without making a model-quality claim."""

    if not rows:
        raise ValueError("context pilot results must contain rows")
    failures = [
        {"task_id": row.get("task_id"), "arm": row.get("arm"), "repeat": row.get("repeat"), "reason": row.get("reason")}
        for row in rows
        if row.get("context_assembly_failure") is True
    ]
    control_values = [row.get("input_chars") for row in rows if row.get("arm") == "control" and isinstance(row.get("input_chars"), (int, float))]
    treatment_values = [row.get("input_chars") for row in rows if row.get("arm") == "treatment" and isinstance(row.get("input_chars"), (int, float))]
    control_median = float(median(control_values)) if control_values else None
    treatment_median = float(median(treatment_values)) if treatment_values else None
    reduction = None
    if control_median and treatment_median is not None:
        reduction = (control_median - treatment_median) / control_median
    if failures:
        decision = "rejected_deterministic_gate"
    elif reduction is not None and reduction >= 0.20:
        decision = "mechanism_canary_passed"
    else:
        decision = "noninferior_signal_no_efficiency_gate"
    return {
        "schema": "harness-slimming-context-projection-analysis-v1",
        "decision": decision,
        "claim_boundary": "mechanism_canary_only_no_noninferiority_claim",
        "rows": len(rows),
        "deterministic_failure_count": len(failures),
        "deterministic_failures": failures,
        "control_input_median_chars": control_median,
        "treatment_input_median_chars": treatment_median,
        "input_reduction_fraction": reduction,
        "provider_quality_status": "not_run",
        "noninferiority_status": "not_estimated",
    }


def analyze_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("results file must contain a rows list")
    return analyze_rows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = json.dumps(analyze_file(args.results), indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
