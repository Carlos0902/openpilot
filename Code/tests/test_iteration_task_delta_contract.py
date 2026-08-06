from __future__ import annotations

from types import SimpleNamespace

import pytest

from autonomous_iteration.agents.iteration_agent import AutonomousIterationAgent
from autonomous_iteration.enhancement_completion_budget import EnhancementCompletionBudgetCoordinator
from autonomous_iteration.models import EvaluationResult, ImprovementGoal, ProjectStateSnapshot
from autonomous_iteration.project_improvement_context import (
    build_iteration_goal_candidates,
    build_iteration_task_design_candidates,
)
from metadata import (
    ContextCandidateKind,
    ContextRequestPurpose,
    EnhancementCompletionComplexity,
    EnhancementCompletionDecisionValue,
    EnhancementCompletionRequest,
    EnhancementCompletionRequirement,
    ReasoningMode,
    ReasoningDecisionComplexity,
    RuntimeBudgetMetadata,
)
from core.exceptions import ContextAssemblyBudgetError, InvalidLLMResponseError, LLMProviderError


class _Evaluator:
    llm_client = None

    def evaluate_project(self, **_kwargs) -> EvaluationResult:
        return EvaluationResult(
            validation_passed=True,
            runnable=True,
            has_blocking_bugs=False,
            summary="Project validation passed.",
        )


def _exhausted_enhancement_budget() -> RuntimeBudgetMetadata:
    budget = RuntimeBudgetMetadata()
    coordinator = EnhancementCompletionBudgetCoordinator(budget)
    for index in range(4):
        coordinator.reserve(
            EnhancementCompletionRequest(
                logical_key=f"exhaustion:{index}",
                purpose=ContextRequestPurpose.CODE_GENERATION,
                complexity=EnhancementCompletionComplexity.COMPLEX,
                prompt_tokens=0,
                remaining_calls=1,
                remaining_value=EnhancementCompletionDecisionValue.HIGH,
                requirement=EnhancementCompletionRequirement.REQUIRED,
            )
        )
    return budget

class _TaskDeltaLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests = []
        self.kwargs = []
        self.settings = SimpleNamespace(
            provider="openai_compatible",
            model="test-model",
            context_max_prompt_tokens=4096,
            context_reserved_prompt_tokens=128,
        )

    def complete(self, request, **kwargs):
        self.requests.append(request)
        self.kwargs.append(kwargs)
        return SimpleNamespace(parsed_json=self.payload, content="")


class _RequiredEnhancementProviderFailure(RuntimeError):
    """Typed test signal that required provider work did not execute."""


class _FailingRequiredLLM(_TaskDeltaLLM):
    def complete(self, request, **kwargs):
        self.requests.append(request)
        self.kwargs.append(kwargs)
        raise _RequiredEnhancementProviderFailure("provider unavailable")


class _LengthThenDeltaLLM(_TaskDeltaLLM):
    def complete(self, request, **kwargs):
        self.requests.append(request)
        self.kwargs.append(kwargs)
        if len(self.requests) == 1:
            raise InvalidLLMResponseError(
                "truncated enhancement delta",
                response_text='{"partial":',
                usage={"completion_tokens": 111},
                finish_reason="length",
            )
        return SimpleNamespace(
            parsed_json=self.payload,
            content="",
            usage={"completion_tokens": 222},
            finish_reason="stop",
        )


def _goal() -> ImprovementGoal:
    return ImprovementGoal(
        id="document-workflow",
        title="Document the exact validation workflow.",
        rationale="Users need a reproducible verification path.",
        acceptance_criteria=[
            "README contains the exact pytest command.",
            "Existing calculator behavior remains unchanged.",
        ],
        priority="high",
    )


def test_completed_goal_is_filtered_before_task_design_can_repeat_work() -> None:
    repeated = _goal()
    fresh = repeated.model_copy(
        update={"id": "fresh", "title": "Add a new validation boundary."}
    )

    selected = AutonomousIterationAgent._select_uncompleted_goal(
        [repeated, fresh],
        ["  DOCUMENT THE EXACT VALIDATION WORKFLOW.  "],
    )

    assert selected == fresh
    assert AutonomousIterationAgent._select_uncompleted_goal(
        [repeated], [repeated.title]
    ) is None


def _state(tmp_path, *, safe_target_count: int = 2) -> ProjectStateSnapshot:
    safe_targets = [str(tmp_path / "README.md")]
    safe_targets.extend(
        str(tmp_path / f"module_{index}.py")
        for index in range(max(0, safe_target_count - 1))
    )
    return ProjectStateSnapshot(
        project_path=str(tmp_path),
        goal="Preserve the calculator API while documenting its validation workflow.",
        written_files=safe_targets,
        safe_target_files=safe_targets,
        run_command="python -m pytest -q",
        validation_context={
            "validation_passed": True,
            "summary": "All requested validation commands passed.",
            "run_command": "python -m pytest -q",
        },
    )


def _report() -> dict:
    return {
        "summary": "Core behavior is correct; improve the documented workflow.",
        "prompt_context": {
            "original_goal": "Preserve the calculator API while documenting its validation workflow.",
            "product_intent": {
                "non_regression_constraints": [
                    "Do not modify tests.",
                    "Preserve the public calculator API.",
                ],
                "disallowed_substitutions": [
                    "Do not replace the Python library with a CLI.",
                ],
            },
            "quality_rubric": [
                "The documented validation command must equal python -m pytest -q.",
            ],
        },
    }


def _task_delta(**overrides) -> dict:
    delta = {
        "description": "Add the verified pytest command to README.md.",
        "target_files": ["README.md"],
        "acceptance_criteria": ["README shows python -m pytest -q."],
        "risk_notes": ["Do not change calculator behavior."],
        "evidence_ids": ["iteration_task_design:validation"],
    }
    delta.update(overrides)
    return delta


def test_task_designer_schema_requests_one_identity_free_bounded_delta(tmp_path) -> None:
    candidates = build_iteration_task_design_candidates(
        project_state=_state(tmp_path),
        goal=_goal(),
        improvement_report=_report(),
        completed_iteration=0,
    )
    schema = next(item for item in candidates if item.kind == ContextCandidateKind.TOOL_SCHEMA)

    assert '"task"' in schema.content
    assert '"tasks"' not in schema.content
    assert '"id"' not in schema.content
    assert '"goal_id"' not in schema.content
    assert '"evidence_ids"' in schema.content
    assert '"description"' in schema.content
    assert '"target_files"' in schema.content
    assert '"acceptance_criteria"' in schema.content
    assert '"risk_notes"' in schema.content


def test_task_designer_evidence_contract_distinguishes_context_headers_from_domain_ids(
    tmp_path,
) -> None:
    candidates = build_iteration_task_design_candidates(
        project_state=_state(tmp_path),
        goal=_goal(),
        improvement_report=_report(),
        completed_iteration=0,
    )
    instruction = next(
        item for item in candidates if item.candidate_id == "iteration_task_design:instruction"
    )
    schema = next(
        item for item in candidates if item.candidate_id == "iteration_task_design:schema"
    )

    assert '[evidence_id="..."]' in instruction.content
    for content in (instruction.content, schema.content):
        assert "evidence_id" in content
        assert "goal ID" in content
        assert "diagnosis candidate ID" in content
        assert "must not" in content


def test_task_delta_accepts_header_evidence_and_rejects_domain_ids_together(tmp_path) -> None:
    goal = _goal()
    report = _report()
    diagnosis_id = "diagnosis_candidate_123"
    payload = {
        "task": _task_delta(
            evidence_ids=[
                goal.id,
                diagnosis_id,
                "iteration_task_design:validation",
            ]
        )
    }
    agent = AutonomousIterationAgent(_Evaluator(), llm_client=_TaskDeltaLLM(payload))

    task = agent._design_tasks(_state(tmp_path), goal, report, 0)[0]

    assert task.evidence_ids == ["iteration_task_design:validation"]


def test_single_target_task_design_uses_routine_reasoning_policy(tmp_path) -> None:
    llm = _TaskDeltaLLM({"task": _task_delta()})
    agent = AutonomousIterationAgent(_Evaluator(), llm_client=llm)

    tasks = agent._design_tasks(
        _state(tmp_path, safe_target_count=1),
        _goal(),
        _report(),
        0,
    )

    assert tasks
    assert llm.requests[0].reasoning_policy.mode == ReasoningMode.DISABLED


def test_goal_and_task_design_requests_reserve_explicit_budgets_without_wide_json_retry(tmp_path) -> None:
    llm = _TaskDeltaLLM({"task": _task_delta()})
    agent = AutonomousIterationAgent(_Evaluator(), llm_client=llm)
    state = _state(tmp_path)

    agent._complete_json_candidates(
        build_iteration_goal_candidates(
            project_state=state,
            improvement_report=_report(),
            completed_iteration=0,
        ),
        purpose=ContextRequestPurpose.ITERATION_GOAL,
    )
    agent._complete_json_candidates(
        build_iteration_task_design_candidates(
            project_state=state,
            goal=_goal(),
            improvement_report=_report(),
            completed_iteration=0,
        ),
        purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
    )

    assert [request.trace_info["context_purpose"] for request in llm.requests] == [
        "iteration_goal",
        "iteration_task_design",
    ]
    assert all(request.max_tokens is not None and request.max_tokens > 0 for request in llm.requests)
    assert all(
        request.trace_info["completion_budget"]["reserved_tokens"] == request.max_tokens
        for request in llm.requests
    )
    assert all(request.reasoning_policy.mode == ReasoningMode.DISABLED for request in llm.requests)
    assert all(kwargs["max_retries"] == 1 for kwargs in llm.kwargs)


def test_reasoning_route_ab_freezes_completion_reservation(tmp_path) -> None:
    candidates = build_iteration_goal_candidates(
        project_state=_state(tmp_path, safe_target_count=1),
        improvement_report=_report(),
        completed_iteration=0,
    )
    payload = {
        "goals": [
            {
                "title": "Bound one project behavior",
                "category": "robustness",
                "rationale": "The route test uses one bounded goal.",
                "acceptance_criteria": ["The target remains in scope."],
                "priority": "high",
            }
        ]
    }

    standard_llm = _TaskDeltaLLM(payload)
    standard_agent = AutonomousIterationAgent(_Evaluator(), llm_client=standard_llm)
    standard_agent._complete_json_candidates(
        candidates,
        purpose=ContextRequestPurpose.ITERATION_GOAL,
        complexity=EnhancementCompletionComplexity.STANDARD,
        reasoning_complexity=ReasoningDecisionComplexity.STANDARD,
    )

    routine_llm = _TaskDeltaLLM(payload)
    routine_agent = AutonomousIterationAgent(_Evaluator(), llm_client=routine_llm)
    routine_agent._complete_json_candidates(
        candidates,
        purpose=ContextRequestPurpose.ITERATION_GOAL,
        complexity=EnhancementCompletionComplexity.STANDARD,
        reasoning_complexity=ReasoningDecisionComplexity.ROUTINE,
    )

    standard_request = standard_llm.requests[0]
    routine_request = routine_llm.requests[0]
    assert standard_request.max_tokens == routine_request.max_tokens
    assert standard_request.reasoning_policy.mode == ReasoningMode.PROVIDER_DEFAULT
    assert routine_request.reasoning_policy.mode == ReasoningMode.DISABLED


@pytest.mark.parametrize("stage", ["goal", "task_design"])
def test_required_goal_and_task_design_do_not_convert_provider_failure_to_fallback_success(
    tmp_path,
    stage,
) -> None:
    agent = AutonomousIterationAgent(
        _Evaluator(),
        llm_client=_FailingRequiredLLM({}),
        enhancement_requirement=EnhancementCompletionRequirement.REQUIRED,
    )

    with pytest.raises(_RequiredEnhancementProviderFailure, match="provider unavailable"):
        if stage == "goal":
            agent._make_goals(_state(tmp_path), _Evaluator().evaluate_project(), _report(), 0)
        else:
            agent._design_tasks(_state(tmp_path), _goal(), _report(), 0)


@pytest.mark.parametrize("stage", ["goal", "task_design"])
def test_required_goal_and_task_design_fail_closed_when_completion_budget_refuses(
    tmp_path,
    stage,
) -> None:
    budget = _exhausted_enhancement_budget()
    agent = AutonomousIterationAgent(
        _Evaluator(),
        llm_client=_TaskDeltaLLM({}),
        runtime_budget=budget,
        enhancement_requirement=EnhancementCompletionRequirement.REQUIRED,
    )

    with pytest.raises(ContextAssemblyBudgetError) as exc:
        if stage == "goal":
            agent._make_goals(_state(tmp_path), _Evaluator().evaluate_project(), _report(), 0)
        else:
            agent._design_tasks(_state(tmp_path), _goal(), _report(), 0)
    assert exc.value.context["omitted_required_candidate_ids"] == [
        f"enhancement_completion_budget:{'iteration_goal' if stage == 'goal' else 'iteration_task_design'}"
    ]


@pytest.mark.parametrize("stage", ["goal", "task_design"])
def test_optional_goal_and_task_design_use_controlled_fallback_when_completion_budget_refuses(
    tmp_path,
    stage,
) -> None:
    budget = _exhausted_enhancement_budget()
    llm = _TaskDeltaLLM({})
    agent = AutonomousIterationAgent(
        _Evaluator(),
        llm_client=llm,
        runtime_budget=budget,
        enhancement_requirement=EnhancementCompletionRequirement.OPTIONAL,
    )

    if stage == "goal":
        result = agent._make_goals(
            _state(tmp_path),
            _Evaluator().evaluate_project(),
            _report(),
            0,
        )
    else:
        result = agent._design_tasks(_state(tmp_path), _goal(), _report(), 0)

    assert result
    assert llm.requests == []


@pytest.mark.parametrize(
    ("purpose", "payload", "candidate_factory"),
    [
        (
            ContextRequestPurpose.ITERATION_GOAL,
            {
                "goals": [
                    {
                        "title": "Document validation.",
                        "rationale": "The workflow is currently implicit.",
                        "acceptance_criteria": ["README contains python -m pytest -q."],
                    }
                ]
            },
            lambda state: build_iteration_goal_candidates(
                project_state=state,
                improvement_report=_report(),
                completed_iteration=0,
            ),
        ),
        (
            ContextRequestPurpose.ITERATION_TASK_DESIGN,
            {"task": _task_delta()},
            lambda state: build_iteration_task_design_candidates(
                project_state=state,
                goal=_goal(),
                improvement_report=_report(),
                completed_iteration=0,
            ),
        ),
    ],
)
def test_required_iteration_json_recovers_once_from_known_usage_length(
    tmp_path,
    purpose,
    payload,
    candidate_factory,
) -> None:
    llm = _LengthThenDeltaLLM(payload)
    budget = RuntimeBudgetMetadata()
    agent = AutonomousIterationAgent(
        _Evaluator(),
        llm_client=llm,
        runtime_budget=budget,
        enhancement_requirement=EnhancementCompletionRequirement.REQUIRED,
    )

    parsed, _ = agent._complete_json_candidates(candidate_factory(_state(tmp_path)), purpose=purpose)

    assert parsed == payload
    assert len(llm.requests) == 2
    first, recovery = llm.requests
    assert recovery.max_tokens > first.max_tokens
    assert recovery.trace_info["completion_budget"]["recovery_of"] == first.trace_info[
        "completion_budget"
    ]["reservation_id"]
    assert budget.enhancement_completion_tokens_used == 333


@pytest.mark.parametrize("failure", ["malformed", "provider_error"])
def test_required_iteration_json_does_not_recover_non_length_failures(tmp_path, failure) -> None:
    class OneFailureLLM(_TaskDeltaLLM):
        def complete(self, request, **kwargs):
            self.requests.append(request)
            self.kwargs.append(kwargs)
            if failure == "provider_error":
                raise RuntimeError("provider unavailable")
            raise InvalidLLMResponseError(
                "malformed JSON",
                response_text="not-json",
                usage={"completion_tokens": 17},
                finish_reason="stop",
            )

    llm = OneFailureLLM({})
    agent = AutonomousIterationAgent(
        _Evaluator(),
        llm_client=llm,
        enhancement_requirement=EnhancementCompletionRequirement.REQUIRED,
    )

    with pytest.raises((InvalidLLMResponseError, RuntimeError)):
        agent._complete_json_candidates(
            build_iteration_task_design_candidates(
                project_state=_state(tmp_path),
                goal=_goal(),
                improvement_report=_report(),
                completed_iteration=0,
            ),
            purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
        )
    assert len(llm.requests) == 1


@pytest.mark.parametrize("requirement", [EnhancementCompletionRequirement.REQUIRED, EnhancementCompletionRequirement.OPTIONAL])
@pytest.mark.parametrize(
    ("stage", "payload"),
    [
        ("goal_no_client", None),
        ("goal_empty", {"goals": []}),
        (
            "goal_invalid",
            {
                "goals": [
                    {
                        "title": "Improve the project",
                        "rationale": "generic",
                        "acceptance_criteria": [],
                    }
                ]
            },
        ),
        ("task_missing", {}),
        ("task_invalid_target", {"task": _task_delta(target_files=["../../outside.py"])}),
    ],
)
def test_required_iteration_structural_failures_are_typed_and_optional_stays_safe(
    tmp_path,
    requirement,
    stage,
    payload,
) -> None:
    llm = None if stage == "goal_no_client" else _TaskDeltaLLM(payload)
    agent = AutonomousIterationAgent(
        _Evaluator(),
        llm_client=llm,
        enhancement_requirement=requirement,
    )

    def invoke():
        if stage.startswith("goal"):
            return agent._make_goals(
                _state(tmp_path),
                _Evaluator().evaluate_project(),
                _report(),
                0,
            )
        return agent._design_tasks(_state(tmp_path), _goal(), _report(), 0)

    if requirement == EnhancementCompletionRequirement.REQUIRED:
        expected = LLMProviderError if stage == "goal_no_client" else InvalidLLMResponseError
        with pytest.raises(expected):
            invoke()
    else:
        result = invoke()
        if stage == "task_invalid_target":
            # An explicit out-of-scope target must not be hidden by silently
            # substituting a different fallback write target.
            assert result == []
        else:
            assert result


def test_runtime_owns_stable_task_and_goal_identity_instead_of_provider(tmp_path) -> None:
    payload = {
        "task": _task_delta(
            id="provider-controlled-task-id",
            goal_id="provider-controlled-goal-id",
        )
    }
    agent = AutonomousIterationAgent(_Evaluator(), llm_client=_TaskDeltaLLM(payload))
    state = _state(tmp_path)
    goal = _goal()

    first = agent._design_tasks(state, goal, _report(), 2)[0]
    second = agent._design_tasks(state, goal, _report(), 2)[0]

    assert first.description == "Add the verified pytest command to README.md."
    assert first.goal_id == goal.id
    assert first.id == second.id
    assert first.id != "provider-controlled-task-id"
    assert first.goal_id != "provider-controlled-goal-id"
    assert first.evidence_ids == ["iteration_task_design:validation"]


def test_task_delta_is_code_bounded_and_keeps_authoritative_quality_semantics(tmp_path) -> None:
    state = _state(tmp_path, safe_target_count=24)
    valid_evidence_ids = [
        "iteration_task_design:goal",
        "iteration_task_design:safety",
        "iteration_task_design:validation",
    ]
    payload = {
        "task": _task_delta(
            description="D" * 5000,
            target_files=[*state.safe_target_files, "../../outside.py"],
            acceptance_criteria=[f"criterion-{index}-" + ("C" * 900) for index in range(30)],
            risk_notes=[f"risk-{index}-" + ("R" * 900) for index in range(30)],
            evidence_ids=[*valid_evidence_ids, *[f"unknown:{index}" for index in range(30)]],
        )
    }
    agent = AutonomousIterationAgent(_Evaluator(), llm_client=_TaskDeltaLLM(payload))

    task = agent._design_tasks(state, _goal(), _report(), 0)[0]

    assert 0 < len(task.description) <= 1000
    assert 0 < len(task.target_files) <= 8
    assert set(task.target_files).issubset(set(state.safe_target_files))
    assert 0 < len(task.acceptance_criteria) <= 8
    assert all(len(item) <= 500 for item in task.acceptance_criteria)
    assert set(_goal().acceptance_criteria).issubset(set(task.acceptance_criteria))
    assert 0 < len(task.risk_notes) <= 8
    assert all(len(item) <= 500 for item in task.risk_notes)
    assert "Do not modify tests." in task.risk_notes
    assert "Preserve the public calculator API." in task.risk_notes
    assert "Do not replace the Python library with a CLI." in task.risk_notes
    assert 0 < len(task.evidence_ids) <= 12
    assert set(task.evidence_ids).issubset(set(valid_evidence_ids))


def test_legacy_tasks_envelope_migrates_first_delta_but_not_provider_identity(tmp_path) -> None:
    payload = {
        "tasks": [
            _task_delta(id="legacy-provider-id", goal_id="legacy-provider-goal"),
            _task_delta(description="A second task must not be accepted in this call."),
        ]
    }
    agent = AutonomousIterationAgent(_Evaluator(), llm_client=_TaskDeltaLLM(payload))

    task = agent._design_tasks(_state(tmp_path), _goal(), _report(), 1)[0]

    assert task.description == "Add the verified pytest command to README.md."
    assert task.goal_id == _goal().id
    assert task.id != "legacy-provider-id"
    assert task.evidence_ids == ["iteration_task_design:validation"]


def test_explicit_out_of_scope_task_target_fails_closed(tmp_path) -> None:
    payload = {"task": _task_delta(target_files=["../../outside.py"])}
    agent = AutonomousIterationAgent(_Evaluator(), llm_client=_TaskDeltaLLM(payload))

    assert agent._design_tasks(_state(tmp_path), _goal(), _report(), 0) == []


def test_malformed_legacy_task_envelope_uses_controlled_fallback(tmp_path) -> None:
    agent = AutonomousIterationAgent(
        _Evaluator(),
        llm_client=_TaskDeltaLLM({"tasks": {"bad": "shape"}}),
    )

    tasks = agent._design_tasks(_state(tmp_path), _goal(), _report(), 0)

    assert len(tasks) == 1
    assert tasks[0].goal_id == _goal().id
