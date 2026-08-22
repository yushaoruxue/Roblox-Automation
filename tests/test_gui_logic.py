import pathlib
import sys
import unittest
from unittest import mock

import numpy as np


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from gui_app import AEAutomationApp


class TemplateAnchorTests(unittest.TestCase):
    def test_template_match_returns_saved_click_anchor_not_template_center(self):
        rng = np.random.default_rng(12345)
        template = rng.integers(0, 256, size=(10, 20, 3), dtype=np.uint8)
        full = np.zeros((100, 100, 3), dtype=np.uint8)
        full[40:50, 30:50] = template

        app = object.__new__(AEAutomationApp)
        result = app.match_template_location(
            full,
            template,
            threshold=0.99,
            click_anchor=(0.25, 0.75),
        )

        self.assertIsNotNone(result)
        rx, ry, confidence = result
        expected_x = 30 + round(0.25 * 19)
        expected_y = 40 + round(0.75 * 9)
        self.assertAlmostEqual(rx, expected_x / 99)
        self.assertAlmostEqual(ry, expected_y / 99)
        self.assertGreaterEqual(confidence, 0.99)

    def test_template_diagnostics_reports_below_threshold_confidence(self):
        rng = np.random.default_rng(54321)
        template = rng.integers(0, 256, size=(8, 12, 3), dtype=np.uint8)
        full = np.zeros((50, 60, 3), dtype=np.uint8)

        app = object.__new__(AEAutomationApp)
        diagnostics = app.analyze_template_match(
            full,
            template,
            threshold=0.99,
        )

        self.assertFalse(diagnostics["matched"])
        self.assertIsInstance(diagnostics["confidence"], float)
        self.assertIn("max_location", diagnostics)
        self.assertIn("relative_x", diagnostics)
        self.assertIn("relative_y", diagnostics)

    def test_recognition_context_detects_cursor_inside_client(self):
        app = object.__new__(AEAutomationApp)
        app.hwnd = 123
        with (
            mock.patch("gui_app.win32gui.GetCursorPos", return_value=(150, 260)),
            mock.patch("gui_app.win32gui.ClientToScreen", return_value=(100, 200)),
            mock.patch(
                "gui_app.win32gui.GetClientRect",
                return_value=(0, 0, 200, 100),
            ),
            mock.patch("gui_app.win32gui.GetForegroundWindow", return_value=456),
        ):
            context = app.get_recognition_context()

        self.assertTrue(context["cursor_inside"])
        self.assertEqual(context["foreground_hwnd"], 456)
        self.assertEqual((context["client_width"], context["client_height"]), (200, 100))

    def test_diagnostic_screenshot_has_per_run_limit(self):
        app = object.__new__(AEAutomationApp)
        app.recognition_poll_number = 7
        app.diagnostic_saved_count = 30
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        with mock.patch("gui_app.cv2.imwrite") as imwrite:
            path = app.save_recognition_diagnostic(frame, "periodic")
        self.assertIsNone(path)
        imwrite.assert_not_called()

    def test_green_button_fallback_detects_centered_wide_button(self):
        full = np.zeros((952, 1219, 3), dtype=np.uint8)
        cv2 = __import__("cv2")
        cv2.rectangle(full, (486, 231), (732, 260), (0, 255, 100), thickness=-1)

        app = object.__new__(AEAutomationApp)
        result = app.detect_start_button_by_color(full)

        self.assertIsNotNone(result)
        self.assertEqual(result["bbox"], (486, 231, 247, 30))
        self.assertAlmostEqual(result["relative_x"], 609.5 / 1218)
        self.assertAlmostEqual(result["relative_y"], 246 / 951)

    def test_green_button_fallback_rejects_corner_distractor(self):
        full = np.zeros((952, 1219, 3), dtype=np.uint8)
        cv2 = __import__("cv2")
        cv2.rectangle(full, (20, 20), (266, 49), (0, 255, 100), thickness=-1)

        app = object.__new__(AEAutomationApp)
        self.assertIsNone(app.detect_start_button_by_color(full))


class StepsAreaTests(unittest.TestCase):
    def test_mousewheel_scrolls_only_steps_canvas_when_content_overflows(self):
        app = object.__new__(AEAutomationApp)
        app.canvas_steps = mock.Mock()
        app.canvas_steps.bbox.return_value = (0, 0, 500, 900)
        app.canvas_steps.winfo_height.return_value = 300
        event = mock.Mock(delta=-120, num=0)

        result = app.on_steps_mousewheel(event)

        self.assertEqual(result, "break")
        app.canvas_steps.yview_scroll.assert_called_once_with(3, "units")

    def test_mousewheel_resets_without_scrolling_when_content_fits(self):
        app = object.__new__(AEAutomationApp)
        app.canvas_steps = mock.Mock()
        app.canvas_steps.bbox.return_value = (0, 0, 500, 200)
        app.canvas_steps.winfo_height.return_value = 300
        event = mock.Mock(delta=-120, num=0)

        result = app.on_steps_mousewheel(event)

        self.assertEqual(result, "break")
        app.canvas_steps.yview_moveto.assert_called_once_with(0)
        app.canvas_steps.yview_scroll.assert_not_called()


if __name__ == "__main__":
    unittest.main()
