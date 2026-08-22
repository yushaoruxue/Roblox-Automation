import pathlib
import sys
import tempfile
import os
import unittest

PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from script_store import ScriptStore, ScriptStoreError


class ScriptStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = ScriptStore(os.path.join(self.tmp, "scripts"))

    def test_create_and_list(self):
        s = self.store.create_script("A", [{"type": "key", "key": "w"}])
        self.assertTrue(s["id"])
        self.assertEqual(s["name"], "A")
        self.assertEqual(s["actions"], [{"type": "key", "key": "w"}])
        names = [x["name"] for x in self.store.list_scripts()]
        self.assertEqual(names, ["A"])

    def test_save_load_roundtrip(self):
        s = self.store.create_script("A", [])
        self.store.save_actions(s["id"], [{"type": "wait", "seconds": 1.0}])
        loaded = self.store.load_script(s["id"])
        self.assertEqual(loaded["actions"], [{"type": "wait", "seconds": 1.0}])

    def test_rename(self):
        s = self.store.create_script("A")
        self.store.rename_script(s["id"], "B")
        self.assertEqual(self.store.load_script(s["id"])["name"], "B")

    def test_delete_moves_to_trash(self):
        s = self.store.create_script("A")
        trash = self.store.delete_script(s["id"])
        self.assertTrue(os.path.exists(trash))
        self.assertEqual(self.store.list_scripts(), [])

    def test_duplicate_name_rejected(self):
        self.store.create_script("A")
        with self.assertRaises(ScriptStoreError):
            self.store.create_script("a")  # case-insensitive duplicate

    def test_atomic_write_creates_backup(self):
        s = self.store.create_script("A", [])
        self.store.save_actions(s["id"], [{"type": "key", "key": "z"}])
        script_path = os.path.join(self.store.script_dir(s["id"]), "script.json")
        self.assertTrue(os.path.exists(script_path + ".bak"))

    def test_corrupt_primary_falls_back_to_backup(self):
        s = self.store.create_script("A", [{"type": "key", "key": "w"}])
        # a second write creates the .bak that the fallback relies on
        self.store.save_actions(s["id"], [{"type": "key", "key": "w"}])
        script_path = os.path.join(self.store.script_dir(s["id"]), "script.json")
        # corrupt the primary, leaving the .bak intact
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        loaded = self.store.load_script(s["id"])
        self.assertEqual(loaded["actions"], [{"type": "key", "key": "w"}])

    def test_write_template_rejects_absolute_path(self):
        s = self.store.create_script("A")
        with self.assertRaises(ScriptStoreError):
            self.store.write_template(s["id"], "D:/abs/foo.png", b"data")
        with self.assertRaises(ScriptStoreError):
            self.store.write_template(s["id"], "../foo.png", b"data")

    def test_write_template_lands_in_assets(self):
        s = self.store.create_script("A")
        abs_path = self.store.write_template(s["id"], "assets/foo.png", b"PNGDATA")
        self.assertTrue(abs_path.startswith(self.store.script_dir(s["id"])))
        self.assertTrue(os.path.exists(abs_path))


if __name__ == "__main__":
    unittest.main()
