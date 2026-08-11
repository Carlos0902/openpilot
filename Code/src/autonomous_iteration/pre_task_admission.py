"""Typed admission for the unified autonomous pre-task entry."""

from __future__ import annotations

from enum import Enum
import re

from pydantic import BaseModel, ConfigDict


class PreTaskAdmissionKind(str, Enum):
    """The internal execution surface admitted for an autonomous-route turn."""

    LIGHTWEIGHT_RESPONSE = "lightweight_response"
    CURRENT_EXTERNAL_RESPONSE = "current_external_response"
    PROJECT_EXECUTION = "project_execution"


class PreTaskAdmissionReason(str, Enum):
    """Stable reason codes for the admission decision."""

    SOCIAL_CONVERSATIONAL = "social_conversational"
    QUESTION_ONLY = "question_only"
    CURRENT_EXTERNAL_FACT = "current_external_fact"
    ARTIFACT_CREATION = "artifact_creation"
    CODE_PROJECT_MUTATION = "code_project_mutation"
    EXECUTION_VALIDATION = "execution_validation"
    AMBIGUOUS_FAIL_SAFE_PROJECT = "ambiguous_fail_safe_project"


class PreTaskAdmissionDecision(BaseModel):
    """Strict controller-owned decision; it grants no tool or mutation authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: PreTaskAdmissionKind
    reason_code: PreTaskAdmissionReason


class UnifiedEntryFailureStage(str, Enum):
    BOUNDED_RESPONSE = "Bounded Response"
    EXTERNAL_EVIDENCE = "External Evidence"
    PROJECT_EXECUTION = "Project Execution"

    @property
    def title(self) -> str:
        return f"{self.value[:1]}{self.value[1:].lower()} failed"


class UnifiedEntryError(RuntimeError):
    """Credential-safe stage wrapper for unified-entry failures."""

    def __init__(self, stage: UnifiedEntryFailureStage, cause: Exception) -> None:
        super().__init__(f"{stage.value} stopped before completion")
        self.stage = stage
        self.cause_type = type(cause).__name__


_PROJECT_CONTEXT_RE = re.compile(
    r"\b(?:repository|repo|codebase|project|file|directory|folder|git)\b"
    r"|\bsource\s+code\b",
)
_EXPLICIT_PATH_RE = re.compile(
    r"(?:[/\\]|\.(?:py|toml|md|json|ya?ml|tsx?|jsx?|rs|go|java|sh)\b)"
)
_ENGLISH_CREATION_RE = re.compile(r"\b(?:build|create|develop|implement|make|write)\b")
_ENGLISH_MUTATION_RE = re.compile(r"\b(?:add|modify|update|fix|refactor|remove|delete)\b")
_ENGLISH_EXECUTION_RE = re.compile(
    r"\b(?:run|execute|test|validate|verify|deploy|publish|install|configure|migrate|merge|commit)\b"
)
_ENGLISH_QUESTION_RE = re.compile(
    r"^(?:what|why|how|when|where|who|which|can you explain|could you explain|"
    r"explain|describe|tell me about)\b"
)
_CURRENT_EXTERNAL_RE = re.compile(
    r"(?:今天|明天|昨天|现在|当前|最新|实时|天气|新闻|股价|价格|比分|赛程|汇率|"
    r"\btoday\b|\btomorrow\b|\byesterday\b|\bnow\b|\bcurrent\b|\blatest\b|"
    r"\breal[- ]?time\b|\bweather\b|\bnews\b|\bprice\b|\bscore\b|\bschedule\b)"
)


def resolve_pre_task_admission(goal: str) -> PreTaskAdmissionDecision:
    """Admit only positively recognized response requests; otherwise fail closed."""

    text = " ".join(str(goal).strip().casefold().split())

    if _EXPLICIT_PATH_RE.search(text):
        return PreTaskAdmissionDecision(
            kind=PreTaskAdmissionKind.PROJECT_EXECUTION,
            reason_code=PreTaskAdmissionReason.CODE_PROJECT_MUTATION,
        )

    if _is_explicit_knowledge_request(text):
        return PreTaskAdmissionDecision(
            kind=PreTaskAdmissionKind.LIGHTWEIGHT_RESPONSE,
            reason_code=PreTaskAdmissionReason.QUESTION_ONLY,
        )

    if _has_project_context(text):
        return PreTaskAdmissionDecision(
            kind=PreTaskAdmissionKind.PROJECT_EXECUTION,
            reason_code=PreTaskAdmissionReason.CODE_PROJECT_MUTATION,
        )

    if _has_creation_intent(text):
        return PreTaskAdmissionDecision(
            kind=PreTaskAdmissionKind.PROJECT_EXECUTION,
            reason_code=PreTaskAdmissionReason.ARTIFACT_CREATION,
        )

    if _has_mutation_intent(text):
        return PreTaskAdmissionDecision(
            kind=PreTaskAdmissionKind.PROJECT_EXECUTION,
            reason_code=PreTaskAdmissionReason.CODE_PROJECT_MUTATION,
        )

    if _has_execution_intent(text):
        return PreTaskAdmissionDecision(
            kind=PreTaskAdmissionKind.PROJECT_EXECUTION,
            reason_code=PreTaskAdmissionReason.EXECUTION_VALIDATION,
        )

    if _CURRENT_EXTERNAL_RE.search(text):
        return PreTaskAdmissionDecision(
            kind=PreTaskAdmissionKind.CURRENT_EXTERNAL_RESPONSE,
            reason_code=PreTaskAdmissionReason.CURRENT_EXTERNAL_FACT,
        )

    if _is_social_or_conversational(text):
        return PreTaskAdmissionDecision(
            kind=PreTaskAdmissionKind.LIGHTWEIGHT_RESPONSE,
            reason_code=PreTaskAdmissionReason.SOCIAL_CONVERSATIONAL,
        )

    if _is_general_question(text):
        return PreTaskAdmissionDecision(
            kind=PreTaskAdmissionKind.LIGHTWEIGHT_RESPONSE,
            reason_code=PreTaskAdmissionReason.QUESTION_ONLY,
        )

    return PreTaskAdmissionDecision(
        kind=PreTaskAdmissionKind.PROJECT_EXECUTION,
        reason_code=PreTaskAdmissionReason.AMBIGUOUS_FAIL_SAFE_PROJECT,
    )


def _is_explicit_knowledge_request(text: str) -> bool:
    chinese_markers = (
        "不要写代码",
        "无需写代码",
        "只介绍",
        "只解释",
        "只说明",
        "实现思路",
        "解释",
        "介绍",
        "原理",
        "通常怎么",
        "怎么实现",
        "如何实现",
        "怎么开发",
        "如何开发",
    )
    english_markers = (
        "without writing code",
        "no code",
        "implementation approach",
        "usually implemented",
    )
    return (
        any(marker in text for marker in chinese_markers)
        or any(marker in text for marker in english_markers)
        or bool(_ENGLISH_QUESTION_RE.match(text))
    )


def _has_project_context(text: str) -> bool:
    chinese_markers = ("仓库", "代码库", "项目", "文件", "目录", "源码")
    return bool(_PROJECT_CONTEXT_RE.search(text)) or any(marker in text for marker in chinese_markers)


def _has_creation_intent(text: str) -> bool:
    markers = ("开发", "创建", "新建", "构建", "搭建", "实现", "制作", "做个", "做一个", "编写", "写个", "写一个")
    return any(marker in text for marker in markers) or bool(_ENGLISH_CREATION_RE.search(text))


def _has_mutation_intent(text: str) -> bool:
    markers = ("修改", "修复", "重构", "添加", "更新", "删除", "移除")
    return any(marker in text for marker in markers) or bool(_ENGLISH_MUTATION_RE.search(text))


def _has_execution_intent(text: str) -> bool:
    markers = ("运行", "执行", "测试", "验证", "部署", "发布", "安装", "配置", "迁移", "合并", "提交")
    return any(marker in text for marker in markers) or bool(_ENGLISH_EXECUTION_RE.search(text))


def _is_social_or_conversational(text: str) -> bool:
    exact = {
        "你好",
        "您好",
        "嗨",
        "hello",
        "hi",
        "hey",
        "谢谢",
        "感谢",
        "再见",
        "bye",
    }
    conversational_markers = ("讲个笑话", "说个笑话", "写一首诗", "讲个故事")
    return text in exact or any(marker in text for marker in conversational_markers)


def _is_general_question(text: str) -> bool:
    chinese_markers = ("是什么", "为什么", "怎么样", "怎么办", "多少", "哪里", "哪个", "哪些", "谁", "吗", "呢")
    return (
        text.endswith(("?", "？"))
        or bool(_ENGLISH_QUESTION_RE.match(text))
        or any(marker in text for marker in chinese_markers)
    )
