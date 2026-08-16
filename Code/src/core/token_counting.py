"""Provider-aware exact token counting for pre-request context budgets."""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx


DEEPSEEK_TOKENIZER_URL = "https://cdn.deepseek.com/api-docs/deepseek_v3_tokenizer.zip"
DEEPSEEK_TOKENIZER_MEMBER = "deepseek_v3_tokenizer/tokenizer.json"
DEEPSEEK_TOKENIZER_ID = "deepseek-official-api-tokenizer"
DEEPSEEK_TOKENIZER_SHA256 = "ecb6f9fc369894346f0511f4074ca75cee5cd5f3b06d02f1ba35fcd39f8e121d"
MAX_TOKENIZER_DOWNLOAD_BYTES = 12_000_000


def default_deepseek_tokenizer_path() -> Path:
    return Path.home() / ".cache" / "openpilot" / "tokenizers" / "deepseek" / "tokenizer.json"


class ProviderTokenCounter:
    """Count text with a locally available provider tokenizer; never estimate."""

    def __init__(
        self,
        *,
        tokenizer: Any | None,
        tokenizer_id: str,
        model: str,
        encoding_kind: str = "tokenizers",
    ) -> None:
        self._tokenizer = tokenizer
        self.tokenizer_id = tokenizer_id
        self.model = model
        self._encoding_kind = encoding_kind

    @property
    def available(self) -> bool:
        return self._tokenizer is not None

    def count_text(self, text: str) -> int:
        if self._tokenizer is None:
            raise RuntimeError("provider tokenizer is unavailable")
        if self._encoding_kind == "tiktoken":
            return len(self._tokenizer.encode(str(text)))
        return len(self._tokenizer.encode(str(text), add_special_tokens=False).ids)

    @classmethod
    def from_settings(cls, settings: Any) -> "ProviderTokenCounter":
        model = str(getattr(settings, "model", "") or "")
        base_url = str(getattr(settings, "base_url", "") or "")
        configured_path = str(getattr(settings, "tokenizer_path", "") or "").strip()
        is_deepseek = model.lower().startswith("deepseek") or "deepseek.com" in base_url.lower()
        if not is_deepseek:
            return cls._from_openai_tiktoken(settings)
        path = Path(configured_path).expanduser() if configured_path else default_deepseek_tokenizer_path()
        tokenizer_id = f"configured-tokenizer:{path.name}" if configured_path else DEEPSEEK_TOKENIZER_ID
        try:
            from tokenizers import Tokenizer

            if path.is_file() and (
                configured_path or hashlib.sha256(path.read_bytes()).hexdigest() == DEEPSEEK_TOKENIZER_SHA256
            ):
                tokenizer = Tokenizer.from_file(str(path))
            else:
                tokenizer = None
        except (ImportError, OSError, ValueError):
            tokenizer = None
        return cls(
            tokenizer=tokenizer,
            tokenizer_id=tokenizer_id if tokenizer is not None else "unavailable",
            model=model,
        )

    @classmethod
    def _from_openai_tiktoken(cls, settings: Any) -> "ProviderTokenCounter":
        """Load an exact local tiktoken encoding for a known OpenAI model."""

        profile = getattr(settings, "reasoning_capability_profile", None)
        profile_value = getattr(profile, "value", profile)
        if str(profile_value or "") not in {
            "openai-chat-known",
            "openai-chat-no-reasoning-known",
        }:
            return cls(tokenizer=None, tokenizer_id="unavailable", model=str(getattr(settings, "model", "") or ""))
        model = str(getattr(settings, "model", "") or "")
        try:
            import tiktoken

            encoding = tiktoken.encoding_for_model(model)
        except (ImportError, KeyError, ValueError):
            return cls(tokenizer=None, tokenizer_id="unavailable", model=model)
        return cls(
            tokenizer=encoding,
            tokenizer_id=f"tiktoken:{encoding.name}",
            model=model,
            encoding_kind="tiktoken",
        )


def install_deepseek_tokenizer(destination: str | Path | None = None) -> Path:
    """Install DeepSeek's official offline tokenizer into the local runtime cache."""
    target = Path(destination).expanduser() if destination else default_deepseek_tokenizer_path()
    response = httpx.get(DEEPSEEK_TOKENIZER_URL, follow_redirects=True, timeout=30.0)
    response.raise_for_status()
    if len(response.content) > MAX_TOKENIZER_DOWNLOAD_BYTES:
        raise ValueError("DeepSeek tokenizer download exceeds the allowed size")
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        info = archive.getinfo(DEEPSEEK_TOKENIZER_MEMBER)
        if info.file_size > MAX_TOKENIZER_DOWNLOAD_BYTES:
            raise ValueError("DeepSeek tokenizer artifact exceeds the allowed size")
        tokenizer_bytes = archive.read(info)

    if hashlib.sha256(tokenizer_bytes).hexdigest() != DEEPSEEK_TOKENIZER_SHA256:
        raise ValueError("DeepSeek tokenizer checksum does not match the reviewed official artifact")

    from tokenizers import Tokenizer

    Tokenizer.from_str(tokenizer_bytes.decode("utf-8"))
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".tokenizer-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(tokenizer_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).replace(target)
    finally:
        temporary_path = Path(temporary_name)
        if temporary_path.exists():
            temporary_path.unlink()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Install an official provider tokenizer")
    parser.add_argument("--install-deepseek", action="store_true")
    parser.add_argument("--destination", default="")
    args = parser.parse_args()
    if not args.install_deepseek:
        parser.error("select --install-deepseek")
    path = install_deepseek_tokenizer(args.destination or None)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
