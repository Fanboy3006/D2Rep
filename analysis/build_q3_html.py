#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_q3_html.py - Q3a-REL 人类/玩家友好 HTML 复核页（列组可开关）。

每行一个英雄：
  英雄 | 场次×3 | 队赚×3 | 优势×3(绿/红) | 转优%×3(绿) | flat%×3(灰) | 转劣%×3(红) | 比赛ID
- 三概率列（转优/flat/转劣）都显示，各自占该窗口盘数的百分比。
- 列组 toggle：场次/队赚/优势/转优/flat/转劣/比赛ID，可开关列（表格不臃肿）。
- 点表头排序、顶部搜索、最少盘数过滤、英雄图标、比赛ID下拉。

输入: q3a_rel_agg_hero.csv + q3a_rel_detail.csv
输出: analysis/output_review/q3_rel_viewer.html
"""
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q3 = os.path.join(ROOT, "analysis", "output_q3")
OUTDIR = os.path.join(ROOT, "analysis", "output_review")
os.makedirs(OUTDIR, exist_ok=True)
HTML = os.path.join(OUTDIR, "q3_rel_viewer.html")
WINS = ["0-10", "10-20", "20+"]
ICON_BASE = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/"


def hero_slug(npc):
    return npc.replace("npc_dota_hero_", "")


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def cell(v):
    return "" if v in (None, "") else v


def wcol(base, w):
    return base + "_" + w.replace("-", "_")


def icon_cell(npc):
    slug = hero_slug(npc)
    url = ICON_BASE + slug + ".png"
    label = npc.replace("npc_dota_hero_", "")
    return ('<div class="hero-cell"><img src="%s" onerror="this.style.display=\'none\'" '
            'alt="%s" class="hero-icon"><span>%s</span></div>' % (url, label, label))


def main():
    agg = [r for r in read_rows(os.path.join(Q3, "q3a_rel_agg_hero.csv")) if r.get("hero")]
    detail = read_rows(os.path.join(Q3, "q3a_rel_detail.csv"))

    # ---- 平均水平（ratio 配色的基准）：全体英雄各窗口 转优率/转劣率 的均值 ----
    import statistics
    tu_vals, td_vals = [], []
    for r in agg:
        for w in WINS:
            n = cell(r.get(wcol("n", w)))
            imp = cell(r.get(wcol("imp", w)))
            dec = cell(r.get(wcol("dec", w)))
            if n and imp and float(n) > 0:
                tu_vals.append(float(imp) / float(n))
            if n and dec and float(n) > 0:
                td_vals.append(float(dec) / float(n))
    avg_tu = statistics.mean(tu_vals) if tu_vals else 0.5
    avg_td = statistics.mean(td_vals) if td_vals else 0.5

    hero_matches = {}
    for r in detail:
        if r.get("hero") and r.get("match_id"):
            hero_matches.setdefault(r["hero"], [])
            if r["match_id"] not in hero_matches[r["hero"]]:
                hero_matches[r["hero"]].append(r["match_id"])

    # 列组定义: (group, label, tip, 窗口数)
    groups = [
        ("n", "场次", "这个英雄在这窗口出现了几场（盘数）", 3),
        ("gain", "队赚", "带上这个英雄，队伍每分钟多赚的净值（越大越肥）", 3),
        ("adv", "优势", "相比对手，队伍经济优势每分钟变化（正=变好/绿，负=变差/红）", 3),
        ("tu", "转优", "这窗口里局势明显变好(转优)的场数占比%（绿色）", 3),
        ("fl", "flat", "这窗口里局势基本没变的场数占比%（灰色）", 3),
        ("td", "转劣", "这窗口里局势明显变差(转劣)的场数占比%（红色）", 3),
    ]

    # 列头 + 每列给 group class
    thead_cells = ['<th data-type="text" class="g-hero">英雄</th>']
    for g, label, tip, nw in groups:
        for w in WINS:
            thead_cells.append('<th data-type="num" class="g-%s" title="%s">%s %s</th>' % (g, tip, label, w))
    thead_cells.append('<th data-type="num" class="g-mid" title="构成这行的比赛ID（可回放核对）">比赛ID</th>')
    thead = "<tr>" + "".join(thead_cells) + "</tr>"

    # 行
    rows_html = []
    for r in agg:
        npc = r["hero"]
        mids = hero_matches.get(npc, [])
        dd = ('<select class="midselect" multiple size="4"><option disabled>%d 局组成</option>' % len(mids) +
              "".join('<option value="%s">%s</option>' % (m, m) for m in mids) + "</select>")
        cols = ['<td class="g-hero" data-label="hero">%s</td>' % icon_cell(npc)]
        # 场次 n
        for w in WINS:
            cols.append('<td class="g-n">%s</td>' % cell(r.get(wcol("n", w))))
        # 队赚 gain
        for w in WINS:
            cols.append('<td class="g-gain">%s</td>' % cell(r.get(wcol("gain", w))))
        # 优势 adv (正=绿 负=红)
        for w in WINS:
            v = cell(r.get(wcol("adv", w)))
            extra = ""
            if v:
                try:
                    extra = "pos" if float(v) >= 0 else "neg"
                except Exception:
                    pass
            clsattr = ' class="g-adv %s"' % extra if extra else ' class="g-adv"'
            cols.append('<td%s>%s</td>' % (clsattr, v))
        # 三概率: 转优/平/转劣 的 %（n 盘里占比），配色按 vs 平均水平
        for key, gname, better, color in (("imp", "tu", "higher", "pos"),
                                          ("flat", "fl", "neutral", ""),
                                          ("dec", "td", "lower", "neg")):
            for w in WINS:
                cnt = cell(r.get(wcol(key, w)))
                n = cell(r.get(wcol("n", w)))
                txt, cls = "", ""
                if cnt and n:
                    try:
                        ratio = float(cnt) / float(n)
                        txt = "%.0f%%" % (100 * ratio)
                        if better == "higher":
                            cls = "pos" if ratio >= avg_tu else "neg"
                        elif better == "lower":
                            cls = "pos" if ratio <= avg_td else "neg"
                        # flat: neutral (no color)
                    except Exception:
                        pass
                clsattr = ' class="g-%s %s"' % (gname, cls) if cls else ' class="g-%s"' % gname
                cols.append('<td%s>%s</td>' % (clsattr, txt))
        cols.append('<td class="g-mid midcol">%s</td>' % dd)
        rows_html.append('<tr data-n="%d">%s</tr>' % (len(mids), "".join(cols)))

    # 开关控制栏
    toggles = []
    for g, label, tip, nw in groups + [("mid", "比赛ID", "是否显示比赛ID下拉", 1)]:
        toggles.append('<label class="tg" title="%s"><input type="checkbox" checked onchange="tg(\'%s\')"> %s</label>' % (tip, g, label))
    toggle_bar = '<div class="wrap toggles"><b>列显示开关：</b>' + " ".join(toggles) + "</div>"

    # 图例
    legend = """
<div class="legend">
 <h3>怎么看这张表（给普通玩家）</h3>
 <p>每一行=【一个英雄】。问：<b>带上这个英雄，队伍经济（钱+装备总价值）是比对手涨得快还是慢？</b></p>
 <ul>
  <li><b>英雄</b>：头像+名字。</li>
  <li><b>场次</b>：这英雄在 0-10 / 10-20 / 20+ 分钟各出现几场。<span class="warn">样本少（&lt;10场）只算方向。</span></li>
  <li><b>队赚</b>：带上这英雄，<b>队伍每分钟多赚多少净值</b>（越大越肥）。</li>
  <li><b>优势</b>：相比对手，<b>队伍经济优势每分钟变化</b>。<i class="pos">正=越来越好(绿)</i>，<i class="neg">负=被拉开(红)</i>。</li>
  <li><b>转优 / flat / 转劣</b>：这些场次里<b>局势明显变好(优势≥+1000) / 基本没变 / 明显变差</b>各占多少<b>百分比</b>，三列≈100%。<b>颜色按"高于/低于全体平均"</b>：转优高于平均=绿、低于=红；转劣低于平均(=转劣少)=绿、高于=红；flat 灰。<span class="ex">平均转优率≈@@AVGTU@@%，平均转劣率≈@@AVGTD@@%</span>。</li>
  <li><b>比赛ID</b>：点开看这行由哪几局构成（可回放核对）。</li>
 </ul>
 <p><b>列开关</b>：表头上面一排勾选框可<u>显示/隐藏</u>各列组，表格不臃肿；悬停勾选框看该组说明。</p>
 <p>口诀：<b>队赚</b>看捞多少，<b>优势</b>看比对手是否更肥，<b>转优/转劣</b>看多久带出优势。</p>
</div>"""

    html = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>DOTA Q3a-REL 复核页</title>
<style>
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#e6edf3;margin:24px}
h1{font-size:18px}
input#f{width:300px;padding:8px;border:1px solid #30363d;border-radius:6px;background:#161b22;color:#e6edf3}
table{border-collapse:collapse;width:100%;font-size:13px;background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden}
th,td{padding:7px 9px;border-bottom:1px solid #21262d;text-align:right;white-space:nowrap}
th{background:#21262d;cursor:pointer;user-select:none;position:sticky;top:0}
th:hover{background:#30363d}
th .arr{font-size:10px;color:#8b949e}
td:first-child,th:first-child{text-align:left}
.hero-cell{display:flex;align-items:center;gap:8px;min-width:200px}
.hero-icon{width:38px;height:38px;border-radius:6px;background:#30363d;object-fit:cover}
tr:hover{background:#1f6feb22}
select.midselect{width:190px;background:#0d1117;color:#8b949e;border:1px solid #30363d;border-radius:4px;font-size:11px}
.note{color:#8b949e;font-size:12px;margin:6px 0}
.legend{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;margin:14px 0;font-size:13px;line-height:1.7}
.legend h3{margin:0 0 8px;font-size:15px}.legend li{margin:3px 0}
.legend .pos{color:#3fb950;font-weight:700}.legend .neg{color:#f85149;font-weight:700}.legend .warn{color:#d29922}
td.pos{color:#3fb950;font-weight:700}
td.neg{color:#f85149;font-weight:700}
toggles{ }
.toggles{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:12px}
.tg{background:#21262d;border:1px solid #30363d;border-radius:14px;padding:3px 9px;cursor:pointer}
.tg:hover{background:#30363d}
</style></head><body>
<h1>Q3a-REL：英雄 &times; 窗口（队伍经济增速 / 经济优势 / 转优-平-转劣）</h1>
<div class="note">点表头排序 · 搜索+最少盘数 · 英雄图标 · 悬停表头看说明 · 列开关显示/隐藏</div>
@@LEGEND@@
@@TOGGLES@@
<div class="wrap"><input id="f" placeholder="筛选英雄名…" oninput="flt()">
 <label style="margin-left:12px">最少盘数 <select id="minn" onchange="flt()">
  <option value="1">1</option><option value="3">3</option><option value="5">5</option>
  <option value="8" selected>8</option><option value="10">10</option><option value="15">15</option></select></label>
 <span class="note" id="cnt"></span></div>
<table id="t"><thead>@@THEAD@@</thead><tbody>@@ROWS@@</tbody></table>
<script>
function flt(){ var q=document.getElementById('f').value.toLowerCase();
  var mn=+document.getElementById('minn').value; var shown=0;
  document.querySelectorAll('#t tbody tr').forEach(function(tr){
    var ok=(tr.dataset.n>=mn) && tr.textContent.toLowerCase().indexOf(q)>=0;
    tr.style.display=ok?'':'none'; if(ok)shown++;});
  document.getElementById('cnt').textContent='显示 '+shown+' 个英雄 (≥'+mn+' 盘)';}
function tg(g){ var on=event.target.checked;
  document.querySelectorAll('#t th.g-'+g+', #t td.g-'+g).forEach(function(c){c.style.display=on?'':'none';})}
var dir=1,last=-1;
document.querySelectorAll('#t th').forEach(function(th,i){
  if(th.style.display==='none')return;
  th.innerHTML+=' <span class="arr"></span>';
  th.onclick=function(){ var tb=document.querySelector('#t tbody');
    var rows=Array.from(tb.rows);
    if(last===i)dir=-dir; else dir=1; last=i;
    rows.sort(function(a,b){var x=a.cells[i].innerText,y=b.cells[i].innerText;
      var xv=isNaN(parseFloat(x.replace(/[^0-9.-]/g,'')))||x===''?x:parseFloat(x.replace(/[^0-9.-]/g,''));
      var yv=isNaN(parseFloat(y.replace(/[^0-9.-]/g,'')))||y===''?y:parseFloat(y.replace(/[^0-9.-]/g,''));
      return (xv>yv?1:xv<yv?-1:0)*dir;});
    rows.forEach(function(r){tb.appendChild(r);});
    document.querySelectorAll('#t th .arr').forEach(function(a){a.textContent='';});
    th.querySelector('.arr').textContent=dir>0?'▲':'▼';
  };
});
</script></body></html>"""

    html = (html
            .replace("@@THEAD@@", thead)
            .replace("@@ROWS@@", "".join(rows_html))
            .replace("@@LEGEND@@", legend)
            .replace("@@TOGGLES@@", toggle_bar)
            .replace("@@AVGTU@@", "%.0f" % (avg_tu * 100))
            .replace("@@AVGTD@@", "%.0f" % (avg_td * 100)))

    with open(HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", HTML, len(html))


if __name__ == "__main__":
    sys.exit(main())
