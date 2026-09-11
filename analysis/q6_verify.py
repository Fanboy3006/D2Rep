#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q6_verify.py — Q6 判型/健康度互验(常驻可复跑), 对上任务书 §7 的两行验证。

A. 判型互验(硬证据, 取代已证伪的"逐场计数法"):
   游戏自身的寿命机制: 每支"到期"的眼, (销毁 − 放置) 必须**精确等于**该型寿命(假眼 360 / 真眼 420)。
   若类名判型把真/假对调, 真眼就会落在 +360 而不是 +420 → 立刻暴露。
   ⚠ 只统计**未被右删失截断**的到期眼(censored=False): 被比赛结束截断的眼, 销毁 = 结束时刻, 差值 < 寿命是正常的。
   (旧文档写过"实体类名判型计数 vs Death 权威单位名逐场相等(40/40)"—— **那个方法不成立**: 全量 placed>death,
    差额正是右删失, 与判型无关。见 analysis/Q5_RULES_CALIBRATION_LOG.md §18.1。)

B. 健康度: 存活上限 ≤ 寿命;超寿命行应为 0。
C. 计数对账(取代旧"逐场相等"): 逐型给出 眼数 / 权威单位名死亡数 / 配对上的死亡数 / 截断数 / 未配到死亡的眼数,
   让"placed 与 death 的差额"有出处可查。

用法: python analysis/q6_verify.py [--sample 200]
输出: analysis/output_q6/q6_verify.json ; 退出码 0=全过 / 1=有失败
"""
import argparse
import collections
import glob as _glob
import io
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import q5_ward as q

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "analysis", "output_q6")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, default=0, help="抽多少场(按文件名序); 0 = 全部 970 场")
    a = ap.parse_args()

    dbs = sorted(_glob.glob(os.path.join(ROOT, "dems", "db_full", "*", "*.db")))
    if a.sample:
        dbs = dbs[:a.sample]
    life = {"observer": q.OBSERVER_LIFE, "sentry": q.SENTRY_LIFE}
    exact = collections.Counter()       # 到期且未截断: |(销毁-放置) - 寿命| <= 0.01
    inexact = []                        # 反例
    n_exp = collections.Counter()       # 到期且未截断的眼数
    n_matched_exp = collections.Counter()   # 其中"配对到了到期死亡记录"的(证据来自 Death)
    over = []                           # 存活超寿命
    cnt = collections.Counter()         # 计数对账
    fails = []
    for db in dbs:
        mid = int(os.path.basename(db)[:-3])
        con = sqlite3.connect(db); con.row_factory = sqlite3.Row
        try:
            obs, sen, _ = q.parse_match(con, mid)
            # 权威单位名死亡池大小(用于对账)
            for r in con.execute("SELECT target, COUNT(*) c FROM combat_log WHERE match_id=? AND type_category='death' "
                                 "AND target IN ('npc_dota_observer_wards','npc_dota_sentry_wards') GROUP BY target", (mid,)):
                wt = "sentry" if r[0] == "npc_dota_sentry_wards" else "observer"
                cnt["death_" + wt] += r[1]
        finally:
            con.close()
        for rec in list(obs) + list(sen):
            wt = rec["type"]
            cnt["eyes_" + wt] += 1
            s = rec["survival"]
            if s is not None and s > life[wt] + 0.01:
                over.append((mid, wt, rec["survival"], rec["reason"]))
            if rec.get("censored"):
                cnt["censored_" + wt] += 1
            if rec.get("reason") == "expired" and not rec.get("censored"):
                n_exp[wt] += 1
                if abs((rec["destroy"] - rec["place"]) - life[wt]) <= 0.01:
                    exact[wt] += 1
                else:
                    inexact.append((mid, wt, rec["place"], rec["destroy"], rec["reason"]))
    ok_a = all(n_exp[k] > 0 and exact[k] == n_exp[k] for k in ("observer", "sentry"))
    ok_b = not over
    print("[Q6 verify] matches=%d" % len(dbs))
    for k in ("observer", "sentry"):
        print("  A %-8s 到期未截断 %5d / 精确等于寿命(%.0fs) %5d  命中率 %s"
              % (k, n_exp[k], life[k], exact[k],
                 ("%.2f%%" % (100.0 * exact[k] / max(1, n_exp[k]))) if n_exp[k] else "n/a"))
    print("  B 存活超寿命行: %d" % len(over))
    print("  C 眼数: observer %d / sentry %d ; 权威单位名死亡: observer %d / sentry %d ; 截断: observer %d / sentry %d"
          % (cnt["eyes_observer"], cnt["eyes_sentry"], cnt["death_observer"], cnt["death_sentry"],
             cnt["censored_observer"], cnt["censored_sentry"]))
    if inexact:
        fails.append("A: %d 支到期眼的 销毁-放置 != 寿命, 例 %s" % (len(inexact), inexact[:3]))
    if over:
        fails.append("B: %d 支存活超寿命, 例 %s" % (len(over), over[:3]))
    print("  => " + ("ALL PASS" if not fails else "FAIL: " + " | ".join(fails)))
    json.dump({"matches": len(dbs), "sample": a.sample,
               "A_expired_not_censored": {k: n_exp[k] for k in n_exp},
               "A_exact_lifetime": {k: exact[k] for k in exact},
               "A_fail_examples": inexact[:10],
               "B_over_life": over[:10], "B_over_life_count": len(over),
               "C_counts": dict(cnt),
               "note": "判型互验用的是【寿命机制】: 到期眼 销毁-放置 == 该型寿命(假眼360/真眼420)。"
                       "旧的'逐场计数相等'法不成立(差额=右删失), 见 Q5_RULES_CALIBRATION_LOG §18.1",
               "failures": fails},
              io.open(os.path.join(OUT, "q6_verify.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("wrote", os.path.join(OUT, "q6_verify.json"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
