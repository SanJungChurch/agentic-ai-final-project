import unittest

from scripts.serve_html_demo import group_gmail_messages, split_appointment_contexts


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


if __name__ == "__main__":
    unittest.main()
