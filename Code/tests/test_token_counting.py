from __future__ import annotations

import sys
from types import SimpleNamespace

from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from core.token_counting import ProviderTokenCounter


def test_provider_token_counter_loads_configured_deepseek_tokenizer(tmp_path) -> None:
    tokenizer = Tokenizer(WordLevel(vocab={"[UNK]": 0, "hello": 1, "world": 2}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer_path = tmp_path / "tokenizer.json"
    tokenizer.save(str(tokenizer_path))
    counter = ProviderTokenCounter.from_settings(
        SimpleNamespace(
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            tokenizer_path=str(tokenizer_path),
        )
    )

    assert counter.available is True
    assert counter.count_text("hello world") == 2
    assert counter.tokenizer_id == "configured-tokenizer:tokenizer.json"


def test_provider_token_counter_never_estimates_when_tokenizer_is_missing(tmp_path) -> None:
    counter = ProviderTokenCounter.from_settings(
        SimpleNamespace(
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            tokenizer_path=str(tmp_path / "missing.json"),
        )
    )

    assert counter.available is False


def test_provider_token_counter_uses_explicit_openai_profile_with_tiktoken(monkeypatch) -> None:
    class _Encoding:
        name = "o200k_base"

        def encode(self, text: str) -> list[int]:
            return list(range(len(text.split())))

    fake_tiktoken = SimpleNamespace(
        encoding_for_model=lambda model: _Encoding()
    )
    monkeypatch.setitem(sys.modules, "tiktoken", fake_tiktoken)

    counter = ProviderTokenCounter.from_settings(
        SimpleNamespace(
            model="gpt-4o-mini",
            base_url="https://api.openai.com/v1",
            provider="openai",
            reasoning_capability_profile="openai-chat-known",
            tokenizer_path=None,
        )
    )

    assert counter.available is True
    assert counter.count_text("hello world") == 2
    assert counter.tokenizer_id == "tiktoken:o200k_base"


def test_provider_token_counter_accepts_openai_no_reasoning_profile_with_tiktoken(monkeypatch) -> None:
    class _Encoding:
        name = "o200k_base"

        def encode(self, text: str) -> list[int]:
            return list(range(len(text.split())))

    monkeypatch.setitem(
        sys.modules,
        "tiktoken",
        SimpleNamespace(encoding_for_model=lambda _model: _Encoding()),
    )

    counter = ProviderTokenCounter.from_settings(
        SimpleNamespace(
            model="gpt-4o-mini",
            base_url="https://api.openai.com/v1",
            provider="openai",
            reasoning_capability_profile="openai-chat-no-reasoning-known",
            tokenizer_path=None,
        )
    )

    assert counter.available is True
    assert counter.count_text("hello world") == 2
    assert counter.tokenizer_id == "tiktoken:o200k_base"


def test_provider_token_counter_blocks_unknown_openai_model_without_guessing(monkeypatch) -> None:
    def unknown_model(_model: str):
        raise KeyError("unknown model")

    monkeypatch.setitem(
        sys.modules,
        "tiktoken",
        SimpleNamespace(encoding_for_model=unknown_model),
    )

    counter = ProviderTokenCounter.from_settings(
        SimpleNamespace(
            model="custom-openai-model",
            base_url="https://api.openai.com/v1",
            provider="openai",
            reasoning_capability_profile="openai-chat-known",
            tokenizer_path=None,
        )
    )

    assert counter.available is False
    assert counter.tokenizer_id == "unavailable"
