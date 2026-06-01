from __future__ import annotations

import argparse
import json
from datetime import datetime, time
from pathlib import Path
from typing import Any

from .schema import (
    CalendarEvent,
    CandidateDecision,
    ExtractionResult,
    ScheduleRecommendation,
    TimeCandidate,
)


DEFAULT_ALLOWED_START = time(9, 0)
DEFAULT_ALLOWED_END = time(22, 0)


def load_calendar(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def recommend_time(
    extraction: ExtractionResult,
    calendar_data: dict[str, Any],
) -> ScheduleRecommendation:
    candidates = build_time_candidates(extraction)
    if not candidates:
        return ScheduleRecommendation(
            status="missing_candidate_times",
            summary="normalized_start/end가 있는 후보 시간이 없습니다.",
        )

    busy_events = [CalendarEvent.model_validate(item) for item in calendar_data.get("busy_events", [])]
    allowed_start, allowed_end = _allowed_hours(calendar_data)
    unavailable = extraction.unavailable_times

    decisions = [
        judge_candidate(candidate, busy_events, unavailable, allowed_start, allowed_end)
        for candidate in candidates
    ]
    valid_decisions = [decision for decision in decisions if decision.valid]
    selected = max(valid_decisions, key=lambda item: item.score) if valid_decisions else None

    if selected:
        return ScheduleRecommendation(
            selected=selected,
            candidates=decisions,
            status="selected",
            summary=f"{selected.candidate.start} 시간이 가장 적합합니다. 점수: {selected.score}",
        )

    return ScheduleRecommendation(
        selected=None,
        candidates=decisions,
        status="no_valid_candidate",
        summary="모든 후보 시간이 hard constraint를 위반했습니다.",
    )


def build_time_candidates(extraction: ExtractionResult) -> list[TimeCandidate]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    for item in extraction.candidate_times:
        if not item.normalized_start or not item.normalized_end:
            continue

        key = (item.normalized_start, item.normalized_end)
        if key not in grouped:
            grouped[key] = {
                "source_expressions": [],
                "available_participants": set(),
                "preferred_by": set(),
            }

        grouped[key]["source_expressions"].append(item.expression)
        if item.participant:
            grouped[key]["available_participants"].add(item.participant)
            if item.availability == "preferred":
                grouped[key]["preferred_by"].add(item.participant)

    return [
        TimeCandidate(
            source_expression=", ".join(sorted(set(data["source_expressions"]))),
            start=start,
            end=end,
            available_participants=sorted(data["available_participants"]),
            preferred_by=sorted(data["preferred_by"]),
        )
        for (start, end), data in grouped.items()
    ]


def judge_candidate(
    candidate: TimeCandidate,
    busy_events: list[CalendarEvent],
    unavailable_times: list[Any],
    allowed_start: time = DEFAULT_ALLOWED_START,
    allowed_end: time = DEFAULT_ALLOWED_END,
) -> CandidateDecision:
    start = _parse_datetime(candidate.start)
    end = _parse_datetime(candidate.end)
    hard_violations: list[str] = []
    reasons: list[str] = []

    if start >= end:
        hard_violations.append("invalid_time_range")

    if start.time() < allowed_start or end.time() > allowed_end:
        hard_violations.append("outside_allowed_hours")
    else:
        reasons.append("허용 시간대 안에 있습니다.")

    conflicts = [
        event
        for event in busy_events
        if event.participant in candidate.available_participants
        and _overlaps(start, end, _parse_datetime(event.start), _parse_datetime(event.end))
    ]
    if conflicts:
        hard_violations.append("calendar_conflict")
    else:
        reasons.append("참석자 캘린더와 충돌하지 않습니다.")

    if _violates_unavailable(start, end, unavailable_times):
        hard_violations.append("violates_unavailable_time")
    else:
        reasons.append("명시적 불가 시간과 겹치지 않습니다.")

    score = _score_candidate(candidate, start, end) if not hard_violations else 0
    if candidate.available_participants:
        reasons.append(f"{len(candidate.available_participants)}명이 가능하다고 언급했습니다.")
    if candidate.preferred_by:
        reasons.append(f"{', '.join(candidate.preferred_by)}가 선호한 시간입니다.")

    return CandidateDecision(
        candidate=candidate,
        valid=not hard_violations,
        score=score,
        hard_violations=hard_violations,
        reasons=reasons,
        conflicts=conflicts,
    )


def _score_candidate(candidate: TimeCandidate, start: datetime, end: datetime) -> int:
    score = 0
    participant_count = len(candidate.available_participants)

    score += participant_count * 10
    if participant_count >= 2:
        score += 40
    score += len(candidate.preferred_by) * 20

    if 10 <= start.hour <= 18:
        score += 10
    elif start.hour < 9 or start.hour >= 21:
        score -= 10

    duration_minutes = int((end - start).total_seconds() / 60)
    if duration_minutes >= 60:
        score += 5

    return score


def _violates_unavailable(start: datetime, end: datetime, unavailable_times: list[Any]) -> bool:
    for item in unavailable_times:
        if not item.normalized_start or not item.normalized_end:
            continue
        if _overlaps(start, end, _parse_datetime(item.normalized_start), _parse_datetime(item.normalized_end)):
            return True
    return False


def _overlaps(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _allowed_hours(calendar_data: dict[str, Any]) -> tuple[time, time]:
    allowed = calendar_data.get("allowed_hours", {})
    start = _parse_time(allowed.get("start"), DEFAULT_ALLOWED_START)
    end = _parse_time(allowed.get("end"), DEFAULT_ALLOWED_END)
    return start, end


def _parse_time(value: str | None, default: time) -> time:
    if not value:
        return default
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run calendar judge and heuristic time optimizer.")
    parser.add_argument("--extraction", required=True, help="Path to a normalized extraction JSON file.")
    parser.add_argument("--calendar", required=True, help="Path to a synthetic calendar JSON file.")
    args = parser.parse_args()

    extraction = ExtractionResult.model_validate_json(Path(args.extraction).read_text(encoding="utf-8"))
    calendar_data = load_calendar(args.calendar)
    recommendation = recommend_time(extraction, calendar_data)

    print(json.dumps(recommendation.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
