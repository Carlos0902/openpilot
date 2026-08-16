"""Task decomposition agent for breaking down complex tasks."""

from __future__ import annotations

import inspect
import re
import uuid
from typing import Any, Callable

from core.graph import Graph, GraphNode, GraphEdge, GraphType
from core.llm import LLMClient, LLMMessage, LLMRequest
from memory.context_assembly import build_context_candidate_request, build_context_llm_request
from memory.session_constraints import build_session_constraint_candidate
from metadata import (
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ContextRequestPurpose,
    SessionConstraintState,
    SessionIngressState,
)
from autonomous_iteration.task_models import (
    Task,
    TaskStatus,
    TaskPriority,
    TaskDecompositionRequest,
    TaskDecompositionResult
)


class TaskDecomposer:
    """Agent for decomposing complex tasks into subtasks."""

    _TASK_KIND_ALIASES = {
        "analysis": "inspect",
        "check": "inspect",
        "codebase_understanding": "codebase_understanding",
        "document": "document",
        "documentation": "document",
        "implement": "implement",
        "implementation": "implement",
        "inspect": "inspect",
        "inspection": "inspect",
        "investigate": "inspect",
        "repair": "repair",
        "test": "validate",
        "validate": "validate",
        "validation": "validate",
        "verify": "validate",
    }

    def __init__(
        self,
        llm_client: LLMClient,
        max_decomposition_depth: int = 3,
        min_subtask_complexity: float = 0.1,
        logger: Any | None = None,
        session_id_getter: Callable[[], str | None] | None = None,
    ):
        """Initialize task decomposer.

        Args:
            llm_client: LLM client for task analysis
            max_decomposition_depth: Maximum decomposition depth
            min_subtask_complexity: Minimum complexity to decompose further
        """
        self.llm_client = llm_client
        self.max_decomposition_depth = max_decomposition_depth
        self.min_subtask_complexity = min_subtask_complexity
        self.logger = logger
        self.session_id_getter = session_id_getter or (lambda: None)

    def should_decompose(self, task: Task, current_depth: int = 0) -> bool:
        """Determine if a task should be decomposed.

        Args:
            task: Task to analyze
            current_depth: Current decomposition depth

        Returns:
            True if task should be decomposed
        """
        # Don't decompose if max depth reached
        if current_depth >= self.max_decomposition_depth:
            return False

        # Don't decompose if already has subtasks
        if task.attributes.get("has_subtasks"):
            return False

        # Use LLM to analyze task complexity
        complexity = self._estimate_complexity(task)

        return complexity > self.min_subtask_complexity

    def decompose(
        self,
        task_description: str,
        context: dict[str, Any] | None = None,
        parent_task_id: str | None = None
    ) -> TaskDecompositionResult:
        self._log_agent(
            "task_decomposition_started",
            input_summary={"task_description": task_description, "context_keys": list((context or {}).keys())},
            success=None,
        )
        try:
            result = self._decompose_impl(task_description, context, parent_task_id)
        except Exception as exc:
            self._log_agent(
                "task_decomposition_failed",
                input_summary={"task_description": task_description},
                success=False,
                error=str(exc),
            )
            raise
        self._log_agent(
            "task_decomposition_completed",
            input_summary={"task_description": task_description},
            output_summary={"subtask_count": len(result.subtasks), "estimated_effort": result.estimated_total_effort},
            success=True,
        )
        return result

    def _decompose_impl(
        self,
        task_description: str,
        context: dict[str, Any] | None = None,
        parent_task_id: str | None = None
    ) -> TaskDecompositionResult:
        """Decompose a task into subtasks.

        Args:
            task_description: Description of the task
            context: Optional context information
            parent_task_id: Optional parent task ID

        Returns:
            TaskDecompositionResult with subtasks and task graph
        """
        context = context or {}

        # Create original task
        original_task = Task(
            id=str(uuid.uuid4()),
            description=task_description,
            parent_id=parent_task_id,
            attributes={"context": context}
        )

        # Analyze task and generate decomposition
        decomposition = self._generate_decomposition(original_task, context)
        if self._is_simple_code_artifact(task_description):
            decomposition["subtasks"] = self._compact_simple_code_subtasks(decomposition.get("subtasks", []))

        # Create subtasks
        subtasks = []
        task_graph = Graph(GraphType.DIRECTED)

        # Add original task to graph
        task_graph.add_node(GraphNode(
            id=original_task.id,
            type="task",
            data={"description": original_task.description, "is_root": True}
        ))

        # First pass: create all subtasks with temporary index-based dependencies
        subtask_indices = []  # Store (subtask, original_dependencies_indices)
        for raw_subtask_desc in decomposition["subtasks"]:
            subtask_desc = self._normalize_subtask_contract(raw_subtask_desc)
            # Store raw dependencies (might be integers)
            raw_deps = subtask_desc.get("dependencies", [])

            subtask = Task(
                id=str(uuid.uuid4()),
                description=subtask_desc["description"],
                parent_id=original_task.id,
                priority=TaskPriority(subtask_desc.get("priority", "medium")),
                estimated_effort=subtask_desc.get("estimated_effort"),
                dependencies=[],  # Will be filled in second pass
                tags=subtask_desc.get("tags", []),
                kind=str(subtask_desc.get("kind") or subtask_desc.get("task_kind") or "general"),
                difficulty=str(subtask_desc.get("difficulty") or "simple"),
                required_inputs=self._string_list(subtask_desc.get("required_inputs")),
                expected_outputs=self._string_list(subtask_desc.get("expected_outputs")),
                read_files=self._string_list(subtask_desc.get("read_files")),
                support_context_files=self._string_list(subtask_desc.get("support_context_files")),
                write_files=self._string_list(subtask_desc.get("write_files")),
                can_run_parallel=bool(subtask_desc.get("can_run_parallel", True)),
                validation_command=str(subtask_desc.get("validation_command") or ""),
            )
            subtasks.append(subtask)
            subtask_indices.append((subtask, raw_deps))

            # Add to graph
            task_graph.add_node(GraphNode(
                id=subtask.id,
                type="subtask",
                data={
                    "description": subtask.description,
                    "kind": subtask.kind,
                    "difficulty": subtask.difficulty,
                    "priority": subtask.priority.value,
                    "estimated_effort": subtask.estimated_effort,
                    "required_inputs": subtask.required_inputs,
                    "expected_outputs": subtask.expected_outputs,
                    "read_files": subtask.read_files,
                    "support_context_files": subtask.support_context_files,
                    "write_files": subtask.write_files,
                    "can_run_parallel": subtask.can_run_parallel,
                    "validation_command": subtask.validation_command,
                }
            ))

            # Add edge from parent to subtask
            task_graph.add_edge(GraphEdge(
                source_id=original_task.id,
                target_id=subtask.id,
                edge_type="has_subtask"
            ))

        # Second pass: resolve dependencies from indices to task IDs
        for subtask, raw_deps in subtask_indices:
            resolved_deps = []
            for dep in raw_deps:
                if isinstance(dep, int):
                    # Convert index to task ID
                    if 0 <= dep < len(subtasks):
                        resolved_deps.append(subtasks[dep].id)
                elif isinstance(dep, str):
                    # Already a task ID or description
                    resolved_deps.append(dep)
            subtask.dependencies = resolved_deps

        # Add dependency edges
        for subtask in subtasks:
            for dep_id in subtask.dependencies:
                # Find dependency by ID
                dep_task = next((t for t in subtasks if t.id == dep_id), None)
                if dep_task:
                    task_graph.add_edge(GraphEdge(
                        source_id=dep_task.id,
                        target_id=subtask.id,
                        edge_type="depends_on"
                    ))

        # Calculate total effort
        total_effort = sum(
            t.estimated_effort for t in subtasks
            if t.estimated_effort is not None
        )

        # Generate task graph summary
        graph_summary = self._generate_graph_summary(task_graph, subtasks)

        return TaskDecompositionResult(
            original_task=original_task,
            subtasks=subtasks,
            task_graph_summary=graph_summary,
            decomposition_rationale=decomposition.get("rationale", ""),
            estimated_total_effort=total_effort
        )

    @classmethod
    def _normalize_subtask_contract(cls, raw_subtask: Any) -> dict[str, Any]:
        if not isinstance(raw_subtask, dict):
            raise ValueError("Each decomposed subtask must be a JSON object.")
        normalized = dict(raw_subtask)
        explicit_kind = normalized.get("kind") or normalized.get("task_kind")
        legacy_type = normalized.get("type")
        raw_kind = explicit_kind if explicit_kind is not None else legacy_type
        if raw_kind is None:
            normalized["kind"] = "general"
            return normalized
        kind_key = str(raw_kind).strip().lower().replace("-", "_")
        canonical = cls._TASK_KIND_ALIASES.get(kind_key)
        if canonical is None:
            raise ValueError(f"Unsupported subtask kind: {raw_kind!r}")
        normalized["kind"] = canonical
        return normalized

    def build_task_graph(self, tasks: list[Task]) -> Graph:
        """Build a task dependency graph.

        Args:
            tasks: List of tasks

        Returns:
            Graph representing task dependencies
        """
        graph = Graph(GraphType.DIRECTED)

        # Add all tasks as nodes
        for task in tasks:
            graph.add_node(GraphNode(
                id=task.id,
                type="task",
                data={
                    "description": task.description,
                    "kind": task.kind,
                    "difficulty": task.difficulty,
                    "status": task.status.value,
                    "priority": task.priority.value,
                    "estimated_effort": task.estimated_effort,
                    "required_inputs": task.required_inputs,
                    "expected_outputs": task.expected_outputs,
                    "read_files": task.read_files,
                    "support_context_files": task.support_context_files,
                    "write_files": task.write_files,
                    "can_run_parallel": task.can_run_parallel,
                    "validation_command": task.validation_command,
                }
            ))

        # Add dependency edges
        for task in tasks:
            for dep_id in task.dependencies:
                if graph.has_node(dep_id):
                    graph.add_edge(GraphEdge(
                        source_id=dep_id,
                        target_id=task.id,
                        edge_type="blocks"
                    ))

        return graph

    def get_execution_order(self, task_graph: Graph) -> list[str]:
        """Get execution order for tasks using topological sort.

        Args:
            task_graph: Task dependency graph

        Returns:
            List of task IDs in execution order

        Raises:
            ValueError: If graph contains cycles
        """
        try:
            sorted_nodes = task_graph.topological_sort()
            return [node.id for node in sorted_nodes]
        except ValueError as e:
            raise ValueError(f"Cannot determine execution order: {e}") from e

    def get_ready_tasks(self, tasks: list[Task]) -> list[Task]:
        """Get tasks that are ready to execute.

        Args:
            tasks: List of tasks

        Returns:
            List of tasks with all dependencies completed
        """
        completed_ids = {t.id for t in tasks if t.status == TaskStatus.COMPLETED}

        ready_tasks = []
        for task in tasks:
            if task.is_ready(completed_ids):
                ready_tasks.append(task)

        return ready_tasks

    def assemble_results(self, parent_task: Task, subtasks: list[Task]) -> Any:
        """Assemble results from subtasks.

        Args:
            parent_task: Parent task
            subtasks: Completed subtasks

        Returns:
            Assembled result
        """
        # Check if all subtasks are completed
        if not all(t.status == TaskStatus.COMPLETED for t in subtasks):
            incomplete = [t for t in subtasks if t.status != TaskStatus.COMPLETED]

            # Build detailed error message
            error_details = []
            for task in incomplete:
                error_info = f"\n  - Task ID: {task.id}"
                error_info += f"\n    Description: {task.description}"
                error_info += f"\n    Status: {task.status.value}"
                if task.error:
                    error_info += f"\n    Error: {task.error}"
                if task.result:
                    error_info += f"\n    Result: {str(task.result)[:200]}"
                error_details.append(error_info)

            error_msg = f"Cannot assemble: {len(incomplete)} subtasks incomplete"
            error_msg += "\n\nIncomplete tasks:" + "".join(error_details)
            raise ValueError(error_msg)

        # Collect results
        results = {
            "parent_task_id": parent_task.id,
            "parent_description": parent_task.description,
            "subtask_results": [
                {
                    "task_id": t.id,
                    "description": t.description,
                    "result": t.result,
                    "duration": t.get_duration()
                }
                for t in subtasks
            ],
            "total_subtasks": len(subtasks),
            "successful_subtasks": len([t for t in subtasks if t.status == TaskStatus.COMPLETED])
        }

        return results

    def _estimate_complexity(self, task: Task) -> float:
        """Estimate task complexity using LLM.

        Args:
            task: Task to analyze

        Returns:
            Complexity score (0.0 to 1.0)
        """
        prompt = f"""Analyze the complexity of this task and rate it from 0.0 (trivial) to 1.0 (very complex).

Task: {task.description}

Consider:
- Number of steps required
- Technical difficulty
- Dependencies on other systems
- Potential for errors

Respond with just a number between 0.0 and 1.0."""

        try:
            request = build_context_llm_request(
                self.llm_client,
                purpose=ContextRequestPurpose.TASK_COMPLEXITY,
                messages=[LLMMessage(role="user", content=prompt)],
                temperature=0.3,
                max_tokens=10,
                timeout_seconds=15.0,
                transport_retries=0,
            )

            response = self.llm_client.complete(request)
            complexity_str = response.content.strip()

            # Parse complexity
            complexity = float(complexity_str)
            return max(0.0, min(1.0, complexity))

        except Exception:
            # Default to medium complexity if LLM fails
            return 0.5

    def _generate_decomposition(self, task: Task, context: dict[str, Any]) -> dict[str, Any]:
        """Generate task decomposition using LLM.

        Args:
            task: Task to decompose
            context: Context information

        Returns:
            Dictionary with subtasks and rationale
        """
        context_without_ingress = {
            key: value
            for key, value in (context or {}).items()
            if key not in {"session_constraints", "session_ingress_state"}
        }
        context_str = (
            "\n".join(f"- {k}: {v}" for k, v in context_without_ingress.items())
            if context_without_ingress
            else "None"
        )
        raw_constraint_state = context.get("session_constraints") if context else None
        raw_ingress = context.get("session_ingress_state") if context else None
        if raw_constraint_state is not None and not isinstance(raw_constraint_state, SessionConstraintState):
            raise TypeError("session_constraints must be a validated SessionConstraintState")
        if raw_ingress is not None and not isinstance(raw_ingress, SessionIngressState):
            raise TypeError("session_ingress_state must be a validated SessionIngressState")
        constraint_state = raw_constraint_state
        if isinstance(raw_ingress, SessionIngressState):
            if isinstance(raw_constraint_state, SessionConstraintState) and raw_ingress.session_constraints != raw_constraint_state:
                raise ValueError("session ingress and constraint state differ")
            constraint_state = raw_ingress.session_constraints

        instruction = """You decompose one task into a strict executable subtask contract.
Return JSON only with this shape:
{{
    "rationale": "Why this decomposition makes sense",
    "subtasks": [
        {{
            "description": "Subtask description",
            "kind": "inspect|implement|repair|validate|document|general",
            "read_files": [],
            "support_context_files": [],
            "write_files": [],
            "dependencies": [],
            "validation_command": ""
        }}
    ]
}}

Guidelines:
- Create 2-7 subtasks
- For simple code artifact tasks that create one script, game, or small app in
  a named file/directory, create 1-3 subtasks and prefer one implementation
  task plus one validation task.
- Each subtask should be independently executable
- Every inspect subtask must list concrete read_files when file inspection is requested.
- Every implement or repair subtask must list every permitted target in write_files.
- For implement or repair subtasks, put helpful but non-authorizing reference files in
  support_context_files instead of read_files when they are not required read-before-write evidence.
- Tasks that write the same file must depend on each other or set can_run_parallel=false
- Every validate subtask must include the exact non-empty validation_command it is required to run.
- Dependencies should be indices (0, 1, 2, etc.) of other subtasks in the list
- Keep descriptions clear and actionable
- Do not replace kind with type or task_kind."""
        prompt = f"""Decompose this task into subtasks.

Task: {task.description}

Context:
{context_str}"""

        try:
            candidates = [
                ContextCandidate(
                    candidate_id="task_decomposition:instruction",
                    kind=ContextCandidateKind.INSTRUCTION,
                    source_id="execution_task_decomposer:instruction",
                    content=instruction,
                    role="system",
                    retention=ContextCandidateRetention.REQUIRED,
                    priority=100,
                    source_order=0,
                    truncation=ContextCandidateTruncation.FORBIDDEN,
                    trust=ContextCandidateTrust.AUTHORITATIVE,
                    freshness=ContextCandidateFreshness.CURRENT,
                ),
                ContextCandidate(
                    candidate_id="task_decomposition:task",
                    kind=ContextCandidateKind.USER_INPUT,
                    source_id="execution_task_decomposer:task",
                    content=prompt,
                    role="user",
                    retention=ContextCandidateRetention.PREFERRED,
                    priority=80,
                    source_order=1,
                    truncation=ContextCandidateTruncation.HEAD,
                    trust=ContextCandidateTrust.DIRECT,
                    freshness=ContextCandidateFreshness.CURRENT,
                ),
            ]
            if isinstance(constraint_state, SessionConstraintState):
                constraint_candidate = build_session_constraint_candidate(constraint_state)
                if constraint_candidate is not None:
                    candidates.append(constraint_candidate)
            request = build_context_candidate_request(
                self.llm_client,
                candidates=candidates,
                purpose=ContextRequestPurpose.TASK_DECOMPOSITION,
                response_format="json_object",
                temperature=0.5,
                max_tokens=3200,
                timeout_seconds=45.0,
                transport_retries=0,
            )

            complete = self.llm_client.complete
            parameters = inspect.signature(complete).parameters
            accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
            kwargs = {"max_retries": 1} if accepts_kwargs or "max_retries" in parameters else {}
            response = complete(request, **kwargs)

            # Parse JSON response
            if response.parsed_json:
                return response.parsed_json

            # Fallback parsing
            import json
            return json.loads(response.content)

        except Exception as e:
            # Fallback to simple decomposition
            return self._fallback_decomposition(task)

    def _fallback_decomposition(self, task: Task) -> dict[str, Any]:
        """Generate simple fallback decomposition.

        Args:
            task: Task to decompose

        Returns:
            Simple decomposition
        """
        return {
            "rationale": "Automatic decomposition (LLM unavailable)",
            "subtasks": [
                {
                    "description": f"Analyze requirements for: {task.description}",
                    "kind": "inspect",
                    "difficulty": "simple",
                    "priority": "high",
                    "estimated_effort": 1.0,
                    "required_inputs": [],
                    "expected_outputs": ["Concrete implementation requirements"],
                    "read_files": [],
                    "write_files": [],
                    "dependencies": [],
                    "can_run_parallel": True,
                    "validation_command": "",
                    "tags": ["analysis"]
                },
                {
                    "description": f"Implement: {task.description}",
                    "kind": "implement",
                    "difficulty": "moderate",
                    "priority": "high",
                    "estimated_effort": 3.0,
                    "required_inputs": ["Concrete implementation requirements"],
                    "expected_outputs": ["Updated project files"],
                    "read_files": [],
                    "write_files": [],
                    "dependencies": [0],
                    "can_run_parallel": False,
                    "validation_command": "",
                    "tags": ["implementation"]
                },
                {
                    "description": f"Test: {task.description}",
                    "kind": "validate",
                    "difficulty": "simple",
                    "priority": "medium",
                    "estimated_effort": 1.0,
                    "required_inputs": ["Updated project files"],
                    "expected_outputs": ["Validation result"],
                    "read_files": [],
                    "write_files": [],
                    "dependencies": [1],
                    "can_run_parallel": True,
                    "validation_command": "",
                    "tags": ["testing"]
                }
            ]
        }

    def _string_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value)]

    def _is_simple_code_artifact(self, task_description: str) -> bool:
        text = task_description.lower()
        has_path = "'" in task_description or '"' in task_description or "/" in task_description
        code_keywords = (
            "app",
            "assistant",
            "cli",
            "game",
            "script",
            "service",
            "site",
            "website",
            "个人数字助手",
            "小游戏",
            "工具",
            "助手",
            "服务",
            "程序",
            "网站",
            "脚本",
            "贪吃蛇",
        )
        return has_path and any(keyword in text for keyword in code_keywords)

    def _compact_simple_code_subtasks(self, subtasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not subtasks:
            return []
        actionable = [
            dict(subtask)
            for subtask in subtasks
            if not self._is_planning_subtask(str(subtask.get("description") or ""))
        ]
        compact = (actionable or [dict(subtask) for subtask in subtasks])[:3]
        for index, subtask in enumerate(compact):
            subtask["dependencies"] = [index - 1] if index > 0 else []
        return compact

    def _is_planning_subtask(self, description: str) -> bool:
        text = description.lower()
        planning_markers = (
            "analyze requirements",
            "define",
            "design",
            "outline",
            "plan",
            "specify",
            "分析需求",
            "定义",
            "梳理",
            "确定",
            "规划",
            "设计",
        )
        implementation_markers = (
            "build",
            "create",
            "develop",
            "fix",
            "generate",
            "implement",
            "run",
            "test",
            "update",
            "validate",
            "write",
            "创建",
            "修复",
            "实现",
            "开发",
            "构建",
            "生成",
            "编写",
            "运行",
            "验证",
        )
        return self._contains_marker(text, planning_markers) and not self._contains_marker(text, implementation_markers)

    def _contains_marker(self, text: str, markers: tuple[str, ...]) -> bool:
        for marker in markers:
            if marker.isascii():
                if re.search(rf"\b{re.escape(marker)}\b", text):
                    return True
            elif marker in text:
                return True
        return False

    def _generate_graph_summary(self, graph: Graph, tasks: list[Task]) -> str:
        """Generate summary of task graph.

        Args:
            graph: Task graph
            tasks: List of tasks

        Returns:
            Summary text
        """
        lines = [
            f"Task Graph Summary:",
            f"- Total tasks: {len(tasks)}",
            f"- Total nodes: {graph.node_count()}",
            f"- Total edges: {graph.edge_count()}",
        ]

        # Count by priority
        by_priority = {}
        for task in tasks:
            priority = task.priority.value
            by_priority[priority] = by_priority.get(priority, 0) + 1

        lines.append("\nBy Priority:")
        for priority, count in sorted(by_priority.items()):
            lines.append(f"  - {priority}: {count}")

        # Identify tasks with no dependencies (can start immediately)
        ready_tasks = [t for t in tasks if not t.dependencies]
        lines.append(f"\nReady to start: {len(ready_tasks)} tasks")

        return "\n".join(lines)

    def _log_agent(
        self,
        event_type: str,
        *,
        success: bool | None,
        input_summary: Any | None = None,
        output_summary: Any | None = None,
        error: str | None = None,
    ) -> None:
        if not self.logger or not hasattr(self.logger, "log_structured_event"):
            return
        self.logger.log_structured_event(
            source_type="agent",
            source_name="autonomous_iteration.agents.execution_task_decomposer",
            phase="task_decomposition",
            event_type=event_type,
            session_id=self.session_id_getter() or "unknown",
            turn_id=1,
            success=success,
            input_summary=input_summary,
            output_summary=output_summary,
            error=error,
        )
