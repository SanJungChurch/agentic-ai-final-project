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


class DemoPlaceProvider:
    """Deterministic query-sensitive provider for the HTML recording demo."""

    def search(self, query: str, *, location_hint: str | None = None) -> list[PlaceCandidate]:
        normalized = f"{query} {location_hint or ''}".lower()
        area = _demo_area(normalized)
        if any(token in normalized for token in ["식당", "restaurant", "점심", "저녁", "식사", "lunch", "dinner"]):
            return [
                PlaceCandidate(
                    name=f"{area} 예약 식당",
                    address=_demo_address(area),
                    category="restaurant",
                    rating=4.4,
                    source_url=_naver_search_url(f"{area} 식당 예약"),
                    availability_hint="네이버 예약 확인 필요",
                ),
                PlaceCandidate(
                    name=f"{area} 조용한 한식당",
                    address=_demo_address(area),
                    category="restaurant",
                    rating=4.1,
                    source_url=_naver_search_url(f"{area} 한식당 예약"),
                    availability_hint="예약 가능 여부 확인 필요",
                ),
            ]
        if any(token in normalized for token in ["스터디룸", "study room", "팀플", "스터디", "과제"]):
            return [
                PlaceCandidate(
                    name=f"{area} 스터디룸",
                    address=_demo_address(area),
                    category="study room",
                    rating=4.5,
                    source_url=_naver_search_url(f"{area} 스터디룸 예약"),
                    availability_hint="네이버 예약 확인 필요",
                )
            ]
        if any(token in normalized for token in ["회의실", "meeting room", "세미나", "발표", "인터뷰", "면접", "면담", "상담"]):
            return [
                PlaceCandidate(
                    name=f"{area} 조용한 회의실",
                    address=_demo_address(area),
                    category="meeting room",
                    rating=4.3,
                    source_url=_naver_search_url(f"{area} 회의실 예약"),
                    availability_hint="네이버 예약 확인 필요",
                )
            ]
        if "강남" in normalized or "gangnam" in normalized:
            return [
                PlaceCandidate(
                    name="강남 브루잉 라운지",
                    address="서울 강남구 테헤란로 152",
                    category="cafe",
                    rating=4.5,
                    source_url="https://map.naver.com/p/search/%EA%B0%95%EB%82%A8%20%EC%B9%B4%ED%8E%98",
                    availability_hint="네이버 예약 확인 필요",
                ),
                PlaceCandidate(
                    name="역삼 스터디 카페",
                    address="서울 강남구 논현로 508",
                    category="study room",
                    rating=4.2,
                    source_url="https://map.naver.com/p/search/%EC%97%AD%EC%82%BC%20%EC%8A%A4%ED%84%B0%EB%94%94%EC%B9%B4%ED%8E%98",
                    availability_hint="좌석 확인 필요",
                ),
            ]
        if "홍대" in normalized or "hongdae" in normalized:
            return [
                PlaceCandidate(
                    name="홍대 루프 카페",
                    address="서울 마포구 와우산로 94",
                    category="cafe",
                    rating=4.4,
                    source_url="https://map.naver.com/p/search/%ED%99%8D%EB%8C%80%20%EC%B9%B4%ED%8E%98",
                    availability_hint="네이버 예약 확인 필요",
                )
            ]
        if "판교" in normalized or "pangyo" in normalized:
            return [
                PlaceCandidate(
                    name="판교 워크 라운지",
                    address="경기 성남시 분당구 판교역로 166",
                    category="cafe",
                    rating=4.3,
                    source_url="https://map.naver.com/p/search/%ED%8C%90%EA%B5%90%20%EC%B9%B4%ED%8E%98",
                    availability_hint="네이버 예약 확인 필요",
                )
            ]
        return [
            PlaceCandidate(
                name="카페 온더힐",
                address="서울 동작구 상도로 369 숭실대입구역 근처",
                category="cafe",
                rating=4.6,
                source_url=(
                    "https://map.naver.com/p/search/%EC%88%AD%EC%8B%A4%EB%8C%80%20%EC%B9%B4%ED%8E%98/"
                    "place/1649187599?c=17.24,0,0,0,dh&placePath=/booking?entry=bmp"
                ),
                availability_hint="네이버 예약 확인 필요",
            ),
            PlaceCandidate(
                name="스터디룸 숭실",
                address="서울 동작구 상도로 360",
                category="study room",
                rating=4.4,
                source_url="https://map.naver.com/p/search/%EC%88%AD%EC%8B%A4%EB%8C%80%20%EC%8A%A4%ED%84%B0%EB%94%94%EB%A3%B8",
                availability_hint="평일 저녁 예약 가능",
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
    query_override: str | None = None,
) -> PlaceRecommendation:
    query = (query_override or "").strip() or build_place_query(extraction)
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
    if name == "demo":
        return DemoPlaceProvider()
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
    if any(token in query for token in ["식당", "점심", "저녁", "식사"]) and candidate.category == "restaurant":
        score += 1.0
    if "스터디룸" in query and candidate.category == "study room":
        score += 1.0
    if any(token in query for token in ["회의실", "면담", "인터뷰", "상담", "세미나"]) and candidate.category == "meeting room":
        score += 1.0

    return candidate.model_copy(update={"score": round(score, 4)})


def _demo_area(normalized: str) -> str:
    if "강남" in normalized or "gangnam" in normalized:
        return "강남역"
    if "홍대" in normalized or "hongdae" in normalized:
        return "홍대"
    if "판교" in normalized or "pangyo" in normalized:
        return "판교"
    if "숭실" in normalized or "soongsil" in normalized:
        return "숭실대"
    return "주변"


def _demo_address(area: str) -> str | None:
    if area == "강남역":
        return "서울 강남구 테헤란로 일대"
    if area == "홍대":
        return "서울 마포구 와우산로 일대"
    if area == "판교":
        return "경기 성남시 분당구 판교역로 일대"
    if area == "숭실대":
        return "서울 동작구 상도로 일대"
    return None


def _naver_search_url(query: str) -> str:
    return "https://map.naver.com/p/search/" + urllib.parse.quote(query)


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
