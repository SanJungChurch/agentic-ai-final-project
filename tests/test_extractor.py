from pathlib import Path
import unittest

from src.email_agent.extractor import extract_from_file
from src.email_agent.schema import Intent


class ExtractorTest(unittest.TestCase):
    def test_extract_sample_email(self) -> None:
        sample = Path("data/samples/email_001.txt")
        result = extract_from_file(sample)

        self.assertEqual(result.intent, Intent.SCHEDULE_MEETING)
        self.assertEqual(set(result.participants), {"동욱", "정인", "민수"})
        self.assertTrue(result.candidate_times)
        self.assertTrue(any(item.expression == "수요일 5시" for item in result.candidate_times))
        self.assertTrue(any(item.expression == "목요일" for item in result.unavailable_times))
        self.assertIsNotNone(result.location_preference)


if __name__ == "__main__":
    unittest.main()
