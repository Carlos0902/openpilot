from __future__ import annotations

import pytest

from core.exceptions import ContextAssemblyGovernanceError
from core.llm import (
    LLMMessage,
    LLMToolCall,
    LLMToolDefinition,
    LLMToolFunction,
    LLMToolFunctionCall,
)
from memory.context_assembly import (
    CONTEXT_REQUEST_MIGRATION_REGISTRY,
    ContextAssembler,
    ContextRequestBuilder,
    build_context_candidate_request,
)
from metadata import (
    ContextAssemblyPolicy,
    ContextAssemblyStatus,
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ContextRequestPurpose,
)


class ExactCounter:
    available = True
    tokenizer_id = "exact-test-tokenizer"
    model = "test-model"

    @staticmethod
    def count_text(text: str) -> int:
        return len(text.encode("utf-8"))


def render(payload: dict) -> str:
    sections: list[str] = []
    if payload.get("system_prompt"):
        sections.append("SYSTEM:" + payload["system_prompt"])
    if payload.get("dialog_context"):
        sections.append(
            "DIALOG:" + "|".join(item["content"] for item in payload["dialog_context"])
        )
    if payload.get("related_files"):
        sections.append(
            "FILES:" + "|".join(item["description"] for item in payload["related_files"])
        )
    if payload.get("related_memories"):
        sections.append(
            "MEMORIES:" + "|".join(item["content"] for item in payload["related_memories"])
        )
    if payload.get("environment_context"):
        sections.append(
            "ENV:" + "|".join(item["content"] for item in payload["environment_context"])
        )
    return "\n".join(sections)


def payload() -> dict:
    return {
        "query": "inspect",
        "project_path": "/tmp/demo",
        "system_prompt": "keep-system",
        "dialog_context": [
            {"role": "user", "content": "old-" * 20, "timestamp": "1"},
            {"role": "assistant", "content": "new-" * 20, "timestamp": "2"},
        ],
        "related_files": [{"path": "a.py", "description": "file-" * 20}],
        "related_memories": [{"type": "project", "content": "memory-" * 20}],
        "environment_context": [{"content": "env-" * 20}],
    }


def test_context_assembler_applies_exact_token_budget_and_emits_existing_metadata() -> None:
    assembler = ContextAssembler(renderer=render, token_counter=ExactCounter())

    with pytest.warns(DeprecationWarning):
        result = assembler.assemble(
            payload(),
            max_prompt_chars=10_000,
            max_prompt_tokens=120,
        )

    selection = result["context_selection"]
    assert ExactCounter.count_text(result["prompt_text"]) <= 120
    assert selection["budget_unit"] == "tokens"
    assert selection["tokenizer_id"] == "exact-test-tokenizer"
    assert selection["model"] == "test-model"
    assert selection["final_prompt_tokens"] == ExactCounter.count_text(result["prompt_text"])
    assert result["dialog_context"][-1]["timestamp"] == "2"


def test_candidate_request_keeps_required_projection_and_accounts_for_tool_schema(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
        lambda _settings: ExactCounter(),
    )

    class Settings:
        context_max_prompt_tokens = 800
        context_reserved_prompt_tokens = 32

    class Client:
        settings = Settings()

    request = build_context_candidate_request(
        Client(),
        candidates=[
            ContextCandidate(
                candidate_id="task:required",
                kind=ContextCandidateKind.TASK,
                content="Inspect the declared file and answer from evidence.",
                retention=ContextCandidateRetention.REQUIRED,
                truncation=ContextCandidateTruncation.FORBIDDEN,
                trust=ContextCandidateTrust.AUTHORITATIVE,
            ),
            ContextCandidate(
                candidate_id="dialog:optional",
                kind=ContextCandidateKind.DIALOG,
                content="historical noise " * 100,
                retention=ContextCandidateRetention.OPTIONAL,
                truncation=ContextCandidateTruncation.HEAD,
                trust=ContextCandidateTrust.OBSERVED,
            ),
        ],
        purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
        tools=[
            LLMToolDefinition(
                function=LLMToolFunction(
                    name="file_reader",
                    description="Read one file",
                    parameters={"type": "object", "properties": {"file_path": {"type": "string"}}},
                )
            )
        ],
    )

    assert request.tools and request.tools[0].function.name == "file_reader"
    assert request.context_selection.request_purpose == ContextRequestPurpose.TOOL_EVENT_DECISION
    assert any("Inspect the declared file" in message.content for message in request.messages)
    assert request.trace_info["provider_tool_schema_tokens"] > 0


def test_context_assembler_is_deterministic_and_does_not_mutate_sources() -> None:
    source = payload()
    original = payload()
    assembler = ContextAssembler(renderer=render)

    with pytest.warns(DeprecationWarning):
        first = assembler.assemble(source, max_prompt_chars=256, max_prompt_tokens=100)
    with pytest.warns(DeprecationWarning):
        second = assembler.assemble(source, max_prompt_chars=256, max_prompt_tokens=100)

    assert first["prompt_text"] == second["prompt_text"]
    assert first["dialog_context"] == second["dialog_context"]
    assert first["related_files"] == second["related_files"]
    assert (
        first["context_selection"]["section_decisions"]
        == second["context_selection"]["section_decisions"]
    )
    assert source == original
    assert first["context_selection"]["budget_unit"] == "characters"
    assert first["context_selection"]["token_count_method"] == "unavailable"


def test_context_assembler_request_hash_includes_budget_and_tokenizer_identity() -> None:
    assembler = ContextAssembler(renderer=render, token_counter=ExactCounter())
    request = {
        "query": "inspect",
        "project_path": "/tmp/demo",
        "include_environment": True,
        "limit": 10,
        "system_prompt": "system",
    }

    first = assembler.request_hash(
        request,
        max_prompt_chars=1_000,
        max_prompt_tokens=100,
    )
    repeated = assembler.request_hash(
        dict(reversed(list(request.items()))),
        max_prompt_chars=1_000,
        max_prompt_tokens=100,
    )
    changed = assembler.request_hash(
        request,
        max_prompt_chars=1_000,
        max_prompt_tokens=99,
    )

    assert first == repeated
    assert first.startswith("sha256:")
    assert changed != first


def test_typed_candidates_use_retention_priority_and_preserve_source_render_order() -> None:
    assembler = ContextAssembler(renderer=render, token_counter=ExactCounter())
    candidates = [
        ContextCandidate(
            candidate_id="optional-old",
            kind=ContextCandidateKind.MEMORY,
            content="optional-" * 20,
            retention=ContextCandidateRetention.OPTIONAL,
            priority=10,
            source_order=0,
        ),
        ContextCandidate(
            candidate_id="required-task",
            kind=ContextCandidateKind.TASK,
            content="required task",
            retention=ContextCandidateRetention.REQUIRED,
            priority=90,
            source_order=1,
            truncation=ContextCandidateTruncation.FORBIDDEN,
        ),
        ContextCandidate(
            candidate_id="preferred-evidence",
            kind=ContextCandidateKind.RUNTIME_EVIDENCE,
            content="evidence-" * 20,
            retention=ContextCandidateRetention.PREFERRED,
            priority=80,
            source_order=2,
        ),
    ]

    result = assembler.assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(max_prompt_chars=10_000, max_prompt_tokens=80),
        renderer=lambda selected: "\n".join(item.content for item in selected),
    )

    assert result.selection.assembly_status == ContextAssemblyStatus.READY
    assert result.selected_candidates[0].candidate_id == "required-task"
    assert result.selection.final_prompt_tokens <= 80
    decisions = {item.candidate_id: item for item in result.selection.candidate_decisions}
    assert decisions["required-task"].action == "kept"
    assert decisions["optional-old"].action == "omitted"


def test_typed_candidates_report_required_forbidden_content_as_budget_insufficient() -> None:
    assembler = ContextAssembler(renderer=render)
    required = ContextCandidate(
        candidate_id="required-schema",
        kind=ContextCandidateKind.TOOL_SCHEMA,
        content="schema-" * 100,
        retention=ContextCandidateRetention.REQUIRED,
        truncation=ContextCandidateTruncation.FORBIDDEN,
    )

    result = assembler.assemble_candidates(
        [required],
        policy=ContextAssemblyPolicy(max_prompt_chars=64),
    )

    assert result.selection.assembly_status == ContextAssemblyStatus.BUDGET_INSUFFICIENT
    assert result.selection.omitted_required_candidate_ids == ["required-schema"]
    assert result.selected_candidates == []
    assert result.prompt_text == ""


def test_typed_candidate_tail_truncation_is_explicit_and_does_not_mutate_source() -> None:
    candidate = ContextCandidate(
        candidate_id="runtime-tail",
        kind=ContextCandidateKind.RUNTIME_EVIDENCE,
        content="old-prefix-" + "x" * 100 + "-latest-error",
        truncation=ContextCandidateTruncation.TAIL,
    )
    assembler = ContextAssembler(renderer=render)

    result = assembler.assemble_candidates(
        [candidate],
        policy=ContextAssemblyPolicy(max_prompt_chars=64),
    )

    assert result.prompt_text.endswith("-latest-error")
    assert result.prompt_text.startswith("...[truncated by context budget]")
    assert candidate.content.startswith("old-prefix-")
    assert result.selection.candidate_decisions[0].action == "partially_kept"


def test_context_request_builder_reserves_tokens_and_preserves_message_roles() -> None:
    assembler = ContextAssembler(renderer=render, token_counter=ExactCounter())
    builder = ContextRequestBuilder(assembler)
    candidates = [
        ContextCandidate(
            candidate_id="system",
            kind=ContextCandidateKind.INSTRUCTION,
            content="Follow the output contract.",
            role="system",
            retention=ContextCandidateRetention.REQUIRED,
            priority=100,
            source_order=0,
            truncation=ContextCandidateTruncation.FORBIDDEN,
        ),
        ContextCandidate(
            candidate_id="user",
            kind=ContextCandidateKind.USER_INPUT,
            content="用户上下文" * 80,
            role="user",
            source_order=1,
        ),
    ]

    prepared = builder.build(
        candidates,
        policy=ContextAssemblyPolicy(
            max_prompt_chars=10_000,
            max_prompt_tokens=160,
            reserved_prompt_tokens=20,
        ),
        response_format="json_object",
        max_tokens=200,
        trace_info={"purpose": "test"},
    )

    assert prepared.request is not None
    assert [message.role for message in prepared.request.messages] == ["system", "user"]
    selection = prepared.assembly.selection
    assert selection.requested_prompt_tokens == 160
    assert selection.reserved_prompt_tokens == 20
    assert selection.max_prompt_tokens == 140
    assert selection.final_prompt_tokens + selection.reserved_prompt_tokens <= 160
    assert selection.remaining_prompt_tokens == 140 - selection.final_prompt_tokens
    assert prepared.request.context_selection == selection


def test_context_request_builder_exposes_stable_evidence_ids_and_accounts_for_them() -> None:
    assembler = ContextAssembler(renderer=render, token_counter=ExactCounter())
    builder = ContextRequestBuilder(assembler)
    candidates = [
        ContextCandidate(
            candidate_id="iteration_task_design:validation",
            kind=ContextCandidateKind.RUNTIME_EVIDENCE,
            content="pytest -q",
            role="user",
            retention=ContextCandidateRetention.REQUIRED,
            truncation=ContextCandidateTruncation.FORBIDDEN,
        )
    ]

    prepared = builder.build(
        candidates,
        policy=ContextAssemblyPolicy(
            purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
            max_prompt_chars=1_000,
            max_prompt_tokens=1_000,
        ),
    )

    request = prepared.require_request()
    expected = (
        '[evidence_id="iteration_task_design:validation"]\n'
        "pytest -q"
    )
    assert request.messages[0].content == expected
    assert prepared.assembly.prompt_text == expected
    assert prepared.assembly.selection.final_prompt_chars == len(expected)
    assert prepared.assembly.selection.final_prompt_tokens == ExactCounter.count_text(expected)


def test_context_request_builder_keeps_adapted_message_content_unwrapped() -> None:
    assembler = ContextAssembler(renderer=render, token_counter=ExactCounter())
    builder = ContextRequestBuilder(assembler)

    prepared = builder.build_messages(
        [LLMMessage(role="user", content="Do not add an evidence wrapper.")],
        purpose=ContextRequestPurpose.SEMANTIC_GOAL,
        policy=ContextAssemblyPolicy(
            purpose=ContextRequestPurpose.SEMANTIC_GOAL,
            max_prompt_chars=1_000,
            max_prompt_tokens=1_000,
        ),
    )

    request = prepared.require_request()
    assert request.messages[0].content == "Do not add an evidence wrapper."
    assert prepared.assembly.prompt_text == "Do not add an evidence wrapper."


def test_context_request_builder_does_not_create_request_when_required_context_is_missing() -> None:
    assembler = ContextAssembler(renderer=render)
    builder = ContextRequestBuilder(assembler)
    candidate = ContextCandidate(
        candidate_id="required",
        kind=ContextCandidateKind.INSTRUCTION,
        content="required-" * 100,
        role="system",
        retention=ContextCandidateRetention.REQUIRED,
        truncation=ContextCandidateTruncation.FORBIDDEN,
    )

    prepared = builder.build(
        [candidate],
        policy=ContextAssemblyPolicy(max_prompt_chars=64),
    )

    assert prepared.request is None
    assert prepared.assembly.selection.assembly_status == ContextAssemblyStatus.BUDGET_INSUFFICIENT


def test_context_request_builder_explicitly_falls_back_without_tokenizer() -> None:
    assembler = ContextAssembler(renderer=render)
    builder = ContextRequestBuilder(assembler)
    candidate = ContextCandidate(
        candidate_id="user",
        kind=ContextCandidateKind.USER_INPUT,
        content="short request",
    )

    prepared = builder.build(
        [candidate],
        policy=ContextAssemblyPolicy(
            max_prompt_chars=256,
            max_prompt_tokens=100,
            reserved_prompt_tokens=10,
        ),
    )

    assert prepared.request is not None
    assert prepared.assembly.selection.budget_unit == "characters"
    assert prepared.assembly.selection.requested_prompt_tokens is None
    assert prepared.assembly.selection.reserved_prompt_tokens == 0


def test_typed_context_assembler_rejects_duplicate_candidate_identity() -> None:
    assembler = ContextAssembler(renderer=render)
    duplicate = ContextCandidate(
        candidate_id="same",
        kind=ContextCandidateKind.TASK,
        content="task",
    )

    try:
        assembler.assemble_candidates(
            [duplicate, duplicate.model_copy(deep=True)],
            policy=ContextAssemblyPolicy(max_prompt_chars=256),
        )
    except ValueError as exc:
        assert "IDs must be unique" in str(exc)
    else:
        raise AssertionError("duplicate context candidates must be rejected")


def test_typed_context_assembler_deduplicates_normalized_exact_content() -> None:
    assembler = ContextAssembler(renderer=render)
    candidates = [
        ContextCandidate(
            candidate_id="memory-low",
            kind=ContextCandidateKind.MEMORY,
            content="Preserve   the dashboard route.",
            priority=40,
            source_order=0,
            trust=ContextCandidateTrust.RETRIEVED,
        ),
        ContextCandidate(
            candidate_id="memory-high",
            kind=ContextCandidateKind.MEMORY,
            content="Preserve the dashboard route.",
            priority=80,
            source_order=1,
            trust=ContextCandidateTrust.RETRIEVED,
        ),
    ]

    result = assembler.assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(max_prompt_chars=1_000),
    )

    assert [item.candidate_id for item in result.selected_candidates] == ["memory-high"]
    decisions = {item.candidate_id: item for item in result.selection.candidate_decisions}
    assert decisions["memory-low"].reason == "duplicate"
    assert decisions["memory-low"].governed_by_candidate_id == "memory-high"


def test_typed_context_assembler_preserves_duplicate_tool_results_by_wire_identity() -> None:
    assembler = ContextAssembler(renderer=render)
    candidates = [
        ContextCandidate(
            candidate_id="tool-result-1",
            kind=ContextCandidateKind.RUNTIME_EVIDENCE,
            content='{"success":false,"error_type":"ProviderToolDuplicateAttempt"}',
            role="tool",
            retention=ContextCandidateRetention.REQUIRED,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            source_order=0,
        ),
        ContextCandidate(
            candidate_id="tool-result-2",
            kind=ContextCandidateKind.RUNTIME_EVIDENCE,
            content='{"success":false,"error_type":"ProviderToolDuplicateAttempt"}',
            role="tool",
            retention=ContextCandidateRetention.REQUIRED,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            source_order=1,
        ),
    ]

    result = assembler.assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(max_prompt_chars=1_000),
    )

    assert [item.candidate_id for item in result.selected_candidates] == [
        "tool-result-1",
        "tool-result-2",
    ]
    assert result.selection.assembly_status == ContextAssemblyStatus.READY


def test_typed_context_assembler_preserves_duplicate_empty_assistant_tool_turns() -> None:
    assembler = ContextAssembler(renderer=render)
    candidates = [
        ContextCandidate(
            candidate_id="assistant-turn-1",
            kind=ContextCandidateKind.PREVIOUS_OUTPUT,
            content="[empty provider tool-call turn]",
            role="assistant",
            retention=ContextCandidateRetention.REQUIRED,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            source_order=0,
        ),
        ContextCandidate(
            candidate_id="tool-result-1",
            kind=ContextCandidateKind.RUNTIME_EVIDENCE,
            content="first result",
            role="tool",
            retention=ContextCandidateRetention.REQUIRED,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            source_order=1,
        ),
        ContextCandidate(
            candidate_id="assistant-turn-2",
            kind=ContextCandidateKind.PREVIOUS_OUTPUT,
            content="[empty provider tool-call turn]",
            role="assistant",
            retention=ContextCandidateRetention.REQUIRED,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            source_order=2,
        ),
        ContextCandidate(
            candidate_id="tool-result-2",
            kind=ContextCandidateKind.RUNTIME_EVIDENCE,
            content="second result",
            role="tool",
            retention=ContextCandidateRetention.REQUIRED,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            source_order=3,
        ),
    ]

    result = assembler.assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(max_prompt_chars=1_000),
    )

    assert [item.candidate_id for item in result.selected_candidates] == [
        "assistant-turn-1",
        "tool-result-1",
        "assistant-turn-2",
        "tool-result-2",
    ]


def test_context_request_builder_keeps_duplicate_assistant_tool_wire_sequence() -> None:
    candidates = [
        ContextCandidate(
            candidate_id="assistant-turn-1",
            kind=ContextCandidateKind.PREVIOUS_OUTPUT,
            content="[empty provider tool-call turn]",
            role="assistant",
            retention=ContextCandidateRetention.REQUIRED,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            source_order=0,
        ),
        ContextCandidate(
            candidate_id="tool-result-1",
            kind=ContextCandidateKind.RUNTIME_EVIDENCE,
            content="first result",
            role="tool",
            retention=ContextCandidateRetention.REQUIRED,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            source_order=1,
        ),
        ContextCandidate(
            candidate_id="assistant-turn-2",
            kind=ContextCandidateKind.PREVIOUS_OUTPUT,
            content="[empty provider tool-call turn]",
            role="assistant",
            retention=ContextCandidateRetention.REQUIRED,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            source_order=2,
        ),
        ContextCandidate(
            candidate_id="tool-result-2",
            kind=ContextCandidateKind.RUNTIME_EVIDENCE,
            content="second result",
            role="tool",
            retention=ContextCandidateRetention.REQUIRED,
            truncation=ContextCandidateTruncation.FORBIDDEN,
            source_order=3,
        ),
    ]
    structured = [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                LLMToolCall(
                    id="call-1",
                    function=LLMToolFunctionCall(name="file_reader", arguments="{}"),
                )
            ],
        ),
        LLMMessage(role="tool", content="first result", tool_call_id="call-1"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                LLMToolCall(
                    id="call-2",
                    function=LLMToolFunctionCall(name="file_reader", arguments="{}"),
                )
            ],
        ),
        LLMMessage(role="tool", content="second result", tool_call_id="call-2"),
    ]
    request = ContextRequestBuilder(
        ContextAssembler(renderer=lambda _payload: "", token_counter=ExactCounter())
    ).build(
        candidates,
        policy=ContextAssemblyPolicy(
            purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
            max_prompt_chars=1_000,
            max_prompt_tokens=900,
        ),
        structured_messages=structured,
    ).require_request()

    assert [(message.role, message.tool_call_id) for message in request.messages] == [
        ("assistant", None),
        ("tool", "call-1"),
        ("assistant", None),
        ("tool", "call-2"),
    ]


def test_typed_context_assembler_omits_explicit_stale_evidence() -> None:
    assembler = ContextAssembler(renderer=render)
    stale = ContextCandidate(
        candidate_id="stale-environment",
        kind=ContextCandidateKind.ENVIRONMENT,
        content="Python 3.9 is active.",
        retention=ContextCandidateRetention.OPTIONAL,
        freshness=ContextCandidateFreshness.STALE,
    )

    result = assembler.assemble_candidates(
        [stale],
        policy=ContextAssemblyPolicy(max_prompt_chars=1_000),
    )

    assert result.selection.assembly_status == ContextAssemblyStatus.READY
    assert result.selected_candidates == []
    assert result.selection.candidate_decisions[0].reason == "stale"


def test_typed_context_assembler_resolves_explicit_conflict_by_precedence() -> None:
    assembler = ContextAssembler(renderer=render)
    candidates = [
        ContextCandidate(
            candidate_id="retrieved-runtime",
            kind=ContextCandidateKind.MEMORY,
            content="Runtime is Python 3.9.",
            trust=ContextCandidateTrust.RETRIEVED,
            freshness=ContextCandidateFreshness.HISTORICAL,
            conflict_key="project.runtime.python",
        ),
        ContextCandidate(
            candidate_id="observed-runtime",
            kind=ContextCandidateKind.ENVIRONMENT,
            content="Runtime is Python 3.13.",
            trust=ContextCandidateTrust.OBSERVED,
            freshness=ContextCandidateFreshness.CURRENT,
            conflict_key="project.runtime.python",
        ),
    ]

    result = assembler.assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(max_prompt_chars=1_000),
    )

    assert [item.candidate_id for item in result.selected_candidates] == ["observed-runtime"]
    decisions = {item.candidate_id: item for item in result.selection.candidate_decisions}
    assert decisions["retrieved-runtime"].reason == "conflict_precedence"
    assert decisions["retrieved-runtime"].governed_by_candidate_id == "observed-runtime"


def test_typed_context_assembler_blocks_conflicting_required_candidates() -> None:
    assembler = ContextAssembler(renderer=render)
    candidates = [
        ContextCandidate(
            candidate_id="required-a",
            kind=ContextCandidateKind.CONSTRAINT,
            content="Use Python 3.12.",
            retention=ContextCandidateRetention.REQUIRED,
            conflict_key="runtime.python",
        ),
        ContextCandidate(
            candidate_id="required-b",
            kind=ContextCandidateKind.CONSTRAINT,
            content="Use Python 3.13.",
            retention=ContextCandidateRetention.REQUIRED,
            conflict_key="runtime.python",
        ),
    ]

    result = assembler.assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(max_prompt_chars=1_000),
    )

    assert result.selection.assembly_status == ContextAssemblyStatus.GOVERNANCE_BLOCKED
    assert result.selection.governance_blocked_candidate_ids == ["required-a", "required-b"]
    assert result.selected_candidates == []
    assert all(
        decision.reason == "governance_conflict"
        for decision in result.selection.candidate_decisions
    )


def test_typed_context_assembler_blocks_required_stale_candidate() -> None:
    assembler = ContextAssembler(renderer=render)
    candidate = ContextCandidate(
        candidate_id="required-stale",
        kind=ContextCandidateKind.CONSTRAINT,
        content="Use the expired constraint.",
        retention=ContextCandidateRetention.REQUIRED,
        freshness=ContextCandidateFreshness.STALE,
    )

    result = assembler.assemble_candidates(
        [candidate],
        policy=ContextAssemblyPolicy(max_prompt_chars=1_000),
    )

    assert result.selection.assembly_status == ContextAssemblyStatus.GOVERNANCE_BLOCKED
    assert result.selection.omitted_required_candidate_ids == ["required-stale"]
    assert result.selection.governance_blocked_candidate_ids == ["required-stale"]


def test_context_request_builder_preserves_governance_failure_cause() -> None:
    assembler = ContextAssembler(renderer=render)
    builder = ContextRequestBuilder(assembler)
    candidates = [
        ContextCandidate(
            candidate_id=f"required-{version}",
            kind=ContextCandidateKind.CONSTRAINT,
            content=f"Use Python {version}.",
            retention=ContextCandidateRetention.REQUIRED,
            conflict_key="runtime.python",
        )
        for version in ("3.12", "3.13")
    ]

    prepared = builder.build(
        candidates,
        policy=ContextAssemblyPolicy(max_prompt_chars=1_000),
    )

    assert prepared.request is None
    with pytest.raises(ContextAssemblyGovernanceError) as exc_info:
        prepared.require_request()
    assert exc_info.value.context["governance_blocked_candidate_ids"] == [
        "required-3.12",
        "required-3.13",
    ]


def test_typed_context_assembler_links_compacted_sources_to_artifact_candidate() -> None:
    assembler = ContextAssembler(renderer=render)
    source = ContextCandidate(
        candidate_id="dialog-old",
        kind=ContextCandidateKind.DIALOG,
        content="USER: old decision",
        source_order=1,
    )
    compacted = ContextCandidate(
        candidate_id="compaction-dialog-old",
        kind=ContextCandidateKind.ARTIFACT,
        content="Earlier dialog: old decision",
        priority=99,
        source_order=0,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        compacted_candidate_ids=[source.candidate_id],
    )

    result = assembler.assemble_candidates(
        [source, compacted],
        policy=ContextAssemblyPolicy(max_prompt_chars=1_000),
    )

    assert [item.candidate_id for item in result.selected_candidates] == [
        "compaction-dialog-old"
    ]
    decisions = {item.candidate_id: item for item in result.selection.candidate_decisions}
    assert decisions["dialog-old"].reason == "compacted"
    assert decisions["dialog-old"].governed_by_candidate_id == "compaction-dialog-old"


def test_typed_context_assembler_rejects_compaction_of_required_candidate() -> None:
    assembler = ContextAssembler(renderer=render)
    required = ContextCandidate(
        candidate_id="required-source",
        kind=ContextCandidateKind.CONSTRAINT,
        content="Required source",
        retention=ContextCandidateRetention.REQUIRED,
    )
    compacted = ContextCandidate(
        candidate_id="illegal-compaction",
        kind=ContextCandidateKind.ARTIFACT,
        content="Summary",
        truncation=ContextCandidateTruncation.FORBIDDEN,
        compacted_candidate_ids=[required.candidate_id],
    )

    with pytest.raises(ValueError, match="required"):
        assembler.assemble_candidates(
            [required, compacted],
            policy=ContextAssemblyPolicy(max_prompt_chars=1_000),
        )


def test_typed_context_assembler_falls_back_to_source_when_compactor_cannot_fit() -> None:
    assembler = ContextAssembler(renderer=render)
    source = ContextCandidate(
        candidate_id="observation-old",
        kind=ContextCandidateKind.RUNTIME_EVIDENCE,
        content="source evidence survives atomic fallback",
        priority=80,
        source_order=1,
        truncation=ContextCandidateTruncation.TAIL,
    )
    compacted = ContextCandidate(
        candidate_id="compaction-observation-old",
        kind=ContextCandidateKind.ARTIFACT,
        content="replacement that cannot fit atomically",
        priority=99,
        source_order=0,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        compacted_candidate_ids=[source.candidate_id],
    )
    policy = ContextAssemblyPolicy(max_prompt_chars=20)
    baseline = assembler.assemble_candidates([source], policy=policy)

    result = assembler.assemble_candidates([source, compacted], policy=policy)

    assert result.prompt_text == baseline.prompt_text
    assert [candidate.candidate_id for candidate in result.selected_candidates] == [
        candidate.candidate_id for candidate in baseline.selected_candidates
    ]
    decisions = {
        decision.candidate_id: decision
        for decision in result.selection.candidate_decisions
    }
    assert decisions[compacted.candidate_id].action == "omitted"
    assert decisions[source.candidate_id].reason != "compacted"
    assert decisions[source.candidate_id].action == "partially_kept"


@pytest.mark.parametrize(
    "truncation",
    [ContextCandidateTruncation.HEAD, ContextCandidateTruncation.TAIL],
)
def test_context_candidate_rejects_truncatable_compactor(
    truncation: ContextCandidateTruncation,
) -> None:
    with pytest.raises(ValueError, match="compaction.*truncation|truncation.*compaction"):
        ContextCandidate(
            candidate_id=f"illegal-{truncation.value}-compactor",
            kind=ContextCandidateKind.ARTIFACT,
            content="A compact projection must remain atomic.",
            truncation=truncation,
            compacted_candidate_ids=["observation-old"],
        )


def test_typed_context_assembler_rejects_compactor_compacting_compactor() -> None:
    assembler = ContextAssembler(renderer=render)
    source = ContextCandidate(
        candidate_id="observation-old",
        kind=ContextCandidateKind.RUNTIME_EVIDENCE,
        content="Original observation",
    )
    inner = ContextCandidate(
        candidate_id="compaction-inner",
        kind=ContextCandidateKind.ARTIFACT,
        content="Inner compact projection",
        truncation=ContextCandidateTruncation.FORBIDDEN,
        compacted_candidate_ids=[source.candidate_id],
    )
    outer = ContextCandidate(
        candidate_id="compaction-outer",
        kind=ContextCandidateKind.ARTIFACT,
        content="Outer compact projection",
        truncation=ContextCandidateTruncation.FORBIDDEN,
        compacted_candidate_ids=[inner.candidate_id],
    )

    with pytest.raises(ValueError, match="compactor|compaction"):
        assembler.assemble_candidates(
            [source, inner, outer],
            policy=ContextAssemblyPolicy(max_prompt_chars=1_000),
        )


def test_typed_context_assembler_rejects_compaction_cycle() -> None:
    assembler = ContextAssembler(renderer=render)
    first = ContextCandidate(
        candidate_id="compaction-first",
        kind=ContextCandidateKind.ARTIFACT,
        content="First compact projection",
        truncation=ContextCandidateTruncation.FORBIDDEN,
        compacted_candidate_ids=["compaction-second"],
    )
    second = ContextCandidate(
        candidate_id="compaction-second",
        kind=ContextCandidateKind.ARTIFACT,
        content="Second compact projection",
        truncation=ContextCandidateTruncation.FORBIDDEN,
        compacted_candidate_ids=["compaction-first"],
    )

    with pytest.raises(ValueError, match="cycle|compactor|compaction"):
        assembler.assemble_candidates(
            [first, second],
            policy=ContextAssemblyPolicy(max_prompt_chars=1_000),
        )


def test_typed_context_assembler_allows_independent_compactors() -> None:
    assembler = ContextAssembler(renderer=render)
    first_source = ContextCandidate(
        candidate_id="observation-first",
        kind=ContextCandidateKind.RUNTIME_EVIDENCE,
        content="First original observation",
    )
    second_source = ContextCandidate(
        candidate_id="observation-second",
        kind=ContextCandidateKind.RUNTIME_EVIDENCE,
        content="Second original observation",
    )
    first_compactor = ContextCandidate(
        candidate_id="compaction-first",
        kind=ContextCandidateKind.ARTIFACT,
        content="First compact projection",
        priority=99,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        compacted_candidate_ids=[first_source.candidate_id],
    )
    second_compactor = ContextCandidate(
        candidate_id="compaction-second",
        kind=ContextCandidateKind.ARTIFACT,
        content="Second compact projection",
        priority=99,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        compacted_candidate_ids=[second_source.candidate_id],
    )

    result = assembler.assemble_candidates(
        [first_source, first_compactor, second_source, second_compactor],
        policy=ContextAssemblyPolicy(max_prompt_chars=1_000),
    )

    assert {candidate.candidate_id for candidate in result.selected_candidates} == {
        first_compactor.candidate_id,
        second_compactor.candidate_id,
    }
    decisions = {
        decision.candidate_id: decision
        for decision in result.selection.candidate_decisions
    }
    assert decisions[first_source.candidate_id].reason == "compacted"
    assert (
        decisions[first_source.candidate_id].governed_by_candidate_id
        == first_compactor.candidate_id
    )
    assert decisions[second_source.candidate_id].reason == "compacted"
    assert (
        decisions[second_source.candidate_id].governed_by_candidate_id
        == second_compactor.candidate_id
    )


def test_context_request_registry_covers_every_typed_production_purpose() -> None:
    expected = {
        purpose.value
        for purpose in ContextRequestPurpose
        if purpose is not ContextRequestPurpose.UNSPECIFIED
    }

    assert set(CONTEXT_REQUEST_MIGRATION_REGISTRY) == expected


def test_context_request_builder_adapts_messages_with_typed_purpose() -> None:
    assembler = ContextAssembler(renderer=render, token_counter=ExactCounter())
    builder = ContextRequestBuilder(assembler)

    prepared = builder.build_messages(
        [
            LLMMessage(role="system", content="Return JSON."),
            LLMMessage(role="user", content="Analyze this task."),
        ],
        purpose=ContextRequestPurpose.SEMANTIC_GOAL,
        policy=ContextAssemblyPolicy(
            purpose=ContextRequestPurpose.SEMANTIC_GOAL,
            max_prompt_chars=1000,
            max_prompt_tokens=200,
            reserved_prompt_tokens=20,
        ),
        response_format="json_object",
    )

    request = prepared.require_request()
    assert [message.model_dump() for message in request.messages] == [
        {"role": "system", "content": "Return JSON."},
        {"role": "user", "content": "Analyze this task."},
    ]
    assert request.context_selection.request_purpose == ContextRequestPurpose.SEMANTIC_GOAL
