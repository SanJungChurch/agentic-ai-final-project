from __future__ import annotations

from .config import Settings, load_settings
from .json_utils import parse_json_object
from .prompting import build_constraint_extraction_prompt
from .schema import EmailThread, ExtractionResult


class GeminiConstraintExtractor:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        if not self.settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is missing. Create a .env file or set the environment variable.")

    def extract(
        self,
        thread: EmailThread,
        *,
        reference_date: str | None = None,
        timezone: str = "Asia/Seoul",
    ) -> ExtractionResult:
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError("google-genai is not installed. Run: pip install google-genai") from exc

        client = genai.Client(api_key=self.settings.gemini_api_key)
        prompt = build_constraint_extraction_prompt(
            thread,
            reference_date=reference_date,
            timezone=timezone,
        )

        response = client.models.generate_content(
            model=self.settings.gemini_model,
            contents=prompt,
        )
        raw_text = getattr(response, "text", None)
        if not raw_text:
            raise ValueError("Gemini returned an empty response.")

        data = parse_json_object(raw_text)
        return ExtractionResult.model_validate(data)
