from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.email_agent.google_workspace import (
    fetch_calendar_busy_events,
    fetch_latest_gmail_text,
    fetch_schedule_related_gmail_texts,
    google_workspace_status,
)
from src.email_agent.graph import apply_selected_date_to_candidates, build_extraction_graph
from src.email_agent.llm_extractor import create_constraint_extractor
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
                result["groups"] = group_gmail_messages(result.get("messages", []))
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
        if parsed.path == "/api/run_batch":
            try:
                payload = self._read_json()
                self._send_json(run_agent_batch(self.server, payload))
            except Exception as exc:
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/chat":
            try:
                payload = self._read_json()
                self._send_json(answer_chat(payload))
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
    llm_provider = payload.get("llm_provider") or args.llm_provider
    llm_model = payload.get("llm_model") or args.llm_model
    place_provider = payload.get("place_provider") or args.place_provider
    manual_naver_url = (payload.get("naver_url") or "").strip()
    naver_url = manual_naver_url or infer_naver_url(email_text)
    reservation_target_auto = not manual_naver_url and executor in {"naver-visible", "naver-headless"}
    selected_date = payload.get("selected_date") or None

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
                "llm_provider": llm_provider,
                "llm_model": llm_model,
                "selected_date": selected_date,
                "calendar_path": calendar_path,
                "place_provider": place_provider,
                "place_search_html": str(PROJECT_ROOT / args.place_search_html),
                "reservation_provider": "showui",
                "reservation_target": reservation_target,
                "reservation_target_auto": reservation_target_auto,
                "showui_runner_command": runner_command,
            }
        )
        reservation_target = graph_result.get("reservation_target") or reservation_target
        graph_result = apply_demo_schedule_fallback(graph_result, email_text, calendar_path, selected_date)
        graph_result = refresh_reservation_if_ready(
            graph_result,
            reservation_target,
            reservation_target_auto,
            runner_command,
            args.timezone,
        )
        reservation_target = graph_result.get("reservation_target") or reservation_target
        graph_result = apply_email_location_override(
            graph_result,
            email_text,
            place_provider,
            args.place_search_html,
            reservation_target,
            reservation_target_auto,
            runner_command,
            args.timezone,
        )
        reservation_target = graph_result.get("reservation_target") or reservation_target
        graph_result = refresh_reservation_if_ready(
            graph_result,
            reservation_target,
            reservation_target_auto,
            runner_command,
            args.timezone,
        )
        reservation_target = graph_result.get("reservation_target") or reservation_target
        graph_result = align_place_with_reservation_target(
            graph_result,
            reservation_target,
            runner_command,
            args.timezone,
            force=executor in {"naver-visible", "naver-headless"} and bool(manual_naver_url),
        )
        reservation_target = graph_result.get("reservation_target") or reservation_target
        return serialize_graph_result(graph_result, payload, reservation_target)
    finally:
        if temp_calendar_path:
            Path(temp_calendar_path).unlink(missing_ok=True)


def run_agent_batch(server: DemoServer, payload: dict) -> dict:
    contexts = payload.get("appointment_contexts") or []
    if not contexts:
        contexts = split_appointment_contexts(payload.get("email_text") or "")
    if not contexts:
        contexts = [{"id": "manual_context", "title": "Manual context", "email_text": payload.get("email_text") or ""}]

    results = []
    for index, context in enumerate(contexts, start=1):
        context_payload = dict(payload)
        context_payload["email_text"] = context.get("email_text") or ""
        context_payload["selected_date"] = context.get("selected_date") or payload.get("selected_date")
        result = run_agent(server, context_payload)
        result["appointment_id"] = context.get("id") or f"appointment_{index}"
        result["appointment_title"] = context.get("title") or f"Appointment {index}"
        result["source_message_ids"] = context.get("message_ids", [])
        results.append(result)

    return {
        "mode": "batch",
        "num_appointments": len(results),
        "appointments": results,
    }


def apply_demo_schedule_fallback(
    graph_result: dict,
    email_text: str,
    calendar_path: str,
    selected_date: str | None = None,
) -> dict:
    recommendation = graph_result.get("recommendation")
    if recommendation and recommendation.selected:
        return graph_result

    demo_extraction = build_demo_extraction(email_text, graph_result.get("extraction"))
    if not demo_extraction.candidate_times:
        return graph_result

    demo_extraction = apply_selected_date_to_candidates(demo_extraction, selected_date)
    recommendation = recommend_time(demo_extraction, load_calendar(calendar_path), preferred_date=selected_date)
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
        "llm_model": graph_result.get("llm_model"),
        "error": graph_result.get("error"),
        "reference_date": graph_result.get("reference_date"),
        "reference_date_source": graph_result.get("reference_date_source"),
        "reference_date_error": graph_result.get("reference_date_error"),
        "reservation_target": reservation_target,
        "selected_date": payload.get("selected_date"),
        "place_search_query": graph_result.get("place_search_query"),
        "place_search_query_source": graph_result.get("place_search_query_source"),
        "place_search_query_reason": graph_result.get("place_search_query_reason"),
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


def align_place_with_reservation_target(
    graph_result: dict,
    reservation_target: str,
    runner_command: str,
    timezone: str,
    *,
    force: bool = False,
) -> dict:
    if not _is_external_reservation_target(reservation_target):
        return graph_result
    if not graph_result.get("extraction") or not graph_result.get("recommendation"):
        return graph_result
    if not graph_result["recommendation"].selected:
        return graph_result

    current_place = graph_result.get("place_recommendation")
    current_url = ""
    if current_place and current_place.selected:
        current_url = current_place.selected.source_url or ""
    if _same_url(current_url, reservation_target):
        return graph_result

    target_place = place_from_reservation_target(
        reservation_target,
        graph_result["extraction"].location_preference,
    )
    fallback_place = PlaceRecommendation(
        query=target_place.name,
        status="selected",
        selected=target_place,
        candidates=[target_place],
        summary="ShowUI가 열 예약 target과 동일한 장소 후보를 사용합니다.",
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


def place_from_reservation_target(
    reservation_target: str,
    location_preference: str | None = None,
) -> PlaceCandidate:
    parsed = urlparse(reservation_target)
    query_params = parse_qs(parsed.query)
    search_text = _first_query_value(query_params, "searchText")
    place_id = _naver_place_id(parsed.path)
    name = search_text or _clean_location_preference(location_preference) or "네이버 예약 페이지 선택 장소"
    if place_id and search_text:
        name = f"{search_text} 예약 장소"
    elif place_id:
        name = f"네이버 예약 장소 {place_id}"

    return PlaceCandidate(
        name=name,
        address=None,
        category="naver_booking",
        source_url=reservation_target,
        availability_hint="ShowUI가 실제 예약 target 페이지에서 확인",
        score=10.0,
    )


def _is_external_reservation_target(target: str) -> bool:
    parsed = urlparse(target)
    return parsed.scheme in {"http", "https"} and "map.naver.com" in parsed.netloc


def _same_url(left: str, right: str) -> bool:
    return left.rstrip("/") == right.rstrip("/") if left and right else False


def _first_query_value(query_params: dict[str, list[str]], name: str) -> str:
    values = query_params.get(name) or []
    return values[0].strip() if values and values[0].strip() else ""


def _naver_place_id(path: str) -> str:
    match = re.search(r"/place/(\d+)", path)
    return match.group(1) if match else ""


def _clean_location_preference(location_preference: str | None) -> str:
    if not location_preference:
        return ""
    cleaned = location_preference.strip().rstrip(".")
    return cleaned.removeprefix("장소는").strip()


def apply_email_location_override(
    graph_result: dict,
    email_text: str,
    place_provider: str,
    place_search_html: str,
    reservation_target: str,
    reservation_target_auto: bool,
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
    place_recommendation = recommend_place(
        extraction,
        graph_result["recommendation"],
        provider=provider,
        query_override=graph_result.get("place_search_query"),
    )
    target_for_reservation = _auto_target_from_place(place_recommendation, reservation_target) if reservation_target_auto else reservation_target
    executor = executor_from_name("showui", target=target_for_reservation, runner_command=runner_command)
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
    updated["reservation_target"] = target_for_reservation
    updated["reservation_result"] = reservation
    updated["reply_draft"] = reply
    return updated


def refresh_reservation_if_ready(
    graph_result: dict,
    reservation_target: str,
    reservation_target_auto: bool,
    runner_command: str,
    timezone: str,
) -> dict:
    extraction = graph_result.get("extraction")
    recommendation = graph_result.get("recommendation")
    place_recommendation = graph_result.get("place_recommendation")
    reservation = graph_result.get("reservation_result")
    current_status = reservation.status if reservation else None

    if current_status not in {None, "skipped"}:
        return graph_result
    if not extraction or not recommendation or not recommendation.selected:
        return graph_result
    if not place_recommendation or not place_recommendation.selected:
        return graph_result

    target_for_reservation = _auto_target_from_place(place_recommendation, reservation_target) if reservation_target_auto else reservation_target
    executor = executor_from_name("showui", target=target_for_reservation, runner_command=runner_command)
    refreshed_reservation = reserve_selected_place(
        extraction,
        recommendation,
        place_recommendation,
        executor=executor,
    )
    reply = generate_reply_draft(
        extraction,
        recommendation,
        place_recommendation=place_recommendation,
        reservation_result=refreshed_reservation,
        timezone=timezone,
    )
    updated = dict(graph_result)
    updated["reservation_target"] = target_for_reservation
    updated["reservation_result"] = refreshed_reservation
    updated["reply_draft"] = reply
    return updated


def _auto_target_from_place(place_recommendation: PlaceRecommendation | None, fallback: str) -> str:
    selected = place_recommendation.selected if place_recommendation else None
    selected_url = (selected.source_url or "").strip() if selected else ""
    return selected_url or fallback


def infer_naver_url(email_text: str) -> str:
    query = infer_place_query_from_text(email_text)
    if not query:
        return DEFAULT_NAVER_BOOKING_URL
    return "https://map.naver.com/p/search/" + quote(query)


def infer_location_query(email_text: str) -> str:
    return infer_place_query_from_text(email_text)


def infer_place_query_from_text(email_text: str) -> str:
    lowered = email_text.lower()
    area = _area_from_text(lowered)
    venue = _venue_from_text(lowered)
    if not area and not venue:
        return ""
    if not area:
        area = "주변"
    return f"{area} {venue} 예약".strip()


def _area_from_text(lowered: str) -> str:
    if "강남역" in lowered or "gangnam" in lowered:
        return "강남역"
    if "강남" in lowered:
        return "강남"
    if "홍대" in lowered or "hongdae" in lowered:
        return "홍대"
    if "판교" in lowered or "pangyo" in lowered:
        return "판교"
    if "숭실" in lowered or "soongsil" in lowered:
        return "숭실대"
    for area in ["서울대", "신촌", "잠실", "사당"]:
        if area in lowered:
            return area
    return ""


def _venue_from_text(lowered: str) -> str:
    if any(token in lowered for token in ["식당", "음식점", "레스토랑", "점심", "저녁", "식사", "밥", "회식", "restaurant", "lunch", "dinner"]):
        return "식당"
    if any(token in lowered for token in ["스터디룸", "스터디", "팀플", "과제", "공부", "study room"]):
        return "스터디룸"
    if any(token in lowered for token in ["회의실", "세미나실", "상담실", "면담", "상담", "인터뷰", "면접", "발표", "세미나"]):
        return "회의실"
    if any(token in lowered for token in ["카페", "커피", "coffee"]):
        return "카페"
    return "장소"


def build_pipeline_steps(graph_result: dict, payload: dict, reservation_target: str) -> list[dict]:
    extraction = graph_result.get("extraction")
    recommendation = graph_result.get("recommendation")
    place = graph_result.get("place_recommendation")
    reservation = graph_result.get("reservation_result")
    reply = graph_result.get("reply_draft")
    return [
        {"name": "Input", "status": "done", "detail": "HTML email text"},
        {"name": "Gmail", "status": "optional", "detail": "available when OAuth is configured"},
        {
            "name": "Reference date",
            "status": graph_result.get("reference_date_source") or "unknown",
            "detail": graph_result.get("reference_date") or graph_result.get("reference_date_error") or "",
        },
        {"name": "Extraction", "status": "done" if extraction else "failed", "detail": graph_result.get("provider") or ""},
        {
            "name": "Calendar",
            "status": "done" if recommendation else "skipped",
            "detail": payload.get("calendar_source") or "synthetic",
        },
        {
            "name": "Time optimization",
            "status": recommendation.status if recommendation else "skipped",
            "detail": _with_selected_date(recommendation.summary if recommendation else "", payload.get("selected_date")),
        },
        {
            "name": "Place retrieval",
            "status": place.status if place else "skipped",
            "detail": graph_result.get("place_search_query") or (place.selected.name if place and place.selected else ""),
        },
        {"name": "Reservation target", "status": "ready", "detail": reservation_target},
        {
            "name": "GUI reservation",
            "status": reservation.status if reservation else "skipped",
            "detail": reservation.message if reservation else "",
        },
        {"name": "Reply draft", "status": reply.status if reply else "skipped", "detail": "generated" if reply else ""},
    ]


def group_gmail_messages(messages: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for message in messages:
        key = _message_group_key(message)
        group = groups.setdefault(
            key,
            {
                "id": key,
                "title": _clean_subject(message.get("subject") or "(no subject)"),
                "message_ids": [],
                "subjects": [],
                "dates": [],
                "email_texts": [],
                "schedule_score": 0,
                "count": 0,
            },
        )
        group["message_ids"].append(message.get("message_id"))
        group["subjects"].append(message.get("subject") or "(no subject)")
        group["dates"].append(message.get("date") or "")
        group["email_texts"].append(message.get("email_text") or "")
        group["schedule_score"] = max(group["schedule_score"], message.get("schedule_score") or 0)
        group["count"] += 1

    grouped = []
    for group in groups.values():
        email_text = "\n\n--- same appointment thread ---\n\n".join(
            text for text in group.pop("email_texts") if text
        )
        grouped.append({**group, "email_text": email_text})
    grouped.sort(key=lambda item: (item["schedule_score"], item["count"]), reverse=True)
    return grouped


def split_appointment_contexts(email_text: str) -> list[dict]:
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*---+\s*\n", email_text) if chunk.strip()]
    if len(chunks) <= 1:
        return []
    return [
        {
            "id": f"manual_{index}",
            "title": _context_title(chunk, index),
            "email_text": chunk,
            "message_ids": [],
        }
        for index, chunk in enumerate(chunks, start=1)
    ]


def answer_chat(payload: dict) -> dict:
    question = (payload.get("question") or "").strip()
    context = payload.get("context") or {}
    llm_provider = payload.get("llm_provider")
    llm_model = payload.get("llm_model")
    if not question:
        return {"answer": "질문을 입력해주세요.", "source": "context"}

    fallback = _fallback_chat_answer(question, context)
    if _is_context_answer_question(question):
        return {"answer": fallback, "source": "context"}

    try:
        extractor = create_constraint_extractor(llm_provider, model=llm_model)
        answer = extractor.generate_text(_build_chat_prompt(question, context))
        return {"answer": answer, "source": getattr(extractor, "provider_name", "llm")}
    except Exception as exc:
        return {"answer": fallback, "source": "context_fallback", "llm_error": f"{type(exc).__name__}: {exc}"}


def _is_context_answer_question(question: str) -> bool:
    lowered = question.lower()
    return any(
        token in lowered
        for token in [
            "예약",
            "reservation",
            "booking",
            "실패",
            "failed",
            "성공",
            "success",
            "상태",
            "status",
            "장소",
            "place",
            "시간",
            "time",
            "날짜",
            "date",
            "메일",
            "mail",
            "email",
            "context",
            "컨텍스트",
            "누구",
            "참석",
            "선택",
            "추천",
        ]
    )


def _fallback_chat_answer(question: str, context: dict) -> str:
    appointments = context.get("appointments") or []
    if not appointments and context:
        appointments = [context]
    lowered = question.lower()

    lines = []
    for index, item in enumerate(appointments, start=1):
        extraction = item.get("extraction") or {}
        recommendation = item.get("recommendation") or {}
        selected = (recommendation.get("selected") or {}).get("candidate") or {}
        place = ((item.get("place_recommendation") or {}).get("selected") or {}).get("name")
        reservation = item.get("reservation_result") or {}
        title = item.get("appointment_title") or f"약속 {index}"
        if "메일" in question or "mail" in lowered or "email" in lowered or "context" in lowered or "컨텍스트" in lowered:
            summary = extraction.get("source_summary") or "선택된 메일 context가 있습니다."
            lines.append(f"{title}: {summary}")
        elif (
            "예약" in question
            or "reservation" in lowered
            or "booking" in lowered
            or "실패" in question
            or "failed" in lowered
            or "성공" in question
            or "success" in lowered
            or "상태" in question
            or "status" in lowered
        ):
            status = reservation.get("status") or "unknown"
            message = reservation.get("message") or ""
            reason = reservation.get("failure_reason") or ""
            if status == "confirmed":
                lines.append(f"{title}: 예약 시도는 성공으로 판정됐습니다. {message}".strip())
            elif status == "failed":
                detail = f" 실패 이유: {reason}." if reason else ""
                lines.append(f"{title}: 예약 시도는 실패했습니다.{detail} {message}".strip())
            elif status == "needs_manual_action":
                lines.append(f"{title}: 예약 페이지에서 로그인, 본인확인, 최종확인 같은 수동 단계가 필요합니다. {message}".strip())
            elif status == "skipped":
                lines.append(f"{title}: 예약을 실행하지 못했습니다. 시간 또는 장소 정보가 부족합니다. {message}".strip())
            else:
                lines.append(f"{title}: 아직 예약 결과가 없습니다.")
        elif "장소" in question or "place" in lowered:
            lines.append(f"{title}: 장소 후보는 {place or extraction.get('location_preference') or '아직 없습니다'}.")
        else:
            start = selected.get("start") or "선택된 시간이 없습니다"
            participants = ", ".join(extraction.get("participants") or [])
            lines.append(f"{title}: 추천 시간은 {start}, 참석자는 {participants or '미확인'}입니다.")

    return "\n".join(lines) if lines else "아직 agent 실행 결과가 없어 답변할 context가 부족합니다."


def _build_chat_prompt(question: str, context: dict) -> str:
    compact_context = json.dumps(_compact_chat_context(context), ensure_ascii=False, indent=2)
    return f"""You are a concise Korean chatbot for an e-mail scheduling agent demo.

Answer only from the provided context. If the context is insufficient, say what is missing.
Never claim a reservation succeeded when reservation_status is failed, skipped, needs_manual_action, or missing.
Keep the answer short and practical.

Context:
{compact_context}

User question:
{question}
"""


def _compact_chat_context(context: dict) -> dict:
    appointments = context.get("appointments")
    if appointments:
        return {
            "appointments": [_compact_single_result(item) for item in appointments],
        }
    if context.get("extraction") or context.get("recommendation"):
        return _compact_single_result(context)
    return {
        "email_text": context.get("email_text", "")[:2000],
        "appointment_contexts": [
            {
                "title": item.get("title"),
                "email_text": (item.get("email_text") or "")[:1200],
            }
            for item in context.get("appointment_contexts", [])[:5]
        ],
    }


def _compact_single_result(item: dict) -> dict:
    extraction = item.get("extraction") or {}
    recommendation = item.get("recommendation") or {}
    selected = recommendation.get("selected") or {}
    place = item.get("place_recommendation") or {}
    reservation = item.get("reservation_result") or {}
    return {
        "title": item.get("appointment_title") or item.get("appointment_id"),
        "participants": extraction.get("participants"),
        "location_preference": extraction.get("location_preference"),
        "selected_time": (selected.get("candidate") or {}).get("start"),
        "schedule_summary": recommendation.get("summary"),
        "place": (place.get("selected") or {}).get("name"),
        "reservation_status": reservation.get("status"),
        "reservation_message": reservation.get("message"),
        "reply_draft": (item.get("reply_draft") or {}).get("body"),
    }


def _message_group_key(message: dict) -> str:
    thread_id = message.get("thread_id")
    if thread_id:
        return f"thread:{thread_id}"
    return "subject:" + _clean_subject(message.get("subject") or "(no subject)").lower()


def _clean_subject(subject: str) -> str:
    cleaned = re.sub(r"^\s*(re|fw|fwd)\s*:\s*", "", subject, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*(답장|전달)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip() or "(no subject)"


def _context_title(text: str, index: int) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("subject:"):
            return line.split(":", 1)[1].strip() or f"Appointment {index}"
        if line:
            return line[:48]
    return f"Appointment {index}"


def _with_selected_date(summary: str, selected_date: str | None) -> str:
    if not selected_date:
        return summary
    return f"{summary} 선택 날짜: {selected_date}".strip()


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
    parser.add_argument("--reference-date", default="auto")
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--llm-provider", default=None)
    parser.add_argument("--llm-model", default=None, help="Optional Ollama model tag, for example qwen3:4b.")
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
