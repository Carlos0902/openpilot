"""Git safety management for project-local autonomous iteration."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from metadata import GitDiffContextMetadata, GitRepositoryMetadata, GitSnapshotMetadata


DEFAULT_IGNORE_RULES = [
    ".venv/",
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "*.log",
    ".DS_Store",
]


class GitManagerError(RuntimeError):
    """Raised when project Git safety setup cannot be completed."""


class GitManagerAgent:
    """Initialize and snapshot a project-local Git repository."""

    def __init__(self, *, git_binary: str = "git") -> None:
        self.git_binary = git_binary

    def ensure_repository(self, project_path: str | Path) -> tuple[GitRepositoryMetadata, GitSnapshotMetadata | None]:
        project = self._project(project_path)
        project.mkdir(parents=True, exist_ok=True)
        self._ensure_git_available()

        initialized_now = not (project / ".git").exists()
        if initialized_now:
            self._git(project, "init")
        self._ensure_local_identity(project)
        ignore_rules = self._ensure_gitignore(project)

        baseline = self.snapshot(
            project,
            reason="initial_baseline" if initialized_now else "environment_sync",
            message="openpilot: initialize project safety baseline" if initialized_now else "openpilot: environment safety snapshot",
        )
        repository = self.repository_metadata(project, ignored_paths=ignore_rules)
        return repository, baseline

    def snapshot(
        self,
        project_path: str | Path,
        *,
        reason: str,
        target_files: list[str] | None = None,
        message: str | None = None,
    ) -> GitSnapshotMetadata:
        project = self._project(project_path)
        self._ensure_git_available()
        if not (project / ".git").exists():
            self._git(project, "init")
            self._ensure_local_identity(project)
            self._ensure_gitignore(project)

        status_before = self._status(project)
        changed_files = self._changed_files(status_before)
        if not status_before:
            return GitSnapshotMetadata(
                project_path=str(project),
                reason=reason,
                message=message or self._snapshot_message(reason),
                commit_hash=self._head(project),
                created=False,
                skipped=True,
                changed_files=[],
                status_before=[],
                status_after=[],
            )

        self._git(project, "add", "-A", "--", ".")
        commit_message = message or self._snapshot_message(reason)
        commit = self._git(project, "commit", "-m", commit_message)
        status_after = self._status(project)
        return GitSnapshotMetadata(
            project_path=str(project),
            reason=reason,
            message=commit_message,
            commit_hash=self._head(project),
            created=True,
            skipped=False,
            changed_files=changed_files or [str(item) for item in target_files or []],
            status_before=status_before,
            status_after=status_after,
            annotations={"commit_output": commit.stdout.strip()[-1000:]},
        )

    def diff_context(
        self,
        project_path: str | Path,
        *,
        base_ref: str = "HEAD",
        target_files: list[str] | None = None,
        max_chars: int = 6000,
    ) -> GitDiffContextMetadata:
        project = self._project(project_path)
        self._ensure_git_available()
        status = self._status(project)
        paths = [self._relative_to_project(project, item) for item in target_files or [] if item]
        diff_args = ["diff", "--stat", base_ref]
        preview_args = ["diff", base_ref, "--"]
        if paths:
            diff_args.extend(["--", *paths])
            preview_args.extend(paths)
        stat = self._git(project, *diff_args, check=False).stdout.strip()
        preview = self._git(project, *preview_args, check=False).stdout
        if len(preview) > max_chars:
            preview = preview[:max_chars] + "\n...[truncated]"
        return GitDiffContextMetadata(
            project_path=str(project),
            base_ref=base_ref,
            head_ref=self._head(project),
            status=status,
            changed_files=self._changed_files(status),
            diff_stat=stat,
            diff_preview=preview,
            target_files=[str(item) for item in target_files or []],
        )

    def restore_files(
        self,
        project_path: str | Path,
        *,
        source_ref: str,
        target_files: list[str],
    ) -> list[str]:
        """Restore explicit project-local files to one safety snapshot."""

        project = self._project(project_path)
        self._ensure_git_available()
        if not source_ref:
            raise GitManagerError("restore requires a safety snapshot ref")
        restored: list[str] = []
        for raw_path in target_files:
            candidate = Path(raw_path).expanduser()
            resolved = candidate.resolve() if candidate.is_absolute() else (project / candidate).resolve()
            try:
                relative = resolved.relative_to(project)
            except ValueError as exc:
                raise GitManagerError(f"restore target escapes project boundary: {raw_path}") from exc
            if not relative.parts:
                raise GitManagerError("restore target cannot be the project root")
            relative_text = str(relative)
            tracked = self._git(
                project,
                "cat-file",
                "-e",
                f"{source_ref}:{relative_text}",
                check=False,
            ).returncode == 0
            if tracked:
                self._git(
                    project,
                    "restore",
                    "--source",
                    source_ref,
                    "--staged",
                    "--worktree",
                    "--",
                    relative_text,
                )
            elif resolved.exists() and resolved.is_file():
                resolved.unlink()
            restored.append(str(resolved))
        return restored

    def repository_metadata(self, project_path: str | Path, *, ignored_paths: list[str] | None = None) -> GitRepositoryMetadata:
        project = self._project(project_path)
        status = self._status(project) if (project / ".git").exists() else []
        return GitRepositoryMetadata(
            project_path=str(project),
            initialized=(project / ".git").exists(),
            branch=self._branch(project),
            head=self._head(project),
            dirty=bool(status),
            status=status,
            ignored_paths=ignored_paths or self._read_gitignore(project),
        )

    def _ensure_git_available(self) -> None:
        if not shutil.which(self.git_binary):
            raise GitManagerError("git executable is not available")

    def _ensure_local_identity(self, project: Path) -> None:
        if not self._git(project, "config", "user.email", check=False).stdout.strip():
            self._git(project, "config", "user.email", "openpilot@local")
        if not self._git(project, "config", "user.name", check=False).stdout.strip():
            self._git(project, "config", "user.name", "OpenPilot Safety Agent")

    def _ensure_gitignore(self, project: Path) -> list[str]:
        path = project / ".gitignore"
        existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        normalized = {line.strip() for line in existing}
        additions = [rule for rule in DEFAULT_IGNORE_RULES if rule not in normalized]
        if additions:
            prefix = "\n" if existing and existing[-1].strip() else ""
            path.write_text("\n".join(existing) + prefix + "\n".join(additions) + "\n", encoding="utf-8")
        return self._read_gitignore(project)

    def _read_gitignore(self, project: Path) -> list[str]:
        path = project / ".gitignore"
        if not path.exists():
            return []
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.strip().startswith("#")]

    def _git(self, project: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [self.git_binary, *args],
            cwd=project,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
            raise GitManagerError(f"git {' '.join(args)} failed: {detail}")
        return result

    def _status(self, project: Path) -> list[str]:
        return [line for line in self._git(project, "status", "--porcelain", check=False).stdout.splitlines() if line]

    def _head(self, project: Path) -> str:
        return self._git(project, "rev-parse", "--short", "HEAD", check=False).stdout.strip()

    def _branch(self, project: Path) -> str:
        return self._git(project, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()

    def _changed_files(self, status: list[str]) -> list[str]:
        return [line[3:].strip() for line in status if len(line) > 3]

    def _relative_to_project(self, project: Path, raw_path: str) -> str:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            return str(path)
        try:
            return str(path.resolve().relative_to(project))
        except ValueError:
            return str(path)

    def _project(self, project_path: str | Path) -> Path:
        return Path(project_path).expanduser().resolve()

    def _snapshot_message(self, reason: str) -> str:
        label = " ".join(str(reason or "safety_snapshot").replace("_", " ").split())[:60]
        return f"openpilot: safety snapshot before {label}"
