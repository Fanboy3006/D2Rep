#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_q6_html.py - Q6 假眼(Observer)眼位复核页 (产物 q6_ward_viewer.html)。

维度: 【战队】(org, **可多选**) × 时间窗(0-7 / 7-20 / 20+ / 全部) × 格(CS=172, 与 Q5B 同网格)
指标: 平均存活 / 假眼出现次数 / 被反率 / 刁钻率 / 刁钻数
【刁钻眼位】toggle: 存活>1min 且 存活期间 1200 内存在敌方真眼 且 与该真眼最长共存 ≥60s
交互: 滚轮缩放 / 拖拽平移 / 点格钻取 / 三个滑块(底图透明度·值热力上限·出现次数下限)
数据: analysis/output_q6/q6_obs_instances.json (逐支假眼实例) —— **前端聚合**:
      战队维度是高基数(实测 40 支战队), 若按 (格×战队×窗口) 预聚合会膨胀到百万级;
      逐支实例仅 ~4 万条, 前端过滤+分箱只需几毫秒。

自包含单文件(地图与图标以 base64 内嵌), 双击即开 / 可直接发布。
"""
import base64
import json
import os
import time

VERSION = "v17"      # 功能版本号(每次改前端就 +1; 页面顶部会显示, 用来确认浏览器加载的是哪一版)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q6 = os.environ.get("Q6_DIR") or os.path.join(ROOT, "analysis", "output_q6")
HTML = os.environ.get("Q6_HTML") or os.path.join(ROOT, "analysis", "output_review", "q6_ward_viewer.html")


def main():
    d = json.load(open(os.path.join(Q6, "q6_obs_instances.json"), encoding="utf-8"))
    mo = d["match_orgs"]
    mo_list = [mo[str(i)] if str(i) in mo else mo.get(i) for i in range(len(d["mids"]))]
    dat = {"cs": d["cs"], "map_half": d["map_half"], "wins": d["wins"], "tricky": d["tricky"],
           "orgs": d["orgs"], "mids": d["mids"], "match_org": mo_list,
           "cols": d["cols"], "inst": d["inst"]}
    # 构建期守卫: viewer 的列索引由 cols 派生, 少一列就必须在这里炸掉(而不是前端悄悄读错字段)
    for c in ("mi", "x", "y", "team", "win", "place", "destroy", "surv", "dew", "tricky",
              "n_es", "d_min", "ovl", "cens"):
        if c not in d["cols"]:
            raise SystemExit("q6 build: 缺少列 %s (cols=%s)" % (c, d["cols"]))
    dat_json = json.dumps(dat, ensure_ascii=False, separators=(",", ":"))
    print("  orgs:", len(d["orgs"]), " matches:", len(d["mids"]), " instances:", len(d["inst"]))

    map_b64 = base64.b64encode(open(os.path.join(ROOT, "analysis", "output_review", "_q5_map_annot.png"), "rb").read()).decode("ascii")
    _icons = os.path.join(ROOT, ".tmp", "redota_src", "public", "images", "icons")
    try:
        obs_b64 = base64.b64encode(open(os.path.join(_icons, "npc_dota_ward_base.png"), "rb").read()).decode("ascii")
    except Exception:
        obs_b64 = ""

    # 战队多选: 一排 checkbox(可多选); 一个都不勾 = 全部战队
    org_box = "".join(
        '<label class="otog" style="margin:2px 8px 2px 0;display:inline-block;font-size:12px">'
        '<input type="checkbox" class="orgck" data-i="%d" onchange="toggleOrg(%d, this.checked)"> %s</label>'
        % (i, i, n) for i, n in enumerate(d["orgs"]))
    # 阵营 toggle: 全部 / 天辉(2) / 夜魇(3)
    side_btns = "".join(
        '<button class="btn sbtn%s" data-s="%d" onclick="setSide(%d)">%s</button>'
        % (" on" if s < 0 else "", s, s, t)
        for s, t in ((-1, "全部"), (2, "天辉"), (3, "夜魇")))
    win_btns = "".join(
        '<button class="btn wbtn" data-w="%d" onclick="setWin(%d)">%s</button>' % (i, i, w)
        for i, w in enumerate(["0-7分", "7-20分", "20分后"])) + \
        '<button class="btn wbtn" data-w="-1" onclick="setWin(-1)">全部</button>'
    met_btns = (
        '<span class="lbl">存活</span>'
        '<button class="btn vbtn" data-m="avg_surv" onclick="setMetric(\'avg_surv\')">平均存活</button>'
        '<span class="sep"></span><span class="lbl" title="频率类指标: 原始计数, 色标用【对数】避免热点全糊成一片">频率</span>'
        '<button class="btn vbtn" data-m="n_obs" onclick="setMetric(\'n_obs\')" title="该格出现的假眼支数(原始计数, 受该战队场次多寡影响)">假眼出现次数</button>'
        '<button class="btn vbtn" data-m="per_match" onclick="setMetric(\'per_match\')" title="支数 ÷ 当前筛选下的场次数 —— 跨战队/跨窗口可比的真·频率">每场出现次数</button>'
        '<button class="btn vbtn" data-m="use_rate" onclick="setMetric(\'use_rate\')" title="该格至少有 1 支假眼的场次数 ÷ 当前筛选下的场次数 —— 这个点位被使用的概率">出现率</button>'
        '<button class="btn vbtn" data-m="n_match" onclick="setMetric(\'n_match\')" title="该格涉及多少场比赛">出场场次</button>'
        '<span class="sep"></span><span class="lbl">被反</span>'
        '<button class="btn vbtn" data-m="dew_rate" onclick="setMetric(\'dew_rate\')">被反率</button>'
        '<span class="sep"></span><span class="lbl">刁钻</span>'
        '<button class="btn vbtn" data-m="tricky_rate" onclick="setMetric(\'tricky_rate\')">刁钻率</button>'
        '<button class="btn vbtn" data-m="n_tricky" onclick="setMetric(\'n_tricky\')">刁钻数</button>')

    html = TEMPLATE
    for k, v in (("@@DAT@@", dat_json), ("@@MAPIMG@@", "data:image/png;base64," + map_b64),
                 ("@@OBSICON@@", "data:image/png;base64," + obs_b64 if obs_b64 else ""),
                 ("@@ORGBOX@@", org_box), ("@@SIDEBTNS@@", side_btns),
                 ("@@WINBTNS@@", win_btns), ("@@METBTNS@@", met_btns),
                 ("@@BUILD@@", "构建 %s · 功能版本 <b>%s</b>" % (time.strftime("%Y-%m-%d %H:%M"), VERSION))):
        html = html.replace(k, v)
    open(HTML, "w", encoding="utf-8").write(html)
    print("wrote", HTML, "size(MB)=", round(len(html) / 1048576, 2))
    return 0


TEMPLATE = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>Q6 假眼眼位热力（全战队）</title>
<style>
body{font-family:-apple-system,Segoe UI,Roboto,"Microsoft YaHei",sans-serif;background:#0d1117;color:#e6edf3;margin:20px}
h1{font-size:20px;margin:0 0 8px}.legend{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;margin:10px 0;font-size:13px;line-height:1.75}
.legend b{color:#79c0ff}.legend code{background:#0d1117;padding:1px 5px;border-radius:4px;color:#ffa657}
.controls{margin:8px 0 10px;background:#11161d;border:1px solid #21262d;border-radius:8px;padding:8px 12px}
.wrap{display:flex;gap:12px;align-items:flex-start;flex-wrap:nowrap}
/* 布局: 地图左、明细表右。两条硬约束:
   ① 地图不许比窗口还大(正方形画布, 用 min(列宽, 视口高-固定占用) 限制边长) -> 不再超出分辨率;
   ② 控件块放在两栏**之上**(整行), 这样地图与明细表的顶部对齐。 */
.left{flex:1 1 auto;min-width:260px;position:relative;text-align:center}
.right{flex:0 0 clamp(360px,34vw,660px);min-width:300px}
canvas{border:1px solid #30363d;border-radius:8px;cursor:grab;display:block;margin:0 auto;
  width:min(100%, max(320px, calc(100vh - 430px)));height:auto;aspect-ratio:1/1;max-width:100%}
.btn{background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:6px;padding:4px 9px;margin:2px;cursor:pointer;font-size:12px}
.btn.on{background:#1f6feb;border-color:#1f6feb;color:#fff}
select{background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:6px;padding:4px 8px;font-size:12px;max-width:260px}
#orgbox{max-height:92px;overflow:auto;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:5px 8px;margin:4px 0;max-width:640px}
#orgbox label{white-space:nowrap;cursor:pointer}
#orgbox label:hover{color:#79c0ff}
.sliderbar{margin:7px 0;font-size:12px}.sliderbar input[type=range]{width:250px;vertical-align:middle;accent-color:#1f6feb}
table#drill{width:100%;border-collapse:collapse;font-size:12px;margin-top:8px}
#drill th,#drill td{border:1px solid #30363d;padding:3px 6px;text-align:right;white-space:nowrap}
#drill th{background:#161b22;position:sticky;top:0}#drill td.c,#drill th.c{text-align:center}
#drill tbody tr:hover{background:#1c2333}
#gridwrap{max-height:460px;overflow:auto;border:1px solid #30363d;border-radius:8px;margin-top:8px}
#tip{position:absolute;background:#161b22ee;border:1px solid #30363d;border-radius:6px;padding:6px 9px;font-size:12px;pointer-events:none;display:none;z-index:9;white-space:nowrap}
.pgn{font-size:12px;color:#8b949e;margin:6px 0}
</style></head><body>
<h1>Q6 · 全战队假眼(Observer)眼位热力 + 刁钻眼位 <span id="build" style="font-size:12px;font-weight:400;color:#8b949e;margin-left:10px">@@BUILD@@</span></h1>
<div class="legend">
<b>统计单元</b> = 假眼位置(格 <code>172</code> 单位, 与 Q5B 同网格); <b>维度</b> = 战队 × 时间窗(0-7 / 7-20 / 20+ / 全部)。<br>
<b>刁钻眼位</b>(owner 终版) = 假眼 <code>存活 &gt; 1 分钟</code> 且 <code>存活期间 1200 单位内存在敌方真眼</code>(真视 1050 + 缓冲 1200)
且 <b>与该真眼的最长共存 ≥ 60 秒</b> —— 把"近处有真眼却没被反"与"本来就安全 / 真眼刚好到期"分开。<br>
<b><span style="color:#ffa657">■</span> 橙圈</b>=刁钻假眼, <b><span style="color:#79c0ff">■</span> 蓝圈</b>=普通假眼, <b><span style="color:#f85149">■</span> 红圈</b>=被反 —— 图标本身用<b>官方假眼图标</b>(与 Q5B 同款), 颜色编码画在图标外圈。<br>
<b>⚠ 存活截断(右删失)</b>: 比赛在<b>远古被摧毁</b>时结束, 但战斗日志此后仍记录约 6~15 分钟结算残留 ——
未被反的假眼若"放置+360s"晚于比赛结束, 其存活已改为 <b>结束时刻−放置时刻</b> 并标 <b>截断</b>(实测约 9.5%)。
测"平均存活"时可勾选 <b>剔除截断眼</b>(存活时长无完整观测)。<br>
口径继承 Q5B(判型靠实体类名 / 放置用 use 候选窗 / 到期=放置+寿命 / 销毁全局一一对应), 详见 <code>STRATEGY/DEM_FORMAT.md §C6.9</code>。
<b>期间敌方真眼 / 最近距离</b> 两列给出 <b>达标/任意</b> 两个数：<b>达标</b>=共存 ≥60s 的敌方真眼（与刁钻判定同一门槛），<b>任意</b>=半径 1200 内窗口有交集的敌方真眼（哪怕只共存 1 秒）。
<b>最长共存</b> 列给的是<b>任意交集</b>里的最长共存（所以非刁钻行也可能有值）。
</div>
<div class="controls">
 <div><label class="lbl" style="font-size:12px">战队(可多选) </label>
  <button class="btn" onclick="setAllOrg(true)">全选</button>
  <button class="btn" onclick="setAllOrg(false)">清空</button>
  <span id="orgstat" style="font-size:12px;color:#8b949e;margin-left:8px"></span></div>
 <div id="orgbox">@@ORGBOX@@</div>
 <div style="margin:6px 0"><span class="lbl">阵营 </span>@@SIDEBTNS@@
  <span class="sep"></span>@@WINBTNS@@
  <label class="toggle" style="margin-left:10px"><input type="checkbox" id="tk" onchange="setTricky()"> <b style="color:#ffa657">只看刁钻</b></label>
  <label class="toggle" style="margin-left:10px"><input type="checkbox" id="cx" onchange="setCens()"> <b>剔除截断眼</b>(存活被比赛结束截断)</label></div>
 <div style="margin:6px 0">@@METBTNS@@</div>
 <div class="sliderbar" style="display:flex;flex-wrap:wrap;gap:14px;align-items:center">
  <label>底图透明度 <input id="mop" type="range" min="0" max="100" value="35" oninput="setBg()"></label> <span id="moppct">35%</span>
  <label><input id="sl" type="range" min="1" max="400" step="1" value="0" oninput="setCap()"> 值热力上限 <b id="cap">自动</b></label> <button class="btn" onclick="resetCap()">重置自动</button>
  <label><input id="ns" type="range" min="0" max="60" value="0" oninput="setN()"> 位置出现次数(假眼数)下限</label> <span id="nsv">0</span>
  <button class="btn" onclick="resetView()">复位视野</button></div>
 <div class="pgn" id="curdesc" style="margin:2px 0 0"></div>
</div>
<div class="wrap">
 <div class="left">
  <canvas id="cv" width="1024" height="1024"></canvas>
  <div id="tip"></div>
  <div id="dotinfo" style="display:none;font-size:12px;line-height:1.7;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 10px;margin-top:6px"></div>
  <div class="pgn">滚轮缩放 · <b>拖动平移:按住鼠标中键(或右键 / Shift+左键)</b> · <b>点格</b>=切到该格并置为 <b>4 倍档</b>(以该格为中心; 已经更近时只居中、不再放大; 点已选中的格不动) ·
   其它缩放档位用<b>滚轮</b>, 回全图用 <b>复位视野</b> · <b>4 倍档就会画出该格的眼点</b>, <b>点眼点</b>(或点右侧明细表任一行)锁定该眼 · hover 看格子数值</div>
 </div>
 <div class="right">
  <h3 style="margin:8px 0">假眼明细（点地图格子）</h3>
  <div id="sum" class="pgn"></div>
  <div id="gridwrap"><table id="drill"><thead><tr>
   <th class="c">match_id</th><th class="c">格</th><th>战队</th><th class="c">阵营</th><th>放置</th><th>销毁</th><th>存活</th>
   <th class="c">被反 / 存活状态</th><th class="c">刁钻?</th><th>期间敌方真眼<br><span style="font-weight:400;color:#8b949e">达标/任意</span></th><th>最近距离<br><span style="font-weight:400;color:#8b949e">达标/任意</span></th><th>最长共存<br><span style="font-weight:400;color:#8b949e">任意交集</span></th></tr></thead><tbody></tbody></table></div>
 </div>
</div>
<script>
const D = @@DAT@@;
const I = D.inst;
// 列索引由 D.cols 派生 —— 生产者加/删列时前端自动跟随, 不会出现"读错字段"的静默错
const IX = {}; D.cols.forEach(function(c, i){ IX[c] = i; });
["mi","x","y","team","win","place","destroy","surv","dew","tricky","n_es","d_min","ovl","cens",
 "n_es_any","d_min_any","ovl_any"].forEach(function(c){
  if(IX[c] === undefined) throw new Error("viewer: 数据缺少列 " + c);
});
const HALF=D.map_half, CS=D.cs, NG=Math.round(2*HALF/CS);
const ORGS=D.orgs, MIDS=D.mids, MORG=D.match_org, WIN=D.wins, TR=D.tricky;
const MAPIMG="@@MAPIMG@@", OBSICON="@@OBSICON@@";
const CALIB_K=0.049038, CALIB_OFFX=508.3019, CALIB_REF_Y=504.5433, CSX=1024;
const CLICK_ZOOM=4, MINW_CLICK=CS*0.5;   // 点格: 以该格为中心放大 4 倍(最小到半格 86 单位)
const ICON_MIN_ZOOM_W=CS*8;              // 只有视野窄于 8 格(1376 单位)时才画眼点图标(见 drawDots)
const cv=document.getElementById('cv'), ctx=cv.getContext('2d');
const bgimg=new Image(); let bgReady=false; bgimg.onload=function(){bgReady=true;render();}; bgimg.src=MAPIMG;
// ⚠ 这里原先是 draw() —— 页面里根本没有 draw 这个函数(渲染函数叫 render), 真实浏览器里图加载完成时
//   这个回调会抛 ReferenceError, 于是"图加载完不会自动重绘"(得先动一下鼠标/滚轮才出现底图)。已改 render()。
const obsIcon=new Image(); obsIcon.src=OBSICON;

// ---- 状态 ----
let orgSet=[];                  // 已勾选的战队索引(空数组 = 全部战队)
let sideSel=-1;                 // 阵营筛选: -1=全部 / 2=天辉 / 3=夜魇
let wsel=0, trickyOnly=false, censOut=false, metric='avg_surv', minN=0, capValue=null, bgOp=35;
let viewRect=null, selKey=null, locked=null, hoverKey=null, DOTS=[], dotTotal=0, dotCapped=false;
let AGG=null, CAP=10;
const SEQ=[[0,[54,84,120]],[0.25,[39,128,163]],[0.5,[86,181,200]],[0.7,[161,216,86]],[0.88,[255,209,102]],[1,[214,30,30]]];
function interp(st,t){ if(t<=st[0][0])return st[0][1]; if(t>=st[st.length-1][0])return st[st.length-1][1];
 for(let i=0;i<st.length-1;i++){const a=st[i],b=st[i+1]; if(t>=a[0]&&t<=b[0]){const f=(t-a[0])/(b[0]-a[0]); return a[1].map((v,j)=>Math.round(v+(b[1][j]-v)*f));}} return st[st.length-1][1]; }
function rgb(c){ return 'rgb('+c[0]+','+c[1]+','+c[2]+')'; }
function mmss(s){ if(s==null||isNaN(s))return '—'; s=Math.round(s); const m=Math.floor(Math.abs(s)/60), q=Math.abs(s)%60; return (s<0?'-':'')+m+':'+String(q).padStart(2,'0'); }

// ---- 坐标变换(与 Q5B 同一标定) ----
function w2p(x,y){ return [CALIB_OFFX+CALIB_K*x, CALIB_REF_Y-CALIB_K*y]; }
function w2pView(x,y){
  if(!viewRect){ return w2p(x,y); }
  const r=viewRect, fx=(x-r[0])/(r[1]-r[0]), fy=(y-r[2])/(r[3]-r[2]);
  return [fx*CSX, (1-fy)*CSX];
}
function px2world(px,py){
  if(!viewRect){ return [(px-CALIB_OFFX)/CALIB_K, (CALIB_REF_Y-py)/CALIB_K]; }
  const r=viewRect, fx=px/CSX, fy=1-py/CSX;
  return [r[0]+fx*(r[1]-r[0]), r[2]+fy*(r[3]-r[2])];
}
// 视野钳制: 最小 = 半格(避免无限放大), 最大 = 全图; 并保证中心不出图(拖拽也不会飘走)
function clampView(v){
  const MAXW=2*HALF, MINW=CS*0.5;
  let [x0,x1,y0,y1]=v; let w=Math.abs(x1-x0), h=Math.abs(y1-y0);
  w=Math.max(MINW, Math.min(w, MAXW)); h=Math.max(MINW, Math.min(h, MAXW));
  let cx=(x0+x1)/2, cy=(y0+y1)/2;
  cx=Math.max(-HALF+w/2, Math.min(HALF-w/2, cx));
  cy=Math.max(-HALF+h/2, Math.min(HALF-h/2, cy));
  return [cx-w/2, cx+w/2, cy-h/2, cy+h/2];
}

// ---- 聚合(前端, 只在筛选变化时重算) ----
function orgOf(r){ const mo=MORG[r[IX.mi]]; return mo ? mo[r[IX.team]===2?0:1] : -1; }
function orgMatch(r){ return orgSet.length===0 || orgSet.indexOf(orgOf(r))>=0; }
function aggregate(){
  const cells=Object.create(null), list=[], gmis={};      // gmis: 当前筛选下的去重场次(=频率的分母)
  for(let i=0;i<I.length;i++){
    const r=I[i];
    if(!orgMatch(r)) continue;
    if(sideSel>=0 && r[IX.team]!==sideSel) continue;          // 阵营 toggle(天辉/夜魇)
    if(wsel>=0 && r[IX.win]!==wsel) continue;
    if(trickyOnly && !r[IX.tricky]) continue;
    if(censOut && r[IX.cens]) continue;
    list.push(i); gmis[r[IX.mi]]=1;
    const cx=Math.floor((r[IX.x]+HALF)/CS), cy=Math.floor((r[IX.y]+HALF)/CS), k=cx+','+cy;
    let c=cells[k]; if(!c){ c=cells[k]={n:0,ss:0,sd:0,st:0,sc:0,cx:cx,cy:cy,mis:{}}; }
    c.n++; c.ss+=r[IX.surv]; c.sd+=r[IX.dew]; c.st+=r[IX.tricky]; c.sc+=r[IX.cens];
    c.mis[r[IX.mi]]=1;                              // 出场场次(去重)
  }
  AGG={cells:cells,list:list,matchN:Object.keys(gmis).length};   // matchN = 频率指标的分母
  document.getElementById('orgstat').textContent =
    (orgSet.length?('已选 '+orgSet.length+' 支战队'):'全部战队')+(sideSel>0?(' · '+(sideSel===2?'天辉':'夜魇')):'')+
    ' · 该筛选下假眼 '+list.length+' 支 / 场 '+AGG.matchN+' / 格 '+Object.keys(cells).length;
}
function valOf(c){
  if(metric==='avg_surv') return c.ss/c.n;
  if(metric==='n_obs') return c.n;                                  // 原始计数(受场次多寡影响)
  if(metric==='per_match') return c.n/Math.max(1,AGG.matchN);        // 每场出现次数(跨战队可比)
  if(metric==='use_rate') return 100*Object.keys(c.mis).length/Math.max(1,AGG.matchN);  // 出现率%
  if(metric==='n_match') return Object.keys(c.mis).length;           // 出场场次
  if(metric==='dew_rate') return 100*c.sd/c.n;
  if(metric==='tricky_rate') return 100*c.st/c.n;
  if(metric==='n_tricky') return c.st;
  return c.n;
}
function unitOf(){ return metric==='avg_surv' ? 's' : (metric==='per_match' ? '次/场'
  : (metric.indexOf('rate')>=0 ? '%' : '')); }
// 色标: 计数类指标是长尾分布(实测 p95=31 而最大 1174, 取 p95 上限会把 5.2% 的格压成同一色) -> 用【对数】拉开低端
const LOG_METRICS={n_obs:1,n_match:1,n_tricky:1};
function colorT(v){
  const vmax=(capValue!=null?capValue:CAP)||1;
  if(LOG_METRICS[metric]) return Math.max(0,Math.min(1, Math.log(1+Math.max(0,v))/Math.log(1+vmax)));
  return Math.max(0,Math.min(1, v/vmax));
}
function autoCap(){
  const vals=[]; for(const k in AGG.cells){ const c=AGG.cells[k]; if(c.n>=minN) vals.push(valOf(c)); }
  if(!vals.length) return 1;
  vals.sort(function(a,b){return a-b;});
  // 计数类指标是长尾分布, 取 95 分位会把 5.2% 的格压成同一色 -> 取 99 分位(压顶降到 1%)。
  //   注意: 对数色标下这个"上限"是对数的底, 取大一点低端才拉得开(见 colorT)。
  const q=LOG_METRICS[metric] ? 0.99 : 0.95;
  const p=vals[Math.min(vals.length-1, Math.floor(vals.length*q))];
  return (p>0?p:1);
}
function cellsFor(){ const out=[]; for(const k in AGG.cells){ const c=AGG.cells[k]; if(c.n>=minN) out.push(c); } return out; }

// ---- 渲染 ----
function drawDot(p,color){
  ctx.beginPath(); ctx.arc(p[0],p[1],5,0,6.2832); ctx.fillStyle=color; ctx.fill();
  ctx.lineWidth=1.5; ctx.strokeStyle='#0d1117'; ctx.stroke();
}
function drawDots(){
  if(!AGG) return;
  // 什么时候画眼点图标:
  //   · 有选中格 -> **在 4 倍档及其以内都画**(owner 要求: 4x 就要看到假眼的准确位置), 超过 4 倍档(滚轮拉远)不画
  //   · 没选中格 -> 只有放大到 8 格以内才画(否则整个视野几千支会糊成一片)
  const vw = viewRect ? Math.abs(viewRect[1]-viewRect[0]) : null;
  const showSel = !!selKey && vw !== null && vw <= CS*25*1.02;      // CS*25 = 全图/4 = 4 倍档
  const showView = !selKey && vw !== null && vw <= ICON_MIN_ZOOM_W;
  if(!showSel && !showView){ DOTS=[]; dotTotal=0; dotCapped=false; return; }
  // 画谁: 选中了格子 -> 只画该格(与右侧明细的主格一致); 只用滚轮放大 -> 画视野内的
  const want=[];
  for(let n=0;n<AGG.list.length;n++){
    const idx=AGG.list[n], r=I[idx];
    const k=Math.floor((r[IX.x]+HALF)/CS)+','+Math.floor((r[IX.y]+HALF)/CS);
    if(selKey){ if(k===selKey) want.push(idx); }
    else {
      if(r[IX.x]>=viewRect[0]&&r[IX.x]<=viewRect[1]&&r[IX.y]>=viewRect[2]&&r[IX.y]<=viewRect[3]) want.push(idx);
    }
  }
  // 图标大小按缩放档给(4 倍档格子只有 ~41px, 用 12px 才能看出"位置"; 放大后逐步变大到 28px)
  const iconReady = !!(obsIcon && obsIcon.complete && obsIcon.naturalWidth>0);
  let s = vw <= CS*2 ? 28 : (vw <= CS*8 ? 20 : 12);
  let cap = selKey ? 1500 : 600;
  if(want.length > cap*1.5) s = Math.max(8, s*0.7);
  const nDraw=Math.min(want.length, cap);
  // 画序: 普通眼先画、刁钻/被反后画(重要的压在上面, 免得被盖住)
  const order = want.slice(0, nDraw).sort(function(a,b){
    const ra=I[a], rb=I[b];
    return (ra[IX.tricky]+ra[IX.dew]*2) - (rb[IX.tricky]+rb[IX.dew]*2);
  });
  DOTS=[]; dotTotal=want.length; dotCapped=(want.length>nDraw);
  for(let i=0;i<order.length;i++){
    const idx=order[i], r=I[idx], p=w2pView(r[IX.x],r[IX.y]);
    const isT=r[IX.tricky]===1, isD=r[IX.dew]===1;
    DOTS.push([p[0],p[1],idx,Math.floor((r[IX.x]+HALF)/CS)+','+Math.floor((r[IX.y]+HALF)/CS)]);  // [x,y,实例号,格键]
    if(iconReady){
      ctx.drawImage(obsIcon, p[0]-s/2, p[1]-s/2, s, s);     // ★ 官方假眼图标(Q5B 同款, 页面里早已内嵌 base64)
    } else {                                                 // 图标没加载出来时的兜底: 实心点
      ctx.beginPath(); ctx.arc(p[0],p[1], Math.max(2.5, s*0.28), 0, 6.2832);
      ctx.fillStyle = isT ? '#ffa657' : '#79c0ff'; ctx.fill();
    }
    // 颜色编码保留(图标本身是白色描边图): 橙=刁钻 / 蓝=普通 / 红=被反
    ctx.beginPath(); ctx.arc(p[0],p[1], s/2+1.5, 0, 6.2832);
    ctx.lineWidth = Math.max(1.6, s*0.16);
    ctx.strokeStyle = isD ? '#f85149' : (isT ? '#ffa657' : '#79c0ff');
    ctx.stroke();
    if(locked===idx){                              // 锁定的眼加白圈
      ctx.beginPath(); ctx.arc(p[0],p[1], s/2+5, 0, 6.2832);
      ctx.lineWidth=2.5; ctx.strokeStyle='#ffffff'; ctx.stroke();
    }
  }
}
// 点眼锁定: 显示 match_id + 坐标 + 双方队名 + 该眼全部字段(任务书 §6)
function eyeInfo(idx){
  const r=I[idx], mo=MORG[r[IX.mi]]||[-1,-1];
  const own=ORGS[mo[r[IX.team]===2?0:1]]||'?', foe=ORGS[mo[r[IX.team]===2?1:0]]||'?';
  return '<b>'+MIDS[r[IX.mi]]+'</b> · '+own+'('+(r[IX.team]===2?'天辉':'夜魇')+') vs '+foe+
    ' · 坐标 <b>('+r[IX.x]+', '+r[IX.y]+')</b> · 格('+Math.floor((r[IX.x]+HALF)/CS)+','+Math.floor((r[IX.y]+HALF)/CS)+')'+
    ' · 窗 '+WIN[r[IX.win]]+'<br>'+
    '放置 <b>'+mmss(r[IX.place])+'</b> → 销毁 <b>'+mmss(r[IX.destroy])+'</b> · 存活 <b>'+r[IX.surv]+'s</b>'+
    ' · '+(r[IX.dew]?(r[IX.cens]?'被反·记录在赛后':'是被反'):(r[IX.cens]?'存活到比赛结束(截断)':'到期'))+
    ' · 刁钻 <b>'+(r[IX.tricky]?'是':'否')+'</b>'+
    ' · 期间敌方真眼 达标(共存≥'+TR.min_overlap+'s) <b>'+r[IX.n_es]+'</b> 支 / 任意交集 <b>'+r[IX.n_es_any]+'</b> 支'+
    (r[IX.d_min]>=0?(' · 达标最近 '+r[IX.d_min]+' 单位'):'')+(r[IX.d_min_any]>=0?(' · 任意最近 '+r[IX.d_min_any]+' 单位'):'')+
    (r[IX.ovl_any]>0?(' · 任意最长共存 '+r[IX.ovl_any]+'s'):'');
}
function setLocked(idx){
  locked = (locked===idx ? null : idx);
  const el=document.getElementById('dotinfo');
  if(locked==null){ el.innerHTML=''; el.style.display='none'; }
  else { el.innerHTML='🔒 '+eyeInfo(locked)+'<br><span style="color:#8b949e">再点这支眼解锁; 点空格子仍然按格钻取。</span>';
         el.style.display='block'; }
  render();
}
function render(){
  if(!AGG) aggregate();
  ctx.clearRect(0,0,CSX,CSX);
  ctx.fillStyle='#0d1117'; ctx.fillRect(0,0,CSX,CSX);
  if(bgReady && bgOp>0){
    ctx.globalAlpha=bgOp/100;
    // ★ 底图必须跟随视野: 放大/平移时按【源裁剪】把视野对应的底图区域铺满画布。
    //   之前一直写 drawImage(bgimg,0,0,CSX,CSX) —— 底图永远是整张全图, 放大后跟热力块/眼点对不上(Q5B 用的是源裁剪)。
    if(viewRect){
      const p0=w2p(viewRect[0], viewRect[3]), p1=w2p(viewRect[1], viewRect[2]);
      ctx.drawImage(bgimg, p0[0], p0[1], p1[0]-p0[0], p1[1]-p0[1], 0, 0, CSX, CSX);
    } else {
      ctx.drawImage(bgimg, 0, 0, CSX, CSX);
    }
    ctx.globalAlpha=1;
  }
  cellsFor().forEach(function(c){
    const v=valOf(c), t=colorT(v);
    const col=interp(SEQ,t);
    const xc=(c.cx+0.5)*CS-HALF, yc=(c.cy+0.5)*CS-HALF;
    const p0=w2pView(xc-CS/2, yc+CS/2), p1=w2pView(xc+CS/2, yc-CS/2);
    ctx.fillStyle=rgb(col); ctx.globalAlpha=0.82;
    ctx.fillRect(p0[0],p0[1],(p1[0]-p0[0]),(p1[1]-p0[1])); ctx.globalAlpha=1;
    if(selKey===c.cx+','+c.cy){ ctx.lineWidth=2.5; ctx.strokeStyle='#ffffff'; ctx.strokeRect(p0[0],p0[1],(p1[0]-p0[0]),(p1[1]-p0[1])); }
  });
  drawDots();
  const mname={avg_surv:'平均存活',n_obs:'假眼出现次数',per_match:'每场出现次数',use_rate:'出现率',
               n_match:'出场场次',dew_rate:'被反率',tricky_rate:'刁钻率',n_tricky:'刁钻数'}[metric];
  document.getElementById('curdesc').textContent =
    '战队:'+(orgSet.length?(orgSet.length+' 支已选'):'全部')+(sideSel>0?(' · '+(sideSel===2?'天辉':'夜魇')):'')+
    ' · '+(wsel<0?'全部窗':WIN[wsel])+(trickyOnly?' · 只看刁钻':'')+' · '+mname+
    ' · 上限'+(capValue!=null?capValue.toFixed(1):('自动'+CAP.toFixed(1)))+
    (LOG_METRICS[metric]?'(对数色标)':'(线性色标)')+
    ' · 分母场次'+AGG.matchN+' · 下限'+minN+
    (dotTotal?(' · 图上标注 +'+(selKey?('该格 '+dotTotal+' 支'):('视野内 '+dotTotal+' 支'))
               +(dotCapped?'(过密, 仅画前 '+Math.min(dotTotal, selKey?1500:600)+' 支)':'')):'');
}

// ---- 交互 ----
// 平移 = **按住鼠标中键拖动**(owner 要求)。但很多鼠标/触控板没有中键(实测 owner 那边按不出来),
//   所以同时支持 **右键拖动** 与 **Shift + 左键拖动**; 左键单击(无修饰键)只用来点格/点眼。
let dragging=false, lastX=0, lastY=0, shiftDown=false, dragEndedAt=0;
function dragButton(e){ return e.button===1 || e.button===2 || (e.button===0 && (e.shiftKey || shiftDown)); }
cv.addEventListener('contextmenu', function(e){ if(e.preventDefault) e.preventDefault(); });   // 右键拿来拖动, 不弹菜单
window.addEventListener('keydown', function(e){ if(e.key==='Shift') shiftDown=true; });
window.addEventListener('keyup', function(e){ if(e.key==='Shift') shiftDown=false; });
cv.addEventListener('mousedown', function(e){
  if(!dragButton(e)){ return; }                  // 只有 中键 / 右键 / Shift+左键 开始拖动
  dragging=true; lastX=e.clientX; lastY=e.clientY;
  cv.style.cursor='grabbing';
  if(e.preventDefault) e.preventDefault();       // 压掉浏览器中键自动滚动
});
window.addEventListener('mouseup', function(){ if(dragging){ dragEndedAt=Date.now(); } dragging=false; cv.style.cursor='grab'; });
// 中键/右键还会触发 auxclick/click: 屏蔽掉, 免得被当成"点格"
cv.addEventListener('auxclick', function(e){ if((e.button===1||e.button===2) && e.preventDefault) e.preventDefault(); });
cv.addEventListener('mousemove', function(e){
  const rect=cv.getBoundingClientRect(), sx=CSX/rect.width;
  const mx=(e.clientX-rect.left)*sx, my=(e.clientY-rect.top)*sx;   // px 在 if 外, 避免作用域坑
  if(dragging){
    const a=px2world(0,0), b=px2world(mx,my);
    const wdx=a[0]-b[0], wdy=a[1]-b[1];
    // 全图时没有可平移的余地: 先按当前指针位置放大到 4 格视野, 再平移(否则"全图下右键拖动"会毫无反应)
    if(!viewRect){
      const nw=CS*4, ccx=b[0], ccy=b[1];
      viewRect=clampView([ccx-nw/2, ccx+nw/2, ccy-nw/2, ccy+nw/2]);
      lastX=e.clientX; lastY=e.clientY; render(); return;
    } else {
      viewRect=clampView([viewRect[0]+wdx, viewRect[1]+wdx, viewRect[2]+wdy, viewRect[3]+wdy]); render();
    }
    lastX=e.clientX; lastY=e.clientY;
    return;
  }
  // hover: 换算到世界坐标 -> 格
  const w=px2world(mx,my);
  const cx=Math.floor((w[0]+HALF)/CS), cy=Math.floor((w[1]+HALF)/CS), k=cx+','+cy;
  if(k!==hoverKey){ hoverKey=k; showTip(e, k, cx, cy); }
  else if(document.getElementById('tip').style.display==='block'){ moveTip(e); }
});
cv.addEventListener('mouseleave', function(){ document.getElementById('tip').style.display='none'; hoverKey=null; });
cv.addEventListener('wheel', function(e){
  e.preventDefault();
  // ★ 拖动中 / 中键或右键**正被按住**时不吃滚轮: 很多鼠标按住滚轮(中键)时设备仍在发 wheel 事件,
  //   那些事件会把拖动顺带变成缩放(owner 反馈"中键按下去还带着缩放, 拖动效果很差")。
  //   用 e.buttons 位掩码判断(2=右键, 4=中键) —— 比"拖动结束后 N 毫秒内忽略"确定, 也不会有时间魔法。
  if(dragging || ((e.buttons|0) & 6)){ return; }
  const rect=cv.getBoundingClientRect(), sx=CSX/rect.width;
  const mx=(e.clientX-rect.left)*sx, my=(e.clientY-rect.top)*sx;
  const cur = viewRect ? viewRect.slice() : [-HALF,HALF,-HALF,HALF];
  const w0=px2world(mx,my);
  const f = e.deltaY<0 ? 0.7 : 1/0.7;
  const nw=(cur[1]-cur[0])*f;
  // 缩到全图之外 -> 复位; 否则钳制(最小半格)。★ 不能用"return 拦截": 单格视野(172)乘 1.43 仍 < 258,
  //   旧写法会把缩小动作整个挡掉, 用户卡在单格视图里出不来(实测抓到)。
  if(nw >= 2*HALF*1.02){ viewRect=null; selKey=null; render(); return; }
  const cx0=(cur[0]+cur[1])/2, cy0=(cur[2]+cur[3])/2;
  const fx=(w0[0]-cur[0])/(cur[1]-cur[0]), fy=(w0[1]-cur[2])/(cur[3]-cur[2]);
  const nx0=w0[0]-fx*nw, ny0=w0[1]-fy*nw;
  viewRect=clampView([nx0, nx0+nw, ny0, ny0+nw]);
  render();
}, {passive:false});
cv.addEventListener('click', function(e){
  if(e.button!==undefined && e.button!==0){ return; }   // 只认左键
  const rect=cv.getBoundingClientRect(), sx=CSX/rect.width;
  const mx=(e.clientX-rect.left)*sx, my=(e.clientY-rect.top)*sx;
  // 【点眼锁定】只在"已选中的格"内生效(半径 10px) —— 否则会把"点格钻取"这个主操作抢掉
  if(selKey){
    let hit=null, hd=1e9;
    for(let i=0;i<DOTS.length;i++){
      if(DOTS[i][3]!==selKey) continue;             // 只认当前选中格里的眼点
      const dd=Math.hypot(DOTS[i][0]-mx, DOTS[i][1]-my);
      if(dd<=10 && dd<hd){ hit=DOTS[i][2]; hd=dd; }
    }
    if(hit!=null){ setLocked(hit); return; }
  }
  const w=px2world(mx,my);
  const cx=Math.floor((w[0]+HALF)/CS), cy=Math.floor((w[1]+HALF)/CS), k=cx+','+cy;
  // 点【已经是当前选中的格】-> 不动(同格重复点不再放大)
  if(selKey===k){ renderDrill(k); render(); return; }
  selKey=k;
  // 点格只有一个动作: 把视野切到【全图 4 倍】这档(4300 单位)并以该格为中心。
  //   · 比 4 倍更远(全图/更浅) -> 收到 4 倍
  //   · 已经在 4 倍或更近(滚轮缩进来的) -> **不再继续放大**, 只把视野重新居中到该格
  //   其余缩放档位全部交给滚轮(owner 口径: 点格只有"4x / 非 4x"两档)。
  const W4 = 2*HALF/CLICK_ZOOM;
  const curW = viewRect ? Math.abs(viewRect[1]-viewRect[0]) : 2*HALF;
  const nw = Math.max(MINW_CLICK, Math.min(curW, W4));
  const ccx=(cx+0.5)*CS-HALF, ccy=(cy+0.5)*CS-HALF;         // 格中心(世界坐标)
  viewRect=clampView([ccx-nw/2, ccx+nw/2, ccy-nw/2, ccy+nw/2]);
  renderDrill(k); render();
});
function resetView(){ viewRect=null; selKey=null; setLocked(null); renderDrill(null); render(); }
function showTip(e,k,cx,cy){
  const c=AGG.cells[k]; const tip=document.getElementById('tip');
  if(!c){ tip.style.display='none'; return; }
  const nMatch=Object.keys(c.mis).length, denom=Math.max(1,AGG.matchN);
  tip.innerHTML='格('+cx+','+cy+') 假眼 <b>'+c.n+'</b> 支 · 出场 <b>'+nMatch+'</b> 场/'+denom+
    ' · 每场 <b>'+(c.n/denom).toFixed(3)+'</b> 次 · 出现率 <b>'+(100*nMatch/denom).toFixed(1)+'%</b>'+
    ' · 平均存活 <b>'+(c.ss/c.n).toFixed(1)+'s</b>'+
    ' · 被反 <b>'+c.sd+'</b> ('+(100*c.sd/c.n).toFixed(0)+'%) · 刁钻 <b>'+c.st+'</b> ('+(100*c.st/c.n).toFixed(0)+'%)'+
    (c.sc?' · 其中截断 <b>'+c.sc+'</b>':'');
  tip.style.display='block'; moveTip(e);
}
function moveTip(e){
  const wrap=document.querySelector('.left').getBoundingClientRect();
  const tip=document.getElementById('tip');
  tip.style.left=(e.clientX-wrap.left+14)+'px'; tip.style.top=(e.clientY-wrap.top+10)+'px';
}
function renderDrill(k){
  const tb=document.querySelector('#drill tbody'); tb.innerHTML='';
  const sum=document.getElementById('sum');
  if(!k){ sum.textContent='点一个格子看该格假眼明细(match_id / 时刻 / 存活 / 被反 / 刁钻 / 期间敌方真眼)。'; return; }
  const cx=parseInt(k.split(',')[0],10), cy=parseInt(k.split(',')[1],10);
  // 明细范围 = 中心格 + 周围 8 格(owner 要求"显示周围 8 格假眼位置的详细情况")
  const rows=[];                       // 存【实例号】而不是行引用, 这样每行都能点开锁定
  const inBlock={}, blockKey=[];
  for(let dx=-1;dx<=1;dx++) for(let dy=-1;dy<=1;dy++){ const kk=(cx+dx)+','+(cy+dy); inBlock[kk]=1; }
  if(AGG) for(let n=0;n<AGG.list.length;n++){
    const idx=AGG.list[n], r=I[idx];
    const kk=Math.floor((r[IX.x]+HALF)/CS)+','+Math.floor((r[IX.y]+HALF)/CS);
    if(inBlock[kk]) rows.push(idx);
  }
  // 排序: 中心格优先, 其余按"格到中心的距离"再按放置时刻
  function dOf(idx){ const r=I[idx]; return Math.max(Math.abs(Math.floor((r[IX.x]+HALF)/CS)-cx), Math.abs(Math.floor((r[IX.y]+HALF)/CS)-cy)); }
  rows.sort(function(a,b){ const da=dOf(a), db=dOf(b); if(da!==db) return da-db; return I[a][IX.place]-I[b][IX.place]; });
  let nT=0,nD=0,nC=0, nCenter=0; const mis={};
  rows.forEach(function(idx){ const r=I[idx]; if(r[IX.tricky])nT++; if(r[IX.dew])nD++; if(r[IX.cens])nC++; mis[r[IX.mi]]=1; if(dOf(idx)===0) nCenter++; });
  const nMatch=Object.keys(mis).length;
  let survSum=0; rows.forEach(function(idx){ survSum+=I[idx][IX.surv]; });
  sum.innerHTML='格 <b>('+cx+','+cy+')</b> 及其<b>周围 8 格</b> · '+(wsel<0?'全部窗':WIN[wsel])+(sideSel>0?(' · '+(sideSel===2?'天辉':'夜魇')):'')+(trickyOnly?' · 只看刁钻':'')+(censOut?' · 已剔除截断眼':'')+' · 战队 '+(orgSet.length?(orgSet.length+' 支已选'):'全部')+
    ' · 假眼 <b>'+rows.length+'</b> 支(中心格 <b>'+nCenter+'</b> 支) · 出场 <b>'+nMatch+'</b> 场/'+Math.max(1,AGG?AGG.matchN:1)+
    ' · 每场 <b>'+(rows.length/Math.max(1,AGG?AGG.matchN:1)).toFixed(3)+'</b> · 出现率 <b>'+(100*nMatch/Math.max(1,AGG?AGG.matchN:1)).toFixed(1)+'%</b>'+
    ' · 刁钻 <b>'+nT+'</b> · 被反 <b>'+nD+'</b>'+(nC?' · 截断 <b>'+nC+'</b>':'')+
    (rows.length?(' · 平均存活 <b>'+(survSum/rows.length).toFixed(1)+'s</b>'):'')+
    '<br><span style="color:#8b949e">「格」列里 <b>C</b>=你点的中心格, <b>N</b>=周围 8 格; 点任一行 = 在地图上锁定该眼(match_id/坐标/双方队名)</span>';
  if(!rows.length){ tb.innerHTML='<tr><td colspan="12" class="c">该格及其周围 8 格在当前筛选下没有假眼</td></tr>'; return; }
  const ROWCAP=4000;                       // 周围 8 格(3x3)在大格上可能上千行, 用一个明确上限保UI流畅
  const shown=Math.min(rows.length, ROWCAP);
  if(rows.length>ROWCAP){
    const note=document.createElement('tr');
    note.innerHTML='<td colspan="12" class="c" style="color:#d29922">共 '+rows.length+' 行, 这里只列出前 '+ROWCAP
      +' 行(优先中心格)。请用"位置出现次数下限 / 时间窗 / 战队"缩小范围, 或放大后用滚轮继续细看。</td>';
    tb.appendChild(note);
  }
  rows.slice(0, shown).forEach(function(idx){
    const tr=document.createElement('tr'), r=I[idx];
    const org=ORGS[MORG[r[IX.mi]][r[IX.team]===2?0:1]]||'?';
    const gx=Math.floor((r[IX.x]+HALF)/CS), gy=Math.floor((r[IX.y]+HALF)/CS), d=dOf(idx);
    const tag = d===0 ? '<b style="color:#79c0ff">C</b>' : '<span style="color:#8b949e">N</span>';
    tr.innerHTML='<td class="c">'+MIDS[r[IX.mi]]+'</td><td class="c">'+tag+' ('+gx+','+gy+')</td><td>'+org+'</td><td class="c">'+(r[IX.team]===2?'天辉':'夜魇')+'</td>'+
      '<td>'+mmss(r[IX.place])+'</td><td>'+mmss(r[IX.destroy])+'</td><td>'+r[IX.surv]+'s</td>'+
      '<td class="c" style="color:'+(r[IX.cens]?'#d29922':(r[IX.dew]?'#f85149':'#8b949e'))+'">'+
        (r[IX.dew]?(r[IX.cens]?'被反·记录在赛后':'是被反'):(r[IX.cens]?'存活到比赛结束(截断)':'到期'))+'</td>'+
      '<td class="c" style="color:'+(r[IX.tricky]?'#ffa657':'#8b949e')+'">'+(r[IX.tricky]?'刁钻':'—')+'</td>'+
      '<td>'+r[IX.n_es]+' 支<span style="color:#8b949e">/'+r[IX.n_es_any]+'</span></td>'+
      '<td>'+(r[IX.d_min]>=0?r[IX.d_min]+'':'—')+'<span style="color:#8b949e">/'+(r[IX.d_min_any]>=0?r[IX.d_min_any]:'—')+'</span></td>'+
      '<td>'+(r[IX.ovl_any]>0?r[IX.ovl_any]+'s':'—')+'</td>';
    tr.style.cursor='pointer';
    tr.onclick=(function(i){ return function(){ setLocked(i); }; })(idx);
    tb.appendChild(tr);
  });
}

// ---- 控件 ----
function setOrg(){ aggregate(); resetCap(); render(); renderDrill(selKey); }
// 战队多选: 一个都不勾 = 全部; 全选/清空两个按钮
function toggleOrg(i, on){ const k=orgSet.indexOf(i);
  if(on && k<0) orgSet.push(i);
  if(!on && k>=0) orgSet.splice(k,1);
  orgSet.sort(function(a,b){return a-b;});
  setOrg(); }
function setAllOrg(on){ orgSet = on ? D.orgs.map(function(_,i){return i;}) : [];
  const bs=document.querySelectorAll('.orgck');
  for(let i=0;i<bs.length;i++){ bs[i].checked = on; }
  setOrg(); }
function setWin(w){ wsel=w;
  document.querySelectorAll('.wbtn').forEach(function(b){ b.classList.toggle('on', parseInt(b.dataset.w,10)===w); });
  aggregate(); resetCap(); render(); renderDrill(selKey); }
function setTricky(){ trickyOnly=document.getElementById('tk').checked; aggregate(); resetCap(); render(); renderDrill(selKey); }
function setCens(){ censOut=document.getElementById('cx').checked; aggregate(); resetCap(); render(); renderDrill(selKey); }
// 阵营 toggle: 全部 / 天辉(2) / 夜魇(3)
function setSide(s){ sideSel=s;
  document.querySelectorAll('.sbtn').forEach(function(b){ b.classList.toggle('on', parseInt(b.dataset.s,10)===s); });
  aggregate(); resetCap(); render(); renderDrill(selKey); }
function setMetric(m){ metric=m;
  document.querySelectorAll('.vbtn').forEach(function(b){ b.classList.toggle('on', b.dataset.m===m); });
  resetCap(); render(); }
function setBg(){ bgOp=parseFloat(document.getElementById('mop').value); document.getElementById('moppct').textContent=bgOp+'%'; render(); }
function setCap(){ const v=parseFloat(document.getElementById('sl').value); capValue=(v>0?v:null);
  document.getElementById('cap').textContent=(capValue!=null?capValue.toFixed(1):'自动'); render(); }
function setN(){ minN=parseInt(document.getElementById('ns').value,10); document.getElementById('nsv').textContent=minN; render(); }
function resetCap(){ capValue=null; CAP=autoCap(); const sl=document.getElementById('sl');
  sl.max=Math.max(2, Math.ceil(CAP*2)); sl.value=0; document.getElementById('cap').textContent='自动'; }

// ---- init ----
aggregate(); CAP=autoCap();
setWin(0); setMetric('avg_surv');
document.getElementById('tk').checked=false;
document.getElementById('cx').checked=false;
document.getElementById('ns').max=Math.min(60, Math.max(5, Math.round(AGG.list.length/40)));
renderDrill(null);
render();
</script></body></html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
