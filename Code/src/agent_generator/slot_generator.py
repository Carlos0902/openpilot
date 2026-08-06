"""Slot generation for user-defined agents."""

from __future__ import annotations

import json
import re
from typing import Any

from agent_generator.models import Slot, SlotKind
from core.llm import LLMClient, LLMMessage, LLMRequest
from memory.context_assembly import build_context_llm_request
from metadata import ContextRequestPurpose
from ui.environment_guard import agent_generator_llm_error_message, is_socks_dependency_error, raise_for_missing_socksio


def generate_slots(task: str, *, llm_client: Any | None = None) -> list[Slot]:
    """Generate reusable slots from a natural-language task with an LLM."""
    cleaned_task = " ".join(task.strip().split())
    if not cleaned_task:
        raise ValueError("task must not be empty")

    raise_for_missing_socksio()
    client = llm_client or LLMClient()
    parsed = _generate_slot_payload(cleaned_task, client)
    raw_slots = parsed.get("slots")
    if not isinstance(raw_slots, list) or not raw_slots:
        raise ValueError("slot generator LLM response must include a non-empty slots array")

    slots = _coerce_slots(raw_slots)
    user_language = str(parsed.get("user_language") or "").lower()
    if _needs_language_repair(cleaned_task, user_language, slots):
        repaired = _repair_slot_language(cleaned_task, user_language or "zh", slots, client)
        repaired_slots = repaired.get("slots")
        if isinstance(repaired_slots, list) and repaired_slots:
            slots = _merge_repaired_slots(slots, repaired_slots)

    if not slots:
        raise ValueError("slot generator LLM response did not contain valid slots")
    return slots


def _generate_slot_payload(task: str, client: Any) -> dict[str, Any]:
    try:
        response = client.complete(
            build_context_llm_request(
                client,
                purpose=ContextRequestPurpose.SLOT_GENERATION,
                response_format="json_object",
                temperature=0.2,
                trace_info={"tool": "agent_generator", "task": "slot_generation"},
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "You generate dynamic slots for reusable user-defined agents. "
                            "First identify the user's language and return it as user_language. "
                            "All user-visible fields must use the same language as the user task. "
                            "The slot name must stay ASCII snake_case. If the task is Chinese, "
                            "description, value, and revision_notes must be Chinese, not Spanish, "
                            "English, or another language. Return only JSON."
                        ),
                    ),
                    LLMMessage(
                        role="user",
                        content=(
                            "Create task-specific slots for this agent-building request.\n\n"
                            f"Task: {task}\n\n"
                            "Return this JSON shape:\n"
                            "{\n"
                            '  "user_language": "zh | en | other",\n'
                            '  "slots": [\n'
                            "    {\n"
                            '      "name": "snake_case_ascii_identifier",\n'
                            '      "kind": "task | constraint | data_source | format | processing | interaction",\n'
                            '      "description": "what this slot controls, in user_language",\n'
                            '      "value": "initial value inferred from the task or null, in user_language",\n'
                            '      "required": true,\n'
                            '      "revision_notes": []\n'
                            "    }\n"
                            "  ]\n"
                            "}\n\n"
                            "Prefer precise slots that reflect the user's actual wording, "
                            "language, ambiguity, and likely follow-up choices."
                        ),
                    ),
                ],
            )
        )
    except Exception as exc:
        if is_socks_dependency_error(exc):
            raise RuntimeError(agent_generator_llm_error_message(exc)) from exc
        raise

    parsed = response.parsed_json
    if not isinstance(parsed, dict):
        raise ValueError("slot generator LLM response must be a JSON object")
    return parsed


def _repair_slot_language(task: str, user_language: str, slots: list[Slot], client: Any) -> dict[str, Any]:
    payload = {
        "task": task,
        "user_language": user_language,
        "slots": [slot.model_dump(mode="json") for slot in slots],
    }
    try:
        response = client.complete(
            build_context_llm_request(
                client,
                purpose=ContextRequestPurpose.SLOT_LANGUAGE_REPAIR,
                response_format="json_object",
                temperature=0.0,
                trace_info={"tool": "agent_generator", "task": "slot_language_repair"},
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "Repair language drift in generated slots. Only rewrite user-visible "
                            "description, value, and revision_notes to match user_language. Preserve "
                            "slot name, kind, and required exactly. Return only JSON."
                        ),
                    ),
                    LLMMessage(
                        role="user",
                        content=(
                            "Fix these slots so user-visible text matches the original task language. "
                            "If user_language is zh, all user-visible text must be Chinese.\n\n"
                            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
                        ),
                    ),
                ],
            )
        )
    except Exception as exc:
        if is_socks_dependency_error(exc):
            raise RuntimeError(agent_generator_llm_error_message(exc)) from exc
        raise
    parsed = response.parsed_json
    return parsed if isinstance(parsed, dict) else {}


def _coerce_slots(raw_slots: list[Any]) -> list[Slot]:
    slots = []
    for index, raw_slot in enumerate(raw_slots, start=1):
        if not isinstance(raw_slot, dict):
            continue
        slots.append(_coerce_slot(raw_slot, index))
    return slots


def _merge_repaired_slots(original: list[Slot], repaired_raw: list[Any]) -> list[Slot]:
    repaired = _coerce_slots(repaired_raw)
    merged = []
    for index, slot in enumerate(original):
        replacement = repaired[index] if index < len(repaired) else None
        if replacement is None:
            merged.append(slot)
            continue
        merged.append(
            Slot(
                name=slot.name,
                kind=slot.kind,
                description=replacement.description,
                value=replacement.value,
                required=slot.required,
                revision_notes=replacement.revision_notes,
            )
        )
    return merged


def _needs_language_repair(task: str, user_language: str, slots: list[Slot]) -> bool:
    if not (_contains_cjk(task) or user_language == "zh"):
        return False
    visible_parts = []
    for slot in slots:
        visible_parts.append(slot.description)
        if slot.value is not None:
            visible_parts.append(str(slot.value))
        visible_parts.extend(str(note) for note in slot.revision_notes)
    visible_text = " ".join(part for part in visible_parts if part)
    if not visible_text:
        return False
    return _cjk_ratio(visible_text) < 0.2


def _coerce_slot(raw_slot: dict[str, Any], index: int) -> Slot:
    kind = _coerce_kind(raw_slot.get("kind"))
    name = str(raw_slot.get("name") or f"slot_{index}").strip() or f"slot_{index}"
    description = str(raw_slot.get("description") or name).strip()
    revision_notes = raw_slot.get("revision_notes")
    if not isinstance(revision_notes, list):
        revision_notes = []

    return Slot(
        name=name,
        kind=kind,
        description=description,
        value=raw_slot.get("value"),
        required=bool(raw_slot.get("required", True)),
        revision_notes=[str(note) for note in revision_notes],
    )


def _coerce_kind(raw_kind: Any) -> SlotKind:
    try:
        return SlotKind(str(raw_kind))
    except ValueError:
        return SlotKind.CONSTRAINT


def _contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def _cjk_ratio(value: str) -> float:
    visible = [char for char in value if not char.isspace()]
    if not visible:
        return 0.0
    cjk = sum(1 for char in visible if _contains_cjk(char))
    return cjk / len(visible)
