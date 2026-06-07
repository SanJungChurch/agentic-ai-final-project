from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.email_agent.graph import build_extraction_graph
from src.email_agent.reply_generator import generate_reply_draft
from src.email_agent.reservation_executor import executor_from_name, reserve_selected_place
from src.email_agent.schema import PlaceCandidate, PlaceRecommendation


DEFAULT_NAVER_BOOKING_URL = (
    "https://map.naver.com/p/search/%EC%88%AD%EC%8B%A4%EB%8C%80%20%EC%B9%B4%ED%8E%98/"
    "place/1649187599?c=17.24,0,0,0,dh&placePath=/booking?entry=bmp&from=map&fromPanelNum=2"
    "&timestamp=202606080430&locale=ko&svcName=map_pcv5"
    "&searchText=%EC%88%AD%EC%8B%A4%EB%8C%80%20%EC%B9%B4%ED%8E%98"
)


class DemoServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class, *, args: argparse.Namespace) -> None:
        super().__init__(server_address, handler_class)
        self.args = args
        self.graph = build_extraction_graph()


class DemoHandler(BaseHTTPRequestHandler):
    server: DemoServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_file(PROJECT_ROOT / "demo" / "email_to_naver_demo.html", "text/html; charset=utf-8")
            return

        if parsed.path.startswith("/demo/"):
            requested = PROJECT_ROOT / unquote(parsed.path.lstrip("/"))
            if _is_within(requested, PROJECT_ROOT / "demo") and requested.exists():
                self._send_file(requested, _content_type(requested))
                return

        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/run":
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            result = run_agent(self.server, payload)
            self._send_json(result)
        except Exception as exc:
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write("[html-demo] " + format % args + "\n")

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_agent(server: DemoServer, payload: dict) -> dict:
    args = server.args
    executor = payload.get("executor") or "naver-visible"
    naver_url = payload.get("naver_url") or DEFAULT_NAVER_BOOKING_URL
    email_text = payload.get("email_text") or _read_text(args.sample)
    reference_date = payload.get("reference_date") or args.reference_date

    if executor in {"mock-visible", "mock-headless"}:
        reservation_target = (PROJECT_ROOT / "demo" / "recording_reservation_site.html").resolve().as_uri()
        headless_flag = " --headless" if executor == "mock-headless" else ""
        runner_command = f"{sys.executable} scripts/showui_reservation_runner.py{headless_flag} --dom-fallback"
    else:
        reservation_target = naver_url
        headless_flag = " --headless" if executor == "naver-headless" else ""
        runner_command = f"{sys.executable} scripts/showui_reservation_runner.py{headless_flag} --showui-source local"

    graph_result = server.graph.invoke(
        {
            "email_text": email_text,
            "reference_date": reference_date,
            "timezone": args.timezone,
            "calendar_path": str(PROJECT_ROOT / args.calendar),
            "place_provider": args.place_provider,
            "place_search_html": str(PROJECT_ROOT / args.place_search_html),
            "reservation_provider": "showui",
            "reservation_target": reservation_target,
            "showui_runner_command": runner_command,
        }
    )
    graph_result = apply_naver_place_fallback(graph_result, reservation_target, runner_command, args.timezone)

    output = {
        "provider": graph_result.get("provider"),
        "error": graph_result.get("error"),
        "reservation_target": reservation_target,
        "extraction": graph_result["extraction"].model_dump() if graph_result.get("extraction") else None,
        "recommendation": graph_result["recommendation"].model_dump() if graph_result.get("recommendation") else None,
        "place_recommendation": graph_result["place_recommendation"].model_dump()
        if graph_result.get("place_recommendation")
        else None,
        "reservation_result": graph_result["reservation_result"].model_dump()
        if graph_result.get("reservation_result")
        else None,
        "reply_draft": graph_result["reply_draft"].model_dump() if graph_result.get("reply_draft") else None,
    }
    return output


def apply_naver_place_fallback(
    graph_result: dict,
    reservation_target: str,
    runner_command: str,
    timezone: str,
) -> dict:
    """Keep the HTML demo moving when the Naver URL already fixes the place.

    Gemini occasionally omits `location_preference` for short Korean demo text.
    In the actual demo screen, however, the Naver booking URL is already a
    concrete downstream place target, so we can safely inject that selected
    place for the reservation stage.
    """

    reservation_result = graph_result.get("reservation_result")
    if reservation_result and reservation_result.status != "skipped":
        return graph_result
    if not graph_result.get("extraction") or not graph_result.get("recommendation"):
        return graph_result
    if not graph_result["recommendation"].selected:
        return graph_result

    fallback_place = PlaceRecommendation(
        query="네이버 예약 URL",
        status="selected",
        selected=PlaceCandidate(
            name="카페 온더힐",
            address="서울 동작구 상도로 369 숭실대입구역 근처",
            category="cafe",
            source_url=reservation_target,
            availability_hint="네이버 예약 페이지로 확인",
            score=1.0,
        ),
        candidates=[
            PlaceCandidate(
                name="카페 온더힐",
                address="서울 동작구 상도로 369 숭실대입구역 근처",
                category="cafe",
                source_url=reservation_target,
                availability_hint="네이버 예약 페이지로 확인",
                score=1.0,
            )
        ],
        summary="네이버 예약 URL이 지정되어 해당 장소를 예약 target으로 사용합니다.",
    )
    executor = executor_from_name("showui", target=reservation_target, runner_command=runner_command)
    reservation = reserve_selected_place(
        graph_result["extraction"],
        graph_result["recommendation"],
        fallback_place,
        executor=executor,
    )
    reply = generate_reply_draft(
        graph_result["extraction"],
        graph_result["recommendation"],
        place_recommendation=fallback_place,
        reservation_result=reservation,
        timezone=timezone,
    )

    updated = dict(graph_result)
    updated["place_recommendation"] = fallback_place
    updated["reservation_result"] = reservation
    updated["reply_draft"] = reply
    return updated


def _read_text(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".js":
        return "text/javascript; charset=utf-8"
    return "application/octet-stream"


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the HTML-only e-mail to Naver booking demo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--sample", default="data/samples/email_001.txt")
    parser.add_argument("--reference-date", default="2026-05-23")
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--calendar", default="data/calendars/synthetic_calendar_001.json")
    parser.add_argument("--place-provider", choices=["mock", "html", "kakao"], default="html")
    parser.add_argument("--place-search-html", default="data/place_search/soongsil_cafes.html")
    args = parser.parse_args()

    server = DemoServer((args.host, args.port), DemoHandler, args=args)
    print(f"HTML demo: http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
