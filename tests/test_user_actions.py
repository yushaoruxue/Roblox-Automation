import pathlib
import sys
import tempfile
import os
import unittest

PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import user_actions as ua
from script_store import ScriptStore
from generic_script_model import GenericScriptModel


class CompileTests(unittest.TestCase):
    # A: key_click -> key + click + wait
    def test_key_click_compiles_to_key_click_wait(self):
        a = ua.new_action("key_click")
        a.update({"key": "1", "hold_seconds": 0.06, "x": 0.535, "y": 0.475, "after_wait": 0.5})
        out = ua.compile_user_actions([a])
        self.assertEqual(out, [
            {"type": "key", "key": "1", "hold_seconds": 0.06},
            {"type": "click", "x": 0.535, "y": 0.475},
            {"type": "wait", "seconds": 0.5},
        ])

    # B: after_wait=0 -> no wait
    def test_after_wait_zero_emits_no_wait(self):
        a = ua.new_action("key")
        a["after_wait"] = 0
        out = ua.compile_user_actions([a])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], "key")

    # C: click_image after_wait -> click_image + wait
    def test_click_image_after_wait(self):
        a = ua.new_action("click_image")
        a.update({"template": "assets/r.png", "threshold": 0.9, "after_wait": 0.3})
        out = ua.compile_user_actions([a])
        self.assertEqual(out, [
            {"type": "click_image", "template": "assets/r.png", "threshold": 0.9},
            {"type": "wait", "seconds": 0.3},
        ])

    # D: group inlines children in order
    def test_group_expands_children(self):
        g = ua.new_action("group")
        g["actions"] = [ua.new_action("key"), ua.new_action("click"), ua.new_action("wait")]
        out = ua.compile_user_actions([g])
        # key+wait, click+wait, wait (新默认 after_wait=0.2 自动追加 wait)
        self.assertEqual([x["type"] for x in out],
                         ["key", "wait", "click", "wait", "wait"])

    # E: nested compile structure
    def test_nested_if_repeat_group(self):
        ifa = ua.new_action("if_image")
        ifa.update({"template": "assets/v.png", "then": [ua.new_action("click")],
                    "else": [ua.new_action("wait")]})
        rep = ua.new_action("repeat")
        rep["count"] = 3
        rep["actions"] = [ua.new_action("find_image")]
        grp = ua.new_action("group")
        grp["actions"] = [ua.new_action("key_click")]
        out = ua.compile_user_actions([ifa, rep, grp])
        self.assertEqual(out[0]["type"], "if_image")
        self.assertEqual(out[0]["then"], [{"type": "click", "x": 0.5, "y": 0.5},
                                          {"type": "wait", "seconds": 0.2}])
        self.assertEqual(out[0]["else"], [{"type": "wait", "seconds": 0.2}])
        self.assertEqual(out[1]["type"], "repeat")
        self.assertEqual(out[1]["count"], 3)
        self.assertEqual(out[1]["actions"][0]["type"], "find_image")
        # group inlines key_click -> key + click + wait (after_wait default 0.5)
        self.assertEqual([x["type"] for x in out[2:]], ["key", "click", "wait"])

    # F: summary
    def test_summaries(self):
        self.assertIn("按键后点击", ua.action_summary(ua.new_action("key_click")))
        self.assertIn("点击图片", ua.action_summary(ua.new_action("click_image")))
        self.assertIn("如果图片", ua.action_summary(ua.new_action("if_image")))
        self.assertIn("重复", ua.action_summary(ua.new_action("repeat")))
        self.assertIn("动作组", ua.action_summary(ua.new_action("group")))


class ModelRoundtripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = ScriptStore(os.path.join(self.tmp, "scripts"))

    # G: save/load roundtrip (user actions, no field loss)
    def test_user_action_roundtrip(self):
        model = GenericScriptModel(self.store)
        actions = [
            ua.new_action("key_click"),
            {"type": "group", "name": "初始化",
             "actions": [{"type": "if_image", "template": "a.png", "threshold": 0.9,
                          "then": [{"type": "click", "x": 0.1, "y": 0.2}], "else": []}]},
        ]
        model.new("A", actions)
        model.save()
        other = GenericScriptModel(self.store)
        other.load(model.script_id)
        self.assertEqual(other.actions[0]["type"], "key_click")
        self.assertEqual(other.actions[0]["after_wait"], 0.2)
        self.assertEqual(other.actions[1]["type"], "group")
        self.assertEqual(other.actions[1]["actions"][0]["then"][0],
                         {"type": "click", "x": 0.1, "y": 0.2})


# ---- key_hold / key_release 编译与摘要 ----
class KeyHoldReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = ScriptStore(os.path.join(self.tmp, "scripts"))

    def test_key_hold_compiles_to_key_down(self):
        a = ua.new_action("key_hold")
        a["key"] = "shift"
        out = ua.compile_user_actions([a])
        self.assertEqual(out, [{"type": "key_down", "key": "shift"}])

    def test_key_release_compiles_to_key_up(self):
        a = ua.new_action("key_release")
        a["key"] = "shift"
        out = ua.compile_user_actions([a])
        self.assertEqual(out, [{"type": "key_up", "key": "shift"}])

    def test_key_hold_release_have_no_after_wait(self):
        self.assertNotIn("after_wait", ua.ACTION_TEMPLATES["key_hold"])
        self.assertNotIn("after_wait", ua.ACTION_TEMPLATES["key_release"])

    def test_summaries(self):
        self.assertEqual(ua.action_summary(ua.new_action("key_hold")), "按住 [shift]")
        self.assertEqual(ua.action_summary(ua.new_action("key_release")), "松开 [shift]")

    def test_validate_rejects_empty_key(self):
        for t in ("key_hold", "key_release"):
            a = ua.new_action(t)
            a["key"] = ""
            with self.assertRaises(ValueError):
                ua.validate_user_actions([a])

    # H: dirty on new/nested edit
    def test_dirty_on_user_actions(self):
        model = GenericScriptModel(self.store)
        model.new("A", [ua.new_action("key_click")])
        model.save()
        self.assertFalse(model.dirty)
        model.insert_action([], ua.new_action("group"))
        self.assertTrue(model.dirty)
        model.save()
        # nested edit inside group
        model.insert_action([1, "actions"], ua.new_action("click"))
        self.assertTrue(model.dirty)


class ValidateTests(unittest.TestCase):
    def test_validate_rejects_bad_type(self):
        with self.assertRaises(ValueError):
            ua.validate_user_actions([{"type": "bogus"}])

    def test_validate_accepts_all_v01(self):
        actions = [ua.new_action(t) for t in ua.ACTION_TEMPLATES]
        ua.validate_user_actions(actions)

    def test_validate_key_click_needs_coords(self):
        a = ua.new_action("key_click")
        a.pop("x")
        with self.assertRaises(ValueError):
            ua.validate_user_actions([a])


if __name__ == "__main__":
    unittest.main()
