from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.llm import LLMResponse
from core.exceptions import ContextAssemblyBudgetError
from core.semantic_analyzer import SemanticAnalyzer
from autonomous_iteration.agents.iteration_agent import AutonomousIterationAgent
from agent_generator.slot_generator import _generate_slot_payload
from metadata import (
    ContextCandidate,
    ContextCandidateRetention,
    ContextCandidateTruncation,
    ContextRequestPurpose,
)
from tools.code_generator import CodeGenerator


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


PHASE_3A_FUNCTIONS = [
    ("core/semantic_analyzer.py", "analyze_goal"),
    ("core/semantic_analyzer.py", "analyze_plan_step"),
    ("core/tool_event_loop.py", "run"),
    ("autonomous_iteration/agents/tool_planning_executor.py", "_retry_empty_decision_plan"),
    ("autonomous_iteration/agents/execution_task_decomposer.py", "_estimate_complexity"),
    ("autonomous_iteration/agents/execution_task_decomposer.py", "_generate_decomposition"),
    ("autonomous_iteration/agents/iteration_agent.py", "_complete_json_candidates"),
    ("autonomous_iteration/tool/project_improvement_tool.py", "project_improvement_tool_executor"),
    ("autonomous_iteration/agents/project_evaluator.py", "_call_llm_text"),
]

PHASE_3B_FUNCTIONS = [
    ("tools/code_generator.py", "_call_llm"),
    ("autonomous_iteration/task_executor.py", "_generate_text_file_content"),
    ("tools/code_unit_generator.py", "_call_llm"),
    ("tools/code_editor.py", "_call_llm"),
    ("tools/bug_fix_tool.py", "_request_fix"),
]

PHASE_3C_FUNCTIONS = [
    ("memory/context_compressor.py", "_generate_summary"),
    ("tools/llm_summarizer.py", "_complete_summary"),
    ("tools/web_searcher.py", "_llm_search_query_variants"),
    ("tools/web_searcher.py", "_select_redirect_links_with_llm"),
    ("tools/web_searcher.py", "_clean_with_llm"),
    ("agent_generator/slot_generator.py", "_generate_slot_payload"),
    ("agent_generator/slot_generator.py", "_repair_slot_language"),
]


def _function_source(relative_path: str, function_name: str) -> str:
    path = SRC_ROOT / relative_path
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    ]
    assert len(matches) == 1, (relative_path, function_name)
    return ast.get_source_segment(source, matches[0]) or ""


@pytest.mark.parametrize(("relative_path", "function_name"), PHASE_3A_FUNCTIONS)
def test_phase_3a_entry_uses_shared_context_request_builder(
    relative_path: str,
    function_name: str,
) -> None:
    source = _function_source(relative_path, function_name)

    assert (
        "build_context_llm_request" in source
        or "build_context_candidate_request" in source
    )
    assert "LLMRequest(" not in source


def test_semantic_goal_request_carries_typed_context_purpose() -> None:
    class FakeClient:
        settings = SimpleNamespace(
            model="test-model",
            base_url="https://example.invalid",
            context_max_prompt_tokens=256,
            context_reserved_prompt_tokens=16,
        )

        def __init__(self) -> None:
            self.requests = []

        def complete(self, request):
            self.requests.append(request)
            return LLMResponse(
                content="{}",
                parsed_json={
                    "task_type": "planning",
                    "risk_level": "medium",
                    "required_resources": ["llm"],
                    "expected_deliverables": [],
                    "intent": "plan",
                    "confidence": 0.9,
                    "reason": "classified",
                },
                model="test-model",
                provider="test",
            )

    client = FakeClient()
    SemanticAnalyzer(client).analyze_goal("Plan the project")

    selection = client.requests[0].context_selection
    assert selection is not None
    assert selection.request_purpose == ContextRequestPurpose.SEMANTIC_GOAL
    assert selection.assembly_status == "ready"


def test_semantic_goal_rejects_structured_payload_before_raw_truncation() -> None:
    class FakeClient:
        settings = SimpleNamespace(
            model="test-model",
            base_url="https://example.invalid",
            context_max_prompt_tokens=256,
            context_reserved_prompt_tokens=16,
        )

        def __init__(self) -> None:
            self.requests = []

        def complete(self, request):
            self.requests.append(request)
            raise AssertionError("malformed structured context must not reach the provider")

    client = FakeClient()

    with pytest.raises(ContextAssemblyBudgetError):
        SemanticAnalyzer(client).analyze_goal("x" * 20_000)

    assert client.requests == []


def test_iteration_owner_does_not_hide_context_budget_failure() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.requests = []

        def complete(self, request, **kwargs):
            self.requests.append(request)
            raise AssertionError("budget-insufficient context must not reach the provider")

    client = FakeClient()
    agent = object.__new__(AutonomousIterationAgent)
    agent.llm_client = client

    with pytest.raises(ContextAssemblyBudgetError):
        agent._complete_json_candidates(
            [
                ContextCandidate(
                    candidate_id="iteration_goal:required-test",
                    kind="constraint",
                    content="x" * 20_000,
                    retention=ContextCandidateRetention.REQUIRED,
                    truncation=ContextCandidateTruncation.FORBIDDEN,
                )
            ],
            purpose=ContextRequestPurpose.ITERATION_GOAL,
        )

    assert client.requests == []


@pytest.mark.parametrize(("relative_path", "function_name"), PHASE_3B_FUNCTIONS)
def test_phase_3b_entry_uses_shared_context_request_builder(
    relative_path: str,
    function_name: str,
) -> None:
    source = _function_source(relative_path, function_name)

    assert "build_context_llm_request" in source
    assert "LLMRequest(" not in source


def test_code_generation_request_carries_typed_purpose_and_fails_closed_on_large_scope() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.requests = []

        def complete(self, request):
            self.requests.append(request)
            return SimpleNamespace(content="```python\nprint('ok')\n```")

    client = FakeClient()
    generator = CodeGenerator(client)

    generator._call_llm("Generate a small program")
    assert client.requests[0].context_selection.request_purpose == ContextRequestPurpose.CODE_GENERATION

    with pytest.raises(ContextAssemblyBudgetError):
        generator._call_llm("x" * 20_000)
    assert len(client.requests) == 1


@pytest.mark.parametrize(("relative_path", "function_name"), PHASE_3C_FUNCTIONS)
def test_phase_3c_entry_uses_shared_context_request_builder(
    relative_path: str,
    function_name: str,
) -> None:
    source = _function_source(relative_path, function_name)

    assert "build_context_llm_request" in source
    assert "LLMRequest(" not in source


def test_slot_generation_request_carries_typed_context_purpose() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.requests = []

        def complete(self, request):
            self.requests.append(request)
            return SimpleNamespace(
                parsed_json={
                    "user_language": "en",
                    "slots": [
                        {
                            "name": "task",
                            "kind": "task",
                            "description": "Task",
                            "value": "Build",
                            "required": True,
                            "revision_notes": [],
                        }
                    ],
                }
            )

    client = FakeClient()
    _generate_slot_payload("Build a demo", client)

    assert (
        client.requests[0].context_selection.request_purpose
        == ContextRequestPurpose.SLOT_GENERATION
    )
