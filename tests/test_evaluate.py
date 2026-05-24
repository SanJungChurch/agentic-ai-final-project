from pathlib import Path
import unittest

from src.email_agent.evaluate import evaluate_sample


class EvaluateTest(unittest.TestCase):
    def test_evaluate_sample(self) -> None:
        metrics = evaluate_sample(
            Path("data/samples/email_001.txt"),
            Path("data/samples/email_001.gold.json"),
        )

        self.assertTrue(metrics["participant_exact_match"])
        self.assertGreaterEqual(metrics["candidate_time_f1"], 0.9)
        self.assertGreaterEqual(metrics["unavailable_time_f1"], 0.9)


if __name__ == "__main__":
    unittest.main()
