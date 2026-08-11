"""Enhanced CLI entry point with improved UI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from rich.console import Console

from autonomous_iteration.pre_task_admission import (
    PreTaskAdmissionKind,
    UnifiedEntryError,
    UnifiedEntryFailureStage,
    resolve_pre_task_admission,
)
from core.config import EmbeddingSettings, LLMSettings
from core.instrumented_llm import InstrumentedLLMClient
from core.model_health import run_startup_model_health_check
from core.openpilot_log import OpenPilotLogger
from runtime_diagnostics.hooks import get_default_hooks
from metadata import (
    ConversationIdentity,
    ProjectImprovementPolicy,
    ProjectImprovementPolicySource,
    ProjectImprovementRequirement,
    Recoverability,
    SessionIngressState,
    SessionTurn,
)
from memory.session_ingress import SessionIngress
from ui.enhanced_ui import EnhancedUI
from ui.progress_tracker import ProgressTracker

if TYPE_CHECKING:
    from metadata import TaskRouteMetadata


DEFAULT_IMPROVEMENT_ITERATIONS = 2
_CONSTRAINT_COMMANDS = frozenset({"/constraints", "/confirm", "/reject", "/revoke"})


class _UnifiedAutonomousEntryScope(str, Enum):
    RESPONSE_CANARY = "response_canary"
    LEGACY_AUTOPILOT = "legacy_autopilot"


def _unified_autonomous_entry_scope(goal: str) -> _UnifiedAutonomousEntryScope:
    """Compatibility projection of the typed pre-task admission decision."""

    if resolve_pre_task_admission(goal).kind is PreTaskAdmissionKind.PROJECT_EXECUTION:
        return _UnifiedAutonomousEntryScope.LEGACY_AUTOPILOT
    return _UnifiedAutonomousEntryScope.RESPONSE_CANARY


def _is_constraint_command(user_input: str) -> bool:
    """Return whether input belongs to the typed session-constraint ingress."""

    command = str(user_input).strip().split(maxsplit=1)[0].casefold() if str(user_input).strip() else ""
    return command in _CONSTRAINT_COMMANDS


def _runtime_diagnostics_enabled() -> bool:
    value = str(os.getenv("OPENPILOT_RUNTIME_DIAGNOSTICS_ENABLED", "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _unified_autonomous_entry_enabled() -> bool:
    value = str(os.getenv("OPENPILOT_UNIFIED_AUTONOMOUS_ENTRY_ENABLED", "1")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _governed_decomposition_enabled() -> bool:
    value = str(os.getenv("OPENPILOT_GOVERNED_DECOMPOSITION", "1")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _iteration_turn_store():
    from autonomous_iteration.iteration_turn_store import IterationTurnStore

    recorder = get_default_hooks().recorder
    return IterationTurnStore(recorder.trajectory_dir / "iteration_turns")


def _try_deterministic_runtime_response(
    goal: str,
    *,
    ingress_state: SessionIngressState,
    settings: LLMSettings,
    runtime_options: "OpenPilotRuntimeOptions",
):
    if not _unified_autonomous_entry_enabled():
        return None
    from autonomous_iteration.deterministic_runtime_response import (
        DeterministicRuntimeResponseController,
    )
    from autonomous_iteration.runtime_facts import RuntimeFactResolver

    facts = RuntimeFactResolver(settings).resolve(
        project_path=ingress_state.identity.project_root,
        project_improvement_policy=runtime_options.project_improvement_policy,
    )
    return DeterministicRuntimeResponseController(_iteration_turn_store()).try_complete(
        goal,
        ingress=ingress_state,
        facts=facts,
    )


def _runtime_fact_projection(
    *,
    ingress_state: SessionIngressState,
    settings: LLMSettings,
    runtime_options: "OpenPilotRuntimeOptions",
):
    from autonomous_iteration.runtime_facts import RuntimeFactResolver

    return RuntimeFactResolver(settings).resolve(
        project_path=ingress_state.identity.project_root,
        project_improvement_policy=runtime_options.project_improvement_policy,
    )


def _execute_response_evidence_task(
    candidate,
    *,
    llm_client,
    ui: EnhancedUI,
    tracker: ProgressTracker | None,
    logger,
    runtime_options: "OpenPilotRuntimeOptions",
):
    if not _governed_decomposition_enabled():
        raise RuntimeError("response evidence requires governed decomposition")
    if not _runtime_diagnostics_enabled():
        raise RuntimeError("response evidence requires durable diagnostics and checkpoint storage")

    hooks = get_default_hooks()
    from autonomous_iteration.response_evidence_runtime import (
        execute_response_evidence_task,
    )

    return execute_response_evidence_task(
        candidate,
        turn_store=_iteration_turn_store(),
        llm_client=llm_client,
        console=ui.console,
        logger=logger,
        tracker=tracker,
        enhanced_ui=ui,
        project_improvement_policy=runtime_options.project_improvement_policy,
        diagnostics_hooks=hooks,
    )


def _try_unified_autonomous_response(
    goal: str,
    *,
    ingress_state: SessionIngressState,
    settings: LLMSettings,
    runtime_options: "OpenPilotRuntimeOptions",
    llm_client,
    ui: EnhancedUI,
    tracker: ProgressTracker | None,
    logger,
):
    response = _try_deterministic_runtime_response(
        goal,
        ingress_state=ingress_state,
        settings=settings,
        runtime_options=runtime_options,
    )
    if response is not None:
        return response
    if not _unified_autonomous_entry_enabled():
        return None
    if _unified_autonomous_entry_scope(goal) == _UnifiedAutonomousEntryScope.LEGACY_AUTOPILOT:
        return None

    from autonomous_iteration.bounded_model_response import BoundedModelResponseController

    try:
        candidate = BoundedModelResponseController(
            _iteration_turn_store(),
            llm_client,
        ).complete(
            goal,
            ingress=ingress_state,
            facts=_runtime_fact_projection(
                ingress_state=ingress_state,
                settings=settings,
                runtime_options=runtime_options,
            ),
        )
    except Exception as exc:
        raise UnifiedEntryError(UnifiedEntryFailureStage.BOUNDED_RESPONSE, exc) from exc
    if not candidate.evidence_required:
        return candidate
    try:
        return _execute_response_evidence_task(
            candidate,
            llm_client=llm_client,
            ui=ui,
            tracker=tracker,
            logger=logger,
            runtime_options=runtime_options,
        )
    except Exception as exc:
        raise UnifiedEntryError(UnifiedEntryFailureStage.EXTERNAL_EVIDENCE, exc) from exc


def _build_task_execution_context(*, source: str, classification: "TaskRouteMetadata") -> dict[str, object]:
    """Create a stable task context before runtime execution begins."""
    return {
        "task_id": f"cli_{uuid4().hex}",
        "source": source,
        "route": classification.route,
        "route_confidence": classification.confidence,
        "route_reason": classification.reason,
    }


@dataclass(frozen=True)
class OpenPilotRuntimeOptions:
    """Runtime options for autopilot creation."""

    improvement_iterations: int = DEFAULT_IMPROVEMENT_ITERATIONS
    prompt_for_project_improvement_iterations: bool = False
    improvement_requirement: ProjectImprovementRequirement = ProjectImprovementRequirement.OPTIONAL
    improvement_policy_source: ProjectImprovementPolicySource = ProjectImprovementPolicySource.AUTOMATIC_DEFAULT

    @property
    def enable_iterative_improvement(self) -> bool:
        return self.improvement_iterations > 0

    @property
    def project_improvement_policy(self) -> ProjectImprovementPolicy:
        if self.improvement_iterations <= 0:
            return ProjectImprovementPolicy(
                requirement=ProjectImprovementRequirement.DISABLED,
                source=self.improvement_policy_source,
                required_accepted_transactions=0,
                max_accepted_transactions=0,
                max_attempts=0,
            )
        from autonomous_iteration.agents.iteration_agent import AutonomousIterationAgent

        automatic_optional = (
            self.improvement_requirement == ProjectImprovementRequirement.OPTIONAL
            and self.improvement_policy_source == ProjectImprovementPolicySource.AUTOMATIC_DEFAULT
        )
        accepted_target = 1 if automatic_optional else self.improvement_iterations
        return ProjectImprovementPolicy(
            requirement=self.improvement_requirement,
            source=self.improvement_policy_source,
            required_accepted_transactions=(
                0 if automatic_optional else accepted_target
            ),
            max_accepted_transactions=accepted_target,
            max_attempts=(
                1
                if automatic_optional
                else AutonomousIterationAgent.minimum_attempt_budget(accepted_target)
            ),
        )


def _format_failure_details(result: dict) -> str:
    context = _extract_failure_context(result)
    details = (
        context.get("failure_reason")
        or _result_value(result, "failure_reason")
        or _result_value(result, "error")
        or _result_value(result, "iteration_error")
        or "Autopilot reported failure"
    )
    failure_stage = context.get("failure_stage") or _result_value(result, "failure_stage")
    failed_tool = context.get("failed_tool") or _result_value(result, "failed_tool")
    failed_call_id = context.get("failed_call_id") or _result_value(result, "failed_call_id")
    failed_step_id = context.get("failed_step_id") or _result_value(result, "failed_step_id")
    failed_iteration = _result_value(result, "failed_iteration")
    context_lines = []
    if context.get("task_description"):
        context_lines.append(f"Task: {context['task_description']}")
    if context.get("task_id"):
        context_lines.append(f"Task ID: {context['task_id']}")
    if context.get("failure_id"):
        context_lines.append(f"Failure ID: {context['failure_id']}")
    recoverable = context.get("recoverable")
    recoverability = context.get("recoverability")
    if recoverable is not None:
        context_lines.append(f"Recoverable: {'yes' if bool(recoverable) else 'no'}")
    if recoverability:
        context_lines.append(f"Recovery status: {recoverability}")
    if failure_stage or failed_tool:
        context_lines.extend(
            [
                f"Stage: {failure_stage or 'unknown'}",
                f"Tool: {failed_tool or 'unknown'}",
            ]
        )
        if failed_call_id:
            context_lines.append(f"Call: {failed_call_id}")
        if failed_step_id:
            context_lines.append(f"Step: {failed_step_id}")
        if failed_iteration:
            context_lines.append(f"Iteration: {failed_iteration}")
    if context.get("file_path"):
        context_lines.append(f"File: {context['file_path']}")
    if context.get("error_type"):
        context_lines.append(f"Error Type: {context['error_type']}")
    if context.get("suggested_recovery"):
        context_lines.append(f"Recovery: {context['suggested_recovery']}")
    response_preview = context.get("response_preview") or context.get("response_text")
    if response_preview:
        context_lines.append(f"Response Preview: {str(response_preview)[:1000]}")
    if context_lines:
        details = f"{details}\n" + "\n".join(context_lines)
    return str(details)


def _cli_exception_failure(
    exc: Exception,
    *,
    task_id: str | None = None,
    stage: UnifiedEntryFailureStage | None = None,
) -> dict[str, object]:
    """Build a bounded ordinary-mode failure without exposing exception text or a traceback."""

    resolved_stage = stage or (
        exc.stage if isinstance(exc, UnifiedEntryError) else None
    )
    return {
        "success": False,
        "failure_reason": "Autonomous iteration stopped before completion.",
        "failure_stage": resolved_stage.value if resolved_stage is not None else "CLI",
        "failed_tool": "autonomous_iteration",
        "task_id": task_id,
        "failure_id": f"{task_id}:cli" if task_id else "cli",
        "error_type": exc.cause_type if isinstance(exc, UnifiedEntryError) else type(exc).__name__,
        "recoverable": False,
        "recoverability": Recoverability.NOT_RECOVERABLE.value,
        "suggested_recovery": "Retry after reviewing the diagnostic log.",
    }


def _result_value(value, key: str):
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _extract_failure_context(result) -> dict:
    if not isinstance(result, dict):
        return {}
    for nested_key in ("session_result", "result"):
        nested_result = result.get(nested_key)
        if isinstance(nested_result, dict):
            nested_context = _extract_failure_context(nested_result)
            if nested_context:
                return nested_context
    direct_reason = result.get("failure_reason")
    if direct_reason and direct_reason != "Autopilot reported failure":
        raw_failure_payload = result.get("failure")
        direct_failure_payload: dict = raw_failure_payload if isinstance(raw_failure_payload, dict) else {}
        direct_context = {
            "failure_reason": direct_reason,
            "failure_stage": result.get("failure_stage"),
            "failed_tool": result.get("failed_tool"),
            "failed_call_id": result.get("failed_call_id"),
            "failed_step_id": result.get("failed_step_id"),
            "task_id": result.get("task_id"),
            "task_description": result.get("task_description"),
            "file_path": result.get("file_path"),
            "error_type": result.get("error_type"),
            "suggested_recovery": result.get("suggested_recovery"),
            "response_preview": result.get("response_preview") or result.get("response_text"),
            "recoverable": result.get("recoverable", direct_failure_payload.get("recoverable")),
            "recoverability": result.get("recoverability"),
            "failure_id": result.get("failure_id"),
        }
        if any(value for key, value in direct_context.items() if key != "failure_reason"):
            return direct_context
    for task_result in result.get("results") or []:
        context = _failure_context_from_task_result(task_result)
        if context:
            return context
    if direct_reason:
        raw_failure_payload = result.get("failure")
        fallback_failure_payload: dict = raw_failure_payload if isinstance(raw_failure_payload, dict) else {}
        return {
            "failure_reason": direct_reason,
            "failure_stage": result.get("failure_stage"),
            "failed_tool": result.get("failed_tool"),
            "failed_call_id": result.get("failed_call_id"),
            "failed_step_id": result.get("failed_step_id"),
            "recoverable": result.get("recoverable", fallback_failure_payload.get("recoverable")),
            "recoverability": result.get("recoverability"),
            "failure_id": result.get("failure_id"),
        }
    decomposition = result.get("decomposition")
    for task in _result_value(decomposition, "subtasks") or []:
        context = _failure_context_from_task(task)
        if context:
            return context
    return {}


def _failure_context_from_task_result(task_result) -> dict:
    metadata = _result_value(task_result, "result_metadata")
    failure = _result_value(metadata, "failure")
    reason = _result_value(failure, "error_message") or _result_value(task_result, "error")
    if not reason:
        return {}
    details = _result_value(failure, "details") or {}
    context = _failure_context_from_details(details, reason)
    context.setdefault("task_id", _result_value(task_result, "task_id"))
    context.setdefault("task_description", details.get("task_description") if isinstance(details, dict) else None)
    return context


def _failure_context_from_task(task) -> dict:
    status = str(_result_value(task, "status") or "").lower()
    if "failed" not in status and "blocked" not in status:
        return {}
    metadata = _result_value(task, "result") or _result_value(task, "result_metadata")
    failure = _result_value(metadata, "failure")
    reason = _result_value(failure, "error_message") or _result_value(task, "error")
    if not reason:
        return {}
    details = _result_value(failure, "details") or {}
    context = _failure_context_from_details(details, reason)
    context.setdefault("task_id", _result_value(task, "id"))
    context.setdefault("task_description", _result_value(task, "description"))
    return context


def _failure_context_from_details(details, reason: str) -> dict:
    if not isinstance(details, dict):
        details = {}
    tool_loop = details.get("tool_loop") if isinstance(details.get("tool_loop"), dict) else {}
    final_error = tool_loop.get("final_error") if isinstance(tool_loop, dict) else {}
    final_details = final_error.get("details") if isinstance(final_error, dict) and isinstance(final_error.get("details"), dict) else {}
    input_summary = details.get("input_summary") if isinstance(details.get("input_summary"), dict) else {}
    final_input = final_details.get("input_summary") if isinstance(final_details.get("input_summary"), dict) else {}
    error_type = (
        details.get("error_type")
        or details.get("failure_error_type")
        or final_details.get("error_type")
        or (final_error.get("error_type") if isinstance(final_error, dict) else None)
    )
    response_preview = (
        details.get("response_preview")
        or details.get("response_preview_start")
        or final_details.get("response_preview")
        or final_details.get("response_preview_start")
        or details.get("response_text")
        or final_details.get("response_text")
    )
    return {
        "failure_reason": reason,
        "failure_stage": details.get("failure_stage") or "Task Executor",
        "failed_tool": details.get("tool_name") or details.get("failed_tool") or final_details.get("tool_name"),
        "failed_call_id": details.get("call_id") or final_details.get("call_id"),
        "failed_step_id": details.get("step_id") or final_details.get("step_id"),
        "task_id": details.get("task_id") or final_details.get("task_id"),
        "task_description": details.get("task_description") or final_details.get("task_description"),
        "file_path": (
            details.get("file_path")
            or final_details.get("file_path")
            or input_summary.get("file_path")
            or final_input.get("file_path")
            or final_details.get("received_path")
        ),
        "error_type": error_type,
        "suggested_recovery": (
            details.get("suggested_recovery")
            or final_details.get("suggested_recovery")
            or details.get("recovery_strategy")
            or final_details.get("recovery_strategy")
        ),
        "response_preview": response_preview,
        "response_text": details.get("response_text") or final_details.get("response_text"),
    }


def run_enhanced_cli(
    args,
    console: Console | None = None,
    llm_client = None
) -> int:
    """Run OpenPilot with enhanced UI."""
    console = console or Console()

    from ui.environment_guard import block_missing_socksio, block_project_venv
    if block_project_venv(console):
        return 2
    if block_missing_socksio(console):
        return 2

    # Probe configured providers before the runtime starts doing real work.
    settings = LLMSettings()
    if llm_client is None:
        run_startup_model_health_check(
            console,
            llm_settings=settings,
            embedding_settings=EmbeddingSettings(),
        )

    # Initialize enhanced UI
    enhanced_ui = EnhancedUI(console)
    enhanced_ui.show_banner()

    # Initialize progress tracker
    tracker = ProgressTracker(enhanced_ui)

    # Create instrumented LLM client
    if llm_client is None:
        llm_client = InstrumentedLLMClient(settings, tracker)

    # Setup logging
    log_file = getattr(args, 'log_file', None)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        logger = OpenPilotLogger(log_file)
    else:
        logger = None

    # Check for once mode
    resume_run_id = str(getattr(args, "resume_run_id", None) or "").strip()
    resume_checkpoint_id = str(getattr(args, "resume_checkpoint_id", None) or "").strip()
    if bool(resume_run_id) != bool(resume_checkpoint_id):
        enhanced_ui.show_error(
            "Resume arguments incomplete",
            "--resume-run-id and --resume-checkpoint-id must be provided together.",
        )
        return 2
    if resume_run_id and resume_checkpoint_id:
        runtime_options = _runtime_options_from_args(args, project_prompt_default=False)
        return _run_resume_mode(
            run_id=resume_run_id,
            checkpoint_id=resume_checkpoint_id,
            project_path=str(getattr(args, "project_path", None) or "").strip(),
            ui=enhanced_ui,
            tracker=tracker,
            logger=logger,
            settings=settings,
            runtime_options=runtime_options,
            llm_client=llm_client,
        )

    if hasattr(args, 'once') and args.once:
        runtime_options = _runtime_options_from_args(args, project_prompt_default=False)
        return _run_once_mode(
            args.once,
            enhanced_ui,
            tracker,
            logger,
            settings,
            runtime_options,
            llm_client,
            checkpointing_enabled=bool(getattr(args, "checkpointing", False)),
            project_path=str(getattr(args, "project_path", None) or "").strip(),
        )

    runtime_options = _runtime_options_from_args(args, project_prompt_default=True)

    # Interactive mode
    return _run_interactive_mode(
        enhanced_ui,
        tracker,
        logger,
        settings,
        args,
        llm_client,
        runtime_options,
    )


def _runtime_options_from_args(
    args,
    *,
    project_prompt_default: bool,
) -> OpenPilotRuntimeOptions:
    """Resolve runtime options from CLI args.

    Interactive autopilot asks per generated project when the user did not pass
    a fixed --improvement-iterations value.
    """
    configured = getattr(args, "improvement_iterations", None)
    if configured is not None:
        return OpenPilotRuntimeOptions(
            improvement_iterations=configured,
            prompt_for_project_improvement_iterations=False,
            improvement_requirement=(
                ProjectImprovementRequirement.REQUIRED
                if configured > 0
                else ProjectImprovementRequirement.DISABLED
            ),
            improvement_policy_source=ProjectImprovementPolicySource.USER_SELECTED,
        )

    return OpenPilotRuntimeOptions(
        improvement_iterations=DEFAULT_IMPROVEMENT_ITERATIONS,
        prompt_for_project_improvement_iterations=project_prompt_default,
    )


def _run_once_mode(
    goal: str,
    ui: EnhancedUI,
    tracker: ProgressTracker,
    logger,
    settings: LLMSettings,
    runtime_options: OpenPilotRuntimeOptions,
    llm_client = None,
    *,
    checkpointing_enabled: bool = False,
    project_path: str = "",
) -> int:
    """Run a single goal and exit."""
    from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
    from core.llm import LLMClient

    ui.console.print()
    ui.console.print(f"[bold cyan]Goal:[/bold cyan] {goal}")
    ui.console.print()
    execution_context: dict[str, object] = {}
    failure_stage: UnifiedEntryFailureStage | None = None

    try:
        classification = _classify_task_route(goal)
        _show_task_route(ui, classification)
        execution_context = _build_task_execution_context(source="cli_once", classification=classification)
        execution_context["checkpointing_enabled"] = checkpointing_enabled
        if project_path:
            execution_context["project_path"] = str(Path(project_path).expanduser().resolve())
        if _runtime_diagnostics_enabled() and classification.route != "agent_generator":
            get_default_hooks().on_route_selected(
                task_id=str(execution_context["task_id"]),
                route=classification.route,
                confidence=classification.confidence,
                reason=classification.reason,
            )

        active_llm_client = llm_client or LLMClient(settings)
        diagnostics_hooks = get_default_hooks() if _runtime_diagnostics_enabled() else None
        if classification.route == "agent_generator":
            return 0 if _execute_agent_generator(goal, ui, active_llm_client, logger) else 2

        if _unified_autonomous_entry_enabled():
            identity = ConversationIdentity(
                conversation_id=f"conversation_{uuid4().hex}",
                run_id=f"run_{uuid4().hex}",
                turn_index=1,
                project_root=str(
                    Path(project_path or Path.cwd()).expanduser().resolve()
                ),
            )
            ingress = SessionIngressState(
                identity=identity,
                turns=[
                    SessionTurn(
                        identity=identity,
                        message_id=f"message_{uuid4().hex}",
                        role="user",
                        content=goal,
                    )
                ],
            )
            response = _try_unified_autonomous_response(
                goal,
                ingress_state=ingress,
                settings=settings,
                runtime_options=runtime_options,
                llm_client=active_llm_client,
                ui=ui,
                tracker=tracker,
                logger=logger,
            )
            if response is not None:
                ui.console.print(response.content)
                return 0

        failure_stage = UnifiedEntryFailureStage.PROJECT_EXECUTION
        # Create autopilot with enhanced UI support
        autopilot = IntelligentAutopilot(
            llm_client=active_llm_client,
            console=ui.console,
            auto_approve=True,
            logger=logger,
            use_enhanced_ui=True,
            enhanced_ui=ui,
            tracker=tracker,
            enable_iterative_improvement=runtime_options.enable_iterative_improvement,
            required_successful_improvements=runtime_options.improvement_iterations,
            project_improvement_policy=runtime_options.project_improvement_policy,
            prompt_for_project_improvement_iterations=runtime_options.prompt_for_project_improvement_iterations,
            runtime_diagnostics_hooks=diagnostics_hooks,
        )

        # Execute with live session
        with ui.live_session(f"Executing: {goal[:50]}..."):
            ui.update_main_content(
                ui.create_status_panel("Autopilot Mode", "Intelligent task decomposition and execution...")
            )

            result = autopilot.execute(goal, context=execution_context)

            # Small delay to let user see final status
            import time
            time.sleep(1)

        ui.show_full_task_graph_timeline()

        if result.get("success"):
            ui.show_success("Goal completed successfully!")
            return 0

        ui.show_error("Execution failed", _format_failure_details(result))
        return 2

    except Exception as e:
        ui.show_full_task_graph_timeline()
        ui.show_error(
            "Execution failed",
            _format_failure_details(
                _cli_exception_failure(
                    e,
                    task_id=str(execution_context.get("task_id") or "") or None,
                    stage=failure_stage,
                )
            ),
        )
        return 2


def _run_resume_mode(
    *,
    run_id: str,
    checkpoint_id: str,
    project_path: str,
    ui: EnhancedUI,
    tracker: ProgressTracker,
    logger,
    settings: LLMSettings,
    runtime_options: OpenPilotRuntimeOptions,
    llm_client=None,
) -> int:
    """Resume one explicitly identified checkpoint and show its preflight result."""
    from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
    from core.llm import LLMClient

    if not project_path:
        ui.show_error("Resume blocked", "--project-path is required for project identity verification.")
        return 2
    diagnostics_hooks = get_default_hooks() if _runtime_diagnostics_enabled() else None
    if diagnostics_hooks is None:
        ui.show_error("Resume blocked", "Runtime diagnostics/checkpoint storage is disabled.")
        return 2
    autopilot = IntelligentAutopilot(
        llm_client=llm_client or LLMClient(settings),
        console=ui.console,
        auto_approve=True,
        logger=logger,
        use_enhanced_ui=True,
        enhanced_ui=ui,
        tracker=tracker,
        enable_iterative_improvement=runtime_options.enable_iterative_improvement,
        required_successful_improvements=runtime_options.improvement_iterations,
        project_improvement_policy=runtime_options.project_improvement_policy,
        prompt_for_project_improvement_iterations=runtime_options.prompt_for_project_improvement_iterations,
        runtime_diagnostics_hooks=diagnostics_hooks,
    )
    try:
        with ui.live_session(f"Resuming checkpoint {checkpoint_id[:12]}..."):
            result = autopilot.resume(
                run_id,
                checkpoint_id,
                {"project_path": str(Path(project_path).expanduser().resolve())},
            )
        title, message, succeeded = _resume_outcome_display(result)
        if succeeded:
            ui.show_success(title, message)
            return 0
        ui.show_error(title, message)
        return 2
    except Exception as exc:
        ui.show_error("Resume failed", str(exc))
        return 2


def _resume_outcome_display(result: dict[str, object]) -> tuple[str, str, bool]:
    """Render typed recovery control fields; explanation text is display-only."""
    decision_value = result.get("resume_decision")
    decision = decision_value if isinstance(decision_value, dict) else {}
    fallback_value = decision.get("fallback")
    fallback = fallback_value if isinstance(fallback_value, dict) else {}
    recoverability = str(decision.get("recoverability") or "")
    reason_code = str(decision.get("reason_code") or "")
    fallback_action = str(fallback.get("action") or "none")
    explanation = str(decision.get("reason") or "").strip()
    instructions = str(fallback.get("instructions") or "").strip()
    succeeded = bool(result.get("success"))

    if succeeded and recoverability == "already_complete":
        title = "Checkpoint already completed"
    elif succeeded:
        title = "Checkpoint resumed successfully"
    elif recoverability == "not_recoverable":
        title = "Checkpoint is not recoverable"
    elif recoverability == "recoverable_after_action":
        title = "Resume action required"
    else:
        title = "Resume failed"

    control_summary = ", ".join(
        item
        for item in (
            f"reason={reason_code}" if reason_code else "",
            f"fallback={fallback_action}" if fallback_action else "",
        )
        if item
    )
    details = [item for item in (control_summary, explanation, instructions) if item]
    message = "\n".join(details) or str(result.get("resume_status") or "resume result unavailable")
    return title, message, succeeded

def _run_interactive_mode(
    ui: EnhancedUI,
    tracker: ProgressTracker,
    logger,
    settings: LLMSettings,
    args,
    llm_client,
    runtime_options: OpenPilotRuntimeOptions,
) -> int:
    """Run interactive REPL mode."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.history import InMemoryHistory
    from ui.commands import get_all_command_names

    # Get commands from registry
    commands = get_all_command_names()

    # Setup completer
    completer = WordCompleter(
        commands,
        ignore_case=True,
        WORD=True,  # 只在单词边界补全
    )

    # Setup history and auto-suggest
    history = InMemoryHistory()
    auto_suggest = AutoSuggestFromHistory()

    session = PromptSession(
        completer=completer,
        history=history,
        auto_suggest=auto_suggest,
        enable_history_search=True,  # Enable Ctrl+R history search
        complete_while_typing=True,  # 输入时自动显示补全菜单
        vi_mode=False,  # 确保使用 Emacs 模式（支持上下键历史）
    )
    conversation_id = f"conversation_{uuid4().hex}"
    ingress_state = SessionIngressState(
        identity=ConversationIdentity(
            conversation_id=conversation_id,
            run_id=f"run_{uuid4().hex}",
            turn_index=0,
            project_root=str(Path.cwd().expanduser().resolve()),
        )
    )

    ui.console.print()
    ui.console.print("[bold green]Welcome to OpenPilot Interactive Mode[/bold green]")
    ui.console.print("[dim]Type your task or use /help for commands[/dim]")
    if runtime_options.prompt_for_project_improvement_iterations:
        ui.console.print("[dim]Project improvement iterations: asked per generated project[/dim]")
    else:
        ui.console.print(
            f"[dim]Project improvement iterations: {runtime_options.improvement_iterations}[/dim]"
        )
    ui.console.print()

    tracker.start_tracking()

    try:
        while True:
            try:
                # Get user input
                user_input = session.prompt("openpilot> ")

                if not user_input.strip():
                    continue

                # Handle exit commands
                if user_input.strip() in ["/exit", "/quit", "exit", "quit", ":q"]:
                    ui.console.print("[yellow]Goodbye![/yellow]")
                    break

                # Handle help command
                if user_input.strip() == "/help":
                    _show_help(ui)
                    continue

                # Handle config command
                if user_input.strip() == "/config":
                    _show_config(ui, settings, runtime_options)
                    continue

                if _is_constraint_command(user_input):
                    ingress_state = _handle_constraint_command(user_input, ingress_state, ui)
                    continue

                # Handle clear command
                if user_input.strip() == "/clear":
                    ui.console.clear()
                    ui.show_banner()
                    continue

                # Handle goal execution
                if not user_input.startswith("/"):
                    ingress_state = _execute_goal_interactive(
                        user_input,
                        ui,
                        tracker,
                        llm_client,
                        logger,
                        runtime_options,
                        ingress_state=ingress_state,
                        settings=settings,
                    )
                else:
                    ui.console.print(f"[yellow]Unknown command: {user_input}[/yellow]")
                    ui.console.print("[dim]Type /help for available commands[/dim]")

            except KeyboardInterrupt:
                ui.console.print("\n[yellow]Interrupted. Type /exit to quit.[/yellow]")
                continue
            except EOFError:
                break

    finally:
        tracker.stop_tracking()

    return 0


def _execute_goal_interactive(
    goal: str,
    ui: EnhancedUI,
    tracker: ProgressTracker,
    llm_client,
    logger,
    runtime_options: OpenPilotRuntimeOptions,
    *,
    ingress_state: SessionIngressState | None = None,
    settings: LLMSettings | None = None,
):
    """Execute a goal in interactive mode."""
    if _handle_shell_state_command(goal, ui):
        return ingress_state if ingress_state is not None else None

    classification = _classify_task_route(goal)
    _show_task_route(ui, classification)
    if ingress_state is not None:
        run_id = f"run_{uuid4().hex}"
        turn = SessionTurn(
            identity=ConversationIdentity(
                conversation_id=ingress_state.identity.conversation_id,
                run_id=run_id,
                turn_index=ingress_state.identity.turn_index + 1,
                project_root=ingress_state.identity.project_root,
            ),
            message_id=f"message_{uuid4().hex}",
            role="user",
            content=goal,
        )
        ingress_state = SessionIngress.open_turn(ingress_state, turn)
    execution_context = _build_task_execution_context(source="interactive", classification=classification)
    if ingress_state is not None:
        execution_context.update(
            {
                "conversation_id": ingress_state.identity.conversation_id,
                "run_id": ingress_state.identity.run_id,
                "session_ingress_state": ingress_state,
            }
        )
    if _runtime_diagnostics_enabled() and classification.route != "agent_generator":
        get_default_hooks().on_route_selected(
            task_id=str(execution_context["task_id"]),
            route=classification.route,
            confidence=classification.confidence,
            reason=classification.reason,
        )
    if classification.route == "agent_generator":
        result = _execute_agent_generator(goal, ui, llm_client, logger)
    else:
        active_settings = settings or getattr(llm_client, "settings", None)
        if ingress_state is not None and isinstance(active_settings, LLMSettings):
            try:
                response = _try_unified_autonomous_response(
                    goal,
                    ingress_state=ingress_state,
                    settings=active_settings,
                    runtime_options=runtime_options,
                    llm_client=llm_client,
                    ui=ui,
                    tracker=tracker,
                    logger=logger,
                )
            except Exception as exc:
                failure = _cli_exception_failure(
                    exc,
                    task_id=str(execution_context.get("task_id") or "") or None,
                )
                show_error = getattr(ui, "show_error", None)
                error_title = (
                    exc.stage.title
                    if isinstance(exc, UnifiedEntryError)
                    else "Autonomous entry failed"
                )
                if callable(show_error):
                    show_error(error_title, _format_failure_details(failure))
                else:
                    ui.console.print(_format_failure_details(failure))
                return ingress_state
            if response is not None:
                ui.console.print(response.content)
                return response.ingress
        result = _execute_autopilot(goal, ui, tracker, llm_client, logger, runtime_options, context=execution_context)
    if ingress_state is None:
        return result
    assistant_turn = SessionTurn(
        identity=ConversationIdentity(
            conversation_id=ingress_state.identity.conversation_id,
            run_id=ingress_state.identity.run_id,
            turn_index=ingress_state.identity.turn_index + 1,
            project_root=ingress_state.identity.project_root,
        ),
        message_id=f"message_{uuid4().hex}",
        role="assistant",
        content=f"Execution result: {str(result)[:2000]}",
    )
    return SessionIngress.open_turn(ingress_state, assistant_turn)


def _handle_constraint_command(
    user_input: str,
    ingress_state: SessionIngressState,
    ui: EnhancedUI,
) -> SessionIngressState:
    """Apply explicit in-session constraint commands without Provider calls."""
    parts = user_input.strip().split()
    command = parts[0].casefold() if parts else "/constraints"
    if command == "/constraints" and len(parts) == 1:
        pending = [proposal.proposal_id for proposal in ingress_state.pending_proposals if proposal.status == "proposed"]
        active = [entry.constraint_key for entry in ingress_state.session_constraints.active_entries]
        ui.console.print(f"[dim]Pending constraint proposals: {pending or 'none'}[/dim]")
        ui.console.print(f"[dim]Active session constraints: {active or 'none'}[/dim]")
        return ingress_state
    try:
        if command in {"/confirm", "/reject"} and len(parts) == 2:
            if command == "/confirm":
                return SessionIngress.confirm_proposal(
                    ingress_state,
                    proposal_id=parts[1],
                    confirmation_turn=ingress_state.identity.turn_index + 1,
                )
            return SessionIngress.reject_proposal(ingress_state, proposal_id=parts[1])
        if command == "/revoke" and len(parts) == 2:
            return SessionIngress.revoke_constraint(
                ingress_state,
                constraint_key=parts[1],
                turn_index=ingress_state.identity.turn_index + 1,
            )
        ui.console.print("[yellow]Usage: /constraints | /confirm <proposal_id> | /reject <proposal_id> | /revoke <constraint_key>[/yellow]")
    except ValueError as exc:
        ui.console.print(f"[yellow]Constraint command rejected: {exc}[/yellow]")
    return ingress_state


def _classify_task_route(task: str) -> "TaskRouteMetadata":
    """Classify a user task before selecting the execution path."""
    from metadata import TaskRouteMetadata, ToolInputMetadata
    from tools.task_classifier import task_classifier_executor

    result = task_classifier_executor(ToolInputMetadata.from_mapping("task_classifier", {"task": task}))
    if not isinstance(result.result, TaskRouteMetadata):
        raise TypeError(f"task_classifier returned {type(result.result).__name__}, expected TaskRouteMetadata")
    return result.result


def _show_task_route(ui: EnhancedUI, classification: "TaskRouteMetadata") -> None:
    """Show the selected route without interrupting execution."""
    ui.console.print(f"[dim]Task route: {classification.route} ({classification.confidence:.2f}) - {classification.reason}[/dim]")


def _handle_shell_state_command(user_input: str, ui: EnhancedUI) -> bool:
    """Explain shell state commands that cannot affect the user's outer shell."""
    text = " ".join(str(user_input or "").strip().split())
    lowered = text.casefold()
    if not text:
        return False
    state_commands = (
        lowered.startswith("source ")
        or lowered.startswith(". ")
        or lowered.startswith("conda activate")
        or lowered == "deactivate"
        or lowered.startswith("deactivate ")
        or lowered.startswith("export ")
        or lowered.startswith("cd ")
    )
    if not state_commands:
        return False
    ui.console.print("[yellow]Shell state command was not executed inside OpenPilot.[/yellow]")
    ui.console.print(
        "[dim]OpenPilot runs project commands with the target project cwd and .venv injected through metadata. "
        "If you want to change your outer terminal environment, run this command in your system shell instead.[/dim]"
    )
    return True


def _show_help(ui: EnhancedUI):
    """Show help information."""
    from ui.commands import get_command_registry

    registry = get_command_registry()
    ui.console.print()
    ui.console.print(registry.format_help())
    ui.console.print()


def _show_config(
    ui: EnhancedUI,
    settings: LLMSettings,
    runtime_options: OpenPilotRuntimeOptions | None = None,
):
    """Show current configuration."""
    from rich.table import Table

    table = Table(title="Current Configuration", show_header=True, header_style="bold cyan")
    table.add_column("Setting", style="cyan", width=20)
    table.add_column("Value", style="white")

    table.add_row("Provider", settings.provider)
    table.add_row("Model", settings.model)
    table.add_row("Base URL", settings.base_url or "[dim]default[/dim]")
    table.add_row("Temperature", str(settings.temperature))
    table.add_row("Timeout", f"{settings.timeout_seconds}s")
    table.add_row("API Key", "✓ Set" if settings.api_key else "✗ Not set")
    embedding_settings = EmbeddingSettings()
    table.add_row("Embedding Provider", embedding_settings.provider)
    table.add_row("Embedding Model", embedding_settings.model)
    table.add_row("Embedding Base URL", embedding_settings.base_url or "[dim]inherits LLM[/dim]")
    table.add_row("Embedding Timeout", f"{embedding_settings.timeout_seconds}s")
    table.add_row("Embedding API Key", "✓ Set" if embedding_settings.api_key else "✗ Not set")
    if runtime_options is not None:
        if runtime_options.prompt_for_project_improvement_iterations:
            table.add_row("Improvement Iterations", "ask per generated project")
        else:
            table.add_row("Improvement Iterations", str(runtime_options.improvement_iterations))

    ui.console.print()
    ui.console.print(table)
    ui.console.print()


def _execute_autopilot(
    goal: str,
    ui: EnhancedUI,
    tracker: ProgressTracker,
    llm_client,
    logger,
    runtime_options: OpenPilotRuntimeOptions | None = None,
    context: dict[str, object] | None = None,
):
    """Execute goal using intelligent autopilot with enhanced UI."""
    from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
    from core.llm import LLMClient

    ui.console.print()
    runtime_options = runtime_options or OpenPilotRuntimeOptions()

    try:
        diagnostics_hooks = get_default_hooks() if _runtime_diagnostics_enabled() else None
        # Create autopilot with enhanced UI support
        autopilot = IntelligentAutopilot(
            llm_client=llm_client or LLMClient(),
            console=ui.console,
            auto_approve=True,
            logger=logger,
            use_enhanced_ui=True,
            enhanced_ui=ui,
            tracker=tracker,
            enable_iterative_improvement=runtime_options.enable_iterative_improvement,
            required_successful_improvements=runtime_options.improvement_iterations,
            project_improvement_policy=runtime_options.project_improvement_policy,
            prompt_for_project_improvement_iterations=runtime_options.prompt_for_project_improvement_iterations,
            runtime_diagnostics_hooks=diagnostics_hooks,
        )

        # Execute with live session
        with ui.live_session(f"Executing: {goal[:50]}..."):
            ui.update_main_content(
                ui.create_status_panel("Autopilot Mode", "Intelligent task decomposition and execution...")
            )

            result = autopilot.execute(goal, context=dict(context or {}))

            # Final status is already shown in the layout by autopilot
            # Just add a small delay to let user see it
            import time
            time.sleep(1)

        ui.show_full_task_graph_timeline()

        if result.get("success"):
            warning = result.get("iteration_error")
            if warning:
                ui.show_success(
                    "Goal completed with iteration warning",
                    warning,
                )
            else:
                ui.show_success("Goal completed!")
        else:
            ui.show_error("Autopilot execution failed", _format_failure_details(result))
        return result

    except Exception as e:
        ui.console.print()
        ui.show_full_task_graph_timeline()
        failure = _cli_exception_failure(
            e,
            task_id=str((context or {}).get("task_id") or "") or None,
            stage=UnifiedEntryFailureStage.PROJECT_EXECUTION,
        )
        ui.show_error("Autopilot execution failed", _format_failure_details(failure))
        return failure


def _execute_agent_generator(task: str, ui: EnhancedUI, llm_client = None, logger = None) -> bool:
    """Generate a reusable Python agent from an interactive task."""
    from pathlib import Path

    from agent_generator.runner import run_agent_generator
    from ui.environment_guard import agent_generator_llm_error_message, block_missing_socksio, block_project_venv

    if block_project_venv(ui.console):
        return False
    if block_missing_socksio(ui.console):
        return False
    output_dir = Path(__file__).resolve().parents[2] / "generated_agents"
    try:
        run_agent_generator(task, console=ui.console, output_dir=output_dir, llm_client=llm_client, logger=logger)
        return True
    except Exception as e:
        ui.show_error("Agent generation failed", agent_generator_llm_error_message(e))
        return False
