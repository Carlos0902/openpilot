"""Shared provider specification and strict controller-model adapter."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import Usage
from .controller import (
    CONTROLLER_INSTRUCTIONS,
    ControllerFormatError,
    ControllerModelResult,
    ControllerProviderError,
)


class SharedProviderSpec(BaseModel):
    """Secret-free provider settings shared by ordinary and active arms."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model_name: str
    model_class: str
    base_url: str
    temperature: float = 0.0
    max_output_tokens: int = 2048
    cache_policy: str = "disabled"
    request_timeout_seconds: float = 60.0
    model_kwargs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_spec(self) -> "SharedProviderSpec":
        if not all(
            value.strip()
            for value in (
                self.provider,
                self.model_name,
                self.model_class,
                self.base_url,
                self.cache_policy,
            )
        ):
            raise ValueError("provider identity fields must not be blank")
        if self.max_output_tokens <= 0 or self.request_timeout_seconds <= 0:
            raise ValueError("provider limits must be positive")
        forbidden_fragments = ("key", "token", "secret", "password", "auth")
        unsafe_keys = sorted(
            key
            for key in self.model_kwargs
            if any(fragment in key.casefold() for fragment in forbidden_fragments)
        )
        if unsafe_keys:
            raise ValueError(
                f"secret-bearing model_kwargs are forbidden: {unsafe_keys}"
            )
        if self.cache_policy != "disabled":
            raise ValueError("phase-zero provider cache policy must be disabled")
        return self

    def request_kwargs(self) -> dict[str, Any]:
        return {
            "api_base": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "timeout": self.request_timeout_seconds,
            "num_retries": 0,
            "drop_params": True,
            "caching": False,
            **self.model_kwargs,
        }

    def agent_model_config(self) -> dict[str, Any]:
        return {
            "model_class": self.model_class,
            "model_kwargs": self.request_kwargs(),
        }

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()


def _response_dict(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json")
    raise ControllerFormatError("provider response is not serializable")


class LiteLLMControllerModel:
    """Issue one strict JSON controller request through the shared provider."""

    def __init__(
        self,
        *,
        spec: SharedProviderSpec,
        completion: Callable[..., Any] | None = None,
    ) -> None:
        if completion is None:
            import litellm

            completion = litellm.completion
        self.spec = spec
        self._completion = completion

    def query(self, *, payload: dict[str, Any]) -> ControllerModelResult:
        started = time.monotonic()
        try:
            response = self._completion(
                model=self.spec.model_name,
                messages=[
                    {"role": "system", "content": CONTROLLER_INSTRUCTIONS},
                    {
                        "role": "user",
                        "content": json.dumps(
                            payload,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                ],
                response_format={"type": "json_object"},
                tools=[],
                **self.spec.request_kwargs(),
            )
        except Exception as exc:
            raise ControllerProviderError(
                f"controller provider call failed: {type(exc).__name__}",
                usage=Usage(
                    provider_calls=1,
                    wall_time_seconds=max(0.0, time.monotonic() - started),
                ),
            ) from exc
        response_data = _response_dict(response)
        try:
            content = response_data["choices"][0]["message"]["content"]
            raw_usage = response_data["usage"]
            input_tokens = raw_usage.get(
                "input_tokens",
                raw_usage.get("prompt_tokens"),
            )
            output_tokens = raw_usage.get(
                "output_tokens",
                raw_usage.get("completion_tokens"),
            )
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ControllerProviderError(
                "provider response is missing content or usage",
                usage=Usage(
                    provider_calls=1,
                    wall_time_seconds=max(
                        0.0,
                        time.monotonic() - started,
                    ),
                ),
                response_metadata=response_data,
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ControllerProviderError(
                "provider returned empty controller content",
                usage=Usage(
                    provider_calls=1,
                    input_tokens=(
                        input_tokens if isinstance(input_tokens, int) else 0
                    ),
                    output_tokens=(
                        output_tokens if isinstance(output_tokens, int) else 0
                    ),
                    wall_time_seconds=max(
                        0.0,
                        time.monotonic() - started,
                    ),
                ),
                raw_response=content if isinstance(content, str) else "",
                response_metadata=response_data,
            )
        if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
            raise ControllerProviderError(
                "provider response is missing integer token usage",
                usage=Usage(
                    provider_calls=1,
                    wall_time_seconds=max(
                        0.0,
                        time.monotonic() - started,
                    ),
                ),
                raw_response=content,
                response_metadata=response_data,
            )
        return ControllerModelResult(
            content=content,
            usage=Usage(
                provider_calls=1,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                wall_time_seconds=max(0.0, time.monotonic() - started),
            ),
            response_metadata=response_data,
        )


class SharedProviderFactory:
    """Build both treatment surfaces from one frozen provider specification."""

    def __init__(
        self,
        *,
        spec: SharedProviderSpec,
        completion: Callable[..., Any] | None = None,
        agent_model_options: dict[str, Any] | None = None,
    ) -> None:
        self.spec = spec
        self._completion = completion
        self._agent_model_options = dict(agent_model_options or {})

    @property
    def fingerprint(self) -> str:
        return self.spec.fingerprint()

    def create_agent_model(self) -> Any:
        from minisweagent.models import get_model

        return get_model(
            input_model_name=self.spec.model_name,
            config={
                **self._agent_model_options,
                **self.spec.agent_model_config(),
            },
        )

    def create_controller_model(self) -> LiteLLMControllerModel:
        return LiteLLMControllerModel(
            spec=self.spec,
            completion=self._completion,
        )
