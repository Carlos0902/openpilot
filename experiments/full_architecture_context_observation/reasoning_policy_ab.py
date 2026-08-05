"""Hash-locked reasoning-policy screening for historical Controller requests."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.config import LLMSettings
from core.llm import LLMClient, LLMRequest
from core.reasoning import resolve_reasoning_policy
from metadata import ReasoningPolicy


ROOT = Path(__file__).resolve().parent
MUTATION_NEEDS = {
    "file_write",
    "file_delete",
    "code_file_create",
    "directory_generate",
    "code_unit_generate",
    "code_symbol_modify",
    "code_patch",
    "code_generation",
    "bug_fix",
    "repair",
}


def load_protocol(path: Path) -> dict[str, Any]:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    if protocol.get("protocol_id") != "reasoning-policy-ab-v1":
        raise ValueError("unsupported reasoning experiment protocol")
    return protocol


def load_request(root: Path, sample: dict[str, Any]) -> LLMRequest:
    artifact = (root / str(sample["request_artifact"])).resolve()
    expected_root = root.resolve()
    if artifact != expected_root and expected_root not in artifact.parents:
        raise ValueError("request artifact escapes experiment root")
    payload = artifact.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != sample["sha256"]:
        raise ValueError(f"request artifact hash mismatch for {sample['sample_id']}")
    return LLMRequest.model_validate_json(payload)


def evaluate_decision_response(
    sample: dict[str, Any],
    parsed_json: dict[str, Any] | list[Any] | None,
) -> dict[str, Any]:
    issues: list[str] = []
    critical = False
    if not isinstance(parsed_json, dict):
        return {
            "quality_pass": False,
            "critical_violation": False,
            "issues": ["invalid_json_root"],
            "decision_need_count": 0,
        }
    needs = parsed_json.get("decision_needs")
    if not isinstance(needs, list) or not needs:
        return {
            "quality_pass": False,
            "critical_violation": False,
            "issues": ["empty_or_invalid_decision_needs"],
            "decision_need_count": 0,
        }
    allowed_types = set(sample.get("allowed_need_types") or [])
    allowed_commands = set(sample.get("allowed_commands") or [])
    required_commands = set(sample.get("required_commands") or [])
    required_targets = set(sample.get("required_target_basenames") or [])
    forbidden_targets = set(sample.get("forbidden_target_basenames") or [])
    observed_commands: set[str] = set()
    observed_targets: set[str] = set()
    observed_mutation = False
    for need in needs:
        if not isinstance(need, dict):
            issues.append("invalid_decision_need")
            continue
        need_type = str(need.get("need_type") or "")
        if allowed_types and need_type not in allowed_types:
            issues.append("disallowed_need_type")
            critical = True
        if need_type in MUTATION_NEEDS:
            observed_mutation = True
        target = str(need.get("target_path") or "")
        if target:
            basename = Path(target).name
            observed_targets.add(basename)
            if basename in forbidden_targets:
                issues.append("forbidden_target")
                critical = True
        command = str(need.get("command") or "").strip()
        if command:
            observed_commands.add(command)
            if command not in allowed_commands:
                issues.append("unexpected_command")
                critical = True
    for command in sorted(required_commands - observed_commands):
        issues.append("missing_required_command")
    for target in sorted(required_targets - observed_targets):
        issues.append("missing_required_target")
    if sample.get("required_mutation") and not observed_mutation:
        issues.append("missing_required_mutation")
    return {
        "quality_pass": not issues,
        "critical_violation": critical,
        "issues": sorted(set(issues)),
        "decision_need_count": len(needs),
        "observed_need_types": sorted(
            {str(need.get("need_type") or "") for need in needs if isinstance(need, dict)}
        ),
        "observed_commands": sorted(observed_commands),
        "observed_target_basenames": sorted(observed_targets),
    }


def apply_task_contract_filter(
    sample: dict[str, Any],
    parsed_json: dict[str, Any] | list[Any] | None,
) -> dict[str, Any] | list[Any] | None:
    if not isinstance(parsed_json, dict) or not isinstance(parsed_json.get("decision_needs"), list):
        return parsed_json
    contract_kind = str(sample.get("contract_kind") or "")
    required_commands = set(sample.get("required_commands") or [])
    filtered: list[Any] = []
    for need in parsed_json["decision_needs"]:
        if not isinstance(need, dict):
            filtered.append(need)
            continue
        need_type = str(need.get("need_type") or "")
        command = str(need.get("command") or "").strip()
        if contract_kind == "validate":
            if need_type == "command_check" and command in required_commands:
                filtered.append(need)
        elif contract_kind == "implement" and need_type == "command_check":
            continue
        else:
            filtered.append(need)
    return {**parsed_json, "decision_needs": filtered}


def summarize_arm(arm: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    quality_passes = [
        bool(record.get("system_quality", record.get("quality", {})).get("quality_pass"))
        for record in records
    ]
    critical_count = sum(
        bool(record.get("system_quality", record.get("quality", {})).get("critical_violation"))
        for record in records
    )
    usage_records = [record.get("usage") or {} for record in records]

    def values(name: str) -> list[int]:
        return [int(usage.get(name) or 0) for usage in usage_records if usage.get(name) is not None]

    quality_gate = bool(records) and all(quality_passes) and critical_count == 0
    return {
        "arm": arm,
        "sample_count": len(records),
        "quality_pass_count": sum(quality_passes),
        "critical_violation_count": critical_count,
        "quality_gate_passed": quality_gate,
        "cost_eligible": quality_gate and len(usage_records) == len(records),
        "completion_tokens_median": _median(values("completion_tokens")),
        "reasoning_tokens_median": _median(values("reasoning_tokens")),
        "total_tokens_median": _median(values("total_tokens")),
        "duration_ms_median": _median(
            [int(record.get("duration_ms") or 0) for record in records]
        ),
    }


def run_experiment(
    protocol_path: Path,
    output_dir: Path,
    *,
    selected_arms: set[str] | None = None,
) -> dict[str, Any]:
    protocol = load_protocol(protocol_path)
    settings = LLMSettings()
    settings.require_ready()
    client = LLMClient(settings, enable_cache=False)
    routine_samples = [sample for sample in protocol["samples"] if not sample.get("complex_positive")]
    complex_samples = [sample for sample in protocol["samples"] if sample.get("complex_positive")]
    arm_specs = {
        "baseline": (protocol["routine_arms"]["baseline"], routine_samples, None),
        "economical": (protocol["routine_arms"]["economical"], routine_samples, None),
        "complex_economical": (
            protocol["complex_arms"]["economical"],
            complex_samples,
            None,
        ),
        "deliberative": (
            protocol["complex_arms"]["deliberative"],
            complex_samples,
            None,
        ),
        **{
            arm: (spec["policy"], routine_samples, int(spec["max_tokens"]))
            for arm, spec in protocol.get("budget_arms", {}).items()
        },
    }
    if selected_arms is not None:
        unknown = selected_arms - set(arm_specs)
        if unknown:
            raise ValueError(f"unknown experiment arms: {sorted(unknown)}")
        arm_specs = {key: value for key, value in arm_specs.items() if key in selected_arms}
    records: list[dict[str, Any]] = []
    for arm, (policy_payload, samples, max_tokens_override) in arm_specs.items():
        policy = ReasoningPolicy.model_validate(policy_payload)
        resolved = resolve_reasoning_policy(policy, settings)
        for sample in samples:
            request_updates: dict[str, Any] = {"reasoning_policy": policy}
            if max_tokens_override is not None:
                request_updates["max_tokens"] = max_tokens_override
            request = load_request(ROOT, sample).model_copy(update=request_updates)
            started = time.monotonic()
            try:
                response = client.complete(request, max_retries=1, use_cache=False)
                usage = _usage(response.usage)
                quality = evaluate_decision_response(sample, response.parsed_json)
                system_quality = evaluate_decision_response(
                    sample,
                    apply_task_contract_filter(sample, response.parsed_json),
                )
                record = {
                    "arm": arm,
                    "sample_id": sample["sample_id"],
                    "request_sha256": sample["sha256"],
                    "max_tokens": request.max_tokens,
                    "resolved_reasoning": resolved.model_dump(mode="json"),
                    "success": True,
                    "finish_reason": response.finish_reason,
                    "usage": usage,
                    "quality": quality,
                    "system_quality": system_quality,
                    "response_content": response.content,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            except Exception as exc:
                record = {
                    "arm": arm,
                    "sample_id": sample["sample_id"],
                    "request_sha256": sample["sha256"],
                    "max_tokens": request.max_tokens,
                    "resolved_reasoning": resolved.model_dump(mode="json"),
                    "success": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "finish_reason": getattr(exc, "finish_reason", None),
                    "usage": _usage(dict(getattr(exc, "usage", {}) or {})),
                    "quality": {
                        "quality_pass": False,
                        "critical_violation": False,
                        "issues": ["provider_failure"],
                        "decision_need_count": 0,
                    },
                    "system_quality": {
                        "quality_pass": False,
                        "critical_violation": False,
                        "issues": ["provider_failure"],
                        "decision_need_count": 0,
                    },
                    "response_content": str(getattr(exc, "response_text", "") or ""),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            records.append(record)
    summaries = {
        arm: summarize_arm(arm, [record for record in records if record["arm"] == arm])
        for arm in arm_specs
    }
    result = {
        "protocol_id": protocol["protocol_id"],
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "provider_fingerprint": _provider_fingerprint(settings),
        "records": records,
        "arm_summaries": summaries,
        "routine_comparison": (
            _routine_comparison(summaries)
            if {"baseline", "economical"}.issubset(summaries)
            else None
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return result


def _usage(raw: dict[str, Any]) -> dict[str, int | None]:
    details = raw.get("completion_tokens_details") or {}
    return {
        "prompt_tokens": _optional_int(raw.get("prompt_tokens")),
        "completion_tokens": _optional_int(raw.get("completion_tokens")),
        "reasoning_tokens": _optional_int(details.get("reasoning_tokens")),
        "total_tokens": _optional_int(raw.get("total_tokens")),
    }


def _routine_comparison(summaries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    baseline = summaries["baseline"]
    treatment = summaries["economical"]
    no_quality_regression = (
        treatment["critical_violation_count"] == 0
        and treatment["quality_pass_count"] >= baseline["quality_pass_count"]
    )
    baseline_reasoning = baseline.get("reasoning_tokens_median")
    treatment_reasoning = treatment.get("reasoning_tokens_median")
    reduction = None
    reduction_basis = "reasoning_tokens"
    if baseline_reasoning not in (None, 0) and treatment_reasoning is not None:
        reduction = (baseline_reasoning - treatment_reasoning) / baseline_reasoning
    elif baseline.get("completion_tokens_median") not in (None, 0) and treatment.get(
        "completion_tokens_median"
    ) is not None:
        reduction_basis = "completion_tokens"
        reduction = (
            baseline["completion_tokens_median"] - treatment["completion_tokens_median"]
        ) / baseline["completion_tokens_median"]
    return {
        "no_quality_regression": no_quality_regression,
        "reasoning_reduction_fraction": reduction,
        "reduction_basis": reduction_basis,
        "thirty_percent_reduction": reduction is not None and reduction >= 0.30,
        "eligible_for_budget_floor_test": no_quality_regression
        and treatment["quality_gate_passed"]
        and reduction is not None
        and reduction >= 0.30,
    }


def _provider_fingerprint(settings: LLMSettings) -> dict[str, str]:
    parsed = urlparse(settings.base_url)
    endpoint = f"{parsed.scheme}://{parsed.hostname or ''}{parsed.path}"
    profile = resolve_reasoning_policy(ReasoningPolicy(), settings)
    payload = {
        "provider": settings.provider,
        "model": settings.model,
        "endpoint": endpoint,
        "profile": f"{profile.profile_id}:{profile.profile_version}",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**payload, "sha256": hashlib.sha256(encoded).hexdigest()}


def _median(values: list[int]) -> float | None:
    return float(statistics.median(values)) if values else None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "REASONING_POLICY_AB_PROTOCOL_V1.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--arms",
        default="",
        help=(
            "Optional comma-separated subset: baseline,economical,economical_1200,"
            "economical_800,complex_economical,deliberative"
        ),
    )
    args = parser.parse_args()
    selected_arms = {item.strip() for item in args.arms.split(",") if item.strip()} or None
    result = run_experiment(args.protocol, args.output_dir, selected_arms=selected_arms)
    print(json.dumps({"output_dir": str(args.output_dir), "routine": result["routine_comparison"]}))


if __name__ == "__main__":
    main()
