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
第二步（本版）：① 点英雄 → 右栏改该英雄 **±10s combat log**（4 toggle：给出/收到 modifier、造成/收到伤害）
              ② 下方 **技能 CD**（三态：冷却中灰+剩余秒 / 未学未拥有 / 就绪；重点追踪 BKB / 刷新球 / TP）
              ③ 顶部 **状态胜率**（派生模型，绝不偷看结果）
"""

import argparse
import base64
import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q7DIR = os.path.join(ROOT, "analysis", "output_q7")
REVIEW = os.path.join(ROOT, "analysis", "output_review")
AB_ICON_DIR = os.path.join(ROOT, "opendota_analysis", "assets", "ability_icons")
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


def build(mid, outdir, lite=False, step=3):
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

    # ---- 图标（短名 → data URI；缺失则该英雄退化为色块）----
    icons = {}
    for p in dat["players"]:
        p["name"] = clean(p["name"])
        fp = os.path.join(ICON_DIR, p["short"] + ".png")
        if os.path.exists(fp):
            icons[p["short"]] = "data:image/png;base64," + b64_png_opt(fp, 48 if lite else 64,
                                                                    48 if lite else 64)

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
        "dnames": ([] if lite else dat.get("detail_names", [])),
        "cd": cd,
        "tp": dat.get("tp", {}),
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

    rname = m.get("radiant_team") or "天辉（stats.db 无队名）"
    dname = m.get("dire_team") or "夜魇（stats.db 无队名）"
    win = m.get("radiant_win")
    win_txt = "—" if win is None else ("天辉胜" if win else "夜魇胜")
    league = m.get("league_id")

    html = HTML_TMPL
    for k, v in (
        ("@@BLOB@@", blob),
        ("@@MAP@@", "data:image/png;base64," + map_b64),
        ("@@MID@@", str(dat["match_id"])),
        ("@@LEAGUE@@", str(league)),
        ("@@RNAME@@", rname),
        ("@@DNAME@@", dname),
        ("@@WIN@@", win_txt),
        ("@@DUR@@", dur(dat["t1"])),
        ("@@DB@@", m.get("db", "")),
        ("@@ENDRULE@@", m.get("end_source", "")),
        ("@@PAUSE@@", "%.1f" % (m.get("clock", {}).get("pause_sec_total") or 0)),
        ("@@DELTA@@", "%.2f" % (m.get("clock", {}).get("delta_file") or 0)),
        ("@@ANCHORS@@", str(m.get("clock", {}).get("anchors", 0))),
        ("@@SRCJSON@@", "analysis/output_q7/q7_%s.json" % mid),
    ):
        html = html.replace(k, v)

    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, "q7_replay_%s%s.html" % (mid, "_lite" if lite else ""))
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote %s  size=%.2f MB  (icons %d)" % (os.path.relpath(out, ROOT),
                                                  os.path.getsize(out) / 1e6, len(icons)))
    return out


def dur(sec):
    s = int(sec)
    return "%d:%02d" % (s // 60, s % 60)


HTML_TMPL = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>Q7 回放浏览器 · @@MID@@</title>
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
.left{min-width:0;min-height:0;display:flex;flex-direction:column;gap:6px}
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
         flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
#mapbox{flex:1 1 auto;min-height:200px;display:flex;align-items:center;justify-content:center}
#cv{border:1px solid var(--bd);border-radius:6px;background:#0d1117;display:block;width:512px;height:512px;
    cursor:grab;touch-action:none}
.mapbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:11.5px;color:var(--dim);margin-top:5px}
.mapbar input[type=range]{vertical-align:middle;accent-color:var(--acc)}
.btn{background:var(--pnl2);border:1px solid var(--bd);border-radius:14px;padding:5px 12px;cursor:pointer;font-size:12px;color:var(--fg)}
.btn:hover{border-color:#4d5866}
.btn.active{background:var(--acc);border-color:var(--acc);color:#fff}
.btn.big{font-size:14px;padding:7px 16px;border-radius:16px}
/* ---------- 头像（地图下方一行 10 个：天辉 5 ｜ 夜魇 5） ---------- */
#avatars{display:flex;flex-direction:column;gap:5px;margin-top:0;flex:0 0 auto}
.arow{display:flex;gap:5px;align-items:center;flex-wrap:wrap}
.arow .tl{width:30px;font-size:11px;text-align:right;flex:0 0 auto}
.arow .tblk{display:flex;gap:5px}
.arow .tdiv{width:1px;height:36px;background:#30363d;margin:0 3px;flex:0 0 auto}
.hero{position:relative;width:48px;height:48px;border-radius:8px;overflow:hidden;border:2px solid #333;
      cursor:pointer;background:#222;flex:0 0 auto}
.hero img{width:100%;height:100%;object-fit:cover;display:block}
.hero .nm{position:absolute;left:0;right:0;bottom:0;font-size:9px;text-align:center;background:#000a;color:#ddd;
          overflow:hidden;white-space:nowrap;text-overflow:ellipsis;padding:1px 2px}
.hero.sel{box-shadow:0 0 0 2px var(--gold);border-color:var(--gold)}
.hero.dead{filter:grayscale(1) brightness(.5)}
.hero.t2{border-color:var(--rad)}.hero.t3{border-color:var(--dire)}
.hero .hpbar{position:absolute;left:0;top:0;height:3px;background:#3fb950}
/* ---------- 时间轴 ---------- */
#timeline{margin-top:0}
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
/* ---------- 第二步：±10s 明细 + 技能 CD ---------- */
.tglrow{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0;font-size:12px}
.tglrow .toggle{padding:3px 8px;font-size:11px}
.dsum{background:#21262d;border:1px solid var(--bd);border-radius:6px;padding:6px 8px;font-size:12px;
      line-height:1.7;margin:4px 0}
.dsum b{color:#79c0ff}
.dwrap{max-height:34vh;overflow:auto;border:1px solid var(--bd);border-radius:6px}
#dtbl{margin:0;font-size:11.5px}
#dtbl th{background:#21262d;font-size:11px}
#dtbl td{padding:2px 5px;border-bottom:1px solid #1c2128}
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
.kv{font-size:12px;color:var(--dim);line-height:1.8}
.kv b{color:var(--fg)}
details{margin-top:8px;font-size:12px;color:var(--dim)}
details summary{cursor:pointer;color:#79c0ff}
details p{line-height:1.75;margin:6px 0}
code{background:#21262d;padding:1px 4px;border-radius:3px;font-size:11px}
</style></head><body>

<h1>Q7 · 全盘复现交互 UI（回放浏览器）— match <span id="mid">@@MID@@</span>
  <span class="sub" style="font-weight:400">联赛 @@LEAGUE@@ ｜ <b>@@RNAME@@</b> vs <b>@@DNAME@@</b> ｜ 结果 @@WIN@@ ｜ 游戏时长 @@DUR@@
  ｜ 显示时钟（0:00=号角）｜ 暂停 @@PAUSE@@s ｜ 数据源 <code>@@DB@@</code></span></h1>

<div id="top">
  <div class="stat clock"><div class="k">当前时刻</div><div class="v" id="vClock">0:00</div>
    <div class="s" id="vPhase">—</div></div>
  <div class="stat"><div class="k">经济差（净值 · m_iNetWorth）★主显</div><div class="v" id="vNw">—</div>
    <div class="s">天辉 − 夜魇 · 标准"经济差"口径（<b>owner 2026 定案主显此列</b>）</div></div>
  <div class="stat"><div class="k">经济差（combat-log 累加）</div><div class="v" id="vCg">—</div>
    <div class="s">= 累计获取金币差（含消耗品支出，非净值）</div></div>
  <div class="stat"><div class="k">经验差（combat-log 累加）</div><div class="v" id="vCx">—</div>
    <div class="s">天辉 − 夜魇 · 无独立源可校验</div></div>
  <div class="stat"><div class="k">胜率（状态胜率 · 派生模型）</div><div class="v" id="vWp">—</div>
    <div class="s" id="vWpNote">未拟合</div></div>
  <div id="sparkwrap">
    <canvas id="spark" width="1200" height="78"></canvas>
    <div class="sparkhint"><span>全场走势（点/拖此条可直接定位）</span>
      <span style="display:flex;gap:6px;align-items:center">
        <span>火花线口径</span>
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
      <div class="evlane" id="evDn"></div>
      <div class="evhint">▲ 上＝对<b class="dr">天辉</b>有利 ｜ ▼ 下＝对<b class="dd">夜魇</b>有利 ｜
        图标＝阵亡英雄头像 / 被毁的塔·兵营·基地·肉山 ｜ 描边色＝它属于哪一方（绿天辉·红夜魇·灰无主肉山）｜
        点图标跳到该时刻<span class="tmax">大时间轴：全场 0:00 → @@DUR@@</span></div>
    </div>
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
      <span id="mapInfo">滚轮缩放 · 拖拽平移 · 点英雄标记选中</span>
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
    <div class="kv" id="selinfo"><b>明细表</b>（默认：双方 10 英雄 KDA + 正反补）</div>
    <div style="overflow:visible">
    <table id="tbl"><thead><tr>
      <th>英雄</th><th>队</th><th>K</th><th>D</th><th>A</th><th>正补</th><th>反补</th>
      <th>净值@t</th><th>累计金币@t</th><th>经验@t</th><th>HP</th>
    </tr></thead><tbody></tbody></table>
    </div>
    <div class="tip" id="liteNote" style="display:none"><b>这是 lite（精简）版</b>：为压体积，
      逐秒数据抽稀到每 <b id="liteStep">3</b> 秒一格、<b>不含 ±10s combat log 明细与技能图标</b>；
      地图/表格/胜率/眼位/烟雾/技能 CD 三态都保留。完整版见同目录 <code>q7_replay_&lt;match&gt;.html</code>。</div>
    <div class="tip"><b>点任意英雄</b>（表格行 / 头像 / 地图上的标记）→ 本栏切到该英雄的
      <b>±10s combat log</b>（4 个 toggle）+ <b>技能 CD</b>。</div>
  </div>

  <div id="paneHero" style="display:none">
    <div class="kv" id="herotop">—</div>
    <div class="tglrow">
      <button class="btn" onclick="clearSel()">↩ 返回 10 英雄表</button>
      <span class="sep"></span>
      <label class="toggle"><input type="checkbox" id="tc0" checked onchange="renderDetail()">给出的 modifier</label>
      <label class="toggle"><input type="checkbox" id="tc1" checked onchange="renderDetail()">收到的 modifier</label>
      <label class="toggle"><input type="checkbox" id="tc2" checked onchange="renderDetail()">造成伤害</label>
      <label class="toggle"><input type="checkbox" id="tc3" checked onchange="renderDetail()">收到伤害</label>
      <label class="toggle"><input type="checkbox" id="tfold" checked onchange="renderDetail()">折叠连续同项</label>
      <label class="toggle"><input type="checkbox" id="tcdall" onchange="renderCD()">显示天赋/空槽</label>
    </div>
    <div class="dsum" id="dsum">—</div>
    <div class="dwrap"><table id="dtbl"><thead><tr>
      <th>时刻</th><th>类别</th><th>名称</th><th>对象</th><th>数值</th>
    </tr></thead><tbody></tbody></table></div>

    <div class="cdhead">技能冷却（三态：冷却中=灰+剩余秒 ｜ 未学未拥有=锁 ｜ 就绪）</div>
    <div id="cdboard"></div>
    <div class="tip" id="cdnote"></div>
  </div>

  <div class="todo" id="todobox">本版已实现：① ±10s combat log 四 toggle ② 技能 CD（三态 + BKB/刷新球/TP）
    ③ 状态胜率。<b>仍未做</b>：眼位/烟雾图层、多场切换、移动端 lite 版。</div>

  <details open><summary>口径与已知边界（坦诚说明 · 请务必先读）</summary>
    <p><b>① 时间口径</b>：0:00 = 号角（<code>combat_log gamestate value=5</code> 的 <code>t_cle</code>）；
      出门 = −1:30（实测初始金 600 事件恰在 −1:29.9）。所有展示时刻都是这条轴。
      <code>entity_snapshots</code> / <code>dems/db</code> 的 CD 事件是<b>回放钟</b>（<code>tick/30</code>）→
      经 <code>analysis/timebase.py</code> 的暂停感知折算（本场暂停 @@PAUSE@@s）。</p>
    <p><b>② ±10s 明细</b>全部来自 <code>combat_log</code>（4 类：<code>modifier</code> 按 attacker/target 分给出/收到、
      <code>damage</code> 同理）。窗口 = <b>当前播放时刻 ±10s</b>（owner 默认）。逐条内嵌、无采样丢失；
      "折叠连续同项"只是在显示层把同名称/同类别/同数值且时间相邻(≤1.2s)的条目并成一行（括号里是区间精确条数）。</p>
    <p><b>③ 技能 CD</b>来自 <code>dems/db/&lt;league&gt;/&lt;match&gt;.db</code> 的
      <code>ability_cd_start/end</code> + <code>ability_known/learn</code> + <code>item_cd_*</code> + <code>item_known</code>
      （combat_log 版库里没有这些：CD 是<b>实体派生</b>、不是 combat 条目）。
      <b>不需要任何冷却常量表</b> —— <code>properties.remaining</code> 就是实体 <code>m_fCooldown</code> 的
      <b>实际剩余冷却秒</b>（含等级/天赋/减CD），区间 = <code>[cd_start, cd_end]</code>（有 end 用真实 end）。
      <b>TP</b> 是充能制、库内无 CD/充能事件 → 只显示"使用时刻 + 次数"（如实标注，不硬造三态）。</p>
    <p><b>④ 胜率是派生模型，不是 combat log</b>：<code>P(天辉胜 | 净值差, 经验差, t)</code> 逻辑回归，
      在 <b id="wpN">—</b> 场上拟合，<b>按 match 划分训练/测试</b>防泄漏；特征只用 t 时刻可观测的量，
      <b>绝不使用最终结果</b>。标签 = 远古被摧毁（badguys_fort 死→天辉胜）。验证：<b id="wpAuc">—</b>。
      它是"历史上处于这种局面的队最终赢了多少"的统计映射，<b>不是这场比赛的预测</b>。
      模型形态 = <b>分时间桶的逻辑回归 + 桶间系数线性插值</b>（对 t 连续）；分桶 AUC 见上。
      ⚠️ 经济差与经验差<b>实测正相关 r=0.52</b> → <b>单个系数的符号不具解释意义</b>，
      模型只在两者联合的实测分布上有意义（页面喂的就是实测值）。</p>
    <p><b>⑤ 两个"经济差"是两套源，别混</b>：
      <span class="dr">净值差（m_iNetWorth）</span>= 标准经济差（现金+装备，非 combat log，项目内对账 OpenDota 0.000%）
      —— <b>已由 owner 定案为本页主显口径</b>；
      <span class="dr">combat-log 累加</span>= 累计<b>获取</b>金币差（已按 <code>gold_reason=1</code> 扣死亡损失；
      但买装备/消耗品不减 → 与净值差会随比赛拉大，实测本场末秒两者差 <b id="gapEnd">—</b>）。
      胜率模型用的是<b>净值差</b>口径。</p>
    <p><b>⑥ 经验差</b>只有 combat-log 一条源（库内无经验快照）→ 无法交叉校验，仅作参考。</p>
    <p><b>⑦ 正反补</b>= <code>death</code> 条目里 attacker 为英雄、target 为线上兵；敌方兵=正补、己方兵=反补。
      <code>assist_players</code> 的值是<b>头部玩家索引</b>且<b>含击杀者本人</b>（已剔除）。</p>
    <p><b>⑧ 位置</b>是解析层 1Hz 采样（偶有缺秒）→ 页面按"沿用上一秒"补齐（无位置则该英雄不画）。</p>
    <p><b>⑨ 眼位图层</b>：<b>完全复用 Q5B 的权威口径</b>（`analysis/q5_ward.py::parse_match` —— 同一份代码、
      同一套裁定）：判型=实体类名、放置=combat item use（±35s PVS 容差）、到期=<code>attacker==target</code>、
      销毁与眼"一一对应"、右删失=比赛结束时仍存活（标"截断"）。页面上只画<b>当前时刻存活</b>的眼；
      点/悬停眼标记可看 放置→销毁、存活秒数、到期/被反/截断。真眼真视半径 1050（未画圈）。</p>
    <p><b>⑩ 烟雾图层</b>：使用时刻来自 <code>combat_log</code> 的 <code>item_smoke_of_deceit</code>
      （item use），紫圈=开启后 20s 内；英雄紫环=<code>modifier_smoke_of_deceit</code> 的 Add→Remove 区间
      （未见 Remove 的按最长 45s 截断，如实标注）。</p>
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
const CATNAME = ["给出的 modifier", "收到的 modifier", "造成伤害", "收到伤害"];
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
    const R = (viewRect ? 15 : 11) * (selIdx === i ? 1.25 : 1);
    if (c[0] < -40 || c[0] > CSX + 40 || c[1] < -40 || c[1] > CSX + 40) return;
    ctx.save();
    ctx.beginPath(); ctx.arc(c[0], c[1], R, 0, Math.PI * 2); ctx.closePath();
    ctx.globalAlpha = dead ? 0.35 : 1;
    ctx.fillStyle = "#000"; ctx.fill();
    const im = imgs[p.short];
    if (im && im.complete && im.naturalWidth) {
      ctx.save(); ctx.clip(); ctx.drawImage(im, c[0] - R, c[1] - R, R * 2, R * 2); ctx.restore();
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
  /* owner：头像条放在地图下方；为了"四块同屏"，10 个头像排成一行（天辉 5 ｜ 夜魇 5，中间一条分隔线），
     比原来两行省 ~57px 高度，正好还给地图。窄屏会自动换行。 */
  const row = document.createElement("div"); row.className = "arow";
  const team = function (tv, label) {
    const lb = document.createElement("div"); lb.className = "tl " + (tv === 2 ? "dr" : "dd");
    lb.textContent = label; row.appendChild(lb);
    const wrap = document.createElement("div"); wrap.className = "tblk";
    PL.forEach(function (p, i) {
      if (p.team !== tv) return;
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
      wrap.appendChild(d);
    });
    row.appendChild(wrap);
  };
  team(2, "天辉");
  const dv = document.createElement("div"); dv.className = "tdiv"; row.appendChild(dv);
  team(3, "夜魇");
  box.appendChild(row);
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
  draw();
}

/* ═══════════════ 英雄 ±10s combat log 明细 ═══════════════ */
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
    + "</b>（" + (p.team === 2 ? "天辉" : "夜魇") + " · " + esc(p.name) + " · steam " + p.steam + "）"
    + ' ｜ KDA <b>' + k.k + "/" + k.d + "/" + k.a + "</b> ｜ 正/反补 <b>" + k.lh + "/" + k.dn + "</b>"
    + " ｜ 净值 <b>" + (valAt(NW, p.npc, tCur) === null ? "—" : Math.round(valAt(NW, p.npc, tCur)).toLocaleString("en-US")) + "</b>"
    + (q ? (" ｜ HP <b>" + (q.hp > 0 ? q.hp + " / " + (hpMaxAt(p.npc, tCur) || "?") : "阵亡") + "</b>") : " ｜ 未出场")
    + (pinfo ? '<br>关键道具：' + pinfo : "");
}

let lastDetail = null;   // 最近一次渲染的 ±10s 窗口（供回归测试/排查）
function foldKey(r) { return r[1] + "|" + r[2] + "|" + r[3]; }
function renderDetail() {
  if (selIdx < 0) return;
  const p = PL[selIdx];
  const abs = detAbs(selIdx), rows = DET[p.npc] || [];
  const lo = tCur - 10, hi = tCur + 10;
  const on = [0, 1, 2, 3].map(function (c) { return document.getElementById("tc" + c).checked; });
  const fold = document.getElementById("tfold").checked;
  // 二分找窗口起点
  let a = 0, b = abs.length;
  while (a < b) { const m = (a + b) >> 1; if (abs[m] < lo) a = m + 1; else b = m; }
  const picked = [];
  for (let k = a; k < abs.length && abs[k] <= hi; k++) {
    const r = rows[k];
    const cat = r[1] >> 2, kind = r[1] & 3;
    if (!on[cat]) continue;
    picked.push({ t: abs[k], cat: cat, kind: kind, nm: DNAMES[r[2]] || "?", oid: r[3],
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
      seq.push({ t: r.t, tEnd: r.t, n: 1, cat: r.cat, kind: r.kind, nm: r.nm, oid: r.oid, val: r.val });
    }
  }
  const cnt = [0, 0, 0, 0];
  picked.forEach(function (r) { cnt[r.cat]++; });
  lastDetail = { lo: lo, hi: hi, t: tCur, n: picked.length, shown: seq.length, cnt: cnt,
                 tMin: picked.length ? picked[0].t : null,
                 tMax: picked.length ? picked[picked.length - 1].t : null };
  document.getElementById("dsum").innerHTML =
    "<b>" + p.short.replace(/_/g, " ") + "</b>（" + (p.team === 2 ? "天辉" : "夜魇") + " · " + p.name + "）"
    + " ｜ 窗口 <b>" + fmt(tCur, true) + " ± 10s</b>（" + fmt(lo) + " → " + fmt(hi) + "）"
    + " ｜ 命中 <b>" + picked.length + "</b> 条"
    + (fold && seq.length !== picked.length ? "（折叠后 " + seq.length + " 行）" : "")
    + "<br>四类条数：" + CATNAME.map(function (c, i) { return c + " <b>" + cnt[i] + "</b>"; }).join(" ｜ ");
  const tb = document.querySelector("#dtbl tbody");
  const lim = 400;
  let html = "";
  for (let k = 0; k < seq.length && k < lim; k++) {
    const r = seq[k];
    const now = (r.t <= tCur + 1 && r.tEnd >= tCur - 1) ? "rownow" : (r.tEnd < tCur ? "rowpast" : "rowfut");
    const other = (r.oid >= 0 ? (DNAMES[r.oid] || "") : "").replace(/^npc_dota_hero_/, "").replace(/^npc_dota_/, "");
    const kk = r.cat < 2 ? (MINKIND[r.kind] || "") : (DMGKIND[r.kind] || "");
    const ttxt = (r.n > 1 && r.tEnd > r.t) ? (fmt(r.t) + "–" + fmt(r.tEnd)) : fmt(r.t);
    html += '<tr class="' + now + '"><td>' + ttxt
      + '</td><td><span class="chip c' + r.cat + '">' + CATNAME[r.cat].slice(0, 2) + "</span></td>"
      + '<td class="nm">' + esc(r.nm) + (kk ? ' <span class="dfold">[' + kk + ']</span>' : "")
      + (r.n > 1 ? ' <b class="dfold">×' + r.n + "</b>" : "") + "</td>"
      + "<td>" + esc(other) + "</td>"
      + "<td>" + (r.cat >= 2 ? r.val : "—") + "</td></tr>";
  }
  if (!seq.length) {
    const noDet = !rows.length;
    html = '<tr><td colspan="5">' + (noDet && DATA.lite
      ? '<b style="color:#d29922">本页是 lite 版，不含 ±10s combat log 明细</b>（lite 省掉了 2.3MB 逐条明细）。'
        + "看明细请构建完整版：<code>python analysis/q7_replay.py " + DATA.mid
        + "</code> → <code>python analysis/build_q7_html.py " + DATA.mid + "</code>"
      : (noDet ? "该英雄没有明细数据（数据缺失）"
               : "该窗口内没有命中（可能该英雄此时不在场/无事件，或 4 个 toggle 都被关掉了）")) + "</td></tr>";
  }
  if (seq.length > lim) html += '<tr><td colspan="5">… 仅列出前 ' + lim + " 行（共 " + seq.length + "）</td></tr>";
  tb.innerHTML = html;
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
function cdChip(key, name, icon, known, ivs, isItem, tracked) {
  const state = cdState(ivs, tCur);
  const locked = (known === null || known === undefined || tCur < known);
  const cls = locked ? "lock" : (state === null ? "ready" : "cool");
  let box;
  if (icon && ABICONS[icon]) box = '<img src="' + ABICONS[icon] + '" alt="">';
  else box = '<span class="fb">' + esc(name) + "</span>";
  let st;
  if (locked) st = "未" + (isItem ? "拥有" : "学");
  else if (state !== null) st = "冷却 " + state.toFixed(0) + "s";
  else st = "就绪";
  return '<div class="cd ' + cls + (tracked ? " track" : "") + '" title="' + esc(key) + " ｜ " + esc(name) + '">'
    + '<div class="box">' + box + (state !== null && !locked ? '<div class="rem">' + Math.ceil(state) + "</div>" : "")
    + '</div><div class="cap">' + esc(name.length > 14 ? name.slice(0, 13) + String.fromCharCode(8230) : name) + '</div><div class="st">' + st + "</div></div>";
}
function renderCD() {
  if (selIdx < 0) return;
  const p = PL[selIdx], npc = p.npc;
  const board = document.getElementById("cdboard"), note = document.getElementById("cdnote");
  if (!CD) {
    board.innerHTML = '<div class="tip">本场没有技能/道具 CD 数据（<code>dems/db/&lt;league&gt;/&lt;match&gt;.db</code> 缺失）'
      + " → <b>如实回退</b>，不硬造三态。</div>";
    note.textContent = "";
    return;
  }
  const abs = [];
  (CD.ab[npc] || []).forEach(function (e) { abs.push({ key: e[0], known: (e[2] !== null ? e[2] : e[1]), ivs: e[3] || [], item: false }); });
  (CD.it[npc] || []).forEach(function (e) { abs.push({ key: e[0], known: e[1], ivs: e[3] || [], item: true }); });
  // 关键道具（BKB/刷新球）即使未拥有也显示（locked）——owner 点名追踪
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
  // TP：充能制，库内无 CD/充能事件 → 只报使用记录（不硬造三态）
  const tp = TPUT[npc] || [];
  const lastTp = tp.length ? tp[tp.length - 1] : null;
  const stTp = lastTp === null ? "无使用记录" : ("最近 " + fmt(lastTp) + "（" + Math.round(tCur - lastTp) + "s 前）");
  html += '<div class="cd track" title="Town Portal Scroll（充能制；库内无 CD/充能事件）">'
    + '<div class="box"><span class="fb">TP</span></div><div class="cap">TP 卷轴</div>'
    + '<div class="st">' + tp.length + " 次</div></div>";
  board.innerHTML = html;
  note.innerHTML = "数据源 <code>" + esc(CD.src) + "</code>（<b>" + esc(CD.time_axis || "") + "</b>）："
    + "<code>ability_cd_start/end</code> + <code>ability_known/learn</code> + <code>item_cd_start/end</code> + <code>item_known</code>。"
    + "冷却区间 = <code>[cd_start, cd_end]</code>（有真实 end 用 end，否则 <code>start + properties.remaining</code>）——"
    + "<b>remaining 是实体 m_fCooldown 的实际剩余秒（含等级/天赋/减CD），所以本页不需要任何冷却常量表</b>。"
    + "金框 = owner 点名追踪的关键道具。<b>TP 是充能制</b>：库内没有 TP 的 CD/充能事件，"
    + "故只显示使用时刻与次数（当前英雄：" + stTp + "），<b>不伪造三态</b>。"
    + "未学/未拥有的技能与道具显示为锁定态（灰 + 🔒）。";
    + (hidden > 0 ? "已隐藏 <b>" + hidden + "</b> 项天赋/空槽占位（勾『显示天赋/空槽』可展开）。" : "")
    + " 名称形如 <code>Xxx_Yyy</code> 的是 <b>Devour 等吃来的中立生物技能</b>（前缀即来源单位），不是 bug。";
}
function clearSel() { if (selIdx >= 0) selectHero(selIdx); }
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
    document.getElementById("vWpNote").textContent = WP ? "该时刻无数据" : "模型未拟合（先跑 q7_winprob.py）";
  } else {
    wpEl.textContent = fmtPct(pw);
    wpEl.className = "v " + (pw > 0.52 ? "rad" : (pw < 0.48 ? "dir" : "zero"));
    document.getElementById("vWpNote").textContent = "天辉胜率（给定 t 时刻的经济/经验差）";
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
    if (!q) { hpEl.textContent = "未出场"; hpEl.style.color = "#8b949e"; }
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
      hb.title = p.short.replace(/_/g, " ") + "（" + p.name + "）"
        + (q ? (" ｜ HP " + (q.hp > 0 ? q.hp + " / " + (hpMaxAt(p.npc, t) || "?") : "阵亡")) : " ｜ 未出场");
    }
  }
  document.getElementById("biglabel").textContent = "大条（已提交）= " + fmt(tBig, true)
    + "（" + Math.round(tBig) + "s / " + T1 + "s）";
  document.getElementById("smalllabel").textContent = "小条偏移 = " + (sVal > 0 ? "+" : "") + sVal
    + "s → 实际时刻 " + fmt(tCur, true) + (Math.abs(sVal) < 0.01 ? "（已归零）" : "");
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
    (entDiff === "nw" ? "净值差" : (entDiff === "cg" ? "combat-log 累计金币差" : "combat-log 经验差"))
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
big.oninput = setBigFromSlider;
big.onchange = setBigFromSlider;

small.oninput = function () {          // 拖小条：实时联动（大条滑块跟着小幅移动 = tCur）
  smallDragging = true; smallDirty = true;
  sVal = parseFloat(small.value);
  tCur = Math.max(T0, Math.min(T1, tBig + sVal));
  big.value = Math.round(tCur);        // 实时反馈：大条同步"小幅"移动（±60s 在整场条上极小）
  refresh(tCur);
};
function smallCommit() {               // 松手提交：大条推进"滑过的量"，小条瞬时归零
  if (!smallDragging || !smallDirty) return;
  smallDragging = false; smallDirty = false;
  commit(tBig + parseFloat(small.value));
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

/* ═══════════════ 启动 ═══════════════ */
(function init() {
  document.getElementById("mid").textContent = DATA.mid;
  const g = document.getElementById("gapEnd");
  const a = DIFF.nw[D - 1], b = DIFF.cg[D - 1];
  if (g && a !== null && b !== null) g.textContent = Math.abs(a - b).toLocaleString("en-US") + "（净值 " + fmtNum(a) + " vs 累计 " + fmtNum(b) + "）";
  if (WP) {
    document.getElementById("wpN").textContent = WP.n_match + " 场 / " + WP.n_train + " 训练样本";
    document.getElementById("wpAuc").textContent =
      "测试集 AUC " + (WP.auc_test === null ? "—" : WP.auc_test.toFixed(3)) + "（训练 " +
      (WP.auc_train === null ? "—" : WP.auc_train.toFixed(3)) + "，Brier " + WP.brier_test.toFixed(3) + "）"
      + "；分桶 AUC " + (WP.buckets || []).map(function (b) {
        return (b.lo / 60) + "-" + (b.hi ? (b.hi / 60) : "+") + "分 " + (b.auc_test === null ? "—" : b.auc_test.toFixed(2));
      }).join(" / ");
  } else {
    document.getElementById("wpN").textContent = "未拟合";
    document.getElementById("wpAuc").textContent = "先跑 python analysis/q7_winprob.py";
  }
  if (DATA.lite) {
    document.getElementById("liteNote").style.display = "";
    document.getElementById("liteStep").textContent = String(STEP);
  }
  buildAvatars(); buildTable(); buildTimelineEvents(); fitCanvas();
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
    args = ap.parse_args()
    for mid in args.match:
        build(mid, args.out, lite=args.lite, step=args.step)


if __name__ == "__main__":
    main()
