from __future__ import annotations

from pathlib import Path

from .schema import EmailThread


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_PATH = PROJECT_ROOT / "prompts" / "constraint_extraction_prompt.md"


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
