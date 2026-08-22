"""Generic action-list script runner with visual flow control.

This is the first runnable generic Roblox script executor. It runs a plain list
of seven action types against a live Roblox window, reusing the production
primitives instead of reimplementing them:

    engine.capture_window            -> a fresh frame for every recognition
    engine.run_input_actions         -> short foreground input sessions
    vision.analyze_template_match    -> template matching
    vision.load_template_click_anchor -> click-anchor resolution

Hard contracts (see migration Slice 3 spec):

- FRESH FRAME: each find_image / click_image / if_image captures the window at
  the moment it executes via engine.capture_window(hwnd). Frames are never
  cached across actions; only template images may be loaded once and reused.

- INPUT LEASE: engine.run_input_actions is a SHORT session (save env -> block
  input -> foreground -> key/click -> restore). Consecutive key/click actions
  are batched into one session; any wait / vision / flow action ends the batch
  so input is never held across a long wait or a recognition.

- INTERRUPTIBLE WAIT: wait polls stop_event every ~50ms rather than blocking.

- STOP: stop_event is checked before every action, before every repeat
  iteration, inside wait, and before each nested then/else branch.
"""

import os
import threading
import time

import cv2

import engine
import vision


_INPUT_TYPES = ("key", "click", "key_down", "key_up")
_VISION_TYPES = ("find_image", "click_image", "if_image")
_ALL_TYPES = _INPUT_TYPES + _VISION_TYPES + ("repeat", "wait")


class ScriptStop(Exception):
    """Signal: stop_event was set; unwind cleanly without starting new input."""


class ScriptError(Exception):
    """Invalid action or unrecoverable execution failure."""


def validate_script_actions(actions, where="script"):
    """Validate a generic action list (recursively) and raise ScriptError on the
    first invalid action. Fails fast before any side effect."""
    if not isinstance(actions, list):
        raise ScriptError(f"{where} 必须是动作列表")
    for i, act in enumerate(actions):
        if not isinstance(act, dict):
            raise ScriptError(f"{where}[{i}] 不是有效的动作对象")
        atype = act.get("type")
        loc = f"{where}[{i}]"
        if atype not in _ALL_TYPES:
            raise ScriptError(f"{loc} 未知动作类型: {atype!r}")
        if atype == "key":
            key = act.get("key")
            if not isinstance(key, str) or not key.strip():
                raise ScriptError(f"{loc} key 动作缺少有效 key")
        elif atype == "click":
            x, y = act.get("x"), act.get("y")
            if x is None or y is None:
                raise ScriptError(f"{loc} click 动作缺少坐标")
            try:
                x, y = float(x), float(y)
            except (TypeError, ValueError):
                raise ScriptError(f"{loc} click 坐标无效")
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ScriptError(f"{loc} click 坐标越界: ({x},{y})")
        elif atype == "wait":
            try:
                seconds = float(act.get("seconds"))
            except (TypeError, ValueError):
                raise ScriptError(f"{loc} wait 时长无效")
            if seconds < 0:
                raise ScriptError(f"{loc} wait 时长不能小于 0")
        elif atype in _VISION_TYPES:
            tpl = act.get("template")
            if not isinstance(tpl, str) or not tpl.strip():
                raise ScriptError(f"{loc} {atype} 缺少有效 template")
        elif atype == "repeat":
            if not act.get("forever"):
                count = act.get("count")
                if not isinstance(count, int) or count < 0:
                    raise ScriptError(f"{loc} repeat 需要 count 或 forever=true")
            validate_script_actions(act.get("actions", []), f"{loc}.actions")
        if atype == "if_image":
            validate_script_actions(act.get("then", []), f"{loc}.then")
            validate_script_actions(act.get("else", []), f"{loc}.else")


class ScriptRunner:
    """Executes a generic action list against a live Roblox window."""

    def __init__(self, hwnd, base_dir, stop_event=None, log_callback=None):
        self.hwnd = hwnd
        self.base_dir = base_dir
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.log = log_callback if log_callback is not None else (lambda _msg: None)

    # ---- public entry ----
    def run(self, actions):
        try:
            validate_script_actions(actions)
            self._execute_actions(actions)
            return True
        except ScriptStop:
            self.log("[Script] 已停止")
            return True
        except ScriptError as exc:
            self.log(f"[Script] 错误: {exc}")
            return False

    # ---- batching + dispatch ----
    def _execute_actions(self, actions):
        batch = []
        for act in actions:
            if self.stop_event.is_set():
                raise ScriptStop()
            if act.get("type") in _INPUT_TYPES:
                batch.append(act)
                continue
            if batch:
                self._flush(batch)
                batch = []
            self._execute_action(act)
        if batch:
            self._flush(batch)

    def _execute_action(self, act):
        atype = act.get("type")
        if atype == "wait":
            self._do_wait(float(act["seconds"]))
        elif atype == "find_image":
            self._do_find(act)
        elif atype == "click_image":
            self._do_click_image(act)
        elif atype == "if_image":
            self._do_if_image(act)
        elif atype == "repeat":
            self._do_repeat(act)
        else:
            raise ScriptError(f"未知动作类型: {atype!r}")

    def _flush(self, batch):
        if not batch:
            return
        if self.stop_event.is_set():
            raise ScriptStop()
        self.log(f"[Script] 输入会话: {len(batch)} 个 key/click 动作")
        ok = engine.run_input_actions(self.hwnd, batch, log_callback=self.log)
        if not ok:
            raise ScriptError("输入会话失败")

    # ---- wait ----
    def _do_wait(self, seconds):
        if seconds <= 0:
            return
        deadline = time.monotonic() + seconds
        while True:
            if self.stop_event.is_set():
                raise ScriptStop()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))

    # ---- vision helpers ----
    def _fresh_frame(self):
        frame = engine.capture_window(self.hwnd)
        if frame is None:
            raise ScriptError("截图失败（窗口不可用或画面不可见）")
        return frame

    def _resolve_template_path(self, rel):
        if os.path.isabs(rel):
            return rel
        return os.path.join(self.base_dir, rel)

    def _load_template(self, rel):
        path = self._resolve_template_path(rel)
        tpl = cv2.imread(path)
        if tpl is None:
            raise ScriptError(f"模板读取失败: {path}")
        return tpl, path

    def _resolve_anchor(self, template_path):
        templates_dir = os.path.dirname(template_path)
        filename = os.path.basename(template_path)
        return vision.load_template_click_anchor(templates_dir, filename, log=self.log)

    def _find(self, act):
        frame = self._fresh_frame()
        tpl, path = self._load_template(act["template"])
        anchor = self._resolve_anchor(path)
        threshold = float(act.get("threshold", 0.85))
        return vision.analyze_template_match(
            frame, tpl, threshold=threshold, click_anchor=anchor
        )

    # ---- vision / flow actions ----
    def _do_find(self, act):
        diag = self._find(act)
        self.log(
            f"[Script] find_image: template={act['template']} "
            f"found={diag['matched']} confidence={diag['confidence']:.4f} "
            f"position=({diag['relative_x']:.4f},{diag['relative_y']:.4f})"
        )
        return diag

    def _do_click_image(self, act):
        diag = self._find(act)
        if not diag["matched"]:
            self.log(f"[Script] click_image: template={act['template']} NOT_FOUND，不点击")
            return
        x, y = diag["relative_x"], diag["relative_y"]
        self.log(f"[Script] click_image: template={act['template']} found，点击 ({x:.4f},{y:.4f})")
        self._flush([{"type": "click", "x": x, "y": y}])

    def _do_if_image(self, act):
        diag = self._find(act)
        branch = act["then"] if diag["matched"] else act.get("else", [])
        self.log(
            f"[Script] if_image: template={act['template']} -> "
            f"{'then' if diag['matched'] else 'else'}"
        )
        self._execute_actions(branch)

    def _do_repeat(self, act):
        body = act.get("actions", [])
        if act.get("forever"):
            while True:
                if self.stop_event.is_set():
                    raise ScriptStop()
                self._execute_actions(body)
        count = int(act.get("count", 0))
        for _ in range(count):
            if self.stop_event.is_set():
                raise ScriptStop()
            self._execute_actions(body)


def run_script(hwnd, actions, base_dir, stop_event=None, log_callback=None):
    """Run a generic action list against ``hwnd`` and return True on success
    (a clean stop counts as success), False on error."""
    runner = ScriptRunner(hwnd, base_dir, stop_event=stop_event, log_callback=log_callback)
    return runner.run(actions)
