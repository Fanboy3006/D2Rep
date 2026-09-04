#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_match_viewer.py - Module B: single self-contained HTML replay viewer
(cloud-viewer friendly: double-click to open, no server / no Node / no Python).

Embeds: the shared map background PNG (base64) + this match's 10-hero time
series (x, y, hp, hp_max, mana, mana_max per second) as JSON + official hero
portrait cards (base64, from the Dota 2 CDN, cached under assets/hero_icons/).
The page draws the map on a zoom/pan canvas, moves the hero icons on a timeline
slider with linear interpolation, and shows HP/mana bars beside each icon and
in a side list.

Usage:
    python opendota_analysis/export_match_viewer.py <match_id | db path>
        [--out dist/viewer_<match_id>.html]
        [--map opendota_analysis/assets/dota_map_1024.png]
        [--step 1]              # export every Nth second (size control)
        [--no-icons]            # colored dots instead of hero portraits
"""
import argparse
import base64
import io
import json
import os
import sqlite3
import sys
import urllib.request
import ssl

VER = "3.4 · 2026-09-04"  # bump on regeneration so stale phone copies are detectable

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

ICON_CACHE = os.path.join(HERE, "assets", "hero_icons")
# Official Valve/Dota2 CDN portraits. The classic folder (images/heroes/
# <name>_full.png) 404s for a few heroes (e.g. dawnbreaker), so fall back to
# the current dota2.com asset path (images/dota_react/heroes/<name>.png).
ICON_URLS = [
    "https://cdn.dota2.com/apps/dota2/images/heroes/%s_full.png",
    "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/%s.png",
]
# official portraits are 256x144 cards; keep 128x72 in cache/HTML
ICON_W, ICON_H = 128, 72
_SSL = ssl._create_unverified_context()  # this machine's network restricts TLS
import threading
_ICON_LOCK = threading.Lock()  # serialize cache download/writes (parallel batch)


def find_db(match_id):
    import glob
    p = os.path.join(HERE, "..", "dems", "db", "*", "%s.db" % match_id)
    hits = glob.glob(p)
    return hits[0] if hits else None


def hero_icon_b64(npc):
    """Return base64 of the cached official portrait for npc_dota_hero_x, or
    None. Downloads once from the Dota 2 CDN into assets/hero_icons and
    downscales to 128x72 PNG. Cache write is atomic + lock-protected so
    parallel batch exports never double-fetch or write partial files."""
    stem = npc[len("npc_dota_hero_"):] if npc.startswith("npc_dota_hero_") else npc
    cached = os.path.join(ICON_CACHE, stem + ".png")
    if os.path.exists(cached):
        with open(cached, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    with _ICON_LOCK:
        if os.path.exists(cached):  # another worker fetched it meanwhile
            with open(cached, "rb") as f:
                return base64.b64encode(f.read()).decode("ascii")
        url = None
        for tpl in ICON_URLS:
            u = tpl % stem
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
                    raw = r.read()
                url = u
                break
            except Exception as e:
                print("  [icon] %s unavailable (%s)" % (u.rsplit("/", 1)[-1], e), file=sys.stderr)
        if url is None:
            return None
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(raw))
            if (im.width, im.height) != (ICON_W, ICON_H):
                im = im.resize((ICON_W, ICON_H), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
            data = buf.getvalue()
        except Exception as e:
            print("  [icon] resize failed for %s: %s" % (stem, e), file=sys.stderr)
            return None
        os.makedirs(ICON_CACHE, exist_ok=True)
        tmp = cached + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, cached)
    return base64.b64encode(data).decode("ascii")


def export(target, out=None, map_path=None, step=1, no_icons=False, quiet=False):
    """Render one match viewer. `target` = match_id or db path. Returns a dict
    with match_id / out / size_mb / heroes / samples / t0 / icons; raises on
    missing db."""
    if map_path is None:
        map_path = os.path.join(HERE, "assets", "dota_map_1024.png")
    step = max(1, step)

    if target.endswith(".db") and os.path.exists(target):
        db = target
        match_id = os.path.basename(db)[:-3]
    else:
        db = find_db(target)
        if not db:
            raise FileNotFoundError("db not found for match %s under dems/db" % target)
        match_id = target
    if out is None:
        out = os.path.join(HERE, "..", "dist", "viewer_%s.html" % match_id)

    con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(db).replace("\\", "/"), uri=True)
    players = []
    for slot, steam, name, hero, team in con.execute(
            "SELECT player_slot, steam_id, player_name, hero_name, team_id "
            "FROM player_identity ORDER BY player_slot"):
        players.append({"slot": slot, "name": name or "", "hero": hero or "",
                        "team": team, "steam": steam})
    hero_npcs = {p["hero"] for p in players}

    # series per hero npc name (only the 10 real player heroes)
    series = {}
    rows = con.execute(
        """SELECT entity_id, game_time_sec, x, y, hp, extra, team
           FROM entity_snapshots WHERE entity_type='hero'
           AND json_extract(extra,'$.player_slot') IS NOT NULL
           ORDER BY entity_id, game_time_sec""")
    for entity_id, t, x, y, hp, extra, team in rows:
        if entity_id not in hero_npcs:
            continue
        if step > 1 and t % step != 0:
            continue
        e = json.loads(extra)
        arr = series.setdefault(entity_id, [])
        arr.append([t, int(round(x)), int(round(y)), hp if hp is not None else 0,
                    e.get("hp_max") or 0, int(round(e.get("mana") or 0)),
                    int(round(e.get("mana_max") or 0))])
    max_event_sec = con.execute(
        "SELECT MAX(game_time_sec) FROM game_events").fetchone()[0]

    # building events -> tower survival list for the map layer
    towers = {}
    for etype, sec, tid, x, y, props in con.execute(
            """SELECT event_type, game_time_sec, target_id, x, y, properties
               FROM game_events
               WHERE event_type IN ('building_spawn','building_destroyed')
               AND json_extract(properties,'$.kind')='tower'"""):
        p = json.loads(props)
        team = p.get("team")
        if team not in (2, 3) or x is None:
            continue
        if etype == "building_spawn":
            towers.setdefault(tid, {"id": tid, "x": int(round(x)),
                                    "y": int(round(y)), "team": team, "d": None})
        else:
            t = towers.get(tid)
            if t is not None:
                t["d"] = int(sec)
    con.close()
    towers = [t for t in towers.values()]

    # ---- fold the "dead tail": after the last real action the replay often
    # keeps ticking with every hero standing still at full HP (game over /
    # pause). Keep only up to the last meaningful second so the timeline does
    # not end with a long frozen stretch; the JS shows a note when folded.
    raw_end = max((arr[-1][0] for arr in series.values() if arr), default=0)
    move_end = 0
    for arr in series.values():
        for i in range(1, len(arr)):
            dt = arr[i][0] - arr[i - 1][0]
            if dt <= 0:
                continue
            dx = arr[i][1] - arr[i - 1][1]
            dy = arr[i][2] - arr[i - 1][2]
            if (dx * dx + dy * dy) ** 0.5 / dt >= 100:  # > ~1/3 walk speed
                move_end = arr[i][0]
    active_end = raw_end
    last_action = max(move_end, int(max_event_sec) if max_event_sec else 0)
    # only fold when a long, genuinely dead stretch follows the last action
    if raw_end - last_action > 120:
        active_end = min(raw_end, last_action + 90)
    if active_end < raw_end:
        for eid in list(series.keys()):
            series[eid] = [r for r in series[eid] if r[0] <= active_end]
        series = {eid: a for eid, a in series.items() if a}

    # official hero portraits (base64) unless disabled
    icons = {}
    if not no_icons:
        for p in players:
            if p["hero"] and p["hero"] in series:
                b = hero_icon_b64(p["hero"])
                if b:
                    icons[p["hero"]] = b

    import map_annotations as mann
    camps = [[c[0], c[1], c[2]] for c in mann.NEUTRAL_CAMPS]
    payload = {"match_id": int(match_id), "players": players, "series": series,
               "meta": {"raw_end": raw_end, "active_end": active_end},
               "towers": towers, "camps": camps, "roshan": list(mann.ROSHAN)}
    data_json = json.dumps(payload, separators=(",", ":"))

    with open(map_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    # lightweight 512px map copy for narrow screens: some Android browsers
    # fail to decode/allocate the ~2MB base64 map; small screens don't need it.
    try:
        from PIL import Image
        import io
        im = Image.open(map_path).convert("RGB")
        im = im.resize((512, 512), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        b64_lite = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        b64_lite = b64
    icons_json = json.dumps(icons, separators=(",", ":"))

    html = (TEMPLATE.replace("__DATA_JSON__", data_json)
            .replace("__ICONS_JSON__", icons_json)
            .replace("__MAP_B64__", b64)
            .replace("__MAP_B64_LITE__", b64_lite)
            .replace("__VER__", VER)
            .replace("__match__", match_id))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    size_mb = os.path.getsize(out) / 1e6
    n = sum(len(v) for v in series.values())
    t0 = min((v[0][0] for v in series.values() if v), default=0)
    if not quiet:
        print("wrote %s (%.2f MB, %d heroes, %d samples, data from %ds%s)"
              % (out, size_mb, len(series), n, t0,
                 "" if icons else ", no icons"))
    return {"match_id": match_id, "out": out, "size_mb": size_mb,
            "heroes": len(series), "samples": n, "t0": t0, "icons": len(icons)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="match_id (searched under dems/db) or a db path")
    ap.add_argument("--out", default=None)
    ap.add_argument("--map", default=os.path.join(HERE, "assets", "dota_map_1024.png"))
    ap.add_argument("--step", type=int, default=1, help="keep every Nth second (default 1)")
    ap.add_argument("--no-icons", action="store_true", help="use plain team dots")
    args = ap.parse_args()
    export(args.target, out=args.out, map_path=args.map,
           step=args.step, no_icons=args.no_icons)
    sys.exit(0)


TEMPLATE = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Dota2 Replay Viewer v__VER__ · __match__</title>
<style>
 :root{--sideW:300px;--topH:44px;--ctrlH:48px}
 body{margin:0;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
      background:#101216;color:#dfe3ea;height:100vh;height:100dvh;overflow:hidden;
      overscroll-behavior:none}
 #top{position:fixed;top:0;left:0;right:0;z-index:30;display:flex;align-items:center;gap:10px;
      padding:8px 12px;background:#171a21;border-bottom:1px solid #262b36}
 #top h1{font-size:15px;margin:0;font-weight:600;white-space:nowrap;overflow:hidden;
         text-overflow:ellipsis}
 #note{font-size:12px;color:#e6b450;background:#2a2414;border:1px solid #5a4a1a;
       padding:3px 9px;border-radius:10px;white-space:nowrap;max-width:56vw;overflow:hidden;
       text-overflow:ellipsis;flex:none}
 #note.ok{color:#7fb97f;background:#142a14;border-color:#1f5a1f}
 #main{position:fixed;top:var(--topH);bottom:var(--ctrlH);left:0;right:0;overflow:hidden}
 #mapwrap{position:absolute;top:0;left:0;right:var(--sideW);bottom:0;overflow:hidden;
          background:#0a0c10;touch-action:none}
 #errbox{display:none;position:fixed;left:50%;transform:translateX(-50%);top:6px;z-index:99;
         background:#5a1f1f;color:#ffd7d7;font-size:12px;padding:5px 12px;border-radius:8px;
         max-width:92vw;border:1px solid #a33}
 #dbg{display:none;position:fixed;right:8px;bottom:8px;z-index:98;font-size:10px;color:#b8c4d8;
      background:#000c;padding:4px 8px;border-radius:6px;white-space:pre}
 canvas{display:block}
 #hint{position:absolute;bottom:10px;left:12px;font-size:11px;color:#7a8194;
       background:#000a;padding:3px 8px;border-radius:8px;pointer-events:none;max-width:70%}
 #zoombtns{position:absolute;top:10px;right:10px;display:flex;flex-direction:column;gap:6px}
 #zoombtns button{width:34px;height:34px;border-radius:8px;border:1px solid #2c3240;
   background:#1b1f28;color:#dfe3ea;font-size:17px;cursor:pointer;line-height:1}
 #zoombtns button:hover{background:#262c38}
 #leg{position:absolute;top:10px;left:10px;font-size:11px;background:#0009;padding:5px 9px;
      border-radius:8px;line-height:1.7;pointer-events:none;user-select:none}
 #leg .tgl{display:flex;gap:10px;margin-top:2px}
 #leg label{display:inline-flex;align-items:center;gap:3px;cursor:pointer}
 #leg input{accent-color:#4da3ff;margin:0;pointer-events:auto}
 #side{position:fixed;top:var(--topH);bottom:var(--ctrlH);right:0;width:var(--sideW);
       display:flex;flex-direction:column;background:#14161c;border-left:1px solid #262b36;
       overflow:hidden}
 #side h3{margin:0;padding:9px 12px;font-size:13px;border-bottom:1px solid #22262f;flex:none}
 #herolist{flex:1;overflow:auto;padding:4px 0;-webkit-overflow-scrolling:touch}
 .hrow{display:flex;align-items:center;gap:8px;padding:5px 10px;cursor:pointer;font-size:13px}
 .hrow:hover{background:#1d212b}
 .hrow.sel{background:#22304a}
 .hrow .nm{width:84px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .hrow .bars{flex:1}
 .hrow .pv{min-width:70px;text-align:right;font-variant-numeric:tabular-nums;font-size:11px;color:#9aa2b2}
 .hbar,.mbar{height:6px;border-radius:3px;background:#262a36;margin:2px 0;overflow:hidden}
 .hbar i{display:block;height:100%;background:linear-gradient(90deg,#27ae60,#5fe08a)}
 .mbar i{display:block;height:100%;background:linear-gradient(90deg,#2980b9,#5bc0ff)}
 .icowrap{width:26px;height:26px;border-radius:50%;flex:none;display:flex;align-items:center;
          justify-content:center;background:#0b0d12;overflow:hidden}
 .icowrap img{width:24px;height:13.5px}
 #controls{position:fixed;left:0;right:0;bottom:0;z-index:25;display:flex;align-items:center;
           gap:10px;padding:6px 12px;padding-bottom:calc(6px + env(safe-area-inset-bottom,0px));
           background:#171a21;border-top:1px solid #262b36}
 #time{font-variant-numeric:tabular-nums;min-width:96px;font-size:13px;text-align:center}
 input[type=range]{flex:1;accent-color:#4da3ff;min-width:0}
 #play{width:36px;height:30px;border-radius:6px;border:1px solid #2c3240;background:#1b1f28;
       color:#dfe3ea;font-size:14px;cursor:pointer}
 #play:hover{background:#262c38}
 @media (max-width: 860px) {
   :root{--sideW:0px}
   #mapwrap{right:0}
   #side{left:0;right:0;top:auto;bottom:var(--ctrlH);width:auto;height:132px;
         border-left:none;border-top:1px solid #262b36}
   #side h3{display:none}
   #herolist{display:flex;flex-direction:column;flex-wrap:wrap;overflow:auto}
   .hrow{flex:0 0 auto;width:264px}
   #top{padding:6px 10px;gap:8px}
   #note{max-width:44vw}
   #hint{display:none}
 }
</style>
</head>
<body>
<div id="top">
  <h1>Dota2 复盘 · 比赛 __match__
    <span style="font-weight:400;color:#8fa0b8;font-size:11px;margin-left:6px">v__VER__</span></h1>
  <span id="note"></span>
</div>
<div id="main">
  <div id="mapwrap">
    <canvas id="cv"></canvas>
    <div id="leg">
      <div><span style="color:#46d160">●</span> 天辉 &nbsp;<span style="color:#ff5f57">●</span> 夜魇</div>
      <div class="tgl">
        <label><input type="checkbox" id="tgCam" checked> ▲野点 ◆远古</label>
        <label><input type="checkbox" id="tgTw" checked> ◉防御塔</label>
      </div>
    </div>
    <div id="hint">滚轮缩放 · 拖拽平移 · 点右侧英雄聚焦 · 空格 播放/暂停</div>
    <div id="zoombtns">
      <button id="zin" title="放大">+</button>
      <button id="zout" title="缩小">−</button>
      <button id="zfit" title="复位">⌂</button>
    </div>
  </div>
  <div id="side">
    <h3>英雄状态（点击聚焦到地图）</h3>
    <div id="herolist"></div>
  </div>
</div>
<div id="controls">
  <button id="play">▶</button>
  <span id="time">0:00</span>
  <input type="range" id="slider" min="0" max="0" step="1" value="0">
</div>
<div id="errbox"></div>
<div id="dbg"></div>
<script>
"use strict";
const DATA = __DATA_JSON__;
const ICONS = __ICONS_JSON__;
// full-res map for wide screens; 512px copy for narrow screens (some Android
// browsers fail on the ~2MB base64 map / huge backing stores)
const MAPB64 = "data:image/png;base64,__MAP_B64__";
const MAPB64LITE = "data:image/png;base64,__MAP_B64_LITE__";
const useLite = (document.documentElement.clientWidth || 0) < 720;
const WORLD = 19134;                       // world units across map (map_background.py)
const TEAMC = {2:'#46d160', 3:'#ff5f57'};

function showErr(m) {
  const b = document.getElementById('errbox');
  b.textContent = m;
  b.style.display = 'block';
}
window.addEventListener('error', e => showErr('脚本错误: ' + (e.message || e.type || '?')));

// ---- heroes: only the real player heroes, with series ----
const heroes = [];
for (const p of DATA.players) {
  const arr = DATA.series[p.hero];
  if (!arr || !arr.length) continue;
  heroes.push({npc: p.hero, name: p.name || p.hero, team: p.team, arr: arr});
}
heroes.sort((a, b) => (a.team || 0) - (b.team || 0));

// ---- data coverage window ----
let tMin = Infinity, tMax = 0;
for (const h of heroes) {
  tMin = Math.min(tMin, h.arr[0][0]);
  tMax = Math.max(tMax, h.arr[h.arr.length - 1][0]);
}
if (!isFinite(tMin)) { tMin = 0; tMax = 1; }
function fmt(sec) {
  const s = Math.max(0, Math.round(sec));
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}
const noteEl = document.getElementById('note');
const META = DATA.meta || {};
const rawEnd = META.raw_end || tMax;
const activeEnd = META.active_end || tMax;
let msg = '';
if (rawEnd - activeEnd > 60) {
  msg = '对局活动止于 ' + fmt(activeEnd - tMin) + '，末尾 ' + fmt(rawEnd - activeEnd) +
        ' 为无走位静止段（对局结束/暂停），已自动折叠';
} else if (tMin > 5) {
  msg = '位置数据自原始时间轴 ' + fmt(tMin) + ' 起（延迟/续录录像），时钟按数据起点归零';
} else {
  msg = '完整轨迹 ' + heroes.length + ' 名英雄 · 拖动滑块回放';
  noteEl.classList.add('ok');
}
noteEl.textContent = msg;
noteEl.title = msg;

// ---- canvas + view ----
const cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
const mapImg = new Image();
mapImg.src = useLite ? MAPB64LITE : MAPB64;
mapImg.onerror = () => showErr('地图图像解码失败（image decode error）——请把下方版本号和本行文字发给我');
let view = {half: 200, ox: 0, oy: 0, fit: 1};
let mapReady = false;   // set once the map image finished decoding OK
function measure() {
  const rs = document.documentElement.style;
  rs.setProperty('--topH', document.getElementById('top').offsetHeight + 'px');
  rs.setProperty('--ctrlH', document.getElementById('controls').offsetHeight + 'px');
  layout();
  draw();
}
function layout() {
  const box = cv.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  cv.style.width = box.width + 'px';
  cv.style.height = box.height + 'px';
  cv.width = Math.max(2, Math.round(box.width * dpr));
  cv.height = Math.max(2, Math.round(box.height * dpr));
  if (view.fit) {
    view.half = (Math.min(box.width, box.height) - 20) / 2;
    view.ox = box.width / 2;
    view.oy = box.height / 2;
    view.fit = 0;
  }
}
// world -> screen (map square of width 2*half centred at ox,oy)
function w2s(wx, wy) {
  const m = (2 * view.half) / 1024;                 // screen px per 1024-map-px
  const px = (wx + WORLD / 2) / WORLD * 1024;       // 0..1024 map px
  const py = (WORLD / 2 - wy) / WORLD * 1024;
  return [view.ox + (px - 512) * m, view.oy + (py - 512) * m];
}

// ---- per-hero icon images ----
const imgs = {};
let iconsReady = false;
function loadIcons(cb) {
  if (iconsReady) { cb(); return; }
  let left = 0;
  for (const h of heroes) if (ICONS[h.npc]) left++;
  if (!left) { iconsReady = true; cb(); return; }
  for (const h of heroes) {
    if (!ICONS[h.npc]) continue;
    (function (npc) {
      const im = new Image();
      im.onload = im.onerror = () => {
        if (--left === 0) { iconsReady = true; cb(); }
      };
      im.src = 'data:image/png;base64,' + ICONS[npc];
      imgs[npc] = im;
    })(h.npc);
  }
}

// ---- linear interpolation between samples ----
function stateAt(arr, t) {
  let lo = 0, hi = arr.length - 1;
  if (t <= arr[0][0]) return arr[0];
  if (t >= arr[hi][0]) return arr[hi];
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (arr[m][0] <= t) lo = m; else hi = m; }
  const a = arr[lo], b = arr[hi];
  if (b[0] === a[0]) return a;
  const f = (t - a[0]) / (b[0] - a[0]);
  return [t, a[1]+(b[1]-a[1])*f, a[2]+(b[2]-a[2])*f, a[3]+(b[3]-a[3])*f,
          a[4]+(b[4]-a[4])*f, a[5]+(b[5]-a[5])*f, a[6]+(b[6]-a[6])*f];
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

let focusNpc = null;
const slider = document.getElementById('slider');
const timeEl = document.getElementById('time');
slider.min = tMin; slider.max = Math.max(tMax, tMin + 1); slider.value = tMin;
const tgCam = document.getElementById('tgCam');
const tgTw = document.getElementById('tgTw');
tgCam.addEventListener('change', draw);
tgTw.addEventListener('change', draw);

function draw() {
  const t = +slider.value;
  timeEl.textContent = fmt(t - tMin);   // clock relative to first data
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cv.clientWidth, cv.clientHeight);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  const mw = 2 * view.half;
  ctx.drawImage(mapImg, view.ox - mw / 2, view.oy - mw / 2, mw, mw);

  // ---- neutral camps + Roshan pit ----
  if (tgCam.checked) {
    const cr = Math.max(3, mw / 260);
    ctx.lineWidth = Math.max(1, mw / 1000);
    for (const c of (DATA.camps || [])) {
      const [x, y] = w2s(c[0], c[1]);
      const nt = c[2];
      ctx.fillStyle = nt === 3 ? '#e8b64c' : nt === 2 ? '#aab6c8'
                   : nt === 1 ? '#8394ac' : '#66778f';
      ctx.strokeStyle = 'rgba(8,10,14,.9)';
      ctx.beginPath();
      if (nt === 3) {                       // ancient tier: diamond
        ctx.moveTo(x, y - cr * 1.6);
        ctx.lineTo(x + cr * 1.5, y);
        ctx.lineTo(x, y + cr * 1.6);
        ctx.lineTo(x - cr * 1.5, y);
      } else {                              // triangle (tier-coded)
        ctx.moveTo(x, y - cr * 1.1);
        ctx.lineTo(x + cr * .9, y + cr * .8);
        ctx.lineTo(x - cr * .9, y + cr * .8);
      }
      ctx.closePath(); ctx.fill(); ctx.stroke();
    }
    if (DATA.roshan) {
      const [x, y] = w2s(DATA.roshan[0], DATA.roshan[1]);
      ctx.strokeStyle = 'rgba(224,101,95,.8)';
      ctx.lineWidth = Math.max(1.6, mw / 700);
      ctx.beginPath(); ctx.arc(x, y, Math.max(5, mw / 105), 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  // ---- tower survival ----
  if (tgTw.checked) {
    const tr = Math.max(4.5, mw / 150);
    for (const tw of (DATA.towers || [])) {
      const dead = tw.d != null && t >= tw.d;
      const [x, y] = w2s(tw.x, tw.y);
      ctx.beginPath(); ctx.arc(x, y, tr, 0, Math.PI * 2);
      ctx.fillStyle = dead ? '#4c525e' : (TEAMC[tw.team] || '#ccc');
      ctx.fill();
      ctx.lineWidth = Math.max(1, tr * .3);
      ctx.strokeStyle = '#0b0d12';
      ctx.stroke();
      if (dead) {
        ctx.strokeStyle = '#22262e';
        ctx.lineWidth = Math.max(1.5, tr * .32);
        ctx.beginPath();
        ctx.moveTo(x - tr * .5, y - tr * .5); ctx.lineTo(x + tr * .5, y + tr * .5);
        ctx.moveTo(x + tr * .5, y - tr * .5); ctx.lineTo(x - tr * .5, y + tr * .5);
        ctx.stroke();
      }
    }
  }

  const iw = Math.max(28, mw / 36), ih = iw * 9 / 16;
  const listEl = document.getElementById('herolist');
  listEl.innerHTML = '';
  for (const h of heroes) {
    const st = stateAt(h.arr, t);
    const [x, y] = w2s(st[1], st[2]);
    const col = TEAMC[h.team] || '#ccc';
    if (imgs[h.npc]) {
      ctx.save();
      ctx.shadowColor = 'rgba(0,0,0,.55)'; ctx.shadowBlur = 4;
      ctx.drawImage(imgs[h.npc], x - iw / 2, y - ih / 2, iw, ih);
      ctx.restore();
      ctx.strokeStyle = col; ctx.lineWidth = 2;
      ctx.strokeRect(x - iw / 2 - 1, y - ih / 2 - 1, iw + 2, ih + 2);
      if (focusNpc === h.npc) {
        ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5;
        ctx.strokeRect(x - iw / 2 - 3, y - ih / 2 - 3, iw + 6, ih + 6);
      }
    } else {
      ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI * 2);
      ctx.fillStyle = col; ctx.fill();
      ctx.strokeStyle = '#0b0d12'; ctx.lineWidth = 2; ctx.stroke();
    }
    // live HP + mana bars under the marker, plus numbers when zoomed in
    const hf = st[4] ? Math.max(0, Math.min(1, st[3] / st[4])) : 0;
    const mf = st[6] ? Math.max(0, Math.min(1, st[5] / st[6])) : 0;
    const bw = iw * 1.05, bh = 3.5;
    const by = y + ih / 2 + 3;
    ctx.fillStyle = 'rgba(0,0,0,.62)';
    ctx.fillRect(x - bw / 2 - 1, by - 1, bw + 2, bh * 2 + 3);
    ctx.fillStyle = hf > .3 ? '#2ecc71' : '#e74c3c';
    ctx.fillRect(x - bw / 2, by, Math.max(0.5, bw * hf), bh);
    ctx.fillStyle = '#3498db';
    ctx.fillRect(x - bw / 2, by + bh + 1, Math.max(0.5, bw * mf), bh);
    if (iw >= 44) {
      const fs = Math.max(10, Math.round(iw * 0.16));
      ctx.font = '600 ' + fs + 'px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'alphabetic';
      const ty = y - ih / 2 - 7;
      const label = Math.round(st[3]) + '/' + st[4] + '  ♥' + Math.round(st[5]);
      ctx.fillStyle = 'rgba(0,0,0,.6)';
      ctx.fillRect(x - iw * 0.62, ty - fs + 2, iw * 1.24, fs + 4);
      ctx.fillStyle = '#fff';
      ctx.fillText(label, x, ty + 1);
    }
    // side row
    const row = document.createElement('div');
    row.className = 'hrow' + (focusNpc === h.npc ? ' sel' : '');
    row.innerHTML =
      '<span class="icowrap" style="border:2px solid ' + col + '">' +
      (imgs[h.npc] ? '<img src="data:image/png;base64,' + ICONS[h.npc] + '">'
                   : '<span style="color:' + col + '">●</span>') + '</span>' +
      '<span class="nm" title="' + escapeHtml(h.name) + '">' + escapeHtml(h.name) + '</span>' +
      '<span class="bars"><span class="hbar"><i style="width:' + (hf * 100).toFixed(1) +
        '%"></i></span><span class="mbar"><i style="width:' + (mf * 100).toFixed(1) +
        '%"></i></span></span>' +
      '<span class="pv">' + Math.round(st[3]) + '/' + st[4] + ' · ' +
        Math.round(st[5]) + '</span>';
    row.onclick = () => {
      focusNpc = (focusNpc === h.npc) ? null : h.npc;
      const s2 = stateAt(h.arr, +slider.value);
      const [fx, fy] = w2s(s2[1], s2[2]);
      view.ox = fx; view.oy = fy;          // centre the clicked hero
      draw();
    };
    listEl.appendChild(row);
  }
  // self-test: green pixel bottom-right + blue bottom-left only if canvas
  // paint actually lands; red text when the map image is unusable
  ctx.fillStyle = '#00e000'; ctx.fillRect(cv.clientWidth - 10, cv.clientHeight - 10, 8, 8);
  ctx.fillStyle = '#0088ff'; ctx.fillRect(2, cv.clientHeight - 10, 8, 8);
  if (mapImg.complete && !mapImg.naturalWidth) {
    showErr('地图图像 0x0（decode fail）');
  }
  // debug panel: append "#dbg" to the URL and reload
  if ((location.hash || '').indexOf('dbg') >= 0) {
    const el = document.getElementById('dbg');
    el.style.display = 'block';
    el.textContent = 'v__VER__' +
      '\nmap lite=' + useLite +
      ' nat=' + mapImg.naturalWidth + 'x' + mapImg.naturalHeight +
      ' complete=' + mapImg.complete +
      '\ncanvas css=' + cv.clientWidth + 'x' + cv.clientHeight +
      ' px=' + cv.width + 'x' + cv.height +
      '\nview.half=' + Math.round(view.half) +
      ' dpr=' + (window.devicePixelRatio || 1) +
      ' heroes=' + heroes.length +
      ' towers=' + (DATA.towers || []).length;
  }
}

// ---- zoom / pan ----
function zoomAt(f, cx, cy) {
  const old = view.half;
  view.half = Math.min(1500, Math.max(60, old * f));
  const k = view.half / old;
  view.ox = cx - (cx - view.ox) * k;
  view.oy = cy - (cy - view.oy) * k;
  draw();
}
cv.addEventListener('wheel', e => {
  e.preventDefault();
  const r = cv.getBoundingClientRect();
  zoomAt(e.deltaY < 0 ? 1.25 : 0.8, e.clientX - r.left, e.clientY - r.top);
}, {passive: false});
cv.addEventListener('wheel', e => {
  e.preventDefault();
  const r = cv.getBoundingClientRect();
  zoomAt(e.deltaY < 0 ? 1.25 : 0.8, e.clientX - r.left, e.clientY - r.top);
}, {passive: false});

// pointer-based pan (mouse + touch) with two-finger pinch zoom
const pts = new Map();
let pinch0 = null; // {cx, cy, dist, half} in canvas-local px at pinch start
cv.addEventListener('pointerdown', e => {
  cv.setPointerCapture(e.pointerId);
  pts.set(e.pointerId, {x: e.clientX, y: e.clientY});
  if (pts.size === 2) {
    const a = [...pts.values()];
    const r = cv.getBoundingClientRect();
    pinch0 = {
      cx: (a[0].x + a[1].x) / 2 - r.left,
      cy: (a[0].y + a[1].y) / 2 - r.top,
      dist: Math.max(1, Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y)),
      half: view.half,
    };
  }
});
cv.addEventListener('pointermove', e => {
  if (!pts.has(e.pointerId)) return;
  const prev = pts.get(e.pointerId);
  pts.set(e.pointerId, {x: e.clientX, y: e.clientY});
  if (pts.size === 2 && pinch0) {
    const a = [...pts.values()];
    const dist = Math.max(1, Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y));
    const newHalf = Math.min(1500, Math.max(60, pinch0.half * dist / pinch0.dist));
    const k = newHalf / view.half;
    view.ox = pinch0.cx - (pinch0.cx - view.ox) * k;
    view.oy = pinch0.cy - (pinch0.cy - view.oy) * k;
    view.half = newHalf;
    draw();
  } else if (pts.size === 1) {
    view.ox += e.clientX - prev.x;
    view.oy += e.clientY - prev.y;
    draw();
  }
});
const endPtr = e => {
  pts.delete(e.pointerId);
  if (pts.size < 2) pinch0 = null;
};
cv.addEventListener('pointerup', endPtr);
cv.addEventListener('pointercancel', endPtr);
cv.addEventListener('lostpointercapture', endPtr);
document.getElementById('zin').onclick = () => {
  const r = cv.getBoundingClientRect(); zoomAt(1.25, r.width / 2, r.height / 2); };
document.getElementById('zout').onclick = () => {
  const r = cv.getBoundingClientRect(); zoomAt(0.8, r.width / 2, r.height / 2); };
document.getElementById('zfit').onclick = () => { view.fit = 1; layout(); draw(); };

// ---- playback ----
const playBtn = document.getElementById('play');
let timer = null;
function togglePlay() {
  if (timer) { clearInterval(timer); timer = null; playBtn.textContent = '▶'; return; }
  playBtn.textContent = '⏸';
  timer = setInterval(() => {
    let v = +slider.value + 1;
    if (v > slider.max) v = slider.min;
    slider.value = v; draw();
  }, 100);
}
playBtn.onclick = togglePlay;
document.addEventListener('keydown', e => {
  if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
});
slider.addEventListener('input', draw);
window.addEventListener('resize', () => { view.fit = 1; measure(); });
window.addEventListener('orientationchange', () => { view.fit = 1; measure(); });

measure();
if (mapImg.complete) draw();   // paint immediately; map pops in on load anyway
function bootDraw() {
  requestAnimationFrame(() => {
    draw();                 // first frame immediately; icons pop in later
    loadIcons(draw);        // redraw once portraits are ready
  });
}
mapImg.onload = () => { mapReady = true; bootDraw(); };
if (mapImg.complete) bootDraw();
setTimeout(bootDraw, 500);  // retry once image/icon decoding settles
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
