#!/usr/bin/env node
/**
 * q7_viewer_itest.js — Q7 回放浏览器（analysis/output_review/q7_replay_<match>.html）交互回归测试。
 *
 * 为什么需要它：本机沙箱里起不了 Chrome（Q6 文档已记录），而 Q5B 当年正是死在 JS 交互上（白屏）。
 * 所以沿用 Q6 的 Node 桩做法，把真实事件序列跑一遍：数据自洽 / 双时间轴语义（松手提交+瞬时归零）/
 * 缩放平移钳制 / 点英雄选中 / 播放推进 / 连续刷新不出现 NaN。
 *
 * 用法： node analysis/q7_viewer_itest.js [match_id]
 * 退出码：0 = 全部通过；1 = 有断言失败
 */
const fs = require("fs");
const path = require("path");

const MID = process.argv[2] || "8955197224";
const LITE = process.env.Q7_LITE === "1";
const HTML = path.join(__dirname, "output_review",
                        "q7_replay_" + MID + (LITE ? "_lite" : "") + ".html");
if (!fs.existsSync(HTML)) { console.error("缺 " + HTML + "（先跑 build_q7_html.py）"); process.exit(1); }
const raw = fs.readFileSync(HTML, "utf8");
const src = raw.match(/<script>([\s\S]*?)<\/script>/)[1];

/* ★ 先做语法检查：内嵌 JS 一旦有引号/括号写错，浏览器会整页白屏（本会话已踩过一次）。
   用 vm.Script 在进程内编译（沙箱里 spawn 子进程 + 管道 stdio 会 EPERM，不能用 node --check）。 */
{
  try {
    new (require("vm").Script)(src, { filename: "viewer-inline.js" });
  } catch (e) {
    console.error("内嵌 JS 语法错误（浏览器会白屏）：" + (e && e.message));
    process.exit(1);
  }
}

/* ── DOM / Canvas 桩 ── */
const calls = { arc: 0, fillRect: 0, fillText: 0, drawImage: 0, stroke: 0 };
const drawImgs = [];
const mkctx = () => new Proxy({}, {
  get: (t, k) => {
    if (k === "measureText") return () => ({ width: 20 });
    if (k === "canvas") return { width: 1024, height: 1024 };
    return (...a) => { if (k in calls) calls[k]++; if (k === "drawImage") drawImgs.push(a.slice()); };
  },
  set: () => true,
});
const els = {};
function mkEl(id) {
  if (els[id]) return els[id];
  const cls = new Set();
  const e = {
    id, style: {}, dataset: {}, textContent: "", title: "",
    _v: "0", _cn: "", _ih: "", checked: true, _h: {},
    addEventListener(t, f) { (e._h[t] = e._h[t] || []).push(f); },
    children: [],
    appendChild(c) { e.children.push(c); }, removeChild() {},
    querySelector() { return mkEl(id + "_q"); },
    querySelectorAll() { return []; },
    getContext() { return mkctx(); },
    getBoundingClientRect() { return { left: 0, top: 0, width: 1024, height: 1024 }; },    setAttribute(k, v) { e["_attr_" + k] = v; },
    getAttribute(k) { return e["_attr_" + k]; },
    focus() {}, blur() {}, click() { if (e.onclick) e.onclick({}); },
  };
  /* classList 真的维护 className（否则 toggle('near') 之类断言永远看不到效果） */
  e.classList = {
    add(c) { cls.add(c); e.className = [...cls].join(" "); },
    remove(c) { cls.delete(c); e.className = [...cls].join(" "); },
    toggle(c, on) {
      const want = (on === undefined) ? !cls.has(c) : !!on;
      if (want) cls.add(c); else cls.delete(c);
      e.className = [...cls].join(" ");
      return want;
    },
    contains(c) { return cls.has(c); },
  };
  Object.defineProperty(e, "className", {
    get() { return e._cn; },
    set(v) {
      e._cn = String(v == null ? "" : v);
      cls.clear();
      e._cn.split(/\s+/).filter(Boolean).forEach((x) => cls.add(x));
    },
  });
  /* innerHTML = "" 必须清空 children（浏览器语义；不清会让重复渲染在桩里累加） */
  Object.defineProperty(e, "innerHTML", {
    get() { return e._ih; },
    set(v) { e._ih = String(v == null ? "" : v); if (e._ih === "") e.children.length = 0; },
  });
  Object.defineProperty(e, "value", { get() { return e._v; }, set(v) { e._v = v; } });
  Object.defineProperty(e, "clientWidth", { get() { return e._cw === undefined ? 900 : e._cw; }, set(v) { e._cw = v; } });
  if (id === "spark") { e.width = 1200; e.height = 78; }
  return (els[id] = e);
}
const created = [];
const winH = {};
const tableRows = [];
global.document = {
  getElementById: mkEl,
  querySelector: (s) => mkEl(String(s)),
  querySelectorAll: (s) => {
    if (s === "#tbl tbody tr") return tableRows;
    return [];
  },
  createElement: (tag) => {
    const e = mkEl("n" + created.length);
    e.tagName = String(tag).toUpperCase();
    created.push(e);
    if (e.tagName === "TR") tableRows.push(e);
    return e;
  },
  addEventListener() {},
  body: mkEl("body"),
};
let rafQueue = [];
global.requestAnimationFrame = (f) => { rafQueue.push(f); return rafQueue.length; };
global.setTimeout = global.setTimeout || ((f) => { rafQueue.push(f); return 0; });
const loadErrors = [];
global.Image = class {
  constructor() { this.complete = false; this.naturalWidth = 0; this.naturalHeight = 0; this.width = 0; this.height = 0; }
  set src(v) {
    this._src = v; this.complete = true; this.naturalWidth = 64; this.naturalHeight = 64;
    const self = this;
    Promise.resolve().then(() => { if (typeof self.onload === "function") { try { self.onload(); } catch (e) { loadErrors.push(String(e && e.message || e)); } } });
  }
  get src() { return this._src; }
};
global.window = { addEventListener(t, f) { (winH[t] = winH[t] || []).push(f); } };

let fails = 0;
const ok = (cond, msg) => { console.log((cond ? "  ok   " : "  FAIL ") + msg); if (!cond) fails++; };
const g = (id) => mkEl(id);
const fmtSec = (s) => (s < 0 ? "-" : "") + Math.floor(Math.abs(s) / 60) + ":" + String(Math.abs(Math.round(s)) % 60).padStart(2, "0");

/* 桩不解析 HTML → 勾选框的初始 checked 必须照 HTML 属性同步，否则默认态与浏览器不一致 */
["showBld", "showRoute", "showName", "showWard", "showSmoke", "showTL", "tlBld", "tc0", "tc1", "tc2", "tc3", "tfold", "tcdall"].forEach((id) => {
  const m = raw.match(new RegExp('<input[^>]*id="' + id + '"[^>]*>'));
  mkEl(id).checked = !!(m && /\bchecked\b/.test(m[0]));
});

/* 加载页面脚本 + 暴露内部量（页面脚本是 "use strict" → 声明不会泄漏，必须显式导出） */
eval(src + `
;globalThis.__t = {
  DATA: DATA, PL: PL, POS: POS, NW: NW, CG: CG, CX: CX, DIFF: DIFF, KDA: KDA, ICONS: ICONS,
  T0: T0, T1: T1, D: D,
  getT: function(){ return tCur; }, getTBig: function(){ return tBig; }, getS: function(){ return sVal; },
  getView: function(){ return viewRect; }, getSel: function(){ return selIdx; }, getPlaying: function(){ return playing; },
  getAnn: function(){ return annMarks; },
  posAt: posAt, valAt: valAt, diffAt: diffAt, kOf: kOf,
  clampView: clampView, w2pView: w2pView, calibFromPx: calibFromPx,
  commit: function(t){ commit(t); }, step: function(d){ step(d); },
  setSpeed: function(v){ setSpeed(v); }, resetZoom: function(){ resetZoom(); },
  setEntDiff: function(v){ setEntDiff(v); }, tick: function(ts){ tick(ts); },
  setView: function(vr){ viewRect = vr; }, clearSel: function(){ clearSel(); },
  draw: function(){ draw(); },
  select: function(i){ selectHero(i); },
  lastDetail: function(){ return lastDetail; },
  winProb: winProb, fmtPct: fmtPct, cdState: cdState,
  cdHTML: function(){ return String(document.getElementById("cdboard").innerHTML); },
  renderDetail: function(){ renderDetail(); }, renderCD: function(){ renderCD(); },
  det: DET, dnames: DNAMES, cd: CD, tput: TPUT, wp: WP,
  wards: WARDS, smoke: SMOKE, smoked: SMOKED, wicons: WICONS,
  iconsq: ICONSQ, tlicons: TLICONS, tlIconSrc: tlIconSrc,
  tl: TL, buildTimelineEvents: function(){ buildTimelineEvents(); },
  markNear: function(){ markNear(); }, jumpTo: jumpTo, fmtTL: fmtTL,
  evEls: function(){ return evEls; },
  wardsAliveAt: wardsAliveAt, isSmoked: isSmoked, markText: markText,
  diffEnd: function(){ return [DIFF.nw[D-1], DIFF.cg[D-1], DIFF.cx[D-1]]; }
};
`);

console.log("[Q7 回放浏览器 · 交互回归] " + path.basename(HTML) + (LITE ? "  【lite】" : ""));
const S = globalThis.__t;
const D = S.D, T0 = S.T0, T1 = S.T1, PL = S.PL;

/* ══ 1. 内嵌数据自洽 ══ */
{
  ok(PL.length === 10, "玩家数 = 10（实际 " + PL.length + "）");
  ok(PL.filter((p) => p.team === 2).length === 5 && PL.filter((p) => p.team === 3).length === 5, "天辉/夜魇各 5 人");
  ok(Object.keys(S.ICONS).length === 10, "10 个英雄头像内嵌（实际 " + Object.keys(S.ICONS).length + "）");
  const NGRID = S.DATA.DP || D;                      // lite 下逐秒数据被抽稀成 NGRID 格
  let lenOk = true, anyPos = true;
  PL.forEach((p) => {
    const P = S.POS[p.npc];
    if (!P || P.x.length !== NGRID || P.y.length !== NGRID || P.hp.length !== NGRID) lenOk = false;
    if (!P || !P.x.some((v) => v !== null)) anyPos = false;
  });
  ok(lenOk, "每英雄 x/y/hp 数组长度 == 网格长度(" + NGRID + (LITE ? "，lite 抽稀 " + S.DATA.step + "s" : " = D") + ")");
  ok(anyPos, "每英雄都至少有位置采样");
  let cov = 0;
  PL.forEach((p) => { cov += S.POS[p.npc].x.filter((v) => v !== null).length; });
  ok(cov / 10 / NGRID > 0.97, "位置覆盖率 " + (100 * cov / 10 / NGRID).toFixed(2) + "% > 97%");
  const NEG = S.DATA.DE || D;
  ok(S.DIFF.nw.length === NEG && S.DIFF.cg.length === NEG && S.DIFF.cx.length === NEG,
     "三条差值序列长度 == 经济网格长度(" + NEG + (LITE ? "，estep " + S.DATA.estep + "s" : " = D") + ")");
  // 击杀/助攻自洽：K/D 列之和 == 击杀事件里的 killer/victim 计数
  let kSum = 0, dSum = 0;
  PL.forEach((p) => { kSum += S.KDA[p.npc].k; dSum += S.KDA[p.npc].d; });
  const kEv = S.DATA.kills.filter((x) => x[1] >= 0).length;
  ok(kSum === kEv, "总击杀 " + kSum + " == 击杀事件里带 killer 的条数 " + kEv);
  ok(dSum === S.DATA.kills.length, "总死亡 " + dSum + " == 击杀事件条数 " + S.DATA.kills.length);
  let asstSelf = false, asstRange = false;
  S.DATA.kills.forEach((x) => { if (x[3].indexOf(x[1]) >= 0 && x[1] >= 0) asstSelf = true;
    x[3].forEach((a) => { if (a < 0 || a > 9) asstRange = true; }); });
  ok(!asstSelf, "助攻列表已剔除击杀者本人（assist_players 含自己的坑）");
  ok(!asstRange, "助攻索引都在 0..9");
  // 位置沿用：找一个 null 秒，必须回退到上一秒
  let tested = false;
  for (const p of PL) {
    const P = S.POS[p.npc];
    const st = S.DATA.step || 1;
    for (let k = 13; k < P.x.length; k++) {
      if (P.x[k] === null && P.x[k - 1] !== null) {
        const q = S.posAt(p.npc, T0 + k * st);
        ok(q && q.x === P.x[k - 1] && q.stale === true,
           "缺格沿用上一格(" + p.short + " @" + (T0 + k * st) + "s → 用 " + (T0 + (k - 1) * st) + "s)");
        tested = true; break;
      }
    }
    if (tested) break;
  }
  if (!tested) ok(true, "（本场没有需要沿用的缺秒——跳过）");
}

/* ══ 2. 双时间轴语义（owner 待拍板默认①：松手提交 + 瞬时归零）══ */
{
  ok(g("big").min === String(T0) || +g("big").min === T0, "大条 min == T0(" + T0 + ")，实际 " + g("big").min);
  ok(+g("big").max === T1, "大条 max == T1(" + T1 + ")");
  const smallAttr = (raw.match(/<input type="range" id="small"[^>]*>/) || [""])[0];
  ok(/min="-60"/.test(smallAttr) && /max="60"/.test(smallAttr), "小条 range 属性 = ±60s（" + smallAttr + "）");
  const bigAttr = (raw.match(/<input type="range" id="big"[^>]*>/) || [""])[0];
  ok(/step="/.test(bigAttr), "大条 range 属性完整（" + bigAttr + "）");
  ok(S.getT() === 0 && S.getTBig() === 0 && S.getS() === 0, "初始：停在 0:00（号角），小条 = 0（实际 tCur=" + S.getT() + "）");
  // 拖小条（未松手）：tCur 跟随，大条不动、但滑块同步移动
  const tb0 = S.getTBig();
  g("small").value = 30; g("small").oninput();
  ok(S.getT() === 30, "拖小条 +30 → tCur = 0:30（实际 " + S.getT() + "s → " + fmtSec(S.getT()) + "）");
  ok(S.getTBig() === tb0, "未松手：大条（已提交）不动");
  ok(+g("big").value === Math.round(S.getT()), "未松手：大条滑块同步小幅移动（" + g("big").value + " == " + Math.round(S.getT()) + "）");
  // 松手提交：大条推进滑量，小条瞬时归零
  g("small").onchange();
  ok(S.getTBig() === 30, "松手 → 大条推进到 0:30（实际 " + S.getTBig() + "s）");
  ok(S.getS() === 0 && +g("small").value === 0, "松手 → 小条瞬时归零");
  ok(S.getT() === 30, "提交后 tCur 保持 0:30");
  // 负向
  g("small").value = -20; g("small").oninput(); g("small").onchange();
  ok(S.getTBig() === 10 && S.getS() === 0, "小条 -20 松手 → 大条 0:10（实际 " + S.getTBig() + "s）");
  // 往前拖到出门期（-1:30）
  S.commit(T0);
  ok(S.getT() === T0, "可直接定位到 T0 = " + fmtSec(T0) + "（出门）");
  // 大条直接拖 = 绝对定位 + 小条归零
  g("small").value = 45; g("small").oninput();
  g("big").value = 1234; g("big").oninput();
  ok(S.getTBig() === 1234 && S.getS() === 0 && S.getT() === 1234, "拖大条 = 绝对定位 1234，小条立即归零");
  g("big").value = T1 + 500; g("big").oninput();  ok(S.getT() === T1, "大条超出 → 钳制到 T1");
  g("small").value = 60; g("small").oninput();
  ok(S.getT() === T1, "末尾再挂小条 +60 → 仍钳制到 T1");
  g("small").onchange();
  ok(S.getTBig() === T1 && S.getS() === 0, "末尾提交 → tBig = T1，小条归零（不越界）");
  g("big").value = T0 - 500; g("big").oninput();
  ok(S.getT() === T0, "大条低于 T0 → 钳制到 T0");
  // 按钮 / 键盘
  g("big").value = 600; g("big").oninput();
  S.step(-5);
  ok(S.getTBig() === 595, "« 5s 按钮 → 595（实际 " + S.getTBig() + "）");
  S.step(5);
  ok(S.getTBig() === 600, "5s » 按钮 → 600");
  (winH["keydown"] || []).forEach((f) => f({ code: "ArrowRight", target: { tagName: "BODY" }, preventDefault() {} }));
  ok(S.getTBig() === 605, "→ 键 → 605（实际 " + S.getTBig() + "）");
  S.setSpeed(4);
  ok(true, "速度按钮不抛异常");
}

/* ══ 3. 地图缩放/平移 + 选中 ══ */
{
  const evp = (x, y, ex) => Object.assign({ clientX: x, clientY: y, button: 0, preventDefault() {} }, ex || {});
  ok(S.getView() === null, "初始为全图（viewRect=null）");
  g("cv").onwheel(evp(512, 512, { deltaY: -100 }));
  const v1 = S.getView();
  ok(v1 && (v1[1] - v1[0]) < 17200 * 0.9, "滚轮上滚 → 放大到视野宽 " + Math.round(v1[1] - v1[0]));
  // 平移（★ 起点必须避开英雄标记：点到标记=选中而不是拖拽，这是设计）
  const clearPt = () => {
    let best = [900, 120], bd = -1;
    for (let x = 60; x <= 960; x += 60) for (let y = 60; y <= 960; y += 60) {
      let d = 1e9;
      S.getAnn().forEach((m) => { d = Math.min(d, Math.hypot(x - m.x, y - m.y)); });
      if (d > bd) { bd = d; best = [x, y]; }
    }
    return { pt: best, d: bd };
  };
  const cp = clearPt(), p0 = cp.pt, p1 = [Math.max(20, p0[0] - 300), p0[1]];
  const x0 = S.getView()[0];
  g("cv").onmousedown(evp(p0[0], p0[1]));
  g("cv").onmousemove(evp(p1[0], p1[1]));
  g("cv").onmouseup();
  ok(Math.abs(S.getView()[0] - x0) > 1,
     "拖拽平移生效 " + x0.toFixed(0) + " → " + S.getView()[0].toFixed(0) + "（起点距最近标记 " + cp.d.toFixed(0) + "px）");
  // 一路缩小 → 回全图
  let back = false;
  for (let i = 0; i < 40; i++) g("cv").onwheel(evp(512, 512, { deltaY: 100 }));
  back = S.getView() === null;
  ok(back, "滚轮下滚 40 次 → 回到全图（不再卡死）");
  // 极限放大被钳制
  for (let i = 0; i < 40; i++) g("cv").onwheel(evp(512, 512, { deltaY: -100 }));
  const vw = S.getView()[1] - S.getView()[0];
  ok(vw >= 599 && vw <= 601, "极限放大钳制在 600 世界单位（实际 " + Math.round(vw) + "）");
  // 平移不越界
  g("cv").onmousedown(evp(100, 100));
  g("cv").onmousemove(evp(900, 900));
  g("cv").onmouseup();
  const v = S.getView();
  ok(v[0] >= -8620 && v[1] <= 8620 && v[2] >= -8620 && v[3] <= 8620,
     "拖到画外 → 视野仍夹在图内 " + v.map((x) => Math.round(x)).join(","));
  eval("void 0");   // (无操作, 保留位置感)
  S.resetZoom();
  ok(S.getView() === null, "「↺ 全图」→ viewRect=null");
  // 坐标变换互逆
  S.setView([-1000, 1000, -1000, 1000]);
  const p = S.w2pView(500, -500);
  const w = S.calibFromPx(p[0], p[1]);
  ok(Math.abs(w[0] - 500) < 0.5 && Math.abs(w[1] + 500) < 0.5, "w2pView / calibFromPx 互逆（放大态）");
  S.resetZoom();
  // 点英雄标记 → 选中
  g("big").value = 900; g("big").oninput();
  const ann = S.getAnn();
  const expectN = PL.filter((q) => S.posAt(q.npc, 900)).length;
  const heroAnn = ann.filter((m) => m.kind === "hero");
  ok(heroAnn.length === expectN && heroAnn.length >= 9,
     "全图 + 900s：英雄标记数 == 有位置的英雄数（" + heroAnn.length + " == " + expectN
     + "，另有眼位/烟雾标记 " + (ann.length - heroAnn.length) + " 个）");
  const t0i = heroAnn[0].i;
  // ★ 几何闭环：地图标记像素坐标必须 == 官方标定式（与 PIL 预览/底图同一套常量）
  {
    const p = PL[t0i], q = S.posAt(p.npc, 900);
    const want = [508.3019 + 0.049038 * q.x, 504.5433 - 0.049038 * q.y];
    ok(Math.abs(heroAnn[0].x - want[0]) < 0.01 && Math.abs(heroAnn[0].y - want[1]) < 0.01,
       "标记像素坐标 == w2p(世界坐标)（" + heroAnn[0].x.toFixed(1) + "," + heroAnn[0].y.toFixed(1)
       + " vs " + want[0].toFixed(1) + "," + want[1].toFixed(1) + "）");
  }
  g("cv").onmousedown(evp(heroAnn[0].x, heroAnn[0].y, { button: 0, preventDefault() {} }));
  ok(S.getSel() >= 0, "点英雄标记 → 选中下标 " + S.getSel() + "（该处是 " + t0i + " 号）");
  ok(String(g("herotop").innerHTML).indexOf(PL[S.getSel()].short.replace(/_/g, " ")) >= 0,
     "选中后右栏顶部显示该英雄（" + PL[S.getSel()].short + "）");
  ok(g("paneHero").style.display === "" && g("paneList").style.display === "none",
     "选中后右栏切到英雄面板（paneHero 显示 / paneList 隐藏）");
  g("cv").onmousedown(evp(heroAnn[0].x, heroAnn[0].y, { button: 0, preventDefault() {} }));
  ok(S.getSel() === -1, "再点同一英雄 → 取消选中");
  ok(g("paneList").style.display === "" && g("paneHero").style.display === "none",
     "取消选中 → 右栏切回 10 英雄表");
  // 空白处 mousedown 不选中、进入拖拽
  g("cv").onmousedown(evp(5, 5, { button: 0, preventDefault() {} }));
  ok(S.getSel() === -1, "点空白 → 不选中（进入拖拽）");
  g("cv").onmouseup();
  // hover 文案
  g("cv").onmousemove(evp(heroAnn[1].x, heroAnn[1].y));
  ok(String(g("mapInfo").textContent).length > 5 && String(g("mapInfo").textContent).indexOf("滚轮") < 0,
     "hover 英雄标记 → 状态行显示该英雄信息：" + String(g("mapInfo").textContent).slice(0, 60));
}

/* ══ 3a0. 布局契约（owner 第一条：地图正方形且宽度 = 网页一半） ══ */
{
  const css = (raw.match(/<style>([\s\S]*?)<\/style>/) || [, ""])[1];
  const wrap = (css.match(/\.wrap\{[^}]*\}/) || [""])[0];
  ok(/display:\s*grid/.test(wrap), "两栏用 grid 布局");
  const m = wrap.match(/grid-template-columns:\s*minmax\(0,\s*calc\((\d+)%\s*([+-])\s*(\d+)px\)\)/);
  ok(!!m, "左栏宽度写成 calc(50% + 18px) 形式（" + (m ? m[0] : wrap) + "）");
  if (m) {
    const pct = +m[1], sign = m[2], off = +m[3];
    const canvasPct = sign === "+" ? pct + (off - 18) / 10 : pct - (off + 18) / 10;
    ok(Math.abs(canvasPct - 50) < 0.2,
       "地图画布宽度 = 内容宽度的 " + canvasPct.toFixed(1) + "%（目标 50%）");
  }
  const cvRule = (css.match(/#cv\{[^}]*\}/) || [""])[0];
  ok(/aspect-ratio:\s*1\s*\/\s*1/.test(cvRule), "画布强制 1:1（aspect-ratio:1/1）");
  ok(/width:\s*100%/.test(cvRule), "画布宽度跟随左栏");
  const iLeft = raw.indexOf('class="left"'), iRight = raw.indexOf('class="right');
  ok(iLeft > 0 && iRight > iLeft, "DOM 顺序：地图在左、明细在右");
  ok(/@media\(max-width:1180px\)\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/.test(css),
     "窄屏（≤1180px）退回单栏");
  ok(!/\.right\{width:470px/.test(css), "右栏不再是固定 470px（改为吃掉剩余宽度）");
  const iCv = raw.indexOf('id="cv"');
  ok(iCv > iLeft && iCv < iRight, "画布在左栏内（不与右栏同级）");
}

/* ══ 3a1. 时间轴重大事件（图标版：阵亡英雄头像 / 建筑图标，上=天辉有利 下=夜魇有利） ══ */
{
  const TL = S.tl || [];
  ok(TL.length > 30, "时间轴事件 " + TL.length + " 条");
  ok(TL.every(e => e.length === 6 && (e[1] === 2 || e[1] === 3) && typeof e[3] === "string"
                  && typeof e[4] === "string" && [0, 2, 3].indexOf(e[5]) >= 0),
     "字段 = [时刻, 有利方, 类型, 文案, 图标, 所属方]");
  const up = TL.filter(e => e[1] === 2), dn = TL.filter(e => e[1] === 3);
  const kills = TL.filter(e => e[2] === 0), blds = TL.filter(e => e[2] !== 0);
  ok(up.length > 0 && dn.length > 0, "天辉有利 " + up.length + " 条 / 夜魇有利 " + dn.length + " 条");
  ok(kills.length > 0 && blds.length > 0, "击杀 " + kills.length + " 条 / 建筑与肉山 " + blds.length + " 条");
  // 击杀：图标 = 阵亡英雄头像；建筑：图标 = 建筑类型键
  ok(kills.every(e => e[4].indexOf("h:") === 0), "击杀事件图标键 = h:<英雄>");
  ok(blds.every(e => ["tower", "rax_melee", "rax_range", "fort", "roshan", "watch"].indexOf(e[4]) >= 0),
     "建筑/肉山图标键合法（" + [...new Set(blds.map(e => e[4]))].join("/") + "）");
  ok(TL.every(e => e[0] >= T0 - 1 && e[0] <= T1 + 1), "事件时刻在时间轴范围内");
  const sq = Object.keys(S.iconsq || {}).length, ti = Object.keys(S.tlicons || {}).length;
  ok(sq >= 5 && ti >= 3, "图标资产已内嵌：英雄方头像 " + sq + " 个 / 建筑类 " + ti + " 个");
  // 预热：时间戳文字关掉（默认）
  g("showTL").checked = false; g("tlBld").checked = false;
  S.buildTimelineEvents();
  const upKids = g("evUp").children, dnKids = g("evDn").children;
  const upMark = upKids.filter(c => String(c.className).indexOf("evm") === 0);
  const dnMark = dnKids.filter(c => String(c.className).indexOf("evm") === 0);
  ok(upMark.length === up.length && dnMark.length === dn.length,
     "标记数 = 各侧事件数（上 " + upMark.length + "/" + up.length + "，下 " + dnMark.length + "/" + dn.length + "）");
  ok(upMark.every(c => c.style.bottom && !c.style.top) && dnMark.every(c => c.style.top && !c.style.bottom),
     "天辉有利贴轴上方 / 夜魇有利贴轴下方");
  // 图标：每个标记都有 <img class=ico> 且 src 是内嵌 PNG
  const imgs = upMark.concat(dnMark).map(c => c.children.filter(x => String(x.className) === "ico")[0]);
  ok(imgs.every(x => x && String(x.src).indexOf("data:image/png;base64,") === 0),
     "每个事件都画了图标（" + imgs.length + " 个内嵌 PNG）");
  // 描边色类 = 所属方；alt = 事件文案；bld 类与类型一致（按 TL 逐条对齐）
  const seqUp = up, seqDn = dn;
  const bad = [];
  [["up", upMark, seqUp], ["dn", dnMark, seqDn]].forEach(function (pair) {
    const tag = pair[0], marks = pair[1], seq = pair[2];
    if (marks.length !== seq.length) { bad.push(tag + ":数量"); return; }
    for (let i2 = 0; i2 < seq.length; i2++) {
      const e = seq[i2], cl = String(marks[i2].className);
      const own = e[5] || 0;
      const wantR = "r" + (own === 2 ? "2" : (own === 3 ? "3" : "0"));
      const wantBld = e[2] !== 0;
      if (cl.indexOf(wantR) < 0) bad.push(tag + "#" + i2 + ":缺少" + wantR);
      if ((cl.indexOf("bld") > 0) !== wantBld) bad.push(tag + "#" + i2 + ":bld类不符");
      if ((cl.indexOf("r2") > 0) && (cl.indexOf("r3") > 0)) bad.push(tag + "#" + i2 + ":双色");
      const im = marks[i2].children.filter(x => String(x.className) === "ico")[0];
      if (!im || String(im.alt) !== String(e[3])) bad.push(tag + "#" + i2 + ":alt");
    }
  });
  ok(bad.length === 0, "描边色类=所属方 / bld类=类型 / alt=文案（" + TL.length + " 条逐条对齐）"
     + (bad.length ? " 不符 " + bad.length + " 处：" + bad.slice(0, 4).join(",") : ""));
  const allMark = upMark.concat(dnMark);
  const cnt = f => allMark.filter(c => String(c.className).indexOf(f) > 0).length;
  const ecnt = v => TL.filter(e => (e[5] || 0) === v).length;
  const n2 = cnt("r2"), n3 = cnt("r3"), n0 = cnt("r0");
  const e2 = ecnt(2), e3 = ecnt(3), e0 = ecnt(0);
  ok(n2 === e2 && n3 === e3 && n0 === e0,
     "绿环（天辉的）" + n2 + "/" + e2 + " 个、红环（夜魇的）" + n3 + "/" + e3
     + " 个、灰环（无主的肉山）" + n0 + "/" + e0 + " 个，与数据一致");
  ok(n2 + n3 + n0 === TL.length, "每个标记恰好一个环色（" + (n2 + n3 + n0) + "/" + TL.length + "）");
  if (e0 > 0) {
    const rEl = TL.filter(e => (e[5] || 0) === 0)[0];
    ok(rEl[4] === "roshan", "无主事件只有肉山（" + rEl[3] + "）");
  }
  // 击杀图标就是阵亡英雄的头像
  const k0 = kills[0];
  const kMark = upMark.concat(dnMark).filter(c => String(c.title).indexOf(k0[3].slice(0, 6)) >= 0)[0];
  ok(!!kMark, "能找到某条击杀事件的标记（" + k0[3] + "）");
  if (kMark) {
    const im0 = kMark.children.filter(x => String(x.className) === "ico")[0];
    ok(String(im0.src) === String(S.iconsq[k0[4].slice(2)]),
       "阵亡英雄（" + k0[4].slice(2) + "）头像用的就是该英雄的小头像");
  }
  // 建筑图标带 bld 类
  ok(upMark.concat(dnMark).filter(c => String(c.className).indexOf("bld") > 0).length === blds.length,
     "建筑类标记数 = 建筑事件数（" + blds.length + "）");
  // 泳道高度必须容得下最高一层标记（否则图标会盖到上面的说明/坐标轴上）
  const laneUp = parseFloat(String(g("evUp").style.height)), laneDn = parseFloat(String(g("evDn").style.height));
  const topUp = Math.max.apply(null, [0].concat(upMark.map(c => parseFloat(String(c.style.bottom || "0")))));
  const topDn = Math.max.apply(null, [0].concat(dnMark.map(c => parseFloat(String(c.style.top || "0")))));
  ok(laneUp >= topUp + 26 && laneDn >= topDn + 26,
     "泳道高度容得下最高一层（上 " + laneUp + "≥" + topUp + "+26，下 " + laneDn + "≥" + topDn + "+26）");
  ok(upMark.every(c => parseFloat(String(c.style.bottom || "0")) >= 0) && upMark.length > 0,
     "上方标记全部锚在轴上方（bottom 定位）");
  // 时间戳文字默认不显示；打开后每个标记带 mm:ss
  ok(upMark.every(c => c.children.filter(x => String(x.className) === "t").length === 0),
     "默认只显示图标（不显示时间戳文字）");
  g("showTL").checked = true; S.buildTimelineEvents();
  const upMark2 = g("evUp").children.filter(c => String(c.className).indexOf("evm") === 0);
  const ts = upMark2.map(c => c.children.filter(x => String(x.className) === "t")[0]);
  ok(ts.every(x => x && /^\d+:\d\d$/.test(String(x.textContent))),
     "打开「时间戳文字」→ 图标旁带 mm:ss（" + ts.slice(0, 4).map(x => x.textContent).join(" ") + "）");
  g("showTL").checked = false;
  // 只标建筑/肉山
  g("tlBld").checked = true; S.buildTimelineEvents();
  const upMark3 = g("evUp").children.filter(c => String(c.className).indexOf("evm") === 0);
  ok(upMark3.length === up.filter(e => e[2] !== 0).length,
     "只标建筑/肉山 → 上方标记 " + upMark3.length + " 个");
  ok(upMark3.every(c => String(c.className).indexOf("bld") > 0), "剩下的都是建筑类");
  g("tlBld").checked = false; S.buildTimelineEvents();
  // 刻度与点击跳转
  const ticks = g("evUp").children.filter(c => String(c.className).indexOf("evt") === 0);
  ok(ticks.length === up.length, "轴上刻度数 = 事件数（" + ticks.length + "）");
  const tgt = up[0];
  const mk2 = g("evUp").children.filter(c => String(c.className).indexOf("evm") === 0)
    .find(c => Math.abs(parseInt(String(c.style.left), 10) / 100 * (T1 - T0) + T0 - tgt[0]) < 40);
  if (mk2) { S.commit(T0); mk2.onclick(); ok(S.getTBig() > T0, "点图标 → 跳到 " + S.getTBig()); }
  // 高亮
  S.commit(tgt[0]); S.markNear();
  ok(S.evEls().filter(x => String(x.el.className).indexOf("near") > 0).length > 0,
     "靠近播放头的事件被高亮");
  // CSS 契约：时间轴在地图之上 + 整宽
  const iTL = raw.indexOf('id="timeline"'), iW = raw.indexOf('class="wrap"');
  ok(iTL > 0 && iW > 0 && iTL < iW, "时间轴在地图（.wrap）之前（上方）");
  const css2 = (raw.match(/<style>([\s\S]*?)<\/style>/) || [, ""])[1];
  ok(/\.evm\{/.test(css2) && /\.evm \.ico\{/.test(css2), "图标标记样式已定义");
  ok(/\.evm\.r0 \.ico/.test(css2), "无主（肉山）灰环样式已定义");
  ok(/#timeline\{margin-top/.test(css2), "#timeline 独立整宽面板");
  S.commit(900);
}

/* ══ 3a. 眼位 + 烟雾图层（复用 Q5B 口径） ══ */
{
  const W = S.wards || [];
  ok(W.length > 50, "眼位数据内嵌：" + W.length + " 支");
  const obs = W.filter((w) => w[3] === 0).length, sen = W.filter((w) => w[3] === 1).length;
  ok(obs > 0 && sen > 0, "真假眼都有（假眼 " + obs + " / 真眼 " + sen + "）");
  ok(W.every((w) => w[5] > w[4]), "每支眼 销毁 > 放置");
  ok(W.every((w) => Math.abs(w[0]) <= 9000 && Math.abs(w[1]) <= 9000), "眼位坐标都在地图范围内");
  const cen = W.filter((w) => w[7] === 1);
  ok(cen.every((w) => w[6] === 2), "被截断的眼 reason 都是『被比赛结束截断』（" + cen.length + " 支）");
  // 存活集合与逐秒判定
  const t = 900;
  const alive = S.wardsAliveAt(t);
  ok(alive.length > 0, "t=900s 存活眼位 " + alive.length + " 支");
  ok(alive.every((w) => t >= w[4] && t <= w[5]), "存活判定：place<=t<=destroy");
  // 地图上确实画出来了（且带 kind 供 hover/锁定）
  const wm = S.getAnn().filter((m) => m.kind === "ward");
  ok(wm.length === alive.length, "地图上眼标记数 == 存活眼数（" + wm.length + " == " + alive.length + "）");
  const mt = S.markText(wm[0]);
  ok(/假眼|真眼/.test(mt) && /放置/.test(mt) && /销毁/.test(mt) && /存活/.test(mt),
     "眼标记 hover 文案含 类型/放置/销毁/存活：" + mt.slice(0, 70));
  // 关掉开关 → 不再画
  g("showWard").checked = false; S.draw();
  ok(S.getAnn().filter((m) => m.kind === "ward").length === 0, "关掉『眼位』→ 地图不再画眼");
  ok(String(g("wardCount").textContent) === "", "关掉后计数清空");
  g("showWard").checked = true; S.draw();
  ok(S.getAnn().filter((m) => m.kind === "ward").length === alive.length, "重新打开 → 恢复");
  ok(/存活 \d+ 支/.test(String(g("wardCount").textContent)), "地图栏显示存活计数：" + g("wardCount").textContent);
  // 烟雾
  const SM = S.smoke || [];
  ok(Object.keys(S.smoked).length === 10, "10 个英雄都有『处于烟雾中』区间数组");
  ok(SM.length > 0, "烟雾使用记录 " + SM.length + " 次");
  const smk = SM[0];
  S.commit(smk[0] + 3);
  const smm = S.getAnn().filter((m) => m.kind === "smoke");
  ok(smm.length > 0, "烟雾开启后 +3s 地图上出现烟雾标记（" + smm.length + " 个）");
  ok(/烟雾/.test(S.markText(smm[0])), "烟雾标记 hover 文案：" + S.markText(smm[0]).slice(0, 60));
  const anySmoked = Object.keys(S.smoked).some((k) => S.smoked[k].length > 0);
  ok(anySmoked, "存在『英雄处于烟雾中』的区间");
  S.commit(900);
}

/* ══ 3b. 第二步：±10s combat log 四 toggle + 技能 CD + 状态胜率 ══ */
if (!LITE) {
  // --- 数据自洽：明细是 Δ 编码的逐条数组；CD 数据存在 ---
  const pl0 = PL[0];
  ok(S.det && Object.keys(S.det).length === 10, "10 个英雄都有 ±10s 明细数组（" + Object.keys(S.det).length + "）");
  let nAll = 0, okShape = true, lastDtNeg = 0;
  Object.keys(S.det).forEach((k) => {
    const a = S.det[k];
    nAll += a.length;
    for (let k = 0; k < a.length; k++) {
      const r = a[k];
      if (r.length < 4 || r.length > 5) okShape = false;
      if (k > 0 && r[0] < 0) lastDtNeg++;      // 首条 dt = 绝对时刻（可为负，号角前）；其余是秒差
      if (r[1] < 0 || r[1] > 15) okShape = false;
    }
  });
  ok(okShape && lastDtNeg === 0, "明细字段形状合法（[Δdt, code, name, other(, val)]，Δdt≥0；共 " + nAll + " 条）");
  ok(nAll > 50000, "逐条内嵌条数 " + nAll + "（无采样丢弃）");
  ok(Array.isArray(S.dnames) && S.dnames.length > 50, "名称字典 " + S.dnames.length + " 项");
  ok(!!S.cd && !!S.cd.keys && Object.keys(S.cd.keys).length > 10,
     "技能/道具 CD 数据存在（键 " + (S.cd ? Object.keys(S.cd.keys).length : 0) + " 个，源 " + (S.cd ? S.cd.src : "-") + "）");
  ok(!!S.wp, "状态胜率模型已内嵌（" + (S.wp ? (S.wp.n_match + " 场拟合") : "缺") + "）");

  // --- 选一个"忙"的时刻，选中英雄，验证窗口严格 ±10s ---
  g("big").value = 1500; g("big").oninput();
  S.select(0);
  ok(S.getSel() === 0, "选中 0 号英雄");
  const ld = S.lastDetail();
  ok(!!ld, "lastDetail 已记录");
  ok(Math.abs(ld.lo - 1490) < 1e-6 && Math.abs(ld.hi - 1510) < 1e-6,
     "明细窗口 = 当前时刻 ±10s（" + ld.lo + " → " + ld.hi + "）");
  ok(ld.tMin === null || (ld.tMin >= ld.lo && ld.tMax <= ld.hi),
     "窗口内所有条目都落在 [t−10, t+10]（" + ld.tMin + " → " + ld.tMax + "）");
  ok(ld.n > 0, "该时刻窗口命中 " + ld.n + " 条（四类合计）");
  ok(ld.cnt.length === 4 && ld.cnt.reduce((a, b) => a + b, 0) === ld.n,
     "四类计数之和 == 命中条数（" + ld.cnt.join("/") + " == " + ld.n + "）");
  ok(String(g("dsum").innerHTML).indexOf("窗口") >= 0 && String(g("dsum").innerHTML).indexOf("命中") >= 0,
     "右栏汇总行显示窗口与命中数");
  ok(String(g("#dtbl tbody").innerHTML).length > 0, "明细表渲染出行（tbody innerHTML 非空）");

  // --- 4 个 toggle 真的过滤 ---
  const before = S.lastDetail().n;
  g("tc0").checked = false; S.renderDetail();
  const after = S.lastDetail().n;
  ok(S.lastDetail().cnt[0] === 0, "关掉『给出的 modifier』→ 该类计数归 0");
  ok(after <= before, "关掉一类后命中数不增加（" + before + " → " + after + "）");
  g("tc0").checked = true; S.renderDetail();
  ok(S.lastDetail().cnt[0] > 0 && S.lastDetail().n === before, "重新打开 → 恢复原命中数 " + before);
  // 折叠开关（不改变命中数，只减少显示行数）
  const shownFold = S.lastDetail().shown;
  g("tfold").checked = false; S.renderDetail();
  ok(S.lastDetail().n === before, "关掉折叠 → 命中条数不变（" + S.lastDetail().n + "）");
  ok(S.lastDetail().shown >= shownFold, "关掉折叠 → 显示行数不减少（" + shownFold + " → " + S.lastDetail().shown + "）");
  g("tfold").checked = true; S.renderDetail();

  // --- 窗口随时间移动 ---
  g("big").value = 2000; g("big").oninput();
  ok(Math.abs(S.lastDetail().lo - 1990) < 1e-6, "移动时间轴 → 窗口跟着移动（" + S.lastDetail().lo + "）");

  // --- 技能 CD 三态 ---
  const cdh = S.cdHTML();
  ok(cdh.length > 100, "CD 面板已渲染（" + cdh.length + " 字符）");
  ok(/Black King Bar/.test(cdh), "关键道具 BKB 出现在 CD 面板");
  ok(/TP 卷轴/.test(cdh), "TP 卷轴出现在 CD 面板（充能制，只报使用记录）");
  ok(/冷却 /.test(cdh) || /就绪/.test(cdh), "CD 面板出现 冷却/就绪 态");
  ok(!/Empty1|Special Bonus/.test(cdh), "默认隐藏天赋/空槽占位（CD 面板不含 Empty1/Special Bonus）");
  g("tcdall").checked = true; S.renderCD();
  ok(/Empty1|Special Bonus/.test(S.cdHTML()), "勾『显示天赋/空槽』→ 全部展开");
  g("tcdall").checked = false; S.renderCD();
  const key0 = Object.keys(S.cd.keys)[0];
  const ivs = [[1000, 1030]];
  ok(S.cdState(ivs, 1010) !== null && Math.abs(S.cdState(ivs, 1010) - 20) < 1e-9,
     "cdState：区间内返回剩余秒（1010 → " + S.cdState(ivs, 1010) + "）");
  ok(S.cdState(ivs, 999) === null && S.cdState(ivs, 1031) === null, "cdState：区间外返回 null（就绪）");
  const ivsAll = [];
  Object.keys(S.cd.ab).forEach((npc) => S.cd.ab[npc].forEach((e) => (e[3] || []).forEach((iv) => ivsAll.push(iv))));
  ok(ivsAll.length > 1000, "CD 区间总数 " + ivsAll.length);
  ok(ivsAll.every((iv) => iv[1] > iv[0]), "所有 CD 区间 end > start");
  const ivsTrack = [];
  Object.keys(S.cd.it).forEach((npc) => S.cd.it[npc].forEach((e) => (e[3] || []).forEach((iv) => ivsTrack.push(iv))));
  ok(ivsTrack.length > 0, "道具 CD 区间 " + ivsTrack.length + " 个（BKB/刷新球等）");
  ok(Object.keys(S.tput).length === 10, "10 个英雄都有 TP 使用记录数组");

  // --- 状态胜率：单调性 + 不偷看结果 ---
  const w1 = S.winProb(2400, 20000, 20000), w2 = S.winProb(2400, 0, 0), w3 = S.winProb(2400, -20000, -20000);
  ok(w1 > w2 && w2 > w3, "胜率随领先单调上升（" + S.fmtPct(w1) + " > " + S.fmtPct(w2) + " > " + S.fmtPct(w3) + "）");
  ok(w2 > 0.3 && w2 < 0.7, "均势时胜率接近 50%（" + S.fmtPct(w2) + "）");
  ok(S.winProb(600, 20000, 20000) < w1, "同样 20k 领先：10 分钟时胜率低于 40 分钟（" + S.fmtPct(S.winProb(600, 20000, 20000)) + " < " + S.fmtPct(w1) + "）");
  ok(S.winProb(1200, null, 0) === null, "缺特征时返回 null（不猜）");
  ok(S.wp.n_test > 0 && S.wp.auc_test > 0.5 && S.wp.auc_test < 1,
     "模型有按 match 划分的测试集且 AUC 合理：" + S.wp.auc_test.toFixed(3) + "（测试样本 " + S.wp.n_test + "）");
  ok(String(g("vWp").textContent).indexOf("%") > 0, "顶部胜率显示百分比：" + g("vWp").textContent);
  S.clearSel();
  ok(S.getSel() === -1 && g("paneList").style.display === "", "返回 10 英雄表");
}

/* ══ 3c. lite 下仍须成立的核心：索引网格 + CD + 胜率 + 眼位 ══ */
if (LITE) {
  const step = S.DATA.step, estep = S.DATA.estep;
  ok(step > 1, "lite 抽稀步长 = " + step + "s（经济 " + estep + "s）");
  ok(S.POS[PL[0].npc].x.length === Math.ceil(D / step) ||
     S.POS[PL[0].npc].x.length === Math.floor((D - 1) / step) + 1,
     "位置数组长度按 step 抽稀（" + S.POS[PL[0].npc].x.length + " vs D/" + step + "）");
  ok(S.DIFF.nw.length === Math.floor((D - 1) / estep) + 1,
     "经济序列长度按 estep 抽稀（" + S.DIFF.nw.length + " vs D/" + estep + "）");
  ok(Object.keys(S.det).length === 0, "lite 版不含 ±10s 明细（载荷 0 条）");
  // 抽稀后索引仍要取到正确时刻的值：与"沿用上一格"一起验证
  S.commit(1200);
  ok(S.diffAt("nw", 1200) !== null && S.posAt(PL[0].npc, 1200) !== null,
     "lite 下 t=1200s 仍能取到净值差/位置");
  const a = S.diffAt("nw", 1200), b = S.diffAt("nw", 1200 + estep - 1);
  ok(a !== null && b !== null, "esetp 网格内相邻时刻都能取到（" + a + " / " + b + "）");
  ok(!!S.cd && Object.keys(S.cd.keys).length > 10, "lite 仍含技能 CD 数据");
  ok(String(g("cdboard").innerHTML).indexOf("Black King Bar") >= 0 || /TP 卷轴/.test(S.cdHTML()),
     "lite 的 CD 面板仍渲染（文字芯片）");
  ok(S.winProb(2400, 20000, 20000) > S.winProb(2400, -20000, -20000), "lite 仍含胜率模型");
  ok((S.wards || []).length > 50 && (S.smoke || []).length > 0, "lite 仍含眼位/烟雾图层");
  ok(String(g("liteNote").style.display) === "", "lite 提示条已显示");
}

/* ══ 4. 连续刷新：表格/顶部不出现 NaN/undefined ══ */
{
  const times = [];
  const span = T1 - T0;
  for (let f = 0; f <= 1.0001; f += 0.02) times.push(Math.round(T0 + f * span));
  times.push(T0, T1, 0, -1, 1, 600, 1200);
  let bad = null, threw = null;
  try {
    times.forEach((t) => {
      g("big").value = t; g("big").oninput();
      ["vClock", "vNw", "vCg", "vCx", "vPhase", "biglabel", "smalllabel", "spTitle"].forEach((id) => {
        const s = String(g(id).textContent);
        if (/NaN|undefined|null/.test(s)) bad = id + " = " + s;
      });
      for (let i = 0; i < 10; i++) {
        ["c-k-", "c-d-", "c-a-", "c-lh-", "c-dn-", "c-nw-", "c-cg-", "c-cx-", "c-hp-"].forEach((pre) => {
          const s = String(g(pre + i).textContent);
          if (/NaN|undefined|null/.test(s)) bad = pre + i + " = " + s;
        });
      }
    });
  } catch (e) { threw = e; }
  ok(!threw, "全时间轴扫描（" + times.length + " 个时刻）不抛异常" + (threw ? "：" + threw.message : ""));
  ok(bad === null, "扫描中无 NaN/undefined 单元格" + (bad ? "（" + bad + "）" : ""));
  // 开场 +2s（英雄已出门）必须有位置/血量
  g("big").value = T0 + 4 * (S.DATA.step || 1); g("big").oninput();
  ok(/^\d+( \/ \d+)?$/.test(String(g("c-hp-0").textContent)), "开场首个采样格 hp 是数值（" + g("c-hp-0").textContent + "）");
  ok(String(g("c-nw-0").textContent).indexOf(",") > 0 || +String(g("c-nw-0").textContent).replace(/,/g, "") > 0,
     "开场首个采样格 净值有值（" + g("c-nw-0").textContent + "）");
}

/* ══ 5. 播放推进 ══ */
{
  S.commit(1000);
  const before = S.getTBig();
  g("play").onclick();                      // ▶
  ok(S.getPlaying() === true, "点播放 → playing=true");
  let ts = 1000;
  for (let i = 0; i < 8; i++) { ts += 250; S.tick(ts); }   // 每帧上限 0.25s → 8 帧 ≈ +2s
  ok(S.getTBig() > before, "播放推进 tBig " + before + " → " + S.getTBig().toFixed(2));
  g("play").onclick();                      // ⏸
  ok(S.getPlaying() === false, "再点 → 暂停");
  // 末尾自动停
  S.commit(T1 - 1);
  g("play").onclick();
  ts = 10000;
  for (let i = 0; i < 20; i++) { ts += 250; S.tick(ts); }
  ok(S.getPlaying() === false && S.getTBig() === T1, "播到末尾自动停且 tBig = T1（实际 " + S.getTBig() + "）");
}

/* ══ 6. 火花线口径切换 ══ */
{
  S.setEntDiff("cg");
  ok(String(g("spTitle").textContent).indexOf("累计") >= 0, "火花线切到『累计金币差』：" + g("spTitle").textContent);
  S.setEntDiff("cx");
  ok(String(g("spTitle").textContent).indexOf("经验") >= 0, "切到『经验差』");
  S.setEntDiff("nw");
  ok(String(g("spTitle").textContent).indexOf("净值差") === 0, "切回『净值差』");
  // 火花线点击定位
  S.commit(500);
  g("spark").onmousedown({ clientX: 600, clientY: 30, preventDefault() {} });
  ok(S.getTBig() > 500, "火花线点击 → 定位到 " + S.getTBig() + "s（> 500）");
}

/* ══ 7. 地图/图 onload 不抛异常 ══ */
/* ══ 7. 可选：把右栏面板渲染成文本打印出来（人眼核对内容；不需要浏览器） ══
   用法： node analysis/q7_viewer_itest.js <mid> dump=<hero_idx>@<显示秒>[,<idx>@<秒>...]
   例：   node analysis/q7_viewer_itest.js 8955197224 dump=5@1500  */
if (process.argv[3] && process.argv[3].indexOf("dump=") === 0) {
  const spec = process.argv[3].slice(5);
  spec.split(",").forEach(function (s) {
    const m = s.split("@");
    const i = parseInt(m[0], 10), t = parseFloat(m[1]);
    S.commit(t); S.select(-1); S.select(i);
    const strip = (h) => String(h).replace(/<[^>]*>/g, "\t").replace(/\t+/g, "  ").replace(/[ \t]+\n/g, "\n").trim();
    console.log("\n" + "=".repeat(96));
    console.log("英雄 #" + i + "（" + PL[i].short + "）@ " + fmtSec(t));
    console.log("-- 头部 --\n" + strip(g("herotop").innerHTML));
    console.log("-- 汇总 --\n" + strip(g("dsum").innerHTML));
    console.log("-- ±10s 明细（前 25 行）--");
    const rows = String(g("#dtbl tbody").innerHTML).split("</tr>").slice(0, 25);
    rows.forEach(function (r) { const txt = strip(r); if (txt) console.log("   " + txt.replace(/\t/g, " | ")); });
    console.log("-- 技能 CD --");
    console.log("   " + strip(g("cdboard").innerHTML).replace(/\t/g, " | "));
    console.log("-- 胜率 -- " + g("vWp").textContent + "（" + g("vWpNote").textContent + "）");
    console.log("-- 时间轴重大事件 --");
    S.buildTimelineEvents();
    [["evUp", "▲ 天辉有利（轴上方）"], ["evDn", "▼ 夜魇有利（轴下方）"]].forEach(function (pair) {
      const kids = g(pair[0]).children;
      const ticks = kids.filter(c => String(c.className).indexOf("evt") === 0);
      const labs = kids.filter(c => String(c.className).indexOf("ev ") === 0);
      console.log("   " + pair[1] + "：刻度 " + ticks.length + " 个，时间戳 " + labs.length + " 个");
      const rows = {};
      labs.forEach(c => { const o = c.style.bottom || c.style.top || "0"; rows[o] = (rows[o] || 0) + 1; });
      console.log("     错行分布 " + JSON.stringify(rows) + " ｜ 样例 " +
        labs.slice(0, 14).map(c => c.textContent + (String(c.className).indexOf("bld") > 0 ? "*" : "")).join(" "));
      const b = labs.filter(c => String(c.className).indexOf("bld") > 0).slice(0, 5);
      if (b.length) console.log("     建筑类时间戳：" + b.map(c => c.textContent + " [" + String(c.title).slice(0, 32) + "]").join(" ｜ "));
    });
  });
}

setTimeout(() => {
  ok(loadErrors.length === 0, "图/头像 onload 回调不抛异常" + (loadErrors.length ? "：" + loadErrors.join("; ") : ""));
  ok(calls.drawImage > 0, "渲染时确实绘制了底图/头像（drawImage " + calls.drawImage + " 次）");
  ok(calls.fillText + calls.arc > 0, "渲染时确实绘制了英雄标记（arc " + calls.arc + " / fillText " + calls.fillText + "）");
  console.log(fails ? "\n" + fails + " 项失败" : "\n全部通过");
  process.exit(fails ? 1 : 0);
}, 20);
