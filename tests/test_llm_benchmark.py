from pathlib import Path
import unittest

from src.email_agent.llm_benchmark import run_llm_benchmark, run_mailex_llm_benchmark
from src.email_agent.schema import EmailThread, ExtractionResult


class FakeExtractor:
    def extract(
        self,
        thread: EmailThread,
        *,
        reference_date: str | None = None,
        timezone: str = "Asia/Seoul",
    ) -> ExtractionResult:
        return ExtractionResult.model_validate(
            {
                "intent": "schedule_meeting",
                "participants": ["동욱", "정인", "민수"],
                "candidate_times": [
                    {
                        "participant": "동욱",
                        "expression": "화요일 오후 3시 이후",
                        "availability": "available",
                    },
                    {
                        "participant": "정인",
                        "expression": "수요일 5시",
                        "availability": "available",
                    },
                    {
                        "participant": "정인",
                        "expression": "목요일 오전",
                        "availability": "available",
                    },
                    {
                        "participant": "민수",
                        "expression": "수요일 5시",
                        "availability": "available",
                    },
                ],
                "unavailable_times": [
                    {
                        "participant": "민수",
                        "expression": "목요일",
                        "availability": "unavailable",
                    }
                ],
                "location_preference": "숭실대 근처 카페",
                "meeting_duration_minutes": None,
                "missing_information": [],
                "confidence": 0.9,
                "source_summary": "",
            }
        )


class LlmBenchmarkTest(unittest.TestCase):
    def test_run_llm_benchmark_with_fake_extractor(self) -> None:
        report = run_llm_benchmark(
            Path("data/benchmarks/week1_event_extraction.jsonl"),
            limit=1,
            extractor=FakeExtractor(),
        )

        self.assertEqual(report["summary"]["num_cases"], 1)
        self.assertEqual(report["summary"]["api_success_rate"], 1.0)
        self.assertEqual(report["summary"]["macro_candidate_time_f1"], 1.0)


if __name__ == "__main__":
    unittest.main()
