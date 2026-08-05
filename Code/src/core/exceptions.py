"""
Project-level exceptions with enhanced error handling utilities.

Inspired by Claude Code's error classification and context extraction.
"""

import sys
import traceback
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class ErrorCategory(str, Enum):
    """Error categories for classification."""
    RETRYABLE = "retryable"  # Temporary errors that can be retried
    TERMINAL = "terminal"  # Permanent errors that won't succeed on retry
    NETWORK = "network"  # Network-related errors
    TIMEOUT = "timeout"  # Timeout errors
    AUTH = "auth"  # Authentication/authorization errors
    VALIDATION = "validation"  # Input validation errors
    UNKNOWN = "unknown"  # Unknown error type


class OpenPilotError(Exception):
    """Base exception for OpenPilot errors."""

    def __init__(self, message: str, category: ErrorCategory = ErrorCategory.UNKNOWN, context: Optional[Dict[str, Any]] = None):
        """
        Initialize error with category and context.

        Args:
            message: Error message
            category: Error category for classification
            context: Additional context (file paths, line numbers, etc.)
        """
        super().__init__(message)
        self.category = category
        self.context = context or {}


class MissingAPIKeyError(OpenPilotError):
    """Raised when an LLM request is attempted without an API key."""

    def __init__(self, message: str = "API key not configured"):
        super().__init__(message, category=ErrorCategory.AUTH)


class LLMTimeoutError(OpenPilotError):
    """Raised when the configured LLM provider times out."""

    def __init__(self, message: str, timeout_seconds: Optional[float] = None):
        context = {"timeout_seconds": timeout_seconds} if timeout_seconds else {}
        super().__init__(message, category=ErrorCategory.TIMEOUT, context=context)


class LLMProviderError(OpenPilotError):
    """Raised when the configured LLM provider returns an error."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        retryable: bool = False,
        category: ErrorCategory | None = None,
    ):
        if category is None:
            message_lower = message.lower()
            if "network" in message_lower or "connection" in message_lower:
                category = ErrorCategory.NETWORK
                retryable = True
            elif "timeout" in message_lower:
                category = ErrorCategory.TIMEOUT
                retryable = True
            elif "rate limit" in message_lower or "429" in message_lower:
                category = ErrorCategory.RETRYABLE
                retryable = True
            else:
                category = ErrorCategory.RETRYABLE if retryable else ErrorCategory.TERMINAL
        context = {"retryable": retryable}
        if status_code:
            context["status_code"] = status_code
        super().__init__(message, category=category, context=context)


class InvalidLLMResponseError(OpenPilotError):
    """Raised when an LLM response cannot be parsed or validated."""

    def __init__(
        self,
        message: str,
        response_text: Optional[str] = None,
        *,
        usage: Optional[Dict[str, Any]] = None,
        finish_reason: Optional[str] = None,
    ):
        self.response_text = response_text or ""
        self.usage = dict(usage or {})
        self.finish_reason = finish_reason
        context = {"response_text": response_text[:500] if response_text else None}
        super().__init__(message, category=ErrorCategory.VALIDATION, context=context)


class ContextAssemblyBudgetError(OpenPilotError):
    """Raised before transport when required model context cannot fit."""

    def __init__(self, omitted_required_candidate_ids: list[str]):
        super().__init__(
            "Required context cannot fit within the configured prompt budget",
            category=ErrorCategory.VALIDATION,
            context={"omitted_required_candidate_ids": list(omitted_required_candidate_ids)},
        )


class ContextAssemblyGovernanceError(OpenPilotError):
    """Raised before transport when required context has unresolved governance."""

    def __init__(self, governance_blocked_candidate_ids: list[str]):
        super().__init__(
            "Required context is blocked by source governance",
            category=ErrorCategory.VALIDATION,
            context={
                "governance_blocked_candidate_ids": list(
                    governance_blocked_candidate_ids
                )
            },
        )


class ContextSourceError(OpenPilotError):
    """Raised when a context source cannot be read or its checkpoint cannot be persisted."""

    def __init__(self, source: str, cause: Exception):
        super().__init__(
            f"Context source unavailable: {source}",
            category=ErrorCategory.VALIDATION,
            context={
                "source": str(source),
                "cause_type": type(cause).__name__,
                "cause_message": str(cause),
            },
        )


class NetworkError(OpenPilotError):
    """Raised for network-related errors."""

    def __init__(self, message: str, url: Optional[str] = None):
        context = {"url": url} if url else {}
        super().__init__(message, category=ErrorCategory.NETWORK, context=context)


class FileOperationError(OpenPilotError):
    """Raised for file operation errors."""

    def __init__(self, message: str, file_path: Optional[str] = None, operation: Optional[str] = None):
        context = {
            "file_path": file_path,
            "operation": operation
        }
        super().__init__(message, category=ErrorCategory.TERMINAL, context=context)


# Error classification utilities


def classify_error(error: Exception) -> ErrorCategory:
    """
    Classify error into category for retry logic.

    Args:
        error: Exception to classify

    Returns:
        Error category

    Example:
        >>> classify_error(LLMTimeoutError("timeout"))
        ErrorCategory.TIMEOUT
    """
    # Check if it's our custom error with category
    if isinstance(error, OpenPilotError):
        return error.category

    # Classify by exception type
    error_type = type(error).__name__

    # Timeout errors
    if 'timeout' in error_type.lower() or 'TimeoutError' in error_type:
        return ErrorCategory.TIMEOUT

    # Network errors
    if any(keyword in error_type.lower() for keyword in ['connection', 'network', 'socket']):
        return ErrorCategory.NETWORK

    # Auth errors
    if any(keyword in error_type.lower() for keyword in ['auth', 'permission', 'forbidden', 'unauthorized']):
        return ErrorCategory.AUTH

    # Check error message for hints
    error_msg = str(error).lower()

    if 'timeout' in error_msg:
        return ErrorCategory.TIMEOUT

    if any(keyword in error_msg for keyword in ['connection', 'network', 'unreachable']):
        return ErrorCategory.NETWORK

    if any(keyword in error_msg for keyword in ['rate limit', 'too many requests', '429']):
        return ErrorCategory.RETRYABLE

    if any(keyword in error_msg for keyword in ['401', '403', 'unauthorized', 'forbidden']):
        return ErrorCategory.AUTH

    return ErrorCategory.UNKNOWN


def is_retryable_error(error: Exception) -> bool:
    """
    Determine if error is retryable.

    Args:
        error: Exception to check

    Returns:
        True if error should be retried

    Example:
        >>> is_retryable_error(LLMTimeoutError("timeout"))
        True
        >>> is_retryable_error(MissingAPIKeyError())
        False
    """
    category = classify_error(error)

    # Retryable categories
    retryable_categories = {
        ErrorCategory.RETRYABLE,
        ErrorCategory.TIMEOUT,
        ErrorCategory.NETWORK
    }

    return category in retryable_categories


def extract_error_context(error: Exception) -> Dict[str, Any]:
    """
    Extract context information from error.

    Args:
        error: Exception to extract context from

    Returns:
        Dictionary with error context

    Example:
        >>> extract_error_context(FileOperationError("failed", file_path="/tmp/test.txt"))
        {'type': 'FileOperationError', 'message': 'failed', 'file_path': '/tmp/test.txt', ...}
    """
    context = {
        'type': type(error).__name__,
        'message': str(error),
        'category': classify_error(error).value
    }

    # Add custom context if available
    if isinstance(error, OpenPilotError) and error.context:
        context.update(error.context)

    # Extract errno information if available
    if hasattr(error, 'errno'):
        context['errno'] = error.errno

    if hasattr(error, 'filename'):
        context['filename'] = error.filename

    # Extract stack trace
    tb = traceback.extract_tb(error.__traceback__) if error.__traceback__ else []
    if tb:
        context['traceback'] = [
            {
                'file': frame.filename,
                'line': frame.lineno,
                'function': frame.name
            }
            for frame in tb
        ]

    return context


def short_error_stack(error: Exception, max_frames: int = 3) -> List[str]:
    """
    Get shortened stack trace for context-efficient error reporting.

    Args:
        error: Exception to get stack from
        max_frames: Maximum number of frames to include

    Returns:
        List of formatted stack frames

    Example:
        >>> short_error_stack(ValueError("test"), max_frames=2)
        ['  File "test.py", line 10, in function_name', ...]
    """
    if not error.__traceback__:
        return []

    tb = traceback.extract_tb(error.__traceback__)

    # Take last N frames (most relevant)
    frames = tb[-max_frames:] if len(tb) > max_frames else tb

    return [
        f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}'
        for frame in frames
    ]


def format_error_for_display(error: Exception, include_stack: bool = True) -> str:
    """
    Format error for user-friendly display.

    Args:
        error: Exception to format
        include_stack: Include stack trace

    Returns:
        Formatted error message

    Example:
        >>> format_error_for_display(ValueError("Invalid input"))
        'ValueError: Invalid input'
    """
    lines = [f"{type(error).__name__}: {str(error)}"]

    # Add category
    category = classify_error(error)
    if category != ErrorCategory.UNKNOWN:
        lines.append(f"Category: {category.value}")

    # Add context
    if isinstance(error, OpenPilotError) and error.context:
        lines.append("Context:")
        for key, value in error.context.items():
            if value is not None:
                lines.append(f"  {key}: {value}")

    # Add stack trace
    if include_stack:
        stack = short_error_stack(error, max_frames=3)
        if stack:
            lines.append("Stack trace:")
            lines.extend(stack)

    return '\n'.join(lines)


def is_file_not_found_error(error: Exception) -> bool:
    """
    Check if error is a file not found error.

    Args:
        error: Exception to check

    Returns:
        True if file not found error
    """
    if isinstance(error, FileNotFoundError):
        return True

    if hasattr(error, 'errno') and error.errno == 2:  # ENOENT
        return True

    return 'no such file' in str(error).lower()


def is_permission_error(error: Exception) -> bool:
    """
    Check if error is a permission error.

    Args:
        error: Exception to check

    Returns:
        True if permission error
    """
    if isinstance(error, PermissionError):
        return True

    if hasattr(error, 'errno') and error.errno in (1, 13):  # EPERM, EACCES
        return True

    return any(keyword in str(error).lower() for keyword in ['permission denied', 'access denied'])
