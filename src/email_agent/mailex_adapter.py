from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


MEETING_EVENT_TYPES = {
    "Deliver_Meeting_Data",
    "Request_Meeting",
    "Request_Meeting_Data",
    "Amend_Meeting_Data",
}

ROLE_TO_FIELD = {
    "Meeting Members": "participants",
    "Meeting Date": "candidate_times",
    "Meeting Time": "candidate_times",
    "Meeting Location": "locations",
}


def load_mailex_rows(root: Path, *, split: str = "test", limit: int | None = None) -> list[dict[str, Any]]:
    """Convert local MailEx JSON files into the project's benchmark row format."""
    split_root = root / split
    if not split_root.exists():
        raise FileNotFoundError(f"MailEx split folder not found: {split_root}")

    rows: list[dict[str, Any]] = []
    for path in sorted(split_root.rglob("*.json")):
        row = build_mailex_row(path, split_root)
        if row:
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break

    return rows


def build_mailex_row(path: Path, split_root: Path) -> dict[str, Any] | None:
    obj = json.loads(path.read_text(encoding="utf-8"))
    events = obj.get("events")
    sentences = obj.get("sentences")

    if not isinstance(events, dict):
        return None

    participants: list[str] = []
    candidate_times: list[str] = []
    locations: list[str] = []
    event_types: list[str] = []

    for turn_id, event_payloads in events.items():
        if not isinstance(event_payloads, dict):
            continue

        tokens = get_tokens_for_turn(sentences, str(turn_id))
        if not tokens:
            continue

        for event_type, payload in event_payloads.items():
            if event_type not in MEETING_EVENT_TYPES or not isinstance(payload, dict):
                continue

            event_types.append(event_type)
            for labels in normalize_label_sequences(payload.get("labels")):
                spans = extract_spans(tokens, labels)
                _add_unique(participants, spans["participants"])
                _add_unique(candidate_times, spans["candidate_times"])
                _add_unique(locations, spans["locations"])

    if not participants and not candidate_times and not locations:
        return None

    source_file = str(path.relative_to(split_root))
    item_id = "mailex_" + source_file.replace("\\", "_").replace("/", "_").replace(".json", "")

    return {
        "id": item_id,
        "source": source_file,
        "event_types": sorted(set(event_types)),
        "text": build_text(sentences),
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
            "location_preference": "; ".join(locations) if locations else None,
            "meeting_duration_minutes": None,
            "missing_information": [],
            "confidence": 1.0,
            "source_summary": "",
            "_official_gold_mapping": {
                "participants": "Meeting Members",
                "candidate_times": ["Meeting Date", "Meeting Time"],
                "location_preference": "Meeting Location",
            },
        },
    }


def build_text(sentences: Any) -> str:
    if isinstance(sentences, list):
        return "\n".join(join_tokens(flatten_tokens(sentence)) for sentence in sentences)

    if isinstance(sentences, dict):
        parts = []
        for key in sorted(sentences.keys(), key=_sort_key):
            parts.append(join_tokens(flatten_tokens(sentences[key])))
        return "\n".join(part for part in parts if part)

    return ""


def get_tokens_for_turn(sentences: Any, turn_id: str) -> list[str]:
    turn = get_turn(sentences, turn_id)
    return flatten_tokens(turn)


def get_turn(sentences: Any, turn_id: str) -> Any:
    idx = turn_id_to_index(turn_id)

    if isinstance(sentences, list) and idx is not None and 0 <= idx < len(sentences):
        return sentences[idx]

    if isinstance(sentences, dict):
        if turn_id in sentences:
            return sentences[turn_id]
        if idx is not None and str(idx) in sentences:
            return sentences[str(idx)]

    return None


def turn_id_to_index(turn_id: str) -> int | None:
    if str(turn_id).isdigit():
        return int(turn_id)

    match = re.search(r"(\d+)$", str(turn_id))
    return int(match.group(1)) if match else None


def flatten_tokens(obj: Any) -> list[str]:
    if obj is None:
        return []
    if isinstance(obj, str):
        return obj.split()
    if isinstance(obj, (int, float)):
        return [str(obj)]
    if isinstance(obj, list):
        tokens: list[str] = []
        for item in obj:
            tokens.extend(flatten_tokens(item))
        return tokens
    if isinstance(obj, dict):
        for key in ["tokens", "words", "sentence", "text"]:
            if key in obj:
                return flatten_tokens(obj[key])

        tokens: list[str] = []
        for value in obj.values():
            tokens.extend(flatten_tokens(value))
        return tokens

    return []


def normalize_label_sequences(labels: Any) -> list[list[Any]]:
    if not isinstance(labels, list):
        return []
    if labels and all(isinstance(item, str) for item in labels):
        return [labels]
    return [item for item in labels if isinstance(item, list)]


def extract_spans(tokens: list[str], labels: list[Any]) -> dict[str, list[str]]:
    output = {
        "participants": [],
        "candidate_times": [],
        "locations": [],
    }
    current_role: str | None = None
    current_tokens: list[str] = []

    def flush() -> None:
        nonlocal current_role, current_tokens
        if current_role and current_tokens:
            field = ROLE_TO_FIELD[current_role]
            span = join_tokens(current_tokens)
            if span:
                _add_unique(output[field], [span])
        current_role = None
        current_tokens = []

    for token, label in zip(tokens, labels):
        parsed = parse_bio_label(label)
        if not parsed:
            flush()
            continue

        bio, role = parsed
        if bio == "B" or current_role != role:
            flush()
            current_role = role
            current_tokens = [str(token)]
        else:
            current_tokens.append(str(token))

    flush()
    return output


def parse_bio_label(raw_label: Any) -> tuple[str, str] | None:
    if raw_label is None:
        return None

    label = str(raw_label).strip()
    if not label or label == "O":
        return None

    match = re.search(r"([BI])-([^:]+)$", label)
    if not match:
        return None

    bio = match.group(1)
    role = match.group(2).strip()
    if role not in ROLE_TO_FIELD:
        return None

    return bio, role


def join_tokens(tokens: list[str]) -> str:
    text = " ".join(str(token) for token in tokens)
    replacements = {
        " ,": ",",
        " .": ".",
        " ?": "?",
        " !": "!",
        " :": ":",
        " ;": ";",
        "( ": "(",
        " )": ")",
        " '": "'",
        " n't": "n't",
        " 's": "'s",
        " 'm": "'m",
        " 're": "'re",
        " 'll": "'ll",
        " 've": "'ve",
        " 'd": "'d",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return " ".join(text.split())


def _add_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        text = str(value).strip()
        if text and text not in target:
            target.append(text)


def _sort_key(value: Any) -> Any:
    text = str(value)
    return int(text) if text.isdigit() else text
