from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict

from .extractor import extract_constraints, parse_email_thread
from .json_utils import parse_json_object
from .llm_extractor import create_constraint_extractor
from .place_retriever import provider_from_name, recommend_place
from .prompting import build_place_search_prompt
from .reservation_executor import executor_from_name, reserve_selected_place
from .reply_generator import generate_reply_draft
from .schema import (
    EmailThread,
    ExtractionResult,
    PlaceRecommendation,
    ReplyDraft,
    ReservationResult,
    ScheduleRecommendation,
)
from .scheduling import load_calendar, recommend_time
from .time_normalizer import normalize_extraction_times


class ExtractionGraphState(TypedDict, total=False):
    email_text: str
    reference_date: str | None
    reference_date_source: str | None
    reference_date_error: str | None
    timezone: str
    llm_provider: str | None
    llm_model: str | None
    selected_date: str | None
    calendar_path: str | None
    place_provider: str | None
    place_search_html: str | None
    place_search_query: str | None
    place_search_query_source: str | None
    place_search_query_reason: str | None
    reservation_provider: str | None
    reservation_html: str | None
    reservation_target: str | None
    reservation_target_auto: bool
    showui_runner_command: str | None
    thread: EmailThread
    extraction: ExtractionResult
    recommendation: ScheduleRecommendation
    place_recommendation: PlaceRecommendation
    reservation_result: ReservationResult
    reply_draft: ReplyDraft
    provider: str
    error: str


def build_extraction_graph():
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise ImportError("langgraph is not installed. Run: pip install langgraph") from exc

    graph = StateGraph(ExtractionGraphState)

    graph.add_node("parse_email", parse_email_node)
    graph.add_node("infer_reference_date", infer_reference_date_node)
    graph.add_node("llm_extract", llm_extract_node)
    graph.add_node("rule_fallback", rule_fallback_node)
    graph.add_node("normalize_times", normalize_times_node)
    graph.add_node("schedule", schedule_node)
    graph.add_node("retrieve_places", retrieve_places_node)
    graph.add_node("reserve_place", reserve_place_node)
    graph.add_node("generate_reply", generate_reply_node)

    graph.add_edge(START, "parse_email")
    graph.add_edge("parse_email", "infer_reference_date")
    graph.add_edge("infer_reference_date", "llm_extract")
    graph.add_conditional_edges(
        "llm_extract",
        should_fallback,
        {
            "fallback": "rule_fallback",
            "done": "normalize_times",
        },
    )
    graph.add_edge("rule_fallback", "normalize_times")
    graph.add_edge("normalize_times", "schedule")
    graph.add_edge("schedule", "retrieve_places")
    graph.add_edge("retrieve_places", "reserve_place")
    graph.add_edge("reserve_place", "generate_reply")
    graph.add_edge("generate_reply", END)

    return graph.compile()


def parse_email_node(state: ExtractionGraphState) -> ExtractionGraphState:
    thread = parse_email_thread(state["email_text"])
    return {**state, "thread": thread}


def infer_reference_date_node(state: ExtractionGraphState) -> ExtractionGraphState:
    reference_date = (state.get("reference_date") or "").strip()
    if reference_date and reference_date.lower() != "auto":
        return {**state, "reference_date": reference_date, "reference_date_source": "user"}

    try:
        extractor = create_graph_extractor(state)
        inferred = extractor.infer_reference_date(
            state["thread"],
            timezone=state.get("timezone", "Asia/Seoul"),
        )
        if inferred:
            return {
                **state,
                "reference_date": inferred,
                "reference_date_source": f"{extractor.provider_name}_inferred",
                "llm_model": extractor.model_name,
            }
        return {**state, "reference_date": None, "reference_date_source": "not_found", "llm_model": extractor.model_name}
    except Exception as exc:
        return {**state, "reference_date": None, "reference_date_source": "failed", "reference_date_error": str(exc)}


def llm_extract_node(state: ExtractionGraphState) -> ExtractionGraphState:
    provider = state.get("llm_provider")
    try:
        extractor = create_graph_extractor(state)
        result = extractor.extract(
            state["thread"],
            reference_date=state.get("reference_date"),
            timezone=state.get("timezone", "Asia/Seoul"),
        )
        return {**state, "extraction": result, "provider": extractor.provider_name, "llm_model": extractor.model_name}
    except Exception as exc:
        failed_provider = (provider or "exaone").lower()
        return {**state, "error": str(exc), "provider": f"{failed_provider}_failed"}


def rule_fallback_node(state: ExtractionGraphState) -> ExtractionGraphState:
    result = extract_constraints(state["thread"])
    return {**state, "extraction": result, "provider": "rule_fallback"}


def normalize_times_node(state: ExtractionGraphState) -> ExtractionGraphState:
    extraction = state.get("extraction")
    if not extraction:
        return state

    try:
        normalized = normalize_extraction_times(
            extraction,
            reference_date=state.get("reference_date"),
            timezone=state.get("timezone", "Asia/Seoul"),
        )
        return {**state, "extraction": normalized}
    except Exception as exc:
        previous_error = state.get("error")
        error = f"{previous_error}; time normalization failed: {exc}" if previous_error else f"time normalization failed: {exc}"
        return {**state, "error": error}


def schedule_node(state: ExtractionGraphState) -> ExtractionGraphState:
    calendar_path = state.get("calendar_path")
    extraction = state.get("extraction")
    if not calendar_path or not extraction:
        return state

    try:
        adjusted_extraction = apply_selected_date_to_candidates(extraction, state.get("selected_date"))
        recommendation = recommend_time(
            adjusted_extraction,
            load_calendar(calendar_path),
            preferred_date=state.get("selected_date"),
        )
        return {**state, "extraction": adjusted_extraction, "recommendation": recommendation}
    except Exception as exc:
        previous_error = state.get("error")
        error = f"{previous_error}; scheduling failed: {exc}" if previous_error else f"scheduling failed: {exc}"
        return {**state, "error": error}


def apply_selected_date_to_candidates(
    extraction: ExtractionResult,
    selected_date: str | None,
) -> ExtractionResult:
    if not selected_date:
        return extraction

    updated_candidates = []
    for item in extraction.candidate_times:
        if not item.normalized_start or not item.normalized_end:
            updated_candidates.append(item)
            continue
        start = datetime.fromisoformat(item.normalized_start)
        end = datetime.fromisoformat(item.normalized_end)
        duration = end - start
        selected_start = datetime.fromisoformat(f"{selected_date}T{start.time().isoformat()}")
        if start.tzinfo:
            selected_start = selected_start.replace(tzinfo=start.tzinfo)
        selected_end = selected_start + duration
        updated_candidates.append(
            item.model_copy(
                update={
                    "normalized_start": selected_start.isoformat(),
                    "normalized_end": selected_end.isoformat(),
                }
            )
        )

    return extraction.model_copy(update={"candidate_times": updated_candidates})


def retrieve_places_node(state: ExtractionGraphState) -> ExtractionGraphState:
    extraction = state.get("extraction")
    if not extraction:
        return state

    try:
        provider_name = state.get("place_provider") or ("html" if state.get("place_search_html") else "mock")
        provider = provider_from_name(provider_name, html_path=state.get("place_search_html"))
        place_query, query_source, query_reason = generate_place_search_query(state)
        if place_query is None:
            return {
                **state,
                "place_search_query": None,
                "place_search_query_source": query_source,
                "place_search_query_reason": query_reason,
                "place_recommendation": PlaceRecommendation(
                    query="",
                    candidates=[],
                    selected=None,
                    status="no_query",
                    summary=query_reason,
                ),
            }
        place_recommendation = recommend_place(
            extraction,
            state.get("recommendation"),
            provider=provider,
            query_override=place_query,
        )
        return {
            **state,
            "place_search_query": place_query,
            "place_search_query_source": query_source,
            "place_search_query_reason": query_reason,
            "place_recommendation": place_recommendation,
        }
    except Exception as exc:
        previous_error = state.get("error")
        error = f"{previous_error}; place retrieval failed: {exc}" if previous_error else f"place retrieval failed: {exc}"
        return {**state, "error": error}


def generate_place_search_query(state: ExtractionGraphState) -> tuple[str | None, str, str]:
    extraction = state.get("extraction")
    if not extraction:
        return None, "missing_extraction", "추출 결과가 없습니다."

    recommendation = state.get("recommendation")
    try:
        extractor = create_graph_extractor(state)
        prompt = build_place_search_prompt(
            state["thread"],
            extraction.model_dump_json(),
            recommendation.model_dump_json() if recommendation else None,
        )
        data = parse_json_object(extractor.generate_text(prompt))
        if data.get("needs_place_search") is False:
            return None, getattr(extractor, "provider_name", "llm"), str(data.get("reason") or "물리적 장소 검색이 필요 없습니다.")
        query = str(data.get("search_query") or "").strip()
        if query:
            return query, getattr(extractor, "provider_name", "llm"), str(data.get("reason") or "")
    except Exception:
        pass

    fallback = infer_place_search_query(extraction)
    if fallback:
        return fallback, "heuristic_fallback", "LLM 장소 검색어 생성 실패 또는 빈 응답으로 heuristic query를 사용했습니다."
    return None, "heuristic_fallback", "장소 검색이 필요하거나 가능한지 판단할 정보가 부족합니다."


def infer_place_search_query(extraction: ExtractionResult) -> str:
    preference = (extraction.location_preference or "").lower()
    text = " ".join(
        item
        for item in [
            extraction.location_preference or "",
            extraction.source_summary or "",
            " ".join(time.expression for time in extraction.candidate_times),
        ]
        if item
    ).lower()
    location = _location_area_from_text(text) or _clean_location_text(extraction.location_preference)

    if any(token in text for token in ["zoom", "온라인", "화상", "전화", "콜", "통화", "google meet", "teams"]):
        return ""
    if any(token in preference for token in ["카페", "coffee", "커피"]):
        return f"{location} 카페 예약".strip()
    if any(token in preference for token in ["식당", "restaurant", "점심", "저녁", "식사"]):
        return f"{location} 식당 예약".strip()
    if "스터디룸" in preference:
        return f"{location} 스터디룸 예약".strip()
    if any(token in preference for token in ["회의실", "세미나실", "상담실"]):
        return f"{location} 회의실 예약".strip()
    if any(token in text for token in ["점심", "저녁", "식사", "밥", "회식", "lunch", "dinner", "restaurant"]):
        return f"{location} 식당 예약".strip()
    if any(token in text for token in ["팀플", "스터디", "과제", "공부", "study"]):
        return f"{location} 스터디룸 예약".strip()
    if any(token in text for token in ["면담", "상담", "인터뷰", "면접", "발표", "세미나", "회의", "미팅", "meeting"]):
        return f"{location} 조용한 회의실 예약".strip()
    if any(token in text for token in ["카페", "coffee", "커피"]):
        return f"{location} 카페 예약".strip()
    return _clean_location_text(extraction.location_preference)


def _location_area_from_text(text: str) -> str:
    for area in ["숭실대", "강남역", "강남", "홍대", "판교", "서울대", "신촌", "잠실", "사당"]:
        if area.lower() in text:
            return area
    return ""


def _clean_location_text(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip().rstrip(".")
    return cleaned.removeprefix("장소는").strip()


def reserve_place_node(state: ExtractionGraphState) -> ExtractionGraphState:
    extraction = state.get("extraction")
    if not extraction:
        return state

    try:
        provider_name = state.get("reservation_provider") or ("html" if state.get("reservation_html") else "mock")
        reservation_target = resolve_reservation_target(state)
        executor = executor_from_name(
            provider_name,
            html_path=state.get("reservation_html"),
            target=reservation_target,
            runner_command=state.get("showui_runner_command"),
        )
        reservation_result = reserve_selected_place(
            extraction,
            state.get("recommendation"),
            state.get("place_recommendation"),
            executor=executor,
        )
        return {**state, "reservation_target": reservation_target, "reservation_result": reservation_result}
    except Exception as exc:
        previous_error = state.get("error")
        error = f"{previous_error}; reservation failed: {exc}" if previous_error else f"reservation failed: {exc}"
        return {**state, "error": error}


def resolve_reservation_target(state: ExtractionGraphState) -> str | None:
    target = state.get("reservation_target")
    place_recommendation = state.get("place_recommendation")
    selected_url = None
    if place_recommendation and place_recommendation.selected:
        selected_url = place_recommendation.selected.source_url

    if state.get("reservation_target_auto") and selected_url:
        return selected_url
    return target or selected_url


def generate_reply_node(state: ExtractionGraphState) -> ExtractionGraphState:
    extraction = state.get("extraction")
    if not extraction:
        return state

    try:
        draft = generate_reply_draft(
            extraction,
            state.get("recommendation"),
            place_recommendation=state.get("place_recommendation"),
            reservation_result=state.get("reservation_result"),
            timezone=state.get("timezone", "Asia/Seoul"),
        )
        return {**state, "reply_draft": draft}
    except Exception as exc:
        previous_error = state.get("error")
        error = f"{previous_error}; reply generation failed: {exc}" if previous_error else f"reply generation failed: {exc}"
        return {**state, "error": error}


def should_fallback(state: ExtractionGraphState) -> Literal["fallback", "done"]:
    if state.get("error") or not state.get("extraction"):
        return "fallback"
    return "done"


def create_graph_extractor(state: ExtractionGraphState):
    return create_constraint_extractor(
        state.get("llm_provider"),
        model=state.get("llm_model"),
    )
