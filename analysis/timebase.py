#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""timebase.py — 数据层共享时基 / 口径工具（Q5B / Q6 / Q7 共用，单一真相源）。

背景（为什么需要这个模块）
────────────────────────────────────────────────────────────────────
`combat_log` 里有**两条时钟**（`STRATEGY/DEM_FORMAT.md` §2/§5）：

| 字段 | 含义 | 暂停时 | 谁在用 |
|---|---|---|---|
| `t_cle` | 游戏时钟（叙事轴） | **冻结** | gold / xp / death / damage / modifier / ability / item 事件 |
| `t_tick` | 回放钟（`tick/30`，实体轴） | **照走** | `entity_snapshots.game_time_sec`、`game_events`(building/ward) |

**业务口径的时间轴 = 显示时钟**：`0:00 = 号角`（`gamestate value==5` 那刻的 `t_cle`），
`disp = t_cle − horn_cle`。出门 = −1:30。

**坑（本模块要解决的）**：快照类数据在 `t_tick` 轴上，必须折算到 `t_cle` 才能与事件对齐；
而 `t_cle − t_tick` **不是常数** —— 每发生一次暂停，游戏钟冻结而回放钟继续走，偏移就变一次。
实测 8830423116：暂停前 `+540.27`、暂停后 `+8.72`（差 531.55s）。
⇒ 任何"用一个常数偏移"的折算在带暂停的场次上必错。

**旧做法与它的实际误差（实测，别夸大）**：`q5_ward.py` 原先用 `bisect_left(t_tick)` 取
"≥tt 的最小 combat 记录的 t_cle"。它**不是**常数偏移，所以没有整段崩掉 —— 由于暂停期间
combat_log 仍会零星收到条目（泉水光环 modifier 等），"取右侧"落到的那条记录的 `cle`
通常离真值很近。45 场抽样实测（`analysis/q7_clock_check.py --scan 45`）：

| 区域 | 旧 vs 新 \|Δ显示秒\| |
|---|---|
| **游戏窗口内**（用户看得见的部分） | 中位 **0.03~0.07s**；最大 **≤ 9.6s**；>2s 的秒数占 **<1%**；**>30s 的 0 场** |
| 窗口外（pre-game `tt<首采样` / post-game `tt>结束`） | 最大可到 ~1600s（被 viewer 的 `[T0,T1]` 裁掉，无影响） |

所以本模块的价值不是"修掉一个巨大错误"，而是：① 把折算变成**有物理依据、可复算**的表
（锚点端点精确 + 活跃秒摊分），不再依赖"暂停期间恰好有没有战斗日志条目"这种运气；
② 顺带把"战斗日志稀疏段"（无暂停也会出现，实测窗口内最大 ~9.6s）一起收干净；
③ 给出 `pause_sec_total` / `pause_blocks` 这两个可直接复核的副产品
（8830423116：531.5s，3 块 347.8+23.6+159.6=531.0s，闭合 99.9%）。

用法
────────────────────────────────────────────────────────────────────
    import timebase as tb
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    ck = tb.Clock(con, mid)          # 建一次，复用
    ck.horn_cle, ck.horn_tt, ck.end_cle
    ck.disp_cle(1013.4)              # 事件 t_cle → 显示秒
    ck.disp(tt)                      # 回放钟秒 → 显示秒（快照类用这个）
    ck.cl(tt)                        # 回放钟秒 → t_cle
    tb.gold_i32(v)                   # gold.value 的 int32 下溢还原

自检：`python analysis/timebase.py <match_id>`（时钟诊断 + 与 stats.db 时长对账）
A/B ：`python analysis/q7_clock_check.py --matches <mid>` / `--scan 45`（影响面）
      `python analysis/q5_clock_check.py --sample 40`（对 Q5B 逐支眼的改动量）
"""

import argparse
import glob
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBFULL = os.path.join(ROOT, "dems", "db_full")
STATS_DB = os.path.join(ROOT, "stats.db")


# ───────────────────────── 常量 / 口径 ─────────────────────────
FORT_TARGETS = ("npc_dota_goodguys_fort", "npc_dota_badguys_fort")
HORN_STATE = 5                     # DOTA_GAMERULES_STATE_GAME_IN_PROGRESS
GATE_OPEN_DISP = -90.0             # 出门（初始金 600 事件实测落在 disp ≈ −89.9）

# `combat_log.gold.gold_reason` 的**实测**观察（不臆造官方枚举名；40 场抽样 144,018 行）
GOLD_REASON_SEEN = {
    0:  ("n=983  mean=+269.8", "起始/杂项（含出门初始金 600）"),
    1:  ("n=2153 mean=−232.0", "★死亡扣钱（**负值被按 uint32 落库** → 必须 gold_i32 还原；行数 == 英雄死亡数）"),
    5:  ("n=12   mean=+106.3", "未定名"),
    6:  ("n=1312 mean=+272.7", "未定名（大额）"),
    11: ("n=3537 mean=+129.4", "未定名"),
    12: ("n=9619 mean=+147.1", "未定名（大额、高频）"),
    13: ("n=68930 mean=+42.7", "线上兵补刀（小额高频，均值≈一个兵的赏金）"),
    14: ("n=46806 mean=+30.9", "中立兵（小额高频，均值≈一个野怪的赏金）"),
    15: ("n=820  mean=+165.7", "未定名"),
    16: ("n=641   mean=+72.9", "未定名"),
    17: ("n=4565 mean=+70.0", "未定名"),
    19: ("n=2970 mean=+23.7", "未定名（极小）"),
    20: ("n=540  mean=+208.0", "未定名"),
    21: ("n=134  mean=+69.4", "未定名"),
    22: ("n=996  mean=+33.1", "未定名"),
}
# 备注：官方枚举名未在本仓库的 proto（`.tmp/redota_src/dota/*.proto`）里出现，
#      上表只写"实测到的量级/频率"，**不写猜的名字**。要精确命名需另找权威来源。


def gold_i32(v):
    """`combat_log.gold.value` 的 int32 下溢还原。

    `CMsgDotaCombatLogEntry.value` 在 proto 里是 **uint32**，而死亡扣钱是负数 →
    Valve 把它按 uint32 发出（实测 `gold_reason=1` 的 2153/2153 行都 ≥ 2^31）。
    不还原会把单人金币合计撑到 4.29e9 量级（纯垃圾）。
    """
    if v is None:
        return None
    v = int(v)
    return v - (1 << 32) if v >= (1 << 31) else v


# ───────────────────────── 单点查询 ─────────────────────────
def horn_cle(con, mid):
    """号角（0:00）= `gamestate value==5` 的 `t_cle`。拿不到返回 None（**不硬造**）。"""
    r = con.execute(
        "SELECT t_cle, t_tick FROM combat_log WHERE match_id=? AND type_category='gamestate' AND value=? "
        "ORDER BY event_seq LIMIT 1", (mid, HORN_STATE)).fetchone()
    if not r:
        return None
    return float(r["t_cle"])


def horn_tt(con, mid):
    r = con.execute(
        "SELECT t_cle, t_tick FROM combat_log WHERE match_id=? AND type_category='gamestate' AND value=? "
        "ORDER BY event_seq LIMIT 1", (mid, HORN_STATE)).fetchone()
    return float(r["t_tick"]) if r else None


def game_end_cle(con, mid):
    """比赛结束 = **远古（Fort）被摧毁**那一刻的 `t_cle`（同 Q5B §8.15）。

    不要用 `MAX(t_cle)`：combat_log 在远古被摧毁后仍记录 360~925s 的结算残留
    （中位 ~398s），会把末段存活时间系统性高估。
    拿不到 fort 记录时回退 `MAX(t_cle)`（把 `end_source` 标出来，别假装精确）。
    """
    q = ("SELECT MIN(t_cle) FROM combat_log WHERE match_id=? AND type_category='death' AND target IN (%s)"
         % ",".join("?" * len(FORT_TARGETS)))
    r = con.execute(q, (mid,) + FORT_TARGETS).fetchone()
    if r and r[0] is not None:
        return float(r[0]), "fort_destroyed"
    r = con.execute("SELECT MAX(t_cle) FROM combat_log WHERE match_id=? AND "
                    "type_category IN ('damage','death','xp','gold')", (mid,)).fetchone()
    if r and r[0] is not None:
        return float(r[0]), "fallback_max_combat"
    return None, None


# ───────────────────────── 时钟（暂停感知） ─────────────────────────
class Clock(object):
    """回放钟(`t_tick`) → 游戏钟(`t_cle`) → 显示钟(`0:00=号角`) 的权威映射。

    构造时一次性建表（每场一次，可复用）。
    """

    def __init__(self, con, mid, verbose=False):
        self.mid = mid
        self.horn_cle = horn_cle(con, mid)
        self.horn_tt = horn_tt(con, mid)
        e, src = game_end_cle(con, mid)
        self.end_cle, self.end_source = e, src
        self._anchors = self._load_anchors(con)
        if not self._anchors:
            raise RuntimeError("match %s: combat_log 无可用锚点，无法建立时钟" % mid)
        self.delta_file = self._anchors[0][1] - self._anchors[0][0]
        act = self._activity(con)
        self._cle_at, self.meta = self._build(act)
        self.meta["anchors"] = len(self._anchors)
        self.meta["delta_file"] = round(self.delta_file, 3)
        self.meta["horn_cle"] = self.horn_cle
        self.meta["horn_tt"] = self.horn_tt
        self.meta["end_cle"] = self.end_cle
        self.meta["end_source"] = self.end_source
        if verbose:
            for k, v in self.meta.items():
                print("   %-18s %s" % (k, v))

    # ---- 建表 ----
    def _load_anchors(self, con):
        out = []
        for r in con.execute("SELECT t_tick, t_cle FROM combat_log WHERE match_id=? "
                             "AND t_tick IS NOT NULL AND t_cle IS NOT NULL ORDER BY t_tick, event_seq",
                             (self.mid,)):
            tt, cle = float(r["t_tick"]), float(r["t_cle"])
            if not out or tt > out[-1][0] + 1e-9:
                out.append((tt, cle))
            else:
                out[-1] = (out[-1][0], max(out[-1][1], cle))
        return out

    def _activity(self, con):
        """每秒是否"游戏在跑"：任一英雄位移>0.5 / hp 变，或任一玩家净值变。"""
        pos, nw = {}, {}
        for r in con.execute("SELECT game_time_sec t, entity_id e, x, y, hp FROM entity_snapshots "
                             "WHERE match_id=? AND entity_type='hero' ORDER BY game_time_sec", (self.mid,)):
            pos.setdefault(int(r["t"]), {})[r["e"]] = (float(r["x"] or 0), float(r["y"] or 0), int(r["hp"] or 0))
        for r in con.execute("SELECT game_time_sec t, entity_id e, hp FROM entity_snapshots "
                             "WHERE match_id=? AND entity_type='networth' ORDER BY game_time_sec", (self.mid,)):
            nw.setdefault(int(r["t"]), {})[r["e"]] = int(r["hp"] or 0)
        self.hero_tt_min = min(pos) if pos else None
        act, prev_p, prev_n = {}, None, None
        for t in sorted(set(pos) | set(nw)):
            a = 0
            cur_p = pos.get(t)
            if cur_p and prev_p:
                for e, v in cur_p.items():
                    p = prev_p.get(e)
                    if p and (abs(p[0] - v[0]) > 0.5 or abs(p[1] - v[1]) > 0.5 or p[2] != v[2]):
                        a = 1
                        break
            if not a:
                cur_n = nw.get(t)
                if cur_n and prev_n:
                    for e, v in cur_n.items():
                        if e in prev_n and prev_n[e] != v:
                            a = 1
                            break
            act[t] = a
            if cur_p:
                prev_p = cur_p
            if nw.get(t):
                prev_n = nw[t]
        return act

    def _build(self, act):
        cle_at, raw = {}, []
        for i in range(1, len(self._anchors)):
            a_tt, a_cle = self._anchors[i - 1]
            b_tt, b_cle = self._anchors[i]
            need = max(0.0, b_cle - a_cle)
            lo, hi = int(a_tt) + 1, int(b_tt) + 1
            span = list(range(max(lo, 0), max(hi, 0) + 1))
            if not span:
                continue
            w = [act.get(s, 0) for s in span]
            tot = sum(w)
            if tot <= 0:                     # 段内没探到活动 → 整段均分兜底（不丢信息）
                w, tot = [1] * len(span), len(span)
            gap = len(span) - need
            if gap > 1.0:
                raw.append((span[0], span[-1], gap))
            acc = 0.0
            for s, wi in zip(span, w):
                acc += wi
                cle_at[s] = a_cle + need * (acc / tot if tot else 1.0)
        # 暂停总时长：精确值 = 整段 Δ回放钟 − Δ游戏钟（未暂停时两者 1:1，战后结算也 1:1）
        (t0, c0), (t1, c1) = self._anchors[0], self._anchors[-1]
        exact = (t1 - t0) - (c1 - c0)
        # 暂停块（报告用）：逐秒看游戏钟有没有推进（未暂停 ≈ +1s/s），连续"没推进"的秒 = 一个暂停块。
        # ⚠️ 不能用"段与段相邻就合并"——锚点段本身是首尾相接的，那样会把整场并成一块。
        blocks, run = [], None
        prev = None
        for s in sorted(cle_at):
            if prev is None or s != prev + 1:
                adv = 1.0
            else:
                adv = cle_at[s] - cle_at[prev]
            if adv < 0.5:
                if run is None:
                    run = [s, s]
                run[1] = s
            else:
                if run is not None:
                    blocks.append(run)
                    run = None
            prev = s
        if run is not None:
            blocks.append(run)
        out = []
        hmin = getattr(self, "hero_tt_min", None)
        for s, e in blocks:
            if hmin is not None and e < hmin:
                continue        # 英雄出现之前的"无实体数据"段不算暂停（见下方 meta 注记）
            base = cle_at.get(s - 1, cle_at[s])
            gap = (e - s + 1) - (cle_at[e] - base)
            if gap >= 3.0:
                out.append([s, e, round(gap, 1)])
        return cle_at, {"pause_sec_total": round(exact, 1), "pause_blocks": out,
                        "pause_blocks_gap_sum": round(sum(b[2] for b in out), 1),
                        "hero_tt_min": hmin}

    # ---- 查询 ----
    def cl(self, tt):
        """回放钟秒 → 游戏钟(t_cle)。表外按首锚点 Δ_file 外推（无暂停区，安全）。"""
        if tt is None:
            return None
        t = int(tt)
        if t in self._cle_at:
            return self._cle_at[t]
        for d in (1, -1, 2, -2, 3, -3):
            if t + d in self._cle_at:
                return self._cle_at[t + d]
        return tt + self.delta_file

    def disp(self, tt):
        """回放钟秒 → **显示秒**（0:00 = 号角）。快照类数据用这个。"""
        c = self.cl(tt)
        return None if c is None or self.horn_cle is None else c - self.horn_cle

    def disp_cle(self, cle):
        """事件 `t_cle` → 显示秒。combat_log 事件类用这个。"""
        return None if cle is None or self.horn_cle is None else cle - self.horn_cle

    @property
    def end_disp(self):
        return self.disp_cle(self.end_cle)

    def fmt(self, disp_sec):
        if disp_sec is None:
            return "—"
        s = int(round(disp_sec))
        return "%s%d:%02d" % ("-" if s < 0 else "", abs(s) // 60, abs(s) % 60)


def con_mid(con):
    """便利函数：取库里第一条 match_id（单场库 = 该场）。"""
    r = con.execute("SELECT match_id FROM combat_log LIMIT 1").fetchone()
    return r[0] if r else None


# ───────────────── 旧口径（仅用于 A/B 对照，勿在新代码里用） ─────────────────
class LegacyClock(object):
    """**旧口径**：`bisect_left(t_tick) → 取 ≥tt 的最小 combat 记录的 t_cle`。

    这是 `q5_ward.py` 在 2026 订正前的实现。缺陷（实测边界，别夸大）：
      · 它跟着附近真实 combat 记录的 `cle` 走，所以**暂停不会让它整段崩**（暂停期间 combat_log
        仍会零星收到条目）；
      · 真正会错的是"**战斗日志稀疏段**"：当某段没有 combat 条目时，"取右侧"会跳到该段之后的
        第一条记录的 `cle`，误差 = 这段期间游戏钟的真实推进量。实测（45 场抽样，`--scan`）：
        窗口内中位 |Δ| 0.03~0.07s、最大 ≤9.6s、>30s 的 0 场；窗口外（pre/post-game）可达 ~1600s。
      · 暂停感知的新口径同时收掉"暂停"与"稀疏段"两类误差，且给出可复核的暂停块。

    保留它只为可复现地量测"这次改动到底改了多少"（`analysis/q7_clock_check.py`）。
    """

    def __init__(self, con, mid):
        self.horn_cle = horn_cle(con, mid)
        self.horn_tt = horn_tt(con, mid)
        e, src = game_end_cle(con, mid)
        self.end_cle, self.end_source = e, src
        self._tt, self._cle = [], []
        for r in con.execute("SELECT t_tick, t_cle FROM combat_log WHERE match_id=? AND t_tick IS NOT NULL "
                             "ORDER BY t_tick", (mid,)):
            self._tt.append(float(r["t_tick"]))
            self._cle.append(float(r["t_cle"]))
        imp = [a[1] - a[0] for a in zip(self._tt, self._cle)]
        self.meta = {"anchors": len(self._tt), "delta_file": None, "pause_sec_total": None,
                     "pause_blocks": [], "pause_blocks_gap_sum": 0.0,
                     "delta_first": round(imp[0], 3) if imp else None,
                     "delta_last": round(imp[-1], 3) if imp else None}
        self.delta_file = imp[0] if imp else 0.0

    def cl(self, tt):
        import bisect
        if not self._tt or tt is None:
            return None
        i = bisect.bisect_left(self._tt, tt)
        idx = i if i < len(self._tt) else len(self._tt) - 1
        return self._cle[idx]

    def disp(self, tt):
        c = self.cl(tt)
        return None if c is None or self.horn_cle is None else c - self.horn_cle

    def disp_cle(self, cle):
        return None if cle is None or self.horn_cle is None else cle - self.horn_cle

    @property
    def end_disp(self):
        return self.disp_cle(self.end_cle)

    def fmt(self, disp_sec):
        return Clock.fmt(self, disp_sec)


# ───────────────────────── CLI：诊断 + 对账 ─────────────────────────
def _find_db(mid):
    hits = glob.glob(os.path.join(DBFULL, "*", "%s.db" % mid))
    if not hits:
        raise SystemExit("找不到 %s 的 .db" % mid)
    return hits[0]


def main():
    ap = argparse.ArgumentParser(description="timebase 自检：打印某场时钟诊断 + 与 stats.db 时长对账")
    ap.add_argument("match", nargs="+")
    args = ap.parse_args()
    for mid in args.match:
        db = _find_db(mid)
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        ck = Clock(con, mid)
        print("=" * 72)
        print("match %s  (%s)" % (mid, os.path.relpath(db, ROOT)))
        print("  号角 cle=%.2f tt=%.2f ｜ 结束 cle=%.2f (%s) → 显示时长 %s"
              % (ck.horn_cle, ck.horn_tt, ck.end_cle, ck.end_source, ck.fmt(ck.end_disp)))
        print("  Δ_file=%.2f ｜ 锚点 %d 段 ｜ 暂停合计 %.1fs（≥3s 的块 %d 个，块内 gap 合计 %.1fs）"
              % (ck.delta_file, ck.meta["anchors"], ck.meta["pause_sec_total"],
                 len(ck.meta["pause_blocks"]), ck.meta["pause_blocks_gap_sum"]))
        for s in ck.meta["pause_blocks"][:8]:
            print("     暂停块 tt[%d,%d] ≈ %.1fs" % tuple(s))
        # 出门期自洽检查：初始金 600 事件应落在 disp ≈ −90
        r = con.execute("SELECT t_cle FROM combat_log WHERE match_id=? AND type_category='gold' "
                        "AND value=600 ORDER BY t_cle LIMIT 1", (mid,)).fetchone()
        if r:
            print("  初始金600事件 disp=%.2f（应 ≈ %.1f = 出门）" % (ck.disp_cle(r["t_cle"]), GATE_OPEN_DISP))
        r = con.execute("SELECT MIN(game_time_sec) m FROM entity_snapshots WHERE match_id=? AND entity_type='hero'",
                        (mid,)).fetchone()
        if r and r["m"] is not None:
            print("  英雄首个采样 disp=%.2f（应 ≈ %.1f）" % (ck.disp(r["m"]), GATE_OPEN_DISP))
        # 独立对账
        if os.path.exists(STATS_DB):
            sc = sqlite3.connect(STATS_DB)
            q = sc.execute("SELECT duration_sec FROM matches WHERE match_id=?", (mid,)).fetchone()
            sc.close()
            if q and q[0]:
                print("  ★ 时长对账：本模块 %.0fs ｜ stats.db(OpenDota) %ds → 差 %+.1fs"
                      % (ck.end_disp, q[0], ck.end_disp - q[0]))
            else:
                print("  ★ 时长对账：stats.db 未覆盖本场 → 无独立源")
        con.close()


if __name__ == "__main__":
    main()
