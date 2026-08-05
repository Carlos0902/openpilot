"""Disposable, network-isolated Docker environment for one SWE-bench arm."""

from __future__ import annotations

import shlex
from pathlib import PurePosixPath
from typing import Any

try:  # Keep receipt-only and Docker-boundary tests independent of mini-SWE runtime.
    from minisweagent.exceptions import Submitted
except ModuleNotFoundError:  # pragma: no cover - exercised only without runtime extra
    class Submitted(Exception):
        pass

from .swebench_network_runner import _verify_local_pinned_image


class SWEbenchDockerEnvironment:
    """Expose one pinned SWE-bench checkout without mounting host data."""

    _WORKSPACE = PurePosixPath("/testbed")
    _KEEPALIVE_COMMAND = ["/bin/sh", "-c", "while true; do sleep 3600; done"]

    def __init__(
        self,
        *,
        instance_id: str,
        instance_image_ref: str,
        expected_image_manifest_digest: str,
        docker_client: object | None = None,
        timeout_seconds: int = 30,
        maximum_patch_bytes: int = 1_000_000,
    ) -> None:
        if not instance_id.strip() or timeout_seconds <= 0 or maximum_patch_bytes <= 0:
            raise ValueError("invalid SWE-bench agent environment configuration")
        if docker_client is None:
            import docker

            docker_client = docker.from_env()
        expected_image_ref = (
            "swebench/sweb.eval.x86_64."
            f"{instance_id.lower().replace('__', '_1776_')}:"
            f"{instance_image_ref.rsplit(':', 1)[-1]}"
        )
        if instance_image_ref != expected_image_ref:
            raise ValueError("pinned image ref does not match the candidate identity")
        self._instance_id = instance_id
        self._instance_image_ref = instance_image_ref
        self._timeout_seconds = timeout_seconds
        self._maximum_patch_bytes = maximum_patch_bytes
        self._image_id = _verify_local_pinned_image(
            images=getattr(docker_client, "images"),
            image_ref=instance_image_ref,
            expected_manifest_digest=expected_image_manifest_digest,
        )
        self._container = getattr(docker_client, "containers").create(
            image=instance_image_ref,
            command=self._KEEPALIVE_COMMAND,
            working_dir=str(self._WORKSPACE),
            network_mode="none",
            environment={"HOME": "/testbed", "LANG": "C.UTF-8", "LC_ALL": "C"},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
        )
        self._container.start()
        self._closed = False

    def _working_directory(self, cwd: str) -> str:
        proposed = PurePosixPath(cwd) if cwd else self._WORKSPACE
        if not proposed.is_absolute():
            proposed = self._WORKSPACE / proposed
        if (
            not proposed.is_relative_to(self._WORKSPACE)
            or ".." in proposed.parts
        ):
            raise ValueError("cwd must remain inside /testbed")
        return str(proposed)

    def execute(
        self,
        action: dict[str, Any],
        cwd: str = "",
        *,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("SWE-bench environment is already closed")
        command = action.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("action must contain a nonempty command")
        command_timeout = timeout or self._timeout_seconds
        if command_timeout <= 0:
            raise ValueError("command timeout must be positive")
        bounded_command = (
            f"exec timeout --signal=KILL {int(command_timeout)}s "
            f"/bin/bash -lc {shlex.quote(command)}"
        )
        exit_code, output = self._container.exec_run(
            ["/bin/bash", "-lc", bounded_command],
            workdir=self._working_directory(cwd),
            environment={"HOME": "/testbed", "LANG": "C.UTF-8", "LC_ALL": "C"},
            demux=False,
        )
        decoded = (
            output.decode("utf-8", errors="replace")
            if isinstance(output, bytes)
            else str(output)
        )
        result = {
            "output": decoded,
            "returncode": int(exit_code),
            "exception_info": "",
        }
        self._check_finished(result)
        return result

    def _check_finished(self, output: dict[str, Any]) -> None:
        lines = output["output"].lstrip().splitlines(keepends=True)
        if (
            lines
            and lines[0].strip() == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
            and output["returncode"] == 0
        ):
            raise Submitted(
                {
                    "role": "exit",
                    "content": "".join(lines[1:]),
                    "extra": {
                        "exit_status": "Submitted",
                        "submission": "".join(lines[1:]),
                    },
                }
            )

    def collect_patch(self) -> str:
        if self._closed:
            raise RuntimeError("SWE-bench environment is already closed")
        exit_code, output = self._container.exec_run(
            ["/bin/bash", "-lc", "git -C /testbed diff --binary --no-ext-diff"],
            workdir=str(self._WORKSPACE),
            environment={"HOME": "/testbed", "LANG": "C.UTF-8", "LC_ALL": "C"},
            demux=False,
        )
        if int(exit_code) != 0:
            raise RuntimeError("unable to collect the agent patch")
        patch = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output)
        if len(patch.encode("utf-8")) > self._maximum_patch_bytes:
            raise ValueError("agent patch exceeds the frozen size limit")
        return patch

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._container.remove(force=True)
        finally:
            self._closed = True

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "system": "Linux",
            "node": "swebench-agent",
            "release": "",
            "version": "",
            "machine": "x86_64",
            "processor": "x86_64",
            "cwd": str(self._WORKSPACE),
            "timeout": self._timeout_seconds,
            "instance_id": self._instance_id,
            **kwargs,
        }

    def serialize(self) -> dict[str, Any]:
        return {
            "info": {
                "config": {
                    "environment": {
                        "instance_id": self._instance_id,
                        "instance_image_ref": self._instance_image_ref,
                        "instance_image_id": self._image_id,
                        "cwd": str(self._WORKSPACE),
                        "timeout": self._timeout_seconds,
                        "network_allowed": False,
                        "host_mounts_allowed": False,
                        "capabilities_dropped": ["ALL"],
                        "no_new_privileges": True,
                    },
                    "environment_type": (
                        f"{self.__class__.__module__}.{self.__class__.__name__}"
                    ),
                }
            }
        }
