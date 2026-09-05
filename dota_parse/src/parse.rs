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
use std::collections::{BTreeMap, HashMap};

use crate::model::{
    self, hero_class_to_npc, team_text, EventRow, PlayerIdentityRow, SnapshotRow, DIRE_SLOT_BASE,
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

/// Purchase extractor: emits raw purchase records from the combat log.
#[derive(Default)]
struct PurchaseExtractor {
    purchases: Vec<(f64, String, String, u32)>,
}

#[observer]
#[uses_all]
impl PurchaseExtractor {
    #[on_combat_log]
    fn on_combat_log(&mut self, _ctx: &Context, cle: &CombatLogEntry) -> ObserverResult {
        if cle.r#type() != DotaCombatlogTypes::DotaCombatlogPurchase {
            return Ok(());
        }
        let t = cle.timestamp().unwrap_or_default() as f64;
        // For purchase entries the buyer is recorded as the target; fall back
        // to the attacker name if the target is missing.
        let buyer = cle
            .target_name()
            .or_else(|_| cle.attacker_name())
            .unwrap_or("unknown")
            .to_string();
        let item = cle.value_name().unwrap_or("").to_string();
        let raw_index = cle.value().unwrap_or(0);
        self.purchases.push((t, buyer, item, raw_index));
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
}

#[derive(Debug, Clone)]
struct WardPlaced {
    t: f64,
    ward_type: &'static str,
    class: String,
    x: f64,
    y: f64,
    team_code: Option<i32>,
}

#[derive(Debug, Clone)]
struct WardDestroyed {
    t: f64,
    ward_type: &'static str,
    unit: String,
    actor: Option<String>,
    self_expired: bool,
    team_code: Option<i32>,
}

fn ward_class_type(class: &str) -> Option<&'static str> {
    match class {
        "CDOTA_NPC_Observer_Ward" => Some("observer"),
        "CDOTA_NPC_Observer_Ward_TrueSight" => Some("sentry"),
        _ => None,
    }
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
        self.placed.push(WardPlaced {
            t: f64::from(ctx.tick()) / f64::from(TICK_RATE),
            ward_type,
            class: class.to_string(),
            x,
            y,
            team_code: try_property!(entity, i32, "m_iTeamNum"),
        });
        Ok(())
    }

    #[on_combat_log]
    fn on_combat_log(&mut self, _ctx: &Context, cle: &CombatLogEntry) -> ObserverResult {
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
        self.destroyed.push(WardDestroyed {
            t: cle.timestamp().unwrap_or_default() as f64,
            ward_type,
            unit,
            actor,
            self_expired,
            team_code,
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
// ability cooldown extractor (技能冷却追踪)
// ---------------------------------------------------------------------------

/// One ability-cooldown transition (enter/leave cooldown) of one hero ability.
struct AbilityEvent {
    t: i64,
    pid: u32,         // m_iPlayerID (2 x header index)
    ability: String,  // e.g. "CDOTA_Ability_Invoker_ForgeSpirit"
    start: bool,
    remaining: f32,   // cooldown seconds left at the transition (len at start)
}

/// Ability extractor: per whole second, for every hero entity read its
/// `m_vecAbilities.*` ability handles, look up each ability entity's current
/// `m_fCooldown` (seconds remaining) and emit `ability_cd_start` /
/// `ability_cd_end` on cooldown transitions. Passive/innate abilities never
/// leave cooldown (m_fCooldown stays 0) so they are naturally filtered out.
/// Sampled on the parity-proof whole-second gate like heroes/buildings.
struct AbilityExtractor {
    /// (hero pid, ability class) -> when its current cooldown started
    active: HashMap<(u32, String), i64>,
    events: Vec<AbilityEvent>,
    last_sec: i64,
}

impl Default for AbilityExtractor {
    fn default() -> Self {
        AbilityExtractor {
            active: HashMap::new(),
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
        // pass 1: ability handle -> (class, remaining seconds)
        let mut abilities: HashMap<u32, (String, f32)> = HashMap::new();
        for e in ctx.entities().iter() {
            let cls = e.class().name();
            if !cls.starts_with("CDOTA_Ability_") || cls.contains("Courier") {
                continue;
            }
            let cd = try_property!(e, f32, "m_fCooldown").unwrap_or(0.0);
            abilities.insert(e.handle(), (cls.to_string(), cd));
        }
        // pass 2: per hero, iterate its ability handles and track transitions
        for e in ctx.entities().iter() {
            let cls = e.class().name();
            if !cls.starts_with("CDOTA_Unit_Hero_") {
                continue;
            }
            let pid = match try_property!(e, u32, "m_iPlayerID") {
                Some(p) => p,
                None => continue,
            };
            let mut ability_handles: Vec<u32> = Vec::new();
            for f in e.fields() {
                if f.name.starts_with("m_vecAbilities.") {
                    if let Some(v) = f.value {
                        if let source2_demo::FieldValue::Unsigned32(h) = v {
                            ability_handles.push(*h);
                        }
                    }
                }
            }
            for h in ability_handles {
                let Some((ab_cls, cd)) = abilities.get(&h) else { continue };
                let key = (pid, ab_cls.clone());
                if cd > &0.0 {
                    if !self.active.contains_key(&key) {
                        self.events.push(AbilityEvent {
                            t,
                            pid,
                            ability: ab_cls.clone(),
                            start: true,
                            remaining: *cd,
                        });
                        self.active.insert(key, t);
                    }
                } else if let Some(start) = self.active.remove(&key) {
                    self.events.push(AbilityEvent {
                        t,
                        pid,
                        ability: ab_cls.clone(),
                        start: false,
                        remaining: 0.0,
                    });
                    let _ = start;
                }
            }
        }
        Ok(())
    }
}

/// Assemble `game_events` rows for ability cooldown transitions (resolving the
/// hero npc via the header players: pid = 2 x header index).
fn build_ability_event_rows(events: &[AbilityEvent], players: &[HeaderPlayer]) -> Vec<EventRow> {
    let mut rows = Vec::new();
    let mut seq: HashMap<(&'static str, String, i64), i64> = HashMap::new();
    for ev in events {
        let hero = (ev.pid as usize / 2)
            .checked_sub(0)
            .and_then(|i| players.get(i))
            .and_then(|p| p.hero_npc.clone())
            .unwrap_or_default();
        let etype: &'static str = if ev.start { "ability_cd_start" } else { "ability_cd_end" };
        let key = (etype, hero.clone(), ev.t);
        let n = seq.entry(key).or_insert(0);
        let event_seq = *n;
        *n += 1;
        rows.push(EventRow {
            game_time_sec: ev.t,
            event_type: etype,
            actor_id: if hero.is_empty() { None } else { Some(hero.clone()) },
            target_id: Some(ev.ability.clone()),
            x: None,
            y: None,
            properties: serde_json::json!({
                "hero": hero,
                "ability": ev.ability,
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
fn build_ward_event_rows(
    placed: &[WardPlaced],
    destroyed: &[WardDestroyed],
) -> Vec<EventRow> {
    let mut rows = Vec::new();
    // (event_type, actor-group, second) -> next event_seq
    let mut seq: HashMap<(&'static str, String, i64), i64> = HashMap::new();
    for p in placed {
        let sec = p.t.floor() as i64;
        let key = ("ward_placed", String::new(), sec);
        let n = seq.entry(key).or_insert(0);
        let event_seq = *n;
        *n += 1;
        rows.push(EventRow {
            game_time_sec: sec,
            event_type: "ward_placed",
            actor_id: None, // placer not resolvable from the ward entity yet
            target_id: Some(p.class.clone()),
            x: Some(p.x),
            y: Some(p.y),
            properties: serde_json::json!({
                "ward_type": p.ward_type,
                "team": p.team_code,
            }),
            event_seq,
        });
    }
    for d in destroyed {
        let sec = d.t.floor() as i64;
        let key = ("ward_destroyed", d.actor.clone().unwrap_or_default(), sec);
        let n = seq.entry(key).or_insert(0);
        let event_seq = *n;
        *n += 1;
        rows.push(EventRow {
            game_time_sec: sec,
            event_type: "ward_destroyed",
            actor_id: d.actor.clone(),
            target_id: Some(d.unit.clone()),
            x: None,
            y: None,
            properties: serde_json::json!({
                "ward_type": d.ward_type,
                "reason": if d.self_expired { "expired" } else { "dewarded" },
                "team": d.team_code,
            }),
            event_seq,
        });
    }
    rows
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
fn build_event_rows(purchases: &[(f64, String, String, u32)]) -> Vec<EventRow> {
    let mut rows = Vec::new();
    // (event_type, actor, second) -> next event_seq
    let mut seq: HashMap<(&'static str, &str, i64), i64> = HashMap::new();
    for (t, buyer, item, raw_index) in purchases {
        let sec = t.floor() as i64;
        let key = ("purchase", buyer.as_str(), sec);
        let n = seq.entry(key).or_insert(0);
        let event_seq = *n;
        *n += 1;
        rows.push(EventRow {
            game_time_sec: sec,
            event_type: "purchase",
            actor_id: Some(buyer.clone()),
            target_id: None,
            x: None,
            y: None,
            properties: serde_json::json!({
                "item": item,
                "item_index": raw_index, // combat-log internal item index, not gold cost
            }),
            event_seq,
        });
    }
    rows
}

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
    let position = parser.register_observer::<PositionExtractor>();
    let purchase = parser.register_observer::<PurchaseExtractor>();
    let ward = parser.register_observer::<WardExtractor>();
    let building = parser.register_observer::<BuildingExtractor>();
    let ability = parser.register_observer::<AbilityExtractor>();

    // Sampling is gated on whole-second change inside the extractor, so no
    // interval injection is needed. (`interval_sec` is kept in the signature
    // for CLI compatibility; values >1 are effectively "at most 1 Hz".)
    let _ = interval_ticks;

    parser.run_to_end()?;
    let position = position.borrow();
    let purchase = purchase.borrow();
    let ward = ward.borrow();
    let building = building.borrow();
    let ability = ability.borrow();

    // --- assemble ---
    let identity_rows = build_identity_rows(&players);
    let snapshot_rows = build_snapshot_rows(&players, &position.samples);
    let mut event_rows = build_event_rows(&purchase.purchases);
    event_rows.extend(build_ward_event_rows(&ward.placed, &ward.destroyed));
    event_rows.extend(build_building_event_rows(&building.events));
    event_rows.extend(build_ability_event_rows(&ability.events, &players));

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
        entity_log,
    })
}
