"""Stateless, outcome-blind API reviewer for the core-benefit screen.

This module is deliberately a review-plane client, not a mini-SWE task runner.
It creates one request per candidate and writes raw task material and model
output only to the configured private review directory.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

from .acquisition import (
    build_candidate_stratum_review_decision,
    write_candidate_stratum_review_decision,
)


ALLOWED_STRATA = frozenset(
    {
        "localization",
        "single_file",
        "multi_file_interface",
        "config_cli",
        "test_regression",
        "measurement_disambiguation",
    }
)
FORBIDDEN_INPUT_FIELDS = frozenset(
    {
        "first_review_decision",
        "first_review_rationale",
        "ordinary_trajectory",
        "active_iteration_trajectory",
        "task_arm_outcome",
        "hidden_evaluator_result",
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
    }
)


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"expected a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"duplicate JSON key: {key}")
        payload[key] = value
    return payload


@dataclass(frozen=True)
class CandidateReviewInput:
    instance_id: str
    problem_statement: str
    patch: str
    test_patch: str

    def as_payload(self) -> dict[str, str]:
        values = {
            "instance_id": self.instance_id,
            "problem_statement": self.problem_statement,
            "patch": self.patch,
            "test_patch": self.test_patch,
        }
        if not all(value.strip() for value in values.values()):
            raise ValueError("candidate review input must be complete")
        return values


@dataclass(frozen=True)
class ProviderReviewProtocol:
    path: Path
    base_url: str
    model_name: str
    temperature: float
    max_output_tokens: int
    api_key_environment_variable: str
    prompt_path: Path
    prompt_sha256: str
    rules_path: Path
    rules_file_sha256: str
    source_parquet_sha256: str
    reviewer_identity_sha256: str
    private_output_root: Path
    public_output_root: Path
    public_receipt_root: Path

    @property
    def sha256(self) -> str:
        return _file_sha256(self.path)

    @classmethod
    def load(cls, path: Path) -> "ProviderReviewProtocol":
        if path.is_symlink() or not path.is_file():
            raise ValueError("provider review protocol must be a regular file")
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
        provider = payload.get("provider")
        if (
            payload.get("schema_version") != "1.0"
            or payload.get("lifecycle") != "pre-execution-review"
            or not isinstance(provider, dict)
            or payload.get("review_provider_execution_authorized") is not True
            or payload.get("task_arm_provider_execution_authorized") is not False
            or payload.get("task_outcomes_generated") is not False
            or payload.get("production_execution_authorized") is not False
            or payload.get("hypothesis_evidence_eligible") is not False
            or payload.get("candidate_input_fields") != ["instance_id", "problem_statement", "patch", "test_patch"]
            or set(payload.get("forbidden_reviewer_inputs", ())) != FORBIDDEN_INPUT_FIELDS
            or provider.get("request_attempts_per_candidate") != 1
            or provider.get("separate_request_per_candidate") is not True
            or provider.get("stream") is not False
            or provider.get("thinking") != {"type": "disabled"}
        ):
            raise ValueError("provider review protocol has an unsafe contract")
        root = path.parent
        prompt_path = root / payload["prompt_file"]
        rules_path = root / payload["rules_file"]
        if _file_sha256(prompt_path) != payload["prompt_sha256"]:
            raise ValueError("provider review prompt hash drifted")
        if _file_sha256(rules_path) != payload["rules_file_sha256"]:
            raise ValueError("provider review rules hash drifted")
        for field in (
            "prompt_sha256",
            "rules_file_sha256",
            "source_parquet_sha256",
            "reviewer_identity_sha256",
        ):
            value = payload[field]
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{field} must be a lowercase SHA-256")
        return cls(
            path=path,
            base_url=provider["base_url"].rstrip("/"),
            model_name=provider["model_name"],
            temperature=provider["temperature"],
            max_output_tokens=provider["max_output_tokens"],
            api_key_environment_variable=payload["api_key_environment_variable"],
            prompt_path=prompt_path,
            prompt_sha256=payload["prompt_sha256"],
            rules_path=rules_path,
            rules_file_sha256=payload["rules_file_sha256"],
            source_parquet_sha256=payload["source_parquet_sha256"],
            reviewer_identity_sha256=payload["reviewer_identity_sha256"],
            private_output_root=(root / payload["private_output_root"]).resolve(),
            public_output_root=(root / payload["public_output_root"]).resolve(),
            public_receipt_root=(root / payload["public_receipt_root"]).resolve(),
        )


def build_review_request(
    *, protocol: ProviderReviewProtocol, candidate: CandidateReviewInput
) -> dict[str, Any]:
    candidate_payload = candidate.as_payload()
    if set(candidate_payload) & FORBIDDEN_INPUT_FIELDS:
        raise ValueError("review request contains a forbidden field")
    return {
        "model": protocol.model_name,
        "temperature": protocol.temperature,
        "max_tokens": protocol.max_output_tokens,
        "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": protocol.prompt_path.read_text(encoding="utf-8")},
            {
                "role": "user",
                "content": json.dumps(candidate_payload, sort_keys=True, separators=(",", ":")),
            },
        ],
    }


def parse_review_response(content: str) -> tuple[str, str]:
    payload = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
    if set(payload) != {"primary_stratum", "rationale"}:
        raise ValueError("review response has an unsupported schema")
    stratum, rationale = payload["primary_stratum"], payload["rationale"]
    if stratum not in ALLOWED_STRATA or not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("review response is incomplete")
    if len(rationale.split()) > 120:
        raise ValueError("review response rationale exceeds the protocol limit")
    return stratum, rationale.strip()


def _write_json_nonoverwriting(*, path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite provider review artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2)
        stream.write("\n")


def materialize_recorded_review(
    *,
    protocol: ProviderReviewProtocol,
    private_path: Path,
    nonexecution_receipt_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
    execution_receipt_path: Path,
) -> Path:
    """Bind one already-recorded provider response without making a request."""

    if private_path.is_symlink() or not private_path.is_file():
        raise ValueError("recorded provider review must be a regular file")
    payload = json.loads(
        private_path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
    )
    candidate_payload = payload.get("candidate_input")
    if not isinstance(candidate_payload, dict) or set(candidate_payload) != {
        "instance_id",
        "problem_statement",
        "patch",
        "test_patch",
    }:
        raise ValueError("recorded provider review has an unsafe candidate input")
    candidate = CandidateReviewInput(**candidate_payload)
    review_id = f"core-benefit-screen-review-{candidate.instance_id}-deepseek-v4-flash-v1"
    if (
        payload.get("schema_version") != "1.0"
        or payload.get("review_id") != review_id
        or payload.get("protocol_sha256") != protocol.sha256
        or payload.get("prompt_sha256") != protocol.prompt_sha256
        or payload.get("instance_id") != candidate.instance_id
        or payload.get("model_name") != protocol.model_name
    ):
        raise ValueError("recorded provider review binding drifted")
    if payload.get("request_sha256") != _canonical_sha256(
        build_review_request(protocol=protocol, candidate=candidate)
    ):
        raise ValueError("recorded provider review request hash drifted")
    raw_response = payload.get("raw_response")
    if not isinstance(raw_response, dict) or payload.get("response_sha256") != _canonical_sha256(
        raw_response
    ):
        raise ValueError("recorded provider review response hash drifted")
    try:
        content = raw_response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("recorded provider review response is incomplete") from error
    proposed_stratum, rationale = parse_review_response(content)
    if payload.get("rationale") != rationale or payload.get("response_model_name") != raw_response.get("model"):
        raise ValueError("recorded provider review response binding drifted")

    decision = build_candidate_stratum_review_decision(
        decision_id=review_id,
        rules_path=protocol.rules_path,
        nonexecution_receipt_path=nonexecution_receipt_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
        image_receipt_path=image_receipt_path,
        execution_receipt_path=execution_receipt_path,
        instance_id=candidate.instance_id,
        reviewer_identity_sha256=protocol.reviewer_identity_sha256,
        proposed_stratum=proposed_stratum,
        private_rationale_path=private_path,
    )
    public_decision_path = (
        protocol.public_output_root
        / f"{candidate.instance_id}.deepseek-v4-flash-reviewer-v1.json"
    )
    write_candidate_stratum_review_decision(
        decision=decision, output_path=public_decision_path
    )
    receipt_path = protocol.public_receipt_root / f"{candidate.instance_id}.json"
    _write_json_nonoverwriting(
        path=receipt_path,
        payload={
            "schema_version": "1.0",
            "receipt_id": review_id,
            "protocol_sha256": protocol.sha256,
            "prompt_sha256": protocol.prompt_sha256,
            "instance_id": candidate.instance_id,
            "reviewer_identity_sha256": protocol.reviewer_identity_sha256,
            "provider": "openai-compatible",
            "model_name": protocol.model_name,
            "temperature": protocol.temperature,
            "request_sha256": payload["request_sha256"],
            "response_sha256": payload["response_sha256"],
            "private_review_sha256": _file_sha256(private_path),
            "decision_sha256": _file_sha256(public_decision_path),
            "task_arm_outcomes_generated": False,
        },
    )
    return public_decision_path


def review_candidate(
    *,
    protocol: ProviderReviewProtocol,
    candidate: CandidateReviewInput,
    api_key: str,
    nonexecution_receipt_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
    execution_receipt_path: Path,
    post: Callable[..., Any] = requests.post,
) -> Path:
    """Make exactly one reviewer call and persist redacted public bindings."""

    if not api_key.strip():
        raise ValueError("provider API key is empty")
    request_payload = build_review_request(protocol=protocol, candidate=candidate)
    request_sha256 = _canonical_sha256(request_payload)
    review_id = f"core-benefit-screen-review-{candidate.instance_id}-deepseek-v4-flash-v1"
    private_path = protocol.private_output_root / f"{candidate.instance_id}.json"
    failure_private_path = protocol.private_output_root / f"{candidate.instance_id}.failed.json"
    failure_receipt_path = protocol.public_receipt_root / f"{candidate.instance_id}.failed.json"
    response_payload: Any | None = None
    try:
        response = post(
            f"{protocol.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=request_payload,
            timeout=120,
        )
        response.raise_for_status()
        response_payload = response.json()
        content = response_payload["choices"][0]["message"]["content"]
        proposed_stratum, rationale = parse_review_response(content)
    except Exception as error:
        _write_json_nonoverwriting(
            path=failure_private_path,
            payload={
                "schema_version": "1.0",
                "review_id": review_id,
                "protocol_sha256": protocol.sha256,
                "prompt_sha256": protocol.prompt_sha256,
                "instance_id": candidate.instance_id,
                "request_sha256": request_sha256,
                "candidate_input": candidate.as_payload(),
                "raw_response": response_payload,
                "error_type": type(error).__name__,
            },
        )
        _write_json_nonoverwriting(
            path=failure_receipt_path,
            payload={
                "schema_version": "1.0",
                "receipt_id": review_id,
                "protocol_sha256": protocol.sha256,
                "prompt_sha256": protocol.prompt_sha256,
                "instance_id": candidate.instance_id,
                "reviewer_identity_sha256": protocol.reviewer_identity_sha256,
                "request_sha256": request_sha256,
                "private_review_sha256": _file_sha256(failure_private_path),
                "review_status": "failed_no_retry",
                "task_arm_outcomes_generated": False,
            },
        )
        raise RuntimeError("provider review attempt failed and was recorded without retry") from error
    response_sha256 = _canonical_sha256(response_payload)
    private_payload = {
        "schema_version": "1.0",
        "review_id": review_id,
        "protocol_sha256": protocol.sha256,
        "prompt_sha256": protocol.prompt_sha256,
        "instance_id": candidate.instance_id,
        "request_sha256": request_sha256,
        "response_sha256": response_sha256,
        "model_name": protocol.model_name,
        "response_model_name": response_payload.get("model"),
        "candidate_input": candidate.as_payload(),
        "raw_response": response_payload,
        "rationale": rationale,
    }
    _write_json_nonoverwriting(path=private_path, payload=private_payload)
    return materialize_recorded_review(
        protocol=protocol,
        private_path=private_path,
        nonexecution_receipt_path=nonexecution_receipt_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
        image_receipt_path=image_receipt_path,
        execution_receipt_path=execution_receipt_path,
    )
