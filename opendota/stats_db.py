#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stats_db.py - storage for public-match standard metrics
(ARCHITECTURE.md section 8 step 1 / layer 5 "standard indicators").

A SEPARATE database from the scheduler catalog (scheduler/catalog.py ->
matches.db). Responsibilities:
  - catalog  = scheduling/index layer for locally parsed replays
               (source private/public, dem/db paths, parse state)
  - stats.db = public-tournament STANDARD METRICS produced by the OpenDota
               pipeline (opendota/fetch_league.py): match list, team-name
               dictionary, per-minute radiant gold advantage.

Association: a catalog row with source='public' and a stats.db row share the
same match_id - join in the application layer or with SQLite ATTACH; no
foreign-key coupling (see join_catalog_rows()).

Win/loss granularity: per MATCH (matches.radiant_win). No series aggregation
in the schema; a series view is a query-layer concern later.

Schema-evolution note (same principle as catalog.py): fixed core columns only
for what the layer is known to need; anything optional/extensible goes into
`metadata_json` (TEXT json) - no rebuild needed to add fields.

CLI:
    python opendota/stats_db.py init
    python opendota/stats_db.py summary
    python opendota/stats_db.py matches [--league 19719] [--limit 20]
    python opendota/stats_db.py teams [--limit 20]
    python opendota/stats_db.py gold <match_id> [--team radiant|dire]
    python opendota/stats_db.py join-catalog [--catalog <matches.db>]

Prints are ASCII-only on purpose (console code-page safety).
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STATS = os.path.join(ROOT, "stats.db")

DDL = """
CREATE TABLE IF NOT EXISTS leagues (
    league_id     INTEGER PRIMARY KEY,
    name          TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS teams (
    team_id       INTEGER PRIMARY KEY,
    name          TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

-- one row per public match (per-match granularity; series = query later)
CREATE TABLE IF NOT EXISTS matches (
    match_id           INTEGER PRIMARY KEY,
    league_id          INTEGER,
    radiant_team_id    INTEGER,
    dire_team_id       INTEGER,
    start_time         INTEGER,
    duration_sec       INTEGER,
    radiant_win        INTEGER,            -- 1 = radiant, 0 = dire, NULL unknown
    series_id          INTEGER,
    series_type        INTEGER,
    game_mode          INTEGER,
    fetched_at         TEXT,
    parse_requested_at TEXT,               -- last POST /request (gold_adv was missing)
    metadata_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_matches_league ON matches (league_id);

-- per-minute gold advantage, RADIANT perspective (raw API value).
-- dire-perspective value = -value; see gold_adv_for() which applies the
-- sign flip from ARCHITECTURE.md section 3.1 team_adv().
CREATE TABLE IF NOT EXISTS gold_adv (
    match_id INTEGER NOT NULL,
    minute   INTEGER NOT NULL,
    value    REAL NOT NULL,
    PRIMARY KEY (match_id, minute)
);
"""


def default_stats_path():
    return DEFAULT_STATS


def connect(path=None):
    con = sqlite3.connect(path or DEFAULT_STATS)
    con.row_factory = sqlite3.Row
    return con


def ensure_schema(con):
    con.executescript(DDL)
    con.commit()


def _meta(metadata):
    return json.dumps(dict(metadata or {}), ensure_ascii=False)


def upsert_league(con, league_id, name=None):
    if league_id is None:
        return
    if name:
        con.execute("INSERT OR IGNORE INTO leagues (league_id, name) VALUES (?,?)",
                    (league_id, name))
    else:
        con.execute("INSERT OR IGNORE INTO leagues (league_id) VALUES (?)",
                    (league_id,))


def upsert_team(con, team_id, name=None):
    if team_id is None:
        return False
    if name:
        con.execute("""INSERT INTO teams (team_id, name) VALUES (?,?)
                       ON CONFLICT(team_id) DO UPDATE SET name=excluded.name""",
                    (team_id, name))
    else:
        con.execute("INSERT OR IGNORE INTO teams (team_id) VALUES (?)",
                    (team_id,))
    return True


def team_name(con, team_id):
    r = con.execute("SELECT name FROM teams WHERE team_id=?", (team_id,)).fetchone()
    return r["name"] if r else None


def upsert_match(con, row, metadata=None):
    """row keys: match_id, league_id, radiant_team_id, dire_team_id,
    start_time, duration_sec, radiant_win, series_id, series_type, game_mode."""
    con.execute("""INSERT INTO matches (match_id, league_id, radiant_team_id,
                   dire_team_id, start_time, duration_sec, radiant_win,
                   series_id, series_type, game_mode, fetched_at, metadata_json)
               VALUES (:match_id,:league_id,:radiant_team_id,:dire_team_id,
                       :start_time,:duration_sec,:radiant_win,:series_id,
                       :series_type,:game_mode,:fetched_at,:meta)
               ON CONFLICT(match_id) DO UPDATE SET
                   league_id=excluded.league_id,
                   radiant_team_id=excluded.radiant_team_id,
                   dire_team_id=excluded.dire_team_id,
                   start_time=excluded.start_time,
                   duration_sec=excluded.duration_sec,
                   radiant_win=excluded.radiant_win,
                   series_id=excluded.series_id,
                   series_type=excluded.series_type,
                   game_mode=excluded.game_mode,
                   fetched_at=excluded.fetched_at,
                   metadata_json=excluded.metadata_json""",
                dict(row, fetched_at=datetime.datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"), meta=_meta(metadata)))


def mark_parse_requested(con, match_id):
    con.execute("UPDATE matches SET parse_requested_at=? WHERE match_id=?",
                (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), match_id))


def set_gold_adv(con, match_id, minute_values):
    """minute_values: iterable of (minute, value); value None entries skipped.
    Returns number of minutes stored."""
    n = 0
    for minute, value in minute_values:
        if value is None:
            continue
        con.execute("INSERT OR REPLACE INTO gold_adv (match_id, minute, value) "
                    "VALUES (?,?,?)", (match_id, minute, float(value)))
        n += 1
    return n


def has_gold_adv(con, match_id):
    r = con.execute("SELECT COUNT(*) FROM gold_adv WHERE match_id=?",
                    (match_id,)).fetchone()[0]
    return r > 0


def gold_adv_for(con, match_id, is_radiant):
    """Per-minute gold advantage from one side's perspective, mirroring
    team_adv() in ARCHITECTURE.md section 3.1: dire view = -radiant view."""
    rows = con.execute(
        "SELECT minute, value FROM gold_adv WHERE match_id=? ORDER BY minute",
        (match_id,)).fetchall()
    return [(r["minute"], r["value"] if is_radiant else -r["value"]) for r in rows]


def match_row(con, match_id):
    r = con.execute("SELECT * FROM matches WHERE match_id=?", (match_id,)).fetchone()
    return dict(r) if r else None


def list_matches(con, league_id=None, limit=None):
    sql = "SELECT * FROM matches"
    args = []
    if league_id:
        sql += " WHERE league_id=?"
        args.append(league_id)
    sql += " ORDER BY start_time DESC, match_id DESC"
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def join_catalog_rows(con, catalog_path):
    """App-layer association demo: stats matches that also have a catalog row
    with source='public' (share match_id as TEXT). Returns joined rows."""
    if not os.path.exists(catalog_path):
        return []
    cat = sqlite3.connect(catalog_path)
    cat.row_factory = sqlite3.Row
    ids = [r["match_id"] for r in cat.execute(
        "SELECT match_id FROM matches WHERE source='public'").fetchall()]
    cat.close()
    out = []
    for mid in ids:
        try:
            m = match_row(con, int(mid))
        except (TypeError, ValueError):
            m = None
        if m:
            out.append((mid, m))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["init", "summary", "matches", "teams",
                                        "gold", "join-catalog"])
    ap.add_argument("match_id", nargs="?", type=int, default=None)
    ap.add_argument("--stats", default=None, help="stats db path")
    ap.add_argument("--league", type=int, default=None)
    ap.add_argument("--team", choices=["radiant", "dire"], default="radiant")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--catalog", default=None,
                    help="matches catalog db (join-catalog)")
    return ap.parse_args()


def main():
    args = _parse_args()
    path = args.stats or DEFAULT_STATS
    con = connect(path)
    ensure_schema(con)
    if args.command == "init":
        print("stats db ready: %s" % path)
        return 0
    if args.command == "summary":
        for t in ("leagues", "teams", "matches", "gold_adv"):
            print("%-10s %d" % (t, con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]))
        withgold = con.execute(
            "SELECT COUNT(DISTINCT match_id) FROM gold_adv").fetchone()[0]
        print("matches-with-gold_adv: %d" % withgold)
        return 0
    if args.command == "matches":
        rows = list_matches(con, league_id=args.league, limit=args.limit)
        print("matches (league=%s, limit=%s):" % (args.league, args.limit))
        for m in rows:
            rw = ("R" if m["radiant_win"] == 1 else
                  "D" if m["radiant_win"] == 0 else "?")
            print("  %-12s %s  t=%-6s d=%-5ss  %s vs %s" % (
                m["match_id"],
                rw,
                datetime.datetime.utcfromtimestamp(m["start_time"] or 0)
                .strftime("%m-%d") if m["start_time"] else "-",
                m["duration_sec"] or "-",
                team_name(con, m["radiant_team_id"]) or m["radiant_team_id"],
                team_name(con, m["dire_team_id"]) or m["dire_team_id"]))
        return 0
    if args.command == "teams":
        rows = con.execute("SELECT team_id, name FROM teams ORDER BY team_id "
                           "LIMIT ?", (args.limit,)).fetchall()
        print("teams (limit=%s):" % args.limit)
        for r in rows:
            print("  %-8s %s" % (r["team_id"], r["name"]))
        return 0
    if args.command == "gold":
        if not args.match_id:
            print("need match_id")
            return 2
        rows = gold_adv_for(con, args.match_id, args.team == "radiant")
        print("match %s gold_adv (%s perspective, %d minutes):" %
              (args.match_id, args.team, len(rows)))
        if rows:
            print("  " + ", ".join("m%d=%d" % (mn, round(v))
                                   for mn, v in rows[:20]))
            if len(rows) > 20:
                print("  ... (%d more)" % (len(rows) - 20))
        return 0
    if args.command == "join-catalog":
        catalog = args.catalog or os.path.join(ROOT, "matches.db")
        joined = join_catalog_rows(con, catalog)
        print("stats matches that also have a catalog source='public' row:")
        if not joined:
            print("  (none - public catalog rows appear once replays are "
                  "registered for download, step 2)")
        for mid, m in joined:
            print("  %s radiant_win=%s" % (mid, m["radiant_win"]))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
