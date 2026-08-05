from __future__ import annotations

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
