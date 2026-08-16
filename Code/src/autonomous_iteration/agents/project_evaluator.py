"""Project-level hard validator for iterative improvement."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import signal
import shlex
import socket
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from autonomous_iteration.improvement_context import ImprovementContextHelper
from autonomous_iteration.models import EvaluationResult
from core.llm import LLMMessage, render_llm_message
from memory.context_assembly import build_context_llm_request
from metadata import (
    ContextCandidateTruncation,
    ContextRequestPurpose,
    ProductIntentMetadata,
    ValidationIssueMetadata,
    WarningCheckResultMetadata,
    WarningItemMetadata,
)
from tools.terminal_smoke import (
    looks_like_terminal_python_source,
    run_terminal_command,
)
from utils.json_utils import safe_parse_json


class ProjectEvaluatorAgent:
    """Validate whether a generated project can run without blocking bugs."""

    def __init__(
        self,
        llm_client: Any | None = None,
        smoke_timeout_seconds: int = 2,
        import_smoke_timeout_seconds: int | None = None,
        logger: Any | None = None,
        session_id_getter: Callable[[], str | None] | None = None,
    ):
        self.llm_client = llm_client
        self.smoke_timeout_seconds = smoke_timeout_seconds
        self.import_smoke_timeout_seconds = import_smoke_timeout_seconds or max(8, smoke_timeout_seconds * 4)
        self.logger = logger
        self.session_id_getter = session_id_getter or (lambda: None)

    def evaluate_project(
        self,
        *,
        goal: str,
        project_path: str | Path,
        written_files: list[str],
        run_command: str = "",
        readme_path: str | Path | None = None,
        static_review: dict[str, Any] | None = None,
        iteration: int = 0,
    ) -> EvaluationResult:
        self._log_agent(
            "project_evaluation_started",
            input_summary={"goal": goal, "project_path": str(project_path), "iteration": iteration},
            success=None,
        )
        try:
            result = self._evaluate_project_impl(
                goal=goal,
                project_path=project_path,
                written_files=written_files,
                run_command=run_command,
                readme_path=readme_path,
                static_review=static_review,
                iteration=iteration,
            )
        except Exception as exc:
            self._log_agent(
                "project_evaluation_failed",
                input_summary={"goal": goal, "project_path": str(project_path), "iteration": iteration},
                success=False,
                error=str(exc),
            )
            raise
        self._log_agent(
            "project_evaluation_completed",
            input_summary={"goal": goal, "project_path": str(project_path), "iteration": iteration},
            output_summary={
                "validation_passed": result.validation_passed,
                "errors": len(result.validation_errors),
                "warnings": len(result.warnings),
            },
            success=result.validation_passed,
            level=self._validation_log_level(result),
        )
        self._log_agent(
            "project_validation_completed",
            input_summary={"goal": goal, "project_path": str(project_path), "iteration": iteration},
            output_summary=self._validation_log_summary(result),
            success=result.validation_passed,
            error=None if result.validation_passed else self._validation_issue_summary(result),
            level=self._validation_log_level(result),
        )
        return result

    def _evaluate_project_impl(
        self,
        *,
        goal: str,
        project_path: str | Path,
        written_files: list[str],
        run_command: str = "",
        readme_path: str | Path | None = None,
        static_review: dict[str, Any] | None = None,
        iteration: int = 0,
    ) -> EvaluationResult:
        """Run deterministic hard validation."""
        project = Path(project_path).expanduser()
        readme = Path(readme_path).expanduser() if readme_path else project / "README.md"
        static_review = static_review or {}
        readme_text = self._read_text(readme)
        effective_run_command = (run_command or self._extract_run_command(readme_text)).strip()
        files = self._augment_files_with_run_command_context(
            project,
            [Path(path).expanduser() for path in written_files],
            effective_run_command,
        )
        product_intent = ImprovementContextHelper().infer_product_intent(
            original_goal=goal,
            project_path=project,
            written_files=written_files,
        )

        errors: list[str] = []
        warnings: list[str] = []
        validation_issues: list[ValidationIssueMetadata] = []
        warning_check_result: WarningCheckResultMetadata | None = None
        opportunities: list[str] = []
        actions: list[str] = []

        existing_files = [path for path in files if path.exists()]
        if not existing_files:
            errors.append("No generated project files were found.")
            actions.append("Regenerate the missing project files in the requested directory.")
            validation_issues.append(
                self._issue(
                    category="environment",
                    message=errors[-1],
                    recommended_action=actions[-1],
                    product_intent=product_intent,
                )
            )
            return self._result(
                errors=errors,
                warnings=warnings,
                product_intent=product_intent,
                validation_issues=validation_issues,
                warning_check_result=warning_check_result,
                run_command=run_command,
                opportunities=opportunities,
                actions=actions,
                goal=goal,
                summary="Project validation failed: no generated files were found.",
            )

        code_text = "\n\n".join(self._read_text(path) for path in existing_files if path.suffix == ".py")
        product_intent = ImprovementContextHelper().infer_product_intent(
            original_goal=goal,
            project_path=project,
            written_files=[str(path) for path in existing_files],
            current_code=code_text,
        )
        target_files = [str(path) for path in existing_files]

        for path in existing_files:
            if path.suffix != ".py":
                continue
            try:
                ast.parse(self._read_text(path))
            except SyntaxError as exc:
                errors.append(f"Syntax error in {path.name} at line {exc.lineno}: {exc.msg}")
                actions.append(f"Fix the syntax error in {path.name}.")
                validation_issues.append(
                    self._issue(
                        category="runtime_error",
                        message=errors[-1],
                        recommended_action=actions[-1],
                        product_intent=product_intent,
                        target_files=[str(path)],
                    )
                )

        for path in existing_files:
            if not self._is_text_project_file(path):
                continue
            file_text = self._read_text(path)
            for finding in self._generated_placeholder_findings(path, file_text):
                errors.append(f"Generated content in {path.name} still contains template placeholders.")
                actions.append(f"Replace placeholder content in {path.name} with real implementation.")
                validation_issues.append(
                    self._issue(
                        category="code_quality",
                        message=errors[-1],
                        recommended_action=actions[-1],
                        product_intent=product_intent,
                        target_files=[str(path)],
                        evidence_spans=[finding],
                        syntax_context=str(finding.get("syntax_context") or ""),
                        issue_fingerprint=self._issue_fingerprint(
                            "generated_placeholder",
                            path,
                            str(finding.get("text") or ""),
                        ),
                        recommended_repair_kind="replace_generated_placeholder",
                        stale_artifact_candidate=self._is_stale_artifact_candidate(
                            path=path,
                            project_path=project,
                            all_files=existing_files,
                            run_command=effective_run_command,
                            readme_text=readme_text,
                        ),
                    )
                )

        if not effective_run_command:
            errors.append("README does not clearly explain how to run the project.")
            actions.append("Add a concrete run command to README.md.")
            validation_issues.append(
                self._issue(
                    category="environment",
                    message=errors[-1],
                    recommended_action=actions[-1],
                    product_intent=product_intent,
                )
            )

        for mismatch in self._runtime_contract_mismatches(readme_text, code_text):
            errors.append(mismatch)
            action = "Align the implementation runtime with the documented project contract, or update the docs if the runtime intentionally changed."
            actions.append(action)
            validation_issues.append(
                self._issue(
                    category="product_intent_drift",
                    message=mismatch,
                    recommended_action=action,
                    product_intent=product_intent,
                    target_files=target_files,
                )
            )

        if not static_review and code_text.strip():
            static_review = self._review_python_code(code_text)

        review_errors = self._blocking_review_errors(static_review)
        if review_errors:
            errors.extend(review_errors)
            actions.extend(static_review.get("suggestions") or [])
            for review_error in review_errors:
                validation_issues.append(
                    self._issue(
                        category="product_intent_drift" if "product-fit" in review_error.lower() else "code_quality",
                        message=review_error,
                        recommended_action=(static_review.get("suggestions") or ["Fix the blocking code review issue."])[0],
                        product_intent=product_intent,
                        target_files=[str(path) for path in existing_files],
                    )
                )

        direct_startup_issues: list[ValidationIssueMetadata] = []
        if effective_run_command and not any(error.lower().startswith("syntax error") for error in errors):
            direct_startup_issues = self._direct_startup_validation_issues(
                project=project,
                run_command=effective_run_command,
                files=existing_files,
                code_text=code_text,
                product_intent=product_intent,
            )
            for issue in direct_startup_issues:
                errors.append(issue.message)
                if issue.recommended_action:
                    actions.append(issue.recommended_action)
                validation_issues.append(issue)

        if (
            effective_run_command
            and not direct_startup_issues
            and not any(error.lower().startswith("syntax error") for error in errors)
        ):
            smoke = self._smoke_test(project, effective_run_command, existing_files)
            warning_check_result = smoke.get("warning_check_result") or warning_check_result
            if not smoke["passed"]:
                errors.append(smoke["message"])
                smoke_category = str(smoke.get("category") or "")
                if warning_check_result and warning_check_result.requires_fix and not smoke_category:
                    actions.append("Fix the runtime warning reported by the smoke test.")
                    validation_issues.append(
                        self._issue(
                            category="runtime_warning",
                            message=smoke["message"],
                            recommended_action=actions[-1],
                            product_intent=product_intent,
                            target_files=self._target_files_from_smoke_message(
                                project,
                                smoke["message"],
                                effective_run_command,
                                existing_files,
                            ),
                        )
                    )
                else:
                    actions.append(smoke.get("recommended_action") or "Fix the runtime error reported by the smoke test.")
                    validation_issues.append(
                        self._issue(
                            category=smoke_category or "runtime_error",
                            message=smoke["message"],
                            recommended_action=actions[-1],
                            product_intent=product_intent,
                            target_files=self._target_files_from_smoke_message(
                                project,
                                smoke["message"],
                                effective_run_command,
                                existing_files,
                            ),
                            recommended_repair_kind=str(smoke.get("recommended_repair_kind") or ""),
                        )
                    )
            elif smoke["warning"]:
                warnings.append(smoke["message"])
                if warning_check_result:
                    validation_issues.append(
                        self._issue(
                            category="runtime_warning",
                            severity="warning",
                            message=smoke["message"],
                            recommended_action=warning_check_result.recommended_fix,
                            product_intent=product_intent,
                            target_files=self._target_files_from_smoke_message(
                                project,
                                smoke["message"],
                                effective_run_command,
                                existing_files,
                            ),
                        )
                    )

        if warnings:
            opportunities.extend(warnings[:3])

        summary = (
            "Project validation passed."
            if not errors
            else f"Project validation failed with {len(errors)} blocking issue(s)."
        )
        return self._result(
            errors=errors,
            warnings=warnings,
            product_intent=product_intent,
            validation_issues=validation_issues,
            warning_check_result=warning_check_result,
            run_command=effective_run_command,
            opportunities=opportunities,
            actions=actions,
            goal=goal,
            summary=summary,
        )

    def _result(
        self,
        *,
        errors: list[str],
        warnings: list[str],
        product_intent: ProductIntentMetadata | None = None,
        validation_issues: list[ValidationIssueMetadata] | None = None,
        warning_check_result: WarningCheckResultMetadata | None = None,
        run_command: str,
        opportunities: list[str],
        actions: list[str],
        goal: str,
        summary: str,
    ) -> EvaluationResult:
        deduped_errors = self._dedupe(errors)
        deduped_warnings = self._dedupe(warnings)
        deduped_actions = self._dedupe(actions)[:5]
        deduped_opportunities = self._dedupe(opportunities)
        validation_passed = not deduped_errors
        return EvaluationResult(
            validation_passed=validation_passed,
            runnable=validation_passed,
            has_blocking_bugs=bool(deduped_errors),
            summary=summary,
            validation_errors=deduped_errors,
            warnings=deduped_warnings,
            product_intent=product_intent,
            validation_issues=validation_issues or [],
            warning_check_result=warning_check_result,
            run_command=run_command,
            improvement_opportunities=deduped_opportunities,
            recommended_actions=deduped_actions,
            next_iteration_goal=self._build_next_iteration_goal(goal, deduped_actions, deduped_errors),
        )

    def _validation_log_level(self, result: EvaluationResult) -> str:
        if result.validation_errors:
            return "ERROR"
        if result.warnings or any(issue.severity == "warning" for issue in result.validation_issues):
            return "WARNING"
        return "INFO"

    def _validation_log_summary(self, result: EvaluationResult) -> dict[str, Any]:
        issues = self._validation_issue_payloads(result)
        return {
            "summary": result.summary,
            "validation_passed": result.validation_passed,
            "blocking_issue_count": len(result.validation_errors),
            "warning_count": len(result.warnings),
            "validation_errors": result.validation_errors,
            "validation_issues": issues,
            "target_files": self._target_files_from_issue_payloads(issues),
            "recommended_actions": result.recommended_actions,
            "run_command": result.run_command,
            "failure_summary": self._validation_issue_summary(result) if not result.validation_passed else "",
        }

    def _validation_issue_payloads(self, result: EvaluationResult) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for issue in result.validation_issues:
            payload = issue.to_json_dict() if hasattr(issue, "to_json_dict") else issue.model_dump(mode="json")
            payloads.append(
                {
                    "category": payload.get("category"),
                    "severity": payload.get("severity"),
                    "message": payload.get("message"),
                    "recommended_action": payload.get("recommended_action"),
                    "target_files": payload.get("target_files") or [],
                    "issue_fingerprint": payload.get("issue_fingerprint"),
                    "recommended_repair_kind": payload.get("recommended_repair_kind"),
                }
            )
        return payloads

    def _target_files_from_issue_payloads(self, issues: list[dict[str, Any]]) -> list[str]:
        targets: list[str] = []
        for issue in issues:
            targets.extend(str(path) for path in issue.get("target_files") or [] if str(path))
        return self._dedupe(targets)

    def _validation_issue_summary(self, result: EvaluationResult) -> str:
        message = ""
        target_files: list[str] = []
        action = ""
        if result.validation_issues:
            issue = result.validation_issues[0]
            message = issue.message
            target_files = issue.target_files
            action = issue.recommended_action
        elif result.validation_errors:
            message = result.validation_errors[0]
        else:
            message = result.summary
        details = [message]
        if target_files:
            details.append("target=" + ", ".join(Path(path).name for path in target_files[:3]))
        if action:
            details.append("action=" + action)
        return " | ".join(part for part in details if part)

    def _issue(
        self,
        *,
        category: str,
        message: str,
        recommended_action: str,
        product_intent: ProductIntentMetadata | None,
        severity: str = "blocking",
        target_files: list[str] | None = None,
        evidence_spans: list[dict[str, Any]] | None = None,
        syntax_context: str = "",
        issue_fingerprint: str = "",
        recommended_repair_kind: str = "",
        closure_status: str = "open",
        stale_artifact_candidate: bool = False,
    ) -> ValidationIssueMetadata:
        return ValidationIssueMetadata(
            category=category,
            severity=severity,
            message=message,
            recommended_action=recommended_action,
            target_files=target_files or [],
            evidence_spans=evidence_spans or [],
            syntax_context=syntax_context,
            issue_fingerprint=issue_fingerprint
            or self._issue_fingerprint(category, None, message),
            recommended_repair_kind=recommended_repair_kind,
            closure_status=closure_status,
            stale_artifact_candidate=stale_artifact_candidate,
            product_intent=product_intent,
            preserves_product_intent=category != "product_intent_drift",
        )

    def _generated_placeholder_findings(self, path: Path, text: str) -> list[dict[str, Any]]:
        """Return only generation leftovers, not legitimate template syntax."""
        findings: list[dict[str, Any]] = []
        if not text:
            return findings
        string_lines = self._python_string_literal_lines(text) if path.suffix == ".py" else set()
        patterns = [
            (r"\{\{\s*([A-Za-z0-9_.-]*(?:code[_-]?generator|generated|output|placeholder|replace[_-]?me|todo)[A-Za-z0-9_.-]*)\s*\}\}", "brace_generated_placeholder"),
            (r"\b__(?:PLACEHOLDER|REPLACE_ME|TODO_GENERATED|CODE_GENERATOR_OUTPUT)__\b", "sentinel_generated_placeholder"),
            (r"<(?:REPLACE_ME|PLACEHOLDER|CODE_GENERATOR_OUTPUT)>", "angle_generated_placeholder"),
        ]
        for line_no, line in enumerate(text.splitlines(), 1):
            for pattern, context in patterns:
                for match in re.finditer(pattern, line, flags=re.IGNORECASE):
                    snippet = match.group(0).strip()
                    findings.append(
                        {
                            "file_path": str(path),
                            "line": line_no,
                            "column": match.start() + 1,
                            "text": snippet,
                            "syntax_context": self._placeholder_syntax_context(
                                path=path,
                                line=line,
                                line_no=line_no,
                                context=context,
                                in_python_string=line_no in string_lines,
                            ),
                        }
                    )
        return findings

    def _python_string_literal_lines(self, source: str) -> set[int]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return set()
        lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                start = getattr(node, "lineno", None)
                end = getattr(node, "end_lineno", None) or start
                if start:
                    lines.update(range(start, end + 1))
        return lines

    def _placeholder_syntax_context(
        self,
        *,
        path: Path,
        line: str,
        line_no: int,
        context: str,
        in_python_string: bool,
    ) -> str:
        suffix = path.suffix.lower()
        lowered = line.lower()
        if in_python_string and any(marker in lowered for marker in ("<div", "{%", "</", "<html", "render_template")):
            return f"python_string_template:{context}"
        if suffix in {".html", ".jinja", ".jinja2", ".vue", ".hbs", ".handlebars"}:
            return f"template_file:{context}"
        if line.lstrip().startswith("#"):
            return f"comment:{context}"
        if suffix in {".md", ".markdown", ".txt"}:
            return f"text_document:{context}"
        if suffix == ".py" and in_python_string:
            return f"python_string:{context}"
        return f"source:{context}"

    def _issue_fingerprint(self, category: str, path: Path | None, evidence: str) -> str:
        path_key = path.name if path is not None else ""
        digest = hashlib.sha1(f"{category}:{path_key}:{evidence.strip().lower()}".encode("utf-8")).hexdigest()[:12]
        return f"{category}:{path_key}:{digest}" if path_key else f"{category}:{digest}"

    def _is_stale_artifact_candidate(
        self,
        *,
        path: Path,
        project_path: Path,
        all_files: list[Path],
        run_command: str,
        readme_text: str,
    ) -> bool:
        try:
            relative_path = str(path.relative_to(project_path))
        except ValueError:
            relative_path = path.name
        if path.name in readme_text or relative_path in readme_text:
            return False
        try:
            args = shlex.split(run_command)
        except ValueError:
            args = []
        entry = self._entry_module_from_args(project_path, args, all_files) if args else None
        if entry is not None and entry.resolve() == path.resolve():
            return False
        if path.suffix == ".py" and self._python_file_is_imported(path, project_path, all_files):
            return False
        return True

    def _python_file_is_imported(self, path: Path, project_path: Path, all_files: list[Path]) -> bool:
        module = path.relative_to(project_path).with_suffix("").as_posix().replace("/", ".")
        module_root = module.split(".", 1)[0]
        for candidate in all_files:
            if candidate == path or candidate.suffix != ".py":
                continue
            try:
                tree = ast.parse(self._read_text(candidate))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported = alias.name
                        if imported == module or imported == module_root:
                            return True
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module == module or node.module == module_root:
                        return True
        return False

    def _smoke_test(self, project_path: Path, run_command: str, files: list[Path] | None = None) -> dict[str, Any]:
        try:
            args = shlex.split(run_command)
        except ValueError as exc:
            return {"passed": False, "warning": False, "message": f"Run command cannot be parsed: {exc}"}

        if not args:
            return {"passed": False, "warning": False, "message": "Run command is empty."}

        args = self._normalize_python_args(project_path, args)
        terminal_risks = self._terminal_static_risks(files or [])

        if self._looks_terminal_interactive_python_project(files or [], args):
            import_result = self._import_only_smoke_test(project_path, args, files or [])
            if not import_result["passed"]:
                return import_result
            terminal_result = self._terminal_smoke_test(project_path, args, terminal_risks)
            if not terminal_result["passed"]:
                return terminal_result
            if terminal_result["warning"]:
                return terminal_result
            if terminal_risks:
                return {
                    "passed": True,
                    "warning": True,
                    "message": "Terminal smoke passed, but static curses review found risk: " + "; ".join(terminal_risks[:2]),
                }
            return terminal_result

        if self._looks_interactive_python_project(files or [], args):
            import_result = self._import_only_smoke_test(project_path, args, files or [])
            if not import_result["passed"]:
                return import_result
            if import_result["warning"]:
                return import_result
            return {
                "passed": True,
                "warning": True,
                "message": "Smoke test skipped full run: interactive terminal or GUI program requires a real terminal/window.",
            }

        try:
            result = subprocess.run(
                args,
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=self.smoke_timeout_seconds,
                env=self._smoke_env(project_path),
            )
        except FileNotFoundError as exc:
            return {"passed": False, "warning": False, "message": f"Run command failed: {exc}"}
        except subprocess.TimeoutExpired as exc:
            combined = f"{exc.stdout or ''}\n{exc.stderr or ''}"
            if self._looks_like_traceback(combined):
                return {"passed": False, "warning": False, "message": self._short_error("Smoke test timed out with error output", combined)}
            warning_check = self._assess_runtime_warnings(
                command=run_command,
                cwd=project_path,
                stdout=str(exc.stdout or ""),
                stderr=str(exc.stderr or ""),
            )
            if warning_check and warning_check.requires_fix:
                return self._warning_fix_failure(warning_check)
            system_check = self._assess_runtime_system_output(
                command=run_command,
                cwd=project_path,
                stdout=str(exc.stdout or ""),
                stderr=str(exc.stderr or ""),
                returncode=None,
            )
            if system_check and system_check.requires_fix:
                return self._system_output_fix_failure(system_check)
            return {
                "passed": True,
                "warning": True,
                "message": f"Smoke test timed out after {self.smoke_timeout_seconds}s; treating as runnable for an interactive app.",
                "warning_check_result": warning_check,
            }

        combined_output = f"{result.stdout or ''}\n{result.stderr or ''}"
        warning_check = self._assess_runtime_warnings(
            command=run_command,
            cwd=project_path,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )
        if warning_check and warning_check.requires_fix and result.returncode == 0 and not self._looks_like_traceback(combined_output):
            return self._warning_fix_failure(warning_check)
        system_check = self._assess_runtime_system_output(
            command=run_command,
            cwd=project_path,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            returncode=result.returncode,
        )
        if system_check and system_check.requires_fix:
            return self._system_output_fix_failure(system_check)
        if self._is_interactive_environment_error(combined_output):
            return {
                "passed": True,
                "warning": True,
                "message": "Smoke test skipped full run: interactive terminal program requires a real terminal.",
                "warning_check_result": warning_check,
            }
        if result.returncode != 0:
            return {
                "passed": False,
                "warning": False,
                "message": self._short_error(f"Smoke test exited with code {result.returncode}", combined_output),
            }
        if self._looks_like_traceback(combined_output):
            return {"passed": False, "warning": False, "message": self._short_error("Smoke test printed a traceback", combined_output)}
        if warning_check and (warning_check.warnings or warning_check.ignored_warnings):
            message = warning_check.reason or "Smoke test emitted runtime warnings."
            return {"passed": True, "warning": True, "message": message, "warning_check_result": warning_check}
        return {"passed": True, "warning": False, "message": "Smoke test passed."}

    def _terminal_smoke_test(self, project_path: Path, args: list[str], static_risks: list[str]) -> dict[str, Any]:
        result = run_terminal_command(
            args,
            cwd=project_path,
            env=self._smoke_env(project_path),
            timeout=max(float(self.smoke_timeout_seconds), 2.0),
            shell=False,
        )
        warning_check = self._assess_runtime_warnings(
            command=result.command,
            cwd=project_path,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        if warning_check and warning_check.requires_fix:
            return self._warning_fix_failure(warning_check)
        if result.skipped:
            message = result.skip_reason or "Terminal smoke skipped because PTY support is unavailable."
            if static_risks:
                message += " Static curses risk: " + "; ".join(static_risks[:2])
            return {"passed": True, "warning": True, "message": message, "warning_check_result": warning_check}
        if not result.success:
            message = self._short_error("Terminal smoke test failed", result.stderr or result.stdout)
            if static_risks:
                message += " Static curses risk: " + "; ".join(static_risks[:2])
            return {
                "passed": False,
                "warning": False,
                "message": message,
                "recommended_action": "Fix the terminal runtime failure by checking terminal size and preventing curses addstr/addch drawing outside the screen.",
                "warning_check_result": warning_check,
            }
        if result.timed_out:
            return {
                "passed": True,
                "warning": True,
                "message": "Terminal smoke ran in a PTY without traceback before timeout; treating long-running interactive loop as runnable.",
                "warning_check_result": warning_check,
            }
        if warning_check and (warning_check.warnings or warning_check.ignored_warnings):
            return {
                "passed": True,
                "warning": True,
                "message": warning_check.reason or "Terminal smoke emitted runtime warnings.",
                "warning_check_result": warning_check,
            }
        return {"passed": True, "warning": False, "message": "Terminal smoke test passed.", "warning_check_result": warning_check}

    def _looks_terminal_interactive_python_project(self, files: list[Path], args: list[str]) -> bool:
        if not args or not self._is_python_executable(args[0]):
            return False
        source = "\n".join(self._read_text(path) for path in files if path.suffix == ".py")
        return looks_like_terminal_python_source(source)

    def _looks_interactive_python_project(self, files: list[Path], args: list[str]) -> bool:
        if not args or not self._is_python_executable(args[0]):
            return False
        source = "\n".join(self._read_text(path) for path in files if path.suffix == ".py")
        if not source:
            return False
        interactive_imports = ("curses", "tkinter", "turtle", "pygame")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return any(f"import {name}" in source or f"from {name}" in source for name in interactive_imports)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        return bool(imports.intersection(interactive_imports))

    def _terminal_static_risks(self, files: list[Path]) -> list[str]:
        risks: list[str] = []
        for path in files:
            if path.suffix != ".py":
                continue
            source = self._read_text(path)
            lowered = source.lower()
            if not looks_like_terminal_python_source(source):
                continue
            has_draw_calls = bool(re.search(r"\.(addstr|addch|addnstr)\s*\(", source))
            has_bounds_guard = any(marker in lowered for marker in ("getmaxyx", "terminal too small", "curses.error"))
            if has_draw_calls and not has_bounds_guard:
                risks.append(
                    f"{path.name} uses curses draw calls without visible terminal-size bounds checks or curses error handling."
                )
            if "sys.stdout.isatty" in lowered and "sys.exit(0)" in lowered:
                risks.append(
                    f"{path.name} exits successfully in non-TTY mode, so non-interactive smoke tests cannot prove terminal runtime correctness."
                )
        return self._dedupe(risks)

    def _direct_startup_validation_issues(
        self,
        *,
        project: Path,
        run_command: str,
        files: list[Path],
        code_text: str,
        product_intent: ProductIntentMetadata | None,
    ) -> list[ValidationIssueMetadata]:
        """Catch problems a user hits by copying README's run command directly."""
        issues: list[ValidationIssueMetadata] = []
        secret_issue = self._required_secret_startup_issue(
            project=project,
            run_command=run_command,
            files=files,
            code_text=code_text,
            product_intent=product_intent,
        )
        if secret_issue is not None:
            issues.append(secret_issue)
            return issues
        port_issue = self._port_conflict_startup_issue(
            project=project,
            run_command=run_command,
            files=files,
            code_text=code_text,
            product_intent=product_intent,
        )
        if port_issue is not None:
            issues.append(port_issue)
            return issues
        runtime_issue = self._runtime_startup_failure_issue(
            project=project,
            run_command=run_command,
            files=files,
            code_text=code_text,
            product_intent=product_intent,
        )
        if runtime_issue is not None:
            issues.append(runtime_issue)
        return issues

    def _runtime_startup_failure_issue(
        self,
        *,
        project: Path,
        run_command: str,
        files: list[Path],
        code_text: str,
        product_intent: ProductIntentMetadata | None,
    ) -> ValidationIssueMetadata | None:
        del code_text
        try:
            args = self._normalize_python_args(project, shlex.split(run_command))
        except ValueError:
            return None
        if not args:
            return None
        if self._looks_terminal_interactive_python_project(files, args) or self._looks_interactive_python_project(files, args):
            return None
        secret_names = self._secret_env_names_in_source("\n".join(self._read_text(path) for path in files if path.suffix == ".py"))
        try:
            result = self._run_startup_command(
                args,
                project=project,
                timeout=max(1, self.smoke_timeout_seconds),
                env=self._smoke_env_with_placeholder_secrets(project, secret_names),
            )
        except FileNotFoundError:
            return None
        output = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}"
        if result.get("returncode") == 0 or result.get("timed_out"):
            return None
        if self._output_indicates_port_in_use(output) or self._output_indicates_missing_secret(output, secret_names):
            return None
        if not self._output_indicates_startup_runtime_failure(output):
            return None

        target_files = self._target_files_from_runtime_failure_output(project, output, args, files)
        observed = self._short_error("Observed startup error", output)
        message = (
            "Direct startup failed with a Python import/runtime contract error before the app became usable. "
            f"{observed}"
        )
        action = (
            "Align the documented entry point with the actual local module API: update imports, exported function names, "
            "or call sites so the run command starts without import/runtime failure."
        )
        return self._issue(
            category="runtime_error",
            message=message,
            recommended_action=action,
            product_intent=product_intent,
            target_files=target_files,
            evidence_spans=self._runtime_failure_evidence_spans(project, output, target_files),
            syntax_context="direct_startup_runtime_failure",
            issue_fingerprint=self._issue_fingerprint("direct_startup_runtime_failure", None, output),
            recommended_repair_kind="fix_startup_import_contract",
        )

    def _required_secret_startup_issue(
        self,
        *,
        project: Path,
        run_command: str,
        files: list[Path],
        code_text: str,
        product_intent: ProductIntentMetadata | None,
    ) -> ValidationIssueMetadata | None:
        secret_names = self._secret_env_names_in_source(code_text)
        if not secret_names:
            return None
        try:
            args = self._normalize_python_args(project, shlex.split(run_command))
        except ValueError:
            return None
        if not args:
            return None
        try:
            result = self._run_startup_command(
                args,
                project=project,
                timeout=max(1, self.smoke_timeout_seconds),
                env=self._smoke_env_without_user_secrets(project, secret_names),
            )
        except FileNotFoundError:
            return None
        if result.get("returncode") == 0:
            return None
        output = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}"
        if not self._output_indicates_missing_secret(output, secret_names):
            return None

        target_files = self._source_files_containing_terms(files, secret_names)
        if not target_files:
            entry = self._entry_module_from_args(project, args, files)
            target_files = [str(entry.resolve())] if entry is not None and entry.exists() else []
        names = ", ".join(secret_names[:4])
        if len(secret_names) > 4:
            names += ", ..."
        observed = self._short_error("Observed startup error", output)
        message = (
            "Direct run fails before the app opens when required runtime secret(s) are missing: "
            f"{names}. Startup should not raise an unhandled exception for missing user configuration. "
            f"{observed}"
        )
        action = (
            "Defer secret validation until the feature that uses it, let the app/UI start with a visible "
            "configuration warning or a clear endpoint-level error, and document exact shell syntax such as "
            '`export OPENAI_API_KEY="..."` with no spaces around `=`.'
        )
        return self._issue(
            category="configuration",
            message=message,
            recommended_action=action,
            product_intent=product_intent,
            target_files=target_files,
            evidence_spans=self._evidence_spans_for_terms(files, secret_names, "startup_secret"),
            syntax_context="direct_startup_missing_secret",
            issue_fingerprint=self._issue_fingerprint("direct_startup_missing_secret", None, output or names),
            recommended_repair_kind="defer_required_secret_validation",
        )

    def _port_conflict_startup_issue(
        self,
        *,
        project: Path,
        run_command: str,
        files: list[Path],
        code_text: str,
        product_intent: ProductIntentMetadata | None,
    ) -> ValidationIssueMetadata | None:
        if not self._looks_like_python_web_server(code_text):
            return None
        try:
            args = self._normalize_python_args(project, shlex.split(run_command))
        except ValueError:
            return None
        if not args:
            return None
        secret_names = self._secret_env_names_in_source(code_text)
        env = self._smoke_env_with_placeholder_secrets(project, secret_names)
        for candidate in self._web_startup_port_candidates(files):
            port = int(candidate.get("port") or 0)
            if port <= 0 or port > 65535:
                continue
            with self._occupied_tcp_port(port) as occupied:
                if not occupied:
                    # The user's machine already has the candidate port occupied,
                    # so the following real startup run exercises that failure path.
                    pass
                try:
                    result = self._run_startup_command(
                        args,
                        project=project,
                        timeout=max(1, self.smoke_timeout_seconds),
                        env=env,
                    )
                except FileNotFoundError:
                    return None
            output = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}"
            if not self._output_indicates_port_in_use(output):
                continue
            system_check = self._assess_runtime_system_output(
                command=run_command,
                cwd=project,
                stdout=str(result.get("stdout") or ""),
                stderr=str(result.get("stderr") or ""),
                returncode=result.get("returncode") if isinstance(result.get("returncode"), int) else None,
            )
            if not system_check or not system_check.requires_fix:
                continue
            path = Path(str(candidate.get("file_path") or ""))
            line_no = int(candidate.get("line") or 1)
            observed = self._short_error("Observed startup error", output)
            reason = system_check.reason or "Port conflict prevents the generated web app from starting."
            fix = system_check.recommended_fix or (
                "Choose an available port at startup or retry from the requested/default port, print the actual local URL, "
                "and keep PORT as an explicit override."
            )
            message = (
                f"Direct startup was executed with port {port} unavailable and failed with an address-in-use error. "
                f"{reason} "
                f"{observed}"
            )
            action = (
                f"{fix} If startup still cannot bind, show a concise command such as "
                "`PORT=<free-port> .venv/bin/python app.py` based on the real run command."
            )
            return self._issue(
                category="environment",
                message=message,
                recommended_action=action,
                product_intent=product_intent,
                target_files=[str(path.resolve())] if path.exists() else [],
                evidence_spans=[self._line_evidence_span(path, line_no, "startup_port_conflict")] if path.exists() else [],
                syntax_context=f"{candidate.get('framework', 'web')}_port_conflict_probe",
                issue_fingerprint=self._issue_fingerprint("port_conflict_probe", path if path.exists() else None, output or str(port)),
                recommended_repair_kind="make_web_port_configurable",
            )
        return None

    def _secret_env_names_in_source(self, source: str) -> list[str]:
        names: set[str] = set()
        known_names = {
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "SERPAPI_API_KEY",
            "TOGETHER_API_KEY",
            "MISTRAL_API_KEY",
            "COHERE_API_KEY",
        }
        for name in known_names:
            if name in source:
                names.add(name)
        pattern = re.compile(
            r"""['"]([A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|ACCESS_KEY)[A-Z0-9_]*)['"]"""
        )
        names.update(match.group(1) for match in pattern.finditer(source or ""))
        return sorted(names)

    def _smoke_env_without_user_secrets(self, project_path: Path, secret_names: list[str]) -> dict[str, str]:
        env = self._smoke_env(project_path)
        explicit = {name.upper() for name in secret_names}
        for key in list(env):
            if key.upper() in explicit or self._looks_like_runtime_secret_env_name(key):
                env.pop(key, None)
        return env

    def _smoke_env_with_placeholder_secrets(self, project_path: Path, secret_names: list[str]) -> dict[str, str]:
        env = self._smoke_env(project_path)
        for name in secret_names:
            upper_name = name.upper()
            if upper_name.endswith("API_KEY"):
                env[name] = "sk-openpilot-startup-probe"
            else:
                env[name] = "openpilot-startup-probe"
        return env

    def _looks_like_runtime_secret_env_name(self, name: str) -> bool:
        return bool(re.search(r"(API[_-]?KEY|TOKEN|SECRET|PASSWORD|ACCESS[_-]?KEY)", name or "", re.IGNORECASE))

    def _output_indicates_missing_secret(self, output: str, secret_names: list[str]) -> bool:
        lower = self._strip_ansi(output).lower()
        if not lower:
            return False
        has_secret_context = any(name.lower() in lower for name in secret_names) or any(
            marker in lower for marker in ("api key", "token", "secret", "credential")
        )
        has_missing_context = any(
            marker in lower
            for marker in (
                "not found",
                "not set",
                "missing",
                "required",
                "not configured",
                "environment variable",
                "set ",
            )
        )
        return has_secret_context and has_missing_context

    def _output_indicates_port_in_use(self, output: str) -> bool:
        lower = self._strip_ansi(output).lower()
        return any(
            marker in lower
            for marker in (
                "address already in use",
                "port is in use",
                "eaddrinuse",
                "errno 48",
                "errno 98",
                "only one usage of each socket address",
            )
        ) or bool(re.search(r"\bport\s+\d+\s+is\s+in\s+use\b", lower))

    def _output_indicates_startup_runtime_failure(self, output: str) -> bool:
        lower = self._strip_ansi(output).lower()
        return any(
            marker in lower
            for marker in (
                "traceback",
                "fatal: cannot import",
                "cannot import name",
                "importerror",
                "modulenotfounderror",
                "no module named",
                "syntaxerror",
                "nameerror",
                "runtimeerror",
                "attributeerror",
                "typeerror",
                "valueerror",
            )
        )

    def _looks_like_runtime_system_message(self, output: str) -> bool:
        lower = self._strip_ansi(output).lower()
        return any(
            marker in lower
            for marker in (
                "address already in use",
                "port is in use",
                "eaddrinuse",
                "airplay receiver",
                "system settings",
                "on macos",
                "no available video device",
                "cannot open display",
                "not a tty",
                "inappropriate ioctl",
                "permission denied",
            )
        ) or bool(re.search(r"\bport\s+\d+\s+is\s+in\s+use\b", lower))

    def _assess_runtime_system_output(
        self,
        *,
        command: str,
        cwd: Path,
        stdout: str,
        stderr: str,
        returncode: int | None,
    ) -> WarningCheckResultMetadata | None:
        combined = f"{stdout}\n{stderr}".strip()
        if not combined or not self._looks_like_runtime_system_message(combined):
            return None
        decision = self._llm_runtime_system_output_decision(
            command=command,
            cwd=cwd,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
        )
        if decision is None:
            decision = self._fallback_runtime_system_output_decision(
                command=command,
                cwd=cwd,
                stdout=stdout,
                stderr=stderr,
                returncode=returncode,
            )
        if decision is None:
            return None

        requires_fix = bool(decision.get("requires_fix"))
        category = str(decision.get("category") or "runtime_system_output")
        reason = str(decision.get("reason") or "")
        recommended_fix = str(decision.get("recommended_fix") or "")
        severity = "fix_required" if requires_fix else "info"
        item = WarningItemMetadata(
            warning_text=self._short_error("Runtime system output", combined),
            warning_source="runtime_system_output",
            category=category,
            severity=severity,
            affects_user_experience=requires_fix,
            requires_fix=requires_fix,
            reason=reason,
        )
        return WarningCheckResultMetadata(
            command=command,
            cwd=str(cwd),
            warnings=[item] if requires_fix else [],
            ignored_warnings=[] if requires_fix else [item],
            requires_fix=requires_fix,
            reason=reason,
            recommended_fix=recommended_fix,
        )

    def _llm_runtime_system_output_decision(
        self,
        *,
        command: str,
        cwd: Path,
        stdout: str,
        stderr: str,
        returncode: int | None,
    ) -> dict[str, Any] | None:
        if self.llm_client is None:
            return None
        prompt = (
            "Decide whether this generated project's runtime system output is a bug that OpenPilot should repair.\n"
            "Return JSON only with keys: requires_fix (boolean), category (short snake_case), "
            "reason (short), recommended_fix (short imperative), recommended_repair_kind (short snake_case).\n"
            "Treat output that prevents a normal user from starting or using the generated app as requires_fix=true. "
            "Treat harmless platform notices as requires_fix=false.\n\n"
            f"Command: {command}\n"
            f"Working directory: {cwd}\n"
            f"Exit code: {returncode}\n"
            f"STDOUT:\n{stdout[-2000:]}\n\n"
            f"STDERR:\n{stderr[-2000:]}\n"
        )
        try:
            raw = self._call_llm_text(prompt)
        except Exception:
            return None
        payload = safe_parse_json(raw, default=None)
        if not isinstance(payload, dict):
            match = re.search(r"\{.*\}", raw or "", flags=re.DOTALL)
            payload = safe_parse_json(match.group(0), default=None) if match else None
        if not isinstance(payload, dict):
            return None
        return {
            "requires_fix": bool(payload.get("requires_fix")),
            "category": str(payload.get("category") or "runtime_system_output"),
            "reason": str(payload.get("reason") or ""),
            "recommended_fix": str(payload.get("recommended_fix") or ""),
            "recommended_repair_kind": str(payload.get("recommended_repair_kind") or ""),
        }

    def _call_llm_text(self, prompt: str) -> str:
        client = self.llm_client
        if client is None:
            return ""
        request = build_context_llm_request(
            client,
            messages=[LLMMessage(role="user", content=prompt)],
            purpose=ContextRequestPurpose.RUNTIME_OUTPUT_EVALUATION,
            response_format="json_object",
            temperature=0.0,
            max_tokens=300,
            timeout_seconds=10,
            transport_retries=0,
            user_truncation=ContextCandidateTruncation.TAIL,
        )
        if hasattr(client, "complete"):
            try:
                response = client.complete(request, max_retries=1, use_cache=False)
            except TypeError:
                response = client.complete(request)
            return str(getattr(response, "content", response))
        if hasattr(client, "generate"):
            return str(client.generate("\n\n".join(message.content for message in request.messages)))
        if hasattr(client, "chat"):
            return str(client.chat([render_llm_message(message) for message in request.messages]))
        if callable(client):
            return str(client("\n\n".join(message.content for message in request.messages)))
        return ""

    def _fallback_runtime_system_output_decision(
        self,
        *,
        command: str,
        cwd: Path,
        stdout: str,
        stderr: str,
        returncode: int | None,
    ) -> dict[str, Any] | None:
        del command, cwd
        combined = f"{stdout}\n{stderr}"
        if self._output_indicates_port_in_use(combined):
            return {
                "requires_fix": True,
                "category": "port_conflict",
                "reason": "The generated web app cannot start because its selected port is already in use.",
                "recommended_fix": (
                    "Handle occupied ports by selecting an available fallback port, printing the actual local URL, "
                    "and keeping PORT as an explicit override."
                ),
                "recommended_repair_kind": "make_web_port_configurable",
            }
        if returncode == 0 and "airplay receiver" in self._strip_ansi(combined).lower():
            return {
                "requires_fix": False,
                "category": "macos_system_hint",
                "reason": "macOS system hint does not indicate a generated-project bug by itself.",
                "recommended_fix": "",
                "recommended_repair_kind": "",
            }
        return None

    def _run_startup_command(
        self,
        args: list[str],
        *,
        project: Path,
        timeout: int | float,
        env: dict[str, str],
    ) -> dict[str, Any]:
        popen_kwargs: dict[str, Any] = {
            "cwd": project,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "env": env,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(args, **popen_kwargs)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return {
                "returncode": process.returncode,
                "stdout": stdout or "",
                "stderr": stderr or "",
                "timed_out": False,
            }
        except subprocess.TimeoutExpired:
            self._terminate_process(process)
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            return {
                "returncode": process.returncode,
                "stdout": stdout or "",
                "stderr": stderr or "",
                "timed_out": True,
            }

    def _terminate_process(self, process: subprocess.Popen[str]) -> None:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=1)
            return
        except Exception:
            pass
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except Exception:
            pass

    @contextmanager
    def _occupied_tcp_port(self, port: int):
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("0.0.0.0", port))
            sock.listen(1)
            yield True
        except OSError:
            if sock is not None:
                sock.close()
                sock = None
            yield False
        finally:
            if sock is not None:
                sock.close()

    def _looks_like_python_web_server(self, source: str) -> bool:
        lower = (source or "").lower()
        return any(
            marker in lower
            for marker in (
                "from flask import",
                "import flask",
                "flask(",
                "app.run(",
                "uvicorn.run(",
                "from fastapi import",
                "import fastapi",
            )
        )

    def _python_web_framework(self, source: str) -> str:
        lower = (source or "").lower()
        if "from flask import" in lower or "import flask" in lower or "flask(" in lower:
            return "flask"
        if "uvicorn.run(" in lower or "from fastapi import" in lower or "import fastapi" in lower:
            return "uvicorn"
        return ""

    def _web_startup_port_candidates(self, files: list[Path]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, int, int]] = set()
        for path in files:
            if path.suffix != ".py":
                continue
            source = self._read_text(path)
            framework = self._python_web_framework(source)
            if not framework:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            constant_ports = self._constant_port_assignments(tree)
            env_port_defaults = self._env_port_default_assignments(tree)
            flask_app_names = self._flask_app_variable_names(tree) if framework == "flask" else set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not self._is_web_server_run_call(node, framework, flask_app_names):
                    continue
                port = self._run_call_port_value(
                    node,
                    framework=framework,
                    constant_ports=constant_ports,
                    env_port_defaults=env_port_defaults,
                )
                if port is None:
                    continue
                line_no = int(getattr(node, "lineno", 1) or 1)
                key = (str(path.resolve()), line_no, port)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "file_path": str(path.resolve()),
                        "line": line_no,
                        "port": port,
                        "framework": framework,
                    }
                )
        return candidates

    def _constant_port_assignments(self, tree: ast.AST) -> dict[str, int]:
        ports: dict[str, int] = {}
        for node in ast.walk(tree):
            value: ast.AST | None = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                value = node.value
                targets = [node.target]
            if value is None:
                continue
            port = self._literal_port_value(value)
            if port is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    ports[target.id] = port
        return ports

    def _env_port_default_assignments(self, tree: ast.AST) -> dict[str, int]:
        defaults: dict[str, int] = {}
        for node in ast.walk(tree):
            value: ast.AST | None = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                value = node.value
                targets = [node.target]
            if value is None or not self._ast_uses_port_env(value):
                continue
            port = self._port_default_from_env_expr(value)
            if port is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    defaults[target.id] = port
        return defaults

    def _run_call_port_value(
        self,
        node: ast.Call,
        *,
        framework: str,
        constant_ports: dict[str, int],
        env_port_defaults: dict[str, int],
    ) -> int | None:
        for keyword in node.keywords:
            if keyword.arg == "port":
                return self._resolve_port_value(keyword.value, constant_ports, env_port_defaults)
        return 5000 if framework == "flask" else 8000 if framework == "uvicorn" else None

    def _resolve_port_value(
        self,
        node: ast.AST,
        constant_ports: dict[str, int],
        env_port_defaults: dict[str, int],
    ) -> int | None:
        literal = self._literal_port_value(node)
        if literal is not None:
            return literal
        if isinstance(node, ast.Name):
            return env_port_defaults.get(node.id) or constant_ports.get(node.id)
        if isinstance(node, ast.Call) and node.args:
            name = self._call_name(node.func)
            if name in {"int", "builtins.int"}:
                return self._resolve_port_value(node.args[0], constant_ports, env_port_defaults)
        if self._ast_uses_port_env(node):
            return self._port_default_from_env_expr(node)
        return None

    def _literal_port_value(self, node: ast.AST) -> int | None:
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None

    def _port_default_from_env_expr(self, node: ast.AST) -> int | None:
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            if not self._call_name(child.func).endswith(("get", "getenv")):
                continue
            if not child.args:
                continue
            first = child.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.upper() == "PORT"):
                continue
            if len(child.args) >= 2:
                return self._literal_port_value(child.args[1])
            for keyword in child.keywords:
                if keyword.arg == "default":
                    return self._literal_port_value(keyword.value)
        return None

    def _env_port_variable_names(self, tree: ast.AST) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(tree):
            value: ast.AST | None = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                value = node.value
                targets = [node.target]
            if value is None:
                continue
            if not (self._ast_uses_port_env(value) or self._ast_uses_free_port_helper(value)):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        return names

    def _flask_app_variable_names(self, tree: ast.AST) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(tree):
            value: ast.AST | None = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                value = node.value
                targets = [node.target]
            if not isinstance(value, ast.Call) or not self._call_name(value.func).endswith("Flask"):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        return names

    def _is_web_server_run_call(self, node: ast.Call, framework: str, flask_app_names: set[str] | None = None) -> bool:
        name = self._call_name(node.func)
        if framework == "flask":
            app_names = flask_app_names or {"app", "application", "server"}
            return name in {f"{app_name}.run" for app_name in app_names}
        if framework == "uvicorn":
            return name == "uvicorn.run"
        return False

    def _run_call_uses_runtime_port_config(self, node: ast.Call, env_port_names: set[str]) -> bool:
        port_value = None
        for keyword in node.keywords:
            if keyword.arg == "port":
                port_value = keyword.value
                break
        if port_value is None:
            return False
        if self._ast_uses_port_env(port_value) or self._ast_uses_free_port_helper(port_value):
            return True
        if isinstance(port_value, ast.Name) and port_value.id in env_port_names:
            return True
        return False

    def _ast_uses_port_env(self, node: ast.AST) -> bool:
        saw_port = False
        saw_env = False
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value.upper() == "PORT":
                saw_port = True
            elif isinstance(child, ast.Subscript) and isinstance(child.slice, ast.Constant) and child.slice.value == "PORT":
                saw_port = True
            elif isinstance(child, ast.Attribute) and child.attr in {"environ", "getenv"}:
                saw_env = True
            elif isinstance(child, ast.Name) and child.id in {"environ", "getenv"}:
                saw_env = True
        return saw_port and saw_env

    def _ast_uses_free_port_helper(self, node: ast.AST) -> bool:
        for child in ast.walk(node):
            name = self._call_name(child.func) if isinstance(child, ast.Call) else self._call_name(child)
            lowered = name.lower()
            if any(marker in lowered for marker in ("free_port", "available_port", "open_port", "find_port")):
                return True
        return False

    def _run_call_port_description(self, node: ast.Call, framework: str) -> str:
        default_port = "5000" if framework == "flask" else "8000"
        for keyword in node.keywords:
            if keyword.arg != "port":
                continue
            value = keyword.value
            if isinstance(value, ast.Constant) and isinstance(value.value, (int, str)):
                return f"fixed port {value.value}"
            return "a fixed or non-runtime-configurable port"
        return f"the default port {default_port}"

    def _source_files_containing_terms(self, files: list[Path], terms: list[str]) -> list[str]:
        targets: list[str] = []
        for path in files:
            if not self._is_text_project_file(path):
                continue
            text = self._read_text(path)
            if any(term in text for term in terms):
                targets.append(str(path.resolve()))
        return self._dedupe(targets)

    def _evidence_spans_for_terms(self, files: list[Path], terms: list[str], syntax_context: str) -> list[dict[str, Any]]:
        spans: list[dict[str, Any]] = []
        for path in files:
            if not self._is_text_project_file(path):
                continue
            for line_no, line in enumerate(self._read_text(path).splitlines(), 1):
                if any(term in line for term in terms):
                    spans.append(
                        {
                            "file_path": str(path.resolve()),
                            "line": line_no,
                            "column": 1,
                            "text": line.strip()[:200],
                            "syntax_context": syntax_context,
                        }
                    )
                    break
        return spans[:5]

    def _line_evidence_span(self, path: Path, line_no: int, syntax_context: str) -> dict[str, Any]:
        lines = self._read_text(path).splitlines()
        text = lines[line_no - 1].strip() if 0 < line_no <= len(lines) else ""
        return {
            "file_path": str(path.resolve()),
            "line": line_no,
            "column": 1,
            "text": text[:200],
            "syntax_context": syntax_context,
        }

    def _target_files_from_smoke_message(
        self,
        project_path: Path,
        message: str,
        run_command: str,
        files: list[Path],
    ) -> list[str]:
        targets: list[str] = []
        for raw_path in re.findall(r'File "([^"]+)"', message or ""):
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = project_path / path
            try:
                resolved = path.resolve()
                resolved.relative_to(project_path.resolve())
            except (OSError, ValueError):
                continue
            if resolved.exists() and resolved.is_file():
                targets.append(str(resolved))
        if not targets:
            try:
                args = shlex.split(run_command)
            except ValueError:
                args = []
            entry = self._entry_module_from_args(project_path, args, files) if args else None
            if entry is not None and entry.exists():
                targets.append(str(entry.resolve()))
        return self._dedupe(targets)

    def _target_files_from_runtime_failure_output(
        self,
        project_path: Path,
        output: str,
        args: list[str],
        files: list[Path],
    ) -> list[str]:
        targets: list[str] = []
        targets.extend(self._target_files_from_smoke_message(project_path, output, " ".join(args), files))
        for raw_path in re.findall(r"\(([^()]+\.py)\)", output or ""):
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = project_path / path
            try:
                resolved = path.resolve()
                resolved.relative_to(project_path.resolve())
            except (OSError, ValueError):
                continue
            if resolved.exists() and resolved.is_file():
                targets.append(str(resolved))
        entry = self._entry_module_from_args(project_path, args, files)
        if entry is not None and entry.exists():
            targets.insert(0, str(entry.resolve()))
        return self._dedupe(targets)

    def _runtime_failure_evidence_spans(
        self,
        project_path: Path,
        output: str,
        target_files: list[str],
    ) -> list[dict[str, Any]]:
        spans: list[dict[str, Any]] = []
        for raw_path in target_files[:5]:
            path = Path(raw_path)
            if not path.exists() or not path.is_file():
                continue
            line_no = 1
            traceback_match = re.search(rf'File "{re.escape(str(path))}", line (\d+)', output or "")
            if traceback_match:
                line_no = int(traceback_match.group(1))
            elif path.name in (output or ""):
                line_no = self._first_relevant_runtime_line(path, output)
            spans.append(self._line_evidence_span(path, line_no, "direct_startup_runtime_failure"))
        if not spans:
            spans.append(
                {
                    "file_path": str(project_path),
                    "line": 1,
                    "column": 1,
                    "text": self._short_error("Startup output", output),
                    "syntax_context": "direct_startup_runtime_failure",
                }
            )
        return spans

    def _first_relevant_runtime_line(self, path: Path, output: str) -> int:
        text = self._read_text(path)
        import_names = re.findall(r"cannot import name ['\"]([^'\"]+)['\"]", output or "", flags=re.IGNORECASE)
        module_names = re.findall(r"from ['\"]([^'\"]+)['\"]", output or "", flags=re.IGNORECASE)
        terms = [*import_names, *module_names]
        for index, line in enumerate(text.splitlines(), 1):
            if any(term and term in line for term in terms):
                return index
        for index, line in enumerate(text.splitlines(), 1):
            lowered = line.lower()
            if "import " in lowered or "from " in lowered:
                return index
        return 1

    def _is_text_project_file(self, path: Path) -> bool:
        if path.name.startswith(".") or path.is_dir():
            return False
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip"}:
            return False
        try:
            path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return False
        return True

    def _runtime_contract_mismatches(self, readme_text: str, code_text: str) -> list[str]:
        readme_lower = readme_text.lower()
        code_lower = code_text.lower()
        runtimes = {
            "pygame": ("import pygame", "from pygame"),
            "curses": ("import curses", "from curses"),
            "tkinter": ("import tkinter", "from tkinter"),
            "turtle": ("import turtle", "from turtle"),
        }
        documented = {name for name in runtimes if name in readme_lower}
        implemented = {
            name
            for name, markers in runtimes.items()
            if any(marker in code_lower for marker in markers)
        }
        if not documented or not implemented:
            return []
        mismatches = []
        for runtime in sorted(documented - implemented):
            if implemented:
                mismatches.append(
                    "Runtime contract mismatch: README references "
                    f"{runtime}, but generated code uses {', '.join(sorted(implemented))} instead."
                )
        return mismatches

    def _import_only_smoke_test(self, project_path: Path, args: list[str], files: list[Path]) -> dict[str, Any]:
        entry = self._entry_module_from_args(project_path, args, files)
        if entry is None:
            return {"passed": True, "warning": True, "message": "Smoke test skipped full run: interactive project entry could not be imported safely."}
        entry = entry.resolve()

        command = [
            args[0],
            "-c",
            (
                "import importlib.util, pathlib; "
                f"path = pathlib.Path({str(entry)!r}); "
                "spec = importlib.util.spec_from_file_location('openpilot_smoke_entry', path); "
                "module = importlib.util.module_from_spec(spec); "
                "spec.loader.exec_module(module)"
            ),
        ]
        try:
            result = subprocess.run(
                command,
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=self.import_smoke_timeout_seconds,
                env=self._smoke_env(project_path),
            )
        except subprocess.TimeoutExpired as exc:
            combined = f"{exc.stdout or ''}\n{exc.stderr or ''}"
            if self._looks_like_traceback(combined):
                return {"passed": False, "warning": False, "message": self._short_error("Import-only smoke test timed out with error output", combined)}
            warning_check = self._assess_runtime_warnings(
                command=" ".join(command),
                cwd=project_path,
                stdout=str(exc.stdout or ""),
                stderr=str(exc.stderr or ""),
            )
            if warning_check and warning_check.requires_fix:
                return self._warning_fix_failure(warning_check)
            if self._has_main_guard(entry) and not self._has_unprotected_interactive_startup(entry):
                return {
                    "passed": True,
                    "warning": True,
                    "message": (
                        f"Import-only smoke test timed out after {self.import_smoke_timeout_seconds}s; "
                        "interactive import was slow, so full run was skipped."
                    ),
                    "warning_check_result": warning_check,
                }
            return {
                "passed": False,
                "warning": False,
                "message": self._short_error(
                    "Import-only smoke test timed out; possible top-level event loop or startup side effect",
                    combined,
                ),
            }

        combined_output = f"{result.stdout or ''}\n{result.stderr or ''}"
        warning_check = self._assess_runtime_warnings(
            command=" ".join(command),
            cwd=project_path,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )
        if warning_check and warning_check.requires_fix and result.returncode == 0 and not self._looks_like_traceback(combined_output):
            return self._warning_fix_failure(warning_check)
        if result.returncode != 0:
            return {
                "passed": False,
                "warning": False,
                "message": self._short_error(f"Import-only smoke test exited with code {result.returncode}", combined_output),
            }
        if warning_check and (warning_check.warnings or warning_check.ignored_warnings):
            message = warning_check.reason or "Import-only smoke test emitted runtime warnings."
            return {"passed": True, "warning": True, "message": message, "warning_check_result": warning_check}
        return {"passed": True, "warning": False, "message": "Import-only smoke test passed."}

    def _assess_runtime_warnings(
        self,
        *,
        command: str,
        cwd: Path,
        stdout: str,
        stderr: str,
    ) -> WarningCheckResultMetadata | None:
        combined_lower = f"{stdout}\n{stderr}".lower()
        if not any(marker in combined_lower for marker in ("warning", "fc-list", "system fonts cannot be loaded")):
            return None
        from metadata import ToolInputMetadata
        from tools.warning_check_tool import warning_check_tool_executor

        result = warning_check_tool_executor(
            ToolInputMetadata.from_mapping(
                "warning_check_tool",
                {
                    "command": command,
                    "cwd": str(cwd),
                    "stdout": stdout,
                    "stderr": stderr,
                },
            )
        )
        if isinstance(result.result, WarningCheckResultMetadata):
            return result.result
        return None

    def _warning_fix_failure(self, warning_check: WarningCheckResultMetadata) -> dict[str, Any]:
        reason = warning_check.reason or "Runtime warning requires repair."
        fix = warning_check.recommended_fix
        message = f"Runtime warning requires repair: {reason}"
        if fix:
            message += f" Recommended fix: {fix}"
        return {
            "passed": False,
            "warning": False,
            "message": message,
            "warning_check_result": warning_check,
        }

    def _system_output_fix_failure(self, system_check: WarningCheckResultMetadata) -> dict[str, Any]:
        reason = system_check.reason or "Runtime system output requires repair."
        fix = system_check.recommended_fix
        message = f"Runtime system output requires repair: {reason}"
        if fix:
            message += f" Recommended fix: {fix}"
        category = system_check.warnings[0].category if system_check.warnings else "runtime_system_output"
        return {
            "passed": False,
            "warning": False,
            "message": message,
            "recommended_action": fix,
            "recommended_repair_kind": self._recommended_repair_kind_for_system_output(category),
            "category": "environment" if category in {"port_conflict", "startup_port_conflict"} else "runtime_error",
            "warning_check_result": system_check,
        }

    def _recommended_repair_kind_for_system_output(self, category: str) -> str:
        if category in {"port_conflict", "startup_port_conflict"}:
            return "make_web_port_configurable"
        return "repair_runtime_system_output"

    def _normalize_python_args(self, project_path: Path, args: list[str]) -> list[str]:
        if not args:
            return args
        executable = args[0]
        if executable == "python" or executable.startswith("python"):
            project_python = self._project_python_executable(project_path)
            args[0] = str(project_python or sys.executable)
        elif executable.startswith(".venv/") or executable.startswith(".venv\\"):
            args[0] = str(project_path / executable)
        return args

    def _project_python_executable(self, project_path: Path) -> Path | None:
        candidates = [
            project_path / ".venv" / "bin" / "python",
            project_path / ".venv" / "Scripts" / "python.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _is_python_executable(self, executable: str) -> bool:
        name = Path(executable).name.lower()
        return name == "python" or name.startswith("python")

    def _entry_module_from_args(self, project_path: Path, args: list[str], files: list[Path]) -> Path | None:
        for arg in args[1:]:
            if arg.startswith("-"):
                continue
            candidate = Path(arg).expanduser()
            if not candidate.is_absolute():
                candidate = project_path / candidate
            if candidate.suffix == ".py" and candidate.exists():
                return candidate
            break
        python_files = [path for path in files if path.suffix == ".py" and path.exists()]
        if len(python_files) == 1:
            return python_files[0]
        for name in ("main.py", "app.py"):
            candidate = project_path / name
            if candidate.exists():
                return candidate
        return python_files[0] if python_files else None

    def _augment_files_with_run_command_context(
        self,
        project_path: Path,
        files: list[Path],
        run_command: str,
    ) -> list[Path]:
        augmented = list(files)
        try:
            args = self._normalize_python_args(project_path, shlex.split(run_command))
        except ValueError:
            args = []
        entry = self._entry_module_from_args(project_path, args, files) if args else None
        if entry is not None:
            augmented.append(entry)
            augmented.extend(self._local_python_import_closure(project_path, entry))
        return self._dedupe_paths(augmented)

    def _local_python_import_closure(self, project_path: Path, entry: Path) -> list[Path]:
        discovered: list[Path] = []
        queue = [entry]
        seen: set[Path] = set()
        while queue and len(discovered) < 40:
            path = queue.pop(0).resolve()
            if path in seen or not path.exists() or path.suffix != ".py":
                continue
            seen.add(path)
            for dependency in self._local_python_import_files(project_path, path):
                resolved = dependency.resolve()
                if resolved not in seen:
                    discovered.append(resolved)
                    queue.append(resolved)
        return discovered

    def _local_python_import_files(self, project_path: Path, path: Path) -> list[Path]:
        try:
            tree = ast.parse(self._read_text(path))
        except SyntaxError:
            return []
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".", 1)[0])
        dependencies: list[Path] = []
        for module in sorted(modules):
            if not module or module.startswith("_"):
                continue
            module_file = project_path / f"{module}.py"
            package_init = project_path / module / "__init__.py"
            if module_file.exists():
                dependencies.append(module_file)
            elif package_init.exists():
                dependencies.append(package_init)
        return dependencies

    def _dedupe_paths(self, paths: list[Path]) -> list[Path]:
        seen: set[str] = set()
        result: list[Path] = []
        for path in paths:
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            result.append(path)
        return result

    def _has_main_guard(self, path: Path) -> bool:
        try:
            tree = ast.parse(self._read_text(path))
        except SyntaxError:
            return False
        return any(isinstance(node, ast.If) and self._is_main_guard_test(node.test) for node in tree.body)

    def _has_unprotected_interactive_startup(self, path: Path) -> bool:
        try:
            tree = ast.parse(self._read_text(path))
        except SyntaxError:
            return True
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.If) and self._is_main_guard_test(node.test):
                continue
            if isinstance(node, (ast.While, ast.For)):
                return True
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and self._is_interactive_startup_call(child):
                    return True
        return False

    def _is_main_guard_test(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.Compare) or len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
            return False
        values = [node.left, *node.comparators]
        return any(isinstance(value, ast.Name) and value.id == "__name__" for value in values) and any(
            isinstance(value, ast.Constant) and value.value == "__main__" for value in values
        )

    def _is_interactive_startup_call(self, node: ast.Call) -> bool:
        name = self._call_name(node.func)
        return (
            name in {"main", "run", "pygame.init", "pygame.display.set_mode"}
            or name.endswith(".mainloop")
            or name.endswith(".run")
            or name.endswith(".set_mode")
        )

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = self._call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        if isinstance(node, ast.Call):
            return self._call_name(node.func)
        return ""

    def _smoke_env(self, project_path: Path | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        if project_path is not None:
            venv_path = project_path / ".venv"
            bin_dir = venv_path / ("Scripts" if os.name == "nt" else "bin")
            if bin_dir.exists():
                env["VIRTUAL_ENV"] = str(venv_path)
                env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        return env

    def _blocking_review_errors(self, static_review: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if static_review.get("approved") is False:
            errors.append("Static code review did not approve the generated code.")
        syntax_errors = static_review.get("syntax_errors") or []
        for error in syntax_errors:
            errors.append(f"Static review syntax error: {error}")
        for issue in static_review.get("issues") or []:
            if any(marker in str(issue).lower() for marker in ("syntax", "runtime", "import", "undefined", "security")):
                errors.append(f"Blocking static review issue: {issue}")
        return errors

    def _review_python_code(self, code_text: str) -> dict[str, Any]:
        try:
            from tools.code_reviewer import code_reviewer_executor

            from metadata import ToolInputMetadata

            result = code_reviewer_executor(ToolInputMetadata.from_mapping("code_reviewer", {"code": code_text, "language": "python"}))
            return result.result.to_json_dict() if result.result else {}
        except Exception:
            return {}

    def _extract_run_command(self, readme_text: str) -> str:
        lines = readme_text.splitlines()
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(("python ", "npm ", ".venv/bin/python ", ".venv/Scripts/python.exe ")):
                return stripped
            if stripped in {"```bash", "```sh", "```"} and index + 1 < len(lines):
                candidate = lines[index + 1].strip()
                if candidate.startswith(("python ", "npm ", ".venv/bin/python ", ".venv/Scripts/python.exe ")):
                    return candidate
        return ""

    def _build_next_iteration_goal(self, goal: str, actions: list[str], errors: list[str]) -> str | None:
        focus = actions[:2] or errors[:2]
        if not focus:
            return None
        return f"Fix the project for this goal: {goal}. Focus on: {'; '.join(focus)}"

    def _looks_like_traceback(self, output: str) -> bool:
        lower = output.lower()
        return any(
            marker in lower
            for marker in (
                "traceback",
                "syntaxerror",
                "modulenotfounderror",
                "importerror",
                "nameerror",
                "runtimeerror",
                "_curses.error",
            )
        )

    def _is_interactive_environment_error(self, output: str) -> bool:
        lower = self._strip_ansi(output).lower()
        markers = (
            "_curses.error: cbreak() returned err",
            "_curses.error: nocbreak() returned err",
            "_curses.error: endwin() returned err",
            "setupterm",
            "not a tty",
            "inappropriate ioctl for device",
            "no available video device",
            "cannot open display",
        )
        return any(marker in lower for marker in markers)

    def _short_error(self, prefix: str, output: str) -> str:
        clean_output = self._strip_ansi(output)
        clean = " ".join(line.strip() for line in clean_output.splitlines() if line.strip())
        if len(clean) > 500:
            clean = clean[:497] + "..."
        return f"{prefix}: {clean}" if clean else prefix

    def _strip_ansi(self, text: str) -> str:
        return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text or "")

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _dedupe(self, values: list[str]) -> list[str]:
        seen = set()
        result = []
        for value in values:
            if value and value not in seen:
                result.append(value)
                seen.add(value)
        return result

    def _log_agent(
        self,
        event_type: str,
        *,
        success: bool | None,
        input_summary: Any | None = None,
        output_summary: Any | None = None,
        error: str | None = None,
        level: str | None = None,
    ) -> None:
        if not self.logger or not hasattr(self.logger, "log_structured_event"):
            return
        self.logger.log_structured_event(
            source_type="agent",
            source_name="autonomous_iteration.agents.project_evaluator",
            phase="project_evaluation",
            event_type=event_type,
            session_id=self.session_id_getter() or "unknown",
            turn_id=1,
            success=success,
            input_summary=input_summary,
            output_summary=output_summary,
            error=error,
            level=level,
        )
