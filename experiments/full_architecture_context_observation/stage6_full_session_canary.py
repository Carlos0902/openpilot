"""Bounded post-core full-session context canary.

This is deliberately narrower than a claim about the whole ``execute`` path.
The normal runtime controller owns ingress and checkpointing, while an
experiment-owned read-only session executor exercises the production
ContextLoader, project-improvement analyzer, Goal Maker and paired Task
Designer boundaries.  No task executor is called and neither arm may mutate
the project or memory store.

The default is a zero-Provider dry run.  ``execute_provider=True`` is an
explicit, persistent-ledger-gated opt-in for one compact/current pair.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from autonomous_iteration.agents.context_loader import ContextLoaderAgent
from autonomous_iteration.agents.iteration_agent import AutonomousIterationAgent
from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from autonomous_iteration.models import EvaluationResult, ProjectStateSnapshot
from autonomous_iteration.project_improvement_context import (
    build_iteration_task_design_candidates,
)
from autonomous_iteration.tool.project_improvement_tool import (
    project_improvement_tool_executor,
)
from core.exceptions import InvalidLLMResponseError
from core.llm import LLMClient
from core.reasoning import resolve_reasoning_policy
from core.openpilot_log import OpenPilotLogger
from core.token_counting import ProviderTokenCounter, default_deepseek_tokenizer_path
from memory.context_builder import MemoryContextBuilder
from memory.context_projection import build_derived_context_projection
from memory.memory_store import MemoryStore
from memory.project_manager import ProjectManager
from memory.session_dialog import session_turn_ledger_hash
from metadata import (
    ContextCandidateKind,
    ConversationIdentity,
    EnhancementCompletionRequirement,
    ReasoningDecisionComplexity,
    SessionIngressState,
    SessionTurn,
    ToolInputMetadata,
)
from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks
from runtime_diagnostics.recorder import DiagnosticRecorder
from stage12_session_compact_canary_admission import (
    AdmissionError,
    CanaryCampaignLedger,
    DEFAULT_PROTOCOL_PATH,
    ProjectionFeatureFlag,
    ProjectionPolicy,
    RuntimeKillSwitch,
    SessionCompactCanaryAdmission,
    UsageObservation,
    UsageRoute,
    run_no_provider_preflight,
)
from stage7c_provider_attempt_telemetry import (
    AttemptStatus,
    DownstreamValidation,
    ProviderAttemptReceipt,
    ProviderAttemptTelemetryStop,
    RecoveryAction,
    build_attempt_receipt,
)
from stage13_session_compact_pipeline_offline import CompactFallbackReceipt, _write_scope_hash

import autonomous_iteration.agents.iteration_agent as iteration_agent_module


class FullSessionCanaryStop(RuntimeError):
    """The bounded canary stopped fail closed."""


class SourceBundle(BaseModel):
    """Immutable input identity shared by both paired arms."""

    model_config = ConfigDict(extra="forbid")

    source_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    session_turn_source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    session_constraints_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    project_manifest_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    conversation_id: str = Field(min_length=1)
    ingress_run_id: str = Field(min_length=1)
    project_root: str = Field(min_length=1)
    raw_turn_count: int = Field(ge=0)


class _DryRunClient:
    """Deterministic local response source; it is never Provider transport."""

    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            provider="offline_full_session_canary",
            model="offline-probe",
            base_url="https://provider.invalid",
            tokenizer_path=str(default_deepseek_tokenizer_path()),
            context_max_prompt_tokens=4096,
            context_reserved_prompt_tokens=128,
        )
        self.calls = 0
        self.requests: list[Any] = []

    def complete(self, request: Any, **_: Any) -> Any:
        self.calls += 1
        self.requests.append(request)
        purpose = str((getattr(request, "trace_info", {}) or {}).get("context_purpose") or "")
        if purpose == "project_improvement":
            payload = {
                "changed_signals": ["The bounded project workflow is ready for inspection."],
                "proposed_actions": ["Inspect calculator.py within the active session scope."],
                "next_decision_or_goal": "Inspect one bounded project behavior.",
                "must_satisfy": ["Preserve the existing public API."],
                "blocking_risks": [],
                "evidence_ids": [],
                "stack_preset_patch": {},
            }
        elif purpose == "iteration_goal":
            payload = {
                "goals": [{
                    "title": "Improve one bounded project behavior",
                    "category": "robustness",
                    "rationale": "Offline full-session canary fixture.",
                    "acceptance_criteria": ["The selected target remains in scope."],
                    "priority": "high",
                }]
            }
        else:
            payload = {
                "task": {
                    "description": "Inspect calculator.py within the active session scope.",
                    "target_files": ["calculator.py"],
                    "acceptance_criteria": ["The selected target remains in scope."],
                    "risk_notes": [],
                    "evidence_ids": [],
                }
            }
        return SimpleNamespace(
            parsed_json=payload,
            content=json.dumps(payload, ensure_ascii=False),
            usage={"prompt_tokens": 0, "completion_tokens": 24, "total_tokens": 24},
            finish_reason="stop",
            provider="offline_full_session_canary",
            model="offline-probe",
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


def _snapshot_tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def _make_ingress(project_root: Path) -> SessionIngressState:
    identity = ConversationIdentity(
        conversation_id="stage6-full-session-conversation",
        run_id="stage6-full-session-ingress-run",
        turn_index=0,
        project_root=str(project_root),
    )
    state = SessionIngressState(identity=identity)
    turns = [
        SessionTurn(
            identity=identity.model_copy(update={"turn_index": 1}),
            message_id="stage6-user-constraints",
            role="user",
            content=(
                "Only calculator.py may be modified. Do not modify README.md. "
                "The validation command must be `python -m pytest -q`. "
                "Preserve the existing public API."
            ),
        ),
        SessionTurn(
            identity=identity.model_copy(update={"turn_index": 2}),
            message_id="stage6-assistant-ack",
            role="assistant",
            content="I will preserve the confirmed scope and validation command.",
        ),
    ]
    for turn in turns:
        # SessionIngress is imported lazily to keep this module's public
        # surface experiment-only while using the normal reducer lifecycle.
        from memory.session_ingress import SessionIngress

        state = SessionIngress.open_turn(state, turn)
    for proposal in list(state.pending_proposals):
        from memory.session_ingress import SessionIngress

        state = SessionIngress.confirm_proposal(
            state,
            proposal_id=proposal.proposal_id,
            confirmation_turn=3,
        )
    # Add bounded assistant history so the production ContextLoader can expose
    # whether its segmented compaction sink is active without making the
    # history itself authoritative.
    for index in range(4, 11):
        from memory.session_ingress import SessionIngress

        state = SessionIngress.open_turn(
            state,
            SessionTurn(
                identity=state.identity.model_copy(update={"turn_index": index}),
                message_id=f"stage6-assistant-history-{index}",
                role="assistant",
                content=(
                    f"Bounded progress note {index}: keep evidence concise and scoped. "
                    + ("Historical observation detail. " * 140)
                )[:3200],
            ),
        )
    return state


def _project_state(project_root: Path, *, goal: str, run_command: str) -> ProjectStateSnapshot:
    files = [project_root / "calculator.py", project_root / "README.md"]
    summaries = []
    for path in files:
        summaries.append({"path": str(path), "name": path.name, "preview": path.read_text(encoding="utf-8")[:1000]})
    return ProjectStateSnapshot(
        project_path=str(project_root),
        goal=goal,
        written_files=[str(path) for path in files],
        safe_target_files=[str(project_root / "calculator.py")],
        file_summaries=summaries,
        readme_summary=(project_root / "README.md").read_text(encoding="utf-8")[:1000],
        run_command=run_command,
        validation_context={"summary": "Current tests pass.", "run_command": run_command},
    )


def _request_hash(request: Any) -> str:
    trace_info = getattr(request, "trace_info", {}) or {}
    candidate = str(trace_info.get("request_hash") or "")
    if candidate.startswith(("sha256:", "v2:sha256:")):
        return candidate
    return _hash(
        {
            "messages": [
                {"role": message.role, "content": message.content}
                for message in list(getattr(request, "messages", []) or [])
            ],
            "max_tokens": getattr(request, "max_tokens", None),
            "response_format": getattr(request, "response_format", None),
            "reasoning_policy": getattr(request, "reasoning_policy", None),
        }
    )


def _usage_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _complete_usage(usage: Mapping[str, Any]) -> tuple[int, int, int] | None:
    def get(*keys: str) -> int | None:
        for key in keys:
            if usage.get(key) is not None:
                return int(usage[key])
        return None

    input_tokens = get("input_tokens", "prompt_tokens")
    output_tokens = get("output_tokens", "completion_tokens")
    total_tokens = get("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    if None in (input_tokens, output_tokens, total_tokens):
        return None
    if total_tokens != input_tokens + output_tokens:
        raise ProviderAttemptTelemetryStop("provider usage total does not reconcile")
    return input_tokens, output_tokens, total_tokens


class _ObservedClient:
    """Attach typed attempt receipts and campaign reservations to one client."""

    def __init__(
        self,
        underlying: Any,
        *,
        arm: ProjectionPolicy,
        route: UsageRoute,
        source: SourceBundle,
        counter: ProviderTokenCounter,
        ledger: CanaryCampaignLedger | None,
        persist: Any,
        campaign_started: float,
        framing_reserve_tokens: int,
        completion_reserve_tokens: int,
        offline: bool,
        receipt_sink: list[ProviderAttemptReceipt] | None = None,
        request_sink: list[dict[str, Any]] | None = None,
    ) -> None:
        self.underlying = underlying
        self.settings = getattr(underlying, "settings", SimpleNamespace())
        self.arm = arm
        self.route = route
        self.source = source
        self.counter = counter
        self.ledger = ledger
        self.persist = persist
        self.campaign_started = campaign_started
        self.arm_started = time.monotonic()
        self.framing_reserve_tokens = framing_reserve_tokens
        self.completion_reserve_tokens = completion_reserve_tokens
        self.offline = offline
        self.receipt_sink = receipt_sink
        self.request_sink = request_sink
        self.receipts: list[ProviderAttemptReceipt] = []
        self.request_meta: list[dict[str, Any]] = []
        self._ordinal = 0

    def _reserve(self, request: Any, execution_id: str) -> int:
        if self.ledger is None:
            return 0
        campaign_elapsed = time.monotonic() - self.campaign_started
        arm_elapsed = time.monotonic() - self.arm_started
        if campaign_elapsed > self.ledger.hard_caps.campaign_wall_clock_seconds:
            raise FullSessionCanaryStop("campaign wall-clock hard cap exceeded before transport")
        if arm_elapsed > self.ledger.hard_caps.per_arm_wall_clock_seconds:
            raise FullSessionCanaryStop("arm wall-clock hard cap exceeded before transport")
        request_completion = int(getattr(request, "max_tokens", 0) or 0)
        if request_completion > self.completion_reserve_tokens:
            raise FullSessionCanaryStop("completion reserve exceeds canary ceiling before transport")
        prompt_tokens = int(getattr(getattr(request, "context_selection", None), "final_prompt_tokens", 0) or 0)
        if prompt_tokens <= 0:
            prompt_tokens = sum(self.counter.count_text(message.content) for message in list(getattr(request, "messages", []) or []))
        reserved = prompt_tokens + self.framing_reserve_tokens + int(getattr(request, "max_tokens", 0) or 0)
        self.ledger = self.ledger.reserve(
            execution_id=execution_id,
            arm=self.arm,
            route=self.route,
            reserved_tokens=reserved,
        )
        self.persist(self.ledger)
        return reserved

    def _request_metadata(
        self,
        request: Any,
        *,
        execution_id: str,
        request_hash: str,
        elapsed_seconds: float,
        status: str,
    ) -> dict[str, Any]:
        selection = getattr(request, "context_selection", None)
        decisions = list(getattr(selection, "candidate_decisions", []) or [])
        selected_candidate_ids = [
            str(getattr(item, "candidate_id", ""))
            for item in decisions
            if getattr(item, "action", "") in {"kept", "partially_kept"}
        ]
        requested_policy = getattr(request, "reasoning_policy", None)
        resolved_policy = (
            resolve_reasoning_policy(requested_policy, self.settings)
            if requested_policy is not None
            else None
        )
        return {
            "execution_id": execution_id,
            "arm": self.arm.value,
            "purpose": (getattr(request, "trace_info", {}) or {}).get("context_purpose"),
            "request_hash": request_hash,
            "source_snapshot_hash": self.source.source_snapshot_hash,
            "session_turn_source_hash": self.source.session_turn_source_hash,
            "rendered_input_tokens": int(getattr(selection, "final_prompt_tokens", 0) or 0),
            "selected_candidate_ids": selected_candidate_ids,
            "selected_compaction_candidate_ids": [
                item for item in selected_candidate_ids if item.startswith("compaction:")
            ],
            "selected_dialog_candidate_ids": [
                item for item in selected_candidate_ids
                if item.startswith(("dialog:", "session_dialog:"))
            ],
            "reasoning_requested_mode": requested_policy.mode.value if requested_policy is not None else None,
            "reasoning_effective_mode": resolved_policy.effective_mode.value if resolved_policy is not None else None,
            "reasoning_resolution": resolved_policy.resolution.value if resolved_policy is not None else None,
            "reasoning_profile_id": resolved_policy.profile_id.value if resolved_policy is not None else None,
            "reasoning_profile_version": resolved_policy.profile_version if resolved_policy is not None else None,
            "status": status,
            "elapsed_seconds": elapsed_seconds,
        }

    def complete(self, request: Any, **kwargs: Any) -> Any:
        self._ordinal += 1
        execution_id = f"stage6:{self.arm.value}:{self._ordinal}:{uuid.uuid4().hex[:8]}"
        self._reserve(request, execution_id)
        request_hash = _request_hash(request)
        started = time.monotonic()
        raw: dict[str, Any]
        try:
            response = self.underlying.complete(request, **kwargs)
            usage = _usage_mapping(getattr(response, "usage", None))
            raw = {
                "status": AttemptStatus.RESPONDED.value,
                "transport_attempted": not self.offline,
                "provider": getattr(response, "provider", None) or getattr(self.settings, "provider", ""),
                "model": getattr(response, "model", None) or getattr(self.settings, "model", ""),
                "endpoint": getattr(self.settings, "base_url", ""),
                "usage": usage,
                "finish_reason": getattr(response, "finish_reason", None),
                "content": getattr(response, "content", ""),
            }
            receipt = build_attempt_receipt(
                raw,
                attempt_id=execution_id,
                execution_id=execution_id,
                request_ordinal=self._ordinal,
                request_hash=request_hash,
                request_context_hash=self.source.source_snapshot_hash,
                session_turn_source_hash=self.source.session_turn_source_hash,
            )
            if self.ledger is not None:
                complete = _complete_usage(usage)
                if complete is None:
                    raise FullSessionCanaryStop("unknown provider usage stops the canary")
                self.ledger = self.ledger.reconcile(
                    UsageObservation(
                        execution_id=execution_id,
                        route=self.route,
                        account_id=(
                            self.ledger.accounting.primary_account_id
                            if self.route == UsageRoute.PRIMARY
                            else self.ledger.accounting.fallback_account_id
                        ),
                        input_tokens=complete[0],
                        output_tokens=complete[1],
                        total_tokens=complete[2],
                    ),
                    arm=self.arm,
                )
                self.persist(self.ledger)
            self.receipts.append(receipt)
            if self.receipt_sink is not None:
                self.receipt_sink.append(receipt)
            self.request_meta.append(self._request_metadata(
                request,
                execution_id=execution_id,
                request_hash=request_hash,
                elapsed_seconds=time.monotonic() - started,
                status="responded",
            ))
            if self.request_sink is not None:
                self.request_sink.append(dict(self.request_meta[-1]))
            return response
        except Exception as exc:
            usage = _usage_mapping(getattr(exc, "usage", None))
            details = getattr(exc, "details", None)
            if not usage and isinstance(details, Mapping):
                attempt = details.get("provider_attempt")
                usage = _usage_mapping(attempt.get("usage")) if isinstance(attempt, Mapping) else {}
            raw = {
                "status": AttemptStatus.FAILED.value,
                "transport_attempted": not self.offline,
                "provider": getattr(self.settings, "provider", ""),
                "model": getattr(self.settings, "model", ""),
                "endpoint": getattr(self.settings, "base_url", ""),
                "usage": usage,
                "finish_reason": getattr(exc, "finish_reason", None),
                "error_type": type(exc).__name__,
                "content": getattr(exc, "response_text", ""),
                "recovery_action": RecoveryAction.STOP.value,
            }
            receipt = build_attempt_receipt(
                raw,
                attempt_id=execution_id,
                execution_id=execution_id,
                request_ordinal=self._ordinal,
                request_hash=request_hash,
                request_context_hash=self.source.source_snapshot_hash,
                session_turn_source_hash=self.source.session_turn_source_hash,
            )
            if self.ledger is not None:
                complete = _complete_usage(usage)
                if complete is not None:
                    self.ledger = self.ledger.reconcile(
                        UsageObservation(
                            execution_id=execution_id,
                            route=self.route,
                            account_id=(
                                self.ledger.accounting.primary_account_id
                                if self.route == UsageRoute.PRIMARY
                                else self.ledger.accounting.fallback_account_id
                            ),
                            input_tokens=complete[0],
                            output_tokens=complete[1],
                            total_tokens=complete[2],
                        ),
                        arm=self.arm,
                    )
                    self.persist(self.ledger)
            self.receipts.append(receipt)
            if self.receipt_sink is not None:
                self.receipt_sink.append(receipt)
            self.request_meta.append(self._request_metadata(
                request,
                execution_id=execution_id,
                request_hash=request_hash,
                elapsed_seconds=time.monotonic() - started,
                status="failed",
            ))
            if self.request_sink is not None:
                self.request_sink.append(dict(self.request_meta[-1]))
            raise


def _campaign_meta_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.meta.json")


def _save_ledger(
    path: Path | None,
    ledger: CanaryCampaignLedger,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(ledger.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    if metadata is not None:
        meta_path = _campaign_meta_path(path)
        meta_temporary = meta_path.with_name(f".{meta_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            meta_temporary.write_text(json.dumps(dict(metadata), ensure_ascii=False, indent=2), encoding="utf-8")
            meta_temporary.replace(meta_path)
        finally:
            if meta_temporary.exists():
                meta_temporary.unlink()


def _load_ledger(path: Path, contract: SessionCompactCanaryAdmission) -> CanaryCampaignLedger:
    if not path.exists():
        return CanaryCampaignLedger(accounting=contract.usage_accounting, hard_caps=contract.hard_caps)
    try:
        ledger = CanaryCampaignLedger.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise FullSessionCanaryStop(f"campaign state is invalid: {exc}") from exc
    if ledger.accounting != contract.usage_accounting or ledger.hard_caps != contract.hard_caps:
        raise FullSessionCanaryStop("campaign state contract does not match admission")
    return ledger


def _quality_task(tasks: list[Any], state: SessionIngressState) -> tuple[bool, str | None, dict[str, Any]]:
    if len(tasks) != 1:
        return False, "task_count_not_one", {"count": len(tasks)}
    task = tasks[0]
    target_paths = [Path(str(item)) for item in getattr(task, "target_files", [])]
    project_root = Path(state.identity.project_root).resolve()
    allowed = {
        (project_root / Path(str(path))).resolve()
        if not Path(str(path)).is_absolute()
        else Path(str(path)).resolve()
        for entry in state.session_constraints.active_entries
        if entry.constraint_key == "write_scope"
        for path in entry.value.allowed_files
    }
    normalized_targets: set[Path] = set()
    for path in target_paths:
        candidate = path.resolve() if path.is_absolute() else (project_root / path).resolve()
        try:
            candidate.relative_to(project_root)
        except ValueError:
            return False, "target_scope_invalid", {"target_files": [str(item) for item in target_paths]}
        normalized_targets.add(candidate)
    if not normalized_targets or (allowed and not normalized_targets.issubset(allowed)):
        return False, "target_scope_invalid", {"target_files": sorted(str(item) for item in normalized_targets), "allowed": sorted(str(item) for item in allowed)}
    if not getattr(task, "acceptance_criteria", None):
        return False, "acceptance_criteria_missing", {}
    return True, None, {"target_files": sorted(str(item.relative_to(project_root)) for item in normalized_targets), "acceptance_criteria": list(task.acceptance_criteria)}


def run_full_session_canary(
    *,
    execute_provider: bool = False,
    feature_flag: ProjectionFeatureFlag = ProjectionFeatureFlag.CANARY_ENABLED,
    kill_switch: RuntimeKillSwitch = RuntimeKillSwitch.ARMED,
    client: Any | None = None,
    campaign_state_path: Path | None = None,
    completion_reserve_tokens: int = 2200,
    framing_reserve_tokens: int = 128,
    context_max_prompt_tokens: int = 4096,
    goal_reasoning_complexity: ReasoningDecisionComplexity | None = None,
) -> dict[str, Any]:
    """Run the bounded post-core full-session canary or its dry-run gate.

    ``goal_reasoning_complexity`` is an experiment-only treatment input.  The
    default keeps the production Goal route unchanged; a canary may select a
    provider-neutral routine route while retaining the same context and
    completion reservation policy.
    """

    if feature_flag != ProjectionFeatureFlag.CANARY_ENABLED:
        return {"status": "stopped", "stop_reasons": ["projection_feature_flag_disabled"], "provider_calls": 0}
    if kill_switch != RuntimeKillSwitch.ARMED:
        return {"status": "stopped", "stop_reasons": ["runtime_kill_switch_engaged"], "provider_calls": 0}
    admission = run_no_provider_preflight()
    if execute_provider and campaign_state_path is None:
        return {"status": "stopped", "stop_reasons": ["campaign_state_path_required"], "provider_calls": 0, "admission": admission}

    settings = getattr(client, "settings", None) if client is not None else None
    provider: Any
    if execute_provider:
        if client is None:
            provider = LLMClient(enable_cache=False)
            settings = provider.settings
        else:
            provider = client
        checker = getattr(settings, "is_ready", None)
        if callable(checker) and not checker():
            return {"status": "stopped", "stop_reasons": ["provider_settings_not_ready"], "provider_calls": 0, "admission": admission}
    else:
        provider = _DryRunClient()
    counter = ProviderTokenCounter.from_settings(getattr(provider, "settings", None))
    if execute_provider and not counter.available:
        raise FullSessionCanaryStop("exact provider tokenizer is unavailable")
    contract = SessionCompactCanaryAdmission.model_validate_json(DEFAULT_PROTOCOL_PATH.read_text(encoding="utf-8"))
    ledger = _load_ledger(campaign_state_path, contract) if execute_provider and campaign_state_path is not None else None
    campaign_started = time.monotonic()
    observations: list[dict[str, Any]] = []
    receipt_records: list[ProviderAttemptReceipt] = []
    request_records: list[dict[str, Any]] = []
    stop_reasons: list[str] = []

    with tempfile.TemporaryDirectory(prefix="openpilot-stage6-full-session-") as raw_root:
        root = Path(raw_root).resolve()
        project_root = root / "project"
        project_root.mkdir()
        (project_root / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (project_root / "README.md").write_text("Run `python -m pytest -q` and preserve the public API.\n", encoding="utf-8")
        memory_root = root / "memory"
        memory_root.mkdir()
        state = _make_ingress(project_root)
        goal_text = "Inspect the calculator project"
        run_command = "python -m pytest -q"
        project_state = _project_state(project_root, goal=goal_text, run_command=run_command)
        evaluation = EvaluationResult(
            validation_passed=True,
            runnable=True,
            has_blocking_bugs=False,
            summary="Current tests pass.",
            run_command=run_command,
            recommended_actions=["Inspect calculator.py within the active scope."],
        )
        project_before = _snapshot_tree(project_root)
        memory_before = _snapshot_tree(memory_root)
        turn_hash = session_turn_ledger_hash(state)
        source = SourceBundle(
            source_snapshot_hash=_hash({
                "ingress": state.model_dump(mode="json"),
                "project_state": project_state.model_dump(mode="json"),
                "evaluation": evaluation.model_dump(mode="json"),
            }),
            session_turn_source_hash=turn_hash,
            session_constraints_hash=state.session_constraints.canonical_hash,
            project_manifest_hash=_hash(project_before),
            conversation_id=state.identity.conversation_id,
            ingress_run_id=state.identity.run_id,
            project_root=str(project_root),
            raw_turn_count=len(state.turns),
        )
        campaign_metadata = {
            "canary_id": contract.canary_id,
            "protocol_sha256": admission.get("protocol_sha256"),
            "source_snapshot_hash": source.source_snapshot_hash,
            "session_turn_source_hash": source.session_turn_source_hash,
            "goal_reasoning_complexity": (
                goal_reasoning_complexity.value
                if goal_reasoning_complexity is not None
                else "default"
            ),
        }
        if execute_provider and campaign_state_path is not None and campaign_state_path.exists():
            meta_path = _campaign_meta_path(campaign_state_path)
            try:
                persisted_metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FullSessionCanaryStop("campaign state is missing a valid source identity envelope") from exc
            for key, expected in campaign_metadata.items():
                if persisted_metadata.get(key) != expected:
                    raise FullSessionCanaryStop(f"campaign state {key} does not match the immutable source bundle")

        hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(root / "diagnostics"))
        autopilot = IntelligentAutopilot(
            provider,
            logger=OpenPilotLogger(log_file=root / "autopilot.jsonl"),
            enable_iterative_improvement=False,
            required_successful_improvements=0,
            runtime_diagnostics_hooks=hooks,
        )
        temp_builder = MemoryContextBuilder(
            memory_store=MemoryStore(memory_root),
            project_manager=ProjectManager(project_root),
            max_prompt_tokens=context_max_prompt_tokens,
            token_counter=counter,
        )
        autopilot.memory_store = temp_builder.memory_store
        autopilot.memory_context_builder = temp_builder
        autopilot.iterative_improvement.memory_store = temp_builder.memory_store
        autopilot.iterative_improvement.memory_context_builder = temp_builder
        autopilot.runtime_controller._configure_prompt_context_checkpointing()

        persist = lambda value: _save_ledger(campaign_state_path, value, metadata=campaign_metadata)

        class _SessionExecutor:
            supports_session_cursor = True

            def run(self, goal: str, context: dict[str, Any], mode: str = "standard", **_: Any) -> dict[str, Any]:
                nonlocal ledger
                loader = ContextLoaderAgent(temp_builder)
                context_result = loader.run(goal, project_root, 0, session_ingress_state=state)
                context_projection = build_derived_context_projection(context_result, state)
                selection = context_result.get("context_selection") or {}
                decisions = list(selection.get("candidate_decisions") or [])
                retained = [item for item in decisions if item.get("action") in {"kept", "partially_kept"}]
                if not any(item.get("kind") == ContextCandidateKind.CONSTRAINT.value and str(item.get("candidate_id", "")).startswith("session_constraints:") for item in retained):
                    raise FullSessionCanaryStop("ContextLoader did not retain the required session constraint")
                shared_arm = ProjectionPolicy.CURRENT
                shared_route = UsageRoute.FALLBACK
                shared_client = _ObservedClient(
                    provider,
                    arm=shared_arm,
                    route=shared_route,
                    source=source,
                    counter=counter,
                    ledger=ledger,
                    persist=persist,
                    campaign_started=campaign_started,
                    framing_reserve_tokens=framing_reserve_tokens,
                    completion_reserve_tokens=completion_reserve_tokens,
                    offline=not execute_provider,
                    receipt_sink=receipt_records,
                    request_sink=request_records,
                )
                analysis_input = ToolInputMetadata.from_mapping(
                    "project_improvement_tool",
                    {
                        "project_path": str(project_root),
                        "goal": goal,
                        "written_files": [str(project_root / "calculator.py")],
                        "run_command": run_command,
                        "iteration": 0,
                        "validation_result": evaluation.model_dump(mode="json"),
                        "readme_path": str(project_root / "README.md"),
                        "prompt_context": {},
                        "session_turn_source_hash": source.session_turn_source_hash,
                        "_llm_client": shared_client,
                        "_session_constraints": state.session_constraints,
                        "_session_ingress_state": state,
                        "_context_projection": context_projection,
                        "_enhancement_required": bool(execute_provider),
                    },
                )
                analysis_result = project_improvement_tool_executor(analysis_input)
                report = analysis_result.result.to_json_dict() if analysis_result.result is not None else {}
                if not report:
                    raise FullSessionCanaryStop("project-improvement analyzer returned no report")
                ledger = shared_client.ledger

                goal_agent = AutonomousIterationAgent(SimpleNamespace(llm_client=shared_client), required_successful_improvements=1, max_iteration_attempts=1, llm_client=shared_client)
                goal_agent.enhancement_requirement = EnhancementCompletionRequirement.REQUIRED if execute_provider else EnhancementCompletionRequirement.OPTIONAL
                goals = goal_agent._run_goal_maker(
                    project_state,
                    evaluation,
                    report,
                    0,
                    session_constraints=state.session_constraints,
                    session_ingress_state=state,
                    context_projection=context_projection,
                    reasoning_complexity=goal_reasoning_complexity,
                )
                if not goals:
                    raise FullSessionCanaryStop("Goal Maker returned no goal")
                selected_goal = goals[0]
                ledger = shared_client.ledger

                arm_results: dict[str, Any] = {}
                fallback_receipt: dict[str, Any] | None = None
                arm_stop_reasons: list[str] = []
                original_builder = iteration_agent_module.build_iteration_task_design_candidates

                def run_task_arm(policy: ProjectionPolicy, route: UsageRoute) -> tuple[_ObservedClient, list[Any] | None, bool, str | None, dict[str, Any]]:
                    nonlocal ledger
                    route = UsageRoute.PRIMARY if policy == ProjectionPolicy.COMPACT else UsageRoute.FALLBACK
                    arm_client = _ObservedClient(
                        provider,
                        arm=policy,
                        route=route,
                        source=source,
                        counter=counter,
                        ledger=ledger,
                        persist=persist,
                        campaign_started=campaign_started,
                        framing_reserve_tokens=framing_reserve_tokens,
                        completion_reserve_tokens=completion_reserve_tokens,
                        offline=not execute_provider,
                        receipt_sink=receipt_records,
                        request_sink=request_records,
                    )
                    arm_agent = AutonomousIterationAgent(SimpleNamespace(llm_client=arm_client), required_successful_improvements=1, max_iteration_attempts=1, llm_client=arm_client)
                    arm_agent.enhancement_requirement = EnhancementCompletionRequirement.REQUIRED if execute_provider else EnhancementCompletionRequirement.OPTIONAL

                    def builder_with_policy(**kwargs: Any) -> list[Any]:
                        return original_builder(**kwargs, projection_policy=policy.value)

                    iteration_agent_module.build_iteration_task_design_candidates = builder_with_policy
                    try:
                        task_context_projection = (
                            context_projection
                            if policy == ProjectionPolicy.COMPACT
                            else None
                        )
                        tasks = arm_agent._run_task_designer(
                            project_state,
                            selected_goal,
                            report,
                            0,
                            session_constraints=state.session_constraints,
                            session_ingress_state=state,
                            context_projection=task_context_projection,
                        )
                        passed, reason, quality = _quality_task(tasks, state)
                        error: Exception | None = None
                    except Exception as exc:
                        tasks = None
                        passed, reason, quality = False, type(exc).__name__, {}
                        error = exc
                    finally:
                        iteration_agent_module.build_iteration_task_design_candidates = original_builder
                    ledger = arm_client.ledger
                    if error is not None and not arm_client.receipts:
                        raise error
                    if arm_client.receipts:
                        arm_client.receipts[-1] = arm_client.receipts[-1].model_copy(update={"downstream_validation": DownstreamValidation.PASSED if passed else DownstreamValidation.FAILED})
                    return arm_client, tasks, passed, reason, quality

                for policy in (ProjectionPolicy.COMPACT, ProjectionPolicy.CURRENT):
                    route = UsageRoute.PRIMARY if policy == ProjectionPolicy.COMPACT else UsageRoute.FALLBACK
                    arm_client, tasks, passed, reason, quality = run_task_arm(policy, route)
                    ledger = arm_client.ledger
                    arm_result = {
                        "requested_projection": policy.value,
                        "effective_projection": policy.value,
                        "quality_passed": passed,
                        "quality_reason": reason,
                        "quality": quality,
                        "receipts": [item.model_dump(mode="json") for item in arm_client.receipts],
                        "requests": list(arm_client.request_meta),
                        "shadow_consumed": False,
                    }
                    arm_results[policy.value] = arm_result
                    if not passed:
                        if policy != ProjectionPolicy.COMPACT or not execute_provider:
                            raise FullSessionCanaryStop(f"{policy.value}:{reason}")
                        usage_known = bool(arm_client.receipts and arm_client.receipts[-1].usage.usage_observed)
                        if not usage_known:
                            raise FullSessionCanaryStop(f"{policy.value}:unknown_usage_before_fallback")
                        fallback_client, fallback_tasks, fallback_passed, fallback_reason, fallback_quality = run_task_arm(ProjectionPolicy.CURRENT, UsageRoute.FALLBACK)
                        if not fallback_passed:
                            raise FullSessionCanaryStop(f"current:fallback_{fallback_reason}")
                        failed_attempt_id = arm_client.receipts[-1].attempt_id
                        fallback_attempt_id = fallback_client.receipts[-1].attempt_id
                        fallback_receipt_model = CompactFallbackReceipt(
                            failed_projection=ProjectionPolicy.COMPACT,
                            effective_projection=ProjectionPolicy.CURRENT,
                            reason_code="compact_projection_failed",
                            failed_execution_id=failed_attempt_id,
                            fallback_execution_id=fallback_attempt_id,
                            source_snapshot_hash=source.source_snapshot_hash,
                            session_turn_source_hash=source.session_turn_source_hash,
                            session_constraints_hash=source.session_constraints_hash,
                            project_environment_mode="read_only",
                            write_scope_hash=_write_scope_hash(state),
                            provider_calls=2,
                            network_calls=2,
                            project_mutations=0,
                        )
                        fallback_receipt = fallback_receipt_model.model_dump(mode="json")
                        arm_results[policy.value] = {
                            **arm_result,
                            "effective_projection": ProjectionPolicy.CURRENT.value,
                            "fallback_used": True,
                            "fallback_receipt": fallback_receipt,
                            "fallback_requests": list(fallback_client.request_meta),
                            "fallback_receipts": [item.model_dump(mode="json") for item in fallback_client.receipts],
                            "shadow_consumed": False,
                        }
                        arm_results[ProjectionPolicy.CURRENT.value] = {
                            "requested_projection": ProjectionPolicy.CURRENT.value,
                            "effective_projection": ProjectionPolicy.CURRENT.value,
                            "quality_passed": fallback_passed,
                            "quality_reason": fallback_reason,
                            "quality": fallback_quality,
                            "receipts": [item.model_dump(mode="json") for item in fallback_client.receipts],
                            "requests": list(fallback_client.request_meta),
                            "shadow_consumed": False,
                            "fallback_for": ProjectionPolicy.COMPACT.value,
                        }
                        ledger = fallback_client.ledger
                        arm_stop_reasons.append(f"compact:{reason}")
                        break
                    ledger = arm_client.ledger
                context_hash = str(context_result.get("context_request_hash") or "")
                return {
                    "success": not arm_stop_reasons,
                    "status": "full_session_post_core_context_canary",
                    "context_request_hash": context_hash,
                    "context_projection_hash": context_projection.projection_hash,
                    "derived_dialog_candidate_count": len(context_projection.dialog_candidates),
                    "derived_artifact_candidate_count": len(context_projection.artifact_candidates),
                    "context_selection": selection,
                    "shared_receipts": [item.model_dump(mode="json") for item in shared_client.receipts],
                    "shared_requests": list(shared_client.request_meta),
                    "arms": arm_results,
                    "fallback_receipt": fallback_receipt,
                    "stop_reasons": arm_stop_reasons,
                }

        executor = _SessionExecutor()
        autopilot.runtime_controller.session_executor = executor
        runtime_exception: Exception | None = None
        try:
            result = autopilot.execute(
                goal_text,
                context={
                    "project_path": str(project_root),
                    "conversation_id": state.identity.conversation_id,
                    "run_id": state.identity.run_id,
                    "session_ingress_state": state,
                    "checkpointing_enabled": True,
                    "task_id": "stage6-full-session-task",
                },
            )
        except Exception as exc:
            runtime_exception = exc
            if execute_provider and campaign_state_path is not None and campaign_state_path.exists():
                try:
                    ledger = _load_ledger(campaign_state_path, contract)
                except FullSessionCanaryStop:
                    raise
            result = {
                "success": False,
                "session_result": {
                    "success": False,
                    "status": "stopped",
                    "stop_reasons": [f"{type(exc).__name__}:{str(exc)[:300]}"],
                    "provider_receipts": [item.model_dump(mode="json") for item in receipt_records],
                    "provider_requests": list(request_records),
                },
            }
        checkpoint = None
        checkpoint_store = autopilot.runtime_controller.checkpoint_store
        checkpoint_run_id = autopilot.runtime_controller._checkpoint_run_id
        if checkpoint_store is not None and checkpoint_run_id:
            checkpoint = checkpoint_store.load_latest(checkpoint_run_id)
        project_after = _snapshot_tree(project_root)
        memory_after = _snapshot_tree(memory_root)
        if project_after != project_before or memory_after != memory_before:
            raise FullSessionCanaryStop("full-session canary mutated project or memory")
        if checkpoint is None or checkpoint.session_ingress_state is None:
            raise FullSessionCanaryStop("full-session canary did not persist ingress checkpoint")
        if session_turn_ledger_hash(checkpoint.session_ingress_state) != source.session_turn_source_hash:
            raise FullSessionCanaryStop("checkpoint ingress turn hash diverged from source bundle")
        session_result = result.get("session_result", {}) if isinstance(result, dict) else {}
        # RuntimeController absorbs the custom executor payload into its final
        # report; retain a compact evidence copy without response bodies.
        all_receipts = []
        if isinstance(session_result, dict):
            all_receipts.extend(session_result.get("shared_receipts", []))
            for item in session_result.get("arms", {}).values() if isinstance(session_result.get("arms"), dict) else []:
                all_receipts.extend(item.get("receipts", []))
        if not all_receipts:
            all_receipts = [item.model_dump(mode="json") for item in receipt_records]
        if runtime_exception is not None:
            stop_reasons.append(f"{type(runtime_exception).__name__}:{str(runtime_exception)[:300]}")
        if execute_provider and ledger is not None:
            _save_ledger(campaign_state_path, ledger)
        return {
            "status": "passed" if result.get("success") else "stopped",
            "claim_boundary": "full_session_post_core_context_canary",
            "provider_execution_admitted": bool(execute_provider),
            "transport_attempted": bool(execute_provider and all_receipts),
            "provider_calls": len(all_receipts) if execute_provider else 0,
            "offline_model_calls": getattr(provider, "calls", 0) if not execute_provider else 0,
            "network_calls": len(all_receipts) if execute_provider else 0,
            "project_mutations": 0,
            "memory_mutations": 0,
            "admission": admission,
            "source": source.model_dump(mode="json"),
            "identity_map": {
                "conversation_id": state.identity.conversation_id,
                "ingress_run_id": state.identity.run_id,
                "runtime_session_id": autopilot.session_id,
                "checkpoint_run_id": checkpoint_run_id,
                "checkpoint_session_id": checkpoint.session_id,
                "checkpoint_conversation_id": checkpoint.session_ingress_state.identity.conversation_id,
            },
            "checkpoint": {
                "turn_hash": session_turn_ledger_hash(checkpoint.session_ingress_state),
                "constraints_hash": checkpoint.session_ingress_state.session_constraints.canonical_hash,
                "context_compaction_count": len(checkpoint.prompt_context_snapshot.compaction_bindings) if checkpoint.prompt_context_snapshot else 0,
            },
            "session_result": session_result,
            "provider_requests": request_records,
            "ledger": ledger.model_dump(mode="json") if ledger is not None else None,
            "receipts": all_receipts,
            "stop_reasons": stop_reasons,
        }


__all__ = ["FullSessionCanaryStop", "SourceBundle", "run_full_session_canary"]
