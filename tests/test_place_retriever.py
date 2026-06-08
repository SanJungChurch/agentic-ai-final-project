from pathlib import Path
import unittest
from unittest.mock import patch

from src.email_agent.place_retriever import (
    DemoPlaceProvider,
    KakaoLocalPlaceProvider,
    StaticHtmlPlaceProvider,
    build_place_query,
    recommend_place,
)
from src.email_agent.schema import ExtractionResult, Intent, PlaceCandidate


class PlaceRetrieverTest(unittest.TestCase):
    def test_build_place_query_cleans_prefix(self) -> None:
        extraction = ExtractionResult(
            intent=Intent.SCHEDULE_MEETING,
            location_preference="장소는 숭실대 근처 카페면 좋겠습니다.",
        )

        self.assertEqual(build_place_query(extraction), "숭실대 근처 카페면 좋겠습니다")

    def test_static_html_provider_parses_candidates(self) -> None:
        provider = StaticHtmlPlaceProvider("data/place_search/soongsil_cafes.html")
        candidates = provider.search("숭실대 근처 카페")

        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0].name, "카페 온더힐")
        self.assertEqual(candidates[0].rating, 4.6)

    def test_recommend_place_selects_candidate(self) -> None:
        extraction = ExtractionResult(
            intent=Intent.SCHEDULE_MEETING,
            location_preference="숭실대 근처 카페",
        )
        provider = StaticHtmlPlaceProvider(Path("data/place_search/soongsil_cafes.html"))

        recommendation = recommend_place(extraction, provider=provider)

        self.assertEqual(recommendation.status, "selected")
        self.assertIsNotNone(recommendation.selected)
        self.assertEqual(recommendation.selected.name, "카페 온더힐")

    def test_recommend_place_filters_unavailable_candidates(self) -> None:
        class FakeProvider:
            def search(self, query, *, location_hint=None):
                return [
                    PlaceCandidate(
                        name="Unavailable Cafe",
                        category="cafe",
                        availability_hint="예약 불가",
                        rating=5.0,
                    ),
                    PlaceCandidate(
                        name="Reservable Cafe",
                        category="cafe",
                        availability_hint="예약 가능",
                        rating=4.0,
                    ),
                ]

        extraction = ExtractionResult(
            intent=Intent.SCHEDULE_MEETING,
            location_preference="campus cafe",
        )

        recommendation = recommend_place(extraction, provider=FakeProvider())

        self.assertEqual(recommendation.status, "selected")
        self.assertEqual(recommendation.selected.name, "Reservable Cafe")
        self.assertEqual([candidate.name for candidate in recommendation.candidates], ["Reservable Cafe"])

    def test_recommend_place_returns_no_candidates_when_all_unavailable(self) -> None:
        class FakeProvider:
            def search(self, query, *, location_hint=None):
                return [
                    PlaceCandidate(name="Closed Cafe", availability_hint="fully booked"),
                    PlaceCandidate(name="Sold Out Cafe", availability_hint="sold out"),
                ]

        extraction = ExtractionResult(
            intent=Intent.SCHEDULE_MEETING,
            location_preference="campus cafe",
        )

        recommendation = recommend_place(extraction, provider=FakeProvider())

        self.assertEqual(recommendation.status, "no_candidates")
        self.assertEqual(recommendation.candidates, [])

    def test_demo_provider_changes_candidates_by_location_query(self) -> None:
        provider = DemoPlaceProvider()
        gangnam = provider.search("강남역 근처 카페")
        hongdae = provider.search("홍대 근처 카페")

        self.assertNotEqual(gangnam[0].name, hongdae[0].name)
        self.assertEqual(gangnam[0].name, "강남 브루잉 라운지")
        self.assertEqual(hongdae[0].name, "홍대 루프 카페")

    def test_kakao_provider_parses_documents(self) -> None:
        payload = {
            "documents": [
                {
                    "place_name": "카페 테스트",
                    "road_address_name": "서울 동작구 상도로 1",
                    "address_name": "서울 동작구 상도동",
                    "category_group_name": "카페",
                    "place_url": "https://place.map.kakao.com/1",
                }
            ]
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                import json

                return json.dumps(payload).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            provider = KakaoLocalPlaceProvider(api_key="test-key")
            candidates = provider.search("숭실대 근처 카페")

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, "카페 테스트")
        self.assertEqual(candidates[0].category, "카페")


if __name__ == "__main__":
    unittest.main()
