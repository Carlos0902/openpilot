"""Stage 9 six-arm provider sentinel orchestration.

The default CLI is a read-only, zero-provider preflight. Paid execution remains
disabled until ``run_observation.py`` supplies the hardened Stage 9 arm adapter.
Tests exercise orchestration through an injected ``run_arm`` callable.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest.mock import patch

import autonomous_iteration.agents.iteration_agent as iteration_agent_module
from autonomous_iteration.models import ImprovementGoal, ProjectStateSnapshot
from stage9_task_designer_scenario_gate import (
    ARMS,
    POSITIVE_SCENARIO_IDS,
    ScenarioFixture,
    build_scenario_fixtures,
    load_offline_report,
    offline_report_snapshot,
    run_offline_sentinel,
)
from stage8_task_designer_context_campaign import (
    _task_designer_observation as task_designer_observation,
)


HERE = Path(__file__).resolve().parent
PROTOCOL_PATH = HERE / "STAGE9_TASK_DESIGNER_PROVIDER_SENTINEL_V1.json"
RUNNER_PATH = HERE / "run_observation.py"
RunArm = Callable[[dict[str, Any], Path, Mapping[str, Any]], dict[str, Any]]
_VOLATILE_KEYS = {
    "annotations",
    "correlation",
    "created_at",
    "kind",
    "schema_version",
    "source",
}


class ProviderSentinelError(RuntimeError):
    """The frozen provider-sentinel contract is invalid."""


class ProviderSentinelStopped(RuntimeError):
    """An arm failed a hard gate and stopped all later provider work."""


def task_designer_observation_with_decisions(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reuse Stage 8 usage alignment and retain decisions for sentinel gates."""

    observation = task_designer_observation(events)
    matching = []
    for event in events:
        if event.get("event_type") != "llm_requested":
            continue
        selection = (event.get("payload") or {}).get("context_selection") or {}
        if selection.get("request_purpose") == "iteration_task_design":
            matching.append(selection)
    observation["candidate_decisions"] = (
        list(matching[0].get("candidate_decisions") or [])
        if len(matching) == 1
        else []
    )
    return observation


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    raw = value if isinstance(value, bytes) else _canonical_bytes(value)
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def capture_enhancement_start_snapshot(project_path: str | Path) -> dict[str, Any]:
    """Capture the production builder entry boundary without import cycles."""

    from run_observation import capture_bounded_project_snapshot

    return capture_bounded_project_snapshot(project_path)


def expected_runtime_artifact_manifest(
    snapshot: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Freeze only runtime sidecars that already exist at enhancement start."""

    root = Path(str(snapshot.get("project_root") or "")).resolve(strict=False)
    files = {str(Path(path).resolve(strict=False)) for path in snapshot.get("files") or {}}
    manifest: list[dict[str, str]] = []
    sketch = root / "sketch.json"
    if str(sketch) in files:
        manifest.append(
            {
                "path": str(sketch),
                "artifact_type": "directory_sketch",
                "relative_path": "sketch.json",
            }
        )
    index_root = root / ".openpilot" / "file_indexes"
    for raw in sorted(files):
        path = Path(raw)
        if not path.is_relative_to(index_root):
            continue
        relative_sidecar = path.relative_to(index_root).as_posix()
        if not relative_sidecar.endswith(".index.json"):
            continue
        relative_target = relative_sidecar.removesuffix(".index.json")
        target = (root / relative_target).resolve(strict=False)
        if str(target) not in files:
            continue
        manifest.append(
            {
                "path": str(path),
                "artifact_type": "file_content_index",
                "relative_path": relative_target,
            }
        )
    return sorted(manifest, key=lambda item: item["path"])


def validate_prior_paid_diagnostics(protocol: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Verify frozen paid diagnostics without treating them as formal arms."""

    diagnostics = list(protocol.get("prior_paid_diagnostics") or [])
    if len(diagnostics) != 2:
        raise ProviderSentinelError("prior paid diagnostic inventory changed")
    observed: list[dict[str, Any]] = []
    for item in diagnostics:
        if item.get("formal_sample") is not False:
            raise ProviderSentinelError("paid diagnostic must be excluded from sample")
        checked = dict(item)
        for evidence_key in ("campaign_state", "campaign_record"):
            evidence = item.get(evidence_key) or {}
            path = (HERE / str(evidence.get("path") or "")).resolve()
            try:
                path.relative_to(HERE)
            except ValueError as exc:
                raise ProviderSentinelError(
                    "paid diagnostic evidence escapes experiment root"
                ) from exc
            if not path.is_file() or _sha256_file(path) != evidence.get("sha256"):
                raise ProviderSentinelError(
                    f"paid diagnostic {evidence_key} hash mismatch"
                )
        record_path = (HERE / item["campaign_record"]["path"]).resolve()
        record = json.loads(record_path.read_text(encoding="utf-8"))
        state_path = (HERE / item["campaign_state"]["path"]).resolve()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        tokens = int(
            (((record.get("usage") or {}).get("lifecycle") or {}).get("total_tokens"))
            or 0
        )
        if tokens != int(item.get("observed_complete_tokens") or 0):
            raise ProviderSentinelError("paid diagnostic token evidence mismatch")
        state_spend = state.get("spend") or {}
        recorded_state_tokens = int(
            state_spend.get(
                "formal_campaign_total",
                state_spend.get("campaign_total", 0),
            )
            or 0
        )
        if recorded_state_tokens != tokens:
            raise ProviderSentinelError("paid diagnostic state spend mismatch")
        if int(record.get("pair") or 0) != int(item.get("pair") or 0):
            raise ProviderSentinelError("paid diagnostic pair evidence mismatch")
        observed.append(checked)
    return observed


def load_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def scenario_overlay_fingerprint(fixture: ScenarioFixture) -> str:
    """Hash exactly the scenario facts overlaid onto otherwise-live state."""

    return _sha256(
        {
            "scenario_id": fixture.scenario_id,
            "diagnosis": fixture.improvement_report["diagnosis"],
            "memory_records": fixture.project_state.memory_records,
            "completed_iteration": fixture.completed_iteration,
        }
    )


def validate_protocol(protocol: Mapping[str, Any]) -> None:
    if protocol.get("campaign_id") != "stage9-task-designer-provider-sentinel-v1":
        raise ProviderSentinelError("unexpected Stage 9 provider campaign id")
    expected_schedule = [
        (1, "strongly_related_diagnosis", ["current", "compact"]),
        (2, "partial_shared_criterion", ["compact", "current"]),
        (3, "relevant_iteration_memory", ["current", "compact"]),
    ]
    observed_schedule = [
        (int(item.get("pair") or 0), item.get("scenario_id"), item.get("arm_order"))
        for item in protocol.get("schedule") or []
    ]
    if observed_schedule != expected_schedule:
        raise ProviderSentinelError("six-arm crossed scenario schedule changed")
    if tuple((protocol.get("scenarios") or {}).keys()) != POSITIVE_SCENARIO_IDS:
        raise ProviderSentinelError("provider scenario membership changed")
    if protocol.get("cache_enabled") is not False:
        raise ProviderSentinelError("provider cache must be disabled")
    if protocol.get("common_interventions") != {
        "default_max_completion_tokens": 4096,
        "semantics": (
            "provider-neutral experiment fallback applied only when a typed "
            "request omits max_tokens"
        ),
    }:
        raise ProviderSentinelError("default completion intervention changed")
    execution = protocol.get("execution") or {}
    if any(
        int(execution.get(key, -1)) != 0
        for key in ("transport_retries", "json_repair_attempts", "length_recoveries")
    ):
        raise ProviderSentinelError("provider retry or recovery contract changed")
    if protocol.get("token_limits") != {
        "per_arm_hard": 30_000,
        "per_pair_hard": 60_000,
        "provider_sentinel_hard": 180_000,
    }:
        raise ProviderSentinelError("provider sentinel token limits changed")
    if protocol.get("mutation_evidence_contract") != {
        "raw_diff_canonical_disjoint_complete": True,
        "runtime_artifacts_must_exist_at_enhancement_start": True,
        "runtime_artifact_add_delete_allowed": False,
        "runtime_artifact_unknown_fields_allowed": False,
        "runtime_artifact_validation": (
            "strict typed metadata plus production-derived final project facts"
        ),
        "user_owned_all_changed_basenames": ["calculator.py"],
    }:
        raise ProviderSentinelError("mutation evidence contract changed")
    validate_prior_paid_diagnostics(protocol)
    if protocol.get("evidence_persistence") != {
        "arm_record_path": "<run_dir>/campaign_record.json",
        "campaign_state_path": "<campaign_root>/campaign_state.json",
        "arm_record_before_gate": True,
        "campaign_state_after_each_arm": True,
        "failure_state_before_raise": True,
    }:
        raise ProviderSentinelError("provider evidence persistence changed")
    baseline = protocol.get("core_pre_enhancement_baseline") or {}
    if baseline != {
        "source_campaign": "stage8-task-designer-context-projection-ab-v1",
        "source_fingerprint": "sha256:f15833e3b2ab5eff0d352b82986792032e79c886cf24650cf7f6ef8dbaa559e3",
        "project_state_sequence": 47,
        "exact_sentence": "Raises ValueError when denominator is zero.",
        "exact_sentence_present": False,
    }:
        raise ProviderSentinelError("core pre-enhancement baseline changed")
    fixtures = build_scenario_fixtures()
    for scenario_id in POSITIVE_SCENARIO_IDS:
        frozen = protocol["scenarios"][scenario_id]
        fixture = fixtures[scenario_id]
        if frozen.get("frozen_source_fingerprint") != scenario_overlay_fingerprint(
            fixture
        ):
            raise ProviderSentinelError(f"source fingerprint mismatch for {scenario_id}")
        if frozen.get("frozen_goal_hash") != fixture.goal_hash:
            raise ProviderSentinelError(f"goal hash mismatch for {scenario_id}")
        arm_contracts = (protocol.get("scenario_candidate_contracts") or {}).get(
            scenario_id
        )
        if not isinstance(arm_contracts, Mapping) or set(arm_contracts) != set(ARMS):
            raise ProviderSentinelError(f"candidate contract missing for {scenario_id}")


def build_schedule(protocol: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_protocol(protocol)
    schedule: list[dict[str, Any]] = []
    ordinal = 0
    for pair_spec in protocol["schedule"]:
        for position, arm in enumerate(pair_spec["arm_order"], start=1):
            ordinal += 1
            schedule.append(
                {
                    "ordinal": ordinal,
                    "pair": int(pair_spec["pair"]),
                    "position": position,
                    "scenario_id": str(pair_spec["scenario_id"]),
                    "arm": str(arm),
                }
            )
    return schedule


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_volatile(child)
            for key, child in value.items()
            if str(key) not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _project_relative(value: Any, project_root: str) -> Any:
    roots = {project_root.rstrip("/")}
    try:
        roots.add(str(Path(project_root).resolve(strict=False)).rstrip("/"))
    except OSError:
        pass
    for root in list(roots):
        if root.startswith("/private/"):
            roots.add(root[len("/private") :])
        elif root.startswith("/var/"):
            roots.add("/private" + root)
    replacements = sorted((root for root in roots if root), key=len, reverse=True)

    def normalize(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, str):
            result = item
            for root in replacements:
                result = result.replace(root, ".")
            return result
        return item

    return normalize(value)


def runtime_contract_hash(
    state: ProjectStateSnapshot,
    goal: ImprovementGoal,
    report: Mapping[str, Any],
) -> str:
    validation = dict(state.validation_context)
    prompt_context = (
        dict(report.get("prompt_context") or {})
        if isinstance(report.get("prompt_context"), Mapping)
        else {}
    )
    payload = {
        "goal": goal.model_dump(mode="json"),
        "project_path": state.project_path,
        "safe_target_files": list(state.safe_target_files),
        "file_summaries": list(state.file_summaries),
        "validation": validation,
        "prompt_context": prompt_context,
    }
    return _sha256(
        _strip_volatile(_project_relative(payload, str(state.project_path)))
    )


def overlay_scenario_sources(
    live_state: ProjectStateSnapshot,
    live_report: Mapping[str, Any],
    fixture: ScenarioFixture,
) -> tuple[ProjectStateSnapshot, dict[str, Any]]:
    state = live_state.model_copy(
        update={"memory_records": list(fixture.project_state.memory_records)}
    )
    report = dict(live_report)
    report["diagnosis"] = fixture.improvement_report["diagnosis"]
    return state, report


def _candidate_contract(
    scenario_id: str,
    arm: str,
    candidates: list[Any],
    protocol: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    expected = dict(protocol["scenario_candidate_contracts"][scenario_id][arm])
    content = "\n".join(str(candidate.content) for candidate in candidates)
    passed = all(item in content for item in expected["required_present"]) and all(
        item not in content for item in expected["required_absent"]
    )
    return expected, passed


@contextmanager
def task_designer_scenario_scope(
    arm: str,
    fixture: ScenarioFixture,
    *,
    agent: Any,
    protocol: Mapping[str, Any] | None = None,
):
    """Overlay only scenario evidence while retaining live execution facts."""

    frozen_protocol = dict(protocol or load_protocol())
    validate_protocol(frozen_protocol)
    if arm not in ARMS:
        raise ProviderSentinelError(f"unsupported projection arm: {arm}")
    if fixture.scenario_id not in POSITIVE_SCENARIO_IDS:
        raise ProviderSentinelError("provider sentinel accepts positive scenarios only")
    production_builder = iteration_agent_module.build_iteration_task_design_candidates
    descriptor: dict[str, Any] = {
        "scenario_id": fixture.scenario_id,
        "projection_policy": arm,
        "source_fingerprint": scenario_overlay_fingerprint(fixture),
        "goal_hash": fixture.goal_hash,
        "runtime_contract_hashes": [],
        "sentinel_contract": frozen_protocol["scenario_candidate_contracts"][
            fixture.scenario_id
        ][arm],
        "sentinel_contract_passed": False,
        "sentinel_candidate_ids": [],
        "injection_scope": "live_facts_plus_scenario_diagnosis_memory_overlay",
        "enhancement_start_snapshot_capture_count": 0,
        "enhancement_start_snapshot_observation_count": 0,
        "enhancement_start_snapshot_consistent": True,
    }

    def build_with_overlay(**runtime_kwargs: Any):
        live_state = runtime_kwargs["project_state"]
        live_goal = runtime_kwargs["goal"]
        live_report = runtime_kwargs["improvement_report"]
        if _sha256(live_goal.model_dump(mode="json")) != fixture.goal_hash:
            raise ProviderSentinelError("runtime goal hash mismatch")
        try:
            observed_snapshot = capture_enhancement_start_snapshot(
                live_state.project_path
            )
        except (OSError, ValueError) as exc:
            raise ProviderSentinelError(
                "enhancement start snapshot capture failed"
            ) from exc
        descriptor["enhancement_start_snapshot_observation_count"] += 1
        if observed_snapshot.get("truncated") or observed_snapshot.get(
            "symlink_paths"
        ):
            descriptor["enhancement_start_snapshot_consistent"] = False
            raise ProviderSentinelError(
                "enhancement start snapshot is untrustworthy"
            )
        observed_fingerprint = _sha256(observed_snapshot)
        if descriptor["enhancement_start_snapshot_capture_count"] == 0:
            descriptor["enhancement_start_project_snapshot"] = observed_snapshot
            descriptor["enhancement_start_snapshot_fingerprint"] = (
                observed_fingerprint
            )
            descriptor["enhancement_start_snapshot_capture_count"] = 1
            descriptor["expected_runtime_artifact_manifest"] = (
                expected_runtime_artifact_manifest(observed_snapshot)
            )
            descriptor["enhancement_start_snapshot_stage"] = (
                "task_designer_production_builder_entry"
            )
        elif observed_fingerprint != descriptor.get(
            "enhancement_start_snapshot_fingerprint"
        ):
            descriptor["enhancement_start_snapshot_consistent"] = False
            raise ProviderSentinelError(
                "enhancement start snapshot changed across builder entries"
            )
        state, report = overlay_scenario_sources(live_state, live_report, fixture)
        descriptor["runtime_contract_hashes"].append(
            runtime_contract_hash(live_state, live_goal, live_report)
        )
        descriptor["runtime_contract_hash"] = descriptor[
            "runtime_contract_hashes"
        ][0]
        candidates = production_builder(
            project_state=state,
            goal=live_goal,
            improvement_report=report,
            completed_iteration=runtime_kwargs["completed_iteration"],
            projection_policy=arm,
        )
        contract, passed = _candidate_contract(
            fixture.scenario_id, arm, candidates, frozen_protocol
        )
        descriptor["sentinel_contract"] = contract
        descriptor["sentinel_contract_passed"] = passed
        descriptor["sentinel_candidate_ids"] = sorted(
            {
                str(candidate.candidate_id)
                for candidate in candidates
                if any(
                    sentinel in str(candidate.content)
                    for sentinel in contract["required_present"]
                )
            }
        )
        if not passed:
            raise ProviderSentinelError("arm-specific candidate sentinel contract failed")
        return candidates

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                iteration_agent_module,
                "build_iteration_task_design_candidates",
                new=build_with_overlay,
            )
        )
        stack.enter_context(
            patch.object(
                agent,
                "_goal_from_candidate",
                new=lambda selected_candidate, report, evaluation: fixture.goal,
            )
        )
        yield descriptor


def validate_modified_paths(project_root: str | Path, paths: list[str | Path]) -> list[str]:
    root_path = Path(project_root).expanduser()
    if root_path.is_symlink():
        return ["project_root_is_symlink"]
    root = root_path.resolve(strict=False)
    reasons: list[str] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        candidate = path if path.is_absolute() else root / path
        if candidate.is_symlink():
            reasons.append("modified_path_is_symlink")
            continue
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            reasons.append("modified_path_outside_project")
    return list(dict.fromkeys(reasons))


def _runtime_source_valid(payload: Mapping[str, Any]) -> bool:
    return payload.get("source") == {
        "source_type": "system",
        "source_name": "openpilot",
    }


def _canonical_payload_path(value: Any, expected: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return Path(value).expanduser().resolve(strict=False) == expected
    except OSError:
        return False


def _filtered_mutation_paths(
    mutations: Mapping[str, Any], owned_paths: set[str]
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key in (
        "added_paths",
        "deleted_paths",
        "modified_paths",
        "all_changed_paths",
    ):
        selected = []
        for raw in mutations.get(key) or []:
            canonical = str(Path(str(raw)).expanduser().resolve(strict=False))
            if canonical in owned_paths:
                selected.append(canonical)
        result[key] = sorted(selected)
    return result


def validate_raw_mutation_descriptor(
    project_root: str | Path,
    mutations: Mapping[str, Any],
) -> list[str]:
    """Require one canonical, disjoint, internally complete hash-diff descriptor."""

    root_path = Path(project_root).expanduser()
    if root_path.is_symlink():
        return ["raw_mutation_project_root_invalid"]
    try:
        root = root_path.resolve(strict=True)
    except OSError:
        return ["raw_mutation_project_root_invalid"]
    categories: dict[str, list[str]] = {}
    failures: list[str] = []
    for key in ("added_paths", "deleted_paths", "modified_paths", "all_changed_paths"):
        values = mutations.get(key)
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            failures.append("raw_mutation_shape_invalid")
            categories[key] = []
            continue
        categories[key] = values
        if len(values) != len(set(values)):
            failures.append("raw_mutation_duplicate")
        for raw in values:
            path = Path(raw).expanduser()
            if not path.is_absolute() or str(path.resolve(strict=False)) != raw:
                failures.append("raw_mutation_path_noncanonical")
                continue
            try:
                path.resolve(strict=False).relative_to(root)
            except ValueError:
                failures.append("raw_mutation_path_outside_project")
    added = set(categories.get("added_paths") or [])
    deleted = set(categories.get("deleted_paths") or [])
    modified = set(categories.get("modified_paths") or [])
    if added & deleted or added & modified or deleted & modified:
        failures.append("raw_mutation_category_overlap")
    if set(categories.get("all_changed_paths") or []) != added | deleted | modified:
        failures.append("raw_all_changed_union_mismatch")
    return list(dict.fromkeys(failures))


def _strip_allowed_volatile(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_allowed_volatile(child)
            for key, child in value.items()
            if str(key) not in {"created_at", "correlation"}
        }
    if isinstance(value, list):
        return [_strip_allowed_volatile(item) for item in value]
    return value


def _validate_index_against_final_file(
    *,
    root: Path,
    index_path: Path,
    relative_path: str,
) -> tuple[Any | None, list[str]]:
    from memory.project_index import ProjectIndexManager, _language_for_path, _read_text
    from metadata import FileContentIndexMetadata
    from pydantic import ValidationError

    failures: list[str] = []
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, ["runtime_owned_json_invalid"]
    if not isinstance(payload, Mapping):
        return None, ["runtime_owned_json_invalid"]
    if payload.get("kind") != "file_content_index":
        return None, ["runtime_owned_kind_mismatch"]
    if not _runtime_source_valid(payload):
        return None, ["runtime_owned_source_mismatch"]
    try:
        index = FileContentIndexMetadata.model_validate(payload)
    except ValidationError:
        return None, ["runtime_owned_typed_schema_invalid"]
    if index.model_dump(mode="json") != payload:
        return None, ["runtime_owned_typed_schema_invalid"]
    target = (root / relative_path).resolve(strict=False)
    expected_index = (
        root
        / ".openpilot"
        / "file_indexes"
        / Path(relative_path).parent
        / f"{Path(relative_path).name}.index.json"
    ).resolve(strict=False)
    if index.annotations:
        failures.append("runtime_owned_annotations_invalid")
    if not _canonical_payload_path(index.project_root, root):
        failures.append("runtime_owned_project_root_mismatch")
    actual_relative = Path(index.relative_path)
    if (
        not index.relative_path
        or actual_relative.is_absolute()
        or ".." in actual_relative.parts
        or actual_relative.as_posix() != index.relative_path
    ):
        failures.append("runtime_owned_relative_path_invalid")
    elif index.relative_path != relative_path:
        failures.append("runtime_owned_relative_path_mismatch")
    if not _canonical_payload_path(index.file_path, target):
        failures.append("runtime_owned_file_path_mismatch")
    if not _canonical_payload_path(index.index_file, expected_index):
        failures.append("runtime_owned_index_file_mismatch")
    if index.deleted:
        failures.append("runtime_owned_deleted_index_invalid")
    if not target.is_file() or target.is_symlink():
        failures.append("runtime_owned_target_invalid")
        return index, failures
    content = _read_text(target)
    if index.content_sha256 != hashlib.sha256(content.encode("utf-8")).hexdigest():
        failures.append("runtime_owned_content_sha256_mismatch")
    if index.byte_size != target.stat().st_size:
        failures.append("runtime_owned_byte_size_mismatch")
    if index.line_count != len(content.splitlines()):
        failures.append("runtime_owned_line_count_mismatch")
    if index.language != _language_for_path(target):
        failures.append("runtime_owned_language_mismatch")
    manager = ProjectIndexManager(root)
    expected_sections = manager._sections_for(target, content, relative_path)
    if _strip_allowed_volatile(
        [item.model_dump(mode="json") for item in index.sections]
    ) != _strip_allowed_volatile(
        [item.model_dump(mode="json") for item in expected_sections]
    ):
        failures.append("runtime_owned_sections_mismatch")
    return index, failures


def _validate_sketch_against_final_project(
    *, root: Path, sketch_path: Path
) -> list[str]:
    from memory.project_index import ProjectIndexManager
    from metadata import DirectorySketchMetadata
    from pydantic import ValidationError

    try:
        payload = json.loads(sketch_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ["runtime_owned_json_invalid"]
    if not isinstance(payload, Mapping):
        return ["runtime_owned_json_invalid"]
    if payload.get("kind") != "directory_sketch":
        return ["runtime_owned_kind_mismatch"]
    if not _runtime_source_valid(payload):
        return ["runtime_owned_source_mismatch"]
    try:
        sketch = DirectorySketchMetadata.model_validate(payload)
    except ValidationError:
        return ["runtime_owned_typed_schema_invalid"]
    if sketch.model_dump(mode="json") != payload:
        return ["runtime_owned_typed_schema_invalid"]
    failures: list[str] = []
    manager = ProjectIndexManager(root)
    if sketch.annotations:
        failures.append("runtime_owned_annotations_invalid")
    if sketch.version != manager.INDEX_VERSION:
        failures.append("runtime_owned_sketch_version_mismatch")
    if not _canonical_payload_path(sketch.project_root, root):
        failures.append("runtime_owned_project_root_mismatch")
    if not _canonical_payload_path(sketch.directory, root):
        failures.append("runtime_owned_directory_mismatch")
    expected_files: dict[str, Any] = {}
    for target in sorted(item for item in root.iterdir() if item.is_file()):
        if target.is_symlink() or manager._skip_project_file(target):
            continue
        relative = target.relative_to(root).as_posix()
        index_path = manager.index_file_for(target).resolve(strict=False)
        index, index_failures = _validate_index_against_final_file(
            root=root,
            index_path=index_path,
            relative_path=relative,
        )
        failures.extend(index_failures)
        if index is not None and not index_failures:
            expected_files[target.name] = manager._sketch_file_item(target, index)
    if sketch.files != expected_files:
        failures.append("runtime_owned_sketch_files_mismatch")
    return list(dict.fromkeys(failures))


def classify_enhancement_mutations(
    project_root: str | Path,
    mutations: Mapping[str, Any],
    *,
    expected_artifacts: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify only start-frozen runtime artifacts verified from final facts."""

    root_path = Path(project_root).expanduser()
    empty = _filtered_mutation_paths(mutations, set())
    raw_failures = validate_raw_mutation_descriptor(project_root, mutations)
    base_validation = {
        "passed": False,
        "raw_descriptor_failures": raw_failures,
        "expected_artifact_count": len(expected_artifacts or []),
        "artifacts": [],
        "failures": list(raw_failures),
    }
    if raw_failures:
        return {
            "runtime_owned_mutations": empty,
            "user_owned_mutations": empty,
            "failures": list(raw_failures),
            "producer_validation": base_validation,
        }
    if root_path.is_symlink():
        return {
            "runtime_owned_mutations": empty,
            "user_owned_mutations": empty,
            "failures": ["project_root_symlink"],
            "producer_validation": {
                **base_validation,
                "failures": ["project_root_symlink"],
            },
        }
    try:
        root = root_path.resolve(strict=True)
    except OSError:
        return {
            "runtime_owned_mutations": empty,
            "user_owned_mutations": empty,
            "failures": ["project_root_invalid"],
            "producer_validation": {
                **base_validation,
                "failures": ["project_root_invalid"],
            },
        }
    expected_by_path: dict[str, Mapping[str, Any]] = {}
    manifest_failures: list[str] = []
    for item in expected_artifacts or []:
        if set(item) != {"path", "artifact_type", "relative_path"}:
            manifest_failures.append("expected_artifact_manifest_invalid")
            continue
        path = str(item.get("path") or "")
        artifact_type = item.get("artifact_type")
        if (
            path != str(Path(path).resolve(strict=False))
            or path in expected_by_path
            or artifact_type not in {"directory_sketch", "file_content_index"}
        ):
            manifest_failures.append("expected_artifact_manifest_invalid")
            continue
        expected_by_path[path] = item
    runtime_paths: set[str] = set()
    user_paths: set[str] = set()
    failures: list[str] = []
    sketch_path = root / "sketch.json"
    index_root = root / ".openpilot" / "file_indexes"

    for raw_path in mutations.get("all_changed_paths") or []:
        path = Path(str(raw_path)).expanduser()
        lexical = path if path.is_absolute() else root / path
        if lexical.is_symlink():
            try:
                lexical_relative = lexical.absolute().relative_to(root)
            except ValueError:
                lexical_relative = Path()
            reason = (
                "runtime_owned_path_symlink"
                if lexical_relative.parts
                and lexical_relative.parts[0] == ".openpilot"
                else "mutation_path_symlink"
            )
            failures.append(f"{reason}:{lexical.absolute()}")
            continue
        canonical = path.resolve(strict=False)
        canonical_text = str(canonical)
        try:
            relative = canonical.relative_to(root)
        except ValueError:
            failures.append(f"mutation_path_outside_project:{raw_path}")
            continue
        is_openpilot = bool(relative.parts and relative.parts[0] == ".openpilot")
        is_sketch = canonical == sketch_path
        is_index = canonical.is_relative_to(index_root)
        if not is_sketch and not is_openpilot:
            user_paths.add(canonical_text)
            continue
        if is_openpilot and not is_index:
            failures.append(f"unknown_openpilot_path:{canonical_text}")
            continue
        expected = expected_by_path.get(canonical_text)
        if expected is None:
            failures.append(f"runtime_owned_not_expected_at_start:{canonical_text}")
            continue
        expected_type = "directory_sketch" if is_sketch else "file_content_index"
        if expected.get("artifact_type") != expected_type:
            failures.append(f"expected_artifact_manifest_invalid:{canonical_text}")
            continue
        if canonical_text in set(mutations.get("added_paths") or []) | set(
            mutations.get("deleted_paths") or []
        ):
            failures.append(f"runtime_owned_add_delete_forbidden:{canonical_text}")
            continue
        if not path.is_file():
            failures.append(f"runtime_owned_file_missing:{canonical_text}")
            continue
        if is_sketch:
            artifact_failures = _validate_sketch_against_final_project(
                root=root, sketch_path=path
            )
            failures.extend(f"{reason}:{canonical_text}" for reason in artifact_failures)
            base_validation["artifacts"].append(
                {
                    "path": canonical_text,
                    "artifact_type": "directory_sketch",
                    "passed": not artifact_failures,
                    "failures": artifact_failures,
                }
            )
            if not artifact_failures:
                runtime_paths.add(canonical_text)
            continue

        relative_value = expected.get("relative_path")
        if not isinstance(relative_value, str) or not relative_value:
            failures.append(f"expected_artifact_manifest_invalid:{canonical_text}")
            continue
        indexed_relative = Path(relative_value)
        if (
            indexed_relative.is_absolute()
            or ".." in indexed_relative.parts
            or indexed_relative.as_posix() != relative_value
        ):
            failures.append(f"runtime_owned_relative_path_invalid:{canonical_text}")
            continue
        target = (root / indexed_relative).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError:
            failures.append(f"runtime_owned_relative_path_invalid:{canonical_text}")
            continue
        expected_index = (
            index_root
            / indexed_relative.parent
            / f"{indexed_relative.name}.index.json"
        ).resolve(strict=False)
        if canonical != expected_index:
            failures.append(f"runtime_owned_index_path_mismatch:{canonical_text}")
            continue
        _, artifact_failures = _validate_index_against_final_file(
            root=root,
            index_path=path,
            relative_path=relative_value,
        )
        failures.extend(f"{reason}:{canonical_text}" for reason in artifact_failures)
        base_validation["artifacts"].append(
            {
                "path": canonical_text,
                "artifact_type": "file_content_index",
                "passed": not artifact_failures,
                "failures": artifact_failures,
            }
        )
        if not artifact_failures:
            runtime_paths.add(canonical_text)

    failures = list(dict.fromkeys([*manifest_failures, *failures]))
    producer_validation = {
        **base_validation,
        "passed": not failures,
        "failures": failures,
    }
    return {
        "runtime_owned_mutations": _filtered_mutation_paths(
            mutations, runtime_paths
        ),
        "user_owned_mutations": _filtered_mutation_paths(mutations, user_paths),
        "failures": failures,
        "producer_validation": producer_validation,
    }


def _divide_docstring_has_exact_sentence(source: str) -> bool:
    try:
        module = ast.parse(source)
    except (SyntaxError, UnicodeError):
        return False
    function = next(
        (
            node
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "divide"
        ),
        None,
    )
    docstring = ast.get_docstring(function, clean=True) if function is not None else None
    normalized = " ".join(str(docstring or "").split())
    return "Raises ValueError when denominator is zero." in normalized


def validate_divide_docstring(calculator_path: str | Path) -> list[str]:
    path = Path(calculator_path)
    if path.is_symlink():
        return ["calculator_is_symlink"]
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ["calculator_not_parseable"]
    if not _divide_docstring_has_exact_sentence(source):
        return ["divide_docstring_contract_missing"]
    return []


def verify_frozen_core_baseline(protocol: Mapping[str, Any]) -> dict[str, Any]:
    from stage8_task_designer_context_campaign import (
        load_campaign_protocol,
        load_frozen_task_designer_source,
    )

    stage8_protocol = load_campaign_protocol()
    frozen = load_frozen_task_designer_source(stage8_protocol)
    preview = next(
        (
            str(summary.get("preview") or "")
            for summary in frozen.project_state.file_summaries
            if summary.get("path") == "./calculator.py"
        ),
        "",
    )
    observation = {
        "source_campaign": stage8_protocol["campaign_id"],
        "source_fingerprint": frozen.source_fingerprint,
        "project_state_sequence": frozen.source_event_sequences["project_state"],
        "exact_sentence": "Raises ValueError when denominator is zero.",
        "exact_sentence_present": _divide_docstring_has_exact_sentence(preview),
    }
    if observation != protocol["core_pre_enhancement_baseline"]:
        raise ProviderSentinelError("frozen core baseline evidence mismatch")
    return observation


def arm_stop_reasons(record: Mapping[str, Any], protocol: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not (record.get("quality_gate") or {}).get("passed"):
        reasons.append("quality_gate_failed")
    item_scenario = str(record.get("scenario_id") or "")
    item_arm = str(record.get("arm") or "")
    if item_scenario not in POSITIVE_SCENARIO_IDS or item_arm not in ARMS:
        reasons.append("scenario_or_arm_mismatch")
    else:
        frozen = protocol["scenarios"][item_scenario]
        if record.get("source_fingerprint") != frozen["frozen_source_fingerprint"]:
            reasons.append("source_fingerprint_mismatch")
        if record.get("goal_hash") != frozen["frozen_goal_hash"]:
            reasons.append("goal_hash_mismatch")
        intervention = record.get("task_designer_context_intervention") or {}
        if (
            intervention.get("scenario_id") != item_scenario
            or intervention.get("projection_policy") != item_arm
            or intervention.get("source_fingerprint")
            != record.get("source_fingerprint")
            or intervention.get("goal_hash") != record.get("goal_hash")
        ):
            reasons.append("context_intervention_descriptor_mismatch")
        runtime_hashes = list(intervention.get("runtime_contract_hashes") or [])
        if (
            len(runtime_hashes) != 1
            or runtime_hashes[0] != record.get("runtime_contract_hash")
            or not record.get("runtime_contract_hash")
        ):
            reasons.append("runtime_contract_descriptor_mismatch")
        if intervention.get("sentinel_contract_passed") is not True:
            reasons.append("candidate_sentinel_contract_failed")
        if intervention.get("sentinel_contract") != protocol[
            "scenario_candidate_contracts"
        ][item_scenario][item_arm]:
            reasons.append("candidate_sentinel_contract_mismatch")
        sentinel_candidate_ids = set(
            intervention.get("sentinel_candidate_ids") or []
        )
        decisions = {
            str(decision.get("candidate_id") or ""): str(
                decision.get("action") or ""
            )
            for decision in (record.get("task_designer") or {}).get(
                "candidate_decisions"
            )
            or []
        }
        if not sentinel_candidate_ids or any(
            decisions.get(candidate_id) != "kept"
            for candidate_id in sentinel_candidate_ids
        ):
            reasons.append("candidate_sentinel_not_fully_kept")
    if record.get("projection_policy") != item_arm:
        reasons.append("projection_policy_mismatch")
    if record.get("provider_identity") != protocol["provider_identity"]:
        reasons.append("provider_identity_mismatch")
    if record.get("overall_usage_coverage") != 1.0:
        reasons.append("incomplete_usage_coverage")
    if int(record.get("unknown_failed_usage_count") or 0):
        reasons.append("unknown_failed_usage")
    if int(record.get("transport_retry_count") or 0):
        reasons.append("transport_retry_observed")
    guard = record.get("guard_observation")
    if not isinstance(guard, Mapping):
        reasons.append("guard_observation_missing")
    else:
        expected_default = int(
            protocol["common_interventions"]["default_max_completion_tokens"]
        )
        if int(guard.get("default_max_completion_tokens") or 0) != expected_default:
            reasons.append("guard_default_completion_mismatch")
        effective_limits = list(guard.get("effective_max_completion_tokens") or [])
        if (
            len(effective_limits)
            != int(guard.get("logical_complete_calls_seen") or 0)
            or any(int(limit or 0) <= 0 for limit in effective_limits)
        ):
            reasons.append("guard_effective_completion_missing")
        if guard.get("usage_censored") is True:
            reasons.append("guard_usage_censored")
        if int(guard.get("unsettled_reserved_tokens") or 0) > 0:
            reasons.append("guard_unsettled_reservation")
        if int(
            guard.get("reservation_overrun_tokens", guard.get("reservation_overrun", 0))
            or 0
        ) > 0:
            reasons.append("guard_reservation_overrun")
        if int(guard.get("blocked_requests_before_transport") or 0) > 0:
            reasons.append("guard_request_blocked")
        if (
            guard.get("reservation_admission_failed") is True
            or int(guard.get("blocked_reservation_tokens") or 0) > 0
            or "request_token_reservation_blocked"
            in set(guard.get("blocking_limits") or [])
        ):
            reasons.append("guard_reservation_admission_failed")
    observed = record.get("task_designer") or {}
    expected_call = protocol["task_designer_call_contract"]
    call_values = {
        "request_count": int(observed.get("request_count") or 0),
        "usage_coverage": 1.0 if observed.get("usage_observed") is True else 0.0,
        "reasoning_mode": str(observed.get("reasoning_mode") or ""),
        "transport_retries": int(record.get("transport_retry_count") or 0),
        "failed_attempt_count": int(observed.get("failed_attempt_count") or 0),
        "recovery_count": int(observed.get("recovery_count") or 0),
        "required_omitted_count": len(observed.get("omitted_required_candidate_ids") or []),
        "required_partial_count": int(observed.get("required_partial_count") or 0),
    }
    if call_values != expected_call:
        reasons.append("task_designer_call_contract_mismatch")
    total = int(((record.get("usage") or {}).get("lifecycle") or {}).get("total_tokens") or 0)
    if total > int(protocol["token_limits"]["per_arm_hard"]):
        reasons.append("per_arm_token_hard_limit_exceeded")
    mutations = record.get("observed_enhancement_mutations") or {}
    changed_paths = list(mutations.get("all_changed_paths") or [])
    if mutations.get("snapshot_missing") is not False:
        reasons.append("enhancement_snapshot_missing")
    if int(mutations.get("start_capture_count") or 0) != 1:
        reasons.append("enhancement_snapshot_capture_invalid")
    if mutations.get("start_consistent") is not True:
        reasons.append("enhancement_snapshot_inconsistent")
    if bool(mutations.get("before_truncated")) or bool(
        mutations.get("after_truncated")
    ):
        reasons.append("enhancement_snapshot_truncated")
    if bool(mutations.get("symlink_paths")):
        reasons.append("enhancement_snapshot_symlink")
    intervention = record.get("task_designer_context_intervention") or {}
    start_snapshot = intervention.get("enhancement_start_project_snapshot")
    derived_expected_artifacts = (
        expected_runtime_artifact_manifest(start_snapshot)
        if isinstance(start_snapshot, Mapping)
        else []
    )
    project_root = Path(str(record.get("project_root") or "")).resolve(strict=False)
    snapshot_root_matches = isinstance(start_snapshot, Mapping) and (
        Path(str(start_snapshot.get("project_root") or "")).resolve(strict=False)
        == project_root
    )
    if (
        not snapshot_root_matches
        or _sha256(start_snapshot)
        != intervention.get("enhancement_start_snapshot_fingerprint")
    ):
        reasons.append("enhancement_start_snapshot_descriptor_mismatch")
    if list(intervention.get("expected_runtime_artifact_manifest") or []) != (
        derived_expected_artifacts
    ):
        reasons.append("expected_runtime_artifact_manifest_mismatch")
    classification = classify_enhancement_mutations(
        str(record.get("project_root") or ""),
        mutations,
        expected_artifacts=derived_expected_artifacts,
    )
    if classification["producer_validation"]["raw_descriptor_failures"]:
        reasons.append("raw_mutation_descriptor_invalid")
    if classification["failures"]:
        reasons.append("runtime_owned_mutation_invalid")
    if (
        record.get("runtime_owned_mutations")
        != classification["runtime_owned_mutations"]
        or record.get("user_owned_mutations")
        != classification["user_owned_mutations"]
        or list(record.get("mutation_classification_failures") or [])
        != classification["failures"]
        or record.get("producer_validation")
        != classification["producer_validation"]
    ):
        reasons.append("mutation_classification_descriptor_mismatch")
    calculator_path = str(
        Path(record.get("calculator_path") or "").resolve(strict=False)
    )
    if list(
        classification["user_owned_mutations"].get("all_changed_paths") or []
    ) != [calculator_path]:
        reasons.append("observed_user_mutation_scope_mismatch")
    reasons.extend(
        validate_modified_paths(
            str(record.get("project_root") or ""),
            changed_paths,
        )
    )
    reasons.extend(validate_divide_docstring(str(record.get("calculator_path") or "")))
    return list(dict.fromkeys(reasons))


def evaluate_spend_limits(
    records: list[Mapping[str, Any]], protocol: Mapping[str, Any]
) -> dict[str, Any]:
    formal_pair_totals: dict[str, int] = {}
    formal_campaign_total = 0
    for record in records:
        tokens = int(
            (((record.get("usage") or {}).get("lifecycle") or {}).get("total_tokens"))
            or 0
        )
        pair = str(record.get("pair"))
        formal_pair_totals[pair] = formal_pair_totals.get(pair, 0) + tokens
        formal_campaign_total += tokens
    prior_pair_totals: dict[str, int] = {}
    prior_campaign_total = 0
    for diagnostic in protocol.get("prior_paid_diagnostics") or []:
        tokens = int(diagnostic.get("observed_complete_tokens") or 0)
        pair = str(diagnostic.get("pair"))
        prior_pair_totals[pair] = prior_pair_totals.get(pair, 0) + tokens
        prior_campaign_total += tokens
    cumulative_pair_totals = dict(prior_pair_totals)
    for pair, total in formal_pair_totals.items():
        cumulative_pair_totals[pair] = cumulative_pair_totals.get(pair, 0) + total
    cumulative_campaign_total = prior_campaign_total + formal_campaign_total
    pair_limit = int(protocol["token_limits"]["per_pair_hard"])
    pair_ids = {
        str(item.get("pair")) for item in protocol.get("schedule") or []
    } | set(cumulative_pair_totals)
    remaining_pair_tokens = {
        pair: pair_limit - int(cumulative_pair_totals.get(pair, 0))
        for pair in sorted(pair_ids, key=int)
    }
    remaining_campaign_tokens = int(
        protocol["token_limits"]["provider_sentinel_hard"]
    ) - cumulative_campaign_total
    hard = [
        f"pair_{pair}_hard_limit_exceeded"
        for pair, total in cumulative_pair_totals.items()
        if total > int(protocol["token_limits"]["per_pair_hard"])
    ]
    if cumulative_campaign_total > int(
        protocol["token_limits"]["provider_sentinel_hard"]
    ):
        hard.append("provider_sentinel_hard_limit_exceeded")
    return {
        "prior_paid_pair_totals": prior_pair_totals,
        "formal_pair_totals": formal_pair_totals,
        "cumulative_pair_totals": cumulative_pair_totals,
        "prior_paid_campaign_total": prior_campaign_total,
        "formal_campaign_total": formal_campaign_total,
        "cumulative_campaign_total": cumulative_campaign_total,
        "remaining_pair_tokens": remaining_pair_tokens,
        "remaining_campaign_tokens": remaining_campaign_tokens,
        "hard_failures": hard,
    }


def remaining_arm_hard_cap(
    records: list[Mapping[str, Any]],
    schedule_item: Mapping[str, Any],
    protocol: Mapping[str, Any],
) -> int:
    spend = evaluate_spend_limits(records, protocol)
    pair_spent = int(
        spend["cumulative_pair_totals"].get(str(schedule_item["pair"]), 0)
    )
    campaign_spent = int(spend["cumulative_campaign_total"])
    cap = min(
        int(protocol["token_limits"]["per_arm_hard"]),
        int(protocol["token_limits"]["per_pair_hard"]) - pair_spent,
        int(protocol["token_limits"]["provider_sentinel_hard"]) - campaign_spent,
    )
    if cap <= 0:
        raise ProviderSentinelStopped("no provider token budget remains for next arm")
    return cap


def build_run_command(
    output_dir: Path,
    schedule_item: Mapping[str, Any],
    protocol: Mapping[str, Any],
    *,
    records: list[Mapping[str, Any]],
    protocol_path: Path = PROTOCOL_PATH,
) -> list[str]:
    cap = remaining_arm_hard_cap(records, schedule_item, protocol)
    return [
        sys.executable,
        str(RUNNER_PATH),
        "--fixed-decomposition",
        "--improvement-requirement",
        "optional",
        "--enhancement-budget-arm",
        "dynamic",
        "--iteration-goal-mode",
        "provider",
        "--memory-mode",
        "isolated_empty",
        "--task-designer-context-arm",
        str(schedule_item["arm"]),
        "--task-designer-source-protocol",
        str(protocol_path.resolve()),
        "--task-designer-scenario-id",
        str(schedule_item["scenario_id"]),
        "--max-provider-tokens",
        str(cap),
        "--default-max-completion-tokens",
        str(protocol["common_interventions"]["default_max_completion_tokens"]),
        "--output-dir",
        str(output_dir),
    ]


def _task_designer_failed_attempt_count(events: list[dict[str, Any]]) -> int:
    purposes: dict[str, str] = {}
    for event in events:
        payload = event.get("payload") or {}
        execution_id = str(
            (payload.get("correlation") or {}).get("execution_id") or ""
        )
        if event.get("event_type") == "llm_requested" and execution_id:
            selection = payload.get("context_selection") or {}
            purposes[execution_id] = str(selection.get("request_purpose") or "")
    return sum(
        event.get("event_type") == "llm_failed"
        and purposes.get(
            str(
                ((event.get("payload") or {}).get("correlation") or {}).get(
                    "execution_id"
                )
                or ""
            )
        )
        == "iteration_task_design"
        for event in events
    )


def build_integrated_run_record(
    run_dir: Path,
    *,
    schedule_item: dict[str, Any],
    protocol: Mapping[str, Any],
    code_snapshot: str = "stage9-adapter-managed",
) -> dict[str, Any]:
    """Reuse Stage 7/8 evidence extraction and add Stage 9 contracts."""

    from run_observation import _load_events
    from stage7_campaign import build_run_record as build_stage7_run_record
    from stage8_task_designer_context_campaign import load_campaign_protocol

    stage8_protocol = load_campaign_protocol()
    stage7_protocol = {
        **stage8_protocol,
        "provider_identity": protocol["provider_identity"],
        "common_interventions": {
            "memory_baseline": {"strategy": "isolated_empty"}
        },
        "analysis": {
            **stage8_protocol["analysis"],
            "enhancement_purposes": [
                "project_improvement",
                "iteration_goal",
                "iteration_task_design",
                "code_generation",
                "code_edit",
            ],
        },
    }
    base = build_stage7_run_record(
        run_dir,
        schedule_item={**schedule_item, "arm": "dynamic"},
        code_snapshot=code_snapshot,
        protocol=stage7_protocol,
    )
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    events = _load_events(run_dir / "diagnostics")
    intervention = manifest.get("task_designer_context_intervention") or {}
    task_designer = task_designer_observation_with_decisions(events)
    reservations = (
        ((base.get("enhancement") or {}).get("completion_budget_audit") or {}).get(
            "reservations"
        )
        or []
    )
    task_designer["recovery_count"] = sum(
        reservation.get("purpose") == "iteration_task_design"
        and reservation.get("recovery_of") is not None
        for reservation in reservations
    )
    task_designer["failed_attempt_count"] = _task_designer_failed_attempt_count(
        events
    )
    outcome = manifest.get("outcome") or {}
    result = outcome.get("result") or {}
    runtime_state = result.get("agent_runtime_state") or {}
    project_root = Path(str(manifest.get("project_dir") or ""))
    observed_mutations = dict(manifest.get("observed_project_mutations") or {})
    enhancement_mutations = dict(
        manifest.get("observed_enhancement_mutations") or {}
    )
    start_snapshot = intervention.get("enhancement_start_project_snapshot")
    derived_expected_artifacts = (
        expected_runtime_artifact_manifest(start_snapshot)
        if isinstance(start_snapshot, Mapping)
        else []
    )
    mutation_classification = classify_enhancement_mutations(
        project_root,
        enhancement_mutations,
        expected_artifacts=derived_expected_artifacts,
    )
    runtime_hash = str(intervention.get("runtime_contract_hash") or "")
    runtime_hashes = list(intervention.get("runtime_contract_hashes") or [])
    if not runtime_hash and len(set(runtime_hashes)) == 1:
        runtime_hash = str(runtime_hashes[0])
    return {
        **base,
        **schedule_item,
        "provider_identity": manifest.get("provider_runtime_identity"),
        "guard_observation": manifest.get("guard_observation"),
        "projection_policy": intervention.get("projection_policy"),
        "source_fingerprint": intervention.get("source_fingerprint"),
        "goal_hash": intervention.get("goal_hash"),
        "runtime_contract_hash": runtime_hash,
        "task_designer_context_intervention": intervention,
        "task_designer": task_designer,
        "project_root": str(project_root),
        "modified_paths": list(enhancement_mutations.get("all_changed_paths") or []),
        "runtime_reported_modified_paths_descriptive": list(
            runtime_state.get("modified_files") or []
        ),
        "observed_project_mutations": observed_mutations,
        "observed_enhancement_mutations": enhancement_mutations,
        "runtime_owned_mutations": mutation_classification[
            "runtime_owned_mutations"
        ],
        "user_owned_mutations": mutation_classification["user_owned_mutations"],
        "mutation_classification_failures": mutation_classification["failures"],
        "producer_validation": mutation_classification["producer_validation"],
        "calculator_path": str(project_root / "calculator.py"),
    }


def pair_stop_reasons(
    records: list[Mapping[str, Any]], *, pair: int
) -> list[str]:
    pair_records = [record for record in records if int(record.get("pair") or 0) == pair]
    if len(pair_records) < 2:
        return []
    if len(pair_records) != 2 or {record.get("arm") for record in pair_records} != set(ARMS):
        return ["pair_arm_membership_mismatch"]
    runtime_hashes = {str(record.get("runtime_contract_hash") or "") for record in pair_records}
    return [] if len(runtime_hashes) == 1 and "" not in runtime_hashes else [
        "runtime_contract_hash_mismatch"
    ]


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def preflight(
    *,
    protocol: Mapping[str, Any] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    del output_dir  # The preflight is intentionally read-only.
    frozen = dict(protocol or load_protocol())
    validate_protocol(frozen)
    offline_runtime = offline_report_snapshot(run_offline_sentinel())
    offline_committed = load_offline_report()
    offline_passed = offline_runtime == offline_committed and bool(
        offline_runtime.get("passed")
    )
    if not offline_passed:
        raise ProviderSentinelError("offline Stage 9 gate is not frozen and passing")
    from core.config import LLMSettings
    from run_observation import provider_runtime_identity

    runtime_identity = provider_runtime_identity(LLMSettings())
    identity_matches = runtime_identity == frozen["provider_identity"]
    if not identity_matches:
        raise ProviderSentinelError("configured provider identity does not match")
    baseline = verify_frozen_core_baseline(frozen)
    return {
        "campaign_id": frozen["campaign_id"],
        "provider_calls": 0,
        "offline_gate_passed": True,
        "execute_requires_explicit_flag": frozen["execute_requires_explicit_flag"],
        "runner_integration_ready": bool(
            frozen["execution"]["runner_integration_ready"]
        ),
        "provider_identity": frozen["provider_identity"],
        "provider_identity_matches": identity_matches,
        "core_pre_enhancement_baseline": baseline,
        "prior_paid_diagnostics": validate_prior_paid_diagnostics(frozen),
        "initial_spend": evaluate_spend_limits([], frozen),
        "token_limits": frozen["token_limits"],
        "schedule": build_schedule(frozen),
    }


def execute_campaign(
    *,
    output_dir: Path,
    run_arm: RunArm | None = None,
    protocol: Mapping[str, Any] | None = None,
    protocol_path: Path = PROTOCOL_PATH,
) -> dict[str, Any]:
    frozen = dict(protocol or load_protocol())
    actual_protocol_path = protocol_path.resolve()
    if load_protocol(actual_protocol_path) != frozen:
        raise ProviderSentinelError(
            "parent protocol object and subprocess protocol path differ"
        )
    preflight(protocol=frozen, output_dir=output_dir)
    from stage7_campaign import code_snapshot_sha256
    from stage8_task_designer_context_campaign import load_campaign_protocol

    snapshot_protocol = load_campaign_protocol()
    code_snapshot = code_snapshot_sha256(snapshot_protocol)
    output_dir.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    state_path = output_dir / "campaign_state.json"

    def persist_state(status: str, stop_reasons: list[str]) -> None:
        _write_json_atomic(
            state_path,
            {
                "campaign_id": frozen["campaign_id"],
                "status": status,
                "records": records,
                "spend": evaluate_spend_limits(records, frozen),
                "stop_reasons": list(dict.fromkeys(stop_reasons)),
            },
        )

    persist_state("running", [])
    try:
        for item in build_schedule(frozen):
            if code_snapshot_sha256(snapshot_protocol) != code_snapshot:
                raise ProviderSentinelStopped(
                    "campaign code snapshot changed before arm"
                )
            run_dir = output_dir / (
                f"{item['ordinal']:02d}_p{item['pair']}_{item['scenario_id']}_{item['arm']}"
            )
            if run_arm is None:
                command = build_run_command(
                    run_dir,
                    item,
                    frozen,
                    records=records,
                    protocol_path=actual_protocol_path,
                )
                completed = subprocess.run(
                    command,
                    check=False,
                    timeout=int(frozen["execution"]["per_arm_wall_clock_seconds"]),
                )
                if not (run_dir / "manifest.json").is_file():
                    raise ProviderSentinelStopped(
                        "provider sentinel arm produced no manifest: "
                        f"returncode={completed.returncode}"
                    )
                if code_snapshot_sha256(snapshot_protocol) != code_snapshot:
                    raise ProviderSentinelStopped(
                        "campaign code snapshot changed during arm"
                    )
                record = build_integrated_run_record(
                    run_dir,
                    schedule_item=item,
                    protocol=frozen,
                    code_snapshot=code_snapshot,
                )
            else:
                record = run_arm(item, run_dir, frozen)
            records.append(record)
            run_dir.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(run_dir / "campaign_record.json", record)
            reasons = arm_stop_reasons(record, frozen)
            reasons.extend(
                evaluate_spend_limits(records, frozen)["hard_failures"]
            )
            reasons.extend(pair_stop_reasons(records, pair=int(item["pair"])))
            persist_state("stopped" if reasons else "running", reasons)
            if reasons:
                raise ProviderSentinelStopped(
                    "provider sentinel stopped after arm: "
                    + ", ".join(dict.fromkeys(reasons))
                )
    except Exception as exc:
        current = json.loads(state_path.read_text(encoding="utf-8"))
        if current.get("status") != "stopped":
            persist_state("stopped", [f"{type(exc).__name__}: {exc}"])
        raise
    persist_state("completed", [])
    return {
        "campaign_id": frozen["campaign_id"],
        "eligible": len(records) == 6,
        "records": records,
        "spend_limits": evaluate_spend_limits(records, frozen),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    protocol = load_protocol(args.protocol)
    plan = preflight(protocol=protocol, output_dir=args.output_dir)
    if args.execute:
        if not plan["runner_integration_ready"]:
            raise SystemExit(
                "Stage 9 provider execution is disabled until the hardened "
                "run_observation arm adapter is connected"
            )
        if args.output_dir is None:
            raise SystemExit("--output-dir is required for paid Stage 9 execution")
        result = execute_campaign(
            output_dir=args.output_dir.resolve(),
            protocol=protocol,
            protocol_path=args.protocol,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["eligible"] else 4
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
