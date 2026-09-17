# Q7 · 全盘复现交互 UI（回放浏览器）—— 任务文档

> 状态：**第一步（MVP）+ 第二步（±10s combat log 四 toggle / 技能 CD / 状态胜率）均已交付并发布公网**。
> **本任务回传**：`analysis/Q7_SUBMISSION.md`（口径说明 / 可复现脚本 / 验证记录 / 准确率边界 / 待拍板）。
> 交付物：`analysis/output_review/q7_replay_8955197224.html`、`q7_replay_8830423116.html`
> （公网 `https://bigfatblackwhale.github.io/DSH-Dota2/q7_replay_<match>.html`）。
>
> **第二步两个关键结论（与任务书 §5/§6/§10 相关，均已实测）**：
> 1. **技能 CD 不需要"冷却时长常量表"** —— `dems/db/<league>/<match>.db` 的 `ability_cd_start/end` +
>    `item_cd_start/end` 已带 `properties.remaining` = 实体 `m_fCooldown` 的**真实剩余冷却秒**
>    （含等级/天赋/减CD；实测 BKB 70.5~95s、刷新球 135~180s）。
>    `dems/db_full`（combat_log 版）里**没有**这些（CD 是实体派生、不是 combat 条目）→ **两库按 match_id join**。
>    **TP** 是充能制、库内无 CD/充能事件 → 页面只报"使用时刻 + 次数"，**不伪造三态**。
> 2. **胜率**按 §5.1"状态化、绝不偷看结果"实现：特征只用 t 时刻可观测的净值差+经验差+时刻，
>    标签用"远古被摧毁"判定，**按 match 切分训练/测试**；970 场 → **测试 AUC 0.830**（Brier 0.169，
>    分桶 AUC 0.69~0.91）。经济差与经验差实测正相关 r=0.52 → 单系数符号不具解释意义（已写进页面口径）。
>
> 定位：与 Q5B/Q6 的"统计热力图"是**不同产品**——这是一个**回放浏览器**（全盘复现）。
> ⚠️ **开工前必须按 §1 通读项目文档**，先理解本项目的大方向与方法论，再动手。
>
> **交付后的 UI 迭代（owner 逐项提，已全部落到公网）**：
> ① 地图保持正方形、宽度收敛；② 时间轴移出左栏 → **地图上方整宽面板**，
> 重大事件带 mm:ss、**上方=对天辉有利 / 下方=对夜魇有利**；③ 事件由文字标签改为**图标**
> （阵亡英雄头像 / 塔·兵营·基地·肉山剪影，**环色=所属方**：绿天辉 / 红夜魇 / 灰无主·肉山），
> mm:ss 改为默认关闭的开关；④ **combat log 占右半屏**（修掉 `.left` 未闭合、导致它被嵌进左栏的 bug）
> + **整页锁一屏**（地图按剩余高度取正方形边长、右栏内部滚动、时间轴层数随视口高自适应 2/3/4）；
> ⑤ 10 个头像**竖排到地图右侧**（owner 选方案①）→ 地图 ≈458 → **≈516px**。
> 逐条实现要点与踩坑见 `analysis/Q7_SUBMISSION.md` §8。

---

## 0. 一句话

把**一场比赛**做成"全盘复现"的交互 UI：**左侧地图 + 双时间轴 + 10 英雄头像；顶部经济/经验/胜率；右侧明细表**（默认 KDA + 正反补；点某英雄 → 显示该英雄 **±10s 的 combat log** 与**技能 CD**）。
**铁律：所有内容严格来自 combat log。**

---

## 1. 必读文档清单（开工前通读，理解大方向）

**项目宪法 / 方法论（先读这组）**
- `STRATEGY/00_AGENT_MAP.md` —— 四层协作结构（①地图 / ②数据 / ③逻辑 / ④总控）、核心原则。
- `STRATEGY/03_LOGIC.md` —— **本项目的魂**："先问对问题，不是先有结论再找数据"；提问红线；坦诚原则。
- `STRATEGY/06_QUESTION_BANK.md` —— **全部问题清单（Q1–Q7）与口径**；队名/team_id 字典；A1 口径；时间口径；人类复核规范；样本量纪律。
- `STRATEGY/05_HANDOFF.md` —— 跨层交接模板（本任务亦用它回传）。
- `STRATEGY/02_DATA_ANALYSIS.md` / `STRATEGY/01_MAP_REPLAY.md` / `STRATEGY/04_ORCHESTRATOR.md` —— 各层边界与产出规范。

**技术格式 / 交互规范（动手前必读）**
- `STRATEGY/DEM_FORMAT.md` —— **replay/combat log 的权威语义**（§C 系列：守卫事件语义/陷阱/订正；§D 系列：根本解设计/接线）。**本任务的 combat log 口径以此为准。**
- `STRATEGY/INTERACTIVE_MAP_PATTERN.md` —— **交互式地图通用规范**（Canvas 缩放/平移/点格钻取/前端坑）。**UI 必须遵循。**
- `STRATEGY/Q5B_WARD_VIEWER.md` —— 已交付的**单文件 viewer 范式**（数据链路、页面交互、构建/部署链路、已知坑）。**本任务的页面骨架与部署直接复用。**
- `STRATEGY/Q6_WARD_HEATMAP.md` —— 姊妹任务（假眼热力图，交互同 Q5B），可参考其"客户/复核"设计。
- `ARCHITECTURE.md` —— 项目架构（§6 通用三表 / §8 步骤）。
- `analysis/README.md` —— 分析层产物与口径。

**参考实现（旧版粗糙版，**口径不同**，仅参考其技能 CD 判定）**
- `dist/viewer_8955197224_lite.html` —— 早期版回放 viewer。**用途**：参考它**如何判定技能/道具 CD**（见 §6）。

---

## 1.5 解析现状：combat log 结构与数据库位置（③实测，非转述）

### 1.5.1 数据库位置与规模
- **本地解析库**：`dems/db_full/<league_id>/<match_id>.db`（**一场一个 sqlite**）。
  - 7 个联赛目录：`19101 / 19255 / 19422 / 19696 / 19719 / 19785 / 19917`
  - **共 970 个 .db**（= 970 场比赛）；单库约 **96 MB**。
  - 另有 `dems/db/`（早期/简版）、`dems/public/`（公开集）。
- **解析器**：`dota_parse/`（Rust + source2-demo）→ SQLite。**只新增提取器，不改 schema**。

### 1.5.2 每库 4 张表（实测样本 `19101/8825993964.db`）
| 表 | 内容 | 实测行数 |
|---|---|---|
| **`combat_log`** | **全类型全量 combat log（本任务核心）** | **104,691** |
| `entity_snapshots` | 实体逐秒采样（hero 位置 / hp 等） | 133,716 |
| `game_events` | 事件（purchase / ward / building / ability） | 175 |
| `player_identity` | 10 名玩家（steam_id / hero_name / team） | 10 |

### 1.5.3 `combat_log` 表结构（29 列 + `raw_json` 全保真）
```
match_id, event_seq, t_cle, t_tick, type_category, type,
attacker, target, damage_source, inflictor, value_name, value, health,
location_x, location_y, a_team, t_team, stack_count,
modifier_duration, modifier_elapsed, ability_level,
assist_players, gold_reason, xp_reason, event_location,
is_attacker_hero, is_target_hero, is_target_building, raw_json
```
- **两条时间轴**：`t_cle` = 游戏时钟（叙事轴，暂停冻结；**展示用这条**）｜`t_tick` = 回放钟（实体轴，不暂停）。
- **`type_category`**（本项目自定义归类，`parse.rs:1474`）：`damage` / `healing` / `ability` / `item` / `modifier` / `death` / `gold` / `xp` / `playerstats` / `gamestate` / `location` / `rune` / `revealed` / `scan` / `aegis` / `summoned` / `tree` / `killeater` / `other`。
- **`raw_json`** = 原始条目**全字段保真** → 固定列没覆盖的字段从这里取。
- **体量提示**：单场 **≈10.5 万条** → 单文件 HTML 若内嵌全量会过大，需**精简字段 + 压缩/分片/按需加载**。

### 1.5.4 对 Q7 的直接含义
- KDA / 正反补 / 经济经验 / modifier / damage → **全部来自 `combat_log`**（映射见 §5）。
- 胜率 → 派生模型（非 combat log）；技能 CD → cast 事件 + 冷却常量（见 §6）。

---

## 2. UI 结构

| 区域 | 内容 |
|---|---|
| **左（半屏宽）** | 地图（**沿用原底图**，复用现有底图 PNG 资产）；Canvas 缩放/平移（同 Q5B/Q6）。正方形、**边长 = min(可用宽, 可用高)**（一屏约束） |
| **左·地图右侧** | **双方 10 个英雄头像**（复用现有 hero icon 资产）：**两列竖排**（天辉一列 ｜ 夜魇一列，各有队名与分隔线）—— owner 方案①，头像不吃地图高度 |
| **地图上方（整页宽）** | **两条滑动块**：大尺度时间轴（全场，带重大事件图标）+ 小尺度时间轴（±60s）——见 §3；owner 迭代后大时间轴占**整页宽**，位于地图之**上** |
| **顶部（最上方）** | 当前 **双方经济差距 / 经验差距 / 胜率** + 全场火花线 |
| **右（半屏宽）** | **明细 / combat log**：默认 = 10 英雄 **KDA + 正反补**；点某英雄 → 改为该英雄 **±10s 的 combat log** + **技能 CD**（见 §4）。**栏内自己滚**，页面整体不滚 |

---

## 3. 双时间轴交互（核心 UX）

- **大尺度时间轴** = 整场比赛（0 → 结束，游戏时钟）。
- **小尺度时间轴** = 当前时刻 **±60s**（最多向前/向后滑 60 秒）。
- **提交逻辑**：小条**滑完（松手）** → **大条按"滑动的量"推进**，小条**归零**（回到中点/零点）。
- **联动**：拖动小条时，**大条按比例/逻辑小幅度同步移动**（实时反馈）。
- 待确认（owner）：小条是"松手提交"还是"实时提交"？归零瞬时还是带动画？
  → **owner 已选默认①：松手提交 + 瞬时归零**（已实现）。
- **大时间轴上的重大事件（owner 迭代 2/3）**：击杀 = 阵亡英雄头像，塔/兵营/基地/肉山 = 对应图标；
  **上下位置 = 对谁有利**（击杀看凶手方、建筑看被毁方所属的**对面**、肉山看击杀方），
  **图标环色 = 它属于哪一方**（绿天辉 / 红夜魇 / 灰 = 无主·肉山）；点图标跳到该时刻，±25s 内高亮。
- **一屏约束（owner 迭代 4）**：combat log 占右半屏，且"combat log + 地图 + 地图下的 10 头像 + 时间轴"
  必须同屏可见 → 整页 100vh 不滚（右栏自己滚），地图是唯一弹性元素（取 `min(可用宽,可用高)` 作边长），
  时间轴泳道层数按视口高 2/3/4 自适应。

---

## 4. 明细表

### 4.1 默认态（10 英雄）
| 字段 | 口径（来自 combat log） |
|---|---|
| KDA | `type_category='death'` 条目：击杀（attacker=该英雄）、死亡（target=该英雄）、助攻（`assist_players` 含该英雄） |
| 正补 / 反补 | creep 死亡条目：`type_category='death'`、target=小兵；attacker=该英雄且为**敌方**小兵=正补，attacker=该英雄且为**己方**小兵=反补 |

### 4.2 点某英雄后（±10s combat log）
以**当前播放时刻**为中心，展示该英雄 **±10 秒**内的 combat log 明细，**4 个可 toggle 的类别**：
| toggle | combat_log 来源 |
|---|---|
| **给出的 modifier** | `type_category='modifier'`（ModifierAdd/Remove/Stack）、`attacker`=该英雄 |
| **收到的 modifier** | `type_category='modifier'`、`target`=该英雄 |
| **造成伤害** | `type_category='damage'`（Damage/ManaDamage/CriticalDamage/SpellAbsorb/…）、`attacker`=该英雄；`value`=数值、`health`=前后血量 |
| **收到伤害** | `type_category='damage'`、`target`=该英雄 |

### 4.3 下方：技能 CD
显示该英雄的**技能冷却情况**（见 §6）。

---

## 5. 数据来源（铁律：严格来自 combat log）

**解析器已有通用 `combat_log` 表**（`dota_parse/src/parse.rs:1505+` 的 `CombatLogExtractor`；`model.rs:110+` 的 `CombatLogRow`）：
> **全类型、全量、不聚合、不去重**；字段：`event_seq` / `t_cle`(游戏时钟) / `t_tick`(回放钟) / `type_category` / `type_name` / `attacker` / `target` / `damage_source` / `inflictor` / `value_name` / `value` / `health` / `location_x,y` / `a_team` / `t_team` / `stack_count` / `modifier_duration` / `modifier_elapsed` / `ability_level` / **`assist_players`** / **`gold_reason`** / **`xp_reason`** / `is_attacker_hero`。

`type_category` 映射（`parse.rs:1474`）：`damage` / `healing` / `ability` / `item` / `modifier` / `death` / `gold` / `xp` / `playerstats` / `gamestate` / `location` / `rune` / `revealed` / `scan` / `aegis` / `summoned` / `tree` / `killeater`。

**三个"不是纯 combat log"的点（必须定，否则做不到"严格"）**
1. **胜率**：**不是** combat log，是**派生模型**——用 970 场（或大样本）的"经济/经验 → 胜率"关系算**当前状态胜率**。**必须状态化：绝不允许偷看最终结果**（否则数据泄漏）。口径待 owner 定。
2. **技能 CD**：cast 事件在 combat log，**冷却时长来自技能/道具常量表**（常量不在 combat log）。→ CD = combat log 事件 + 常量（见 §6）。
3. **经济差 / 经验差**：可由 combat-log 的 `gold`/`xp` 条目（含 `gold_reason`/`xp_reason`、`value`）**按玩家累加**重建（满足"严格来自 combat log"）。注意：这与 A1 的 `m_iNetWorth`（**非** combat log、对账 OpenDota 0.000%）是**两套源**——owner 要求严格 combat log → **主显用 combat-log 累加**，可旁注 m_iNetWorth 作校验。

---

## 6. 技能 CD 判定（参考旧版 + 关键道具）

**旧版做法**（`dist/viewer_8955197224_lite.html`，仅参考其 CD 判定思路）：
- 预计算 `cdmap[hero].ab` / `.items`，每条含 **`cds` = cooldown 区间数组 `[[start, end], ...]`**。
- 渲染三态：
  - **冷却中**：`t ∈ [start, end]` → 灰色 + 剩余秒 `ceil(end − t)`；
  - **未学/未拥有 (locked)**：`t < when`（`when` = 学会该技能 / 获得该道具的时刻）→ 灰锁；
  - **就绪**：其余 → 正常显示。
- **区间来源**：cast/use 事件时刻 + 该技能/道具的**冷却时长**（常量）→ `[cast, cast+cd]`。

**本任务要求**：
- 技能 CD：按上述**区间法**，由 combat log 的 `ability`/`item` 事件 + 冷却常量推出；三态展示（冷却中/未学未拥有/就绪），冷却中显示剩余秒。
- **关键道具追踪（owner 点名）**：**BKB（黑皇杖 Black King Bar）、刷新球（Refresher Orb）、TP（回城卷轴 Town Portal Scroll）**。
  - 追踪其**获得时刻**、**每次使用时刻**、**冷却区间**；BKB/刷新球按冷却常量算区间；TP 按使用/冷却/充能情况处理。
  - 道具获得：combat log `type_category='item'`（Purchase/Item/NeutralItemEarned 等）+ 背包/购买事件。

---

## 7. 资产复用

- 底图 PNG：`opendota_analysis/assets/dota_map_1024.png` 等（同 Q5B/Q6；**沿用原底图**）。
- 英雄头像：`opendota_analysis/assets/hero_icons/*.png`。
- 技能/道具图标：`opendota_analysis/assets/ability_icons/*.png`。
- 单文件内嵌（base64），同 Q5B/Q6。

---

## 8. 构建 / 部署（复用 Q5B 链路）

```
dems/db_full/<league>/<match_id>.db   ← combat_log 表（解析层产出）
      │  (新脚本, 例 analysis/q7_replay_build.py)
      ▼
output_q7/*.json                       ← 每场的 combat log 切片 / KDA / CS / 经济经验 / CD 区间
      │  (新脚本, 例 analysis/build_q7_html.py；复用 build_q5_html.py 骨架)
      ▼
output_review/q7_replay_<match>.html   ← 单文件 viewer
      │  copy → publish_repo/ → git push
      ▼
https://bigfatblackwhale.github.io/DSH-Dota2/q7_replay_<match>.html
```

- 先做**单场**跑通（建议用 owner 给过的 `8955197224` 或 Q5B 已核对过的 `8830423116` 做基准），再谈多场。
- 部署注意（同 Q5B）：git 推送时 `sh.exe ... Win32 error 5` 是无害告警，**以最后一行 `xxx..yyy main -> main` 为准**；本机无法自检公网，需人工刷新。

---

## 9. 已知难点 / 风险

1. **combat log 体量大**：单场全类型全量条目可能数十万条 → 单文件 HTML 内嵌需**分片/按需加载**（或只内嵌"该场 + 精简字段"），否则文件过大（Q5B 已 7.8 MB）。
2. **±10s 明细的实时性**：拖动时间轴时，右表要快速过滤 combat log（按时刻窗口 + 英雄 + 4 类 toggle）→ 前端需预处理索引。
3. **t_cle vs t_tick**：combat log 有两条时间轴（`t_cle` 游戏钟=叙事轴、`t_tick` 回放钟=实体轴）。**展示用 `t_cle`（游戏时钟）**；与地图/实体对齐时注意两者差异（暂停/量化）。
4. **经济/经验两套源**（§5.3）需 owner 拍板。
5. **胜率模型**（§5.1）需 owner 拍板口径，且严防泄漏。
6. **CD 常量表**：需要技能/道具冷却时长常量（游戏数据），本任务需要一份（或复用已有）。

---

## 10. 待确认（owner）

1. **胜率口径**：状态胜率模型用哪套数据、什么特征（经济差/经验差/时间）？确认"不看结果"。
2. **经济/经验差源**：combat-log 累加（严格） vs `m_iNetWorth`（更准但非 combat log）。
3. **技能 CD**：接受"combat log 事件 + 冷却常量表"；关键道具 BKB/刷新球/TP 的追踪粒度。
4. **双时间轴**：松手提交 vs 实时提交；归零瞬时 vs 动画。
5. **单场还是多场**：先做单场样板（建议）。
6. **±10s 窗口**：以当前播放时刻为中心？

---

## 11. 执行方式（③逻辑层判断）

**新开 agent**（不与 Q5B/Q6 同会话）：
- 这是**另一种产品**（回放浏览器），与统计热力图的**数据依赖（全量 combat_log）+ 复杂度**都不同；
- 任务量大，塞进已很重的 Q5B/Q6 context 会**污染**（违背本项目"上下文纯净"原则）。
- **但必须复用**：`INTERACTIVE_MAP_PATTERN.md`（交互规范）+ Q5B/Q6 的**单文件 HTML 骨架、Canvas 缩放平移、钻取、部署链路** + 现有底图/头像资产；并**参考** `dist/viewer_8955197224_lite.html` 的 CD 判定。

**开工顺序建议**：① 通读 §1 文档 → ② 单场跑通"combat_log 表 → 切片 JSON"（先 KDA/正反补/经济经验）→ ③ 搭 UI 骨架（地图+头像+双时间轴）→ ④ 明细表 + 4 toggle → ⑤ 技能 CD + 关键道具 → ⑥ 顶部胜率 → ⑦ 部署。

---

## 附：相关文档

- `STRATEGY/00_AGENT_MAP.md`、`STRATEGY/03_LOGIC.md`、`STRATEGY/06_QUESTION_BANK.md`、`STRATEGY/05_HANDOFF.md`
- `STRATEGY/DEM_FORMAT.md`、`STRATEGY/INTERACTIVE_MAP_PATTERN.md`
- `STRATEGY/Q5B_WARD_VIEWER.md`、`STRATEGY/Q6_WARD_HEATMAP.md`
- `ARCHITECTURE.md`、`analysis/README.md`
- 参考实现：`dist/viewer_8955197224_lite.html`（CD 判定）
