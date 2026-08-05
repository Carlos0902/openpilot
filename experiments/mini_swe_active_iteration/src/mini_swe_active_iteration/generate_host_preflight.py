"""Generate a versioned host-only exploratory acquisition preflight receipt."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pyarrow.parquet as parquet

from .acquisition import (
    AcquisitionHostFacts,
    build_acquisition_host_preflight_receipt_v2,
    load_exploratory_acquisition_rules,
)
from .generate_candidate_inventory import _source_url


def _output(
    command: tuple[str, ...],
    *,
    timeout: int = 60,
) -> str:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return completed.stdout.strip()


def _physical_memory_bytes() -> int:
    if sys.platform == "darwin":
        return int(_output(("sysctl", "-n", "hw.memsize")))
    page_size = os.sysconf("SC_PAGE_SIZE")
    page_count = os.sysconf("SC_PHYS_PAGES")
    return int(page_size * page_count)


def _absolute_without_symlink_resolution(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _probe_host(
    *,
    harness_root: Path,
    harness_python: Path,
    docker_smoke_image: str,
    docker_virtual_disk: Path | None,
) -> AcquisitionHostFacts:
    harness_root = harness_root.resolve()
    harness_python = _absolute_without_symlink_resolution(harness_python)
    harness_commit = _output(
        ("git", "-C", str(harness_root), "rev-parse", "HEAD")
    )
    harness_clean = not _output(
        ("git", "-C", str(harness_root), "status", "--porcelain")
    )
    harness_python_version = _output(
        (
            str(harness_python),
            "-c",
            "import platform; print(platform.python_version())",
        )
    )
    harness_environment = _output(
        (
            str(harness_python),
            "-c",
            (
                "import importlib.metadata as m, json; "
                "print(json.dumps(sorted("
                "f'{d.metadata[\"Name\"]}=={d.version}' "
                "for d in m.distributions()), separators=(',', ':')))"
            ),
        )
    )
    harness_import = subprocess.run(
        (
            str(harness_python),
            "-c",
            "import swebench; print(swebench.__version__)",
        ),
        capture_output=True,
        text=True,
        timeout=60,
    )
    harness_cli = subprocess.run(
        (
            str(harness_python),
            "-m",
            "swebench.harness.run_evaluation",
            "--help",
        ),
        capture_output=True,
        text=True,
        timeout=60,
    )
    docker_client_version = _output(
        ("docker", "version", "--format", "{{.Client.Version}}")
    )
    docker_server_version = _output(
        ("docker", "version", "--format", "{{.Server.Version}}")
    )
    docker_server_architecture = _output(
        ("docker", "info", "--format", "{{.Architecture}}")
    )
    docker_memory_bytes = int(
        _output(("docker", "info", "--format", "{{.MemTotal}}"))
    )
    docker_smoke_image_id = _output(
        (
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            docker_smoke_image,
        )
    )
    docker_smoke = subprocess.run(
        (
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--pull",
            "never",
            docker_smoke_image,
            "true",
        ),
        capture_output=True,
        text=True,
        timeout=60,
    )
    docker_virtual_disk_bytes = None
    docker_virtual_disk_allocated_bytes = None
    if docker_virtual_disk is not None:
        docker_virtual_disk = _absolute_without_symlink_resolution(
            docker_virtual_disk
        )
        if (
            docker_virtual_disk.is_symlink()
            or not docker_virtual_disk.is_file()
        ):
            raise ValueError(
                "Docker virtual disk must be a regular non-symlink file"
            )
        disk_stat = docker_virtual_disk.stat()
        docker_virtual_disk_bytes = disk_stat.st_size
        docker_virtual_disk_allocated_bytes = disk_stat.st_blocks * 512
    return AcquisitionHostFacts(
        host_architecture=platform.machine(),
        os_name=platform.system(),
        os_version=platform.mac_ver()[0] or platform.release(),
        physical_memory_bytes=_physical_memory_bytes(),
        available_storage_bytes=shutil.disk_usage("/").free,
        docker_client_version=docker_client_version,
        docker_server_version=docker_server_version,
        docker_server_architecture=docker_server_architecture,
        docker_memory_bytes=docker_memory_bytes,
        docker_virtual_disk_bytes=docker_virtual_disk_bytes,
        docker_virtual_disk_allocated_bytes=(
            docker_virtual_disk_allocated_bytes
        ),
        docker_daemon_available=True,
        docker_smoke_image=docker_smoke_image,
        docker_smoke_image_id=docker_smoke_image_id,
        docker_smoke_passed=docker_smoke.returncode == 0,
        harness_commit=harness_commit,
        harness_clean=harness_clean,
        harness_python_version=harness_python_version,
        harness_environment_fingerprint=hashlib.sha256(
            harness_environment.encode()
        ).hexdigest(),
        harness_import_passed=harness_import.returncode == 0,
        harness_cli_passed=harness_cli.returncode == 0,
    )


def generate_host_preflight(
    *,
    rules_path: Path,
    output_path: Path,
    harness_root: Path,
    harness_python: Path,
    docker_smoke_image: str,
    source_parquet_path: Path | None = None,
    receipt_id: str = "mini-swe-exploratory-host-preflight-v2",
    docker_virtual_disk: Path | None = None,
) -> Path:
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite host preflight: {output_path}"
        )
    rules = load_exploratory_acquisition_rules(rules_path)
    if rules.source.harness_revision is None:
        raise ValueError("host preflight rules must pin the harness revision")
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
    table = parquet.read_table(io.BytesIO(source_payload))
    records = table.to_pylist()
    identifiers = tuple(str(record["instance_id"]) for record in records)
    repositories = tuple(str(record["repo"]) for record in records)
    host_facts = _probe_host(
        harness_root=harness_root,
        harness_python=harness_python,
        docker_smoke_image=docker_smoke_image,
        docker_virtual_disk=docker_virtual_disk,
    )
    receipt = build_acquisition_host_preflight_receipt_v2(
        rules=rules,
        rules_file_sha256=hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        source_parquet_sha256=hashlib.sha256(source_payload).hexdigest(),
        source_parquet_bytes=len(source_payload),
        source_row_count=table.num_rows,
        unique_instance_id_count=len(set(identifiers)),
        repository_count=len(set(repositories)),
        source_columns=tuple(table.column_names),
        host_facts=host_facts,
        receipt_id=receipt_id,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump(
            receipt.model_dump(mode="json"),
            stream,
            indent=2,
            sort_keys=True,
        )
        stream.write("\n")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path("EXPLORATORY_TASK_ACQUISITION_RULES_V2.json"),
    )
    parser.add_argument("--docker-virtual-disk", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("EXPLORATORY_ACQUISITION_PREFLIGHT_V2.json"),
    )
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--harness-python", type=Path, required=True)
    parser.add_argument(
        "--docker-smoke-image",
        default="redis:7-alpine",
    )
    parser.add_argument("--source-parquet", type=Path)
    parser.add_argument(
        "--receipt-id",
        default="mini-swe-exploratory-host-preflight-v2",
    )
    args = parser.parse_args()
    output = generate_host_preflight(
        rules_path=args.rules,
        output_path=args.output,
        harness_root=args.harness_root,
        harness_python=args.harness_python,
        docker_smoke_image=args.docker_smoke_image,
        source_parquet_path=args.source_parquet,
        receipt_id=args.receipt_id,
        docker_virtual_disk=args.docker_virtual_disk,
    )
    print(output.resolve())


if __name__ == "__main__":
    main()
