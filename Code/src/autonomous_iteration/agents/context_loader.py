"""Context Loader agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.exceptions import (
    ContextAssemblyBudgetError,
    ContextAssemblyGovernanceError,
    ContextSourceError,
)
from memory.context_builder import MemoryContextBuilder
from metadata import SessionConstraintState, SessionIngressState


DEFAULT_AUTONOMOUS_ITERATION_SYSTEM_PROMPT = (
    "You are OpenPilot's Autonomous Iteration Context Loader. Build a complete, "
    "project-wise context for the iteration pipeline. Preserve the user's original "
    "intent, prefer module-owned agents and standard tools, and include compressed "
    "dialog history, related project files, related memories, and virtual environment "
    "information before any goal, task design, decomposition, or execution step."
)


class ContextLoaderAgent:
    """Load related context from the Memory module."""

    def __init__(
        self,
        memory_context_builder: Any | None = None,
        *,
        system_prompt: str | None = None,
    ) -> None:
        self.memory_context_builder = memory_context_builder
        self.system_prompt = (
            DEFAULT_AUTONOMOUS_ITERATION_SYSTEM_PROMPT
            if system_prompt is None
            else system_prompt
        )

    def run(
        self,
        goal: str,
        project_path: str | Path,
        iteration: int = 0,
        *,
        session_constraints: SessionConstraintState | None = None,
        session_ingress_state: SessionIngressState | None = None,
    ) -> dict[str, Any]:
        if session_ingress_state is not None:
            try:
                canonical_project_root = Path(project_path).expanduser().resolve(strict=False)
                ingress_project_root = Path(
                    session_ingress_state.identity.project_root
                ).expanduser().resolve(strict=False)
            except OSError as exc:
                raise ValueError("session ingress project identity cannot be canonicalized") from exc
            if ingress_project_root != canonical_project_root:
                raise ValueError("session ingress project identity mismatch")
            if (
                session_constraints is not None
                and session_constraints.canonical_hash
                != session_ingress_state.session_constraints.canonical_hash
            ):
                raise ValueError("session ingress and explicit constraints differ")
            if session_ingress_state.session_constraints.project_root:
                try:
                    constraint_project_root = Path(
                        session_ingress_state.session_constraints.project_root
                    ).expanduser().resolve(strict=False)
                except OSError as exc:
                    raise ValueError("session constraint project identity cannot be canonicalized") from exc
                if constraint_project_root != canonical_project_root:
                    raise ValueError("session constraint project identity mismatch")
        builder = self.memory_context_builder or MemoryContextBuilder()
        build_kwargs: dict[str, Any] = {
            "project_path": project_path,
            "include_environment": True,
            "limit": 10,
            "system_prompt": self.system_prompt,
        }
        if session_constraints is not None:
            build_kwargs["session_constraints"] = session_constraints
        if session_ingress_state is not None:
            build_kwargs["session_ingress_state"] = session_ingress_state
        # Context loading is a read-only projection boundary.  Index/sketch
        # refresh is an explicit project-management side effect and must not
        # happen as a hidden part of prompt assembly.  Strict source handling
        # also prevents a partial context from being reported as success.
        build_kwargs["project_index_mode"] = "read_only"
        build_kwargs["strict_sources"] = True
        try:
            return builder.build(
                f"{goal} autonomous iteration {iteration}",
                **build_kwargs,
            )
        except (
            ContextAssemblyBudgetError,
            ContextAssemblyGovernanceError,
            ContextSourceError,
        ):
            raise
        except Exception as exc:
            raise ContextSourceError("context_loader", exc) from exc
