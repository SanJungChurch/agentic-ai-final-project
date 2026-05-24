from __future__ import annotations

import argparse
import json

from .extractor import extract_from_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract scheduling constraints from an email thread.")
    parser.add_argument("--sample", required=True, help="Path to an email thread text file.")
    args = parser.parse_args()

    result = extract_from_file(args.sample)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
