from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.email_agent.google_workspace import fetch_latest_gmail_text, google_workspace_status


def main() -> None:
    status = google_workspace_status()
    print(f"credentials: {status['credentials_path']}")
    print(f"token: {status['token_path']}")
    if not status["configured"]:
        raise SystemExit("Google OAuth credentials are missing.")

    print("Starting Google OAuth login. Complete the browser login window.")
    result = fetch_latest_gmail_text(query="newer_than:30d", max_results=1)
    print("OAuth completed.")
    print(f"Loaded Gmail source: {result.get('subject') or result.get('message')}")


if __name__ == "__main__":
    main()
