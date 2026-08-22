"""Generic input action model + legacy AE step compilation.

Pure data / transform module. It must NOT import tkinter, win32api, win32gui,
pydirectinput, cv2, or PIL: it only builds plain dicts describing input
actions. Real Windows input lives in ``engine.run_input_actions``.

Slice 2 supports exactly three action types:

    key   -> {"type": "key",   "key": "<k>", "hold_seconds": <float>}
    click -> {"type": "click", "x": <0..1>, "y": <0..1>}   # normalized client coords
    wait  -> {"type": "wait",  "seconds": <float>}

The "x"/"y" of a click are Roblox client normalized coordinates (0..1).

A legacy AE deployment step is compiled into the explicit sequence the old
engine actually performed, so the generic executor needs zero hidden sleeps and
the per-step Z reset is visible.
"""

_Z_RESET_KEY = "z"
_Z_RESET_HOLD = 0.05
_SLOT_KEY_HOLD = 0.06
_POST_Z_WAIT = 0.10
_POST_KEY_WAIT = 0.25
_POST_CLICK_WAIT = 0.15


def compile_legacy_steps(steps, start_click_rx=None, start_click_ry=None):
    """Compile legacy AE steps (and optional start click) into generic actions.

    One legacy step ``{"key": "1", "rx": 0.561, "ry": 0.553, "delay": 0.1}``
    maps to::

        z-reset key -> wait -> slot key -> wait -> click -> wait -> delay wait

    The per-step Z reset and all implicit settling delays are made explicit so
    the timing is preserved exactly and the executor stays generic. The key is
    passed through as-is (no 1..6 restriction here — that belongs to the legacy
    ``run_action_sequence`` wrapper).
    """
    actions = []
    for step in steps:
        key = str(step["key"]).strip()
        rx = float(step["rx"])
        ry = float(step["ry"])
        delay = max(0.0, float(step.get("delay", 0.5)))
        actions.append({"type": "key", "key": _Z_RESET_KEY, "hold_seconds": _Z_RESET_HOLD})
        actions.append({"type": "wait", "seconds": _POST_Z_WAIT})
        actions.append({"type": "key", "key": key, "hold_seconds": _SLOT_KEY_HOLD})
        actions.append({"type": "wait", "seconds": _POST_KEY_WAIT})
        actions.append({"type": "click", "x": rx, "y": ry})
        actions.append({"type": "wait", "seconds": _POST_CLICK_WAIT})
        actions.append({"type": "wait", "seconds": delay})

    if start_click_rx is not None or start_click_ry is not None:
        if start_click_rx is None or start_click_ry is None:
            raise ValueError("开始按钮必须同时提供 rx 和 ry")
        actions.append({
            "type": "click",
            "x": float(start_click_rx),
            "y": float(start_click_ry),
            "label": "start",
        })
    return actions


def validate_actions(actions):
    """Validate a list of generic actions; raise ValueError on the first
    invalid action. Generic contract: any non-empty key string, click x/y in
    [0, 1], non-negative wait seconds. No 1..6 key restriction."""
    for index, act in enumerate(actions):
        atype = act.get("type")
        if atype == "key":
            key = act.get("key")
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"动作 {index + 1} 的按键不能为空")
            try:
                hold = float(act.get("hold_seconds", 0.0))
            except (TypeError, ValueError):
                raise ValueError(f"动作 {index + 1} 的 hold_seconds 无效")
            if hold < 0:
                raise ValueError(f"动作 {index + 1} 的 hold_seconds 不能小于 0")
        elif atype == "click":
            x, y = act.get("x"), act.get("y")
            if x is None or y is None:
                raise ValueError(f"动作 {index + 1} 的坐标不能为空")
            try:
                x, y = float(x), float(y)
            except (TypeError, ValueError):
                raise ValueError(f"动作 {index + 1} 的坐标无效")
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError(f"动作 {index + 1} 的坐标越界: ({x},{y})")
        elif atype == "wait":
            try:
                seconds = float(act.get("seconds"))
            except (TypeError, ValueError):
                raise ValueError(f"动作 {index + 1} 的等待时长无效")
            if seconds < 0:
                raise ValueError(f"动作 {index + 1} 的等待时长不能小于 0")
        else:
            raise ValueError(f"动作 {index + 1} 的类型无效: {atype!r}")
    return actions
