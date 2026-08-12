from __future__ import annotations

from types import SimpleNamespace
from datetime import timedelta

import pytest

from core.exceptions import ContextAssemblyBudgetError, InvalidLLMResponseError
from memory.context_assembly import ContextAssembler
from metadata import (
    ContextAssemblyPolicy,
    ContextAssemblyStatus,
    ContextCandidateRetention,
    ContextCandidateTruncation,
    ContextRequestPurpose,
    EnhancementCompletionRequirement,
    ReasoningMode,
    RuntimeBudgetMetadata,
)
from tools.code_generation_context import build_code_generation_candidates
from tools.code_generator import CodeGenerator
from tools.code_models import CodeGenerationRequest
from runtime_diagnostics.llm_proxy import TrajectoryLLMClientProxy


def _contextual_request(*, current_code: str | None = None, diagnosis: str = "") -> CodeGenerationRequest:
    code = current_code if current_code is not None else (
        "def add(left, right):\n"
        "    return left + right\n\n"
        "def divide(numerator, denominator):\n"
        "    return numerator / denominator\n"
    )
    return CodeGenerationRequest(
        request_id="improvement-codegen",
        task_description="Add a guarded __main__ demo without changing add or divide.",
        language="python",
        max_lines=260,
        forbidden_operations=["os.system", "eval", "exec"],
        prompt_context={
            "operation_kind": "file_replace",
            "original_goal": "Preserve the calculator API and add a runnable demonstration.",
            "iteration_goal": "Add an if __name__ == '__main__' demonstration.",
            "acceptance_criteria": [
                "python calculator.py prints add and divide examples.",
                "Imported add and divide behavior remains unchanged.",
            ],
            "tool_task": "Modify only calculator.py by adding a guarded demo block.",
            "agent_instruction": "Make the smallest safe replacement and avoid unrelated rewrites.",
            "project_context": {
                "project_path": "/tmp/calculator",
                "target_file": "/tmp/calculator/calculator.py",
                "written_files": ["/tmp/calculator/calculator.py"],
                "current_code_context": code,
                "validation_passed": True,
                "validation_errors": [],
                "warnings": ["Documentation is minimal."],
                "environment": {"provider_dump": "ENVIRONMENT_NOISE " * 1200},
            },
            "product_intent": {
                "delivery_surface": "project_native",
                "runtime_mode": "best_fit_for_goal",
                "non_regression_constraints": [
                    "Preserve the public calculator API.",
                    "Do not modify tests.",
                ],
                "disallowed_substitutions": ["Do not replace the library with a CLI."],
            },
            "stack_preset": {
                "delivery_surface": "project_native",
                "architecture": "single_runtime",
                "backend_language": "python",
            },
            "ui_iteration_contract": {
                "assessment_required": True,
                "implementation_required_for_user_facing_change": False,
            },
            "quality_rubric": ["The demo must not run on import."],
            "dependency_strategy": {"preserve_packages": ["pytest"]},
            "improvement_report_summary": {
                "summary": "The library works but has no direct-run demonstration.",
            },
            "selected_candidate": {"candidate_id": "add-demo", "priority_score": 0.8},
            "diagnosis": {"full_history": diagnosis or ("DIAGNOSIS_NOISE " * 1800)},
            "product_judgment": {"provider_history": "JUDGMENT_NOISE " * 1200},
        },
    )


def _render(candidates) -> str:
    return "\n\n".join(candidate.content for candidate in candidates)


def test_contextual_code_generation_projects_required_facts_and_optional_noise_separately() -> None:
    request = _contextual_request()

    candidates = build_code_generation_candidates(request)
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    required_ids = {
        "code_generation:instruction",
        "code_generation:task",
        "code_generation:permission",
        "code_generation:safety",
        "code_generation:current_code",
        "code_generation:output_contract",
    }

    assert required_ids.issubset(by_id)
    assert all(
        by_id[candidate_id].retention == ContextCandidateRetention.REQUIRED
        and by_id[candidate_id].truncation == ContextCandidateTruncation.FORBIDDEN
        for candidate_id in required_ids
    )
    required_text = _render([by_id[candidate_id] for candidate_id in sorted(required_ids)])
    assert "Add a guarded __main__ demo" in required_text
    assert "/tmp/calculator/calculator.py" in required_text
    assert "file_replace" in required_text
    assert "Do not modify tests." in required_text
    assert "project_native" in required_text
    assert "python calculator.py prints" in required_text
    assert "def divide" in required_text
    assert "DIAGNOSIS_NOISE" not in required_text
    assert "ENVIRONMENT_NOISE" not in _render(candidates)
    assert "JUDGMENT_NOISE" not in _render(candidates)
    assert all(candidate.source_id for candidate in candidates)


def test_large_optional_context_is_omitted_without_losing_required_code_generation_facts() -> None:
    candidates = build_code_generation_candidates(_contextual_request())
    required = [
        candidate
        for candidate in candidates
        if candidate.retention == ContextCandidateRetention.REQUIRED
    ]
    optional = [
        candidate
        for candidate in candidates
        if candidate.retention != ContextCandidateRetention.REQUIRED
    ]
    required_budget = len(_render(sorted(required, key=lambda item: item.source_order)))

    result = ContextAssembler(renderer=lambda _payload: "").assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(
            purpose=ContextRequestPurpose.CODE_GENERATION,
            max_prompt_chars=required_budget,
        ),
        renderer=_render,
    )
    decisions = {decision.candidate_id: decision for decision in result.selection.candidate_decisions}

    assert result.selection.assembly_status == ContextAssemblyStatus.READY
    assert result.selection.omitted_required_candidate_ids == []
    assert all(decisions[candidate.candidate_id].action == "kept" for candidate in required)
    assert all(decisions[candidate.candidate_id].action == "omitted" for candidate in optional)


def test_existing_file_replacement_fails_closed_when_required_current_code_cannot_fit() -> None:
    candidates = build_code_generation_candidates(
        _contextual_request(current_code="REQUIRED_CURRENT_CODE\n" + ("value = 1\n" * 3000))
    )

    result = ContextAssembler(renderer=lambda _payload: "").assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(
            purpose=ContextRequestPurpose.CODE_GENERATION,
            max_prompt_chars=1_500,
        ),
        renderer=_render,
    )

    assert result.selection.assembly_status == ContextAssemblyStatus.BUDGET_INSUFFICIENT
    assert "code_generation:current_code" in result.selection.omitted_required_candidate_ids


def test_existing_file_replacement_requires_current_code_evidence() -> None:
    request = _contextual_request()
    request.prompt_context["project_context"].pop("current_code_context")

    with pytest.raises(ValueError, match="existing-file replacement requires current code context"):
        build_code_generation_candidates(request)


class _CapturingLLM:
    def __init__(self) -> None:
        self.requests = []
        self.settings = SimpleNamespace(
            provider="test",
            model="test-model",
            base_url="",
            tokenizer_path=None,
            context_max_prompt_tokens=4096,
            context_reserved_prompt_tokens=128,
        )

    def complete(self, request):
        self.requests.append(request)
        return SimpleNamespace(content="```python\nprint('ok')\n```")


def test_contextual_generator_submits_candidate_selection_not_monolithic_message() -> None:
    client = _CapturingLLM()
    generator = CodeGenerator(client)

    generator.generate_code(_contextual_request())

    assert len(client.requests) == 1
    selection = client.requests[0].context_selection
    candidate_ids = {decision.candidate_id for decision in selection.candidate_decisions}
    assert selection.request_purpose == ContextRequestPurpose.CODE_GENERATION
    assert selection.assembly_status == ContextAssemblyStatus.READY
    assert "code_generation:instruction" in candidate_ids
    assert "code_generation:current_code" in candidate_ids
    assert "code_generation:message:1" not in candidate_ids
    assert selection.omitted_required_candidate_ids == []


def test_code_generation_submits_an_explicit_completion_reservation(tmp_path) -> None:
    client = _CapturingLLM()
    generator = CodeGenerator(client)

    generator.generate_code(_contextual_request())

    request = client.requests[0]
    assert request.max_tokens is not None
    assert request.max_tokens > 0
    assert request.trace_info["completion_budget"]["reserved_tokens"] == request.max_tokens
    # file_replace can rewrite an entire file and must never be classified as a
    # routine/narrow completion merely because it has one target.
    assert request.reasoning_policy.mode == ReasoningMode.PROVIDER_DEFAULT


def test_ambiguous_multi_target_code_generation_keeps_provider_default_reasoning() -> None:
    client = _CapturingLLM()
    generator = CodeGenerator(client)
    request = _contextual_request()
    request.prompt_context["project_context"]["written_files"] = [
        "/tmp/calculator/calculator.py",
        "/tmp/calculator/README.md",
    ]
    request.prompt_context["project_context"]["target_file"] = ""

    generator.generate_code(request)

    assert client.requests[0].reasoning_policy.mode == ReasoningMode.PROVIDER_DEFAULT


def test_code_generation_length_finish_reason_fails_typed_without_returning_truncated_code() -> None:
    class LengthLLM(_CapturingLLM):
        def complete(self, request):
            self.requests.append(request)
            return SimpleNamespace(
                content="```python\ndef incomplete(",
                usage={"completion_tokens": request.max_tokens},
                finish_reason="length",
            )

    generator = CodeGenerator(LengthLLM())

    with pytest.raises(InvalidLLMResponseError) as exc:
        generator.generate_code(_contextual_request())

    assert exc.value.finish_reason == "length"
    assert "def incomplete(" not in getattr(exc.value, "generated_code", "")


def test_code_generation_retries_one_observed_length_with_larger_budget() -> None:
    class LengthThenStopLLM(_CapturingLLM):
        def complete(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return SimpleNamespace(
                    content="```python\ndef incomplete(",
                    usage={"completion_tokens": request.max_tokens},
                    finish_reason="length",
                )
            return SimpleNamespace(
                content="```python\nprint('ok')\n```",
                usage={"completion_tokens": 12},
                finish_reason="stop",
            )

    client = LengthThenStopLLM()
    budget = RuntimeBudgetMetadata()
    generator = CodeGenerator(
        client,
        runtime_budget=budget,
        enhancement_requirement=EnhancementCompletionRequirement.REQUIRED,
    )

    generated = generator.generate_code(_contextual_request())

    assert generated.code == "print('ok')"
    assert len(client.requests) == 2
    assert client.requests[1].max_tokens > client.requests[0].max_tokens
    assert len(budget.enhancement_completion_reservations) == 2
    assert len(budget.enhancement_completion_reconciliations) == 2


def test_code_generation_second_length_fails_without_third_attempt() -> None:
    class AlwaysLengthLLM(_CapturingLLM):
        def complete(self, request):
            self.requests.append(request)
            return SimpleNamespace(
                content="```python\ndef incomplete(",
                usage={"completion_tokens": request.max_tokens},
                finish_reason="length",
            )

    client = AlwaysLengthLLM()
    generator = CodeGenerator(
        client,
        runtime_budget=RuntimeBudgetMetadata(),
        enhancement_requirement=EnhancementCompletionRequirement.REQUIRED,
    )

    with pytest.raises(InvalidLLMResponseError) as exc:
        generator.generate_code(_contextual_request())

    assert len(client.requests) == 2
    assert exc.value.context["recovery_disposition"] == "decompose_required"


def test_code_generation_length_without_usage_does_not_retry() -> None:
    class UnknownUsageLengthLLM(_CapturingLLM):
        def complete(self, request):
            self.requests.append(request)
            return SimpleNamespace(
                content="```python\ndef incomplete(",
                usage=None,
                finish_reason="length",
            )

    client = UnknownUsageLengthLLM()
    generator = CodeGenerator(
        client,
        runtime_budget=RuntimeBudgetMetadata(),
        enhancement_requirement=EnhancementCompletionRequirement.REQUIRED,
    )

    with pytest.raises(InvalidLLMResponseError) as exc:
        generator.generate_code(_contextual_request())

    assert len(client.requests) == 1
    assert exc.value.context["recovery_disposition"] == "decompose_required"


def test_core_code_generation_does_not_consume_enhancement_budget_but_improvement_does() -> None:
    from metadata import RuntimeBudgetMetadata

    core_budget = RuntimeBudgetMetadata()
    core_client = _CapturingLLM()
    core_generator = CodeGenerator(core_client, runtime_budget=core_budget)
    core_generator._call_llm("Generate a small standalone program")

    assert core_budget.enhancement_completion_tokens_reserved == 0
    assert core_budget.enhancement_completion_tokens_used == 0

    improvement_budget = RuntimeBudgetMetadata()
    improvement_client = _CapturingLLM()
    improvement_generator = CodeGenerator(
        improvement_client,
        runtime_budget=improvement_budget,
        enhancement_requirement=EnhancementCompletionRequirement.REQUIRED,
    )
    improvement_generator.generate_code(_contextual_request())

    assert (
        improvement_budget.enhancement_completion_tokens_reserved
        + improvement_budget.enhancement_completion_tokens_used
    ) > 0


def test_codegen_provider_identity_and_logical_reservation_ignore_business_and_audit_noise() -> None:
    from metadata import EnhancementCompletionRequirement, RuntimeBudgetMetadata

    budget = RuntimeBudgetMetadata()
    client = _CapturingLLM()
    generator = CodeGenerator(
        client,
        runtime_budget=budget,
        enhancement_requirement=EnhancementCompletionRequirement.REQUIRED,
    )
    first_business_request = _contextual_request()
    second_business_request = first_business_request.model_copy(
        update={
            "request_id": "different-business-request-id",
            "created_at": first_business_request.created_at + timedelta(hours=3),
        }
    )

    generator.generate_code(first_business_request)
    reserved_after_first = budget.enhancement_completion_tokens_reserved
    generator.generate_code(second_business_request)

    first, second = client.requests
    assert first.messages == second.messages
    assert first.trace_info["completion_budget"]["reservation_id"] == second.trace_info[
        "completion_budget"
    ]["reservation_id"]
    assert budget.enhancement_completion_tokens_reserved == reserved_after_first
    assert len(budget.enhancement_completion_reservations) == 1

    audit_variant = second.model_copy(
        update={
            "trace_info": {
                **second.trace_info,
                "request_id": "audit-only-request-id",
                "completion_budget": {
                    **second.trace_info["completion_budget"],
                    "remaining_tokens": 1,
                },
            },
            "context_selection": second.context_selection.model_copy(
                update={
                    "original_prompt_chars": second.context_selection.original_prompt_chars + 500,
                }
            ),
        }
    )
    settings = client.settings
    assert TrajectoryLLMClientProxy._request_hash(first, settings=settings) == (
        TrajectoryLLMClientProxy._request_hash(audit_variant, settings=settings)
    )


def test_non_contextual_code_generation_keeps_legacy_message_boundary() -> None:
    client = _CapturingLLM()
    generator = CodeGenerator(client)

    generator._call_llm("Generate a small program")

    assert client.requests[0].context_selection.request_purpose == ContextRequestPurpose.CODE_GENERATION
    assert [
        decision.candidate_id
        for decision in client.requests[0].context_selection.candidate_decisions
    ] == ["code_generation:message:1"]
    with pytest.raises(ContextAssemblyBudgetError):
        generator._call_llm("x" * 20_000)
    assert len(client.requests) == 1
