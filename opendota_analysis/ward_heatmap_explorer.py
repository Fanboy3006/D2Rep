#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ward_heatmap_explorer.py - Module A interactive HTML heatmap explorer.

Single self-contained page (double-click to open, no server): shared map
background + every observer ward instance (x, y, lifetime, dewarded/expired)
from all parsed matches. Two range sliders bound the survival window
[lo, hi] — only instances whose lifetime falls inside the window are counted —
plus a show/hide heatmap toggle. The "被反占比" heat overlay and the text
summary recompute live while dragging.

Pairing semantics identical to ward_survival_heatmap.py: per (match, team)
FIFO; observers still alive at match end are censored and counted as expired
with lifetime = end - place.

Usage:
    python opendota_analysis/ward_heatmap_explorer.py
        [--db-root dems/db] [--bin 400] [--max-life 1800]
        [--out dist/ward_heatmap_explorer.html]
"""
import argparse
import base64
import collections
import glob
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from opendota_analysis import map_background as mb  # noqa: E402
from opendota_analysis.ward_survival_heatmap import zone_name  # noqa: E402


def collect_instances(db_root):
    """Return list of [x, y, lifetime, dewarded] per placed observer ward over
    all parsed matches (pairing as documented in module A)."""
    records = []
    dbs = sorted(glob.glob(os.path.join(db_root, "*", "*.db")))
    for db in dbs:
        con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(db).replace("\\", "/"),
                              uri=True)
        end = con.execute("SELECT MAX(game_time_sec) FROM entity_snapshots").fetchone()[0]
        placed = collections.defaultdict(list)
        for sec, team, x, y in con.execute(
                """SELECT game_time_sec, json_extract(properties,'$.team'),
                          x, y FROM game_events WHERE event_type='ward_placed'
                   AND json_extract(properties,'$.ward_type')='observer'"""):
            if team is not None:
                placed[int(team)].append((int(sec), float(x), float(y)))
        destroyed = collections.defaultdict(list)
        for sec, team, reason in con.execute(
                """SELECT game_time_sec, json_extract(properties,'$.team'),
                          json_extract(properties,'$.reason')
                   FROM game_events WHERE event_type='ward_destroyed'
                   AND json_extract(properties,'$.ward_type')='observer'"""):
            if team is not None:
                destroyed[int(team)].append((int(sec), reason))
        con.close()
        for team in set(placed) | set(destroyed):
            q = sorted(placed.get(team, []))
            destroy = sorted(destroyed.get(team, []))
            used = [False] * len(destroy)
            for (sec, x, y) in q:
                match_idx = None
                for i, (dsec, _reason) in enumerate(destroy):
                    if not used[i] and dsec >= sec:
                        match_idx = i
                        break
                if match_idx is not None:
                    used[match_idx] = True
                    dsec, reason = destroy[match_idx]
                    lifetime = dsec - sec
                else:
                    lifetime = max(0, (end or 0) - sec)
                    reason = "expired"
                records.append([int(round(x)), int(round(y)),
                                max(0, lifetime),
                                1 if reason == "dewarded" else 0])
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db-root", default=os.path.join(HERE, "..", "dems", "db"))
    ap.add_argument("--bin", type=int, default=400)
    ap.add_argument("--max-life", type=int, default=1800)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.out is None:
        args.out = os.path.join(HERE, "..", "dist", "ward_heatmap_explorer.html")

    records = collect_instances(args.db_root)
    print("instances:", len(records), "max life:",
          max((r[2] for r in records), default=0))
    data_json = json.dumps(records, separators=(",", ":"))
    with open(os.path.join(HERE, "assets", "dota_map_1024.png"), "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    html = (TEMPLATE.replace("__DATA_JSON__", data_json)
            .replace("__MAP_B64__", b64)
            .replace("__BIN__", str(args.bin))
            .replace("__MAXLIFE__", str(args.max_life))
            .replace("__WORLD__", str(int(mb.WORLD_SPAN))))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", args.out, "(%.1f MB)" % (os.path.getsize(args.out) / 1e6))


TEMPLATE = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>假眼生存/被反热力图探索</title>
<style>
 body{margin:0;font-family:system-ui,'Segoe UI',Roboto,sans-serif;background:#101216;
      color:#dfe3ea;height:100vh;height:100dvh;display:flex;flex-direction:column;overflow:hidden}
 #top{display:flex;align-items:center;gap:12px;padding:8px 12px;background:#171a21;
      border-bottom:1px solid #262b36;flex:none;flex-wrap:wrap}
 #top h1{font-size:15px;margin:0}
 .tgl{display:flex;align-items:center;gap:6px;font-size:13px}
 .tgl input{accent-color:#4da3ff}
 #main{flex:1;display:flex;min-height:0}
 #mapwrap{flex:1;position:relative;overflow:hidden;background:#0a0c10;touch-action:none;min-width:0;min-height:0}
 canvas{display:block}
 #panel{width:300px;border-left:1px solid #262b36;background:#14161c;overflow:auto;padding:12px;flex:none}
 #panel h3{margin:4px 0 6px;font-size:13px;color:#9fb0c8}
 .rg{width:100%;margin:2px 0 10px;accent-color:#4da3ff}
 .vals{font-variant-numeric:tabular-nums;font-size:12px;color:#c9d2e0;margin-bottom:2px}
 #sum{font-size:12px;line-height:1.75}
 #sum b{color:#fff}
 #sum .hot{color:#ffb4ae}
 #grad{height:10px;border-radius:5px;margin:6px 0 2px;
       background:linear-gradient(90deg,#3357ff,#fff0,#ff2f2f)}
 #glab{display:flex;justify-content:space-between;font-size:11px;color:#9aa2b2}
 #hint{position:absolute;bottom:10px;left:12px;font-size:11px;color:#7a8194;background:#000a;
       padding:3px 8px;border-radius:8px;pointer-events:none}
 @media (max-width:860px){
   #main{flex-direction:column}
   #panel{width:100%;height:168px;border-left:none;border-top:1px solid #262b36}
 }
</style>
</head>
<body>
<div id="top">
  <h1>假眼生存/被反热力图 · 全语料</h1>
  <span class="tgl"><label><input type="checkbox" id="tgHeat" checked> 显示热力</label></span>
  <span class="tgl"><label><input type="checkbox" id="tgPoint" checked> 样本点</label></span>
</div>
<div id="main">
  <div id="mapwrap"><canvas id="cv"></canvas>
    <div id="hint">双滑块筛选存活时长区间 · 拖动即时重算</div>
  </div>
  <div id="panel">
    <h3>存活时长区间（秒）</h3>
    <div class="vals">下限 <b id="vLo">300</b> s</div>
    <input class="rg" id="lo" type="range" min="0" max="0" step="5" value="0">
    <div class="vals">上限 <b id="vHi">1800</b> s</div>
    <input class="rg" id="hi" type="range" min="0" max="0" step="5" value="0">
    <h3>文字摘要</h3>
    <div id="sum">…</div>
    <h3>被反占比色阶</h3>
    <div id="grad"></div>
    <div id="glab"><span>0（全自然过期）</span><span>100%（全被反）</span></div>
  </div>
</div>
<script>
"use strict";
const REC = __DATA_JSON__;
const MAPB64 = "data:image/png;base64,__MAP_B64__";
const BIN = __BIN__;            // world units per heat cell
const WORLD = __WORLD__;
const MAXLIFE = __MAXLIFE__;
const half = WORLD / 2;
const nCells = Math.round(WORLD / BIN);

const cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
const mapImg = new Image();
mapImg.src = MAPB64;
const tgHeat = document.getElementById('tgHeat');
const tgPoint = document.getElementById('tgPoint');
const loEl = document.getElementById('lo');
const hiEl = document.getElementById('hi');
const vLo = document.getElementById('vLo');
const vHi = document.getElementById('vHi');
loEl.max = hiEl.max = MAXLIFE;
hiEl.value = Math.min(MAXLIFE, 600);
loEl.value = 300;
vLo.textContent = loEl.value; vHi.textContent = hiEl.value;

function compute() {
  const lo = Math.min(+loEl.value, +hiEl.value);
  const hi = Math.max(+loEl.value, +hiEl.value);
  vLo.textContent = lo; vHi.textContent = hi;
  const grid = new Array(nCells * nCells).fill(null); // [d, e]
  let tot = 0, dTot = 0, eTot = 0;
  for (let i = 0; i < REC.length; i++) {
    const r = REC[i];
    if (r[2] < lo || r[2] > hi) continue;
    tot++; if (r[3]) dTot++; else eTot++;
    const cx = Math.floor((r[0] + half) / BIN);
    const cy = Math.floor((r[1] + half) / BIN);
    if (cx < 0 || cx >= nCells || cy < 0 || cy >= nCells) continue;
    const g = grid[cy * nCells + cx];
    if (g) g[r[3]]++;
    else grid[cy * nCells + cx] = [r[3] ? 1 : 0, r[3] ? 0 : 1];
  }
  drawMap(lo, hi, grid);
  // text summary
  const el = document.getElementById('sum');
  const rate = tot ? (100 * dTot / tot) : 0;
  let hot = [];
  for (let cy = 0; cy < nCells; cy++) for (let cx = 0; cx < nCells; cx++) {
    const g = grid[cy * nCells + cx];
    if (!g) continue;
    const n = g[0] + g[1];
    if (n < 5) continue;
    const wx = (cx + .5) * BIN - half, wy = half - (cy + .5) * BIN;
    hot.push([g[0] / n, n, g[0], wx, wy]);
  }
  hot.sort((a, b) => b[0] - a[0]);
  let h = '';
  for (let i = 0; i < Math.min(6, hot.length); i++) {
    const x = hot[i];
    h += '<div class="hot">被反 ' + Math.round(100 * x[0]) + '% (n=' + x[1] +
         ') @ (' + Math.round(x[3]) + ', ' + Math.round(x[4]) + ')</div>';
  }
  el.innerHTML =
    '区间 [<b>' + lo + ', ' + hi + '</b>] s：样本 <b>' + tot + '</b>（被反 <b>' +
    dTot + '</b> = ' + rate.toFixed(1) + '% · 自然过期 <b>' + eTot + '</b>）<br>' +
    (h ? '高发区（n≥5，按占比）：<br>' + h : '（区间内无 n≥5 的格）');
}

function drawMap(lo, hi, grid) {
  const box = cv.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  cv.style.width = box.width + 'px'; cv.style.height = box.height + 'px';
  cv.width = Math.max(2, Math.round(box.width * dpr));
  cv.height = Math.max(2, Math.round(box.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = box.width, H = box.height;
  ctx.clearRect(0, 0, W, H);
  // map letterboxed square
  const S = Math.min(W, H) - 4;
  const ox = (W - S) / 2, oy = (H - S) / 2;
  ctx.drawImage(mapImg, ox, oy, S, S);
  const scale = S / WORLD;      // px per world unit (square image maps whole span)
  if (tgHeat.checked) {
    const cs = BIN * scale;
    for (let cy = 0; cy < nCells; cy++) {
      for (let cx = 0; cx < nCells; cx++) {
        const g = grid[cy * nCells + cx];
        if (!g) continue;
        const n = g[0] + g[1];
        if (n < 3) continue;
        const ratio = g[0] / n;
        const a = Math.min(175, 30 + 12 * n);
        ctx.fillStyle = 'rgba(' + Math.round(30 + 200 * ratio) + ',40,' +
                        Math.round(60 + 190 * (1 - ratio)) + ',' + a + ')';
        const px = cx * cs + ox, py = cy * cs + oy;
        ctx.fillRect(px, py, cs + .5, cs + .5);
      }
    }
  }
  if (tgPoint.checked) {
    ctx.fillStyle = 'rgba(255,255,255,.22)';
    const du = S / WORLD;         // world units per px
    const dw = Math.max(1, 28 * du), dh = dw;
    for (let i = 0; i < REC.length; i++) {
      const r = REC[i];
      if (r[2] < lo || r[2] > hi) continue;
      const x = ox + ((r[0] + half) / WORLD) * S;
      const y = oy + ((half - r[1]) / WORLD) * S;
      ctx.fillRect(x - dw / 2, y - dh / 2, dw, dh);
    }
  }
}
mapImg.onload = compute;
tgHeat.addEventListener('change', compute);
tgPoint.addEventListener('change', compute);
loEl.addEventListener('input', compute);
hiEl.addEventListener('input', compute);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
