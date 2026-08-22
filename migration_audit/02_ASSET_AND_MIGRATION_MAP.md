# 02 — 资产与迁移地图（文件级 + 函数级 + 专用逻辑扫描）

> 裁决词：`KEEP` `KEEP_AND_WRAP` `EXTRACT` `GENERALIZE` `REWRITE_LATER` `DROP` `UNKNOWN`
> 证据等级：`FACT` / `INFERENCE` / `UNKNOWN`

---

## A. 文件级裁决

| 文件 | 当前职责 | 裁决 | 理由 |
|---|---|---|---|
| `engine.py` (415 行) | Roblox 窗口定位 / ImageGrab 截图 / pydirectinput 键 + win32 鼠标 / 焦点 / BlockInput | **KEEP** | 纯后台、无 Tk import、无 AE 文案、`tests/test_engine.py` 覆盖坐标/输入/锁/窗口发现。是“生产基线”里最干净、最值得原样保留的部分。 |
| `gui_app.py` (2483 行) | GUI + 识图 + 挂机循环 + 方案管理 + 预览 + 夜间屏幕 | **EXTRACT**（渐进，不重写） | 三个职责混在单文件：识图(§B)、挂机循环(§C)、GUI。按风险最小逐段抽出；UI 与数据模型优先保留。 |
| `profile_store.py` (298 行) | 方案 JSON 持久化（原子写 + .bak + .trash） | **GENERALIZE** | 磁盘格式与读写逻辑极稳（测试覆盖 9 例）；只需把“部署方案/步骤”术语与 schema 泛化为“脚本/动作”。文件本身几乎可原样保留。 |
| `display_dimmer.py` (140 行) | Windows 伽马调暗（不影响截图像素） | **KEEP** | 与 Roblox 自动化正交的舒适功能，独立、自包含、有测试。 |
| `preview_dialog.py` (87 行) | 方案预览图查看器（PIL+Canvas） | **KEEP** | 纯展示、独立。 |
| `config.json` | 旧版步骤数组（已被 ProfileStore 一次性导入） | **DROP**（保留文件作 legacy 导入源） | 迁移后无运行时引用；仅 `ProfileStore(legacy_config_path)` 首次初始化读一次。 |
| `templates/start_btn.png` + `.json` | “开始游戏”按钮模板 + 点击锚点元数据 | **GENERALIZE** | 会成为通用 `FindImage` 资产（模板 + 锚点 + 阈值）。元数据 schema 已足够通用。 |
| `profiles/` | 方案库（index.json + 每方案目录） | **GENERALIZE** | 变 `scripts/`；index/原子写/.bak/.trash 机制全保留。 |
| `night_screen.json` | 夜间屏幕配置 | **KEEP**（附属） | |
| `clash_proxies.json` | Clash 代理配置 | **DROP** | **FACT**：全仓 `.py` 无任何引用（grep 仅命中 venv 第三方库），属误入目录的无关文件。 |
| `requirements.txt` | 依赖 | **KEEP** | opencv / Pillow / pywin32 / numpy / pydirectinput，通用脚本软件同样需要。 |
| `diagnostics/` | 诊断关键帧（磁盘） | **KEEP** | 落盘不占常驻内存；`≤30张/次` 有上限（gui_app.py:2270）。 |
| `tests/` | 4 个测试文件 | **KEEP** | 迁移的回归保障，直接复用。 |

---

## B. 截图路径审计（§4 重点）

| 项 | 内容 | 证据 |
|---|---|---|
| 函数 | `engine.capture_window(hwnd)` | engine.py:113 |
| 文件 | engine.py | — |
| Capture API | **`PIL.ImageGrab.grab(bbox, all_screens=True)`** | engine.py:132 |
| 是否 PrintWindow/BitBlt | **否**。生产 AE 只用 ImageGrab。PrintWindow 仅在 `experiments/background_input_lab*/capture/capture.py`，非生产 | capture.py（实验） |
| Roblox 状态要求 | 调用本身不校验前台；但 **ImageGrab 抓屏幕矩形，识别正确要求 Roblox 在该矩形可见且未被遮挡** | engine.py:113-138（无 force_foreground/遮挡校验） |
| 调用时机 | **每轮 `while` 循环重新抓**（无缓存） | gui_app.py:2335 |
| 输出对象 | `cv2.cvtColor(np.array(img), RGB2BGR)` → **BGR ndarray** | engine.py:135 |
| 生命周期 | 局部变量 `frame`，每轮重赋值，循环尾随 GC；**无 `self.last_frame` / 无缓存** | gui_app.py:2335 |
| 是否实时 | **FACT**（源码：每次新建）+ **实测**（探针 8 帧 8 hash 全不同） | freshness_probe.py |
| 是否裁 Region | 是，按 `ClientToScreen(0,0)+GetClientRect` 的 bbox 整窗裁剪（非识图 ROI） | engine.py:123-129 |
| DPI/多屏 | `configure_dpi_awareness()` Per-Monitor V2 在建 Tk 前设置；`all_screens=True` 支持多屏 | gui_app.py:13-34; engine.py:132 |

**关键回答（§4 A–D）**：

- **A**：`FACT`——`farming_loop` 每次迭代都调用 `capture_window`（L2335）重新抓当前 Roblox 客户区，无跨轮复用。
- **B**：`FACT`——代码中**不存在** `self.last_frame`、cached screenshot、把 preview 图用于识图的路径。`self.steps_snapshot` 存的是动作数据非图像；`diagnostics` 落盘。唯一跨轮保留的图像是**识别模板** `start_tpl`（L2317，只读参考）。
- **C**：`FACT`——`capture_window` **不强制也不校验前台**；挂机循环在抓帧前**不**调用 `force_foreground`（force_foreground 只在 `run_action_sequence` 内、且是在抓帧**之后**才发生）。故“识别正确”依赖用户保持 Roblox 可见。
- **D**：`FACT`——截图到识图之间**无 GUI preview / resize / cache** 参与；`frame` 直入 `analyze_template_match`（L2334→L2344）。

> 迁移含义：这是与“新项目截图 freshness 错误”同类风险的源头——**旧 AE 的识图其实是“屏幕矩形实时采样”，其正确性绑定在“Roblox 处于前台且未被遮挡”这一外部前提上**，源码本身未显式保证。

---

## C. 识图（Vision）审计（§5）

| 项 | 模板匹配 | 绿色几何兜底 |
|---|---|---|
| 函数 | `analyze_template_match` gui_app.py:2144 | `detect_start_button_by_color` gui_app.py:2174 |
| 调用 | `cv2.matchTemplate(..., TM_CCOEFF_NORMED)` L2161 | `cvtColor(BGR2HSV)` + `inRange((35,100,100),(95,255,255))` + `findContours` L2187-2193 |
| 模板路径 | `templates/start_btn.png` | 无（颜色驱动） |
| 加载时机 | `farming_loop` 开头 `cv2.imread` 一次，长期复用 | 每次调用现算 |
| 阈值 | **0.85**（L2347 传入） | 几何+占比+填充率硬过滤（见下） |
| search region | 整帧 | 整帧（靠几何条件限定中心区） |
| 返回坐标 | `max_loc + 锚点偏移` → 归一化 `cx/(w-1), cy/(h-1)` L2164-2171 | 轮廓中心 → 归一化 L2238-2239 |
| 优先级 | 先试，命中即用 | 模板未命中时才用（L2360-2368） |

**模板点击锚点**：`load_template_click_anchor`（L2105）读 `start_btn.json` 的 `click_offset_x/y`（相对模板内），缺省回退模板中心 `(0.5,0.5)`。`start_btn.json` 实测含 `click_offset_x=0.6075, click_offset_y=0.5319, template_width=266, height=48`。

**绿色兜底的几何条件（FACT，L2212-2223）**：宽度占比 0.15–0.27、高度占比 0.02–0.055、长宽比 6.0–10.5、中心 rx 0.38–0.62 / ry 0.16–0.36、填充率≥0.72，再按中心/长宽比/填充率打分取最高。

**为什么存在（FACT，源码注释 L2174-2179）**：“用于 Roblox 在鼠标位于窗口外时按钮外观发生变化的情况”——即按钮 hover 态变化使固定模板失配时的兜底。

**抽成通用 `find_image()` 的设计结论（本轮只设计不实现）**：
- 现有“模板匹配 + 锚点偏移 + 置信度阈值 + 颜色几何兜底”这四要素，**语义上已经是 `FindImage`**。
- 抽法：把 `analyze_template_match` / `match_template_location` / `detect_start_button_by_color` / `load_template_click_anchor` 作为纯函数抽到 `vision.py`；`farming_loop` 的调用点与阈值 0.85、兜底顺序、归一化公式**逐字不变**。旧 AE 行为即可完全保留。
- “开始按钮”这一语义通过“资产名 start_btn + 绿色兜底硬编码几何”表达，泛化时绿色兜底作为可选 `find_by_color` 策略保留，但**不进入 v0.1 的 FindImage 主路径**。

---

## D. 动作 schema 审计（§6）

**FACT**——Profile 步骤的真实 schema（`profile_store.py:normalize_steps` L25-42，磁盘见 `profiles/*/profile.json`）：

```json
{ "key": "1", "rx": 0.67, "ry": 0.73, "delay": 0.1 }
```

- `key` ∈ {"1".."6"}（单位槽，**AE 专用**：`run_action_sequence` L262 与 `normalize_steps` L29 双重校验）
- `rx`,`ry` ∈ [0,1] 相对客户区坐标
- `delay` ≥ 0（秒）
- **没有** `enabled/name/slot/order` 等字段——顺序即 `steps` 数组下标；无“跳过/禁用”概念。

**FACT**：一个旧 step 的真实执行语义（engine.py:296-347）= `Z重置 → 按 key → 移到(rx,ry)点击 → sleep(delay)`。即 **key+click+wait 三合一**的复合部署动作，`delay` 在点击之后、下一动作之前。

**字段在各层如何使用（FACT）**：

| 层 | key | rx/ry | delay |
|---|---|---|---|
| GUI（add_step/编辑） | `cb_key` 下拉 1–6 | `pick_relative_coordinate` 选点写 `step_data["rx/ry"]`；坐标 label 显示 | `ent_delay` Entry |
| Profile Store | `normalize_steps` 校验 1–6 | 校验 0–1 | 校验 ≥0 |
| Runtime 快照 | `build_current_steps_sequence` 读控件 → 不可变 dict | 同左 | 同左 |
| engine | `run_action_sequence` 校验 1–6 → `_press_key_held(key)` | `relative_to_screen` → `_move_and_verify` | `sleep(delay)` |

---

## E. 动作执行器审计（§7）

| 函数 | 输入 | 返回 | 副作用 | 依赖 | 验证 | 异常行为 | 保留建议 |
|---|---|---|---|---|---|---|---|
| `run_action_sequence` engine.py:239 | hwnd, steps, start_click_rx/ry, log_callback | bool | 抢焦点、锁/解锁 BlockInput、改光标、发键鼠 | force_foreground, 各私有函数 | `test_engine.py` 7 例 | 任一校验失败即 `return False`；`finally` 必恢复焦点+光标+解锁+释放锁 | **EXTRACT**（执行器语义通用，AE 语义是 z-reset 与 key∈1..6） |
| `_move_and_verify` engine.py:198 | screen_x, screen_y | (bool, x, y) | SetCursorPos + 两次 mouse_event MOVE | win32api | 测试覆盖 wiggle 序列 | 光标未达目标返回 False | **KEEP**（BetterClick 通用） |
| `_press_key_held` engine.py:215 | key, hold_seconds | bool | pydirectinput keyDown/Up | pydirectinput | 测试 mock 覆盖调用 | — | **KEEP** |
| `_click_current_position` engine.py:223 | — | None | mouse_event LEFTDOWN/UP | win32api | 测试覆盖 | — | **KEEP** |
| `_point_hits_window` engine.py:188 | hwnd, sx, sy | (bool, hit, root) | 无 | win32gui WindowFromPoint | 未单测（被 run_action_sequence 间接覆盖） | 异常返回 False | **KEEP** |
| `_set_physical_input_blocked` engine.py:230 | blocked | bool | BlockInput | user32 | 测试覆盖 lock/unlock 顺序 | 失败返回 False → 上层中止 | **KEEP** |

**输入机制结论（FACT）**：
- 键盘 = `pydirectinput.keyDown/keyUp`（engine.py:215-220）。
- 鼠标 = `win32api.SetCursorPos`（engine.py:205）+ `win32api.mouse_event`（engine.py:207-209/225-227）。
- wiggle +1/-1：**FACT**——源码注释明确“让游戏刷新鼠标位于客户区内的状态”（engine.py:202-204）；用于规避 Roblox 对合成输入/光标态的判断，配合 `_point_hits_window` 遮挡校验。

---

## F. GUI 交互审计（§8）

- **动作列表新增/删除/排序**：`add_step`(L1233)、`delete_step`(L1346)、`update_step_numbers`(L1354)。**无拖拽排序**，顺序=添加顺序；删除后重编号。排序能力缺失是已知 gap，但“增删改”交互成熟，应**保留 UI、替换数据模型**。
- **坐标设置**：`pick_relative_coordinate`(L1367) —— 在 Roblox 客户区铺半透明遮罩 + crosshair，点击后 `ScreenToClient` → `rx=cx/(cw-1), ry=cy/(ch-1)` 写回。**这比新项目 picker 更成熟**（有负坐标显示器/多屏处理、越界拒绝、误点自己窗口提示）。另有 `test_single_step`/`test_all_steps` 现场试放。**FACT**：坐标拾取与试放是现成的、可保留。
- **Profile UI**：新建/另存/重命名/删除/切换（L1789-1867），含未保存保护 `resolve_unsaved_profile`(L1720)、删除进 `.trash` 可恢复。→ 直接泛化为 Project/Script。
- **Start/Stop**：`start_farming`(L2056)/`stop_farming`(L2096)。重复点击防护：`start_farming` 未显式判 `self.running`，但启动后 `btn_start` 置 disabled(L2086)；`test_all_steps` 判 `self.running`(L2006)。`stop_farming` 幂等（可重复点）。

---

## G. Profile Store 审计（§9）

| 项 | 事实 |
|---|---|
| 文件格式 | `index.json`（version/active_profile_id/profile_order）+ 每方案目录 `profile.json`（version/id/name/steps/created_at/updated_at/preview） |
| 目录 | `profiles/<uuid>/`；`.trash` 回收 |
| 版本 | `SCHEMA_VERSION = 1` |
| 默认值 | 无方案时建“默认方案”，单步 `{key:1,rx:.5,ry:.5,delay:.5}`；或从旧 config.json 导入 |
| 异常文件 | 读损坏先回退 `.bak`，再失败抛 `ProfileStoreError` |
| 原子保存 | `mkstemp`+`fsync`+`os.replace`，写前 `copy2` 到 `.bak`（L57-85） |
| 覆盖策略 | 保存=整文件覆盖当前方案 |
| 排序 | `profile_order` 数组；新方案 append |
| ID/名称规则 | id=uuid4().hex；名称 1–60 字符、大小写不敏感去重 |

**裁决**：JSON 读写/原子写/.bak/.trash/去重**全保留**；需泛化的是**术语与 schema**（方案→脚本、步骤→动作）与 `key∈1..6` 的校验；可删除的 AE 固定字段=无（`preview` 也是通用缩略图概念）。

---

## H. 线程 / Stop / Cancellation 审计（§10）

| 问 | 答（FACT/INFERENCE） |
|---|---|
| 线程创建 | `start_farming` L2093 建**单条 daemon** `loop_thread`；`test_single_step` L1982、`test_all_steps` L2044 各建短 daemon 线程 |
| running flag | `self.running` 布尔，`farming_loop` 在 `while self.running` 顶部 + 各 sleep 后轮询 |
| sleep | **大量 blocking `time.sleep`**：2/1/3/5 秒（L2339/2450/2456/2445 等）；无事件通知 |
| Stop | `stop_farming` 只置 `running=False`；**FACT：最坏 ~5 秒生效**（L2445 sleep(5)） |
| Stop 时按键/鼠标/BlockInput | **FACT：不持有**——这些只在 `run_action_sequence` 内短暂持有，函数返回前 `finally` 已释放 |
| 异常退出 finally | **FACT**：`run_action_sequence` 有完整 `finally`（恢复焦点/光标/解锁/释放锁 L391-412） |
| GUI 关闭时 Worker | **FACT**：`on_close`(L832) 置 `running=False` 后直接 `root.destroy()`，**不 join、不显式解锁 BlockInput**；daemon 线程随进程退出终止。**UNKNOWN**：若关闭恰在序列执行中，依赖系统在进程终止时释放 BlockInput（未实测） |

---

## I. BlockInput 审计（§11）

- **lock**：`run_action_sequence` L278 `_set_physical_input_blocked(True)`，在 `force_foreground` **之前**（源码注释 L276-277 说明原因）。
- **unlock**：`finally` L409 `_set_physical_input_blocked(False)`，在恢复焦点+光标**之后**（注释 L393-394 说明顺序）。
- **谁负责**：仅 `run_action_sequence`，单点。
- **重复 lock**：`_ACTION_LOCK`(threading.Lock, blocking=False) 拒绝并发序列，天然防重复；BlockInput 本身无幂等防护，靠此锁串行化。
- **重复 unlock**：`finally` 无条件执行一次 unlock。
- **异常恢复**：任何 return/raise 都走 `finally` unlock。
- **FACT（核心结论）**：旧 AE **只在实际执行摆放+点击开始期间 Lock，整个挂机周期不 Lock**。挂机等待期间用户键盘鼠标可正常使用（除夜间调暗外不影响输入）。

---

## J. 内存 / 资源（仅迁移视角，§12）

| 项 | 判定 | 证据 |
|---|---|---|
| Tk Text 日志无界追加 | **FIX_LATER** | `process_logs` L1129 `txt_log.insert("end", ...)` 从不删除；state=0 每 ~1s 一行，长挂机单调增长 |
| 每轮截图三副本（PIL→np→BGR） | **NOT_PROBLEM** | 局部变量每轮重赋值后 GC，无缓存；非泄漏 |
| matchTemplate 结果数组 | **NOT_PROBLEM** | 每轮临时，尾随 GC |
| 绿色兜底 hsv/mask/contours | **NOT_PROBLEM** | 仅模板未命中时现算，临时 |
| 诊断帧 | **NOT_PROBLEM** | `≤30/次` 上限（L2270），落盘 |
| 识别模板 start_tpl | **NOT_PROBLEM** | 一次加载，266×48 |
| log_queue | **NOT_PROBLEM** | 每 100ms 排空（L1125）；日志速率受循环 sleep 限制 |
| DisplayDimmer 缓存的 gamma ramp | **NOT_PROBLEM** | 每显示器 768 word，极小 |
| 预览图（PreviewDialog） | **NOT_PROBLEM** | 仅弹窗打开时持有 `source_image`+`ImageTk` |
| 线程 | **NOT_PROBLEM** | 单 daemon 循环线程 + 短测试线程，无重复创建 |

**结论**：**没有 MUST_FIX_BEFORE_MIGRATION**（迁移本身不引入新内存问题）。唯一值得在迁移中顺带处理的是 **Tk 日志无界追加 → FIX_LATER**（可加行数上限，与迁移解耦）。

---

## K. AE 专用逻辑扫描（§13，全部 FACT）

**Generic（可直接复用）**：`find_roblox_hwnd`、`capture_window`、`force_foreground`、`relative_to_screen`/`normalized_to_client_point`、`get_window_process_info`、`get_process_resource_usage`、`_move_and_verify`、`_press_key_held`、`_click_current_position`、`_point_hits_window`、`_set_physical_input_blocked`、ProfileStore 的 JSON/原子写/回收、日志、线程。

**Genericizable（换语义/资产即可通用）**：
- Start 识别 → `FindImage`（模板+锚点+阈值通用；绿色兜底是可选策略）
- 动作序列 → 通用 Action 列表（去掉 z-reset 与 key∈1..6 即可）

**AE-only（源码语义明确）**：
- `key∈{1..6}` 单位槽校验（engine.py:262, profile_store.py:29）
- 每步 `_press_key_held("z", 0.05)` 重置（engine.py:302）
- “开始游戏”按钮语义 + `detect_start_button_by_color` 的绿色几何硬编码（gui_app.py:2174-2248）
- 固定 `state∈{0,1}` 二态挂机机（gui_app.py:2326-2461）
- 文案：“单位槽/放置坐标/开始挂机/部署动作/截取开始按钮/夜间屏幕”等

**Dead / Legacy**：
- `config.json`：已迁移为“默认方案”，无运行时引用（仅首次导入）
- `clash_proxies.json`：全仓无引用（grep 仅命中 venv）
- `gui_app.py:1750 save_config()`：仅 `return self.save_active_profile(...)` 的兼容别名，无独立调用点
