from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from memory.context_assembly import ContextAssembler
from memory.context_assembly.evaluation import evaluate_context_quality
from memory.context_compressor import ContextCompressor
from metadata import (
    ContextAssemblyPolicy,
    ContextCandidate,
    ContextQualityExpectation,
    ContextQualityIssueCode,
)


def _fixture_cases() -> list[dict]:
    path = Path(__file__).parent / "fixtures" / "context_quality_cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda case: case["name"])
def test_context_quality_fixture_corpus(case: dict) -> None:
    candidates = [ContextCandidate.model_validate(item) for item in case["candidates"]]
    assembler = ContextAssembler(renderer=lambda _payload: "")
    result = assembler.assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(max_prompt_chars=case["max_prompt_chars"]),
    )

    evaluation = evaluate_context_quality(
        candidates,
        result,
        ContextQualityExpectation(
            expected_selected_candidate_ids=case["expected_selected"],
            expected_omitted_candidate_ids=case["expected_omitted"],
        ),
    )

    assert evaluation.passed, evaluation.model_dump(mode="json")
    assert evaluation.issue_codes == []


def test_context_quality_reports_explicit_expectation_failures_deterministically() -> None:
    candidates = [
        ContextCandidate(
            candidate_id="selected-memory",
            kind="memory",
            content="selected",
        )
    ]
    result = ContextAssembler(renderer=lambda _payload: "").assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(max_prompt_chars=100),
    )
    expectation = ContextQualityExpectation(
        expected_selected_candidate_ids=["missing-memory"],
        expected_omitted_candidate_ids=["selected-memory"],
    )

    first = evaluate_context_quality(candidates, result, expectation)
    repeated = evaluate_context_quality(candidates, result, expectation)

    assert first == repeated
    assert first.passed is False
    assert set(first.issue_codes) == {
        ContextQualityIssueCode.EXPECTED_CANDIDATE_MISSING,
        ContextQualityIssueCode.FORBIDDEN_CANDIDATE_SELECTED,
    }


def test_production_source_has_no_legacy_context_entry_callers() -> None:
    source_root = Path(__file__).parents[1] / "src"
    legacy_definitions = {
        source_root / "memory" / "context_assembly" / "assembler.py",
        source_root / "memory" / "context_compressor.py",
    }
    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        if path in legacy_definitions:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr == "assemble":
                violations.append(f"{path.relative_to(source_root)}:{node.lineno}:assemble")
            if isinstance(node.func, ast.Name) and node.func.id == "ContextCompressor":
                violations.append(
                    f"{path.relative_to(source_root)}:{node.lineno}:ContextCompressor"
                )
    assert violations == []


def test_legacy_context_entries_emit_deprecation_warnings() -> None:
    assembler = ContextAssembler(renderer=lambda payload: str(payload))
    with pytest.warns(DeprecationWarning, match="assemble_candidates"):
        assembler.assemble(
            {
                "system_prompt": "legacy",
                "dialog_context": [],
                "related_files": [],
                "related_memories": [],
                "environment_context": [],
            },
            max_prompt_chars=100,
        )
    with pytest.warns(DeprecationWarning, match="artifact-backed"):
        ContextCompressor(object())
