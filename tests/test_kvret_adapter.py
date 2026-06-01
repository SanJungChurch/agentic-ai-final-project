import unittest

from src.email_agent.kvret_adapter import build_kvret_row


class KvretAdapterTest(unittest.TestCase):
    def test_build_kvret_schedule_row(self) -> None:
        item = {
            "scenario": {
                "task": {"intent": "schedule"},
                "uuid": "abc",
            },
            "dialogue": [
                {
                    "turn": "driver",
                    "data": {"utterance": "schedule dinner with my sister on the 11th at 4pm"},
                },
                {
                    "turn": "assistant",
                    "data": {
                        "utterance": "Set.",
                        "slots": {
                            "event": "dinner",
                            "party": "sister",
                            "date": "11th",
                            "time": "4pm",
                            "room": "conference room",
                        },
                    },
                },
            ],
        }

        row = build_kvret_row(item, split="test")

        self.assertIsNotNone(row)
        self.assertEqual(row["id"], "kvret_test_abc")
        self.assertEqual(row["gold"]["participants"], ["sister"])
        self.assertEqual(row["gold"]["candidate_times"][0]["expression"], "11th 4pm")
        self.assertEqual(row["gold"]["location_preference"], "conference room")
        self.assertIn("driver: schedule dinner", row["text"])


if __name__ == "__main__":
    unittest.main()
