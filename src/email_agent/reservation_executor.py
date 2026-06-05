from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Sequence, Protocol

from dotenv import load_dotenv

from .schema import (
    ExtractionResult,
    PlaceRecommendation,
    ReservationRequest,
    ReservationResult,
    ScheduleRecommendation,
)


class ReservationExecutor(Protocol):
    def reserve(self, request: ReservationRequest) -> ReservationResult:
        ...


class MockReservationExecutor:
    def reserve(self, request: ReservationRequest) -> ReservationResult:
        return ReservationResult(
            status="confirmed",
            place_name=request.place_name,
            start=request.start,
            end=request.end,
            confirmation_id=_confirmation_id("MOCK", request.start),
            message="Mock reservation confirmed.",
            steps=[
                "open reservation page",
                "fill date and time",
                "fill party size",
                "submit reservation form",
                "read confirmation message",
            ],
        )


class StaticHtmlReservationExecutor:
    """Reservation executor for a local mock HTML booking page.

    The MVP does not book a real venue. It parses a controlled HTML page that
    represents visible booking slots and returns the same success/failure state
    a GUI executor should detect after operating a real website.
    """

    def __init__(self, html_path: str | Path) -> None:
        self.html_path = Path(html_path)

    def reserve(self, request: ReservationRequest) -> ReservationResult:
        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise ImportError("beautifulsoup4 is not installed. Run: pip install beautifulsoup4") from exc

        soup = BeautifulSoup(self.html_path.read_text(encoding="utf-8"), "html.parser")
        steps = [
            f"open {self.html_path}",
            f"select place: {request.place_name}",
            f"select time: {request.start}",
            f"set party size: {request.party_size}",
        ]

        for slot in soup.select("[data-slot]"):
            place = _attr(slot, "data-place")
            start = _attr(slot, "data-start")
            end = _attr(slot, "data-end")
            available = _attr(slot, "data-available").lower() == "true"

            if not _matches_slot(request, place, start):
                continue

            if not available:
                return ReservationResult(
                    status="failed",
                    place_name=place or request.place_name,
                    start=start or request.start,
                    end=end or request.end,
                    message="Reservation slot exists but is unavailable.",
                    failure_reason="slot_unavailable",
                    steps=steps + ["submit reservation form", "read unavailable message"],
                )

            confirmation_id = _attr(slot, "data-confirmation-id") or _confirmation_id("HTML", request.start)
            return ReservationResult(
                status="confirmed",
                place_name=place or request.place_name,
                start=start or request.start,
                end=end or request.end,
                confirmation_id=confirmation_id,
                message="Static HTML reservation confirmed.",
                steps=steps + ["submit reservation form", "read confirmation message"],
            )

        return ReservationResult(
            status="failed",
            place_name=request.place_name,
            start=request.start,
            end=request.end,
            message="No matching reservation slot was found.",
            failure_reason="slot_not_found",
            steps=steps + ["scan available slots"],
        )


class ShowUIReservationExecutor:
    """Adapter for delegating reservation work to an external ShowUI runner.

    ShowUI is a visual GUI agent rather than a simple HTML parser. This adapter
    prepares the task payload and optionally calls a user-provided runner command.
    The runner should read JSON from stdin and print a ReservationResult-shaped
    JSON object to stdout.
    """

    def __init__(
        self,
        *,
        target: str | None = None,
        runner_command: str | Sequence[str] | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        load_dotenv()
        self.target = target or os.getenv("SHOWUI_RESERVATION_TARGET")
        self.runner_command = runner_command or os.getenv("SHOWUI_RUNNER_COMMAND")
        self.timeout_seconds = timeout_seconds

    def reserve(self, request: ReservationRequest) -> ReservationResult:
        prompt = build_showui_task_prompt(request, target=self.target)
        payload = {
            "task": "reserve_place",
            "target": self.target,
            "prompt": prompt,
            "reservation_request": request.model_dump(),
            "expected_output_schema": "ReservationResult",
        }

        if not self.runner_command:
            return ReservationResult(
                status="needs_manual_action",
                place_name=request.place_name,
                start=request.start,
                end=request.end,
                message=prompt,
                failure_reason="showui_runner_not_configured",
                steps=[
                    "build ShowUI reservation task",
                    "waiting for SHOWUI_RUNNER_COMMAND",
                ],
            )

        completed = subprocess.run(
            _command_args(self.runner_command),
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )

        if completed.returncode != 0:
            return ReservationResult(
                status="failed",
                place_name=request.place_name,
                start=request.start,
                end=request.end,
                message=completed.stderr.strip() or completed.stdout.strip() or "ShowUI runner failed.",
                failure_reason="showui_runner_failed",
                steps=["build ShowUI reservation task", "run ShowUI runner"],
            )

        try:
            data = _loads_runner_json(completed.stdout)
            result = ReservationResult.model_validate(data)
        except (json.JSONDecodeError, ValueError) as exc:
            return ReservationResult(
                status="failed",
                place_name=request.place_name,
                start=request.start,
                end=request.end,
                message=f"ShowUI runner returned invalid JSON: {exc}",
                failure_reason="showui_runner_invalid_output",
                steps=["build ShowUI reservation task", "run ShowUI runner", "parse runner output"],
            )

        if not result.steps:
            result = result.model_copy(update={"steps": ["build ShowUI reservation task", "run ShowUI runner"]})
        return result


def reserve_selected_place(
    extraction: ExtractionResult,
    recommendation: ScheduleRecommendation | None,
    place_recommendation: PlaceRecommendation | None,
    *,
    executor: ReservationExecutor | None = None,
) -> ReservationResult:
    request = build_reservation_request(extraction, recommendation, place_recommendation)
    if not request:
        return ReservationResult(
            status="skipped",
            message="Reservation skipped because selected time or place is missing.",
            failure_reason="missing_time_or_place",
        )

    reservation_executor = executor or MockReservationExecutor()
    return reservation_executor.reserve(request)


def build_reservation_request(
    extraction: ExtractionResult,
    recommendation: ScheduleRecommendation | None,
    place_recommendation: PlaceRecommendation | None,
) -> ReservationRequest | None:
    if not recommendation or not recommendation.selected:
        return None
    if not place_recommendation or not place_recommendation.selected:
        return None

    candidate = recommendation.selected.candidate
    place = place_recommendation.selected
    party_size = max(1, len([participant for participant in extraction.participants if participant]))

    return ReservationRequest(
        place_name=place.name,
        start=candidate.start,
        end=candidate.end,
        party_size=party_size,
        note=extraction.source_summary or None,
    )


def build_showui_task_prompt(request: ReservationRequest, *, target: str | None = None) -> str:
    target_line = f"Open and operate this reservation target: {target}." if target else "Open the reservation page."
    return (
        "You are a GUI reservation agent using ShowUI.\n"
        f"{target_line}\n"
        f"Reserve place: {request.place_name}\n"
        f"Start time: {request.start}\n"
        f"End time: {request.end}\n"
        f"Party size: {request.party_size}\n"
        f"Customer name: {request.customer_name}\n"
        "Use visible UI controls only. Fill the reservation form, submit it, and judge the result from the visible confirmation or error message. "
        "Return JSON with status, place_name, start, end, confirmation_id, message, failure_reason, and steps."
    )


def executor_from_name(
    name: str,
    *,
    html_path: str | None = None,
    target: str | None = None,
    runner_command: str | Sequence[str] | None = None,
) -> ReservationExecutor:
    if name == "mock":
        return MockReservationExecutor()
    if name == "html":
        if not html_path:
            raise ValueError("--reservation-html is required when --reservation-provider html is used.")
        return StaticHtmlReservationExecutor(html_path)
    if name == "showui":
        return ShowUIReservationExecutor(target=target or html_path, runner_command=runner_command)
    raise ValueError(f"Unknown reservation provider: {name}")


def _command_args(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command, posix=os.name != "nt")
    return list(command)


def _loads_runner_json(output: str) -> dict:
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pass

    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("No JSON object found in ShowUI runner output", output, 0)


def _matches_slot(request: ReservationRequest, place: str, start: str) -> bool:
    if start != request.start:
        return False
    if not place:
        return True
    return place == request.place_name or place.lower() in request.place_name.lower()


def _attr(node, name: str) -> str:
    value = node.get(name)
    return str(value).strip() if value else ""


def _confirmation_id(prefix: str, start: str) -> str:
    try:
        value = datetime.fromisoformat(start)
        return f"{prefix}-{value:%Y%m%d%H%M}"
    except ValueError:
        safe = "".join(ch for ch in start if ch.isalnum())[:16]
        return f"{prefix}-{safe or 'reservation'}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a reservation executor for the selected schedule/place.")
    parser.add_argument("--extraction", required=True)
    parser.add_argument("--recommendation", required=True)
    parser.add_argument("--place-recommendation", required=True)
    parser.add_argument("--reservation-provider", choices=["mock", "html", "showui"], default="mock")
    parser.add_argument("--reservation-html", default=None)
    parser.add_argument("--reservation-target", default=None)
    parser.add_argument("--showui-runner-command", default=None)
    args = parser.parse_args()

    extraction = ExtractionResult.model_validate_json(Path(args.extraction).read_text(encoding="utf-8"))
    recommendation = ScheduleRecommendation.model_validate_json(Path(args.recommendation).read_text(encoding="utf-8"))
    place_recommendation = PlaceRecommendation.model_validate_json(
        Path(args.place_recommendation).read_text(encoding="utf-8")
    )

    executor = executor_from_name(
        args.reservation_provider,
        html_path=args.reservation_html,
        target=args.reservation_target,
        runner_command=args.showui_runner_command,
    )
    result = reserve_selected_place(extraction, recommendation, place_recommendation, executor=executor)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
