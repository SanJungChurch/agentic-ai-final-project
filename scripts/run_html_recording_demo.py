from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.email_agent.graph import build_extraction_graph


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the HTML-only recording demo with a local reservation page.")
    parser.add_argument("--sample", default="data/samples/email_001.txt")
    parser.add_argument("--reference-date", default="2026-05-23")
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--calendar", default="data/calendars/synthetic_calendar_001.json")
    parser.add_argument("--place-provider", choices=["mock", "html", "kakao"], default="html")
    parser.add_argument("--place-search-html", default="data/place_search/soongsil_cafes.html")
    parser.add_argument("--reservation-html", default="demo/recording_reservation_site.html")
    parser.add_argument(
        "--executor",
        choices=["dom", "local-showui"],
        default="dom",
        help="Use dom for deterministic recording, or local-showui for GPU ShowUI grounded clicks.",
    )
    parser.add_argument("--headless", action="store_true", help="Run the browser without showing the reservation UI.")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    reservation_target = (PROJECT_ROOT / args.reservation_html).resolve().as_uri()
    runner_flags = "--dom-fallback" if args.executor == "dom" else "--showui-source local"
    headless_flag = " --headless" if args.headless else ""
    runner_command = f"{sys.executable} scripts/showui_reservation_runner.py{headless_flag} {runner_flags}"

    graph = build_extraction_graph()
    result = graph.invoke(
        {
            "email_text": (PROJECT_ROOT / args.sample).read_text(encoding="utf-8"),
            "reference_date": args.reference_date,
            "timezone": args.timezone,
            "calendar_path": str(PROJECT_ROOT / args.calendar),
            "place_provider": args.place_provider,
            "place_search_html": str(PROJECT_ROOT / args.place_search_html),
            "reservation_provider": "showui",
            "reservation_target": reservation_target,
            "showui_runner_command": runner_command,
        }
    )

    output = {
        "provider": result.get("provider"),
        "error": result.get("error"),
        "extraction": result["extraction"].model_dump() if result.get("extraction") else None,
        "recommendation": result["recommendation"].model_dump() if result.get("recommendation") else None,
        "place_recommendation": result["place_recommendation"].model_dump()
        if result.get("place_recommendation")
        else None,
        "reservation_result": result["reservation_result"].model_dump() if result.get("reservation_result") else None,
        "reply_draft": result["reply_draft"].model_dump() if result.get("reply_draft") else None,
    }

    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
