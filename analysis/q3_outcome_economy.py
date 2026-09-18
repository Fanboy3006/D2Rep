#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q3_outcome_economy.py (v2) - Q3 英雄经济产出 & 经济→胜率 转化，用【精确净值】。

A1 已精确化（读 .dem 的 CDOTA_DataRadiant/Dire.m_iNetWorth，对账 0.000%）。
此版本用 `entity_snapshots` 的 `entity_type='networth'`（`entity_id='nw:<team>:<idx>'`，
`hp`=净值，`extra.reliable/unreliable`=现金）计算：

Q3a 经济产出(刷钱能力): 各英雄 0-10 / 10-20 / 20+ 的每分净值增量。
   per-hero per-min = (networth@end_window - networth@start_window) / window_minutes。
   (用精确逐分净值，非金币收入近似。)

Q3b 经济→胜率 转化（不控版）: 英雄 × team 10min经济状态 → team 胜率。
   team 10min状态用 stats.db 的 gold_adv@10（team view, ±thr）；英雄归属用
   player_identity.hero_name + team_id。控版/leave-one-out 需固定效应，本期标注待做。

Usage:
    python analysis/q3_outcome_economy.py analysis/output_q3/*.db --thr 1000
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATS = os.path.join(ROOT, "stats.db")
WINDOWS = [("0-10", 0, 600), ("10-20", 600, 1200), ("20+", 1200, None)]
TEAM_CODE = {2: "radiant", 3: "dire"}


def connect(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def load_identity(con, mid):
    return con.execute(
        "SELECT player_slot, hero_name, team_id FROM player_identity "
        "WHERE match_id=? AND hero_name!=''", (mid,)).fetchall()


def load_networth_series(con, mid):
    """entity_id('nw:<team>:<idx>') -> {second: (networth, reliable, unreliable)}."""
    out = {}
    for r in con.execute(
            "SELECT entity_id, game_time_sec, hp, json_extract(extra,'$.reliable') rel, "
            "json_extract(extra,'$.unreliable') unrel "
            "FROM entity_snapshots WHERE match_id=? AND entity_type='networth'", (mid,)):
        e = out.setdefault(r["entity_id"], {})
        e[r["game_time_sec"]] = (r["hp"], r["rel"], r["unrel"])
    return out


def value_at(series, sec):
    """net worth at/just-before `sec`. Mid-start recordings begin after 0, so for a
    window start that precedes the first sample, fall back to the earliest value."""
    ok = [s for s in series if s <= sec]
    if ok:
        return series[max(ok)][0]
    if series:
        return series[min(series)][0]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dbs", nargs="*", help="db files; or none -> glob dems/db/*/*.db")
    ap.add_argument("--thr", type=int, default=1000)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "output_q3"))
    args = ap.parse_args()
    import glob as _g
    dbs = args.dbs or sorted(_g.glob(os.path.join(ROOT, "dems", "db", "*", "*.db")))
    os.makedirs(args.out, exist_ok=True)
    con_stat = sqlite3.connect(STATS)
    con_stat.row_factory = sqlite3.Row
    thr = args.thr

    # Q3a: hero -> window -> [net worth increment, window cardinality (match-ids)]
    per_hero_win = {}   # hero -> win -> {delta, n}  (per-match delta accumulated)
    # Q3b: hero -> state -> [n, win]
    per_hero_state = {}
    n_matches = 0
    n_heroes_tagged = 0

    for db in dbs:
        con = connect(db)
        for mid in [r[0] for r in con.execute("SELECT DISTINCT match_id FROM game_events ORDER BY match_id")]:
            ident = load_identity(con, mid)
            if not ident:
                continue
            # map team+idx -> hero_name (entity_id nw:<team>:<idx>; radiant idx0-4 slot0-4, dire idx0-4 slot128-132)
            hero_by_nw = {}
            for r in ident:
                if r["team_id"] == 2:
                    hero_by_nw["nw:radiant:%d" % r["player_slot"]] = r["hero_name"]
                    hero_by_nw.setdefault(r["hero_name"], r["team_id"])
                elif r["team_id"] == 3:
                    hero_by_nw["nw:dire:%d" % (r["player_slot"] - 128)] = r["hero_name"]
                    hero_by_nw.setdefault(r["hero_name"], r["team_id"])
            series = load_networth_series(con, mid)
            if not series:
                continue
            # --- game-start alignment (demo clock includes pre-game) ---
            all_secs = sorted(set(s for s in series.values() for s in s))
            game_start = None
            for sec in all_secs:
                if any(v[sec][0] > 0 for v in series.values() if sec in v):
                    game_start = sec
                    break
            if game_start is None:
                continue
            win_secs = [(w, lo + game_start, (hi + game_start) if hi is not None else None)
                        for w, lo, hi in WINDOWS]
            # team gold_adv@10 + win from stats.db
            sm = con_stat.execute("SELECT radiant_team_id,dire_team_id,radiant_win FROM matches WHERE match_id=?", (mid,)).fetchone()
            if not sm:
                continue
            g10r = con_stat.execute("SELECT value FROM gold_adv WHERE match_id=? AND minute=10", (mid,)).fetchone()
            if not g10r:
                continue
            g10 = g10r["value"]

            # iterate actual series entities
            for nwid, s in series.items():
                hero = hero_by_nw.get(nwid)
                if hero is None:
                    continue
                n_heroes_tagged += 1
                for w, lo, hi in win_secs:
                    v_lo = value_at(s, lo)
                    v_hi = value_at(s, hi) if hi is not None else value_at(s, 10 ** 9)
                    if v_lo is None or v_hi is None:
                        continue
                    mins = (hi - lo) / 60.0 if hi is not None else (max(s) - lo) / 60.0
                    if mins <= 0:
                        continue
                    inc = (v_hi - v_lo) / mins
                    e = per_hero_win.setdefault(hero, {}).setdefault(w, [0.0, 0])
                    e[0] += inc
                    e[1] += 1
                # Q3b: team 10min state (team view) + result, per hero on the team
                # determine team of hero via ident
                hteam = hero_by_nw.get(hero)  # team_id
                rteam = ident_map = None
                # simpler: use player_identity team for the hero
                for r in ident:
                    if r["hero_name"] == hero:
                        hteam = r["team_id"]
                        break
                side = "radiant" if hteam == 2 else "dire"
                t10 = g10 if side == "radiant" else -g10
                win = sm["radiant_win"] if side == "radiant" else (1 - sm["radiant_win"] if sm["radiant_win"] is not None else None)
                state = "lead" if t10 > thr else ("trail" if t10 < -thr else "even")
                cell = per_hero_state.setdefault(hero, {}).setdefault(state, [0, 0])
                cell[0] += 1
                if win is not None and win == 1:
                    cell[1] += 1
            n_matches += 1
        con.close()

    print("=" * 76)
    print("Q3a 经济产出(刷钱能力) — 每分【净值】增量 by 英雄 × 时段  (A1 精确净值)")
    print("  matches=%d  hero-tags=%d" % (n_matches, n_heroes_tagged))
    print("  %-42s %10s %10s %10s" % ("hero", "0-10/min", "10-20/min", "20+/min"))
    rows = []
    for hero in sorted(per_hero_win):
        e = per_hero_win[hero]
        g = lambda w: (e.get(w, [0.0, 0])[0] / e.get(w, [0.0, 0])[1]) if e.get(w, [0.0, 0])[1] else 0.0
        rows.append((hero, g("0-10"), g("10-20"), g("20+"), e.get("0-10", [0,0])[1], e.get("10-20",[0,0])[1], e.get("20+",[0,0])[1]))
        print("  %-42s %10.0f %10.0f %10.0f   (n=%d/%d/%d)" % (hero, rows[-1][1], rows[-1][2], rows[-1][3], rows[-1][4], rows[-1][5], rows[-1][6]))

    print("\n" + "=" * 76)
    print("Q3b 经济→胜率 转化（不控版）: 英雄 × team 10min状态 → 胜率  (阈值±%d)" % thr)
    print("  %-42s %-6s %6s %6s %8s" % ("hero", "state", "n", "win", "winrate"))
    for hero in sorted(per_hero_state):
        for st in ("lead", "even", "trail"):
            n, w = per_hero_state[hero].get(st, [0, 0])
            if n:
                print("  %-42s %-6s %6d %6d %8.3f" % (hero, st, n, w, w / n))

    import csv
    q3a = os.path.join(args.out, "q3a_hero_nw_increment.csv")
    with open(q3a, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f); wr.writerow(["hero", "per_min_0_10", "per_min_10_20", "per_min_20_plus", "n0_10", "n10_20", "n20"])
        for h, a, b, c, n1, n2, n3 in rows:
            wr.writerow([h, round(a, 1), round(b, 1), round(c, 1), n1, n2, n3])
    print("\nwrote", os.path.basename(q3a))
    q3b = os.path.join(args.out, "q3b_hero_state_winrate.csv")
    with open(q3b, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f); wr.writerow(["hero", "state", "n", "win", "winrate"])
        for hero in sorted(per_hero_state):
            for st in ("lead", "even", "trail"):
                n, w = per_hero_state[hero].get(st, [0, 0])
                if n:
                    wr.writerow([hero, st, n, w, round(w / n, 4)])
    print("wrote", os.path.basename(q3b))
    print("\ndone")


if __name__ == "__main__":
    sys.exit(main())
