import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.email_agent.graph import build_extraction_graph


class GraphTest(unittest.TestCase):
    def test_graph_falls_back_without_api_key(self) -> None:
        email_text = Path("data/samples/email_001.txt").read_text(encoding="utf-8")
        graph = build_extraction_graph()

        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": ""}, clear=False):
            result = graph.invoke(
                {
                    "email_text": email_text,
                    "reference_date": "2026-05-23",
                    "timezone": "Asia/Seoul",
                    "llm_provider": "gemini",
                }
            )

        self.assertEqual(result["provider"], "rule_fallback")
        self.assertEqual(result["extraction"].intent, "schedule_meeting")
        self.assertIn("GEMINI_API_KEY", result["error"])

    def test_graph_fallback_normalizes_then_schedules(self) -> None:
        email_text = Path("data/samples/email_001.txt").read_text(encoding="utf-8")
        graph = build_extraction_graph()

        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": ""}, clear=False):
            result = graph.invoke(
                {
                    "email_text": email_text,
                    "reference_date": "2026-05-23",
                    "timezone": "Asia/Seoul",
                    "llm_provider": "gemini",
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
            with patch("src.email_agent.graph.create_constraint_extractor") as factory_mock:
                extractor_mock = factory_mock.return_value
                extractor_mock.provider_name = "gemini"
                extractor_mock.extract.return_value = ExtractionResult.model_validate_json(email_text)
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

    def test_graph_passes_qwen_provider_and_auto_reference_date(self) -> None:
        email_text = Path("data/samples/extraction_001.normalized.json").read_text(encoding="utf-8")
        graph = build_extraction_graph()

        with patch("src.email_agent.graph.parse_email_thread") as parse_mock:
            from src.email_agent.schema import EmailThread, ExtractionResult

            parse_mock.return_value = EmailThread(subject="fixture", body="Date: Tue, 26 May 2026 09:00:00 +0900")
            with patch("src.email_agent.graph.create_constraint_extractor") as factory_mock:
                extractor_mock = factory_mock.return_value
                extractor_mock.provider_name = "ollama"
                extractor_mock.infer_reference_date.return_value = "2026-05-26"
                extractor_mock.extract.return_value = ExtractionResult.model_validate_json(email_text)
                result = graph.invoke(
                    {
                        "email_text": "Subject: normalized fixture",
                        "reference_date": "auto",
                        "timezone": "Asia/Seoul",
                        "llm_provider": "qwen",
                    }
                )

        factory_mock.assert_any_call("qwen")
        extractor_mock.extract.assert_called_once()
        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["reference_date"], "2026-05-26")
        self.assertEqual(result["reference_date_source"], "ollama_inferred")


if __name__ == "__main__":
    unittest.main()
