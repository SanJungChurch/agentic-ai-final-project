import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.email_agent.graph import build_extraction_graph


class GraphTest(unittest.TestCase):
    def test_graph_falls_back_without_api_key(self) -> None:
        email_text = Path("data/samples/email_001.txt").read_text(encoding="utf-8")
        graph = build_extraction_graph()

        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            result = graph.invoke(
                {
                    "email_text": email_text,
                    "reference_date": "2026-05-23",
                    "timezone": "Asia/Seoul",
                }
            )

        self.assertEqual(result["provider"], "rule_fallback")
        self.assertEqual(result["extraction"].intent, "schedule_meeting")
        self.assertIn("GEMINI_API_KEY", result["error"])

    def test_graph_fallback_normalizes_then_schedules(self) -> None:
        email_text = Path("data/samples/email_001.txt").read_text(encoding="utf-8")
        graph = build_extraction_graph()

        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            result = graph.invoke(
                {
                    "email_text": email_text,
                    "reference_date": "2026-05-23",
                    "timezone": "Asia/Seoul",
                    "calendar_path": "data/calendars/synthetic_calendar_001.json",
                }
            )

        self.assertEqual(result["provider"], "rule_fallback")
        self.assertEqual(result["recommendation"].status, "selected")
        self.assertEqual(result["place_recommendation"].status, "selected")
        self.assertEqual(result["reservation_result"].status, "confirmed")
        self.assertEqual(result["reply_draft"].status, "ready")
        self.assertEqual(
            result["recommendation"].selected.candidate.start,
            "2026-05-27T17:00:00+09:00",
        )

    def test_graph_runs_scheduling_when_calendar_path_is_given(self) -> None:
        email_text = Path("data/samples/extraction_001.normalized.json").read_text(encoding="utf-8")
        graph = build_extraction_graph()

        with patch("src.email_agent.graph.parse_email_thread") as parse_mock:
            from src.email_agent.schema import ExtractionResult

            parse_mock.return_value = None
            with patch("src.email_agent.graph.GeminiConstraintExtractor") as extractor_mock:
                extractor_mock.return_value.extract.return_value = ExtractionResult.model_validate_json(email_text)
                result = graph.invoke(
                    {
                        "email_text": "Subject: normalized fixture",
                        "reference_date": "2026-05-23",
                        "timezone": "Asia/Seoul",
                        "calendar_path": "data/calendars/synthetic_calendar_001.json",
                    }
                )

        self.assertEqual(result["provider"], "gemini")
        self.assertEqual(result["recommendation"].status, "selected")
        self.assertEqual(
            result["recommendation"].selected.candidate.start,
            "2026-05-27T17:00:00+09:00",
        )


if __name__ == "__main__":
    unittest.main()
