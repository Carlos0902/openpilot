from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.config import LLMSettings
from core.llm import LLMResponse, LLMToolCall, LLMToolFunctionCall
from metadata import ReasoningCapabilityProfileId

from experiments.full_architecture_context_observation.stage_h8r2bn_mutation_confirmation_matrix import (
    CASES,
    EXACT_VALIDATION_COMMAND,
    build_mock_mutation_confirmation_matrix,
    canonical_hash,
    prepare_case_context_for_test,
    run_mutation_confirmation_matrix,
    validate_mutation_confirmation_matrix_receipt,
)


_SECRET_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}(?![A-Za-z0-9])")


class FakeMatrixMutationClient:
    def __init__(self, project_root: Path, case_id: str, *, wrong_validation: bool = False) -> None:
        self.project_root = project_root
        self.case = next(case for case in CASES if case.case_id == case_id)
        self.wrong_validation = wrong_validation
        self.requests = []
        self.response_history = []
        self._index = 0

    def complete(self, request, **_kwargs):
        self.requests.append(request)
        prompt_tokens = max(1, sum(len(message.content.split()) for message in request.messages))
        sequence = self._index % 4
        self._index += 1
        if sequence == 0:
            response = LLMResponse(
                content="",
                tool_calls=[
                    LLMToolCall(
                        id=f"bn-read-{self.case.case_id}-{self._index}",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments=json.dumps({"file_path": self.case.target_file}),
                        ),
                    )
                ],
                model="fake-deepseek-v4-flash",
                provider="offline-mock",
                finish_reason="tool_calls",
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 24,
                    "total_tokens": prompt_tokens + 24,
                },
            )
        elif sequence == 1:
            response = LLMResponse(
                content="",
                tool_calls=[
                    LLMToolCall(
                        id=f"bn-patch-{self.case.case_id}-{self._index}",
                        function=LLMToolFunctionCall(
                            name="file_patch_writer",
                            arguments=json.dumps(
                                {
                                    "file_path": self.case.target_file,
                                    "operation_kind": "modify_symbol",
                                    "symbol_name": self.case.target_symbol,
                                    "replacement_text": self.case.mock_replacement_text,
                                }
                            ),
                        ),
                    )
                ],
                model="fake-deepseek-v4-flash",
                provider="offline-mock",
                finish_reason="tool_calls",
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 64,
                    "total_tokens": prompt_tokens + 64,
                },
            )
        elif sequence == 2:
            command = EXACT_VALIDATION_COMMAND
            if self.wrong_validation:
                command = "python -m compileall calculator.py"
            response = LLMResponse(
                content="",
                tool_calls=[
                    LLMToolCall(
                        id=f"bn-validate-{self.case.case_id}-{self._index}",
                        function=LLMToolFunctionCall(
                            name="command_executor",
                            arguments=json.dumps(
                                {
                                    "command": command,
                                    "cwd": str(self.project_root),
                                    "mode": "automatic",
                                    "timeout": 30,
                                    "env": {"PYTHONDONTWRITEBYTECODE": "1"},
                                }
                            ),
                        ),
                    )
                ],
                model="fake-deepseek-v4-flash",
                provider="offline-mock",
                finish_reason="tool_calls",
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 48,
                    "total_tokens": prompt_tokens + 48,
                },
            )
        else:
            response = LLMResponse(
                content="Scoped mutation and exact validation completed.",
                model="fake-deepseek-v4-flash",
                provider="offline-mock",
                finish_reason="stop",
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 16,
                    "total_tokens": prompt_tokens + 16,
                },
            )
        self.response_history.append(response)
        return response


def _settings() -> LLMSettings:
    return LLMSettings(
        _env_file=None,
        provider="openai-compatible",
        base_url="https://api.deepseek.com",
        api_key="test-key",
        model="fake-deepseek-v4-flash",
        temperature=0.0,
        transport_retries=0,
        reasoning_capability_profile=ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
    )


def test_mock_mutation_confirmation_matrix_passes_all_case_gates(tmp_path: Path) -> None:
    result = build_mock_mutation_confirmation_matrix(output_root=tmp_path / "bn")

    assert result["status"] == "passed"
    assert result["case_count"] == 3
    assert result["arm_count"] == 6
    assert result["aggregate_usage"]["prompt_token_delta"] > 0
    assert result["aggregate_usage"]["total_token_delta"] > 0
    assert result["side_effects"]["provider_transport_attempted"] is False
    assert result["side_effects"]["mock_completion_calls"] == 24
    assert all(result["invariants"].values())

    for case in result["cases"]:
        assert case["status"] == "passed"
        assert set(case["arms"]) == {"raw", "reusable"}
        for arm in case["arms"].values():
            assert arm["status"] == "passed"
            assert arm["execution"]["execution_mode"] == "real_mutation"
            assert arm["mutation_gate"]["writer_observed"] is True
            assert arm["mutation_gate"]["provider_exact_validation_observed"] is True
            assert arm["mutation_gate"]["provider_validation_exit_zero"] is True
            assert arm["mutation_gate"]["independent_exact_validation_exit_zero"] is True
            assert arm["mutation_gate"]["only_scoped_source_file_changed"] is True
            assert arm["mutation_gate"]["suspicious_success"] is False

    assert validate_mutation_confirmation_matrix_receipt(result)["status"] == "passed"


def test_mutation_confirmation_matrix_uses_required_system_tool_workflow(tmp_path: Path) -> None:
    prepared = prepare_case_context_for_test(CASES[0], tmp_path / "context")
    authority = next(
        candidate
        for candidate in prepared.raw_candidates
        if candidate.candidate_id.startswith("h8r2bn-task-authority")
    )

    assert authority.role == "system"
    assert authority.retention == "required"
    assert "first response must call file_reader" in authority.content
    assert "Do not answer in prose before the first tool call" in authority.content


def test_mutation_confirmation_matrix_receipt_is_body_and_secret_free(tmp_path: Path) -> None:
    result = build_mock_mutation_confirmation_matrix(output_root=tmp_path / "bn-body")
    encoded = json.dumps(result, ensure_ascii=False)

    for token in (
        "replacement_text",
        "division by zero",
        "max(low",
        "mapping.get",
        "def divide",
        "def clamp",
        "def safe_get",
        "stdout",
        "stderr",
        "\"content\"",
        "low-value-",
    ):
        assert token not in encoded
    assert not _SECRET_RE.search(encoded)

    result["cases"][0]["arms"]["raw"]["requests"][0]["content"] = "must not persist"
    result["receipt_hash"] = canonical_hash(
        {key: value for key, value in result.items() if key != "receipt_hash"}
    )
    with pytest.raises(ValueError, match="prompt/body"):
        validate_mutation_confirmation_matrix_receipt(result)


def test_mutation_confirmation_matrix_blocks_without_settings(tmp_path: Path) -> None:
    calls = {"client_factory": 0}

    def missing_settings() -> LLMSettings:
        raise ValueError("missing credential")

    def client_factory(_settings: LLMSettings, project_root: Path, case_id: str, _arm_id: str):
        calls["client_factory"] += 1
        return FakeMatrixMutationClient(project_root, case_id)

    result = run_mutation_confirmation_matrix(
        output_root=tmp_path / "bn-blocked",
        settings_factory=missing_settings,
        client_factory=client_factory,
        provider_transport=False,
    )

    assert result["status"] == "blocked"
    assert result["side_effects"]["provider_calls"] == 0
    assert result["side_effects"]["mock_completion_calls"] == 0
    assert result["side_effects"]["provider_transport_attempted"] is False
    assert result["provider_descriptor"]["credential_serialized"] is False
    assert calls["client_factory"] == 0


def test_mutation_confirmation_matrix_rejects_suspicious_validation_success(tmp_path: Path) -> None:
    def client_factory(_settings: LLMSettings, project_root: Path, case_id: str, _arm_id: str):
        return FakeMatrixMutationClient(
            project_root,
            case_id,
            wrong_validation=(case_id == CASES[0].case_id),
        )

    result = run_mutation_confirmation_matrix(
        output_root=tmp_path / "bn-suspicious",
        settings_factory=_settings,
        client_factory=client_factory,
        provider_transport=False,
    )

    assert result["status"] == "needs_followup"
    first_raw = result["cases"][0]["arms"]["raw"]
    assert first_raw["mutation_gate"]["provider_exact_validation_observed"] is False
    assert first_raw["mutation_gate"]["suspicious_success"] is True
    assert validate_mutation_confirmation_matrix_receipt(result)["status"] == "needs_followup"
