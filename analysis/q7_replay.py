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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timebase as tb   # noqa: E402  数据层共享时基（号角/结束/暂停感知折算 + gold int32 还原）

DBFULL = os.path.join(ROOT, "dems", "db_full")
DBOLD = os.path.join(ROOT, "dems", "db")          # Q5 版散装 extractor（技能/道具 CD 在这里）
OUTDIR = os.path.join(ROOT, "analysis", "output_q7")
STATS_DB = os.path.join(ROOT, "stats.db")
ICON_DIR = os.path.join(ROOT, "opendota_analysis", "assets", "ability_icons")
SKILLICON_OUT = os.path.join(ROOT, "analysis", "output_q7", "icons")

WORLD_HALF = 8600.0          # 地图世界半径（与地图层标定一致）
CREEP_PREFIX = "npc_dota_creep_"

# gold.value 的 int32 下溢还原：统一走 timebase（单一真相源）
i32 = tb.gold_i32


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


def find_old_db(match_id):
    """Q5 版库（dems/db/）——技能/道具 CD 数据只在这里。缺失返回 None（不硬造）。"""
    hits = glob.glob(os.path.join(DBOLD, "*", "%s.db" % match_id))
    return hits[0] if hits else None


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


# ──────────────────────── 2. 单场切片 ────────────────────────
def parse_match(db, match_id, league, with_detail=True):
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

    # --- 时钟重建（暂停感知，统一走数据层共享实现 timebase.Clock）---
    pos_tt, nw_tt, hpmax = load_snapshots(con)
    CLK = tb.Clock(con, match_id)
    clock_meta = dict(CLK.meta)
    clock_meta["pause_segments"] = clock_meta.get("pause_blocks", [])   # 兼容旧字段名（viewer 用）
    delta_file = CLK.delta_file

    def disp_of_tt(tt):
        return CLK.disp(tt)

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
        for npc, m in hpmax.items():          # hpmax: npc -> {tt: hp_max}
            if npc in hpm and tt in m:
                hpm[npc][k] = m[tt]

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

    # 2e. 该英雄 ±10s 的 combat log 明细（4 类）+ 关键道具 TP 使用时刻
    #     载荷优化：把「同英雄 · 同类别 · 同 kind · 同名称 · 同数值 · 同对手 · 时间相邻(≤1.2s)」
    #     的连续条目**折叠成一个区间**（保留条数 n 与首末时刻）——逐条渲染 12.9 万行既没人看、
    #     单文件也装不下；折叠后显示内容等价（区间 + ×N + 首末时刻）。
    if with_detail:
        detail, names = build_combat_detail(con, players, horn_cle, t0, t1)
    else:
        detail, names = {}, []
    tpu = {p["npc"]: [] for p in players}
    for r in con.execute(
        "SELECT t_cle, attacker FROM combat_log WHERE type_category='item' AND inflictor='item_tpscroll'"
    ):
        npc = r["attacker"]
        if npc in tpu:
            k = idx(float(r["t_cle"]) - horn_cle)
            if k is not None:
                tpu[npc].append(int(round(float(r["t_cle"]) - horn_cle)))

    # 2f. 眼位（守卫）+ 烟雾：让"全盘复现"包含视野层
    #   眼位口径**完全复用 Q5B**（analysis/q5_ward.py::parse_match —— 口径单一真相源）：
    #     判型=实体类名；放置=combat item use（±35s PVS 容差）；到期=attacker==target；
    #     销毁与眼"一一对应"；右删失=比赛结束时仍存活。返回的 place/destroy 是**游戏钟(cle)**。
    wards = load_wards(con, match_id, horn_cle, t0, t1)
    smoke, smoked = load_smoke(con, players, horn_cle, t0, t1, pos, t0)
    tl = build_timeline(con, match_id, players, kills, horn_cle, t0, t1)

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
    old_db = find_old_db(match_id)
    cd = load_cd(old_db, match_id, CLK, players, t0, t1)
    meta.update({
        "db": os.path.relpath(db, ROOT).replace("\\", "/"),
        "db_old": (os.path.relpath(old_db, ROOT).replace("\\", "/") if old_db else None),
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
        "detail": detail,
        "detail_names": names,
        "cd": cd,
        "tp": tpu,
        "wards": wards,
        "tl": tl,
        "smoke": smoke,
        "smoked": smoked,
        "kda": kda,
        "meta": meta,
    }


# ──────────────────────── 2b. combat log 明细（±10s 面板用） ────────────────────────
#   4 类：给出的 modifier / 收到的 modifier / 造成伤害 / 收到伤害（Q7_REPLAY_UI.md §4.2）
#   折叠规则见 parse_match 里的注释；`n` = 该区间内的原始条目数（不隐藏任何条目，只是合并显示）
MOD_KIND = {"DotaCombatlogModifierAdd": 0, "DotaCombatlogModifierRemove": 1,
            "DotaCombatlogModifierStackEvent": 2}
DMG_KIND = {"DotaCombatlogDamage": 0, "DotaCombatlogCriticalDamage": 1,
            "DotaCombatlogManaDamage": 2, "DotaCombatlogSpellAbsorb": 3}
RUN_GAP = 1.2      # 相邻条目间隔 ≤ 该秒数 → 视为同一"连续区间"


def build_combat_detail(con, players, horn_cle, t0, t1):
    """返回 (detail, names)：
    detail[npc] = [[dt, cat, kind, name_idx, val, other_idx], ...]
      dt  = 该条与**上一条**的显示秒差（≥0）—— UI 累加成绝对时刻；省掉 4 位绝对秒，载荷减半
      cat = 0 给出的 modifier ｜ 1 收到的 modifier ｜ 2 造成伤害 ｜ 3 收到伤害
      kind= modifier: 0 Add / 1 Remove / 2 Stack ；damage: 0 Damage / 1 Critical / 2 ManaDamage / 3 SpellAbsorb
      other_idx = 对手/来源名索引（-1 = 无）
    ★ 逐条保留（不预折叠）：折叠交给 UI 的"折叠连续同项"开关做，保证 ±10s 窗口内的计数是精确的。
    names = 名称字典（modifier inflictor / damage inflictor / damage_source / 单位名）
    """
    hdr_of = {p["npc"]: p["i"] for p in players}
    hero_set = set(hdr_of)
    names, nidx = [], {}

    def nid(s):
        s = s or ""
        if s not in nidx:
            nidx[s] = len(names)
            names.append(s)
        return nidx[s]

    raw = collections.defaultdict(list)
    for r in con.execute(
        "SELECT t_cle, type_category c, type, attacker, target, inflictor, damage_source, "
        "COALESCE(value,0) v FROM combat_log "
        "WHERE type_category IN ('modifier','damage') ORDER BY t_cle, event_seq"
    ):
        a, tg, c = r["attacker"], r["target"], r["c"]
        if a in hero_set and c == "modifier":
            cat, hero, other, nm = 0, a, tg, r["inflictor"]
        elif tg in hero_set and c == "modifier":
            cat, hero, other, nm = 1, tg, a, r["inflictor"]
        elif a in hero_set:
            cat, hero, other = 2, a, tg
            nm = r["inflictor"] or r["damage_source"] or ""
            if not nm or nm == a:
                nm = "普通攻击"          # inflictor 缺省且来源=自己 → 右键平A
        elif tg in hero_set:
            cat, hero, other = 3, tg, a
            nm = r["inflictor"] or r["damage_source"] or ""
            if not nm or nm == a:
                nm = "普通攻击"
        else:
            continue
        kind = (MOD_KIND if c == "modifier" else DMG_KIND).get(r["type"], 0)
        t = float(r["t_cle"]) - horn_cle
        if t < t0 - 1 or t > t1 + 1:
            continue
        raw[hero].append((int(round(t)), cat, kind, nid(nm), int(r["v"] or 0),
                          nid(other) if other else -1))

    detail = {}
    for npc, rows in raw.items():
        rows.sort(key=lambda x: x[0])
        out, prev = [], None
        for t, cat, kind, nm, val, oid in rows:
            dt = t - prev if prev is not None else t
            code = cat * 4 + kind          # 合并 cat/kind 省一个字段
            # 伤害类带数值；modifier 的 value 恒 0 → 省掉该字段（变长数组，JS 端 r[4]||0）
            out.append([dt, code, nm, oid] if cat < 2 else [dt, code, nm, oid, val])
            prev = t
        detail[npc] = out
    # 名称字典可读化：npc_dota_hero_x → x（其余 npc_dota_ 前缀去掉）
    names = [n.replace("npc_dota_hero_", "").replace("npc_dota_", "") for n in names]
    for p in players:
        detail.setdefault(p["npc"], [])
    return detail, names


# ──────────────────────── 2c. 技能 / 道具 CD（读 dems/db，回放钟 → 显示钟） ────────────────────────
#   数据源：`dems/db/<league>/<match>.db` 的 `game_events`（**回放钟**，见 DEM_FORMAT §5）。
#   为什么不去 db_full 拿：COMBAT_LOG_REWRITE §5.5 删掉了散装 extractor，而 CD 是**实体派生**
#   （读实体 `m_fCooldown`）不是 combat 条目 → db_full 里没有。
#   ★ 关键利好：`properties.remaining` = **实际剩余冷却秒**（含等级/天赋/减CD），
#     所以"技能 CD"**不需要任何常量表**（实测 BKB 70.5~95.0s / 刷新球 135~180s）。
CD_PREFIX = "CDOTA_Ability_"
ITEM_PREFIX = "ITEM:"
KEY_ITEMS = ["ITEM:Black_King_Bar", "ITEM:RefresherOrb", "ITEM:TownPortalScroll"]


def camel_to_snake(s):
    import re
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", s)
    return s.lower()


def pretty_ability(key, short):
    """CDOTA_Ability_DoomBringer_ScorchedEarth → 'Scorched Earth'；
    中立生物技能（Devour 吃来的，如 CDOTA_Ability_BlackDragon_Fireball）保留其来源前缀。"""
    import re
    tail = key[len(CD_PREFIX):] if key.startswith(CD_PREFIX) else key
    hero_camel = "".join(w.capitalize() for w in short.split("_"))
    if tail.startswith(hero_camel + "_"):
        tail = tail[len(hero_camel) + 1:]
    tail = tail.replace("_", " ")
    tail = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", tail)
    return re.sub(r"\s+", " ", tail).strip() or tail


def pretty_item(key):
    import re
    tail = key[len(ITEM_PREFIX):] if key.startswith(ITEM_PREFIX) else key
    tail = tail.replace("_", " ")
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", tail).strip()


def ability_class(key):
    """天赋 / 空槽 → UI 默认不显示（可在页面里打开"显示天赋/空槽"）。"""
    k = key.split("_")[-1] if "_" in key else key
    if key.startswith(CD_PREFIX) and ("Special_Bonus" in key or "_Bonus" in key or "Attribute" in key):
        return "talent"
    if "_Empty" in key or k in ("Empty1", "Empty2", "Empty3", "Empty4", "Empty5", "Empty6"):
        return "empty"
    return "ability"


def load_cd(old_db, mid, clk, players, t0, t1):
    """技能/道具冷却区间（显示钟）。找不到旧库/无数据 → 返回 None（**不硬造**）。"""
    if not old_db or not os.path.exists(old_db):
        return None
    con = sqlite3.connect(old_db)
    con.row_factory = sqlite3.Row
    short_of = {p["npc"]: p["short"] for p in players}
    starts = collections.defaultdict(list)     # (npc,key) -> [disp,...]
    ends = collections.defaultdict(list)
    known = {}
    learns = {}
    kinds = {}
    for r in con.execute(
        "SELECT game_time_sec t, event_type et, actor_id a, target_id k, properties p FROM game_events "
        "WHERE event_type IN ('ability_cd_start','ability_cd_end','item_cd_start','item_cd_end',"
        "'ability_known','ability_learn','item_known') ORDER BY game_time_sec"
    ):
        a, k = r["a"], r["k"]
        if a not in short_of or not k:
            continue
        try:
            props = json.loads(r["p"] or "{}")
        except Exception:
            props = {}
        rem = props.get("remaining")
        d = clk.disp(int(r["t"]))          # 回放钟 → 显示钟
        et = r["et"]
        if et in ("ability_cd_start", "item_cd_start"):
            starts[(a, k)].append((d, float(rem or 0.0)))
            kinds[k] = "item" if k.startswith(ITEM_PREFIX) else "ability"
        elif et in ("ability_cd_end", "item_cd_end"):
            ends[(a, k)].append(d)
        elif et == "ability_known":
            known.setdefault((a, k), d)
        elif et == "item_known":
            known.setdefault((a, k), d)
        elif et == "ability_learn":
            learns.setdefault((a, k), d)
    con.close()

    # starts / ends 一一配对（每个 end 配"最近一个尚未闭合的 start"）
    seq = {}
    for k in set(list(starts) + list(ends)):
        ss = sorted(starts.get(k, []))
        ee = sorted(ends.get(k, []))
        ivs = []
        for sd, rem in ss:
            ivs.append({"s": sd, "e": sd + rem, "closed": False})
        for ed in ee:
            best, bd = None, 1e9
            for it in ivs:
                if it["closed"] or it["s"] > ed:
                    continue
                dd = abs(it["e"] - ed)
                if dd < bd and dd <= 8.0:
                    bd, best = dd, it
            if best is not None:
                best["e"] = ed
                best["closed"] = True
        seq[k] = [[int(round(i["s"])), max(int(round(i["e"])), int(round(i["s"])) + 1)]
                  for i in ivs if i["e"] > i["s"] + 0.05]

    keys = sorted({k for (a, k) in list(starts) + list(ends) + list(known) + list(learns)})
    key_info = {}
    for k in keys:
        short = ""
        for (a, kk) in list(known) + list(learns) + list(starts):
            if kk == k:
                short = short_of[a]
                break
        nm = pretty_item(k) if k.startswith(ITEM_PREFIX) else pretty_ability(k, short)
        icon = None
        if k.startswith(ITEM_PREFIX):
            for cand in (camel_to_snake(k[len(ITEM_PREFIX):]), k[len(ITEM_PREFIX):].lower()):
                if os.path.exists(os.path.join(ICON_DIR, "item_%s.png" % cand)):
                    icon = "item_%s" % cand
                    break
        else:
            cand = camel_to_snake(k[len(CD_PREFIX):])
            if os.path.exists(os.path.join(ICON_DIR, "%s.png" % cand)):
                icon = cand
        key_info[k] = {"name": nm, "kind": "item" if k.startswith(ITEM_PREFIX) else "ability",
                       "icon": icon, "cls": "item" if k.startswith(ITEM_PREFIX) else ability_class(k)}

    ab, it = {}, {}
    for p in players:
        npc = p["npc"]
        A, I = [], []
        for k in keys:
            if (npc, k) not in starts and (npc, k) not in known and (npc, k) not in learns:
                continue
            entry = [k, known.get((npc, k)), learns.get((npc, k)), seq.get((npc, k), [])]
            if k.startswith(ITEM_PREFIX):
                I.append(entry)
            else:
                A.append(entry)
        ab[npc] = A
        it[npc] = I
    return {"keys": key_info, "ab": ab, "it": it,
            "track": [k for k in KEY_ITEMS if k in key_info],
            "src": os.path.relpath(old_db, ROOT).replace("\\", "/"),
            "time_axis": "replay→display（经 timebase.Clock 折算）"}


# ──────────────────────── 2d. 眼位（守卫） / 烟雾 ────────────────────────
WARD_REASONS = ["expired", "dewarded", "censored", "post_game", ""]


def load_wards(con, mid, horn_cle, t0, t1):
    """守卫（真/假眼）存活区间（显示钟）。**口径完全复用 Q5B**（q5_ward.parse_match）。

    返回 [[x, y, team, kind, place_disp, destroy_disp, reason_idx, censored], ...]
      kind: 0=observer(假眼,寿命360s) 1=sentry(真眼,寿命420s)
      destroy_disp 可能因"比赛结束"被截断（censored=1）—— 与 Q5B 同口径。
    拿不到 → 返回 []（不硬造）。
    """
    try:
        import importlib
        q5 = importlib.import_module("q5_ward")
        obs, sen, _ = q5.parse_match(con, mid)
    except Exception as e:
        sys.stderr.write("  ⚠ 眼位解析失败(%s)：%s → 本场不提供眼位层\n" % (mid, e))
        return []
    out = []
    for recs, kind in ((obs, 0), (sen, 1)):
        for r in recs:
            p, d = r.get("place"), r.get("destroy")
            if p is None:
                continue
            pd = p - horn_cle
            dd = (d - horn_cle) if d is not None else None
            if dd is None or dd < t0 or pd > t1:
                continue
            rs = r.get("reason") or ""
            out.append([round(r["x"], 1), round(r["y"], 1), int(r["team"]), kind,
                        int(round(pd)), min(int(round(dd)), t1),
                        WARD_REASONS.index(rs) if rs in WARD_REASONS else len(WARD_REASONS) - 1,
                        1 if r.get("censored") else 0])
    out.sort(key=lambda w: w[4])
    return out


def load_smoke(con, players, horn_cle, t0, t1, pos, t0i):
    """烟雾：① 使用时刻（combat_log `item_smoke_of_deceit` 的 item use，取使用者当时位置）
             ② 各英雄"处于烟雾中"的区间（`modifier_smoke_of_deceit` 的 Add/Remove 成对）。
    返回 (smoke, smoked)：smoke=[[disp, hero_idx, x, y], ...]；smoked={npc: [[s,e],...]}
    """
    hdr = {p["npc"]: p["i"] for p in players}

    def posat(npc, disp):
        P = pos.get(npc)
        if not P:
            return (0.0, 0.0)
        k = int(round(disp)) - t0i
        for i in range(max(0, k), max(0, k) - 15, -1):
            if i < len(P["x"]) and P["x"][i] is not None:
                return (P["x"][i], P["y"][i])
        return (0.0, 0.0)

    smoke = []
    for r in con.execute("SELECT t_cle, attacker FROM combat_log WHERE type_category='item' "
                         "AND inflictor='item_smoke_of_deceit' ORDER BY t_cle"):
        npc = r["attacker"]
        if npc not in hdr:
            continue
        d = float(r["t_cle"]) - horn_cle
        if d < t0 or d > t1:
            continue
        x, y = posat(npc, d)
        smoke.append([int(round(d)), hdr[npc], round(x, 1), round(y, 1)])
    # 烟雾 buff 区间（Add/Remove 成对，按英雄）
    openv = {}
    smoked = {p["npc"]: [] for p in players}
    for r in con.execute("SELECT t_cle, type, target FROM combat_log WHERE type_category='modifier' "
                         "AND inflictor='modifier_smoke_of_deceit' ORDER BY t_cle"):
        npc = r["target"]
        if npc not in smoked:
            continue
        d = int(round(float(r["t_cle"]) - horn_cle))
        if r["type"] == "DotaCombatlogModifierAdd":
            openv.setdefault(npc, []).append(d)
        else:
            if openv.get(npc):
                s = openv[npc].pop()
                if d > s:
                    smoked[npc].append([s, d])
    for npc, st in openv.items():
        for s in st:
            smoked[npc].append([s, min(t1, s + 45)])       # 烟雾最长 45s（未见 Remove）
    for npc in smoked:
        smoked[npc].sort()
    return smoke, smoked


# ──────────────────────── 2e2. 时间轴重大事件 ────────────────────────
LANE_CN = {"top": "上路", "mid": "中路", "bot": "下路"}
TL_KILL, TL_TOWER, TL_RAX, TL_FORT, TL_ROSHAN = 0, 1, 2, 3, 4


def bld_label(tgt):
    """npc_dota_badguys_tower1_top → '夜魇 上路 1塔'（用于时间轴标注）。"""
    import re
    t = (tgt or "").replace("npc_dota_", "")
    side = ("天辉" if t.startswith("goodguys") else
            ("夜魇" if t.startswith("badguys") else "中立"))
    rest = t.split("_", 1)[1] if "_" in t else t
    lane = ""
    for k, v in LANE_CN.items():
        if rest.endswith("_" + k) or ("_" + k + "_") in rest:
            lane = v
            break
    if "fort" in rest:
        return side + " 基地"
    if "rax" in rest or "barracks" in rest:
        kind = ("近战兵营" if "melee" in rest else
                ("远程兵营" if "range" in rest else "兵营"))
        return (side + " " + lane + " " + kind).replace("  ", " ")
    if "tower" in rest:
        m = re.search(r"tower(\d)", rest)
        return (side + " " + lane + " " + (m.group(1) if m else "?") + "塔").replace("  ", " ")
    if "fillers" in rest:
        return side + " 基地填充塔"
    if "watch" in rest:
        return side + " 瞭望塔"
    return side + " " + rest.replace("_", " ")


def build_timeline(con, match_id, players, kills, horn_cle, t0, t1):
    """时间轴重大事件：[disp, side, kind, text]；side=2 画上方（对天辉有利）、3 画下方。"""
    short = {p["npc"]: p["short"] for p in players}
    team_of = {p["i"]: p["team"] for p in players}
    out = []
    for k in kills:
        disp, ki, vi, asst = k
        if ki < 0 or ki not in team_of or vi not in team_of:
            continue
        out.append([int(disp), int(team_of[ki]), TL_KILL,
                    short[players[vi]["npc"]] + " 被 " + short[players[ki]["npc"]] + " 击杀"])
    for r in con.execute(
        "SELECT t_cle, target, t_team FROM combat_log WHERE match_id=? AND type_category='death' "
        "AND is_target_building=1 ORDER BY t_cle", (match_id,)):
        d = int(round(float(r["t_cle"]) - horn_cle))
        if d < t0 or d > t1:
            continue
        t = r["target"] or ""
        if "fillers" in t:            # 基地旁的填充塔不计入"重大事件"
            continue
        own = int(r["t_team"]) if r["t_team"] in (2, 3) else (2 if "goodguys" in t else 3)
        kind = TL_FORT if "fort" in t else (TL_RAX if ("rax" in t or "barracks" in t) else TL_TOWER)
        out.append([d, 3 if own == 2 else 2, kind, bld_label(t)])
    for r in con.execute(
        "SELECT t_cle, attacker, a_team FROM combat_log WHERE match_id=? AND type_category='death' "
        "AND target='npc_dota_roshan' ORDER BY t_cle", (match_id,)):
        d = int(round(float(r["t_cle"]) - horn_cle))
        if d < t0 or d > t1:
            continue
        side = int(r["a_team"]) if r["a_team"] in (2, 3) else 2
        who = short.get(r["attacker"], r["attacker"] or "?")
        out.append([d, side, TL_ROSHAN, "肉山 被 " + who + " 击杀"])
    out.sort(key=lambda e: e[0])
    return out


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
    det = dat.get("detail", {})
    nraw = sum(len(v) for v in det.values())
    msgs.append("±10s 明细：逐条 %d 条（Δ编码）｜名称字典 %d ｜ JSON 内占比见文件大小"
                % (nraw, len(dat.get("detail_names", []))))
    cd = dat.get("cd")
    if cd:
        nab = sum(len(v) for v in cd["ab"].values())
        nit = sum(len(v) for v in cd["it"].values())
        nint = sum(len(e[3]) for v in cd["ab"].values() for e in v) + \
               sum(len(e[3]) for v in cd["it"].values() for e in v)
        msgs.append("技能/道具 CD：能力条目 %d ｜ 道具条目 %d ｜ 冷却区间 %d ｜ 源 %s"
                    % (nab, nit, nint, cd["src"]))
        msgs.append("关键道具追踪：%s" % ("、".join("%s(%s)" % (k, cd["keys"][k]["name"]) for k in cd["track"]) or "无"))
        msgs.append("TP 使用次数（combat_log item use）：%s"
                    % "，".join("%s=%d" % (p["short"], len(dat["tp"].get(p["npc"], []))) for p in dat["players"]))
    else:
        msgs.append("技能/道具 CD：**旧库 dems/db/ 无本场 → 不提供**（如实回退，不硬造）")
    wd = dat.get("wards", [])
    if wd:
        no = sum(1 for w in wd if w[3] == 0)
        ns = sum(1 for w in wd if w[3] == 1)
        dew = sum(1 for w in wd if w[6] == 1)
        cen = sum(1 for w in wd if w[7] == 1)
        msgs.append("眼位（Q5B 同口径）：%d 支（假眼 %d / 真眼 %d）｜被反 %d ｜被比赛结束截断 %d"
                    % (len(wd), no, ns, dew, cen))
    else:
        msgs.append("眼位：无（解析失败或本场无数据）")
    msgs.append("烟雾：使用 %d 次 ｜ 英雄处于烟雾中的区间 %d 段"
                % (len(dat.get("smoke", [])),
                   sum(len(v) for v in (dat.get("smoked") or {}).values())))
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
    ap.add_argument("--out", default=None)
    ap.add_argument("--lite", action="store_true",
                    help="精简切片：跳过 ±10s combat log 明细（体积/耗时的大头），供 lite viewer / 批量构建")
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
    dat = parse_match(db, mid, league, with_detail=(not args.lite))
    for line in selfcheck(dat):
        print("  " + line)

    if args.no_write:
        return
    outdir = args.out or (os.path.join(OUTDIR, "lite") if args.lite else OUTDIR)
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, "q7_%s.json" % mid)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(dat, f, ensure_ascii=False, separators=(",", ":"))
    print("wrote %s  size=%.2f MB" % (os.path.relpath(p, ROOT), os.path.getsize(p) / 1e6))


if __name__ == "__main__":
    main()
