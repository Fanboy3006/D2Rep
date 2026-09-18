# 守卫（眼位）事件重写 —— 最终确认说明（给数据层）

> 面向"重写 parser 的守卫插/反眼逻辑，且一次改对"的权威依据。所有结论均经 `probe_sentry` / `probe_ward_use` / `probe_ward_corr` / `probe_item_fields` 对 **8817145578** 实证，并与解析器落库逐条对账。配套知识库：`STRATEGY/DEM_FORMAT.md` 附录 C；证据文件见 `.tmp/probe_*.txt`。

---

## 0. 一句话结论
- **插眼** = `DotaCombatlogItem`，`attacker=英雄`、`inflictor=item_ward_sentry(真眼)/item_ward_observer(假眼)/item_ward_dispenser(眼药盒)`、`target=空`、带 `cle.timestamp()`(游戏时钟，精确)，**无坐标、无队伍、无 entity/index**。
- **反眼** = `DotaCombatlogDamage`/`Death`，`target=守卫自身(npc_dota_*_wards)`、`attacker_team/target_team` 区分敌我，带 `cle.timestamp()`；**同样无坐标**。
- **位置与类型只能从实体拿**（`CDOTA_NPC_Observer_Ward_TrueSight`=真眼 / `CDOTA_NPC_Observer_Ward`=假眼，`entity.index()` 为主键，`cell→world`）。
- **combat-log 无任何能连到具体实体的字段** → 插眼只能靠"**同队 + 同类型 + 时间顺序**"配对到实体拿坐标。

---

## 1. 插眼条目字段（决定性：无坐标/队伍/index）

`probe_item_fields` 对 `DotaCombatlogItem`(inflictor=item_ward_*) 逐字段打 [present?]：
```
Y  attacker_name  = npc_dota_hero_sand_king   ← 谁放的
Y  inflictor_name = item_ward_observer        ← 用的道具
Y  target_is_self = true
-  target_name / target_source_name / damage_source_name
-  value
-  location_x / location_y                     ← 无坐标
-  event_location
-  attacker_team / target_team                 ← 无队伍
```
> `CMsgDotaCombatLogEntry` 全量字段表（vendor dota.rs 2246-2350）中**没有任何 entity/handle/index 字段**。

## 2. 真眼实体（位置的唯一可靠来源）
- 真眼 = `CDOTA_NPC_Observer_Ward_TrueSight`；假眼 = `CDOTA_NPC_Observer_Ward`。
- 位置 = `world=(cell−128)*128+vec`；主键 = `entity.index()`（稳定、生命周期内不变）。
- 8817145578：真眼实体 **89** 个、假眼 **52** 个。

## 3. 反眼条目字段（也无坐标，`target_team`=守卫自己队伍）
```
type=DotaCombatlogDeath target=npc_dota_sentry_wards
Y target_name=npc_dota_sentry_wards  value=50(伤害数)  event_location=3(枚举非坐标)
Y attacker_name/team(敌方)  target_team=2(被反眼的队)
- location_x / location_y
```
- **自过期**：`attacker==target`（都是 `npc_dota_*_wards`）。**被反/被摧毁**：`attacker`=敌方英雄/兵 且 `attacker_team != target_team`。
- 8817145578：真眼销毁 94（过期 73 / 被反 21）；假眼 54（过期 29 / 被反 25）。
- ⚠️`value` 对 Damage 是**伤害数**，对 Purchase 才是 item index——**别混用**。`value_name` 上可能是技能/装备名，**不要用它判守卫类型**，用 `target_name`。

## 4. 配对方案（已被进一步判定为"精修"，见 §4b 根本解）
~~`use(cle)` 与 `Created(tick)` 各自单调顺序一致 → "同队+同类型+时间顺序最近邻"贪心配对~~ —— 实测仍不可靠（use 顺序错配、`entity_index` 复用、孤儿 use），**已废弃**，改为 §4b。

## 4b. 根本解（Q5 依据——重新解析按此设计）
> 三事实（插眼cle / 实体位置·类型 / 销毁cle）**各自独立，无一条能靠 `entity_index` 串联**（index 复用、use 无 index/位置/队伍、tick→cle 需官方时钟）。
1. **时钟用 `CDOTAGamerulesProxy` 官方字段**（`m_flGameStartTime`/`m_nPauseStartTick`/`m_nTotalPausedTicks`/`m_nGameState`/`m_flPreGameStartTime`/`m_flStateTransitionTime`）精确算 tick↔cle，**杀掉 segmap**（消除 1s 误差 + 号角前负数）。
2. **三事实分别落库**：`ward_use`(cle+英雄)、`ward_place`(实体位置/类型/队+真实创建tick，`on_entity Created` 去重后的首次)、`ward_destroy`(cle+过期/被反)。分析层按 `(队, 型, 时间最近邻+位置锚定)` 健壮 join，**不以 index 为主键**。
3. **眼稳定键 = `(队, 型, 出生位置, ~出生时刻)` 复合键**（复用是另一位置/时刻的支眼，天然区分）。

## 5. 三个必须处理的"破坏对账"坑
1. **孤儿 use**：8817145578 有 **36 条** `item_ward_*` 在 ±2s 内无实体出生（"用了眼但没落地"/错位）。
2. **重复 Created**：`on_entity(Created)` 重复触发 → `ward_placed` 真眼 94 行/89 独立、假眼 54/53，且 **`entity_index` 会被复用**（eidx=2876 两支共用）。**不能按 index 聚合；用"首次位置+时刻"复合键。**
3. **dispenser 分不清真/假眼**：`inflictor=item_ward_dispenser`(66) 覆盖真眼 39 + 假眼 27；只有 50 真眼用 `item_ward_sentry`、25 假眼用 `item_ward_observer`。**判型必须用实体。**

## 6. 给数据层落库/改 parser 的具体动作
- **位置 + 类型**：一律实体（逐秒跟踪 `CDOTA_NPC_Observer_Ward` / `..._TrueSight`，首个非零坐标，`entity.index()` 主键）。
- **谁、何时放**：`DotaCombatlogItem`(attacker=英雄, inflictor=item_ward_*, timestamp=cle)。**`ward_use` 目前抓 `obs_wards_placed` 是错的（恒 0）**，应改抓 `item_ward_*` 的 Item 条目。
- **判反眼**：`Death/Damage target=npc_dota_*_wards` + `attacker==target`→过期 / `a_team!=t_team`→被反；坐标用实体。
- **双时钟**：快照/实体用**回放时钟** `tick/30`；combat 的 placed/destroyed 用**游戏时钟** `cle.timestamp()`。统一到"0:00 显示口径"（`cle − game_start_cl`）或用 `t_tick` 桥接（见 DEM_FORMAT §2.5、A1-A5）。

## 7. 快速核对点（SQL，8817145578）
- 真眼实体 89：`SELECT COUNT(DISTINCT entity_id) FROM entity_snapshots WHERE entity_type='ward' AND json_extract(extra,'$.ward_type')='sentry';`
- 真眼 placed 94 行 / 89 独立：`SELECT COUNT(*), COUNT(DISTINCT json_extract(properties,'$.entity_index')) FROM game_events WHERE event_type='ward_placed' AND json_extract(properties,'$.ward_type')='sentry';`
- 真眼 destroyed 94（expired 73 / dewarded 21）、假眼 54（29/25）。
- `ward_use` = 0（死代码，改了之后应>0 且≈放置实体数）。

---
依据：`STRATEGY/DEM_FORMAT.md`（§2 时间系统、附录 A、附录 C）；探针 `probe_sentry`/`probe_ward_use`/`probe_ward_corr`/`probe_item_fields`。
