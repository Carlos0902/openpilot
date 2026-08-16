"""Shared helpers for emitting typed tool lifecycle events."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from metadata import (
    FailureMetadata,
    ToolCallMetadata,
    ToolContextMetadata,
    ToolErrorMetadata,
    ToolEventMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
)


class ToolEventEmitter:
    """Build tool context/events and forward them to optional UI hooks."""

    def __init__(
        self,
        runtime: Any,
        *,
        log_hook: Callable[..., None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.log_hook = log_hook
        self._event_index = 0

    def next_event_index(self) -> int:
        self._event_index += 1
        return self._event_index

    def build_context(
        self,
        *,
        task_id: str,
        session_id: str,
        step_id: str,
        call_id: str,
        tool_name: str,
        input_metadata: ToolInputMetadata,
    ) -> ToolContextMetadata:
        environment = self._environment_for_input(input_metadata)
        project_path = str(
            input_metadata.project_path
            or environment.get("project_path")
            or self._project_from_path(input_metadata.cwd or input_metadata.file_path or "")
            or ""
        )
        cwd = str(input_metadata.cwd or environment.get("command_cwd") or project_path or "")
        env = {str(key): str(value) for key, value in (input_metadata.env or {}).items()}
        if not env and isinstance(environment.get("command_env"), dict):
            env = {str(key): str(value) for key, value in environment["command_env"].items()}
        git_snapshot = getattr(self.runtime, "_last_git_snapshot", None)
        if not isinstance(git_snapshot, dict):
            git_snapshot = None
        safety_notes: list[str] = []
        if git_snapshot:
            commit_hash = git_snapshot.get("commit_hash") or ""
            safety_notes.append(f"git snapshot available{f': {commit_hash}' if commit_hash else ''}")
        permission_required = self._permission_required(tool_name, input_metadata)
        if permission_required:
            safety_notes.append("tool may require explicit permission")
        return ToolContextMetadata(
            session_id=session_id,
            task_id=task_id,
            step_id=step_id,
            call_id=call_id,
            project_path=project_path,
            cwd=cwd,
            env=env,
            python_command=str(environment.get("python_command") or ""),
            pip_command=str(environment.get("pip_command") or ""),
            git_snapshot=git_snapshot,
            permission_required=permission_required,
            safety_notes=safety_notes,
            attributes={"tool_name": tool_name},
        )

    def create_tool_call(
        self,
        *,
        session_id: str,
        task_id: str,
        step_id: str,
        call_id: str,
        tool_name: str,
        input_metadata: ToolInputMetadata,
        tool_context: ToolContextMetadata | None,
        status: str = "pending",
        reason: str = "",
        recoverable: bool = True,
        round_index: int = 1,
        provider_call_id: str | None = None,
        provider_executed: bool = False,
    ) -> ToolCallMetadata:
        return ToolCallMetadata(
            session_id=session_id,
            task_id=task_id,
            step_id=step_id,
            call_id=call_id,
            tool_name=tool_name,
            input_metadata=input_metadata,
            tool_context=tool_context,
            status=status,
            reason=reason,
            recoverable=recoverable,
            round_index=round_index,
            provider_call_id=provider_call_id,
            provider_executed=provider_executed,
            event_index=self.next_event_index(),
        )

    def emit(
        self,
        *,
        task_id: str,
        tool_call: ToolCallMetadata,
        event_type: str,
        status: str,
        input_metadata: ToolInputMetadata | None = None,
        output_metadata: ToolResultMetadata | None = None,
        tool_context: ToolContextMetadata | None = None,
        failure: FailureMetadata | None = None,
        tool_error: ToolErrorMetadata | None = None,
        recoverable: bool = True,
        round_index: int = 1,
        provider_executed: bool = False,
    ) -> ToolEventMetadata:
        event = ToolEventMetadata(
            session_id=tool_call.session_id,
            task_id=task_id,
            step_id=tool_call.step_id,
            call_id=tool_call.call_id,
            tool_name=tool_call.tool_name or "unknown",
            event_type=event_type,
            status=status,
            input_metadata=input_metadata,
            output_metadata=output_metadata,
            tool_context=tool_context,
            tool_call=tool_call,
            tool_error=tool_error,
            failure=failure,
            recoverable=recoverable,
            provider_executed=provider_executed,
            round_index=round_index,
            event_index=self.next_event_index(),
        )
        self.emit_ui(event)
        return event

    def emit_ui(self, event: ToolEventMetadata) -> None:
        ui = getattr(self.runtime, "enhanced_ui", None)
        hook = getattr(ui, "append_tool_event", None)
        if not callable(hook):
            return
        try:
            hook(event)
        except Exception as exc:
            self._log_ui_hook_failure(event, exc)

    def _log_ui_hook_failure(self, event: ToolEventMetadata, exc: Exception) -> None:
        output_summary = {
            "call_id": event.call_id,
            "tool": event.tool_name,
            "event_type": event.event_type,
        }
        if self.log_hook:
            self.log_hook(
                "tool_event_ui_hook_failed",
                output_summary=output_summary,
                success=False,
                error=str(exc),
            )
            return
        logger = getattr(self.runtime, "logger", None)
        if logger and hasattr(logger, "log_event"):
            logger.log_event(
                "tool_event_ui_hook_failed",
                output_summary,
                session_id=getattr(self.runtime, "session_id", None) or "unknown",
                turn_id=1,
            )

    def _environment_for_input(self, input_metadata: ToolInputMetadata) -> dict[str, Any]:
        environment_for_tool_input = getattr(self.runtime, "_environment_for_tool_input", None)
        if callable(environment_for_tool_input):
            environment = environment_for_tool_input(input_metadata)
            if isinstance(environment, dict):
                return environment
        environments = getattr(self.runtime, "_project_environments", {}) or {}
        if len(environments) == 1:
            return next(iter(environments.values()))
        return {}

    def _project_from_path(self, raw: str) -> str:
        if not raw:
            return ""
        environments = getattr(self.runtime, "_project_environments", {}) or {}
        try:
            path = Path(str(raw)).expanduser().resolve()
        except OSError:
            path = Path(str(raw)).expanduser()
        for project_path in environments:
            try:
                project = Path(str(project_path)).expanduser().resolve()
            except OSError:
                project = Path(str(project_path)).expanduser()
            if path == project or project in path.parents:
                return str(project_path)
        return ""

    def _permission_required(self, tool_name: str, input_metadata: ToolInputMetadata) -> bool:
        if input_metadata.requires_user_input:
            return True
        registry = getattr(self.runtime, "tool_registry", None)
        definition = registry.get(tool_name) if registry and hasattr(registry, "get") else None
        permission_level = str(getattr(definition, "permission_level", "") or "").lower()
        return permission_level in {"medium", "high", "forbidden"}
