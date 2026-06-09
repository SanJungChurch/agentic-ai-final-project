from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "exaone"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:4b"
    exaone_model: str = "LGAI-EXAONE/EXAONE-4.0-1.2B"
    exaone_device_map: str = "auto"
    exaone_torch_dtype: str = "auto"
    exaone_max_new_tokens: int = 768


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "exaone"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=_ollama_model_from_env(),
        exaone_model=os.getenv("EXAONE_MODEL", "LGAI-EXAONE/EXAONE-4.0-1.2B"),
        exaone_device_map=os.getenv("EXAONE_DEVICE_MAP", "auto"),
        exaone_torch_dtype=os.getenv("EXAONE_TORCH_DTYPE", "auto"),
        exaone_max_new_tokens=_env_int("EXAONE_MAX_NEW_TOKENS", 768),
    )


def _ollama_model_from_env() -> str:
    return (
        os.getenv("OLLAMA_MODEL")
        or os.getenv("QWEN_MODEL")
        or os.getenv("QWEN4B_MODEL")
        or os.getenv("QWEN4BMODEL")
        or "qwen3:4b"
    )


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default
