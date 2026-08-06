"""Context compression module for OpenPilot.

This module provides Claude Code-style context compression to manage token budgets.
Inspired by Claude Code's compaction system.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

from core.llm import LLMClient, LLMMessage, LLMRequest
from memory.context_assembly import build_context_llm_request
from memory.short_memory import Message
from metadata import ContextRequestPurpose


@dataclass
class CompressionResult:
    """Result of context compression."""

    compressed_messages: list[Message]
    compression_summary: str
    original_token_count: int
    compressed_token_count: int
    compression_ratio: float
    preserved_message_count: int


class ContextCompressor:
    """Compresses conversation context to manage token budget."""

    def __init__(
        self,
        llm_client: LLMClient,
        compression_threshold: int = 150000,
        min_preserved_messages: int = 10,
        target_compression_ratio: float = 0.3
    ):
        """Initialize context compressor.

        Args:
            llm_client: LLM client for generating summaries
            compression_threshold: Token count threshold to trigger compression
            min_preserved_messages: Minimum number of recent messages to preserve
            target_compression_ratio: Target ratio of compressed to original tokens
        """
        warnings.warn(
            "ContextCompressor is legacy; use artifact-backed context compaction",
            DeprecationWarning,
            stacklevel=2,
        )
        self.llm_client = llm_client
        self.compression_threshold = compression_threshold
        self.min_preserved_messages = min_preserved_messages
        self.target_compression_ratio = target_compression_ratio

    def should_compress(self, messages: list[Message]) -> bool:
        """Check if compression is needed.

        Args:
            messages: List of messages

        Returns:
            True if compression should be performed
        """
        token_count = self.estimate_tokens(messages)
        return token_count > self.compression_threshold

    def estimate_tokens(self, messages: list[Message]) -> int:
        """Estimate token count for messages.

        Args:
            messages: List of messages

        Returns:
            Estimated token count
        """
        # Rough estimation: ~4 characters per token
        total_chars = sum(len(msg.content) for msg in messages)
        return total_chars // 4

    def compress(self, messages: list[Message]) -> CompressionResult:
        """Compress context messages.

        Args:
            messages: List of messages to compress

        Returns:
            CompressionResult with compressed messages
        """
        if len(messages) <= self.min_preserved_messages:
            # Not enough messages to compress
            return CompressionResult(
                compressed_messages=messages,
                compression_summary="No compression needed (too few messages)",
                original_token_count=self.estimate_tokens(messages),
                compressed_token_count=self.estimate_tokens(messages),
                compression_ratio=1.0,
                preserved_message_count=len(messages)
            )

        original_token_count = self.estimate_tokens(messages)

        # Split messages into compressible and preserved
        preserved_count = max(self.min_preserved_messages, int(len(messages) * 0.2))
        messages_to_compress = messages[:-preserved_count]
        preserved_messages = messages[-preserved_count:]

        # Generate compression summary
        compression_summary = self._generate_summary(messages_to_compress)

        # Create compressed context
        compressed_messages = [
            Message(
                role="system",
                content=f"[COMPRESSION BOUNDARY]\n\nPrevious conversation summary:\n{compression_summary}",
                attributes={"compression": True, "original_message_count": len(messages_to_compress)}
            )
        ] + preserved_messages

        compressed_token_count = self.estimate_tokens(compressed_messages)
        compression_ratio = compressed_token_count / original_token_count if original_token_count > 0 else 1.0

        return CompressionResult(
            compressed_messages=compressed_messages,
            compression_summary=compression_summary,
            original_token_count=original_token_count,
            compressed_token_count=compressed_token_count,
            compression_ratio=compression_ratio,
            preserved_message_count=len(preserved_messages)
        )

    def _generate_summary(self, messages: list[Message]) -> str:
        """Generate summary of messages using LLM.

        Args:
            messages: Messages to summarize

        Returns:
            Summary text
        """
        # Build conversation text
        conversation_text = []
        for msg in messages:
            conversation_text.append(f"{msg.role.upper()}: {msg.content}")

        conversation = "\n\n".join(conversation_text)

        # Create summarization prompt
        prompt = f"""Summarize the following conversation, preserving key information:
- Important decisions and agreements
- Technical details and specifications
- User preferences and requirements
- Context needed for future conversation

Conversation:
{conversation}

Provide a concise summary (aim for {int(len(conversation) * self.target_compression_ratio)} characters):"""

        # Generate summary
        try:
            request = build_context_llm_request(
                self.llm_client,
                purpose=ContextRequestPurpose.MEMORY_COMPRESSION,
                messages=[
                    LLMMessage(role="user", content=prompt)
                ],
                temperature=0.3,
                max_tokens=2000
            )

            response = self.llm_client.complete(request)
            return response.content

        except Exception as e:
            # Fallback to simple truncation if LLM fails
            return self._fallback_summary(messages)

    def _fallback_summary(self, messages: list[Message]) -> str:
        """Generate fallback summary without LLM.

        Args:
            messages: Messages to summarize

        Returns:
            Simple summary text
        """
        summary_lines = [
            f"Compressed {len(messages)} messages from earlier in the conversation.",
            f"Message types: {self._count_message_types(messages)}",
        ]

        # Extract key phrases (simple heuristic)
        key_phrases = []
        for msg in messages:
            # Look for sentences with important keywords
            if any(keyword in msg.content.lower() for keyword in [
                "important", "note", "remember", "must", "should", "requirement"
            ]):
                # Take first sentence
                sentences = msg.content.split(".")
                if sentences:
                    key_phrases.append(sentences[0].strip())

        if key_phrases:
            summary_lines.append("\nKey points:")
            for phrase in key_phrases[:5]:
                summary_lines.append(f"- {phrase}")

        return "\n".join(summary_lines)

    def _count_message_types(self, messages: list[Message]) -> str:
        """Count message types.

        Args:
            messages: List of messages

        Returns:
            String describing message type counts
        """
        counts: dict[str, int] = {}
        for msg in messages:
            counts[msg.role] = counts.get(msg.role, 0) + 1

        parts = [f"{count} {role}" for role, count in sorted(counts.items())]
        return ", ".join(parts)

    def compress_with_preservation(
        self,
        messages: list[Message],
        preserve_patterns: list[str] | None = None
    ) -> CompressionResult:
        """Compress context while preserving messages matching patterns.

        Args:
            messages: List of messages
            preserve_patterns: Patterns to preserve (e.g., ["git", "error", "important"])

        Returns:
            CompressionResult
        """
        if preserve_patterns is None:
            return self.compress(messages)

        # Separate messages to preserve
        preserved_messages = []
        compressible_messages = []

        for msg in messages:
            if any(pattern.lower() in msg.content.lower() for pattern in preserve_patterns):
                preserved_messages.append(msg)
            else:
                compressible_messages.append(msg)

        # Compress only compressible messages
        if not compressible_messages:
            return CompressionResult(
                compressed_messages=messages,
                compression_summary="All messages preserved by patterns",
                original_token_count=self.estimate_tokens(messages),
                compressed_token_count=self.estimate_tokens(messages),
                compression_ratio=1.0,
                preserved_message_count=len(messages)
            )

        # Generate summary for compressible messages
        compression_summary = self._generate_summary(compressible_messages)

        # Combine compressed summary with preserved messages
        compressed_messages = [
            Message(
                role="system",
                content=f"[COMPRESSION BOUNDARY]\n\nCompressed conversation summary:\n{compression_summary}",
                attributes={"compression": True, "original_message_count": len(compressible_messages)}
            )
        ] + preserved_messages

        original_token_count = self.estimate_tokens(messages)
        compressed_token_count = self.estimate_tokens(compressed_messages)
        compression_ratio = compressed_token_count / original_token_count if original_token_count > 0 else 1.0

        return CompressionResult(
            compressed_messages=compressed_messages,
            compression_summary=compression_summary,
            original_token_count=original_token_count,
            compressed_token_count=compressed_token_count,
            compression_ratio=compression_ratio,
            preserved_message_count=len(preserved_messages)
        )

    def get_compression_stats(self, messages: list[Message]) -> dict[str, Any]:
        """Get compression statistics without actually compressing.

        Args:
            messages: List of messages

        Returns:
            Dictionary with compression statistics
        """
        token_count = self.estimate_tokens(messages)
        would_compress = self.should_compress(messages)

        preserved_count = max(self.min_preserved_messages, int(len(messages) * 0.2))
        compressible_count = len(messages) - preserved_count

        return {
            "total_messages": len(messages),
            "estimated_tokens": token_count,
            "compression_threshold": self.compression_threshold,
            "would_compress": would_compress,
            "compressible_messages": compressible_count,
            "preserved_messages": preserved_count,
            "estimated_compression_ratio": self.target_compression_ratio
        }
