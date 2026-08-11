from __future__ import annotations

import pytest

from autonomous_iteration.pre_task_admission import (
    PreTaskAdmissionKind,
    PreTaskAdmissionReason,
    resolve_pre_task_admission,
)


@pytest.mark.parametrize(
    "goal",
    [
        "帮我开发一个贪吃蛇小游戏",
        "做个网页",
        "搭建一个个人博客",
        "实现登录功能",
        "build a dashboard",
    ],
)
def test_artifact_and_mutation_requests_enter_project_execution(goal: str) -> None:
    decision = resolve_pre_task_admission(goal)

    assert decision.kind is PreTaskAdmissionKind.PROJECT_EXECUTION
    assert decision.reason_code in {
        PreTaskAdmissionReason.ARTIFACT_CREATION,
        PreTaskAdmissionReason.CODE_PROJECT_MUTATION,
    }


@pytest.mark.parametrize(
    "goal",
    [
        "解释贪吃蛇通常怎么实现",
        "不要写代码，只介绍实现思路",
        "Explain how a snake game is usually implemented",
    ],
)
def test_knowledge_requests_remain_lightweight_responses(goal: str) -> None:
    decision = resolve_pre_task_admission(goal)

    assert decision.kind is PreTaskAdmissionKind.LIGHTWEIGHT_RESPONSE
    assert decision.reason_code is PreTaskAdmissionReason.QUESTION_ONLY


def test_current_weather_request_uses_external_response_admission() -> None:
    decision = resolve_pre_task_admission("今天常熟的天气怎么样")

    assert decision.kind is PreTaskAdmissionKind.CURRENT_EXTERNAL_RESPONSE
    assert decision.reason_code is PreTaskAdmissionReason.CURRENT_EXTERNAL_FACT


@pytest.mark.parametrize("goal", ["你好", "谢谢", "讲个笑话"])
def test_social_and_conversational_requests_remain_lightweight(goal: str) -> None:
    decision = resolve_pre_task_admission(goal)

    assert decision.kind is PreTaskAdmissionKind.LIGHTWEIGHT_RESPONSE


def test_ambiguous_intent_fails_safe_to_project_execution() -> None:
    decision = resolve_pre_task_admission("贪吃蛇小游戏")

    assert decision.kind is PreTaskAdmissionKind.PROJECT_EXECUTION
    assert decision.reason_code is PreTaskAdmissionReason.AMBIGUOUS_FAIL_SAFE_PROJECT


@pytest.mark.parametrize("goal", ["", "   ", "解释 Code/game.py 的实现"])
def test_empty_or_explicit_project_path_fails_safe_to_project_execution(goal: str) -> None:
    decision = resolve_pre_task_admission(goal)

    assert decision.kind is PreTaskAdmissionKind.PROJECT_EXECUTION
