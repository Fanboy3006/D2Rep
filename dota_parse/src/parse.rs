//! Replay parsing: header identity extraction + the initial extractors.
//!
//! Design (ARCHITECTURE.md §6.4): parsing the .dem with source2-demo runs the
//! whole tick stream once. Each extractor subscribes to what it cares about
//! and emits generic rows:
//!
//! * hero position extractor  -> `entity_snapshots` rows (1 s resampling)
//! * purchase extractor       -> `game_events` rows (event_type 'purchase')
//! * replay header extractor  -> `player_identity` rows
//!
//! A future extractor (wards, kills, ability casts, ...) only adds another
//! observer here plus a query on layer 4 — no schema change, no change to the
//! other extractors.
//!
//! Verified facts reused from ARCHITECTURE.md §6.6:
//! * world = (cell as i32 - 128) * 128 + vec
//! * `m_iPlayerID` on hero entities = 2 × header player index (0..18)
//! * hero npc ↔ class name conversion is prefix + snake_case
//! * purchase buyers appear in the combat log's target field
//! * `on_tick_start` fires before the current tick's entity deltas, i.e. it
//!   reads the previous tick's state — negligible at 1 s resampling.

use source2_demo::prelude::*;
use source2_demo::proto::DotaCombatlogTypes;
use std::collections::{BTreeMap, HashMap, HashSet};

use crate::model::{
    self, hero_class_to_npc, team_text, CombatLogRow, EventRow, PlayerIdentityRow, SnapshotRow,
    DIRE_SLOT_BASE,
};

pub const TICK_RATE: u32 = 30; // Dota 2 simulation ticks per second
pub const INVALID_PLAYER_ID: u32 = u32::MAX;
const CELL_ORIGIN: i32 = 128; // unsigned cell value of the world centre
const CELL_SIZE: i32 = 128;

fn cell_to_world(cell: Option<u32>, vec: Option<f32>) -> Option<f64> {
    match (cell, vec) {
        (Some(c), Some(v)) => Some(f64::from(c as i32 - CELL_ORIGIN) * f64::from(CELL_SIZE) + f64::from(v)),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// replay header -> player identities
// ---------------------------------------------------------------------------

/// A player described by the replay header (`CGameInfo.CDotaGameInfo`).
#[derive(Debug, Clone)]
pub struct HeaderPlayer {
    /// Header order index (0..=9). `m_iPlayerID` on the hero entity is 2×this.
    pub header_index: usize,
    /// Dota player slot convention: radiant 0..=4, dire 128..=132.
    pub player_slot: i64,
    pub steam_id: Option<u64>,
    pub player_name: String,
    /// Canonical hero npc name, e.g. "npc_dota_hero_legion_commander".
    pub hero_npc: Option<String>,
    /// Game team code: 2 = radiant, 3 = dire.
    pub team_code: Option<i32>,
    pub is_fake_client: bool,
}

/// Parse the replay header into one identity entry per player.
fn header_players(parser: &Parser) -> Vec<HeaderPlayer> {
    let mut out = Vec::new();
    let info = parser.replay_info();
    let Some(dota) = info
        .game_info
        .as_ref()
        .and_then(|g| g.dota.as_ref())
    else {
        return out;
    };
    let mut radiant_rank = 0usize;
    let mut dire_rank = 0usize;
    for (i, p) in dota.player_info.iter().enumerate() {
        let team_code = p.game_team;
        let player_slot = match team_code {
            Some(model::TEAM_CODE_RADIANT) => {
                let s = radiant_rank;
                radiant_rank += 1;
                s as i64
            }
            Some(model::TEAM_CODE_DIRE) => {
                let s = dire_rank;
                dire_rank += 1;
                DIRE_SLOT_BASE + s as i64
            }
            _ => i as i64, // unusual entry (observer / no team): keep order index
        };
        let player_name = p
            .player_name
            .as_ref()
            .map(|b| String::from_utf8_lossy(b.as_slice()).into_owned())
            .unwrap_or_default();
        out.push(HeaderPlayer {
            header_index: i,
            player_slot,
            steam_id: p.steamid,
            player_name,
            hero_npc: p.hero_name.clone(),
            team_code,
            is_fake_client: p.is_fake_client.unwrap_or(false),
        });
    }
    out
}

/// Header-only read: header match id, playback duration and player identities,
/// WITHOUT running the tick stream. Used by `dota_parse --info` so the
/// scheduler layer (ARCHITECTURE.md §8 step 7) can register a replay before
/// (or instead of) a full parse. Shares the §6.6-verified player_info decode.
#[derive(Debug, Clone)]
pub struct HeaderInfo {
    /// Match id carried by the replay header (`CGameInfo.match_id`), if any.
    /// Absent or 0 means the file has no usable official id (private/custom
    /// recordings) — callers fall back to a content hash id.
    pub match_id: Option<i64>,
    /// `playback_time` from the header, in seconds.
    pub duration_seconds: Option<f64>,
    pub players: Vec<HeaderPlayer>,
}

/// Parse only the replay header (fast, no extractors run).
pub fn parse_header(bytes: &[u8]) -> anyhow::Result<HeaderInfo> {
    let parser = Parser::new(bytes)?;
    let info = parser.replay_info().clone();
    let header_match_id = info
        .game_info
        .as_ref()
        .and_then(|g| g.dota.as_ref())
        .and_then(|d| d.match_id)
        .map(|v| v as i64);
    Ok(HeaderInfo {
        match_id: header_match_id.filter(|&v| v > 0),
        duration_seconds: info.playback_time.map(f64::from),
        players: header_players(&parser),
    })
}

// ---------------------------------------------------------------------------
// extractors
// ---------------------------------------------------------------------------

/// One position sample of a hero entity.
#[derive(Debug, Clone)]
struct HeroSample {
    t: i64,
    x: f64,
    y: f64,
    z: Option<f64>,
    hp: Option<i64>,
    hp_max: Option<i64>,
    mana: Option<f64>,
    mana_max: Option<f64>,
    pid: u32,
}

/// Position extractor: emits one `HeroSample` per hero per whole second.
///
/// The demo tick stream occasionally repeats a tick/second (observed on the
/// verified replay), so samples are stored in a per-second map: the latest
/// occurrence of a second wins, matching the snapshot-table primary key
/// `(match_id, entity_id, game_time_sec)` = one state per entity per second.
///
/// Sampling gate: sample once per whole second (the first tick whose
/// `tick / TICK_RATE` second differs from the previous call), instead of a
/// `tick % TICK_RATE == 0` modulo. Dota replay files interleave phases that
/// only deliver even- or odd-numbered ticks (measured on league replays), so a
/// modulo-30 gate silently starves whole stretches of a game — hundreds of
/// matches lost all early/mid-game positions before this fix. The
/// second-changed gate fires exactly once per second regardless of parity.
struct PositionExtractor {
    /// hero entity class name -> (whole second -> latest sample that second)
    samples: HashMap<String, BTreeMap<i64, HeroSample>>,
    /// whole second sampled on the previous `on_tick_start`, for the gate.
    last_sec: i64,
}

impl Default for PositionExtractor {
    fn default() -> Self {
        PositionExtractor {
            samples: HashMap::new(),
            last_sec: i64::MIN,
        }
    }
}

#[observer]
#[uses_all]
impl PositionExtractor {
    #[on_tick_start]
    fn on_tick_start(&mut self, ctx: &Context) -> ObserverResult {
        let tick = ctx.tick();
        let t = i64::from(tick / TICK_RATE); // whole seconds since match start
        if t == self.last_sec {
            return Ok(()); // already sampled this whole second
        }
        self.last_sec = t;
        for entity in ctx.entities().iter() {
            if !entity.class().name().starts_with("CDOTA_Unit_Hero_") {
                continue;
            }
            let class = entity.class().name().to_string();
            let pid = try_property!(entity, u32, "m_iPlayerID").unwrap_or(INVALID_PLAYER_ID);
            let x = cell_to_world(
                try_property!(entity, u32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_cellX"),
                try_property!(entity, f32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_vecX"),
            );
            let y = cell_to_world(
                try_property!(entity, u32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_cellY"),
                try_property!(entity, f32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_vecY"),
            );
            let z = cell_to_world(
                try_property!(entity, u32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_cellZ"),
                try_property!(entity, f32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_vecZ"),
            );
            let (Some(x), Some(y)) = (x, y) else {
                continue;
            };
            let hp = try_property!(entity, i32, "m_iHealth").map(i64::from);
            let hp_max = try_property!(entity, i32, "m_iMaxHealth").map(i64::from);
            let mana = try_property!(entity, f32, "m_flMana").map(f64::from);
            let mana_max = try_property!(entity, f32, "m_flMaxMana").map(f64::from);
            self.samples
                .entry(class)
                .or_default()
                .insert(t, HeroSample {
                    t,
                    x,
                    y,
                    z,
                    hp,
                    hp_max,
                    mana,
                    mana_max,
                    pid,
                });
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// ward extractor (视野/守卫事件)
// ---------------------------------------------------------------------------

/// Ward (observer / sentry) sight events, written to the generic
/// `game_events` table as `ward_placed` / `ward_destroyed`.
///
/// Sources, both verified empirically on 2026 demo builds:
/// * placed: a ward appears as a unit entity -> `Created` event. The two unit
///   classes are `CDOTA_NPC_Observer_Ward` (plain vision observer) and
///   `CDOTA_NPC_Observer_Ward_TrueSight` (sentry, true-sight). Entity class
///   names are *not* semantically trustworthy in general — this mapping was
///   cross-calibrated: created counts match combat-log death counts per type
///   (44 vs 43 observer / 79 vs 74 sentry on the calibration replay). See
///   ARCHITECTURE §6.6 if counts ever stop matching.
/// * destroyed: combat log `Death` whose target unit is
///   `npc_dota_observer_wards` / `npc_dota_sentry_wards` (authoritative unit
///   npc). attacker == target means the ward expired on its own; otherwise the
///   attacker (hero or tower) destroyed it. Death entries carry no position.
#[derive(Default)]
struct WardExtractor {
    placed: Vec<WardPlaced>,
    destroyed: Vec<WardDestroyed>,
    /// per whole second -> (index -> latest sample that second). Each ward unit
    /// is a live entity with its own stable index; this lets us track every
    /// ward's true position and lifetime without LIFO pairing.
    samples: HashMap<i64, HashMap<u32, WardSample>>,
    /// whole second sampled on the previous on_tick_start, for the gate.
    last_sec: i64,
    /// indices present in the previous sampled whole second (disappearance detection)
    last_sec_present: Option<Vec<u32>>,
    /// index -> (last_seen_t, x, y) at the moment before disappearance
    last_seen: HashMap<u32, (i64, f64, f64)>,
    /// index -> ward type string
    idx_class: HashMap<u32, &'static str>,
    /// index -> npc name
    idx_npc: HashMap<u32, String>,
    /// index -> team code
    idx_team: HashMap<u32, Option<i32>>,
    /// GAME_IN_PROGRESS (DotaCombatlogGameState val=5) 的 cle.timestamp == 游戏时钟 0:00 锚点。
    game_start_cl: Option<f64>,
    /// 放置假眼(Observer Ward)事件: cle.timestamp + attacker + 英雄位置。
    /// 来自 combat-log 的 obs_wards_placed > 0(玩家插假眼)。
    ward_use: Vec<WardUse>,
    /// GamerulesProxy 官方时钟(精确 tick↔cle)。
    gst_base: f64,           // m_pGameRules.m_flGameStartTime (号角基值, cle 口径)
    cum_paused_ticks: f64,   // m_pGameRules.m_nTotalPausedTicks (累计暂停 tick 数)
    game_paused: bool,       // m_pGameRules.m_bGamePaused
}

#[derive(Debug, Clone)]
struct WardUse {
    t: f64,          // cle.timestamp (游戏时钟)
    actor: Option<String>,
    ward_type: &'static str,  // "sentry" | "observer"
    attacker_team: Option<i32>,  // 插眼者队伍 (2/3)
}

#[derive(Debug, Clone, Copy)]
struct WardSample {
    t: i64,
    x: f64,
    y: f64,
    team: Option<i32>,
    class: &'static str,
}

#[derive(Debug, Clone)]
struct WardPlaced {
    t: f64,
    /// 精确游戏时钟(cle) = tick_sec − (cum_paused_ticks/30)。从 GamerulesProxy 官方时钟算出。
    cle: f64,
    ward_type: &'static str,
    class: String,
    x: f64,
    y: f64,
    team_code: Option<i32>,
    /// stable per-ward entity index (primary key across samples) — lets Q5
    /// associate place/destroy/position without any time-based pairing.
    entity_index: u32,
}

#[derive(Debug, Clone)]
struct WardDestroyed {
    /// combat-log timestamp (game clock, from 0:00, differs from raw by per-match
    /// pre-game/pause offset) — keep for reference.
    t: f64,
    /// raw world second (tick/TICK_RATE), same clock as placed/entity snapshots.
    /// Authoritative for Q5 destroy timing so deward/expire can be bound to the
    /// exact ward entity without two-clock confusion.
    t_tick: Option<f64>,
    ward_type: &'static str,
    unit: String,
    actor: Option<String>,
    self_expired: bool,
    team_code: Option<i32>,          // target_team = 被反眼自己的队 (2/3)
    attacker_team: Option<i32>,      // 反眼英雄的队 (2/3)
    /// stable per-ward entity index when resolvable; None when inferred from
    /// combat-log death alone
    entity_index: Option<u32>,
    /// position at the moment of disappearance (from the per-entity tracker)
    x: Option<f64>,
    y: Option<f64>,
}

fn ward_class_type(class: &str) -> Option<&'static str> {
    match class {
        "CDOTA_NPC_Observer_Ward" => Some("observer"),
        "CDOTA_NPC_Observer_Ward_TrueSight" => Some("sentry"),
        _ => None,
    }
}

// ---- GamerulesProxy 官方时钟字段读取 (精确 tick↔cle) ----
fn gr_field_i64(e: &Entity, path: &str) -> Option<f64> {
    for f in e.fields() {
        if f.name != path { continue; }
        return match &f.value {
            Some(FieldValue::Signed32(v)) => Some(f64::from(*v)),
            Some(FieldValue::Unsigned32(v)) => Some(f64::from(*v)),
            Some(FieldValue::Signed64(v)) => Some(*v as f64),
            Some(FieldValue::Unsigned64(v)) => Some(*v as f64),
            _ => None,
        };
    }
    None
}
fn gr_field_float(e: &Entity, path: &str) -> Option<f64> {
    for f in e.fields() {
        if f.name != path { continue; }
        return match &f.value {
            Some(FieldValue::Float(v)) => Some(f64::from(*v)),
            Some(FieldValue::Signed32(v)) => Some(f64::from(*v)),
            _ => None,
        };
    }
    None
}

#[observer]
#[uses_all]
impl WardExtractor {
    #[on_entity]
    fn on_entity(&mut self, ctx: &Context, event: EntityEvents, entity: &Entity) -> ObserverResult {
        if event != EntityEvents::Created {
            return Ok(());
        }
        let class = entity.class().name();
        let Some(ward_type) = ward_class_type(class) else {
            return Ok(());
        };
        let x = cell_to_world(
            try_property!(entity, u32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_cellX"),
            try_property!(entity, f32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_vecX"),
        );
        let y = cell_to_world(
            try_property!(entity, u32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_cellY"),
            try_property!(entity, f32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_vecY"),
        );
        let (Some(x), Some(y)) = (x, y) else {
            return Ok(());
        };
        let tick_sec = f64::from(ctx.tick()) / f64::from(TICK_RATE);
        let cle = tick_sec - (self.cum_paused_ticks / f64::from(TICK_RATE));
        self.placed.push(WardPlaced {
            t: tick_sec,
            cle,
            ward_type,
            class: class.to_string(),
            x,
            y,
            team_code: try_property!(entity, i32, "m_iTeamNum"),
            entity_index: entity.index(),
        });
        Ok(())
    }

    #[on_tick_start]
    fn on_tick_start(&mut self, ctx: &Context) -> ObserverResult {
        // 采集 CDOTAGamerulesProxy 官方时钟(精确 tick↔cle)。
        for entity in ctx.entities().iter() {
            if entity.class().name() != "CDOTAGamerulesProxy" { continue; }
            if let Some(g) = gr_field_float(entity, "m_pGameRules.m_flGameStartTime") {
                if g > 0.0 { self.gst_base = g; }
            }
            if let Some(p) = gr_field_i64(entity, "m_pGameRules.m_nTotalPausedTicks") {
                self.cum_paused_ticks = p;
            }
            if let Some(b) = gr_field_i64(entity, "m_pGameRules.m_bGamePaused") {
                self.game_paused = b != 0.0;
            }
        }
        let tick = ctx.tick();
        let t = i64::from(tick / TICK_RATE);
        if t == self.last_sec {
            return Ok(());
        }
        self.last_sec = t;
        let mut present: Vec<u32> = Vec::new();
        for entity in ctx.entities().iter() {
            let class = entity.class().name();
            let Some(ward_type) = ward_class_type(class) else {
                continue;
            };
            let x = cell_to_world(
                try_property!(entity, u32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_cellX"),
                try_property!(entity, f32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_vecX"),
            );
            let y = cell_to_world(
                try_property!(entity, u32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_cellY"),
                try_property!(entity, f32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_vecY"),
            );
            let (Some(x), Some(y)) = (x, y) else {
                continue;
            };
            let idx = entity.index();
            present.push(idx);
            let team = try_property!(entity, i32, "m_iTeamNum");
            self.samples.entry(t).or_default().insert(idx, WardSample {
                t,
                x,
                y,
                team,
                class: ward_type,
            });
            // per-index tracking maps (for disappearance detection)
            self.idx_class.insert(idx, ward_type);
            self.idx_team.insert(idx, team);
            self.idx_npc.entry(idx).or_insert_with(|| match ward_type {
                "sentry" => "npc_dota_sentry_wards".to_string(),
                _ => "npc_dota_observer_wards".to_string(),
            });
            self.last_seen.insert(idx, (t, x, y));
        }
        // Disappearance detection: an index present last second but absent now
        // means the ward died (dewarded or expired). Emit event at its last
        // seen instant with its real coordinate — no LIFO needed.
        if let Some(prev) = self.last_sec_present.take() {
            for idx in prev {
                if !present.contains(&idx) {
                    if let Some((lt, lx, ly)) = self.last_seen.get(&idx).copied() {
                        let wt = self.idx_class.get(&idx).copied().unwrap_or("observer");
                        self.destroyed.push(WardDestroyed {
                            t: lt as f64,
                            t_tick: Some(lt as f64),
                            ward_type: wt,
                            unit: self.idx_npc.get(&idx).cloned().unwrap_or_else(|| "npc_dota_sentry_wards".to_string()),
                            actor: None,
                            self_expired: false,
                            team_code: self.idx_team.get(&idx).copied().flatten(),
                            attacker_team: None,
                            entity_index: Some(idx),
                            x: Some(lx),
                            y: Some(ly),
                        });
                    }
                }
            }
        }
        self.last_sec_present = Some(present);
        Ok(())
    }

    #[on_combat_log]
    fn on_combat_log(&mut self, ctx: &Context, cle: &CombatLogEntry) -> ObserverResult {
        // GAME_IN_PROGRESS (DotaCombatlogGameState val=5) = 游戏时钟 0:00 锚点。
        if cle.r#type() == DotaCombatlogTypes::DotaCombatlogGameState {
            if cle.value().ok() == Some(5) {
                if let Ok(ts) = cle.timestamp() {
                    self.game_start_cl = Some(f64::from(ts));
                }
            }
            return Ok(());
        }
        // 放置守卫(插眼/真眼/假眼): DotaCombatlogItem + inflictor=item_ward_sentry/observer + attacker=英雄。
        // 记录 cle 时间(游戏时钟) + 插眼者 + 守卫类型。⚠️ 无坐标/无队伍/无 entity_index(见 Q5_RULES §7)。
        if cle.r#type() == DotaCombatlogTypes::DotaCombatlogItem {
            let inf = cle.inflictor_name().ok().map(str::to_string).unwrap_or_default();
            let ward_type = match inf.as_str() {
                "item_ward_sentry" => Some("sentry"),
                "item_ward_observer" => Some("observer"),
                _ => None,
            };
            if let Some(wt) = ward_type {
                if cle.is_attacker_hero().unwrap_or(false) {
                    // rule out "给队友"(uses ... Ward on <英雄>): target 非空 或 target_is_self==false
                    // = 递给队友, 不是插眼。仅保留真插眼(target 空 且 target_is_self==true)。
                    let tgt = cle.target_name().ok().map(str::to_string).unwrap_or_default();
                    let tgt_self = cle.target_is_self().ok().unwrap_or(false);
                    if !tgt.is_empty() || !tgt_self {
                        return Ok(());
                    }
                    self.ward_use.push(WardUse {
                        t: cle.timestamp().unwrap_or_default() as f64,
                        actor: cle.attacker_name().ok().map(str::to_string),
                        ward_type: wt,
                        attacker_team: cle.attacker_team().ok().map(|v| v as i32),
                    });
                }
            }
        }
        if cle.r#type() != DotaCombatlogTypes::DotaCombatlogDeath {
            return Ok(());
        }
        let unit = cle.target_name().unwrap_or("").to_string();
        let Some(ward_type) = (match unit.as_str() {
            "npc_dota_observer_wards" => Some("observer"),
            "npc_dota_sentry_wards" => Some("sentry"),
            _ => None,
        }) else {
            return Ok(());
        };
        let actor = cle.attacker_name().ok().map(str::to_string);
        let self_expired = actor.as_deref() == Some(unit.as_str());
        // target_team = the ward's own team (DOTA_TEAM code 2/3 when present)
        let team_code = cle.target_team().ok().map(|v| v as i32);
        let attacker_team = cle.attacker_team().ok().map(|v| v as i32);
        let t_tick = Some(f64::from(ctx.tick()) / f64::from(TICK_RATE));
        self.destroyed.push(WardDestroyed {
            t: cle.timestamp().unwrap_or_default() as f64,
            t_tick,
            ward_type,
            unit,
            actor,
            self_expired,
            team_code,
            attacker_team,
            entity_index: None,
            x: None,
            y: None,
        });
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// building extractor (防御塔 / 兵营 / 瞭望塔 生存状态)
// ---------------------------------------------------------------------------

/// Kind of a tracked building, keyed by its entity class name.
fn building_kind(class: &str) -> Option<&'static str> {
    match class {
        "CDOTA_BaseNPC_Tower" => Some("tower"),
        "CDOTA_BaseNPC_Barracks" => Some("barracks"),
        "CDOTA_BaseNPC_Watch_Tower" => Some("watch"),
        _ => None,
    }
}

/// One building instance: static position + alive/dead bookkeeping.
struct BuildingState {
    x: f64,
    y: f64,
    team: Option<i32>,
    alive: bool,
    last_seen_sec: i64,
}

struct BuildingEvent {
    t: i64,
    spawned: bool,
    class: String,
    idx: u32,
    x: f64,
    y: f64,
    team: Option<i32>,
    kind: &'static str,
}

/// Building extractor: emits `building_spawn` (first sighting, carries the
/// static world position) and `building_destroyed` (health dropped to <=0, or
/// the entity vanished from the list for 2+ consecutive seconds) into
/// `game_events`. Sampled on the same whole-second gate as heroes, so it is
/// immune to the tick-parity issue. Towers/barracks/watch towers are
/// identified per instance by `entity.index()` because they share class names.
struct BuildingExtractor {
    buildings: HashMap<(String, u32), BuildingState>,
    events: Vec<BuildingEvent>,
    last_sec: i64,
}

impl Default for BuildingExtractor {
    fn default() -> Self {
        BuildingExtractor {
            buildings: HashMap::new(),
            events: Vec::new(),
            last_sec: i64::MIN,
        }
    }
}

#[observer]
#[uses_all]
impl BuildingExtractor {
    #[on_tick_start]
    fn on_tick_start(&mut self, ctx: &Context) -> ObserverResult {
        let tick = ctx.tick();
        let t = i64::from(tick / TICK_RATE);
        if t == self.last_sec {
            return Ok(());
        }
        self.last_sec = t;
        let mut present: Vec<(String, u32)> = Vec::new();
        for entity in ctx.entities().iter() {
            let class = entity.class().name();
            let Some(kind) = building_kind(class) else {
                continue;
            };
            let idx = entity.index();
            let key = (class.to_string(), idx);
            let x = cell_to_world(
                try_property!(entity, u32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_cellX"),
                try_property!(entity, f32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_vecX"),
            );
            let y = cell_to_world(
                try_property!(entity, u32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_cellY"),
                try_property!(entity, f32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_vecY"),
            );
            let (Some(x), Some(y)) = (x, y) else {
                continue;
            };
            let hp = try_property!(entity, i32, "m_iHealth").map(i64::from);
            let team = try_property!(entity, i32, "m_iTeamNum");
            present.push(key.clone());
            if let Some(st) = self.buildings.get_mut(&key) {
                st.last_seen_sec = t;
                if st.alive && hp.map_or(false, |h| h <= 0) {
                    st.alive = false;
                    self.events.push(BuildingEvent {
                        t,
                        spawned: false,
                        class: class.to_string(),
                        idx,
                        x: st.x,
                        y: st.y,
                        team: st.team,
                        kind,
                    });
                }
            } else {
                self.buildings.insert(
                    key.clone(),
                    BuildingState {
                        x,
                        y,
                        team,
                        alive: hp.map_or(true, |h| h > 0),
                        last_seen_sec: t,
                    },
                );
                self.events.push(BuildingEvent {
                    t,
                    spawned: true,
                    class: class.to_string(),
                    idx,
                    x,
                    y,
                    team,
                    kind,
                });
            }
        }
        // buildings that are gone from the entity list for 2 consecutive
        // seconds are considered destroyed (removed right after death).
        let dead: Vec<(String, u32)> = self
            .buildings
            .iter()
            .filter(|(k, st)| st.alive && !present.contains(k))
            .filter_map(|(k, st)| {
                if st.last_seen_sec <= t - 2 {
                    Some(k.clone())
                } else {
                    None
                }
            })
            .collect();
        for key in dead {
            let Some(st) = self.buildings.get_mut(&key) else {
                continue;
            };
            st.alive = false;
            let (class, idx) = key.clone();
            self.events.push(BuildingEvent {
                t: st.last_seen_sec + 1, // first second it was gone
                spawned: false,
                class: class.clone(),
                idx,
                x: st.x,
                y: st.y,
                team: st.team,
                kind: building_kind(&class).unwrap_or("tower"),
            });
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// jungle / economy extractor (野怪击杀 + 逐玩家金币事件)
// ---------------------------------------------------------------------------

/// A neutral camp kill (a `CDOTA_BaseNPC_Creep_Neutral` / Roshan died).
struct JungleKill {
    t: i64,
    x: f64,
    y: f64,
    kind: &'static str, // "neutral" | "roshan"
}

/// A combat-log `Gold` event: a hero earned gold (kill bounty, creep, passive,
/// etc.). `value` is the gold amount, `reason` the Dota gold-reason code.
struct GoldEvent {
    t: f64,
    hero: String,
    value: i64,
    reason: Option<u32>,
    x: Option<f64>,
    y: Option<f64>,
}

/// Jungle extractor:
///  * snapshots neutral creep / Roshan entities each whole second (position +
///    hp, tracked per entity index) and emits `neutral_kill` / `roshan_kill`
///    when one dies (hp <= 0, or it vanished from the entity list while alive).
///    This gives the "刷野活动热区" by position, with no parser schema change.
///  * mirrors combat-log `DotaCombatlogGold` into `gold` events (per-player
///    gold income with value/reason/location) for team gold_adv aggregation.
///
/// Kill *attribution* (which hero cleared the camp) is joined later by matching
/// the combat-log Death (target = neutral unit) with these positioned kills.
struct JungleExtractor {
    /// entity index -> (x, y, hp, alive, last_seen_sec)
    units: HashMap<u32, (f64, f64, i64, bool, i64)>,
    kills: Vec<JungleKill>,
    golds: Vec<GoldEvent>,
    last_sec: i64,
}

impl Default for JungleExtractor {
    fn default() -> Self {
        JungleExtractor {
            units: HashMap::new(),
            kills: Vec::new(),
            golds: Vec::new(),
            last_sec: i64::MIN,
        }
    }
}

fn jungle_kind(class: &str) -> Option<&'static str> {
    match class {
        "CDOTA_BaseNPC_Creep_Neutral" => Some("neutral"),
        "CDOTA_Unit_Roshan" => Some("roshan"),
        _ => None,
    }
}

#[observer]
#[uses_all]
impl JungleExtractor {
    #[on_tick_start]
    fn on_tick_start(&mut self, ctx: &Context) -> ObserverResult {
        let tick = ctx.tick();
        let t = i64::from(tick / TICK_RATE);
        if t == self.last_sec {
            return Ok(());
        }
        self.last_sec = t;
        let mut present: Vec<u32> = Vec::new();
        for entity in ctx.entities().iter() {
            let class = entity.class().name();
            let Some(kind) = jungle_kind(class) else {
                continue;
            };
            let idx = entity.index();
            let x = cell_to_world(
                try_property!(entity, u32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_cellX"),
                try_property!(entity, f32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_vecX"),
            );
            let y = cell_to_world(
                try_property!(entity, u32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_cellY"),
                try_property!(entity, f32, "CBodyComponent.m_skeletonInstance.m_vecOrigin.m_vecY"),
            );
            let (Some(x), Some(y)) = (x, y) else { continue };
            let hp = try_property!(entity, i32, "m_iHealth").map(i64::from);
            present.push(idx);
            if let Some((_, _, old_hp, alive, _)) = self.units.get_mut(&idx) {
                let new_hp = hp.unwrap_or(0);
                if *alive && new_hp <= 0 {
                    self.kills.push(JungleKill { t, x, y, kind });
                }
                self.units.insert(idx, (x, y, new_hp, new_hp > 0, t));
            } else {
                let h = hp.unwrap_or(0);
                self.units.insert(idx, (x, y, h, h > 0, t));
            }
        }
        // units that vanished from the entity list while alive are considered
        // killed (neutral creeps are removed right after death).
        let dead: Vec<u32> = self
            .units
            .iter()
            .filter(|(idx, (_, _, _, alive, last_seen))| *alive && !present.contains(idx))
            .filter_map(|(idx, (_, _, _, _, last_seen))| {
                if *last_seen <= t - 2 {
                    Some(*idx)
                } else {
                    None
                }
            })
            .collect();
        for idx in dead {
            if let Some((x, y, _, _, last_seen)) = self.units.get(&idx).copied() {
                self.kills.push(JungleKill {
                    t: last_seen + 1,
                    x,
                    y,
                    kind: "neutral",
                });
                self.units.remove(&idx);
            }
        }
        Ok(())
    }

    #[on_combat_log]
    fn on_combat_log(&mut self, _ctx: &Context, cle: &CombatLogEntry) -> ObserverResult {
        if cle.r#type() != DotaCombatlogTypes::DotaCombatlogGold {
            return Ok(());
        }
        let recipient = cle
            .target_name()
            .map(str::to_string)
            .unwrap_or_default();
        if recipient.is_empty() || !recipient.starts_with("npc_dota_hero_") {
            return Ok(());
        }
        let value = cle.value().unwrap_or(0) as i64;
        let reason = cle.gold_reason().ok();
        let x = cle.location_x().ok().map(f64::from);
        let y = cle.location_y().ok().map(f64::from);
        self.golds.push(GoldEvent {
            t: cle.timestamp().unwrap_or_default() as f64,
            hero: recipient,
            value,
            reason,
            x,
            y,
        });
        Ok(())
    }
}

/// Assemble `game_events` rows for jungle kills and gold events.
fn build_jungle_event_rows(
    kills: &[JungleKill],
    golds: &[GoldEvent],
) -> Vec<EventRow> {
    let mut rows = Vec::new();
    // (event_type, actor-group, second) -> next event_seq
    let mut seq: HashMap<(&'static str, String, i64), i64> = HashMap::new();
    for k in kills {
        let key = ("neutral_kill", k.kind.to_string(), k.t);
        let n = seq.entry(key).or_insert(0);
        let event_seq = *n;
        *n += 1;
        rows.push(EventRow {
            game_time_sec: k.t,
            event_type: "neutral_kill",
            actor_id: None,
            target_id: Some(k.kind.to_string()),
            x: Some(k.x),
            y: Some(k.y),
            properties: serde_json::json!({ "kind": k.kind }),
            event_seq,
        });
    }
    for g in golds {
        let sec = g.t.floor() as i64;
        let key = ("gold", g.hero.clone(), sec);
        let n = seq.entry(key).or_insert(0);
        let event_seq = *n;
        *n += 1;
        rows.push(EventRow {
            game_time_sec: sec,
            event_type: "gold",
            actor_id: Some(g.hero.clone()),
            target_id: None,
            x: g.x,
            y: g.y,
            properties: serde_json::json!({
                "value": g.value,
                "reason": g.reason,
            }),
            event_seq,
        });
    }
    rows
}

// ---------------------------------------------------------------------------
// net worth extractor (逐玩家每分净值 / 现金, A1)
// ---------------------------------------------------------------------------
//
// Per-player net worth + reliable/unreliable gold are broadcast on the
// CDOTA_DataRadiant / CDOTA_DataDire entities, one sub-field per player:
//   <index>.m_iNetWorth, <index>.m_iReliableGold, <index>.m_iUnreliableGold
// Values are readable (verified): e.g. CDOTA_DataRadiant.0000.m_iNetWorth=8446.
// Sampled on the same whole-second gate as heroes; emitted as entity_snapshots
// rows of entity_type='networth' so it never collides with hero positions.

#[derive(Clone)]
struct NetWorthSample {
    networth: i64,
    reliable: Option<i64>,
    unreliable: Option<i64>,
}

fn parse_index_suffix(field: &str) -> Option<(u32, &str)> {
    let b = field.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i].is_ascii_digit() {
            let start = i;
            while i < b.len() && b[i].is_ascii_digit() {
                i += 1;
            }
            let idx: u32 = field[start..i].parse().ok()?;
            return Some((idx, &field[i..]));
        }
        i += 1;
    }
    None
}

struct NetWorthExtractor {
    // entity_id ("nw:radiant:0") -> (second -> sample)
    samples: HashMap<String, BTreeMap<i64, NetWorthSample>>,
    // (team, minute) -> in-game team net worth (from CDOTASpectatorGraphManagerProxy)
    team_networth: HashMap<(String, u32), i64>,
    last_sec: i64,
}

impl Default for NetWorthExtractor {
    fn default() -> Self {
        NetWorthExtractor {
            samples: HashMap::new(),
            team_networth: HashMap::new(),
            last_sec: i64::MIN,
        }
    }
}

fn data_team_of(class: &str) -> Option<&'static str> {
    match class {
        "CDOTA_DataRadiant" => Some("radiant"),
        "CDOTA_DataDire" => Some("dire"),
        _ => None,
    }
}

#[observer]
#[uses_all]
impl NetWorthExtractor {
    #[on_tick_start]
    fn on_tick_start(&mut self, ctx: &Context) -> ObserverResult {
        let t = i64::from(ctx.tick() / TICK_RATE);
        if t == self.last_sec {
            return Ok(());
        }
        self.last_sec = t;
        for entity in ctx.entities().iter() {
            let class = entity.class().name();
            let Some(team) = data_team_of(class) else { continue };
            let mut by_idx: HashMap<u32, NetWorthSample> = HashMap::new();
            for f in entity.fields() {
                let Some((idx, suffix)) = parse_index_suffix(&f.name) else { continue };
                let val = match &f.value {
                    Some(FieldValue::Signed32(v)) => i64::from(*v),
                    _ => continue,
                };
                let e = by_idx
                    .entry(idx)
                    .or_insert(NetWorthSample { networth: 0, reliable: None, unreliable: None });
                if suffix == ".m_iNetWorth" {
                    e.networth = val;
                } else if suffix.ends_with("m_iReliableGold") {
                    e.reliable = Some(val);
                } else if suffix.ends_with("m_iUnreliableGold") {
                    e.unreliable = Some(val);
                }
            }
            for (idx, s) in by_idx {
                let entity_id = format!("nw:{team}:{idx}");
                self.samples.entry(entity_id).or_default().insert(t, s);
            }
        }
        // In-game TEAM net worth per minute (authoritative gold-adv source).
        for entity in ctx.entities().iter() {
            let class = entity.class().name();
            if !class.contains("GraphManager") && !class.contains("GameRules") {
                continue;
            }
            for f in entity.fields() {
                let nm = f.name.as_str();
                let nw = match &f.value {
                    Some(FieldValue::Signed32(v)) => i64::from(*v),
                    _ => continue,
                };
                // ...m_rgRadiantNetWorth.0042 / ...m_rgDireNetWorth.0042
                let idx = if let Some(p) = nm.rfind("m_rgRadiantNetWorth.") {
                    let rest = &nm[p + "m_rgRadiantNetWorth.".len()..];
                    if let Ok(mm) = rest.parse::<u32>() {
                        self.team_networth.insert(("radiant".to_string(), mm), nw);
                        continue;
                    } else {
                        continue;
                    }
                } else if let Some(p) = nm.rfind("m_rgDireNetWorth.") {
                    let rest = &nm[p + "m_rgDireNetWorth.".len()..];
                    if let Ok(mm) = rest.parse::<u32>() {
                        self.team_networth.insert(("dire".to_string(), mm), nw);
                        continue;
                    } else {
                        continue;
                    }
                } else {
                    continue;
                };
            }
        }
        Ok(())
    }
}

/// Assemble `entity_snapshots` rows for per-player net worth (entity_type='networth').
fn build_networth_snapshot_rows(
    samples: &HashMap<String, BTreeMap<i64, NetWorthSample>>,
) -> Vec<SnapshotRow> {
    let mut rows = Vec::new();
    for (entity_id, per_sec) in samples {
        for (t, s) in per_sec {
            rows.push(SnapshotRow {
                game_time_sec: *t,
                entity_type: "networth",
                entity_id: entity_id.clone(),
                team: None,
                x: 0.0,
                y: 0.0,
                hp: Some(s.networth),
                extra: serde_json::json!({
                    "networth": s.networth,
                    "reliable": s.reliable,
                    "unreliable": s.unreliable,
                    "kind": "networth",
                }),
            });
        }
    }
    rows
}

/// Assemble `entity_snapshots` rows for in-game team net worth per minute
/// (entity_type='team_networth', entity_id='team:radiant').
fn build_team_networth_rows(
    team_networth: &HashMap<(String, u32), i64>,
) -> Vec<SnapshotRow> {
    let mut rows = Vec::new();
    for ((team, minute), nw) in team_networth {
        rows.push(SnapshotRow {
            game_time_sec: i64::from(*minute) * 60,
            entity_type: "team_networth",
            entity_id: format!("team:{team}"),
            team: None,
            x: 0.0,
            y: 0.0,
            hp: Some(*nw),
            extra: serde_json::json!({ "networth": nw, "kind": "team_networth" }),
        });
    }
    rows
}

// ---------------------------------------------------------------------------
// ability cooldown extractor (技能冷却追踪)
// ---------------------------------------------------------------------------

/// One ability/item cooldown (or known/learn) transition of one hero.
struct AbilityEvent {
    t: i64,
    pid: u32,             // m_iPlayerID / m_iPlayerOwnerID (2 x header index)
    key: String,          // ability class, or "ITEM:<item short name>"
    kind: &'static str,   // "ability" | "item"
    phase: &'static str,  // "known" | "learn" | "cd_start" | "cd_end"
    remaining: f32,
}

/// Ability extractor: per whole second, for every hero entity read its
/// `m_vecAbilities.*` ability handles, read each ability's `m_fCooldown`
/// (seconds remaining) and `m_iLevel`; emits ability_known / ability_learn /
/// ability_cd_start / ability_cd_end. Also tracks active items (BKB /
/// Refresher) via their `m_iPlayerOwnerID` + `m_fCooldown` as item_known /
/// item_cd_start / item_cd_end. Passives never leave cooldown (m_fCooldown 0)
/// so they are naturally filtered; unlearned abilities (m_iLevel 0) are still
/// reported via ability_known so the viewer can grey them out.
struct AbilityExtractor {
    /// (hero pid, key) -> when its current cooldown started
    active: HashMap<(u32, String), i64>,
    known: HashSet<(u32, String)>,
    learned: HashSet<(u32, String)>,
    item_known: HashSet<(u32, String)>,
    smoke_last: HashMap<u32, u32>,
    events: Vec<AbilityEvent>,
    last_sec: i64,
}

impl Default for AbilityExtractor {
    fn default() -> Self {
        AbilityExtractor {
            active: HashMap::new(),
            known: HashSet::new(),
            learned: HashSet::new(),
            item_known: HashSet::new(),
            smoke_last: HashMap::new(),
            events: Vec::new(),
            last_sec: i64::MIN,
        }
    }
}

#[observer]
#[uses_all]
impl AbilityExtractor {
    #[on_tick_start]
    fn on_tick_start(&mut self, ctx: &Context) -> ObserverResult {
        let tick = ctx.tick();
        let t = i64::from(tick / TICK_RATE);
        if t == self.last_sec {
            return Ok(());
        }
        self.last_sec = t;
        // pass 1: ability handle -> (class, cd, level)
        let mut abilities: HashMap<u32, (String, f32, i32)> = HashMap::new();
        for e in ctx.entities().iter() {
            let cls = e.class().name();
            if !cls.starts_with("CDOTA_Ability_") || cls.contains("Courier") {
                continue;
            }
            let cd = try_property!(e, f32, "m_fCooldown").unwrap_or(0.0);
            let lvl = try_property!(e, i32, "m_iLevel").unwrap_or(0);
            abilities.insert(e.handle(), (cls.to_string(), cd, lvl));
        }
        // pass 2: per hero, iterate its ability handles; known/learn/cd events
        for e in ctx.entities().iter() {
            let cls = e.class().name();
            if !cls.starts_with("CDOTA_Unit_Hero_") {
                continue;
            }
            let pid = match try_property!(e, u32, "m_iPlayerID") {
                Some(p) => p,
                None => continue,
            };
            let mut handles: Vec<u32> = Vec::new();
            for f in e.fields() {
                if f.name.starts_with("m_vecAbilities.") {
                    if let Some(v) = f.value {
                        if let source2_demo::FieldValue::Unsigned32(h) = v {
                            handles.push(*h);
                        }
                    }
                }
            }
            for h in handles {
                let Some((ab_cls, cd, lvl)) = abilities.get(&h) else { continue };
                let key = (pid, ab_cls.clone());
                if self.known.insert(key.clone()) {
                    self.events.push(AbilityEvent {
                        t, pid, key: ab_cls.clone(), kind: "ability",
                        phase: "known", remaining: 0.0,
                    });
                }
                if *lvl > 0 && self.learned.insert(key.clone()) {
                    self.events.push(AbilityEvent {
                        t, pid, key: ab_cls.clone(), kind: "ability",
                        phase: "learn", remaining: 0.0,
                    });
                }
                if cd > &0.0 {
                    if !self.active.contains_key(&key) {
                        self.events.push(AbilityEvent {
                            t, pid, key: ab_cls.clone(), kind: "ability",
                            phase: "cd_start", remaining: *cd,
                        });
                        self.active.insert(key, t);
                    }
                } else if let Some(_start) = self.active.remove(&key) {
                    self.events.push(AbilityEvent {
                        t, pid, key: ab_cls.clone(), kind: "ability",
                        phase: "cd_end", remaining: 0.0,
                    });
                }
            }
        }
        // pass 3: active items (BKB / Refresher) tracked by owner player id
        for e in ctx.entities().iter() {
            let cls = e.class().name();
            let Some(short) = cls.strip_prefix("CDOTA_Item_") else { continue };
            if short.contains("Recipe_") || !ITEM_TRACK.contains(&short) {
                continue;
            }
            let pid = match try_property!(e, u32, "m_iPlayerOwnerID") {
                Some(p) => p,
                None => continue,
            };
            let cd = try_property!(e, f32, "m_fCooldown").unwrap_or(0.0);
            let key = (pid, format!("ITEM:{}", short));
            if self.item_known.insert(key.clone()) {
                self.events.push(AbilityEvent {
                    t, pid, key: format!("ITEM:{}", short), kind: "item",
                    phase: "known", remaining: 0.0,
                });
            }
            if cd > 0.0 {
                if !self.active.contains_key(&key) {
                    self.events.push(AbilityEvent {
                        t, pid, key: format!("ITEM:{}", short), kind: "item",
                        phase: "cd_start", remaining: cd,
                    });
                    self.active.insert(key, t);
                }
            } else if let Some(_start) = self.active.remove(&key) {
                self.events.push(AbilityEvent {
                    t, pid, key: format!("ITEM:{}", short), kind: "item",
                    phase: "cd_end", remaining: 0.0,
                });
            }
        }
        // pass 4: smoke of deceit count per hero (panels as smoke_count changes)
        let mut smoke: HashMap<u32, u32> = HashMap::new();
        for e in ctx.entities().iter() {
            if e.class().name() != "CDOTA_Item_Smoke_Of_Deceit" {
                continue;
            }
            if let Some(pid) = try_property!(e, u32, "m_iPlayerOwnerID") {
                *smoke.entry(pid).or_insert(0) += 1;
            }
        }
        for (pid, cnt) in smoke {
            if self.smoke_last.get(&pid) != Some(&cnt) {
                self.events.push(AbilityEvent {
                    t, pid, key: "SMOKE".to_string(), kind: "smoke",
                    phase: "count", remaining: cnt as f32,
                });
                self.smoke_last.insert(pid, cnt);
            }
        }
        Ok(())
    }
}

// active items we track cooldowns for (short class name after CDOTA_Item_)
const ITEM_TRACK: &[&str] = &["Black_King_Bar", "RefresherOrb"];

/// Assemble `game_events` rows for ability/item knowledge, learning and
/// cooldown transitions (resolving hero npc via header players: pid=2 x index).
fn build_ability_event_rows(events: &[AbilityEvent], players: &[HeaderPlayer]) -> Vec<EventRow> {
    let mut rows = Vec::new();
    let mut seq: HashMap<(&'static str, String, i64), i64> = HashMap::new();
    fn etype(kind: &str, phase: &str) -> &'static str {
        match (kind, phase) {
            ("ability", "known") => "ability_known",
            ("ability", "learn") => "ability_learn",
            ("ability", "cd_start") => "ability_cd_start",
            ("ability", "cd_end") => "ability_cd_end",
            ("item", "known") => "item_known",
            ("item", "cd_start") => "item_cd_start",
            ("item", "cd_end") => "item_cd_end",
            ("smoke", "count") => "smoke_count",
            _ => "ability_cd_start",
        }
    }
    for ev in events {
        let hero = players
            .get(ev.pid as usize / 2)
            .and_then(|p| p.hero_npc.clone())
            .unwrap_or_default();
        let etype = etype(ev.kind, ev.phase);
        let key = (etype, hero.clone(), ev.t);
        let n = seq.entry(key).or_insert(0);
        let event_seq = *n;
        *n += 1;
        rows.push(EventRow {
            game_time_sec: ev.t,
            event_type: etype,
            actor_id: if hero.is_empty() { None } else { Some(hero.clone()) },
            target_id: Some(ev.key.clone()),
            x: None,
            y: None,
            properties: serde_json::json!({
                "hero": hero,
                "key": ev.key,
                "kind": ev.kind,
                "phase": ev.phase,
                "remaining": ev.remaining,
            }),
            event_seq,
        });
    }
    rows
}

/// Assemble `game_events` rows for building spawn/destroy events.
fn build_building_event_rows(events: &[BuildingEvent]) -> Vec<EventRow> {
    let mut rows = Vec::new();
    let mut seq: HashMap<(&'static str, i64), i64> = HashMap::new();
    for ev in events {
        let etype: &'static str = if ev.spawned {
            "building_spawn"
        } else {
            "building_destroyed"
        };
        let n = seq.entry((etype, ev.t)).or_insert(0);
        let event_seq = *n;
        *n += 1;
        rows.push(EventRow {
            game_time_sec: ev.t,
            event_type: etype,
            actor_id: None,
            target_id: Some(format!("{}#{}", ev.class, ev.idx)),
            x: Some(ev.x),
            y: Some(ev.y),
            properties: serde_json::json!({
                "kind": ev.kind,
                "class": ev.class,
                "team": ev.team,
            }),
            event_seq,
        });
    }
    rows
}

/// Assemble `game_events` rows for ward events.
fn build_ward_event_rows(placed: &[WardPlaced], game_start_cl: Option<f64>) -> Vec<EventRow> {
    let mut rows = Vec::new();
    let mut seq: HashMap<(&'static str, String, i64), i64> = HashMap::new();
    let mut next_seq = |key: &(&'static str, String, i64)| -> i64 {
        let n = seq.entry(key.clone()).or_insert(0);
        let v = *n;
        *n += 1;
        v
    };
    // GAME_IN_PROGRESS 0:00 锚点: 一个 game_state 事件, 存 cle.timestamp (游戏时钟 0:00 基准)。
    if let Some(gsc) = game_start_cl {
        let sec = gsc.floor() as i64;
        let event_seq = next_seq(&("game_state", String::new(), sec));
        rows.push(EventRow {
            game_time_sec: sec,
            event_type: "game_state",
            actor_id: None,
            target_id: None,
            x: None,
            y: None,
            properties: serde_json::json!({
                "state": 5,
                "game_start_cl": gsc,
            }),
            event_seq,
        });
    }
    for p in placed {
        let sec = p.t.floor() as i64;
        let event_seq = next_seq(&("ward_placed", String::new(), sec));
        rows.push(EventRow {
            game_time_sec: sec,
            event_type: "ward_placed",
            actor_id: None,
            target_id: Some(p.class.clone()),
            x: Some(p.x),
            y: Some(p.y),
            properties: serde_json::json!({
                "ward_type": p.ward_type,
                "team": p.team_code,
                "entity_index": p.entity_index,
                "entity_id": format!("ward:{}", p.entity_index),
                "t_cle": p.cle,
                "t_tick": p.t,
            }),
            event_seq,
        });
    }
    rows
}

/// Per-ward position snapshots -> `entity_snapshots` rows (entity_type='ward').
/// entity_id = "ward:<index>", so each ward's true position + lifetime is
/// reconstructed directly from the entity stream — no LIFO pairing.
fn build_ward_snapshot_rows(samples: &HashMap<i64, HashMap<u32, WardSample>>) -> Vec<SnapshotRow> {
    let mut rows = Vec::new();
    for (t, per_sec) in samples {
        for (idx, s) in per_sec {
            // only emit coordinates once resolvable
            if s.x == 0.0 && s.y == 0.0 {
                continue;
            }
            rows.push(SnapshotRow {
                game_time_sec: *t,
                entity_type: "ward",
                entity_id: format!("ward:{}", idx),
                team: s.team.and_then(team_text).map(str::to_string),
                x: s.x,
                y: s.y,
                hp: None,
                extra: serde_json::json!({
                    "ward_type": s.class,
                    "entity_index": idx,
                }),
            });
        }
    }
    rows
}

// ---------------------------------------------------------------------------
// combat log extractor (通用 combat_log 表: 全类型全量, 不聚合不去重)
// ---------------------------------------------------------------------------

/// Map a `DOTA_COMBATLOG_TYPES` Debug name (e.g. "DotaCombatlogDamage") to the
/// in-game toggle category (the `type_category` enum).
fn type_category(ty: &str) -> &'static str {
    match ty {
        "DotaCombatlogDamage" | "DotaCombatlogManaDamage" | "DotaCombatlogCriticalDamage"
        | "DotaCombatlogSpellAbsorb" | "DotaCombatlogPhysicalDamagePrevented"
        | "DotaCombatlogAttackEvade" => "damage",
        "DotaCombatlogHeal" | "DotaCombatlogManaRestored" | "DotaCombatlogBottleHealAlly" => "healing",
        "DotaCombatlogAbility" | "DotaCombatlogAbilityTrigger" | "DotaCombatlogHeroLevelup"
        | "DotaCombatlogInterruptChannel" => "ability",
        "DotaCombatlogItem" | "DotaCombatlogPurchase" | "DotaCombatlogBuyback"
        | "DotaCombatlogNeutralItemEarned" => "item",
        "DotaCombatlogModifierAdd" | "DotaCombatlogModifierRemove"
        | "DotaCombatlogModifierStackEvent" => "modifier",
        "DotaCombatlogDeath" | "DotaCombatlogKillstreak" | "DotaCombatlogMultikill"
        | "DotaCombatlogFirstBlood" | "DotaCombatlogTeamBuildingKill"
        | "DotaCombatlogEndKillstreak" => "death",
        "DotaCombatlogGold" => "gold",
        "DotaCombatlogXp" => "xp",
        "DotaCombatlogPlayerstats" => "playerstats",
        "DotaCombatlogGameState" => "gamestate",
        "DotaCombatlogLocation" => "location",
        "DotaCombatlogPickupRune" => "rune",
        "DotaCombatlogRevealedInvisible" => "revealed",
        "DotaCombatlogSuccessfulScan" => "scan",
        "DotaCombatlogAegisTaken" => "aegis",
        "DotaCombatlogUnitSummoned" => "summoned",
        "DotaCombatlogTreeCut" => "tree",
        "DotaCombatlogKillEaterEvent" => "killeater",
        _ => "other",
    }
}

/// Captures every combat-log entry into the universal `combat_log` table.
/// Records the GamerulesProxy `m_flGameStartTime` (0:00 horn base) and a dense
/// raw-tick -> cle calibration (from all combat entries) so entity rows
/// (ward_placed) can carry the same game-clock domain.
struct CombatLogExtractor {
    rows: Vec<CombatLogRow>,
    seq: i64,
    /// (raw tick/30, cle) samples from all combat entries — dense clock reference.
    clock: Vec<(f64, f64)>,
    /// GamerulesProxy `m_pGameRules.m_flGameStartTime` (horn base, cle scale).
    game_start_cl: Option<f64>,
}

impl Default for CombatLogExtractor {
    fn default() -> Self {
        CombatLogExtractor {
            rows: Vec::new(),
            seq: 0,
            clock: Vec::new(),
            game_start_cl: None,
        }
    }
}

impl CombatLogExtractor {
    /// Convert a raw tick/30 second to the game-clock (cle) domain, by
    /// interpolation between the bracketing combat-entry samples. The clock vec
    /// must be sorted by raw tick (done in `parse_replay`) before use.
    fn cle_at(&self, tick_sec: f64) -> f64 {
        let c = &self.clock;
        if c.is_empty() {
            return tick_sec;
        }
        match c.binary_search_by(|(t, _)| t.partial_cmp(&tick_sec).unwrap_or(std::cmp::Ordering::Equal)) {
            Ok(i) => c[i].1,
            Err(i) => {
                if i == 0 {
                    c[0].1
                } else if i >= c.len() {
                    c[c.len() - 1].1
                } else {
                    let (t0, c0) = c[i - 1];
                    let (t1, c1) = c[i];
                    let f = if (t1 - t0).abs() < 1e-9 { 0.0 } else { (tick_sec - t0) / (t1 - t0) };
                    c0 + f * (c1 - c0)
                }
            }
        }
    }
}

#[observer]
#[uses_all]
impl CombatLogExtractor {
    #[on_tick_start]
    fn on_tick_start(&mut self, ctx: &Context) -> ObserverResult {
        if self.game_start_cl.is_none() {
            for e in ctx.entities().iter() {
                if e.class().name().contains("GameRules") {
                    if let Some(v) = try_property!(e, f32, "m_pGameRules.m_flGameStartTime") {
                        self.game_start_cl = Some(f64::from(v));
                    }
                    break;
                }
            }
        }
        Ok(())
    }

    #[on_combat_log]
    fn on_combat_log(&mut self, ctx: &Context, cle: &CombatLogEntry) -> ObserverResult {
        let type_name = format!("{:?}", cle.r#type());
        let t_cle = cle.timestamp().unwrap_or_default() as f64;
        let t_tick = f64::from(ctx.tick()) / f64::from(TICK_RATE);
        self.clock.push((t_tick, t_cle));
        let log = cle.log();
        let seq = self.seq;
        self.seq += 1;
        let attacker = cle.attacker_name().ok().map(str::to_string);
        let target = cle.target_name().ok().map(str::to_string);
        let damage_source = cle.damage_source_name().ok().map(str::to_string);
        let inflictor = cle.inflictor_name().ok().map(str::to_string);
        let value_name = cle.value_name().ok().map(str::to_string);
        let assist = if log.assist_players.is_empty() {
            None
        } else {
            Some(serde_json::json!(log.assist_players.iter().collect::<Vec<_>>()).to_string())
        };
        let raw_json = serde_json::json!({
            "type": type_name.clone(),
            "attacker": attacker.clone(), "target": target.clone(),
            "damage_source": damage_source.clone(),
            "inflictor": inflictor.clone(), "value_name": value_name.clone(),
            "value": cle.value().ok(), "health": cle.health().ok(),
            "location": [cle.location_x().ok(), cle.location_y().ok()],
            "a_team": cle.attacker_team().ok(), "t_team": cle.target_team().ok(),
        })
        .to_string();
        self.rows.push(CombatLogRow {
            event_seq: seq,
            t_cle,
            t_tick,
            type_category: type_category(&type_name),
            type_name,
            attacker,
            target,
            damage_source,
            inflictor,
            value_name,
            value: cle.value().ok().map(i64::from),
            health: cle.health().ok().map(i64::from),
            location_x: cle.location_x().ok().map(f64::from),
            location_y: cle.location_y().ok().map(f64::from),
            a_team: cle.attacker_team().ok().map(i64::from),
            t_team: cle.target_team().ok().map(i64::from),
            stack_count: cle.stack_count().ok().map(i64::from),
            modifier_duration: cle.modifier_duration().ok().map(f64::from),
            modifier_elapsed: cle.modifier_elapsed_duration().ok().map(f64::from),
            ability_level: cle.ability_level().ok().map(i64::from),
            assist_players: assist,
            gold_reason: cle.gold_reason().ok().map(i64::from),
            xp_reason: cle.xp_reason().ok().map(i64::from),
            event_location: cle.event_location().ok().map(i64::from),
            is_attacker_hero: cle.is_attacker_hero().ok().map(|b| b as i64),
            is_target_hero: cle.is_target_hero().ok().map(|b| b as i64),
            is_target_building: cle.is_target_building().ok().map(|b| b as i64),
            raw_json: Some(raw_json),
        });
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// results
// ---------------------------------------------------------------------------

/// Everything extracted from one replay, ready for the DB writer.
#[derive(Debug)]
pub struct ParsedReplay {
    pub match_id: i64,
    pub duration_seconds: Option<f64>,
    pub header_players: Vec<HeaderPlayer>,
    pub identity_rows: Vec<PlayerIdentityRow>,
    pub snapshot_rows: Vec<SnapshotRow>,
    pub event_rows: Vec<EventRow>,
    /// Universal combat-log narrative (all types, raw) -> `combat_log` table.
    pub combat_rows: Vec<CombatLogRow>,
    /// (entity_id, sample count, first second, last second) for the log.
    pub entity_log: Vec<(String, usize, i64, i64)>,
}

/// Extract header identity rows. The writer binds `match_id` once per match.
fn build_identity_rows(players: &[HeaderPlayer]) -> Vec<PlayerIdentityRow> {
    players
        .iter()
        .map(|p| PlayerIdentityRow {
            player_slot: p.player_slot,
            steam_id: p.steam_id.map(|v| v as i64),
            player_name: p.player_name.clone(),
            hero_name: p.hero_npc.clone().unwrap_or_default(),
            // Numeric hero id needs the external hero dictionary (§7): filled
            // by a later enrichment step, not by the .dem parser.
            hero_id: None,
            team_id: p.team_code.map(i64::from),
        })
        .collect()
}

/// Assemble `entity_snapshots` rows from collected hero samples. Hero identity
/// (npc / team / slot) is resolved **from the entity's own `m_iPlayerID`**
/// (= 2 × header index, verified in §6.6), NOT by guessing the class name from
/// the npc name: entity class strings are not guaranteed to be the npc name in
/// CamelCase (newer builds e.g. use `CDOTA_Unit_Hero_Spiritbreaker` while the
/// header npc is `npc_dota_hero_spirit_breaker`). The pid->header mapping also
/// makes summons (no player slot) fall back to a class-derived id with no team.
fn build_snapshot_rows(
    players: &[HeaderPlayer],
    samples_by_class: &HashMap<String, BTreeMap<i64, HeroSample>>,
) -> Vec<SnapshotRow> {
    let mut rows = Vec::new();
    let mut npc_occurrences: HashMap<String, u32> = HashMap::new();

    for (class, samples) in samples_by_class {
        let class = class.clone();
        // Resolve the header player from the first sample's pid (all samples of
        // one class belong to the same entity). pid = 2 * header index.
        let header_idx = samples
            .values()
            .next()
            .and_then(|s| {
                let pid = s.pid;
                if pid != INVALID_PLAYER_ID && pid % 2 == 0 {
                    let idx = (pid / 2) as usize;
                    (idx < players.len()).then_some(idx)
                } else {
                    None
                }
            });
        let (npc, team, player_slot, team_code) = match header_idx {
            Some(i) => {
                let p = &players[i];
                (
                    p.hero_npc.clone().unwrap_or_else(|| hero_class_to_npc(&class)),
                    p.team_code.and_then(team_text).map(str::to_string),
                    Some(p.player_slot),
                    p.team_code,
                )
            }
            None => (hero_class_to_npc(&class), None, None, None),
        };
        // entity_id must be unique per match (primary key). Real matches have
        // exactly one hero per npc; the counter guards exotic custom modes.
        let seen = npc_occurrences.entry(npc.clone()).or_insert(0);
        let entity_id = if *seen == 0 {
            npc.clone()
        } else {
            format!("{npc}#{}", *seen + 1)
        };
        *seen += 1;

        for s in samples.values() {
            let mut extra = model::snapshot_extra(
                &class,
                s.z,
                if s.pid == INVALID_PLAYER_ID {
                    None
                } else {
                    Some(s.pid)
                },
                player_slot,
                team_code,
            );
            // resource bars for the replay viewer (module B): only present when
            // the replay exposes the fields
            if let Some(v) = s.hp_max {
                extra["hp_max"] = serde_json::Value::from(v);
            }
            if let Some(v) = s.mana {
                extra["mana"] = serde_json::Value::from(v);
            }
            if let Some(v) = s.mana_max {
                extra["mana_max"] = serde_json::Value::from(v);
            }
            rows.push(SnapshotRow {
                game_time_sec: s.t,
                entity_type: "hero",
                entity_id: entity_id.clone(),
                team: team.clone(),
                x: s.x,
                y: s.y,
                hp: s.hp,
                extra,
            });
        }
    }
    rows
}

/// Assemble `game_events` rows for purchase events.
/// Parse one replay fully and return rows for all three tables.
///
/// `fallback_match_id` is used only when the .dem header carries no match id
/// (usually derived from the file name by the caller).
pub fn parse_replay(
    bytes: &[u8],
    interval_sec: u32,
    fallback_match_id: Option<i64>,
) -> anyhow::Result<ParsedReplay> {
    let mut parser = Parser::new(bytes)?;
    let info = parser.replay_info().clone();

    // --- header: match id + players ---
    let header_match_id = info
        .game_info
        .as_ref()
        .and_then(|g| g.dota.as_ref())
        .and_then(|d| d.match_id);
    let players = header_players(&parser);

    let interval_ticks = (TICK_RATE * interval_sec).max(1);

    // --- extractors ---
    let combat = parser.register_observer::<CombatLogExtractor>();
    let position = parser.register_observer::<PositionExtractor>();
    let ward = parser.register_observer::<WardExtractor>();
    let building = parser.register_observer::<BuildingExtractor>();
    let networth = parser.register_observer::<NetWorthExtractor>();
    // Ability/item cooldown + knowledge layer. It was written for the Q5-era
    // schema and left unregistered when combat narrative moved into
    // `combat_log`; Q7's skill-cooldown panel needs it, and a freshly parsed
    // personal replay has no legacy database to fall back on, so it is
    // registered again. Events land in `game_events` as ability_known /
    // ability_learn / ability_cd_start / ability_cd_end / item_* / smoke_count.
    let ability = parser.register_observer::<AbilityExtractor>();
    // Jungle/gold layer (neutral kills + per-hero gold timeline). Also written for
    // the Q5-era schema and left unregistered by the combat-log rewrite; needed so
    // a freshly parsed replay is equivalent to the league corpus for the analyses
    // that read game_events (gold timeline, neutral kills).
    let jungle = parser.register_observer::<JungleExtractor>();

    // Sampling is gated on whole-second change inside the extractor, so no
    // interval injection is needed. (`interval_sec` is kept in the signature
    // for CLI compatibility; values >1 are effectively "at most 1 Hz".)
    let _ = interval_ticks;

    parser.run_to_end()?;
    // sort the dense (raw tick -> cle) clock so ward_placed can map to cle.
    combat
        .borrow_mut()
        .clock
        .sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    let combat = combat.borrow();
    let position = position.borrow();
    let ward = ward.borrow();
    let building = building.borrow();
    let networth = networth.borrow();
    let ability = ability.borrow();
    let jungle = jungle.borrow();

    // --- assemble ---
    let combat_rows = combat.rows.clone();
    let game_start_cl = combat.game_start_cl;
    let identity_rows = build_identity_rows(&players);
    let mut snapshot_rows = build_snapshot_rows(&players, &position.samples);
    snapshot_rows.extend(build_networth_snapshot_rows(&networth.samples));
    snapshot_rows.extend(build_team_networth_rows(&networth.team_networth));
    snapshot_rows.extend(build_ward_snapshot_rows(&ward.samples));
    // Combat narrative now lives in `combat_log` (all types). `game_events` keeps
    // the entity-anchored space/state layer: ward_placed(实体) + building + 号角锚点,
    // plus the ability/item cooldown + knowledge layer (Q7 技能 CD 面板要用).
    let mut event_rows = build_ward_event_rows(&ward.placed, game_start_cl);
    event_rows.extend(build_building_event_rows(&building.events));
    event_rows.extend(build_ability_event_rows(&ability.events, &players));
    event_rows.extend(build_jungle_event_rows(&jungle.kills, &jungle.golds));

    // entity-level counts (by resolved entity_id) for the console log
    let mut entity_log: Vec<(String, usize, i64, i64)> = Vec::new();
    {
        let mut per_entity: HashMap<&str, (usize, i64, i64)> = HashMap::new();
        for r in &snapshot_rows {
            let e = per_entity.entry(r.entity_id.as_str()).or_insert((0, i64::MAX, i64::MIN));
            e.0 += 1;
            e.1 = e.1.min(r.game_time_sec);
            e.2 = e.2.max(r.game_time_sec);
        }
        let mut v: Vec<_> = per_entity.into_iter().collect();
        v.sort_by_key(|(k, _)| k.to_string());
        for (k, (n, lo, hi)) in v {
            entity_log.push((k.to_string(), n, lo, hi));
        }
    }

    let match_id = match header_match_id {
        Some(v) => v as i64,
        None => fallback_match_id.unwrap_or_default(),
    };

    Ok(ParsedReplay {
        match_id,
        duration_seconds: info.playback_time.map(f64::from),
        header_players: players,
        identity_rows,
        snapshot_rows,
        event_rows,
        combat_rows,
        entity_log,
    })
}
