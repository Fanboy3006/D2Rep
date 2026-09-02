#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_analysis.py - ARCHITECTURE.md section 8 step 5: the FIRST layer-4
cross-match analysis over the generic three-table model.

Everything here is pure SQL + aggregation on top of entity_snapshots /
game_events / player_identity. No parser change, no schema change - this is
exactly the "new analysis dimension == new query" hypothesis being validated.

Deliverables:
  1. Hero position heatmap (primary): per-team occupancy grids per match and
     a pooled cross-match view, plus an ASCII dominance map and CSV grids.
  2. Purchase rhythm (secondary, game_events only): purchase counts per
     5-minute bucket per team per match and pooled.

Key data caveats applied (ARCHITECTURE.md 4.2 / 2026-09 notes):
  - only hero entities WITH a player_slot are real players; summons whose
    class also starts with CDOTA_Unit_Hero_ have no player_slot and are
    excluded via json_extract(extra, '$.player_slot').
  - some CDN recordings start mid-match (demo time axis has an offset); for
    time-aligned aggregates (purchases) we therefore report demo-relative
    minutes and mark matches that start near 0 (first hero sample < 300 s) as
    game-aligned.

Usage:
    python run_analysis.py <db1.db> [db2.db ...] [--cell 250] [--out DIR]

ASCII-safe console output; full results written as files.
"""
import argparse
import csv
import json
import os
import sqlite3
import sys

MAP_HALF = 10000.0          # dota world coords live roughly within +/-10000
FIRST_SAMPLE_ALIGNED_S = 300  # matches whose first hero sample < this are 0-start

# --------------------------------------------------------------------------
# sqlite helpers
# --------------------------------------------------------------------------

HERO_SQL = """
    SELECT team, x, y, game_time_sec, entity_id
      FROM entity_snapshots
     WHERE match_id = ?1 AND entity_type = 'hero'
       AND json_extract(extra, '$.player_slot') IS NOT NULL
"""


def connect(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def list_matches(con):
    rows = con.execute("SELECT DISTINCT match_id FROM entity_snapshots "
                       "UNION SELECT DISTINCT match_id FROM player_identity "
                       "ORDER BY match_id").fetchall()
    return [r[0] for r in rows]


def match_summary(con, match_id):
    """Minimal facts per match (identity + event histogram)."""
    n_pl = con.execute(
        "SELECT COUNT(*) FROM player_identity WHERE match_id=? AND hero_name!=''",
        (match_id,)).fetchone()[0]
    n_ev = con.execute(
        "SELECT COUNT(*) FROM game_events WHERE match_id=?", (match_id,)).fetchone()[0]
    ev_types = con.execute(
        "SELECT event_type, COUNT(*) FROM game_events WHERE match_id=? "
        "GROUP BY event_type", (match_id,)).fetchall()
    t0 = con.execute(
        "SELECT MIN(game_time_sec) FROM entity_snapshots WHERE match_id=? "
        "AND entity_type='hero'", (match_id,)).fetchone()[0]
    return n_pl, n_ev, ev_types, (t0 if t0 is not None else 0)


def hero_rows(con, match_id):
    return con.execute(HERO_SQL, (match_id,)).fetchall()


# --------------------------------------------------------------------------
# heatmap
# --------------------------------------------------------------------------

def build_heatmaps(rows, cell):
    """rows -> {team: 2D count grid}. Index (col,row): col = x bin eastward,
    row = y bin northward. World window is [-MAP_HALF, MAP_HALF]^2."""
    n = int(round(2 * MAP_HALF / cell))
    half = int(MAP_HALF / cell)
    grids = {"radiant": [[0] * n for _ in range(n)],
             "dire": [[0] * n for _ in range(n)]}
    n_total = 0
    n_out = 0
    for r in rows:
        team = r["team"]
        if team not in grids:
            continue  # summons / unknown-team heroes are not real players here
        x, y = r["x"], r["y"]
        if abs(x) > MAP_HALF or abs(y) > MAP_HALF:
            n_out += 1
            continue
        cx = int(x / cell) + half
        cy = int(y / cell) + half
        cx = max(0, min(n - 1, cx))
        cy = max(0, min(n - 1, cy))
        grids[team][cy][cx] += 1
        n_total += 1
    return grids, n_total, n_out


def grids_to_csv(match_label, grids, cell, out_csv):
    n = len(next(iter(grids.values()))[0])
    half = int(MAP_HALF / cell)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["match", "team", "cell_x", "cell_y", "x_center", "y_center", "count"])
        for team, g in grids.items():
            for cy in range(n):
                for cx in range(n):
                    c = g[cy][cx]
                    if c:
                        w.writerow([match_label, team, cx, cy,
                                    (cx - half) * cell + cell / 2,
                                    (cy - half) * cell + cell / 2, c])


def ascii_dominance(grids, cell, width=80, height=30):
    """Radiant vs Dire dominance map. To keep the console narrow we aggregate
    ceil(n/width) x ceil(n/height) cell blocks."""
    n = len(next(iter(grids.values()))[0])
    rg, dg = grids["radiant"], grids["dire"]
    bx = max(1, -(-n // width))
    by = max(1, -(-n // height))
    out = []
    for row in range(height):
        line = []
        y0 = row * by
        for col in range(width):
            x0 = col * bx
            rr = dd = 0
            for yy in range(y0, min(y0 + by, n)):
                for xx in range(x0, min(x0 + bx, n)):
                    rr += rg[yy][xx]
                    dd += dg[yy][xx]
            if rr == 0 and dd == 0:
                ch = "."
            elif rr > 2 * dd:
                ch = "R"
            elif dd > 2 * rr:
                ch = "D"
            elif rr + dd > 0:
                ch = "o"  # contested / both present
            else:
                ch = "."
            line.append(ch)
        out.append("".join(line))
    return "\n".join(out)


# --------------------------------------------------------------------------
# purchases
# --------------------------------------------------------------------------

def purchase_buckets(con, match_id, bucket_s=300):
    rows = con.execute(
        "SELECT game_time_sec, actor_id, properties FROM game_events "
        "WHERE match_id=? AND event_type='purchase' ORDER BY game_time_sec",
        (match_id,)).fetchall()
    team_of = dict(con.execute(
        "SELECT hero_name, team_id FROM player_identity WHERE match_id=? "
        "AND hero_name!=''", (match_id,)).fetchall())
    buckets = {}  # (team, minute_bucket_start) -> count
    for r in rows:
        t = r["game_time_sec"]
        b = (t // bucket_s) * (bucket_s // 60)
        hero = r["actor_id"]
        team = "radiant" if team_of.get(hero) == 2 else "dire" if team_of.get(hero) == 3 else "?"
        buckets[(team, b)] = buckets.get((team, b), 0) + 1
    return buckets, len(rows)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dbs", nargs="+", help="sqlite db files (one per run dir or several)")
    ap.add_argument("--cell", type=int, default=250, help="heatmap cell size in world units")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "output"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cell = args.cell
    print("dbs:", args.dbs)

    pooled = {"radiant": None, "dire": None}   # summed raw counts across matches
    pooled_time_s = {"radiant": 0.0, "dire": 0.0}
    normalized_sums = {"radiant": None, "dire": None}  # per-match normalized share, summed
    n_matches_norm = 0
    purchase_pool = {}  # (team, minute) -> count across aligned matches
    aligned_matches = 0

    for db in args.dbs:
        con = connect(db)
        for match_id in list_matches(con):
            label = "%s:%d" % (os.path.basename(db), match_id)
            print("\n" + "=" * 78)
            print("MATCH", label)
            n_pl, n_ev, ev_types, t0 = match_summary(con, match_id)
            print("  players=%d events=%d first_hero_sample_t=%ds (%s)" % (
                n_pl, n_ev, t0,
                "0-start-ish" if t0 < FIRST_SAMPLE_ALIGNED_S else "MID-START (offset axis)"))
            if n_pl == 0:
                print("  skip: no players in identity table")
                continue
            rows = hero_rows(con, match_id)

            # identity cross-check: only slotted heroes should have been kept
            hero_ids = {r["entity_id"] for r in rows}
            ident_heroes = {r[0] for r in con.execute(
                "SELECT hero_name FROM player_identity WHERE match_id=? AND hero_name!=''",
                (match_id,)).fetchall()}
            missing = ident_heroes - hero_ids
            extra = hero_ids - ident_heroes
            if missing or extra:
                print("  [warn] hero identity mismatch: missing=%s extra=%s" %
                      (sorted(missing), sorted(extra)))

            grids, n_total, n_out = build_heatmaps(rows, cell)
            print("  heatmap rows used=%d out-of-window=%d" % (n_total, n_out))
            per_team = {t: sum(sum(row) for row in g) for t, g in grids.items()}
            print("  team occupancy (hero-seconds): %s" % per_team)

            # mean position centroid per team
            sums = {"radiant": [0.0, 0.0], "dire": [0.0, 0.0]}
            for r in rows:
                t = r["team"]
                if t in sums:
                    sums[t][0] += r["x"]
                    sums[t][1] += r["y"]
            for t in ("radiant", "dire"):
                n = per_team[t] or 1
                print("  %-7s centroid = (x=%8.0f, y=%8.0f)" % (
                    t, sums[t][0] / n, sums[t][1] / n))

            # quadrant share per team (bottom-left == radiant home corner)
            q = {"radiant": [0, 0, 0, 0], "dire": [0, 0, 0, 0]}
            for r in rows:
                t = r["team"]
                if t not in q:
                    continue
                i = (1 if r["x"] >= 0 else 0) + (2 if r["y"] >= 0 else 0)
                q[t][i] += 1
            for t in ("radiant", "dire"):
                tot = sum(q[t]) or 1
                print("  %-7s quadrant share [x<0,y<0  x<0,y>0  x>0,y<0  x>0,y>0] = %s" %
                      (t, ["%d%%" % round(100.0 * v / tot) for v in q[t]]))

            # per-match dominance map
            print("  dominance map (%d-cell, R=radiant D=dire o=contested):" % cell)
            print(ascii_dominance(grids, cell))

            # write per-match grid
            grid_csv = os.path.join(args.out, "%s_heatmap_%d.csv" % (label.replace(":", "_"), cell))
            grids_to_csv(label, grids, cell, grid_csv)
            print("  wrote", os.path.basename(grid_csv))

            # pooling (raw + per-match normalized)
            for t in ("radiant", "dire"):
                g = grids[t]
                total = sum(sum(row) for row in g)
                if pooled[t] is None:
                    pooled[t] = [[0] * len(g[0]) for _ in g]
                    normalized_sums[t] = [[0.0] * len(g[0]) for _ in g]
                for y in range(len(g)):
                    for x in range(len(g[y])):
                        pooled[t][y][x] += g[y][x]
                        if total:
                            normalized_sums[t][y][x] += g[y][x] / float(total)
                pooled_time_s[t] += total
            n_matches_norm += 1

            # purchases
            buckets, npur = purchase_buckets(con, match_id)
            print("  purchases=%d" % npur)
            if npur:
                items = []
                for b in sorted({b for _, b in buckets}):
                    rc = buckets.get(("radiant", b), 0)
                    dc = buckets.get(("dire", b), 0)
                    if rc + dc:
                        items.append("%d:%d/%d" % (b, rc, dc))
                print("  per-5min purchases [start_minute:R/D]: %s" % " ".join(items))
                if t0 < FIRST_SAMPLE_ALIGNED_S:
                    aligned_matches += 1
                    for (team, b), c in buckets.items():
                        purchase_pool[(team, b)] = purchase_pool.get((team, b), 0) + c
        con.close()

    # ---- pooled cross-match output ----
    print("\n" + "=" * 78)
    print("POOLED ACROSS %d MATCHES" % n_matches_norm)
    if n_matches_norm:
        for t in ("radiant", "dire"):
            g = pooled[t]
            if g is None:
                continue
            print("  %-7s total hero-seconds=%d (raw heatmap in pooled CSV)"
                  % (t, int(pooled_time_s[t])))
        pooled_csv = os.path.join(args.out, "pooled_heatmap_%d.csv" % cell)
        with open(pooled_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["team", "cell_x", "cell_y", "raw_count", "avg_norm_share"])
            half = int(MAP_HALF / cell)
            n = len(next(iter(pooled.values()))[0])
            for t in ("radiant", "dire"):
                for cy in range(n):
                    for cx in range(n):
                        c = pooled[t][cy][cx]
                        if c:
                            w.writerow([t, cx, cy, c,
                                        round(normalized_sums[t][cy][cx] / n_matches_norm, 8)])
        print("  wrote", os.path.basename(pooled_csv))
        print("  pooled dominance map (per-match-normalised averages):")
        print(ascii_dominance(
            {"radiant": [[normalized_sums["radiant"][y][x] * 1000
                          for x in range(len(normalized_sums["radiant"][y]))]
                         for y in range(len(normalized_sums["radiant"]))],
             "dire": [[normalized_sums["dire"][y][x] * 1000
                       for x in range(len(normalized_sums["dire"][y]))]
                      for y in range(len(normalized_sums["dire"]))]}, cell))

    if purchase_pool:
        print("  pooled purchases per 5-min (aligned matches=%d):" % aligned_matches)
        items = []
        minutes = sorted({b for _, b in purchase_pool})
        for b in minutes:
            rc = purchase_pool.get(("radiant", b), 0)
            dc = purchase_pool.get(("dire", b), 0)
            if rc + dc:
                items.append("%d:%d/%d" % (b, rc, dc))
        print("  " + " ".join(items))
        with open(os.path.join(args.out, "pooled_purchases_5min.csv"), "w",
                  newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["minute_bucket_5min", "radiant", "dire"])
            for b in minutes:
                w.writerow([b, purchase_pool.get(("radiant", b), 0),
                            purchase_pool.get(("dire", b), 0)])
    print("\ndone")


if __name__ == "__main__":
    sys.exit(main())
