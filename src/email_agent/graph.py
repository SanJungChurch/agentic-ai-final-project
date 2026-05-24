from __future__ import annotations

from typing import Literal, TypedDict

from .extractor import extract_constraints, parse_email_thread
from .llm_extractor import GeminiConstraintExtractor
from .schema import EmailThread, ExtractionResult


class ExtractionGraphState(TypedDict, total=False):
    email_text: str
    reference_date: str | None
    timezone: str
    thread: EmailThread
    extraction: ExtractionResult
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

    graph.add_edge(START, "parse_email")
    graph.add_edge("parse_email", "llm_extract")
    graph.add_conditional_edges(
        "llm_extract",
        should_fallback,
        {
            "fallback": "rule_fallback",
            "done": END,
        },
    )
    graph.add_edge("rule_fallback", END)

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


def should_fallback(state: ExtractionGraphState) -> Literal["fallback", "done"]:
    if state.get("error") or not state.get("extraction"):
        return "fallback"
    return "done"
