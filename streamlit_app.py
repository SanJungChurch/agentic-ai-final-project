from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from src.email_agent.graph import build_extraction_graph


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SAMPLE = PROJECT_ROOT / "data" / "samples" / "email_001.txt"
DEFAULT_CALENDAR = PROJECT_ROOT / "data" / "calendars" / "synthetic_calendar_001.json"
DEFAULT_PLACE_HTML = PROJECT_ROOT / "data" / "place_search" / "soongsil_cafes.html"
DEFAULT_RESERVATION_HTML = PROJECT_ROOT / "data" / "reservation" / "mock_reservation.html"


def main() -> None:
    st.set_page_config(
        page_title="Schedule-to-Action Agent Demo",
        page_icon="calendar",
        layout="wide",
    )

    st.title("Schedule-to-Action Agent")
    st.caption("E-mail extraction -> scheduling -> place retrieval -> ShowUI reservation -> reply draft")

    with st.sidebar:
        st.header("Demo Settings")
        reference_date = st.text_input("Reference date", value="2026-05-23")
        timezone = st.text_input("Timezone", value="Asia/Seoul")
        showui_mode = st.radio(
            "Reservation executor",
            ["DOM fallback demo", "Local GPU ShowUI-2B"],
            index=0,
            help="DOM fallback is fast and stable for recording. Local GPU uses ShowUI-2B visual grounding.",
        )
        llm_provider = st.selectbox(
            "LLM provider",
            ["exaone", "gemini", "qwen", "ollama", "rule_fallback"],
            index=0,
        )
        default_model = {
            "exaone": "LGAI-EXAONE/EXAONE-4.0-1.2B",
            "gemini": "gemini-2.5-flash",
            "qwen": "qwen3:4b",
            "ollama": "qwen3:4b",
            "rule_fallback": "",
        }[llm_provider]
        llm_model = st.text_input("LLM model", value=default_model)
        run_button = st.button("Run Pipeline", type="primary", use_container_width=True)

        st.divider()
        st.subheader("Target Page")
        st.code(DEFAULT_RESERVATION_HTML.as_uri(), language="text")

    email_text = DEFAULT_SAMPLE.read_text(encoding="utf-8")

    left, right = st.columns([0.95, 1.05], gap="large")

    with left:
        st.subheader("Input E-mail")
        email_text = st.text_area(
            "E-mail thread",
            value=email_text,
            height=230,
            label_visibility="collapsed",
        )

        st.subheader("Reservation Page Preview")
        components.html(
            DEFAULT_RESERVATION_HTML.read_text(encoding="utf-8"),
            height=520,
            scrolling=True,
        )

    with right:
        st.subheader("Pipeline")
        if "demo_result" not in st.session_state:
            st.info("Click Run Pipeline to start the demo.")
        else:
            render_result(st.session_state["demo_result"])

    if run_button:
        runner_command = build_runner_command(showui_mode)
        state = {
            "email_text": email_text,
            "reference_date": reference_date,
            "timezone": timezone,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "calendar_path": str(DEFAULT_CALENDAR),
            "place_provider": "html",
            "place_search_html": str(DEFAULT_PLACE_HTML),
            "reservation_provider": "showui",
            "reservation_target": DEFAULT_RESERVATION_HTML.as_uri(),
            "showui_runner_command": runner_command,
        }

        with st.spinner("Running agent workflow..."):
            result = run_graph(state)
        st.session_state["demo_result"] = result
        st.rerun()


def run_graph(state: dict) -> dict:
    result = build_extraction_graph().invoke(state)
    return {
        "provider": result.get("provider"),
        "llm_model": result.get("llm_model"),
        "error": result.get("error"),
        "extraction": result["extraction"].model_dump() if result.get("extraction") else None,
        "recommendation": result["recommendation"].model_dump() if result.get("recommendation") else None,
        "place_recommendation": result["place_recommendation"].model_dump() if result.get("place_recommendation") else None,
        "reservation_result": result["reservation_result"].model_dump() if result.get("reservation_result") else None,
        "reply_draft": result["reply_draft"].model_dump() if result.get("reply_draft") else None,
    }


def build_runner_command(showui_mode: str) -> str:
    runner = PROJECT_ROOT / "scripts" / "showui_reservation_runner.py"
    command = f"{sys.executable} {runner} --headless"
    if showui_mode == "DOM fallback demo":
        command += " --dom-fallback"
    else:
        command += " --showui-source local"
    return command


def render_result(result: dict) -> None:
    provider = result.get("provider") or "-"
    model = result.get("llm_model") or "-"
    error = result.get("error")
    reservation = result.get("reservation_result") or {}
    reply = result.get("reply_draft") or {}

    status_cols = st.columns(4)
    status_cols[0].metric("Extractor", provider, help=f"Model: {model}")
    status_cols[1].metric("Schedule", _nested_status(result, "recommendation"))
    status_cols[2].metric("Place", _nested_status(result, "place_recommendation"))
    status_cols[3].metric("Reservation", reservation.get("status", "-"))

    if error:
        st.warning(f"Recovered error: {error}")

    tabs = st.tabs(["Extraction", "Schedule", "Place", "ShowUI Reservation", "Reply", "Raw JSON"])

    with tabs[0]:
        extraction = result.get("extraction") or {}
        st.write("Participants")
        st.write(extraction.get("participants", []))
        st.write("Candidate times")
        st.dataframe(extraction.get("candidate_times", []), use_container_width=True)
        st.write("Unavailable times")
        st.dataframe(extraction.get("unavailable_times", []), use_container_width=True)

    with tabs[1]:
        recommendation = result.get("recommendation") or {}
        selected = (recommendation.get("selected") or {}).get("candidate") or {}
        st.success(recommendation.get("summary", ""))
        st.json(selected)

    with tabs[2]:
        place = result.get("place_recommendation") or {}
        st.write(place.get("summary", ""))
        st.dataframe(place.get("candidates", []), use_container_width=True)

    with tabs[3]:
        if reservation.get("status") == "confirmed":
            st.success(reservation.get("message", "Reservation confirmed."))
        elif reservation:
            st.error(reservation.get("message", "Reservation failed."))
        st.write("Steps")
        st.write(reservation.get("steps", []))
        st.json(reservation)

    with tabs[4]:
        st.text_area("Generated reply", value=reply.get("body", ""), height=260)

    with tabs[5]:
        st.json(result)


def _nested_status(result: dict, key: str) -> str:
    value = result.get(key) or {}
    return value.get("status", "-")


if __name__ == "__main__":
    main()
