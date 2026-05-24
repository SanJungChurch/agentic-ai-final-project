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


if __name__ == "__main__":
    unittest.main()
