"""Strict experiment-local E2/E3 controller boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from .contracts import (
    ActionEffect,
    ActiveState,
    Controllability,
    Decision,
    DecisionKind,
    MeasurementResult,
    SignalStatus,
    Usage,
)


CONTROLLER_SCHEMA_VERSION = "mini-swe-active-controller-0.1"
CONTROLLER_INSTRUCTIONS = """
Use only the supplied task messages, public tool observations, active state, and
budget. Return exactly one JSON object with no extra fields:
{
  "action_effect": null or {
    "source_message_id": "message-N",
    "effect": "applied|no_effect|partial|unknown"
  },
  "measurements": [{
    "condition_id": "stable-kebab-case-id",
    "status": "met|unmet|unknown",
    "source_message_ids": ["message-N"],
    "controllability": "controllable|uncontrollable|unknown"
  }],
  "decision": {
    "kind": "measure|act|verify|recover|stop|delegate",
    "reason": "specific reason grounded in visible messages",
    "command": "one Bash command or null",
    "metadata": {}
  }
}
First reconcile any observed prior action effect, then record only measurements
supported by cited public messages, then choose one next decision. Cite only
message_id values present in the request. MEASURE and VERIFY use non-mutating
Bash. ACT and RECOVER may make a scoped task-workspace mutation. These four
decision kinds require a command. DELEGATE has no command and gives the stock
mini-SWE model one native step; use it when ordinary model reasoning or final
submission is appropriate. STOP has no command and is only for exhausted
budget, external blocking, or unrecoverable failure. STOP never claims task or
hidden-evaluator success. Never request, infer, or mention hidden evaluator
content.
""".strip()


class ControllerFormatError(ValueError):
    """The controller response cannot safely drive the experiment."""


class ControllerProviderError(ControllerFormatError):
    """A billable controller provider call failed before a valid response."""

    def __init__(
        self,
        message: str,
        *,
        usage: Usage,
        raw_response: str = "",
        response_metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.usage = usage
        self.raw_response = raw_response
        self.response_metadata = dict(response_metadata or {})


@dataclass(frozen=True)
class ControllerModelResult:
    content: str
    usage: Usage
    response_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ControllerReceipt:
    sequence: int
    request_sha256: str
    response_sha256: str
    raw_response: str
    usage: Usage
    provider_response: dict[str, Any]


class ControllerModel(Protocol):
    def query(self, *, payload: dict[str, Any]) -> ControllerModelResult: ...


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ActionEffectUpdate(_StrictModel):
    source_message_id: str
    effect: ActionEffect


class _MeasurementUpdate(_StrictModel):
    condition_id: str
    status: SignalStatus
    source_message_ids: tuple[str, ...]
    controllability: Controllability


class _DecisionPayload(_StrictModel):
    kind: DecisionKind
    reason: str
    command: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_command(self) -> "_DecisionPayload":
        command_kinds = {
            DecisionKind.MEASURE,
            DecisionKind.ACT,
            DecisionKind.VERIFY,
            DecisionKind.RECOVER,
        }
        if self.kind in command_kinds and not (self.command or "").strip():
            raise ValueError(f"{self.kind.value} requires a command")
        if self.kind in {DecisionKind.STOP, DecisionKind.DELEGATE} and self.command:
            raise ValueError(f"{self.kind.value} must not include a command")
        return self


class _ControllerEnvelope(_StrictModel):
    action_effect: _ActionEffectUpdate | None
    measurements: tuple[_MeasurementUpdate, ...]
    decision: _DecisionPayload


class ActiveIterationController:
    """Turn native mini messages into strict state updates and one E3 decision."""

    def __init__(self, *, model: ControllerModel) -> None:
        self._model = model
        self.state = ActiveState()
        self.usage = Usage()
        self.receipts: list[ControllerReceipt] = []

    def _request_payload(
        self,
        messages: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], set[str]]:
        identified_messages = [
            {"message_id": f"message-{index}", **message}
            for index, message in enumerate(messages, start=1)
        ]
        message_ids = {
            message["message_id"] for message in identified_messages
        }
        active_state = [
            {
                "condition_id": signal.condition_id,
                "status": signal.status.value,
                "source_message_ids": list(signal.source_message_ids),
                "freshness": signal.freshness.value,
                "controllability": signal.controllability.value,
                "last_action_effect": signal.last_action_effect.value,
            }
            for signal in self.state.signals()
        ]
        return (
            {
                "schema_version": CONTROLLER_SCHEMA_VERSION,
                "instructions": CONTROLLER_INSTRUCTIONS,
                "messages": identified_messages,
                "active_state": active_state,
            },
            message_ids,
        )

    def decide(self, *, messages: tuple[dict[str, Any], ...]) -> Decision:
        payload, allowed_message_ids = self._request_payload(messages)
        request_bytes = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        request_sha256 = hashlib.sha256(request_bytes).hexdigest()
        try:
            result = self._model.query(payload=payload)
        except ControllerProviderError as exc:
            self.usage += exc.usage
            self.receipts.append(
                ControllerReceipt(
                    sequence=len(self.receipts) + 1,
                    request_sha256=request_sha256,
                    response_sha256=hashlib.sha256(
                        exc.raw_response.encode()
                    ).hexdigest(),
                    raw_response=exc.raw_response,
                    usage=exc.usage,
                    provider_response=(
                        exc.response_metadata
                        or {"error_type": type(exc).__name__}
                    ),
                )
            )
            raise ControllerFormatError(str(exc)) from exc
        if not isinstance(result, ControllerModelResult):
            raise ControllerFormatError(
                "controller model must return ControllerModelResult"
            )
        self.usage += result.usage
        self.receipts.append(
            ControllerReceipt(
                sequence=len(self.receipts) + 1,
                request_sha256=request_sha256,
                response_sha256=hashlib.sha256(
                    result.content.encode()
                ).hexdigest(),
                raw_response=result.content,
                usage=result.usage,
                provider_response=result.response_metadata,
            )
        )
        try:
            envelope = _ControllerEnvelope.model_validate_json(result.content)
        except ValidationError as exc:
            raise ControllerFormatError(
                f"invalid controller response: {exc}"
            ) from exc

        referenced_ids: set[str] = set()
        if envelope.action_effect is not None:
            referenced_ids.add(envelope.action_effect.source_message_id)
        for measurement in envelope.measurements:
            referenced_ids.update(measurement.source_message_ids)
        unknown_ids = sorted(referenced_ids - allowed_message_ids)
        if unknown_ids:
            raise ControllerFormatError(
                f"unknown source_message_id values: {unknown_ids}"
            )

        if envelope.action_effect is not None:
            self.state.record_mutation(
                source_message_id=envelope.action_effect.source_message_id,
                effect=envelope.action_effect.effect,
            )
        for measurement in envelope.measurements:
            self.state.record_measurement(
                MeasurementResult(
                    condition_id=measurement.condition_id,
                    status=measurement.status,
                    source_message_ids=measurement.source_message_ids,
                    controllability=measurement.controllability,
                )
            )
        return Decision(
            kind=envelope.decision.kind,
            reason=envelope.decision.reason,
            command=envelope.decision.command,
            metadata=envelope.decision.metadata,
        )
