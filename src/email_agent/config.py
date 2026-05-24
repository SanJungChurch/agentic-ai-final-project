from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    gemini_model: str = "gemini-2.5-flash"


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    )
