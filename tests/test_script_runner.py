import pathlib
import sys
import threading
import unittest
from unittest import mock


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import script_runner as sr


FAKE_FRAME = object()
FAKE_TEMPLATE = object()


def _diag(matched=True, conf=0.99, x=0.5, y=0.5):
    return {
        "matched": matched,
        "confidence": conf,
        "max_location": (10, 10),
        "relative_x": x,
        "relative_y": y,
    }


class ScriptRunnerTests(unittest.TestCase):
    def _run(self, actions, stop_event=None, capture_side_effect=None,
             match_side_effect=None, base_dir="."):
        """Run a script with all external primitives mocked; return (result, calls)."""
        calls = {"capture": 0, "match": 0, "input": []}
        stop_event = stop_event or threading.Event()

        def fake_capture(hwnd):
            calls["capture"] += 1
            if capture_side_effect:
                capture_side_effect(calls)
            return FAKE_FRAME

        def fake_match(frame, tpl, threshold=0.85, click_anchor=(0.5, 0.5)):
            calls["match"] += 1
            if match_side_effect:
                return match_side_effect(calls)
            return _diag()

        def fake_input(hwnd, actions_list, log_callback=None):
            calls["input"].append(list(actions_list))
            return True

        with mock.patch.object(sr.engine, "capture_window", side_effect=fake_capture), \
             mock.patch.object(sr.engine, "run_input_actions", side_effect=fake_input), \
             mock.patch.object(sr.vision, "analyze_template_match", side_effect=fake_match), \
             mock.patch.object(sr.vision, "load_template_click_anchor", return_value=(0.5, 0.5)), \
             mock.patch.object(sr.cv2, "imread", return_value=FAKE_TEMPLATE):
            result = sr.run_script(123, actions, base_dir, stop_event=stop_event)
        return result, calls

    # ---- A. fresh capture ----
    def test_fresh_capture_each_find(self):
        result, calls = self._run([
            {"type": "find_image", "template": "a.png"},
            {"type": "find_image", "template": "a.png"},
        ])
        self.assertTrue(result)
        self.assertEqual(calls["capture"], 2)
        self.assertEqual(calls["match"], 2)

    # ---- B. click_image found ----
    def test_click_image_found_single_click(self):
        result, calls = self._run([
            {"type": "click_image", "template": "a.png"},
        ])
        self.assertTrue(result)
        self.assertEqual(len(calls["input"]), 1)
        self.assertEqual(calls["input"][0], [{"type": "click", "x": 0.5, "y": 0.5}])

    # ---- C. click_image not found ----
    def test_click_image_not_found_no_input(self):
        result, calls = self._run(
            [{"type": "click_image", "template": "a.png"}],
            match_side_effect=lambda c: _diag(matched=False, conf=0.1),
        )
        self.assertTrue(result)
        self.assertEqual(len(calls["input"]), 0)

    # ---- D. if_image true ----
    def test_if_image_true_runs_then(self):
        result, calls = self._run([
            {"type": "if_image", "template": "a.png",
             "then": [{"type": "key", "key": "a"}],
             "else": [{"type": "key", "key": "b"}]},
        ])
        self.assertTrue(result)
        self.assertEqual(len(calls["input"]), 1)
        self.assertEqual(calls["input"][0], [{"type": "key", "key": "a"}])

    # ---- E. if_image false ----
    def test_if_image_false_runs_else(self):
        result, calls = self._run(
            [{"type": "if_image", "template": "a.png",
              "then": [{"type": "key", "key": "a"}],
              "else": [{"type": "key", "key": "b"}]}],
            match_side_effect=lambda c: _diag(matched=False, conf=0.1),
        )
        self.assertTrue(result)
        self.assertEqual(len(calls["input"]), 1)
        self.assertEqual(calls["input"][0], [{"type": "key", "key": "b"}])

    # ---- F. repeat count ----
    def test_repeat_count_exact(self):
        result, calls = self._run([
            {"type": "repeat", "count": 3, "actions": [{"type": "find_image", "template": "a.png"}]},
        ])
        self.assertTrue(result)
        self.assertEqual(calls["capture"], 3)
        self.assertEqual(calls["match"], 3)

    # ---- G. repeat forever + stop ----
    def test_repeat_forever_stops(self):
        stop = threading.Event()

        def capture_side(calls):
            if calls["capture"] >= 3:
                stop.set()

        result, calls = self._run(
            [{"type": "repeat", "forever": True,
              "actions": [{"type": "find_image", "template": "a.png"}]}],
            stop_event=stop,
            capture_side_effect=capture_side,
        )
        self.assertTrue(result)
        self.assertGreaterEqual(calls["capture"], 3)

    # ---- H. interruptible wait ----
    def test_interruptible_wait(self):
        stop = threading.Event()

        def fake_sleep(seconds):
            stop.set()

        with mock.patch.object(sr.time, "sleep", side_effect=fake_sleep):
            result, calls = self._run(
                [{"type": "wait", "seconds": 10}],
                stop_event=stop,
            )
        self.assertTrue(result)

    # ---- I. input lease ----
    def test_input_lease_two_batches(self):
        result, calls = self._run([
            {"type": "key", "key": "w"},
            {"type": "click", "x": 0.1, "y": 0.1},
            {"type": "find_image", "template": "a.png"},
            {"type": "key", "key": "a"},
            {"type": "click", "x": 0.2, "y": 0.2},
        ])
        self.assertTrue(result)
        self.assertEqual(len(calls["input"]), 2)
        self.assertEqual(
            calls["input"][0],
            [{"type": "key", "key": "w"}, {"type": "click", "x": 0.1, "y": 0.1}],
        )
        self.assertEqual(
            calls["input"][1],
            [{"type": "key", "key": "a"}, {"type": "click", "x": 0.2, "y": 0.2}],
        )
        self.assertEqual(calls["capture"], 1)

    # ---- J. vision during no lock ----
    def test_vision_not_inside_input_session(self):
        seq = []

        def fake_input(hwnd, actions_list, log_callback=None):
            seq.append(("input_start",))
            seq.append(("input_end",))
            return True

        def fake_capture(hwnd):
            seq.append(("capture",))
            return FAKE_FRAME

        def fake_match(frame, tpl, threshold=0.85, click_anchor=(0.5, 0.5)):
            seq.append(("match",))
            return _diag()

        with mock.patch.object(sr.engine, "capture_window", side_effect=fake_capture), \
             mock.patch.object(sr.engine, "run_input_actions", side_effect=fake_input), \
             mock.patch.object(sr.vision, "analyze_template_match", side_effect=fake_match), \
             mock.patch.object(sr.vision, "load_template_click_anchor", return_value=(0.5, 0.5)), \
             mock.patch.object(sr.cv2, "imread", return_value=FAKE_TEMPLATE):
            sr.run_script(123, [
                {"type": "key", "key": "w"},
                {"type": "find_image", "template": "a.png"},
                {"type": "click", "x": 0.3, "y": 0.3},
            ], ".")

        inside = False
        for ev in seq:
            if ev[0] == "input_start":
                inside = True
            elif ev[0] == "input_end":
                inside = False
            elif ev[0] in ("capture", "match") and inside:
                self.fail(f"vision event {ev} occurred inside an input session")
        self.assertIn(("capture",), seq)
        self.assertIn(("match",), seq)

    # ---- K. nested stop ----
    def test_nested_stop_propagates(self):
        stop = threading.Event()

        def capture_side(calls):
            if calls["capture"] >= 4:
                stop.set()

        result, calls = self._run(
            [{"type": "repeat", "forever": True, "actions": [
                {"type": "if_image", "template": "a.png",
                 "then": [{"type": "find_image", "template": "b.png"}]},
            ]}],
            stop_event=stop,
            capture_side_effect=capture_side,
        )
        self.assertTrue(result)
        self.assertLess(calls["capture"], 10)

    # ---- L. invalid action ----
    def test_invalid_action_fails(self):
        result, calls = self._run([{"type": "bogus"}])
        self.assertFalse(result)
        self.assertEqual(calls["capture"], 0)
        self.assertEqual(len(calls["input"]), 0)


if __name__ == "__main__":
    unittest.main()
