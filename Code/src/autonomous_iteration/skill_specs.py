"""Minimal future-skill specs for planning-surface integration.

This module does not implement skill execution. It only parses lightweight
skill descriptions and converts them into planner-facing capability cards so
future skills can participate in progressive disclosure without redesigning the
tool-planning prompt layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from autonomous_iteration.planning_surface import (
    CapabilityExposure,
    CapabilitySourceKind,
    PlanningSurfaceCard,
)


DEFAULT_SKILL_NEED_TYPES: tuple[str, ...] = (
    "project_structure",
    "file_read",
    "command_check",
)


@dataclass(frozen=True)
class SkillSpec:
    """Parsed planning-only skill metadata."""

    skill_id: str
    name: str
    description: str
    source_path: str
    exposure: str = CapabilityExposure.DEFERRED.value
    trigger_terms: tuple[str, ...] = ()
    need_types: tuple[str, ...] = DEFAULT_SKILL_NEED_TYPES
    allowed_tools: tuple[str, ...] = ()
    when_to_use: tuple[str, ...] = ()
    when_not_to_use: tuple[str, ...] = ()
    procedure_summary: tuple[str, ...] = ()
    backing_refs: tuple[str, ...] = ()

    def summary_text(self) -> str:
        parts: list[str] = [self.description.strip()]
        if self.when_to_use:
            parts.append(f"Use when {self.when_to_use[0].strip()}.")
        if self.procedure_summary:
            snippet = "; ".join(item.strip() for item in self.procedure_summary[:2] if item.strip())
            if snippet:
                parts.append(f"Procedure: {snippet}.")
        if self.allowed_tools:
            parts.append(f"Allowed tools: {', '.join(self.allowed_tools[:4])}.")
        return " ".join(part for part in parts if part).strip()


class SkillCapabilityCardProvider:
    """Convert planning-only skill specs into deferred capability cards."""

    def __init__(self, skills: Iterable[SkillSpec]) -> None:
        self._skills = tuple(skills)

    def planning_cards(self) -> list[PlanningSurfaceCard]:
        cards: list[PlanningSurfaceCard] = []
        for skill in self._skills:
            example_need = {
                "need_type": skill.need_types[0] if skill.need_types else "project_structure",
                "target_path": "/abs/path/project",
            }
            cards.append(
                PlanningSurfaceCard(
                    card_id=f"skill_{skill.skill_id}",
                    title=skill.name,
                    source_kind=CapabilitySourceKind.SKILL_FUTURE,
                    exposure=skill.exposure,
                    need_types=skill.need_types or DEFAULT_SKILL_NEED_TYPES,
                    summary=skill.summary_text(),
                    required_fields_hint="task goal and evidence scope",
                    example_need=example_need,
                    trigger_terms=skill.trigger_terms,
                    backing_refs=skill.backing_refs or (f"skill:{skill.skill_id}",),
                )
            )
        return cards


def default_skill_search_roots(code_root: Path) -> list[Path]:
    """Return conventional locations for planning-only skills."""

    repo_root = code_root.parent
    return [
        code_root / "skills",
        repo_root / "skills",
        code_root / ".openpilot" / "skills",
        repo_root / ".openpilot" / "skills",
    ]


def load_skill_specs(roots: Iterable[str | Path]) -> list[SkillSpec]:
    """Load all SKILL.md files found under the provided roots.

    Invalid or incomplete skill files are ignored to keep the planning layer
    resilient while the skill format is still evolving.
    """

    specs: list[SkillSpec] = []
    for skill_file in discover_skill_files(roots):
        spec = parse_skill_file(skill_file)
        if spec is not None:
            specs.append(spec)
    return specs


def discover_skill_files(roots: Iterable[str | Path]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for raw_root in roots:
        root = Path(raw_root).expanduser()
        if not root.exists() or not root.is_dir():
            continue
        for path in sorted(root.rglob("SKILL.md")):
            if path in seen:
                continue
            seen.add(path)
            files.append(path)
    return files


def parse_skill_file(path: str | Path) -> SkillSpec | None:
    skill_path = Path(path).expanduser()
    try:
        text = skill_path.read_text(encoding="utf-8")
    except OSError:
        return None

    frontmatter, body = _split_frontmatter(text)
    metadata = _parse_frontmatter(frontmatter)
    name = str(metadata.get("name") or "").strip()
    description = str(metadata.get("description") or "").strip()
    if not name or not description:
        return None

    skill_id = str(metadata.get("id") or _slugify(skill_path.parent.name or name)).strip()
    exposure = str(metadata.get("exposure") or CapabilityExposure.DEFERRED.value).strip() or CapabilityExposure.DEFERRED.value
    need_types = _tuple_of_strings(metadata.get("need_types")) or DEFAULT_SKILL_NEED_TYPES
    allowed_tools = _tuple_of_strings(metadata.get("allowed_tools"))
    trigger_terms = _tuple_of_strings(metadata.get("trigger_terms")) or _default_trigger_terms(skill_id, name)
    backing_refs = _tuple_of_strings(metadata.get("backing_refs")) or (f"skill:{skill_id}",)

    sections = _markdown_sections(body)
    when_to_use = tuple(_section_items(sections, "when to use"))
    when_not_to_use = tuple(_section_items(sections, "when not to use"))
    procedure_summary = tuple(_section_items(sections, "procedure"))

    return SkillSpec(
        skill_id=skill_id,
        name=name,
        description=description,
        source_path=str(skill_path),
        exposure=exposure,
        trigger_terms=trigger_terms,
        need_types=need_types,
        allowed_tools=allowed_tools,
        when_to_use=when_to_use,
        when_not_to_use=when_not_to_use,
        procedure_summary=procedure_summary,
        backing_refs=backing_refs,
    )


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        return "", text
    return text[4:end], text[end + 5 :]


def _parse_frontmatter(frontmatter: str) -> dict[str, Any]:
    if not frontmatter.strip():
        return {}

    lines = frontmatter.splitlines()
    data: dict[str, Any] = {}
    index = 0
    while index < len(lines):
        raw = lines[index]
        if not raw.strip() or raw.lstrip().startswith("#"):
            index += 1
            continue
        if ":" not in raw:
            index += 1
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            data[key] = _parse_scalar_or_inline_list(value)
            index += 1
            continue

        items: list[str] = []
        index += 1
        while index < len(lines):
            nested = lines[index]
            stripped = nested.strip()
            if not stripped:
                index += 1
                continue
            if nested.startswith((" ", "\t")) and stripped.startswith("- "):
                items.append(_strip_quotes(stripped[2:].strip()))
                index += 1
                continue
            break
        data[key] = items
    return data


def _parse_scalar_or_inline_list(value: str) -> Any:
    stripped = value.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(part.strip()) for part in inner.split(",") if part.strip()]
    return _strip_quotes(stripped)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _markdown_sections(body: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in body.splitlines():
        heading = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", raw_line)
        if heading:
            current = heading.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(raw_line.rstrip())
    return sections


def _section_items(sections: dict[str, list[str]], heading_name: str) -> list[str]:
    lines = sections.get(heading_name.lower(), [])
    items: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        bullet = re.sub(r"^[-*+]\s+", "", stripped)
        bullet = re.sub(r"^\d+[.)]\s*", "", bullet)
        if bullet:
            items.append(bullet)
    return items


def _tuple_of_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        stripped = value.strip()
        return (stripped,) if stripped else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return cleaned or "skill"


def _default_trigger_terms(skill_id: str, name: str) -> tuple[str, ...]:
    terms = [skill_id.replace("_", " "), name.lower().strip()]
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        term = term.strip()
        if not term or term in seen:
            continue
        seen.add(term)
        result.append(term)
    return tuple(result)
