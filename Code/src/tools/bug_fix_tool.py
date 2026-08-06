"""Bug Fix Tool - repair runtime/program execution failures."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from core.command_approval import CommandApprovalGate
from core.llm import LLMClient, LLMMessage, LLMRequest
from memory.context_assembly import build_context_llm_request
from core.tool_contracts import PermissionLevel, ToolCapability, ToolDefinition, ToolFailureMode
from metadata import (
    BugFixAttemptMetadata,
    BugFixResultMetadata,
    CommandArtifactMetadata,
    ContextCandidateTruncation,
    ContextRequestPurpose,
    FailureMetadata,
    ResultStatus,
    ToolContractMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
    WarningCheckResultMetadata,
)
from memory.agents.git_manager_agent import GitManagerAgent, GitManagerError
from tools.command_tool import command_executor
from tools.file_reader import file_reader_executor
from tools.file_writer import file_writer_executor
from tools.terminal_smoke import looks_like_terminal_python_files, run_terminal_command


BUG_FIX_TOOL_DEFINITION = ToolDefinition(
    name="bug_fix_tool",
    display_name="Bug Fix Tool",
    description=(
        "Fix program execution bugs until the user's command runs successfully. "
        "This tool only handles runtime, syntax, import, and command failure bugs; "
        "it does not judge semantic quality or user satisfaction."
    ),
    version="1.0.0",
    capabilities=[ToolCapability.FILE_READ, ToolCapability.FILE_WRITE, ToolCapability.SHELL_EXECUTION, ToolCapability.LLM_CALL],
    permission_level=PermissionLevel.HIGH,
    contract_metadata=ToolContractMetadata(
        tool_name="bug_fix_tool",
        input_metadata_type="ToolInputMetadata",
        output_metadata_type="ToolResultMetadata",
        required_input_fields=["command", "file_paths"],
        input_defaults={
            "timeout": 30,
            "max_iterations": 5,
            "continuation_iterations": 3,
            "cwd": None,
            "fix_instruction": None,
            "warning_check_required": False,
            "warning_check_result": None,
        },
    ),
    timeout_seconds=900,
    max_retries=0,
    failure_modes=[
        ToolFailureMode(
            error_type="invalid_input",
            description="Command or explicit target files are missing",
            recovery_strategy="Provide command and file_paths metadata.",
        ),
        ToolFailureMode(
            error_type="invalid_fix_payload",
            description="The LLM returned a patch outside the allowed target files",
            recovery_strategy="Retry with a fix constrained to the declared file_paths.",
        ),
        ToolFailureMode(
            error_type="max_iterations_reached",
            description="The command still fails after the configured bug-fix iterations",
            recovery_strategy="Ask the user whether to continue with more iterations.",
        ),
    ],
    tags=["bug", "fix", "runtime", "execution", "repair"],
    audit_required=True,
)


def bug_fix_tool_executor(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
    """Fix runtime/program bugs and verify by running the requested command."""
    params = input_metadata.to_params()
    command = str(params.get("command") or "").strip()
    if not command:
        raise ValueError("Invalid input: command is required")
    if not input_metadata.file_paths:
        raise ValueError("Invalid input: file_paths is required and must contain at least one target file")

    cwd = str(params.get("cwd") or "").strip()
    env = {str(key): str(value) for key, value in params.get("env", {}).items()} if isinstance(params.get("env"), dict) else None
    timeout = int(params.get("timeout") or 30)
    max_iterations = _positive_int(params.get("max_iterations"), default=5)
    continuation_iterations = _positive_int(params.get("continuation_iterations"), default=3)
    fix_instruction = str(params.get("fix_instruction") or "").strip()
    warning_check_required = bool(params.get("warning_check_required"))
    warning_check_context = _warning_check_context(input_metadata)
    if warning_check_context and warning_check_context.recommended_fix:
        fix_instruction = (fix_instruction + "\n" if fix_instruction else "") + (
            "Runtime warning repair requirement: "
            f"{warning_check_context.reason}. {warning_check_context.recommended_fix}"
        )
    ask_user_to_continue = params.get("_ask_user_to_continue")
    command_approval_callback = params.get("_command_approval_callback")
    progress_callback = params.get("_bug_fix_progress_callback")

    allowed_files = _resolve_allowed_files(input_metadata.file_paths, cwd)
    use_terminal_smoke = _should_use_terminal_smoke(allowed_files)
    attempts: list[BugFixAttemptMetadata] = []

    initial_command = _run_command(
        command,
        cwd,
        timeout,
        env,
        terminal_smoke=use_terminal_smoke,
        command_approval_callback=command_approval_callback,
    )
    attempts.append(_attempt(iteration=0, command_result=initial_command, modified_files=[], rationale="initial validation"))
    initial_warning_check = _check_command_warnings(
        command_result=initial_command,
        command=command,
        cwd=cwd,
        warning_check_required=warning_check_required,
    )
    last_warning_check = initial_warning_check
    _emit_progress(
        progress_callback,
        event="initial_validation",
        iteration=0,
        budget=max_iterations,
        success=initial_command.success and not _warning_requires_fix(initial_warning_check),
        error_summary=_command_error_summary(initial_command),
    )
    if initial_command.success and not _warning_requires_fix(initial_warning_check):
        result = _bug_fix_result(
            command=command,
            cwd=cwd,
            allowed_files=allowed_files,
            fixed=False,
            iterations_used=0,
            max_iterations=max_iterations,
            continuation_iterations=continuation_iterations,
            attempts=attempts,
            final_command_result=initial_command,
        )
        return ToolResultMetadata(tool_name="bug_fix_tool", status=ResultStatus.SUCCESS, result=result)

    llm_client = params.get("_llm_client") or LLMClient()
    budget = max_iterations
    iteration = 0
    last_command_result = initial_command

    while True:
        while iteration < budget:
            iteration += 1
            _emit_progress(
                progress_callback,
                event="iteration_started",
                iteration=iteration,
                budget=budget,
                success=False,
                error_summary=_command_error_summary(last_command_result),
            )
            file_contents = _read_allowed_files(allowed_files)
            fix_payload = _request_fix(
                llm_client=llm_client,
                command=command,
                cwd=cwd,
                target_files=list(allowed_files),
                file_contents=file_contents,
                command_result=last_command_result,
                attempts=attempts,
                iteration=iteration,
                fix_instruction=fix_instruction,
                terminal_smoke=use_terminal_smoke,
            )
            try:
                changes, rationale = _validate_fix_payload(fix_payload, allowed_files)
            except ValueError as exc:
                attempts.append(
                    BugFixAttemptMetadata(
                        iteration=iteration,
                        command_result=last_command_result,
                        error_summary=str(exc),
                        modified_files=[],
                        rationale="invalid fix payload",
                        llm_payload=fix_payload if isinstance(fix_payload, dict) else {"raw": str(fix_payload)},
                    )
                )
                _emit_progress(
                    progress_callback,
                    event="iteration_completed",
                    iteration=iteration,
                    budget=budget,
                    success=False,
                    error_summary=str(exc),
                )
                result = _bug_fix_result(
                    command=command,
                    cwd=cwd,
                    allowed_files=allowed_files,
                    fixed=False,
                    iterations_used=iteration,
                    max_iterations=max_iterations,
                    continuation_iterations=continuation_iterations,
                    attempts=attempts,
                    final_command_result=last_command_result,
                )
                return ToolResultMetadata(
                    tool_name="bug_fix_tool",
                    status=ResultStatus.FAIL,
                    result=result,
                    failure=FailureMetadata(
                        error_type="InvalidBugFixPayload",
                        error_message=str(exc),
                        recoverable=True,
                        retry_recommended=True,
                    ),
                )

            modified_files = _write_changes(changes)
            last_command_result = _run_command(
                command,
                cwd,
                timeout,
                env,
                terminal_smoke=use_terminal_smoke,
                command_approval_callback=command_approval_callback,
            )
            latest_warning_check = _check_command_warnings(
                command_result=last_command_result,
                command=command,
                cwd=cwd,
                warning_check_required=warning_check_required,
            )
            last_warning_check = latest_warning_check
            attempts.append(
                _attempt(
                    iteration=iteration,
                    command_result=last_command_result,
                    modified_files=modified_files,
                    rationale=rationale,
                    llm_payload=fix_payload,
                )
            )
            _emit_progress(
                progress_callback,
                event="iteration_completed",
                iteration=iteration,
                budget=budget,
                success=last_command_result.success and not _warning_requires_fix(latest_warning_check),
                modified_files=modified_files,
                error_summary=_command_error_summary(last_command_result),
            )
            if last_command_result.success and not _warning_requires_fix(latest_warning_check):
                result = _bug_fix_result(
                    command=command,
                    cwd=cwd,
                    allowed_files=allowed_files,
                    fixed=True,
                    iterations_used=iteration,
                    max_iterations=max_iterations,
                    continuation_iterations=continuation_iterations,
                    attempts=attempts,
                    final_command_result=last_command_result,
                )
                return ToolResultMetadata(tool_name="bug_fix_tool", status=ResultStatus.SUCCESS, result=result)

        result = _bug_fix_result(
            command=command,
            cwd=cwd,
            allowed_files=allowed_files,
            fixed=False,
            iterations_used=iteration,
            max_iterations=max_iterations,
            continuation_iterations=continuation_iterations,
            attempts=attempts,
            final_command_result=last_command_result,
            requires_user_decision=True,
        )
        if callable(ask_user_to_continue):
            _emit_progress(
                progress_callback,
                event="continuation_requested",
                iteration=iteration,
                budget=budget,
                success=False,
                error_summary=_command_error_summary(last_command_result),
            )
            if _should_continue(ask_user_to_continue, result):
                result.requires_user_decision = False
                budget += continuation_iterations
                _emit_progress(
                    progress_callback,
                    event="continuation_approved",
                    iteration=iteration,
                    budget=budget,
                    success=False,
                )
                continue
            result.requires_user_decision = False
            result.user_terminated = True
            _emit_progress(
                progress_callback,
                event="terminated",
                iteration=iteration,
                budget=budget,
                success=False,
            )
            return ToolResultMetadata(
                tool_name="bug_fix_tool",
                status=ResultStatus.FAIL,
                result=result,
                failure=FailureMetadata(
                    error_type="BugFixTerminatedByUser",
                    error_message="Bug fix stopped after reaching the iteration limit and user declined to continue.",
                    recoverable=True,
                    retry_recommended=False,
                ),
            )

        _emit_progress(
            progress_callback,
            event="iteration_limit_reached",
            iteration=iteration,
            budget=budget,
            success=False,
            error_summary=_command_error_summary(last_command_result),
        )
        return ToolResultMetadata(
            tool_name="bug_fix_tool",
            status=ResultStatus.FAIL,
            result=result,
            failure=FailureMetadata(
                error_type="MaxBugFixIterationsReached",
                error_message=(
                    f"Runtime warning still requires repair after {iteration} bug-fix iteration(s): {last_warning_check.reason}"
                    if _warning_requires_fix(last_warning_check)
                    else f"Command still fails after {iteration} bug-fix iteration(s)."
                ),
                recoverable=True,
                retry_recommended=True,
                recovery_strategy="Ask the user whether to continue with more bug-fix iterations.",
            ),
        )


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _emit_progress(callback: Any, **event: Any) -> None:
    if not callable(callback):
        return
    try:
        callback(event)
    except Exception:
        return


def _resolve_allowed_files(file_paths: list[str], cwd: str) -> dict[str, Path]:
    base = Path(cwd).expanduser() if cwd else Path.cwd()
    allowed: dict[str, Path] = {}
    for raw_path in file_paths:
        path = Path(str(raw_path)).expanduser()
        if not path.is_absolute():
            path = base / path
        resolved = path.resolve()
        allowed[str(raw_path)] = resolved
        allowed[str(resolved)] = resolved
    return allowed


def _should_use_terminal_smoke(allowed_files: dict[str, Path]) -> bool:
    return looks_like_terminal_python_files(sorted({str(path) for path in allowed_files.values()}))


def _run_command(
    command: str,
    cwd: str,
    timeout: int,
    env: dict[str, str] | None = None,
    *,
    terminal_smoke: bool = False,
    command_approval_callback=None,
) -> CommandArtifactMetadata:
    if terminal_smoke:
        CommandApprovalGate().approve(command, cwd=cwd, approval_callback=command_approval_callback)
        smoke = run_terminal_command(
            command,
            cwd=cwd or None,
            env=env,
            timeout=max(float(timeout), 2.0),
            shell=True,
        )
        artifact = smoke.to_command_artifact()
        if smoke.skipped:
            artifact.success = False
            artifact.exit_code = -1
            artifact.stderr = smoke.skip_reason or "Terminal smoke verification skipped."
        return artifact
    result = command_executor(
        ToolInputMetadata.from_mapping(
            "command_executor",
            {
                "command": command,
                "mode": "automatic",
                "timeout": timeout,
                "cwd": cwd or None,
                "env": env,
                "_command_approval_callback": command_approval_callback,
            },
        )
    )
    if not isinstance(result.result, CommandArtifactMetadata):
        raise TypeError(f"command_executor returned {type(result.result).__name__}, expected CommandArtifactMetadata")
    return result.result


def _warning_check_context(input_metadata: ToolInputMetadata) -> WarningCheckResultMetadata | None:
    raw = input_metadata.warning_check_result
    if isinstance(raw, WarningCheckResultMetadata):
        return raw
    if isinstance(raw, dict):
        try:
            return WarningCheckResultMetadata.model_validate(raw)
        except Exception:
            return None
    return None


def _check_command_warnings(
    *,
    command_result: CommandArtifactMetadata,
    command: str,
    cwd: str,
    warning_check_required: bool,
) -> WarningCheckResultMetadata | None:
    if not warning_check_required:
        return None
    from tools.warning_check_tool import warning_check_tool_executor

    result = warning_check_tool_executor(
        ToolInputMetadata.from_mapping(
            "warning_check_tool",
            {
                "command": command,
                "cwd": cwd or None,
                "stdout": command_result.stdout,
                "stderr": command_result.stderr,
            },
        )
    )
    return result.result if isinstance(result.result, WarningCheckResultMetadata) else None


def _warning_requires_fix(warning_check: WarningCheckResultMetadata | None) -> bool:
    return bool(warning_check and warning_check.requires_fix)


def _read_allowed_files(allowed_files: dict[str, Path]) -> dict[str, str]:
    contents: dict[str, str] = {}
    for path in sorted(set(allowed_files.values()), key=lambda item: str(item)):
        result = file_reader_executor(
            ToolInputMetadata.from_mapping(
                "file_reader",
                {
                    "file_path": str(path),
                    "read_mode": "full",
                    "encoding": "utf-8",
                    "max_size_mb": 5,
                },
            )
        )
        contents[str(path)] = str(result.get("content") or "")
    return contents


def _request_fix(
    *,
    llm_client: Any,
    command: str,
    cwd: str,
    target_files: list[str],
    file_contents: dict[str, str],
    command_result: CommandArtifactMetadata,
    attempts: list[BugFixAttemptMetadata],
    iteration: int,
    fix_instruction: str,
    terminal_smoke: bool,
) -> dict[str, Any]:
    request = build_context_llm_request(
        llm_client,
        purpose=ContextRequestPurpose.BUG_FIX,
        messages=[
            LLMMessage(
                role="system",
                content=(
                    "You are OpenPilot's Bug Fix Tool. Fix only program execution bugs: syntax errors, "
                    "runtime exceptions, import errors, command failures, missing wiring that prevents the command from running, "
                    "or runtime warnings explicitly marked as requiring repair because they harm user-visible behavior. "
                    "Do not optimize, redesign, change product behavior, or improve semantic output quality. "
                    "Return only JSON."
                ),
            ),
            LLMMessage(
                role="user",
                content=json.dumps(
                    {
                        "iteration": iteration,
                        "success_standard": "The command must exit with code 0. Do not judge stdout semantics.",
                        "terminal_success_standard": (
                            "For terminal-interactive programs, validation runs in a PTY. The program must start "
                            "without traceback or curses drawing errors; do not use a non-TTY sys.exit(0) bypass as a fix."
                            if terminal_smoke
                            else ""
                        ),
                        "warning_success_standard": (
                            "If runtime warning repair is required, the command must also run without warnings "
                            "classified as requiring repair."
                        ),
                        "command": command,
                        "cwd": cwd,
                        "target_files": target_files,
                        "last_command_result": command_result.to_json_dict(),
                        "previous_attempts": [attempt.to_json_dict() for attempt in attempts[-5:]],
                        "file_contents": file_contents,
                        "fix_instruction": fix_instruction,
                        "required_json_shape": {
                            "rationale": "brief runtime bug fix explanation",
                            "files": [{"file_path": "one of target_files", "content": "complete replacement file content"}],
                        },
                    },
                    ensure_ascii=False,
                ),
            ),
        ],
        response_format="json_object",
        temperature=0.0,
        max_tokens=6000,
        trace_info={"tool": "bug_fix_tool", "task": "runtime_bug_fix", "iteration": iteration},
        user_truncation=ContextCandidateTruncation.FORBIDDEN,
    )
    response = llm_client.complete(request)
    if getattr(response, "parsed_json", None) is not None:
        parsed = response.parsed_json
    else:
        parsed = json.loads(str(getattr(response, "content", "")))
    if not isinstance(parsed, dict):
        raise ValueError("Bug fix LLM response must be a JSON object")
    return parsed


def _validate_fix_payload(fix_payload: dict[str, Any], allowed_files: dict[str, Path]) -> tuple[dict[Path, str], str]:
    files = fix_payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("Bug fix LLM response must include a non-empty files list")
    changes: dict[Path, str] = {}
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Each bug fix file entry must be an object")
        raw_path = str(item.get("file_path") or "")
        if not raw_path:
            raise ValueError("Bug fix file entry is missing file_path")
        path = _allowed_path(raw_path, allowed_files)
        if path is None:
            raise ValueError(f"Bug fix attempted to modify undeclared file: {raw_path}")
        content = item.get("content")
        if not isinstance(content, str):
            raise ValueError(f"Bug fix content for {raw_path} must be a string")
        changes[path] = content
    rationale = str(fix_payload.get("rationale") or "").strip()
    return changes, rationale


def _allowed_path(raw_path: str, allowed_files: dict[str, Path]) -> Path | None:
    if raw_path in allowed_files:
        return allowed_files[raw_path]
    resolved = Path(raw_path).expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:
        return None
    return allowed_files.get(str(resolved))


def _write_changes(changes: dict[Path, str]) -> list[str]:
    modified_files: list[str] = []
    if changes:
        project_path = _common_project_path(list(changes))
        try:
            GitManagerAgent().snapshot(
                project_path,
                reason="bug_fix_tool_internal_write",
                target_files=[str(path) for path in changes],
            )
        except GitManagerError:
            pass
    for path, content in changes.items():
        file_writer_executor(
            ToolInputMetadata.from_mapping(
                "file_writer",
                {
                    "file_path": str(path),
                    "content": content,
                    "encoding": "utf-8",
                    "create_dirs": False,
                    "overwrite": True,
                    "operation_kind": "file_replace",
                },
            )
        )
        modified_files.append(str(path))
    return modified_files


def _common_project_path(paths: list[Path]) -> Path:
    if not paths:
        return Path.cwd()
    parents = [path.expanduser().resolve().parent for path in paths]
    try:
        return Path(os.path.commonpath([str(parent) for parent in parents]))
    except ValueError:
        return parents[0]


def _attempt(
    *,
    iteration: int,
    command_result: CommandArtifactMetadata,
    modified_files: list[str],
    rationale: str,
    llm_payload: dict[str, Any] | None = None,
) -> BugFixAttemptMetadata:
    return BugFixAttemptMetadata(
        iteration=iteration,
        command_result=command_result,
        error_summary=_command_error_summary(command_result),
        modified_files=modified_files,
        rationale=rationale,
        llm_payload=llm_payload or {},
    )


def _command_error_summary(command_result: CommandArtifactMetadata) -> str:
    if command_result.success:
        return ""
    stderr = command_result.stderr.strip()
    stdout = command_result.stdout.strip()
    detail = stderr or stdout
    return detail[:1000]


def _bug_fix_result(
    *,
    command: str,
    cwd: str,
    allowed_files: dict[str, Path],
    fixed: bool,
    iterations_used: int,
    max_iterations: int,
    continuation_iterations: int,
    attempts: list[BugFixAttemptMetadata],
    final_command_result: CommandArtifactMetadata,
    requires_user_decision: bool = False,
) -> BugFixResultMetadata:
    target_files = sorted({str(path) for path in allowed_files.values()})
    return BugFixResultMetadata(
        command=command,
        cwd=cwd,
        target_files=target_files,
        fixed=fixed,
        iterations_used=iterations_used,
        max_iterations=max_iterations,
        continuation_iterations=continuation_iterations,
        attempts=attempts,
        final_command_result=final_command_result,
        requires_user_decision=requires_user_decision,
    )


def _should_continue(callback: Any, result: BugFixResultMetadata) -> bool:
    try:
        return bool(callback(result))
    except TypeError:
        return bool(callback())
