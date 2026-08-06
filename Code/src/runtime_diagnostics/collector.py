"""Collectors that convert runtime evidence into ProblemSignalMetadata."""

from __future__ import annotations

from typing import Any

from metadata import FailureMetadata, ProblemSignalMetadata, RuntimeStateMetadata, ToolErrorMetadata


ENVIRONMENT_ERROR_HINTS = (
    "modulenotfounderror",
    "importerror",
    "filenotfounderror",
    "permissionerror",
    "environment",
    "dependency",
    "no module named",
)


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _category_from_error(error_type: str, message: str, default: str = "tool_execution") -> str:
    text = f"{error_type}\n{message}".lower()
    if any(hint in text for hint in ENVIRONMENT_ERROR_HINTS):
        return "environment"
    return default


def collect_from_tool_error(error: ToolErrorMetadata | dict[str, Any]) -> ProblemSignalMetadata:
    """Create a problem signal from a tool error metadata object or mapping."""
    error_type = str(_value(error, "error_type", "ToolError"))
    error_message = str(_value(error, "error_message", ""))
    tool_name = str(_value(error, "tool_name", ""))
    task_id = str(_value(error, "task_id", ""))
    suggested_recovery = str(_value(error, "suggested_recovery", "") or "")
    category = _category_from_error(error_type, error_message)
    evidence = [item for item in [error_type, error_message, suggested_recovery] if item]

    raw_payload = error.to_json_dict() if hasattr(error, "to_json_dict") else dict(error)
    return ProblemSignalMetadata(
        signal_source="tool_error",
        category=category,
        message=error_message or error_type,
        evidence=evidence,
        task_id=task_id,
        tool_name=tool_name,
        raw_payload=raw_payload,
    )


def collect_from_failure(
    failure: FailureMetadata | dict[str, Any],
    *,
    source: str = "runtime_failure",
    task_id: str = "",
    tool_name: str = "",
) -> ProblemSignalMetadata:
    """Create a problem signal from FailureMetadata or a failure-like mapping."""
    error_type = str(_value(failure, "error_type", "Failure"))
    error_message = str(_value(failure, "error_message", ""))
    recovery_strategy = str(_value(failure, "recovery_strategy", "") or "")
    details = _value(failure, "details", {}) or {}
    category = _category_from_error(error_type, error_message)
    evidence = [item for item in [error_type, error_message, recovery_strategy] if item]
    raw_payload = failure.to_json_dict() if hasattr(failure, "to_json_dict") else dict(failure)

    return ProblemSignalMetadata(
        signal_source=source,
        category=category,
        message=error_message or error_type,
        evidence=evidence,
        task_id=task_id,
        tool_name=tool_name,
        raw_payload={**raw_payload, "details": details},
    )


def collect_from_runtime_state(state: RuntimeStateMetadata | dict[str, Any]) -> list[ProblemSignalMetadata]:
    """Extract obvious problem signals from runtime state snapshots."""
    signals: list[ProblemSignalMetadata] = []
    goal = str(_value(state, "goal", ""))
    phase = str(_value(state, "phase", ""))
    verification_status = str(_value(state, "verification_status", ""))
    unknowns = list(_value(state, "unknowns", []) or [])
    assumptions = list(_value(state, "assumptions", []) or [])
    path_resolutions = list(_value(state, "path_resolutions", []) or [])
    no_progress_rounds = int(_value(state, "no_progress_rounds", 0) or 0)
    completion_reason = _value(state, "completion_reason", None)

    if phase.endswith("blocked") or phase == "AgentPhase.BLOCKED":
        signals.append(
            ProblemSignalMetadata(
                signal_source="runtime_state",
                category="state_transition",
                message="Runtime entered blocked phase",
                evidence=[f"phase={phase}", f"completion_reason={completion_reason}"],
                raw_payload={"goal": goal, "phase": phase, "completion_reason": completion_reason},
            )
        )

    if verification_status in {"failed", "fail", "error"}:
        signals.append(
            ProblemSignalMetadata(
                signal_source="runtime_state",
                category="verification",
                message="Runtime verification did not pass",
                evidence=[f"verification_status={verification_status}"],
                raw_payload={"goal": goal, "verification_status": verification_status},
            )
        )

    if no_progress_rounds >= 2:
        signals.append(
            ProblemSignalMetadata(
                signal_source="runtime_state",
                category="planning",
                message="Runtime made no progress for multiple rounds",
                evidence=[f"no_progress_rounds={no_progress_rounds}"],
                raw_payload={"goal": goal, "unknowns": unknowns, "assumptions": assumptions},
            )
        )

    for resolution in path_resolutions:
        status = str(_value(resolution, "status", "") or "")
        if status not in {"blocked", "ambiguous"}:
            continue
        reason = str(_value(resolution, "reason", "") or "")
        raw_path = str(_value(resolution, "raw_path", "") or "")
        candidate_paths = list(_value(resolution, "candidate_paths", []) or [])
        signals.append(
            ProblemSignalMetadata(
                signal_source="runtime_state",
                category="path_resolution",
                message=f"Path grounding {status}",
                evidence=[
                    item
                    for item in [
                        f"status={status}",
                        f"raw_path={raw_path}",
                        reason,
                        f"candidate_count={len(candidate_paths)}" if candidate_paths else "",
                    ]
                    if item
                ],
                raw_payload=resolution.to_json_dict() if hasattr(resolution, "to_json_dict") else dict(resolution),
            )
        )

    return signals


def suspicious_success_signal(
    *,
    task_id: str = "",
    message: str = "Task reported success without enough verification evidence",
    evidence: list[str] | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> ProblemSignalMetadata:
    """Create a suspicious-success signal for final-result checks."""
    return ProblemSignalMetadata(
        signal_source="final_result",
        category="suspicious_success",
        message=message,
        evidence=evidence or [],
        task_id=task_id,
        raw_payload=raw_payload or {},
    )
