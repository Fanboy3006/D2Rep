#!/usr/bin/env node
/**
 * q6_public_check.js — 校验【公网已部署的页面】是否就是【本地刚构建的那份】。
 *
 * 为什么需要它: 本机 Chrome 起不来, 一直只能"相信已推送"。而 GitHub Pages 有 CDN 缓存(max-age=600)
 * 与构建延迟 —— 实测就出现过"推上去了但用户打开的还是旧页面"。本脚本直接拉公网内容逐项比对:
 *   ① 页面顶部的构建戳记(时间 + 功能版本)是否与本地一致
 *   ② 新交互/新功能的标记是否都在(setSide / resetView / ICON_MIN_ZOOM_W / n_es_any / per_match ...)
 *   ③ 内嵌的逐支假眼数据是否与源 JSON 逐行相同(排除"页面旧、数据新"这类半新半旧)
 *   ④ 换行归一后的 sha256 是否相同(仓库是 LF, 本地工作区是 CRLF, 直接比字节会差几百字节, 属正常)
 *
 * 用法: node analysis/q6_public_check.js [--wait 180]   (--wait = 最多等多少秒让 Pages 刷新)
 * 退出码: 0 = 一致; 1 = 公网仍是旧版或数据不一致(会打印差异)
 */
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const URL_PUB = "https://bigfatblackwhale.github.io/DSH-Dota2/q6_ward_viewer.html";
const LOCAL = path.join(__dirname, "output_review", "q6_ward_viewer.html");
const SRC_JSON = path.join(__dirname, "output_q6", "q6_obs_instances.json");
const MARKERS = ["setSide", "resetView", "ICON_MIN_ZOOM_W", "n_es_any", "per_match", "sbtn"];

const argv = process.argv.slice(2);
const waitSec = (() => { const i = argv.indexOf("--wait"); return i >= 0 ? parseInt(argv[i + 1], 10) || 0 : 0; })();

const norm = (s) => crypto.createHash("sha256").update(s.replace(/\r\n/g, "\n")).digest("hex").toUpperCase();
const stampOf = (s) => { const m = s.match(/id="build"[^>]*>([^<]*)<b>([^<]*)<\/b>/); return m ? (m[1] + m[2]).trim() : "(无戳记)"; };
const datOf = (s) => {
  // 模板里是: const D = {...};\n const I = D.inst;\n ... \n const HALF=D.map_half, ...
  const a = s.indexOf("const D = ");
  const b = s.indexOf("const I = D.inst;");
  if (a < 0 || b < 0 || b < a) return null;
  return JSON.parse(s.slice(a + 10, b).replace(/;\s*$/, ""));
};

(async () => {
  const localTxt = fs.readFileSync(LOCAL, "utf8");
  const localStamp = stampOf(localTxt), localDat = datOf(localTxt);
  const src = JSON.parse(fs.readFileSync(SRC_JSON, "utf8"));
  console.log("本地构建: %s · 字节 %d · 戳记 [%s]", path.basename(LOCAL), Buffer.byteLength(localTxt), localStamp);
  if (!localDat) { console.log("FAIL 本地文件解析不出内嵌数据"); process.exit(1); }

  const t0 = Date.now();
  let last = null;
  while (true) {
    try {
      const r = await fetch(URL_PUB + "?cb=" + Date.now());
      const txt = await r.text();
      const stamp = stampOf(txt);
      const ok = stamp === localStamp;
      console.log("公网: HTTP %d · 字节 %d · 戳记 [%s]%s", r.status, Buffer.byteLength(txt), stamp, ok ? "  <= 一致" : "");
      last = { txt, stamp, ok };
      if (ok) break;
    } catch (e) { console.log("抓取失败: " + e.message); }
    if (Date.now() - t0 > waitSec * 1000) break;
    await new Promise((s) => setTimeout(s, 15000));
    console.log("  仍在等 Pages 刷新… (%ds)", Math.round((Date.now() - t0) / 1000));
  }
  if (!last) { console.log("FAIL 抓不到公网页面"); process.exit(1); }

  const fails = [];
  const { txt, ok } = last;
  if (!ok) fails.push("公网戳记与本地不同(公网仍可能是旧版; 也可能是 Pages 还在刷新/CDN 缓存)");
  for (const m of MARKERS) {
    const n = txt.split(m).length - 1;
    console.log("  标记 " + m.padEnd(16) + " 公网 " + n + " 处");
    if (!n) fails.push("公网缺少标记 " + m);
  }
  const pubDat = datOf(txt);
  if (!pubDat) fails.push("公网页面解析不出内嵌数据");
  else {
    console.log("  内嵌实例: 公网 %d / 本地 %d / 源 JSON %d", pubDat.inst.length, localDat.inst.length, src.inst.length);
    if (pubDat.inst.length !== src.inst.length) fails.push("公网内嵌实例数与源 JSON 不同");
    else if (JSON.stringify(pubDat.inst) !== JSON.stringify(src.inst)) fails.push("公网内嵌数据与源 JSON 不一致");
    if (JSON.stringify(pubDat.cols) !== JSON.stringify(localDat.cols)) fails.push("公网列定义与本地不同");
  }
  const same = norm(txt) === norm(localTxt);
  console.log("  换行归一后 sha256 相同: %s", same);
  if (!same && !fails.length) fails.push("字节内容仍不同(可能只差构建戳记)");
  console.log(fails.length ? "\nFAIL: " + fails.join(" | ") : "\nPASS: 公网页面 == 本地构建(戳记/标记/内嵌数据全部一致)");
  process.exit(fails.length ? 1 : 0);
})();
