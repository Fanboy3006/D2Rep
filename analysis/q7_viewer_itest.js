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
const HTML = path.join(__dirname, "output_review", "q7_replay_" + MID + ".html");
if (!fs.existsSync(HTML)) { console.error("缺 " + HTML + "（先跑 build_q7_html.py）"); process.exit(1); }
const raw = fs.readFileSync(HTML, "utf8");
const src = raw.match(/<script>([\s\S]*?)<\/script>/)[1];

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
  const e = {
    id, style: {}, dataset: {}, innerHTML: "", textContent: "", className: "", title: "",
    _v: "0", checked: true, _h: {},
    classList: {
      _s: new Set(),
      toggle(c, on) { if (on === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); } else if (on) this._s.add(c); else this._s.delete(c); },
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); },
    },
    addEventListener(t, f) { (e._h[t] = e._h[t] || []).push(f); },
    appendChild() {}, removeChild() {},
    querySelector() { return mkEl(id + "_q"); },
    querySelectorAll() { return []; },
    getContext() { return mkctx(); },
    getBoundingClientRect() { return { left: 0, top: 0, width: 1024, height: 1024 }; },    setAttribute(k, v) { e["_attr_" + k] = v; },
    getAttribute(k) { return e["_attr_" + k]; },
    focus() {}, blur() {}, click() { if (e.onclick) e.onclick({}); },
  };
  Object.defineProperty(e, "value", { get() { return e._v; }, set(v) { e._v = v; } });
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
  diffEnd: function(){ return [DIFF.nw[D-1], DIFF.cg[D-1], DIFF.cx[D-1]]; }
};
`);

console.log("[Q7 回放浏览器 · 交互回归] " + path.basename(HTML));
const S = globalThis.__t;
const D = S.D, T0 = S.T0, T1 = S.T1, PL = S.PL;

/* ══ 1. 内嵌数据自洽 ══ */
{
  ok(PL.length === 10, "玩家数 = 10（实际 " + PL.length + "）");
  ok(PL.filter((p) => p.team === 2).length === 5 && PL.filter((p) => p.team === 3).length === 5, "天辉/夜魇各 5 人");
  ok(Object.keys(S.ICONS).length === 10, "10 个英雄头像内嵌（实际 " + Object.keys(S.ICONS).length + "）");
  let lenOk = true, anyPos = true;
  PL.forEach((p) => {
    const P = S.POS[p.npc];
    if (!P || P.x.length !== D || P.y.length !== D || P.hp.length !== D) lenOk = false;
    if (!P || !P.x.some((v) => v !== null)) anyPos = false;
  });
  ok(lenOk, "每英雄 x/y/hp 数组长度 == D(" + D + ")");
  ok(anyPos, "每英雄都至少有位置采样");
  let cov = 0;
  PL.forEach((p) => { cov += S.POS[p.npc].x.filter((v) => v !== null).length; });
  ok(cov / 10 / D > 0.97, "位置覆盖率 " + (100 * cov / 10 / D).toFixed(2) + "% > 97%");
  ok(S.DIFF.nw.length === D && S.DIFF.cg.length === D && S.DIFF.cx.length === D, "三条差值序列长度 == D");
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
    for (let k = 13; k < D; k++) {
      if (P.x[k] === null && P.x[k - 1] !== null) {
        const q = S.posAt(p.npc, T0 + k);
        ok(q && q.x === P.x[k - 1] && q.stale === true, "缺秒沿用上一秒(" + p.short + " @" + (T0 + k) + "s → 用 " + (T0 + k - 1) + "s)");
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
  ok(ann.length === expectN && ann.length >= 9,
     "全图 + 900s：地图标记数 == 有位置的英雄数（" + ann.length + " == " + expectN + "）");
  const t0i = ann[0].i;
  // ★ 几何闭环：地图标记像素坐标必须 == 官方标定式（与 PIL 预览/底图同一套常量）
  {
    const p = PL[t0i], q = S.posAt(p.npc, 900);
    const want = [508.3019 + 0.049038 * q.x, 504.5433 - 0.049038 * q.y];
    ok(Math.abs(ann[0].x - want[0]) < 0.01 && Math.abs(ann[0].y - want[1]) < 0.01,
       "标记像素坐标 == w2p(世界坐标)（" + ann[0].x.toFixed(1) + "," + ann[0].y.toFixed(1)
       + " vs " + want[0].toFixed(1) + "," + want[1].toFixed(1) + "）");
  }
  g("cv").onmousedown(evp(ann[0].x, ann[0].y, { button: 0, preventDefault() {} }));
  ok(S.getSel() >= 0, "点英雄标记 → 选中下标 " + S.getSel() + "（该处是 " + t0i + " 号）");
  ok(String(g("selinfo").innerHTML).indexOf("第二步") >= 0, "选中后右栏提示『第二步入口』");
  ok(g("selinfo").innerHTML.indexOf(PL[S.getSel()].short.replace(/_/g, " ")) >= 0, "右栏显示选中的英雄名");
  g("cv").onmousedown(evp(ann[0].x, ann[0].y, { button: 0, preventDefault() {} }));
  ok(S.getSel() === -1, "再点同一英雄 → 取消选中");
  // 空白处 mousedown 不选中、进入拖拽
  g("cv").onmousedown(evp(5, 5, { button: 0, preventDefault() {} }));
  ok(S.getSel() === -1, "点空白 → 不选中（进入拖拽）");
  g("cv").onmouseup();
  // hover 文案
  g("cv").onmousemove(evp(ann[1].x, ann[1].y));
  ok(String(g("mapInfo").textContent).length > 5 && String(g("mapInfo").textContent).indexOf("滚轮") < 0,
     "hover 英雄标记 → 状态行显示该英雄信息：" + String(g("mapInfo").textContent).slice(0, 60));
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
  g("big").value = T0 + 2; g("big").oninput();
  ok(/^\d+$/.test(String(g("c-hp-0").textContent)), "开场+2s hp 单元格是数值（" + g("c-hp-0").textContent + "）");
  ok(String(g("c-nw-0").textContent).indexOf(",") > 0 || +String(g("c-nw-0").textContent).replace(/,/g, "") > 0,
     "开场+2s 净值单元格有值（" + g("c-nw-0").textContent + "）");
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
setTimeout(() => {
  ok(loadErrors.length === 0, "图/头像 onload 回调不抛异常" + (loadErrors.length ? "：" + loadErrors.join("; ") : ""));
  ok(calls.drawImage > 0, "渲染时确实绘制了底图/头像（drawImage " + calls.drawImage + " 次）");
  ok(calls.fillText + calls.arc > 0, "渲染时确实绘制了英雄标记（arc " + calls.arc + " / fillText " + calls.fillText + "）");
  console.log(fails ? "\n" + fails + " 项失败" : "\n全部通过");
  process.exit(fails ? 1 : 0);
}, 20);
