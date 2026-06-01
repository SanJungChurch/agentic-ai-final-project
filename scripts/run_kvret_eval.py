from __future__ import annotations

"""Run Gemini extraction evaluation on the KVRET schedule subset.

Edit the CONFIG section, then run:

    python scripts/run_kvret_eval.py
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.email_agent.llm_benchmark import run_kvret_llm_benchmark


# =============================================================================
# CONFIG: change these values for your experiment
# =============================================================================

# KVRET folder structure:
#   dataset/kvret/kvret_train_public.json
#   dataset/kvret/kvret_dev_public.json
#   dataset/kvret/kvret_test_public.json
#   dataset/kvret/kvret_entities.json
KVRET_ROOT = Path("dataset/kvret")

# Choose one of: "train", "dev", "test"
SPLIT = "test"

# Number of schedule-domain examples to evaluate.
LIMIT = 100

# Skip this many schedule-domain examples before evaluation.
OFFSET = 0

# Reference date for relative time expressions.
REFERENCE_DATE = "2026-05-27"

# Timezone passed to the Gemini prompt.
TIMEZONE = "Asia/Seoul"

# Increase this if you hit Gemini rate limits.
SLEEP_SECONDS = 0.0

# Report output path. The reports/ folder is gitignored.
OUTPUT_PATH = Path("reports/gemini_kvret_test_100_eval.json")


def main() -> None:
    report = run_kvret_llm_benchmark(
        KVRET_ROOT,
        split=SPLIT,
        limit=LIMIT,
        offset=OFFSET,
        reference_date=REFERENCE_DATE,
        timezone=TIMEZONE,
        sleep_seconds=SLEEP_SECONDS,
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    OUTPUT_PATH.write_text(text + "\n", encoding="utf-8")

    print(text)
    print()
    print(f"[saved] {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
