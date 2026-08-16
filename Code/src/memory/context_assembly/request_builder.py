"""Build provider-neutral LLM requests from typed assembled candidates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from core.llm import LLMMessage, LLMRequest, LLMToolDefinition, render_llm_tools
from core.exceptions import ContextAssemblyBudgetError, ContextAssemblyGovernanceError
from core.token_counting import ProviderTokenCounter
from memory.context_assembly.assembler import ContextAssembler
from metadata import (
    ContextAssemblyPolicy,
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextCandidate,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTruncation,
    ContextRequestPurpose,
    ReasoningPolicy,
)


DEFAULT_MAX_PROMPT_CHARS = 16_000
DEFAULT_MAX_PROMPT_TOKENS = 4_096
DEFAULT_RESERVED_PROMPT_TOKENS = 128

CONTEXT_REQUEST_MIGRATION_REGISTRY = {
    ContextRequestPurpose.SLOT_GENERATION.value: ("agent_generator.slot_generator", "3C"),
    ContextRequestPurpose.SLOT_LANGUAGE_REPAIR.value: ("agent_generator.slot_generator", "3C"),
    ContextRequestPurpose.SEMANTIC_GOAL.value: ("core.semantic_analyzer", "3A"),
    ContextRequestPurpose.SEMANTIC_PLAN_STEP.value: ("core.semantic_analyzer", "3A"),
    ContextRequestPurpose.TOOL_EVENT_DECISION.value: ("core.tool_event_loop", "3A"),
    ContextRequestPurpose.TOOL_PLAN_RETRY.value: ("autonomous_iteration.tool_planning_executor", "3A"),
    ContextRequestPurpose.TASK_COMPLEXITY.value: ("autonomous_iteration.execution_task_decomposer", "3A"),
    ContextRequestPurpose.TASK_DECOMPOSITION.value: ("autonomous_iteration.execution_task_decomposer", "3A"),
    ContextRequestPurpose.ITERATION_GOAL.value: ("autonomous_iteration.iteration_agent", "3A"),
    ContextRequestPurpose.ITERATION_TASK_DESIGN.value: ("autonomous_iteration.iteration_agent", "3A"),
    ContextRequestPurpose.PROJECT_IMPROVEMENT.value: ("autonomous_iteration.project_improvement_tool", "3A"),
    ContextRequestPurpose.RUNTIME_OUTPUT_EVALUATION.value: ("autonomous_iteration.project_evaluator", "3A"),
    ContextRequestPurpose.CODE_GENERATION.value: ("tools.code_generator", "3B"),
    ContextRequestPurpose.TEXT_FILE_GENERATION.value: ("autonomous_iteration.task_executor", "3B"),
    ContextRequestPurpose.CODE_UNIT_GENERATION.value: ("tools.code_unit_generator", "3B"),
    ContextRequestPurpose.CODE_EDIT.value: ("tools.code_editor", "3B"),
    ContextRequestPurpose.BUG_FIX.value: ("tools.bug_fix_tool", "3B"),
    ContextRequestPurpose.MEMORY_COMPRESSION.value: ("memory.context_compressor", "3C"),
    ContextRequestPurpose.TEXT_SUMMARIZATION.value: ("tools.llm_summarizer", "3C"),
    ContextRequestPurpose.WEB_QUERY_GENERATION.value: ("tools.web_searcher", "3C"),
    ContextRequestPurpose.WEB_LINK_SELECTION.value: ("tools.web_searcher", "3C"),
    ContextRequestPurpose.WEB_CLEANUP.value: ("tools.web_searcher", "3C"),
}


@dataclass(frozen=True)
class PreparedContextRequest:
    """Assembly evidence plus an executable request only when it is ready."""

    assembly: ContextAssemblyResult
    request: LLMRequest | None

    def require_request(self) -> LLMRequest:
        if self.request is None:
            if (
                self.assembly.selection.assembly_status
                == ContextAssemblyStatus.GOVERNANCE_BLOCKED
            ):
                raise ContextAssemblyGovernanceError(
                    self.assembly.selection.governance_blocked_candidate_ids
                )
            raise ContextAssemblyBudgetError(
                self.assembly.selection.omitted_required_candidate_ids
            )
        return self.request


class ContextRequestBuilder:
    """Preserve candidate roles while attaching one selection evidence record."""

    def __init__(self, assembler: ContextAssembler) -> None:
        self.assembler = assembler

    def build(
        self,
        candidates: list[ContextCandidate],
        *,
        policy: ContextAssemblyPolicy,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
        transport_retries: int | None = None,
        trace_info: dict[str, Any] | None = None,
        reasoning_policy: ReasoningPolicy | None = None,
        structured_messages: list[LLMMessage] | None = None,
    ) -> PreparedContextRequest:
        return self._build(
            candidates,
            policy=policy,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            transport_retries=transport_retries,
            trace_info=trace_info,
            reasoning_policy=reasoning_policy,
            expose_candidate_ids=True,
            structured_messages=structured_messages,
        )

    def _build(
        self,
        candidates: list[ContextCandidate],
        *,
        policy: ContextAssemblyPolicy,
        response_format: Literal["text", "json_object"],
        temperature: float | None,
        max_tokens: int | None,
        timeout_seconds: float | None,
        transport_retries: int | None,
        trace_info: dict[str, Any] | None,
        reasoning_policy: ReasoningPolicy | None,
        expose_candidate_ids: bool,
        structured_messages: list[LLMMessage] | None = None,
    ) -> PreparedContextRequest:
        renderer = (
            self._render_message_content
            if expose_candidate_ids
            else self._render_raw_message_content
        )
        assembly = self.assembler.assemble_candidates(
            candidates,
            policy=policy,
            renderer=renderer,
        )
        if assembly.selection.assembly_status != ContextAssemblyStatus.READY:
            return PreparedContextRequest(assembly=assembly, request=None)

        original_by_id = {}
        if structured_messages is not None:
            original_by_id = {
                candidate.candidate_id: message
                for candidate, message in zip(candidates, structured_messages, strict=True)
            }
        request_messages = []
        for candidate in assembly.selected_candidates:
            original = original_by_id.get(candidate.candidate_id)
            if original is not None:
                round_trip = original.role == "tool" or bool(original.tool_calls)
                request_messages.append(
                    original.model_copy(
                        update={"content": original.content if round_trip else candidate.content}
                    )
                )
            else:
                request_messages.append(
                    LLMMessage(
                        role=candidate.role,
                        content=(
                            self._render_candidate_content(candidate)
                            if expose_candidate_ids
                            else candidate.content
                        ),
                    )
                )
        request_trace_info = dict(trace_info or {})
        request_trace_info.setdefault(
            "selected_candidate_ids",
            [candidate.candidate_id for candidate in assembly.selected_candidates],
        )
        request = LLMRequest(
            messages=request_messages,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            transport_retries=transport_retries,
            trace_info=request_trace_info,
            context_selection=assembly.selection,
            reasoning_policy=reasoning_policy or ReasoningPolicy(),
        )
        return PreparedContextRequest(assembly=assembly, request=request)

    def build_messages(
        self,
        messages: list[LLMMessage],
        *,
        purpose: ContextRequestPurpose,
        policy: ContextAssemblyPolicy,
        user_truncation: ContextCandidateTruncation = ContextCandidateTruncation.HEAD,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
        transport_retries: int | None = None,
        trace_info: dict[str, Any] | None = None,
        reasoning_policy: ReasoningPolicy | None = None,
    ) -> PreparedContextRequest:
        if policy.purpose != purpose:
            raise ValueError("context assembly policy purpose does not match request purpose")
        candidates = [
            self._message_candidate(
                message,
                purpose=purpose,
                source_order=index,
                user_truncation=user_truncation,
            )
            for index, message in enumerate(messages)
        ]
        return self._build(
            candidates,
            policy=policy,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            transport_retries=transport_retries,
            trace_info=trace_info,
            reasoning_policy=reasoning_policy,
            expose_candidate_ids=False,
            structured_messages=messages,
        )

    @staticmethod
    def _render_message_content(candidates: list[ContextCandidate]) -> str:
        return "\n\n".join(
            ContextRequestBuilder._render_candidate_content(candidate)
            for candidate in candidates
        )

    @staticmethod
    def _render_raw_message_content(candidates: list[ContextCandidate]) -> str:
        return "\n\n".join(candidate.content for candidate in candidates)

    @staticmethod
    def _render_candidate_content(candidate: ContextCandidate) -> str:
        encoded_id = json.dumps(candidate.candidate_id, ensure_ascii=False)
        return f"[evidence_id={encoded_id}]\n{candidate.content}"

    @staticmethod
    def _message_candidate(
        message: LLMMessage,
        *,
        purpose: ContextRequestPurpose,
        source_order: int,
        user_truncation: ContextCandidateTruncation,
    ) -> ContextCandidate:
        kind = {
            "system": ContextCandidateKind.INSTRUCTION,
            "user": ContextCandidateKind.USER_INPUT,
            "assistant": ContextCandidateKind.PREVIOUS_OUTPUT,
            "tool": ContextCandidateKind.RUNTIME_EVIDENCE,
        }[message.role]
        truncation = {
            "system": ContextCandidateTruncation.FORBIDDEN,
            "user": user_truncation,
            "assistant": ContextCandidateTruncation.TAIL,
            "tool": ContextCandidateTruncation.FORBIDDEN,
        }[message.role]
        round_trip_message = message.role == "tool" or bool(message.tool_calls)
        content = message.content
        if round_trip_message:
            content = json.dumps(
                {
                    "role": message.role,
                    "content": message.content,
                    "reasoning_content": message.reasoning_content,
                    "tool_calls": [
                        call.model_dump(mode="json", exclude_none=True)
                        for call in message.tool_calls
                    ],
                    "tool_call_id": message.tool_call_id,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        if not content.strip():
            content = "[empty provider tool-call turn]"
        return ContextCandidate(
            candidate_id=f"{purpose.value}:message:{source_order + 1}",
            kind=kind,
            source_id=f"message:{source_order + 1}",
            content=content,
            role=message.role,
            retention=ContextCandidateRetention.REQUIRED,
            priority=100 - min(source_order, 20),
            source_order=source_order,
            truncation=ContextCandidateTruncation.FORBIDDEN if round_trip_message else truncation,
        )


def build_context_llm_request(
    llm_client: Any,
    *,
    messages: list[LLMMessage],
    purpose: ContextRequestPurpose,
    context_max_prompt_tokens: int | None = None,
    response_format: Literal["text", "json_object"] = "text",
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout_seconds: float | None = None,
    transport_retries: int | None = None,
    trace_info: dict[str, Any] | None = None,
    user_truncation: ContextCandidateTruncation = ContextCandidateTruncation.HEAD,
    reasoning_policy: ReasoningPolicy | None = None,
    tools: list[LLMToolDefinition] | None = None,
    tool_choice: Literal["auto", "none", "required"] | None = None,
) -> LLMRequest:
    """Bind one existing message call to its provider-aware assembly policy."""
    settings = getattr(llm_client, "settings", None)
    counter = ProviderTokenCounter.from_settings(settings)
    requested_tokens = int(
        context_max_prompt_tokens
        if context_max_prompt_tokens is not None
        else getattr(settings, "context_max_prompt_tokens", DEFAULT_MAX_PROMPT_TOKENS)
        or DEFAULT_MAX_PROMPT_TOKENS
    )
    if requested_tokens < 1:
        raise ValueError("context_max_prompt_tokens must be positive")
    reserved_tokens = int(
        getattr(settings, "context_reserved_prompt_tokens", DEFAULT_RESERVED_PROMPT_TOKENS)
        or 0
    )
    reserved_tokens = min(reserved_tokens, max(0, requested_tokens - 1))
    provider_tools = list(tools or [])
    tool_schema_tokens = 0
    if provider_tools:
        if not counter.available:
            raise ContextAssemblyBudgetError(["provider_tools"])
        tool_schema_tokens = counter.count_text(
            json.dumps(render_llm_tools(provider_tools), ensure_ascii=False, separators=(",", ":"))
        )
        available_context_tokens = requested_tokens - tool_schema_tokens
        if available_context_tokens <= reserved_tokens:
            raise ContextAssemblyBudgetError(["provider_tools"])
        requested_tokens = available_context_tokens
    assembler = ContextAssembler(renderer=lambda _payload: "", token_counter=counter)
    prepared = ContextRequestBuilder(assembler).build_messages(
        messages,
        purpose=purpose,
        policy=ContextAssemblyPolicy(
            purpose=purpose,
            max_prompt_chars=DEFAULT_MAX_PROMPT_CHARS,
            max_prompt_tokens=requested_tokens,
            reserved_prompt_tokens=reserved_tokens,
        ),
        user_truncation=user_truncation,
        response_format=response_format,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        transport_retries=transport_retries,
        trace_info={
            **dict(trace_info or {}),
            "context_purpose": purpose.value,
            **({"provider_tool_schema_tokens": tool_schema_tokens} if provider_tools else {}),
        },
        reasoning_policy=reasoning_policy,
    )
    request = prepared.require_request()
    return request.model_copy(update={"tools": provider_tools, "tool_choice": tool_choice})


def build_context_candidate_request(
    llm_client: Any,
    *,
    candidates: list[ContextCandidate],
    purpose: ContextRequestPurpose,
    response_format: Literal["text", "json_object"] = "text",
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout_seconds: float | None = None,
    transport_retries: int | None = None,
    trace_info: dict[str, Any] | None = None,
    reasoning_policy: ReasoningPolicy | None = None,
    tools: list[LLMToolDefinition] | None = None,
    tool_choice: Literal["auto", "none", "required"] | None = None,
    structured_messages: list[LLMMessage] | None = None,
) -> LLMRequest:
    """Build one provider-aware request from owner-projected typed candidates."""
    settings = getattr(llm_client, "settings", None)
    counter = ProviderTokenCounter.from_settings(settings)
    requested_tokens = int(
        getattr(settings, "context_max_prompt_tokens", DEFAULT_MAX_PROMPT_TOKENS)
        or DEFAULT_MAX_PROMPT_TOKENS
    )
    reserved_tokens = int(
        getattr(settings, "context_reserved_prompt_tokens", DEFAULT_RESERVED_PROMPT_TOKENS)
        or 0
    )
    reserved_tokens = min(reserved_tokens, max(0, requested_tokens - 1))
    provider_tools = list(tools or [])
    tool_schema_tokens = 0
    if provider_tools:
        if not counter.available:
            raise ContextAssemblyBudgetError(["provider_tools"])
        tool_schema_tokens = counter.count_text(
            json.dumps(render_llm_tools(provider_tools), ensure_ascii=False, separators=(",", ":"))
        )
        requested_tokens -= tool_schema_tokens
        if requested_tokens <= reserved_tokens:
            raise ContextAssemblyBudgetError(["provider_tools"])
    prepared = ContextRequestBuilder(
        ContextAssembler(renderer=lambda _payload: "", token_counter=counter)
    ).build(
        candidates,
        policy=ContextAssemblyPolicy(
            purpose=purpose,
            max_prompt_chars=DEFAULT_MAX_PROMPT_CHARS,
            max_prompt_tokens=requested_tokens,
            reserved_prompt_tokens=reserved_tokens,
        ),
        response_format=response_format,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        transport_retries=transport_retries,
        trace_info={
            **dict(trace_info or {}),
            "context_purpose": purpose.value,
            **({"provider_tool_schema_tokens": tool_schema_tokens} if provider_tools else {}),
        },
        reasoning_policy=reasoning_policy,
        structured_messages=structured_messages,
    )
    return prepared.require_request().model_copy(
        update={"tools": provider_tools, "tool_choice": tool_choice}
    )
