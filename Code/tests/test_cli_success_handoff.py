from __future__ import annotations

from autonomous_iteration.models import IterationResult
from ui import enhanced_cli


def test_success_details_explain_each_accepted_iteration() -> None:
    details = enhanced_cli._format_success_details(
        {
            "completed_improvements": 1,
            "required_improvements": 1,
            "iterations": [
                IterationResult(
                    iteration=1,
                    validation_passed=True,
                    completed_successful_iteration=True,
                    applied_actions=["Add a visible score display."],
                    changed_files=["/project/snake_game.py"],
                    success=True,
                )
            ],
        }
    )

    assert "代码优化: 1/1" in details
    assert "第 1 轮: Add a visible score display." in details
    assert "修改文件: snake_game.py" in details
    assert "验证: 通过" in details


def test_success_details_unwrap_checkpointed_session_result() -> None:
    details = enhanced_cli._format_success_details(
        {
            "success": True,
            "session_result": {
                "completed_improvements": 1,
                "required_improvements": 1,
                "iterations": [
                    {
                        "iteration": 1,
                        "validation_passed": True,
                        "applied_actions": ["Add a start screen."],
                        "changed_files": ["/project/snake_game.py"],
                    }
                ],
            },
        }
    )

    assert "代码优化: 1/1" in details
    assert "第 1 轮: Add a start screen." in details
    assert "修改文件: snake_game.py" in details
