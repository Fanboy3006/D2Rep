#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_q7_html.py — Q7「全盘复现交互 UI（回放浏览器）」· 单文件 HTML 生成器。

输入：analysis/output_q7/q7_<match_id>.json（q7_replay.py 产物）
      analysis/output_q7/q7_winprob.json（q7_winprob.py 产物；状态胜率模型，可选）
产出：analysis/output_review/q7_replay_<match_id>.html（自包含，双击即开）

页面骨架与交互复用 Q5B/Q6 范式（STRATEGY/INTERACTIVE_MAP_PATTERN.md）：
  · Canvas 底图 + 滚轮缩放 + 拖拽平移（复用 w2p / w2pView / calibFromPx / clampView）
  · 底图 = analysis/output_review/_q5_map_annot.png（沿用原底图 + 官方标定常量）
  · 已继承的前端坑（§9）：capValue 的 DOM 字符串陷阱、onmousemove 的 px 作用域、draw() 重入锁

第一步（MVP）：地图 + 缩放/平移 + 双方 10 英雄头像 + 双时间轴 + 顶部经济/经验差 + 逐秒位置 + KDA/正反补表。
第二步（本版）：① 点英雄 → 右栏改该英雄 **±45s combat log**（4 toggle：给出/收到 modifier、造成/收到伤害；
                每行带「技能图标 + 对方英雄图标 + 本英雄头像」，见 detail_icons.py）
              ② 下方 **技能 CD**（三态：冷却中灰+剩余秒 / 未学未拥有 / 就绪；重点追踪 BKB / 刷新球 / TP）
              ③ 顶部 **状态胜率**（派生模型，绝不偷看结果）
"""

import argparse
import base64
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detail_icons as DI          # noqa: E402  明细行图标（英雄头像/技能/道具/建筑/普通攻击）
import hero_ultimates as HU        # noqa: E402  英雄 → 大招（头像框的"大招就绪"标记）

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q7DIR = os.path.join(ROOT, "analysis", "output_q7")
REVIEW = os.path.join(ROOT, "analysis", "output_review")
AB_ICON_DIR = os.path.join(ROOT, "opendota_analysis", "assets", "ability_icons")
ITEM_ICON_DIR = os.path.join(ROOT, "opendota_analysis", "assets", "item_icons")
WARD_ICON_DIR = os.path.join(ROOT, "opendota_analysis", "assets", "ward_icons")
TL_ICON_DIR = os.path.join(ROOT, "opendota_analysis", "assets", "tl_icons")
ICON_DIR = os.path.join(ROOT, "opendota_analysis", "assets", "hero_icons")
MAP_PNG = os.path.join(REVIEW, "_q5_map_annot.png")
WINPROB = os.path.join(Q7DIR, "q7_winprob.json")


def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def b64_png_opt(path, colors=64, size=None):
    """PNG → 64 色量化（体积约 1/3），把技能/道具/英雄图标塞进单文件；失败则原样内嵌。"""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGBA")
        if size and (im.width > size or im.height > size):
            im.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        im.convert("P", palette=Image.ADAPTIVE, colors=colors).save(buf, "PNG", optimize=True)
        data = buf.getvalue()
        if len(data) >= os.path.getsize(path):
            data = open(path, "rb").read()
        return base64.b64encode(data).decode("ascii")
    except Exception:
        return b64(path)


def square_avatar(path, size=26):
    """英雄图（128×72 卡片）→ 裁中心正方形 → 缩小。时间轴上的"阵亡英雄头像"用它。"""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        w, h = im.size
        s0 = min(w, h)
        left = (w - s0) // 2
        im = im.crop((left, 0, left + s0, s0)).resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return b64(path)


def tint_icon(path, rgb, size=26):
    """官方建筑图标是**白色字形 + alpha**（好/坏阵营文件完全相同）→ 按队伍上色。"""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGBA")
        out = Image.new("RGBA", im.size, rgb + (255,))
        out.putalpha(im.getchannel("A"))
        out = out.resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, "PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return b64(path)


def clean(s):
    if not s:
        return ""
    return "".join(ch if (ch.isprintable() and ch != "\ufffd") else "?" for ch in str(s))


def strip_comments(html):
    """剥掉产出 HTML 里的注释（HTML `<!-- -->` / CSS 与 JS 的 `/* */`、`//`）。

    为什么：模板里的注释是给维护者看的，但**产出文件是发给用户的成品** —— 里面不该留下
    "owner/方案①/Q5B"这类内部讨论痕迹。源代码模板保留注释，只在构建时剥掉。

    ★ 分段处理，避免误伤：
      · `<style>` 段**只**去 `/* */`（CSS 里 `url(data:image/png;base64,…)` 的 base64 含 `/`，
        若按 `//` 当行注释会把图片整个切掉 —— 第一版就踩了这个坑，页面从 9MB 掉到 6MB）；
      · `<script>` 段用"是否在字符串里"的状态机去 `/* */` 与 `//`（支持 ' " ` 与转义），
        并用"前一个非空字符"启发式区分正则字面量（`replace(/^npc_/, "")`）与除号；
      · 其余（HTML）段只去 `<!-- -->`。
    """
    import re as _re
    parts = _re.split(r"(?is)(<script\b[\s\S]*?</script>|<style\b[\s\S]*?</style>)", html)

    def _js(code):
        out, i, n, quote, prev_sig = [], 0, len(code), None, ""
        while i < n:
            ch, two = code[i], code[i:i + 2]
            if quote:
                out.append(ch)
                if ch == "\\" and i + 1 < n:
                    out.append(code[i + 1])
                    i += 2
                    continue
                if ch == quote:
                    quote = None
                i += 1
                continue
            if two == "/*":
                j = code.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if two == "//" and prev_sig not in ("(", ",", "=", ":", "[", "!", "&", "|", "?"):
                # ↑ 只有在"正则字面量可能出现的位置"才不当注释。**不能**把 `}` `;` 也算进去：
                #   行尾注释最常见的上下文就是 `}` / `;` 之后，把这两者列入白名单会漏掉一半注释
                #   （第一版就是这么漏的）。本页 JS 里没有以 `//` 开头的正则字面量。
                j = code.find("\n", i)
                if j < 0:
                    i = n
                else:
                    out.append("\n")
                    i = j + 1
                continue
            if ch in "\"'`":
                quote = ch
            out.append(ch)
            if not ch.isspace():
                prev_sig = ch
            i += 1
        return "".join(out)

    res = []
    for p in parts:
        if p[:7].lower() == "<script":
            head = p[:p.index(">") + 1]
            tail = p[-9:]
            res.append(head + _js(p[len(head):-9]) + tail)
        elif p[:6].lower() == "<style":
            res.append(_re.sub(r"/\*[\s\S]*?\*/", "", p))          # CSS：只去块注释
        else:
            res.append(_re.sub(r"<!--[\s\S]*?-->", "", p))          # HTML 注释
    return "".join(res)


def decimate_keep(a, step):
    """按 step 抽稀：每个窗口取**最后一个非空值**（窗口全空则保留 None）。

    比 a[::step] 稳：1Hz 采样偶有缺秒，正好抽到缺秒会让英雄在地图上"消失"几秒。
    """
    if step <= 1 or not a:
        return a
    out = []
    for i in range(0, len(a), step):
        v = None
        for j in range(i, min(i + step, len(a))):
            if a[j] is not None:
                v = a[j]
        out.append(v)
    return out


def decimate(a, step, fill=0):
    """按 step 秒抽稀（取该窗口内最后一个非空值）。"""
    if step <= 1 or not a:
        return a
    out = []
    for i in range(0, len(a), step):
        v = None
        for j in range(i, min(i + step, len(a))):
            if a[j] is not None:
                v = a[j]
        out.append(v if v is not None else fill)
    return out


def _fights_from_db(dat, lite):
    """老切片没有 `fights` 字段时，按 match_id 找到主库现算团战（与切片期同一份代码）。

    返回 (紧凑载荷, 用到的技能/物品名列表)；算不出来就返回 (空载荷, [])，页面显示"这一场没有团战数据"。
    """
    import glob
    import sqlite3
    import q7_fights as QF
    import timebase as tb
    mid = dat.get("match_id")
    # ★ 递归匹配：联赛库是 dems/db_full/<league>/<mid>.db，而私人录像多一层
    #   dems/db_full/local/<scope>/<mid>.db —— 少一层通配符就会漏掉本地场次（实测踩过）。
    cand = glob.glob(os.path.join(ROOT, "dems", "db_full", "**", "%s.db" % mid), recursive=True)
    if not cand:
        return {"keys": [], "list": []}, []
    con = sqlite3.connect("file:%s?mode=ro" % cand[0].replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    players = [{"npc": p["npc"], "team": p["team"], "short": p["short"]} for p in dat["players"]]
    try:
        horn = tb.Clock(con, str(mid)).horn_cle
        raw = QF.build_fights(con, players, horn, dat["t0"], dat["t1"])
    except Exception as e:
        print("  ⚠ 现算团战失败（%s）→ 战斗回顾为空" % str(e)[:70])
        con.close()
        return {"keys": [], "list": []}, []
    con.close()
    return QF.pack(raw, players, with_dba=not lite), QF.names_used(raw)


def build(mid, outdir, lite=False, step=3, fetch_icons=True):
    src = os.path.join(Q7DIR, "q7_%s.json" % mid)
    if lite:
        alt = os.path.join(Q7DIR, "lite", "q7_%s.json" % mid)
        if os.path.exists(alt):
            src = alt                      # lite 切片（不含明细）优先
    if not os.path.exists(src):
        raise SystemExit("缺 %s（先跑 python analysis/q7_replay.py %s%s）"
                         % (src, mid, " --lite" if lite else ""))
    with open(src, encoding="utf-8") as f:
        dat = json.load(f)

    # ---- 战斗回顾（复现游戏的 Fight Recap）：切片里没有就从数据库现算 ----
    #   团战由构建期自动识别（analysis/q7_fights.py 与 q7_replay.py 共用同一份代码）。
    #   为什么允许"现算"：老切片是以前生成的、没有 fights 字段，而重跑 60 场切片要一两个小时；
    #   这里按 match_id 找到主库 + timebase 的号角时刻，直接算出来（几秒/场）。
    fights = dat.get("fights")
    fight_names = dat.get("fight_names") or []
    if not fights or not (fights.get("list")):
        fights, fight_names = _fights_from_db(dat, lite)

    # ---- 大招标记 / TP 固定冷却：切片里没有就现补（老的切片缓存因此不必重跑）----
    #   标记写在 cd.keys[*].ult 上，页面据此给头像框画"大招就绪"色（见 hero_ultimates.py）。
    HU.mark_ult_flags(dat.get("cd"), dat.get("players"))
    if not dat.get("tpcool"):
        dat["tpcool"] = float((HU.load() or {}).get("tp_cooldown") or 80.0)

    # ---- 图标（短名 → data URI；缺失则该英雄退化为色块）----
    #   ★ 尺寸要够头像条用：头像框是 56px，而英雄图是 128×72 的横版卡片 —— 若按 64 缩
    #     （64×36），`object-fit:cover` 要放大 1.56 倍才铺满方框，脸会发虚。
    #     按原生 128 内嵌每张只多 ~4KB（10 个英雄 ≈ +39KB/页），换清晰度很值。
    icons = {}
    for p in dat["players"]:
        p["name"] = clean(p["name"])
        fp = os.path.join(ICON_DIR, p["short"] + ".png")
        if os.path.exists(fp):
            icons[p["short"]] = "data:image/png;base64," + b64_png_opt(fp, 48 if lite else 64,
                                                                    96 if lite else 128)

    # ---- 技能/道具图标（只内嵌本场 CD 数据里真正用到的，64 色量化）----
    cd = dat.get("cd") or {}
    ab_icons = {}
    for k, info in (cd.get("keys") or {}).items():
        base = info.get("icon")
        if not base or base in ab_icons:
            continue
        fp = os.path.join(AB_ICON_DIR, base + ".png")
        if os.path.exists(fp):
            ab_icons[base] = "data:image/png;base64," + b64_png_opt(fp, 64)

    # ---- 回城卷轴（TP）图标：头像框与地图上的 TP 徽标用它（一张 64×64 的道具图）----
    tp_icon = ""
    tp_fp = os.path.join(ITEM_ICON_DIR, "tpscroll.png")
    if os.path.exists(tp_fp):
        tp_icon = "data:image/png;base64," + b64_png_opt(tp_fp, 64)

    # ---- 战斗日志（±45s 明细）每行的小图标：名字 → CSS 类（一张 28px PNG）----
    #   英雄短名 → 方头像；技能/道具 → 官方图；建筑 → tl_icons 字形（按阵营上色）；
    #   "普通攻击" → 自绘 UI 字形；解析不到 → 该行不画图标（文字照旧）。
    #   只内嵌**本场明细里真正出现**的名字；联网取图带缓存（见 detail_icons.py），--no-fetch 可关。
    dc_css, dicons, dic_stat = [], [], None
    dnames_all = dat.get("detail_names") or []
    dnames = [] if lite else list(dnames_all)
    if dnames:
        cnt = {}
        for _npc, _rows in (dat.get("detail") or {}).items():
            for r in _rows:
                if len(r) >= 4:
                    cnt[r[2]] = cnt.get(r[2], 0) + 1
                    cnt[r[3]] = cnt.get(r[3], 0) + 1
        rs = DI.Resolver(allow_fetch=fetch_icons)
        got = rs.prepare(dnames, cnt)
        for i in range(len(dnames)):
            r = got.get(i)
            if not r:
                dicons.append("")
                continue
            kind, fp = r
            # 小兵类字形按阵营上色（goodguys_/badguys_ → 天辉绿 / 夜魇红），其余保持原色
            if kind == "unit":
                tm = DI.team_of(dnames[i])
                if tm:
                    fp = DI.tint_glyph(fp, tm) or fp
            key = "dc%d" % len(dc_css)
            try:
                dc_css.append(".%s{background-image:url(data:image/png;base64,%s)}"
                              % (key, DI.b64_png(fp, 28)))
            except Exception:
                dicons.append("")
                continue
            dicons.append(key)
        dic_stat = rs.stats()

    # ---- 战斗回顾里用到的技能/物品图标（**独立一套**，与上面的明细图标解耦）----
    #   为什么单独做：团战面板在 lite 版也要能用，而一场比赛团战里会用到 120~150 个不同的
    #   技能/物品 —— 按明细那套 28px 内嵌要 2.7KB/个（lite 页面直接 +450KB）。这里用
    #   **18px / 48 色**（约 0.62KB/个），显示尺寸只有 ~15px，清晰度够，总体 +80KB 左右。
    fight_icons = []
    fkeys = (fights or {}).get("keys") or []
    if fkeys:
        rsf = DI.Resolver(allow_fetch=fetch_icons)
        gotf = rsf.prepare(fkeys, {i: 1 for i in range(len(fkeys))})
        for i in range(len(fkeys)):
            r = gotf.get(i)
            if not r:
                fight_icons.append("")
                continue
            kind, fp = r
            if kind == "unit":
                tm = DI.team_of(fkeys[i])
                if tm:
                    fp = DI.tint_glyph(fp, tm) or fp
            key = "dc%d" % len(dc_css)
            try:
                dc_css.append(".%s{background-image:url(data:image/png;base64,%s)}"
                              % (key, DI.b64_png(fp, 18, 48)))
            except Exception:
                fight_icons.append("")
                continue
            fight_icons.append(key)
        dic_stat = rsf.stats()

    # ---- 本英雄头像（选中那个英雄，日志第 2 列用）----
    self_icons = {}
    for i, p in enumerate(dat["players"]):
        fp = os.path.join(ICON_DIR, p["short"] + ".png")
        if os.path.exists(fp):
            key = "sc%d" % i
            dc_css.append(".%s{background-image:url(data:image/png;base64,%s)}"
                          % (key, DI.b64_png(fp, 32)))
            self_icons[p["npc"]] = key

    # ---- 时间轴图标：阵亡英雄方形头像 + 建筑/肉山图标（按队伍上色，只内嵌本场用到的）----
    TINT = {2: (126, 231, 135), 3: (255, 140, 140)}
    iconsq, tlicons = {}, {}
    for e in dat.get("tl", []):
        ic = e[4] if len(e) > 4 else ""
        own = e[5] if len(e) > 5 else 0
        if ic.startswith("h:"):
            short = ic[2:]
            if short not in iconsq:
                fp = os.path.join(ICON_DIR, short + ".png")
                if os.path.exists(fp):
                    iconsq[short] = "data:image/png;base64," + square_avatar(fp, 52)
        else:
            k = ic if ic == "roshan" else (ic + ("_r" if own == 2 else "_d"))
            if k in tlicons:
                continue
            fp = os.path.join(TL_ICON_DIR, ic + ".png")
            if not os.path.exists(fp):
                continue
            if ic == "roshan":
                tlicons[k] = "data:image/png;base64," + b64_png_opt(fp, 64, 52)
            else:
                tlicons[k] = "data:image/png;base64," + tint_icon(fp, TINT.get(own, TINT[2]), 52)

    # ---- 眼位图标（官方 observer / truesight，各 ~1.4KB）----
    ward_icons = {}
    for k, fn in (("obs", "ward_observer.png"), ("sen", "ward_sentry.png")):
        fp = os.path.join(WARD_ICON_DIR, fn)
        if os.path.exists(fp):
            ward_icons[k] = "data:image/png;base64," + b64(fp)

    # ---- 状态胜率模型（q7_winprob.py 产物；缺失则页面显示"未拟合"）----
    wp = None
    if os.path.exists(WINPROB):
        try:
            wp = json.load(open(WINPROB, encoding="utf-8"))
        except Exception:
            wp = None

    m = dat["meta"]
    step = max(1, int(step)) if lite else 1
    estep = max(step, int(round(15 / step)) * step) if lite else 1
    if lite:
        # 逐秒级数据按 step 抽稀；t0/D 不变，JS 端用 step 索引（kOf 内部除以 step）
        for npc in list(dat["pos"].keys()):
            for k in ("x", "y"):
                dat["pos"][npc][k] = decimate_keep(dat["pos"][npc][k], step)
            for k in ("hp",):
                dat["pos"][npc][k] = dat["pos"][npc][k][::step]
        for npc in list((dat.get("hpm") or {}).keys()):
            dat["hpm"][npc] = dat["hpm"][npc][::step]
        # 经济/经验序列另用更粗的 estep（曲线平滑；estep 取 step 的整数倍，索引才对得上）
        estep = max(step, int(round(15 / step)) * step)
        for key in ("nw", "cg", "cx"):
            for npc in list(dat[key].keys()):
                dat[key][npc] = decimate_keep(dat[key][npc], estep)
        for key in ("nw", "cg", "cx"):
            dat["diff"][key] = dat["diff"][key][::estep]
    payload = {
        "mid": dat["match_id"],
        "t0": dat["t0"],
        "t1": dat["t1"],
        "D": dat["D"],
        "step": step,
        "estep": estep,
        "lite": bool(lite),
        "DP": (len(dat["pos"][dat["players"][0]["npc"]]["x"]) if dat.get("pos") else dat["D"]),
        "DE": (len(dat["diff"]["nw"]) if dat.get("diff") else dat["D"]),
        "players": dat["players"],
        "pos": dat["pos"],
        "hpm": dat.get("hpm", {}),
        "nw": dat["nw"],
        "cg": dat["cg"],
        "cx": dat["cx"],
        "diff": dat["diff"],
        "kills": dat["kills"],
        "events": dat["events"],
        "buildings": dat.get("buildings", []),
        "kda": dat["kda"],
        "detail": ({} if lite else dat.get("detail", {})),
        "dnames": dnames,
        "dicons": dicons,
        "fights": fights,
        "fighticons": fight_icons,
        "selficons": self_icons,
        "attr": (dat.get("attr_check") or []),
        "cd": cd,
        "tp": dat.get("tp", {}),
        "tpicon": tp_icon,
        "wp": wp,
        "tl": dat.get("tl", []),
        "wards": dat.get("wards", []),
        "smoke": dat.get("smoke", []),
        "smoked": dat.get("smoked", {}),
        "meta": m,
        "icons": icons,
        "abicons": ({} if lite else ab_icons),
        "wicons": ward_icons,
        "iconsq": iconsq,
        "tlicons": tlicons,
    }
    blob = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/")
    map_b64 = b64_png_opt(MAP_PNG, 64, (448 if lite else None)) if lite else b64(MAP_PNG)

    rname = m.get("radiant_team") or "天辉（未获取到队名）"
    dname = m.get("dire_team") or "夜魇（未获取到队名）"
    win = m.get("radiant_win")
    win_from = "战绩数据" if win is not None else None
    if win is None:
        # 个人/练习房录像没有外部战绩数据 → 用**录像本身**判定：远古（基地）被摧毁即分出胜负。
        # 时间轴里的基地事件 own = 被摧毁的基地属于哪一方：夜魇基地被毁 → 天辉胜。
        for e in dat.get("tl", []):
            if e[2] == 3 and e[5] in (2, 3):
                win = (e[5] == 3)
                win_from = "远古被摧毁"
                break
    if win is None:
        win_txt = "—"
    elif win_from == "远古被摧毁":
        win_txt = ("天辉胜" if win else "夜魇胜") + "（按远古被摧毁判定）"
    else:
        win_txt = "天辉胜" if win else "夜魇胜"
    league = m.get("league_id")

    html = HTML_TMPL
    for k, v in (
        ("@@BLOB@@", blob),
        ("@@MAP@@", "data:image/png;base64," + map_b64),
        ("@@DICSS@@", "".join(dc_css)),
        ("@@MID@@", str(dat["match_id"])),
        ("@@LEAGUE@@", str(league)),
        ("@@RNAME@@", rname),
        ("@@DNAME@@", dname),
        ("@@WIN@@", win_txt),
        ("@@DUR@@", dur(dat["t1"])),
        ("@@DB@@", m.get("db", "")),
        ("@@ENDRULE@@", m.get("end_source", "")),
        ("@@PAUSE@@", "%.1f" % (m.get("clock", {}).get("pause_sec_total") or 0)),
        ("@@PAUSE_TXT@@", ("本场基本没有暂停"
                           if abs(m.get("clock", {}).get("pause_sec_total") or 0) < 1
                           else "本场共暂停 %.0f 秒，已折算回游戏内时间"
                                % (m.get("clock", {}).get("pause_sec_total") or 0))),
        ("@@DELTA@@", "%.2f" % (m.get("clock", {}).get("delta_file") or 0)),
        ("@@ANCHORS@@", str(m.get("clock", {}).get("anchors", 0))),
        ("@@SRCJSON@@", "analysis/output_q7/q7_%s.json" % mid),
    ):
        html = html.replace(k, v)

    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, "q7_replay_%s%s.html" % (mid, "_lite" if lite else ""))
    # 产出文件是"成品"：剥掉所有注释（模板里的内部讨论痕迹不带出去）
    html = strip_comments(html)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote %s  size=%.2f MB  (icons %d%s)"
          % (os.path.relpath(out, ROOT), os.path.getsize(out) / 1e6, len(icons),
             ("" if not dic_stat else "，明细图标 %d 个名字 / 取图 %d 次"
              % (len([x for x in dicons if x]), dic_stat["fetched"]))))
    return out


def dur(sec):
    s = int(sec)
    return "%d:%02d" % (s // 60, s % 60)


HTML_TMPL = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>Dota 2 比赛回放 · @@MID@@</title>
<style>
:root{--bg:#0d1117;--pnl:#161b22;--pnl2:#21262d;--bd:#30363d;--fg:#e6edf3;--dim:#8b949e;
      --rad:#4aa564;--dire:#d24b4b;--acc:#1f6feb;--gold:#e3b341}
*{box-sizing:border-box}
html,body{height:100%}
/* 一屏布局：整页不做纵向滚动，右栏（combat log）自己滚。地图按剩余高度定尺寸（JS fitCanvas）。 */
body{font-family:-apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif;background:var(--bg);color:var(--fg);
     margin:0;padding:8px 12px;height:100vh;overflow-x:hidden;overflow-y:auto;
     display:flex;flex-direction:column;gap:8px}
h1{font-size:15px;margin:0;flex:0 0 auto}
.sub{color:var(--dim);font-size:11.5px;line-height:1.5;margin:0}
.sub b{color:#79c0ff}
.panel{background:var(--pnl);border:1px solid var(--bd);border-radius:8px;padding:8px 10px}
#timeline{margin-top:0;flex:0 0 auto}
#timeline .tlrow{margin:0}
/* ---------- 顶部数值条 ---------- */
#top{display:flex;flex-wrap:wrap;gap:8px;align-items:stretch;margin-bottom:0;flex:0 0 auto}
.stat{background:var(--pnl);border:1px solid var(--bd);border-radius:8px;padding:5px 10px;min-width:126px}
.stat .k{color:var(--dim);font-size:10px;letter-spacing:.3px;white-space:nowrap}
.stat .v{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2}
.stat .s{color:var(--dim);font-size:9.5px;line-height:1.35}
.stat.clock .v{font-size:22px;color:#fff}
.rad{color:var(--rad)}.dir{color:var(--dire)}.zero{color:var(--dim)}
#sparkwrap{background:var(--pnl);border:1px solid var(--bd);border-radius:8px;padding:4px 8px 0;flex:1;min-width:320px}
#spark{width:100%;height:48px;display:block;cursor:crosshair}
.sparkhint{color:var(--dim);font-size:10.5px;padding:0 2px 2px;display:flex;justify-content:space-between}
/* ---------- 主体：左＝地图+头像，右＝明细/combat log，各占一半宽 ---------- */
.wrap{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:stretch;
      flex:1 1 auto;min-height:0}
/* 左栏：地图在左、10 个头像条竖排在地图**右侧**（owner 方案①）—— 头像不再吃掉地图的高度 */
.left{min-width:0;min-height:0;display:flex;flex-direction:row;gap:6px;align-items:stretch}
.right{min-width:0;min-height:0;overflow:auto;overscroll-behavior:contain}
@media(max-width:1180px){
  body{height:auto;overflow:auto;display:block}
  .wrap{grid-template-columns:minmax(0,1fr);display:block}
  .left,.right{min-height:0;overflow:visible}
  #mapbox{min-height:0}
  #cv{width:100%!important;height:auto!important}
  #timeline{margin-bottom:10px}
}
#mapwrap{position:relative;background:var(--pnl);border:1px solid var(--bd);border-radius:8px;padding:6px;
         flex:1 1 auto;min-width:0;min-height:0;display:flex;flex-direction:column}
#mapbox{flex:1 1 auto;min-height:200px;display:flex;align-items:center;justify-content:center}
#cv{border:1px solid var(--bd);border-radius:6px;background:#0d1117;display:block;width:512px;height:512px;
    cursor:grab;touch-action:none}
.mapbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:11.5px;color:var(--dim);margin-top:5px}
.mapbar input[type=range]{vertical-align:middle;accent-color:var(--acc)}
.btn{background:var(--pnl2);border:1px solid var(--bd);border-radius:14px;padding:5px 12px;cursor:pointer;font-size:12px;color:var(--fg)}
.btn:hover{border-color:#4d5866}
.btn.active{background:var(--acc);border-color:var(--acc);color:#fff}
.btn.big{font-size:14px;padding:7px 16px;border-radius:16px}
/* ---------- 头像：竖排在地图右侧（天辉一列 ｜ 夜魇一列）---------- */
#avatars{display:flex;flex-direction:row;gap:4px;align-items:center;flex:0 0 auto;min-height:0}
.arow{display:flex;gap:5px;align-items:center;flex-wrap:wrap}
.tcol{display:flex;flex-direction:column;gap:4px;align-items:center}
.tcap{font-size:10px;line-height:12px;flex:0 0 auto}
.tdiv{width:1px;align-self:stretch;min-height:40px;background:#30363d;margin:0 2px;flex:0 0 auto}
/* 每名英雄一行：**大招长条 + TP 徽标 + 头像**三个横排，夜魇整排镜像（row-reverse）。
   ★ 长条与 TP 徽标都在头像**外面**（owner 定案）——互不遮挡，也不受头像圆角裁切影响，
     代价是这一列宽一些（owner 明确允许）。 */
.hrow{display:flex;align-items:center;gap:3px;flex:0 0 auto}
.hrow.t3{flex-direction:row-reverse}
/* 长条：颜色＝大招状态（黄＝就绪、暗灰＝冷却中、更暗＝无数据） */
.ultbar{width:5px;align-self:stretch;margin:7px 0;border-radius:3px;background:#30363d80;flex:0 0 auto}
.hrow.ult-ready .ultbar{background:#e3b341}
.hrow.ult-cool .ultbar{background:#6e7681}
.hrow.ult-none .ultbar{background:#30363d80}
/* TP 徽标：回城卷轴的**图标** + 冷却剩余秒（数字画在徽标内部） */
.tpbadge{position:relative;width:20px;height:20px;border-radius:5px;background:#0d1117;
         border:2px solid #0d1117;box-sizing:border-box;display:none;align-items:center;
         justify-content:center;flex:0 0 auto}
.tpbadge img{width:100%;height:100%;display:block;border-radius:3px}
.tpcd{position:absolute;inset:0;display:none;align-items:center;justify-content:center;
      border-radius:3px;background:#00000073;color:#fff;font-size:10px;font-weight:700;
      text-shadow:0 0 3px #000,0 1px 2px #000;font-variant-numeric:tabular-nums}
.hrow.tp-ok .tpbadge{display:flex}
.hrow.tp-cd .tpbadge{display:flex;filter:grayscale(.7) brightness(.62)}
.hrow.tp-cd .tpcd{display:flex}
.hrow.tp-none .tpbadge{display:none}
.hero{position:relative;width:56px;height:56px;border-radius:9px;overflow:hidden;border:2px solid #333;
      cursor:pointer;background:#222;flex:0 0 auto}
.hero img{width:100%;height:100%;object-fit:cover;display:block}
.hero .nm{position:absolute;left:0;right:0;bottom:0;font-size:9px;text-align:center;background:#000a;color:#ddd;
          overflow:hidden;white-space:nowrap;text-overflow:ellipsis;padding:1px 2px}
.hero.sel{box-shadow:0 0 0 2px var(--gold);border-color:var(--gold)}
/* 阵亡只置灰头像本身（长条与 TP 徽标在框外，不受影响） */
.hero.dead img{filter:grayscale(1) brightness(.5)}
.hero.t2{border-color:var(--rad)}.hero.t3{border-color:var(--dire)}
.hero .hpbar{position:absolute;left:0;top:0;height:3px;background:#3fb950}
/* 图例：一根长条 + 一个 TP 图标的样子 */
.fleg{font-size:11px;color:#8b949e;white-space:nowrap}
.fleg b{display:inline-block;width:5px;height:12px;border-radius:2px;vertical-align:-2px;margin:0 3px}
.fleg em{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-2px;margin:0 3px;
         border:1px solid #30363d}
/* 矮屏：头像缩一档，保证 5 个一列仍然塞得进左栏高度 */
@media(max-height:860px){.hero{width:48px;height:48px;border-radius:8px}.hero .nm{font-size:8px}}
@media(max-height:700px){.hero{width:40px;height:40px;border-radius:7px}.ultbar{width:4px}
  .tpbadge{width:18px;height:18px}.hero .nm{font-size:7px}}
/* ---------- 时间轴 ---------- */
#timeline{margin-top:0;position:relative}
/* 拖动进度条时跟着滑块走的时间气泡（"拖着看不到当前时间"的反馈） */
.seekbub{position:absolute;transform:translateX(-50%);background:#1f6feb;color:#fff;font-size:15px;
         font-weight:700;padding:2px 9px;border-radius:7px;pointer-events:none;white-space:nowrap;
         box-shadow:0 3px 10px #0008;opacity:0;transition:opacity .08s;z-index:9;
         font-variant-numeric:tabular-nums}
.seekbub.on{opacity:1}
.seekbub::after{content:"";position:absolute;left:50%;bottom:-5px;margin-left:-5px;width:0;height:0;
                border-left:6px solid transparent;border-right:6px solid transparent;border-top:5px solid #1f6feb}
.tlrow{display:flex;gap:8px;align-items:center;margin:0;flex-wrap:wrap}
.tlrow .lb{width:auto;font-size:11.5px;color:var(--dim);flex:0 0 auto}
.tlrow .sep{color:#30363d}
.tlrow #smallwrap{flex:0 1 320px;min-width:140px}
input[type=range]{width:100%;accent-color:var(--acc)}
#big{-webkit-appearance:none;appearance:none;height:16px;background:transparent}
#big::-webkit-slider-runnable-track{height:8px;background:#21262d;border:1px solid var(--bd);border-radius:5px}
#big::-webkit-slider-thumb{-webkit-appearance:none;width:12px;height:20px;margin-top:-7px;border-radius:3px;background:var(--acc);border:1px solid #fff3;cursor:pointer}
#small::-webkit-slider-thumb{cursor:pointer}
/* ── 时间轴：上下事件带（天辉有利在上、夜魇有利在下）── */
.tlaxis{position:relative;margin-top:2px}
.evlane{position:relative;height:65px}
.evlane .evt{position:absolute;border-radius:1px}
.evlane .evt.b{width:3px}
.tlaxis .evhint{font-size:10px;color:#8b949e;line-height:13px;display:flex;gap:8px;align-items:baseline}
.tlaxis .evhint.up{color:#8b949e}
.tlaxis .evhint .lg{color:#8b949e;font-size:10px}
.tlaxis .evhint .tmax{margin-left:auto;color:#8b949e}
/* 事件标记：图标（+可选 mm:ss）；上方=天辉有利、下方=夜魇有利 */
.evm{position:absolute;transform:translateX(-50%);cursor:pointer;display:flex;flex-direction:column;
     align-items:center;gap:0;padding:1px 2px;border-radius:4px;border:1px solid transparent}
.evm:hover{border-color:#fff;background:#1f6feb66;z-index:6}
.evm .ico{width:26px;height:26px;border-radius:50%;display:block;object-fit:cover;background:#0d1117;
          border:2px solid #555}
.evm.r2 .ico{border-color:#4aa564}
.evm.r3 .ico{border-color:#d24b4b}
.evm.r0 .ico{border-color:#8b949e}
.evm.bld .ico{border-radius:4px}
.evm .t{font-size:9.5px;line-height:11px;color:#a9b1ba;font-variant-numeric:tabular-nums;white-space:nowrap}
.evm.near{background:#e3b34140;border-color:#e3b341}
.evm.near .t{color:#fff}
.evm.near .ico{border-color:#e3b341}
.axrow{position:relative}
#big{width:100%}
#smallwrap{position:relative}
#scenter{position:absolute;left:50%;top:-1px;width:1px;height:14px;background:#8b949e;opacity:.6;pointer-events:none}
.smalllbl{font-size:10.5px;color:var(--dim);white-space:nowrap}
.nowlbl{font-size:11.5px;color:#c9d1d9;font-variant-numeric:tabular-nums;white-space:nowrap}
.tip{color:var(--dim);font-size:11px;line-height:1.7}
.tip b{color:#79c0ff}
/* ---------- 右表 ---------- */
table{border-collapse:collapse;font-size:12px;width:100%;margin-top:6px}
th,td{padding:4px 6px;border-bottom:1px solid #21262d;text-align:right;white-space:nowrap}
th{background:var(--pnl2);position:sticky;top:0}
td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){text-align:left}
tr.hov{background:#1f6feb26}
tr.selrow{background:#e3b34122;outline:1px solid var(--gold)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}
.dr{color:var(--rad)}.dd{color:var(--dire)}
.todo{border:1px dashed #d29922;border-radius:6px;padding:6px 8px;color:#d29922;font-size:11px;line-height:1.6;margin-top:8px}
/* ---------- 第二步：±45s 明细（带图标）+ 技能 CD ---------- */
.tglrow{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0;font-size:12px}
.tglrow .toggle{padding:3px 8px;font-size:11px}
.dsum{background:#21262d;border:1px solid var(--bd);border-radius:6px;padding:6px 8px;font-size:12px;
      line-height:1.7;margin:4px 0}
.dsum b{color:#79c0ff}
.dwrap{max-height:34vh;overflow:auto;border:1px solid var(--bd);border-radius:6px}
#dtbl{margin:0;font-size:11.5px;table-layout:auto}
#dtbl th{background:#21262d;font-size:11px}
#dtbl td{padding:2px 5px;border-bottom:1px solid #1c2128}
#dtbl tbody tr{height:24px}          /* 虚拟滚动按固定行高算（±45s 窗口可能上千行） */
#dtbl tbody tr.sp td{border:0;padding:0}
.chip{display:inline-block;padding:0 5px;border-radius:8px;font-size:10px;line-height:15px;white-space:nowrap}
.c0{background:#2d4f6b;color:#bcd9f2}.c1{background:#4a3a5c;color:#dcccf0}
.c2{background:#5c2f2f;color:#f5c6c6}.c3{background:#5c4a1f;color:#f0dda6}
.rownow{background:#e3b34122;outline:1px solid #e3b34155}
.rowpast{color:#c9d1d9}.rowfut{color:#7d8590}
.dfold{color:#8b949e}
.nm{font-family:ui-monospace,Consolas,monospace}
.cdhead{font-size:12px;color:#79c0ff;margin:10px 0 4px;font-weight:700}
#cdboard{display:flex;flex-wrap:wrap;gap:6px}
.cd{width:60px;text-align:center;font-size:9.5px;color:#c9d1d9;position:relative}
.cd .box{width:48px;height:48px;margin:0 auto;border-radius:6px;border:2px solid #444;overflow:hidden;
         position:relative;background:#15181d;display:flex;align-items:center;justify-content:center}
.cd .box img{width:100%;height:100%;object-fit:cover;display:block}
.cd .box .fb{font-size:9px;padding:2px;line-height:1.1;color:#9aa4af;word-break:break-all}
.cd .rem{position:absolute;inset:0;background:rgba(0,0,0,.62);color:#fff;font-size:15px;font-weight:700;
         display:flex;align-items:center;justify-content:center;font-variant-numeric:tabular-nums}
.cd.lock .box{filter:grayscale(1) brightness(.45);border-color:#333}
.cd.lock .box::after{content:"🔒";position:absolute;right:1px;bottom:0;font-size:10px}
.cd.cool .box{border-color:#484f58}
.cd.ready .box{border-color:#3fb950}
.cd.track .box{border-color:#e3b341;border-width:3px}
.cd .cap{margin-top:1px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.cd .st{color:#8b949e}
/* 大招：左上角一个「大」角标把它和普通技能区分开 */
.cd.ult .box::after{content:"大";position:absolute;left:-2px;top:-2px;font-size:9px;line-height:12px;
                    padding:0 3px;border-radius:4px;background:#e3b341;color:#1b1b1b;font-weight:700}
.kv{font-size:12px;color:var(--dim);line-height:1.8}
.kv b{color:var(--fg)}
details{margin-top:8px;font-size:12px;color:var(--dim)}
details summary{cursor:pointer;color:#79c0ff}
details p{line-height:1.75;margin:6px 0}
code{background:#21262d;padding:1px 4px;border-radius:3px;font-size:11px}
/* ---------- combat log 行内图标（背景图由构建脚本注入到独立的 style 块，按名字一类一张）---------- */
.di{display:inline-block;width:22px;height:22px;background-size:cover;background-position:center;
    background-color:#15181d;border:1px solid #30363d;border-radius:4px;vertical-align:middle;flex:0 0 auto}
.di.self{border-radius:50%;border-color:#8b949e}
.di.big{width:26px;height:26px}
.di.rci{width:16px;height:16px;border-radius:3px;vertical-align:-3px}   /* 战斗回顾里的技能/物品图标 */
.dirow{display:flex;align-items:center;gap:6px}
.dirow .txt{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#dtbl td.dc{text-align:center;width:1%;padding-left:4px;padding-right:4px}
#dtbl td.dv{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
#dtbl td.do{white-space:nowrap;max-width:170px}
#dtbl td.do .txt{display:inline-block;max-width:120px;overflow:hidden;text-overflow:ellipsis;
                 vertical-align:middle}
#dtbl td.dn2{white-space:nowrap;max-width:330px}
/* ---------- 战斗回顾（复现游戏里的 Fight Recap 面板；口径见 analysis/q7_fights.py）----------
   游戏侧：每一段都是「天辉容器 ｜ 中间两队合计 ｜ 夜魇容器」，逐人一根条；
   技能/物品段是「图标 + x次数」；死亡段是头像。这里照同一套结构做。 */
#fightLane{position:relative;height:14px;margin-top:1px}
#fightLane .fbar{position:absolute;top:2px;height:10px;border-radius:3px;box-sizing:border-box;
                 background:#39414f;border:1px solid #5b667899;cursor:pointer}
#fightLane .fbar:hover{background:#4a5568;border-color:#8b949e}
#fightLane .fbar.on{background:#e3b341;border-color:#ffe08a}
#fightLane .fbar b{position:absolute;left:50%;top:-2px;transform:translateX(-50%);
                   font-size:9px;font-weight:700;color:#c9d1d9}
#fightLane .fbar.on b{color:#1b1b1b}
#paneRecap .rcbar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0;font-size:12px}
#paneRecap .rcseg{border-top:1px solid #21262d;padding:5px 0 3px}
#paneRecap .rchead{font-size:11.5px;color:#79c0ff;margin-bottom:3px}
#paneRecap .rchead span{color:#8b949e;font-weight:400}
.rcbody3{display:flex;align-items:flex-start;gap:6px}
.rcside{flex:1 1 0;min-width:0}
.rcside.dire .rcent{flex-direction:row-reverse}
.rcmid{flex:0 0 104px;text-align:center;font-size:11px;color:#c9d1d9;
       font-variant-numeric:tabular-nums;line-height:15px;padding-top:1px}
.rcmid .d{color:#8b949e}
.rcent{display:flex;align-items:center;gap:4px;font-size:11px;line-height:16px;margin-bottom:1px}
.rcent img.av{width:15px;height:15px;border-radius:3px;flex:0 0 auto;object-fit:cover}
.rcent .bar{height:8px;border-radius:2px;flex:0 0 auto;background:#4a5568}
.rcent.t2 .bar{background:#4aa564}.rcent.t3 .bar{background:#d24b4b}
.rcent .val{font-variant-numeric:tabular-nums;flex:0 0 auto;min-width:44px;text-align:right;color:#c9d1d9}
.rcent .val.pos{color:#3fb950}.rcent .val.neg{color:#f85149}
.rcdie{display:inline-flex;align-items:center;gap:4px;margin:0 8px 2px 0;font-size:11px}
.rcdie img{width:18px;height:18px;border-radius:4px}
.rcdie .x{color:#f85149;font-weight:700}
.rcico{display:inline-flex;align-items:center;gap:3px;margin:0 7px 2px 0;font-size:10px;color:#8b949e}
.rchero{display:flex;align-items:flex-start;gap:4px;margin-bottom:2px}
.rchero img.av{width:15px;height:15px;border-radius:3px;flex:0 0 auto;margin-top:3px;object-fit:cover}
.rchero .list{min-width:0;line-height:15px}
.rchero .list .k{color:#8b949e}
.rcdba{margin-top:3px;font-size:10.5px;color:#8b949e;line-height:1.6}
</style>
<style id="dicss">@@DICSS@@</style>
</head><body>

<h1>Dota 2 比赛回放浏览器
  <span class="sub" style="font-weight:400"><b>@@RNAME@@</b> vs <b>@@DNAME@@</b> ｜ @@WIN@@ ｜ 时长 @@DUR@@
  ｜ 比赛编号 <span id="mid">@@MID@@</span></span>
  <span class="sub" style="font-weight:400">　怎么用：<b>拖时间轴</b>或点 <b>▶ 播放</b> 看回放；<b>点地图上的英雄</b>（或右下表格任一行）→ 右栏看它的战斗记录与技能冷却；时间轴图标可直接点跳转。</span></h1>

<div id="top">
  <div class="stat clock"><div class="k">当前时刻</div><div class="v" id="vClock">0:00</div>
    <div class="s" id="vPhase">—</div></div>
  <div class="stat"><div class="k">经济差（净值）</div><div class="v" id="vNw">—</div>
    <div class="s">天辉 − 夜魇 ｜ 两队总资产之差（现金 + 装备），最直观的经济形势</div></div>
  <div class="stat"><div class="k">经济差（累计获取）</div><div class="v" id="vCg">—</div>
    <div class="s">天辉 − 夜魇 ｜ 从击杀、补刀等<b>赚到的</b>金币之差（已扣阵亡损失，不扣买装备的支出）</div></div>
  <div class="stat"><div class="k">经验差</div><div class="v" id="vCx">—</div>
    <div class="s">天辉 − 夜魇 ｜ 从战斗记录累加得到的经验差，可作等级形势参考</div></div>
  <div class="stat"><div class="k">当前胜率（天辉）</div><div class="v" id="vWp">—</div>
    <div class="s" id="vWpNote">—</div></div>
  <div id="sparkwrap">
    <canvas id="spark" width="1200" height="78"></canvas>
    <div class="sparkhint"><span>全场走势（点一下或拖动，可直接跳到那一刻）</span>
      <span style="display:flex;gap:6px;align-items:center">
        <span>显示</span>
        <button class="btn ebtn active" data-e="nw" onclick="setEntDiff('nw')">净值差</button>
        <button class="btn ebtn" data-e="cg" onclick="setEntDiff('cg')">累计金币差</button>
        <button class="btn ebtn" data-e="cx" onclick="setEntDiff('cx')">经验差</button>
        <b id="spTitle" style="margin-left:6px"></b>
      </span></div>
  </div>
</div>

<div class="panel" id="timeline">
    <div class="tlrow">
      <button class="btn big" id="play">▶ 播放</button>
      <button class="btn" onclick="step(-5)">« 5s</button>
      <button class="btn" onclick="step(5)">5s »</button>
      <button class="btn spd active" data-s="1" onclick="setSpeed(1)">1×</button>
      <button class="btn spd" data-s="2" onclick="setSpeed(2)">2×</button>
      <button class="btn spd" data-s="4" onclick="setSpeed(4)">4×</button>
      <span class="sep">｜</span>
      <label class="toggle"><input type="checkbox" id="showTL" onchange="buildTimelineEvents()"> 时间戳文字</label>
      <label class="toggle"><input type="checkbox" id="tlBld" onchange="buildTimelineEvents()"> 只标建筑/肉山</label>
      <span class="sep">｜</span>
      <span class="lb">±60s</span>
      <div id="smallwrap">
        <input type="range" id="small" min="-60" max="60" value="0" step="0.5">
        <div id="scenter"></div>
      </div>
      <b class="nowlbl" id="biglabel">—</b>
      <span class="smalllbl" id="smalllabel"></span>
      <span class="tip" style="margin-left:auto">空格=播放/暂停 ｜ ←→=±5s ｜ 点图标=跳到该时刻</span>
    </div>

    <div class="tlaxis">
      <div class="evlane" id="evUp"></div>
      <div class="axrow">
        <input type="range" id="big" min="0" max="1" value="0" step="0.5">
      </div>
      <div id="fightLane"></div>
      <div class="evlane" id="evDn"></div>
      <div class="evhint">▲ 上＝对<b class="dr">天辉</b>有利 ｜ ▼ 下＝对<b class="dd">夜魇</b>有利 ｜
        图标＝阵亡英雄头像 / 被毁的塔·兵营·基地·肉山 ｜ 描边色＝它属于哪一方（绿天辉·红夜魇·灰无主肉山）｜
        点图标跳到该时刻<span class="tmax">大时间轴：全场 0:00 → @@DUR@@</span></div>
    </div>
    <div class="seekbub" id="seekbub">0:00</div>
  </div>

<div class="wrap">
<div class="left">
  <div id="mapwrap">
   <div id="mapbox">
    <canvas id="cv" width="1024" height="1024"></canvas>
   </div>
    <div class="mapbar">
      <span class="lbl">底图透明度</span><input id="mop" type="range" min="0" max="100" value="55" style="width:120px">
      <span id="moppct">55%</span>
      <span class="sep">｜</span>
      <button class="btn" onclick="resetZoom()">↺ 全图</button>
      <button class="btn" onclick="clearSel()">取消选中</button>
      <span class="sep">｜</span>
      <button class="btn" id="btnRecap" onclick="openRecap()">战斗回顾</button>
      <span class="sep">｜</span>
      <span class="lbl">头像框</span>
      <span class="fleg" id="fleg"><b style="background:#e3b341"></b>大招就绪<b style="background:#6e7681"></b>冷却中
        （天辉在头像左侧 ｜ 夜魇在右侧）　<em style="background:#0d1117"></em>紧挨着长条的 TP 图标：亮＝可用、带数字＝冷却秒数</span>
      <span class="sep">｜</span>
      <span id="mapInfo">滚轮缩放 · 拖拽平移 · 点英雄圆点选中</span>
      <label style="margin-left:auto"><input type="checkbox" id="showBld" checked> 建筑</label>
      <label><input type="checkbox" id="showRoute" checked> 轨迹(最近40s)</label>
      <label><input type="checkbox" id="showName" checked> 名字</label>
      <label><input type="checkbox" id="showWard" checked> 眼位</label>
      <label><input type="checkbox" id="showSmoke" checked> 烟雾</label>
      <span id="wardCount" style="color:#8b949e"></span>
    </div>
  </div>

  <div id="avatars"></div>
</div><!-- /.left -->

<div class="right panel">
  <div id="paneList">
    <div class="kv" id="selinfo">双方 <b>10 名英雄</b>的 K/D/A、正补/反补，以及<b>当前时刻</b>各自的净值、累计金币、经验与血量</div>
    <div style="overflow:visible">
    <table id="tbl"><thead><tr>
      <th>英雄</th><th>队</th><th>K</th><th>D</th><th>A</th><th>正补</th><th>反补</th>
      <th>净值@t</th><th>累计金币@t</th><th>经验@t</th><th>HP</th>
    </tr></thead><tbody></tbody></table>
    </div>
    <div class="tip" id="liteNote" style="display:none"><b>这是精简版页面</b>：为了减小体积，
      地图上的逐秒数据每 <b id="liteStep">3</b> 秒取一格，并且<b>不含逐条战斗记录与技能图标</b>；
      地图、表格、胜率、眼位、烟雾、技能冷却都在。想看逐条战斗记录，请打开同一文件夹里的<b>完整版</b>页面。</div>
    <div class="tip"><b>点任意英雄</b>（表格行 / 地图上方的头像 / 地图上的标记）→ 这里换成这名英雄的
      <b>战斗记录</b>（当前时刻前后各 45 秒）和<b>技能冷却</b>。</div>
  </div>

  <div id="paneRecap" style="display:none">
    <div class="rcbar">
      <button class="btn" onclick="recapJump(-1)">← 上一波</button>
      <button class="btn" onclick="recapJump(1)">下一波 →</button>
      <label class="toggle"><input type="checkbox" id="tcfollow" checked onchange="renderRecap()"> 跟随当前时刻</label>
      <button class="btn" onclick="closeRecap()">✕ 收起</button>
      <span class="tip" style="margin-left:auto">团战由录像自动识别（12 秒内 ≥2 名英雄阵亡算一波）</span>
    </div>
    <div class="kv" id="recapHead">—</div>
    <div id="recapBody"></div>
  </div>

  <div id="paneHero" style="display:none">
    <div class="kv" id="herotop">—</div>
    <div class="tglrow">
      <button class="btn" onclick="clearSel()">↩ 返回 10 英雄表</button>
      <span class="sep"></span>
      <label class="toggle"><input type="checkbox" id="tc0" checked onchange="renderDetail()">施加的状态</label>
      <label class="toggle"><input type="checkbox" id="tc1" checked onchange="renderDetail()">受到的状态</label>
      <label class="toggle"><input type="checkbox" id="tc2" checked onchange="renderDetail()">造成的伤害</label>
      <label class="toggle"><input type="checkbox" id="tc3" checked onchange="renderDetail()">受到的伤害</label>
      <label class="toggle"><input type="checkbox" id="tfold" checked onchange="renderDetail()">合并连续相同项</label>
      <label class="toggle" title="只隐藏“对手是小兵 / 中立生物 / 召唤物 / 肉山”的行；英雄与塔·兵营·基地照常显示"><input type="checkbox" id="tnc" onchange="renderDetail()">隐藏小兵/中立/召唤</label>
      <label class="toggle" title="显示还没学习、或者没买到的技能与道具栏位"><input type="checkbox" id="tcdall" onchange="renderCD()">显示未学/未拥有</label>
    </div>
    <div class="dsum" id="dsum">—</div>
    <div class="dwrap" id="dwrap"><table id="dtbl"><thead><tr>
      <th>时刻</th><th>本英雄</th><th>技能 / 事件</th><th>数值</th><th>对象</th>
    </tr></thead><tbody></tbody></table></div>

    <div class="cdhead">技能与道具冷却（灰 = 冷却中并显示剩余秒 ｜ 锁 = 未学习 / 未拥有 ｜ 绿框 = 可用）</div>
    <div id="cdboard"></div>
    <div class="tip" id="cdnote"></div>
  </div>

  <div class="todo" id="todobox">这个页面能做什么：<b>看回放</b>（地图上 10 个人的走位、血量、装备与经济）·
    <b>看局势</b>（经济差 / 经验差 / 当前胜率 / 全场走势）·
    <b>看大事件</b>（击杀、推塔、肉山都在时间轴上）·
    <b>看细节</b>（点某个英雄，查他每一秒在做什么、技能什么时候冷却好）· <b>看视野</b>（眼位与烟雾）。</div>

  <details open><summary>使用说明与数据说明（第一次用建议先看这里）</summary>
    <p><b>① 时间轴怎么读</b>：横轴是比赛时间，<b>0:00 = 号角响起</b>（出兵前 90 秒是选人/出门期，所以横轴左侧是负时间）。
      上方那条是<b>全场进度</b>，下方那条是<b>±60 秒微调</b>：拖微调条时画面实时跟着走，松手后全场进度推进、微调条回到中间。
      <b>拖任意一条时间轴时，滑块上方会出现一个蓝色时间气泡</b>，跟着你走、显示当前游戏时间，松手后消失。
      快捷键：<b>空格</b>=播放/暂停，<b>← →</b>=前后 5 秒。速度可切 1×/2×/4×。</p>
    <p><b>② 时间轴上的图标</b>：<b>轴上方 = 对天辉有利</b>、<b>轴下方 = 对夜魇有利</b>；
      图标画的是"发生了什么"（阵亡英雄的头像、被摧毁的塔/兵营/基地、肉山）；
      图标外圈的<b>颜色是它属于哪一方</b>（绿=天辉、红=夜魇、灰=无主，例如肉山）。
      <b>点一下图标</b>即可跳到那一刻；播放头附近的图标会高亮。想看具体时间，勾"时间戳文字"；只想看推塔和肉山，勾"只标建筑/肉山"。</p>
    <p><b>③ 地图</b>：滚轮缩放、拖拽平移、双击回到全图；每个英雄是一个圆点，<b>圆点的颜色就是它属于哪一方</b>
      （绿=天辉、红=夜魇）。地图上只保留队伍信息，<b>大招与 TP 的状态不画在图上</b>。
      <b>阵亡时会变灰</b>，最近 40 秒有轨迹；<b>点圆点</b>就是选中这名英雄。建筑被摧毁会在图上打叉；
      眼位（假眼/真眼）和烟雾也画在图上，鼠标移上去能看到详细信息。
      上方一排开关可以分别隐藏：建筑、轨迹、名字、眼位、烟雾。
      <b>地图右侧那一列英雄头像</b>上才显示大招与 TP：头像旁那根<b>竖长条</b>＝大招（<b>天辉在头像左侧、夜魇在右侧</b>，
      <b>黄＝就绪</b>、<b>暗灰＝冷却中</b>、<b>更暗＝这一刻没有数据</b>）；<b>紧挨着长条</b>（同一侧）那个
      <b>回城卷轴图标</b>＝ TP（<b>亮着＝可用</b>、<b>压暗并带数字＝冷却中，数字就是还剩几秒</b>）。
      长条与 TP 图标各自占一块地方、互不遮挡。鼠标移到头像上有完整文字说明。</p>
    <p><b>④ 右侧表格</b>：<b>K/D/A</b>=击杀/阵亡/助攻，<b>正补/反补</b>=补掉对方/己方小兵的数量；
      <b>净值</b>=现金+装备总价值，<b>累计金币</b>=从击杀补刀等赚到的钱，<b>经验</b>=累计获得的经验值，<b>HP</b>=此刻血量。
      带 <b>@t</b> 的列会随着播放时刻一起变化。</p>
    <p><b>⑤ 战斗记录（点英雄后）</b>：列顺序是 <b>时刻 ｜ 本英雄 ｜ 技能/事件 ｜ 数值 ｜ 对象</b>，
      所以"对方是谁"永远在最右边。默认显示当前时刻<b>前后各 45 秒</b>的条目，四类内容（施加的状态、受到的状态、
      造成的伤害、受到的伤害）可以分别关掉；<b>合并连续相同项</b>把连续重复的同类条目合成一行（后面的 ×N 是次数）；
      <b>隐藏小兵/中立/召唤</b>只留英雄与建筑相关的条目，团战混乱时特别好用。
      每行的图标是"技能/单位/英雄"的示意图，<b>文字名称一直保留</b>。</p>
    <p><b>⑥ 技能冷却</b>：显示该英雄每个技能与常用道具此刻是冷却中（灰底 + 剩余秒）、可用，还是未学习/未拥有（锁）。
      灰色数字来自录像中技能的<b>真实剩余冷却</b>（已含等级与减 CD 效果），所以不需要另配一张冷却时间表。
      <b>大招</b>在表里和头像框上都会被单独标出来（大招就绪＝绿）。
      <b>回城卷轴（TP）</b>是充能制：录像里只有它的<b>使用时刻</b>，没有冷却/充能事件，
      所以头像框旁的 TP 状态是<b>按 TP 卷轴那只固定的共享冷却推算</b>出来的（秒数显示在按钮旁的图例里）。
      别的传送手段（比如飞鞋）也会占用同一只共享冷却，因此 TP 状态只能当参考，它不是录像里的原始数值。</p>
    <p><b>⑦ 数字从哪来</b>：全部来自这一场比赛的录像解析（位置、血量、经济、战斗事件、眼位、技能状态）。
      录像里的时间有两种：游戏内时间会因暂停而停住，回放时间不会；本页统一换算成<b>游戏内时间</b>并显示，
      所以你看到的时刻就是选手看到的时刻（@@PAUSE_TXT@@）。</p>
    <p><b>⑧ 读数字时的几点注意</b>：
      · "<b>经济差（净值）</b>"与"<b>经济差（累计获取）</b>"的算法不同：前者含装备与存款、后者只算赚到的钱，
      两者随比赛推进会越差越多（本场结束时的差距：<b id="gapEnd">—</b>）；
      · <b>经验差</b>只有一条数据来源，无法交叉校验，作趋势参考；
      · <b>当前胜率</b>是把"历史上处于同样经济/经验局面的队伍最终赢了多少"统计出来的参考值（基于 <b id="wpN">—</b>），
      <b>不是对这场比赛的预测</b>，也不代表必然结果；该统计的区分度：<b id="wpAuc">—</b>；
      · 地图位置按每秒采样、偶尔会有缺格，缺格时沿用上一秒的位置（hover 会标注）；
      · 小兵、中立生物、召唤物这类单位<b>没有官方头像</b>，页面上用简笔图标表示类别（士兵/弓箭/攻城车/旗手/野兽爪印/召唤物），
      具体名字在文字里写着；
      · 中途开始录制的比赛，时间轴会带上偏移，此时画面上的开场不等于 0:00。</p>
  </details>
</div>
</div>

<script>
"use strict";
/* ═══════════════ 数据 ═══════════════ */
const DATA = @@BLOB@@;
const MAPIMG = "@@MAP@@";
const T0 = DATA.t0, T1 = DATA.t1, D = DATA.D;
const PL = DATA.players, ICONS = DATA.icons, META = DATA.meta;
const POS = DATA.pos, HPM = DATA.hpm || {}, NW = DATA.nw, CG = DATA.cg, CX = DATA.cx, DIFF = DATA.diff;
const KILLSX = DATA.kills, BLD = DATA.buildings, KDA = DATA.kda;
const DET = DATA.detail || {}, DNAMES = DATA.dnames || [];
const CD = DATA.cd || null, TPUT = DATA.tp || {}, WP = DATA.wp || null, ABICONS = DATA.abicons || {};
const TPICON = DATA.tpicon || "";     // 回城卷轴图标（必须在建 Image 之前就绪）
const WARDS = DATA.wards || [], SMOKE = DATA.smoke || [], SMOKED = DATA.smoked || {}, WICONS = DATA.wicons || {};
const WARDNAME = ["假眼 Observer（寿命 360s）", "真眼 Sentry（寿命 420s，真视 1050）"];
const WREASON = ["自然到期", "被反/被摧毁", "被比赛结束截断", "赛后残留", "—"];
/* 眼位：Q5B 同口径（q5_ward.parse_match）；w = [x,y,team,kind,place,destroy,reason,censored] */
function wardsAliveAt(t) {
  const out = [];
  for (let i = 0; i < WARDS.length; i++) {
    const w = WARDS[i];
    if (t >= w[4] && t <= w[5]) out.push(w);
  }
  return out;
}
function isSmoked(npc, t) {
  const a = SMOKED[npc];
  if (!a) return false;
  for (let i = 0; i < a.length; i++) { if (t >= a[i][0] && t <= a[i][1]) return true; }
  return false;
}
const CATNAME = ["施加的状态", "受到的状态", "造成的伤害", "受到的伤害"];
const CATCHIP = ["施加", "受到", "造成", "受伤"];   // 表格里的小标签（两字，避免"受到"撞车）
const MINKIND = ["Add", "Remove", "Stack", ""];
const DMGKIND = ["", "暴击", "魔法伤害", "吸收"];
const MAP_HALF = 8600;
const STEP = DATA.step || 1;                      // lite 版逐秒数据被抽稀到每 STEP 秒一格
const HOLD = Math.max(1, Math.round(12 / STEP));  // "沿用上一秒"的窗口（按格数换算）
const ROUTE = Math.max(2, Math.round(40 / STEP));
/* 明细按「与上一条的秒差」编码 → 惰性展开成绝对秒（仅该英雄首次被选中时算一次） */
const DETABS = {};
function detAbs(i) {
  const npc = PL[i].npc;
  if (DETABS[npc]) return DETABS[npc];
  const rows = DET[npc] || [];
  const out = new Array(rows.length);
  let t = 0;
  for (let k = 0; k < rows.length; k++) { t += rows[k][0]; out[k] = t; }
  DETABS[npc] = out;
  return out;
}
/* ── 状态胜率（派生模型；系数来自 analysis/q7_winprob.py）──
   模型 = 分时间桶的逻辑回归 + 桶间对系数线性插值（对 t 连续）。
   ⚠️ 经济差与经验差实测正相关 r=0.52 → 单个系数的符号不具解释意义，只在联合分布上有效。 */
function winCoefAt(t) {
  const C = WP.centers, K = WP.coefs;
  if (t <= C[0]) return K[0];
  if (t >= C[C.length - 1]) return K[K.length - 1];
  for (let i = 0; i < C.length - 1; i++) {
    if (t >= C[i] && t <= C[i + 1]) {
      const f = (t - C[i]) / (C[i + 1] - C[i]), a = K[i], b = K[i + 1];
      return [a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2])];
    }
  }
  return K[K.length - 1];
}
function winProb(t, gd, xd) {
  if (!WP || !WP.centers || gd === null || xd === null || t === null) return null;
  const w = winCoefAt(t);
  const z = w[0] + w[1] * (gd / 1000) + w[2] * (xd / 1000);
  return 1 / (1 + Math.exp(-Math.max(-30, Math.min(30, z))));
}
function fmtPct(v) { return (v === null || v === undefined) ? "—" : (100 * v).toFixed(1) + "%"; }


/* ═══════════════ 地图坐标（沿用 Q5B 官方标定） ═══════════════ */
/* CSX = 画布的"逻辑边长"，也是绘制坐标系（w2p 输出 0..CSX）。地图改成按剩余屏幕高度定尺寸后，
   CSX 必须跟着画布 CSS 尺寸走（否则缩小后字/线会等比缩得看不清）。fitCanvas() 负责同步。 */
let CSX = 1024;
const CALIB_K = 0.049038, CALIB_OFFX = 508.3019, CALIB_REF_Y = 504.5433;
const CALIB_REF = 1024;                    // 官方标定式是对着 1024×1024 底图推的
const FULL = [-8600, 8600, -8600, 8600];
let viewRect = null;
/* 画布边长改成随屏幕高度变化后，标定式的输出必须按 CSX/1024 缩放（否则全图态下标记会跑到画布外）。
   字/线的绝对尺寸因此保持屏幕像素不变（缩小地图不会把字也缩小）。 */
function mapScale() { return CSX / CALIB_REF; }
function w2p(x, y) {
  const s = mapScale();
  return [(CALIB_OFFX + CALIB_K * x) * s, (CALIB_REF_Y - CALIB_K * y) * s];
}
function w2pView(x, y) {
  if (!viewRect) return w2p(x, y);
  const v = viewRect;
  const fx = (x - v[0]) / (v[1] - v[0]), fy = (y - v[2]) / (v[3] - v[2]);
  return [fx * CSX, (1 - fy) * CSX];
}
function calibFromPx(px, py) {
  if (!viewRect) {
    const s = mapScale();
    return [(px / s - CALIB_OFFX) / CALIB_K, (CALIB_REF_Y - py / s) / CALIB_K];
  }
  const v = viewRect;
  return [v[0] + (px / CSX) * (v[1] - v[0]), v[2] + (1 - py / CSX) * (v[3] - v[2])];
}
function clampView(vr) {
  let w = vr[1] - vr[0], h = vr[3] - vr[2];
  const MW = FULL[1] - FULL[0], MH = FULL[3] - FULL[2];
  w = Math.max(600, Math.min(w, MW)); h = Math.max(600, Math.min(h, MH));
  let cx = (vr[0] + vr[1]) / 2, cy = (vr[2] + vr[3]) / 2;
  cx = Math.max(FULL[0] + w / 2, Math.min(FULL[1] - w / 2, cx));
  cy = Math.max(FULL[2] + h / 2, Math.min(FULL[3] - h / 2, cy));
  return [cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2];
}
/* 缩到/拖到"已经整张图都看得到"时，归位成全图态（viewRect=null）——否则永远回不到全图 */
function snapFull(vr) {
  if (vr[0] <= FULL[0] + 1 && vr[1] >= FULL[1] - 1 && vr[2] <= FULL[2] + 1 && vr[3] >= FULL[3] - 1) return null;
  return vr;
}

/* ═══════════════ 状态 ═══════════════ */
let tBig = 0;        // 已提交时刻（显示秒）
let sVal = 0;        // 小条偏移（-60..60）
let tCur = 0;        // 实际展示时刻 = clamp(tBig + sVal)
let playing = false, speed = 1, selIdx = -1;
let entDiff = "nw";  // 火花线口径：nw | cg | cx
let annMarks = [];   // 地图上可点对象
let hoverMark = null;
let _drawing = false;
let _lastFrame = -1;

/* ═══════════════ 取值工具 ═══════════════ */
const DP = DATA.DP || D, DEE = DATA.DE || D;
function kOf(t) { const k = Math.round((t - T0) / STEP); return (k >= 0 && k < DP) ? k : null; }
/* 经济/经验序列用 estep 抽稀 → 独立索引（DIFF/NW/CG/CX 三个都是 estep 网格） */
const ESTEP = DATA.estep || 1;
const EMAX = DATA.DE || (Math.floor((D - 1) / ESTEP) + 1);
function kOfE(t) { const k = Math.round((t - T0) / ESTEP); return (k >= 0 && k < EMAX) ? k : null; }
function fmt(sec, sign) {
  if (sec === null || sec === undefined || isNaN(sec)) return "—";
  const v = Math.round(sec), s = Math.abs(v);
  const t = Math.floor(s / 60) + ":" + (s % 60 < 10 ? "0" : "") + (s % 60);
  return (v < 0 ? "-" : (sign && v > 0 ? "+" : "")) + t;
}
function fmtNum(v) {
  if (v === null || v === undefined || isNaN(v)) return "—";
  v = Math.round(v);
  const a = Math.abs(v), s = a.toLocaleString("en-US");
  return (v > 0 ? "+" : (v < 0 ? "-" : "")) + s;
}
/* 位置：沿用上一秒（1Hz 采样偶有缺秒） */
function posAt(npc, t) {
  const k = kOf(t); if (k === null) return null;
  const P = POS[npc]; if (!P) return null;
  const n = Math.min(P.x.length, P.y.length, P.hp.length, DP);
  if (k < n && P.x[k] != null) {
    return { x: P.x[k], y: P.y[k], hp: P.hp[k], t: T0 + k * STEP };
  }
  for (let i = Math.min(k - 1, n - 1); i >= 0 && i > k - HOLD; i--) {
    if (P.x[i] != null) {
      return { x: P.x[i], y: P.y[i], hp: P.hp[i], t: T0 + i * STEP, stale: true };
    }
  }
  return null;
}
/* 血量上限（随等级变）——同样沿用上一秒 */
function hpMaxAt(npc, t) {
  const k = kOf(t), a = HPM[npc];
  if (k === null || !a) return null;
  if (k < a.length && a[k] != null) return a[k];
  for (let i = Math.min(k - 1, a.length - 1); i >= 0 && i > k - Math.max(1, Math.round(30 / STEP)); i--) {
    if (a[i] != null) return a[i];
  }
  return null;
}
function valAt(series, npc, t) {
  const k = kOfE(t); if (k === null) return null;
  const a = series[npc]; if (!a) return null;
  if (k < a.length && a[k] != null) return a[k];
  for (let i = Math.min(k - 1, a.length - 1); i >= 0 && i > k - Math.max(1, Math.round(15 / ESTEP)); i--) {
    if (a[i] != null) return a[i];
  }
  return null;
}
function diffAt(key, t) {
  const k = kOfE(t); if (k === null) return null;
  const a = DIFF[key]; if (!a) return null;
  return (k < a.length && a[k] != null) ? a[k] : null;
}
function isDead(i, t) {
  const p = PL[i], q = posAt(p.npc, t);
  if (!q) return true;
  return (q.hp !== null && q.hp !== undefined && q.hp <= 0);
}

/* ═══════════════ 地图绘制 ═══════════════ */
const cv = document.getElementById("cv"), ctx = cv.getContext("2d");
const bgimg = new Image(); let bgReady = false;
bgimg.onload = function () { bgReady = true; draw(); };
bgimg.src = MAPIMG;
const imgs = {};
Object.keys(ICONS).forEach(function (k) { const im = new Image(); im.src = ICONS[k]; imgs[k] = im; });
const wimgs = {};
Object.keys(WICONS).forEach(function (k) { const im = new Image(); im.src = WICONS[k]; wimgs[k] = im; });

function markText(m) {
  if (m.kind === "ward") {
    const w = m.w;
    return WARDNAME[w[3]] + " ｜ " + (w[2] === 2 ? "天辉" : "夜魇")
      + " ｜ (" + Math.round(w[0]) + "," + Math.round(w[1]) + ")"
      + " ｜ 放置 " + fmt(w[4]) + " → 销毁 " + fmt(w[5])
      + "（存活 " + (w[5] - w[4]) + "s）｜ " + WREASON[w[6]]
      + (w[7] ? "【截断：比赛结束时仍存活】" : "");
  }
  if (m.kind === "smoke") {
    const sm = m.sm;
    return "烟雾（" + PL[sm[1]].short.replace(/_/g, " ") + "）" + " ｜ 开启 " + fmt(sm[0])
      + " ｜ 距现在 " + Math.round(tCur - sm[0]) + "s ｜ (" + Math.round(sm[2]) + "," + Math.round(sm[3]) + ")"
      + " ｜ 英雄受烟雾 buff 的区间见紫环";
  }
  const p = PL[m.i], q = posAt(p.npc, tCur);
  return p.short.replace(/_/g, " ") + " ｜ " + (p.team === 2 ? "天辉" : "夜魇")
    + " ｜ 坐标(" + Math.round(m.wx) + "," + Math.round(m.wy) + ")"
    + (q && q.stale ? " ｜ ⚠位置沿用 " + fmt(q.t) : "")
    + (isSmoked(p.npc, tCur) ? " ｜ 处于烟雾中" : "");
}
function teamColor(t) { return t === 2 ? "#4aa564" : "#d24b4b"; }

function render() {
  ctx.clearRect(0, 0, CSX, CSX);
  ctx.fillStyle = "#0d1117"; ctx.fillRect(0, 0, CSX, CSX);
  const op = parseFloat(document.getElementById("mop").value) / 100;
  if (bgReady) {
    ctx.globalAlpha = op;
    if (viewRect) {
      const v = viewRect;
      const p0 = w2p(v[0], v[3]), p1 = w2p(v[1], v[2]);
      ctx.drawImage(bgimg, p0[0], p0[1], Math.max(1, p1[0] - p0[0]), Math.max(1, p1[1] - p0[1]), 0, 0, CSX, CSX);
    } else {
      ctx.drawImage(bgimg, 0, 0, CSX, CSX);
    }
    ctx.globalAlpha = 1;
  }
  annMarks = [];

  // 建筑
  if (document.getElementById("showBld").checked) {
    const zoom = viewRect ? 1 : 0;
    BLD.forEach(function (b) {
      const dead = (b[4] !== null && tCur >= b[4]);
      const p = w2pView(b[0], b[1]);
      if (p[0] < -20 || p[0] > CSX + 20 || p[1] < -20 || p[1] > CSX + 20) return;
      const s = (b[3] === "barracks" ? 9 : (b[3] === "tower" ? 8 : 12)) + zoom * 4;
      ctx.globalAlpha = dead ? 0.28 : 0.85;
      ctx.fillStyle = dead ? "#555" : teamColor(b[2]);
      ctx.fillRect(p[0] - s / 2, p[1] - s / 2, s, s);
      ctx.globalAlpha = 1;
      if (dead) { ctx.strokeStyle = "#ff6b6b"; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(p[0] - s / 2, p[1] - s / 2); ctx.lineTo(p[0] + s / 2, p[1] + s / 2);
        ctx.moveTo(p[0] + s / 2, p[1] - s / 2); ctx.lineTo(p[0] - s / 2, p[1] + s / 2); ctx.stroke(); }
    });
  }

  // 眼位（存活中的守卫）—— 与 Q5B 同一套口径与图标
  if (document.getElementById("showWard").checked) {
    const alive = wardsAliveAt(tCur);
    alive.forEach(function (w) {
      const c = w2pView(w[0], w[1]);
      if (c[0] < -30 || c[0] > CSX + 30 || c[1] < -30 || c[1] > CSX + 30) return;
      const s = (viewRect ? 15 : 10);
      const im = wimgs[w[3] === 1 ? "sen" : "obs"];
      ctx.globalAlpha = 0.95;
      if (im && im.complete && im.naturalWidth) {
        ctx.drawImage(im, c[0] - s / 2, c[1] - s / 2, s, s);
      } else {   // 兜底：假眼=圆、真眼=菱形，颜色按队
        ctx.fillStyle = teamColor(w[2]);
        ctx.beginPath();
        if (w[3] === 1) { ctx.moveTo(c[0], c[1] - s / 2); ctx.lineTo(c[0] + s / 2, c[1]); ctx.lineTo(c[0], c[1] + s / 2); ctx.lineTo(c[0] - s / 2, c[1]); ctx.closePath(); }
        else { ctx.arc(c[0], c[1], s / 2, 0, Math.PI * 2); }
        ctx.fill(); ctx.strokeStyle = "#fff"; ctx.lineWidth = 1; ctx.stroke();
      }
      ctx.globalAlpha = 1;
      annMarks.push({ x: c[0], y: c[1], r: s / 2 + 3, kind: "ward", w: w });
    });
    const wbox = document.getElementById("wardCount");
    if (wbox) wbox.textContent = "（存活 " + alive.length + " 支 / 全场 " + WARDS.length + "）";
  } else {
    const wbox = document.getElementById("wardCount");
    if (wbox) wbox.textContent = "";
  }

  // 烟雾使用（激活后 20s 内高亮）—— combat_log item_smoke_of_deceit
  if (document.getElementById("showSmoke").checked) {
    SMOKE.forEach(function (sm) {
      const dt = tCur - sm[0];
      if (dt < 0 || dt > 20) return;
      const c = w2pView(sm[2], sm[3]);
      if (c[0] < -30 || c[0] > CSX + 30 || c[1] < -30 || c[1] > CSX + 30) return;
      const r = (viewRect ? 17 : 12);
      ctx.globalAlpha = Math.max(0.15, 0.7 - dt / 30);
      ctx.fillStyle = "#a371f7";
      ctx.beginPath(); ctx.arc(c[0], c[1], r, 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = 1;
      ctx.strokeStyle = "#d2a8ff"; ctx.lineWidth = 2; ctx.stroke();
      annMarks.push({ x: c[0], y: c[1], r: r + 3, kind: "smoke", sm: sm });
    });
  }

  // 最近击杀的"骷髅"标记（±4s 内）
  KILLSX.forEach(function (k) {
    if (Math.abs(k[0] - tCur) > 4) return;
    const p = PL[k[2]]; if (!p) return;
    const q = posAt(p.npc, k[0]); if (!q) return;
    const c = w2pView(q.x, q.y);
    ctx.globalAlpha = 0.85; ctx.fillStyle = "#f85149";
    ctx.beginPath(); ctx.arc(c[0], c[1], viewRect ? 11 : 8, 0, Math.PI * 2); ctx.fill();
    ctx.globalAlpha = 1;
  });

  // 英雄轨迹（最近 40s）
  if (document.getElementById("showRoute").checked) {
    for (let i = 0; i < PL.length; i++) {
      const p = PL[i], P = POS[p.npc];
      if (!P) continue;
      ctx.beginPath(); let started = false;
      const kEnd = kOf(tCur); if (kEnd === null) continue;
      for (let k = Math.max(0, kEnd - ROUTE); k <= kEnd; k++) {
        if (P.x[k] === null) continue;
        const c = w2pView(P.x[k], P.y[k]);
        if (!started) { ctx.moveTo(c[0], c[1]); started = true; } else ctx.lineTo(c[0], c[1]);
      }
      if (started) {
        ctx.strokeStyle = teamColor(p.team);
        ctx.globalAlpha = (selIdx === i) ? 0.95 : 0.4;
        ctx.lineWidth = (selIdx === i) ? 3 : 1.6;
        ctx.stroke(); ctx.globalAlpha = 1;
      }
    }
  }

  // 英雄（沿线绘制的顺序：先敌后友，选中最后画）
  const order = [];
  for (let i = 0; i < PL.length; i++) order.push(i);
  order.sort(function (a, b) {
    const da = (selIdx === a) ? 1 : 0, db = (selIdx === b) ? 1 : 0;
    return da - db;
  });
  const showName = document.getElementById("showName").checked;
  order.forEach(function (i) {
    const p = PL[i], q = posAt(p.npc, tCur);
    if (!q) return;
    const c = w2pView(q.x, q.y);
    const dead = (q.hp !== null && q.hp <= 0);
    const R = (viewRect ? 18 : 13) * (selIdx === i ? 1.22 : 1);
    if (c[0] < -40 || c[0] > CSX + 40 || c[1] < -40 || c[1] > CSX + 40) return;
    ctx.save();
    ctx.beginPath(); ctx.arc(c[0], c[1], R, 0, Math.PI * 2); ctx.closePath();
    ctx.globalAlpha = dead ? 0.35 : 1;
    ctx.fillStyle = "#000"; ctx.fill();
    const im = imgs[p.short];
    if (im && im.complete && im.naturalWidth) {
      /* ★ 英雄图是 128×72 的横版卡片：必须取**中间正方形**那块源区域再画，
         直接把它铺进 2R×2R 的圆里会把头像横向压扁（"保持原来的比例"）。 */
      const ss = Math.min(im.naturalWidth, im.naturalHeight);
      const sx = (im.naturalWidth - ss) / 2, sy = (im.naturalHeight - ss) / 2;
      ctx.save(); ctx.clip();
      ctx.drawImage(im, sx, sy, ss, ss, c[0] - R, c[1] - R, R * 2, R * 2);
      ctx.restore();
    } else {
      ctx.fillStyle = teamColor(p.team);
      ctx.font = "bold " + Math.round(R * 1.1) + "px sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(p.short.slice(0, 2).toUpperCase(), c[0], c[1]);
    }
    ctx.globalAlpha = 1;
    ctx.lineWidth = (selIdx === i) ? 3.5 : 2.2;
    ctx.strokeStyle = dead ? "#666" : teamColor(p.team);
    ctx.stroke();
    if (selIdx === i) {
      ctx.beginPath(); ctx.arc(c[0], c[1], R + 4, 0, Math.PI * 2);
      ctx.strokeStyle = "#e3b341"; ctx.lineWidth = 2; ctx.stroke();
    }
    /* 地图上的英雄圆点**保持干净**：只有队伍色描边（＋选中金环／烟雾紫环）。
       大招状态在页面中间那列头像旁的长条上、TP 在头像右上角的徽标上 —— 地图上不再画这些
       （owner 定案：小地图的英雄图标不要显示这个）。 */
    ctx.restore();
    if (showName) {
      ctx.font = "bold 11px 'Segoe UI',sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
      const label = p.short.replace(/_/g, " ");
      const w = ctx.measureText(label).width + 6;
      ctx.fillStyle = "rgba(0,0,0,.55)";
      ctx.fillRect(c[0] - w / 2, c[1] + R + 2, w, 13);
      ctx.fillStyle = dead ? "#999" : "#fff";
      ctx.fillText(label, c[0], c[1] + R + 12);
    }
    if (isSmoked(p.npc, tCur)) {       // 处于烟雾中：紫色外环
      ctx.beginPath(); ctx.arc(c[0], c[1], R + 7, 0, Math.PI * 2);
      ctx.strokeStyle = "#a371f7"; ctx.lineWidth = 2.5; ctx.stroke();
    }
    annMarks.push({ x: c[0], y: c[1], r: R + 3, kind: "hero", i: i, wx: q.x, wy: q.y });
  });

  // 比例尺
  ctx.font = "11px sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  ctx.fillStyle = "rgba(255,255,255,.45)";
  if (viewRect) ctx.fillText("放大 " + (17000 / (viewRect[1] - viewRect[0])).toFixed(1) + "×（滚轮缩放/拖拽平移，双击复位）", 8, 16);
}

let _pending = false;
function draw() {
  if (_drawing) { _pending = true; return; }
  _drawing = true;
  try { render(); } finally { _drawing = false; if (_pending) { _pending = false; } }
}

/* ═══════════════ 一屏适配：地图按剩余高度/宽度取正方形边长 ═══════════════
   目标：地图 + 头像 + 时间轴 + 右栏 combat log 同屏可见（页面本身不纵向滚动）。
   地图是唯一可伸缩的元素：取 min(左栏可用宽, 左栏可用高)，并把逻辑坐标系 CSX 跟着改，
   这样字/线在屏幕上的绝对大小不随地图缩小而变小。 */
const CV_MIN = 200;
function fitCanvas() {
  const box = document.getElementById("mapbox");
  if (!box || !cv) return;
  const bw = box.clientWidth || 0, bh = box.clientHeight || 0;
  /* 窄屏（≤1180px）走"单栏 + 整页滚动"那套 CSS：地图按栏宽铺满，1:1 仍然成立 */
  const narrow = (typeof window !== "undefined" && window.innerWidth) ? window.innerWidth <= 1180 : false;
  let s = narrow ? Math.floor(bw || 512)
                 : Math.floor(Math.min(bw || (bh || 900), bh || (bw || 900)));
  if (!isFinite(s) || s <= 0) s = 512;
  s = Math.max(CV_MIN, s);
  if (s !== CSX || cv.width !== s || cv.height !== s) {
    CSX = s;
    cv.width = s; cv.height = s;
    cv.style.width = s + "px"; cv.style.height = s + "px";
    draw();
    return true;
  }
  return false;
}

/* ═══════════════ 地图交互 ═══════════════ */
function evPx(e) {
  const r = cv.getBoundingClientRect();
  return [(e.clientX - r.left) / r.width * CSX, (e.clientY - r.top) / r.height * CSX];
}
let drag = null;
cv.onwheel = function (e) {
  e.preventDefault();
  const px = evPx(e)[0], py = evPx(e)[1];
  const w = calibFromPx(px, py);
  const vr = viewRect ? viewRect.slice() : FULL.slice();
  const z = e.deltaY < 0 ? 0.8 : 1.25;
  const cw = vr[1] - vr[0], ch = vr[3] - vr[2];
  const nw = cw * z, nh = ch * z;
  const fx = (w[0] - vr[0]) / cw, fy = (w[1] - vr[2]) / ch;
  const nx0 = w[0] - fx * nw, ny0 = w[1] - fy * nh;
  viewRect = snapFull(clampView([nx0, nx0 + nw, ny0, ny0 + nh]));
  draw();
};
cv.onmousedown = function (e) {
  if (e.button !== 0 && e.button !== 1) return;
  e.preventDefault();
  const px = evPx(e)[0], py = evPx(e)[1];       // ★ px/py 必须在 if(drag) 之外声明（Q5B §9.2 坑）
  let hit = null, best = 1e9;
  annMarks.forEach(function (m) { const d = Math.hypot(px - m.x, py - m.y); if (d < m.r + 4 && d < best) { hit = m; best = d; } });
  if (hit && hit.kind === "hero") { selectHero(hit.i); return; }
  if (hit) { pinnedMark = hit; draw(); return; }
  drag = { sx: px, sy: py, vr: viewRect ? viewRect.slice() : FULL.slice() };
  cv.style.cursor = "grabbing";
};
cv.onmousemove = function (e) {
  const px = evPx(e)[0], py = evPx(e)[1];
  if (drag) {
    const vr = drag.vr, w = vr[1] - vr[0], h = vr[3] - vr[2];
    const dx = (px - drag.sx) / CSX * w, dy = (py - drag.sy) / CSX * h;
    viewRect = snapFull(clampView([vr[0] - dx, vr[1] - dx, vr[2] + dy, vr[3] + dy]));
    draw(); return;
  }
  let hit = null, best = 1e9;
  annMarks.forEach(function (m) { const d = Math.hypot(px - m.x, py - m.y); if (d < m.r + 4 && d < best) { hit = m; best = d; } });
  let txt = "滚轮缩放 · 拖拽平移 · 点英雄标记选中 · 点眼/烟雾标记锁定";
  if (hit) {
    txt = markText(hit);
  } else if (pinnedMark) {
    txt = markText(pinnedMark);
  }
  if (drag) txt = "拖拽平移中…";
  if (document.getElementById("mapInfo").textContent !== txt) document.getElementById("mapInfo").textContent = txt;
};
cv.onmouseup = function () { if (drag) { drag = null; cv.style.cursor = "grab"; } };
cv.onmouseleave = function () { if (drag) { drag = null; cv.style.cursor = "grab"; } };
cv.ondblclick = function () { resetZoom(); };
function resetZoom() { viewRect = null; draw(); }

/* ═══════════════ 头像 / 明细表 ═══════════════ */
function buildAvatars() {
  const box = document.getElementById("avatars");
  box.innerHTML = "";
  /* owner 方案①：10 个头像竖排在地图右侧（天辉一列 ｜ 夜魇一列），把地图下方的整块高度还给地图。
     窄屏（≤1180px 单栏）时自动折行，仍然可用。 */
  const team = function (tv, label) {
    const col = document.createElement("div"); col.className = "tcol";
    const cap = document.createElement("div");
    cap.className = "tcap " + (tv === 2 ? "dr" : "dd"); cap.textContent = label;
    col.appendChild(cap);
    PL.forEach(function (p, i) {
      if (p.team !== tv) return;
      /* 一行 = 大招长条 + TP 徽标 + 头像（夜魇整排镜像）；长条与徽标都在头像**外面** */
      const row = document.createElement("div");
      row.className = "hrow " + (p.team === 2 ? "t2" : "t3");
      row.setAttribute("data-i", i);
      const bar = document.createElement("div"); bar.className = "ultbar";
      const badge = document.createElement("div"); badge.className = "tpbadge";
      badge.innerHTML = (TPICON ? '<img src="' + TPICON + '" alt="TP">' : "") + '<b class="tpcd"></b>';
      const d = document.createElement("div");
      d.className = "hero " + (p.team === 2 ? "t2" : "t3");
      d.setAttribute("data-i", i); d.title = p.short.replace(/_/g, " ") + "（" + p.name + "）";
      const im = ICONS[p.short];
      d.innerHTML = (im ? '<img src="' + im + '" alt="">' : "") +
        '<div class="nm">' + p.short.replace(/_/g, " ") + '</div>' +
        '<div class="hpbar" style="width:0%"></div>';
      d.onclick = function () { selectHero(i); };
      d.onmouseenter = function () { hoverRow(i); };
      d.onmouseleave = function () { hoverRow(-1); };
      row.appendChild(bar); row.appendChild(badge); row.appendChild(d);
      col.appendChild(row);
    });
    box.appendChild(col);
    return col;
  };
  team(2, "天辉");
  const dv = document.createElement("div"); dv.className = "tdiv"; box.appendChild(dv);
  team(3, "夜魇");
  if (typeof fitCanvas === "function") fitCanvas();
}
function hoverRow(i) {
  Array.prototype.forEach.call(document.querySelectorAll("#tbl tbody tr"), function (tr) {
    tr.classList.toggle("hov", i >= 0 && +tr.getAttribute("data-i") === i);
  });
}
function selectHero(i) {
  selIdx = (selIdx === i) ? -1 : i;
  Array.prototype.forEach.call(document.querySelectorAll(".hero"), function (d) {
    d.classList.toggle("sel", +d.getAttribute("data-i") === selIdx);
  });
  Array.prototype.forEach.call(document.querySelectorAll("#tbl tbody tr"), function (tr) {
    tr.classList.toggle("selrow", +tr.getAttribute("data-i") === selIdx);
  });
  document.getElementById("paneList").style.display = (selIdx < 0) ? "" : "none";
  document.getElementById("paneHero").style.display = (selIdx < 0) ? "none" : "";
  if (selIdx >= 0) { detAbs(selIdx); renderHeroHead(); renderDetail(); renderCD(); }
  /* 右栏三个面板互斥：点英雄时收起"战斗回顾"。
     ★ 必须放在上面两行**之后**：closeRecap() 会按 selIdx 重新决定列表是否显示，
       放前面会被它覆盖（第一版就是这个顺序问题，表现成"列表和英雄面板同时显示"）。 */
  if (selIdx >= 0 && typeof recapIdx === "number" && recapIdx >= 0) {
    document.getElementById("paneList").style.display = "none";
    closeRecap();
  }
  draw();
}

/* ═══════════════ 英雄 ±45s combat log 明细（带图标） ═══════════════ */
function renderHeroHead() {
  if (selIdx < 0) return;
  const p = PL[selIdx], k = KDA[p.npc];
  const q = posAt(p.npc, tCur);
  const pinfo = CD ? ((CD.track || []).map(function (kk) {
    const e = (CD.it[p.npc] || []).filter(function (x) { return x[0] === kk; })[0];
    const st = !e ? "未拥有" : (cdState(e[3] || [], tCur) !== null
      ? "冷却 " + Math.ceil(cdState(e[3] || [], tCur)) + "s" : "就绪");
    return (CD.keys[kk] || {}).name + " " + st;
  }).join(" ｜ ")) : "";
  document.getElementById("herotop").innerHTML =
    '<b style="color:' + (p.team === 2 ? "#4aa564" : "#d24b4b") + '">' + p.short.replace(/_/g, " ")
    + "</b>（" + (p.team === 2 ? "天辉" : "夜魇") + " · " + esc(p.name) + "）"
    + ' ｜ KDA <b>' + k.k + "/" + k.d + "/" + k.a + "</b> ｜ 正/反补 <b>" + k.lh + "/" + k.dn + "</b>"
    + " ｜ 净值 <b>" + (valAt(NW, p.npc, tCur) === null ? "—" : Math.round(valAt(NW, p.npc, tCur)).toLocaleString("en-US")) + "</b>"
    + (q ? (" ｜ HP <b>" + (q.hp > 0 ? q.hp + " / " + (hpMaxAt(p.npc, tCur) || "?")
      : ((q.hp === null || q.hp === undefined) ? "无数据" : "阵亡")) + "</b>") : " ｜ 未出场")
    + (pinfo ? '<br>关键道具：' + pinfo : "");
}

let lastDetail = null;   // 最近一次渲染的窗口（供回归测试/排查）
const DET_WIN = 45;      // combat log 窗口：当前时刻 ±45s（owner 2026 定案）
const DICONS = DATA.dicons || [], SELFICONS = DATA.selficons || {};
const ATTR = DATA.attr || [];      // 构建期用 DB 做的"英雄↔英雄归属"对账结果（自检用）
let _detKey = "", _detAt = 0, _detTimer = null;
/* 图标：名字下标 → CSS 类（一类一张背景图）。没有图标就返回空串，单元格留白。 */
function diTag(idx, cls) {
  const k = (idx >= 0 && idx < DICONS.length) ? DICONS[idx] : "";
  return k ? '<i class="di ' + (cls ? cls + " " : "") + k + '"></i>' : "";
}
function foldKey(r) { return r[1] + "|" + r[2] + "|" + r[3]; }
/* 「小怪」判定（owner 2026 加的开关）：**不是英雄、也不是建筑**的对象/来源都算小怪 ——
   小兵（creep_*）、中立与肉山（neutral_* / miniboss* / roshan）、英雄的召唤物与分身
   （lone_druid_bear1 / unit_undying_zombie_torso / invoker_forged_spirit / thinker …）。
   英雄（10 位）与建筑（塔/兵营/基地/泉水）**保留**；没有对手方的行（other=-1，如自身 modifier）也保留。
   判定只看名字，不做任何"猜测性归类"；开关默认**关**（默认行为与以前完全一致）。 */
const HERO_SHORTS = {};
PL.forEach(function (q) { HERO_SHORTS[q.short] = 1; });
function isBldName(nm) { return /(^|_)(tower|rax|barracks|fort|fountain)/.test(nm); }
function isCritter(nm) {
  if (!nm) return false;
  if (HERO_SHORTS[nm]) return false;
  if (isBldName(nm)) return false;
  return true;
}
function renderDetail(force) {
  if (selIdx < 0) return;
  const p = PL[selIdx];
  const abs = detAbs(selIdx), rows = DET[p.npc] || [];
  const lo = tCur - DET_WIN, hi = tCur + DET_WIN;
  const on = [0, 1, 2, 3].map(function (c) { return document.getElementById("tc" + c).checked; });
  const fold = document.getElementById("tfold").checked;
  const hideCrit = document.getElementById("tnc").checked;   // 隐藏小兵/中立/召唤
  /* ±45s 的窗口比原来大 8 倍（几百行、每行带图标）→ **播放时按 130ms 节流重建**，
     并保证补一次（窗口一定追上播放头）；拖动滑块/显式调用（force）都立即重建，手感不打折。 */
  const key = [selIdx, Math.round(lo), Math.round(hi), on.join(""), fold, hideCrit ? 1 : 0].join("|");
  if (!force && playing) {
    if (key === _detKey) return;
    if (Date.now() - _detAt < 130) {
      if (!_detTimer) {
        _detTimer = setTimeout(function () { _detTimer = null; renderDetail(true); }, 140);
      }
      return;
    }
  }
  _detKey = key; _detAt = Date.now();
  // 二分找窗口起点
  let a = 0, b = abs.length;
  while (a < b) { const m = (a + b) >> 1; if (abs[m] < lo) a = m + 1; else b = m; }
  const picked = [];
  let crit = 0;
  for (let k = a; k < abs.length && abs[k] <= hi; k++) {
    const r = rows[k];
    const cat = r[1] >> 2, kind = r[1] & 3;
    if (!on[cat]) continue;
    const other = (r[3] >= 0) ? (DNAMES[r[3]] || "") : "";
    if (hideCrit && isCritter(other)) { crit += 1; continue; }
    picked.push({ t: abs[k], cat: cat, kind: kind, nm: DNAMES[r[2]] || "?", nid: r[2], oid: r[3],
                  val: r[4] || 0 });
  }
  let seq = picked;
  if (fold) {
    seq = [];
    for (let k = 0; k < picked.length; k++) {
      const r = picked[k], last = seq[seq.length - 1];
      if (last && last.cat === r.cat && last.kind === r.kind && last.nm === r.nm &&
          last.val === r.val && last.oid === r.oid && r.t - last.tEnd <= 1.2) {
        last.tEnd = r.t; last.n += 1; continue;
      }
      seq.push({ t: r.t, tEnd: r.t, n: 1, cat: r.cat, kind: r.kind, nm: r.nm, nid: r.nid, oid: r.oid, val: r.val });
    }
  }
  const cnt = [0, 0, 0, 0];
  picked.forEach(function (r) { cnt[r.cat]++; });
  lastDetail = { lo: lo, hi: hi, t: tCur, n: picked.length, shown: seq.length, cnt: cnt,
                 crit: crit, hideCrit: hideCrit,
                 tMin: picked.length ? picked[0].t : null,
                 tMax: picked.length ? picked[picked.length - 1].t : null };
  document.getElementById("dsum").innerHTML =
    "<b>" + p.short.replace(/_/g, " ") + "</b>（" + (p.team === 2 ? "天辉" : "夜魇") + " · " + p.name + "）"
    + " ｜ 时间范围 <b>" + fmt(lo) + " → " + fmt(hi) + "</b>（当前时刻前后各 " + DET_WIN + " 秒）"
    + " ｜ 共 <b>" + picked.length + "</b> 条"
    + (fold && seq.length !== picked.length ? "（合并后 " + seq.length + " 行）" : "")
    + (hideCrit ? " ｜ 已隐藏小兵/中立/召唤 <b>" + crit + "</b> 条" : "")
    + "<br>分类条数：" + CATNAME.map(function (c, i) { return c + " <b>" + cnt[i] + "</b>"; }).join(" ｜ ");
  const tb = document.querySelector("#dtbl tbody");
  const selfK = SELFICONS[p.npc] || "";
  const selfTag = selfK ? '<i class="di self big ' + selfK + '"></i>' : "";
  const emptyMsg = !seq.length ? ((!rows.length && DATA.lite)
      ? '<b style="color:#d29922">这是精简版页面，不含逐条战斗记录</b>'
        + "（为了减小体积，精简版省略了这一部分）。想看逐条记录，请打开同一文件夹里的<b>完整版</b>页面。"
      : (!rows.length ? "这场比赛没有这名英雄的战斗记录。"
                      : (hideCrit ? "这一段没有可显示的内容（被上方的类别开关或『隐藏小兵/中立/召唤』过滤掉了）。"
                                  : "这一段没有可显示的内容（可能这名英雄当时不在场，或上方的类别开关被全部关掉了）。"))) : "";
  /* ★ 虚拟滚动：±45s 的窗口在团战期可能上千行（本场实测最多 1785 行），
     一次性塞进 DOM 会卡（每行还带 3 个图标）。这里只画视口附近的 ~160 行，
     上下用等高占位行撑出滚动条；行高固定 24px（CSS 里写死，见 #dtbl tbody tr）。 */
  detSeq = seq; detSelf = selfTag; detEmpty = emptyMsg; detNow = tCur;
  let firstNow = 0;
  for (let k = 0; k < seq.length; k++) { if (seq[k].tEnd >= tCur - 1) { firstNow = k; break; } }
  detNowIdx = firstNow;
  const dw = document.getElementById("dwrap");
  if (dw) {
    const h = dw.clientHeight || 320;
    dw.scrollTop = Math.max(0, firstNow * DET_ROWH - h * 0.4);   // 让"当前时刻"落在视口上方 40% 处
  }
  paintDetRows();
}
/* ══ 明细行：只渲染视口内的行（虚拟滚动）══ */
const DET_ROWH = 24, DET_VIEW = 80, DET_PAD = 20;
let detSeq = [], detSelf = "", detEmpty = "", detNow = 0, detNowIdx = 0, detPaintPending = false;
function detRowHTML(r, tNow) {
  const now = (r.t <= tNow + 1 && r.tEnd >= tNow - 1) ? "rownow" : (r.tEnd < tNow ? "rowpast" : "rowfut");
  const other = (r.oid >= 0 ? (DNAMES[r.oid] || "") : "").replace(/^npc_dota_hero_/, "").replace(/^npc_dota_/, "");
  const kk = r.cat < 2 ? (MINKIND[r.kind] || "") : (DMGKIND[r.kind] || "");
  const ttxt = (r.n > 1 && r.tEnd > r.t) ? (fmt(r.t) + "–" + fmt(r.tEnd)) : fmt(r.t);
  return '<tr class="' + now + '"><td>' + ttxt + "</td>"
    + '<td class="dc">' + detSelf + "</td>"
    + '<td class="dn2"><div class="dirow">' + diTag(r.nid) + '<span class="txt">'
    + '<span class="chip c' + r.cat + '">' + CATCHIP[r.cat] + "</span> "
    + esc(r.nm) + (kk ? ' <span class="dfold">[' + kk + ']</span>' : "")
    + (r.n > 1 ? ' <b class="dfold">×' + r.n + "</b>" : "")
    + "</span></div></td>"
    + '<td class="dv">' + (r.cat >= 2 ? r.val : "—") + "</td>"
    + '<td class="do"><div class="dirow">' + diTag(r.oid) + '<span class="txt">' + esc(other)
    + "</span></div></td></tr>";
}
function paintDetRows() {
  const tb = document.querySelector("#dtbl tbody");
  if (!tb) return;
  if (!detSeq.length) { tb.innerHTML = '<tr><td colspan="5">' + detEmpty + "</td></tr>"; return; }
  const dw = document.getElementById("dwrap");
  const st = dw ? dw.scrollTop : 0;
  const h = (dw && dw.clientHeight) || 320;
  let first = Math.max(0, Math.floor(st / DET_ROWH) - DET_PAD);
  let last = Math.min(detSeq.length, first + Math.ceil(h / DET_ROWH) + DET_PAD * 2);
  let html = "";
  if (first > 0) {
    html += '<tr class="sp"><td colspan="5" style="height:' + (first * DET_ROWH) + 'px"></td></tr>';
  }
  for (let k = first; k < last; k++) html += detRowHTML(detSeq[k], detNow);
  if (last < detSeq.length) {
    html += '<tr class="sp"><td colspan="5" style="height:'
      + ((detSeq.length - last) * DET_ROWH) + 'px"></td></tr>';
  }
  tb.innerHTML = html;
}
function detOnScroll() {
  if (detPaintPending) return;
  detPaintPending = true;
  requestAnimationFrame(function () { detPaintPending = false; paintDetRows(); });
}
function esc(s) {
  return String(s === undefined || s === null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/* ═══════════════ 技能 CD（三态） ═══════════════ */
function cdState(ivs, t) {
  for (let i = 0; i < ivs.length; i++) {
    if (t >= ivs[i][0] && t <= ivs[i][1]) return Math.max(0, ivs[i][1] - t);
  }
  return null;
}
/* ═══════════════ 头像框状态：大招 / TP / 队伍 ═══════════════
   大招：取自录像里的技能冷却区间（构建期已标出哪个技能是该英雄的大招，见 hero_ultimates.py）。
   TP：录像里只有**使用时刻**（combat_log 的 item_tpscroll），没有冷却/充能事件，所以冷却
       中与否是按 TP 卷轴的固定冷却（秒数随载荷传入，读自游戏文件 items.txt 的
       AbilityCooldown；它同时是和飞鞋共享的 teleport 共享冷却）**推算**的 —— 页面写明这一点。 */
const TPCOOL = DATA.tpcool || 80;
function ultKeyOwnedBy(key, npc) {
  /* 只认「这名英雄自己的」大招。两个理由：
     ① 老库里有少数场次（shadow_demon 之类）会把**别人的**技能重复归属到同一名英雄名下
        （实测 62 场里 13 场有这种情况），而大招标记是打在**键**上的 —— 不校验归属，
        头像框就会把别人的大招当成他的；
     ② 拉比克这类会把「偷来的」大招列进自己的技能表，那是偷的不算他的大招。
     判据：技能键名里含这名英雄的短名（去掉 CDOTA_Ability_ 前缀后去下划线小写比对）。 */
  const k = String(key).replace(/^CDOTA_Ability_/, "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const h = String(npc).replace(/^npc_dota_hero_/, "").toLowerCase().replace(/[^a-z0-9]/g, "");
  return !!h && k.indexOf(h) >= 0;
}
function ultStateOf(npc, t) {
  if (!CD) return { st: "none" };
  const ent = (CD.ab[npc] || []).filter(function (e) {
    return (CD.keys[e[0]] || {}).ult && ultKeyOwnedBy(e[0], npc);
  });
  if (!ent.length) return { st: "none" };
  let ready = false, best = null, known = false;
  ent.forEach(function (e) {
    const kn = (e[2] !== null && e[2] !== undefined) ? e[2] : e[1];
    if (kn === null || kn === undefined || t < kn) return;   // 这一时刻还没学
    known = true;
    const s = cdState(e[3] || [], t);
    if (s === null || s <= 0.05) ready = true;
    else if (best === null || s > best.s) best = { s: s, name: (CD.keys[e[0]] || {}).name };
  });
  if (!known) return { st: "none" };
  if (ready) return { st: "ready" };
  return { st: "cool", left: best.s, name: best.name };
}
function tpStateOf(npc, t) {
  const arr = TPUT[npc] || [];
  let last = null;
  for (let i = arr.length - 1; i >= 0; i--) { if (arr[i] <= t) { last = arr[i]; break; } }
  if (last === null) return { st: "ok", last: null };
  const left = TPCOOL - (t - last);
  return (left > 0.5) ? { st: "cd", left: left, last: last } : { st: "ok", last: last };
}
function applyHeroFrames(t) {
  for (let i = 0; i < PL.length; i++) {
    const p = PL[i];
    const el = document.querySelector('.hero[data-i="' + i + '"]');
    /* 大招/TP 的状态类挂在**这一行**上（长条与 TP 徽标都是头像的兄弟节点） */
    const row = document.querySelector('.hrow[data-i="' + i + '"]') || el;
    if (!el || !row) continue;
    row.classList.remove("ult-ready", "ult-cool", "ult-none", "tp-ok", "tp-cd", "tp-none");
    el.classList.remove("dead");
    const q = posAt(p.npc, t);
    const dead = !!(q && q.hp !== null && q.hp !== undefined && q.hp <= 0);
    if (!q || dead) el.classList.add("dead");
    const us = ultStateOf(p.npc, t), ts = tpStateOf(p.npc, t);
    let tip = p.short.replace(/_/g, " ") + "（" + p.name + "）";
    if (q) tip += " ｜ HP " + (q.hp > 0 ? (q.hp + " / " + (hpMaxAt(p.npc, t) || "?")) : "阵亡");
    else tip += " ｜ 未出场";
    if (us.st === "ready") { row.classList.add("ult-ready"); tip += " ｜ 大招就绪"; }
    else if (us.st === "cool") {
      row.classList.add("ult-cool");
      tip += " ｜ 大招冷却 " + Math.ceil(us.left) + "s" + (us.name ? "（" + us.name + "）" : "");
    } else { row.classList.add("ult-none"); tip += " ｜ 大招：这一时刻没有数据"; }
    if (ts.st === "ok") {
      row.classList.add("tp-ok");
      tip += " ｜ TP 可用" + (ts.last === null ? "（本场无使用记录）"
        : "（最近一次 " + Math.round(t - ts.last) + "s 前）");
    } else {
      row.classList.add("tp-cd");
      tip += " ｜ TP 冷却中约 " + Math.ceil(ts.left) + "s（按 " + Math.round(TPCOOL) + " 秒共享冷却推算）";
    }
    /* TP 徽标上的剩余秒：冷却中才显示数字（可用时留空，图标本身就说明了） */
    const cdEl = row.querySelector(".tpcd");
    if (cdEl) cdEl.textContent = (ts.st === "ok") ? "" : String(Math.max(1, Math.ceil(ts.left)));
    el.title = tip;
  }
}
let cdHeroNpc = "";      // renderCD 正在渲染哪名英雄（cdChip 判"大招"角标时要校验归属）
function cdChip(key, name, icon, known, ivs, isItem, tracked) {
  const state = cdState(ivs, tCur);
  const locked = (known === null || known === undefined || tCur < known);
  const cls = locked ? "lock" : (state === null ? "ready" : "cool");
  const isUlt = !isItem && !!((CD.keys || {})[key] || {}).ult && ultKeyOwnedBy(key, cdHeroNpc);
  let box;
  if (icon && ABICONS[icon]) box = '<img src="' + ABICONS[icon] + '" alt="">';
  else box = '<span class="fb">' + esc(name) + "</span>";
  let st;
  if (locked) st = "未" + (isItem ? "拥有" : "学");
  else if (state !== null) st = "冷却 " + state.toFixed(0) + "s";
  else st = "就绪";
  return '<div class="cd ' + cls + (tracked ? " track" : "") + (isUlt ? " ult" : "")
    + '" title="' + esc(key) + " ｜ " + esc(name) + (isUlt ? "（本英雄的大招）" : "") + '">'
    + '<div class="box">' + box + (state !== null && !locked ? '<div class="rem">' + Math.ceil(state) + "</div>" : "")
    + '</div><div class="cap">' + esc(name.length > 14 ? name.slice(0, 13) + String.fromCharCode(8230) : name) + '</div><div class="st">' + st + "</div></div>";
}
function renderCD() {
  if (selIdx < 0) return;
  const p = PL[selIdx], npc = p.npc;
  cdHeroNpc = npc;
  const board = document.getElementById("cdboard"), note = document.getElementById("cdnote");
  if (!CD) {
    board.innerHTML = '<div class="tip">这场比赛没有技能冷却数据，因此这一栏暂时为空。</div>';
    note.textContent = "";
    return;
  }
  const abs = [];
  (CD.ab[npc] || []).forEach(function (e) { abs.push({ key: e[0], known: (e[2] !== null ? e[2] : e[1]), ivs: e[3] || [], item: false }); });
  (CD.it[npc] || []).forEach(function (e) { abs.push({ key: e[0], known: e[1], ivs: e[3] || [], item: true }); });
  // 关键道具（BKB/刷新球）即使未拥有也显示为锁定态
  (CD.track || []).forEach(function (k) {
    if (abs.some(function (x) { return x.key === k; })) return;
    abs.push({ key: k, known: null, ivs: [], item: true, tracked: true });
  });
  abs.sort(function (a, b) {
    const ta = (CD.track || []).indexOf(a.key) >= 0 ? 0 : (a.item ? 1 : 2);
    const tb = (CD.track || []).indexOf(b.key) >= 0 ? 0 : (b.item ? 1 : 2);
    if (ta !== tb) return ta - tb;
    return (a.known === null ? 1e9 : a.known) - (b.known === null ? 1e9 : b.known);
  });
  const showAll = document.getElementById("tcdall").checked;
  let hidden = 0;
  let html = "";
  abs.forEach(function (x) {
    const info = CD.keys[x.key] || { name: x.key, icon: null };
    const tracked = (CD.track || []).indexOf(x.key) >= 0 || x.tracked;
    if (!showAll && !tracked && (info.cls === "talent" || info.cls === "empty")) { hidden++; return; }
    html += cdChip(x.key, info.name, info.icon, x.known, x.ivs, x.item || info.kind === "item", tracked);
  });
  // TP：录像里只有使用时刻、没有冷却/充能事件 → 按固定的共享冷却推算状态（页面上写明这一点）
  const tp = TPUT[npc] || [];
  const tps = tpStateOf(npc, tCur);
  const lastTp = tps.last;
  const stTp = lastTp === null ? "无使用记录" : ("最近 " + fmt(lastTp) + "（" + Math.round(tCur - lastTp) + "s 前）");
  html += '<div class="cd track ' + (tps.st === "ok" ? "ready" : "cool")
    + '" title="回城卷轴：录像里只有使用时刻，冷却中与否按 ' + Math.round(TPCOOL) + ' 秒共享冷却推算">'
    + '<div class="box">' + (TPICON ? '<img src="' + TPICON + '" alt="">' : '<span class="fb">TP</span>')
    + '</div><div class="cap">TP 卷轴</div>'
    + '<div class="st">' + (tps.st === "ok" ? "可用" : ("冷却 " + Math.ceil(tps.left) + "s")) + "</div></div>";
  board.innerHTML = html;
  note.innerHTML = "每一段灰色 = 技能/道具的一次真实冷却，灰色上的秒数就是那一刻的<b>剩余冷却时间</b>"
    + "（直接取自录像里的技能状态，已含等级与减 CD 效果）。标了 <b>大</b> 角标的是<b>这名英雄的大招</b>。"
    + "<b>回城卷轴（TP）</b>是充能制，录像里没有它的冷却/充能事件，所以这里的可用/冷却状态是按"
    + "那只固定的共享冷却（<b>" + Math.round(TPCOOL) + "</b> 秒）从使用时刻<b>推算</b>的"
    + "（这名英雄：" + stTp + "）；飞鞋等其它传送手段会占用同一只共享冷却，因此只作参考。"
    + "还没学习或还没拥有的技能/道具显示为锁定态（灰 + 🔒）。"
    + (hidden > 0 ? "另有 <b>" + hidden + "</b> 个天赋/空槽栏位按默认隐藏（勾『显示未学/未拥有』可展开）。" : "")
    + " 名字里带下划线的（如 <code>BlackDragon_Fireball</code>）是<b>从中立生物处获得的技能</b>，前缀就是来源单位。";
}
function clearSel() { if (selIdx >= 0) selectHero(selIdx); }

/* ═══════════════ 战斗回顾（复现游戏的 Fight Recap 面板） ═══════════════
   游戏侧依据：`panorama/layout/hud/dota_hud_fightrecap.xml` —— 七段
   （死亡 / 金钱变化 / 经验变化 / 造成伤害 / 总治疗量 / 已使用的技能 / 已使用的物品），
   每段一行「天辉容器 ｜ 中间两队合计 ｜ 夜魇容器」，逐人一根条；
   技能与物品段是「图标 + x次数」。团战由构建期自动识别（见 analysis/q7_fights.py）。
   比游戏多一层：完整版带**按技能拆分的伤害**（我们的 combat_log 每条伤害都带 inflictor）。 */
const FIGHTS = ((DATA.fights || {}).list) || [];
const FKEYS = ((DATA.fights || {}).keys) || [];
const FICONS = DATA.fighticons || [];      // 与 FKEYS 一一对应（独立的一套 18px 小图标）
let recapIdx = -1;          // 当前打开的波次；-1 = 没打开
function fightAt(t) {       // 包含当前时刻的波次；落在波次之间就取"下一波"
  if (!FIGHTS.length) return -1;
  for (let i = 0; i < FIGHTS.length; i++) {
    if (t >= FIGHTS[i].t0 && t <= FIGHTS[i].t1) return i;
  }
  for (let i = 0; i < FIGHTS.length; i++) { if (FIGHTS[i].t0 > t) return i; }
  return FIGHTS.length - 1;
}
function heroAvatar(i, cls) {
  const im = ICONS[PL[i].short];
  return im ? '<img class="' + (cls || "av") + '" src="' + im + '" alt="">' : '<span class="' + (cls || "av") + '"></span>';
}
function recapIconTag(kidx) {
  const cls = FICONS[kidx];
  return cls ? '<i class="di rci ' + cls + '"></i>' : "";
}
function buildFightLane() {
  const lane = document.getElementById("fightLane");
  if (!lane) return;
  lane.innerHTML = "";
  const span = Math.max(1, T1 - T0);
  FIGHTS.forEach(function (f, i) {
    const el = document.createElement("div");
    el.className = "fbar";
    el.style.left = ((f.t0 - T0) / span * 100) + "%";
    el.style.width = Math.max(0.6, (f.t1 - f.t0) / span * 100) + "%";
    el.title = "第 " + f.i + " 波 ｜ " + fmt(f.t0, true) + "~" + fmt(f.t1, true)
      + " ｜ 阵亡 " + f.n + " 人（天辉 " + f.tot.d[0] + " / 夜魇 " + f.tot.d[1] + "）";
    el.innerHTML = "<b>" + f.n + "</b>";
    el.onclick = function () { openRecap(i); };
    lane.appendChild(el);
  });
}
function markFightLane() {
  const lane = document.getElementById("fightLane");
  if (!lane) return;
  Array.prototype.forEach.call(lane.children, function (el, i) {
    el.classList.toggle("on", i === recapIdx);
  });
}
function recapNum(per, i) { return per[i] || 0; }
/* 一段数值（金钱/经验/伤害/治疗）：两边各列各人 + 中间两队合计 */
function rcNumSeg(title, key, signed) {
  const seg = FIGHTS[recapIdx];
  const per = {};
  (seg[key] || []).forEach(function (e) { per[e[0]] = e[1]; });
  const val = function (i) { return recapNum(per, i); };
  const mx = Math.max(1, PL.map(function (p, i) { return Math.abs(val(i)); }).reduce(function (a, b) {
    return Math.max(a, b);
  }, 0));
  const sign = function (v) {
    const cls = !signed ? "" : (v > 0 ? " pos" : (v < 0 ? " neg" : ""));
    return '<span class="val' + cls + '">' + (signed && v > 0 ? "+" : "") + Math.round(v).toLocaleString("en-US") + "</span>";
  };
  const side = function (team) {
    const rows = PL.map(function (p, i) { return i; })
      .filter(function (i) { return PL[i].team === team && val(i); })
      .sort(function (a, b) { return Math.abs(val(b)) - Math.abs(val(a)); });
    return '<div class="rcside' + (team === 3 ? " dire" : "") + '" data-team="' + team + '">'
      + (rows.length ? rows.map(function (i) {
        const w = Math.max(2, Math.round(Math.abs(val(i)) / mx * 72));
        return '<div class="rcent ' + (team === 2 ? "t2" : "t3") + '">' + heroAvatar(i)
          + '<span class="bar" style="width:' + w + 'px"></span>' + sign(val(i)) + "</div>";
      }).join("") : '<div class="tip">—</div>')
      + "</div>";
  };
  return rcSeg(title, side(2)
    + '<div class="rcmid"><span class="dr">' + fmtNum(seg.tot[key][0]) + '</span><br>'
    + '<span class="dd">' + fmtNum(seg.tot[key][1]) + "</span></div>"
    + side(3));
}
function rcSeg(title, inner, sub) {
  return '<div class="rcseg"><div class="rchead">' + title
    + (sub ? ' <span>' + sub + "</span>" : "") + "</div>" + inner + "</div>";
}
function rcDeaths() {
  const seg = FIGHTS[recapIdx];
  const per = {};
  (seg.d || []).forEach(function (e) { per[e[0]] = e[1]; });
  const side = function (team) {
    const rows = PL.map(function (p, i) { return i; })
      .filter(function (i) { return PL[i].team === team && per[i]; });
    return '<div class="rcside' + (team === 3 ? " dire" : "") + '">'
      + (rows.length ? rows.map(function (i) {
        return '<span class="rcdie">' + heroAvatar(i) + '<span class="x">×' + per[i] + "</span></span>";
      }).join("") : '<div class="tip">—</div>') + "</div>";
  };
  return rcSeg("① 阵亡", '<div class="rcbody3">' + side(2)
    + '<div class="rcmid"><span class="dr">' + seg.tot.d[0] + '</span><br>'
    + '<span class="dd">' + seg.tot.d[1] + "</span></div>" + side(3) + "</div>");
}
function rcIcons(key, title, sub) {
  const seg = FIGHTS[recapIdx];
  const per = {};
  (seg[key] || []).forEach(function (e) { per[e[0]] = e[1]; });
  const side = function (team) {
    const rows = PL.map(function (p, i) { return i; })
      .filter(function (i) { return PL[i].team === team && per[i]; });
    if (!rows.length) return '<div class="rcside' + (team === 3 ? " dire" : "") + '"><div class="tip">—</div></div>';
    return '<div class="rcside' + (team === 3 ? " dire" : "") + '">'
      + rows.map(function (i) {
        return '<div class="rchero">' + heroAvatar(i) + '<div class="list">'
          + per[i].map(function (e) {
            return '<span class="rcico">' + recapIconTag(e[0]) + "×" + e[1] + "</span>";
          }).join("") + "</div></div>";
      }).join("") + "</div>";
  };
  return rcSeg(title, '<div class="rcbody3">' + side(2) + '<div class="rcmid d">—</div>' + side(3) + "</div>", sub);
}
/* 比游戏多的一层：按技能拆分的伤害（每人前 6 项） */
function rcDba() {
  const seg = FIGHTS[recapIdx];
  if (!seg.dba) return "";
  const side = function (team) {
    const rows = PL.map(function (p, i) { return i; })
      .filter(function (i) { return PL[i].team === team && (seg.dba || []).some(function (e) { return e[0] === i; }); });
    return '<div class="rcside' + (team === 3 ? " dire" : "") + '">'
      + rows.map(function (i) {
        const items = (seg.dba.filter(function (e) { return e[0] === i; })[0] || [])[1] || [];
        return '<div class="rchero">' + heroAvatar(i) + '<div class="list"><span class="k">'
          + PL[i].short.replace(/_/g, " ") + "</span> "
          + items.map(function (e) {
            return '<span class="rcico">' + recapIconTag(e[0]) + " " + Math.round(e[1]).toLocaleString("en-US") + "</span>";
          }).join("") + "</div></div>";
      }).join("") + "</div>";
  };
  return rcSeg("⑧ 按技能拆分的伤害 <span>（游戏面板没有这一层：录像里每条伤害都标了来源技能）</span>",
    '<div class="rcbody3">' + side(2) + '<div class="rcmid d">—</div>' + side(3) + "</div>");
}
function renderRecap() {
  const seg = FIGHTS[recapIdx];
  const head = document.getElementById("recapHead");
  if (!seg) {
    if (head) head.textContent = FIGHTS.length ? "这场比赛没有识别到团战" : "这一场没有团战数据（切片较老）";
    const b = document.getElementById("recapBody");
    if (b) b.innerHTML = "";
    markFightLane();
    return;
  }
  if (head) {
    head.innerHTML = "<b>第 " + seg.i + " / " + FIGHTS.length + " 波</b> ｜ "
      + fmt(seg.t0, true) + " ~ " + fmt(seg.t1, true) + "（" + Math.round(seg.t1 - seg.t0) + " 秒）"
      + " ｜ 阵亡 <b>" + seg.n + "</b> 人（天辉 " + seg.tot.d[0] + " / 夜魇 " + seg.tot.d[1] + "）"
      + '<br><span class="tip">每一段左边是天辉、右边是夜魇，中间是两队的合计；条的长度＝数值大小。'
      + "团战由录像自动识别（12 秒内 ≥2 名英雄阵亡算一波）。</span>";
  }
  document.getElementById("recapBody").innerHTML =
    rcDeaths()
    + rcNumSeg("② 金钱变化情况", "g", true)
    + rcNumSeg("③ 经验变化情况", "x", true)
    + rcNumSeg("④ 造成伤害", "dm", false)
    + rcNumSeg("⑤ 总治疗量", "hl", false)
    + rcIcons("ab", "⑥ 已使用的技能")
    + rcIcons("it", "⑦ 已使用的物品")
    + rcDba();
  markFightLane();
}
function openRecap(idx) {
  if (!FIGHTS.length) { alert("这一场没有识别到团战（或切片较老，需要重跑切片）"); return; }
  /* 先取消英雄选中（它会重排右栏面板），再统一设一次显示状态：
     ★ 顺序反了的话 paneList 会被重新显示出来，回顾面板被挤到列表下面（第一版实测如此）。 */
  if (selIdx >= 0) selectHero(selIdx);
  recapIdx = (idx === undefined || idx === null) ? fightAt(tCur) : idx;
  if (recapIdx < 0) recapIdx = 0;
  document.getElementById("paneList").style.display = "none";
  document.getElementById("paneHero").style.display = "none";
  document.getElementById("paneRecap").style.display = "";
  document.getElementById("btnRecap").classList.add("active");
  const f = document.getElementById("tcfollow");
  if (f) { f.checked = true; }
  renderRecap();
  draw();
}
function closeRecap() {
  recapIdx = -1;
  document.getElementById("paneRecap").style.display = "none";
  /* 只有"没选中英雄"时才把默认列表放回来 */
  document.getElementById("paneList").style.display = (selIdx < 0) ? "" : "none";
  document.getElementById("btnRecap").classList.remove("active");
  markFightLane();
}
function recapJump(d) {
  if (!FIGHTS.length) return;
  const f = document.getElementById("tcfollow");
  if (f) { f.checked = false; }
  recapIdx = Math.max(0, Math.min(FIGHTS.length - 1, recapIdx + d));
  renderRecap();
  commit(FIGHTS[recapIdx].t0 + 1);
}
function recapFollowTick() {   // 播放/拖动时：跟随当前时刻换波次
  if (recapIdx < 0) return;
  const f = document.getElementById("tcfollow");
  if (!f || !f.checked) return;
  const i = fightAt(tCur);
  if (i >= 0 && i !== recapIdx) { recapIdx = i; renderRecap(); }
}
function buildTable() {
  const tb = document.querySelector("#tbl tbody");
  tb.innerHTML = "";
  PL.forEach(function (p, i) {
    const tr = document.createElement("tr");
    tr.setAttribute("data-i", i);
    tr.innerHTML = '<td><span class="dot" style="background:' + teamColor(p.team) + '"></span>' + p.short.replace(/_/g, " ")
      + '</td><td class="' + (p.team === 2 ? "dr" : "dd") + '">' + (p.team === 2 ? "天辉" : "夜魇") + "</td>"
      + '<td id="c-k-' + i + '"></td><td id="c-d-' + i + '"></td><td id="c-a-' + i + '"></td>'
      + '<td id="c-lh-' + i + '"></td><td id="c-dn-' + i + '"></td>'
      + '<td id="c-nw-' + i + '"></td><td id="c-cg-' + i + '"></td><td id="c-cx-' + i + '"></td><td id="c-hp-' + i + '"></td>';
    tr.onclick = function () { selectHero(i); };
    tr.onmouseenter = function () { hoverRow(i); };
    tr.onmouseleave = function () { hoverRow(-1); };
    tb.appendChild(tr);
  });
}
function setCell(id, v, colorize) {
  const el = document.getElementById(id); if (!el) return;
  el.textContent = v;
  el.style.color = colorize ? (String(v).charAt(0) === "-" ? "#f85149" : (String(v).charAt(0) === "+" ? "#3fb950" : "")) : "";
}

/* ═══════════════ 顶部 + 表格刷新 ═══════════════ */
function refresh(t) {
  document.getElementById("vClock").textContent = fmt(t, true);
  const ph = t < 0 ? "出门/选人期（号角前）" : (t <= 600 ? "对线期" : (t <= 1200 ? "中期" : "后期"));
  document.getElementById("vPhase").textContent = ph + " ｜ 第 " + Math.floor(Math.max(0, t) / 60) + " 分钟";
  const nw = diffAt("nw", t), cg = diffAt("cg", t), cx = diffAt("cx", t);
  const put = function (id, v) {
    const el = document.getElementById(id);
    el.textContent = (v === null ? "—" : fmtNum(v));
    el.className = "v " + (v === null ? "zero" : (v > 0 ? "rad" : (v < 0 ? "dir" : "zero")));
  };
  put("vNw", nw); put("vCg", cg); put("vCx", cx);

  // 状态胜率（派生模型：净值差 + 经验差 + 时刻）
  const pw = winProb(t, nw, cx);
  const wpEl = document.getElementById("vWp");
  if (pw === null) {
    wpEl.textContent = "—";
    wpEl.className = "v zero";
    document.getElementById("vWpNote").textContent = WP ? "这一时刻没有可用的胜率数据" : "本页没有附带胜率数据";
  } else {
    wpEl.textContent = fmtPct(pw);
    wpEl.className = "v " + (pw > 0.52 ? "rad" : (pw < 0.48 ? "dir" : "zero"));
    document.getElementById("vWpNote").textContent = "按历史同局面统计的参考值，不是本场预测";
  }

  for (let i = 0; i < PL.length; i++) {
    const p = PL[i], k = KDA[p.npc];
    setCell("c-k-" + i, k.k); setCell("c-d-" + i, k.d); setCell("c-a-" + i, k.a);
    setCell("c-lh-" + i, k.lh); setCell("c-dn-" + i, k.dn);
    const a = valAt(NW, p.npc, t), b = valAt(CG, p.npc, t), c = valAt(CX, p.npc, t);
    setCell("c-nw-" + i, a === null ? "—" : Math.round(a).toLocaleString("en-US"));
    setCell("c-cg-" + i, b === null ? "—" : Math.round(b).toLocaleString("en-US"));
    setCell("c-cx-" + i, c === null ? "—" : Math.round(c).toLocaleString("en-US"));
    const q = posAt(p.npc, t);
    const hpEl = document.getElementById("c-hp-" + i);
    /* ★ 血量缺失（采样格没有这一秒）必须显示「—」，不能落到「阵亡」分支：
       JS 里 `null <= 0` 是 true，写 `q.hp <= 0` 会把"没数据"显示成"阵亡"（开场前几秒常见）。 */
    if (!q) { hpEl.textContent = "未出场"; hpEl.style.color = "#8b949e"; }
    else if (q.hp === null || q.hp === undefined) { hpEl.textContent = "—"; hpEl.style.color = "#8b949e"; }
    else if (q.hp <= 0) { hpEl.textContent = "阵亡"; hpEl.style.color = "#f85149"; }
    else {
      const mx = hpMaxAt(p.npc, t);
      hpEl.textContent = q.hp + (mx ? " / " + mx : "");
      hpEl.style.color = (mx && q.hp / mx < 0.3) ? "#d29922" : "";
    }
    const hb = document.querySelector('.hero[data-i="' + i + '"]');
    if (hb) {
      const dead = (!q || q.hp <= 0);
      hb.classList.toggle("dead", dead);
      const bar = hb.querySelector(".hpbar");
      if (bar) {
        const mx = hpMaxAt(p.npc, t);
        const ratio = (q && q.hp > 0 && mx) ? Math.max(0.02, Math.min(1, q.hp / mx)) : 0;
        bar.style.width = (ratio * 100) + "%";
        bar.style.background = dead ? "#f85149" : (ratio < 0.3 ? "#d29922" : "#3fb950");
      }
    }
  }
  applyHeroFrames(t);      // 头像框：大招 / TP 状态（含 title 与阵亡置灰）
  recapFollowTick();       // 战斗回顾打开且勾了"跟随当前时刻"时，跟着换波次
  document.getElementById("biglabel").textContent = "全场进度 = " + fmt(tBig, true)
    + "（" + Math.round(tBig) + "s / " + T1 + "s）";
  document.getElementById("smalllabel").textContent = "微调 = " + (sVal > 0 ? "+" : "") + sVal
    + "s → 当前时刻 " + fmt(tCur, true) + (Math.abs(sVal) < 0.01 ? "（已归零）" : "");
  if (selIdx >= 0) { renderHeroHead(); renderDetail(); renderCD(); }
  paintSpark();
  draw();
}

/* ═══════════════ 火花线 ═══════════════ */
const sp = document.getElementById("spark"), sctx = sp.getContext("2d");
let sparkCache = null, sparkCacheKey = "", sparkMax = 1;
function sparkSeries() {
  if (entDiff === "nw") return DIFF.nw;
  if (entDiff === "cg") return DIFF.cg;
  return DIFF.cx;
}
function sparkXY(W, H) {
  const a = sparkSeries();
  let mx = 1;
  const SPL = a.length;
  for (let k = 0; k < SPL; k++) { const v = Math.abs(a[k] || 0); if (v > mx) mx = v; }
  sparkMax = mx;
  return { a: a, mx: mx, n: SPL, X: function (k) { return k / (SPL - 1) * W; }, Y: function (v) { return H / 2 - (v / mx) * (H / 2 - 8); } };
}
/* 静态部分（底、面积、折线、击杀竖线）缓存到离屏画布 —— 播放时每帧只重画播放头，避免卡顿 */
function sparkBuildCache(W, H) {
  const t = sparkXY(W, H), a = t.a;
  const c = document.createElement("canvas"); c.width = W; c.height = H;
  const g = c.getContext("2d");
  g.fillStyle = "#0d1117"; g.fillRect(0, 0, W, H);
  g.strokeStyle = "#30363d"; g.lineWidth = 1;
  g.beginPath(); g.moveTo(0, H / 2); g.lineTo(W, H / 2); g.stroke();
  g.beginPath(); g.moveTo(0, H / 2);
  for (let k = 0; k < t.n; k++) g.lineTo(t.X(k), t.Y(a[k] || 0));
  g.lineTo(W, H / 2); g.closePath();
  g.fillStyle = "rgba(227,179,65,.18)"; g.fill();
  g.beginPath();
  for (let k = 0; k < t.n; k++) { const x = t.X(k), y = t.Y(a[k] || 0); if (k === 0) g.moveTo(x, y); else g.lineTo(x, y); }
  g.strokeStyle = "#e3b341"; g.lineWidth = 1.4; g.stroke();
  g.strokeStyle = "rgba(248,81,73,.35)";
  KILLSX.forEach(function (kk) { const k = kOfE(kk[0]); if (k === null) return;
    g.beginPath(); g.moveTo(t.X(k), 4); g.lineTo(t.X(k), H - 4); g.stroke(); });
  return c;
}
function paintSpark() {
  const W = sp.width, H = sp.height;
  if (!sparkCache || sparkCacheKey !== entDiff) {
    sparkCache = sparkBuildCache(W, H); sparkCacheKey = entDiff;
  }
  const t = sparkXY(W, H);
  sctx.clearRect(0, 0, W, H);
  sctx.drawImage(sparkCache, 0, 0);
  const kc = kOfE(tCur);
  if (kc !== null) {
    sctx.strokeStyle = "#fff"; sctx.lineWidth = 2;
    sctx.beginPath(); sctx.moveTo(t.X(kc), 0); sctx.lineTo(t.X(kc), H); sctx.stroke();
    sctx.fillStyle = "#fff"; sctx.beginPath(); sctx.arc(t.X(kc), t.Y(t.a[kc] || 0), 3.4, 0, Math.PI * 2); sctx.fill();
  }
  document.getElementById("spTitle").textContent =
    (entDiff === "nw" ? "净值差" : (entDiff === "cg" ? "累计金币差" : "经验差"))
    + " ｜ 峰值 ±" + Math.round(sparkMax).toLocaleString("en-US");
}
let sparkDrag = false;
function sparkSeek(e) {
  const r = sp.getBoundingClientRect();
  const f = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
  commit(T0 + f * (T1 - T0));
}
sp.onmousedown = function (e) { sparkDrag = true; sparkSeek(e); e.preventDefault(); };
sp.onmousemove = function (e) { if (sparkDrag) sparkSeek(e); };
window.addEventListener("mouseup", function () { sparkDrag = false; });
function setEntDiff(v) {
  entDiff = v;
  document.querySelectorAll(".ebtn").forEach(function (b) { b.classList.toggle("active", b.getAttribute("data-e") === v); });
  paintSpark();
}
/* ═══════════════ 双时间轴 ═══════════════ */
const big = document.getElementById("big"), small = document.getElementById("small");
big.min = T0; big.max = T1; big.value = 0; big.step = 1;
let smallDragging = false, smallDirty = false;

function apply() {                     // 由 tBig/sVal 推出 tCur 并刷新
  tCur = Math.max(T0, Math.min(T1, tBig + sVal));
  refresh(tCur);
}
function commit(t) {                   // 绝对定位（大条推进到位，小条归零）
  tBig = Math.max(T0, Math.min(T1, t));
  sVal = 0; small.value = 0; big.value = Math.round(tBig);
  apply();
}
function setBigFromSlider() {          // 用户直接拖大条
  tBig = parseFloat(big.value); sVal = 0; small.value = 0; apply();
}
/* 拖动时跟着滑块走的时间气泡：反馈"拖进度条看不到当前游戏时间"。
   气泡按**当前时刻**对齐大条（大条覆盖全场，位置就是时间本身），位置在滑块正上方。 */
const seekbub = document.getElementById("seekbub");
let bubTimer = null;
function showSeekBub() {
  if (!seekbub) return;
  const tl = document.getElementById("timeline");
  if (!tl) return;
  const r = big.getBoundingClientRect(), tr = tl.getBoundingClientRect();
  const f = (T1 > T0) ? Math.max(0, Math.min(1, (tCur - T0) / (T1 - T0))) : 0;
  const x = (r.left - tr.left) + f * r.width;
  seekbub.style.left = Math.max(36, Math.min(tr.width - 36, x)) + "px";
  seekbub.style.top = Math.max(0, (r.top - tr.top) - 27) + "px";
  seekbub.textContent = fmt(tCur, true);
  seekbub.classList.add("on");
  if (bubTimer) { clearTimeout(bubTimer); bubTimer = null; }
}
function hideSeekBub(delay) {
  if (bubTimer) clearTimeout(bubTimer);
  bubTimer = setTimeout(function () {
    if (seekbub) seekbub.classList.remove("on");
  }, (delay === undefined) ? 500 : delay);
}
big.oninput = function () { setBigFromSlider(); showSeekBub(); };
big.onchange = function () { setBigFromSlider(); hideSeekBub(420); };
big.onpointerdown = showSeekBub;

small.oninput = function () {          // 拖小条：实时联动（大条滑块跟着小幅移动 = tCur）
  smallDragging = true; smallDirty = true;
  sVal = parseFloat(small.value);
  tCur = Math.max(T0, Math.min(T1, tBig + sVal));
  big.value = Math.round(tCur);        // 实时反馈：大条同步"小幅"移动（±60s 在整场条上极小）
  refresh(tCur);
  showSeekBub();
};
function smallCommit() {               // 松手提交：大条推进"滑过的量"，小条瞬时归零
  if (!smallDragging || !smallDirty) return;
  smallDragging = false; smallDirty = false;
  commit(tBig + parseFloat(small.value));
  hideSeekBub(420);
}
small.onchange = smallCommit;
small.onpointerup = smallCommit;
small.onmouseup = smallCommit;
small.ontouchend = smallCommit;
window.addEventListener("pointerup", function () { setTimeout(smallCommit, 0); });

/* ═══════════════ 时间轴：重大事件时间戳（上=天辉有利 / 下=夜魇有利） ═══════════════ */
const TL = DATA.tl || [];
const KIND_NAME = ["击杀", "塔", "兵营", "基地", "肉山"];
let evEls = [];          // [{el, t, side}]
function fmtTL(t) { const v = Math.round(t); return Math.floor(Math.abs(v) / 60) + ":" + String(Math.abs(v) % 60).padStart(2, "0"); }
const ICONSQ = DATA.iconsq || {}, TLICONS = DATA.tlicons || {};
function tlIconSrc(e) {
  const ic = e[4] || "", own = e[5] || 0;
  if (ic.indexOf("h:") === 0) return ICONSQ[ic.slice(2)] || null;
  if (ic === "roshan") return TLICONS.roshan || null;
  return TLICONS[ic + (own === 2 ? "_r" : "_d")] || TLICONS[ic + "_r"] || null;
}
function buildTimelineEvents() {
  const up = document.getElementById("evUp"), dn = document.getElementById("evDn");
  up.innerHTML = ""; dn.innerHTML = "";
  evEls = [];
  const showTime = document.getElementById("showTL").checked;
  const onlyBld = document.getElementById("tlBld").checked;
  const W = Math.max(320, up.clientWidth || 900);
  const span = Math.max(1, T1 - T0);
  /* 层数按屏幕高度自适应：小屏少留几条泳道，保证"地图+头像+时间轴+右栏"同屏。
     ≥1000px 视口 → 4 层（团战也不挤）；780~1000 → 3 层；更矮 → 2 层。 */
  const VH = (typeof window !== "undefined" && window.innerHeight) ? window.innerHeight : 900;
  const ROWH = showTime ? 48 : 31;                                    // 每层高度（带时间戳时更高）
  const MH = showTime ? 42 : 30;                                      // 标记自身高度（图标 26 + 边框内边距 4 + 时间戳 12）
  const SP = showTime ? 46 : 29;                                      // 同层最小水平间距
  const NROW = showTime ? 3 : (VH >= 1000 ? 4 : (VH >= 780 ? 3 : 2)); // 每个事件最多几层错开
  up.style.height = dn.style.height = (4 + (NROW - 1) * ROWH + MH) + "px";
  const mk = function (host, e, side, rows) {
    const bld = e[2] !== 0, own = e[5] || 0;
    const x = (e[0] - T0) / span * W;
    const tick = document.createElement("div");
    tick.className = "evt" + (bld ? " b" : "");
    tick.style.left = (e[0] - T0) / span * 100 + "%";
    tick.style.background = own === 2 ? "#4aa564" : (own === 3 ? "#d24b4b" : "#8b949e");
    tick.style.height = (bld ? 13 : 8) + "px";
    if (side === 2) { tick.style.bottom = "0"; } else { tick.style.top = "0"; }
    tick.title = fmtTL(e[0]) + "  " + KIND_NAME[e[2]] + "  " + e[3];
    tick.onclick = function () { jumpTo(e[0]); };
    host.appendChild(tick);
    if (onlyBld && !bld) return;
    let row = -1, best = 1e9;
    for (let r = 0; r < rows.length; r++) {
      if (x - rows[r] >= SP && rows[r] < best) { best = rows[r]; row = r; }
    }
    if (row < 0) { row = 0; for (let r = 1; r < rows.length; r++) if (rows[r] < rows[row]) row = r; }
    rows[row] = x + SP;
    const m = document.createElement("div");
    m.className = "evm r" + (own === 2 ? "2" : (own === 3 ? "3" : "0")) + (bld ? " bld" : "");
    m.style.left = (e[0] - T0) / span * 100 + "%";
    const off = 4 + row * ROWH;
    if (side === 2) { m.style.bottom = off + "px"; } else { m.style.top = off + "px"; }
    const src = tlIconSrc(e);
    const im = document.createElement("img");
    im.className = "ico";
    im.alt = e[3];
    if (src) { im.src = src; } else { im.style.visibility = "hidden"; }
    const tm = document.createElement("span");
    tm.className = "t";
    tm.textContent = fmtTL(e[0]);
    if (side === 2) { if (showTime) m.appendChild(tm); m.appendChild(im); }
    else { m.appendChild(im); if (showTime) m.appendChild(tm); }
    m.title = fmtTL(e[0]) + "  " + KIND_NAME[e[2]] + " ｜ " + e[3]
      + (own === 2 ? "（天辉的）" : (own === 3 ? "（夜魇的）" : ""));
    m.onclick = function () { jumpTo(e[0]); };
    host.appendChild(m);
    evEls.push({ el: m, t: e[0] });
  };
  // 每条泳道各自分层：初值给 -1e9（0 会让开头的几个事件挤在同一层）
  const NEG = -1e9;
  const ru = [], rd = [];
  for (let i = 0; i < NROW; i++) { ru.push(NEG); rd.push(NEG); }
  TL.forEach(function (e) {
    if (e[1] === 2) { mk(up, e, 2, ru); } else { mk(dn, e, 3, rd); }
  });
  markNear();
  fitCanvas();          // 时间轴高度变了 → 地图可用高度跟着变，重新取正方形边长
}
function jumpTo(t) {
  playing = false;
  document.getElementById("play").textContent = "▶ 播放";
  commit(t);
}
function markNear() {
  for (let i = 0; i < evEls.length; i++) {
    const d = Math.abs(evEls[i].t - tCur);
    evEls[i].el.classList.toggle("near", d <= 25);
  }
}
window.addEventListener("resize", function () { buildTimelineEvents(); fitCanvas(); });

/* ═══════════════ 播放 ═══════════════ */
function tick(ts) {
  if (!playing) return;
  if (_lastFrame < 0) _lastFrame = ts;
  const dt = Math.min(0.25, Math.max(0, (ts - _lastFrame) / 1000)) * speed;
  _lastFrame = ts;
  let nt = tBig + dt;
  if (nt >= T1) { nt = T1; playing = false; setPlayBtn(); }
  commit(nt);
  if (playing) requestAnimationFrame(tick);
}
function setPlayBtn() { document.getElementById("play").textContent = playing ? "⏸ 暂停" : "▶ 播放"; }
document.getElementById("play").onclick = function () {
  playing = !playing; _lastFrame = -1; setPlayBtn();
  if (playing) requestAnimationFrame(tick);
};
function setSpeed(v) {
  speed = v;
  document.querySelectorAll(".spd").forEach(function (b) { b.classList.toggle("active", +b.getAttribute("data-s") === v); });
}
function step(d) { playing = false; setPlayBtn(); commit(tBig + d); }
window.addEventListener("keydown", function (e) {
  if (e.target && (e.target.tagName === "INPUT")) return;
  if (e.code === "Space") { e.preventDefault(); document.getElementById("play").click(); }
  else if (e.code === "ArrowLeft") step(-5);
  else if (e.code === "ArrowRight") step(5);
});
document.getElementById("mop").oninput = function () {
  document.getElementById("moppct").textContent = this.value + "%"; draw();
};
document.getElementById("showBld").onchange = draw;
document.getElementById("showWard").onchange = draw;
document.getElementById("showSmoke").onchange = draw;
document.getElementById("showRoute").onchange = draw;
document.getElementById("showName").onchange = draw;
document.getElementById("dwrap").onscroll = detOnScroll;    // 虚拟滚动：滚到哪画哪

/* ═══════════════ 启动 ═══════════════ */
(function init() {
  /* ★ 文案填充一律走 setTxt：**少一个元素也不能让初始化中断**。
     （踩过一次：改头部文案时删掉了 #mid，init 第一句就抛 TypeError →
     后面的头像条 / 时间轴 / 地图尺寸全部没建出来，页面看起来"英雄没了、地图变小了"。） */
  const setTxt = function (id, txt) {
    const el = document.getElementById(id);
    if (el) el.textContent = txt;
  };
  try {
    setTxt("mid", DATA.mid);
    const a = DIFF.nw[D - 1], b = DIFF.cg[D - 1];
    if (a !== null && b !== null) {
      setTxt("gapEnd", Math.abs(a - b).toLocaleString("en-US")
        + "（净值 " + fmtNum(a) + " vs 累计 " + fmtNum(b) + "）");
    }
    if (WP) {
      setTxt("wpN", WP.n_match + " 场比赛统计");
      setTxt("wpAuc", "区分度 " + (WP.auc_test === null ? "—" : WP.auc_test.toFixed(2))
        + "（1.00 = 完全分得开、0.50 = 与瞎猜无异）");
    } else {
      setTxt("wpN", "无数据");
      setTxt("wpAuc", "本页未附带胜率统计");
    }
    if (DATA.lite) {
      const ln = document.getElementById("liteNote");
      if (ln) ln.style.display = "";
      setTxt("liteStep", String(STEP));
    }
  } catch (e) {
    if (typeof console !== "undefined" && console.warn) console.warn("文案填充出错（不影响交互）：", e);
  }
  /* 交互初始化：这一段的成败决定页面能不能用，必须放在文案之后单独执行 */
  buildAvatars(); buildTable(); buildTimelineEvents(); buildFightLane(); fitCanvas();
  commit(0);          // 默认停在 0:00（号角）；往前拖 = 出门期（-1:30 起）
  /* 首帧之后再量一次：字体/图片加载完，左栏可用高度会变（避免地图第一次就取错尺寸） */
  requestAnimationFrame(function () { fitCanvas(); });
})();
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Q7 单文件 viewer 生成器")
    ap.add_argument("match", nargs="+", help="match_id（可多个）")
    ap.add_argument("--out", default=REVIEW)
    ap.add_argument("--lite", action="store_true",
                    help="精简版：地图 512px、逐秒数据抽稀、不含 combat log 明细与技能图标")
    ap.add_argument("--step", type=int, default=3, help="lite 的抽稀步长（秒）")
    ap.add_argument("--no-fetch", action="store_true",
                    help="不给明细图标联网取图（只用本地 assets + 已有缓存）")
    args = ap.parse_args()
    for mid in args.match:
        build(mid, args.out, lite=args.lite, step=args.step, fetch_icons=not args.no_fetch)


if __name__ == "__main__":
    main()
