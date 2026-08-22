import pathlib
import sys
import tempfile
import os
import unittest
from unittest import mock

PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from script_store import ScriptStore
import generic_script_model as gm


FAKE_FRAME = object()
FAKE_TEMPLATE = object()


class GenericScriptModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = ScriptStore(os.path.join(self.tmp, "scripts"))
        self.model = gm.GenericScriptModel(self.store)

    # ---- B: pure dict/list roundtrip ----
    def test_model_roundtrip(self):
        actions = [
            {"type": "key", "key": "w", "hold_seconds": 0.06},
            {"type": "click", "x": 0.5, "y": 0.4},
            {"type": "wait", "seconds": 1.0},
        ]
        self.model.new("脚本", actions)
        self.model.save()
        other = gm.GenericScriptModel(self.store)
        other.load(self.model.script_id)
        self.assertEqual(other.actions, actions)
        self.assertEqual(other.name, "脚本")
        self.assertFalse(other.dirty)

    # ---- C: param modify persists on save ----
    def test_set_action_persists(self):
        self.model.new("A", [{"type": "wait", "seconds": 0.1}])
        self.model.save()
        self.model.set_action([0], {"type": "wait", "seconds": 5.0})
        self.assertTrue(self.model.dirty)
        self.model.save()
        loaded = self.store.load_script(self.model.script_id)
        self.assertEqual(loaded["actions"][0]["seconds"], 5.0)

    # ---- D: nested roundtrip ----
    def test_if_image_nested_roundtrip(self):
        self.model.new("A", [{
            "type": "if_image", "template": "assets/a.png", "threshold": 0.9,
            "then": [{"type": "click", "x": 0.1, "y": 0.2}],
            "else": [{"type": "wait", "seconds": 2.0}],
        }])
        self.model.save()
        other = gm.GenericScriptModel(self.store)
        other.load(self.model.script_id)
        self.assertEqual(other.actions[0]["then"], [{"type": "click", "x": 0.1, "y": 0.2}])
        self.assertEqual(other.actions[0]["else"], [{"type": "wait", "seconds": 2.0}])

    def test_repeat_nested_roundtrip(self):
        self.model.new("A", [{
            "type": "repeat", "count": 3,
            "actions": [{"type": "find_image", "template": "assets/b.png"}],
        }])
        self.model.save()
        other = gm.GenericScriptModel(self.store)
        other.load(self.model.script_id)
        self.assertEqual(other.actions[0]["actions"],
                         [{"type": "find_image", "template": "assets/b.png"}])

    # ---- E: dirty tracking ----
    def test_dirty_on_operations(self):
        self.model.new("A", [{"type": "key", "key": "w"}])
        self.model.save()
        self.assertFalse(self.model.dirty)

        self.model.set_action([0], {"type": "key", "key": "a"})
        self.assertTrue(self.model.dirty)
        self.model.save()

        self.model.insert_action([], {"type": "wait", "seconds": 0.1})
        self.assertTrue(self.model.dirty)
        self.model.save()

        self.model.remove_action([1])
        self.assertTrue(self.model.dirty)
        self.model.save()

    def test_dirty_on_move_and_duplicate(self):
        self.model.new("A", [{"type": "key", "key": "1"}, {"type": "key", "key": "2"}])
        self.model.save()
        self.assertFalse(self.model.dirty)
        self.model.move_action([0], 1)  # [1,2] -> [2,1]
        self.assertTrue(self.model.dirty)
        self.model.save()
        self.assertEqual([a["key"] for a in self.model.actions], ["2", "1"])
        self.model.duplicate_action([0])  # [2,1] -> [2,2,1]
        self.assertTrue(self.model.dirty)
        self.model.save()
        self.assertEqual([a["key"] for a in self.model.actions], ["2", "2", "1"])

    def test_dirty_on_nested_change(self):
        self.model.new("A", [{"type": "if_image", "template": "x", "then": [], "else": []}])
        self.model.save()
        self.assertFalse(self.model.dirty)
        # insert into nested then list
        self.model.insert_action([0, "then"], {"type": "click", "x": 0.5, "y": 0.5})
        self.assertTrue(self.model.dirty)

    # ---- F: unsaved save / discard ----
    def test_save_clears_dirty(self):
        self.model.new("A", [])
        self.assertTrue(self.model.dirty)
        self.model.save()
        self.assertFalse(self.model.dirty)

    def test_discard_reverts(self):
        self.model.new("A", [{"type": "wait", "seconds": 0.1}])
        self.model.save()
        self.model.set_action([0], {"type": "wait", "seconds": 9.9})
        self.assertTrue(self.model.dirty)
        self.model.discard()
        self.assertFalse(self.model.dirty)
        self.assertEqual(self.model.actions[0]["seconds"], 0.1)

    # ---- G: template path relative ----
    def test_template_rel_path_is_relative(self):
        self.model.new("A")
        self.model.save()
        rel = self.model.template_rel_path("foo.png")
        self.assertEqual(rel, "assets/foo.png")
        self.assertFalse(os.path.isabs(rel))

    def test_resolve_template_abs(self):
        self.model.new("A")
        self.model.save()
        abs_path = self.model.resolve_template_abs("assets/foo.png")
        self.assertTrue(abs_path.startswith(self.store.script_dir(self.model.script_id)))
        self.assertTrue(abs_path.endswith(os.path.join("assets", "foo.png")))

    # ---- I: live test helper triggers fresh capture ----
    def test_test_find_image_fresh_capture(self):
        self.model.new("A")
        self.model.save()
        captures = []

        def fake_capture(hwnd):
            captures.append(hwnd)
            return FAKE_FRAME

        with mock.patch.object(gm.engine, "capture_window", side_effect=fake_capture), \
             mock.patch.object(gm.cv2, "imread", return_value=FAKE_TEMPLATE), \
             mock.patch.object(gm.vision, "load_template_click_anchor", return_value=(0.5, 0.5)), \
             mock.patch.object(gm.vision, "analyze_template_match",
                               return_value={"matched": True, "confidence": 0.99,
                                             "relative_x": 0.5, "relative_y": 0.5}):
            self.model.test_find_image(123, "assets/foo.png")
            self.model.test_find_image(123, "assets/foo.png")
        self.assertEqual(len(captures), 2)  # two calls -> two fresh captures


class GenericRunnerControllerTests(unittest.TestCase):
    def test_start_runs_worker_and_logs_via_queue(self):
        called = []
        entered = gm.threading.Event()
        release = gm.threading.Event()

        def fake_run(hwnd, actions, base_dir, stop_event=None, log_callback=None):
            called.append((hwnd, actions, base_dir, stop_event))
            if log_callback:
                log_callback("hello from runner")
            entered.set()
            release.wait(timeout=3)
            return True

        c = gm.GenericRunnerController()
        patcher = mock.patch.object(gm.script_runner, "run_script", side_effect=fake_run)
        patcher.start()
        try:
            ok = c.start(123, [{"type": "key", "key": "w"}], "/base")
            self.assertTrue(ok)
            self.assertTrue(entered.wait(timeout=3))
            self.assertTrue(c.is_running())
            release.set()
            c.thread.join(timeout=3)
        finally:
            patcher.stop()
        self.assertFalse(c.is_running())
        self.assertEqual(called[0][0], 123)
        self.assertEqual(called[0][1], [{"type": "key", "key": "w"}])
        self.assertEqual(called[0][2], "/base")
        self.assertIn("hello from runner", c.drain_logs())

    def test_stop_sets_event(self):
        c = gm.GenericRunnerController()
        self.assertFalse(c.stop_event.is_set())
        c.stop()
        self.assertTrue(c.stop_event.is_set())

    def test_start_while_running_returns_false(self):
        c = gm.GenericRunnerController()
        block = gm.threading.Event()

        def fake_run(*a, **k):
            block.wait(timeout=3)

        patcher = mock.patch.object(gm.script_runner, "run_script", side_effect=fake_run)
        patcher.start()
        try:
            self.assertTrue(c.start(1, [], "/b"))
            self.assertFalse(c.start(1, [], "/b"))  # already running
            block.set()
            c.thread.join(timeout=3)
        finally:
            patcher.stop()


if __name__ == "__main__":
    unittest.main()
