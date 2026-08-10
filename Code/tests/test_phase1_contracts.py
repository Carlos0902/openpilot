from __future__ import annotations

import json
import sys
import importlib.util
from base64 import urlsafe_b64encode
from io import StringIO
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from openai import OpenAIError
from rich.console import Console

from agent_generator.data_collector import _build_search_query, collect_data
from agent_generator.data_processor import process_data
from agent_generator.data_presenter import present_data
from agent_generator.models import DataArtifact, DataArtifactKind, Slot
from agent_generator.pipeline_combiner import combine_pipelines
from agent_generator.runner import _complete_empty_slots
from agent_generator.slot_generator import generate_slots
from core.instrumented_llm import InstrumentedLLMClient
from core.llm import LLMClient, LLMMessage, LLMRequest
from core.exceptions import (
    ContextAssemblyBudgetError,
    ErrorCategory,
    InvalidLLMResponseError,
    LLMProviderError,
    LLMTimeoutError,
)
from core.openpilot_log import OpenPilotLogger
from core.semantic_analyzer import SemanticAnalyzer
from core.semantic_types import RiskLevel, TaskType
from ui.enhanced_ui import EnhancedUI
from ui.progress_tracker import ProgressTracker
from metadata import (
    CollectedDataMetadata,
    ContextAssemblyStatus,
    ContextSelectionMetadata,
    ResultStatus,
    SearchArtifactMetadata,
    ToolContractMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
)
from tools.code_reviewer import code_reviewer_executor as _code_reviewer_executor
from tools.builtin_tools import register_builtin_tools
from tools.file_reader import file_reader_executor
from tools.llm_summarizer import llm_summarizer_executor as _llm_summarizer_executor
from tools.web_searcher import _build_search_query_variants, _default_http_get, web_searcher_executor as _web_searcher_executor
from tools.tool_executor import ToolExecutor
from core.tool_contracts import (
    PermissionLevel,
    ToolDefinition,
)
from tools.tool_selection import ToolSelection
from tools.tool_registry import ToolRegistry
from utils.json_utils import safe_parse_json


def _registered_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


def code_reviewer_executor(params: dict) -> dict:
    return _code_reviewer_executor(ToolInputMetadata.from_mapping("code_reviewer", params))


def llm_summarizer_executor(params: dict) -> dict:
    return _llm_summarizer_executor(ToolInputMetadata.from_mapping("llm_summarizer", params))


def web_searcher_executor(params: dict) -> dict:
    return _web_searcher_executor(ToolInputMetadata.from_mapping("web_searcher", params))


class FakeCleanupLLM:
    def __init__(
        self,
        payload: dict | str | None = None,
        *,
        fail: bool = False,
        link_selection: str = "NONE",
        keyword_variants: str = "",
        fail_tasks: set[str] | None = None,
    ) -> None:
        self.payload = payload or (
            "## Summary\n"
            "Clean summary\n\n"
            "## Key Points\n"
            "- Useful fact\n\n"
            "## Source Notes\n"
            "- https://example.com/alpha: Primary source\n\n"
            "## Follow-up Queries\n"
            "- openpilot follow up"
        )
        self.fail = fail
        self.link_selection = link_selection
        self.keyword_variants = keyword_variants
        self.fail_tasks = fail_tasks or set()
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        task = request.trace_info.get("task")
        if self.fail or task in self.fail_tasks:
            raise RuntimeError("llm unavailable")
        if task == "keyword_generation":
            return SimpleNamespace(parsed_json=None, content=self.keyword_variants)
        if task == "link_selection":
            return SimpleNamespace(parsed_json=None, content=self.link_selection)
        if isinstance(self.payload, dict):
            content = json.dumps(self.payload)
        else:
            content = self.payload
        return SimpleNamespace(
            parsed_json=None,
            content=content,
        )


class FakeStructuredLogger:
    def __init__(self) -> None:
        self.events = []

    def log_structured_event(self, **kwargs) -> None:
        self.events.append(kwargs)


class FakeSummarizerLLM:
    def __init__(
        self,
        content: str | list[str] = "## 最终结果\n整理后的机器学习报告。",
        *,
        fail: bool = False,
        finish_reason: str | list[str] = "stop",
    ) -> None:
        self.content = content
        self.fail = fail
        self.finish_reason = finish_reason
        self.requests = []
        self.model = "fake-summarizer"

    def complete(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("summarizer unavailable")
        if isinstance(self.content, list):
            index = min(len(self.requests) - 1, len(self.content) - 1)
            content = self.content[index]
        else:
            content = self.content
        if isinstance(self.finish_reason, list):
            finish_index = min(len(self.requests) - 1, len(self.finish_reason) - 1)
            finish_reason = self.finish_reason[finish_index]
        else:
            finish_reason = self.finish_reason
        return SimpleNamespace(
            content=content,
            parsed_json=None,
            usage={"total_tokens": 123},
            model=self.model,
            finish_reason=finish_reason,
        )


class FakeSlotLLM:
    def __init__(self, first_payload: dict, repair_payload: dict | None = None) -> None:
        self.first_payload = first_payload
        self.repair_payload = repair_payload or first_payload
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        task = request.trace_info.get("task")
        payload = self.repair_payload if task == "slot_language_repair" else self.first_payload
        return SimpleNamespace(parsed_json=payload, content=json.dumps(payload))


class FakeLLMSettings:
    provider = "fake-provider"
    base_url = "https://example.com/v1"
    api_key = "test-key"
    model = "fake-model"
    timeout_seconds = 10.0
    temperature = 0.2
    transport_retries = 0
    retry_initial_delay = 0.0
    retry_max_delay = 0.0

    def require_ready(self) -> None:
        return None


def _fake_chat_response(content, *, finish_reason: str = "stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)],
        usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 7}),
        model="fake-model",
        id="fake-id",
        created=123,
    )


def _fake_stream_chunk(content: str = "", *, finish_reason: str | None = None, reasoning_content: str | None = None):
    delta = SimpleNamespace(content=content)
    if reasoning_content is not None:
        delta.reasoning_content = reasoning_content
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
        usage=None,
        model="fake-model",
        id="fake-stream-id",
        created=456,
    )


def test_builtin_tools_register_expected_contracts() -> None:
    registry = _registered_registry()

    names = {tool.name for tool in registry.list_all()}
    removed_directory_tool = "directory" + "_lister"

    assert len(names) == 20
    assert {
        "bug_fix_tool",
        "command_executor",
        "embedder",
        "environment_fix_tool",
        "file_reader",
        "file_delete_tool",
        "file_patch_writer",
        "file_writer",
        "code_editor",
        "code_unit_generator",
        "multi_file_reader",
        "task_classifier",
        "task_file_resolver",
        "warning_check_tool",
        "web_searcher",
    }.issubset(names)
    assert removed_directory_tool not in names
    assert "autonomy_tool" not in names
    assert "project_environment_tool" not in names
    assert "memory_context" not in names
    assert "project_state_reader" not in names
    assert "project_improvement_tool" not in names

    multi_file_reader = registry.get("multi_file_reader")
    assert multi_file_reader.contract_metadata.required_any_of == [["file_paths"], ["directory_path"]]


def test_core_imports_remain_available() -> None:
    from core.openpilot_log import OpenPilotLogger as ImportedLogger
    from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
    from memory.memory_store import MemoryStore
    from tools.tool_executor import ToolExecutor as ImportedToolExecutor

    assert IntelligentAutopilot is not None
    assert ImportedToolExecutor is ToolExecutor
    assert MemoryStore is not None
    assert ImportedLogger is OpenPilotLogger


def test_slot_generator_prompt_requires_user_language_match(monkeypatch) -> None:
    monkeypatch.setattr("agent_generator.slot_generator.raise_for_missing_socksio", lambda: None)
    payload = {
        "user_language": "zh",
        "slots": [
            {
                "name": "investigation_goal",
                "kind": "task",
                "description": "调查目标",
                "value": "基础概念",
                "required": True,
                "revision_notes": [],
            }
        ],
    }
    fake_llm = FakeSlotLLM(payload)

    generate_slots("帮我调查一下机器学习", llm_client=fake_llm)

    prompt = "\n".join(message.content for message in fake_llm.requests[0].messages)
    assert "user_language" in prompt
    assert "same language as the user task" in prompt
    assert "If the task is Chinese" in prompt
    assert "must be Chinese" in prompt


def test_slot_generator_repairs_language_drift_with_llm(monkeypatch) -> None:
    monkeypatch.setattr("agent_generator.slot_generator.raise_for_missing_socksio", lambda: None)
    first_payload = {
        "user_language": "zh",
        "slots": [
            {
                "name": "investigation_goal",
                "kind": "task",
                "description": "El propósito de la investigación",
                "value": "conceptos básicos",
                "required": True,
                "revision_notes": [],
            },
            {
                "name": "depth",
                "kind": "constraint",
                "description": "Nivel de profundidad deseado",
                "value": "principiante",
                "required": False,
                "revision_notes": [],
            },
        ],
    }
    repair_payload = {
        "user_language": "zh",
        "slots": [
            {
                "name": "investigation_goal",
                "kind": "task",
                "description": "调查目标：了解机器学习的基础概念",
                "value": "基础概念",
                "required": True,
                "revision_notes": [],
            },
            {
                "name": "depth",
                "kind": "constraint",
                "description": "期望的内容深度",
                "value": "初学者",
                "required": False,
                "revision_notes": [],
            },
        ],
    }
    fake_llm = FakeSlotLLM(first_payload, repair_payload)

    slots = generate_slots("帮我调查一下机器学习", llm_client=fake_llm)

    assert [request.trace_info["task"] for request in fake_llm.requests] == [
        "slot_generation",
        "slot_language_repair",
    ]
    assert slots[0].description == "调查目标：了解机器学习的基础概念"
    assert slots[0].value == "基础概念"
    assert slots[1].description == "期望的内容深度"
    assert slots[1].value == "初学者"


def test_slot_generator_language_repair_preserves_slot_structure(monkeypatch) -> None:
    monkeypatch.setattr("agent_generator.slot_generator.raise_for_missing_socksio", lambda: None)
    first_payload = {
        "user_language": "zh",
        "slots": [
            {
                "name": "output_format",
                "kind": "format",
                "description": "Formato de salida deseado",
                "value": "resumen",
                "required": False,
                "revision_notes": ["nota previa"],
            }
        ],
    }
    repair_payload = {
        "user_language": "zh",
        "slots": [
            {
                "name": "changed_name",
                "kind": "task",
                "description": "期望的输出格式",
                "value": "摘要",
                "required": True,
                "revision_notes": ["已修复为中文"],
            }
        ],
    }
    fake_llm = FakeSlotLLM(first_payload, repair_payload)

    slots = generate_slots("帮我调查一下机器学习", llm_client=fake_llm)

    assert len(slots) == 1
    assert slots[0].name == "output_format"
    assert str(slots[0].kind) == "format"
    assert slots[0].required is False
    assert slots[0].description == "期望的输出格式"
    assert slots[0].value == "摘要"
    assert slots[0].revision_notes == ["已修复为中文"]


def test_slot_generator_does_not_force_english_task_to_chinese(monkeypatch) -> None:
    monkeypatch.setattr("agent_generator.slot_generator.raise_for_missing_socksio", lambda: None)
    payload = {
        "user_language": "en",
        "slots": [
            {
                "name": "investigation_goal",
                "kind": "task",
                "description": "Purpose of the investigation",
                "value": "basic concepts",
                "required": True,
                "revision_notes": [],
            }
        ],
    }
    fake_llm = FakeSlotLLM(payload)

    slots = generate_slots("Research machine learning", llm_client=fake_llm)

    assert [request.trace_info["task"] for request in fake_llm.requests] == ["slot_generation"]
    assert slots[0].description == "Purpose of the investigation"
    assert slots[0].value == "basic concepts"


def test_agent_generator_empty_slot_direct_input_fills_value(monkeypatch) -> None:
    slot = Slot(
        name="focus_area",
        kind="constraint",
        description="重点关注的方向，如算法、应用、工具等",
        value=None,
        required=False,
    )
    prompts = []

    def fake_read_text(prompt: str) -> str:
        prompts.append(prompt)
        return "算法"

    monkeypatch.setattr("agent_generator.runner.read_text", fake_read_text)

    changed = _complete_empty_slots([slot], Console(file=StringIO()), auto_approve=False)

    assert changed is True
    assert slot.value == "算法"
    assert slot.revision_notes == ["Filled during empty-slot completion."]
    assert prompts == ["focus_area (重点关注的方向，如算法、应用、工具等) [Enter to skip]> "]


def test_agent_generator_empty_slot_blank_input_skips(monkeypatch) -> None:
    slot = Slot(
        name="time_range",
        kind="constraint",
        description="时间范围偏好，如经典内容、最近进展等",
        value=None,
        required=False,
    )

    monkeypatch.setattr("agent_generator.runner.read_text", lambda prompt: "")

    changed = _complete_empty_slots([slot], Console(file=StringIO()), auto_approve=False)

    assert changed is True
    assert slot.value is None
    assert slot.revision_notes == ["User chose to keep this slot empty."]


def _sample_collected_web_artifact() -> DataArtifact:
    search_artifact = SearchArtifactMetadata(
        query="机器学习 概述",
        provider="bing_html",
        effective_query="机器学习 概述",
        research_summary="Clean summary about machine learning.",
        key_points=["Useful fact", "Another useful fact"],
        source_notes=[{"url": "https://example.com/ml", "note": "Primary source"}],
        results=[
            {
                "title": "Machine Learning Guide",
                "url": "https://example.com/ml",
                "snippet": "Introductory material.",
            }
        ],
        pages=[
            {
                "title": "Machine Learning Guide",
                "url": "https://example.com/ml",
                "content_excerpt": "Readable page content with useful details.",
            }
        ],
    )
    return DataArtifact(
        id="artifact_collected_web",
        name="Collected web research data",
        kind=DataArtifactKind.COLLECTED,
        content=CollectedDataMetadata(
            mode="web",
            task="帮我调查一下机器学习",
            tool_name="web_searcher",
            query="机器学习 概述",
            artifact=search_artifact,
            tool_result=ToolResultMetadata(tool_name="web_searcher", status=ResultStatus.SUCCESS, result=search_artifact),
        ),
        source="web_search:机器学习 概述",
        confidence=0.8,
        preview="Web search found 1 result(s).",
    )


def test_llm_cache_key_includes_max_tokens() -> None:
    client = LLMClient(FakeLLMSettings())
    request_10 = LLMRequest(messages=[LLMMessage(role="user", content="same prompt")], max_tokens=10)
    request_20 = LLMRequest(messages=[LLMMessage(role="user", content="same prompt")], max_tokens=20)

    assert client._make_cache_key(request_10) != client._make_cache_key(request_20)


def test_llm_client_rejects_typed_budget_insufficient_request_before_transport() -> None:
    client = LLMClient(FakeLLMSettings())
    request = LLMRequest(
        messages=[LLMMessage(role="user", content="must not be sent")],
        context_selection=ContextSelectionMetadata(
            max_prompt_chars=10,
            assembly_status=ContextAssemblyStatus.BUDGET_INSUFFICIENT,
            omitted_required_candidate_ids=["required"],
        ),
    )

    with pytest.raises(ContextAssemblyBudgetError):
        client.complete(request)


def test_llm_empty_length_response_is_not_cached(monkeypatch) -> None:
    client = LLMClient(FakeLLMSettings())
    responses = [
        _fake_chat_response("", finish_reason="length"),
        _fake_chat_response("Recovered content", finish_reason="stop"),
    ]
    calls = []

    def fake_create(_openai_client, payload):
        calls.append(payload)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_create)
    request = LLMRequest(messages=[LLMMessage(role="user", content="same prompt")], max_tokens=10)

    first = client.complete(request)
    second = client.complete(request)

    assert first.content == ""
    assert first.finish_reason == "length"
    assert first.provider_details["empty_length_response"] is True
    assert second.content == "Recovered content"
    assert len(calls) == 2


def test_safe_parse_json_failure_cache_never_returns_sentinel_object() -> None:
    invalid = "{not valid json"

    first = safe_parse_json(invalid)
    second = safe_parse_json(invalid)

    assert first is None
    assert second is None


def test_llm_json_mode_treats_non_dict_list_parsed_value_as_invalid(monkeypatch) -> None:
    client = LLMClient(FakeLLMSettings())
    calls = []

    def fake_create(_openai_client, payload):
        calls.append(payload)
        if len(calls) == 1:
            return _fake_chat_response('"just a string"')
        return _fake_chat_response('{"ok": true}')

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_create)

    response = client.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="json")],
            response_format="json_object",
        )
    )

    assert response.parsed_json == {"ok": True}
    assert len(calls) == 2
    assert response.provider_details["json_repair_attempts"] == 2


def test_llm_json_mode_retries_after_cached_parse_failure(monkeypatch) -> None:
    client = LLMClient(FakeLLMSettings())
    invalid = "{not valid json"
    assert safe_parse_json(invalid) is None
    assert safe_parse_json(invalid) is None
    calls = []

    def fake_create(_openai_client, _payload):
        calls.append(_payload)
        if len(calls) == 1:
            return _fake_chat_response(invalid)
        return _fake_chat_response('{"repaired": true}')

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_create)

    response = client.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="json")],
            response_format="json_object",
        )
    )

    assert response.parsed_json == {"repaired": True}
    assert len(calls) == 2


def test_llm_json_mode_invalid_after_retries_raises_clean_error(monkeypatch) -> None:
    client = LLMClient(FakeLLMSettings())
    provider_calls = []

    def fake_create(_openai_client, _payload):
        provider_calls.append(_payload)
        return _fake_chat_response("<not-json>")

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_create)

    with pytest.raises(InvalidLLMResponseError) as exc:
        client.complete(
            LLMRequest(
                messages=[LLMMessage(role="user", content="json")],
                response_format="json_object",
            ),
            max_retries=1,
        )

    assert "LLM returned invalid JSON" in str(exc.value)
    assert "validation errors for LLMResponse" not in str(exc.value)
    assert exc.value.context["response_text"] == "<not-json>"
    assert exc.value.context["response_length"] == len("<not-json>")
    assert exc.value.context["response_preview_start"] == "<not-json>"
    assert exc.value.context["json_repair_attempts"] == 1
    assert "transport_retry_history" in exc.value.context
    # max_retries is the total JSON-attempt count, not "one initial call plus
    # one retry". Transport retries remain independently controlled.
    assert len(provider_calls) == 1


def test_llm_transport_retries_without_env_proxy_after_network_error(monkeypatch) -> None:
    class FakeCompletions:
        def __init__(self, response=None, error: Exception | None = None) -> None:
            self.response = response
            self.error = error

        def create(self, **_payload):
            if self.error:
                raise self.error
            return self.response

    class FakeClient:
        def __init__(self, response=None, error: Exception | None = None) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions(response, error))

    monkeypatch.setenv("all_proxy", "http://127.0.0.1:7892")
    client = LLMClient(FakeLLMSettings())
    direct_response = _fake_chat_response('{"ok": true}')
    direct_client = FakeClient(response=direct_response)
    fallback_calls = []

    def fake_make_openai_client(*, trust_env: bool = True):
        fallback_calls.append(trust_env)
        return direct_client

    monkeypatch.setattr(client, "_make_openai_client", fake_make_openai_client)
    monkeypatch.setattr(client, "_classify_provider_error", lambda _exc: ErrorCategory.NETWORK)

    response = client._create_completion_with_transport_retry(
        FakeClient(error=OpenAIError("Connection error.")),
        {"model": "fake-model", "messages": []},
    )

    assert response is direct_response
    assert fallback_calls == [False]
    assert client._last_transport_retry_history[-1]["reason"] == "env_proxy_fallback"
    assert client._last_transport_retry_history[-1]["trust_env"] is False


def test_llm_transport_retries_incomplete_chunked_reads(monkeypatch) -> None:
    class FakeCompletions:
        def __init__(self, response) -> None:
            self.response = response
            self.calls = 0

        def create(self, **_payload):
            self.calls += 1
            if self.calls == 1:
                raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")
            return self.response

    response = _fake_chat_response('{"ok": true}')
    completions = FakeCompletions(response)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client = LLMClient(FakeLLMSettings())
    client.settings.transport_retries = 1

    result = client._create_completion_with_transport_retry(
        fake_client,
        {"model": "fake-model", "messages": []},
    )

    assert result is response
    assert completions.calls == 2
    assert client._last_transport_retry_history[0]["category"] == ErrorCategory.NETWORK.value
    assert client._last_transport_retry_history[0]["retryable"] is True


def test_llm_openai_client_disables_sdk_level_retries(monkeypatch) -> None:
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("core.llm.OpenAI", FakeOpenAI)
    client = LLMClient(FakeLLMSettings())

    client._make_openai_client()

    assert captured["max_retries"] == 0


def test_llm_request_can_override_timeout_and_transport_retry_budget(monkeypatch) -> None:
    client = LLMClient(FakeLLMSettings())
    calls = []

    def fake_create(_openai_client, payload, *, transport_retries=None):
        calls.append((payload, transport_retries))
        return _fake_chat_response('{"ok": true}')

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_create)

    response = client.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="bounded")],
            response_format="json_object",
            timeout_seconds=4.0,
            transport_retries=0,
        )
    )

    assert response.parsed_json == {"ok": True}
    assert calls[0][0]["timeout"] == 4.0
    assert calls[0][1] == 0


def test_semantic_analyzer_local_fallback_classifies_multilingual_code_goal() -> None:
    analyzer = SemanticAnalyzer(SimpleNamespace())

    analysis = analyzer.fallback_goal_analysis("在目标目录中做一个个人数字助手", "LLMTimeoutError")

    assert analysis.task_type == TaskType.CODING
    assert analysis.risk_level == RiskLevel.MEDIUM
    assert "code_execution" in analysis.required_resources
    assert "LLMTimeoutError" in analysis.reason


def test_llm_extracts_text_from_content_parts(monkeypatch) -> None:
    client = LLMClient(FakeLLMSettings())

    def fake_create(_openai_client, _payload):
        return _fake_chat_response(
            [
                {"type": "text", "text": "Part A"},
                SimpleNamespace(text="Part B"),
            ]
        )

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_create)

    response = client.complete(LLMRequest(messages=[LLMMessage(role="user", content="parts")]))

    assert response.content == "Part A\nPart B"
    diagnostics = response.provider_details["content_diagnostics"]
    assert diagnostics["content_type"] == "list"
    assert diagnostics["content_part_count"] == 2


def test_llm_streaming_chunks_assemble_response_and_emit_events(monkeypatch) -> None:
    client = LLMClient(FakeLLMSettings())
    events = []
    payloads = []

    def fake_create(_openai_client, payload):
        payloads.append(payload)
        return [
            _fake_stream_chunk("Hello "),
            _fake_stream_chunk("world", finish_reason="stop"),
        ]

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_create)

    response = client.complete(
        LLMRequest(messages=[LLMMessage(role="user", content="stream")]),
        stream_callback=events.append,
    )

    assert payloads[0]["stream"] is True
    assert response.content == "Hello world"
    assert response.finish_reason == "stop"
    assert [event.text_delta for event in events if event.event_type == "delta"] == ["Hello ", "world"]
    assert events[-1].event_type == "done"
    assert events[-1].chars_received == len("Hello world")


def test_llm_streaming_json_response_still_parses(monkeypatch) -> None:
    client = LLMClient(FakeLLMSettings())
    events = []

    def fake_create(_openai_client, _payload):
        return [
            _fake_stream_chunk('{"ok": '),
            _fake_stream_chunk("true}", finish_reason="stop"),
        ]

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_create)

    response = client.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="json")],
            response_format="json_object",
        ),
        stream_callback=events.append,
    )

    assert response.parsed_json == {"ok": True}
    assert response.content == '{"ok": true}'
    assert any(event.event_type == "delta" for event in events)


def test_llm_streaming_uses_provider_timeout_as_default_wall_clock_deadline(monkeypatch) -> None:
    client = LLMClient(FakeLLMSettings())
    captured = {}

    def fake_stream(_client, _payload, _callback, *, wall_clock_timeout=None):
        captured["wall_clock_timeout"] = wall_clock_timeout
        return _fake_chat_response("done")

    monkeypatch.setattr(client, "_create_streaming_completion_with_transport_retry", fake_stream)

    response = client.complete(
        LLMRequest(messages=[LLMMessage(role="user", content="bounded stream")]),
        stream_callback=lambda _event: None,
    )

    assert response.content == "done"
    assert captured["wall_clock_timeout"] == FakeLLMSettings.timeout_seconds


def test_llm_streaming_wall_clock_timeout_stops_continuous_chunks(monkeypatch) -> None:
    client = LLMClient(FakeLLMSettings())
    clock = iter([10.0, 12.0])
    monkeypatch.setattr("core.llm.time.monotonic", lambda: next(clock))

    with pytest.raises(LLMTimeoutError, match="wall-clock timeout"):
        client._collect_streaming_completion(
            [_fake_stream_chunk("still streaming")],
            lambda _event: None,
            wall_clock_timeout=1.0,
        )


def test_llm_streaming_wraps_transport_errors_raised_during_iteration() -> None:
    class BrokenStream:
        def __iter__(self):
            yield _fake_stream_chunk("partial")
            raise httpx.RemoteProtocolError("incomplete chunked read")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_payload: BrokenStream()),
        )
    )
    client = LLMClient(FakeLLMSettings())

    with pytest.raises(LLMProviderError, match="incomplete chunked read") as exc:
        client._create_streaming_completion_with_transport_retry(
            fake_client,
            {"model": "fake-model", "messages": []},
            lambda _event: None,
        )

    assert exc.value.category == ErrorCategory.NETWORK


def test_llm_streaming_filters_reasoning_fields_from_visible_delta(monkeypatch) -> None:
    client = LLMClient(FakeLLMSettings())
    events = []

    def fake_create(_openai_client, _payload):
        return [
            _fake_stream_chunk("Visible", reasoning_content="private chain of thought"),
            _fake_stream_chunk("", finish_reason="stop", reasoning_content="more hidden"),
        ]

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_create)

    response = client.complete(
        LLMRequest(messages=[LLMMessage(role="user", content="stream")]),
        stream_callback=events.append,
    )

    visible_deltas = "".join(event.text_delta for event in events if event.event_type == "delta")
    assert visible_deltas == "Visible"
    assert "private chain of thought" not in visible_deltas
    assert response.provider_details["hidden_reasoning_fields"]["reasoning_content"] == len("private chain of thought") + len("more hidden")


def test_llm_streaming_cache_hit_does_not_call_provider(monkeypatch) -> None:
    client = LLMClient(FakeLLMSettings())
    calls = []

    def fake_create(_openai_client, payload):
        calls.append(payload)
        return _fake_chat_response("cached content")

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_create)
    request = LLMRequest(messages=[LLMMessage(role="user", content="cache me")])

    first = client.complete(request)
    events = []
    second = client.complete(request, stream_callback=events.append)

    assert first.content == second.content == "cached content"
    assert len(calls) == 1
    assert [event.event_type for event in events] == ["cache_hit"]


def test_instrumented_llm_streaming_updates_tracker_and_clears_active_operation(monkeypatch) -> None:
    ui = EnhancedUI(Console(record=True, width=100))
    tracker = ProgressTracker(ui)
    client = InstrumentedLLMClient(FakeLLMSettings(), tracker)

    def fake_create(_openai_client, _payload):
        return [
            _fake_stream_chunk("Streaming "),
            _fake_stream_chunk("preview", finish_reason="stop"),
        ]

    monkeypatch.setattr(client, "_create_completion_with_transport_retry", fake_create)

    response = client.complete(LLMRequest(messages=[LLMMessage(role="user", content="prompt")]))

    assert response.content == "Streaming preview"
    assert tracker.get_active_operations() == []
    completed = tracker.get_completed_operations()
    assert completed[0].stream_text == "Streaming preview"
    assert completed[0].tokens_or_chars == len("Streaming preview")


def test_llm_summarizer_uses_injected_client_without_real_llm() -> None:
    fake_llm = FakeSummarizerLLM("Injected summary")

    result = llm_summarizer_executor(
        {
            "text": "Collected text",
            "instruction": "Produce the final output.",
            "max_tokens": 321,
            "_llm_client": fake_llm,
        }
    )

    assert result["summary"] == "Injected summary"
    assert result["tokens_used"] == 123
    assert result["model"] == "fake-summarizer"
    assert result["finish_reason"] == "stop"
    assert result["response_chars"] == len("Injected summary")
    assert result["prompt_chars"] > len("Collected text")
    request = fake_llm.requests[0]
    assert request.response_format == "text"
    assert request.max_tokens == 321
    assert "Produce the final output." in request.messages[0].content
    assert "Collected text" in request.messages[0].content


def test_llm_summarizer_empty_response_returns_diagnostics() -> None:
    fake_llm = FakeSummarizerLLM("")

    result = llm_summarizer_executor(
        {
            "text": "Collected text",
            "instruction": "Produce the final output.",
            "_llm_client": fake_llm,
        }
    )

    assert result["summary"] == ""
    assert result["response_chars"] == 0
    assert result["finish_reason"] == "stop"
    assert result["prompt_chars"] > 0


def test_llm_summarizer_retries_empty_length_response() -> None:
    fake_llm = FakeSummarizerLLM(
        ["", "# 恢复结果\n第二次生成成功。"],
        finish_reason=["length", "stop"],
    )

    result = llm_summarizer_executor(
        {
            "text": "Collected text",
            "instruction": "Produce the final output.",
            "max_tokens": 300,
            "_llm_client": fake_llm,
        }
    )

    assert result["summary"] == "# 恢复结果\n第二次生成成功。"
    assert result["finish_reason"] == "stop"
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["finish_reason"] == "length"
    assert result["attempts"][0]["response_chars"] == 0
    assert result["attempts"][1]["max_tokens"] > 300
    assert "previous response was empty" in fake_llm.requests[1].messages[0].content


def test_llm_summarizer_keeps_diagnostics_after_repeated_empty_length() -> None:
    fake_llm = FakeSummarizerLLM(["", ""], finish_reason=["length", "length"])

    result = llm_summarizer_executor(
        {
            "text": "Collected text",
            "instruction": "Produce the final output.",
            "max_tokens": 300,
            "_llm_client": fake_llm,
        }
    )

    assert result["summary"] == ""
    assert result["finish_reason"] == "length"
    assert result["response_chars"] == 0
    assert len(result["attempts"]) == 2
    assert [attempt["response_chars"] for attempt in result["attempts"]] == [0, 0]


def test_data_processor_generates_user_facing_result_with_llm() -> None:
    fake_llm = FakeSummarizerLLM("# 机器学习报告\n这是处理后的报告正文。")
    slots = [
        Slot(name="output_format", kind="format", description="输出形式", value="报告", required=False),
        Slot(name="language_preference", kind="constraint", description="语言", value="中文", required=False),
    ]

    processed_data, pipeline = process_data(
        "帮我调查一下机器学习",
        slots,
        [_sample_collected_web_artifact()],
        llm_client=fake_llm,
    )

    content = processed_data[0].content
    assert processed_data[0].name == "Processed agent result"
    assert content["result_text"] == "# 机器学习报告\n这是处理后的报告正文。"
    assert content["result_format"] == "报告"
    assert content["processing_tool"] == "llm_summarizer"
    assert "Requested output format: 报告" in content["processing_instruction"]
    assert "Clean summary about machine learning" in fake_llm.requests[0].messages[0].content
    assert "# Processing Evidence Brief" in fake_llm.requests[0].messages[0].content
    assert pipeline.steps[0].strategy == "llm"
    assert "Generated 报告 result" in processed_data[0].preview
    assert content["summarizer_output"]["attempts"][0]["attempt"] == "full_context"
    assert content["summarizer_output"]["response_chars"] == len("# 机器学习报告\n这是处理后的报告正文。")


def test_data_processor_uses_summarizer_internal_retry_for_empty_length() -> None:
    fake_llm = FakeSummarizerLLM(
        ["", "# 内部重试结果\n处理阶段拿到了正文。"],
        finish_reason=["length", "stop"],
    )

    processed_data, pipeline = process_data(
        "帮我调查一下机器学习",
        [Slot(name="output_format", kind="format", description="输出形式", value="报告", required=False)],
        [_sample_collected_web_artifact()],
        llm_client=fake_llm,
    )

    content = processed_data[0].content
    assert content["processing_tool"] == "llm_summarizer"
    assert content["result_text"] == "# 内部重试结果\n处理阶段拿到了正文。"
    assert len(content["summarizer_output"]["attempts"]) == 1
    assert len(content["summarizer_output"]["attempts"][0]["summarizer_attempts"]) == 2
    assert pipeline.steps[0].parameters["processing_retry_count"] == 0


def test_data_processor_retries_empty_summary_with_short_context() -> None:
    fake_llm = FakeSummarizerLLM(["", "# 重试结果\n短上下文生成成功。"])
    slots = [Slot(name="output_format", kind="format", description="输出形式", value="报告", required=False)]

    processed_data, pipeline = process_data(
        "帮我调查一下机器学习",
        slots,
        [_sample_collected_web_artifact()],
        llm_client=fake_llm,
    )

    content = processed_data[0].content
    assert content["processing_tool"] == "llm_summarizer"
    assert content["result_text"] == "# 重试结果\n短上下文生成成功。"
    assert [attempt["attempt"] for attempt in content["summarizer_output"]["attempts"]] == [
        "full_context",
        "short_retry",
    ]
    assert "previous attempt returned an empty response" in fake_llm.requests[1].messages[0].content
    assert pipeline.steps[0].parameters["processing_retry_count"] == 1


def test_data_processor_logs_retry_and_result_diagnostics() -> None:
    logger = FakeStructuredLogger()

    process_data(
        "帮我调查一下机器学习",
        [Slot(name="output_format", kind="format", description="输出形式", value="报告", required=False)],
        [_sample_collected_web_artifact()],
        llm_client=FakeSummarizerLLM(["", "# 重试结果\n成功。"]),
        logger=logger,
    )

    event = logger.events[-1]
    assert event["source_name"] == "data_processor"
    assert event["phase"] == "data_processing"
    assert event["success"] is True
    assert event["input_summary"]["used_retry"] is True
    assert event["output_summary"]["result_text_chars"] > 0


def test_data_processor_falls_back_after_two_empty_summaries() -> None:
    fake_llm = FakeSummarizerLLM(["", ""])
    slots = [Slot(name="output_format", kind="format", description="输出形式", value="报告", required=False)]

    processed_data, pipeline = process_data(
        "帮我调查一下机器学习",
        slots,
        [_sample_collected_web_artifact()],
        llm_client=fake_llm,
    )

    content = processed_data[0].content
    assert content["processing_tool"] == "rule_based_fallback"
    assert "empty response after 2 attempt" in content["warnings"][0]
    assert "Machine Learning Guide" in content["result_text"]
    assert [attempt["response_chars"] for attempt in content["summarizer_output"]["attempts"]] == [0, 0]
    assert pipeline.steps[0].strategy == "function"


def test_data_processor_instruction_follows_non_report_output_format() -> None:
    fake_llm = FakeSummarizerLLM("- 资源 A\n- 资源 B")
    slots = [Slot(name="output_format", kind="format", description="输出形式", value="资源列表", required=False)]

    processed_data, _pipeline = process_data(
        "整理机器学习资源",
        slots,
        [_sample_collected_web_artifact()],
        llm_client=fake_llm,
    )

    instruction = processed_data[0].content["processing_instruction"]
    assert "Requested output format: 资源列表" in instruction
    assert processed_data[0].content["result_format"] == "资源列表"
    assert processed_data[0].content["result_text"] == "- 资源 A\n- 资源 B"


def test_data_processor_falls_back_to_rule_based_result_when_llm_fails() -> None:
    fake_llm = FakeSummarizerLLM(fail=True)
    slots = [Slot(name="output_format", kind="format", description="输出形式", value="摘要", required=False)]

    processed_data, pipeline = process_data(
        "帮我调查一下机器学习",
        slots,
        [_sample_collected_web_artifact()],
        llm_client=fake_llm,
    )

    content = processed_data[0].content
    assert content["processing_tool"] == "rule_based_fallback"
    assert "Clean summary about machine learning." in content["result_text"]
    assert "Useful fact" in content["result_text"]
    assert "LLM processing failed" in content["warnings"][0]
    assert pipeline.steps[0].strategy == "function"


def test_data_processor_fallback_uses_urls_and_excerpts_without_summary() -> None:
    artifact = _sample_collected_web_artifact()
    output = artifact.content.artifact
    output.research_summary = ""
    output.key_points = []

    processed_data, _pipeline = process_data(
        "帮我调查一下机器学习",
        [Slot(name="output_format", kind="format", description="输出形式", value="摘要", required=False)],
        [artifact],
        llm_client=FakeSummarizerLLM(fail=True),
    )

    result_text = processed_data[0].content["result_text"]
    assert "### 参考链接" in result_text
    assert "https://example.com/ml" in result_text
    assert "Introductory material." in result_text
    assert "### 页面摘录" in result_text
    assert "Readable page content" in result_text
    assert "## 资料限制" in result_text


def test_processed_data_preview_displays_result_text() -> None:
    processed_data, _pipeline = process_data(
        "帮我调查一下机器学习",
        [Slot(name="output_format", kind="format", description="输出形式", value="报告", required=False)],
        [_sample_collected_web_artifact()],
        llm_client=FakeSummarizerLLM("# 成品\n这是真正的处理结果。"),
    )

    rendered = StringIO()
    present_data(processed_data, Console(file=rendered, width=120, force_terminal=False))

    text = rendered.getvalue()
    assert "Processed Result" in text
    assert "# 成品" in text
    assert "这是真正的处理结果" in text


def test_pipeline_combiner_embeds_artifacts_and_run_returns_processed_result(tmp_path) -> None:
    processed_data, pipeline = process_data(
        "帮我调查一下机器学习",
        [Slot(name="output_format", kind="format", description="输出形式", value="报告", required=False)],
        [_sample_collected_web_artifact()],
        llm_client=FakeSummarizerLLM("# 成品\n生成 agent 应返回这段。"),
    )
    pipeline.approved = True
    for step in pipeline.steps:
        step.approved = True

    spec = combine_pipelines([pipeline], output_dir=tmp_path, agent_name="demo agent", artifacts=processed_data)

    module_spec = importlib.util.spec_from_file_location("generated_demo_agent", spec.agent_file)
    assert module_spec and module_spec.loader
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    run_output = module.run()

    assert spec.artifacts[0].content["result_text"] == "# 成品\n生成 agent 应返回这段。"
    assert run_output["result"] == "# 成品\n生成 agent 应返回这段。"
    assert run_output["artifacts"][0]["id"] == "artifact_processed_result"


def test_config_check_cli_returns_success() -> None:
    from ui.cli import main

    assert main(["config", "check"]) == 0


def test_embedding_settings_inherit_llm_endpoint_when_unset(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENPILOT_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("OPENPILOT_LLM_API_KEY", "llm-key")
    monkeypatch.delenv("OPENPILOT_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("OPENPILOT_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("OPENPILOT_EMBEDDING_MODEL", raising=False)
    from core.config import EmbeddingSettings

    settings = EmbeddingSettings()

    assert settings.base_url == "https://llm.example/v1"
    assert settings.api_key == "llm-key"
    assert settings.model == "text-embedding-3-small"


def test_embedding_settings_allow_independent_endpoint_and_model(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENPILOT_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("OPENPILOT_LLM_API_KEY", "llm-key")
    monkeypatch.setenv("OPENPILOT_EMBEDDING_BASE_URL", "https://embed.example/v1")
    monkeypatch.setenv("OPENPILOT_EMBEDDING_API_KEY", "embed-key")
    monkeypatch.setenv("OPENPILOT_EMBEDDING_MODEL", "embedding-special")
    from core.config import EmbeddingSettings

    settings = EmbeddingSettings()

    assert settings.base_url == "https://embed.example/v1"
    assert settings.api_key == "embed-key"
    assert settings.model == "embedding-special"


def test_tool_executor_rejects_missing_required_input() -> None:
    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="read-file",
                tool_name="file_reader",
                reason="capability_match",
                input_metadata={},
            )
        )
    finally:
        executor.shutdown()

    assert not result.success
    assert result.error is not None
    assert result.error.error_type == "InvalidInput"
    assert "file_path" in result.error.error_message
    assert result.error.recoverable
    assert result.error.retry_recommended
    assert result.attributes["failure_mode"] == "invalid_input"
    assert "required metadata fields" in result.attributes["recovery_strategy"]


def test_tool_executor_reads_file_and_applies_defaults(tmp_path) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("hello openpilot", encoding="utf-8")
    registry = _registered_registry()
    executor = ToolExecutor(registry)
    selection = ToolSelection(
        step_id="read-file",
        tool_name="file_reader",
        reason="capability_match",
        input_metadata={"file_path": str(target)},
    )
    try:
        result = executor.execute_single(selection)
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["content"] == "hello openpilot"
    assert result.output_metadata.result["encoding"] == "utf-8"
    assert result.output_metadata.result["file_type"] == "data"
    assert result.output_metadata.result["truncated"] is False
    assert selection.input_metadata.encoding == "utf-8"
    assert selection.input_metadata.max_size_mb == 10


def test_file_reader_supports_sample_mode(tmp_path) -> None:
    target = tmp_path / "sample.log"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="sample-file",
                tool_name="file_reader",
                reason="capability_match",
                input_metadata={
                    "file_path": str(target),
                    "read_mode": "sample",
                    "max_lines": 2,
                },
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["content"] == "one\ntwo\n"
    assert result.output_metadata.result["lines_read"] == 2
    assert result.output_metadata.result["total_lines"] == 3
    assert result.output_metadata.result["truncated"] is True
    assert result.output_metadata.result["attributes"]["read_mode"] == "sample"


def test_file_reader_honors_explicit_window_in_full_mode(tmp_path) -> None:
    target = tmp_path / "window.py"
    target.write_text("line-0\nline-1\nline-2\nline-3\nline-4\n", encoding="utf-8")
    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="window-file",
                tool_name="file_reader",
                reason="capability_match",
                input_metadata={
                    "file_path": str(target),
                    "max_lines": 2,
                    "offset": 2,
                },
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["content"] == "line-2\nline-3\n"
    assert result.output_metadata.result["lines_read"] == 2
    assert result.output_metadata.result["total_lines"] == 5
    assert result.output_metadata.result["truncated"] is True
    assert result.output_metadata.result["attributes"]["read_mode"] == "window"


def test_file_reader_supports_range_mode(tmp_path) -> None:
    target = tmp_path / "range.py"
    target.write_text("line-0\nline-1\nline-2\nline-3\n", encoding="utf-8")
    result = file_reader_executor(
        ToolInputMetadata(
            file_path=str(target),
            read_mode="range",
            max_lines=2,
            offset=1,
        )
    )

    assert result.result["content"] == "line-1\nline-2\n"
    assert result.result["attributes"]["read_mode"] == "range"

    offset_result = file_reader_executor(
        ToolInputMetadata(
            file_path=str(target),
            read_mode="offset",
            max_lines=1,
            offset=2,
        )
    )
    assert offset_result.result["content"] == "line-2\n"
    assert offset_result.result["attributes"]["read_mode"] == "offset"


def test_file_reader_supports_tail_mode(tmp_path) -> None:
    target = tmp_path / "tail.log"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="tail-file",
                tool_name="file_reader",
                reason="capability_match",
                input_metadata={
                    "file_path": str(target),
                    "read_mode": "tail",
                    "max_lines": 2,
                },
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["content"] == "two\nthree\n"
    assert result.output_metadata.result["lines_read"] == 2
    assert result.output_metadata.result["total_lines"] == 3
    assert result.output_metadata.result["truncated"] is True
    assert result.output_metadata.result["attributes"]["read_mode"] == "tail"


def test_file_reader_adaptive_mode_samples_log_files(tmp_path) -> None:
    target = tmp_path / "events.log"
    target.write_text("first\nsecond\nthird\n", encoding="utf-8")
    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="adaptive-file",
                tool_name="file_reader",
                reason="capability_match",
                input_metadata={
                    "file_path": str(target),
                    "read_mode": "adaptive",
                    "max_lines": 2,
                },
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["file_type"] == "log"
    assert result.output_metadata.result["content"] == "first\nsecond\n"
    assert result.output_metadata.result["truncated"] is True
    assert result.output_metadata.result["attributes"]["read_mode"] == "adaptive"


def test_file_reader_adaptive_mode_respects_explicit_window_for_code_files(tmp_path) -> None:
    target = tmp_path / "adaptive.py"
    target.write_text("".join(f"line-{index}\n" for index in range(8)), encoding="utf-8")

    result = file_reader_executor(
        ToolInputMetadata(
            file_path=str(target),
            read_mode="adaptive",
            max_lines=2,
            offset=3,
        )
    )

    assert result.result["content"] == "line-3\nline-4\n"
    assert result.result["lines_read"] == 2
    assert result.result["total_lines"] == 8
    assert result.result["truncated"] is True
    assert result.result["attributes"]["read_mode"] == "adaptive"
    assert result.result["read_window"].model_dump(mode="json") == {
        "read_mode": "adaptive",
        "offset": 3,
        "max_lines": 2,
    }

    with pytest.raises(ValueError, match="offset must be greater than or equal to 0"):
        file_reader_executor(
            ToolInputMetadata(
                file_path=str(target),
                read_mode="adaptive",
                offset=-1,
            )
        )


def test_file_read_window_spec_is_typed_and_round_trips() -> None:
    from metadata import FileReadWindowSpec

    spec = FileReadWindowSpec(
        file_path="Code/tests/test_provider_tool_roundtrip.py",
        read_mode="adaptive",
        offset=1240,
        max_lines=120,
    )
    restored = FileReadWindowSpec.model_validate_json(spec.model_dump_json())
    assert restored == spec
    with pytest.raises(ValueError):
        FileReadWindowSpec(
            file_path="Code/tests/test_provider_tool_roundtrip.py",
            read_mode="adaptive",
            offset=-1,
            max_lines=120,
        )


def test_file_reader_adaptive_mode_without_window_stays_full_for_code_and_config(tmp_path) -> None:
    for filename, content in (
        ("adaptive.py", "line-0\nline-1\nline-2\n"),
        ("adaptive.json", '{"name": "openpilot"}\n'),
    ):
        target = tmp_path / filename
        target.write_text(content, encoding="utf-8")

        result = file_reader_executor(
            ToolInputMetadata(
                file_path=str(target),
                read_mode="adaptive",
            )
        )

        assert result.result["content"] == content
        assert result.result["truncated"] is False
        assert result.result["lines_read"] == result.result["total_lines"]

    config_target = tmp_path / "adaptive-window.json"
    config_target.write_text('{"line": 0}\n{"line": 1}\n{"line": 2}\n', encoding="utf-8")
    bounded = file_reader_executor(
        ToolInputMetadata(
            file_path=str(config_target),
            read_mode="adaptive",
            max_lines=1,
            offset=1,
        )
    )
    assert bounded.result["content"] == '{"line": 1}\n'
    assert bounded.result["lines_read"] == 1
    assert bounded.result["total_lines"] == 3
    assert bounded.result["truncated"] is True


def test_file_reader_returns_placeholder_for_binary_files(tmp_path) -> None:
    target = tmp_path / "image.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n")
    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="binary-file",
                tool_name="file_reader",
                reason="capability_match",
                input_metadata={"file_path": str(target)},
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["content"] == "[Binary file - content not displayed]"
    assert result.output_metadata.result["file_type"] == "binary"
    assert result.output_metadata.result["encoding"] == "binary"
    assert result.output_metadata.result["lines_read"] == 0


def test_multi_file_reader_scans_directory_with_glob(tmp_path) -> None:
    first = tmp_path / "alpha完成报告.md"
    second = tmp_path / "beta完成报告.md"
    ignored = tmp_path / "notes.txt"
    first.write_text("alpha", encoding="utf-8")
    second.write_text("beta", encoding="utf-8")
    ignored.write_text("ignored", encoding="utf-8")

    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="read-matching-files",
                tool_name="multi_file_reader",
                reason="capability_match",
                input_metadata={
                    "directory_path": str(tmp_path),
                    "pattern": "*完成报告.md",
                },
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["count"] == 2
    assert result.output_metadata.result["files"] == [str(first), str(second)]
    assert "alpha" in result.output_metadata.result["content"]
    assert "beta" in result.output_metadata.result["content"]
    assert "ignored" not in result.output_metadata.result["content"]


def test_multi_file_reader_creates_sketch_for_empty_directory(tmp_path) -> None:
    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="read-empty-directory",
                tool_name="multi_file_reader",
                reason="capability_match",
                input_metadata={
                    "directory_path": str(tmp_path),
                },
            )
        )
    finally:
        executor.shutdown()

    sketch_path = tmp_path / "sketch.json"
    sketch_payload = json.loads(sketch_path.read_text(encoding="utf-8"))
    assert result.success
    assert sketch_path.exists()
    assert sketch_payload["files"] == {}
    assert result.output_metadata.result["count"] == 0
    assert result.output_metadata.result["sketch_files"] == [str(sketch_path)]
    assert result.output_metadata.result["sketch_refreshed"] is True


def test_multi_file_reader_skips_binary_files_and_keeps_directory_sketch(tmp_path) -> None:
    source = tmp_path / "app.py"
    binary = tmp_path / "asset.bin"
    source.write_text("print('ok')\n", encoding="utf-8")
    binary.write_bytes(b"\x00\xf6\x00not-text")

    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="read-directory-with-binary",
                tool_name="multi_file_reader",
                reason="capability_match",
                input_metadata={
                    "directory_path": str(tmp_path),
                    "pattern": "*",
                },
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert (tmp_path / "sketch.json").exists()
    assert str(source) in result.output_metadata.result["files"]
    assert str(binary) not in result.output_metadata.result["files"]
    assert "print('ok')" in result.output_metadata.result["content"]
    skipped_files = result.output_metadata.result["skipped_files"]
    assert skipped_files == [
        {
            "path": str(binary),
            "reason": "binary_file",
            "size_bytes": binary.stat().st_size,
            "mime_type": "application/octet-stream",
        }
    ]


def test_multi_file_reader_refreshes_parent_sketch_for_explicit_files(tmp_path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("remember this\n", encoding="utf-8")

    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="read-explicit-file",
                tool_name="multi_file_reader",
                reason="capability_match",
                input_metadata={
                    "file_paths": [str(source)],
                },
            )
        )
    finally:
        executor.shutdown()

    sketch_path = tmp_path / "sketch.json"
    assert result.success
    assert sketch_path.exists()
    assert result.output_metadata.result["files"] == [str(source)]
    assert result.output_metadata.result["sketch_files"] == [str(sketch_path)]


def test_code_reviewer_does_not_reject_specific_stack_judgment_without_product_intent() -> None:
    result = code_reviewer_executor(
        {
            "code": "print('hi')\n",
            "language": "python",
            "prompt_context": {
                "product_judgment": {
                    "preferred_stack": "pygame",
                }
            },
        }
    )

    assert result["approved"] is True
    assert not any("Product-fit rubric not satisfied" in item for item in result["warnings"])


def test_code_reviewer_uses_diagnosis_alignment_context_generically() -> None:
    result = code_reviewer_executor(
        {
            "code": "print('ok')\n",
            "language": "python",
            "prompt_context": {
                "selected_candidate": {
                    "candidate_id": "gap_ux",
                    "title": "Improve primary workflow feedback",
                }
            },
        }
    )

    assert result["approved"] is True
    assert "diagnosis_alignment" in result["rejection_categories"]
    assert any("Diagnosis alignment" in item for item in result["warnings"])


def test_code_reviewer_allows_pygame_code_without_product_fit_warning() -> None:
    result = code_reviewer_executor(
        {
            "code": "import pygame\npygame.init()\n",
            "language": "python",
            "prompt_context": {
                "product_judgment": {
                    "preferred_stack": "pygame",
                }
            },
        }
    )

    assert not any("Product-fit rubric not satisfied" in item for item in result["warnings"])
    assert not any("Product-fit rubric not satisfied" in item for item in result["suggestions"])


def test_code_reviewer_rejects_generic_product_intent_drift() -> None:
    result = code_reviewer_executor(
        {
            "code": "import curses\n\ndef main(stdscr):\n    pass\n",
            "language": "python",
            "prompt_context": {
                "product_intent": {
                    "runtime_mode": "standalone_gui",
                    "delivery_surface": "native_window",
                    "core_capabilities": ["visible_feedback"],
                    "disallowed_substitutions": ["terminal_ui"],
                }
            },
        }
    )

    assert result["approved"] is False
    assert "product_intent_drift" in result["rejection_categories"]
    assert any("Product intent drift" in item for item in result["warnings"])


def test_code_reviewer_rejects_unapproved_dependency_drift() -> None:
    result = code_reviewer_executor(
        {
            "code": "import curses\n\ndef main(stdscr):\n    stdscr.addstr(0, 0, 'game')\n",
            "language": "python",
            "prompt_context": {
                "dependency_strategy": {
                    "preserve_packages": ["pygame"],
                    "replaceable_packages": [],
                    "rationale": ["Preserve pygame rendering/input capability."],
                },
                "dependencies": [
                    {
                        "kind": "project_dependency",
                        "package_name": "pygame",
                        "import_names": ["pygame"],
                        "dependency_sources": ["installed", "import_scan"],
                        "role": "interactive_window_rendering_input_game_loop",
                    }
                ],
            },
        }
    )

    assert result["approved"] is False
    assert "dependency_drift" in result["rejection_categories"]
    assert any("Dependency drift" in item for item in result["warnings"])


def test_code_reviewer_requires_git_snapshot_for_critical_dangerous_operations() -> None:
    result = code_reviewer_executor(
        {
            "code": "value = eval(user_input)\n",
            "language": "python",
            "prompt_context": {},
        }
    )

    assert result["approved"] is False
    assert "dangerous_operation" in result["rejection_categories"]


def test_code_reviewer_allows_dangerous_operations_with_git_snapshot_context() -> None:
    result = code_reviewer_executor(
        {
            "code": "value = eval(user_input)\n",
            "language": "python",
            "prompt_context": {
                "git_safety_context": {
                    "project_path": "/tmp/project",
                    "snapshot_available": True,
                    "snapshot": {
                        "kind": "git_snapshot",
                        "commit_hash": "abc123",
                        "created": True,
                    },
                }
            },
        }
    )

    assert result["approved"] is True
    assert "dangerous_operation" not in result["rejection_categories"]
    assert result["git_safety_context"]["snapshot_available"] is True


def test_code_reviewer_rejects_out_of_scope_destructive_operation_even_with_git_snapshot(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    result = code_reviewer_executor(
        {
            "code": f"import os\nos.remove('{outside}')\n",
            "language": "python",
            "prompt_context": {
                "git_safety_context": {
                    "project_path": str(project),
                    "snapshot_available": True,
                    "snapshot": {
                        "kind": "git_snapshot",
                        "commit_hash": "abc123",
                        "created": True,
                    },
                }
            },
        }
    )

    assert result["approved"] is False
    assert "safety_boundary" in result["rejection_categories"]
    assert any("outside project" in item for item in result["warnings"])


def test_tool_executor_uses_contract_metadata_defaults() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="contract_default_tool",
            display_name="Contract Default Tool",
            description="Uses contract metadata defaults",
            permission_level=PermissionLevel.LOW,
            contract_metadata=ToolContractMetadata(
                tool_name="contract_default_tool",
                input_metadata_type="ToolInputMetadata",
                output_metadata_type="ToolResultMetadata",
                input_defaults={"query": "default query"},
            ),
            audit_required=False,
        ),
        lambda metadata: {"query": metadata.query},
    )
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="contract-default",
                tool_name="contract_default_tool",
                reason="capability_match",
                input_metadata={},
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result.get("query") == "default query"
    assert result.attributes.get("validation_warnings", []) == []


def test_command_executor_defaults_to_dry_run(tmp_path) -> None:
    target = tmp_path / "should_not_exist.txt"
    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="dry-run-command",
                tool_name="command_executor",
                reason="capability_match",
                input_metadata={"command": f"touch {target}"},
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["stdout"].startswith("[DRY RUN]")
    assert result.output_metadata.result["exit_code"] == 0
    assert result.output_metadata.result["risk_assessment"]["risk_level"] == "medium"
    assert not target.exists()


def test_command_executor_automatic_runs_low_risk_command() -> None:
    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="automatic-command",
                tool_name="command_executor",
                reason="capability_match",
                input_metadata={
                    "command": f"{sys.executable} -c \"print('ok')\"",
                    "mode": "automatic",
                    "timeout": 10,
                },
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["success"]
    assert result.output_metadata.result["stdout"].strip() == "ok"
    assert result.output_metadata.result["stderr"] == ""
    assert result.output_metadata.result["exit_code"] == 0


def test_command_executor_normalizes_execute_mode_to_automatic() -> None:
    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="execute-command",
                tool_name="command_executor",
                reason="capability_match",
                input_metadata={
                    "command": f"{sys.executable} -c \"print('ok')\"",
                    "mode": "execute",
                    "timeout": 10,
                },
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["stdout"].strip() == "ok"


def test_web_searcher_requires_query() -> None:
    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="search-missing-query",
                tool_name="web_searcher",
                reason="capability_match",
                input_metadata={},
            )
        )
    finally:
        executor.shutdown()

    assert not result.success
    assert result.error is not None
    assert result.error.error_type == "InvalidInput"
    assert "query" in result.error.error_message


def test_web_searcher_parses_google_html_without_network() -> None:
    html = """
    <html>
      <body>
        <a href="/url?q=https%3A%2F%2Fexample.com%2Falpha&amp;sa=U">Alpha Result</a>
        <a href="/search?q=openpilot">Google internal link</a>
        <a href="/url?q=https%3A%2F%2Fdocs.example.org%2Fbeta&amp;sa=U">Beta Result</a>
      </body>
    </html>
    """

    def fake_http_get(url: str, timeout: int) -> str:
        assert "google.com/search" in url
        assert "q=openpilot+research" in url
        assert timeout == 7
        return html

    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="search-web",
                tool_name="web_searcher",
                reason="capability_match",
                input_metadata={
                    "query": "openpilot research",
                    "max_results": 1,
                    "max_pages": 0,
                    "timeout": 7,
                    "llm_cleanup": False,
                    "_http_get": fake_http_get,
                },
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["provider"] == "google_html"
    assert result.output_metadata.result["effective_query"] == "openpilot research"
    assert result.output_metadata.result["count"] == 1
    assert result.output_metadata.result["llm_cleanup"] is False
    assert result.output_metadata.result["results"][0] == {
        "rank": 1,
        "title": "Alpha Result",
        "url": "https://example.com/alpha",
        "snippet": "",
        "source_domain": "example.com",
    }
    assert result.output_metadata.result["search_attempts"][0]["provider"] == "google_html"
    assert result.output_metadata.result["warnings"] == []


def test_web_searcher_parses_google_url_parameter_without_network() -> None:
    html = """
    <html><body>
      <a href="/url?url=https%3A%2F%2Fexample.com%2Furl-param&amp;sa=U">URL Param Result</a>
    </body></html>
    """

    result = web_searcher_executor(
        {
            "query": "openpilot",
            "max_pages": 0,
            "llm_cleanup": False,
            "max_search_attempts": 1,
            "_http_get": lambda url, timeout: html,
        }
    )

    assert result["count"] == 1
    assert result["results"][0]["url"] == "https://example.com/url-param"


def test_web_searcher_parses_bing_ck_redirect_without_google_base() -> None:
    target = "https://example.com/bing-article"
    encoded = urlsafe_b64encode(target.encode("utf-8")).decode("ascii").rstrip("=")
    html = f"""
    <ol>
      <li class="b_algo">
        <h2><a href="/ck/a?u=a1{encoded}&amp;ntb=1">Bing Redirect Result</a></h2>
        <p>Bing snippet.</p>
      </li>
    </ol>
    """

    def fake_http_get(url: str, timeout: int) -> str:
        if "google.com/search" in url:
            return "<html><body>No Google results</body></html>"
        if "bing.com/search" in url:
            return html
        raise AssertionError(f"unexpected url: {url}")

    result = web_searcher_executor(
        {
            "query": "openpilot",
            "max_pages": 0,
            "llm_cleanup": False,
            "max_search_attempts": 2,
            "_http_get": fake_http_get,
        }
    )

    assert result["provider"] == "bing_html"
    assert result["count"] == 1
    assert result["results"][0]["url"] == target
    assert "google.com/ck" not in result["results"][0]["url"]


def test_web_searcher_empty_results_return_warning_without_failure() -> None:
    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="search-empty",
                tool_name="web_searcher",
                reason="capability_match",
                input_metadata={
                    "query": "nothing",
                    "llm_cleanup": False,
                    "_http_get": lambda url, timeout: "<html><body>No results</body></html>",
                },
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["count"] == 0
    assert result.output_metadata.result["results"] == []
    assert result.output_metadata.result["search_attempts"]
    assert all(attempt["status"] == "empty" for attempt in result.output_metadata.result["search_attempts"])
    assert result.output_metadata.result["warnings"]


def test_web_searcher_continues_from_empty_google_to_bing_without_network() -> None:
    bing_html = """
    <ol>
      <li class="b_algo">
        <h2><a href="https://example.com/bing-result">Bing Result</a></h2>
        <p>Bing snippet with useful details.</p>
      </li>
    </ol>
    """

    def fake_http_get(url: str, timeout: int) -> str:
        if "google.com/search" in url:
            return "<html><body>No Google results</body></html>"
        if "bing.com/search" in url:
            return bing_html
        raise AssertionError(f"unexpected url: {url}")

    result = web_searcher_executor(
        {
            "query": "openpilot research",
            "max_pages": 0,
            "llm_cleanup": False,
            "max_search_attempts": 2,
            "_http_get": fake_http_get,
        }
    )

    assert result["provider"] == "bing_html"
    assert result["effective_query"] == "openpilot research"
    assert [attempt["status"] for attempt in result["search_attempts"]] == ["empty", "success"]
    assert result["results"][0]["url"] == "https://example.com/bing-result"


def test_web_searcher_continues_to_duckduckgo_and_decodes_redirect_without_network() -> None:
    duckduckgo_html = """
    <div class="result">
      <a class="result__a" href="/l/?uddg=https%3A%2F%2Fpypi.org%2Fproject%2FSpeechRecognition%2F">
        SpeechRecognition - PyPI
      </a>
      <a class="result__snippet">Install the published speech recognition package.</a>
    </div>
    """

    def fake_http_get(url: str, timeout: int) -> str:
        if "google.com/search" in url or "bing.com/search" in url:
            return "<html><body>No results</body></html>"
        if "html.duckduckgo.com/html/" in url:
            return duckduckgo_html
        raise AssertionError(f"unexpected url: {url}")

    result = web_searcher_executor(
        {
            "query": "speech_recognition pypi",
            "max_pages": 0,
            "llm_cleanup": False,
            "max_search_attempts": 3,
            "_http_get": fake_http_get,
        }
    )

    assert result["provider"] == "duckduckgo_html"
    assert [attempt["status"] for attempt in result["search_attempts"]] == ["empty", "empty", "success"]
    assert result["results"][0]["url"] == "https://pypi.org/project/SpeechRecognition/"


def test_web_searcher_builds_short_query_variants_from_long_slot_query() -> None:
    seen_search_urls = []
    html = '<a href="/url?q=https%3A%2F%2Fexample.com%2Fml&amp;sa=U">ML Result</a>'

    def fake_http_get(url: str, timeout: int) -> str:
        seen_search_urls.append(url)
        if "google.com/search" in url:
            return html
        raise AssertionError(f"unexpected url: {url}")

    result = web_searcher_executor(
        {
            "query": "帮我调研一下机器学习 subject: 机器学习 depth: overview output_format: report language: 中文 time_focus: latest",
            "max_pages": 0,
            "llm_cleanup": False,
            "max_search_attempts": 1,
            "_http_get": fake_http_get,
        }
    )

    assert result["effective_query"].startswith("机器学习")
    assert "subject:" not in result["effective_query"]
    assert "subject%3A" not in seen_search_urls[0]
    assert "机器学习" in result["effective_query"]


def test_agent_generator_search_query_keeps_natural_language_without_slots() -> None:
    assert _build_search_query("帮我调查机器学习", []) == "帮我调查机器学习"


def test_agent_generator_search_query_uses_explicit_subject_slot() -> None:
    slot = Slot(
        name="subject",
        kind="data_source",
        description="搜索主题",
        value="机器学习",
        required=True,
    )

    assert _build_search_query("帮我调查机器学习", [slot]) == "机器学习"


def test_web_searcher_llm_query_planner_runs_before_raw_natural_language() -> None:
    seen_queries = []
    html = '<a href="/url?q=https%3A%2F%2Fexample.com%2Fml&amp;sa=U">ML Result</a>'

    def fake_http_get(url: str, timeout: int) -> str:
        seen_queries.append(parse_qs(urlparse(url).query)["q"][0])
        return html

    fake_llm = FakeCleanupLLM(keyword_variants="机器学习\nmachine learning overview")
    result = web_searcher_executor(
        {
            "query": "帮我调查机器学习",
            "max_pages": 0,
            "llm_cleanup": False,
            "max_search_attempts": 1,
            "_http_get": fake_http_get,
            "_llm_client": fake_llm,
        }
    )

    assert result["effective_query"] == "机器学习"
    assert seen_queries[0] == "机器学习"
    assert [request.trace_info["task"] for request in fake_llm.requests] == ["keyword_generation"]


def test_web_searcher_llm_query_planner_generalizes_without_keyword_lists() -> None:
    fake_llm = FakeCleanupLLM(keyword_variants="transformer 核心脉络\ntransformer architecture overview")

    variants, warnings = _build_search_query_variants(
        query="我想弄明白 transformer 的核心脉络",
        llm_client=fake_llm,
        max_variants=4,
    )

    assert warnings == []
    assert variants[0] == "transformer 核心脉络"
    assert "我想弄明白 transformer 的核心脉络" in variants


def test_web_searcher_llm_query_planner_supports_english_natural_language() -> None:
    fake_llm = FakeCleanupLLM(keyword_variants="edge AI trends\nedge artificial intelligence market trends")

    variants, warnings = _build_search_query_variants(
        query="I need to get a solid picture of edge AI trends",
        llm_client=fake_llm,
        max_variants=4,
    )

    assert warnings == []
    assert variants[0] == "edge AI trends"


def test_web_searcher_tries_next_query_variant_until_success() -> None:
    google_html = '<a href="/url?q=https%3A%2F%2Fexample.com%2Fml&amp;sa=U">Machine Learning</a>'

    def fake_http_get(url: str, timeout: int) -> str:
        if "q=%E6%9C%BA%E5%99%A8%E5%AD%A6%E4%B9%A0" in url and "overview" not in url and "%E6%A6%82%E8%BF%B0" not in url:
            return google_html
        return "<html><body>No results for first variant</body></html>"

    result = web_searcher_executor(
        {
            "query": "subject: 机器学习 depth: overview",
            "max_pages": 0,
            "llm_cleanup": False,
            "max_search_attempts": 3,
            "_http_get": fake_http_get,
        }
    )

    assert result["count"] == 1
    assert result["effective_query"] == "机器学习"
    assert [attempt["status"] for attempt in result["search_attempts"]] == ["empty", "empty", "success"]


def test_web_searcher_llm_keyword_generation_failure_uses_local_variants() -> None:
    html = '<a href="/url?q=https%3A%2F%2Fexample.com%2Fml&amp;sa=U">ML Result</a>'

    def fake_http_get(url: str, timeout: int) -> str:
        if "google.com/search" in url:
            return html
        raise AssertionError(f"unexpected url: {url}")

    fake_llm = FakeCleanupLLM(fail_tasks={"keyword_generation"})
    result = web_searcher_executor(
        {
            "query": "subject: 机器学习 depth: overview",
            "max_pages": 0,
            "llm_cleanup": False,
            "max_search_attempts": 1,
            "_http_get": fake_http_get,
            "_llm_client": fake_llm,
        }
    )

    assert result["count"] == 1
    assert any("LLM keyword generation failed" in warning for warning in result["warnings"])
    assert [request.trace_info["task"] for request in fake_llm.requests] == ["keyword_generation"]


def test_web_searcher_search_budget_warning_is_not_duplicated(monkeypatch) -> None:
    monotonic_values = iter([0, 4, 5, 6])
    monkeypatch.setattr("tools.web_searcher.time.monotonic", lambda: next(monotonic_values))

    result = web_searcher_executor(
        {
            "query": "openpilot research",
            "max_pages": 0,
            "llm_cleanup": False,
            "search_budget_seconds": 3,
        }
    )

    assert result["warnings"].count("Search budget exhausted after 3s.") == 1


def test_web_searcher_default_http_get_uses_httpx_env_proxy_and_redirects(monkeypatch) -> None:
    calls = {}

    class FakeResponse:
        text = "<html>ok</html>"

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            calls["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def get(self, url: str):
            calls["url"] = url
            return FakeResponse()

    monkeypatch.setattr("tools.web_searcher._load_dotenv_for_proxy", lambda: calls.setdefault("dotenv", True))
    monkeypatch.setattr("tools.web_searcher.httpx.Client", FakeClient)

    html = _default_http_get("https://example.com/search", 7)

    assert html == "<html>ok</html>"
    assert calls["dotenv"] is True
    assert calls["url"] == "https://example.com/search"
    assert calls["client_kwargs"]["timeout"] == 7
    assert calls["client_kwargs"]["trust_env"] is True
    assert calls["client_kwargs"]["follow_redirects"] is True


def test_web_searcher_network_errors_include_sanitized_proxy_diagnostics(monkeypatch) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)

    def fake_http_get(url: str, timeout: int) -> str:
        raise httpx.ReadError("SSL: UNEXPECTED_EOF_WHILE_READING via https://proxy.example/secret")

    result = web_searcher_executor(
        {
            "query": "openpilot",
            "max_pages": 0,
            "llm_cleanup": False,
            "max_search_attempts": 1,
            "_http_get": fake_http_get,
        }
    )

    assert result["count"] == 0
    assert result["search_attempts"][0]["status"] == "network_error"
    assert "Network/proxy failure" in result["search_attempts"][0]["error"]
    assert "[redacted-url]" in result["search_attempts"][0]["error"]
    assert "proxy.example" not in json.dumps(result.result.model_dump(mode="json"), ensure_ascii=False)
    assert result["network_diagnostics"]["proxy_env_vars"] == []
    assert any("No HTTP_PROXY/HTTPS_PROXY/ALL_PROXY" in warning for warning in result["warnings"])


def test_web_searcher_blocked_search_page_is_diagnosed() -> None:
    def fake_http_get(url: str, timeout: int) -> str:
        return "<html><title>CAPTCHA</title><body>Please enable JavaScript to continue</body></html>"

    result = web_searcher_executor(
        {
            "query": "openpilot",
            "max_pages": 0,
            "llm_cleanup": False,
            "max_search_attempts": 1,
            "_http_get": fake_http_get,
        }
    )

    assert result["count"] == 0
    assert result["search_attempts"][0]["status"] == "blocked"
    assert "challenge" in result["search_attempts"][0]["error"].lower()
    assert any("status=blocked" in warning for warning in result["warnings"])


def test_web_searcher_http_status_blocked_is_diagnosed() -> None:
    request = httpx.Request("GET", "https://www.google.com/search?q=openpilot")
    response = httpx.Response(429, request=request)

    def fake_http_get(url: str, timeout: int) -> str:
        raise httpx.HTTPStatusError("too many requests", request=request, response=response)

    result = web_searcher_executor(
        {
            "query": "openpilot",
            "max_pages": 0,
            "llm_cleanup": False,
            "max_search_attempts": 1,
            "_http_get": fake_http_get,
        }
    )

    assert result["count"] == 0
    assert result["search_attempts"][0]["status"] == "blocked"
    assert "HTTP 429" in result["search_attempts"][0]["error"]


def test_web_searcher_fallback_extracts_external_search_links() -> None:
    html = """
    <html><body>
      <div class="unexpected_result_shape">
        <a href="https://www.bing.com/search?q=openpilot">Internal search</a>
        <a href="https://example.com/article">Useful Article</a>
        <a href="/url?q=https%3A%2F%2Fexample.org%2Fguide&amp;sa=U">Useful Guide</a>
      </div>
    </body></html>
    """

    def fake_http_get(url: str, timeout: int) -> str:
        return html

    result = web_searcher_executor(
        {
            "query": "openpilot",
            "max_results": 2,
            "max_pages": 0,
            "llm_cleanup": False,
            "max_search_attempts": 1,
            "_http_get": fake_http_get,
        }
    )

    assert result["count"] == 2
    assert result["search_attempts"][0]["status"] == "success"
    assert [item["url"] for item in result["results"]] == [
        "https://example.com/article",
        "https://example.org/guide",
    ]


def test_web_searcher_network_errors_are_reported_without_failure() -> None:
    def failing_http_get(url: str, timeout: int) -> str:
        raise OSError("network down")

    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="search-network-failure",
                tool_name="web_searcher",
                reason="capability_match",
                input_metadata={
                    "query": "openpilot",
                    "_http_get": failing_http_get,
                },
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["count"] == 0
    assert result.output_metadata.result["results"] == []
    assert result.output_metadata.result["search_attempts"]
    assert all(attempt["status"] == "network_error" for attempt in result.output_metadata.result["search_attempts"])
    assert any("network down" in warning for warning in result.output_metadata.result["warnings"])


def test_web_searcher_fetches_pages_and_cleans_with_llm_without_network() -> None:
    search_html = """
    <a href="/url?q=https%3A%2F%2Fexample.com%2Falpha&amp;sa=U">Alpha Result</a>
    <a href="/url?q=https%3A%2F%2Fexample.org%2Fbeta&amp;sa=U">Beta Result</a>
    """
    pages = {
        "https://example.com/alpha": """
        <html><head><title>Alpha Page</title><script>ignore()</script></head>
        <body><nav>menu</nav><main><h1>Alpha heading</h1>
        <p>Alpha useful paragraph with enough detail for extraction.</p>
        <p>Alpha useful paragraph with enough detail for extraction.</p></main></body></html>
        """,
        "https://example.org/beta": """
        <html><head><title>Beta Page</title></head>
        <body><article><p>Beta useful paragraph with distinct research details.</p></article></body></html>
        """,
    }

    def fake_http_get(url: str, timeout: int) -> str:
        if "google.com/search" in url or "bing.com/search" in url:
            return search_html
        return pages[url]

    fake_llm = FakeCleanupLLM()
    result = web_searcher_executor(
        {
            "query": "openpilot research",
            "max_results": 2,
            "max_pages": 2,
            "max_page_chars": 500,
            "_http_get": fake_http_get,
            "_llm_client": fake_llm,
        }
    )

    assert result["llm_cleanup"] is True
    assert [request.trace_info["task"] for request in fake_llm.requests] == ["keyword_generation", "cleanup"]
    assert fake_llm.requests[-1].response_format == "text"
    assert len(result["pages"]) == 2
    assert result["pages"][0]["title"] == "Alpha Page"
    assert result["pages"][0]["fetch_success"] is True
    assert result["pages"][0]["fetch_depth"] == 0
    assert result["pages"][0]["parent_url"] is None
    assert result["pages"][0]["selected_by_llm"] is False
    assert "menu" not in result["pages"][0]["content_excerpt"]
    assert "Alpha useful paragraph" in result["pages"][0]["content_excerpt"]
    assert "Clean summary" in result["research_summary"]
    assert result["key_points"] == ["Useful fact"]
    assert result["source_notes"] == [{"url": "https://example.com/alpha", "note": "Primary source"}]
    assert result["follow_up_queries"] == ["openpilot follow up"]


def test_web_searcher_max_pages_zero_skips_page_fetch_and_cleanup() -> None:
    html = '<a href="/url?q=https%3A%2F%2Fexample.com%2Fa&amp;sa=U">A</a>'

    def fake_http_get(url: str, timeout: int) -> str:
        assert "google.com/search" in url
        return html

    fake_llm = FakeCleanupLLM()
    result = web_searcher_executor(
        {
            "query": "openpilot",
            "max_pages": 0,
            "_http_get": fake_http_get,
            "_llm_client": fake_llm,
        }
    )

    assert result["count"] == 1
    assert result["pages"] == []
    assert result["llm_cleanup"] is False
    assert [request.trace_info["task"] for request in fake_llm.requests] == ["keyword_generation"]


def test_web_searcher_llm_cleanup_false_skips_llm() -> None:
    html = '<a href="/url?q=https%3A%2F%2Fexample.com%2Fa&amp;sa=U">A</a>'

    def fake_http_get(url: str, timeout: int) -> str:
        if "google.com/search" in url or "bing.com/search" in url:
            return html
        return "<html><body><p>Readable page content with useful details.</p></body></html>"

    fake_llm = FakeCleanupLLM()
    result = web_searcher_executor(
        {
            "query": "openpilot",
            "llm_cleanup": False,
            "_http_get": fake_http_get,
            "_llm_client": fake_llm,
        }
    )

    assert result["pages"][0]["fetch_success"] is True
    assert result["llm_cleanup"] is False
    assert result["research_summary"] == ""
    assert result["key_points"] == []
    assert [request.trace_info["task"] for request in fake_llm.requests] == ["keyword_generation"]


def test_web_searcher_page_fetch_failure_is_nonfatal() -> None:
    html = '<a href="/url?q=https%3A%2F%2Fexample.com%2Fa&amp;sa=U">A</a>'

    def fake_http_get(url: str, timeout: int) -> str:
        if "google.com/search" in url or "bing.com/search" in url:
            return html
        raise OSError("page down")

    result = web_searcher_executor(
        {
            "query": "openpilot",
            "llm_cleanup": False,
            "_http_get": fake_http_get,
        }
    )

    assert result["pages"][0]["fetch_success"] is False
    assert result["pages"][0]["error"] == "page down"
    assert any("Failed to fetch https://example.com/a" in warning for warning in result["warnings"])


def test_web_searcher_follows_llm_selected_page_links_without_network() -> None:
    search_html = '<a href="/url?q=https%3A%2F%2Fexample.com%2Fa&amp;sa=U">A</a>'
    pages = {
        "https://example.com/a": """
        <html><head><title>A</title></head><body>
        <main><p>Readable page content with useful details.</p>
        <a href="/detail">Official detail</a>
        <a href="mailto:test@example.com">Email</a>
        <a href="javascript:void(0)">Script</a>
        <a href="#comments">Comments</a>
        <a href="https://ads.example.net/ad">Ad</a>
        </main></body></html>
        """,
        "https://example.com/detail": """
        <html><head><title>Detail</title></head>
        <body><article><p>Deeper official detail with enough useful research content.</p></article></body></html>
        """,
    }

    def fake_http_get(url: str, timeout: int) -> str:
        if "google.com/search" in url or "bing.com/search" in url:
            return search_html
        return pages[url]

    fake_llm = FakeCleanupLLM(link_selection="L1 | adds official details")
    result = web_searcher_executor(
        {
            "query": "openpilot",
            "max_pages": 1,
            "llm_cleanup": False,
            "_http_get": fake_http_get,
            "_llm_client": fake_llm,
        }
    )

    assert [request.trace_info["task"] for request in fake_llm.requests] == ["keyword_generation", "link_selection"]
    assert fake_llm.requests[1].response_format == "text"
    assert len(result["pages"]) == 2
    assert result["pages"][1]["url"] == "https://example.com/detail"
    assert result["pages"][1]["fetch_depth"] == 1
    assert result["pages"][1]["parent_url"] == "https://example.com/a"
    assert result["pages"][1]["selected_by_llm"] is True
    assert result["pages"][1]["selection_reason"] == "adds official details"


def test_web_searcher_default_redirect_depth_stops_after_one_layer() -> None:
    search_html = '<a href="/url?q=https%3A%2F%2Fexample.com%2Fa&amp;sa=U">A</a>'
    pages = {
        "https://example.com/a": """
        <html><body><p>Readable page content with useful details.</p>
        <a href="/detail">Detail</a></body></html>
        """,
        "https://example.com/detail": """
        <html><body><p>Second layer content with useful details.</p>
        <a href="/third">Third layer</a></body></html>
        """,
        "https://example.com/third": "<html><body><p>Third layer should not be fetched by default.</p></body></html>",
    }

    def fake_http_get(url: str, timeout: int) -> str:
        if "google.com/search" in url or "bing.com/search" in url:
            return search_html
        return pages[url]

    fake_llm = FakeCleanupLLM(link_selection="L1 | useful next page")
    result = web_searcher_executor(
        {
            "query": "openpilot",
            "max_pages": 1,
            "llm_cleanup": False,
            "_http_get": fake_http_get,
            "_llm_client": fake_llm,
        }
    )

    assert [page["url"] for page in result["pages"]] == [
        "https://example.com/a",
        "https://example.com/detail",
    ]
    assert [request.trace_info["task"] for request in fake_llm.requests] == ["keyword_generation", "link_selection"]


def test_web_searcher_redirect_selection_failure_is_nonfatal() -> None:
    search_html = '<a href="/url?q=https%3A%2F%2Fexample.com%2Fa&amp;sa=U">A</a>'

    def fake_http_get(url: str, timeout: int) -> str:
        if "google.com/search" in url or "bing.com/search" in url:
            return search_html
        return """
        <html><body><p>Readable page content with useful details.</p>
        <a href="/detail">Detail</a></body></html>
        """

    fake_llm = FakeCleanupLLM(fail_tasks={"link_selection"})
    result = web_searcher_executor(
        {
            "query": "openpilot",
            "max_pages": 1,
            "llm_cleanup": False,
            "_http_get": fake_http_get,
            "_llm_client": fake_llm,
        }
    )

    assert len(result["pages"]) == 1
    assert any("LLM redirect selection failed" in warning for warning in result["warnings"])


def test_web_searcher_can_disable_redirect_following() -> None:
    search_html = '<a href="/url?q=https%3A%2F%2Fexample.com%2Fa&amp;sa=U">A</a>'

    def fake_http_get(url: str, timeout: int) -> str:
        if "google.com/search" in url or "bing.com/search" in url:
            return search_html
        return """
        <html><body><p>Readable page content with useful details.</p>
        <a href="/detail">Detail</a></body></html>
        """

    fake_llm = FakeCleanupLLM(link_selection="L1 | would be useful")
    disabled = web_searcher_executor(
        {
            "query": "openpilot",
            "max_pages": 1,
            "llm_cleanup": False,
            "follow_redirects": False,
            "_http_get": fake_http_get,
            "_llm_client": fake_llm,
        }
    )
    depth_zero = web_searcher_executor(
        {
            "query": "openpilot",
            "max_pages": 1,
            "llm_cleanup": False,
            "max_redirect_depth": 0,
            "_http_get": fake_http_get,
            "_llm_client": fake_llm,
        }
    )

    assert len(disabled["pages"]) == 1
    assert len(depth_zero["pages"]) == 1
    assert [request.trace_info["task"] for request in fake_llm.requests] == ["keyword_generation", "keyword_generation"]


def test_web_searcher_llm_cleanup_failure_is_reported() -> None:
    html = '<a href="/url?q=https%3A%2F%2Fexample.com%2Fa&amp;sa=U">A</a>'

    def fake_http_get(url: str, timeout: int) -> str:
        if "google.com/search" in url or "bing.com/search" in url:
            return html
        return "<html><body><p>Readable page content with useful details.</p></body></html>"

    try:
        web_searcher_executor(
            {
                "query": "openpilot",
                "_http_get": fake_http_get,
                "_llm_client": FakeCleanupLLM(fail=True),
            }
        )
    except RuntimeError as exc:
        assert "LLM cleanup failed" in str(exc)
    else:
        raise AssertionError("Expected LLM cleanup failure")


def test_web_searcher_cleanup_failure_can_return_raw_for_agent_generator() -> None:
    html = '<a href="/url?q=https%3A%2F%2Fexample.com%2Fa&amp;sa=U">A</a>'

    def fake_http_get(url: str, timeout: int) -> str:
        if "google.com/search" in url or "bing.com/search" in url:
            return html
        return "<html><body><p>Readable page content with useful details.</p></body></html>"

    fake_llm = FakeCleanupLLM(fail=True)
    result = web_searcher_executor(
        {
            "query": "openpilot",
            "_http_get": fake_http_get,
            "_llm_client": fake_llm,
            "_cleanup_failure_policy": "return_raw",
        }
    )

    assert result["llm_cleanup"] is False
    assert result["research_summary"] == ""
    assert result["key_points"] == []
    assert result["llm_cleanup_error"]
    assert any("LLM cleanup failed" in warning for warning in result["warnings"])
    assert [request.trace_info["task"] for request in fake_llm.requests] == ["keyword_generation", "cleanup"]


def test_web_searcher_search_failure_returns_attempt_diagnostics_with_cleanup_fallback_policy() -> None:
    def failing_http_get(url: str, timeout: int) -> str:
        raise OSError("network down")

    result = web_searcher_executor(
        {
            "query": "openpilot",
            "_http_get": failing_http_get,
            "_cleanup_failure_policy": "return_raw",
        }
    )

    assert result["count"] == 0
    assert result["search_attempts"]
    assert all(attempt["status"] == "network_error" for attempt in result["search_attempts"])
    assert any("No search results found" in warning for warning in result["warnings"])


def test_agent_generator_collect_data_raises_on_unusable_network_search(monkeypatch) -> None:
    def fake_web_searcher(params):
        params = params.to_params()
        return {
            "query": params["query"],
            "provider": "bing_html",
            "effective_query": "",
            "search_attempts": [
                {
                    "provider": "google_html",
                    "query": params["query"],
                    "result_count": 0,
                    "status": "network_error",
                    "error": "SSL EOF. Network/proxy failure while contacting Google/Bing.",
                },
                {
                    "provider": "bing_html",
                    "query": params["query"],
                    "result_count": 0,
                    "status": "blocked",
                    "error": "Search provider returned HTTP 429",
                },
            ],
            "network_diagnostics": {
                "proxy_env_vars": [],
                "proxy_env_detected": False,
                "http_client": "httpx",
                "trust_env": True,
                "follow_redirects": True,
                "dotenv_loaded": False,
            },
            "results": [],
            "count": 0,
            "pages": [],
            "llm_cleanup": False,
            "research_summary": "",
            "key_points": [],
            "source_notes": [],
            "follow_up_queries": [],
            "warnings": [],
        }

    monkeypatch.setattr("agent_generator.data_collector.web_searcher_executor", fake_web_searcher)
    slots = [Slot(name="topic", kind="constraint", description="topic", value="machine learning", required=False)]

    with pytest.raises(RuntimeError) as exc_info:
        collect_data("research machine learning", slots, llm_client=None)

    message = str(exc_info.value)
    assert "network, proxy" in message
    assert "No HTTP_PROXY/HTTPS_PROXY/ALL_PROXY" in message
    assert "google_html" in message
    assert "bing_html" in message


def test_agent_generator_collect_data_keeps_true_empty_search_artifact(monkeypatch) -> None:
    def fake_web_searcher(params):
        params = params.to_params()
        return {
            "query": params["query"],
            "provider": "bing_html",
            "effective_query": "",
            "search_attempts": [
                {
                    "provider": "google_html",
                    "query": params["query"],
                    "result_count": 0,
                    "status": "empty",
                    "error": "No search results parsed from provider response",
                }
            ],
            "network_diagnostics": {"proxy_env_vars": [], "proxy_env_detected": False},
            "results": [],
            "count": 0,
            "pages": [],
            "llm_cleanup": False,
            "research_summary": "",
            "key_points": [],
            "source_notes": [],
            "follow_up_queries": [],
            "warnings": ["No search results found after trying Google/Bing query variants."],
        }

    monkeypatch.setattr("agent_generator.data_collector.web_searcher_executor", fake_web_searcher)
    slots = [Slot(name="topic", kind="constraint", description="topic", value="unlikely topic", required=False)]

    artifacts, _pipeline = collect_data("research unlikely topic", slots, llm_client=None)

    assert len(artifacts) == 1
    assert artifacts[0].confidence == 0.45
    assert artifacts[0].content.artifact.get("search_attempts")[0]["status"] == "empty"


def test_agent_generator_collect_data_returns_web_artifact_when_cleanup_fails(monkeypatch) -> None:
    def fake_web_searcher(params):
        params = params.to_params()
        assert params["_cleanup_failure_policy"] == "return_raw"
        return {
            "query": params["query"],
            "provider": "google_html",
            "results": [
                {
                    "rank": 1,
                    "title": "A",
                    "url": "https://example.com/a",
                    "snippet": "A snippet",
                    "source_domain": "example.com",
                }
            ],
            "count": 1,
            "pages": [
                {
                    "url": "https://example.com/a",
                    "title": "A",
                    "source_domain": "example.com",
                    "content_excerpt": "Readable page content with useful details.",
                    "content_chars": 42,
                    "fetch_success": True,
                    "error": None,
                }
            ],
            "llm_cleanup": False,
            "research_summary": "",
            "key_points": [],
            "source_notes": [],
            "follow_up_queries": [],
            "warnings": [
                "LLM cleanup failed, so Agent Generator continued with raw web search results. "
                "Reason: LLM cleanup failed: Failed to parse JSON (attempt 3/3)."
            ],
            "llm_cleanup_error": "LLM cleanup failed: Failed to parse JSON (attempt 3/3)",
        }

    monkeypatch.setattr("agent_generator.data_collector.web_searcher_executor", fake_web_searcher)
    logger = FakeStructuredLogger()
    slots = [Slot(name="topic", kind="constraint", description="topic", value="machine learning", required=False)]

    artifacts, pipeline = collect_data("research machine learning", slots, llm_client=FakeCleanupLLM(), logger=logger)

    assert len(artifacts) == 1
    output = artifacts[0].content.artifact
    assert output.get("llm_cleanup") is False
    assert output.results[0]["url"] == "https://example.com/a"
    step_params = pipeline.steps[0].parameters
    assert step_params["llm_cleanup_requested"] is True
    assert step_params["llm_cleanup_executed"] is False
    assert "LLM cleanup failed" in step_params["cleanup_fallback_warning"]
    assert step_params["produced_artifact_ids"] == [artifacts[0].id]

    rendered = StringIO()
    present_data(artifacts, Console(file=rendered, width=120, force_terminal=False))
    text = rendered.getvalue()
    assert "LLM cleanup failed" in text
    assert "https://example.com/a" in text
    assert "Readable page content" in text

    assert any(event["source_type"] == "agent_generator" for event in logger.events)
    assert any(event["event_type"] == "warning" and event["phase"] == "web_cleanup" for event in logger.events)
    assert "_llm_client" not in json.dumps(logger.events, default=str)


def test_embedder_uses_injected_service_without_network() -> None:
    class FakeEmbeddingService:
        provider = "fake"
        model = "fake-embedding"

        def embed_text(self, text: str, use_cache: bool = True) -> list[float]:
            assert text == "hello semantic world"
            assert use_cache is False
            return [0.1, 0.2, 0.3]

    registry = _registered_registry()
    executor = ToolExecutor(registry)
    try:
        result = executor.execute_single(
            ToolSelection(
                step_id="embed-query",
                tool_name="embedder",
                reason="capability_match",
                input_metadata={
                    "query": "hello semantic world",
                    "use_cache": False,
                    "_embedding_service": FakeEmbeddingService(),
                },
            )
        )
    finally:
        executor.shutdown()

    assert result.success
    assert result.output_metadata.result["embedding"] == [0.1, 0.2, 0.3]
    assert result.output_metadata.result["dimension"] == 3
    assert result.output_metadata.result["model"] == "fake-embedding"
    assert result.output_metadata.result["provider"] == "fake"
    assert result.output_metadata.result["cached"] is False


def test_embedding_service_uses_embedding_settings_and_cache_namespace(tmp_path, monkeypatch) -> None:
    class FakeOpenAI:
        def __init__(self, *, api_key, base_url=None, timeout=None):
            self.api_key = api_key
            self.base_url = base_url
            self.timeout = timeout
            self.embeddings = SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    data=[SimpleNamespace(embedding=[0.4, 0.5, 0.6])]
                )
            )

    monkeypatch.setattr("core.embedding.OpenAI", FakeOpenAI)
    from core.config import EmbeddingSettings
    from core.embedding import EmbeddingService

    settings = EmbeddingSettings(
        OPENPILOT_EMBEDDING_BASE_URL="https://embed.example/v1",
        OPENPILOT_EMBEDDING_API_KEY="embed-key",
        OPENPILOT_EMBEDDING_MODEL="embedding-special",
        OPENPILOT_EMBEDDING_TIMEOUT_SECONDS=12,
    )
    service = EmbeddingService(settings=settings, cache_dir=tmp_path)

    assert service.model == "embedding-special"
    assert service.base_url == "https://embed.example/v1"
    assert service.timeout == 12
    assert service.client.base_url == "https://embed.example/v1"
    assert service._get_cache_key("same text") != EmbeddingService(
        settings=EmbeddingSettings(
            OPENPILOT_EMBEDDING_BASE_URL="https://other.example/v1",
            OPENPILOT_EMBEDDING_API_KEY="embed-key",
            OPENPILOT_EMBEDDING_MODEL="embedding-special",
        ),
        cache_dir=tmp_path / "other",
    )._get_cache_key("same text")


def test_embedder_uses_configured_embedding_model_by_default(tmp_path, monkeypatch) -> None:
    created: dict[str, str] = {}

    class FakeEmbeddingService:
        provider = "openai-compatible"

        def __init__(self, *, provider, model, base_url=None, settings=None):
            created["provider"] = provider
            created["model"] = model
            created["base_url"] = base_url or ""
            self.provider = provider
            self.model = model

        def embed_text(self, text: str, use_cache: bool = True) -> list[float]:
            return [0.7, 0.8]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENPILOT_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("OPENPILOT_LLM_API_KEY", "llm-key")
    monkeypatch.delenv("OPENPILOT_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("OPENPILOT_EMBEDDING_API_KEY", raising=False)
    monkeypatch.setenv("OPENPILOT_EMBEDDING_MODEL", "configured-embedding")
    monkeypatch.setattr("core.embedding.EmbeddingService", FakeEmbeddingService)

    result = _registered_registry().get_executor("embedder")(
        ToolInputMetadata.from_mapping("embedder", {"query": "semantic text"})
    )

    assert result.result["model"] == "configured-embedding"
    assert created["model"] == "configured-embedding"
    assert created["base_url"] == "https://llm.example/v1"


def test_embedder_explicit_model_overrides_embedding_settings(tmp_path, monkeypatch) -> None:
    created: dict[str, str] = {}

    class FakeEmbeddingService:
        def __init__(self, *, provider, model, base_url=None, settings=None):
            created["model"] = model
            self.provider = provider
            self.model = model

        def embed_text(self, text: str, use_cache: bool = True) -> list[float]:
            return [0.9]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENPILOT_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("OPENPILOT_LLM_API_KEY", "llm-key")
    monkeypatch.setenv("OPENPILOT_EMBEDDING_MODEL", "configured-embedding")
    monkeypatch.setattr("core.embedding.EmbeddingService", FakeEmbeddingService)

    result = _registered_registry().get_executor("embedder")(
        ToolInputMetadata.from_mapping("embedder", {"query": "semantic text", "model": "explicit-embedding"})
    )

    assert result.result["model"] == "explicit-embedding"
    assert created["model"] == "explicit-embedding"


def test_logger_writes_legacy_and_structured_jsonl(tmp_path) -> None:
    log_file = tmp_path / "openpilot.jsonl"
    logger = OpenPilotLogger(log_file)

    logger.log_event(
        "legacy_event",
        {"message": "ok"},
        session_id="session-1",
        turn_id=1,
    )
    logger.log_structured_event(
        source_type="tool",
        source_name="file_reader",
        phase="pre_execution",
        event_type="structured_event",
        session_id="session-1",
        turn_id=2,
        success=True,
        duration_ms=3,
        input_summary={"file_path": "demo.txt"},
        output_summary={"size_bytes": 4},
        annotations={"contract": "phase1"},
    )

    events = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
    ]

    assert [event["event_type"] for event in events] == [
        "legacy_event",
        "structured_event",
    ]
    assert events[0]["level"] == "INFO"
    assert events[1]["level"] == "INFO"
    assert events[1]["payload"]["level"] == "INFO"
    assert events[0]["payload"] == {"message": "ok"}
    assert events[1]["payload"]["source_type"] == "tool"
    assert events[1]["payload"]["source_name"] == "file_reader"
    assert events[1]["payload"]["annotations"] == {"contract": "phase1"}


def test_logger_infers_warning_and_error_levels(tmp_path) -> None:
    log_file = tmp_path / "openpilot.jsonl"
    logger = OpenPilotLogger(log_file)

    logger.log_structured_event(
        source_type="agent",
        source_name="tool_loop",
        phase="task_execution",
        event_type="tool_loop_recoverable_error",
        session_id="session-1",
        turn_id=1,
        success=False,
        error="missing input",
    )
    logger.log_event(
        "task_failed",
        {"success": False, "error": "boom"},
        session_id="session-1",
        turn_id=1,
    )

    events = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
    ]

    assert events[0]["level"] == "WARNING"
    assert events[0]["payload"]["level"] == "WARNING"
    assert events[1]["level"] == "ERROR"
