from __future__ import annotations

from types import SimpleNamespace

import pytest

from autonomous_iteration.agents.execution_task_decomposer import TaskDecomposer


def _decomposer_for(payload: object) -> TaskDecomposer:
    decomposer = TaskDecomposer(SimpleNamespace())
    decomposer._generate_decomposition = lambda _task, _context: payload  # type: ignore[method-assign]
    return decomposer


@pytest.mark.parametrize("raw_kind", ["general", None])
def test_decomposition_accepts_general_and_missing_kind(raw_kind: str | None) -> None:
    result = _decomposer_for(
        {
            "rationale": "one bounded task",
            "subtasks": [{"description": "Answer the user", "kind": raw_kind}],
        }
    ).decompose("Answer the user")

    assert len(result.subtasks) == 1
    assert result.subtasks[0].kind == "general"


@pytest.mark.parametrize(
    "payload",
    [
        {"subtasks": [{"description": "Answer the user", "kind": "unsupported"}]},
        {"subtasks": ["not an object"]},
        {"subtasks": [{"kind": "general"}]},
        {"subtasks": "not a list"},
        [],
    ],
)
def test_malformed_decomposition_is_bounded_before_runtime_conversion(payload: object) -> None:
    with pytest.raises(ValueError) as error_info:
        _decomposer_for(payload).decompose("Answer the user")

    assert len(str(error_info.value)) < 200
