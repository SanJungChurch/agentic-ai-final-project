import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.email_agent.graph import (
    apply_selected_date_to_candidates,
    align_location_with_place_query,
    build_extraction_graph,
    infer_place_search_query,
    resolve_reservation_target,
    resolve_reservation_target_source,
)
from src.email_agent.prompting import build_place_search_prompt


class GraphTest(unittest.TestCase):
    def test_graph_falls_back_without_api_key(self) -> None:
        email_text = Path("data/samples/email_001.txt").read_text(encoding="utf-8")
        graph = build_extraction_graph()

        with patch("src.email_agent.graph.create_exaone_extractor") as exaone_factory:
            exaone = exaone_factory.return_value
            exaone.provider_name = "exaone"
            exaone.model_name = "LGAI-EXAONE/EXAONE-4.0-1.2B"
            exaone.extract.side_effect = ValueError("invalid exaone output")
            with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": ""}, clear=False):
                result = graph.invoke(
                    {
                        "email_text": email_text,
                        "reference_date": "2026-05-23",
                        "timezone": "Asia/Seoul",
                        "llm_provider": "gemini",
                    }
                )

        self.assertEqual(result["provider"], "gemini_timeout")
        self.assertNotIn("extraction", result)
        self.assertIn("TimeoutError", result["error"])
        self.assertIn("GEMINI_API_KEY", result["error"])
        self.assertEqual(exaone.extract.call_count, 3)

    def test_graph_exaone_fallback_normalizes_then_schedules(self) -> None:
        email_text = Path("data/samples/email_001.txt").read_text(encoding="utf-8")
        extraction_json = Path("data/samples/extraction_001.normalized.json").read_text(encoding="utf-8")
        graph = build_extraction_graph()

        def generate_text(prompt: str) -> str:
            if "Valid candidate list JSON" in prompt:
                return '{"selected_index": 0, "score": 99, "reasons": ["적절한 시간입니다."], "summary": "시간 선택 완료"}'
            if "needs_place_search" in prompt:
                return '{"needs_place_search": true, "search_query": "숭실대 카페", "venue_type": "카페", "reason": "장소 검색 필요"}'
            return '{"subject": "Re: 일정 조율", "body": "확인했습니다.", "status": "ready", "rationale": []}'

        with patch("src.email_agent.graph.create_exaone_extractor") as exaone_factory:
            from src.email_agent.schema import ExtractionResult

            exaone = exaone_factory.return_value
            exaone.provider_name = "exaone"
            exaone.model_name = "LGAI-EXAONE/EXAONE-4.0-1.2B"
            exaone.extract.return_value = ExtractionResult.model_validate_json(extraction_json)
            exaone.generate_text.side_effect = generate_text
            with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": ""}, clear=False):
                result = graph.invoke(
                    {
                        "email_text": email_text,
                        "reference_date": "2026-05-23",
                        "timezone": "Asia/Seoul",
                        "llm_provider": "gemini",
                        "calendar_path": "data/calendars/synthetic_calendar_001.json",
                        "place_provider": "mock",
                    }
                )

        self.assertEqual(result["provider"], "exaone")
        self.assertEqual(result["recommendation"].status, "selected")
        self.assertEqual(result["place_recommendation"].status, "selected")
        self.assertEqual(result["reservation_result"].status, "confirmed")
        self.assertEqual(result["reply_draft"].status, "ready")
        self.assertIn("GEMINI_API_KEY", result["primary_llm_error"])

    def test_graph_runs_scheduling_when_calendar_path_is_given(self) -> None:
        email_text = Path("data/samples/extraction_001.normalized.json").read_text(encoding="utf-8")
        graph = build_extraction_graph()

        def generate_text(prompt: str) -> str:
            if "Valid candidate list JSON" in prompt:
                return '{"selected_index": 0, "score": 88, "reasons": ["캘린더와 충돌하지 않습니다."], "summary": "시간 선택 완료"}'
            if "needs_place_search" in prompt:
                return '{"needs_place_search": true, "search_query": "숭실대 카페", "venue_type": "카페", "reason": "장소 검색 필요"}'
            return '{"subject": "Re: 일정 조율", "body": "확인했습니다.", "status": "ready", "rationale": []}'

        with patch("src.email_agent.graph.parse_email_thread") as parse_mock:
            from src.email_agent.schema import EmailThread, ExtractionResult

            parse_mock.return_value = EmailThread(subject="normalized fixture", body="수요일 5시 회의")
            with patch("src.email_agent.graph.create_constraint_extractor") as factory_mock:
                extractor_mock = factory_mock.return_value
                extractor_mock.provider_name = "gemini"
                extractor_mock.model_name = "gemini-2.5-flash"
                extractor_mock.extract.return_value = ExtractionResult.model_validate_json(email_text)
                extractor_mock.generate_text.side_effect = generate_text
                result = graph.invoke(
                    {
                        "email_text": "Subject: normalized fixture",
                        "reference_date": "2026-05-23",
                        "timezone": "Asia/Seoul",
                        "llm_provider": "gemini",
                        "calendar_path": "data/calendars/synthetic_calendar_001.json",
                        "place_provider": "mock",
                    }
                )

        self.assertEqual(result["provider"], "gemini")
        self.assertEqual(result["llm_model"], "gemini-2.5-flash")
        self.assertEqual(result["recommendation"].status, "selected")
        self.assertEqual(
            result["recommendation"].selected.candidate.start,
            "2026-05-27T17:00:00+09:00",
        )

    def legacy_test_graph_fallback_normalizes_then_schedules(self) -> None:
        email_text = Path("data/samples/email_001.txt").read_text(encoding="utf-8")
        graph = build_extraction_graph()

        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": ""}, clear=False):
            result = graph.invoke(
                {
                    "email_text": email_text,
                    "reference_date": "2026-05-23",
                    "timezone": "Asia/Seoul",
                    "llm_provider": "gemini",
                }
            )

        self.assertEqual(result["provider"], "rule_fallback")
        self.assertEqual(result["extraction"].intent, "schedule_meeting")
        self.assertIn("GEMINI_API_KEY", result["error"])

    def legacy_test_graph_fallback_normalizes_then_schedules_full(self) -> None:
        email_text = Path("data/samples/email_001.txt").read_text(encoding="utf-8")
        graph = build_extraction_graph()

        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": ""}, clear=False):
            result = graph.invoke(
                {
                    "email_text": email_text,
                    "reference_date": "2026-05-23",
                    "timezone": "Asia/Seoul",
                    "llm_provider": "gemini",
                    "calendar_path": "data/calendars/synthetic_calendar_001.json",
                }
            )

        self.assertEqual(result["provider"], "rule_fallback")
        self.assertEqual(result["recommendation"].status, "selected")
        self.assertEqual(result["place_recommendation"].status, "selected")
        self.assertEqual(result["reservation_result"].status, "confirmed")
        self.assertEqual(result["reply_draft"].status, "ready")
        self.assertEqual(
            result["recommendation"].selected.candidate.start,
            "2026-05-27T17:00:00+09:00",
        )

    def legacy_test_graph_runs_scheduling_when_calendar_path_is_given(self) -> None:
        email_text = Path("data/samples/extraction_001.normalized.json").read_text(encoding="utf-8")
        graph = build_extraction_graph()

        with patch("src.email_agent.graph.parse_email_thread") as parse_mock:
            from src.email_agent.schema import EmailThread, ExtractionResult

            parse_mock.return_value = EmailThread(subject="normalized fixture", body="수요일 5시 회의")
            with patch("src.email_agent.graph.create_constraint_extractor") as factory_mock:
                extractor_mock = factory_mock.return_value
                extractor_mock.provider_name = "gemini"
                extractor_mock.model_name = "gemini-2.5-flash"
                extractor_mock.extract.return_value = ExtractionResult.model_validate_json(email_text)
                result = graph.invoke(
                    {
                        "email_text": "Subject: normalized fixture",
                        "reference_date": "2026-05-23",
                        "timezone": "Asia/Seoul",
                        "calendar_path": "data/calendars/synthetic_calendar_001.json",
                    }
                )

        self.assertEqual(result["provider"], "gemini")
        self.assertEqual(result["llm_model"], "gemini-2.5-flash")
        self.assertEqual(result["recommendation"].status, "selected")
        self.assertEqual(
            result["recommendation"].selected.candidate.start,
            "2026-05-27T17:00:00+09:00",
        )

    def test_graph_passes_qwen_provider_and_auto_reference_date(self) -> None:
        email_text = Path("data/samples/extraction_001.normalized.json").read_text(encoding="utf-8")
        graph = build_extraction_graph()

        with patch("src.email_agent.graph.parse_email_thread") as parse_mock:
            from src.email_agent.schema import EmailThread, ExtractionResult

            parse_mock.return_value = EmailThread(subject="fixture", body="Date: Tue, 26 May 2026 09:00:00 +0900")
            with patch("src.email_agent.graph.create_constraint_extractor") as factory_mock:
                extractor_mock = factory_mock.return_value
                extractor_mock.provider_name = "ollama"
                extractor_mock.model_name = "qwen3:4b"
                extractor_mock.infer_reference_date.return_value = "2026-05-26"
                extractor_mock.extract.return_value = ExtractionResult.model_validate_json(email_text)
                result = graph.invoke(
                    {
                        "email_text": "Subject: normalized fixture",
                        "reference_date": "auto",
                        "timezone": "Asia/Seoul",
                        "llm_provider": "qwen",
                        "llm_model": "qwen3:4b",
                    }
                )

        factory_mock.assert_any_call("qwen", model="qwen3:4b")
        extractor_mock.extract.assert_called_once()
        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["llm_model"], "qwen3:4b")
        self.assertEqual(result["reference_date"], "2026-05-26")
        self.assertEqual(result["reference_date_source"], "ollama_inferred")

    def test_selected_date_rewrites_candidate_dates(self) -> None:
        from src.email_agent.schema import ExtractionResult

        extraction = ExtractionResult.model_validate_json(
            Path("data/samples/extraction_001.normalized.json").read_text(encoding="utf-8")
        )

        adjusted = apply_selected_date_to_candidates(extraction, "2026-06-10")

        self.assertTrue(
            all(item.normalized_start.startswith("2026-06-10") for item in adjusted.candidate_times)
        )
        self.assertEqual(adjusted.candidate_times[0].normalized_start[-6:], "+09:00")

    def test_infer_place_search_query_matches_appointment_type(self) -> None:
        from src.email_agent.schema import ExtractionResult

        meal = ExtractionResult.model_validate(
            {
                "intent": "schedule_meeting",
                "participants": ["A"],
                "candidate_times": [],
                "unavailable_times": [],
                "location_preference": "강남역 근처",
                "confidence": 0.8,
                "source_summary": "점심 약속을 잡아야 합니다.",
            }
        )
        advising = meal.model_copy(update={"location_preference": "숭실대 근처", "source_summary": "교수님 면담 일정을 잡아야 합니다."})
        explicit_cafe = meal.model_copy(update={"location_preference": "숭실대 근처 카페", "source_summary": "팀플 회의입니다."})

        self.assertEqual(infer_place_search_query(meal), "강남역 식당 예약")
        self.assertEqual(infer_place_search_query(advising), "숭실대 조용한 회의실 예약")
        self.assertEqual(infer_place_search_query(explicit_cafe), "숭실대 카페 예약")

    def test_auto_reservation_target_uses_selected_place_url(self) -> None:
        from src.email_agent.schema import PlaceCandidate, PlaceRecommendation

        selected_url = (
            "https://map.naver.com/p/search/%ED%99%8D%EB%8C%80%20%EB%A7%9B%EC%A7%91"
            "%20%EC%84%9C%EC%9A%B8%20%EB%A7%88%ED%8F%AC%EA%B5%AC%20%EC%98%88%EC%95%BD"
        )
        state = {
            "reservation_target": "https://map.naver.com/p/search/%ED%99%8D%EB%8C%80%20%EC%8B%9D%EB%8B%B9%20%EC%98%88%EC%95%BD",
            "reservation_target_auto": True,
            "place_provider": "kakao",
            "place_recommendation": PlaceRecommendation(
                query="홍대 식당 예약",
                status="selected",
                selected=PlaceCandidate(name="홍대 맛집", source_url=selected_url),
            ),
        }

        self.assertEqual(resolve_reservation_target(state), selected_url)
        self.assertEqual(resolve_reservation_target_source(state), "kakao_selected_place")

    def test_align_location_with_place_query_replaces_conflicting_fallback_area(self) -> None:
        from src.email_agent.schema import ExtractionResult

        extraction = ExtractionResult.model_validate(
            {
                "intent": "schedule_meeting",
                "participants": ["주정인"],
                "candidate_times": [],
                "unavailable_times": [],
                "location_preference": "숭실대 식당 예약",
                "confidence": 0.9,
                "source_summary": "한양대 근처에서 만나고 밥 먹기",
            }
        )

        aligned = align_location_with_place_query(extraction, "한양대 근처 식당 예약")

        self.assertEqual(aligned.location_preference, "한양대 근처 식당 예약")

    def test_place_search_prompt_requires_korean_only_query(self) -> None:
        from src.email_agent.schema import EmailThread

        prompt = build_place_search_prompt(
            EmailThread(subject="회의", body="경희대에서 운영 회의를 합니다."),
            extraction_json="{}",
        )

        self.assertIn("natural Korean only", prompt)
        self.assertIn("meeting room", prompt)
        self.assertIn("경희대 회의실", prompt)
        self.assertIn("Do not include meta words", prompt)


if __name__ == "__main__":
    unittest.main()
