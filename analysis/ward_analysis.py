#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ward_analysis.py - ARCHITECTURE.md section 8 step 6 demo query: "where did
each team place its observer/sentry wards over the whole match(es)".

Pure layer-4: reads ONLY the generic `game_events` rows produced by the ward
extractor (event_type in ('ward_placed','ward_destroyed')). No parser change,
no schema change - same proof as the heatmap in step 5, now for a second
dimension.

Usage:
    python analysis/ward_analysis.py <db...> [--team 2|3|all] [--out DIR]

Outputs console counts per (match, team, type), an ASCII placement map per team
(O=observer S=sentry B=both), and dewarded counts per destroying side.
"""
import argparse
import collections
import json
import os
import sqlite3
import sys

MAP_HALF = 10000.0
GRID = 20  # ASCII map resolution in one direction (20x20 for 20k world units)


def connect(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def ward_rows(con, match_id, event_type):
    return con.execute(
        """SELECT game_time_sec, x, y, actor_id, target_id, properties
           FROM game_events
           WHERE match_id=? AND event_type=? ORDER BY game_time_sec, event_seq""",
        (match_id, event_type)).fetchall()


def ascii_map(points):
    """points: list of (x, y, ch) -> grid string. Row 0 = north (y max)."""
    n = GRID
    cell = (2 * MAP_HALF) / n
    grid = [["."] * n for _ in range(n)]
    for x, y, ch in points:
        cx = int((x + MAP_HALF) / cell)
        cy = int((MAP_HALF - y) / cell)  # north up
        if 0 <= cx < n and 0 <= cy < n:
            prev = grid[cy][cx]
            grid[cy][cx] = ch if prev == "." else ("B" if prev != ch else ch)
    return "\n".join("".join(row) for row in grid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dbs", nargs="*", help="sqlite db files (or use --catalog)")
    ap.add_argument("--catalog", default=None,
                    help="matches catalog db (scheduler/) - enumerate parsed "
                         "matches instead of passing db files by hand")
    ap.add_argument("--source", choices=["private", "public"], default=None,
                    help="only with --catalog: restrict by source")
    ap.add_argument("--state", default="parsed",
                    help="only with --catalog: restrict by parse_state")
    ap.add_argument("--team", choices=["2", "3", "all"], default="all")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "output"))
    args = ap.parse_args()

    if args.catalog:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from scheduler import catalog as cat
        dbs = cat.dbs_for_analysis(args.catalog, source=args.source,
                                   parse_state=args.state)
        if not dbs:
            ap.error("catalog %s has no matches (source=%s, state=%s) with a db"
                     % (args.catalog, args.source, args.state))
        print("catalog %s -> %d db(s)" % (args.catalog, len(dbs)))
    else:
        if not args.dbs:
            ap.error("provide db files or --catalog")
        dbs = args.dbs

    os.makedirs(args.out, exist_ok=True)
    teams = (2, 3) if args.team == "all" else (int(args.team),)

    for db in dbs:
        con = connect(db)
        mids = [r[0] for r in con.execute(
            "SELECT DISTINCT match_id FROM game_events ORDER BY match_id")]
        for match_id in mids:
            print("\n" + "=" * 74)
            print("MATCH %s:%d" % (os.path.basename(db), match_id))
            placed = ward_rows(con, match_id, "ward_placed")
            destroyed = ward_rows(con, match_id, "ward_destroyed")
            # aggregate by team + type
            team_of = {}  # not needed: team is in properties
            count = collections.Counter()
            for r in placed:
                p = json.loads(r["properties"])
                count[(p.get("team"), p.get("ward_type"))] += 1
            print("  placed  per (team,type):", dict(sorted(
                count.items(), key=lambda kv: (kv[0][0] is None, kv[0]))))

            for team in teams:
                pts = []
                for r in placed:
                    p = json.loads(r["properties"])
                    if p.get("team") != team:
                        continue
                    ch = "S" if p.get("ward_type") == "sentry" else "O"
                    pts.append((r["x"], r["y"], ch))
                print("  %s ward placement map (%d rows, O=observer S=sentry B=both):" %
                      ("radiant" if team == 2 else "dire", len(pts)))
                print(ascii_map(pts))

            # who destroyed wards (dewarded) - by actor side where resolvable
            dewards = collections.Counter()
            expired = 0
            for r in destroyed:
                p = json.loads(r["properties"])
                if p.get("reason") == "expired":
                    expired += 1
                else:
                    actor = r["actor_id"] or "?"
                    kind = "tower" if "tower" in actor else (
                        "hero:" + actor.replace("npc_dota_hero_", ""))
                    dewards[kind] += 1
            print("  destroyed: dewarded=%d (by %s), expired=%d"
                  % (sum(dewards.values()), dict(dewards), expired))

            # write placed rows summary CSV (team,type,x,y,time)
            csv_path = os.path.join(
                args.out, "%s_%d_ward_placed.csv" % (os.path.basename(db).replace(".db", ""), match_id))
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                import csv
                w = csv.writer(f)
                w.writerow(["game_time_sec", "team", "ward_type", "x", "y"])
                for r in placed:
                    p = json.loads(r["properties"])
                    w.writerow([r["game_time_sec"], p.get("team"),
                                p.get("ward_type"), r["x"], r["y"]])
            print("  wrote", os.path.basename(csv_path))
        con.close()
    print("\ndone")


if __name__ == "__main__":
    sys.exit(main())
