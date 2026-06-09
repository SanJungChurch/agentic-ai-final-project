import unittest

from src.email_agent.llm_extractor import _reference_date_from_response
from src.email_agent.schema import EmailThread


class LlmExtractorTest(unittest.TestCase):
    def test_reference_date_from_llm_json(self) -> None:
        thread = EmailThread(subject="meeting", body="Date: Tue, 26 May 2026 09:00:00 +0900")

        self.assertEqual(
            _reference_date_from_response('{"reference_date": "2026-05-27", "evidence": "body"}', thread),
            "2026-05-27",
        )

    def test_reference_date_falls_back_to_email_header(self) -> None:
        thread = EmailThread(subject="meeting", body="Date: Tue, 26 May 2026 09:00:00 +0900\n\n내일 회의")

        self.assertEqual(_reference_date_from_response("not json", thread), "2026-05-26")


if __name__ == "__main__":
    unittest.main()
