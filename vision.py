"""Vision primitives: template matching + click-anchor resolution.

These are the ONLY image-recognition functions shared across the app. They are
deliberately generic (no "start button" semantics), stateless, and take the
current frame as an explicit argument. The caller is responsible for acquiring
a fresh frame each poll via engine.capture_window().

Design rules (kept from the old AE, behavior unchanged):
- No frame caching; no preview/picker screenshot reuse; no PrintWindow.
- The same cv2.matchTemplate(TM_CCOEFF_NORMED) + anchor-offset + normalization
  math as the original gui_app methods (threshold default 0.85).
- The AE-specific green-geometry fallback (detect_start_button_by_color) is
  intentionally NOT a vision primitive and remains in gui_app.py.
"""
import os
import json

import cv2


def load_template_click_anchor(templates_dir, filename, log=None):
    """Read the click-anchor offset from ``<filename>.json`` next to the
    template. Falls back to the template center (0.5, 0.5) when metadata is
    missing or unreadable. ``log`` is an optional single-argument callable."""
    template_path = os.path.join(templates_dir, filename)
    metadata_path = os.path.splitext(template_path)[0] + ".json"
    if not os.path.exists(metadata_path):
        if log:
            log(f"模板没有点击锚点元数据，将兼容使用模板中心: {metadata_path}")
        return 0.5, 0.5
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        offset_x = float(metadata["click_offset_x"])
        offset_y = float(metadata["click_offset_y"])
        if not (0.0 <= offset_x <= 1.0 and 0.0 <= offset_y <= 1.0):
            raise ValueError(f"点击锚点越界: ({offset_x},{offset_y})")
        return offset_x, offset_y
    except Exception as e:
        if log:
            log(f"读取模板点击锚点失败，将使用中心点: {e}")
        return 0.5, 0.5


def analyze_template_match(full_img, template_img, threshold=0.85,
                           click_anchor=(0.5, 0.5)):
    """Run template matching and return the best candidate regardless of
    threshold, for diagnostic logging. Returns a dict with ``matched``,
    ``confidence``, ``max_location``, and normalized ``relative_x/relative_y``
    (the click point inside the template, mapped to full-frame coordinates)."""
    if full_img is None or template_img is None:
        raise ValueError("识别图像或模板为空")
    full_h, full_w = full_img.shape[:2]
    template_h, template_w = template_img.shape[:2]
    if template_w > full_w or template_h > full_h:
        raise ValueError(
            f"模板尺寸 {template_w}x{template_h} 大于截图 {full_w}x{full_h}"
        )

    res = cv2.matchTemplate(full_img, template_img, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
    anchor_x, anchor_y = click_anchor
    cx = max_loc[0] + round(anchor_x * max(1, template_w - 1))
    cy = max_loc[1] + round(anchor_y * max(1, template_h - 1))
    return {
        "matched": bool(max_val >= threshold),
        "confidence": float(max_val),
        "max_location": tuple(max_loc),
        "relative_x": cx / max(1, full_w - 1),
        "relative_y": cy / max(1, full_h - 1),
    }


def match_template_location(full_img, template_img, threshold=0.85,
                            click_anchor=(0.5, 0.5)):
    """Find a template and return its pre-calibrated click point as
    ``(relative_x, relative_y, confidence)``, or None when below threshold."""
    diagnostics = analyze_template_match(
        full_img,
        template_img,
        threshold=threshold,
        click_anchor=click_anchor,
    )
    if diagnostics["matched"]:
        return (
            diagnostics["relative_x"],
            diagnostics["relative_y"],
            diagnostics["confidence"],
        )
    return None
