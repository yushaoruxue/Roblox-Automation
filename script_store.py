"""Versioned, atomic storage for generic Roblox scripts and their assets.

Deliberately mirrors the mature persistence behavior of ``profile_store.py``
(atomic write + fsync + os.replace, ``.bak`` fallback, name validation, UUID
ids, ``.trash`` on delete) so generic scripts get the same reliability as the
legacy AE profiles without touching the legacy store.

Layout (kept separate from ``profiles/``)::

    scripts/
      index.json
      <uuid>/
        script.json
        assets/        # templates captured by the user (optional)

A script is a plain dict::

    {
      "version": 1,
      "id": "<uuid-hex>",
      "name": "...",
      "actions": [ ...7 Slice-3 action types... ],
      "created_at": "...",
      "updated_at": "..."
    }

This module only persists the ``actions`` list verbatim; semantic validation of
the action types is owned by the model / ``script_runner.validate_script_actions``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import shutil
import tempfile
import uuid


SCHEMA_VERSION = 1
DEFAULT_SCRIPT_NAME = "新建脚本"


class ScriptStoreError(RuntimeError):
    pass


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class ScriptStore:
    def __init__(self, scripts_dir):
        self.scripts_dir = os.path.abspath(scripts_dir)
        self.index_path = os.path.join(self.scripts_dir, "index.json")
        self.trash_dir = os.path.join(self.scripts_dir, ".trash")
        os.makedirs(self.scripts_dir, exist_ok=True)
        self._ensure_initialized()

    # ---- low-level atomic persistence (mirrors profile_store) ----
    def _atomic_write_bytes(self, path, payload):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix=".tmp-", dir=os.path.dirname(path)
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
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
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
                raise ScriptStoreError(f"无法读取脚本文件: {path}") from primary_error

    def _ensure_initialized(self):
        if os.path.exists(self.index_path):
            self._validate_index(self._load_json(self.index_path))
            return
        self._write_index({"version": SCHEMA_VERSION, "script_order": []})

    def _validate_index(self, index):
        if index.get("version") != SCHEMA_VERSION:
            raise ScriptStoreError("脚本索引版本不受支持")
        order = index.get("script_order")
        if not isinstance(order, list):
            raise ScriptStoreError("脚本索引格式无效")

    def _read_index(self):
        index = self._load_json(self.index_path)
        self._validate_index(index)
        return index

    def _write_index(self, index):
        self._validate_index(index)
        self._atomic_write_json(self.index_path, index)

    def script_dir(self, script_id):
        return os.path.join(self.scripts_dir, script_id)

    def _script_dir(self, script_id):
        return self.script_dir(script_id)

    def _script_path(self, script_id):
        return os.path.join(self._script_dir(script_id), "script.json")

    def assets_dir(self, script_id):
        return os.path.join(self._script_dir(script_id), "assets")

    def ensure_assets_dir(self, script_id):
        path = self.assets_dir(script_id)
        os.makedirs(path, exist_ok=True)
        return path

    def _validated_name(self, name, excluding_id=None):
        name = str(name).strip()
        if not name:
            raise ScriptStoreError("脚本名称不能为空")
        if len(name) > 60:
            raise ScriptStoreError("脚本名称不能超过 60 个字符")
        for script in self.list_scripts():
            if (
                script["id"] != excluding_id
                and script["name"].casefold() == name.casefold()
            ):
                raise ScriptStoreError(f"脚本名称已存在: {name}")
        return name

    # ---- CRUD ----
    def load_script(self, script_id):
        script = self._load_json(self._script_path(script_id))
        if script.get("id") != script_id:
            raise ScriptStoreError(f"脚本 ID 不匹配: {script_id}")
        script["actions"] = list(script.get("actions", []))
        return script

    def list_scripts(self):
        index = self._read_index()
        return [self.load_script(sid) for sid in index["script_order"]]

    def create_script(self, name, actions=None):
        name = self._validated_name(name)
        script_id = uuid.uuid4().hex
        now = _now_iso()
        script = {
            "version": SCHEMA_VERSION,
            "id": script_id,
            "name": name,
            "actions": list(actions or []),
            "created_at": now,
            "updated_at": now,
        }
        self._atomic_write_json(self._script_path(script_id), script)
        index = self._read_index()
        index["script_order"].append(script_id)
        self._write_index(index)
        return script

    def save_actions(self, script_id, actions):
        script = self.load_script(script_id)
        script["actions"] = list(actions)
        script["updated_at"] = _now_iso()
        self._atomic_write_json(self._script_path(script_id), script)
        return script

    def rename_script(self, script_id, new_name):
        script = self.load_script(script_id)
        script["name"] = self._validated_name(new_name, excluding_id=script_id)
        script["updated_at"] = _now_iso()
        self._atomic_write_json(self._script_path(script_id), script)
        return script

    def delete_script(self, script_id):
        index = self._read_index()
        if script_id not in index["script_order"]:
            raise ScriptStoreError(f"脚本不存在: {script_id}")
        source_dir = self._script_dir(script_id)
        os.makedirs(self.trash_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        trash_path = os.path.join(self.trash_dir, f"{script_id}-{timestamp}")
        shutil.move(source_dir, trash_path)
        index["script_order"].remove(script_id)
        try:
            self._write_index(index)
        except Exception:
            shutil.move(trash_path, source_dir)
            raise
        return trash_path

    def write_template(self, script_id, rel_path, image_bytes):
        """Persist a captured template image inside the script's assets dir.

        ``rel_path`` must be a relative path (e.g. ``assets/foo.png``); it is
        resolved against the script directory so templates never escape it.
        """
        if os.path.isabs(rel_path) or ".." in rel_path.replace("\\", "/").split("/"):
            raise ScriptStoreError(f"模板路径必须是脚本内的相对路径: {rel_path}")
        abs_path = os.path.join(self._script_dir(script_id), rel_path)
        self._atomic_write_bytes(abs_path, image_bytes)
        return abs_path
