"""Stage 5B-3a full-entry, zero-provider admission probe.

This experiment enters through ``IntelligentAutopilot.execute`` and the real
``AgentRuntimeController.run``.  Its session executor is replaced by a typed
offline sentinel before any Provider-facing work, so the probe can validate
identity propagation and checkpoint persistence without a network call or
project mutation.  It is not a Provider canary and does not claim Goal/Task
quality or full-session Token savings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from memory.session_dialog import session_turn_ledger_hash
from memory.session_ingress import SessionIngress
from metadata import ConversationIdentity, SessionIngressState, SessionTurn
from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks
from runtime_diagnostics.recorder import DiagnosticRecorder
from stage12_session_compact_canary_admission import (
    ProjectionFeatureFlag,
    RuntimeKillSwitch,
    run_no_provider_preflight,
)


class FullEntryAdmissionStop(RuntimeError):
    """The full-entry zero-provider gate stopped before any transport."""


class _NoProviderClient:
    """Settings-only client whose transport is forbidden by construction."""

    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            provider="offline_admission",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            tokenizer_path="",
            context_max_prompt_tokens=4096,
            context_reserved_prompt_tokens=128,
        )

    def complete(self, *_args: Any, **_kwargs: Any) -> Any:
        raise FullEntryAdmissionStop("provider transport is forbidden")


class _NoProviderSessionExecutor:
    """Runtime session shell that stops below task/provider execution."""

    supports_session_cursor = True

    def __init__(self) -> None:
        self.capture: dict[str, Any] | None = None

    def run(
        self,
        goal: str,
        context: dict[str, Any],
        mode: str = "standard",
        resume_cursor: Any | None = None,
        resume_bootstrap: Any | None = None,
    ) -> dict[str, Any]:
        self.capture = {
            "goal": goal,
            "context": dict(context),
            "mode": mode,
            "resume_cursor": resume_cursor,
            "resume_bootstrap": resume_bootstrap,
        }
        return {
            "success": True,
            "status": "offline_admission",
            "provider_calls": 0,
        }


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _ingress_state(project_root: str) -> SessionIngressState:
    identity = ConversationIdentity(
        conversation_id="stage5b-full-entry-conversation",
        run_id="stage5b-full-entry-run",
        turn_index=0,
        project_root=project_root,
    )
    state = SessionIngressState(identity=identity)
    state = SessionIngress.open_turn(
        state,
        SessionTurn(
            identity=identity.model_copy(update={"turn_index": 1}),
            message_id="stage5b-full-entry-user-constraint",
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
            message_id="stage5b-full-entry-assistant-ack",
            role="assistant",
            content="I will preserve the confirmed session constraints while working.",
        ),
    )
    return state


def run_full_entry_admission(
    *,
    feature_flag: ProjectionFeatureFlag = ProjectionFeatureFlag.CANARY_ENABLED,
    kill_switch: RuntimeKillSwitch = RuntimeKillSwitch.ARMED,
    transport: Any | None = None,
) -> dict[str, Any]:
    """Validate full execute/runtime/checkpoint ingress with transport closed."""

    if transport is not None:
        raise FullEntryAdmissionStop("provider transport is forbidden in full-entry probe")
    stop_reasons: list[str] = []
    if feature_flag != ProjectionFeatureFlag.CANARY_ENABLED:
        stop_reasons.append("projection_feature_flag_disabled")
    if kill_switch != RuntimeKillSwitch.ARMED:
        stop_reasons.append("runtime_kill_switch_engaged")
    if stop_reasons:
        return {
            "status": "stopped",
            "claim_boundary": "execute_runtime_checkpoint_only",
            "production_entry_exercised": False,
            "provider_calls": 0,
            "network_calls": 0,
            "project_mutations": 0,
            "requests": [],
            "stop_reasons": stop_reasons,
        }

    admission = run_no_provider_preflight()
    with tempfile.TemporaryDirectory(prefix="openpilot-stage5b-full-entry-") as raw_root:
        root = Path(raw_root).resolve()
        project_root = root / "project"
        project_root.mkdir()
        hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(root / "diagnostics"))
        autopilot = IntelligentAutopilot(
            _NoProviderClient(),
            log_file=root / "autopilot.jsonl",
            enable_iterative_improvement=False,
            required_successful_improvements=0,
            runtime_diagnostics_hooks=hooks,
        )
        executor = _NoProviderSessionExecutor()
        autopilot.runtime_controller.session_executor = executor
        ingress = _ingress_state(str(project_root))
        result = autopilot.execute(
            "Inspect the calculator project",
            context={
                "project_path": str(project_root),
                "session_ingress_state": ingress,
                "checkpointing_enabled": True,
                "task_id": "stage5b-full-entry-task",
            },
        )
        if executor.capture is None:
            raise FullEntryAdmissionStop("runtime session executor was not reached")
        captured_context = executor.capture["context"]
        if captured_context.get("session_ingress_state") != ingress:
            raise FullEntryAdmissionStop("execute did not preserve the ingress snapshot")
        if captured_context.get("session_constraints") != ingress.session_constraints:
            raise FullEntryAdmissionStop("execute did not project the typed constraint state")

        controller = autopilot.runtime_controller
        checkpoint_store = controller.checkpoint_store
        run_id = controller._checkpoint_run_id
        checkpoint = (
            checkpoint_store.load_latest(run_id)
            if checkpoint_store is not None and run_id
            else None
        )
        if checkpoint is None or checkpoint.session_ingress_state is None:
            raise FullEntryAdmissionStop("full runtime did not persist a session ingress checkpoint")
        if checkpoint.session_ingress_state.session_constraints.canonical_hash != ingress.session_constraints.canonical_hash:
            raise FullEntryAdmissionStop("checkpoint constraint state diverged from ingress state")
        ingress_turn_hash = session_turn_ledger_hash(ingress)
        checkpoint_turn_hash = session_turn_ledger_hash(checkpoint.session_ingress_state)
        if checkpoint_turn_hash != ingress_turn_hash:
            raise FullEntryAdmissionStop("checkpoint raw turn ledger diverged from ingress state")

        project_files = sorted(
            str(path.relative_to(root))
            for path in root.rglob("*")
            if path.is_file()
            and "diagnostics" not in path.parts
            and path.name != "autopilot.jsonl"
        )
        return {
            "status": "passed",
            "claim_boundary": "execute_runtime_checkpoint_only",
            "production_entry_exercised": True,
            "admission": admission,
            "conversation_id": ingress.identity.conversation_id,
            "run_id": autopilot.session_id,
            "checkpoint_run_id": run_id,
            "session_constraints_hash": ingress.session_constraints.canonical_hash,
            "checkpoint_constraints_hash": checkpoint.session_ingress_state.session_constraints.canonical_hash,
            "session_turn_source_hash": ingress_turn_hash,
            "checkpoint_turn_source_hash": checkpoint_turn_hash,
            "raw_turn_count": len(checkpoint.session_ingress_state.turns),
            "active_constraint_count": len(ingress.session_constraints.active_entries),
            "provider_calls": 0,
            "network_calls": 0,
            "project_mutations": 0,
            "project_files": project_files,
            "result_status": result.get("status"),
            "stop_reasons": [],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flag-off", action="store_true")
    parser.add_argument("--kill-switch", action="store_true")
    args = parser.parse_args()
    result = run_full_entry_admission(
        feature_flag=(
            ProjectionFeatureFlag.DISABLED
            if args.flag_off
            else ProjectionFeatureFlag.CANARY_ENABLED
        ),
        kill_switch=(
            RuntimeKillSwitch.ENGAGED
            if args.kill_switch
            else RuntimeKillSwitch.ARMED
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
