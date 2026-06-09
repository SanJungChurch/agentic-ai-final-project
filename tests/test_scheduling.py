from pathlib import Path
import unittest

from src.email_agent.schema import ExtractionResult
from src.email_agent.scheduling import build_time_candidates, load_calendar, recommend_time


class SchedulingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.extraction = ExtractionResult.model_validate_json(
            Path("data/samples/extraction_001.normalized.json").read_text(encoding="utf-8")
        )
        self.calendar = load_calendar("data/calendars/synthetic_calendar_001.json")

    def test_build_time_candidates_groups_same_times(self) -> None:
        candidates = build_time_candidates(self.extraction)

        self.assertEqual(len(candidates), 2)
        first = next(item for item in candidates if item.start == "2026-05-27T17:00:00+09:00")
        self.assertEqual(set(first.available_participants), {"동욱", "정인", "민수"})

    def test_recommend_time_selects_valid_highest_score(self) -> None:
        recommendation = recommend_time(self.extraction, self.calendar)

        self.assertEqual(recommendation.status, "selected")
        self.assertIsNotNone(recommendation.selected)
        self.assertEqual(
            recommendation.selected.candidate.start,
            "2026-05-27T17:00:00+09:00",
        )
        self.assertTrue(recommendation.selected.valid)

    def test_recommend_time_prefers_selected_date_when_valid(self) -> None:
        extraction = ExtractionResult.model_validate(
            {
                "intent": "schedule_meeting",
                "participants": ["A", "B"],
                "candidate_times": [
                    {
                        "participant": "A",
                        "expression": "수요일 5시",
                        "normalized_start": "2026-05-27T17:00:00+09:00",
                        "normalized_end": "2026-05-27T18:00:00+09:00",
                        "availability": "available",
                    },
                    {
                        "participant": "A",
                        "expression": "금요일 2시",
                        "normalized_start": "2026-05-29T14:00:00+09:00",
                        "normalized_end": "2026-05-29T15:00:00+09:00",
                        "availability": "available",
                    },
                    {
                        "participant": "B",
                        "expression": "금요일 2시",
                        "normalized_start": "2026-05-29T14:00:00+09:00",
                        "normalized_end": "2026-05-29T15:00:00+09:00",
                        "availability": "available",
                    },
                ],
                "unavailable_times": [],
                "location_preference": "숭실대",
                "confidence": 0.9,
                "source_summary": "",
            }
        )
        calendar = {
            "allowed_hours": {"start": "09:00", "end": "22:00"},
            "busy_events": [],
        }

        recommendation = recommend_time(extraction, calendar, preferred_date="2026-05-29")

        self.assertEqual(recommendation.selected.candidate.start, "2026-05-29T14:00:00+09:00")
        self.assertIn("사용자가 선택한 날짜", recommendation.selected.reasons[-1])

    def test_unavailable_time_invalidates_candidate(self) -> None:
        recommendation = recommend_time(self.extraction, self.calendar)
        thursday = next(
            item
            for item in recommendation.candidates
            if item.candidate.start == "2026-05-28T10:00:00+09:00"
        )

        self.assertFalse(thursday.valid)
        self.assertIn("calendar_conflict", thursday.hard_violations)
        self.assertIn("violates_unavailable_time", thursday.hard_violations)


if __name__ == "__main__":
    unittest.main()
