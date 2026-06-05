from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .schema import ExtractionResult, PlaceRecommendation, ReplyDraft, ReservationResult, ScheduleRecommendation


def generate_reply_draft(
    extraction: ExtractionResult,
    recommendation: ScheduleRecommendation | None,
    *,
    place_recommendation: PlaceRecommendation | None = None,
    reservation_result: ReservationResult | None = None,
    timezone: str = "Asia/Seoul",
) -> ReplyDraft:
    if not recommendation or recommendation.status == "missing_candidate_times":
        return ReplyDraft(
            subject="Re: 일정 조율",
            body=(
                "안녕하세요.\n\n"
                "가능한 시간을 확인하려고 했지만, 확정할 수 있는 날짜나 시간이 부족합니다. "
                "가능한 날짜와 시간대를 한 번 더 알려주시면 확인해보겠습니다.\n\n"
                "감사합니다."
            ),
            status="needs_clarification",
            rationale=["추천 가능한 normalized candidate time이 없습니다."],
        )

    if recommendation.status == "no_valid_candidate" or not recommendation.selected:
        return ReplyDraft(
            subject="Re: 일정 조율",
            body=(
                "안녕하세요.\n\n"
                "제안된 시간들을 캘린더와 비교해보았는데, 현재는 충돌 없이 확정 가능한 시간이 없습니다. "
                "다른 후보 시간을 몇 가지 더 공유해주시면 다시 확인해보겠습니다.\n\n"
                "감사합니다."
            ),
            status="no_valid_candidate",
            rationale=["모든 후보 시간이 hard constraint를 위반했습니다."],
        )

    selected = recommendation.selected
    candidate = selected.candidate
    time_text = _format_time_range(candidate.start, candidate.end, timezone)
    participants = _format_participants(extraction.participants)
    location_line = _location_line(extraction.location_preference, place_recommendation)
    reservation_line = _reservation_line(reservation_result)
    reason_text = _reason_sentence(selected.reasons)

    body = (
        "안녕하세요.\n\n"
        f"확인해보니 {time_text}에 진행하는 것이 가장 적합해 보입니다. "
        f"{reason_text}"
    )
    if participants:
        body += f"\n참석자는 {participants} 기준으로 확인했습니다."
    if location_line:
        body += f"\n{location_line}"
    if reservation_line:
        body += f"\n{reservation_line}"

    body += "\n\n이 시간으로 진행해도 괜찮을까요?\n\n감사합니다."

    return ReplyDraft(
        subject="Re: 일정 조율",
        body=body,
        status="ready",
        rationale=selected.reasons,
    )


def _format_time_range(start: str, end: str, timezone: str) -> str:
    tz = ZoneInfo(timezone)
    start_dt = datetime.fromisoformat(start).astimezone(tz)
    end_dt = datetime.fromisoformat(end).astimezone(tz)

    weekday = ["월", "화", "수", "목", "금", "토", "일"][start_dt.weekday()]
    date_text = f"{start_dt.month}월 {start_dt.day}일({weekday})"
    start_text = _format_korean_time(start_dt)
    end_text = _format_korean_time(end_dt)
    return f"{date_text} {start_text}-{end_text}"


def _format_korean_time(value: datetime) -> str:
    period = "오전" if value.hour < 12 else "오후"
    hour = value.hour if 1 <= value.hour <= 12 else abs(value.hour - 12)
    if hour == 0:
        hour = 12
    if value.minute:
        return f"{period} {hour}시 {value.minute:02d}분"
    return f"{period} {hour}시"


def _format_participants(participants: list[str]) -> str:
    cleaned = [item for item in participants if item]
    if not cleaned:
        return ""
    if len(cleaned) <= 3:
        return ", ".join(cleaned)
    return ", ".join(cleaned[:3]) + f" 외 {len(cleaned) - 3}명"


def _location_line(
    location_preference: str | None,
    place_recommendation: PlaceRecommendation | None = None,
) -> str:
    if place_recommendation and place_recommendation.selected:
        place = place_recommendation.selected
        detail = f"{place.name}"
        if place.address:
            detail += f"({place.address})"
        return f"장소 후보로는 {detail}을 우선 확인했습니다."

    if not location_preference:
        return ""
    cleaned = location_preference.strip().rstrip(".")
    cleaned = cleaned.removeprefix("장소는").strip()
    return f"장소는 요청하신 조건({cleaned})을 기준으로 이어서 확인하겠습니다."


def _reason_sentence(reasons: list[str]) -> str:
    if not reasons:
        return "캘린더 충돌 여부를 기준으로 선택했습니다."
    return " ".join(reason.rstrip(".") + "." for reason in reasons[:2])


def _reservation_line(reservation_result: ReservationResult | None) -> str:
    if not reservation_result:
        return ""
    if reservation_result.status == "confirmed":
        confirmation = f" 예약번호는 {reservation_result.confirmation_id}입니다." if reservation_result.confirmation_id else ""
        return f"예약 페이지에서 해당 시간 예약을 확인했습니다.{confirmation}"
    if reservation_result.status == "failed":
        reason = f" ({reservation_result.failure_reason})" if reservation_result.failure_reason else ""
        return f"예약 시도는 실패했습니다{reason}. 다른 장소나 시간을 다시 확인해야 합니다."
    if reservation_result.status == "needs_manual_action":
        return "예약 페이지에서 추가 수동 확인이 필요합니다."
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a reply draft from extraction and recommendation JSON files.")
    parser.add_argument("--extraction", required=True)
    parser.add_argument("--recommendation", required=True)
    parser.add_argument("--timezone", default="Asia/Seoul")
    args = parser.parse_args()

    extraction = ExtractionResult.model_validate_json(Path(args.extraction).read_text(encoding="utf-8"))
    recommendation = ScheduleRecommendation.model_validate_json(Path(args.recommendation).read_text(encoding="utf-8"))
    draft = generate_reply_draft(extraction, recommendation, timezone=args.timezone)
    print(json.dumps(draft.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
