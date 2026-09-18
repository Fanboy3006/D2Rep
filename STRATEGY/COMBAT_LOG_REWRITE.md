# 交接任务：重建 parser 为「combat_log 通用表 + 实体空间富化」架构（一次性完整实现）

> **给底层机制 agent 的执行任务。** 完整、自包含。请按此一次实现到位（新建 combat_log 表 + 删除散装 extractor + 独立库），不要试错打补丁。
> 依据：`STRATEGY/DEM_FORMAT.md` **附录 D**（尤其 D6）、`STRATEGY/DEM_WARD_REWRITE_NOTES.md`、`analysis/Q5_RULES_CALIBRATION_LOG.md`（§7/§12/§13）。

---

## 0. 一句话目标

把现在 parser 里"东一榔头西一棒子"的散装 extractor（purchase / gold / ward_* / ability_* / neutral_kill / building / game_state …各自一个 `event_type`），**替换成一张全类型通用的 `combat_log` 表**：一行 = 一条 combat 条目，带 `type_category` 枚举（= 游戏内 toggle），全类型全量、不聚合、不去重。实体流（entity_snapshots）只做空间富化（位置/类型）。

**combat log = 事件叙事（who/what/when）；实体流 = 空间层（where/位置/类型）。二者按 (队, 型, 时间最近邻 + 位置) 关联。**

---

## 1. 背景与动机（为什么）

- 游戏内 combat log（人读版）就是全部事件数据，底部 toggle（Damage/Healing/Abilities/Items/Modifier/Deaths + Attacker/Target）= **类型维度过滤**。开头 `[00:40.33]` 就是 `cle.timestamp()`（游戏时钟，暂停冻结）。
- 现在 parser 为每个事件单独写 extractor + `event_type` + `properties`（`purchase`/`gold`/`ward_placed`/`ward_destroyed`/`ward_use`/`ability_cd_start`/`neutral_kill`/`building_spawn`/`game_state`…），互相零散、易错配、难维护，且半途精修（segmap/顺序配对都源于此）。
- **根解法**：把 combat 流一次性全量落成一个通用表，类型只作为一个字段；散装 extractor 全删——这正是"重新解析且一次改对"的方向。

---

## 2. 必须承认的边界（combat log 的先天局限）

- **无实体句柄**：`CMsgDotaCombatLogEntry` 字段表里**没有任何 entity/handle/index 字段**（vendor dota.rs 2226-2350 已核）。→ 与实体的关联只能靠 `(队, 型, 时间, 名称)`。
- **无坐标**：`DotaCombatlogItem`(插眼 use) 的 `location_x/y=(0,0)`；`Death` 无坐标；`Modifier` 无坐标。**位置只能从实体拿。**
- **`value` 语义按 type**：Damage=伤害值、Gold=金额、Purchase=item index、GameState=state 值。**勿混用。**
- **双时钟**：`t_cle`(叙事轴，游戏时钟，暂停冻结) 与 `t_tick`(实体轴，回放时钟)` 并存。跨表关联用 `t_tick` 或折算到"0:00 口径"（GamerulesProxy 官方时钟）。

---

## 3. 新表 schema：`combat_log`

新增**独立表** `combat_log`（区别于现有 `game_events`）：

```sql
CREATE TABLE IF NOT EXISTS combat_log (
  match_id     INTEGER NOT NULL,
  event_seq    INTEGER NOT NULL,        -- (match, t_cle, type_category, attacker, target) 内序
  t_cle        REAL    NOT NULL,        -- cle.timestamp() 游戏时钟(叙事轴,暂停冻结)
  t_tick       REAL    NOT NULL,        -- ctx.tick()/30 回放时钟(实体轴)
  type_category TEXT   NOT NULL,        -- 枚举=toggle (见 §4)
  type         TEXT   NOT NULL,         -- 原始 DOTA_COMBATLOG_TYPES 名 (e.g. "DOTA_COMBATLOG_DAMAGE")
  attacker     TEXT,                    -- attacker_name
  target       TEXT,                    -- target_name
  damage_source TEXT,                   -- damage_source_name
  inflictor    TEXT,                    -- inflictor_name
  value_name   TEXT,                    -- value_name
  value        INTEGER,                 -- 按 type 语义(damage伤害/gold金额/item index/gamestate值)
  health       INTEGER,                 -- 前后血(damage 有)
  location_x   REAL,                    -- 有则填(Damage/Gold 常带; Item use/Death/Modifier 常0)
  location_y   REAL,
  a_team       INTEGER,                 -- 2/3
  t_team       INTEGER,                 -- 2/3 (Death的t_team=死者自己队; 守卫=守卫自己队)
  stack_count  INTEGER,                 -- Modifier stack
  modifier_duration REAL,               -- Modifier
  modifier_elapsed REAL,                -- Modifier
  ability_level INTEGER,                -- Ability
  assist_players TEXT,                  -- 击杀助攻列表(json数组字符串)
  gold_reason  INTEGER,                 -- Gold
  xp_reason    INTEGER,                 -- Xp
  event_location INTEGER,               -- 有则填(枚举,非坐标)
  is_attacker_hero INTEGER,             -- bool
  is_target_hero INTEGER,               -- bool
  is_target_building INTEGER,           -- bool
  raw_json     TEXT                     -- 整条原始序列化(字段演进而schema不变时兜底)
);
CREATE INDEX IF NOT EXISTS idx_combat_log ON combat_log(match_id, t_cle);
CREATE INDEX IF NOT EXISTS idx_combat_log_cat ON combat_log(match_id, type_category);
```

> 落库位置：**独立目录** `dems/db_full/<league>/<match>.db`，**不覆盖** `dems/db/`（Q5 版）。现有 Q5 管线在 `dems/db/` 保留不动。

---

## 4. type_category 枚举（= 游戏内 toggle）

从 `DOTA_COMBATLOG_TYPES` 映射（对齐游戏内过滤）：

| toggle | type_category | 覆盖的原始 DOTA_COMBATLOG_TYPES |
|---|---|---|
| Damage | `damage` | `DOTA_COMBATLOG_DAMAGE` `MANA_DAMAGE` `CRITICAL_DAMAGE` `SPELL_ABSORB` `PHYSICAL_DAMAGE_PREVENTED` `ATTACK_EVADE` |
| Healing | `healing` | `HEAL` `MANA_RESTORED` `BOTTLE_HEAL_ALLY` |
| Abilities | `ability` | `ABILITY` `ABILITY_TRIGGER` `HERO_LEVELUP` `INTERRUPT_CHANNEL` |
| Items | `item` | `ITEM` `PURCHASE` `BUYBACK` `NEUTRAL_ITEM_EARNED` |
| Modifiers | `modifier` | `MODIFIER_ADD` `MODIFIER_REMOVE` `MODIFIER_STACK_EVENT` |
| Deaths | `death` | `DEATH` `KILLSTREAK` `MULTIKILL` `FIRST_BLOOD` `TEAM_BUILDING_KILL` `END_KILLSTREAK` |
| — | 单列其它 | `GOLD` `XP` `PLAYERSTATS` `GAME_STATE` `LOCATION` `PICKUP_RUNE` `REVEALED_INVISIBLE` `SUCCESSFUL_SCAN` `AEGIS_TAKEN` `UNIT_SUMMONED` `TREE_CUT` `KILL_EATER_EVENT` `NEUTRAL_CAMP_STACK` |

> 实现：每个 `DOTA_COMBATLOG_TYPES` 变体 → `type_category`；未列出的归 `other`，但**不丢弃**（`raw_json` 兜底）。

---

## 5. parser 改动（parse.rs / main.rs / model.rs / sqlite.rs）

### 5.1 新增 `CombatLogExtractor`（一个订阅全类型）
- 用 `#[observer]` + `#[on_combat_log]`，**对每条** combat 条目收集到 `Vec<CombatLogRow>`。
- 字段采集（复用现有 `cle.*()` API + `cle.log()` 原始 protobuf 的 `is_some` 判断）：
  - `cle.r#type()` → `type`；`cle.timestamp()` → `t_cle`；`ctx.tick()/30` → `t_tick`。
  - `cle.attacker_name/target_name/damage_source_name/inflictor_name/value_name`。
  - `cle.value / location_x / location_y / attacker_team / target_team / stack_count / modifier_duration / ability_level / assist_players / gold_reason / xp_reason / event_location / is_attacker_hero / is_target_hero / is_target_building`（`cle.log().xxx.is_some()` 判断 present，缺省 None）。
  - 判 `type_category`（§4 映射）。
- **不聚合、不去重**，每条约一条。

### 5.2 新增 `CombatLogRow`（model.rs）
`pub struct CombatLogRow` 对应 §3 表列。加到 `ParsedReplay` 的 `combat_rows: Vec<CombatLogRow>`。

### 5.3 `parse_replay`（parse.rs）
- 注册 `let combat = parser.register_observer::<CombatLogExtractor>();`
- 组装：`let combat_rows = combat.borrow().rows.clone();` 并入 `ParsedReplay.combat_rows`。
- **删除**散装 extractor 的注册 + assemble：
  - `purchase`(PurchaseExtractor)、`ability`(AbilityExtractor)、`jungle`(JungleExtractor 的 gold/neutral)、`ward`(WardExtractor 的 ward_use/destroyed —— 但 ward_placed 实体事件保留)。
  - 相应删 `build_event_rows`(purchase)、`build_ability_event_rows`、`build_jungle_event_rows`。
  - **保留**：`building`(BuildingExtractor)、`ward`(WardExtractor 的**实体** placed/snapshot，空间层)、`position`、`networth`。
- `game_state`(号角) 事件**保留**（作为 0:00 锚点，GamerulesProxy 采集保留在 WardExtractor / 或独立）。

### 5.4 落库（sqlite.rs）
- 新增 `PreparedWriter::insert_combat_log(match_id, row)` + `Db` 层。
- main.rs `run()`：`for row in &p.combat_rows { writer.insert_combat_log(...)?; }`。
- schema.rs：加入 `combat_log` 建表语句到 `SCHEMA_SQL`。
- `verify_from_db` 加 combat_log 计数 + 按 type_category 分组统计。

### 5.5 删除散装（一次性）
- 从 `SCHEMA_SQL`、`verify_from_db`、model/parse 里**移除** `purchase/gold/ward_use/ward_destroyed(散装部分)/ability_*/neutral_kill/equipment` 这些 `game_events` 的散装 `event_type` 分支（原来是 combat 流的）。
- ⚠️ 保留 `entity_snapshots` 里的 ward/hero/building/networth（空间层仍是实体流）。

> **重要**：现有 `game_events` 表**保留**（部分非 combat 事件，如 building_spawn/destroyed、净价值、实体事件），但**不再**承载 combat 叙事。combat 叙事全部进 `combat_log`。

---

## 6. 守卫（眼位）在 combat_log 下的处理

**三来源独立（各自真实），分析层按 (队, 型, 时间最近邻 + 位置) join，绝不以 entity_index 为主键：**

1. **插眼 cle**：`type_category='item'` 且 `inflictor=item_ward_sentry/observer/dispenser`。
   - ⚠️ **排除"给队友"**：`target_name` 非空 或 `target_is_self=false`（如 `uses Sentry Ward on Winter Wyvern`）= 递给队友，不是插眼，排除（已在第 7 节验证）。
   - ⚠️ **dispenser**(`item_ward_dispenser`) 一次可能产真眼或假眼，自身不带类型 → **必须用实体 Created 判型**（见②）。
   - `attacker`(插眼英雄) → 队伍经 `player_identity.hero_name→team_id` 查（combat-log 无 a_team）。
2. **实体位置与类型**：实体 `CDOTA_NPC_Observer_Ward`(observer) / `..._TrueSight`(sentry) 首现位置 + 队 + 真实创建 `t_tick`（`entity.index()` 会复用，**仅作候选，不作主键**）。
3. **销毁 cle**：`type_category='death'` 且 `target=npc_dota_sentry_wards/observer_wards`。
   - 过期：`attacker==target`（都是 `npc_dota_*_wards`）；被反：`a_team != t_team`。

**眼稳定键 = `(队, 型, 出生位置, ~出生时刻)` 复合键**（复用只是另一位置/时刻的支眼，天然区分）。

---

## 7. 已验证的关键事实（直接用，勿推翻）

- **`[格式]` 时间 = `cle.timestamp()`（游戏时钟，号角 0:00 起，暂停冻结）**。`base=943.5`(8946650558) 已实证：Spirit Breaker 插眼 `cle=995.7` → 显示 `0:52.2` ✓。
- **插眼 vs 给队友** 区分：`cle=859.8 att=spirit_breaker tgt='npc_dota_hero_winter_wyvern' tgt_self=false` = **给队友**（排除）；`cle=995.7 tgt='' tgt_self=true` = **插眼**。
- **GamerulesProxy 官方时钟**（精确 tick↔cle）：`m_pGameRules.m_flGameStartTime`(号角基值)、`m_nTotalPausedTicks`/`m_nPauseStartTick`/`m_bGamePaused`(暂停)、`m_nGameState`(=5 号角)。8946650558 号角 `m_flGameStartTime=943.5`，**全程无暂停**。
- **`ward_placed`(实体) 现在可带 `t_cle`**(从 GamerulesProxy cum_paused 算出)，供实体与 combat-log 同域对应。
- **entity_index 会复用**（eidx=2876 两支眼共用，跨队/位置）→ 不能作主键。

---

## 8. 验证清单（改完一场，8946650558.dem）

- `SELECT type_category, COUNT(*) FROM combat_log GROUP BY type_category;` → 8 类 + 单列，总量 ~258k/场。
- `SELECT COUNT(*) FROM combat_log WHERE type_category='item' AND inflictor='item_ward_sentry';` → 插眼数;其中 `target_name` 为空的 = 真插眼。
- 守卫插眼 cle vs 实体位置：Spirit Breaker `[0:52.2]` 插眼应能锚到正确实体位置。
- 号角前插眼（cle<号角基值）：显示应为负值（如 -1:23 给队友已被排除）。
- `ward_destroyed`(死亡, target=npc_*_wards) 的 t_cle 与实体消失时刻（t_tick 折算）吻合。

---

## 9. 交付要求

1. 实现上述 schema / model / extractor / 落库 / 散装删除。
2. 重新解析 **`dems/public/19719/8946650558.dem`** → 写入 **`dems/db_full/19719/8946650558.db`**（新目录，不覆盖 Q5 版）。
3. 跑 §8 验证，贴出各 type_category 计数 + 守卫插眼/销毁的抽样。
4. 编译必须过（`cargo build --release --bin dota_parse --offline`，工具链 PATH + `DOTA_PARSE_SQLITE_DLL` 已配）。
5. 更新 `STRATEGY/DEM_FORMAT.md` 附录 D 反映"已实现 combat_log 表"。

---

## 10. 参考（本轮已探索，可复用）

- probe：`probe_gamerules_clock.rs`(GamerulesProxy 时钟)、`probe_item_target.rs`(插眼 vs 给队友)、`probe_sentry_full.rs`(守卫 PLACE/DESTROY)、`probe_dispenser.rs`(item_ward_dispenser 67 条)。
- `analysis/Q5_RULES_CALIBRATION_LOG.md` §13（GamerulesProxy 实测）、§12（根本解方案）。
- `STRATEGY/DEM_WARD_REWRITE_NOTES.md` §4b（根本解三来源）、§5（三个破坏对账的坑：孤儿use/重复Created/index复用/dispenser判型）。

**再也不做**：segmap（ward_destroyed 交点插值）、use-cle 顺序配对（贪心）、以 entity_index 为主键。
