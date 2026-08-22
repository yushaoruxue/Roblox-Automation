"""Versioned, atomic storage for deployment profiles and their preview images."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import shutil
import tempfile
import uuid


SCHEMA_VERSION = 1
DEFAULT_PROFILE_NAME = "默认方案"


class ProfileStoreError(RuntimeError):
    pass


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_steps(steps):
    normalized = []
    for index, step in enumerate(steps):
        key = str(step.get("key", "")).strip()
        if key not in {"1", "2", "3", "4", "5", "6"}:
            raise ProfileStoreError(f"步骤 {index + 1} 的单位槽无效: {key!r}")
        try:
            rx = float(step["rx"])
            ry = float(step["ry"])
            delay = float(step.get("delay", 0.5))
        except (KeyError, TypeError, ValueError) as error:
            raise ProfileStoreError(f"步骤 {index + 1} 的数据格式无效") from error
        if not (0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0):
            raise ProfileStoreError(f"步骤 {index + 1} 的坐标超出 0..1")
        if delay < 0:
            raise ProfileStoreError(f"步骤 {index + 1} 的间隔不能小于 0")
        normalized.append({"key": key, "rx": rx, "ry": ry, "delay": delay})
    return normalized


class ProfileStore:
    def __init__(self, profiles_dir, legacy_config_path=None):
        self.profiles_dir = os.path.abspath(profiles_dir)
        self.legacy_config_path = (
            os.path.abspath(legacy_config_path) if legacy_config_path else None
        )
        self.index_path = os.path.join(self.profiles_dir, "index.json")
        self.trash_dir = os.path.join(self.profiles_dir, ".trash")
        self.migration_performed = False
        os.makedirs(self.profiles_dir, exist_ok=True)
        self._ensure_initialized()

    def _atomic_write_bytes(self, path, payload):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix=".tmp-",
            dir=os.path.dirname(path),
        )
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    def _atomic_write_json(self, path, data):
        payload = json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        backup_path = path + ".bak"
        if os.path.exists(path):
            shutil.copy2(path, backup_path)
        self._atomic_write_bytes(path, payload)

    def _load_json(self, path):
        try:
            with open(path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (OSError, json.JSONDecodeError) as primary_error:
            backup_path = path + ".bak"
            try:
                with open(backup_path, "r", encoding="utf-8") as file:
                    return json.load(file)
            except (OSError, json.JSONDecodeError):
                raise ProfileStoreError(f"无法读取配置文件: {path}") from primary_error

    def _ensure_initialized(self):
        if os.path.exists(self.index_path):
            self._validate_index(self._load_json(self.index_path))
            return

        legacy_steps = None
        if self.legacy_config_path and os.path.exists(self.legacy_config_path):
            try:
                with open(self.legacy_config_path, "r", encoding="utf-8") as file:
                    legacy_data = json.load(file)
                if not isinstance(legacy_data, list):
                    raise ProfileStoreError("旧 config.json 顶层必须是步骤列表")
                legacy_steps = normalize_steps(legacy_data)
            except (OSError, json.JSONDecodeError, ProfileStoreError) as error:
                raise ProfileStoreError(
                    f"旧配置无法安全导入: {self.legacy_config_path}"
                ) from error

        if legacy_steps is None:
            legacy_steps = [{"key": "1", "rx": 0.5, "ry": 0.5, "delay": 0.5}]
        self._create_initial_profile(DEFAULT_PROFILE_NAME, legacy_steps)
        self.migration_performed = legacy_steps is not None and bool(
            self.legacy_config_path and os.path.exists(self.legacy_config_path)
        )

    def _create_initial_profile(self, name, steps):
        profile_id = uuid.uuid4().hex
        now = _now_iso()
        profile = {
            "version": SCHEMA_VERSION,
            "id": profile_id,
            "name": self._validated_name(name),
            "steps": normalize_steps(steps),
            "created_at": now,
            "updated_at": now,
            "preview": None,
        }
        self._atomic_write_json(self._profile_path(profile_id), profile)
        self._write_index(
            {
                "version": SCHEMA_VERSION,
                "active_profile_id": profile_id,
                "profile_order": [profile_id],
            }
        )

    def _validate_index(self, index):
        if index.get("version") != SCHEMA_VERSION:
            raise ProfileStoreError("部署方案索引版本不受支持")
        order = index.get("profile_order")
        if not isinstance(order, list) or not order:
            raise ProfileStoreError("部署方案索引为空或格式无效")
        if index.get("active_profile_id") not in order:
            raise ProfileStoreError("当前部署方案不在索引中")

    def _read_index(self):
        index = self._load_json(self.index_path)
        self._validate_index(index)
        return index

    def _write_index(self, index):
        self._validate_index(index)
        self._atomic_write_json(self.index_path, index)

    def _profile_dir(self, profile_id):
        return os.path.join(self.profiles_dir, profile_id)

    def _profile_path(self, profile_id):
        return os.path.join(self._profile_dir(profile_id), "profile.json")

    def _validated_name(self, name, excluding_id=None):
        name = str(name).strip()
        if not name:
            raise ProfileStoreError("方案名称不能为空")
        if len(name) > 60:
            raise ProfileStoreError("方案名称不能超过 60 个字符")
        if os.path.exists(self.index_path):
            for profile in self.list_profiles():
                if (
                    profile["id"] != excluding_id
                    and profile["name"].casefold() == name.casefold()
                ):
                    raise ProfileStoreError(f"方案名称已存在: {name}")
        return name

    def load_profile(self, profile_id):
        profile = self._load_json(self._profile_path(profile_id))
        if profile.get("id") != profile_id:
            raise ProfileStoreError(f"方案 ID 不匹配: {profile_id}")
        profile["steps"] = normalize_steps(profile.get("steps", []))
        return profile

    def list_profiles(self):
        index = self._read_index()
        return [self.load_profile(profile_id) for profile_id in index["profile_order"]]

    def active_profile_id(self):
        return self._read_index()["active_profile_id"]

    def set_active_profile(self, profile_id):
        index = self._read_index()
        if profile_id not in index["profile_order"]:
            raise ProfileStoreError(f"方案不存在: {profile_id}")
        index["active_profile_id"] = profile_id
        self._write_index(index)

    def create_profile(self, name, steps=None, make_active=True):
        name = self._validated_name(name)
        if steps is None:
            steps = [{"key": "1", "rx": 0.5, "ry": 0.5, "delay": 0.5}]
        profile_id = uuid.uuid4().hex
        now = _now_iso()
        profile = {
            "version": SCHEMA_VERSION,
            "id": profile_id,
            "name": name,
            "steps": normalize_steps(steps),
            "created_at": now,
            "updated_at": now,
            "preview": None,
        }
        self._atomic_write_json(self._profile_path(profile_id), profile)
        index = self._read_index()
        index["profile_order"].append(profile_id)
        if make_active:
            index["active_profile_id"] = profile_id
        self._write_index(index)
        return profile

    def save_steps(self, profile_id, steps):
        profile = self.load_profile(profile_id)
        profile["steps"] = normalize_steps(steps)
        profile["updated_at"] = _now_iso()
        self._atomic_write_json(self._profile_path(profile_id), profile)
        return profile

    def rename_profile(self, profile_id, new_name):
        profile = self.load_profile(profile_id)
        profile["name"] = self._validated_name(new_name, excluding_id=profile_id)
        profile["updated_at"] = _now_iso()
        self._atomic_write_json(self._profile_path(profile_id), profile)
        return profile

    def duplicate_profile(self, profile_id, new_name, steps=None):
        source = self.load_profile(profile_id)
        return self.create_profile(
            new_name,
            source["steps"] if steps is None else steps,
            make_active=True,
        )

    def delete_profile(self, profile_id):
        index = self._read_index()
        if profile_id not in index["profile_order"]:
            raise ProfileStoreError(f"方案不存在: {profile_id}")
        if len(index["profile_order"]) <= 1:
            raise ProfileStoreError("至少需要保留一个部署方案")

        source_dir = self._profile_dir(profile_id)
        os.makedirs(self.trash_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        trash_path = os.path.join(self.trash_dir, f"{profile_id}-{timestamp}")
        shutil.move(source_dir, trash_path)

        index["profile_order"].remove(profile_id)
        if index["active_profile_id"] == profile_id:
            index["active_profile_id"] = index["profile_order"][0]
        try:
            self._write_index(index)
        except Exception:
            # Keep the previous index usable if persisting the deletion fails.
            shutil.move(trash_path, source_dir)
            raise
        return index["active_profile_id"], trash_path

    def set_preview(self, profile_id, image_bytes, width, height):
        if not image_bytes:
            raise ProfileStoreError("预览图数据为空")
        profile = self.load_profile(profile_id)
        preview_path = os.path.join(self._profile_dir(profile_id), "preview.jpg")
        self._atomic_write_bytes(preview_path, image_bytes)
        captured_at = _now_iso()
        profile["preview"] = {
            "filename": "preview.jpg",
            "captured_at": captured_at,
            "width": int(width),
            "height": int(height),
        }
        profile["updated_at"] = captured_at
        self._atomic_write_json(self._profile_path(profile_id), profile)
        return preview_path

    def preview_path(self, profile_id):
        profile = self.load_profile(profile_id)
        preview = profile.get("preview")
        if not preview:
            return None
        path = os.path.join(self._profile_dir(profile_id), preview["filename"])
        return path if os.path.exists(path) else None
