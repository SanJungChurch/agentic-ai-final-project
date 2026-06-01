from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from statistics import mean
from typing import Any, Protocol

from .extractor import parse_email_thread
from .kvret_adapter import load_kvret_rows
from .llm_extractor import GeminiConstraintExtractor
from .mailex_adapter import load_mailex_rows
from .schema import EmailThread, ExtractionResult


class ConstraintExtractor(Protocol):
    def extract(
        self,
        thread: EmailThread,
        *,
        reference_date: str | None = None,
        timezone: str = "Asia/Seoul",
    ) -> ExtractionResult:
        ...


def run_llm_benchmark(
    benchmark_path: Path,
    *,
    limit: int = 10,
    offset: int = 0,
    reference_date: str | None = None,
    timezone: str = "Asia/Seoul",
    extractor: ConstraintExtractor | None = None,
    sleep_seconds: float = 0.0,
) -> dict[str, Any]:
    rows = _read_jsonl(benchmark_path)[offset : offset + limit]
    return run_llm_benchmark_rows(
        rows,
        benchmark_name=str(benchmark_path),
        limit=limit,
        offset=offset,
        reference_date=reference_date,
        timezone=timezone,
        extractor=extractor,
        sleep_seconds=sleep_seconds,
    )


def run_mailex_llm_benchmark(
    mailex_root: Path,
    *,
    split: str = "test",
    limit: int = 10,
    offset: int = 0,
    reference_date: str | None = None,
    timezone: str = "Asia/Seoul",
    extractor: ConstraintExtractor | None = None,
    sleep_seconds: float = 0.0,
) -> dict[str, Any]:
    rows = load_mailex_rows(mailex_root, split=split, limit=offset + limit)[offset : offset + limit]
    return run_llm_benchmark_rows(
        rows,
        benchmark_name=f"MailEx:{mailex_root}/{split}",
        limit=limit,
        offset=offset,
        reference_date=reference_date,
        timezone=timezone,
        extractor=extractor,
        sleep_seconds=sleep_seconds,
    )


def run_kvret_llm_benchmark(
    kvret_root: Path,
    *,
    split: str = "test",
    limit: int = 10,
    offset: int = 0,
    reference_date: str | None = None,
    timezone: str = "Asia/Seoul",
    extractor: ConstraintExtractor | None = None,
    sleep_seconds: float = 0.0,
) -> dict[str, Any]:
    rows = load_kvret_rows(kvret_root, split=split, limit=offset + limit)[offset : offset + limit]
    return run_llm_benchmark_rows(
        rows,
        benchmark_name=f"KVRET:{kvret_root}/{split}:schedule",
        limit=limit,
        offset=offset,
        reference_date=reference_date,
        timezone=timezone,
        extractor=extractor,
        sleep_seconds=sleep_seconds,
    )


def run_llm_benchmark_rows(
    rows: list[dict[str, Any]],
    *,
    benchmark_name: str,
    limit: int,
    offset: int,
    reference_date: str | None,
    timezone: str,
    extractor: ConstraintExtractor | None = None,
    sleep_seconds: float = 0.0,
) -> dict[str, Any]:
    llm = extractor or GeminiConstraintExtractor()
    cases = []

    for idx, row in enumerate(rows):
        case = _run_case(
            row,
            extractor=llm,
            reference_date=reference_date,
            timezone=timezone,
        )
        cases.append(case)

        if sleep_seconds > 0 and idx < len(rows) - 1:
            time.sleep(sleep_seconds)

    summary = _summarize(cases)
    return {
        "benchmark": benchmark_name,
        "provider": "gemini",
        "limit": limit,
        "offset": offset,
        "reference_date": reference_date,
        "timezone": timezone,
        "cases": cases,
        "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Gemini extraction on a small JSONL benchmark.")
    parser.add_argument(
        "--benchmark",
        default="data/benchmarks/week1_event_extraction.jsonl",
        help="Path to a JSONL benchmark file.",
    )
    parser.add_argument(
        "--mailex-root",
        default=None,
        help="Path to local MailEx data root, for example dataset/data. When set, --benchmark is ignored.",
    )
    parser.add_argument(
        "--kvret-root",
        default=None,
        help="Path to local KVRET root, for example dataset/kvret. When set, --benchmark and --mailex-root are ignored.",
    )
    parser.add_argument("--split", default="test", help="Dataset split to evaluate.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of cases to send to Gemini.")
    parser.add_argument("--offset", type=int, default=0, help="Number of leading cases to skip.")
    parser.add_argument("--reference-date", default=None, help="Reference date for relative time normalization.")
    parser.add_argument("--timezone", default="Asia/Seoul", help="Timezone for time normalization.")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between API calls.")
    parser.add_argument("--output", default=None, help="Optional path to write the full JSON report.")
    args = parser.parse_args()

    if args.kvret_root:
        report = run_kvret_llm_benchmark(
            Path(args.kvret_root),
            split=args.split,
            limit=args.limit,
            offset=args.offset,
            reference_date=args.reference_date,
            timezone=args.timezone,
            sleep_seconds=args.sleep,
        )
    elif args.mailex_root:
        report = run_mailex_llm_benchmark(
            Path(args.mailex_root),
            split=args.split,
            limit=args.limit,
            offset=args.offset,
            reference_date=args.reference_date,
            timezone=args.timezone,
            sleep_seconds=args.sleep,
        )
    else:
        report = run_llm_benchmark(
            Path(args.benchmark),
            limit=args.limit,
            offset=args.offset,
            reference_date=args.reference_date,
            timezone=args.timezone,
            sleep_seconds=args.sleep,
        )

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")


def _run_case(
    row: dict[str, Any],
    *,
    extractor: ConstraintExtractor,
    reference_date: str | None,
    timezone: str,
) -> dict[str, Any]:
    try:
        prediction = extractor.extract(
            parse_email_thread(row["text"]),
            reference_date=reference_date,
            timezone=timezone,
        ).model_dump()
        error = None
    except Exception as exc:
        prediction = _empty_prediction()
        error = f"{type(exc).__name__}: {exc}"

    gold = row.get("gold", {})
    metrics = _evaluate_prediction(gold, prediction)

    return {
        "id": row.get("id"),
        "source": row.get("source", row.get("source_file", "unknown")),
        "error": error,
        **metrics,
        "gold": {
            "participants": _gold_participants(gold),
            "candidate_times": _gold_time_expressions(gold, "candidate_times"),
            "unavailable_times": _gold_time_expressions(gold, "unavailable_times"),
            "locations": _gold_locations(gold),
        },
        "pred": {
            "participants": _pred_participants(prediction),
            "candidate_times": _pred_time_expressions(prediction, "candidate_times"),
            "unavailable_times": _pred_time_expressions(prediction, "unavailable_times"),
            "locations": _pred_locations(prediction),
        },
        "prediction": prediction,
    }


def _evaluate_prediction(gold: dict[str, Any], pred: dict[str, Any]) -> dict[str, Any]:
    gold_participants = _gold_participants(gold)
    pred_participants = _pred_participants(pred)
    gold_candidates = _gold_time_expressions(gold, "candidate_times")
    pred_candidates = _pred_time_expressions(pred, "candidate_times")
    gold_unavailable = _gold_time_expressions(gold, "unavailable_times")
    pred_unavailable = _pred_time_expressions(pred, "unavailable_times")
    gold_locations = _gold_locations(gold)
    pred_locations = _pred_locations(pred)

    return {
        "participant_exact_match": _normalized_set(gold_participants) == _normalized_set(pred_participants),
        "participant_f1": _span_list_f1(gold_participants, pred_participants),
        "candidate_time_f1": _span_list_f1(gold_candidates, pred_candidates),
        "unavailable_time_f1": _span_list_f1(gold_unavailable, pred_unavailable),
        "location_f1": _span_list_f1(gold_locations, pred_locations),
        "json_valid": bool(pred.get("intent")),
    }


def _summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    metric_keys = [
        "participant_f1",
        "candidate_time_f1",
        "unavailable_time_f1",
        "location_f1",
    ]
    macro = {f"macro_{key}": _safe_mean([float(case[key]) for case in cases]) for key in metric_keys}
    participant_exact = _safe_mean([1.0 if case["participant_exact_match"] else 0.0 for case in cases])
    json_valid_rate = _safe_mean([1.0 if case["json_valid"] else 0.0 for case in cases])
    api_success_rate = _safe_mean([1.0 if not case["error"] else 0.0 for case in cases])

    overall = _safe_mean(
        [
            macro["macro_participant_f1"],
            macro["macro_candidate_time_f1"],
            macro["macro_location_f1"],
        ]
    )
    return {
        "num_cases": len(cases),
        "api_success_rate": api_success_rate,
        "json_valid_rate": json_valid_rate,
        "macro_participant_exact_match": participant_exact,
        **macro,
        "overall_score": overall,
        "overall_score_formula": (
            "mean(macro_participant_f1, macro_candidate_time_f1, macro_location_f1)"
        ),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _empty_prediction() -> dict[str, Any]:
    return {
        "intent": None,
        "participants": [],
        "candidate_times": [],
        "unavailable_times": [],
        "location_preference": None,
        "meeting_duration_minutes": None,
        "missing_information": [],
        "confidence": 0.0,
        "source_summary": "",
    }


def _gold_participants(gold: dict[str, Any]) -> list[str]:
    values = gold.get("participants", [])
    return _filter_participants([str(item) for item in values] if isinstance(values, list) else [])


def _pred_participants(prediction: dict[str, Any]) -> list[str]:
    values = prediction.get("participants", [])
    return _filter_participants([str(item) for item in values] if isinstance(values, list) else [])


def _gold_time_expressions(gold: dict[str, Any], key: str) -> list[str]:
    return _time_expressions(gold.get(key, []))


def _pred_time_expressions(prediction: dict[str, Any], key: str) -> list[str]:
    return _time_expressions(prediction.get(key, []))


def _time_expressions(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []

    output = []
    for item in values:
        if isinstance(item, dict) and item.get("expression"):
            output.append(str(item["expression"]))
        elif isinstance(item, str):
            output.append(item)
    return output


def _gold_locations(gold: dict[str, Any]) -> list[str]:
    return _split_locations(gold.get("location_preference"))


def _pred_locations(prediction: dict[str, Any]) -> list[str]:
    return _split_locations(prediction.get("location_preference"))


def _split_locations(value: Any) -> list[str]:
    if value is None:
        return []

    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "unknown", "n/a", "na"}:
        return []

    return [part.strip() for part in re.split(r";|\n|,", text) if part.strip()]


def _safe_normalize(value: Any) -> str:
    text = str(value).lower()
    text = re.sub(r"\b(\d{1,2})\s*(a\.m\.|p\.m\.|am|pm)\b", r"\1\2", text)
    text = re.sub(r"\b0(\d)(am|pm)\b", r"\1\2", text)
    text = re.sub(r"\bat\s+", " ", text)
    text = re.sub(r"\bon\s+", " ", text)
    text = re.sub(r"\bthe\s+", " ", text)
    text = re.sub(r"\bof\s+", " ", text)
    text = text.replace("a.m.", "am").replace("p.m.", "pm")
    text = re.sub(r"[^a-z0-9가-힣]+", " ", text)
    return " ".join(text.split())


def _filter_participants(values: list[str]) -> list[str]:
    ignored = {"driver", "assistant", "sender"}
    return [value for value in values if _safe_normalize(value) not in ignored]


def _normalized_set(values: list[str]) -> set[str]:
    return {_safe_normalize(value) for value in values if _safe_normalize(value)}


def _span_list_f1(gold_values: list[str], pred_values: list[str], threshold: float = 0.5) -> float:
    gold = [value for value in gold_values if _safe_normalize(value)]
    pred = [value for value in pred_values if _safe_normalize(value)]

    if not gold and not pred:
        return 1.0
    if not gold or not pred:
        return 0.0

    pairs = []
    for gold_idx, gold_value in enumerate(gold):
        for pred_idx, pred_value in enumerate(pred):
            score = _string_f1(gold_value, pred_value)
            if score >= threshold:
                pairs.append((score, gold_idx, pred_idx))

    pairs.sort(reverse=True)
    used_gold: set[int] = set()
    used_pred: set[int] = set()
    matches = 0

    for _, gold_idx, pred_idx in pairs:
        if gold_idx in used_gold or pred_idx in used_pred:
            continue
        used_gold.add(gold_idx)
        used_pred.add(pred_idx)
        matches += 1

    precision = matches / len(pred)
    recall = matches / len(gold)
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)


def _string_f1(gold: str, pred: str) -> float:
    gold_tokens = set(_safe_normalize(gold).split())
    pred_tokens = set(_safe_normalize(pred).split())

    if not gold_tokens and not pred_tokens:
        return 1.0
    if not gold_tokens or not pred_tokens:
        return 0.0

    overlap = len(gold_tokens & pred_tokens)
    if overlap == 0:
        return 0.0

    precision = overlap / len(pred_tokens)
    recall = overlap / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def _normalize(value: Any) -> str:
    text = str(value).lower()
    text = re.sub(r"[^a-z0-9가-힣]+", " ", text)
    return " ".join(text.split())


def _safe_mean(values: list[float]) -> float:
    return round(mean(values), 4) if values else 0.0


if __name__ == "__main__":
    main()
