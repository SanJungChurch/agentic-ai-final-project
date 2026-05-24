import unittest

from src.email_agent.json_utils import parse_json_object


class JsonUtilsTest(unittest.TestCase):
    def test_parse_fenced_json(self) -> None:
        data = parse_json_object('```json\n{"intent": "schedule_meeting"}\n```')
        self.assertEqual(data["intent"], "schedule_meeting")

    def test_parse_json_with_extra_text(self) -> None:
        data = parse_json_object('Here is the result: {"intent": "other"}')
        self.assertEqual(data["intent"], "other")


if __name__ == "__main__":
    unittest.main()
