from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from core.config import (
    EmbeddingSettings,
    LLMSettings,
    ModelHealthSettings,
    _CODE_ROOT,
    _REPOSITORY_ROOT,
    _SHARED_REPOSITORY_ROOT,
    _discover_shared_repository_root,
)
from core.model_health import check_configured_models, run_startup_model_health_check
from ui.enhanced_cli import run_enhanced_cli


class FakeOpenAI:
    calls: list[tuple[str, str, str | None]] = []
    fail_embedding = False

    def __init__(self, *, base_url=None, **_kwargs) -> None:
        self.base_url = base_url
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))
        self.embeddings = SimpleNamespace(create=self._embedding)

    def _chat(self, *, model, messages, **_kwargs):
        self.calls.append(("chat", model, self.base_url))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])

    def _embedding(self, *, model, input, **_kwargs):
        self.calls.append(("embedding", model, self.base_url))
        if self.fail_embedding:
            raise TimeoutError("embedding endpoint timed out")
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])])


def _settings():
    llm = LLMSettings(
        base_url="https://models.example.test/v1",
        api_key="test-key",
        model="chat-test",
    )
    embedding = EmbeddingSettings(
        base_url="https://embeddings.example.test/v1",
        api_key="embedding-key",
        model="embedding-test",
    )
    health = ModelHealthSettings(enabled=True, timeout_seconds=0.5)
    return llm, embedding, health


def test_check_configured_models_reports_chat_and_embedding_latency() -> None:
    FakeOpenAI.calls = []
    FakeOpenAI.fail_embedding = False
    llm, embedding, health = _settings()

    results = check_configured_models(
        llm_settings=llm,
        embedding_settings=embedding,
        health_settings=health,
        openai_factory=FakeOpenAI,
    )

    assert [(result.model_kind, result.reachable) for result in results] == [
        ("chat", True),
        ("embedding", True),
    ]
    assert all(result.latency_ms >= 0 for result in results)
    assert sorted(FakeOpenAI.calls) == [
        ("chat", "chat-test", "https://models.example.test/v1"),
        ("embedding", "embedding-test", "https://embeddings.example.test/v1"),
    ]


def test_check_configured_models_returns_warning_detail_for_failed_endpoint() -> None:
    FakeOpenAI.calls = []
    FakeOpenAI.fail_embedding = True
    llm, embedding, health = _settings()

    results = check_configured_models(
        llm_settings=llm,
        embedding_settings=embedding,
        health_settings=health,
        openai_factory=FakeOpenAI,
    )

    assert results[0].reachable is True
    assert results[1].reachable is False
    assert results[1].detail == "embedding endpoint timed out"


def test_check_configured_models_reports_missing_configuration_without_request() -> None:
    FakeOpenAI.calls = []
    health = ModelHealthSettings(enabled=True, timeout_seconds=0.5)
    embedding = EmbeddingSettings(base_url="https://unused.test/v1", api_key="unused", model="embedding-test")
    embedding.base_url = ""
    embedding.api_key = None

    results = check_configured_models(
        llm_settings=LLMSettings(base_url="", api_key=None, model="chat-test"),
        embedding_settings=embedding,
        health_settings=health,
        openai_factory=FakeOpenAI,
    )

    assert all(result.reachable is False for result in results)
    assert all("missing configuration" in result.detail for result in results)
    assert FakeOpenAI.calls == []


def test_startup_model_health_check_prints_latency_and_warning(monkeypatch) -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)
    results = [
        SimpleNamespace(label="chat model chat-test (openai-compatible)", reachable=True, latency_ms=12, detail=""),
        SimpleNamespace(label="embedding model embedding-test (openai-compatible)", reachable=False, latency_ms=34, detail="timed out"),
    ]
    monkeypatch.setattr("core.model_health.check_configured_models", lambda **_kwargs: results)

    returned = run_startup_model_health_check(console, health_settings=ModelHealthSettings(enabled=True))

    text = output.getvalue()
    assert returned == results
    assert "chat model chat-test" in text
    assert "reachable (12 ms)" in text
    assert "WARNING" in text
    assert "timed out" in text


def test_startup_model_health_check_can_be_disabled() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)

    results = run_startup_model_health_check(console, health_settings=ModelHealthSettings(enabled=False))

    assert results == []
    assert "skipped by configuration" in output.getvalue()


def test_enhanced_cli_runs_model_health_check_before_banner(monkeypatch) -> None:
    events = []
    args = SimpleNamespace(log_file=None, once=None, improvement_iterations=0)
    monkeypatch.setattr("ui.environment_guard.block_project_venv", lambda _console: False)
    monkeypatch.setattr("ui.environment_guard.block_missing_socksio", lambda _console: False)
    monkeypatch.setattr(
        "ui.enhanced_cli.run_startup_model_health_check",
        lambda *_args, **_kwargs: events.append("health"),
    )
    monkeypatch.setattr("ui.enhanced_cli.EnhancedUI.show_banner", lambda _self: events.append("banner"))
    monkeypatch.setattr("ui.enhanced_cli._run_interactive_mode", lambda *_args, **_kwargs: 0)

    exit_code = run_enhanced_cli(args, console=Console(file=StringIO()))

    assert exit_code == 0
    assert events == ["health", "banner"]


def test_enhanced_cli_skips_configured_model_probe_for_injected_client(monkeypatch) -> None:
    events = []
    args = SimpleNamespace(log_file=None, once=None, improvement_iterations=0)
    monkeypatch.setattr("ui.environment_guard.block_project_venv", lambda _console: False)
    monkeypatch.setattr("ui.environment_guard.block_missing_socksio", lambda _console: False)
    monkeypatch.setattr(
        "ui.enhanced_cli.run_startup_model_health_check",
        lambda *_args, **_kwargs: events.append("health"),
    )
    monkeypatch.setattr("ui.enhanced_cli.EnhancedUI.show_banner", lambda _self: events.append("banner"))
    monkeypatch.setattr("ui.enhanced_cli._run_interactive_mode", lambda *_args, **_kwargs: 0)

    exit_code = run_enhanced_cli(args, console=Console(file=StringIO()), llm_client=object())

    assert exit_code == 0
    assert events == ["banner"]


def test_settings_search_repository_and_code_env_files() -> None:
    env_files = tuple(str(path) for path in LLMSettings.model_config["env_file"])

    assert str(_SHARED_REPOSITORY_ROOT / ".env") in env_files
    assert str(_REPOSITORY_ROOT / ".env") in env_files
    assert str(_CODE_ROOT / ".env") in env_files
    assert env_files[-1] == ".env"


def test_settings_discovers_main_checkout_from_linked_worktree(tmp_path) -> None:
    main_checkout = tmp_path / "main"
    common_git_dir = main_checkout / ".git"
    linked_checkout = tmp_path / "linked"
    linked_git_dir = common_git_dir / "worktrees" / "linked"
    linked_git_dir.mkdir(parents=True)
    linked_checkout.mkdir()
    (linked_checkout / ".git").write_text(f"gitdir: {linked_git_dir}\n", encoding="utf-8")
    (linked_git_dir / "commondir").write_text("../..\n", encoding="utf-8")

    assert _discover_shared_repository_root(linked_checkout) == main_checkout.resolve()
