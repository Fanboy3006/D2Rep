#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q7_clock_check.py — 量测「暂停感知时基」对 Q7 回放数据（英雄位置）的实际影响（A/B）。

对比两种 `t_tick → 显示秒` 折算：
  · legacy = `timebase.LegacyClock`（旧口径：二分取 ≥tt 的最小 combat 记录）
  · new    = `timebase.Clock`      （暂停感知：锚点 + "暂停时实体静止"的物理证据摊分）

**实测结论（先看这个，别把改动量想大）**：45 场抽样下，「游戏窗口内」两口径的 |Δ显示秒|
中位 0.03~0.07s、最大 ≤9.6s、>30s 的 **0 场**；大的偏差（可达 ~1600s）全在窗口外
（pre-game / post-game），会被 viewer 的 `[T0,T1]` 裁掉。所以这次改动是**加固**而非"修掉一个大错"。

量四件事（都按 UI 的真实取值语义「缺秒沿用上一秒」算，否则会把"1 秒的孔"误报成"整段丢失"）：
  ① 英雄·秒 中被折算改变的条数与 |Δdisp|（含窗口内外分开看）；
  ② 出门期（-1:30→0:00）地图上到底能不能画出人、画错多远；
  ③ **独立交叉校验**：初始金 600 是 combat 事件（t_cle 直得，不需折算），
     英雄首个采样是实体快照（必须折算）—— 两者都应落在"出门 ≈ −1:30"，这是映射对不对的硬证据；
  ④ 样例表（出门期若干时刻的可见英雄数与坐标）。

用法：
  python analysis/q7_clock_check.py                      # 3 场细看
  python analysis/q7_clock_check.py --scan 45            # 抽样 45 场，出影响面分布（真正的量测）
"""
import argparse
import glob as _glob
import math
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import timebase as tb   # noqa: E402

DBFULL = os.path.join(ROOT, "dems", "db_full")
DEFAULT = ["8830423116", "8825993964", "8955197224"]
HOLD = 12        # 与 viewer 一致：缺秒最多沿用上一秒 12s


def find(mid):
    h = _glob.glob(os.path.join(DBFULL, "*", "%s.db" % mid))
    return h[0] if h else None


def build(rows, clock):
    """按某口径把英雄采样归档到显示秒（同秒取最后一条）。"""
    pos = {}
    for t, e, x, y, hp in rows:
        d = clock.disp(t)
        if d is None:
            continue
        pos.setdefault(e, {})[int(round(d))] = (x, y, hp)
    return pos


def at(pos, npc, t):
    """UI 语义：精确命中，否则往前沿用 ≤HOLD 秒。"""
    p = pos.get(npc)
    if not p:
        return None
    if t in p:
        return p[t]
    for b in range(1, HOLD + 1):
        if (t - b) in p:
            return p[t - b]
    return None


def scan(n):
    """抽样 N 场，量"游戏窗口内"两种口径的偏差（含窗口外的偏差单列，避免夸大影响面）。"""
    import glob as _g
    dbs = sorted(_g.glob(os.path.join(DBFULL, "*", "*.db")))
    step = max(1, len(dbs) // n)
    sample = dbs[::step][:n]
    print("=" * 96)
    print("「暂停感知时基」影响面扫描：%d 场（抽样自 %d 场）" % (len(sample), len(dbs)))
    print("  游戏窗口 = [出门, 远古被摧毁]=[disp≈−90, end_disp]；窗口内的偏差才是用户看得见的")
    print("=" * 96)
    print("%-11s %7s %8s %10s %10s %10s %8s" %
          ("match", "暂停s", "窗口外max", "窗口内max", "窗口内中位", "超2s秒数", "总秒数"))
    rows_out = []
    for db in sample:
        mid = os.path.basename(db)[:-3]
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
            con.row_factory = sqlite3.Row
            o, nw = tb.LegacyClock(con, mid), tb.Clock(con, mid)
            hmin = nw.hero_tt_min
            con.close()
        except Exception as e:
            print("  %s 失败: %s" % (mid, e))
            continue
        if hmin is None:
            continue
        w0, w1 = hmin, int(nw.horn_tt + max(0.0, nw.end_disp or 0)) + 2
        inw, outw = [], []
        for tt in range(int(hmin), 4310, 1):
            a, b = o.disp(tt), nw.disp(tt)
            if a is None or b is None:
                continue
            d = abs(a - b)
            (inw if w0 <= tt <= w1 else outw).append(d)
        if not inw:
            continue
        inw.sort()
        over2 = sum(1 for d in inw if d > 2.0)
        rows_out.append((mid, nw.meta["pause_sec_total"], inw[-1], inw[len(inw) // 2], over2, len(inw)))
        if nw.meta["pause_sec_total"] and nw.meta["pause_sec_total"] > 3:
            print("%-11s %7.1f %8.1f %10.2f %10.2f %10d %8d" %
                  (mid, nw.meta["pause_sec_total"], max(outw) if outw else 0.0,
                   inw[-1], inw[len(inw) // 2], over2, len(inw)))
    print("-" * 96)
    if not rows_out:
        print("无可用样本"); return
    bad = [r for r in rows_out if r[2] > 2.0]
    worst = sorted(rows_out, key=lambda r: -r[2])[:10]
    print("样本 %d 场 ｜ 窗口内 max|Δdisp| > 2s 的场次 = %d（%.1f%%）｜ > 30s 的 = %d ｜ > 300s 的 = %d"
          % (len(rows_out), len(bad), 100.0 * len(bad) / len(rows_out),
             sum(1 for r in rows_out if r[2] > 30), sum(1 for r in rows_out if r[2] > 300)))
    print("\n窗口内偏差最大的 10 场：")
    print("  %-11s %8s %12s %12s %10s" % ("match", "暂停s", "窗口内max", "窗口内中位", "超2s秒数"))
    for mid, p, mx, med, o2, tot in worst:
        print("  %-11s %8.1f %12.2f %12.2f %10d / %d" % (mid, p, mx, med, o2, tot))
    print("\n说明：窗口外偏差（pre-game tt<首采样、post-game tt>结束）在 viewer 里被 [T0,T1] 裁掉，不计入影响。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matches", nargs="*", default=DEFAULT)
    ap.add_argument("--probe", nargs="*", type=int, default=[-85, -75, -60, -45, -30, -15, 0])
    ap.add_argument("--scan", type=int, default=0,
                    help="抽样 N 场，统计两种口径在【游戏窗口内】的偏差分布（这才是真正的影响面）")
    args = ap.parse_args()

    if args.scan:
        return scan(args.scan)

    tch = tpts = 0
    print("=" * 90)
    print("Q7 时基 A/B：旧口径(LegacyClock) vs 新口径(Clock)")
    print("=" * 90)
    for mid in args.matches:
        db = find(mid)
        if not db:
            print("  %s：找不到 .db，跳过" % mid)
            continue
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        old, new = tb.LegacyClock(con, mid), tb.Clock(con, mid)
        rows = [(int(r["t"]), r["e"], r["x"], r["y"], r["hp"]) for r in con.execute(
            "SELECT game_time_sec t, entity_id e, x, y, hp FROM entity_snapshots "
            "WHERE entity_type='hero' ORDER BY game_time_sec")]
        npcs = sorted({e for _, e, _, _, _ in rows})
        po, pn = build(rows, old), build(rows, new)

        # ① disp 变化
        ch, dd = 0, []
        for t, e, _, _, _ in rows:
            a, b = old.disp(t), new.disp(t)
            if a is None or b is None:
                continue
            if abs(a - b) >= 0.5:
                ch += 1; dd.append(abs(a - b))
        tch += ch; tpts += len(rows)

        print("\nmatch %s ｜ 英雄采样 %d 条 ｜ 暂停 %.1fs%s ｜ 英雄首采样 tt=%s"
              % (mid, len(rows), new.meta["pause_sec_total"],
                 ("（块 " + "、".join("%.0fs" % b[2] for b in new.meta["pause_blocks"]) + "）")
                 if new.meta["pause_blocks"] else "（无）", new.hero_tt_min))
        print("  ① 折算后显示时刻被改变的英雄·秒 = %d / %d（%.2f%%）｜ |Δdisp| 最大 %.1fs 中位 %.1fs"
              % (ch, len(rows), 100.0 * ch / max(1, len(rows)),
                 max(dd) if dd else 0.0, sorted(dd)[len(dd) // 2] if dd else 0.0))

        # ② 出门期可见性 + 位置误差
        gate = list(range(-90, 1))
        seen_o = sum(1 for t in gate for n in npcs if at(po, n, t))
        seen_n = sum(1 for t in gate for n in npcs if at(pn, n, t))
        errs = []
        for t in gate:
            for n in npcs:
                a, b = at(po, n, t), at(pn, n, t)
                if a and b:
                    errs.append(math.hypot(a[0] - b[0], a[1] - b[1]))
        print("  ② 出门期（-1:30→0:00，91 秒 × %d 人）：地图上画得出人的「英雄·秒」 旧 %d ｜ 新 %d"
              % (len(npcs), seen_o, seen_n))
        if errs:
            errs.sort()
            print("     两口径都画得出时的**位置差**（世界单位）：中位 %.0f ｜ p90 %.0f ｜ 最大 %.0f"
                  "（地图半径 8600，一格 172 → 中位 ≈ %.1f 格）"
                  % (errs[len(errs) // 2], errs[int(len(errs) * 0.9)], errs[-1], errs[len(errs) // 2] / 172))

        # ③ 独立交叉校验
        r = con.execute("SELECT t_cle FROM combat_log WHERE match_id=? AND type_category='gold' "
                        "AND value=600 ORDER BY t_cle LIMIT 1", (mid,)).fetchone()
        if r and new.hero_tt_min is not None:
            ev = new.disp_cle(r["t_cle"])                      # combat 事件：不需折算
            print("  ③ 交叉校验（初始金600 combat事件 disp=%.2f ｜ 英雄首采样 旧=%.2f 新=%.2f）：旧差 %.1fs，新差 %.1fs"
                  % (ev, old.disp(new.hero_tt_min), new.disp(new.hero_tt_min),
                     abs(old.disp(new.hero_tt_min) - ev), abs(new.disp(new.hero_tt_min) - ev)))
        # ③b 建筑 spawn（game_events 走回放钟，需折算）vs combat 里该塔被摧毁的时刻
        bs = con.execute("SELECT MIN(game_time_sec) m FROM game_events WHERE match_id=? AND event_type='building_spawn'",
                         (mid,)).fetchone()
        if bs and bs["m"] is not None:
            print("  ③b 建筑 spawn tt=%d → 旧 disp=%s 新 disp=%s（应 ≈ 出门前/出门附近，负数）"
                  % (bs["m"], old.fmt(old.disp(bs["m"])), new.fmt(new.disp(bs["m"]))))

        # ④ 样例
        print("  ④ 出门期样例（显示时刻 → 地图可见英雄数 旧/新）：")
        for s in args.probe:
            no = sum(1 for n in npcs if at(po, n, s))
            nn_ = sum(1 for n in npcs if at(pn, n, s))
            line = "     t=%-7s 旧 %2d 人 ｜ 新 %2d 人" % (new.fmt(s), no, nn_)
            if 0 < nn_ <= 3:
                line += " ｜ 新: " + "; ".join(
                    "%s(%.0f,%.0f)" % (n.replace("npc_dota_hero_", ""), at(pn, n, s)[0], at(pn, n, s)[1])
                    for n in npcs if at(pn, n, s))
            print(line)
        con.close()

    print("\n" + "=" * 90)
    print("合计：%d / %d 条英雄·秒 的显示时刻被改变（%.2f%%）"
          % (tch, tpts, 100.0 * tch / max(1, tpts)))
    print("受影响的是**快照类**（位置/净值）；经济·经验·KDA·正反补等**事件类**走 t_cle，不受影响。")


if __name__ == "__main__":
    main()
