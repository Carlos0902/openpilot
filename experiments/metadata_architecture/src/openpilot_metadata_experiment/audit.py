"""AST-only inventory of the production metadata package.

The audit deliberately does not import production modules. Its output is evidence about
shape and naming, not proof that two fields own the same semantic fact.
"""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


_ENVELOPE_FIELDS = {"kind", "schema_version", "source", "correlation", "created_at", "annotations"}


def _annotation_names(annotation: ast.expr) -> set[str]:
    names = {child.id for child in ast.walk(annotation) if isinstance(child, ast.Name)}
    for child in ast.walk(annotation):
        if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
            continue
        try:
            forward_tree = ast.parse(child.value, mode="eval")
        except SyntaxError:
            continue
        names.update(item.id for item in ast.walk(forward_tree) if isinstance(item, ast.Name))
    return names


def audit_metadata_directory(directory: Path) -> dict[str, Any]:
    paths = sorted(directory.glob("*.py"))
    parsed = [(path, ast.parse(path.read_text(encoding="utf-8"))) for path in paths]
    class_names = {
        node.name
        for _, tree in parsed
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    field_counts: Counter[str] = Counter()
    nested_uses: dict[str, list[dict[str, str]]] = defaultdict(list)

    for _, tree in parsed:
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
                    continue
                field_name = statement.target.id
                if field_name not in _ENVELOPE_FIELDS:
                    field_counts[field_name] += 1
                referenced_names = _annotation_names(statement.annotation)
                for referenced in sorted((referenced_names & class_names) - {node.name}):
                    nested_uses[referenced].append({"parent": node.name, "field": field_name})

    return {
        "files": [path.name for path in paths],
        "field_name_counts": dict(sorted(field_counts.items(), key=lambda item: (-item[1], item[0]))),
        "nested_type_uses": dict(sorted(nested_uses.items())),
        "interpretation_warning": (
            "Repeated field names and nested types are discovery signals only; ownership, lifecycle, "
            "producer, consumer, and snapshot semantics must be reviewed before declaring duplication."
        ),
    }
