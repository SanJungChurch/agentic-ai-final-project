import importlib.util
import unittest
from pathlib import Path


def _load_runner_module():
    path = Path("scripts/showui_reservation_runner.py")
    spec = importlib.util.spec_from_file_location("showui_reservation_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShowUIReservationRunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = _load_runner_module()

    def test_split_start_returns_date_and_time(self) -> None:
        date_text, time_text = self.runner.split_start("2026-05-27T17:00:00+09:00")

        self.assertEqual(date_text, "2026-05-27")
        self.assertEqual(time_text, "17:00")

    def test_parse_showui_point_scales_normalized_coordinates(self) -> None:
        x, y = self.runner.parse_showui_point("[0.5, 0.25]")

        self.assertEqual(x, 640)
        self.assertEqual(y, 180)

    def test_parse_showui_point_accepts_pixel_coordinates(self) -> None:
        x, y = self.runner.parse_showui_point({"point": [320, 240]})

        self.assertEqual(x, 320)
        self.assertEqual(y, 240)

    def test_normalize_target_url_keeps_file_url(self) -> None:
        url = self.runner.normalize_target_url("file:///C:/VSProject/agentic/data/reservation/mock_reservation.html")

        self.assertEqual(url, "file:///C:/VSProject/agentic/data/reservation/mock_reservation.html")

    def test_grounder_from_source_supports_local_alias(self) -> None:
        original = self.runner.LocalShowUIGrounder

        class FakeLocalGrounder:
            def __init__(self, model_id):
                self.model_id = model_id

        try:
            self.runner.LocalShowUIGrounder = FakeLocalGrounder
            grounder = self.runner.grounder_from_source("local")
        finally:
            self.runner.LocalShowUIGrounder = original

        self.assertEqual(grounder.model_id, "showlab/ShowUI-2B")


if __name__ == "__main__":
    unittest.main()
