"""User-facing action model + compiler (Slice 4R / Slice 5).

Three-layer model:

  Layer 1  execution primitives:  key / click / wait / find_image /
                                   click_image / if_image / repeat /
                                   find_color / click_color / if_color /
                                   wait_color / wait_image / drag
                                   (``script_runner.py`` + ``engine.py``)
  Layer 2  user actions:          what the GUI edits (this module)
  Layer 3  custom actions:        saved groups / game-specific logic (future)

The GUI edits Layer-2 "user actions"; ``compile_user_actions`` lowers them to
Layer-1 primitives that ``script_runner.run_script`` executes. The GUI never
edits primitives directly.

Color conventions:
  - Layer-2 stores colors as hex strings (``"#43A982"``) for readability.
  - The compiler converts them to RGB tuples ``(r, g, b)`` for Layer-1, which
    is what ``vision.find_color`` consumes.
  - ``region`` is either ``None`` (whole client) or an ``(x, y, w, h)``
    normalized tuple; the GUI stores it as a dict ``{"x","y","width","height"}``
    and the compiler flattens it.

Immediate actions (key / click / key_click / click_image / click_color / drag)
carry an optional ``after_wait`` that compiles to a trailing ``wait``; 0 emits
no wait. ``key_click`` is the P0 composite (key + click), never auto-inserts
"z" (that is legacy AE compatibility, not generic semantics). ``group`` is a
named container; the compiler inlines its children.
"""

from __future__ import annotations

import copy

import vision


# ---- action library (categories shown in the GUI) ----
ACTION_LIBRARY = {
    "点击": [("点击坐标", "click"), ("点击图片", "click_image"),
             ("点击颜色", "click_color")],
    "输入": [("按键", "key"), ("按键后点击", "key_click")],
    "判断": [("如果图片", "if_image"), ("如果颜色", "if_color"),
             ("等待图片", "wait_image"), ("等待颜色", "wait_color")],
    "手势": [("拖动", "drag")],
    "流程": [("等待", "wait"), ("重复", "repeat"), ("动作组", "group")],
    "高级": [("找图片", "find_image"), ("找颜色", "find_color")],
}


# ---- default templates for new actions ----
ACTION_TEMPLATES = {
    "key": {"type": "key", "key": "1", "hold_seconds": 0.06, "after_wait": 0.2},
    "click": {"type": "click", "x": 0.5, "y": 0.5, "after_wait": 0.2},
    "key_click": {"type": "key_click", "key": "1", "hold_seconds": 0.06,
                  "x": 0.5, "y": 0.5, "after_wait": 0.2},
    "click_image": {"type": "click_image", "template": "", "threshold": 0.85,
                    "after_wait": 0.2},
    "if_image": {"type": "if_image", "template": "", "threshold": 0.85,
                 "then": [], "else": []},
    "repeat": {"type": "repeat", "count": 1, "actions": []},
    "group": {"type": "group", "name": "动作组", "actions": []},
    "wait": {"type": "wait", "seconds": 0.2},
    "find_image": {"type": "find_image", "template": "", "threshold": 0.85},
    # ---- Slice 5: color ----
    "click_color": {"type": "click_color", "color": "#43A982", "tolerance": 12,
                    "region": None, "after_wait": 0.2},
    "if_color": {"type": "if_color", "color": "#43A982", "tolerance": 12,
                 "region": None, "then": [], "else": []},
    "wait_color": {"type": "wait_color", "color": "#43A982", "tolerance": 12,
                   "region": None, "poll_interval": 0.5, "timeout": None},
    "find_color": {"type": "find_color", "color": "#43A982", "tolerance": 12,
                   "region": None},
    # ---- Slice 5: wait image ----
    "wait_image": {"type": "wait_image", "template": "", "threshold": 0.85,
                   "poll_interval": 0.5, "timeout": None},
    # ---- Slice 5: drag ----
    "drag": {"type": "drag", "from": {"x": 0.3, "y": 0.4},
             "to": {"x": 0.7, "y": 0.4}, "duration": 0.5, "after_wait": 0.2},
}


# ---- future roadmap (documented, NOT implemented; GUI hides them) ----
RESERVED_ACTIONS = {
    "v0.3": ["滚轮", "输入文字", "变量", "OCR", "RunScript", "JavaScript",
             "Recorder", "自定义动作库"],
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
    # ---- color ----
    if t == "click_color":
        return f"点击颜色 {act.get('color', '')} ±{act.get('tolerance', 0)}" + suffix
    if t == "if_color":
        return f"如果颜色 {act.get('color', '')} ±{act.get('tolerance', 0)}"
    if t == "wait_color":
        return f"等待颜色 {act.get('color', '')} ±{act.get('tolerance', 0)}  {_timeout_label(act)}"
    if t == "find_color":
        return f"找颜色 {act.get('color', '')} ±{act.get('tolerance', 0)}"
    # ---- wait image ----
    if t == "wait_image":
        return f"等待图片 {act.get('template', '(未选)')}  {_timeout_label(act)}"
    # ---- drag ----
    if t == "drag":
        frm = act.get("from", {})
        to = act.get("to", {})
        return (f"拖动 ({frm.get('x', 0):.2f},{frm.get('y', 0):.2f}) → "
                f"({to.get('x', 0):.2f},{to.get('y', 0):.2f})  "
                f"{act.get('duration', 0.5):g}s" + suffix)
    return f"未知 {t}"


def _timeout_label(act):
    timeout = act.get("timeout")
    return "∞" if timeout is None else f"{timeout:g}s"


def child_container(action):
    """Return the list of child-list keys for a container user action, or [].

    if_image / if_color -> ["then", "else"]; repeat / group -> ["actions"];
    leaves -> [].
    """
    t = action.get("type")
    if t in ("if_image", "if_color"):
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
            key_val = act.get("key")
            if not isinstance(key_val, str) or not key_val.strip():
                raise ValueError(f"{loc} {atype} key 必须是非空字符串")
        if atype in ("click", "key_click"):
            _validate_point(act.get("x"), act.get("y"), f"{loc} {atype}")
        if atype == "drag":
            frm, to = act.get("from") or {}, act.get("to") or {}
            _validate_point(frm.get("x"), frm.get("y"), f"{loc} drag.from")
            _validate_point(to.get("x"), to.get("y"), f"{loc} drag.to")
            if float(act.get("duration", 0.5)) <= 0:
                raise ValueError(f"{loc} drag 时长必须大于 0")
        if atype in ("key", "click", "key_click", "click_image", "click_color",
                     "drag"):
            if float(act.get("after_wait", 0) or 0) < 0:
                raise ValueError(f"{loc} after_wait 不能小于 0")
        if atype in ("find_image", "click_image", "if_image", "wait_image"):
            if not isinstance(act.get("template"), str):
                raise ValueError(f"{loc} {atype} template 必须是字符串")
        if atype in ("find_color", "click_color", "if_color", "wait_color"):
            if act.get("color") is None:
                raise ValueError(f"{loc} {atype} 缺少颜色")
            try:
                vision.hex_to_rgb(act["color"])
            except ValueError as e:
                raise ValueError(f"{loc} {atype} 颜色无效: {e}")
            if float(act.get("tolerance", 0)) < 0:
                raise ValueError(f"{loc} {atype} tolerance 不能小于 0")
            region = act.get("region")
            if region is not None:
                _validate_region(region, f"{loc} {atype}.region")
        if atype in ("wait_image", "wait_color"):
            if float(act.get("poll_interval", 0.5)) <= 0:
                raise ValueError(f"{loc} {atype} poll_interval 必须大于 0")
        if atype == "wait":
            if float(act.get("seconds", 0)) < 0:
                raise ValueError(f"{loc} 等待时长不能小于 0")
        if atype == "repeat":
            if not act.get("forever") and not isinstance(act.get("count"), int):
                raise ValueError(f"{loc} repeat 需要 count 或 forever=true")
            validate_user_actions(act.get("actions", []), f"{loc}.actions")
        if atype in ("if_image", "if_color"):
            validate_user_actions(act.get("then", []), f"{loc}.then")
            validate_user_actions(act.get("else", []), f"{loc}.else")
        if atype == "group":
            validate_user_actions(act.get("actions", []), f"{loc}.actions")
    return actions


def _validate_point(x, y, loc):
    if x is None or y is None:
        raise ValueError(f"{loc} 缺少坐标")
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError):
        raise ValueError(f"{loc} 坐标无效")
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise ValueError(f"{loc} 坐标越界: ({x},{y})")


def _validate_region(region, loc):
    for k in ("x", "y", "width", "height"):
        if k not in region:
            raise ValueError(f"{loc} 缺少 {k}")
        if not (0.0 <= float(region[k]) <= 1.0):
            raise ValueError(f"{loc} {k} 越界")


def _after_wait(act):
    return float(act.get("after_wait", 0) or 0)


def _region_tuple(region):
    """Flatten a GUI region dict to an (x, y, w, h) tuple, or None."""
    if region is None:
        return None
    return (float(region["x"]), float(region["y"]),
            float(region["width"]), float(region["height"]))


def _color_rgb(act):
    return list(vision.hex_to_rgb(act["color"]))


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
        return compile_user_actions(act.get("actions", []))
    if t == "wait":
        return [{"type": "wait", "seconds": float(act.get("seconds", 0))}]
    if t == "find_image":
        return [{"type": "find_image", "template": act["template"],
                 "threshold": float(act.get("threshold", 0.85))}]
    # ---- color ----
    if t in ("find_color", "click_color", "if_color", "wait_color"):
        prim = [{
            "type": t,
            "color": _color_rgb(act),
            "tolerance": int(act.get("tolerance", 0)),
            "region": _region_tuple(act.get("region")),
        }]
        if t == "if_color":
            prim[0]["then"] = compile_user_actions(act.get("then", []))
            prim[0]["else"] = compile_user_actions(act.get("else", []))
        if t == "wait_color":
            prim[0]["poll_interval"] = float(act.get("poll_interval", 0.5))
            prim[0]["timeout"] = act.get("timeout")
        if t == "click_color":
            _append_wait(prim, _after_wait(act))
        return prim
    # ---- wait image ----
    if t == "wait_image":
        return [{
            "type": "wait_image",
            "template": act["template"],
            "threshold": float(act.get("threshold", 0.85)),
            "poll_interval": float(act.get("poll_interval", 0.5)),
            "timeout": act.get("timeout"),
        }]
    # ---- drag ----
    if t == "drag":
        prim = [{
            "type": "drag",
            "from": {"x": float(act["from"]["x"]), "y": float(act["from"]["y"])},
            "to": {"x": float(act["to"]["x"]), "y": float(act["to"]["y"])},
            "duration": float(act.get("duration", 0.5)),
        }]
        _append_wait(prim, _after_wait(act))
        return prim
    raise ValueError(f"未知用户动作类型: {t!r}")


def _append_wait(prim, seconds):
    if seconds > 0:
        prim.append({"type": "wait", "seconds": seconds})
