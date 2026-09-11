#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q5_ward.py - Q5 眼位：不易被反的假眼 / 容易反到假眼的真眼。

owner 口径:
  假眼=Observer(可反)→生存时长; 真眼=Sentry(真视,让范围内敌方假眼现形、被持有方反掉;自身自然到期)。
指标(逐格):
  假眼: 绝对生存(被反=destroy-place; 自然过期=寿命; 存活到终局=右删失, 单列/只算被反);
        相对生存 = 该眼位生存 ÷ 附近(100/300/600)平均生存 ×100%。高=刁钻。
  真眼: 成功 = 真眼存活窗口[放置,销毁]内, 其真视半径内【敌方假眼被持真眼方反掉】次数;
        相对 = 反眼成功÷附近平均 ×100%。高=好反的真眼位。
分辨率(sensitivity, owner 定): 细胞 1 / 4 / 16 单位(用实际坐标范围 MAP_HALF=8200 保全图; spec 的 9472² 覆盖不到)。
  判读: 细档=点状/局部; 粗档=保留=区域级。
输出(analysis/output_q5):
  q5_cell_<cs>.json  逐格绝对+相对(3 邻域)+match 列表(稀疏, 供 canvas 复核页)
  q5_detail.csv      逐格×match 明细(用于内部 drill-down)

Usage: python analysis/q5_ward.py [--sample N] [--tree]
"""
import argparse
import collections
import csv
import glob as _glob
import json
import math
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRUE_SIGHT_R = 1050.0   # 真视覆盖判定半径(单位): 假眼被反时 1050 内无真眼 -> 不归属(宝石/其他反的)
MAP_HALF = 8600.0   # 覆盖实测眼位坐标 ±8469 (spec 9472 仅 ±4736, 会裁边)
BINNINGS = [1, 4, 16, 172]   # add 172: 2*8600/172 = 100 -> 100x100 grid (Q5B 热力用)
TWINDOWS = [("-1:30-7", -1e9, 420), ("7-15", 420, 900), ("15+", 900, None)]   # 游戏时钟: 0:00=号角, -1:30=出门; 第一窗(含更早)归入
WINNAMES = ["-1:30-7", "7-15", "15+"]


def win_of_gl(sec):
    for i, (_, lo, hi) in enumerate(TWINDOWS):
        if sec is not None and sec >= lo and (hi is None or sec < hi):
            return i
    return None
RADII = [100, 300, 600]
BUCKET = 600.0   # spatial index bucket for nearby queries
SENTRY_LIFE = 420   # 真眼标准寿命秒(7分钟整, owner 确认)
OBSERVER_LIFE = 360 # 假眼标准寿命秒(6分钟整)


def other_team(t):
    return 3 if t == 2 else 2


# steam_id -> pro_players.team_name (官方队名), 用于给 viewer 显示对战双方
_pro_team_name = None
def load_pro_team_name():
    global _pro_team_name
    if _pro_team_name is not None:
        return _pro_team_name
    m = {}
    try:
        pp = json.load(open(os.path.join(ROOT, ".tmp", "pro_players.json"), encoding="utf-8"))
        for p in pp:
            if p.get("steamid") and p.get("team_name"):
                m[str(p["steamid"])] = p["team_name"]
    except Exception:
        pass
    _pro_team_name = m
    return m


def match_team_names(con, mid):
    """Return {'radiant': team_name, 'dire': team_name} for a match, aggregated
    from player_identity.steam_id -> pro_players.team_name (多数派)."""
    pm = load_pro_team_name()
    side = {"radiant": {}, "dire": {}}
    for r in con.execute("SELECT steam_id, player_slot FROM player_identity WHERE match_id=?", (mid,)):
        sid = r["steam_id"]
        if sid is None:
            continue
        tn = pm.get(str(sid))
        if not tn:
            continue
        k = "dire" if r["player_slot"] >= 128 else "radiant"
        side[k][tn] = side[k].get(tn, 0) + 1
    res = {}
    for k in ("radiant", "dire"):
        if side[k]:
            res[k] = max(side[k].items(), key=lambda x: x[1])[0]
        else:
            res[k] = None
    return res


def game_start(con, mid):
    """0:00(号角) = GAME_IN_PROGRESS(combat-log gamestate value=5) 的 t_cle(游戏时钟)。
    新架构: read from combat_log(通用表)。也是 game_start_raw(那刻 t_tick)分开。"""
    # 优先: combat_log 里 gamestate value=5 的 t_cle
    r = con.execute("SELECT t_cle, t_tick FROM combat_log WHERE match_id=? AND type_category='gamestate' AND value=5 LIMIT 1", (mid,)).fetchone()
    if r:
        return float(r["t_cle"])
    # fallback: 旧 building_spawn+90 启发式
    bs = con.execute("SELECT min(game_time_sec) FROM game_events WHERE match_id=? AND event_type='building_spawn'", (mid,)).fetchone()[0]
    if bs is None:
        r2 = con.execute("""SELECT min(t.game_time_sec) FROM (
            SELECT game_time_sec FROM game_events WHERE match_id=? AND event_type='gold'
            GROUP BY game_time_sec
            HAVING COUNT(DISTINCT actor_id)>=9 AND MIN(json_extract(properties,'$.value'))=MAX(json_extract(properties,'$.value')) AND MIN(json_extract(properties,'$.value'))<100
        ) t""", (mid,)).fetchone()
        return r2[0] if r2 and r2[0] is not None else None
    return bs + 90


def game_end_marker(con, mid):
    """真实游戏结束(raw)≈ 最后一条铁定 gameplay 事件(building_destroyed / gold)。
    不用 ward_destroyed/ward_placed(它们会被结算阶段的残留记录污染, 导致把结束后的僵直当暂停)。"""
    m1 = con.execute("SELECT MAX(game_time_sec) FROM game_events WHERE match_id=? "
                     "AND event_type IN ('building_destroyed','gold')", (mid,)).fetchone()[0]
    if m1 is None:
        return 0
    return m1


def pause_blocks(con, mid, end_marker=None):
    """Return list of (start, end) pause blocks (exclusive end) from hero-position
    freezing, but ONLY within [first game, end_marker]. Pauses after the game ends
    (end-of-game hero standstill) are excluded. Uses a whole-second gate."""
    if end_marker is None:
        end_marker = game_end_marker(con, mid)
    rows = list(con.execute("SELECT game_time_sec, entity_id, x, y FROM entity_snapshots "
                            "WHERE match_id=? AND entity_type='hero' ORDER BY game_time_sec", (mid,)))
    by_t = collections.defaultdict(dict)
    for t, eid, x, y in rows:
        by_t[int(t)][eid] = (x, y)
    ts = sorted(by_t)
    frozen = {}
    prev = None
    for t in ts:
        cur = by_t[t]
        if prev is None:
            frozen[t] = False
        else:
            frozen[t] = (sum(1 for k in cur if k in prev and cur[k] != prev[k]) == 0)
        prev = cur
    blocks = []
    start = None
    end = None
    for t in ts:
        if t > end_marker:
            # 游戏结束, 若正处冻结则截断到 end_marker
            if start is not None:
                blocks.append((start, min(end or start, end_marker)))
            start = None
            break
        if frozen[t]:
            if start is None:
                start = t
            end = t
        else:
            if start is not None:
                blocks.append((start, end))
                start = None
    if start is not None:
        blocks.append((start, min(end or start, end_marker)))
    # merge blocks separated by <=2s
    merged = []
    for b in blocks:
        if not merged:
            merged.append(b)
        elif b[0] - merged[-1][1] <= 2:
            merged[-1] = (merged[-1][0], b[1])
        else:
            merged.append(b)
    return [(s, e) for s, e in merged if e - s + 1 >= 3]


def pause_before(con, mid, raw, end_marker=None, gs_raw=None):
    """Cumulative pause seconds from 0:00(号角) up to `raw` (only real pauses inside game)."""
    if end_marker is None:
        end_marker = game_end_marker(con, mid)
    if gs_raw is None:
        gs_raw = game_start(con, mid)
    pb = pause_blocks(con, mid, end_marker)
    return sum(min(e, raw) - max(s, gs_raw) for s, e in pb if e > gs_raw and s < raw)


def game_end_cle(con, mid):
    """真实比赛结束(游戏时钟 cle) = **远古(Fort)被摧毁的那一刻**。

    依据(实测, 见 Q5_RULES_CALIBRATION_LOG §19):
      · combat_log 在远古被摧毁后仍继续记录很久 —— 30 场实测尾部 360~925s(中位 398s),
        其中含 354 条守卫死亡(结算残留)与 gamestate 条目;
      · 用 combat_log 的 MAX(t_cle) 当"比赛结束" => 把这段残留算进比赛,
        使末段守卫的存活时间被**系统性高估**(实测 9.2% 的假眼 place+寿命 > 真实结束时刻);
      · ward_placed 事件在该时刻之后为 0 条(无人再插眼), 远古死亡 970/970 场都有。
    返回 None 表示拿不到权威结束时刻(此时调用方应退回"不截断"的老口径)。
    """
    r = con.execute("SELECT MAX(t_cle) FROM combat_log WHERE match_id=? AND type_category='death' "
                    "AND target IN ('npc_dota_goodguys_fort','npc_dota_badguys_fort')", (mid,)).fetchone()
    v = r[0] if r else None
    return float(v) if v is not None else None


def parse_match(con, mid, censor_at_end=True):
    """Return (obs_records, sentry_records) for this match.  新架构版。

    【权威方法 DEM_FORMAT 附录 C/D5】以守卫实体为"眼"稳定单元, 三源独立:
      - 实体(位置/类型/队/区间[出生,末现]):  game_events.ward_placed + entity_snapshots.
        判型 = 实体类名(CDOTA_NPC_Observer_Ward_TrueSight=真眼 / CDOTA_NPC_Observer_Ward=假眼);
        绝不用 combat inflictor 判型(dispenser 真/假都出, 实测 829 假 / 1313 真).
      - 放置(时刻): 同队 item use(inflictor=item_ward_*) ±2.5s → 取 its t_cle;
        无命中则用实体 t_tick 经 combat_log 轴换算(实体自带 t_cle 恒早 540s, 禁用).
      - 销毁(时刻): combat_log death(target=npc_*_wards). **Death 不带坐标**(parse.rs 明载),
        故按 (队,型) 做"一一对应"匹配: ① 到期精确配对(出生tt+寿命 ±2.5s, attacker==target);
        ② 其余(non-self杀)按【实体自身末现 − 9s】定位, 每个 death 只用一次; ③ 兜底=出生+寿命.
    ◆ 眼稳定键 = (队, 型, 出生位置, ~出生时刻), 绝不以 entity_index 为主键(会复用).
    ◆ 到期口径(owner 裁定 2026): attacker==target ⇒ expired; 其余(敌方英雄/小兵/塔/野怪/
      **同队英雄自毁**)统计上一律计入"被反"。到期眼的销毁时刻取常量(出生+420/360)。
    ◆ 右删失(censor_at_end=True, 默认): 存活窗口被**比赛结束**(远古被摧毁)截断 ——
      到期时刻 > 结束时刻 的眼, 其"存活"改为 结束时刻-放置时刻 并置 censored=True
      (实测 9.2% 的假眼受影响; 见 game_end_cle 注释)。censor_at_end=False 退回旧口径(存活=寿命, 上界)。
    """
    # 0) 权威比赛结束时刻(游戏时钟): 远古被摧毁。用于右删失截断(见 docstring).
    end_cle = game_end_cle(con, mid) if censor_at_end else None

    # 1) 实体区间: entity_snapshots 每实体 [出生,末现] + 型/队
    ent_span = {}
    for r in con.execute(
            "SELECT entity_id, MIN(game_time_sec) t0, MAX(game_time_sec) t1, "
            "json_extract(extra,'$.ward_type') wt, team FROM entity_snapshots "
            "WHERE match_id=? AND entity_type='ward' GROUP BY entity_id", (mid,)):
        eid = r["entity_id"]
        if not eid.startswith("ward:"):
            continue
        idx = int(eid.split(":")[1])
        ent_span[idx] = {"t0": r["t0"], "t1": r["t1"], "wt": r["wt"], "team_text": r["team"]}

    # 2) 实体"出生"坐标: ward_placed 每行 = 一支眼(含位置/型/队/t_tick/t_cle)。可能与 ent_span 对应.
    #    眼用 (team, wt, x, y, t_tick) 复合键; 同一 eidx 若多次出现(位置不同) = 复用的不同眼.
    ents = []
    for r in con.execute(
            "SELECT game_time_sec, x, y, json_extract(properties,'$.ward_type') wt, "
            "json_extract(properties,'$.team') team, json_extract(properties,'$.t_tick') tt, "
            "json_extract(properties,'$.t_cle') tcle, json_extract(properties,'$.entity_index') eidx "
            "FROM game_events WHERE match_id=? AND event_type='ward_placed'", (mid,)):
        eidx = r["eidx"]
        span = ent_span.get(eidx) if eidx is not None else None
        ents.append({"x": r["x"], "y": r["y"], "wt": r["wt"], "team": r["team"],
                     "tt": r["tt"] or r["game_time_sec"], "tcle": r["tcle"] or r["game_time_sec"],
                     "eidx": eidx,
                     "t0": span["t0"] if span else (r["tt"] or r["game_time_sec"]),
                     "t1": span["t1"] if span else None})

    # 3) 放置(use) 事件: combat_log item(inflictor=item_ward_*), 排除给队友(target 非空).
    #    判型用实体, 但这里按 inflictor 粗分(仅用于同型过滤), 放置时刻/玩家取自它.
    uses = []
    hero_team = {}
    def team_of(hero):
        if hero not in hero_team:
            rr = con.execute("SELECT team_id FROM player_identity WHERE match_id=? AND hero_name=?", (mid, hero)).fetchone()
            hero_team[hero] = rr["team_id"] if rr else None
        return hero_team[hero]
    for r in con.execute(
            "SELECT t_cle, t_tick, attacker, inflictor FROM combat_log "
            "WHERE match_id=? AND type_category='item' AND inflictor IN "
            "('item_ward_sentry','item_ward_observer','item_ward_dispenser') AND (target IS NULL OR target='')", (mid,)):
        team = team_of(r["attacker"])
        uses.append({"cle": r["t_cle"], "tt": r["t_tick"], "att": r["attacker"], "team": team, "infl": r["inflictor"]})

    # 4) 销毁(destroy) 事件: combat_log death, target = **权威单位名精确匹配**.
    #    【根因修正-死亡池】绝不能用 `target LIKE '%ward%'` —— 那会把"名字带 ward 的召唤物"也收进来:
    #      npc_dota_venomancer_plague_ward_1..4(瘟疫守卫) / npc_dota_witch_doctor_death_ward /
    #      npc_dota_juggernaut_healing_ward  等(实测 80 场里占 8.43% 即 829/9836),
    #      它们会被当成 observer 的死亡参与配对 -> 污染存活窗口(实测把"活满寿命到期"的假眼判成 136s 被反).
    #    只有 npc_dota_observer_wards / npc_dota_sentry_wards 才是眼(parse.rs 明载权威单位名).
    #    按 (t_team, wt) 分组 + 按 tt 排序.
    dests = collections.defaultdict(list)
    for r in con.execute(
            "SELECT t_cle, t_tick, attacker, target, a_team, t_team FROM combat_log "
            "WHERE match_id=? AND type_category='death' "
            "AND target IN ('npc_dota_observer_wards','npc_dota_sentry_wards')", (mid,)):
        wt = "sentry" if r["target"] == "npc_dota_sentry_wards" else "observer"
        # 【口径-权威】到期 ⇔ attacker == target(守卫"被它自己"销毁; 见 dota_parse/src/parse.rs 文档)。
        # 其余一律算"被摧毁/被反": 敌方英雄反眼、**同队英雄自毁**、小兵/塔、中立野怪(眼插野点被打)。
        #   owner 裁定: 同队英雄自毁在统计意义上计入"被反"。
        # 旧口径用 a_team != t_team 判被反 -> 会把那 25 条自毁当成"非被反", 甚至让早死的眼 fallback 成活满 420s。
        isdew = (r["attacker"] or "") != (r["target"] or "")
        dests[(r["t_team"], wt)].append({"cle": r["t_cle"], "tt": r["t_tick"], "isdew": isdew})
    for k in dests:
        dests[k].sort(key=lambda d: d["tt"])
    # 放置(use) 按 (team, wt) 分组排序
    #   【根因修正-dispenser】item_ward_dispenser 真/假眼都会出(实测 observer 829 / sentry 1313),
    #   而 inflictor 字符串里不含 "sentry" -> 旧写法一律归到 observer 桶 -> **用眼药盒插的真眼永远找不到自己的 use**
    #   (实测 8826052816 eidx2402: enchantress 用 dispenser 插的真眼, 只能绑定到 17s 后才首见的 snapfire sentry use,
    #    寿命窗口因此偏晚 -> 到期死亡配不上 -> 被错判成 175s 被反)。故 dispenser 的 use **同时进两个桶**。
    use_by_g = collections.defaultdict(list)
    for u in uses:
        infl = u["infl"] or ""
        if "dispenser" in infl:
            wts = ("sentry", "observer")
        elif "sentry" in infl:
            wts = ("sentry",)
        else:
            wts = ("observer",)
        for wt in wts:
            use_by_g[(u["team"], wt)].append(dict(u, wt=wt))
    for k in use_by_g:
        use_by_g[k].sort(key=lambda u: u["tt"])

    def nearest_in(grp, t, window):
        """在已按tt排序的 grp 里找 tt 最接近 t 且 |tt-t|<=window 的事件."""
        if not grp: return None
        import bisect
        keys = [g["tt"] for g in grp]
        i = bisect.bisect_left(keys, t)
        cands = []
        if i < len(keys): cands.append(grp[i])
        if i - 1 >= 0: cands.append(grp[i - 1])
        best = None; bd = 1e18
        for c in cands:
            dd = abs(c["tt"] - t)
            if dd < bd: bd = dd; best = c
        return (best if bd <= window else None)

    # ── t_tick → 游戏时钟(t_cle) 换算表 ─────────────────────────────────
    # 【根因】ward_placed 实体自带的 t_cle 字段恒比 combat_log 体系早 540s(全表 t_cle-t_tick=-531.53),
    #        而 combat_log 的 t_cle 才是 Dota 游戏时钟(且 t_cle-t_tick=+8.73 号角后恒定).
    #        因此放置时刻绝不能 fallback 到实体自带 t_cle; 必须由实体 t_tick(与 combat 同轴)
    #        经 combat_log 的 (t_tick->t_cle) 映射还原. 这里一次性建表供二分.
    tt2cle = []
    for r in con.execute("SELECT t_tick, t_cle FROM combat_log WHERE match_id=? AND t_tick IS NOT NULL "
                         "ORDER BY t_tick", (mid,)):
        tt2cle.append((r["t_tick"], r["t_cle"]))
    tt_axis = [x[0] for x in tt2cle]
    def cle_for_tt(tt):
        """按 combat_log 轴, 求 t_tick=tt 附近最近的游戏时钟(t_cle). t_cle 随 t_tick 单调不减(暂停时冻结=平)."""
        import bisect
        if not tt2cle or tt is None:
            return None
        i = bisect.bisect_left(tt_axis, tt)
        # 相邻两个 combat 记录(≤前/≥后): 取 t_cle 更接近且 >= 侧优先? 直接取二分左侧(>=tt 的最小), 失效回退前一个.
        idx = i if i < len(tt2cle) else len(tt2cle) - 1
        return tt2cle[idx][1]

    # 5) 组装眼 records: 实体为主 + 销毁"一一对应"匹配
    #   【根因修正-销毁】原实现"出生窗内最早的 death 归本眼"会让相邻同队同型眼**互相抢死亡**
    #   (实测 8830423116: 中心眼 eidx2474 抢走了左上眼 eidx3508 在 15:46 的被反, 使 3508 的被反
    #    错记到 2474 身上, 并让 3508 被判为自然到期) -> 位置/时刻全错.
    #   新规则(三条, 每条独立可验):
    #     ① 到期精确配对: 真/假眼寿命固定(420/360) —— ⚠️寿命是【游戏时钟 cle】常量(玩家看到的 7:00/6:00),
    #        必须在 cle 空间配对! 用 tick 空间会因暂停(cle 冻结/tick 照走)而差出几十秒 -> 到期死亡配不上、
    #        被别的眼抢走(实测 8825996999 eidx962: cle 差正好 420.00, tick 差 437.5 -> 旧写法漏配).
    #     ② 被反一一对应: 窗口也在 cle 空间; 死亡定位用**实体自身末现 t1**(tick 空间, 与死亡 t_tick 同轴,
    #        尾差 ~5-14s 取主峰 9s); 每个 death 只能用一次
    #     ③ 兜底: place + 寿命 = expired
    #     【根因修正-PVS 延迟】守卫实体"首次被看到"会因视野(PVS)晚于真实插入(实测最晚差 40s+)。
    #       use(combat item)才是真实插入时刻 -> 放置候选 = use 落在 [首见−35s, 首见+2.5s](同队同型), 兜底用首见换算.
    #       只搜 ±2.5s 会漏掉真实 use, 导致寿命窗口起点偏晚 -> 到期死亡配不上 -> 被错配成"被反"
    #       (实测 8826052816 eidx2402: 真插 3:56.73(enchantress dispenser), 首见 4:13.87(snapfire sentry),
    #        到期死亡 tick = 真插+420.00 精确命中; 旧写法按首见算 -> 错判成 175s 被反).
    #       use 与 death 都做**一一对应**(一次 use 只产一支眼; 一条死亡只销一支眼)。
    TAIL = 9.0        # 实体末现比真实死亡晚 ~5-14s(1Hz 采样量化), 取主峰 9s
    USE_BACK = 35.0   # use 允许早于"首见"的最大秒数(PVS 延迟)
    NO_DEATH = 500.0  # 无死亡可配(自然到期/死亡事件缺失)的基准代价
    PROX = 0.1        # 放置候选"离首见多远"的每秒钟轻微惩罚(仅作平局判据)
    pool = {k: [dict(x, used=False) for x in ds] for k, ds in dests.items()}
    # use 对象按 (队,型) 共享(带 taken 标记) -> 保证"一次 use 只产一支眼"
    use_pool = {}
    for k, us in use_by_g.items():
        use_pool[k] = [{"cle": u["cle"], "tt": u["tt"], "att": u.get("att"), "infl": u.get("infl"),
                        "src": "use", "taken": False} for u in us]
    wards = []
    for e in ents:
        if e["wt"] is None or e["team"] is None:
            continue
        life = SENTRY_LIFE if e["wt"] == "sentry" else OBSERVER_LIFE
        t1 = e.get("t1")
        # entity_index 复用时 t1 可能属后一段 -> 不可信, 弃用
        if t1 is not None and e["tt"] is not None and (t1 - e["tt"]) > life + 25:
            t1 = None
        wards.append({"e": e, "life": life, "t1": t1, "place": None, "use": None,
                      "death": None, "reason": None})

    # ① (use, death) 联合候选 -> 全局一一对应(贪心, 代价升序)
    cands = []
    for (team, wt), ds in pool.items():
        ws = [w for w in wards if w["e"]["team"] == team and w["e"]["wt"] == wt]
        if not ws:
            continue
        us = use_pool.get((team, wt), [])
        for w in ws:
            life = w["life"]; first = w["e"]["tt"]
            plist = [u for u in us
                     if first is not None and first - USE_BACK <= u["tt"] <= first + 2.5]
            if not plist and first is not None:
                plist = [{"cle": cle_for_tt(first), "tt": first, "att": None, "infl": None,
                          "src": "fallback", "taken": False}]
            for p in plist:
                if p["cle"] is None:
                    continue
                # (a) 无死亡 -> 到期兜底
                cands.append((NO_DEATH + abs((first if first is not None else p["tt"]) - p["tt"]), w, p, None))
                # (b) 配一条死亡
                for d in ds:
                    if not (p["cle"] < d["cle"] <= p["cle"] + life + 2):
                        continue
                    if (not d["isdew"]) and abs(d["cle"] - (p["cle"] + life)) > 2.5:
                        continue
                    if w["t1"] is not None:
                        cost = abs(d["tt"] - (w["t1"] - TAIL))
                    else:
                        cost = 1000.0 + abs(d["cle"] - (p["cle"] + life))
                    # 同一死亡可能有多个候选 use(代价相同) -> 用"越靠近首见越可信"做平局判据(PROX 很小,
                    # 不会盖过身份证据; 只为避免放置时刻无理由地漂到更早的 use)
                    if first is not None and p["src"] == "use":
                        cost += PROX * abs(p["tt"] - first)
                    cands.append((cost, w, p, d))
    cands.sort(key=lambda x: x[0])
    for cost, w, p, d in cands:
        if w["place"] is not None:
            continue
        # 注意: 不设 use 独占 —— 若某次 use 被判给别的眼, 本眼的"所有死亡候选"会因共享该放置而一起失效,
        # 只剩"无死亡->到期"兜底(实测 8825996999 eidx2530: 快照跨期仅 84s 却被迫判成到期 420s)。
        # use 与眼本就近似一一对应, 放开独占只在"近同时插眼"时让两眼放置时刻差几秒(可忽略)。
        if d is not None and d["used"]:
            continue
        w["place"] = p["cle"]; w["use"] = p
        p["taken"] = True
        if d is not None:
            d["used"] = True; w["death"] = d
            w["reason"] = "dewarded" if d["isdew"] else "expired"
        else:
            w["reason"] = "expired"
    # 兜底: 仍无放置的眼(首见缺失等)
    for w in wards:
        if w["place"] is None and w["e"]["tt"] is not None:
            w["place"] = cle_for_tt(w["e"]["tt"])
            w["reason"] = w["reason"] or "expired"
        elif w["reason"] is None:
            w["reason"] = "expired"
    # 5b) 产出 records
    records = []
    orphan_uses = []
    for w in wards:
        e = w["e"]; wt = e["wt"]; team = e["team"]
        u = w["use"]
        place_clock = w["place"]
        life = w["life"]
        # 【口径】到期 = 寿命常量: 判为 expired 的眼, 销毁时刻直接取 出生 + 寿命(真眼 420 / 假眼 360).
        #   死亡事件只用于【确认】是到期(见 pass①), 不用它定时 —— 避免 1Hz 时钟量化带来的 0~2.5s 记账误差.
        #   被反(dewarded)才用该死亡事件自己的游戏时钟.
        if w["death"] is not None and w["reason"] == "dewarded":
            destroy_clock = w["death"]["cle"]; reason = "dewarded"
        else:
            destroy_clock = place_clock + life; reason = "expired"
        censored = False
        if destroy_clock is not None and place_clock is not None and destroy_clock < place_clock:
            destroy_clock = place_clock + life; reason = "expired"
        # 被反时刻不得越过寿命(匹配窗口给了 +2s 容差) -> 超过的按寿命截断, 保证 存活 <= 寿命
        if destroy_clock is not None and place_clock is not None and destroy_clock > place_clock + life:
            destroy_clock = place_clock + life
        # 【右删失】存活窗口被"比赛结束"(远古被摧毁)截断 —— 见 game_end_cle()。
        #   未被反的眼若 出生+寿命 晚于比赛结束, 它并不是"活满寿命", 而是**比赛结束了**;
        #   不截断会把末段(20+ 窗口)的"平均存活"系统性拉高(实测 8.5% 的假眼属于此类)。
        #   容差 0.05s = 半个导出格: "到期比结束晚"就是"结束那一刻它还活着", 不做任何"差不多算到期"的宽容
        #   (早先用 0.5s 容差 -> 全量里有 2 行 存活 比 结束-放置 大 0.3~0.4s, 明细会自相矛盾)。
        if censor_at_end and end_cle is not None and place_clock is not None and destroy_clock is not None \
                and destroy_clock > end_cle + 0.05:
            destroy_clock = max(place_clock, end_cle)
            censored = True
            if place_clock > end_cle + 0.05:
                reason = "post_game"                      # 防御: 结束后才插的眼(实测 0 条)
            elif reason == "expired":
                reason = "censored"                      # 到期时刻落在比赛结束之后 -> 只知道"活到结束"
        survival = (destroy_clock - place_clock) if destroy_clock is not None else None
        records.append({"type": wt, "team": team, "x": e["x"], "y": e["y"],
                        "place": place_clock, "destroy": destroy_clock, "reason": reason,
                        "survival": survival, "actor": u["att"] if u else None, "censored": censored,
                        "entity_index": e["eidx"]})

    # 6) 反眼归属: 反眼地点 = 被反假眼/真眼位置; 归属给"覆盖该地点最近的存活真眼"(异阵营)。
    sentries = [r for r in records if r["type"] == "sentry"]
    for s in sentries:
        s["success"] = 0; s["dew_sen"] = 0; s["dew_times"] = []
        s["dws_times"] = []; s["dew_obs_pos"] = []; s["dew_sen_pos"] = []
    deward_obs = [r for r in records if r["type"] == "observer" and r.get("reason") == "dewarded"]
    deward_sen = [r for r in records if r["type"] == "sentry" and r.get("reason") == "dewarded"]
    def covering(team, x, y, t):
        best = None; bestd = 10 ** 18
        for s in sentries:
            if s["team"] != team: continue
            s_end = s["destroy"] if s["destroy"] is not None else 10 ** 9
            if not (s["place"] <= t <= s_end): continue
            dd = math.hypot(s["x"] - x, s["y"] - y)
            if dd <= TRUE_SIGHT_R and dd < bestd:
                bestd = dd; best = s
        return best
    for o in deward_obs:
        s = covering(other_team(o["team"]), o["x"], o["y"], o["destroy"])
        if s is not None:
            s["success"] += 1
            s["dew_times"].append(o["destroy"])
            s["dew_obs_pos"].append([round(o["x"], 1), round(o["y"], 1), o["destroy"], o["team"], s["team"]])
    for ss in deward_sen:
        s = covering(other_team(ss["team"]), ss["x"], ss["y"], ss["destroy"])
        if s is not None:
            s["dew_sen"] += 1
            s["dws_times"].append(ss["destroy"])
            s["dew_sen_pos"].append([round(ss["x"], 1), round(ss["y"], 1), ss["destroy"], ss["team"], s["team"]])
    obs = [r for r in records if r["type"] == "observer"]
    sen = [r for r in records if r["type"] == "sentry"]
    return obs, sen, orphan_uses


BUCKET = 600.0   # spatial index bucket (unused with prefix-sum, kept for reference)
GS = 50.0        # prefix-sum grid cell (units); ng = map_width/GS


def build_pref(wards):
    """2D prefix sums over a coarse grid for O(1) radius-window mean. square window approximation."""
    ng = int(round(2 * MAP_HALF / GS))
    s = [[0.0] * ng for _ in range(ng)]
    c = [[0] * ng for _ in range(ng)]
    for w in wards:
        x, y, v = w[0], w[1], w[2]
        gx = max(0, min(ng - 1, int((x + MAP_HALF) / GS)))
        gy = max(0, min(ng - 1, int((y + MAP_HALF) / GS)))
        s[gy][gx] += v; c[gy][gx] += 1
    ps = [[0.0] * (ng + 1) for _ in range(ng + 1)]
    pc = [[0] * (ng + 1) for _ in range(ng + 1)]
    for i in range(ng):
        si = s[i]; psi = ps[i]; psi1 = ps[i + 1]; pci = pc[i]; pci1 = pc[i + 1]
        for j in range(ng):
            psi1[j + 1] = psi[j + 1] + psi1[j] - psi[j] + si[j]
            pci1[j + 1] = pci[j + 1] + pci1[j] - pci[j] + c[i][j]
    return ps, pc, ng


def near_mean(ps, pc, ng, xc, yc, r):
    gx0 = max(0, min(ng, int((xc - r + MAP_HALF) / GS)))
    gx1 = max(0, min(ng, int((xc + r + MAP_HALF) / GS) + 1))
    gy0 = max(0, min(ng, int((yc - r + MAP_HALF) / GS)))
    gy1 = max(0, min(ng, int((yc + r + MAP_HALF) / GS) + 1))
    cnt = pc[gy1][gx1] - pc[gy0][gx1] - pc[gy1][gx0] + pc[gy0][gx0]
    if cnt == 0:
        return None
    sm = ps[gy1][gx1] - ps[gy0][gx1] - ps[gy1][gx0] + ps[gy0][gx0]
    return sm / cnt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--tree", action="store_true")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "output_q5"))
    ap.add_argument("--no-censor-at-end", action="store_true",
                    help="关闭右删失截断(退回旧口径: 未被反的眼一律记 存活=寿命, 是上界)")
    args = ap.parse_args()
    dbs = sorted(_glob.glob(os.path.join(ROOT, "dems", "db_full", "*", "*.db")))
    if args.sample:
        dbs = dbs[:args.sample]
    os.makedirs(args.out, exist_ok=True)

    obs_all = []      # (x,y,survival_full, survival_dew_or_None, censored)
    sen_all = []      # (x,y,success)
    det_rows = []
    orphan_all = []   # (mid, (team, ward_type, use_cle, placed_tick, gap)) 供对证(孤儿 use)
    n_match = 0
    match_teams = {}   # mid -> {radiant, dire} 官方队名
    for db in dbs:
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        for mid in [r[0] for r in con.execute("SELECT DISTINCT match_id FROM combat_log WHERE type_category='item' AND inflictor LIKE 'item_ward%'")]:
            gs = game_start(con, mid) or 0
            match_teams[mid] = match_team_names(con, mid)
            obs, sen, orphan_uses = parse_match(con, mid, censor_at_end=not args.no_censor_at_end)
            for ou in orphan_uses:
                orphan_all.append((mid, ou))
            for o in obs:
                surv = o["survival"] if o["survival"] is not None else None
                obs_all.append((o["x"], o["y"], surv if surv is not None else -1,
                                (surv if o["reason"] == "dewarded" else None), o["censored"], mid))
                if args.tree:
                    sur = ("%ds/%s" % (o["survival"], o["reason"])) if o["survival"] is not None else "CENSORED"
                    print("  obs t=%s (%+.0f,%+.0f) %s" % (o["team"], o["x"], o["y"], sur))
            for s in sen:
                sen_all.append((s["x"], s["y"], s["success"], s["dew_sen"], s["team"], mid,
                                (s["place"] - gs), (s["destroy"] - gs) if s["destroy"] is not None else None,
                                s["survival"], 1 if s["reason"] == "dewarded" else 0,
                                [round(t - gs, 1) for t in s["dew_times"]] if s["dew_times"] else [],
                                [round(t - gs, 1) for t in s["dws_times"]] if s["dws_times"] else [],
                                [[p[0], p[1], round(p[2] - gs, 1), p[3], p[4]] for p in s["dew_obs_pos"]],
                                [[p[0], p[1], round(p[2] - gs, 1), p[3], p[4]] for p in s["dew_sen_pos"]]))
                if args.tree:
                    print("  sen t=%s (%+.0f,%+.0f) succ=%s" % (s["team"], s["x"], s["y"], s["success"]))
            n_match += 1
            if n_match % 100 == 0:
                print("parsed %d/%d matches..." % (n_match, len(dbs)))
        con.close()
    print("matches:", n_match, " obs:", len(obs_all), " sentries:", len(sen_all))

    # 孤儿 use 单独输出(供总控到游戏里核实 toggle stacked ward 等)
    if orphan_all:
        op_out = os.path.join(args.out, "q5_orphan_uses.csv")
        with open(op_out, "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(["match_id", "team", "ward_type", "use_cle", "placed_tick", "gap_s"])
            def _r(v):
                return round(v, 2) if v is not None else None
            for (m, ou) in orphan_all:
                team, wt, use_cle, placed_tick, gap = ou
                wr.writerow([m, team, wt, _r(use_cle), _r(placed_tick), _r(gap)])
        print("  wrote orphan uses ->", op_out, "count:", len(orphan_all))
    else:
        print("  orphan uses: 0")

    # 各场对战双方队名(供 Q5B 查看器显示)
    mj_out = os.path.join(args.out, "q5_matches.json")
    with open(mj_out, "w", encoding="utf-8") as f:
        json.dump({str(m): v for m, v in match_teams.items()}, f, ensure_ascii=False)
    print("wrote", mj_out, "matches:", len(match_teams))

    # NON-CENSORED pools for nearby / absolute (right-censored suppressed from survival means)
    obs_full = [(x, y, v) for (x, y, v, _d, _c, _m) in obs_all if v >= 0]
    obs_dew = [(x, y, v) for (x, y, _v, d, _c, _m) in obs_all if d is not None for v in [d]]
    pref_full = build_pref(obs_full)
    pref_dew = build_pref(obs_dew)
    pref_sen = build_pref(sen_all)

    # ---- per-resolution cell aggregation ----
    for cs in BINNINGS:
        n = int(round(2 * MAP_HALF / cs))
        cell_obs = collections.defaultdict(dict)   # (cx,cy) -> {count,sum,sum_dew,ced}
        cell_sen = collections.defaultdict(dict)
        win_sen = collections.defaultdict(dict)   # (cx,cy,w) -> sentry aggregates per time window
        det_sen = []   # [cx,cy,match,place_clock,destroy_clock,survival,success]
        for (x, y, v, d, ce, m) in obs_all:
            cx = int((x + MAP_HALF) / cs); cy = int((y + MAP_HALF) / cs)
            cx = max(0, min(n - 1, cx)); cy = max(0, min(n - 1, cy))
            a = cell_obs.setdefault((cx, cy), {"cnt": 0, "sum": 0.0, "dew_cnt": 0, "dew_sum": 0.0, "ced": 0, "mids": set()})
            if ce:
                a["ced"] += 1
            if v >= 0:
                a["cnt"] += 1; a["sum"] += v
                if d is not None:
                    a["dew_cnt"] += 1; a["dew_sum"] += d
            a["mids"].add(m)
        for (x, y, succ, dewsen, team, m, pl, dest, surv, wasdew, dewts, dwsts, dewobspos, dewsenspos) in sen_all:
            cx = int((x + MAP_HALF) / cs); cy = int((y + MAP_HALF) / cs)
            cx = max(0, min(n - 1, cx)); cy = max(0, min(n - 1, cy))
            a = cell_sen.setdefault((cx, cy), {"cnt": 0, "sum": 0.0, "max": 0, "mids": set(), "n0": 0, "n1": 0, "n2": 0, "sdw": 0, "dws": 0})
            a["cnt"] += 1; a["sum"] += succ; a["max"] = max(a["max"], succ); a["dws"] += dewsen
            if succ == 0: a["n0"] += 1
            elif succ == 1: a["n1"] += 1
            else: a["n2"] += 1
            if wasdew: a["sdw"] += 1     # 真眼自己被反(反真眼)
            a["mids"].add(m)
            w = win_of_gl(pl)
            if w is not None:
                wa = win_sen.setdefault((cx, cy, w, team), {"cnt": 0, "sum": 0.0, "dws": 0, "mids": set()})
                wa["cnt"] += 1; wa["sum"] += succ; wa["dws"] += dewsen
                wa["mids"].add(m)
            det_sen.append([cx, cy, m, pl, dest, surv, succ, wasdew, dewsen, w, team, dewts, dwsts, dewobspos, dewsenspos])

        cells = []
        for (cx, cy) in sorted(set(list(cell_obs.keys()) + list(cell_sen.keys()))):
            xc = (cx + 0.5) * cs - MAP_HALF; yc = (cy + 0.5) * cs - MAP_HALF
            ob = cell_obs.get((cx, cy), {})
            sn = cell_sen.get((cx, cy), {})
            obs_cnt = ob.get("cnt", 0)
            obs_surv = (ob["sum"] / ob["cnt"]) if ob.get("cnt") else None
            obs_surv_dew = (ob["dew_sum"] / ob["dew_cnt"]) if ob.get("dew_cnt") else None
            sen_cnt = sn.get("cnt", 0)
            sen_rate = (sn["sum"] / sn["cnt"]) if sn.get("cnt") else None
            sen_total = sn.get("sum", 0)
            cell = {"cs": cs, "cx": cx, "cy": cy, "x": round(xc, 1), "y": round(yc, 1),
                    "n_obs": obs_cnt, "obs_surv": obs_surv, "obs_surv_dew": obs_surv_dew,
                    "obs_ced": ob.get("ced", 0), "n_obs_games": len(ob.get("mids", ())),
                    "n_sen": sen_cnt, "sen_rate": sen_rate, "sen_total": sen_total,
                    "n_sen_games": len(sn.get("mids", ())),
                    "sen0": sn.get("n0", 0), "sen1": sn.get("n1", 0), "sen2": sn.get("n2", 0),
                    "sen_dew": sn.get("sdw", 0), "sen_dwtotal": sn.get("dws", 0),
                    "rel": {}}
            for r in RADII:
                nb_full = near_mean(pref_full[0], pref_full[1], pref_full[2], xc, yc, r)
                nb_dew = near_mean(pref_dew[0], pref_dew[1], pref_dew[2], xc, yc, r)
                nb_sen = near_mean(pref_sen[0], pref_sen[1], pref_sen[2], xc, yc, r)
                cell["rel"][str(r)] = {
                    "obs_surv": (obs_surv / nb_full * 100) if (obs_surv is not None and nb_full) else None,
                    "obs_surv_dew": (obs_surv_dew / nb_dew * 100) if (obs_surv_dew is not None and nb_dew) else None,
                    "sen_rate": (sen_rate / nb_sen * 100) if (sen_rate is not None and nb_sen) else None,
                }
            cells.append(cell)
        # write json (drop huge unrelated) + match detail
        out = os.path.join(args.out, "q5_cell_%d.json" % cs)
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"cs": cs, "n": n, "map_half": MAP_HALF, "cells": cells}, f, ensure_ascii=False)
        occ = sum(1 for c in cells if c["n_obs"] or c["n_sen"])
        print(" cs=%d  cells=%d (occupied=%d)" % (cs, len(cells), occ))
        # per-cell x match detail csv
        det_out = os.path.join(args.out, "q5_cell_matches_%d.csv" % cs)
        with open(det_out, "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(["cell_x", "cell_y", "x_center", "y_center", "match_id"])
            for (cx, cy), mids in sorted((k, v.get("mids", set())) for k, v in cell_obs.items()):
                xc = (cx + 0.5) * cs - MAP_HALF; yc = (cy + 0.5) * cs - MAP_HALF
                for m in sorted(mids):
                    wr.writerow([cx, cy, round(xc, 1), round(yc, 1), m])
            for (cx, cy), mids in sorted((k, v.get("mids", set())) for k, v in cell_sen.items()):
                if (cx, cy) in cell_obs:
                    continue
                xc = (cx + 0.5) * cs - MAP_HALF; yc = (cy + 0.5) * cs - MAP_HALF
                for m in sorted(mids):
                    wr.writerow([cx, cy, round(xc, 1), round(yc, 1), m])
        # sentry per-placement detail (for verify drill-down): match + game-clock place/destroy + survival
        sd_out = os.path.join(args.out, "q5_sentry_detail_%d.csv" % cs)
        with open(sd_out, "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(["cell_x", "cell_y", "x_center", "y_center", "match_id", "place_clock", "destroy_clock", "survival_sec", "success", "was_dewarded", "dew_sen", "win", "team", "dew_times", "dws_times", "dew_obs_pos", "dew_sen_pos"])
            for cx, cy, m, pl, dest, surv, succ, wasdew, dewsen, w, team, dewts, dwsts, dewobspos, dewsenspos in det_sen:
                xc = (cx + 0.5) * cs - MAP_HALF; yc = (cy + 0.5) * cs - MAP_HALF
                wr.writerow([cx, cy, round(xc, 1), round(yc, 1), m,
                             round(pl, 1) if pl is not None else None,
                             round(dest, 1) if dest is not None else None,
                             round(surv, 1) if surv is not None else None, succ, wasdew, dewsen, w, team,
                             " ".join(str(t) for t in dewts), " ".join(str(t) for t in dwsts),
                             json.dumps(dewobspos), json.dumps(dewsenspos)])
        # per (cell, time-window, team): average deward per sentry (假眼/真眼/总)
        wj_out = os.path.join(args.out, "q5_sen_win_%d.json" % cs)
        wcells = []
        for (cx, cy, w, team), a in sorted(win_sen.items()):
            xc = (cx + 0.5) * cs - MAP_HALF; yc = (cy + 0.5) * cs - MAP_HALF
            nt = a["cnt"]
            aj = (a["sum"] / nt) if nt else 0.0   # 平均反假眼
            az = (a["dws"] / nt) if nt else 0.0   # 平均反真眼
            wcells.append({"cs": cs, "cx": cx, "cy": cy, "x": round(xc, 1), "y": round(yc, 1), "win": w, "team": team,
                           "n_sen": nt, "ng": len(a["mids"]),
                           "avg_jy": round(aj, 3), "avg_zy": round(az, 3), "avg_all": round(aj + az, 3),
                           "jy_total": round(a["sum"]), "zy_total": round(a["dws"])})
        with open(wj_out, "w", encoding="utf-8") as f:
            json.dump({"cs": cs, "map_half": MAP_HALF, "wins": TWINDOWS, "cells": wcells}, f, ensure_ascii=False)
        print("  win_sen cells:", len(wcells))
    print("done")


if __name__ == "__main__":
    sys.exit(main())
