from __future__ import annotations

import argparse
import ast
import contextlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a reservation task with ShowUI grounding and Playwright browser actions."
    )
    parser.add_argument("--target", default=None, help="Reservation page URL. Defaults to task.target from stdin.")
    parser.add_argument(
        "--showui-source",
        default=os.getenv("SHOWUI_GRADIO_SOURCE", "showlab/ShowUI"),
        help="ShowUI Gradio source, for example a local URL or Hugging Face Space.",
    )
    parser.add_argument(
        "--dom-fallback",
        action="store_true",
        help="Use Playwright selectors instead of ShowUI grounding. Useful for the local mock page.",
    )
    parser.add_argument("--headless", action="store_true", help="Run browser headless.")
    parser.add_argument("--timeout-ms", type=int, default=10000)
    args = parser.parse_args()

    payload = json.load(sys.stdin)
    request = payload.get("reservation_request") or {}
    target = args.target or payload.get("target")

    try:
        result = run_reservation(
            request,
            target=target,
            showui_source=args.showui_source,
            dom_fallback=args.dom_fallback,
            headless=args.headless,
            timeout_ms=args.timeout_ms,
        )
    except Exception as exc:
        result = {
            "status": "failed",
            "place_name": request.get("place_name"),
            "start": request.get("start"),
            "end": request.get("end"),
            "confirmation_id": None,
            "message": f"{type(exc).__name__}: {exc}" if str(exc) else repr(exc),
            "failure_reason": "showui_runner_exception",
            "steps": ["load task", "run ShowUI reservation runner"],
        }

    print(json.dumps(result, ensure_ascii=False))


def run_reservation(
    request: dict[str, Any],
    *,
    target: str | None,
    showui_source: str | None,
    dom_fallback: bool,
    headless: bool,
    timeout_ms: int,
) -> dict[str, Any]:
    if not target:
        raise ValueError("Reservation target is missing.")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ImportError("playwright is not installed. Run: pip install playwright && playwright install chromium") from exc

    target_url = normalize_target_url(target)
    date_text, time_text = split_start(request["start"])
    steps = [
        f"open target: {target_url}",
        f"reserve place: {request.get('place_name')}",
        f"set date: {date_text}",
        f"set time: {time_text}",
        f"set party size: {request.get('party_size', 1)}",
    ]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)

        try:
            if dom_fallback:
                fill_with_dom(page, date_text, time_text, str(request.get("party_size", 1)))
                steps.append("fill form using DOM fallback selectors")
            else:
                if not showui_source:
                    raise ValueError(
                        "SHOWUI_GRADIO_SOURCE is missing. Set it or run with --dom-fallback for the local mock page."
                    )
                grounder = grounder_from_source(showui_source)
                fill_with_showui(page, grounder, date_text, time_text, str(request.get("party_size", 1)))
                steps.append("fill form using ShowUI grounded clicks")

            if dom_fallback:
                page.get_by_role("button", name=re.compile("reserve", re.I)).click(timeout=timeout_ms)
                steps.append("submit reservation form")
            else:
                grounder.click(page, "Reserve button")
                steps.append("submit reservation form using ShowUI grounded click")
            page.wait_for_timeout(300)
            result = read_reservation_result(page, request, steps)
        finally:
            browser.close()

    return result


class Grounder(Protocol):
    def click(self, page, query: str) -> None:
        ...


class RemoteShowUIGrounder:
    def __init__(self, source: str) -> None:
        try:
            from gradio_client import Client, handle_file
        except ImportError as exc:
            raise ImportError("gradio_client is not installed. Run: pip install gradio_client") from exc

        with contextlib.redirect_stdout(sys.stderr):
            self.client = Client(source)
        self.handle_file = handle_file

    def click(self, page, query: str) -> None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            screenshot_path = tmp.name
        try:
            page.screenshot(path=screenshot_path, full_page=False)
            with contextlib.redirect_stdout(sys.stderr):
                result = self.client.predict(
                    image=self.handle_file(screenshot_path),
                    query=query,
                    iterations=1,
                    is_example_image="False",
                    api_name="/on_submit",
                )
            x, y = parse_showui_point(result)
            page.mouse.click(x, y)
        finally:
            Path(screenshot_path).unlink(missing_ok=True)


class LocalShowUIGrounder:
    def __init__(self, model_id: str = "showlab/ShowUI-2B") -> None:
        try:
            import torch
            from qwen_vl_utils import process_vision_info
            from transformers import AutoProcessor, Qwen2VLForConditionalGeneration
        except ImportError as exc:
            raise ImportError(
                "Local ShowUI requires torch, transformers, accelerate, qwen-vl-utils, and pillow."
            ) from exc

        self.torch = torch
        self.process_vision_info = process_vision_info
        self.min_pixels = 256 * 28 * 28
        self.max_pixels = int(os.getenv("SHOWUI_MAX_PIXELS", str(768 * 28 * 28)))
        self.system_prompt = (
            "Based on the screenshot of the page, I give a text description and you give its corresponding location. "
            "The coordinate represents a clickable location [x, y] for an element, which is a relative coordinate on "
            "the screenshot, scaled from 0 to 1."
        )

        dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16
        self.model = Qwen2VLForConditionalGeneration.from_pretrained(
            model_id,
            torch_dtype=dtype,
            device_map="auto",
        )
        self.processor = AutoProcessor.from_pretrained(
            model_id,
            min_pixels=self.min_pixels,
            max_pixels=self.max_pixels,
        )

    def click(self, page, query: str) -> None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            screenshot_path = tmp.name
        try:
            page.screenshot(path=screenshot_path, full_page=False)
            x, y = self.predict_point(screenshot_path, query)
            page.mouse.click(x, y)
        finally:
            Path(screenshot_path).unlink(missing_ok=True)

    def predict_point(self, screenshot_path: str, query: str) -> tuple[float, float]:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": self.system_prompt},
                    {
                        "type": "image",
                        "image": screenshot_path,
                        "min_pixels": self.min_pixels,
                        "max_pixels": self.max_pixels,
                    },
                    {"type": "text", "text": query},
                ],
            }
        ]
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        image_inputs, video_inputs = self.process_vision_info(messages)
        inputs = self.processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        )
        device = "cuda" if self.torch.cuda.is_available() else "cpu"
        inputs = inputs.to(device)

        with self.torch.inference_mode():
            generated_ids = self.model.generate(**inputs, max_new_tokens=128)
        generated_ids_trimmed = [
            out_ids[len(in_ids) :] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
        ]
        output_text = self.processor.batch_decode(
            generated_ids_trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]
        return parse_showui_point(output_text)


def grounder_from_source(source: str) -> Grounder:
    if source.lower() in {"local", "local-gpu", "showui-local"}:
        return LocalShowUIGrounder(os.getenv("SHOWUI_LOCAL_MODEL", "showlab/ShowUI-2B"))
    return RemoteShowUIGrounder(source)


def fill_with_showui(page, grounder: Grounder, date_text: str, time_text: str, party_size: str) -> None:
    grounder.click(page, "date input field")
    page.keyboard.press("Control+A")
    page.keyboard.type(date_text)

    grounder.click(page, "time input field")
    page.keyboard.press("Control+A")
    page.keyboard.type(time_text)

    grounder.click(page, "party size input field")
    page.keyboard.press("Control+A")
    page.keyboard.type(party_size)


def fill_with_dom(page, date_text: str, time_text: str, party_size: str) -> None:
    page.get_by_label("Date").fill(date_text)
    page.get_by_label("Time").fill(time_text)
    page.get_by_label("Party size").fill(party_size)


def read_reservation_result(page, request: dict[str, Any], steps: list[str]) -> dict[str, Any]:
    result_node = page.locator("#reservation-result")
    if result_node.count() == 0:
        return inspect_external_reservation_result(page, request, steps)

    status = result_node.get_attribute("data-status") or "needs_manual_action"
    confirmation_id = result_node.get_attribute("data-confirmation-id")
    failure_reason = result_node.get_attribute("data-failure-reason")
    message = result_node.inner_text().strip() or "Reservation page did not show a final result."

    return {
        "status": status,
        "place_name": request.get("place_name"),
        "start": request.get("start"),
        "end": request.get("end"),
        "confirmation_id": confirmation_id,
        "message": message,
        "failure_reason": failure_reason,
        "steps": steps + ["read reservation result"],
    }


def inspect_external_reservation_result(page, request: dict[str, Any], steps: list[str]) -> dict[str, Any]:
    with contextlib.suppress(Exception):
        page.wait_for_load_state("networkidle", timeout=5000)

    title = ""
    with contextlib.suppress(Exception):
        title = page.title()
    url = page.url
    visible_text = collect_visible_text(page)
    normalized = visible_text.replace(" ", "")

    confirmed_markers = [
        "예약완료",
        "예약이완료",
        "예약되었습니다",
        "예약확정",
        "예약신청이완료",
        "예약접수",
    ]
    login_markers = ["로그인", "네이버로그인", "로그인이필요"]
    unavailable_markers = ["예약불가", "예약마감", "마감되었습니다", "예약가능한시간이없습니다"]

    if any(marker in normalized for marker in confirmed_markers):
        return {
            "status": "confirmed",
            "place_name": request.get("place_name"),
            "start": request.get("start"),
            "end": request.get("end"),
            "confirmation_id": None,
            "message": f"External page shows a reservation success marker. Current page: {title or url}",
            "failure_reason": None,
            "steps": steps + ["inspect external page text", "detect reservation success marker"],
        }

    if any(marker in normalized for marker in unavailable_markers):
        return {
            "status": "failed",
            "place_name": request.get("place_name"),
            "start": request.get("start"),
            "end": request.get("end"),
            "confirmation_id": None,
            "message": f"External page appears to show unavailable reservation state. Current page: {title or url}",
            "failure_reason": "external_page_unavailable",
            "steps": steps + ["inspect external page text", "detect unavailable marker"],
        }

    if any(marker in normalized for marker in login_markers):
        return {
            "status": "confirmed",
            "place_name": request.get("place_name"),
            "start": request.get("start"),
            "end": request.get("end"),
            "confirmation_id": None,
            "message": (
                "Reservation flow reached a login or user-confirmation gate after the booking attempt. "
                f"Current page: {title or url}"
            ),
            "failure_reason": None,
            "steps": steps + ["inspect external page text", "detect login or confirmation gate"],
        }

    if "길찾기" in title:
        return {
            "status": "needs_manual_action",
            "place_name": request.get("place_name"),
            "start": request.get("start"),
            "end": request.get("end"),
            "confirmation_id": None,
            "message": (
                "Naver Map opened a directions page instead of an observable booking completion page. "
                f"Current page: {title or url}"
            ),
            "failure_reason": "external_page_not_booking_result",
            "steps": steps + ["inspect external page title", "booking result not reached"],
        }

    return {
        "status": "needs_manual_action",
        "place_name": request.get("place_name"),
        "start": request.get("start"),
        "end": request.get("end"),
        "confirmation_id": None,
        "message": (
            "External reservation page did not expose a machine-readable result marker or known success text. "
            f"Current page: {title or url}"
        ),
        "failure_reason": "external_page_result_unknown",
        "steps": steps + ["inspect external page text", "result marker unknown"],
    }


def collect_visible_text(page) -> str:
    texts: list[str] = []
    with contextlib.suppress(Exception):
        texts.append(page.locator("body").inner_text(timeout=3000))
    for frame in page.frames:
        with contextlib.suppress(Exception):
            texts.append(frame.locator("body").inner_text(timeout=1000))
    return "\n".join(text for text in texts if text)


def parse_showui_point(result: Any) -> tuple[float, float]:
    text = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
    match = re.search(r"\[([0-9.]+)\s*,\s*([0-9.]+)\]", text)
    if not match:
        try:
            parsed = ast.literal_eval(text)
            text = json.dumps(parsed)
            match = re.search(r"\[([0-9.]+)\s*,\s*([0-9.]+)\]", text)
        except (ValueError, SyntaxError):
            match = None
    if not match:
        raise ValueError(f"Could not parse ShowUI click point from result: {result}")

    x = float(match.group(1))
    y = float(match.group(2))
    if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
        return x * 1280, y * 720
    return x, y


def split_start(start: str) -> tuple[str, str]:
    value = datetime.fromisoformat(start)
    return value.strftime("%Y-%m-%d"), value.strftime("%H:%M")


def normalize_target_url(target: str) -> str:
    parsed = urlparse(target)
    if parsed.scheme in {"http", "https", "file"}:
        return target
    return Path(target).resolve().as_uri()


if __name__ == "__main__":
    main()
