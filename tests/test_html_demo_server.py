import unittest

from scripts.serve_html_demo import (
    answer_chat,
    group_gmail_messages,
    infer_location_query,
    infer_naver_url,
    place_from_reservation_target,
    split_appointment_contexts,
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

    def test_place_from_reservation_target_uses_naver_url(self) -> None:
        url = (
            "https://map.naver.com/p/search/%EC%88%AD%EC%8B%A4%EB%8C%80%20%EC%B9%B4%ED%8E%98/"
            "place/1649187599?placePath=/booking&searchText=%EC%88%AD%EC%8B%A4%EB%8C%80%20%EC%B9%B4%ED%8E%98"
        )

        place = place_from_reservation_target(url, "숭실대 근처 카페")

        self.assertEqual(place.source_url, url)
        self.assertIn("숭실대 카페", place.name)
        self.assertEqual(place.category, "naver_booking")

    def test_infer_naver_url_uses_venue_type_from_email(self) -> None:
        text = "숭실대 근처 음식점에서 점심 약속을 잡아줘"

        self.assertIn("%EC%8B%9D%EB%8B%B9", infer_naver_url(text))
        self.assertEqual(infer_location_query(text), "숭실대 식당 예약")

    def test_chat_answers_reservation_failure_from_context_without_llm(self) -> None:
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

        self.assertEqual(answer["source"], "context")
        self.assertIn("실패했습니다", answer["answer"])
        self.assertIn("slot_unavailable", answer["answer"])


if __name__ == "__main__":
    unittest.main()
