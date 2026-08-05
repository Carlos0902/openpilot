"""Strict development-task fixtures with host-only hidden evaluators."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"fixture path must be safe relative path: {value!r}")


class HiddenEvaluatorFixture(_StrictModel):
    test_files: dict[str, str]
    command: str

    @model_validator(mode="after")
    def validate_paths_and_command(self) -> "HiddenEvaluatorFixture":
        if not self.test_files:
            raise ValueError("hidden evaluator must contain at least one test file")
        for path in self.test_files:
            _validate_relative_path(path)
        if not self.command.strip():
            raise ValueError("hidden evaluator command must not be blank")
        return self


class DevelopmentTaskFixture(_StrictModel):
    task_id: str
    stratum: str
    instruction: str
    public_files: dict[str, str]
    hidden_evaluator: HiddenEvaluatorFixture

    @model_validator(mode="after")
    def validate_fixture(self) -> "DevelopmentTaskFixture":
        if not self.task_id.strip() or not self.stratum.strip():
            raise ValueError("task_id and stratum must not be blank")
        if not self.instruction.strip() or not self.public_files:
            raise ValueError("instruction and public_files must not be empty")
        for path in self.public_files:
            _validate_relative_path(path)
        overlap = set(self.public_files) & set(self.hidden_evaluator.test_files)
        if overlap:
            raise ValueError(
                f"public and hidden evaluator paths overlap: {sorted(overlap)}"
            )
        return self

    def materialize_public(self, destination: Path) -> None:
        destination = destination.resolve()
        destination.mkdir(parents=True, exist_ok=True)
        for relative_path, content in self.public_files.items():
            _validate_relative_path(relative_path)
            path = destination / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def evaluate_hidden(self, *, workspace: Path) -> dict[str, object]:
        """Evaluate a host-side copy without materializing hidden files for the agent."""
        self.validate_agent_workspace(workspace)
        with tempfile.TemporaryDirectory(
            prefix=f"mini-swe-evaluator-{self.task_id}-"
        ) as temporary_directory:
            evaluation_root = Path(temporary_directory) / "submission"
            shutil.copytree(workspace, evaluation_root)
            for relative_path, content in self.hidden_evaluator.test_files.items():
                _validate_relative_path(relative_path)
                path = evaluation_root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            result = subprocess.run(
                ["/bin/bash", "-c", self.hidden_evaluator.command],
                cwd=evaluation_root,
                env={
                    **os.environ,
                    "PATH": os.pathsep.join(
                        (
                            str(Path(sys.executable).parent),
                            os.environ.get("PATH", ""),
                        )
                    ),
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                    "PYTHONNOUSERSITE": "1",
                },
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        return {
            "verified_task_success": result.returncode == 0,
            "returncode": result.returncode,
            "output": result.stdout,
        }

    def validate_agent_workspace(self, workspace: Path) -> None:
        workspace = workspace.resolve()
        for path in workspace.rglob("*"):
            if path.is_symlink():
                raise ValueError(
                    f"agent workspace must not contain symlinks: {path}"
                )


class DevelopmentTaskSuite(_StrictModel):
    schema_version: str
    suite_id: str
    lifecycle: str
    hypothesis_evidence_eligible: bool
    tasks: tuple[DevelopmentTaskFixture, ...]

    @model_validator(mode="after")
    def validate_suite(self) -> "DevelopmentTaskSuite":
        if self.schema_version != "1.0":
            raise ValueError("unsupported development suite schema")
        if self.lifecycle != "development":
            raise ValueError("development suite lifecycle must be development")
        if self.hypothesis_evidence_eligible:
            raise ValueError("development suite cannot be hypothesis eligible")
        task_ids = [task.task_id for task in self.tasks]
        if not task_ids or len(task_ids) != len(set(task_ids)):
            raise ValueError("development task IDs must be non-empty and unique")
        return self


def load_development_suite(path: Path) -> DevelopmentTaskSuite:
    if path.is_symlink() or not path.is_file():
        raise ValueError("development suite must be a regular non-symlink file")
    return DevelopmentTaskSuite.model_validate_json(path.read_text())
