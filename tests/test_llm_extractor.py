import unittest

from src.email_agent.config import Settings
from src.email_agent.llm_extractor import (
    ExaoneConstraintExtractor,
    OllamaConstraintExtractor,
    _reference_date_from_response,
    create_constraint_extractor,
)
from src.email_agent.schema import EmailThread


class LlmExtractorTest(unittest.TestCase):
    def test_reference_date_from_llm_json(self) -> None:
        thread = EmailThread(subject="meeting", body="Date: Tue, 26 May 2026 09:00:00 +0900")

        self.assertEqual(
            _reference_date_from_response('{"reference_date": "2026-05-27", "evidence": "body"}', thread),
            "2026-05-27",
        )

    def test_reference_date_falls_back_to_email_header(self) -> None:
        thread = EmailThread(subject="meeting", body="Date: Tue, 26 May 2026 09:00:00 +0900\n\n내일 회의")

        self.assertEqual(_reference_date_from_response("not json", thread), "2026-05-26")

    def test_qwen4b_alias_resolves_to_ollama_model(self) -> None:
        settings = Settings(llm_provider="gemini", ollama_model="qwen3:4b")

        extractor = create_constraint_extractor("qwen4bmodel", settings=settings)

        self.assertIsInstance(extractor, OllamaConstraintExtractor)
        self.assertEqual(extractor.model, "qwen3:4b")
        self.assertEqual(extractor.provider_name, "ollama")
        self.assertEqual(extractor.model_name, "qwen3:4b")

    def test_explicit_llm_model_overrides_env_model(self) -> None:
        settings = Settings(llm_provider="gemini", ollama_model="qwen3:4b")

        extractor = create_constraint_extractor("qwen", settings=settings, model="qwen2.5:7b")

        self.assertIsInstance(extractor, OllamaConstraintExtractor)
        self.assertEqual(extractor.model, "qwen2.5:7b")
        self.assertEqual(extractor.model_name, "qwen2.5:7b")

    def test_provider_can_be_ollama_model_tag(self) -> None:
        settings = Settings(llm_provider="gemini", ollama_model="qwen3:4b")

        extractor = create_constraint_extractor("qwen2.5:7b", settings=settings)

        self.assertIsInstance(extractor, OllamaConstraintExtractor)
        self.assertEqual(extractor.model, "qwen2.5:7b")

    def test_exaone_provider_resolves_to_huggingface_model(self) -> None:
        settings = Settings(llm_provider="exaone", exaone_model="LGAI-EXAONE/EXAONE-4.0-1.2B")

        extractor = create_constraint_extractor("exaone", settings=settings)

        self.assertIsInstance(extractor, ExaoneConstraintExtractor)
        self.assertEqual(extractor.provider_name, "exaone")
        self.assertEqual(extractor.model_name, "LGAI-EXAONE/EXAONE-4.0-1.2B")

    def test_gemini_provider_ignores_ollama_model_override_for_reporting(self) -> None:
        settings = Settings(
            llm_provider="gemini",
            gemini_api_key="test-key",
            gemini_model="gemini-2.5-flash",
            ollama_model="qwen3:4b",
        )

        extractor = create_constraint_extractor("gemini", settings=settings, model="qwen3:4b")

        self.assertEqual(extractor.provider_name, "gemini")
        self.assertEqual(extractor.model_name, "gemini-2.5-flash")


if __name__ == "__main__":
    unittest.main()
