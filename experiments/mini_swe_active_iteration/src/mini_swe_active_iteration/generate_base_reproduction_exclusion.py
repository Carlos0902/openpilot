"""Redact two resolved base runs into a preregistered exclusion receipt."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .acquisition import (
    CandidateBaseReproductionAttempt,
    build_candidate_base_reproduction_exclusion_receipt,
    write_candidate_base_reproduction_exclusion_receipt,
)
from .generate_execution_preflight import (
    _one_file,
    _sha256,
    _status_counts,
)


def _build_resolved_base_attempt(
    *,
    result_path: Path,
    private_work_dir: Path,
    instance_id: str,
    repetition: int,
) -> CandidateBaseReproductionAttempt:
    for path in (result_path, private_work_dir):
        if path.is_symlink() or not path.exists():
            raise ValueError("private base artifact is missing or symlinked")
    result = json.loads(result_path.read_text())
    if (
        result.get("schema_version") != "1.0"
        or result.get("runner_version") != "network-isolated-swebench-v4"
        or result.get("instance_id") != instance_id
        or result.get("mode") != "base"
        or result.get("repetition") != repetition
    ):
        raise ValueError("private base result identity drifted")
    summary = result.get("report")
    if not isinstance(summary, dict):
        raise ValueError("private base summary is missing")
    summary_digest = str(result.get("report_sha256") or "")
    matching_summary_files = tuple(
        path
        for path in private_work_dir.glob("*.json")
        if path.is_file()
        and not path.is_symlink()
        and _sha256(path) == summary_digest
    )
    if len(matching_summary_files) != 1:
        raise ValueError("private base summary hash cannot be reproduced")
    detailed_report_path = _one_file(
        private_work_dir / "logs" / "run_evaluation",
        "report.json",
    )
    test_output_path = _one_file(
        private_work_dir / "logs" / "run_evaluation",
        "test_output.txt",
    )
    run_log_path = _one_file(
        private_work_dir / "logs" / "run_evaluation",
        "run_instance.log",
    )
    detailed = json.loads(detailed_report_path.read_text())
    if set(detailed) != {instance_id} or not isinstance(
        detailed[instance_id],
        dict,
    ):
        raise ValueError("detailed base report candidate drifted")
    candidate = detailed[instance_id]
    fail_success, fail_failure = _status_counts(
        candidate.get("tests_status"),
        "FAIL_TO_PASS",
    )
    pass_success, pass_failure = _status_counts(
        candidate.get("tests_status"),
        "PASS_TO_PASS",
    )
    runtime_matches = re.findall(
        r"Test runtime: ([0-9]+(?:\.[0-9]+)?) seconds",
        run_log_path.read_text(),
    )
    if len(runtime_matches) != 1:
        raise ValueError("private base evaluator runtime is not unique")
    completed = summary.get("completed_ids") == [instance_id]
    resolved = summary.get("resolved_ids") == [instance_id]
    if (
        bool(candidate.get("resolved")) != resolved
        or summary.get("error_instances") != 0
    ):
        raise ValueError("base summary and detailed outcome disagree")
    created_images = result.get("created_images")
    if not isinstance(created_images, list) or len(created_images) != 1:
        raise ValueError("base exclusion must use one pinned image")
    return CandidateBaseReproductionAttempt(
        attempt_id=str(result.get("run_id") or ""),
        repetition=repetition,
        runner_version=str(result.get("runner_version") or ""),
        result_sha256=_sha256(result_path),
        summary_report_sha256=summary_digest,
        detailed_report_sha256=_sha256(detailed_report_path),
        test_output_sha256=_sha256(test_output_path),
        evaluator_seconds=float(runtime_matches[0]),
        network_disabled=bool(result.get("network_disabled")),
        instance_image_ref=str(created_images[0]),
        instance_image_manifest_digest=str(
            result.get("instance_image_manifest_digest") or ""
        ),
        instance_image_id=str(result.get("instance_image_id") or ""),
        completed=completed,
        resolved=resolved,
        fail_to_pass_success_count=fail_success,
        fail_to_pass_failure_count=fail_failure,
        pass_to_pass_success_count=pass_success,
        pass_to_pass_failure_count=pass_failure,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt-id", required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--image-receipt", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite base exclusion: {args.output}"
        )
    attempts = tuple(
        _build_resolved_base_attempt(
            result_path=args.private_root / f"base-r{repetition}.result.json",
            private_work_dir=args.private_root / f"base-r{repetition}",
            instance_id=args.instance_id,
            repetition=repetition,
        )
        for repetition in (1, 2)
    )
    receipt = build_candidate_base_reproduction_exclusion_receipt(
        receipt_id=args.receipt_id,
        rules_path=args.rules,
        host_preflight_path=args.preflight,
        inventory_path=args.inventory,
        image_receipt_path=args.image_receipt,
        instance_id=args.instance_id,
        attempts=attempts,
    )
    write_candidate_base_reproduction_exclusion_receipt(
        receipt=receipt,
        output_path=args.output,
    )
    print(args.output.resolve())


if __name__ == "__main__":
    main()
