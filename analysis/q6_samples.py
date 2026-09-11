#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q6_samples.py — 从 Q6 产物里抽【owner 进游戏复核用】的样本, 并给出该眼的完整证据链。

用途(每次口径改动后复核清单都会变, 所以做成常驻工具而不是临时脚本):
  · --tricky N    抽 N 支"刁钻"假眼(默认按【与敌方真眼的最长共存】降序, 优先 <1050 单位内)
  · --censored N  抽 N 支【存活被比赛结束截断】的假眼(看"截断"如何改变存活与刁钻判定)
  · --worst N     抽 N 支【与被反时刻最贴边】(存活最短)的假眼(验证"被反"判定)
  · --at mid:x:y  复查指定眼(mid 与坐标都来自之前交付给 owner 的清单) —— 口径变了以后这些眼还在不在/数值变成多少
每条样本给出: 该眼(场次/战队/阵营/时刻/坐标/存活/是否被反/是否截断/是否刁钻)
            + 敌真眼证据(坐标/放置/销毁/共存时长/最近距离) —— 供直接进游戏核对。

用法:
  python analysis/q6_samples.py --tricky 3
  python analysis/q6_samples.py --at 8888160657:-1299:-4388 --at 8733879453:-7409:4
  python analysis/q6_samples.py --censored 3
退出码: 0 正常; 1 = 某项没找到对应实例
"""
import argparse
import glob as _glob
import io
import json
import math
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import q5_ward as q

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q6 = os.path.join(ROOT, "analysis", "output_q6")
IX = {"mi": 0, "x": 1, "y": 2, "team": 3, "win": 4, "place": 5, "destroy": 6, "surv": 7,
      "dew": 8, "tricky": 9, "n_es": 10, "d_min": 11, "ovl": 12, "cens": 13}
SIDE = {2: "天辉", 3: "夜魇"}


def mmss(s):
    m = int(abs(s) // 60)
    return "%s%d:%05.2f" % ("-" if s < 0 else "", m, abs(s) % 60)


def load():
    d = json.load(io.open(os.path.join(Q6, "q6_obs_instances.json"), encoding="utf-8"))
    return d


def find(d, mid, x, y, team=None):
    """按 (场次, 坐标) 找眼: 完全同格才可能撞车, 所以取【最近】的一条(并可指定阵营)。
    ⚠ 早先版本取"第一个落在 ±60 单位内"的行 -> 会把邻近的**另一支队/另一支眼**当成交付样本(实测踩到)。"""
    best, bd = None, None
    for r in d["inst"]:
        if d["mids"][r[IX["mi"]]] != mid:
            continue
        if team is not None and r[IX["team"]] != team:
            continue
        dd = math.hypot(r[IX["x"]] - x, r[IX["y"]] - y)
        if dd > 60:
            continue
        if bd is None or dd < bd:
            best, bd = r, dd
    return best


def evidence(d, row, radius, min_ovl=60.0):
    """回到 db 里取该眼的完整证据链(用共享解析器, 与产物同一真源)"""
    mi = row[IX["mi"]]
    mid = d["mids"][mi]
    db = _glob.glob(os.path.join(ROOT, "dems", "db_full", "*", "%d.db" % mid))[0]
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    gs = q.game_start(con, mid)
    end = q.game_end_cle(con, mid)
    obs, sen, _ = q.parse_match(con, mid)
    con.close()
    team = row[IX["team"]]
    o = min(obs, key=lambda o: math.hypot(o["x"] - row[IX["x"]], o["y"] - row[IX["y"]])
            + abs((o["place"] - gs) - row[IX["place"]]))
    enemy = 3 if team == 2 else 2
    ev = []
    for s in sen:
        if s["team"] != enemy or s["place"] is None or s["destroy"] is None:
            continue
        dd = math.hypot(s["x"] - o["x"], s["y"] - o["y"])
        if dd > radius:
            continue
        ov = min(o["destroy"], s["destroy"]) - max(o["place"], s["place"])
        if ov <= 0:
            continue
        ev.append({"x": s["x"], "y": s["y"], "place": s["place"] - gs, "destroy": s["destroy"] - gs,
                   "dist": dd, "ovl": ov, "dew": 1 if s.get("reason") == "dewarded" else 0,
                   "meets": ov >= min_ovl})          # 是否达"共存 ≥ 门槛"(与刁钻判定同一门槛)
    ev.sort(key=lambda x: -x["ovl"])
    org = d["orgs"][d["match_orgs"][str(mi)][0 if team == 2 else 1]]
    return {"mid": mid, "org": org, "row": row, "o": o, "gs": gs, "end": end - gs,
            "enemy_sentries": ev, "side": SIDE[team]}


def show(tag, e, radius, min_ovl):
    r, o = e["row"], e["o"]
    print("=" * 96)
    print("[%s] match %d  战队 %s  %s  眼坐标 (%d,%d)" % (tag, e["mid"], e["org"], e["side"], r[1], r[2]))
    print("   放置 %s(%.1fs)  销毁 %s(%.1fs)  存活 %.1fs  被反=%s  截断=%s  刁钻=%s(门槛: 存活>60s · 距离<=%.0f · 共存>=%.0fs)"
          % (mmss(r[5]), r[5], mmss(r[6]), r[6], r[7], "是" if r[8] else "否",
             "是" if r[13] else "否", "是" if r[9] else "否", radius, min_ovl))
    print("   该场真实结束(远古被摧毁) = %s(%.1fs)   |  命中记录 reason=%s censored=%s"
          % (mmss(e["end"]), e["end"], o.get("reason"), o.get("censored")))
    if not e["enemy_sentries"]:
        print("   ⚠ 存活期间 %.0f 单位内没有敌方真眼(不满足刁钻的距离条件)" % radius)
    for s in e["enemy_sentries"][:3]:
        print("   敌真眼 (%d,%d) 放置 %s 销毁 %s  共存 %ss  距离 %.0f  达门槛=%s  自身被反=%s"
              % (s["x"], s["y"], mmss(s["place"]), mmss(s["destroy"]), round(s["ovl"], 1),
                 s["dist"], "是" if s["meets"] else "**否**", "是" if s["dew"] else "否"))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tricky", type=int, default=0, help="抽 N 支刁钻假眼(按最长共存降序, 限最近敌真眼 <=1050)")
    ap.add_argument("--censored", type=int, default=0,
                    help="抽 N 支被比赛结束截断的假眼; --short 则按存活升序(最严重的高估)")
    ap.add_argument("--short", action="store_true", help="配合 --censored: 抽存活最短的截断眼(旧口径把它们记成 360s)")
    ap.add_argument("--worst", type=int, default=0, help="抽 N 支存活最短(最贴边被反)的假眼")
    ap.add_argument("--at", action="append", default=[],
                    help="复查指定眼: mid:x:y[:team] (可多次; 取坐标最近的一条, 避免抓到邻近的另一支队)")
    ap.add_argument("--radius", type=float, default=1200.0)
    a = ap.parse_args()

    d = load()
    inst = d["inst"]
    tr = d.get("tricky", {})
    mn_ovl = float(tr.get("min_overlap", 0.0))
    mn_surv = float(tr.get("min_surv", 60.0))
    print("# 本次口径: 存活 > %.0fs · 距离 <= %.0f · 共存 >= %.0fs(%s)"
          % (mn_surv, float(tr.get("radius", 1200.0)), mn_ovl,
             "owner 终版" if mn_ovl > 0 else "旧口径(只要窗口有交集)"))
    rc = 0
    if a.tricky:
        pool = [r for r in inst if r[IX["tricky"]] == 1 and 0 <= r[IX["d_min"]] <= 1050]
        pool.sort(key=lambda r: -r[IX["ovl"]])
        for r in pool[:a.tricky]:
            rc |= show("刁钻样本(共存长且近)", evidence(d, r, a.radius, mn_ovl), a.radius, mn_ovl)
    if a.censored:
        pool = [r for r in inst if r[IX["cens"]] == 1]
        pool.sort(key=lambda r: (r[IX["surv"]] if a.short else -r[IX["surv"]]))
        for r in pool[:a.censored]:
            rc |= show("截断样本(存活=结束-放置%s)" % ("·最严重" if a.short else ""),
                       evidence(d, r, a.radius, mn_ovl), a.radius, mn_ovl)
    if a.worst:
        pool = [r for r in inst if r[IX["dew"]] == 1]
        pool.sort(key=lambda r: r[IX["surv"]])
        for r in pool[:a.worst]:
            rc |= show("极短存活(被反判定)", evidence(d, r, a.radius, mn_ovl), a.radius, mn_ovl)
    for spec in a.at:
        parts = [int(v) for v in spec.split(":")]
        mid, x, y = parts[0], parts[1], parts[2]
        team = parts[3] if len(parts) > 3 else None
        row = find(d, mid, x, y, team)
        if row is None:
            print("=" * 96)
            print("[复查 %s] ⚠ 新产物里找不到该眼(口径/数据变了, 该样本已失效)" % spec)
            rc = 1
        else:
            show("复查 %s" % spec, evidence(d, row, a.radius, mn_ovl), a.radius, mn_ovl)
    return rc


if __name__ == "__main__":
    sys.exit(main())
