#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q7_replay.py — Q7「全盘复现交互 UI（回放浏览器）」· 单场数据切片（第 4 层，只读）。

输入：dems/db_full/<league_id>/<match_id>.db（解析层产出，**只读、不改**）
输出：analysis/output_q7/q7_<match_id>.json（供 build_q7_html.py 内嵌成单文件 HTML）

────────────────────────────────────────────────────────────────────────
口径（权威出处：STRATEGY/Q7_REPLAY_UI.md §5 / STRATEGY/DEM_FORMAT.md §2 §5）
────────────────────────────────────────────────────────────────────────
1) **显示时钟 disp**：0:00 = 号角（combat_log `type_category='gamestate' AND value=5`
   那一条的 t_cle，即 `DOTA_GAMERULES_STATE_GAME_IN_PROGRESS`）。
   `disp = t_cle − horn_cle`。展示一律用这条（-1:30 出门 / 0:00 号角）。
2) **比赛结束** = 远古（`*_fort`）被摧毁那一刻的 t_cle（同 Q5B §8.15），
   **不是** combat_log 的 MAX(t_cle)（战后结算残留会多记 360~925s）。
3) **combat_log.t_cle 已是游戏时钟**（暂停冻结）→ 事件类（gold/xp/death/damage/modifier/
   ability/item）直接 `disp = t_cle − horn_cle`。
4) **entity_snapshots.game_time_sec = 回放钟**（= tick/30，与 combat_log 的 `t_tick` 同轴）
   → 位置/净值/守卫必须经 (t_tick → t_cle) 映射折算。
   ★ 本脚本用「**不暂停秒 = 场上实体在动**」这一物理证据重建映射：
     暂停时游戏钟冻结、且所有实体状态逐秒完全不变（实测位移恒为 0）；
     于是把每对 combat 锚点之间已知的 Δcle 按"活跃秒"配额分配，锚点误差 <1s。
     （q5_ward.py 的二分取右侧会把整段暂停压到同一秒 → 位置/放置时刻整体前移，本脚本不采用。）
5) **gold.value 有 int32 负数被当 uint32 落库的缺陷**（gold_reason=1 = 死亡扣钱，
   实测 40/40 行 = 2^32 + 负数）→ 统一 `v ≥ 2^31 ⇒ v −= 2^32` 还原。
6) KDA/正反补全部来自 combat_log：
   · 击杀：`type_category='death' AND is_target_hero=1`，killer=`attacker`，
     assist=`assist_players`（★ 该列表**含击杀者本人**，已剔除）。
   · 正补 = 击杀**敌方**线上兵（`a_team != t_team`）；反补 = 击杀**己方**线上兵。
"""

import argparse
import collections
import glob
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBFULL = os.path.join(ROOT, "dems", "db_full")
OUTDIR = os.path.join(ROOT, "analysis", "output_q7")
STATS_DB = os.path.join(ROOT, "stats.db")

WORLD_HALF = 8600.0          # 地图世界半径（与地图层标定一致）
CREEP_PREFIX = "npc_dota_creep_"


# ────────────────────────────── 工具 ──────────────────────────────
def i32(v):
    """把被当 uint32 落库的负数还原（gold 死亡扣钱等）。"""
    if v is None:
        return None
    v = int(v)
    return v - (1 << 32) if v >= (1 << 31) else v


def find_db(match_id):
    hits = glob.glob(os.path.join(DBFULL, "*", "%s.db" % match_id))
    if not hits:
        hits = glob.glob(os.path.join(ROOT, "dems", "*", "**", "%s.db" % match_id), recursive=True)
    if not hits:
        raise SystemExit("找不到 match %s 的 .db（dems/db_full/<league>/<match_id>.db）" % match_id)
    p = hits[0]
    league = os.path.basename(os.path.dirname(p))
    return p, league


def team_name(match_id, team_id):
    if team_id in (None, 0):
        return None
    if not os.path.exists(STATS_DB):
        return None
    try:
        con = sqlite3.connect(STATS_DB)
        r = con.execute("SELECT name FROM teams WHERE team_id=?", (team_id,)).fetchone()
        con.close()
        return r[0] if r else None
    except Exception:
        return None


def match_meta(match_id):
    """stats.db 的公开元数据（队名/胜负/时长）——缺失则返回空 dict（不硬造）。"""
    if not os.path.exists(STATS_DB):
        return {}
    try:
        con = sqlite3.connect(STATS_DB)
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT * FROM matches WHERE match_id=?", (match_id,)).fetchone()
        con.close()
        if not r:
            return {}
        d = dict(r)
        return {
            "radiant_team_id": d.get("radiant_team_id"),
            "dire_team_id": d.get("dire_team_id"),
            "radiant_team": team_name(match_id, d.get("radiant_team_id")),
            "dire_team": team_name(match_id, d.get("dire_team_id")),
            "radiant_win": d.get("radiant_win"),
            "stats_duration_sec": d.get("duration_sec"),
            "source": "stats.db",
        }
    except Exception:
        return {}


# ──────────────────────── 1. 时钟重建 ────────────────────────
def load_snapshots(con):
    """读英雄逐秒位置 + 逐玩家净值（两者都在回放钟轴上）。"""
    pos = collections.defaultdict(dict)      # tt -> {npc: (x, y, hp)}
    nw = collections.defaultdict(dict)       # tt -> {nwkey: networth}
    hpmax = {}                               # npc -> {tt: hp_max}
    for r in con.execute(
        "SELECT game_time_sec t, entity_id e, x, y, hp, extra FROM entity_snapshots "
        "WHERE entity_type='hero' ORDER BY game_time_sec"
    ):
        pos[int(r["t"])][r["e"]] = (float(r["x"] or 0.0), float(r["y"] or 0.0), int(r["hp"] or 0))
        try:
            hm = json.loads(r["extra"] or "{}").get("hp_max")
            if hm:
                hpmax.setdefault(r["e"], {})[int(r["t"])] = int(hm)
        except Exception:
            pass
    for r in con.execute(
        "SELECT game_time_sec t, entity_id e, hp, extra FROM entity_snapshots "
        "WHERE entity_type='networth' ORDER BY game_time_sec"
    ):
        v = None
        try:
            v = json.loads(r["extra"] or "{}").get("networth")
        except Exception:
            v = None
        nw[int(r["t"])][r["e"]] = int(v if v is not None else (r["hp"] or 0))
    return pos, nw, hpmax


def activity_map(pos, nw):
    """每秒是否「游戏在跑（未暂停）」：任一英雄位移>0.5 / hp 变化，或任一玩家净值变化。"""
    act = {}
    secs = sorted(set(pos) | set(nw))
    prev_p, prev_n = None, None
    for t in secs:
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


def build_clock(con, pos, nw):
    """重建 (t_tick → t_cle) 映射，返回 {int_sec: t_cle} + 元信息。

    做法：把 combat_log 里所有 (t_tick, t_cle) 当锚点；相邻锚点之间的 Δcle 是"真实游戏
    时间流逝"，按两锚点之间各秒的**活跃度**配额分配（活跃秒≈未暂停秒）。锚点端点精确，
    段内按物理证据摊——比二分/线性都更贴真实暂停结构。
    """
    anchors = []
    for r in con.execute(
        "SELECT t_tick, t_cle FROM combat_log WHERE t_tick IS NOT NULL AND t_cle IS NOT NULL ORDER BY t_tick, event_seq"
    ):
        tt, cle = float(r["t_tick"]), float(r["t_cle"])
        if not anchors or tt > anchors[-1][0] + 1e-9:
            anchors.append((tt, cle))
        else:
            anchors[-1] = (anchors[-1][0], max(anchors[-1][1], cle))
    if not anchors:
        raise SystemExit("combat_log 没有可用锚点，无法重建时钟")

    act = activity_map(pos, nw)
    cle_at = {}
    pause_total = 0.0
    active_total = 0
    seg_pauses = []
    for i in range(1, len(anchors)):
        a_tt, a_cle = anchors[i - 1]
        b_tt, b_cle = anchors[i]
        need = b_cle - a_cle
        if need < 0:
            need = 0.0
        lo, hi = int(a_tt) + 1, int(b_tt) + 1
        span = list(range(max(lo, 0), max(hi, 0) + 1))
        if not span:
            continue
        w = [act.get(s, 0) for s in span]
        tot = sum(w)
        if tot <= 0:
            # 段内没探到任何活动（例如全段静止的暂停）→ 用整段均分兜底，避免信息丢失
            w = [1] * len(span)
            tot = len(span)
        if len(span) - tot > 0:
            gap = len(span) - need
            if gap > 1.0:
                pause_total += gap
                seg_pauses.append([span[0], span[-1], round(gap, 1)])
        active_total += tot
        acc = 0.0
        for s, wi in zip(span, w):
            acc += wi
            cle_at[s] = a_cle + need * (acc / tot if tot else 1.0)

    # 锚点之前的区间按首个锚点的同一"文件偏移"外推（Δ_file 恒定，无暂停）
    first_tt, first_cle = anchors[0]
    delta = first_cle - first_tt
    return cle_at, {
        "anchors": len(anchors),
        "first_anchor": [first_tt, first_cle],
        "last_anchor": list(anchors[-1]),
        "delta_file": round(delta, 3),
        "pause_sec_total": round(pause_total, 1),
        "pause_segments": seg_pauses[:40],
        "active_sec": active_total,
        "clock_min": min(cle_at) if cle_at else None,
        "clock_max": max(cle_at) if cle_at else None,
    }


def cle_of(cle_at, tt, delta_file):
    """回放钟秒 → 游戏钟（t_cle）。整数秒查表；表外回退 Δ_file 外推。"""
    if tt is None:
        return None
    t = int(tt)
    if t in cle_at:
        return cle_at[t]
    if cle_at:
        ks = [k for k in (t, t - 1, t + 1, t - 2, t + 2) if k in cle_at]
        if ks:
            return cle_at[min(ks, key=lambda k: abs(k - t))]
    return tt + delta_file


# ──────────────────────── 2. 单场切片 ────────────────────────
def parse_match(db, match_id, league):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    # --- 玩家身份（10 名；player_slot 2=天辉 0-4 / 3=夜魇 128-132）---
    players = []
    for i, r in enumerate(con.execute("SELECT * FROM player_identity ORDER BY player_slot")):
        players.append({
            "i": i,
            "slot": int(r["player_slot"]),
            "team": int(r["team_id"]),
            "npc": r["hero_name"],
            "short": (r["hero_name"] or "").replace("npc_dota_hero_", ""),
            "name": r["player_name"],
            "steam": r["steam_id"],
        })
    npc2i = {p["npc"]: p["i"] for p in players}

    # --- 号角（0:00）---
    r = con.execute(
        "SELECT t_cle, t_tick FROM combat_log WHERE type_category='gamestate' AND value=5 LIMIT 1"
    ).fetchone()
    if not r:
        raise SystemExit("该库没有 gamestate value=5（号角），无法建立显示时钟 → 不硬造，退出")
    horn_cle, horn_tt = float(r["t_cle"]), float(r["t_tick"])

    # --- 比赛结束 = 远古被摧毁 ---
    end_cle, end_src = None, None
    r = con.execute(
        "SELECT t_cle FROM combat_log WHERE type_category='death' AND target LIKE '%_fort' ORDER BY t_cle LIMIT 1"
    ).fetchone()
    if r:
        end_cle, end_src = float(r["t_cle"]), "fort_destroyed"
    else:
        r = con.execute(
            "SELECT MAX(t_cle) m FROM combat_log WHERE type_category IN ('damage','death','xp','gold')"
        ).fetchone()
        if r and r["m"]:
            end_cle, end_src = float(r["m"]), "fallback_max_combat"
    end_disp = end_cle - horn_cle

    # --- 时钟重建 ---
    pos_tt, nw_tt, hpmax = load_snapshots(con)
    cle_at, clock_meta = build_clock(con, pos_tt, nw_tt)
    delta_file = clock_meta["delta_file"]

    def disp_of_tt(tt):
        return cle_of(cle_at, tt, delta_file) - horn_cle

    # --- 时间轴范围 ---
    hero_first_disp = None
    if pos_tt:
        hero_first_disp = min(disp_of_tt(t) for t in pos_tt)
    t0 = int(min(-90, (hero_first_disp if hero_first_disp is not None else -90) - 1))
    t1 = int(round(end_disp))
    if t1 <= t0:
        raise SystemExit("结束早于开始（t0=%s t1=%s）→ 数据异常，退出" % (t0, t1))
    D = t1 - t0 + 1

    def idx(disp):
        k = int(round(disp)) - t0
        return k if 0 <= k < D else None

    # --- 英雄位置 / hp（按显示秒归档；同秒多条取最后一条）---
    pos = {}
    hpm = {}
    for p in players:
        pos[p["npc"]] = {"x": [None] * D, "y": [None] * D, "hp": [None] * D}
        hpm[p["npc"]] = [None] * D
    for tt in sorted(pos_tt):
        k = idx(disp_of_tt(tt))
        if k is None:
            continue
        for npc, (x, y, hp) in pos_tt[tt].items():
            if npc not in pos:
                continue
            pos[npc]["x"][k] = round(x, 1)
            pos[npc]["y"][k] = round(y, 1)
            pos[npc]["hp"][k] = hp
        for npc, hm in hpmax.get(tt, {}).items():
            if npc in hpm:
                hpm[npc][k] = hm

    # --- m_iNetWorth（旁注校验源）---
    key2npc = {}
    for p in players:
        key2npc["nw:%s:%d" % ("radiant" if p["team"] == 2 else "dire",
                              p["slot"] if p["team"] == 2 else p["slot"] - 128)] = p["npc"]
    nw = {p["npc"]: [None] * D for p in players}
    for tt in sorted(nw_tt):
        k = idx(disp_of_tt(tt))
        if k is None:
            continue
        for key, v in nw_tt[tt].items():
            npc = key2npc.get(key)
            if npc:
                nw[npc][k] = v

    # --- combat_log 事件类：disp 直接由 t_cle 得到 ---
    # 2a. 金币累加（含 int32 还原）+ 经验累加
    cg = {p["npc"]: [0] * D for p in players}
    cx = {p["npc"]: [0] * D for p in players}
    gold_rows = collections.defaultdict(list)   # k -> [(npc, val)]
    xp_rows = collections.defaultdict(list)
    for r in con.execute(
        "SELECT t_cle, target, value FROM combat_log WHERE type_category='gold' AND target LIKE 'npc_dota_hero%'"
    ):
        k = idx(float(r["t_cle"]) - horn_cle)
        if k is not None:
            gold_rows[k].append((r["target"], i32(r["value"]) or 0))
    for r in con.execute(
        "SELECT t_cle, target, value FROM combat_log WHERE type_category='xp' AND target LIKE 'npc_dota_hero%'"
    ):
        k = idx(float(r["t_cle"]) - horn_cle)
        if k is not None:
            xp_rows[k].append((r["target"], i32(r["value"]) or 0))

    run_g = {p["npc"]: 0 for p in players}
    run_x = {p["npc"]: 0 for p in players}
    for k in range(D):
        for npc, v in gold_rows.get(k, ()):  # noqa: E501
            if npc in run_g:
                run_g[npc] += v
        for npc, v in xp_rows.get(k, ()):
            if npc in run_x:
                run_x[npc] += v
        for p in players:
            npc = p["npc"]
            cg[npc][k] = run_g[npc]
            cx[npc][k] = run_x[npc]

    # 2b. KDA + 正反补（combat_log death 条目）
    kills = []          # [disp, killer_i, victim_i, [assist_i...]]
    kda = {p["npc"]: {"k": 0, "d": 0, "a": 0, "lh": 0, "dn": 0} for p in players}
    hdr_of_npc = {p["npc"]: p["i"] for p in players}
    for r in con.execute(
        "SELECT t_cle, attacker, target, assist_players FROM combat_log "
        "WHERE type_category='death' AND is_target_hero=1 ORDER BY t_cle"
    ):
        ki, vi = hdr_of_npc.get(r["attacker"]), hdr_of_npc.get(r["target"])
        if vi is None:
            continue
        asst = []
        try:
            raw = json.loads(r["assist_players"]) if r["assist_players"] else []
            # ★ assist_players 里的值是「头部玩家索引（0-9）」，且**含击杀者本人** → 剔除
            asst = sorted({int(x) for x in raw if int(x) != ki and 0 <= int(x) < len(players)})
        except Exception:
            asst = []
        kda[players[vi]["npc"]]["d"] += 1
        if ki is not None:
            kda[players[ki]["npc"]]["k"] += 1
        for a in asst:
            kda[players[a]["npc"]]["a"] += 1
        d = float(r["t_cle"]) - horn_cle
        if t0 <= d <= t1:
            kills.append([int(round(d)), ki if ki is not None else -1, vi, asst])

    for r in con.execute(
        "SELECT attacker, target, a_team, t_team FROM combat_log "
        "WHERE type_category='death' AND is_attacker_hero=1 AND target LIKE ?", (CREEP_PREFIX + "%",)
    ):
        npc = r["attacker"]
        if npc not in kda:
            continue
        at, tt = r["a_team"], r["t_team"]
        if at is None or tt is None:
            # 退化判据：名字前缀 goodguys=天辉(2) / badguys=夜魇(3)
            tt = 2 if "goodguys" in (r["target"] or "") else 3
            at = 2 if players[hdr_of_npc[npc]]["team"] == 2 else 3
        if tt != at:
            kda[npc]["lh"] += 1
        else:
            kda[npc]["dn"] += 1

    # 2c. 关键事件标记（供时间轴）：肉山/建筑
    events = []
    for r in con.execute(
        "SELECT t_cle, target FROM combat_log WHERE type_category='death' "
        "AND (target LIKE '%_fort' OR target LIKE '%_tower%' OR target LIKE '%_rax_%' OR target LIKE '%roshan%')"
    ):
        d = float(r["t_cle"]) - horn_cle
        if t0 <= d <= t1:
            events.append([int(round(d)), r["target"]])
    events.sort()

    # 2d. 建筑（塔/兵营/基地）：game_events 的 building_spawn / building_destroyed
    #     ★ 该表的时间轴是**回放钟**（DEM_FORMAT §5）→ 必须经 disp_of_tt 折算。
    spawned = {}
    destroyed = {}
    for r in con.execute(
        "SELECT game_time_sec, event_type, target_id, x, y, properties FROM game_events "
        "WHERE event_type IN ('building_spawn','building_destroyed')"
    ):
        try:
            props = json.loads(r["properties"] or "{}")
        except Exception:
            props = {}
        key = r["target_id"]
        if r["event_type"] == "building_spawn":
            spawned[key] = (r["x"], r["y"], props.get("team"), props.get("kind"))
        else:
            destroyed[key] = (float(r["game_time_sec"]), r["x"], r["y"], props.get("team"), props.get("kind"))
    buildings = []
    for key, (x, y, team, kind) in sorted(spawned.items()):
        dsc = None
        if key in destroyed:
            dsc = int(round(disp_of_tt(destroyed[key][0])))
        buildings.append([round(x, 1), round(y, 1), team, kind or "building", dsc])
    for key, (tt, x, y, team, kind) in sorted(destroyed.items()):
        if key not in spawned:      # 只有摧毁记录的建筑（spawn 未采到）也保留
            buildings.append([round(x, 1), round(y, 1), team, kind or "building",
                              int(round(disp_of_tt(tt)))])

    con.close()

    # --- 队伍合计/差值（两套源）---
    def team_sum(series, team):
        out = [0] * D
        for p in players:
            if p["team"] != team:
                continue
            a = series[p["npc"]]
            for k in range(D):
                out[k] += (a[k] or 0)
        return out

    def diff(series):
        r = team_sum(series, 2)
        d = team_sum(series, 3)
        return [r[k] - d[k] for k in range(D)]

    meta = match_meta(match_id)
    meta.update({
        "db": os.path.relpath(db, ROOT).replace("\\", "/"),
        "league_id": int(league) if str(league).isdigit() else league,
        "horn_cle": round(horn_cle, 3),
        "horn_tt": round(horn_tt, 3),
        "end_cle": round(end_cle, 3),
        "end_disp": round(end_disp, 2),
        "end_source": end_src,
        "clock": clock_meta,
        "gold_i32_fixed": True,
        "assist_excludes_killer": True,
    })

    return {
        "match_id": int(match_id),
        "t0": t0,
        "t1": t1,
        "D": D,
        "players": players,
        "pos": pos,
        "hpm": hpm,
        "nw": nw,
        "cg": cg,
        "cx": cx,
        "diff": {"nw": diff(nw), "cg": diff(cg), "cx": diff(cx)},
        "kills": kills,
        "events": events,
        "buildings": buildings,
        "kda": kda,
        "meta": meta,
    }


# ──────────────────────── 3. 自检 + 落盘 ────────────────────────
def selfcheck(dat):
    D, t0, t1 = dat["D"], dat["t0"], dat["t1"]
    m = dat["meta"]
    msgs = []
    msgs.append("match=%s league=%s  disp=[%d, %d] D=%d" % (dat["match_id"], m["league_id"], t0, t1, D))
    msgs.append("horn_cle=%.2f end_cle=%.2f (end_source=%s) → 时长 %s"
                % (m["horn_cle"], m["end_cle"], m["end_source"], fmt(t1)))
    c = m["clock"]
    msgs.append("锚点 %d 段；Δ_file=%.2f；测得暂停合计 %.1fs（%d 段）"
                % (c["anchors"], c["delta_file"], c["pause_sec_total"], len(c["pause_segments"])))
    for seg in c["pause_segments"][:6]:
        msgs.append("   暂停段 tt[%d,%d] ≈ %.1fs" % (seg[0], seg[1], seg[2]))
    # 位置覆盖
    cov = []
    for p in dat["players"]:
        a = dat["pos"][p["npc"]]["x"]
        n = sum(1 for v in a if v is not None)
        cov.append((p["short"], n))
    msgs.append("位置覆盖(秒)：" + "，".join("%s=%d" % (k, v) for k, v in cov))
    first = min((i for i, v in enumerate(dat["pos"][dat["players"][0]["npc"]]["x"]) if v is not None),
                default=None)
    msgs.append("首个英雄采样 disp=%s（应 ≈ -90 = 出门）" % (t0 + first if first is not None else None))
    # 经济对账（末秒）
    dif_cg = dat["diff"]["cg"][-1]
    dif_nw = dat["diff"]["nw"][-1]
    dif_cx = dat["diff"]["cx"][-1]
    msgs.append("末秒 经济差：combat-log 累加=%d ｜ m_iNetWorth=%d ｜ 经验差(combat-log)=%d"
                % (dif_cg, dif_nw, dif_cx))
    tot_cg = sum(dat["cg"][p["npc"]][-1] for p in dat["players"])
    tot_nw = sum((dat["nw"][p["npc"]][-1] or 0) for p in dat["players"])
    msgs.append("全场总值：combat-log 累计金币=%d vs m_iNetWorth 合计=%d（比值 %.3f）"
                % (tot_cg, tot_nw, (tot_cg / tot_nw) if tot_nw else 0))
    msgs.append("KDA/CS：" + "，".join(
        "%s %d/%d/%d LH%d DN%d" % (p["short"], dat["kda"][p["npc"]]["k"], dat["kda"][p["npc"]]["d"],
                                   dat["kda"][p["npc"]]["a"], dat["kda"][p["npc"]]["lh"],
                                   dat["kda"][p["npc"]]["dn"]) for p in dat["players"]))
    msgs.append("击杀事件 %d 条；建筑/肉山标记 %d 条" % (len(dat["kills"]), len(dat["events"])))
    bld = dat.get("buildings", [])
    msgs.append("建筑 %d 座（已摧毁 %d）" % (len(bld), sum(1 for b in bld if b[4] is not None)))
    # ★ 独立对账：stats.db（OpenDota）的 duration_sec vs 本脚本"远古被摧毁"推得的时长
    dur = m.get("stats_duration_sec")
    if dur:
        msgs.append("★ 时长对账：本脚本远古被摧毁 → %d s ｜ stats.db(OpenDota) duration_sec=%d s → 差 %+.1f s（%s）"
                    % (dat["t1"], dur, dat["t1"] - dur, "一致" if abs(dat["t1"] - dur) <= 3 else "⚠偏差大，需查"))
    else:
        msgs.append("★ 时长对账：stats.db 未覆盖本场（league %s）→ 无独立源可对，仅报数" % m.get("league_id"))
    return msgs


def fmt(sec):
    sec = int(sec)
    s = abs(sec)
    return "%s%d:%02d" % ("-" if sec < 0 else "", s // 60, s % 60)


def main():
    ap = argparse.ArgumentParser(description="Q7 单场回放数据切片")
    ap.add_argument("match", help="match_id（如 8955197224）或 .db 路径")
    ap.add_argument("--out", default=OUTDIR)
    ap.add_argument("--no-write", action="store_true", help="只跑自检，不落盘")
    args = ap.parse_args()

    if os.path.exists(args.match):
        db = args.match
        mid = os.path.basename(db).replace(".db", "")
        league = os.path.basename(os.path.dirname(db))
    else:
        mid = args.match
        db, league = find_db(mid)

    print("DB :", os.path.relpath(db, ROOT))
    dat = parse_match(db, mid, league)
    for line in selfcheck(dat):
        print("  " + line)

    if args.no_write:
        return
    os.makedirs(args.out, exist_ok=True)
    p = os.path.join(args.out, "q7_%s.json" % mid)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(dat, f, ensure_ascii=False, separators=(",", ":"))
    print("wrote %s  size=%.2f MB" % (os.path.relpath(p, ROOT), os.path.getsize(p) / 1e6))


if __name__ == "__main__":
    main()
