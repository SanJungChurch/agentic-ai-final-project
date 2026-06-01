from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SPLIT_TO_FILENAME = {
    "train": "kvret_train_public.json",
    "dev": "kvret_dev_public.json",
    "test": "kvret_test_public.json",
}


def load_kvret_rows(root: Path, *, split: str = "test", limit: int | None = None) -> list[dict[str, Any]]:
    """Convert KVRET calendar scheduling dialogues into benchmark rows."""
    if split not in SPLIT_TO_FILENAME:
        raise ValueError(f"Unsupported KVRET split: {split}. Use one of {sorted(SPLIT_TO_FILENAME)}")

    path = root / SPLIT_TO_FILENAME[split]
    if not path.exists():
        raise FileNotFoundError(f"KVRET split file not found: {path}")

    items = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []

    for item in items:
        if item.get("scenario", {}).get("task", {}).get("intent") != "schedule":
            continue

        row = build_kvret_row(item, split=split)
        if row:
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break

    return rows


def build_kvret_row(item: dict[str, Any], *, split: str) -> dict[str, Any] | None:
    slots = aggregate_slots(item.get("dialogue", []))
    if not slots:
        return None

    uuid = item.get("scenario", {}).get("uuid", "unknown")
    participants = _as_list(slots.get("party"))
    candidate_times = build_candidate_times(slots)
    location = _first(slots.get("room"))
    event = _first(slots.get("event"))
    agenda = _first(slots.get("agenda"))

    return {
        "id": f"kvret_{split}_{uuid}",
        "source": f"kvret_{split}_public.json",
        "domain": "schedule",
        "text": build_dialogue_text(item.get("dialogue", [])),
        "gold": {
            "intent": "schedule_meeting",
            "participants": participants,
            "candidate_times": [
                {
                    "participant": None,
                    "expression": expression,
                    "normalized_start": None,
                    "normalized_end": None,
                    "availability": "unknown",
                }
                for expression in candidate_times
            ],
            "unavailable_times": [],
            "location_preference": location,
            "meeting_duration_minutes": None,
            "missing_information": [],
            "confidence": 1.0,
            "source_summary": _summary(event, agenda),
            "_official_gold_mapping": {
                "participants": "party",
                "candidate_times": ["date", "time"],
                "location_preference": "room",
                "event": "event",
                "agenda": "agenda",
            },
        },
    }


def aggregate_slots(dialogue: list[dict[str, Any]]) -> dict[str, list[str]]:
    slots: dict[str, list[str]] = {}
    for turn in dialogue:
        turn_slots = turn.get("data", {}).get("slots")
        if not isinstance(turn_slots, dict):
            continue

        for key, value in turn_slots.items():
            for item in _as_list(value):
                slots.setdefault(key, [])
                if item not in slots[key]:
                    slots[key].append(item)

    return slots


def build_candidate_times(slots: dict[str, list[str]]) -> list[str]:
    dates = _as_list(slots.get("date"))
    times = _as_list(slots.get("time"))

    if dates and times:
        return [f"{date} {time}" for date in dates for time in times]
    if dates:
        return dates
    return times


def build_dialogue_text(dialogue: list[dict[str, Any]]) -> str:
    lines = []
    for turn in dialogue:
        speaker = turn.get("turn", "unknown")
        utterance = turn.get("data", {}).get("utterance")
        if utterance:
            lines.append(f"{speaker}: {utterance}")
    return "\n".join(lines)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _first(value: Any) -> str | None:
    values = _as_list(value)
    return values[0] if values else None


def _summary(event: str | None, agenda: str | None) -> str:
    parts = []
    if event:
        parts.append(f"event={event}")
    if agenda:
        parts.append(f"agenda={agenda}")
    return "; ".join(parts)
