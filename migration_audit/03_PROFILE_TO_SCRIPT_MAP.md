# 03 — 旧 Profile → 通用 Script 迁移模型

> 目标：**所有现有 Profile 都能自动迁移，无需用户手工重建**。本轮只设计映射，不写迁移代码。

---

## 1. 旧 schema（FACT，`profile_store.py:127-135` + 实际 `profiles/*/profile.json`）

```json
{
  "version": 1,
  "id": "e4b4c82e57e74460832b55340eda1d7a",
  "name": "T3",
  "steps": [
    { "key": "1", "rx": 0.6702747710241466, "ry": 0.73, "delay": 0.1 },
    { "key": "2", "rx": 0.48959200666111574, "ry": 0.5333333333333333, "delay": 0.1 }
  ],
  "created_at": "2026-08-09T10:27:06+08:00",
  "updated_at": "2026-08-09T10:29:15+08:00",
  "preview": null
}
```

字段全集：`version, id, name, steps[{key, rx, ry, delay}], created_at, updated_at, preview`
步骤字段全集：`key`（1..6）、`rx`（0..1）、`ry`（0..1）、`delay`（秒，≥0）。**没有** `enabled/name/slot/order`。

**一个旧 step 的执行语义（FACT，engine.py:296-347）**：
`Z重置 → 按数字键 key 选单位槽 → 移动到(rx,ry)点击放置 → sleep(delay)`。

---

## 2. 新 Script schema（v0.1，动作类型仅 §18 七种）

```json
{
  "version": 1,
  "id": "<uuid>",
  "name": "T3",
  "actions": [
    { "type": "key", "key": "1" },
    { "type": "click", "x": 0.6702, "y": 0.73 },
    { "type": "wait", "ms": 100 }
  ],
  "created_at": "...",
  "updated_at": "...",
  "thumbnail": null
}
```

动作类型（本轮范围）：`key` / `click` / `wait` / `find_image` / `click_image` / `if_image` / `repeat`。

---

## 3. 映射规则（关键）

> 旧 AE 的 `{key, rx, ry, delay}` 在语义上是 **“选单位→点放置→等待”三合一**。
> 但注意：**`delay` 是“点击之后、下一动作之前”的等待**，因此映射为**跟随该步的 `wait`**。

一个旧 step → 三个新 action：

| 旧字段 | 新 action | 说明 |
|---|---|---|
| `key` | `{ "type":"key", "key":"<key>" }` | 选单位槽 |
| `rx`,`ry` | `{ "type":"click", "x":rx, "y":ry }` | 放置点击 |
| `delay` | `{ "type":"wait", "ms": round(delay*1000) }` | 放置后等待 |

**示例**（对应旧 `{key:"1", rx:0.67, ry:0.73, delay:0.1}`）：

```json
[
  { "type": "key",  "key": "1" },
  { "type": "click", "x": 0.6702747710241466, "y": 0.73 },
  { "type": "wait", "ms": 100 }
]
```

（提示词 §17 的示例把 delay 映射成 wait 是对的；本表按真实执行顺序明确为“每步内：key→click→wait”。）

---

## 4. 无损性结论

**可无损迁移**：`key` `rx` `ry` `delay` 四字段 1:1 落入 key/click/wait；`name`→`name`；`id`/`created_at`/`updated_at` 原样保留；`preview`→`thumbnail`（文件名/尺寸/时间不变）。

**无法由数据字段表达、但**并非**数据丢失**的隐式行为（它们本来就**不在** Profile schema 里，而是写死在执行器 `run_action_sequence` 中，FACT）：

| 隐式行为 | 出处 | 迁移处置 |
|---|---|---|
| 每步前 `Z 重置` | engine.py:302 | AE 专用；通用 `key` 动作不含。若要复刻旧行为，需在脚本里**显式**加 `{type:"key", key:"z"}`，或保留“AE 部署复合动作” |
| 前置 `BlockInput` + `force_foreground` | engine.py:278/292 | 运行器级，非数据级；新 runner 是否锁输入属运行器策略 |
| BetterClick 微移 | engine.py:198-204 | 运行器级 `click` 的实现细节 |
| 遮挡校验 `_point_hits_window` | engine.py:327 | 运行器级 |
| key∈{1..6} 校验 | engine.py:262 | 旧校验约束；新 `key` 动作放行任意字符，兼容超集 |

**结论**：**所有现有 Profile 均可自动迁移**，四个数据字段无损。唯一的语义等价代价是：若希望迁移后的脚本**行为与旧 AE 完全一致**，需要在新 runner 里保留“AE 部署复合动作”（z 重置 + 选槽 + 点击 + 等待 + 开始按钮点击）这一 AE 语义层，或把 z 重置显式编入迁移产物。**建议第一版：迁移时把 z 重置显式写为一个 `key:"z"` 动作**，这样脚本自描述、且旧 AE 行为可复现（见 04 的验收）。

---

## 5. 与 v0.1 动作集的缺口（FACT，明确列出）

旧 AE **不产生**、也不需要以下字段，故无迁移缺口：`find_image`（对应“开始按钮识别”目前是**运行器级固定逻辑**，非脚本动作）、`if_image`、`repeat`、`click_image`。这些是 v0.1 新增能力，旧数据里没有对应物，不是“丢字段”。

需**泛化时新增**（非迁移必须）：
- 脚本级“开始条件”：旧 AE 的 `start_btn` 模板 + 0.85 阈值 + 绿色兜底 → 未来作为脚本前置 `find_image` / 循环 `if_image`，但**迁移 v0.1 阶段先不搬**，保持旧 AE 运行器里的固定逻辑不变。
