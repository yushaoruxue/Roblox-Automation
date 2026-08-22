"""Slice 5 tests: color primitives, color/wait-image/drag actions, and the
key_hold/key_release removal."""

import pathlib
import sys
import threading
import unittest
from unittest import mock

import numpy as np


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import vision
import user_actions as ua
import script_runner as sr
import engine


# --------------------------------------------------------------------------- #
# A-D: vision.find_color unit tests (pure function)
# --------------------------------------------------------------------------- #
class FindColorTests(unittest.TestCase):
    def _frame(self):
        # 100x100 BGR frame
        f = np.zeros((100, 100, 3), dtype=np.uint8)
        # a 20x20 green block at (20..40, 10..30), color #43A982 = (67,169,130) RGB
        f[10:30, 20:40] = (130, 169, 67)  # BGR
        return f

    def test_A_exact_match(self):
        r = vision.find_color(self._frame(), (67, 169, 130), 0)
        self.assertTrue(r["found"])
        self.assertEqual(r["match_count"], 400)
        self.assertEqual(r["actual_color"], (67, 169, 130))

    def test_B_tolerance_match(self):
        f = self._frame()
        # slightly off target: (75, 175, 138) vs (67,169,130) -> dRGB=(8,6,8)
        f[10:30, 20:40] = (138, 175, 75)  # BGR
        r = vision.find_color(f, (67, 169, 130), 12)
        self.assertTrue(r["found"])

    def test_C_outside_tolerance_false(self):
        f = self._frame()
        f[10:30, 20:40] = (130, 169, 67)  # exact block
        r = vision.find_color(f, (255, 0, 0), 12)  # red target
        self.assertFalse(r["found"])

    def test_D_region_restricts(self):
        f = self._frame()
        # region covering only top-left (0,0,0.15,0.15) -> no green block
        r = vision.find_color(f, (67, 169, 130), 0, region=(0, 0, 0.15, 0.15))
        self.assertFalse(r["found"])
        # region covering the block
        r2 = vision.find_color(f, (67, 169, 130), 0, region=(0.1, 0.05, 0.3, 0.3))
        self.assertTrue(r2["found"])


# --------------------------------------------------------------------------- #
# Color / wait-image / drag: user-action compile + summary
# --------------------------------------------------------------------------- #
class Slice5UserActionTests(unittest.TestCase):
    def test_click_color_compiles_with_wait(self):
        out = ua.compile_user_actions([ua.new_action("click_color")])
        self.assertEqual(out[0]["type"], "click_color")
        self.assertEqual(out[0]["color"], [67, 169, 130])
        self.assertEqual(out[0]["tolerance"], 12)
        self.assertEqual(out[0]["region"], None)
        self.assertEqual(out[1], {"type": "wait", "seconds": 0.2})

    def test_if_color_compiles_branches(self):
        a = ua.new_action("if_color")
        a["then"] = [ua.new_action("click")]
        a["else"] = [ua.new_action("wait")]
        out = ua.compile_user_actions([a])
        self.assertEqual(out[0]["then"][0]["type"], "click")
        self.assertEqual(out[0]["else"][0]["type"], "wait")

    def test_wait_color_timeout_and_forever(self):
        a = ua.new_action("wait_color")
        a["timeout"] = 30
        out = ua.compile_user_actions([a])
        self.assertEqual(out[0]["timeout"], 30)
        self.assertEqual(out[0]["poll_interval"], 0.5)
        b = ua.new_action("wait_color")
        out2 = ua.compile_user_actions([b])
        self.assertIsNone(out2[0]["timeout"])

    def test_wait_image_compiles(self):
        a = ua.new_action("wait_image")
        a["template"] = "assets/foo.png"
        out = ua.compile_user_actions([a])
        self.assertEqual(out[0]["type"], "wait_image")
        self.assertEqual(out[0]["template"], "assets/foo.png")

    def test_drag_compiles_with_after_wait(self):
        out = ua.compile_user_actions([ua.new_action("drag")])
        self.assertEqual(out[0]["type"], "drag")
        self.assertEqual(out[0]["from"], {"x": 0.3, "y": 0.4})
        self.assertEqual(out[0]["to"], {"x": 0.7, "y": 0.4})
        self.assertEqual(out[0]["duration"], 0.5)
        self.assertEqual(out[1], {"type": "wait", "seconds": 0.2})

    def test_drag_validate_rejects_missing_points(self):
        a = ua.new_action("drag")
        a["from"] = {"x": 0.5}  # missing y
        with self.assertRaises(ValueError):
            ua.validate_user_actions([a])

    def test_color_region_flattens(self):
        a = ua.new_action("find_color")
        a["region"] = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
        out = ua.compile_user_actions([a])
        self.assertEqual(out[0]["region"], (0.1, 0.2, 0.3, 0.4))

    def test_summaries(self):
        self.assertIn("#43A982", ua.action_summary(ua.new_action("click_color")))
        self.assertIn("∞", ua.action_summary(ua.new_action("wait_color")))
        self.assertIn("拖动", ua.action_summary(ua.new_action("drag")))

    def test_T_key_hold_removed(self):
        self.assertNotIn("key_hold", ua.ACTION_TEMPLATES)
        self.assertNotIn("key_release", ua.ACTION_TEMPLATES)
        for cat_items in ua.ACTION_LIBRARY.values():
            for _, t in cat_items:
                self.assertNotIn(t, ("key_hold", "key_release"))
        with self.assertRaises(ValueError):
            ua.validate_user_actions([{"type": "key_hold", "key": "shift"}])


# --------------------------------------------------------------------------- #
# Color actions in script_runner (E-J) + wait-image (K-M)
# --------------------------------------------------------------------------- #
class Slice5RunnerTests(unittest.TestCase):
    def _runner(self, color_side_effect=None, match_side_effect=None,
                stop_event=None):
        calls = {"capture": 0, "find_color": 0, "match": 0, "input": []}
        stop_event = stop_event or threading.Event()

        def fake_capture(hwnd):
            calls["capture"] += 1
            return object()

        def fake_find_color(frame, color, tolerance, region=None):
            calls["find_color"] += 1
            if color_side_effect:
                return color_side_effect(calls)
            return {"found": True, "position": (0.5, 0.5), "match_count": 1,
                    "actual_color": color}

        def fake_match(frame, tpl, threshold=0.85, click_anchor=(0.5, 0.5)):
            calls["match"] += 1
            if match_side_effect:
                return match_side_effect(calls)
            return {"matched": True, "confidence": 0.99, "max_location": (0, 0),
                    "relative_x": 0.5, "relative_y": 0.5}

        def fake_input(hwnd, actions_list, log_callback=None):
            calls["input"].append(list(actions_list))
            return True

        with mock.patch.object(sr.engine, "capture_window", side_effect=fake_capture), \
             mock.patch.object(sr.engine, "run_input_actions", side_effect=fake_input), \
             mock.patch.object(sr.vision, "find_color", side_effect=fake_find_color), \
             mock.patch.object(sr.vision, "analyze_template_match", side_effect=fake_match), \
             mock.patch.object(sr.vision, "load_template_click_anchor", return_value=(0.5, 0.5)), \
             mock.patch.object(sr.cv2, "imread", return_value=object()):
            result = sr.run_script(123, [], ".", stop_event=stop_event)
        return result, calls

    def test_E_two_color_actions_two_fresh_captures(self):
        runner = sr.ScriptRunner(123, ".")
        captures = {"n": 0}

        def fake_capture(hwnd):
            captures["n"] += 1
            return object()

        with mock.patch.object(sr.engine, "capture_window", side_effect=fake_capture), \
             mock.patch.object(sr.vision, "find_color", return_value={
                 "found": True, "position": (0.5, 0.5), "match_count": 1,
                 "actual_color": (67, 169, 130)}):
            runner._do_find_color({"type": "find_color", "color": (67, 169, 130),
                                   "tolerance": 12, "region": None})
            runner._do_find_color({"type": "find_color", "color": (67, 169, 130),
                                   "tolerance": 12, "region": None})
        self.assertEqual(captures["n"], 2)

    def test_F_click_color_found_one_click(self):
        runner = sr.ScriptRunner(123, ".")
        calls = {"input": []}
        with mock.patch.object(sr.engine, "capture_window", return_value=object()), \
             mock.patch.object(sr.vision, "find_color", return_value={
                 "found": True, "position": (0.4, 0.6), "match_count": 5,
                 "actual_color": (67, 169, 130)}), \
             mock.patch.object(sr.engine, "run_input_actions",
                               side_effect=lambda hwnd, acts, **k: calls["input"].append(acts) or True):
            runner._do_click_color({"type": "click_color", "color": (67, 169, 130),
                                    "tolerance": 12, "region": None})
        self.assertEqual(len(calls["input"]), 1)
        self.assertEqual(calls["input"][0], [{"type": "click", "x": 0.4, "y": 0.6}])

    def test_G_click_color_not_found_no_input(self):
        runner = sr.ScriptRunner(123, ".")
        calls = {"input": []}
        with mock.patch.object(sr.engine, "capture_window", return_value=object()), \
             mock.patch.object(sr.vision, "find_color", return_value={
                 "found": False, "position": None, "match_count": 0,
                 "actual_color": None}), \
             mock.patch.object(sr.engine, "run_input_actions",
                               side_effect=lambda hwnd, acts, **k: calls["input"].append(acts) or True):
            runner._do_click_color({"type": "click_color", "color": (67, 169, 130),
                                    "tolerance": 12, "region": None})
        self.assertEqual(calls["input"], [])

    def test_H_if_color_true_false(self):
        runner = sr.ScriptRunner(123, ".")
        executed = []
        with mock.patch.object(sr.engine, "capture_window", return_value=object()), \
             mock.patch.object(sr.vision, "find_color", return_value={
                 "found": True, "position": (0.5, 0.5), "match_count": 1,
                 "actual_color": (67, 169, 130)}):
            # then branch: a key action batched into input
            with mock.patch.object(sr.engine, "run_input_actions", return_value=True):
                runner._do_if_color({"type": "if_color", "color": (67, 169, 130),
                                     "tolerance": 12, "region": None,
                                     "then": [{"type": "wait", "seconds": 0}],
                                     "else": [{"type": "wait", "seconds": 0}]})
        # false case
        runner2 = sr.ScriptRunner(123, ".")
        with mock.patch.object(sr.engine, "capture_window", return_value=object()), \
             mock.patch.object(sr.vision, "find_color", return_value={
                 "found": False, "position": None, "match_count": 0,
                 "actual_color": None}), \
             mock.patch.object(sr.engine, "run_input_actions", return_value=True):
            branch = []
            runner2._do_if_color({"type": "if_color", "color": (67, 169, 130),
                                  "tolerance": 12, "region": None,
                                  "then": [{"type": "click", "x": 0.1, "y": 0.1}],
                                  "else": [{"type": "click", "x": 0.9, "y": 0.9}]})

    def test_I_wait_color_polls_fresh_captures(self):
        runner = sr.ScriptRunner(123, ".")
        captures = {"n": 0}
        found_at = [3]  # becomes found on the 3rd poll

        def fake_capture(hwnd):
            captures["n"] += 1
            return object()

        def fake_find_color(frame, color, tol, region=None):
            if captures["n"] >= found_at[0]:
                return {"found": True, "position": (0.5, 0.5), "match_count": 1,
                        "actual_color": color}
            return {"found": False, "position": None, "match_count": 0,
                    "actual_color": None}

        with mock.patch.object(sr.engine, "capture_window", side_effect=fake_capture), \
             mock.patch.object(sr.vision, "find_color", side_effect=fake_find_color), \
             mock.patch.object(sr.time, "sleep"), \
             mock.patch.object(sr.time, "monotonic", side_effect=[0, 1, 2, 3]):
            runner._do_wait_color({"type": "wait_color", "color": (67, 169, 130),
                                   "tolerance": 12, "region": None,
                                   "poll_interval": 0.5, "timeout": None})
        self.assertEqual(captures["n"], 3)

    def test_J_wait_color_stop(self):
        stop = threading.Event()
        stop.set()
        runner = sr.ScriptRunner(123, ".", stop_event=stop)
        with mock.patch.object(sr.engine, "capture_window", return_value=object()), \
             mock.patch.object(sr.vision, "find_color", return_value={
                 "found": False, "position": None, "match_count": 0,
                 "actual_color": None}):
            with self.assertRaises(sr.ScriptStop):
                runner._poll_until(lambda: None, 0.5, None)

    def test_K_wait_image_third_capture(self):
        runner = sr.ScriptRunner(123, ".")
        captures = {"n": 0}

        def fake_capture(hwnd):
            captures["n"] += 1
            return object()

        def fake_match(frame, tpl, threshold=0.85, click_anchor=(0.5, 0.5)):
            return {"matched": captures["n"] >= 3, "confidence": 0.99,
                    "max_location": (0, 0), "relative_x": 0.5, "relative_y": 0.5}

        with mock.patch.object(sr.engine, "capture_window", side_effect=fake_capture), \
             mock.patch.object(sr.vision, "analyze_template_match", side_effect=fake_match), \
             mock.patch.object(sr.vision, "load_template_click_anchor", return_value=(0.5, 0.5)), \
             mock.patch.object(sr.cv2, "imread", return_value=object()), \
             mock.patch.object(sr.time, "sleep"), \
             mock.patch.object(sr.time, "monotonic", side_effect=[0, 1, 2, 3]):
            runner._do_wait_image({"type": "wait_image", "template": "a.png",
                                   "threshold": 0.85, "poll_interval": 0.5,
                                   "timeout": None})
        self.assertEqual(captures["n"], 3)

    def test_L_wait_image_timeout(self):
        runner = sr.ScriptRunner(123, ".")
        with mock.patch.object(sr.engine, "capture_window", return_value=object()), \
             mock.patch.object(sr.vision, "analyze_template_match", return_value={
                 "matched": False, "confidence": 0.1, "max_location": (0, 0),
                 "relative_x": 0.0, "relative_y": 0.0}), \
             mock.patch.object(sr.vision, "load_template_click_anchor", return_value=(0.5, 0.5)), \
             mock.patch.object(sr.cv2, "imread", return_value=object()), \
             mock.patch.object(sr.time, "sleep"), \
             mock.patch.object(sr.time, "monotonic", side_effect=[0, 10]):
            # timeout=5 -> first monotonic 0, poll returns None immediately,
            # then deadline check: monotonic() >= 5 -> return None
            result = runner._poll_until(lambda: None, 0.5, 5)
        self.assertIsNone(result)


# --------------------------------------------------------------------------- #
# Drag in engine (N-S)
# --------------------------------------------------------------------------- #
class DragEngineTests(unittest.TestCase):
    def test_Q_mousedown_move_mouseup_order(self):
        events = []

        def mouse_event(flags, *a):
            events.append(("mouse", flags))

        def set_cursor(pos):
            events.append(("cursor", pos))

        hwnd = 123
        with mock.patch.object(engine.win32api, "mouse_event", side_effect=mouse_event), \
             mock.patch.object(engine.win32api, "SetCursorPos", side_effect=set_cursor), \
             mock.patch.object(engine.win32gui, "GetForegroundWindow", return_value=hwnd), \
             mock.patch.object(engine, "relative_to_screen",
                               side_effect=lambda h, x, y: (0, 0, x * 1000, y * 1000, 2560, 1417)), \
             mock.patch.object(engine, "_move_and_verify", return_value=(True, 0, 0)):
            ok = engine._do_drag(hwnd, {"from": {"x": 0.1, "y": 0.1},
                                        "to": {"x": 0.9, "y": 0.9},
                                        "duration": 0.05})
        self.assertTrue(ok)
        mouse_flags = [f for f in events if f[0] == "mouse"]
        self.assertEqual(mouse_flags[0][1], engine.win32con.MOUSEEVENTF_LEFTDOWN)
        self.assertEqual(mouse_flags[-1][1], engine.win32con.MOUSEEVENTF_LEFTUP)

    def test_R_exception_still_mouse_up(self):
        events = []

        def mouse_event(flags, *a):
            events.append(flags)

        def set_cursor(pos):
            if pos[0] > 500:  # 中途异常
                raise RuntimeError("boom")

        hwnd = 123
        with mock.patch.object(engine.win32api, "mouse_event", side_effect=mouse_event), \
             mock.patch.object(engine.win32api, "SetCursorPos", side_effect=set_cursor), \
             mock.patch.object(engine.win32gui, "GetForegroundWindow", return_value=hwnd), \
             mock.patch.object(engine, "relative_to_screen",
                               side_effect=lambda h, x, y: (0, 0, x * 1000, y * 1000, 2560, 1417)), \
             mock.patch.object(engine, "_move_and_verify", return_value=(True, 0, 0)):
            with self.assertRaises(RuntimeError):
                engine._do_drag(hwnd, {"from": {"x": 0.1, "y": 0.1},
                                       "to": {"x": 0.9, "y": 0.9},
                                       "duration": 0.05})
        # _do_drag 的 finally 保证 MouseUp 一定发送（即使中途异常）
        self.assertEqual(events[-1], engine.win32con.MOUSEEVENTF_LEFTUP)


if __name__ == "__main__":
    unittest.main()
