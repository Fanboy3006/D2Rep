#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_q7b_index.py — 私人 / 本地录像索引页（Q7B）。

数据源：`matches.db`（catalog，`source='local'`）+ 每场的解析库
`dems/db_full/local/<scope>/<match>.db`。
产出：`analysis/output_review/q7b_index.html`（单文件、双击即开）。

**这个页面只在本地用，不发布到公网** —— 它会列出玩家名，属于个人数据。
（联赛那份索引 `q7_index.html` 才是发布用的。）

用法：
    python analysis/build_q7b_index.py              # 读 catalog
    python analysis/build_q7b_index.py --rescan     # 顺带扫 dems/local/*/，把还没录入的 .dem 也列出来
    python analysis/build_q7b_index.py --open       # 构建完打印文件路径
"""
import argparse
import glob
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import build_q7_html as BQ          # noqa: E402  复用 strip_comments / b64 等
from scheduler import catalog as cat  # noqa: E402

REVIEW = os.path.join(HERE, "output_review")
LOCAL_ROOT = os.path.join(ROOT, "dems", "local")
DBFULL_LOCAL = os.path.join(ROOT, "dems", "db_full", "local")
OUT = os.path.join(REVIEW, "q7b_index.html")


def fmt_size(n):
    if not n:
        return None
    return round(n / 1e6, 1)


def scan_match(db_path):
    """从解析库里读这场的事实：时长（号角→远古被摧毁）、胜负、双方英雄、玩家名、击杀数。"""
    out = {"dur": None, "rwin": None, "hr": [], "hd": [], "players": [], "kills": 0, "err": None}
    if not db_path or not os.path.exists(db_path):
        out["err"] = "库文件不存在"
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT t_cle FROM combat_log WHERE type_category='gamestate' AND value=5 "
                        "LIMIT 1").fetchone()
        horn = float(r["t_cle"]) if r else None
        r = con.execute("SELECT t_cle, target FROM combat_log WHERE type_category='death' "
                        "AND target LIKE '%_fort' ORDER BY t_cle LIMIT 1").fetchone()
        if r and horn is not None:
            out["dur"] = int(round(float(r["t_cle"]) - horn))
            tgt = (r["target"] or "").lower()
            if "badguys" in tgt:          # 夜魇的远古被摧毁 → 天辉胜
                out["rwin"] = 1
            elif "goodguys" in tgt:
                out["rwin"] = 0
        for p in con.execute("SELECT hero_name, team_id, player_name FROM player_identity "
                             "ORDER BY player_slot"):
            short = (p["hero_name"] or "").replace("npc_dota_hero_", "")
            out["players"].append({"h": short, "t": int(p["team_id"]), "n": p["player_name"]})
            (out["hr"] if int(p["team_id"]) == 2 else out["hd"]).append(short)
        out["kills"] = con.execute("SELECT COUNT(*) FROM combat_log WHERE type_category='death' "
                                   "AND is_target_building=0").fetchone()[0]
        con.close()
    except Exception as e:
        out["err"] = "读库失败：%s" % str(e)[:80]
    return out


def built_viewers():
    """output_review 里已生成的本地球迷页面（决定给链接还是给命令）。"""
    full, lite = set(), set()
    for f in os.listdir(REVIEW) if os.path.isdir(REVIEW) else []:
        if not (f.startswith("q7_replay_") and f.endswith(".html")):
            continue
        tag = f[len("q7_replay_"):-len(".html")]
        if tag.endswith("_lite"):
            lite.add(tag[:-5])
        else:
            full.add(tag)
    return full, lite


def unregistered_dems(known):
    """扫 dems/local/*/ 找还没进 catalog 的 .dem（--rescan 用）。"""
    out = []
    for scope in sorted(os.listdir(LOCAL_ROOT)) if os.path.isdir(LOCAL_ROOT) else []:
        d = os.path.join(LOCAL_ROOT, scope)
        if not os.path.isdir(d) or scope == "registered":
            continue
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith(".dem"):
                continue
            mid = f[:-4]
            if mid in known:
                continue
            p = os.path.join(d, f)
            out.append({"mid": mid, "scope": scope, "dem": os.path.relpath(p, ROOT).replace("\\", "/"),
                        "dem_mb": fmt_size(os.path.getsize(p)), "registered": False})
    return out


def build(rescan=False):
    con = cat.connect()
    cat.ensure_schema(con)
    rows = cat.list_rows(con, source="local")
    full, lite = built_viewers()
    out = []
    for r in rows:
        mid = r["match_id"]
        scope = r.get("scope") or "?"
        meta = r.get("metadata") or {}
        db = r.get("db_path")
        s = scan_match(db)
        dem = r.get("dem_path")
        out.append({
            "mid": mid, "scope": scope, "state": r["parse_state"],
            "dur": s["dur"] if s["dur"] is not None else r.get("duration_sec"),
            "rwin": s["rwin"], "hr": s["hr"], "hd": s["hd"],
            "players": s["players"], "kills": s["kills"], "err": s["err"],
            "dem_mb": fmt_size(os.path.getsize(dem)) if dem and os.path.exists(dem) else None,
            "db_mb": fmt_size(os.path.getsize(db)) if db and os.path.exists(db) else None,
            "dem": os.path.relpath(dem, ROOT).replace("\\", "/") if dem else None,
            "db": os.path.relpath(db, ROOT).replace("\\", "/") if db else None,
            "at": r.get("registered_at"), "note": meta.get("note") or "",
            "sha": (meta.get("sha256") or "")[:10], "id_src": meta.get("id_source") or "",
            "full": 1 if mid in full else 0, "lite": 1 if mid in lite else 0,
            "registered": True,
        })
    if rescan:
        out += unregistered_dems({r["match_id"] for r in rows})
    out.sort(key=lambda x: (x["scope"], x["mid"]), reverse=False)
    blob = json.dumps(out, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    # 已生成页面的计数只算**本地场次**（output_review 里还有 60 个联赛页面，别混进来）
    # 注意：未录入的行用的是 "mid" 键（扫目录得来的），已录入的用 "match_id"
    local_mids = {(r.get("match_id") or r.get("mid")) for r in out if r.get("registered")}
    nfull, nlite = len(full & local_mids), len(lite & local_mids)
    html = (HTML_TMPL
            .replace("@@BLOB@@", blob)
            .replace("@@N@@", str(len([r for r in out if r.get("registered")])))
            .replace("@@NUN@@", str(len([r for r in out if not r.get("registered")])))
            .replace("@@NFULL@@", str(nfull))
            .replace("@@NLITE@@", str(nlite))
            .replace("@@WHEN@@", time.strftime("%Y-%m-%d %H:%M")))
    os.makedirs(REVIEW, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(BQ.strip_comments(html))
    print("wrote %s  %.1f KB  （已录入 %d 场 ｜ 未录入 %d 个文件 ｜ 本地页面 完整 %d / lite %d）"
          % (os.path.relpath(OUT, ROOT), os.path.getsize(OUT) / 1024,
             len([r for r in out if r.get("registered")]), len([r for r in out if not r.get("registered")]),
             nfull, nlite))
    return OUT


HTML_TMPL = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>私人录像索引（Q7B）</title>
<style>
body{font-family:-apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:16px 20px 60px}
h1{font-size:18px;margin:0 0 6px}
.sub{color:#8b949e;font-size:12px;line-height:1.75;margin-bottom:10px}
.sub b{color:#79c0ff}.sub code{background:#21262d;padding:1px 4px;border-radius:3px}
.warn{background:#3a2d12;border:1px solid #d29922;color:#f0dda6;border-radius:8px;padding:7px 10px;font-size:12px;margin-bottom:10px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;background:#161b22;border:1px solid #30363d;
     border-radius:8px;padding:8px 10px;margin-bottom:8px;font-size:12px;position:sticky;top:0;z-index:5}
input[type=text],select{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:4px;padding:4px 6px;font-size:12px}
input[type=text]{width:210px}
label{display:inline-flex;align-items:center;gap:4px;color:#8b949e}
.btn{background:#21262d;border:1px solid #30363d;border-radius:12px;padding:4px 10px;cursor:pointer;font-size:12px;color:#e6edf3}
.btn:hover{border-color:#4d5866}
table{border-collapse:collapse;font-size:12px;width:100%}
th,td{padding:3px 6px;border-bottom:1px solid #21262d;text-align:left;white-space:nowrap}
th{background:#21262d;position:sticky;top:52px;cursor:pointer}
th:hover{color:#79c0ff}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr:hover{background:#1f6feb22}
tr.unreg{opacity:.62}
a{color:#58a6ff;text-decoration:none}a:hover{text-decoration:underline}
.dr{color:#4aa564}.dd{color:#d24b4b}
.mut{color:#8b949e}
.tag{display:inline-block;padding:0 5px;border-radius:8px;font-size:10px;line-height:15px;background:#21262d;color:#8b949e}
.tag.full{background:#1f6feb;color:#fff}.tag.lite{background:#2d4f6b;color:#cfe6ff}
.tag.warn{background:#5c4a1f;color:#f0dda6}
#cmdwrap{display:none;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 10px;margin:8px 0;font-size:12px}
.chip2{display:inline-block;padding:1px 6px;margin:1px 3px 1px 0;border-radius:9px;font-size:11px;
       background:#21262d;color:#cfe6ff;text-decoration:none}
.chip2:hover{background:#1f6feb;color:#fff}
#cmd{width:100%;height:76px;background:#0d1117;color:#7ee787;border:1px solid #30363d;border-radius:4px;font-family:ui-monospace,Consolas,monospace;font-size:11px}
</style></head><body>
<h1>私人录像索引 —— 本地录像 <span id="nn">@@N@@</span> 场<span id="nun"></span></h1>
<div class="warn"><b>这一页会列出玩家名</b>（个人数据）。链接是发给朋友的，<b>别往公开场合贴</b>。
联赛场次的回放索引在这里：<a href="https://bigfatblackwhale.github.io/DSH-Dota2/q7_index.html" target="_blank">q7_index.html</a>。</div>
<div class="sub">
  <b>只想看比赛</b>：直接点下面"已生成页面"里的芯片，或表格里"打开"。
  <br><b>加新录像（自己的机器上）</b>：把 <code>&lt;match_id&gt;.dem</code> 放进
  <code>dems/local/&lt;scope&gt;/</code>（scope 是分组名，如 scrim / team_ts / 2026q1），然后
  <code>python scheduler/intake_local.py --scope &lt;名&gt;</code> 录入解析，最后
  <code>python analysis/build_q7b_index.py --rescan --publish</code> 刷新并发布本页。
  <br>「时长 / 结果」由<b>录像本身</b>推出：号角 → 远古被摧毁，被摧毁的是哪一方的远古就判哪一方输
  （本地场次没有外部战绩可比对，所以这里不写"已核对"）。
  已生成：<span class="tag full">完整 @@NFULL@@</span>
  <span class="tag lite">lite @@NLITE@@</span>。索引生成于 @@WHEN@@。
  <br>想发给朋友：把表格里"打开"的链接发过去就行（单文件页面，点开即看）。
</div>
<div class="bar" style="background:#1c2333;border-color:#1f6feb">
  <label><b style="color:#79c0ff">已生成页面</b></label><span id="chips"></span>
</div>
<div class="bar">
  <label>搜索 <input type="text" id="q" placeholder="match_id / 英雄 / 玩家 / 备注 / scope" oninput="render()"></label>
  <label>scope <select id="sc" onchange="render()"></select></label>
  <label>结果 <select id="w" onchange="render()">
    <option value="-1">全部</option><option value="1">天辉胜</option><option value="0">夜魇胜</option></select></label>
  <label><input type="checkbox" id="onlyB" onchange="render()"> 只看已生成页面</label>
  <label><input type="checkbox" id="onlyR" checked onchange="render()"> 只看已录入（解析完成）</label>
  <button class="btn" onclick="clearSel()">清空选择</button>
  <button class="btn" onclick="genCmd()">生成命令</button>
  <span id="stat" class="mut"></span>
</div>
<div id="cmdwrap"><div style="margin-bottom:4px;color:#8b949e">把下面命令粘到项目根目录执行：</div>
<textarea id="cmd" readonly></textarea></div>
<div style="max-height:72vh;overflow:auto">
<table id="t"><thead><tr>
  <th style="width:26px"><input type="checkbox" id="all" onchange="toggleAll()"></th>
  <th data-k="mid" class="n">match_id</th><th data-k="scope">scope</th>
  <th data-k="dur" class="n">时长</th><th data-k="rwin">结果</th>
  <th data-k="kills" class="n">击杀</th>
  <th>天辉英雄</th><th>夜魇英雄</th>
  <th data-k="dem_mb" class="n">录像</th><th data-k="db_mb" class="n">库</th>
  <th data-k="at">录入</th><th>备注</th><th data-k="built">打开</th>
</tr></thead><tbody id="tb"></tbody></table>
</div>
<script>
"use strict";
const ROWS = @@BLOB@@;
let sortK = "at", sortDir = -1;
const sel = new Set();
function builtRows() { return ROWS.filter(r => r.full || r.lite); }
function renderChips() {
  const box = document.getElementById("chips");
  const list = builtRows().sort((a, b) => (b.full - a.full) || String(a.mid).localeCompare(String(b.mid)));
  box.innerHTML = list.length ? list.map(r => {
    const href = "q7_replay_" + r.mid + (r.full ? "" : "_lite") + ".html";
    const tag = r.full ? "完整" : "lite";
    return '<a href="' + href + '" target="_blank" class="chip2" title="' + (r.scope || "") + ' ｜ ' + tag
      + ' ｜ ' + fmtDur(r.dur) + ' ｜ ' + winTxt(r) + '">' + r.mid + " · " + tag + "</a>";
  }).join(" ") : '<span class="mut">还没有生成过页面（下面表格里点"命令"）</span>';
}
function fmtDur(s) { if (s === null || s === undefined) return "—"; s = Math.round(s); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
function winTxt(r) {
  if (r.rwin === null || r.rwin === undefined) return '<span class="mut">—</span>';
  return r.rwin ? '<span class="dr">天辉胜</span>' : '<span class="dd">夜魇胜</span>';
}
function initSel() {
  const scs = [...new Set(ROWS.map(r => r.scope))].sort();
  document.getElementById("sc").innerHTML = '<option value="">全部</option>'
    + scs.map(v => '<option value="' + v + '">' + v + '</option>').join("");
}
function filtered() {
  const q = document.getElementById("q").value.trim().toLowerCase();
  const sc = document.getElementById("sc").value;
  const w = +document.getElementById("w").value;
  const onlyB = document.getElementById("onlyB").checked;
  const onlyR = document.getElementById("onlyR").checked;
  return ROWS.filter(r => {
    if (sc && r.scope !== sc) return false;
    if (w >= 0 && r.rwin !== w) return false;
    if (onlyB && !(r.full || r.lite)) return false;
    if (onlyR && !r.registered) return false;
    if (q) {
      const hay = [r.mid, r.scope, r.note, r.state, (r.hr || []).join(" "), (r.hd || []).join(" "),
                   (r.players || []).map(p => p.h + " " + (p.n || "")).join(" ")].join(" ").toLowerCase();
      if (hay.indexOf(q) < 0) return false;
    }
    return true;
  });
}
function sortRows(a) {
  const k = sortK;
  a.sort((x, y) => {
    let p = x[k], q2 = y[k];
    if (k === "built") { p = (x.full || 0) * 2 + (x.lite || 0); q2 = (y.full || 0) * 2 + (y.lite || 0); }
    if (p === null || p === undefined || p === "") p = -1;
    if (q2 === null || q2 === undefined || q2 === "") q2 = -1;
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
    const open = r.full ? '<a href="q7_replay_' + r.mid + '.html" target="_blank"><b>打开(完整)</b></a>'
      : (r.lite ? '<a href="q7_replay_' + r.mid + '_lite.html" target="_blank"><b>打开(lite)</b></a>'
                : '<button class="btn" style="padding:2px 8px" onclick="cmdFor(\'' + r.mid + '\')">命令</button>');
    const tags = (r.full ? '<span class="tag full">完整</span> ' : "")
      + (r.lite ? '<span class="tag lite">lite</span> ' : "")
      + (!r.registered ? '<span class="tag warn">未录入</span>' : (r.state !== "parsed" ? '<span class="tag warn">' + r.state + "</span>" : ""));
    const pl = (r.players || []).map(p => p.h + "(" + (p.n || "?") + ")").join(" ｜ ");
    html += '<tr class="' + (r.registered ? "" : "unreg") + '">'
      + '<td><input type="checkbox" data-mid="' + r.mid + '"' + (sel.has(r.mid) ? " checked" : "")
      + ' onchange="toggleOne(this)"></td>'
      + '<td class="n">' + r.mid + "</td>"
      + '<td class="mut">' + (r.scope || "—") + "</td>"
      + '<td class="n">' + fmtDur(r.dur) + "</td>"
      + "<td>" + winTxt(r) + "</td>"
      + '<td class="n">' + (r.kills || "—") + "</td>"
      + '<td class="mut" title="' + pl + '">' + (r.hr || []).join(", ") + "</td>"
      + '<td class="mut" title="' + pl + '">' + (r.hd || []).join(", ") + "</td>"
      + '<td class="n mut">' + (r.dem_mb === null || r.dem_mb === undefined ? "—" : r.dem_mb + " MB") + "</td>"
      + '<td class="n mut">' + (r.db_mb === null || r.db_mb === undefined ? "—" : r.db_mb + " MB") + "</td>"
      + '<td class="mut">' + (r.at || "").slice(0, 16) + "</td>"
      + '<td class="mut">' + esc(r.note || "") + (r.err ? ' <span class="dd">' + esc(r.err) + "</span>" : "") + "</td>"
      + "<td>" + open + " " + tags + "</td></tr>";
  }
  tb.innerHTML = html || '<tr><td colspan="13" class="mut">没有匹配的场次</td></tr>';
  const nb = a.filter(r => r.full || r.lite).length;
  document.getElementById("stat").textContent = "筛选出 " + a.length + " 场（已生成页面 " + nb + "）";
}
function esc(s) { return String(s === undefined || s === null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function toggleOne(el) { const m = el.getAttribute("data-mid"); if (el.checked) sel.add(m); else sel.delete(m); }
function toggleAll() {
  const on = document.getElementById("all").checked;
  document.querySelectorAll("#tb input[type=checkbox]").forEach(el => { el.checked = on; toggleOne(el); });
}
function clearSel() { sel.clear(); document.getElementById("all").checked = false; render(); }
function oneCmd(r) {
  return "# " + r.mid + "（scope=" + (r.scope || "?") + "）\n"
    + (r.registered ? "" : "python scheduler/intake_local.py --scope " + (r.scope || "inbox") + "\n")
    + "python analysis/q7_replay.py " + r.mid + "\n"
    + "python analysis/build_q7_html.py " + r.mid + " --out analysis/output_review\n"
    + "# lite（0.5MB，便于发人）\n"
    + "python analysis/build_q7_html.py " + r.mid + " --lite --step 4 --out analysis/output_review";
}
function cmdFor(mid) {
  const r = ROWS.find(x => String(x.mid) === String(mid));
  if (!r) return;
  const txt = (sel.size ? [...sel].map(m => oneCmd(ROWS.find(x => String(x.mid) === m))).join("\n\n")
                        : oneCmd(r))
    + "\n\n# 索引刷新\npython analysis/build_q7b_index.py";
  document.getElementById("cmdwrap").style.display = "block";
  document.getElementById("cmd").value = txt;
  document.getElementById("cmd").select();
}
function genCmd() { cmdFor(sel.size ? [...sel][0] : (filtered()[0] || {}).mid); }
document.querySelectorAll("th[data-k]").forEach(th => {
  th.onclick = function () {
    const k = th.getAttribute("data-k");
    if (sortK === k) sortDir = -sortDir; else { sortK = k; sortDir = -1; }
    render();
  };
});
document.getElementById("nun").innerHTML = @@NUN@@ ? '（另有 <b>@@NUN@@</b> 个录像文件还没录入 → 下面标"未录入"）' : "";
initSel(); renderChips(); render();
</script>
</body></html>
"""


def publish(rows):
    """把索引页 + 它引用的本地 viewer 拷到 publish_repo/q7b/（站点上的 Q7B 目录）。

    站点结构：`publish_repo/q7b/index.html`（本索引）+ `q7b/q7_replay_<mid>[_lite].html`。
    索引里的链接是相对路径，所以放同一个目录就能直接点开。
    """
    dst = os.path.join(ROOT, "publish_repo", "q7b")
    os.makedirs(dst, exist_ok=True)
    n = 0
    shutil.copyfile(OUT, os.path.join(dst, "index.html"))
    for r in rows:
        if not r.get("registered"):
            continue
        for suffix in (("", "_lite") if r.get("full") and r.get("lite") else
                       (("",) if r.get("full") else (("_lite",) if r.get("lite") else ()))):
            f = os.path.join(REVIEW, "q7_replay_%s%s.html" % (r["mid"], suffix))
            if os.path.exists(f):
                shutil.copyfile(f, os.path.join(dst, os.path.basename(f)))
                n += 1
    print("published → %s（索引 + %d 个页面）" % (os.path.relpath(dst, ROOT), n))
    print("公网地址：https://bigfatblackwhale.github.io/DSH-Dota2/q7b/index.html")
    print("记得 push：git -C publish_repo add -A; git -C publish_repo commit -m \"...\"; "
          "git -C publish_repo push origin main")


def main():
    ap = argparse.ArgumentParser(description="Q7B 私人录像索引页生成器")
    ap.add_argument("--rescan", action="store_true", help="顺带扫 dems/local/*/ 把未录入的 .dem 也列出来")
    ap.add_argument("--publish", action="store_true",
                    help="同时拷到 publish_repo/q7b/（站点目录；索引与本地 viewer 一起）")
    ap.add_argument("--open", action="store_true", help="构建完打印文件路径")
    args = ap.parse_args()
    out = build(rescan=args.rescan)
    if args.publish:
        con = cat.connect()
        cat.ensure_schema(con)
        rows = cat.list_rows(con, source="local")
        full, lite = built_viewers()
        payload = [{"mid": r["match_id"], "registered": True,
                    "full": 1 if r["match_id"] in full else 0,
                    "lite": 1 if r["match_id"] in lite else 0} for r in rows]
        publish(payload)
    if args.open:
        print(out)


if __name__ == "__main__":
    main()
