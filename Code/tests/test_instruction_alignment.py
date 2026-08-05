from __future__ import annotations

import json

import pytest

from autonomous_iteration.models import DesignedImprovementTask
from autonomous_iteration.agents.context_loader import (
    DEFAULT_AUTONOMOUS_ITERATION_SYSTEM_PROMPT,
    ContextLoaderAgent,
)
from autonomous_iteration.agents.goal_maker import GoalMakerAgent
from autonomous_iteration.agents.task_decomposer import TaskDecomposerAgent
from autonomous_iteration.agents.task_designer import TaskDesignerAgent
from autonomous_iteration.pipeline import AutonomousIterationPipeline
from core.openpilot_log import OpenPilotLogger
from memory.agents.context_manager import ContextManagerAgent
from memory.agents.memory_vault_agent import MemoryVaultAgent
from memory.agents.project_manager_agent import ProjectManagerAgent
from memory.agents.virtual_environment_manager import VirtualEnvironmentManager
from memory.memory_models import MemoryRecord, MemoryType
from memory.memory_store import MemoryStore
from tools.task_classifier import task_classifier_executor
from metadata import (
    ConversationIdentity,
    SessionConstraintState,
    SessionIngressState,
    TaskRouteMetadata,
    ToolInputMetadata,
)
from tools.builtin_tools import register_builtin_tools
from tools.tool_executor import ToolExecutor
from tools.tool_selection import ToolSelection
from tools.tool_registry import ToolRegistry


def test_memory_agent_facades_import_and_context_rules() -> None:
    manager = ContextManagerAgent(max_agent_chars=40)
    user_text = "用户原话必须保持：不要改写 spacing   和标点。"
    agent_text = "agent-output-" * 20

    manager.add_user_message(user_text)
    manager.add_agent_message(agent_text)
    context = manager.output_context()

    assert context["messages"][0]["content"] == user_text
    assert context["messages"][1]["attributes"]["compressed"] is True
    assert context["messages"][1]["content"].startswith("[COMPRESSED AGENT HISTORY]")


def test_memory_vault_agent_remind_and_confidence_without_graph(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory")
    store.save(
        MemoryRecord(
            id="pref-1",
            memory_type=MemoryType.PROJECT,
            content="User prefers robust modular agents.",
            tags=["agents"],
            confidence=0.9,
        )
    )
    agent = MemoryVaultAgent(memory_store=store)

    reminders = agent.remind("robust agents")
    confidence, answer = agent.confidence_evaluate("robust agents")

    assert reminders[0]["id"] == "pref-1"
    assert confidence > 0
    assert "robust modular agents" in answer


def test_project_manager_agent_sketch_shape(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def main():\n    return 'instruction aligned'\n", encoding="utf-8")
    agent = ProjectManagerAgent(root_path=project)

    result = agent.update(project)
    sketch = json.loads((project / "sketch.json").read_text(encoding="utf-8"))

    assert result["file_count"] == 1
    assert "main.py" in sketch["files"]
    assert sketch["files"]["main.py"]["function_description"]
    assert sketch["files"]["main.py"]["semantic_info"]["kind"] == "content_index"
    assert sketch["files"]["main.py"]["content_index"]["sections"][0]["title"] == "function main"
    assert agent.search("instruction aligned")[0]["name"] == "main.py"


def test_virtual_environment_manager_returns_context_without_creating_env(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("rich\npydantic>=2\n", encoding="utf-8")
    manager = VirtualEnvironmentManager(tmp_path)

    context = manager.get_environment_context()

    assert context["env_name"] == ".venv"
    assert "python -m venv .venv" in context["setup_commands"]
    assert any("install rich pydantic>=2" in command for command in context["setup_commands"])
    assert not (tmp_path / ".venv").exists()


def test_autonomous_iteration_pipeline_stage_order_and_decomposition() -> None:
    captured_kwargs = {}

    class Builder:
        def build(self, query, **kwargs):
            captured_kwargs.update(kwargs)
            return {
                "query": query,
                "system_prompt": kwargs.get("system_prompt", ""),
                "prompt_text": f"## System Prompt\n{kwargs.get('system_prompt', '')}",
            }

    events = []
    pipeline = AutonomousIterationPipeline(
        context_loader=ContextLoaderAgent(memory_context_builder=Builder()),
        goal_maker=GoalMakerAgent(lambda *args: ["goal"]),
        task_designer=TaskDesignerAgent(lambda *args: ["task"]),
        task_decomposer=TaskDecomposerAgent(
            lambda tasks: [task.description for task in tasks],
            lambda tasks: {
                "level": "high" if max(len(task.target_files) for task in tasks) > 1 else "low",
                "score": max(len(task.target_files) for task in tasks),
            },
            easy_threshold=1,
            max_depth=3,
        ),
    )
    task = DesignedImprovementTask(
        id="task",
        goal_id="goal",
        description="Update files",
        target_files=["a.py", "b.py"],
        acceptance_criteria=["works"],
        risk_notes=["risk"],
    )

    events.append("Context Loader")
    context = pipeline.load_context("goal", ".", 0)
    events.append("Project Diagnosis")
    events.append("Goal Maker")
    goals = pipeline.make_goals({}, {}, {}, 0)
    events.append("Task Designer")
    tasks = pipeline.design_tasks({}, goals[0], {}, 0)
    events.append("Task Decomposer")
    decomposition = pipeline.decompose_tasks([task], context)

    assert events == pipeline.stage_names[:5]
    assert context["system_prompt"] == DEFAULT_AUTONOMOUS_ITERATION_SYSTEM_PROMPT
    assert captured_kwargs["system_prompt"] == DEFAULT_AUTONOMOUS_ITERATION_SYSTEM_PROMPT
    assert tasks == ["task"]
    assert decomposition["depth"] == 1
    assert decomposition["difficulty"]["level"] == "low"
    assert len(decomposition["subtasks"]) == 2


def test_context_loader_forwards_session_constraint_state() -> None:
    captured: dict[str, object] = {}

    class Builder:
        def build(self, query, **kwargs):
            captured.update(kwargs)
            return {"query": query}

    state = SessionConstraintState(session_id="session-1", revision=2)
    pipeline = AutonomousIterationPipeline(
        context_loader=ContextLoaderAgent(memory_context_builder=Builder()),
        goal_maker=GoalMakerAgent(lambda *args: []),
        task_designer=TaskDesignerAgent(lambda *args: []),
        task_decomposer=TaskDecomposerAgent(lambda tasks: [], lambda tasks: {}),
    )

    pipeline.load_context("goal", ".", 0, session_constraints=state)

    assert captured["session_constraints"] is state
    assert captured["project_index_mode"] == "read_only"
    assert captured["strict_sources"] is True


def test_context_loader_forwards_optional_session_ingress_state() -> None:
    captured: dict[str, object] = {}

    class Builder:
        def build(self, query, **kwargs):
            captured.update(kwargs)
            return {"query": query}

    state = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-1",
            turn_index=0,
            project_root="/project",
        )
    )
    ContextLoaderAgent(memory_context_builder=Builder()).run(
        "goal",
        "/project",
        session_ingress_state=state,
    )

    assert captured["session_ingress_state"] is state


def test_context_loader_rejects_session_ingress_project_identity_mismatch() -> None:
    class Builder:
        def build(self, query, **kwargs):
            return {"query": query}

    state = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-1",
            turn_index=0,
            project_root="/owned-project",
        )
    )

    with pytest.raises(ValueError, match="project identity"):
        ContextLoaderAgent(memory_context_builder=Builder()).run(
            "goal",
            "/other-project",
            session_ingress_state=state,
        )


def test_pipeline_forwards_session_ingress_state_to_context_loader() -> None:
    captured: dict[str, object] = {}

    class ContextLoader:
        def run(self, goal, project_path, iteration, **kwargs):
            captured.update(kwargs)
            return {"query": goal}

    pipeline = AutonomousIterationPipeline(
        context_loader=ContextLoader(),
        goal_maker=GoalMakerAgent(lambda *args: []),
        task_designer=TaskDesignerAgent(lambda *args: []),
        task_decomposer=TaskDecomposerAgent(lambda tasks: [], lambda tasks: {}),
    )
    state = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-1",
            turn_index=0,
            project_root="/project",
        )
    )

    pipeline.load_context(
        "goal",
        "/project",
        0,
        session_ingress_state=state,
    )

    assert captured["session_ingress_state"] is state


def test_tool_executor_structured_logs_include_source_type(tmp_path) -> None:
    log_file = tmp_path / "tools.jsonl"
    logger = OpenPilotLogger(log_file)
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = ToolExecutor(registry, logger=logger)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="read-missing",
                tool_name="file_reader",
                reason="capability_match",
                input_metadata={},
            )
        )
    finally:
        executor.shutdown()

    assert not result.success
    events = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
    ]
    payloads = [event["payload"] for event in events]
    assert {payload["source_type"] for payload in payloads} == {"tool"}
    assert payloads[-1]["source_name"] == "file_reader"
    assert payloads[-1]["phase"] == "tool_execution"
    assert payloads[-1]["success"] is False


def test_task_classifier_routes_direct_project_work_to_autonomous_iteration() -> None:
    result = task_classifier_executor(ToolInputMetadata.from_mapping("task_classifier", {"task": "帮我在项目里做一个贪吃蛇"}))

    assert isinstance(result.result, TaskRouteMetadata)
    assert result["route"] == "autonomous_iteration"
    assert result["confidence"] >= 0.8


def test_task_classifier_routes_reusable_agent_requests_to_agent_generator() -> None:
    result = task_classifier_executor(ToolInputMetadata.from_mapping("task_classifier", {"task": "生成一个可复用的研究报告 agent"}))

    assert isinstance(result.result, TaskRouteMetadata)
    assert result["route"] == "agent_generator"
    assert result["confidence"] >= 0.8


def test_task_classifier_routes_knowledge_work_to_agent_generator() -> None:
    tasks = [
        "帮我调查一下机器学习",
        "帮我研究一下新能源汽车出海市场",
        "整理一下这些资料并生成报告",
        "解释一下 transformer 的核心思想",
    ]

    for task in tasks:
        result = task_classifier_executor(ToolInputMetadata.from_mapping("task_classifier", {"task": task}))
        assert result["route"] == "agent_generator", task
        assert "knowledge work" in result["reason"]


def test_task_classifier_execution_intent_overrides_knowledge_work() -> None:
    tasks = [
        "帮我在项目里实现机器学习模块",
        "修复 web_searcher.py 里的 bug",
        "在 /tmp/demo 创建一个 Python 项目",
        "研究 web_searcher.py 为什么失败并修复",
    ]

    for task in tasks:
        result = task_classifier_executor(ToolInputMetadata.from_mapping("task_classifier", {"task": task}))
        assert result["route"] == "autonomous_iteration", task


def test_task_classifier_defaults_ambiguous_tasks_to_autonomous_iteration() -> None:
    result = task_classifier_executor(ToolInputMetadata.from_mapping("task_classifier", {"task": "分析一下这个任务"}))

    assert result["route"] == "autonomous_iteration"


def test_command_registry_hides_removed_agent_and_autopilot_commands() -> None:
    from ui.commands import get_all_command_names, get_command_registry

    names = get_all_command_names()
    help_text = get_command_registry().format_help()

    assert "/agent" not in names
    assert "/autopilot" not in names
    assert "/agent" not in help_text
    assert "/autopilot" not in help_text
    assert "task classifier" in help_text.lower()


def test_enhanced_cli_failure_details_prefer_specific_failure_context() -> None:
    from ui import enhanced_cli

    details = enhanced_cli._format_failure_details(
        {
            "error": "Autopilot reported failure",
            "failure_reason": "Tool file_reader failed: Not a file: /tmp/project",
            "failure_stage": "Task Executor",
            "failed_tool": "file_reader",
            "failed_call_id": "task:r1:c2",
        }
    )

    assert details.startswith("Tool file_reader failed")
    assert "Stage: Task Executor" in details
    assert "Tool: file_reader" in details
    assert "Call: task:r1:c2" in details


def test_enhanced_cli_failure_details_extract_from_task_result_metadata() -> None:
    from types import SimpleNamespace

    from ui import enhanced_cli

    failure = SimpleNamespace(
        error_message="Decision need schema validation failed: candidate_paths must be a list",
        details={
            "failure_stage": "Tool Planning",
            "failed_tool": "tool_planning_executor",
        },
    )
    task_metadata = SimpleNamespace(failure=failure)
    task_result = SimpleNamespace(error="Autopilot reported failure", result_metadata=task_metadata)

    details = enhanced_cli._format_failure_details(
        {
            "error": "Autopilot reported failure",
            "results": [task_result],
        }
    )

    assert details.startswith("Decision need schema validation failed")
    assert "Stage: Tool Planning" in details
    assert "Tool: tool_planning_executor" in details


def test_enhanced_cli_failure_details_extract_from_runtime_session_result() -> None:
    from ui import enhanced_cli

    details = enhanced_cli._format_failure_details(
        {
            "success": False,
            "session_result": {
                "success": False,
                "failure_reason": "Request timed out while generating code",
                "failure_stage": "Task Executor",
                "failed_tool": "code_generator",
                "failed_call_id": "task:r1:c1",
                "error_type": "LLMTimeoutError",
                "suggested_recovery": "Retry code generation with a bounded request.",
            },
        }
    )

    assert details.startswith("Request timed out while generating code")
    assert "Stage: Task Executor" in details
    assert "Tool: code_generator" in details
    assert "Call: task:r1:c1" in details
    assert "Error Type: LLMTimeoutError" in details
    assert "Recovery: Retry code generation with a bounded request." in details


def test_enhanced_cli_failure_details_show_diagnostics_for_tool_and_llm_errors() -> None:
    from types import SimpleNamespace

    from ui import enhanced_cli

    failure = SimpleNamespace(
        error_message="LLM returned invalid JSON (attempt 3/3)",
        details={
            "task_id": "task-1",
            "task_description": "Clarify requirements",
            "failure_stage": "Tool Planning",
            "failed_tool": "tool_planning_executor",
            "error_type": "InvalidLLMResponseError",
            "response_preview_start": '{"decision_needs": [{"need_type": "project_structure"',
            "suggested_recovery": "Return valid JSON only.",
        },
    )
    task_metadata = SimpleNamespace(failure=failure)
    task_result = SimpleNamespace(task_id="task-1", error="Autopilot reported failure", result_metadata=task_metadata)

    details = enhanced_cli._format_failure_details(
        {
            "error": "Autopilot reported failure",
            "results": [task_result],
        }
    )

    assert details.startswith("LLM returned invalid JSON")
    assert "Task: Clarify requirements" in details
    assert "Task ID: task-1" in details
    assert "Error Type: InvalidLLMResponseError" in details
    assert "Recovery: Return valid JSON only." in details
    assert "Response Preview:" in details


def test_execute_goal_interactive_routes_with_task_classifier(monkeypatch) -> None:
    from ui import enhanced_cli

    calls = []

    class Console:
        def print(self, *args, **kwargs):
            pass

    class UI:
        console = Console()

    runtime_options = enhanced_cli.OpenPilotRuntimeOptions()

    def fake_agent(task, ui, llm_client=None, logger=None):
        calls.append(("agent", task))
        return "agent-result"

    def fake_autopilot(goal, ui, tracker, llm_client, logger, runtime_options, context=None):
        calls.append(("autopilot", goal, dict(context or {})))
        return "autopilot-result"

    monkeypatch.setattr(enhanced_cli, "_execute_agent_generator", fake_agent)
    monkeypatch.setattr(enhanced_cli, "_execute_autopilot", fake_autopilot)

    assert (
        enhanced_cli._execute_goal_interactive(
            "生成一个可复用的研究报告 agent",
            UI(),
            tracker=None,
            llm_client=None,
            logger=None,
            runtime_options=runtime_options,
        )
        == "agent-result"
    )
    assert calls[-1] == ("agent", "生成一个可复用的研究报告 agent")

    assert (
        enhanced_cli._execute_goal_interactive(
            "帮我做一个项目",
            UI(),
            tracker=None,
            llm_client=None,
            logger=None,
            runtime_options=runtime_options,
        )
        == "autopilot-result"
    )
    assert calls[-1][0:2] == ("autopilot", "帮我做一个项目")
    assert calls[-1][2]["source"] == "interactive"
    assert str(calls[-1][2]["task_id"]).startswith("cli_")
    assert calls[0] == ("agent", "生成一个可复用的研究报告 agent")
    assert len(calls) == 2


def test_execute_goal_interactive_intercepts_shell_activation(monkeypatch) -> None:
    from ui import enhanced_cli

    calls = []

    class Console:
        def __init__(self) -> None:
            self.messages = []

        def print(self, *args, **kwargs):
            self.messages.append(" ".join(str(arg) for arg in args))

    class UI:
        console = Console()

    monkeypatch.setattr(enhanced_cli, "_classify_task_route", lambda task: calls.append(("classify", task)))

    result = enhanced_cli._execute_goal_interactive(
        "source /tmp/project/.venv/bin/activate",
        UI(),
        tracker=None,
        llm_client=None,
        logger=None,
        runtime_options=enhanced_cli.OpenPilotRuntimeOptions(),
    )

    assert result is None
    assert calls == []
    assert any("Shell state command" in message for message in UI.console.messages)


def test_runtime_diagnostics_are_enabled_by_default_and_explicitly_disableable(monkeypatch) -> None:
    from ui import enhanced_cli

    monkeypatch.delenv("OPENPILOT_RUNTIME_DIAGNOSTICS_ENABLED", raising=False)
    assert enhanced_cli._runtime_diagnostics_enabled() is True

    for value in ("0", "false", "no", "off"):
        monkeypatch.setenv("OPENPILOT_RUNTIME_DIAGNOSTICS_ENABLED", value)
        assert enhanced_cli._runtime_diagnostics_enabled() is False

    monkeypatch.setenv("OPENPILOT_RUNTIME_DIAGNOSTICS_ENABLED", "1")
    assert enhanced_cli._runtime_diagnostics_enabled() is True
