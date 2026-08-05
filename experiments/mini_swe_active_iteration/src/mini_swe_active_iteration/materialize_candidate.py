"""Materialize one hash-verified candidate only inside a private evaluator area."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

import pyarrow.parquet as parquet

from .acquisition import (
    CandidateInventory,
    load_acquisition_host_preflight_receipt_v2,
    load_candidate_inventory,
    load_exploratory_acquisition_rules,
)


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def materialize_candidate_dataset(
    *,
    inventory: CandidateInventory,
    source_payload: bytes,
    instance_id: str,
    output_path: Path,
) -> Path:
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite private candidate: {output_path}"
        )
    candidates = {
        candidate.instance_id: candidate
        for candidate in inventory.candidates
    }
    if instance_id not in candidates:
        raise ValueError("candidate is not present in the frozen inventory")
    table = parquet.read_table(io.BytesIO(source_payload))
    matches = [
        row
        for row in table.to_pylist()
        if str(row.get("instance_id")) == instance_id
    ]
    if len(matches) != 1:
        raise ValueError("candidate source identity must occur exactly once")
    row = matches[0]
    candidate = candidates[instance_id]
    if _canonical_sha256(row) != candidate.dataset_row_sha256:
        raise ValueError("candidate dataset row hash drifted")
    if hashlib.sha256(
        str(row["problem_statement"]).encode()
    ).hexdigest() != candidate.problem_statement_sha256:
        raise ValueError("candidate problem statement hash drifted")

    output_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_path.parent, 0o700)
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump([row], stream, indent=2, sort_keys=True, default=str)
        stream.write("\n")
    os.chmod(output_path, 0o600)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--source-parquet", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.source_parquet.is_symlink() or not args.source_parquet.is_file():
        raise ValueError("source parquet must be a regular non-symlink file")
    rules = load_exploratory_acquisition_rules(args.rules)
    preflight = load_acquisition_host_preflight_receipt_v2(
        args.preflight,
        rules_path=args.rules,
    )
    if not preflight.host_resource_gate_passed:
        raise ValueError("candidate materialization requires a passed host gate")
    source_payload = args.source_parquet.read_bytes()
    source_sha256 = hashlib.sha256(source_payload).hexdigest()
    if source_sha256 != preflight.source_parquet_sha256:
        raise ValueError("source parquet hash drifted")
    inventory = load_candidate_inventory(
        args.inventory,
        rules=rules,
        source_parquet_sha256=source_sha256,
    )
    output = materialize_candidate_dataset(
        inventory=inventory,
        source_payload=source_payload,
        instance_id=args.instance_id,
        output_path=args.output,
    )
    print(output.resolve())


if __name__ == "__main__":
    main()
