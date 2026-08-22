import pathlib
import sys
import unittest
from unittest import mock


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import actions
import engine


class CompileLegacyStepsTests(unittest.TestCase):
    def test_compile_single_step_exact(self):
        out = actions.compile_legacy_steps([{"key": "1", "rx": 0.561, "ry": 0.553, "delay": 0.1}])
        self.assertEqual(out, [
            {"type": "key", "key": "z", "hold_seconds": 0.05},
            {"type": "key", "key": "1", "hold_seconds": 0.06},
            {"type": "click", "x": 0.561, "y": 0.553},
            {"type": "wait", "seconds": 0.1},
        ])

    def test_compile_multi_step_order(self):
        out = actions.compile_legacy_steps([
            {"key": "1", "rx": 0.1, "ry": 0.1, "delay": 0.1},
            {"key": "2", "rx": 0.2, "ry": 0.2, "delay": 0.2},
        ])
        keys = [a["key"] for a in out if a["type"] == "key"]
        self.assertEqual(keys, ["z", "1", "z", "2"])
        clicks = [(a["x"], a["y"]) for a in out if a["type"] == "click"]
        self.assertEqual(clicks, [(0.1, 0.1), (0.2, 0.2)])

    def test_compile_start_click_appended(self):
        out = actions.compile_legacy_steps(
            [{"key": "1", "rx": 0.5, "ry": 0.5, "delay": 0}],
            start_click_rx=0.6,
            start_click_ry=0.4,
        )
        self.assertEqual(out[-1], {"type": "click", "x": 0.6, "y": 0.4, "label": "start"})


class ValidateActionsTests(unittest.TestCase):
    def test_accepts_generic_keys(self):
        # w / space / z must all be valid (no 1..6 restriction in generic core).
        actions.validate_actions([
            {"type": "key", "key": "w", "hold_seconds": 0.1},
            {"type": "key", "key": "space", "hold_seconds": 0.1},
            {"type": "key", "key": "z", "hold_seconds": 0.1},
        ])

    def test_rejects_invalid_actions(self):
        cases = [
            [{"type": "bogus"}],
            [{"type": "key", "key": ""}],
            [{"type": "key", "key": "a", "hold_seconds": -1}],
            [{"type": "click", "x": 1.5, "y": 0.5}],
            [{"type": "click", "x": 0.5}],
            [{"type": "wait", "seconds": -1}],
        ]
        for acts in cases:
            with self.subTest(acts=acts):
                with self.assertRaises(ValueError):
                    actions.validate_actions(acts)


class GenericExecutorTests(unittest.TestCase):
    def test_accepts_generic_keys(self):
        pressed = []

        def key(k, hold):
            pressed.append((k, hold))
            return True

        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine.win32gui, "GetForegroundWindow", return_value=123),
            mock.patch.object(engine.win32gui, "GetCursorPos", return_value=(0, 0)),
            mock.patch.object(engine, "_set_physical_input_blocked", return_value=True),
            mock.patch.object(engine, "force_foreground", return_value=True),
            mock.patch.object(engine, "_press_key_held", side_effect=key),
            mock.patch.object(engine.win32api, "SetCursorPos"),
            mock.patch.object(engine.time, "sleep"),
        ):
            result = engine.run_input_actions(123, [
                {"type": "key", "key": "w", "hold_seconds": 0.1},
                {"type": "key", "key": "space", "hold_seconds": 0.1},
                {"type": "key", "key": "z", "hold_seconds": 0.1},
            ])

        self.assertTrue(result)
        self.assertEqual(pressed, [("w", 0.1), ("space", 0.1), ("z", 0.1)])

    def test_invalid_action_rejected_before_blockinput(self):
        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine, "_set_physical_input_blocked") as block_input,
        ):
            result = engine.run_input_actions(123, [{"type": "wait", "seconds": -1}])

        self.assertFalse(result)
        # The lock (BlockInput(True)) must never be attempted for an invalid
        # action; the finally's unconditional BlockInput(False) is the safety
        # unlock and is expected.
        self.assertNotIn(mock.call(True), block_input.call_args_list)

    def test_session_cleanup_on_second_action_failure(self):
        events = []
        fg_state = [456]

        def get_fg():
            return fg_state[0]

        def block(v):
            events.append(("block", v))
            return True

        def focus(hwnd, **_kwargs):
            events.append(("focus", hwnd))
            fg_state[0] = hwnd
            return True

        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine.win32gui, "GetForegroundWindow", side_effect=get_fg),
            mock.patch.object(engine.win32gui, "GetCursorPos", return_value=(10, 20)),
            mock.patch.object(engine, "_set_physical_input_blocked", side_effect=block),
            mock.patch.object(engine, "force_foreground", side_effect=focus),
            mock.patch.object(engine, "_press_key_held", side_effect=[True, False]),
            mock.patch.object(engine.win32api, "SetCursorPos"),
            mock.patch.object(engine.time, "sleep"),
        ):
            result = engine.run_input_actions(123, [
                {"type": "key", "key": "z", "hold_seconds": 0.05},
                {"type": "key", "key": "w", "hold_seconds": 0.06},
            ])

        self.assertFalse(result)
        self.assertEqual(
            events,
            [("block", True), ("focus", 123), ("focus", 456), ("block", False)],
        )

    def test_click_uses_betterclick_primitives(self):
        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine.win32gui, "GetForegroundWindow", return_value=123),
            mock.patch.object(engine.win32gui, "GetCursorPos", return_value=(0, 0)),
            mock.patch.object(engine, "_set_physical_input_blocked", return_value=True),
            mock.patch.object(engine, "force_foreground", return_value=True),
            mock.patch.object(engine, "relative_to_screen", return_value=(50, 25, 1050, 225, 100, 50)),
            mock.patch.object(engine, "_move_and_verify", return_value=(True, 1050, 225)) as move,
            mock.patch.object(engine, "_point_hits_window", return_value=(True, 123, 123)) as phw,
            mock.patch.object(engine, "_click_current_position") as click,
            mock.patch.object(engine.win32api, "SetCursorPos"),
            mock.patch.object(engine.time, "sleep"),
        ):
            result = engine.run_input_actions(123, [{"type": "click", "x": 0.5, "y": 0.5}])

        self.assertTrue(result)
        move.assert_called_once_with(1050, 225)
        phw.assert_called_once_with(123, 1050, 225)
        click.assert_called_once_with()


class LegacyWrapperTests(unittest.TestCase):
    def test_rejects_key_7(self):
        messages = []
        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine.win32gui, "GetClientRect", return_value=(0, 0, 100, 100)),
        ):
            result = engine.run_action_sequence(
                123,
                [{"key": "7", "rx": 0.5, "ry": 0.5}],
                log_callback=messages.append,
            )
        self.assertFalse(result)
        self.assertTrue(any("按键无效" in m for m in messages))

    def test_legacy_step_full_trace(self):
        events = []
        fg_state = [456]

        def get_fg():
            return fg_state[0]

        def block(v):
            events.append(("block", v))
            return True

        def focus(hwnd, **_kwargs):
            events.append(("focus", hwnd))
            fg_state[0] = hwnd
            return True

        def key(k, hold):
            events.append(("key", k, hold))
            return True

        def move(sx, sy):
            events.append(("move", sx, sy))
            return (True, sx, sy)

        def phw(h, sx, sy):
            events.append(("hit", sx, sy))
            return (True, h, h)

        def click():
            events.append(("click",))

        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine.win32gui, "GetForegroundWindow", side_effect=get_fg),
            mock.patch.object(engine.win32gui, "GetCursorPos", return_value=(0, 0)),
            mock.patch.object(engine, "_set_physical_input_blocked", side_effect=block),
            mock.patch.object(engine, "force_foreground", side_effect=focus),
            mock.patch.object(engine, "_press_key_held", side_effect=key),
            mock.patch.object(engine, "relative_to_screen", return_value=(50, 25, 1050, 225, 100, 50)),
            mock.patch.object(engine, "_move_and_verify", side_effect=move),
            mock.patch.object(engine, "_point_hits_window", side_effect=phw),
            mock.patch.object(engine, "_click_current_position", side_effect=click),
            mock.patch.object(engine.win32api, "SetCursorPos"),
            mock.patch.object(engine.time, "sleep"),
        ):
            result = engine.run_action_sequence(
                123,
                [{"key": "1", "rx": 0.5, "ry": 0.5, "delay": 0}],
                start_click_rx=0.6,
                start_click_ry=0.4,
            )

        self.assertTrue(result)
        self.assertEqual([e for e in events if e[0] == "key"], [("key", "z", 0.05), ("key", "1", 0.06)])
        self.assertEqual([e for e in events if e[0] == "click"], [("click",), ("click",)])
        self.assertEqual([e for e in events if e[0] == "focus"], [("focus", 123), ("focus", 456)])
        self.assertEqual([e for e in events if e[0] == "block"], [("block", True), ("block", False)])


if __name__ == "__main__":
    unittest.main()
