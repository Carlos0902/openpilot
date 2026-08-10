"""Deterministic reducer for explicit in-session user constraints.

This module deliberately does not write memory or execute tools. It turns a
narrow allowlist of explicit user statements into source-linked proposals and
applies only an explicit confirmation transition to the runtime-owned ledger.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from metadata import (
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    SessionConstraintAuthority,
    SessionConstraintCategory,
    SessionConstraintEntry,
    SessionConstraintProposal,
    SessionConstraintProposalStatus,
    SessionConstraintSourceKind,
    SessionConstraintState,
    SessionConstraintStatus,
    SessionConstraintValue,
    SessionConstraintViolationCode,
)


_PATH_PATTERN = re.compile(r"(?<![\w.-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]+")
_BACKTICK_COMMAND_PATTERN = re.compile(r"`([^`\n]{1,240})`")
_ONLY_SCOPE_PATTERN = re.compile(
    r"(?:only|仅|只能)\s+(.{1,240}?)\s+(?:may\s+be\s+)?(?:modified|changed|written|edited|修改|编辑)",
    re.IGNORECASE,
)
_FORBIDDEN_SCOPE_PATTERN = re.compile(
    r"(?:do\s+not|don't|must\s+not|不得|不要|禁止)\s+(?:modify|change|touch|edit|修改|编辑)\s+(.{1,240})",
    re.IGNORECASE,
)
_VALIDATION_PATTERN = re.compile(
    r"(?:validation\s+command(?:\s+is|\s+must\s+be)?|the\s+validation\s+command\s+must\s+be|"
    r"must\s+(?:run|execute)|required\s+command|验证命令(?:是|为)?|必须运行)\s*[:：]?\s*",
    re.IGNORECASE,
)
_API_PATTERN = re.compile(
    r"(?:preserve|keep|do\s+not\s+change|must\s+not\s+change)\s+(?:the\s+)?(?:existing\s+public\s+|public\s+existing\s+|existing\s+|public\s+)?api"
    r"|(?:不要|不得|不能)\s*(?:改变|修改)\s*(?:现有|公共)?\s*api",
    re.IGNORECASE,
)
_ACCEPTANCE_PATTERN = re.compile(
    r"(?:acceptance\s+(?:criterion|criteria)|验收条件)\s*[:：]\s*([^\n]{1,360})",
    re.IGNORECASE,
)


def extract_constraint_proposals(
    messages: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
) -> list[SessionConstraintProposal]:
    """Extract explicit proposals from user messages only.

    Missing stable message IDs are ignored rather than assigned a synthetic
    authority. Assistant messages, summaries, and vague prose are ignored.
    ``session_id`` is accepted as an ownership guard for callers; it is not
    copied into each proposal because the surrounding runtime state owns it.
    """

    if not str(session_id).strip():
        raise ValueError("session_id is required")
    proposals: list[SessionConstraintProposal] = []
    for ordinal, raw_message in enumerate(messages, start=1):
        if not isinstance(raw_message, Mapping):
            continue
        if str(raw_message.get("role") or "").strip().lower() != "user":
            continue
        message_id = str(raw_message.get("message_id") or raw_message.get("id") or "").strip()
        content = str(raw_message.get("content") or "").strip()
        if not message_id or not content:
            continue
        turn_index = _turn_index(raw_message, ordinal)
        source_hash = _message_hash(message_id, turn_index, content)
        proposals.extend(
            _extract_message_proposals(
                session_id, message_id, turn_index, source_hash, content
            )
        )
    return proposals


def reject_constraint_proposal(
    state: SessionConstraintState,
    proposal: SessionConstraintProposal,
) -> SessionConstraintState:
    """Record no state change for a rejected proposal."""

    _ensure_same_session(state, proposal)
    return state


def confirm_constraint_proposal(
    proposal: SessionConstraintProposal,
) -> SessionConstraintProposal:
    """Mark a proposal confirmed without mutating runtime state."""

    if proposal.status != SessionConstraintProposalStatus.PROPOSED:
        raise ValueError("only proposed constraints may be confirmed")
    return proposal.model_copy(update={"status": SessionConstraintProposalStatus.CONFIRMED})


def activate_constraint_proposal(
    state: SessionConstraintState,
    proposal: SessionConstraintProposal,
    *,
    confirmation_turn: int,
) -> SessionConstraintState:
    """Explicitly project one proposal into the active session ledger."""

    _ensure_same_session(state, proposal)
    if proposal.status != SessionConstraintProposalStatus.CONFIRMED:
        raise ValueError("only confirmed constraints may be activated")
    if confirmation_turn < proposal.source_turn_index:
        raise ValueError("confirmation turn cannot precede source turn")

    previous = next(
        (entry for entry in state.entries if entry.constraint_key == proposal.constraint_key),
        None,
    )
    constraint_id = _constraint_id(proposal, confirmation_turn)
    entry = SessionConstraintEntry(
        constraint_id=constraint_id,
        constraint_key=proposal.constraint_key,
        category=proposal.category,
        value=proposal.value,
        statement=proposal.statement,
        source_kind=SessionConstraintSourceKind.USER_CONFIRMATION,
        source_id=proposal.source_id,
        source_turn_index=proposal.source_turn_index,
        source_hash=proposal.source_hash,
        authority=SessionConstraintAuthority.USER_CONFIRMED,
        supersedes_constraint_id=previous.constraint_id if previous else None,
        confirmed_at_turn=confirmation_turn,
    )
    entries = [item for item in state.entries if item.constraint_key != proposal.constraint_key]
    entries.append(entry)
    updated = state.model_copy(
        update={
            "revision": state.revision + 1,
            "processed_through_turn": max(state.processed_through_turn, confirmation_turn),
            "entries": entries,
        }
    )
    return SessionConstraintState.model_validate(updated.model_dump(mode="python"))


def revoke_constraint(
    state: SessionConstraintState,
    constraint_key: str,
    *,
    turn_index: int,
) -> SessionConstraintState:
    """Replace one active entry with its revoked tombstone."""

    current = next(
        (entry for entry in state.entries if entry.constraint_key == constraint_key),
        None,
    )
    if current is None:
        raise ValueError("unknown constraint key")
    if turn_index < current.source_turn_index:
        raise ValueError("revocation turn cannot precede source turn")
    if not current.is_active:
        return state
    revoked = current.model_copy(
        update={
            "status": SessionConstraintStatus.REVOKED,
            "revoked_at_turn": turn_index,
        }
    )
    entries = [item for item in state.entries if item.constraint_key != constraint_key]
    entries.append(revoked)
    updated = state.model_copy(
        update={
            "revision": state.revision + 1,
            "processed_through_turn": max(state.processed_through_turn, turn_index),
            "entries": entries,
        }
    )
    return SessionConstraintState.model_validate(updated.model_dump(mode="python"))


def build_session_constraint_candidate(
    state: SessionConstraintState,
) -> ContextCandidate | None:
    """Render active constraints as one bounded, source-linked candidate."""

    active = state.active_entries
    if not active:
        return None
    state_hash = state.authority_hash
    projection = session_constraint_projection(state)
    content = "Active Session Constraints:\n" + json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ContextCandidate(
        candidate_id=f"session_constraints:{state_hash.removeprefix('sha256:')[:24]}",
        kind=ContextCandidateKind.CONSTRAINT,
        source_id=state_hash,
        content=content,
        role="user",
        retention=ContextCandidateRetention.REQUIRED,
        priority=100,
        source_order=50,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.AUTHORITATIVE,
        freshness=ContextCandidateFreshness.CURRENT,
        conflict_key="session_constraints",
    )


def session_constraint_projection(state: SessionConstraintState) -> dict[str, Any]:
    """Return the bounded source-linked projection used by compatibility views."""

    active = state.active_entries
    # The model-facing projection must remain stable while ordinary ingress
    # turns advance the full snapshot cursor.  ``canonical_hash`` remains the
    # complete checkpoint/replay snapshot identity; ``authority_hash`` is the
    # bounded active-constraint source identity used here.
    state_hash = state.authority_hash
    return {
        "session_id": state.session_id,
        "revision": state.revision,
        "state_hash": state_hash,
        "constraints": [
            {
                "constraint_id": entry.constraint_id,
                "constraint_key": entry.constraint_key,
                "category": entry.category,
                "value": entry.value.model_dump(mode="json"),
                "source_id": entry.source_id,
                "source_hash": entry.source_hash,
                "authority": entry.authority,
                "confirmed_at_turn": entry.confirmed_at_turn,
                "supersedes_constraint_id": entry.supersedes_constraint_id,
            }
            for entry in active
        ],
    }


def session_constraint_prompt_text(state: SessionConstraintState) -> str:
    """Render the bounded active-constraint view for non-memory prompts.

    Planner/decomposer prompts do not all pass through ``MemoryContextBuilder``.
    They still need the same source-linked, active-only view, without leaking
    the Pydantic repr or the surrounding ingress turn ledger into the prompt.
    """

    if not state.active_entries:
        return ""
    return "Active Session Constraints:\n" + json.dumps(
        session_constraint_projection(state),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def session_constraint_violation(
    state: SessionConstraintState,
    *,
    target_files: Sequence[str],
    command: str | None,
    task_validation_command: str | None = None,
    command_is_mutation: bool = False,
) -> SessionConstraintViolationCode | None:
    """Return a typed final-gate denial, or ``None`` when no active constraint is violated."""

    targets = _normalise_paths(target_files, state.project_root)
    for entry in state.active_entries:
        value = entry.value
        if entry.category == SessionConstraintCategory.EXECUTION_MODE:
            if value.execution_mode == "read_only" and (targets or command_is_mutation):
                return SessionConstraintViolationCode.READ_ONLY
        elif entry.category == SessionConstraintCategory.WRITE_SCOPE and targets:
            forbidden = _normalise_paths(value.forbidden_files, state.project_root)
            allowed = _normalise_paths(value.allowed_files, state.project_root)
            if forbidden.intersection(targets) or (allowed and not targets.issubset(allowed)):
                return SessionConstraintViolationCode.WRITE_SCOPE
        elif (
            entry.category == SessionConstraintCategory.VALIDATION_COMMAND
            and (task_validation_command is not None or command)
        ):
            required = {_command_signature(item) for item in value.validation_commands}
            if task_validation_command is not None and (
                not task_validation_command
                or _command_signature(task_validation_command) not in required
            ):
                return SessionConstraintViolationCode.VALIDATION_COMMAND
            if (
                command
                and (_looks_like_validation_command(command) or task_validation_command is not None)
                and _command_signature(command) not in required
            ):
                return SessionConstraintViolationCode.VALIDATION_COMMAND
    return None


def _extract_message_proposals(
    session_id: str,
    message_id: str,
    turn_index: int,
    source_hash: str,
    content: str,
) -> list[SessionConstraintProposal]:
    proposals: list[SessionConstraintProposal] = []
    allowed: list[str] = []
    forbidden: list[str] = []
    for match in _ONLY_SCOPE_PATTERN.finditer(content):
        allowed.extend(_paths(match.group(1)))
    for match in _FORBIDDEN_SCOPE_PATTERN.finditer(content):
        forbidden.extend(_paths(match.group(1)))
    if allowed or forbidden:
        value = SessionConstraintValue(
            allowed_files=_unique(allowed),
            forbidden_files=_unique(forbidden),
        )
        proposals.append(
            _proposal(
                session_id,
                message_id,
                turn_index,
                source_hash,
                "write_scope",
                SessionConstraintCategory.WRITE_SCOPE,
                value,
                "Explicit user write-file scope.",
            )
        )

    commands = _validation_commands(content)
    if commands:
        proposals.append(
            _proposal(
                session_id,
                message_id,
                turn_index,
                source_hash,
                "validation_command",
                SessionConstraintCategory.VALIDATION_COMMAND,
                SessionConstraintValue(validation_commands=commands),
                "Explicit user validation command.",
            )
        )

    if _API_PATTERN.search(content):
        proposals.append(
            _proposal(
                session_id,
                message_id,
                turn_index,
                source_hash,
                "api_compatibility",
                SessionConstraintCategory.API_COMPATIBILITY,
                SessionConstraintValue(api_compatibility="preserve_existing_api"),
                "Preserve the existing public API.",
            )
        )

    for match in _ACCEPTANCE_PATTERN.finditer(content):
        criterion = match.group(1).strip().rstrip(".")
        if criterion:
            proposals.append(
                _proposal(
                    session_id,
                    message_id,
                    turn_index,
                    source_hash,
                    "goal_acceptance",
                    SessionConstraintCategory.GOAL_ACCEPTANCE,
                    SessionConstraintValue(acceptance_criteria=[criterion]),
                    "Explicit user acceptance criterion.",
                )
            )
    return proposals


def _proposal(
    session_id: str,
    message_id: str,
    turn_index: int,
    source_hash: str,
    key: str,
    category: SessionConstraintCategory,
    value: SessionConstraintValue,
    statement: str,
) -> SessionConstraintProposal:
    return SessionConstraintProposal(
        proposal_id=f"proposal:{message_id}:{key}:{turn_index}",
        session_id=session_id,
        constraint_key=key,
        category=category,
        value=value,
        statement=statement,
        source_kind=SessionConstraintSourceKind.USER_MESSAGE,
        source_id=message_id,
        source_turn_index=turn_index,
        source_hash=source_hash,
    )


def _validation_commands(content: str) -> list[str]:
    commands: list[str] = []
    for match in _VALIDATION_PATTERN.finditer(content):
        tail = content[match.end() : match.end() + 300]
        quoted = _BACKTICK_COMMAND_PATTERN.search(tail)
        if quoted:
            command = quoted.group(1).strip()
        else:
            command = tail.split(".", 1)[0].split("\n", 1)[0].strip(" `:：")
        if command and ("pytest" in command or "test" in command or "compile" in command):
            commands.append(command[:240])
    return _unique(commands)


def _paths(fragment: str) -> list[str]:
    return [match.group(0) for match in _PATH_PATTERN.finditer(fragment)]


def _turn_index(message: Mapping[str, Any], ordinal: int) -> int:
    for key in ("turn_index", "turn_id", "index"):
        raw = message.get(key)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value >= 1:
            return value
    return ordinal


def _message_hash(message_id: str, turn_index: int, content: str) -> str:
    payload = {"message_id": message_id, "turn_index": turn_index, "content": content}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _constraint_id(proposal: SessionConstraintProposal, confirmation_turn: int) -> str:
    payload = {
        "proposal_id": proposal.proposal_id,
        "source_hash": proposal.source_hash,
        "confirmation_turn": confirmation_turn,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "constraint:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _unique(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _normalise_paths(values: Sequence[str], project_root: str) -> set[str]:
    root = Path(project_root).expanduser().resolve(strict=False) if project_root else None
    normalized: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        if not value:
            continue
        path = Path(value).expanduser()
        if root is not None:
            path = path if path.is_absolute() else root / path
            try:
                path = path.resolve(strict=False).relative_to(root)
            except ValueError:
                path = path.resolve(strict=False)
        normalized.add(path.as_posix())
    return normalized


def _command_signature(command: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(str(command).strip()))
    except ValueError:
        return (str(command).strip(),)


def _looks_like_validation_command(command: str) -> bool:
    lowered = str(command).lower()
    return any(token in lowered for token in ("pytest", "unittest", " test", "compileall", "ruff", "mypy"))


def _ensure_same_session(
    state: SessionConstraintState,
    proposal: SessionConstraintProposal,
) -> None:
    if not state.session_id.strip():
        raise ValueError("session state requires session_id")
    if proposal.session_id != state.session_id:
        raise ValueError("session constraint proposal belongs to a different session")
