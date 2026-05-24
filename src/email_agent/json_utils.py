from __future__ import annotations

import json
import re
from typing import Any


FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = _strip_markdown_fence(text).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = json.loads(_extract_first_json_object(cleaned))

    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object.")
    return data


def _strip_markdown_fence(text: str) -> str:
    match = FENCED_JSON_RE.search(text)
    return match.group(1) if match else text


def _extract_first_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in LLM response.")
    return text[start : end + 1]
