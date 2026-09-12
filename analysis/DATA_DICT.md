# data dictionary — ② 数据分析/收集层

> 本层新增分析维度对应的事件/字段口径。通用三表结构（`entity_snapshots` /
> `game_events` / `player_identity`）不变，新增维度=新增 `event_type`（开放字符串）。
> 所有数据均来自本地 `.dem`（`dota_parse` 解析），不依赖 OpenDota。

---

## 【2026 订正 · 必读】两套库并存 + combat_log 口径（Q7 会话实测）

### A. 两套库的**内容不同**，用之前先确认读的是哪套

| | `dems/db/<league>/<match>.db` | `dems/db_full/<league>/<match>.db` |
|---|---|---|
| 场次 | 971 | 970 |
| 表 | `entity_snapshots` / `game_events` / `player_identity` | + **`combat_log`** |
| 定位 | **Q5 版散装 extractor** | **combat_log 重写版**（见 `STRATEGY/COMBAT_LOG_REWRITE.md`） |
| `game_events` 事件类型 | 17 种（见右） | 只剩 **3 种**（`ward_placed` / `building_spawn` / `building_destroyed`）—— 即"实体空间层" |
| 叙事事件 | 散装 `event_type`：`gold`(3.54M 行) / `ability_cd_start`+`ability_cd_end`(各 3.15M) / `neutral_kill`(1.23M) / `purchase`(569k) / `ability_known`+`ability_learn`(各 ~12万/10万) / `item_cd_start`+`item_cd_end`(各 ~78k) / `ward_use`(58k) / `smoke_count` / `item_known` / `game_state` | 全进 **`combat_log`**（全类型全量，含 `type_category` 枚举） |
| 谁在用 | **Q1**（`q1_value_zones.py` 的 glob 是 `dems/db/*/*.db`）；`analysis/run_analysis.py`、`ward_analysis.py` | **Q5B / Q6 / Q7** |

> ⚠️ **跨任务比较时要小心**：Q1 的"逐格金币/GPM"来自 `dems/db/gold`，
> 而 Q5B/Q6/Q7 的战斗叙事来自 `dems/db_full/combat_log`。两者是**同一批 .dem 的两次不同解析**，
> 时间语义也不同（`game_events.game_time_sec` 混钟，见 `DEM_FORMAT.md` §2/§5）。
>
> ⚠️ **`dems/db_full` 里没有技能/道具 CD 数据**：COMBAT_LOG_REWRITE §5.5 把散装 extractor 删了，
> 而 CD 区间是**实体派生**（不是 combat 条目），所以没进 `combat_log`。
> **Q7 第二步要的"技能 CD"不需要外部常量表** —— `dems/db/` 里本来就有：
> `ability_cd_start` / `ability_cd_end` / `ability_known` / `ability_learn`（`properties.remaining` = **实际剩余冷却秒**，
> 来自实体 `m_fCooldown`，含等级/天赋/减CD），道具同理 `item_cd_start` / `item_cd_end` / `item_known`
> （键形如 `ITEM:Black_King_Bar`）。做法：**按 match_id 两个库 join**（或把该 extractor 补回新 parser）。

### B. `combat_log.gold.value` 的 **int32 下溢**（必须还原）

`CMsgDotaCombatLogEntry.value` 在 proto 里是 `uint32`，而"死亡扣钱"是负数 → Valve 按 uint32 发出。
实测（40 场抽样 144,018 条 gold 行）：

- 溢出（`value ≥ 2^31`）**只出现在 `gold_reason=1`**：2153/2153 行，还原后均值 **−232**（= 死亡扣钱，
  行数 == 该场英雄死亡数）；
- **其余类别（`healing` / `xp` / `damage` / `modifier` / `item`）零行溢出** → 不需要处理；
- 不还原的后果：单人金币合计被撑到 4.29e9 量级（纯垃圾）。

统一还原函数：`analysis/timebase.py::gold_i32(v)`（`v ≥ 2^31 ⇒ v −= 2^32`）。

### C. `gold_reason` 的**实测**观察（不写猜的枚举名）

`DOTA_COMBATLOG_GOLD_*` 的官方枚举名**不在本仓库的 proto 里**（`.tmp/redota_src/dota/*.proto` 只有
`DOTA_COMBATLOG_GOLD = 8` 这个 type）。下面只写**实测到的量级/频率**（40 场抽样），命名待权威来源补：

| reason | n | 均值 | 观察 |
|---|---|---|---|
| 0 | 983 | +269.8 | 起始/杂项（含出门初始金 600） |
| **1** | 2153 | **−232.0** | **死亡扣钱**（负数，需 `gold_i32`；行数 == 英雄死亡数） |
| 6 | 1312 | +272.7 | 未定名（大额） |
| 11 | 3537 | +129.4 | 未定名 |
| 12 | 9619 | +147.1 | 未定名（大额高频） |
| 13 | 68930 | +42.7 | **线上兵补刀**（小额高频，均值≈一个兵的赏金） |
| 14 | 46806 | +30.9 | **中立兵**（小额高频） |
| 15 / 16 / 17 / 19 / 20 / 21 / 22 | 820 / 641 / 4565 / 2970 / 540 / 134 / 996 | +165.7 / +72.9 / +70.0 / +23.7 / +208.0 / +69.4 / +33.1 | 未定名 |
| 5 | 12 | +106.3 | 未定名（极罕见） |

**`combat-log 累加` ≠ 净值**：13/14 只覆盖"补刀/野怪"这类被动收入的一部分，
且买装备/消耗品**不减**（死亡扣钱已由 reason=1 扣）。所以累加值是"**累计获取金币**"，
与 `m_iNetWorth`（净值 = 现金 + 装备）语义不同、可差 10%+。
→ **owner 2026 定案：Q7 顶部"经济差"主显用 `m_iNetWorth` 净值差**，combat-log 累加并列显示。

### D. `combat_log.assist_players` 的口径

值形如 `'[8,7,6,5]'`，是 **头部玩家索引 0..9**（**不是** `player_identity.player_slot`），
且**包含击杀者本人**。→ 计助攻必须 `set(assist_players) − {killer_index}`
（不剔除会让每个击杀者凭空 +1 助攻，8830423116 的 40 条击杀几乎全部命中）。
实现：`analysis/q7_replay.py`。

### E. `combat_log.raw_json` **不是**"全字段保真"

实测只有 11 个键：`a_team / attacker / damage_source / health / inflictor / location / t_team /
target / type / value / value_name`。`assist_players / networth / last_hits / obs_wards_placed` 等
**不在里面**（但都已在固定列）。`STRATEGY/DEM_FORMAT.md` §4.3 的"全字段保真"表述**需订正**。

### F. 时基（共享实现，别再各写一份）

`analysis/timebase.py`：`Clock(con, mid)` → `.horn_cle` / `.end_cle` / `.cl(tt)` / `.disp(tt)` / `.disp_cle(cle)`；
号角 = `gamestate value==5` 的 `t_cle`；结束 = **远古被摧毁**（不是 `MAX(t_cle)`）；
`t_tick→t_cle` 折算按"暂停时实体静止"的物理证据摊分，并给出 `pause_sec_total` / `pause_blocks` 供复核。
自检：`python analysis/timebase.py <match_id>`。

**暂停是常态（全量 970 场实测，`analysis/output_q5/clock_ab/pause_scan.csv`）**：

| 指标 | 值 |
|---|---|
| 有暂停（> 1s）的场次 | **624 / 970（64.3%）** |
| > 30s / > 60s / > 120s / > 300s | 480（49.5%）/ 337（34.7%）/ 170（17.5%）/ 43（4.4%） |
| 暂停总量 | 合计 **66,929s**（≈18.6 小时）；中位 29.2s；最大 **1321.4s**（22 分钟） |
| `Δ_file`（首锚点 `cle − tt`） | min 6.6 / 中位 11.5 / **max 540.3** |

⇒ ① 任何"用一个常数偏移"把回放钟折算到游戏钟的做法，在 2/3 的场次上都有系统误差；
② 最强的一场 `Δ_file = 540.3`（8830423116）说明这个"常数"本身就能差到 9 分钟。

---

## 新增：`game_events` 事件类型（Q1 jungle / economy）

> ⚠️ 本节描述的事件类型**只存在于 `dems/db/`**（Q5 版散装 extractor）；
> `dems/db_full/`（combat_log 版）里没有它们 —— 对应内容在 `combat_log` 的
> `type_category='gold'` / `'xp'` / `'item'` / `'ability'` 等分类里。见上方 A 节。

## 新增：`game_events` 事件类型（Q1 jungle / economy）

### `neutral_kill` — 中立生物击杀（野怪/肉山死亡）
来源：`dota_parse` 新增 `JungleExtractor`，逐秒跟踪
`CDOTA_BaseNPC_Creep_Neutral` / `CDOTA_Unit_Roshan` 位置与 HP，检测到死亡（HP≤0
或存活状态下从实体列表消失）即记录。

| 列 | 含义 |
|---|---|
| `game_time_sec` | 击杀时刻（demo 相对秒，注意「中途起录」偏移，见下） |
| `event_type` | 固定 `'neutral_kill'` |
| `target_id` | `'neutral'`（野怪）或 `'roshan'`（肉山） |
| `x`,`y` | 击杀位置（世界坐标，供「野点活动热区」） |
| `properties.kind` | 同 `target_id`，冗余便于 JSON 查询 |

口径说明：
- **只算中立生物**：`CDOTA_BaseNPC_Creep_Neutral`（非线野 `_Lane`/`_Siege`）与 Roshan。
- **归属队伍**（清理哪个野区）：中立单位无队伍，按地图象限近似——
  `x<0 ∧ y<0`→radiant 野区，`x>0 ∧ y>0`→dire 野区，其余记为 `mid`（不归边）。
  这是 proxy 级近似；精确到「被哪个英雄清空」需与战斗日志 Death 配对，属下一步。
- **去掉 `_Lane`/`_Siege`**：避免把线野/投石车当野怪。

### `gold` — 逐玩家金币收入事件
来源：战斗日志 `DotaCombatlogGold`。每个事件=一个英雄获得一笔金币。

| 列 | 含义 |
|---|---|
| `game_time_sec` | 获得金币时刻 |
| `event_type` | 固定 `'gold'` |
| `actor_id` | 获得金币的英雄 `npc_dota_hero_*` |
| `x`,`y` | 金币发生的世界坐标（英雄位置，可供「在哪赚金」分析） |
| `properties.value` | 金币数额 |
| `properties.reason` | Dota 金币来源码（0=初装/未知；11/12/13/14 等=击杀/线野/被动等） |

口径说明：
- `actor_id` 必须在 `player_identity` 中（有 `player_slot` 的真英雄）。
- 聚合时**剔除异常值**：`value<0` 或 `>100000` 视为溢出/哨兵值丢弃。
- 团队金币=按 `player_identity.team_id`（2=radiant,3=dire）对 `actor_id` 求和。

## A1 逐玩家经济口径（净值 = 现金 + 有效装备）【重要，长期维护】

> 共同依赖 A1（Q1/Q3/Q4 都用它）的**精确方案已定案**：直接从 `.dem` 读游戏内**真实
> 逐玩家逐分钟净值**，不是反推。

### 数据源（精确，已对账 0.000%）
- 实体：`CDOTA_DataRadiant`（天辉）/ `CDOTA_DataDire`（夜魇）。每玩家每 tick 有：
  - `<index>.m_iNetWorth`（**净值**，精确）
  - `<index>.m_iReliableGold` + `<index>.m_iUnreliableGold`（**现金**=可靠+不可靠金）
  - 及完整金币流：`m_iIncomeGold`、`m_iGoldSpentOnItems/Consumables/Buybacks`、
    `m_iHeroKillGold`、`m_iCreepKillGold`、`m_iNeutralKillGold`、`m_iTotalEarnedGold`…
- 团队净值逐分钟：`CDOTASpectatorGraphManagerProxy.m_rgRadiantNetWorth.*` / `m_rgDireNetWorth.*`
  （= OpenDota `radiant_gold_adv` 的源头）。
- **旧尝试（记录备查）**：`CDOTA_PlayerResource.m_iNetWorth.*` 字段名在但值 source2-demo
  解不出；`m_vecDataTeam.*` 同理。**真正可读的是 `CDOTA_DataRadiant/Dire.NNNN.m_iNetWorth`**。

### 已实现
`dota_parse` 新增 `NetWorthExtractor`：逐整秒读 `CDOTA_DataRadiant/Dire.<idx>.m_iNetWorth`
+ 可靠/不可靠金，写入 `entity_snapshots`（`entity_type='networth'`，
`entity_id='nw:radiant:0'`/`'nw:dire:0'`…，`hp`=净值，`extra`={networth,reliable,unreliable}）。
- **新库** `player_econ_t`（stats.db，OpenDota 口径）作为反推版**已弃用**；精确版走
  `entity_snapshots(etype='networth')`。
- **对账**：2026-06 一场 10/10 玩家逐玩家最终净值 vs OpenDota `net_worth` = **0.000%**。

### 口径
- 逐玩家逐分钟净值、现金(可靠+不可靠)、金币流分解都来自 `.dem` 真实值 → **精确**。
- 消费掉的装备不用再反推扣除——因为是游戏内真实净值，天然把消耗/卖装/买活都算对了。

> ⚠️ 使用时 note：map `entity_id='nw:<team>:<idx>'` ↔ `player_identity`：天辉 idx=0..4 →
> slot 0..4；夜魇 idx=0..4 → slot 128..132。

## 选手官方 ID 映射（steam_id → 官方选手名）
- `.dem` 的 `player_identity.steam_id` 是 **64 位游戏 steam id**（技术 ID）。
- 要显示**官方选手名**（如 Team Spirit 的 `Yatoro`），用 **`.tmp/pro_players.json`**（OpenDota 选手字典）：
  ```
  steam_id(str) -> p["name"]   # 官方选手名; 也含 team_name/team_id
  ```
  映射方法：读取 `.tmp/pro_players.json`，建 `{str(p["steamid"]): p["name"]}`；对每名选手，
  `player_name = pro_name.get(str(steam_id), 录像里的 player_name)`（找不到才 fallback 录像名）。
- 用于"队伍名单/官方ID"展示（Q2 队伍 roster 下拉）。脚本：`analysis/build_all_teams_q2.py`。


## 时间口径（重要）
- `game_time_sec` 是 **demo 相对**秒。部分 CDN 公共录像（如 19101 联赛多场）**中途起录**，
  时间轴带偏移（实测样例金币从 ~12 分钟才出现）。故：
  - 分窗口统计（0-10 / 10-20 / 20+ / 整场）在「中途起录」场次下**前段窗口会偏空**。
  - 跨场 pooled 聚合前应先检测每个 match 的起始偏移（首个事件秒数），
    或只纳入「0 起录」场次（沿用 `run_analysis.py` 的 `FIRST_SAMPLE_ALIGNED_S<300` 判据）。

## Q1（新）地图经济价值分区【③逻辑层重定义，描述性版】
> 目标：输出【全图经济价值分区】（金角/银边/草肚皮）——英雄"处于某区域"时的经济效果。

### 单元
- 地图 **250 网格**（cell=250 世界单位，`MAP_HALF=10000` → **80×80** 格）+ 语义标签。

### 每格 3 指标（全部 game-start 对齐，仅统计 t>=首个净值>0 秒）
| 指标 | 定义 | 公式（per 格） |
|---|---|---|
| ① GPM | 英雄在该格的期望每分金币获取 | `(该格累计金币 / 该格英雄秒) × 60` |
| ② 绝对队经济 | 天辉有人在该格 → 天辉总经济增量/分钟 | `(天辉占用秒里天辉逐秒金币和 / 占用秒) × 60` |
| ③ 相对队经济 | 天辉有人在该格 → Δ(天辉−夜魇)金/分钟 | `(天辉占用秒里 (天辉−夜魇)秒金币和 / 占用秒) × 60` |

- **逐玩家归属**：用 A1 的英雄逐秒位置（`entity_type='hero'` 的 `x,y`）判定"谁在该格"；
  单笔 `gold` 事件归属到该英雄此刻所在格。
- **队绝对/相对**：用逐秒金币（`game_events.gold` 按 team 汇总）+ 视角翻转（夜魇系同理）。

### 输入
`entity_snapshots`（`entity_type='hero'` 位置 + `networth` 定 game_start）+ `game_events.gold`
+ `player_identity`（hero→team_id）。

### 输出（analysis/output_q1）
- `q1_zone_agg.csv`：逐格聚合（cell_x,cell_y,x_center,y_center,zone,n_hero_sec,gpm_per_min,
  r_occ_sec,r_abs_per_min,r_rel_per_min, d_occ_sec,d_abs_per_min,d_rel_per_min,n_match）。
- `q1_zone_detail.db`：逐格×match 明细（sqlite，供 drill-down，逐场增量写，表 `detail`）。
- 复核页：`analysis/output_review/q1_value_zones_viewer.html`（3 指标热区 + 分区标注 +
  **颜色滑块**（拖拽调色阶）+ 逐格明细表）。

### 口径 / 限制
- **描述性版**：不做"加条件"版；**反候果**（资源优势才去对面野区）由看图者自行领会，不作控制。
- **每格 n 小**（970 场、英雄秒摊到 6400 格），只作描述性；pub 大样本（战略扩充）后再上统计精度。
- **语义标签为近似**：`zone_label(x,y)` 常量可调（中路斜带 |x−y|<1400；上路 y>2600∧x<y；
  下路 y<−2600∧x>y；肉山坑 (−1600,2300) 半径 1000；三角区两角；其余野区）。可再校准。
- 脚本：`analysis/q1_value_zones.py`（计算）、`analysis/build_q1_zones_html.py`（复核页）。

## Q5 眼位：不易被反的假眼 / 容易反到假眼的真眼【③逻辑/owner 定义】

> ⚠️ **2026 重写**:原本"`placed↔destroyed` 按 (team,ward_type) FIFO/LIFO 配对"的旧口径**已废弃**。
> 原因是它没有每支眼的身份/坐标,在含 pause 场次必然错配。现改为 **per-ward 实体跟踪**(见下)。

### 定义
- **假眼 = Observer Ward**（敌方可见可反）→ 看**生存时长**。
- **真眼 = Sentry Ward**（真视，让范围内敌方假眼现形、被持有方反掉；自身对敌隐身+免疫攻击→只能自然到期，
  **存活窗口=[放置,销毁]**）。

### 数据(per-ward 实体模型)
- 每支眼 = 一个实体,`entity_snapshots` 里 `entity_type='ward'`、`entity_id='ward:<entity_index>'`,
  **逐秒真实坐标**;`extra{ward_type, entity_index}`。
- `ward_placed`：`x,y` + `properties{team, ward_type, entity_index, entity_id}`；`actor_id=None`(未记录插眼者)。
- `ward_destroyed`：**带坐标**(取自该眼实体消失处)+ `properties{reason, team, ward_type, entity_index, entity_id, t_tick}`；
  `actor_id` = 反方英雄/眼单位自己。
- **时间口径(2026 最终,给③逻辑/总控)**：
  - **combat-log 时间(`cle.timestamp()`)是权威游戏时钟**(精确到小数秒;随 pause 冻结;从 0:00 号角起)。
  - **tick 时钟** `game_time_sec=tick/TICK_RATE` 与 combat-log 时间**同源、差<1s**(实测),但**受 pause 影响**:pause 期间 raw(tick)照走、游戏时钟冻结。
  - 因此:**游戏时钟 = raw − 0:00raw − (0:00之后、该事件之前的累计 pause)**;其中 **0:00raw = min(building_spawn)+90 + 号角前 pause**。
  - 用固定观测可反推 0:00raw(如 8817145578→1062, 8885871759→842, 8842596730→982);pause 边界精度有 **~2s** 误差。
  - **后续方向(已定,待重解析)**:改用 combat-log 时间作为一切时间源,不再用 tick+pause 校正,一劳永逸(消除 ~2s 边界误差)。
- **销毁判定**：
  - **自然到期(expired)** = 放置 + 标准寿命(**真眼 420s / 假眼 360s**)。
  - **被反(dewarded)** = 该眼提前消失(entity last_t < 放置+寿命)且存在同队同型 combat-log `dewarded`。
    **被反时刻 = combat-log 死亡 `t_tick`(权威,比 entity last_t 准,后者晚几秒)**。
- 真眼反眼成功：某真眼存活窗口 [place, destroy] 内，**敌方假眼被持真眼方反掉**(reason=dewarded) 且
  位置落在**真视半径(1050 单位)**内 → 计为该真眼成功 1 次。（敌方队伍 = `2 if team==3 else 3`。）
- **反眼归属(不依赖 actor)**:按"被反假眼位置在真眼真视半径内 + 存活窗"归属,天辉真眼反夜魇假眼(敌方)。
  `actor_id`(反方英雄)仅用于展示,不参与归属判据(实测 actor 可能被解析到非实际反方英雄)。
- **反眼率(核心指标)**:每支真眼**平均反掉的敌方假眼数**(avg_jy = Σsuccess/n),只算反假眼,不含反真眼、不含真眼被反。
- **⚠️ 双插注意项(不特殊处理,记录)**:combat-log 里真假眼重叠时显示 `uses Observer Ward and Sentry Ward`,
  具体放了哪支需判断、两支都要纳入考虑;当前不做特殊逻辑,出现时按"两支都评估"处理。

### 每格指标（分辨率 cs ∈ {1,4,16,172} 单位；邻域 ρ ∈ {100,300,600}）
| 指标 | 定义 |
|---|---|
| 假眼·绝对生存 | 该格平均 `destroy−place`（被反+过期；右删失剔除，单列 `obs_ced`） |
| 假眼·相对生存 | 该格绝对 ÷ 附近 ρ 平均生存 ×100%（>100% = 比周围更难反 = 刁钻） |
| 真眼·绝对反眼 | 该格每支真眼**平均反掉的敌方假眼数**（avg_jy = Σsuccess/n；**只算反假眼**，不含反真眼；"反眼率"核心指标） |
| 真眼·相对反眼 | 该格真眼反眼 ÷ 附近 ρ 平均 ×100%（>100% = 比周围更好反） |

> **Q5B 反眼率口径(2026 确认)**:反眼率 = 每支真眼平均反掉的**敌方假眼**数(avg_jy)。不含反真眼、不含真眼被反。
> 另配"其它平均"(平均反真眼 avg_zy / 平均总反眼 avg_all)与"总反眼数"(jy_total/zy_total/all_total)供参考。
> Q5B 数据按 **队伍(天辉/夜魇)+ 时间窗(-1:30-7 / 7-15 / 15+)** 分开聚合。

### 分辨率敏感性
- 细胞 1 / 4 / 16 / 172 单位(172 → 2×8600/172 = 100 → 100×100 格,Q5B 热力用)。
  **用实际坐标范围 `MAP_HALF=8600`**(实测眼位坐标 ±8469)。
- 判读：细档=点状/局部刁钻；粗档仍保留=区域级刁钻。

### 产出（analysis/output_q5 + output_review）
- `q5_cell_<cs>.json`：每格绝对+相对(100/300/600)+match 列表。
- `q5_cell_matches_<cs>.csv`：逐格×match 明细。
- `q5_sentry_detail_<cs>.csv`：逐支真眼明细(match、放置/销毁/存活 mm:ss、成功、被反?、反眼时刻、
  **被反假眼/真眼位置列表 `dew_obs_pos`/`dew_sen_pos`**，每项 `[x,y,游戏时钟,被反眼阵营,反眼队伍]`）。
- `q5_matches.json`：每场 `{radiant, dire}` 官方队名(steam_id→pro_players.team_name 聚合，970 场全覆盖)。
- 复核页 `analysis/output_review/q5b_ward_viewer.html`(canvas 自包含)。交互:
  两套指标(反眼率·平均反假眼默认 / 其它平均 / 总反眼数) + **手动缩放**(滚轮缩放+左键拖拽,自由对比) +
  **点格子自动放大进入**(只显示该格+标注真假眼位,再点同格缩放回去) + **点击被反眼图标**(锁定显示比赛ID/事件坐标/被摧毁时间/双方队名/反眼队伍) +
  悬停高亮 + 官方图标标注 + 恢复正常大小按钮。
- 脚本：`analysis/q5_ward.py`(per-ward 实体读取 + 被反判定 + 聚合 + 队名映射,`--sample N` 可验)、
  `analysis/build_q5_html.py`(复核页)。

### 限制
- 每格 n 小（尤其细档），相对值在 n=1 时可极端 → 描述性；4 档分辨率用于判别点状/区域级。
- 真眼反眼受"敌方眼位习惯+时机"影响（说明项），未作控制。
- **插眼者**：`ward_placed.actor_id=None`,插眼英雄只能靠买眼事件或最近己方位置近似。
- **插眼者**：`ward_placed.actor_id=None`,插眼英雄只能靠买眼事件或最近己方位置近似。
- 接近标准寿命才被反的眼,时间上难区分"被反 vs 到期",保守归为到期。
