"""Narrow adapter from mini-SWE native agents to experiment run contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import BudgetLimits, Usage
from .mini_agent import ActiveIterationAgent, BudgetedDefaultAgent


@dataclass(frozen=True)
class MiniAgentRun:
    messages: tuple[dict[str, Any], ...]
    commands: tuple[str, ...]
    usage: Usage
    submission: str
    exit_status: str
    trajectory: dict[str, Any]


class MiniAgentAdapter:
    """Create one fresh native mini agent for one task-arm run."""

    def __init__(
        self,
        *,
        model: Any,
        environment: Any,
        budget_limits: BudgetLimits,
        agent_kwargs: dict[str, Any],
    ) -> None:
        self._model = model
        self._environment = environment
        self._budget_limits = budget_limits
        self._agent_kwargs = dict(agent_kwargs)

    def run(
        self,
        *,
        task: str,
        controller: Any | None = None,
    ) -> MiniAgentRun:
        if controller is None:
            agent = BudgetedDefaultAgent(
                self._model,
                self._environment,
                budget_limits=self._budget_limits,
                **self._agent_kwargs,
            )
        else:
            agent = ActiveIterationAgent(
                self._model,
                self._environment,
                controller=controller,
                budget_limits=self._budget_limits,
                **self._agent_kwargs,
            )
        try:
            run_result = agent.run(task)
        except Exception as exc:
            if not agent.messages:
                agent.add_messages(
                    self._model.format_message(
                        role="exit",
                        content=type(exc).__name__,
                        extra={
                            "exit_status": type(exc).__name__,
                            "submission": "",
                            "exception_type": type(exc).__name__,
                        },
                    )
                )
            last_message = agent.messages[-1]
            run_result = last_message.get("extra", {})

        commands = tuple(
            action["command"]
            for message in agent.messages
            for action in message.get("extra", {}).get("actions", [])
            if isinstance(action, dict) and isinstance(action.get("command"), str)
        )
        return MiniAgentRun(
            messages=tuple(agent.messages),
            commands=commands,
            usage=agent.usage,
            submission=str(run_result.get("submission", "")),
            exit_status=str(run_result.get("exit_status", "")),
            trajectory=agent.serialize(),
        )
