"""Deterministic offline A/B replay for project-improvement context assembly."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from autonomous_iteration.models import ImprovementGoal, ProjectStateSnapshot
from autonomous_iteration.project_improvement_context import (
    build_iteration_task_design_candidates,
)
from core.token_counting import ProviderTokenCounter
from memory.context_assembly.assembler import ContextAssembler
from metadata import (
    ContextAssemblyPolicy,
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ContextRequestPurpose,
)


HERE = Path(__file__).resolve().parent
PROTOCOL_PATH = HERE / "PROJECT_IMPROVEMENT_CONTEXT_AB_PROTOCOL_V1.json"

# This is an experiment-owned reconstruction of the historical request template.
# It is deliberately immutable and does not import code from git history.
LEGACY_TEMPLATE_V1 = (
    "You are OpenPilot's Task Designer Agent. Convert one improvement goal into 1-2 "
    "specific implementation tasks with target files and acceptance criteria. Return ONLY JSON.\n"
    "Carry forward the Prompt Context/rubric from the improvement report exactly. Do not dilute "
    "a product-fit migration goal into terminal-only polish tasks. Assess UI impact for every "
    "feature task: when behavior is user-facing, include the corresponding controls, visible states, "
    "feedback, navigation, and frontend target files required by the persisted stack preset. "
    "Do not silently change frontend/backend languages or frameworks; request an explicit stack preset revision.\n\n"
    "Completed successful improvements: {{completed_iteration}}\n"
    "Selected goal JSON: {{goal_json}}\n"
    "Project state JSON: {{state_json}}\n"
    "Improvement report JSON: {{report_json}}\n\n"
    'Return: {"tasks": [{"id":"task_1","goal_id":"goal_1","description":"specific implementation task","target_files":["path"],"acceptance_criteria":["observable criterion"],"risk_notes":["risk or empty"]}]}'
)


class FrozenInputError(RuntimeError):
    """The replay source or a frozen experiment asset is not authoritative."""


class HardGateFailure(RuntimeError):
    """A correctness condition makes the experiment arm inadmissible."""


@dataclass(frozen=True)
class FrozenInputs:
    project_state: ProjectStateSnapshot
    context_loader_context: dict[str, Any]
    improvement_report: dict[str, Any]
    selected_goal: ImprovementGoal
    completed_iteration: int
    source_event_sequences: dict[str, int]


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def load_protocol() -> dict[str, Any]:
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def load_frozen_inputs(protocol: Mapping[str, Any]) -> FrozenInputs:
    source_run = protocol["source_run"]
    events_path = HERE / str(source_run["events_path"])
    event_bytes = events_path.read_bytes()
    actual_file_hash = _sha256_bytes(event_bytes)
    if actual_file_hash != source_run["events_sha256"]:
        raise FrozenInputError(
            f"events file hash mismatch: expected {source_run['events_sha256']}, got {actual_file_hash}"
        )

    events: dict[int, dict[str, Any]] = {}
    for line in event_bytes.decode("utf-8").splitlines():
        if line.strip():
            event = json.loads(line)
            events[int(event["sequence"])] = event

    extracted: dict[str, Any] = {}
    sequences: dict[str, int] = {}
    for name, source in protocol["source_events"].items():
        sequence = int(source["sequence"])
        event = events.get(sequence)
        if event is None:
            raise FrozenInputError(f"{name} source event sequence {sequence} is missing")
        payload = event.get("payload") or {}
        if event.get("event_type") != "pipeline_progress":
            raise FrozenInputError(f"{name} source event type mismatch")
        if (payload.get("input_summary") or {}).get("event") != source["pipeline_event"]:
            raise FrozenInputError(f"{name} pipeline event mismatch")
        output = payload.get("output_summary") or {}
        if source["output_key"] not in output:
            raise FrozenInputError(f"{name} output key is missing")
        value = output[source["output_key"]]
        actual_payload_hash = _sha256_bytes(_canonical_bytes(value))
        if actual_payload_hash != source["payload_sha256"]:
            raise FrozenInputError(
                f"{name} payload hash mismatch: expected {source['payload_sha256']}, "
                f"got {actual_payload_hash}"
            )
        extracted[name] = value
        sequences[name] = sequence

    state_payload = dict(extracted["project_state"])
    context_payload = dict(extracted["context_loader"])
    # Seq 44 owns the state. Seq 45 owns the context subsequently written into it.
    state_payload["memory_context"] = context_payload
    return FrozenInputs(
        project_state=ProjectStateSnapshot.model_validate(state_payload),
        context_loader_context=context_payload,
        improvement_report=dict(extracted["improvement_report"]),
        selected_goal=ImprovementGoal.model_validate(extracted["selected_goal"]),
        completed_iteration=int(protocol["completed_iteration"]),
        source_event_sequences=sequences,
    )


def _legacy_prompt(frozen: FrozenInputs) -> str:
    state = frozen.project_state.model_dump(mode="json")
    memory_context = state.get("memory_context") or {}
    state["memory_context"] = {
        "prompt_text": str(memory_context.get("prompt_text") or ""),
        "context_selection": (
            memory_context.get("context_selection")
            if isinstance(memory_context.get("context_selection"), dict)
            else {}
        ),
    }
    values = {
        "{{completed_iteration}}": str(frozen.completed_iteration),
        "{{goal_json}}": frozen.selected_goal.model_dump_json(),
        "{{state_json}}": json.dumps(
            state, ensure_ascii=False, separators=(",", ":"), default=str
        ),
        "{{report_json}}": json.dumps(
            frozen.improvement_report, ensure_ascii=False, default=str
        ),
    }
    prompt = LEGACY_TEMPLATE_V1
    for marker, value in values.items():
        prompt = prompt.replace(marker, value)
    return prompt


def _renderer(candidates: list[ContextCandidate]) -> str:
    return "\n\n".join(candidate.content for candidate in candidates)


def _counter_and_policy(protocol: Mapping[str, Any]) -> tuple[ProviderTokenCounter, ContextAssemblyPolicy]:
    configured = protocol["assembly_policy"]
    counter = ProviderTokenCounter.from_settings(
        SimpleNamespace(
            model=configured["model"],
            base_url="https://api.deepseek.com",
            tokenizer_path="",
        )
    )
    if not counter.available:
        raise FrozenInputError("frozen provider tokenizer is unavailable")
    if counter.tokenizer_id != configured["tokenizer_id"] or counter.model != configured["model"]:
        raise FrozenInputError("frozen provider tokenizer identity mismatch")
    policy = ContextAssemblyPolicy(
        purpose=ContextRequestPurpose(configured["purpose"]),
        max_prompt_chars=int(configured["max_prompt_chars"]),
        max_prompt_tokens=int(configured["requested_prompt_tokens"]),
        reserved_prompt_tokens=int(configured["reserved_prompt_tokens"]),
    )
    if policy.max_prompt_tokens - policy.reserved_prompt_tokens != configured["effective_prompt_tokens"]:
        raise FrozenInputError("frozen effective prompt budget mismatch")
    return counter, policy


def _legacy_candidate(prompt: str, protocol: Mapping[str, Any]) -> ContextCandidate:
    run = protocol["source_run"]
    return ContextCandidate(
        candidate_id="legacy:iteration_task_design:message:1",
        kind=ContextCandidateKind.USER_INPUT,
        source_id=f"trajectory:{run['run_id']}:seq44+45+48+51",
        content=prompt,
        retention=ContextCandidateRetention.REQUIRED,
        priority=100,
        source_order=0,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.CURRENT,
    )


def _selected_required_text(candidates: list[ContextCandidate]) -> str:
    return "\n".join(
        candidate.content
        for candidate in candidates
        if candidate.retention == ContextCandidateRetention.REQUIRED
    )


def _hard_gates(frozen: FrozenInputs, current: Any) -> dict[str, Any]:
    validation = frozen.project_state.validation_context
    product_intent = validation.get("product_intent") or {}
    constraints = list(product_intent.get("non_regression_constraints") or [])
    selected_by_id = {candidate.candidate_id: candidate for candidate in current.selected_candidates}
    safety = selected_by_id.get("iteration_task_design:safety")
    safety_text = safety.content if safety is not None else ""
    missing_constraints = [value for value in constraints if value not in safety_text]
    required_decisions = [
        decision
        for decision in current.selection.candidate_decisions
        if decision.retention == ContextCandidateRetention.REQUIRED
    ]
    selected_required = _selected_required_text(current.selected_candidates)
    goal_values = [
        frozen.selected_goal.id,
        frozen.selected_goal.title,
        *frozen.selected_goal.acceptance_criteria,
    ]
    delivery_surface = str(product_intent.get("delivery_surface") or "")
    checks = {
        "assembly_ready": str(current.selection.assembly_status) == "ready",
        "required_representation_complete": bool(required_decisions)
        and all(decision.action == "kept" for decision in required_decisions),
        "selected_goal_present": all(value in selected_required for value in goal_values if value),
        "schema_present": '"task"' in selected_required,
        "safety_candidate_selected": safety is not None,
        "non_regression_constraints_present": not missing_constraints,
        "delivery_surface_present": bool(delivery_surface)
        and delivery_surface in safety_text,
        "validation_evidence_present": "iteration_task_design:validation" in selected_by_id,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "required_non_regression_constraints": constraints,
        "missing_non_regression_constraints": missing_constraints,
        "delivery_surface": delivery_surface,
    }


def assert_hard_gates(gates: Mapping[str, Any]) -> None:
    if gates.get("passed"):
        return
    failed = [name for name, passed in (gates.get("checks") or {}).items() if not passed]
    missing = list(gates.get("missing_non_regression_constraints") or [])
    details = failed or missing or ["unspecified hard gate"]
    raise HardGateFailure(
        "required project-improvement context gate failed: " + "; ".join(details)
    )


def run_counterfactual(*, enforce_hard_gates: bool = True) -> dict[str, Any]:
    protocol = load_protocol()
    frozen = load_frozen_inputs(protocol)
    expected_template_hash = protocol["legacy_asset"]["template_sha256"]
    actual_template_hash = _sha256_bytes(LEGACY_TEMPLATE_V1.encode("utf-8"))
    if actual_template_hash != expected_template_hash:
        raise FrozenInputError(
            f"legacy template hash mismatch: expected {expected_template_hash}, got {actual_template_hash}"
        )

    counter, policy = _counter_and_policy(protocol)
    assembler = ContextAssembler(renderer=lambda _: "", token_counter=counter)
    legacy_prompt = _legacy_prompt(frozen)
    legacy = assembler.assemble_candidates(
        [_legacy_candidate(legacy_prompt, protocol)], policy=policy, renderer=_renderer
    )
    current_candidates = build_iteration_task_design_candidates(
        project_state=frozen.project_state,
        goal=frozen.selected_goal,
        improvement_report=frozen.improvement_report,
        completed_iteration=frozen.completed_iteration,
    )
    current = assembler.assemble_candidates(
        current_candidates, policy=policy, renderer=_renderer
    )

    expected = protocol["legacy_asset"]
    legacy_observed = {
        "assembly_status": str(legacy.selection.assembly_status),
        "original_prompt_chars": legacy.selection.original_prompt_chars,
        "original_prompt_tokens": legacy.selection.original_prompt_tokens,
        "final_prompt_tokens": legacy.selection.final_prompt_tokens,
        "omitted_required_candidate_ids": legacy.selection.omitted_required_candidate_ids,
    }
    expected_legacy = {
        "assembly_status": expected["expected_assembly_status"],
        "original_prompt_chars": expected["expected_original_prompt_chars"],
        "original_prompt_tokens": expected["expected_original_prompt_tokens"],
        "final_prompt_tokens": 0,
        "omitted_required_candidate_ids": ["legacy:iteration_task_design:message:1"],
    }
    if legacy_observed != expected_legacy:
        raise FrozenInputError(
            f"legacy baseline mismatch: expected {expected_legacy}, got {legacy_observed}"
        )

    decisions = current.selection.candidate_decisions
    current_observed = {
        "assembly_status": str(current.selection.assembly_status),
        "original_prompt_chars": current.selection.original_prompt_chars,
        "original_prompt_tokens": current.selection.original_prompt_tokens,
        "final_prompt_tokens": current.selection.final_prompt_tokens,
        "omitted_required_candidate_ids": current.selection.omitted_required_candidate_ids,
        "decision_coverage": len(decisions) / len(current_candidates),
        "required_partial_count": sum(
            decision.retention == ContextCandidateRetention.REQUIRED
            and decision.action == "partially_kept"
            for decision in decisions
        ),
        "selected_candidate_count": len(current.selected_candidates),
        "omitted_candidate_count": sum(decision.action == "omitted" for decision in decisions),
        "partial_candidate_count": sum(
            decision.action == "partially_kept" for decision in decisions
        ),
    }
    hard_gates = _hard_gates(frozen, current)
    result = {
        "protocol_id": protocol["protocol_id"],
        "instrumentation_valid": len(decisions) == len(current_candidates),
        "legacy": legacy_observed,
        "current": current_observed,
        "hard_gates": hard_gates,
    }
    if enforce_hard_gates:
        assert_hard_gates(hard_gates)
    return result


def main() -> int:
    try:
        print(json.dumps(run_counterfactual(), ensure_ascii=False, indent=2))
    except (FrozenInputError, HardGateFailure) as error:
        print(str(error))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
