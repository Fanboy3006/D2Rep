#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q6_check.py — Q6 交付产物的端到端校验器(可复跑)。

校验对象: analysis/output_q6/q6_obs_instances.json (viewer 的数据源)
          analysis/output_q6/q6_observer_detail_172.csv
          analysis/output_review/q6_observer_viewer.html (内嵌数据)
校验内容:
  A. 完整性: 场次数覆盖 dems/db_full 全部比赛; 每场战队映射齐全; 时间窗索引与导出 place 严格一致。
  B. 内部一致性(零豁免): 存活 == 销毁-放置; 刁钻 <=> (存活>阈值 且 共存>下限); 寿命上限; 字段自洽。
  C. 独立重算(抽查 K 支): 用 q5_ward.parse_match 取该眼, 独立重算 放置/销毁/存活/被反/距离/共存,
     与产物逐字段比对。判定阈值上 ±0.1s 的条目(导出分辨率)单独计数, 不算失败; 其余必须全等。
  D. viewer 内嵌数据 == 源 JSON (防止 build 阶段丢数据/截断)。
  G. 右删失(截断)真值: 逐场取 db 里的权威结束时刻(远古被摧毁), 对全部实例核 存活<=结束-放置 /
     截断标记与口径相符 / 该截断的都标了 —— 这一节是 parser 级的独立核对(C 节的独立性只在口径/聚合层)。

用法: python analysis/q6_check.py [--sample 250]
退出码: 0 = 全过; 1 = 有失败项(打印 FAIL 明细)
"""
import argparse
import collections
import glob as _glob
import io
import json
import math
import os
import random
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import q5_ward as q

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q6 = os.path.join(ROOT, "analysis", "output_q6")
HTML = os.path.join(ROOT, "analysis", "output_review", "q6_ward_viewer.html")
CS = 172
Q6WIN = [("0-7", -1e9, 420.0), ("7-20", 420.0, 1200.0), ("20+", 1200.0, None)]
RES = 0.1          # 导出时间分辨率(秒): 所有边界豁免都以此为界
EPS = 0.02         # 浮点噪声容差(远小于 0.1 的舍入步长)

fails = []


def ok(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def win_of(sec):
    for i, (_, lo, hi) in enumerate(Q6WIN):
        if sec >= lo and (hi is None or sec < hi):
            return i
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=250)
    a = ap.parse_args()

    d = json.load(io.open(os.path.join(Q6, "q6_obs_instances.json"), encoding="utf-8"))
    inst, mids, orgs = d["inst"], d["mids"], d["orgs"]
    mo = d["match_orgs"]
    tr = d["tricky"]
    mn_s, mn_o, radius = tr["min_surv"], tr["min_overlap"], tr["radius"]
    print("[Q6 check] instances=%d matches=%d orgs=%d  tricky_def: surv>%ss & overlap>%ss & r<=%s"
          % (len(inst), len(mids), len(orgs), mn_s, mn_o, radius))

    # ---------- A. 完整性 ----------
    dbs = sorted(_glob.glob(os.path.join(ROOT, "dems", "db_full", "*", "*.db")))
    ok(len(dbs) == len(mids), "A1 match count covers db_full: db=%d json=%d" % (len(dbs), len(mids)))
    ok(set(int(os.path.basename(x)[:-3]) for x in dbs) == set(mids), "A2 match id set identical")
    ok(len(mo) == len(mids), "A3 every match has org mapping: %d/%d" % (len(mo), len(mids)))
    bad_org = [k for k, v in mo.items() if not isinstance(v, list) or len(v) != 2
               or not all(isinstance(x, int) and 0 <= x < len(orgs) for x in v)]
    ok(not bad_org, "A4 org index valid (bad=%d)" % len(bad_org))
    # 窗口索引由导出 place 派生 -> 必须严格一致, 不再有取整豁免
    bad_win = [r for r in inst if r[4] != win_of(r[5])]
    ok(not bad_win, "A5 window index == f(exported place), strict (bad=%d)" % len(bad_win))
    bad_team = [r for r in inst if r[3] not in (2, 3)]
    ok(not bad_team, "A6 side value in {2,3} (bad=%d)" % len(bad_team))

    # ---------- B. 内部一致性(零豁免) ----------
    bad_surv = [r for r in inst if abs(r[7] - (r[6] - r[5])) > EPS]
    ok(not bad_surv, "B1 surv == destroy - place, strict (bad=%d)" % len(bad_surv))
    bad_rnd = [r for r in inst if abs(r[7] - round(r[6] - r[5], 1)) > EPS]
    ok(not bad_rnd, "B1b surv == round(destroy-place,1) (bad=%d)" % len(bad_rnd))
    bad_flag = [r for r in inst if r[8] not in (0, 1) or r[9] not in (0, 1)]
    ok(not bad_flag, "B2 dew/tricky in {0,1} (bad=%d)" % len(bad_flag))
    bad_es = [r for r in inst if (r[10] > 0) != (r[11] >= 0)]
    ok(not bad_es, "B3 n_es vs d_min consistent (bad=%d)" % len(bad_es))
    bad_rng = [r for r in inst if r[10] > 0 and (r[11] < 0 or r[11] > math.ceil(radius))]
    ok(not bad_rng, "B3b d_min within [0, radius] (bad=%d)" % len(bad_rng))
    over = [r for r in inst if r[7] > q.OBSERVER_LIFE + EPS]
    ok(not over, "B4 surv <= observer life %.0fs (bad=%d)" % (q.OBSERVER_LIFE, len(over)))
    # 刁钻 <=> 定义(全部按导出值判定, 双向都要成立)
    n_tr = sum(1 for r in inst if r[9] == 1)
    miss = [r for r in inst if r[9] == 1 and not (r[7] > mn_s and r[10] > 0 and r[12] >= mn_o)]
    ok(not miss, "B5 tricky -> (surv>%ss & n_es>0 & ovl>=%ss): bad=%d" % (mn_s, mn_o, len(miss)))
    extra = [r for r in inst if r[9] == 0 and (r[7] > mn_s and r[10] > 0 and r[12] >= mn_o)]
    ok(not extra, "B6 (surv>%ss & n_es>0 & ovl>=%ss) -> tricky: bad=%d" % (mn_s, mn_o, len(extra)))
    ok(n_tr > 0, "B7 tricky count > 0 (=%d)" % n_tr)
    # 存活列与 1 位小数分辨率自洽
    bad_res = [r for r in inst if abs(r[7] * 10 - round(r[7] * 10)) > 1e-6
               or abs(r[5] * 10 - round(r[5] * 10)) > 1e-6 or abs(r[6] * 10 - round(r[6] * 10)) > 1e-6]
    ok(not bad_res, "B8 times on 0.1s grid (bad=%d)" % len(bad_res))
    ok(all(len(r) == 17 for r in inst), "B9 every row has 17 fields (cols=%s)" % (d.get("cols") and len(d["cols"])))
    # B10 两个口径的敌方真眼列必须自洽(达标 ⊆ 任意)
    bad_any = [r for r in inst if not (r[10] <= r[14] and r[12] <= r[16] + 1e-9
                                       and (r[15] < 0 or r[11] < 0 or r[15] <= r[11]))]
    ok(not bad_any, "B10 n_es/d_min/ovl (threshold) consistent with *_any (bad=%d)" % len(bad_any))
    bad_pair = [r for r in inst if (r[11] >= 0) != (r[10] > 0) or (r[15] >= 0) != (r[14] > 0)]
    ok(not bad_pair, "B10b distance present iff count > 0, both口径 (bad=%d)" % len(bad_pair))

    # ---------- G. 右删失(截断)真值: 逐场用 db 里的权威结束时刻核对全部行 ----------
    # 权威结束 = 远古被摧毁(q5_ward.game_end_cle); 导出 place/destroy 都是显示时钟(减 gs)。
    ends = {}
    for db in dbs:
        mid = int(os.path.basename(db)[:-3])
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        e = q.game_end_cle(con, mid)
        gs = q.game_start(con, mid)
        con.close()
        if e is not None and gs is not None:
            ends[mid] = e - gs
    ok(len(ends) == len(mids), "G1 every match has authoritative game end (fort): %d/%d" % (len(ends), len(mids)))
    n_cens = 0
    n_band = 0
    g_bad_upper = []      # 存活超过 结束-放置 (物理上不可能)
    g_bad_flag = []       # 截断标记与口径不符
    g_bad_missing = []    # 该截断却没标
    for r in inst:
        end = ends.get(mids[r[0]])
        if end is None:
            continue
        # 容差 0.2s = 0.05s 截断容差(半个导出格) + 0.1s 时间列舍入
        over = r[7] - (end - r[5])
        if over > 0.2:
            g_bad_upper.append((mids[r[0]], r, end, over))
        elif over > 0.0:
            n_band += 1
        c = r[13]
        if c:
            n_cens += 1
            # 截断行: 销毁必须停在比赛结束时刻(导出为 0.1s -> 容差 0.06)
            if abs(r[6] - end) > 0.06:
                g_bad_flag.append((mids[r[0]], r, end))
            # 未被反的截断行: 必须真的是"出生+寿命 > 比赛结束"(留 0.1s 网格余量)
            if r[8] == 0 and r[5] + q.OBSERVER_LIFE <= end - 0.1:
                g_bad_flag.append((mids[r[0]], r, end))
        else:
            if r[8] == 0 and r[5] + q.OBSERVER_LIFE > end + 0.1:
                g_bad_missing.append((mids[r[0]], r, end))
    ok(not g_bad_upper, "G2 surv <= game_end - place (tolerance 0.2s; bad=%d)" % len(g_bad_upper))
    ok(not g_bad_flag, "G3 censored rows consistent with definition (bad=%d)" % len(g_bad_flag))
    ok(not g_bad_missing, "G4 expired rows past game end are flagged censored (missing=%d)" % len(g_bad_missing))
    print("  info rows in rounding band (0 < surv-(end-place) <= 0.2): %d" % n_band)
    for row in (g_bad_upper + g_bad_flag + g_bad_missing)[:5]:
        print("       bad: %s" % (row,))
    print("  info censored rows = %d (%.2f%% of %d)" % (n_cens, 100.0 * n_cens / max(1, len(inst)), len(inst)))

    # ---------- C. 独立重算(抽查) ----------
    random.seed(20260910)
    picks = sorted(random.sample(range(len(inst)), min(a.sample, len(inst))))
    checked = n_place = n_destroy = n_surv = n_dew = n_es = n_dmin = n_tricky = n_cens_ok = n_any_ok = 0
    unmatched = 0
    edge_tricky = 0
    tricky_bad = []
    cache = {}
    for i in picks:
        r = inst[i]
        mi = r[0]
        if mi not in cache:
            mid = mids[mi]
            db = _glob.glob(os.path.join(ROOT, "dems", "db_full", "*", "%d.db" % mid))[0]
            con = sqlite3.connect(db)
            con.row_factory = sqlite3.Row
            gs = q.game_start(con, mid)
            obs, sen, _ = q.parse_match(con, mid)
            cache[mi] = (gs, obs, sen)
            con.close()
        gs, obs, sen = cache[mi]
        # 独立实现: 按 (坐标, 放置时刻) 最近匹配同一支眼
        o = min(obs, key=lambda o: math.hypot(o["x"] - r[1], o["y"] - r[2]) + abs((o["place"] - gs) - r[5]))
        if math.hypot(o["x"] - r[1], o["y"] - r[2]) > 1.0 or abs((o["place"] - gs) - r[5]) > 0.11:
            unmatched += 1
            continue
        checked += 1
        if abs((o["place"] - gs) - r[5]) <= 0.06:
            n_place += 1
        if abs((o["destroy"] - gs) - r[6]) <= 0.11:
            n_destroy += 1
        raw_surv = (o["survival"] if o["survival"] is not None else 0.0)
        end = ends.get(mids[mi])
        # 独立预测存活(只用 db 的权威结束时刻 + 寿命常量 + 导出的 place/该次死亡时刻):
        #   未被反 => min(place+寿命, 比赛结束) - place ; 被反 => min(该次死亡, 比赛结束) - place
        #   全部按导出网格(0.1s)取整后再算 —— 导出的 place/destroy/surv 都落在该网格上(见 §7 约定);
        #   原始(未取整)的 place/destroy 已由 C1/C2 独立核过。
        end_r = round(end, 1) if end is not None else 1e18
        if r[8] == 0:
            pred = round(min(r[5] + q.OBSERVER_LIFE, end_r) - r[5], 1)
        else:
            pred = round(min(round(o["destroy"] - gs, 1), end_r) - r[5], 1)
        if abs(pred - r[7]) <= 0.06:
            n_surv += 1
        if int(o.get("reason") == "dewarded") == r[8]:
            n_dew += 1
        # 独立重算 敌方真眼(支数 / 最近距离 / 最长共存), 全部用原始时刻
        enemy = 3 if o["team"] == 2 else 2
        nes = 0
        dmin = None
        ovl = 0.0
        nany = 0
        dmin_any = None
        ovl_any = 0.0
        for s in sen:
            if s["team"] != enemy or s["place"] is None or s["destroy"] is None:
                continue
            dd = math.hypot(s["x"] - o["x"], s["y"] - o["y"])
            if dd > radius:
                continue
            ov = min(o["destroy"], s["destroy"]) - max(o["place"], s["place"])
            if ov <= 0:
                continue
            nany += 1                                   # 任意交集口径
            ovl_any = max(ovl_any, ov)
            dmin_any = dd if dmin_any is None else min(dmin_any, dd)
            if ov >= mn_o:
                nes += 1
                ovl = max(ovl, ov)
                dmin = dd if dmin is None else min(dmin, dd)
        if nany == r[14] and (dmin_any is None) == (r[15] < 0) \
                and (r[15] < 0 or abs(dmin_any - r[15]) <= 1.0) \
                and abs(round(ovl_any, 1) - r[16]) <= 0.06:
            n_any_ok += 1
        t_raw = 1 if (raw_surv > mn_s and nes > 0 and ovl >= mn_o) else 0
        t_exp = 1 if (r[7] > mn_s and r[10] > 0 and r[12] >= mn_o) else 0
        if t_raw == t_exp:
            n_tricky += 1
        elif (abs(raw_surv - mn_s) <= RES or (nes > 0 and ovl > 0 and abs(ovl - mn_o) <= RES)):
            edge_tricky += 1          # 阈值 ±0.1s 带内 -> 导出分辨率导致的翻转, 单独计数
        else:
            tricky_bad.append((mids[mi], r, raw_surv, ovl, nes))
        if nes == r[10]:
            n_es += 1
        if dmin is None:
            if r[11] < 0:
                n_dmin += 1
        elif abs(dmin - r[11]) <= 1.0:
            n_dmin += 1
        if int(bool(o.get("censored"))) == r[13]:
            n_cens_ok += 1
    ok(checked >= min(50, a.sample), "C0 sampled rows matched to source (%d checked, %d unmatched)"
       % (checked, unmatched))
    ok(unmatched <= max(2, picks.__len__() // 50), "C0b unmatched ratio <= 2%% (unmatched=%d/%d)"
       % (unmatched, len(picks)))
    ok(n_place == checked, "C1 place clock identical %d/%d" % (n_place, checked))
    ok(n_destroy == checked, "C2 destroy clock identical %d/%d" % (n_destroy, checked))
    ok(n_surv == checked, "C3 survival identical %d/%d" % (n_surv, checked))
    ok(n_dew == checked, "C4 dewarded identical %d/%d" % (n_dew, checked))
    ok(n_es == checked, "C5 enemy-sentry count identical %d/%d" % (n_es, checked))
    ok(n_dmin == checked, "C6 nearest enemy-sentry distance identical %d/%d" % (n_dmin, checked))
    ok(n_cens_ok == checked, "C7 censored flag identical to parser %d/%d" % (n_cens_ok, checked))
    ok(n_any_ok == checked, "C7b any-overlap sentry stats (count/dist/max-overlap) identical %d/%d"
       % (n_any_ok, checked))
    ok(not tricky_bad, "C8 tricky verdict identical outside +-%.1fs band: bad=%d (band=%d)"
       % (RES, len(tricky_bad), edge_tricky))
    for row in tricky_bad[:5]:
        print("       bad: mid=%s row=%s raw_surv=%.2f ovl=%.2f nes=%d" % row)

    # ---------- D. viewer 内嵌数据 ----------
    if os.path.exists(HTML):
        s = io.open(HTML, encoding="utf-8").read()
        ok(s.count("@@") == 0, "D1 no unreplaced placeholder in viewer")
        m = s.find('"inst":[')
        ok(m > 0, "D2 viewer embeds instance array")
        mm = re.search(r'const D = (\{.*?\});\n', s, re.S) or re.search(r'const D = (\{.*?\});', s, re.S)
        if mm:
            dj = json.loads(mm.group(1))
            ok(len(dj["inst"]) == len(inst), "D3 embedded instance count == source: %d/%d"
               % (len(dj["inst"]), len(inst)))
            ok(dj["orgs"] == orgs and dj["mids"] == mids, "D4 org/match index identical")
            ok(dj["inst"] == inst, "D5 embedded rows byte-identical to source")
            ok(dj.get("cs") == CS and dj.get("map_half") == d.get("map_half"),
               "D6 grid params identical (cs=%s)" % dj.get("cs"))
            # D7 自包含: 单文件 viewer 不许依赖任何外部资源(否则双击/离线就打不开)
            exts = [u for u in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)', s)
                    if not (u.startswith("data:") or u.startswith("#"))]
            urls = [u for u in re.findall(r'url\(([^)]+)\)', s) if "data:" not in u]
            bad_ext = [u for u in exts + urls if u.startswith("http") or u.startswith("//")]
            ok(not bad_ext, "D7 self-contained (no external resource): bad=%s" % bad_ext[:3])
        else:
            ok(False, "D2b cannot parse embedded JSON")
    else:
        ok(False, "D0 viewer file exists")

    # ---------- E. 网格边界(信息项, 不判失败) ----------
    # MAP_HALF=8600, CS=172 -> 理论格号 0..99;实测有极少数眼落在 |x| 略超 8600 的图外角(分析器不夹取格号,
    # viewer 按世界坐标画点/上色因此不受影响; q6_bins.py 导出多分辨率时把这类夹进边界格)。
    outside = [r for r in inst if not (0 <= (r[1] + CS * 50) // CS <= 99 and 0 <= (r[2] + CS * 50) // CS <= 99)]
    print("  info out-of-grid instances (|x| or |y| > 8600): %d (%.3f%%)"
          % (len(outside), 100.0 * len(outside) / max(1, len(inst))))

    out = {"checks_failed": len(fails), "failures": fails, "checked_sample": checked,
           "unmatched_sample": unmatched, "tricky_boundary_band": edge_tricky,
           "instances": len(inst), "matches": len(mids), "orgs": len(orgs), "tricky": n_tr,
           "censored": n_cens, "matches_with_game_end": len(ends),
           "tricky_def": tr, "resolution_sec": RES}
    json.dump(out, io.open(os.path.join(Q6, "q6_check.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\nALL PASS (%d sampled, boundary-band %d)" % (checked, edge_tricky) if not fails
          else "\n%d CHECK(S) FAILED" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
