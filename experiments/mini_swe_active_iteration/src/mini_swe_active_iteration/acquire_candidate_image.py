"""Acquire one official SWE-bench instance image by immutable digest."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from .acquisition import (
    build_candidate_image_acquisition_receipt,
    write_candidate_image_acquisition_receipt,
)


def _run_json(command: tuple[str, ...], *, timeout: int) -> Any:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return json.loads(completed.stdout)


def _parse_manifest(
    payload: Any,
) -> tuple[str, int, int, str, str]:
    if not isinstance(payload, dict):
        raise ValueError("Docker manifest payload must be an object")
    descriptor = payload.get("Descriptor")
    manifest = payload.get("SchemaV2Manifest")
    if not isinstance(descriptor, dict) or not isinstance(manifest, dict):
        raise ValueError("Docker manifest must include descriptor and schema V2")
    platform = descriptor.get("platform")
    if not isinstance(platform, dict):
        raise ValueError("Docker manifest platform is missing")
    architecture = str(platform.get("architecture") or "")
    image_os = str(platform.get("os") or "")
    digest = str(descriptor.get("digest") or "")
    descriptor_bytes = int(descriptor.get("size") or 0)
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ValueError("Docker manifest layers are missing")
    compressed_layer_bytes = sum(
        int(layer.get("size") or 0)
        for layer in layers
        if isinstance(layer, dict)
    )
    if architecture != "amd64" or image_os != "linux":
        raise ValueError("official instance image must be linux/amd64")
    if (
        not digest.startswith("sha256:")
        or len(digest) != 71
        or descriptor_bytes <= 0
        or compressed_layer_bytes <= 0
    ):
        raise ValueError("Docker manifest identity or sizes are invalid")
    return (
        digest,
        descriptor_bytes,
        compressed_layer_bytes,
        architecture,
        image_os,
    )


def acquire_candidate_image(
    *,
    receipt_id: str,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    instance_id: str,
    output_path: Path,
    docker_executable: str = "docker",
    pull_timeout_seconds: int = 1800,
) -> Path:
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite candidate image receipt: {output_path}"
        )
    repository = (
        "swebench/sweb.eval.x86_64."
        f"{instance_id.lower().replace('__', '_1776_')}"
    )
    source_image_ref = f"{repository}:latest"
    manifest_payload = _run_json(
        (
            docker_executable,
            "manifest",
            "inspect",
            "--verbose",
            source_image_ref,
        ),
        timeout=60,
    )
    (
        manifest_digest,
        descriptor_bytes,
        compressed_layer_bytes,
        architecture,
        image_os,
    ) = _parse_manifest(manifest_payload)
    digest_ref = f"{repository}@{manifest_digest}"
    pinned_image_ref = (
        f"{repository}:acq-v4-"
        f"{manifest_digest.removeprefix('sha256:')[:12]}"
    )
    subprocess.run(
        (docker_executable, "pull", digest_ref),
        check=True,
        timeout=pull_timeout_seconds,
    )
    subprocess.run(
        (docker_executable, "tag", digest_ref, pinned_image_ref),
        check=True,
        timeout=60,
    )
    inspect_payload = _run_json(
        (docker_executable, "image", "inspect", pinned_image_ref),
        timeout=60,
    )
    if (
        not isinstance(inspect_payload, list)
        or len(inspect_payload) != 1
        or not isinstance(inspect_payload[0], dict)
    ):
        raise ValueError("Docker image inspect must return exactly one image")
    image = inspect_payload[0]
    receipt = build_candidate_image_acquisition_receipt(
        receipt_id=receipt_id,
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
        instance_id=instance_id,
        manifest_digest=manifest_digest,
        manifest_descriptor_bytes=descriptor_bytes,
        compressed_layer_bytes=compressed_layer_bytes,
        local_image_id=str(image.get("Id") or ""),
        repo_digests=tuple(
            str(value) for value in image.get("RepoDigests") or ()
        ),
        image_architecture=str(image.get("Architecture") or architecture),
        image_os=str(image.get("Os") or image_os),
        image_size_bytes=int(image.get("Size") or 0),
    )
    if receipt.source_image_ref != source_image_ref:
        raise ValueError("resolved source image does not match candidate receipt")
    if receipt.pinned_image_ref != pinned_image_ref:
        raise ValueError("pinned image tag does not match candidate receipt")
    write_candidate_image_acquisition_receipt(
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
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--docker-executable", default="docker")
    parser.add_argument("--pull-timeout-seconds", type=int, default=1800)
    args = parser.parse_args()
    output = acquire_candidate_image(
        receipt_id=args.receipt_id,
        rules_path=args.rules,
        host_preflight_path=args.preflight,
        inventory_path=args.inventory,
        instance_id=args.instance_id,
        output_path=args.output,
        docker_executable=args.docker_executable,
        pull_timeout_seconds=args.pull_timeout_seconds,
    )
    print(output.resolve())


if __name__ == "__main__":
    main()
