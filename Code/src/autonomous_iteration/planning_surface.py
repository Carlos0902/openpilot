"""Deterministic planning-surface cards for tool planning prompts.

This module intentionally sits above ToolRegistry and below the LLM-facing
prompt builder. It lets the planner see a compact, stable capability surface
without exposing every tool contract on every turn.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Protocol


NEED_CATALOG: tuple[dict[str, str], ...] = (
    {
        "need_type": "file_read",
        "description": "Inspect one concrete file before editing.",
        "required": "target_path",
    },
    {
        "need_type": "project_structure",
        "description": "Inspect a directory or small multi-file slice.",
        "required": "directory_path or candidate_paths",
    },
    {
        "need_type": "command_check",
        "description": "Run a concrete validation or smoke-check command.",
        "required": "command",
    },
    {
        "need_type": "web_search",
        "description": "Use public web research only when local evidence is insufficient.",
        "required": "query",
    },
    {
        "need_type": "code_file_create",
        "description": "Create a new executable source file or scaffold.",
        "required": "target_path or attributes.task_description",
    },
    {
        "need_type": "code_unit_generate",
        "description": "Add a new symbol to an existing file after reading it.",
        "required": "target_path, operation_kind=add_symbol",
    },
    {
        "need_type": "code_symbol_modify",
        "description": "Modify an existing symbol or local code scope after reading it.",
        "required": "target_path, operation_kind=modify_symbol",
    },
    {
        "need_type": "file_write",
        "description": "Write generated content or apply a patch after evidence.",
        "required": "target_path",
    },
    {
        "need_type": "file_delete",
        "description": "Delete a concrete file only after evidence.",
        "required": "target_path",
    },
    {
        "need_type": "readme_generation",
        "description": "Generate README or delivery instructions for a project.",
        "required": "target_path or attributes.project_path",
    },
)

CORE_CARD_IDS = (
    "file_evidence",
    "project_structure_evidence",
    "command_validation",
    "web_research",
)


class CapabilitySourceKind(str, Enum):
    """Where a planning-surface card comes from.

    Tool-backed cards are executable today. Skill-backed cards are intentionally
    allowed in the model before a concrete SkillRegistry exists, so the prompt
    surface does not have to be redesigned later.
    """

    TOOL = "tool"
    SKILL_FUTURE = "skill_future"


class CapabilityExposure(str, Enum):
    """Disclosure tier for a planning-surface capability."""

    CORE = "core"
    DEFERRED = "deferred"
    HIDDEN = "hidden"

CREATE_TERMS = (
    "add app",
    "build",
    "create",
    "develop",
    "generate",
    "implement",
    "make ",
    "scaffold",
    "创建",
    "开发",
    "实现",
    "生成",
    "编写",
)

MODIFICATION_TERMS = (
    "adjust",
    "change",
    "edit",
    "existing",
    "fix ",
    "modify",
    "patch",
    "refactor",
    "repair",
    "replace",
    "update",
    "修复",
    "修改",
    "更新",
    "替换",
    "重构",
)

DOCUMENTATION_TERMS = (
    "docs",
    "documentation",
    "readme",
    "usage guide",
    "文档",
    "说明",
)

DELETE_TERMS = (
    "delete",
    "drop",
    "remove",
    "清理",
    "删除",
    "移除",
)

RUNTIME_REPAIR_TERMS = (
    "dependency",
    "environment",
    "import error",
    "importerror",
    "modulenotfounderror",
    "module not found",
    "package install",
    "pip",
    "runtime failure",
    "runtime error",
    "startup failed",
    "venv",
    "virtual environment",
    "启动失败",
    "依赖损坏",
    "环境损坏",
    "导入失败",
    "模块不存在",
)


@dataclass(frozen=True)
class PlanningSurfaceCard:
    """A compact, planner-facing capability card."""

    card_id: str
    title: str
    source_kind: str | CapabilitySourceKind
    exposure: str | CapabilityExposure
    need_types: tuple[str, ...]
    summary: str
    required_fields_hint: str
    example_need: dict[str, Any]
    trigger_terms: tuple[str, ...]
    backing_refs: tuple[str, ...]

    def render(self) -> str:
        example = json.dumps(self.example_need, ensure_ascii=False, separators=(",", ":"))
        return f"- {self.title}: {self.summary}\n  Example: {example}"


class CapabilityCardProvider(Protocol):
    """Source of planner-facing capability cards.

    Implementations can be backed by ToolRegistry, a future SkillRegistry,
    MCP resources, static built-ins, or tests. The selector and renderer only
    operate on cards and never assume execution semantics.
    """

    def planning_cards(self) -> list[PlanningSurfaceCard]:
        """Return cards that this provider wants to expose to the catalog."""


@dataclass(frozen=True)
class PlanningSurfaceSelection:
    """Selected cards for one tool-planning prompt."""

    core_cards: tuple[PlanningSurfaceCard, ...]
    deferred_cards: tuple[PlanningSurfaceCard, ...]

    def render(self) -> str:
        lines = ["Need Catalog:"]
        for entry in NEED_CATALOG:
            lines.append(
                f"- {entry['need_type']}: {entry['description']} Required: {entry['required']}."
            )

        lines.append("")
        lines.append("Core Capability Cards:")
        lines.extend(card.render() for card in self.core_cards)

        lines.append("")
        lines.append("Deferred Capability Cards:")
        if self.deferred_cards:
            lines.extend(card.render() for card in self.deferred_cards)
        else:
            lines.append(
                "- None selected for this task. Stay with core evidence and validation needs unless the task explicitly requires mutation."
            )
        return "\n".join(lines)


class StaticCapabilityCardProvider:
    """Simple provider for static or future capability cards."""

    def __init__(self, cards: Iterable[PlanningSurfaceCard]) -> None:
        self._cards = tuple(cards)

    def planning_cards(self) -> list[PlanningSurfaceCard]:
        return list(self._cards)


class ToolCapabilityCardProvider:
    """Provider that exposes tool-backed planning cards when tools exist."""

    def __init__(self, tools: Iterable[Any]) -> None:
        self._tool_names = {str(getattr(tool, "name", "") or "") for tool in tools}

    def planning_cards(self) -> list[PlanningSurfaceCard]:
        return [
            card
            for card in _default_tool_card_specs()
            if any(ref in self._tool_names for ref in card.backing_refs)
        ]


class PlanningSurfaceCatalog:
    """Registry-independent card catalog assembled from capability providers."""

    def __init__(self, cards: Iterable[PlanningSurfaceCard]) -> None:
        ordered = tuple(cards)
        self._cards = ordered
        self._by_id = {card.card_id: card for card in ordered}

    @classmethod
    def from_providers(cls, providers: Iterable[CapabilityCardProvider]) -> "PlanningSurfaceCatalog":
        cards: list[PlanningSurfaceCard] = []
        seen_ids: set[str] = set()
        for provider in providers:
            for card in provider.planning_cards():
                if card.card_id in seen_ids:
                    continue
                seen_ids.add(card.card_id)
                cards.append(card)
        return cls(cards)

    @classmethod
    def from_tools(cls, tools: list[Any]) -> "PlanningSurfaceCatalog":
        return cls.from_providers([ToolCapabilityCardProvider(tools)])

    def get(self, card_id: str) -> PlanningSurfaceCard | None:
        return self._by_id.get(card_id)

    def core_cards(self) -> tuple[PlanningSurfaceCard, ...]:
        return tuple(
            card
            for card_id in CORE_CARD_IDS
            if (card := self.get(card_id)) is not None and _enum_value(card.exposure) == CapabilityExposure.CORE.value
        )

    def deferred_cards(self) -> tuple[PlanningSurfaceCard, ...]:
        return tuple(card for card in self._cards if _enum_value(card.exposure) == CapabilityExposure.DEFERRED.value)

    def cards(self) -> tuple[PlanningSurfaceCard, ...]:
        return self._cards


class PlanningSurfaceSelector:
    """Deterministically choose which deferred cards to disclose."""

    def select(
        self,
        catalog: PlanningSurfaceCatalog,
        *,
        task_description: str,
        goal: str = "",
        history_text: str = "",
        retry_reason: str = "",
        signal: Any | None = None,
        plan_data: dict[str, Any] | None = None,
    ) -> PlanningSurfaceSelection:
        combined = "\n".join(
            part
            for part in (
                task_description,
                goal,
                history_text,
                retry_reason,
                self._signal_text(signal),
                json.dumps(plan_data, ensure_ascii=False, default=str)[:1000] if plan_data else "",
            )
            if part
        ).lower()

        deferred_ids: list[str] = [
            card.card_id
            for card in catalog.deferred_cards()
            if self._contains_any(combined, card.trigger_terms)
        ]

        if (retry_reason or signal) and not deferred_ids:
            if self._looks_like_mutation_task(combined):
                preferred_need = (
                    "code_symbol_modify"
                    if self._contains_any(combined, MODIFICATION_TERMS)
                    else "code_file_create"
                )
                fallback = self._first_deferred_card_for_need(catalog, preferred_need)
                if fallback:
                    deferred_ids.append(fallback.card_id)
            elif "tool_calls" in combined:
                fallback = self._first_deferred_card_for_need(catalog, "code_file_create")
                if fallback:
                    deferred_ids.append(fallback.card_id)

        ordered_deferred: list[PlanningSurfaceCard] = []
        seen_ids: set[str] = set()
        for card_id in deferred_ids:
            if card_id in seen_ids:
                continue
            card = catalog.get(card_id)
            if card is None:
                continue
            seen_ids.add(card_id)
            ordered_deferred.append(card)
            if len(ordered_deferred) >= 3:
                break

        return PlanningSurfaceSelection(
            core_cards=catalog.core_cards(),
            deferred_cards=tuple(ordered_deferred),
        )

    def _contains_any(self, text: str, terms: tuple[str, ...]) -> bool:
        return any(term in text for term in terms)

    def _looks_like_mutation_task(self, text: str) -> bool:
        return self._contains_any(text, CREATE_TERMS + MODIFICATION_TERMS + DOCUMENTATION_TERMS + DELETE_TERMS)

    def _first_deferred_card_for_need(
        self,
        catalog: PlanningSurfaceCatalog,
        need_type: str,
    ) -> PlanningSurfaceCard | None:
        for card in catalog.deferred_cards():
            if need_type in card.need_types:
                return card
        return None

    def _signal_text(self, signal: Any | None) -> str:
        if signal is None:
            return ""
        if hasattr(signal, "to_json_dict"):
            try:
                payload = signal.to_json_dict()
            except Exception:
                payload = {}
        elif isinstance(signal, dict):
            payload = signal
        else:
            payload = {"signal": str(signal)}
        return json.dumps(payload, ensure_ascii=False, default=str)


def _default_tool_card_specs() -> tuple[PlanningSurfaceCard, ...]:
    return (
        PlanningSurfaceCard(
            card_id="file_evidence",
            title="File evidence",
            source_kind=CapabilitySourceKind.TOOL,
            exposure=CapabilityExposure.CORE,
            need_types=("file_read",),
            summary="Use for factual inspection of one file before mutation.",
            required_fields_hint="target_path",
            example_need={
                "need_type": "file_read",
                "target_path": "/abs/path/cli.py",
            },
            trigger_terms=("read", "inspect"),
            backing_refs=("file_reader",),
        ),
        PlanningSurfaceCard(
            card_id="project_structure_evidence",
            title="Project structure evidence",
            source_kind=CapabilitySourceKind.TOOL,
            exposure=CapabilityExposure.CORE,
            need_types=("project_structure",),
            summary="Use for directory listing or cross-module evidence gathering.",
            required_fields_hint="directory_path or candidate_paths",
            example_need={
                "need_type": "project_structure",
                "target_path": "/abs/path/project",
            },
            trigger_terms=("structure", "cross-module"),
            backing_refs=("multi_file_reader",),
        ),
        PlanningSurfaceCard(
            card_id="command_validation",
            title="Command validation",
            source_kind=CapabilitySourceKind.TOOL,
            exposure=CapabilityExposure.CORE,
            need_types=("command_check",),
            summary="Use for smoke tests or validation commands with explicit commands only.",
            required_fields_hint="command; keep mode automatic/dry_run/interactive only",
            example_need={
                "need_type": "command_check",
                "command": "python -m compileall /abs/path/project",
            },
            trigger_terms=("test", "validate", "verify"),
            backing_refs=("command_executor",),
        ),
        PlanningSurfaceCard(
            card_id="web_research",
            title="External research",
            source_kind=CapabilitySourceKind.TOOL,
            exposure=CapabilityExposure.CORE,
            need_types=("web_search",),
            summary="Use only when local files and history do not provide enough evidence.",
            required_fields_hint="query",
            example_need={
                "need_type": "web_search",
                "query": "pytest fixture scope behavior",
            },
            trigger_terms=("search", "reference"),
            backing_refs=("web_searcher",),
        ),
        PlanningSurfaceCard(
            card_id="code_generation_create",
            title="New file generation",
            source_kind=CapabilitySourceKind.TOOL,
            exposure=CapabilityExposure.DEFERRED,
            need_types=("code_file_create", "directory_generate", "file_write"),
            summary="Use for new executable files or scaffolded project files. Generate first, then write with create_file semantics.",
            required_fields_hint="target_path, operation_kind=create_file when writing, language=python/shell/bash only",
            example_need={
                "need_type": "code_file_create",
                "target_path": "/abs/path/app.py",
                "operation_kind": "create_file",
                "attributes": {"language": "python"},
            },
            trigger_terms=CREATE_TERMS,
            backing_refs=("code_generator", "file_writer"),
        ),
        PlanningSurfaceCard(
            card_id="code_modification_patch",
            title="Existing code modification",
            source_kind=CapabilitySourceKind.TOOL,
            exposure=CapabilityExposure.DEFERRED,
            need_types=("code_unit_generate", "code_symbol_modify", "file_write"),
            summary="Use for existing files only: read evidence first, then add_symbol or modify_symbol, then write through patch semantics.",
            required_fields_hint="target_path, operation_kind=add_symbol or modify_symbol, symbol_name when known",
            example_need={
                "need_type": "code_symbol_modify",
                "target_path": "/abs/path/cli.py",
                "operation_kind": "modify_symbol",
                "symbol_name": "main",
                "symbol_type": "function",
            },
            trigger_terms=MODIFICATION_TERMS,
            backing_refs=("code_unit_generator", "code_editor", "file_patch_writer"),
        ),
        PlanningSurfaceCard(
            card_id="documentation_readme",
            title="Documentation delivery",
            source_kind=CapabilitySourceKind.TOOL,
            exposure=CapabilityExposure.DEFERRED,
            need_types=("readme_generation",),
            summary="Use for README or delivery instructions after implementation or when the task is docs-only.",
            required_fields_hint="target_path or attributes.project_path",
            example_need={
                "need_type": "readme_generation",
                "target_path": "/abs/path/project",
            },
            trigger_terms=DOCUMENTATION_TERMS,
            backing_refs=("readme_tool",),
        ),
        PlanningSurfaceCard(
            card_id="delete_guarded",
            title="Guarded deletion",
            source_kind=CapabilitySourceKind.TOOL,
            exposure=CapabilityExposure.DEFERRED,
            need_types=("file_delete",),
            summary="Use only after evidence shows the file should be removed. Collect file_read or project_structure evidence first.",
            required_fields_hint="target_path, operation_kind=delete_file",
            example_need={
                "need_type": "file_delete",
                "target_path": "/abs/path/obsolete.py",
                "operation_kind": "delete_file",
            },
            trigger_terms=DELETE_TERMS,
            backing_refs=("file_delete_tool",),
        ),
        PlanningSurfaceCard(
            card_id="runtime_repair",
            title="Runtime or environment repair",
            source_kind=CapabilitySourceKind.TOOL,
            exposure=CapabilityExposure.DEFERRED,
            need_types=("bug_fix", "repair"),
            summary="Use only for explicit runtime, import, dependency, or environment breakage after gathering failure evidence.",
            required_fields_hint="command and candidate file paths when using bug_fix-style repair",
            example_need={
                "need_type": "bug_fix",
                "command": "python app.py --smoke",
                "attributes": {"file_paths": ["/abs/path/app.py"]},
            },
            trigger_terms=RUNTIME_REPAIR_TERMS,
            backing_refs=("bug_fix_tool", "environment_fix_tool"),
        ),
    )


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))
