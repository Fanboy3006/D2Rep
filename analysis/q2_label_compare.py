#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q2_label_compare.py - Q2 复核：规则标签 vs owner 人工标签（并排 + 一致性 + 分歧）。

规则标签（由数据 gold_adv 派生，映射到 owner 的 WIN/DRAW/LOST 语义）：
  10min状态: lead->WIN(对线优)  even->DRAW(对线平)  trail->LOST(对线劣)
  10->20方向: turn_up->WIN(中期优)  flat->DRAW(中期平)  turn_down->LOST(中期劣)
owner 人工标签：来自 XG/VG/TS统计.xlsx Sheet1 的「对线情况」「中期情况」(WIN/DRAW/LOST)。

输出：一致性(规则==owner) + 分歧小表(match_id, 规则 vs owner)。TY/PV 无 owner 表。
用法: python analysis/q2_label_compare.py
"""
import os
import sqlite3
import sys
import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATS = os.path.join(ROOT, "stats.db")
THR = 1000
TARGET = {"XG": {8261500}, "VG": {726228}, "TS": {7119388},
          "TY": {9823272}, "PV": {9572001, 9824702}}
XLSX = {"XG统计.xlsx": "XG", "VG统计.xlsx": "VG", "TS统计.xlsx": "TS"}


def rule_label(g, thr=THR):
    return "WIN" if g > thr else ("LOST" if g < -thr else "DRAW")


def main():
    con = sqlite3.connect(STATS)
    con.row_factory = sqlite3.Row
    rows = []  # (team, match_id, rule_s10, rule_s20, owner_s10, owner_s20)
    seen = set()

    # owner labels per match (from xlsx)
    owner = {}  # (team, match_id) -> (lane_label, mid_label)
    for xlsx, subj in XLSX.items():
        wb = openpyxl.load_workbook(os.path.join(ROOT, xlsx), data_only=True)
        ws = wb.worksheets[0]
        prev_lane = prev_mid = None
        for r in ws.iter_rows(values_only=True):
            if len(r) >= 9:
                mid = r[1]
                if isinstance(mid, (int, float)) and int(mid) > 0:
                    lane = r[9] if len(r) > 9 else None   # 对线情况
                    midl = r[10] if len(r) > 10 else None   # 中期情况
                    owner[(subj, int(mid))] = (
                        (str(lane).strip().upper() if lane is not None else None),
                        (str(midl).strip().upper() if midl is not None else None))

    # rule labels from stats.db (per team, per match)
    for team, tids in TARGET.items():
        for m in con.execute("SELECT match_id,radiant_team_id,dire_team_id,radiant_win FROM matches"):
            mid = m["match_id"]
            g10r = con.execute("SELECT value FROM gold_adv WHERE match_id=? AND minute=10", (mid,)).fetchone()
            g20r = con.execute("SELECT value FROM gold_adv WHERE match_id=? AND minute=20", (mid,)).fetchone()
            if not g10r or not g20r:
                continue
            g10 = g10r["value"]; g20 = g20r["value"]
            # does this team play? determine side
            side = None
            if m["radiant_team_id"] in tids: side = "radiant"
            elif m["dire_team_id"] in tids: side = "dire"
            if side is None:
                continue
            if side == "radiant":
                t10, t20 = g10, g20
            else:
                t10, t20 = -g10, -g20
            dw = t20 - t10
            rule_s10 = rule_label(t10)
            rule_s20 = rule_label(dw)
            o = owner.get((team, mid))
            rows.append({"team": team, "match_id": mid, "rule_s10": rule_s10,
                         "rule_s20": rule_s20,
                         "owner_s10": o[0] if o else None, "owner_s20": o[1] if o else None})

    # consistency + disagreement
    print("=" * 78)
    print("Q2 复核：规则标签(数据gold_adv) vs owner 人工标签  —— 一致性 + 分歧")
    print("规则: 10min状态(lead/even/trail)->WIN/DRAW/LOST；10->20(Δ)->WIN/DRAW/LOST")
    for team in sorted(TARGET):
        rr = [r for r in rows if r["team"] == team and r["owner_s10"] is not None]
        if not rr:
            continue
        cons_lane = sum(1 for r in rr if r["rule_s10"] == r["owner_s10"])
        cons_mid = sum(1 for r in rr if r["rule_s20"] == r["owner_s20"])
        print("\n  %s: 有owner标签=%d | 对线一致性=%d/%d=%.1f%% | 中期一致性=%d/%d=%.1f%%"
              % (team, len(rr), cons_lane, len(rr), 100 * cons_lane / len(rr),
                 cons_mid, len(rr), 100 * cons_mid / len(rr)))
        # disagreement small table
        dis = [r for r in rr if r["rule_s10"] != r["owner_s10"] or r["rule_s20"] != r["owner_s20"]]
        if dis:
            print("   分歧(%d):" % len(dis))
            for r in dis[:20]:
                print("      %s owner:%s/%s rule:%s/%s" % (r["match_id"], r["owner_s10"], r["owner_s20"],
                                                           r["rule_s10"], r["rule_s20"]))
    # write CSV
    import csv
    outd = os.path.join(ROOT, "analysis", "output_review")
    os.makedirs(outd, exist_ok=True)
    p = os.path.join(outd, "Q2_label_compare.csv")
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["team", "match_id", "rule_10min_state", "rule_10to20",
                    "owner_lane", "owner_mid"])
        for r in rows:
            w.writerow([r["team"], r["match_id"], r["rule_s10"], r["rule_s20"],
                        r["owner_s10"], r["owner_s20"]])
    print("\nwrote", p)


if __name__ == "__main__":
    sys.exit(main())
