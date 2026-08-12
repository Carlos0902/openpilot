"""Atomic persistence for conversation-owned iteration turn records."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import fcntl

from metadata import (
    DurableArtifactReference,
    IterationTurnRecordMetadata,
    SessionIngressState,
)


class IterationTurnConflictError(RuntimeError):
    """Raised when a stale writer conflicts with durable conversation state."""


class IterationTurnSecretError(ValueError):
    """Raised when a persisted iteration payload contains a credential field."""


class IterationTurnStore:
    """Persist immutable turn generations, artifacts, and conversation ingress."""

    def __init__(
        self,
        root_dir: str | Path,
        *,
        warning_sink: Callable[[str], None] | None = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.warning_sink = warning_sink
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        record: IterationTurnRecordMetadata,
        *,
        expected_generation: int | None = None,
    ) -> IterationTurnRecordMetadata:
        payload = record.to_json_dict()
        sensitive_path = self._find_sensitive_key(payload)
        if sensitive_path:
            raise IterationTurnSecretError(
                f"iteration turn record contains sensitive field: {sensitive_path}"
            )
        run_dir = self._run_dir(record.identity.conversation_id, record.identity.run_id)
        records_dir = run_dir / "records"
        records_dir.mkdir(parents=True, exist_ok=True)
        with self._lock(run_dir / ".turn.lock"):
            current_generation = self._current_generation(
                record.identity.conversation_id,
                record.identity.run_id,
            )
            if expected_generation is not None and current_generation != expected_generation:
                raise IterationTurnConflictError(
                    f"turn generation conflict: expected {expected_generation}, found {current_generation}"
                )
            if record.generation <= current_generation:
                raise IterationTurnConflictError(
                    f"turn generation must advance beyond {current_generation}, got {record.generation}"
                )
            payload["integrity_digest"] = self._checksum(payload, digest_field="integrity_digest")
            saved = IterationTurnRecordMetadata.model_validate(payload)
            path = records_dir / f"{self._safe_id(saved.record_id)}.json"
            if path.exists():
                raise IterationTurnConflictError(f"iteration turn record already exists: {saved.record_id}")
            self._atomic_write_json(path, saved.to_json_dict())
            self._atomic_write_json(
                run_dir / "latest_record.json",
                {
                    "record_id": saved.record_id,
                    "generation": saved.generation,
                    "integrity_digest": saved.integrity_digest,
                },
            )
            return saved

    def load(
        self,
        conversation_id: str,
        run_id: str,
        record_id: str,
    ) -> IterationTurnRecordMetadata | None:
        path = self._run_dir(conversation_id, run_id) / "records" / f"{self._safe_id(record_id)}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._warn(f"iteration turn record {record_id} could not be loaded: {exc}")
            return None
        if not isinstance(payload, dict):
            self._warn(f"iteration turn record {record_id} is not a JSON object")
            return None
        if payload.get("integrity_digest") != self._checksum(payload, digest_field="integrity_digest"):
            self._warn(f"iteration turn record {record_id} checksum mismatch")
            return None
        try:
            record = IterationTurnRecordMetadata.model_validate(payload)
        except ValueError as exc:
            self._warn(f"iteration turn record {record_id} could not be loaded: {exc}")
            return None
        if (
            record.identity.conversation_id != conversation_id
            or record.identity.run_id != run_id
        ):
            self._warn(f"iteration turn record {record_id} identity mismatch")
            return None
        return record

    def load_latest(
        self,
        conversation_id: str,
        run_id: str,
    ) -> IterationTurnRecordMetadata | None:
        run_dir = self._run_dir(conversation_id, run_id)
        pointer_path = run_dir / "latest_record.json"
        latest_id = ""
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            latest_id = str(pointer["record_id"])
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            self._warn(f"latest iteration turn pointer is invalid: {exc}")
        if latest_id:
            latest = self.load(conversation_id, run_id, latest_id)
            if latest is not None:
                return latest
        candidates: list[IterationTurnRecordMetadata] = []
        records_dir = run_dir / "records"
        if records_dir.exists():
            for path in records_dir.glob("*.json"):
                if path.stem == latest_id:
                    continue
                record = self.load(conversation_id, run_id, path.stem)
                if record is not None:
                    candidates.append(record)
        if not candidates:
            return None
        fallback = max(candidates, key=lambda item: item.generation)
        self._warn(f"falling back to iteration turn record {fallback.record_id}")
        return fallback

    def save_artifact(
        self,
        conversation_id: str,
        run_id: str,
        *,
        kind: str,
        payload: dict[str, Any],
    ) -> DurableArtifactReference:
        sensitive_path = self._find_sensitive_key(payload)
        if sensitive_path:
            raise IterationTurnSecretError(f"iteration artifact contains sensitive field: {sensitive_path}")
        envelope = {"kind": kind, "payload": payload}
        encoded = self._encode(envelope)
        checksum = "sha256:" + hashlib.sha256(encoded).hexdigest()
        artifact_id = checksum.removeprefix("sha256:")
        path = self._run_dir(conversation_id, run_id) / "artifacts" / f"{artifact_id}.json"
        with self._lock(path.parent / ".artifact.lock"):
            if path.exists():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    raise IterationTurnConflictError(
                        f"existing iteration artifact is unreadable: {artifact_id}"
                    ) from exc
                if existing != envelope:
                    raise IterationTurnConflictError(
                        f"content-addressed artifact collision: {artifact_id}"
                    )
            else:
                self._atomic_write_json(path, envelope)
        return DurableArtifactReference(
            artifact_id=artifact_id,
            kind=kind,
            integrity_checksum=checksum,
            bytes=len(encoded),
        )

    def load_artifact(
        self,
        conversation_id: str,
        run_id: str,
        reference: DurableArtifactReference,
    ) -> dict[str, Any] | None:
        path = (
            self._run_dir(conversation_id, run_id)
            / "artifacts"
            / f"{self._safe_id(reference.artifact_id)}.json"
        )
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._warn(f"iteration artifact {reference.artifact_id} could not be loaded: {exc}")
            return None
        if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), dict):
            self._warn(f"iteration artifact {reference.artifact_id} envelope is invalid")
            return None
        if envelope.get("kind") != reference.kind:
            self._warn(f"iteration artifact {reference.artifact_id} kind mismatch")
            return None
        encoded = self._encode(envelope)
        checksum = "sha256:" + hashlib.sha256(encoded).hexdigest()
        if checksum != reference.integrity_checksum or len(encoded) != reference.bytes:
            self._warn(f"iteration artifact {reference.artifact_id} checksum mismatch")
            return None
        return envelope["payload"]

    def save_ingress(
        self,
        state: SessionIngressState,
        *,
        expected_revision: int,
    ) -> int:
        conversation_dir = self._conversation_dir(state.identity.conversation_id)
        path = conversation_dir / "session_ingress.json"
        with self._lock(conversation_dir / ".ingress.lock"):
            _current, current_revision = self.load_ingress(state.identity.conversation_id)
            if path.exists() and _current is None:
                raise IterationTurnConflictError(
                    "existing session ingress is unreadable and cannot be overwritten"
                )
            if current_revision != expected_revision:
                raise IterationTurnConflictError(
                    f"ingress revision conflict: expected {expected_revision}, found {current_revision}"
                )
            revision = current_revision + 1
            payload: dict[str, Any] = {
                "revision": revision,
                "state": state.model_dump(mode="json"),
                "integrity_digest": "",
            }
            sensitive_path = self._find_sensitive_key(payload)
            if sensitive_path:
                raise IterationTurnSecretError(
                    f"session ingress contains sensitive field: {sensitive_path}"
                )
            payload["integrity_digest"] = self._checksum(payload, digest_field="integrity_digest")
            self._atomic_write_json(path, payload)
            return revision

    def load_ingress(
        self,
        conversation_id: str,
    ) -> tuple[SessionIngressState | None, int]:
        path = self._conversation_dir(conversation_id) / "session_ingress.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, 0
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._warn(f"session ingress could not be loaded: {exc}")
            return None, 0
        if not isinstance(payload, dict):
            self._warn("session ingress is not a JSON object")
            return None, 0
        if payload.get("integrity_digest") != self._checksum(payload, digest_field="integrity_digest"):
            self._warn("session ingress checksum mismatch")
            return None, 0
        try:
            revision = int(payload["revision"])
            if revision < 1:
                raise ValueError("session ingress revision must be positive")
            state = SessionIngressState.model_validate(payload["state"])
        except (KeyError, TypeError, ValueError) as exc:
            self._warn(f"session ingress could not be loaded: {exc}")
            return None, 0
        if state.identity.conversation_id != conversation_id:
            self._warn("session ingress conversation identity mismatch")
            return None, 0
        return state, revision

    def _current_generation(self, conversation_id: str, run_id: str) -> int:
        records_dir = self._run_dir(conversation_id, run_id) / "records"
        generations = [0]
        if records_dir.exists():
            for path in records_dir.glob("*.json"):
                record = self.load(conversation_id, run_id, path.stem)
                if record is None:
                    raise IterationTurnConflictError(
                        f"existing iteration record is unreadable: {path.stem}"
                    )
                generations.append(record.generation)
        return max(generations)

    def _conversation_dir(self, conversation_id: str) -> Path:
        return self.root_dir / self._safe_id(conversation_id)

    def _run_dir(self, conversation_id: str, run_id: str) -> Path:
        return self._conversation_dir(conversation_id) / self._safe_id(run_id)

    @staticmethod
    def _safe_id(value: str) -> str:
        if not value or value in {".", ".."} or Path(value).name != value:
            raise ValueError(f"unsafe iteration identifier: {value!r}")
        return value

    @staticmethod
    def _encode(payload: dict[str, Any]) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def _checksum(cls, payload: dict[str, Any], *, digest_field: str) -> str:
        canonical = dict(payload)
        canonical[digest_field] = ""
        return "sha256:" + hashlib.sha256(cls._encode(canonical)).hexdigest()

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
    @contextmanager
    def _lock(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
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


__all__ = [
    "IterationTurnConflictError",
    "IterationTurnSecretError",
    "IterationTurnStore",
]
