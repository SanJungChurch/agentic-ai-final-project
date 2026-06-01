from datetime import date
import unittest

from src.email_agent.schema import ExtractionResult, Intent, TimeConstraint
from src.email_agent.time_normalizer import normalize_extraction_times, parse_time_expression


class TimeNormalizerTest(unittest.TestCase):
    def test_korean_weekday_afternoon_hour(self) -> None:
        parsed = parse_time_expression(
            "수요일 5시",
            reference=date(2026, 5, 25),
            duration_minutes=60,
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.start.isoformat(), "2026-05-27T17:00:00+09:00")
        self.assertEqual(parsed.end.isoformat(), "2026-05-27T18:00:00+09:00")

    def test_korean_next_week(self) -> None:
        parsed = parse_time_expression(
            "다음 주 월요일 오후 2시",
            reference=date(2026, 5, 27),
            duration_minutes=60,
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.start.isoformat(), "2026-06-01T14:00:00+09:00")

    def test_english_weekday_time(self) -> None:
        parsed = parse_time_expression(
            "Friday 11 am",
            reference=date(2026, 5, 27),
            duration_minutes=60,
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.start.isoformat(), "2026-05-29T11:00:00+09:00")

    def test_unavailable_day_becomes_all_day_range(self) -> None:
        extraction = ExtractionResult(
            intent=Intent.SCHEDULE_MEETING,
            unavailable_times=[
                TimeConstraint(expression="목요일", availability="unavailable")
            ],
        )

        normalized = normalize_extraction_times(
            extraction,
            reference_date="2026-05-27",
        )

        self.assertEqual(
            normalized.unavailable_times[0].normalized_start,
            "2026-05-28T00:00:00+09:00",
        )
        self.assertEqual(
            normalized.unavailable_times[0].normalized_end,
            "2026-05-29T00:00:00+09:00",
        )


if __name__ == "__main__":
    unittest.main()
