#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_match_viewer_dom.py - Canvas-free, universally-openable match viewer.

Same data / same fold as export_match_viewer.py but rendered with plain
HTML/CSS (no Canvas2D): the map is an <img>, heroes/towers/camps/Roshan are
absolutely positioned DOM elements, the timeline is a native <input type=range>
and the interaction is plain JS. Because the initial board (positions at the
data start) is baked into the markup, the page still shows the full map +
heroes even where JavaScript is disabled (e.g. iOS Quick Look), and is fully
interactive in any real browser / WeChat's Android webview.

Usage:
    python opendota_analysis/export_match_viewer_dom.py <match_id|db>
        [--out dist/viewer_<id>_lite.html] [--map assets/dota_map_902.png]
"""
import base64
import collections
import glob
import io
import json
import os
import sqlite3
import sys
import urllib.request
import ssl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from opendota_analysis import map_annotations as mann  # noqa: E402

ICON_CACHE = os.path.join(HERE, "assets", "hero_icons")
ICON_URLS = [
    "https://cdn.dota2.com/apps/dota2/images/heroes/%s_full.png",
    "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/%s.png",
]
_SSL = ssl._create_unverified_context()
import threading
_LOCK = threading.Lock()


def hero_icon_b64(npc):
    stem = npc[len("npc_dota_hero_"):] if npc.startswith("npc_dota_hero_") else npc
    cached = os.path.join(ICON_CACHE, stem + ".png")
    if os.path.exists(cached):
        with open(cached, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    with _LOCK:
        if os.path.exists(cached):
            with open(cached, "rb") as f:
                return base64.b64encode(f.read()).decode("ascii")
        raw = None
        for tpl in ICON_URLS:
            try:
                req = urllib.request.Request(tpl % stem, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
                    raw = r.read()
                break
            except Exception as e:
                print("  [icon] %s unavailable (%s)" % (stem, e), file=sys.stderr)
        if raw is None:
            return None
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(raw)).resize((128, 72), Image.LANCZOS)
            buf = io.BytesIO(); im.save(buf, "PNG", optimize=True)
            data = buf.getvalue()
        except Exception:
            return None
        os.makedirs(ICON_CACHE, exist_ok=True)
        tmp = cached + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, cached)
        return base64.b64encode(data).decode("ascii")


def find_db(match_id):
    p = os.path.join(HERE, "..", "dems", "db", "*", "%s.db" % match_id)
    hits = glob.glob(p)
    return hits[0] if hits else None


def load_payload(target, step=1):
    """Return (match_id, payload, icons) with the same fold as the canvas viewer."""
    if target.endswith(".db") and os.path.exists(target):
        db = target
        match_id = os.path.basename(db)[:-3]
    else:
        db = find_db(target)
        if not db:
            raise FileNotFoundError("db not found for match %s" % target)
        match_id = target
    con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(db).replace("\\", "/"), uri=True)
    players = []
    for slot, steam, name, hero, team in con.execute(
            "SELECT player_slot, steam_id, player_name, hero_name, team_id "
            "FROM player_identity ORDER BY player_slot"):
        players.append({"slot": slot, "name": name or "", "hero": hero or "",
                        "team": team, "steam": steam})
    hero_npcs = {p["hero"] for p in players}
    series = {}
    for entity_id, t, x, y, hp, extra, team in con.execute(
            """SELECT entity_id, game_time_sec, x, y, hp, extra, team
               FROM entity_snapshots WHERE entity_type='hero'
               AND json_extract(extra,'$.player_slot') IS NOT NULL
               ORDER BY entity_id, game_time_sec"""):
        if entity_id not in hero_npcs:
            continue
        if step > 1 and t % step != 0:
            continue
        e = json.loads(extra)
        arr = series.setdefault(entity_id, [])
        arr.append([t, int(round(x)), int(round(y)), hp if hp is not None else 0,
                    e.get("hp_max") or 0, int(round(e.get("mana") or 0)),
                    int(round(e.get("mana_max") or 0))])
    max_event = con.execute("SELECT MAX(game_time_sec) FROM game_events").fetchone()[0]
    towers = {}
    for etype, sec, tid, x, y, props in con.execute(
            """SELECT event_type, game_time_sec, target_id, x, y, properties
               FROM game_events WHERE event_type IN ('building_spawn','building_destroyed')
               AND json_extract(properties,'$.kind')='tower'"""):
        p = json.loads(props)
        if p.get("team") not in (2, 3) or x is None:
            continue
        if etype == "building_spawn":
            towers.setdefault(tid, {"x": int(round(x)), "y": int(round(y)),
                                    "team": p["team"], "d": None})
        elif tid in towers:
            towers[tid]["d"] = int(sec)
    con.close()
    towers = list(towers.values())

    raw_end = max((a[-1][0] for a in series.values() if a), default=0)
    move_end = 0
    for arr in series.values():
        for i in range(1, len(arr)):
            dt = arr[i][0] - arr[i - 1][0]
            if dt <= 0:
                continue
            dx, dy = arr[i][1] - arr[i - 1][1], arr[i][2] - arr[i - 1][2]
            if (dx * dx + dy * dy) ** .5 / dt >= 100:
                move_end = arr[i][0]
    active_end = raw_end
    last_action = max(move_end, int(max_event) if max_event else 0)
    if raw_end - last_action > 120:
        active_end = min(raw_end, last_action + 90)
    if active_end < raw_end:
        series = {eid: [r for r in a if r[0] <= active_end] for eid, a in series.items()}
        series = {eid: a for eid, a in series.items() if a}

    icons = {}
    for p in players:
        if p["hero"] and p["hero"] in series:
            b = hero_icon_b64(p["hero"])
            if b:
                icons[p["hero"]] = b
    payload = {"match_id": int(match_id), "players": players, "series": series,
               "meta": {"raw_end": raw_end, "active_end": active_end},
               "towers": towers, "camps": [[c[0], c[1], c[2]] for c in mann.NEUTRAL_CAMPS],
               "roshan": list(mann.ROSHAN)}
    return match_id, payload, icons


WORLD = 19134.0


def pct(x, y):
    """world -> percentage inside the square board."""
    half = WORLD / 2
    return (x + half) / WORLD * 100, (half - y) / WORLD * 100


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target")
    ap.add_argument("--out", default=None)
    ap.add_argument("--map", default=os.path.join(HERE, "assets", "dota_map_1024.png"))
    ap.add_argument("--step", type=int, default=1)
    args = ap.parse_args()

    match_id, payload, icons = load_payload(args.target, args.step)
    if args.out is None:
        args.out = os.path.join(HERE, "..", "dist", "viewer_%s_lite.html" % match_id)

    # static positions baked at data start (shows even without JS)
    t0 = min((a[0][0] for a in payload["series"].values() if a), default=0)
    hero_markup = []
    for p in payload["players"]:
        arr = payload["series"].get(p["hero"])
        if not arr:
            continue
        x, y = arr[0][1], arr[0][2]
        left, top = pct(x, y)
        ico = ('<img class="ico" src="data:image/png;base64,%s" alt="">' % icons[p["hero"]]
               if p["hero"] in icons else '')
        hero_markup.append(
            '<div class="hm" data-hero="%s" style="left:%.3f%%;top:%.3f%%">%s'
            '<span class="hbar"><i></i></span><span class="mbar"><i></i></span>'
            '<span class="hnm">%s</span></div>' % (
                p["hero"], left, top, ico, (p["name"] or p["hero"])))
    tower_markup = []
    for tw in payload["towers"]:
        left, top = pct(tw["x"], tw["y"])
        tower_markup.append(
            '<div class="tw t%d%s" style="left:%.3f%%;top:%.3f%%"></div>' % (
                2 if tw["team"] == 2 else 3, " dead" if tw["d"] is not None and tw["d"] <= t0 else "",
                left, top))
    camp_markup = []
    for c in payload["camps"]:
        left, top = pct(c[0], c[1])
        camp_markup.append('<div class="camp ct%d" style="left:%.3f%%;top:%.3f%%"></div>'
                           % (c[2], left, top))
    if payload.get("roshan"):
        left, top = pct(payload["roshan"][0], payload["roshan"][1])
        camp_markup.append('<div class="camp rosh" style="left:%.3f%%;top:%.3f%%"></div>'
                           % (left, top))

    # map image (downscale to 768 for memory friendliness everywhere)
    from PIL import Image
    im = Image.open(args.map).convert("RGB").resize((768, 768), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "PNG", optimize=True)
    map_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    data_json = json.dumps(payload, separators=(",", ":"))
    html = (TEMPLATE
            .replace("__DATA_JSON__", data_json)
            .replace("__MAP_B64__", map_b64)
            .replace("__HEROES__", "\n".join(hero_markup))
            .replace("__TOWERS__", "\n".join(tower_markup))
            .replace("__CAMPS__", "\n".join(camp_markup))
            .replace("__MATCH__", match_id)
            .replace("__T0__", str(int(t0))))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote %s (%.1f MB, %d heroes, %d samples)" %
          (args.out, os.path.getsize(args.out) / 1e6, len(payload["series"]),
           sum(len(v) for v in payload["series"].values())))


TEMPLATE = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dota2 复盘(通用版) · __MATCH__</title>
<style>
 body{margin:0;background:#101216;color:#dfe3ea;font-family:system-ui,sans-serif}
 .top{display:flex;align-items:center;gap:10px;padding:8px 12px;background:#171a21;
      border-bottom:1px solid #262b36;flex-wrap:wrap}
 .top h1{font-size:15px;margin:0;font-weight:600}
 .top .ver{font-size:11px;color:#8fa0b8}
 .wrap{display:flex;flex-direction:row;align-items:flex-start}
 .board{position:relative;flex:1 1 auto;min-width:0;aspect-ratio:1/1;
        max-width:min(96vw,96vh);margin:8px auto;background:#0a0c10;overflow:hidden}
 .board img{position:absolute;inset:0;width:100%;height:100%;object-fit:fill;display:block}
 .mk{position:absolute;transform:translate(-50%,-50%)}
 .camp{width:10px;height:10px;position:absolute;transform:translate(-50%,-50%);opacity:.95}
 .ct3{background:#e8b64c;transform:translate(-50%,-50%) rotate(45deg)}
 .ct2{background:#aab6c8;clip-path:polygon(50% 0,100% 100%,0 100%)}
 .ct1{background:#8394ac;clip-path:polygon(50% 0,100% 100%,0 100%);width:8px;height:8px}
 .ct0{background:#66778f;clip-path:polygon(50% 0,100% 100%,0 100%);width:7px;height:7px}
 .rosh{border:2px solid #e0655f;border-radius:50%;width:14px;height:14px;background:none}
 .tw{width:12px;height:12px;border-radius:50%;position:absolute;transform:translate(-50%,-50%);
     border:2px solid #0b0d12}
 .t2{background:#46d160}.t3{background:#ff5f57}
 .tw.dead{background:#4c525e}
 .tw.dead::after{content:"✕";color:#111;font-size:9px;position:absolute;
     left:50%;top:50%;transform:translate(-50%,-50%)}
 .hm{position:absolute;transform:translate(-50%,-50%);text-align:center;width:52px}
 .hm .ico{width:46px;height:26px;display:block;border:2px solid #555;border-radius:4px}
 .hm .hnm{display:block;font-size:10px;color:#fff;text-shadow:0 0 2px #000;white-space:nowrap;
          overflow:hidden;text-overflow:ellipsis;width:64px;margin:1px auto 0}
 .hm .hbar,.hm .mbar{display:block;height:4px;background:#262a36;margin:1px auto;width:50px;
     border-radius:2px;overflow:hidden}
 .hm .hbar i{display:block;height:100%;background:#2ecc71}
 .hm .mbar i{display:block;height:100%;background:#3498db}
 .panel{width:260px;flex:none;padding:10px;border-left:1px solid #262b36}
 .panel.tgl{display:flex;gap:12px;align-items:center;font-size:12px;margin-bottom:8px}
 .panel input[type=checkbox]{accent-color:#4da3ff}
 .ctrl{display:flex;align-items:center;gap:10px;padding:6px 12px;background:#171a21;
       border-top:1px solid #262b36}
 .ctrl #time{min-width:92px;text-align:center;font-variant-numeric:tabular-nums;font-size:13px}
 .ctrl input[type=range]{flex:1;accent-color:#4da3ff}
 .ctrl button{width:36px;height:28px;background:#1b1f28;color:#dfe3ea;border:1px solid #2c3240;
              border-radius:6px;font-size:14px}
 .note{font-size:12px;color:#9aa2b2;padding:2px 12px 6px}
 @media (max-width:760px){
   .wrap{flex-direction:column}
   .panel{width:100%;border-left:none;border-top:1px solid #262b36;
          display:flex;gap:16px;align-items:center;padding:6px 12px}
   .panel .leg{display:none}
   .board{max-width:96vw}
 }
</style>
</head>
<body>
<div class="top"><h1>Dota2 复盘 · 比赛 __MATCH__
  <span class="ver">兼容版 · 无需浏览器高级特性</span></h1>
</div>
<div class="note">点击"播放"或拖动时间条查看走位；若页面本身不能互动（如部分系统预览），仍会显示初始局面。</div>
<div class="wrap">
  <div class="board"><img src="data:image/png;base64,__MAP_B64__" alt="map">
__CAMPS____TOWERS____HEROES__
  </div>
  <div class="panel">
    <div class="tgl">
      <label><input type="checkbox" id="tgc" checked> 野点</label>
      <label><input type="checkbox" id="tgt" checked> 塔</label>
    </div>
    <div class="leg">
      <div>●天辉　<span style="color:#ff5f57">●</span>夜魇</div>
      <div>▲野点　◆远古　◉肉山　<span style="color:#46d160">◉</span>塔=存活　<span style="color:#4c525e">✕</span>=摧毁</div>
    </div>
  </div>
</div>
<div class="ctrl">
  <button id="play">▶</button>
  <span id="time">0:00</span>
  <input type="range" id="slider" min="__T0__" max="__T0__" step="1" value="__T0__">
</div>
<script>
"use strict";
var DATA = __DATA_JSON__;
var t0 = +document.getElementById('slider').min;
var tMax = t0;
var heroes = [];
(function () {
  var byHero = {};
  for (var i = 0; i < DATA.players.length; i++) {
    var p = DATA.players[i];
    var arr = DATA.series[p.hero];
    if (!arr || !arr.length) continue;
    byHero[p.hero] = arr;
    if (arr[arr.length - 1][0] > tMax) tMax = arr[arr.length - 1][0];
    var el = document.querySelector('.hm[data-hero="' + p.hero + '"]');
    if (el) heroes.push({hero: p.hero, el: el, arr: arr, team: p.team});
  }
  document.getElementById('slider').max = Math.max(tMax, t0 + 1);
  window.__data = DATA;
  var H = 2 * 0; // world half for % calc below
})();
var WORLD = 19134, half = WORLD / 2;
function pct(x, y) { return [(x + half) / WORLD * 100, (half - y) / WORLD * 100]; }
function stateAt(arr, t) {
  var lo = 0, hi = arr.length - 1;
  if (t <= arr[0][0]) return arr[0];
  if (t >= arr[hi][0]) return arr[hi];
  while (hi - lo > 1) { var m = (lo + hi) >> 1; if (arr[m][0] <= t) lo = m; else hi = m; }
  var a = arr[lo], b = arr[hi];
  if (b[0] === a[0]) return a;
  var f = (t - a[0]) / (b[0] - a[0]);
  return [t, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f,
          a[3] + (b[3] - a[3]) * f, a[4] + (b[4] - a[4]) * f,
          a[5] + (b[5] - a[5]) * f, a[6] + (b[6] - a[6]) * f];
}
function fmt(s) { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }
function draw() {
  var t = +document.getElementById('slider').value;
  document.getElementById('time').textContent = fmt(t - t0);
  var showc = document.getElementById('tgc').checked ? '' : 'none';
  var showt = document.getElementById('tgt').checked ? '' : 'none';
  var cs = document.querySelectorAll('.camp'), k;
  for (k = 0; k < cs.length; k++) cs[k].style.display = showc;
  var tws = document.querySelectorAll('.tw');
  for (k = 0; k < tws.length; k++) tws[k].style.display = showt;
  for (var i = 0; i < heroes.length; i++) {
    var h = heroes[i], st = stateAt(h.arr, t);
    var xy = pct(st[1], st[2]);
    h.el.style.left = xy[0] + '%'; h.el.style.top = xy[1] + '%';
    var hf = st[4] ? Math.min(1, st[3] / st[4]) : 0;
    var mf = st[6] ? Math.min(1, st[5] / st[6]) : 0;
    h.el.querySelector('.hbar i').style.width = (hf * 100) + '%';
    h.el.querySelector('.mbar i').style.width = (mf * 100) + '%';
  }
  for (var j = 0; j < DATA.towers.length; j++) {
    var tw = DATA.towers[j], els = document.querySelectorAll('.tw');
    // towers markup order equals DATA.towers order (baked); reuse index j
    if (els[j]) els[j].className = 'tw t' + (tw.team === 2 ? 2 : 3) +
      (tw.d != null && t >= tw.d ? ' dead' : '');
  }
}
document.getElementById('slider').addEventListener('input', draw);
document.getElementById('tgc').addEventListener('change', draw);
document.getElementById('tgt').addEventListener('change', draw);
document.getElementById('play').addEventListener('click', function () {
  var s = document.getElementById('slider');
  if (this._t) { clearInterval(this._t); this._t = null; this.textContent = '▶'; return; }
  this.textContent = '⏸'; var self = this;
  this._t = setInterval(function () {
    var v = +s.value + 1; if (v > s.max) v = s.min; s.value = v; draw();
  }, 100);
});
draw();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
