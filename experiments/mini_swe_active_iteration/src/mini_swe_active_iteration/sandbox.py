"""macOS Seatbelt environment for disposable development task arms."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

from minisweagent.exceptions import Submitted
from pydantic import BaseModel, ConfigDict


class SeatbeltEnvironmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cwd: Path
    forbidden_read_roots: tuple[Path, ...]
    timeout: int = 30
    executable: Path = Path("/usr/bin/sandbox-exec")
    env: dict[str, str]


def _seatbelt_literal(path: Path) -> str:
    return json.dumps(str(path.resolve()))


class SeatbeltEnvironment:
    """Run Bash with read exclusions, no network, and sandbox-only writes."""

    def __init__(
        self,
        *,
        cwd: Path,
        forbidden_read_roots: tuple[Path, ...],
        timeout: int = 30,
        executable: Path = Path("/usr/bin/sandbox-exec"),
    ) -> None:
        self.config = SeatbeltEnvironmentConfig(
            cwd=cwd.resolve(),
            forbidden_read_roots=tuple(
                dict.fromkeys(
                    (
                        Path.home().resolve(),
                        *(path.resolve() for path in forbidden_read_roots),
                    )
                )
            ),
            timeout=timeout,
            executable=executable,
            env={
                "PATH": os.environ.get(
                    "PATH",
                    "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                ),
                "HOME": str(cwd.resolve()),
                "TMPDIR": str(cwd.resolve()),
                "LANG": "C.UTF-8",
                "LC_ALL": "C",
                "PYTHONNOUSERSITE": "1",
                "PIP_CONFIG_FILE": "/dev/null",
            },
        )
        if platform.system() != "Darwin" or not executable.is_file():
            raise RuntimeError("SeatbeltEnvironment requires macOS sandbox-exec")
        self.config.cwd.mkdir(parents=True, exist_ok=True)
        self._profile = self._build_profile()

    def _build_profile(self) -> str:
        rules = [
            "(version 1)",
            "(deny default)",
            "(allow process*)",
            "(allow sysctl-read)",
            "(allow mach-lookup)",
            "(allow file-read*)",
            (
                "(allow file-write* "
                f"(subpath {_seatbelt_literal(self.config.cwd)}) "
                '(literal "/dev/null"))'
            ),
        ]
        rules.extend(
            f"(deny file-read* (subpath {_seatbelt_literal(path)}))"
            for path in self.config.forbidden_read_roots
        )
        return "\n".join(rules)

    def execute(
        self,
        action: dict,
        cwd: str = "",
        *,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        command = action.get("command", "")
        portable_command = (
            "if ! command -v python >/dev/null 2>&1; then\n"
            "  python() { /usr/bin/python3 \"$@\"; }\n"
            "fi\n"
            f"{command}"
        )
        execution_cwd = Path(cwd).resolve() if cwd else self.config.cwd
        if not execution_cwd.is_relative_to(self.config.cwd):
            raise ValueError("cwd must remain inside the disposable sandbox")
        try:
            result = subprocess.run(
                [
                    str(self.config.executable),
                    "-p",
                    self._profile,
                    "/bin/bash",
                    "-c",
                    portable_command,
                ],
                cwd=execution_cwd,
                env=self.config.env,
                text=True,
                timeout=timeout or self.config.timeout,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            output = {
                "output": result.stdout,
                "returncode": result.returncode,
                "exception_info": "",
            }
        except Exception as exc:
            raw_output = getattr(exc, "output", None)
            if isinstance(raw_output, bytes):
                raw_output = raw_output.decode("utf-8", errors="replace")
            output = {
                "output": raw_output or "",
                "returncode": -1,
                "exception_info": (
                    f"An error occurred while executing the command: {exc}"
                ),
                "extra": {
                    "exception_type": type(exc).__name__,
                    "exception": str(exc),
                },
            }
        self._check_finished(output)
        return output

    def _check_finished(self, output: dict[str, Any]) -> None:
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if (
            lines
            and lines[0].strip() == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
            and output["returncode"] == 0
        ):
            submission = "".join(lines[1:])
            raise Submitted(
                {
                    "role": "exit",
                    "content": submission,
                    "extra": {
                        "exit_status": "Submitted",
                        "submission": submission,
                    },
                }
            )

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return {
            **platform.uname()._asdict(),
            "cwd": str(self.config.cwd),
            "timeout": self.config.timeout,
            **kwargs,
        }

    def serialize(self) -> dict[str, Any]:
        return {
            "info": {
                "config": {
                    "environment": {
                        "cwd": str(self.config.cwd),
                        "forbidden_read_roots": [
                            str(path)
                            for path in self.config.forbidden_read_roots
                        ],
                        "timeout": self.config.timeout,
                        "environment_variable_names": sorted(self.config.env),
                        "network_allowed": False,
                        "writes_restricted_to_cwd": True,
                    },
                    "environment_type": (
                        f"{self.__class__.__module__}.{self.__class__.__name__}"
                    ),
                }
            }
        }
