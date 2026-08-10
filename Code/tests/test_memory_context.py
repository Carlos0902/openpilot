from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from autonomous_iteration.agents.context_loader import ContextLoaderAgent
from core.exceptions import ContextAssemblyBudgetError, ContextSourceError
from memory.context_builder import MEMORY_CONTEXT_ADAPTER_VERSION, MemoryContextBuilder
from memory.rolling_compaction import (
    RollingSummaryAdapter,
    RollingSummaryAttemptEvidence,
    RollingSummaryRequest,
)
from memory.memory_models import MemoryRecord, MemoryType
from memory.memory_store import MemoryStore
from memory.project_manager import ProjectManager
from memory.short_memory import ShortMemory
from memory.tool.memory_context_tool import memory_context_executor
from metadata import (
    ContextCandidate,
    ContextCandidateKind,
    DurableArtifactReference,
    ToolInputMetadata,
)


def test_context_loader_does_not_refresh_project_index_or_sketch(tmp_path) -> None:
    project = tmp_path / "read-only-project"
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")

    builder = MemoryContextBuilder(memory_store=MemoryStore(tmp_path / "memory"))

    ContextLoaderAgent(memory_context_builder=builder).run(
        "inspect the project",
        project,
    )

    assert not (project / "sketch.json").exists()
    assert not (project / ".openpilot" / "file_indexes").exists()


def test_context_loader_fails_closed_when_memory_source_is_unavailable(tmp_path) -> None:
    class BrokenMemoryStore:
        def query(self, *args, **kwargs):
            raise OSError("memory store unavailable")

        def load_all(self, *args, **kwargs):
            raise OSError("memory store unavailable")

    builder = MemoryContextBuilder(memory_store=BrokenMemoryStore())

    with pytest.raises(ContextSourceError) as exc_info:
        ContextLoaderAgent(memory_context_builder=builder).run(
            "inspect the project",
            tmp_path,
        )

    assert exc_info.value.context["source"] == "related_memories"
    assert exc_info.value.context["cause_type"] == "OSError"


def test_context_loader_fails_closed_when_checkpoint_snapshot_cannot_be_saved(tmp_path) -> None:
    builder = MemoryContextBuilder(memory_store=MemoryStore(tmp_path / "memory"))

    def fail_snapshot(*args, **kwargs):
        raise OSError("checkpoint unavailable")

    builder.set_checkpoint_handlers(snapshot_sink=fail_snapshot)

    with pytest.raises(ContextSourceError) as exc_info:
        ContextLoaderAgent(memory_context_builder=builder).run(
            "inspect the project",
            tmp_path,
        )

    assert exc_info.value.context["source"] == "context_snapshot"
    assert exc_info.value.context["cause_type"] == "OSError"


def test_project_manager_updates_sketch_and_searches_by_content(tmp_path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    source = project / "app.py"
    source.write_text(
        "def render_dashboard():\n    return 'pineapple project dashboard'\n",
        encoding="utf-8",
    )

    manager = ProjectManager(project)
    update_result = manager.update(project)
    results = manager.search("pineapple dashboard")

    sketch = project / "sketch.json"
    assert sketch.exists()
    sketch_payload = json.loads(sketch.read_text(encoding="utf-8"))
    assert "app.py" in sketch_payload["files"]
    assert "function_description" in sketch_payload["files"]["app.py"]
    assert "semantic_info" in sketch_payload["files"]["app.py"]
    content_index = sketch_payload["files"]["app.py"]["content_index"]
    assert content_index["language"] == "python"
    assert content_index["sections"][0]["title"] == "function render_dashboard"
    assert content_index["sections"][0]["line_start"] == 1
    assert content_index["sections"][0]["line_end"] == 2
    index_file = content_index["index_file"]
    index_payload = json.loads(Path(index_file).read_text(encoding="utf-8"))
    assert index_payload["kind"] == "file_content_index"
    assert index_payload["relative_path"] == "app.py"
    assert index_payload["sections"][0]["embedding"]
    assert update_result["file_count"] == 1
    assert results[0]["name"] == "app.py"
    assert "pineapple project dashboard" in results[0]["description"]


def test_memory_context_builder_combines_dialog_memory_files_and_environment(tmp_path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    (project / "game.py").write_text("import pygame\n# arcade scene loop\n", encoding="utf-8")

    store = MemoryStore(tmp_path / "memory")
    store.save(
        MemoryRecord(
            id="project-memory",
            memory_type=MemoryType.PROJECT,
                content="The project should prioritize pygame arcade polish.",
                tags=["pygame", "arcade"],
                confidence=0.9,
                attributes={"project_path": str(project)},
        )
    )
    store.save(
        MemoryRecord(
            id="env-memory",
            memory_type=MemoryType.SHORT_TERM,
                content="Project environment for demo: pygame installed.",
                tags=["project_environment", "demo", "pygame"],
                confidence=0.95,
                attributes={"project_path": str(project)},
        )
    )
    short_memory = ShortMemory(repo_path=tmp_path)
    short_memory.add_message("user", "保持用户原话，不要改写。")
    short_memory.add_message("assistant", "Compressed assistant note.")
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=store,
        project_manager=ProjectManager(project),
    )

    context = builder.build(
        "pygame arcade",
        project_path=project,
        limit=5,
        system_prompt="Fixed autonomous iteration prompt.",
    )

    assert context["system_prompt"] == "Fixed autonomous iteration prompt."
    assert context["dialog_context"][0]["content"] == "保持用户原话，不要改写。"
    assert context["related_memories"][0]["id"] == "project-memory"
    assert context["related_files"][0]["name"] == "game.py"
    assert context["environment_context"][0]["id"] == "env-memory"
    prompt_text = context["prompt_text"]
    assert prompt_text.startswith("## System Prompt\nFixed autonomous iteration prompt.")
    assert prompt_text.index("## System Prompt") < prompt_text.index("## Dialog Context")
    assert prompt_text.index("## Dialog Context") < prompt_text.index("## Related Files")
    assert prompt_text.index("## Related Files") < prompt_text.index("## Related Memories")
    assert prompt_text.index("## Related Memories") < prompt_text.index("## Environment Context")
    assert "pygame arcade polish" in context["prompt_text"]
    assert context["context_selection"]["kind"] == "context_selection"
    assert context["context_selection"]["truncated"] is False
    assert context["context_selection"]["original_prompt_chars"] == len(prompt_text)
    assert context["context_selection"]["final_prompt_chars"] == len(prompt_text)


def test_memory_context_builder_filters_project_memories_by_canonical_project_identity(
    tmp_path,
) -> None:
    project = tmp_path / "calculator"
    project.mkdir()
    alias_root = tmp_path / "project-alias"
    alias_root.symlink_to(tmp_path, target_is_directory=True)
    aliased_project = alias_root / project.name
    other_project = tmp_path / "snake"
    other_project.mkdir()

    store = MemoryStore(tmp_path / "memory")
    store.save(
        MemoryRecord(
            id="current-project-via-alias",
            memory_type=MemoryType.PROJECT,
            content="Shared iteration evidence for the calculator project.",
            tags=["shared", "iteration"],
            confidence=0.9,
            attributes={"project_path": str(aliased_project)},
        )
    )
    store.save(
        MemoryRecord(
            id="other-project",
            memory_type=MemoryType.PROJECT,
            content="Shared iteration evidence for the snake project.",
            tags=["shared", "iteration"],
            confidence=1.0,
            attributes={"project_path": str(other_project)},
        )
    )

    context = MemoryContextBuilder(memory_store=store).build(
        "shared iteration evidence",
        project_path=project,
        include_environment=False,
        limit=10,
    )

    assert context["project_path"] == str(project)
    assert [record["id"] for record in context["related_memories"]] == [
        "current-project-via-alias"
    ]
    assert "calculator project" in context["prompt_text"]
    assert "snake project" not in context["prompt_text"]


def test_memory_context_builder_treats_var_and_private_var_as_one_project_identity(
    tmp_path,
) -> None:
    project = tmp_path / "calculator"
    project.mkdir()
    canonical_path = str(project.resolve())
    if not canonical_path.startswith("/private/var/"):
        pytest.skip("Darwin /var alias is not available in this test environment")
    var_alias = canonical_path.removeprefix("/private")

    store = MemoryStore(tmp_path / "memory")
    store.save(
        MemoryRecord(
            id="var-alias-project",
            memory_type=MemoryType.PROJECT,
            content="Alias-specific calculator memory.",
            tags=["alias-specific"],
            confidence=0.9,
            attributes={"project_path": var_alias},
        )
    )
    store.save(
        MemoryRecord(
            id="unrelated-var-project",
            memory_type=MemoryType.PROJECT,
            content="Alias-specific memory for an unrelated project.",
            tags=["alias-specific"],
            confidence=1.0,
            attributes={"project_path": str(tmp_path / "unrelated")},
        )
    )

    context = MemoryContextBuilder(memory_store=store).build(
        "alias-specific",
        project_path=canonical_path,
        include_environment=False,
    )

    assert [record["id"] for record in context["related_memories"]] == [
        "var-alias-project"
    ]


def test_memory_context_builder_enforces_prompt_budget_and_records_selection_boundary(tmp_path) -> None:
    short_memory = ShortMemory(repo_path=tmp_path)
    for index in range(5):
        short_memory.add_message("user", f"message-{index}-" + (str(index) * 180))
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "memory"),
        max_prompt_chars=500,
    )

    context = builder.build(
        "budgeted context",
        project_path=None,
        include_environment=False,
        limit=10,
        system_prompt="Keep the latest dialog evidence.",
    )

    selection = context["context_selection"]
    dialog_decision = next(
        decision for decision in selection["section_decisions"] if decision["section"] == "dialog_context"
    )

    assert len(context["prompt_text"]) <= 500
    assert selection["max_prompt_chars"] == 500
    assert selection["budget_unit"] == "characters"
    assert selection["token_count_method"] == "unavailable"
    assert selection["original_prompt_chars"] > selection["final_prompt_chars"]
    assert selection["final_prompt_chars"] == len(context["prompt_text"])
    assert selection["truncated"] is True
    assert selection["strategy"] == "retention_priority_order_v1"
    assert context["dialog_context"][-1]["content"].startswith("message-4-")
    assert not any(item["content"].startswith("message-0-") for item in context["dialog_context"])
    assert dialog_decision["action"] == "partially_kept"
    assert dialog_decision["reason"] == "prompt_budget"
    assert selection["dialog_messages_total"] == 5
    assert selection["dialog_messages_selected"] == len(context["dialog_context"])
    assert selection["dialog_start_index"] == 5 - len(context["dialog_context"])
    assert selection["oldest_selected_dialog_timestamp"] == context["dialog_context"][0]["timestamp"]


def test_memory_context_tool_returns_stable_context_without_llm(tmp_path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("Pygame arcade demo", encoding="utf-8")
    store = MemoryStore(tmp_path / "memory")
    store.save(
        MemoryRecord(
            id="memory-1",
            memory_type=MemoryType.PROJECT,
                content="Remember pygame demo requirements.",
                tags=["pygame"],
                confidence=0.8,
                attributes={"project_path": str(project)},
        )
    )
    short_memory = ShortMemory(repo_path=tmp_path)
    short_memory.add_message("user", "Build a pygame demo.")

    result = memory_context_executor(
        ToolInputMetadata.from_mapping("memory_context", {
            "query": "pygame demo",
            "project_path": str(project),
            "_memory_store": store,
            "_short_memory": short_memory,
            "_project_manager": ProjectManager(project),
            "system_prompt": "Tool caller fixed prompt.",
        })
    )

    assert result["system_prompt"] == "Tool caller fixed prompt."
    assert result["dialog_context"][0]["content"] == "Build a pygame demo."
    assert result["related_memories"][0]["id"] == "memory-1"
    assert result["related_files"][0]["name"] == "README.md"
    assert result["prompt_text"].startswith("## System Prompt\nTool caller fixed prompt.")
    assert "## Related Files" in result["prompt_text"]


def test_memory_context_tool_forwards_explicit_total_character_budget(tmp_path) -> None:
    short_memory = ShortMemory(repo_path=tmp_path)
    for index in range(4):
        short_memory.add_message("user", f"tool-message-{index}-" + ("x" * 220))

    result = memory_context_executor(
        ToolInputMetadata.from_mapping(
            "memory_context",
            {
                "query": "budget",
                "project_path": str(tmp_path),
                "include_environment": False,
                "max_total_chars": 420,
                "_memory_store": MemoryStore(tmp_path / "memory"),
                "_short_memory": short_memory,
            },
        )
    )

    assert len(result["prompt_text"]) <= 420
    assert result["context_selection"]["max_prompt_chars"] == 420
    assert result["context_selection"]["truncated"] is True


def test_memory_context_builder_replays_checkpoint_payload_before_reading_changed_sources(tmp_path) -> None:
    short_memory = ShortMemory(repo_path=tmp_path)
    short_memory.add_message("user", "original durable context")
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "memory"),
    )
    captured: list[tuple[str, dict]] = []
    builder.set_checkpoint_handlers(
        snapshot_sink=lambda request_hash, payload: captured.append((request_hash, payload)),
    )
    original = builder.build(
        "resume context",
        include_environment=False,
        system_prompt="fixed system",
    )
    assert len(captured) == 1

    short_memory.context_manager.clear()
    short_memory.add_message("user", "changed context that must not replace durable input")
    request_hash, durable_payload = captured[0]
    builder.set_checkpoint_handlers(
        replay_provider=lambda candidate_hash: durable_payload
        if candidate_hash == request_hash
        else None,
    )
    replayed = builder.build(
        "resume context",
        include_environment=False,
        system_prompt="fixed system",
    )

    assert replayed == original
    assert "original durable context" in replayed["prompt_text"]
    assert "changed context" not in replayed["prompt_text"]


def test_memory_context_builder_enforces_exact_token_budget_and_records_tokenizer(tmp_path) -> None:
    class MixedLanguageCounter:
        available = True
        tokenizer_id = "test-provider-tokenizer"
        model = "test-model"

        @staticmethod
        def count_text(text: str) -> int:
            return sum(2 if ord(character) > 127 else 1 for character in text)

    short_memory = ShortMemory(repo_path=tmp_path)
    short_memory.add_message("user", "中文上下文" * 30)
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "memory"),
        token_counter=MixedLanguageCounter(),
        max_prompt_tokens=90,
    )

    context = builder.build(
        "token budget",
        include_environment=False,
        system_prompt="fixed",
    )
    selection = context["context_selection"]

    assert selection["budget_unit"] == "tokens"
    assert selection["token_count_method"] == "provider_tokenizer"
    assert selection["tokenizer_id"] == "test-provider-tokenizer"
    assert selection["model"] == "test-model"
    assert selection["original_prompt_tokens"] > 90
    assert selection["final_prompt_tokens"] <= 90
    assert selection["final_prompt_tokens"] == MixedLanguageCounter.count_text(context["prompt_text"])


def test_memory_context_builder_rejects_control_instruction_that_cannot_fit(tmp_path) -> None:
    builder = MemoryContextBuilder(
        short_memory=ShortMemory(repo_path=tmp_path),
        memory_store=MemoryStore(tmp_path / "memory"),
        max_prompt_chars=256,
    )

    with pytest.raises(ContextAssemblyBudgetError) as exc_info:
        builder.build(
            "control boundary",
            include_environment=False,
            system_prompt="MUST PRESERVE THIS CONTROL INSTRUCTION. " * 100,
        )

    assert exc_info.value.context["omitted_required_candidate_ids"] == [
        "legacy:system_prompt"
    ]


def test_memory_context_builder_emits_per_source_candidate_decisions(tmp_path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    (project / "app.py").write_text("def render_dashboard():\n    return 'ok'\n", encoding="utf-8")
    store = MemoryStore(tmp_path / "memory")
    store.save(
        MemoryRecord(
            id="memory-project-rule",
            memory_type=MemoryType.PROJECT,
                content="Preserve the dashboard route.",
                tags=["dashboard"],
                confidence=0.9,
                attributes={"project_path": str(project)},
        )
    )
    short_memory = ShortMemory(repo_path=tmp_path)
    short_memory.add_message("user", "Inspect the dashboard route.")
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=store,
        project_manager=ProjectManager(project),
    )

    context = builder.build(
        "dashboard route",
        project_path=project,
        include_environment=False,
        system_prompt="Preserve project constraints.",
    )

    selection = context["context_selection"]
    decisions = selection["candidate_decisions"]
    assert selection["strategy"] == "retention_priority_order_v1"
    assert {decision["kind"] for decision in decisions} >= {
        "instruction",
        "dialog",
        "project_file",
        "memory",
    }
    assert any(
        decision["candidate_id"] == "memory:memory-project-rule"
        and decision["source_id"] == "memory-project-rule"
        for decision in decisions
    )
    assert any(
        decision["kind"] == "project_file"
        and decision["source_id"] == str(project / "app.py")
        for decision in decisions
    )
    decision_by_kind = {decision["kind"]: decision for decision in decisions}
    assert decision_by_kind["instruction"]["trust"] == "authoritative"
    assert decision_by_kind["instruction"]["freshness"] == "current"
    assert decision_by_kind["dialog"]["trust"] == "direct"
    assert decision_by_kind["project_file"]["trust"] == "observed"
    assert decision_by_kind["memory"]["trust"] == "retrieved"
    assert decision_by_kind["memory"]["freshness"] == "historical"


def test_memory_context_builder_uses_typed_adapter_not_legacy_assemble(tmp_path) -> None:
    short_memory = ShortMemory(repo_path=tmp_path)
    short_memory.add_message("user", "typed adapter")
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "memory"),
    )

    def reject_legacy(*args, **kwargs):
        raise AssertionError("legacy section assembly must not be called")

    builder.context_assembler.assemble = reject_legacy

    context = builder.build("typed adapter", include_environment=False)

    assert "typed adapter" in context["prompt_text"]
    assert context["context_selection"]["candidate_decisions"]


def test_memory_context_builder_typed_dialog_selection_is_a_recent_suffix(tmp_path) -> None:
    short_memory = ShortMemory(repo_path=tmp_path)
    for index in range(8):
        short_memory.add_message("assistant", f"dialog-{index}-" + (str(index) * 120))
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "memory"),
        max_prompt_chars=520,
    )

    context = builder.build(
        "recent suffix",
        include_environment=False,
        limit=8,
        system_prompt="Keep recent dialog.",
    )

    selected_indexes = [
        int(item["content"].split("-", 2)[1]) for item in context["dialog_context"]
    ]
    assert selected_indexes == list(range(selected_indexes[0], 8))
    dialog_decisions = [
        decision
        for decision in context["context_selection"]["candidate_decisions"]
        if decision["kind"] == "dialog"
    ]
    assert any(decision["action"] == "omitted" for decision in dialog_decisions)
    assert dialog_decisions[-1]["action"] != "omitted"


def test_memory_context_builder_versions_typed_adapter_request_hash(tmp_path) -> None:
    assert MEMORY_CONTEXT_ADAPTER_VERSION == "typed_memory_candidates_segmented_compaction_v5"
    builder = MemoryContextBuilder(
        short_memory=ShortMemory(repo_path=tmp_path),
        memory_store=MemoryStore(tmp_path / "memory"),
    )
    captured: list[str] = []
    builder.set_checkpoint_handlers(
        snapshot_sink=lambda request_hash, payload: captured.append(request_hash),
    )
    legacy_hash = builder.context_assembler.request_hash(
        {
            "query": "versioned",
            "project_path": "",
            "include_environment": False,
            "limit": 10,
            "system_prompt": "fixed",
        },
        max_prompt_chars=builder.max_prompt_chars,
        max_prompt_tokens=builder.max_prompt_tokens,
    )

    builder.build(
        "versioned",
        include_environment=False,
        system_prompt="fixed",
    )

    assert captured
    assert captured[0] != legacy_hash


def test_memory_context_builder_projects_governed_duplicate_reason_to_section(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory")
    for memory_id, confidence in (("duplicate-low", 0.6), ("duplicate-high", 0.9)):
        store.save(
            MemoryRecord(
                id=memory_id,
                memory_type=MemoryType.PROJECT,
                content="Preserve the exact dashboard route.",
                tags=["dashboard"],
                confidence=confidence,
            )
        )
    builder = MemoryContextBuilder(
        short_memory=ShortMemory(repo_path=tmp_path),
        memory_store=store,
    )

    context = builder.build("dashboard route", include_environment=False)

    memory_decisions = [
        decision
        for decision in context["context_selection"]["candidate_decisions"]
        if decision["kind"] == "memory"
    ]
    section_decision = next(
        decision
        for decision in context["context_selection"]["section_decisions"]
        if decision["section"] == "related_memories"
    )
    assert len(context["related_memories"]) == 1
    assert any(decision["reason"] == "duplicate" for decision in memory_decisions)
    assert section_decision["action"] == "partially_kept"
    assert section_decision["reason"] == "source_governance"


def test_memory_context_builder_compacts_omitted_dialog_with_artifact_binding(tmp_path) -> None:
    short_memory = ShortMemory(repo_path=tmp_path)
    for index in range(8):
        short_memory.add_message("assistant", f"dialog-{index}-" + (str(index) * 120))
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "memory"),
        max_prompt_chars=620,
    )
    persisted: list[dict] = []

    def persist(record: dict) -> DurableArtifactReference:
        persisted.append(record)
        return DurableArtifactReference(
            artifact_id="compaction-artifact",
            kind="context_compaction",
            integrity_checksum="sha256:" + "a" * 64,
            bytes=100,
        )

    builder.set_checkpoint_handlers(compaction_sink=persist)
    context = builder.build(
        "compact dialog",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    assert len(persisted) == 1
    assert len(context["context_compactions"]) == 1
    assert context["context_compactions"][0]["source_binding_hash"].startswith("sha256:")
    assert "## Earlier Dialog Summary" in context["prompt_text"]
    selected_indexes = [
        int(item["content"].split("-", 2)[1]) for item in context["dialog_context"]
    ]
    assert selected_indexes == list(range(selected_indexes[0], 8))
    compacted_decisions = [
        decision
        for decision in context["context_selection"]["candidate_decisions"]
        if decision["reason"] == "compacted"
    ]
    assert compacted_decisions
    assert all(
        decision["governed_by_candidate_id"].startswith("compaction:")
        for decision in compacted_decisions
    )


def test_memory_context_compaction_only_governs_older_assistant_dialog(tmp_path) -> None:
    short_memory = ShortMemory(repo_path=tmp_path)
    messages = [("user", "dialog-0-" + ("0" * 120))]
    messages.extend(
        ("assistant", f"dialog-{index}-" + (str(index) * 120))
        for index in range(1, 8)
    )
    for role, content in messages:
        short_memory.add_message(role, content)
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "memory"),
        max_prompt_chars=620,
    )
    persisted: list[dict] = []

    def persist(record: dict) -> DurableArtifactReference:
        persisted.append(record)
        return DurableArtifactReference(
            artifact_id="assistant-only-compaction",
            kind="context_compaction",
            integrity_checksum="sha256:" + "b" * 64,
            bytes=100,
        )

    dialog = builder._dialog_context(8)
    user_candidate_ids = {
        "dialog:"
        + builder._stable_id(
            str(item["role"]),
            str(item["timestamp"]),
            str(item["content"]),
        )
        for item in dialog
        if item["role"] == "user"
    }
    assistant_candidate_ids = {
        "dialog:"
        + builder._stable_id(
            str(item["role"]),
            str(item["timestamp"]),
            str(item["content"]),
        )
        for item in dialog
        if item["role"] == "assistant"
    }
    builder.set_checkpoint_handlers(compaction_sink=persist)

    context = builder.build(
        "assistant-only compaction",
        include_environment=False,
        limit=8,
        system_prompt="Preserve user constraints.",
    )

    assert persisted
    compacted_ids = set(persisted[0]["source_candidate_ids"])
    assert compacted_ids
    assert compacted_ids <= assistant_candidate_ids
    assert compacted_ids.isdisjoint(user_candidate_ids)
    decision_by_id = {
        decision["candidate_id"]: decision
        for decision in context["context_selection"]["candidate_decisions"]
    }
    assert all(
        decision_by_id[candidate_id]["reason"] != "compacted"
        for candidate_id in user_candidate_ids
    )


def test_memory_context_short_assistant_prefix_falls_back_to_uncompacted_selection(tmp_path) -> None:
    short_memory = ShortMemory(repo_path=tmp_path)
    for content in ("x", "y", "R" * 140, "S" * 140):
        short_memory.add_message("assistant", content)

    baseline_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "baseline-memory"),
        max_prompt_chars=256,
    )
    baseline = baseline_builder.build(
        "short prefix",
        include_environment=False,
        limit=4,
        system_prompt="fixed",
    )

    compact_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "compact-memory"),
        max_prompt_chars=256,
    )
    persisted: list[dict] = []

    def persist(record: dict) -> DurableArtifactReference:
        persisted.append(record)
        return DurableArtifactReference(
            artifact_id="unexpected-short-prefix-compaction",
            kind="context_compaction",
            integrity_checksum="sha256:" + "c" * 64,
            bytes=100,
        )

    compact_builder.set_checkpoint_handlers(compaction_sink=persist)
    compacted = compact_builder.build(
        "short prefix",
        include_environment=False,
        limit=4,
        system_prompt="fixed",
    )

    assert persisted == []
    assert compacted["context_compactions"] == []
    assert compacted["prompt_text"] == baseline["prompt_text"]
    compact_selection = dict(compacted["context_selection"])
    baseline_selection = dict(baseline["context_selection"])
    compact_selection.pop("created_at")
    baseline_selection.pop("created_at")
    assert compact_selection == baseline_selection


def test_memory_context_compaction_fingerprint_changes_with_source_dialog(tmp_path) -> None:
    short_memory = ShortMemory(repo_path=tmp_path)
    for index in range(8):
        short_memory.add_message("assistant", f"source-{index}-" + (str(index) * 120))
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "memory"),
        max_prompt_chars=620,
    )
    records: list[dict] = []

    def persist(record: dict) -> DurableArtifactReference:
        records.append(record)
        return DurableArtifactReference(
            artifact_id=f"artifact-{len(records)}",
            kind="context_compaction",
            integrity_checksum="sha256:" + str(len(records)) * 64,
            bytes=100,
        )

    builder.set_checkpoint_handlers(compaction_sink=persist)
    builder.build(
        "compact source",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )
    short_memory.context_manager.messages[0].content = "changed-source-" + ("z" * 120)
    builder.build(
        "compact source",
        include_environment=False,
        limit=8,
        system_prompt="Preserve recent dialog.",
    )

    assert len(records) == 2
    assert records[0]["source_fingerprint"] != records[1]["source_fingerprint"]


def test_memory_context_segmented_compaction_preserves_signals_and_masks_long_observations() -> None:
    noisy_output = "\n".join(f"NOISE-LINE-{index}" for index in range(300))
    candidates = [
        ContextCandidate(
            candidate_id="dialog:user-requirement",
            kind=ContextCandidateKind.DIALOG,
            role="assistant",
            content=(
                "USER: Must preserve the exact validation command python -m pytest -q.\n"
                + noisy_output
            ),
            source_order=1,
        ),
        ContextCandidate(
            candidate_id="dialog:tool-failure",
            kind=ContextCandidateKind.DIALOG,
            role="assistant",
            content=(
                "ASSISTANT: Tool observation follows.\n"
                + noisy_output
                + "\nERROR: test_divide failed with ZeroDivisionError"
            ),
            source_order=2,
        ),
    ]

    record = MemoryContextBuilder._dialog_compaction_record(candidates)

    assert record.algorithm == "deterministic_observation_mask_v1"
    assert "python -m pytest -q" in record.summary
    assert "ZeroDivisionError" in record.summary
    assert "NOISE-LINE-137" not in record.summary
    assert "[observation masked:" in record.summary
    assert "sha256:" in record.summary
    assert record.compacted_chars < record.original_chars


def test_memory_context_segmented_compaction_preserves_structured_markers() -> None:
    candidates = [
        ContextCandidate(
            candidate_id=f"dialog:marker-{index}",
            kind=ContextCandidateKind.DIALOG,
            role="assistant",
            content=(
                f"ASSISTANT: Decision {index}: {marker}. "
                + (f"low-value-marker-{index} " * 160)
            ),
            source_order=index,
        )
        for index, marker in enumerate(
            [
                "scoped_target=calculator.py",
                "forbidden_target=README.md",
                "api_rule=preserve_api",
                "validation_command=pytest_q",
            ]
        )
    ]

    record = MemoryContextBuilder._dialog_compaction_record(candidates)

    assert "scoped_target=calculator.py" in record.summary
    assert "forbidden_target=README.md" in record.summary
    assert "api_rule=preserve_api" in record.summary
    assert "validation_command=pytest_q" in record.summary
    assert "low-value-marker" not in record.summary
    assert record.compacted_chars < record.original_chars


def _rolling_request_for_candidates(
    candidates: tuple[ContextCandidate, ...],
    limit: int,
    *,
    payload: dict[str, object] | None = None,
) -> RollingSummaryRequest:
    source_payload = [
        {"candidate_id": candidate.candidate_id, "content": candidate.content}
        for candidate in candidates
    ]
    fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(
            source_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return RollingSummaryRequest(
        source_candidate_ids=tuple(candidate.candidate_id for candidate in candidates),
        source_fingerprint=fingerprint,
        provider_payload=payload
        or {
            "goal_delta": "older observations summarized",
            "verified_facts": ["source segment retained"],
            "decisions": ["keep the recent suffix"],
            "open_issues": [],
            "evidence_ids": [candidates[0].candidate_id],
            "next_action": "continue with the current task",
        },
        attempt=RollingSummaryAttemptEvidence(
            usage={"prompt_tokens": 40, "completion_tokens": 12, "total_tokens": 52},
            usage_observed=True,
            finish_reason="stop",
        ),
        max_summary_tokens=limit,
        original_chars=sum(len(candidate.content) for candidate in candidates),
        current_source_fingerprint=fingerprint,
    )


def _rolling_builder(tmp_path, *, factory, enabled: bool = True) -> MemoryContextBuilder:
    short_memory = ShortMemory(repo_path=tmp_path)
    for index in range(8):
        short_memory.add_message("assistant", f"dialog-{index}-" + (str(index) * 120))
    return MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_path / "memory"),
        max_prompt_chars=620,
        rolling_summary_enabled=enabled,
        rolling_summary_adapter=RollingSummaryAdapter(count_tokens=lambda text: len(text.split())),
        rolling_summary_request_factory=factory,
    )


def _persist_records(records: list[dict]):
    def persist(record: dict) -> DurableArtifactReference:
        records.append(record)
        return DurableArtifactReference(
            artifact_id=f"rolling-{len(records)}",
            kind="context_compaction",
            integrity_checksum="sha256:" + str(len(records)) * 64,
            bytes=100,
        )

    return persist


def test_rolling_summary_is_default_off_even_when_injected(tmp_path) -> None:
    calls: list[tuple[tuple[ContextCandidate, ...], int]] = []

    def factory(candidates, limit):
        calls.append((candidates, limit))
        raise AssertionError("feature-off builder must not call the summary factory")

    builder = _rolling_builder(tmp_path, factory=factory, enabled=False)
    persisted: list[dict] = []
    builder.set_checkpoint_handlers(compaction_sink=_persist_records(persisted))
    builder.build("feature off", include_environment=False, limit=8)

    assert calls == []
    assert persisted and persisted[0]["algorithm"] == "deterministic_observation_mask_v1"


def test_rolling_summary_acceptance_reuses_atomic_artifact_sink(tmp_path) -> None:
    def factory(candidates, limit):
        return _rolling_request_for_candidates(candidates, limit)

    builder = _rolling_builder(tmp_path, factory=factory)
    persisted: list[dict] = []
    builder.set_checkpoint_handlers(compaction_sink=_persist_records(persisted))
    context = builder.build("rolling summary", include_environment=False, limit=8)

    assert persisted and persisted[0]["algorithm"] == "llm_rolling_summary_v1"
    assert context["context_compactions"][0]["record"]["algorithm"] == "llm_rolling_summary_v1"
    assert "## Earlier Dialog Summary" in context["prompt_text"]


def test_rolling_summary_fallback_restores_deterministic_record(tmp_path) -> None:
    def factory(candidates, limit):
        return _rolling_request_for_candidates(
            candidates,
            limit,
            payload={"unknown": "field"},
        )

    builder = _rolling_builder(tmp_path, factory=factory)
    persisted: list[dict] = []
    builder.set_checkpoint_handlers(compaction_sink=_persist_records(persisted))
    builder.build("rolling fallback", include_environment=False, limit=8)

    assert persisted and persisted[0]["algorithm"] == "deterministic_observation_mask_v1"
