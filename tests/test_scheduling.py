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
