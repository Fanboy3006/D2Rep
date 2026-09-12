#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_q7_html.py — Q7「全盘复现交互 UI（回放浏览器）」· 单文件 HTML 生成器。

输入：analysis/output_q7/q7_<match_id>.json（q7_replay.py 产物）
产出：analysis/output_review/q7_replay_<match_id>.html（自包含，双击即开）

页面骨架与交互复用 Q5B/Q6 范式（STRATEGY/INTERACTIVE_MAP_PATTERN.md）：
  · Canvas 底图 + 滚轮缩放 + 拖拽平移（复用 w2p / w2pView / calibFromPx / clampView）
  · 底图 = analysis/output_review/_q5_map_annot.png（沿用原底图 + 官方标定常量）
  · 已继承的前端坑（§9）：capValue 的 DOM 字符串陷阱、onmousemove 的 px 作用域、draw() 重入锁

第一步（MVP）范围：地图 + 缩放/平移 + 双方 10 英雄头像 + 双时间轴 + 顶部经济/经验差
                 + 地图上英雄逐秒位置 + 右侧 10 英雄 KDA/正反补表。
**未做（第二步）**：左侧点英雄后的 ±10s combat log 四 toggle、技能 CD（含 BKB/刷新球/TP）、胜率。
"""

import argparse
import base64
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q7DIR = os.path.join(ROOT, "analysis", "output_q7")
REVIEW = os.path.join(ROOT, "analysis", "output_review")
ICON_DIR = os.path.join(ROOT, "opendota_analysis", "assets", "hero_icons")
MAP_PNG = os.path.join(REVIEW, "_q5_map_annot.png")


def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def clean(s):
    if not s:
        return ""
    return "".join(ch if (ch.isprintable() and ch != "\ufffd") else "?" for ch in str(s))


def build(mid, outdir):
    src = os.path.join(Q7DIR, "q7_%s.json" % mid)
    if not os.path.exists(src):
        raise SystemExit("缺 %s（先跑 python analysis/q7_replay.py %s）" % (src, mid))
    with open(src, encoding="utf-8") as f:
        dat = json.load(f)

    # ---- 图标（短名 → data URI；缺失则该英雄退化为色块）----
    icons = {}
    for p in dat["players"]:
        p["name"] = clean(p["name"])
        fp = os.path.join(ICON_DIR, p["short"] + ".png")
        if os.path.exists(fp):
            icons[p["short"]] = "data:image/png;base64," + b64(fp)

    m = dat["meta"]
    payload = {
        "mid": dat["match_id"],
        "t0": dat["t0"],
        "t1": dat["t1"],
        "D": dat["D"],
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
        "meta": m,
        "icons": icons,
    }
    blob = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/")
    map_b64 = b64(MAP_PNG)

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
    out = os.path.join(outdir, "q7_replay_%s.html" % mid)
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
body{font-family:-apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:14px 18px 40px}
h1{font-size:17px;margin:0 0 4px}
.sub{color:var(--dim);font-size:12px;line-height:1.7;margin-bottom:10px}
.sub b{color:#79c0ff}
.panel{background:var(--pnl);border:1px solid var(--bd);border-radius:8px;padding:10px 12px}
/* ---------- 顶部数值条 ---------- */
#top{display:flex;flex-wrap:wrap;gap:10px;align-items:stretch;margin-bottom:10px}
.stat{background:var(--pnl);border:1px solid var(--bd);border-radius:8px;padding:8px 14px;min-width:150px}
.stat .k{color:var(--dim);font-size:11px;letter-spacing:.4px}
.stat .v{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.25}
.stat .s{color:var(--dim);font-size:11px}
.stat.clock .v{font-size:26px;color:#fff}
.rad{color:var(--rad)}.dir{color:var(--dire)}.zero{color:var(--dim)}
#sparkwrap{background:var(--pnl);border:1px solid var(--bd);border-radius:8px;padding:6px 8px 2px;flex:1;min-width:320px}
#spark{width:100%;height:78px;display:block;cursor:crosshair}
.sparkhint{color:var(--dim);font-size:11px;padding:0 2px 4px;display:flex;justify-content:space-between}
/* ---------- 主体 ---------- */
.wrap{display:flex;gap:14px;align-items:flex-start}
.left{flex:1;min-width:0}
.right{width:470px;position:sticky;top:10px}
@media(max-width:1180px){.wrap{flex-direction:column}.right{width:100%;position:static}}
#mapwrap{position:relative;background:var(--pnl);border:1px solid var(--bd);border-radius:8px;padding:8px}
#cv{border:1px solid var(--bd);border-radius:6px;background:#0d1117;display:block;width:100%;height:auto;cursor:grab;touch-action:none}
.mapbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--dim);margin-top:6px}
.mapbar input[type=range]{vertical-align:middle;accent-color:var(--acc)}
.btn{background:var(--pnl2);border:1px solid var(--bd);border-radius:14px;padding:5px 12px;cursor:pointer;font-size:12px;color:var(--fg)}
.btn:hover{border-color:#4d5866}
.btn.active{background:var(--acc);border-color:var(--acc);color:#fff}
.btn.big{font-size:14px;padding:7px 16px;border-radius:16px}
/* ---------- 头像 ---------- */
#avatars{display:flex;flex-direction:column;gap:6px;margin-top:8px}
.arow{display:flex;gap:6px;align-items:center}
.arow .tl{width:34px;font-size:11px;color:var(--dim);text-align:right}
.hero{position:relative;width:52px;height:52px;border-radius:8px;overflow:hidden;border:2px solid #333;
      cursor:pointer;background:#222;flex:0 0 auto}
.hero img{width:100%;height:100%;object-fit:cover;display:block}
.hero .nm{position:absolute;left:0;right:0;bottom:0;font-size:9px;text-align:center;background:#000a;color:#ddd;
          overflow:hidden;white-space:nowrap;text-overflow:ellipsis;padding:1px 2px}
.hero.sel{box-shadow:0 0 0 2px var(--gold);border-color:var(--gold)}
.hero.dead{filter:grayscale(1) brightness(.5)}
.hero.t2{border-color:var(--rad)}.hero.t3{border-color:var(--dire)}
.hero .hpbar{position:absolute;left:0;top:0;height:3px;background:#3fb950}
/* ---------- 时间轴 ---------- */
#timeline{margin-top:8px}
.tlrow{display:flex;gap:10px;align-items:center;margin:6px 0}
.tlrow .lb{width:96px;font-size:12px;color:var(--dim);flex:0 0 auto;text-align:right}
input[type=range]{width:100%;accent-color:var(--acc)}
#big{-webkit-appearance:none;appearance:none;height:16px;background:transparent}
#big::-webkit-slider-runnable-track{height:8px;background:#21262d;border:1px solid var(--bd);border-radius:5px}
#big::-webkit-slider-thumb{-webkit-appearance:none;width:12px;height:20px;margin-top:-7px;border-radius:3px;background:var(--acc);border:1px solid #fff3;cursor:pointer}
#small::-webkit-slider-thumb{cursor:pointer}
#bigmarks{position:relative;height:14px;margin:0 0 -10px 106px;pointer-events:none}
#bigmarks i{position:absolute;top:0;width:1px;height:9px;background:#f0883e;opacity:.85}
#bigmarks i.b{background:#8b949e;height:12px;width:2px}
#smallwrap{position:relative}
#scenter{position:absolute;left:50%;top:-2px;width:1px;height:22px;background:#8b949e;opacity:.6;pointer-events:none}
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
.kv{font-size:12px;color:var(--dim);line-height:1.8}
.kv b{color:var(--fg)}
details{margin-top:8px;font-size:12px;color:var(--dim)}
details summary{cursor:pointer;color:#79c0ff}
details p{line-height:1.75;margin:6px 0}
code{background:#21262d;padding:1px 4px;border-radius:3px;font-size:11px}
</style></head><body>

<h1>Q7 · 全盘复现交互 UI（回放浏览器）— match <span id="mid">@@MID@@</span>
  <span class="sub" style="font-weight:400">联赛 @@LEAGUE@@ ｜ <b>@@RNAME@@</b> vs <b>@@DNAME@@</b> ｜ 结果 @@WIN@@ ｜ 游戏时长 @@DUR@@</span></h1>
<div class="sub">数据源 <code>@@DB@@</code> → <code>@@SRCJSON@@</code>。时间轴一律为<b>显示时钟（0:00 = 号角）</b>；
  比赛结束 = 远古被摧毁（<code>@@ENDRULE@@</code>）。本场测得暂停 <b>@@PAUSE@@ s</b>（回放钟 4 段锚点 @@ANCHORS@@ 个，Δ_file=@@DELTA@@s）——
  位置/净值等"回放钟"数据已按"暂停时实体静止"的物理证据折回显示时钟。</div>

<div id="top">
  <div class="stat clock"><div class="k">当前时刻</div><div class="v" id="vClock">0:00</div>
    <div class="s" id="vPhase">—</div></div>
  <div class="stat"><div class="k">经济差（净值 · m_iNetWorth）★主显</div><div class="v" id="vNw">—</div>
    <div class="s">天辉 − 夜魇 · 标准"经济差"口径（<b>owner 2026 定案主显此列</b>）</div></div>
  <div class="stat"><div class="k">经济差（combat-log 累加）</div><div class="v" id="vCg">—</div>
    <div class="s">= 累计获取金币差（含消耗品支出，非净值）</div></div>
  <div class="stat"><div class="k">经验差（combat-log 累加）</div><div class="v" id="vCx">—</div>
    <div class="s">天辉 − 夜魇 · 无独立源可校验</div></div>
  <div class="stat"><div class="k">胜率</div><div class="v zero">未做</div>
    <div class="s">第二步：状态胜率模型（绝不偷看结果）</div></div>
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

<div class="wrap">
<div class="left">
  <div id="mapwrap">
    <canvas id="cv" width="1024" height="1024"></canvas>
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
    </div>
  </div>

  <div id="avatars"></div>

  <div class="panel" id="timeline">
    <div class="tlrow">
      <span class="lb">播放</span>
      <button class="btn big" id="play">▶ 播放</button>
      <button class="btn" onclick="step(-5)">« 5s</button>
      <button class="btn" onclick="step(5)">5s »</button>
      <span class="lbl" style="color:var(--dim);font-size:12px">速度</span>
      <button class="btn spd active" data-s="1" onclick="setSpeed(1)">1×</button>
      <button class="btn spd" data-s="2" onclick="setSpeed(2)">2×</button>
      <button class="btn spd" data-s="4" onclick="setSpeed(4)">4×</button>
      <span class="tip" style="margin-left:auto">空格=播放/暂停 ｜ ←→=±5s</span>
    </div>

    <div class="tlrow"><span class="lb">大时间轴<br><span style="font-size:10px">全场 0 → @@DUR@@</span></span>
      <div style="flex:1;min-width:0">
        <div id="bigmarks"></div>
        <input type="range" id="big" min="0" max="1" value="0" step="0.5">
        <div class="tip" id="biglabel">—</div>
      </div>
    </div>

    <div class="tlrow"><span class="lb">小时间轴<br><span style="font-size:10px">±60s</span></span>
      <div style="flex:1;min-width:0" id="smallwrap">
        <input type="range" id="small" min="-60" max="60" value="0" step="0.5">
        <div id="scenter"></div>
        <div class="tip" id="smalllabel">—</div>
      </div>
    </div>
    <div class="tip"><b>双条语义</b>：拖小条 → 实际时刻 = 大条 + 小条偏移（地图/表格实时跟随，大条滑块同步小幅移动）；
      <b>松手提交</b> → 大条推进"滑过的量"，小条<b>瞬时归零</b>。点火花线/拖大条 = 直接绝对定位（小条归零）。</div>
  </div>
</div>

<div class="right panel">
  <div class="kv" id="selinfo"><b>明细表</b>（默认：双方 10 英雄 KDA + 正反补）</div>
  <div style="max-height:62vh;overflow:auto">
  <table id="tbl"><thead><tr>
    <th>英雄</th><th>队</th><th>K</th><th>D</th><th>A</th><th>正补</th><th>反补</th>
    <th>净值@t</th><th>累计金币@t</th><th>经验@t</th><th>HP</th>
  </tr></thead><tbody></tbody></table>
  </div>
  <div class="todo">第二步（本页未做）：<b>点某英雄</b> → 右栏改为该英雄 <b>±10s 的 combat log</b>
    （4 个 toggle：给出的 modifier / 收到的 modifier / 造成伤害 / 收到伤害）+ 下方<b>技能 CD</b>
    （三态：冷却中灰+剩余秒 / 未学未拥有 / 就绪；重点追踪 <b>BKB / 刷新球 / TP</b>）。<br>
    本页点英雄 = 选中 + 地图聚焦，是第二步的入口。</div>

  <details open><summary>口径与已知边界（坦诚说明 · 请务必先读）</summary>
    <p><b>① 时间口径</b>：0:00 = 号角（<code>combat_log gamestate value=5</code> 的 <code>t_cle</code>）；
      出门 = −1:30（实测初始金 600 事件恰在 −0:90）。所有展示时刻都是这条轴。</p>
    <p><b>② combat_log 的 t_cle = 游戏钟</b>（暂停冻结）→ 事件类直接可用。
      <code>entity_snapshots</code> 的时间轴是<b>回放钟</b>（<code>tick/30</code>）→ 必须折算。
      本页新增了「暂停检测」：暂停时场上实体逐秒完全静止，据此把每对锚点间的真实游戏时间按活跃秒摊分，
      本场暂停 @@PAUSE@@s 被正确定位（否则整段暂停会被压到同一秒，出门期位置全错）。</p>
    <p><b>③ 两个"经济差"是两套源，别混</b>：
      <span class="dr">净值差（m_iNetWorth）</span>= 标准经济差（现金+装备，非 combat log，项目内对账 OpenDota 0.000%）
      —— <b>已由 owner 定案为本页主显口径</b>；
      <span class="dr">combat-log 累加</span>= 累计<b>获取</b>金币差（已按 <code>gold_reason=1</code> 扣死亡损失；
      但买装备/消耗品不减 → 与净值差会随比赛拉大，实测本场末秒两者差 <b id="gapEnd">—</b>）。
      页面同时给出两者，切换按钮只改"哪条驱动火花线"。</p>
    <p><b>④ 经验差</b>只有 combat-log 一条源（库内无经验快照）→ 无法交叉校验，仅作参考。</p>
    <p><b>⑤ 正反补</b>= <code>death</code> 条目里 attacker 为英雄、target 为线上兵；敌方兵=正补、己方兵=反补。
      助手同理杀者：<code>assist_players</code> 里的值是<b>头部玩家索引</b>且<b>含击杀者本人</b>（已剔除）。</p>
    <p><b>⑥ 位置</b>是解析层 1Hz 采样（偶有缺秒）→ 页面按"沿用上一秒"补齐（无位置则该英雄不画）。</p>
    <p><b>⑦ 未做</b>：±10s combat log 明细、技能 CD、胜率、眼位/烟雾图层。</p>
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
const MAP_HALF = 8600;

/* ═══════════════ 地图坐标（沿用 Q5B 官方标定） ═══════════════ */
const CSX = 1024;
const CALIB_K = 0.049038, CALIB_OFFX = 508.3019, CALIB_REF_Y = 504.5433;
const FULL = [-8600, 8600, -8600, 8600];
let viewRect = null;
function w2p(x, y) { return [CALIB_OFFX + CALIB_K * x, CALIB_REF_Y - CALIB_K * y]; }
function w2pView(x, y) {
  if (!viewRect) return w2p(x, y);
  const v = viewRect;
  const fx = (x - v[0]) / (v[1] - v[0]), fy = (y - v[2]) / (v[3] - v[2]);
  return [fx * CSX, (1 - fy) * CSX];
}
function calibFromPx(px, py) {
  if (!viewRect) return [(px - CALIB_OFFX) / CALIB_K, (CALIB_REF_Y - py) / CALIB_K];
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
function kOf(t) { const k = Math.round(t) - T0; return (k >= 0 && k < D) ? k : null; }
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
  if (P.x[k] !== null) return { x: P.x[k], y: P.y[k], hp: P.hp[k], t: Math.round(t) };
  for (let i = k - 1; i >= 0 && i > k - 12; i--) {
    if (P.x[i] !== null) return { x: P.x[i], y: P.y[i], hp: P.hp[i], t: T0 + i, stale: true };
  }
  return null;
}
/* 血量上限（随等级变）——同样沿用上一秒 */
function hpMaxAt(npc, t) {
  const k = kOf(t), a = HPM[npc];
  if (k === null || !a) return null;
  if (a[k] !== null && a[k] !== undefined) return a[k];
  for (let i = k - 1; i >= 0 && i > k - 30; i--) if (a[i] !== null && a[i] !== undefined) return a[i];
  return null;
}
function valAt(series, npc, t) {
  const k = kOf(t); if (k === null) return null;
  const a = series[npc]; if (!a) return null;
  if (a[k] !== null && a[k] !== undefined) return a[k];
  for (let i = k - 1; i >= 0 && i > k - 15; i--) if (a[i] !== null && a[i] !== undefined) return a[i];
  return null;
}
function diffAt(key, t) {
  const k = kOf(t); if (k === null) return null;
  const a = DIFF[key]; if (!a) return null;
  return (a[k] === null || a[k] === undefined) ? null : a[k];
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
      for (let k = Math.max(0, kEnd - 40); k <= kEnd; k++) {
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
    annMarks.push({ x: c[0], y: c[1], r: R + 3, i: i, wx: q.x, wy: q.y });
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
  if (hit) { selectHero(hit.i); return; }
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
  let txt = "滚轮缩放 · 拖拽平移 · 点英雄标记选中";
  if (hit) { const p = PL[hit.i], q = posAt(p.npc, tCur);
    txt = p.short.replace(/_/g, " ") + " ｜ " + (p.team === 2 ? "天辉" : "夜魇") + " ｜ 坐标(" + Math.round(hit.wx) + "," + Math.round(hit.wy) + ")"
      + (q && q.stale ? " ｜ ⚠位置沿用 " + fmt(q.t) : ""); }
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
  [[2, "天辉"], [3, "夜魇"]].forEach(function (tv) {
    const row = document.createElement("div"); row.className = "arow";
    const lb = document.createElement("div"); lb.className = "tl"; lb.textContent = tv[1]; row.appendChild(lb);
    const box2 = document.createElement("div"); box2.style.display = "flex"; box2.style.gap = "6px";
    PL.forEach(function (p, i) {
      if (p.team !== tv[0]) return;
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
      box2.appendChild(d);
    });
    row.appendChild(box2); box.appendChild(row);
  });
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
  const si = document.getElementById("selinfo");
  if (selIdx < 0) { si.innerHTML = "<b>明细表</b>（默认：双方 10 英雄 KDA + 正反补）"; }
  else {
    const p = PL[selIdx], k = KDA[p.npc];
    si.innerHTML = '已选中 <b class="' + (p.team === 2 ? "dr" : "dd") + '">' + p.short.replace(/_/g, " ") + "</b>"
      + "（" + (p.team === 2 ? "天辉" : "夜魇") + " · " + p.name + " · steam " + p.steam + "）"
      + " ｜ KDA <b>" + k.k + "/" + k.d + "/" + k.a + "</b> ｜ 正/反补 <b>" + k.lh + "/" + k.dn + "</b>"
      + '<br><span style="color:#d29922">第二步入口：此处将改为该英雄 ±10s combat log（4 toggle）+ 技能 CD。</span>';
  }
  draw();
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
  for (let k = 0; k < D; k++) { const v = Math.abs(a[k] || 0); if (v > mx) mx = v; }
  sparkMax = mx;
  return { a: a, mx: mx, X: function (k) { return k / (D - 1) * W; }, Y: function (v) { return H / 2 - (v / mx) * (H / 2 - 8); } };
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
  for (let k = 0; k < D; k++) g.lineTo(t.X(k), t.Y(a[k] || 0));
  g.lineTo(W, H / 2); g.closePath();
  g.fillStyle = "rgba(227,179,65,.18)"; g.fill();
  g.beginPath();
  for (let k = 0; k < D; k++) { const x = t.X(k), y = t.Y(a[k] || 0); if (k === 0) g.moveTo(x, y); else g.lineTo(x, y); }
  g.strokeStyle = "#e3b341"; g.lineWidth = 1.4; g.stroke();
  g.strokeStyle = "rgba(248,81,73,.35)";
  KILLSX.forEach(function (kk) { const k = kOf(kk[0]); if (k === null) return;
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
  const kc = kOf(tCur);
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

/* 大时间轴上的击杀/建筑刻度 */
function buildMarks() {
  const box = document.getElementById("bigmarks");
  box.innerHTML = "";
  const span = T1 - T0;
  KILLSX.forEach(function (k) {
    const i = document.createElement("i");
    i.style.left = ((k[0] - T0) / span * 100) + "%";
    i.title = "击杀 " + fmt(k[0]);
    box.appendChild(i);
  });
  (DATA.events || []).forEach(function (ev) {
    const i = document.createElement("i"); i.className = "b";
    i.style.left = ((ev[0] - T0) / span * 100) + "%";
    i.title = ev[1] + " @ " + fmt(ev[0]);
    box.appendChild(i);
  });
}

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
document.getElementById("showRoute").onchange = draw;
document.getElementById("showName").onchange = draw;

/* ═══════════════ 启动 ═══════════════ */
(function init() {
  document.getElementById("mid").textContent = DATA.mid;
  const g = document.getElementById("gapEnd");
  const a = DIFF.nw[D - 1], b = DIFF.cg[D - 1];
  if (g && a !== null && b !== null) g.textContent = Math.abs(a - b).toLocaleString("en-US") + "（净值 " + fmtNum(a) + " vs 累计 " + fmtNum(b) + "）";
  buildAvatars(); buildTable(); buildMarks();
  commit(0);          // 默认停在 0:00（号角）；往前拖 = 出门期（-1:30 起）
})();
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Q7 单文件 viewer 生成器")
    ap.add_argument("match", nargs="+", help="match_id（可多个）")
    ap.add_argument("--out", default=REVIEW)
    args = ap.parse_args()
    for mid in args.match:
        build(mid, args.out)


if __name__ == "__main__":
    main()
