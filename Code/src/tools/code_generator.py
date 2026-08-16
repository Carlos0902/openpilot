"""Code Generator Tool - Generate code using LLM based on task description."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import time
import uuid
from typing import Any, Optional

from metadata import (
    ContextCandidateTruncation,
    ContextRequestPurpose,
    ToolContractMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
    metadata_tool_result,
    EnhancementCompletionComplexity,
    EnhancementCompletionDecisionValue,
    EnhancementCompletionRequest,
    EnhancementCompletionRequirement,
    RuntimeBudgetMetadata,
    ReasoningDecisionComplexity,
)

from core.exceptions import ContextAssemblyBudgetError, InvalidLLMResponseError, OpenPilotError
from core.llm import LLMMessage, render_llm_message
from core.reasoning import reasoning_policy_for_decision
from autonomous_iteration.enhancement_completion_budget import EnhancementCompletionBudgetCoordinator
from memory.context_assembly import (
    build_context_candidate_request,
    build_context_llm_request,
)
from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
    ToolFailureMode,
)
from tools.code_models import CodeGenerationRequest, CodeLanguage, GeneratedCode
from tools.code_generation_context import build_code_generation_candidates


CODE_GENERATION_LLM_TIMEOUT_SECONDS = 90.0


CODE_GENERATOR_DEFINITION = ToolDefinition(
    name="code_generator",
    display_name="Code Generator",
    description="Generate code using LLM based on task description",
    version="1.0.0",
    capabilities=[ToolCapability.CODE_EXECUTION, ToolCapability.LLM_CALL],
    permission_level=PermissionLevel.MEDIUM,
    contract_metadata=ToolContractMetadata(
        tool_name='code_generator',
        input_metadata_type="ToolInputMetadata",
        output_metadata_type="ToolResultMetadata",
        required_input_fields=['task_description', 'language'],
        input_defaults={'context': '', 'prompt_context': {}},
    ),
    timeout_seconds=300,
    max_retries=2,
    failure_modes=[
        ToolFailureMode(
            error_type="llm_timeout",
            description="LLM request timed out",
            recovery_strategy="Retry with simpler task description"
        ),
        ToolFailureMode(
            error_type="llm_error",
            description="LLM returned error",
            recovery_strategy="Check LLM configuration and API key"
        ),
        ToolFailureMode(
            error_type="invalid_language",
            description="Unsupported programming language",
            recovery_strategy="Use python, shell, or bash"
        )
    ],
    tags=["code", "generation", "llm", "programming"],
    audit_required=True
)


@metadata_tool_result('code_generator')
def code_generator_executor(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
    params = input_metadata.to_params()
    """
    Execute code generator tool.

    Args:
        params: Tool parameters (task_description, language, context)

    Returns:
        Dictionary with code, language, explanation, imports, functions
    """
    from core.llm import LLMClient

    task_description = params["task_description"]
    language_str = params["language"].lower()
    context = params.get("context", "")
    prompt_context = params.get("prompt_context") or {}
    operation_kind = str(params.get("operation_kind") or "file_create")
    if isinstance(prompt_context, dict):
        prompt_context = {**prompt_context, "operation_kind": operation_kind}
    use_local_fallback = bool(
        isinstance(prompt_context, dict)
        and prompt_context.get("local_fallback_after_provider_failure")
    )

    # Map language string to CodeLanguage enum
    language_map = {
        "python": CodeLanguage.PYTHON,
        "shell": CodeLanguage.SHELL,
        "bash": CodeLanguage.BASH,
    }

    if language_str not in language_map:
        raise ValueError(f"Unsupported language: {language_str}. Use python, shell, or bash.")

    language = language_map[language_str]

    try:
        llm_client = params.get("_llm_client")
        if llm_client is None:
            from core.config import LLMSettings
            settings = LLMSettings()
            llm_client = LLMClient(settings)

        runtime_budget = params.get("_runtime_budget")
        enhancement_requirement = params.get("_enhancement_requirement")
        generator = CodeGenerator(
            None if use_local_fallback else llm_client,
            runtime_budget=(runtime_budget if isinstance(runtime_budget, RuntimeBudgetMetadata) else None),
            enhancement_requirement=(
                enhancement_requirement
                if isinstance(enhancement_requirement, EnhancementCompletionRequirement)
                else None
            ),
        )

        # Create request
        request = CodeGenerationRequest(
            request_id=f"req_{uuid.uuid4().hex[:8]}",
            task_description=task_description,
            language=language,
            context=context,
            prompt_context=prompt_context if isinstance(prompt_context, dict) else {},
            max_lines=260 if prompt_context else 100,
        )

        # Generate code
        result = generator.generate_code(request)

        output = {
            "code": result.code,
            "language": result.language.value,
            "explanation": f"Generated {result.line_count} lines of {result.language.value} code",
            "imports": result.imports,
            "functions": result.functions,
            "generation_mode": "local_fallback" if use_local_fallback else "llm",
        }
        if use_local_fallback:
            output["warning"] = (
                "Remote code generation remained unavailable after bounded retries; "
                "emitted a deterministic local fallback scaffold."
            )
        return output
    except OpenPilotError as exc:
        exc.context.setdefault("tool_name", "code_generator")
        raise
    except Exception as exc:
        raise RuntimeError(f"Code generation failed ({type(exc).__name__}): {exc}") from exc


class CodeGenerator:
    """代码生成器"""

    # Python 代码生成模板
    PYTHON_TEMPLATE = """你是一个专业的 Python 代码生成助手。请根据以下任务描述生成 Python 代码。

任务描述：
{task_description}

要求：
1. 生成完整、可执行的 Python 代码
2. 代码应该简洁、高效、易读
3. 包含必要的错误处理
4. 添加适当的注释说明关键逻辑
5. 遵循 PEP 8 代码规范
{constraints}

请只返回 Python 代码，不要包含任何解释文字。代码应该用 ```python 代码块包裹。
"""

    # Shell 代码生成模板
    SHELL_TEMPLATE = """你是一个专业的 Shell 脚本生成助手。请根据以下任务描述生成 Shell 脚本。

任务描述：
{task_description}

要求：
1. 生成完整、可执行的 Shell 脚本
2. 脚本应该健壮、安全
3. 包含必要的错误检查
4. 添加适当的注释
5. 使用 bash 语法
{constraints}

请只返回 Shell 代码，不要包含任何解释文字。代码应该用 ```bash 代码块包裹。
"""

    def __init__(
        self,
        llm_client: Optional[object] = None,
        *,
        runtime_budget: RuntimeBudgetMetadata | None = None,
        enhancement_requirement: EnhancementCompletionRequirement | None = None,
    ):
        """
        初始化代码生成器

        Args:
            llm_client: LLM 客户端（如果为 None，使用模拟生成）
        """
        self.llm_client = llm_client
        self.enhancement_requirement = enhancement_requirement
        self.runtime_budget = (
            runtime_budget
            if runtime_budget is not None and enhancement_requirement is not None
            else RuntimeBudgetMetadata()
        )
        self.enhancement_budget = EnhancementCompletionBudgetCoordinator(self.runtime_budget)
        self._generation_count = 0

    def generate_code(self, request: CodeGenerationRequest) -> GeneratedCode:
        """
        生成代码

        Args:
            request: 代码生成请求

        Returns:
            GeneratedCode: 生成的代码
        """
        start_time = time.time()

        # 1. 构建提示词
        prompt = self._build_prompt(request)

        # 2. 调用 LLM 生成代码
        if self.llm_client:
            raw_response = self._call_llm(prompt, generation_request=request)
        else:
            # 模拟生成（用于测试）
            raw_response = self._simulate_generation(request)

        # 3. 解析响应，提取代码
        code = self._extract_code(raw_response, request.language)

        # 4. 分析代码元信息
        line_count = len([line for line in code.split("\n") if line.strip()])
        imports = self._extract_imports(code, request.language)
        functions = self._extract_functions(code, request.language)

        # 5. 生成代码 ID
        code_id = f"code_{uuid.uuid4().hex[:8]}"

        # 6. 计算生成时间
        generation_time_ms = int((time.time() - start_time) * 1000)

        # 7. 更新统计
        self._generation_count += 1

        return GeneratedCode(
            code_id=code_id,
            request_id=request.request_id,
            language=request.language,
            code=code,
            line_count=line_count,
            imports=imports,
            functions=functions,
            model_used=self._get_model_name(),
            generation_time_ms=generation_time_ms,
            tokens_used=self._estimate_tokens(prompt, code),
        )

    def _build_prompt(self, request: CodeGenerationRequest) -> str:
        """构建提示词"""
        if request.prompt_context:
            return self._build_contextual_prompt(request)

        # 选择模板
        if request.language == CodeLanguage.PYTHON:
            template = self.PYTHON_TEMPLATE
        elif request.language in (CodeLanguage.SHELL, CodeLanguage.BASH):
            template = self.SHELL_TEMPLATE
        else:
            raise ValueError(f"不支持的语言: {request.language}")

        # 构建约束条件
        constraints = []

        if request.max_lines:
            constraints.append(f"6. 代码行数不超过 {request.max_lines} 行")

        if request.allowed_imports:
            imports_str = ", ".join(request.allowed_imports)
            constraints.append(f"7. 只能使用以下模块: {imports_str}")

        if request.forbidden_operations:
            ops_str = ", ".join(request.forbidden_operations)
            constraints.append(f"8. 禁止使用以下操作: {ops_str}")

        if request.context:
            constraints.append(f"9. 上下文信息: {request.context}")

        constraints_text = "\n".join(constraints) if constraints else ""

        # 填充模板
        prompt = template.format(
            task_description=request.task_description,
            constraints=constraints_text,
        )

        return prompt

    def _build_contextual_prompt(self, request: CodeGenerationRequest) -> str:
        """Build a tool-specific prompt around the upper-layer Prompt Context."""
        prompt_context = request.prompt_context or {}
        constraints = self._constraint_lines(request)
        context_json = json.dumps(prompt_context, ensure_ascii=False, indent=2, default=str)
        product_intent = prompt_context.get("product_intent") if isinstance(prompt_context.get("product_intent"), dict) else {}
        quality_rubric = prompt_context.get("quality_rubric") or []
        if isinstance(quality_rubric, str):
            quality_rubric = [quality_rubric]
        rubric_text = "\n".join(f"- {item}" for item in quality_rubric[:8])
        if not rubric_text:
            rubric_text = "- Satisfy the original user goal with visible, user-facing behavior."

        intent_constraints = product_intent.get("non_regression_constraints") or []
        disallowed_substitutions = product_intent.get("disallowed_substitutions") or []
        dependency_guidance = self._dependency_guidance(prompt_context)
        stack_guidance = self._stack_preset_guidance(prompt_context)
        intent_guidance = ""
        if product_intent:
            intent_guidance = (
                "\nProduct-intent invariants: preserve the requested delivery surface, runtime mode, "
                "and core capabilities while fixing the current issue. Do not substitute a different "
                "interaction model just because it is easier to run."
            )
            if intent_constraints:
                intent_guidance += "\nNon-regression constraints:\n" + "\n".join(f"- {item}" for item in intent_constraints[:6])
            if disallowed_substitutions:
                intent_guidance += "\nDisallowed substitutions:\n" + "\n".join(f"- {item}" for item in disallowed_substitutions[:6])
        return f"""You are OpenPilot's Code Generator Tool.
The parent Agent has already decided the project intent, product judgment, rubric, and iteration goal.
Preserve that upper-layer context exactly; use it as the source of truth, then apply your tool-specific code generation duties.

PROMPT CONTEXT JSON:
{context_json}

TOOL TASK:
{request.task_description}

PRODUCT QUALITY RUBRIC:
{rubric_text}
{intent_guidance}
{dependency_guidance}
{stack_guidance}

TOOL OUTPUT REQUIREMENTS:
1. Generate complete, executable {request.language.value} code only when operation_kind is file_create, directory_generate, or file_replace.
2. Do not use this tool for symbol-level edits. For add_symbol use code_unit_generator; for modify_symbol/code_patch use code_editor.
2a. Return full replacement source code only when operation_kind is explicitly file_replace/full_file_replace or the target file is new.
3. Keep existing useful behavior unless the Prompt Context explicitly asks to replace it for product fit.
3a. Preserve existing useful third-party packages from the dependency strategy. Do not replace them with stdlib-only or lower-capability substitutes unless the strategy explicitly approves the replacement.
4. Include necessary imports, entry point, and concise comments for non-obvious logic.
5. Improve the selected diagnosis candidate with observable acceptance-criteria evidence.
5a. For every user-facing feature, implement the corresponding UI controls, visible states, feedback, and navigation required by the UI iteration contract.
5b. Honor the persisted frontend/backend language and framework preset. Do not silently change architecture; a stack change requires an explicit preset revision.
5c. Direct-run startup must be user-friendly: missing API keys, tokens, credentials, or config values must not raise an unhandled traceback before the app opens. Defer validation to the feature that needs the secret, show a visible warning or endpoint-level error, and document exact shell syntax such as `export OPENAI_API_KEY="..."` with no spaces around `=`.
5d. Web servers must not hardcode a single port. Read `PORT` from the environment, fall back to a sensible default, recover from address-in-use failures by selecting an available fallback port when possible, and print the actual local URL.
6. Return only code in a fenced code block; do not include explanations outside the code block.
{constraints}
"""

    def _dependency_guidance(self, prompt_context: dict[str, Any]) -> str:
        strategy = prompt_context.get("dependency_strategy")
        if not isinstance(strategy, dict):
            diagnosis = prompt_context.get("diagnosis")
            if isinstance(diagnosis, dict):
                strategy = diagnosis.get("dependency_strategy")
        if not isinstance(strategy, dict):
            return ""
        preserve = [str(item) for item in strategy.get("preserve_packages") or [] if str(item)]
        recommended = [str(item) for item in strategy.get("recommended_packages") or [] if str(item)]
        replaceable = [str(item) for item in strategy.get("replaceable_packages") or [] if str(item)]
        rationale = [str(item) for item in strategy.get("rationale") or [] if str(item)]
        lines = ["\nDependency strategy:"]
        if preserve:
            lines.append("- Preserve useful existing packages: " + ", ".join(preserve[:8]))
        if recommended:
            lines.append("- Recommended packages are available if environment sync installs them first: " + ", ".join(recommended[:6]))
        if replaceable:
            lines.append("- Approved replaceable packages: " + ", ".join(replaceable[:6]))
        if rationale:
            lines.append("- Rationale: " + " | ".join(rationale[:4]))
        return "\n".join(lines)

    def _stack_preset_guidance(self, prompt_context: dict[str, Any]) -> str:
        preset = prompt_context.get("stack_preset")
        if not isinstance(preset, dict):
            return ""
        ui_contract = prompt_context.get("ui_iteration_contract")
        ui_contract = ui_contract if isinstance(ui_contract, dict) else {}
        lines = [
            "\nPersisted project stack preset:",
            f"- Revision: {preset.get('revision', 1)}",
            f"- Delivery surface: {preset.get('delivery_surface', 'project_native')}",
            f"- Architecture: {preset.get('architecture', 'single_runtime')}",
            f"- Frontend: {preset.get('frontend_language', 'best_fit_for_surface')} "
            f"({', '.join(str(item) for item in preset.get('frontend_frameworks') or []) or 'no framework forced'})",
            f"- Backend: {preset.get('backend_language', 'python')} "
            f"({', '.join(str(item) for item in preset.get('backend_frameworks') or []) or 'no framework forced'})",
            f"- UI strategy: {preset.get('ui_strategy', 'evaluate_user_facing_ui')}",
        ]
        if ui_contract.get("assessment_required"):
            lines.append("- Assess UI impact for this iteration even when the selected goal starts as a backend feature.")
        if ui_contract.get("implementation_required_for_user_facing_change"):
            lines.append("- UI implementation is required for user-facing behavior changes; it is not optional polish.")
        return "\n".join(lines)

    def _constraint_lines(self, request: CodeGenerationRequest) -> str:
        constraints = []

        if request.max_lines:
            constraints.append(f"- Code should be at most {request.max_lines} lines when practical.")

        if request.allowed_imports:
            imports_str = ", ".join(request.allowed_imports)
            constraints.append(f"- Allowed imports only: {imports_str}")

        if request.forbidden_operations:
            ops_str = ", ".join(request.forbidden_operations)
            constraints.append(f"- Forbidden operations: {ops_str}")

        if request.context:
            constraints.append(f"- Additional tool context: {request.context}")

        return "\n".join(constraints)

    def _call_llm(
        self,
        prompt: str,
        *,
        generation_request: CodeGenerationRequest | None = None,
    ) -> str:
        """调用 LLM"""
        # 调用实际的 LLM API。真实 provider 错误必须向上传递，交给
        # LLM/tool retry 层分类处理，避免误写入模拟代码。
        if generation_request is not None and generation_request.prompt_context:
            request = build_context_candidate_request(
                self.llm_client,
                candidates=build_code_generation_candidates(generation_request),
                purpose=ContextRequestPurpose.CODE_GENERATION,
                response_format="text",
                temperature=0.7,
                timeout_seconds=CODE_GENERATION_LLM_TIMEOUT_SECONDS,
                transport_retries=0,
            )
        else:
            request = build_context_llm_request(
                self.llm_client,
                messages=[LLMMessage(role="user", content=prompt)],
                purpose=ContextRequestPurpose.CODE_GENERATION,
                response_format="text",
                temperature=0.7,
                timeout_seconds=CODE_GENERATION_LLM_TIMEOUT_SECONDS,
                transport_retries=0,
                user_truncation=ContextCandidateTruncation.FORBIDDEN,
            )
        project_context = (
            generation_request.prompt_context.get("project_context")
            if generation_request is not None
            and isinstance(generation_request.prompt_context.get("project_context"), dict)
            else {}
        )
        written_files = project_context.get("written_files") if isinstance(project_context, dict) else []
        routine = bool(
            generation_request is not None
            and generation_request.prompt_context
            and str(generation_request.prompt_context.get("operation_kind") or "file_create")
            == "file_create"
            and str(project_context.get("target_file") or "").strip()
            and len(written_files or []) <= 1
        )
        generation_identity = json.dumps(
            (
                generation_request.model_dump(
                    mode="json", exclude={"request_id", "created_at"}
                )
                if generation_request is not None
                else {"prompt": prompt}
            ),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        reservation = self.enhancement_budget.reserve(
            EnhancementCompletionRequest(
                logical_key=(
                    "code_generation:"
                    + hashlib.sha256(generation_identity.encode("utf-8")).hexdigest()
                ),
                purpose=ContextRequestPurpose.CODE_GENERATION,
                complexity=(
                    EnhancementCompletionComplexity.ROUTINE
                    if routine
                    else EnhancementCompletionComplexity.COMPLEX
                ),
                prompt_tokens=int(getattr(request.context_selection, "final_prompt_tokens", 0) or 0),
                remaining_calls=1,
                remaining_value=EnhancementCompletionDecisionValue.HIGH,
                requirement=(
                    self.enhancement_requirement
                    or EnhancementCompletionRequirement.REQUIRED
                ),
            )
        )
        if reservation is None:
            raise ContextAssemblyBudgetError(["enhancement_completion_budget:code_generation"])
        request = request.model_copy(
            update={
                "max_tokens": reservation.max_tokens,
                "trace_info": {
                    **request.trace_info,
                    "completion_budget": {
                        "purpose": ContextRequestPurpose.CODE_GENERATION.value,
                        "reservation_id": reservation.reservation_id,
                        "reserved_tokens": reservation.max_tokens,
                        "remaining_tokens": self.runtime_budget.enhancement_completion_tokens_remaining,
                    },
                },
                "reasoning_policy": reasoning_policy_for_decision(
                    getattr(self.llm_client, "settings", None),
                    (
                        ReasoningDecisionComplexity.ROUTINE
                        if routine
                        else ReasoningDecisionComplexity.COMPLEX
                    ),
                ),
            }
        )
        if hasattr(self.llm_client, 'complete'):
            try:
                response = self.llm_client.complete(request)
            except Exception as exc:
                self.enhancement_budget.reconcile_failure(reservation, exc)
                raise
            usage = getattr(response, "usage", None)
            actual_tokens = None
            if isinstance(usage, dict):
                actual_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
            self.enhancement_budget.reconcile(
                reservation,
                actual_tokens=int(actual_tokens) if actual_tokens is not None else None,
                finish_reason=getattr(response, "finish_reason", None),
                response_empty=not bool(str(getattr(response, "content", "") or "")),
            )
            if str(getattr(response, "finish_reason", "") or "").lower() in {
                "length",
                "max_tokens",
            }:
                raise InvalidLLMResponseError(
                    "Code generation reached its completion limit; truncated source is not safe to apply.",
                    response_text=str(getattr(response, "content", "") or ""),
                    usage=usage if isinstance(usage, dict) else None,
                    finish_reason=getattr(response, "finish_reason", None),
                )
            return response.content
        elif hasattr(self.llm_client, 'generate'):
            response = self.llm_client.generate("\n\n".join(message.content for message in request.messages))
        elif hasattr(self.llm_client, 'chat'):
            response = self.llm_client.chat([render_llm_message(message) for message in request.messages])
        else:
            # 如果 LLM 客户端没有标准方法，尝试直接调用
            response = self.llm_client("\n\n".join(message.content for message in request.messages))

        # 确保返回字符串
        if isinstance(response, dict):
            response = response.get('content') or response.get('text') or str(response)
        elif not isinstance(response, str):
            response = str(response)

        return response

    def _simulate_llm_response(self, prompt: str) -> str:
        """模拟 LLM 响应（用于测试）"""
        if "Python" in prompt:
            return """```python
def process_data(data):
    \"\"\"处理数据\"\"\"
    result = []
    for item in data:
        if item > 0:
            result.append(item * 2)
    return result

if __name__ == "__main__":
    data = [1, 2, 3, -1, 4]
    result = process_data(data)
    print(f"Result: {result}")
```"""
        else:
            return """```bash
#!/bin/bash
# 处理文件

if [ -f "$1" ]; then
    echo "Processing file: $1"
    cat "$1" | wc -l
else
    echo "File not found: $1"
    exit 1
fi
```"""

    def _simulate_generation(self, request: CodeGenerationRequest) -> str:
        """模拟代码生成（用于测试）"""
        if request.language == CodeLanguage.PYTHON:
            # 根据任务描述生成简单的 Python 代码
            if "read" in request.task_description.lower():
                return """```python
def read_file(file_path):
    \"\"\"读取文件内容\"\"\"
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        return None
    except Exception as e:
        print(f"Error reading file: {e}")
        return None

if __name__ == "__main__":
    result = read_file("example.txt")
    if result:
        print(result)
```"""
            elif "write" in request.task_description.lower():
                return """```python
def write_file(file_path, content):
    \"\"\"写入文件内容\"\"\"
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"Error writing file: {e}")
        return False

if __name__ == "__main__":
    success = write_file("output.txt", "Hello, World!")
    if success:
        print("File written successfully")
```"""
            else:
                return """```python
def main():
    \"\"\"主函数\"\"\"
    print("Task completed")
    return True

if __name__ == "__main__":
    main()
```"""
        else:
            # Shell 脚本
            return """```bash
#!/bin/bash
echo "Task completed"
exit 0
```"""

    def _extract_code(self, response: str, language: CodeLanguage) -> str:
        """从响应中提取代码"""
        # 尝试提取代码块
        patterns = [
            r"```python\n(.*?)```",
            r"```bash\n(.*?)```",
            r"```shell\n(.*?)```",
            r"```\n(.*?)```",
        ]

        for pattern in patterns:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                return match.group(1).strip()

        # 如果没有代码块标记，返回整个响应
        return response.strip()

    def _extract_imports(self, code: str, language: CodeLanguage) -> list[str]:
        """提取导入的模块"""
        imports = []

        if language == CodeLanguage.PYTHON:
            try:
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.append(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imports.append(node.module)
            except SyntaxError:
                # 如果解析失败，使用正则表达式
                import_pattern = r"^(?:from\s+(\S+)\s+)?import\s+(.+)$"
                for line in code.split("\n"):
                    match = re.match(import_pattern, line.strip())
                    if match:
                        if match.group(1):
                            imports.append(match.group(1))
                        else:
                            imports.extend(
                                [m.strip() for m in match.group(2).split(",")]
                            )

        return list(set(imports))  # 去重

    def _extract_functions(self, code: str, language: CodeLanguage) -> list[str]:
        """提取定义的函数"""
        functions = []

        if language == CodeLanguage.PYTHON:
            try:
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        functions.append(node.name)
            except SyntaxError:
                # 如果解析失败，使用正则表达式
                func_pattern = r"^def\s+(\w+)\s*\("
                for line in code.split("\n"):
                    match = re.match(func_pattern, line.strip())
                    if match:
                        functions.append(match.group(1))
        elif language in (CodeLanguage.SHELL, CodeLanguage.BASH):
            # Shell 函数
            func_pattern = r"^(\w+)\s*\(\s*\)\s*\{"
            for line in code.split("\n"):
                match = re.match(func_pattern, line.strip())
                if match:
                    functions.append(match.group(1))

        return functions

    def _get_model_name(self) -> str:
        """获取模型名称"""
        if self.llm_client and hasattr(self.llm_client, "model_name"):
            return self.llm_client.model_name
        return "simulated"

    def _estimate_tokens(self, prompt: str, code: str) -> int:
        """估算使用的 token 数"""
        # 简单估算：每 4 个字符约等于 1 个 token
        total_chars = len(prompt) + len(code)
        return total_chars // 4

    def get_stats(self) -> dict:
        """获取生成统计"""
        return {
            "total_generations": self._generation_count,
            "model": self._get_model_name(),
        }
