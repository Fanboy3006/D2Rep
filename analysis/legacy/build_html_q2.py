#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_html_q2.py - Q2 家庭友好 HTML 复核页（5 队 10min/10→20 经济→胜率）。

每行一个(队伍×状态桶)的胜率 + 该队逐场明细。玩家友好列名 + 长图例 + 列组开关 + 绿/红。

来源: stats.db (matches+gold_adv, team 名) + owner xlsx 人工标签。
输出: analysis/output_review/q2_viewer.html
"""
import collections
import csv
import os
import sqlite3
import sys
import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATS = os.path.join(ROOT, "stats.db")
OUTDIR = os.path.join(ROOT, "analysis", "output_review")
os.makedirs(OUTDIR, exist_ok=True)
HTML = os.path.join(OUTDIR, "q2_viewer.html")
THR = 1000
TARGET = {"Xtreme Gaming": {8261500}, "Team Spirit": {7119388}, "Vici Gaming": {726228},
          "Team Yandex": {9823272}, "Team Vision (PVISION)": {9572001, 9824702}}
# owner xlsx (only for those three; removed from display unless TARGET includes abbrev)
XLSX = {"XG统计.xlsx": "XG", "VG统计.xlsx": "VG", "TS统计.xlsx": "TS"}
TEAM_COLOR = {"Xtreme Gaming": "#f85149", "Team Spirit": "#3fb950", "Vici Gaming": "#d29922",
              "Team Yandex": "#79c0ff", "Team Vision (PVISION)": "#bc8cff"}


def cell(v):
    return "" if v is None else str(v)


def main():
    con = sqlite3.connect(STATS)
    con.row_factory = sqlite3.Row

    # per-team rows: (match_id, 10min, 20min, delta, result, rule_s10, rule_s20, owner_s10, owner_s20)
    per_team = collections.defaultdict(list)
    for m in con.execute("SELECT match_id,radiant_team_id,dire_team_id,radiant_win FROM matches"):
        mid = m["match_id"]
        g10 = con.execute("SELECT value FROM gold_adv WHERE match_id=? AND minute=10", (mid,)).fetchone()
        g20 = con.execute("SELECT value FROM gold_adv WHERE match_id=? AND minute=20", (mid,)).fetchone()
        if not g10 or not g20:
            continue
        for side, tid, win in (("R", m["radiant_team_id"], m["radiant_win"]),
                               ("D", m["dire_team_id"], m["radiant_win"])):
            label = next((k for k, v in TARGET.items() if tid in v), None)
            if label is None:
                continue
            if side == "R":
                t10, t20, res = g10["value"], g20["value"], win
            else:
                t10, t20 = -g10["value"], -g20["value"]
                res = 1 - win if win is not None else None
            dw = t20 - t10
            rs10 = "lead" if t10 > THR else ("trail" if t10 < -THR else "even")
            rs20 = "up" if dw > THR else ("down" if dw < -THR else "flat")
            per_team[label].append({"mid": mid, "t10": t10, "t20": t20, "dw": dw,
                                    "res": ("W" if res == 1 else ("L" if res == 0 else "?")),
                                    "s10": rs10, "s20": rs20})

    # owner labels (for TS/VG/XG comparison)
    owner = {}
    for xlsx, subj in XLSX.items():
        wb = openpyxl.load_workbook(os.path.join(ROOT, xlsx), data_only=True)
        ws = wb.worksheets[0]
        for r in ws.iter_rows(values_only=True):
            if len(r) >= 10 and isinstance(r[1], (int, float)):
                owner[(subj, int(r[1]))] = (str(r[9]).upper() if r[9] else "", str(r[10]).upper() if r[10] else "")

    # per-team summary: win rate by 10min-state and 10->20 direction
    states10 = ["lead", "even", "trail"]
    states20 = ["up", "flat", "down"]
    # average (for coloring)
    all_wr = lambda key: []
    def agg(team, key):
        d = collections.defaultdict(list)
        for r in per_team[team]:
            st = r["s10"] if key == "s10" else r["s20"]
            d[st].append(1 if r["res"] == "W" else 0)
        return d

    # compute per-team rows for HTML
    rows_html = []
    for team in sorted(TARGET):
        recs = per_team[team]
        n = len(recs)
        wins = sum(1 for r in recs if r["res"] == "W")
        # win rate per state
        cells = ['<td class="g-team" style="color:%s">%s</td>' % (TEAM_COLOR[team], team)]
        # matches, wins
        cells.append('<td class="g-n">%d</td>' % n)
        cells.append('<td class="g-win">%d</td>' % wins)
        if n:
            cells.append('<td class="g-wr %s">%.0f%%</td>' % ("pos" if wins / n >= 0.5 else "neg", 100 * wins / n))
        else:
            cells.append('<td class="g-wr"></td>')
        # win-rate by 10min state
        ag = agg(team, "s10")
        for st in states10:
            lst = ag.get(st, [])
            if lst:
                wr = sum(lst) / len(lst)
                cls = "pos" if wr >= 0.5 else "neg"
                cells.append('<td class="g-s10 %s">%.0f%%<small>(%d)</small></td>' % (cls, 100 * wr, len(lst)))
            else:
                cells.append('<td class="g-s10">—</td>')
        # win-rate by 10->20 direction
        ag2 = agg(team, "s20")
        for st in states20:
            lst = ag2.get(st, [])
            if lst:
                wr = sum(lst) / len(lst)
                cls = "pos" if wr >= 0.5 else "neg"
                cells.append('<td class="g-s20 %s">%.0f%%<small>(%d)</small></td>' % (cls, 100 * wr, len(lst)))
            else:
                cells.append('<td class="g-s20">—</td>')
        rows_html.append("<tr>" + "".join(cells) + "</tr>")

    # per-match detail (all teams)
    det_rows = []
    for team in sorted(TARGET):
        for r in per_team[team]:
            ol = owner.get((team, r["mid"]), ("", ""))
            det_rows.append([team, r["mid"], cell(r["t10"]), cell(r["t20"]), cell(r["dw"]),
                             r["res"], r["s10"], r["s20"], ol[0] or "—", ol[1] or "—"])
    det_html = "".join("<tr>" + "".join("<td>%s</td>" % c for c in h) + "</tr>" for h in det_rows)

    thead = ("<tr><th class='g-team'>队伍</th><th class='g-n'>场次</th><th class='g-win'>胜</th>"
             "<th class='g-wr'>胜率</th>"
             "<th class='g-s10'>10min线优率</th><th class='g-s10'>线平率</th><th class='g-s10'>线劣率</th>"
             "<th class='g-s20'>10→20转优胜率</th><th class='g-s20'>持平率</th><th class='g-s20'>转劣胜率</th></tr>")

    toggles = ("<b>列显示开关：</b>"
               "<label class='tg'><input checked onchange=\"tg('g-n')\">场次</label> "
               "<label class='tg'><input checked onchange=\"tg('g-win')\">胜</label> "
               "<label class='tg'><input checked onchange=\"tg('g-wr')\">胜率</label> "
               "<label class='tg'><input checked onchange=\"tg('g-s10')\">10min(线优/平/劣)</label> "
               "<label class='tg'><input checked onchange=\"tg('g-s20')\">10→20(转优/平/劣)</label>")

    legend = """
<div class="legend"><h3>怎么看这张表（给普通玩家）</h3>
<p>这是 5 支队伍（XG/VG/TY/PV/TS）：<b>开局第 10 分钟经济(钱+装备)领先或落后，对最终胜负的影响有多大</b>。</p>
<ul>
<li><b>队伍</b>：五队之一（颜色区分）。</li>
<li><b>场次/胜/胜率</b>：这队打了多少场、赢几场、胜率。</li>
<li><b>10min 线优率/线平率/线劣率</b>：按<b>开局10分钟经济差</b>分档（线优=领先&gt;1000，线劣=落后&gt;1000，线平=中间）时的<b>胜率</b>。<span class="pos">绿色</span>=这队在这种开局下赢面大，<span class="neg">红色</span>=赢面小。</li>
<li><b>10→20 转优/持平/转劣胜率</b>：看<b>10到20分钟经济是滚大(转优)还是被拉开(转劣)</b>，对应胜率。这是"滚雪球"的关键。</li>
</ul>
<p>下端是<b>逐场明细</b>（每队每局：10/20/Δ/结果），可逐局核对。样本少的小篮=方向性。</p></div>"""

    det_head = ("<tr><th>队伍</th><th>比赛ID</th><th>10min</th><th>20min</th><th>Δ</th>"
                "<th>结果</th><th>规则10min</th><th>规则10→20</th><th>owner对线</th><th>owner中期</th></tr>")
    html = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>DOTA Q2 复核页</title>
<style>
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#e6edf3;margin:24px}
input#f{width:280px;padding:8px;border:1px solid #30363d;border-radius:6px;background:#161b22;color:#e6edf3}
table{border-collapse:collapse;width:100%;font-size:13px;background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden}
th,td{padding:7px 9px;border-bottom:1px solid #21262d;text-align:right;white-space:nowrap}
th{background:#21262d;cursor:pointer;position:sticky;top:0}
th:first-child,td:first-child{text-align:left}
tr:hover{background:#1f6feb22}
.legend{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;margin:14px 0;font-size:13px;line-height:1.7}
.legend h3{margin:0 0 8px}
.legend .pos{color:#3fb950;font-weight:700}.legend .neg{color:#f85149;font-weight:700}
td.pos{color:#3fb950;font-weight:700}td.neg{color:#f85149;font-weight:700}
.tg{background:#21262d;border:1px solid #30363d;border-radius:14px;padding:3px 9px;cursor:pointer;font-size:12px}
.note{color:#8b949e;font-size:12px;margin:6px 0}
</style></head><body>
<h1>Q2：五队 10 分钟经济领先/落后 → 胜率 &amp; 10→20 滚雪球</h1>
<div class="note">点表头排序 · 搜索 · 列开关 · 悬停看说明</div>
@@LEGEND@@
<div class="wrap">@@TOGGLES@@ <input id="f" placeholder="筛选队伍或比赛ID…" oninput="flt()"></div>
<table id="t"><thead>@@THEAD@@</thead><tbody>@@ROWS@@</tbody></table>
<h2 style="font-size:15px">逐场明细（可逐局核对）</h2>
<table id="det"><thead>@@DETHEAD@@</thead><tbody>@@DETROWS@@</tbody></table>
<script>
function flt(){var q=document.getElementById('f').value.toLowerCase();
 document.querySelectorAll('#t tbody tr,#det tbody tr').forEach(function(tr){tr.style.display=tr.textContent.toLowerCase().indexOf(q)>=0?'':'none';});}
function tg(g){var on=event.target.checked;document.querySelectorAll('#t .'+g).forEach(function(c){c.style.display=on?'':'none';})}
// generic sort: click any th in any table
var lastTb=null,lastCol=-1,dir=1;
document.querySelectorAll('table').forEach(function(tb){
  tb.querySelectorAll('th').forEach(function(th,i){
    if(th.style.display==='none'){return;}
    th.innerHTML+=' <span class="arr"></span>';
    th.style.cursor='pointer';
    th.onclick=function(){
      var rows=Array.from(tb.tBodies[0].rows);
      if(lastTb===tb&&lastCol===i){dir=-dir;}else{dir=1;lastTb=tb;lastCol=i;}
      rows.sort(function(a,b){var x=a.cells[i].innerText,y=b.cells[i].innerText;
        var xv=isNaN(parseFloat(x.replace(/[^0-9.-]/g,'')))||x===''?x:parseFloat(x.replace(/[^0-9.-]/g,''));
        var yv=isNaN(parseFloat(y.replace(/[^0-9.-]/g,'')))||y===''?y:parseFloat(y.replace(/[^0-9.-]/g,''));
        return (xv>yv?1:xv<yv?-1:0)*dir;});
      rows.forEach(function(r){tb.tBodies[0].appendChild(r);});
      tb.querySelectorAll('th .arr').forEach(function(a){a.textContent='';});
      th.querySelector('.arr').textContent=dir>0?'▲':'▼';
    };
  });
});
</script></body></html>"""

    html = (html.replace("@@THEAD@@", thead).replace("@@ROWS@@", "".join(rows_html))
                .replace("@@DETHEAD@@", det_head).replace("@@DETROWS@@", det_html)
                .replace("@@LEGEND@@", legend).replace("@@TOGGLES@@", toggles))
    with open(HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", HTML, len(html))
    print("teams:", {t: len(per_team[t]) for t in sorted(TARGET)})


if __name__ == "__main__":
    sys.exit(main())
