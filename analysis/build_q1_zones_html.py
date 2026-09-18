#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_q1_zones_html.py - Q1(新) 地图经济价值分区 复核页 (全场+3窗口, 每指标自适配色阶, 分帧渲染+进度条)。

读取 q1_value_zones.py 产物  analysis/output_q1/q1_zone_agg.csv(window 列含 all)。
输出  analysis/output_review/q1_value_zones_viewer.html
"""
import csv
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGG = os.path.join(ROOT, "analysis", "output_q1", "q1_zone_agg.csv")
HTML = os.path.join(ROOT, "analysis", "output_review", "q1_value_zones_viewer.html")
WINIDX = {"0-10": 0, "10-20": 1, "20+": 2, "all": 3}


def main():
    cells = {}
    with open(AGG, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (int(r["cell_x"]), int(r["cell_y"]))
            c = cells.setdefault(key, {
                "cx": int(r["cell_x"]), "cy": int(r["cell_y"]),
                "x": float(r["x_center"]), "y": float(r["y_center"]), "z": r["zone"],
                "occ": [0, 0, 0, 0], "gpm": [0.0, 0.0, 0.0, 0.0], "rabs": [0.0, 0.0, 0.0, 0.0],
                "rrel": [0.0, 0.0, 0.0, 0.0], "dabs": [0.0, 0.0, 0.0, 0.0], "drel": [0.0, 0.0, 0.0, 0.0],
                "nm": [0, 0, 0, 0]})
            w = WINIDX[r["window"]]
            c["occ"][w] = int(r["n_hero_sec"])
            c["gpm"][w] = float(r["gpm_per_min"])
            c["rabs"][w] = float(r["r_abs_per_min"])
            c["rrel"][w] = float(r["r_rel_per_min"])
            c["dabs"][w] = float(r["d_abs_per_min"])
            c["drel"][w] = float(r["d_rel_per_min"])
            c["nm"][w] = int(r["n_match"])
    cell_list = list(cells.values())
    cells_json = json.dumps(cell_list, ensure_ascii=False)

    METRICS = [
        ("gpm", "个人GPM", "seq"),
        ("rabs", "天辉整体GPM", "seq"),
        ("rrel", "相对GPM", "div"),
        ("dabs", "夜魇整体GPM", "seq"),
        ("drel", "夜魇相对GPM", "div"),
    ]
    WINDOWS = [("all", "全场"), ("0-10", "0-10"), ("10-20", "10-20"), ("20+", "20+")]

    win_btns = "".join('<button class="wbtn" data-w="%d" onclick="setW(%d)">%s</button>'
                       % (i, i, name) for i, (_, name) in enumerate(WINDOWS))
    met_btns = "".join('<button class="mbtn" data-m="%s" onclick="setM(\'%s\')">%s</button>'
                       % (m, m, label) for m, label, _ in METRICS)

    html = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>Q1 地图经济价值分区</title>
<style>
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#e6edf3;margin:24px}
h1{font-size:20px}.legend{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;margin:12px 0;font-size:13px;line-height:1.7}
.legend h3{margin:0 0 8px}.legend b{color:#79c0ff}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0}
.btn{background:#21262d;border:1px solid #30363d;border-radius:14px;padding:6px 12px;cursor:pointer;font-size:12px;color:#e6edf3}
.btn.active{background:#1f6feb;border-color:#1f6feb}
.lbl{color:#8b949e;font-size:12px;margin-right:2px}.sep{border-left:1px solid #30363d;height:22px;margin:0 6px}
.sliderbar{margin:8px 0;font-size:13px}.sliderbar input[type=range]{width:280px;vertical-align:middle;accent-color:#1f6feb}
#cap{color:#79c0ff;font-weight:700}
#progwrap{display:none;margin:8px 0}.bar{background:#21262d;border:1px solid #30363d;border-radius:6px;height:12px;overflow:hidden}
.fill{background:linear-gradient(90deg,#1f6feb,#3fb950);height:100%;width:0%}
#ptext{color:#8b949e;font-size:12px;margin-top:4px}
.maprow{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}
#hm{background:#0d1117;border:1px solid #30363d;border-radius:6px}
.cbar{font-size:12px;color:#8b949e}.cbar .grad{width:260px;height:14px;border-radius:4px;border:1px solid #30363d;margin-top:4px}
.cbar .t{display:flex;justify-content:space-between;width:260px}
table{border-collapse:collapse;font-size:12px;background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-top:10px;width:100%}
th,td{padding:5px 8px;border-bottom:1px solid #21262d;text-align:right;white-space:nowrap}
th{background:#21262d;cursor:pointer;position:sticky;top:0}td:first-child,th:first-child{text-align:left}
td.gp{color:#3fb950;font-weight:700}td.gn{color:#f85149;font-weight:700}
#f{width:280px;padding:6px;border:1px solid #30363d;border-radius:6px;background:#161b22;color:#e6edf3}
.pgn{color:#8b949e;font-size:12px;margin:6px 0}
</style></head><body>
<h1>Q1：地图经济价值分区（金角/银边/草肚皮）</h1>
<div class="legend"><h3>这是什么 &amp; 怎么看</h3>
<p>地图切成 <b>250 单位/格</b>(80×80),统计<b>英雄站在某格时</b>的三项经济指标,可选<b>全场 / 0-10 / 10-20 / 20+</b>。样本 <b>970 场职业赛</b>,每格 n 较小,本页为<b>描述性</b>;反候果(优势才去对面野区)请读者自行领会。</p>
<ul>
<li><b>个人GPM</b>=该格金币/该格英雄秒×60(英雄在该格赚多少金/分)。</li>
<li><b>整体GPM</b>=某队有人在该格→该队逐秒金币增量/分。</li>
<li><b>相对GPM</b>=某队有人在该格→(己方−对方)逐秒金币差/分(正=拉大,负=被拉开)。</li>
<li><b>色阶</b>:顺序指标冷→热;发散指标绿(益)/红(损)。<b>每个指标/每个窗口用自己独立的上限</b>(因为三者量级差很多)。</li>
<li><b>颜色滑块</b>=拖拽调整<b>当前指标+当前窗口</b>的色阶上限。</li>
<li><b>分区标注</b>近似(中路/上/下/野区/肉山/三角区),阈值可再校准。<b>点击格子</b>看该格明细(该场/该窗口的逐场 match 级明细在配套数据文件中)。</li>
</ul></div>
<div class="controls"><span class="lbl">窗口</span>@@WINBTNS@@<span class="sep"></span><span class="lbl">指标</span>@@METBTNS@@</div>
<div class="sliderbar"><label><input id="sl" type="range" min="0" max="1000" value="500" oninput="onSlide()"></label>
<span>色阶上限 <b id="cap">500</b></span>（<span id="curdesc"></span>）</div>
<div id="progwrap"><div class="bar"><div class="fill" id="pf"></div></div><div id="ptext"></div></div>
<div class="maprow">
<div id="svgwrap"><svg id="hm" width="640" height="640" viewBox="0 0 640 640"></svg></div>
<div class="cbar"><div class="grad" id="cgrad"></div><div class="t"><span id="cmin"></span><span id="cmax"></span></div></div>
</div>
<h2>逐格明细（当前窗口)</h2>
<input id="f" placeholder="筛选(区/坐标/数值)…" oninput="flt()">
<div class="pgn" id="pgn"></div>
<table id="t"><thead><tr><th>zone</th><th>x,y</th><th>occ</th><th>个人GPM</th><th>天辉整体</th><th>天辉相对</th><th>夜魇整体</th><th>夜魇相对</th><th>场次</th></tr></thead><tbody></tbody></table>
<script>
const CELLS = @@CELLS@@;
const METRICS = @@METRICS@@;
let curw = 0, curm = 'gpm', nextSlice = 0, renderToken = 0;
const svg = document.getElementById('hm');
const SL = document.getElementById('sl'), CAP = document.getElementById('cap'), CURD = document.getElementById('curdesc');
const PROG = document.getElementById('progwrap'), PF = document.getElementById('pf'), PT = document.getElementById('ptext');
// ---- colormap ----
const SEQ = [[0,[13,27,42]],[0.17,[31,73,101]],[0.34,[39,128,163]],[0.52,[86,181,200]],[0.68,[161,216,86]],[0.83,[255,209,102]],[1,[208,0,0]]];
const DIV = [[-1,[178,24,43]],[0,[13,17,23]],[1,[26,152,80]]];
function interp(stops,t){ if(t<=stops[0][0])return stops[0][1]; if(t>=stops[stops.length-1][0])return stops[stops.length-1][1];
 for(let i=0;i<stops.length-1;i++){const a=stops[i],b=stops[i+1]; if(t>=a[0]&&t<=b[0]){const f=(t-a[0])/(b[0]-a[0]); return a[1].map((v,j)=>Math.round(v+(b[1][j]-v)*f));}} return stops[stops.length-1][1]; }
function rgb(c){ return 'rgb('+c[0]+','+c[1]+','+c[2]+')'; }
// ---- per (metric,window) robust upper bound ----
const RANGE_CACHE = {};
function boundsFor(m, w){
  const key = m+'_'+w;
  if (RANGE_CACHE[key]) return RANGE_CACHE[key];
  let vals=[], maxv=0;
  CELLS.forEach(function(c){ if(c['occ'][w]){ const v=Math.abs(c[m][w]); vals.push(v); if(v>maxv)maxv=v; } });
  vals.sort(function(a,b){return a-b;});
  const p90 = vals.length ? vals[Math.floor(vals.length*0.90)] : 1;
  const def = Math.max(1, Math.ceil(p90));
  const mx = Math.max(def, Math.ceil(p90*2));   // 留一点拖动余量
  RANGE_CACHE[key] = {max: mx, def: def};
  return RANGE_CACHE[key];
}
function colormap(t, kind){ return rgb(kind==='div' ? interp(DIV,t) : interp(SEQ,t)); }
function colorOf(v, cap, kind){
  if (kind==='div'){ let t=Math.max(-1,Math.min(1,v/cap)); return rgb(interp(DIV,t)); }
  let t=Math.min(1, v/cap); return rgb(interp(SEQ,t));
}
// ---- chunked render ----
function render(){
  const tok = ++renderToken;
  svg.innerHTML = '';
  const metric = METRICS.find(function(m){return m[0]===curm;});
  const b = boundsFor(curm, curw);
  SL.min = 0; SL.max = b.max; SL.value = b.def; CAP.textContent = b.def;
  // gather
  const items = [];
  CELLS.forEach(function(c){ if(c['occ'][curw]) items.push({c:c, v:c[curm][curw], kind:metric[2]}); });
  const scale = 640/80;
  PROG.style.display = 'block'; PF.style.width = '0%';
  nextSlice = 0; const CH = 400;
  (function step(){
    if (tok !== renderToken) return;
    const frag = document.createDocumentFragment();
    const end = Math.min(nextSlice + CH, items.length);
    for (let i=nextSlice; i<end; i++){
      const it = items[i], c = it.c;
      const x = c.cx*scale, y = (80 - c.cy)*scale;
      const r = document.createElementNS('http://www.w3.org/2000/svg','rect');
      r.setAttribute('x',x); r.setAttribute('y',y); r.setAttribute('width',scale); r.setAttribute('height',scale);
      r.setAttribute('data-v', it.v); r.setAttribute('data-z', c.z);
      r.setAttribute('style','fill:'+colorOf(it.v, b.def, it.kind)+';stroke:#0d1117;stroke-width:0.5');
      r.onclick = (function(cc){ return function(){ alert('格 ('+cc.x+','+cc.y+') '+cc.z+'\\n个人GPM='+cc.gpm[curw]+' 天辉整体='+cc.rabs[curw]+' 天辉相对='+cc.rrel[curw]+'\\n英雄秒='+cc.occ[curw]+' 场次='+cc.nm[curw]); }; })(c);
      frag.appendChild(r);
    }
    svg.appendChild(frag);
    nextSlice = end;
    PF.style.width = Math.round(nextSlice/items.length*100)+'%';
    PT.textContent = '正在绘制 ' + (WINNAMES[curw]) + ' · ' + metric[1] + ' … (' + nextSlice + '/' + items.length + ')';
    if (nextSlice < items.length){ requestAnimationFrame(step); }
    else { PF.style.width='100%'; PROG.style.display='none'; setColorbar(b.def, metric[2], b.max); refreshTable(); }
  })();
}
function setColorbar(cap, kind, mx){
  const grad = document.getElementById('cgrad'), cmin=document.getElementById('cmin'), cmax=document.getElementById('cmax');
  if (kind==='div'){ grad.style.background='linear-gradient(90deg,#b2182b,#0d1117,#1a9850)'; cmin.textContent='-'+cap; cmax.textContent='+'+cap; }
  else { grad.style.background='linear-gradient(90deg,'+rgb(interp(SEQ,0))+','+rgb(interp(SEQ,0.5))+','+rgb(interp(SEQ,1))+')'; cmin.textContent='0'; cmax.textContent=cap; }
}
function onSlide(){ recolor(); }
let recolorTimer = 0;
function recolor(){
  clearTimeout(recolorTimer);
  recolorTimer = setTimeout(function(){ CAP.textContent = SL.value;
    const cap = parseFloat(SL.value); const metric = METRICS.find(function(m){return m[0]===curm;});
    svg.querySelectorAll('rect').forEach(function(r){ const v=parseFloat(r.dataset.v); r.setAttribute('style','fill:'+colorOf(v, cap, metric[2])+';stroke:#0d1117;stroke-width:0.5'); });
  }, 40);
}
function setW(i){ curw = i; document.querySelectorAll('.wbtn').forEach(function(b){b.classList.toggle('active', +b.dataset.w===i);}); render(); }
function setM(m){ curm = m; document.querySelectorAll('.mbtn').forEach(function(b){b.classList.toggle('active', b.dataset.m===m);}); render(); }
// ---- table (top rows of current window) ----
function refreshTable(){
  const tb = document.querySelector('#t tbody'); tb.innerHTML='';
  let items = CELLS.filter(function(c){return c['occ'][curw];});
  items.sort(function(a,b){return b['occ'][curw]-a['occ'][curw];});
  const MAX = 150; items = items.slice(0, MAX);
  const f=function(v){return (v>=0?'+':'')+ (Math.round(v*10)/10);};
  const frag = document.createDocumentFragment();
  items.forEach(function(c){ const tr=document.createElement('tr');
    tr.innerHTML='<td>'+c.z+'</td><td>'+c.x+','+c.y+'</td><td>'+c.occ[curw]+'</td>'
      +'<td class="'+(c.gpm[curw]>0?'gp':'')+'">'+f(c.gpm[curw])+'</td>'
      +'<td class="'+(c.rabs[curw]>0?'gp':'gn')+'">'+f(c.rabs[curw])+'</td>'
      +'<td class="'+(c.rrel[curw]>=0?'gp':'gn')+'">'+f(c.rrel[curw])+'</td>'
      +'<td class="'+(c.dabs[curw]>0?'gp':'gn')+'">'+f(c.dabs[curw])+'</td>'
      +'<td class="'+(c.drel[curw]>=0?'gp':'gn')+'">'+f(c.drel[curw])+'</td><td>'+c.nm[curw]+'</td>';
    frag.appendChild(tr); });
  tb.appendChild(frag);
  document.getElementById('pgn').textContent = WINNAMES[curw]+' · 按英雄秒排序, 显示前 '+MAX+' 格(共 '+CELLS.filter(function(c){return c['occ'][curw];}).length+' 格)';
}
function flt(){ const q=document.getElementById('f').value.toLowerCase(); document.querySelectorAll('#t tbody tr').forEach(function(r){ r.style.display=r.textContent.toLowerCase().indexOf(q)>=0?'':'none'; }); }
document.querySelectorAll('#t th').forEach(function(th,i){ th.onclick=function(){ var tb2=th.closest('table').tBodies[0]; var rows=Array.from(tb2.rows); var d=1;
 rows.sort(function(a,b){var x=parseFloat(a.cells[i].innerText.replace(/[^0-9.-]/g,''))||a.cells[i].innerText,y=parseFloat(b.cells[i].innerText.replace(/[^0-9.-]/g,''))||b.cells[i].innerText;return (x>y?1:x<y?-1:0)*d;}); d=-d; rows.forEach(function(r){tb2.appendChild(r);}); }; });
const WINNAMES = @@WINNAMES@@;
setW(0); setM('gpm');
</script></body></html>"""
    html = (html.replace("@@WINBTNS@@", win_btns).replace("@@METBTNS@@", met_btns)
                .replace("@@CELLS@@", cells_json)
                .replace("@@METRICS@@", json.dumps(METRICS, ensure_ascii=False))
                .replace("@@WINNAMES@@", json.dumps([w for _, w in WINDOWS], ensure_ascii=False)))
    os.makedirs(os.path.dirname(HTML), exist_ok=True)
    open(HTML, "w", encoding="utf-8").write(html)
    print("wrote", HTML, "cells:", len(cell_list))


if __name__ == "__main__":
    main()
