import json
import pathlib
import sys
import tempfile
import unittest


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from profile_store import ProfileStore, ProfileStoreError


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        self.legacy = self.root / "config.json"
        self.legacy_steps = [
            {"key": "2", "rx": 0.25, "ry": 0.75, "delay": 0.4},
        ]
        self.legacy.write_text(
            json.dumps(self.legacy_steps),
            encoding="utf-8",
        )
        self.store = ProfileStore(self.root / "profiles", self.legacy)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_legacy_config_is_imported_without_modifying_source(self):
        profile = self.store.load_profile(self.store.active_profile_id())
        self.assertTrue(self.store.migration_performed)
        self.assertEqual(profile["name"], "默认方案")
        self.assertEqual(profile["steps"], self.legacy_steps)
        self.assertEqual(
            json.loads(self.legacy.read_text(encoding="utf-8")),
            self.legacy_steps,
        )

    def test_create_save_rename_and_duplicate(self):
        original_id = self.store.active_profile_id()
        created = self.store.create_profile("地图二")
        self.assertEqual(self.store.active_profile_id(), created["id"])

        changed_steps = [
            {"key": "3", "rx": 0.6, "ry": 0.4, "delay": 1.2},
        ]
        self.store.save_steps(created["id"], changed_steps)
        renamed = self.store.rename_profile(created["id"], "地图二夜间")
        duplicate = self.store.duplicate_profile(
            created["id"],
            "地图二副本",
        )

        self.assertEqual(renamed["name"], "地图二夜间")
        self.assertEqual(duplicate["steps"], changed_steps)
        self.assertEqual(
            [profile["id"] for profile in self.store.list_profiles()],
            [original_id, created["id"], duplicate["id"]],
        )

    def test_duplicate_names_are_rejected_case_insensitively(self):
        self.store.create_profile("Boss")
        with self.assertRaises(ProfileStoreError):
            self.store.create_profile("boss")

    def test_delete_moves_profile_to_recoverable_trash(self):
        original_id = self.store.active_profile_id()
        created = self.store.create_profile("临时方案")
        active_id, trash_path = self.store.delete_profile(created["id"])

        self.assertEqual(active_id, original_id)
        self.assertTrue(pathlib.Path(trash_path).exists())
        self.assertFalse(
            (self.root / "profiles" / created["id"]).exists()
        )

    def test_last_profile_cannot_be_deleted(self):
        with self.assertRaises(ProfileStoreError):
            self.store.delete_profile(self.store.active_profile_id())

    def test_preview_bytes_and_metadata_are_kept_with_profile(self):
        profile_id = self.store.active_profile_id()
        payload = b"fake-jpeg-data"
        preview_path = self.store.set_preview(profile_id, payload, 1200, 900)
        profile = self.store.load_profile(profile_id)

        self.assertEqual(pathlib.Path(preview_path).read_bytes(), payload)
        self.assertEqual(profile["preview"]["width"], 1200)
        self.assertEqual(profile["preview"]["height"], 900)
        self.assertEqual(self.store.preview_path(profile_id), preview_path)

    def test_corrupt_current_index_recovers_from_backup(self):
        original_id = self.store.active_profile_id()
        self.store.create_profile("可恢复方案")
        index_path = self.root / "profiles" / "index.json"
        index_path.write_text("{broken", encoding="utf-8")

        reopened = ProfileStore(self.root / "profiles", self.legacy)

        self.assertEqual(reopened.active_profile_id(), original_id)
        self.assertEqual(
            [profile["name"] for profile in reopened.list_profiles()],
            ["默认方案"],
        )

    def test_invalid_legacy_config_is_not_silently_replaced(self):
        invalid_root = self.root / "invalid"
        invalid_root.mkdir()
        invalid_legacy = invalid_root / "config.json"
        invalid_legacy.write_text('{"not": "steps"}', encoding="utf-8")

        with self.assertRaises(ProfileStoreError):
            ProfileStore(invalid_root / "profiles", invalid_legacy)

        self.assertFalse((invalid_root / "profiles" / "index.json").exists())


if __name__ == "__main__":
    unittest.main()
