# Q7 · 全盘复现交互 UI（回放浏览器）—— 任务文档

> 状态：**第一步（MVP）+ 第二步（combat log 四 toggle（窗口经 owner 放大到 **±45s**、逐行带图标）/ 技能 CD / 状态胜率）均已交付并发布公网**。
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

把**一场比赛**做成"全盘复现"的交互 UI：**左侧地图 + 双时间轴 + 10 英雄头像；顶部经济/经验/胜率；右侧明细表**（默认 KDA + 正反补；点某英雄 → 显示该英雄 **±45s 的 combat log**（逐行带技能/对方英雄图标）与**技能 CD**）。
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
| **右（半屏宽）** | **明细 / combat log**：默认 = 10 英雄 **KDA + 正反补**；点某英雄 → 改为该英雄 **±45s 的 combat log**（列序：时刻 ｜ 本英雄 ｜ 技能/事件 ｜ 数值 ｜ 对象，带图标）+ **技能 CD**（见 §4）。**栏内自己滚**，页面整体不滚 |

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

## 3.5 本轮迭代：拖动时间气泡 + 头像框状态色（owner + 朋友反馈）

朋友反馈两条、owner 另加一条视觉修正，均已实现（回归测试 **308 条断言全绿**；
62 个已发布页面全跑一遍共 **14,067 条**）。

**① 拖进度条时看不到当前游戏时间** → 滑块上方现在会出现一个**蓝色时间气泡**（`#seekbub`）：
按当前时刻在整场大条上的位置定位，拖大条或小条都会出现并实时更新，松手后约 0.5 秒淡出。
（"当前时刻"面板与工具条上的文字本来就在跟着更新，但它们离滑块远、字号小 —— 实测反馈就是"看不到"。）

**② 大招与 TP 显示在哪（owner 三轮定案，最终形态）** —— **只在地图右侧那一列英雄头像上**，
地图里的英雄圆点保持干净：

| 位置 | 画什么 |
|---|---|
| **头像旁边一根竖长条** | **大招**：**天辉的在头像左侧、夜魇的在右侧**（朝各自队伍外侧）；**绿 = 就绪**、**灰 = 冷却中**、**深灰 = 这一刻没有数据** |
| **头像右上角** | **TP**：画的是**回城卷轴的道具图标**（24px，随载荷内嵌）：**亮着 = 可用**；**压暗 + 右下角琥珀色数字 = 冷却中（数字＝还剩几秒）** |
| **地图（小地图）上的英雄圆点** | **只有队伍色圆环**（绿天辉 / 红夜魇）＋选中金环、烟雾紫环、名字、轨迹 —— **不画大招也不画 TP** |

演化过程留档：朋友最初的反馈是"地图上英雄头像边框用不同颜色表示有没有 TP 或大招"，第一版因此把
**地图圆点**的内环做成状态色、右上角加了个 9px 小色点；owner 随后两次收窄：先要求 TP 换成
**图标 + 冷却秒数**，最后定案为**状态只放在中间那列头像上、地图保持干净**，大招也从内环改成
**旁边一根长条**（方向按队伍分）。所以"模式切换"那三个按钮也随之删掉了 —— 设计固定，不需要切换。

按钮旁有随模式变化的小图例；地图右侧那列头像用**完全相同的配色**，hover 有文字说明。
实现要点：内环的配色规则**必须挂在模式类下面**（`#avatars.f-ult .hero.ult-ready .ring`）——
否则 `.hero.tp-ok .ring` 会在特异性相同时盖掉大招色，表现成"大招模式里所有环都是 TP 蓝"
（第一版实测就是这个 bug，回归测试里加了一条 CSS 静态守卫盯着它）。

**③ 头像框放大 + 不再压扁** → `.hero` 48 → **56px**（矮屏 48 / 40），
并把地图上的头像改成"取 128×72 卡片的**中心正方形**再画进圆里" —— 此前是 `drawImage`
把整张横版卡片直接铺进正方形圆点，横向压扁 44%（脸是扁的）。头像条用的是 `object-fit:cover`，
本来就只裁不压。实测（1680×1100 窗口）：头像 56px、地图 314px、页面无溢出，
**头像放大并没有挤小地图**（地图尺寸由时间轴泳道与顶部数据行决定）。

---

## 3.6 复现游戏里的 **Fight Recap**（战斗回放）面板

owner 2026 提的："研究一下游戏里有没有 show fight recap 之类的内容，看看能不能复现这个"。
结论：**有，而且能复现**（已实现并发布）。

### 3.6.1 游戏里它是什么（证据来自游戏文件本身，不是转述）

| 来源 | 内容 |
|---|---|
| `resource/localization/dota_english.txt` / `dota_schinese.txt` | `fight_recap_show`=**显示战斗回放** ｜ `fight_recap_hide` ｜ `fight_recap_pause`=**显示战斗回放时暂停游戏** ｜ `UI_Fight_Recap_Terse`=**团战简要回顾**（还有个精简版）｜ 七个段名：`fight_recap_gold`=**金钱变化情况**、`fight_recap_xp`=**经验变化情况**、`fight_recap_dmg`=**造成伤害**、`fight_recap_heal`=**总治疗量**、`fight_recap_abilites_used`=**已使用的技能**、`fight_recap_items_used`=**已使用的物品**、`fight_recap_totals`=**总计** |
| `panorama/layout/hud/dota_hud_fightrecap.xml` | 面板结构：**每一段一行「天辉容器 ｜ 中间两队合计 ｜ 夜魇容器」**；数值段逐人一根条/箭头（`{i:dmg_value}`、`{i:radiant_damage_done}`…），技能段与物品段每侧 **4 行「图标 + x{usage_count}」**，死亡段是头像 + **买活图标**（`DeathBuybackIcon`） |
| 相邻但不同的两个功能 | `dota_settings_death_summary`=**死亡汇总**（死亡时刻的伤害来源）；`DOTA_CombatLog*`=**战斗日志**（攻击者/目标/技能/物品/窗口）——后者我们早就复现了，就是现在的 ±45s 明细 |

### 3.6.2 我们怎么复现（`analysis/q7_fights.py` + 页面第三个面板）

**团战由录像自动识别**（owner 定案）：12 秒内 ≥2 名英雄阵亡算一波；窗口 = 首次阵亡前 20s ~ 末次阵亡后 8s。
时间轴上多了一条**团战标记带**（每波一根可点的小条，数字＝该波阵亡人数），右栏多了**第三个面板**
（默认列表 ｜ 英雄明细 ｜ **战斗回顾**，三者互斥），面板里照游戏的七段布局渲染，
并支持"上一波/下一波"与"跟随当前时刻"。

**比游戏多一段**：录像里每条伤害都带来源技能（`inflictor`），所以完整版加第 ⑧ 段
**按技能拆分的伤害** —— 这正是打开这个面板通常想知道的事。

### 3.6.3 数据侧踩到的坑（都可复现，务必记牢）

1. **"属于谁"不在同一列**：`damage`/`healing`/`ability` 在 **attacker**；`gold`/`xp` **在 target**。
   弄错的表现是"金钱与经验两段全 0"（第一版就是这样，窗口里明明有 72 条 gold）。
2. **物品"使用"与"购买"是两种行**：使用 = `type='DotaCombatlogItem'`（人在 attacker、物品在 inflictor）；
   购买 = `DotaCombatlogPurchase`（人在 target）。不区分就会把"买装备"算成"用装备"。
3. **金钱 int32 下溢**：`gold_reason=1`（死亡扣钱）的负值按 uint32 落库，必须过
   `analysis/timebase.py::gold_i32()` 还原（全库 1.4% 的 gold 行受影响）。
4. **`n` 用窗口内阵亡数**（不是"簇内"人数）：窗口比簇宽，两者不等；统一成窗口内人数，
   才能让"头部数字 == 面板阵亡段 == 时间轴标记"三者自洽。
5. **本地录像的库多一层目录**（`db_full/local/<scope>/`），按 match_id 找库要**递归** glob，
   否则私人场次会算出 0 波。

### 3.6.4 做不到的一项（如实标注）

**买活**：库里 `type='DotaCombatlogBuyback'` 的行没有英雄字段（attacker/target 都空、
`value_name` 是 `item_ward_dispenser` 之类、值只有个位数），是解析噪声；
`gold_reason` 的实测码表（`analysis/DATA_DICT.md`）里也没有买活项。
所以面板**不画买活图标**，而不是硬凑一个。

### 3.6.5 体积与工程取舍

- 团战载荷**建站时压缩**：英雄用**下标**、技能/物品名建**键表** → 实测 36 波 47.7 KB（完整）/ 35.4 KB（lite），
  约 1 KB/波。原始写法（每次重复 `npc_dota_hero_xxx` 与技能名）是 143 KB。
- **老切片不必重跑**：切片里没有 `fights` 就从主库**现算**（与切片期同一份 `q7_fights.py`），
  实测 3 秒/场 —— 否则要重跑 60 场切片（一两个小时）。
- **团战图标单独一套（18px）**：一场团战会用到 120~150 个不同的技能/物品名，
  若复用明细那套 28px（2.7 KB/个）会让每个 lite 页面 +450 KB；18px/48 色约 0.62 KB，
  总体 +80 KB 左右（lite 页面 0.64 → 0.80 MB）。

---

## 4. 明细表

### 4.1 默认态（10 英雄）
| 字段 | 口径（来自 combat log） |
|---|---|
| KDA | `type_category='death'` 条目：击杀（attacker=该英雄）、死亡（target=该英雄）、助攻（`assist_players` 含该英雄） |
| 正补 / 反补 | creep 死亡条目：`type_category='death'`、target=小兵；attacker=该英雄且为**敌方**小兵=正补，attacker=该英雄且为**己方**小兵=反补 |

### 4.2 点某英雄后（±45s combat log，带图标）
以**当前播放时刻**为中心，展示该英雄 **±45 秒**内的 combat log 明细（owner 2026 从 ±10s 放大），**4 个可 toggle 的类别**：
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

### 6.1 大招识别与 TP 状态（实现口径）

**大招怎么认出来的**：录像的 `ability_cd_*` 事件里**没有槽位信息**，技能键名也不带"这是大招"的标记，
所以改用一份构建期资产 `opendota_analysis/assets/hero_ultimates.json`（由 `analysis/hero_ultimates.py`
从 Valve 游戏文件抽取、带磁盘缓存，离线可复现）：英雄文件里每个技能的定义自带
`"AbilityType" "ABILITY_TYPE_ULTIMATE"` 标记，据此判定，并要求该技能出现在该英雄的 `Ability1..N`
槽位表里（**跳过 `AbilityDraftAbilities` 那一组** —— 技能征召的槽位与真实槽位不同：烬火精灵那一组的
Ability4 是 `activate_fire_remnant`，而真大招 `fire_remnant` 在第 6 位）。
比对时两侧都去掉 `CDOTA_Ability_` 前缀、再去掉下划线小写，所以录像里的 `Windrunner_FocusFire`
与游戏文件的 `windrunner_focusfire` 能对上。

**两处改名**（游戏文件与录像键名不一致，但是同一个技能）：`mirana_invis` ↔ `Mirana_MoonlightShadow`、
`monkey_king_wukongs_command` ↔ `MonkeyKing_FurArmy`，在 `hero_ultimates.REPLAY_ALIASES` 里显式列出。

**交叉验证**：拿 **700 场已解析库**核对覆盖率 —— 126 个出场英雄里 **123 个完全命中**，2 个就是上面那两处改名，
1 个（虚空假面）是数据稀疏；另外用"大招通常是冷却最长的技能"做一次独立排序验证，**84% 落在前二**，
偏离的例子恰好都能解释（卡尔的大招 `Invoke` 冷却只有几秒；拉比克榜首是他**偷来的**大招）。

**TP 状态只能推算**：录像里**只有使用时刻**（`combat_log` 的 `item_tpscroll`），没有冷却/充能事件
（解析器 `ITEM_TRACK` 只跟踪 BKB 与刷新球；给它加个名字就能产出 TP 的真实冷却，但**联赛页读的是 Q5 老库**，
只有新解析的私人录像才有，而联赛页与私人页必须用同一套口径）。
因此头像框与 CD 面板上的 TP"可用/冷却中"是**按固定共享冷却从使用时刻推算**的：秒数读自游戏文件
`items.txt` 里 `item_tpscroll` 的 `AbilityCooldown`（当前 **80 秒**），随载荷传给页面（`tpcool`），
图例里显示这个数字。注意它同时是 TP 卷轴与飞鞋共享的 `teleport` 共享冷却，而且"身上到底有没有卷轴"
录像里查不到 —— 所以页面上写明这是**推算值、只作参考**，不是录像里的原始数值。

---

## 7. 资产复用

- 底图 PNG：`opendota_analysis/assets/dota_map_1024.png` 等（同 Q5B/Q6；**沿用原底图**）。
- 英雄头像：`opendota_analysis/assets/hero_icons/*.png`（**128×72 的横版卡片**，画进圆点前必须先取中心正方形）。
- 技能/道具图标：`opendota_analysis/assets/ability_icons/*.png`。
- 大招对照表：`opendota_analysis/assets/hero_ultimates.json`（由 `analysis/hero_ultimates.py` 生成，见 §6.1）。
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
2. **明细的实时性**：拖动时间轴时，右表要快速过滤 combat log（按时刻窗口 + 英雄 + 4 类 toggle）→ 前端预处理索引；±45s 窗口在团战期可达 ~1.8k 行 → 页面用**虚拟滚动**（只画视口附近 ~160 行）+ 播放时 130ms 节流。
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
6. ~~±10s 窗口~~ → **owner 2026 定：±45s**（已实现）。

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
