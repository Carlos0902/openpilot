"""Minimal E2-to-E3 bridge used by the phase-zero experiment."""

from __future__ import annotations

from typing import Any, Protocol

from .contracts import ActionEffect, ActiveState, Decision, MeasurementResult


class E3Controller(Protocol):
    def decide(
        self,
        *,
        state: ActiveState,
        messages: tuple[dict[str, Any], ...],
    ) -> Decision: ...


class ActiveIterationPolicy:
    """Keep only the evidence state required for the next E3 decision."""

    def __init__(self, *, e3: E3Controller) -> None:
        self.state = ActiveState()
        self._e3 = e3

    def record_measurement(self, result: MeasurementResult) -> None:
        self.state.record_measurement(result)

    def record_mutation(
        self,
        *,
        source_message_id: str,
        effect: ActionEffect = ActionEffect.UNKNOWN,
    ) -> None:
        self.state.record_mutation(
            source_message_id=source_message_id,
            effect=effect,
        )

    def decide(self, *, messages: tuple[dict[str, Any], ...]) -> Decision:
        return self._e3.decide(state=self.state, messages=messages)
