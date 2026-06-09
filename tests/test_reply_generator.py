import unittest

from src.email_agent.reply_generator import generate_reply_draft
from src.email_agent.schema import (
    CandidateDecision,
    ExtractionResult,
    Intent,
    ReplyDraft,
    ScheduleRecommendation,
    PlaceCandidate,
    PlaceRecommendation,
    ReservationResult,
    TimeCandidate,
)


class ReplyGeneratorTest(unittest.TestCase):
    def test_generate_ready_reply(self) -> None:
        extraction = ExtractionResult(
            intent=Intent.SCHEDULE_MEETING,
            participants=["동욱", "정인", "민수"],
            location_preference="숭실대 근처 카페",
        )
        recommendation = ScheduleRecommendation(
            status="selected",
            selected=CandidateDecision(
                candidate=TimeCandidate(
                    source_expression="수요일 5시",
                    start="2026-05-27T17:00:00+09:00",
                    end="2026-05-27T18:00:00+09:00",
                    available_participants=["정인", "민수"],
                ),
                valid=True,
                score=75,
                reasons=["캘린더와 충돌하지 않습니다.", "2명이 가능하다고 언급했습니다."],
            ),
        )

        draft = generate_reply_draft(extraction, recommendation)

        self.assertIsInstance(draft, ReplyDraft)
        self.assertEqual(draft.status, "ready")
        self.assertIn("5월 27일(수) 오후 5시-오후 6시", draft.body)
        self.assertIn("요청하신 조건(숭실대 근처 카페)", draft.body)

    def test_generate_reply_with_place_recommendation(self) -> None:
        extraction = ExtractionResult(
            intent=Intent.SCHEDULE_MEETING,
            participants=["동욱", "정인"],
            location_preference="숭실대 근처 카페",
        )
        recommendation = ScheduleRecommendation(
            status="selected",
            selected=CandidateDecision(
                candidate=TimeCandidate(
                    source_expression="수요일 5시",
                    start="2026-05-27T17:00:00+09:00",
                    end="2026-05-27T18:00:00+09:00",
                ),
                valid=True,
                score=75,
            ),
        )
        place = PlaceRecommendation(
            query="숭실대 근처 카페",
            status="selected",
            selected=PlaceCandidate(name="카페 온더힐", address="서울 동작구 상도로 369"),
        )

        draft = generate_reply_draft(extraction, recommendation, place_recommendation=place)

        self.assertIn("카페 온더힐", draft.body)

    def test_generate_reply_states_reservation_failure(self) -> None:
        extraction = ExtractionResult(
            intent=Intent.SCHEDULE_MEETING,
            participants=["동욱", "정인"],
            location_preference="숭실대 근처 식당",
        )
        recommendation = ScheduleRecommendation(
            status="selected",
            selected=CandidateDecision(
                candidate=TimeCandidate(
                    source_expression="수요일 5시",
                    start="2026-05-27T17:00:00+09:00",
                    end="2026-05-27T18:00:00+09:00",
                ),
                valid=True,
                score=75,
            ),
        )
        place = PlaceRecommendation(
            query="숭실대 식당 예약",
            status="selected",
            selected=PlaceCandidate(name="숭실대 예약 식당", category="restaurant"),
        )
        reservation = ReservationResult(
            status="failed",
            failure_reason="slot_unavailable",
            message="This reservation slot is currently unavailable.",
        )

        draft = generate_reply_draft(
            extraction,
            recommendation,
            place_recommendation=place,
            reservation_result=reservation,
        )

        self.assertEqual(draft.status, "reservation_failed")
        self.assertIn("예약은 완료되지 않았습니다", draft.body)
        self.assertNotIn("진행해도 괜찮을까요", draft.body)

    def test_generate_clarification_reply(self) -> None:
        extraction = ExtractionResult(intent=Intent.SCHEDULE_MEETING)
        recommendation = ScheduleRecommendation(status="missing_candidate_times")

        draft = generate_reply_draft(extraction, recommendation)

        self.assertEqual(draft.status, "needs_clarification")
        self.assertIn("가능한 날짜와 시간대", draft.body)


if __name__ == "__main__":
    unittest.main()
