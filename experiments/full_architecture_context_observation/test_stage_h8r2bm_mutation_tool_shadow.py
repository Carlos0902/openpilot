from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.config import LLMSettings
from core.llm import LLMResponse, LLMToolCall, LLMToolFunctionCall
from metadata import ReasoningCapabilityProfileId

from experiments.full_architecture_context_observation.stage_h8r2bm_mutation_tool_shadow import (
    EXACT_VALIDATION_COMMAND,
    build_mock_mutation_pair,
    canonical_hash,
    run_mutation_tool_shadow,
    validate_mutation_shadow_receipt,
)


_SECRET_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}(?![A-Za-z0-9])")


class FakeMutationClient:
    def __init__(self, project_root: Path, *, fail_validation: bool = False) -> None:
        self.project_root = project_root
        self.fail_validation = fail_validation
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
                        id=f"bm-read-{self._index}",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments=json.dumps({"file_path": "calculator.py"}),
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
                        id=f"bm-patch-{self._index}",
                        function=LLMToolFunctionCall(
                            name="file_patch_writer",
                            arguments=json.dumps(
                                {
                                    "file_path": "calculator.py",
                                    "operation_kind": "modify_symbol",
                                    "symbol_name": "divide",
                                    "replacement_text": (
                                        "def divide(a, b):\n"
                                        "    if b == 0:\n"
                                        "        raise ValueError(\"division by zero\")\n"
                                        "    return a / b"
                                    ),
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
            command = "python -m pytest -q tests/test_calculator.py"
            if self.fail_validation:
                command = "python -m compileall calculator.py"
            response = LLMResponse(
                content="",
                tool_calls=[
                    LLMToolCall(
                        id=f"bm-validate-{self._index}",
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


def test_mock_mutation_pair_requires_writer_validation_and_token_reduction(tmp_path: Path) -> None:
    result = build_mock_mutation_pair(output_root=tmp_path / "bm")

    assert result["status"] == "passed"
    assert result["arm_count"] == 2
    assert result["aggregate_usage"]["prompt_token_delta"] > 0
    assert result["aggregate_usage"]["total_token_delta"] > 0
    assert all(result["invariants"].values())
    assert result["side_effects"]["provider_transport_attempted"] is False
    assert result["side_effects"]["mock_completion_calls"] == 8

    for arm in result["arms"]:
        assert arm["status"] == "passed"
        assert arm["execution"]["execution_mode"] == "real_mutation"
        assert arm["execution"]["allow_mutations"] is True
        assert arm["execution"]["user_confirmed"] is True
        assert arm["mutation_gate"]["writer_observed"] is True
        assert arm["mutation_gate"]["provider_exact_validation_observed"] is True
        assert arm["mutation_gate"]["provider_validation_exit_zero"] is True
        assert arm["mutation_gate"]["independent_exact_validation_exit_zero"] is True
        assert arm["mutation_gate"]["only_scoped_source_file_changed"] is True
        assert arm["mutation_gate"]["target_changed"] is True

    assert validate_mutation_shadow_receipt(result)["status"] == "passed"


def test_mutation_shadow_receipt_is_body_and_secret_free(tmp_path: Path) -> None:
    result = build_mock_mutation_pair(output_root=tmp_path / "bm-body")
    encoded = json.dumps(result, ensure_ascii=False)

    assert "replacement_text" not in encoded
    assert "division by zero" not in encoded
    assert "def divide" not in encoded
    assert "stdout" not in encoded
    assert "stderr" not in encoded
    assert not _SECRET_RE.search(encoded)
    assert "\"content\"" not in encoded
    assert "low-value-" not in encoded

    result["arms"][0]["requests"][0]["content"] = "must not be persisted"
    result["receipt_hash"] = canonical_hash(
        {key: value for key, value in result.items() if key != "receipt_hash"}
    )
    with pytest.raises(ValueError, match="prompt/body"):
        validate_mutation_shadow_receipt(result)


def test_mutation_shadow_blocks_without_settings(tmp_path: Path) -> None:
    calls = {"client_factory": 0}

    def missing_settings() -> LLMSettings:
        raise ValueError("missing credential")

    def client_factory(_settings: LLMSettings, project_root: Path, _arm_id: str):
        calls["client_factory"] += 1
        return FakeMutationClient(project_root)

    result = run_mutation_tool_shadow(
        output_root=tmp_path / "bm-blocked",
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


def test_mutation_shadow_rejects_suspicious_validation_success(tmp_path: Path) -> None:
    def client_factory(_settings: LLMSettings, project_root: Path, _arm_id: str):
        return FakeMutationClient(project_root, fail_validation=True)

    result = run_mutation_tool_shadow(
        output_root=tmp_path / "bm-suspicious",
        settings_factory=_settings,
        client_factory=client_factory,
        provider_transport=False,
    )

    assert result["status"] == "needs_followup"
    assert result["arms"][0]["mutation_gate"]["provider_exact_validation_observed"] is False
    assert result["arms"][0]["mutation_gate"]["suspicious_success"] is True
    assert validate_mutation_shadow_receipt(result)["status"] == "needs_followup"
    assert result["validation_command_sha256"] == canonical_hash(EXACT_VALIDATION_COMMAND)
