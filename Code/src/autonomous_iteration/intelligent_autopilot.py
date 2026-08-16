"""Intelligent Autopilot executor using dynamic task decomposition and tools."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import shlex
import sys
import time
import uuid
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from autonomous_iteration.agents.iteration_agent import AutonomousIterationAgent
from autonomous_iteration.agents.project_evaluator import ProjectEvaluatorAgent
from core.llm import LLMClient
from core.semantic_analyzer import SemanticAnalyzer
from core.tool_event_emitter import ToolEventEmitter
from memory.memory_store import MemoryStore
from autonomous_iteration.task_models import (
    Task,
    TaskStatus,
    TaskPriority,
    TaskExecutionContext,
    TaskExecutionResult
)
from autonomous_iteration.models import EvaluationResult, IterationResult
from tools.tool_selection import (
    ToolSelection,
)
from tools.tool_registry import ToolRegistry
from tools.tool_executor import ToolExecutor
from tools.environment_fix_tool import summarize_environment_failure
from tools.mutation_descriptor import file_mutation_targets
from tools.executor_models import ExecutionError, ExecutionStatus
from core.openpilot_log import OpenPilotLogger
from core.exceptions import ErrorCategory, OpenPilotError, classify_error
from metadata import (
    EnvironmentOperation,
    EnvironmentReadiness,
    ExecutionStateMetadata,
    FailureMetadata,
    ProjectObjectiveMetadata,
    ProjectImprovementPolicy,
    ProjectImprovementPolicySource,
    ProjectImprovementRequirement,
    ReferenceInsightMetadata,
    ResultStatus,
    SuccessMetricMetadata,
    TaskResultMetadata,
    TextArtifactMetadata,
    TaskGraphEdgeMetadata,
    TaskGraphNodeMetadata,
    ToolExecutionEnvelopeMetadata,
    ToolCallMetadata,
    ToolContextMetadata,
    ToolErrorMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
    VerificationPlanMetadata,
    RuntimeBudgetMetadata,
    DerivedContextProjection,
    SessionConstraintState,
    SessionIngressState,
)
from autonomous_iteration.improvement_context import ImprovementContextHelper
from autonomous_iteration.project_improvement_runtime import ProjectImprovementRuntime
from memory.session_dialog import session_turn_ledger_hash
from autonomous_iteration.task_executor import AutonomousTaskExecutor
from autonomous_iteration.agents.execution_orchestrator import AgentOrchestrator
from autonomous_iteration.agents.execution_task_decomposer import TaskDecomposer
from autonomous_iteration.agents.tool_planning_executor import ToolPlanningTaskExecutor
from autonomous_iteration.skill_specs import (
    SkillCapabilityCardProvider,
    default_skill_search_roots,
    load_skill_specs,
)
from ui.console_presenter import ConsolePresenter
from ui.iteration_dashboard import IterationDashboardAdapter
from autonomous_iteration.project_iteration import ProjectIterationHelper
from autonomous_iteration.tool_io import ExecutionToolIO
from autonomous_iteration.runtime_controller import AgentRuntimeController
from runtime_diagnostics.llm_proxy import TrajectoryLLMClientProxy
from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks, get_default_hooks


class IntelligentAutopilot:
    """Intelligent autopilot using dynamic task decomposition."""

    def __init__(
        self,
        llm_client: LLMClient,
        console: Console | None = None,
        auto_approve: bool = True,
        logger: OpenPilotLogger | None = None,
        log_file: str | Path | None = None,
        use_enhanced_ui: bool = False,
        enhanced_ui: Any | None = None,
        tracker: Any | None = None,
        enable_iterative_improvement: bool = True,
        required_successful_improvements: int = 2,
        required_successful_iterations: int | None = None,
        max_iteration_attempts: int = 4,
        project_improvement_policy: ProjectImprovementPolicy | None = None,
        prompt_for_project_improvement_iterations: bool = False,
        project_objective_override: ProjectObjectiveMetadata | None = None,
        success_metric_overrides: list[SuccessMetricMetadata] | None = None,
        preferred_improvement_dimensions: list[str] | None = None,
        disallowed_improvement_directions: list[str] | None = None,
        allow_reference_search: bool = True,
        reference_provider: Callable[..., list[Any]] | None = None,
        runtime_diagnostics_hooks: RuntimeDiagnosticsHooks | None = None,
        skill_roots: list[str | Path] | None = None,
    ):
        """Initialize intelligent autopilot.

        Args:
            llm_client: LLM client
            console: Rich console
            auto_approve: Auto-approve low/medium risk operations
            logger: Logger instance
            log_file: Log file path
            use_enhanced_ui: Use enhanced UI with progress tracking
            enhanced_ui: Existing enhanced UI instance to update
            tracker: Existing progress tracker to reuse
            enable_iterative_improvement: Run evaluation and improvement loops for project outputs
            required_successful_improvements: Successful code improvement rounds required before stopping
            required_successful_iterations: Backward-compatible alias for required_successful_improvements
            max_iteration_attempts: Maximum improvement/repair attempts before stopping
            prompt_for_project_improvement_iterations: Ask per generated project how many improvement rounds to run
        """
        self.console = console or Console()
        self.auto_approve = auto_approve
        self.use_enhanced_ui = use_enhanced_ui
        if required_successful_iterations is not None:
            required_successful_improvements = required_successful_iterations
        if project_improvement_policy is None:
            if not enable_iterative_improvement or required_successful_improvements <= 0:
                project_improvement_policy = ProjectImprovementPolicy(
                    requirement=ProjectImprovementRequirement.DISABLED,
                    source=ProjectImprovementPolicySource.LEGACY_CONFIG,
                    target_successes=0,
                    max_attempts=0,
                )
            else:
                is_automatic_default = (
                    required_successful_iterations is None
                    and required_successful_improvements == 2
                    and max_iteration_attempts == 4
                )
                target_attempts = max(
                    max_iteration_attempts,
                    AutonomousIterationAgent.minimum_attempt_budget(required_successful_improvements),
                )
                project_improvement_policy = ProjectImprovementPolicy(
                    requirement=(
                        ProjectImprovementRequirement.OPTIONAL
                        if is_automatic_default
                        else ProjectImprovementRequirement.REQUIRED
                    ),
                    source=(
                        ProjectImprovementPolicySource.AUTOMATIC_DEFAULT
                        if is_automatic_default
                        else ProjectImprovementPolicySource.LEGACY_CONFIG
                    ),
                    target_successes=required_successful_improvements,
                    max_attempts=target_attempts,
                )
        self.project_improvement_policy = project_improvement_policy
        self.enable_iterative_improvement = project_improvement_policy.enabled
        self.required_successful_improvements = project_improvement_policy.target_successes
        self.max_iteration_attempts = project_improvement_policy.max_attempts
        self.prompt_for_project_improvement_iterations = prompt_for_project_improvement_iterations
        self.allow_reference_search = allow_reference_search
        code_root = Path(__file__).resolve().parents[2]
        self.skill_specs = load_skill_specs(skill_roots or default_skill_search_roots(code_root))
        self.planning_surface_providers: list[Any] = []
        if self.skill_specs:
            self.planning_surface_providers.append(SkillCapabilityCardProvider(self.skill_specs))
        if runtime_diagnostics_hooks is not None:
            self.runtime_diagnostics_hooks = runtime_diagnostics_hooks
        elif str(os.getenv("OPENPILOT_RUNTIME_DIAGNOSTICS_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}:
            self.runtime_diagnostics_hooks = get_default_hooks()
        else:
            self.runtime_diagnostics_hooks = None
        self._project_improvement_iterations_prompted = False
        self._project_environments: dict[str, dict[str, Any]] = {}

        # Initialize UI components
        if use_enhanced_ui:
            from ui.enhanced_ui import EnhancedUI
            from ui.progress_tracker import ProgressTracker
            from core.instrumented_llm import InstrumentedLLMClient

            self.enhanced_ui = enhanced_ui or EnhancedUI(self.console)
            self.tracker = tracker or ProgressTracker(self.enhanced_ui)
            self._owns_tracker = tracker is None
            if isinstance(llm_client, InstrumentedLLMClient):
                llm_client.tracker = self.tracker
                self.llm_client = llm_client
            elif hasattr(llm_client, "settings"):
                self.llm_client = InstrumentedLLMClient(llm_client.settings, self.tracker)
            else:
                self.llm_client = llm_client
        else:
            self.enhanced_ui = None
            self.tracker = None
            self._owns_tracker = False
            self.llm_client = llm_client

        self._current_task_id: str = ""
        self._current_goal: str = ""
        if self.runtime_diagnostics_hooks and hasattr(self.llm_client, "complete") and not isinstance(self.llm_client, TrajectoryLLMClientProxy):
            self.llm_client = TrajectoryLLMClientProxy(
                self.llm_client,
                hooks=self.runtime_diagnostics_hooks,
                task_id_getter=lambda: self._current_task_id,
                session_id_getter=lambda: str(self.session_id or ""),
                phase_getter=self._diagnostic_phase,
                goal_getter=lambda: self._current_goal,
            )

        # Initialize logger
        default_log_file = Path(__file__).resolve().parents[2] / "logs" / "autopilot.jsonl"
        self.logger = logger or OpenPilotLogger(log_file or default_log_file)

        # Session tracking
        self.session_id: str | None = None
        self.conversation_id: str | None = None
        self.stats = {
            "start_time": None,
            "end_time": None,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "success": False,
        }

        # Initialize components
        self.task_decomposer = TaskDecomposer(
            self.llm_client,
            logger=self.logger,
            session_id_getter=lambda: self.session_id,
        )
        self.project_evaluator = ProjectEvaluatorAgent(
            self.llm_client,
            logger=self.logger,
            session_id_getter=lambda: self.session_id,
        )
        self.memory_store = MemoryStore()
        try:
            from memory.context_builder import MemoryContextBuilder
            from memory.agents.memory_vault_agent import MemoryVaultAgent
            from core.token_counting import ProviderTokenCounter
            from memory.rolling_compaction import RollingSummaryAdapter
            from memory.rolling_summary_factory import build_llm_rolling_summary_request_factory

            self.memory_vault_agent = MemoryVaultAgent(
                memory_store=self.memory_store,
                logger=self.logger,
                session_id_getter=lambda: self.session_id,
            )
            llm_settings = getattr(self.llm_client, "settings", None)
            token_counter = ProviderTokenCounter.from_settings(llm_settings)
            rolling_summary_enabled = bool(
                getattr(llm_settings, "rolling_summary_enabled", False)
            )
            rolling_summary_adapter = (
                RollingSummaryAdapter(count_tokens=token_counter.count_text)
                if rolling_summary_enabled
                else None
            )
            rolling_summary_request_factory = (
                build_llm_rolling_summary_request_factory(
                    self.llm_client,
                    context_max_prompt_tokens=int(
                        getattr(llm_settings, "context_max_prompt_tokens", 4096) or 4096
                    ),
                    timeout_seconds=getattr(llm_settings, "timeout_seconds", None),
                    transport_retries=0,
                )
                if rolling_summary_enabled
                else None
            )
            self.memory_context_builder = MemoryContextBuilder(
                memory_store=self.memory_store,
                memory_vault_agent=self.memory_vault_agent,
                token_counter=token_counter,
                max_prompt_tokens=int(
                    getattr(llm_settings, "context_max_prompt_tokens", 4096) or 4096
                ),
                rolling_summary_enabled=rolling_summary_enabled,
                rolling_summary_adapter=rolling_summary_adapter,
                rolling_summary_request_factory=rolling_summary_request_factory,
                rolling_summary_token_limit=int(
                    getattr(llm_settings, "rolling_summary_token_limit", 256) or 256
                ),
            )
        except Exception as exc:
            self.memory_vault_agent = None
            self.memory_context_builder = None
            self.logger.log_structured_event(
                source_type="module",
                source_name="autonomous_iteration.intelligent_autopilot.memory_context_builder",
                phase="initialization",
                event_type="module_failed",
                session_id="unknown",
                turn_id=1,
                success=False,
                error=str(exc),
            )
        self._local_enhancement_runtime_budget = RuntimeBudgetMetadata()
        self.iterative_improvement = AutonomousIterationAgent(
            self.project_evaluator,
            required_successful_improvements=self.required_successful_improvements,
            max_iteration_attempts=self.max_iteration_attempts,
            llm_client=self.llm_client,
            memory_store=self.memory_store,
            memory_context_builder=self.memory_context_builder,
            logger=self.logger,
            project_objective_override=project_objective_override,
            success_metric_overrides=success_metric_overrides,
            preferred_improvement_dimensions=preferred_improvement_dimensions,
            disallowed_improvement_directions=disallowed_improvement_directions,
            allow_reference_search=allow_reference_search,
            reference_provider=reference_provider or self._gather_project_reference_insights,
            runtime_budget=self._local_enhancement_runtime_budget,
        )
        self.orchestrator = AgentOrchestrator(max_concurrent_tasks=3)
        self.semantic_analyzer = SemanticAnalyzer(self.llm_client)
        self.tool_registry = ToolRegistry()

        # Register built-in tools
        from tools.builtin_tools import register_builtin_tools
        register_builtin_tools(self.tool_registry)
        self._register_contextual_tools()

        # Use instrumented executor if enhanced UI is enabled
        if use_enhanced_ui:
            from tools.instrumented_executor import InstrumentedToolExecutor
            self.tool_executor = InstrumentedToolExecutor(self.tool_registry, self.tracker)
        else:
            self.tool_executor = ToolExecutor(self.tool_registry, logger=self.logger)

        self.tool_io = ExecutionToolIO(self.logger, lambda: self.session_id)
        self.project_iteration = ProjectIterationHelper(self.logger, lambda: self.session_id)
        self.iteration_dashboard = IterationDashboardAdapter(self, self.logger, lambda: self.session_id)
        self.improvement_context = ImprovementContextHelper(
            environment_context_getter=self._project_environment_context,
            logger=self.logger,
            session_id_getter=lambda: self.session_id,
        )
        self.project_improvement_runtime = ProjectImprovementRuntime(self)
        self.autonomous_task_executor = AutonomousTaskExecutor(self)
        self.tool_planning_task_executor = ToolPlanningTaskExecutor(self)
        self.console_presenter = ConsolePresenter(
            self.console,
            auto_approve_getter=lambda: self.auto_approve,
            stats_getter=lambda: self.stats,
            logger=self.logger,
            session_id_getter=lambda: self.session_id,
        )
        self.runtime_controller = AgentRuntimeController(self)

        # Register task executor
        self.orchestrator.set_task_executor(self._execute_task)

    def _register_contextual_tools(self) -> None:
        """Register tool wrappers that can reuse this autopilot's runtime context."""
        from tools.code_generator import CODE_GENERATOR_DEFINITION, code_generator_executor
        from tools.code_editor import CODE_EDITOR_DEFINITION, code_editor_executor
        from tools.code_unit_generator import CODE_UNIT_GENERATOR_DEFINITION, code_unit_generator_executor

        def _with_llm_client(input_metadata: ToolInputMetadata, tool_name: str) -> ToolInputMetadata:
            if not isinstance(input_metadata, ToolInputMetadata):
                raise TypeError(f"contextual {tool_name} requires ToolInputMetadata")
            runtime_handles = dict(input_metadata.runtime_handles)
            runtime_handles["_llm_client"] = self.llm_client
            return input_metadata.model_copy(update={"runtime_handles": runtime_handles})

        def execute_code_generator(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
            return code_generator_executor(_with_llm_client(input_metadata, "code_generator"))

        def execute_code_unit_generator(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
            return code_unit_generator_executor(_with_llm_client(input_metadata, "code_unit_generator"))

        def execute_code_editor(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
            return code_editor_executor(_with_llm_client(input_metadata, "code_editor"))

        self.tool_registry.register(
            CODE_GENERATOR_DEFINITION,
            execute_code_generator,
            allow_override=True,
        )
        self.tool_registry.register(
            CODE_UNIT_GENERATOR_DEFINITION,
            execute_code_unit_generator,
            allow_override=True,
        )
        self.tool_registry.register(
            CODE_EDITOR_DEFINITION,
            execute_code_editor,
            allow_override=True,
        )

    def _gather_project_reference_insights(self, query: str, state: Any, objective: Any) -> list[ReferenceInsightMetadata]:
        """Fetch a compact optional reference insight for a low-evidence diagnosis."""
        if not self.allow_reference_search:
            return []
        try:
            from tools.web_searcher import web_searcher_executor

            result = web_searcher_executor(
                ToolInputMetadata.from_mapping(
                    "web_searcher",
                    {
                        "query": query,
                        "max_results": 3,
                        "max_pages": 0,
                        "llm_cleanup": False,
                        "timeout": 8,
                    },
                )
            )
        except Exception:
            return []
        artifact = result.result if isinstance(result, ToolResultMetadata) else None
        if artifact is None:
            return []
        summary = str(getattr(artifact, "research_summary", "") or "")
        results = getattr(artifact, "results", []) or []
        source_notes = []
        best_practices = []
        for item in results[:3]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            snippet = str(item.get("snippet") or item.get("summary") or "").strip()
            if title:
                source_notes.append(title[:180])
            if snippet:
                best_practices.append(snippet[:220])
        if not summary and not best_practices:
            return []
        return [
            ReferenceInsightMetadata(
                query=query,
                summary=summary or f"Reference search returned {len(results)} result(s) for {getattr(objective, 'project_type', 'project')}.",
                best_practices=best_practices,
                applicability="external_reference",
                source_notes=source_notes,
                confidence=0.45,
            )
        ]

    def _stop_tracking_if_owned(self) -> None:
        if self.tracker and self._owns_tracker:
            self.tracker.stop_tracking()

    def execute(self, goal: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute goal using intelligent task decomposition.

        Args:
            goal: User goal
            context: Optional context information

        Returns:
            Execution result
        """
        self.stats["start_time"] = datetime.now()
        context = self._normalize_execution_context(context or {})
        raw_ingress = context.get("session_ingress_state")
        if raw_ingress is not None and not isinstance(raw_ingress, SessionIngressState):
            raise TypeError("session_ingress_state must be a validated SessionIngressState")
        if isinstance(raw_ingress, SessionIngressState):
            for identity_key in ("conversation_id", "session_id"):
                supplied_identity = str(context.get(identity_key) or "").strip()
                if supplied_identity and supplied_identity != raw_ingress.identity.conversation_id:
                    raise ValueError("session ingress conversation identity mismatch")
            supplied_project = str(context.get("project_path") or "").strip()
            if supplied_project:
                ingress_project = Path(raw_ingress.identity.project_root).expanduser().resolve(strict=False)
                requested_project = Path(supplied_project).expanduser().resolve(strict=False)
                if ingress_project != requested_project:
                    raise ValueError("session ingress project identity mismatch")
            context.setdefault("conversation_id", raw_ingress.identity.conversation_id)
            context.setdefault("project_path", raw_ingress.identity.project_root)
            context["session_constraints"] = raw_ingress.session_constraints
        self.session_id = str(context.get("run_id") or uuid.uuid4())
        self.conversation_id = str(
            context.get("conversation_id")
            or context.get("session_id")
            or self.session_id
        )
        context["conversation_id"] = self.conversation_id
        context["run_id"] = self.session_id
        self._current_execution_context = context
        self._current_goal = goal
        self._current_task_id = str(context.get("task_id") or self.session_id)
        if self.runtime_diagnostics_hooks:
            self.runtime_diagnostics_hooks.on_task_received(
                task_id=self._current_task_id,
                source=str(context.get("source") or "interactive"),
                raw_input=goal,
                extra={"tags": list(context.get("tags") or [])},
                session_id=self.session_id,
            )

        mode = "enhanced_ui" if self.use_enhanced_ui and self.enhanced_ui and self.tracker else "standard"
        try:
            return self.runtime_controller.run(goal, context, mode=mode)
        except Exception as e:
            if self.enhanced_ui:
                self.enhanced_ui.log_activity("error", f"Execution failed: {str(e)}")
            self.logger.log_event(
                "execution_error",
                {"error": str(e), "goal": goal},
                session_id=self.session_id or "unknown",
                turn_id=1,
            )
            if classify_error(e) in {ErrorCategory.NETWORK, ErrorCategory.TIMEOUT, ErrorCategory.RETRYABLE}:
                return self._structured_execution_error(goal, e)
            raise

    def resume(
        self,
        run_id: str,
        checkpoint_id: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resume one explicit durable checkpoint without creating a new session identity."""
        normalized_context = self._normalize_execution_context(context or {})
        self._current_execution_context = normalized_context
        self._reattach_resume_environment(normalized_context)
        mode = "enhanced_ui" if self.use_enhanced_ui and self.enhanced_ui and self.tracker else "standard"
        return self.runtime_controller.resume(
            run_id,
            checkpoint_id,
            normalized_context,
            mode=mode,
        )

    def _reattach_resume_environment(self, context: dict[str, Any]) -> None:
        """Rebuild the ready-environment cache with a read-only resume preflight."""
        raw_project_path = str(context.get("project_path") or context.get("cwd") or "").strip()
        if not raw_project_path:
            return
        from memory.agents.project_environment_tool import inspect_project_environment

        project_path = Path(raw_project_path).expanduser().resolve()
        preflight = inspect_project_environment(project_path=project_path)
        if not hasattr(self, "_project_environments"):
            self._project_environments = {}
        self._project_environments.pop(str(project_path), None)
        if preflight.readiness == EnvironmentReadiness.READY:
            self._project_environments[str(project_path)] = preflight.to_json_dict()

    def _normalize_execution_context(self, context: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(context or {})
        if not str(normalized.get("cwd") or "").strip():
            normalized["cwd"] = str(Path.cwd().expanduser().resolve())
        return normalized

    def _task_parent_context(self, goal: str) -> dict[str, Any]:
        parent_context: dict[str, Any] = {
            "goal": goal,
            "session_id": self.session_id,
            "conversation_id": getattr(self, "conversation_id", None) or self.session_id,
        }
        current_context = getattr(self, "_current_execution_context", {}) or {}
        for key in (
            "cwd",
            "project_path",
            "target_dir",
            "output_dir",
            "run_command",
            "test_command",
            "validation_command",
            "validation_context",
            "conversation_id",
            "run_id",
            "session_constraints",
        ):
            value = current_context.get(key)
            if value is not None and value != "":
                parent_context[key] = value
        return parent_context

    def _diagnostic_phase(self) -> str:
        controller = getattr(self, "runtime_controller", None)
        state = getattr(controller, "state", None)
        phase = getattr(state, "phase", "")
        if hasattr(phase, "value"):
            return str(phase.value)
        return str(phase or "")

    def _structured_execution_error(self, goal: str, error: Exception) -> dict[str, Any]:
        category = classify_error(error)
        self.stats["success"] = False
        self.stats["tasks_failed"] = max(1, self.stats.get("tasks_failed", 0))
        self.stats["end_time"] = datetime.now()
        if self.enhanced_ui:
            self.enhanced_ui.set_current_task_state(
                title="Autopilot execution failed",
                details=(
                    f"Stage: LLM Transport\n"
                    f"Category: {category.value}\n"
                    f"Reason: {error}"
                ),
                status="failed",
            )
        self._stop_tracking_if_owned()
        return {
            "success": False,
            "goal": goal,
            "error": str(error),
            "failure_stage": "LLM Transport",
            "failed_tool": "llm_client",
            "failure_reason": str(error),
            "retry_attempted": True,
            "retry_history": [],
            "partial_success": False,
            "stats": self.stats,
        }

    def _try_simple_code_artifact_fast_path(self, goal: str, semantic: Any) -> dict[str, Any] | None:
        """Generate simple single-file code artifacts without multi-step decomposition."""
        target_file = self._simple_code_artifact_target(goal, semantic)
        if target_file is None:
            return None

        if self.enhanced_ui:
            self.enhanced_ui.set_current_task_state(
                title="Fast path code generation",
                details=f"Target: {target_file}",
                status="running",
            )
            self.enhanced_ui.update_main_content(
                self.enhanced_ui.create_status_panel(
                    "Task Decomposition",
                    "Simple code artifact detected. Using 1-step fast path."
                )
            )
            self.enhanced_ui.log_activity("task", "Task Decomposition: 1 fast-path code task")
        else:
            self.console.print("[cyan]Fast path:[/cyan] generating a single code artifact")

        self._project_improvement_actions: list[str] = []
        task = Task(
            id=str(uuid.uuid4()),
            description=f"Generate complete code artifact at {target_file}",
            priority=TaskPriority.HIGH,
            kind="implement",
            write_files=[str(target_file), str(target_file.parent / "README.md")],
            validation_command=f"python {shlex.quote(target_file.name)}",
        )
        started = datetime.now()
        tool_results: list[ToolExecutionEnvelopeMetadata] = []

        code_prompt = (
            f"Create a complete, runnable single-file Python program for this user request: {goal}\n"
            f"Write the final artifact for: {target_file}\n"
            "Prefer Python standard library modules when practical. If this is a game, include controls, "
            "score display, restart or exit behavior, collision/game-over handling, and clear inline comments. "
            "Return only the Python source code."
        )

        code_result = self._execute_fast_tool(
            task=task,
            step_id="fast_code_generator",
            tool_name="code_generator",
            input_metadata=ToolInputMetadata.from_mapping("code_generator", {
                "task_description": code_prompt,
                "language": "python",
                "context": f"Output file: {target_file}",
            }),
        )
        tool_results.append(code_result)

        code = ""
        if code_result.success and code_result.output is not None:
            code = str(code_result.output.get("code", ""))

        syntax_error = None
        if code:
            try:
                ast.parse(code)
            except SyntaxError as exc:
                syntax_error = f"Syntax error on line {exc.lineno}: {exc.msg}"

        if code and syntax_error is None:
            write_result = self._execute_fast_tool(
                task=task,
                step_id="fast_file_writer",
                tool_name="file_writer",
                input_metadata=ToolInputMetadata.from_mapping("file_writer", {
                    "file_path": str(target_file),
                    "content": code,
                    "encoding": "utf-8",
                    "create_dirs": True,
                    "overwrite": True,
                }),
            )
            tool_results.append(write_result)
            if write_result.success:
                environment_result = self._sync_project_environment(
                    task=task,
                    step_id="fast_project_environment_tool",
                    project_path=target_file.parent,
                    written_files=[str(target_file)],
                    entry_files=[str(target_file)],
                    run_command=f"python {shlex.quote(target_file.name)}",
                    goal=goal,
                )
                tool_results.append(environment_result)
                if not environment_result.success:
                    if self.enhanced_ui:
                        self.enhanced_ui.set_current_task_state(
                            title="Environment Setup failed",
                            details=summarize_environment_failure(environment_result.error_message or ""),
                            status="failed",
                        )
                    readme_result = None
                    environment_payload = None
                    run_command = f"python {shlex.quote(target_file.name)}"
                else:
                    readme_result = None
                    environment_payload = environment_result.output
                    run_command = str(environment_payload.get("run_command") or f"python {shlex.quote(target_file.name)}") if environment_payload else f"python {shlex.quote(target_file.name)}"
                if environment_result.success:
                    readme_result = self._execute_fast_tool(
                        task=task,
                        step_id="fast_readme_tool",
                        tool_name="readme_tool",
                        input_metadata=ToolInputMetadata.from_mapping("readme_tool", {
                            "project_path": str(target_file.parent),
                            "project_summary": goal,
                            "written_files": [str(target_file)],
                            "entry_files": [str(target_file)],
                            "run_command": run_command,
                            "setup_commands": (environment_payload.get("setup_commands") or []) if environment_payload else [],
                            "environment": self._readme_environment_context(environment_payload.to_json_dict() if environment_payload else {}),
                            "overwrite": True,
                        }),
                    )
                    tool_results.append(readme_result)
        elif syntax_error:
            tool_results.append(ToolExecutionEnvelopeMetadata(
                tool_name="syntax_validation",
                step_id="fast_syntax_validation",
                status=ResultStatus.FAIL,
                success=False,
                input_metadata=ToolInputMetadata.from_mapping("syntax_validation", {"file_path": str(target_file)}),
                failure=FailureMetadata(error_type="SyntaxError", error_message=syntax_error),
            ))

        primary_results = [result for result in tool_results if result.tool_name != "readme_tool"]
        core_success = all(result.success for result in primary_results)
        success = core_success
        duration = (datetime.now() - started).total_seconds()
        error_msg = None
        if not success:
            errors = [r.error_message for r in primary_results if not r.success]
            error_msg = "; ".join(error for error in errors if error) or "Fast-path code generation failed"
        readme_result = next((r for r in tool_results if r.tool_name == "readme_tool"), None)
        readme_error = readme_result.error_message if readme_result and not readme_result.success else None
        improvement_result = None
        iteration_error_msg = None
        if core_success:
            run_command = f"python {shlex.quote(target_file.name)}"
            environment_result = next((r for r in tool_results if r.tool_name == "project_environment_tool" and r.success), None)
            if environment_result and environment_result.output is not None:
                run_command = str(environment_result.output.get("run_command") or run_command)
            try:
                improvement_result = self._run_iterative_improvement(
                    goal=goal,
                    project_path=target_file.parent,
                    written_files=[str(target_file)],
                    run_command=run_command,
                    readme_path=(
                        readme_result.output.get("file_path")
                        if readme_result and readme_result.output is not None
                        else target_file.parent / "README.md"
                    ),
                    session_ingress_state=(
                        self._current_execution_context.get("session_ingress_state")
                        if isinstance(getattr(self, "_current_execution_context", None), dict)
                        else None
                    ),
                )
            except Exception as exc:
                improvement_result = {
                    "success": False,
                    "status": "interrupted",
                    "error_type": type(exc).__name__,
                    "failure_stage": "Project Improvement",
                    "failed_tool": "project_improvement_runtime",
                    "failure_reason": str(exc),
                }
            if improvement_result is not None and not improvement_result.get("success", False):
                iteration_error_msg = self._format_iteration_failure(improvement_result)
                if self.project_improvement_policy.controls_top_level_success:
                    success = False
                    error_msg = iteration_error_msg

        task_result = TaskExecutionResult(
            task_id=task.id,
            status=TaskStatus.COMPLETED if core_success else TaskStatus.FAILED,
            result_metadata=TaskResultMetadata(
                task_id=task.id,
                status=ResultStatus.SUCCESS if core_success else ResultStatus.FAIL,
                result=TextArtifactMetadata(
                    content="completed" if core_success else (error_msg or "failed"),
                    attributes={
                        "description": task.description,
                        "tool_results": [tool_result.to_json_dict() for tool_result in tool_results],
                        "all_tools_succeeded": core_success,
                        "final_output": tool_results[-1].output.to_json_dict() if tool_results and tool_results[-1].output else None,
                    },
                ) if core_success else None,
                failure=None if core_success else FailureMetadata(error_type="FastPathError", error_message=error_msg or "Fast-path code generation failed"),
            ),
            error=None if core_success else error_msg,
            duration=duration,
            annotations={"fast_path": True, "target_file": str(target_file)},
        )

        self.stats["success"] = success
        self.stats["tasks_completed"] = 1 if core_success else 0
        self.stats["tasks_failed"] = 0 if core_success else 1
        self.stats["end_time"] = datetime.now()
        self._stop_tracking_if_owned()

        if self.enhanced_ui:
            fast_details = (
                f"Wrote {target_file}"
                if success
                else f"Fast-path execution failed: {error_msg}"
            )
            if success and readme_result and readme_result.success and readme_result.output is not None:
                fast_details += f"\nREADME: {readme_result.output.get('file_path', 'README.md')}"
            elif success and readme_error:
                fast_details += f"\nREADME generation failed: {readme_error}"
            if improvement_result:
                if improvement_result.get("validation"):
                    fast_details += (
                        f"\nImprovements applied: {improvement_result.get('completed_improvements', 0)}/"
                        f"{improvement_result.get('required_improvements', self.required_successful_improvements)}"
                    )
                if iteration_error_msg:
                    fast_details += f"\nIteration warning: {iteration_error_msg}"
            self.enhanced_ui.update_main_content(
                self.enhanced_ui.create_status_panel(
                    "Success" if success else "Failed",
                    fast_details,
                )
            )

        self.logger.log_event(
            "fast_path_completed",
            {
                "goal": goal,
                "target_file": str(target_file),
                "success": success,
                "error": error_msg,
                "iteration_error": iteration_error_msg,
                "readme_error": readme_error,
                "improvement": self._summarize_metadata_output(improvement_result),
            },
            session_id=self.session_id or "unknown",
            turn_id=1,
        )

        return {
            "success": success,
            "core_success": core_success,
            "project_improvement_policy": self.project_improvement_policy.model_dump(mode="json"),
            "project_improvement_status": (
                "skipped"
                if not self.project_improvement_policy.enabled or improvement_result is None
                else "succeeded"
                if improvement_result.get("success")
                else "interrupted"
                if improvement_result.get("status") == "interrupted"
                else "failed"
            ),
            "goal": goal,
            "semantic_analysis": semantic,
            "fast_path": True,
            "target_file": str(target_file),
            "results": [task_result],
            "stats": self.stats,
            "error": error_msg,
            "iteration_error": iteration_error_msg,
            "readme": readme_result,
            "validation": improvement_result.get("validation") if improvement_result else None,
            "evaluation": improvement_result.get("evaluation") if improvement_result else None,
            "completed_improvements": improvement_result.get("completed_improvements", 0) if improvement_result else 0,
            "required_improvements": improvement_result.get("required_improvements", self.required_successful_improvements) if improvement_result else self.required_successful_improvements,
            "completed_iterations": improvement_result.get("completed_iterations", 0) if improvement_result else 0,
            "required_iterations": improvement_result.get("required_iterations", self.required_successful_improvements) if improvement_result else self.required_successful_improvements,
            "improvement_report": improvement_result.get("improvement_report", {}) if improvement_result else {},
            "iterations": improvement_result.get("iterations", []) if improvement_result else [],
            "partial_success": improvement_result.get("partial_success", False) if improvement_result else False,
            "failure_stage": improvement_result.get("failure_stage") if improvement_result else None,
            "failed_iteration": improvement_result.get("failed_iteration") if improvement_result else None,
            "failed_tool": improvement_result.get("failed_tool") if improvement_result else None,
            "failure_reason": improvement_result.get("failure_reason") if improvement_result else None,
            "retry_attempted": improvement_result.get("retry_attempted", False) if improvement_result else False,
            "retry_history": improvement_result.get("retry_history", []) if improvement_result else [],
            "remaining_goals": improvement_result.get("remaining_goals", []) if improvement_result else [],
        }

    def _simple_code_artifact_target(self, goal: str, semantic: Any) -> Path | None:
        path_match = re.search(r"['\"](?P<path>/[^'\"]+)['\"]", goal)
        if not path_match:
            return None

        goal_lower = goal.lower()
        code_keywords = ("snake", "贪吃蛇", "game", "小游戏", "脚本", "script", "程序", "app")
        if not any(keyword in goal_lower for keyword in code_keywords):
            return None

        task_type = getattr(getattr(semantic, "task_type", None), "value", getattr(semantic, "task_type", ""))
        if task_type and task_type not in {"coding", "file_workflow", "automation", "unknown"}:
            return None

        requested_path = Path(path_match.group("path")).expanduser()
        if requested_path.suffix == ".py":
            return requested_path
        return requested_path / "main.py"

    def _execute_fast_tool(
        self,
        task: Task,
        step_id: str,
        tool_name: str,
        input_metadata: ToolInputMetadata,
        timeout_override: int | None = None,
        parent_task_id: str | None = None,
    ) -> ToolExecutionEnvelopeMetadata:
        if timeout_override is None:
            timeout_override = self._llm_tool_timeout_override(tool_name)
        typed_input = self._apply_project_command_context(tool_name, input_metadata)
        display_payload = typed_input.to_params()
        session_id = self.session_id or "unknown"
        task_id = str(task.id)
        call_id = self._new_non_loop_call_id(task_id, step_id)
        event_emitter = ToolEventEmitter(self)
        tool_context = event_emitter.build_context(
            task_id=task_id,
            session_id=session_id,
            step_id=step_id,
            call_id=call_id,
            tool_name=tool_name,
            input_metadata=typed_input,
        )
        tool_context = tool_context.model_copy(
            update={
                "attributes": {
                    **tool_context.attributes,
                    "execution_route": "fast_registry",
                    "runtime_phase": self._diagnostic_phase(),
                }
            }
        )
        tool_call = event_emitter.create_tool_call(
            session_id=session_id,
            task_id=task_id,
            step_id=step_id,
            call_id=call_id,
            tool_name=tool_name,
            input_metadata=typed_input,
            tool_context=tool_context,
            status="pending",
            reason="capability_match",
        )
        tool_events = [
            event_emitter.emit(
                task_id=task_id,
                tool_call=tool_call,
                event_type="pending",
                status="pending",
                input_metadata=typed_input,
                tool_context=tool_context,
            )
        ]

        if self.enhanced_ui:
            if parent_task_id:
                self._set_dashboard_task_status(parent_task_id, "running")
            self._set_dashboard_tool_status(
                parent_task_id=parent_task_id,
                tool_id=step_id,
                tool_name=tool_name,
                status="running",
            )
            display_params = dict(display_payload)
            if "content" in display_params:
                display_params["content"] = f"<{len(str(display_params['content']))} chars>"
            param_lines = "\n".join(f"{key}: {value}" for key, value in display_params.items())
            self.enhanced_ui.set_current_task_state(
                title=f"Tool: {tool_name}",
                details=f"Task: {task.description}\nStep: {step_id}\n{param_lines}",
                status="running",
            )

        selection = ToolSelection(
            step_id=step_id,
            tool_name=tool_name,
            reason="capability_match",
            confidence=0.95,
            input_metadata=typed_input,
            requires_confirmation=False,
            fallback_tools=[],
            depends_on=[],
            timeout_override=timeout_override,
        )

        self.logger.log_event(
            "tool_execution_start",
            {
                "task_id": task.id,
                "step_id": step_id,
                "tool": tool_name,
                "timeout_override": timeout_override,
                "input_metadata_summary": self._sanitize_tool_metadata(typed_input),
            },
            session_id=self.session_id or "unknown",
            turn_id=1,
        )
        self._record_durable_tool_started(tool_call)

        tool_events.append(
            event_emitter.emit(
                task_id=task_id,
                tool_call=tool_call,
                event_type="running",
                status="running",
                input_metadata=typed_input,
                tool_context=tool_context,
            )
        )
        scope_failure = self._fast_mutation_scope_failure(task, selection)
        if scope_failure is not None:
            return self._fast_tool_failure_result(
                tool_call=tool_call,
                tool_context=tool_context,
                input_metadata=typed_input,
                tool_events=tool_events,
                event_emitter=event_emitter,
                failure=scope_failure,
                timeout_override=timeout_override,
            )

        guard_error = self.tool_planning_task_executor.guard_preselected_tool_call(
            task,
            tool_call,
            selection,
        )
        if guard_error is not None:
            return self._fast_tool_failure_result(
                tool_call=tool_call,
                tool_context=tool_context,
                input_metadata=typed_input,
                tool_events=tool_events,
                event_emitter=event_emitter,
                failure=guard_error.failure or FailureMetadata(
                    error_type=guard_error.error_type,
                    error_message=guard_error.error_message,
                ),
                timeout_override=timeout_override,
            )

        controller = getattr(self, "runtime_controller", None)
        if self._fast_mutation_targets(selection) and task.validation_command:
            set_pending_verification = getattr(controller, "set_pending_verification", None)
            if callable(set_pending_verification):
                set_pending_verification(
                    VerificationPlanMetadata(
                        reason="Fast mutation must retain the task's exact validation command.",
                        commands=[task.validation_command],
                        target_files=list(task.write_files),
                    )
                )
        replay_tool_result = getattr(controller, "replay_tool_result", None)
        exec_result = (
            replay_tool_result(tool_call, selection)
            if callable(replay_tool_result)
            else None
        )
        retry_history: list[dict[str, Any]] = []
        before_hashes: dict[str, str | None] = {}
        if exec_result is None:
            prepare_tool_call = getattr(controller, "prepare_tool_call", None)
            if callable(prepare_tool_call) and not prepare_tool_call(tool_call, selection):
                return self._fast_tool_failure_result(
                    tool_call=tool_call,
                    tool_context=tool_context,
                    input_metadata=typed_input,
                    tool_events=tool_events,
                    event_emitter=event_emitter,
                    failure=FailureMetadata(
                        error_type="CheckpointPrepareFailed",
                        error_message="Mutation was not executed because its prepared checkpoint was not durable.",
                        recovery_strategy="Restore checkpoint storage before retrying the mutation.",
                    ),
                    timeout_override=timeout_override,
                )
            before_hashes = self._fast_mutation_hashes(selection)
            exec_result, retry_history = self._execute_tool_with_fast_retry(selection)
            no_observed_diff = bool(
                exec_result.success
                and self._fast_mutation_targets(selection)
                and not self._fast_mutation_has_observed_diff(selection, before_hashes)
            )
            observed_result = exec_result
            if no_observed_diff:
                failure_error = ExecutionError(
                    error_type="NoObservedFileMutation",
                    error_message="The requested fast mutation produced no target-file diff.",
                    recoverable=False,
                    retry_recommended=False,
                )
                if hasattr(exec_result, "model_copy"):
                    observed_result = exec_result.model_copy(
                        update={
                            "success": False,
                            "status": ExecutionStatus.FAILED,
                            "error": failure_error,
                        }
                    )
                else:
                    observed_result = SimpleNamespace(
                        **{
                            **vars(exec_result),
                            "success": False,
                            "status": ExecutionStatus.FAILED,
                            "error": failure_error,
                        }
                    )
            observe_tool_result = getattr(controller, "observe_tool_result", None)
            if callable(observe_tool_result) and not observe_tool_result(tool_call, selection, observed_result):
                return self._fast_tool_failure_result(
                    tool_call=tool_call,
                    tool_context=tool_context,
                    input_metadata=typed_input,
                    tool_events=tool_events,
                    event_emitter=event_emitter,
                    failure=FailureMetadata(
                        error_type="CheckpointObservationFailed",
                        error_message="Tool returned, but its result could not be durably observed.",
                        recovery_strategy="Reconcile the indeterminate side effect before retrying.",
                    ),
                    timeout_override=timeout_override,
                    attempts_used=getattr(exec_result, "attempt_number", 1),
                    retry_history=retry_history,
                )
            if no_observed_diff:
                return self._fast_tool_failure_result(
                    tool_call=tool_call,
                    tool_context=tool_context,
                    input_metadata=typed_input,
                    tool_events=tool_events,
                    event_emitter=event_emitter,
                    failure=FailureMetadata(
                        error_type="NoObservedFileMutation",
                        error_message="The requested fast mutation produced no target-file diff.",
                        details={"target_files": sorted(before_hashes)},
                    ),
                    timeout_override=timeout_override,
                    attempts_used=getattr(exec_result, "attempt_number", 1),
                    retry_history=retry_history,
                )

        state = getattr(controller, "state", None)
        updater = getattr(controller, "state_updater", None)
        if (
            not bool(getattr(exec_result, "recovery_already_applied", False))
            and state is not None
            and updater is not None
        ):
            updater.apply_tool_result(state, selection, exec_result)
        if self.enhanced_ui:
            status = "completed" if exec_result.success else "failed"
            self._set_dashboard_tool_status(
                parent_task_id=parent_task_id,
                tool_id=step_id,
                tool_name=tool_name,
                status=status,
            )
            detail = "Tool returned successfully"
            if not exec_result.success and exec_result.error:
                detail = exec_result.error.error_message
            self.enhanced_ui.set_current_task_state(
                title=f"Tool: {tool_name}",
                details=detail,
                status=status,
            )
        status_text = getattr(exec_result.status, "value", str(exec_result.status))
        status = self._result_status_from_execution(status_text, exec_result.success)
        failure = self._failure_metadata_from_execution(exec_result.error)
        if exec_result.success:
            tool_events.append(
                event_emitter.emit(
                    task_id=task_id,
                    tool_call=tool_call,
                    event_type="completed",
                    status="completed",
                    input_metadata=typed_input,
                    output_metadata=exec_result.output_metadata,
                    tool_context=tool_context,
                )
            )
        else:
            tool_events.append(
                event_emitter.emit(
                    task_id=task_id,
                    tool_call=tool_call,
                    event_type="error",
                    status="error",
                    input_metadata=typed_input,
                    tool_context=tool_context,
                    failure=failure,
                    recoverable=bool(failure and failure.recoverable),
                )
            )
        result = ToolExecutionEnvelopeMetadata(
            tool_name=tool_name,
            step_id=step_id,
            status=status,
            success=exec_result.success,
            input_metadata=typed_input,
            output_metadata=exec_result.output_metadata,
            failure=failure,
            duration_seconds=exec_result.duration_seconds,
            timeout_override=timeout_override,
            attempts_used=getattr(exec_result, "attempt_number", 1),
            retry_count=getattr(exec_result, "retry_count", 0),
            retry_history=retry_history,
            call_id=call_id,
            tool_context=tool_context,
            tool_events=tool_events,
        )
        self._record_durable_tool_terminal(tool_call, result)
        self.logger.log_event(
            "tool_executed",
            {
                "task_id": task.id,
                "step_id": step_id,
                "tool": tool_name,
                "success": exec_result.success,
                "status": status.value,
                "error_type": failure.error_type if failure else None,
                "error": failure.error_message if failure else None,
                "duration_seconds": result.duration_seconds,
                "attempts_used": result.attempts_used,
                "retry_count": result.retry_count,
                "output": self._summarize_metadata_output(exec_result.output_metadata),
            },
            session_id=self.session_id or "unknown",
            turn_id=1,
        )
        return result

    def _build_durable_tool_identity(
        self,
        *,
        task: Task,
        step_id: str,
        tool_name: str,
        input_metadata: ToolInputMetadata,
        execution_route: str = "module_owned",
    ) -> tuple[ToolCallMetadata, ToolContextMetadata]:
        """Build the existing typed call identity for non-loop tool execution."""
        session_id = self.session_id or "unknown"
        task_id = str(task.id)
        call_id = self._new_non_loop_call_id(task_id, step_id)
        emitter = ToolEventEmitter(self)
        tool_context = emitter.build_context(
            task_id=task_id,
            session_id=session_id,
            step_id=step_id,
            call_id=call_id,
            tool_name=tool_name,
            input_metadata=input_metadata,
        )
        tool_context = tool_context.model_copy(
            update={
                "attributes": {
                    **tool_context.attributes,
                    "execution_route": execution_route,
                    "runtime_phase": self._diagnostic_phase(),
                }
            }
        )
        tool_call = emitter.create_tool_call(
            session_id=session_id,
            task_id=task_id,
            step_id=step_id,
            call_id=call_id,
            tool_name=tool_name,
            input_metadata=input_metadata,
            tool_context=tool_context,
            status="pending",
            reason=execution_route,
        )
        return tool_call, tool_context

    @staticmethod
    def _new_non_loop_call_id(task_id: str, step_id: str) -> str:
        return f"{task_id}:{step_id}:{uuid.uuid4().hex[:12]}"

    def _record_durable_tool_started(self, tool_call: ToolCallMetadata) -> None:
        diagnostics = self.runtime_diagnostics_hooks
        if diagnostics is None:
            return
        try:
            diagnostics.on_tool_started(tool_call=tool_call)
        except Exception as exc:
            self._log_diagnostics_bridge_failure("started", tool_call, exc)

    def _record_durable_tool_terminal(
        self,
        tool_call: ToolCallMetadata,
        result: ToolExecutionEnvelopeMetadata,
    ) -> None:
        diagnostics = self.runtime_diagnostics_hooks
        if diagnostics is None:
            return
        try:
            if result.success:
                diagnostics.on_tool_completed(
                    tool_execution=result,
                    task_id=tool_call.task_id,
                    session_id=tool_call.session_id,
                )
                return
            failure = result.failure or FailureMetadata(
                error_type="ToolExecutionFailed",
                error_message=f"{result.tool_name} failed",
            )
            diagnostics.on_tool_failed(
                ToolErrorMetadata(
                    session_id=tool_call.session_id,
                    task_id=tool_call.task_id,
                    step_id=tool_call.step_id,
                    call_id=tool_call.call_id,
                    tool_name=tool_call.tool_name,
                    error_type=failure.error_type,
                    error_message=failure.error_message,
                    recoverable=failure.recoverable,
                    suggested_recovery=failure.recovery_strategy or "",
                    failure=failure,
                    input_metadata=result.input_metadata,
                    tool_context=result.tool_context,
                    round_index=tool_call.round_index,
                )
            )
        except Exception as exc:
            self._log_diagnostics_bridge_failure("terminal", tool_call, exc)

    def _log_diagnostics_bridge_failure(
        self,
        stage: str,
        tool_call: ToolCallMetadata,
        exc: Exception,
    ) -> None:
        try:
            self.logger.log_event(
                "runtime_diagnostics_hook_failed",
                {
                    "stage": stage,
                    "tool": tool_call.tool_name,
                    "call_id": tool_call.call_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                session_id=tool_call.session_id,
                turn_id=1,
            )
        except Exception:
            return

    @staticmethod
    def _fast_mutation_targets(selection: ToolSelection) -> list[str]:
        return file_mutation_targets(selection)

    @staticmethod
    def _normalized_scope_path(raw_path: str) -> str:
        return str(Path(raw_path).expanduser().resolve(strict=False))

    def _fast_mutation_scope_failure(
        self,
        task: Task,
        selection: ToolSelection,
    ) -> FailureMetadata | None:
        targets = self._fast_mutation_targets(selection)
        if not targets:
            return None
        controller = getattr(self, "runtime_controller", None)
        state = getattr(controller, "state", None)
        execution_mode = str(getattr(getattr(state, "execution_mode", ""), "value", getattr(state, "execution_mode", "")))
        if execution_mode == "read_only":
            return FailureMetadata(
                error_type="ReadOnlyExecutionMode",
                error_message="Fast mutation is forbidden by the root read-only execution mode.",
                details={"target_files": targets},
            )
        if str(task.kind).lower() in {"inspect", "inspection", "analysis", "investigate", "codebase_understanding"}:
            return FailureMetadata(
                error_type="ReadOnlyTaskKind",
                error_message=f"Task kind {task.kind} cannot authorize a file mutation.",
                details={"target_files": targets},
            )
        allowed = {
            self._normalized_scope_path(str(path))
            for path in task.write_files
            if str(path).strip()
        }
        if not allowed:
            return FailureMetadata(
                error_type="MissingTaskWriteScope",
                error_message="Fast mutation requires explicit Task.write_files authority.",
                details={"target_files": targets},
            )
        requested = {self._normalized_scope_path(path) for path in targets}
        outside = sorted(requested - allowed)
        if outside:
            return FailureMetadata(
                error_type="TaskWriteScopeViolation",
                error_message="Fast mutation target is outside Task.write_files authority.",
                details={"target_files": sorted(requested), "allowed_write_files": sorted(allowed)},
            )
        constraint_check = getattr(controller, "session_constraint_violation", None)
        if callable(constraint_check):
            violation = constraint_check(
                selection,
                task_validation_command=str(task.validation_command or ""),
            )
            if violation is not None:
                return FailureMetadata(
                    error_type="SessionConstraintViolation",
                    error_message=f"Active session constraint denied this mutation: {violation.value}",
                    details={
                        "violation_code": violation.value,
                        "target_files": sorted(requested),
                    },
                )
        return None

    def _fast_mutation_hashes(self, selection: ToolSelection) -> dict[str, str | None]:
        hashes: dict[str, str | None] = {}
        for raw_path in self._fast_mutation_targets(selection):
            normalized = self._normalized_scope_path(raw_path)
            path = Path(normalized)
            try:
                hashes[normalized] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            except OSError:
                hashes[normalized] = None
        return hashes

    def _fast_mutation_has_observed_diff(
        self,
        selection: ToolSelection,
        before_hashes: dict[str, str | None],
    ) -> bool:
        if not before_hashes:
            return True
        return self._fast_mutation_hashes(selection) != before_hashes

    def _fast_tool_failure_result(
        self,
        *,
        tool_call: ToolCallMetadata,
        tool_context: ToolContextMetadata,
        input_metadata: ToolInputMetadata,
        tool_events: list[Any],
        event_emitter: ToolEventEmitter,
        failure: FailureMetadata,
        timeout_override: int | None,
        attempts_used: int = 0,
        retry_history: list[dict[str, Any]] | None = None,
    ) -> ToolExecutionEnvelopeMetadata:
        tool_events.append(
            event_emitter.emit(
                task_id=tool_call.task_id,
                tool_call=tool_call,
                event_type="error",
                status="error",
                input_metadata=input_metadata,
                tool_context=tool_context,
                failure=failure,
                recoverable=failure.recoverable,
            )
        )
        result = ToolExecutionEnvelopeMetadata(
            tool_name=tool_call.tool_name,
            step_id=tool_call.step_id,
            status=ResultStatus.FAIL,
            success=False,
            input_metadata=input_metadata,
            failure=failure,
            timeout_override=timeout_override,
            attempts_used=attempts_used,
            retry_count=max(0, attempts_used - 1),
            retry_history=retry_history or [],
            call_id=tool_call.call_id,
            tool_context=tool_context,
            tool_events=tool_events,
        )
        self._record_durable_tool_terminal(tool_call, result)
        return result

    def _apply_project_command_context(self, tool_name: str, input_metadata: ToolInputMetadata) -> ToolInputMetadata:
        """Inject the project-local cwd and venv environment into command-like tools."""
        if tool_name not in {"command_executor", "bug_fix_tool", "warning_check_tool"}:
            return input_metadata
        input_metadata = self._attach_command_approval_callback(tool_name, input_metadata)
        environment = self._environment_for_tool_input(input_metadata)
        if not environment:
            return input_metadata
        updates: dict[str, Any] = {}
        command_cwd = str(environment.get("command_cwd") or environment.get("project_path") or "").strip()
        if command_cwd and input_metadata.cwd != command_cwd:
            updates["cwd"] = command_cwd
        command_env = environment.get("command_env") if isinstance(environment.get("command_env"), dict) else {}
        if command_env:
            merged_env = {str(key): str(value) for key, value in (input_metadata.env or {}).items()}
            merged_env.update({str(key): str(value) for key, value in command_env.items()})
            updates["env"] = merged_env
        if input_metadata.command:
            rewritten_command = self._rewrite_project_command(input_metadata.command, environment)
            if rewritten_command != input_metadata.command:
                updates["command"] = rewritten_command
                updates["requested_command"] = input_metadata.requested_command or input_metadata.command
                updates["effective_interpreter"] = str(environment.get("python_command") or "") or None
        updates["environment_id"] = str(environment.get("environment_id") or "") or None
        if not updates:
            return input_metadata
        return input_metadata.model_copy(update=updates)

    def _attach_command_approval_callback(self, tool_name: str, input_metadata: ToolInputMetadata) -> ToolInputMetadata:
        if tool_name not in {"command_executor", "bug_fix_tool"}:
            return input_metadata
        handles = dict(input_metadata.runtime_handles or {})
        handles.setdefault("_command_approval_callback", self._confirm_command_execution)
        if tool_name == "bug_fix_tool":
            handles.setdefault("_bug_fix_progress_callback", self._report_bug_fix_progress)
        return input_metadata.model_copy(update={"runtime_handles": handles})

    def _report_bug_fix_progress(self, event: dict[str, Any]) -> None:
        """Expose the bug-fix tool's internal loop through logs and the live UI."""
        payload = {str(key): value for key, value in dict(event or {}).items()}
        self.logger.log_event(
            "bug_fix_iteration_progress",
            payload,
            session_id=self.session_id or "unknown",
            turn_id=1,
        )
        if self.enhanced_ui:
            iteration = int(payload.get("iteration") or 0)
            budget = int(payload.get("budget") or 0)
            details = [f"Event: {payload.get('event') or 'progress'}"]
            if payload.get("modified_files"):
                details.append(f"Modified files: {', '.join(payload['modified_files'])}")
            if payload.get("error_summary"):
                details.append(f"Latest error: {payload['error_summary']}")
            self.enhanced_ui.set_current_task_state(
                title=f"Bug Fix Iteration {iteration}/{budget}",
                details="\n".join(details),
                status="success" if payload.get("success") else "running",
            )

    def _confirm_command_execution(self, decision) -> bool:
        reasons = "; ".join(getattr(decision, "reasons", []) or ["Command requires confirmation."])
        command = getattr(decision, "command", "")
        cwd = getattr(decision, "cwd", "")
        description = f"Command:\n{command}\n\nWorking directory: {cwd or '(current)'}\n\nReason: {reasons}"
        if self.enhanced_ui:
            self.enhanced_ui.set_current_task_state(
                title="Command Approval Required",
                details=description,
                status="running",
            )
        from ui.question_ui import QuestionUI

        return QuestionUI(self.console).ask_confirm(
            "command_approval",
            "Run this command?",
            title="Command Approval Required",
            description=description,
            default=False,
        )

    def _environment_for_tool_input(self, input_metadata: ToolInputMetadata) -> dict[str, Any]:
        candidates = []
        for raw in (
            input_metadata.project_path,
            input_metadata.cwd,
            input_metadata.file_path,
            *(input_metadata.file_paths or []),
            *(input_metadata.written_files or []),
        ):
            if raw:
                candidates.append(Path(str(raw)).expanduser())
        environments = getattr(self, "_project_environments", {}) or {}
        for candidate in candidates:
            match = self._environment_for_path(candidate, environments)
            if match:
                return match
        ready_environments = [environment for environment in environments.values() if self._ready_environment(environment)]
        if len(ready_environments) == 1:
            return ready_environments[0]
        return {}

    def _environment_for_path(self, path: Path, environments: dict[str, dict[str, Any]]) -> dict[str, Any]:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        for project_path, environment in environments.items():
            if not self._ready_environment(environment):
                continue
            try:
                project = Path(project_path).expanduser().resolve()
            except OSError:
                project = Path(project_path).expanduser()
            if resolved == project or project in resolved.parents:
                return environment
        return {}

    @staticmethod
    def _ready_environment(environment: dict[str, Any]) -> bool:
        return (
            str(environment.get("readiness") or "") == "ready"
            and bool(str(environment.get("environment_id") or "").strip())
            and bool(str(environment.get("python_command") or environment.get("python_executable") or "").strip())
            and bool(str(environment.get("command_cwd") or environment.get("project_path") or "").strip())
        )

    def _rewrite_project_command(self, command: str, environment: dict[str, Any]) -> str:
        try:
            parts = shlex.split(command)
        except ValueError:
            return command
        if not parts:
            return command
        executable = Path(parts[0]).name.lower()
        if executable in {"python", "python3"} and environment.get("python_command"):
            parts[0] = str(environment["python_command"])
        elif executable in {"pip", "pip3"} and environment.get("pip_command"):
            parts[0] = str(environment["pip_command"])
        else:
            return command
        return shlex.join(parts)

    def _result_status_from_execution(self, status_text: str, success: bool) -> ResultStatus:
        if success:
            return ResultStatus.SUCCESS
        if "timeout" in status_text.lower():
            return ResultStatus.TIMEOUT
        if "cancel" in status_text.lower():
            return ResultStatus.CANCELLED
        return ResultStatus.FAIL

    def _failure_metadata_from_execution(self, error: Any) -> FailureMetadata | None:
        if error is None:
            return None
        return FailureMetadata(
            error_type=getattr(error, "error_type", type(error).__name__),
            error_message=getattr(error, "error_message", str(error)),
            error_code=getattr(error, "error_code", None),
            recoverable=bool(getattr(error, "recoverable", False)),
            retry_recommended=bool(getattr(error, "retry_recommended", False)),
        )

    def _execute_tool_with_fast_retry(self, selection: ToolSelection):
        tool_def = self.tool_registry.get(selection.tool_name)
        max_retries = max(0, int(getattr(tool_def, "max_retries", 0) or 0))
        attempts_allowed = max_retries + 1
        retry_history: list[dict[str, Any]] = []
        last_result = None
        delay = 0.25

        for attempt in range(1, attempts_allowed + 1):
            exec_result = self.tool_executor.execute_single(selection, context=None)
            exec_result.attempt_number = attempt
            exec_result.retry_count = attempt - 1
            last_result = exec_result
            retry_history.append(self._tool_retry_history_item(selection, exec_result, attempt))

            if exec_result.success:
                return exec_result, retry_history
            if self._execution_result_is_timeout(exec_result):
                return exec_result, retry_history
            if not self._should_retry_execution_result(exec_result):
                return exec_result, retry_history
            if attempt >= attempts_allowed:
                return exec_result, retry_history

            self.logger.log_event(
                "tool_execution_retry",
                {
                    "step_id": selection.step_id,
                    "tool": selection.tool_name,
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "max_attempts": attempts_allowed,
                    "error_type": exec_result.error.error_type if exec_result.error else None,
                    "error": exec_result.error.error_message if exec_result.error else None,
                },
                session_id=self.session_id or "unknown",
                turn_id=1,
            )
            if self.enhanced_ui:
                self.enhanced_ui.set_current_task_state(
                    title=f"Tool retry: {selection.tool_name}",
                    details=(
                        f"Attempt {attempt}/{attempts_allowed} failed\n"
                        f"Retrying step: {selection.step_id}"
                    ),
                    status="running",
                )
            time.sleep(delay)
            delay = min(delay * 2, 1.0)

        return last_result, retry_history

    def _tool_retry_history_item(self, selection: ToolSelection, exec_result, attempt: int) -> dict[str, Any]:
        return {
            "attempt": attempt,
            "step_id": selection.step_id,
            "tool": selection.tool_name,
            "status": getattr(exec_result.status, "value", str(exec_result.status)),
            "success": exec_result.success,
            "duration_seconds": exec_result.duration_seconds,
            "error_type": exec_result.error.error_type if exec_result.error else None,
            "error": exec_result.error.error_message if exec_result.error else None,
        }

    def _execution_result_is_timeout(self, exec_result) -> bool:
        status = getattr(exec_result.status, "value", str(exec_result.status))
        error_type = exec_result.error.error_type if exec_result.error else ""
        error_message = exec_result.error.error_message if exec_result.error else ""
        return "timeout" in f"{status} {error_type} {error_message}".lower()

    def _should_retry_execution_result(self, exec_result) -> bool:
        if exec_result.success or not exec_result.error:
            return False
        if self._execution_result_is_timeout(exec_result):
            return False
        error_type = exec_result.error.error_type or ""
        if error_type in {"LLMProviderError", "LLMTimeoutError"}:
            return False
        if exec_result.error.retry_recommended:
            return True
        category = classify_error(Exception(exec_result.error.error_message))
        return category.value in {"retryable", "network"}

    def _llm_tool_timeout_override(self, tool_name: str) -> int | None:
        tool_def = self.tool_registry.get(tool_name) if getattr(self, "tool_registry", None) else None
        capabilities = getattr(tool_def, "capabilities", []) if tool_def else []
        has_llm_call = any(getattr(capability, "value", capability) == "llm_call" for capability in capabilities)
        if not has_llm_call:
            return None

        settings = getattr(self.llm_client, "settings", None)
        provider_timeout = float(getattr(settings, "timeout_seconds", 60.0) or 60.0)
        transport_attempts = max(1, int(getattr(settings, "transport_retries", 0) or 0) + 1)
        initial_delay = max(0.0, float(getattr(settings, "retry_initial_delay", 0.0) or 0.0))
        max_delay = max(initial_delay, float(getattr(settings, "retry_max_delay", initial_delay) or initial_delay))
        delay_budget = 0.0
        delay = initial_delay
        for _ in range(max(0, transport_attempts - 1)):
            delay_budget += min(delay, max_delay)
            delay = min(delay * 2 if delay else 0.0, max_delay)

        json_attempt_budget = 2
        computed = int(provider_timeout * transport_attempts * json_attempt_budget + delay_budget * json_attempt_budget + 30)
        default_timeout = int(getattr(tool_def, "timeout_seconds", 30) or 30)
        return max(default_timeout, min(computed, 900))

    def _finalize_project_readme(
        self,
        goal: str,
        results: list[TaskExecutionResult],
    ) -> ToolExecutionEnvelopeMetadata | None:
        """Generate README.md once after successful project/file creation."""
        if not self._should_auto_finalize_readme(goal):
            return None
        if self._results_include_tool(results, "readme_tool"):
            return None

        written_files = self._collect_written_files(results)
        if not written_files:
            return None

        project_path = self._infer_project_path_from_files(goal, written_files)
        if project_path is None:
            return None

        task = Task(
            id=str(uuid.uuid4()),
            description=f"Generate README.md for {project_path}",
            priority=TaskPriority.MEDIUM,
            kind="documentation",
            read_files=list(written_files),
            write_files=[str(project_path / "README.md")],
        )
        readme_result = self._execute_fast_tool(
            task=task,
            step_id="final_readme_tool",
            tool_name="readme_tool",
            input_metadata=ToolInputMetadata.from_mapping("readme_tool", {
                "project_path": str(project_path),
                "project_summary": goal,
                "written_files": written_files,
                "entry_files": written_files,
                "overwrite": True,
            }),
        )

        if self.enhanced_ui:
            if readme_result.success:
                output = readme_result.output
                self.enhanced_ui.log_activity("success", f"README generated: {output.get('file_path', 'README.md') if output else 'README.md'}")
            else:
                self.enhanced_ui.log_activity("error", f"README generation failed: {readme_result.error_message}")
        elif readme_result.success:
            output = readme_result.output
            self.console.print(f"[green]README generated:[/green] {output.get('file_path', project_path / 'README.md') if output else project_path / 'README.md'}")
        else:
            self.console.print(f"[yellow]README generation failed:[/yellow] {readme_result.error_message}")

        self.logger.log_event(
            "readme_finalized",
            {
                "goal": goal,
                "project_path": str(project_path),
                "written_files": written_files,
                "success": readme_result.success,
                "error": readme_result.error_message,
                "output": self._summarize_metadata_output(readme_result.output),
            },
            session_id=self.session_id or "unknown",
            turn_id=1,
        )
        return readme_result

    @staticmethod
    def _should_auto_finalize_readme(goal: str) -> bool:
        lowered = str(goal or "").lower()
        creation_intent = any(
            term in lowered
            for term in (
                "build ",
                "create ",
                "develop ",
                "generate ",
                "scaffold ",
                "创建",
                "开发",
                "搭建",
                "新建",
                "生成",
            )
        )
        project_surface = any(
            term in lowered
            for term in (
                " app",
                " application",
                " project",
                " script",
                " tool",
                " website",
                "应用",
                "工具",
                "项目",
                "网站",
                "脚本",
            )
        )
        return creation_intent and project_surface

    def _run_iterative_improvement(
        self,
        *,
        goal: str,
        project_path: str | Path,
        written_files: list[str],
        run_command: str = "",
        readme_path: str | Path | None = None,
        session_constraints: SessionConstraintState | None = None,
        session_ingress_state: SessionIngressState | None = None,
    ) -> dict[str, Any] | None:
        """Run fixed-count validation and improvement loop."""
        return self.project_improvement_runtime.run(
            goal=goal,
            project_path=project_path,
            written_files=written_files,
            run_command=run_command,
            readme_path=readme_path,
            session_constraints=(
                session_constraints
                if session_constraints is not None
                else getattr(getattr(self, "runtime_controller", None), "state", None).session_constraints
                if getattr(getattr(self, "runtime_controller", None), "state", None) is not None
                else None
            ),
            session_ingress_state=(
                session_ingress_state
                if session_ingress_state is not None
                else (
                    self._current_execution_context.get("session_ingress_state")
                    if isinstance(getattr(self, "_current_execution_context", None), dict)
                    else None
                )
            ),
        )

    def _sync_project_environment(
        self,
        *,
        task: Task,
        step_id: str,
        project_path: Path,
        written_files: list[str],
        entry_files: list[str],
        run_command: str,
        goal: str = "",
        stack_preset_update: dict[str, Any] | None = None,
        parent_task_id: str | None = None,
        environment_operation: EnvironmentOperation = EnvironmentOperation.SETUP,
        approval_granted: bool = False,
    ) -> ToolExecutionEnvelopeMetadata:
        if not self.auto_approve and not approval_granted:
            from memory.agents.project_environment_tool import inspect_project_environment

            preflight = inspect_project_environment(
                project_path=project_path,
                written_files=written_files,
                entry_files=entry_files,
                run_command=run_command,
            )
            if not self._confirm_environment_setup(preflight):
                reason = "Environment setup approval was not granted."
                return ToolExecutionEnvelopeMetadata(
                    tool_name="project_environment_tool",
                    step_id=step_id,
                    status=ResultStatus.FAIL,
                    success=False,
                    input_metadata=ToolInputMetadata.from_mapping(
                        "project_environment_tool",
                        {
                            "project_path": str(project_path),
                            "environment_operation": environment_operation,
                            "written_files": written_files,
                            "entry_files": entry_files,
                            "run_command": run_command,
                        },
                    ),
                    failure=FailureMetadata(
                        error_type="EnvironmentSetupApprovalRequired",
                        error_message=reason,
                        recoverable=True,
                        retry_recommended=True,
                    ),
                )
        if self.enhanced_ui and parent_task_id:
            self._set_dashboard_task_status(parent_task_id, "running")
            self.enhanced_ui.set_current_task_state(
                title="Environment Setup",
                details=f"Project: {project_path}\nVirtual environment: .venv",
                status="running",
            )
        input_metadata_payload = {
            "project_path": str(project_path),
            "written_files": written_files,
            "entry_files": entry_files,
            "run_command": run_command,
            "goal": goal,
            "stack_preset_update": stack_preset_update or {},
            "env_name": ".venv",
            "environment_operation": environment_operation,
            "install": True,
        }
        result = self._execute_project_environment_agent_tool(
            task=task,
            step_id=step_id,
            input_metadata=ToolInputMetadata.from_mapping("project_environment_tool", input_metadata_payload),
            parent_task_id=parent_task_id,
        )
        if result.success and result.output is not None:
            payload = result.output
            if not hasattr(self, "_project_environments"):
                self._project_environments = {}
            self._project_environments[str(project_path.resolve())] = payload.to_json_dict()
            if self.enhanced_ui and parent_task_id:
                packages = payload.get("detected_packages") or []
                stack_preset = payload.get("stack_preset") or {}
                self._set_dashboard_task_status(parent_task_id, "completed")
                self._append_dashboard_stage_child(
                    "environment",
                    child_id=f"sync_{step_id}",
                    description=(
                        f".venv ready; packages: {', '.join(packages) if packages else 'none'}; "
                        f"python: {Path(str(payload.get('python_executable') or '')).name}"
                    ),
                    kind="result",
                )
                self._append_dashboard_stage_child(
                    "environment",
                    child_id=f"memory_{step_id}",
                    description="Saved project environment dependency context to short-term memory",
                    kind="note",
                )
                if stack_preset:
                    self._append_dashboard_stage_child(
                        "environment",
                        child_id=f"stack_{step_id}",
                        description=(
                            f"Stack preset r{stack_preset.get('revision', 1)}: "
                            f"{stack_preset.get('architecture', 'project_native')}; "
                            f"UI strategy: {stack_preset.get('ui_strategy', 'evaluate_user_facing_ui')}"
                        ),
                        kind="note",
                    )
                git_repository = payload.get("git_repository")
                if git_repository:
                    head = git_repository.get("head") if hasattr(git_repository, "get") else ""
                    self._append_dashboard_stage_child(
                        "environment",
                        child_id=f"git_{step_id}",
                        description=f"Git initialized{f' at {head}' if head else ''}",
                        kind="note",
                    )
                git_snapshot = payload.get("git_snapshot")
                if git_snapshot:
                    commit_hash = git_snapshot.get("commit_hash") if hasattr(git_snapshot, "get") else ""
                    created = git_snapshot.get("created") if hasattr(git_snapshot, "get") else False
                    self._append_dashboard_stage_child(
                        "environment",
                        child_id=f"git_snapshot_{step_id}",
                        description=(
                            f"Safety snapshot {'created' if created else 'ready'}"
                            f"{f': {commit_hash}' if commit_hash else ''}"
                        ),
                        kind="note",
                    )
                self.enhanced_ui.set_current_task_state(
                    title="Environment Setup",
                    details=(
                        f"Virtual environment: {payload.get('venv_path')}\n"
                        f"Run command: {payload.get('run_command')}\n"
                        f"Packages: {', '.join(packages) if packages else 'none'}\n"
                        f"Stack preset: {stack_preset.get('architecture', 'project_native')}\n"
                        f"UI strategy: {stack_preset.get('ui_strategy', 'evaluate_user_facing_ui')}"
                    ),
                    status="completed",
                )
        elif self.enhanced_ui and parent_task_id:
            self._set_dashboard_task_status(parent_task_id, "failed")
            self._append_dashboard_stage_child(
                "environment",
                child_id=f"sync_failed_{step_id}",
                description=summarize_environment_failure(result.error_message or ""),
                kind="result",
                status="failed",
            )
        return result

    def _ensure_environment_for_task(
        self,
        task: Task,
        context: TaskExecutionContext,
    ) -> str | None:
        """Attach or prepare the project environment before Python validation."""

        if not self._task_requires_python_environment(task):
            return None
        parent_context = context.parent_context or {}
        raw_project = str(parent_context.get("project_path") or "").strip()
        if not raw_project:
            return "Environment not ready: Python validation requires an explicit project_path."
        project_path = Path(raw_project).expanduser()
        from memory.agents.project_environment_tool import inspect_project_environment

        preflight = inspect_project_environment(
            project_path=project_path,
            written_files=[*task.read_files, *task.write_files],
            entry_files=task.write_files,
            run_command=task.validation_command,
        )
        if preflight.readiness == EnvironmentReadiness.READY:
            self._project_environments[str(project_path.resolve())] = preflight.to_json_dict()
            return None
        if preflight.readiness == EnvironmentReadiness.BLOCKED:
            return "Environment not ready: existing project environment is incomplete or corrupt."
        if preflight.readiness not in {
            EnvironmentReadiness.SETUP_REQUIRED,
            EnvironmentReadiness.STALE,
        }:
            return f"Environment not ready: unsupported readiness {preflight.readiness.value}."
        if not self.auto_approve and not self._confirm_environment_setup(preflight):
            return "Environment setup approval was not granted; validation was not executed."
        operation = (
            EnvironmentOperation.SYNC
            if preflight.readiness == EnvironmentReadiness.STALE
            else EnvironmentOperation.SETUP
        )
        result = self._sync_project_environment(
            task=task,
            step_id=f"core_{task.id}_project_environment_{operation.value}",
            project_path=project_path,
            written_files=[*task.read_files, *task.write_files],
            entry_files=task.write_files,
            run_command=task.validation_command,
            goal=str(parent_context.get("goal") or ""),
            environment_operation=operation,
            approval_granted=True,
        )
        if not result.success or result.output is None:
            return f"Environment not ready: {result.error_message or 'project environment setup failed.'}"
        payload = result.output.to_json_dict()
        if not self._ready_environment(payload):
            return "Environment not ready: setup did not produce a validated ready environment."
        self._project_environments[str(project_path.resolve())] = payload
        return None

    def _prepare_session_environment(self, tasks: list[Task], goal: str) -> None:
        """Run the shared pre-execution environment gate for the first Python validation."""

        validation_task = next(
            (task for task in tasks if self._task_requires_python_environment(task)),
            None,
        )
        if validation_task is None:
            return
        context = TaskExecutionContext(
            task=validation_task,
            parent_context=self._task_parent_context(goal),
        )
        error = self._ensure_environment_for_task(validation_task, context)
        if error:
            raise RuntimeError(error)

    @staticmethod
    def _task_requires_python_environment(task: Task) -> bool:
        command = str(task.validation_command or "").strip()
        if not command:
            return False
        try:
            executable = Path(shlex.split(command)[0]).name.lower()
        except (ValueError, IndexError):
            return False
        return executable in {"python", "python3", "pytest", "py.test", "tox", "nox", "pip", "pip3"}

    def _confirm_environment_setup(self, preflight: Any) -> bool:
        if self.auto_approve:
            return True
        from ui.question_ui import QuestionUI

        packages = ", ".join(preflight.missing_packages or preflight.detected_packages) or "none"
        return QuestionUI(self.console).ask_confirm(
            "environment_setup_approval",
            "Prepare the project-local execution environment?",
            title="Environment Setup Required",
            description=(
                f"Project: {preflight.project_path}\n"
                f"Environment: {preflight.venv_path}\n"
                f"Missing packages: {packages}"
            ),
            default=False,
        )

    def _execute_project_environment_agent_tool(
        self,
        *,
        task: Task,
        step_id: str,
        input_metadata: ToolInputMetadata,
        parent_task_id: str | None = None,
    ) -> ToolExecutionEnvelopeMetadata:
        """Run the environment manager's agent-local tool without public registry selection."""
        from memory.agents.project_environment_tool import project_environment_tool_executor

        tool_name = "project_environment_tool"
        typed_input = input_metadata
        input_payload = typed_input.to_params()
        tool_call, tool_context = self._build_durable_tool_identity(
            task=task,
            step_id=step_id,
            tool_name=tool_name,
            input_metadata=typed_input,
        )
        started = time.monotonic()
        if self.enhanced_ui:
            self._set_dashboard_tool_status(
                parent_task_id=parent_task_id,
                tool_id=step_id,
                tool_name=tool_name,
                status="running",
            )
            param_lines = "\n".join(f"{key}: {value}" for key, value in input_payload.items())
            self.enhanced_ui.set_current_task_state(
                title=f"Tool: {tool_name}",
                details=f"Task: {task.description}\nStep: {step_id}\n{param_lines}",
                status="running",
            )

        self.logger.log_event(
            "tool_execution_start",
            {
                "task_id": task.id,
                "step_id": step_id,
                "tool": tool_name,
                "timeout_override": None,
                "input_metadata_summary": self._sanitize_tool_metadata(typed_input),
            },
            session_id=self.session_id or "unknown",
            turn_id=1,
        )
        self._record_durable_tool_started(tool_call)

        success = False
        output_metadata: ToolResultMetadata | None = None
        failure: FailureMetadata | None = None
        try:
            typed_input.runtime_handles["_memory_store"] = self.memory_store
            output_metadata = project_environment_tool_executor(typed_input)
            success = output_metadata.status == ResultStatus.SUCCESS
            failure = output_metadata.failure if not success else None
        except Exception as exc:
            failure = FailureMetadata(
                error_type=exc.__class__.__name__,
                error_message=str(exc) or "Project environment sync failed.",
            )

        duration_seconds = time.monotonic() - started
        status = ResultStatus.SUCCESS if success else ResultStatus.FAIL
        if not success and failure is None:
            failure = FailureMetadata(
                error_type="ToolError",
                error_message="Project environment sync failed.",
            )
        result = ToolExecutionEnvelopeMetadata(
            tool_name=tool_name,
            step_id=step_id,
            status=status,
            success=success,
            input_metadata=typed_input,
            output_metadata=output_metadata,
            failure=failure,
            duration_seconds=duration_seconds,
            call_id=tool_call.call_id,
            tool_context=tool_context,
        )
        self._record_durable_tool_terminal(tool_call, result)

        if self.enhanced_ui:
            self._set_dashboard_tool_status(
                parent_task_id=parent_task_id,
                tool_id=step_id,
                tool_name=tool_name,
                status=status.value,
            )
            self.enhanced_ui.set_current_task_state(
                title=f"Tool: {tool_name}",
                details=(
                    "Tool returned successfully"
                    if success
                    else summarize_environment_failure(failure.error_message if failure else "")
                ),
                status=status.value,
            )

        self.logger.log_event(
            "tool_executed",
            {
                "task_id": task.id,
                "step_id": step_id,
                "tool": tool_name,
                "success": success,
                "status": status.value,
                "error_type": failure.error_type if failure else None,
                "error": failure.error_message if failure else None,
                "duration_seconds": duration_seconds,
                "attempts_used": 1,
                "retry_count": 0,
                "output": self._summarize_metadata_output(output_metadata),
            },
            session_id=self.session_id or "unknown",
            turn_id=1,
        )
        return result

    def _execute_project_state_reader_agent_tool(
        self,
        *,
        task: Task,
        step_id: str,
        input_metadata: ToolInputMetadata,
        parent_task_id: str | None = None,
    ) -> ToolExecutionEnvelopeMetadata:
        """Run the autonomous-iteration project state reader without public registry selection."""
        from autonomous_iteration.tool.project_improvement_tool import project_state_reader_executor

        return self._execute_module_owned_tool(
            task=task,
            step_id=step_id,
            tool_name="project_state_reader",
            input_metadata=input_metadata,
            executor=lambda metadata: (
                metadata.runtime_handles.__setitem__("_memory_store", self.memory_store)
                or project_state_reader_executor(metadata)
            ),
            parent_task_id=parent_task_id,
        )

    def _execute_project_improvement_agent_tool(
        self,
        *,
        task: Task,
        step_id: str,
        input_metadata: ToolInputMetadata,
        parent_task_id: str | None = None,
    ) -> ToolExecutionEnvelopeMetadata:
        """Run the autonomous-iteration improvement analyzer without public registry selection."""
        from autonomous_iteration.tool.project_improvement_tool import project_improvement_tool_executor

        return self._execute_module_owned_tool(
            task=task,
            step_id=step_id,
            tool_name="project_improvement_tool",
            input_metadata=input_metadata,
            executor=lambda metadata: (
                metadata.runtime_handles.__setitem__("_llm_client", self.llm_client)
                or metadata.runtime_handles.__setitem__("_runtime_budget", self._enhancement_runtime_budget())
                or metadata.runtime_handles.__setitem__(
                    "_enhancement_required",
                    bool(self.project_improvement_policy.controls_top_level_success),
                )
                or project_improvement_tool_executor(metadata)
            ),
            parent_task_id=parent_task_id,
        )

    def _enhancement_runtime_budget(self) -> RuntimeBudgetMetadata:
        controller = getattr(self, "runtime_controller", None)
        state = getattr(controller, "state", None)
        budget = getattr(state, "budget", None)
        if isinstance(budget, RuntimeBudgetMetadata):
            return budget
        return self._local_enhancement_runtime_budget

    def _execute_environment_fix_agent_tool(
        self,
        *,
        task: Task,
        step_id: str,
        input_metadata: ToolInputMetadata,
        parent_task_id: str | None = None,
    ) -> ToolExecutionEnvelopeMetadata:
        """Run the project environment repair tool without public registry selection."""
        from tools.environment_fix_tool import environment_fix_tool_executor

        handles = dict(input_metadata.runtime_handles or {})
        handles.setdefault("_command_approval_callback", self._confirm_command_execution)
        handles.setdefault("_memory_store", self.memory_store)
        input_metadata = input_metadata.model_copy(update={"runtime_handles": handles})
        result = self._execute_module_owned_tool(
            task=task,
            step_id=step_id,
            tool_name="environment_fix_tool",
            input_metadata=input_metadata,
            executor=environment_fix_tool_executor,
            parent_task_id=parent_task_id,
        )
        if result.output_metadata and result.output_metadata.status != ResultStatus.SUCCESS:
            return result.model_copy(
                update={
                    "status": result.output_metadata.status,
                    "success": False,
                    "failure": result.output_metadata.failure,
                }
            )
        return result

    def _execute_module_owned_tool(
        self,
        *,
        task: Task,
        step_id: str,
        tool_name: str,
        input_metadata: ToolInputMetadata,
        executor: Callable[[ToolInputMetadata], ToolResultMetadata],
        parent_task_id: str | None = None,
    ) -> ToolExecutionEnvelopeMetadata:
        """Run a module-owned tool and return the same envelope as registry-backed tools."""
        typed_input = input_metadata
        input_payload = typed_input.to_params()
        tool_call, tool_context = self._build_durable_tool_identity(
            task=task,
            step_id=step_id,
            tool_name=tool_name,
            input_metadata=typed_input,
        )
        started = time.monotonic()
        if self.enhanced_ui:
            self._set_dashboard_tool_status(
                parent_task_id=parent_task_id,
                tool_id=step_id,
                tool_name=tool_name,
                status="running",
            )
            param_lines = "\n".join(f"{key}: {value}" for key, value in input_payload.items())
            self.enhanced_ui.set_current_task_state(
                title=f"Tool: {tool_name}",
                details=f"Task: {task.description}\nStep: {step_id}\n{param_lines}",
                status="running",
            )

        self.logger.log_event(
            "tool_execution_start",
            {
                "task_id": task.id,
                "step_id": step_id,
                "tool": tool_name,
                "timeout_override": None,
                "input_metadata_summary": self._sanitize_tool_metadata(typed_input),
            },
            session_id=self.session_id or "unknown",
            turn_id=1,
        )
        self._record_durable_tool_started(tool_call)

        success = False
        output_metadata: ToolResultMetadata | None = None
        failure: FailureMetadata | None = None
        try:
            output_metadata = executor(typed_input)
            success = output_metadata.status == ResultStatus.SUCCESS
            failure = output_metadata.failure if not success else None
        except Exception as exc:
            failure = FailureMetadata(
                error_type=exc.__class__.__name__,
                error_message=str(exc) or f"{tool_name} failed.",
            )

        duration_seconds = time.monotonic() - started
        status = ResultStatus.SUCCESS if success else ResultStatus.FAIL
        if not success and failure is None:
            failure = FailureMetadata(
                error_type="ToolError",
                error_message=f"{tool_name} failed.",
            )
        result = ToolExecutionEnvelopeMetadata(
            tool_name=tool_name,
            step_id=step_id,
            status=status,
            success=success,
            input_metadata=typed_input,
            output_metadata=output_metadata,
            failure=failure,
            duration_seconds=duration_seconds,
            call_id=tool_call.call_id,
            tool_context=tool_context,
        )
        self._record_durable_tool_terminal(tool_call, result)

        if self.enhanced_ui:
            self._set_dashboard_tool_status(
                parent_task_id=parent_task_id,
                tool_id=step_id,
                tool_name=tool_name,
                status=status.value,
            )
            self.enhanced_ui.set_current_task_state(
                title=f"Tool: {tool_name}",
                details=(
                    "Tool returned successfully"
                    if success
                    else (failure.error_message if failure else f"{tool_name} failed.")
                ),
                status=status.value,
            )

        self.logger.log_event(
            "tool_executed",
            {
                "task_id": task.id,
                "step_id": step_id,
                "tool": tool_name,
                "success": success,
                "status": status.value,
                "error_type": failure.error_type if failure else None,
                "error": failure.error_message if failure else None,
                "duration_seconds": duration_seconds,
                "attempts_used": 1,
                "retry_count": 0,
                "output": self._summarize_metadata_output(output_metadata),
            },
            session_id=self.session_id or "unknown",
            turn_id=1,
        )
        return result

    def _readme_environment_context(self, environment_payload: dict[str, Any]) -> dict[str, Any]:
        return self.project_iteration.readme_environment_context(environment_payload)

    def _project_environment_context(self, project_path: Path | None) -> dict[str, Any]:
        return self.project_iteration.project_environment_context(
            project_path,
            getattr(self, "_project_environments", {}),
        )

    def _resolve_project_improvement_iterations(self, goal: str, project_path: str | Path) -> bool:
        """Resolve per-project improvement count, optionally asking the user."""
        return self.project_iteration.resolve_project_improvement_iterations(self, goal, project_path)

    def _analyze_project_improvements(
        self,
        *,
        goal: str,
        project_path: Path,
        written_files: list[str],
        run_command: str,
        readme_path: Path,
        completed_iteration: int,
        evaluation: EvaluationResult,
        session_constraints: SessionConstraintState | None = None,
        session_ingress_state: SessionIngressState | None = None,
        context_projection: DerivedContextProjection | None = None,
    ) -> dict[str, Any]:
        prompt_context = self._build_prompt_context(
            original_goal=goal,
            project_path=project_path,
            written_files=written_files,
            run_command=run_command,
            evaluation=evaluation,
            iteration_goal="Analyze product-fit and choose the next autonomous improvement.",
            acceptance_criteria=evaluation.recommended_actions,
            tool_task="Produce a concrete next-iteration project improvement report.",
            agent_instruction=(
                "Goal Maker context: judge what is actually better for the user's project type, "
                "not only what is easiest to add to the current implementation."
            ),
        )
        task = Task(
            id=str(uuid.uuid4()),
            description=f"Analyze project improvements after iteration {completed_iteration}",
            priority=TaskPriority.MEDIUM,
        )
        tool_result = self._execute_project_improvement_agent_tool(
            task=task,
            step_id=f"iteration_{completed_iteration}_project_improvement_tool",
            input_metadata=ToolInputMetadata.from_mapping("project_improvement_tool", {
                "project_path": str(project_path),
                "goal": goal,
                "written_files": written_files,
                "run_command": run_command,
                "iteration": completed_iteration,
                "validation_result": evaluation.model_dump(),
                "readme_path": str(readme_path),
                "prompt_context": prompt_context,
                "session_turn_source_hash": (
                    session_turn_ledger_hash(session_ingress_state)
                    if session_ingress_state is not None
                    else None
                ),
                "_session_constraints": (
                    session_constraints
                    if session_constraints is not None
                    else session_ingress_state.session_constraints
                    if session_ingress_state is not None
                    else None
                ),
                "_session_ingress_state": session_ingress_state,
                "_context_projection": context_projection,
            }),
            parent_task_id=self._dashboard_stage_id("goal_maker"),
        )
        if tool_result.success and tool_result.output is not None:
            return tool_result.output.to_json_dict()
        if self.project_improvement_policy.controls_top_level_success:
            failure = tool_result.failure
            failure_payload = (
                failure.to_json_dict()
                if hasattr(failure, "to_json_dict")
                else failure.model_dump(mode="json")
                if hasattr(failure, "model_dump")
                else {"error_message": tool_result.error_message}
            )
            raise OpenPilotError(
                tool_result.error_message
                or "Required project_improvement_tool did not return a usable report.",
                category=ErrorCategory.TERMINAL,
                context={
                    "failure_stage": "Project Improvement",
                    "failed_tool": "project_improvement_tool",
                    "tool_failure": failure_payload,
                },
            )
        fallback = {
            "summary": evaluation.summary,
            "improvement_opportunities": evaluation.improvement_opportunities,
            "recommended_actions": evaluation.recommended_actions,
            "next_iteration_goal": evaluation.next_iteration_goal,
            "blocking_risks": evaluation.validation_errors,
            "prompt_context": prompt_context,
            "product_judgment": prompt_context.get("product_judgment") or {},
            "source": "fallback",
            "fallback_reason": tool_result.error_message or "project_improvement_tool did not return a usable report.",
        }
        return fallback

    def _build_prompt_context(
        self,
        *,
        original_goal: str,
        project_path: Path | None = None,
        written_files: list[str] | None = None,
        run_command: str = "",
        evaluation: EvaluationResult | None = None,
        iteration_goal: str = "",
        acceptance_criteria: list[str] | None = None,
        tool_task: str = "",
        agent_instruction: str = "",
        target_file: Path | None = None,
        current_code: str = "",
        code_context: str = "",
        mode: str = "",
    ) -> dict[str, Any]:
        return self.improvement_context.build_prompt_context(
            original_goal=original_goal,
            project_path=project_path,
            written_files=written_files,
            run_command=run_command,
            evaluation=evaluation,
            iteration_goal=iteration_goal,
            acceptance_criteria=acceptance_criteria,
            tool_task=tool_task,
            agent_instruction=agent_instruction,
            target_file=target_file,
            current_code=current_code,
            code_context=code_context,
            mode=mode,
        )

    def _infer_product_judgment(
        self,
        *,
        original_goal: str,
        project_path: Path | None,
        written_files: list[str],
        current_code: str = "",
    ) -> dict[str, Any]:
        return self.improvement_context.infer_product_judgment(
            original_goal=original_goal,
            project_path=project_path,
            written_files=written_files,
            current_code=current_code,
        )

    def _quality_rubric_for_product(self, product_judgment: dict[str, Any]) -> list[str]:
        return self.improvement_context.quality_rubric_for_product(product_judgment)

    def _apply_project_improvement(
        self,
        *,
        goal: str,
        project_path: Path,
        written_files: list[str],
        run_command: str,
        readme_path: Path,
        iteration: int,
        evaluation: EvaluationResult,
        actions: list[str],
        improvement_report: dict[str, Any] | None = None,
        is_repair: bool = False,
    ) -> IterationResult:
        """Apply one safe project improvement round."""
        return self.autonomous_task_executor.execute_improvement(
            goal=goal,
            project_path=project_path,
            written_files=written_files,
            run_command=run_command,
            readme_path=readme_path,
            iteration=iteration,
            evaluation=evaluation,
            actions=actions,
            improvement_report=improvement_report,
            is_repair=is_repair,
        )

    def _run_code_generation_retry_pipeline(
        self,
        *,
        task: Task,
        iteration: int,
        goal: str,
        target_file: Path,
        current_code: str,
        evaluation: EvaluationResult,
        actions: list[str],
        improvement_report: dict[str, Any],
        is_repair: bool,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return self.autonomous_task_executor.run_code_generation_retry_pipeline(
            task=task,
            iteration=iteration,
            goal=goal,
            target_file=target_file,
            current_code=current_code,
            evaluation=evaluation,
            actions=actions,
            improvement_report=improvement_report,
            is_repair=is_repair,
        )

    def _build_code_generation_prompt_context(
        self,
        *,
        goal: str,
        target_file: Path,
        current_code: str,
        evaluation: EvaluationResult,
        actions: list[str],
        improvement_report: dict[str, Any],
        is_repair: bool,
        simplified: bool,
        mode: str,
    ) -> dict[str, Any]:
        return self.autonomous_task_executor.build_code_generation_prompt_context(
            goal=goal,
            target_file=target_file,
            current_code=current_code,
            evaluation=evaluation,
            actions=actions,
            improvement_report=improvement_report,
            is_repair=is_repair,
            simplified=simplified,
            mode=mode,
        )

    def _prompt_context_layer_summary(self, prompt_context: dict[str, Any]) -> dict[str, Any]:
        return self.improvement_context.prompt_context_layer_summary(prompt_context)

    def _build_surgical_project_improvement_prompt(
        self,
        *,
        goal: str,
        target_file: Path,
        current_code: str,
        evaluation: EvaluationResult,
        actions: list[str],
        improvement_report: dict[str, Any],
        is_repair: bool,
    ) -> str:
        return self.autonomous_task_executor.build_surgical_project_improvement_prompt(
            goal=goal,
            target_file=target_file,
            current_code=current_code,
            evaluation=evaluation,
            actions=actions,
            improvement_report=improvement_report,
            is_repair=is_repair,
        )

    def _code_generation_attempt_summary(
        self,
        *,
        mode: str,
        prompt: str,
        result: dict[str, Any],
        attempt: int,
    ) -> dict[str, Any]:
        return self.autonomous_task_executor.code_generation_attempt_summary(
            mode=mode,
            prompt=prompt,
            result=result,
            attempt=attempt,
        )

    def _append_code_generation_attempt_to_dashboard(self, iteration: int, attempt: dict[str, Any]) -> None:
        self.autonomous_task_executor.append_code_generation_attempt_to_dashboard(iteration, attempt)

    def _should_retry_code_generation_attempt(self, result: dict[str, Any]) -> bool:
        return self.autonomous_task_executor.should_retry_code_generation_attempt(result)

    def _build_project_improvement_prompt(
        self,
        *,
        goal: str,
        target_file: Path,
        current_code: str,
        evaluation: EvaluationResult,
        actions: list[str],
        improvement_report: dict[str, Any],
        is_repair: bool,
        simplified: bool,
    ) -> str:
        return self.autonomous_task_executor.build_project_improvement_prompt(
            goal=goal,
            target_file=target_file,
            current_code=current_code,
            evaluation=evaluation,
            actions=actions,
            improvement_report=improvement_report,
            is_repair=is_repair,
            simplified=simplified,
        )

    def _execute_code_generation_for_improvement(
        self,
        *,
        task: Task,
        iteration: int,
        target_file: Path,
        improvement_prompt: str,
        simplified: bool,
        mode: str | None = None,
        prompt_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.autonomous_task_executor.execute_code_generation_for_improvement(
            task=task,
            iteration=iteration,
            target_file=target_file,
            improvement_prompt=improvement_prompt,
            simplified=simplified,
            mode=mode,
            prompt_context=prompt_context,
        )

    def _is_timeout_tool_result(self, result: dict[str, Any]) -> bool:
        return self.autonomous_task_executor.is_timeout_tool_result(result)

    def _visible_tool_failure_summary(
        self,
        *,
        tool: str,
        tool_result: dict[str, Any],
        retry_attempted: bool = False,
    ) -> str:
        """Create a compact failure reason suitable for the dashboard."""
        return self.autonomous_task_executor.visible_tool_failure_summary(
            tool=tool,
            tool_result=tool_result,
            retry_attempted=retry_attempted,
        )

    def _budget_code_context(self, code: str, max_chars: int) -> str:
        return self.autonomous_task_executor.budget_code_context(code, max_chars)

    def _compact_code_context(self, code: str, actions: list[str], max_chars: int) -> str:
        return self.autonomous_task_executor.compact_code_context(code, actions, max_chars)

    def _log_iteration_failure(
        self,
        *,
        iteration: int,
        stage: str,
        tool: str,
        target_file: Path,
        actions: list[str],
        error: str,
        prompt_length: int,
        current_code_length: int,
        retry_attempted: bool,
        tool_result: dict[str, Any],
        retry_history: list[dict[str, Any]] | None = None,
    ) -> None:
        self.autonomous_task_executor.log_iteration_failure(
            iteration=iteration,
            stage=stage,
            tool=tool,
            target_file=target_file,
            actions=actions,
            error=error,
            prompt_length=prompt_length,
            current_code_length=current_code_length,
            retry_attempted=retry_attempted,
            tool_result=tool_result,
            retry_history=retry_history,
        )

    def _finish_active_operations(self, reason: str) -> None:
        """Clear stale active LLM/tool traces after a terminal iteration state."""
        self.iteration_dashboard.finish_active_operations(reason)

    def _format_iteration_failure(self, improvement_result: dict[str, Any] | None) -> str:
        """Return a concise, user-facing iteration failure summary."""
        return self.iteration_dashboard.format_iteration_failure(improvement_result)

    def _select_iteration_target_file(self, written_files: list[str], actions: list[str]) -> Path | None:
        return self.improvement_context.select_iteration_target_file(written_files, actions)

    def _reset_iteration_dashboard(self, goal: str) -> None:
        self.iteration_dashboard.reset_iteration_dashboard(goal)

    def _ensure_dashboard_iteration(self, iteration_number: int | None = None) -> str:
        return self.iteration_dashboard.ensure_dashboard_iteration(iteration_number)

    def _dashboard_iteration_stage_nodes(self, iteration_id: str) -> list[dict[str, Any]]:
        return self.iteration_dashboard.dashboard_iteration_stage_nodes(iteration_id)

    def _dashboard_stage_id(self, stage_key: str) -> str | None:
        return self.iteration_dashboard.dashboard_stage_id(stage_key)

    def _short_dashboard_text(self, value: Any, limit: int = 140) -> str:
        return self.iteration_dashboard.short_dashboard_text(value, limit)

    def _append_dashboard_stage_child(
        self,
        stage_key: str,
        *,
        child_id: str,
        description: str,
        kind: str,
        status: str = "completed",
        children: list[dict[str, Any]] | None = None,
    ) -> None:
        self.iteration_dashboard.append_dashboard_stage_child(
            stage_key,
            child_id=child_id,
            description=description,
            kind=kind,
            status=status,
            children=children,
        )

    def _handle_iteration_progress(self, event: str, payload: dict[str, Any]) -> None:
        self.iteration_dashboard.handle_iteration_progress(event, payload)

    def _append_dashboard_tasks(self, new_tasks: list[dict[str, Any]], current_task_id: str | None = None) -> None:
        self.iteration_dashboard.append_dashboard_tasks(new_tasks, current_task_id)

    def _set_dashboard_task_status(self, task_id: str, status: str) -> None:
        self.iteration_dashboard.set_dashboard_task_status(task_id, status)

    def _set_dashboard_running_descendants_status(self, parent_id: str | None, status: str) -> None:
        self.iteration_dashboard.set_dashboard_running_descendants_status(parent_id, status)

    def _append_dashboard_child(
        self,
        *,
        parent_id: str,
        child: dict[str, Any],
        current_task_id: str | None = None,
    ) -> None:
        self.iteration_dashboard.append_dashboard_child(
            parent_id=parent_id,
            child=child,
            current_task_id=current_task_id,
        )

    def _set_dashboard_tool_status(
        self,
        *,
        parent_task_id: str | None,
        tool_id: str,
        tool_name: str,
        status: str,
    ) -> None:
        self.iteration_dashboard.set_dashboard_tool_status(
            parent_task_id=parent_task_id,
            tool_id=tool_id,
            tool_name=tool_name,
            status=status,
        )

    def _update_dashboard_node(
        self,
        nodes: list[dict[str, Any]],
        node_id: str,
        updater,
    ) -> tuple[list[dict[str, Any]], bool]:
        return self.iteration_dashboard.update_dashboard_node(nodes, node_id, updater)

    def _results_include_tool(self, results: list[TaskExecutionResult], tool_name: str) -> bool:
        for result in results:
            task_payload = result.result_metadata.result if result.result_metadata else None
            tool_results = getattr(task_payload, "attributes", {}).get("tool_results", []) if task_payload else []
            for tool_result in tool_results:
                if tool_result.get("tool_name") == tool_name or tool_result.get("tool") == tool_name:
                    return True
        return False

    def _collect_written_files(self, results: list[TaskExecutionResult]) -> list[str]:
        files: list[str] = []
        seen: set[str] = set()
        for result in results:
            observed_files = result.attributes.get("observed_modified_files") or []
            if isinstance(observed_files, list):
                for path in observed_files:
                    normalized = str(path or "").strip()
                    if normalized and normalized not in seen:
                        files.append(normalized)
                        seen.add(normalized)
            task_payload = result.result_metadata.result if result.result_metadata else None
            tool_results = getattr(task_payload, "attributes", {}).get("tool_results", []) if task_payload else []
            for tool_result in tool_results:
                if not isinstance(tool_result, dict):
                    continue
                tool_name = tool_result.get("tool_name") or tool_result.get("tool")
                if tool_name not in {"file_writer", "file_patch_writer", "file_delete_tool"} or not tool_result.get("success"):
                    continue
                output = self._tool_result_payload(tool_result)
                path = self._file_path_from_payload(output)
                if not path:
                    input_metadata = tool_result.get("input_metadata") or {}
                    path = self._file_path_from_payload(input_metadata)
                if path and path not in seen:
                    files.append(path)
                    seen.add(path)
        return files

    def _tool_result_payload(self, tool_result: dict[str, Any]) -> Any:
        direct_result = tool_result.get("result")
        if direct_result is not None:
            return direct_result
        output_metadata = tool_result.get("output_metadata") or {}
        if hasattr(output_metadata, "result"):
            return output_metadata.result
        if isinstance(output_metadata, dict):
            return output_metadata.get("result")
        return None

    def _file_path_from_payload(self, payload: Any) -> str | None:
        if payload is None:
            return None
        file_path = getattr(payload, "file_path", None)
        if file_path:
            return str(file_path)
        if isinstance(payload, dict):
            direct = payload.get("file_path")
            if direct:
                return str(direct)
            result = payload.get("result")
            if isinstance(result, dict) and result.get("file_path"):
                return str(result["file_path"])
        return None

    def _infer_project_path_from_files(self, goal: str, written_files: list[str]) -> Path | None:
        current_context = getattr(self, "_current_execution_context", {}) or {}
        explicit_project = str(current_context.get("project_path") or "").strip()
        if explicit_project:
            return Path(explicit_project).expanduser().resolve()
        goal_path = self._extract_goal_path(goal)
        if goal_path:
            path = Path(goal_path).expanduser()
            if path.suffix:
                return path.parent
            return path

        if not written_files:
            return None
        first_file = Path(written_files[0]).expanduser()
        if first_file.suffix:
            return first_file.parent
        return first_file

    def _extract_goal_path(self, goal: str) -> str | None:
        path_match = re.search(r"['\"](?P<path>/[^'\"]+)['\"]", goal)
        return path_match.group("path") if path_match else None

    def _sanitize_tool_metadata(self, value: Any) -> dict[str, Any]:
        return self.tool_io.sanitize_tool_metadata(value)

    def _summarize_metadata_output(self, output: Any) -> dict[str, Any]:
        return self.tool_io.summarize_metadata_output(output)

    def _json_safe_summary(self, value: Any) -> Any:
        return self.tool_io.json_safe_summary(value)

    def _resolve_chained_metadata(
        self,
        tool_name: str,
        input_metadata: ToolInputMetadata,
        last_output: Any,
        last_code_output: Any,
    ) -> ToolInputMetadata:
        return self.tool_io.resolve_chained_metadata(
            tool_name,
            input_metadata,
            last_output,
            last_code_output,
        )

    def _extract_generated_content(self, output: Any) -> str | None:
        return self.tool_io.extract_generated_content(output)

    def _execute_with_enhanced_ui_v2(self, goal, context):
        return self.runtime_controller.run(goal, context, mode="enhanced_ui")

    def _execute_standard(self, goal: str, context: dict[str, Any]) -> dict[str, Any]:
        """Execute with standard console output."""
        return self.runtime_controller.run(goal, context, mode="standard")

    def _execute_tasks(
        self,
        tasks: list[Task],
        goal: str = "",
        *,
        prior_results: list[TaskExecutionResult] | None = None,
        start_index: int = 0,
        progress_sink: Any | None = None,
    ) -> list[TaskExecutionResult]:
        """Execute tasks using the runtime task graph and selected UI mode."""
        self._log_task_execution_event(
            "task_execution_started",
            input_summary={"total_tasks": len(tasks), "goal": goal},
            success=None,
        )
        task_graph = self.task_decomposer.build_task_graph(tasks)

        try:
            execution_order = self.task_decomposer.get_execution_order(task_graph)
        except ValueError:
            if self.use_enhanced_ui:
                self.enhanced_ui.log_activity(
                    "error",
                    "Cannot determine execution order, executing sequentially",
                )
            else:
                self.console.print(
                    "[yellow]⚠ Cannot determine execution order (cyclic dependencies?), executing sequentially[/yellow]"
                )
            execution_order = [task.id for task in tasks]
        execution_batches = IntelligentAutopilot._execution_batches_with_file_locks(tasks, execution_order)
        graph_nodes, graph_edges = IntelligentAutopilot._task_graph_metadata(tasks)
        self._log_task_execution_event(
            "task_graph_scheduled",
            input_summary={"total_tasks": len(tasks), "execution_order": execution_order},
            output_summary={
                "execution_batches": execution_batches,
                "task_graph_nodes": [node.to_json_dict() for node in graph_nodes],
                "task_graph_edges": [edge.to_json_dict() for edge in graph_edges],
            },
            success=True,
        )

        if self.use_enhanced_ui:
            results = self._execute_tasks_enhanced_ui(
                tasks,
                execution_order,
                goal,
                prior_results=prior_results,
                start_index=start_index,
                progress_sink=progress_sink,
            )
        else:
            results = self._execute_tasks_standard(
                tasks,
                execution_order,
                goal,
                prior_results=prior_results,
                start_index=start_index,
                progress_sink=progress_sink,
            )

        execution_state = IntelligentAutopilot._execution_state_metadata(tasks, results, execution_batches)
        self._log_task_execution_event(
            "task_execution_completed",
            output_summary={
                "results": len(results),
                "completed": len([result for result in results if result.status == TaskStatus.COMPLETED]),
                "failed": len([result for result in results if result.status == TaskStatus.FAILED]),
                "execution_state": execution_state.to_json_dict(),
            },
            success=all(result.status == TaskStatus.COMPLETED for result in results),
        )
        return results

    def _dashboard_task_items(
        self,
        tasks: list[Task],
        running_task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Convert task models into UI dashboard rows."""
        items = []
        for task in tasks:
            status = task.status.value if hasattr(task.status, "value") else str(task.status)
            if running_task_id and task.id == running_task_id and status == "pending":
                status = "running"
            items.append(
                {
                    "id": task.id,
                    "description": task.description,
                    "status": status,
                    "effort": f"{task.estimated_effort:.1f}u" if task.estimated_effort else "",
                }
            )
        return items

    def _implicit_dependencies_for_task(self, task: Task, execution_order: list[str], zero_based_index: int) -> list[str]:
        """Infer simple sequential dependencies from task wording."""
        dependencies = list(task.dependencies or [])
        description = task.description.lower()
        previous_markers = (
            "based on subtask",
            "requirements from subtask",
            "from subtask",
            "previous task",
            "prior task",
            "上一步",
            "上一",
            "前序",
            "前一步",
            "子任务 0",
            "子任务0",
        )
        if zero_based_index > 0 and any(marker in description for marker in previous_markers):
            previous_id = execution_order[zero_based_index - 1]
            if previous_id not in dependencies:
                dependencies.append(previous_id)
        return dependencies

    @staticmethod
    def _task_graph_metadata(tasks: list[Task]) -> tuple[list[TaskGraphNodeMetadata], list[TaskGraphEdgeMetadata]]:
        nodes = [
            TaskGraphNodeMetadata(
                task_id=task.id,
                description=task.description,
                priority=task.priority.value if hasattr(task.priority, "value") else str(task.priority),
                estimated_effort=task.estimated_effort,
                task_kind=task.kind,
                difficulty=task.difficulty,
                required_inputs=list(task.required_inputs),
                expected_outputs=list(task.expected_outputs),
                read_files=list(task.read_files),
                support_context_files=list(task.support_context_files),
                write_files=list(task.write_files),
                dependencies=list(task.dependencies),
                can_run_parallel=task.can_run_parallel,
                validation_command=task.validation_command,
                tags=list(task.tags),
                problem_resolution_depth=int(task.attributes.get("problem_resolution_depth") or 0),
                problem_resolution_parent_task_id=(
                    str(task.attributes.get("problem_resolution_parent_task_id"))
                    if task.attributes.get("problem_resolution_parent_task_id")
                    else None
                ),
            )
            for task in tasks
        ]
        edges = [
            TaskGraphEdgeMetadata(from_task=dependency_id, to_task=task.id, edge_type="blocks")
            for task in tasks
            for dependency_id in task.dependencies
        ]
        explicit_edges = {(edge.from_task, edge.to_task) for edge in edges}
        effective_dependencies = IntelligentAutopilot._effective_task_dependencies(tasks)
        for task in tasks:
            for dependency_id in effective_dependencies.get(task.id, set()):
                if dependency_id in task.dependencies or (dependency_id, task.id) in explicit_edges:
                    continue
                edges.append(TaskGraphEdgeMetadata(from_task=dependency_id, to_task=task.id, edge_type="validates"))
        return nodes, edges

    @staticmethod
    def _execution_batches_with_file_locks(tasks: list[Task], execution_order: list[str]) -> list[list[str]]:
        task_by_id = {task.id: task for task in tasks}
        effective_dependencies = IntelligentAutopilot._effective_task_dependencies(tasks)
        completed: set[str] = set()
        scheduled: set[str] = set()
        batches: list[list[str]] = []
        while len(scheduled) < len(execution_order):
            batch: list[str] = []
            locked_writes: set[str] = set()
            batch_allows_more = True
            progress = False
            for task_id in execution_order:
                if task_id in scheduled:
                    continue
                if not batch_allows_more:
                    break
                task = task_by_id.get(task_id)
                if task is None:
                    scheduled.add(task_id)
                    progress = True
                    continue
                if not all(dependency_id in completed for dependency_id in effective_dependencies.get(task.id, set())):
                    continue
                if not IntelligentAutopilot._task_can_join_batch(task, locked_writes, batch_is_empty=not batch):
                    continue
                batch.append(task_id)
                locked_writes.update(IntelligentAutopilot._normalized_task_write_files(task))
                if not task.can_run_parallel:
                    batch_allows_more = False
                scheduled.add(task_id)
                progress = True
            if not batch and not progress:
                remaining = [task_id for task_id in execution_order if task_id not in scheduled]
                if remaining:
                    batch = [remaining[0]]
                    scheduled.add(remaining[0])
            if batch:
                batches.append(batch)
                completed.update(batch)
        return batches

    @staticmethod
    def _effective_task_dependencies(tasks: list[Task]) -> dict[str, set[str]]:
        dependencies = {task.id: set(task.dependencies) for task in tasks}
        write_tasks = [task for task in tasks if task.write_files]
        for task in tasks:
            if task.kind != "validate" and not task.validation_command:
                continue
            for write_task in write_tasks:
                if write_task.id != task.id:
                    dependencies.setdefault(task.id, set()).add(write_task.id)
        return dependencies

    @staticmethod
    def _task_can_join_batch(task: Task, locked_writes: set[str], *, batch_is_empty: bool) -> bool:
        if not task.can_run_parallel and not batch_is_empty:
            return False
        writes = IntelligentAutopilot._normalized_task_write_files(task)
        if writes.intersection(locked_writes):
            return False
        return True

    @staticmethod
    def _normalized_task_write_files(task: Task) -> set[str]:
        return {IntelligentAutopilot._normalize_task_file_key(path) for path in task.write_files if str(path).strip()}

    @staticmethod
    def _normalize_task_file_key(path: str) -> str:
        try:
            return str(Path(path).expanduser().resolve())
        except OSError:
            return str(Path(path).expanduser())

    @staticmethod
    def _execution_state_metadata(
        tasks: list[Task],
        results: list[TaskExecutionResult],
        execution_batches: list[list[str]],
    ) -> ExecutionStateMetadata:
        completed = [result.task_id for result in results if result.status == TaskStatus.COMPLETED]
        failed = [result.task_id for result in results if result.status == TaskStatus.FAILED and not result.attributes.get("blocked")]
        blocked = [result.task_id for result in results if result.attributes.get("blocked")]
        changed_files: list[str] = []
        for result in results:
            observed = result.attributes.get("observed_modified_files") or []
            if isinstance(observed, list):
                changed_files.extend(str(path) for path in observed if str(path).strip())
        return ExecutionStateMetadata(
            completed_tasks=completed,
            failed_tasks=failed,
            blocked_tasks=blocked,
            changed_files=list(dict.fromkeys(changed_files)),
            validation_result={"all_completed": not failed and not blocked},
            execution_batches=execution_batches,
        )

    def _blocking_dependency(
        self,
        task: Task,
        tasks: list[Task],
        results: list[TaskExecutionResult],
        execution_order: list[str],
        zero_based_index: int,
    ) -> tuple[str, str] | None:
        dependencies = self._implicit_dependencies_for_task(task, execution_order, zero_based_index)
        if not dependencies:
            return None
        task_by_id = {candidate.id: candidate for candidate in tasks}
        result_by_id = {result.task_id: result for result in results}
        for dependency_id in dependencies:
            dependency_task = task_by_id.get(dependency_id)
            dependency_result = result_by_id.get(dependency_id)
            dependency_status = getattr(dependency_task, "status", None)
            result_status = getattr(dependency_result, "status", None)
            if dependency_status in {TaskStatus.FAILED, TaskStatus.BLOCKED} or result_status == TaskStatus.FAILED:
                reason = (
                    getattr(dependency_result, "error", None)
                    or getattr(dependency_task, "error", None)
                    or "dependency did not complete successfully"
                )
                return dependency_id, str(reason)
        return None

    def _blocked_task_result(self, task: Task, blocking_task_id: str, root_cause: str) -> TaskExecutionResult:
        message = f"Blocked because task {blocking_task_id} failed: {root_cause}"
        task.mark_blocked()
        task.error = message
        metadata = TaskResultMetadata(
            task_id=task.id,
            status=ResultStatus.FAIL,
            failure=FailureMetadata(
                error_type="TaskBlocked",
                error_message=message,
                details={
                    "task_id": task.id,
                    "task_description": task.description,
                    "blocked_by_task_id": blocking_task_id,
                    "root_cause": root_cause,
                    "failure_stage": "Task Dependency",
                    "suggested_recovery": "Fix the failed dependency task before retrying this task.",
                },
            ),
        )
        task.result = metadata
        return TaskExecutionResult(
            task_id=task.id,
            status=TaskStatus.FAILED,
            result_metadata=metadata,
            error=message,
            duration=0.0,
            attributes={"blocked": True, "blocked_by_task_id": blocking_task_id},
        )

    def _execution_history_payload(self, tasks: list[Task], results: list[TaskExecutionResult]) -> list[dict[str, Any]]:
        task_by_id = {task.id: task for task in tasks}
        history: list[dict[str, Any]] = []
        for result in results:
            task = task_by_id.get(result.task_id)
            history.append(
                {
                    "task_id": result.task_id,
                    "description": task.description if task else None,
                    "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                    "failure_type": (
                        result.result_metadata.failure.error_type
                        if result.result_metadata is not None and result.result_metadata.failure is not None
                        else None
                    ),
                    "error": result.error,
                    "result_summary": self._history_result_summary(result),
                    "observed_paths": self._history_observed_paths(result),
                }
            )
        return history

    def _history_result_summary(self, result: TaskExecutionResult) -> Any:
        if result.result_summary is not None:
            return result.result_summary
        if result.result_metadata is None:
            return None
        summary: dict[str, Any] = {}
        artifact = result.result_metadata.result
        if artifact is not None:
            content = getattr(artifact, "content", None)
            if content and str(content).strip() and str(content).strip() != "completed":
                summary["content_preview"] = self._history_value_summary(str(content), max_length=400)
            attributes = getattr(artifact, "attributes", None)
            if isinstance(attributes, dict):
                if "final_output" in attributes:
                    summary["final_output"] = self._history_value_summary(attributes.get("final_output"))
                tool_results = attributes.get("tool_results")
                if isinstance(tool_results, list) and tool_results:
                    summary["tool_results"] = self._history_value_summary(tool_results[-3:])
                if "all_tools_succeeded" in attributes:
                    summary["all_tools_succeeded"] = bool(attributes.get("all_tools_succeeded"))
        if result.error:
            summary["error"] = result.error
        if not summary:
            summary = self._history_value_summary(result.result_metadata.to_json_dict())
        return summary

    def _history_observed_paths(self, result: TaskExecutionResult) -> list[str]:
        if result.observed_paths:
            return list(result.observed_paths)
        if result.result_metadata is None:
            return []
        artifact = result.result_metadata.result
        candidates: list[str] = []
        if artifact is not None:
            content = getattr(artifact, "content", None)
            if isinstance(content, str):
                candidates.extend(self._extract_history_paths_from_text(content))
            attributes = getattr(artifact, "attributes", None)
            if isinstance(attributes, dict):
                candidates.extend(self._collect_history_paths(attributes))
        return list(dict.fromkeys(path for path in candidates if path))[:12]

    def _collect_history_paths(self, value: Any) -> list[str]:
        paths: list[str] = []
        safe_value = self._json_safe_summary(value)
        if isinstance(safe_value, dict):
            for key, nested in safe_value.items():
                lowered = str(key).lower()
                if lowered in {"file_path", "directory_path", "project_path", "path"} and nested:
                    paths.append(str(nested))
                elif lowered in {"files", "file_paths", "written_files", "sketch_files", "candidate_paths"} and isinstance(nested, list):
                    paths.extend(str(item) for item in nested if item)
                elif lowered in {"content", "result_summary", "summary"} and isinstance(nested, str):
                    paths.extend(self._extract_history_paths_from_text(nested))
                else:
                    paths.extend(self._collect_history_paths(nested))
            return paths
        if isinstance(safe_value, list):
            for item in safe_value:
                paths.extend(self._collect_history_paths(item))
            return paths
        if isinstance(safe_value, str):
            return self._extract_history_paths_from_text(safe_value)
        return []

    def _extract_history_paths_from_text(self, text: str) -> list[str]:
        if not text:
            return []
        paths: list[str] = []
        absolute_pattern = re.compile(r"(/[^\s`'\"<>|]+)")
        relative_pattern = re.compile(r"(?<![\w.-])([A-Za-z0-9_./-]+\.(?:py|md|json|toml|yaml|yml|txt))(?![\w.-])")
        for match in absolute_pattern.finditer(text):
            candidate = match.group(1).strip("`'\"()[]{}<>,;:")
            if candidate:
                paths.append(candidate)
        for match in relative_pattern.finditer(text):
            candidate = match.group(1).strip("`'\"()[]{}<>,;:")
            if candidate and ".." not in Path(candidate).parts:
                paths.append(candidate)
        return paths

    def _history_value_summary(self, value: Any, *, max_length: int = 300, depth: int = 0) -> Any:
        safe_value = self._json_safe_summary(value)
        if depth >= 3:
            return self._trim_history_text(safe_value, max_length=max_length)
        if isinstance(safe_value, str):
            return self._trim_history_text(safe_value, max_length=max_length)
        if isinstance(safe_value, list):
            return [self._history_value_summary(item, max_length=max_length, depth=depth + 1) for item in safe_value[:5]]
        if isinstance(safe_value, dict):
            important_keys = (
                "tool_name",
                "status",
                "success",
                "question",
                "reason",
                "file_path",
                "directory_path",
                "project_path",
                "files",
                "sketch_files",
                "content",
                "result",
                "output",
                "output_metadata",
                "input_metadata",
                "error",
                "error_message",
            )
            keys = [key for key in important_keys if key in safe_value]
            keys.extend(key for key in safe_value.keys() if key not in keys)
            compact: dict[str, Any] = {}
            for key in keys[:8]:
                compact[str(key)] = self._history_value_summary(safe_value[key], max_length=max_length, depth=depth + 1)
            return compact
        return safe_value

    def _trim_history_text(self, value: Any, *, max_length: int = 300) -> Any:
        if isinstance(value, str) and len(value) > max_length:
            return f"{value[:max_length]}..."
        return value

    def _execute_tasks_enhanced_ui(
        self,
        tasks: list[Task],
        execution_order: list[str],
        goal: str,
        *,
        prior_results: list[TaskExecutionResult] | None = None,
        start_index: int = 0,
        progress_sink: Any | None = None,
    ) -> list[TaskExecutionResult]:
        """Execute tasks with enhanced UI updates."""
        results = list(prior_results or [])

        self.logger.log_event(
            "task_execution_started",
            {
                "total_tasks": len(tasks),
                "execution_order": execution_order,
                "goal": goal,
            },
            session_id=self.session_id or "unknown",
            turn_id=1,
        )
        self._log_task_execution_event(
            "enhanced_task_execution_started",
            input_summary={"total_tasks": len(tasks), "execution_order": execution_order},
            success=None,
        )

        for zero_based_index, task_id in enumerate(
            execution_order[start_index:],
            start=start_index,
        ):
            index = zero_based_index + 1
            task = next((candidate for candidate in tasks if candidate.id == task_id), None)
            if not task:
                self.logger.log_event(
                    "task_not_found",
                    {"task_id": task_id, "index": index},
                    session_id=self.session_id or "unknown",
                    turn_id=1,
                )
                continue

            blocking = self._blocking_dependency(task, tasks, results, execution_order, index - 1)
            if blocking:
                blocked_by, root_cause = blocking
                result = self._blocked_task_result(task, blocked_by, root_cause)
                results.append(result)
                self.enhanced_ui.set_task_graph_state(
                    tasks=self._dashboard_task_items(tasks),
                    current_task_id=task.id,
                )
                self.enhanced_ui.set_current_task_state(
                    title=f"Task {index}/{len(tasks)}",
                    details=result.error or "Task blocked",
                    status="failed",
                )
                if progress_sink is not None:
                    progress_sink(tasks, execution_order, results, zero_based_index + 1)
                self.enhanced_ui.log_activity("error", f"✗ Task {index} blocked: {result.error}")
                self.logger.log_event(
                    "task_execution_blocked",
                    {
                        "task_id": task.id,
                        "task_index": index,
                        "description": task.description,
                        "blocked_by_task_id": blocked_by,
                        "root_cause": root_cause,
                    },
                    session_id=self.session_id or "unknown",
                    turn_id=1,
                )
                continue

            self.logger.log_event(
                "task_execution_start",
                {
                    "task_id": task.id,
                    "task_index": index,
                    "description": task.description,
                    "priority": task.priority.value if hasattr(task.priority, "value") else str(task.priority),
                },
                session_id=self.session_id or "unknown",
                turn_id=1,
            )

            completed_count = len([result for result in results if result.status == TaskStatus.COMPLETED])
            failed_count = len([result for result in results if result.status == TaskStatus.FAILED])
            status_detail = (
                f"{task.description}\n\n"
                f"Completed: {completed_count}\n"
                f"Failed: {failed_count}\n"
                f"Remaining: {len(tasks) - index}"
            )

            self.enhanced_ui.set_task_graph_state(
                tasks=self._dashboard_task_items(tasks, running_task_id=task.id),
                current_task_id=task.id,
            )
            self.enhanced_ui.set_current_task_state(
                title=f"Task {index}/{len(tasks)}",
                details=status_detail,
                status="running",
            )

            task_context = TaskExecutionContext(
                task=task,
                parent_context=self._task_parent_context(goal),
                shared_state={"previous_task_results": self._execution_history_payload(tasks, results)},
                execution_history=self._execution_history_payload(tasks, results),
            )

            try:
                result = IntelligentAutopilot._execute_task_with_problem_resolution(self, task, task_context, goal)
                results.append(result)

                self.logger.log_event(
                    "task_execution_complete",
                    {
                        "task_id": task.id,
                        "task_index": index,
                        "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                        "success": result.status == TaskStatus.COMPLETED,
                        "error": result.error,
                        "duration": result.duration,
                        "result_summary": str(result.result_metadata)[:200] if result.result_metadata else None,
                    },
                    session_id=self.session_id or "unknown",
                    turn_id=1,
                )

                if result.status == TaskStatus.COMPLETED:
                    task.mark_completed(result.result_metadata)
                    self.enhanced_ui.set_task_graph_state(
                        tasks=self._dashboard_task_items(tasks),
                        current_task_id=task.id,
                    )
                    self.enhanced_ui.set_current_task_state(
                        title=f"Task {index}/{len(tasks)}",
                        details=task.description,
                        status="completed",
                    )
                    self.enhanced_ui.log_activity(
                        "success",
                        f"✓ Task {index}: {task.description[:50]}... ({result.duration:.1f}s)",
                    )
                else:
                    task.mark_failed(result.error or "Unknown error")
                    task.result = result.result_metadata
                    self.enhanced_ui.set_task_graph_state(
                        tasks=self._dashboard_task_items(tasks),
                        current_task_id=task.id,
                    )
                    self.enhanced_ui.set_current_task_state(
                        title=f"Task {index}/{len(tasks)}",
                        details=result.error or "Unknown error",
                        status="failed",
                    )
                    self.enhanced_ui.log_activity("error", f"✗ Task {index} failed: {result.error}")
                    self.logger.log_event(
                        "task_execution_failed",
                        {
                            "task_id": task.id,
                            "task_index": index,
                            "description": task.description,
                            "error": result.error,
                            "result_metadata": result.result_metadata.to_json_dict() if result.result_metadata else None,
                        },
                        session_id=self.session_id or "unknown",
                        turn_id=1,
                    )

                if task.status == TaskStatus.PENDING:
                    self.logger.log_event(
                        "task_status_update_failed",
                        {
                            "task_id": task.id,
                            "task_index": index,
                            "result_status": result.status.value if hasattr(result.status, "value") else str(result.status),
                            "task_status": task.status.value if hasattr(task.status, "value") else str(task.status),
                        },
                        session_id=self.session_id or "unknown",
                        turn_id=1,
                    )
                    if result.status == TaskStatus.COMPLETED:
                        task.status = TaskStatus.COMPLETED
                        task.result = result.result_metadata
                    else:
                        task.status = TaskStatus.FAILED
                        task.error = result.error
                        task.result = result.result_metadata

                if progress_sink is not None:
                    progress_sink(tasks, execution_order, results, zero_based_index + 1)

            except Exception as exc:
                error_msg = f"Task execution exception: {str(exc)}"
                self.enhanced_ui.log_activity("error", f"✗ Task {index} exception: {str(exc)}")
                self.logger.log_event(
                    "task_execution_exception",
                    {
                        "task_id": task.id,
                        "task_index": index,
                        "description": task.description,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                    session_id=self.session_id or "unknown",
                    turn_id=1,
                )

                result = TaskExecutionResult(
                    task_id=task.id,
                    status=TaskStatus.FAILED,
                    error=error_msg,
                    duration=0.0,
                    attributes={},
                )
                results.append(result)
                task.mark_failed(error_msg)
                self.enhanced_ui.set_task_graph_state(
                    tasks=self._dashboard_task_items(tasks),
                    current_task_id=task.id,
                )
                self.enhanced_ui.set_current_task_state(
                    title=f"Task {index}/{len(tasks)}",
                    details=error_msg,
                    status="failed",
                )
                if progress_sink is not None:
                    progress_sink(tasks, execution_order, results, zero_based_index + 1)

        completed = len([result for result in results if result.status == TaskStatus.COMPLETED])
        failed = len([result for result in results if result.status == TaskStatus.FAILED])
        self.logger.log_event(
            "task_execution_summary",
            {
                "total": len(results),
                "completed": completed,
                "failed": failed,
                "task_statuses": [
                    {
                        "id": task.id,
                        "description": task.description[:50],
                        "status": task.status.value if hasattr(task.status, "value") else str(task.status),
                    }
                    for task in tasks
                ],
            },
            session_id=self.session_id or "unknown",
            turn_id=1,
        )
        self._log_task_execution_event(
            "enhanced_task_execution_completed",
            output_summary={"completed": completed, "failed": failed},
            success=failed == 0,
        )

        return results

    def _execute_tasks_standard(
        self,
        tasks: list[Task],
        execution_order: list[str],
        goal: str,
        *,
        prior_results: list[TaskExecutionResult] | None = None,
        start_index: int = 0,
        progress_sink: Any | None = None,
    ) -> list[TaskExecutionResult]:
        """Execute tasks with standard console output."""
        results = list(prior_results or [])
        self._log_task_execution_event(
            "standard_task_execution_started",
            input_summary={"total_tasks": len(tasks), "execution_order": execution_order},
            success=None,
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=self.console,
        ) as progress:
            task_progress = progress.add_task("Executing tasks...", total=len(tasks))

            for zero_based_index, task_id in enumerate(
                execution_order[start_index:],
                start=start_index,
            ):
                index = zero_based_index + 1
                task = next((candidate for candidate in tasks if candidate.id == task_id), None)
                if not task:
                    continue

                blocking = self._blocking_dependency(task, tasks, results, execution_order, index - 1)
                if blocking:
                    blocked_by, root_cause = blocking
                    result = self._blocked_task_result(task, blocked_by, root_cause)
                    results.append(result)
                    self.stats["tasks_failed"] += 1
                    self.logger.log_event(
                        "task_execution_blocked",
                        {
                            "task_id": task.id,
                            "task_index": index,
                            "description": task.description,
                            "blocked_by_task_id": blocked_by,
                            "root_cause": root_cause,
                        },
                        session_id=self.session_id or "unknown",
                        turn_id=1,
                    )
                    self.console.print(
                        f"  [red]✗[/red] Task {index}: {task.description[:60]} (blocked)"
                    )
                    self.console.print(f"    [red]Error: {result.error}[/red]")
                    if progress_sink is not None:
                        progress_sink(tasks, execution_order, results, zero_based_index + 1)
                    progress.advance(task_progress)
                    continue

                progress.update(
                    task_progress,
                    description=f"[{index}/{len(tasks)}] {task.description[:50]}...",
                )

                task_context = TaskExecutionContext(
                    task=task,
                    parent_context=self._task_parent_context(goal),
                    shared_state={"previous_task_results": self._execution_history_payload(tasks, results)},
                    execution_history=self._execution_history_payload(tasks, results),
                )

                try:
                    result = IntelligentAutopilot._execute_task_with_problem_resolution(self, task, task_context, goal)
                except Exception as exc:
                    result = TaskExecutionResult(
                        task_id=task.id,
                        status=TaskStatus.FAILED,
                        error=f"Task execution exception: {str(exc)}",
                        duration=0.0,
                        attributes={},
                    )
                results.append(result)

                if result.status == TaskStatus.COMPLETED:
                    task.mark_completed(result.result_metadata)
                    self.stats["tasks_completed"] += 1
                    status_icon = "✓"
                    status_color = "green"
                else:
                    task.mark_failed(result.error or "Unknown error")
                    task.result = result.result_metadata
                    self.stats["tasks_failed"] += 1
                    status_icon = "✗"
                    status_color = "red"

                self.console.print(
                    f"  [{status_color}]{status_icon}[/{status_color}] "
                    f"Task {index}: {task.description[:60]} "
                    f"({result.duration:.1f}s)"
                )

                if result.error:
                    self.console.print(f"    [red]Error: {result.error}[/red]")

                if progress_sink is not None:
                    progress_sink(tasks, execution_order, results, zero_based_index + 1)

                progress.advance(task_progress)

        self._log_task_execution_event(
            "standard_task_execution_completed",
            output_summary={
                "results": len(results),
                "completed": len([result for result in results if result.status == TaskStatus.COMPLETED]),
                "failed": len([result for result in results if result.status == TaskStatus.FAILED]),
            },
            success=all(result.status == TaskStatus.COMPLETED for result in results),
        )
        return results

    def _log_task_execution_event(
        self,
        event_type: str,
        *,
        success: bool | None = None,
        input_summary: Any | None = None,
        output_summary: Any | None = None,
        error: str | None = None,
    ) -> None:
        logger = getattr(self, "logger", None)
        if not logger:
            return
        logger.log_structured_event(
            source_type="module",
            source_name="autonomous_iteration.intelligent_autopilot",
            phase="task_execution",
            event_type=event_type,
            session_id=getattr(self, "session_id", None) or "unknown",
            turn_id=1,
            success=success,
            input_summary=input_summary,
            output_summary=output_summary,
            error=error,
        )

    def _map_reason_to_enum(self, reason_text: str) -> str:
        """Map free-form reason text to SelectionReason enum value.

        Args:
            reason_text: Free-form text from LLM

        Returns:
            Valid SelectionReason enum value
        """
        return self.tool_io.map_reason_to_enum(reason_text)

    @staticmethod
    def _execute_task_with_problem_resolution(
        runtime: Any,
        task: Task,
        context: TaskExecutionContext,
        goal: str,
    ) -> TaskExecutionResult:
        """Execute one task and locally decompose recoverable hard planning gaps."""
        ensure_environment = getattr(runtime, "_ensure_environment_for_task", None)
        if callable(ensure_environment):
            environment_error = ensure_environment(task, context)
            if environment_error:
                return TaskExecutionResult(
                    task_id=task.id,
                    status=TaskStatus.FAILED,
                    error=environment_error,
                    result_metadata=TaskResultMetadata(
                        task_id=task.id,
                        status=ResultStatus.FAIL,
                        failure=FailureMetadata(
                            error_type="EnvironmentNotReady",
                            error_message=environment_error,
                            recoverable=True,
                            retry_recommended=True,
                        ),
                    ),
                )
        result = runtime._execute_task(task, context)
        resolution_plan = IntelligentAutopilot._failure_resolution_plan(result)
        if not resolution_plan or resolution_plan.get("strategy") != "decompose":
            return result
        depth = int(task.attributes.get("problem_resolution_depth") or 0)
        if depth >= 1:
            return result
        decomposer = getattr(runtime, "task_decomposer", None)
        if decomposer is None or not hasattr(decomposer, "decompose"):
            return result

        failure_details = IntelligentAutopilot._failure_details(result)
        try:
            runtime._log_task_execution_event(
                "task_problem_decomposition_started",
                input_summary={
                    "task_id": task.id,
                    "resolution_plan": resolution_plan,
                    "problem_signal": failure_details.get("problem_signal"),
                },
                success=None,
            )
            decomposition = decomposer.decompose(
                task.description,
                context={
                    "goal": goal,
                    "parent_task_id": task.id,
                    "problem_signal": failure_details.get("problem_signal"),
                    "problem_judgment": failure_details.get("problem_judgment"),
                    "difficulty_assessment": failure_details.get("difficulty_assessment"),
                    "resolution_plan": resolution_plan,
                },
                parent_task_id=task.id,
            )
        except Exception as exc:
            runtime._log_task_execution_event(
                "task_problem_decomposition_failed",
                input_summary={"task_id": task.id},
                success=False,
                error=str(exc),
            )
            return result

        subtasks = list(getattr(decomposition, "subtasks", []) or [])
        if not subtasks:
            runtime._log_task_execution_event(
                "task_problem_decomposition_empty",
                input_summary={"task_id": task.id},
                success=False,
                error="Problem decomposition produced no subtasks",
            )
            return result
        for subtask in subtasks:
            subtask.attributes["problem_resolution_depth"] = depth + 1
            subtask.attributes["problem_resolution_parent_task_id"] = task.id

        sub_results = runtime._execute_tasks(subtasks, goal)
        all_completed = all(sub_result.status == TaskStatus.COMPLETED for sub_result in sub_results)
        duration = sum(float(sub_result.duration or 0.0) for sub_result in sub_results)
        payload = {
            "original_task_id": task.id,
            "resolution_strategy": "decompose",
            "subtask_count": len(subtasks),
            "subtask_results": [
                {
                    "task_id": sub_result.task_id,
                    "status": sub_result.status.value if hasattr(sub_result.status, "value") else str(sub_result.status),
                    "error": sub_result.error,
                }
                for sub_result in sub_results
            ],
            "original_failure": failure_details,
        }
        runtime._log_task_execution_event(
            "task_problem_decomposition_completed",
            input_summary={"task_id": task.id, "subtask_count": len(subtasks)},
            output_summary=payload,
            success=all_completed,
        )
        if all_completed:
            return TaskExecutionResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                result_metadata=TaskResultMetadata(
                    task_id=task.id,
                    status=ResultStatus.SUCCESS,
                    result=TextArtifactMetadata(content="problem resolved through decomposition", attributes=payload),
                    duration=duration,
                ),
                duration=duration,
                attributes={"problem_resolution": payload},
            )

        failed = [sub_result for sub_result in sub_results if sub_result.status == TaskStatus.FAILED]
        error = failed[0].error if failed else "Problem decomposition did not complete"
        return TaskExecutionResult(
            task_id=task.id,
            status=TaskStatus.FAILED,
            error=f"Problem decomposition failed: {error}",
            result_metadata=TaskResultMetadata(
                task_id=task.id,
                status=ResultStatus.FAIL,
                failure=FailureMetadata(
                    error_type="ProblemDecompositionFailed",
                    error_message=f"Problem decomposition failed: {error}",
                    details=payload,
                ),
                duration=duration,
            ),
            duration=duration,
            attributes={"problem_resolution": payload},
        )

    @staticmethod
    def _failure_resolution_plan(result: TaskExecutionResult) -> dict[str, Any]:
        details = IntelligentAutopilot._failure_details(result)
        resolution_plan = details.get("resolution_plan")
        return resolution_plan if isinstance(resolution_plan, dict) else {}

    @staticmethod
    def _failure_details(result: TaskExecutionResult) -> dict[str, Any]:
        metadata = getattr(result, "result_metadata", None)
        failure = getattr(metadata, "failure", None)
        details = getattr(failure, "details", None)
        return details if isinstance(details, dict) else {}

    def _execute_task(self, task: Task, context: TaskExecutionContext) -> TaskExecutionResult:
        """Execute a single task through the module-owned tool planning agent."""
        return self.tool_planning_task_executor.execute_task(task, context)

    def _format_tools_for_llm(self, tools: list) -> str:
        """Format available tools for LLM prompt."""
        return self.tool_io.format_tools_for_llm(tools)

    def _format_planning_surface(
        self,
        tools: list,
        *,
        task_description: str,
        goal: str = "",
        history_text: str = "",
        retry_reason: str = "",
        signal: Any | None = None,
        plan_data: dict[str, Any] | None = None,
        capability_card_providers: list[Any] | None = None,
    ) -> str:
        """Format a compact planner-facing capability surface."""
        return self.tool_io.format_planning_surface(
            tools,
            task_description=task_description,
            goal=goal,
            history_text=history_text,
            retry_reason=retry_reason,
            signal=signal,
            plan_data=plan_data,
            capability_card_providers=capability_card_providers,
        )

    def _resolve_selection_metadata(
        self,
        selection: ToolSelection,
        step_outputs: dict[str, Any]
    ) -> ToolSelection:
        """Resolve tool metadata from previous step outputs.

        Based on the modern autopilot tool-chaining path.
        """
        return self.tool_io.resolve_selection_metadata(selection, step_outputs)

    def _show_start_panel(self, goal: str):
        """Show start panel."""
        return self.console_presenter.show_start_panel(goal)

    def _show_task_tree(self, decomposition):
        """Show task decomposition tree."""
        return self.console_presenter.show_task_tree(decomposition)

    def _show_completion_summary(self, decomposition, results):
        """Show completion summary."""
        return self.console_presenter.show_completion_summary(decomposition, results)

    def _build_task_graph_for_ui(self, decomposition) -> dict[str, Any]:
        """Build task graph structure for enhanced UI display."""
        return self.console_presenter.build_task_graph_for_ui(decomposition)
