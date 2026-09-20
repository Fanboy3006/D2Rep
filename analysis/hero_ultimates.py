# -*- coding: utf-8 -*-
"""英雄大招映射（构建期资产，产出 opendota_analysis/assets/hero_ultimates.json）。

为什么需要它
------------
录像的数据库里，技能事件的 `properties` 只有 `hero / key / kind / phase / remaining`，
**没有槽位**；技能的键名（`CDOTA_Ability_QueenOfPain_SonicWave`）也不带"这是大招"的标记。
所以"哪个技能是大招"必须靠一份英雄→大招的对照表，这份表来自权威来源并缓存在仓库里，
构建时离线可用、可复现。

来源
----
Valve 的游戏文件，经 GitHub 镜像 `spirit-bear-productions/dota_vpk_updates`：

  · `scripts/npc/npc_heroes.txt`         → 已拆成 `#base` 分文件（下面的每英雄文件）
  · `scripts/npc/heroes/npc_dota_hero_*.txt` → 每英雄的官方槽位定义
  · `scripts/npc/items.txt`              → 道具（取 TP 卷轴的固定冷却）

抽取规则
--------
英雄文件末尾就是该英雄每个技能的**完整定义**，其中大招自己带标记：

    "antimage_mana_void"
    {
        "AbilityType"  "ABILITY_TYPE_ULTIMATE"
        …

所以判定用这个标记，而不是槽位位置 —— 槽位会骗人：烬火精灵的 `Ability6` 是空的、
改版过的英雄（如工程师、力丸）真正的大招也未必落在 `Ability6`。优先级：

  1. 既被标为 ULTIMATE、又出现在英雄 `Ability1..N` 槽位表里的 → 取槽位靠前者
     （少数英雄合法地有两个大招：凯的剑/匕两形态、工程师的神杖大招）；
  2. 只被标为 ULTIMATE 的；
  3. 兜底：槽位表里有有效 `Ability6` 就用它，否则用槽位表最后一个。

说明：槽位表取英雄块自身的 `Ability1..AbilityN`，**跳过 `"AbilityDraftAbilities"`**
（技能征召的另一套槽位，与真实槽位不同：烬火精灵那一组的 Ability4 是
`activate_fire_remnant`，而真大招 `fire_remnant` 在第 6 位）。

键名比对
--------
CD 键 `CDOTA_Ability_QueenOfPain_SonicWave` 按驼峰切成 `queen_of_pain_sonic_wave`，
而游戏文件写作 `queenofpain_sonic_wave`。因此比对一律**去掉下划线后全小写**，
两侧就一致了（`windrunner_focusfire` vs `windrunner_focus_fire` 同理）。
"""
import datetime
import json
import os
import re
import ssl
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET = os.path.join(ROOT, "opendota_analysis", "assets", "hero_ultimates.json")
CACHE_DIR = os.path.join(ROOT, "analysis", "output_q7", "vpk_cache")

MIRROR = ("https://raw.githubusercontent.com/spirit-bear-productions/"
          "dota_vpk_updates/main/scripts/npc/")
HERO_FILE = MIRROR + "heroes/%s.txt"
ITEMS_FILE = MIRROR + "items.txt"
HEROES_STUB = MIRROR + "npc_heroes.txt"

# 兜底：TP 卷轴的固定冷却（读不到 items.txt 时用；值来自 items.txt 的 AbilityCooldown）
TP_COOLDOWN_FALLBACK = 80.0

_PAIR = re.compile(r'"(Ability\d+)"\s+"([^"]*)"')


def _tokens(s):
    """Valve KV 分词：带引号的串、`{`、`}`；注释与 `#base`/`#include` 一并跳过。"""
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t\r\n":
            i += 1
            continue
        if (c == "/" and i + 1 < n and s[i + 1] == "/") or c == "#":
            j = s.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n:
                if s[j] == "\\" and j + 1 < n:
                    buf.append(s[j + 1]); j += 2; continue
                if s[j] == '"':
                    break
                buf.append(s[j]); j += 1
            yield ("str", "".join(buf))
            i = j + 1
            continue
        if c in "{}":
            yield (c, c); i += 1; continue
        if c == "[" or c == "]":      # 数组下标（talent 列表等）跳过
            i += 1; continue
        j = i
        while j < n and s[j] not in ' \t\r\n{}"':
            j += 1
        yield ("str", s[i:j])          # 裸值（数字等）
        i = j


def parse_kv(text):
    """把 Valve KV 文本解析成嵌套 dict（同键重复时后者覆盖，够用）。"""
    toks = list(_tokens(text))
    pos = 0

    def obj():
        nonlocal pos
        d = {}
        while pos < len(toks):
            k, v = toks[pos]
            if k == "}":
                pos += 1
                return d
            if k == "{":
                pos += 1
                continue
            pos += 1
            if pos >= len(toks):
                return d
            nk, nv = toks[pos]
            if nk == "{":
                pos += 1
                d[v] = obj()
            else:
                pos += 1
                d[v] = nv
        return d

    return obj()


def hero_slots(hero_block):
    """英雄块里的 Ability1..AbilityN（不含 AbilityDraftAbilities、不含天赋）。"""
    out = []
    for n in list(range(1, 30)):
        v = hero_block.get("Ability%d" % n)
        if isinstance(v, str) and v.strip():
            if v.startswith("special_bonus") or v.startswith("generic_"):
                continue
            out.append(v.strip())
    return out


def _collect_ultimates(node, out):
    """递归收集 `AbilityType == ABILITY_TYPE_ULTIMATE` 的技能（键名 -> 定义）。"""
    if not isinstance(node, dict):
        return out
    for k, v in node.items():
        if isinstance(v, dict):
            if v.get("AbilityType") == "ABILITY_TYPE_ULTIMATE":
                out[k] = v
            _collect_ultimates(v, out)
    return out


def ultimate_from_hero_file(text):
    """从单个英雄文件里取出大招键名；取不到返回 None。

    判定依据是 Valve 自己的 **`"AbilityType" "ABILITY_TYPE_ULTIMATE"` 标记**，它写在英雄块
    下 `"AbilityDefinitions"` 里每个技能的完整定义中，而不是靠槽位位置 —— 槽位会骗人：
    烬火精灵的 `Ability6` 是空的，改版过的英雄（工程师、力丸之类）真正的大招也未必落在
    `Ability6`。优先级：
      1) 既被标为 ULTIMATE、又出现在英雄 `Ability1..N` 槽位表里的 → 取槽位靠前者
         （少数英雄合法地有两个大招：凯的剑/匕两形态、工程师的神杖大招）；
      2) 只被标为 ULTIMATE 的；
      3) 兜底：槽位表里有有效 `Ability6` 就用它，否则用槽位表最后一个。
    """
    root = parse_kv(text)
    d = root.get("DOTAHeroes") if isinstance(root.get("DOTAHeroes"), dict) else root
    hero = None
    for k, v in d.items():
        if k.startswith("npc_dota_hero_") and isinstance(v, dict):
            hero = v
            break
    slots = hero_slots(hero) if hero else []
    defs = hero.get("AbilityDefinitions") if hero else None
    ults = _collect_ultimates(defs if isinstance(defs, dict) else (hero or {}), {})
    ults = [k for k in ults if not k.startswith("special_bonus") and "_empty" not in k
            and not k.startswith("generic_")]
    cand = [s for s in slots if s in ults]
    if cand:
        return cand[0]
    if ults:
        return ults[0]
    if slots:                      # 兜底：槽位表里的 Ability6，否则最后一个
        for n in ("Ability6",):
            v = (hero or {}).get(n)
            if isinstance(v, str) and v.strip() and not v.startswith("special_bonus"):
                return v.strip()
        return slots[-1]
    return None


def norm(s):
    """比对用归一化：去掉下划线与非字母数字后全小写。"""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# 游戏文件里的技能名与**录像 CD 事件里的键名**不一致的少数几处（同一个技能，改了名）。
# 左边＝游戏文件里的大招名，右边＝录像里出现的写法。来源：拿 700 场已解析库做覆盖率
# 交叉核对时发现的仅有几例（见 hero_ultimates.py 的说明）。
REPLAY_ALIASES = {
    "mirana_invis": "Mirana_MoonlightShadow",
    "monkey_king_wukongs_command": "MonkeyKing_FurArmy",
}

# 录像里的技能键统一带这个前缀
CD_PREFIX = "CDOTA_Ability_"


def ult_norms(asset=None):
    """{npc: {归一化后的大招键名, …}}；含录像侧别名，直接拿去比对 CD 键。

    比对时两侧都先去掉 `CDOTA_Ability_` 前缀、再去掉下划线小写 —— 因为有些技能
    在录像里写作 `Windrunner_FocusFire`（驼峰切出来是 windrunner_focus_fire），
    而游戏文件写作 `windrunner_focusfire`。
    """
    a = asset or load()
    out = {}
    for npc, ult in (a.get("heroes") or {}).items():
        names = {norm(ult)}
        alias = REPLAY_ALIASES.get(ult)
        if alias:
            names.add(norm(alias))
        out[npc] = names
    return out


def is_ult(npc, cd_key, table=None):
    """给定的 CD 键（如 CDOTA_Ability_Antimage_ManaVoid）是否是该英雄的大招。"""
    if not npc or not cd_key:
        return False
    k = cd_key[len(CD_PREFIX):] if cd_key.startswith(CD_PREFIX) else cd_key
    names = (table or ult_norms()).get(npc)
    return bool(names) and norm(k) in names


def mark_ult_flags(cd, players=None):
    """给 CD 载荷的 `keys[*]` 补上 `ult` 标记（已标过的跳过），返回本次补了几个。

    键 → 英雄的归属从 `ab[npc]` / `it[npc]` 反推（技能键名本身含英雄名，但改名的那两处
    反推不可靠，所以以"这名英雄的技能表里有这个键"为准）。
    """
    if not cd or not isinstance(cd, dict):
        return 0
    table = ult_norms()
    owner = {}
    for npc, arr in list((cd.get("ab") or {}).items()) + list((cd.get("it") or {}).items()):
        for e in arr or []:
            if e:
                owner.setdefault(e[0], npc)
    n = 0
    for k, info in (cd.get("keys") or {}).items():
        if not isinstance(info, dict) or "ult" in info:
            continue
        info["ult"] = is_ult(owner.get(k), k, table)
        n += 1
    return n


def _get(url, cache_name):
    """带磁盘缓存的 GET（缓存放 analysis/output_q7/vpk_cache，已被 .gitignore 覆盖）。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = os.path.join(CACHE_DIR, cache_name)
    if os.path.exists(p):
        return open(p, encoding="utf-8", errors="replace").read()
    req = urllib.request.Request(url, headers={"User-Agent": "q7-ultimates"})
    with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as r:
        t = r.read().decode("utf-8", errors="replace")
    open(p, "w", encoding="utf-8").write(t)
    return t


def hero_list():
    """npc 名清单：从 npc_heroes.txt 的 #base 行读，读不到就退回 hero_abilities.json。"""
    skip = {"npc_dota_hero_base", "npc_dota_hero_target_dummy"}
    try:
        stub = _get(HEROES_STUB, "npc_heroes.txt")
        names = re.findall(r'#base\s+"heroes/(npc_dota_hero_[a-z0-9_]+)\.txt"', stub)
        if names:
            return sorted(set(names) - skip)
    except Exception:
        pass
    p = os.path.join(ROOT, "analysis", "output_q7", "hero_abilities.json")
    if os.path.exists(p):
        return sorted(set(json.load(open(p, encoding="utf-8"))) - skip)
    return []


def tp_cooldown():
    """TP 卷轴的固定冷却（游戏文件的 AbilityCooldown）。"""
    try:
        t = _get(ITEMS_FILE, "items.txt")
        m = re.search(r'"item_tpscroll"', t)
        if m:
            seg = t[m.start():m.start() + 1600]
            cd = re.search(r'"AbilityCooldown"\s+"([0-9.]+)"', seg)
            if cd:
                return float(cd.group(1))
    except Exception:
        pass
    return TP_COOLDOWN_FALLBACK


def generate(write=True):
    """抓取并生成资产 JSON，返回该 dict。"""
    heroes = hero_list()
    if not heroes:
        raise RuntimeError("拿不到英雄清单（镜像与本机缓存都不可用）")
    out, missing = {}, []
    for npc in heroes:
        try:
            t = _get(HERO_FILE % npc, npc + ".txt")
        except Exception as e:
            missing.append((npc, "下载失败 %s" % str(e)[:40]))
            continue
        u = ultimate_from_hero_file(t)
        if u:
            out[npc] = u
        else:
            missing.append((npc, "文件里认不出大招"))
    asset = {
        "source": MIRROR,
        "note": ("判定依据是英雄文件里每个技能定义自带的 "
                 "\"AbilityType\" \"ABILITY_TYPE_ULTIMATE\" 标记；同时要求该技能出现在英雄的 "
                 "Ability1..N 槽位表里（跳过 AbilityDraftAbilities）。比对时去掉下划线后全小写。"),
        "fetched": datetime.date.today().isoformat(),
        "tp_cooldown": tp_cooldown(),
        "heroes": out,
    }
    if write:
        os.makedirs(os.path.dirname(ASSET), exist_ok=True)
        with open(ASSET, "w", encoding="utf-8") as f:
            json.dump(asset, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
    return asset, missing


def load():
    """读资产；不存在则现场生成（生成失败返回空表）。"""
    if not os.path.exists(ASSET):
        try:
            generate()
        except Exception as e:
            print("  大招映射不可用：%s" % str(e)[:80], file=sys.stderr)
            return {"heroes": {}, "tp_cooldown": TP_COOLDOWN_FALLBACK}
    return json.load(open(ASSET, encoding="utf-8"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asset, missing = generate()
    print("英雄总数：%d ｜ 认到大招：%d ｜ 异常：%d"
          % (len(hero_list()), len(asset["heroes"]), len(missing)))
    print("TP 卷轴固定冷却：%s 秒（读自 items.txt）" % asset["tp_cooldown"])
    print("产物：%s" % os.path.relpath(ASSET, ROOT))
    for npc, why in missing:
        print("  异常 %-30s %s" % (npc, why))
    for npc in ("npc_dota_hero_antimage", "npc_dota_hero_invoker", "npc_dota_hero_ember_spirit",
                "npc_dota_hero_queenofpain", "npc_dota_hero_windrunner"):
        print("  抽查 %-30s -> %s" % (npc, asset["heroes"].get(npc)))
