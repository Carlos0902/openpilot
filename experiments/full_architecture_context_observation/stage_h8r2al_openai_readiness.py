"""Zero-transport OpenAI readiness gate for the next cross-provider lane."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from core.config import LLMSettings, ProviderToolExecutionBudget
from core.reasoning import select_reasoning_capability_profile
from core.token_counting import ProviderTokenCounter
from core.provider_lane import (
    OPENAI_GPT4O_MINI_LANE,
    credential_from_env,
    settings_for_lane,
    validate_lane_settings,
)


SCHEMA = "phase-h8r2al-openai-readiness-v1"
ZERO_SIDE_EFFECTS = {
    "provider_transport_attempted": False,
    "provider_calls": 0,
    "network_side_effects": 0,
    "project_mutations": 0,
    "memory_mutations": 0,
    "writer_actions": 0,
    "command_actions": 0,
    "verification_runs": 0,
}
SECRET_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9-]{20,}(?![A-Za-z0-9])", re.IGNORECASE)


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _settings() -> LLMSettings:
    settings = settings_for_lane(
        OPENAI_GPT4O_MINI_LANE,
        credential=credential_from_env(OPENAI_GPT4O_MINI_LANE),
    )
    validate_lane_settings(OPENAI_GPT4O_MINI_LANE, settings)
    return settings


def build_readiness(*, settings: LLMSettings, tokenizer: ProviderTokenCounter) -> dict[str, Any]:
    profile = select_reasoning_capability_profile(settings)
    budget = ProviderToolExecutionBudget.for_profile(OPENAI_GPT4O_MINI_LANE.budget_profile)
    credential_present = bool(settings.api_key and settings.api_key.strip())
    tokenizer_identity_ok = (
        not tokenizer.available
        or tokenizer.tokenizer_id == OPENAI_GPT4O_MINI_LANE.tokenizer_id
    )
    status = "passed" if tokenizer.available and tokenizer_identity_ok and credential_present else "typed_blocked"
    blockers: list[str] = []
    if not tokenizer.available:
        blockers.append("tokenizer_unavailable")
    elif not tokenizer_identity_ok:
        blockers.append("tokenizer_identity_mismatch")
    if not credential_present:
        blockers.append("missing_credentials")
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "status": status,
        "claim_boundary": "openai_identity_tokenizer_reasoning_readiness_no_transport",
        "provider": {
            "provider": settings.provider,
            "endpoint": settings.base_url.rstrip("/"),
            "model": settings.model,
            "capability_profile": f"{profile.profile_id.value}:{profile.version}",
            "credential_present": credential_present,
            "credential_serialized": False,
            "credential_fingerprint_sha256": hashlib.sha256(settings.api_key.encode("utf-8")).hexdigest() if credential_present else None,
        },
        "lane_id": OPENAI_GPT4O_MINI_LANE.lane_id,
        "reasoning": {"requested_mode": "disabled", "resolved_mode": "disabled"},
        "tokenizer": {"available": tokenizer.available, "tokenizer_id": tokenizer.tokenizer_id, "model": tokenizer.model},
        "budget": budget.__dict__,
        "blockers": blockers,
        "side_effects": dict(ZERO_SIDE_EFFECTS),
        "secret_handling": {"provider_scoped_env_only": True, "serialized": False, "command_line": False, "receipt_value": False},
    }
    receipt["readiness_hash"] = canonical_hash(receipt)
    return receipt


def validate_readiness(receipt: dict[str, Any]) -> dict[str, Any]:
    if receipt.get("schema") != SCHEMA or receipt.get("status") not in {"passed", "typed_blocked"}:
        raise ValueError("OpenAI readiness schema/status mismatch")
    if receipt.get("readiness_hash") != canonical_hash({key: value for key, value in receipt.items() if key != "readiness_hash"}):
        raise ValueError("OpenAI readiness hash mismatch")
    provider = receipt.get("provider") or {}
    if receipt.get("lane_id") != OPENAI_GPT4O_MINI_LANE.lane_id:
        raise ValueError("OpenAI lane identity mismatch")
    if (
        provider.get("provider") != OPENAI_GPT4O_MINI_LANE.provider
        or provider.get("endpoint") != OPENAI_GPT4O_MINI_LANE.endpoint
        or provider.get("model") != OPENAI_GPT4O_MINI_LANE.model
    ):
        raise ValueError("OpenAI identity mismatch")
    if provider.get("capability_profile") != "openai-chat-no-reasoning-known:v1" or provider.get("credential_serialized") is not False:
        raise ValueError("OpenAI capability/credential contract failed")
    if receipt.get("reasoning") != {"requested_mode": "disabled", "resolved_mode": "disabled"}:
        raise ValueError("OpenAI reasoning resolution contract failed")
    if receipt.get("status") == "passed" and (not provider.get("credential_present") or not receipt.get("tokenizer", {}).get("available")):
        raise ValueError("OpenAI passed readiness is missing required capability state")
    if receipt.get("status") == "passed" and receipt.get("tokenizer", {}).get("tokenizer_id") != OPENAI_GPT4O_MINI_LANE.tokenizer_id:
        raise ValueError("OpenAI tokenizer identity mismatch")
    if receipt.get("side_effects") != ZERO_SIDE_EFFECTS:
        raise ValueError("OpenAI readiness side effects are not zero")
    if SECRET_RE.search(json.dumps(receipt, ensure_ascii=False)):
        raise ValueError("OpenAI readiness contains a secret-shaped value")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = _settings()
    tokenizer = ProviderTokenCounter.from_settings(settings)
    receipt = build_readiness(settings=settings, tokenizer=tokenizer)
    args.output.parent.mkdir(parents=True, exist_ok=False)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    validate_readiness(receipt)
    print(json.dumps({"status": receipt["status"], "readiness": str(args.output.resolve()), "readiness_hash": receipt["readiness_hash"], "provider_transport_attempted": False, "credential_present": receipt["provider"]["credential_present"], "blockers": receipt["blockers"]}, ensure_ascii=False, indent=2))
    return 0 if receipt["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
