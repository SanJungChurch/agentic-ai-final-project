import unittest
from unittest.mock import patch

from scripts.serve_html_demo import (
    DEFAULT_NAVER_BOOKING_URL,
    _clean_chat_tool_query,
    answer_chat,
    group_gmail_messages,
    split_appointment_contexts,
    sync_reservation_target_from_place,
)


class HtmlDemoServerTest(unittest.TestCase):
    def test_group_gmail_messages_uses_thread_id(self) -> None:
        groups = group_gmail_messages(
            [
                {
                    "message_id": "m1",
                    "thread_id": "t1",
                    "subject": "Re: 팀플 회의",
                    "date": "Tue, 26 May 2026 09:00:00 +0900",
                    "email_text": "Subject: 팀플 회의\n\n수요일 가능",
                    "schedule_score": 3,
                },
                {
                    "message_id": "m2",
                    "thread_id": "t1",
                    "subject": "팀플 회의",
                    "date": "Tue, 26 May 2026 10:00:00 +0900",
                    "email_text": "Subject: 팀플 회의\n\n목요일은 불가",
                    "schedule_score": 4,
                },
            ]
        )

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(groups[0]["message_ids"], ["m1", "m2"])
        self.assertIn("same appointment thread", groups[0]["email_text"])

    def test_split_appointment_contexts_uses_manual_separator(self) -> None:
        contexts = split_appointment_contexts("Subject: A\n\n수요일 회의\n\n---\n\nSubject: B\n\n금요일 면담")

        self.assertEqual(len(contexts), 2)
        self.assertEqual(contexts[0]["title"], "A")
        self.assertEqual(contexts[1]["title"], "B")

    def test_default_reservation_target_is_generic_until_agent_runs(self) -> None:
        self.assertIn("%EC%98%88%EC%95%BD%20%EA%B0%80%EB%8A%A5", DEFAULT_NAVER_BOOKING_URL)
        self.assertNotIn("%EC%88%AD%EC%8B%A4%EB%8C%80", DEFAULT_NAVER_BOOKING_URL)
        self.assertNotIn("%EB%AA%85%EC%A7%80%EB%8C%80", DEFAULT_NAVER_BOOKING_URL)

    def test_sync_reservation_target_prefers_kakao_selected_place_url(self) -> None:
        from src.email_agent.schema import PlaceCandidate, PlaceRecommendation

        initial_target = "https://map.naver.com/p/search/%ED%99%8D%EB%8C%80%20%EC%8B%9D%EB%8B%B9%20%EC%98%88%EC%95%BD"
        kakao_selected_url = (
            "https://map.naver.com/p/search/%EC%84%A0%ED%83%9D%EB%90%9C%20%ED%99%8D%EB%8C%80%20%EC%8B%9D%EB%8B%B9"
            "%20%EC%84%9C%EC%9A%B8%20%EB%A7%88%ED%8F%AC%EA%B5%AC%20%EC%98%88%EC%95%BD"
        )
        graph_result = {
            "reservation_target": initial_target,
            "place_recommendation": PlaceRecommendation(
                query="홍대 식당 예약",
                status="selected",
                selected=PlaceCandidate(
                    name="선택된 홍대 식당",
                    address="서울 마포구",
                    source_url=kakao_selected_url,
                ),
            ),
        }

        updated, target = sync_reservation_target_from_place(
            graph_result,
            initial_target,
            True,
            "kakao",
        )

        self.assertEqual(target, kakao_selected_url)
        self.assertEqual(updated["reservation_target"], kakao_selected_url)
        self.assertEqual(updated["reservation_target_source"], "kakao_selected_place")

    def test_chat_uses_exaone_response_without_context_fallback(self) -> None:
        with patch("scripts.serve_html_demo.create_constraint_extractor") as factory:
            extractor = factory.return_value
            extractor.provider_name = "exaone"
            extractor.model_name = "LGAI-EXAONE/EXAONE-4.0-1.2B"
            extractor.generate_text.side_effect = [
                '{"action": "answer", "query": null, "reason": "context question"}',
                "EXAONE answer",
            ]
            answer = answer_chat(
                {
                    "question": "reservation status?",
                    "llm_provider": "qwen",
                    "context": {
                        "reservation_result": {
                            "status": "failed",
                            "failure_reason": "slot_unavailable",
                            "message": "No matching reservation slot was found.",
                        },
                        "extraction": {"participants": ["A", "B"]},
                    },
                }
            )

        factory.assert_called_once()
        self.assertEqual(answer["source"], "exaone")
        self.assertEqual(answer["answer"], "EXAONE answer")
        self.assertEqual(answer["tool_calls"], [])

    def test_chat_can_call_kakao_place_search_tool(self) -> None:
        from src.email_agent.schema import PlaceCandidate, PlaceRecommendation

        with patch("scripts.serve_html_demo.create_constraint_extractor") as factory:
            extractor = factory.return_value
            extractor.provider_name = "exaone"
            extractor.model_name = "LGAI-EXAONE/EXAONE-4.0-1.2B"
            extractor.generate_text.side_effect = [
                '{"action": "search_place", "query": "경희대 식당", "reason": "저녁 식사 장소 검색"}',
                "경희대 근처 식당 후보를 찾았습니다.",
            ]
            with patch("scripts.serve_html_demo.provider_from_name") as provider_factory:
                provider_factory.return_value = object()
                with patch("scripts.serve_html_demo.recommend_place") as recommend_mock:
                    recommend_mock.return_value = PlaceRecommendation(
                        query="경희대 식당",
                        status="selected",
                        selected=PlaceCandidate(
                            name="경희대 식당 후보",
                            address="서울 동대문구",
                            category="음식점",
                            source_url="https://map.naver.com/p/search/test",
                            score=2.0,
                        ),
                        candidates=[
                            PlaceCandidate(
                                name="경희대 식당 후보",
                                address="서울 동대문구",
                                category="음식점",
                                source_url="https://map.naver.com/p/search/test",
                                score=2.0,
                            )
                        ],
                    )
                    answer = answer_chat(
                        {
                            "question": "회의 끝나고 저녁 식사 장소도 찾아줘",
                            "context": {
                                "extraction": {"location_preference": "경희대"},
                                "reservation_result": {"status": "confirmed"},
                            },
                        }
                    )

        provider_factory.assert_called_once_with("kakao")
        recommend_mock.assert_called_once()
        self.assertEqual(answer["tool_calls"][0]["action"], "search_place")
        self.assertEqual(answer["tool_result"]["status"], "selected")
        self.assertEqual(answer["tool_result"]["selected"]["name"], "경희대 식당 후보")
        self.assertIn("식당 후보", answer["answer"])

    def test_chat_place_query_removes_meal_time_noise(self) -> None:
        self.assertEqual(_clean_chat_tool_query("인하대학교 저녁 식당"), "인하대학교 식당")
        self.assertEqual(_clean_chat_tool_query("인하대학교 근처 예약 가능한 식당 검색"), "인하대학교 식당")

    def test_chat_final_answer_unwraps_json_code_block(self) -> None:
        with patch("scripts.serve_html_demo.create_constraint_extractor") as factory:
            extractor = factory.return_value
            extractor.provider_name = "exaone"
            extractor.model_name = "LGAI-EXAONE/EXAONE-4.0-1.2B"
            extractor.generate_text.side_effect = [
                '{"action": "answer", "query": null, "reason": "context question"}',
                '```json\n{"response": "예약은 아직 완료되지 않았고 직접 확인이 필요합니다."}\n```',
            ]
            answer = answer_chat(
                {
                    "question": "예약됐어?",
                    "context": {
                        "reservation_result": {
                            "status": "needs_manual_action",
                            "message": "External page requires user confirmation.",
                        },
                    },
                }
            )

        self.assertEqual(answer["answer"], "예약은 아직 완료되지 않았고 직접 확인이 필요합니다.")
        self.assertNotIn("```", answer["answer"])

    def test_chat_reserves_place_from_previous_kakao_search(self) -> None:
        from src.email_agent.schema import ReservationResult

        context = {
            "extraction": {
                "intent": "schedule_meeting",
                "participants": ["김동욱", "주정인"],
                "candidate_times": [],
                "unavailable_times": [],
                "location_preference": "수원대학교",
                "meeting_duration_minutes": 60,
                "missing_information": [],
                "confidence": 0.9,
                "source_summary": "수원대학교 근처 회의",
            },
            "recommendation": {
                "selected": {
                    "candidate": {
                        "source_expression": "15시",
                        "start": "2026-06-11T15:00:00+09:00",
                        "end": "2026-06-11T16:00:00+09:00",
                        "available_participants": [],
                        "preferred_by": [],
                    },
                    "valid": True,
                    "score": 10,
                    "hard_violations": [],
                    "reasons": [],
                    "conflicts": [],
                },
                "candidates": [],
                "status": "selected",
                "summary": "15시 선택",
            },
            "place_recommendation": {
                "query": "수원대학교 회의실",
                "candidates": [],
                "selected": {
                    "name": "브레인온 스터디카페 화성봉담점",
                    "address": "경기 화성시",
                    "category": "스터디카페",
                    "source_url": "https://map.naver.com/p/search/study",
                    "score": 1.0,
                },
                "status": "selected",
                "summary": "기존 agent 장소",
            },
            "chat_tool_result": {
                "tool": "search_place",
                "query": "수원대학교 식당",
                "status": "selected",
                "selected": {
                    "name": "버거킹 화성봉담점",
                    "address": "경기 화성시 봉담읍",
                    "category": "음식점",
                    "source_url": "https://map.naver.com/p/search/burgerking",
                    "score": 2.0,
                },
                "candidates": [],
            },
        }

        with patch("scripts.serve_html_demo.create_constraint_extractor") as factory:
            extractor = factory.return_value
            extractor.provider_name = "exaone"
            extractor.model_name = "LGAI-EXAONE/EXAONE-4.0-1.2B"
            extractor.generate_text.side_effect = [
                '{"action": "reserve_place", "query": null, "reason": "방금 찾은 식당 예약"}',
                "버거킹 화성봉담점 예약을 시도했고 확인 단계에 도달했습니다.",
            ]
            with patch("scripts.serve_html_demo.executor_from_name") as executor_factory:
                executor_factory.return_value = object()
                with patch("scripts.serve_html_demo.reserve_selected_place") as reserve_mock:
                    reserve_mock.return_value = ReservationResult(
                        status="needs_manual_action",
                        place_name="버거킹 화성봉담점",
                        start="2026-06-11T15:00:00+09:00",
                        end="2026-06-11T16:00:00+09:00",
                        message="External page requires user confirmation.",
                    )
                    answer = answer_chat(
                        {
                            "question": "식당 예약도 진행해줘",
                            "executor": "naver-headless",
                            "context": context,
                        }
                    )

        executor_factory.assert_called_once()
        self.assertIn("burgerking", executor_factory.call_args.kwargs["target"])
        reserve_mock.assert_called_once()
        place_recommendation = reserve_mock.call_args.args[2]
        self.assertEqual(place_recommendation.selected.name, "버거킹 화성봉담점")
        self.assertEqual(answer["tool_calls"][0]["action"], "reserve_place")
        self.assertEqual(answer["tool_result"]["reservation_result"]["status"], "needs_manual_action")


if __name__ == "__main__":
    unittest.main()
