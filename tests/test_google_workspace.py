import base64
import unittest

from src.email_agent.google_workspace import _extract_text_from_payload


def _gmail_data(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8").rstrip("=")


class GoogleWorkspaceTest(unittest.TestCase):
    def test_multipart_alternative_prefers_plain_text_once(self) -> None:
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _gmail_data("회의 가능 시간은 수요일 5시입니다.")},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _gmail_data("<p>회의 가능 시간은 수요일 5시입니다.</p>")},
                },
            ],
        }

        text = _extract_text_from_payload(payload)

        self.assertEqual(text, "회의 가능 시간은 수요일 5시입니다.")
        self.assertEqual(text.count("수요일 5시"), 1)

    def test_nested_parts_drop_exact_duplicate_text(self) -> None:
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _gmail_data("내일 면담 가능합니다.")},
                },
                {
                    "mimeType": "text/plain",
                    "body": {"data": _gmail_data("내일 면담 가능합니다.")},
                },
            ],
        }

        self.assertEqual(_extract_text_from_payload(payload), "내일 면담 가능합니다.")


if __name__ == "__main__":
    unittest.main()
