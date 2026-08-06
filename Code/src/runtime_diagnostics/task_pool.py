"""Task-pool loading helpers for real-task diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime_diagnostics.raw_task import RawTaskInput


def load_raw_tasks(path: str | Path) -> list[RawTaskInput]:
    """Load RawTaskInput records from a file or directory.

    Supported inputs:
    - one JSON object
    - a JSON list of objects
    - JSONL (one object per line)
    - a directory containing `.json` and `.jsonl` files
    """
    source = Path(path)
    if source.is_dir():
        tasks: list[RawTaskInput] = []
        for child in sorted(source.iterdir()):
            if child.suffix.lower() not in {".json", ".jsonl"}:
                continue
            tasks.extend(load_raw_tasks(child))
        return tasks

    if source.suffix.lower() == ".jsonl":
        return _load_jsonl(source)
    if source.suffix.lower() == ".json":
        return _load_json(source)
    raise ValueError(f"Unsupported task-pool input: {source}")


def _load_json(path: Path) -> list[RawTaskInput]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return [RawTaskInput.model_validate(payload)]
    if isinstance(payload, list):
        return [RawTaskInput.model_validate(item) for item in payload]
    raise ValueError(f"JSON task file must contain an object or list: {path}")


def _load_jsonl(path: Path) -> list[RawTaskInput]:
    tasks: list[RawTaskInput] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        tasks.append(RawTaskInput.model_validate(json.loads(stripped)))
    return tasks
