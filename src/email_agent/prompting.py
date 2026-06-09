from __future__ import annotations

import json
from pathlib import Path

from .schema import EmailThread


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_PATH = PROJECT_ROOT / "prompts" / "constraint_extraction_prompt.md"
TIME_OPTIMIZATION_PROMPT_PATH = PROJECT_ROOT / "prompts" / "time_optimization_prompt.md"
REPLY_GENERATION_PROMPT_PATH = PROJECT_ROOT / "prompts" / "reply_generation_prompt.md"


def load_prompt_template(path: str | Path = DEFAULT_PROMPT_PATH) -> str:
    return Path(path).read_text(encoding="utf-8")


def build_constraint_extraction_prompt(
    thread: EmailThread,
    *,
    reference_date: str | None = None,
    timezone: str = "Asia/Seoul",
    template: str | None = None,
) -> str:
    prompt_template = template or load_prompt_template()
    subject = thread.subject or "(no subject)"
    reference_date_text = reference_date or "not provided"

    return f"""{prompt_template}

Reference date: {reference_date_text}
Timezone: {timezone}

E-mail subject:
{subject}

E-mail thread:
{thread.body}
"""


def build_reference_date_prompt(thread: EmailThread, *, timezone: str = "Asia/Seoul") -> str:
    subject = thread.subject or "(no subject)"
    return f"""You are a metadata parser for an e-mail scheduling agent.

Infer the best reference date for resolving relative time expressions such as today, tomorrow, this week, 다음 주, 수요일, 내일.

Use the e-mail Date header first when it is present. If there is no usable Date header, use the most explicit date in the body that indicates when the message was written or sent. Do not use proposed meeting dates as the reference date unless the message clearly says it was written on that date.

Return only valid JSON:
{{"reference_date": "YYYY-MM-DD or null", "evidence": "short reason"}}

Timezone: {timezone}

E-mail subject:
{subject}

E-mail thread:
{thread.body}
"""


def build_place_search_prompt(
    thread: EmailThread,
    extraction_json: str,
    recommendation_json: str | None = None,
) -> str:
    subject = thread.subject or "(no subject)"
    recommendation_text = recommendation_json or "{}"
    return f"""You are the place retrieval planner for an e-mail-to-action scheduling agent.

Read the e-mail context and extracted scheduling facts. Decide whether the appointment needs a physical place search. If it does, create one concise Korean search query suitable for Kakao/Naver map search.

Important rules:
- Do not default to cafe.
- The search_query must be written in natural Korean only. Do not mix English words such as "meeting room", "search", or "booking".
- Do not include meta words such as "검색", "찾기", or "search" in search_query. Output a map keyword, for example "경희대 회의실", "경희대 세미나실", "명지대 스터디룸", or "홍대 식당".
- Choose the venue type that fits the appointment: restaurant for meals, study room for study/team project work, meeting room or seminar room for business/interview/presentation, office/department room when explicitly stated, clinic/hospital for medical visits, cafe only for coffee chat or when the text asks for a cafe.
- Include the location area when the text provides one, such as 한양대, 숭실대, 건대, 강남역, 홍대, 판교.
- If the appointment is clearly online/phone-only, set needs_place_search to false and search_query to null.
- Do not invent a specific store name. Return a search query, not a final place.
- Return JSON only.

Return this schema:
{{"needs_place_search": true, "search_query": "string or null", "venue_type": "string or null", "reason": "short Korean reason"}}

E-mail subject:
{subject}

E-mail thread:
{thread.body}

Extraction JSON:
{extraction_json}

Schedule recommendation JSON:
{recommendation_text}
"""


def build_time_optimization_prompt(
    *,
    email_thread: EmailThread,
    extraction_json: str,
    candidates: list[dict],
    calendar_json: str,
    preferred_date: str | None = None,
    template: str | None = None,
) -> str:
    prompt_template = template or load_prompt_template(TIME_OPTIMIZATION_PROMPT_PATH)
    subject = email_thread.subject or "(no subject)"
    return f"""{prompt_template}

Preferred date: {preferred_date or "not provided"}

E-mail subject:
{subject}

E-mail thread:
{email_thread.body}

Extraction JSON:
{extraction_json}

Calendar JSON:
{calendar_json}

Valid candidate list JSON:
{json.dumps(candidates, ensure_ascii=False, indent=2)}
"""


def build_reply_generation_prompt(
    *,
    extraction_json: str,
    recommendation_json: str | None,
    place_recommendation_json: str | None,
    reservation_result_json: str | None,
    timezone: str = "Asia/Seoul",
    template: str | None = None,
) -> str:
    prompt_template = template or load_prompt_template(REPLY_GENERATION_PROMPT_PATH)
    return f"""{prompt_template}

Timezone: {timezone}

Extraction JSON:
{extraction_json}

Schedule recommendation JSON:
{recommendation_json or "{}"}

Place recommendation JSON:
{place_recommendation_json or "{}"}

Reservation result JSON:
{reservation_result_json or "{}"}
"""
