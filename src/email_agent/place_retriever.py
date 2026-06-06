from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv

from .schema import ExtractionResult, PlaceCandidate, PlaceRecommendation, ScheduleRecommendation


KAKAO_KEYWORD_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


class PlaceProvider(Protocol):
    def search(self, query: str, *, location_hint: str | None = None) -> list[PlaceCandidate]:
        ...


class MockPlaceProvider:
    def search(self, query: str, *, location_hint: str | None = None) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                name="카페 온더힐",
                address="서울 동작구 상도로 369 숭실대입구역 근처",
                category="cafe",
                rating=4.6,
                source_url="https://example.com/on-the-hill",
                availability_hint="예약 가능 여부 확인 필요",
            ),
            PlaceCandidate(
                name="스터디룸 숭실",
                address="서울 동작구 상도로 360",
                category="study room",
                rating=4.4,
                source_url="https://example.com/study-soongsil",
                availability_hint="평일 저녁 예약 가능",
            ),
            PlaceCandidate(
                name="커피랩 상도",
                address="서울 동작구 상도로 350",
                category="cafe",
                rating=4.2,
                source_url="https://example.com/coffee-lab",
                availability_hint="좌석 확인 필요",
            ),
        ]


class StaticHtmlPlaceProvider:
    def __init__(self, html_path: str | Path) -> None:
        self.html_path = Path(html_path)

    def search(self, query: str, *, location_hint: str | None = None) -> list[PlaceCandidate]:
        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise ImportError("beautifulsoup4 is not installed. Run: pip install beautifulsoup4") from exc

        soup = BeautifulSoup(self.html_path.read_text(encoding="utf-8"), "html.parser")
        candidates = []
        for card in soup.select(".place-card"):
            name = _text(card, ".name")
            if not name:
                continue
            candidates.append(
                PlaceCandidate(
                    name=name,
                    category=_text(card, ".category"),
                    address=_text(card, ".address"),
                    rating=_float_or_none(_text(card, ".rating")),
                    source_url=_href(card, ".source"),
                    availability_hint=_text(card, ".availability"),
                )
            )
        return candidates


class KakaoLocalPlaceProvider:
    """Kakao Local keyword search provider.

    Requires `KAKAO_REST_API_KEY` in `.env` or the process environment.
    Official docs: https://developers.kakao.com/docs/latest/en/local/dev-guide
    """

    def __init__(self, api_key: str | None = None, *, size: int = 10) -> None:
        load_dotenv()
        self.api_key = api_key or os.getenv("KAKAO_REST_API_KEY")
        self.size = size
        if not self.api_key:
            raise ValueError("KAKAO_REST_API_KEY is missing. Add it to .env or set the environment variable.")

    def search(self, query: str, *, location_hint: str | None = None) -> list[PlaceCandidate]:
        params = {
            "query": query,
            "size": str(self.size),
        }
        url = KAKAO_KEYWORD_SEARCH_URL + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"KakaoAK {self.api_key}"},
            method="GET",
        )

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Kakao Local API request failed with HTTP {exc.code}: {body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Kakao Local API request failed: {exc.reason}") from exc

        return [self._from_document(item) for item in payload.get("documents", [])]

    def _from_document(self, item: dict[str, Any]) -> PlaceCandidate:
        category = item.get("category_group_name") or item.get("category_name")
        address = item.get("road_address_name") or item.get("address_name")
        return PlaceCandidate(
            name=item.get("place_name", ""),
            address=address,
            category=category,
            rating=None,
            source_url=item.get("place_url"),
            availability_hint="실시간 예약 가능 여부 확인 필요",
        )


def recommend_place(
    extraction: ExtractionResult,
    recommendation: ScheduleRecommendation | None = None,
    *,
    provider: PlaceProvider | None = None,
) -> PlaceRecommendation:
    query = build_place_query(extraction)
    if not query:
        return PlaceRecommendation(query="", status="no_query", summary="장소 검색 조건이 없습니다.")

    place_provider = provider or MockPlaceProvider()
    candidates = place_provider.search(query, location_hint=extraction.location_preference)
    reservable_candidates = [candidate for candidate in candidates if candidate.name and _is_reservation_candidate(candidate)]
    scored = [_score_place(candidate, query) for candidate in reservable_candidates]
    scored.sort(key=lambda item: item.score, reverse=True)
    selected = scored[0] if scored else None

    if not selected:
        return PlaceRecommendation(
            query=query,
            candidates=[],
            selected=None,
            status="no_candidates",
            summary="장소 후보를 찾지 못했습니다.",
        )

    return PlaceRecommendation(
        query=query,
        candidates=scored,
        selected=selected,
        status="selected",
        summary=f"{selected.name} 후보를 선택했습니다.",
    )


def build_place_query(extraction: ExtractionResult) -> str:
    preference = (extraction.location_preference or "").strip()
    if not preference:
        return ""

    cleaned = preference.rstrip(".")
    cleaned = cleaned.removeprefix("장소는").strip()
    return cleaned


def provider_from_name(name: str, *, html_path: str | None = None) -> PlaceProvider:
    if name == "mock":
        return MockPlaceProvider()
    if name == "html":
        if not html_path:
            raise ValueError("--html is required when --provider html is used.")
        return StaticHtmlPlaceProvider(html_path)
    if name == "kakao":
        return KakaoLocalPlaceProvider()
    raise ValueError(f"Unknown provider: {name}")


def _score_place(candidate: PlaceCandidate, query: str) -> PlaceCandidate:
    score = 0.0
    query_lower = query.lower()
    searchable = " ".join(
        value
        for value in [candidate.name, candidate.category, candidate.address, candidate.availability_hint]
        if value
    ).lower()

    for token in query_lower.split():
        if token and token in searchable:
            score += 1.0

    if candidate.rating:
        score += candidate.rating / 5.0
    if "예약 가능" in (candidate.availability_hint or ""):
        score += 1.0
    if "카페" in query and candidate.category and "카페" in candidate.category:
        score += 1.0
    if "카페" in query and candidate.category == "cafe":
        score += 1.0

    return candidate.model_copy(update={"score": round(score, 4)})


def _is_reservation_candidate(candidate: PlaceCandidate) -> bool:
    hint = (candidate.availability_hint or "").strip().lower()
    if not hint:
        return True

    unavailable_markers = [
        "예약 불가",
        "예약불가",
        "예약 마감",
        "예약마감",
        "예약 종료",
        "예약종료",
        "예약 불가능",
        "예약불가능",
        "마감",
        "불가",
        "불가능",
        "unavailable",
        "not available",
        "fully booked",
        "closed",
        "sold out",
    ]
    return not any(marker in hint for marker in unavailable_markers)


def _text(card, selector: str) -> str | None:
    node = card.select_one(selector)
    if not node:
        return None
    text = " ".join(node.get_text(" ", strip=True).split())
    return text or None


def _href(card, selector: str) -> str | None:
    node = card.select_one(selector)
    if not node:
        return None
    href = node.get("href")
    return str(href) if href else None


def _float_or_none(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieve and choose a place candidate.")
    parser.add_argument("--extraction", required=True, help="Path to an ExtractionResult JSON file.")
    parser.add_argument("--provider", choices=["mock", "html", "kakao"], default="mock")
    parser.add_argument("--html", default=None, help="Static HTML search result file for --provider html.")
    args = parser.parse_args()

    extraction = ExtractionResult.model_validate_json(Path(args.extraction).read_text(encoding="utf-8"))
    provider = provider_from_name(args.provider, html_path=args.html)
    try:
        result = recommend_place(extraction, provider=provider)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
