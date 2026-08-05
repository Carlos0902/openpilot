"""Run the frozen, outcome-blind mechanism-review plane only."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .core_benefit_screen import (
    CoreBenefitScreenMechanismReview,
    write_core_benefit_screen_artifact,
)


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("mechanism review artifact must be a regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class MechanismReviewProviderProtocol:
    path: Path
    base_url: str
    model_name: str
    temperature: float
    max_output_tokens: int
    api_key_environment_variable: str
    reviewer_identity_sha256: str
    prompt_path: Path
    prompt_sha256: str
    private_output_root: Path
    public_output_root: Path
    task_arm_provider_execution_authorized: bool

    @classmethod
    def load(cls, path: Path) -> "MechanismReviewProviderProtocol":
        if path.is_symlink() or not path.is_file():
            raise ValueError("mechanism review protocol must be a regular file")
        payload = json.loads(path.read_text(encoding="utf-8"))
        provider = payload.get("provider")
        if (
            payload.get("schema_version") != "1.0"
            or payload.get("lifecycle") != "pre-execution-review"
            or payload.get("review_provider_execution_authorized") is not True
            or payload.get("task_arm_provider_execution_authorized") is not False
            or payload.get("task_outcomes_generated") is not False
            or payload.get("production_execution_authorized") is not False
            or payload.get("hypothesis_evidence_eligible") is not False
            or not isinstance(provider, dict)
            or provider.get("request_attempts_per_candidate") != 1
            or provider.get("separate_request_per_candidate") is not True
            or provider.get("stream") is not False
            or provider.get("thinking") != {"type": "disabled"}
        ):
            raise ValueError("mechanism review protocol has an unsafe contract")
        root = path.parent
        prompt_path = root / payload["prompt_file"]
        if _sha256(prompt_path) != payload["prompt_sha256"]:
            raise ValueError("mechanism review prompt hash drifted")
        for field in ("prompt_sha256", "reviewer_identity_sha256"):
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
            reviewer_identity_sha256=payload["reviewer_identity_sha256"],
            prompt_path=prompt_path,
            prompt_sha256=payload["prompt_sha256"],
            private_output_root=(root / payload["private_output_root"]).resolve(),
            public_output_root=(root / payload["public_output_root"]).resolve(),
            task_arm_provider_execution_authorized=False,
        )


def parse_mechanism_review_response(content: str) -> tuple[bool, bool, bool, str]:
    payload = json.loads(content)
    expected = {
        "diagnostic_measurement",
        "post_action_validation",
        "recovery_or_safe_stop",
        "rationale",
    }
    if set(payload) != expected:
        raise ValueError("mechanism review response has an unsupported schema")
    diagnostic = payload["diagnostic_measurement"]
    validation = payload["post_action_validation"]
    recovery = payload["recovery_or_safe_stop"]
    rationale = payload["rationale"]
    if (
        not all(isinstance(value, bool) for value in (diagnostic, validation, recovery))
        or not (diagnostic or validation or recovery)
        or not isinstance(rationale, str)
        or not rationale.strip()
        or len(rationale.split()) > 120
    ):
        raise ValueError("mechanism review response is incomplete")
    return diagnostic, validation, recovery, rationale.strip()


def review_mechanism_candidate(
    *,
    protocol: MechanismReviewProviderProtocol,
    instance_id: str,
    selection_rank: int,
    candidate_input: dict[str, str],
    first_review_path: Path,
    second_review_path: Path,
    api_key: str,
) -> Path:
    """Issue one request and write a private raw response plus public receipt."""

    if set(candidate_input) != {"instance_id", "problem_statement", "patch", "test_patch"}:
        raise ValueError("mechanism review input must use the frozen field set")
    if candidate_input["instance_id"] != instance_id or not all(candidate_input.values()):
        raise ValueError("mechanism review input binding drifted")
    private_path = protocol.private_output_root / f"{instance_id}.json"
    public_path = protocol.public_output_root / f"{instance_id}.json"
    if any(path.exists() or path.is_symlink() for path in (private_path, public_path)):
        raise FileExistsError(f"mechanism review already recorded for {instance_id}")
    response = requests.post(
        f"{protocol.base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": protocol.model_name,
            "temperature": protocol.temperature,
            "max_tokens": protocol.max_output_tokens,
            "stream": False,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": protocol.prompt_path.read_text(encoding="utf-8")},
                {"role": "user", "content": json.dumps(candidate_input, sort_keys=True, separators=(",", ":"))},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    raw_response = response.json()
    try:
        content = raw_response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("mechanism provider response is incomplete") from error
    diagnostic, validation, recovery, rationale = parse_mechanism_review_response(content)
    private_payload = {
        "schema_version": "1.0",
        "instance_id": instance_id,
        "protocol_sha256": _sha256(protocol.path),
        "prompt_sha256": protocol.prompt_sha256,
        "candidate_input": candidate_input,
        "raw_response": raw_response,
        "rationale": rationale,
    }
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_text(json.dumps(private_payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    receipt = CoreBenefitScreenMechanismReview(
        schema_version="1.0",
        review_id=f"core-benefit-screen-mechanism-{instance_id}-deepseek-v4-flash-v1",
        instance_id=instance_id,
        selection_rank=selection_rank,
        first_review_sha256=_sha256(first_review_path),
        second_review_sha256=_sha256(second_review_path),
        adjudication_sha256=None,
        reviewer_identity_sha256=protocol.reviewer_identity_sha256,
        diagnostic_measurement=diagnostic,
        post_action_validation=validation,
        recovery_or_safe_stop=recovery,
        private_rationale_sha256=_sha256(private_path),
        blind_to_future_arm_outcomes=True,
        agent_arm_outcomes_available=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )
    write_core_benefit_screen_artifact(artifact=receipt, output_path=public_path)
    return public_path
