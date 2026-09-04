#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_match_viewer.py - Module B: single self-contained HTML replay viewer
(cloud-viewer friendly: double-click to open, no server / no Node / no Python).

Embeds: the shared map background PNG (base64) + this match's 10-hero time
series (x, y, hp, hp_max, mana, mana_max per second) as JSON. The page draws
the map on a canvas, moves/updates the heroes on a timeline slider with linear
interpolation between snapshots, and shows HP/mana bars beside each marker and
in a side list.

Usage:
    python opendota_analysis/export_match_viewer.py <match_id | db path>
        [--out dist/viewer_<match_id>.html] [--map assets/dota_map_1024.png]
        [--max-rows 5000]
"""
import argparse
import base64
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def find_db(match_id):
    import glob
    p = os.path.join(HERE, "..", "dems", "db", "*", "%s.db" % match_id)
    hits = glob.glob(p)
    return hits[0] if hits else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="match_id (searched under dems/db) or a db path")
    ap.add_argument("--out", default=None)
    ap.add_argument("--map", default=os.path.join(HERE, "assets", "dota_map_1024.png"))
    args = ap.parse_args()

    if args.target.endswith(".db") and os.path.exists(args.target):
        db = args.target
        match_id = os.path.basename(db)[:-3]
    else:
        db = find_db(args.target)
        if not db:
            sys.exit("db not found for match %s under dems/db" % args.target)
        match_id = args.target
    if args.out is None:
        args.out = os.path.join(HERE, "..", "dist", "viewer_%s.html" % match_id)

    con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(db).replace("\\", "/"), uri=True)
    # header info
    players = []
    for slot, steam, name, hero, team in con.execute(
            "SELECT player_slot, steam_id, player_name, hero_name, team_id "
            "FROM player_identity ORDER BY player_slot"):
        players.append({"slot": slot, "name": name or "", "hero": hero or "",
                        "team": team, "steam": steam})
    # series per hero (player-slot heroes only, entity_id = hero npc)
    series = {}
    rows = con.execute(
        """SELECT entity_id, game_time_sec, x, y, hp, extra, team
           FROM entity_snapshots WHERE entity_type='hero'
           AND json_extract(extra,'$.player_slot') IS NOT NULL
           ORDER BY entity_id, game_time_sec""")
    cur_hero = None
    arr = None
    for entity_id, t, x, y, hp, extra, team in rows:
        if entity_id != cur_hero:
            cur_hero = entity_id
            arr = []
            series[entity_id] = arr
        e = json.loads(extra)
        arr.append([t, int(round(x)), int(round(y)), hp if hp is not None else 0,
                    e.get("hp_max") or 0, int(round(e.get("mana") or 0)),
                    int(round(e.get("mana_max") or 0))])
    con.close()

    payload = {"match_id": int(match_id), "players": players, "series": series}
    data_json = json.dumps(payload, separators=(",", ":"))

    with open(args.map, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    html = TEMPLATE.replace("__DATA_JSON__", data_json).replace(
        "__MAP_B64__", b64).replace("__match__", match_id)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    size_mb = os.path.getsize(args.out) / 1e6
    n = sum(len(v) for v in series.values())
    print("wrote %s (%.2f MB, %d heroes, %d samples)" % (args.out, size_mb, len(series), n))
    sys.exit(0)


TEMPLATE = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Dota2 Replay Viewer - __match__</title>
<style>
 body{margin:0;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;background:#14161c;color:#dfe3ea}
 #wrap{display:flex;height:100vh}
 #mapwrap{flex:1;position:relative;overflow:hidden;display:flex;align-items:center;justify-content:center}
 canvas{max-width:96vw;max-height:88vh;image-rendering:auto}
 #side{width:320px;border-left:1px solid #2a2e3a;padding:12px;overflow:auto;box-sizing:border-box}
 #side h3{margin:4px 0 10px;font-size:15px}
 .hero{display:flex;align-items:center;gap:8px;margin:6px 0;font-size:13px}
 .dot{width:10px;height:10px;border-radius:50%;flex:none}
 .bars{flex:1}
 .bar{height:7px;border-radius:4px;background:#262a36;position:relative;margin:2px 0}
 .bar i{position:absolute;left:0;top:0;bottom:0;border-radius:4px}
 .hp i{background:#2ecc71}.mp i{background:#3498db}
 #controls{border-top:1px solid #2a2e3a;padding:8px 12px;display:flex;align-items:center;gap:12px;position:fixed;bottom:0;left:0;right:320px;background:#0f1116}
 #time{font-variant-numeric:tabular-nums;min-width:110px}
 input[type=range]{flex:1}
 #legend{position:absolute;top:10px;left:10px;background:#0009;padding:6px 10px;border-radius:6px;font-size:12px;line-height:1.7}
</style>
</head>
<body>
<div id="wrap">
 <div id="mapwrap"><canvas id="cv"></canvas>
   <div id="legend"><span style="color:#5fd35f">●</span> 天辉 (radiant)
     <br><span style="color:#ff5f57">●</span> 夜魇 (dire)</div>
 </div>
 <div id="side"><h3>英雄状态</h3><div id="heroes"></div></div>
</div>
<div id="controls">
  <span id="time">0:00</span>
  <input type="range" id="slider" min="0" max="0" step="1" value="0">
  <span>◀▶ 拖动时间查看走位与血蓝（双击英雄可聚焦，未实现）</span>
</div>
<script>
"use strict";
const DATA = __DATA_JSON__;
const MAPB64 = "data:image/png;base64,__MAP_B64__";
const WORLD = 19134; // world units across the map (see map_background.py)
const heroes = [];
for (const hero of Object.keys(DATA.series)) {
  const p = DATA.players.find(x => x.hero === hero);
  heroes.push({id: hero, name: (p && p.name) || hero, team: p ? p.team : null,
               slot: p ? p.slot : -1, arr: DATA.series[hero]});
}
let maxT = 0;
heroes.forEach(h => { if (h.arr.length) maxT = Math.max(maxT, h.arr[h.arr.length-1][0]); });
const mapImg = new Image();
mapImg.src = MAPB64;
const cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
const slider = document.getElementById('slider');
const timeEl = document.getElementById('time');
slider.max = Math.max(maxT, 1);
const SIDE = {2:'#5fd35f', 3:'#ff5f57'};

function stateAt(arr, t) {
  // arr items: [t,x,y,hp,hpMax,mana,manaMax]; linear interp between neighbors
  let lo = 0, hi = arr.length - 1;
  if (t <= arr[0][0]) return arr[0];
  if (t >= arr[hi][0]) return arr[hi];
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (arr[m][0] <= t) lo = m; else hi = m; }
  const a = arr[lo], b = arr[hi];
  if (b[0] === a[0]) return a;
  const f = (t - a[0]) / (b[0] - a[0]);
  return [t,
          a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f,
          a[3] + (b[3] - a[3]) * f, a[4] + (b[4] - a[4]) * f,
          a[5] + (b[5] - a[5]) * f, a[6] + (b[6] - a[6]) * f];
}
function fmt(sec){ const m = Math.floor(sec/60), s = sec % 60;
  return m + ':' + String(s).padStart(2,'0'); }
function draw() {
  const t = +slider.value;
  timeEl.textContent = fmt(t);
  const S = Math.min(cv.clientWidth, cv.clientHeight);
  if (cv.width !== S || cv.height !== S) { cv.width = S; cv.height = S; }
  ctx.clearRect(0, 0, S, S);
  // draw map
  ctx.save();
  ctx.globalAlpha = 1;
  ctx.drawImage(mapImg, 0, 0, S, S);
  // heroes
  const side = document.getElementById('heroes');
  side.innerHTML = '';
  heroes.sort((a, b) => (a.team||0) - (b.team||0));
  for (const h of heroes) {
    if (!h.arr.length) continue;
    const st = stateAt(h.arr, t);
    const px = (st[1] + WORLD/2) / WORLD * S;
    const py = (WORLD/2 - st[2]) / WORLD * S;
    const col = SIDE[h.team] || '#ccc';
    ctx.beginPath(); ctx.arc(px, py, 8, 0, Math.PI * 2);
    ctx.fillStyle = col; ctx.fill();
    ctx.strokeStyle = '#0b0d12'; ctx.lineWidth = 2; ctx.stroke();
    // hp / mana mini-bars beside marker
    const bw = 26, bh = 4;
    ctx.fillStyle = 'rgba(0,0,0,.55)';
    ctx.fillRect(px + 9, py - 10, bw, bh * 2 + 1);
    const hf = st[4] ? Math.max(0, Math.min(1, st[3] / st[4])) : 0;
    ctx.fillStyle = '#2ecc71'; ctx.fillRect(px + 9, py - 10, bw * hf, bh);
    const mf = st[6] ? Math.max(0, Math.min(1, st[5] / st[6])) : 0;
    ctx.fillStyle = '#3498db'; ctx.fillRect(px + 9, py - 5, bw * mf, bh);
    // side list
    const row = document.createElement('div');
    row.className = 'hero';
    row.innerHTML = '<span class="dot" style="background:' + col + '"></span>' +
      '<span style="width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
      escapeHtml(h.name) + '</span>' +
      '<span class="bars"><span class="bar hp"><i style="width:' + (hf*100) + '%"></i></span>' +
      '<span class="bar mp"><i style="width:' + (mf*100) + '%"></i></span></span>' +
      '<span style="min-width:52px;text-align:right">' + Math.round(st[3]) + '/' + st[4] + '</span>';
    side.appendChild(row);
  }
  ctx.restore();
}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
slider.addEventListener('input', draw);
mapImg.onload = draw;
window.addEventListener('resize', draw);
draw();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
