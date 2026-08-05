"""Stage 7B-3b zero-provider ContextLoader-to-Goal/Task safety gate.

This probe exercises the production read-only ContextLoader and the production
project-improvement analyzer candidate boundary before running the existing
Goal/Task projection arms.  A local deterministic analyzer stub is used only
to prove request assembly; it is not Provider traffic.  The same immutable
``SessionIngressState`` is used throughout, and the temporary project and
memory directories are snapshotted before and after the probe.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from autonomous_iteration.agents.context_loader import ContextLoaderAgent
from autonomous_iteration.models import ProjectStateSnapshot
from autonomous_iteration.tool.project_improvement_tool import (
    project_improvement_tool_executor,
)
from core.token_counting import default_deepseek_tokenizer_path
from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.project_manager import ProjectManager
from memory.session_dialog import session_turn_ledger_hash
from metadata import (
    ContextCandidateKind,
    SessionIngressState,
    ToolInputMetadata,
)

from stage12_session_compact_canary_admission import (
    ProjectionFeatureFlag,
    ProjectionPolicy,
    RuntimeKillSwitch,
)
from stage13_session_compact_pipeline_offline import (
    _improvement_report,
    _ingress_state,
    _project_state,
    _run_arm,
)


class FullContextOfflineStop(RuntimeError):
    """The full context safety gate stopped before any external transport."""


class _LocalAnalyzerClient:
    """Deterministic local response source; it never opens a network transport."""

    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            provider="offline_context_gate",
            model="offline-analyzer",
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
        return SimpleNamespace(
            parsed_json={
                "changed_signals": ["The bounded project workflow is ready for inspection."],
                "proposed_actions": ["Inspect calculator.py within the active session scope."],
                "next_decision_or_goal": "Inspect one bounded project behavior.",
                "must_satisfy": ["Preserve the existing public API."],
                "blocking_risks": [],
                "evidence_ids": [],
                "stack_preset_patch": {},
            },
            content="",
            usage={"completion_tokens": 24, "prompt_tokens": 0, "total_tokens": 24},
            finish_reason="stop",
        )


def _snapshot_tree(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not root.exists():
        return result
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = str(path.relative_to(root))
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _assert_ingress_decisions(
    decisions: list[dict[str, Any]],
    state: SessionIngressState,
) -> tuple[list[str], list[str]]:
    dialog = [item for item in decisions if item.get("kind") == ContextCandidateKind.DIALOG.value]
    constraints = [
        item
        for item in decisions
        if item.get("kind") == ContextCandidateKind.CONSTRAINT.value
        and str(item.get("candidate_id") or "").startswith("session_constraints:")
    ]
    expected_dialog_ids = sorted(turn.message_id for turn in state.turns)
    selected_dialog_ids = sorted(
        str(item.get("source_id"))
        for item in dialog
        if item.get("action") in {"kept", "partially_kept"} and item.get("source_id")
    )
    if expected_dialog_ids != selected_dialog_ids:
        raise FullContextOfflineStop("ContextLoader dialog source IDs were not fully retained")
    if len(constraints) != 1 or constraints[0].get("action") != "kept":
        raise FullContextOfflineStop("ContextLoader required session constraint was not retained")
    return expected_dialog_ids, [str(item.get("candidate_id")) for item in constraints]


def run_full_context_offline_probe(
    *,
    feature_flag: ProjectionFeatureFlag = ProjectionFeatureFlag.CANARY_ENABLED,
    kill_switch: RuntimeKillSwitch = RuntimeKillSwitch.ARMED,
    compact_projection_failure: bool = False,
) -> dict[str, Any]:
    """Run ContextLoader, analyzer, Goal and Task boundaries without Provider I/O."""

    if feature_flag != ProjectionFeatureFlag.CANARY_ENABLED:
        return {
            "status": "stopped",
            "stop_reasons": ["projection_feature_flag_disabled"],
            "production_entry_exercised": False,
            "provider_calls": 0,
            "network_calls": 0,
            "project_mutations": 0,
        }
    if kill_switch != RuntimeKillSwitch.ARMED:
        return {
            "status": "stopped",
            "stop_reasons": ["runtime_kill_switch_engaged"],
            "production_entry_exercised": False,
            "provider_calls": 0,
            "network_calls": 0,
            "project_mutations": 0,
        }

    with tempfile.TemporaryDirectory(prefix="openpilot-stage7b3b-context-") as raw_root:
        root = Path(raw_root).resolve()
        project_root = root / "project"
        project_root.mkdir()
        (project_root / "calculator.py").write_text(
            "def add(a, b):\n    return a + b\n",
            encoding="utf-8",
        )
        (project_root / "README.md").write_text(
            "Run `python -m pytest -q` and preserve the public API.\n",
            encoding="utf-8",
        )
        memory_root = root / "memory"
        memory_store = MemoryStore(memory_root)
        project_manager = ProjectManager(project_root)
        state = _ingress_state(str(project_root))
        turn_hash = session_turn_ledger_hash(state)
        project_before = _snapshot_tree(project_root)
        memory_before = _snapshot_tree(memory_root)

        context_loader = ContextLoaderAgent(
            MemoryContextBuilder(
                memory_store=memory_store,
                project_manager=project_manager,
                max_prompt_tokens=4096,
            )
        )
        context = context_loader.run(
            "Repair the calculator project",
            project_root,
            0,
            session_ingress_state=state,
        )
        selection = context.get("context_selection") or {}
        dialog_ids, constraint_ids = _assert_ingress_decisions(
            list(selection.get("candidate_decisions") or []),
            state,
        )
        context_hash = str(context.get("context_request_hash") or "")
        if not context_hash.startswith("sha256:"):
            raise FullContextOfflineStop("ContextLoader did not emit a request hash")

        analyzer = _LocalAnalyzerClient()
        source_file = project_root / "calculator.py"
        analyzer_input = ToolInputMetadata.from_mapping(
            "project_improvement_tool",
            {
                "project_path": str(project_root),
                "goal": "Repair the calculator project",
                "written_files": [str(source_file)],
                "run_command": "python -m pytest -q",
                "validation_result": {
                    "validation_passed": True,
                    "summary": "Current tests pass.",
                    "recommended_actions": ["Inspect calculator.py within the active scope."],
                },
                "readme_path": str(project_root / "README.md"),
                "prompt_context": {},
                "session_turn_source_hash": turn_hash,
                "_llm_client": analyzer,
                "_session_constraints": state.session_constraints,
                "_session_ingress_state": state,
            },
        )
        analyzer_result = project_improvement_tool_executor(analyzer_input)
        if str(getattr(analyzer_result.status, "value", analyzer_result.status)) != "success" or analyzer_result.result is None:
            raise FullContextOfflineStop("project-improvement analyzer did not return a bounded result")
        if len(analyzer.requests) != 1:
            raise FullContextOfflineStop("unexpected analyzer call count")
        analyzer_request = analyzer.requests[0]
        analyzer_selection = analyzer_request.context_selection
        if analyzer_selection is None:
            raise FullContextOfflineStop("analyzer request has no context selection evidence")
        analyzer_decisions = [item.model_dump(mode="json") for item in analyzer_selection.candidate_decisions]
        analyzer_dialog_ids, analyzer_constraint_ids = _assert_ingress_decisions(
            analyzer_decisions,
            state,
        )
        if analyzer_dialog_ids != dialog_ids or analyzer_constraint_ids != constraint_ids:
            raise FullContextOfflineStop("analyzer did not consume the ContextLoader ingress projection")

        project_state: ProjectStateSnapshot = _project_state(str(project_root))
        report = _improvement_report()
        arms = {
            policy.value: _run_arm(
                projection=policy,
                state=state,
                project_state=project_state,
                report=report,
                goal="Repair the calculator project",
                source_hash=context_hash,
                compact_projection_failure=compact_projection_failure,
            )
            for policy in (ProjectionPolicy.CURRENT, ProjectionPolicy.COMPACT)
        }
        project_after = _snapshot_tree(project_root)
        memory_after = _snapshot_tree(memory_root)
        if project_after != project_before:
            raise FullContextOfflineStop("full context probe mutated the project tree")
        if memory_after != memory_before:
            raise FullContextOfflineStop("full context probe mutated the memory store")
        unexpected_artifacts = [
            path
            for path in project_after
            if path == "sketch.json" or path.startswith(".openpilot/")
        ]
        if unexpected_artifacts:
            raise FullContextOfflineStop("read-only ContextLoader created project artifacts")

    fallback_receipt = arms[ProjectionPolicy.COMPACT.value].fallback_receipt
    if compact_projection_failure and fallback_receipt is None:
        raise FullContextOfflineStop("compact failure did not produce a typed fallback receipt")
    if fallback_receipt is not None:
        if fallback_receipt.session_turn_source_hash != turn_hash:
            raise FullContextOfflineStop("fallback changed the ingress turn hash")
        if fallback_receipt.session_constraints_hash != state.session_constraints.canonical_hash:
            raise FullContextOfflineStop("fallback changed the constraint hash")
    lineage_receipts = {
        key: value.lineage_receipt.model_dump(mode="json")
        for key, value in arms.items()
    }
    if lineage_receipts[ProjectionPolicy.CURRENT.value] != lineage_receipts[ProjectionPolicy.COMPACT.value]:
        raise FullContextOfflineStop("current and compact arms diverged in source or authority lineage")
    if lineage_receipts[ProjectionPolicy.CURRENT.value]["source_snapshot_hash"] != context_hash:
        raise FullContextOfflineStop("arm source snapshot is not the ContextLoader snapshot")

    return {
        "status": "passed",
        "claim_boundary": "context_loader_analyzer_goal_task_no_provider",
        "production_entry_exercised": True,
        "provider_calls": 0,
        "offline_model_calls": len(analyzer.requests),
        "network_calls": 0,
        "project_mutations": 0,
        "memory_mutations": 0,
        "context_request_hash": context_hash,
        "session_turn_source_hash": turn_hash,
        "session_constraints_hash": state.session_constraints.canonical_hash,
        "lineage_receipts": lineage_receipts,
        "context_loader_dialog_source_ids": dialog_ids,
        "analyzer_dialog_source_ids": analyzer_dialog_ids,
        "goal_task_dialog_recall": {
            key: value.dialog_recall for key, value in arms.items()
        },
        "goal_task_constraint_recall": {
            key: value.constraint_recall for key, value in arms.items()
        },
        "fallback_receipt": (
            fallback_receipt.model_dump(mode="json")
            if fallback_receipt is not None
            else None
        ),
        "arms": {key: value.model_dump(mode="json") for key, value in arms.items()},
        "stop_reasons": (
            list(arms[ProjectionPolicy.COMPACT.value].stop_reasons)
            if compact_projection_failure
            else []
        ),
    }


__all__ = ["FullContextOfflineStop", "run_full_context_offline_probe"]
