"""Run the frozen stateless review-plane protocol, never a task arm."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

import pyarrow.parquet as parquet

from .review_provider import (
    CandidateReviewInput,
    ProviderReviewProtocol,
    review_candidate,
)


def _load_dotenv_value(*, path: Path, variable: str) -> str | None:
    """Read one dotenv assignment without logging either it or other values."""

    if path.is_symlink() or not path.is_file():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if separator and key.strip() == variable:
            return value.strip().strip('"').strip("'")
    return None


def _read_candidates(*, source_parquet: Path, instance_ids: set[str]) -> dict[str, CandidateReviewInput]:
    if source_parquet.is_symlink() or not source_parquet.is_file():
        raise ValueError("source parquet must be a regular file")
    table = parquet.read_table(
        source_parquet,
        columns=["instance_id", "problem_statement", "patch", "test_patch"],
    )
    candidates: dict[str, CandidateReviewInput] = {}
    for row in table.to_pylist():
        instance_id = row["instance_id"]
        if instance_id in instance_ids:
            if instance_id in candidates:
                raise ValueError("source parquet has duplicate candidate identity")
            candidates[instance_id] = CandidateReviewInput(
                instance_id=instance_id,
                problem_statement=row["problem_statement"],
                patch=row["patch"],
                test_patch=row["test_patch"],
            )
    missing = sorted(instance_ids - set(candidates))
    if missing:
        raise ValueError(f"source parquet omitted reviewed candidates: {missing}")
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--source-parquet", type=Path, required=True)
    parser.add_argument("--nonexecution-receipts", type=Path, required=True)
    parser.add_argument("--host-preflight", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--image-receipts", type=Path, required=True)
    parser.add_argument("--execution-receipts", type=Path, required=True)
    parser.add_argument("--dotenv", type=Path, required=True)
    args = parser.parse_args()

    protocol = ProviderReviewProtocol.load(args.protocol)
    source_digest = hashlib.sha256(args.source_parquet.read_bytes()).hexdigest()
    if source_digest != protocol.source_parquet_sha256:
        raise ValueError("source parquet hash drifted from frozen reviewer protocol")
    if any(path.is_symlink() or not path.is_dir() for path in (args.nonexecution_receipts, args.image_receipts, args.execution_receipts)):
        raise ValueError("receipt directories must be regular directories")
    nonexecution_paths = tuple(sorted(args.nonexecution_receipts.glob("*.json")))
    if not nonexecution_paths:
        raise ValueError("no nonexecution receipts available for second review")
    instance_ids = {path.stem for path in nonexecution_paths}
    candidates = _read_candidates(source_parquet=args.source_parquet, instance_ids=instance_ids)
    api_key = os.environ.get(protocol.api_key_environment_variable) or _load_dotenv_value(
        path=args.dotenv,
        variable=protocol.api_key_environment_variable,
    )
    if not api_key:
        raise RuntimeError(
            f"missing {protocol.api_key_environment_variable}; no reviewer request was made"
        )
    for instance_id in sorted(instance_ids):
        public_output = protocol.public_output_root / f"{instance_id}.deepseek-v4-flash-reviewer-v1.json"
        private_output = protocol.private_output_root / f"{instance_id}.json"
        receipt_output = protocol.public_receipt_root / f"{instance_id}.json"
        failed_private_output = protocol.private_output_root / f"{instance_id}.failed.json"
        failed_receipt_output = protocol.public_receipt_root / f"{instance_id}.failed.json"
        if any(path.exists() or path.is_symlink() for path in (public_output, private_output, receipt_output, failed_private_output, failed_receipt_output)):
            raise FileExistsError(f"review attempt already recorded for {instance_id}")
        output = review_candidate(
            protocol=protocol,
            candidate=candidates[instance_id],
            api_key=api_key,
            nonexecution_receipt_path=args.nonexecution_receipts / f"{instance_id}.json",
            host_preflight_path=args.host_preflight,
            inventory_path=args.inventory,
            image_receipt_path=args.image_receipts / f"{instance_id}.json",
            execution_receipt_path=args.execution_receipts / f"{instance_id}.json",
        )
        print(output)


if __name__ == "__main__":
    main()
