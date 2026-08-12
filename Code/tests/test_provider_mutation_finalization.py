from __future__ import annotations

from types import SimpleNamespace

import pytest

from autonomous_iteration.runtime_controller import StateUpdater
from autonomous_iteration.task_models import Task
from core.llm import LLMMessage, LLMResponse, LLMToolCall, LLMToolFunctionCall
from core.provider_final_response_transition import ProviderFinalResponseAction
from core.provider_mutation_finalization import (
    ProviderMutationFinalizationError,
    ProviderMutationFinalizationRunner,
)
from core.provider_mutation_validation_round import (
    ProviderMutationValidationRoundResult,
)
from core.provider_validation_observation import ProviderValidationObservation
from core.provider_mutation_transition import (
    ProviderMutationTransition,
    ProviderMutationTransitionAction,
)
from metadata import RuntimeStateMetadata


class _LLM:
    def __init__(self, response):
        self.settings = SimpleNamespace(context_max_prompt_tokens=4096, model="test-model")
        self.response = response
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return self.response


class _Owner:
    def __init__(self, runtime):
        self.runtime = runtime

    def _reasoning_policy_for_task(self, _task):
        return None


def _runtime(llm):
    return SimpleNamespace(
        llm_client=llm,
        runtime_controller=SimpleNamespace(
            state=RuntimeStateMetadata(goal="finalize"),
            state_updater=StateUpdater(),
        ),
    )


def _validation(_response) -> ProviderMutationValidationRoundResult:
    return ProviderMutationValidationRoundResult(
        observation=ProviderValidationObservation.SUCCEEDED,
        transition=ProviderMutationTransition(
            action=ProviderMutationTransitionAction.REQUEST_FINALIZATION,
            error_code=None,
            post_mutation_active=False,
            finalization_pending=True,
        ),
        loop_result=None,
        messages=(LLMMessage(role="user", content="Apply"),),
        attempts=(),
        rounds_used=1,
    )


def test_finalization_requires_pending_validation_transition():
    validation = ProviderMutationValidationRoundResult(
        observation=ProviderValidationObservation.SUCCEEDED,
        transition=ProviderMutationTransition(
            action=ProviderMutationTransitionAction.NONE,
            error_code=None,
            post_mutation_active=False,
            finalization_pending=False,
        ),
        loop_result=None,
        messages=(LLMMessage(role="user", content="Apply"),),
        attempts=(),
        rounds_used=1,
    )
    with pytest.raises(ProviderMutationFinalizationError, match="pending"):
        ProviderMutationFinalizationRunner(
            _Owner(
                _runtime(
                    _LLM(
                        LLMResponse(
                            content="done",
                            model="test-model",
                            provider="test-provider",
                            finish_reason="stop",
                        )
                    )
                )
            ),
            Task(id="task", description="finalize"),
            validation=validation,
        )


def test_finalization_exposes_no_tools_and_returns_final_response(monkeypatch):
    response = LLMResponse(
        content="Mutation and validation completed.",
        model="test-model",
        provider="test-provider",
        finish_reason="stop",
    )
    llm = _LLM(response)
    owner = _Owner(_runtime(llm))
    result = ProviderMutationFinalizationRunner(
        owner,
        Task(id="task", description="finalize"),
        validation=_validation(response),
    ).run()

    assert result.success is True
    assert result.transition.action is ProviderFinalResponseAction.COMPLETE
    assert llm.requests[0].tools == []
    assert llm.requests[0].tool_choice is None


def test_finalization_rejects_provider_tool_call(monkeypatch):
    response = LLMResponse(
        content="",
        tool_calls=[
            LLMToolCall(
                id="unexpected",
                function=LLMToolFunctionCall(
                    name="command_executor",
                    arguments="{}",
                ),
            )
        ],
        model="test-model",
        provider="test-provider",
        finish_reason="tool_calls",
    )
    llm = _LLM(response)
    result = ProviderMutationFinalizationRunner(
        _Owner(_runtime(llm)),
        Task(id="task", description="finalize"),
        validation=_validation(response),
    ).run()

    assert result.success is False
    assert result.error_message == "ProviderToolFinalizationToolCall"
