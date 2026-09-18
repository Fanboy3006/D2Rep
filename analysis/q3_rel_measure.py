#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q3_rel_measure.py - Q3a 相对量: 英雄在场时 ①队伍净值增速 ②双方净值差变化(该队视角)。

用精确净值(entity_type='networth'):
  队伍净值(sec) = 该队5人逐秒净值求和
  gold_adv(sec) = radiant队伍净值 - dire队伍净值
  该队视角 adv = gold_adv (天辉) | -gold_adv (夜魇)
按窗口(0-10/10-20/20+):
  team_nw_gain/min = (队伍净值(win_end) - 队伍净值(win_start)) / minutes
  Δadv/min = (adv(win_end) - adv(win_start)) / minutes  -> 正=IMPROVE, 负=DECREASE
输出 CSV + 控制台。匹配(game_time_sec)基于 .dem 相对秒，注意中途起录场次前段窗口偏空。

Usage: python analysis/q3_rel_measure.py analysis/output_q3/*.db
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOWS = [("0-10", 0, 600), ("10-20", 600, 1200), ("20+", 1200, None)]
THR = 1000


def connect(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def load_identity(con, mid):
    return con.execute(
        "SELECT player_slot, hero_name, team_id FROM player_identity "
        "WHERE match_id=? AND hero_name!=''", (mid,)).fetchall()


def value_at(series, sec):
    ok = [s for s in series if s <= sec]
    if ok:
        return series[max(ok)]
    return series[min(series)] if series else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dbs", nargs="*", help="db files; or none -> glob dems/db/*/*.db")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "output_q3"))
    args = ap.parse_args()
    import glob as _g
    dbs = args.dbs or sorted(_g.glob(os.path.join(ROOT, "dems", "db", "*", "*.db")))
    rel = {}   # hero -> win -> list of (match_id, team_gain, adv_change)
    n_matches = 0

    for db in dbs:
        con = connect(db)
        for mid in [r[0] for r in con.execute("SELECT DISTINCT match_id FROM game_events ORDER BY match_id")]:
            ident = load_identity(con, mid)
            if not ident:
                continue
            # per-player net worth series + hero->team
            player_series = {}   # (team, idx) -> {sec: nw}
            hero_of = {}         # (team, idx) -> hero_name
            for r in con.execute(
                    "SELECT entity_id, game_time_sec, hp FROM entity_snapshots "
                    "WHERE match_id=? AND entity_type='networth'", (mid,)):
                nwid = r["entity_id"]
                _, team, idx = nwid.split(":")
                player_series.setdefault((team, int(idx)), {})[r["game_time_sec"]] = r["hp"]
            for r in ident:
                if r["team_id"] == 2:
                    hero_of[("radiant", r["player_slot"])] = r["hero_name"]
                else:
                    hero_of[("dire", r["player_slot"] - 128)] = r["hero_name"]
            # --- game-start alignment: .dem clock includes pre-game; real game starts
            #     when the first player's net worth leaves 0 (or first non-starting gold).
            all_secs = sorted(set(s for s in player_series.values() for s in s))
            game_start = None
            for sec in all_secs:
                if any(v[sec] > 0 for v in player_series.values() if sec in v):
                    game_start = sec
                    break
            if game_start is None:
                continue
            # shift windows to game-relative seconds
            win_secs = [(w, lo + game_start, (hi + game_start) if hi is not None else None)
                        for w, lo, hi in WINDOWS]
            # team net worth per second
            team_nw = {"radiant": {}, "dire": {}}
            for (team, idx), s in player_series.items():
                for sec, nw in s.items():
                    team_nw[team][sec] = team_nw[team].get(sec, 0) + nw
            # per hero on each team, per window
            for (team, idx), hero in hero_of.items():
                if (team, idx) not in player_series:
                    continue
                for w, lo, hi in win_secs:
                    t = team_nw[team]
                    v_lo = value_at(t, lo)
                    v_hi = value_at(t, hi) if hi is not None else value_at(t, 10 ** 9)
                    if v_lo is None or v_hi is None:
                        continue
                    mins = (hi - lo) / 60.0 if hi is not None else (max(t) - lo) / 60.0
                    if mins <= 0:
                        continue
                    gain = (v_hi - v_lo) / mins
                    rlo = value_at(team_nw["radiant"], lo); rhi = value_at(team_nw["radiant"], hi if hi is not None else 10 ** 9)
                    dlo = value_at(team_nw["dire"], lo); dhi = value_at(team_nw["dire"], hi if hi is not None else 10 ** 9)
                    if rlo is None or rhi is None or dlo is None or dhi is None:
                        continue
                    adv_lo = rlo - dlo; adv_hi = rhi - dhi
                    adv_total = adv_hi - adv_lo   # 窗口总变化(分档用)
                    hero_view = adv_total if team == "radiant" else -adv_total
                    rel.setdefault(hero, {}).setdefault(w, []).append((mid, gain, hero_view, mins))
            n_matches += 1
        con.close()

    import csv
    outd = os.path.join(args.out)

    # ---- 逐场明细附件（人类可一局局复核） ----
    detail = os.path.join(outd, "q3a_rel_detail.csv")
    with open(detail, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["hero", "window", "match_id", "team_nw_gain_per_min", "delta_adv_per_min", "net_thr%03d" % THR])
        for hero in sorted(rel):
            for win in ("0-10", "10-20", "20+"):
                for (mid, gain, adv_total, mins) in rel[hero].get(win, []):
                    net = "IMPROVE" if adv_total > THR else ("DECREASE" if adv_total < -THR else "flat")
                    w.writerow([hero, win, mid, round(gain, 1), round(adv_total, 1), net])
    print("  detail ->", os.path.basename(detail))

    # ---- 聚合：一行一英雄，窗口展开成列 ----
    import collections
    per = collections.defaultdict(lambda: {w: None for w in ("0-10", "10-20", "20+")})
    for hero in rel:
        for win in ("0-10", "10-20", "20+"):
            lst = rel[hero].get(win)
            if not lst:
                continue
            gains = [x[1] for x in lst]; advs = [x[2] for x in lst]
            n = len(lst)
            tg = sum(gains) / n
            rate = sum(a / m for a, m in zip(advs, [x[3] for x in lst])) / n
            net = [("IMPROVE" if a > THR else ("DECREASE" if a < -THR else "flat")) for a in advs]
            ni, nf, nd = net.count("IMPROVE"), net.count("flat"), net.count("DECREASE")
            tot = sum(abs(g) for g in gains)
            ms = max(abs(g) for g in gains) / tot if tot else 0
            per[hero][win] = {"n": n, "gain": tg, "adv": rate, "imp": ni, "flat": nf, "dec": nd, "ms": ms}

    hdr = ["hero", "n_0_10", "n_10_20", "n_20+", "gain_0_10", "gain_10_20", "gain_20+",
           "adv_0_10", "adv_10_20", "adv_20+", "imp_0_10", "imp_10_20", "imp_20+",
           "flat_0_10", "flat_10_20", "flat_20+", "dec_0_10", "dec_10_20", "dec_20+",
           "maxshare_0_10", "maxshare_10_20", "maxshare_20+"]
    agg = os.path.join(outd, "q3a_rel_agg_hero.csv")
    with open(agg, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        print("=" * 78)
        print("Q3a-REL 聚合(一行一英雄, 窗口展开成列): gain=队伍净值/min(该英雄在队), adv=Δadv/min(该队视角), imp/dec=盘数, maxshare=最大单场占比%")
        print("  %-38s %4s %5s %5s | %7s %7s %7s | %7s %7s %7s | %5s %5s %5s" % (
            "hero","n0-10","n10-20","n20+","g0-10","g10-20","g20+","a0-10","a10-20","a20+","im0-10","im10-20","im20+"))
        for hero in sorted(per):
            p = per[hero]
            def gv(k): return (round(p[k]["gain"], 0) if p[k] else "")
            def av(k): return (round(p[k]["adv"], 0) if p[k] else "")
            def nv(k): return (p[k]["n"] if p[k] else "")
            def iv(k): return (p[k]["imp"] if p[k] else "")
            def fv(k): return (p[k]["flat"] if p[k] else "")
            def dv(k): return (p[k]["dec"] if p[k] else "")
            def mv(k): return ((round(100 * p[k]["ms"], 1)) if p[k] else "")
            print("  %-38s %4s %5s %5s | %7s %7s %7s | %7s %7s %7s | %5s %5s %5s" % (
                hero, nv("0-10"), nv("10-20"), nv("20+"), gv("0-10"), gv("10-20"), gv("20+"),
                av("0-10"), av("10-20"), av("20+"), iv("0-10"), iv("10-20"), iv("20+")))
            w.writerow([hero, nv("0-10"), nv("10-20"), nv("20+"),
                        gv("0-10"), gv("10-20"), gv("20+"),
                        av("0-10"), av("10-20"), av("20+"),
                        iv("0-10"), iv("10-20"), iv("20+"),
                        fv("0-10"), fv("10-20"), fv("20+"),
                        dv("0-10"), dv("10-20"), dv("20+"),
                        mv("0-10"), mv("10-20"), mv("20+")])
    print("  agg ->", os.path.basename(agg))


if __name__ == "__main__":
    sys.exit(main())
