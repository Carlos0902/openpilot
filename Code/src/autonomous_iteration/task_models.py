"""Task models for agent system."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field

from metadata import FileReadWindowSpec, TaskResultMetadata


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    """Task priority levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


TaskKind: TypeAlias = Literal[
    "general",
    "inspect",
    "inspection",
    "analysis",
    "investigate",
    "implement",
    "repair",
    "validate",
    "validation",
    "verify",
    "test",
    "document",
    "documentation",
    "codebase_understanding",
]


class Task(BaseModel):
    """A task in the agent system."""

    id: str
    description: str
    parent_id: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    dependencies: list[str] = Field(default_factory=list)  # Task IDs that must complete first
    estimated_effort: float | None = None  # Estimated effort in arbitrary units
    actual_effort: float | None = None
    assigned_agent: str | None = None
    kind: TaskKind = "general"
    difficulty: str = "simple"
    required_inputs: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    read_files: list[str] = Field(default_factory=list)
    read_windows: list[FileReadWindowSpec] = Field(default_factory=list)
    support_context_files: list[str] = Field(default_factory=list)
    write_files: list[str] = Field(default_factory=list)
    can_run_parallel: bool = True
    validation_command: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    result: Any | None = None
    error: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    def is_ready(self, completed_task_ids: set[str]) -> bool:
        """Check if task is ready to execute.

        Args:
            completed_task_ids: Set of completed task IDs

        Returns:
            True if all dependencies are completed
        """
        if self.status != TaskStatus.PENDING:
            return False

        return all(dep_id in completed_task_ids for dep_id in self.dependencies)

    def mark_started(self, agent_id: str | None = None) -> None:
        """Mark task as started.

        Args:
            agent_id: Optional agent ID
        """
        self.status = TaskStatus.IN_PROGRESS
        self.started_at = datetime.now(timezone.utc).isoformat()
        if agent_id:
            self.assigned_agent = agent_id

    def mark_completed(self, result: Any = None) -> None:
        """Mark task as completed.

        Args:
            result: Task result
        """
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.result = result

    def mark_failed(self, error: str) -> None:
        """Mark task as failed.

        Args:
            error: Error message
        """
        self.status = TaskStatus.FAILED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.error = error

    def mark_blocked(self) -> None:
        """Mark task as blocked."""
        self.status = TaskStatus.BLOCKED

    def get_duration(self) -> float | None:
        """Get task duration in seconds.

        Returns:
            Duration in seconds or None if not completed
        """
        if not self.started_at or not self.completed_at:
            return None

        try:
            started = datetime.fromisoformat(self.started_at.replace('Z', '+00:00'))
            completed = datetime.fromisoformat(self.completed_at.replace('Z', '+00:00'))
            return (completed - started).total_seconds()
        except Exception:
            return None


class TaskDecompositionRequest(BaseModel):
    """Request for task decomposition."""

    task_description: str
    context: dict[str, Any] = Field(default_factory=dict)
    max_subtasks: int = 10
    min_subtask_complexity: float = 0.1  # Minimum complexity to decompose further


class TaskDecompositionResult(BaseModel):
    """Result of task decomposition."""

    original_task: Task
    subtasks: list[Task]
    task_graph_summary: str
    decomposition_rationale: str
    estimated_total_effort: float


class AgentCapability(str, Enum):
    """Agent capabilities."""
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    RESEARCH = "research"
    PLANNING = "planning"
    EXECUTION = "execution"
    GENERAL = "general"


class Agent(BaseModel):
    """An agent in the system."""

    id: str
    name: str
    capabilities: list[AgentCapability] = Field(default_factory=list)
    max_concurrent_tasks: int = 1
    current_tasks: list[str] = Field(default_factory=list)  # Task IDs
    completed_tasks: int = 0
    failed_tasks: int = 0
    status: str = "idle"  # idle, busy, offline
    attributes: dict[str, Any] = Field(default_factory=dict)

    def is_available(self) -> bool:
        """Check if agent is available for new tasks.

        Returns:
            True if agent can accept new tasks
        """
        return (
            self.status == "idle" and
            len(self.current_tasks) < self.max_concurrent_tasks
        )

    def can_handle(self, task: Task) -> bool:
        """Check if agent can handle a task.

        Args:
            task: Task to check

        Returns:
            True if agent has required capabilities
        """
        if not self.capabilities:
            return True  # General agent can handle anything

        # Check if task requires specific capabilities from tags or attributes.
        required_caps = task.attributes.get("required_capabilities", [])
        if not required_caps:
            return True

        return any(cap in self.capabilities for cap in required_caps)

    def assign_task(self, task_id: str) -> None:
        """Assign a task to this agent.

        Args:
            task_id: Task ID
        """
        self.current_tasks.append(task_id)
        self.status = "busy"

    def complete_task(self, task_id: str, success: bool = True) -> None:
        """Mark a task as completed.

        Args:
            task_id: Task ID
            success: Whether task succeeded
        """
        if task_id in self.current_tasks:
            self.current_tasks.remove(task_id)

        if success:
            self.completed_tasks += 1
        else:
            self.failed_tasks += 1

        if not self.current_tasks:
            self.status = "idle"


class TaskExecutionContext(BaseModel):
    """Context for task execution."""

    task: Task
    parent_context: dict[str, Any] = Field(default_factory=dict)
    shared_state: dict[str, Any] = Field(default_factory=dict)
    execution_history: list[dict[str, Any]] = Field(default_factory=list)


class TaskExecutionResult(BaseModel):
    """Result of task execution."""

    task_id: str
    status: TaskStatus
    result_metadata: TaskResultMetadata | None = None
    error: str | None = None
    duration: float | None = None
    result_summary: str | None = None
    observed_paths: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
