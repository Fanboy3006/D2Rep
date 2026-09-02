//! Canonical database schema for the generic three-table model defined in
//! ARCHITECTURE.md §6.2 (通用表结构：两张表覆盖任意维度 + player_identity).
//!
//! This is the SQLite dialect of the schema. The columns follow the design doc
//! exactly, with these pragmatic SQLite adaptations (each is a candidate for
//! review when the Postgres migration happens):
//!
//! 1. `JSON` columns are stored as `TEXT` (SQLite has no JSON type). Values are
//!    always serialised with `serde_json`, and Postgres later just changes the
//!    column type to `jsonb`.
//! 2. `BIGINT` -> `INTEGER`, `FLOAT` -> `REAL`.
//! 3. `game_events` adds `event_seq` to the primary key. Purchase (and future)
//!    event logs can legitimately contain several events with the same actor,
//!    type and whole-second timestamp (verified: quick-buying 2 items in one
//!    second happens regularly). `event_seq` is a per-match increasing ordinal
//!    that keeps every row addressable; §6.2 already flags this PK as
//!    "视情况调整".
//! 4. `player_identity` adds a `hero_name` column. The .dem header only
//!    contains the canonical hero npc string (`CPlayerInfo.hero_name`); the
//!    numeric hero id lives in an external hero dictionary (§7 待选型: 物品价格
//!    / hero dictionaries), so `hero_id` is reserved and nullable while
//!    `hero_name` carries the self-sufficient parse-layer value.
//! 5. `player_identity.team_id` stores the game team code from the demo header
//!    (2 = radiant, 3 = dire, matching DOTA_TEAM_GOOD_GUYS / DOTA_TEAM_BAD_GUYS).
//!    Organisation-level team ids (radiant_team_id / dire_team_id) come from the
//!    OpenDota metadata layer, not from a .dem, and live in the matches table.
//!
//! Idempotency: a parse run deletes every row for its match_id from all three
//! tables inside one transaction before inserting, so re-parsing a replay is
//! safe and does not need the primary keys to be upsert-style.

pub const SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS entity_snapshots (
    match_id      INTEGER NOT NULL,           -- 比赛ID（.dem头部 match_id）
    game_time_sec INTEGER NOT NULL,           -- 从比赛开始经过的秒数（1s 重采样）
    entity_type   TEXT    NOT NULL,           -- 'hero' / 'creep' / 'building' / 'ward' / ...
    entity_id     TEXT    NOT NULL,           -- 本场比赛内唯一实体标识（hero 用 npc 名）
    team          TEXT,                       -- 'radiant' / 'dire' / NULL
    x             REAL,                       -- 世界坐标 X
    y             REAL,                       -- 世界坐标 Y
    hp            INTEGER,                    -- 生命值（解析层尽力而为，缺省为 NULL）
    extra         TEXT,                       -- JSON：任意附加属性（z、pid、class 等），无需改表
    PRIMARY KEY (match_id, entity_id, game_time_sec)
);

CREATE TABLE IF NOT EXISTS game_events (
    match_id      INTEGER NOT NULL,
    game_time_sec INTEGER NOT NULL,           -- 事件发生秒（整数秒，来源时间戳向下取整）
    event_type    TEXT    NOT NULL,           -- 'purchase' / 'kill' / 'ward_placed' / ... 可无限扩展
    actor_id      TEXT,                       -- 触发事件的实体（购买者 hero npc）
    target_id     TEXT,                       -- 事件目标（购买事件为空，未来击杀=死者、排眼=被摧毁眼）
    x             REAL,                       -- 事件位置（无则 NULL）
    y             REAL,
    properties    TEXT,                       -- JSON：事件特有字段，如 purchase 的 item / item_index
    event_seq     INTEGER NOT NULL DEFAULT 0, -- 同 match+秒+actor+type 的去重序号（见文件头注释）
    PRIMARY KEY (match_id, game_time_sec, event_type, actor_id, event_seq)
);

CREATE TABLE IF NOT EXISTS player_identity (
    match_id    INTEGER NOT NULL,
    player_slot INTEGER NOT NULL,             -- Dota 惯例：天辉 0-4，夜魇 128-132
    steam_id    INTEGER,                      -- 64位 steam id（跨场次追踪同一人的锚点）
    player_name TEXT,
    hero_name   TEXT,                         -- 录像头部的 canonical hero npc（如 npc_dota_hero_pudge）
    hero_id     INTEGER,                      -- 数字 hero id：需外部英雄字典，暂时 NULL（见文件头注释）
    team_id     INTEGER,                      -- 队伍代码：2=天辉 3=夜魇（来自录像头部）
    PRIMARY KEY (match_id, player_slot)
);

CREATE INDEX IF NOT EXISTS idx_entity_snapshots_type ON entity_snapshots (match_id, entity_type, team);
CREATE INDEX IF NOT EXISTS idx_game_events_type     ON game_events (match_id, event_type);
"#;
