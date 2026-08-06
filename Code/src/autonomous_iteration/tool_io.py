"""Tool input/output helpers for execution flows."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from autonomous_iteration.planning_surface import PlanningSurfaceCatalog, PlanningSurfaceSelector
from autonomous_iteration.planning_surface import ToolCapabilityCardProvider
from metadata import CodeArtifactMetadata
from metadata import ToolInputMetadata, ToolResultMetadata, artifact_to_tool_input
from core.python_requirements import is_requirements_file
from tools.tool_selection import ToolSelection


class ExecutionToolIO:
    """Pure helpers for tool prompt formatting, chaining, and summaries."""

    def __init__(self, logger: Any | None = None, session_id_getter: Any | None = None) -> None:
        self.logger = logger
        self.session_id_getter = session_id_getter

    def sanitize_tool_metadata(self, value: Any) -> dict[str, Any]:
        params = value.to_params() if isinstance(value, ToolInputMetadata) else value
        sanitized: dict[str, Any] = {}
        for key, value in params.items():
            if key.startswith("_"):
                continue
            if key in {"content", "code", "task_description"} and isinstance(value, str):
                sanitized[key] = f"<{len(value)} chars>"
                sanitized[f"{key}_length"] = len(value)
                sanitized[f"{key}_preview"] = value[:200]
            else:
                sanitized[key] = value
        self._log_function("sanitize_tool_metadata", {"keys": list(params)}, {"keys": list(sanitized)})
        return sanitized

    def summarize_metadata_output(self, output: Any) -> dict[str, Any]:
        if isinstance(output, ToolResultMetadata):
            return self.summarize_metadata_output(output.result)
        if hasattr(output, "model_dump"):
            return self.summarize_metadata_output(output.model_dump(mode="json"))
        if not isinstance(output, dict):
            summary = {"output_type": type(output).__name__} if output is not None else {}
            self._log_function("summarize_metadata_output", {"output_type": type(output).__name__}, summary)
            return summary

        summary = {
            key: self.json_safe_summary(value)
            for key, value in output.items()
        }
        if "code" in summary and isinstance(summary["code"], str):
            summary["code_length"] = len(summary["code"])
            summary["code_preview"] = summary["code"][:200]
            summary.pop("code", None)
        if "content" in summary and isinstance(summary["content"], str):
            summary["content_length"] = len(summary["content"])
            summary["content_preview"] = summary["content"][:200]
            summary.pop("content", None)
        self._log_function("summarize_metadata_output", {"keys": list(output)}, {"keys": list(summary)})
        return summary

    def json_safe_summary(self, value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return self.json_safe_summary(value.model_dump())
        if isinstance(value, dict):
            return {key: self.json_safe_summary(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self.json_safe_summary(child) for child in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def resolve_chained_metadata(
        self,
        tool_name: str,
        input_metadata: ToolInputMetadata,
        last_output: Any,
        last_code_output: Any,
    ) -> ToolInputMetadata:
        params = input_metadata.to_params()
        if tool_name not in {
            "code_editor",
            "code_executor",
            "code_reviewer",
            "file_patch_writer",
            "file_writer",
        }:
            return input_metadata
        preferred_output = last_code_output or last_output
        if (
            tool_name == "file_writer"
            and isinstance(preferred_output, ToolResultMetadata)
            and preferred_output.tool_name != "code_generator"
        ):
            return input_metadata
        routed = artifact_to_tool_input(tool_name, preferred_output)
        routed_params = routed.to_params()
        rerouted_file_path = self._reroute_incompatible_writer_target(
            params.get("file_path"),
            routed_params,
            preferred_output,
        )
        if rerouted_file_path:
            params["file_path"] = rerouted_file_path
            try:
                if Path(rerouted_file_path).expanduser().exists():
                    params["operation_kind"] = "file_replace"
                    params["overwrite"] = True
            except OSError:
                pass
        for key, value in routed_params.items():
            current = params.get(key)
            if current in (None, "", [], {}) or self._is_generated_placeholder(current):
                params[key] = value
        resolved = ToolInputMetadata.from_mapping(tool_name, params)
        self._log_function(
            "resolve_chained_metadata",
            {"tool_name": tool_name, "input_keys": list(input_metadata.to_params())},
            {"output_keys": list(resolved.to_params())},
        )
        return resolved

    def _reroute_incompatible_writer_target(
        self,
        current_file_path: Any,
        routed_params: dict[str, Any],
        preferred_output: Any,
    ) -> str:
        if not current_file_path or not is_requirements_file(str(current_file_path)):
            return ""
        artifact = preferred_output.result if isinstance(preferred_output, ToolResultMetadata) else preferred_output
        if not isinstance(artifact, CodeArtifactMetadata):
            return ""
        if str(artifact.language or "").lower() != "python":
            return ""
        content = str(routed_params.get("content") or artifact.code or artifact.content or "")
        if not content.strip():
            return ""
        return self._suggest_python_file_path(Path(str(current_file_path)), content, artifact)

    def _suggest_python_file_path(
        self,
        rejected_path: Path,
        content: str,
        artifact: CodeArtifactMetadata,
    ) -> str:
        attrs = artifact.attributes if isinstance(artifact.attributes, dict) else {}
        for key in ("file_path", "target_file", "target_path"):
            candidate = str(attrs.get(key) or "").strip()
            if candidate and candidate.endswith(".py") and not is_requirements_file(candidate):
                return candidate

        lowered = content.lower()
        stem = "generated"
        if "assistant" in lowered:
            stem = "assistant"
        elif re.search(r"^\s*def\s+main\s*\(", content, flags=re.MULTILINE) or "__main__" in content:
            stem = "main"
        else:
            class_match = re.search(r"^\s*class\s+([A-Z][A-Za-z0-9_]*)", content, flags=re.MULTILINE)
            if class_match:
                stem = self._camel_to_snake(class_match.group(1))
        return str(rejected_path.expanduser().parent / f"{stem}.py")

    def _camel_to_snake(self, value: str) -> str:
        first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first).lower()

    def _is_generated_placeholder(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        lowered = value.lower()
        if any(
            marker in lowered
            for marker in (
                "will be replaced",
                "replace_me",
                "to_be_filled",
                "filled_by_codegeneration",
                "todo_generated",
                "code_generator_output",
                "{{code_generation.output}}",
                "{{ code_generation.output }}",
                "待填充",
                "后填充",
                "输出填充",
                "前一步输出",
                "code_generation生成",
            )
        ):
            return True
        standalone = lowered.strip().strip("#/*- ")
        return standalone in {"placeholder", "占位"}

    def extract_generated_content(self, output: Any) -> str | None:
        if output is None:
            return None
        if isinstance(output, str):
            return output
        if isinstance(output, dict):
            for key in ("code", "content", "text"):
                value = output.get(key)
                if isinstance(value, str):
                    return value
        if isinstance(output, ToolResultMetadata):
            return self.extract_generated_content(output.result)
        return None

    def format_tools_for_llm(self, tools: list[Any]) -> str:
        tool_descriptions = []
        for tool in tools:
            params_str = ""
            contract = getattr(tool, "contract_metadata", None)
            if contract:
                params = [f"  - {field} [required]" for field in contract.required_input_fields]
                required_any_of = getattr(contract, "required_any_of", []) or []
                if required_any_of:
                    readable_group = " or ".join(
                        " + ".join(str(field) for field in field_group)
                        for field_group in required_any_of
                    )
                    params.append(f"  - one of: {readable_group} [required]")
                for requirement in getattr(contract, "conditional_requirements", []) or []:
                    params.append(f"  - conditional: {requirement}")
                params.extend(f"  - {field} [default={value!r}]" for field, value in contract.input_defaults.items())
                params_str = "\n".join(params)

            tool_descriptions.append(
                f"- {tool.name}: {tool.description}\n"
                f"  Input metadata:\n{params_str if params_str else '  (none)'}"
            )

        result = "\n\n".join(tool_descriptions)
        self._log_function("format_tools_for_llm", {"tool_count": len(tools)}, {"chars": len(result)})
        return result

    def format_planning_surface(
        self,
        tools: list[Any],
        *,
        task_description: str,
        goal: str = "",
        history_text: str = "",
        retry_reason: str = "",
        signal: Any | None = None,
        plan_data: dict[str, Any] | None = None,
        capability_card_providers: list[Any] | None = None,
    ) -> str:
        providers = [ToolCapabilityCardProvider(tools), *(capability_card_providers or [])]
        catalog = PlanningSurfaceCatalog.from_providers(providers)
        selection = PlanningSurfaceSelector().select(
            catalog,
            task_description=task_description,
            goal=goal,
            history_text=history_text,
            retry_reason=retry_reason,
            signal=signal,
            plan_data=plan_data,
        )
        result = selection.render()
        self._log_function(
            "format_planning_surface",
            {
                "tool_count": len(tools),
                "task_preview": task_description[:120],
                "retry_reason": retry_reason[:120],
            },
            {
                "chars": len(result),
                "core_card_ids": [card.card_id for card in selection.core_cards],
                "deferred_card_ids": [card.card_id for card in selection.deferred_cards],
            },
        )
        return result

    def map_reason_to_enum(self, reason_text: str) -> str:
        reason_lower = reason_text.lower()
        if any(word in reason_lower for word in ["capability", "can", "able to", "supports"]):
            return "capability_match"
        if any(word in reason_lower for word in ["best", "optimal", "performance", "efficient"]):
            return "best_performance"
        if any(word in reason_lower for word in ["only", "single", "no other", "no alternative"]):
            return "only_option"
        if any(word in reason_lower for word in ["prefer", "user", "requested"]):
            return "user_preference"
        if any(word in reason_lower for word in ["fallback", "backup", "alternative"]):
            return "fallback"
        if any(word in reason_lower for word in ["cost", "cheap", "economical"]):
            return "cost_optimized"
        return "capability_match"

    def resolve_selection_metadata(
        self,
        selection: ToolSelection,
        step_outputs: dict[str, Any],
    ) -> ToolSelection:
        input_metadata = selection.input_metadata
        if selection.depends_on:
            for dependency_step_id in selection.depends_on:
                if dependency_step_id in step_outputs:
                    input_metadata = self.resolve_chained_metadata(
                        selection.tool_name,
                        input_metadata,
                        step_outputs[dependency_step_id],
                        step_outputs[dependency_step_id],
                    )

        self._log_function(
            "resolve_selection_metadata",
            {"tool_name": selection.tool_name, "depends_on": selection.depends_on},
            {"input_keys": list(input_metadata.to_params())},
        )
        return ToolSelection(
            step_id=selection.step_id,
            tool_name=selection.tool_name,
            reason=selection.reason,
            confidence=selection.confidence,
            input_metadata=input_metadata,
            requires_confirmation=selection.requires_confirmation,
            fallback_tools=selection.fallback_tools,
            depends_on=selection.depends_on,
            timeout_override=selection.timeout_override,
        )

    def _log_function(self, source_name: str, input_summary: Any, output_summary: Any) -> None:
        if not self.logger or not hasattr(self.logger, "log_structured_event"):
            return
        session_id = self.session_id_getter() if self.session_id_getter else "unknown"
        self.logger.log_structured_event(
            source_type="function",
            source_name=f"autonomous_iteration.tool_io.{source_name}",
            phase="execution_tool_io",
            event_type="function_completed",
            session_id=session_id or "unknown",
            turn_id=1,
            success=True,
            input_summary=input_summary,
            output_summary=output_summary,
        )
