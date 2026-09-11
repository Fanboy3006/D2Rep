#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_q5_html.py - Q5B 真眼复核页 (最终结构)。
- 队伍 checkbox(天辉/夜魇) + 时间窗 toggle(0-7/7-15/15+) + 平均反眼指标(平均反假眼/平均反真眼/平均总)。
- 颜色=该位置真眼插下后【平均反掉多少眼】(值热力, 非频率)。拆分假眼/真眼/总三个平均。
- 点格子钻取: match_id + 放置/销毁/存活 mm:ss + 反假眼数 + 反真眼数 + 是否被反。
读取 q5_ward.py 产物 q5_sen_win_<cs>.json + q5_sentry_detail_<cs>.csv。自包含双击即开。
"""
import base64
import csv
import collections
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q5 = os.path.join(ROOT, "analysis", "output_q5")
HTML = os.path.join(ROOT, "analysis", "output_review", "q5b_ward_viewer.html")
CS = 172


def main():
    wj = json.load(open(os.path.join(Q5, "q5_sen_win_%d.json" % CS), encoding="utf-8"))
    # compact: [x,y,win,team,n_sen,ng,avg_jy,avg_zy,avg_all,jy_total,zy_total,all_total]
    cells = [[round(c["x"]), round(c["y"]), c["win"], c["team"], c["n_sen"], c.get("ng", 0),
              round(c["avg_jy"], 3), round(c["avg_zy"], 3), round(c["avg_all"], 3),
              c.get("jy_total", 0), c.get("zy_total", 0),
              (c.get("jy_total", 0) or 0) + (c.get("zy_total", 0) or 0)] for c in wj["cells"]]
    data_json = json.dumps({"map_half": 8600.0, "cells": cells, "wins": [w[0] for w in wj["wins"]]},
                           ensure_ascii=False, separators=(",", ":"))
    print("  cells:", len(cells))

    # 各场对战双方队名 -> viewer 高亮显示
    mt_path = os.path.join(Q5, "q5_matches.json")
    try:
        mt = json.load(open(mt_path, encoding="utf-8"))
    except Exception:
        mt = {}
    mt_json = json.dumps(mt, ensure_ascii=False, separators=(",", ":"))
    print("  matches with team:", len(mt))

    # sentry per-placement detail (verify) grouped by cell
    seng = collections.defaultdict(list)
    sd = os.path.join(Q5, "q5_sentry_detail_%d.csv" % CS)
    with open(sd, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = r["cell_x"] + "," + r["cell_y"]
            def _p(s):
                try:
                    return json.loads(s) if (s and s.strip()) else []
                except Exception:
                    return []
            seng[k].append([int(r["match_id"]), r["place_clock"], r["destroy_clock"], r["survival_sec"],
                            int(r["success"]), int(r["was_dewarded"]), int(r["dew_sen"]), int(r["win"]), int(r["team"]),
                            ([float(x) for x in r["dew_times"].split()] if r["dew_times"] else []),
                            ([float(x) for x in r["dws_times"].split()] if r["dws_times"] else []),
                            _p(r.get("dew_obs_pos", "")), _p(r.get("dew_sen_pos", ""))])
    seng_json = json.dumps({k: v for k, v in seng.items()}, ensure_ascii=False, separators=(",", ":"))

    map_png = os.path.join(ROOT, "analysis", "output_review", "_q5_map_annot.png")
    map_b64 = base64.b64encode(open(map_png, "rb").read()).decode("ascii")

    # 官方眼图标(假眼=observer, 真眼=主视truesight), 用于在地图上标注被反眼位置
    _icons_dir = os.path.join(ROOT, ".tmp", "redota_src", "public", "images", "icons")
    _obs_icon = os.path.join(_icons_dir, "npc_dota_ward_base.png")
    _sen_icon = os.path.join(_icons_dir, "npc_dota_ward_base_truesight.png")
    try:
        obs_b64 = base64.b64encode(open(_obs_icon, "rb").read()).decode("ascii")
        sen_b64 = base64.b64encode(open(_sen_icon, "rb").read()).decode("ascii")
    except Exception:
        obs_b64 = sen_b64 = ""

    team_boxes = '<label class="toggle"><input type="checkbox" class="tmchk" data-t="2" checked onchange="setTeam()">天辉</label><label class="toggle"><input type="checkbox" class="tmchk" data-t="3" checked onchange="setTeam()">夜魇</label>'
    win_btns = '<button class="btn wbtn" data-w="-1" onclick="setWin(-1)">全部</button><button class="btn wbtn" data-w="0" onclick="setWin(0)">0-7分</button><button class="btn wbtn" data-w="1" onclick="setWin(1)">7-15分</button><button class="btn wbtn" data-w="2" onclick="setWin(2)">15分后</button>'
    met_btns = '<span class="lbl">反眼率·平均反假眼</span><button class="btn vbtn" data-m="avg_jy" onclick="setMetric(\'avg_jy\')">平均反假眼</button><span class="sep"></span><span class="lbl">其它平均</span><button class="btn vbtn" data-m="avg_zy" onclick="setMetric(\'avg_zy\')">平均反真眼</button><button class="btn vbtn" data-m="avg_all" onclick="setMetric(\'avg_all\')">平均总反眼</button><span class="sep"></span><span class="lbl">总反眼数</span><button class="btn vbtn" data-m="jy_total" onclick="setMetric(\'jy_total\')">总反假眼数</button><button class="btn vbtn" data-m="zy_total" onclick="setMetric(\'zy_total\')">总反真眼数</button><button class="btn vbtn" data-m="all_total" onclick="setMetric(\'all_total\')">总反眼数</button>'

    html = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>Q5B 真眼</title>
<style>
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#e6edf3;margin:24px}
h1{font-size:20px}.legend{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;margin:12px 0;font-size:13px;line-height:1.7}
.legend h3{margin:0 0 8px}.legend b{color:#79c0ff}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0}
.btn{background:#21262d;border:1px solid #30363d;border-radius:14px;padding:6px 12px;cursor:pointer;font-size:12px;color:#e6edf3}
.btn.resetz{background:#c9510c;border:1px solid #ff7b3d;color:#fff;font-weight:700;padding:8px 16px;font-size:13px}
.btn.active{background:#1f6feb;border-color:#1f6feb}
.lbl{color:#8b949e;font-size:12px;margin-right:2px}.sep{border-left:1px solid #30363d;height:22px;margin:0 6px}
.sliderbar{margin:8px 0;font-size:13px}.sliderbar input[type=range]{width:280px;vertical-align:middle;accent-color:#1f6feb}
#cap{color:#79c0ff;font-weight:700}
#cv{border:1px solid #30363d;border-radius:6px;background:#0d1117}
.maprow{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}
.cbar{font-size:12px;color:#8b949e}.cbar .grad{width:260px;height:14px;border-radius:4px;border:1px solid #30363d;margin-top:4px}
.cbar .t{display:flex;justify-content:space-between;width:260px}
table{border-collapse:collapse;font-size:12px;background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-top:10px;width:100%}
th,td{padding:5px 8px;border-bottom:1px solid #21262d;text-align:right;white-space:nowrap}
th{background:#21262d}td:first-child,th:first-child{text-align:left}
#curdesc{color:#8b949e;font-size:12px}.pgn{color:#8b949e;font-size:12px;margin:6px 0}
.toggle{display:inline-flex;align-items:center;gap:6px;background:#161b22;border:1px solid #30363d;border-radius:14px;padding:4px 12px;cursor:pointer;font-size:13px}
.toggle input{width:15px;height:15px;accent-color:#1f6feb}
.wrap{display:flex;gap:12px;align-items:flex-start}
.left{flex:1;min-width:0}
.maprow{position:relative;display:block}
.mapoverlay{display:flex;flex-wrap:wrap;gap:14px;align-items:center;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 12px;margin:0 0 8px;max-width:100%;font-size:12px;color:#8b949e;justify-content:flex-start}
.mapoverlay .cbar .grad{width:200px;height:14px;border-radius:4px;border:1px solid #30363d;margin-top:4px}
.mapoverlay .cbar .t{display:flex;justify-content:space-between;width:200px}
.mapoverlay .sliderbar{margin:0;font-size:12px}
.mapoverlay .sliderbar input[type=range]{width:180px;vertical-align:middle;accent-color:#1f6feb}
.right{width:360px;position:sticky;top:12px;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;max-height:92vh;overflow:auto}
.drillsum{background:#21262d;border:1px solid #30363d;border-radius:6px;padding:8px 10px;margin-bottom:8px;font-size:13px;line-height:1.6}
.drillsum b{color:#79c0ff}.drillsum .pos{color:#3fb950;font-weight:700}
tr.hoverrow{background:#1f6feb33;outline:1px solid #1f6feb}
@media(max-width:960px){ .wrap{flex-direction:column} .right{width:100%;position:static;max-height:none} .mapoverlay{position:static;margin-top:8px} }
</style></head><body>
<h1>Q5B：真眼(Sentry)——每位置平均能反多少眼（假眼/真眼/总）</h1>
<div class="legend"><h3>怎么看（真眼; 假眼位置另立 Q6）</h3>
<p>真眼按放置点分格(100×100, 实际坐标范围保全图)。样本<b>970 场职业赛</b>, 描述性; 底图为真实Dota地图(标定对齐)。</p>
<ul>
<li><b>颜色=该位置真眼插下后【平均反掉的敌方假眼数】</b>(反眼率核心)——不是"反到几个眼都算", 而是<b>只算反掉的敌方假眼</b>, 每支真眼的平均值, 防止个别高反眼真眼把整格抬成"高价值"。</li>
<li><b>几类指标</b>(toggle):
  · <b>反眼率·平均反假眼</b>(默认) = 每支真眼<b>平均反掉几个敌方假眼</b> —— 热力主指标;
  · <b>其它平均</b> = 平均反真眼 / 平均总反眼(均含反真眼, 非核心口径);
  · <b>总反眼数</b> = 该格全部真眼<b>累计</b>反掉的眼数(总反假眼数/总反真眼数/总反眼数)。</li>
<li><b>队伍 checkbox</b>(天辉/夜魇) + <b>时间窗 toggle</b>(0-7 / 7-15 / 15分后): 只显示勾选队伍+该时间窗插下的真眼。</li>
<li><b>颜色滑块</b>=当前指标的值热力上限(各指标单独适配色阶)。</li>
<li><b>手动缩放(自由查看/对比)</b>: 在地图上<b>滚轮</b>以鼠标为中心缩放、<b>按住左键拖动</b>平移。自由缩放时显示<b>所有方块</b>(全图视角), <b>不进入具体格子、不显示真假眼位标注</b>, 便于对照不同区域。</li>
<li><b>点格子</b>: 点一个方块 → <b>自动放大进入该格</b>(此时只显示该格 + 该格真眼反掉的真假眼位标注), 右侧显示该格明细(match_id + 放置/销毁/存活 mm:ss + 反假眼数 + 反真眼数 + 被反?), 可到录像核对; <b>再点同一格</b> → 缩放回去。</li>
<li><b>点击某支被反假眼/真眼图标</b>: 锁定显示该笔事件的<b>比赛ID + 事件发生地点(坐标) + 双方队名 + 反眼队伍</b>, 直到再次点击地图。</li>
<li><b>恢复正常大小按钮</b>: 一键回到全图(无论自动进入还是手动缩放)。</li>
</ul></div>
<div class="controls"><span class="lbl">队伍</span>@@TEAMBOX@@<span class="sep"></span><span class="lbl">时间窗</span>@@WINBTNS@@<span class="sep"></span><span class="lbl">平均指标</span>@@METBTNS@@<span class="sep"></span><button class="btn resetz" onclick="resetZoom()">↺ 恢复正常大小</button></div>
<div class="sliderbar"><label>底图透明度 <input id="mop" type="range" min="0" max="100" value="35" oninput="setMapOp()"></label> <span id="moppct">35%</span>（0=纯黑底,100=完整地图）</div>
<div class="wrap">
<div class="left">
<div class="maprow">
<div class="mapoverlay">
<div class="cbar"><div class="grad" id="cgrad"></div><div class="t"><span id="cmin"></span><span id="cmax"></span></div></div>
<div class="sliderbar"><label><input id="sl" type="range" min="0.05" max="3" step="0.05" value="1" oninput="onSlide()"></label> <span>值热力上限 <b id="cap">1.0</b></span> <button class="btn" onclick="resetCap()">重置自动</button><br><span id="curdesc"></span></div>
<div class="sliderbar"><label><input id="ns" type="range" min="0" max="30" value="0" oninput="setN()"> 位置出现次数(真眼数)下限</label><span id="nsv">0</span></div>
</div>
<canvas id="cv" width="1024" height="1024"></canvas>
</div>
</div>
<div class="right">
<h3>真眼明细（点击地图格子）</h3>
<p class="pgn">点一个真眼格子, 右侧显示该格该时间窗该队伍的真眼: match_id、放置/销毁/存活(mm:ss)、反假眼数、反真眼数、被反?。可据此到录像核对。
<b>存活 &lt; 420s 且"被反?"=否 = 被比赛结束截断</b>(比赛在远古被摧毁那刻结束, 真眼没活满寿命; 这类行的存活按"结束−放置"记并标 <b>截断</b>)。</p>
<div id="drillsum" class="drillsum"></div>
<div id="hoverinfo" class="drillsum" style="display:none;margin-top:8px"></div>
<table id="drill"><thead><tr><th>match_id</th><th>队伍</th><th>放置</th><th>销毁</th><th>存活</th><th>反假眼</th><th>反假眼时刻</th><th>反真眼</th><th>反真眼时刻</th><th>被反?</th></tr></thead><tbody></tbody></table></div>
<script>
const DAT = @@DAT@@; const WINC = DAT.cells; const NGAMES=970; const WINNAMES = DAT.wins;
const IDX={x:0,y:1,win:2,team:3,n_sen:4,ng:5,avg_jy:6,avg_zy:7,avg_all:8,jy_total:9,zy_total:10,all_total:11};
const MAPIMG="@@MAPIMG@@"; const CALIB_K=0.049038, CALIB_OFFX=508.3019, CALIB_REF_Y=504.5433;
function w2p(x,y){ return [CALIB_OFFX+CALIB_K*x, CALIB_REF_Y-CALIB_K*y]; }
// zoom-aware view transform: world rect [wx0,wx1,wy0,wy1] -> full canvas
let viewRect=null;   // null = whole map (normal size)
function w2pView(x,y){
  if(!viewRect){ return w2p(x,y); }
  const [wx0,wx1,wy0,wy1]=viewRect;
  const fx=(x-wx0)/(wx1-wx0), fy=(y-wy0)/(wy1-wy0);
  return [fx*CSX, (1-fy)*CSX];
}
function calibFromPx(px,py){
  // invert w2p: world from canvas pixel
  if(!viewRect){
    return [ (px-CALIB_OFFX)/CALIB_K, (CALIB_REF_Y-py)/CALIB_K ];
  }
  const [wx0,wx1,wy0,wy1]=viewRect;
  const fx=px/CSX, fy=1-py/CSX;
  return [ wx0+fx*(wx1-wx0), wy0+fy*(wy1-wy0) ];
}
const bgimg=new Image(); let bgReady=false; bgimg.onload=function(){bgReady=true;draw();}; bgimg.src=MAPIMG;
// 官方眼图标(标注被反真假眼位置)
const obsIcon=new Image(); obsIcon.src="@@OBSICON@@";
const senIcon=new Image(); senIcon.src="@@SENICON@@";
function drawWardIcon(p, isSent){
  const img = isSent ? senIcon : obsIcon;
  const s = viewRect ? 28 : 16;   // 缩放时图标也较大
  if (img && img.complete && img.naturalWidth) {
    ctx.drawImage(img, p[0]-s/2, p[1]-s/2, s, s);
  } else {
    drawDot(p, isSent ? '#f85149' : '#3fb950', isSent ? '真' : '假');
  }
}
const CSX=1024; const cv=document.getElementById('cv'), ctx=cv.getContext('2d');
const SL=document.getElementById('sl'), CAP=document.getElementById('cap'), CURD=document.getElementById('curdesc');
let wsel=0, metric='avg_jy', teamSel={2:true,3:true}, minNsen=0, capValue=null;
let maxN=0; WINC.forEach(function(c){ if(c[IDX.n_sen]>maxN) maxN=c[IDX.n_sen]; }); document.getElementById('ns').max=Math.min(maxN,60); document.getElementById('nsv').textContent='0';
const SEQ=[[0,[54,84,120]],[0.25,[39,128,163]],[0.5,[86,181,200]],[0.7,[161,216,86]],[0.88,[255,209,102]],[1,[214,30,30]]];
function interp(st,t){ if(t<=st[0][0])return st[0][1]; if(t>=st[st.length-1][0])return st[st.length-1][1];
 for(let i=0;i<st.length-1;i++){const a=st[i],b=st[i+1]; if(t>=a[0]&&t<=b[0]){const f=(t-a[0])/(b[0]-a[0]); return a[1].map((v,j)=>Math.round(v+(b[1][j]-v)*f));}} return st[st.length-1][1]; }
function rgb(c){ return 'rgb('+c[0]+','+c[1]+','+c[2]+')'; }
function cellsFor(){ return WINC.filter(function(c){ return (wsel<0 || c[2]===wsel) && teamSel[c[3]] && c[IDX.n_sen]>=minNsen; }); }
function valOf(c){ return c[IDX[metric]]||0; }
const RC={};
function bounds(){ const key=wsel+'_'+metric+'_'+JSON.stringify(teamSel)+'_'+minNsen; if(RC[key])return RC[key];
 let vals=[],fmax=0,maxv=0;
 cellsFor().forEach(function(c){ const v=valOf(c); if(v!=null&&!isNaN(v)){vals.push(v); if(v>maxv)maxv=v;} if(c[IDX.n_sen]>fmax)fmax=c[IDX.n_sen]; });
 vals.sort(function(a,b){return a-b;});
 const n=vals.length;
 const p95=n?vals[Math.min(n-1, Math.floor(n*0.95))]:0;
 const cap=n?Math.max(0.05, (p95>0? p95 : maxv)) : 1; if(!(cap>0)) cap=0.05;
 RC[key]={cap:cap, fmax:fmax, maxv:maxv, n:n}; return RC[key]; }
function refreshNS(){ const mxcells=WINC.filter(function(c){return (wsel<0 || c[2]===wsel) && teamSel[c[3]];});
 let mx=0; mxcells.forEach(function(c){ if(c[IDX.n_sen]>mx)mx=c[IDX.n_sen]; });
 const newMax=Math.max(1, Math.min(mx, 200)); document.getElementById('ns').max=newMax;
 // 不强制回写 minNsen(避免滑块值被程序改导致抖动/死循环); 仅保证值不超出上限
 if(minNsen>newMax){ minNsen=newMax; document.getElementById('nsv').textContent=newMax; } }
let selCX=null, selCY=null;
let annMarks=[];            // 地图上的被反眼标注点 (供 hover 高亮)
let hoverMark=null;         // 当前鼠标悬停的标注点
let pinnedMark=null;        // 点击锁定的标注点 (点到真眼/假眼图标 -> 固定显示直到再点地图)
function drawDot(p,color,label){
  ctx.beginPath(); ctx.arc(p[0],p[1],6,0,Math.PI*2); ctx.fillStyle=color; ctx.fill();
  ctx.strokeStyle='#000'; ctx.lineWidth=1; ctx.stroke();
  ctx.font='bold 11px sans-serif'; ctx.textAlign='center'; ctx.fillStyle=color; ctx.fillText(label,p[0],p[1]-9); ctx.textAlign='left'; }
function render(cap){ ctx.clearRect(0,0,CSX,CSX); ctx.fillStyle='#0d1117'; ctx.fillRect(0,0,CSX,CSX);
 if(bgReady){ ctx.globalAlpha=parseFloat(document.getElementById('mop').value)/100;
   if(viewRect){ // zoom: crop the base map's world-rect and stretch onto canvas
     const [wx0,wx1,wy0,wy1]=viewRect;
     const p0=w2p(wx0,wy1); // world (wx0, wy0-inverted): base-map pixel top-left
     const p1=w2p(wx1,wy0);
     const sx=p0[0], sy=p0[1], sw=p1[0]-p0[0], sh=p1[1]-p0[1];
     ctx.drawImage(bgimg, sx, sy, sw, sh, 0, 0, CSX, CSX);
   } else {
     ctx.drawImage(bgimg,0,0,CSX,CSX);
   }
   ctx.globalAlpha=1;
 }
 let arr=cellsFor();
 // 放大(选中格)时仅显示当前格, 突出被反眼位置; 全图时才显示全部格
 if(viewRect && selCX!=null){ arr = arr.filter(function(c){ return Math.floor((c[IDX.x]+8600)/172)===selCX && Math.floor((c[IDX.y]+8600)/172)===selCY; }); }
 // 当前视图下 1 世界单位 = 多少 px(用于让方块随缩放等比例变化)
 const unitPx = viewRect ? CSX/(viewRect[1]-viewRect[0]) : CSX/(2*8600);
 const CELLW = 172*unitPx;   // 一格 172 世界单位在屏幕上应有的边长
 arr.forEach(function(c){ const v=valOf(c); const p=w2pView(c[0],c[1]);
   const sz=Math.max(3, Math.min(CELLW*0.92, 3+Math.sqrt(c[IDX.n_sen])*2.2 + (viewRect? CELLW*0.3:0)));
   const col=rgb(interp(SEQ,Math.min(1,v/cap))); ctx.globalAlpha=0.85; ctx.fillStyle=col; ctx.fillRect(p[0]-sz/2,p[1]-sz/2,sz,sz); });
 ctx.globalAlpha=1;
 if(selCX!=null){ const cw=(selCX+0.5)*172-8600, ch=(selCY+0.5)*172-8600; const cc=w2pView(cw,ch);
   // 选中格子的格间方块(按地形格 172*172)
   const cellPx=Math.abs(w2pView(selCX*172-8600,0)[0]-w2pView((selCX+1)*172-8600,0)[0]);
   ctx.strokeStyle='#ffffff'; ctx.lineWidth=2; ctx.strokeRect(cc[0]-cellPx/2, cc[1]-cellPx/2, cellPx, cellPx);
   // 外圈高亮圈
   ctx.beginPath(); ctx.arc(cc[0],cc[1], 16, 0, Math.PI*2); ctx.strokeStyle='#f0b429'; ctx.lineWidth=3; ctx.stroke();
   ctx.beginPath(); ctx.arc(cc[0],cc[1], 20, 0, Math.PI*2); ctx.strokeStyle='rgba(240,180,41,0.45)'; ctx.lineWidth=2; ctx.stroke();
   ctx.fillStyle='rgba(255,255,255,0.9)'; ctx.font='bold 13px sans-serif'; ctx.textAlign='center'; ctx.fillText('('+selCX+','+selCY+')', cc[0], cc[1]-22); ctx.textAlign='left'; }
 // 标注: 选中格内真眼反掉的假眼(绿)/真眼(红) 准确位置
 annMarks=[];   // 每次渲染重建, 只含当前窗口(wsel)+队伍(teamSel)筛选下的被反眼
 if(selCX!=null && SENG[selCX+','+selCY]){
   const rows=SENG[selCX+','+selCY].filter(function(r){return (wsel<0 || r[7]===wsel) && teamSel[r[8]];});
   rows.forEach(function(r,ri){
     (r[11]||[]).forEach(function(pt){ if(pt&&pt.length>=2){ const p=w2pView(pt[0],pt[1]); drawWardIcon(p,false); annMarks.push({x:p[0],y:p[1],wx:pt[0],wy:pt[1],tt:pt[2],mid:String(r[0]),ri:ri,kind:'obs',faction:pt[3],dewTeam:pt[4]}); } });
     (r[12]||[]).forEach(function(pt){ if(pt&&pt.length>=2){ const p=w2pView(pt[0],pt[1]); drawWardIcon(p,true); annMarks.push({x:p[0],y:p[1],wx:pt[0],wy:pt[1],tt:pt[2],mid:String(r[0]),ri:ri,kind:'sen',faction:pt[3],dewTeam:pt[4]}); } });
   });
 }

 const grad=document.getElementById('cgrad'), cn=document.getElementById('cmin'), cx=document.getElementById('cmax');
 grad.style.background='linear-gradient(90deg,'+rgb(interp(SEQ,0))+','+rgb(interp(SEQ,0.5))+','+rgb(interp(SEQ,1))+')';
 cn.textContent='0 眼'; cx.textContent=cap.toFixed(2)+' 眼(平均/支)';
 const mname={'avg_jy':'平均反假眼(平均反眼率指标)','avg_zy':'平均反真眼(平均反眼率指标)','avg_all':'平均总反眼(平均反眼率指标)','jy_total':'总反假眼数(总反眼数指标)','zy_total':'总反真眼数(总反眼数指标)','all_total':'总反眼数(总反眼数指标)'}[metric];
 CURD.textContent='队伍:'+Object.keys(teamSel).filter(k=>teamSel[k]).map(k=>k==='2'?'天辉':'夜魇').join('/')+' · '+winName(wsel)+' · '+mname+' · 上限'+cap.toFixed(2); refreshTable(); }
let _drawing=false;
function draw(){ if(_drawing) return; _drawing=true;
 try { refreshNS(); const b=bounds(); SL.min=0; SL.max=Math.max(0.5,Math.round(Math.max(b.maxv,b.cap)*10)/10);
  if(capValue==null) capValue=b.cap;
  // SL.max/SL.min are DOM strings; clamp as NUMBERS then coerce back so capValue stays a number
  let cv=+capValue; if(isNaN(cv) || cv<=0) cv=b.cap;
  if(cv>+SL.max) cv=+SL.max; if(cv<+SL.min) cv=+SL.min;
  capValue=cv;
  SL.value=capValue; CAP.textContent=capValue.toFixed(2); render(capValue); }
 finally { _drawing=false; } }
let _pendingN=false;
function setN(){ if(_pendingN) return; _pendingN=true; requestAnimationFrame(function(){ _pendingN=false;
  minNsen=parseInt(document.getElementById('ns').value); document.getElementById('nsv').textContent=minNsen; draw(); }); }
function onSlide(){ capValue=parseFloat(SL.value); CAP.textContent=capValue.toFixed(2); render(capValue); }
function resetCap(){ capValue=null; draw(); }
function setMapOp(){ document.getElementById('moppct').textContent=document.getElementById('mop').value+'%'; draw(); }
function setTeam(){ teamSel={2:false,3:false}; document.querySelectorAll('.tmchk').forEach(function(b){ teamSel[+b.dataset.t]=b.checked; }); draw(); }
function setWin(w){ wsel=w; document.querySelectorAll('.wbtn').forEach(function(b){b.classList.toggle('active',+b.dataset.w===w);}); draw(); }
function setMetric(m){ metric=m; document.querySelectorAll('.vbtn').forEach(function(b){b.classList.toggle('active',b.dataset.m===m);}); draw(); }
function fmt(sec){ if(sec==null||sec===''||isNaN(sec))return '—'; const v=Math.round(parseFloat(sec)); const bad=(v<0||v>720); const m=Math.floor(Math.abs(v)/60), s=Math.abs(v)%60; return (bad?'⚠':'')+m+':'+(s<10?'0':'')+s; }
function fmtT(sec){ if(sec==null||isNaN(sec))return '—'; const v=Math.round(parseFloat(sec)); const m=Math.floor(Math.abs(v)/60), s=Math.abs(v)%60; return (v<0?'-':'')+m+':'+(s<10?'0':'')+s; }
function winName(w){ return (w<0) ? '全部' : WINNAMES[w]; }
const SENG = @@SENG@@;
const MATCHTEAMS = @@MATCHTEAMS@@;
function setZoom(cx,cy){
  // 以选中格为中心, 放大到该格 ~4 倍(显示约 1/4 全图宽度, 保留邻域上下文)
  const cxm=(cx+0.5)*172-8600, cym=(cy+0.5)*172-8600;
  const half=2200;   // world units half-width => 全图约 17000, 显示~4400 => ~4x 放大
  viewRect=[cxm-half, cxm+half, cym-half, cym+half];
  draw();
}
function resetZoom(){ viewRect=null; selCX=null; selCY=null; draw(); document.querySelector('#drill tbody').innerHTML=''; document.getElementById('drillsum').innerHTML=''; clearHover(); }
// ---- 手动缩放(自由查看/对比): 滚轮缩放 + 按住左键拖拽平移 ----
const FULL=[-8600,8600,-8600,8600];   // 全图世界范围 (MAP_HALF)
function clampView(vr){
  const [wx0,wx1,wy0,wy1]=vr; let w=wx1-wx0,h=wy1-wy0;
  const MAXW=FULL[1]-FULL[0], MAXH=FULL[3]-FULL[2];
  w=Math.max(500, Math.min(w, MAXW)); h=Math.max(500, Math.min(h, MAXH));
  let cx=(wx0+wx1)/2, cy=(wy0+wy1)/2;
  cx=Math.max(FULL[0]+w/2, Math.min(FULL[1]-w/2, cx));
  cy=Math.max(FULL[2]+h/2, Math.min(FULL[3]-h/2, cy));
  return [cx-w/2, cx+w/2, cy-h/2, cy+h/2];
}
cv.onwheel=function(e){ e.preventDefault(); const rect=cv.getBoundingClientRect(); const px=(e.clientX-rect.left)/rect.width*1024, py=(e.clientY-rect.top)/rect.height*1024;
  const [wx,wy]=calibFromPx(px,py);
  let vr=viewRect || FULL.slice();
  const z = e.deltaY<0 ? 0.8 : 1.25;   // 上滚放大, 下滚缩小
  const [x0,x1,y0,y1]=vr; const cw=(x1-x0), ch=(y1-y0);
  const nw=cw*z, nh=ch*z;
  // 保持鼠标下的世界点不动
  const fx=(wx-x0)/cw, fy=(wy-y0)/ch;
  const nx0=wx-fx*nw, nx1=nx0+nw, ny0=wy-fy*nh, ny1=ny0+nh;
  viewRect=clampView([nx0,nx1,ny0,ny1]);
  draw();
};
let drag=null;
cv.onmousedown=function(e){ if(e.button!==0 && e.button!==1) return; e.preventDefault(); const rect=cv.getBoundingClientRect(); const px=(e.clientX-rect.left)/rect.width*1024, py=(e.clientY-rect.top)/rect.height*1024;
  // 若点在某个被反眼图标附近 -> 不进入拖拽(留给 click)
  let near=false;
  (annMarks||[]).forEach(function(m){ if(Math.hypot(px-m.x,py-m.y)<16) near=true; });
  if(near) return;
  drag={sx:px, sy:py, vr: viewRect? viewRect.slice() : FULL.slice()};
  cv.style.cursor='grabbing';
};
cv.onmousemove=function(e){ if(pinnedMark) return; const rect=cv.getBoundingClientRect(); const px=(e.clientX-rect.left)/rect.width*1024, py=(e.clientY-rect.top)/rect.height*1024;
  if(drag){ const [x0,x1,y0,y1]=drag.vr; const w=x1-x0,h=y1-y0;
    const dx=(px-drag.sx)/1024*w, dy=(py-drag.sy)/1024*h;
    viewRect=clampView([x0-dx, x1-dx, y0+dy, y1+dy]); draw(); return; }
  let best=null, bestd=1e9;
  (annMarks||[]).forEach(function(m){ const d=Math.hypot(px-m.x,py-m.y); if(d<Math.min(24, bestd) && d<24){ best=m; bestd=d; } });
  highlightHover(best);
};
cv.onmouseup=function(){ if(drag){ drag=null; cv.style.cursor=''; } };
cv.onmouseleave=function(){ if(pinnedMark) return; if(drag){ drag=null; cv.style.cursor=''; } clearHover(); };
cv.onclick=function(e){ const rect=cv.getBoundingClientRect(); const px=(e.clientX-rect.left)/rect.width*1024, py=(e.clientY-rect.top)/rect.height*1024;
  const [wx,wy]=calibFromPx(px,py); const cx=Math.floor((wx+8600)/172), cy=Math.floor((wy+8600)/172); const key=cx+','+cy;
  // 1) 点击到被反假眼/真眼图标 -> 锁定显示比赛ID + 事件发生地点 (直到再次点击地图)
  let hitMark=null, bestd=1e9;
  (annMarks||[]).forEach(function(m){ const d=Math.hypot(px-m.x,py-m.y); if(d<20 && d<bestd){ hitMark=m; bestd=d; } });
  if(hitMark){ pinnedMark=hitMark; showMarkInfo(hitMark); return; }
  // 2) 点击其它任何地图位置 -> 清除锁定(若有)
  if(pinnedMark){ pinnedMark=null; clearHover(); }
  // 3) 再点同一个已选中格 -> 保持放大(viewRect), 取消"独显该格+标注", 改回显示其它方块(等价于中键拉近看)
  if(viewRect && selCX===cx && selCY===cy){ selCX=null; selCY=null; draw(); clearHover(); return; }
  // 4) 否则进入该格
  selCX=cx; selCY=cy; setZoom(cx,cy); renderDrill(key); };
function showMarkInfo(m){
  const hi=document.getElementById('hoverinfo');
  const info=MATCHTEAMS[m.mid];
  let html='<b>比赛ID</b> '+m.mid+'<br><b>事件发生地点</b> ('+Math.round(m.wx)+', '+Math.round(m.wy)+') '
    +'<span class="pos">'+teamText(m.faction)+'</span> 被反眼<br>'
    +'<b>被摧毁时间</b> '+fmtT(m.tt)
    +' (游戏时钟 mm:ss)';
  if(info){ html+='<br>对战 · 天辉 '+(info['radiant']||'?')+'  vs  夜魇 '+(info['dire']||'?'); }
  html+='<br>反眼队伍 = <span style="color:'+(m.kind==='obs'?'#3fb950':'#f85149')+'">'+teamText(m.dewTeam)+'</span>';
  hi.innerHTML=html; hi.style.display='block';
  // 右侧高亮对应行
  const tb=document.querySelector('#drill tbody');
  Array.prototype.forEach.call(tb.children, function(tr){ tr.classList.toggle('hoverrow', tr.getAttribute('data-mid')===m.mid); });
}
function teamText(t){ return (t===2||t==='2')?'天辉':((t===3||t==='3')?'夜魇':'?'); }
function highlightHover(m){
  const hi=document.getElementById('hoverinfo');
  const tb=document.querySelector('#drill tbody');
  // 高亮右侧对应行
  Array.prototype.forEach.call(tb.children, function(tr){
    tr.classList.toggle('hoverrow', m && tr.getAttribute('data-mid')===m.mid);
  });
  if(!m){ hi.style.display='none'; hi.innerHTML=''; return; }
  const info=MATCHTEAMS[m.mid];
  let html='';
  if(info){ html+='对战 · <b>天辉</b> '+(info['radiant']||'?')+'  vs  <b>夜魇</b> '+(info['dire']||'?'); }
  if(m.kind==='obs'){ html+='<br>反眼队伍(反掉这支假眼)= <span style="color:#3fb950">'+teamText(m.dewTeam)+'</span>'; }
  else { html+='<br>反眼队伍(反掉这支真眼)= <span style="color:#f85149">'+teamText(m.dewTeam)+'</span>'; }
  hi.innerHTML=html; hi.style.display='block';
}
function clearHover(){
  const hi=document.getElementById('hoverinfo'); if(hi){ hi.style.display='none'; hi.innerHTML=''; }
  const tb=document.querySelector('#drill tbody');
  if(tb){ Array.prototype.forEach.call(tb.children, function(tr){ tr.classList.remove('hoverrow'); }); }
}
function renderDrill(key){ const tb=document.querySelector('#drill tbody'); tb.innerHTML='';
  const sum=document.getElementById('drillsum'); sum.innerHTML='';
  if(!SENG[key]){ tb.innerHTML='<tr><td colspan=10>该格无真眼(或未命中)</td></tr>'; sum.innerHTML='<b>本格(未命中)</b>'; return; }
  const rows=SENG[key].filter(function(r){return (wsel<0 || r[7]===wsel) && teamSel[r[8]];}).slice(); rows.sort(function(a,b){return (a[1]||0)-(b[1]||0);});
  if(!rows.length){ tb.innerHTML='<tr><td colspan=10>该格在 '+winName(wsel)+' 该队伍无真眼</td></tr>'; sum.innerHTML='本格在 '+winName(wsel)+' 该队伍无真眼'; return; }
  const n=rows.length; const dw=rows.filter(function(r){return (r[4])>0;}).length;   // 反眼率只算"反到≥1个敌方假眼"
  const aj=rows.reduce(function(a,r){return a+(parseFloat(r[4])||0);},0)/n;
  const az=rows.reduce(function(a,r){return a+(parseFloat(r[6])||0);},0)/n;
  const rate=(dw/n*100);
  sum.innerHTML='本格 <b>('+key+')</b> · 格子坐标 <b>('+selCX+','+selCY+')</b> · '+winName(wsel)+' · 该队伍 · 真眼 <b>'+n+'</b> 支<br>'
    +'<b>反眼率</b> = 反到≥1个<b>敌方假眼</b>的真眼占比 <span class="pos">'+rate.toFixed(1)+'%</span> ('+dw+'/'+n+')<br>'
    +'平均反假眼 <b>'+aj.toFixed(2)+'</b> · 平均反真眼 <b>'+az.toFixed(2)+'</b> · 平均总 <b>'+(aj+az).toFixed(2)+'</b>';
  rows.forEach(function(r,ri){ const tr=document.createElement('tr'); const surv=parseFloat(r[3]); const bad=(!isNaN(surv)&&(surv<0||surv>720));
    // 截断(右删失) = 没被反 且 没活满寿命: 存活窗口被"比赛结束(远古被摧毁)"截断, 存活 = 结束-放置。
    //   (判据可靠: 未被反的真眼 destroy 只有两种取值 —— 放置+420, 或 比赛结束)
    const cens=(!r[5] && !isNaN(surv) && surv<419.5);
    const jt=(r[9]&&r[9].length)? r[9].map(fmtT).join(', ') : '—';
    const zt=(r[10]&&r[10].length)? r[10].map(fmtT).join(', ') : '—';
    tr.setAttribute('data-mid', String(r[0])); tr.setAttribute('data-ri', String(ri));
    tr.innerHTML='<td>'+r[0]+'</td><td>'+((r[8]===2)?'天辉':'夜魇')+'</td><td>'+fmt(r[1])+'</td><td>'+fmt(r[2])+'</td><td'+(bad?' style="color:#f85149"':'')+'>'+fmt(r[3])+(cens?' <span style="color:#d29922">(截断)</span>':'')+'</td><td>'+r[4]+'</td><td>'+jt+'</td><td>'+r[6]+'</td><td>'+zt+'</td><td>'+ (r[5]? '<span style="color:#f85149">是</span>':'否') +'</td>';
    tb.appendChild(tr); });
  document.getElementById('curdesc').textContent='真眼明细 格 '+key+' · '+winName(wsel)+' · '+n+' 支'; }
function refreshTable(){ const tb=document.querySelector('#drill tbody'); if(!tb)return; }
setTeam(); setWin(0); setMetric('avg_jy');
</script></body></html>"""
    html = (html.replace("@@TEAMBOX@@", team_boxes).replace("@@WINBTNS@@", win_btns).replace("@@METBTNS@@", met_btns)
                .replace("@@DAT@@", data_json).replace("@@MAPIMG@@", "data:image/png;base64," + map_b64)
                .replace("@@OBSICON@@", "data:image/png;base64," + obs_b64)
                .replace("@@SENICON@@", "data:image/png;base64," + sen_b64)
                .replace("@@SENG@@", seng_json)
                .replace("@@MATCHTEAMS@@", mt_json))
    os.makedirs(os.path.dirname(HTML), exist_ok=True)
    open(HTML, "w", encoding="utf-8").write(html)
    print("wrote", HTML, "size(MB)=%.1f" % (os.path.getsize(HTML) / 1e6))


if __name__ == "__main__":
    main()
