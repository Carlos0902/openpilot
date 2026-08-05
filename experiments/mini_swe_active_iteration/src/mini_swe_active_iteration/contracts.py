"""Small, experiment-local contracts for phase-zero active iteration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class SignalStatus(str, Enum):
    MET = "met"
    UNMET = "unmet"
    UNKNOWN = "unknown"


class Freshness(str, Enum):
    VALID = "valid"
    INVALIDATED = "invalidated"
    UNKNOWN = "unknown"


class Controllability(str, Enum):
    CONTROLLABLE = "controllable"
    UNCONTROLLABLE = "uncontrollable"
    UNKNOWN = "unknown"


class ActionEffect(str, Enum):
    APPLIED = "applied"
    NO_EFFECT = "no_effect"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class DecisionKind(str, Enum):
    MEASURE = "measure"
    ACT = "act"
    VERIFY = "verify"
    RECOVER = "recover"
    STOP = "stop"
    DELEGATE = "delegate"


class ExperimentArm(str, Enum):
    ORDINARY = "ordinary"
    ACTIVE = "active_iteration"


@dataclass(frozen=True)
class Usage:
    """Arm-neutral usage accounting, including controller usage."""

    provider_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    iterations: int = 0
    wall_time_seconds: float = 0.0

    def __post_init__(self) -> None:
        values = (
            self.provider_calls,
            self.input_tokens,
            self.output_tokens,
            self.tool_calls,
            self.iterations,
            self.wall_time_seconds,
        )
        if any(value < 0 for value in values):
            raise ValueError("usage values must be non-negative")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: object) -> "Usage":
        if not isinstance(other, Usage):
            return NotImplemented
        return Usage(
            provider_calls=self.provider_calls + other.provider_calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            tool_calls=self.tool_calls + other.tool_calls,
            iterations=self.iterations + other.iterations,
            wall_time_seconds=max(
                self.wall_time_seconds,
                other.wall_time_seconds,
            ),
        )


@dataclass(frozen=True)
class BudgetLimits:
    """Common non-monetary limits shared by both experiment arms."""

    max_provider_calls: int | None = None
    max_total_tokens: int | None = None
    max_tool_calls: int | None = None
    max_iterations: int | None = None
    max_wall_time_seconds: float | None = None

    def __post_init__(self) -> None:
        values = (
            self.max_provider_calls,
            self.max_total_tokens,
            self.max_tool_calls,
            self.max_iterations,
            self.max_wall_time_seconds,
        )
        if any(value is not None and value < 0 for value in values):
            raise ValueError("budget limits must be non-negative")

    def exceeded_limits(self, usage: Usage) -> tuple[str, ...]:
        limits_and_values = (
            ("provider_calls", self.max_provider_calls, usage.provider_calls),
            ("total_tokens", self.max_total_tokens, usage.total_tokens),
            ("tool_calls", self.max_tool_calls, usage.tool_calls),
            ("iterations", self.max_iterations, usage.iterations),
            (
                "wall_time_seconds",
                self.max_wall_time_seconds,
                usage.wall_time_seconds,
            ),
        )
        return tuple(
            name
            for name, limit, value in limits_and_values
            if limit is not None and value > limit
        )


@dataclass(frozen=True)
class MeasurementResult:
    condition_id: str
    status: SignalStatus
    source_message_ids: tuple[str, ...]
    controllability: Controllability

    def __post_init__(self) -> None:
        if not self.condition_id.strip():
            raise ValueError("condition_id must not be blank")
        if not self.source_message_ids or any(
            not message_id.strip() for message_id in self.source_message_ids
        ):
            raise ValueError("measurement must reference non-blank message IDs")
        if len(set(self.source_message_ids)) != len(self.source_message_ids):
            raise ValueError("source_message_ids must be unique")


@dataclass(frozen=True)
class ActiveSignal:
    condition_id: str
    status: SignalStatus
    freshness: Freshness
    source_message_ids: tuple[str, ...]
    controllability: Controllability
    last_action_effect: ActionEffect


@dataclass
class ActiveState:
    """Minimal in-memory evidence state passed from E2 to E3."""

    _signals: dict[str, ActiveSignal] = field(default_factory=dict, init=False)

    def record_measurement(self, result: MeasurementResult) -> None:
        self._signals[result.condition_id] = ActiveSignal(
            condition_id=result.condition_id,
            status=result.status,
            freshness=Freshness.VALID,
            source_message_ids=result.source_message_ids,
            controllability=result.controllability,
            last_action_effect=ActionEffect.UNKNOWN,
        )

    def record_mutation(
        self,
        *,
        source_message_id: str,
        effect: ActionEffect = ActionEffect.UNKNOWN,
    ) -> None:
        if not source_message_id.strip():
            raise ValueError("source_message_id must not be blank")
        self._signals = {
            condition_id: ActiveSignal(
                condition_id=signal.condition_id,
                status=signal.status,
                freshness=Freshness.INVALIDATED,
                source_message_ids=signal.source_message_ids,
                controllability=signal.controllability,
                last_action_effect=effect,
            )
            for condition_id, signal in self._signals.items()
        }

    def signal(self, condition_id: str) -> ActiveSignal:
        return self._signals[condition_id]

    def signals(self) -> tuple[ActiveSignal, ...]:
        return tuple(self._signals[key] for key in sorted(self._signals))

    def effective_status(self, condition_id: str) -> SignalStatus:
        signal = self._signals.get(condition_id)
        if signal is None or signal.freshness is not Freshness.VALID:
            return SignalStatus.UNKNOWN
        return signal.status

    def can_claim_success(self, *, required_conditions: Iterable[str]) -> bool:
        conditions = tuple(required_conditions)
        return bool(conditions) and all(
            self.effective_status(condition_id) is SignalStatus.MET
            for condition_id in conditions
        )


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: str
    command: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("decision reason must not be blank")


@dataclass(frozen=True)
class ExperimentResult:
    arm: ExperimentArm
    messages: tuple[dict[str, Any], ...]
    commands: tuple[str, ...]
    submission: str
    agent_usage: Usage
    controller_usage: Usage
    total_usage: Usage
    evaluation: dict[str, Any]
    exit_status: str = ""
    trajectory: dict[str, Any] = field(default_factory=dict)
    final_files: dict[str, str | None] = field(default_factory=dict)
    cleanup_succeeded: bool = False
    hypothesis_evidence_eligible: bool = False
