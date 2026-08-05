"""Zero-provider same-source probe for the production Goal/Task pipeline.

Stage 5B must not jump from a Task Designer-only paid experiment to a claim
about the complete conversation architecture.  This experiment therefore
stays below transport: it constructs a real ``AutonomousIterationAgent``,
feeds it a typed ``SessionIngressState``, and records the requests assembled
by the production Goal Maker and Task Designer boundaries.  The current and
compact arms share one immutable source snapshot.  No ``complete`` call,
network call, project write, memory write, or downstream task execution is
allowed.

The request ``rendered_input_tokens`` field is an exact local-tokenizer
measurement, not Provider usage. Provider usage fields remain null and
``usage_observed`` remains false so this probe cannot accidentally be reported
as a paid canary result.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

import autonomous_iteration.agents.iteration_agent as iteration_agent_module
from autonomous_iteration.agents.iteration_agent import AutonomousIterationAgent
from autonomous_iteration.models import (
    EvaluationResult,
    ImprovementGoal,
    ProjectStateSnapshot,
)
from core.token_counting import default_deepseek_tokenizer_path
from memory.context_assembly.request_builder import build_context_candidate_request
from memory.session_ingress import SessionIngress
from memory.session_dialog import session_turn_ledger_hash
from metadata import (
    ContextCandidateKind,
    ContextRequestPurpose,
    ConversationIdentity,
    SessionIngressState,
    SessionTurn,
)
from stage12_session_compact_canary_admission import (
    ProjectionFeatureFlag,
    ProjectionPolicy,
    RuntimeKillSwitch,
)


class OfflinePipelineStop(RuntimeError):
    """A zero-provider safety gate stopped the probe before transport."""


class CompactFallbackReceipt(BaseModel):
    """Typed evidence that a compact assembly failure used the safe current arm."""

    model_config = ConfigDict(extra="forbid")

    failed_projection: ProjectionPolicy
    effective_projection: ProjectionPolicy
    reason_code: Literal["compact_projection_failed"]
    failed_execution_id: str = Field(min_length=1)
    fallback_execution_id: str = Field(min_length=1)
    source_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    session_turn_source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    session_constraints_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    project_environment_mode: Literal["read_only"] = "read_only"
    write_scope_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_calls: int = Field(default=0, ge=0)
    network_calls: int = Field(default=0, ge=0)
    project_mutations: int = Field(default=0, ge=0)


class OfflineContextLineageReceipt(BaseModel):
    """Typed same-source and authority evidence for one offline arm.

    ``project_environment_mode`` describes the ContextLoader boundary used by
    both arms.  The probe never grants a write capability; the write-scope
    digest is derived from the authoritative ingress constraint state so a
    fallback cannot silently widen the root-task scope.
    """

    model_config = ConfigDict(extra="forbid")

    source_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    session_turn_source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    session_constraints_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    project_environment_mode: Literal["read_only"] = "read_only"
    write_scope_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class OfflineRequestObservation(BaseModel):
    """One model-facing request measured without claiming Provider usage."""

    model_config = ConfigDict(extra="forbid")

    execution_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    projection_policy: ProjectionPolicy
    source_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    # ``rendered_input_tokens`` is an exact local-tokenizer measurement.  The
    # Provider fields intentionally remain null: this probe never transports a
    # request, so unknown usage must not be represented as zero.
    rendered_input_tokens: int = Field(ge=0)
    provider_input_tokens: int | None = Field(default=None, ge=0)
    provider_output_tokens: int | None = Field(default=None, ge=0)
    provider_total_tokens: int | None = Field(default=None, ge=0)
    token_scope: Literal["rendered_prompt_only"] = "rendered_prompt_only"
    usage_observed: bool = False
    transport_attempted: bool = False
    constraint_recall: float = Field(ge=0.0, le=1.0)
    dialog_recall: float = Field(ge=0.0, le=1.0)
    dialog_source_ids: list[str] = Field(default_factory=list)
    session_turn_source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    stop_reason: str = "provider_transport_forbidden"


class _OfflineArm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_projection: ProjectionPolicy
    effective_projection: ProjectionPolicy
    requests: list[OfflineRequestObservation] = Field(default_factory=list)
    provider_calls: int = 0
    fallback_count: int = 0
    constraint_recall: float = Field(ge=0.0, le=1.0)
    dialog_recall: float = Field(ge=0.0, le=1.0)
    lineage_receipt: OfflineContextLineageReceipt
    fallback_receipt: CompactFallbackReceipt | None = None
    stop_reasons: list[str] = Field(default_factory=list)


@dataclass
class _OfflineHarness:
    agent: AutonomousIterationAgent
    session_state: SessionIngressState
    source_snapshot_hash: str
    projection: ProjectionPolicy
    observations: list[OfflineRequestObservation]

    def complete_candidates(
        self,
        candidates: list[Any],
        *,
        purpose: ContextRequestPurpose,
        **_: Any,
    ) -> tuple[dict[str, Any], set[str]]:
        """Assemble through production code, then return a deterministic fixture."""

        request = build_context_candidate_request(
            self.agent.llm_client,
            candidates=candidates,
            purpose=purpose,
            response_format="json_object",
            temperature=0.2,
            transport_retries=0,
        )
        selection = request.context_selection
        if selection is None or selection.final_prompt_tokens is None:
            raise OfflinePipelineStop("exact rendered-prompt token count is unavailable")
        decisions = list(selection.candidate_decisions)
        constraint_decisions = [
            item
            for item in decisions
            if item.kind == ContextCandidateKind.CONSTRAINT
            and str(item.candidate_id).startswith("session_constraints:")
        ]
        recall = float(
            all(item.action == "kept" for item in constraint_decisions)
            and len(constraint_decisions) == 1
        )
        expected_dialog_ids = {
            turn.message_id for turn in self.session_state.turns
        }
        dialog_decisions = [
            item for item in decisions if item.kind == ContextCandidateKind.DIALOG
        ]
        selected_dialog_ids = {
            str(item.source_id)
            for item in dialog_decisions
            if item.action in {"kept", "partially_kept"}
            and item.source_id is not None
        }
        dialog_recall = float(expected_dialog_ids <= selected_dialog_ids)
        dialog_source_ids = sorted(selected_dialog_ids)
        turn_source_hash = session_turn_ledger_hash(self.session_state)
        prompt_payload = [message.model_dump(mode="json") for message in request.messages]
        request_hash = _hash(
            {
                "messages": prompt_payload,
                "purpose": purpose.value,
                "projection": self.projection.value,
                "source": self.source_snapshot_hash,
            }
        )
        self.observations.append(
            OfflineRequestObservation(
                execution_id=f"offline:{self.projection.value}:{len(self.observations) + 1}",
                purpose=purpose.value,
                projection_policy=self.projection,
                source_snapshot_hash=self.source_snapshot_hash,
                context_request_hash=request_hash,
                rendered_input_tokens=int(selection.final_prompt_tokens),
                provider_input_tokens=None,
                provider_output_tokens=None,
                provider_total_tokens=None,
                usage_observed=False,
                transport_attempted=False,
                constraint_recall=recall,
                dialog_recall=dialog_recall,
                dialog_source_ids=dialog_source_ids,
                session_turn_source_hash=turn_source_hash,
            )
        )
        retained_ids = {
            str(item.candidate_id)
            for item in decisions
            if str(item.action) not in {"omitted", "ContextCandidateAction.OMITTED"}
        }
        if purpose == ContextRequestPurpose.ITERATION_GOAL:
            return (
                {
                    "goals": [
                        {
                            "title": "Improve one bounded project behavior",
                            "category": "robustness",
                            "rationale": "Offline pipeline probe fixture",
                            "acceptance_criteria": [
                                "The selected target remains within the active session scope."
                            ],
                            "priority": "high",
                        }
                    ]
                },
                retained_ids,
            )
        return (
            {
                "task": {
                    "description": "Inspect calculator.py within the active session scope.",
                    "target_files": ["calculator.py"],
                    "acceptance_criteria": [
                        "The selected target remains within the active session scope."
                    ],
                    "risk_notes": [],
                    "evidence_ids": [],
                }
            },
            retained_ids,
        )


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _write_scope_hash(state: SessionIngressState) -> str:
    """Hash only the active typed write scope, not free-form dialog prose."""

    scopes: list[dict[str, list[str]]] = []
    for entry in state.session_constraints.active_entries:
        category = getattr(entry.category, "value", entry.category)
        if category != "write_scope":
            continue
        scopes.append(
            {
                "allowed_files": sorted(entry.value.allowed_files),
                "forbidden_files": sorted(entry.value.forbidden_files),
            }
        )
    return _hash(sorted(scopes, key=lambda item: json.dumps(item, sort_keys=True)))


def _offline_llm() -> Any:
    settings = SimpleNamespace(
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        tokenizer_path=str(default_deepseek_tokenizer_path()),
        context_max_prompt_tokens=4096,
        context_reserved_prompt_tokens=128,
    )

    class NoProvider:
        def __init__(self) -> None:
            self.settings = settings

        def complete(self, *_args: Any, **_kwargs: Any) -> Any:
            raise OfflinePipelineStop("provider transport is forbidden")

    return NoProvider()


def _ingress_state(project_root: str) -> SessionIngressState:
    identity = ConversationIdentity(
        conversation_id="stage5b-offline-conversation",
        run_id="stage5b-offline-run",
        turn_index=0,
        project_root=project_root,
    )
    state = SessionIngressState(identity=identity)
    state = SessionIngress.open_turn(
        state,
        SessionTurn(
            identity=identity.model_copy(update={"turn_index": 1}),
            message_id="stage5b-user-constraint",
            role="user",
            content=(
                "Only calculator.py may be modified. Do not modify README.md. "
                "The validation command must be `python -m pytest -q`. "
                "Preserve the existing public API."
            ),
        ),
    )
    for proposal in list(state.pending_proposals):
        state = SessionIngress.confirm_proposal(
            state,
            proposal_id=proposal.proposal_id,
            confirmation_turn=2,
        )
    state = SessionIngress.open_turn(
        state,
        SessionTurn(
            identity=state.identity.model_copy(update={"turn_index": 3}),
            message_id="stage5b-assistant-ack",
            role="assistant",
            content="I will preserve the confirmed session constraints while working.",
        ),
    )
    return state


def _source_snapshot(
    *,
    state: SessionIngressState,
    project_state: ProjectStateSnapshot,
    improvement_report: Mapping[str, Any],
    completed_iteration: int,
) -> str:
    return _hash(
        {
            "ingress": state.model_dump(mode="json"),
            "project_state": project_state.model_dump(mode="json"),
            "improvement_report": dict(improvement_report),
            "completed_iteration": completed_iteration,
        }
    )


def _project_state(project_root: str) -> ProjectStateSnapshot:
    return ProjectStateSnapshot(
        project_path=project_root,
        goal="Repair the calculator project",
        written_files=["calculator.py", "README.md"],
        safe_target_files=["calculator.py"],
        file_summaries=[
            {
                "path": f"module_{index}.py",
                "preview": "historical project evidence " * 20,
            }
            for index in range(1, 3)
        ],
        readme_summary="README evidence " * 20,
        memory_records=[
            {
                "id": f"memory-{index}",
                "type": "task",
                "content": "Historical autonomous iteration evidence " * 80,
                "tags": ["autonomous_iteration"],
                "attributes": {"goal": "Improve one bounded project behavior"},
            }
            for index in range(1, 4)
        ],
        validation_context={
            "run_command": "python -m pytest -q",
            "summary": "Current tests pass before the optional improvement.",
        },
    )


def _improvement_report() -> dict[str, Any]:
    return {
        "summary": "A bounded robustness improvement is available.",
        "selected_candidate": None,
        "recommended_actions": ["Inspect calculator.py within the active scope."],
        "diagnosis": {
            "summary": "Selected diagnosis is intentionally compacted only in the compact arm.",
            "selected_candidate": {
                "candidate_id": "offline-candidate-1",
                "title": "Bound calculator behavior",
                "dimension": "robustness",
                "rationale": "The calculator has one bounded behavior gap.",
                "acceptance_criteria": ["The public API remains unchanged."],
                "target_metrics": ["metric-1"],
                "dependencies": ["pytest"],
                "risks": ["Do not widen the write scope."],
                "evidence": ["offline-evidence-1"],
            },
            "success_metrics": [
                {
                    "metric_id": "metric-1",
                    "name": "validation",
                    "dimension": "robustness",
                    "metric_type": "test",
                    "target": "pytest passes",
                    "current_assessment": "baseline passes",
                    "evidence": ["offline-evidence-1"],
                    "required": True,
                    "satisfied": True,
                }
            ],
        },
    }


def _new_agent() -> AutonomousIterationAgent:
    class Evaluator:
        llm_client = None

    return AutonomousIterationAgent(
        Evaluator(),
        required_successful_improvements=1,
        max_iteration_attempts=1,
        llm_client=_offline_llm(),
    )


def _run_arm(
    *,
    projection: ProjectionPolicy,
    state: SessionIngressState,
    project_state: ProjectStateSnapshot,
    report: Mapping[str, Any],
    goal: str,
    source_hash: str,
    compact_projection_failure: bool,
) -> _OfflineArm:
    agent = _new_agent()
    observations: list[OfflineRequestObservation] = []
    harness = _OfflineHarness(agent, state, source_hash, projection, observations)
    agent._complete_json_candidates = harness.complete_candidates  # type: ignore[method-assign]
    original_builder = iteration_agent_module.build_iteration_task_design_candidates

    def build_with_policy(**kwargs: Any) -> list[Any]:
        if compact_projection_failure and projection == ProjectionPolicy.COMPACT:
            raise OfflinePipelineStop("compact projection assembly failed")
        return original_builder(**kwargs, projection_policy=projection.value)

    iteration_agent_module.build_iteration_task_design_candidates = build_with_policy
    stop_reasons: list[str] = []
    fallback_count = 0
    fallback_receipt: CompactFallbackReceipt | None = None
    effective_projection = projection
    lineage_receipt = OfflineContextLineageReceipt(
        source_snapshot_hash=source_hash,
        session_turn_source_hash=session_turn_ledger_hash(state),
        session_constraints_hash=state.session_constraints.canonical_hash,
        project_environment_mode="read_only",
        write_scope_hash=_write_scope_hash(state),
    )
    try:
        evaluation = EvaluationResult(
            validation_passed=True,
            runnable=True,
            has_blocking_bugs=False,
            summary="Current tests pass.",
            run_command="python -m pytest -q",
        )
        goals = agent._run_goal_maker(
            project_state,
            evaluation,
            dict(report),
            0,
            session_constraints=state.session_constraints,
            session_ingress_state=state,
        )
        selected_goal = goals[0]
        agent._run_task_designer(
            project_state,
            selected_goal,
            dict(report),
            0,
            session_constraints=state.session_constraints,
            session_ingress_state=state,
        )
    except OfflinePipelineStop as exc:
        if projection != ProjectionPolicy.COMPACT or "compact projection assembly failed" not in str(exc):
            raise
        stop_reasons.append("compact_projection_failed")
        fallback_count = 1
        effective_projection = ProjectionPolicy.CURRENT
        failed_execution_id = f"offline:{projection.value}:{len(observations) + 1}"
        harness.projection = ProjectionPolicy.CURRENT
        iteration_agent_module.build_iteration_task_design_candidates = original_builder
        # Re-run only the failed Task Designer request under the current policy;
        # the Goal Maker request remains the shared source observation.
        agent._run_task_designer(
            project_state,
            selected_goal,
            dict(report),
            0,
            session_constraints=state.session_constraints,
            session_ingress_state=state,
        )
        fallback_observation = observations[-1]
        if fallback_observation.projection_policy != ProjectionPolicy.CURRENT:
            raise OfflinePipelineStop("current fallback observation missing")
        fallback_receipt = CompactFallbackReceipt(
            failed_projection=projection,
            effective_projection=effective_projection,
            reason_code="compact_projection_failed",
            failed_execution_id=failed_execution_id,
            fallback_execution_id=fallback_observation.execution_id,
            source_snapshot_hash=source_hash,
            session_turn_source_hash=fallback_observation.session_turn_source_hash,
            session_constraints_hash=state.session_constraints.canonical_hash,
            project_environment_mode=lineage_receipt.project_environment_mode,
            write_scope_hash=lineage_receipt.write_scope_hash,
            provider_calls=0,
            network_calls=0,
            project_mutations=0,
        )
    finally:
        iteration_agent_module.build_iteration_task_design_candidates = original_builder

    recall = sum(item.constraint_recall for item in observations) / len(observations)
    dialog_recall = sum(item.dialog_recall for item in observations) / len(observations)
    return _OfflineArm(
        requested_projection=projection,
        effective_projection=effective_projection,
        requests=observations,
        provider_calls=0,
        fallback_count=fallback_count,
        constraint_recall=recall,
        dialog_recall=dialog_recall,
        lineage_receipt=lineage_receipt,
        fallback_receipt=fallback_receipt,
        stop_reasons=stop_reasons,
    )


def run_offline_pipeline_probe(
    *,
    feature_flag: ProjectionFeatureFlag = ProjectionFeatureFlag.CANARY_ENABLED,
    kill_switch: RuntimeKillSwitch = RuntimeKillSwitch.ARMED,
    compact_projection_failure: bool = False,
    transport: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run the same-source Goal/Task probe with every transport path closed."""

    if transport is not None:
        raise OfflinePipelineStop("provider transport is forbidden in the offline probe")
    stop_reasons: list[str] = []
    if feature_flag != ProjectionFeatureFlag.CANARY_ENABLED:
        stop_reasons.append("projection_feature_flag_disabled")
    if kill_switch != RuntimeKillSwitch.ARMED:
        stop_reasons.append("runtime_kill_switch_engaged")
    if stop_reasons:
        return {
            "status": "stopped",
            "claim_boundary": "goal_and_task_projection_only",
            "production_entry_exercised": False,
            "provider_calls": 0,
            "network_calls": 0,
            "project_mutations": 0,
            "requests": [],
            "stop_reasons": stop_reasons,
        }

    with tempfile.TemporaryDirectory(prefix="openpilot-stage5b-offline-") as raw_root:
        root = Path(raw_root).resolve()
        state = _ingress_state(str(root))
        project_state = _project_state(str(root))
        report = _improvement_report()
        source_hash = _source_snapshot(
            state=state,
            project_state=project_state,
            improvement_report=report,
            completed_iteration=0,
        )
        arms = {
            projection.value: _run_arm(
                projection=projection,
                state=state,
                project_state=project_state,
                report=report,
                goal="Repair the calculator project",
                source_hash=source_hash,
                compact_projection_failure=compact_projection_failure,
            )
            for projection in (ProjectionPolicy.CURRENT, ProjectionPolicy.COMPACT)
        }

    all_requests = [
        item.model_dump(mode="json")
        for arm in arms.values()
        for item in arm.requests
    ]
    final_stop_reasons = [
        *stop_reasons,
        *[
            reason
            for arm in arms.values()
            for reason in arm.stop_reasons
        ],
    ]
    return {
        "status": "stopped" if final_stop_reasons else "passed",
        "claim_boundary": "goal_and_task_projection_only",
        "production_entry_exercised": False,
        "provider_calls": 0,
        "network_calls": 0,
        "project_mutations": 0,
        "same_source": len({item["source_snapshot_hash"] for item in all_requests}) == 1,
        "source_snapshot_hash": source_hash,
        "session_constraints_active": len(state.session_constraints.active_entries),
        "requests": all_requests,
        "stop_reasons": final_stop_reasons,
        "arms": {
            key: value.model_dump(mode="json") for key, value in arms.items()
        },
        "lineage_receipts": {
            key: value.lineage_receipt.model_dump(mode="json")
            for key, value in arms.items()
        },
    }


__all__ = [
    "CompactFallbackReceipt",
    "OfflineContextLineageReceipt",
    "OfflinePipelineStop",
    "run_offline_pipeline_probe",
]
