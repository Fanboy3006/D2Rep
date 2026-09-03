//! dota_parse — formal .dem parser (ARCHITECTURE.md §8 step 4).
//!
//! Usage: dota_parse <replay.dem> [output.db] [sample_interval_sec]
//!        dota_parse --info <replay.dem>   (header-only, prints JSON, no DB)
//!
//! Parses a Dota 2 replay with source2-demo (verified stack, §6.6) and writes
//! the generic three-table model of §6.2 — `entity_snapshots` /
//! `game_events` / `player_identity` — into a SQLite database instead of a
//! single JSON file. Re-parsing the same match overwrites its rows
//! idempotently (delete + insert inside one transaction).
//!
//! Requires `sqlite3.dll` (official prebuilt, sqlite.org) to be discoverable —
//! see `sqlite::dll_candidates` and the DOTA_PARSE_SQLITE_DLL env var.
//! Postgres migration later only swaps the storage layer; row/schema model
//! (§6.2) stays identical.

mod model;
mod parse;
mod schema;
mod sqlite;

use anyhow::{bail, Context as _, Result};
use std::path::PathBuf;
use std::time::Instant;

use crate::parse::parse_replay;
use crate::sqlite::{Db, PreparedWriter, Step};

const DEFAULT_DEM: &str = "8592126358.dem";

fn usage() -> ! {
    eprintln!(
        "usage: dota_parse <replay.dem> [output.db] [sample_interval_sec]\n\
         \x20      dota_parse --info <replay.dem>\n\
         \n\
         examples:\n\
         \x20 dota_parse 8592126358.dem                     -> 8592126358.db next to the demo\n\
         \x20 dota_parse in.dem out.db 1                    -> explicit db, 1s resampling\n\
         \x20 dota_parse --info in.dem                      -> header-only JSON (catalog registration)\n\
         \n\
         sqlite3.dll is resolved via DOTA_PARSE_SQLITE_DLL, the exe dir, the\n\
         working dir, or the OS search path."
    );
    std::process::exit(2);
}

fn fallback_match_id(dem: &std::path::Path) -> Option<i64> {
    dem.file_stem()
        .and_then(|s| s.to_str())
        .and_then(|s| s.parse::<i64>().ok())
}

/// Sanity-check the rows we just collected before writing (cheap invariants).
fn sanity_checks(parsed: &parse::ParsedReplay) {
    let bad = parsed
        .snapshot_rows
        .iter()
        .filter(|r| !r.x.is_finite() || !r.y.is_finite() || r.x.abs() > 200_000.0 || r.y.abs() > 200_000.0)
        .count();
    if bad > 0 {
        println!("[warn] {bad} snapshot rows have out-of-range/non-finite coordinates");
    }
    if parsed.snapshot_rows.is_empty() {
        println!("[warn] no position snapshots were collected");
    }
    if parsed.event_rows.is_empty() {
        println!("[warn] no game events were collected");
    }
}

fn run() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();

    // ------------------------------------------------------------------
    // --info: header-only read (fast) -> JSON on stdout. Used by the
    // scheduler layer (scheduler/intake_private.py) to register a replay
    // from its header without a full parse.
    // ------------------------------------------------------------------
    if args.get(1).map(String::as_str) == Some("--info") {
        if args.len() != 3 {
            usage();
        }
        let dem_path = PathBuf::from(&args[2]);
        let bytes = std::fs::read(&dem_path)
            .with_context(|| format!("reading {}", dem_path.display()))?;
        let info = parse::parse_header(&bytes)
            .map_err(|e| anyhow::anyhow!("header parse failed: {e:#}"))?;
        let players: Vec<serde_json::Value> = info
            .players
            .iter()
            .map(|p| {
                serde_json::json!({
                    "index": p.header_index,
                    "slot": p.player_slot,
                    "steam_id": p.steam_id,
                    "player_name": p.player_name,
                    "hero_npc": p.hero_npc,
                    "team_code": p.team_code,
                    "fake_client": p.is_fake_client,
                })
            })
            .collect();
        let out = serde_json::json!({
            "file": dem_path.display().to_string(),
            "match_id": info.match_id,
            "duration_seconds": info.duration_seconds,
            "players": players,
        });
        println!("{}", serde_json::to_string_pretty(&out)?);
        return Ok(());
    }

    if args.len() > 4 {
        usage();
    }
    let dem_path = PathBuf::from(args.get(1).map(String::as_str).unwrap_or(DEFAULT_DEM));
    let db_path = match args.get(2) {
        Some(p) => PathBuf::from(p),
        None => dem_path.with_extension("db"),
    };
    let interval_sec: u32 = match args.get(3) {
        Some(s) => s
            .parse()
            .context("sample_interval_sec must be a positive integer")?,
        None => 1,
    };
    if interval_sec == 0 {
        bail!("sample_interval_sec must be >= 1");
    }

    println!("[input]  {} (resampling: every {interval_sec}s)", dem_path.display());
    let started = Instant::now();

    let bytes = std::fs::read(&dem_path)
        .with_context(|| format!("reading {}", dem_path.display()))?;
    println!("[input]  loaded {} bytes", bytes.len());

    let fallback = fallback_match_id(&dem_path);
    let parsed = parse_replay(&bytes, interval_sec, fallback)
        .map_err(|e| anyhow::anyhow!("parse failed: {e:#}"))?;
    println!("[parse]  finished in {:.1}s", started.elapsed().as_secs_f64());

    let p = &parsed;
    println!("[match]  match_id={} duration={:?}s players={}",
        p.match_id,
        p.duration_seconds.map(|d| format!("{d:.0}")).unwrap_or_else(|| "?".into()),
        p.header_players.len());
    for pl in &p.header_players {
        println!(
            "  idx={:<2} slot={:>3} steam={:<17} team={:?} fake={:<5} hero={:?} name={}",
            pl.header_index,
            pl.player_slot,
            pl.steam_id.map(|v| v.to_string()).unwrap_or_default(),
            pl.team_code,
            pl.is_fake_client,
            pl.hero_npc,
            pl.player_name
        );
    }
    println!("[extract] identity={} snapshot_rows={} event_rows={}",
        p.identity_rows.len(), p.snapshot_rows.len(), p.event_rows.len());
    println!("[extract] positions per entity (entity_id, samples, first_s, last_s):");
    for (id, n, lo, hi) in &p.entity_log {
        println!("    {id:<48} {n:>5}  [{lo}, {hi}]");
    }
    let mut by_type: Vec<(&str, usize)> = Vec::new();
    for r in &p.event_rows {
        match by_type.iter_mut().find(|(t, _)| *t == r.event_type) {
            Some((_, n)) => *n += 1,
            None => by_type.push((r.event_type, 1)),
        }
    }
    println!("[extract] events by type: {:?}", by_type);
    sanity_checks(p);

    // ------------------------------------------------------------------
    // write to SQLite
    // ------------------------------------------------------------------
    println!("\n[db]     opening {}", db_path.display());
    let db = Db::open(&db_path)?;
    db.exec(schema::SCHEMA_SQL)
        .context("applying schema")?;

    let mut writer = PreparedWriter::begin(&db, p.match_id).context("starting write transaction")?;
    for row in &p.identity_rows {
        writer.insert_player(p.match_id, row)?;
    }
    for row in &p.snapshot_rows {
        writer.insert_snapshot(p.match_id, row)?;
    }
    for row in &p.event_rows {
        writer.insert_event(p.match_id, row)?;
    }
    let stats = writer.commit().context("committing write transaction")?;
    println!(
        "[db]     committed: {} snapshots, {} events, {} identity rows (match_id={})",
        stats.snapshots, stats.events, stats.players, p.match_id
    );

    // ------------------------------------------------------------------
    // verify from the database itself (read back what we wrote)
    // ------------------------------------------------------------------
    verify_from_db(&db, p.match_id)?;
    println!(
        "[done]   {} in {:.1}s",
        db_path.display(),
        started.elapsed().as_secs_f64()
    );
    Ok(())
}

/// Read back the rows of one match from the database and cross-check counts,
/// team sides and coordinate plausibility.
fn verify_from_db(db: &Db, match_id: i64) -> Result<()> {
    println!("\n[verify] reading back from the database…");
    let counts = [
        ("entity_snapshots", "SELECT COUNT(*) FROM entity_snapshots WHERE match_id = ?1"),
        ("game_events", "SELECT COUNT(*) FROM game_events WHERE match_id = ?1"),
        ("player_identity", "SELECT COUNT(*) FROM player_identity WHERE match_id = ?1"),
    ];
    for (name, sql) in counts {
        let st = db.prepare(sql)?;
        st.bind_int64(1, match_id)?;
        match st.step()? {
            Step::Row => println!("[verify] {name:<16} rows = {}", st.column_i64(0)),
            Step::Done => println!("[verify] {name:<16} rows = 0"),
        }
    }

    // Per-entity position summary. Fountain sanity check: radiant fountain is
    // in the negative coordinate quadrant, dire in the positive one.
    println!("[verify] per-entity snapshot summary (entity, samples, x∈[min,max], y∈[min,max]):");
    {
        let sql = "SELECT entity_id, team,
                          COUNT(*), MIN(x), MAX(x), MIN(y), MAX(y),
                          MIN(game_time_sec), MAX(game_time_sec)
                   FROM entity_snapshots WHERE match_id = ?1
                   GROUP BY entity_id, team ORDER BY entity_id";
        let st = db.prepare(sql)?;
        st.bind_int64(1, match_id)?;
        while st.step()? == Step::Row {
            let side = st.column_str(1);
            println!(
                "    {:<46} {:<8} n={:<5} x=[{:>9.0}, {:>9.0}] y=[{:>9.0}, {:>9.0}] t=[{},{:}]",
                st.column_str(0),
                if side.is_empty() { "—".to_string() } else { side },
                st.column_i64(2),
                st.column_f64(3),
                st.column_f64(4),
                st.column_f64(5),
                st.column_f64(6),
                st.column_i64(7),
                st.column_i64(8),
            );
        }
    }

    println!("[verify] events by type:");
    {
        let sql = "SELECT event_type, COUNT(*) FROM game_events WHERE match_id = ?1 GROUP BY event_type ORDER BY event_type";
        let st = db.prepare(sql)?;
        st.bind_int64(1, match_id)?;
        while st.step()? == Step::Row {
            println!("    {:<16} n={}", st.column_str(0), st.column_i64(1));
        }
    }

    println!("[verify] first 3 purchase events:");
    {
        let sql = "SELECT game_time_sec, actor_id, properties
                   FROM game_events WHERE match_id = ?1 AND event_type = 'purchase'
                   ORDER BY game_time_sec, event_seq LIMIT 3";
        let st = db.prepare(sql)?;
        st.bind_int64(1, match_id)?;
        while st.step()? == Step::Row {
            println!(
                "    t={:<6} actor={:<46} props={}",
                st.column_i64(0),
                st.column_str(1),
                st.column_str(2)
            );
        }
    }

    println!("[verify] player_identity rows:");
    {
        let sql = "SELECT player_slot, steam_id, player_name, hero_name, team_id
                   FROM player_identity WHERE match_id = ?1 ORDER BY player_slot";
        let st = db.prepare(sql)?;
        st.bind_int64(1, match_id)?;
        while st.step()? == Step::Row {
            println!(
                "    slot={:>3} steam={:<17} team={:<5} hero={:<40} name={}",
                st.column_i64(0),
                st.column_str(1),
                st.column_str(4),
                st.column_str(3),
                st.column_str(2),
            );
        }
    }
    Ok(())
}

fn main() {
    // Deep recursion inside source2-demo needs a larger stack than the 1 MiB
    // main-thread default (verified in §6.6); run everything on a 64 MiB stack.
    let outcome = std::thread::Builder::new()
        .stack_size(64 * 1024 * 1024)
        .spawn(run)
        .expect("spawning parser thread")
        .join();
    match outcome {
        Ok(Ok(())) => {}
        Ok(Err(e)) => {
            eprintln!("error: {e:#}");
            std::process::exit(1);
        }
        Err(pan) => {
            eprintln!("error: parser thread panicked: {pan:?}");
            std::process::exit(1);
        }
    }
}
