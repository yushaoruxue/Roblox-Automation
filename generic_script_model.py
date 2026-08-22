"""GUI-free model + controller for the generic script editor.

Keeps the generic script's data as a plain ``dict``/``list`` tree (never Tk
widgets), tracks dirty state, edits the Slice-3 action tree, resolves template
paths relative to the script directory, and provides a small runner bridge that
delegates to ``script_runner.run_script`` on a worker thread with a log queue.

No ``tkinter`` imports here — this layer is fully unit-testable with mocks.
"""

from __future__ import annotations

import copy
import os
import queue
import threading

import cv2

import engine
import vision
import script_runner
import user_actions
from script_store import ScriptStoreError


_CONTAINER_KEYS = ("then", "else", "actions")


class GenericScriptModel:
    """Owns the in-memory action tree + dirty flag; delegates persistence."""

    def __init__(self, store):
        self.store = store
        self.script_id = None
        self.name = ""
        self.actions = []  # pure list[dict], the root action list
        self.dirty = False

    # ---- lifecycle ----
    def new(self, name, actions=None):
        self.script_id = None
        self.name = str(name).strip()
        self.actions = list(actions or [])
        self.dirty = True

    def load(self, script_id):
        script = self.store.load_script(script_id)
        self.script_id = script["id"]
        self.name = script["name"]
        self.actions = list(script["actions"])
        self.dirty = False

    def save(self):
        if self.script_id is None:
            script = self.store.create_script(self.name, self.actions)
            self.script_id = script["id"]
        else:
            script = self.store.save_actions(self.script_id, self.actions)
        self.name = script["name"]
        self.dirty = False
        return script

    def rename(self, new_name):
        self.name = str(new_name).strip()
        self.mark_dirty()

    def discard(self):
        """Revert in-memory state to the last persisted version."""
        if self.script_id is None:
            self.script_id = None
            self.name = ""
            self.actions = []
        else:
            script = self.store.load_script(self.script_id)
            self.name = script["name"]
            self.actions = list(script["actions"])
        self.dirty = False

    def delete(self):
        if self.script_id is not None:
            self.store.delete_script(self.script_id)
        self.script_id = None
        self.name = ""
        self.actions = []
        self.dirty = False

    def mark_dirty(self):
        self.dirty = True

    # ---- path resolution ----
    # A path is a list of steps: int -> index into a list, str -> key into a dict.
    # []                -> root action list
    # [i]               -> root action i
    # [i, "then"]       -> the "then" list of root action i
    # [i, "then", j]    -> action j of that list
    # [k, "actions", m] -> action m of repeat k's body
    def _resolve(self, path):
        node = self.actions
        for step in path:
            if isinstance(node, list) and isinstance(step, int):
                node = node[step]
            elif isinstance(node, dict) and isinstance(step, str):
                node = node[step]
            else:
                raise KeyError(f"动作路径无效: {path!r} @ {step!r}")
        return node

    def get_action(self, path):
        action = self._resolve(path)
        if not isinstance(action, dict):
            raise KeyError(f"路径不是动作: {path!r}")
        return action

    def get_list(self, path):
        lst = self._resolve(path)
        if not isinstance(lst, list):
            raise KeyError(f"路径不是动作列表: {path!r}")
        return lst

    def set_action(self, path, action):
        if not path or not isinstance(path[-1], int):
            raise KeyError(f"动作路径必须以索引结尾: {path!r}")
        lst = self.get_list(path[:-1])
        lst[path[-1]] = action
        self.mark_dirty()

    def insert_action(self, list_path, action, index=None):
        lst = self.get_list(list_path)
        if index is None:
            index = len(lst)
        lst.insert(index, action)
        self.mark_dirty()

    def remove_action(self, path):
        if not path or not isinstance(path[-1], int):
            raise KeyError(f"动作路径必须以索引结尾: {path!r}")
        lst = self.get_list(path[:-1])
        lst.pop(path[-1])
        self.mark_dirty()

    def move_action(self, path, delta):
        if not path or not isinstance(path[-1], int):
            raise KeyError(f"动作路径必须以索引结尾: {path!r}")
        lst = self.get_list(path[:-1])
        index = path[-1]
        new_index = index + delta
        if not (0 <= new_index < len(lst)):
            return
        item = lst.pop(index)
        lst.insert(new_index, item)
        self.mark_dirty()

    def duplicate_action(self, path):
        if not path or not isinstance(path[-1], int):
            raise KeyError(f"动作路径必须以索引结尾: {path!r}")
        lst = self.get_list(path[:-1])
        lst.insert(path[-1] + 1, copy.deepcopy(lst[path[-1]]))
        self.mark_dirty()

    # ---- container helpers ----
    @staticmethod
    def child_lists(action):
        """Return {key: list} of editable child action lists for a container
        user action (if_image -> then/else, repeat/group -> actions); {} for leaves."""
        return {key: action.setdefault(key, [])
                for key in user_actions.child_container(action)}

    def compiled_actions(self):
        """Lower the in-memory user actions to Layer-1 primitives for the runner."""
        return user_actions.compile_user_actions(self.actions)

    def validate(self):
        user_actions.validate_user_actions(self.actions)

    # ---- template path helpers ----
    def assets_dir(self):
        if self.script_id is None:
            return None
        return self.store.assets_dir(self.script_id)

    def ensure_assets_dir(self):
        if self.script_id is None:
            raise ScriptStoreError("脚本尚未保存，无法创建资源目录")
        return self.store.ensure_assets_dir(self.script_id)

    def template_rel_path(self, name):
        """Relative forward-slash path for a captured template asset."""
        name = name.replace("\\", "/").lstrip("/")
        return f"assets/{name}"

    def resolve_template_abs(self, template_rel):
        """Resolve a stored (relative) template path against the script dir."""
        if self.script_id is None:
            raise ScriptStoreError("脚本尚未保存，无法解析模板路径")
        if os.path.isabs(template_rel):
            return os.path.normpath(template_rel)
        return os.path.normpath(
            os.path.join(self.store.script_dir(self.script_id), template_rel)
        )

    # ---- live action test (fresh capture; NOT cached frames) ----
    def test_find_image(self, hwnd, template_rel, threshold=0.85):
        """Capture the Roblox frame NOW and match the template. Returns the
        same diagnostics dict as vision.analyze_template_match."""
        frame = engine.capture_window(hwnd)
        if frame is None:
            raise ScriptStoreError("截图失败（窗口不可用或画面不可见）")
        abs_path = self.resolve_template_abs(template_rel)
        tpl = cv2.imread(abs_path)
        if tpl is None:
            raise ScriptStoreError(f"模板读取失败: {abs_path}")
        anchor = vision.load_template_click_anchor(
            os.path.dirname(abs_path), os.path.basename(abs_path)
        )
        return vision.analyze_template_match(
            frame, tpl, threshold=threshold, click_anchor=anchor
        )

    def test_input(self, hwnd, action):
        """Run a single key/click/key_click user action through the real input
        session (compiled to primitives first)."""
        prims = user_actions.compile_user_actions([action])
        return engine.run_input_actions(hwnd, prims)


class GenericRunnerController:
    """Runs a script on a worker thread; stop via stop_event; logs via queue."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None
        self.log_queue = queue.Queue()

    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, hwnd, actions, base_dir):
        if self.is_running():
            return False
        self.stop_event.clear()

        def _worker():
            try:
                script_runner.run_script(
                    hwnd,
                    actions,
                    base_dir,
                    stop_event=self.stop_event,
                    log_callback=lambda msg: self.log_queue.put(msg),
                )
            except Exception as exc:  # never let the thread die silently
                self.log_queue.put(f"[Runner] 异常: {exc}")

        self.thread = threading.Thread(target=_worker, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.stop_event.set()

    def drain_logs(self):
        messages = []
        while True:
            try:
                messages.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        return messages
