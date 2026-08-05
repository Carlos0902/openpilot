"""Generate a redacted exploratory candidate inventory from a pinned dataset."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import urllib.request
from pathlib import Path

import pyarrow.parquet as parquet

from .acquisition import (
    build_candidate_inventory,
    load_acquisition_host_preflight_receipt_v2,
    load_acquisition_preflight_receipt,
    load_exploratory_acquisition_rules,
    write_candidate_inventory,
)


def _source_url(dataset_url: str) -> str:
    prefix = "https://huggingface.co/datasets/"
    if not dataset_url.startswith(prefix) or "/tree/" not in dataset_url:
        raise ValueError("unsupported pinned Hugging Face dataset URL")
    repository_and_revision = dataset_url.removeprefix(prefix).replace(
        "/tree/",
        "/resolve/",
        1,
    )
    return (
        f"{prefix}{repository_and_revision}/default/test/0000.parquet"
        "?download=true"
    )


def _collect_exposure_documents(
    *,
    package_root: Path,
    relative_paths: tuple[str, ...],
) -> dict[str, bytes]:
    package_root = package_root.resolve()
    documents: dict[str, bytes] = {}
    for relative_path in relative_paths:
        candidate = package_root / relative_path
        if candidate.is_symlink():
            raise ValueError(f"exposure registry cannot be a symlink: {candidate}")
        if candidate.is_file():
            documents[relative_path] = candidate.read_bytes()
            continue
        if not candidate.is_dir():
            raise ValueError(f"missing exposure registry path: {candidate}")
        for child in sorted(candidate.rglob("*")):
            if child.is_symlink():
                raise ValueError(
                    f"exposure registry cannot contain symlinks: {child}"
                )
            if child.is_file():
                key = child.relative_to(package_root).as_posix()
                documents[key] = child.read_bytes()
    return documents


def generate_candidate_inventory(
    *,
    rules_path: Path,
    preflight_path: Path,
    output_path: Path,
    package_root: Path,
    source_parquet_path: Path | None = None,
    inventory_id: str = "mini-swe-exploratory-candidate-inventory-v1",
) -> Path:
    rules = load_exploratory_acquisition_rules(rules_path)
    preflight_payload = preflight_path.read_text()
    if json.loads(preflight_payload).get("schema_version") == "2.0":
        receipt = load_acquisition_host_preflight_receipt_v2(
            preflight_path,
            rules_path=rules_path,
        )
        if not receipt.host_resource_gate_passed:
            raise ValueError("candidate inventory requires a passed host gate")
    else:
        receipt = load_acquisition_preflight_receipt(
            preflight_path,
            rules=rules,
        )
    if source_parquet_path is None:
        with urllib.request.urlopen(
            _source_url(rules.source.dataset_url),
            timeout=60,
        ) as response:
            source_payload = response.read()
    else:
        if (
            source_parquet_path.is_symlink()
            or not source_parquet_path.is_file()
        ):
            raise ValueError(
                "source parquet must be a regular non-symlink file"
            )
        source_payload = source_parquet_path.read_bytes()
    source_sha256 = hashlib.sha256(source_payload).hexdigest()
    if source_sha256 != receipt.source_parquet_sha256:
        raise ValueError("downloaded source payload hash drifted")
    table = parquet.read_table(io.BytesIO(source_payload))
    if table.num_rows != rules.source.expected_row_count:
        raise ValueError("downloaded source row count drifted")
    records = table.to_pylist()
    exposure_documents = _collect_exposure_documents(
        package_root=package_root,
        relative_paths=rules.exposure_registry_paths,
    )
    inventory = build_candidate_inventory(
        records=records,
        rules=rules,
        source_parquet_sha256=source_sha256,
        exposure_documents=exposure_documents,
        inventory_id=inventory_id,
    )
    write_candidate_inventory(
        inventory=inventory,
        output_path=output_path,
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path("EXPLORATORY_TASK_ACQUISITION_RULES_V1.json"),
    )
    parser.add_argument("--source-parquet", type=Path)
    parser.add_argument(
        "--inventory-id",
        default="mini-swe-exploratory-candidate-inventory-v1",
    )
    parser.add_argument(
        "--preflight",
        type=Path,
        default=Path("EXPLORATORY_ACQUISITION_PREFLIGHT_V1.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("EXPLORATORY_CANDIDATE_INVENTORY_V1.json"),
    )
    args = parser.parse_args()
    output = generate_candidate_inventory(
        rules_path=args.rules,
        preflight_path=args.preflight,
        output_path=args.output,
        package_root=Path(__file__).parents[2],
        source_parquet_path=args.source_parquet,
        inventory_id=args.inventory_id,
    )
    print(output.resolve())


if __name__ == "__main__":
    main()
