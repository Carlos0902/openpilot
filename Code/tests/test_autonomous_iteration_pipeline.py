from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from autonomous_iteration.models import EvaluationResult, IterationResult, ProjectStateSnapshot
from autonomous_iteration.agents.iteration_agent import AutonomousIterationAgent
from autonomous_iteration.project_iteration import ProjectIterationHelper
from memory.agents.git_manager_agent import GitManagerAgent
from memory.memory_models import MemoryType
from memory.memory_store import MemoryStore
from core.exceptions import ContextSourceError
from metadata import ConversationIdentity, SessionIngressState


class FakeEvaluator:
    llm_client = None

    def evaluate_project(self, **kwargs) -> EvaluationResult:
        return EvaluationResult(
            validation_passed=True,
            runnable=True,
            has_blocking_bugs=False,
            summary="Project validation passed.",
            improvement_opportunities=["Improve visible polish."],
            recommended_actions=["Add a visible polish improvement."],
            next_iteration_goal="Add visible polish.",
            run_command=kwargs.get("run_command", ""),
        )


class FailingEvaluator:
    llm_client = None

    def evaluate_project(self, **kwargs) -> EvaluationResult:
        return EvaluationResult(
            validation_passed=False,
            runnable=False,
            has_blocking_bugs=True,
            summary="Runtime smoke test failed.",
            validation_errors=["NameError: snake is not defined"],
            recommended_actions=["Fix the runtime error reported by the smoke test."],
            next_iteration_goal="Fix runtime smoke test failure.",
            run_command=kwargs.get("run_command", ""),
        )


class SequenceEvaluator:
    llm_client = None

    def __init__(self, passed_sequence: list[bool]) -> None:
        self.passed_sequence = list(passed_sequence)
        self.calls = 0

    def evaluate_project(self, **kwargs) -> EvaluationResult:
        passed = self.passed_sequence[min(self.calls, len(self.passed_sequence) - 1)]
        self.calls += 1
        return EvaluationResult(
            validation_passed=passed,
            runnable=passed,
            has_blocking_bugs=not passed,
            summary="Project validation passed." if passed else "Runtime smoke test failed.",
            validation_errors=[] if passed else ["RuntimeError: boom"],
            improvement_opportunities=["Improve the project."] if passed else [],
            recommended_actions=["Improve the project."] if passed else ["Fix runtime error."],
            next_iteration_goal="Improve the project." if passed else "Fix runtime error.",
            run_command=kwargs.get("run_command", ""),
        )


class RecordingEvaluator:
    llm_client = None

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def evaluate_project(self, **kwargs) -> EvaluationResult:
        self.calls.append(kwargs)
        return EvaluationResult(
            validation_passed=True,
            runnable=True,
            has_blocking_bugs=False,
            summary="Project validation passed.",
            improvement_opportunities=["Improve the project."],
            recommended_actions=["Improve the project."],
            next_iteration_goal="Improve the project.",
            run_command=kwargs.get("run_command", ""),
        )


class FakeMemoryContextBuilder:
    def build(
        self,
        query: str,
        *,
        project_path,
        include_environment: bool,
        limit: int,
        system_prompt: str = "",
        project_index_mode: str = "refresh",
        strict_sources: bool = False,
    ) -> dict:
        return {
            "query": query,
            "project_path": str(project_path),
            "system_prompt": system_prompt,
            "dialog_context": [{"role": "user", "content": "原始用户需求"}],
            "related_memories": [{"id": "memory-1", "content": "Prefer visible polish."}],
            "related_files": [{"path": str(Path(project_path) / "app.py")}],
            "environment_context": [{"content": "Python environment ready."}],
            "prompt_text": f"## System Prompt\n{system_prompt}\n\n## Dialog Context\nUSER: 原始用户需求",
        }


class RecordingJsonLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.context_selections: list[object] = []

    def complete(self, request, **kwargs):
        prompt = "\n\n".join(message.content for message in request.messages)
        self.prompts.append(prompt)
        self.context_selections.append(request.context_selection)
        if "Goal Maker Agent" in prompt:
            parsed_json = {
                "goals": [
                    {
                        "id": "goal-1",
                        "title": "Improve context budgeting",
                        "acceptance_criteria": ["Context evidence is not duplicated"],
                    }
                ]
            }
        else:
            parsed_json = {
                "tasks": [
                    {
                        "id": "task-1",
                        "goal_id": "goal-1",
                        "description": "Remove duplicated context evidence",
                        "target_files": ["app.py"],
                        "acceptance_criteria": ["Prompt contains one context copy"],
                    }
                ]
            }
        return SimpleNamespace(parsed_json=parsed_json, content="")


def _project_state(project_path: Path) -> dict:
    return {
        "project_path": str(project_path),
        "goal": "Improve project",
        "written_files": [str(project_path / "app.py")],
        "file_summaries": [],
        "readme_summary": "",
        "run_command": "",
        "memory_records": [],
        "validation_context": {},
        "safe_target_files": [str(project_path / "app.py")],
    }


def test_iteration_agent_forwards_session_ingress_state_to_pipeline(tmp_path) -> None:
    captured: dict[str, object] = {}

    class Pipeline:
        def load_context(self, goal, project_path, iteration, **kwargs):
            captured.update(kwargs)
            return {"query": goal}

    agent = object.__new__(AutonomousIterationAgent)
    agent.pipeline = Pipeline()
    state = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="session-1",
            run_id="run-1",
            turn_index=0,
            project_root=str(tmp_path),
        )
    )

    result = agent._load_context(
        goal="Improve project",
        project_path=tmp_path,
        iteration=0,
        session_ingress_state=state,
    )

    assert result["query"] == "Improve project"
    assert captured["session_ingress_state"] is state


def test_iteration_agent_does_not_turn_context_source_failure_into_empty_success() -> None:
    class Pipeline:
        def load_context(self, *args, **kwargs):
            raise OSError("context source unavailable")

    agent = object.__new__(AutonomousIterationAgent)
    agent.pipeline = Pipeline()

    with pytest.raises(ContextSourceError, match="Context source unavailable"):
        agent._load_context(
            goal="Inspect project",
            project_path=".",
            iteration=0,
        )


def test_goal_and_task_prompts_use_granular_memory_without_reloading_prompt_text(tmp_path) -> None:
    marker = "UNIQUE_CONTEXT_FACT"
    llm = RecordingJsonLLM()
    agent = AutonomousIterationAgent(FakeEvaluator(), llm_client=llm)
    project_state = ProjectStateSnapshot(
        project_path=str(tmp_path),
        safe_target_files=["app.py"],
        memory_records=[{"id": "fact", "content": marker, "attributes": {}}],
        memory_context={
            "dialog_context": [{"role": "user", "content": marker}],
            "related_memories": [{"content": marker}],
            "prompt_text": f"## Dialog Context\nUSER: {marker}",
            "context_selection": {
                "kind": "context_selection",
                "max_prompt_chars": 500,
                "final_prompt_chars": 40,
            },
        },
    )
    evaluation = FakeEvaluator().evaluate_project()

    goals = agent._make_goals(project_state, evaluation, {}, 0)
    agent._design_tasks(project_state, goals[0], {}, 0)

    assert len(llm.prompts) == 2
    assert all(prompt.count(marker) == 1 for prompt in llm.prompts)
    assert all(selection is not None for selection in llm.context_selections)


def test_autonomous_iteration_events_and_memory_context(tmp_path) -> None:
    events: list[str] = []
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("print('hello')\n", encoding="utf-8")
    agent = AutonomousIterationAgent(
        FakeEvaluator(),
        required_successful_improvements=1,
        max_iteration_attempts=2,
        memory_context_builder=FakeMemoryContextBuilder(),
    )

    def apply_improvement(iteration, evaluation, actions, improvement_report, is_repair):
        assert improvement_report["task_difficulty"]["level"] in {"low", "medium", "high"}
        assert improvement_report["diagnosis"]["kind"] == "project_diagnosis"
        assert improvement_report["selected_candidate"]["candidate_id"]
        assert improvement_report["selected_goal"]["title"] == improvement_report["selected_candidate"]["title"]
        return IterationResult(
            iteration=iteration,
            validation_passed=False,
            completed_successful_iteration=False,
            applied_actions=actions,
            changed_files=[str(project / "app.py")],
            success=True,
        )

    result = agent.run_project_pipeline(
        goal="Improve project",
        project_path=project,
        written_files=[str(project / "app.py")],
        apply_improvement=apply_improvement,
        analyze_improvements=lambda completed, evaluation: {
            "summary": "Need polish.",
            "next_iteration_goal": "Add visible polish.",
            "recommended_actions": ["Add visible polish."],
        },
        read_project_state=lambda evaluation, iteration: _project_state(project),
        on_progress=lambda event, payload: events.append(event),
    )

    assert result["success"]
    assert result["project_state"].memory_context["system_prompt"]
    assert result["project_state"].memory_context["prompt_text"].startswith("## System Prompt")
    assert result["project_state"].memory_context["related_memories"][0]["id"] == "memory-1"
    assert "context_loader" in events
    assert events.index("project_diagnosis") < events.index("goal_maker")
    assert events.index("project_state") < events.index("context_loader")
    assert events.index("goal_maker_started") < events.index("goal_maker")
    assert events.index("context_loader") < events.index("goal_maker")
    assert events.index("task_designer_started") < events.index("task_designer")
    assert events.index("goal_maker") < events.index("task_designer")
    assert events.index("decomposition_started") < events.index("decomposition")
    assert events.index("task_designer") < events.index("decomposition")
    assert events.index("decomposition") < events.index("iteration_started")


def test_autonomous_iteration_validation_tracks_changed_files_after_iteration(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    app = project / "app.py"
    server = project / "server.py"
    app.write_text("print('hello')\n", encoding="utf-8")
    server.write_text("print('server')\n", encoding="utf-8")
    evaluator = RecordingEvaluator()
    agent = AutonomousIterationAgent(
        evaluator,
        required_successful_improvements=1,
        max_iteration_attempts=2,
        memory_context_builder=FakeMemoryContextBuilder(),
    )

    def apply_improvement(iteration, evaluation, actions, improvement_report, is_repair):
        return IterationResult(
            iteration=iteration,
            validation_passed=False,
            completed_successful_iteration=False,
            applied_actions=actions,
            changed_files=[str(server)],
            success=True,
        )

    result = agent.run_project_pipeline(
        goal="Improve project",
        project_path=project,
        written_files=[str(app)],
        run_command="python server.py",
        apply_improvement=apply_improvement,
        analyze_improvements=lambda completed, evaluation: {
            "summary": "Need server.",
            "next_iteration_goal": "Add server.",
            "recommended_actions": ["Add server."],
        },
        read_project_state=lambda evaluation, iteration: _project_state(project),
    )

    assert result["success"] is True
    assert evaluator.calls[0]["written_files"] == [str(app)]
    assert evaluator.calls[1]["written_files"] == [str(app), str(server)]


def test_autonomous_iteration_repair_path_reports_full_stage_chain(tmp_path) -> None:
    events: list[str] = []
    reports: list[dict] = []
    repairs: list[bool] = []
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("print(missing)\n", encoding="utf-8")
    memory_store = MemoryStore(tmp_path / "memory")
    agent = AutonomousIterationAgent(
        FailingEvaluator(),
        required_successful_improvements=1,
        max_iteration_attempts=2,
        memory_context_builder=FakeMemoryContextBuilder(),
        memory_store=memory_store,
    )

    def apply_improvement(iteration, evaluation, actions, improvement_report, is_repair):
        repairs.append(is_repair)
        reports.append(improvement_report)
        return IterationResult(
            iteration=iteration,
            validation_passed=False,
            completed_successful_iteration=False,
            applied_actions=actions,
            changed_files=[],
            success=False,
            failure_stage="Task Executor",
            failed_tool="code_generator",
            failure_reason="repair generation failed",
        )

    result = agent.run_project_pipeline(
        goal="Fix project",
        project_path=project,
        written_files=[str(project / "app.py")],
        apply_improvement=apply_improvement,
        read_project_state=lambda evaluation, iteration: _project_state(project),
        on_progress=lambda event, payload: events.append(event),
    )

    assert not result["success"]
    assert repairs == [True]
    assert reports[0]["repair"] is True
    assert reports[0]["selected_goal"]
    assert reports[0]["designed_tasks"]
    assert reports[0]["task_difficulty"]
    assert events.index("project_state") < events.index("context_loader")
    assert events.index("goal_maker_started") < events.index("goal_maker")
    assert events.index("context_loader") < events.index("goal_maker")
    assert events.index("task_designer_started") < events.index("task_designer")
    assert events.index("goal_maker") < events.index("task_designer")
    assert events.index("decomposition_started") < events.index("decomposition")
    assert events.index("task_designer") < events.index("decomposition")
    assert events.index("decomposition") < events.index("iteration_started")
    failed_memory = memory_store.load_all(MemoryType.TASK)[0]
    assert failed_memory.attributes["project_path"] == str(project.resolve())
    assert failed_memory.attributes["failure_stage"] == "Task Executor"
    assert failed_memory.attributes["failed_tool"] == "code_generator"
    assert failed_memory.attributes["selected_candidate_id"] == reports[0]["selected_candidate"]["candidate_id"]


def test_autonomous_iteration_task_executor_failure_stage_remains_compatible(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    agent = AutonomousIterationAgent(
        FakeEvaluator(),
        required_successful_improvements=1,
        max_iteration_attempts=2,
        memory_context_builder=FakeMemoryContextBuilder(),
    )

    def apply_improvement(iteration, evaluation, actions, improvement_report, is_repair):
        raise RuntimeError("tool crashed")

    result = agent.run_project_pipeline(
        goal="Improve project",
        project_path=project,
        written_files=[],
        apply_improvement=apply_improvement,
        analyze_improvements=lambda completed, evaluation: {
            "summary": "Need polish.",
            "next_iteration_goal": "Add visible polish.",
            "recommended_actions": ["Add visible polish."],
        },
        read_project_state=lambda evaluation, iteration: _project_state(project),
    )

    assert not result["success"]
    assert result["failure_stage"] == "Task Executor"
    assert result["failed_iteration"] == 1
    assert "tool crashed" in result["failure_reason"]


def test_repair_attempt_does_not_consume_successful_improvement_count(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("print('hello')\n", encoding="utf-8")
    agent = AutonomousIterationAgent(
        SequenceEvaluator([False, True, True, True, True, True, True]),
        required_successful_improvements=5,
        max_iteration_attempts=4,
        memory_context_builder=FakeMemoryContextBuilder(),
    )
    repairs: list[bool] = []

    def apply_improvement(iteration, evaluation, actions, improvement_report, is_repair):
        repairs.append(is_repair)
        return IterationResult(
            iteration=iteration,
            validation_passed=False,
            completed_successful_iteration=False,
            applied_actions=actions,
            changed_files=[str(project / "app.py")],
            success=True,
        )

    result = agent.run_project_pipeline(
        goal="Improve project",
        project_path=project,
        written_files=[str(project / "app.py")],
        apply_improvement=apply_improvement,
        analyze_improvements=lambda completed, evaluation: {
            "summary": f"Need improvement {completed}.",
            "next_iteration_goal": f"Improve feature {completed}.",
            "recommended_actions": [f"Improve feature {completed}."],
        },
        read_project_state=lambda evaluation, iteration: _project_state(project),
    )

    assert result["success"] is True
    assert repairs == [True, False, False, False, False, False]
    assert result["completed_improvements"] == 5
    assert result["repair_attempts"] == 1
    assert result["attempts_used"] == 6
    assert result["max_iteration_attempts"] >= 8
    assert result["iterations"][0].repair_completed is True
    assert result["iterations"][0].completed_successful_iteration is False


def test_failed_modification_stops_before_next_iteration_setup(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("print('hello')\n", encoding="utf-8")
    agent = AutonomousIterationAgent(
        SequenceEvaluator([False]),
        required_successful_improvements=2,
        max_iteration_attempts=1,
        memory_context_builder=FakeMemoryContextBuilder(),
    )
    events: list[str] = []

    def apply_improvement(iteration, evaluation, actions, improvement_report, is_repair):
        return IterationResult(
            iteration=iteration,
            validation_passed=False,
            completed_successful_iteration=False,
            applied_actions=actions,
            changed_files=[str(project / "app.py")],
            success=True,
        )

    result = agent.run_project_pipeline(
        goal="Improve project",
        project_path=project,
        written_files=[str(project / "app.py")],
        apply_improvement=apply_improvement,
        read_project_state=lambda evaluation, iteration: _project_state(project),
        on_progress=lambda event, payload: events.append(event),
    )

    assert result["success"] is False
    assert result["failure_stage"] == "Modification Evaluator"
    assert result["failed_tool"] == "project_evaluator"
    assert result["attempts_used"] == 1
    assert events[-1] == "iteration_completed"
    assert "max_attempts_reached" not in events
    assert events.count("iteration_started") == 1


def test_failed_optional_improvement_restores_pre_iteration_files(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    app = project / "app.py"
    app.write_text("print('verified core')\n", encoding="utf-8")
    agent = AutonomousIterationAgent(
        SequenceEvaluator([True, False]),
        required_successful_improvements=1,
        max_iteration_attempts=2,
        memory_context_builder=FakeMemoryContextBuilder(),
    )
    applied_iterations: list[int] = []

    def apply_improvement(iteration, evaluation, actions, improvement_report, is_repair):
        applied_iterations.append(iteration)
        app.write_text("raise RuntimeError('broken enhancement')\n", encoding="utf-8")
        GitManagerAgent().snapshot(project, reason="post_write_environment_sync")
        return IterationResult(
            iteration=iteration,
            validation_passed=False,
            completed_successful_iteration=False,
            applied_actions=actions,
            changed_files=[str(app)],
            success=True,
        )

    result = agent.run_project_pipeline(
        goal="Improve project",
        project_path=project,
        written_files=[str(app)],
        apply_improvement=apply_improvement,
        read_project_state=lambda evaluation, iteration: _project_state(project),
    )

    assert result["success"] is False
    assert app.read_text(encoding="utf-8") == "print('verified core')\n"
    assert result["iterations"][0].rollback_applied is True
    assert result["iterations"][0].rollback_files == [str(app)]
    assert result["iterations"][0].rollback_snapshot_ref
    assert applied_iterations == [1]


def test_project_iteration_prompt_expands_attempt_budget(monkeypatch, tmp_path) -> None:
    from ui.question_ui import QuestionUI

    monkeypatch.setattr(QuestionUI, "ask_integer", lambda *args, **kwargs: 5)
    autopilot = SimpleNamespace(
        prompt_for_project_improvement_iterations=True,
        _project_improvement_iterations_prompted=False,
        required_successful_improvements=2,
        max_iteration_attempts=4,
        enable_iterative_improvement=True,
        iterative_improvement=SimpleNamespace(required_successful_improvements=2, max_iteration_attempts=4),
        enhanced_ui=None,
        console=Console(),
    )

    should_run = ProjectIterationHelper().resolve_project_improvement_iterations(
        autopilot,
        "Improve project",
        tmp_path,
    )

    assert should_run is True
    assert autopilot.required_successful_improvements == 5
    assert autopilot.iterative_improvement.required_successful_improvements == 5
    assert autopilot.max_iteration_attempts == 8
    assert autopilot.iterative_improvement.max_iteration_attempts == 8
    assert autopilot.project_improvement_policy.requirement == "required"
    assert autopilot.project_improvement_policy.source == "user_selected"
