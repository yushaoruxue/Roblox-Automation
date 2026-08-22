# 04 — 第一刀与后续迁移计划

> 原则：运行事实 > 架构美观。每一刀做完，**旧 AE 仍然能用**。渐进式、可回滚、每刀真实回归。

---

## 目标结构（仅作方向，不强行一次到位）

```
gui_app.py         GUI + 数据编辑（保留）
engine.py          Roblox / 输入 / 截图（保留）
vision.py          识图原语（新增，第一刀）
actions.py         动作模型 + 执行原语（后续）
script_runner.py   脚本执行循环（后续）
script_store.py    脚本持久化（后续）
models.py          数据模型（后续）
```

**不强行拆**：`farming_loop` 里的状态机若暂留 `gui_app.py` 风险最低，就先留。审计结论支持“先抽纯函数，再抽运行器”。

---

## 后续 Slice（最多 5 步，以源码分析为准）

| Slice | 内容 | 依据 |
|---|---|---|
| 1 | 识图原语抽到 `vision.py`（纯函数） | 见下“第一刀” |
| 2 | 动作模型 `models.py` + 执行原语抽到 `actions.py`（把 `run_action_sequence` 内层“key/click/wait”泛化，去掉 z 与 1..6 的 AE 硬编码，保留 `engine` 原语） | engine.py:239-347 |
| 3 | 挂机循环抽到 `script_runner.py`（先保持二态机，输入仍调 `engine`） | gui_app.py:2316-2465 |
| 4 | ProfileStore 泛化为 `script_store.py`（术语/校验泛化，JSON+原子写+.bak+.trash 原样保留） | profile_store.py |
| 5 | GUI 文案与数据编辑泛化（动作列表保留 UI、替换数据模型；坐标选点/试放 UI 保留） | gui_app.py:1233-1352, 1367-1448 |

> 每一步的验证门槛相同：**真实 AE 回归通过 + 旧 Profile 能跑**。不做“先重构完再祈祷能跑”。

---

## 最终裁决

```
RECOMMENDED FIRST SLICE:
把 Start 按钮识别的四个纯函数从 gui_app.py 抽到新文件 vision.py，
调用点与阈值/兜底顺序/归一化公式/锚点偏移全部保持不变；
gui_app.py 里的同名方法改为“薄委托”继续存在（保 API 与现有测试不变）。

WHY:
1) 这四个函数（analyze_template_match / match_template_location /
   detect_start_button_by_color / load_template_click_anchor）是当前唯一真实
   使用的“视觉能力”，且几乎全部是纯函数——抽取风险最低、可精确回归。
2) vision.py 正是目标结构里已预定的模块；“Start识别→FindImage”是路线第一步。
3) 不触碰 engine、不触碰 farming_loop 状态机、不触碰 Profile 数据格式，
   因此旧 AE 行为与数据 100% 不变。
4) 有现成单测直接背书：tests/test_gui_logic.py 的 TemplateAnchorTests 与
   green-fallback 测试会在抽取后原样通过（薄委托保持方法名）。

FILES TO MODIFY:
- 新建 D:\Gemas\Roblox_AE_Automation\vision.py
- 修改 D:\Gemas\Roblox_AE_Automation\gui_app.py（仅替换这四个方法体为委托调用）

FUNCTIONS TO TOUCH:
- gui_app.py: load_template_click_anchor (L2105)
- gui_app.py: match_template_location (L2124)
- gui_app.py: analyze_template_match (L2144)
- gui_app.py: detect_start_button_by_color (L2174)
（抽到 vision.py；gui_app 保留同名薄委托方法）

FILES/FUNCTIONS FORBIDDEN TO TOUCH:
- engine.py 全部（find_roblox_hwnd / capture_window / force_foreground /
  run_action_sequence / _move_and_verify / _press_key_held / _click_current_position /
  _point_hits_window / _set_physical_input_blocked）
- gui_app.py: farming_loop (L2316) 及其状态机、start_farming (L2056)、
  stop_farming (L2096)、build_current_steps_sequence (L1984)、
  pick_relative_coordinate (L1367)、start_crop_template (L1451)、
  process_logs (L1125) 等其余一切
- profile_store.py 全部
- display_dimmer.py / preview_dialog.py 全部
- 不改 templates/start_btn.png(.json)、不改 profiles/ 任何数据

REAL ROBLOX REGRESSION TEST:
1) python -m unittest tests.test_gui_logic tests.test_engine 全绿（识别/坐标/锁/窗口）。
2) 启动旧 AE：能载入现有 Profile（如 "T3"，7 步）；“开始按钮模板与点击锚点: 已就绪”。
3) 保持 Roblox 前台，点“开始挂机”：日志出现 [识别轮询 #n] ... template_confidence≈1.0
   时触发部署 → Roblox 实际放下单位并点击“开始游戏”→ state 进入“战斗/等待下局”。
4) 切到某障碍场景验证绿色兜底仍触发（或至少确认模板未命中时 fallback 分支不报错）。
5) “停止挂机”≤5s 生效；夜间屏幕 Ctrl+Alt+Home 恢复正常。

ROLLBACK:
git checkout 前，直接恢复：删除 vision.py 并把 gui_app.py 四方法体改回原实现
（即本次 diff 的逆操作）。因未改数据格式、未改 engine、未改状态机，
回滚后旧 AE 与迁移前逐字节行为一致。
```

---

## 附：第一刀内部结构（供评审，不实施）

```python
# vision.py（拟）
def load_template_click_anchor(templates_dir, filename, log) -> (float, float):
    ...  # 原 gui_app.py:2105 逻辑，log 改为注入
def match_template_location(full_img, template_img, threshold=0.85, click_anchor=(0.5,0.5)):
    ...  # 原 gui_app.py:2124
def analyze_template_match(full_img, template_img, threshold=0.85, click_anchor=(0.5,0.5)) -> dict:
    ...  # 原 gui_app.py:2144（matchTemplate TM_CCOEFF_NORMED + 锚点偏移 + 归一化）
def detect_start_button_by_color(full_img):
    ...  # 原 gui_app.py:2174（绿色几何兜底）
```

```python
# gui_app.py（拟，薄委托，行为不变）
from vision import analyze_template_match as _analyze, detect_start_button_by_color as _detect, ...
def analyze_template_match(self, full_img, template_img, threshold=0.85, click_anchor=(0.5,0.5)):
    return _analyze(full_img, template_img, threshold, click_anchor)
```

`load_template_click_anchor` 因使用 `self.log` 与 `TEMPLATES_DIR`，委托为
`vision.load_template_click_anchor(TEMPLATES_DIR, filename, self.log)`。
