from __future__ import annotations

from pathlib import Path

from openpilot_metadata_experiment.audit import audit_metadata_directory


def test_audit_is_reproducible_and_separates_field_names_from_nested_types(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text(
        """
class Leaf:
    evidence: list[str]

class Parent:
    evidence: list[str]
    child: "Leaf"
    kind: str
""",
        encoding="utf-8",
    )

    result = audit_metadata_directory(tmp_path)

    assert result["field_name_counts"]["evidence"] == 2
    assert result["nested_type_uses"]["Leaf"] == [{"parent": "Parent", "field": "child"}]
    assert result["files"] == ["sample.py"]
