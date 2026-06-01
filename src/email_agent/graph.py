from __future__ import annotations

from typing import Literal, TypedDict

from .extractor import extract_constraints, parse_email_thread
from .llm_extractor import GeminiConstraintExtractor
from .place_retriever import provider_from_name, recommend_place
from .reply_generator import generate_reply_draft
from .schema import EmailThread, ExtractionResult, PlaceRecommendation, ReplyDraft, ScheduleRecommendation
from .scheduling import load_calendar, recommend_time
from .time_normalizer import normalize_extraction_times


class ExtractionGraphState(TypedDict, total=False):
    email_text: str
    reference_date: str | None
    timezone: str
    calendar_path: str | None
    place_provider: str | None
    place_search_html: str | None
    thread: EmailThread
    extraction: ExtractionResult
    recommendation: ScheduleRecommendation
    place_recommendation: PlaceRecommendation
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
    graph.add_node("llm_extract", llm_extract_node)
    graph.add_node("rule_fallback", rule_fallback_node)
    graph.add_node("normalize_times", normalize_times_node)
    graph.add_node("schedule", schedule_node)
    graph.add_node("retrieve_places", retrieve_places_node)
    graph.add_node("generate_reply", generate_reply_node)

    graph.add_edge(START, "parse_email")
    graph.add_edge("parse_email", "llm_extract")
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
    graph.add_edge("retrieve_places", "generate_reply")
    graph.add_edge("generate_reply", END)

    return graph.compile()


def parse_email_node(state: ExtractionGraphState) -> ExtractionGraphState:
    thread = parse_email_thread(state["email_text"])
    return {**state, "thread": thread}


def llm_extract_node(state: ExtractionGraphState) -> ExtractionGraphState:
    try:
        extractor = GeminiConstraintExtractor()
        result = extractor.extract(
            state["thread"],
            reference_date=state.get("reference_date"),
            timezone=state.get("timezone", "Asia/Seoul"),
        )
        return {**state, "extraction": result, "provider": "gemini"}
    except Exception as exc:
        return {**state, "error": str(exc), "provider": "gemini_failed"}


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
        recommendation = recommend_time(extraction, load_calendar(calendar_path))
        return {**state, "recommendation": recommendation}
    except Exception as exc:
        previous_error = state.get("error")
        error = f"{previous_error}; scheduling failed: {exc}" if previous_error else f"scheduling failed: {exc}"
        return {**state, "error": error}


def retrieve_places_node(state: ExtractionGraphState) -> ExtractionGraphState:
    extraction = state.get("extraction")
    if not extraction:
        return state

    try:
        provider_name = state.get("place_provider") or ("html" if state.get("place_search_html") else "mock")
        provider = provider_from_name(provider_name, html_path=state.get("place_search_html"))
        place_recommendation = recommend_place(
            extraction,
            state.get("recommendation"),
            provider=provider,
        )
        return {**state, "place_recommendation": place_recommendation}
    except Exception as exc:
        previous_error = state.get("error")
        error = f"{previous_error}; place retrieval failed: {exc}" if previous_error else f"place retrieval failed: {exc}"
        return {**state, "error": error}


def generate_reply_node(state: ExtractionGraphState) -> ExtractionGraphState:
    extraction = state.get("extraction")
    if not extraction:
        return state

    try:
        draft = generate_reply_draft(
            extraction,
            state.get("recommendation"),
            place_recommendation=state.get("place_recommendation"),
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
