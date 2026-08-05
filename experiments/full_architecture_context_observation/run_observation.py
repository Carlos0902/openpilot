"""Run the frozen full-architecture context observation pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import tempfile
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping
from unittest.mock import patch

from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from autonomous_iteration.models import ImprovementGoal
from core.config import LLMSettings
from core.llm import LLMClient, LLMRequest, LLMResponse, normalized_provider_endpoint
from core.reasoning import resolve_reasoning_policy
from core.token_counting import ProviderTokenCounter
from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks
from runtime_diagnostics.recorder import DiagnosticRecorder
from fixed_decomposition_harness import (
    collect_improvement_cost,
    evaluate_upstream_gate,
    install_fixed_decomposition,
)
from metadata import (
    ContextRequestPurpose,
    EnhancementCompletionBudgetPolicy,
    EnhancementCompletionPurposeLimit,
    ProjectImprovementPolicy,
    ProjectImprovementPolicySource,
    ProjectImprovementRequirement,
    ReasoningPolicy,
)
from memory.memory_store import MemoryStore


ROOT = Path(__file__).resolve().parent
PROTOCOL_PATH = ROOT / "OBSERVATION_PROTOCOL_V1.json"
PROVIDER_ARM_PROTOCOL_PATH = ROOT / "PROJECT_IMPROVEMENT_PROVIDER_ARM_PROTOCOL_V1.json"
PROVIDER_ARM_PROTOCOL_ID = "project-improvement-provider-arm-v1"
_SNAPSHOT_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}


def capture_bounded_project_snapshot(
    project_root: str | Path,
    *,
    max_files: int = 200,
    max_file_bytes: int = 4_000_000,
    max_total_bytes: int = 20_000_000,
) -> dict[str, Any]:
    """Hash bounded project files without following links or environment trees."""

    root_path = Path(project_root).expanduser()
    if root_path.is_symlink():
        raise ValueError("project snapshot root must not be a symlink")
    root = root_path.resolve(strict=True)
    files: dict[str, str] = {}
    symlinks: list[str] = []
    truncated = False
    total_bytes = 0
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories = []
        for name in sorted(directories):
            path = current_path / name
            if name in _SNAPSHOT_EXCLUDED_DIRS:
                continue
            if path.is_symlink():
                symlinks.append(str(path.absolute()))
                continue
            kept_directories.append(name)
        directories[:] = kept_directories
        for name in sorted(filenames):
            path = current_path / name
            if path.is_symlink():
                symlinks.append(str(path.absolute()))
                continue
            if len(files) >= max_files:
                truncated = True
                break
            size = path.stat().st_size
            if size > max_file_bytes or total_bytes + size > max_total_bytes:
                truncated = True
                break
            resolved = path.resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError:
                symlinks.append(str(path.absolute()))
                continue
            files[str(resolved)] = hashlib.sha256(path.read_bytes()).hexdigest()
            total_bytes += size
        if truncated:
            break
    return {
        "project_root": str(root),
        "max_files": max_files,
        "max_file_bytes": max_file_bytes,
        "max_total_bytes": max_total_bytes,
        "total_bytes": total_bytes,
        "files": dict(sorted(files.items())),
        "symlink_paths": sorted(set(symlinks)),
        "truncated": truncated,
    }


def diff_project_snapshots(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    if before.get("project_root") != after.get("project_root"):
        raise ValueError("project snapshot roots differ")
    before_files = dict(before.get("files") or {})
    after_files = dict(after.get("files") or {})
    added = sorted(set(after_files) - set(before_files))
    deleted = sorted(set(before_files) - set(after_files))
    modified = sorted(
        path
        for path in set(before_files) & set(after_files)
        if before_files[path] != after_files[path]
    )
    return {
        "project_root": before.get("project_root"),
        "added_paths": added,
        "deleted_paths": deleted,
        "modified_paths": modified,
        "all_changed_paths": sorted([*added, *deleted, *modified]),
        "before_truncated": bool(before.get("truncated")),
        "after_truncated": bool(after.get("truncated")),
        "symlink_paths": sorted(
            set(before.get("symlink_paths") or [])
            | set(after.get("symlink_paths") or [])
        ),
    }


def build_enhancement_mutation_observation(
    intervention: Mapping[str, Any] | None,
    final_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Diff the canonical Task Designer boundary against the final project."""

    descriptor = intervention or {}
    boundary = descriptor.get("enhancement_start_project_snapshot")
    common = {
        "snapshot_missing": not isinstance(boundary, Mapping),
        "start_capture_count": int(
            descriptor.get("enhancement_start_snapshot_capture_count") or 0
        ),
        "start_observation_count": int(
            descriptor.get("enhancement_start_snapshot_observation_count") or 0
        ),
        "start_consistent": descriptor.get(
            "enhancement_start_snapshot_consistent"
        )
        is True,
    }
    if not isinstance(boundary, Mapping):
        return {
            "project_root": final_snapshot.get("project_root"),
            "added_paths": [],
            "deleted_paths": [],
            "modified_paths": [],
            "all_changed_paths": [],
            "before_truncated": False,
            "after_truncated": bool(final_snapshot.get("truncated")),
            "symlink_paths": sorted(final_snapshot.get("symlink_paths") or []),
            **common,
        }
    return {**diff_project_snapshots(boundary, final_snapshot), **common}
EXPERIMENT_PROVIDER_FRAMING_RESERVE_TOKENS = 128


@contextmanager
def experiment_memory_scope(mode: str, output_dir: Path):
    """Select the experiment-only memory baseline before runtime construction."""

    if mode == "shared":
        yield {"strategy": "shared", "data_dir": "data/memory"}
        return
    if mode != "isolated_empty":
        raise ValueError(f"unsupported experiment memory mode: {mode}")
    data_dir = (output_dir / "isolated_memory").resolve()
    with patch(
        "autonomous_iteration.intelligent_autopilot.MemoryStore",
        new=lambda: MemoryStore(data_dir),
    ):
        yield {"strategy": "isolated_empty", "data_dir": str(data_dir)}


def configure_enhancement_budget_arm(
    autopilot: IntelligentAutopilot,
    arm: str | None,
    *,
    iteration_goal_mode: str = "provider",
) -> dict[str, Any] | None:
    """Apply an experiment-only completion policy to the shared enhancement budget."""

    if arm is None:
        return None
    budget = autopilot._local_enhancement_runtime_budget
    if arm == "dynamic":
        descriptor = {"budget_mode": "production_dynamic_policy"}
    elif arm == "static":
        caps = {
            ContextRequestPurpose.PROJECT_IMPROVEMENT: 1_500,
            ContextRequestPurpose.ITERATION_GOAL: 1_200,
            ContextRequestPurpose.ITERATION_TASK_DESIGN: 2_200,
            ContextRequestPurpose.CODE_GENERATION: 3_500,
            ContextRequestPurpose.CODE_EDIT: 1_600,
        }
        budget.enhancement_completion_policy = EnhancementCompletionBudgetPolicy(
            total_tokens=12_000,
            recovery_step=0,
            purpose_limits={
                purpose: EnhancementCompletionPurposeLimit(floor=cap, ceiling=cap)
                for purpose, cap in caps.items()
            },
        )
        descriptor = {
            "budget_mode": "static_purpose_ceiling",
            "purpose_max_tokens": {
                purpose.value: cap for purpose, cap in caps.items()
            },
        }
    else:
        raise ValueError(f"unsupported enhancement budget arm: {arm}")
    autopilot.iterative_improvement.runtime_budget = budget
    # The full runtime resolves the authoritative controller-owned budget at
    # enhancement start. Pin that resolver for this disposable experiment arm
    # so the declared arm cannot be overwritten by controller initialization.
    autopilot._enhancement_runtime_budget = lambda: budget
    if iteration_goal_mode == "provider":
        autopilot.iterative_improvement._goal_from_candidate = (
            lambda selected_candidate, report, evaluation: None
        )
        goal_intervention = {
            "strategy": "provider",
            "injection_point": "AutonomousIterationAgent._goal_from_candidate",
            "behavior": "return_none_to_disable_deterministic_selected_candidate_bypass",
        }
    elif iteration_goal_mode == "fixed_divide_docstring":
        fixed_goal = ImprovementGoal(
            id="campaign-divide-docstring",
            title="Document divide's denominator-zero contract.",
            category="documentation",
            rationale=(
                "The repaired public function should explain its typed domain error "
                "without changing tests or unrelated symbols."
            ),
            acceptance_criteria=[
                "divide has a concise docstring that documents ValueError when denominator is zero.",
                "calculator.py remains the only modified file.",
                "python -m pytest -q passes.",
                "python -m compileall -q calculator.py succeeds.",
            ],
            priority="high",
        )
        autopilot.iterative_improvement._goal_from_candidate = (
            lambda selected_candidate, report, evaluation: fixed_goal
        )
        goal_intervention = {
            "strategy": "fixed_divide_docstring",
            "injection_point": "AutonomousIterationAgent._goal_from_candidate",
            "behavior": "return_fixed_observable_divide_docstring_goal",
        }
    else:
        raise ValueError(
            f"unsupported experiment iteration goal mode: {iteration_goal_mode}"
        )
    return {
        **descriptor,
        "common_iteration_goal_intervention": {
            "injection_point": goal_intervention["injection_point"],
            "behavior": goal_intervention["behavior"],
        },
        "iteration_goal_intervention": goal_intervention,
        "budget_resolver_intervention": {
            "injection_point": "IntelligentAutopilot._enhancement_runtime_budget",
            "behavior": "return_experiment_arm_budget",
        },
        "policy": budget.enhancement_completion_policy.model_dump(mode="json"),
    }


class ObservationLimitExceeded(RuntimeError):
    pass


def provider_runtime_identity(settings: LLMSettings) -> dict[str, str]:
    resolved = resolve_reasoning_policy(ReasoningPolicy(), settings)
    mode = settings.tool_event_reasoning_mode
    return {
        "provider": settings.provider,
        "model": settings.model,
        "endpoint": normalized_provider_endpoint(settings.base_url),
        "tool_event_reasoning_mode": mode.value,
        "reasoning_profile": f"{resolved.profile_id.value}:{resolved.profile_version}",
    }


def resolve_protocol_path(
    *, protocol_path: Path | None, fixed_decomposition: bool
) -> Path:
    if protocol_path is not None and fixed_decomposition:
        raise ValueError("--protocol and --fixed-decomposition are mutually exclusive")
    if fixed_decomposition:
        return PROVIDER_ARM_PROTOCOL_PATH
    return (protocol_path or PROTOCOL_PATH).resolve()


def apply_protocol_intervention(
    autopilot: IntelligentAutopilot,
    protocol: dict[str, Any],
) -> dict[str, Any] | None:
    intervention = protocol.get("intervention")
    if intervention == "none":
        if "fixed_decomposition" in protocol:
            raise ValueError("non-intervention protocol must not contain fixed_decomposition")
        return None
    if intervention != "fixed_decomposition":
        raise ValueError(f"unsupported experiment intervention: {intervention!r}")
    if protocol.get("protocol_id") != PROVIDER_ARM_PROTOCOL_ID:
        raise ValueError("fixed decomposition is only allowed by the provider-arm protocol")

    decomposition_contract = protocol.get("fixed_decomposition")
    if not isinstance(decomposition_contract, dict):
        raise ValueError("provider-arm protocol requires fixed_decomposition")
    fixed_decomposition = install_fixed_decomposition(
        autopilot,
        fixture_path=ROOT / decomposition_contract["fixture_path"],
        expected_sha256=decomposition_contract["fixture_sha256"],
    )
    for field_name in (
        "fixture_id",
        "source_run",
        "injection_point",
        "replaced_method",
        "validation_command_sha256",
    ):
        if fixed_decomposition[field_name] != decomposition_contract[field_name]:
            raise ValueError(f"fixed decomposition protocol mismatch for {field_name}")
    if fixed_decomposition["delegated_methods"] != decomposition_contract["delegated_methods"]:
        raise ValueError("fixed decomposition delegated-method contract mismatch")
    return fixed_decomposition


class GuardedLLMClient:
    """Bound calls and reserve the worst-case current request before transport."""

    def __init__(
        self,
        client: LLMClient,
        *,
        max_calls: int,
        max_total_tokens: int,
        max_wall_clock_seconds: float,
        default_max_completion_tokens: int | None = None,
        token_counter: Any | None = None,
        framing_reserve_tokens: int = EXPERIMENT_PROVIDER_FRAMING_RESERVE_TOKENS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._client = client
        self.settings = client.settings
        self.max_calls = max_calls
        self.max_total_tokens = max_total_tokens
        self.max_wall_clock_seconds = max_wall_clock_seconds
        self.default_max_completion_tokens = default_max_completion_tokens
        self.token_counter = token_counter or getattr(client, "token_counter", None)
        self.framing_reserve_tokens = int(framing_reserve_tokens)
        if self.framing_reserve_tokens < 0:
            raise ValueError("framing reserve tokens must be non-negative")
        self._clock = clock
        self._started_at = clock()
        self.calls = 0
        self.total_tokens = 0
        self.successful_response_tokens = 0
        self.failed_attempt_tokens = 0
        self.attempts_with_observed_usage = 0
        self.failed_attempts_without_observed_usage = 0
        self.successful_attempts_without_observed_usage = 0
        self.attempts_without_observed_usage = 0
        self.unsettled_reserved_tokens = 0
        self.usage_censored = False
        self.reservation_overrun_tokens = 0
        self.last_request_reservation_tokens = 0
        self.defaulted_max_completion_request_count = 0
        self.effective_max_completion_tokens: list[int] = []
        self.blocked_reservation_tokens = 0
        self.reservation_admission_failed = False
        self.pretransport_failure_reason = ""
        self.blocked_requests = 0
        self.last_attempt_reached_or_crossed_limits: list[str] = []

    def _elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    def _blocking_limits(self) -> list[str]:
        limits: list[str] = []
        if self.calls >= self.max_calls:
            limits.append("max_provider_calls")
        if self.total_tokens >= self.max_total_tokens:
            limits.append("max_cumulative_provider_tokens")
        if self.usage_censored:
            limits.append("unknown_provider_usage")
        if self.reservation_overrun_tokens:
            limits.append("provider_usage_exceeded_reservation")
        if self.reservation_admission_failed:
            limits.append("request_token_reservation_blocked")
        if self._elapsed_seconds() >= self.max_wall_clock_seconds:
            limits.append("wall_clock_seconds")
        return limits

    def _record_usage(
        self,
        usage: dict[str, Any] | None,
        *,
        failed: bool,
        reservation_tokens: int,
    ) -> None:
        if not isinstance(usage, dict) or not _usage_observed(usage):
            self.attempts_without_observed_usage += 1
            if failed:
                self.failed_attempts_without_observed_usage += 1
            else:
                self.successful_attempts_without_observed_usage += 1
            self.unsettled_reserved_tokens += reservation_tokens
            self.usage_censored = True
            return
        tokens = _usage_int(usage, "total_tokens")
        self.total_tokens += tokens
        self.attempts_with_observed_usage += 1
        if failed:
            self.failed_attempt_tokens += tokens
        else:
            self.successful_response_tokens += tokens

        if tokens > reservation_tokens:
            self.reservation_overrun_tokens += tokens - reservation_tokens

    def _prepare_request(self, request: LLMRequest) -> tuple[LLMRequest, bool]:
        if not isinstance(request, LLMRequest):
            raise ObservationLimitExceeded(
                "observation token reservation requires a typed LLMRequest"
            )
        defaulted = request.max_tokens is None
        if defaulted:
            default_limit = self.default_max_completion_tokens
            if default_limit is None or int(default_limit) <= 0:
                raise ObservationLimitExceeded(
                    "observation default max completion tokens must be configured "
                    "as a positive integer for an unbounded typed request"
                )
            request = request.model_copy(
                update={"max_tokens": int(default_limit), "transport_retries": 0}
            )
        else:
            request = request.model_copy(update={"transport_retries": 0})
        return request, defaulted

    def _request_reservation(self, request: LLMRequest) -> int:
        if request.max_tokens is None:
            raise ObservationLimitExceeded(
                "observation token reservation requires explicit max_tokens"
            )
        counter = self.token_counter
        if counter is None or getattr(counter, "available", False) is not True:
            raise ObservationLimitExceeded(
                "observation token reservation requires an exact provider token counter"
            )
        try:
            input_tokens = sum(
                int(counter.count_text(message.content)) for message in request.messages
            )
        except Exception as exc:
            raise ObservationLimitExceeded(
                "observation token counter could not count the current request exactly"
            ) from exc
        return input_tokens + self.framing_reserve_tokens + int(request.max_tokens)

    def _block_before_transport(self, reason: str, message: str) -> None:
        self.blocked_requests += 1
        self.pretransport_failure_reason = reason
        raise ObservationLimitExceeded(message)

    def complete(self, request: LLMRequest, **kwargs: Any) -> LLMResponse:
        blocking_limits = self._blocking_limits()
        if blocking_limits:
            self._block_before_transport(
                ",".join(blocking_limits),
                "observation safety limit blocks the next call before provider request: "
                + ", ".join(blocking_limits),
            )
        try:
            effective_request, defaulted = self._prepare_request(request)
            reservation_tokens = self._request_reservation(effective_request)
        except ObservationLimitExceeded as exc:
            self.reservation_admission_failed = True
            self._block_before_transport("request_reservation_unavailable", str(exc))
        self.last_request_reservation_tokens = reservation_tokens
        projected_tokens = (
            self.total_tokens + self.unsettled_reserved_tokens + reservation_tokens
        )
        if projected_tokens > self.max_total_tokens:
            self.blocked_reservation_tokens = reservation_tokens
            self.reservation_admission_failed = True
            self._block_before_transport(
                "max_cumulative_provider_tokens_reservation",
                "observation token reservation blocks the provider request before "
                f"transport: settled_or_held={self.total_tokens + self.unsettled_reserved_tokens}, "
                f"request_reservation={reservation_tokens}, hard_cap={self.max_total_tokens}",
            )
        effective_max_tokens = int(effective_request.max_tokens or 0)
        self.effective_max_completion_tokens.append(effective_max_tokens)
        if defaulted:
            self.defaulted_max_completion_request_count += 1
        self.calls += 1
        kwargs.pop("use_cache", None)
        # Campaign safety is enforced at the transport boundary, not merely
        # declared in the campaign protocol.  A request-level value also wins
        # over the provider setting, whose normal production default may retry.
        try:
            response = self._client.complete(
                effective_request, use_cache=False, **kwargs
            )
        except Exception as exc:
            self._record_usage(
                _failed_provider_attempt_usage(exc),
                failed=True,
                reservation_tokens=reservation_tokens,
            )
            self.last_attempt_reached_or_crossed_limits = self._blocking_limits()
            raise
        self._record_usage(
            getattr(response, "usage", None),
            failed=False,
            reservation_tokens=reservation_tokens,
        )
        self.last_attempt_reached_or_crossed_limits = self._blocking_limits()
        return response

    def observation_snapshot(self) -> dict[str, Any]:
        blocking_limits = self._blocking_limits()
        return {
            "logical_complete_calls_seen": self.calls,
            "provider_attempts_with_observed_usage": self.attempts_with_observed_usage,
            "successful_response_usage_tokens_seen": self.successful_response_tokens,
            "failed_attempt_usage_tokens_seen": self.failed_attempt_tokens,
            "failed_attempts_without_observed_usage": (
                self.failed_attempts_without_observed_usage
            ),
            "successful_attempts_without_observed_usage": (
                self.successful_attempts_without_observed_usage
            ),
            "attempts_without_observed_usage": self.attempts_without_observed_usage,
            "usage_censored": self.usage_censored,
            "unsettled_reserved_tokens": self.unsettled_reserved_tokens,
            "reservation_overrun_tokens": self.reservation_overrun_tokens,
            "last_request_reservation_tokens": self.last_request_reservation_tokens,
            "default_max_completion_tokens": self.default_max_completion_tokens,
            "defaulted_max_completion_request_count": (
                self.defaulted_max_completion_request_count
            ),
            "effective_max_completion_tokens": list(
                self.effective_max_completion_tokens
            ),
            "last_effective_max_completion_tokens": (
                self.effective_max_completion_tokens[-1]
                if self.effective_max_completion_tokens
                else None
            ),
            "blocked_reservation_tokens": self.blocked_reservation_tokens,
            "observed_provider_attempt_usage_tokens_seen": self.total_tokens,
            "elapsed_wall_clock_seconds": self._elapsed_seconds(),
            "limits": {
                "max_provider_calls": self.max_calls,
                "max_cumulative_provider_tokens": self.max_total_tokens,
                "wall_clock_seconds": self.max_wall_clock_seconds,
                "framing_reserve_tokens": self.framing_reserve_tokens,
                "default_max_completion_tokens": self.default_max_completion_tokens,
            },
            "blocking_limits": blocking_limits,
            "safety_limit_reached": bool(blocking_limits),
            "last_attempt_reached_or_crossed_limits": (
                self.last_attempt_reached_or_crossed_limits
            ),
            "blocked_requests_before_transport": self.blocked_requests,
            "pretransport_failure_reason": self.pretransport_failure_reason,
            "next_request_allowed": not blocking_limits,
            "accounting_scope": (
                "complete input/output usage from successful and failed attempts; "
                "unknown usage keeps its pre-transport reservation and censors the run"
            ),
            "enforcement_semantics": (
                "before transport, settled or held usage plus exact provider-tokenizer "
                "input, frozen framing reserve, and max completion must fit the hard cap"
            ),
        }

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _failed_provider_attempt_usage(exc: Exception) -> dict[str, Any] | None:
    usage = getattr(exc, "usage", None)
    if isinstance(usage, dict):
        return usage
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return None
    provider_attempt = details.get("provider_attempt")
    if isinstance(provider_attempt, dict) and isinstance(provider_attempt.get("usage"), dict):
        return provider_attempt["usage"]
    nested_failure = details.get("failure")
    if isinstance(nested_failure, dict):
        nested_details = nested_failure.get("details")
        if isinstance(nested_details, dict):
            provider_attempt = nested_details.get("provider_attempt")
            if isinstance(provider_attempt, dict) and isinstance(
                provider_attempt.get("usage"), dict
            ):
                return provider_attempt["usage"]
    return None


def build_guard_manifest_observation(
    guarded: GuardedLLMClient,
    outcome: dict[str, Any],
) -> dict[str, Any]:
    observation = guarded.observation_snapshot()
    if outcome.get("error_type") == "ObservationLimitExceeded" or observation[
        "blocked_requests_before_transport"
    ]:
        run_termination = (
            "runtime_finished_after_guard_blocked_request_before_transport"
            if outcome.get("completed")
            else "guard_blocked_request_before_transport"
        )
    elif observation["usage_censored"]:
        state = "finished" if outcome.get("completed") else "failed"
        run_termination = f"runtime_{state}_with_unknown_provider_usage"
    elif observation["last_attempt_reached_or_crossed_limits"]:
        state = "finished" if outcome.get("completed") else "failed"
        run_termination = (
            f"runtime_{state}_after_final_attempt_reached_or_crossed_limit"
        )
    elif outcome.get("completed"):
        run_termination = "runtime_finished_within_guard_limits"
    else:
        run_termination = "runtime_failed_without_guard_limit_termination"
    observation["run_termination"] = run_termination
    return observation


def _usage_int(usage: dict[str, Any], key: str) -> int:
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }
    for alias in aliases[key]:
        value = usage.get(alias)
        if value is not None:
            return int(value)
    if key == "total_tokens":
        return _usage_int(usage, "input_tokens") + _usage_int(usage, "output_tokens")
    return 0


def _usage_observed(usage: dict[str, Any]) -> bool:
    input_observed = any(
        usage.get(key) is not None for key in ("input_tokens", "prompt_tokens")
    )
    output_observed = any(
        usage.get(key) is not None for key in ("output_tokens", "completion_tokens")
    )
    return input_observed and output_observed


def _reasoning_tokens(usage: dict[str, Any]) -> int | None:
    details = usage.get("completion_tokens_details")
    if not isinstance(details, dict) or details.get("reasoning_tokens") is None:
        return None
    return int(details["reasoning_tokens"])


def _empty_token_totals() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _add_usage(totals: dict[str, int], usage: dict[str, Any]) -> None:
    totals["input_tokens"] += _usage_int(usage, "input_tokens")
    totals["output_tokens"] += _usage_int(usage, "output_tokens")
    totals["total_tokens"] += _usage_int(usage, "total_tokens")


def _load_events(diagnostics_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in sorted((diagnostics_dir / "task_trajectory").glob("*/events.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return sorted(events, key=lambda item: (item.get("created_at", ""), item.get("sequence", 0)))


def analyze(events: list[dict[str, Any]]) -> dict[str, Any]:
    requests: dict[str, dict[str, Any]] = {}
    failed_call_ids: set[str] = set()
    responded_call_ids: set[str] = set()
    calls: list[dict[str, Any]] = []
    failed_attempts: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") or {}
        correlation = payload.get("correlation") or {}
        call_id = str(
            payload.get("call_id")
            or correlation.get("execution_id")
            or ""
        )
        if event.get("event_type") == "llm_requested" and call_id:
            requests[call_id] = event
        elif event.get("event_type") == "llm_responded" and call_id in requests:
            responded_call_ids.add(call_id)
            request_event = requests[call_id]
            request_payload = request_event.get("payload") or {}
            request_metadata = request_payload.get("request_metadata") or request_payload
            response_metadata = payload.get("response_metadata") or payload
            usage = response_metadata.get("usage") or {}
            trace_info = request_metadata.get("trace_info") or {}
            diagnostics = trace_info.get("diagnostics") or {}
            context_selection = request_metadata.get("context_selection") or {}
            calls.append(
                {
                    "ordinal": len(calls) + 1,
                    "call_id": call_id,
                    "phase": request_event.get("phase") or request_payload.get("phase") or "",
                    "purpose": context_selection.get("request_purpose") or request_metadata.get("purpose") or "",
                    "input_tokens": _usage_int(usage, "input_tokens"),
                    "output_tokens": _usage_int(usage, "output_tokens"),
                    "reasoning_tokens": _reasoning_tokens(usage),
                    "usage_observed": _usage_observed(usage),
                    "total_tokens": _usage_int(usage, "total_tokens"),
                    "message_count": diagnostics.get("message_count"),
                    "prompt_chars": diagnostics.get("prompt_chars"),
                    "duration_ms": (response_metadata.get("provider_details") or {}).get("duration_ms"),
                    "content_length": (response_metadata.get("provider_details") or {}).get("content_length"),
                    "context_selection": context_selection,
                }
            )
        elif event.get("event_type") == "llm_failed" and call_id:
            failed_call_ids.add(call_id)
            failure_metadata = payload.get("failure") or payload
            details = failure_metadata.get("details") or {}
            provider_attempt = details.get("provider_attempt") or {}
            usage = provider_attempt.get("usage") or {}
            request_event = requests.get(call_id) or {}
            request_payload = request_event.get("payload") or {}
            request_metadata = request_payload.get("request_metadata") or request_payload
            selection = request_metadata.get("context_selection") or {}
            failed_attempts.append(
                {
                    "call_id": call_id,
                    "phase": request_event.get("phase") or event.get("phase") or "",
                    "purpose": selection.get("request_purpose")
                    or request_metadata.get("purpose")
                    or "",
                    "input_tokens": _usage_int(usage, "input_tokens"),
                    "output_tokens": _usage_int(usage, "output_tokens"),
                    "reasoning_tokens": _reasoning_tokens(usage),
                    "total_tokens": _usage_int(usage, "total_tokens"),
                    "usage_observed": _usage_observed(usage),
                    "finish_reason": provider_attempt.get("finish_reason"),
                    "response_length": provider_attempt.get("response_length"),
                }
            )

    request_series: list[dict[str, Any]] = []
    for call_id, event in requests.items():
        payload = event.get("payload") or {}
        selection = payload.get("context_selection") or {}
        diagnostics = (payload.get("trace_info") or {}).get("diagnostics") or {}
        request_series.append(
            {
                "ordinal": len(request_series) + 1,
                "call_id": call_id,
                "phase": event.get("phase") or "",
                "purpose": selection.get("request_purpose") or payload.get("purpose") or "",
                "assembled_prompt_tokens": selection.get("final_prompt_tokens"),
                "original_prompt_tokens": selection.get("original_prompt_tokens"),
                "max_prompt_tokens": selection.get("max_prompt_tokens"),
                "prompt_chars": diagnostics.get("prompt_chars"),
                "max_completion_tokens": diagnostics.get("max_tokens"),
                "status": (
                    "responded"
                    if call_id in responded_call_ids
                    else "failed"
                    if call_id in failed_call_ids
                    else "unknown"
                ),
            }
        )

    inputs = [call["input_tokens"] for call in calls]
    thirds = max(1, len(inputs) // 3)
    first_median = statistics.median(inputs[:thirds]) if inputs else 0
    last_median = statistics.median(inputs[-thirds:]) if inputs else 0
    consecutive_windows = []
    for start in range(max(0, len(inputs) - 2)):
        window = inputs[start : start + 3]
        if len(window) == 3 and window[0] < window[1] < window[2] and window[2] >= 1.5 * window[0]:
            consecutive_windows.append([start + 1, start + 3])

    by_purpose: dict[str, list[dict[str, Any]]] = {}
    for call in calls:
        by_purpose.setdefault(call["purpose"], []).append(call)
    repeated_growth = []
    output_bloat = []
    for purpose, purpose_calls in by_purpose.items():
        if len(purpose_calls) >= 3:
            first = purpose_calls[0]["input_tokens"]
            last = purpose_calls[-1]["input_tokens"]
            if first and last >= 1.5 * first:
                repeated_growth.append(purpose)
            if statistics.mean(call["output_tokens"] for call in purpose_calls) > 512:
                output_bloat.append(purpose)

    assembled_growth = []
    requests_by_purpose: dict[str, list[dict[str, Any]]] = {}
    for request in request_series:
        requests_by_purpose.setdefault(request["purpose"], []).append(request)
    for purpose, purpose_requests in requests_by_purpose.items():
        selected = [
            request["assembled_prompt_tokens"]
            for request in purpose_requests
            if request["assembled_prompt_tokens"] is not None
        ]
        if len(selected) >= 3 and selected[0] and selected[-1] >= 1.5 * selected[0]:
            assembled_growth.append(purpose)

    pressured = 0
    for call in calls:
        selection = call["context_selection"]
        final_tokens = selection.get("final_prompt_tokens")
        max_tokens = selection.get("max_prompt_tokens")
        if final_tokens is not None and max_tokens and final_tokens / max_tokens >= 0.85:
            pressured += 1

    signals = {
        "repeated_purpose_growth": repeated_growth,
        "consecutive_growth_windows": consecutive_windows,
        "late_run_amplification": bool(first_median and last_median >= 1.5 * first_median),
        "prompt_budget_pressure_count": pressured,
        "controller_output_bloat": output_bloat,
    }
    observed_outcomes = [*calls, *failed_attempts]
    reasoning_observed = [
        outcome
        for outcome in observed_outcomes
        if outcome["reasoning_tokens"] is not None
    ]
    reasoning_output_tokens = sum(
        outcome["output_tokens"] for outcome in reasoning_observed
    )
    logical_with_usage = {
        outcome["call_id"]
        for outcome in observed_outcomes
        if outcome["usage_observed"] and outcome["call_id"] in requests
    }
    responded_totals = _empty_token_totals()
    failed_attempt_totals = _empty_token_totals()
    for call in calls:
        if call["usage_observed"]:
            _add_usage(responded_totals, call)
    for attempt in failed_attempts:
        if attempt["usage_observed"]:
            _add_usage(failed_attempt_totals, attempt)
    observed_attempt_totals = {
        key: responded_totals[key] + failed_attempt_totals[key]
        for key in responded_totals
    }
    logical_request_count = len(request_series)
    secondary_signals = {
        "assembled_prompt_repeated_purpose_growth": assembled_growth,
        "reasoning_output_share": (
            sum(int(outcome["reasoning_tokens"]) for outcome in reasoning_observed)
            / reasoning_output_tokens
            if reasoning_output_tokens
            else None
        ),
    }
    return {
        "request_count": logical_request_count,
        "logical_request_count": logical_request_count,
        "aligned_call_count": len(calls),
        "responded_call_count": len(calls),
        "failed_call_count": len(failed_call_ids),
        "responded_totals": responded_totals,
        "failed_attempt_totals": failed_attempt_totals,
        "observed_attempt_totals": observed_attempt_totals,
        "usage_coverage": {
            "logical_requests": logical_request_count,
            "logical_requests_with_observed_usage": len(logical_with_usage),
            "logical_request_usage_fraction": (
                len(logical_with_usage) / logical_request_count
                if logical_request_count
                else None
            ),
            "observed_outcomes": len(observed_outcomes),
            "outcomes_with_reasoning_usage": len(reasoning_observed),
            "reasoning_usage_fraction": (
                len(reasoning_observed) / len(observed_outcomes)
                if observed_outcomes
                else None
            ),
        },
        "failed_request_completion_reservations": sum(
            int(request["max_completion_tokens"] or 0)
            for request in request_series
            if request["status"] == "failed"
        ),
        "first_third_input_median": first_median,
        "last_third_input_median": last_median,
        "inflation_signal_observed": bool(
            repeated_growth or consecutive_windows or signals["late_run_amplification"] or pressured >= 3 or output_bloat
        ),
        "signals": signals,
        "secondary_signals": secondary_signals,
        "requests": request_series,
        "calls": calls,
        "failed_attempts": failed_attempts,
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path)
    protocol_group = parser.add_mutually_exclusive_group()
    protocol_group.add_argument("--protocol", type=Path)
    protocol_group.add_argument(
        "--fixed-decomposition",
        action="store_true",
        help="select the frozen project-improvement provider-arm protocol",
    )
    parser.add_argument(
        "--improvement-requirement",
        choices=("optional", "required"),
        default="optional",
    )
    parser.add_argument(
        "--enhancement-budget-arm",
        choices=("static", "dynamic"),
        help="experiment-only Stage 7 completion-budget arm",
    )
    parser.add_argument("--max-provider-tokens", type=int)
    parser.add_argument(
        "--default-max-completion-tokens",
        type=int,
        help=(
            "provider-neutral experiment fallback for typed requests that omit "
            "max_tokens"
        ),
    )
    parser.add_argument(
        "--iteration-goal-mode",
        choices=("provider", "fixed_divide_docstring"),
        default="provider",
    )
    parser.add_argument(
        "--memory-mode",
        choices=("shared", "isolated_empty"),
        default="shared",
    )
    parser.add_argument(
        "--task-designer-context-arm",
        choices=("current", "compact"),
        help="experiment-only Stage 8 production projection policy",
    )
    parser.add_argument(
        "--task-designer-source-protocol",
        type=Path,
        help="Stage 8/9 protocol owning the verified Task Designer source",
    )
    parser.add_argument(
        "--task-designer-scenario-id",
        choices=(
            "strongly_related_diagnosis",
            "partial_shared_criterion",
            "relevant_iteration_memory",
        ),
        help="Stage 9 provider-sentinel scenario using the shared arm/source flags",
    )
    return parser


def validate_task_designer_context_args(args: Any) -> None:
    arm_supplied = bool(args.task_designer_context_arm)
    source_supplied = bool(args.task_designer_source_protocol)
    scenario_supplied = bool(args.task_designer_scenario_id)
    if arm_supplied != source_supplied or (scenario_supplied and not arm_supplied):
        raise ValueError(
            "--task-designer-context-arm, --task-designer-source-protocol, and "
            "the optional Stage 9 --task-designer-scenario-id must be supplied together"
        )


def load_task_designer_context_scope(args: Any, agent: Any) -> Any:
    """Load the Stage 8 frozen source or Stage 9 live-fact overlay scope."""

    validate_task_designer_context_args(args)
    if not args.task_designer_context_arm:
        return nullcontext(None)
    source_path = Path(args.task_designer_source_protocol)
    source_protocol = json.loads(source_path.read_text(encoding="utf-8"))
    campaign_id = str(source_protocol.get("campaign_id") or "")
    if campaign_id == "stage8-task-designer-context-projection-ab-v1":
        if args.task_designer_scenario_id:
            raise ValueError("Stage 8 Task Designer source does not accept a scenario id")
        from stage8_task_designer_context_campaign import (
            load_frozen_task_designer_source,
            task_designer_context_scope,
            validate_campaign_protocol as validate_stage8_protocol,
        )

        validate_stage8_protocol(source_protocol)
        frozen_source = load_frozen_task_designer_source(source_protocol)
        return task_designer_context_scope(
            args.task_designer_context_arm,
            frozen_source,
            agent=agent,
        )
    if campaign_id == "stage9-task-designer-provider-sentinel-v1":
        if not args.task_designer_scenario_id:
            raise ValueError(
                "Stage 9 --task-designer-scenario-id must be supplied together "
                "with the shared arm/source arguments"
            )
        from stage9_task_designer_provider_sentinel import (
            task_designer_scenario_scope,
            validate_protocol as validate_stage9_protocol,
        )
        from stage9_task_designer_scenario_gate import build_scenario_fixtures

        validate_stage9_protocol(source_protocol)
        fixture = build_scenario_fixtures()[args.task_designer_scenario_id]
        return task_designer_scenario_scope(
            args.task_designer_context_arm,
            fixture,
            agent=agent,
            protocol=source_protocol,
        )
    if campaign_id == "stage9-task-designer-paired-shadow-v2":
        if not args.task_designer_scenario_id:
            raise ValueError(
                "Stage 9 V2 --task-designer-scenario-id must be supplied together "
                "with the shared production-policy/source arguments"
            )
        from stage9_task_designer_paired_shadow import (
            paired_task_designer_scenario_scope,
            validate_protocol as validate_paired_shadow_protocol,
        )
        from stage9_task_designer_scenario_gate import build_scenario_fixtures

        validate_paired_shadow_protocol(source_protocol)
        fixture = build_scenario_fixtures()[args.task_designer_scenario_id]
        return paired_task_designer_scenario_scope(
            args.task_designer_context_arm,
            fixture,
            agent=agent,
            protocol=source_protocol,
        )
    raise ValueError(f"unsupported Task Designer source protocol: {campaign_id!r}")


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    validate_task_designer_context_args(args)
    selected_protocol_path = resolve_protocol_path(
        protocol_path=args.protocol,
        fixed_decomposition=args.fixed_decomposition,
    )
    protocol = json.loads(selected_protocol_path.read_text(encoding="utf-8"))
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (args.output_dir or ROOT / "runs" / timestamp).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    project_dir = Path(tempfile.mkdtemp(prefix="openpilot-context-observe-"))
    shutil.copytree(
        ROOT / "fixtures" / "calculator_project",
        project_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
    )
    project_snapshot_before = capture_bounded_project_snapshot(project_dir)

    settings = LLMSettings()
    provider_token_counter = ProviderTokenCounter.from_settings(settings)
    guarded = GuardedLLMClient(
        LLMClient(settings, enable_cache=False),
        max_calls=protocol["safety_limits"]["max_provider_calls"],
        max_total_tokens=min(
            protocol["safety_limits"]["max_cumulative_provider_tokens"],
            args.max_provider_tokens
            if args.max_provider_tokens is not None
            else protocol["safety_limits"]["max_cumulative_provider_tokens"],
        ),
        max_wall_clock_seconds=protocol["safety_limits"]["wall_clock_seconds"],
        default_max_completion_tokens=(
            args.default_max_completion_tokens
            if args.default_max_completion_tokens is not None
            else protocol["safety_limits"].get("default_max_completion_tokens")
        ),
        token_counter=provider_token_counter,
        framing_reserve_tokens=EXPERIMENT_PROVIDER_FRAMING_RESERVE_TOKENS,
    )
    recorder = DiagnosticRecorder(output_dir / "diagnostics")
    hooks = RuntimeDiagnosticsHooks(recorder)
    with experiment_memory_scope(args.memory_mode, output_dir) as memory_intervention:
        autopilot = IntelligentAutopilot(
            llm_client=guarded,
            auto_approve=True,
            use_enhanced_ui=False,
            enable_iterative_improvement=True,
            required_successful_improvements=1,
            project_improvement_policy=ProjectImprovementPolicy(
                requirement=ProjectImprovementRequirement(args.improvement_requirement),
                source=ProjectImprovementPolicySource.RUNTIME_CONFIG,
                target_successes=1,
                max_attempts=3,
            ),
            prompt_for_project_improvement_iterations=False,
            runtime_diagnostics_hooks=hooks,
        )
    enhancement_budget_arm = configure_enhancement_budget_arm(
        autopilot,
        args.enhancement_budget_arm,
        iteration_goal_mode=args.iteration_goal_mode,
    )
    fixed_decomposition = apply_protocol_intervention(autopilot, protocol)
    task_designer_context_intervention: dict[str, Any] | None = None
    task_designer_scope = load_task_designer_context_scope(
        args, autopilot.iterative_improvement
    )
    context = {
        "task_id": protocol["sample"]["task_id"],
        "source": "full_architecture_context_observation_v1",
        "project_path": str(project_dir),
        "checkpointing_enabled": False,
    }
    outcome: dict[str, Any]
    with task_designer_scope as task_designer_context_intervention:
        try:
            result = autopilot.execute(protocol["task_goal"], context=context)
            outcome = {"completed": True, "result": result}
        except Exception as exc:
            outcome = {"completed": False, "error_type": type(exc).__name__, "error": str(exc)}

    project_snapshot_after = capture_bounded_project_snapshot(project_dir)
    observed_project_mutations = diff_project_snapshots(
        project_snapshot_before, project_snapshot_after
    )
    observed_enhancement_mutations = build_enhancement_mutation_observation(
        task_designer_context_intervention,
        project_snapshot_after,
    )

    events = _load_events(output_dir / "diagnostics")
    analysis = analyze(events)
    upstream_gate = evaluate_upstream_gate(events)
    improvement_cost = collect_improvement_cost(events, upstream_gate)
    analysis["upstream_gate"] = upstream_gate
    analysis["improvement_cost"] = improvement_cost
    manifest = {
        "protocol_id": protocol["protocol_id"],
        "started_output_timestamp": timestamp,
        "model": settings.model,
        "provider": settings.provider,
        "provider_runtime_identity": provider_runtime_identity(settings),
        "project_improvement_policy": autopilot.project_improvement_policy.model_dump(mode="json"),
        "sample_status": upstream_gate["status"],
        "censored": upstream_gate["censored"],
        "project_dir": str(project_dir),
        "memory_intervention": memory_intervention,
        "guard_observation": build_guard_manifest_observation(guarded, outcome),
        "outcome": outcome,
        "observed_project_mutations": observed_project_mutations,
        "observed_enhancement_mutations": observed_enhancement_mutations,
    }
    if fixed_decomposition is not None:
        manifest["fixed_decomposition"] = fixed_decomposition
    if enhancement_budget_arm is not None:
        manifest["enhancement_budget_arm"] = enhancement_budget_arm
        effective_budget = autopilot._enhancement_runtime_budget()
        manifest["effective_enhancement_completion_policy"] = (
            effective_budget.enhancement_completion_policy.model_dump(mode="json")
        )
    if task_designer_context_intervention is not None:
        manifest["task_designer_context_intervention"] = (
            task_designer_context_intervention
        )
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (output_dir / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), **analysis}, ensure_ascii=False, indent=2))
    if upstream_gate["status"] != "valid":
        return 3
    return 0 if outcome["completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
