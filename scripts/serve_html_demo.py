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
from src.email_agent.json_utils import parse_json_object
from src.email_agent.llm_extractor import create_constraint_extractor
from src.email_agent.place_retriever import provider_from_name, recommend_place
from src.email_agent.reservation_executor import executor_from_name, reserve_selected_place
from src.email_agent.schema import ExtractionResult, Intent, PlaceCandidate, PlaceRecommendation, ScheduleRecommendation


DEFAULT_NAVER_BOOKING_URL = (
    "https://map.naver.com/p/search/%EC%98%88%EC%95%BD%20%EA%B0%80%EB%8A%A5%20%EC%9E%A5%EC%86%8C"
)
CHAT_TOOL_RETRY_LIMIT = 3
CHAT_ALLOWED_ACTIONS = {"answer", "search_place", "reserve_place"}
CHAT_QUERY_NOISE_PATTERNS = [
    r"예약\s*가능한?",
    r"가능한?",
    r"식사\s*할\s*만한",
    r"밥\s*먹을\s*만한",
    r"먹을\s*만한",
    r"회의\s*후",
    r"끝나고",
    r"근처",
    r"주변",
]
CHAT_QUERY_NOISE_TOKENS = [
    "저녁",
    "점심",
    "아침",
    "오전",
    "오후",
    "밤",
    "낮",
    "새벽",
    "식사",
    "밥",
    "검색",
    "찾기",
    "추천",
    "장소",
    "예약",
    "가능",
]


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
                self._send_json(answer_chat(payload, self.server))
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
    naver_url = manual_naver_url or DEFAULT_NAVER_BOOKING_URL
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
        graph_result, reservation_target = sync_reservation_target_from_place(
            graph_result,
            reservation_target,
            reservation_target_auto,
            place_provider,
        )
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


def serialize_graph_result(graph_result: dict, payload: dict, reservation_target: str) -> dict:
    return {
        "provider": graph_result.get("provider"),
        "llm_model": graph_result.get("llm_model"),
        "error": graph_result.get("error"),
        "reference_date": graph_result.get("reference_date"),
        "reference_date_source": graph_result.get("reference_date_source"),
        "reference_date_error": graph_result.get("reference_date_error"),
        "reservation_target": reservation_target,
        "reservation_target_source": graph_result.get("reservation_target_source"),
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


def sync_reservation_target_from_place(
    graph_result: dict,
    reservation_target: str,
    reservation_target_auto: bool,
    place_provider: str,
) -> tuple[dict, str]:
    if not reservation_target_auto:
        return graph_result, graph_result.get("reservation_target") or reservation_target

    place_recommendation = graph_result.get("place_recommendation")
    selected = place_recommendation.selected if place_recommendation and place_recommendation.selected else None
    selected_url = (selected.source_url or "").strip() if selected else ""
    if not selected_url:
        return graph_result, graph_result.get("reservation_target") or reservation_target

    updated = dict(graph_result)
    updated["reservation_target"] = selected_url
    updated["reservation_target_source"] = f"{place_provider or 'place'}_selected_place"
    return updated, selected_url


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
        {
            "name": "Reservation target",
            "status": graph_result.get("reservation_target_source") or "ready",
            "detail": reservation_target,
        },
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


def answer_chat(payload: dict, server: DemoServer | None = None) -> dict:
    question = (payload.get("question") or "").strip()
    context = payload.get("context") or {}
    llm_model = payload.get("llm_model")
    if not question:
        return {"answer": "", "source": "exaone", "error": "질문을 입력해주세요."}

    try:
        model = llm_model if llm_model and "EXAONE" in llm_model.upper() else None
        extractor = create_constraint_extractor("exaone", model=model)
        action = _plan_chat_action(extractor, question, context)
        tool_result = _execute_chat_action(action, context, payload)
        answer = _clean_chat_final_answer(extractor.generate_text(_build_chat_prompt(question, context, action, tool_result)))
        return {
            "answer": answer,
            "source": getattr(extractor, "provider_name", "llm"),
            "llm_model": getattr(extractor, "model_name", llm_model),
            "tool_calls": [action] if action["action"] != "answer" else [],
            "tool_result": tool_result,
        }
    except Exception as exc:
        return {"answer": "", "source": "exaone", "error": f"{type(exc).__name__}: {exc}"}


def _plan_chat_action(extractor, question: str, context: dict) -> dict:
    errors: list[str] = []
    for attempt in range(1, CHAT_TOOL_RETRY_LIMIT + 1):
        prompt = _build_chat_action_prompt(question, context, errors)
        try:
            data = parse_json_object(extractor.generate_text(prompt))
            return _validate_chat_action(data)
        except Exception as exc:
            errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
    raise TimeoutError(f"chat tool planning failed after {CHAT_TOOL_RETRY_LIMIT} EXAONE attempts. {' | '.join(errors)}")


def _validate_chat_action(data: dict) -> dict:
    action = str(data.get("action") or "").strip()
    if action not in CHAT_ALLOWED_ACTIONS:
        raise ValueError(f"unsupported chat action: {action}")

    reason = str(data.get("reason") or "").strip()
    if action == "answer":
        return {"action": "answer", "query": None, "reason": reason}

    if action == "reserve_place":
        return {"action": "reserve_place", "query": None, "reason": reason}

    query = _clean_chat_tool_query(data.get("query"))
    if not query:
        raise ValueError("search_place requires a non-empty Korean query.")
    return {"action": "search_place", "query": query, "reason": reason}


def _clean_chat_tool_query(value) -> str:
    query = str(value or "").strip()
    if not query or len(query) > 60:
        return ""
    if any(char in query for char in ["\n", "\r", "\t", "{", "}", "[", "]"]):
        return ""
    lowered = query.lower()
    if any(token in lowered for token in ["meeting room", "search", "booking"]):
        return ""
    for pattern in CHAT_QUERY_NOISE_PATTERNS:
        query = re.sub(pattern, " ", query)
    for token in CHAT_QUERY_NOISE_TOKENS:
        query = query.replace(token, " ")
    return " ".join(query.split())


def _execute_chat_action(action: dict, context: dict, payload: dict) -> dict | None:
    if action["action"] == "answer":
        return None
    if action["action"] == "search_place":
        return _search_place_tool(action["query"])
    if action["action"] == "reserve_place":
        return _reserve_chat_selected_place(context, payload)
    raise ValueError(f"unsupported chat action: {action['action']}")


def _search_place_tool(query: str) -> dict:
    provider = provider_from_name("kakao")
    extraction = ExtractionResult(
        intent=Intent.SCHEDULE_MEETING,
        location_preference=query,
        source_summary="chatbot requested an additional place search",
    )
    recommendation = recommend_place(extraction, provider=provider, query_override=query)
    return {
        "tool": "search_place",
        "query": recommendation.query,
        "status": recommendation.status,
        "summary": recommendation.summary,
        "selected": recommendation.selected.model_dump() if recommendation.selected else None,
        "candidates": [candidate.model_dump() for candidate in recommendation.candidates[:5]],
    }


def _reserve_chat_selected_place(context: dict, payload: dict) -> dict:
    extraction = _chat_extraction(context)
    recommendation = _chat_schedule_recommendation(context)
    place_recommendation, place_source = _chat_place_recommendation(context)
    selected_place = place_recommendation.selected if place_recommendation else None
    reservation_target = (selected_place.source_url or "").strip() if selected_place else ""
    if not reservation_target:
        reservation_target = (payload.get("naver_url") or "").strip() or DEFAULT_NAVER_BOOKING_URL

    executor_name = payload.get("executor") or "naver-visible"
    target, runner_command = _chat_reservation_runner_config(executor_name, reservation_target)
    executor = executor_from_name("showui", target=target, runner_command=runner_command)
    result = reserve_selected_place(extraction, recommendation, place_recommendation, executor=executor)
    return {
        "tool": "reserve_place",
        "status": result.status,
        "reservation_target": target,
        "reservation_target_source": place_source,
        "selected_place": selected_place.model_dump() if selected_place else None,
        "reservation_result": result.model_dump(),
    }


def _chat_extraction(context: dict) -> ExtractionResult:
    data = context.get("extraction") or {}
    if data:
        return ExtractionResult.model_validate(data)
    return ExtractionResult(
        intent=Intent.SCHEDULE_MEETING,
        participants=[],
        source_summary="chatbot reservation request",
    )


def _chat_schedule_recommendation(context: dict) -> ScheduleRecommendation | None:
    data = context.get("recommendation") or {}
    if not data:
        return None
    return ScheduleRecommendation.model_validate(data)


def _chat_place_recommendation(context: dict) -> tuple[PlaceRecommendation | None, str]:
    chat_tool = context.get("chat_tool_result") or context.get("last_chat_tool_result") or {}
    if chat_tool.get("tool") == "search_place" and chat_tool.get("selected"):
        selected = PlaceCandidate.model_validate(chat_tool["selected"])
        candidates = [
            PlaceCandidate.model_validate(candidate)
            for candidate in chat_tool.get("candidates", [])
            if isinstance(candidate, dict)
        ]
        if selected.name not in {candidate.name for candidate in candidates}:
            candidates.insert(0, selected)
        return (
            PlaceRecommendation(
                query=chat_tool.get("query") or selected.name,
                candidates=candidates,
                selected=selected,
                status="selected",
                summary=chat_tool.get("summary") or "챗봇이 선택한 장소입니다.",
            ),
            "chat_kakao_selected_place",
        )

    place = context.get("place_recommendation") or {}
    if place.get("selected"):
        return PlaceRecommendation.model_validate(place), "agent_selected_place"
    return None, "missing_selected_place"


def _chat_reservation_runner_config(executor_name: str, reservation_target: str) -> tuple[str, str]:
    if executor_name in {"mock-visible", "mock-headless"}:
        target = (PROJECT_ROOT / "demo" / "recording_reservation_site.html").resolve().as_uri()
        headless_flag = " --headless" if executor_name == "mock-headless" else ""
        return target, f"{sys.executable} scripts/showui_reservation_runner.py{headless_flag} --dom-fallback"

    headless_flag = " --headless" if executor_name == "naver-headless" else ""
    target = reservation_target or DEFAULT_NAVER_BOOKING_URL
    return target, f"{sys.executable} scripts/showui_reservation_runner.py{headless_flag} --showui-source local"


def _build_chat_action_prompt(question: str, context: dict, errors: list[str] | None = None) -> str:
    compact_context = json.dumps(_compact_chat_context(context), ensure_ascii=False, indent=2)
    error_text = "\n".join(errors or [])
    return f"""You are a tool router for an e-mail scheduling agent chatbot.

Decide whether the user only needs an answer from context or whether the chatbot must call a tool.

Allowed actions:
- answer: answer from the existing context. Use this for status, selected time, selected place, reservation result, or reply draft questions.
- search_place: call Kakao Local place search. Use this when the user asks to find another place, a meal/restaurant/cafe/study room near the meeting location, or an after-meeting place.
- reserve_place: call ShowUI reservation for the currently selected place. Use this when the user asks to reserve/book the place that was just selected or searched.

Rules for search_place:
- query must be natural Korean only.
- Do not include English words such as "meeting room", "search", or "booking".
- Do not include meta words such as "검색" or "찾기".
- Do not include meal-time or intent words such as "저녁", "점심", "아침", "식사", "예약", "가능", "추천", or "장소".
- Use only the area plus the venue category, such as "경희대 식당", "경희대 카페", "명지대 회의실", "홍대 식당".
- Infer the area from context when possible. If context has location_preference "경희대" and the user asks for dinner, use "경희대 식당".
- If the user asks for dinner near "인하대학교", the query must be "인하대학교 식당", not "인하대학교 저녁 식당".

Rules for reserve_place:
- Use reserve_place when the user says "예약해줘", "예약도 진행해줘", "그 식당 예약해줘", or asks to book the currently selected/searched place.
- If context has chat_tool = "search_place" and chat_selected_place exists, reserve that chat-selected place.
- Do not switch back to an older agent-selected place when the user asks to reserve the place found in the chat.

Return JSON only:
{{"action": "answer or search_place or reserve_place", "query": "Korean query or null", "reason": "short Korean reason"}}

Previous invalid attempts:
{error_text or "none"}

Context:
{compact_context}

User question:
{question}
"""


def _build_chat_prompt(question: str, context: dict, action: dict | None = None, tool_result: dict | None = None) -> str:
    compact_context = json.dumps(_compact_chat_context(context), ensure_ascii=False, indent=2)
    action_text = json.dumps(action or {"action": "answer"}, ensure_ascii=False, indent=2)
    tool_text = json.dumps(tool_result or {}, ensure_ascii=False, indent=2)
    return f"""You are a concise Korean chatbot for an e-mail scheduling agent demo.

Answer only from the provided context. If the context is insufficient, say what is missing.
Never claim a reservation succeeded when reservation_status is failed, skipped, needs_manual_action, or missing.
If a tool result is provided, use it directly. If search_place found candidates, summarize the selected place and 2 alternatives if available.
Do not claim that a newly searched place was reserved. Say it was only searched unless a reservation_result says confirmed.
If reserve_place was called, report the exact reservation_result.status from the tool result.
For reserve_place status failed, skipped, or needs_manual_action, clearly say the reservation was not completed and the user must complete it manually from the opened target page.
Return plain Korean prose only.
Do not output JSON, Markdown, code fences, or key-value blocks.
Never wrap the answer in ```json or any other fenced block.
Keep the answer short and practical.

Context:
{compact_context}

Planned action:
{action_text}

Tool result:
{tool_text}

User question:
{question}
"""


def _clean_chat_final_answer(answer: str) -> str:
    text = str(answer or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = parse_json_object(text)
    except ValueError:
        return text

    for key in ["response", "answer", "message"]:
        if isinstance(data.get(key), str) and data[key].strip():
            return data[key].strip()

    selected_place = data.get("selected_place")
    alternatives = data.get("alternatives")
    if selected_place:
        lines = [f"{selected_place}을(를) 우선 후보로 찾았습니다."]
        if isinstance(alternatives, list) and alternatives:
            alt_names = [
                item.get("name") if isinstance(item, dict) else str(item)
                for item in alternatives[:2]
            ]
            alt_names = [name for name in alt_names if name]
            if alt_names:
                lines.append("대안으로는 " + ", ".join(alt_names) + "도 있습니다.")
        return " ".join(lines)

    return text


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
    chat_tool = item.get("chat_tool_result") or {}
    chat_selected = chat_tool.get("selected") or {}
    return {
        "title": item.get("appointment_title") or item.get("appointment_id"),
        "participants": extraction.get("participants"),
        "location_preference": extraction.get("location_preference"),
        "selected_time": (selected.get("candidate") or {}).get("start"),
        "schedule_summary": recommendation.get("summary"),
        "place": (place.get("selected") or {}).get("name"),
        "chat_tool": chat_tool.get("tool"),
        "chat_selected_place": chat_selected.get("name"),
        "chat_selected_place_url": chat_selected.get("source_url"),
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
    parser.add_argument("--llm-model", default=None, help="Optional model id, for example LGAI-EXAONE/EXAONE-4.0-1.2B.")
    parser.add_argument("--calendar", default="data/calendars/synthetic_calendar_001.json")
    parser.add_argument("--place-provider", choices=["mock", "html", "kakao", "demo"], default="kakao")
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
