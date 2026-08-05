"""Bounded Goal Maker reasoning-complexity canary.

This experiment isolates reasoning routing from context and completion-budget
effects.  Both arms reuse one immutable candidate list, one JSON schema and
one completion reservation complexity; only the provider-neutral
``ReasoningDecisionComplexity`` changes.  The default is dry-run and no arm
may mutate a project or memory store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from autonomous_iteration.agents.iteration_agent import AutonomousIterationAgent
from autonomous_iteration.models import ProjectStateSnapshot
from autonomous_iteration.project_improvement_context import build_iteration_goal_candidates
from core.config import LLMSettings
from core.llm import LLMClient
from core.reasoning import resolve_reasoning_policy
from core.token_counting import ProviderTokenCounter
from memory.session_dialog import session_turn_ledger_hash
from metadata import (
    ContextRequestPurpose,
    EnhancementCompletionComplexity,
    EnhancementCompletionRequirement,
    ReasoningDecisionComplexity,
)
from stage12_session_compact_canary_admission import run_no_provider_preflight
from stage13_session_compact_pipeline_offline import _improvement_report, _ingress_state, _project_state


class GoalReasoningCanaryStop(RuntimeError):
    """The isolated reasoning canary stopped fail closed."""


class GoalArmReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arm: ReasoningDecisionComplexity
    pair_index: int = Field(default=0, ge=0)
    source_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reasoning_mode: str
    effective_reasoning_mode: str | None = None
    reasoning_resolution: str | None = None
    reasoning_profile_id: str | None = None
    reasoning_profile_version: str | None = None
    max_tokens: int = Field(ge=1)
    rendered_input_tokens: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    finish_reason: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    json_valid: bool = False
    goal_valid: bool = False
    error_type: str | None = None
    error: str | None = None
    attempt_usage: list[dict[str, int | None]] = Field(default_factory=list)
    attempt_finish_reasons: list[str | None] = Field(default_factory=list)
    attempt_error_types: list[str | None] = Field(default_factory=list)


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _usage(response: Any) -> dict[str, int | None]:
    raw = getattr(response, "usage", None)
    if not isinstance(raw, Mapping):
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None, "reasoning_tokens": None}
    details = raw.get("completion_tokens_details")
    return {
        "input_tokens": _int_or_none(raw.get("prompt_tokens", raw.get("input_tokens"))),
        "output_tokens": _int_or_none(raw.get("completion_tokens", raw.get("output_tokens"))),
        "total_tokens": _int_or_none(raw.get("total_tokens")),
        "reasoning_tokens": (
            _int_or_none(details.get("reasoning_tokens")) if isinstance(details, Mapping) else None
        ),
    }


def _usage_from_exception(exc: Exception) -> dict[str, int | None]:
    raw = getattr(exc, "usage", None)
    if isinstance(raw, Mapping):
        details = raw.get("completion_tokens_details")
        return {
            "input_tokens": _int_or_none(raw.get("prompt_tokens", raw.get("input_tokens"))),
            "output_tokens": _int_or_none(raw.get("completion_tokens", raw.get("output_tokens"))),
            "total_tokens": _int_or_none(raw.get("total_tokens")),
            "reasoning_tokens": (
                _int_or_none(details.get("reasoning_tokens")) if isinstance(details, Mapping) else None
            ),
        }
    return {"input_tokens": None, "output_tokens": None, "total_tokens": None, "reasoning_tokens": None}


def _int_or_none(value: Any) -> int | None:
    return int(value) if value is not None else None


class _Evaluator:
    llm_client = None


class _RecordingClient:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.settings = client.settings
        self.requests: list[Any] = []
        self.responses: list[Any] = []
        self.attempts: list[dict[str, Any]] = []

    def complete(self, request: Any, **kwargs: Any) -> Any:
        self.requests.append(request)
        try:
            response = self.client.complete(request, **kwargs)
        except Exception as exc:
            self.attempts.append({"response": None, "error": exc})
            raise
        self.attempts.append({"response": response, "error": None})
        self.responses.append(response)
        return response


def _goal_valid(payload: Any) -> bool:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("goals"), list):
        return False
    return bool(
        payload["goals"]
        and isinstance(payload["goals"][0], Mapping)
        and str(payload["goals"][0].get("title") or "").strip()
        and isinstance(payload["goals"][0].get("acceptance_criteria"), list)
        and payload["goals"][0]["acceptance_criteria"]
    )


def _run_arm(
    *,
    client: Any,
    candidates: list[Any],
    source_snapshot_hash: str,
    arm: ReasoningDecisionComplexity,
) -> GoalArmReceipt:
    recorder = _RecordingClient(client)
    agent = AutonomousIterationAgent(
        _Evaluator(),
        llm_client=recorder,
        max_iteration_attempts=1,
        enhancement_requirement=EnhancementCompletionRequirement.REQUIRED,
    )
    request: Any | None = None

    def attempt_telemetry() -> dict[str, list[Any]]:
        usages: list[dict[str, int | None]] = []
        finishes: list[str | None] = []
        errors: list[str | None] = []
        for attempt in recorder.attempts:
            response = attempt.get("response")
            error = attempt.get("error")
            usages.append(_usage(response) if response is not None else _usage_from_exception(error))
            finishes.append(
                getattr(response, "finish_reason", None)
                if response is not None
                else getattr(error, "finish_reason", None)
            )
            errors.append(type(error).__name__ if error is not None else None)
        return {"usages": usages, "finishes": finishes, "errors": errors}

    def policy_telemetry(request: Any | None) -> dict[str, str | None]:
        if request is None:
            return {
                "effective_reasoning_mode": None,
                "reasoning_resolution": None,
                "reasoning_profile_id": None,
                "reasoning_profile_version": None,
            }
        resolved = resolve_reasoning_policy(request.reasoning_policy, recorder.settings)
        return {
            "effective_reasoning_mode": resolved.effective_mode.value,
            "reasoning_resolution": resolved.resolution.value,
            "reasoning_profile_id": resolved.profile_id.value,
            "reasoning_profile_version": resolved.profile_version,
        }

    try:
        initial_request: Any | None = None
        payload, _ = agent._complete_json_candidates(
            candidates,
            purpose=ContextRequestPurpose.ITERATION_GOAL,
            # Freeze completion reservation complexity: this is a reasoning
            # treatment, not a completion-budget treatment.
            complexity=EnhancementCompletionComplexity.STANDARD,
            reasoning_complexity=arm,
            remaining_calls=1,
        )
        initial_request = recorder.requests[0]
        request = initial_request
        response = recorder.responses[-1]
        telemetry = attempt_telemetry()
        policy = policy_telemetry(initial_request)
        usage = _usage(response)
        return GoalArmReceipt(
            arm=arm,
            source_snapshot_hash=source_snapshot_hash,
            context_request_hash=_hash(request.model_dump(mode="json")),
            reasoning_mode=request.reasoning_policy.mode.value,
            max_tokens=int(request.max_tokens or 0),
            rendered_input_tokens=int(getattr(request.context_selection, "final_prompt_tokens", 0) or 0),
            **usage,
            finish_reason=getattr(response, "finish_reason", None),
            attempt_count=len(recorder.requests),
            json_valid=isinstance(payload, Mapping),
            goal_valid=_goal_valid(payload),
            **policy,
            attempt_usage=telemetry["usages"],
            attempt_finish_reasons=telemetry["finishes"],
            attempt_error_types=telemetry["errors"],
        )
    except Exception as exc:
        # The initial request is the frozen treatment. A later recovery may
        # have a larger ceiling, but it must not redefine the A/B arm budget.
        request = recorder.requests[0] if recorder.requests else None
        response = recorder.responses[-1] if recorder.responses else None
        telemetry = attempt_telemetry()
        policy = policy_telemetry(request)
        usage = _usage(response) if response is not None else (
            telemetry["usages"][0] if telemetry["usages"] else _usage_from_exception(exc)
        )
        return GoalArmReceipt(
            arm=arm,
            source_snapshot_hash=source_snapshot_hash,
            context_request_hash=_hash(request.model_dump(mode="json")) if request is not None else _hash({"arm": arm.value}),
            reasoning_mode=(request.reasoning_policy.mode.value if request is not None else "unknown"),
            max_tokens=int(request.max_tokens or 0) if request is not None else 0,
            rendered_input_tokens=int(getattr(request.context_selection, "final_prompt_tokens", 0) or 0) if request is not None else 0,
            **usage,
            finish_reason=getattr(exc, "finish_reason", None),
            attempt_count=len(recorder.requests),
            error_type=type(exc).__name__,
            error=str(exc)[:500],
            **policy,
            attempt_usage=telemetry["usages"],
            attempt_finish_reasons=telemetry["finishes"],
            attempt_error_types=telemetry["errors"],
        )


def run_goal_reasoning_canary(*, execute_provider: bool = False, result_path: Path | None = None) -> dict[str, Any]:
    admission = run_no_provider_preflight()
    if not execute_provider:
        return {
            "status": "stopped",
            "claim_boundary": "goal_reasoning_complexity_only",
            "provider_execution_admitted": False,
            "provider_calls": 0,
            "stop_reasons": ["provider_execution_not_explicitly_enabled"],
            "admission": admission,
        }
    if admission.get("status") != "passed" or admission.get("controls_admitted") is not True:
        raise GoalReasoningCanaryStop("preflight controls are not admitted")
    if result_path is None:
        raise GoalReasoningCanaryStop("result_path_required_for_provider_execution")

    settings = LLMSettings()
    if not settings.is_ready():
        raise GoalReasoningCanaryStop("provider_settings_not_ready")
    client = LLMClient(settings, enable_cache=False)
    counter = ProviderTokenCounter.from_settings(settings)
    if not counter.available:
        raise GoalReasoningCanaryStop("exact_provider_tokenizer_unavailable")

    with tempfile.TemporaryDirectory(prefix="openpilot-stage7e-goal-") as raw_root:
        root = Path(raw_root).resolve()
        project_root = root / "project"
        project_root.mkdir()
        (project_root / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (project_root / "README.md").write_text("Run `python -m pytest -q`.\n", encoding="utf-8")
        ingress = _ingress_state(str(project_root))
        project_state: ProjectStateSnapshot = _project_state(str(project_root))
        report = _improvement_report()
        candidates = build_iteration_goal_candidates(
            project_state=project_state,
            improvement_report=report,
            completed_iteration=0,
            session_constraints=ingress.session_constraints,
            session_ingress_state=ingress,
        )
        source_snapshot_hash = _hash({
            "ingress": ingress.model_dump(mode="json"),
            "project_state": project_state.model_dump(mode="json"),
            "report": report,
            "candidate_ids": [candidate.candidate_id for candidate in candidates],
        })
        arms = [
            _run_arm(client=client, candidates=candidates, source_snapshot_hash=source_snapshot_hash, arm=arm)
            for arm in (ReasoningDecisionComplexity.ROUTINE, ReasoningDecisionComplexity.STANDARD)
        ]
        if arms[0].max_tokens != arms[1].max_tokens:
            raise GoalReasoningCanaryStop("reasoning treatment changed completion reservation")
        result = {
            "status": "passed" if all(item.json_valid and item.goal_valid and item.finish_reason == "stop" for item in arms) else "stopped",
            "claim_boundary": "goal_reasoning_complexity_only",
            "provider_execution_admitted": True,
            "provider_calls": sum(item.attempt_count for item in arms),
            "network_calls": sum(item.attempt_count for item in arms),
            "project_mutations": 0,
            "memory_mutations": 0,
            "source_snapshot_hash": source_snapshot_hash,
            "admission": admission,
            "arms": [item.model_dump(mode="json") for item in arms],
            "stop_reasons": [] if all(item.json_valid and item.goal_valid and item.finish_reason == "stop" for item in arms) else ["goal_quality_gate_failed"],
        }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def run_interleaved_goal_reasoning_pairs(
    *,
    execute_provider: bool = False,
    result_path: Path | None = None,
    pair_count: int = 3,
    client: Any | None = None,
) -> dict[str, Any]:
    """Run interleaved pairs under a bounded-at-most-one-recovery cap.

    Each pair normally uses two calls.  A provider ``length`` response may
    trigger one bounded recovery in the existing completion policy, so the
    experiment admits at most three calls per pair and records that recovery
    rather than silently treating it as a new treatment budget. The token cap
    is computed from every attempt's usage, not only the final receipt.
    """

    if pair_count < 1:
        raise GoalReasoningCanaryStop("pair_count must be positive")
    admission = run_no_provider_preflight()
    if not execute_provider:
        return {
            "status": "stopped",
            "claim_boundary": "goal_reasoning_complexity_interleaved_pairs",
            "provider_execution_admitted": False,
            "provider_calls": 0,
            "stop_reasons": ["provider_execution_not_explicitly_enabled"],
            "admission": admission,
        }
    if admission.get("status") != "passed" or admission.get("controls_admitted") is not True:
        raise GoalReasoningCanaryStop("preflight controls are not admitted")
    if result_path is None:
        raise GoalReasoningCanaryStop("result_path_required_for_provider_execution")

    provider = client
    settings = getattr(provider, "settings", None) if provider is not None else LLMSettings()
    if provider is None:
        if not settings.is_ready():
            raise GoalReasoningCanaryStop("provider_settings_not_ready")
        provider = LLMClient(settings, enable_cache=False)
    counter = ProviderTokenCounter.from_settings(getattr(provider, "settings", settings))
    if client is None and not counter.available:
        raise GoalReasoningCanaryStop("exact_provider_tokenizer_unavailable")

    with tempfile.TemporaryDirectory(prefix="openpilot-stage7f-goal-") as raw_root:
        root = Path(raw_root).resolve()
        project_root = root / "project"
        project_root.mkdir()
        (project_root / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (project_root / "README.md").write_text("Run `python -m pytest -q`.\n", encoding="utf-8")
        ingress = _ingress_state(str(project_root))
        project_state: ProjectStateSnapshot = _project_state(str(project_root))
        report = _improvement_report()
        candidates = build_iteration_goal_candidates(
            project_state=project_state,
            improvement_report=report,
            completed_iteration=0,
            session_constraints=ingress.session_constraints,
            session_ingress_state=ingress,
        )
        source_snapshot_hash = _hash({
            "ingress": ingress.model_dump(mode="json"),
            "project_state": project_state.model_dump(mode="json"),
            "report": report,
            "candidate_ids": [candidate.candidate_id for candidate in candidates],
        })
        order = [
            arm
            for _ in range(pair_count)
            for arm in (ReasoningDecisionComplexity.ROUTINE, ReasoningDecisionComplexity.STANDARD)
        ]
        receipts: list[GoalArmReceipt] = []
        for ordinal, arm in enumerate(order, start=1):
            receipt = _run_arm(
                client=provider,
                candidates=candidates,
                source_snapshot_hash=source_snapshot_hash,
                arm=arm,
            ).model_copy(update={"pair_index": (ordinal + 1) // 2})
            receipts.append(receipt)
            if sum(item.attempt_count for item in receipts) > 3 * pair_count:
                raise GoalReasoningCanaryStop("interleaved reasoning call cap exceeded")
            attempt_totals = [
                usage.get("total_tokens")
                for item in receipts
                for usage in item.attempt_usage
            ]
            if any(value is None for value in attempt_totals):
                raise GoalReasoningCanaryStop("interleaved reasoning usage unknown")
            if sum(int(value) for value in attempt_totals) > 30_000:
                raise GoalReasoningCanaryStop("interleaved reasoning token cap exceeded")

        pairs = []
        for pair_index in range(1, pair_count + 1):
            pair = [item for item in receipts if item.pair_index == pair_index]
            if len(pair) != 2 or pair[0].max_tokens != pair[1].max_tokens:
                raise GoalReasoningCanaryStop("pair completion reservation mismatch")
            pairs.append({
                "pair_index": pair_index,
                "initial_max_tokens": pair[0].max_tokens,
                "source_snapshot_hash": source_snapshot_hash,
                "routine": pair[0].model_dump(mode="json"),
                "standard": pair[1].model_dump(mode="json"),
                "routine_quality": pair[0].json_valid and pair[0].goal_valid and pair[0].finish_reason == "stop",
                "standard_quality": pair[1].json_valid and pair[1].goal_valid and pair[1].finish_reason == "stop",
            })
        routine_quality = all(pair["routine_quality"] for pair in pairs)
        standard_quality = all(pair["standard_quality"] for pair in pairs)
        stop_reasons = []
        if not routine_quality:
            stop_reasons.append("routine_quality_gate_failed")
        if not standard_quality:
            stop_reasons.append("standard_quality_gate_failed")
        def aggregate_field(field: str) -> int | None:
            values = [
                usage.get(field)
                for item in receipts
                for usage in item.attempt_usage
            ]
            return sum(int(value) for value in values) if values and all(value is not None for value in values) else None

        aggregate_usage = {
            field: aggregate_field(field)
            for field in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens")
        }
        result = {
            "status": "passed" if routine_quality and standard_quality else "stopped",
            "claim_boundary": "goal_reasoning_complexity_interleaved_pairs",
            "provider_execution_admitted": True,
            "provider_calls": sum(item.attempt_count for item in receipts),
            "network_calls": sum(item.attempt_count for item in receipts),
            "project_mutations": 0,
            "memory_mutations": 0,
            "source_snapshot_hash": source_snapshot_hash,
            "session_turn_source_hash": session_turn_ledger_hash(ingress),
            "session_constraints_hash": ingress.session_constraints.canonical_hash,
            "attempt_total_tokens": aggregate_usage["total_tokens"],
            "attempt_input_tokens": aggregate_usage["input_tokens"],
            "attempt_output_tokens": aggregate_usage["output_tokens"],
            "attempt_reasoning_tokens": aggregate_usage["reasoning_tokens"],
            "attempt_token_cap": 30_000,
            "admission": admission,
            "pairs": pairs,
            "stop_reasons": stop_reasons,
        }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-provider", action="store_true")
    parser.add_argument("--result-path", type=Path, required=False)
    args = parser.parse_args()
    result = run_goal_reasoning_canary(
        execute_provider=args.execute_provider,
        result_path=args.result_path,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
