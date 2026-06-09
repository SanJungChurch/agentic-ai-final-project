from __future__ import annotations

import json
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib import error, request

from .config import Settings, load_settings
from .json_utils import parse_json_object
from .prompting import build_constraint_extraction_prompt, build_reference_date_prompt
from .schema import EmailThread, ExtractionResult


class ConstraintExtractor(Protocol):
    provider_name: str
    model_name: str

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
        self.model_name = self.settings.gemini_model
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
        self.provider_name = "ollama"
        self.model_name = self.model

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


class ExaoneConstraintExtractor:
    provider_name = "exaone"
    _model_cache: dict[tuple[str, str, str], tuple[Any, Any]] = {}

    def __init__(self, settings: Settings | None = None, *, model: str | None = None) -> None:
        self.settings = settings or load_settings()
        self.model = model or self.settings.exaone_model
        self.model_name = self.model

    def extract(
        self,
        thread: EmailThread,
        *,
        reference_date: str | None = None,
        timezone: str = "Asia/Seoul",
    ) -> ExtractionResult:
        raw_text = self._generate(
            build_constraint_extraction_prompt(
                thread,
                reference_date=reference_date,
                timezone=timezone,
            )
        )
        data = parse_json_object(raw_text)
        return ExtractionResult.model_validate(data)

    def infer_reference_date(self, thread: EmailThread, *, timezone: str = "Asia/Seoul") -> str | None:
        raw_text = self._generate(build_reference_date_prompt(thread, timezone=timezone))
        return _reference_date_from_response(raw_text, thread)

    def generate_text(self, prompt: str) -> str:
        return self._generate(prompt).strip()

    def _generate(self, prompt: str) -> str:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise ImportError(
                "EXAONE local inference requires torch and transformers>=4.54.0. "
                "Install them, then retry: pip install torch 'transformers>=4.54.0' accelerate safetensors"
            ) from exc

        tokenizer, model = self._load_model(AutoModelForCausalLM, AutoTokenizer, torch)
        messages = [
            {
                "role": "system",
                "content": "You are a precise extraction model. Return only valid JSON when the task asks for JSON.",
            },
            {"role": "user", "content": prompt},
        ]
        inputs = _apply_chat_template(tokenizer, messages)
        device = next(model.parameters()).device
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.no_grad():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=self.settings.exaone_max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        generated_ids = output_ids[0][inputs["input_ids"].shape[-1] :]
        return tokenizer.decode(generated_ids, skip_special_tokens=True).strip()

    def _load_model(self, model_cls, tokenizer_cls, torch_module):
        cache_key = (self.model, self.settings.exaone_device_map, self.settings.exaone_torch_dtype)
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

        dtype = self.settings.exaone_torch_dtype
        torch_dtype = "auto" if dtype == "auto" else getattr(torch_module, dtype)
        tokenizer = tokenizer_cls.from_pretrained(self.model, trust_remote_code=True)
        model = model_cls.from_pretrained(
            self.model,
            device_map=self.settings.exaone_device_map,
            torch_dtype=torch_dtype,
            trust_remote_code=True,
        )
        model.eval()
        self._model_cache[cache_key] = (tokenizer, model)
        return tokenizer, model


def create_constraint_extractor(
    provider: str | None = None,
    settings: Settings | None = None,
    *,
    model: str | None = None,
) -> ConstraintExtractor:
    resolved_settings = settings or load_settings()
    selected_raw = (provider or resolved_settings.llm_provider or "exaone").strip()
    selected = selected_raw.lower()
    if selected in {"gemini", "google"}:
        return GeminiConstraintExtractor(resolved_settings)
    exaone_model = _resolve_exaone_model(selected_raw, resolved_settings, model=model)
    if exaone_model:
        return ExaoneConstraintExtractor(resolved_settings, model=exaone_model)
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


def _resolve_exaone_model(
    selected_provider: str,
    settings: Settings,
    *,
    model: str | None = None,
) -> str | None:
    selected = selected_provider.lower()
    explicit_model = _clean_model_name(model)
    if selected in {"exaone", "lgai-exaone", "transformers", "hf", "huggingface"}:
        return explicit_model or settings.exaone_model
    if selected.startswith("exaone:"):
        embedded_model = _clean_model_name(selected_provider.split(":", 1)[1])
        return explicit_model or embedded_model or settings.exaone_model
    if selected_provider.startswith("LGAI-EXAONE/") or selected_provider.startswith("lgai-exaone/"):
        return explicit_model or selected_provider
    return None


def _clean_model_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned.lower() in {"", "auto", "none", "null"}:
        return None
    return cleaned


def _apply_chat_template(tokenizer, messages: list[dict[str, str]]) -> dict[str, Any]:
    try:
        return tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        )
    except TypeError:
        input_ids = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
        )
        return {"input_ids": input_ids}


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
