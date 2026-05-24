from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .extractor import extract_from_file


def evaluate_sample(sample_path: Path, gold_path: Path) -> dict[str, Any]:
    prediction = extract_from_file(sample_path).model_dump()
    gold = json.loads(gold_path.read_text(encoding="utf-8"))

    pred_participants = set(prediction["participants"])
    gold_participants = set(gold["participants"])

    pred_candidates = _slot_set(prediction["candidate_times"])
    gold_candidates = _slot_set(gold["candidate_times"])
    pred_unavailable = _slot_set(prediction["unavailable_times"])
    gold_unavailable = _slot_set(gold["unavailable_times"])

    return {
        "sample": str(sample_path),
        "participant_exact_match": pred_participants == gold_participants,
        "candidate_time_f1": _f1(pred_candidates, gold_candidates),
        "unavailable_time_f1": _f1(pred_unavailable, gold_unavailable),
        "location_detected": bool(prediction.get("location_preference")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate extractor output against a gold JSON file.")
    parser.add_argument("--sample", required=True, help="Path to an email thread text file.")
    parser.add_argument("--gold", required=True, help="Path to the matching gold JSON file.")
    args = parser.parse_args()

    metrics = evaluate_sample(Path(args.sample), Path(args.gold))
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


def _slot_set(items: list[dict[str, Any]]) -> set[tuple[str | None, str, str]]:
    return {
        (
            item.get("participant"),
            _normalize_expression(item.get("expression", "")),
            item.get("availability", "unknown"),
        )
        for item in items
    }


def _normalize_expression(value: str) -> str:
    return " ".join(value.split())


def _f1(predicted: set[Any], gold: set[Any]) -> float:
    if not predicted and not gold:
        return 1.0
    if not predicted or not gold:
        return 0.0

    true_positive = len(predicted & gold)
    precision = true_positive / len(predicted)
    recall = true_positive / len(gold)

    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)


if __name__ == "__main__":
    main()
