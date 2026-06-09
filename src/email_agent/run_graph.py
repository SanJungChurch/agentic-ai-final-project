from __future__ import annotations

import argparse
import json
from pathlib import Path

from .graph import build_extraction_graph


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LangGraph e-mail extraction workflow.")
    parser.add_argument("--sample", required=True, help="Path to an email thread text file.")
    parser.add_argument(
        "--reference-date",
        default=None,
        help="Reference date for relative time expressions. Use auto or omit it to infer from the e-mail.",
    )
    parser.add_argument("--timezone", default="Asia/Seoul", help="Timezone for normalization.")
    parser.add_argument("--llm-provider", default=None, help="LLM provider: gemini, ollama, qwen, or an alias such as qwen4bmodel.")
    parser.add_argument("--llm-model", default=None, help="Optional Ollama model tag, for example qwen3:4b.")
    parser.add_argument("--selected-date", default=None, help="Preferred meeting date in YYYY-MM-DD format.")
    parser.add_argument("--calendar", default=None, help="Optional path to a synthetic calendar JSON file.")
    parser.add_argument("--place-provider", choices=["mock", "html", "kakao"], default=None)
    parser.add_argument("--place-search-html", default=None, help="Optional static HTML place search result file.")
    parser.add_argument("--reservation-provider", choices=["mock", "html", "showui"], default=None)
    parser.add_argument("--reservation-html", default=None, help="Optional static HTML reservation page.")
    parser.add_argument("--reservation-target", default=None, help="Reservation page URL or local path for ShowUI.")
    parser.add_argument("--showui-runner-command", default=None, help="Optional command that runs the ShowUI executor.")
    args = parser.parse_args()

    app = build_extraction_graph()
    result = app.invoke(
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
            "reservation_provider": args.reservation_provider,
            "reservation_html": args.reservation_html,
            "reservation_target": args.reservation_target,
            "showui_runner_command": args.showui_runner_command,
        }
    )

    extraction = result["extraction"].model_dump()
    output = {
        "provider": result.get("provider"),
        "llm_model": result.get("llm_model"),
        "error": result.get("error"),
        "reference_date": result.get("reference_date"),
        "reference_date_source": result.get("reference_date_source"),
        "reference_date_error": result.get("reference_date_error"),
        "place_search_query": result.get("place_search_query"),
        "place_search_query_source": result.get("place_search_query_source"),
        "place_search_query_reason": result.get("place_search_query_reason"),
        "extraction": extraction,
        "recommendation": result["recommendation"].model_dump() if result.get("recommendation") else None,
        "place_recommendation": result["place_recommendation"].model_dump() if result.get("place_recommendation") else None,
        "reservation_result": result["reservation_result"].model_dump() if result.get("reservation_result") else None,
        "reply_draft": result["reply_draft"].model_dump() if result.get("reply_draft") else None,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
