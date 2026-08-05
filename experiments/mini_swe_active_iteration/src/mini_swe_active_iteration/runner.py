"""Arm-neutral phase-zero coordinator.

The coordinator depends on a narrow adapter so its safety and accounting
contracts can be tested without imitating mini-SWE-agent internals.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from .contracts import ExperimentArm, ExperimentResult, Usage


class AgentRun(Protocol):
    messages: tuple[dict[str, Any], ...]
    commands: tuple[str, ...]
    usage: Usage
    submission: str
    exit_status: str
    trajectory: dict[str, Any]


class AgentAdapter(Protocol):
    def run(self, *, task: str, controller: Any | None = None) -> AgentRun: ...


class HiddenEvaluator(Protocol):
    def evaluate(self, *, submission: str) -> dict[str, Any]: ...


class ExperimentRunner:
    """Create a fresh agent per run and keep evaluation outside agent history."""

    def __init__(
        self,
        *,
        agent_factory: Callable[[], AgentAdapter],
        controller_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._agent_factory = agent_factory
        self._controller_factory = controller_factory

    def run(
        self,
        *,
        task: str,
        arm: ExperimentArm,
        hidden_evaluator: HiddenEvaluator | None = None,
    ) -> ExperimentResult:
        agent = self._agent_factory()
        controller: Any | None = None
        controller_usage = Usage()
        if arm is ExperimentArm.ACTIVE:
            if self._controller_factory is None:
                raise ValueError("active arm requires a controller_factory")
            controller = self._controller_factory()

        agent_run = agent.run(task=task, controller=controller)
        if controller is not None:
            raw_usage = getattr(controller, "usage", None)
            if not isinstance(raw_usage, Usage):
                raise ValueError("active controller must expose complete Usage")
            controller_usage = raw_usage

        evaluation = (
            hidden_evaluator.evaluate(submission=agent_run.submission)
            if hidden_evaluator is not None
            else {}
        )
        return ExperimentResult(
            arm=arm,
            messages=agent_run.messages,
            commands=agent_run.commands,
            submission=agent_run.submission,
            agent_usage=agent_run.usage,
            controller_usage=controller_usage,
            total_usage=agent_run.usage + controller_usage,
            evaluation=evaluation,
            exit_status=getattr(agent_run, "exit_status", ""),
            trajectory=getattr(agent_run, "trajectory", {}),
        )
