#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q7_winprob.py — Q7「状态胜率」模型：P(天辉胜 | 当前经济差、经验差、时刻)。

铁律（`STRATEGY/Q7_REPLAY_UI.md` §5.1）：**必须状态化，绝不偷看结果**。
  · 特征只用 **t 时刻可观测**的量：净值差（m_iNetWorth）、经验差（combat-log 累加）、t；
  · 标签 = 该场最终胜负；**训练/测试按 match 划分**（同场样本高度相关，绝不能跨集）；
  · 同一场比赛的多个时刻样本会被一起放进训练集或测试集（按 match 分组切分），避免信息泄漏。

标签来源（不依赖 OpenDota）：**远古被摧毁** —— `npc_dota_badguys_fort` 死 → 天辉胜；
`npc_dota_goodguys_fort` 死 → 夜魇胜。

模型：**分时间桶的逻辑回归 + 桶间线性插值系数**（每桶 3 参数：截距 + 经济差 + 经验差）
  桶 = [0,10) [10,20) [20,30) [30,40) [40,50) [50,∞) 分钟；桶中心 = 300/900/1500/2100/2700/3300 秒
  t 落在两中心之间时对系数线性插值（两端外推夹住）→ 对 t 连续、无跳变
  P = sigmoid(b0(t) + bg(t)·gd/1000 + bx(t)·xd/1000)
为什么不用单式 6 参数（带交互）：实测留出集 AUC 0.787 / Brier 0.1851 略差于分桶（0.790 / 0.1837），
且分桶的尾部校准（gd ±5000 档）更贴经验值。仅用经济差的模型 AUC 只有 0.617（说明经验差信息量更大）。
⚠️ 经济差与经验差在实测数据里正相关（r=0.52）→ **单看某个桶里某个系数的符号没有意义**，
   模型只在两者联合的实测分布上有意义（页面喂的就是实测值）。

产出：`analysis/output_q7/q7_winprob.json`（系数 + 验证指标 + 校准表 + 分桶经验胜率），供 viewer 内嵌。

用法：
  python analysis/q7_winprob.py                 # 全量 970 场（约 10~20 分钟）
  python analysis/q7_winprob.py --limit 120     # 先小样本试跑
  python analysis/q7_winprob.py --holdout 0.2   # 测试集比例（按 match 切分）
"""
import argparse
import glob as _glob
import json
import math
import os
import sqlite3
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timebase as tb   # noqa: E402

DBFULL = os.path.join(ROOT, "dems", "db_full")
OUT = os.path.join(ROOT, "analysis", "output_q7", "q7_winprob.json")
SAMPLE_STEP = 30        # 每 30 秒取一个样本（同一场内部高度相关，切分时按场分组）
EDGE = 30               # 距开场/结束各留 30s，避免边界噪声


def winner_of(con, mid):
    q = ("SELECT target, MIN(t_cle) t FROM combat_log WHERE match_id=? AND type_category='death' "
         "AND target IN ('npc_dota_goodguys_fort','npc_dota_badguys_fort') GROUP BY target")
    rows = list(con.execute(q, (mid,)))
    if not rows:
        return None
    g = {r["target"]: r["t"] for r in rows}
    if "npc_dota_badguys_fort" in g and "npc_dota_goodguys_fort" in g:
        return 1 if g["npc_dota_badguys_fort"] <= g["npc_dota_goodguys_fort"] else 0
    return 1 if "npc_dota_badguys_fort" in g else 0     # 只有一个被摧毁 → 另一边胜


def match_samples(db, step=SAMPLE_STEP):
    mid = int(os.path.basename(db)[:-3])
    con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    try:
        win = winner_of(con, mid)
        if win is None:
            return None
        clk = tb.Clock(con, mid)
        end = int(clk.end_disp or 0)
        if end < 300:
            return None
        # 净值：逐秒两队合计
        nw_team = {2: {}, 3: {}}
        key = {}
        for i, r in enumerate(con.execute("SELECT player_slot, team_id FROM player_identity ORDER BY player_slot")):
            key[i] = int(r["team_id"])
        for r in con.execute("SELECT game_time_sec t, entity_id e, hp FROM entity_snapshots "
                             "WHERE match_id=? AND entity_type='networth'", (mid,)):
            e = r["e"]          # nw:radiant:i / nw:dire:i
            team = 2 if e.startswith("nw:radiant") else 3
            d = nw_team[team]
            t = int(r["t"])
            d[t] = d.get(t, 0) + int(r["hp"] or 0)
        # 经验：逐秒逐英雄累计（combat-log xp 事件，target=英雄）
        hdr = [r["hero_name"] for r in con.execute("SELECT hero_name FROM player_identity ORDER BY player_slot")]
        team_of = {r["hero_name"]: int(r["team_id"])
                   for r in con.execute("SELECT hero_name, team_id FROM player_identity")}
        ev = {}
        for r in con.execute("SELECT t_cle, target, value FROM combat_log WHERE match_id=? "
                             "AND type_category='xp' AND target LIKE 'npc_dota_hero%'", (mid,)):
            d = clk.disp_cle(float(r["t_cle"]))
            if d is None:
                continue
            ev.setdefault(int(round(d)), []).append((r["target"], tb.gold_i32(r["value"]) or 0))
        xp_cum = {h: 0 for h in hdr}
        xp_team = {2: {}, 3: {}}
        for s in range(0, end + 1):
            for h, v in ev.get(s, ()):
                if h in xp_cum:
                    xp_cum[h] += v
            xp_team[2][s] = sum(xp_cum[h] for h in hdr if team_of.get(h) == 2)
            xp_team[3][s] = sum(xp_cum[h] for h in hdr if team_of.get(h) == 3)
        out = []
        for t in range(EDGE, end - EDGE + 1, step):
            gr = nw_team[2].get(t)
            gd_ = nw_team[3].get(t)
            if gr is None or gd_ is None:
                continue
            out.append((t, gr - gd_, xp_team[2].get(t, 0) - xp_team[3].get(t, 0), win))
        return mid, out
    except Exception as e:
        sys.stderr.write("  skip %s: %s\n" % (mid, e))
        return None
    finally:
        con.close()


def fit_logit(X, y, ridge=1e-6, iters=60):
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        z = X @ w
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        W = np.clip(p * (1 - p), 1e-9, None)
        H = X.T @ (X * W[:, None]) + ridge * np.eye(X.shape[1])
        g = X.T @ (y - p)
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        w = w + step
        if np.max(np.abs(step)) < 1e-9:
            break
    return w


def auc(y, p):
    y = np.asarray(y)
    p = np.asarray(p)
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(p)
    ranks = np.empty(len(p), float)
    ranks[order] = np.arange(1, len(p) + 1)
    # 平均并列名次
    sp = p[order]
    i = 0
    while i < len(sp):
        j = i
        while j + 1 < len(sp) and sp[j + 1] == sp[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return float((ranks[y == 1].sum() - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg)))


def features(t, gd_, xd_):
    tm = t / 1800.0
    return [1.0, gd_ / 1000.0, xd_ / 1000.0, tm, (gd_ / 1000.0) * tm, (xd_ / 1000.0) * tm]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--holdout", type=float, default=0.2)
    ap.add_argument("--step", type=int, default=SAMPLE_STEP)
    ap.add_argument("--refit-only", action="store_true", help="只用手里的样本缓存重拟合（不碰 DB）")
    args = ap.parse_args()

    cache = os.path.join(ROOT, "analysis", "output_q7", "winprob_samples.csv")
    per_match = []
    if args.refit_only:
        if not os.path.exists(cache):
            raise SystemExit("缺样本缓存 %s（先跑一次全量）" % cache)
        cur = None
        for line in open(cache, encoding="utf-8"):
            if line.startswith("#") or line.startswith("mid,"):
                continue
            mid, t, gd_, xd_, win = line.rstrip("\n").split(",")
            if cur is None or cur[0] != int(mid):
                cur = (int(mid), [])
                per_match.append(cur)
            cur[1].append((int(t), int(gd_), int(xd_), int(win)))
        print("从缓存载入 %d 场（%d 样本）" % (len(per_match), sum(len(r) for _, r in per_match)))
    else:
        dbs = sorted(_glob.glob(os.path.join(DBFULL, "*", "*.db")))
        if args.limit:
            st = max(1, len(dbs) // args.limit)
            dbs = dbs[::st][:args.limit]
        print("场次：%d（每 %ds 一个样本）" % (len(dbs), args.step))
        t0 = time.time()
        for i, db in enumerate(dbs):
            r = match_samples(db, args.step)
            if r:
                per_match.append(r)
            if i % 25 == 0:
                print("  %d/%d  用时 %.0fs  已收 %d 场" % (i, len(dbs), time.time() - t0, len(per_match)))
        print("收集完成：%d 场，用时 %.0fs" % (len(per_match), time.time() - t0))
        if not args.limit:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w", encoding="utf-8") as f:
                f.write("mid,t,gd,xd,win\n")
                for mid, rows in per_match:
                    for (t, gd_, xd_, win) in rows:
                        f.write("%d,%d,%d,%d,%d\n" % (mid, t, gd_, xd_, win))
            print("样本缓存 →", os.path.relpath(cache, ROOT))

    mids = [m for m, _ in per_match]
    rng = np.random.RandomState(20260911)
    idx = rng.permutation(len(mids))
    ntest = int(round(len(mids) * args.holdout))
    test_set = {mids[i] for i in idx[:ntest]}

    Xtr, ytr, Xte, yte, te_meta = [], [], [], [], []
    for mid, rows in per_match:
        for (t, gd_, xd_, win) in rows:
            f = features(t, gd_, xd_)
            if mid in test_set:
                Xte.append(f); yte.append(win); te_meta.append((mid, t, gd_, xd_))
            else:
                Xtr.append(f); ytr.append(win)
    Xtr = np.array(Xtr); ytr = np.array(ytr, float)
    Xte = np.array(Xte); yte = np.array(yte, float)
    print("训练样本 %d（场 %d）｜测试样本 %d（场 %d）" % (len(ytr), len(mids) - ntest, len(yte), ntest))

    # ── 分桶拟合（每桶 [1, gd/1000, xd/1000]）──
    BUCKETS = [(0, 600), (600, 1200), (1200, 1800), (1800, 2400), (2400, 3000), (3000, 10 ** 9)]
    CENTERS = [300.0, 900.0, 1500.0, 2100.0, 2700.0, 3300.0]
    ttr = Xtr[:, 3] * 1800.0
    tte = Xte[:, 3] * 1800.0
    coefs, bybucket = [], []
    for bi, (lo, hi) in enumerate(BUCKETS):
        mtr = (ttr >= lo) & (ttr < hi)
        mte = (tte >= lo) & (tte < hi)
        if mtr.sum() < 30 or mte.sum() < 5:
            coefs.append(coefs[-1] if coefs else [0.0, 0.0, 0.0])
            continue
        Xb = np.column_stack([np.ones(int(mtr.sum())), Xtr[mtr][:, 1], Xtr[mtr][:, 2]])
        w = fit_logit(Xb, ytr[mtr])
        coefs.append([float(x) for x in w])
        Xe = np.column_stack([np.ones(int(mte.sum())), Xte[mte][:, 1], Xte[mte][:, 2]])
        pe = 1.0 / (1.0 + np.exp(-np.clip(Xe @ w, -30, 30)))
        bybucket.append({"lo": lo, "hi": (None if hi >= 10 ** 9 else hi), "center": CENTERS[bi],
                         "n_train": int(mtr.sum()), "n_test": int(mte.sum()),
                         "coef": [round(float(x), 5) for x in w],
                         "auc_test": auc(yte[mte], pe),
                         "brier_test": float(np.mean((pe - yte[mte]) ** 2))})

    def coef_at(t):
        """按桶中心线性插值（两端夹住）——与页面 JS 完全同一套规则。"""
        if t <= CENTERS[0]:
            return coefs[0]
        if t >= CENTERS[-1]:
            return coefs[-1]
        for i2 in range(len(CENTERS) - 1):
            a, b = CENTERS[i2], CENTERS[i2 + 1]
            if a <= t <= b:
                f = (t - a) / (b - a)
                return [coefs[i2][k] + f * (coefs[i2 + 1][k] - coefs[i2][k]) for k in range(3)]
        return coefs[-1]

    def pred_block(X, ts):
        out = np.empty(len(ts))
        for i2 in range(len(ts)):
            w = coef_at(ts[i2])
            out[i2] = 1.0 / (1.0 + np.exp(-max(-30.0, min(30.0,
                w[0] + w[1] * X[i2, 1] + w[2] * X[i2, 2]))))
        return out

    ptr, pte = pred_block(Xtr, ttr), pred_block(Xte, tte)
    res = {
        "kind": "bucket_linear",
        "centers": CENTERS,
        "coefs": [[round(float(x), 5) for x in c] for c in coefs],
        "buckets": bybucket,
        "feature_desc": ["1", "gd/1000", "xd/1000"],
        "n_match": len(mids), "n_train": int(len(ytr)), "n_test": int(len(yte)),
        "holdout": args.holdout, "sample_step": args.step,
        "auc_train": auc(ytr, ptr), "auc_test": auc(yte, pte),
        "brier_test": float(np.mean((pte - yte) ** 2)),
        "base_rate": float(ytr.mean()),
        "winner_source": "combat_log 远古被摧毁（badguys_fort 死→天辉胜）",
        "features": "净值差 m_iNetWorth + 经验差 combat-log 累加 + 时刻（只含 t 时刻可观测信息；按 match 切分）",
        "collinearity_note": "经济差与经验差实测正相关 r=0.52 → 单个系数的符号不具解释意义，模型只在联合分布上有效",
    }
    # 校准表（测试集，按预测概率 10 分位）
    bins = np.linspace(0, 1, 11)
    calib = []
    for i2 in range(10):
        m = (pte >= bins[i2]) & (pte < bins[i2 + 1] if i2 < 9 else pte <= bins[i2 + 1])
        if m.sum() > 0:
            calib.append([round(float(bins[i2]), 2), round(float(bins[i2 + 1]), 2),
                          int(m.sum()), round(float(pte[m].mean()), 4), round(float(yte[m].mean()), 4)])
    res["calib_test"] = calib
    # 分桶经验胜率（全量，供人眼核对模型形状）：时间桶 × 经济差分档
    emp = []
    for lo, hi in BUCKETS:
        for glo, ghi in [(-10 ** 9, -5000), (-5000, -1500), (-1500, 1500), (1500, 5000), (5000, 10 ** 9)]:
            sel = [(gd_, win) for mid, rows in per_match for (t, gd_, xd_, win) in rows
                   if lo <= t < hi and glo <= gd_ < ghi]
            if len(sel) < 30:
                continue
            emp.append([lo, hi, glo, ghi, len(sel), round(sum(w2 for _, w2 in sel) / len(sel), 4)])
    res["empirical"] = emp
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("桶中心:", CENTERS)
    print("各桶系数 [b0, gd, xd]:", [[round(x, 3) for x in c] for c in coefs])
    print("AUC 测试 %.4f / 训练 %.4f ｜ Brier %.4f ｜ 基准胜率 %.3f"
          % (res["auc_test"], res["auc_train"], res["brier_test"], res["base_rate"]))
    print("分桶 AUC(测试):", ["%s-%s:%.3f" % (b["lo"] // 60, (b["hi"] // 60 if b["hi"] else "-"), b["auc_test"] or 0)
                            for b in bybucket])
    print("校准(测试集 10 分位): 预测→实际")
    for c in calib:
        print("   [%.1f,%.1f) n=%-6d pred=%.3f actual=%.3f" % tuple(c))
    print("wrote", os.path.relpath(OUT, ROOT))


if __name__ == "__main__":
    main()
