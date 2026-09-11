# DEM_FORMAT.md — Source 2 / Dota 2 .dem 底层解读（权威知识库）

> 本文档由 **⑨ 底层机制分析 Agent（DEM_AGENT，一次性研究型）** 产出，是数据层/解析层长期依赖的**唯一权威依据**。
> 结论全部来自三条交叉验证的途径：**① 读 source2-demo 库与 Valve 官方 protobuf 源码；② 读 OpenDota/redota 官方回放解析器源码（`.tmp/redota_src/`，与 Valve 客户端同源）；③ 对真实 .dem（尤其已知病根场 **8817145578**，暂停 72s）做二进制探针实证**。
> 三路证据完全吻合。数据层后续所有解析、眼位重写、Q1/Q2/Q3、pause 处理，均以本文为准。

---

## 0. 结论速览（TL;DR）

- **`.dem` 里根本没有"绝对墙钟（真实挂钟时间）"**。比赛的真实时间（`start_time`/`duration`）只来自 OpenDota API，不在 `.dem` 内部。
- `.dem` 内部只有**两个真正的时钟**，都由同一帧 30Hz 的**服务端全局 tick** 派生：
  1. **回放时钟（raw / demotick）**：`ctx.tick()`，30 tick/s，**永不暂停**（暂停、选人、加载、赛后空转全都照走）。换算为秒：`tick / 30`。
  2. **游戏时钟（combat-log game clock）**：`cle.timestamp()`，**暂停时冻结**。它是"显示时钟减去号角基值"之前的那个量（redota 称 `game.time`）。换算为秒：`cle.timestamp()`。
- 还有一个**原始时间戳** `cle.timestamp_raw()`：是回放时钟的等价量（`≈ tick/30 + 每场常量偏移`），**不随暂停冻结**。
- **"游戏时钟 0:00（号角）"的基值** = `DOTA_GAMERULES_STATE_GAME_IN_PROGRESS`（combat-log `value==5`）那一刻的 `cle.timestamp()`。**显示的分钟:秒 = `cle.timestamp() − 号角基值`**。
- **gameclock 与录像时间对不上的根因**：解析器把 `tick/30`（回放时钟）当 `game_time_sec` 存，但**不同事件混用了不同时钟**（见下）。二者之差 = **积累暂停时长 + 开始前（选人/加载）时长**。8817145578 暂停 72s，且号角在 raw≈1062s 处——这就是对不上的全部来源。
- **致命伤（解析器当前状态）**：`game_events.game_time_sec` 在**同一张表里混用两种时钟**——`purchase`/`gold`/`ward_use`/combat版`ward_destroyed` 用**游戏时钟**，`ward_placed`/`building`/`能力`/`中立击杀`/`hero·ward·nw 快照` 用**回放时钟**。两钟在 8817145578 相差约 973s（~900s 开始前 + ~73s 暂停），任何跨类型时间对齐/配对（眼位 placed↔destroyed、购买↔位置）都被严重污染。

---

## 1. .dem 容器格式（文件结构）

`.dem` 是 Source 2 引擎的**二进制演示流**，由若干 `(command, tick, payload)` 记录依次组成。

### 1.1 文件头
- 前 8 字节：魔数 `PBDEMS2\0`。
- 跟着 8 字节：skip（`Parser::new` 里 `reader.read_bytes(8)`）。
- 偏移 8 处读 4 字节小端 `offset`，跳到该 offset 处解一条 **`CDemoFileInfo`（`demo.proto`）** 作为全局头：

| 字段 | 类型 | 含义 |
|---|---|---|
| `playback_time` | float | 回放总时长（秒） |
| `playback_ticks` | int32 | 总 tick 数 |
| `playback_frames` | int32 | 总帧数 |
| `game_info` | CGameInfo | 比赛信息（含 `CDotaGameInfo`） |

- `playback_ticks / playback_time` 恒等于 tick 率（实证：8817145578 → 167082 / 5569.40 = **30.000**）。
- `CDotaGameInfo`（`demo.proto`）里有用字段：`match_id`(1)、`game_mode`(2)、`game_winner`(3)、`player_info`(4, 每玩家：`hero_name`/`player_name`/`is_fake_client`/`steamid`/`game_team`)、`leagueid`(5)、`radiant_team_id`/`dire_team_id`(7/8)、`end_time`(11)。

### 1.2 命令流（`EDemoCommands`，`demo.proto`）

| 值 | 命令 | 说明 |
|---|---|---|
| 1 | `DEM_FileHeader` | 文件头 |
| 2 | `DEM_FileInfo` | `CDemoFileInfo` 头 |
| 3 | `DEM_SyncTick` | 同步点；`Parser::prologue` 到此结束（**prologue 完成**） |
| 4 | `DEM_SendTables` | 发送表（字段序列化 schema，`CSvcMsgFlattenedSerializer`） |
| 5 | `DEM_ClassInfo` | 实体类注册（`CDemoClassInfo`，class_id ↔ network_name ↔ serializer） |
| 6 | `DEM_StringTables` | 字符串表（含 `instancebaseline` 基线、`CombatLogNames` 名称表、`EntityNames`） |
| 7 | `DEM_Packet` | 普通数据包（内含 svc/net/user/game 消息流） |
| 8 | `DEM_SignonPacket` | 连接包 |
| 13 | `DEM_FullPacket` | 全量（string table + packet） |
| 0 | `DEM_Stop` | 结束 |

- **每条记录都带一个 `tick`**。`Parser` 在 `on_tick_start(message.tick)` 里把它写入 `ctx.tick`（`messages.rs`）。
- **`ctx.tick()` = 这条 demo 消息上的全局 tick 戳**（服务端全局模拟 tick），**不是** `net_tick`（后者来自 `CNETMsg_Tick`，`on_net_message` 里单独写 `ctx.net_tick`）。
- 消息流转：`run_to_end` → `prologue`（读到 `DEM_SyncTick` 为止）→ 逐条 `on_tick_start(message.tick)` + `on_demo_command`。

### 1.3 字段为 30Hz 的原因
tick 率没有协议字段硬编码，但 `CDemoFileInfo.playback_ticks / playback_time = 30.000`（实测）。Dota 固定 30Hz 模拟。解析器 `TICK_RATE = 30` 与库默认一致，可放心使用。

---

## 2. 时间系统（本文档重点，首要病根）

### 2.0 一句话
> **回放时钟 `tick/30` 永不暂停；游戏时钟 `cle.timestamp()` 暂停时冻结；显示时钟 = 游戏时钟 − 号角基值。三者之差 = 开始前时长 + 累计暂停时长。**

### 2.1 三个"时钟"的精确定义

| 名称 | 来源/字段 | 单位 | 暂停 | 与回放时钟的关系 | 谁需要它 |
|---|---|---|---|---|---|
| **回放时钟（替身：raw / demotick）** | `ctx.tick() / TICK_RATE` | 秒 | **不暂停** | = 自身 | 实体快照（位置/血量/塔/野/净值/眼位实体）、building、能力、中立击杀 |
| **原始时间戳（替身：raw-time）** | `cle.timestamp_raw()` | 秒 | **不暂停** | `≈ tick/30 + Δ_file`（Δ_file≈每场常量，实测≈8.3s） | 等价于回放时钟；衡量"真实耗时" |
| **游戏时间（替身：game-time）** | `cle.timestamp()` | 秒 | **冻结** | `= raw − 累计暂停` | 一切"游戏内事件"（购买/金币/combat 类） |
| **显示时钟（替身：clock-time / 游戏时钟按 0:00）** | `cle.timestamp() − 号角基值` | 秒 | **冻结** | `= raw − 累计暂停 − 开始前时长` | 业务口径：-1:30 出门 / 0:00 号角 / 7-15 分钟窗口 |

> 说明：redota/官方把"回放时钟"叫 `serverTick * tickInterval`，把"游戏时间"叫 `game.time`，把"显示时钟"叫 `clockTime`。名称不同，量纲相同。

### 2.2 `tick` 到底是不是"暂停无关"？
**是**。`.dem` 是服务端回放，服务端全局 tick 计数器在暂停时**依旧前进**（只是游戏逻辑冻结）。实证：8817145578 从 tick 970→1063（原始秒 970→1063）期间游戏发生了 ~73s 暂停，`max tick = playback_ticks = 167082`，tick 流连续无大跳（秒步长只有 29/31/14 的 ±1 抖动，14 只在最末收尾）。**没有**整段停 tick。

### 2.3 游戏时钟是如何"冻结"的（官方实现，`redota Replay.js::processGameRules`）
```js
// CDOTAGamerulesProxy 实体的关键字段：
if ('m_pGameRules.m_bGamePaused' in delta) {
  game.isPaused = delta['m_pGameRules.m_bGamePaused'];
  game.pauseStartTick = game.isPaused ? delta['m_pGameRules.m_nPauseStartTick'] : null;
  if (!game.isPaused) {
    game.totalPausedTicks = delta['m_pGameRules.m_nTotalPausedTicks'];
  }
}
if ('m_pGameRules.m_nGameState' in delta)   game.phase = delta['m_pGameRules.m_nGameState'];
if ('m_pGameRules.m_flGameStartTime' in delta)   game.startTime = delta['m_pGameRules.m_flGameStartTime'] | 0;
if ('m_pGameRules.m_fGameTime' in delta)        game.time  = delta['m_pGameRules.m_fGameTime'] | 0;
else {
  // 7.32e 起 m_fGameTime 不再每 tick 更新；按服务端 tick 减去累计暂停 tick 推断：
  const ticksElapsed = (game.pauseStartTick || this.serverTick) - game.totalPausedTicks;
  game.time = (ticksElapsed * this.tickInterval) | 0;
}
get clockTime() { return this.startTime ? this.time - this.startTime
                                       : this.preStartTime ? this.time - this.stateTransitionTime : null; }
```
要点：
- **暂停开始时** `game.pauseStartTick = m_nPauseStartTick`（暂停起始 tick）。
- **暂停期间** `game.time = (pauseStartTick − totalPausedTicks) * tickInterval` = **冻结值**（`serverTick` 还在涨，但用 `pauseStartTick` 取固定值）。
- **恢复暂停** `game.totalPausedTicks = m_nTotalPausedTicks`（把暂停时长累加进去），游戏时间继续走。
- **`game.time`（= `cle.timestamp()`）≠ 显示时钟**。显示时钟要 `− this.startTime`（= `m_flGameStartTime`，**服务端 tick 口径下的号角绝对基值**）。

### 2.4 实证：8817145578 三个时钟的真实数值
（探针 `probe_time`，逐分钟采样；完整在 `.tmp/probe_time_8817145578.txt`）

| 原始秒 `tick/30` | `cle.timestamp()` | `cle.timestamp_raw()` | 备注 |
|---|---|---|---|
| 0 | 8.20 | 8.20 | 文件开始；`raw == game（未暂停，含 +8.3 文件偏移）` |
| 823 | 831.33 | 831.33 | `raw == game`，偏移 +8.3 |
| 899 | 907.37 | 907.37 | 英雄首次出现；`raw == game` |
| 960 | 968.23 | 968.23 | 暂停前最后一刻 |
| **1035** | **969.77** | **1043.27** | **暂停中/刚恢复：`raw` 继续 +75s，`game` 只 +1.5s → 差 ~73.5s** |
| 1062 | 997.43 | 1070.93 | `game` 落后 `raw` ~73.5s；`game_state(value=5)` = 号角 |
| 1200 | 1134.80 | 1208.30 | 之后 `game = raw − 65.2`，`raw = tick + 8.3` |
| 5569（结束） | 5503.87 | 5577.37 | `game` 落后 `raw` ~65.1s |

**直接结论**：
- `cle.timestamp_raw() ≈ tick/30 + 8.3`（**恒定**，每场一个 `Δ_file`，不随暂停变）。
- 没暂停时 `cle.timestamp() == cle.timestamp_raw()`。
- 暂停 ~73.5s 后 `cle.timestamp() = cle.timestamp_raw() − 73.5`，之后两者同步前进直到下一次暂停。
- **暂停时长** = 冻结前后 `game` 的差值 = `73.5s`（任务所述 72s 略四舍五入的差异来自帧边界）。
- **显示时钟的号角基值 `game_start_cl` = 997.43s**（正好是 `value==5 / GAME_IN_PROGRESS` 那刻的 `cle.timestamp()`；这也解释了为什么**不能用** `m_fGameTime` 直接当 0:00——它不是 0）。

### 2.5 换算公式（数据层直接用）
设：
- `R = ctx.tick() / TICK_RATE`（回放秒，实体现有 `game_time_sec`）
- `cg = cle.timestamp()`（combat-log 游戏时间）
- `cr = cle.timestamp_raw()`
- `GS =` 号角基值 = 首次/最近一次 `value==5`（`DOTA_GAMERULES_STATE_GAME_IN_PROGRESS`）的 `cg`（**解析器已把它存进 `game_state` 事件 `properties.game_start_cl`**）

则有：
```
cr        = R + Δ_file                 (每场常量，≈8.3s)
cg        = cr − Σ暂停_已过去           (= raw − 累计暂停)
显示时钟   = cg − GS = (R + Δ_file − 累计暂停) − GS
等价地     = R − (GS − Δ_file) − 累计暂停
```
> 实务上数据层最省心的做法：**把所有事件统一折算到"游戏时钟（0:00 显示口径）"**，即 `clock = (cg − GS)`（对 combat-log 事件）或 `R − game_start_raw`（对快照/实体事件，其中 `game_start_raw` 用 `value==5` 那刻的 `R`）。这样 Q1/Q2/Q3 的 "-1:30 ~ 7 / 7-15 / 15+" 窗口、眼位 placed↔destroyed 配对、购买↔位置对齐 全部成立。

### 2.6 为什么 gameclock 对不上（完整解释）
`gameclock`（显示时钟）对不上`录像时间`（回放时钟）由**三部分**叠加：
1. **开始前时长（选人/加载/录像前置空转）**：`.dem` 从服务端很早开始录（8817145578 英雄在 raw≈899s 才出现；ARCHITECTURE.md 也记录过 750s 才出现的场次）。这一大段回放时钟在走，但游戏时钟在号角前是负值/未计。
2. **累计暂停时长**：暂停让回放时钟照走、游戏时钟冻结（8817145578：~73.5s）。
3. **帧/tick 相位 + 每场文件偏移 `Δ_file`**：`cr = R + Δ_file`，说明 combat-log 时间基与 demo tick 计数器基本来就差一个每场常量（约几秒到十几秒）。

所以 **`游戏时钟 ≠ 回放时钟`，差 = 开始前时长 + 累计暂停 + Δ_file**。**正确做法**：始终以 `value==5`（或 `m_flGameStartTime`）为号角基值把两者统一到"0:00 显示口径"，而不是假设 `tick/30` 就是游戏时钟，也不是假设某一 combat-log 时间就是 0:00。

---

## 3. 消息类型语义（从解析器视角）

### 3.1 观察者钩子（source2-demo，`parser/observer.rs`）
解析器把 `.dem` 一次跑完，按兴趣分发给各 extractor：
- `#[on_tick_start]`（`ctx.tick()` 前进到新值才触发）：读**上一个 tick 的实体状态**（增量在 tick_start 之后才应用）→ **存在 1 tick 滞后，1s 粒度可忽略，高精度需注意**。
- `#[on_entity]`（`EntityEvents::Created / Deleted`，来自 `SvcPacketEntities` 的增量）：实体**创建/删除**事件。视觉守卫生成、塔消失、野怪消失等用它。
- `#[on_combat_log]`（来自 `EDotaUserMessages::DotaUmCombatLogDataHltv`，**缓冲到 tick_end 才批量派发**）：给 `CMsgDotaCombatLogEntry`。
- `#[on_game_event]`（Source1 legacy game event `CSvcMsgGameEvent`）：`dota_*` 事件。
- `#[on_demo_command] / #[on_message] / #[on_svc_message]` …

### 3.2 触发语义（重要）
- **tick 偶奇相位**：`.dem` 的 tick 流存在"某一整段只送偶数或只送奇数 tick"的相位（source2-demo 大步长 ±2 推进）。**不要用 `tick % 30 == 0` 门控 1s 采样**，会静默饿死（ARCHITECTURE.md §6.6 已定位）。应用 **"秒变化即采样"**：记录上一次 `tick/30`，变了才采一次（与相位无关、天然 1Hz、同秒去重）。
- **combat-log 时间基准**：`cle.timestamp()` 与 `ctx.tick()` **不是同一基**（差 `Δ_file`），且 combat-log 在 tick_end 派发时 `ctx.tick()` 可能已前进到下一个 tick。

### 3.3 常见的 Dota 状态机（`DOTA_GAMERULES_STATE_*`，`value` 命名）
```
0 INIT / 1 WAIT_FOR_PLAYERS / 2 HERO_SELECTION / 3 STRATEGY_TIME /
4 PRE_GAME / 5 GAME_IN_PROGRESS(号角) / 6 POST_GAME / 7 DISCONNECT /
8 TEAM_SHOWCASE / 9 CUSTOM_GAME_SETUP / 10 WAIT_FOR_MAP / 11 SCENARIO_SETUP / 12 PLAYER_DRAFT
```
- **号角 = `value==5`（GAME_IN_PROGRESS）**。该刻的 `cle.timestamp()` 是显示时钟基准（存为 `game_state.game_start_cl`）。

---

## 4. 关键字段语义（每个对解析有意义的字段）

### 4.1 实体字段
| 字段 | 类型 | 含义 |
|---|---|---|
| `m_vecOrigin` | cell+vec（3 轴） | **世界坐标**。Dota 用"格子+格内偏移"存坐标：`world = (cell − 128) * 128 + vec`（`cell` 是无符号 0..255，128=世界中心；`vec` 是格内 -128..128）。三轴分别 `m_cellX/m_vecX`、`m_cellY/m_vecY`、`m_cellZ/m_vecZ`。**必须用公式折算，不能拿 cell 当坐标**。 |
| `m_iPlayerID` | uint | 英雄对应的**玩家 ID = 2 × 头玩家索引（0..18）**（7.31 起阀值翻倍）。`INVALID = 0xFFFFFFFF`。用它照 header 解玩家/英雄/队伍/槽位。 |
| `m_iTeamNum` | int | 队伍：2=天辉(radiant/GOOD_GUYS)，3=夜魇(dire/BAD_GUYS)。 |
| `m_iHealth`/`m_iMaxHealth` | int | 当前/最大血量。≤0 判死。 |
| `m_flMana`/`m_flMaxMana` | float | 蓝量。 |
| `m_fCooldown`/`m_iLevel`/`m_iManaCost`/`m_hOwnerEntity`/`m_hAbilities.*` | 技能 | 冷却余秒、等级、蓝耗、属主、技能句柄。能力实类 `CDOTA_Ability_*`；冷却=0 通常是被动/未进冷却。 |
| `m_vecDataTeam.000X.m_iNetWorth/m_iReliableGold/m_iUnreliableGold/m_iTotalEarnedGold/m_iTotalEarnedXP` | int | 逐玩家**净值和金币**（在 `CDOTA_DataRadiant/CDOTA_DataDire` 上，下标=玩家）。 |
| `CDOTA_DataRadiant/Dire .m_rgRadiantNetWorth.XXXX` | int | 逐**分钟**的团队净值（`XXXX`=分钟，用作权威 gold-adv）。 |
| `entity.index()` | 稳定句柄 | 每实体在 `.dem` 中的网络索引，**生命周期内不变**。用它当主键区分同类多实例（塔/兵营/野怪/守卫）。 |
| `CBodyComponent.m_angRotation` | float[] | 朝向（旋转），仅 Y 轴（`[1]`）有用。 |
| `m_bIsWaitingToSpawn` | bool | 是否在等出生（判断是否真正进游戏）。 |

### 4.2 CDOTAGamerulesProxy（时间/暂停/状态，官方字段名）
`m_pGameRules.m_bGamePaused`、`.m_nPauseStartTick`、`.m_nTotalPausedTicks`、`.m_nGameState`、`.m_flGameStartTime`、`.m_flStateTransitionTime`、`.m_flPreGameStartTime`、`.m_fGameTime`、`.m_iNetTimeOfDay`。
- 7.32e 起 `m_fGameTime` 不再连续更新，靠 `serverTick − totalPausedTicks` 推断（见 2.3）。

### 4.3 combat-log 字段（`CMsgDotaCombatLogEntry`，`dota_shared_enums.proto`）
| 字段 | 含义 |
|---|---|
| `type` | `DOTA_COMBATLOG_TYPES`：Death / Health / Gold / Purchase / Ability / ModifierAdd · Remove / GameState / Damage / Heal / Xp / Item / … |
| **`timestamp`** | **游戏时间**（`game.time`，暂停冻结，非 0 显示口径）。 |
| **`timestamp_raw`** | **原始时间**（`≈ tick/30 + Δ_file`，不随暂停冻结）。 |
| `value` | 通用数值：Gold→金额；Purchase→内部 item index（**不是金币**）；GameState→state 值；Death→? |
| `attacker_name`/`target_name`/`damage_source_name`/`inflictor_name`/`value_name` | 字符串表名。**purchase 的购买者在 `target_name`**（客串 target=买家）。 |
| `location_x`/`location_y` | 事件世界坐标。 |
| `gold_reason`/`xp_reason` | 金币/经验来源码。 |
| `attacker_team`/`target_team` | 队伍码（2/3）。**Death 的 `target_team` = 死者/被毁单位自己的队**（眼位配对靠它）。 |
| `obs_wards_placed` | 玩家**插假眼计数**（>0 表示这次 combat entry=放置假眼）。 |
| `is_attacker_hero`/`is_target_hero`/`is_attacker_illusion`/`is_target_illusion`/`is_target_building` | 归属/类型判定。 |
| `neutral_camp_type`/`neutral_camp_team` | 野怪营位置/归属。 |
| `last_hits`/`networth`/`gpm`/`xpm`/`attacker_hero_level`/`target_hero_level` | 快照型数值。 |
| `assist_players` | 击杀助攻列表。 |

### 4.4 归属/语义注意点（易踩坑）
- **购买者 = combat `target_name`**（不是 attacker）。`value` 是内部 item index。
- **中立击杀归属**：解析器只从实体死亡（hp≤0/消失）拿到"某点某刻死了一只野怪"；**谁打的**要靠 combat-log `Death`(target=该中立 npc) 与这些位置击杀做匹配（field 无直接 owner）。可用 `nearest combat Death within ~15s + 同类`。
- **"反眼归属"**：被反假眼的"真眼覆盖判定"只能用 `cle.timestamp()`/`t_tick`（同一游戏时钟）做 `place≤t≤destroy` 窗口，且要**同队口径**；用 raw 时钟会错位。
- **塔/兵营/瞭望塔**：静态坐标；摧毁=hp≤0 或实体连续 2s 消失（时长=最后可见秒+1）。

---

## 5. 各事件/实体"应该用哪个时钟"（权威对照表）

> **规则**：位置、实体状态、持续存在的物体 → **回放时钟 `tick/30`**；离散的、由 combat-log 驱动的"事件时刻" → **游戏时钟 `cle.timestamp()`**。两者**绝不可混在同一张表的同一个 `game_time_sec` 列**。

| 数据 | 权威时钟 | 备注 |
|---|---|---|
| hero / ward / building / networth / team_networth 快照 | 回放 `R` | 逐秒取样，秒级去重取最新 |
| building_spawn / building_destroyed | 回放 `R` | 静态坐标 + 存活状态 |
| neutral_kill / roshan_kill | 回放 `R` | 实体消失判定 |
| ability_known/learn/cd / item / smoke | 回放 `R` | 逐秒技能状态 |
| purchase | **游戏时钟 `cg`**（=`R+Δ_file−暂停`） | 或用 `t_tick` 统一 |
| gold | **游戏时钟 `cg`** | 同上 |
| ward_placed（实体 Created） | **回放 `R`** | 与实体快照同钟 |
| ward_destroyed（combat Death） | **游戏时钟 `cg`**，但**另存 `t_tick=R`** | 双钟并存，配对用 `t_tick` |
| ward_destroyed（实体消失推断） | **回放 `R`** | |
| ward_use（插假眼, combat `obs_wards_placed>0`） | **游戏时钟 `cg`** | |
| game_state（value=5 号角） | `cg`（自身就是基准） | `properties.game_start_cl` |

---

## 6. 已实证案例：8817145578（暂停 72s / gameclock 对不上 / 眼位被污染）

- header：`match_id=8817145578`，`playback_time=5569.40s`，`playback_ticks=167082`，`tick_rate=30.000`。
- 英雄首次出现在 raw≈899s（回放时钟），号角在 raw≈1062s（`value=5`，`game_start_cl=997.43`）。
- 一场约 **73.5s** 的暂停：`cle.timestamp()` 从 968.23(暂停前)→969.77(暂停后)，只涨 1.5s；而 `cle.timestamp_raw()` 从 968.23→1043.27，涨 75s → **游戏时钟落后原始时间 ~73.5s**。
- 于是**游戏时钟 0:00 ≠ raw 0**，且 `purchase/gold/ward_destroyed(combat)`（游戏时钟）与 `hero/ward_placed/building`（回放时钟）在 `game_time_sec` 上**相差 ~973s**（≈900 开始前 + 73 暂停）。这正是"gameclock 对不上 / 眼位被 pollute"的全部来源。
- 数据层应：对 combat-log 事件统一 `clock = cle.timestamp() − game_start_cl`；对快照类统一 `clock = R − game_start_raw`（`game_start_raw` = `value=5` 那刻的 `R`）。

---

## 7. 数据层：可靠时间基准的落地做法（给后续解析/分析）

1. **不要让 `game_time_sec` 继续当"一个钟"用**。给它明确一个标准（推荐：**全部折算到"游戏时钟 0:00 显示口径"**），或者保留双列：`raw_sec`（回放时钟，排序/单调用）+ `game_sec`（0:00 显示时钟，业务/窗口/配对用）。
2. **解析器必须显式提取** `m_pGameRules.m_flGameStartTime`（或 `value==5` 的 `cle.timestamp()`/`R`）作为号角基准，替换现在的"building_spawn+90 启发式"。
3. **暂停**：解析器可从 `m_pGameRules.m_nTotalPausedTicks` / `m_bGamePaused` / `m_nPauseStartTick` 或 hero 冻结法拿到每个暂停块；游戏时钟公式 = `raw − 累计暂停`。
4. **时间窗口**（-1:30~7 / 7-15 / 15+，眼位/经济）必须基于"0:00 显示口径"，否则带暂停的场次全部错位。

---

# 附录 A：dota_parse 各 extractor 时间/字段问题清单 + 修正建议

> 逐条列出当前 `dota_parse/src/parse.rs` 里用错的字段/时间语义及正确做法。按严重度排序。

### A1.【严重】`purchase` 事件用游戏时钟，与周围"回放时钟"事件不同源
- 现状：`PurchaseExtractor::on_combat_log` 用 `cle.timestamp()`（`parse.rs:258`），`build_event_rows` 里 `sec = t.floor()`（`:1662`）。
- 结果：`purchase.game_time_sec` 是游戏时钟，而英雄位置/塔状态是回放时钟。二者相差（开始前+暂停+Δ_file），**"某时刻在哪、买了什么"对不上**。
- 建议：① 所有事件统一用同一个时钟；若要用游戏时钟，则同时把 `tick/30`（`t_tick`）与 `cle.timestamp_raw()` 一并存进 `properties`。② 或统一折算到 `game_sec = cle.timestamp() − game_start_cl`。

### A2.【严重】`gold` 事件用游戏时钟，而 `neutral_kill` 用回放时钟（同属 jungle extractor，混钟）
- 现状：`JungleExtractor` 里 `neutral_kill` 用 `tick/30`（`:875`），`gold` 用 `cle.timestamp()`（`:850/892`）。同一 extractor 产两种钟。
- 结果：野区"刷野热区 + 逐玩家金币"无法在同一秒上对齐。
- 建议：gold 与 neutral_kill 统一时钟；gold 保留 `t_tick`。

### A3.【严重，直接导致"眼位被污染"】`ward_placed` 用回放时钟，`ward_destroyed`(combat) 用游戏时钟
- 现状：`WardPlaced.t = tick/30`（`:403`），`ward_destroyed` 的 `game_time_sec = cd.t.floor() = cle.timestamp()`（`:1432`）。
- 结果：眼位 placed↔destroyed **FIFO 配对/存活时长** 在 `game_time_sec` 上跨钟，带暂停/前置的场次完全错位（8817145578 差 ~973s）。q5_ward / ward_survival 因此被"pollute"。
- 建议：`destroyed` 事件的 `game_time_sec` 改用 `t_tick`（回放 `R`，与 placed 同钟）；把 `cle.timestamp()` 只存 `properties.t_cle`。**分析层目前已在 q5 `parse_match` 里改用 `t_tick`（`:236` 注释已指出）——应把修正下沉到解析器，而不是在分析层打补丁。**

### A4.【误导】`game_state` 事件把基值 997.43 标成"游戏时钟 0:00 锚点"
- 现状：`WardExtractor` 把 `value==5` 的 `cle.timestamp()` 存成 `game_start_cl`，注释写"游戏时钟 0:00 锚点"（`:312`），事件 `game_time_sec=997`（`:1370`）。
- 结果值**不是 0**（是 997.43，即 `game.time` 基值）；若被当作"0:00"直接减会错 ~997s。
- 建议：改注释为"号角（GAME_IN_PROGRESS）时刻的 `cle.timestamp()` 基值，显示时钟 = cg − 该值"。同时建议**也存 `game_start_raw`（那刻的 `tick/30`）**，供快照类折算。

### A5.【严重】`game_time_sec` 在 `game_events` 表内**混用两套钟**，无可信的统一键
- 现列表：`purchase/gold/ward_use/combat版ward_destroyed` = 游戏时钟；`ward_placed/building/ability/neutral_kill` = 回放时钟；`game_state` = 基值。
- 结果：任何 `ORDER BY game_time_sec`、时间窗口、跨类型 JOIN 都会错位。**这是"反复重跑仍对不上"的总根**。
- 建议：schema 加 `raw_sec`（回放）与 `game_sec`（0:00 显示）两列，或至少统一全部到 `game_sec`；保留 `event_properties.t_tick/t_cle` 作为桥。

### A6.【中】英雄快照用 `tick/30`，但与 `m_iPlayerID` 无关的召唤物/无槽位实体也进了快照
- 现状：`PositionExtractor` 只按 class 前缀 `CDOTA_Unit_Hero_` 收（`:201`），`build_snapshot_rows` 对无 header 玩家回退到 class 派生 id（`:1606`）。
- 结果：召唤物（怪腾/猴子猴孙等，class 也是 `CDOTA_Unit_Hero_*` 但 `m_iPlayerID` 无效）会被当英雄采样，污染位置热区。
- 建议：仅保留 `pid != INVALID && pid % 2 == 0` 的英雄；召唤物归入"unit"或丢弃。

### A7.【中】`on_entity(Created)` 读到的位置是**创建瞬间**的 `m_vecOrigin`，可能为 0/未初始化
- 现状：`WardExtractor::on_entity`（`:391`）创建时即读坐标，`build_ward_snapshot_rows` 里过滤 `x==0&&y==0`（`:1511`）。
- 结果：部分守卫首帧坐标缺失，`ward_placed` 事件坐标不可靠；依赖它的热力图缺样本。
- 建议：`ward_placed` 坐标在下一个 `on_tick_start`（实体已稳定）再取，或与 per-entity 快照首次非零坐标对齐。

### A8.【中】`on_entity(Created)` 不是"唯一出生标记"，会重复触发 → 眼位 over-count
- 现状：`WardExtractor::on_entity` 只认 `Created`（`:384`），每次触发都记一条 `placed`。
- 实证（8817145578 小样本）：同一实体在**每个 tick 上都以 `Created` 重新出现**（`on_entity(Created)` 由 packet-entities 的 2bit cmd==2「enterPVS」驱动；cmd==0=update、1=skip、3=delete）。游戏开局（raw 899–905s）与中场（raw 3000s）均见英雄/塔/兵/野怪被反复 `Created`。**对守卫的影响直接可测：`ward_placed` 行=148，但 distinct entity_index=141 → 7 条重复**（约 +5%）。
- 建议：① `ward_placed` 按 `entity_index` 去重（每个 index 只记首次）；② 或改用 `on_tick_start` 里的"实体首次出现 if 不在 samples"判定（与 hero 快照同门控），避免 on_entity 的 PVS 抖动；③ `ward_placed` 的"真实出生时刻"应以守卫实体**第一个非零/稳定坐标**为准，而不是 on_entity(Created) 的触发 tick。

### A9.【低】`TICK_RATE` 硬编码 30
- 现状：`parse.rs:31` const 30。
- 建议：可从 `playback_ticks / playback_time` 计算（实测恒 30），或读库默认。硬编码++防御即可，不必改。

### A10.【低，采样语义】`on_tick_start` 读的是**上一 tick**状态（1 tick 滞后）
- 现状：注释已说明（`:20-21`、`main.rs` 无）；ARCHITECTURE.md §6.6 也记录。
- 建议：1s 粒度忽略；若未来做高精度，须在 tick_start 前先应用本 tick 增量，或改用 `on_tick_end`。

### A11.【低】`ward_destroyed` 的"实体消失"分支与"combat Death"分支重复/冲突
- 现状：实体消失（`:461-481`）与 combat Death（`:511-540`）各产一条 `destroyed`，`build_ward_event_rows` 用"±15s 且同型最近消失实体"去配对（`:1415-1421`），成对时二选一。
- 结果：同一守卫可能产生两条 destroy（一条 combat、一条 entity），配对能消重但**时间取的是 combat（游戏时钟）**，与 placed（回放）仍错位。
- 建议：以 `t_tick` 统一配对与时间。

---

# 附录 B：小样本逐行归类对照（8817145578, 回放 raw 899–905s）

> 探针 `probe_dump` 在窗口 `raw[899,905]s`（tick 26971–27149）抓到的一"行行信息"，按来源归类并逐条注解（完整文件 `.tmp/probe_dump_8817145578.txt`）。这是"每一行信息意义"的可复现样本。

## B1. [HEADER] 文件头 —— `CDemoFileInfo`
```
match_id=8817145578  game_mode=2  league_id=19696
playback_time=5569.40s  playback_ticks=167082  playback_frames=83535
=> tick_rate=29.999997895868642 (≈30)
player[i] hero/team/steamid/name (10 名, index 0..9)
```
**意义**：唯一"元信息"层。`playback_ticks/playback_time=30` 即 tick 率。`game_team` 2=天辉/3=夜魇。

## B2. [CONTAINER] demo 命令脊柱 —— `EDemoCommands`
```
tick=4294967295  DemFileHeader      # 文件头
tick=4294967295  DemSignonPacket    # 连接包 (payload 是实体/字符串表推送)
tick=4294967295  DemRecovery        # 恢复点
tick=4294967295  DemSendTables      # 发送表 (837KB, 字段序列化 schema)
tick=4294967295  DemClassInfo       # 类注册 (135KB)
tick=4294967295  DemStringTables    # 字符串表 (266KB, 含基线/名称表)
tick=4294967295  DemSyncTick        # ← prologue 结束标记
tick=4           DemPacket          # 之后才是"正常"游戏包, tick=4,7,9,... 递增
...
tick=26979       DemPacket          # 窗口内的包 (每 2 tick 一个: 26975/26979...=奇数/偶数相位混)
```
**意义**：容器结构。`DemSyncTick` 之前 tick 全是 `u32::MAX`（无真实 tick）；之后每个包带真实服务端 tick，**所有包都挂着同一 tick**（同一 tick 内多个包）。注意出现了 `DemRecovery`（文档 enum 里没列，说明源还导出恢复命令）。

## B3. [TICKS] tick 边界
```
span 26971..27149 (共 89 个, ~30/s)   # tick 26971,26973,26975,26979,... (偶有 +2/+4 跳跃)
```
**意义**：回放时钟刻度。`tick/30` = 回放秒。每 tick 间隔通常 +2（奇偶相位），偶发 +4/继续。

## B4. [ENTITIES] 实体创建/删除 —— 关键字段
```
@26979 New CDOTA_PlayerResource           idx=140            # 玩家资源容器
@26979 New CDOTA_DataRadiant              idx=142  team=2     # 天辉数据(净值/金币在上面)
@26979 New CDOTA_DataDire                 idx=143  team=3     # 夜魇数据
@26979 New CDOTA_DataSpectator            idx=144  team=1
@26979 New CDOTA_DataCustomTeam           idx=145  team=6     # 自定义队 x8
@26979 New CDOTA_NeutralSpawner           idx=567  team=0     # 野点刷新器 x28
@26979 New CDOTA_RoshanSpawner            idx=659  team=0
@26979 New CDOTA_BaseNPC_Tower            idx=698  team=2 hp=2500 cellX=-32 cellY=-48   # 塔(x22)
@26979 New CDOTA_BaseNPC_Barracks         idx=707  team=2 hp=2200 cellX=-38 cellY=-36   # 兵营(x12)
@26979 New CDOTA_BaseNPC_Watch_Tower      idx=754  team=2 hp=450 cellX=-32 cellY=-4     # 瞭望塔
@26979 New CDOTA_Unit_Roshan              idx=1342 team=4 hp=6000 cellX=-26 cellY=18
@26979 New CDOTA_Unit_Hero_SandKing       idx=1352 pid=0 team=2 hp=670 cellX=-54 cellY=-54
@26983 New CDOTA_Unit_Hero_Lich           idx=321  pid=10 team=3 hp=654 cellX=52 cellY=50
@27009 Del CDOTA_Ability_Creep_Siege      idx=1306             # 删除=单位消失/置换
```
**意义**：
- `entity.index()` 是稳定且唯一的实体主键（每实例一个）。
- 英雄 `m_iPlayerID = 2 × header index`（SandKing pid=0↔player[0]，DarkWillow pid=2↔player[1]，…Lion pid=18↔player[9]，**逐一吻合**）。
- `m_iTeamNum` 2=radiant/3=dire（英雄、塔、兵营据此分边；`team=4`=Roshan、`team=0`=中立）。
- `m_iHealth` 初始 HP（塔 2500/1800/2600、Roshan 6000、英雄 582–868）。
- 世界坐标 = `(cell−128)*128 + vec`；此处打印的是 `cell-128`（居中格号），负=天辉半场、正=夜魇半场；天辉泉水≈`(-54,-54)`（即世界 ≈(−6912,−6912)+vec）。
- **`on_entity(Created)` 会反复触发**（见 A8）：同一英雄/塔/兵在窗口内几乎每个 tick 都重新出现 `New`，不是"唯一出生"。

## B5. [COMBAT] combat-log 条目
```
@26985 DotaCombatlogGold        ts=907.63 raw=907.63 target=npc_dota_hero_lion value=600 gold_reason=0 loc=(7300,6100)
@26985 DotaCombatlogPurchase    ts=907.67 raw=907.67 target=npc_dota_hero_lion value=7
@26985 DotaCombatlogModifierAdd ts=907.63 raw=907.63 attacker=dota_fountain target=npc_dota_hero_shredder value=0 a_team=3 t_team=3
@27033 DotaCombatlogAbility     ts=910.00 raw=910.00 attacker=npc_dota_hero_windrunner
```
**意义**：
- **游戏时钟** `cle.timestamp()` 与 **原始时间** `cle.timestamp_raw()` 此时**相等**（未暂停）：`ts == raw == 907.x`（= raw tick/30 + Δ_file，此处 ≈907 = 903.7tick + ~3.5）。→ 提醒：目前帧内 `cle.ts` 比 `ctx.tick` 略大，正是"combat 时钟基与 demo tick 基差一个 Δ_file"的体现。
- `Gold`：`target`=得金英雄，`value`=金额，`gold_reason`=来源码，`loc`=世界坐标（7300,6100=夜魇泉/商店区）。
- `Purchase`：**`target`=购买者**，`value`=内部 item index（非金币）。
- `ModifierAdd/Remove`：属性增益/移除（`attacker=dota_fountain`=泉水光环），`a_team/t_team`=双方队伍。
- `Ability`：技能释放（attacker=英雄）。

## B6. [GAME_EVENTS] legacy game event
```
@27111 dota_chase_hero {target1=Int(434), type=Byte(0), gametime=Float(914.63),
                        target1playerid=Byte(8), target2playerid=Byte(64), eventtype=Int(1)}
```
**意义**：Source1 legacy game event。字段带 **`gametime`（Float）= 该事件的"游戏时间"**（≈914.63），以及 `target1/2playerid`（8=pid，64=0x40=夜魇阵营/槽位标记）、`eventtype`。→ 提醒：game event 自带 `gametime` 字段，可作时间基准之一。

## B7. 归类小结
| 类别 | 载体 | 时间字段 | 关键主键 | 备注 |
|---|---|---|---|---|
| 容器 | `CDemoFileInfo` / `EDemoCommands` | `playback_time/ticks` | — | tick 率 30 |
| 实体状态 | `on_entity` / `on_tick_start` | `tick/30`（回放） | `entity.index()` | `m_iPlayerID=2×index`、team 2/3、cell→world |
| 离散事件 | combat-log / gamestate | `cle.timestamp`（游戏）/`timestamp_raw`（原始） | attacker/target/value | 同名条目含双时间 |
| 语义事件 | legacy game event | 自带 `gametime` | target1/2playerid | 含 `eventtype` |

---

# 附录 C：combat-log 守卫（眼位）事件语义与陷阱（真眼/假眼「使用」研究专用）

> 针对"研究 uses sentry ward / observer ward"的专项结论。全部经 `probe_ward_use` / `probe_sentry` 对 **8817145578** 实证，并与解析器落库结果逐条对账。**这是判断真假眼行为最可靠的依据。**

## C1. 关键前提：combat-log 里守卫相关信号的 4 种来源

| # | 信号 | 出处 | 计数(8817145578) | 能否区分真/假眼 | 有无位置 |
|---|---|---|---|---|---|
| P1 | **"使用眼"条目**：`type=DotaCombatlogItem`；该条目的 **`inflictor_name`**（语义＝"施加该效果的一方/来源"，对 item-use 恰好是**被用的道具**）= `item_ward_sentry / item_ward_observer / item_ward_dispenser` | `cle.inflictor_name()` | sentry=60, observer=30, dispenser=123 | **不能**（dispenser 兼放真/假眼） | **无**(loc=0,0) |
| P2 | **实体出生**：`CDOTA_NPC_Observer_Ward`(假眼) / `CDOTA_NPC_Observer_Ward_TrueSight`(真眼) | `on_entity`/`on_tick_start` | 假眼 52, 真眼 89 | **能** | **有**(cell→world) |
| D1 | **被摧毁**：`type=DotaCombatlogDeath` 且 `target_name = npc_dota_observer_wards / npc_dota_sentry_wards` | `cle.target_name()` | 假眼 54, 真眼 94 | **能** | **无** |
| C1 | **假眼累计计数**：`type=DotaCombatlogPlayerstats` 上 `obs_wards_placed = N` | `cle.obs_wards_placed()` | 玩家 1..14 | 只统计**假眼** | 无 |

> **`inflictor_name` 不要被名字误导**：它语义是"施加该效果的一方/来源"（伤害/死亡里＝攻击来源），**不是"使用"语义**。只是 `DotaCombatlogItem`（使用道具）这类条目里，它恰好装的是**被用的道具名**。所以用它判"玩家放没放眼"可行，但**用它判"真眼还是假眼"不可行**（见 C2b）。
> **`obs_wards_placed` 也不是"放置事件"**：只出现在 `Playerstats` 上，是该玩家**累计假眼数**（1..14），`attacker`=dota_unknown、`is_attacker_hero=false`。**解析器用 `obs_wards_placed>0 && is_attacker_hero` 记 `ward_use` → 条件永假，`ward_use`=0（死代码）**。proto 里**没有** `sentry_wards_placed`。

## C2. 真眼(假眼)「使用/放置」怎么判（权威做法）
- **真眼放置的最佳信号 = 实体** `CDOTA_NPC_Observer_Ward_TrueSight` 首次出现（8817145578 = 89 个）。其位置真实、obs/sentry 明确。
- **combat-log 的 `item_ward_sentry`（60）远少于实体真眼（89）**：因为 7.3x+ 的 **Ward Dispenser（`item_ward_dispenser`，使用了 123 次）** 会同时放真/假眼，且 combat-log 只记 `item_ward_dispenser`，不再单独标 `item_ward_sentry/observer`。**只靠 combat-log 判真眼会严重漏（60 vs 89，漏约 1/3）。**
- **使用事件无位置**：`inflictor=item_ward_sentry` 那条 `location=(0,0)`。要拿放置坐标，必须用"该时刻前后、与本玩家同队、同类型(obs/sentry)"的守卫实体去配。

## C2b. 用"实体出生"对账 combat-log 放置信号（实证，`probe_ward_corr`）
- **每个守卫实体出生（真眼 89 / 假眼 52）都能在 ±2s 内找到一条 combat `DotaCombatlogItem` 且 `attacker=英雄`、`inflictor` 含 `ward` 的条目 —— 匹配率 100%。**
- 匹配到的 `inflictor` 分布：**`item_ward_dispenser=66`、`item_ward_observer=25`、`item_ward_sentry=50`**。换算：真眼 89 = 直接真眼 50 + 用药盒放的真眼 39；假眼 52 = 直接假眼 25 + 药盒放的假眼 27。
  - → **`inflictor=item_ward_sentry` 只覆盖 50/89 真眼；真眼里靠 `item_ward_dispenser` 放的 39 个，combat-log 里根本看不出是真眼。**
  - → 所以**判真/假眼一定要用实体类**；combat-log 的 `DotaCombatlogItem` 只能当"玩家在此时刻用了一个眼道具"的粗略触发，不能当类型判据。
- **孤儿 combat `item_ward_*` 条目（±2s 内无实体出生）有 36 条** ~ 说明**"用了眼道具"不等于"必有一个眼落地"**（可能补货、空放、或与实体出生错位）。所以用 combat-log 当"放置"会引入假阳/错位。
- 结论：**放置时刻 ≈ `DotaCombatlogItem`(inflictor=item_ward_*) 的攻击者+时间；放置类型与坐标 = 实体。两者按 `±2s + 同队 + 同实体index` 配对。**

## C3. 真眼(假眼)「被摧毁/过期」怎么判（权威做法）
- `type=Death` 且 `target_name=npc_dota_sentry_wards / observer_wards`。计数与实体对得上（真眼 94 摧毁 = 73 过期 + 21 被反；假眼 54 = 29 过期 + 25 被反，**与解析器落库完全一致**）。
- **自过期 vs 被反的判别（权威 = `attacker_name == target_name`）**：
  - `attacker_name == target_name`（都是 `npc_dota_*_wards`）→ **自然过期**（真眼 73，假眼 29）。
  - 其余一律**被摧毁/被反**：敌方英雄、小兵/塔、中立野怪(眼插野点被打)、**同队英雄自毁**。
  - **`target_team` = 守卫自己所属队伍**（2=天辉，3=夜魇）。
  - ⚠️ **不要**用 `attacker_team != target_team` 判"被反" —— 那会把"同队英雄自毁"漏成"非被反"（实测 40 场 4916 条里 25 条，占 0.5%）。**owner 裁定（2026）：同队英雄自毁在统计意义上计入"被反"。** 详见 §C6。
- **Death 条目不带位置**（`location=(0,0)` / NULL）→ 把销毁绑到具体那支眼**只能靠实体自身末现**，见 §C6。
- 注意：`value_name` 上的值可能是**技能/装备**（如 `modifier_ancient_apparition_bone_chill_debuff`、`item_ward_sentry`）而非单位——**不要用 value_name 判守卫类型，要用 `target_name`。**

## C4. 守卫研究专属陷阱清单
1. **两种眼名极易混**：真眼实体类 = `CDOTA_NPC_Observer_Ward_**TrueSight**`（名字里有 Observer，其实是真眼/哨卫）；假眼 = `CDOTA_NPC_Observer_Ward`（无后缀）。死亡目标 npc：真眼 `npc_dota_sentry_wards`、假眼 `npc_dota_observer_wards`。
2. **obs_wards_placed = 累计计数，不是事件**（见 C1）——已导致解析器 `ward_use` 死代码。
3. **无 `sentry_wards_placed` 字段**（proto 确认不存在）；真眼的"使用"只能靠 `DotaCombatlogItem` 的 `inflictor=item_ward_sentry`（只覆盖 50/89）或**实体**。判真/假眼必须落到实体。
4. **`item_ward_dispenser`（新版眼药盒）**：同时放真/假眼，combat-log 不区分 → 判真/假眼必须落到实体。
5. **放置/使用事件无位置**（loc=0,0）→ 坐标必须与实体配对拿。
6. **重复触发**：`on_entity(Created)` 重复 → `ward_placed` 真眼 94 行/89 独立、假眼 54 行/53 独立；`ward_destroyed` 也可能对同一守卫有冗余（94 摧毁 > 89 实体，差额 5≈5%）。**按 `entity_index` 去重**。
7. **双时钟**：放置实体首见用**回放时钟** `tick/30`，combat 放置/摧毁用**游戏时钟** `cle.timestamp()`→配对须折算（见 §2.5）。
8. **守卫归属（谁放的）**：放置事件 `attacker_name`=英雄（`is_attacker_hero=true`）；但 `item_ward_dispenser` 那类不含明确玩家区分，需按"该时刻最近同队英雄 + 实体出生点"归属。

## C5. 数据层建议（眼位重写 / Q5 加强）
- **判真/假眼+坐标**：优先用实体（`CDOTA_NPC_Observer_Ward` / `..._TrueSight`）逐秒跟踪 + `ward_placed(placed)` 用其首个非零坐标；**不要**依赖 combat-log `inflictor` 判 obs/sentry（它分不出，且只覆盖 ~56%）。
- **判放置时刻/玩家**：用 combat-log `DotaCombatlogItem`（`inflictor=item_ward_sentry/observer/dispenser`，`attacker=英雄`）判定"此刻有人用眼"（**仅触发，不作类型/坐标**），与实体出生按 `±2s+同队+同实体index` 配对。
- **判摧毁原因**：`Death target=npc_*_wards` + `attacker==target`→过期；**其余全部算"被反"（含同队英雄自毁、小兵/塔、中立野怪）** —— 旧写的"`a_team != t_team`"口径已废弃，见 §C6.2。
- **落库**：`ward_use`（放置）事件应改为抓 `item_ward_sentry/observer/dispenser` 的 Item 条目（当前抓 `obs_wards_placed` 是错的、恒 0）。

## C6. 【2026 订正 · Q5 实证】到期/被反口径 + 销毁配对 + 判型互验（owner 现场验证揪出）

> 起因：Q5 报某支眼"6:39 插 / 15:47 被反 / 坐标在地图中心"，用户开游戏复核后纠正：**那支眼 8:53 才插、15:47 被反、在【地图左上方边缘】**；6:39 那条是**另一支眼自然到期**。据此查出四处错误，全部落在**分析层**（解析层数据是对的）。

### C6.1 判型权威 = 实体类名（并已与权威单位名互验）
- `CDOTA_NPC_Observer_Ward_TrueSight` = **真眼(sentry)**；`CDOTA_NPC_Observer_Ward` = **假眼(observer)**（`dota_parse/src/parse.rs::ward_class_type()`）。
- **不可**用 combat `inflictor` 判型 —— 40 场交叉表：`item_ward_dispenser` → observer **829** / sentry **1313**（两类都出）；`item_ward_observer`→observer 694；`item_ward_sentry`→sentry 1488。
- **独立互验（2026-09 订正）**："实体类名判型计数 vs Death 权威单位名逐场相等"这个验证方法**不成立** —— 全量 970 场并不相等（真眼 placed 70,421 / death 69,020；假眼 38,813 / 38,208），差额来自**右删失**（比赛结束时仍存活的眼没有死亡事件，232/970 场有差额）。
  改用**游戏自身寿命机制**验证判型：200 场里所有 `attacker==target` 的到期眼，`(销毁 − 放置)` **精确 = 该型寿命**（真眼 420 / 假眼 360；|偏差| ≤ 0.01s 命中率 **100%**，真眼 9,593/9,593、假眼 5,151/5,151）。若类名↔型对调，真眼会落在 +360 → 故判型正确。证据：`analysis/output_q6/q6_verify.json`。
- 探针 `probe_truesight` 直读 dem 实测：**combat log 的 target/attacker 不含实体类名（total=0）** → 判型只能来自实体表。

### C6.2 到期口径（owner 裁定）
- 权威：**`attacker == target` ⇒ 自然到期**；其余 = 被摧毁/被反（含**同队英雄自毁**，owner 裁定统计上计入"被反"）。
- 40 场 4916 条守卫死亡构成：到期 3289(66.9%)、敌方英雄反眼 1447(29.4%)、小兵/塔 90(1.8%)、中立野怪/召唤物 ~65(1.3%)、**同队英雄自毁 25(0.5%)**。
- 分眼型：**假眼** 1523 = 到期 969(63.6%) / 真·敌方反眼 544(35.7%) / 自毁 10(0.66%)，无野怪；**真眼** 3393 = 到期 2320(68.4%) / 敌方 1033(30.5%) / 自毁 15(0.44%) / 野怪 25(0.74%)。

### C6.3 销毁必须"一一对应"，不能用"窗口内最早"
- **Death 无坐标**（`parse.rs` 注释："Death entries carry no position"）；`ward_placed`/`entity_snapshots` 也只给**出生位置**。
- → 定位"哪支眼被哪条 Death 收走"**只能靠实体自身末现**。实测标定（40 场）：**到期死亡 `tt = 出生tt + 420` 精确到 0.0x s**；实体快照跨期 = 寿命 + 尾差（1Hz 量化，主峰 ≈9s）。
- 旧做法"`[出生, 出生+寿命]` 内取最早 death"会让**相邻同队同型眼互相抢死亡**。实证 8830423116：中心眼 `eidx=2474`(15:39.07 插) 抢走了左上眼 `eidx=3508`(8:52.20 插) 的 15:46.67 被反。
- **正确配对**：① 到期精确配对（出生+寿命 ±2.5s，`attacker==target`）；② 其余按 **`实体末现 − 9s`** 取最近，**每个 Death 只用一次**；③ 兜底 = 出生+寿命。

### C6.4 到期时刻 = 寿命常量（不要用 Death 事件定时）
- 判为 `expired` 的眼，销毁时刻**直接取 `出生 + 寿命`**（真眼 420 / 假眼 360）；Death 只用于**确认**到期。
- 理由：寿命是常量，而 Death 的 tick↔cle 有 1Hz 量化，用它会引入 0~2.5s 记账误差。

### C6.5 `ward_placed.properties.t_cle` 是坏字段（禁用）
- 该字段**恒比 combat_log 体系早 540.26s**（全表 `t_cle − t_tick = −531.53`；combat_log 恒为 `+8.73`）。
- 旧代码在无 combat use 命中时 fallback 到它 → 插眼时刻提前 → 凭空造出"存活 500~900s 的超长真眼"（全量 7203 条、占 10.2%）。
- **改用实体 `t_tick`（与 combat_log 同轴）经 `t_tick→t_cle` 映射还原游戏时钟。**

### C6.6 地图方位校准（用建筑，避免"左右上下"说错）
- `building_spawn`：Radiant 塔 x<0,y<0（左下）；Dire 塔 x>0,y>0（右上）→ **中心 = (0,0)**。
- **左上方 = x<0 且 y>0**。

### C6.7 订正后全量实测（970 场 / `dems/db_full`）
- 真眼 70,421 条：`survival` min **0.5s**、max **420.5s**；`>420.5s` = **0 条**（修复前 7203 条 >420s、最小 −2693s）。
- 判定构成（真眼）：被反 23,473(33.3%) / 到期 46,948(66.7%)。
- 样本（8830423116，与用户现场一致）：`eidx=3508` 真眼 (-6248.5, 7396.8) **左上边缘** 8:52.20 插 → **15:46.67 被反**（存活 414.5s）；`eidx=2474` 真眼 (603.8,−983.6) **中心** 15:39.07 插 → 16:28.30 被反（存活 49.2s）。

### C6.8 分析层落地实现（`analysis/q5_ward.py::parse_match`）
- 放置时刻：**use 候选窗口 `[实体首见 −35s, 实体首见 +2.5s]`**（同队同型，PVS 延迟容差）→ 取候选的 `t_cle`；
  无 use 命中则 `cle_for_tt(实体 t_tick)`。
- 销毁：**(use, death) 联合一一对应**（代价 = `|death.t_tick − (实体末现 − 9s)|`，即"实体消失紧跟死亡"的身份证据），
  硬约束 `place < death.cle <= place + 寿命 + 2`，且**到期型死亡**（`attacker==target`）还须 `|death.cle − (place+寿命)| <= 2.5`。
- 到期眼 `destroy = place + 寿命`；被反眼 `destroy = Death.t_cle`（并按寿命截断）。

### C6.9 【2026·前置修复】配对链路的 5 个根因（Q6 起算的基线，做眼位分析前必读）
> 起因：任务书报"`placed↔destroyed` 的 FIFO 配对错配（真眼 16:59 插却归入 15:39 的反眼）"。追查后确认 **FIFO 那套已彻底废弃**（`opendota_analysis/ward_survival_heatmap.py` 等读的 `ward_destroyed` 事件在新库**已不存在**），但**新的配对实现本身也有 5 个真根因**，逐个修掉：

| # | 根因 | 实证 | 修法 |
|---|---|---|---|
| 1 | **死亡池被"非眼 ward"污染**：`target LIKE '%ward%'` 把 `npc_dota_venomancer_plague_ward_*`(瘟疫守卫) / `npc_dota_witch_doctor_death_ward` / `npc_dota_juggernaut_healing_ward` 也当眼收进死亡池（80 场占 **8.43%**，829/9836） | `8826099852` eidx1690：被错配成"136s 被反"，实际**活满寿命到期** | death 只认**权威单位名** `npc_dota_observer_wards` / `npc_dota_sentry_wards` |
| 2 | **寿命是「游戏时钟 cle」常量，不是 tick 常量**：暂停时 cle 冻结而 tick 照走 → 用 tick 空间比 `出生+420` 会差出几十秒 | `8825996999` eidx962：cle 差**正好 420.00**，tick 差 437.5 → 旧写法漏配那条到期死亡 | **到期判定与寿命窗口全部改 cle 空间** |
| 3 | **dispenser 的 use 被塞进 observer 桶**（`inflictor='item_ward_dispenser'` 不含子串 "sentry"）→ **用眼药盒插的真眼永远找不到自己的 use** | `8826052816` eidx2402：enchantress 用 dispenser 插的真眼，其 use 被 17s 后才首见的 snapfire `item_ward_sentry` 抢走 → 错判 175s 被反 | dispenser 的 use **同时进 sentry/observer 两个候选池**（真/假由实体定） |
| 4 | **PVS 延迟**：守卫"首次被看到"（`ward_placed`/EntityNames Created）晚于真实插入 | 同上：真插 3:56.73，首见 4:13.87（**晚 17.1s**） | use 候选窗口扩成 `[首见 −35s, 首见 +2.5s]` |
| 5 | **同刻插入的平局 + use 独占的连锁失效**：两支同队同型眼同时刻插/同时刻到期时，按 `|cle−(插+寿命)|` 排序两边都≈0 → 顺序任意必错一支；且 use 被判给别的眼后，本眼所有死亡候选一起失效 | `8825996999` 眼 2530/2532 均 3:30–3:32 插、均 10:32 到期 | 改**全局一一对应**（代价含"实体末现−9s"身份证据），放置用"靠近首见"做平局判据，**取消 use 独占** |

**修复后健康度（80 场 / 9008 支眼）**：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 真错配（实体末现比所配死亡晚 >25s） | 122 条（3.87%） | **16 条（0.53%）** |
| 尾差落在 [5,14] 正常带 | 95.0% | **98.3%** |
| `survival` 上限 | 420.5s | **420.0s** |
| 超寿命行 | 9 | **0** |

**参照案例（5/5 通过）**：`8830423116` eidx3508 8:52.23→15:46.67(414.4s 被反) / eidx2474 15:39.07→16:28.30(49.2s 被反)；
`8826052816` eidx2402 3:56.73→10:56.73(420.0 到期)；`8825996999` eidx2530 3:30.33→4:48.33(78.0 被反) / eidx2532 3:32.13→10:32.13(420.0 到期)。

> ⚠️ **关于"半径"**：Death 条目**不带坐标**（见 C3/C6.3），所以 `placed↔destroyed` **无法**用半径匹配——用的是
> **实体存活窗口 + 实体自身末现**的身份匹配。半径（真视 1050 / 刁钻缓冲 1200）用在**反眼归属**与**刁钻眼位判定**上
> （眼位与真眼窗口都有坐标，可靠）。

### C6.10 【2026·第 6 个根因】"比赛结束"的权威取法 + 三个时钟的空间（做存活统计前必读）

**根因**：`combat_log` 在**远古（Fort）被摧毁之后仍继续记录 360~925s（中位 398s）**——那段是结算残留，
里面有 `gamestate` 条目，还有 **354 条守卫死亡**（30 场实测）。于是：

- 用 `MAX(t_cle) FROM combat_log` 当"比赛结束"是**错的**；旧开关 `--censor-at-end` 正是这么写的，
  导致右删失只标出 2.29%，而真实受影响的假眼是 **9.2~9.9%**（30 场 106/1153；5 场 20/202）。
- 权威取法：`type_category='death' AND target IN ('npc_dota_goodguys_fort','npc_dota_badguys_fort')` 的 `t_cle`
  （实测 970/970 场都有）→ 已落成 `analysis/q5_ward.py::game_end_cle()`，**唯一**入口。
- `game_events` 里**没有**远古死亡？有，但 `building_destroyed` 的最大值往往只是最后一座**塔/兵营**（不含远古），
  所以别用 `game_end_marker()` 当比赛结束（它只适合在同一 tick 空间内截断暂停块）。

**三个时钟的空间（此前没写清，踩过坑）**：

| 数据 | 空间 | 依据 |
|---|---|---|
| `combat_log.t_cle` | 游戏时钟（暂停冻结） | 权威叙事轴；`place`/`destroy`/窗口都用它 |
| `combat_log.t_tick` | 复播时钟（不停） | `t_tick ≥ t_cle` 恒成立 |
| `game_events.game_time_sec`、`entity_snapshots.game_time_sec` | **t_tick 空间** | 同一行 `ward_placed`：`game_time_sec=3672.0` ≈ `properties.t_tick=3672.97`，而 `properties.t_cle=3271.7` |
| `ward_placed.properties.t_cle` | **坏字段**（恒早 ~540s） | 见 C6.5，禁用 |

**推论**：任何"比赛结束/开始"的量都要先确认空间；`game_start()` 返回的是"0:00 那一刻的 `t_cle`"，
所以"显示时钟 = `t_cle − gs`"；`entity_snapshots`/`game_events` 的时间要用 `cle_for_tt()` 换算才能与 `t_cle` 相减。

**落地**：`parse_match(con, mid, censor_at_end=True)` 默认按权威结束做右删失（未到期却被比赛打断的眼：
`销毁 = 结束`、`存活 = 结束 − 放置`、`censored=True`）；`--no-censor-at-end` 退回旧口径（存活=寿命，是**上界**）。
Q5B（真眼）与 Q6（假眼）共用这一处修复。


---

# 附录 D：全量信息盘点 & 解析需求清单（给数据层，含决策 + 耗时基准）

> 用 `probe_inventory`（`.tmp/probe_inventory.txt`，全量类别+类计数+未知类字段）与 `probe_proxy_fields`（`.tmp/probe_proxy_fields.txt`，proxy/controller/team/data 全字段模板）对 **8817145578** 汇总；`dota_parse` 实测耗时见 §D4。凡"全部需要/不需要"均为**数据层已拍板**，后续按此清单实现，不要再论证。

## D1. 不做（无价值/与玩法无关 —— 已决策跳过）

| 消息 | 场次量 | 说明 |
|---|---|---|
| `SvcVoiceData` 926k / `SvcVoiceInit` / `UmSendAudio` / `UmVoiceMask` | ~927k | **解说语音**（非队内语音，是解说），不用 |
| `UmParticleManager` 187k | 187k | 粒子特效，不用 |
| `GeSosStartSoundEvent` + `SetParams` + `Stop`（`GeSos*`）| ~133k | 音效/音频，不用 |
| `SvcHltvStatus` 705 / `NetSpawnGroup*` 176 | — | 观察者流状态 / 地图内容流式加载，不用 |
| legacy game event `dota_*` | 938 | 几乎全是 `dota_chase_hero`(相机跟随) + `hltv_title/versioninfo`；**不属于玩法逻辑**，不用 |
| `GamerulesProxy.m_hTeamForts/Fountains/SecretShop1/2/SideShop1/2/Watchers/WisdomShrines/LotusPools` | — | 地图结构**句柄**（不用，非坐标/逻辑） |

## D2. 全部需要（新增解析）
### (a) combat-log 类型 —— 全部落库（不聚合、不去重）
`Damage 64.6k`、`ModifierStackEvent 31k`、`Heal 8.5k`、`Xp 6.7k`、`Playerstats 364`、`Buyback 8`、`Killstreak 27`、`Multikill 10`、`CriticalDamage 28`、`FirstBlood 1`、`TeamBuildingKill 23`（合计 **+~111k 事件/场**）。
- **Damage**：谁对谁造成多少伤害（`attacker_name/target_name/值/位置/类型`）——主战场。
- **ModifierStackEvent**：= **buff/debuff 的"逐 tick 生效/叠层"记录**。每条一条：`attacker`(施)、`target`(被挂)、`inflictor`=modifier 名、`stack_count`(层数)、`modifier_duration/elapsed`、`ability_level`、`a_team/t_team`。**每 tick 都发**（如 AA 的 Bone Chill 持续期内每 tick 重发、`stack_count` 恒 1 的"逐段生效"），**全部记录**；`stack_count` 会随真叠层递增。
- **Heal / Xp**：治疗/经验获得（`target`=受者、`value`=量、`reason`）。
- 落库：`event_type` 分别 `damage` / `modifier_stack` / `heal` / `xp` / `playerstats` / `buyback` / `killstreak` / `multikill` / `critical_damage` / `first_blood` / `team_building_kill`，字段含 `t_cle`+`t_tick`（保双钟）。

### (b) 信息型实体
| 实体 | 字段(要的) | 备注 |
|---|---|---|
| `CDOTAGamerulesProxy` | 时钟/暂停：`m_bGamePaused`、`m_nPauseStartTick`、`m_nTotalPausedTicks`、`m_nGameState`、`m_flGameStartTime`、`m_flPreGameStartTime`、`m_flStateTransitionTime`、`m_iNetTimeOfDay`、`m_bIsNightstalkerNight`、`m_flDaytimeStart/NighttimeStart`；塔/兵营摧毁：`m_arrTier1/2/3TowerDestroyed`、`m_arrMeleeBarracksDestroyed`、`m_bTier*TowerDestroyed`、`m_bMeleeBarracksDestroyed`；野怪盒：`m_NeutralSpawnBoxes.N{nCampType,vSpawnBoxOrigin,m_vMin/MaxBounds,strCampName}`；选人：`m_SelectedHeroes`、`m_BannedHeroes`、`m_iGameMode`、`m_nGameWinner`、`m_iActiveTeam`、`m_iStartingTeam` | **时钟/暂停=官方权威**（替代 hero 冻结法）；**不做**地图句柄 |
| `CDOTA_DataSpectator` | `m_vPossibleWardPlacement`、`m_vDesiredWardPlacement`、`m_flSuggestedWardWeights`、`m_nSuggestedWardIndexes`、`m_iSuggestedLanes`、`m_bSuggestedLaneJungle/Roam`、`m_fRadiantWinProbability`、`m_roshanSpawnInfo`、`m_nNextPowerRuneSpawnIndex/Type`、`m_vecNeutralItemsTierInfo` | **建议眼位/分路**；数据本身有点存疑，但解析量小 → 做 |
| `CDOTASpectatorGraphManagerProxy` | `m_rgRadiantWinChance`、`m_rgRadiant/DireNetWorth`、`m_rgRadiant/DireTotalEarnedGold/XP` | **逐分钟胜率**+经济，很重要 |
| `CDOTAPlayerController` | `m_nPlayerID`、`m_steamID`、`m_iszPlayerName`、`m_hAssignedHero`、`m_hPawn` | **玩家↔英雄句柄**，很重要（比 `m_iPlayerID=2×index` 更直接） |
| `CDOTAPlayerPawn` | `m_nPlayerID`、`m_iHealth/m_iMaxHealth`、`m_lifeState`、`m_flDeathTime`、`m_hController` | **精准死亡时刻**(`m_flDeathTime`)，很重要；**不逐秒采位置**(与 hero 重复) |
| `CDOTAGameManagerProxy` | `m_CurrentHeroAvailable.N` | 可用英雄池，需要 |
| `CDOTATeam` | `m_szTag`、`m_szTeamname`、`m_iScore`、`m_iTowerKills`、`m_iBarracksKills`、`m_iHeroKills`、`m_unTournamentTeamID` | 比分/击杀，需要 |
| 其它类（`CDOTAWearableItem` 6.4M、`CBaseEntity`、`CDOTAFogOfWarTempViewers`(视野) 等） | — | 可后续按需，不优先 |

## D3. 增幅与建议
- 数据行数：每场 现状 114k → 新 ≈ **258k**（快照 +~33k 信息型 1Hz、事件 +~111k raw combat）≈ **2.3×**，一个数量级内，可接受。
- **不要逐秒采 PlayerController/Pawn 位置**（与 hero 位置重复）；Pawn 只取 `m_flDeathTime`、Controller 只取 `m_hAssignedHero/m_hPawn/steamID`（关联用，低频）。
- `Damage`/`ModifierStackEvent` 若将来嫌大，可再考虑聚合；**当前按"全部 raw 落库"实现**。

## D4. 解析耗时基准（实测，`dota_parse` release）
- **98MB / 55min 匹配：parse 22.9s + verify，总计 23.5s**；≈ **7ms / 游戏秒**。
- 单场随对局时长：40min≈17s / 55min≈23.5s / 93min(8817145578)≈38s；平均 970 场 ≈ **18–20s**。
- 加全部新 extractor 后：**扫描不变**（tick 流+实体+combat 全扫），只多写行 → **单场 ~33–40s**。
- 全量 970：`parse_public.py` 用 ThreadPoolExecutor 子进程并行（`--workers` 默认 3）。现状 3 workers ≈ **1.7h**；加新后 8–12 workers ≈ **1–1.5h**。
- **建议**：把 `parse_public.py --workers` 提到 8–12（每进程单线程，纯进程级并行）。

## D5. 守卫眼位"根本解"（重新解析的核心设计 —— Q5 依据）

> 背景：Q5 眼位分析曾用"`ward_placed`(实体 tick) + segmap(tick→cle) + `entity_index` 配对 destroy"→ 暴露 **`entity_index` 复用（eidx=2876 两支共用）、segmap 外推 1s 误差、号角前被算成负数**。判定为"精修"，改为**全部重新解析**，并按下列机制重新设计。

**三个事实各自独立，没有一条能靠 `entity_index` 串起来：**
| 事实 | 来源 | 可靠身份 | 时间 | 位置 | 类型/队 |
|---|---|---|---|---|---|
| **插眼(use)** | combat `DotaCombatlogItem`(inflictor=item_ward_*) | `attacker(英雄)+inflictor(道具)` | **cle 精确** | 无 | 无(dispenser 分不出) |
| **眼实体(place)** | `on_entity Created` | `entity.index()`（**会复用**） | tick（1Hz 采样有量化） | **有**(cell→world) | **有**(TrueSight/Observer_Ward+team) |
| **销毁(destroy)** | combat `Death target=npc_*_wards` | 无 index | cle | 无 | 有 |

**三个死结**：① `entity_index` 复用；② use-cle 无 index/位置/队伍 → 无法直连实体；③ tick→cle 用 segmap 从 `ward_destroyed` 反推是近似。

**根本解（重新解析必须这么改，3 点）：**
1. **时钟映射用官方字段，杀掉 segmap**：读 `CDOTAGamerulesProxy` 的 `m_flGameStartTime`(号角偏移) + `m_nPauseStartTick`/`m_nTotalPausedTicks`(暂停跳变) + `m_nGameState` + `m_flPreGameStartTime`/`m_flStateTransitionTime` → **精确算任意 tick↔cle**，不再靠 `ward_destroyed` 反推 segmap；号角前负数、1s 插值误差同时消失。
2. **三个事实分别落库，不要硬合成一行"眼"**：`ward_use`(插眼cle+英雄)、`ward_place`(实体位置/类型/队+真实创建 tick，用 `on_entity Created` 去重后的首次，去 1Hz 量化)、`ward_destroy`(cle+过期/被反)。分析层按 `(队, 型, 时间最近邻+位置锚定)` 做健壮 join，**不以 index 为主键**。
3. **眼的稳定键 = `(队, 型, 出生位置, ~出生时刻)` 复合键**，不是 index（复用只是另一位置/时刻的支眼，天然区分）。

**给底层的结论**：重新解析时用 `GamerulesProxy` 官方时钟做精确 tick↔cle；把 `插眼cle / 实体位置与类型 / 销毁cle` 作为三个独立来源分别落库；分析层只用"队+型+时间+位置"做健壮关联，绝不以 `entity_index` 为主键。

### D5b. 【已落地 · 实测订正】三个来源落地后的真实接线方式（Q5 全量 970 场验证）
> 上面 D5 是**设计**；这里是**落地后实测**发现的接线细节（2026 订正，细节见 §C6）。凡与本节冲突，以本节为准。

| 来源 | 落地字段 | 分析层必须怎么用 |
|---|---|---|
| **插眼时刻** | `combat_log` type_category=`item` + `inflictor LIKE 'item_ward%'`（排除 `target` 非空的"给队友"） | 取该条 `t_cle`；**不能**用 `ward_placed.properties.t_cle`（该字段恒早 540.26s，坏字段） |
| **实体位置/类型/队** | `game_events.ward_placed`（`target_id`=实体类名, x/y, `properties.t_tick`/`team`）+ `entity_snapshots`（逐秒 x/y, 末现 `t1`） | 判型用 `target_id`/`ward_type`（=实体类名）；坐标用 x/y；**时间轴用 `t_tick`**（与 combat 同轴） |
| **销毁(过期/被反)** | `combat_log` type_category=`death` + `target LIKE '%ward%'`（`attacker==target`⇒过期） | **Death 无坐标** ⇒ 必须"一一对应"绑定到具体实体（`实体末现 − 9s`），**禁止**用"寿命窗内最早 death" |

**三条实测定律：**
1. **到期死亡时刻 = `出生t_tick + 420`（真眼）/ `+360`（假眼），精确到 0.0x s** —— 到期眼的销毁时刻直接用常量寿命算，不用 Death 事件定时（Death 只用于确认到期）。
2. **实体快照跨期 = 寿命 + 尾差**（1Hz 量化，主峰 ≈9s）—— `t1 − 9s ≈ 真实死亡时刻`，这是把 Death 绑到实体的唯一可用锚。
3. **判型互验（订正）**：计数法不成立（差额=右删失，非判型错）；改以**寿命机制**验证：到期眼 `(销毁−放置)` 精确 = 该型寿命（真眼 420 / 假眼 360，200 场 100% 零偏差，n=9,593/5,151）。另 `probe_truesight` 实测 combat log **不含**实体类名 → 判型只能来自实体表。

**订正后全量（970 场）**：真眼 70,421 条，`survival` max **420.5s**、`>420.5s` **0 条**（修复前 7203 条 >420s、最小 −2693s）。

---

## D6. 以 combat-log 为骨干的建表方案（重新解析 → 一张通用表替代散装 extractor）

> ✅ **已实现**（`dota_parse` 已按此重构并实测跑通，见 §D6.6）。

> 依据：游戏内 combat log（截图）就是这些事件的人读版，底部 toggle(Damage/Healing/Abilities/Items/Modifier/Deaths + Attacker/Target) 即**类型维度**。**combat log = 事件叙事(谁/何时/做了什么)；实体流 = 空间层(位置/类型)。** 因此重新解析应**以一张全类型通用 `combat_log` 表为主干**，实体流只用来给"缺坐标/缺类型/缺身份"的事件补空间。

### D6.1 建议建一张 `combat_log` 表（一行 = 一条 combat 条目）
| 列 | 类型 | 说明 |
|---|---|---|
| `match_id` | int | |
| `event_seq` | int | (match, t_cle, type_category, attacker, target) 序 |
| `t_cle` | float/real | `cle.timestamp()`（**游戏时钟，权威叙事轴**，暂停冻结） |
| `t_tick` | float/real | `ctx.tick()/30`（**回放时钟**，与实体/快照同轴） |
| `type_category` | text | **枚举 = toggle**：`damage/healing/ability/item/modifier/death/buyback/gold/xp/playerstats/gamestate/multikill/killstreak/critical/first_blood/team_building_kill/rune/...`（见 D2） |
| `type` | text | 原始 `DOTA_COMBATLOG_TYPES` 名 |
| `attacker` / `target` / `damage_source` / `inflictor` / `value_name` | text | 名称（`attacker`=使用者、`value_name`=购买 item 等） |
| `value` | int | 伤害值/Gold 金额/item index（**按 type 语义**，勿混用） |
| `health` | int | 前后血（damage 有） |
| `location_x` / `location_y` | real | **有则填**（Damage/Gold 常带；Item use/Death/Modifier 常 0） |
| `a_team` / `t_team` | int | 2/3（`t_team` 对 Death=死者自己队、对守卫=守卫队） |
| `stack_count` / `modifier_duration` / `modifier_elapsed` | int/float | Modifier 系列 |
| `ability_level` / `assist_players` / `gold_reason` / `xp_reason` / `event_location` | int | 各类型附带 |
| `is_target_building` / `is_attacker_hero` / `is_target_hero` / `hidden_modifier` | bool | 类型/归属判定 |
| `raw_json` | text | 整条原始字段(schema 变时不失) |

> `type_category` 就是游戏内 toggle 的落地：分析层任何"toggle 视图" = `WHERE type_category=?`；时间轴 = `ORDER BY t_cle`。

### D6.2 toggle → type_category 映射（对齐游戏内过滤）
- **Damage**: `DOTA_COMBATLOG_DAMAGE`、`MANA_DAMAGE`、`CRITICAL_DAMAGE`、`SPELL_ABSORB`、`PHYSICAL_DAMAGE_PREVENTED`、`ATTACK_EVADE`
- **Healing**: `HEAL`、`MANA_RESTORED`、`BOTTLE_HEAL_ALLY`
- **Abilities**: `ABILITY`、`ABILITY_TRIGGER`、`HERO_LEVELUP`、`INTERRUPT_CHANNEL`
- **Items**: `ITEM`、`PURCHASE`、`BUYBACK`、`NEUTRAL_ITEM_EARNED`
- **Modifiers**: `MODIFIER_ADD`、`MODIFIER_REMOVE`、`MODIFIER_STACK_EVENT`
- **Deaths**: `DEATH`、`KILLSTREAK`、`MULTIKILL`、`FIRST_BLOOD`、`TEAM_BUILDING_KILL`、`END_KILLSTREAK`
- 其它单列：`GOLD`、`XP`、`PLAYERSTATS`、`GAME_STATE`、`LOCATION`、`PICKUP_RUNE`、`REVEALED_INVISIBLE`、`SUCCESSFUL_SCAN`、`AEGIS_TAKEN`、`UNIT_SUMMONED`、`TREE_CUT`、`KILL_EATER_EVENT`、`NEUTRAL_CAMP_STACK` 等

### D6.3 空间富化（实体流补坐标/类型/身份）
- **守卫**：`item` + `inflictor=item_ward_*` 给"谁、何时"；实体 `CDOTA_NPC_Observer_Ward(_TrueSight)` 给"位置+类型"；`death` + `target=npc_*_wards` 给"销毁+过期/被反"。三者按 `(队,型,时间最近邻+位置)` join（D5）。
- **英雄/塔/野/建筑**：实体快照已有；`combat_log` 只提供"事件叙述"，坐标全从实体。
- **玩家身份**：`CDOTAPlayerController`/`Pawn` (`m_hAssignedHero/m_hPawn/steamID`) 关联。

### D6.4 相比现状的收益
- 一处全量落库，**取代**现在散装的 `purchase/gold/ward_*/ability_*/neutral_kill/game_state` 各一个 extractor（未来只需一个"combat 订阅"）。
- `type_category` 让任何"按 toggle 过滤/时间轴"分析直接可做，且与游戏内 UI 一一对应。
- 不再为每个 event 单独设计 `event_type`/`properties`，减少错配与"半精修"感。

### D6.5 已知边界（务必保留）
- **combat log 无实体句柄**（`CMsgDotaCombatLogEntry` 无 entity/index 字段）→ 与实体的关联只能靠 `(队, 型, 时间, 名称)`。
- **无坐标**：Item use / Death / Modifier 无 `location`；位置必须从实体。
- **`value` 语义按 type**：Damage=伤害值、Gold=金额、Purchase=item index、GameState=state 值。
- **双时钟**：`t_cle`(叙事轴) 与 `t_tick`(实体轴) 并存，跨表关联用 `t_tick` 或折算到"0:00 口径"（GamerulesProxy 时钟）。

### D6.6 实现状态（实测）
- **已重构**：`model.rs`(CombatLogRow) + `schema.rs`(combat_log DDL) + `parse.rs`(CombatLogExtractor 全类型采集 + type_category 映射 + 保留实体空间层) + `sqlite.rs`(insert_combat_log) + `main.rs`(写库/verify)。散装 combat extractor 已从 `game_events` 移除（`game_events` 只剩空间层的 `ward_placed` + `building_spawn/destroyed` + 号角锚点）。
- **实测 8946650558 → `dems/db_full/19719/8946650558.db`**：parse 55.2s；`combat_log`=63,569 行；`game_events`=145（ward_placed 91 + building 54）；`entity_snapshots`=82,754。
- **type_category 分布**：damage 25,945 / modifier 19,703 / xp 3,617 / death 3,547 / healing 3,488 / gold 2,985 / ability 2,097 / item 1,939 / playerstats 239 / gamestate 9。
- **守卫抽样**：Spirit Breaker 插眼 `cle=995.70`、号角锚点 `gamestate value=5 cle=943.50` → `0:52.2` ✓；`cle=859.83 target=npc_dota_hero_winter_wyvern` = **给队友（GIVE，排除）** ✓；真/假眼销毁 `death target=npc_*_wards`（`attacker==target`→过期、`a_team!=t_team`→被反）✓。
- **已知**（分析层处理）：entity_index 复用（ward:1315 两段）、`item_ward_dispenser` 判型需实体、"给队友"按 `target 非空/tgt_self=false` 排除。散装 extractor 的**旧 struct 仍保留为死代码**（已不再注册/产出），可后续整段删除。

---

## 文末约束
- 本文为**长期权威依据**；数据层修正解析器时以此为准，**不要再靠试错打补丁**。
- 探针产物：`dota_parse/src/bin/probe_time.rs`、`probe_dump.rs`、`probe_ward_use.rs`、`probe_sentry.rs`、`probe_item_fields.rs`、`probe_inventory.rs`、`probe_proxy_fields.rs`、`probe_modstack.rs`（保留可复用），`.tmp/probe_*_8817145578.txt`、`probe_inventory.txt`、`probe_proxy_fields.txt`（实证时序/逐行归类/守卫语义/全场盘点）。
- 与下载 agent 边界：下载只负责拿 `.dem`；本 agent 只负责搞懂 `.dem` 格式。
