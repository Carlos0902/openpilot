"""Redact four private SWE-bench runs into one execution-preflight receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .acquisition import (
    CandidateExecutionAttempt,
    build_candidate_execution_preflight_receipt,
    write_candidate_execution_preflight_receipt,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _one_file(root: Path, name: str) -> Path:
    matches = tuple(
        path
        for path in root.rglob(name)
        if path.is_file() and not path.is_symlink()
    )
    if len(matches) != 1:
        raise ValueError(f"expected exactly one private {name}: {matches}")
    return matches[0]


def _status_counts(payload: Any, group: str) -> tuple[int, int]:
    if not isinstance(payload, dict):
        raise ValueError("detailed test status must be an object")
    group_payload = payload.get(group)
    if not isinstance(group_payload, dict):
        raise ValueError(f"detailed report is missing {group}")
    success = group_payload.get("success")
    failure = group_payload.get("failure")
    if not isinstance(success, list) or not isinstance(failure, list):
        raise ValueError("detailed test status lists are invalid")
    return len(success), len(failure)


def _build_attempt(
    *,
    result_path: Path,
    private_work_dir: Path,
    instance_id: str,
    mode: str,
    repetition: int,
) -> CandidateExecutionAttempt:
    for path in (result_path, private_work_dir):
        if path.is_symlink() or not path.exists():
            raise ValueError("private execution artifact is missing or symlinked")
    result = json.loads(result_path.read_text())
    if (
        result.get("schema_version") != "1.0"
        or result.get("runner_version") != "network-isolated-swebench-v4"
        or result.get("instance_id") != instance_id
        or result.get("mode") != mode
        or result.get("repetition") != repetition
    ):
        raise ValueError("private runner result identity drifted")
    summary = result.get("report")
    if not isinstance(summary, dict):
        raise ValueError("private runner summary is missing")
    summary_digest = str(result.get("report_sha256") or "")
    matching_summary_files = tuple(
        path
        for path in private_work_dir.glob("*.json")
        if path.is_file()
        and not path.is_symlink()
        and _sha256(path) == summary_digest
    )
    if len(matching_summary_files) != 1:
        raise ValueError("private summary report hash cannot be reproduced")

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
        raise ValueError("detailed report candidate identity drifted")
    candidate = detailed[instance_id]
    tests_status = candidate.get("tests_status")
    fail_success, fail_failure = _status_counts(
        tests_status,
        "FAIL_TO_PASS",
    )
    pass_success, pass_failure = _status_counts(
        tests_status,
        "PASS_TO_PASS",
    )
    runtime_matches = re.findall(
        r"Test runtime: ([0-9]+(?:\.[0-9]+)?) seconds",
        run_log_path.read_text(),
    )
    if len(runtime_matches) != 1:
        raise ValueError("private evaluator runtime is not unique")
    completed = summary.get("completed_ids") == [instance_id]
    resolved = summary.get("resolved_ids") == [instance_id]
    if bool(candidate.get("resolved")) != resolved:
        raise ValueError("summary and detailed resolved state disagree")
    if summary.get("error_instances") != 0:
        raise ValueError("execution preflight cannot contain harness errors")
    created_images = result.get("created_images")
    if not isinstance(created_images, list) or len(created_images) != 1:
        raise ValueError("execution preflight must use one pinned image")
    return CandidateExecutionAttempt(
        attempt_id=str(result.get("run_id") or ""),
        mode=mode,
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


def generate_execution_preflight(
    *,
    receipt_id: str,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
    instance_id: str,
    private_root: Path,
    output_path: Path,
) -> Path:
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite execution preflight: {output_path}"
        )
    attempts = tuple(
        _build_attempt(
            result_path=private_root / f"{mode}-r{repetition}.result.json",
            private_work_dir=private_root / f"{mode}-r{repetition}",
            instance_id=instance_id,
            mode=mode,
            repetition=repetition,
        )
        for mode, repetition in (
            ("base", 1),
            ("base", 2),
            ("gold", 1),
            ("gold", 2),
        )
    )
    receipt = build_candidate_execution_preflight_receipt(
        receipt_id=receipt_id,
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
        image_receipt_path=image_receipt_path,
        instance_id=instance_id,
        attempts=attempts,
    )
    write_candidate_execution_preflight_receipt(
        receipt=receipt,
        output_path=output_path,
    )
    return output_path


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
    output = generate_execution_preflight(
        receipt_id=args.receipt_id,
        rules_path=args.rules,
        host_preflight_path=args.preflight,
        inventory_path=args.inventory,
        image_receipt_path=args.image_receipt,
        instance_id=args.instance_id,
        private_root=args.private_root,
        output_path=args.output,
    )
    print(output.resolve())


if __name__ == "__main__":
    main()
