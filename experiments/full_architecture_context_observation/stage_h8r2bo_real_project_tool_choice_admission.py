"""Replay the real-project mutation admission boundary after BN tool-choice repair.

This stage is deliberately provider-free: it uses a deterministic
provider-shaped mock client, a source-isolated temporary workspace, and the
production provider-tool execution entry.  The goal is to prove the real-project
task shape carries the BN ``tool_choice=required`` / finalization contract
before any credentialed real-project mutation arm is attempted.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping, Protocol, Sequence
from unittest.mock import patch

from autonomous_iteration.agents.tool_planning_executor import ToolPlanningTaskExecutor
from autonomous_iteration.task_models import Task, TaskExecutionContext, TaskStatus
from core.config import LLMSettings
from core.llm import LLMRequest, LLMResponse, LLMToolCall, LLMToolFunctionCall
from metadata import (
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ReasoningCapabilityProfileId,
)

from experiments.full_architecture_context_observation.stage_h8r2aj_real_execution import (
    _request_summary,
    _response_summary,
)
from experiments.full_architecture_context_observation.stage_h8r2at_provider_summary_shadow import (
    _write_receipt,
)
from experiments.full_architecture_context_observation.stage_h8r2av_multi_window_summary_quality import (
    canonical_hash,
)
from experiments.full_architecture_context_observation.stage_h8r2bh_builder_sourced_simulation import (
    _candidate_digest,
    _sha256_text,
)
from experiments.full_architecture_context_observation.stage_h8r2bi_token_aware_opt_in_canary import (
    _WhitespaceTokenCounter,
)
from experiments.full_architecture_context_observation.stage_h8r2bk_real_provider_read_only_paired_canary import (
    _safe_receipt_hash,
)
from experiments.full_architecture_context_observation.stage_h8r2bm_mutation_tool_shadow import (
    _attempts_summary,
    _changed_files,
    _ignored_runtime_artifact,
    _mutation_settings,
    _runtime,
    _usage_summary,
    _validation_environment,
)


SCHEMA = "phase-h8r2bo-real-project-tool-choice-admission-v1"
CLAIM_BOUNDARY = "real_project_tool_choice_admission_replay_no_provider_transport"
TASK_ID = "h8r2bo-provider-roundtrip-test-sentinel"
TARGET_FILE = "Code/tests/test_provider_tool_roundtrip.py"
SUPPORT_FILES = [
    "Code/src/core/provider_tool_roundtrip.py",
    "Code/src/tools/file_reader.py",
]
READ_FILES = [TARGET_FILE, *SUPPORT_FILES]
WRITE_FILES = [TARGET_FILE]
EXACT_VALIDATION_COMMAND = (
    "PYTHONPATH=Code/src:. python -m pytest -q "
    "Code/tests/test_provider_tool_roundtrip.py"
)
TOOL_ALLOWLIST = ["file_reader", "file_patch_writer", "command_executor"]
SENTINEL_TEST = """\
def test_h8r2bo_tool_choice_admission_sentinel():
    assert ProviderToolRoundTripRunner.__name__ == "ProviderToolRoundTripRunner"
"""

_SECRET_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}(?![A-Za-z0-9])")
_FORBIDDEN_BODY_KEYS = {
    "prompt",
    "body",
    "content",
    "content_preview",
    "response_text",
    "raw_response",
    "stdout",
    "stderr",
    "generated_unit",
    "replacement_text",
    "patch",
}


class SupportsComplete(Protocol):
    settings: Any

    def complete(self, request: LLMRequest, **kwargs: Any) -> LLMResponse: ...


@dataclass(frozen=True)
class ReplayOptions:
    finalization_tool_call: bool = False


class _MockRealProjectClient:
    """Deterministic provider-shaped client for the BO replay gate."""

    def __init__(self, project_root: Path, *, options: ReplayOptions | None = None) -> None:
        self.settings = _mock_settings()
        self.project_root = project_root
        self.options = options or ReplayOptions()
        self.requests: list[LLMRequest] = []
        self.response_history: list[LLMResponse] = []
        self._index = 0

    def complete(self, request: LLMRequest, **_kwargs: Any) -> LLMResponse:
        self.requests.append(request)
        prompt_tokens = max(
            1,
            sum(len(str(message.content or "").split()) for message in request.messages),
        )
        sequence = self._index
        self._index += 1
        if sequence == 0:
            response = LLMResponse(
                content="",
                tool_calls=[
                    LLMToolCall(
                        id="bo-read-target-1",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments=json.dumps(
                                {
                                    "file_path": TARGET_FILE,
                                    "read_mode": "adaptive",
                                    "offset": 0,
                                    "max_lines": 80,
                                }
                            ),
                        ),
                    )
                ],
                model="fake-deepseek-v4-flash",
                provider="offline-mock",
                finish_reason="tool_calls",
                usage=_usage(prompt_tokens, 24),
            )
        elif sequence == 1:
            response = LLMResponse(
                content="",
                tool_calls=[
                    LLMToolCall(
                        id="bo-add-test-1",
                        function=LLMToolFunctionCall(
                            name="file_patch_writer",
                            arguments=json.dumps(
                                {
                                    "file_path": TARGET_FILE,
                                    "operation_kind": "add_symbol",
                                    "generated_unit": SENTINEL_TEST,
                                    "insertion_hint": "end_of_file",
                                }
                            ),
                        ),
                    )
                ],
                model="fake-deepseek-v4-flash",
                provider="offline-mock",
                finish_reason="tool_calls",
                usage=_usage(prompt_tokens, 72),
            )
        elif sequence == 2:
            response = LLMResponse(
                content="",
                tool_calls=[
                    LLMToolCall(
                        id="bo-validate-1",
                        function=LLMToolFunctionCall(
                            name="command_executor",
                            arguments=json.dumps(
                                {
                                    "command": EXACT_VALIDATION_COMMAND,
                                    "cwd": str(self.project_root),
                                    "mode": "automatic",
                                    "timeout": 60,
                                    "env": {"PYTHONDONTWRITEBYTECODE": "1"},
                                }
                            ),
                        ),
                    )
                ],
                model="fake-deepseek-v4-flash",
                provider="offline-mock",
                finish_reason="tool_calls",
                usage=_usage(prompt_tokens, 48),
            )
        elif self.options.finalization_tool_call:
            response = LLMResponse(
                content="",
                tool_calls=[
                    LLMToolCall(
                        id="bo-finalization-tool-call-1",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments=json.dumps({"file_path": TARGET_FILE}),
                        ),
                    )
                ],
                model="fake-deepseek-v4-flash",
                provider="offline-mock",
                finish_reason="tool_calls",
                usage=_usage(prompt_tokens, 16),
            )
        else:
            response = LLMResponse(
                content="Real-project replay mutation and exact validation completed.",
                model="fake-deepseek-v4-flash",
                provider="offline-mock",
                finish_reason="stop",
                usage=_usage(prompt_tokens, 16),
            )
        self.response_history.append(response)
        return response


def _usage(prompt_tokens: int, completion_tokens: int) -> dict[str, int]:
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _mock_settings() -> LLMSettings:
    return _mutation_settings(
        LLMSettings(
            _env_file=None,
            provider="openai-compatible",
            base_url="https://api.deepseek.com",
            api_key="test-key",
            model="fake-deepseek-v4-flash",
            temperature=0.0,
            transport_retries=0,
            reasoning_capability_profile=ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
        )
    )


def _task() -> Task:
    return Task(
        id=TASK_ID,
        description=(
            "In a disposable source-isolated workspace, add exactly one focused "
            "sentinel pytest test to Code/tests/test_provider_tool_roundtrip.py. "
            "Do not modify production source, docs, README, config, or any other test. "
            "Run the exact validation command after the scoped edit."
        ),
        kind="implement",
        read_files=list(READ_FILES),
        write_files=list(WRITE_FILES),
        validation_command=EXACT_VALIDATION_COMMAND,
        tags=["h8r2bo", "real_project_shape", "tool_choice_admission"],
    )


def _authority_candidate() -> ContextCandidate:
    return ContextCandidate(
        candidate_id="h8r2bo-real-project-task-authority",
        kind=ContextCandidateKind.TASK,
        source_id="h8r2bo:real-project-task-contract",
        role="system",
        content=(
            "Provider-tool mode is mandatory. The first response must call file_reader "
            f"for {TARGET_FILE}; do not answer in prose before the first tool call. "
            f"Then call file_patch_writer once for {TARGET_FILE} with operation_kind=add_symbol. "
            f"Then call command_executor with the exact command: {EXACT_VALIDATION_COMMAND}. "
            "The command cwd must be the disposable project root. "
            "Modify no files outside write_files."
        ),
        retention=ContextCandidateRetention.REQUIRED,
        priority=100,
        source_order=0,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.AUTHORITATIVE,
        freshness=ContextCandidateFreshness.CURRENT,
    )


def _constraint_candidate() -> ContextCandidate:
    return ContextCandidate(
        candidate_id="h8r2bo-mutation-scope-constraint",
        kind=ContextCandidateKind.CONSTRAINT,
        source_id="h8r2bo:scope-and-validation",
        role="system",
        content=(
            f"read_files={','.join(READ_FILES)}; write_files={','.join(WRITE_FILES)}; "
            f"validation_command_sha256={canonical_hash(EXACT_VALIDATION_COMMAND)}; "
            "no_readme=true; no_production_source_write=true; no_default_on=true."
        ),
        retention=ContextCandidateRetention.REQUIRED,
        priority=99,
        source_order=1,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.AUTHORITATIVE,
        freshness=ContextCandidateFreshness.CURRENT,
    )


def _initial_candidates() -> list[ContextCandidate]:
    return [_authority_candidate(), _constraint_candidate()]


def _copy_source_workspace(source_root: Path, project_root: Path) -> None:
    code_root = project_root / "Code"
    tests_root = code_root / "tests"
    tests_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source_root / "Code" / "src",
        code_root / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    shutil.copy2(
        source_root / TARGET_FILE,
        project_root / TARGET_FILE,
    )


def _source_file_hashes(project_root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(project_root.rglob("*")):
        if path.is_file() and not _ignored_runtime_artifact(path, project_root):
            hashes[path.relative_to(project_root).as_posix()] = canonical_hash(
                path.read_bytes().hex()
            )
    return hashes


def _tool_event_summary(
    attributes: Mapping[str, Any],
    *,
    project_root: Path,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for loop in attributes.get("tool_loops") or []:
        for event in loop.get("events") or []:
            if not isinstance(event, Mapping):
                continue
            input_metadata = event.get("input_metadata") or {}
            output_metadata = event.get("output_metadata") or {}
            result = output_metadata.get("result") if isinstance(output_metadata, Mapping) else {}
            result = result if isinstance(result, Mapping) else {}
            tool_name = str(event.get("tool_name") or input_metadata.get("tool_name") or "")
            file_path = str(input_metadata.get("file_path") or "")
            command = str(input_metadata.get("command") or "")
            cwd = str(input_metadata.get("cwd") or "")
            relative_file = ""
            if file_path:
                try:
                    relative_file = (
                        Path(file_path)
                        .expanduser()
                        .resolve()
                        .relative_to(project_root.resolve())
                        .as_posix()
                    )
                except Exception:
                    relative_file = ""
            events.append(
                {
                    "tool_name": tool_name,
                    "event_type": str(event.get("event_type") or ""),
                    "status": str(event.get("status") or ""),
                    "round_index": int(event.get("round_index") or 0),
                    "file_path_sha256": _sha256_text(file_path) if file_path else "",
                    "relative_file": relative_file,
                    "command_sha256": canonical_hash(command) if command else "",
                    "command_is_exact_validation": command == EXACT_VALIDATION_COMMAND,
                    "cwd_sha256": _sha256_text(cwd) if cwd else "",
                    "cwd_is_project_root": bool(cwd)
                    and Path(cwd).expanduser().resolve() == project_root.resolve(),
                    "mode": str(input_metadata.get("mode") or ""),
                    "output_success": result.get("success"),
                    "exit_code": result.get("exit_code"),
                    "error_type": str((event.get("failure") or {}).get("error_type") or ""),
                }
            )
    return events


def _independent_validation(project_root: Path) -> dict[str, Any]:
    result = subprocess.run(
        EXACT_VALIDATION_COMMAND,
        shell=True,
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=90,
        env={
            **dict(__import__("os").environ),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
        },
    )
    return {
        "command_sha256": canonical_hash(EXACT_VALIDATION_COMMAND),
        "exit_zero": result.returncode == 0,
        "exit_code": result.returncode,
    }


def _mutation_gate(
    *,
    result_status: TaskStatus,
    before_hashes: Mapping[str, str],
    after_hashes: Mapping[str, str],
    target_before_sha256: str,
    target_after_sha256: str,
    tool_events: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
    independent_validation: Mapping[str, Any],
) -> dict[str, Any]:
    changed = _changed_files(before_hashes, after_hashes)
    writer_observed = any(
        attempt.get("tool_name") == "file_patch_writer"
        and attempt.get("success") is True
        for attempt in attempts
    )
    validation_events = [
        event
        for event in tool_events
        if event.get("tool_name") == "command_executor"
        and event.get("event_type") == "completed"
        and event.get("command_is_exact_validation") is True
        and event.get("cwd_is_project_root") is True
    ]
    provider_exact_validation_observed = bool(validation_events)
    provider_validation_exit_zero = any(
        event.get("output_success") is True and event.get("exit_code") == 0
        for event in validation_events
    )
    only_scoped = changed == [TARGET_FILE]
    suspicious_success = bool(target_before_sha256 != target_after_sha256) and (
        not provider_exact_validation_observed or not provider_validation_exit_zero
    )
    return {
        "target_before_sha256": target_before_sha256,
        "target_after_sha256": target_after_sha256,
        "target_changed": target_before_sha256 != target_after_sha256,
        "changed_source_files": changed,
        "only_scoped_source_file_changed": only_scoped,
        "writer_observed": writer_observed,
        "provider_exact_validation_observed": provider_exact_validation_observed,
        "provider_validation_exit_zero": provider_validation_exit_zero,
        "independent_exact_validation_exit_zero": independent_validation.get("exit_zero")
        is True,
        "suspicious_success": suspicious_success,
        "passed": bool(
            result_status == TaskStatus.COMPLETED
            and writer_observed
            and provider_exact_validation_observed
            and provider_validation_exit_zero
            and independent_validation.get("exit_zero") is True
            and only_scoped
            and target_before_sha256 != target_after_sha256
            and not suspicious_success
        ),
    }


def _request_shape_gate(requests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tool_phase_requests = [
        request for request in requests if request.get("tool_names")
    ]
    finalization_requests = [
        request for request in requests if not request.get("tool_names")
    ]
    required_tool_choice = all(
        request.get("tool_choice") == "required" for request in tool_phase_requests
    )
    finalization_without_tool_choice = bool(finalization_requests) and all(
        request.get("tool_choice") is None for request in finalization_requests
    )
    finalization_without_tools = bool(finalization_requests) and all(
        not request.get("tool_names") for request in finalization_requests
    )
    return {
        "request_count": len(requests),
        "tool_phase_request_count": len(tool_phase_requests),
        "finalization_request_count": len(finalization_requests),
        "all_tool_phase_requests_required": required_tool_choice,
        "finalization_without_tool_choice": finalization_without_tool_choice,
        "finalization_without_tools": finalization_without_tools,
        "passed": bool(
            len(requests) >= 4
            and len(tool_phase_requests) >= 3
            and required_tool_choice
            and finalization_without_tool_choice
            and finalization_without_tools
        ),
    }


def run_real_project_tool_choice_admission_replay(
    *,
    output_root: Path,
    source_root: Path | None = None,
    options: ReplayOptions | None = None,
) -> dict[str, Any]:
    source_root = (source_root or Path.cwd()).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="h8r2bo-real-project-") as tmp:
        project_root = Path(tmp) / "source"
        _copy_source_workspace(source_root, project_root)
        before_hashes = _source_file_hashes(project_root)
        target = project_root / TARGET_FILE
        target_before_sha256 = canonical_hash(target.read_bytes().hex())

        client = _MockRealProjectClient(project_root, options=options)
        runtime = _runtime(client)
        executor = ToolPlanningTaskExecutor(runtime)
        task = _task()
        context = TaskExecutionContext(
            task=task,
            parent_context={"goal": task.description, "project_path": str(project_root)},
        )
        with _validation_environment():
            with patch(
                "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
                return_value=_WhitespaceTokenCounter(),
            ):
                result = executor.execute_provider_tool_task(
                    task,
                    context,
                    tool_names=list(TOOL_ALLOWLIST),
                    user_confirmed=True,
                    allow_mutations=True,
                    max_rounds=8,
                    initial_context_candidates=_initial_candidates(),
                )
            independent_validation = _independent_validation(project_root)

        after_hashes = _source_file_hashes(project_root)
        target_after_sha256 = canonical_hash(target.read_bytes().hex())
        attributes = dict(result.attributes or {})
        requests = [_request_summary(request) for request in client.requests]
        responses = [_response_summary(response) for response in client.response_history]
        attempts = _attempts_summary(attributes)
        tool_events = _tool_event_summary(attributes, project_root=project_root)
        mutation_gate = _mutation_gate(
            result_status=result.status,
            before_hashes=before_hashes,
            after_hashes=after_hashes,
            target_before_sha256=target_before_sha256,
            target_after_sha256=target_after_sha256,
            tool_events=tool_events,
            attempts=attempts,
            independent_validation=independent_validation,
        )
        request_shape_gate = _request_shape_gate(requests)
        usage = _usage_summary(client.response_history)

    status = (
        "passed"
        if mutation_gate["passed"]
        and request_shape_gate["passed"]
        and result.status == TaskStatus.COMPLETED
        else "needs_followup"
    )
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "status": status,
        "claim_boundary": CLAIM_BOUNDARY,
        "task": {
            "task_id": TASK_ID,
            "kind": "implement",
            "read_files": list(READ_FILES),
            "write_files": list(WRITE_FILES),
            "description_sha256": canonical_hash(task.description),
        },
        "validation_command_sha256": canonical_hash(EXACT_VALIDATION_COMMAND),
        "tool_allowlist": list(TOOL_ALLOWLIST),
        "snapshot_hashes": {
            "root_path_sha256": _sha256_text(str(source_root)),
            "target_file_sha256": canonical_hash((source_root / TARGET_FILE).read_bytes().hex()),
            "support_file_hashes": {
                path: canonical_hash((source_root / path).read_bytes().hex())
                for path in SUPPORT_FILES
            },
        },
        "candidate_digests": [
            _candidate_digest(candidate) for candidate in _initial_candidates()
        ],
        "execution": {
            "execution_mode": attributes.get("execution_mode"),
            "allow_mutations": attributes.get("allow_mutations"),
            "user_confirmed": attributes.get("user_confirmed"),
            "budget_profile": attributes.get("budget_profile"),
            "reasoning_mode": attributes.get("reasoning_mode"),
            "rounds_used": attributes.get("rounds_used"),
            "task_status": result.status.value,
            "task_error_type": type(result.error).__name__ if result.error else "",
            "task_error_message": str(result.error) if result.error else "",
            "request_diagnostic_count": len(attributes.get("request_diagnostics") or []),
            "budget_diagnostic_count": len(attributes.get("budget_diagnostics") or []),
        },
        "usage": usage,
        "requests": requests,
        "responses": responses,
        "attempts": attempts,
        "tool_events": tool_events,
        "mutation_gate": mutation_gate,
        "request_shape_gate": request_shape_gate,
        "independent_validation": independent_validation,
        "side_effects": {
            "provider_transport_attempted": False,
            "provider_calls": 0,
            "mock_completion_calls": len(client.requests),
            "network_side_effects": 0,
            "project_mutations": 1 if mutation_gate["target_changed"] else 0,
            "memory_mutations": 0,
            "writer_actions": sum(
                1
                for attempt in attempts
                if attempt.get("tool_name") == "file_patch_writer"
                and attempt.get("success") is True
            ),
            "command_actions": sum(
                1
                for attempt in attempts
                if attempt.get("tool_name") == "command_executor"
                and attempt.get("success") is True
            ),
            "verification_runs": 1,
            "retry_count": 0,
            "fallback_count": 0,
            "used_in_prompt": False,
        },
        "secret_handling": {
            "credential_present": False,
            "credential_serialized": False,
        },
        "receipt_body_free": True,
    }
    receipt["receipt_hash"] = _safe_receipt_hash(receipt)
    _write_receipt(output_root / "aggregate" / "receipt.json", receipt)
    return receipt


def _assert_no_bodies(value: Any, *, location: str = "receipt") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_BODY_KEYS:
                raise ValueError(f"full prompt/body field was recorded at {location}.{key}")
            _assert_no_bodies(child, location=f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_bodies(child, location=f"{location}[{index}]")


def validate_real_project_tool_choice_admission_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    data = dict(receipt)
    if data.get("schema") != SCHEMA:
        raise ValueError("BO receipt schema mismatch")
    if data.get("receipt_hash") != _safe_receipt_hash(data):
        raise ValueError("BO receipt hash mismatch")
    if data.get("tool_allowlist") != TOOL_ALLOWLIST:
        raise ValueError("BO tool allowlist mismatch")
    if data.get("validation_command_sha256") != canonical_hash(EXACT_VALIDATION_COMMAND):
        raise ValueError("BO validation command hash mismatch")
    if (data.get("secret_handling") or {}).get("credential_serialized") is not False:
        raise ValueError("BO credential serialization flag mismatch")
    if (data.get("side_effects") or {}).get("provider_transport_attempted") is not False:
        raise ValueError("BO replay must not attempt provider transport")
    if (data.get("side_effects") or {}).get("used_in_prompt") is not False:
        raise ValueError("BO prompt-use flag mismatch")
    for event in data.get("tool_events") or []:
        if event.get("tool_name") not in TOOL_ALLOWLIST:
            raise ValueError("BO tool escaped allowlist")
    if data.get("status") == "passed":
        gate = data.get("mutation_gate") or {}
        request_gate = data.get("request_shape_gate") or {}
        if gate.get("passed") is not True:
            raise ValueError("BO passed receipt lacks mutation gate pass")
        if request_gate.get("passed") is not True:
            raise ValueError("BO passed receipt lacks request-shape gate pass")
        if gate.get("changed_source_files") != [TARGET_FILE]:
            raise ValueError("BO passed receipt changed an out-of-scope file")
        if gate.get("provider_exact_validation_observed") is not True:
            raise ValueError("BO passed receipt lacks provider exact validation")
        if gate.get("independent_exact_validation_exit_zero") is not True:
            raise ValueError("BO passed receipt lacks independent validation")
    encoded = json.dumps(data, ensure_ascii=False)
    if _SECRET_RE.search(encoded):
        raise ValueError("BO receipt contains a secret-shaped value")
    _assert_no_bodies(data)
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = run_real_project_tool_choice_admission_replay(
        output_root=args.output_root,
        source_root=args.source_root,
    )
    validate_real_project_tool_choice_admission_receipt(result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "receipt_hash": result["receipt_hash"],
                "request_shape_gate": result["request_shape_gate"],
                "mutation_gate_passed": result["mutation_gate"]["passed"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
