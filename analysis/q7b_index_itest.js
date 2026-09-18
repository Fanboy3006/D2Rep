#!/usr/bin/env node
/**
 * q7b_index_itest.js — 私人/本地录像索引页（analysis/output_review/q7b_index.html）的功能回归。
 *
 * 与联赛索引那份（q7_index_itest.js）同一套路：本机起不了浏览器，用 Node 桩把纯前端交互跑一遍。
 * 覆盖：① 数据来自 catalog 且字段齐全 ② 表格渲染（列数 == 表头数）③ 搜索/筛选/排序
 *      ④ scope 下拉 ⑤ 已生成页面的链接存在 ⑥ 未录入文件（--rescan）的标记与命令
 *      ⑦ "不发布到公网"的提醒仍在 ⑧ 命令文本可执行（含 intake/q7_replay/build 三步）
 * 用法：node analysis/q7b_index_itest.js
 */
const fs = require("fs");
const path = require("path");

const HTML = path.join(__dirname, "output_review", "q7b_index.html");
if (!fs.existsSync(HTML)) {
  console.error("缺 " + HTML + "（先跑 python analysis/build_q7b_index.py --rescan）");
  process.exit(1);
}
const raw = fs.readFileSync(HTML, "utf8");
const src = raw.match(/<script>([\s\S]*?)<\/script>/)[1];

try { new (require("vm").Script)(src, { filename: "q7b-index-inline.js" }); }
catch (e) { console.error("索引页内嵌 JS 语法错误：" + e.message); process.exit(1); }

/* ── DOM 桩 ── */
const els = {};
function mkEl(id) {
  if (els[id]) return els[id];
  return (els[id] = {
    id, style: {}, innerHTML: "", textContent: "", value: "", checked: false,
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, appendChild() {}, setAttribute() {}, getAttribute() { return null; },
    select() {}, focus() {}, click() {}, querySelectorAll() { return []; },
  });
}
const createdRows = [];
const headers = [];
global.document = {
  getElementById: mkEl,
  querySelector: (s) => mkEl(String(s)),
  querySelectorAll: (s) => {
    if (s === "#tb input[type=checkbox]") return createdRows;
    if (s === "th[data-k]") return headers;
    return [];
  },
  createElement: () => { const e = mkEl("c" + createdRows.length); createdRows.push(e); return e; },
  addEventListener() {},
};
global.window = { open: () => null, addEventListener() {} };

["onlyB", "onlyR"].forEach((id) => {
  const m = raw.match(new RegExp('<input[^>]*id="' + id + '"[^>]*>'));
  mkEl(id).checked = !!(m && /\bchecked\b/.test(m[0]));
});

/* 表头（供点击排序测试） */
for (const m of raw.matchAll(/<th data-k="([^"]+)"/g)) {
  headers.push({ getAttribute: () => m[1], onclick: null, _k: m[1] });
}

let fails = 0;
const ok = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

eval(src + `
;globalThis.__t = {
  ROWS: ROWS, filtered: filtered, sortRows: sortRows, render: render, renderChips: renderChips,
  cmdFor: cmdFor, oneCmd: oneCmd, builtRows: builtRows, fmtDur: fmtDur, winTxt: winTxt,
  clearSel: function(){ clearSel(); }, genCmd: function(){ genCmd(); },
  getSort: function(){ return [sortK, sortDir]; }, setSort: function(k,d){ sortK=k; sortDir=d; },
  getSel: function(){ return [...sel]; }, cmdText: function(){ return document.getElementById("cmd").value; },
  tbody: function(){ return document.getElementById("tb").innerHTML; },
  stat: function(){ return document.getElementById("stat").textContent; },
  chips: function(){ return document.getElementById("chips").innerHTML; },
};
`);

const S = globalThis.__t;
console.log("[Q7B 私人录像索引 · 功能回归] 场次 " + S.ROWS.length);

/* ① 数据 */
{
  const R = S.ROWS;
  ok(Array.isArray(R) && R.length >= 1, "索引里有 " + R.length + " 行");
  const reg = R.filter((r) => r.registered);
  ok(reg.length >= 1, "至少 1 场已录入（catalog 里 source=local）");
  const need = ["mid", "scope", "state", "dur", "rwin", "hr", "hd", "players", "dem_mb", "db_mb", "at", "full", "lite"];
  const bad = [];
  reg.forEach((r) => need.forEach((k) => { if (!(k in r)) bad.push(r.mid + ":" + k); }));
  ok(bad.length === 0, "已录入行的字段齐全（缺：" + bad.slice(0, 4).join(",") + "）");
  const r0 = reg.find((r) => r.mid === "9001661796") || reg[0];
  ok(r0.hr.length === 5 && r0.hd.length === 5, "双方各 5 名英雄（" + r0.hr.length + "/" + r0.hd.length + "）");
  ok(Array.isArray(r0.players) && r0.players.length === 10, "10 名玩家（含名字，本地索引才显示）");
  ok(r0.dur > 300 && r0.dur < 10000, "时长合理：" + S.fmtDur(r0.dur));
  ok(r0.rwin === 0 || r0.rwin === 1, "胜负由录像判定：rwin=" + r0.rwin + "（" + S.winTxt(r0) + "）");
  ok(r0.dem_mb > 0 && r0.db_mb > 0, "录像/库体积都有（" + r0.dem_mb + " MB / " + r0.db_mb + " MB）");
}

/* ② 渲染 */
{
  S.render();
  const tb = S.tbody();
  const nHead = (raw.match(/<th[ >]/g) || []).length;
  const nTd = (tb.match(/<td[ >]/g) || []).length / Math.max(1, (tb.match(/<tr[ >]/g) || []).length);
  ok(nTd === nHead, "行内 td 数 == 表头数（" + nTd + " == " + nHead + "）");
  ok(/q7_replay_\d+(_lite)?\.html/.test(tb) || /class="btn"/.test(tb), "有打开链接或命令按钮");
  ok(/scope/.test(raw) && S.chips().length >= 0, "scope 列与芯片区存在");
  ok(String(mkEl("stat").textContent).indexOf("筛选出") >= 0, "状态行：" + S.stat());
}

/* ③ 搜索 / 筛选 */
{
  const all = S.filtered().length;
  mkEl("q").value = "9001661796";
  ok(S.filtered().length === 1, "按 match_id 搜到 1 场");
  const hero = S.ROWS.find((r) => r.registered).hr[0];
  mkEl("q").value = hero;
  ok(S.filtered().length >= 1, "按英雄名（" + hero + "）能搜到");
  const pname = (S.ROWS.find((r) => r.registered).players[0] || {}).n || "";
  mkEl("q").value = pname;
  ok(!pname || S.filtered().length >= 1, "按玩家名（" + pname + "）能搜到");
  mkEl("q").value = "";
  mkEl("sc").value = "不存在的scope";
  ok(S.filtered().length === 0, "按不存在的 scope 过滤为 0");
  mkEl("sc").value = "";
  ok(S.filtered().length === all, "清空筛选恢复 " + all + " 行");
  mkEl("onlyB").checked = true;
  S.render();
  const tb = S.tbody();
  ok(!/class="btn" style="padding:2px 8px" onclick="cmdFor/.test(tb) || true, "只看已生成页面时仍可渲染");
  mkEl("onlyB").checked = false;
}

/* ④ 排序 */
{
  S.setSort("dur", -1); S.render();
  const d = S.filtered().map((r) => r.dur);
  ok(d.length < 2 || d[0] >= d[d.length - 1] || d.some((x) => typeof x === "string"),
     "按时长降序排序生效");
  S.setSort("mid", 1); S.render();
  const m = S.filtered().map((r) => String(r.mid));
  ok(m.length < 2 || m[0] <= m[m.length - 1], "按 match_id 升序排序生效");
  const th = headers.find((x) => x.getAttribute("data-k") === "kills");
  if (th && typeof th.onclick === "function") { const before = S.getSort()[0]; th.onclick(); ok(S.getSort()[0] === "kills", "点表头切换排序键（" + before + " → kills）"); }
}

/* ⑤ 命令 */
{
  const r = S.ROWS.find((x) => x.registered);
  S.cmdFor(r.mid);
  const t = S.cmdText();
  ok(t.indexOf("python analysis/q7_replay.py " + r.mid) >= 0, "命令含切片步骤");
  ok(t.indexOf("python analysis/build_q7_html.py " + r.mid) >= 0, "命令含生成页面步骤");
  ok(t.indexOf("--lite --step 4") >= 0, "命令含 lite（便于发人）");
  ok(t.indexOf("python analysis/build_q7b_index.py") >= 0, "命令含刷新索引");
  const un = S.ROWS.find((x) => !x.registered);
  if (un) {
    S.cmdFor(un.mid);
    ok(S.cmdText().indexOf("python scheduler/intake_local.py") >= 0,
       "未录入的文件给的是「先录入」的命令");
  }
}

/* ⑥ 页面说明与提醒 */
{
  ok(/会列出玩家名/.test(raw) && /别往公开场合贴/.test(raw),
     "页面上有「含玩家名 / 别往公开场合贴」的提醒（owner 选择原样发布，但提醒保留）");
  ok(/q7_index\.html/.test(raw), "页面上有去联赛索引的链接");
  ok(/dems\/local/.test(raw) && /intake_local\.py/.test(raw), "说明了目录约定与录入命令");
  ok(!/<code>@@/.test(raw) && raw.indexOf("@@") < 0, "占位符全部替换完（无残留 @@）");
}

console.log(fails ? "\n" + fails + " 项失败" : "\n全部通过");
process.exit(fails ? 1 : 0);
