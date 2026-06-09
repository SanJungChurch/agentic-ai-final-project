import unittest
from unittest.mock import patch

from scripts.serve_html_demo import (
    DEFAULT_NAVER_BOOKING_URL,
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
            extractor.generate_text.return_value = "EXAONE answer"
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


if __name__ == "__main__":
    unittest.main()
