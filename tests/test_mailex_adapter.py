from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from src.email_agent.mailex_adapter import load_mailex_rows


class MailExAdapterTest(unittest.TestCase):
    def test_load_mailex_rows_from_split(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            split = root / "test"
            split.mkdir()
            fixture = {
                "sentences": [
                    ["Alice", "can", "meet", "Bob", "on", "Monday", "at", "Cafe", "A", "."]
                ],
                "events": {
                    "turn_0": {
                        "Request_Meeting_Data": {
                            "labels": [
                                [
                                    "Request_Meeting_Data:B-Meeting Members",
                                    "O",
                                    "O",
                                    "Request_Meeting_Data:B-Meeting Members",
                                    "O",
                                    "Request_Meeting_Data:B-Meeting Date",
                                    "O",
                                    "Request_Meeting_Data:B-Meeting Location",
                                    "Request_Meeting_Data:I-Meeting Location",
                                    "O",
                                ]
                            ]
                        }
                    }
                },
            }
            (split / "sample.json").write_text(json.dumps(fixture), encoding="utf-8")

            rows = load_mailex_rows(root, split="test", limit=1)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["gold"]["participants"], ["Alice", "Bob"])
        self.assertEqual(rows[0]["gold"]["candidate_times"][0]["expression"], "Monday")
        self.assertEqual(rows[0]["gold"]["location_preference"], "Cafe A")
