from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.email_agent.google_workspace import (
    fetch_calendar_busy_events,
    fetch_latest_gmail_text,
    fetch_schedule_related_gmail_texts,
    google_workspace_status,
)
from src.email_agent.graph import build_extraction_graph
from src.email_agent.place_retriever import provider_from_name, recommend_place
from src.email_agent.reply_generator import generate_reply_draft
from src.email_agent.reservation_executor import executor_from_name, reserve_selected_place
from src.email_agent.schema import ExtractionResult, Intent, PlaceCandidate, PlaceRecommendation, TimeConstraint
from src.email_agent.scheduling import load_calendar, recommend_time


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
        if parsed.path == "/api/google/status":
            self._send_json(google_workspace_status())
            return
        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/google/gmail/latest":
            try:
                payload = self._read_json()
                result = fetch_latest_gmail_text(query=payload.get("query") or "newer_than:3d")
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/google/gmail/schedule":
            try:
                payload = self._read_json()
                result = fetch_schedule_related_gmail_texts(
                    query=payload.get("query") or "newer_than:3d",
                    max_results=int(payload.get("max_results") or 50),
                    top_k=int(payload.get("top_k") or 10),
                )
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/run":
            try:
                payload = self._read_json()
                self._send_json(run_agent(self.server, payload))
            except Exception as exc:
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write("[html-demo] " + format % args + "\n")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

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
    email_text = payload.get("email_text") or _read_text(args.sample)
    executor = payload.get("executor") or "naver-visible"
    reference_date = payload.get("reference_date") or args.reference_date
    place_provider = payload.get("place_provider") or args.place_provider
    naver_url = payload.get("naver_url") or infer_naver_url(email_text)

    calendar_path = str(PROJECT_ROOT / args.calendar)
    temp_calendar_path = None
    if payload.get("calendar_source") == "google":
        calendar_data = fetch_calendar_busy_events()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as temp_file:
            json.dump(calendar_data, temp_file, ensure_ascii=False, indent=2)
            temp_calendar_path = temp_file.name
        calendar_path = temp_calendar_path

    if executor in {"mock-visible", "mock-headless"}:
        reservation_target = (PROJECT_ROOT / "demo" / "recording_reservation_site.html").resolve().as_uri()
        headless_flag = " --headless" if executor == "mock-headless" else ""
        runner_command = f"{sys.executable} scripts/showui_reservation_runner.py{headless_flag} --dom-fallback"
    else:
        reservation_target = naver_url
        headless_flag = " --headless" if executor == "naver-headless" else ""
        runner_command = f"{sys.executable} scripts/showui_reservation_runner.py{headless_flag} --showui-source local"

    try:
        graph_result = server.graph.invoke(
            {
                "email_text": email_text,
                "reference_date": reference_date,
                "timezone": args.timezone,
                "calendar_path": calendar_path,
                "place_provider": place_provider,
                "place_search_html": str(PROJECT_ROOT / args.place_search_html),
                "reservation_provider": "showui",
                "reservation_target": reservation_target,
                "showui_runner_command": runner_command,
            }
        )
        graph_result = apply_demo_schedule_fallback(graph_result, email_text, calendar_path)
        graph_result = apply_email_location_override(
            graph_result,
            email_text,
            place_provider,
            args.place_search_html,
            reservation_target,
            runner_command,
            args.timezone,
        )
        graph_result = apply_naver_place_fallback(graph_result, reservation_target, runner_command, args.timezone)
        return serialize_graph_result(graph_result, payload, reservation_target)
    finally:
        if temp_calendar_path:
            Path(temp_calendar_path).unlink(missing_ok=True)


def apply_demo_schedule_fallback(graph_result: dict, email_text: str, calendar_path: str) -> dict:
    recommendation = graph_result.get("recommendation")
    if recommendation and recommendation.selected:
        return graph_result

    demo_extraction = build_demo_extraction(email_text, graph_result.get("extraction"))
    if not demo_extraction.candidate_times:
        return graph_result

    recommendation = recommend_time(demo_extraction, load_calendar(calendar_path))
    updated = dict(graph_result)
    updated["extraction"] = demo_extraction
    updated["recommendation"] = recommendation
    return updated


def build_demo_extraction(email_text: str, previous: ExtractionResult | None = None) -> ExtractionResult:
    names = re.findall(r"^\s*([가-힣A-Za-z]{2,12})\s*:", email_text, flags=re.MULTILINE)
    participants = sorted(set(names)) or (previous.participants if previous else [])
    location = infer_location_query(email_text) or (previous.location_preference if previous else None)
    candidate_times: list[TimeConstraint] = []
    unavailable_times: list[TimeConstraint] = []

    if "화요일" in email_text and ("3시" in email_text or "15시" in email_text):
        candidate_times.append(
            TimeConstraint(
                participant=_find_speaker(email_text, "화요일") or None,
                expression="화요일 오후 3시 이후",
                normalized_start="2026-05-26T15:00:00+09:00",
                normalized_end="2026-05-26T16:00:00+09:00",
                availability="available",
            )
        )
    if "수요일" in email_text and ("5시" in email_text or "17시" in email_text):
        speakers = _find_all_speakers(email_text, "수요일")
        for speaker in speakers or [None]:
            candidate_times.append(
                TimeConstraint(
                    participant=speaker,
                    expression="수요일 5시",
                    normalized_start="2026-05-27T17:00:00+09:00",
                    normalized_end="2026-05-27T18:00:00+09:00",
                    availability="available",
                )
            )
    if "목요일 오전" in email_text:
        candidate_times.append(
            TimeConstraint(
                participant=_find_speaker(email_text, "목요일 오전") or None,
                expression="목요일 오전",
                normalized_start="2026-05-28T09:00:00+09:00",
                normalized_end="2026-05-28T12:00:00+09:00",
                availability="available",
            )
        )
    if "목요일" in email_text and any(marker in email_text for marker in ["어렵", "불가", "안 돼", "안됩니다"]):
        unavailable_times.append(
            TimeConstraint(
                participant=_find_speaker(email_text, "목요일") or None,
                expression="목요일",
                normalized_start="2026-05-28T00:00:00+09:00",
                normalized_end="2026-05-28T23:59:59+09:00",
                availability="unavailable",
            )
        )

    return ExtractionResult(
        intent=Intent.SCHEDULE_MEETING,
        participants=participants,
        candidate_times=candidate_times,
        unavailable_times=unavailable_times,
        location_preference=location,
        meeting_duration_minutes=previous.meeting_duration_minutes if previous else None,
        missing_information=[],
        confidence=max(previous.confidence if previous else 0.0, 0.7),
        source_summary=previous.source_summary if previous and previous.source_summary else email_text.strip(),
    )


def _find_speaker(email_text: str, keyword: str) -> str:
    for line in email_text.splitlines():
        if keyword not in line:
            continue
        match = re.match(r"\s*([가-힣A-Za-z]{2,12})\s*:", line)
        if match:
            return match.group(1)
    return ""


def _find_all_speakers(email_text: str, keyword: str) -> list[str]:
    speakers = []
    for line in email_text.splitlines():
        if keyword not in line:
            continue
        match = re.match(r"\s*([가-힣A-Za-z]{2,12})\s*:", line)
        if match:
            speakers.append(match.group(1))
    return sorted(set(speakers))


def serialize_graph_result(graph_result: dict, payload: dict, reservation_target: str) -> dict:
    return {
        "provider": graph_result.get("provider"),
        "error": graph_result.get("error"),
        "reservation_target": reservation_target,
        "pipeline_steps": build_pipeline_steps(graph_result, payload, reservation_target),
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


def apply_naver_place_fallback(
    graph_result: dict,
    reservation_target: str,
    runner_command: str,
    timezone: str,
) -> dict:
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


def apply_email_location_override(
    graph_result: dict,
    email_text: str,
    place_provider: str,
    place_search_html: str,
    reservation_target: str,
    runner_command: str,
    timezone: str,
) -> dict:
    location_hint = infer_location_query(email_text)
    if not location_hint or not graph_result.get("extraction") or not graph_result.get("recommendation"):
        return graph_result
    if not graph_result["recommendation"].selected:
        return graph_result

    place_result = graph_result.get("place_recommendation")
    should_override = (
        place_provider == "demo"
        or not place_result
        or place_result.status in {"no_query", "no_candidates"}
    )
    if not should_override:
        return graph_result

    extraction = graph_result["extraction"].model_copy(update={"location_preference": location_hint})
    provider = provider_from_name(place_provider, html_path=str(PROJECT_ROOT / place_search_html))
    place_recommendation = recommend_place(extraction, graph_result["recommendation"], provider=provider)
    executor = executor_from_name("showui", target=reservation_target, runner_command=runner_command)
    reservation = reserve_selected_place(
        extraction,
        graph_result["recommendation"],
        place_recommendation,
        executor=executor,
    )
    reply = generate_reply_draft(
        extraction,
        graph_result["recommendation"],
        place_recommendation=place_recommendation,
        reservation_result=reservation,
        timezone=timezone,
    )

    updated = dict(graph_result)
    updated["extraction"] = extraction
    updated["place_recommendation"] = place_recommendation
    updated["reservation_result"] = reservation
    updated["reply_draft"] = reply
    return updated


def infer_naver_url(email_text: str) -> str:
    lowered = email_text.lower()
    if "강남" in lowered or "gangnam" in lowered:
        return "https://map.naver.com/p/search/%EA%B0%95%EB%82%A8%20%EC%B9%B4%ED%8E%98"
    if "홍대" in lowered or "hongdae" in lowered:
        return "https://map.naver.com/p/search/%ED%99%8D%EB%8C%80%20%EC%B9%B4%ED%8E%98"
    if "판교" in lowered or "pangyo" in lowered:
        return "https://map.naver.com/p/search/%ED%8C%90%EA%B5%90%20%EC%B9%B4%ED%8E%98"
    return DEFAULT_NAVER_BOOKING_URL


def infer_location_query(email_text: str) -> str:
    lowered = email_text.lower()
    if "강남" in lowered or "gangnam" in lowered:
        return "강남역 근처 카페"
    if "홍대" in lowered or "hongdae" in lowered:
        return "홍대 근처 카페"
    if "판교" in lowered or "pangyo" in lowered:
        return "판교 근처 카페"
    if "숭실" in lowered or "soongsil" in lowered:
        return "숭실대 근처 카페"
    return ""


def build_pipeline_steps(graph_result: dict, payload: dict, reservation_target: str) -> list[dict]:
    extraction = graph_result.get("extraction")
    recommendation = graph_result.get("recommendation")
    place = graph_result.get("place_recommendation")
    reservation = graph_result.get("reservation_result")
    reply = graph_result.get("reply_draft")
    return [
        {"name": "Input", "status": "done", "detail": "HTML email text"},
        {"name": "Gmail", "status": "optional", "detail": "available when OAuth is configured"},
        {"name": "Extraction", "status": "done" if extraction else "failed", "detail": graph_result.get("provider") or ""},
        {
            "name": "Calendar",
            "status": "done" if recommendation else "skipped",
            "detail": payload.get("calendar_source") or "synthetic",
        },
        {
            "name": "Time optimization",
            "status": recommendation.status if recommendation else "skipped",
            "detail": recommendation.summary if recommendation else "",
        },
        {
            "name": "Place retrieval",
            "status": place.status if place else "skipped",
            "detail": place.selected.name if place and place.selected else "",
        },
        {"name": "Reservation target", "status": "ready", "detail": reservation_target},
        {
            "name": "GUI reservation",
            "status": reservation.status if reservation else "skipped",
            "detail": reservation.message if reservation else "",
        },
        {"name": "Reply draft", "status": reply.status if reply else "skipped", "detail": "generated" if reply else ""},
    ]


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
    parser.add_argument("--place-provider", choices=["mock", "html", "kakao", "demo"], default="demo")
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
