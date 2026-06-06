import unittest
from unittest.mock import Mock, patch

from src.email_agent.reservation_executor import (
    ShowUIReservationExecutor,
    StaticHtmlReservationExecutor,
    build_showui_task_prompt,
    build_reservation_request,
    reserve_selected_place,
    _loads_runner_json,
)
from src.email_agent.schema import (
    CandidateDecision,
    ExtractionResult,
    Intent,
    PlaceCandidate,
    PlaceRecommendation,
    TimeCandidate,
    ScheduleRecommendation,
)


class ReservationExecutorTest(unittest.TestCase):
    def test_build_reservation_request_uses_selected_time_and_place(self) -> None:
        extraction, recommendation, place_recommendation = _fixtures()

        request = build_reservation_request(extraction, recommendation, place_recommendation)

        self.assertIsNotNone(request)
        self.assertEqual(request.place_name, "Cafe On The Hill")
        self.assertEqual(request.start, "2026-05-27T17:00:00+09:00")
        self.assertEqual(request.party_size, 2)

    def test_static_html_executor_confirms_matching_slot(self) -> None:
        extraction, recommendation, place_recommendation = _fixtures()
        executor = StaticHtmlReservationExecutor("data/reservation/mock_reservation.html")

        result = reserve_selected_place(extraction, recommendation, place_recommendation, executor=executor)

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.confirmation_id, "HTML-20260527-1700")
        self.assertIn("submit reservation form", result.steps)

    def test_static_html_executor_fails_missing_slot(self) -> None:
        extraction, recommendation, place_recommendation = _fixtures(start="2026-05-30T17:00:00+09:00")
        executor = StaticHtmlReservationExecutor("data/reservation/mock_reservation.html")

        result = reserve_selected_place(extraction, recommendation, place_recommendation, executor=executor)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_reason, "slot_not_found")

    def test_showui_executor_returns_manual_task_without_runner(self) -> None:
        extraction, recommendation, place_recommendation = _fixtures()
        executor = ShowUIReservationExecutor(target="file:///mock_reservation.html", runner_command=None)

        result = reserve_selected_place(extraction, recommendation, place_recommendation, executor=executor)

        self.assertEqual(result.status, "needs_manual_action")
        self.assertEqual(result.failure_reason, "showui_runner_not_configured")
        self.assertIn("You are a GUI reservation agent using ShowUI.", result.message)
        self.assertIn("Cafe On The Hill", result.message)

    def test_showui_executor_accepts_runner_json_output(self) -> None:
        extraction, recommendation, place_recommendation = _fixtures()
        runner_output = (
            '{"status":"confirmed","place_name":"Cafe On The Hill",'
            '"start":"2026-05-27T17:00:00+09:00","end":"2026-05-27T18:00:00+09:00",'
            '"confirmation_id":"SHOWUI-1","message":"confirmed by ShowUI","steps":["click reserve"]}'
        )
        completed = Mock(returncode=0, stdout=runner_output, stderr="")
        executor = ShowUIReservationExecutor(
            target="file:///mock_reservation.html",
            runner_command=["showui-runner"],
        )

        with patch("subprocess.run", return_value=completed) as run_mock:
            result = reserve_selected_place(extraction, recommendation, place_recommendation, executor=executor)

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.confirmation_id, "SHOWUI-1")
        payload = run_mock.call_args.kwargs["input"]
        self.assertIn("reserve_place", payload)
        self.assertIn("Cafe On The Hill", payload)

    def test_build_showui_task_prompt_contains_reservation_goal(self) -> None:
        extraction, recommendation, place_recommendation = _fixtures()
        request = build_reservation_request(extraction, recommendation, place_recommendation)

        prompt = build_showui_task_prompt(request, target="https://example.com/reserve")

        self.assertIn("https://example.com/reserve", prompt)
        self.assertIn("Reserve place: Cafe On The Hill", prompt)
        self.assertIn("Start time: 2026-05-27T17:00:00+09:00", prompt)

    def test_runner_json_loader_ignores_stdout_logs(self) -> None:
        output = 'Loaded as API: https://showlab-showui.hf.space\n{"status":"confirmed","message":"ok"}\n'

        data = _loads_runner_json(output)

        self.assertEqual(data["status"], "confirmed")
        self.assertEqual(data["message"], "ok")


def _fixtures(start: str = "2026-05-27T17:00:00+09:00"):
    extraction = ExtractionResult(
        intent=Intent.SCHEDULE_MEETING,
        participants=["A", "B"],
        location_preference="near campus cafe",
    )
    recommendation = ScheduleRecommendation(
        status="selected",
        selected=CandidateDecision(
            candidate=TimeCandidate(
                source_expression="Wednesday 5pm",
                start=start,
                end="2026-05-27T18:00:00+09:00",
                available_participants=["A", "B"],
            ),
            valid=True,
            score=10,
        ),
    )
    place_recommendation = PlaceRecommendation(
        query="near campus cafe",
        status="selected",
        selected=PlaceCandidate(name="Cafe On The Hill", category="cafe"),
    )
    return extraction, recommendation, place_recommendation


if __name__ == "__main__":
    unittest.main()
