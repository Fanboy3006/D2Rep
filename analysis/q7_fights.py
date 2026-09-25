# -*- coding: utf-8 -*-
"""q7_fights.py —— 复现游戏里的 **Fight Recap**（国服译作"战斗回放"）面板。

游戏侧的依据（不是猜的，来自游戏文件本身）
------------------------------------------
· 字符串：`resource/localization/dota_english.txt` / `dota_schinese.txt`
    `fight_recap_show`=显示战斗回放 ｜ `fight_recap_pause`=显示战斗回放时暂停游戏
    `UI_Fight_Recap_Terse`=团战简要回顾 ｜ `fight_recap_totals`=总计
    `fight_recap_gold`=金钱变化情况 ｜ `fight_recap_xp`=经验变化情况
    `fight_recap_dmg`=造成伤害 ｜ `fight_recap_heal`=总治疗量
    `fight_recap_abilites_used`=已使用的技能 ｜ `fight_recap_items_used`=已使用的物品
· 面板结构：`panorama/layout/hud/dota_hud_fightrecap.xml`
    每一段都是一行「天辉容器 ｜ 中间两队合计 ｜ 夜魇容器」，逐人一根横条/箭头
    （`{i:dmg_value}`、`{i:radiant_damage_done}` …）；技能段与物品段每侧 4 行
    「图标 + x{usage_count}」；死亡段带买活图标（`DeathBuybackIcon`）。

本模块做什么
------------
**自动检测团战**（`FIGHT_GAP` 秒内的英雄阵亡算同一波，至少 `MIN_DEATHS` 人），
再按上面的七段逐人聚合，产出紧凑载荷给页面渲染。

字段形态（★ 每个类别的"属于谁"在不同列，踩过坑）
------------------------------------------------
  damage / healing / ability：人在 **attacker**（技能键在 `inflictor`）
  gold   / xp               ：人在 **target**（`value` 需过 `timebase.gold_i32` 还原 int32 下溢）
  item（"已使用"）          ：`type='DotaCombatlogItem'`，人在 attacker、物品在 inflictor
                              （购买是 `DotaCombatlogPurchase`，**必须排除**，否则"买装备"会算成"用装备"）

做不到的一项（如实标注，不硬凑）
--------------------------------
**买活**：库里 `type='DotaCombatlogBuyback'` 的行没有英雄字段（attacker/target 都空，
`value_name` 是 `item_ward_dispenser` 之类、值只有个位数），是解析噪声；
`gold_reason` 的实测码表（见 `analysis/DATA_DICT.md`）里也没有买活项。
所以本面板**不画买活图标**。
"""
import collections
import re

# 团战窗口参数（owner 2026 定案：自动检测击杀聚集）
GAP = 12.0        # 相邻阵亡间隔 ≤ 该秒数 → 算同一波
MIN_DEATHS = 2    # 一波团战至少要有几名英雄阵亡
BACK = 20.0       # 窗口：首次阵亡往前
FWD = 8.0         # 窗口：末次阵亡往后

HERO = "npc_dota_hero_"


def _gold_i32(v):
    """gold.value 的 int32 下溢还原（唯一真相源在 timebase，这里做个不依赖导入的兜底）。"""
    try:
        import timebase
        return timebase.gold_i32(v)
    except Exception:
        v = float(v or 0)
        return v - (1 << 32) if v >= (1 << 31) else v


def _short(npc):
    return (npc or "").replace(HERO, "")


def _ab_key(raw):
    """技能/物品键名归一化：去掉 CDOTA_Ability_ 前缀，尽量变成可解析成图标的 snake 名。"""
    s = raw or "?"
    s = s.replace("CDOTA_Ability_", "").replace("CDOTA_Item_", "")
    if s.startswith("item_"):
        s = s[len("item_"):]
    if s.startswith("modifier_"):
        s = s.replace("modifier_", "")
    # CamelCase → snake（QueenOfPain → queen_of_pain）
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", s)
    return s.lower()


def detect_fights(con, horn_cle, t0, t1):
    """找出所有团战窗口（显示钟）。返回 [(lo, hi, [阵亡时刻…]), …]。

    规则：把英雄阵亡按时间排序，从前到后贪心成簇（相邻间隔 ≤ GAP 视为同一波），
    簇内阵亡数 ≥ MIN_DEATHS 才算一波；窗口 = 首次阵亡 − BACK ~ 末次阵亡 + FWD（夹在 [t0,t1] 内）。
    """
    deaths = []
    for r in con.execute(
        "SELECT t_cle, target, attacker FROM combat_log "
        "WHERE type_category='death' AND target LIKE ? ORDER BY t_cle", (HERO + "%",)
    ):
        d = float(r["t_cle"]) - horn_cle
        if t0 <= d <= t1:
            deaths.append((d, r["target"], r["attacker"]))
    out, i = [], 0
    while i < len(deaths):
        j = i
        while j + 1 < len(deaths) and deaths[j + 1][0] - deaths[j][0] <= GAP:
            j += 1
        grp = deaths[i:j + 1]
        if len(grp) >= MIN_DEATHS:
            lo = max(t0, grp[0][0] - BACK)
            hi = min(t1, grp[-1][0] + FWD)
            out.append((lo, hi, [g[0] for g in grp]))
        i = j + 1
    return out


def build_fights(con, players, horn_cle, t0, t1):
    """检测团战并逐段聚合。返回载荷用的紧凑列表（见模块 docstring 的字段说明）。

    载荷键（尽量短，控制单文件体积）：
      i    第几波（1 起）
      t0/t1 窗口（显示钟，秒）
      n    本波阵亡人数
      d    [[npc, 次数], …] 阵亡
      g/x  [[npc, 数值], …] 金钱 / 经验变化（只列非 0）
      dm/hl[[npc, 数值], …] 造成伤害 / 总治疗量（只列非 0）
      ab/it[[npc, [[键, 次数], …]], …] 已使用的技能 / 物品
      dba  [[npc, [[键, 伤害], …]], …] 按技能拆分的伤害（比游戏面板多的一层）
      tot  两队合计：{"g":[天辉,夜魇], "x":…, "dm":…, "hl":…, "d":[阵亡数…]}
    """
    team_of = {p["npc"]: p["team"] for p in players}
    hero_set = set(team_of)
    wins = detect_fights(con, horn_cle, t0, t1)
    fights = []
    for n, (lo, hi, dtimes) in enumerate(wins, 1):
        g = collections.Counter()
        x = collections.Counter()
        dm = collections.Counter()
        hl = collections.Counter()
        died = collections.Counter()
        ab = collections.defaultdict(collections.Counter)
        it = collections.defaultdict(collections.Counter)
        dba = collections.defaultdict(collections.Counter)
        for r in con.execute(
            "SELECT type_category c, type t, attacker a, target tg, value v, inflictor i, "
            "damage_source ds FROM combat_log WHERE t_cle BETWEEN ? AND ?",
            (lo + horn_cle, hi + horn_cle),
        ):
            c, a, tg, v = r["c"], r["a"], r["tg"], r["v"] or 0
            if c == "gold":
                if tg in hero_set:
                    g[tg] += _gold_i32(v)
            elif c == "xp":
                if tg in hero_set:
                    x[tg] += v
            elif c == "damage":
                if a in hero_set:
                    dm[a] += v
                    dba[a][_ab_key(r["i"] or r["ds"])] += v
            elif c == "healing":
                if a in hero_set:
                    hl[a] += v
            elif c == "ability":
                if a in hero_set:
                    ab[a][_ab_key(r["i"] or r["ds"])] += 1
            elif c == "item" and r["t"] == "DotaCombatlogItem":
                if a in hero_set:
                    it[a][_ab_key(r["i"] or r["ds"])] += 1
        for d, victim, _killer in [(t, v, k) for t, v, k in _deaths_in(con, horn_cle, lo, hi)]:
            if victim in hero_set:
                died[victim] += 1

        def pack(counter, nd=0):
            return [[k, nd and round(v, nd) or int(round(v))]
                    for k, v in counter.most_common() if v]

        def pack_map(m):
            out = []
            for npc, cnt in m.items():
                items = [[k, int(v)] for k, v in cnt.most_common() if v]
                if items:
                    out.append([npc, items])
            return out

        tot = {}
        for key, counter in (("g", g), ("x", x), ("dm", dm), ("hl", hl)):
            tot[key] = [int(round(sum(v for k, v in counter.items() if team_of[k] == 2))),
                        int(round(sum(v for k, v in counter.items() if team_of[k] == 3)))]
        tot["d"] = [sum(v for k, v in died.items() if team_of[k] == 2),
                    sum(v for k, v in died.items() if team_of[k] == 3)]
        # ★ `n` 用**窗口内**的阵亡数（不是簇内人数）：窗口比簇宽（前 20s/后 8s），
        #   两者不等。统一成窗口内人数，才能让"头部数字 == 面板里的阵亡段 == 时间轴标记"三者一致。
        fights.append({
            "i": n, "t0": round(lo, 1), "t1": round(hi, 1), "n": int(sum(died.values())),
            "d": pack(died), "g": pack(g), "x": pack(x), "dm": pack(dm), "hl": pack(hl),
            "ab": pack_map(ab), "it": pack_map(it), "dba": pack_map(dba), "tot": tot,
        })
    return fights


def _deaths_in(con, horn_cle, lo, hi):
    """窗口内的英雄阵亡（显示钟已换算好）。单独查一次，避免和上面的大循环混在一起。"""
    rows = []
    for r in con.execute(
        "SELECT t_cle, target, attacker FROM combat_log WHERE type_category='death' "
        "AND target LIKE ? AND t_cle BETWEEN ? AND ?", (HERO + "%", lo + horn_cle, hi + horn_cle)
    ):
        rows.append((float(r["t_cle"]) - horn_cle, r["target"], r["attacker"]))
    return rows


def names_used(fights):
    """本场所有团战里用到的技能/物品键（供构建期解析图标用）。"""
    s = set()
    for f in fights:
        for arr_key in ("ab", "it", "dba"):
            for _npc, items in f.get(arr_key) or []:
                for k, _v in items:
                    s.add(k)
    return sorted(s)


def pack(fights, players, with_dba=True, dba_top=6):
    """把逐段压成紧凑载荷：**英雄下标 + 技能键名下标**。

    为什么值得压：36 波 × 10 人 × 7 个数组，直接写 `npc_dota_hero_invoker` 这种 21 字符串
    会重复上千次（实测原始 143 KB）。英雄用下标（对应载荷里 `players` 的顺序）、
    技能/物品键也建一张表，体积掉到 ~1/4。
    `dba`（按技能拆分的伤害，游戏面板没有这层）每人只留前 `dba_top` 项；`with_dba=False` 整段不带（lite 版）。
    """
    hidx = {p["npc"]: i for i, p in enumerate(players)}
    keys = []
    kidx = {}

    def kid(k):
        if k not in kidx:
            kidx[k] = len(keys)
            keys.append(k)
        return kidx[k]

    def pack_map(arr, top=0):
        out = []
        for npc, items in arr or []:
            if npc not in hidx:
                continue
            items = sorted(items, key=lambda x: -x[1])[:top] if top else items
            items = [[kid(k), int(v)] for k, v in items if v]
            if items:
                out.append([hidx[npc], items])
        return out

    def pack_num(arr):
        return [[hidx[k], int(round(v))] for k, v in (arr or []) if k in hidx and v]

    def pack_deaths(arr):
        return [[hidx[k], int(v)] for k, v in (arr or []) if k in hidx and v]

    lst = []
    for f in fights:
        d = {"i": f["i"], "t0": f["t0"], "t1": f["t1"], "n": f["n"], "tot": f["tot"],
             "d": pack_deaths(f.get("d")), "g": pack_num(f.get("g")), "x": pack_num(f.get("x")),
             "dm": pack_num(f.get("dm")), "hl": pack_num(f.get("hl")),
             "ab": pack_map(f.get("ab")), "it": pack_map(f.get("it"))}
        if with_dba:
            d["dba"] = pack_map(f.get("dba"), top=dba_top)
        lst.append(d)
    return {"keys": keys, "list": lst}
