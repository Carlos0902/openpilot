"""Enhanced command tool with risk assessment and confidence scoring."""

from __future__ import annotations

import os
import platform
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Any

from metadata import ToolContractMetadata, ToolInputMetadata, ToolResultMetadata, metadata_tool_result

from core.command_approval import CommandApprovalGate
from pydantic import BaseModel

from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
    ToolFailureMode,
)


class RiskLevel(str, Enum):
    """Command risk levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExecutionMode(str, Enum):
    """Command execution modes."""
    DRY_RUN = "dry_run"
    INTERACTIVE = "interactive"
    AUTOMATIC = "automatic"


@dataclass
class RiskAssessment:
    """Risk assessment for a command."""

    risk_level: RiskLevel
    risk_factors: list[str]
    affected_paths: list[str]
    requires_confirmation: bool
    recommendation: str


class CommandResult(BaseModel):
    """Result of command execution."""

    command: str
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration: float
    risk_assessment: dict[str, Any] | None = None


COMMAND_EXECUTOR_DEFINITION = ToolDefinition(
    name="command_executor",
    display_name="Command Executor",
    description="Execute or dry-run a shell command with risk assessment",
    version="1.0.0",
    capabilities=[ToolCapability.SHELL_EXECUTION],
    permission_level=PermissionLevel.HIGH,
    contract_metadata=ToolContractMetadata(
        tool_name='command_executor',
        input_metadata_type="ToolInputMetadata",
        output_metadata_type="ToolResultMetadata",
        required_input_fields=['command'],
        input_defaults={'mode': None, 'timeout': 30},
    ),
    timeout_seconds=60,
    max_retries=1,
    failure_modes=[
        ToolFailureMode(
            error_type="invalid_input",
            description="Command parameters are invalid",
            recovery_strategy="Provide a non-empty command and a valid execution mode",
        ),
        ToolFailureMode(
            error_type="execution_timeout",
            description="Command timed out",
            recovery_strategy="Increase timeout or run a narrower command",
        ),
        ToolFailureMode(
            error_type="execution_error",
            description="Command returned an error or could not be launched",
            recovery_strategy="Review stderr, cwd, environment variables, and command syntax",
        ),
    ],
    tags=["command", "shell", "execution", "risk"],
    audit_required=True,
)


class CommandTool:
    """Enhanced command execution with risk assessment."""

    # Destructive command patterns
    DESTRUCTIVE_PATTERNS = [
        r'\brm\b.*-rf',
        r'\brm\b.*-fr',
        r'\bformat\b',
        r'\bdd\b',
        r'\bmkfs\b',
        r'\bfdisk\b',
        r'\bparted\b',
    ]

    # High-risk command patterns
    HIGH_RISK_PATTERNS = [
        r'\brm\b',
        r'\bchmod\b',
        r'\bchown\b',
        r'\bsudo\b',
        r'\bsu\b',
        r'\bkill\b',
        r'\bpkill\b',
        r'\bshutdown\b',
        r'\breboot\b',
    ]

    # Medium-risk patterns
    MEDIUM_RISK_PATTERNS = [
        r'\bmv\b',
        r'\bcp\b',
        r'\bmkdir\b',
        r'\btouch\b',
        r'\bwrite\b',
        r'\b>\b',
        r'\b>>\b',
    ]

    def __init__(self, default_timeout: float = 30.0):
        """Initialize command tool.

        Args:
            default_timeout: Default command timeout in seconds
        """
        self.default_timeout = default_timeout
        self.system = platform.system()

    def assess_risk(self, command: str) -> RiskAssessment:
        """Assess risk of a command.

        Args:
            command: Command to assess

        Returns:
            RiskAssessment
        """
        risk_factors = []
        affected_paths = []
        risk_level = RiskLevel.LOW

        # Check for destructive patterns
        for pattern in self.DESTRUCTIVE_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                risk_factors.append(f"Destructive operation: {pattern}")
                risk_level = RiskLevel.CRITICAL

        # Check for high-risk patterns
        if risk_level != RiskLevel.CRITICAL:
            for pattern in self.HIGH_RISK_PATTERNS:
                if re.search(pattern, command, re.IGNORECASE):
                    risk_factors.append(f"High-risk operation: {pattern}")
                    risk_level = RiskLevel.HIGH

        # Check for medium-risk patterns
        if risk_level == RiskLevel.LOW:
            for pattern in self.MEDIUM_RISK_PATTERNS:
                if re.search(pattern, command, re.IGNORECASE):
                    risk_factors.append(f"File modification: {pattern}")
                    risk_level = RiskLevel.MEDIUM

        # Extract file paths
        affected_paths = self._extract_paths(command)

        # Determine if confirmation required
        requires_confirmation = risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)

        # Generate recommendation
        recommendation = self._generate_recommendation(risk_level, risk_factors)

        return RiskAssessment(
            risk_level=risk_level,
            risk_factors=risk_factors,
            affected_paths=affected_paths,
            requires_confirmation=requires_confirmation,
            recommendation=recommendation
        )

    def estimate_confidence(self, command: str) -> float:
        """Estimate confidence in command execution.

        Args:
            command: Command to estimate

        Returns:
            Confidence score (0.0 to 1.0)
        """
        confidence = 1.0

        # Check command syntax
        if not command.strip():
            return 0.0

        # Check for common command errors
        if command.count('"') % 2 != 0 or command.count("'") % 2 != 0:
            confidence *= 0.5  # Unmatched quotes

        # Check for pipe errors
        if '|' in command:
            parts = command.split('|')
            if any(not p.strip() for p in parts):
                confidence *= 0.7  # Empty pipe segment

        # Check for path existence
        paths = self._extract_paths(command)
        for path in paths:
            if not os.path.exists(path):
                confidence *= 0.8  # Path doesn't exist

        return max(0.0, min(1.0, confidence))

    def execute(
        self,
        command: str,
        mode: ExecutionMode = ExecutionMode.AUTOMATIC,
        timeout: float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None
    ) -> CommandResult:
        """Execute a command.

        Args:
            command: Command to execute
            mode: Execution mode
            timeout: Command timeout
            cwd: Working directory
            env: Environment variables

        Returns:
            CommandResult
        """
        import time

        # Assess risk
        risk_assessment = self.assess_risk(command)

        # Handle dry run
        if mode == ExecutionMode.DRY_RUN:
            return CommandResult(
                command=command,
                success=True,
                stdout=f"[DRY RUN] Would execute: {command}",
                stderr="",
                exit_code=0,
                duration=0.0,
                risk_assessment=risk_assessment.__dict__
            )

        # Handle interactive mode (would need user confirmation in real implementation)
        if mode == ExecutionMode.INTERACTIVE and risk_assessment.requires_confirmation:
            # In real implementation, prompt user here
            pass

        # Execute command
        timeout = timeout or self.default_timeout
        start_time = time.time()

        try:
            process_env = os.environ.copy()
            if env:
                process_env.update(env)
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=process_env,
            )

            duration = time.time() - start_time

            return CommandResult(
                command=command,
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                duration=duration,
                risk_assessment=risk_assessment.__dict__
            )

        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            return CommandResult(
                command=command,
                success=False,
                stdout="",
                stderr=f"Command timed out after {timeout}s",
                exit_code=-1,
                duration=duration,
                risk_assessment=risk_assessment.__dict__
            )

        except Exception as e:
            duration = time.time() - start_time
            return CommandResult(
                command=command,
                success=False,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                duration=duration,
                risk_assessment=risk_assessment.__dict__
            )

    def _extract_paths(self, command: str) -> list[str]:
        """Extract file paths from command."""
        # Simple path extraction (can be improved)
        paths = []
        tokens = command.split()

        for token in tokens:
            # Skip flags
            if token.startswith('-'):
                continue

            # Check if looks like a path
            if '/' in token or '\\' in token or '.' in token:
                # Remove quotes
                path = token.strip('"').strip("'")
                if path and not path.startswith('$'):
                    paths.append(path)

        return paths

    def _generate_recommendation(self, risk_level: RiskLevel, risk_factors: list[str]) -> str:
        """Generate recommendation based on risk."""
        if risk_level == RiskLevel.CRITICAL:
            return "CRITICAL: This command is irreversible and highly destructive. Confirm before proceeding."
        elif risk_level == RiskLevel.HIGH:
            return "HIGH RISK: This command may cause significant changes. Review carefully before executing."
        elif risk_level == RiskLevel.MEDIUM:
            return "MEDIUM RISK: This command will modify files. Ensure you have backups if needed."
        else:
            return "LOW RISK: This command is safe to execute."


@metadata_tool_result('command_executor')
def command_executor(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
    params = input_metadata.to_params()
    """Standard ToolDefinition-compatible command executor."""
    command = str(params.get("command") or "").strip()
    if not command:
        raise ValueError("Invalid input: command is required")

    mode_value = _normalize_execution_mode(str(params.get("mode") or ExecutionMode.DRY_RUN.value))
    try:
        mode = ExecutionMode(mode_value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ExecutionMode)
        raise ValueError(f"Invalid input: unsupported command execution mode: {mode_value}. Allowed modes: {allowed}") from exc

    timeout = params.get("timeout", 30)
    cwd = params.get("cwd")
    env = params.get("env")
    approval_decision = CommandApprovalGate().approve(
        command,
        cwd=str(cwd) if cwd else None,
        project_path=str(params.get("project_path") or "") or None,
        approval_callback=params.get("_command_approval_callback"),
    )
    command = approval_decision.command
    tool = CommandTool(default_timeout=float(timeout))
    result = tool.execute(
        command=command,
        mode=mode,
        timeout=float(timeout),
        cwd=str(cwd) if cwd else None,
        env={str(key): str(value) for key, value in env.items()} if isinstance(env, dict) else None,
    )
    payload = result.model_dump()
    payload["command_approval"] = approval_decision.to_json_dict()
    risk_assessment = payload.get("risk_assessment")
    if isinstance(risk_assessment, dict):
        risk_level = risk_assessment.get("risk_level")
        if isinstance(risk_level, RiskLevel):
            risk_assessment["risk_level"] = risk_level.value
    return payload


def _normalize_execution_mode(mode_value: str) -> str:
    normalized = str(mode_value or "").strip().lower()
    aliases = {
        "execute": ExecutionMode.AUTOMATIC.value,
        "exec": ExecutionMode.AUTOMATIC.value,
        "run": ExecutionMode.AUTOMATIC.value,
    }
    return aliases.get(normalized, normalized)
