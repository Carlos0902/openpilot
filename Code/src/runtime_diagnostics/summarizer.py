"""Stage summaries and human-readable reports for runtime diagnostics."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class RepeatedSignalSummary(BaseModel):
    fingerprint: str
    category: str
    source: str
    tool_name: str = ""
    message: str
    count: int
    task_ids: list[str] = Field(default_factory=list)
    severities: list[str] = Field(default_factory=list)


class TaskSummary(BaseModel):
    task_id: str
    issue_count: int
    categories: dict[str, int] = Field(default_factory=dict)
    severities: dict[str, int] = Field(default_factory=dict)


class DiagnosticSummary(BaseModel):
    total_records: int = 0
    suspicious_success_count: int = 0
    by_category: dict[str, int] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)
    by_tool: dict[str, int] = Field(default_factory=dict)
    by_task: dict[str, int] = Field(default_factory=dict)
    repeated_signals: list[RepeatedSignalSummary] = Field(default_factory=list)
    top_tasks: list[TaskSummary] = Field(default_factory=list)
    example_record_ids: list[str] = Field(default_factory=list)


def summarize_records(records: list[dict[str, Any]]) -> DiagnosticSummary:
    categories: Counter[str] = Counter()
    severities: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    tasks: Counter[str] = Counter()
    suspicious_success_count = 0

    pattern_counts: dict[str, dict[str, Any]] = {}
    task_buckets: dict[str, dict[str, Counter[str]]] = {}
    example_record_ids: list[str] = []

    for record in records:
        signal = record.get("signal") or {}
        judgment = record.get("judgment") or {}
        category = str(signal.get("category") or "unknown")
        severity = str(judgment.get("severity") or "unjudged")
        source = str(signal.get("source") or "unknown")
        tool = str(signal.get("tool_name") or "")
        task_id = str(record.get("task_id") or signal.get("task_id") or "none")
        message = str(signal.get("message") or "")
        record_id = str(record.get("record_id") or "")

        categories[category] += 1
        severities[severity] += 1
        sources[source] += 1
        tools[tool or "none"] += 1
        tasks[task_id] += 1
        if category == "suspicious_success":
            suspicious_success_count += 1
        if record_id:
            example_record_ids.append(record_id)

        fingerprint = signal_fingerprint(signal)
        bucket = pattern_counts.setdefault(
            fingerprint,
            {
                "fingerprint": fingerprint,
                "category": category,
                "source": source,
                "tool_name": tool,
                "message": message,
                "count": 0,
                "task_ids": set(),
                "severities": set(),
            },
        )
        bucket["count"] += 1
        if task_id and task_id != "none":
            bucket["task_ids"].add(task_id)
        if severity:
            bucket["severities"].add(severity)

        task_bucket = task_buckets.setdefault(task_id, {"categories": Counter(), "severities": Counter()})
        task_bucket["categories"][category] += 1
        task_bucket["severities"][severity] += 1

    repeated_signals = [
        RepeatedSignalSummary(
            fingerprint=item["fingerprint"],
            category=item["category"],
            source=item["source"],
            tool_name=item["tool_name"] or "",
            message=item["message"],
            count=item["count"],
            task_ids=sorted(item["task_ids"]),
            severities=sorted(item["severities"]),
        )
        for item in pattern_counts.values()
        if item["count"] >= 2
    ]
    repeated_signals.sort(key=lambda item: (-item.count, item.category, item.message))

    top_tasks = [
        TaskSummary(
            task_id=task_id,
            issue_count=tasks[task_id],
            categories=dict(bucket["categories"]),
            severities=dict(bucket["severities"]),
        )
        for task_id, bucket in task_buckets.items()
        if task_id != "none"
    ]
    top_tasks.sort(key=lambda item: (-item.issue_count, item.task_id))

    return DiagnosticSummary(
        total_records=len(records),
        suspicious_success_count=suspicious_success_count,
        by_category=dict(categories),
        by_severity=dict(severities),
        by_source=dict(sources),
        by_tool=dict(tools),
        by_task=dict(tasks),
        repeated_signals=repeated_signals[:10],
        top_tasks=top_tasks[:10],
        example_record_ids=example_record_ids[:10],
    )


def signal_fingerprint(signal: dict[str, Any]) -> str:
    category = str(signal.get("category") or "unknown")
    source = str(signal.get("source") or "unknown")
    tool_name = str(signal.get("tool_name") or "")
    message = str(signal.get("message") or "").strip()
    message_head = " ".join(message.split())[:120]
    return f"{category}|{source}|{tool_name}|{message_head}"


def render_summary_markdown(summary: DiagnosticSummary) -> str:
    lines: list[str] = []
    lines.append("# Runtime Diagnostics Summary")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- Total records: {summary.total_records}")
    lines.append(f"- Suspicious success count: {summary.suspicious_success_count}")
    lines.append("")

    lines.append("## By Category")
    lines.append("")
    for name, count in sorted(summary.by_category.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {name}: {count}")
    lines.append("")

    lines.append("## By Severity")
    lines.append("")
    for name, count in sorted(summary.by_severity.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {name}: {count}")
    lines.append("")

    lines.append("## Repeated Signals")
    lines.append("")
    if summary.repeated_signals:
        for item in summary.repeated_signals:
            lines.append(f"- [{item.category}] x{item.count} — {item.message}")
            if item.tool_name:
                lines.append(f"  - Tool: {item.tool_name}")
            if item.task_ids:
                lines.append(f"  - Tasks: {', '.join(item.task_ids)}")
            if item.severities:
                lines.append(f"  - Severities: {', '.join(item.severities)}")
    else:
        lines.append("- No repeated signals yet.")
    lines.append("")

    lines.append("## Top Tasks")
    lines.append("")
    if summary.top_tasks:
        for item in summary.top_tasks:
            lines.append(f"- {item.task_id}: {item.issue_count} issue(s)")
            if item.categories:
                formatted = ", ".join(f"{name}={count}" for name, count in sorted(item.categories.items()))
                lines.append(f"  - Categories: {formatted}")
            if item.severities:
                formatted = ", ".join(f"{name}={count}" for name, count in sorted(item.severities.items()))
                lines.append(f"  - Severities: {formatted}")
    else:
        lines.append("- No task-level issue groups yet.")
    lines.append("")

    if summary.example_record_ids:
        lines.append("## Example Record IDs")
        lines.append("")
        for record_id in summary.example_record_ids:
            lines.append(f"- {record_id}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_summary_markdown(summary: DiagnosticSummary, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_summary_markdown(summary), encoding="utf-8")
    return target


def write_summary_json(summary: DiagnosticSummary, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary.model_dump(mode="python"), ensure_ascii=False, indent=2), encoding="utf-8")
    return target
