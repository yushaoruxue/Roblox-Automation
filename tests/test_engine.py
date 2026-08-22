import pathlib
import sys
import unittest
from unittest import mock


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import engine


class CoordinateTests(unittest.TestCase):
    def test_center_coordinate(self):
        self.assertEqual(
            engine.normalized_to_client_point(1219, 952, 0.5, 0.5),
            (609, 476),
        )

    def test_edges_stay_inside_client_area(self):
        self.assertEqual(engine.normalized_to_client_point(100, 50, 0, 0), (0, 0))
        self.assertEqual(engine.normalized_to_client_point(100, 50, 1, 1), (99, 49))

    def test_out_of_range_coordinate_is_rejected(self):
        for rx, ry in [(-0.01, 0.5), (1.01, 0.5), (0.5, -1), (0.5, 2)]:
            with self.subTest(rx=rx, ry=ry):
                with self.assertRaises(ValueError):
                    engine.normalized_to_client_point(100, 100, rx, ry)

    def test_relative_to_screen_uses_checked_client_point(self):
        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine.win32gui, "GetClientRect", return_value=(0, 0, 100, 50)),
            mock.patch.object(
                engine.win32gui,
                "ClientToScreen",
                side_effect=lambda hwnd, point: (point[0] + 1000, point[1] + 200),
            ),
        ):
            self.assertEqual(
                engine.relative_to_screen(123, 0.5, 0.5),
                (50, 24, 1050, 224, 100, 50),
            )


class SafetyTests(unittest.TestCase):
    def test_game_mouse_move_includes_relative_nudge(self):
        with (
            mock.patch.object(engine.win32api, "SetCursorPos") as set_pos,
            mock.patch.object(engine.win32api, "mouse_event") as mouse_event,
            mock.patch.object(engine.win32gui, "GetCursorPos", return_value=(500, 300)),
            mock.patch.object(engine.time, "sleep"),
        ):
            result = engine._move_and_verify(500, 300)
        self.assertEqual(result, (True, 500, 300))
        set_pos.assert_called_once_with((500, 300))
        self.assertEqual(
            mouse_event.call_args_list,
            [
                mock.call(engine.win32con.MOUSEEVENTF_MOVE, 1, 0, 0, 0),
                mock.call(engine.win32con.MOUSEEVENTF_MOVE, -1, 0, 0, 0),
            ],
        )

    def test_invalid_step_aborts_before_input(self):
        messages = []
        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine.win32gui, "GetClientRect", return_value=(0, 0, 100, 100)),
            mock.patch.object(engine, "_press_key_held") as press_key,
            mock.patch.object(engine, "_set_physical_input_blocked", return_value=True),
        ):
            result = engine.run_action_sequence(
                123,
                [{"key": "1", "rx": -0.2, "ry": 0.5}],
                log_callback=messages.append,
            )
        self.assertFalse(result)
        press_key.assert_not_called()
        self.assertTrue(any("越界" in message for message in messages))

    def test_busy_action_lock_rejects_second_sequence(self):
        messages = []
        engine._ACTION_LOCK.acquire()
        try:
            result = engine.run_action_sequence(123, [], log_callback=messages.append)
        finally:
            engine._ACTION_LOCK.release()
        self.assertFalse(result)
        self.assertTrue(any("已有动作序列" in message for message in messages))

    def test_foreground_is_verified(self):
        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine.win32gui, "GetForegroundWindow", return_value=123),
            mock.patch.object(engine.win32gui, "SetForegroundWindow") as set_foreground,
        ):
            self.assertTrue(engine.force_foreground(123))
        set_foreground.assert_not_called()

    def test_valid_sequence_runs_once_and_completes(self):
        messages = []
        point = (50, 25, 1050, 225, 100, 50)
        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine.win32gui, "GetForegroundWindow", return_value=123),
            mock.patch.object(engine.win32gui, "GetCursorPos", return_value=(10, 20)),
            mock.patch.object(engine, "relative_to_screen", return_value=point),
            mock.patch.object(engine, "force_foreground", return_value=True),
            mock.patch.object(engine, "_press_key_held", return_value=True) as press_key,
            mock.patch.object(engine, "_move_and_verify", return_value=(True, 1050, 225)),
            mock.patch.object(engine, "_point_hits_window", return_value=(True, 123, 123)),
            mock.patch.object(engine, "_click_current_position") as click,
            mock.patch.object(
                engine, "_set_physical_input_blocked", return_value=True
            ) as block_input,
            mock.patch.object(engine.win32api, "SetCursorPos"),
            mock.patch.object(engine.time, "sleep"),
        ):
            result = engine.run_action_sequence(
                123,
                [{"key": "3", "rx": 0.5, "ry": 0.5, "delay": 0}],
                log_callback=messages.append,
            )
        self.assertTrue(result)
        self.assertEqual(
            press_key.call_args_list,
            [mock.call("z", 0.05), mock.call("3", 0.06)],
        )
        click.assert_called_once_with()
        self.assertEqual(
            block_input.call_args_list,
            [mock.call(True), mock.call(False)],
        )
        self.assertTrue(any("screen=(1050,225)" in message for message in messages))
        self.assertTrue(any("confirm=BetterClick" in message for message in messages))
        self.assertTrue(any("锁定物理键盘和鼠标" in message for message in messages))

    def test_start_button_click_is_protected_by_physical_input_lock(self):
        messages = []
        point = (60, 20, 1060, 220, 100, 50)
        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine.win32gui, "GetForegroundWindow", return_value=123),
            mock.patch.object(engine.win32gui, "GetCursorPos", return_value=(10, 20)),
            mock.patch.object(engine, "relative_to_screen", return_value=point),
            mock.patch.object(engine, "force_foreground", return_value=True),
            mock.patch.object(
                engine,
                "_move_and_verify",
                return_value=(True, 1060, 220),
            ),
            mock.patch.object(
                engine,
                "_point_hits_window",
                return_value=(True, 123, 123),
            ),
            mock.patch.object(engine, "_click_current_position") as click,
            mock.patch.object(
                engine,
                "_set_physical_input_blocked",
                return_value=True,
            ) as block_input,
            mock.patch.object(engine.win32api, "SetCursorPos"),
            mock.patch.object(engine.time, "sleep"),
        ):
            result = engine.run_action_sequence(
                123,
                [],
                start_click_rx=0.6,
                start_click_ry=0.4,
                log_callback=messages.append,
            )

        self.assertTrue(result)
        click.assert_called_once_with()
        self.assertEqual(
            block_input.call_args_list,
            [mock.call(True), mock.call(False)],
        )
        self.assertTrue(
            any(
                "已点击开始按钮" in message
                and "physical_lock=keyboard+mouse" in message
                for message in messages
            )
        )

    def test_start_button_lock_is_released_when_cursor_move_fails(self):
        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine.win32gui, "GetForegroundWindow", return_value=123),
            mock.patch.object(engine.win32gui, "GetCursorPos", return_value=(10, 20)),
            mock.patch.object(
                engine,
                "relative_to_screen",
                return_value=(60, 20, 1060, 220, 100, 50),
            ),
            mock.patch.object(engine, "force_foreground", return_value=True),
            mock.patch.object(
                engine,
                "_move_and_verify",
                return_value=(False, 900, 300),
            ),
            mock.patch.object(
                engine,
                "_set_physical_input_blocked",
                return_value=True,
            ) as block_input,
            mock.patch.object(engine.win32api, "SetCursorPos"),
            mock.patch.object(engine.time, "sleep"),
        ):
            result = engine.run_action_sequence(
                123,
                [],
                start_click_rx=0.6,
                start_click_ry=0.4,
            )

        self.assertFalse(result)
        self.assertEqual(
            block_input.call_args_list,
            [mock.call(True), mock.call(False)],
        )

    def test_sequence_aborts_before_focus_when_keyboard_mouse_lock_fails(self):
        messages = []
        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine.win32gui, "GetForegroundWindow", return_value=456),
            mock.patch.object(engine.win32gui, "GetCursorPos", return_value=(10, 20)),
            mock.patch.object(
                engine,
                "_set_physical_input_blocked",
                side_effect=[False, False],
            ) as block_input,
            mock.patch.object(engine, "force_foreground") as foreground,
            mock.patch.object(engine, "_press_key_held") as press_key,
            mock.patch.object(engine.win32api, "SetCursorPos"),
        ):
            result = engine.run_action_sequence(123, [], log_callback=messages.append)

        self.assertFalse(result)
        foreground.assert_not_called()
        press_key.assert_not_called()
        self.assertEqual(
            block_input.call_args_list,
            [mock.call(True), mock.call(False)],
        )
        self.assertTrue(any("已取消动作序列" in message for message in messages))

    def test_keyboard_mouse_unlock_happens_after_original_focus_restore(self):
        events = []

        def block_input(value):
            events.append(("block", value))
            return True

        def focus(hwnd, **_kwargs):
            events.append(("focus", hwnd))
            return True

        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(engine.win32gui, "GetForegroundWindow", return_value=456),
            mock.patch.object(engine.win32gui, "GetCursorPos", return_value=(10, 20)),
            mock.patch.object(
                engine,
                "_set_physical_input_blocked",
                side_effect=block_input,
            ),
            mock.patch.object(engine, "force_foreground", side_effect=focus),
            mock.patch.object(engine.win32api, "SetCursorPos"),
            mock.patch.object(engine.time, "sleep"),
        ):
            result = engine.run_action_sequence(123, [])

        self.assertTrue(result)
        self.assertEqual(
            events,
            [
                ("block", True),
                ("focus", 123),
                ("focus", 456),
                ("block", False),
            ],
        )


class WindowDiscoveryTests(unittest.TestCase):
    def test_non_game_roblox_titles_are_excluded(self):
        titles = {
            1: "Roblox",
            2: "Roblox_AE_Automation - 文件资源管理器",
            3: "Other Game",
        }
        process_info = {
            1: (101, r"C:\Roblox\RobloxPlayerBeta.exe"),
            2: (102, r"C:\Windows\explorer.exe"),
            3: (103, r"C:\Games\OtherGame.exe"),
        }

        def enum_windows(callback, extra):
            for hwnd in titles:
                callback(hwnd, extra)

        with (
            mock.patch.object(engine.win32gui, "EnumWindows", side_effect=enum_windows),
            mock.patch.object(engine.win32gui, "IsWindowVisible", return_value=True),
            mock.patch.object(engine.win32gui, "GetWindowText", side_effect=lambda hwnd: titles[hwnd]),
            mock.patch.object(
                engine,
                "get_window_process_info",
                side_effect=lambda hwnd: process_info[hwnd],
            ),
        ):
            self.assertEqual(engine.find_roblox_hwnd(), [(1, "Roblox")])

    def test_process_resource_usage_returns_private_commit_and_runtime(self):
        fake_process = mock.Mock()
        fake_creation = mock.Mock()
        fake_creation.timestamp.return_value = 1000.0
        with (
            mock.patch.object(engine.win32gui, "IsWindow", return_value=True),
            mock.patch.object(
                engine.win32process,
                "GetWindowThreadProcessId",
                return_value=(1, 456),
            ),
            mock.patch.object(
                engine.win32api,
                "OpenProcess",
                return_value=fake_process,
            ),
            mock.patch.object(
                engine.win32process,
                "GetProcessMemoryInfo",
                return_value={
                    "WorkingSetSize": 2 * 1024 ** 3,
                    "PagefileUsage": 4 * 1024 ** 3,
                },
            ),
            mock.patch.object(
                engine.win32process,
                "GetProcessTimes",
                return_value={"CreationTime": fake_creation},
            ),
            mock.patch.object(engine.time, "time", return_value=8200.0),
        ):
            usage = engine.get_process_resource_usage(123)

        self.assertEqual(usage["pid"], 456)
        self.assertEqual(usage["working_set_bytes"], 2 * 1024 ** 3)
        self.assertEqual(usage["private_commit_bytes"], 4 * 1024 ** 3)
        self.assertEqual(usage["runtime_seconds"], 7200.0)
        fake_process.Close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
