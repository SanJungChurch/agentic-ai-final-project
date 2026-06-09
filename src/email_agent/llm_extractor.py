from __future__ import annotations

import json
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib import error, request

from .config import Settings, load_settings
from .json_utils import parse_json_object
from .prompting import build_constraint_extraction_prompt, build_reference_date_prompt
from .schema import EmailThread, ExtractionResult


class ConstraintExtractor(Protocol):
    provider_name: str

    def extract(
        self,
        thread: EmailThread,
        *,
        reference_date: str | None = None,
        timezone: str = "Asia/Seoul",
    ) -> ExtractionResult:
        ...

    def infer_reference_date(self, thread: EmailThread, *, timezone: str = "Asia/Seoul") -> str | None:
        ...

    def generate_text(self, prompt: str) -> str:
        ...


class GeminiConstraintExtractor:
    provider_name = "gemini"

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

    def infer_reference_date(self, thread: EmailThread, *, timezone: str = "Asia/Seoul") -> str | None:
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError("google-genai is not installed. Run: pip install google-genai") from exc

        client = genai.Client(api_key=self.settings.gemini_api_key)
        response = client.models.generate_content(
            model=self.settings.gemini_model,
            contents=build_reference_date_prompt(thread, timezone=timezone),
        )
        return _reference_date_from_response(getattr(response, "text", None), thread)

    def generate_text(self, prompt: str) -> str:
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError("google-genai is not installed. Run: pip install google-genai") from exc

        client = genai.Client(api_key=self.settings.gemini_api_key)
        response = client.models.generate_content(
            model=self.settings.gemini_model,
            contents=prompt,
        )
        raw_text = getattr(response, "text", None)
        if not raw_text:
            raise ValueError("Gemini returned an empty response.")
        return raw_text.strip()


class OllamaConstraintExtractor:
    def __init__(self, settings: Settings | None = None, *, model: str | None = None) -> None:
        self.settings = settings or load_settings()
        self.model = model or self.settings.ollama_model
        self.provider_name = f"ollama:{self.model}"

    def extract(
        self,
        thread: EmailThread,
        *,
        reference_date: str | None = None,
        timezone: str = "Asia/Seoul",
    ) -> ExtractionResult:
        prompt = build_constraint_extraction_prompt(
            thread,
            reference_date=reference_date,
            timezone=timezone,
        )
        raw_text = self._generate(prompt)
        data = parse_json_object(raw_text)
        return ExtractionResult.model_validate(data)

    def infer_reference_date(self, thread: EmailThread, *, timezone: str = "Asia/Seoul") -> str | None:
        raw_text = self._generate(build_reference_date_prompt(thread, timezone=timezone))
        return _reference_date_from_response(raw_text, thread)

    def generate_text(self, prompt: str) -> str:
        return self._generate(prompt).strip()

    def _generate(self, prompt: str) -> str:
        url = self.settings.ollama_base_url.rstrip("/") + "/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with request.urlopen(req, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.URLError as exc:
            raise RuntimeError(
                f"Ollama request failed. Make sure Ollama is running and `{self.model}` is pulled."
            ) from exc
        raw_text = data.get("response")
        if not raw_text:
            raise ValueError("Ollama returned an empty response.")
        return raw_text


def create_constraint_extractor(
    provider: str | None = None,
    settings: Settings | None = None,
    *,
    model: str | None = None,
) -> ConstraintExtractor:
    resolved_settings = settings or load_settings()
    selected = (provider or resolved_settings.llm_provider or "gemini").strip().lower()
    if selected in {"gemini", "google"}:
        return GeminiConstraintExtractor(resolved_settings)
    ollama_model = _resolve_ollama_model(selected, resolved_settings, model=model)
    if ollama_model:
        return OllamaConstraintExtractor(resolved_settings, model=ollama_model)
    raise ValueError(f"Unknown LLM provider: {provider}")


def _resolve_ollama_model(
    selected_provider: str,
    settings: Settings,
    *,
    model: str | None = None,
) -> str | None:
    explicit_model = _clean_model_name(model)
    if ":" in selected_provider and selected_provider.startswith(("qwen", "llama", "mistral", "gemma")):
        return explicit_model or selected_provider
    if selected_provider in {"ollama", "qwen", "qwen3", "qwen4b", "qwen4bmodel", "qwen3-4b", "qwen3_4b"}:
        return explicit_model or settings.ollama_model

    for prefix in ["ollama:", "qwen:"]:
        if selected_provider.startswith(prefix):
            embedded_model = _clean_model_name(selected_provider.removeprefix(prefix))
            return explicit_model or embedded_model or settings.ollama_model

    if selected_provider.startswith("qwen") and "4b" in selected_provider:
        return explicit_model or settings.ollama_model
    return None


def _clean_model_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned.lower() in {"", "auto", "none", "null"}:
        return None
    return cleaned


def _reference_date_from_response(raw_text: str | None, thread: EmailThread) -> str | None:
    if raw_text:
        try:
            data = parse_json_object(raw_text)
            value = data.get("reference_date")
            if isinstance(value, str) and value.lower() not in {"", "null", "none"}:
                return value[:10]
        except Exception:
            pass
    return _reference_date_from_header(thread)


def _reference_date_from_header(thread: EmailThread) -> str | None:
    for line in thread.body.splitlines():
        if not line.lower().startswith("date:"):
            continue
        raw_date = line.split(":", 1)[1].strip()
        try:
            return parsedate_to_datetime(raw_date).date().isoformat()
        except (TypeError, ValueError, IndexError):
            return None
    return None
