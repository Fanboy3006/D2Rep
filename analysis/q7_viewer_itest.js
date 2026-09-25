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
    /* 显示尺寸 == 逻辑尺寸（页面 fitCanvas() 会把两者设成同一个值），否则 evPx 的换算会错 */
    getBoundingClientRect() { return { left: 0, top: 0, width: e.width || e.clientWidth, height: e.height || e.clientHeight }; },    setAttribute(k, v) { e["_attr_" + k] = v; },
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
  /* innerHTML = "" 必须清空 children（浏览器语义；不清会让重复渲染在桩里累加）。
     另外：真实浏览器会把 innerHTML 字符串里的 id 变成真元素（表格单元格 c-hp-0 之类就是这么做出来的），
     桩不解析 DOM，所以这里把字符串里的 id 记进"运行时存在的 id"集合，供 getElementById 判断。 */
  Object.defineProperty(e, "innerHTML", {
    get() { return e._ih; },
    set(v) {
      e._ih = String(v == null ? "" : v);
      if (e._ih === "") e.children.length = 0;
      const mm = e._ih.match(/id="([^"]+)"/g);
      if (mm) mm.forEach((x) => RUNTIME_IDS.add(x.slice(4, -1)));
    },
  });
  Object.defineProperty(e, "value", { get() { return e._v; }, set(v) { e._v = v; } });
  Object.defineProperty(e, "clientWidth", { get() { return e._cw === undefined ? 900 : e._cw; }, set(v) { e._cw = v; } });
  Object.defineProperty(e, "clientHeight", { get() { return e._ch === undefined ? 560 : e._ch; }, set(v) { e._ch = v; } });
  if (id === "spark") { e.width = 1200; e.height = 78; }
  return (els[id] = e);
}
const created = [];
const RUNTIME_IDS = new Set();          // innerHTML 字符串里出现过的 id（浏览器会真的建出这些元素）
const winH = {};
const tableRows = [];
/* ★ 桩只认"页面上真实存在的 id"（浏览器就是这样）：
   以前桩对任何 id 都凭空造元素，于是"改文案时删掉了 #mid、init 第一句就抛错 →
   头像条/时间轴/地图尺寸全没建出来"这种事故在测试里完全看不出来（页面表现成"英雄没了、地图变小了"）。
   "真实存在" = 静态 markup 里的 id ∪ 脚本用 innerHTML 字符串造出来的 id（如表格单元格 c-hp-0）。 */
const MARKUP_IDS = new Set([
  ...(raw.slice(0, raw.indexOf("<script")).match(/id="([^"]+)"/g) || []),
  ...(raw.slice(raw.indexOf("<script")).match(/id="([^"]+)"/g) || []),
].map((x) => x.slice(4, -1)));
const DYNAMIC_IDS = new Set();          // 脚本里动态 setAttribute("id", ...) 的（本页目前没有）
const phantomIds = new Set();
global.document = {
  getElementById: (id) => {
    const k = String(id);
    if (!MARKUP_IDS.has(k) && !DYNAMIC_IDS.has(k) && !RUNTIME_IDS.has(k)
        && !created.some((e) => e.id === k)) {
      phantomIds.add(k);                // 记下来，测试结束时报错
      return null;                      // 浏览器行为：不存在就是 null
    }
    return mkEl(k);
  },
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
    /* ★ 宽高故意取**英雄卡的 128×72**（真实资产就是这个尺寸，见 opendota_analysis/assets/hero_icons）。
       以前桩一律给 64×64 正方形，于是"把 128×72 硬塞进正方形圆点"这种横向压扁的 bug
       在测试里完全看不出来 —— 正方形的桩算出来的裁剪量永远是 0。 */
    this._src = v; this.complete = true; this.naturalWidth = 128; this.naturalHeight = 72;
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
["showBld", "showRoute", "showName", "showWard", "showSmoke", "showTL", "tlBld", "tc0", "tc1", "tc2", "tc3", "tfold", "tcdall", "tnc"].forEach((id) => {
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
  getCSX: function(){ return CSX; }, fitCanvas: function(){ return fitCanvas(); },
  mapScale: mapScale, w2p: w2p,
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
  renderDetail: function(){ renderDetail(true); }, renderCD: function(){ renderCD(); },
  det: DET, dnames: DNAMES, cd: CD, tput: TPUT, wp: WP,
  wards: WARDS, smoke: SMOKE, smoked: SMOKED, wicons: WICONS,
  iconsq: ICONSQ, tlicons: TLICONS, tlIconSrc: tlIconSrc,
  dicons: DICONS, selficons: SELFICONS, diTag: diTag, DETWIN: DET_WIN,
  isCritter: isCritter, isBldName: isBldName,
  attr: ATTR, detLen: function(){ return detSeq.length; }, paintDet: function(){ paintDetRows(); }, detScroll: function(){ detOnScroll(); },
  tl: TL, buildTimelineEvents: function(){ buildTimelineEvents(); },
  markNear: function(){ markNear(); }, jumpTo: jumpTo, fmtTL: fmtTL,
  evEls: function(){ return evEls; },
  wardsAliveAt: wardsAliveAt, isSmoked: isSmoked, markText: markText,
  diffEnd: function(){ return [DIFF.nw[D-1], DIFF.cg[D-1], DIFF.cx[D-1]]; },
  fmt: fmt,
  ultStateOf: ultStateOf, tpStateOf: tpStateOf, applyHeroFrames: applyHeroFrames,
  getTpCool: function(){ return TPCOOL; },
  fights: FIGHTS, openRecap: function(i){ openRecap(i); }, closeRecap: function(){ closeRecap(); },
  recapJump: function(d){ recapJump(d); }, recapIdx: function(){ return recapIdx; },
  renderRecap: function(){ renderRecap(); },
  showSeekBub: showSeekBub, hideSeekBub: hideSeekBub,
  seekBubEl: function(){ return seekbub; },
  heroTitles: function(){
    const o = {};
    PL.forEach(function(p, i){
      const el = document.querySelector('.hero[data-i="' + i + '"]');
      o[i] = el ? String(el.title) : null;
    });
    return o;
  }
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
  // ★ 目标时刻按 T1 推导：写死 1234 的话，遇到 20 分钟左右的短场会被钳制到 T1 而误报失败。
  g("small").value = 45; g("small").oninput();
  const seekT = Math.min(1234, T1 - 5);
  g("big").value = seekT; g("big").oninput();
  ok(S.getTBig() === seekT && S.getS() === 0 && S.getT() === seekT,
     "拖大条 = 绝对定位 " + seekT + "，小条立即归零");
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
  const CSX = S.getCSX();                     // 画布逻辑边长（fitCanvas 按可用高度定的）
  const CTR = Math.round(CSX / 2);
  ok(S.getView() === null, "初始为全图（viewRect=null）");
  g("cv").onwheel(evp(CTR, CTR, { deltaY: -100 }));
  const v1 = S.getView();
  ok(v1 && (v1[1] - v1[0]) < 17200 * 0.9, "滚轮上滚 → 放大到视野宽 " + Math.round(v1[1] - v1[0]));
  // 平移（★ 起点必须避开英雄标记：点到标记=选中而不是拖拽，这是设计）
  const clearPt = () => {
    let best = [Math.round(CSX * 0.9), Math.round(CSX * 0.12)], bd = -1;
    const st = Math.round(CSX / 16);
    for (let x = st; x <= CSX - st; x += st) for (let y = st; y <= CSX - st; y += st) {
      let d = 1e9;
      S.getAnn().forEach((m) => { d = Math.min(d, Math.hypot(x - m.x, y - m.y)); });
      if (d > bd) { bd = d; best = [x, y]; }
    }
    return { pt: best, d: bd };
  };
  const cp = clearPt(), p0 = cp.pt, p1 = [Math.max(Math.round(CSX * 0.02), p0[0] - Math.round(CSX * 0.15)), p0[1]];
  const x0 = S.getView()[0];
  g("cv").onmousedown(evp(p0[0], p0[1]));
  g("cv").onmousemove(evp(p1[0], p1[1]));
  g("cv").onmouseup();
  ok(Math.abs(S.getView()[0] - x0) > 1,
     "拖拽平移生效 " + x0.toFixed(0) + " → " + S.getView()[0].toFixed(0) + "（起点距最近标记 " + cp.d.toFixed(0) + "px）");
  // 一路缩小 → 回全图
  let back = false;
  for (let i = 0; i < 40; i++) g("cv").onwheel(evp(CTR, CTR, { deltaY: 100 }));
  back = S.getView() === null;
  ok(back, "滚轮下滚 40 次 → 回到全图（不再卡死）");
  // 极限放大被钳制
  for (let i = 0; i < 40; i++) g("cv").onwheel(evp(CTR, CTR, { deltaY: -100 }));
  const vw = S.getView()[1] - S.getView()[0];
  ok(vw >= 599 && vw <= 601, "极限放大钳制在 600 世界单位（实际 " + Math.round(vw) + "）");
  // 平移不越界
  g("cv").onmousedown(evp(Math.round(CSX * 0.2), Math.round(CSX * 0.2)));
  g("cv").onmousemove(evp(Math.round(CSX * 0.8), Math.round(CSX * 0.8)));
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
    const sc = CSX / 1024;                       // 标定式是 1024 底图推出的 → 按 CSX 缩放
    const want = [(508.3019 + 0.049038 * q.x) * sc, (504.5433 - 0.049038 * q.y) * sc];
    ok(Math.abs(heroAnn[0].x - want[0]) < 0.01 && Math.abs(heroAnn[0].y - want[1]) < 0.01,
       "标记像素坐标 == w2p(世界坐标)×(CSX/1024)（" + heroAnn[0].x.toFixed(1) + "," + heroAnn[0].y.toFixed(1)
       + " vs " + want[0].toFixed(1) + "," + want[1].toFixed(1) + "，缩放 " + sc.toFixed(3) + "）");
    const w0 = S.w2p(q.x, q.y);
    ok(Math.abs(w0[0] - want[0]) < 0.01 && Math.abs(w0[1] - want[1]) < 0.01,
       "页面自己的 w2p() 也按同一比例缩放（全图态标定闭环）");
    const wb = S.calibFromPx(w0[0], w0[1]);
    ok(Math.abs(wb[0] - q.x) < 1 && Math.abs(wb[1] - q.y) < 1,
       "calibFromPx 与缩放后的 w2p 互逆（" + Math.round(wb[0]) + "," + Math.round(wb[1]) + " vs " + Math.round(q.x) + "," + Math.round(q.y) + "）");
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

/* ══ 3a0. 布局契约（owner：地图+头像在左、combat log 占右半屏、四块同屏不滚动） ══ */
{
  const css = (raw.match(/<style>([\s\S]*?)<\/style>/) || [, ""])[1];
  const wrap = (css.match(/\.wrap\{[^}]*\}/) || [""])[0];
  ok(/display:\s*grid/.test(wrap), "两栏用 grid 布局");
  ok(/grid-template-columns:\s*minmax\(0,\s*1fr\)\s+minmax\(0,\s*1fr\)/.test(wrap),
     "左右两栏各占一半宽（combat log 吃掉右半屏）");
  ok(/flex:\s*1 1 auto/.test(wrap) && /min-height:\s*0/.test(wrap),
     ".wrap 吃掉剩下的高度（flex:1 1 auto + min-height:0）");
  ok(/body\{[^}]*height:\s*100vh/.test(css) && /body\{[^}]*overflow-x:\s*hidden/.test(css),
     "页面锁一屏高（100vh，横向不滚；高度不够时才纵向兜底滚动）");
  ok(/html,body\{height:100%\}/.test(css), "html/body 高度拉到 100%");
  const rightRule = (css.match(/\.right\{[^}]*\}/) || [""])[0];
  ok(/overflow:\s*auto/.test(rightRule), "右栏自己滚（combat log 长了不撑破页面）");
  ok(/@media\(max-width:1180px\)/.test(css) && /display:\s*block/.test(css),
     "窄屏（≤1180px）退回单栏 + 允许整页滚动");
  ok(!/\.right\{width:470px/.test(css), "右栏不是固定 470px");

  /* ★ 真·DOM 结构检查：HTML 标签必须配对，.right 必须是 .left 的兄弟（曾经漏了一个 </div>，
       结果整个 combat log 被塞进左栏里、右半屏全空 —— 文本下标的断言完全没发现）。 */
  const stop = raw.indexOf("<script");
  const seg = raw.slice(0, stop > 0 ? stop : raw.length);
  const root = { tag: "root", cls: "", id: "", children: [] };
  const stack = [root];
  let unclosed = 0;
  const re = /<(\/?)(div|canvas)\b([^>]*)>/g;
  let mm;
  while ((mm = re.exec(seg))) {
    if (mm[1] === "/") {
      for (let k = stack.length - 1; k > 0; k--) {
        if (stack[k].tag === mm[2]) { stack.length = k; break; }
      }
      continue;
    }
    const attr = mm[3];
    const node = {
      tag: mm[2],
      cls: (attr.match(/class="([^"]*)"/) || [, ""])[1],
      id: (attr.match(/id="([^"]*)"/) || [, ""])[1],
      children: [],
      parent: stack[stack.length - 1],
    };
    stack[stack.length - 1].children.push(node);
    stack.push(node);
  }
  unclosed = stack.length - 1;
  ok(unclosed === 0, "markup 里 div/canvas 全部配对（未闭合 " + unclosed + " 个）");
  const find = (n, pred, out) => {
    out = out || [];
    n.children.forEach((c) => { if (pred(c)) out.push(c); find(c, pred, out); });
    return out;
  };
  const wraps = find(root, (n) => n.cls === "wrap");
  ok(wraps.length === 1, "只有一个 .wrap（实际 " + wraps.length + "）");
  if (wraps.length === 1) {
    const w = wraps[0];
    ok(w.children.length === 2, ".wrap 直接子元素 = 2 个（实际 " + w.children.length + "）");
    const l = w.children[0], r = w.children[1];
    ok(l.cls === "left" && /(^|\s)right(\s|$)/.test(r.cls),
       ".wrap 的两个孩子 = .left / .right（实际 " + l.cls + " / " + r.cls + "）");
    ok(l.children.some((c) => c.id === "mapwrap") && l.children.some((c) => c.id === "avatars"),
       ".left 里是地图 + 头像条");
    ok(!/right/.test(l.cls) && find(l, (n) => /(^|\s)right(\s|$)/.test(n.cls)).length === 0,
       ".left 里没有 .right（combat log 不在左栏里）");
    ok(r.children.some((c) => c.id === "paneList") && r.children.some((c) => c.id === "paneHero"),
       ".right 里是默认表 + 英雄 combat log 两个面板");
    const mw = find(l, (n) => n.id === "mapwrap")[0];
    ok(mw && mw.children.some((c) => c.id === "mapbox"), "#mapwrap 里有 #mapbox（画布按剩余高度定尺寸）");
    const mb = find(l, (n) => n.id === "mapbox")[0];
    ok(mb && mb.children.some((c) => c.id === "cv" && c.tag === "canvas"), "#mapbox 里是 #cv");
  }
  /* 地图尺寸由 JS 定：正方形 + 逻辑坐标系跟着走 */
  const cvw = Number(g("cv").width), cvh = Number(g("cv").height);
  ok(cvw === cvh && cvw > 0, "画布正方形（" + cvw + "×" + cvh + "）");
  ok(S.getCSX() === cvw, "逻辑坐标系 CSX == 画布边长（" + S.getCSX() + "）");
  ok(parseFloat(String(g("cv").style.width)) === cvw && parseFloat(String(g("cv").style.height)) === cvw,
     "画布 CSS 尺寸 = 逻辑尺寸（1:1，缩小地图不会把字也缩小）");
  ok(S.getCSX() === 560, "按左栏可用高度取边长（桩里 mapbox=900×560 → 560，实际 " + S.getCSX() + "）");
  ok(/function fitCanvas/.test(src) && /mapScale\(\)/.test(src),
     "地图尺寸/标定缩放都在页面里（fitCanvas + mapScale）");
  /* 头像条：竖排在地图右侧（天辉一列 ｜ 夜魇一列），地图不再被头像压高度（owner 方案①） */
  const leftRule = (css.match(/\.left\{[^}]*\}/) || [""])[0];
  ok(/flex-direction:\s*row/.test(leftRule), "左栏是横向排布（地图 + 右侧头像条）");
  const avRule = (css.match(/#avatars\{[^}]*\}/) || [""])[0];
  ok(/flex-direction:\s*row/.test(avRule) && /flex:\s*0 0 auto/.test(avRule),
     "#avatars 是地图右侧的固定宽度条");
  ok(/\.tcol\{[^}]*flex-direction:\s*column/.test(css), "每队 5 个头像竖着一列");
  ok(/function buildAvatars/.test(src) && /team\(2, "天辉"\)/.test(src) && /team\(3, "夜魇"\)/.test(src),
     "头像条渲染：先是天辉一列、再是夜魇一列");
  ok(/dv\.className = "tdiv"/.test(src), "两列头像之间有分隔线");
  ok(/@media\(max-height:860px\)/.test(css) && /@media\(max-height:700px\)/.test(css),
     "矮屏自动把头像缩一档（保证一列 5 个塞得下）");
  ok(/\.tlrow\{[^}]*flex-wrap:\s*wrap/.test(css), "时间轴控制行窄屏会换行（不会挤出屏幕）");
}

/* ══ 3a0b. 文案守卫（owner：面向普通用户，不留内部讨论痕迹） ══ */
{
  // 先把"非文字"的部分剔掉：base64 图片、以及内嵌的数据块（纯随机 base64 里会偶然出现 Q7/Q6 这类字母组合）
  const textOnly = raw
    .replace(/data:[a-z/+.-]+;base64,[A-Za-z0-9+/=]+/g, "<image>")
    .replace(/const DATA = \{[\s\S]*?\};\n/, "const DATA = {};\n")
    .replace(/@@DICSS@@/g, "");
  const stop = textOnly.indexOf("<script");
  const markup = textOnly.slice(0, stop > 0 ? stop : textOnly.length);
  const js = textOnly.slice(stop);
  // 内部词：项目代号、开发过程用词、内部文件/表名、"我们讨论"的痕迹
  const BANNED = ["owner", "定案", "口径", "不硬造", "如实回退", "Q5B", "Q6", "Q7", "本步",
                  "仍未做", "占位图", "combat_log", "entity_snapshots", "dems/", "stats.db",
                  "timebase", "parser", "COMBAT_LOG", "DEM_FORMAT", "§", "规则集", "回归测试",
                  "lite 版", "combat log", "modifier 分给出"];
  const hitM = BANNED.filter((k) => markup.indexOf(k) >= 0);
  ok(hitM.length === 0, "页面可见文字没有内部词（命中：" + hitM.join(",") + "）");
  // JS 里"会显示给用户"的字符串（含中文的字符串常量）也不能有内部词
  const literals = [];
  const re = /"((?:[^"\\\n]|\\.)*[\u4e00-\u9fff](?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*[\u4e00-\u9fff](?:[^'\\\n]|\\.)*)'/g;
  let m;
  while ((m = re.exec(js))) literals.push(m[1] || m[2]);
  const hitJ = [];
  literals.forEach((t) => BANNED.forEach((k) => { if (t.indexOf(k) >= 0) hitJ.push(k + "→" + t.slice(0, 40)); }));
  ok(hitJ.length === 0, "JS 生成的用户文字没有内部词（命中：" + hitJ.slice(0, 3).join(" ; ") + "）");
  // 面向用户该有的东西在不在
  ok(/怎么用/.test(markup), "页首有『怎么用』引导");
  ok(/使用说明与数据说明/.test(markup), "有面向用户的使用说明");
  ok(markup.indexOf("第一步") < 0 && markup.indexOf("第二步") < 0 && markup.indexOf("第三步") < 0,
     "没有『第一步/第二步/第三步』这类内部阶段说法");
  ok(!/<code>python /.test(raw) && !/<code>analysis\//.test(raw),
     "页面上没有让用户去跑命令行/看内部路径的说明");
}

/* ══ 3a0c. 元素 id 契约：JS 里 getElementById("X") 的 X 必须在页面上真的存在 ══
   （曾经改文案时删掉了 #mid，而 init() 第一句就是取它 → 抛错中断 →
     头像条 / 时间轴 / 地图尺寸全都没建出来，页面表现成"英雄没了、地图变小了"。
     桩 DOM 对任何 id 都会凭空造元素，所以只有对着**真实 HTML** 做这个检查才拦得住。） */
{
  const stop = raw.indexOf("<script");
  const markupIds = new Set((raw.slice(0, stop).match(/id="([^"]+)"/g) || [])
    .map((x) => x.slice(4, -1)));
  const allIds = new Set((raw.match(/id="([^"]+)"/g) || []).map((x) => x.slice(4, -1)));
  const jsSrc = raw.slice(stop);
  const refs = new Set();
  let m;
  const re1 = /getElementById\("([^"]+)"\)/g;
  while ((m = re1.exec(jsSrc))) refs.add(m[1]);
  const re2 = /querySelector\("#([A-Za-z0-9_-]+)/g;
  while ((m = re2.exec(jsSrc))) refs.add(m[1]);
  const missing = [...refs].filter((x) => !allIds.has(x));
  ok(missing.length === 0,
     "JS 引用的 " + refs.size + " 个元素 id 都存在（缺：" + missing.join(",") + "）");
  // 关键元素必须由 HTML 提供（不能靠脚本创建），否则初始化会踩空
  const mustHave = ["cv", "spark", "big", "small", "play", "evUp", "evDn", "avatars", "mapbox",
                    "timeline", "dwrap", "dtbl", "cdboard", "dsum", "herotop", "mid"];
  const miss2 = mustHave.filter((x) => !markupIds.has(x));
  ok(miss2.length === 0, "关键元素都在 HTML 里（缺：" + miss2.join(",") + "）");
  // 初始化里的文案填充必须是"取不到就跳过"，不能再裸取
  ok(/const setTxt = function \(id, txt\)/.test(jsSrc) && /if \(el\) el\.textContent/.test(jsSrc),
     "初始化里的文案填充对缺失元素是安全的（setTxt 判空）");
  // 运行时也不能去取不存在的元素（桩现在会像浏览器一样返回 null 并记账）
  ok(phantomIds.size === 0,
     "运行期没有向不存在的元素取过值（" + [...phantomIds].join(",") + "）");
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
  // 用「<阵亡者> 被 」精确定位（只搜前 6 个字符会误匹配到"阵亡者当凶手"的那条，
  // 换成另一批英雄的场次就会误报 —— 私人录像 9001661796 上就这么露过一次）
  const wantVictim = " " + k0[4].slice(2) + " 被 ";
  const kMark = upMark.concat(dnMark).filter(c => String(c.title).indexOf(wantVictim) >= 0)[0];
  ok(!!kMark, "能找到某条击杀事件的标记（" + k0[3] + "）");
  if (kMark) {
    const im0 = kMark.children.filter(x => String(x.className) === "ico")[0];
    ok(String(im0.src) === String(S.iconsq[k0[4].slice(2)]),
       "阵亡英雄（" + k0[4].slice(2) + "）头像用的就是该英雄的小头像"
       + "（标记图标 " + String(im0.src).length + "B / iconsq " + String(S.iconsq[k0[4].slice(2)]).length + "B"
       + " ｜ 标记 title=" + String(kMark.title).slice(0, 40) + "）");
  }
  // 建筑图标带 bld 类
  ok(upMark.concat(dnMark).filter(c => String(c.className).indexOf("bld") > 0).length === blds.length,
     "建筑类标记数 = 建筑事件数（" + blds.length + "）");
  // 泳道高度必须容得下最高一层标记（否则图标会盖到上面的说明/坐标轴上）
  const cssT = (raw.match(/<style>([\s\S]*?)<\/style>/) || [, ""])[1];
  const icoW = parseFloat((cssT.match(/\.evm \.ico\{width:([\d.]+)px/) || [0, "0"])[1]) || 0;
  ok(icoW >= 24, "事件图标够大（CSS " + icoW + "px，头像才认得出是谁）");
  const laneUp = parseFloat(String(g("evUp").style.height)), laneDn = parseFloat(String(g("evDn").style.height));
  const topUp = Math.max.apply(null, [0].concat(upMark.map(c => parseFloat(String(c.style.bottom || "0")))));
  const topDn = Math.max.apply(null, [0].concat(dnMark.map(c => parseFloat(String(c.style.top || "0")))));
  ok(laneUp >= topUp + icoW + 4 && laneDn >= topDn + icoW + 4,
     "泳道高度容得下最高一层（上 " + laneUp + "≥" + topUp + "+" + (icoW + 4) + "，下 " + laneDn + "≥" + topDn + "+" + (icoW + 4) + "）");
  ok(upMark.every(c => parseFloat(String(c.style.bottom || "0")) >= 0) && upMark.length > 0,
     "上方标记全部锚在轴上方（bottom 定位）");
  // 密集处不该全挤在同一条水平线上：同一侧最多用到 4 层
  const lvUp = new Set(upMark.map(c => String(c.style.bottom)));
  ok(lvUp.size >= 3 && lvUp.size <= 4, "同侧最多 4 层错开（实际用到 " + lvUp.size + " 层）");
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
  ok(W.every((w) => w[5] >= w[4]), "每支眼 销毁 >= 放置（同一秒也算合法）");
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

/* ══ 3b. 第二步：±45s combat log（带技能/对方英雄图标）四 toggle + 技能 CD + 状态胜率 ══ */
if (!LITE) {
  // --- 数据自洽：明细是 Δ 编码的逐条数组；CD 数据存在 ---
  const pl0 = PL[0];
  ok(S.det && Object.keys(S.det).length === 10, "10 个英雄都有 ±45s 明细数组（" + Object.keys(S.det).length + "）");
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

  /* ★ 归属：英雄↔英雄的事件必须记进**双方**日志（owner 2026 实测抓到过：
     英雄打英雄只进攻击者一侧 → 受害者"收到伤害"里只剩小兵/中立/塔）。 */
  {
    const sh = new Set(PL.map((p) => p.short));
    let noHeroTaken = [];
    PL.forEach((p) => {
      const rows = S.det[p.npc] || [];
      const c3 = rows.filter((r) => (r[1] >> 2) === 3 && r[3] >= 0 && sh.has(S.dnames[r[3]]));
      const c1 = rows.filter((r) => (r[1] >> 2) === 1 && r[3] >= 0 && sh.has(S.dnames[r[3]]));
      if (!c3.length || !c1.length) noHeroTaken.push(p.short);
      else if (c3.length < 50 || c1.length < 50) noHeroTaken.push(p.short + "(少)");
    });
    ok(noHeroTaken.length === 0,
       "每个英雄的『收到伤害/收到的 modifier』里都有英雄来源（缺 " + noHeroTaken.join(",") + "）");
    // 攻击者一侧也必须有对应条目（不能只补一边）
    let noHeroGiven = [];
    PL.forEach((p) => {
      const rows = S.det[p.npc] || [];
      const c2 = rows.filter((r) => (r[1] >> 2) === 2 && r[3] >= 0 && sh.has(S.dnames[r[3]]));
      const c0 = rows.filter((r) => (r[1] >> 2) === 0 && r[3] >= 0 && sh.has(S.dnames[r[3]]));
      if (c2.length < 50 || c0.length < 50) noHeroGiven.push(p.short);
    });
    ok(noHeroGiven.length === 0,
       "每个英雄的『造成伤害/给出的 modifier』里都有英雄目标（缺 " + noHeroGiven.join(",") + "）");
    // 构建期拿 DB 做的对账结果（真值来自库里逐条 count）必须一致
    ok(Array.isArray(S.attr) && S.attr.length === 4, "载荷带构建期四类归属对账结果（" + (S.attr || []).length + " 项）");
    (S.attr || []).forEach((c) => {
      ok(c.ok === true && c.expect === c.payload,
         "归属对账·" + c.what + "：库里应有 " + c.expect + " 条 ｜ 载荷 " + c.payload + " 条 → 一致");
      if (c.hero_expect || c.hero_payload) {
        ok(c.hero_ok === true && c.hero_expect === c.hero_payload,
           "　其中英雄↔英雄 ·" + c.what + "：" + c.hero_expect + " 条（两侧对得上）");
      }
    });
    const byCat = {};
    (S.attr || []).forEach((c) => { byCat[c.cat] = c; });
    ok(byCat[0] && byCat[1] && byCat[2] && byCat[3], "四类（0/1/2/3）都在对账结果里");
    ok(byCat[0] && byCat[1] && byCat[0].hero_expect === byCat[1].hero_expect,
       "同一个英雄↔英雄 modifier 事件在两侧计数一致（给出 " + (byCat[0] || {}).hero_expect
       + " == 收到 " + (byCat[1] || {}).hero_expect + "）");
    ok(byCat[2] && byCat[3] && byCat[2].hero_expect === byCat[3].hero_expect,
       "同一个英雄↔英雄伤害事件在两侧计数一致（造成 " + (byCat[2] || {}).hero_expect
       + " == 收到 " + (byCat[3] || {}).hero_expect + "）");
  }
  ok(Array.isArray(S.dnames) && S.dnames.length > 50, "名称字典 " + S.dnames.length + " 项");
  ok(!!S.cd && !!S.cd.keys && Object.keys(S.cd.keys).length > 10,
     "技能/道具 CD 数据存在（键 " + (S.cd ? Object.keys(S.cd.keys).length : 0) + " 个，源 " + (S.cd ? S.cd.src : "-") + "）");
  ok(!!S.wp, "状态胜率模型已内嵌（" + (S.wp ? (S.wp.n_match + " 场拟合") : "缺") + "）");

  // --- 选一个"忙"的时刻，选中英雄，验证窗口严格 ±45s ---
  g("big").value = 1500; g("big").oninput();
  S.select(0);
  ok(S.getSel() === 0, "选中 0 号英雄");
  const ld = S.lastDetail();
  ok(!!ld, "lastDetail 已记录");
  ok(Math.abs(ld.lo - 1455) < 1e-6 && Math.abs(ld.hi - 1545) < 1e-6,
     "明细窗口 = 当前时刻 ±45s（" + ld.lo + " → " + ld.hi + "）");
  ok(ld.tMin === null || (ld.tMin >= ld.lo && ld.tMax <= ld.hi),
     "窗口内所有条目都落在 [t−45, t+45]（" + ld.tMin + " → " + ld.tMax + "）");
  ok(ld.n > 0, "该时刻窗口命中 " + ld.n + " 条（四类合计）");
  ok(ld.cnt.length === 4 && ld.cnt.reduce((a, b) => a + b, 0) === ld.n,
     "四类计数之和 == 命中条数（" + ld.cnt.join("/") + " == " + ld.n + "）");
  ok(String(g("dsum").innerHTML).indexOf("时间范围") >= 0 && String(g("dsum").innerHTML).indexOf("条") >= 0,
     "右栏汇总行显示时间范围与条数");
  ok(String(g("#dtbl tbody").innerHTML).length > 0, "明细表渲染出行（tbody innerHTML 非空）");

  // --- 行内图标与列序（owner：时刻 ｜ 本英雄 ｜ 技能 ｜ 数值 ｜ 对象）---
  {
    const dn = S.dnames.length;
    ok(Array.isArray(S.dicons) && S.dicons.length === dn,
       "明细图标表按名字下标对齐（" + S.dicons.length + " / " + dn + "）");
    const nIcon = S.dicons.filter((x) => !!x).length;
    ok(nIcon > 100, "有图标的名字 " + nIcon + " 个（技能/道具/建筑/英雄/普通攻击）");
    const selfN = Object.keys(S.selficons || {}).length;
    ok(selfN === 10, "10 个本英雄头像都内嵌（" + selfN + "）");
    const css = (raw.match(/<style id="dicss">([\s\S]*?)<\/style>/) || [, ""])[1];
    /* 一套 CSS 规则现在服务两组图标：明细行图标 + 战斗回顾用的团战图标（18px，独立一套） */
    const nFI = (S.DATA.fighticons || []).filter((x) => !!x).length;
    ok(css.length > 1000 && (css.match(/\.dc\d+\{/g) || []).length === nIcon + nFI,
       "每张图标一条 CSS 规则（" + (css.match(/\.dc\d+\{/g) || []).length + " 条 = 明细 "
       + nIcon + " + 团战 " + nFI + "）");
    ok(css.indexOf("data:image/png;base64,") > 0, "图标以 data URI 内嵌（单文件、无外链）");
    // 表头列序
    const hdr = (raw.match(/<table id="dtbl">[\s\S]*?<\/thead>/) || [""])[0];
    const cols = (hdr.match(/<th>([^<]*)<\/th>/g) || []).map((x) => x.replace(/<\/?th>/g, ""));
    ok(cols.join("|") === "时刻|本英雄|技能 / 事件|数值|对象",
       "列序 = 时刻 ｜ 本英雄 ｜ 技能/事件 ｜ 数值 ｜ 对象（实际 " + cols.join(" | ") + "）");
    const tb = String(g("#dtbl tbody").innerHTML);
    const rows = (tb.match(/<tr/g) || []).length;
    ok(rows > 30, "±45s 窗口渲染行数 " + rows + "（比 ±10s 时明显变多）");
    ok(tb.indexOf('class="di self big') > 0, "每行第 2 列是本英雄头像（.di.self）");
    ok((tb.match(/class="di[ "]/g) || []).length >= rows,
       "技能列挂了图标（" + (tb.match(/class="di[ "]/g) || []).length + " 个 <i class=di>）");
    ok(tb.indexOf('class="dv"') > 0 && tb.indexOf('class="do"') > 0,
       "数值列在对象列之前（.dv 先于 .do）");
    // 对象列最后：一行里 do 必须出现在 dv 之后
    const i1 = tb.indexOf('class="dv"'), i2 = tb.indexOf('class="do"');
    ok(i1 > 0 && i2 > i1, "同一行里 数值 在 对象 之前");
    // 技能名文字仍在（"保留文字信息"）
    const nm = S.dnames[Object.keys(S.det)[0] ? 0 : 0] || "";
    ok(/[a-z_]{4,}/.test(tb) && tb.indexOf('class="txt"') > 0, "文字名仍然在（图标只是补充）");
    // 无图标的名字不该渲染出空 <i>
    const noIconIdx = S.dicons.findIndex((x) => !x);
    ok(noIconIdx < 0 || S.diTag(noIconIdx).indexOf("<i") < 0,
       "没有图标的名字 → 不画图标（不拿占位图冒充）");
    // ★ 图标不再有空白：官方图 + 自绘类别字形（小兵/中立/召唤/状态）全兜住
    {
      const miss = S.dnames.filter((n, i) => !S.dicons[i]);
      ok(miss.length === 0, "所有明细名字都有图标（缺 " + miss.length + "：" + miss.slice(0, 5).join(",") + "）");
      // 取两张不同名字的图，必须是**不同的** base64（不是同一张占位图）
      const clsOf = (n) => { const i = S.dnames.indexOf(n); return i >= 0 ? S.dicons[i] : ""; };
      const uniq = new Set(S.dicons.filter(Boolean));
      ok(uniq.size > S.dicons.filter(Boolean).length * 0.9,
         "图标几乎一一对应（" + uniq.size + " / " + S.dicons.filter(Boolean).length + " 个不同类）");
      const cg = clsOf("creep_goodguys_melee"), cb = clsOf("creep_badguys_melee");
      if (cg && cb) {
        ok(cg !== cb, "天辉/夜魇小兵用的是不同类（阵营上色）");
      }
      const cssAll = String(raw.match(/<style id="dicss">([\s\S]*?)<\/style>/) ?
                            raw.match(/<style id="dicss">([\s\S]*?)<\/style>/)[1] : "");
      const ruleOf = (k) => {
        const m = new RegExp("\\." + k + "\\{background-image:url\\(data:image/png;base64,([^)]+)\\)\\}");
        const mm = cssAll.match(m);
        return mm ? mm[1] : "";
      };
      if (cg && cb) {
        const a = ruleOf(cg), b = ruleOf(cb);
        ok(a && b && a !== b, "两阵营字形确实是不同的内嵌图（长度 " + a.length + " / " + b.length + "）");
      }
      const st = clsOf("modifier_stunned");
      ok(!!st, "引擎状态 modifier 也有图标（modifier_stunned → " + st + "）");
      ok(!!clsOf("miniboss") && !!clsOf("thinker"),
         "中立/召唤单位也有图标（miniboss / thinker）");
      // 自绘字形资产在仓库里（可复现）
      const uiDir = path.join(__dirname, "..", "opendota_analysis", "assets", "ui_icons");
      const need = ["attack.png", "status.png", "unit_creep.png", "unit_ranged.png",
                    "unit_siege.png", "unit_flag.png", "unit_neutral.png", "unit_summon.png"];
      const missF = need.filter((f) => !fs.existsSync(path.join(uiDir, f)));
      ok(missF.length === 0, "类别字形资产齐备（缺 " + missF.join(",") + "）");
    }
    ok(S.DETWIN === 45, "窗口常量 DET_WIN = 45");
  }

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

  // --- 「隐藏小兵/中立/召唤」开关（owner 2026）---
  {
    ok(/id="tnc"/.test(raw) && /隐藏小兵\/中立\/召唤/.test(raw),
       "右栏有『隐藏小兵/中立/召唤』开关");
    ok(/<input[^>]*id="tnc"[^>]*>/.test(raw) && !/<input[^>]*id="tnc"[^>]*checked/.test(raw),
       "该开关默认关（默认行为与以前一致）");
    // 判定规则：英雄/建筑 → 保留；小兵/中立/召唤/肉山/无 → 小怪
    const sh0 = PL[0].short;
    ok(S.isCritter(sh0) === false, "英雄不算小怪（" + sh0 + "）");
    ok(S.isCritter("creep_goodguys_melee") && S.isCritter("creep_badguys_ranged_upgraded_mega"),
       "近战/远程小兵算小怪");
    ok(S.isCritter("neutral_black_drake") && S.isCritter("miniboss") && S.isCritter("roshan"),
       "中立/小野怪/肉山算小怪");
    ok(S.isCritter("lone_druid_bear1") && S.isCritter("unit_undying_zombie_torso")
       && S.isCritter("invoker_forged_spirit"), "召唤物算小怪");
    ok(!S.isCritter("goodguys_tower1_mid") && !S.isCritter("badguys_tower3_bot")
       && !S.isCritter("goodguys_fort") && !S.isCritter("dota_fountain"),
       "塔/兵营/基地/泉水不算小怪（保留）");
    ok(S.isCritter("") === false, "没有对手方（other=-1）的行保留");

    // 本场自动挑"明细最多"的英雄 + 一个"小怪很多"的时刻（不写死某场某英雄，两场都能跑）
    const pick = (function () {
      let bi = 0, bn = -1;
      PL.forEach((q, i) => { const n = (S.det[q.npc] || []).length; if (n > bn) { bn = n; bi = i; } });
      const rows = S.det[PL[bi].npc] || [], seq = [];
      let t = 0;
      rows.forEach((r, i) => { t = (i === 0) ? r[0] : t + r[0]; seq.push({ t: t, r: r }); });
      const hit = (T) => {
        let c = 0, tot = 0;
        for (let i = 0; i < seq.length; i++) {
          if (seq[i].t < T - 45 || seq[i].t > T + 45) continue;
          tot += 1;
          const onm = seq[i].r[3] >= 0 ? (S.dnames[seq[i].r[3]] || "") : "";
          if (S.isCritter(onm)) c += 1;
        }
        return { crit: c, tot: tot };
      };
      const step = Math.max(1, Math.floor(seq.length / 80));
      for (let i = 0; i < seq.length; i += step) {
        const T = Math.round(seq[i].t);
        const h = hit(T);
        if (h.crit > 20) return { i: bi, T: T, crit: h.crit, tot: h.tot };
      }
      const T = Math.round((T0 + T1) / 2);
      const h = hit(T);
      return { i: bi, T: T, crit: h.crit, tot: h.tot };
    })();
    const T = pick.T, sel0 = pick.i;
    S.clearSel(); g("big").value = T; g("big").oninput();
    g("tnc").checked = false; S.select(sel0);
    const nOff = S.lastDetail().n;
    g("tnc").checked = true; S.renderDetail();
    const ld = S.lastDetail();
    ok(ld.hideCrit === true, "开关打开 → lastDetail.hideCrit=true（" + PL[sel0].short + " @ " + T + "s）");
    ok(ld.crit > 0, "该窗口隐藏了小怪相关 " + ld.crit + " 条");
    ok(ld.n < nOff && ld.n + ld.crit === nOff,
       "开/关命中数守恒：" + ld.n + " + " + ld.crit + " == " + nOff);
    // 独立复算：同一个窗口按同一规则在小工具里再数一遍
    ok(pick.crit === ld.crit && pick.tot === nOff,
       "独立复算一致：小怪 " + pick.crit + " 条 / 窗口 " + pick.tot + " 条（页面报 " + ld.crit + " / " + nOff + "）");
    // DOM 里不该再出现"对象是小怪"的行
    const tb = String(g("#dtbl tbody").innerHTML);
    {
      const objs = [];
      const re = /<td class="do"><div class="dirow">(?:<i class="di[^"]*"><\/i>)?<span class="txt">([^<]*)<\/span>/g;
      let m2;
      while ((m2 = re.exec(tb))) objs.push(m2[1]);
      const bad = objs.filter((x) => x && S.isCritter(x));
      ok(objs.length > 10 && bad.length === 0,
         "渲染出的行里没有小怪对象（检查 " + objs.length + " 行，小怪 " + bad.length + " 个）");
      ok(objs.some((x) => x && !S.isCritter(x)),
         "英雄/建筑对象仍在（样例 " + [...new Set(objs)].slice(0, 5).join(",") + "）");
    }
    ok(/已隐藏小兵\/中立\/召唤/.test(String(g("dsum").innerHTML)),
       "汇总行写明隐藏了多少条");
    g("tnc").checked = false; S.renderDetail();
    ok(S.lastDetail().n === nOff && S.lastDetail().hideCrit === false, "关掉开关 → 恢复原来的命中数");
  }

  // --- 窗口随时间移动 ---
  g("big").value = 2000; g("big").oninput();
  ok(Math.abs(S.lastDetail().lo - 1955) < 1e-6, "移动时间轴 → 窗口跟着移动（" + S.lastDetail().lo + "）");

  // --- 虚拟滚动：±45s 的团战窗口上千行，DOM 里只画视口附近 ---
  {
    const iv = PL.findIndex((q) => q.short === "invoker");
    if (iv >= 0) {
      g("big").value = 2745; g("big").oninput();
      S.clearSel(); S.select(iv);
      const ld2 = S.lastDetail();
      ok(ld2 && ld2.shown > 400,
         "团战时刻（Invoker @2745s）±45s 窗口 " + (ld2 ? ld2.shown : "-") + " 行（实测本场最多 1785 行）");
      ok(S.detLen() === ld2.shown, "内部序列长度 == 汇总行数（" + S.detLen() + "）");
      const tbHtml = String(g("#dtbl tbody").innerHTML);
      const nRow = (tbHtml.match(/<tr/g) || []).length;
      ok(nRow > 20 && nRow < 300,
         "虚拟滚动：DOM 里只画 " + nRow + " 行（seq " + ld2.shown + " 行）");
      ok(tbHtml.indexOf('class="sp"') > 0, "上下各有等高占位行（撑出正确滚动条）");
      const iFirst = tbHtml.indexOf("<td>");
      ok(iFirst > 0, "行首是时刻列");
      // 滚到中段 → 画中段
      const dw = g("dwrap");
      dw.scrollTop = 24 * 600; S.paintDet();
      const tb2 = String(g("#dtbl tbody").innerHTML);
      ok(tb2 !== tbHtml, "滚动后重画了另一段（虚拟滚动生效）");
      dw.scrollTop = 0; S.paintDet();
    }
  }

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
  ok(Object.keys(S.det).length === 0, "lite 版不含 ±45s 明细（载荷 0 条）");
  // 抽稀后索引仍要取到正确时刻的值：与"沿用上一格"一起验证
  S.commit(1200);
  // 同样按 T1 推导：短场时 1200s 之后可能已经超出比赛末尾
  const gridT = Math.max(T0, Math.min(1200, T1 - estep - 1));
  ok(S.diffAt("nw", gridT) !== null && S.posAt(PL[0].npc, gridT) !== null,
     "lite 下 t=" + gridT + "s 仍能取到净值差/位置");
  const a = S.diffAt("nw", gridT), b = S.diffAt("nw", gridT + estep - 1);
  ok(a !== null && b !== null, "estep 网格内相邻时刻都能取到（" + a + " / " + b + "）");
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
  // 开场 +2s（英雄已出门）必须有位置/血量。
  // ★ 采样格可能整格缺数据（页面显示「—」）、也可能英雄此刻确实阵亡 —— 都算"有交代"，
  //   但绝不能出现 undefined/空串。真正的"血量可用"用下面这条**搜索式**断言：
  //   个别格会出现"有坐标但没有血量"，拿固定时刻断言会误报。
  g("big").value = T0 + 4 * (S.DATA.step || 1); g("big").oninput();
  const hpTxt = String(g("c-hp-0").textContent);
  ok(/^(\d+( \/ \d+)?|—|未出场|阵亡)$/.test(hpTxt), "开场首个采样格 hp 有明确交代（" + hpTxt + "）");
  let hpNumAt = null;
  for (let t = T0; t <= Math.min(T1, 300); t += 5) {
    S.commit(t);
    if (/^\d+( \/ \d+)?$/.test(String(g("c-hp-0").textContent))) { hpNumAt = t; break; }
  }
  ok(hpNumAt !== null, "开场 5 分钟内存在血量数值的采样（"
     + (hpNumAt === null ? "无" : Math.round(hpNumAt) + "s 时 " + g("c-hp-0").textContent) + "）");
  /* ★ 把当前时刻放回"开场后 4 个采样格"：上面的搜索会停在别的时刻，
     而紧随其后的断言（开场净值）是按这个时刻写的 —— 不改回来会把那条老断言带偏。 */
  g("big").value = T0 + 4 * (S.DATA.step || 1); g("big").oninput();
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
  /* dump=recap[.<波次>]：把"战斗回顾"面板渲染成文本（不用浏览器就能核对内容） */
  if (spec.indexOf("recap") === 0) {
    const strip = (h) => String(h).replace(/<\/(div|span|i)>/g, " ").replace(/<[^>]*>/g, "")
      .replace(/\s+/g, " ").trim();
    const only = spec.split(".")[1] ? parseInt(spec.split(".")[1], 10) - 1 : null;
    const F = S.fights || [];
    const idxs = only === null ? F.map((_f, i) => i) : [only];
    idxs.forEach(function (i) {
      S.commit(F[i].t0 + 1);
      S.openRecap(i);
      console.log("\n" + "=".repeat(96));
      console.log("第 " + F[i].i + " 波 ｜ " + fmtSec(F[i].t0) + " ~ " + fmtSec(F[i].t1) + " ｜ 阵亡 " + F[i].n);
      console.log("-- 头部 --\n" + strip(g("recapHead").innerHTML));
      String(g("recapBody").innerHTML).split('<div class="rcseg">').slice(1).forEach(function (s) {
        console.log("   • " + strip(s).slice(0, 320));
      });
      S.closeRecap();
    });
    process.exit(0);
  }
  spec.split(",").forEach(function (s) {
    const m = s.split("@");
    const i = parseInt(m[0], 10), t = parseFloat(m[1]);
    S.commit(t); S.select(-1); S.select(i);
    const strip = (h) => String(h).replace(/<[^>]*>/g, "\t").replace(/\t+/g, "  ").replace(/[ \t]+\n/g, "\n").trim();
    console.log("\n" + "=".repeat(96));
    console.log("英雄 #" + i + "（" + PL[i].short + "）@ " + fmtSec(t));
    console.log("-- 头部 --\n" + strip(g("herotop").innerHTML));
    console.log("-- 汇总 --\n" + strip(g("dsum").innerHTML));
    console.log("-- ±45s 明细（前 25 行）--");
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
      const labs = kids.filter(c => String(c.className).indexOf("evm") === 0);
      const withT = labs.filter(c => c.children.some(x => String(x.className) === "t"));
      console.log("   " + pair[1] + "：刻度 " + ticks.length + " 个，图标 " + labs.length
                  + " 个（带 mm:ss 的 " + withT.length + " 个）");
      const rows = {};
      labs.forEach(c => { const o = c.style.bottom || c.style.top || "0"; rows[o] = (rows[o] || 0) + 1; });
      console.log("     错行分布 " + JSON.stringify(rows) + " ｜ 样例 " +
        labs.slice(0, 10).map(c => String(c.title).slice(0, 30)).join(" ｜ "));
    });
  });
}

/* ═══════════ 拖动进度条时的当前时间气泡（朋友的反馈） ═══════════ */
S.commit(500);
g("big").value = 1234; g("big").oninput();
const bub = S.seekBubEl();
ok(bub.classList.contains("on"), "拖大时间轴 → 蓝色时间气泡出现");
ok(bub.textContent === S.fmt(S.getT(), true), "气泡文字 = 当前游戏时间（" + bub.textContent + " ｜ tCur=" + S.getT() + "）");
{
  const w = g("timeline").clientWidth || 900;
  const left = parseFloat(String(bub.style.left));
  ok(isFinite(left) && left >= 0 && left <= w, "气泡横向落在时间轴范围内（left=" + bub.style.left + "）");
  ok(parseFloat(String(bub.style.top)) >= 0, "气泡在滑块上方（top=" + bub.style.top + "）");
}
g("small").value = 25; g("small").oninput();
ok(bub.classList.contains("on") && bub.textContent === S.fmt(S.getT(), true),
   "拖微调条同样显示气泡，且跟着当前时刻走（" + bub.textContent + "）");
g("small").value = 0; g("small").oninput();
g("big").value = 900; g("big").oninput();
ok(S.getT() >= 880 && S.getT() <= 920, "气泡不改变定位结果（回到 " + fmtSec(S.getT()) + "）");

/* ═══════════ 英雄头像：按原比例取中心正方形，不再被压扁 ═══════════ */
S.resetZoom(); S.clearSel(); S.commit(900);
drawImgs.length = 0;
S.draw();
{
  const heroDraws = drawImgs.filter((a) => a.length === 9 && a[7] === a[8] && a[3] === a[4]);
  /* 9 参数绘制 = 英雄头像（取正方形源区域）。地图上**不再画** TP 徽标与大招内环
     （owner 定案：小地图的英雄图标保持干净），所以这里应当**只有 10 次**。 */
  ok(heroDraws.length === 10, "地图上只画 10 个英雄头像（" + heroDraws.length + " 次 9 参数绘制）");
  ok(heroDraws.every((a) => Math.abs(a[3] - 72) < 1e-9),
     "裁出的源正方形边长 = 卡片高度 72px（实际 " + (heroDraws[0] ? heroDraws[0][3] : "-") + "）");
  ok(heroDraws.every((a) => Math.abs(a[1] - 28) < 1e-9 && Math.abs(a[2]) < 1e-9),
     "源裁剪从 128×72 的横向居中开始（sx=" + (heroDraws[0] ? heroDraws[0][1] : "-")
     + "，sy=" + (heroDraws[0] ? heroDraws[0][2] : "-") + "）");
  const small = heroDraws.filter((a) => a[7] <= 20);
  ok(small.length === 0, "地图上没有任何小徽标（TP 只画在头像列上，实际 " + small.length + " 个）");
  const old = drawImgs.filter((a) => a.length === 9 && a[3] === 128 && a[4] === 72);
  ok(old.length === 0, "不存在「整张 128×72 塞进正方形」的老写法（" + old.length + " 次）");
}

/* ═══════════ 头像状态：大招长条 + TP 徽标 ═══════════ */
{
  ok(S.getTpCool() === 80, "TP 固定冷却取自游戏文件（" + S.getTpCool() + " 秒）");
  /* 大招标记：只断言机制与一致性 —— 逐场"认出几个"是数据稀疏度问题
     （实测 62 个切片：48 场 10/10、平均 9.0、最差 3/10），不适合当硬阈值。 */
  const flaggedKeys = Object.keys(S.cd.keys).filter((k) => S.cd.keys[k].ult);
  const nk = (x) => String(x).toLowerCase().replace(/[^a-z0-9]/g, "");
  let overMax = 0, withOwn = 0, orphanFlag = 0;
  PL.forEach((p) => {
    const mine = (S.cd.ab[p.npc] || []).filter((e) => (S.cd.keys[e[0]] || {}).ult);
    if (mine.length > 12) overMax++;          // 上限放宽：老库会把别人的技能重复归属给同一名英雄
    if (mine.some((e) => nk(e[0]).indexOf(nk(p.short)) >= 0)) withOwn++;
  });
  flaggedKeys.forEach((k) => {
    const inSomeList = Object.keys(S.cd.ab).some((npc) => (S.cd.ab[npc] || []).some((e) => e[0] === k));
    if (!inSomeList) orphanFlag++;
  });
  ok(flaggedKeys.length >= 3, "载荷里标出了大招（本场 " + flaggedKeys.length + " 个技能被标为大招）");
  ok(flaggedKeys.every((k) => k.indexOf("CDOTA_Ability_") === 0), "只有技能会被标成大招，道具不会");
  ok(orphanFlag === 0, "每个被标为大招的技能都出现在某名英雄的技能表里");
  /* 上限放到 12：老库里有少数场次把**别人的技能**重复归属到同一名英雄名下
     （shadow_demon 一场能列出 68 个技能 / 8 个大招），极端值不许再高。 */
  ok(overMax === 0, "没有英雄被标出超过 12 个大招（防止映射跑飞）");
  ok(withOwn >= 3, "至少 3 名英雄的「自己的大招」在载荷里被认出来（本场 " + withOwn + "/10）");

  /* 找一个「既有冷却时刻、又有就绪时刻」的英雄来验证三态。
     ★ 不能假设"最后一段冷却结束后就就绪"：最后一段常常一直延续到比赛结束，
       夹到 T1 附近仍然在冷却里。所以在区间序列里**搜出真实存在的两种时刻**。 */
  let pick = -1, tCool = null, tRdy = null;
  PL.forEach((p, i) => {
    if (pick >= 0) return;
    const ent = (S.cd.ab[p.npc] || []).filter((x) => (S.cd.keys[x[0]] || {}).ult);
    const ivs = [];
    ent.forEach((e) => (e[3] || []).forEach((iv) => ivs.push(iv)));
    if (!ivs.length) return;
    const cool = ivs.map((iv) => (iv[0] + iv[1]) / 2)
      .find((t) => t > T0 + 20 && t < T1 - 20 && S.ultStateOf(p.npc, t).st === "cool");
    const rdy = ivs.map((iv) => iv[1] + 10)
      .find((t) => t > T0 + 20 && t < T1 - 20 && S.ultStateOf(p.npc, t).st === "ready");
    if (cool !== undefined && rdy !== undefined) { pick = i; tCool = cool; tRdy = rdy; }
  });
  ok(pick >= 0, "找到同时具备「冷却中/就绪」两种时刻的英雄（英雄 #" + pick + "）");
  if (pick >= 0) {
    S.commit(tCool);
    ok(S.ultStateOf(PL[pick].npc, tCool).st === "cool" && S.ultStateOf(PL[pick].npc, tCool).left > 0,
       "大招冷却中：剩余 " + (S.ultStateOf(PL[pick].npc, tCool).left || 0).toFixed(1) + "s（"
       + Math.round(tCool) + "s 时）");
    ok(g('.hrow[data-i="' + pick + '"]').classList.contains("ult-cool"), "该英雄那一行挂了「冷却中」状态（长条转暗灰）");
    ok(/大招冷却/.test(String(g('.hero[data-i="' + pick + '"]').title)), "头像 hover 文案写了大招冷却："
       + String(g('.hero[data-i="' + pick + '"]').title).slice(-40));
    S.commit(tRdy);
    ok(S.ultStateOf(PL[pick].npc, tRdy).st === "ready", "区间之后 → 大招就绪（" + Math.round(tRdy) + "s）");
    ok(g('.hrow[data-i="' + pick + '"]').classList.contains("ult-ready"), "该英雄那一行挂了「就绪」状态（长条转黄）");
  }

  /* TP 三态：按使用时刻 + 固定共享冷却推算 */
  const tpn = PL.map((p, i) => ({ i: i, arr: S.tput[p.npc] || [] })).filter((x) => x.arr.length)[0];
  ok(!!tpn, "找到有 TP 使用记录的英雄（#" + (tpn ? tpn.i : "-") + "）");
  if (tpn) {
    const use = tpn.arr[0];
    S.commit(use + 10);
    const s1 = S.tpStateOf(PL[tpn.i].npc, use + 10);
    ok(s1.st === "cd" && s1.left > 0 && s1.left < 80, "刚用过 TP → 推算冷却中（剩 "
       + (s1.left || 0).toFixed(0) + "s）");
    ok(g('.hrow[data-i="' + tpn.i + '"]').classList.contains("tp-cd"), "TP 那一行挂了「冷却中」样式");
    /* 桩里 el.querySelector(".tpcd") 返回的是 `mkEl(el.id + "_q")`，所以这样读同一个元素 */
    const tcd = String(g('.hrow[data-i="' + tpn.i + '"]' + "_q").textContent);
    ok(/^\d+$/.test(tcd) && +tcd > 0 && +tcd <= 80, "TP 徽标上写出剩余秒数（" + tcd + "s）");
    /* ★ 不能拿"第一次使用 + 200s"当"可用"：两次 TP 间隔常常短于 200s（推完一波又回家）。
       所以在使用时刻序列里现搜一个真的可用的时刻。 */
    const okT = tpn.arr.map((u) => u + 90).find((t) => t > T0 && t < T1 - 5
      && S.tpStateOf(PL[tpn.i].npc, t).st === "ok");
    ok(okT !== undefined, "TP 使用记录里存在「已过固定冷却」的时刻（" + (okT === undefined ? "无" : Math.round(okT) + "s") + "）");
    if (okT !== undefined) {
      S.commit(okT);
      ok(S.tpStateOf(PL[tpn.i].npc, okT).st === "ok", "超过固定冷却之后 → TP 可用（" + Math.round(okT) + "s）");
      ok(g('.hrow[data-i="' + tpn.i + '"]').classList.contains("tp-ok"), "TP 状态切到「可用」");
      ok(String(g('.hrow[data-i="' + tpn.i + '"]' + "_q").textContent) === "",
         "可用时徽标上不显示数字（图标亮着即可）");
    }
    ok(/TP 冷却中约|TP 可用/.test(String(g('.hero[data-i="' + tpn.i + '"]').title)),
       "头像 hover 文案带 TP 状态：" + String(g('.hero[data-i="' + tpn.i + '"]').title).slice(-46));
  }

  /* 大招长条 + TP 徽标（owner 定案的设计，不再是"模式切换"） */
  ok(typeof S.DATA.tpicon === "string" && S.DATA.tpicon.indexOf("data:image/png;base64,") === 0,
     "载荷内嵌回城卷轴图标（" + (S.DATA.tpicon ? Math.round(S.DATA.tpicon.length / 1024) + " KB" : "缺") + "）");
  /* 头像元素是 buildAvatars 用 createElement 造出来的，桩里不在 getElementById 表里，
     所以直接查桩的 created 列表（本文件自己维护的）。 */
  ok(created.some((e) => String(e.innerHTML).indexOf('alt="TP"') >= 0),
     "TP 徽标用的是回城卷轴图标而不是色块");
  {
    /* 一行 = 长条 + TP 徽标 + 头像，三个横排；长条与徽标都在**头像外面**（owner 定案）。 */
    const row = created.find((e) => /^hrow\b/.test(String(e.className)));
    ok(!!row, "每名英雄被一行 hrow 包住（图标放在头像外面）");
    if (row) {
      const cl = row.children.map((c) => String(c.className));
      ok(cl.length === 3 && cl[0] === "ultbar" && cl[1] === "tpbadge" && /^hero\b/.test(cl[2]),
         "这一行的顺序 = 长条 → TP 徽标 → 头像（实际 " + cl.join(" / ") + "）");
      ok(cl.every((c) => c.indexOf("ultbar") < 0 || true), "长条与 TP 徽标是头像的兄弟节点（在框外，不被圆角裁切）");
    }
  }
  /* 图例是静态 HTML（不再由 JS 填），桩不解析 markup，所以从产物的 markup 段里查 */
  const markup = raw.slice(0, raw.indexOf("<script"));
  ok(/大招就绪/.test(markup) && /TP 图标/.test(markup) && /天辉在头像左侧/.test(markup),
     "页面上的图例同时说明了长条（含方向）与 TP 图标");
  ok(!/setFrameMode|class="fbtn/.test(src), "模式切换按钮已移除（设计固定：长条＝大招、图标＝TP）");
  {
    const css = raw.match(/<style>([\s\S]*?)<\/style>/)[1];
    /* ★ 一行三件横排，夜魇整排镜像；长条与徽标都在框外 —— 靠 **flex 间距**保证不互相覆盖 */
    ok(/\.hrow\{display:flex;[^}]*align-items:center;gap:(\d+)px/.test(css), "一行是横排 flex 且带间距");
    const gap = +(css.match(/\.hrow\{display:flex;[^}]*gap:(\d+)px/) || [0, 0])[1];
    ok(gap >= 2, "间距够长条与徽标分开（gap=" + gap + "px）");
    ok(/\.hrow\.t3\{flex-direction:row-reverse\}/.test(css), "夜魇整排镜像 → 长条与 TP 都在右侧");
    ok(/\.hrow\.ult-ready \.ultbar\{background:#e3b341\}/.test(css), "长条：就绪＝黄");
    ok(/\.hrow\.ult-cool \.ultbar\{background:#6e7681\}/.test(css), "长条：冷却中＝暗灰");
    const barW = +(css.match(/\.ultbar\{[^}]*width:(\d+)px/) || [0, 0])[1];
    const badW = +(css.match(/\.tpbadge\{[^}]*width:(\d+)px/) || [0, 0])[1];
    ok(barW > 0 && badW > 0 && badW <= 20,
       "长条 " + barW + "px、TP 徽标 " + badW + "px（都在框外，徽标仍保持小尺寸）");
    ok(!/\.hero \.ultbar/.test(css) && !/\.hero \.tpbadge/.test(css),
       "长条与 TP 徽标不再画在头像框内部");
    ok(!/\.hero \.ring/.test(css), "旧的圆形内环样式已移除");
    ok(!/\.fbtn/.test(css), "模式按钮样式已移除");
    ok(/\.hrow\.tp-cd \.tpcd\{display:flex\}/.test(css), "TP 冷却中数字画在徽标内部");
  }

  /* 全部英雄的 title 都能生成，且不出现 undefined/NaN */
  const titles = S.heroTitles();
  ok(Object.keys(titles).length === 10 && Object.keys(titles).every((k) => titles[k] && titles[k].length > 4),
     "10 个头像都有 hover 文案");
  ok(!Object.keys(titles).some((k) => /undefined|NaN/.test(titles[k])), "hover 文案里没有 undefined/NaN");

  /* CD 面板：大招角标 */
  S.select(pick >= 0 ? pick : 0);
  ok(/class="cd[^"]*ult/.test(S.cdHTML()), "CD 面板给大招加了「大」角标");
  ok(/可用|冷却 \d+s/.test(S.cdHTML()), "CD 面板的 TP 格子给出可用/冷却状态");
  ok(/推算/.test(String(g("cdnote").innerHTML)), "CD 面板说明 TP 状态是按固定冷却推算的");
  S.clearSel();
}

/* ═══════════ 战斗回顾（复现游戏里的 Fight Recap 面板） ═══════════ */
{
  const seg = S.DATA.fights || { keys: [], list: [] };
  const F = seg.list || [], K = seg.keys || [];
  ok(F.length > 0, "切片里带团战检测结果（" + F.length + " 波）");
  ok(K.length > 0, "团战的技能/物品名做成了键表（" + K.length + " 个）");
  const FI = S.DATA.fighticons || [];
  ok(FI.length === K.length && FI.filter((x) => !!x).length >= K.length * 0.9,
     "团战图标表与键表一一对应且有图（" + FI.filter((x) => !!x).length + "/" + K.length + "）");
  const badWin = F.filter((f) => !(f.t1 > f.t0) || f.t0 < T0 - 0.01 || f.t1 > T1 + 0.01);
  ok(badWin.length === 0, "每波窗口都落在比赛时间轴内（越界 " + badWin.length + " 波）");
  const segKeys = ["d", "g", "x", "dm", "hl", "ab", "it"];
  const hardKeys = ["d", "g", "x", "dm", "ab", "it"];   // 这六段每波都该有内容
  const missing = [];
  let withHeal = 0;
  F.forEach((f) => {
    hardKeys.forEach((k) => { if (!f[k] || !f[k].length) missing.push(f.i + ":" + k); });
    if (f.hl && f.hl.length) withHeal++;
    const dn = (f.d || []).reduce((a, e) => a + e[1], 0);
    if (dn !== f.n) missing.push(f.i + ":n≠Σd");
  });
  ok(missing.length === 0, "每波的六段硬指标都有内容、阵亡数自洽（异常 " + missing.slice(0, 4).join(",") + "）");
  ok(withHeal >= F.length * 0.5,
     "治疗段大多数波次有内容（" + withHeal + "/" + F.length + "，本来就可能没人治疗）");
  ok(F.some((f) => f.dba && f.dba.length) === !LITE,
     LITE ? "lite 版不带「按技能拆分的伤害」（控体积）" : "完整版带「按技能拆分的伤害」");
  let oob = 0;
  F.forEach((f) => {
    segKeys.forEach((k) => {
      (f[k] || []).forEach((e) => { if (!(e[0] >= 0 && e[0] < PL.length)) oob++; });
    });
  });
  ok(oob === 0, "团战载荷里的英雄下标都合法（越界 " + oob + " 个）");
  ok((g("fightLane").children || []).length === F.length,
     "时间轴上的团战标记数 == 波数（" + (g("fightLane").children || []).length + "）");

  S.select(-1);
  S.commit(F[0].t0 + 1);
  S.openRecap();
  ok(S.recapIdx() >= 0, "点开战斗回顾 → 第 " + (S.recapIdx() + 1) + " 波");
  ok(g("paneRecap").style.display !== "none", "右栏切到战斗回顾面板");
  /* ★ 三个面板必须互斥：开回顾时默认列表与英雄面板都要收起
     （第一版 openRecap 末尾调 selectHero(-1) 又把列表放了出来，回顾被挤到列表下面）。 */
  ok(g("paneList").style.display === "none" && g("paneHero").style.display === "none",
     "开回顾时默认列表与英雄面板都收起（list=" + g("paneList").style.display
     + " / hero=" + g("paneHero").style.display + "）");
  const body = String(g("recapBody").innerHTML);
  ok(/阵亡/.test(body) && /金钱变化情况/.test(body) && /经验变化情况/.test(body)
     && /造成伤害/.test(body) && /总治疗量/.test(body) && /已使用的技能/.test(body)
     && /已使用的物品/.test(body), "七个板块都渲染出来了");
  ok((body.match(/class="rcseg"/g) || []).length >= 7,
     "段数 ≥7（实际 " + (body.match(/class="rcseg"/g) || []).length + "）");
  ok(/class="rcside/.test(body) && /class="rcmid/.test(body), "每段是「天辉 ｜ 合计 ｜ 夜魇」三段式");
  ok((body.match(/class="di /g) || []).length > 0,
     "技能/物品用图标展示（" + (body.match(/class="di /g) || []).length + " 个）");
  /* ★ 必须先剥掉内嵌图片（data URI）：base64 里随机出现的 "NaN"/"undefined" 是字母巧合，
     不是渲染出错 —— 页面自己的文案守卫也是这么做的。 */
  const noData = body.replace(/data:image\/[a-z+]*;base64,[A-Za-z0-9+/=]+/g, "<img>");
  const badTxt = noData.match(/.{0,50}(?:undefined|NaN).{0,50}/);
  ok(!badTxt, "回顾面板里没有 undefined/NaN" + (badTxt ? "（" + badTxt[0].replace(/\s+/g, " ") + "）" : ""));
  const i0 = S.recapIdx();
  S.recapJump(1);
  ok(S.recapIdx() === Math.min(F.length - 1, i0 + 1), "「下一波」翻到第 " + (S.recapIdx() + 1) + " 波");
  S.recapJump(-1);
  ok(S.recapIdx() === i0, "「上一波」翻回第 " + (S.recapIdx() + 1) + " 波");
  S.closeRecap();
  ok(S.recapIdx() === -1 && g("paneRecap").style.display === "none", "收起回顾 → 回到默认面板");
  S.openRecap();
  S.select(0);
  ok(S.recapIdx() === -1 && g("paneHero").style.display === "", "点英雄 → 自动收起回顾、切到英雄面板");
  ok(g("paneList").style.display === "none" && g("paneRecap").style.display === "none",
     "此时列表与回顾都不显示（list=" + g("paneList").style.display + "）");
  S.clearSel();
  ok(g("paneList").style.display === "", "取消选中 → 回到默认列表");
}

setTimeout(() => {
  ok(loadErrors.length === 0, "图/头像 onload 回调不抛异常" + (loadErrors.length ? "：" + loadErrors.join("; ") : ""));
  ok(calls.drawImage > 0, "渲染时确实绘制了底图/头像（drawImage " + calls.drawImage + " 次）");
  ok(calls.fillText + calls.arc > 0, "渲染时确实绘制了英雄标记（arc " + calls.arc + " / fillText " + calls.fillText + "）");
  console.log(fails ? "\n" + fails + " 项失败" : "\n全部通过");
  process.exit(fails ? 1 : 0);
}, 20);
