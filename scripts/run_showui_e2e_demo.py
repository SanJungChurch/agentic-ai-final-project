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
    parser = argparse.ArgumentParser(description="Run the end-to-end ShowUI reservation demo.")
    parser.add_argument("--sample", default="data/samples/email_001.txt")
    parser.add_argument("--reference-date", default="auto")
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--llm-provider", default=None)
    parser.add_argument("--llm-model", default=None, help="Optional model id, for example LGAI-EXAONE/EXAONE-4.0-1.2B.")
    parser.add_argument("--selected-date", default=None)
    parser.add_argument("--calendar", default="data/calendars/synthetic_calendar_001.json")
    parser.add_argument("--place-provider", choices=["mock", "html", "kakao"], default="html")
    parser.add_argument("--place-search-html", default="data/place_search/soongsil_cafes.html")
    parser.add_argument(
        "--reservation-target",
        default="file:///C:/VSProject/agentic/data/reservation/mock_reservation.html",
    )
    parser.add_argument(
        "--runner-command",
        default=f"{sys.executable} scripts/showui_reservation_runner.py --headless --dom-fallback",
        help=(
            "ShowUI runner command. The default uses DOM fallback for deterministic local demo. "
            "Use without --dom-fallback to call the official ShowUI HF Space or SHOWUI_GRADIO_SOURCE."
        ),
    )
    parser.add_argument(
        "--local-showui",
        action="store_true",
        help="Use local GPU ShowUI-2B grounding instead of deterministic DOM fallback.",
    )
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    runner_command = args.runner_command
    if args.local_showui:
        runner_command = f"{sys.executable} scripts/showui_reservation_runner.py --headless --showui-source local"

    graph = build_extraction_graph()
    result = graph.invoke(
        {
            "email_text": Path(args.sample).read_text(encoding="utf-8"),
            "reference_date": args.reference_date,
            "timezone": args.timezone,
            "llm_provider": args.llm_provider,
            "llm_model": args.llm_model,
            "selected_date": args.selected_date,
            "calendar_path": args.calendar,
            "place_provider": args.place_provider,
            "place_search_html": args.place_search_html,
            "reservation_provider": "showui",
            "reservation_target": args.reservation_target,
            "showui_runner_command": runner_command,
        }
    )

    output = {
        "provider": result.get("provider"),
        "llm_model": result.get("llm_model"),
        "error": result.get("error"),
        "reference_date": result.get("reference_date"),
        "reference_date_source": result.get("reference_date_source"),
        "reference_date_error": result.get("reference_date_error"),
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
