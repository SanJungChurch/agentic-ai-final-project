from __future__ import annotations

import argparse
import json
from pathlib import Path

from .graph import build_extraction_graph


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LangGraph e-mail extraction workflow.")
    parser.add_argument("--sample", required=True, help="Path to an email thread text file.")
    parser.add_argument("--reference-date", default=None, help="Reference date for relative time expressions.")
    parser.add_argument("--timezone", default="Asia/Seoul", help="Timezone for normalization.")
    args = parser.parse_args()

    app = build_extraction_graph()
    result = app.invoke(
        {
            "email_text": Path(args.sample).read_text(encoding="utf-8"),
            "reference_date": args.reference_date,
            "timezone": args.timezone,
        }
    )

    extraction = result["extraction"].model_dump()
    output = {
        "provider": result.get("provider"),
        "error": result.get("error"),
        "extraction": extraction,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
