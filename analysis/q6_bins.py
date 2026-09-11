#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q6_bins.py — Q6 源数据的多分辨率聚合导出(任务书 §1: "源数据另出 1 / 4 / 16 三档供灵敏度对照")。

输入: analysis/output_q6/q6_obs_instances.json  (逐支假眼实例, 分析器的唯一真源)
输出: analysis/output_q6/q6_cell_<cs>.json      (cs ∈ 1/4/16/172)
      rows = [cx, cy, win, org, n, surv_sum, n_dew, n_tricky, n_cens, n_match]
        · cx,cy   格坐标(0..N-1, N = 2*MAP_HALF/cs)
        · win     时间窗 0/1/2 (0-7 / 7-20 / 20+)
        · org     战队索引(→ orgs[])
        · n       假眼支数 ; surv_sum 存活秒数合计(均值 = surv_sum/n)
        · n_dew/n_tricky/n_cens 被反/刁钻/截断支数
        · n_match 出场场次(该 (格,窗,战队) 涉及多少场比赛, 去重)
并按分辨率打印"灵敏度对照"表: 占用格数 / 明细行数 / 每格中位支数 / 各指标的格间均值。

为什么不在分析器里做: 三档只是同一批实例的不同分箱, 放这儿可以让分析器保持"一个口径真源",
而且换分箱不需要重跑 970 场(15 分钟) —— 本脚本秒级完成。

用法: python analysis/q6_bins.py [--cs 1,4,16,172]
"""
import argparse
import collections
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q6 = os.path.join(ROOT, "analysis", "output_q6")
IX = {"mi": 0, "x": 1, "y": 2, "team": 3, "win": 4, "place": 5, "destroy": 6, "surv": 7,
      "dew": 8, "tricky": 9, "n_es": 10, "d_min": 11, "ovl": 12, "cens": 13}


def median(v):
    if not v:
        return 0
    v = sorted(v)
    return v[len(v) // 2]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cs", default="1,4,16,172", help="要导出的格边长(逗号分隔)")
    ap.add_argument("--out", default=Q6)
    a = ap.parse_args()

    d = json.load(io.open(os.path.join(Q6, "q6_obs_instances.json"), encoding="utf-8"))
    I, HALF, WINS = d["inst"], d["map_half"], d["wins"]
    print("instances=%d  matches=%d  orgs=%d  tricky_def=%s" % (len(I), len(d["mids"]), len(d["orgs"]), d["tricky"]))

    NAMES = ["cs", "占用格数", "明细行数", "每格中位支数", "平均存活(格均值)", "被反率(格均值%)", "刁钻率(格均值%)", "出场场次(格均值)"]
    print("\n%-6s %9s %10s %12s %16s %16s %16s %16s" % tuple(NAMES))
    for cs in [int(x) for x in a.cs.split(",") if x.strip()]:
        n = int(round(2 * HALF / cs))
        agg = collections.defaultdict(lambda: {"n": 0, "ss": 0.0, "dew": 0, "tr": 0, "ce": 0, "mis": {}})
        for r in I:
            cx = int((r[IX["x"]] + HALF) // cs)
            cy = int((r[IX["y"]] + HALF) // cs)
            if not (0 <= cx < n and 0 <= cy < n):
                cx = max(0, min(n - 1, cx)); cy = max(0, min(n - 1, cy))
            org = d["match_orgs"][str(r[IX["mi"]])][0 if r[IX["team"]] == 2 else 1]
            c = agg[(cx, cy, r[IX["win"]], org)]
            c["n"] += 1; c["ss"] += r[IX["surv"]]; c["dew"] += r[IX["dew"]]
            c["tr"] += r[IX["tricky"]]; c["ce"] += r[IX["cens"]]; c["mis"][r[IX["mi"]]] = 1
        rows = [[k[0], k[1], k[2], k[3], c["n"], round(c["ss"], 1), c["dew"], c["tr"], c["ce"], len(c["mis"])]
                for k, c in sorted(agg.items())]
        cells = collections.defaultdict(lambda: {"n": 0, "ss": 0.0, "dew": 0, "tr": 0, "mis": {}})
        for r in rows:
            c = cells[(r[0], r[1])]
            c["n"] += r[4]; c["ss"] += r[5]; c["dew"] += r[6]; c["tr"] += r[7]
            c["mis"] = c["mis"]          # 格级出场场次不需要精确, 只用支数/存活/被反/刁钻做灵敏度对照
        cnts = [c["n"] for c in cells.values()]
        ms = [c["ss"] / c["n"] for c in cells.values() if c["n"]]
        dr = [100.0 * c["dew"] / c["n"] for c in cells.values() if c["n"]]
        trr = [100.0 * c["tr"] / c["n"] for c in cells.values() if c["n"]]
        ngm = [r[9] for r in rows]           # 出场场次按 (格,窗,战队) 行给(格级不去重相加无意义)
        print("%-6d %9d %10d %12d %16.1f %16.1f %16.1f %16.1f"
              % (cs, len(cells), len(rows), median(cnts), sum(ms) / max(1, len(ms)),
                 sum(dr) / max(1, len(dr)), sum(trr) / max(1, len(trr)), sum(ngm) / max(1, len(ngm))))
        p = os.path.join(a.out, "q6_cell_%d.json" % cs)
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump({"cs": cs, "map_half": HALF, "grid_n": n, "wins": WINS, "orgs": d["orgs"],
                       "tricky": d["tricky"], "resolution_sec": 0.1,
                       "cols": ["cx", "cy", "win", "org", "n", "surv_sum", "n_dew", "n_tricky", "n_cens", "n_match"],
                       "rows": rows}, f, ensure_ascii=False, separators=(",", ":"))
        print("   wrote %s  rows=%d" % (p, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
