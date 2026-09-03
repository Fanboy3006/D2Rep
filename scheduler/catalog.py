#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""catalog.py - matches registry (ARCHITECTURE.md section 8 step 7, layer 1).

An independent, light index over all match databases. The catalog only stores
per-match index rows (source / paths / parse state / small facts); the heavy
generic three-table data stays in each per-match .db produced by dota_parse.
Layer-4 analysis (analysis/) enumerates match .db files through this catalog
instead of being handed a db list by hand.

match_id naming (namespace rules, see ARCHITECTURE.md step 7):
  - source='public'  -> the real OpenDota match id (decimal string)
  - source='private' -> the replay header's own match id when present
    (official-looking), else 'manual_<sha256 prefix>' derived from the file
    content so re-runs are idempotent and never collide with public ids.

Schema-evolution note: fixed core columns cover the lifecycle; anything
optional/extensible (public-event team/series placeholders, user notes,
future tournament names, ...) goes into `metadata_json` (TEXT json) so the
catalog never needs a rebuild to grow.

Usage (CLI):
    python scheduler/catalog.py init [--catalog matches.db]
    python scheduler/catalog.py list [--catalog matches.db]
             [--source private|public] [--state pending|parsed|failed] [--json]
    python scheduler/catalog.py dbs  [--catalog matches.db]
             [--source private|public] [--state parsed]     # feeds analysis/

Prints are ASCII-only on purpose (console code-page safety).
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CATALOG = os.path.join(ROOT, "matches.db")

SOURCES = ("private", "public")
STATES = ("pending", "parsed", "failed")

DDL = """
CREATE TABLE IF NOT EXISTS matches (
    match_id      TEXT PRIMARY KEY,        -- see module docstring for naming
    source        TEXT NOT NULL CHECK (source IN ('private','public')),
    dem_path      TEXT,                    -- raw .dem location (may move to registered/)
    db_path       TEXT,                    -- parsed three-table sqlite db
    parse_state   TEXT NOT NULL DEFAULT 'pending'
                  CHECK (parse_state IN ('pending','parsed','failed')),
    dem_sha256    TEXT,                    -- file content hash (idempotency anchor)
    duration_sec  INTEGER,                 -- header playback time
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    parsed_at     TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_matches_source_state
    ON matches (source, parse_state);
"""


def default_catalog_path():
    return DEFAULT_CATALOG


def connect(path=None):
    con = sqlite3.connect(path or DEFAULT_CATALOG)
    con.row_factory = sqlite3.Row
    return con


def ensure_schema(con):
    con.executescript(DDL)
    con.commit()


def row_to_dict(r):
    d = dict(r)
    try:
        d["metadata"] = json.loads(d.get("metadata_json") or "{}")
    except Exception:
        d["metadata"] = {}
    return d


def register(con, match_id, source="private", dem_path=None, dem_sha256=None,
             duration_sec=None, metadata=None):
    """Insert a match row; returns True if inserted, False if it already exists."""
    metadata = dict(metadata or {})
    existing = con.execute(
        "SELECT match_id FROM matches WHERE match_id=?", (match_id,)).fetchone()
    if existing:
        return False
    con.execute(
        """INSERT INTO matches (match_id, source, dem_path, parse_state,
                                dem_sha256, duration_sec, metadata_json)
           VALUES (?,?,?,'pending',?,?,?)""",
        (match_id, source, dem_path, dem_sha256, duration_sec,
         json.dumps(metadata, ensure_ascii=False)))
    con.commit()
    return True


def get(con, match_id):
    r = con.execute("SELECT * FROM matches WHERE match_id=?", (match_id,)).fetchone()
    return row_to_dict(r) if r else None


def set_parse_result(con, match_id, db_path=None, state=None, metadata_merge=None):
    """Update parse outcome + optional metadata merge (state: parsed/failed)."""
    row = get(con, match_id)
    if not row:
        return False
    sets, args = [], []
    if state is not None:
        assert state in STATES, "bad state %r" % state
        sets.append("parse_state=?")
        args.append(state)
        if state == "parsed":
            sets.append("parsed_at=?")
            args.append(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if db_path is not None:
        sets.append("db_path=?")
        args.append(db_path)
    if metadata_merge:
        m = dict(row.get("metadata") or {})
        m.update(metadata_merge)
        sets.append("metadata_json=?")
        args.append(json.dumps(m, ensure_ascii=False))
    if sets:
        args.append(match_id)
        con.execute("UPDATE matches SET %s WHERE match_id=?" % ", ".join(sets), args)
    con.commit()
    return True


def update_dem_path(con, match_id, new_path):
    con.execute("UPDATE matches SET dem_path=? WHERE match_id=?", (new_path, match_id))
    con.commit()


def list_rows(con, source=None, parse_state=None):
    sql = "SELECT * FROM matches"
    cond, args = [], []
    if source:
        cond.append("source=?")
        args.append(source)
    if parse_state:
        cond.append("parse_state=?")
        args.append(parse_state)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY match_id"
    return [row_to_dict(r) for r in con.execute(sql, args).fetchall()]


def db_paths(con, source=None, parse_state="parsed", require_exists=True):
    """Paths of per-match parse dbs (feed for analysis/)."""
    out = []
    for r in list_rows(con, source=source, parse_state=parse_state):
        p = r.get("db_path")
        if p and (not require_exists or os.path.exists(p)):
            out.append(p)
    return out


def dbs_for_analysis(catalog_path=None, source=None, parse_state="parsed"):
    """One-call helper for layer-4 scripts: open the catalog and expand to the
    per-match parse db paths (only rows that have an existing db)."""
    con = connect(catalog_path)
    ensure_schema(con)
    return db_paths(con, source=source, parse_state=parse_state, require_exists=True)


def _parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["init", "list", "dbs"])
    ap.add_argument("--catalog", default=None, help="catalog db path")
    ap.add_argument("--source", choices=list(SOURCES), default=None)
    ap.add_argument("--state", choices=list(STATES), default=None)
    ap.add_argument("--json", action="store_true", help="list: dump rows as json")
    return ap.parse_args()


def main():
    args = _parse_args()
    path = args.catalog or DEFAULT_CATALOG
    con = connect(path)
    ensure_schema(con)
    if args.command == "init":
        print("catalog ready: %s" % path)
        return 0
    if args.command == "list":
        rows = list_rows(con, source=args.source, parse_state=args.state)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            print("matches in %s:" % path)
            for r in rows:
                print("  %-24s source=%-7s state=%-8s db=%s dem=%s" % (
                    r["match_id"], r["source"], r["parse_state"],
                    os.path.basename(r["db_path"] or "-"),
                    os.path.basename(r["dem_path"] or "-")))
        return 0
    if args.command == "dbs":
        for p in db_paths(con, source=args.source, parse_state=args.state or "parsed"):
            print(p)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
