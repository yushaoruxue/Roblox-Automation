import json
import pathlib
import sys
import tempfile
import unittest

import numpy as np


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import vision


class AnalyzeTemplateMatchTests(unittest.TestCase):
    """The same anchor math as gui_app.analyze_template_match, exercised on the
    extracted module-level function (no gui_app import -> proves independence)."""

    def test_returns_saved_click_anchor_not_template_center(self):
        rng = np.random.default_rng(12345)
        template = rng.integers(0, 256, size=(10, 20, 3), dtype=np.uint8)
        full = np.zeros((100, 100, 3), dtype=np.uint8)
        full[40:50, 30:50] = template

        result = vision.analyze_template_match(
            full, template, threshold=0.99, click_anchor=(0.25, 0.75)
        )

        self.assertTrue(result["matched"])
        expected_x = 30 + round(0.25 * 19)
        expected_y = 40 + round(0.75 * 9)
        self.assertAlmostEqual(result["relative_x"], expected_x / 99)
        self.assertAlmostEqual(result["relative_y"], expected_y / 99)
        self.assertGreaterEqual(result["confidence"], 0.99)

    def test_reports_below_threshold_confidence(self):
        rng = np.random.default_rng(54321)
        template = rng.integers(0, 256, size=(8, 12, 3), dtype=np.uint8)
        full = np.zeros((50, 60, 3), dtype=np.uint8)

        diagnostics = vision.analyze_template_match(full, template, threshold=0.99)

        self.assertFalse(diagnostics["matched"])
        self.assertIsInstance(diagnostics["confidence"], float)
        self.assertIn("max_location", diagnostics)
        self.assertIn("relative_x", diagnostics)
        self.assertIn("relative_y", diagnostics)

    def test_raises_when_images_missing(self):
        with self.assertRaises(ValueError):
            vision.analyze_template_match(None, None)

    def test_raises_when_template_larger_than_frame(self):
        big = np.zeros((20, 20, 3), dtype=np.uint8)
        small = np.zeros((10, 10, 3), dtype=np.uint8)
        with self.assertRaises(ValueError):
            vision.analyze_template_match(small, big)


class MatchTemplateLocationTests(unittest.TestCase):
    def test_returns_location_tuple_when_matched(self):
        rng = np.random.default_rng(7)
        template = rng.integers(0, 256, size=(6, 8, 3), dtype=np.uint8)
        full = np.zeros((60, 60, 3), dtype=np.uint8)
        full[20:26, 15:23] = template

        result = vision.match_template_location(
            full, template, threshold=0.99, click_anchor=(0.5, 0.5)
        )

        self.assertIsNotNone(result)
        rx, ry, confidence = result
        self.assertGreaterEqual(confidence, 0.99)
        self.assertTrue(0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0)

    def test_returns_none_when_not_matched(self):
        rng = np.random.default_rng(11)
        template = rng.integers(0, 256, size=(6, 8, 3), dtype=np.uint8)
        full = np.zeros((60, 60, 3), dtype=np.uint8)  # empty frame, no template

        self.assertIsNone(
            vision.match_template_location(full, template, threshold=0.99)
        )


class LoadTemplateClickAnchorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_offset_from_metadata(self):
        (self.dir / "thing.png").write_bytes(b"x")
        (self.dir / "thing.json").write_text(
            json.dumps({"click_offset_x": 0.25, "click_offset_y": 0.75}),
            encoding="utf-8",
        )
        self.assertEqual(
            vision.load_template_click_anchor(str(self.dir), "thing.png"),
            (0.25, 0.75),
        )

    def test_missing_metadata_falls_back_to_center(self):
        (self.dir / "thing.png").write_bytes(b"x")
        logs = []
        self.assertEqual(
            vision.load_template_click_anchor(str(self.dir), "thing.png", logs.append),
            (0.5, 0.5),
        )
        self.assertTrue(logs)

    def test_invalid_metadata_falls_back_to_center(self):
        (self.dir / "thing.png").write_bytes(b"x")
        (self.dir / "thing.json").write_text("{broken", encoding="utf-8")
        logs = []
        self.assertEqual(
            vision.load_template_click_anchor(str(self.dir), "thing.png", logs.append),
            (0.5, 0.5),
        )
        self.assertTrue(logs)

    def test_out_of_range_anchor_falls_back_to_center(self):
        (self.dir / "thing.png").write_bytes(b"x")
        (self.dir / "thing.json").write_text(
            json.dumps({"click_offset_x": 1.5, "click_offset_y": 0.5}),
            encoding="utf-8",
        )
        logs = []
        self.assertEqual(
            vision.load_template_click_anchor(str(self.dir), "thing.png", logs.append),
            (0.5, 0.5),
        )
        self.assertTrue(logs)


if __name__ == "__main__":
    unittest.main()
