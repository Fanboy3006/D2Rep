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
    self, hero_class_to_npc, hero_npc_to_class, team_text, EventRow, PlayerIdentityRow,
    SnapshotRow, DIRE_SLOT_BASE,
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

impl HeaderPlayer {
    pub fn hero_class(&self) -> Option<String> {
        self.hero_npc.as_deref().map(hero_npc_to_class)
    }
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
    pid: u32,
}

/// Position extractor: emits one `HeroSample` per hero per whole second.
///
/// The demo tick stream occasionally repeats a tick/second (observed on the
/// verified replay), so samples are stored in a per-second map: the latest
/// occurrence of a second wins, matching the snapshot-table primary key
/// `(match_id, entity_id, game_time_sec)` = one state per entity per second.
#[derive(Default)]
struct PositionExtractor {
    /// hero entity class name -> (whole second -> latest sample that second)
    samples: HashMap<String, BTreeMap<i64, HeroSample>>,
    interval_ticks: u32,
}

#[observer]
#[uses_all]
impl PositionExtractor {
    #[on_tick_start]
    fn on_tick_start(&mut self, ctx: &Context) -> ObserverResult {
        let tick = ctx.tick();
        if tick % self.interval_ticks != 0 {
            return Ok(());
        }
        let t = i64::from(tick / TICK_RATE); // whole seconds since match start
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
            self.samples
                .entry(class)
                .or_default()
                .insert(t, HeroSample {
                    t,
                    x,
                    y,
                    z,
                    hp,
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
/// (npc / team / slot) is resolved via the header using pid = 2 × header index.
fn build_snapshot_rows(
    players: &[HeaderPlayer],
    samples_by_class: &HashMap<String, BTreeMap<i64, HeroSample>>,
) -> Vec<SnapshotRow> {
    // header hero class -> HeaderPlayer index
    let class_to_header: HashMap<String, usize> = players
        .iter()
        .enumerate()
        .filter_map(|(i, p)| p.hero_class().map(|c| (c, i)))
        .collect();

    let mut rows = Vec::new();
    let mut npc_occurrences: HashMap<String, u32> = HashMap::new();

    for (class, samples) in samples_by_class {
        let class = class.clone();
        let header_idx = class_to_header.get(&class).copied();
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
            rows.push(SnapshotRow {
                game_time_sec: s.t,
                entity_type: "hero",
                entity_id: entity_id.clone(),
                team: team.clone(),
                x: s.x,
                y: s.y,
                hp: s.hp,
                extra: model::snapshot_extra(
                    &class,
                    s.z,
                    if s.pid == INVALID_PLAYER_ID {
                        None
                    } else {
                        Some(s.pid)
                    },
                    player_slot,
                    team_code,
                ),
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

    // Inject the resample interval. The observer framework constructs the
    // extractor via Default; setting the field afterwards keeps this cheap.
    position.borrow_mut().interval_ticks = interval_ticks;

    parser.run_to_end()?;
    let position = position.borrow();
    let purchase = purchase.borrow();

    // --- assemble ---
    let identity_rows = build_identity_rows(&players);
    let snapshot_rows = build_snapshot_rows(&players, &position.samples);
    let event_rows = build_event_rows(&purchase.purchases);

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
