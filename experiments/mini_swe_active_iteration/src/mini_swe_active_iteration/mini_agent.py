"""Minimal mini-SWE-agent subclass used for phase-zero conformance.

Only the DELEGATE path is enabled in phase zero. Other active decisions remain
fail-closed until their execution and accounting contracts have tests.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from minisweagent.agents.default import DefaultAgent

from .contracts import BudgetLimits, Decision, DecisionKind, Usage
from .controller import ControllerFormatError


class ActiveController(Protocol):
    usage: Usage

    def decide(self, *, messages: tuple[dict[str, Any], ...]) -> Decision: ...


def _usage_payload(usage: Usage) -> dict[str, int | float]:
    return {
        "provider_calls": usage.provider_calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "tool_calls": usage.tool_calls,
        "iterations": usage.iterations,
        "wall_time_seconds": usage.wall_time_seconds,
        "total_tokens": usage.total_tokens,
    }


def _response_tokens(message: dict[str, Any]) -> tuple[int, int]:
    response = message.get("extra", {}).get("response", {})
    if not isinstance(response, dict):
        return 0, 0
    usage = response.get("usage", {})
    if not isinstance(usage, dict):
        return 0, 0
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    output_tokens = usage.get(
        "output_tokens",
        usage.get("completion_tokens", 0),
    )
    return int(input_tokens or 0), int(output_tokens or 0)


class BudgetedDefaultAgent(DefaultAgent):
    """Stock mini control flow with arm-neutral non-monetary budget checks."""

    def __init__(
        self,
        model,
        env,
        *,
        budget_limits: BudgetLimits | None = None,
        **kwargs,
    ) -> None:
        super().__init__(model, env, **kwargs)
        self.budget_limits = budget_limits
        self._executed_tool_calls = 0

    def _native_usage(self, *, iterations: int | None = None) -> Usage:
        input_tokens = 0
        output_tokens = 0
        for message in self.messages:
            message_input, message_output = _response_tokens(message)
            input_tokens += message_input
            output_tokens += message_output
        return Usage(
            provider_calls=self.n_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=self._executed_tool_calls,
            iterations=self.n_calls if iterations is None else iterations,
            wall_time_seconds=max(0.0, time.time() - self._start_time),
        )

    def _budget_terminal(
        self,
        exceeded: tuple[str, ...],
        *,
        decision_kind: DecisionKind | None = None,
    ) -> list[dict]:
        extra: dict[str, Any] = {
            "exit_status": "BudgetExhausted",
            "submission": "",
            "exceeded_limits": list(exceeded),
        }
        if decision_kind is not None:
            extra["decision_kind"] = decision_kind.value
        return self.add_messages(
            self.model.format_message(
                role="exit",
                content="BudgetExhausted",
                extra=extra,
            )
        )

    def _exceeded(self, usage: Usage) -> tuple[str, ...]:
        if self.budget_limits is None:
            return ()
        return self.budget_limits.exceeded_limits(usage)

    def step(self) -> list[dict]:
        projected_query = self._native_usage(
            iterations=self.n_calls + 1
        ) + Usage(provider_calls=1)
        if exceeded := self._exceeded(projected_query):
            return self._budget_terminal(exceeded)

        message = self.query()
        actions = message.get("extra", {}).get("actions", [])
        projected_execution = self._native_usage() + Usage(
            tool_calls=len(actions) if isinstance(actions, list) else 0
        )
        if exceeded := self._exceeded(projected_execution):
            return self._budget_terminal(exceeded)
        return self.execute_actions(message)

    def execute_actions(self, message: dict) -> list[dict]:
        actions = message.get("extra", {}).get("actions", [])
        if isinstance(actions, list):
            self._executed_tool_calls += len(actions)
        return super().execute_actions(message)

    @property
    def usage(self) -> Usage:
        return self._native_usage()


class ActiveIterationAgent(BudgetedDefaultAgent):
    """Preserve the native loop while recording an active-control sidecar."""

    def __init__(
        self,
        model,
        env,
        *,
        controller: ActiveController,
        budget_limits: BudgetLimits | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            model,
            env,
            budget_limits=budget_limits,
            **kwargs,
        )
        self.controller = controller
        self.active_decisions: list[Decision] = []
        self._decision_usage: list[Usage] = []

    def _combined_usage(self, *, iterations: int | None = None) -> Usage:
        native_usage = self._native_usage(
            iterations=(
                len(self.active_decisions)
                if iterations is None
                else iterations
            )
        )
        controller_usage = self.controller.usage
        if not isinstance(controller_usage, Usage):
            raise ValueError("active controller must expose complete Usage")
        return native_usage + controller_usage

    @property
    def usage(self) -> Usage:
        """Report active control rounds without double-counting controller cost."""
        return self._native_usage(iterations=len(self.active_decisions))

    def _budget_exhausted(
        self,
        *,
        decision: Decision,
        usage: Usage,
    ) -> list[dict] | None:
        projected = usage
        if decision.kind is DecisionKind.DELEGATE:
            projected += Usage(provider_calls=1)
        elif decision.command:
            projected += Usage(tool_calls=1)
        exceeded = self._exceeded(projected)
        if not exceeded:
            return None
        return self._budget_terminal(
            exceeded,
            decision_kind=decision.kind,
        )

    def step(self) -> list[dict]:
        try:
            decision = self.controller.decide(messages=tuple(self.messages))
        except ControllerFormatError as exc:
            return self.add_messages(
                self.model.format_message(
                    role="exit",
                    content="InvalidActiveDecision",
                    extra={
                        "exit_status": "InvalidActiveDecision",
                        "submission": "",
                        "exception_str": str(exc),
                    },
                )
            )
        self.active_decisions.append(decision)
        usage = self._combined_usage(iterations=len(self.active_decisions))
        self._decision_usage.append(usage)
        budget_stop = self._budget_exhausted(decision=decision, usage=usage)
        if budget_stop is not None:
            return budget_stop
        if decision.kind is DecisionKind.DELEGATE:
            message = self.query()
            actions = message.get("extra", {}).get("actions", [])
            projected = self._combined_usage() + Usage(
                tool_calls=len(actions) if isinstance(actions, list) else 0
            )
            if exceeded := self._exceeded(projected):
                return self._budget_terminal(
                    exceeded,
                    decision_kind=decision.kind,
                )
            return self.execute_actions(message)
        if decision.kind is DecisionKind.STOP:
            return self.add_messages(
                self.model.format_message(
                    role="exit",
                    content="ActiveStopped",
                    extra={
                        "exit_status": "ActiveStopped",
                        "submission": "",
                        "decision_kind": decision.kind.value,
                    },
                )
            )
        if not decision.command:
            return self.add_messages(
                self.model.format_message(
                    role="exit",
                    content="InvalidActiveDecision",
                    extra={
                        "exit_status": "InvalidActiveDecision",
                        "submission": "",
                        "decision_kind": decision.kind.value,
                    },
                )
            )
        message = self.model.format_message(
            role="assistant",
            content=decision.reason,
            extra={
                "actions": [{"command": decision.command}],
                "active_decision_kind": decision.kind.value,
                "cost": 0.0,
            },
        )
        self.add_messages(message)
        return self.execute_actions(message)

    def serialize(self, *extra_dicts) -> dict:
        usage = self.controller.usage
        if not isinstance(usage, Usage):
            raise ValueError("active controller must expose complete Usage")
        decisions = [
            {
                "sequence": sequence,
                "kind": decision.kind.value,
                "reason": decision.reason,
                "command": decision.command,
                "metadata": decision.metadata,
                "usage_after_decision": _usage_payload(decision_usage),
            }
            for sequence, (decision, decision_usage) in enumerate(
                zip(self.active_decisions, self._decision_usage, strict=True),
                start=1,
            )
        ]
        sidecar = {
            "experiment": {
                "active_iteration": {
                    "decisions": decisions,
                    "controller_usage": _usage_payload(usage),
                    "controller_receipts": [
                        {
                            "sequence": receipt.sequence,
                            "request_sha256": receipt.request_sha256,
                            "response_sha256": receipt.response_sha256,
                            "raw_response": receipt.raw_response,
                            "usage": _usage_payload(receipt.usage),
                            "provider_response": receipt.provider_response,
                        }
                        for receipt in getattr(
                            self.controller,
                            "receipts",
                            (),
                        )
                    ],
                    "hypothesis_evidence_eligible": False,
                }
            }
        }
        return super().serialize(sidecar, *extra_dicts)
