#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q1_value_zones.py - Q1(新) 地图经济价值分区 (descriptive) —— 按窗口拆分。

③逻辑层重定义: 输出【全图经济价值分区】——英雄"处于某区域(格)"时的经济效果。
每格每窗口 3 指标:
  ① GPM      : 该窗口内 英雄在该格的期望每分金币获取 = 该格金币 / 该格英雄秒 × 60
  ② 绝对队经济: 某队有人在该格 -> 该队逐秒金币增量 / 分钟
  ③ 相对队经济: 某队有人在该格 -> (己方−对方)逐秒金币差 / 分钟

窗口 (game-clock, 已对齐 pre-game 偏移): 0-10=[0,600) 10-20=[600,1200) 20+=[1200,∞)。
单元: 地图 250 格 (cell=250, MAP 半宽 10000 => 80×80)。附语义标签(近似)。
口径: 描述性版; 反候果由看图者自行领会, 不作控制; 逐玩家归属用 A1 位置。
输出(analysis/output_q1):
  q1_zone_agg.csv     逐格×窗口聚合 (加 window 列)
  q1_zone_detail.db   逐格×窗口×match 明细 (sqlite, 逐场增量写)
"""
import argparse
import csv
import glob as _glob
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_HALF = 10000.0
CELL = 250.0
TEAM_CODE = {2: "radiant", 3: "dire"}
WINDOWS = [("0-10", 0, 600), ("10-20", 600, 1200), ("20+", 1200, None)]


def game_start(con, mid):
    r = con.execute("SELECT min(game_time_sec) FROM entity_snapshots "
                    "WHERE match_id=? AND entity_type='networth' AND hp>0", (mid,)).fetchone()
    return r[0] if r and r[0] is not None else None


def win_of(gc):
    for i, (_, lo, hi) in enumerate(WINDOWS):
        if gc >= lo and (hi is None or gc < hi):
            return i
    return None


ALL = 3                       # 全场聚合桶
WNAMES = ["0-10", "10-20", "20+", "all"]


def buckets_of(gc):
    """Window buckets a given game-clock second belongs to: its own window + the 'all' bucket."""
    w = win_of(gc)
    return [w, ALL] if w is not None else []


def zone_label(x, y):
    rosh = (-1600.0, 2300.0)
    if (x - rosh[0]) ** 2 + (y - rosh[1]) ** 2 < 1000 ** 2:
        return "肉山"
    if abs(x - y) < 1400.0:
        return "中路"
    if y > 2600.0 and x < y:
        return "上路"
    if y < -2600.0 and x > y:
        return "下路"
    if x < -2600.0 and y < -2600.0:
        return "三角区"
    if x > 2600.0 and y > 2600.0:
        return "三角区"
    return "野区"


def cell_of(x, y, n):
    cx = int(x / CELL) + int(MAP_HALF / CELL)
    cy = int(y / CELL) + int(MAP_HALF / CELL)
    return max(0, min(n - 1, cx)), max(0, min(n - 1, cy))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid", type=int, default=80)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "output_q1"))
    args = ap.parse_args()
    n = args.grid
    half = int(MAP_HALF / CELL)
    dbs = sorted(_glob.glob(os.path.join(ROOT, "dems", "db", "*", "*.db")))
    if args.sample:
        dbs = dbs[:args.sample]
    os.makedirs(args.out, exist_ok=True)

    agg = {}   # (cx,cy,w) -> metrics
    def getagg(c, w):
        k = (c[0], c[1], w)
        if k not in agg:
            agg[k] = {"occ": 0, "gold": 0, "r_occ_sec": 0, "r_abs": 0, "r_rel": 0,
                      "d_occ_sec": 0, "d_abs": 0, "d_rel": 0, "n_match": set()}
        return agg[k]

    det_path = os.path.join(args.out, "q1_zone_detail.db")
    if os.path.exists(det_path):
        os.remove(det_path)
    det = sqlite3.connect(det_path)
    det.execute("""CREATE TABLE IF NOT EXISTS detail(
        cell_x INT, cell_y INT, x_center REAL, y_center REAL, zone TEXT, win INT, match_id BIGINT,
        n_hero_sec INT, gold REAL, gpm_per_min REAL, r_occ_sec INT, r_abs_per_min REAL, r_rel_per_min REAL)""")
    det.execute("CREATE INDEX IF NOT EXISTS idx_cell ON detail(cell_x, cell_y, win)")

    n_ok = 0
    for db in dbs:
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        for mid in [r[0] for r in con.execute("SELECT DISTINCT match_id FROM entity_snapshots WHERE entity_type='hero'")]:
            gs = game_start(con, mid)
            if gs is None:
                continue
            hero_pos = {}
            per_sec = {}
            for r in con.execute("SELECT game_time_sec,entity_id,team,x,y FROM entity_snapshots "
                                 "WHERE match_id=? AND entity_type='hero' AND game_time_sec>=?", (mid, gs)):
                s = r["game_time_sec"]; hero = r["entity_id"]
                hero_pos.setdefault(hero, {})[s] = (r["x"], r["y"])
                per_sec.setdefault(s, []).append((hero, r["team"], r["x"], r["y"]))
            if not per_sec:
                con.close(); continue
            hero_team = {r["hero_name"]: r["team_id"] for r in con.execute(
                "SELECT hero_name, team_id FROM player_identity WHERE match_id=? AND hero_name!=''", (mid,))}
            tg = {"radiant": {}, "dire": {}}
            for r in con.execute("SELECT actor_id, game_time_sec, json_extract(properties,'$.value') v "
                                 "FROM game_events WHERE match_id=? AND event_type='gold' AND game_time_sec>=?", (mid, gs)):
                t = int(r["game_time_sec"]); v = r["v"]
                if v is None or v < 0 or v > 100000:
                    continue
                tid = hero_team.get(r["actor_id"])
                team = TEAM_CODE.get(tid) if tid else None
                if team is None:
                    continue
                tg[team][t] = tg[team].get(t, 0) + v
            det_local = {}
            def getdet(c, w):
                k = (c[0], c[1], w)
                if k not in det_local:
                    det_local[k] = {"occ": 0, "gold": 0, "r_occ_sec": 0, "r_abs": 0, "r_rel": 0}
                return det_local[k]
            # gold -> hero cell, binned by window (agg gets own-window + all; detail only own-window)
            for r in con.execute("SELECT actor_id, game_time_sec, json_extract(properties,'$.value') v "
                                 "FROM game_events WHERE match_id=? AND event_type='gold' AND game_time_sec>=?", (mid, gs)):
                t = int(r["game_time_sec"]); v = r["v"]
                if v is None or v < 0 or v > 100000:
                    continue
                w = win_of(t - gs)
                if w is None:
                    continue
                pos = hero_pos.get(r["actor_id"], {}).get(t)
                if pos is None:
                    continue
                c = cell_of(pos[0], pos[1], n)
                for b in (w, ALL):
                    getagg(c, b)["gold"] += v
                getdet(c, w)["gold"] += v
            # occupancy per second, binned by window
            radi_cells = set(); dire_cells = set()
            for s, heroes in per_sec.items():
                w = win_of(s - gs)
                if w is None:
                    continue
                tg_r = tg["radiant"].get(s, 0); tg_d = tg["dire"].get(s, 0)
                radi_cells.clear(); dire_cells.clear()
                for hero, team, x, y in heroes:
                    c = cell_of(x, y, n)
                    for b in (w, ALL):
                        a = getagg(c, b)
                        a["occ"] += 1; a["n_match"].add(mid)
                    getdet(c, w)["occ"] += 1
                    if team == "radiant":
                        radi_cells.add(c)
                    else:
                        dire_cells.add(c)
                for c in radi_cells:
                    for b in (w, ALL):
                        a = getagg(c, b); a["r_occ_sec"] += 1; a["r_abs"] += tg_r; a["r_rel"] += (tg_r - tg_d)
                    d = getdet(c, w); d["r_occ_sec"] += 1; d["r_abs"] += tg_r; d["r_rel"] += (tg_r - tg_d)
                for c in dire_cells:
                    for b in (w, ALL):
                        a = getagg(c, b); a["d_occ_sec"] += 1; a["d_abs"] += tg_d; a["d_rel"] += (tg_d - tg_r)
            rows = []
            for k, d in det_local.items():
                cx, cy, w = k
                xc = (cx - half) * CELL + CELL / 2; yc = (cy - half) * CELL + CELL / 2
                gpm = (d["gold"] / d["occ"] * 60) if d["occ"] else 0.0
                r_abs = (d["r_abs"] / d["r_occ_sec"] * 60) if d["r_occ_sec"] else 0.0
                r_rel = (d["r_rel"] / d["r_occ_sec"] * 60) if d["r_occ_sec"] else 0.0
                rows.append((cx, cy, round(xc, 1), round(yc, 1), zone_label(xc, yc), w, mid,
                             d["occ"], d["gold"], round(gpm, 1), d["r_occ_sec"], round(r_abs, 1), round(r_rel, 1)))
            det.executemany("INSERT INTO detail VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.close()
        n_ok += 1
        if n_ok % 100 == 0:
            print("processed %d/%d..." % (n_ok, len(dbs)))
    det.commit()
    print("matches processed:", n_ok, " cells:", len(agg))

    agg_csv = os.path.join(args.out, "q1_zone_agg.csv")
    with open(agg_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["window", "cell_x", "cell_y", "x_center", "y_center", "zone", "n_hero_sec",
                    "gpm_per_min", "r_occ_sec", "r_abs_per_min", "r_rel_per_min",
                    "d_occ_sec", "d_abs_per_min", "d_rel_per_min", "n_match"])
        for (cx, cy, wi), a in sorted(agg.items()):
            xc = (cx - half) * CELL + CELL / 2; yc = (cy - half) * CELL + CELL / 2
            gpm = (a["gold"] / a["occ"] * 60) if a["occ"] else 0.0
            r_abs = (a["r_abs"] / a["r_occ_sec"] * 60) if a["r_occ_sec"] else 0.0
            r_rel = (a["r_rel"] / a["r_occ_sec"] * 60) if a["r_occ_sec"] else 0.0
            d_abs = (a["d_abs"] / a["d_occ_sec"] * 60) if a["d_occ_sec"] else 0.0
            d_rel = (a["d_rel"] / a["d_occ_sec"] * 60) if a["d_occ_sec"] else 0.0
            w.writerow([WNAMES[wi], cx, cy, round(xc, 1), round(yc, 1), zone_label(xc, yc), a["occ"],
                        round(gpm, 1), a["r_occ_sec"], round(r_abs, 1), round(r_rel, 1),
                        a["d_occ_sec"], round(d_abs, 1), round(d_rel, 1), len(a["n_match"])])
    print("wrote", os.path.basename(agg_csv), "detail rows in db:", det.execute("SELECT COUNT(*) FROM detail").fetchone()[0])


if __name__ == "__main__":
    sys.exit(main())
