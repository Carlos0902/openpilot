"""Plan, execute, and analyze the frozen Stage 7 full-architecture campaign."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.config import LLMSettings
from fixed_decomposition_harness import collect_improvement_cost, evaluate_upstream_gate
from run_observation import _load_events, analyze, provider_runtime_identity


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
PROTOCOL_PATH = HERE / "STAGE7_COMPLETION_BUDGET_CAMPAIGN_V4.json"
RUNNER_PATH = HERE / "run_observation.py"
ARMS = {"static", "dynamic"}
TOKEN_KEYS = ("input_tokens", "output_tokens", "total_tokens")


def load_campaign_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_campaign_protocol(protocol: dict[str, Any]) -> None:
    pairs = int(protocol.get("pairs") or 0)
    orders = protocol.get("pair_arm_order") or []
    if pairs < 3 or len(orders) != pairs:
        raise ValueError("campaign requires at least three fully specified pairs")
    prior_first = None
    for order in orders:
        if len(order) != 2 or set(order) != ARMS:
            raise ValueError("each pair must contain static and dynamic exactly once")
        if prior_first == order[0]:
            raise ValueError("pair arm order must alternate")
        prior_first = order[0]
    if protocol.get("cache_enabled") is not False:
        raise ValueError("campaign cache must be disabled")
    if int(protocol.get("execution", {}).get("transport_retries", -1)) != 0:
        raise ValueError("campaign transport retries must be zero")
    limits = protocol.get("token_limits") or {}
    if limits != {
        "per_arm_hard": 45_000,
        "per_pair_warning": 75_000,
        "per_pair_hard": 90_000,
        "campaign_warning": 225_000,
        "campaign_hard": 270_000,
    }:
        raise ValueError("campaign token limits do not match the frozen safety gate")
    purposes = protocol.get("analysis", {}).get("enhancement_purposes") or []
    expected_purposes = [
        "project_improvement",
        "iteration_goal",
        "iteration_task_design",
        "code_generation",
    ]
    if protocol.get("campaign_id") != "stage7-enhancement-completion-budget-ab-v1":
        expected_purposes.append("code_edit")
    if purposes != expected_purposes:
        raise ValueError(
            f"campaign must cover the exact {len(expected_purposes)} enhancement purposes"
        )
    analysis = protocol.get("analysis") or {}
    required = analysis.get("required_provider_purposes")
    mutation_any = analysis.get("mutation_purpose_any_of")
    if required is not None and not set(required).issubset(set(purposes)):
        raise ValueError("required provider purposes must belong to enhancement purposes")
    if mutation_any is not None and not set(mutation_any).issubset(set(purposes)):
        raise ValueError("mutation route purposes must belong to enhancement purposes")
    if protocol.get("campaign_id") == "stage7-enhancement-completion-budget-ab-v4":
        memory = (protocol.get("common_interventions") or {}).get(
            "memory_baseline"
        ) or {}
        if memory.get("strategy") != "isolated_empty":
            raise ValueError("V4 requires an isolated empty memory baseline per arm")


def build_schedule(protocol: dict[str, Any]) -> list[dict[str, Any]]:
    validate_campaign_protocol(protocol)
    schedule = []
    ordinal = 0
    for pair, order in enumerate(protocol["pair_arm_order"], start=1):
        for position, arm in enumerate(order, start=1):
            ordinal += 1
            schedule.append(
                {"ordinal": ordinal, "pair": pair, "position": position, "arm": arm}
            )
    return schedule


def _excluded(relative: Path, patterns: list[str]) -> bool:
    return any(
        pattern in relative.parts or fnmatch.fnmatch(relative.name, pattern)
        for pattern in patterns
    )


def code_snapshot_sha256(protocol: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    excludes = list(protocol.get("code_snapshot_excludes") or [])
    files: list[Path] = []
    for root_name in protocol["code_snapshot_roots"]:
        root = (REPO_ROOT / root_name).resolve()
        for path in root.rglob("*"):
            relative = path.relative_to(REPO_ROOT)
            if path.is_file() and not _excluded(relative, excludes):
                files.append(path)
    for path in sorted(set(files)):
        relative = path.relative_to(REPO_ROOT).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _usage(usage: dict[str, Any]) -> tuple[dict[str, int], bool]:
    input_value = usage.get("input_tokens")
    if input_value is None:
        input_value = usage.get("prompt_tokens")
    output_value = usage.get("output_tokens")
    if output_value is None:
        output_value = usage.get("completion_tokens")
    observed = input_value is not None and output_value is not None
    input_tokens = int(input_value or 0)
    output_tokens = int(output_value or 0)
    total_value = usage.get("total_tokens")
    total_tokens = int(
        total_value if total_value is not None else input_tokens + output_tokens
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }, observed


def _window_usage(events: list[dict[str, Any]], start_sequence: int) -> dict[str, Any]:
    requests: dict[str, dict[str, Any]] = {}
    totals = {window: {key: 0 for key in TOKEN_KEYS} for window in ("core", "enhancement")}
    purpose_totals: dict[str, dict[str, int]] = {}
    for index, event in enumerate(events):
        sequence = int(event.get("sequence") or index + 1)
        payload = event.get("payload") or {}
        call_id = str(
            payload.get("call_id")
            or (payload.get("correlation") or {}).get("execution_id")
            or ""
        )
        if not call_id:
            continue
        if event.get("event_type") == "llm_requested":
            selection = payload.get("context_selection") or {}
            requests[call_id] = {
                "window": "enhancement" if sequence > start_sequence else "core",
                "purpose": str(selection.get("request_purpose") or payload.get("purpose") or ""),
            }
            continue
        if event.get("event_type") not in {"llm_responded", "llm_failed"}:
            continue
        request = requests.get(call_id)
        if request is None:
            continue
        if event.get("event_type") == "llm_responded":
            metadata = payload.get("response_metadata") or payload
            raw_usage = metadata.get("usage") or {}
        else:
            metadata = payload.get("failure") or payload
            raw_usage = ((metadata.get("details") or {}).get("provider_attempt") or {}).get("usage") or {}
        normalized, observed = _usage(raw_usage)
        if not observed:
            continue
        window = request["window"]
        for key in TOKEN_KEYS:
            totals[window][key] += normalized[key]
        if window == "enhancement":
            purpose = request["purpose"]
            bucket = purpose_totals.setdefault(purpose, {key: 0 for key in TOKEN_KEYS})
            for key in TOKEN_KEYS:
                bucket[key] += normalized[key]
    lifecycle = {
        key: totals["core"][key] + totals["enhancement"][key] for key in TOKEN_KEYS
    }
    return {**totals, "lifecycle": lifecycle, "enhancement_purpose_totals": purpose_totals}


def _successful_commands(events: list[dict[str, Any]]) -> list[str]:
    commands = []
    for event in events:
        if event.get("event_type") != "tool_succeeded":
            continue
        payload = event.get("payload") or {}
        inputs = payload.get("input_metadata") or {}
        command = inputs.get("command") or inputs.get("run_command")
        if command:
            commands.append(_canonical_validation_command(str(command)))
    return commands


def _canonical_validation_command(command: str) -> str:
    """Normalize only the project-interpreter prefix used by validation."""

    try:
        parts = shlex.split(command)
    except ValueError:
        return command.strip()
    if len(parts) >= 3 and parts[1] == "-m" and Path(parts[0]).name in {
        "python",
        "python3",
    }:
        parts[0] = "python"
    return shlex.join(parts)


def _contains_ordered(values: list[str], required: list[str]) -> bool:
    cursor = 0
    for value in values:
        if cursor < len(required) and value == required[cursor]:
            cursor += 1
    return cursor == len(required)


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate_campaign_quality(
    manifest: dict[str, Any],
    events: list[dict[str, Any]],
    protocol: dict[str, Any],
) -> dict[str, Any]:
    gates = protocol["quality_gates"]
    outcome = manifest.get("outcome") or {}
    result = outcome.get("result") or {}
    state = result.get("agent_runtime_state") or {}
    session_result = result.get("session_result") or {}
    modified = sorted(Path(path).name for path in state.get("modified_files") or [])
    commands = _successful_commands(events)
    project_dir = Path(str(manifest.get("project_dir") or ""))
    unchanged_hash = _file_sha256(project_dir / gates["unchanged_file"])
    descriptor = manifest.get("fixed_decomposition") or {}
    checks = {
        "run_completed": outcome.get("completed") is True,
        "core_success": state.get("core_success") is True,
        "verification_passed": state.get("verification_status") == "passed",
        "improvement_succeeded": state.get("project_improvement_status") == "succeeded",
        "improvement_count": (
            int(session_result.get("completed_improvements") or 0)
            >= int(session_result.get("required_improvements") or 1)
        ),
        "mutation_scope": (
            set(modified).issubset(set(gates["allowed_modified_basenames"]))
            and set(gates["required_modified_basenames"]).issubset(modified)
        ),
        "required_commands": _contains_ordered(commands, gates["required_commands"]),
        "unchanged_file": unchanged_hash == gates["unchanged_file_sha256"],
        "fixed_decomposition": (
            descriptor.get("fixture_id") == protocol["fixed_decomposition"]["fixture_id"]
            and descriptor.get("fixture_sha256")
            == protocol["fixed_decomposition"]["fixture_sha256"]
        ),
    }
    signature_payload = {
        "core_success": state.get("core_success"),
        "verification_status": state.get("verification_status"),
        "project_improvement_status": state.get("project_improvement_status"),
        "completed_improvements": session_result.get("completed_improvements"),
        "required_improvements": session_result.get("required_improvements"),
        "modified_basenames": modified,
        "successful_required_commands": [
            command for command in commands if command in gates["required_commands"]
        ],
        "unchanged_file_sha256": unchanged_hash,
    }
    signature = "sha256:" + hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {"passed": all(checks.values()), "checks": checks, "signature": signature}


def build_run_record(
    run_dir: Path,
    *,
    schedule_item: dict[str, Any],
    code_snapshot: str,
    protocol: dict[str, Any],
) -> dict[str, Any]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    events = _load_events(run_dir / "diagnostics")
    overall = analyze(events)
    gate = evaluate_upstream_gate(events)
    start = int(gate.get("improvement_started_sequence") or 10**12)
    usage = _window_usage(events, start)
    enhancement = collect_improvement_cost(events, gate)
    purpose_totals = {
        purpose: usage["enhancement_purpose_totals"].get(
            purpose, {key: 0 for key in TOKEN_KEYS}
        )
        for purpose in protocol["analysis"]["enhancement_purposes"]
    }
    audit = dict(enhancement["completion_budget_audit"])
    audit["recovery_count"] = sum(
        reservation.get("recovery_of") is not None
        for reservation in audit["reservations"]
    )
    enhancement = {**enhancement, "purpose_totals": purpose_totals, "completion_budget_audit": audit}
    memory_intervention = manifest.get("memory_intervention") or {}
    expected_memory = (protocol.get("common_interventions") or {}).get(
        "memory_baseline"
    ) or {}
    expected_memory_dir = (run_dir / "isolated_memory").resolve()
    memory_baseline_matches = (
        not expected_memory
        or (
            memory_intervention.get("strategy") == expected_memory.get("strategy")
            and (
                expected_memory.get("strategy") != "isolated_empty"
                or Path(str(memory_intervention.get("data_dir") or "")).resolve()
                == expected_memory_dir
            )
        )
    )
    return {
        **schedule_item,
        "run_dir": str(run_dir),
        "code_snapshot_sha256": code_snapshot,
        "provider_identity": manifest.get("provider_runtime_identity"),
        "enhancement_budget_arm": manifest.get("enhancement_budget_arm"),
        "memory_intervention": memory_intervention,
        "memory_baseline_matches": memory_baseline_matches,
        "effective_arm_policy_matches": (
            manifest.get("effective_enhancement_completion_policy")
            == (manifest.get("enhancement_budget_arm") or {}).get("policy")
        ),
        "quality_gate": evaluate_campaign_quality(manifest, events, protocol),
        "overall_usage_coverage": overall["usage_coverage"][
            "logical_request_usage_fraction"
        ],
        "unknown_failed_usage_count": max(
            sum(not attempt["usage_observed"] for attempt in overall["failed_attempts"]),
            int(
                (manifest.get("guard_observation") or {}).get(
                    "failed_attempts_without_observed_usage", 0
                )
                or 0
            ),
        ),
        "transport_retry_count": _transport_retry_count(events),
        "usage": {key: usage[key] for key in ("core", "enhancement", "lifecycle")},
        "enhancement": enhancement,
    }


def _transport_retry_count(events: list[dict[str, Any]]) -> int:
    total = 0
    for event in events:
        if event.get("event_type") != "llm_requested":
            continue
        payload = event.get("payload") or {}
        diagnostics = (payload.get("trace_info") or {}).get("diagnostics") or {}
        total += int(diagnostics.get("transport_retries") or 0)
    return total


def arm_stop_reasons(record: dict[str, Any], protocol: dict[str, Any]) -> list[str]:
    reasons = []
    if record["usage"]["lifecycle"]["total_tokens"] > int(
        protocol["token_limits"]["per_arm_hard"]
    ):
        reasons.append("per_arm_token_hard_limit_exceeded")
    if not record["quality_gate"]["passed"]:
        reasons.append("quality_gate_failed")
    coverage = record.get("overall_usage_coverage")
    if coverage is None or coverage < float(protocol["analysis"]["require_usage_coverage"]):
        reasons.append("incomplete_usage_coverage")
    if int(record.get("unknown_failed_usage_count") or 0):
        reasons.append("unknown_failed_usage")
    if int(record.get("transport_retry_count") or 0):
        reasons.append("transport_retry_observed")
    if record.get("effective_arm_policy_matches") is not True:
        reasons.append("effective_arm_policy_mismatch")
    if record.get("memory_baseline_matches") is not True:
        reasons.append("memory_baseline_mismatch")
    reasons.extend(_purpose_coverage_reasons(record, protocol))
    if record.get("provider_identity") != protocol["provider_identity"]:
        reasons.append("provider_identity_mismatch")
    return reasons


def _purpose_coverage_reasons(
    record: dict[str, Any], protocol: dict[str, Any]
) -> list[str]:
    enhancement = record.get("enhancement") or {}
    coverage = enhancement.get("target_purpose_coverage") or {}
    analysis = protocol["analysis"]
    required = analysis.get("required_provider_purposes")
    mutation_any = analysis.get("mutation_purpose_any_of")
    allowed_goal_modes = analysis.get("allowed_iteration_goal_modes")
    if required is None and mutation_any is None and allowed_goal_modes is None:
        return (
            []
            if all(coverage.get(p) for p in analysis["enhancement_purposes"])
            else ["incomplete_purpose_coverage"]
        )
    reasons = []
    if any(not coverage.get(purpose) for purpose in required or []):
        reasons.append("incomplete_required_purpose_coverage")
    if mutation_any and not any(coverage.get(purpose) for purpose in mutation_any):
        reasons.append("incomplete_mutation_purpose_coverage")
    if allowed_goal_modes and enhancement.get("iteration_goal_mode") not in allowed_goal_modes:
        reasons.append("iteration_goal_mode_mismatch")
    return reasons


def evaluate_spend_limits(
    records: list[dict[str, Any]], protocol: dict[str, Any]
) -> dict[str, Any]:
    limits = protocol["token_limits"]
    pair_totals: dict[str, int] = {}
    campaign_total = 0
    for record in records:
        tokens = int(record["usage"]["lifecycle"]["total_tokens"])
        campaign_total += tokens
        key = str(record["pair"])
        pair_totals[key] = pair_totals.get(key, 0) + tokens
    warnings = [
        f"pair_{pair}_warning_threshold_reached"
        for pair, total in pair_totals.items()
        if total >= int(limits["per_pair_warning"])
    ]
    if campaign_total >= int(limits["campaign_warning"]):
        warnings.append("campaign_warning_threshold_reached")
    hard_failures = [
        f"pair_{pair}_hard_limit_exceeded"
        for pair, total in pair_totals.items()
        if total > int(limits["per_pair_hard"])
    ]
    if campaign_total > int(limits["campaign_hard"]):
        hard_failures.append("campaign_hard_limit_exceeded")
    return {
        "pair_totals": pair_totals,
        "campaign_total": campaign_total,
        "warnings": warnings,
        "hard_failures": hard_failures,
    }


def analyze_campaign_records(
    records: list[dict[str, Any]], protocol: dict[str, Any]
) -> dict[str, Any]:
    validate_campaign_protocol(protocol)
    grouped: dict[int, dict[str, dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(int(record["pair"]), {})[record["arm"]] = record
    included = []
    excluded: dict[str, list[str]] = {}
    campaign_snapshot = records[0]["code_snapshot_sha256"] if records else None
    required_coverage = float(protocol["analysis"]["require_usage_coverage"])
    purposes = protocol["analysis"]["enhancement_purposes"]
    for pair in range(1, int(protocol["pairs"]) + 1):
        arms = grouped.get(pair, {})
        reasons = []
        if set(arms) != ARMS:
            reasons.append("missing_arm")
        else:
            static, dynamic = arms["static"], arms["dynamic"]
            if not static["quality_gate"]["passed"] or not dynamic["quality_gate"]["passed"]:
                reasons.append("quality_gate_failed")
            elif static["quality_gate"]["signature"] != dynamic["quality_gate"]["signature"]:
                reasons.append("quality_signature_mismatch")
            for record in (static, dynamic):
                coverage = record["enhancement"]["usage_coverage"].get(
                    "logical_request_usage_fraction"
                )
                if coverage is None or coverage < required_coverage:
                    reasons.append("incomplete_usage_coverage")
                    break
                coverage_reasons = _purpose_coverage_reasons(record, protocol)
                if coverage_reasons:
                    reasons.extend(coverage_reasons)
                    break
                if record["code_snapshot_sha256"] != campaign_snapshot:
                    reasons.append("code_snapshot_mismatch")
                    break
                if record["provider_identity"] != protocol["provider_identity"]:
                    reasons.append("provider_identity_mismatch")
                    break
                expected_mode = protocol["arms"][record["arm"]]["budget_mode"]
                if (record.get("enhancement_budget_arm") or {}).get(
                    "budget_mode"
                ) != expected_mode:
                    reasons.append("arm_descriptor_mismatch")
                    break
                if record.get("memory_baseline_matches") is not True:
                    reasons.append("memory_baseline_mismatch")
                    break
        if reasons:
            excluded[str(pair)] = list(dict.fromkeys(reasons))
        else:
            included.append(pair)
    totals = {arm: 0 for arm in ARMS}
    window_totals = {
        window: {arm: 0 for arm in ARMS} for window in ("core", "enhancement")
    }
    purpose_totals = {
        purpose: {arm: 0 for arm in ARMS} for purpose in purposes
    }
    failed_totals = {arm: 0 for arm in ARMS}
    reservation_totals = {arm: 0 for arm in ARMS}
    recovery_totals = {arm: 0 for arm in ARMS}
    coverage_totals = {
        arm: {"logical_requests": 0, "logical_requests_with_observed_usage": 0}
        for arm in ARMS
    }
    for pair in included:
        for arm in ARMS:
            record = grouped[pair][arm]
            totals[arm] += record["usage"]["lifecycle"]["total_tokens"]
            for window in window_totals:
                window_totals[window][arm] += record["usage"][window]["total_tokens"]
            for purpose in purposes:
                purpose_totals[purpose][arm] += record["enhancement"][
                    "purpose_totals"
                ][purpose]["total_tokens"]
            failed_totals[arm] += record["enhancement"]["failed_attempt_totals"][
                "total_tokens"
            ]
            audit = record["enhancement"]["completion_budget_audit"]
            reservation_totals[arm] += int(audit["reservation_count"])
            recovery_totals[arm] += int(audit["recovery_count"])
            coverage = record["enhancement"]["usage_coverage"]
            coverage_totals[arm]["logical_requests"] += int(
                coverage["logical_requests"]
            )
            coverage_totals[arm]["logical_requests_with_observed_usage"] += int(
                coverage["logical_requests_with_observed_usage"]
            )
    usage_coverage = {}
    for arm, coverage in coverage_totals.items():
        logical = coverage["logical_requests"]
        usage_coverage[arm] = {
            **coverage,
            "logical_request_usage_fraction": (
                coverage["logical_requests_with_observed_usage"] / logical
                if logical
                else None
            ),
        }
    change = (
        (totals["dynamic"] - totals["static"]) / totals["static"]
        if totals["static"]
        else None
    )
    minimum = int(protocol["analysis"]["minimum_quality_matched_pairs"])
    return {
        "campaign_id": protocol["campaign_id"],
        "eligible": len(included) >= minimum,
        "quality_matched_pair_count": len(included),
        "included_pairs": included,
        "excluded_pairs": excluded,
        "lifecycle_total_tokens": totals,
        "window_total_tokens": window_totals,
        "enhancement_purpose_total_tokens": purpose_totals,
        "enhancement_failed_attempt_total_tokens": failed_totals,
        "enhancement_usage_coverage": usage_coverage,
        "completion_budget_audit": {
            arm: {
                "reservations": reservation_totals[arm],
                "recoveries": recovery_totals[arm],
            }
            for arm in ("static", "dynamic")
        }
        | {"refunds_inferred": False},
        "lifecycle_total_token_change_fraction": change,
        "claim_boundary": "mechanism_campaign_not_distribution_wide_causality",
    }


def build_run_command(
    output_dir: Path, arm: str, protocol: dict[str, Any]
) -> list[str]:
    fixed_goal = (protocol.get("common_interventions") or {}).get(
        "fixed_iteration_goal"
    ) or {}
    iteration_goal_mode = fixed_goal.get("strategy", "provider")
    memory_baseline = (protocol.get("common_interventions") or {}).get(
        "memory_baseline"
    ) or {}
    memory_mode = memory_baseline.get("strategy", "shared")
    return [
        sys.executable,
        str(RUNNER_PATH),
        "--fixed-decomposition",
        "--improvement-requirement",
        "optional",
        "--enhancement-budget-arm",
        arm,
        "--iteration-goal-mode",
        iteration_goal_mode,
        "--memory-mode",
        memory_mode,
        "--max-provider-tokens",
        "45000",
        "--output-dir",
        str(output_dir),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    parser.add_argument("--output-dir", type=Path, default=HERE / "runs" / "stage7_campaign")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    protocol = load_campaign_protocol(args.protocol)
    schedule = build_schedule(protocol)
    snapshot = code_snapshot_sha256(protocol)
    identity = provider_runtime_identity(LLMSettings())
    plan = {
        "campaign_id": protocol["campaign_id"],
        "execute": args.execute,
        "code_snapshot_sha256": snapshot,
        "provider_identity": identity,
        "provider_identity_matches": identity == protocol["provider_identity"],
        "arms": protocol["arms"],
        "common_interventions": protocol["common_interventions"],
        "token_limits": protocol["token_limits"],
        "execution_limits": protocol["execution"],
        "schedule": schedule,
    }
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if identity != protocol["provider_identity"]:
        raise SystemExit("configured provider identity does not match frozen campaign")
    output_root = args.output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    records = []
    for item in schedule:
        if code_snapshot_sha256(protocol) != snapshot:
            raise RuntimeError("campaign code snapshot changed before arm execution")
        run_dir = output_root / f"{item['ordinal']:02d}_p{item['pair']}_{item['arm']}"
        completed = subprocess.run(
            build_run_command(run_dir, item["arm"], protocol),
            check=False,
            timeout=int(protocol["execution"]["per_arm_wall_clock_seconds"]),
        )
        if code_snapshot_sha256(protocol) != snapshot:
            raise RuntimeError("campaign code snapshot changed during arm execution")
        if not (run_dir / "manifest.json").is_file():
            raise RuntimeError(f"campaign arm produced no manifest: {completed.returncode}")
        record = build_run_record(
            run_dir,
            schedule_item=item,
            code_snapshot=snapshot,
            protocol=protocol,
        )
        records.append(record)
        (run_dir / "campaign_record.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        stop_reasons = arm_stop_reasons(record, protocol)
        spend = evaluate_spend_limits(records, protocol)
        if spend["warnings"]:
            print(json.dumps({"campaign_warnings": spend["warnings"]}, ensure_ascii=False))
        stop_reasons.extend(spend["hard_failures"])
        if stop_reasons:
            raise RuntimeError(
                "campaign stopped after arm: " + ", ".join(dict.fromkeys(stop_reasons))
            )
    analysis = analyze_campaign_records(records, protocol)
    analysis["spend_limits"] = evaluate_spend_limits(records, protocol)
    (output_root / "campaign_analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(analysis, ensure_ascii=False, indent=2))
    return 0 if analysis["eligible"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
