"""Windows display gamma dimming that does not darken screen-capture pixels."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading


GAMMA_ENTRIES = 256
CHANNEL_COUNT = 3
RAMP_SIZE = GAMMA_ENTRIES * CHANNEL_COUNT


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


def build_dimmed_ramp(original_values, level):
    """
    Convert a 0..100 comfort level into a Windows-compatible gamma ramp.

    At level 0, mid-tones are heavily reduced while the white endpoint remains
    at 50%. Windows rejects lower endpoints on many modern display drivers.
    Level 100 returns the original ramp.
    """
    level = max(0, min(100, int(round(level))))
    blend = level / 100.0
    endpoint_scale = 0.50 + 0.50 * blend
    gamma = 2.0 - blend
    result = []
    for value in original_values:
        normalized = max(0.0, min(1.0, int(value) / 65535.0))
        adjusted = 65535.0 * endpoint_scale * (normalized ** gamma)
        result.append(max(0, min(65535, int(round(adjusted)))))
    return result


class DisplayDimmer:
    """Capture, apply, and restore gamma ramps for every active monitor."""

    def __init__(self):
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32
        self._ramp_type = wintypes.WORD * RAMP_SIZE
        self._original_ramps = {}
        self._active_level = 100
        self._lock = threading.RLock()
        self._capture_original_ramps()

    @property
    def active_level(self):
        return self._active_level

    @property
    def available(self):
        return bool(self._original_ramps)

    def _active_display_names(self):
        names = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )

        @callback_type
        def collect(monitor, _hdc, _rect, _data):
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(info)
            if self._user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                if info.szDevice not in names:
                    names.append(info.szDevice)
            return True

        if not self._user32.EnumDisplayMonitors(0, None, collect, 0):
            return []
        return names

    def _with_display_dc(self, device_name, callback):
        dc = self._gdi32.CreateDCW(device_name, device_name, None, None)
        if not dc:
            return False
        try:
            return bool(callback(dc))
        finally:
            self._gdi32.DeleteDC(dc)

    def _capture_original_ramps(self):
        for device_name in self._active_display_names():
            ramp = self._ramp_type()

            def read(dc, target=ramp):
                return self._gdi32.GetDeviceGammaRamp(dc, ctypes.byref(target))

            if self._with_display_dc(device_name, read):
                self._original_ramps[device_name] = tuple(ramp)

    def apply(self, level):
        level = max(0, min(100, int(round(level))))
        with self._lock:
            if not self._original_ramps:
                raise RuntimeError("未找到支持伽马调节的活动显示器")
            successful = []
            failed = []
            for device_name, original in self._original_ramps.items():
                values = original if level >= 100 else build_dimmed_ramp(original, level)
                ramp = self._ramp_type(*values)

                def write(dc, target=ramp):
                    return self._gdi32.SetDeviceGammaRamp(dc, ctypes.byref(target))

                if self._with_display_dc(device_name, write):
                    successful.append(device_name)
                else:
                    failed.append(device_name)

            if failed:
                for device_name in successful:
                    original = self._ramp_type(*self._original_ramps[device_name])

                    def rollback(dc, target=original):
                        return self._gdi32.SetDeviceGammaRamp(dc, ctypes.byref(target))

                    self._with_display_dc(device_name, rollback)
                raise RuntimeError("显示器拒绝亮度设置: " + ", ".join(failed))
            self._active_level = level
            return len(successful)

    def restore(self):
        return self.apply(100)
