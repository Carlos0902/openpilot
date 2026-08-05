"""Atomic persistence for resumable runtime checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fcntl

from metadata import DurableArtifactReference, RuntimeCheckpointMetadata


class CheckpointConflictError(RuntimeError):
    """Raised when a stale writer attempts to replace a newer generation."""


class CheckpointSecretError(ValueError):
    """Raised when checkpoint state contains a recognized secret field."""


class RuntimeCheckpointStore:
    """Persist immutable checkpoint generations and an atomic latest pointer."""

    def __init__(self, root_dir: Path | str, *, warning_sink: Callable[[str], None] | None = None) -> None:
        self.root_dir = Path(root_dir)
        self.warning_sink = warning_sink
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        checkpoint: RuntimeCheckpointMetadata,
        *,
        expected_generation: int | None = None,
    ) -> RuntimeCheckpointMetadata:
        initial_payload = checkpoint.to_json_dict()
        sensitive_path = self._find_sensitive_key(initial_payload)
        if sensitive_path:
            raise CheckpointSecretError(f"checkpoint contains sensitive field: {sensitive_path}")
        run_dir = self._run_dir(checkpoint.run_id)
        checkpoints_dir = run_dir / "checkpoints"
        checkpoints_dir.mkdir(parents=True, exist_ok=True)
        with self._run_lock(run_dir):
            current_generation = self._current_generation(checkpoint.run_id)
            if expected_generation is not None and current_generation != expected_generation:
                raise CheckpointConflictError(
                    f"checkpoint generation conflict: expected {expected_generation}, found {current_generation}"
                )
            if checkpoint.generation <= current_generation:
                raise CheckpointConflictError(
                    f"checkpoint generation must advance beyond {current_generation}, got {checkpoint.generation}"
                )

            payload = initial_payload
            payload["integrity_checksum"] = self._checksum(payload)
            saved = RuntimeCheckpointMetadata.model_validate(payload)
            checkpoint_path = checkpoints_dir / f"{self._safe_id(saved.checkpoint_id)}.json"
            if checkpoint_path.exists():
                raise CheckpointConflictError(f"checkpoint already exists: {saved.checkpoint_id}")
            self._atomic_write_json(checkpoint_path, saved.to_json_dict())
            pointer = {
                "checkpoint_id": saved.checkpoint_id,
                "generation": saved.generation,
                "integrity_checksum": saved.integrity_checksum,
            }
            self._atomic_write_json(run_dir / "latest_checkpoint.json", pointer)
            return saved

    def load(self, run_id: str, checkpoint_id: str) -> RuntimeCheckpointMetadata | None:
        path = self._run_dir(run_id) / "checkpoints" / f"{self._safe_id(checkpoint_id)}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            checkpoint = RuntimeCheckpointMetadata.model_validate(payload)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._warn(f"checkpoint {checkpoint_id} could not be loaded: {exc}")
            return None
        if checkpoint.run_id != run_id:
            self._warn(f"checkpoint {checkpoint_id} belongs to a different run")
            return None
        if checkpoint.integrity_checksum != self._checksum(payload):
            self._warn(f"checkpoint {checkpoint_id} checksum mismatch")
            return None
        return checkpoint

    def checkpoint_exists(self, run_id: str, checkpoint_id: str) -> bool:
        """Return whether the explicitly identified checkpoint file exists."""
        path = self._run_dir(run_id) / "checkpoints" / f"{self._safe_id(checkpoint_id)}.json"
        return path.is_file()

    def save_recovery_artifact(
        self,
        run_id: str,
        *,
        kind: str,
        payload: dict[str, Any],
    ) -> DurableArtifactReference:
        """Atomically persist one checksum-addressed recovery payload."""
        sensitive_path = self._find_sensitive_key(payload)
        if sensitive_path:
            raise CheckpointSecretError(f"recovery artifact contains sensitive field: {sensitive_path}")
        artifact_id = uuid.uuid4().hex
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        checksum = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
        path = self._run_dir(run_id) / "recovery_artifacts" / f"{artifact_id}.json"
        self._atomic_write_json(path, payload)
        return DurableArtifactReference(
            artifact_id=artifact_id,
            kind=kind,
            integrity_checksum=checksum,
            bytes=len(encoded),
        )

    def load_recovery_artifact(
        self,
        run_id: str,
        reference: DurableArtifactReference,
    ) -> dict[str, Any] | None:
        """Load an artifact only when its path, JSON shape, checksum, and size match."""
        path = self._run_dir(run_id) / "recovery_artifacts" / f"{self._safe_id(reference.artifact_id)}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._warn(f"recovery artifact {reference.artifact_id} could not be loaded: {exc}")
            return None
        if not isinstance(payload, dict):
            self._warn(f"recovery artifact {reference.artifact_id} is not a JSON object")
            return None
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        checksum = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
        if checksum != reference.integrity_checksum or len(encoded) != reference.bytes:
            self._warn(f"recovery artifact {reference.artifact_id} checksum mismatch")
            return None
        return payload

    def try_acquire_run_lease(self, run_id: str) -> Any | None:
        """Acquire the non-blocking single-writer lease for one runtime run."""
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        handle = (run_dir / ".runtime.lease").open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid()}))
        handle.flush()
        os.fsync(handle.fileno())
        return handle

    @staticmethod
    def release_run_lease(handle: Any | None) -> None:
        """Release a lease returned by :meth:`try_acquire_run_lease`."""
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def load_latest(self, run_id: str) -> RuntimeCheckpointMetadata | None:
        pointer_path = self._run_dir(run_id) / "latest_checkpoint.json"
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            latest_id = str(pointer["checkpoint_id"])
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            self._warn(f"latest checkpoint pointer for {run_id} is invalid: {exc}")
            latest_id = ""
        if latest_id:
            checkpoint = self.load(run_id, latest_id)
            if checkpoint is not None:
                return checkpoint
        return self._load_previous_valid(run_id, excluding=latest_id)

    def _load_previous_valid(self, run_id: str, *, excluding: str = "") -> RuntimeCheckpointMetadata | None:
        directory = self._run_dir(run_id) / "checkpoints"
        candidates: list[RuntimeCheckpointMetadata] = []
        if not directory.exists():
            return None
        for path in directory.glob("*.json"):
            checkpoint_id = path.stem
            if checkpoint_id == excluding:
                continue
            checkpoint = self.load(run_id, checkpoint_id)
            if checkpoint is not None:
                candidates.append(checkpoint)
        if not candidates:
            return None
        fallback = max(candidates, key=lambda item: item.generation)
        self._warn(f"falling back to checkpoint {fallback.checkpoint_id} for run {run_id}")
        return fallback

    def _current_generation(self, run_id: str) -> int:
        pointer_path = self._run_dir(run_id) / "latest_checkpoint.json"
        pointer_generation = 0
        try:
            if pointer_path.exists():
                pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
                pointer_generation = int(pointer.get("generation") or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            pointer_generation = 0
        durable_generations = [pointer_generation]
        checkpoints_dir = self._run_dir(run_id) / "checkpoints"
        if checkpoints_dir.exists():
            for path in checkpoints_dir.glob("*.json"):
                checkpoint = self.load(run_id, path.stem)
                if checkpoint is not None:
                    durable_generations.append(checkpoint.generation)
        return max(durable_generations, default=0)

    def _run_dir(self, run_id: str) -> Path:
        return self.root_dir / self._safe_id(run_id)

    @staticmethod
    @contextmanager
    def _run_lock(run_dir: Path):
        run_dir.mkdir(parents=True, exist_ok=True)
        lock_path = run_dir / ".checkpoint.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _safe_id(value: str) -> str:
        if not value or value in {".", ".."} or Path(value).name != value:
            raise ValueError(f"unsafe checkpoint identifier: {value!r}")
        return value

    @staticmethod
    def _checksum(payload: dict[str, Any]) -> str:
        canonical = dict(payload)
        canonical["integrity_checksum"] = ""
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    @classmethod
    def _find_sensitive_key(cls, value: Any, *, path: str = "") -> str:
        sensitive_keys = {
            "api_key",
            "access_token",
            "refresh_token",
            "authorization",
            "password",
            "client_secret",
            "private_key",
        }
        if isinstance(value, dict):
            for key, nested in value.items():
                nested_path = f"{path}.{key}" if path else str(key)
                if str(key).lower() in sensitive_keys:
                    return nested_path
                found = cls._find_sensitive_key(nested, path=nested_path)
                if found:
                    return found
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                found = cls._find_sensitive_key(nested, path=f"{path}[{index}]")
                if found:
                    return found
        return ""

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _warn(self, message: str) -> None:
        if self.warning_sink is not None:
            self.warning_sink(message)
