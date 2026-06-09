from __future__ import annotations

import argparse
import json
from datetime import datetime, time
from pathlib import Path
from typing import Any, Callable

from .json_utils import parse_json_object
from .prompting import build_time_optimization_prompt
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
    *,
    preferred_date: str | None = None,
    email_thread: Any | None = None,
    llm_text_generator: Callable[[str], str] | None = None,
    llm_required: bool = False,
    llm_retry_limit: int = 3,
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
    preferred_decisions = _filter_by_preferred_date(valid_decisions, preferred_date)
    selected_pool = preferred_decisions or valid_decisions
    selected = _select_candidate_with_llm(
        selected_pool,
        extraction,
        calendar_data,
        preferred_date=preferred_date,
        email_thread=email_thread,
        llm_text_generator=llm_text_generator,
        required=llm_required,
        retry_limit=llm_retry_limit,
    )
    if not selected and not llm_required:
        selected = max(selected_pool, key=lambda item: item.score) if selected_pool else None

    if selected:
        if preferred_decisions:
            selected.reasons.append(f"사용자가 선택한 날짜({preferred_date})의 후보입니다.")
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


def _select_candidate_with_llm(
    selected_pool: list[CandidateDecision],
    extraction: ExtractionResult,
    calendar_data: dict[str, Any],
    *,
    preferred_date: str | None,
    email_thread: Any | None,
    llm_text_generator: Callable[[str], str] | None,
    required: bool = False,
    retry_limit: int = 3,
) -> CandidateDecision | None:
    if not selected_pool:
        return None
    if not llm_text_generator or not email_thread:
        if required:
            raise TimeoutError("time optimization failed because EXAONE text generation was not available.")
        return None

    candidate_payload = [
        {
            "index": index,
            "candidate": decision.candidate.model_dump(),
            "heuristic_score": decision.score,
            "hard_violations": decision.hard_violations,
            "rule_reasons": decision.reasons,
        }
        for index, decision in enumerate(selected_pool)
    ]
    prompt = build_time_optimization_prompt(
        email_thread=email_thread,
        extraction_json=extraction.model_dump_json(),
        candidates=candidate_payload,
        calendar_json=json.dumps(calendar_data, ensure_ascii=False, indent=2),
        preferred_date=preferred_date,
    )
    errors: list[str] = []
    for attempt in range(1, retry_limit + 1):
        try:
            data = parse_json_object(llm_text_generator(prompt))
            selected_index = int(data.get("selected_index"))
            if selected_index < 0 or selected_index >= len(selected_pool):
                raise ValueError(f"selected_index out of range: {selected_index}")
            selected = selected_pool[selected_index]
            llm_reasons = [str(item) for item in data.get("reasons", []) if str(item).strip()]
            llm_summary = str(data.get("summary") or "").strip()
            reasons = selected.reasons + [f"LLM 최적화: {reason}" for reason in llm_reasons[:3]]
            if llm_summary:
                reasons.append(f"LLM 요약: {llm_summary}")
            score = int(data.get("score", selected.score))
            return selected.model_copy(update={"score": score, "reasons": reasons})
        except Exception as exc:
            errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")

    if required:
        raise TimeoutError(
            f"time optimization failed after {retry_limit} EXAONE attempts. {' | '.join(errors)}"
        )
    return None


def _filter_by_preferred_date(
    decisions: list[CandidateDecision],
    preferred_date: str | None,
) -> list[CandidateDecision]:
    if not preferred_date:
        return []
    return [
        decision
        for decision in decisions
        if _parse_datetime(decision.candidate.start).date().isoformat() == preferred_date
    ]


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
