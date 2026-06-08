from __future__ import annotations

import base64
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CREDENTIALS_PATH = PROJECT_ROOT / "credentials" / "google_oauth_client.json"
DEFAULT_TOKEN_PATH = PROJECT_ROOT / "credentials" / "google_workspace_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
SCHEDULE_KEYWORDS = [
    "회의",
    "미팅",
    "면담",
    "일정",
    "약속",
    "참석",
    "가능",
    "시간",
    "인터뷰",
    "상담",
    "콜",
    "meeting",
    "meet",
    "schedule",
    "appointment",
    "available",
    "availability",
    "interview",
    "sync",
    "call",
]


def google_workspace_status() -> dict[str, Any]:
    load_dotenv()
    credentials_path = _credentials_path()
    token_path = _token_path()
    return {
        "configured": credentials_path.exists(),
        "authorized": token_path.exists(),
        "credentials_path": str(credentials_path),
        "token_path": str(token_path),
        "scopes": SCOPES,
        "message": (
            "Google OAuth credentials are configured."
            if credentials_path.exists()
            else "Add Google OAuth Desktop credentials to credentials/google_oauth_client.json."
        ),
    }


def fetch_latest_gmail_text(query: str = "newer_than:30d", max_results: int = 5) -> dict[str, Any]:
    result = fetch_schedule_related_gmail_texts(query=query, max_results=max_results, top_k=1)
    if not result["messages"]:
        return {"email_text": "", "source": "gmail", "message": result["message"]}
    first = result["messages"][0]
    return {
        "email_text": first["email_text"],
        "source": "gmail",
        "message_id": first["message_id"],
        "subject": first["subject"],
        "schedule_score": first["schedule_score"],
    }


def fetch_schedule_related_gmail_texts(
    *,
    query: str = "newer_than:3d",
    max_results: int = 50,
    top_k: int = 10,
) -> dict[str, Any]:
    service = _build_service("gmail", "v1")
    response = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    messages = response.get("messages", [])
    candidates = []

    for item in messages:
        message = service.users().messages().get(userId="me", id=item["id"], format="full").execute()
        candidate = _gmail_message_to_candidate(message)
        if candidate["schedule_score"] <= 0:
            continue
        candidates.append(candidate)

    candidates.sort(key=lambda item: (item["schedule_score"], item.get("internal_date", 0)), reverse=True)
    selected = candidates[:top_k]
    if not selected:
        return {
            "source": "gmail",
            "query": query,
            "messages": [],
            "email_text": "",
            "message": "No schedule-related Gmail messages were found in the selected period.",
        }

    combined = "\n\n---\n\n".join(item["email_text"] for item in selected)
    return {
        "source": "gmail",
        "query": query,
        "messages": selected,
        "email_text": combined,
        "message": f"Found {len(selected)} schedule-related Gmail messages.",
    }


def fetch_calendar_busy_events(days: int = 14, calendar_id: str = "primary") -> dict[str, Any]:
    service = _build_service("calendar", "v3")
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days)).isoformat()
    response = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        )
        .execute()
    )
    busy_events = []
    for item in response.get("items", []):
        start = item.get("start", {}).get("dateTime")
        end = item.get("end", {}).get("dateTime")
        if not start or not end:
            continue
        busy_events.append(
            {
                "participant": "me",
                "start": start,
                "end": end,
                "title": item.get("summary", ""),
                "location": item.get("location"),
            }
        )
    return {
        "timezone": "Asia/Seoul",
        "allowed_hours": {"start": "09:00", "end": "22:00"},
        "default_duration_minutes": 60,
        "participants": ["me"],
        "busy_events": busy_events,
        "source": "google_calendar",
    }


def _gmail_message_to_candidate(message: dict[str, Any]) -> dict[str, Any]:
    headers = {item["name"].lower(): item["value"] for item in message.get("payload", {}).get("headers", [])}
    subject = headers.get("subject", "(no subject)")
    sender = headers.get("from", "")
    date = headers.get("date", "")
    snippet = message.get("snippet", "")
    body = _extract_text_from_payload(message.get("payload", {}))
    body = body or snippet
    text_for_score = f"{subject}\n{snippet}\n{body}"
    score = _schedule_score(text_for_score)
    email_text = f"Subject: {subject}\nFrom: {sender}\nDate: {date}\n\n{body.strip() or snippet}"
    return {
        "message_id": message.get("id"),
        "thread_id": message.get("threadId"),
        "subject": subject,
        "from": sender,
        "date": date,
        "snippet": snippet,
        "email_text": email_text,
        "schedule_score": score,
        "matched_keywords": _matched_schedule_keywords(text_for_score),
        "internal_date": int(message.get("internalDate", "0") or 0),
    }


def _extract_text_from_payload(payload: dict[str, Any]) -> str:
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")
    if body_data and mime_type in {"text/plain", "text/html"}:
        text = _decode_gmail_body(body_data)
        if mime_type == "text/html":
            text = re.sub(r"<[^>]+>", " ", text)
        return " ".join(text.split())

    parts = []
    for part in payload.get("parts", []) or []:
        text = _extract_text_from_payload(part)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _decode_gmail_body(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="replace")


def _schedule_score(text: str) -> int:
    lowered = text.lower()
    return sum(1 for keyword in SCHEDULE_KEYWORDS if keyword.lower() in lowered)


def _matched_schedule_keywords(text: str) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in SCHEDULE_KEYWORDS if keyword.lower() in lowered]


def _build_service(api_name: str, api_version: str):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ImportError(
            "Google Workspace integration requires google-api-python-client, "
            "google-auth-oauthlib, and google-auth-httplib2."
        ) from exc

    credentials_path = _credentials_path()
    token_path = _token_path()
    if not credentials_path.exists():
        raise FileNotFoundError(f"Google OAuth credentials not found: {credentials_path}")

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return build(api_name, api_version, credentials=creds)


def _credentials_path() -> Path:
    load_dotenv()
    return Path(os.getenv("GOOGLE_OAUTH_CLIENT_FILE", str(DEFAULT_CREDENTIALS_PATH)))


def _token_path() -> Path:
    load_dotenv()
    return Path(os.getenv("GOOGLE_WORKSPACE_TOKEN_FILE", str(DEFAULT_TOKEN_PATH)))
