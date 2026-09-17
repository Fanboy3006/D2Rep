# -*- coding: utf-8 -*-
"""detail_icons.py — combat log 明细行的图标解析（英雄头像 / 技能 / 道具 / 普通攻击字形）。

为什么单独一个模块：Q7 右栏的 ±45s combat log 想给每行配上「技能图标 + 对方英雄图标」，
但明细里的名字是解析层原始名（`modifier_kez_echo_slash_slow` / `kez_echo_slash` / `tusk` /
`item_blood_grenade` / `普通攻击` …）。这里只做**一件事**：把名字映射到一张本地 PNG。

规则（从强到弱，命中即停）：
  1. 名字就是英雄短名（`tusk`）→ 该英雄官方卡面中心裁切方头像（**真有这个单位**）；
  2. `普通攻击` → 自绘 UI 字形 `assets/ui_icons/attack.png`（是符号，不是游戏数据；文案仍写"普通攻击"）；
  3. 去掉 `modifier_` / `item_` 前缀后查本地技能图标、道具图标；
  4. 再逐级去掉尾部 token（最多 3 个）后查：`modifier_kez_echo_slash_slow` → `kez_echo_slash`；
  5. 仍没有 → 允许联网时从 Steam 官方 CDN 取（dota_react abilities / items），**落盘缓存**，
     之后离线可用；每个候选名只尝试一次，结果（含失败）写进缓存，避免重复打网络。

诚实边界：图标是"技能/单位的官方图"，映射靠名字匹配（3/4 两条是启发式）。页面上**文字名一直保留**，
图标只是补充；解析不到就不画图标（留空），不猜、不用占位图冒充。
"""
import io
import json
import os
import re
import ssl
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AB_DIR = os.path.join(ROOT, "opendota_analysis", "assets", "ability_icons")
IT_DIR = os.path.join(ROOT, "opendota_analysis", "assets", "item_icons")
HERO_DIR = os.path.join(ROOT, "opendota_analysis", "assets", "hero_icons")
UI_DIR = os.path.join(ROOT, "opendota_analysis", "assets", "ui_icons")
TL_DIR = os.path.join(ROOT, "opendota_analysis", "assets", "tl_icons")
TINT = {2: (126, 231, 135), 3: (255, 140, 140)}
CACHE = os.path.join(ROOT, "analysis", "output_q7", "icon_fetch_cache.json")

CDN_AB = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/abilities/%s.png"
CDN_IT = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/%s.png"
UA = {"User-Agent": "Mozilla/5.0"}
SAVE_PX = 64          # 落盘尺寸（CD 面板用 48、战斗日志用 28，64 够用且省体积）


def _ls(d):
    try:
        return {f[:-4] for f in os.listdir(d) if f.endswith(".png")}
    except OSError:
        return set()


def _cache_load():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _cache_save(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, separators=(",", ":"))


def _fetch(url, dest, size=SAVE_PX, timeout=8.0):
    """取一张官方图 → 落盘（缩小到 SAVE_PX）。成功 True。"""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as r:
            raw = r.read()
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        if max(im.size) > size:
            im = im.resize((size, size), Image.LANCZOS)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        im.save(dest, "PNG", optimize=True)
        return True
    except Exception:
        return False


def _norm(nm):
    return re.sub(r"[^a-z0-9_]+", "_", str(nm).lower()).strip("_")


def variants(nm):
    """候选名，长的优先（精确 → 去前缀 → 去尾部数字 → 逐级去尾部 token）。"""
    nm = str(nm)
    out = []
    for pre in ("npc_dota_hero_", "npc_dota_", "modifier_", "item_", "unit_"):
        if nm.startswith(pre):
            out.append(nm[len(pre):])
    out.append(nm)
    for pre in ("unit_", "modifier_", "item_"):        # 组合前缀：modifier_item_xxx / unit_xxx_yyy
        for b0 in list(out):
            if b0.startswith(pre):
                out.append(b0[len(pre):])
    for b in list(out):                      # 召唤物带序号：lone_druid_bear1 → lone_druid_bear
        m = re.match(r"^(.*?)(\d+)$", b)
        if m and m.group(1):
            out.append(m.group(1))
    # 状态后缀：modifier_largo_song_speed_burst_slowres → largo_song_speed_burst（技能图可能存在）
    SUF = ("_slowres", "_slow_res", "_slow", "_debuff", "_buff", "_aura", "_effect", "_instance",
           "_stack", "_stacks", "_bonus", "_stun", "_crit", "_passive", "_dot", "_modifier",
           "_counter", "_charge", "_marker", "_active", "_hidden", "_lua", "_proc")
    for b in list(out):
        for s in SUF:
            if b.endswith(s) and len(b) > len(s) + 2:
                out.append(b[:-len(s)])
    for b in list(out):
        parts = b.split("_")
        for k in range(1, 4):
            if len(parts) - k >= 1:
                out.append("_".join(parts[:len(parts) - k]))
    seen, res = set(), []
    for v in out:
        v = _norm(v)
        if v and v not in seen:
            seen.add(v)
            res.append(v)
    return res


# ── 「类别字形」：官方 CDN **没有**小兵/中立/召唤的图（我实测 5 个候选路径全 404）——
#    下面这几个是我**自绘的 UI 符号**（`assets/ui_icons/`），只表示"属于哪一类单位/状态"；
#    页面上文字名照旧显示，不冒充游戏美术、也不编造信息。
GLYPH_RULES = (                     # 顺序即优先级
    ("siege", "unit_siege"),        # 攻城车（*_siege）
    ("flagbearer", "unit_flag"),    # 旗手
    ("ranged", "unit_ranged"),      # 远程小兵
    ("creep", "unit_creep"),        # 近战小兵（含 upgraded / mega 各种前缀）
    ("neutral_", "unit_neutral"),   # 中立生物
    ("miniboss", "unit_neutral"),   # 小野怪 / 大野怪
)


def glyph_for(nm):
    """类别字形（找不到返回 None）。只兜底"单位/引擎状态"，技能与道具永远走真实图标。"""
    if not nm:
        return None
    if nm.startswith("modifier_"):
        # 引擎 modifier（modifier_stunned / modifier_invoke_bonuses / modifier_tower_aura_bonus …）
        fp = os.path.join(UI_DIR, "status.png")
        return ("status", fp) if os.path.exists(fp) else None
    for key, g in GLYPH_RULES:
        if key in nm:
            fp = os.path.join(UI_DIR, g + ".png")
            return ("unit", fp) if os.path.exists(fp) else None
    # 其余没解析出来的基本是单位（召唤物/分身/宠物/野怪变体…）→ 召唤物字形
    fp = os.path.join(UI_DIR, "unit_summon.png")
    return ("unit", fp) if os.path.exists(fp) else None


def team_of(nm):
    """名字里带没带阵营（`goodguys` / `badguys`，注意小兵是 `creep_goodguys_melee` 这种**中缀**）
    → 2/3；用于给小兵字形上色。"""
    if not nm:
        return 0
    if "goodguys" in nm:
        return 2
    if "badguys" in nm:
        return 3
    return 0


def tint_glyph(path, team):
    """把小兵类白描字形按阵营上色（goodguys=天辉绿 / badguys=夜魇红），带落盘缓存。"""
    if team not in (2, 3) or not path:
        return None
    base = os.path.basename(path)[:-4]
    dest = os.path.join(UI_DIR, "%s_%s.png" % (base, "r" if team == 2 else "d"))
    if os.path.exists(dest):
        return dest
    try:
        from PIL import Image
        im = Image.open(path).convert("RGBA")
        out = Image.new("RGBA", im.size, TINT[team] + (255,))
        out.putalpha(im.getchannel("A"))
        out.resize((SAVE_PX, SAVE_PX), Image.LANCZOS).save(dest, "PNG", optimize=True)
        return dest
    except Exception:
        return None


def bld_icon(nm):
    """建筑/肉山名 → tl_icons 里的官方白描字形（建筑按 goodguys/badguys 上色）。找不到返回 None。"""
    if nm == "roshan" or nm == "npc_dota_roshan":
        fp = os.path.join(TL_DIR, "roshan.png")
        return ("roshan", fp) if os.path.exists(fp) else None
    if not nm or ("tower" not in nm and "rax" not in nm and "barracks" not in nm and "fort" not in nm):
        return None
    team = 2 if nm.startswith("goodguys") else (3 if nm.startswith("badguys") else 0)
    if team == 0:
        return None
    if "fort" in nm:
        key = "fort"
    elif "rax" in nm or "barracks" in nm:
        key = "rax_range" if ("range" in nm or "ranged" in nm) else "rax_melee"
    else:
        key = "tower"
    fp = os.path.join(TL_DIR, key + ".png")
    if not os.path.exists(fp):
        return None
    dest = os.path.join(UI_DIR, "bld_%s_%s.png" % (key, "r" if team == 2 else "d"))
    if not os.path.exists(dest):
        try:
            from PIL import Image
            im = Image.open(fp).convert("RGBA")
            out = Image.new("RGBA", im.size, TINT[team] + (255,))
            out.putalpha(im.getchannel("A"))
            out.resize((SAVE_PX, SAVE_PX), Image.LANCZOS).save(dest, "PNG", optimize=True)
        except Exception:
            return None
    return ("building", dest)


class Resolver(object):
    """带缓存的解析器：本地优先，允许联网时按"出现次数从多到少"补充下载。"""

    def __init__(self, allow_fetch=True, budget=500, verbose=False):
        self.allow_fetch = allow_fetch
        self.budget = budget
        self.verbose = verbose
        self.n_fetch = 0
        self.n_skip = 0
        self.cache = _cache_load()
        self.hero = _ls(HERO_DIR)
        self.ab = _ls(AB_DIR)
        self.it = _ls(IT_DIR)
        self.atk = os.path.join(UI_DIR, "attack.png")
        self.log = []          # [(name, kind, file or None)]

    # ---- 单名解析 ----
    def resolve(self, nm):
        if not nm:
            return None
        if nm in self.hero:
            return ("hero", os.path.join(HERO_DIR, nm + ".png"))
        if nm == "普通攻击":
            return ("atk", self.atk) if os.path.exists(self.atk) else None
        b = bld_icon(nm)
        if b:
            return b
        cands = variants(nm)
        for v in cands:                                   # 本地技能
            if v in self.ab:
                return ("ability", os.path.join(AB_DIR, v + ".png"))
        for v in cands:                                   # 本地道具
            if v in self.it:
                return ("item", os.path.join(IT_DIR, v + ".png"))
        for v in cands:                                   # 召唤物/幻象 → 所属英雄头像（启发式）
            if v in self.hero:
                return ("hero", os.path.join(HERO_DIR, v + ".png"))
        if not self.allow_fetch:
            return glyph_for(nm)          # 离线构建也要有兜底字形
        for v in cands:                                   # 联网（带缓存）
            key = "ab:" + v
            if key not in self.cache:
                if self.n_fetch >= self.budget:
                    self.n_skip += 1
                    continue
                self.n_fetch += 1
                dest = os.path.join(AB_DIR, v + ".png")
                ok = _fetch(CDN_AB % v, dest)
                self.cache[key] = bool(ok)
                if ok:
                    self.ab.add(v)
                    return ("ability", dest)
                key2 = "it:" + v
                dest2 = os.path.join(IT_DIR, v + ".png")
                self.n_fetch += 1
                ok2 = _fetch(CDN_IT % v, dest2)
                self.cache[key2] = bool(ok2)
                if ok2:
                    self.it.add(v)
                    return ("item", dest2)
            elif self.cache[key]:
                self.ab.add(v)
                return ("ability", os.path.join(AB_DIR, v + ".png"))
        # ★ 官方图实在没有 → 用自绘的"类别字形"兜底（小兵/中立/召唤/引擎状态）；仍不给占位假图。
        return glyph_for(nm)

    # ---- 一批名字（按次数排，先满足高频）----
    def prepare(self, names, counts=None):
        """names = DNAMES（有序）；counts = {idx: 出现次数}。返回 {idx: (kind, file)}。"""
        order = sorted(range(len(names)), key=lambda i: -(counts or {}).get(i, 0))
        out = {}
        for i in order:
            nm = names[i]
            r = self.resolve(nm)
            if r:
                out[i] = r
            self.log.append((nm, r[0] if r else None, r[1] if r else None))
        _cache_save(self.cache)
        return out

    def stats(self):
        from collections import Counter
        c = Counter(k for _, k, _ in self.log)
        return {"kinds": dict(c), "fetched": self.n_fetch, "skipped_over_budget": self.n_skip,
                "cache_size": len(self.cache)}


def b64_png(path, px=28, colors=None):
    """读 PNG → 缩放/量化 → base64（页面内嵌用）。"""
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    if px and max(im.size) > px:
        im = im.resize((px, px), Image.LANCZOS)
    buf = io.BytesIO()
    if colors:
        im.convert("RGB").quantize(colors=colors, method=Image.MEDIANCUT).save(buf, "PNG", optimize=True)
    else:
        im.save(buf, "PNG", optimize=True)
    import base64
    return base64.b64encode(buf.getvalue()).decode("ascii")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    mid = sys.argv[1] if len(sys.argv) > 1 else "8955197224"
    nofetch = "--no-fetch" in sys.argv
    with open(os.path.join(ROOT, "analysis", "output_q7", "q7_%s.json" % mid), encoding="utf-8") as f:
        dat = json.load(f)
    dn = dat.get("detail_names") or []
    cnt = {}
    for npc, rows in (dat.get("detail") or {}).items():
        for r in rows:
            if len(r) >= 4:
                cnt[r[2]] = cnt.get(r[2], 0) + 1
                cnt[r[3]] = cnt.get(r[3], 0) + 1
    t0 = time.time()
    rs = Resolver(allow_fetch=not nofetch)
    got = rs.prepare(dn, cnt)
    tot = sum(cnt.values())
    hit = sum(v for k, v in cnt.items() if k in got)
    print("名字 %d 个 / 出现 %d 次 → 解析到图标 %d 个，按次数覆盖 %.1f%%（用时 %.0fs）"
          % (len(dn), tot, len(got), 100.0 * hit / max(1, tot), time.time() - t0))
    print("统计:", rs.stats())
    miss = sorted((cnt[i], dn[i]) for i in cnt if i not in got)[::-1][:12]
    print("未命中最多的名字:", miss)
    rows = sorted(((cnt[i], dn[i]) for i in got), reverse=True)[:12]
    print("命中最多的名字:", [(n, rs.resolve(nm)[0]) for n, nm in rows])
