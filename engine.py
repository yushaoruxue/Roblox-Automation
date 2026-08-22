import ctypes
import os
import time
import threading
import win32api
import win32con
import win32gui
import win32process
import pydirectinput
from PIL import ImageGrab

import actions

# 限制 pydirectinput 动作间隔，设为 0.01 避免内置延迟，由我们自己精准控制
pydirectinput.PAUSE = 0.01
_ACTION_LOCK = threading.Lock()


def _emit(message, log_callback=None):
    print(message)
    if log_callback:
        try:
            log_callback(message)
        except Exception:
            pass


def normalized_to_client_point(width, height, rx, ry):
    """把 0~1 相对坐标换算成有效的客户区像素；无效输入直接报错，绝不夹到边缘。"""
    if width <= 0 or height <= 0:
        raise ValueError(f"客户区尺寸无效: {width}x{height}")
    if rx is None or ry is None:
        raise ValueError("相对坐标不能为空")
    rx = float(rx)
    ry = float(ry)
    if not (0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0):
        raise ValueError(f"相对坐标越界: ({rx:.4f}, {ry:.4f})")
    return round(rx * (width - 1)), round(ry * (height - 1))


def relative_to_screen(hwnd, rx, ry):
    """返回相对坐标对应的客户区和屏幕坐标，并执行完整边界检查。"""
    if not win32gui.IsWindow(hwnd):
        raise ValueError(f"目标窗口句柄无效: {hwnd}")
    _, _, width, height = win32gui.GetClientRect(hwnd)
    client_x, client_y = normalized_to_client_point(width, height, rx, ry)
    screen_x, screen_y = win32gui.ClientToScreen(hwnd, (client_x, client_y))
    return client_x, client_y, screen_x, screen_y, width, height


def get_window_process_info(hwnd):
    """返回窗口所属 PID 和完整进程路径；用于避免根据标题误选窗口。"""
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return pid, None
    try:
        size = ctypes.c_uint(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
            process, 0, buffer, ctypes.byref(size)
        )
        return pid, buffer.value if ok else None
    finally:
        ctypes.windll.kernel32.CloseHandle(process)


def get_process_resource_usage(hwnd):
    """读取目标窗口进程的工作集、私有提交和运行时间，不修改进程状态。"""
    if not win32gui.IsWindow(hwnd):
        raise ValueError(f"目标窗口句柄无效: {hwnd}")
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    process = win32api.OpenProcess(
        win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
        False,
        pid,
    )
    try:
        memory = win32process.GetProcessMemoryInfo(process)
        process_times = win32process.GetProcessTimes(process)
        creation_time = process_times["CreationTime"]
        runtime_seconds = max(0.0, time.time() - creation_time.timestamp())
        return {
            "pid": pid,
            "working_set_bytes": int(memory["WorkingSetSize"]),
            # Windows 的 PagefileUsage 对应进程私有提交量，不等于实际页面文件读写量。
            "private_commit_bytes": int(memory["PagefileUsage"]),
            "runtime_seconds": runtime_seconds,
        }
    finally:
        process.Close()


def find_roblox_hwnd():
    """
    寻找所有真正的 Roblox 游戏窗口句柄，排查无关窗口
    返回列表: [(hwnd, window_title), ...]
    """
    hwnds = []
    def callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            _, process_path = get_window_process_info(hwnd)
            executable = os.path.basename(process_path or "").lower()

            # 只接受真实 Roblox Player 进程，不再根据窗口标题猜测。
            if executable == "robloxplayerbeta.exe":
                hwnds.append((hwnd, title))
        return True
    win32gui.EnumWindows(callback, None)
    return hwnds

def capture_window(hwnd):
    """
    通过屏幕绝对区域裁剪 (ImageGrab) 截取 Roblox 客户区的画面。
    这样绕过了 Byfron 的 PrintWindow 限制，防屏蔽率 100%。
    """
    try:
        if not win32gui.IsWindow(hwnd):
            return None
            
        # 获取客户区左上角在屏幕上的绝对物理坐标
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        _, _, w, h = win32gui.GetClientRect(hwnd)
        if w <= 0 or h <= 0:
            return None

        # 截取该屏幕矩形区域
        bbox = (left, top, left + w, top + h)
        
        # 截取画面 (all_screens=True 支持多显示器)
        img = ImageGrab.grab(bbox, all_screens=True)
        
        # 转换为 OpenCV BGR 格式
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    except Exception as e:
        print(f"[Engine] 截图失败: {e}")
        return None

def force_foreground(hwnd, timeout=1.0, log_callback=None):
    """
    智能强制激活窗口并置于前台，根据是否最小化动态分配等待时间，确保瞬间完成置顶。
    """
    try:
        if not win32gui.IsWindow(hwnd):
            _emit(f"[Engine] 目标窗口句柄无效: {hwnd}", log_callback)
            return False
        if win32gui.GetForegroundWindow() == hwnd:
            return True
            
        # 检查窗口是否最小化
        if win32gui.IsIconic(hwnd):
            # 最小化状态需要还原，并留出动画时间
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        else:
            # 只是被遮挡时，显示并请求置顶
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.SetForegroundWindow(hwnd)

        deadline = time.monotonic() + max(0.05, timeout)
        while time.monotonic() < deadline:
            if win32gui.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.02)
    except Exception as first_error:
        _emit(f"[Engine] 首次置顶请求失败: {first_error}", log_callback)

    # Windows 可能拒绝后台进程抢焦点；Alt 仅作为兼容性重试，最后仍以实际前台句柄为准。
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shell.SendKeys('%')
        win32gui.SetForegroundWindow(hwnd)
        deadline = time.monotonic() + max(0.05, timeout)
        while time.monotonic() < deadline:
            if win32gui.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.02)
    except Exception as e:
        _emit(f"[Engine] 置顶重试失败: {e}", log_callback)

    actual = win32gui.GetForegroundWindow()
    _emit(f"[Engine] Roblox 未获得前台焦点，期望 hwnd={hwnd}，实际 hwnd={actual}", log_callback)
    return False


def _point_hits_window(hwnd, screen_x, screen_y):
    """判断屏幕点下方的顶层窗口是否为目标窗口。"""
    try:
        hit = win32gui.WindowFromPoint((screen_x, screen_y))
        root = win32gui.GetAncestor(hit, win32con.GA_ROOT)
        return root == hwnd, hit, root
    except Exception:
        return False, None, None


def _move_and_verify(screen_x, screen_y):
    """
    移动到目标后发送 +1/-1 相对移动事件。

    Roblox 宏社区常用这种 BetterClick 方式让游戏刷新“鼠标位于客户区内”的状态；
    最终坐标仍回到原目标点。
    """
    win32api.SetCursorPos((screen_x, screen_y))
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 1, 0, 0, 0)
    time.sleep(0.03)
    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, -1, 0, 0, 0)
    time.sleep(0.08)
    actual_x, actual_y = win32gui.GetCursorPos()
    return abs(actual_x - screen_x) <= 1 and abs(actual_y - screen_y) <= 1, actual_x, actual_y


def _press_key_held(key, hold_seconds=0.08):
    """用扫描码按下并保持一小段时间，避免游戏帧轮询漏掉极短按键。"""
    downed = pydirectinput.keyDown(key)
    time.sleep(hold_seconds)
    upped = pydirectinput.keyUp(key)
    return bool(downed and upped)


def _click_current_position():
    """在当前系统光标位置发送一次标准的合成左键点击。"""
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.1)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def _do_drag(hwnd, act, log_callback=None):
    """Execute a drag gesture: move to start, mouse-down, linear-interpolate to
    end over ``duration`` seconds, mouse-up. Guarantees mouse-up on any error so
    the button is never left stuck. Returns True on success."""
    from_rel = act["from"]
    to_rel = act["to"]
    duration = float(act.get("duration", 0.5))
    if duration <= 0:
        duration = 0.05

    _, _, sx0, sy0, width, height = relative_to_screen(hwnd, from_rel["x"], from_rel["y"])
    _, _, sx1, sy1, _, _ = relative_to_screen(hwnd, to_rel["x"], to_rel["y"])

    if win32gui.GetForegroundWindow() != hwnd:
        _emit("[Engine] 拖动前 Roblox 已失去焦点，序列中止。", log_callback)
        return False
    if not _move_and_verify(sx0, sy0)[0]:
        _emit(f"[Engine] 拖动起点定位失败: ({sx0},{sy0})", log_callback)
        return False
    if win32gui.GetForegroundWindow() != hwnd:
        _emit("[Engine] 拖动按下前 Roblox 已失去焦点，序列中止。", log_callback)
        return False

    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    mouse_down = True
    try:
        steps = max(1, int(duration / 0.01))
        for i in range(1, steps + 1):
            t = i / steps
            x = round(sx0 + (sx1 - sx0) * t)
            y = round(sy0 + (sy1 - sy0) * t)
            win32api.SetCursorPos((x, y))
            time.sleep(duration / steps)
        # 最终精确到位，确保终点坐标无误。
        win32api.SetCursorPos((sx1, sy1))
        time.sleep(0.05)
    finally:
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    _emit(
        f"[Engine] drag: ({from_rel['x']:.3f},{from_rel['y']:.3f})→"
        f"({to_rel['x']:.3f},{to_rel['y']:.3f})  {duration:g}s, "
        "physical_lock=keyboard+mouse",
        log_callback,
    )
    return True


def _set_physical_input_blocked(blocked):
    """
    短暂屏蔽真实键盘和鼠标，防止用户输入进入临时置前的 Roblox。

    调用线程通过 SendInput/mouse_event 生成的输入仍可执行。该功能需要管理员权限。
    """
    return bool(ctypes.windll.user32.BlockInput(bool(blocked)))


def run_action_sequence(hwnd, steps, start_click_rx=None, start_click_ry=None, log_callback=None):
    """旧 AE 兼容包装器。保留旧契约（key 仅 1..6、合法 rx/ry），内部把旧步骤
    编译为通用 actions 并交给 run_input_actions 执行。对外函数名/参数/返回值不变。"""
    try:
        for index, step in enumerate(steps):
            key = str(step.get("key", "")).strip()
            if key not in {"1", "2", "3", "4", "5", "6"}:
                raise ValueError(f"步骤 {index + 1} 的按键无效: {key!r}")
            relative_to_screen(hwnd, step.get("rx"), step.get("ry"))
        if start_click_rx is not None or start_click_ry is not None:
            if start_click_rx is None or start_click_ry is None:
                raise ValueError("开始按钮必须同时提供 rx 和 ry")
            relative_to_screen(hwnd, start_click_rx, start_click_ry)
    except Exception as e:
        _emit(f"[Engine] 动作序列执行异常: {e}", log_callback)
        return False

    compiled = actions.compile_legacy_steps(steps, start_click_rx, start_click_ry)
    _emit(f"[Actions] compiled legacy steps: {len(steps)} steps -> {len(compiled)} actions", log_callback)
    result = run_input_actions(hwnd, compiled, log_callback=log_callback)
    if result and (start_click_rx is not None or start_click_ry is not None):
        client_x, client_y, screen_x, screen_y, width, height = relative_to_screen(
            hwnd, start_click_rx, start_click_ry
        )
        _emit(
            f"[Engine] 已点击开始按钮: client=({client_x},{client_y})/"
            f"{width}x{height}, screen=({screen_x},{screen_y}), "
            "physical_lock=keyboard+mouse",
            log_callback,
        )
    return result


def run_input_actions(hwnd, actions_list, log_callback=None):
    """执行一个短暂的、需要真实前台输入的输入会话，只处理 key / click / wait。

    通用执行器：不包含任何业务语义，也不限制按键的取值范围。继承旧执行路径已
    真实验证的会话行为：动作锁、全量预校验、保存/恢复前台与光标、全程输入锁定、
    置顶目标窗口、BetterClick（+1/-1 微移）与遮挡检查。
    """
    if not _ACTION_LOCK.acquire(blocking=False):
        _emit("[Engine] 已有动作序列正在执行，本次请求已拒绝，防止多个测试互相抢焦点。", log_callback)
        return False

    orig_hwnd = None
    orig_pos = None
    session_ok = False
    foreground_attempted = False
    try:
        if not win32gui.IsWindow(hwnd):
            _emit(f"[Engine] 目标窗口句柄无效: {hwnd}", log_callback)
            return False

        # 在改变焦点前完成全部动作校验，避免中途才发现非法输入。
        checked = actions.validate_actions(actions_list)

        orig_hwnd = win32gui.GetForegroundWindow()
        orig_pos = win32gui.GetCursorPos()

        # 必须在 Roblox 获得前台焦点前锁定。旧实现只在鼠标点击阶段锁定，
        # 用户键盘会在数字键发送前和步骤等待期间进入游戏。
        if not _set_physical_input_blocked(True):
            _emit(
                "[Engine] 无法锁定物理键盘和鼠标，已取消动作序列；"
                "请确认程序以管理员身份运行。",
                log_callback,
            )
            return False
        _emit(
            "[Engine] 已锁定物理键盘和鼠标，锁定范围覆盖整个动作会话。",
            log_callback,
        )

        foreground_attempted = True
        if not force_foreground(hwnd, log_callback=log_callback):
            return False
        time.sleep(0.15)

        for index, act in enumerate(checked):
            atype = act["type"]
            if atype == "key":
                if win32gui.GetForegroundWindow() != hwnd:
                    _emit(f"[Engine] 动作 {index + 1} 前 Roblox 已失去焦点，序列中止。", log_callback)
                    return False
                if not _press_key_held(act["key"], act["hold_seconds"]):
                    _emit(f"[Engine] 动作 {index + 1} 的按键 {act['key']!r} 发送失败，序列中止。", log_callback)
                    return False
            elif atype == "click":
                client_x, client_y, screen_x, screen_y, width, height = relative_to_screen(
                    hwnd, act["x"], act["y"]
                )
                moved, actual_x, actual_y = _move_and_verify(screen_x, screen_y)
                if not moved:
                    _emit(
                        f"[Engine] 光标未到达目标，期望=({screen_x},{screen_y})，"
                        f"实际=({actual_x},{actual_y})，序列中止。",
                        log_callback,
                    )
                    return False
                if win32gui.GetForegroundWindow() != hwnd:
                    _emit(f"[Engine] 动作 {index + 1} 点击前 Roblox 已失去焦点，序列中止。", log_callback)
                    return False
                hits_target, hit_hwnd, root_hwnd = _point_hits_window(hwnd, screen_x, screen_y)
                if not hits_target:
                    _emit(
                        f"[Engine] 目标点被其他窗口遮挡或句柄不匹配：point=({screen_x},{screen_y})，"
                        f"hit={hit_hwnd}，root={root_hwnd}，expected={hwnd}。",
                        log_callback,
                    )
                    return False
                # 部署键固定为鼠标左键。BetterClick 先用相对微移刷新游戏内
                # 光标状态，再发送一次左键点击。
                _click_current_position()
                _emit(
                    f"[Engine] click: client=({client_x},{client_y})/"
                    f"{width}x{height}, screen=({screen_x},{screen_y}), "
                    "confirm=BetterClick, physical_lock=keyboard+mouse",
                    log_callback,
                )
            elif atype == "drag":
                if not _do_drag(hwnd, act, log_callback):
                    return False
            elif atype == "wait":
                time.sleep(act["seconds"])
            else:
                raise ValueError(f"动作 {index + 1} 的类型无效: {atype!r}")

        session_ok = True
        return True
    except Exception as e:
        _emit(f"[Engine] 输入动作执行异常: {e}", log_callback)
        return False
    finally:
        try:
            # 输入保持锁定，直到焦点与鼠标都回到用户原来的窗口，避免释放瞬间
            # 用户仍在打字而 Roblox 尚未失焦。
            if (
                foreground_attempted
                and orig_hwnd
                and orig_hwnd != hwnd
                and win32gui.IsWindow(orig_hwnd)
            ):
                force_foreground(orig_hwnd, timeout=0.5, log_callback=log_callback)
            if orig_pos is not None:
                win32api.SetCursorPos(orig_pos)
        except Exception as restore_error:
            _emit(f"[Engine] 恢复焦点或鼠标位置失败: {restore_error}", log_callback)
        finally:
            # 最后一道保险：任何异常路径都不得让物理键盘和鼠标保持锁定。
            try:
                _set_physical_input_blocked(False)
            except Exception as unlock_error:
                _emit(f"[Engine] 解除物理键鼠锁失败: {unlock_error}", log_callback)
        _ACTION_LOCK.release()
        if not session_ok:
            _emit("[Engine] 输入动作序列未完成，请根据上方首个错误定位原因。", log_callback)
