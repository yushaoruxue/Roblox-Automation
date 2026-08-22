"""User-facing action model + compiler (Slice 4R).

Three-layer model:

  Layer 1  execution primitives:  key / click / wait / find_image /
                                   click_image / if_image / repeat
                                   (``script_runner.py``)
  Layer 2  user actions:          key / click / key_click / click_image /
                                   if_image / repeat / group / wait / find_image
                                   (this module — what the GUI edits)
  Layer 3  custom actions:        saved groups / game-specific logic (future)

The GUI edits Layer-2 "user actions"; ``compile_user_actions`` lowers them to
Layer-1 primitives that ``script_runner.run_script`` executes. The GUI never
edits primitives directly.

Key UX rules (v0.1):

- Every "immediate" action (key / click / key_click / click_image) carries an
  optional ``after_wait`` (seconds) which compiles to a trailing ``wait``; a
  value of 0 emits no wait.
- ``key_click`` is the P0 composite: one action = key + click (+ wait). It never
  auto-inserts "z" — that is legacy AE compatibility, not generic semantics.
- ``group`` is a named container; the compiler recursively inlines its children
  (it has no runtime behavior of its own).
"""

from __future__ import annotations

import copy


# ---- v0.1 action library (categories shown in the GUI) ----
ACTION_LIBRARY = {
    "点击": [("点击坐标", "click"), ("点击图片", "click_image")],
    "输入": [("按键", "key"), ("按键后点击", "key_click")],
    "判断": [("如果图片", "if_image")],
    "流程": [("等待", "wait"), ("重复", "repeat"), ("动作组", "group")],
    "高级": [("找图片", "find_image")],
}

# ---- default templates for new actions ----
ACTION_TEMPLATES = {
    "key": {"type": "key", "key": "1", "hold_seconds": 0.06, "after_wait": 0.0},
    "click": {"type": "click", "x": 0.5, "y": 0.5, "after_wait": 0.0},
    "key_click": {"type": "key_click", "key": "1", "hold_seconds": 0.06,
                  "x": 0.5, "y": 0.5, "after_wait": 0.5},
    "click_image": {"type": "click_image", "template": "", "threshold": 0.85,
                    "after_wait": 0.3},
    "if_image": {"type": "if_image", "template": "", "threshold": 0.85,
                 "then": [], "else": []},
    "repeat": {"type": "repeat", "count": 1, "actions": []},
    "group": {"type": "group", "name": "动作组", "actions": []},
    "wait": {"type": "wait", "seconds": 1.0},
    "find_image": {"type": "find_image", "template": "", "threshold": 0.85},
}

# ---- v0.2 / v0.3 roadmap (documented, NOT implemented; GUI hides them) ----
RESERVED_ACTIONS = {
    "v0.2": ["点击颜色", "如果颜色", "等待图片", "等待颜色"],
    "v0.3": ["拖动", "滚轮", "按住按键", "释放按键", "输入文字", "变量",
             "OCR", "RunScript", "JavaScript", "Recorder", "自定义动作库"],
}


_VALID_TYPES = set(ACTION_TEMPLATES)


def new_action(atype):
    """Return a deep copy of the default template for a user action type."""
    return copy.deepcopy(ACTION_TEMPLATES[atype])


def action_summary(act):
    """Human-readable one-line summary for the flow tree."""
    t = act.get("type", "?")
    after = act.get("after_wait", 0) or 0
    suffix = f"  +{after:g}s" if after else ""
    if t == "key":
        return f"按键 [{act.get('key', '')}]" + suffix
    if t == "click":
        return f"点击 ({act.get('x', 0):.3f}, {act.get('y', 0):.3f})" + suffix
    if t == "key_click":
        return (f"按键后点击 [{act.get('key', '')}] → "
                f"({act.get('x', 0):.3f},{act.get('y', 0):.3f})" + suffix)
    if t == "click_image":
        return f"点击图片 {act.get('template', '(未选)')}  ≥{act.get('threshold', 0.85):g}" + suffix
    if t == "if_image":
        return f"如果图片 {act.get('template', '(未选)')}  ≥{act.get('threshold', 0.85):g}"
    if t == "repeat":
        return "重复 ∞" if act.get("forever") else f"重复 ×{act.get('count', 1)}"
    if t == "group":
        return f"动作组：{act.get('name', '')}"
    if t == "wait":
        return f"等待 {act.get('seconds', 0):g}s"
    if t == "find_image":
        return f"找图片 {act.get('template', '(未选)')}  ≥{act.get('threshold', 0.85):g}"
    return f"未知 {t}"


def child_container(action):
    """Return the list of child-list keys for a container user action, or [].

    if_image -> ["then", "else"]; repeat / group -> ["actions"]; leaves -> [].
    """
    t = action.get("type")
    if t == "if_image":
        return ["then", "else"]
    if t in ("repeat", "group"):
        return ["actions"]
    return []


def validate_user_actions(actions, where="script"):
    """Validate a user-action list (recursively); raise ValueError on the first
    invalid action. Fails fast before any side effect."""
    if not isinstance(actions, list):
        raise ValueError(f"{where} 必须是动作列表")
    for i, act in enumerate(actions):
        if not isinstance(act, dict):
            raise ValueError(f"{where}[{i}] 不是有效的动作对象")
        atype = act.get("type")
        loc = f"{where}[{i}]"
        if atype not in _VALID_TYPES:
            raise ValueError(f"{loc} 未知动作类型: {atype!r}")
        if atype in ("key", "key_click"):
            if not isinstance(act.get("key"), str):
                raise ValueError(f"{loc} {atype} key 必须是字符串")
        if atype in ("click", "key_click"):
            x, y = act.get("x"), act.get("y")
            if x is None or y is None:
                raise ValueError(f"{loc} {atype} 缺少坐标")
            if not (0.0 <= float(x) <= 1.0 and 0.0 <= float(y) <= 1.0):
                raise ValueError(f"{loc} {atype} 坐标越界: ({x},{y})")
        if atype in ("key", "click", "key_click", "click_image"):
            after = act.get("after_wait", 0) or 0
            if float(after) < 0:
                raise ValueError(f"{loc} after_wait 不能小于 0")
        if atype in ("find_image", "click_image", "if_image"):
            if not isinstance(act.get("template"), str):
                raise ValueError(f"{loc} {atype} template 必须是字符串")
        if atype == "wait":
            if float(act.get("seconds", 0)) < 0:
                raise ValueError(f"{loc} 等待时长不能小于 0")
        if atype == "repeat":
            if not act.get("forever") and not isinstance(act.get("count"), int):
                raise ValueError(f"{loc} repeat 需要 count 或 forever=true")
            validate_user_actions(act.get("actions", []), f"{loc}.actions")
        if atype == "if_image":
            validate_user_actions(act.get("then", []), f"{loc}.then")
            validate_user_actions(act.get("else", []), f"{loc}.else")
        if atype == "group":
            validate_user_actions(act.get("actions", []), f"{loc}.actions")
    return actions


def _after_wait(act):
    return float(act.get("after_wait", 0) or 0)


def compile_user_actions(actions):
    """Lower a list of user actions to Layer-1 primitives for script_runner."""
    out = []
    for act in actions:
        out.extend(_compile_one(act))
    return out


def _compile_one(act):
    t = act.get("type")
    if t == "key":
        prim = [{"type": "key", "key": act["key"],
                 "hold_seconds": float(act.get("hold_seconds", 0.06))}]
        _append_wait(prim, _after_wait(act))
        return prim
    if t == "click":
        prim = [{"type": "click", "x": float(act["x"]), "y": float(act["y"])}]
        _append_wait(prim, _after_wait(act))
        return prim
    if t == "key_click":
        prim = [
            {"type": "key", "key": act["key"],
             "hold_seconds": float(act.get("hold_seconds", 0.06))},
            {"type": "click", "x": float(act["x"]), "y": float(act["y"])},
        ]
        _append_wait(prim, _after_wait(act))
        return prim
    if t == "click_image":
        prim = [{"type": "click_image", "template": act["template"],
                 "threshold": float(act.get("threshold", 0.85))}]
        _append_wait(prim, _after_wait(act))
        return prim
    if t == "if_image":
        return [{
            "type": "if_image",
            "template": act["template"],
            "threshold": float(act.get("threshold", 0.85)),
            "then": compile_user_actions(act.get("then", [])),
            "else": compile_user_actions(act.get("else", [])),
        }]
    if t == "repeat":
        node = {"type": "repeat",
                "actions": compile_user_actions(act.get("actions", []))}
        if act.get("forever"):
            node["forever"] = True
        else:
            node["count"] = int(act.get("count", 1))
        return [node]
    if t == "group":
        # groups inline their children (no runtime behavior of their own)
        return compile_user_actions(act.get("actions", []))
    if t == "wait":
        return [{"type": "wait", "seconds": float(act.get("seconds", 0))}]
    if t == "find_image":
        return [{"type": "find_image", "template": act["template"],
                 "threshold": float(act.get("threshold", 0.85))}]
    raise ValueError(f"未知用户动作类型: {t!r}")


def _append_wait(prim, seconds):
    if seconds > 0:
        prim.append({"type": "wait", "seconds": seconds})
