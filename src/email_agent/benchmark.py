from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from .evaluate import _f1, _slot_set
from .extractor import extract_constraints, parse_email_thread


def run_benchmark(path: Path) -> dict[str, Any]:
    rows = [_evaluate_case(item) for item in _read_jsonl(path)]

    return {
        "benchmark": str(path),
        "num_cases": len(rows),
        "macro_participant_exact_match": _avg_bool(rows, "participant_exact_match"),
        "macro_candidate_time_f1": _avg_float(rows, "candidate_time_f1"),
        "macro_unavailable_time_f1": _avg_float(rows, "unavailable_time_f1"),
        "macro_location_detection": _avg_bool(rows, "location_detected"),
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the week 1 event extraction benchmark.")
    parser.add_argument(
        "--benchmark",
        default="data/benchmarks/week1_event_extraction.jsonl",
        help="Path to a JSONL benchmark file.",
    )
    args = parser.parse_args()

    result = run_benchmark(Path(args.benchmark))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _evaluate_case(item: dict[str, Any]) -> dict[str, Any]:
    prediction = extract_constraints(parse_email_thread(item["text"])).model_dump()
    gold = item["gold"]

    pred_participants = set(prediction["participants"])
    gold_participants = set(gold["participants"])

    pred_candidates = _slot_set(prediction["candidate_times"])
    gold_candidates = _slot_set(gold["candidate_times"])
    pred_unavailable = _slot_set(prediction["unavailable_times"])
    gold_unavailable = _slot_set(gold["unavailable_times"])

    return {
        "id": item["id"],
        "source": item.get("source", "unknown"),
        "participant_exact_match": pred_participants == gold_participants,
        "candidate_time_f1": _f1(pred_candidates, gold_candidates),
        "unavailable_time_f1": _f1(pred_unavailable, gold_unavailable),
        "location_detected": bool(prediction.get("location_preference")) == bool(gold.get("location_preference")),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _avg_bool(rows: list[dict[str, Any]], key: str) -> float:
    return round(mean(1.0 if row[key] else 0.0 for row in rows), 4) if rows else 0.0


def _avg_float(rows: list[dict[str, Any]], key: str) -> float:
    return round(mean(float(row[key]) for row in rows), 4) if rows else 0.0


if __name__ == "__main__":
    main()
