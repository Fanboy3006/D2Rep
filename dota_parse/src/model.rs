//! Generic row model for the three tables in ARCHITECTURE.md §6.2, plus the
//! entity-naming / team conventions shared by every extractor.
//!
//! The extractor principle (§6.4): each extractor only ever emits rows of
//! [`SnapshotRow`], [`EventRow`] or [`PlayerIdentityRow`]. Adding a new
//! analysis dimension never changes this model or the schema — it adds a new
//! extractor (or a new `event_type` / `entity_type` value).

use serde_json::{Map, Value};

/// Game team codes as stored in the replay header (`CPlayerInfo.game_team`) and
/// in `player_identity.team_id`.
pub const TEAM_CODE_RADIANT: i32 = 2; // DOTA_TEAM_GOOD_GUYS
pub const TEAM_CODE_DIRE: i32 = 3; // DOTA_TEAM_BAD_GUYS

/// Dire `player_slot` base: radiant slots are 0..=4, dire slots 128..=132.
pub const DIRE_SLOT_BASE: i64 = 128;

/// Convert a game team code to the short text stored in
/// `entity_snapshots.team` ('radiant' / 'dire').
pub fn team_text(code: i32) -> Option<&'static str> {
    match code {
        TEAM_CODE_RADIANT => Some("radiant"),
        TEAM_CODE_DIRE => Some("dire"),
        _ => None,
    }
}

/// "CDOTA_Unit_Hero_Legion_Commander" -> "npc_dota_hero_legion_commander".
/// Fallback only for hero-class entities without a header player (summons and
/// the like). Real-player identity never relies on this guess: the parser
/// resolves heroes via m_iPlayerID -> header (see parse.rs), because entity
/// class strings are not guaranteed to match the npc name CamelCase-wise
/// (e.g. newer builds use `CDOTA_Unit_Hero_Spiritbreaker` while the header
/// npc is `npc_dota_hero_spirit_breaker`).
pub fn hero_class_to_npc(class: &str) -> String {
    let short = class
        .strip_prefix("CDOTA_Unit_Hero_")
        .unwrap_or(class)
        .to_ascii_lowercase();
    format!("npc_dota_hero_{short}")
}

/// One row for `entity_snapshots`. `extra` carries everything the fixed
/// columns cannot (z coordinate, entity class, m_iPlayerID, player slot, ...).
#[derive(Debug, Clone)]
pub struct SnapshotRow {
    pub game_time_sec: i64,
    pub entity_type: &'static str,
    pub entity_id: String,
    pub team: Option<String>,
    pub x: f64,
    pub y: f64,
    pub hp: Option<i64>,
    pub extra: Value,
}

/// One row for `game_events`.
#[derive(Debug, Clone)]
pub struct EventRow {
    pub game_time_sec: i64,
    pub event_type: &'static str,
    pub actor_id: Option<String>,
    pub target_id: Option<String>,
    pub x: Option<f64>,
    pub y: Option<f64>,
    pub properties: Value,
    /// Disambiguator among events sharing (match, second, type, actor).
    pub event_seq: i64,
}

/// One row for `player_identity`. One per player per match.
#[derive(Debug, Clone)]
pub struct PlayerIdentityRow {
    pub player_slot: i64,
    pub steam_id: Option<i64>,
    pub player_name: String,
    pub hero_name: String,
    /// Numeric hero id — reserved; needs the external hero dictionary (§7).
    pub hero_id: Option<i64>,
    pub team_id: Option<i64>,
}

/// Build the `extra` JSON for a hero position snapshot.
pub fn snapshot_extra(
    class: &str,
    z: Option<f64>,
    pid: Option<u32>,
    player_slot: Option<i64>,
    team_code: Option<i32>,
) -> Value {
    let mut m = Map::new();
    m.insert("class".to_string(), Value::String(class.to_string()));
    m.insert("z".to_string(), z.map_or(Value::Null, |v| Value::from(v)));
    m.insert(
        "pid".to_string(),
        pid.map_or(Value::Null, |v| Value::from(v)),
    );
    m.insert(
        "player_slot".to_string(),
        player_slot.map_or(Value::Null, |v| Value::from(v)),
    );
    m.insert(
        "team_code".to_string(),
        team_code.map_or(Value::Null, |v| Value::from(v)),
    );
    Value::Object(m)
}
