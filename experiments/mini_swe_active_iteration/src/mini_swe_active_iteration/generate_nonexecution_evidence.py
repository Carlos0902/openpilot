"""Collect redacted resource and patch-shape evidence for one candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .acquisition import (
    build_candidate_nonexecution_evidence_receipt,
    load_candidate_image_acquisition_receipt,
    write_candidate_nonexecution_evidence_receipt,
)


def _load_private_record(path: Path, instance_id: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("private dataset must be a regular non-symlink file")
    payload = json.loads(path.read_text())
    records = payload if isinstance(payload, list) else [payload]
    matching = tuple(
        record
        for record in records
        if isinstance(record, dict)
        and record.get("instance_id") == instance_id
    )
    if len(matching) != 1:
        raise ValueError("private candidate identity is not unique")
    return matching[0]


def _changed_paths(patch: str) -> tuple[str, ...]:
    paths: set[str] = set()
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        fields = shlex.split(line)
        if (
            len(fields) != 4
            or fields[:2] != ["diff", "--git"]
            or not fields[2].startswith("a/")
            or not fields[3].startswith("b/")
        ):
            raise ValueError("unsupported patch path header")
        paths.add(fields[3].removeprefix("b/"))
    if not paths:
        raise ValueError("candidate patch contains no changed paths")
    return tuple(sorted(paths))


def _path_set_sha256(paths: tuple[str, ...]) -> str:
    payload = json.dumps(
        paths,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _validate_license_relative_path(value: str) -> str:
    path = Path(value)
    if (
        not value.strip()
        or path.is_absolute()
        or ".." in path.parts
        or "\n" in value
        or "\0" in value
    ):
        raise ValueError("license path must be a safe relative image path")
    return value


def _inspect_local_image(
    *,
    docker_binary: Path,
    image_ref: str,
    expected_manifest_digest: str,
    expected_image_id: str,
) -> None:
    completed = subprocess.run(
        [
            str(docker_binary),
            "image",
            "inspect",
            image_ref,
            "--format",
            "{{json .}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    repository = image_ref.rsplit(":", 1)[0]
    if (
        payload.get("Id") != expected_image_id
        or f"{repository}@{expected_manifest_digest}"
        not in payload.get("RepoDigests", [])
    ):
        raise ValueError("local image does not match acquisition receipt")


def _measure_checkout_and_license(
    *,
    docker_binary: Path,
    image_ref: str,
    license_relative_path: str,
) -> tuple[int, str]:
    safe_license_path = _validate_license_relative_path(license_relative_path)
    command = (
        "cd /testbed && du -sk . && sha256sum -- "
        f"{shlex.quote(safe_license_path)}"
    )
    completed = subprocess.run(
        [
            str(docker_binary),
            "run",
            "--rm",
            "--network",
            "none",
            "--platform",
            "linux/amd64",
            "--entrypoint",
            "/bin/sh",
            image_ref,
            "-lc",
            command,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    lines = tuple(
        line.strip() for line in completed.stdout.splitlines() if line.strip()
    )
    if len(lines) != 2:
        raise ValueError("checkout/license measurement output is invalid")
    checkout_fields = lines[0].split()
    license_fields = lines[1].split()
    if (
        len(checkout_fields) < 2
        or not checkout_fields[0].isdigit()
        or len(license_fields) < 2
    ):
        raise ValueError("checkout/license measurement fields are invalid")
    license_sha256 = license_fields[0]
    if len(license_sha256) != 64 or any(
        character not in "0123456789abcdef"
        for character in license_sha256
    ):
        raise ValueError("license measurement is not SHA-256")
    return int(checkout_fields[0]) * 1024, license_sha256


def generate_nonexecution_evidence(
    *,
    receipt_id: str,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
    execution_receipt_path: Path,
    private_dataset_path: Path,
    instance_id: str,
    repository_license_spdx: str,
    license_source_url: str,
    license_relative_path: str,
    docker_binary: Path,
    output_path: Path,
) -> None:
    image_receipt = load_candidate_image_acquisition_receipt(
        image_receipt_path,
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
    )
    if image_receipt.instance_id != instance_id:
        raise ValueError("nonexecution image candidate drifted")
    _inspect_local_image(
        docker_binary=docker_binary,
        image_ref=image_receipt.pinned_image_ref,
        expected_manifest_digest=image_receipt.manifest_digest,
        expected_image_id=image_receipt.local_image_id,
    )
    checkout_bytes, license_evidence_sha256 = (
        _measure_checkout_and_license(
            docker_binary=docker_binary,
            image_ref=image_receipt.pinned_image_ref,
            license_relative_path=license_relative_path,
        )
    )
    record = _load_private_record(private_dataset_path, instance_id)
    patch = record.get("patch")
    test_patch = record.get("test_patch")
    if not isinstance(patch, str) or not isinstance(test_patch, str):
        raise ValueError("private candidate patches are missing")
    production_paths = _changed_paths(patch)
    test_paths = _changed_paths(test_patch)
    receipt = build_candidate_nonexecution_evidence_receipt(
        receipt_id=receipt_id,
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
        image_receipt_path=image_receipt_path,
        execution_receipt_path=execution_receipt_path,
        instance_id=instance_id,
        repository_license_spdx=repository_license_spdx,
        license_evidence_sha256=license_evidence_sha256,
        license_source_url=license_source_url,
        license_local_research_evaluation_permitted=True,
        checkout_bytes=checkout_bytes,
        production_files_changed=len(production_paths),
        test_files_changed=len(test_paths),
        production_path_set_sha256=_path_set_sha256(production_paths),
        test_path_set_sha256=_path_set_sha256(test_paths),
    )
    write_candidate_nonexecution_evidence_receipt(
        receipt=receipt,
        output_path=output_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt-id", required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--host-preflight", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--image-receipt", type=Path, required=True)
    parser.add_argument("--execution-receipt", type=Path, required=True)
    parser.add_argument("--private-dataset", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--repository-license-spdx", required=True)
    parser.add_argument("--license-source-url", required=True)
    parser.add_argument("--license-relative-path", required=True)
    parser.add_argument("--docker-binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate_nonexecution_evidence(
        receipt_id=args.receipt_id,
        rules_path=args.rules,
        host_preflight_path=args.host_preflight,
        inventory_path=args.inventory,
        image_receipt_path=args.image_receipt,
        execution_receipt_path=args.execution_receipt,
        private_dataset_path=args.private_dataset,
        instance_id=args.instance_id,
        repository_license_spdx=args.repository_license_spdx,
        license_source_url=args.license_source_url,
        license_relative_path=args.license_relative_path,
        docker_binary=args.docker_binary,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
