from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autonomous_iteration.improvement_context import ImprovementContextHelper
from core.exceptions import InvalidLLMResponseError, LLMProviderError
from core.openpilot_log import OpenPilotLogger
from metadata import ReasoningMode, RuntimeBudgetMetadata, ToolInputMetadata
from autonomous_iteration.tool.project_improvement_tool import project_improvement_tool_executor
from autonomous_iteration.tool.project_improvement_tool import _project_file_manifest


def test_improvement_context_target_file_and_generic_product_fit(tmp_path) -> None:
    app = tmp_path / "app.py"
    app.write_text("import curses\n", encoding="utf-8")
    helper = ImprovementContextHelper(
        environment_context_getter=lambda path: {"run_command": "python app.py"} if path else {}
    )

    target = helper.select_iteration_target_file([str(app)], ["Improve app.py"])
    judgment = helper.infer_product_judgment(
        original_goal="Build a snake game",
        project_path=tmp_path,
        written_files=[str(app)],
    )
    rubric = helper.quality_rubric_for_product(judgment)
    context = helper.build_prompt_context(
        original_goal="Build a snake game",
        project_path=tmp_path,
        written_files=[str(app)],
        tool_task="Improve game",
        code_context="print('small')",
    )

    assert target == app
    assert judgment["project_type"] == "interactive_software"
    assert judgment["preferred_stack"] == "project_native"
    assert judgment["current_runtime"] == "terminal_curses"
    assert any("diagnosed success metric" in item for item in rubric)
    assert context["product_intent"]["runtime_mode"] == "interactive"
    assert context["product_intent"]["delivery_surface"] == "project_native"
    assert context["project_context"]["environment"]["run_command"] == "python app.py"
    assert helper.prompt_context_layer_summary(context)["has_product_intent"] is True
    assert helper.prompt_context_layer_summary(context)["code_context_chars"] == len("print('small')")


def test_product_intent_is_generic_for_non_game_goal(tmp_path) -> None:
    helper = ImprovementContextHelper()
    intent = helper.infer_product_intent(
        original_goal="Create a CSV analysis report script",
        project_path=tmp_path,
        written_files=[],
    )

    assert intent.experience_type == "general_project"
    assert intent.runtime_mode == "best_fit_for_goal"
    assert intent.delivery_surface == "project_native"
    assert "structured_output" in intent.core_capabilities


def test_improvement_context_requires_ui_impact_review_from_stack_preset(tmp_path) -> None:
    helper = ImprovementContextHelper(
        environment_context_getter=lambda path: {
            "stack_preset": {
                "revision": 2,
                "delivery_surface": "browser",
                "architecture": "frontend_backend_split",
                "frontend_language": "html_css_javascript",
                "backend_language": "python",
                "ui_strategy": "browser_application",
                "ui_review_required": True,
            }
        }
    )

    context = helper.build_prompt_context(
        original_goal="Build a personal digital assistant",
        project_path=tmp_path,
        written_files=[],
        tool_task="Add reminder creation",
    )

    assert context["stack_preset"]["revision"] == 2
    assert context["ui_iteration_contract"]["assessment_required"] is True
    assert context["ui_iteration_contract"]["implementation_required_for_user_facing_change"] is True
    assert any("UI impact is mandatory" in item for item in context["quality_rubric"])
    assert helper.prompt_context_layer_summary(context)["stack_preset_revision"] == 2


def test_improvement_context_flags_terminal_preset_conflict_for_user_facing_assistant(tmp_path) -> None:
    helper = ImprovementContextHelper(
        environment_context_getter=lambda path: {
            "stack_preset": {
                "revision": 1,
                "preset_source": "initial_inference",
                "delivery_surface": "terminal",
                "architecture": "terminal_application",
                "frontend_language": "terminal_text",
                "backend_language": "python",
                "ui_strategy": "terminal_ui",
                "ui_review_required": True,
            }
        }
    )

    context = helper.build_prompt_context(
        original_goal="帮我做一个个人数字助手",
        project_path=tmp_path,
        written_files=[],
        tool_task="Improve the assistant",
    )

    judgment = context["product_judgment"]
    assert judgment["preferred_runtime"] == "browser"
    assert judgment["preferred_surface"] == "browser"
    assert judgment["recommended_stack_preset_update"]["delivery_surface"] == "browser"


def test_project_improvement_tool_carries_deterministic_stack_revision_without_llm(tmp_path) -> None:
    result = project_improvement_tool_executor(
        ToolInputMetadata.from_mapping(
            "project_improvement_tool",
            {
                "project_path": str(tmp_path),
                "goal": "帮我做一个个人数字助手",
                "prompt_context": {
                    "product_judgment": {
                        "recommended_stack_preset_update": {
                            "delivery_surface": "browser",
                            "architecture": "frontend_backend_split",
                            "frontend_language": "html_css_javascript",
                            "ui_strategy": "browser_application",
                            "ui_review_required": True,
                        }
                    }
                },
            },
        )
    )

    assert result.result.stack_preset_update["delivery_surface"] == "browser"


def test_project_improvement_tool_uses_purpose_specific_candidate_selection(tmp_path) -> None:
    class CapturingLLM:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                provider="openai_compatible",
                model="test-model",
                context_max_prompt_tokens=4096,
                context_reserved_prompt_tokens=128,
            )
            self.request = None

        def complete(self, request, **kwargs):
            self.request = request
            return SimpleNamespace(
                parsed_json={
                    "summary": "Keep the verified behavior and improve documentation.",
                    "improvement_opportunities": ["Document validation."],
                    "recommended_actions": ["Add the exact pytest command."],
                    "next_iteration_goal": "Document validation.",
                    "must_implement_next": ["README contains python -m pytest -q."],
                    "blocking_risks": [],
                    "stack_preset_update": {},
                },
                content="",
            )

    files = []
    for index in range(6):
        path = tmp_path / f"module_{index}.py"
        path.write_text((f"# optional-{index}\n" + "value = 1\n" * 800), encoding="utf-8")
        files.append(str(path))
    llm = CapturingLLM()

    result = project_improvement_tool_executor(
        ToolInputMetadata.from_mapping(
            "project_improvement_tool",
            {
                "project_path": str(tmp_path),
                "goal": "Preserve the calculator API and document validation.",
                "written_files": files,
                "run_command": "python -m pytest -q",
                "validation_result": {
                    "validation_passed": True,
                    "summary": "All tests passed.",
                    "provider_dump": "low-value " * 4000,
                },
                "prompt_context": {
                    "product_intent": {
                        "non_regression_constraints": ["Preserve the public API."],
                    }
                },
                "_llm_client": llm,
            },
        )
    )

    assert result.result.annotations["source"] == "llm"
    assert llm.request is not None
    decisions = llm.request.context_selection.candidate_decisions
    candidate_ids = {decision.candidate_id for decision in decisions}
    assert "project_improvement:instruction" in candidate_ids
    assert "project_improvement:task" in candidate_ids
    assert "project_improvement:safety" in candidate_ids
    assert "project_improvement:validation" in candidate_ids
    assert "project_improvement:message:1" not in candidate_ids


def test_project_improvement_analysis_submits_explicit_completion_budget_and_economical_reasoning(
    tmp_path,
) -> None:
    class CapturingLLM:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                provider="openai_compatible",
                model="test-model",
                context_max_prompt_tokens=4096,
                context_reserved_prompt_tokens=128,
            )
            self.request = None
            self.kwargs = None

        def complete(self, request, **kwargs):
            self.request = request
            self.kwargs = kwargs
            return SimpleNamespace(
                parsed_json={
                    "changed_signals": ["Validation passes."],
                    "proposed_actions": ["Document validation."],
                    "next_decision_or_goal": "Document the exact validation command.",
                    "must_satisfy": ["README contains python -m pytest -q."],
                    "blocking_risks": [],
                    "evidence_ids": ["project_improvement:validation"],
                    "stack_preset_patch": {},
                },
                content="",
                usage={"completion_tokens": 91},
                finish_reason="stop",
            )

    target = tmp_path / "calculator.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    llm = CapturingLLM()
    project_improvement_tool_executor(
        ToolInputMetadata.from_mapping(
            "project_improvement_tool",
            {
                "project_path": str(tmp_path),
                "goal": "Preserve the calculator API and document validation.",
                "written_files": [str(target)],
                "run_command": "python -m pytest -q",
                "validation_result": {"validation_passed": True, "summary": "All tests passed."},
                "_llm_client": llm,
            },
        )
    )

    assert llm.request.max_tokens is not None
    assert llm.request.max_tokens > 0
    assert llm.request.trace_info["completion_budget"]["reserved_tokens"] == llm.request.max_tokens
    assert llm.request.reasoning_policy.mode == ReasoningMode.DISABLED
    # LLMClient defines max_retries as total JSON attempts, so one means exactly
    # one provider call and cannot replay the same wide schema.
    assert llm.kwargs["max_retries"] == 1


def test_project_improvement_recovers_once_from_known_usage_length_with_narrow_delta(tmp_path) -> None:
    class LengthThenDeltaLLM:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                provider="openai_compatible",
                model="test-model",
                context_max_prompt_tokens=4096,
                context_reserved_prompt_tokens=128,
            )
            self.requests = []

        def complete(self, request, **_kwargs):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise InvalidLLMResponseError(
                    "truncated project improvement delta",
                    response_text='{"changed_signals":["partial',
                    usage={"completion_tokens": 111},
                    finish_reason="length",
                )
            return SimpleNamespace(
                parsed_json={
                    "changed_signals": ["Validation passes but the workflow is undocumented."],
                    "proposed_actions": ["Document the exact validation command."],
                    "next_decision_or_goal": "Document validation.",
                    "must_satisfy": ["README contains python -m pytest -q."],
                    "blocking_risks": [],
                    "evidence_ids": ["project_improvement:validation"],
                    "stack_preset_patch": {},
                },
                content="",
                usage={"completion_tokens": 222},
                finish_reason="stop",
            )

    target = tmp_path / "calculator.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    llm = LengthThenDeltaLLM()
    budget = RuntimeBudgetMetadata()

    result = project_improvement_tool_executor(
        ToolInputMetadata.from_mapping(
            "project_improvement_tool",
            {
                "project_path": str(tmp_path),
                "goal": "Preserve the calculator API and document validation.",
                "written_files": [str(target)],
                "run_command": "python -m pytest -q",
                "validation_result": {"validation_passed": True, "summary": "All tests passed."},
                "_llm_client": llm,
                "_runtime_budget": budget,
                "_enhancement_required": True,
            },
        )
    )

    assert result.result.annotations["source"] == "llm"
    assert len(llm.requests) == 2
    first, recovery = llm.requests
    assert recovery.max_tokens > first.max_tokens
    assert recovery.trace_info["completion_budget"]["recovery_of"] == first.trace_info[
        "completion_budget"
    ]["reservation_id"]
    assert budget.enhancement_completion_tokens_used == 333


@pytest.mark.parametrize("failure_kind", ["malformed", "provider_error"])
def test_project_improvement_does_not_recover_ordinary_invalid_output_or_provider_error(
    tmp_path,
    failure_kind,
) -> None:
    class NonRecoverableLLM:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                provider="openai_compatible",
                model="test-model",
                context_max_prompt_tokens=4096,
                context_reserved_prompt_tokens=128,
            )
            self.requests = []

        def complete(self, request, **_kwargs):
            self.requests.append(request)
            if failure_kind == "provider_error":
                raise RuntimeError("provider unavailable")
            return SimpleNamespace(
                parsed_json=None,
                content="not-json",
                usage={"completion_tokens": 17},
                finish_reason="stop",
            )

    target = tmp_path / "calculator.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    llm = NonRecoverableLLM()
    params = ToolInputMetadata.from_mapping(
        "project_improvement_tool",
        {
            "project_path": str(tmp_path),
            "goal": "Preserve the calculator API.",
            "written_files": [str(target)],
            "validation_result": {"validation_passed": True},
            "_llm_client": llm,
        },
    )

    if failure_kind == "provider_error":
        project_improvement_tool_executor(params)
    else:
        project_improvement_tool_executor(params)

    assert len(llm.requests) == 1


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("failure_kind", ["no_client", "non_json", "schema_invalid"])
def test_project_improvement_required_fails_typed_while_optional_falls_back(
    tmp_path,
    required,
    failure_kind,
) -> None:
    class InvalidOutputLLM:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                provider="openai_compatible",
                model="test-model",
                context_max_prompt_tokens=4096,
                context_reserved_prompt_tokens=128,
            )

        def complete(self, _request, **_kwargs):
            if failure_kind == "non_json":
                return SimpleNamespace(
                    parsed_json=None,
                    content="not-json",
                    usage={"completion_tokens": 12},
                    finish_reason="stop",
                )
            return SimpleNamespace(
                parsed_json={"forbidden_wide_state": "must not pass delta validation"},
                content='{"forbidden_wide_state":"must not pass delta validation"}',
                usage={"completion_tokens": 12},
                finish_reason="stop",
            )

    target = tmp_path / "calculator.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    params = {
        "project_path": str(tmp_path),
        "goal": "Preserve the calculator API.",
        "written_files": [str(target)],
        "validation_result": {"validation_passed": True},
        "_enhancement_required": required,
    }
    if failure_kind != "no_client":
        params["_llm_client"] = InvalidOutputLLM()

    if required:
        expected = LLMProviderError if failure_kind == "no_client" else InvalidLLMResponseError
        with pytest.raises(expected):
            project_improvement_tool_executor(
                ToolInputMetadata.from_mapping("project_improvement_tool", params)
            )
    else:
        result = project_improvement_tool_executor(
            ToolInputMetadata.from_mapping("project_improvement_tool", params)
        )
        assert result.result.annotations["source"] == "fallback"


def test_project_improvement_required_context_includes_bounded_project_file_manifest(tmp_path) -> None:
    class CapturingLLM:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                provider="openai_compatible",
                model="test-model",
                context_max_prompt_tokens=4096,
                context_reserved_prompt_tokens=128,
            )
            self.request = None

        def complete(self, request, **kwargs):
            self.request = request
            return SimpleNamespace(
                parsed_json={
                    "summary": "Keep the verified behavior.",
                    "improvement_opportunities": ["Improve calculator.py."],
                    "recommended_actions": ["Modify the implementation only."],
                    "next_iteration_goal": "Improve calculator.py.",
                    "must_implement_next": ["Keep existing tests passing."],
                    "blocking_risks": [],
                    "stack_preset_update": {},
                },
                content="",
            )

    source = tmp_path / "calculator.py"
    source.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    test_file = tmp_path / "test_calculator.py"
    test_body_sentinel = "TEST_BODY_MUST_NOT_BE_LOADED_INTO_ANALYSIS_CONTEXT"
    test_file.write_text(
        test_body_sentinel + "\n" + ("assert True\n" * 10_000),
        encoding="utf-8",
    )
    llm = CapturingLLM()

    project_improvement_tool_executor(
        ToolInputMetadata.from_mapping(
            "project_improvement_tool",
            {
                "project_path": str(tmp_path),
                "goal": "Improve the calculator implementation without changing tests.",
                "written_files": [str(source)],
                "run_command": "python -m pytest -q",
                "validation_result": {"validation_passed": True, "summary": "1 passed"},
                "_llm_client": llm,
            },
        )
    )

    assert llm.request is not None
    rendered = "\n".join(message.content for message in llm.request.messages)
    assert "test_calculator.py" in rendered
    assert test_body_sentinel not in rendered
    assert len(rendered) < 32_000


def test_project_manifest_prunes_excluded_trees_before_walking_them(tmp_path, monkeypatch) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "app.py").write_text("print('ok')\n", encoding="utf-8")
    visited: list[str] = []

    def bounded_walk(root, topdown=True):
        assert topdown is True
        directory_names = [".git", ".venv", "node_modules", "src"]
        yield str(root), directory_names, ["README.md"]
        assert directory_names == ["src"]
        visited.extend(directory_names)
        yield str(source_dir), [], ["app.py"]

    monkeypatch.setattr(
        "autonomous_iteration.tool.project_improvement_tool.os.walk",
        bounded_walk,
    )

    manifest = _project_file_manifest(tmp_path)

    assert visited == ["src"]
    assert [path.name for path in manifest] == ["README.md", "app.py"]


def test_improvement_context_structured_logs_are_jsonl(tmp_path) -> None:
    log_file = tmp_path / "context.jsonl"
    logger = OpenPilotLogger(log_file)
    helper = ImprovementContextHelper(logger=logger, session_id_getter=lambda: "session")

    judgment = helper.infer_product_judgment(
        original_goal="Build a terminal tool",
        project_path=None,
        written_files=[],
    )
    helper.quality_rubric_for_product(judgment)

    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]

    assert events
    assert {event["payload"]["source_type"] for event in events} == {"function"}
    assert all(event["payload"]["phase"] == "improvement_context" for event in events)
