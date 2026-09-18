#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_all_teams_q2.py - 全部队伍 Q2（10min/10→20 经济状态→胜率）。

数据：
  - OpenDota /api/leagues/{id}/matches 拿到所有公开比赛的天辉/夜魇 team_id + radiant_win + 队名。
  - 每场 gold_adv@10/20 用已解析 .dem 的精确净值(天辉5人求和-夜魇5人求和, game-clock对齐) 算。
  然后按"每支队伍"聚合：场次/胜/胜率 + 10min 线优/线平/线劣胜率 + 10→20 转优/平/劣胜率。
输出：analysis/output_review/q2_all_teams_viewer.html
"""
import collections
import json
import os
import sqlite3
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.opendota.com"
UA = {"User-Agent": "Mozilla/5.0 (dota-replay-analyzer)"}
OUT = os.path.join(ROOT, "analysis", "output_review")
os.makedirs(OUT, exist_ok=True)
HTML = os.path.join(OUT, "q2_all_teams_viewer.html")
THR = 1000
DEMS_DB = os.path.join(ROOT, "dems", "db")
TEAM_NAME_CACHE = os.path.join(ROOT, ".tmp", "team_names.json")


def api(path, retries=3):
    for a in range(retries):
        try:
            req = urllib.request.Request(API + path, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            print("  err", path, e, file=sys.stderr)
            time.sleep(2 * (a + 1))
    return None


def load_team_names():
    names = {}
    if os.path.exists(TEAM_NAME_CACHE):
        return json.load(open(TEAM_NAME_CACHE, encoding="utf-8"))
    return names


def save_team_names(names):
    json.dump(names, open(TEAM_NAME_CACHE, "w", encoding="utf-8"), ensure_ascii=False)


def main():
    con = sqlite3.connect(os.path.join(ROOT, "stats.db"))
    con.row_factory = sqlite3.Row
    # 官方选手名: steam_id -> name（pro_players.json）
    pro_name = {}
    try:
        pp = json.load(open(os.path.join(ROOT, ".tmp", "pro_players.json"), encoding="utf-8"))
        for p in pp:
            if p.get("steamid") and p.get("name"):
                pro_name[str(p["steamid"])] = p["name"]
    except Exception as e:
        print("pro_players load warn:", e)
    print("pro players with name:", len(pro_name))
    # 联赛官方名: league_id -> name
    league_names = {}
    try:
        league_names = json.load(open(os.path.join(ROOT, ".tmp", "league_names.json"), encoding="utf-8"))
    except Exception:
        pass
    leagues = [r[0] for r in con.execute("SELECT DISTINCT league_id FROM matches WHERE league_id IS NOT NULL")]
    print("leagues:", leagues)

    # 1. fetch all league matches -> metadata
    match_meta = {}
    names = load_team_names()
    for lid in leagues:
        ms = api("/api/leagues/%d/matches" % lid)
        time.sleep(0.7)
        if not isinstance(ms, list):
            continue
        for m in ms:
            mid = m.get("match_id")
            if not mid:
                continue
            rtid, dtid = m.get("radiant_team_id"), m.get("dire_team_id")
            rw = m.get("radiant_win")
            match_meta[mid] = {"radiant_team_id": rtid, "dire_team_id": dtid,
                               "radiant_win": (1 if rw is True else 0 if rw is False else None),
                               "league": lid}
            for tid in (rtid, dtid):
                if tid and tid not in names:
                    tm = api("/api/teams/%d" % tid)
                    time.sleep(0.5)
                    names[tid] = (tm or {}).get("name") or ("TEAM_%s" % tid)
                    save_team_names(names)
    print("matches with metadata:", len(match_meta), "teams:", len(names))

    # 2. compute gold_adv@10/20 from dem networth (exact, game-clock aligned), per match
    # team -> aggregated Q2
    team_rows = collections.defaultdict(list)  # team_id -> list of (s10, s20, win)
    team_name = {}
    team_roster = collections.defaultdict(set)  # team_id -> set(steam_id)  (近似合并用)
    team_players = collections.defaultdict(dict)  # team_id -> {steam_id: player_name}
    team_leagues = collections.defaultdict(collections.Counter)  # team_id -> {league_name: matches}
    def value_at(series, sec):
        ok = [s for s in series if s <= sec]
        if ok:
            return series[max(ok)]
        return series[min(series)] if series else None
    n_matches = 0
    for db in sorted(os.listdir(DEMS_DB)):
        ld = os.path.join(DEMS_DB, db)
        for f in os.listdir(ld):
            if not f.endswith(".db"):
                continue
            mid = int(f[:-3])
            meta = match_meta.get(mid)
            if not meta or not meta["radiant_team_id"] or not meta["dire_team_id"]:
                continue
            con2 = sqlite3.connect(os.path.join(ld, f))
            con2.row_factory = sqlite3.Row
            # per-player net worth + team
            pnw = {}
            for r in con2.execute("SELECT entity_id,game_time_sec,hp FROM entity_snapshots WHERE entity_type='networth'"):
                _, team, idx = r["entity_id"].split(":")
                pnw.setdefault((team, int(idx)), {})[r["game_time_sec"]] = r["hp"]
            if not pnw:
                con2.close(); continue
            # game-start
            allsec = sorted(set(s for v in pnw.values() for s in v))
            gs = None
            for sec in allsec:
                if any(v[sec] > 0 for v in pnw.values() if sec in v):
                    gs = sec; break
            if gs is None:
                con2.close(); continue
            # team nw per second
            teamnw = {"radiant": {}, "dire": {}}
            for (team, idx), v in pnw.items():
                for s, val in v.items():
                    teamnw[team][s] = teamnw[team].get(s, 0) + val
            # gold_adv at minute 10 and 20 (game-relative)
            def adv_at(sec):
                r = value_at(teamnw["radiant"], gs + sec)
                d = value_at(teamnw["dire"], gs + sec)
                return (r - d) if (r is not None and d is not None) else None
            adv10 = adv_at(600); adv20 = adv_at(1200)
            if adv10 is None or adv20 is None:
                con2.close(); continue
            dw = adv20 - adv10
            rw = meta["radiant_win"]
            # roster: steam_ids per side for this team_id (近似合并依据)
            try:
                ids_r = set(); ids_d = set()
                for r in con2.execute("SELECT player_slot, steam_id, player_name FROM player_identity WHERE match_id=? AND steam_id IS NOT NULL AND steam_id>0", (mid,)):
                    slot = r["player_slot"]
                    off = pro_name.get(str(r["steam_id"]), r["player_name"] or ("steam_%s" % r["steam_id"]))
                    if slot < 128:
                        ids_r.add(r["steam_id"]); team_players[meta["radiant_team_id"]].setdefault(r["steam_id"], off)
                    else:
                        ids_d.add(r["steam_id"]); team_players[meta["dire_team_id"]].setdefault(r["steam_id"], off)
                team_roster[meta["radiant_team_id"]] |= ids_r
                team_roster[meta["dire_team_id"]] |= ids_d
            except Exception:
                pass
            for side, tid in (("R", meta["radiant_team_id"]), ("D", meta["dire_team_id"])):
                if side == "R":
                    t10, t20, win = adv10, adv20, rw
                else:
                    t10, t20 = -adv10, -adv20
                    win = (1 - rw) if rw is not None else None
                s10 = "lead" if t10 > THR else ("trail" if t10 < -THR else "even")
                s20 = "up" if dw > THR else ("down" if dw < -THR else "flat")
                team_rows[tid].append((s10, s20, win))
                team_name[tid] = names.get(tid, str(tid))
                lname = league_names.get(str(meta["league"])) or "league_%s" % meta["league"]
                team_leagues[tid][lname] += 1
            n_matches += 1
            con2.close()
    print("matches computed:", n_matches, "teams:", len(team_rows))

    # ---- 近似合并：两个 org 共用 >=3 名选手(steam_id) -> 并成一个队 ----
    team_ids = list(team_rows.keys())
    parent = {t: t for t in team_ids}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    for i, t1 in enumerate(team_ids):
        for t2 in team_ids[i + 1:]:
            if (team_roster[t1] and team_roster[t2] and len(team_roster[t1] & team_roster[t2]) >= 3):
                union(t1, t2)
    # canonical name per group = the member with most matches
    merged = collections.defaultdict(list)
    n_matches_of = {t: len(team_rows[t]) for t in team_ids}
    for t in team_ids:
        merged[find(t)].append(t)
    team_merge = {}  # team_id -> canonical name
    for root, mems in merged.items():
        best = max(mems, key=lambda m: n_matches_of[m])
        canonical = team_name[best]
        for m in mems:
            team_merge[m] = canonical
    print("after merge:", len(merged), "groups (from", len(team_ids), "teams)")
    print("  merged e.g.:", {team_name[k]: team_name[find(k)] for k in list(team_ids)[:8] if find(k) != k})

    # aggregate by merged team
    merged_rows = collections.defaultdict(list)
    merged_roster = collections.defaultdict(dict)
    merged_leagues = collections.defaultdict(collections.Counter)  # group -> {league_name: matches}
    for t, recs in team_rows.items():
        merged_rows[team_merge[t]].extend(recs)
    for t in team_ids:
        g = team_merge[t]
        for sid, nm in team_players[t].items():
            merged_roster[g].setdefault(sid, nm)
        merged_leagues[g].update(team_leagues[t])  # same group: merge regardless of root path
    # mark merged teams (contained >1 original team_id)
    merged_flag = {team_merge[t] for t in team_ids if find(t) in [x for x in parent if parent.get(x)==x and len([m for m in team_ids if find(m)==x])>1]}

    # 全体平均值（分类率的配色/±差基准）
    def class_avg(key, groupvals):
        arr = []
        for name, recs in merged_rows.items():
            n = len(recs)
            if not n:
                continue
            idx = 0 if key == "s10" else 1
            c = sum(1 for r in recs if r[idx] == groupvals)
            arr.append(c / n)
        return sum(arr) / len(arr) if arr else 0
    avg_class10 = {"lead": class_avg("s10", "lead"), "even": class_avg("s10", "even"), "trail": class_avg("s10", "trail")}
    avg_class20 = {"up": class_avg("s20", "up"), "flat": class_avg("s20", "flat"), "down": class_avg("s20", "down")}

    def fmt_rate(rate, avg, noteutral=False):
        """'X% (+Y%)' + color; neutural(线平/持平) not colored. higher=better for lead/up, lower=better for trail/down."""
        if rate is None:
            return "", ""
        txt = "%.0f%%" % (100 * rate)
        d = rate - avg
        if abs(d) >= 0.005:
            txt += " (%+.1f%%)" % (100 * d)   # 与平均相比多/少
        return txt, d

    rows_html, teams_out = [], []
    for name, recs in sorted(merged_rows.items(), key=lambda kv: -len(kv[1])):
        n = len(recs)
        wins = sum(1 for r in recs if r[2] == 1)
        wr = wins / n if n else 0
        def cls(key, val):
            idx = 0 if key == "s10" else 1
            return sum(1 for x in recs if x[idx] == val) / n if n else None
        def st_rate(key, val):
            idx = 0 if key == "s10" else 1
            lst = [r[2] for r in recs if r[idx] == val]
            return (sum(1 for w in lst if w == 1) / len(lst)) if lst else None
        roster = merged_roster.get(name, {})
        dd = '<select class="midselect" multiple size="3"><option disabled>%d名选手(官方ID)</option>' % len(roster) + \
             "".join('<option value="%s">%s (%s)</option>' % (sid, nm, sid) for sid, nm in sorted(roster.items(), key=lambda kv: kv[1])) + "</select>"
        # 来源联赛 = 该队比赛来自哪些联赛(官方名) + 场次
        lc = merged_leagues.get(name, {})
        lcount = sum(lc.values())
        if lc:
            ldd = ('<select class="midselect leaguesel" multiple size="4" title="该队比赛来源联赛">'
                   '<option disabled>来源联赛(%d场)</option>' % lcount +
                   "".join('<option>%s · %d场</option>' % (ln, c) for ln, c in sorted(lc.items(), key=lambda kv: -kv[1])) + "</select>")
        else:
            ldd = "—"
        cells = ['<td class="g-base">%s<br><small>%d场%s</small></td>' % (name, n, "·合并" if name in merged_flag else "")]
        cells.append('<td class="g-base">%d</td>' % n)
        cells.append('<td class="g-win">%d</td>' % wins)
        cells.append('<td class="g-wr %s">%.0f%%</td>' % ("pos" if wr >= 0.5 else "neg", 100 * wr))
        # 10min classification 率 (线优/平/劣), vs avg, 线平 no color
        for st, avg, higher_better, neutral in (("lead", avg_class10["lead"], True, False),
                                                ("even", avg_class10["even"], None, True),
                                                ("trail", avg_class10["trail"], False, False)):
            c = cls("s10", st)
            txt, d = fmt_rate(c, avg)
            color = "" if neutral else ("pos" if ((higher_better and d >= 0) or (not higher_better and d <= 0)) else "neg")
            cells.append('<td class="g-c10 %s">%s</td>' % (color, txt) if txt else '<td class="g-c10">—</td>')
        # 10min win-rate given state (线优情况胜率)
        for st in ("lead", "even", "trail"):
            r = st_rate("s10", st)
            cells.append('<td class="g-w10 %s">%s</td>' % ("pos" if r is not None and r >= 0.5 else "neg",
                                                          ("%.0f%%" % (100 * r)) if r is not None else "—"))
        # 10->20 classification 率
        for st, avg, higher_better, neutral in (("up", avg_class20["up"], True, False),
                                                ("flat", avg_class20["flat"], None, True),
                                                ("down", avg_class20["down"], False, False)):
            c = cls("s20", st)
            txt, d = fmt_rate(c, avg)
            color = "" if neutral else ("pos" if ((higher_better and d >= 0) or (not higher_better and d <= 0)) else "neg")
            cells.append('<td class="g-c20 %s">%s</td>' % (color, txt) if txt else '<td class="g-c20">—</td>')
        # 10->20 win-rate given state
        for st in ("up", "flat", "down"):
            r = st_rate("s20", st)
            cells.append('<td class="g-w20 %s">%s</td>' % ("pos" if r is not None and r >= 0.5 else "neg",
                                                          ("%.0f%%" % (100 * r)) if r is not None else "—"))
        cells.append('<td class="g-roster rostercol">%s</td>' % dd)
        cells.append('<td class="g-league">%s</td>' % ldd)
        rows_html.append('<tr data-team="%s">%s</tr>' % (name, "".join(cells)))
        teams_out.append((name, n))

    # 队伍勾选栏（按队显示/隐藏）
    team_box = ("<div class='tog'><b>队伍选择：</b>"
                "<label class='tg'><input type='checkbox' checked onchange=\"tgl('')\">全选</label> " +
                "".join('<label class="tg"><input type="checkbox" class="tm" checked data-t="%s" onchange="flt()">%s</label>' % (name, name) for name, _ in teams_out) +
                "</div>")

    legend = """<div class="legend"><h3>怎么看这张表（全部队伍，已近似合并队伍/替补）</h3>
<p>这是公开比赛里所有能对上的队伍：<b>开局第10分钟经济领先/落后，对最终胜负的影响</b>。</p>
<ul>
<li><b>队伍</b>（org 名，含"合并"标记=由共用≥3名选手的队伍近似合并）。</li>
<li><b>场次/胜/胜率</b>。</li>
<li><b>线优率/线平率/线劣率</b>=<b>被判定为10分钟领先/持平/落后的盘数占比</b>（领先&gt;1000，落后&lt;-1000）。<span class="pos">绿</span>=高于全体平均，<span class="neg">红</span>=低于平均；<b>线平率不配色</b>。括号 <span class="ex">(+/-X%)</span> = 与全体平均相比较多/少多少。</li>
<li><b>转优/持平/转劣率</b>=10→20经济滚大/持平/被拉开的<b>盘数占比</b>（同上配色+±差）。</li>
<li><b>线优情况胜率</b>等=在该状态下的<br><b>胜率</b>（绿=赢面大）。</li>
<li><b>选手官方ID</b>：该队名单（官方名+steam_id，来自 pro_players.json）。</li>
<li><b>来源联赛</b>：该队这些比赛来自哪些联赛（官方名+各自场次，league id→官方名）。</li>
<li>样本&lt;10场只作方向参考。</li></ul></div>"""
    html = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>Q2 全队伍</title>
<style>
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#e6edf3;margin:24px}
table{border-collapse:collapse;width:100%;font-size:13px;background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden}
th,td{padding:7px 9px;border-bottom:1px solid #21262d;text-align:right;white-space:nowrap}
th{background:#21262d;cursor:pointer;position:sticky;top:0}
td:first-child{text-align:left}
tr:hover{background:#1f6feb22}
.legend{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;margin:14px 0;font-size:13px;line-height:1.7}
.legend h3{margin:0 0 8px}.legend .pos{color:#3fb950}.legend .neg{color:#f85149}.legend .ex{color:#79c0ff}
td.pos{color:#3fb950;font-weight:700}td.neg{color:#f85149;font-weight:700}
input#f{width:260px;padding:8px;border:1px solid #30363d;border-radius:6px;background:#161b22;color:#e6edf3}
.tg{background:#21262d;border:1px solid #30363d;border-radius:14px;padding:3px 9px;cursor:pointer;font-size:12px}
.tog{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0}
</style></head><body>
<h1>Q2：全部队伍 10分钟经济 → 胜率 &amp; 10→20 滚雪球（含分类率）</h1>
@@LEGEND@@
<div class="tog"><b>列开关：</b>
@@TOGGLES@@
</div>
@@TEAMBOX@@
<div class="wrap"><input id="f" placeholder="筛选队伍…" oninput="flt()"></div>
<table id="t"><thead>@@THEAD@@</thead><tbody>@@ROWS@@</tbody></table>
<script>
function flt(){var q=document.getElementById('f').value.toLowerCase();
 document.querySelectorAll('#t tbody tr').forEach(function(r){
   var cb=document.querySelector('.tm[data-t="'+r.dataset.team+'"]');
   var okTeam=(!cb||cb.checked);
   var okText=r.textContent.toLowerCase().indexOf(q)>=0;
   r.style.display=(okTeam&&okText)?'':'none';});}
function tgl(){var all=document.querySelectorAll('.tm');
 var any=Array.from(all).some(function(c){return c.checked;});
 all.forEach(function(c){c.checked=any?false:true;});flt();}
function tg(g){var on=event.target.checked;document.querySelectorAll('#t th.g-'+g+',#t td.g-'+g).forEach(function(c){c.style.display=on?'':'none';})}
var lastTb=null,lastCol=-1,dir=1;
document.querySelectorAll('#t th').forEach(function(th,i){
  th.innerHTML+=' <span class="arr" style="font-size:10px;color:#8b949e"></span>';
  th.onclick=function(){var tb=document.querySelector('#t tbody');
    var rows=Array.from(tb.rows);
    if(lastTb===tb&&lastCol===i){dir=-dir;}else{dir=1;lastTb=tb;lastCol=i;}
    rows.sort(function(a,b){var x=parseFloat(a.cells[i].innerText.replace(/[^0-9.-]/g,''))||a.cells[i].innerText,
     y=parseFloat(b.cells[i].innerText.replace(/[^0-9.-]/g,''))||b.cells[i].innerText;return (x>y?1:x<y?-1:0)*dir;});
    rows.forEach(function(r){tb.appendChild(r);});};});
</script></body></html>"""
    # header
    thead = ("<tr><th class='g-base'>队伍</th><th class='g-base'>场次</th><th class='g-win'>胜</th><th class='g-wr'>胜率</th>"
             "<th class='g-c10'>线优率</th><th class='g-c10'>线平率</th><th class='g-c10'>线劣率</th>"
             "<th class='g-w10'>线优情况胜率</th><th class='g-w10'>线平情况胜率</th><th class='g-w10'>线劣情况胜率</th>"
             "<th class='g-c20'>转优率</th><th class='g-c20'>持平率</th><th class='g-c20'>转劣率</th>"
             "<th class='g-w20'>转优情况胜率</th><th class='g-w20'>持平情况胜率</th><th class='g-w20'>转劣情况胜率</th>"
             "<th class='g-roster'>选手官方ID</th>"
             "<th class='g-league'>来源联赛</th></tr>")
    toggles = ("<label class='tg'><input type='checkbox' checked onchange=\"tg('g-base')\">队伍/场次</label>"
               "<label class='tg'><input type='checkbox' checked onchange=\"tg('g-win')\">胜</label>"
               "<label class='tg'><input type='checkbox' checked onchange=\"tg('g-wr')\">胜率</label>"
               "<label class='tg'><input type='checkbox' checked onchange=\"tg('g-c10')\">10min分类率(线优/平/劣)</label>"
               "<label class='tg'><input type='checkbox' checked onchange=\"tg('g-w10')\">10min情况胜率</label>"
               "<label class='tg'><input type='checkbox' checked onchange=\"tg('g-c20')\">10→20分类率</label>"
               "<label class='tg'><input type='checkbox' checked onchange=\"tg('g-w20')\">10→20情况胜率</label>"
               "<label class='tg'><input type='checkbox' checked onchange=\"tg('g-roster')\">选手官方ID</label>"
               "<label class='tg'><input type='checkbox' checked onchange=\"tg('g-league')\">来源联赛</label>")
    html = (html.replace("@@THEAD@@", thead).replace("@@ROWS@@", "".join(rows_html))
                .replace("@@LEGEND@@", legend).replace("@@TOGGLES@@", toggles)
                .replace("@@TEAMBOX@@", team_box))
    open(HTML, "w", encoding="utf-8").write(html)
    print("wrote", HTML)
    print("teams:", teams_out[:25], "...")
    print("avg_class10:", {k: round(v * 100, 1) for k, v in avg_class10.items()},
          "avg_class20:", {k: round(v * 100, 1) for k, v in avg_class20.items()})


if __name__ == "__main__":
    sys.exit(main())
