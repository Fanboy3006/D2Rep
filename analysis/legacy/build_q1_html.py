#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_q1_html.py - Q1 打野经济 复核页（HTML，内联 SVG 热区 + B2 逐分钟 gold_adv）。

读取 jungle_economy_proxy.py 的产物:
  output_q1/q1_neutral_activity_heat_250.csv  (B1 热区: window,team,cell_x,cell_y,x_center,y_center,count)
  output_q1/q1_gold_adv_minute.csv            (B2 逐分钟: minute,radiant,dire,gold_adv_radiant)

输出: analysis/output_review/q1_jungle_viewer.html
"""
import csv
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "analysis")
Q1 = os.path.join(OUT, "output_q1")
HTML = os.path.join(OUT, "output_review", "q1_jungle_viewer.html")

MAP_HALF = 10000.0
CELL = 250
N = int(round(2 * MAP_HALF / CELL))   # 80 cells/side
WINDOWS = ["0-10", "10-20", "20+", "whole"]


def load_grid():
    """(window, team) -> {(cx,cy): count}"""
    grid = {}
    with open(os.path.join(Q1, "q1_neutral_activity_heat_%d.csv" % CELL), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            grid.setdefault((r["window"], r["team"]), {})[(int(r["cell_x"]), int(r["cell_y"]))] = int(r["count"])
    return grid


def load_gold():
    rows = []
    with open(os.path.join(Q1, "q1_gold_adv_minute.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append((int(r["minute"]), int(r["radiant"]), int(r["dire"]), int(r["gold_adv_radiant"])))
    return rows


def svg_heatmap(grid_rad, grid_dire, scale, title):
    """One SVG: radiant cells green (intensity), dire cells red (intensity). North up."""
    w = N * scale
    gmax = 1
    for g in (grid_rad, grid_dire):
        for v in g.values():
            gmax = max(gmax, v)
    parts = ['<svg width="%d" height="%d" viewBox="0 0 %d %d" style="background:#0d1117;border:1px solid #30363d;border-radius:6px">'
             % (w, w, w, w)]
    for cy in range(N):
        for cx in range(N):
            v = 0; col = None
            if (cx, cy) in grid_rad:
                v = grid_rad[(cx, cy)]; col = "radiant"
            elif (cx, cy) in grid_dire:
                v = grid_dire[(cx, cy)]; col = "dire"
            if col is None or v == 0:
                continue
            alpha = 0.20 + 0.80 * (v / gmax)
            fill = "34,197,94" if col == "radiant" else "248,81,73"   # green / red
            x = cx * scale
            y = (N - cy) * scale      # north up
            parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                         'fill="rgba(%s,%.2f)" title="%s: %d"/>' % (x, y, scale, scale, fill, alpha, col, v))
    parts.append('</svg>')
    return "".join(parts)


def main():
    grid = load_grid()
    gold = load_gold()
    scale = 7
    # per window: combined svg + team counts
    html_sections = []
    for w in WINDOWS:
        gr = grid.get((w, "radiant"), {})
        gd = grid.get((w, "dire"), {})
        rc = sum(gr.values()); dc = sum(gd.values())
        svg = svg_heatmap(gr, gd, scale, w)
        html_sections.append(
            '<div class="win"><h2>窗口 %s <small>(game-clock)</small></h2>'
            '<div class="cnt">天辉野区击杀 <b>%d</b> · 夜魇野区击杀 <b>%d</b> · 合计 <b>%d</b></div>'
            '<div class="svgwrap">%s</div>'
            '<div class="note">绿=天辉野区 红=夜魇野区,格深=该格被清次数越多。归属按"野怪死亡所在象限"近似。</div></div>'
            % (w, rc, dc, rc + dc, svg))

    # B2 table
    tbl = []
    for m, r, d, adv in gold:
        if m < 0:
            continue
        cls = "pos" if adv > 0 else ("neg" if adv < 0 else "")
        tbl.append('<tr><td class="g-base">%d</td><td class="g-base">%s</td><td class="g-base">%s</td>'
                   '<td class="g-adv %s">%s</td></tr>'
                   % (m, f"{r:,}", f"{d:,}", cls, ("%+d" % adv)))
    gold_adv = '\n'.join(tbl)

    thead = ("<tr><th class='g-base'>分钟</th><th class='g-base'>天辉金币</th>"
             "<th class='g-base'>夜魇金币</th><th class='g-adv'>经济差(天辉-夜魇)</th></tr>")

    print("heat rows windows/teams:", [(k, len(v)) for k, v in sorted(grid.items())])
    print("gold rows:", len(gold))

    html = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>Q1 打野经济</title>
<style>
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#e6edf3;margin:24px}
h1{font-size:20px}h2{font-size:16px;margin:16px 0 4px}
.legend{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;margin:14px 0;font-size:13px;line-height:1.7}
.legend h3{margin:0 0 8px}.legend b{color:#79c0ff}
.win{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;margin:12px 0}
.cnt{margin:4px 0 8px;font-size:13px}.svgwrap{overflow:auto}
.note{color:#8b949e;font-size:12px;margin-top:6px}
table{border-collapse:collapse;font-size:13px;background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-top:8px}
th,td{padding:6px 10px;border-bottom:1px solid #21262d;text-align:right;white-space:nowrap}
th{background:#21262d}td.pos{color:#3fb950;font-weight:700}td.neg{color:#f85149;font-weight:700}
</style></head><body>
<h1>Q1 打野经济：刷哪片野、何时刷（野区清怪热区 + 宏观 gold_adv）</h1>
<div class="legend"><h3>怎么看</h3>
<p>数据来自 <b>970 场公开比赛</b>的本地解析(<code>neutral_kill</code> 事件 + <code>gold</code> 收入事件)。</p>
<ul>
<li><b>热区图</b>=野怪被击杀的位置按窗口(<b>0-10 / 10-20 / 20+ / 整场</b>,按比赛游戏钟对齐)统计。<b>绿=天辉野区,红=夜魇野区</b>,格越深=该处被清次数越多。</li>
<li><b>宏观 gold_adv</b>=把每场金币收入按分钟<strong>求和</strong>(非场均),天辉-夜魇的经济差;仅为"宏观对照",不归属到某个具体野点。</li>
<li><b>归属说明</b>:野怪无队伍,当前按"死亡所在象限"近似——x&lt;0∧y&lt;0 记天辉, x&gt;0∧y&gt;0 记夜魇,其余记 mid(不归边)。<b>不是</b>精确到具体野点坑位,也<b>未</b>做机会成本(lane_available)项(概念有争议,暂缓)。</li>
<li>入口页为 proxy 首版:不含 per-camp 归属、不含"哪个英雄清的"(需与战斗日志 Death 配对,下一步)。</li>
</ul></div>
@@SECTIONS@@
<h2>B2 宏观：每分钟经济差（gold_adv, pooled sum）</h2>
<table><thead>@@THEAD@@</thead><tbody>@@GOLD@@</tbody></table>
<script>
(function(){document.querySelectorAll('th').forEach(function(th,i){
 th.innerHTML+=' <span style="font-size:10px;color:#8b949e"></span>';
 th.onclick=function(){var tb=th.closest('table').tBodies[0];var rows=Array.from(tb.rows);
  var d=1;
  rows.sort(function(a,b){var x=parseFloat(a.cells[i].innerText.replace(/[^0-9.-]/g,''))||a.cells[i].innerText,
   y=parseFloat(b.cells[i].innerText.replace(/[^0-9.-]/g,''))||b.cells[i].innerText;return (x>y?1:x<y?-1:0)*d;});
  d=-d; rows.forEach(function(r){tb.appendChild(r);});};});})();
</script></body></html>"""
    html = html.replace("@@SECTIONS@@", "".join(html_sections)).replace("@@THEAD@@", thead).replace("@@GOLD@@", gold_adv)
    os.makedirs(os.path.dirname(HTML), exist_ok=True)
    open(HTML, "w", encoding="utf-8").write(html)
    print("wrote", HTML)


if __name__ == "__main__":
    main()
