#!/usr/bin/env node
/**
 * q6_viewer_itest.js — Q6 viewer(analysis/output_review/q6_observer_viewer.html)的交互回归测试。
 *
 * 为什么需要它：本机沙箱里 Chrome 起不来（crashpad 自杀）、TLS 也不可用，无法截图/抓页面；
 * 而 Q5B 当年正是死在 JS 交互上（白屏）。所以用 Node 桩把真实事件序列跑一遍：
 * 数据/筛选/滚轮缩放 / 拖拽平移 / hover 气泡 / 点格钻取 / 点格画点 / 表格列数对齐。
 *
 * 抓过的真 bug：
 *  ① 滚轮从"单格视野"(172 单位)缩小被 `nw < CS*1.5` 守卫整个 return 掉 → 用户卡在单格视图出不来
 *     （Q5B 用的是"钳制到最小 500"，所以没这问题）。已修：改为钳制。
 *  ② 筛选切换（战队/时间窗/只看刁钻）不重新聚合 —— render() 只在 AGG 为空时聚合。已修：各 setter 调 aggregate()。
 *  ③ 汇总行 colspan 与表头列数不一致（加列时漏改）。已修：断言表头列数 == 明细行 td 数 == colspan。
 *
 * 用法： node analysis/q6_viewer_itest.js
 * 退出码：0 = 全部通过；1 = 有断言失败（会打印 FAIL 行）
 */
const fs = require("fs");
const path = require("path");

const HTML = path.join(__dirname, "output_review", "q6_ward_viewer.html");
const raw = fs.readFileSync(HTML, "utf8");
const src = raw.match(/<script>([\s\S]*?)<\/script>/)[1];

// ---- DOM / Canvas 桩 ----
// ctx 必须记录 drawImage 的**参数**: 底图缩放是否跟随视野, 只看"源裁剪矩形"对不对
// (早先的桩把参数丢了 + bgReady 恒 false -> 底图不跟随视野的 bug 逃过了回归测试)
const calls = { append: 0, fillRect: 0, arc: 0 };
const drawImgs = [];
const ctx = new Proxy({}, { get: (t, k) => (...a) => {
  if (k in calls) calls[k]++;
  if (k === "drawImage") drawImgs.push(a.slice());
} });
const els = {};
function mkEl(id) {
  if (els[id]) return els[id];
  const e = { id, style: {}, dataset: {}, innerHTML: "", textContent: "", _max: 100, _v: "0", checked: false, _h: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener(t, f) { (e._h[t] = e._h[t] || []).push(f); },
    appendChild() { calls.append++; }, querySelector() { return mkEl(id + "_q"); },
    getContext() { return ctx; }, getBoundingClientRect() { return { left: 0, top: 0, width: 1024, height: 1024 }; } };
  Object.defineProperty(e, "value", { get() { return e._v; }, set(v) { e._v = v; } });
  return (els[id] = e);
}
const created = [];
const winH = {};
global.document = { getElementById: mkEl, querySelector: (s) => mkEl(String(s)), querySelectorAll: () => [],
                    createElement: () => { const e = mkEl("n" + created.length); created.push(e); return e; } };
// Image 桩: src 设好后【异步】触发 onload(与浏览器一致) —— 同步触发会在脚本还没跑完时就调用 render(),
// 撞上 let AGG 的 TDZ(那是桩的问题, 不是页面的问题)。onload 里抛的异常会记进 loadErrors。
const loadErrors = [];
global.Image = class {
  constructor() { this.complete = false; this.naturalWidth = 0; this.naturalHeight = 0; }
  set src(v) { this._src = v; this.complete = true; this.naturalWidth = 24; this.naturalHeight = 24;
               const self = this;
               setTimeout(function () {
                 if (typeof self.onload === "function") {
                   try { self.onload(); } catch (e) { loadErrors.push(String(e && e.message || e)); }
                 }
               }, 0); }
  get src() { return this._src; }
};
global.window = { addEventListener(t, f) { (winH[t] = winH[t] || []).push(f); } };

const fire = (el, type, ev) => {
  const hs = els[el]._h[type] || [];
  if (!hs.length) throw new Error("no handler " + el + "." + type);
  hs.forEach((f) => f(ev));
};
const fireWin = (type, ev) => (winH[type] || []).forEach((f) => f(ev));
const ev = (x, y, ex) => Object.assign({ clientX: x, clientY: y, button: 0, preventDefault() {} }, ex || {});

let fails = 0;
const ok = (cond, msg) => { console.log((cond ? "  ok   " : "  FAIL ") + msg); if (!cond) fails++; };

let S = null;
eval(src + "\n;globalThis.__t={getView:function(){return viewRect;},getSel:function(){return selKey;},"
  + "getAgg:function(){return AGG;},getD:function(){return D;},getIX:function(){return IX;},"
  + "getFlags:function(){return {censOut:censOut,nOrg:orgSet.length,wsel:wsel,metric:metric};},"
  + "getIcon:function(){return obsIcon;},"
  + "getBg:function(){return {ready:bgReady,op:bgOp};},"
  + "getDots:function(){return DOTS;},getDot:function(){return locked;},"
  + "getDotStat:function(){return {total:dotTotal,capped:dotCapped};},"
  + "getColorT:function(v){ return colorT(v); },"
  + "valOf:function(c){ return valOf(c); },"
  + "getCap:function(){ return CAP; },"
  + "getMatchN:function(){ return AGG?AGG.matchN:0; },"
  + "forceBg:function(){ bgReady=true; bgOp=35; render(); },"
  + "clearSel:function(){ selKey=null; render(); },"
  + "setSel:function(k){ selKey=k; viewRect=null; renderDrill(k); render(); return AGG?AGG.list.length:0; }};");
S = globalThis.__t;
const width = () => { const v = S.getView(); return v ? v[1] - v[0] : null; };
const nAgg = () => S.getAgg().list.length;

console.log("[Q6 viewer 交互回归] " + path.basename(HTML));
ok(width() === null, "初始为全图视图(视野宽=null)");

// ---- ★ 底图必须跟随视野(用户实测报的 bug: 放大后底图没跟着走) ----
S.forceBg();                                    // 打开底图(桩里 Image.onload 不会触发)
const K = 0.049038, OFFX = 508.3019, REFY = 504.5433;   // 与 viewer 同一套标定
const _w2p = (x, y) => [OFFX + K * x, REFY - K * y];   // 名字加下划线: 页面脚本里也有 w2p
const lastImg = () => drawImgs[drawImgs.length - 1];
// 只取"底图"那次 drawImage(眼点用的是 obsIcon, 参数个数/尺寸都不同, 别混在一起)
const lastMap = () => { for (let i = drawImgs.length - 1; i >= 0; i--) { const a = drawImgs[i]; if (a[0] !== S.getIcon()) return a; } return null; };
const toPx = (x, y) => { const r = S.getView();   // 世界 -> 画布像素(放大后画布≠图像, 必须按视野换算)
  return r ? [(x - r[0]) / (r[1] - r[0]) * 1024, (r[3] - y) / (r[3] - r[2]) * 1024]
           : [508.3019 + 0.049038 * x, 504.5433 - 0.049038 * y]; };
let li = lastMap();
ok(li && li.length === 5 && li[3] === 1024 && li[4] === 1024,
   "全图视野: 底图整张贴满画布(5 参 drawImage)");
const fullCropW = li[0] === 0 ? 1024 : null;
eval("setWin(-1)"); eval("setAllOrg(false)");
fire("cv", "wheel", ev(512, 512, { deltaY: -100 }));
const vr0 = S.getView();
li = lastMap();
{
  const p0 = _w2p(vr0[0], vr0[3]), p1 = _w2p(vr0[1], vr0[2]);
  const wantW = p1[0] - p0[0], wantH = p1[1] - p0[1];
  ok(li.length === 9, "放大后: 底图改用【源裁剪】方式绘制(9 参 drawImage) —— 否则底图不会跟着视野缩放");
  ok(Math.abs(li[3] - wantW) < 0.5 && Math.abs(li[4] - wantH) < 0.5,
     "放大后: 底图裁剪宽高与视野一致 (实测 " + li[3].toFixed(1) + "x" + li[4].toFixed(1)
     + " vs 期望 " + wantW.toFixed(1) + "x" + wantH.toFixed(1) + ")");
  ok(Math.abs(li[1] - p0[0]) < 0.5 && Math.abs(li[2] - p0[1]) < 0.5,
     "放大后: 底图裁剪左上角与视野一致 (" + li[1].toFixed(1) + "," + li[2].toFixed(1) + ")");
  ok(li[5] === 0 && li[6] === 0 && li[7] === 1024 && li[8] === 1024, "裁出的底图铺满整个画布");
}
// 中键拖拽平移后, 裁剪区域必须跟着移动
const beforeX = lastMap()[1];
fire("cv", "mousedown", ev(700, 500, { button: 1 })); fire("cv", "mousemove", ev(400, 500)); fireWin("mouseup", ev(400, 500, { button: 1 }));
ok(Math.abs(lastMap()[1] - beforeX) > 1, "拖拽平移后: 底图裁剪区域跟着移动 ("
   + beforeX.toFixed(1) + " -> " + lastMap()[1].toFixed(1) + ")");
// 极限放大(滚轮到最小半格 86 单位)时, 底图裁剪也必须跟着收到 86 单位
eval("setWin(0)"); eval("setAllOrg(false)");
for (let i = 0; i < 20; i++) fire("cv", "wheel", ev(512, 512, { deltaY: 100 }));   // 先缩回全图
S.setSel(null);
for (let i = 0; i < 30; i++) fire("cv", "wheel", ev(512, 512, { deltaY: -100 }));  // 再一路放大到钳制值
li = lastMap();
{
  const wNow = width();
  ok(Math.abs(li[3] - wNow * K) < 1.0,
     "极限放大(" + Math.round(wNow) + " 单位): 底图裁剪 " + li[3].toFixed(2) + "px ≈ 视野×K(" + (wNow * K).toFixed(2) + "px)");
}
// 点格(全图下)必须只放大 4 倍 -> 底图裁剪 = 全图/4 的世界宽度
for (let i = 0; i < 40; i++) fire("cv", "wheel", ev(512, 512, { deltaY: 100 }));   // 复位全图
S.setSel(null);
const cellPx = toPx(86, -86);                       // 格(50,49)中心 = 世界(86,-86)
fire("cv", "mousedown", ev(cellPx[0], cellPx[1])); fireWin("mouseup", ev(cellPx[0], cellPx[1]));
fire("cv", "click", ev(cellPx[0], cellPx[1]));
li = lastMap();
ok(S.getSel() === "50,49", "点格 -> 选中 (50,49)(当前 " + S.getSel() + ")");
ok(Math.abs(li[3] - (2 * 8600 / 4) * K) < 2,
   "点格 -> 底图裁剪 = 全图/4(实测 " + li[3].toFixed(2) + "px, 期望 " + ((2 * 8600 / 4) * K).toFixed(2) + "px)");
S.setSel(null);
eval("setWin(-1)");

// ---- 数据/表结构自洽(加列/换口径时最容易漏的地方) ----
const D = S.getD(), IX = S.getIX(), I = D.inst;
ok(I.length > 1000, "内嵌实例数 " + I.length);
ok(Array.isArray(D.cols) && D.cols.length === 17 && D.cols[16] === "ovl_any", "cols 17 列且末列 = ovl_any");
const lens = new Set(I.map((r) => r.length));
ok(lens.size === 1 && lens.has(17), "每行 17 个字段(实际 " + [...lens].join("/") + ")");
ok(I.every((r) => r[IX.n_es] <= r[IX.n_es_any]), "达标支数 <= 任意交集支数");
ok(I.every((r) => r[IX.ovl] <= r[IX.ovl_any] + 1e-9), "达标最长共存 <= 任意最长共存");
ok(I.some((r) => r[IX.n_es_any] > 0 && r[IX.n_es] === 0), "存在'任意交集有、但都不达 60s 门槛'的眼(两列确实不同)");
ok(I.some((r) => r[IX.cens] === 1), "存在截断(cens=1)行: " + I.filter((r) => r[IX.cens] === 1).length + " 支");
ok(I.every((r) => r[IX.cens] !== 1 || r[IX.surv] <= Math.max(0, r[IX.destroy] - r[IX.place]) + 0.05),
   "截断行的 存活 == 销毁-放置");
const hdrCols = (raw.match(/<thead><tr>([\s\S]*?)<\/tr><\/thead>/) || [, ""])[1].match(/<th/g).length;
ok(hdrCols === 11, "表头列数 11(「格」列已加、「刁钻?」列已移除; 实际 " + hdrCols + ")");

// ---- 布局契约(owner 实测两轮: 地图超出分辨率 / 与明细表对不齐) ----
// 没有 CSS 引擎可渲染, 所以把"布局规则"本身钉住: ①两栏不换行 ②地图边长受视口限制且保持正方形
// ③控件整行放在两栏之上(这样地图与明细表顶部对齐) ④#curdesc 等控件元素仍在
{
  const css = (raw.match(/<style>([\s\S]*?)<\/style>/) || [, ""])[1];
  ok(/\.wrap\{[^}]*flex-wrap:\s*nowrap/.test(css), "布局: 两栏 flex-wrap:nowrap(明细表不会被挤到地图下面)");
  const cvRule = (css.match(/canvas\{[^}]*\}/) || [""])[0];
  ok(/width:\s*min\(100%,/.test(cvRule) && /100vh/.test(cvRule),
     "布局: 地图边长 = min(列宽, 视口高-固定占用) -> 不会超出分辨率");
  ok(/aspect-ratio:\s*1\s*\/\s*1/.test(cvRule), "布局: 地图保持 1:1(放大后不会被拉扁)");
  ok(/\.right\{[^}]*flex:\s*0 0 clamp\(/.test(css), "布局: 右栏宽度用 clamp(360~660px) 自适应");
  const iControls = raw.indexOf('class="controls"'), iWrap = raw.indexOf('class="wrap"');
  ok(iControls > 0 && iWrap > 0 && iControls < iWrap, "布局: 控件块在 .wrap 之前(两栏顶部对齐)");
  ok(iWrap < raw.indexOf('id="cv"'), "布局: 画布在 .wrap 内(与明细表同排)");
  ok(/id="curdesc"/.test(raw) && /id="orgstat"/.test(raw) && /id="ns"/.test(raw), "布局: 控件元素移出左栏后仍在(curdesc/orgstat/ns)");
}

fire("cv", "wheel", ev(512, 512, { deltaY: -100 }));
ok(width() !== null && width() < 17200, "滚轮放大 -> 视野宽 " + Math.round(width()));

fire("cv", "mousemove", ev(512, 512));
ok(els["tip"].style.display === "block", "hover 显示气泡: " + String(els["tip"].innerHTML).slice(0, 42) + "...");

// ---- 点格: 只负责"切到 4 倍档 + 居中"(owner 口径: 点格只有 4x / 非4x 两档, 其余交给滚轮) ----
const widthBeforeClick = width();
const a0 = calls.arc, r0 = calls.append;
const imgN0 = drawImgs.length;
fire("cv", "mousedown", ev(512, 512)); fireWin("mouseup", ev(512, 512)); fire("cv", "click", ev(512, 512));
const sel = S.getSel();
const rows = calls.append - r0;
ok(sel !== null, "点格 -> 选中格 " + sel);
ok(Math.abs(width() - 2 * 8600 / 4) < 1,
   "点格 -> 切到 4 倍档(全图/4 = 4300): " + Math.round(widthBeforeClick) + " -> " + Math.round(width()));
{
  const vv = S.getView(), cc = vv ? [(vv[0] + vv[1]) / 2, (vv[2] + vv[3]) / 2] : null;
  const sc = String(sel).split(",").map(Number);
  const wantC = [(sc[0] + 0.5) * 172 - 8600, (sc[1] + 0.5) * 172 - 8600];
  ok(cc && Math.abs(cc[0] - wantC[0]) < 1 && Math.abs(cc[1] - wantC[1]) < 1,
     "点格 -> 视野中心 = 该格中心 (" + (cc ? cc.map((x) => Math.round(x)).join(",") : "null") + ")");
}
// 再点另一个格 -> 仍在 4 倍档, 只重新居中(不再叠乘 4 倍)
{
  const w1 = width();
  S.clearSel();                       // 只取消选中, 保留当前视野(选中态下点眼点会触发"锁定"而不是换格)
  const p = S.getView();
  const ccx = (p[0] + p[1]) / 2 + 172, ccy = (p[2] + p[3]) / 2;   // 视野中心右移一格(必在该格内)
  const px = toPx(ccx, ccy);
  fire("cv", "mousedown", ev(px[0], px[1])); fireWin("mouseup", ev(px[0], px[1])); fire("cv", "click", ev(px[0], px[1]));
  ok(Math.abs(width() - w1) < 1, "点别的格 -> 仍是 4 倍档, 只重新居中: " + Math.round(w1) + " -> " + Math.round(width()));
  ok(String(S.getSel()).split(",").map(Number)[0] === Math.floor((ccx + 8600) / 172), "新选中格与点击位置一致(" + S.getSel() + ")");
}
// ★ owner 口径(终版): 点格只有"4x / 非4x"两档 —— 不叠加、也不把滚轮的档位拉回来
{
  eval("resetView()");
  const p0 = toPx(86, -86);                    // 全图下点格(50,49)
  const stepAt = (pt) => { fire("cv", "mousedown", ev(pt[0], pt[1])); fireWin("mouseup", ev(pt[0], pt[1]));
                           fire("cv", "click", ev(pt[0], pt[1])); return width(); };
  const a = stepAt(p0);                        // 全图(17200) -> 4 倍档 4300
  ok(Math.abs(a - 2 * 8600 / 4) < 1, "点格(全图下) -> 切到 4 倍档(全图/4 = 4300): 17200 -> " + Math.round(a));
  // ★ owner 要求: 4 倍档就要能看到假眼的准确位置 -> 选中格的眼点在 4 倍档就画(小号 12px)
  ok(S.getDots().length > 0, "4 倍档(4300)就画出选中格的眼点: " + S.getDots().length + " 支");
  {
    const last = drawImgs.filter((x) => x[0] === S.getIcon()).pop();
    ok(last && last[3] === 12, "4 倍档的图标尺寸 = 12px(实测 " + (last ? last[3] : "-") + "px; 放大后逐步到 20/28px)");
  }
  const b = stepAt(p0);                        // 再点同一个格 -> 不动
  ok(b === a && S.getSel() === "50,49", "再点同一个格 -> 视野不动(" + Math.round(a) + " -> " + Math.round(b) + "), 选中不变");
  // 点【别的格】-> 仍然是 4 倍档(只把视野居中到新格), 不是"在 4 倍基础上再放大 4 倍"
  const c = stepAt(toPx(86 + 172, -86));
  ok(Math.abs(c - a) < 1, "点别的格 -> 仍是 4 倍档(只重新居中): " + Math.round(a) + " -> " + Math.round(c)
     + " (选中 " + S.getSel() + ")");
  // 滚轮缩到更近之后再点别的格: 只居中, 不许被拉回 4 倍档、也不许继续放大
  {
    const q1 = toPx(86, -86);
    fire("cv", "wheel", ev(q1[0], q1[1], { deltaY: -100 }));
    const wWheel = width();
    const d = stepAt(toPx(86 + 344, -86));
    ok(Math.abs(d - wWheel) < 1, "滚轮缩放后点别的格 -> 保持滚轮的缩放档(" + Math.round(wWheel) + " -> " + Math.round(d) + "), 只居中");
    eval("resetView()");
  }
  // 滚轮继续放大(不换格) -> 到 8 格以内就会画出该格的眼点图标
  eval("resetView()");
  const q0 = toPx(86, -86);
  stepAt(q0);                                   // -> 4300, 选中(50,49)
  for (let i = 0; i < 4; i++) fire("cv", "wheel", ev(q0[0], q0[1], { deltaY: -100 }));
  ok(S.getSel() === "50,49" && width() <= 8 * 172 + 1, "滚轮继续放大到 " + Math.round(width()) + " 单位(仍选中 50,49)");
  ok(S.getDots().length > 0, "放大到 8 格以内开始画眼点图标: " + S.getDots().length + " 支");
  eval("resetView()");
}
// 深层缩放下点【眼点图标】-> 锁定该眼(点空白处仍然放大)
{
  for (let i = 0; i < 40; i++) fire("cv", "wheel", ev(512, 512, { deltaY: 100 }));   // 复位全图
  const px = toPx(86, -86);
  fire("cv", "mousedown", ev(px[0], px[1])); fireWin("mouseup", ev(px[0], px[1])); fire("cv", "click", ev(px[0], px[1]));
  for (let i = 0; i < 4; i++) fire("cv", "wheel", ev(px[0], px[1], { deltaY: -100 }));   // 放大到能点图标
  const d0 = S.getDots()[0];
  ok(!!d0, "有可点的眼点(" + S.getDots().length + " 支)");
  if (d0) {
    fire("cv", "mousedown", ev(d0[0], d0[1])); fireWin("mouseup", ev(d0[0], d0[1])); fire("cv", "click", ev(d0[0], d0[1]));
    ok(S.getDot() != null, "点眼点图标 -> 锁定实例 " + S.getDot());
    fire("cv", "mousedown", ev(d0[0], d0[1])); fireWin("mouseup", ev(d0[0], d0[1])); fire("cv", "click", ev(d0[0], d0[1]));
    ok(S.getDot() == null, "再点同一支眼 -> 解锁");
  }
}
// 复位视野按钮
{
  eval("resetView()");
  ok(width() === null && S.getSel() === null, "「复位视野」按钮 -> 回到全图且取消选中");
}

// ---- 平移必须支持多种手势(owner 反馈: 中键在不少鼠标/触控板上按不出来) ----
{
  eval("resetView()");
  const panBy = (opts) => {
    for (let i = 0; i < 40; i++) fire("cv", "wheel", ev(512, 512, { deltaY: 100 }));    // 先回全图
    fire("cv", "wheel", ev(512, 512, { deltaY: -100 }));                                 // 放大一次(有可平移的余地)
    const v0 = S.getView().slice();
    fire("cv", "mousedown", ev(700, 500, opts)); fire("cv", "mousemove", ev(300, 500));
    fireWin("mouseup", ev(300, 500, opts));
    const v1 = S.getView().slice();
    return { moved: Math.abs(v1[0] - v0[0]) > 1, from: v0[0], to: v1[0] };
  };
  const m = panBy({ button: 1 });   ok(m.moved, "中键拖动 -> 平移 " + m.from.toFixed(0) + " -> " + m.to.toFixed(0));
  const r = panBy({ button: 2 });   ok(r.moved, "右键拖动 -> 平移 " + r.from.toFixed(0) + " -> " + r.to.toFixed(0));
  const s = panBy({ button: 0, shiftKey: true }); ok(s.moved, "Shift+左键拖动 -> 平移 " + s.from.toFixed(0) + " -> " + s.to.toFixed(0));
  const l = panBy({ button: 0 });   ok(!l.moved, "普通左键拖动 -> 不平移(仍是点格)");
}
// ---- 阵营 toggle(天辉/夜魇) ----
{
  eval("setWin(-1)"); eval("setAllOrg(false)");
  const nAll = nAgg();
  eval("setSide(2)");
  const nR = nAgg(), listR = S.getAgg().list;
  ok(nR > 0 && nR < nAll, "阵营=天辉: " + nR + " 支(全部 " + nAll + ")");
  ok(listR.every((i) => I[i][IX.team] === 2), "阵营=天辉 -> 聚合里全是 team==2");
  eval("setSide(3)");
  const nD = nAgg();
  ok(nD > 0 && listR.every((i) => I[i][IX.team] === 2), "阵营=夜魇: " + nD + " 支");
  ok(S.getAgg().list.every((i) => I[i][IX.team] === 3), "阵营=夜魇 -> 聚合里全是 team==3");
  ok(nR + nD === nAll, "天辉 + 夜魇 = 全部(" + nR + " + " + nD + " == " + nAll + ")");
  ok(S.getMatchN() > 0 && S.getMatchN() <= 970, "阵营筛选后频率分母跟着算: " + S.getMatchN() + " 场");
  eval("setSide(-1)");
  ok(nAgg() === nAll, "阵营=全部 -> 回到 " + nAll);
}

// ---- 频率热图(owner 要求: 总频率也是重要热图) ----
{
  eval("setWin(-1)"); eval("setAllOrg(false)");
  const mN = S.getMatchN();
  ok(mN === 970, "频率分母 = 当前筛选下的去重场次数(" + mN + ")");
  // 每场出现次数 = 支数 / 分母
  eval("setMetric('per_match')");
  const cs = Object.values(S.getAgg().cells);
  ok(cs.every((c) => Math.abs(S.valOf(c) - c.n / mN) < 1e-12), "每场出现次数 == 支数 ÷ 分母场次(逐格核对)");
  eval("setMetric('use_rate')");
  ok(cs.every((c) => Math.abs(S.valOf(c) - 100 * Object.keys(c.mis).length / mN) < 1e-9), "出现率 == 出场场次 ÷ 分母场次(逐格核对)");
  ok(cs.every((c) => S.valOf(c) <= 100.0001), "出现率 <= 100%");
  ok(cs.some((c) => S.valOf(c) > 0), "出现率有非零值");
  // 分母必须等于"当前筛选下行里出现的去重场次"(独立重算)
  eval("toggleOrg(0, true)");
  {
    const mis = {};
    S.getAgg().list.forEach((i) => { mis[I[i][IX.mi]] = 1; });
    ok(S.getMatchN() === Object.keys(mis).length,
       "换筛选后分母跟着变: " + S.getMatchN() + " == 该筛选下去重场次 " + Object.keys(mis).length);
  }
  eval("toggleOrg(0, false)");
  // 计数类指标用对数色标: t(1) 明显大于线性下的 1/CAP(低端被拉开)
  eval("setMetric('n_obs')");
  const cap = S.getCap(), t1 = S.getColorT(1), lin = 1 / cap;
  ok(t1 > lin * 1.5, "频率热图用对数色标: t(1)=" + t1.toFixed(3) + " > 线性 " + lin.toFixed(3));
  ok(Math.abs(S.getColorT(cap) - 1) < 1e-9 && S.getColorT(0) === 0, "对数色标端点: t(0)=0, t(上限)=1");
  // 率类指标仍是线性: t = v/vmax (注意 setMetric 会重置自动上限, 要取当前 CAP)
  eval("setMetric('dew_rate')");
  const cap2 = S.getCap();
  ok(Math.abs(S.getColorT(cap2 / 2) - 0.5) < 1e-9, "被反率(率类)仍是线性色标 (cap=" + cap2.toFixed(1) + ")");
  eval("setMetric('avg_surv')");
}

const icon = S.getIcon();
ok(!!icon && icon.complete && icon.naturalWidth > 0, "图标已加载(内嵌 base64 官方假眼图标)");
{
  // 选中格 -> 只画该格的眼: 每个眼一次 drawImage(obsIcon, ...), 尺寸 >= 20px
  // 注意: 图标只在放大到 8 格以内才画; 同格点击不再放大 -> 这里改用滚轮放大
  for (let i = 0; i < 40; i++) fire("cv", "wheel", ev(512, 512, { deltaY: 100 }));   // 复位全图
  const px = toPx(86, -86);                             // 点格(50,49) -> 4300
  fire("cv", "mousedown", ev(px[0], px[1])); fireWin("mouseup", ev(px[0], px[1])); fire("cv", "click", ev(px[0], px[1]));
  for (let i = 0; i < 4; i++) fire("cv", "wheel", ev(px[0], px[1], { deltaY: -100 }));   // 滚轮到 8 格以内
  const imgN1 = drawImgs.length;                        // 只数"这次渲染"的图标绘制
  fire("cv", "wheel", ev(px[0], px[1], { deltaY: -100 }));
  const iconDraws = drawImgs.slice(imgN1).filter((a) => a[0] === icon);
  ok(S.getDots().length > 0 && S.getSel() === "50,49", "选中格 50,49 -> 画出该格眼点 " + S.getDots().length + " 支");
  ok(iconDraws.length > 0, "用官方图标绘制眼点 " + iconDraws.length + " 次(以前画的是小圆圈)");
  ok(iconDraws.every((a) => a[3] >= 20 && a[3] === a[4]),
     "图标尺寸 >= 20px(实际 " + (iconDraws.length ? iconDraws[iconDraws.length - 1][3] : "-") + "px, 以前圆圈只有 7~9px)");
  ok(iconDraws.length === S.getDots().length, "画了几个眼点就有几次图标绘制(" + iconDraws.length + " == " + S.getDots().length + ")");
  ok(S.getDots().every((d) => d[3] === "50,49"), "只画选中格里的眼点");
  const st = S.getDotStat();
  ok(st.total > 0, "图上标注统计: 该格 " + st.total + " 支" + (st.capped ? "(过密已缩略)" : ""));
  S.setSel(null);
  eval("setWin(-1)");
}

// 明细行 td 数必须等于表头列数
const trEls = created.filter((e) => String(e.innerHTML).indexOf("<td") >= 0);
if (trEls.length) {
  const tds = (String(trEls[trEls.length - 1].innerHTML).match(/<td/g) || []).length;
  ok(tds === hdrCols, "明细行 td 数 " + tds + " == 表头列数 " + hdrCols);
} else { ok(false, "钻取表没有生成任何 <td> 行"); }
const statusCells = trEls.map((e) => String(e.innerHTML).match(/是被反|到期|存活到比赛结束\(截断\)|被反·记录在赛后/g)).flat().filter(Boolean);
ok(statusCells.length > 0, "状态列文案可识别(样例: " + [...new Set(statusCells)].slice(0, 3).join(" / ") + ")");

// ★ 回归: 从"最小视野"必须能缩出去(旧写法用 nw<CS*1.5 守卫把缩小动作整个 return 掉 -> 卡死)
for (let i = 0; i < 40; i++) fire("cv", "wheel", ev(512, 512, { deltaY: 100 }));   // 先回全图
for (let i = 0; i < 30; i++) fire("cv", "wheel", ev(512, 512, { deltaY: -100 }));  // 一路放大到钳制值
ok(width() >= 80 && width() <= 90, "先放大到最小视野(半格): " + Math.round(width()));
let canOut = false;
for (let i = 0; i < 10; i++) { fire("cv", "wheel", ev(512, 512, { deltaY: 100 })); if (width() > 100) { canOut = true; break; } }
ok(canOut, "从最小视野滚轮缩小 -> 视野宽 " + Math.round(width()) + "(不再卡死)");

for (let i = 0; i < 30 && width() !== null; i++) fire("cv", "wheel", ev(512, 512, { deltaY: 100 }));
ok(width() === null, "继续缩小 -> 复位为全图");

for (let i = 0; i < 30; i++) fire("cv", "wheel", ev(512, 512, { deltaY: -100 }));
ok(width() >= 80 && width() <= 90, "极限放大被钳制在半格附近: " + Math.round(width()));

fire("cv", "mousedown", ev(100, 100, { button: 1 })); fire("cv", "mousemove", ev(900, 900)); fireWin("mouseup", ev(900, 900, { button: 1 }));
const v = S.getView();
const inMap = v && (v[0] >= -8620 && v[1] <= 8620 && v[2] >= -8620 && v[3] <= 8620);
ok(!!inMap, "拖拽到画面外 -> 视野仍夹在图内: " + (v ? v.map((x) => Math.round(x)).join(",") : "null"));

// ---- 筛选: 每次都必须在【当前筛选】上重新聚合 ----
// (页面上这些控件用的是 HTML 内联 onchange="setX()", 桩里没有 addEventListener ->
//  直接按浏览器的方式调用同名函数, 并同时验证"函数会读控件当前状态")
fire("cv", "click", ev(512, 512));
eval("setWin(-1)");                    // 先在"全部时间窗"下测战队多选, 行数才等于实例总数
eval("setAllOrg(false)");
const nAll0 = nAgg();
ok(nAll0 === I.length, "清空多选 => 全部战队(全部窗): " + nAll0 + " / " + I.length);
// 单选一支(取 0 号战队, 与"全部"做对比)
eval("toggleOrg(0, true)");
const nOne = nAgg();
ok(nOne > 0 && nOne < nAll0, "勾 1 支战队(0 号)生效: " + nOne + " < " + nAll0);
ok(S.getAgg().list.every((i) => { const mo = D.match_org[I[i][IX.mi]]; return mo && mo[I[i][IX.team] === 2 ? 0 : 1] === 0; }),
   "勾 1 支 -> 聚合里只有该战队的眼");
// 多选: 再勾一支 => 行数应增加, 且两支队都在聚合里
eval("toggleOrg(1, true)");
const nTwo = nAgg();
ok(nTwo > nOne, "再勾 1 支 -> 多选生效: " + nOne + " -> " + nTwo);
const teams = new Set(S.getAgg().list.map((i) => { const mo = D.match_org[I[i][IX.mi]]; return mo[I[i][IX.team] === 2 ? 0 : 1]; }));
ok(teams.size === 2 && teams.has(0) && teams.has(1), "多选 2 支 -> 聚合里恰好这两支队: " + [...teams].join(","));
// 取消勾选回到"全部"
eval("toggleOrg(0, false)"); eval("toggleOrg(1, false)");
ok(nAgg() === nAll0, "全部取消 -> 回到全部战队 " + nAll0);
// 全选
eval("setAllOrg(true)");
const nSelAll = S.getFlags().nOrg;
ok(nSelAll === D.orgs.length - D.hidden_orgs.length,
   "全选 = 只勾可选的 " + nSelAll + " 支(共 " + D.orgs.length + " 支, 隐藏 " + D.hidden_orgs.length + " 支)");
ok(nAgg() < nAll0, "全选(不含隐藏队) 的行数 " + nAgg() + " < 不勾任何一支(全部战队) " + nAll0
   + " —— 隐藏队的数据仍在'全部'里");
eval("setAllOrg(false)");
// 剔除截断眼(在"全部"下)
const nAny = nAgg();
mkEl("cx").checked = true; eval("setCens()");
ok(S.getAgg().list.every((i) => I[i][IX.cens] === 0), "剔除截断眼 -> 聚合里没有截断行");
mkEl("cx").checked = false; eval("setCens()");
ok(nAgg() === nAny, "取消剔除 -> 回到 " + nAny);

// ---- owner 2026-09 两处改动: ① 页面上完全没有"刁钻"统计 ② 4 支中国战队不给单项筛选 ----
{
  ok(!/id="tk"/.test(raw), "页面上没有「只看刁钻」开关(#tk 不存在)");
  ok(!/tricky_rate|n_tricky/.test(raw), "页面上没有刁钻率/刁钻数指标按钮");
  ok(!/<th[^>]*>刁钻/.test(raw), "明细表没有「刁钻?」列");
  ok(!/data-m="tricky/.test(raw), "指标按钮里没有 tricky 项");
  ok(!/刁钻/.test(raw), "页面上完全不出现「刁钻」字样");
  ok(/这是什么/.test(raw) && /怎么用/.test(raw) && /读数字时要注意/.test(raw), "顶部有面向使用者的操作说明(这是什么/怎么用/读数字时要注意)");
  ok(!/owner|Q5B|口径|裁定|STRATEGY\/|DEM_FORMAT/.test(raw.replace(/data:image\/png;base64,[^"]*/g, "")),
     "页面正文不含内部用语(owner/Q5B/口径/裁定/文档路径)");
  ok(/970 场/.test(raw) && /100 × 100/.test(raw) && /172 单位/.test(raw), "说明里写清数据规模与网格口径");
  const cks = (raw.match(/class="orgck"/g) || []).length;
  ok(cks === D.orgs.length - D.hidden_orgs.length, "战队复选框 " + cks + " 个 = " + D.orgs.length + " − " + D.hidden_orgs.length);
  const hidden = D.hidden_orgs.map((i) => "<label class=\"otog\"[^>]*>[^<]*<input[^>]*data-i=\"" + i + "\"");
  ok(hidden.every((re) => !new RegExp(re).test(raw)), "4 支隐藏战队没有复选框: " + D.hidden_orgs.join(","));
  ok(D.hidden_orgs.every((i) => D.orgs[i]), "隐藏索引对应真实队名: " + D.hidden_orgs.map((i) => D.orgs[i]).join(" / "));
  // 隐藏队的眼在"全部"里, 但点不到它们 -> 单独筛选只可能是其它队
  const hiddenRows = I.filter((r) => D.hidden_orgs.indexOf(D.match_org[r[IX.mi]][r[IX.team] === 2 ? 0 : 1]) >= 0).length;
  ok(hiddenRows > 0, "4 支隐藏队在数据里共 " + hiddenRows + " 支眼(计入总量)");
  eval("setAllOrg(false)");
  ok(S.getAgg().list.length === I.length, "不勾任何一支 -> 全部 " + I.length + " 支(含隐藏队)");
}

// 时间窗(4 个按钮: 0-7/7-20/20+/全部)
[-1, 0, 1, 2].forEach((w) => {
  try { eval("setWin(" + w + ")"); ok(true, "setWin(" + w + ") 未抛异常 -> " + nAgg() + " 支"); }
  catch (e) { ok(false, "setWin(" + w + ") 抛异常: " + e.message); }
});
ok(S.getFlags().wsel === 2, "最后一个 setWin 生效(wsel=2)");
// 指标切换(含新增的"出场场次")
["avg_surv", "n_obs", "per_match", "use_rate", "n_match", "dew_rate"].forEach((m) => {
  try { eval("setMetric('" + m + "')"); ok(true, "setMetric('" + m + "') 未抛异常"); }
  catch (e) { ok(false, "setMetric('" + m + "') 抛异常: " + e.message); }
});
// 出场场次: 全量下每格出场场次 <= 支数, 且最大值 <= 970
eval("setWin(-1)");
const cellsAll = Object.values(S.getAgg().cells);
const nMatchMax = Math.max(...cellsAll.map((c) => Object.keys(c.mis).length));
ok(nMatchMax > 0 && nMatchMax <= 970, "出场场次(格) 最大值 " + nMatchMax + " <= 970");
ok(cellsAll.every((c) => Object.keys(c.mis).length <= c.n), "每格 出场场次 <= 假眼支数");

// 点眼锁定(任务书 §6): 先缩到单格让眼点画出来, 再点中一个眼点 -> 显示 match_id/坐标/双方队名
eval("setWin(0)");
eval("setAllOrg(false)");
fire("cv", "mousedown", ev(512, 512)); fireWin("mouseup", ev(512, 512)); fire("cv", "click", ev(512, 512));
const dotLocked = S.getDot && S.getDot();
ok(S.getDots().length > 0, "点格后画出了 " + S.getDots().length + " 个眼点(供锁定)");
if (S.getDots().length) {
  const d0 = S.getDots()[0];
  fire("cv", "mousedown", ev(d0[0], d0[1])); fireWin("mouseup", ev(d0[0], d0[1]));
  fire("cv", "click", ev(d0[0], d0[1]));
  const info = String(mkEl("dotinfo").innerHTML);
  ok(S.getDot() != null, "点眼点 -> 锁定实例 " + S.getDot());
  ok(/^\s*🔒/.test(info) || info.indexOf("🔒") >= 0, "锁定后显示详情框");
  ok(/\d{9,}/.test(info), "详情含 match_id: " + info.replace(/<[^>]*>/g, "").slice(0, 60) + "...");
  ok(/坐标/.test(info) && /vs/.test(info), "详情含坐标与双方队名");
  // 再点同一支眼 -> 解锁
  fire("cv", "mousedown", ev(d0[0], d0[1])); fireWin("mouseup", ev(d0[0], d0[1]));
  fire("cv", "click", ev(d0[0], d0[1]));
  ok(S.getDot() == null, "再点同一支眼 -> 解锁");
}
ok(true, "筛选/指标/锁定交互未抛异常");

// ---- 鲁棒性: 空结果 + 最坏格钻取规模 ----
// 空结果(该筛选下一支眼都没有)不许抛异常, 且必须给出"没有假眼"的空态
eval("setWin(0)"); eval("setAllOrg(false)");

mkEl("cx").checked = true; eval("setCens()");
eval("toggleOrg(0, true)");
const nEmpty = nAgg();
ok(nEmpty >= 0, "极端筛选下聚合可算: " + nEmpty + " 支");
let emptyOk = true;
try {
  eval("setMetric('avg_surv')");
  eval("setN()");                                   // 重新渲染一次(空 cells 也要能画)
  S.setSel("0,0");                                  // 选中一个(在筛选下很可能为空的)格 -> 走空态分支
  S.setSel(null);
} catch (e) { emptyOk = false; console.log("       empty-state threw: " + e.message); }
ok(emptyOk, "空结果/空格钻取不抛异常且出空态");
eval("setAllOrg(false)");
mkEl("cx").checked = false; eval("setCens()");
eval("setWin(-1)");

// 最坏格: 找出实例最多的格, 直接选中它, 量钻取表的行数与耗时
const perCell = new Map();
for (let i = 0; i < I.length; i++) {
  const k = Math.floor((I[i][IX.x] + D.map_half) / D.cs) + "," + Math.floor((I[i][IX.y] + D.map_half) / D.cs);
  perCell.set(k, (perCell.get(k) || 0) + 1);
}
let worstK = null, worstN = 0;
for (const [k, v] of perCell) if (v > worstN) { worstN = v; worstK = k; }
// 现在明细 = 中心格 + 周围 8 格(3x3), 所以最坏情况的行数是中心格支数的数倍 -> 有 4000 行上限保护
let blockN = 0;
{
  const [wx, wy] = worstK.split(",").map(Number);
  for (const [k, v] of perCell) { const [x, y] = k.split(",").map(Number);
    if (Math.abs(x - wx) <= 1 && Math.abs(y - wy) <= 1) blockN += v; }
}
const nBefore = created.length;
const t0 = Date.now();
const nRows = S.setSel(worstK);
const dt = Date.now() - t0;
const trs = created.slice(nBefore).filter((e) => String(e.innerHTML).indexOf("<td") >= 0).length;
const hasNote = created.slice(nBefore).some((e) => String(e.innerHTML).indexOf("只列出前") >= 0);
ok(nRows === I.length, "最坏格测试前置: 全量聚合 " + nRows + " 支");
ok(dt < 3000, "最坏格 3x3 钻取(中心 " + worstN + " 支 / 块内 " + blockN + " 支 / 表内 td 行 " + trs + ") 渲染耗时 " + dt + "ms < 3000ms");
ok(trs <= 4001, "最坏格钻取行数受 4000 行上限保护: " + trs + " <= 4001" + (hasNote ? "(有截断提示)" : ""));
ok(blockN <= 4000 || hasNote, "超过 4000 行时必须给出截断提示");
S.setSel(null);
ok(true, "鲁棒性检查完成");

// 全量视图下"剔除截断眼"必须真的减少行数(否则该开关形同虚设)
eval("setWin(-1)");      // 前面 setWin 循环把窗口留在了 20+, 这里复位, 否则行数是 20+ 窗的
eval("setAllOrg(false)");
const nAll = nAgg();
ok(nAll === I.length, "全量(全部窗/全部战队): " + nAll);
mkEl("cx").checked = true; eval("setCens()");
const nAllNoCens = nAgg();
ok(nAll > 0 && nAllNoCens < nAll, "全量: 剔除截断眼后 " + nAllNoCens + " < " + nAll
   + " (截断 " + (nAll - nAllNoCens) + " 支)");
ok((nAll - nAllNoCens) > 0.05 * nAll, "截断占比 >5%: " + (100 * (nAll - nAllNoCens) / nAll).toFixed(2) + "%");
mkEl("cx").checked = false; eval("setCens()");

// ---- 异步回调收尾: 图/图标的 onload 必须不抛异常, 且加载完成后能自动重绘 ----
setTimeout(function () {
  ok(loadErrors.length === 0, "图/图标 onload 回调不抛异常" + (loadErrors.length ? "(实际: " + loadErrors.join("; ") + ")" : ""));
  ok(S.getBg().ready === true, "图加载完成 -> bgReady=true(会自动重绘; 以前 onload 里调了不存在的 draw() → 抛错且不重绘)");
  ok(S.getIcon().complete === true && S.getIcon().naturalWidth > 0, "官方图标可用");
  console.log(fails ? "\n" + fails + " 项失败" : "\n全部通过");
  process.exit(fails ? 1 : 0);
}, 10);
