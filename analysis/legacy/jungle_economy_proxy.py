#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jungle_economy_proxy.py - Q1 (B) first-version proxy (② 数据分析/收集层).

Problem (Q1): "刷哪个野点、何时刷，对本人经济+团队经济的边际影响/机会成本".
Per ③逻辑层 settled scope, the first-version proxy does NOT include the
lane_available(t) opportunity-cost term (shelved - concept contested). This
script delivers the two measurable, reproducible pieces:

  (B1) 野点活动热区 (jungle-farming activity heatmap):
       bin `neutral_kill` events (new parser extractor) over the map window,
       split into the 4 owner-settled windows: 0-10 / 10-20 / 20+ / whole.
       Output: per-window CSV (team, cell_x, cell_y, count) + ASCII density map.

  (B2) 团队经济 gold_adv 宏观对照 (team gold advantage over time):
       sum `gold` events (per-player gold income) per team per minute, and
       each window's team gold advantage (radiant - dire). This is the coarse
       "macroscopic" counterpoint for jungle value; it is NOT attributable to a
       single camp.

Sources: only the generic three-table model produced by dota_parse with the
jungle/economy extractor: `game_events` (neutral_kill / gold) +
`player_identity` (hero -> team). No schema change, no OpenDota.

Usage:
    python analysis/jungle_economy_proxy.py analysis/output_q1/*.db \
        [--cell 250] [--out analysis/output_q1]

Windows (owner-settled, game-clock seconds):
    0-10  : [0, 600)
    10-20 : [600, 1200)
    20+   : [1200, inf)
    whole : [0, inf)
"""
import argparse
import csv
import glob as _glob
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_HALF = 10000.0
WINDOWS = [
    ("0-10", 0, 600),
    ("10-20", 600, 1200),
    ("20+", 1200, None),
    ("whole", 0, None),
]
TEAM_CODE = {2: "radiant", 3: "dire"}


def connect(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def team_of_hero(con, mid):
    return dict(con.execute(
        "SELECT hero_name, team_id FROM player_identity "
        "WHERE match_id=? AND hero_name!=''", (mid,)).fetchall())


def game_start(con, mid):
    """First demo second where any player net worth > 0 (removes pre-game offset)."""
    r = con.execute(
        "SELECT min(game_time_sec) FROM entity_snapshots "
        "WHERE match_id=? AND entity_type='networth' AND hp>0", (mid,)).fetchone()
    return r[0] if r and r[0] is not None else None


def in_window(t, win):
    _, lo, hi = win
    if t < lo:
        return False
    if hi is not None and t >= hi:
        return False
    return True


def load_neutral_kills(con, mid):
    return con.execute(
        "SELECT game_time_sec AS t, x, y, json_extract(properties,'$.kind') AS kind "
        "FROM game_events WHERE match_id=? AND event_type='neutral_kill'",
        (mid,)).fetchall()


def load_gold(con, mid):
    return con.execute(
        "SELECT game_time_sec AS t, actor_id, json_extract(properties,'$.value') AS value "
        "FROM game_events WHERE match_id=? AND event_type='gold'",
        (mid,)).fetchall()


def bin_grid(points, cell):
    """points: list of (x,y). -> {(col,row): count}, col=x east, row=y north."""
    n = int(round(2 * MAP_HALF / cell))
    half = int(MAP_HALF / cell)
    grid = {}
    for x, y in points:
        if abs(x) > MAP_HALF or abs(y) > MAP_HALF:
            continue
        cx = int(x / cell) + half
        cy = int(y / cell) + half
        cx = max(0, min(n - 1, cx))
        cy = max(0, min(n - 1, cy))
        grid[(cx, cy)] = grid.get((cx, cy), 0) + 1
    return grid


def ascii_grid(grid, cell, width=60, height=24):
    n = int(round(2 * MAP_HALF / cell))
    if n <= 0:
        return "(empty)"
    bx = max(1, -(-n // width))
    by = max(1, -(-n // height))
    out = []
    mx = max(grid.values()) if grid else 0
    ramp = " .:-=+*#%@"
    top = len(ramp) - 1
    for row in range(height):
        line = []
        y0 = row * by
        for col in range(width):
            x0 = col * bx
            s = 0
            for yy in range(y0, min(y0 + by, n)):
                for xx in range(x0, min(x0 + bx, n)):
                    s += grid.get((xx, yy), 0)
            if s == 0:
                line.append(".")
            else:
                ch = ramp[min(top, int(round(top * s / (mx or 1))))]
                line.append(ch)
        out.append("".join(line))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dbs", nargs="*", help="new-parser sqlite db files (with neutral_kill/gold); none -> glob dems/db/*/*.db")
    ap.add_argument("--cell", type=int, default=250)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "output_q1"))
    args = ap.parse_args()
    dbs = args.dbs or sorted(_glob.glob(os.path.join(ROOT, "dems", "db", "*", "*.db")))
    os.makedirs(args.out, exist_ok=True)
    cell = args.cell

    # ---- collect across dbs ----
    # per window per team -> grid
    window_team_grid = {w: {} for w, _, _ in WINDOWS}  # w -> "team" -> {(cx,cy):count}
    # per window per team -> neutral kill count
    window_team_count = {w: {} for w, _, _ in WINDOWS}
    # per minute -> team gold sums
    minute_gold = {}  # minute -> {"radiant": int, "dire": int}
    neutral_rows = []  # (win, team, x, y) for CSV
    n_matches = 0
    n_gold = 0

    for db in dbs:
        con = connect(db)
        mids = [r[0] for r in con.execute("SELECT DISTINCT match_id FROM game_events ORDER BY match_id")]
        for mid in mids:
            # game-start alignment (demo clock may include pre-game)
            gs = game_start(con, mid)
            if gs is None:
                continue
            for w, lo, hi in WINDOWS:
                window_team_grid[w].setdefault("radiant", {})
                window_team_grid[w].setdefault("dire", {})
                window_team_count[w].setdefault("radiant", 0)
                window_team_count[w].setdefault("dire", 0)
            for r in load_neutral_kills(con, mid):
                neutral_rows.append((r["t"], r["kind"], r["x"], r["y"]))
                t_al = r["t"] - gs
                for w, lo, hi in WINDOWS:
                    if in_window(t_al, (w, lo, hi)):
                        # neutral creeps have no team; attribute by map quadrant
                        # (radiant jungle is the negative-x/negative-y region on
                        #  the standard Dota map).
                        team = "radiant" if (r["x"] < 0 and r["y"] < 0) else (
                            "dire" if (r["x"] > 0 and r["y"] > 0) else "mid")
                        if team in ("radiant", "dire"):
                            g = window_team_grid[w][team]
                            n = int(round(2 * MAP_HALF / cell))
                            half = int(MAP_HALF / cell)
                            if abs(r["x"]) <= MAP_HALF and abs(r["y"]) <= MAP_HALF:
                                cx = max(0, min(n - 1, int(r["x"] / cell) + half))
                                cy = max(0, min(n - 1, int(r["y"] / cell) + half))
                                g[(cx, cy)] = g.get((cx, cy), 0) + 1
                            window_team_count[w][team] += 1
            team_of = team_of_hero(con, mid)
            for r in load_gold(con, mid):
                n_gold += 1
                team = TEAM_CODE.get(team_of.get(r["actor_id"]))
                if team is None:
                    continue
                value = r["value"]
                if value is None or value < 0 or value > 100000:
                    continue  # drop sentinel/overflow values
                minute = int((r["t"] - gs) // 60)
                slot = minute_gold.setdefault(minute, {"radiant": 0, "dire": 0})
                slot[team] += value
            n_matches += 1
        con.close()

    print("=" * 74)
    print("Q1 (B) JUNGLE-ECONOMY PROXY  (matches=%d, neutral_kill events=%d, gold events=%d)"
          % (n_matches, len(neutral_rows), n_gold))
    print("libraries read: %d  cell=%d" % (len(dbs), cell))

    # ---- B1: per-window activity heatmap CSV + ASCII ----
    heat_csv = os.path.join(args.out, "q1_neutral_activity_heat_%d.csv" % cell)
    with open(heat_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["window", "team", "cell_x", "cell_y", "x_center", "y_center", "count"])
        half = int(MAP_HALF / cell)
        n = int(round(2 * MAP_HALF / cell))
        for win, lo, hi in WINDOWS:
            for team in ("radiant", "dire"):
                g = window_team_grid[win][team]
                for (cx, cy), c in sorted(g.items()):
                    w.writerow([win, team, cx, cy,
                                (cx - half) * cell + cell / 2,
                                (cy - half) * cell + cell / 2, c])
    print("\n(B1) wrote %s" % os.path.basename(heat_csv))

    print("\n(B1) Neutral-farm activity per window (team kills):")
    for win, lo, hi in WINDOWS:
        rc = window_team_count[win]["radiant"]
        dc = window_team_count[win]["dire"]
        print("  %-6s radiant=%4d  dire=%4d  (sum=%d)" % (win, rc, dc, rc + dc))

    print("\n(B1) ASCII density map (per window, . empty -> @ hottest):")
    for win, lo, hi in WINDOWS:
        combo = {}
        for team in ("radiant", "dire"):
            for (k, v) in window_team_grid[win][team].items():
                combo[k] = combo.get(k, 0) + v
        print("\n  -- window %s --" % win)
        print(ascii_grid(combo, cell))

    # ---- B2: team gold_adv per minute + per window ----
    print("\n(B2) Team gold advantage (radiant - dire) per minute (from gold events):")
    gold_csv = os.path.join(args.out, "q1_gold_adv_minute.csv")
    with open(gold_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["minute", "radiant", "dire", "gold_adv_radiant"])
        for m in sorted(minute_gold):
            s = minute_gold[m]
            adv = s["radiant"] - s["dire"]
            w.writerow([m, s["radiant"], s["dire"], adv])
            print("  m=%3d radiant=%7d dire=%7d adv=%+8d" % (m, s["radiant"], s["dire"], adv))
    print("  wrote %s" % os.path.basename(gold_csv))

    # per-window gold advantage (window bounds are in seconds; minutes = s/60)
    print("\n(B2) Per-window team gold (income) + advantage:")
    for win, lo, hi in WINDOWS:
        r = d = 0
        minute_lo = lo // 60 if lo is not None else 0
        minute_hi = hi // 60 if hi is not None else 10 ** 9
        for m, s in minute_gold.items():
            if minute_lo <= m < minute_hi:
                r += s["radiant"]
                d += s["dire"]
        print("  %-6s radiant=%8d  dire=%8d  adv=%+8d" % (win, r, d, r - d))

    print("\ndone")


if __name__ == "__main__":
    sys.exit(main())
