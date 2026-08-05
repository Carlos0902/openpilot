"""Stage 5B-3c opt-in, two-request Task Designer Provider canary.

The default is a dry run.  ``execute_provider=True`` is the only path that
transports requests, and it is bounded to one compact primary request and one
current control/fallback request from the same typed source snapshot.  The
full execute/runtime/checkpoint admission is run before transport, while the
Provider boundary is the production context candidate/request builder.  No
project mutation or downstream task execution is permitted here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from autonomous_iteration.models import ImprovementGoal, ProjectStateSnapshot
from autonomous_iteration.project_improvement_context import build_iteration_task_design_candidates
from core.config import LLMSettings
from core.exceptions import ContextAssemblyBudgetError, ContextAssemblyGovernanceError
from core.llm import LLMClient, LLMResponse
from core.token_counting import ProviderTokenCounter, default_deepseek_tokenizer_path
from memory.context_assembly.request_builder import build_context_candidate_request
from metadata import ContextRequestPurpose
from stage12_session_compact_canary_admission import (
    AdmissionError,
    CanaryCampaignLedger,
    DEFAULT_PROTOCOL_PATH,
    ProjectionFeatureFlag,
    ProjectionPolicy,
    RuntimeKillSwitch,
    SessionCompactCanaryAdmission,
    UsageObservation,
    UsageRoute,
    run_no_provider_preflight,
)
from stage13_session_compact_pipeline_offline import (
    _hash as _source_hash,
    _improvement_report,
    _ingress_state,
    _project_state,
)
from stage14_full_entry_admission import run_full_entry_admission


class ProviderCanaryStop(RuntimeError):
    """The opt-in Provider canary stopped fail closed."""


class _ProbeClient:
    """Settings-only client for dry-run request assembly."""

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings or SimpleNamespace(
            provider="offline_probe",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            tokenizer_path=str(default_deepseek_tokenizer_path()),
            context_max_prompt_tokens=4096,
            context_reserved_prompt_tokens=128,
        )

    def complete(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ProviderCanaryStop("Provider transport is disabled in dry-run")


def _usage_value(usage: Mapping[str, Any], key: str) -> int | None:
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }[key]
    for alias in aliases:
        value = usage.get(alias)
        if value is not None:
            return int(value)
    if key == "total_tokens":
        input_tokens = _usage_value(usage, "input_tokens")
        output_tokens = _usage_value(usage, "output_tokens")
        if input_tokens is not None and output_tokens is not None:
            return input_tokens + output_tokens
    return None


def _observed_usage(usage: Any) -> dict[str, int] | None:
    if not isinstance(usage, Mapping):
        return None
    input_tokens = _usage_value(usage, "input_tokens")
    output_tokens = _usage_value(usage, "output_tokens")
    total_tokens = _usage_value(usage, "total_tokens")
    if input_tokens is None or output_tokens is None or total_tokens is None:
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _exception_usage(exc: Exception) -> dict[str, Any] | None:
    usage = getattr(exc, "usage", None)
    if isinstance(usage, Mapping):
        return dict(usage)
    details = getattr(exc, "details", None)
    if isinstance(details, Mapping):
        attempt = details.get("provider_attempt")
        if isinstance(attempt, Mapping) and isinstance(attempt.get("usage"), Mapping):
            return dict(attempt["usage"])
    return None


def _reasoning_tokens(usage: Mapping[str, Any]) -> int | None:
    details = usage.get("completion_tokens_details")
    if isinstance(details, Mapping) and details.get("reasoning_tokens") is not None:
        return int(details["reasoning_tokens"])
    return None


def _failure_evidence(exc: Exception) -> dict[str, Any]:
    """Keep bounded failure evidence without copying provider secrets."""

    evidence: dict[str, Any] = {
        "error_type": type(exc).__name__,
        "message": str(exc)[:500],
    }
    for name in ("finish_reason", "response_text"):
        value = getattr(exc, name, None)
        if value is not None:
            evidence[name] = str(value)[:500]
    details = getattr(exc, "details", None)
    exception_usage = _observed_usage(getattr(exc, "usage", None))
    if exception_usage is not None:
        evidence["observed_usage"] = exception_usage
    if isinstance(details, Mapping):
        safe_details: dict[str, Any] = {}
        for key in (
            "finish_reason",
            "response_text",
            "response_preview",
            "json_repair_attempts",
            "provider_attempt",
        ):
            value = details.get(key)
            if value is not None:
                if key == "provider_attempt" and isinstance(value, Mapping):
                    safe_details[key] = {
                        subkey: value.get(subkey)
                        for subkey in ("finish_reason", "usage", "model")
                        if value.get(subkey) is not None
                    }
                else:
                    safe_details[key] = str(value)[:500]
        if safe_details:
            evidence["details"] = safe_details
    return evidence


def _quality_check(response: Any, state: Any) -> tuple[bool, str | None, dict[str, Any]]:
    payload = getattr(response, "parsed_json", None)
    task = payload.get("task") if isinstance(payload, Mapping) else None
    if not isinstance(task, Mapping):
        return False, "task_schema_missing", {"parsed_json": payload}
    target_files = task.get("target_files")
    if not isinstance(target_files, list) or not target_files:
        return False, "target_files_missing", {"task": dict(task)}
    allowed = {
        Path(str(path)).name
        for entry in state.session_constraints.active_entries
        if entry.constraint_key == "write_scope"
        for path in entry.value.allowed_files
    }
    observed_targets = {Path(str(path)).name for path in target_files}
    if allowed and not observed_targets.issubset(allowed):
        return False, "target_files_outside_active_write_scope", {"task": dict(task), "allowed": sorted(allowed)}
    return True, None, {"task": dict(task)}


def _load_campaign_ledger(
    path: Path,
    contract: SessionCompactCanaryAdmission,
) -> CanaryCampaignLedger:
    if not path.exists():
        return CanaryCampaignLedger(
            accounting=contract.usage_accounting,
            hard_caps=contract.hard_caps,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        ledger = CanaryCampaignLedger.model_validate(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProviderCanaryStop(f"campaign state is invalid: {exc}") from exc
    if ledger.accounting != contract.usage_accounting or ledger.hard_caps != contract.hard_caps:
        raise ProviderCanaryStop("campaign state contract does not match admission")
    return ledger


def _save_campaign_ledger(path: Path, ledger: CanaryCampaignLedger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(ledger.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _settings_ready(settings: Any) -> bool:
    checker = getattr(settings, "is_ready", None)
    if callable(checker):
        return bool(checker())
    return bool(str(getattr(settings, "api_key", "") or "").strip()) and bool(
        str(getattr(settings, "base_url", "") or "").strip()
    )


def run_task_designer_provider_canary(
    *,
    execute_provider: bool = False,
    feature_flag: ProjectionFeatureFlag = ProjectionFeatureFlag.CANARY_ENABLED,
    kill_switch: RuntimeKillSwitch = RuntimeKillSwitch.ARMED,
    client: Any | None = None,
    campaign_state_path: Path | None = None,
    # Match the existing typed enhancement policy ceiling for
    # ``iteration_task_design``; the canary must not introduce a smaller
    # completion budget that would confound Provider-default reasoning.
    completion_reserve_tokens: int = 2200,
    # Match the existing observation guard's provider framing reserve.
    framing_reserve_tokens: int = 128,
) -> dict[str, Any]:
    """Run a bounded compact/current Task Designer pair."""

    if feature_flag != ProjectionFeatureFlag.CANARY_ENABLED:
        return {"status": "stopped", "stop_reasons": ["projection_feature_flag_disabled"], "provider_calls": 0}
    if kill_switch != RuntimeKillSwitch.ARMED:
        return {"status": "stopped", "stop_reasons": ["runtime_kill_switch_engaged"], "provider_calls": 0}
    admission = run_no_provider_preflight()
    if not execute_provider:
        return {
            "status": "stopped",
            "claim_boundary": "task_designer_provider_canary_dry_run",
            "provider_execution_admitted": False,
            "transport_attempted": False,
            "admission": admission,
            "stop_reasons": ["provider_execution_not_explicitly_enabled"],
            "provider_calls": 0,
        }
    if campaign_state_path is None:
        return {
            "status": "stopped",
            "claim_boundary": "task_designer_provider_canary",
            "provider_execution_admitted": False,
            "transport_attempted": False,
            "admission": admission,
            "stop_reasons": ["campaign_state_path_required"],
            "provider_calls": 0,
        }

    settings = getattr(client, "settings", None) if client is not None else LLMSettings()
    if not _settings_ready(settings):
        return {
            "status": "stopped",
            "claim_boundary": "task_designer_provider_canary",
            "provider_execution_admitted": False,
            "transport_attempted": False,
            "admission": admission,
            "stop_reasons": ["provider_settings_not_ready"],
            "provider_calls": 0,
        }
    provider = client or LLMClient(settings, enable_cache=False)
    counter = ProviderTokenCounter.from_settings(settings)
    if not counter.available:
        raise ProviderCanaryStop("exact provider tokenizer is unavailable")
    contract = SessionCompactCanaryAdmission.model_validate_json(
        DEFAULT_PROTOCOL_PATH.read_text(encoding="utf-8")
    )

    with tempfile.TemporaryDirectory(prefix="openpilot-stage5b-provider-") as raw_root:
        root = Path(raw_root).resolve()
        project_root = root / "project"
        project_root.mkdir()
        ingress = _ingress_state(str(project_root))
        project_state = _project_state(str(project_root))
        goal = ImprovementGoal(
            id="document-workflow",
            title="Document the exact validation workflow.",
            category="documentation",
            rationale="Users need a reproducible verification path without changing calculator behavior.",
            acceptance_criteria=[
                "README contains the exact pytest command.",
                "Existing calculator behavior remains unchanged.",
            ],
            priority="high",
        )
        report = _improvement_report()
        source_hash = _source_hash(
            {
                "project_state": project_state.model_dump(mode="json"),
                "goal": goal.model_dump(mode="json"),
                "report": report,
                "session_constraints": ingress.session_constraints.model_dump(mode="json"),
            }
        )
        full_entry = run_full_entry_admission()
        if full_entry.get("status") != "passed":
            raise ProviderCanaryStop("full execute/runtime admission did not pass")
        ledger = _load_campaign_ledger(campaign_state_path, contract)
        observations: list[dict[str, Any]] = []
        stop_reasons: list[str] = []
        fallback_used = False
        for arm in (ProjectionPolicy.COMPACT, ProjectionPolicy.CURRENT):
            route = UsageRoute.PRIMARY if arm == ProjectionPolicy.COMPACT else UsageRoute.FALLBACK
            candidates = build_iteration_task_design_candidates(
                project_state=project_state,
                goal=goal,
                improvement_report=report,
                completed_iteration=0,
                projection_policy=arm.value,
                session_constraints=ingress.session_constraints,
            )
            request = build_context_candidate_request(
                _ProbeClient(settings),
                candidates=candidates,
                purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
                response_format="json_object",
                max_tokens=int(completion_reserve_tokens),
                transport_retries=0,
                trace_info={
                    "stage": "stage5b-3c-task-designer-provider-canary",
                    "projection_policy": arm.value,
                    "source_snapshot_hash": source_hash,
                },
            )
            selection = request.context_selection
            rendered_tokens = int(getattr(selection, "final_prompt_tokens", 0) or 0)
            request_tokens = sum(counter.count_text(message.content) for message in request.messages)
            reserved_tokens = request_tokens + int(framing_reserve_tokens) + int(request.max_tokens or 0)
            execution_id = f"stage5b3c:{uuid.uuid4().hex}"
            ledger = ledger.reserve(
                execution_id=execution_id,
                arm=arm,
                route=route,
                reserved_tokens=reserved_tokens,
            )
            _save_campaign_ledger(campaign_state_path, ledger)
            started = time.monotonic()
            provider_response: Any | None = None
            observed_provider_usage: dict[str, int] | None = None
            record: dict[str, Any] = {
                "execution_id": execution_id,
                "arm": arm.value,
                "route": route.value,
                "purpose": ContextRequestPurpose.ITERATION_TASK_DESIGN.value,
                "source_snapshot_hash": source_hash,
                "rendered_input_tokens": rendered_tokens,
                "reserved_tokens": reserved_tokens,
                "provider_input_tokens": None,
                "provider_output_tokens": None,
                "provider_total_tokens": None,
                "failed_attempt_usage": None,
                "reasoning_tokens": None,
                "finish_reason": None,
                "provider_usage_observed": False,
                "quality_passed": False,
                "transport_attempted": False,
                "error": None,
                "failure_evidence": None,
            }
            try:
                # ``LLMClient.complete`` interprets max_retries as the number
                # of JSON attempts (range(max_retries)); zero would execute
                # no Provider attempt at all.  One means exactly one attempt,
                # while request.transport_retries=0 still disables transport
                # retries.
                response = provider.complete(request, max_retries=1, use_cache=False)
                provider_response = response
                record["transport_attempted"] = True
                usage = _observed_usage(getattr(response, "usage", None))
                if usage is None:
                    ledger.reconcile(None, arm=arm, execution_id=execution_id)
                observed_provider_usage = usage
                record.update(
                    provider_input_tokens=usage["input_tokens"],
                    provider_output_tokens=usage["output_tokens"],
                    provider_total_tokens=usage["total_tokens"],
                    reasoning_tokens=_reasoning_tokens(getattr(response, "usage", {}) or {}),
                    finish_reason=getattr(response, "finish_reason", None),
                    provider_usage_observed=True,
                )
                observation = UsageObservation(
                    execution_id=execution_id,
                    route=route,
                    account_id=(
                        contract.usage_accounting.primary_account_id
                        if route == UsageRoute.PRIMARY
                        else contract.usage_accounting.fallback_account_id
                    ),
                    **usage,
                )
                ledger = ledger.reconcile(observation, arm=arm)
                _save_campaign_ledger(campaign_state_path, ledger)
                quality_passed, quality_reason, quality_payload = _quality_check(response, ingress)
                record.update(quality_passed=quality_passed, quality=quality_payload)
                if not quality_passed:
                    record["error"] = quality_reason
                    stop_reasons.append(f"{arm.value}:{quality_reason}")
                    if arm == ProjectionPolicy.COMPACT:
                        fallback_used = True
            except Exception as exc:
                if provider_response is not None and observed_provider_usage is not None:
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    record["failure_evidence"] = _failure_evidence(exc)
                    stop_reasons.append(f"{arm.value}:usage_reconcile_failed")
                    observations.append(record)
                    break
                record["transport_attempted"] = record["transport_attempted"] or type(exc) is not ProviderCanaryStop
                record["error"] = f"{type(exc).__name__}: {exc}"
                record["failure_evidence"] = _failure_evidence(exc)
                record["finish_reason"] = getattr(exc, "finish_reason", None)
                usage = _observed_usage(_exception_usage(exc))
                if usage is not None:
                    record["failed_attempt_usage"] = usage
                    record["provider_input_tokens"] = usage["input_tokens"]
                    record["provider_output_tokens"] = usage["output_tokens"]
                    record["provider_total_tokens"] = usage["total_tokens"]
                if usage is None:
                    stop_reasons.append(f"{arm.value}:unknown_provider_usage")
                else:
                    try:
                        ledger = ledger.reconcile(
                            UsageObservation(
                                execution_id=execution_id,
                                route=route,
                                account_id=(
                                    contract.usage_accounting.primary_account_id
                                    if route == UsageRoute.PRIMARY
                                    else contract.usage_accounting.fallback_account_id
                                ),
                                **usage,
                            ),
                            arm=arm,
                        )
                        _save_campaign_ledger(campaign_state_path, ledger)
                        record.update(
                            provider_input_tokens=usage["input_tokens"],
                            provider_output_tokens=usage["output_tokens"],
                            provider_total_tokens=usage["total_tokens"],
                            provider_usage_observed=True,
                        )
                        if arm == ProjectionPolicy.COMPACT and type(exc).__name__ == "InvalidLLMResponseError":
                            fallback_used = True
                            stop_reasons.append(f"{arm.value}:invalid_json_fallback")
                        else:
                            stop_reasons.append(f"{arm.value}:provider_exception")
                    except AdmissionError as reconcile_error:
                        stop_reasons.append(f"{arm.value}:{reconcile_error}")
                if usage is None:
                    stop_reasons.append(f"{arm.value}:provider_exception")
            finally:
                elapsed = time.monotonic() - started
                try:
                    ledger.enforce_wall_clock(
                        arm=arm,
                        arm_elapsed_seconds=elapsed,
                        campaign_elapsed_seconds=0.0,
                    )
                except AdmissionError as exc:
                    stop_reasons.append(f"{arm.value}:{exc}")
            observations.append(record)
            if stop_reasons and not (arm == ProjectionPolicy.COMPACT and fallback_used):
                break

        return {
            "status": "stopped" if stop_reasons else "passed",
            "claim_boundary": "task_designer_provider_canary",
            "provider_execution_admitted": True,
            "transport_attempted": any(item["transport_attempted"] for item in observations),
            "fallback_used": fallback_used,
            "admission": admission,
            "full_entry": {
                "production_entry_exercised": full_entry["production_entry_exercised"],
                "session_constraints_hash": full_entry["session_constraints_hash"],
                "checkpoint_constraints_hash": full_entry["checkpoint_constraints_hash"],
            },
            "source_snapshot_hash": source_hash,
            "observations": observations,
            "ledger": {
                "reservation_count": len(ledger.reservations),
                "observation_count": len(ledger.observations),
                "hard_caps": ledger.hard_caps.model_dump(mode="json"),
            },
            "provider_calls": len(observations),
            "network_calls": sum(1 for item in observations if item["transport_attempted"]),
            "project_mutations": 0,
            "stop_reasons": stop_reasons,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-provider", action="store_true")
    parser.add_argument("--campaign-state", type=Path)
    args = parser.parse_args()
    result = run_task_designer_provider_canary(
        execute_provider=args.execute_provider,
        campaign_state_path=args.campaign_state,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
