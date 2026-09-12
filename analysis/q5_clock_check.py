#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q5_clock_check.py — 量测「暂停感知时基」对 Q5B 眼位口径的实际改动量（A/B）。

背景：`q5_ward.py::cle_for_tt` 旧实现是"二分取 ≥tt 的最小 combat 记录"，新实现走
`analysis/timebase.py`（锚点 + "暂停时实体静止"的物理证据摊分）。

**实测结论**：41 场 / 4591 支眼，**逐支眼字段变化 = 0（0.00%）**。
原因：`cle_for_tt` 只在 fallback 路径被调用，而实测 **100% 的眼都能匹配到 combat `use` 事件**
（`type_category='item'` + `inflictor=item_ward_*`，自带 `t_cle`，不需要折算）。
另：`destroy` 的匹配在 **tick 空间**做（`t1` 是实体末现、death 也有 `t_tick`），暂停在两边同时存在会抵消。
⇒ 这次改动是**潜在缺陷的加固**（不再依赖"暂停期间恰好有没有战斗日志条目"），**不是**产出数字的变动；
Q5B 的公网页面**不需要因此重算/重发**。

用法：
  python analysis/q5_clock_check.py                 # 默认抽 60 场 + 2 场已知带暂停的
  python analysis/q5_clock_check.py --sample 120
  python analysis/q5_clock_check.py --matches 8830423116 8955197224

输出：控制台汇总 + `analysis/output_q5/clock_ab/ab_<mode>.csv`（逐支眼，可人工核）
退出码：0（本脚本只报告差异，不做断言）
"""
import argparse
import csv
import glob as _glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "output_q5", "clock_ab")
KNOWN_PAUSED = ["8830423116", "8825993964"]


def dump(mode, dbs, path):
    """在子进程里跑一遍（LEGACY_CLOCK 在 import 时读取，必须换进程）。"""
    code = r'''
import os, sys, sqlite3, csv, json
sys.path.insert(0, r"%s")
import q5_ward
rows = []
for db in %r:
    mid = os.path.basename(db)[:-3]
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    try:
        obs, sen, orph = q5_ward.parse_match(con, mid)
    except Exception as e:
        sys.stderr.write("parse_match 失败 %%s: %%s\n" %% (mid, e)); con.close(); continue
    for r in obs + sen:
        rows.append([mid, r["type"], r["team"], r.get("entity_index"), r["reason"],
                     "" if r["place"] is None else round(r["place"], 2),
                     "" if r["destroy"] is None else round(r["destroy"], 2),
                     "" if r["survival"] is None else round(r["survival"], 2),
                     int(bool(r.get("censored"))), int(r.get("success", 0)), int(r.get("dew_sen", 0)),
                     round(r["x"], 1), round(r["y"], 1),
                     "" if r.get("actor") is None else r["actor"]])
    con.close()
with open(r"%s", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["match","type","team","eidx","reason","place","destroy","survival",
                "censored","success","dew_sen","x","y","actor"])
    w.writerows(rows)
print("dumped", len(rows), "rows ->", r"%s")
''' % (HERE, dbs, path, path)
    env = dict(os.environ, PYTHONUTF8="1")
    env["Q5_LEGACY_CLOCK"] = "1" if mode == "legacy" else "0"
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    sys.stdout.write("  [%s] %s\n" % (mode, (p.stdout or "").strip()))
    if p.returncode != 0:
        print(p.stderr[-2000:])
        raise SystemExit("dump 失败（mode=%s）" % mode)


def load(path):
    d = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["match"], r["type"], r["team"], r["eidx"], r["x"], r["y"])
            d.setdefault(key, []).append(r)
    return d


def fnum(v):
    return None if v in ("", None) else float(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--matches", nargs="*", default=None)
    args = ap.parse_args()

    all_dbs = sorted(_glob.glob(os.path.join(ROOT, "dems", "db_full", "*", "*.db")))
    if args.matches:
        dbs = [d for d in all_dbs if os.path.basename(d)[:-3] in set(args.matches)]
    else:
        step = max(1, len(all_dbs) // args.sample)
        dbs = all_dbs[::step][:args.sample]
        for mid in KNOWN_PAUSED:
            hit = [d for d in all_dbs if os.path.basename(d)[:-3] == mid]
            if hit and hit[0] not in dbs:
                dbs.append(hit[0])
    print("A/B 场次：%d 场（含已知带暂停的 %s）" % (len(dbs), ", ".join(KNOWN_PAUSED)))
    os.makedirs(OUT, exist_ok=True)
    pl = os.path.join(OUT, "ab_legacy.csv")
    pn = os.path.join(OUT, "ab_new.csv")
    dump("legacy", dbs, pl)
    dump("new", dbs, pn)

    L, N = load(pl), load(pn)
    keys = set(L) | set(N)
    only_l = set(L) - set(N)
    only_n = set(N) - set(L)
    diffs = []
    for k in sorted(keys & set(L) & set(N)):
        a, b = L[k][0], N[k][0]
        fa = (fnum(a["place"]), fnum(a["destroy"]), fnum(a["survival"]), a["reason"], int(a["censored"]),
              int(a["success"]), int(a["dew_sen"]))
        fb = (fnum(b["place"]), fnum(b["destroy"]), fnum(b["survival"]), b["reason"], int(b["censored"]),
              int(b["success"]), int(b["dew_sen"]))
        if fa != fb:
            diffs.append((k, a, b))
    n = len(keys & set(L) & set(N))
    print("\n" + "=" * 78)
    print("逐支眼总数  旧=%d  新=%d  可比对=%d" % (len(L), len(N), n))
    print("仅在旧口径出现=%d ｜ 仅在新口径出现=%d" % (len(only_l), len(only_n)))
    print("字段有变化的眼 = %d（%.2f%%）" % (len(diffs), 100.0 * len(diffs) / max(1, n)))
    bym = {}
    for k, a, b in diffs:
        bym.setdefault(k[0], []).append((a, b))
    if bym:
        print("\n按场次（只列有变化的场，前 15）：")
        for mid, v in sorted(bym.items(), key=lambda x: -len(x[1]))[:15]:
            dpl = [abs((fnum(b["place"]) or 0) - (fnum(a["place"]) or 0)) for a, b in v]
            print("   %s  变化 %3d 支 ｜ |Δ放置| 最大 %7.1fs 中位 %6.1fs" % (
                mid, len(v), max(dpl), sorted(dpl)[len(dpl) // 2]))
    if diffs:
        print("\n明细样例（前 12 条）：")
        print("   %-11s %-8s %-6s %-10s %14s %14s" % ("match", "type", "team", "field", "旧", "新"))
        for k, a, b in diffs[:12]:
            for f in ("place", "destroy", "survival", "reason", "success", "dew_sen"):
                if a[f] != b[f]:
                    print("   %-11s %-8s %-6s %-10s %14s %14s" % (k[0], k[1], k[2], f, a[f], b[f]))
                    break
    # 已知带暂停场次的细看
    print("\n已知带暂停场次细看（8830423116 = 出门期 531s 暂停；8825993964 = 中期亦有两段）：")
    for mid in KNOWN_PAUSED:
        sub = [(k, a, b) for k, a, b in diffs if k[0] == mid]
        tot = len([k for k in keys if k[0] == mid])
        print("   %s：%d/%d 支眼字段变化" % (mid, len(sub), tot))
        for k, a, b in sub[:6]:
            print("      %-8s %-6s eidx=%s 放置 %s → %s ｜ 销毁 %s → %s ｜ %s → %s" % (
                k[1], k[2], k[3], a["place"], b["place"], a["destroy"], b["destroy"], a["reason"], b["reason"]))
    print("\n逐支眼明细：%s / %s" % (os.path.relpath(pl, ROOT), os.path.relpath(pn, ROOT)))


if __name__ == "__main__":
    main()
