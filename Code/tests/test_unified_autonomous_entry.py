from __future__ import annotations

from types import SimpleNamespace

from autonomous_iteration.bounded_model_response import BoundedModelResponseController
from autonomous_iteration.iteration_turn_store import IterationTurnStore
from core.config import LLMSettings
from metadata import ConversationIdentity, SessionIngressState, TaskRouteMetadata
from ui import enhanced_cli


class _Console:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *args, **_kwargs) -> None:
        self.messages.append(" ".join(str(arg) for arg in args))


class _UI:
    def __init__(self) -> None:
        self.console = _Console()


def _route(route: str) -> TaskRouteMetadata:
    return TaskRouteMetadata(route=route, confidence=1.0, reason="test route")


def _settings() -> LLMSettings:
    return LLMSettings(provider="openai", model="gpt-test", api_key="")


def test_feature_flagged_interactive_runtime_question_skips_autopilot(
    tmp_path, monkeypatch
) -> None:
    store = IterationTurnStore(tmp_path)
    ui = _UI()
    monkeypatch.setenv("OPENPILOT_UNIFIED_AUTONOMOUS_ENTRY_ENABLED", "1")
    monkeypatch.setattr(enhanced_cli, "_runtime_diagnostics_enabled", lambda: False)
    monkeypatch.setattr(enhanced_cli, "_iteration_turn_store", lambda: store)
    monkeypatch.setattr(
        enhanced_cli,
        "_classify_task_route",
        lambda _goal: _route("autonomous_iteration"),
    )
    monkeypatch.setattr(
        enhanced_cli,
        "_execute_autopilot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("autopilot called")),
    )
    ingress = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-0",
            turn_index=0,
            project_root=str(tmp_path.resolve()),
        )
    )

    result = enhanced_cli._execute_goal_interactive(
        "你使用的是什么模型？",
        ui,
        tracker=None,
        llm_client=object(),
        logger=None,
        runtime_options=enhanced_cli.OpenPilotRuntimeOptions(),
        ingress_state=ingress,
        settings=_settings(),
    )

    assert result.turns[-1].role == "assistant"
    assert result.turns[-1].content == "Model: gpt-test"
    assert any("Model: gpt-test" in message for message in ui.console.messages)


def test_feature_flagged_greeting_completes_inside_autonomous_iteration_without_autopilot(
    tmp_path, monkeypatch
) -> None:
    store = IterationTurnStore(tmp_path)
    ui = _UI()
    monkeypatch.setenv("OPENPILOT_UNIFIED_AUTONOMOUS_ENTRY_ENABLED", "1")
    monkeypatch.setattr(enhanced_cli, "_runtime_diagnostics_enabled", lambda: False)
    monkeypatch.setattr(enhanced_cli, "_iteration_turn_store", lambda: store)
    monkeypatch.setattr(
        enhanced_cli,
        "_classify_task_route",
        lambda _goal: _route("autonomous_iteration"),
    )
    monkeypatch.setattr(
        enhanced_cli,
        "_try_deterministic_runtime_response",
        lambda *_args, **_kwargs: None,
    )
    ingress = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-0",
            turn_index=0,
            project_root=str(tmp_path.resolve()),
        )
    )
    completed = SimpleNamespace(
        evidence_required=False,
        content="你好！有什么我可以帮你的吗？",
        ingress=ingress,
    )
    monkeypatch.setattr(
        BoundedModelResponseController,
        "complete",
        lambda *_args, **_kwargs: completed,
    )
    monkeypatch.setattr(
        enhanced_cli,
        "_runtime_fact_projection",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        enhanced_cli,
        "_execute_autopilot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("autopilot called")),
    )

    result = enhanced_cli._execute_goal_interactive(
        "你好",
        ui,
        tracker=None,
        llm_client=object(),
        logger=None,
        runtime_options=enhanced_cli.OpenPilotRuntimeOptions(),
        ingress_state=ingress,
        settings=_settings(),
    )

    assert result is ingress
    assert any("Task route: autonomous_iteration" in message for message in ui.console.messages)
    assert any("你好！有什么我可以帮你的吗？" in message for message in ui.console.messages)


def test_feature_flagged_project_file_request_keeps_legacy_autopilot(monkeypatch) -> None:
    ui = _UI()
    calls: list[str] = []
    monkeypatch.setenv("OPENPILOT_UNIFIED_AUTONOMOUS_ENTRY_ENABLED", "1")
    monkeypatch.setattr(enhanced_cli, "_runtime_diagnostics_enabled", lambda: False)
    monkeypatch.setattr(
        enhanced_cli,
        "_classify_task_route",
        lambda _goal: _route("autonomous_iteration"),
    )
    monkeypatch.setattr(
        enhanced_cli,
        "_try_deterministic_runtime_response",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        BoundedModelResponseController,
        "complete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("bounded response called")
        ),
    )
    monkeypatch.setattr(
        enhanced_cli,
        "_execute_autopilot",
        lambda goal, *_args, **_kwargs: calls.append(goal) or "legacy-result",
    )
    ingress = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-0",
            turn_index=0,
            project_root="/tmp/project",
        )
    )

    result = enhanced_cli._execute_goal_interactive(
        "读取 Code/pyproject.toml 的项目版本号并回答，不要修改文件",
        ui,
        tracker=None,
        llm_client=object(),
        logger=None,
        runtime_options=enhanced_cli.OpenPilotRuntimeOptions(),
        ingress_state=ingress,
        settings=_settings(),
    )

    assert result.turns[-1].content == "Execution result: legacy-result"
    assert calls == ["读取 Code/pyproject.toml 的项目版本号并回答，不要修改文件"]


def test_feature_flagged_snake_game_request_enters_project_execution(monkeypatch) -> None:
    ui = _UI()
    calls: list[str] = []
    monkeypatch.setenv("OPENPILOT_UNIFIED_AUTONOMOUS_ENTRY_ENABLED", "1")
    monkeypatch.setattr(enhanced_cli, "_runtime_diagnostics_enabled", lambda: False)
    monkeypatch.setattr(
        enhanced_cli,
        "_classify_task_route",
        lambda _goal: _route("autonomous_iteration"),
    )
    monkeypatch.setattr(
        enhanced_cli,
        "_try_deterministic_runtime_response",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        BoundedModelResponseController,
        "complete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("bounded response called")
        ),
    )
    monkeypatch.setattr(
        enhanced_cli,
        "_execute_autopilot",
        lambda goal, *_args, **_kwargs: calls.append(goal) or "project-result",
    )
    ingress = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-0",
            turn_index=0,
            project_root="/tmp/project",
        )
    )

    result = enhanced_cli._execute_goal_interactive(
        "帮我开发一个贪吃蛇小游戏",
        ui,
        tracker=None,
        llm_client=object(),
        logger=None,
        runtime_options=enhanced_cli.OpenPilotRuntimeOptions(),
        ingress_state=ingress,
        settings=_settings(),
    )

    assert result.turns[-1].content == "Execution result: project-result"
    assert calls == ["帮我开发一个贪吃蛇小游戏"]
    assert any("Task route: autonomous_iteration" in message for message in ui.console.messages)


def test_unified_response_scope_does_not_match_project_word_substrings() -> None:
    assert enhanced_cli._unified_autonomous_entry_scope(
        "What is your profile?"
    ) == enhanced_cli._UnifiedAutonomousEntryScope.RESPONSE_CANARY


def test_feature_flagged_once_runtime_question_skips_runtime_construction(
    tmp_path, monkeypatch
) -> None:
    store = IterationTurnStore(tmp_path)
    ui = _UI()
    monkeypatch.setenv("OPENPILOT_UNIFIED_AUTONOMOUS_ENTRY_ENABLED", "1")
    monkeypatch.setattr(enhanced_cli, "_runtime_diagnostics_enabled", lambda: False)
    monkeypatch.setattr(enhanced_cli, "_iteration_turn_store", lambda: store)
    monkeypatch.setattr(
        enhanced_cli,
        "_classify_task_route",
        lambda _goal: _route("autonomous_iteration"),
    )

    status = enhanced_cli._run_once_mode(
        "What model are you using?",
        ui,
        tracker=None,
        logger=None,
        settings=_settings(),
        runtime_options=enhanced_cli.OpenPilotRuntimeOptions(),
        llm_client=object(),
        project_path=str(tmp_path),
    )

    assert status == 0
    assert any("Model: gpt-test" in message for message in ui.console.messages)


def test_agent_generator_route_bypasses_unified_entry_under_flag(monkeypatch) -> None:
    ui = _UI()
    calls: list[str] = []
    monkeypatch.setenv("OPENPILOT_UNIFIED_AUTONOMOUS_ENTRY_ENABLED", "1")
    monkeypatch.setattr(
        enhanced_cli,
        "_classify_task_route",
        lambda _goal: _route("agent_generator"),
    )
    monkeypatch.setattr(
        enhanced_cli,
        "_execute_agent_generator",
        lambda goal, *_args, **_kwargs: calls.append(goal) or "agent-result",
    )
    monkeypatch.setattr(
        enhanced_cli,
        "_try_deterministic_runtime_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unified entry called")),
    )

    result = enhanced_cli._execute_goal_interactive(
        "Create a reusable research agent",
        ui,
        tracker=None,
        llm_client=object(),
        logger=None,
        runtime_options=enhanced_cli.OpenPilotRuntimeOptions(),
        settings=_settings(),
    )

    assert result == "agent-result"
    assert calls == ["Create a reusable research agent"]


def test_current_external_candidate_uses_governed_runtime_bridge(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENPILOT_UNIFIED_AUTONOMOUS_ENTRY_ENABLED", "1")
    monkeypatch.setattr(
        enhanced_cli,
        "_try_deterministic_runtime_response",
        lambda *_args, **_kwargs: None,
    )
    candidate = SimpleNamespace(evidence_required=True, record=object(), ingress=object())
    monkeypatch.setattr(
        BoundedModelResponseController,
        "complete",
        lambda *_args, **_kwargs: candidate,
    )
    monkeypatch.setattr(
        enhanced_cli,
        "_runtime_fact_projection",
        lambda **_kwargs: object(),
    )
    calls = []
    expected = SimpleNamespace(content="grounded", ingress=object())
    monkeypatch.setattr(
        enhanced_cli,
        "_execute_response_evidence_task",
        lambda observed, **_kwargs: calls.append(observed) or expected,
    )
    ingress = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-1",
            turn_index=1,
            project_root=str(tmp_path),
        )
    )

    result = enhanced_cli._try_unified_autonomous_response(
        "What is the latest weather?",
        ingress_state=ingress,
        settings=_settings(),
        runtime_options=enhanced_cli.OpenPilotRuntimeOptions(),
        llm_client=object(),
        ui=_UI(),
        tracker=None,
        logger=None,
    )

    assert result is expected
    assert calls == [candidate]


def test_evidence_execution_failure_does_not_fall_back_to_legacy_pipeline(
    monkeypatch,
    tmp_path,
) -> None:
    ui = _UI()
    monkeypatch.setenv("OPENPILOT_UNIFIED_AUTONOMOUS_ENTRY_ENABLED", "1")
    monkeypatch.setattr(enhanced_cli, "_runtime_diagnostics_enabled", lambda: False)
    monkeypatch.setattr(
        enhanced_cli,
        "_classify_task_route",
        lambda _goal: _route("autonomous_iteration"),
    )
    monkeypatch.setattr(
        enhanced_cli,
        "_try_unified_autonomous_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sensitive detail")),
    )
    monkeypatch.setattr(
        enhanced_cli,
        "_execute_autopilot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy fallback called")),
    )
    ingress = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-0",
            turn_index=0,
            project_root=str(tmp_path),
        )
    )

    result = enhanced_cli._execute_goal_interactive(
        "What is in this project?",
        ui,
        tracker=None,
        llm_client=object(),
        logger=None,
        runtime_options=enhanced_cli.OpenPilotRuntimeOptions(),
        ingress_state=ingress,
        settings=_settings(),
    )

    assert result.turns[-1].role == "user"
    rendered = "\n".join(ui.console.messages)
    assert "Autonomous iteration stopped before completion" in rendered
    assert "sensitive detail" not in rendered


def test_bounded_response_failure_has_specific_error_title(monkeypatch, tmp_path) -> None:
    ui = _UI()
    errors: list[tuple[str, str]] = []
    ui.show_error = lambda title, details: errors.append((title, details))
    monkeypatch.setenv("OPENPILOT_UNIFIED_AUTONOMOUS_ENTRY_ENABLED", "1")
    monkeypatch.setattr(enhanced_cli, "_runtime_diagnostics_enabled", lambda: False)
    monkeypatch.setattr(enhanced_cli, "_classify_task_route", lambda _goal: _route("autonomous_iteration"))
    monkeypatch.setattr(enhanced_cli, "_try_deterministic_runtime_response", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        BoundedModelResponseController,
        "complete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sensitive detail")),
    )
    ingress = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-0",
            turn_index=0,
            project_root=str(tmp_path),
        )
    )

    enhanced_cli._execute_goal_interactive(
        "你好，请介绍一下你自己",
        ui,
        tracker=None,
        llm_client=object(),
        logger=None,
        runtime_options=enhanced_cli.OpenPilotRuntimeOptions(),
        ingress_state=ingress,
        settings=_settings(),
    )

    assert errors[0][0] == "Bounded response failed"
    assert "Stage: Bounded Response" in errors[0][1]
    assert "sensitive detail" not in errors[0][1]


def test_external_evidence_failure_has_specific_error_title(monkeypatch, tmp_path) -> None:
    ui = _UI()
    errors: list[tuple[str, str]] = []
    ui.show_error = lambda title, details: errors.append((title, details))
    monkeypatch.setenv("OPENPILOT_UNIFIED_AUTONOMOUS_ENTRY_ENABLED", "1")
    monkeypatch.setattr(enhanced_cli, "_runtime_diagnostics_enabled", lambda: False)
    monkeypatch.setattr(enhanced_cli, "_classify_task_route", lambda _goal: _route("autonomous_iteration"))
    monkeypatch.setattr(enhanced_cli, "_try_deterministic_runtime_response", lambda *_args, **_kwargs: None)
    candidate = SimpleNamespace(evidence_required=True, record=object(), ingress=object())
    monkeypatch.setattr(BoundedModelResponseController, "complete", lambda *_args, **_kwargs: candidate)
    monkeypatch.setattr(enhanced_cli, "_runtime_fact_projection", lambda **_kwargs: object())
    monkeypatch.setattr(
        enhanced_cli,
        "_execute_response_evidence_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sensitive detail")),
    )
    ingress = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id="conversation-1",
            run_id="run-0",
            turn_index=0,
            project_root=str(tmp_path),
        )
    )

    enhanced_cli._execute_goal_interactive(
        "今天常熟的天气怎么样",
        ui,
        tracker=None,
        llm_client=object(),
        logger=None,
        runtime_options=enhanced_cli.OpenPilotRuntimeOptions(),
        ingress_state=ingress,
        settings=_settings(),
    )

    assert errors[0][0] == "External evidence failed"
    assert "Stage: External Evidence" in errors[0][1]
    assert "sensitive detail" not in errors[0][1]


def test_project_execution_exception_failure_has_specific_stage() -> None:
    failure = enhanced_cli._cli_exception_failure(
        RuntimeError("sensitive detail"),
        task_id="task-1",
        stage=enhanced_cli.UnifiedEntryFailureStage.PROJECT_EXECUTION,
    )

    rendered = enhanced_cli._format_failure_details(failure)

    assert "Stage: Project Execution" in rendered
    assert "Error Type: RuntimeError" in rendered
    assert "sensitive detail" not in rendered
