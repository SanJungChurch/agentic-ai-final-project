from __future__ import annotations

import re
from pathlib import Path

from .schema import EmailThread, ExtractionResult, Intent, TimeConstraint


PARTICIPANT_RE = re.compile(r"^\s*([A-Za-z가-힣]{2,12})\s*[:：]", re.MULTILINE)
TIME_RE = re.compile(
    r"((?:이번|다음)\s*주\s*)?([월화수목금토일]요일|오늘|내일)"
    r"(?:\s*(오전|오후|저녁))?"
    r"(?:\s*(\d{1,2})\s*(?:시|:00)?(?:\s*(?:이후|전|쯤))?)?"
)
LOCATION_RE = re.compile(
    r"(장소는|근처|카페|커피|식당|음식점|레스토랑|회의실|세미나실|상담실|스터디룸|"
    r"학교|연구실|사무실|병원|클리닉|숭실대|강남|홍대|판교|신촌|잠실|사당)"
)
UNAVAILABLE_RE = re.compile(r"(어렵|불가|안\s*돼|안\s*됩니다|힘들)")
AVAILABLE_RE = re.compile(r"(가능|괜찮|좋|됩니다|돼요|됩니다)")


def parse_email_thread(text: str) -> EmailThread:
    lines = text.strip().splitlines()
    subject = None
    body = text.strip()

    if lines and lines[0].lower().startswith("subject:"):
        subject = lines[0].split(":", 1)[1].strip()
        body = "\n".join(lines[1:]).strip()

    return EmailThread(subject=subject, body=body)


def extract_constraints(thread: EmailThread) -> ExtractionResult:
    """Rule-based fallback extractor for week 1 development.

    The Gemini-backed extractor will replace or augment this function once the
    prompt and API wrapper are ready.
    """
    participants = sorted(set(PARTICIPANT_RE.findall(thread.body)))
    participant_names = [item[0] if isinstance(item, tuple) else item for item in participants]

    candidate_times: list[TimeConstraint] = []
    unavailable_times: list[TimeConstraint] = []

    for line in thread.body.splitlines():
        speaker_match = re.match(r"\s*([A-Za-z가-힣]{2,12})\s*[:：]", line)
        participant = speaker_match.group(1) if speaker_match else None
        content = line.split(":", 1)[1] if ":" in line else line

        for expression, availability in _extract_time_constraints(content):

            constraint = TimeConstraint(
                participant=participant,
                expression=expression,
                availability=availability,
            )
            if availability == "unavailable":
                unavailable_times.append(constraint)
            else:
                candidate_times.append(constraint)

    location_preference = _extract_location_preference(thread.body)
    missing_information = []
    if not candidate_times:
        missing_information.append("candidate_time")
    if not participant_names:
        missing_information.append("participants")
    if not location_preference:
        missing_information.append("location_preference")

    return ExtractionResult(
        intent=Intent.SCHEDULE_MEETING if candidate_times else Intent.OTHER,
        participants=participant_names,
        candidate_times=candidate_times,
        unavailable_times=unavailable_times,
        location_preference=location_preference,
        meeting_duration_minutes=None,
        missing_information=missing_information,
        confidence=0.55 if candidate_times else 0.25,
        source_summary=_summarize(thread),
    )


def extract_from_file(path: str | Path) -> ExtractionResult:
    thread = parse_email_thread(Path(path).read_text(encoding="utf-8"))
    return extract_constraints(thread)


def _availability_for_line(line: str) -> str:
    if UNAVAILABLE_RE.search(line):
        return "unavailable"
    if AVAILABLE_RE.search(line):
        return "available"
    return "unknown"


def _extract_time_constraints(text: str) -> list[tuple[str, str]]:
    constraints: list[tuple[str, str]] = []
    clauses = re.split(r"(?<=고)|[,/]| 또는 | 혹은 | 그리고 ", text)

    for clause in clauses:
        availability = _availability_for_line(clause)
        for match in TIME_RE.finditer(clause):
            expression = match.group(0).strip()
            if expression:
                constraints.append((expression, availability))

    return _dedupe_constraints(constraints)


def _dedupe_constraints(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []

    for expression, availability in items:
        key = (expression, availability)
        if key in seen:
            continue
        seen.add(key)
        result.append(key)

    return result


def _extract_location_preference(text: str) -> str | None:
    for line in text.splitlines():
        if LOCATION_RE.search(line):
            return line.strip()
    return None


def _summarize(thread: EmailThread) -> str:
    subject = f"{thread.subject}: " if thread.subject else ""
    compact = " ".join(thread.body.split())
    return f"{subject}{compact[:180]}"
