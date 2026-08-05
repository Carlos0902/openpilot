"""Zero-provider sentinel gate for Stage 9 Task Designer scenarios.

This module freezes typed source fixtures and exercises the production current and
compact projection adapters.  It deliberately has no provider execution path.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from autonomous_iteration.models import ImprovementGoal, ProjectStateSnapshot
from autonomous_iteration.project_improvement_context import (
    build_iteration_task_design_candidates,
    compact_project_memory_record,
)
from memory.memory_models import MemoryRecord, MemoryType
from memory.context_assembly import ContextAssembler
from metadata import (
    ContextAssemblyPolicy,
    ContextCandidateRetention,
    ContextCandidateTruncation,
    ContextRequestPurpose,
    ImprovementCandidateMetadata,
    ProductIntentMetadata,
    ProjectDiagnosisMetadata,
    ProjectDimensionAssessmentMetadata,
    ProjectObjectiveMetadata,
    SuccessMetricMetadata,
)


HERE = Path(__file__).resolve().parent
PROTOCOL_PATH = HERE / "STAGE9_TASK_DESIGNER_SCENARIO_CANARY_V2.json"
OFFLINE_REPORT_PATH = HERE / "STAGE9_TASK_DESIGNER_SCENARIO_CANARY_V2_OFFLINE_RESULT.json"
SCENARIO_IDS = (
    "unrelated_diagnosis",
    "strongly_related_diagnosis",
    "partial_shared_criterion",
    "relevant_iteration_memory",
)
POSITIVE_SCENARIO_IDS = SCENARIO_IDS[1:]
ARMS = ("current", "compact")
_CREATED_AT = "2026-08-04T00:00:00+00:00"
_PROJECT_PATH = "."
_TARGETS = ["./calculator.py"]
_VALIDATION_COMMANDS = [
    "python -m pytest -q",
    "python -m compileall -q calculator.py",
]
_DOCSTRING_ACCEPTANCE_CRITERIA = [
    "divide has a docstring containing the exact sentence: Raises ValueError when denominator is zero.",
    "calculator.py remains the only modified file.",
    "python -m pytest -q passes.",
    "python -m compileall -q calculator.py succeeds.",
]


class ScenarioGateError(RuntimeError):
    """A frozen scenario or arm violates the Stage 9 isolation contract."""


class StableLexicalTokenCounter:
    """Offline deterministic counter used to exercise token-budget assembly."""

    available = True
    tokenizer_id = "stage9-stable-lexical-v1"
    model = "offline-gate"

    @staticmethod
    def count_text(text: str) -> int:
        return len(re.findall(r"[\w]+|[^\w\s]", str(text), flags=re.UNICODE))


@dataclass(frozen=True)
class ScenarioFixture:
    scenario_id: str
    project_state: ProjectStateSnapshot
    improvement_report: dict[str, Any]
    goal: ImprovementGoal
    completed_iteration: int
    expected_compact_sentinels: tuple[str, ...]
    forbidden_compact_sentinels: tuple[str, ...]
    source_fingerprint: str
    goal_hash: str
    contract_hash: str


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical_bytes(value)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def load_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_offline_report(path: Path = OFFLINE_REPORT_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_protocol(protocol: Mapping[str, Any]) -> None:
    if protocol.get("campaign_id") != "stage9-task-designer-scenario-canary-v2":
        raise ScenarioGateError("unexpected Stage 9 campaign id")
    phases = protocol.get("phases") or {}
    if phases.get("offline_sentinel") != {
        "provider_calls": 0,
        "required_scenarios": list(SCENARIO_IDS),
    }:
        raise ScenarioGateError("offline sentinel phase contract changed")
    provider_sentinel = phases.get("provider_sentinel") or {}
    confirmatory = phases.get("confirmatory") or {}
    paid_scenarios = [
        "strongly_related_diagnosis",
        "partial_shared_criterion",
        "relevant_iteration_memory",
    ]
    if provider_sentinel != {
        "scenarios": paid_scenarios,
        "arms": list(ARMS),
        "runs": 6,
        "requires_offline_pass": True,
        "execute_requires_explicit_flag": True,
    }:
        raise ScenarioGateError("provider sentinel phase contract changed")
    if confirmatory != {
        "scenarios": paid_scenarios,
        "arms": list(ARMS),
        "runs": 6,
        "requires_provider_sentinel_pass": True,
        "execute_requires_explicit_flag": True,
    }:
        raise ScenarioGateError("confirmatory phase contract changed")
    if tuple((protocol.get("scenarios") or {}).keys()) != SCENARIO_IDS:
        raise ScenarioGateError("Stage 9 scenario order or membership changed")
    limits = protocol.get("token_limits") or {}
    if limits != {
        "per_arm_hard": 30000,
        "per_pair_hard": 60000,
        "provider_sentinel_hard": 180000,
        "confirmatory_hard": 180000,
        "campaign_hard": 360000,
    }:
        raise ScenarioGateError("confirmatory token budget changed")


def _base_fields() -> dict[str, Any]:
    return {"created_at": _CREATED_AT}


def _product_intent() -> ProductIntentMetadata:
    return ProductIntentMetadata(
        **_base_fields(),
        experience_type="python_library",
        runtime_mode="library_tests",
        delivery_surface="project_native",
        target_platforms=["python"],
        core_capabilities=["safe arithmetic"],
        non_regression_constraints=["preserve public arithmetic behavior"],
        disallowed_substitutions=["do not replace Python implementation"],
        evidence=["validation:pytest", "project_file:calculator.py"],
        confidence=1.0,
    )


def _docstring_goal(goal_id: str) -> ImprovementGoal:
    return ImprovementGoal(
        id=goal_id,
        title="Document divide's denominator-zero contract.",
        category="documentation",
        rationale=(
            "The repaired public function should explain its typed domain error "
            "without changing tests or unrelated symbols."
        ),
        acceptance_criteria=list(_DOCSTRING_ACCEPTANCE_CRITERIA),
        priority="high",
    )


def _diagnosis(
    *,
    goal: ImprovementGoal,
    candidate_id: str,
    title: str,
    sentinel: str,
    acceptance_criteria: list[str],
    selected_metric_sentinel: str = "S9_SELECTED_METRIC",
) -> ProjectDiagnosisMetadata:
    selected = ImprovementCandidateMetadata(
        **_base_fields(),
        candidate_id=candidate_id,
        title=title,
        dimension="robustness",
        rationale=f"Typed scenario evidence {sentinel}",
        acceptance_criteria=acceptance_criteria,
        target_metrics=[
            f"all validation commands pass ({selected_metric_sentinel})"
        ],
        risks=["do not widen mutation scope"],
        evidence=[sentinel, "project_file:calculator.py"],
        selected=True,
    )
    objective = ProjectObjectiveMetadata(
        **_base_fields(),
        goal=goal.title,
        delivery_surface="project_native",
        core_value=["safe arithmetic"],
        constraints=["calculator.py is the only writable target"],
        evidence=["goal:" + goal.id],
        confidence=1.0,
    )
    return ProjectDiagnosisMetadata(
        **_base_fields(),
        project_path=_PROJECT_PATH,
        iteration=0,
        objective=objective,
        success_metrics=[
            SuccessMetricMetadata(
                **_base_fields(),
                metric_id="unrelated-render-latency",
                name="Unrelated UI render latency",
                dimension="performance",
                target="S9_UNRELATED_METRIC",
                current_assessment="not relevant to the selected arithmetic goal",
                evidence=["S9_UNRELATED_METRIC"],
            )
        ],
        dimension_assessments=[
            ProjectDimensionAssessmentMetadata(
                **_base_fields(),
                dimension="performance",
                summary="S9_UNRELATED_DIMENSION",
                gaps=["S9_UNRELATED_DIMENSION"],
                evidence=["S9_UNRELATED_METRIC"],
            )
        ],
        improvement_candidates=[selected],
        ranked_candidate_ids=[candidate_id],
        selected_candidate=selected,
        summary=f"Frozen diagnosis summary {sentinel}",
        confidence=1.0,
    )


def serialize_memory_for_project_state(record: MemoryRecord) -> dict[str, Any]:
    """Mirror project_state_reader `_memory_mapping` plus production compaction."""

    reader_mapping = {
        "id": record.id,
        "type": record.memory_type.value,
        "content": record.content,
        "tags": record.tags,
        "confidence": record.confidence,
        "timestamp": record.timestamp,
        "attributes": record.attributes,
    }
    return compact_project_memory_record(reader_mapping)


def _memory_records(*, goal: ImprovementGoal, scenario_memory: bool) -> list[dict[str, Any]]:
    records = [
        MemoryRecord(
            id="stage9-environment-noise",
            memory_type=MemoryType.PROJECT,
            content="Environment-only evidence S9_ENVIRONMENT_NOISE",
            tags=["project_environment"],
            timestamp="2026-08-03T00:00:00+00:00",
            confidence=1.0,
            attributes={"project_path": _PROJECT_PATH, "success": True},
        )
    ]
    if scenario_memory:
        memory_specs = [
            (
                "stage9-old-relevant-iteration-result",
                "S9_OLD_RELEVANT_ITERATION_RESULT",
                "2026-08-01T00:00:00+00:00",
                goal.id,
                goal.title,
            ),
            (
                "stage9-new-unrelated-iteration-result",
                "S9_NEW_UNRELATED_ITERATION_RESULT",
                "2026-08-03T00:00:00+00:00",
                "stage9-unrelated-candidate",
                "Unrelated renderer cleanup",
            ),
            (
                "stage9-latest-relevant-iteration-result",
                "S9_LATEST_RELEVANT_ITERATION_RESULT",
                "2026-08-04T00:00:00+00:00",
                goal.id,
                goal.title,
            ),
        ]
        for record_id, sentinel, timestamp, candidate_id, candidate_title in memory_specs:
            records.append(
                MemoryRecord(
                    id=record_id,
                    memory_type=MemoryType.TASK,
                    content=f"Autonomous iteration result memory: {sentinel}",
                    tags=["autonomous_iteration", "iteration_result"],
                    timestamp=timestamp,
                    confidence=1.0,
                    attributes={
                        "project_path": _PROJECT_PATH,
                        "selected_candidate_id": candidate_id,
                        "selected_candidate": candidate_title,
                        "success": True,
                        "run_command": _VALIDATION_COMMANDS[0],
                    },
                )
            )
    return [serialize_memory_for_project_state(record) for record in records]


def _contract_payload(goal: ImprovementGoal, state: ProjectStateSnapshot) -> dict[str, Any]:
    validation = dict(state.validation_context)
    return {
        "goal": goal.model_dump(mode="json"),
        "safe_target_files": list(state.safe_target_files),
        "validation_commands": list(validation.get("required_commands") or []),
        "validation_passed": validation.get("validation_passed"),
        "product_intent": validation.get("product_intent"),
    }


def _source_payload(
    *,
    scenario_id: str,
    goal: ImprovementGoal,
    state: ProjectStateSnapshot,
    report: Mapping[str, Any],
    completed_iteration: int,
) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "goal": goal.model_dump(mode="json"),
        "project_state": state.model_dump(mode="json"),
        "improvement_report": dict(report),
        "completed_iteration": completed_iteration,
    }


def build_scenario_fixtures() -> dict[str, ScenarioFixture]:
    definitions = {
        "unrelated_diagnosis": {
            "goal": ImprovementGoal(
                id="stage9-divide-docstring",
                title="Document divide's denominator-zero contract.",
                category="documentation",
                acceptance_criteria=["divide documents ValueError for a zero denominator"],
                priority="high",
            ),
            "candidate_id": "stage9-cleanup-logging",
            "candidate_title": "Reduce verbose debug logging",
            "diagnosis_sentinel": "S9_UNRELATED_DIAGNOSIS",
            "candidate_criteria": ["debug output is concise"],
            "memory": False,
            "expected": (),
            "forbidden": (
                "S9_UNRELATED_DIAGNOSIS",
                "S9_ENVIRONMENT_NOISE",
            ),
        },
        "strongly_related_diagnosis": {
            "goal": _docstring_goal("stage9-divide-docstring-strong"),
            "candidate_id": "stage9-divide-docstring-strong",
            "candidate_title": "Document divide's denominator-zero contract.",
            "diagnosis_sentinel": "S9_STRONG_DIAGNOSIS",
            "candidate_criteria": list(_DOCSTRING_ACCEPTANCE_CRITERIA),
            "memory": False,
            "expected": ("S9_STRONG_DIAGNOSIS",),
            "forbidden": (
                "S9_ENVIRONMENT_NOISE",
                "S9_UNRELATED_DIMENSION",
                "S9_UNRELATED_METRIC",
            ),
        },
        "partial_shared_criterion": {
            "goal": _docstring_goal("stage9-divide-docstring-partial"),
            "candidate_id": "stage9-arithmetic-doc-consistency",
            "candidate_title": "Improve arithmetic API documentation consistency",
            "diagnosis_sentinel": "S9_PARTIAL_DIAGNOSIS",
            "candidate_criteria": [
                _DOCSTRING_ACCEPTANCE_CRITERIA[0],
                "arithmetic APIs use consistent error documentation",
            ],
            "memory": False,
            "expected": ("S9_PARTIAL_DIAGNOSIS", "S9_SELECTED_METRIC"),
            "forbidden": (
                "S9_ENVIRONMENT_NOISE",
                "S9_UNRELATED_DIMENSION",
                "S9_UNRELATED_METRIC",
            ),
        },
        "relevant_iteration_memory": {
            "goal": _docstring_goal("stage9-divide-docstring-memory"),
            "candidate_id": "stage9-unrelated-readme-cleanup",
            "candidate_title": "Clean up README examples",
            "diagnosis_sentinel": "S9_MEMORY_UNRELATED_DIAGNOSIS",
            "candidate_criteria": ["README examples are concise"],
            "memory": True,
            "expected": ("S9_LATEST_RELEVANT_ITERATION_RESULT",),
            "forbidden": (
                "S9_MEMORY_UNRELATED_DIAGNOSIS",
                "S9_ENVIRONMENT_NOISE",
                "S9_OLD_RELEVANT_ITERATION_RESULT",
                "S9_NEW_UNRELATED_ITERATION_RESULT",
            ),
        },
    }
    fixtures: dict[str, ScenarioFixture] = {}
    for scenario_id in SCENARIO_IDS:
        definition = definitions[scenario_id]
        goal = definition["goal"]
        diagnosis = _diagnosis(
            goal=goal,
            candidate_id=definition["candidate_id"],
            title=definition["candidate_title"],
            sentinel=definition["diagnosis_sentinel"],
            acceptance_criteria=definition["candidate_criteria"],
        )
        product_intent = _product_intent().model_dump(mode="json")
        state = ProjectStateSnapshot(
            project_path=_PROJECT_PATH,
            goal=goal.title,
            file_summaries=[
                {
                    "path": "./calculator.py",
                    "preview": "def divide(a, b): ...",
                    "sha256": "stage9-fixed-calculator-source",
                }
            ],
            run_command=_VALIDATION_COMMANDS[0],
            memory_records=_memory_records(
                goal=goal, scenario_memory=definition["memory"]
            ),
            validation_context={
                "validation_passed": True,
                "required_commands": list(_VALIDATION_COMMANDS),
                "product_intent": product_intent,
                "quality_sentinel": "S9_QUALITY_CONTRACT",
            },
            safe_target_files=list(_TARGETS),
        )
        report = {
            "diagnosis": diagnosis.model_dump(mode="json"),
            "prompt_context": {
                "allowed_write_files": list(_TARGETS),
                "required_validation_commands": list(_VALIDATION_COMMANDS),
                "product_intent": product_intent,
            },
        }
        completed_iteration = 0
        source_payload = _source_payload(
            scenario_id=scenario_id,
            goal=goal,
            state=state,
            report=report,
            completed_iteration=completed_iteration,
        )
        contract_payload = _contract_payload(goal, state)
        fixtures[scenario_id] = ScenarioFixture(
            scenario_id=scenario_id,
            project_state=state,
            improvement_report=report,
            goal=goal,
            completed_iteration=completed_iteration,
            expected_compact_sentinels=definition["expected"],
            forbidden_compact_sentinels=definition["forbidden"],
            source_fingerprint=_sha256(source_payload),
            goal_hash=_sha256(goal.model_dump(mode="json")),
            contract_hash=_sha256(contract_payload),
        )
    return fixtures


def _render_candidates(candidates: list[Any]) -> str:
    return "\n\n".join(
        f"[evidence_id={candidate.candidate_id}]\n{candidate.content}"
        for candidate in candidates
    )


def _arm_record(
    fixture: ScenarioFixture,
    arm: str,
    source_candidates: list[Any],
    assembly: Any,
) -> dict[str, Any]:
    rendered = assembly.prompt_text
    observed = [
        sentinel
        for sentinel in fixture.expected_compact_sentinels
        if sentinel in rendered
    ]
    required = [
        {
            "candidate_id": candidate.candidate_id,
            "content": candidate.content,
        }
        for candidate in source_candidates
        if candidate.retention == ContextCandidateRetention.REQUIRED
    ]
    decision_by_id = {
        decision.candidate_id: decision
        for decision in assembly.selection.candidate_decisions
    }
    protected_failures = []
    for candidate in source_candidates:
        if not (
            candidate.retention == ContextCandidateRetention.REQUIRED
            or candidate.truncation == ContextCandidateTruncation.FORBIDDEN
        ):
            continue
        decision = decision_by_id[candidate.candidate_id]
        if decision.action != "kept":
            protected_failures.append(candidate.candidate_id)
    serialized_decisions = [
        decision.model_dump(mode="json")
        for decision in assembly.selection.candidate_decisions
    ]
    return {
        "arm": arm,
        "source_fingerprint": fixture.source_fingerprint,
        "goal_hash": fixture.goal_hash,
        "contract_hash": fixture.contract_hash,
        "content_fingerprint": _sha256(rendered.encode("utf-8")),
        "required_content_fingerprint": _sha256(required),
        "candidate_count": len(source_candidates),
        "selected_candidate_count": len(assembly.selected_candidates),
        "final_prompt_tokens": assembly.selection.final_prompt_tokens,
        "assembly_status": str(assembly.selection.assembly_status),
        "candidate_decisions": serialized_decisions,
        "candidate_decisions_fingerprint": _sha256(serialized_decisions),
        "selected_content_fingerprints": {
            candidate.candidate_id: _sha256(candidate.content.encode("utf-8"))
            for candidate in assembly.selected_candidates
        },
        "protected_candidate_failures": protected_failures,
        "observed_optional_sentinels": observed,
        "rendered_content": rendered,
    }


def assemble_scenario_arms(
    fixture: ScenarioFixture,
    *,
    runtime_goal: ImprovementGoal | None = None,
    runtime_project_state: ProjectStateSnapshot | None = None,
) -> dict[str, dict[str, Any]]:
    goal = runtime_goal or fixture.goal
    state = runtime_project_state or fixture.project_state
    if _sha256(goal.model_dump(mode="json")) != fixture.goal_hash:
        raise ScenarioGateError("runtime goal hash mismatch")
    if _sha256(_contract_payload(goal, state)) != fixture.contract_hash:
        raise ScenarioGateError("runtime contract hash mismatch")
    arms: dict[str, dict[str, Any]] = {}
    token_counter = StableLexicalTokenCounter()
    assembler = ContextAssembler(
        renderer=_render_candidates,
        token_counter=token_counter,
    )
    policy = ContextAssemblyPolicy(
        purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
        max_prompt_chars=250_000,
        max_prompt_tokens=50_000,
    )
    for arm in ARMS:
        candidates = build_iteration_task_design_candidates(
            project_state=state,
            goal=goal,
            improvement_report=fixture.improvement_report,
            completed_iteration=fixture.completed_iteration,
            projection_policy=arm,
        )
        assembly = assembler.assemble_candidates(
            candidates,
            policy=policy,
            renderer=_render_candidates,
        )
        arms[arm] = _arm_record(fixture, arm, candidates, assembly)
    if arms["current"]["required_content_fingerprint"] != arms["compact"][
        "required_content_fingerprint"
    ]:
        raise ScenarioGateError("required candidate content differs between arms")
    return arms


def run_offline_sentinel(
    protocol: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    frozen_protocol = dict(protocol or load_protocol())
    validate_protocol(frozen_protocol)
    fixtures = build_scenario_fixtures()
    scenario_results: dict[str, Any] = {}
    for scenario_id in SCENARIO_IDS:
        fixture = fixtures[scenario_id]
        expected = frozen_protocol["scenarios"][scenario_id]
        if expected.get("frozen_source_fingerprint") != fixture.source_fingerprint:
            raise ScenarioGateError(
                f"frozen source fingerprint mismatch for {scenario_id}"
            )
        if expected.get("frozen_goal_hash") != fixture.goal_hash:
            raise ScenarioGateError(f"frozen goal hash mismatch for {scenario_id}")
        if expected.get("frozen_contract_hash") != fixture.contract_hash:
            raise ScenarioGateError(f"frozen contract hash mismatch for {scenario_id}")
        if expected.get("expected_compact_sentinels") != list(
            fixture.expected_compact_sentinels
        ):
            raise ScenarioGateError(f"expected sentinel contract mismatch for {scenario_id}")
        if expected.get("forbidden_compact_sentinels") != list(
            fixture.forbidden_compact_sentinels
        ):
            raise ScenarioGateError(f"forbidden sentinel contract mismatch for {scenario_id}")
        arms = assemble_scenario_arms(fixture)
        compact_content = arms["compact"]["rendered_content"]
        observed = arms["compact"]["observed_optional_sentinels"]
        reasons: list[str] = []
        if observed != list(fixture.expected_compact_sentinels):
            reasons.append("required_compact_sentinel_missing")
        if any(item in compact_content for item in fixture.forbidden_compact_sentinels):
            reasons.append("forbidden_compact_sentinel_present")
        if arms["current"]["contract_hash"] != arms["compact"]["contract_hash"]:
            reasons.append("arm_contract_mismatch")
        if arms["current"]["protected_candidate_failures"]:
            reasons.append("current_protected_candidate_not_kept")
        if arms["compact"]["protected_candidate_failures"]:
            reasons.append("compact_protected_candidate_not_kept")
        current_tokens = int(arms["current"]["final_prompt_tokens"] or 0)
        compact_tokens = int(arms["compact"]["final_prompt_tokens"] or 0)
        reduction = (
            (current_tokens - compact_tokens) / current_tokens
            if current_tokens > 0
            else 0.0
        )
        if compact_tokens >= current_tokens:
            reasons.append("compact_tokens_not_lower")
        if reduction < 0.10:
            reasons.append("compact_reduction_below_initial_gate")
        scenario_results[scenario_id] = {
            "passed": not reasons,
            "reasons": reasons,
            "input_reduction_fraction": reduction,
            "forbidden_compact_sentinels": list(fixture.forbidden_compact_sentinels),
            **arms,
        }
    return {
        "campaign_id": frozen_protocol["campaign_id"],
        "phase": "offline_sentinel",
        "provider_calls": 0,
        "token_counter": {
            "tokenizer_id": StableLexicalTokenCounter.tokenizer_id,
            "model": StableLexicalTokenCounter.model,
        },
        "passed": all(item["passed"] for item in scenario_results.values()),
        "scenarios": scenario_results,
    }


def offline_report_snapshot(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable, reviewable subset committed as offline evidence."""

    scenarios: dict[str, Any] = {}
    for scenario_id in SCENARIO_IDS:
        source = report["scenarios"][scenario_id]
        arms: dict[str, Any] = {}
        for arm in ARMS:
            record = source[arm]
            arms[arm] = {
                "source_fingerprint": record["source_fingerprint"],
                "goal_hash": record["goal_hash"],
                "contract_hash": record["contract_hash"],
                "content_fingerprint": record["content_fingerprint"],
                "candidate_decisions_fingerprint": record[
                    "candidate_decisions_fingerprint"
                ],
                "candidate_count": record["candidate_count"],
                "selected_candidate_count": record["selected_candidate_count"],
                "final_prompt_tokens": record["final_prompt_tokens"],
                "assembly_status": record["assembly_status"],
                "protected_candidate_failures": record[
                    "protected_candidate_failures"
                ],
                "observed_optional_sentinels": record[
                    "observed_optional_sentinels"
                ],
            }
        scenarios[scenario_id] = {
            "passed": source["passed"],
            "reasons": source["reasons"],
            "input_reduction_fraction": source["input_reduction_fraction"],
            **arms,
        }
    return {
        "campaign_id": report["campaign_id"],
        "phase": report["phase"],
        "provider_calls": report["provider_calls"],
        "passed": report["passed"],
        "token_counter": report["token_counter"],
        "scenarios": scenarios,
    }


if __name__ == "__main__":
    snapshot = offline_report_snapshot(run_offline_sentinel())
    if snapshot != load_offline_report():
        raise SystemExit("committed Stage 9 offline report does not match runtime")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
