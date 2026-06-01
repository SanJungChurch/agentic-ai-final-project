from __future__ import annotations

import argparse
import json
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .schema import ExtractionResult, TimeConstraint


KOREAN_WEEKDAYS = {
    "월요일": 0,
    "월": 0,
    "화요일": 1,
    "화": 1,
    "수요일": 2,
    "수": 2,
    "목요일": 3,
    "목": 3,
    "금요일": 4,
    "금": 4,
    "토요일": 5,
    "토": 5,
    "일요일": 6,
    "일": 6,
}

ENGLISH_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

PERIOD_WINDOWS = {
    "morning": (time(9, 0), time(12, 0)),
    "오전": (time(9, 0), time(12, 0)),
    "afternoon": (time(13, 0), time(18, 0)),
    "오후": (time(13, 0), time(18, 0)),
    "evening": (time(18, 0), time(21, 0)),
    "저녁": (time(18, 0), time(21, 0)),
}


@dataclass(frozen=True)
class NormalizedRange:
    start: datetime
    end: datetime


def normalize_extraction_times(
    extraction: ExtractionResult,
    *,
    reference_date: str | None = None,
    timezone: str = "Asia/Seoul",
    default_duration_minutes: int = 60,
) -> ExtractionResult:
    ref = _reference_date(reference_date, timezone)
    duration = extraction.meeting_duration_minutes or default_duration_minutes

    candidate_times = [
        normalize_time_constraint(
            item,
            reference=ref,
            timezone=timezone,
            duration_minutes=duration,
            all_day_without_time=False,
        )
        for item in extraction.candidate_times
    ]
    unavailable_times = [
        normalize_time_constraint(
            item,
            reference=ref,
            timezone=timezone,
            duration_minutes=duration,
            all_day_without_time=True,
        )
        for item in extraction.unavailable_times
    ]

    return extraction.model_copy(
        update={
            "candidate_times": candidate_times,
            "unavailable_times": unavailable_times,
        }
    )


def normalize_time_constraint(
    constraint: TimeConstraint,
    *,
    reference: date,
    timezone: str,
    duration_minutes: int,
    all_day_without_time: bool,
) -> TimeConstraint:
    if constraint.normalized_start and constraint.normalized_end:
        return constraint

    normalized = parse_time_expression(
        constraint.expression,
        reference=reference,
        timezone=timezone,
        duration_minutes=duration_minutes,
        all_day_without_time=all_day_without_time,
    )
    if not normalized:
        return constraint

    return constraint.model_copy(
        update={
            "normalized_start": normalized.start.isoformat(),
            "normalized_end": normalized.end.isoformat(),
        }
    )


def parse_time_expression(
    expression: str,
    *,
    reference: date,
    timezone: str = "Asia/Seoul",
    duration_minutes: int = 60,
    all_day_without_time: bool = False,
) -> NormalizedRange | None:
    text = _normalize_text(expression)
    tz = ZoneInfo(timezone)

    parsed_date = _parse_date(text, reference)
    parsed_time = _parse_clock_time(text)
    parsed_period = _parse_period(text)

    if not parsed_date and parsed_time:
        parsed_date = reference

    if not parsed_date:
        return None

    if parsed_time:
        start = datetime.combine(parsed_date, parsed_time, tzinfo=tz)
        end = start + timedelta(minutes=duration_minutes)
        return NormalizedRange(start=start, end=end)

    if parsed_period:
        start_time, end_time = parsed_period
        return NormalizedRange(
            start=datetime.combine(parsed_date, start_time, tzinfo=tz),
            end=datetime.combine(parsed_date, end_time, tzinfo=tz),
        )

    if all_day_without_time:
        start = datetime.combine(parsed_date, time(0, 0), tzinfo=tz)
        return NormalizedRange(start=start, end=start + timedelta(days=1))

    return None


def _parse_date(text: str, reference: date) -> date | None:
    if "오늘" in text or re.search(r"\btoday\b", text):
        return reference
    if "내일" in text or re.search(r"\btomorrow\b", text):
        return reference + timedelta(days=1)

    weekday = _weekday_from_text(text)
    if weekday is not None:
        if "다음 주" in text or re.search(r"\bnext week\b", text):
            return _date_in_week(reference + timedelta(days=7), weekday)
        if "이번 주" in text or re.search(r"\bthis week\b", text):
            return _date_in_week(reference, weekday)
        days_ahead = (weekday - reference.weekday()) % 7
        return reference + timedelta(days=days_ahead)

    day = _day_of_month_from_text(text)
    if day is not None:
        year = reference.year
        month = reference.month
        if "this month" not in text and "이번 달" not in text and day < reference.day:
            year, month = _next_month(year, month)
        day = min(day, monthrange(year, month)[1])
        return date(year, month, day)

    return None


def _weekday_from_text(text: str) -> int | None:
    for word, weekday in KOREAN_WEEKDAYS.items():
        if word in text:
            return weekday
    for word, weekday in ENGLISH_WEEKDAYS.items():
        if re.search(rf"\b{word}\b", text):
            return weekday
    return None


def _day_of_month_from_text(text: str) -> int | None:
    korean = re.search(r"(\d{1,2})\s*일", text)
    if korean:
        return int(korean.group(1))

    english = re.search(r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", text)
    if english:
        return int(english.group(1))

    return None


def _parse_clock_time(text: str) -> time | None:
    korean = re.search(r"(오전|오후|저녁)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?", text)
    if korean:
        period = korean.group(1)
        hour = int(korean.group(2))
        minute = int(korean.group(3) or 0)
        return _apply_period(hour, minute, period)

    english = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.m\.|p\.m\.|am|pm)\b", text)
    if english:
        hour = int(english.group(1))
        minute = int(english.group(2) or 0)
        meridiem = english.group(3).replace(".", "")
        return _apply_period(hour, minute, meridiem)

    return None


def _apply_period(hour: int, minute: int, period: str | None) -> time:
    if period in {"오후", "저녁", "pm"} and hour < 12:
        hour += 12
    elif period in {"오전", "am"} and hour == 12:
        hour = 0
    elif period is None and 1 <= hour <= 7:
        hour += 12

    return time(hour % 24, minute)


def _parse_period(text: str) -> tuple[time, time] | None:
    for period, window in PERIOD_WINDOWS.items():
        if period in text:
            return window
    return None


def _date_in_week(reference: date, target_weekday: int) -> date:
    monday = reference - timedelta(days=reference.weekday())
    return monday + timedelta(days=target_weekday)


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _reference_date(reference_date: str | None, timezone: str) -> date:
    if reference_date:
        return date.fromisoformat(reference_date)
    return datetime.now(ZoneInfo(timezone)).date()


def _normalize_text(value: str) -> str:
    text = str(value).lower()
    text = text.replace("p.m.", "pm").replace("a.m.", "am")
    return " ".join(text.split())


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize extracted time expressions into ISO datetimes.")
    parser.add_argument("--extraction", required=True, help="Path to an ExtractionResult JSON file.")
    parser.add_argument("--reference-date", required=True, help="Reference date in YYYY-MM-DD format.")
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--output", default=None, help="Optional output path. Prints to stdout if omitted.")
    args = parser.parse_args()

    extraction = ExtractionResult.model_validate_json(Path(args.extraction).read_text(encoding="utf-8"))
    normalized = normalize_extraction_times(
        extraction,
        reference_date=args.reference_date,
        timezone=args.timezone,
    )
    text = json.dumps(normalized.model_dump(), ensure_ascii=False, indent=2)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
