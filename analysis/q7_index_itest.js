#!/usr/bin/env node
/**
 * q7_index_itest.js — Q7 索引页（analysis/output_review/q7_index.html）的功能回归。
 *
 * 为什么需要：owner 反馈"索引看起来没法直接选取"。索引页是纯前端（970 行内嵌 JSON + 搜索/筛选/跳转/
 * 生成命令），本机起不了浏览器，所以沿用 Q6/Q7 viewer 的 Node 桩做法把交互跑一遍。
 *
 * 覆盖：① 顶部"直接打开"（已构建→打开；未构建→给命令）② 默认只显示可点开的 ③ 筛选/排序/搜索
 *      ④ 芯片列表 == 已构建集合 ⑤ 整批命令生成 ⑥ 表头列数 == 行内 td 数
 * 用法：node analysis/q7_index_itest.js
 */
const fs = require("fs");
const path = require("path");

const HTML = path.join(__dirname, "output_review", "q7_index.html");
const raw = fs.readFileSync(HTML, "utf8");
const src = raw.match(/<script>([\s\S]*?)<\/script>/)[1];

/* 先做语法检查（内嵌 JS 写错会整页白屏） */
try { new (require("vm").Script)(src, { filename: "index-inline.js" }); }
catch (e) { console.error("索引页内嵌 JS 语法错误：" + e.message); process.exit(1); }

/* ── DOM 桩 ── */
const els = {};
const opened = [];
function mkEl(id) {
  if (els[id]) return els[id];
  const e = {
    id, style: {}, dataset: {}, innerHTML: "", textContent: "", value: "", checked: false,
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, appendChild() {}, setAttribute() {}, getAttribute() { return null; },
    select() {}, focus() {}, click() {},
    querySelectorAll() { return []; },
  };
  return (els[id] = e);
}
const createdRows = [];
global.document = {
  getElementById: mkEl,
  querySelector: (s) => mkEl(String(s)),
  querySelectorAll: (s) => (s === "#tb input[type=checkbox]" ? createdRows : []),
  createElement: () => { const e = mkEl("c" + createdRows.length); createdRows.push(e); return e; },
  addEventListener() {},
};
global.window = { open: (u) => { opened.push(u); return null; }, addEventListener() {} };

/* 初始 checked 照 HTML 属性同步（桩不解析 HTML） */
["onlyF"].forEach((id) => {
  const m = raw.match(new RegExp('<input[^>]*id="' + id + '"[^>]*>'));
  mkEl(id).checked = !!(m && /\bchecked\b/.test(m[0]));
});

let fails = 0;
const ok = (cond, msg) => { console.log((cond ? "  ok   " : "  FAIL ") + msg); if (!cond) fails++; };

eval(src + `
;globalThis.__t = {
  ROWS: ROWS, filtered: filtered, sortRows: sortRows, render: render, renderChips: renderChips,
  jump: jump, cmdFor: cmdFor, oneCmd: oneCmd, builtRows: builtRows,
  clearSel: function(){ clearSel(); }, genCmd: function(){ genCmd(); },
  getSort: function(){ return [sortK, sortDir]; }, setSort: function(k,d){ sortK=k; sortDir=d; },
  getSel: function(){ return [...sel]; }, cmdText: function(){ return document.getElementById("cmd").value; },
};
`);

const S = globalThis.__t;
const ROWS = S.ROWS;
console.log("[Q7 索引页 · 功能回归] 行数 " + ROWS.length);

/* ① 数据完整性 */
{
  ok(ROWS.length > 900, "索引覆盖 " + ROWS.length + " 场");
  ok(ROWS.every(r => r.mid && r.lg !== undefined), "每行都有 match_id 与联赛");
  ok(ROWS.every(r => r.rwin === 0 || r.rwin === 1), "每行都有胜负（由远古被摧毁推出）");
  ok(ROWS.every(r => r.dur === null || r.dur > 60), "每行时长都 > 60s（或空）");
  ok(ROWS.every(r => r.hr.length + r.hd.length === 10), "每行 5+5 = 10 名英雄");
  const built = S.builtRows();
  const nf = built.filter(r => r.full).length, nl = built.filter(r => !r.full).length;
  ok(built.length === nf + nl && built.length > 10,
     "已构建（可点开）= " + built.length + " 场（" + nf + " 完整 + " + nl + " 仅 lite）");
  ok(built.every(r => r.full || r.lite), "已构建集合与标记一致");
}

/* ② 默认"只看已构建" */
{
  mkEl("q").value = ""; mkEl("lg").value = ""; mkEl("h").value = ""; mkEl("w").value = "-1";
  mkEl("onlyF").checked = true;
  const f = S.filtered();
  ok(f.length === S.builtRows().length, "默认勾选『只看已构建』→ 筛选出 " + f.length + " 场（= 可点开的场次）");
  ok(f.every(r => r.full || r.lite), "筛出来的每一行都能点开");
  mkEl("onlyF").checked = false;
  ok(S.filtered().length === ROWS.length, "取消勾选 → 全部 " + S.filtered().length + " 场");
  mkEl("onlyF").checked = true;
}

/* ③ 顶部"直接打开" */
{
  const builtFull = ROWS.find(r => r.full);
  const builtLite = ROWS.find(r => r.lite && !r.full);
  console.log("     完整版样例 " + builtFull.mid + " ｜ lite 样例 " + builtLite.mid);
  opened.length = 0;
  mkEl("jump").value = String(builtFull.mid); S.jump();
  ok(opened.length === 1 && opened[0] === "q7_replay_" + builtFull.mid + ".html",
     "输入完整版 match_id → 打开 " + opened[0]);
  opened.length = 0;
  mkEl("jump").value = "  " + builtLite.mid + "  "; S.jump();
  ok(opened.length === 1 && opened[0] === "q7_replay_" + builtLite.mid + "_lite.html",
     "输入 lite match_id（带空格也能识别）→ 打开 " + opened[0]);
  // 未构建：不打开，改为填命令
  const unbuilt = ROWS.find(r => !r.full && !r.lite);
  opened.length = 0;
  mkEl("jump").value = String(unbuilt.mid); S.jump();
  ok(opened.length === 0, "未构建的 match_id → 不打开（不会白开一个 404）");
  ok(S.cmdText().indexOf(String(unbuilt.mid)) >= 0 && /q7_replay\.py/.test(S.cmdText()),
     "未构建 → 命令框已填好该场命令");
  ok(/还没构建/.test(String(mkEl("jumpmsg").textContent)), "给出明确提示：" + mkEl("jumpmsg").textContent);
  // 非法输入 / 不存在的场次
  mkEl("jump").value = "abc"; S.jump();
  ok(/请输入 match_id/.test(String(mkEl("jumpmsg").textContent)), "非法输入有提示");
  mkEl("jump").value = "1234567890"; S.jump();
  ok(/没有这一场/.test(String(mkEl("jumpmsg").textContent)), "970 场之外有提示");
}

/* ④ 芯片列表 == 已构建集合 */
{
  S.renderChips();
  const chips = String(mkEl("chips").innerHTML);
  const mids = [...chips.matchAll(/q7_replay_(\d+)(_lite)?\.html/g)].map(m => m[1]);
  const want = S.builtRows().map(r => String(r.mid));
  ok(mids.length === want.length && want.every(m => mids.includes(m)),
     "芯片数 == 已构建数（" + mids.length + "）且 match_id 一致");
  ok(/cf/.test(chips), "完整版芯片有高亮样式");
}

/* ⑤ 渲染出的表格结构 */
{
  S.render();
  const html = String(mkEl("tb").innerHTML);
  const trs = html.split("<tr>").length - 1;
  ok(trs === S.builtRows().length, "表格行数 == 默认筛选结果（" + trs + "）");
  const tdInRow = (html.split("</tr>")[0].match(/<td[\s>]/g) || []).length;
  const thCount = (raw.match(/<th[\s>]/g) || []).length;   // 注意 <thead> 也会被 <th[^>]*> 误匹配
  ok(tdInRow === thCount, "行内 td 数 == 表头 th 数（" + tdInRow + " == " + thCount + "）");
  ok(html.indexOf("打开(lite)") > 0 || html.indexOf("打开(完整)") > 0, "已构建行给的是『打开』链接");
  // 全部显示时未构建行要有"命令"按钮
  mkEl("onlyF").checked = false; S.render();
  const html2 = String(mkEl("tb").innerHTML);
  ok(/onclick="cmdFor\(\d+\)"/.test(html2), "未构建行提供『命令』按钮（不再是死胡同）");
  mkEl("onlyF").checked = true;
}

/* ⑥ 搜索 / 筛选 / 排序 */
{
  mkEl("onlyF").checked = false;
  const axe = ROWS.find(r => r.h.includes("axe"));
  mkEl("q").value = "axe";
  ok(S.filtered().length > 0 && S.filtered().every(r => r.h.includes("axe")),
     "按关键词 axe 搜到 " + S.filtered().length + " 场，且都含 axe");
  mkEl("q").value = String(axe.mid);
  ok(S.filtered().length === 1 && S.filtered()[0].mid === axe.mid, "按 match_id 搜到唯一一场");
  mkEl("q").value = "";
  mkEl("w").value = "1";
  ok(S.filtered().every(r => r.rwin === 1), "按『天辉胜』筛选后每行都是天辉胜（" + S.filtered().length + " 场）");
  mkEl("w").value = "-1";
  mkEl("lg").value = String(ROWS[0].lg);
  ok(S.filtered().every(r => String(r.lg) === String(ROWS[0].lg)), "按联赛筛选生效");
  mkEl("lg").value = "";
  S.setSort("dur", -1);
  const a = S.sortRows(S.filtered()).map(r => r.dur || -1);
  ok(a.every((v, i) => i === 0 || a[i - 1] >= v), "按时长降序排序正确（首 " + a[0] + "s，末 " + a[a.length - 1] + "s）");
  S.setSort("built", -1); mkEl("onlyF").checked = true;
}

/* ⑦ 整批命令生成 */
{
  mkEl("q").value = ""; mkEl("lg").value = ""; mkEl("h").value = ""; mkEl("w").value = "-1";
  S.clearSel();
  S.genCmd();
  const t = S.cmdText();
  ok(/--lite/.test(t) && /q7_replay\.py/.test(t) && /build_q7_html\.py/.test(t), "整批命令含 lite 链路");
  const ids = (t.match(/\d{7,}/g) || []);
  ok(ids.length >= S.builtRows().length, "整批命令覆盖当前筛选的 " + ids.length + " 个 match_id");
}

console.log(fails ? "\n" + fails + " 项失败" : "\n全部通过");
process.exit(fails ? 1 : 0);
