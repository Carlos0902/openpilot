"""Frozen offline A/B for the failed full-architecture code-generation input."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
from tools.code_generation_context import build_code_generation_candidates
from tools.code_generator import CodeGenerator
from tools.code_models import CodeGenerationRequest


HERE = Path(__file__).resolve().parent
EVENTS_PATH = (
    HERE
    / "runs/20260803T183221Z/diagnostics/task_trajectory/"
    "b92b93a41df84b008f3c96b987e14086/events.jsonl"
)
EVENTS_SHA256 = "9ffb79fec27c778dd2952317e377835219be22b65145c4dcf8dbfe4874283613"


class FrozenCodeGenerationInputError(RuntimeError):
    """The historical source no longer matches the frozen experiment."""


def _load_events() -> dict[int, dict[str, Any]]:
    raw = EVENTS_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EVENTS_SHA256:
        raise FrozenCodeGenerationInputError("historical events hash mismatch")
    return {
        int(event["sequence"]): event
        for event in (json.loads(line) for line in raw.decode("utf-8").splitlines())
    }


def build_frozen_request() -> CodeGenerationRequest:
    """Rebuild the request shape that produced the 36,586-char retry input."""

    events = _load_events()
    report = events[56]["payload"]["output_summary"]["improvement_report"]
    task = events[58]["payload"]["output_summary"]["designed_tasks"][0]
    current_code = events[29]["payload"]["output_metadata"]["result"]["content"]
    prompt_context = deepcopy(report["annotations"]["prompt_context"])
    prompt_context.update(
        {
            "operation_kind": "file_replace",
            "iteration_goal": report["next_iteration_goal"],
            "acceptance_criteria": task["acceptance_criteria"],
            "tool_task": task["description"],
            "agent_instruction": (
                "Full improvement: implement the selected autonomous iteration goal while "
                "honoring the product-fit rubric."
            ),
            "improvement_report_summary": {
                "summary": report.get("summary") or "",
                "opportunities": (report.get("improvement_opportunities") or [])[:4],
                "recommended_actions": (report.get("recommended_actions") or [])[:4],
                "blocking_risks": (report.get("blocking_risks") or [])[:3],
            },
            "diagnosis": report["diagnosis"],
            "selected_candidate": report["selected_candidate"],
        }
    )
    project = prompt_context["project_context"]
    project["target_file"] = project["written_files"][0]
    project["current_code_context"] = current_code
    dependency_strategy = report["diagnosis"].get("dependency_strategy")
    if isinstance(dependency_strategy, dict):
        prompt_context["dependency_strategy"] = dependency_strategy
    return CodeGenerationRequest(
        request_id="frozen-improvement-codegen-183221",
        task_description=task["description"],
        language="python",
        max_lines=2000,
        forbidden_operations=["os.system", "eval", "exec"],
        prompt_context=prompt_context,
    )


def _renderer(candidates: list[ContextCandidate]) -> str:
    return "\n\n".join(candidate.content for candidate in candidates)


def _legacy_candidate(request: CodeGenerationRequest) -> ContextCandidate:
    prompt = CodeGenerator()._build_contextual_prompt(request)
    return ContextCandidate(
        candidate_id="legacy:code_generation:message:1",
        kind=ContextCandidateKind.USER_INPUT,
        source_id="trajectory:20260803T183221Z:seq29+56+58",
        content=prompt,
        retention=ContextCandidateRetention.REQUIRED,
        priority=100,
        source_order=0,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.CURRENT,
    )


def run_counterfactual() -> dict[str, Any]:
    request = build_frozen_request()
    counter = ProviderTokenCounter.from_settings(
        SimpleNamespace(
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            tokenizer_path="",
        )
    )
    if not counter.available:
        raise FrozenCodeGenerationInputError("frozen provider tokenizer is unavailable")
    policy = ContextAssemblyPolicy(
        purpose=ContextRequestPurpose.CODE_GENERATION,
        max_prompt_chars=16000,
        max_prompt_tokens=4096,
        reserved_prompt_tokens=128,
    )
    assembler = ContextAssembler(renderer=lambda _payload: "", token_counter=counter)
    legacy = assembler.assemble_candidates(
        [_legacy_candidate(request)], policy=policy, renderer=_renderer
    )
    candidates = build_code_generation_candidates(request)
    current = assembler.assemble_candidates(candidates, policy=policy, renderer=_renderer)
    selected = {candidate.candidate_id: candidate for candidate in current.selected_candidates}
    required = [
        decision
        for decision in current.selection.candidate_decisions
        if decision.retention == ContextCandidateRetention.REQUIRED
    ]
    required_text = _renderer(
        [candidate for candidate in current.selected_candidates if candidate.retention == ContextCandidateRetention.REQUIRED]
    )
    expected_facts = [
        "calculator.py",
        "file_replace",
        "Existing behavior of imported add and divide functions remains unchanged.",
        "Preserve delivery surface: project_native.",
        "def divide",
    ]
    checks = {
        "legacy_reproduces_budget_failure": str(legacy.selection.assembly_status) == "budget_insufficient",
        "current_is_ready": str(current.selection.assembly_status) == "ready",
        "all_required_kept": bool(required) and all(item.action == "kept" for item in required),
        "permission_selected": "code_generation:permission" in selected,
        "current_code_selected": "code_generation:current_code" in selected,
        "required_facts_preserved": all(value in required_text for value in expected_facts),
        "diagnosis_not_required": "Additional diagnosis history" not in required_text,
    }
    return {
        "protocol_id": "code-generation-context-ab-v1",
        "source_run": "20260803T183221Z",
        "historical_retry_prompt_chars": 36586,
        "reconstructed_prompt_context_chars": len(
            json.dumps(request.prompt_context, ensure_ascii=False, default=str)
        ),
        "legacy": {
            "assembly_status": str(legacy.selection.assembly_status),
            "original_prompt_chars": legacy.selection.original_prompt_chars,
            "original_prompt_tokens": legacy.selection.original_prompt_tokens,
            "final_prompt_tokens": legacy.selection.final_prompt_tokens,
            "omitted_required_candidate_ids": legacy.selection.omitted_required_candidate_ids,
        },
        "current": {
            "assembly_status": str(current.selection.assembly_status),
            "original_prompt_chars": current.selection.original_prompt_chars,
            "original_prompt_tokens": current.selection.original_prompt_tokens,
            "final_prompt_tokens": current.selection.final_prompt_tokens,
            "selected_candidate_ids": sorted(selected),
            "omitted_candidate_ids": [
                decision.candidate_id
                for decision in current.selection.candidate_decisions
                if decision.action == "omitted"
            ],
            "omitted_required_candidate_ids": current.selection.omitted_required_candidate_ids,
        },
        "hard_gates": {"passed": all(checks.values()), "checks": checks},
    }


if __name__ == "__main__":
    print(json.dumps(run_counterfactual(), ensure_ascii=False, indent=2))
