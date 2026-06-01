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
    parser.add_argument("--calendar", default=None, help="Optional path to a synthetic calendar JSON file.")
    parser.add_argument("--place-provider", choices=["mock", "html", "kakao"], default=None)
    parser.add_argument("--place-search-html", default=None, help="Optional static HTML place search result file.")
    args = parser.parse_args()

    app = build_extraction_graph()
    result = app.invoke(
        {
            "email_text": Path(args.sample).read_text(encoding="utf-8"),
            "reference_date": args.reference_date,
            "timezone": args.timezone,
            "calendar_path": args.calendar,
            "place_provider": args.place_provider,
            "place_search_html": args.place_search_html,
        }
    )

    extraction = result["extraction"].model_dump()
    output = {
        "provider": result.get("provider"),
        "error": result.get("error"),
        "extraction": extraction,
        "recommendation": result["recommendation"].model_dump() if result.get("recommendation") else None,
        "place_recommendation": result["place_recommendation"].model_dump() if result.get("place_recommendation") else None,
        "reply_draft": result["reply_draft"].model_dump() if result.get("reply_draft") else None,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
