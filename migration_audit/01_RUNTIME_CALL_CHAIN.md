# 01 — 旧 AE 真实运行调用链

> 证据等级：`FACT` = 源码可直接证明；`INFERENCE` = 由调用关系可较有把握推导；`UNKNOWN` = 证据不足。
> 所有行号对应当前源码（`gui_app.py` 2483 行、`engine.py` 415 行）。

---

## 0. 进程启动

```
run.bat  (管理员提权)
  └─ .\venv\Scripts\pythonw.exe gui_app.py
        └─ gui_app.py:2467  if __name__ == "__main__":
             └─ tk.Tk() → AEAutomationApp(root) → root.mainloop()
```

- `gui_app.py:13-34` `configure_dpi_awareness()` 在 import Tk 之前设置 `SetProcessDpiAwarenessContext(-4)`（Per-Monitor V2），失败逐级回退。`DPI_MODE` 是模块级全局（L34）。
- 模块级副作用（L59-65）：`logging.basicConfig(filename=automation.log)`。

**FACT**：入口是 `gui_app.py`；`engine.py` 是纯后台引擎（无 Tk import，可独立 import/测试）。

---

## 1. GUI 初始化

```
AEAutomationApp.__init__  (gui_app.py:68)
  ├─ 状态属性 (L77-106)：hwnd=None, steps=[], running=False, loop_thread=None,
  │   log_queue=queue.Queue(), steps_snapshot=[], recognition_poll_number=0,
  │   diagnostic_saved_count=0, display_dimmer=DisplayDimmer(), ...
  ├─ os.makedirs(TEMPLATES_DIR, DIAGNOSTICS_DIR)   L109-110
  ├─ setup_styles()   L113  (暗色 ttk "clam" 主题)
  ├─ create_widgets()  L116 (全部 GUI 布局)
  ├─ load_config()     L119 → ProfileStore 载入
  ├─ load_night_screen_config()  L120
  ├─ root.protocol("WM_DELETE_WINDOW", self.on_close)  L122
  ├─ root.after(100, process_logs)            L125
  ├─ root.after(100, poll_emergency_brightness_hotkey)  L126
  ├─ refresh_windows()  L128 → engine.find_roblox_hwnd()
  └─ root.after(120, set_initial_pane_positions)  L130
```

**FACT**：`DisplayDimmer` 在 `__init__` 即构造（L91），会枚举显示器并缓存原始伽马 ramp（`display_dimmer.py:97`），常驻但极小。

---

## 2. 加载 Profile → 动作进入 GUI

```
load_config()  gui_app.py:1754
  └─ ProfileStore(PROFILES_DIR, CONFIG_FILE)   profile_store.py:45
       ├─ 若无 profiles/index.json：从旧 config.json 导入（normalize_steps）
       └─ 已有 index.json：校验版本/active_profile_id/profile_order
  └─ active_profile_id = store.active_profile_id()
  └─ load_profile_into_ui(active_profile_id)   gui_app.py:1698
       ├─ clear_steps()
       └─ 对每个 profile["steps"] 项调用 add_step(key,rx,ry,delay,mark_dirty=False)
            gui_app.py:1233 → 创建 step_frame（单位槽 cb_key=1..6、
            坐标 label、间隔 entry、选取/测试/删除按钮），step_data 存入 self.steps
```

**FACT**：动作数据以 `self.steps`（list of `step_data` dict，含 `rx/ry` + Tk 控件引用）驻留内存；`step_data` 的 `rx/ry` 是运行时的**可变真值**，`cb_key.get()`/`ent_delay.get()` 在保存时实时读取。

**FACT**：Profile 磁盘 schema = `{version, id, name, steps[{key,rx,ry,delay}], created_at, updated_at, preview}`（见 `profile_store.py:127-135`、实际 `profiles/*/profile.json`）。

---

## 3. 点击 Start → 启动 Worker

```
start_farming()  gui_app.py:2056
  ├─ 校验 hwnd 非空
  ├─ 校验模板：cv2.imread(start_btn.png) 尺寸≥20×10 且 start_btn.json 存在  L2061-2076
  ├─ self.steps_snapshot = build_current_steps_sequence()   L2080
  │     build_current_steps_sequence()  L1984：
  │       在 Tk 主线程遍历 self.steps，读取 cb_key/ent_delay/rx/ry，
  │       产出不可变 list[{key,rx,ry,delay}]（后台线程不再读 Tk 控件）
  ├─ self.running = True；按钮状态翻转  L2085-2088
  ├─ 若 night_auto：apply_night_screen(automatic=True)  L2091-2092
  └─ self.loop_thread = threading.Thread(target=self.farming_loop, daemon=True)
        .start()   L2093-2094
```

**FACT**：Worker 是**单条 daemon 线程**跑 `farming_loop`。`steps_snapshot` 是 Tk 主线程内制作的不可变快照，后台线程只读它。

---

## 4. 主循环：寻找/前台/截图/识别/触发

```
farming_loop()  gui_app.py:2316
  ├─ start_tpl = cv2.imread(templates/start_btn.png)   ← 模板只加载一次  L2317
  ├─ start_click_anchor = load_template_click_anchor("start_btn.png")  L2318
  ├─ state = 0   (0=等“开始游戏”按钮；1=战斗中/等下一局)
  │
  └─ while self.running:                              L2330
       ├─ monitor_roblox_resources()                 L2332（每60s读Roblox资源）
       ├─ frame = engine.capture_window(self.hwnd)    L2335  ← 每轮重新抓帧
       │     engine.capture_window  engine.py:113
       │        ClientToScreen(hwnd,(0,0)) + GetClientRect
       │        → ImageGrab.grab(bbox, all_screens=True)
       │        → np.array(img) → cv2.cvtColor(RGB2BGR)
       ├─ analyze_template_match(frame, start_tpl, 0.85, anchor)  L2344
       │     gui_app.py:2144 → cv2.matchTemplate(TM_CCOEFF_NORMED) L2161
       │        → minMaxLoc → 置信度 + 模板内点击锚点 → 归一化相对坐标
       ├─ 若未匹配：detect_start_button_by_color(frame)  L2361
       │     gui_app.py:2174 → BGR2HSV → inRange(绿 35..95) → findContours
       │        → boundingRect → 长宽比/屏幕占比/中心/填充率过滤 → 打分
       ├─ 记录识别日志 + 按需存诊断帧(≤30/次)  L2371-2420
       │
       ├─ [state==0 且 match_result 命中]
       │     └─ engine.run_action_sequence(hwnd, steps_seq,
       │            start_click_rx=rx, start_click_ry=ry, log_callback)  L2435
       │           （见 §5）
       │     └─ ok → state=1; sleep(5)   L2443-2445
       │     └─ fail → 保持 state=0; sleep(3)  L2446-2448
       │
       ├─ [state==0 未命中] → sleep(1)  L2450
       │
       ├─ [state==1 未命中] → sleep(3)  L2456（战斗进行中）
       └─ [state==1 又命中] → 下一局已加载 → state=0; sleep(1)  L2457-2461
```

**FACT**：`capture_window` 在每次 `while` 迭代都被调用（L2335），循环体内**没有任何 frame 缓存**；模板 `start_tpl` 是唯一跨迭代保留的图像对象，且只在识别时作为参考模板。

**FACT（实验佐证）**：只读探针 `experiments/freshness_probe.py` 连续 8 次调用 `capture_window`，8 个 hash 全部不同、每帧 ~98–129ms、亮度 ~106–108（非黑）→ 移动画面下每次返回新抓像素。

**INFERENCE**：`ImageGrab.grab` 抓的是**屏幕矩形**，故识别“正确”隐含 Roblox 在该矩形处**可见且未被遮挡**；`capture_window` 自身不强制前台、不校验遮挡。若 Roblox 被遮挡/最小化，识图看到的是遮挡物内容。生产代码里**没有** PrintWindow 路径（PrintWindow 只存在于 `experiments/background_input_lab*/capture/capture.py`，非生产）。

---

## 5. 动作序列执行（engine.run_action_sequence，engine.py:239）

```
run_action_sequence(hwnd, steps, start_click_rx, start_click_ry, log_callback)
  ├─ _ACTION_LOCK.acquire(blocking=False)   L244  ← 拒绝并发序列
  ├─ 预校验全部 step：key ∈ {1..6}；rx/ry 经 relative_to_screen 越界即 raise  L259-265
  ├─ orig_hwnd = GetForegroundWindow(); orig_pos = GetCursorPos()  L273-274
  ├─ _set_physical_input_blocked(True)   L278  ← BlockInput；失败(非管理员)则中止
  ├─ force_foreground(hwnd)              L292  ← 置顶+抢焦点(含 Alt 重试)
  ├─ 逐 step 循环  L296-347：
  │    ├─ 校验 GetForegroundWindow()==hwnd
  │    ├─ _press_key_held("z", 0.05)     L302  ← Z 重置(清除上次残留部署态)
  │    ├─ _press_key_held(key, 0.06)     L307  ← pydirectinput 按数字键选单位槽
  │    ├─ _move_and_verify(screen_x, screen_y)  L314
  │    │     engine.py:198 → SetCursorPos + mouse_event(MOVE,+1) + mouse_event(MOVE,-1)
  │    │        （BetterClick 微移，见源码注释 L202-204：刷新 Roblox 游戏内光标态）
  │    ├─ 校验前台；_point_hits_window(hwnd,...)  L323-334（WindowFromPoint 遮挡校验）
  │    ├─ _click_current_position()      L338  ← mouse_event LEFTDOWN/LEFTUP
  │    └─ sleep(delay)                   L347
  ├─ 点击“开始游戏”（若给 start_click_rx/ry） L349-384：同样的 move/verify/point-hits/click
  └─ finally  L391-412：
       ├─ force_foreground(orig_hwnd) 恢复原前台  L395-401
       ├─ SetCursorPos(orig_pos) 恢复鼠标    L402-403
       ├─ _set_physical_input_blocked(False) 解锁  L407-411
       └─ _ACTION_LOCK.release()
```

**FACT**：BlockInput **只在 `run_action_sequence` 内锁定**（L278 锁定、L409 解锁），**整个挂机循环期间不锁**。挂机循环等待期间只做截图+匹配+sleep，不持有输入锁。

**FACT**：键盘用 `pydirectinput`（`_press_key_held`，engine.py:215）；鼠标用 `win32api.SetCursorPos` + `win32api.mouse_event`（`_move_and_verify`/`_click_current_position`，engine.py:198/223）；坐标换算用 `normalized_to_client_point` → `round(rx*(width-1))`（engine.py:38）。

**FACT**：`_move_and_verify` 的 +1/-1 微移目的在**源码注释里写明**（engine.py:202-204“刷新 Roblox 游戏内‘鼠标位于客户区内’状态”），非仅我的推断。

---

## 6. 停止 / 关闭

```
stop_farming()  gui_app.py:2096
  ├─ self.running = False   ← 仅置标志
  ├─ 按钮状态翻转
  └─ 若 display_dimmer.active_level<100 → restore_night_screen()

on_close()  gui_app.py:832
  ├─ resolve_unsaved_profile("关闭程序", ...)  ← 未保存弹窗
  ├─ self.running = False；selection_in_progress=False
  ├─ 销毁遮罩、save_night_screen_config()、display_dimmer.restore()
  └─ root.destroy()
```

**FACT**：Stop 只是置 `running=False`；`farming_loop` 在 `while self.running` 顶部（L2330）与各 `sleep` 之后才感知。

**FACT（最坏停止延迟）**：循环内存在 `sleep(5)`（成功部署后 L2445），故 **Stop 最坏约 5 秒才生效**（其余 sleep 为 1/2/3 秒）。这是**轮询式**停止，非事件式。

**INFERENCE**：`run_action_sequence` 内部不检查 `running` 标志，故 Stop 不能中断**正在执行中的**序列；序列自身的 `finally` 会完整恢复前台+鼠标+解锁 BlockInput（正常路径）。若用户在序列执行中直接关窗（`on_close`），daemon 线程可能被进程退出中断——依赖系统在进程终止时释放 BlockInput，属 `UNKNOWN`（未实测，见 02 的资源章节）。

---

## 7. 端到端一句话

```
run.bat → gui_app.__main__ → AEAutomationApp.__init__ → ProfileStore.load → add_step×N
→ 点“开始挂机” → start_farming → build_current_steps_sequence 快照 → farming_loop(daemon)
→ while running: capture_window(ImageGrab) → matchTemplate(0.85)/green-fallback
→ 命中 → run_action_sequence(BlockInput→置顶→Z→数字键→BetterClick移动→点击→delay→点击开始)
→ finally 恢复焦点+鼠标+解锁 → state=1 → 每3s轮询等下一局 → 又命中 → state=0 → 循环
```

**核心判定**：旧 AE 唯一“游戏内智能”就是**“开始游戏按钮是否出现在当前屏幕矩形”**这一布尔信号；动作执行是**固定 key+相对坐标+delay 的盲放宏**。没有相机/走位/升级/角色识别/富状态机（这些在 Phase 1 侦察已确认不存在）。
