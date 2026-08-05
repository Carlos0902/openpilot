"""JSONL recorder for runtime diagnostic records."""

from __future__ import annotations

import json
import fcntl
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from metadata import ProblemJudgmentMetadata, ProblemSignalMetadata
from metadata.base import json_safe

from runtime_diagnostics.models import ArtifactRecord, DiagnosticRecord, EventRecord, RunRecord, RunSummaryRecord


DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "runtime_diagnostics"


class DiagnosticRecorder:
    """Persist runtime diagnostic records in append-only JSONL form."""

    def __init__(self, data_dir: Path | str = DEFAULT_DATA_DIR):
        self.data_dir = Path(data_dir)
        self.issues_file = self.data_dir / "issues.jsonl"
        self.runs_file = self.data_dir / "runs.jsonl"
        self.trajectory_dir = self.data_dir / "task_trajectory"
        self._run_ids_by_key: dict[str, str] = {}
        self._event_sequences: dict[str, int] = {}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.trajectory_dir.mkdir(parents=True, exist_ok=True)

    def record_signal(
        self,
        signal: ProblemSignalMetadata,
        judgment: ProblemJudgmentMetadata | None = None,
    ) -> DiagnosticRecord:
        record = DiagnosticRecord(
            task_id=signal.task_id,
            signal=signal.to_json_dict(),
            judgment=judgment.to_json_dict() if judgment else None,
        )
        self._append_jsonl(self.issues_file, record.model_dump(mode="python"))
        return record

    def record_judgment(
        self,
        signal: ProblemSignalMetadata,
        judgment: ProblemJudgmentMetadata,
    ) -> DiagnosticRecord:
        return self.record_signal(signal, judgment)

    def record_run(self, payload: dict[str, Any], *, mirror_to_trajectory: bool = True) -> None:
        self._append_jsonl(self.runs_file, payload)
        if mirror_to_trajectory:
            self._record_event_from_legacy_payload(payload)

    def ensure_run(
        self,
        task_key: str,
        *,
        source: str = "",
        raw_input: str = "",
        goal: str = "",
        session_id: str = "",
        route: str = "",
    ) -> RunRecord:
        task_key = str(task_key or "").strip() or "unknown"
        existing_run_id = self._run_ids_by_key.get(task_key)
        if existing_run_id is None and session_id:
            existing_run_id = self._run_ids_by_key.get(str(session_id))
        if existing_run_id is None:
            direct_run = self.load_run(task_key)
            if direct_run is not None and direct_run.run_id == task_key:
                existing_run_id = direct_run.run_id
        if existing_run_id:
            run = self.load_run(existing_run_id)
            if run is None:
                self._run_ids_by_key.pop(task_key, None)
            else:
                self._register_run_alias(existing_run_id, task_key)
                if session_id:
                    self._register_run_alias(existing_run_id, str(session_id))
                return self.update_run(
                    existing_run_id,
                    task_id=run.task_id or task_key,
                    session_id=run.session_id or str(session_id or ""),
                    source=run.source or source,
                    raw_input=run.raw_input or raw_input,
                    goal=run.goal or goal,
                    route=run.route or route,
                )

        run = RunRecord(
            task_id=task_key,
            session_id=str(session_id or ""),
            source=source,
            raw_input=raw_input,
            goal=goal,
            route=route,
        )
        run_dir = self._run_dir(run.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(self._run_file(run.run_id), run.model_dump(mode="python"))
        self._register_run_alias(run.run_id, task_key)
        if session_id:
            self._register_run_alias(run.run_id, str(session_id))
        self._event_sequences.setdefault(run.run_id, 0)
        return run

    def update_run(self, run_key: str, **fields: Any) -> RunRecord:
        run_id = self._resolve_run_id(run_key)
        if run_id is None:
            run = self.ensure_run(str(run_key))
            run_id = run.run_id
        run = self.load_run(run_id) or RunRecord(run_id=run_id, task_id=str(run_key))
        data = run.model_dump(mode="python")
        for key, value in fields.items():
            if value in (None, ""):
                continue
            data[key] = value
        updated = RunRecord.model_validate(data)
        self._write_json(self._run_file(run_id), updated.model_dump(mode="python"))
        self._register_run_alias(run_id, updated.task_id)
        if updated.session_id:
            self._register_run_alias(run_id, updated.session_id)
        return updated

    def attach_existing_run(
        self,
        run_id: str,
        *,
        expected_task_id: str = "",
        expected_session_id: str = "",
    ) -> RunRecord:
        """Bind persisted run aliases in a replacement process without creating a run."""
        run = self.load_run(run_id)
        if run is None or run.run_id != str(run_id):
            raise ValueError(f"existing run not found: {run_id}")
        if expected_task_id and run.task_id != str(expected_task_id):
            raise ValueError("existing run task identity does not match")
        if expected_session_id and run.session_id != str(expected_session_id):
            raise ValueError("existing run session identity does not match")
        self._register_run_alias(run.run_id, run.run_id)
        self._register_run_alias(run.run_id, run.task_id)
        if run.session_id:
            self._register_run_alias(run.run_id, run.session_id)
        return run

    def load_run(self, run_key: str) -> RunRecord | None:
        run_id = self._resolve_run_id(run_key) or str(run_key)
        path = self._run_file(run_id)
        if not path.exists():
            return None
        try:
            return RunRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, ValueError):
            return None

    def record_event(
        self,
        task_key: str,
        *,
        event_type: str,
        payload: Any = None,
        payload_kind: str = "",
        source: str = "",
        raw_input: str = "",
        goal: str = "",
        route: str = "",
        session_id: str = "",
        phase: str = "",
        summary: str = "",
        idempotency_key: str = "",
    ) -> EventRecord:
        normalized_payload_kind, normalized_payload = self._normalize_event_payload(payload_kind=payload_kind, payload=payload)
        resolved_task_key = str(self._payload_task_id(normalized_payload) or task_key or self._payload_session_id(normalized_payload) or "").strip()
        resolved_session_id = str(session_id or self._payload_session_id(normalized_payload) or "")
        run = self.ensure_run(
            resolved_task_key or "unknown",
            source=source or self._payload_source(normalized_payload),
            raw_input=raw_input or self._payload_raw_input(normalized_payload),
            goal=goal or self._payload_goal(normalized_payload),
            session_id=resolved_session_id or str(normalized_payload.get("session_id") or ""),
            route=route or self._payload_route(normalized_payload),
        )
        normalized_payload = self._normalize_payload_identity(
            normalized_payload,
            root_task_id=run.task_id,
            session_id=run.session_id or resolved_session_id,
        )
        with self._event_write_lock(run.run_id):
            event = self._find_idempotent_event(run.run_id, event_type, idempotency_key)
            created = event is None
            if event is None:
                sequence = self._next_event_sequence(run.run_id)
                event = EventRecord(
                    run_id=run.run_id,
                    sequence=sequence,
                    event_type=event_type,
                    idempotency_key=idempotency_key,
                    task_id=run.task_id,
                    session_id=run.session_id or resolved_session_id,
                    phase=phase or str(normalized_payload.get("phase") or self._payload_phase(normalized_payload) or ""),
                    summary=summary or self._default_summary(event_type, normalized_payload),
                    payload_kind=normalized_payload_kind,
                    payload=normalized_payload,
                )
                self._append_jsonl(self._events_file(run.run_id), event.model_dump(mode="python"))
        if created:
            legacy_payload = {
                "run_id": run.run_id,
                "event_id": event.event_id,
                "sequence": event.sequence,
                "event": event_type,
                "payload_kind": normalized_payload_kind,
                "payload": normalized_payload,
            }
            legacy_payload.setdefault("task_id", run.task_id)
            if run.session_id:
                legacy_payload.setdefault("session_id", run.session_id)
            self.record_run(legacy_payload, mirror_to_trajectory=False)
        self._apply_run_update_from_event(run, event)
        return event

    def _find_idempotent_event(
        self,
        run_id: str,
        event_type: str,
        idempotency_key: str,
    ) -> EventRecord | None:
        if not idempotency_key:
            return None
        path = self._events_file(run_id)
        if not path.exists():
            return None
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = EventRecord.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
            if event.event_type == event_type and event.idempotency_key == idempotency_key:
                return event
        return None

    def record_artifact(
        self,
        run_key: str,
        *,
        kind: str,
        content: str | bytes,
        filename: str | None = None,
        content_type: str = "text/plain",
        source_event_id: str = "",
    ) -> ArtifactRecord:
        run = self.ensure_run(str(run_key))
        artifact_id = filename or f"{kind}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}"
        artifact_path = self._artifacts_dir(run.run_id) / artifact_id
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            artifact_path.write_bytes(content)
            byte_count = len(content)
        else:
            artifact_path.write_text(content, encoding="utf-8")
            byte_count = len(content.encode("utf-8"))
        artifact = ArtifactRecord(
            run_id=run.run_id,
            kind=kind,
            path=str(artifact_path),
            content_type=content_type,
            bytes=byte_count,
            source_event_id=source_event_id,
        )
        self._append_jsonl(self._artifacts_index_file(run.run_id), artifact.model_dump(mode="python"))
        self._refresh_summary(run.run_id)
        return artifact

    def load_recent_records(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.issues_file.exists():
            return []
        lines = [line for line in self.issues_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        selected = lines[-limit:] if limit > 0 else lines
        records: list[dict[str, Any]] = []
        for line in selected:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def load_run_events(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.runs_file.exists():
            return []
        lines = [line for line in self.runs_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        selected = lines[-limit:] if limit > 0 else lines
        events: list[dict[str, Any]] = []
        for line in selected:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def load_trajectory_events(self, run_key: str, limit: int = 0) -> list[dict[str, Any]]:
        run_id = self._resolve_run_id(run_key) or str(run_key)
        path = self._events_file(run_id)
        if not path.exists():
            return []
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        selected = lines[-limit:] if limit > 0 else lines
        events: list[dict[str, Any]] = []
        for line in selected:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def load_run_summary(self, run_key: str) -> RunSummaryRecord | None:
        run_id = self._resolve_run_id(run_key) or str(run_key)
        path = self._summary_file(run_id)
        if not path.exists():
            return None
        try:
            return RunSummaryRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, ValueError):
            return None

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(json_safe(payload), ensure_ascii=False, sort_keys=True) + "\n")

    def _record_event_from_legacy_payload(self, payload: dict[str, Any]) -> None:
        event_type = str(payload.get("event") or "").strip()
        task_id = str(payload.get("task_id") or "").strip()
        if not event_type or not task_id:
            return
        session_id = str(payload.get("session_id") or "")
        if event_type == "task_received":
            self.ensure_run(
                task_id,
                source=str(payload.get("source") or ""),
                raw_input=str(payload.get("raw_input") or ""),
                session_id=session_id,
            )
        else:
            self.ensure_run(task_id, session_id=session_id)
        run_id = self._resolve_run_id(session_id) or self._resolve_run_id(task_id)
        if run_id is None:
            return
        normalized_payload_kind, normalized_payload = self._normalize_event_payload(
            payload_kind=str(payload.get("payload_kind") or ""),
            payload=payload.get("payload") if "payload" in payload else payload,
        )
        run = self.load_run(run_id)
        root_task_id = run.task_id if run else task_id
        resolved_session_id = (run.session_id if run else "") or session_id
        normalized_payload = self._normalize_payload_identity(
            normalized_payload,
            root_task_id=root_task_id,
            session_id=resolved_session_id,
        )
        with self._event_write_lock(run_id):
            event = EventRecord(
                run_id=run_id,
                sequence=self._next_event_sequence(run_id),
                event_type=event_type,
                task_id=root_task_id,
                session_id=resolved_session_id,
                phase=str(payload.get("phase") or normalized_payload.get("phase") or ""),
                summary=self._default_summary(event_type, normalized_payload),
                payload_kind=normalized_payload_kind,
                payload=normalized_payload,
            )
            self._append_jsonl(self._events_file(run_id), event.model_dump(mode="python"))
        run = self.load_run(run_id)
        if run:
            self._apply_run_update_from_event(run, event)
        self._refresh_summary(run_id)

    def _apply_run_update_from_event(self, run: RunRecord, event: EventRecord) -> None:
        event_type = event.event_type
        payload = event.payload
        updates: dict[str, Any] = {}
        if event.session_id and not run.session_id:
            updates["session_id"] = event.session_id
        if event_type == "task_received":
            input_summary = self._metadata_input_summary(payload)
            updates["source"] = str(input_summary.get("source") or run.source)
            updates["raw_input"] = str(input_summary.get("raw_input") or run.raw_input)
        elif event_type == "task_card_ready":
            output_summary = self._metadata_output_summary(payload)
            task_card = output_summary.get("task_card") or {}
            if isinstance(task_card, dict):
                updates["goal"] = str(task_card.get("goal") or run.goal)
        elif event_type == "route_selected":
            updates["route"] = str(payload.get("route") or run.route)
        elif event_type == "task_finished":
            success = payload.get("success")
            output_summary = self._metadata_output_summary(payload)
            summary = output_summary.get("summary") if isinstance(output_summary, dict) else {}
            completion_reason = summary.get("completion_reason") if isinstance(summary, dict) else ""
            updates["finished_at"] = event.created_at
            updates["success"] = bool(success)
            updates["final_status"] = "success" if bool(success) else "failed"
            updates["completion_reason"] = str(completion_reason or ("task finished" if bool(success) else "task failed"))
        if updates:
            self.update_run(run.run_id, **updates)
        self._refresh_summary(run.run_id)

    def _resolve_run_id(self, run_key: str) -> str | None:
        key = str(run_key or "").strip()
        if not key:
            return None
        if key in self._run_ids_by_key:
            return self._run_ids_by_key[key]
        direct_path = self._run_file(key)
        if direct_path.exists():
            return key
        return None

    def _register_run_alias(self, run_id: str, key: str) -> None:
        key = str(key or "").strip()
        if key:
            self._run_ids_by_key[key] = run_id

    def _next_event_sequence(self, run_id: str) -> int:
        current = self._event_sequences.get(run_id, 0)
        events_path = self._events_file(run_id)
        if events_path.exists():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    current = max(current, int(json.loads(line).get("sequence") or 0))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
        self._event_sequences[run_id] = current
        current = self._event_sequences[run_id] + 1
        self._event_sequences[run_id] = current
        return current

    @contextmanager
    def _event_write_lock(self, run_id: str):
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        with (run_dir / ".events.lock").open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _run_dir(self, run_id: str) -> Path:
        return self.trajectory_dir / run_id

    def _run_file(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "run.json"

    def _events_file(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "events.jsonl"

    def _artifacts_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "artifacts"

    def _artifacts_index_file(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "artifacts.jsonl"

    def _summary_file(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "summary.json"

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")

    def _default_summary(self, event_type: str, payload: dict[str, Any]) -> str:
        if event_type == "route_selected":
            return f"route={payload.get('route') or ''}"
        if event_type == "llm_requested":
            return f"purpose={payload.get('purpose') or ''}"
        if event_type == "llm_responded":
            return f"model={payload.get('model') or ''}"
        if event_type == "llm_failed":
            return str(payload.get("error_type") or "llm_failed")
        if event_type == "task_finished":
            return f"success={payload.get('success')}"
        if event_type == "task_received":
            return str(self._metadata_input_summary(payload).get("raw_input") or payload.get("raw_input") or "")[:120]
        return event_type

    def _normalize_event_payload(self, *, payload_kind: str, payload: Any) -> tuple[str, dict[str, Any]]:
        requested_kind = self._normalize_kind_value(payload_kind)
        if payload is None:
            return requested_kind or "none", {}
        if hasattr(payload, "to_json_dict"):
            json_payload = json_safe(payload.to_json_dict())
            if not isinstance(json_payload, dict):
                json_payload = {"value": json_payload}
            if "kind" in json_payload:
                json_payload["kind"] = self._normalize_kind_value(json_payload.get("kind"))
            detected_kind = self._normalize_kind_value(json_payload.get("kind")) or requested_kind or type(payload).__name__
            return detected_kind, json_payload
        if isinstance(payload, dict):
            normalized_payload = json_safe(dict(payload))
            if not isinstance(normalized_payload, dict):
                normalized_payload = {"value": normalized_payload}
            if "kind" in normalized_payload:
                normalized_payload["kind"] = self._normalize_kind_value(normalized_payload.get("kind"))
            detected_kind = self._normalize_kind_value(normalized_payload.get("kind")) or requested_kind or "dict"
            return detected_kind, normalized_payload
        return requested_kind or type(payload).__name__, {"value": json_safe(payload)}

    def _normalize_kind_value(self, value: Any) -> str:
        if value is None:
            return ""
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, str) and enum_value:
            return enum_value
        text = str(value).strip()
        if text.startswith("MetadataKind."):
            _, _, suffix = text.partition(".")
            return suffix.lower()
        return text

    def _payload_task_id(self, payload: dict[str, Any]) -> str:
        correlation = payload.get("correlation")
        if isinstance(correlation, dict):
            return str(correlation.get("task_id") or payload.get("task_id") or "")
        return str(payload.get("task_id") or "")

    def _payload_session_id(self, payload: dict[str, Any]) -> str:
        correlation = payload.get("correlation")
        if isinstance(correlation, dict):
            return str(correlation.get("session_id") or payload.get("session_id") or "")
        return str(payload.get("session_id") or "")

    def _payload_phase(self, payload: dict[str, Any]) -> str:
        return str(payload.get("phase") or "")

    def _with_payload_correlation(self, payload: dict[str, Any], *, task_id: str = "", session_id: str = "") -> dict[str, Any]:
        correlation = payload.get("correlation")
        correlation_dict = dict(correlation) if isinstance(correlation, dict) else {}
        if task_id and not correlation_dict.get("task_id"):
            correlation_dict["task_id"] = task_id
        if session_id and not correlation_dict.get("session_id"):
            correlation_dict["session_id"] = session_id
        if not correlation_dict:
            return payload
        enriched = dict(payload)
        enriched["correlation"] = correlation_dict
        return enriched

    def _normalize_payload_identity(
        self,
        payload: dict[str, Any],
        *,
        root_task_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        enriched = self._with_payload_correlation(payload, task_id=root_task_id, session_id=session_id)
        correlation = enriched.get("correlation")
        correlation_dict = dict(correlation) if isinstance(correlation, dict) else {}
        annotations = dict(enriched.get("annotations") or {})
        original_task_id = str(correlation_dict.get("task_id") or payload.get("task_id") or "")
        if root_task_id:
            if original_task_id and original_task_id != root_task_id:
                annotations.setdefault("subtask_id", original_task_id)
                annotations.setdefault("parent_task_id", root_task_id)
            correlation_dict["task_id"] = root_task_id
        if session_id:
            correlation_dict["session_id"] = session_id
        normalized = dict(enriched)
        if correlation_dict:
            normalized["correlation"] = correlation_dict
        if annotations:
            normalized["annotations"] = annotations
        return normalized

    def _payload_source(self, payload: dict[str, Any]) -> str:
        input_summary = self._metadata_input_summary(payload)
        return str(input_summary.get("source") or payload.get("source") or "")

    def _payload_raw_input(self, payload: dict[str, Any]) -> str:
        input_summary = self._metadata_input_summary(payload)
        return str(input_summary.get("raw_input") or payload.get("raw_input") or "")

    def _payload_goal(self, payload: dict[str, Any]) -> str:
        if payload.get("goal"):
            return str(payload.get("goal") or "")
        output_summary = self._metadata_output_summary(payload)
        task_card = output_summary.get("task_card") if isinstance(output_summary, dict) else {}
        if isinstance(task_card, dict):
            return str(task_card.get("goal") or "")
        return ""

    def _payload_route(self, payload: dict[str, Any]) -> str:
        return str(payload.get("route") or "")

    def _refresh_summary(self, run_id: str) -> None:
        run = self.load_run(run_id)
        if run is None:
            return
        events = self.load_trajectory_events(run_id, limit=0)
        artifacts = self._load_jsonl(self._artifacts_index_file(run_id))
        tool_called_count = sum(1 for item in events if item.get("event_type") == "tool_called")
        tool_succeeded_count = sum(1 for item in events if item.get("event_type") == "tool_succeeded")
        tool_failed_count = sum(1 for item in events if item.get("event_type") == "tool_failed")
        verification_state_changes = sum(1 for item in events if item.get("event_type") == "verification_state_changed")
        phase_changes = sum(1 for item in events if item.get("event_type") == "runtime_phase_changed")
        last_phase = ""
        verification_status = ""
        for item in reversed(events):
            payload = item.get("payload") or {}
            if not last_phase:
                last_phase = str(item.get("phase") or payload.get("phase") or "")
            if not verification_status:
                verification_status = str(
                    payload.get("verification_status")
                    or self._metadata_output_summary(payload).get("verification_status")
                    or ""
                )
            if last_phase and verification_status:
                break
        summary = RunSummaryRecord(
            run_id=run.run_id,
            task_id=run.task_id,
            session_id=run.session_id,
            source=run.source,
            route=run.route,
            goal=run.goal,
            raw_input_preview=(run.raw_input or "")[:200],
            started_at=run.started_at,
            finished_at=run.finished_at,
            success=run.success,
            final_status=run.final_status,
            completion_reason=run.completion_reason,
            event_count=len(events),
            tool_called_count=tool_called_count,
            tool_succeeded_count=tool_succeeded_count,
            tool_failed_count=tool_failed_count,
            verification_state_changes=verification_state_changes,
            phase_changes=phase_changes,
            artifact_count=len(artifacts),
            last_phase=last_phase,
            verification_status=verification_status,
        )
        self._write_json(self._summary_file(run_id), summary.model_dump(mode="python"))

    def _load_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
        return rows

    def _metadata_input_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = payload.get("input_summary")
        return value if isinstance(value, dict) else {}

    def _metadata_output_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = payload.get("output_summary")
        return value if isinstance(value, dict) else {}
