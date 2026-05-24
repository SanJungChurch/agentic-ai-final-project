from pathlib import Path
import unittest

from src.email_agent.benchmark import run_benchmark


class BenchmarkTest(unittest.TestCase):
    def test_week1_event_extraction_benchmark_runs(self) -> None:
        result = run_benchmark(Path("data/benchmarks/week1_event_extraction.jsonl"))

        self.assertEqual(result["num_cases"], 3)
        self.assertGreaterEqual(result["macro_participant_exact_match"], 0.9)
        self.assertGreaterEqual(result["macro_candidate_time_f1"], 0.8)
        self.assertGreaterEqual(result["macro_unavailable_time_f1"], 0.8)


if __name__ == "__main__":
    unittest.main()
