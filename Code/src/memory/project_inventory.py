"""Bounded, read-only project file discovery."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_IGNORED_DIRECTORIES = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "site-packages"})


@dataclass(frozen=True)
class ProjectFileInventory:
    files: tuple[Path, ...]
    directories_scanned: int
    entries_scanned: int
    truncated: bool


def collect_project_files(
    project_root: str | Path,
    *,
    suffixes: set[str] | frozenset[str] | None = None,
    max_files: int = 200,
    max_directories: int = 256,
    max_entries: int = 4096,
    max_depth: int = 8,
    ignored_directories: set[str] | frozenset[str] = DEFAULT_IGNORED_DIRECTORIES,
) -> ProjectFileInventory:
    if min(max_files, max_directories, max_entries) <= 0 or max_depth < 0:
        raise ValueError("project inventory bounds must be positive")
    root = Path(project_root).expanduser().resolve(strict=False)
    if not root.is_dir():
        return ProjectFileInventory((), 0, 0, False)
    wanted = {suffix.casefold() for suffix in suffixes} if suffixes else None
    queue = deque([(root, 0)])
    files: list[Path] = []
    directories_scanned = 0
    entries_scanned = 0
    truncated = False
    while queue:
        if directories_scanned >= max_directories or entries_scanned >= max_entries:
            truncated = True
            break
        directory, depth = queue.popleft()
        directories_scanned += 1
        entries: list[os.DirEntry[str]] = []
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if entries_scanned >= max_entries:
                        truncated = True
                        break
                    entries_scanned += 1
                    entries.append(entry)
        except OSError:
            continue
        for entry in sorted(entries, key=lambda item: item.name.casefold()):
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in ignored_directories:
                        continue
                    if depth < max_depth:
                        queue.append((Path(entry.path), depth + 1))
                    else:
                        truncated = True
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            path = Path(entry.path)
            if wanted is None or path.suffix.casefold() in wanted:
                files.append(path)
                if len(files) >= max_files:
                    return ProjectFileInventory(tuple(sorted(files)), directories_scanned, entries_scanned, True)
    return ProjectFileInventory(tuple(sorted(files)), directories_scanned, entries_scanned, truncated)
