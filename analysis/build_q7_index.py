#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_q7_index.py — Q7 多场索引页 + 批量构建（「多场切换」的落地方案）。

为什么不是"一个页面里切 970 场"：完整版单场 4~6MB（含 ±10s 全量 combat log），970 场 ≈ 5GB，
不可能塞进一个仓库/一个页面。所以拆成：
  · **索引页** `q7_index.html`：970 场的可搜索/可筛选清单（联赛、队伍、英雄、胜负、时长），
    已构建的直接给链接，未构建的给"一行构建命令"（可一键复制整批筛选结果）。
  · **lite 版**（`build_q7_html.py --lite`）：0.4~0.6MB/场（地图 448px、逐秒数据抽稀 4s、
    不含 ±10s 明细与技能图标；地图/时间轴/经济·经验差/胜率/KDA/眼位/烟雾/技能CD 全保留）→ 可批量铺。
  · **完整版**：只给 owner 点名/关心的场次（含 ±10s 全量明细，5~6MB）。

索引数据来源（全部只读、按 (match_id, type_category) 索引查询，快）：
  combat_log：号角（gamestate value=5）、远古被摧毁（death target=*_fort）→ 时长与胜负；
  player_identity：10 名英雄与队伍；stats.db：队名/开赛时间/OpenDota 时长（仅覆盖已抓取的联赛）。

用法：
  python analysis/build_q7_index.py                        # 只重建索引页
  python analysis/build_q7_index.py --rescan                # 重新扫 970 场（约 1~3 分钟）
  python analysis/build_q7_index.py --batch league:19719    # 批量构建该联赛的 lite
  python analysis/build_q7_index.py --batch ids:8830423116,8955197224 --full
  python analysis/build_q7_index.py --batch top:20          # 按联赛/时长排序取前 20（示例）
  python analysis/build_q7_index.py --publish               # 复制索引页到 publish_repo
"""
import argparse
import glob as _glob
import json
import os
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DBFULL = os.path.join(ROOT, "dems", "db_full")
STATS_DB = os.path.join(ROOT, "stats.db")
CACHE = os.path.join(HERE, "output_q7", "q7_index.json")
OUT = os.path.join(HERE, "output_review", "q7_index.html")
PUBLISH = os.path.join(ROOT, "publish_repo")
FORT_R, FORT_D = "npc_dota_badguys_fort", "npc_dota_goodguys_fort"


def stats_lookup():
    """stats.db（OpenDota）覆盖到的场次：队名/开赛/时长/胜负。缺则空 dict（不硬造）。"""
    out = {}
    if not os.path.exists(STATS_DB):
        return out
    try:
        con = sqlite3.connect(STATS_DB)
        con.row_factory = sqlite3.Row
        teams = {r["team_id"]: r["name"] for r in con.execute("SELECT team_id, name FROM teams")}
        for r in con.execute("SELECT match_id, radiant_team_id, dire_team_id, start_time, duration_sec, "
                             "radiant_win, league_id FROM matches"):
            out[int(r["match_id"])] = {
                "rt": teams.get(r["radiant_team_id"]), "dt": teams.get(r["dire_team_id"]),
                "start": r["start_time"], "dur": r["duration_sec"], "rwin": r["radiant_win"],
                "league": r["league_id"],
            }
        con.close()
    except Exception as e:
        sys.stderr.write("stats.db 读取失败：%s\n" % e)
    return out


def scan_one(db, mid, league, st):
    con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    rec = {"mid": int(mid), "league": int(league) if str(league).isdigit() else league,
           "heroes": [], "rwin": None, "dur": None, "t0": None}
    try:
        h = con.execute("SELECT t_cle FROM combat_log WHERE match_id=? AND type_category='gamestate' "
                        "AND value=5 LIMIT 1", (mid,)).fetchone()
        horn = float(h["t_cle"]) if h else None
        f = {r["target"]: r["t"] for r in con.execute(
            "SELECT target, MIN(t_cle) t FROM combat_log WHERE match_id=? AND type_category='death' "
            "AND target IN (?,?) GROUP BY target", (mid, FORT_R, FORT_D))}
        if f:
            if FORT_R in f and FORT_D in f:
                rec["rwin"] = 1 if f[FORT_R] <= f[FORT_D] else 0
                end = min(f[FORT_R], f[FORT_D])
            elif FORT_R in f:
                rec["rwin"] = 1
                end = f[FORT_R]
            else:
                rec["rwin"] = 0
                end = f[FORT_D]
            if horn is not None:
                rec["dur"] = int(round(float(end) - horn))
        for r in con.execute("SELECT hero_name, team_id, player_slot FROM player_identity ORDER BY player_slot"):
            rec["heroes"].append([r["hero_name"].replace("npc_dota_hero_", ""), int(r["team_id"]),
                                  int(r["player_slot"])])
    except Exception as e:
        rec["err"] = str(e)
    finally:
        con.close()
    if rec["rwin"] is not None and st.get(int(mid)) and st[int(mid)].get("rwin") is not None:
        if rec["rwin"] != st[int(mid)]["rwin"]:
            rec["win_conflict"] = [rec["rwin"], st[int(mid)]["rwin"]]
    if st.get(int(mid)):
        s = st[int(mid)]
        rec["rt"], rec["dt"], rec["start"] = s.get("rt"), s.get("dt"), s.get("start")
        rec["od_dur"] = s.get("dur")
    return rec


def build_index(rescan=False):
    if not rescan and os.path.exists(CACHE):
        data = json.load(open(CACHE, encoding="utf-8"))
        print("索引缓存：%d 场（--rescan 可重扫）" % len(data["matches"]))
        return data
    st = stats_lookup()
    dbs = sorted(_glob.glob(os.path.join(DBFULL, "*", "*.db")))
    print("扫描 %d 场（stats.db 覆盖 %d 场）…" % (len(dbs), len(st)))
    t0 = time.time()
    ms = []
    for i, db in enumerate(dbs):
        mid = os.path.basename(db)[:-3]
        lg = os.path.basename(os.path.dirname(db))
        ms.append(scan_one(db, mid, lg, st))
        if i % 100 == 0:
            print("  %d/%d  %.0fs" % (i, len(dbs), time.time() - t0))
    data = {"matches": ms, "scanned_at": time.strftime("%Y-%m-%d %H:%M"), "n": len(ms)}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(data, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("扫描完成 %d 场，用时 %.0fs → %s" % (len(ms), time.time() - t0, os.path.relpath(CACHE, ROOT)))
    return data


def built_files():
    """publish_repo / output_review 里已存在的 Q7 viewer（决定索引页给链接还是给命令）。"""
    full, lite = set(), set()
    for d in (PUBLISH, os.path.join(HERE, "output_review")):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.startswith("q7_replay_") and f.endswith(".html"):
                mid = f[len("q7_replay_"):-len(".html")]
                if mid.endswith("_lite"):
                    lite.add(mid[:-len("_lite")])
                else:
                    full.add(mid)
    return full, lite


def htm(res):
    full, lite = built_files()
    rows = []
    for m in res["matches"]:
        rows.append({
            "mid": m["mid"], "lg": m["league"], "rt": m.get("rt"), "dt": m.get("dt"),
            "rwin": m.get("rwin"), "dur": m.get("dur"), "od": m.get("od_dur"),
            "start": m.get("start"), "err": m.get("err"),
            "h": [h[0] for h in m["heroes"]], "hr": [h[0] for h in m["heroes"] if h[1] == 2],
            "hd": [h[0] for h in m["heroes"] if h[1] == 3],
            "full": 1 if str(m["mid"]) in full else 0, "lite": 1 if str(m["mid"]) in lite else 0,
        })
    blob = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    nfull = sum(r["full"] for r in rows)
    nlite = sum(r["lite"] for r in rows)
    return HTML_TMPL.replace("@@BLOB@@", blob).replace("@@N@@", str(len(rows))) \
        .replace("@@NFULL@@", str(nfull)).replace("@@NLITE@@", str(nlite)) \
        .replace("@@WHEN@@", res.get("scanned_at", ""))


HTML_TMPL = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>Q7 回放浏览器 · 多场索引</title>
<style>
body{font-family:-apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:16px 20px 60px}
h1{font-size:18px;margin:0 0 6px}
.sub{color:#8b949e;font-size:12px;line-height:1.75;margin-bottom:10px}
.sub b{color:#79c0ff}.sub code{background:#21262d;padding:1px 4px;border-radius:3px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;background:#161b22;border:1px solid #30363d;
     border-radius:8px;padding:8px 10px;margin-bottom:8px;font-size:12px;position:sticky;top:0;z-index:5}
input[type=text],select{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:4px;padding:4px 6px;font-size:12px}
input[type=text]{width:190px}
label{display:inline-flex;align-items:center;gap:4px;color:#8b949e}
.btn{background:#21262d;border:1px solid #30363d;border-radius:12px;padding:4px 10px;cursor:pointer;font-size:12px;color:#e6edf3}
.btn:hover{border-color:#4d5866}
#stat{color:#8b949e}
table{border-collapse:collapse;font-size:12px;width:100%}
th,td{padding:3px 6px;border-bottom:1px solid #21262d;text-align:left;white-space:nowrap}
th{background:#21262d;position:sticky;top:52px;cursor:pointer}
th:hover{color:#79c0ff}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr:hover{background:#1f6feb22}
a{color:#58a6ff;text-decoration:none}a:hover{text-decoration:underline}
.dr{color:#4aa564}.dd{color:#d24b4b}
.mut{color:#8b949e}
.tag{display:inline-block;padding:0 5px;border-radius:8px;font-size:10px;line-height:15px;background:#21262d;color:#8b949e}
.tag.full{background:#1f6feb;color:#fff}.tag.lite{background:#2d4f6b;color:#cfe6ff}
#cmdwrap{display:none;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 10px;margin:8px 0;font-size:12px}
#cmd{width:100%;height:52px;background:#0d1117;color:#7ee787;border:1px solid #30363d;border-radius:4px;font-family:ui-monospace,Consolas,monospace;font-size:11px}
</style></head><body>
<h1>Q7 回放浏览器 · 多场索引（<span id="nn">@@N@@</span> 场）</h1>
<div class="sub">
  已构建：<span class="tag full">完整版 @@NFULL@@ 场</span>（含 ±10s 全量 combat log 明细，4~6MB/场）
  <span class="tag lite">lite @@NLITE@@ 场</span>（0.4~0.6MB/场：地图 448px、逐秒数据抽稀、不含明细与技能图标；
  地图/双时间轴/经济·经验差/状态胜率/KDA/眼位/烟雾/技能 CD 全保留）。
  索引扫描于 @@WHEN@@。<br>
  <b>为什么不是"一个页面切 970 场"</b>：完整版单场 4~6MB（±10s 明细是大头），970 场 ≈ 5GB，塞不进一个仓库/页面。
  所以：索引页负责"找场次 + 一行命令"，lite 版负责"批量能点开"，完整版只给重点场次。<br>
  <b>构建任意一场</b>：<code>python analysis/q7_replay.py &lt;match_id&gt; --lite</code> →
  <code>python analysis/build_q7_html.py &lt;match_id&gt; --lite</code>（完整版去掉 <code>--lite</code>）。
  勾选下方"选择"列可生成整批命令。
  时长/胜负在本页由 <b>combat_log 的"号角 → 远古被摧毁"</b> 推出（<b>不依赖 OpenDota</b>）；
  队名/开赛时间/OpenDota 时长来自 <code>stats.db</code>（只覆盖已抓取的联赛，其余留空）。
</div>
<div class="bar">
  <label>搜索 <input type="text" id="q" placeholder="match_id / 队伍 / 英雄 / 联赛" oninput="render()"></label>
  <label>联赛 <select id="lg" onchange="render()"></select></label>
  <label>英雄 <select id="h" onchange="render()"></select></label>
  <label>胜负 <select id="w" onchange="render()">
    <option value="-1">全部</option><option value="1">天辉胜</option><option value="0">夜魇胜</option></select></label>
  <label><input type="checkbox" id="onlyF" onchange="render()"> 只看已构建</label>
  <button class="btn" onclick="clearSel()">清空选择</button>
  <button class="btn" onclick="genCmd()">生成构建命令</button>
  <span id="stat"></span>
</div>
<div id="cmdwrap"><div style="margin-bottom:4px;color:#8b949e">把下面命令粘到项目根目录执行（先 q7_replay 再 build）：</div>
<textarea id="cmd" readonly></textarea></div>
<div style="max-height:74vh;overflow:auto">
<table id="t"><thead><tr>
  <th style="width:26px"><input type="checkbox" id="all" onchange="toggleAll()"></th>
  <th data-k="mid" class="n">match_id</th><th data-k="lg" class="n">联赛</th>
  <th data-k="rt">天辉</th><th data-k="dt">夜魇</th><th data-k="rwin">结果</th>
  <th data-k="dur" class="n">时长</th><th data-k="od" class="n">OpenDota</th><th data-k="start">开赛</th>
  <th>天辉英雄</th><th>夜魇英雄</th><th data-k="built">打开</th>
</tr></thead><tbody id="tb"></tbody></table>
</div>
<script>
"use strict";
const ROWS = @@BLOB@@;
let sortK = "dur", sortDir = -1;
const sel = new Set();
function fmtDur(s) { if (s === null || s === undefined) return "—"; s = Math.round(s); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
function fmtDate(t) { if (!t) return "—"; const d = new Date(t * 1000); return d.toISOString().slice(0, 10); }
function initSel() {
  const lgs = [...new Set(ROWS.map(r => r.lg))].sort((a, b) => a - b);
  const hs = [...new Set(ROWS.flatMap(r => r.h))].sort();
  const L = document.getElementById("lg"), H = document.getElementById("h");
  L.innerHTML = '<option value="">全部</option>' + lgs.map(v => '<option value="' + v + '">' + v + '</option>').join("");
  H.innerHTML = '<option value="">全部</option>' + hs.map(v => '<option value="' + v + '">' + v + '</option>').join("");
}
function filtered() {
  const q = document.getElementById("q").value.trim().toLowerCase();
  const lg = document.getElementById("lg").value;
  const h = document.getElementById("h").value;
  const w = +document.getElementById("w").value;
  const onlyF = document.getElementById("onlyF").checked;
  return ROWS.filter(r => {
    if (lg && String(r.lg) !== lg) return false;
    if (h && !r.h.includes(h)) return false;
    if (w >= 0 && r.rwin !== w) return false;
    if (onlyF && !(r.full || r.lite)) return false;
    if (q) {
      const s = (r.mid + " " + (r.rt || "") + " " + (r.dt || "") + " " + r.lg + " " + r.h.join(" ")).toLowerCase();
      if (s.indexOf(q) < 0) return false;
    }
    return true;
  });
}
function sortRows(a) {
  const k = sortK;
  a.sort((x, y) => {
    let p = x[k], q2 = y[k];
    if (k === "built") { p = x.full * 2 + x.lite; q2 = y.full * 2 + y.lite; }
    if (k === "od") { p = x.od === undefined ? -1 : x.od; q2 = y.od === undefined ? -1 : y.od; }
    if (p === null || p === undefined) p = -1;
    if (q2 === null || q2 === undefined) q2 = -1;
    if (typeof p === "string" || typeof q2 === "string") return sortDir * String(p).localeCompare(String(q2));
    return sortDir * (p - q2);
  });
  return a;
}
function render() {
  const a = sortRows(filtered());
  const tb = document.getElementById("tb");
  let html = "";
  for (const r of a) {
    const link = r.full ? '<a href="q7_replay_' + r.mid + '.html" target="_blank">完整版</a>'
      : (r.lite ? '<a href="q7_replay_' + r.mid + '_lite.html" target="_blank">lite</a>' : '<span class="mut">未构建</span>');
    const tags = (r.full ? '<span class="tag full">完整</span> ' : "") + (r.lite ? '<span class="tag lite">lite</span>' : "");
    html += '<tr><td><input type="checkbox" data-mid="' + r.mid + '"' + (sel.has(r.mid) ? " checked" : "") + ' onchange="toggleOne(this)"></td>'
      + '<td class="n">' + r.mid + '</td><td class="n">' + r.lg + "</td>"
      + '<td class="dr">' + (r.rt || '<span class="mut">—</span>') + "</td>"
      + '<td class="dd">' + (r.dt || '<span class="mut">—</span>') + "</td>"
      + "<td>" + (r.rwin === null ? '<span class="mut">—</span>' : (r.rwin ? '<span class="dr">天辉胜</span>' : '<span class="dd">夜魇胜</span>')) + "</td>"
      + '<td class="n">' + fmtDur(r.dur) + '</td><td class="n mut">' + (r.od ? fmtDur(r.od) : "—") + "</td>"
      + '<td class="mut">' + fmtDate(r.start) + "</td>"
      + '<td class="mut">' + r.hr.slice(0, 5).join(", ") + "</td>"
      + '<td class="mut">' + r.hd.slice(0, 5).join(", ") + "</td>"
      + "<td>" + link + " " + tags + "</td></tr>";
  }
  tb.innerHTML = html || '<tr><td colspan="12" class="mut">没有匹配的场次</td></tr>';
  const nf = a.filter(r => r.full).length, nl = a.filter(r => r.lite).length;
  document.getElementById("stat").textContent = "筛选出 " + a.length + " 场（完整 " + nf + " / lite " + nl + "）";
  document.getElementById("nn").textContent = String(a.length);
}
function toggleOne(el) { const m = +el.getAttribute("data-mid"); if (el.checked) sel.add(m); else sel.delete(m); }
function toggleAll() {
  const on = document.getElementById("all").checked;
  document.querySelectorAll('#tb input[type=checkbox]').forEach(el => { el.checked = on; toggleOne(el); });
}
function clearSel() { sel.clear(); document.getElementById("all").checked = false; render(); }
function genCmd() {
  const a = sortRows(filtered()).map(r => r.mid);
  const pick = sel.size ? [...sel] : a;
  if (!pick.length) return;
  const ids = pick.join(" ");
  const txt = "# lite（0.4~0.6MB/场，推荐批量）\npython analysis/q7_replay.py " + ids + " --lite\n"
    + "python analysis/build_q7_html.py " + ids + " --lite --step 4\n\n"
    + "# 完整版（含 ±10s 全量明细，4~6MB/场）\npython analysis/q7_replay.py " + ids + "\n"
    + "python analysis/build_q7_html.py " + ids + "\n\n"
    + "# 构建后把 output_review 里的 html 拷到 publish_repo 再 push";
  document.getElementById("cmdwrap").style.display = "block";
  document.getElementById("cmd").value = txt;
  document.getElementById("cmd").select();
}
document.querySelectorAll("th[data-k]").forEach(th => th.onclick = () => {
  const k = th.getAttribute("data-k");
  if (sortK === k) sortDir = -sortDir; else { sortK = k; sortDir = -1; }
  render();
});
initSel(); render();
</script></body></html>
"""


def batch(sel, full=False, step=4):
    """sel 形如 league:19719 / ids:a,b / top:20（按时长降序）/ hero:xxx"""
    res = build_index(False)
    ms = res["matches"]
    pick = []
    if sel.startswith("ids:"):
        want = {int(x) for x in sel[4:].split(",") if x.strip()}
        pick = [m for m in ms if m["mid"] in want]
    elif sel.startswith("league:"):
        lg = int(sel[7:])
        pick = [m for m in ms if int(m["league"]) == lg]
    elif sel.startswith("hero:"):
        h = sel[5:]
        pick = [m for m in ms if h in [x[0] for x in m["heroes"]]]
    elif sel.startswith("top:"):
        pick = sorted(ms, key=lambda m: -(m.get("dur") or 0))[: int(sel[4:])]
    elif sel.startswith("spread:"):
        # 每个联赛等间隔取 N 场（确定性：按 match_id 排序后均匀抽样）→ 代表性样本
        n = int(sel[7:])
        by = {}
        for m in ms:
            by.setdefault(int(m["league"]), []).append(m)
        for lg, arr in sorted(by.items()):
            arr.sort(key=lambda m: m["mid"])
            step = max(1, len(arr) // n)
            pick += arr[::step][:n]
    else:
        raise SystemExit("--batch 选择器要形如 league:19719 / ids:1,2 / hero:axe / top:20 / spread:8")
    if not pick:
        raise SystemExit("没有匹配的场次")
    ids = [str(m["mid"]) for m in pick]
    print("批量构建 %d 场（%s）…" % (len(ids), sel))
    for mid in ids:
        cmd = [sys.executable, os.path.join(HERE, "q7_replay.py"), mid]
        if not full:
            cmd.append("--lite")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print("  ✗ replay %s 失败：%s" % (mid, (r.stderr or "")[-200:]))
            continue
        cmd = [sys.executable, os.path.join(HERE, "build_q7_html.py"), mid]
        if not full:
            cmd += ["--lite", "--step", str(step)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print("  " + (r.stdout or r.stderr or "").strip().splitlines()[-1] if (r.stdout or r.stderr) else "  ?")
    print("批量完成")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rescan", action="store_true")
    ap.add_argument("--batch", default=None)
    ap.add_argument("--full", action="store_true", help="批量构建完整版（默认 lite）")
    ap.add_argument("--step", type=int, default=4)
    ap.add_argument("--publish", action="store_true", help="把索引页复制到 publish_repo")
    args = ap.parse_args()

    if args.batch:
        batch(args.batch, args.full, args.step)
    data = build_index(args.rescan)
    html = htm(data)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write(html)
    print("wrote %s  %.2f MB" % (os.path.relpath(OUT, ROOT), os.path.getsize(OUT) / 1e6))
    if args.publish:
        os.makedirs(PUBLISH, exist_ok=True)
        dst = os.path.join(PUBLISH, "q7_index.html")
        open(dst, "w", encoding="utf-8").write(html)
        print("copied →", os.path.relpath(dst, ROOT))


if __name__ == "__main__":
    main()
