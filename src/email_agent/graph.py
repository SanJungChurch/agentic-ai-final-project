from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, TypedDict

from .extractor import parse_email_thread
from .json_utils import parse_json_object
from .llm_extractor import create_constraint_extractor
from .place_retriever import provider_from_name, recommend_place
from .prompting import build_place_search_prompt
from .reservation_executor import executor_from_name, reserve_selected_place
from .reply_generator import generate_reply_draft_with_llm
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


EXAONE_FALLBACK_ATTEMPTS = 3


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
    reservation_target_source: str | None
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
    primary_llm_error: str


def build_extraction_graph():
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise ImportError("langgraph is not installed. Run: pip install langgraph") from exc

    graph = StateGraph(ExtractionGraphState)

    graph.add_node("parse_email", parse_email_node)
    graph.add_node("infer_reference_date", infer_reference_date_node)
    graph.add_node("llm_extract", llm_extract_node)
    graph.add_node("normalize_times", normalize_times_node)
    graph.add_node("schedule", schedule_node)
    graph.add_node("retrieve_places", retrieve_places_node)
    graph.add_node("reserve_place", reserve_place_node)
    graph.add_node("generate_reply", generate_reply_node)

    graph.add_edge(START, "parse_email")
    graph.add_edge("parse_email", "infer_reference_date")
    graph.add_edge("infer_reference_date", "llm_extract")
    graph.add_edge("llm_extract", "normalize_times")
    graph.add_edge("normalize_times", "schedule")
    graph.add_edge("schedule", "retrieve_places")
    graph.add_edge("retrieve_places", "reserve_place")
    graph.add_edge("reserve_place", "generate_reply")
    graph.add_edge("generate_reply", END)

    return graph.compile()


def parse_email_node(state: ExtractionGraphState) -> ExtractionGraphState:
    thread = parse_email_thread(state["email_text"])
    return {**state, "thread": thread}


def should_try_primary_provider(state: ExtractionGraphState) -> bool:
    provider = (state.get("llm_provider") or "").strip()
    if not provider:
        return False
    selected = provider.lower()
    if selected in {"exaone", "lgai-exaone", "transformers", "hf", "huggingface"}:
        return False
    if selected.startswith("exaone:"):
        return False
    if provider.startswith("LGAI-EXAONE/") or provider.startswith("lgai-exaone/"):
        return False
    return True


def create_exaone_extractor(state: ExtractionGraphState):
    model = state.get("llm_model")
    if model and "EXAONE" in model.upper():
        return create_constraint_extractor("exaone", model=model)
    return create_constraint_extractor("exaone")


def run_with_exaone_fallback(
    state: ExtractionGraphState,
    description: str,
    operation: Callable[[Any], Any],
    *,
    allow_none: bool = False,
    attempts: int = EXAONE_FALLBACK_ATTEMPTS,
) -> tuple[Any, Any]:
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            extractor = create_exaone_extractor(state)
            result = operation(extractor)
            if result is None and not allow_none:
                raise ValueError("EXAONE returned no usable result.")
            return result, extractor
        except Exception as exc:
            errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
    raise TimeoutError(f"{description} failed after {attempts} EXAONE attempts. {' | '.join(errors)}")


def infer_reference_date_node(state: ExtractionGraphState) -> ExtractionGraphState:
    reference_date = (state.get("reference_date") or "").strip()
    if reference_date and reference_date.lower() != "auto":
        return {**state, "reference_date": reference_date, "reference_date_source": "user"}

    try:
        inferred, extractor = run_with_exaone_fallback(
            state,
            "reference date inference",
            lambda candidate: candidate.infer_reference_date(
                state["thread"],
                timezone=state.get("timezone", "Asia/Seoul"),
            ),
            allow_none=False,
        )
        return {
            **state,
            "reference_date": inferred,
            "reference_date_source": f"{extractor.provider_name}_inferred",
            "llm_model": extractor.model_name,
        }
    except Exception as exc:
        return {**state, "reference_date": None, "reference_date_source": "failed", "reference_date_error": str(exc)}


def llm_extract_node(state: ExtractionGraphState) -> ExtractionGraphState:
    provider = state.get("llm_provider")
    primary_error = None
    try:
        if should_try_primary_provider(state):
            extractor = create_graph_extractor(state)
            result = extractor.extract(
                state["thread"],
                reference_date=state.get("reference_date"),
                timezone=state.get("timezone", "Asia/Seoul"),
            )
            return {**state, "extraction": result, "provider": extractor.provider_name, "llm_model": extractor.model_name}
    except Exception as exc:
        primary_error = exc

    try:
        result, extractor = run_with_exaone_fallback(
            state,
            "constraint extraction",
            lambda candidate: candidate.extract(
                state["thread"],
                reference_date=state.get("reference_date"),
                timezone=state.get("timezone", "Asia/Seoul"),
            ),
            allow_none=False,
        )
        updated = {**state, "extraction": result, "provider": extractor.provider_name, "llm_model": extractor.model_name}
        if primary_error:
            updated["primary_llm_error"] = f"{type(primary_error).__name__}: {primary_error}"
        return updated
    except TimeoutError as exc:
        failed_provider = (provider or "exaone").lower()
        error = f"TimeoutError: {exc}"
        if primary_error:
            error += f"; primary_error={type(primary_error).__name__}: {primary_error}"
        return {**state, "error": error, "provider": f"{failed_provider}_timeout"}


def normalize_times_node(state: ExtractionGraphState) -> ExtractionGraphState:
    if state.get("error"):
        return state
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
    if state.get("error"):
        return state
    calendar_path = state.get("calendar_path")
    extraction = state.get("extraction")
    if not calendar_path or not extraction:
        return state

    try:
        adjusted_extraction = apply_selected_date_to_candidates(extraction, state.get("selected_date"))
        extractor = create_exaone_extractor(state)
        recommendation = recommend_time(
            adjusted_extraction,
            load_calendar(calendar_path),
            preferred_date=state.get("selected_date"),
            email_thread=state.get("thread"),
            llm_text_generator=extractor.generate_text,
            llm_required=True,
        )
        return {
            **state,
            "extraction": adjusted_extraction,
            "recommendation": recommendation,
            "llm_model": extractor.model_name,
        }
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
    if state.get("error"):
        return state
    extraction = state.get("extraction")
    if not extraction:
        return state

    try:
        provider_name = state.get("place_provider") or "kakao"
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
        extraction_for_place = align_location_with_place_query(extraction, place_query)
        place_recommendation = recommend_place(
            extraction_for_place,
            state.get("recommendation"),
            provider=provider,
            query_override=place_query,
        )
        return {
            **state,
            "extraction": extraction_for_place,
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

    def plan_with_exaone(extractor) -> tuple[str | None, str, str]:
        prompt = build_place_search_prompt(
            state["thread"],
            extraction.model_dump_json(),
            recommendation.model_dump_json() if recommendation else None,
        )
        data = parse_json_object(extractor.generate_text(prompt))
        if data.get("needs_place_search") is False:
            return None, getattr(extractor, "provider_name", "llm"), str(data.get("reason") or "")
        query = str(data.get("search_query") or "").strip()
        if query:
            return query, getattr(extractor, "provider_name", "llm"), str(data.get("reason") or "")
        raise ValueError("EXAONE place planner returned an empty search_query.")

    result, _extractor = run_with_exaone_fallback(
        state,
        "place search planning",
        plan_with_exaone,
        allow_none=False,
    )
    return result


def align_location_with_place_query(extraction: ExtractionResult, place_query: str | None) -> ExtractionResult:
    query = (place_query or "").strip()
    if not query:
        return extraction
    query_area = _known_area(query)
    current_area = _known_area(extraction.location_preference or "")
    if query_area and query_area != current_area:
        return extraction.model_copy(update={"location_preference": query})
    if query_area and not (extraction.location_preference or "").strip():
        return extraction.model_copy(update={"location_preference": query})
    return extraction


def _known_area(text: str) -> str:
    lowered = text.lower()
    for area, aliases in {
        "한양대": ["한양대", "한양", "hanyang"],
        "숭실대": ["숭실대", "숭실", "soongsil"],
        "건대": ["건대", "건국대", "konkuk"],
        "명지대": ["명지대", "명지대학교", "myongji"],
        "강남역": ["강남역", "강남", "gangnam"],
        "홍대": ["홍대", "hongdae"],
        "판교": ["판교", "pangyo"],
    }.items():
        if any(alias in lowered for alias in aliases):
            return area
    return ""


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
    for area in ["명지대", "명지대학교", "숭실대", "강남역", "강남", "홍대", "판교", "서울대", "신촌", "잠실", "사당"]:
        if area.lower() in text:
            return "명지대" if area == "명지대학교" else area
    return ""


def _clean_location_text(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip().rstrip(".")
    return cleaned.removeprefix("장소는").strip()


def reserve_place_node(state: ExtractionGraphState) -> ExtractionGraphState:
    if state.get("error"):
        return state
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
        return {
            **state,
            "reservation_target": reservation_target,
            "reservation_target_source": resolve_reservation_target_source(state),
            "reservation_result": reservation_result,
        }
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


def resolve_reservation_target_source(state: ExtractionGraphState) -> str | None:
    target = state.get("reservation_target")
    place_recommendation = state.get("place_recommendation")
    selected_url = None
    if place_recommendation and place_recommendation.selected:
        selected_url = place_recommendation.selected.source_url

    if state.get("reservation_target_auto") and selected_url:
        provider = state.get("place_provider") or "place"
        return f"{provider}_selected_place"
    if target:
        return "provided_or_inferred_target"
    if selected_url:
        return "selected_place"
    return None


def generate_reply_node(state: ExtractionGraphState) -> ExtractionGraphState:
    if state.get("error"):
        return state
    extraction = state.get("extraction")
    if not extraction:
        return state

    try:
        draft, extractor = run_with_exaone_fallback(
            state,
            "reply generation",
            lambda candidate: generate_reply_draft_with_llm(
                extraction,
                state.get("recommendation"),
                place_recommendation=state.get("place_recommendation"),
                reservation_result=state.get("reservation_result"),
                timezone=state.get("timezone", "Asia/Seoul"),
                generate_text=candidate.generate_text,
            ),
            allow_none=False,
        )
        return {**state, "reply_draft": draft, "llm_model": extractor.model_name}
    except Exception as exc:
        previous_error = state.get("error")
        error = f"{previous_error}; reply generation failed: {exc}" if previous_error else f"reply generation failed: {exc}"
        return {**state, "error": error}

def create_graph_extractor(state: ExtractionGraphState):
    return create_constraint_extractor(
        state.get("llm_provider"),
        model=state.get("llm_model"),
    )
