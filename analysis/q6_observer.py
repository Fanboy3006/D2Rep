#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q6_observer.py - Q6: 全战队【假眼(Observer)】眼位统计热力图 + 刁钻眼位标记。

owner 口径（任务书 Q6）:
  统计单元 = 假眼位置(格, 复用 Q5B 的 172 单位 / 100x100 网格)。
  维度   = 【战队】(org, 来自 player_identity.steam_id -> pro_players.team_name) × 时间窗(0-7 / 7-20 / 20+ / 全部)。
  指标   = 该格·该队·该时间窗 的: 假眼出现次数 / 平均存活时间 / 被反率 / **刁钻率**。
  【刁钻眼位】= 假眼【存活 > 1 分钟】且【其存活期间 1200 单位内存在敌方真眼】(真视 1050, 用 1200 作缓冲)。
      精髓: 把"刁钻(近处有真眼却没被反)"与"本来就安全(附近没敌人)"分开。

继承 Q5B 全部口径(见 STRATEGY/Q5B_WARD_VIEWER.md §4 与 DEM_FORMAT.md §C6/§C6.9):
  ① 判型靠实体类名 ② 放置时刻 = use(候选窗口 [首见-35s, 首见+2.5s]), 禁用 ward_placed.t_cle
  ③ 到期 ⇔ attacker==target, 其余算被反 ④ 寿命是 cle 常量(真眼 420 / 假眼 360), 到期=放置+寿命
  ⑤ 销毁全局一一对应(代价 = |death.tick - (实体末现-9s)|), 死亡只认权威单位名
本脚本不重复实现上述逻辑 —— 直接复用 analysis/q5_ward.py::parse_match(单一真源)。

输出(analysis/output_q6):
  q6_obs_instances.json   逐支假眼实例(供 viewer 前端聚合) + 战队/场次索引
  q6_observer_detail_<cs>.csv  复核明细(match_id / 时刻 / 坐标 / 存活 / 被反 / 刁钻 / 期间敌方真眼)
  q6_stats.json            健康度与口径自检

Usage: python analysis/q6_observer.py [--sample N]
"""
import argparse
import collections
import csv
import glob as _glob
import json
import math
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import q5_ward as q  # 复用眼位解析(口径单一真源)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_HALF = q.MAP_HALF          # 8600
CS = 172                       # Q6B 热力网格(与 Q5B 一致)
# 时间窗(owner 定): 0-7 / 7-20 / 20+ / 全部; 第一窗含更早(含号角前, 归入本窗)
Q6WIN = [("0-7", -1e9, 420.0), ("7-20", 420.0, 1200.0), ("20+", 1200.0, None)]
TRICKY_MIN_SURV = 60.0         # 刁钻: 存活 > 1 分钟
TRICKY_RADIUS = 1200.0         # 刁钻: 存活期间 1200 内存在敌方真眼(真视 1050 + 缓冲)
TRICKY_MIN_OVERLAP = 60.0      # 刁钻: 与该真眼的【最长共存】下限(owner 2026-09 裁定 >=60s;
#                                早先只要求"有交集">0, 但实测 31% 的刁钻共存 <5s —— 真眼恰在假眼插下时到期, 语义上不算) 
UNKNOWN_ORG = "(未识别战队)"


def win_of(sec):
    if sec is None:
        return None
    for i, (_, lo, hi) in enumerate(Q6WIN):
        if sec >= lo and (hi is None or sec < hi):
            return i
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "output_q6"))
    # ---- 待 owner 裁定的口径开关(默认 = 任务书字面定义; 裁定后改参数即可重算) ----
    ap.add_argument("--tricky-min-surv", type=float, default=TRICKY_MIN_SURV,
                    help="刁钻: 存活下限(秒), 默认 60")
    ap.add_argument("--tricky-radius", type=float, default=TRICKY_RADIUS,
                    help="刁钻: 与敌方真眼的距离上限(单位), 默认 1200(真视 1050 + 缓冲); 可设 1050")
    ap.add_argument("--tricky-min-overlap", type=float, default=TRICKY_MIN_OVERLAP,
                    help="刁钻: 与敌方真眼的【最长共存】下限(秒), 默认 60(owner 裁定); 0 = 退回旧口径(只要窗口有交集)")
    ap.add_argument("--censor-at-end", action=argparse.BooleanOptionalAction, default=True,
                    help="右删失(默认开): 存活窗口被【比赛结束=远古被摧毁】截断 —— 到期时刻晚于比赛结束的眼, "
                         "存活记为 结束时刻-放置时刻 并标 censored(实测约 9.5%% 的假眼受影响); "
                         "旧的'比赛结束=combat_log 最大 t_cle'是错的(结算残留会晚 360~925s)。"
                         "用 --no-censor-at-end 退回旧口径(存活=寿命, 是上界)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    dbs = sorted(_glob.glob(os.path.join(ROOT, "dems", "db_full", "*", "*.db")))
    if args.sample:
        dbs = dbs[:args.sample]
    print("matches to parse:", len(dbs), flush=True)

    mids, orgs = [], []          # mids[i] = match_id ; orgs[j] = 战队名
    org_idx, mid_idx = {}, {}
    match_orgs = {}              # mid_idx -> [orgTeam2, orgTeam3]
    inst = []                    # 逐支假眼
    det_rows = []                # 复核明细
    n_obs = n_sen = 0
    n_tricky = n_dew = 0
    max_surv_o = 0.0
    bad_life = 0
    n_ovl_tiny = 0   # 与敌方真眼共存时长 >0 但不足 0.1s(导出分辨率下限)的对数
    n_cens = 0       # 右删失(存活窗口被比赛结束截断)的假眼数
    n_cens_dew = 0   # 其中"被反但死亡记录晚于比赛结束"的(已截断到结束时刻)

    for di, db in enumerate(dbs):
        mid = int(os.path.basename(db)[:-3])
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            gs = q.game_start(con, mid)
            if gs is None:
                continue
            # 右删失由 parse_match(共享真源)完成: 比赛结束 = 远古被摧毁, 见 q5_ward.game_end_cle()
            obs, sen, _orph = q.parse_match(con, mid, censor_at_end=args.censor_at_end)
            if not obs and not sen:
                continue
            tn = q.match_team_names(con, mid)
            mi = len(mids); mids.append(mid); mid_idx[mid] = mi
            row = []
            for t in (2, 3):
                name = tn.get("radiant" if t == 2 else "dire") or UNKNOWN_ORG
                if name not in org_idx:
                    org_idx[name] = len(orgs); orgs.append(name)
                row.append(org_idx[name])
            match_orgs[mi] = row
            n_obs += len(obs); n_sen += len(sen)

            # 真眼按队分桶(供刁钻判定)
            sen_by_team = collections.defaultdict(list)
            for s in sen:
                if s["destroy"] is None or s["place"] is None:
                    continue
                sen_by_team[s["team"]].append(s)

            for o in obs:
                if o["place"] is None or o["destroy"] is None:
                    continue
                if o.get("censored"):
                    n_cens += 1
                surv = o["survival"] if o["survival"] is not None else 0.0
                life = q.OBSERVER_LIFE
                disp_place = o["place"] - gs
                # ---- 导出值先定到 0.1s, 再由导出值派生【时间窗】【存活】【刁钻】 ----
                # 原因: 若各列各自独立四舍五入/各自取源值, 明细里会出现
                #   存活 != 销毁-放置 (实测 3285/38813 行差 0.1s),
                #   存活恰好 60.0s 却标刁钻 (定义是 >1 分钟, 实测 26 行),
                #   win 与导出 place 落在不同窗口 (窗口边界 ±0.05s 带)。
                # 0.1s 步长远小于任何判定阈值(60s/360s/420s/1200s), 不改变口径语义,
                # 却让全部口径都能从导出数据 100% 复现(校验器不再需要任何豁免)。
                e_place = round(disp_place, 1)
                e_destroy = round(o["destroy"] - gs, 1)
                e_surv = round(e_destroy - e_place, 1)
                w = win_of(e_place)
                if w is None:
                    continue
                if e_surv > life + 0.5:
                    bad_life += 1
                max_surv_o = max(max_surv_o, e_surv)
                # ---- 刁钻判定 ----
                # 定义(owner 2026-09 终版): 存活 > 1 分钟 且 存活期间 1200 内存在敌方真眼, 且与该真眼【最长共存 ≥ 60s】。
                #   共存下限是 owner 看了分布后加的: 早先只要求"有交集"时, 31.3% 的刁钻共存 <5s
                #   (真眼恰在假眼插下那一刻到期), 字面符合但语义上不算"近处有真眼却没被反"。
                #   n_es / d_min / ovl 三列都按同一门槛统计(即"共存 ≥ 下限 的敌真眼"), 口径从导出数据 100% 可复现。
                #   另出三列 **任意交集** 口径(n_es_any / d_min_any / ovl_any): "半径内有过交集的敌真眼" ——
                #   因为按门槛统计时, 一支"30 秒共存"的敌真眼会被算成 0 支, 看明细的人容易以为数据漏了。
                enemy = 3 if o["team"] == 2 else 2
                n_es = 0; dmin = None; ovl = 0.0
                n_any = 0; dmin_any = None; ovl_any = 0.0
                for s in sen_by_team.get(enemy, []):
                    dd = math.hypot(s["x"] - o["x"], s["y"] - o["y"])
                    if dd > args.tricky_radius:
                        continue
                    lo = max(o["place"], s["place"]); hi = min(o["destroy"], s["destroy"])
                    ov = hi - lo
                    if ov <= 0:
                        continue
                    n_any += 1                                  # 任意交集口径
                    if ov > ovl_any:
                        ovl_any = ov
                    if dmin_any is None or dd < dmin_any:
                        dmin_any = dd
                    if ov >= args.tricky_min_overlap:            # 共存下限(默认 60s)
                        n_es += 1
                        if ov > ovl:
                            ovl = ov
                        if dmin is None or dd < dmin:
                            dmin = dd
                e_ovl = round(ovl, 1)
                if n_any and n_es == 0:
                    # 半径内有敌真眼、窗口也有交集, 但共存不足下限 -> 旧口径(下限 0)会算刁钻, 现不算
                    n_ovl_tiny += 1
                # 刁钻口径按【导出值】判定, 保证 csv/json/JS 单文件里 100% 可复现:
                #   存活 > 60.0 且 共存(1 位小数) ≥ 下限
                tricky = 1 if (e_surv > args.tricky_min_surv and n_es > 0
                               and e_ovl >= args.tricky_min_overlap) else 0
                if tricky:
                    n_tricky += 1
                dew = 1 if o.get("reason") == "dewarded" else 0
                if dew:
                    n_dew += 1
                cens = 1 if o.get("censored") else 0
                if cens:
                    n_cens_dew += dew          # 截断且被反(死亡记录晚于比赛结束, 已截断到结束时刻)
                inst.append([mi, int(round(o["x"])), int(round(o["y"])), o["team"], w,
                             e_place, e_destroy, e_surv,
                             dew, tricky, n_es, (int(round(dmin)) if dmin is not None else -1),
                             e_ovl, cens, n_any,
                             (int(round(dmin_any)) if dmin_any is not None else -1),
                             round(ovl_any, 1)])
                # 格号夹进 0..N-1: 极少数眼落在 |x| 略超 MAP_HALF 的图外角(实测 19 支),
                # 不夹的话明细 CSV 会出现 cell_x=-1 / 100, 下游按格索引读会越界。
                ncell = int(round(2 * MAP_HALF / CS))
                cx = max(0, min(ncell - 1, int((o["x"] + MAP_HALF) // CS)))
                cy = max(0, min(ncell - 1, int((o["y"] + MAP_HALF) // CS)))
                det_rows.append([cx, cy,
                                 round((cx + 0.5) * CS - MAP_HALF, 1), round((cy + 0.5) * CS - MAP_HALF, 1),
                                 mid, orgs[row[0] if o["team"] == 2 else row[1]], o["team"],
                                 e_place, e_destroy, e_surv,
                                 dew, tricky, n_es, (round(dmin, 1) if dmin is not None else ""),
                                 e_ovl, cens, n_any,
                                 (round(dmin_any, 1) if dmin_any is not None else ""), round(ovl_any, 1)])
        except Exception as ex:
            print("ERR", mid, ex, flush=True)
        finally:
            con.close()
        if (di + 1) % 100 == 0:
            print("parsed %d/%d matches..." % (di + 1, len(dbs)), flush=True)

    print("matches: %d  observers: %d  sentries: %d" % (len(mids), n_obs, n_sen))
    print("tricky: %d (%.1f%% of observers)   dewarded: %d (%.1f%%)   teams(orgs): %d" % (
        n_tricky, 100.0 * n_tricky / max(1, n_obs), n_dew, 100.0 * n_dew / max(1, n_obs), len(orgs)))
    print("excluded by overlap floor (near enemy sentry but coexistence < %.0fs): %d   over-life rows: %d"
          % (args.tricky_min_overlap, n_ovl_tiny, bad_life))
    print("right-censored (survival clamped at game end): %d (%.2f%% of observers), of which dewarded-record-after-end: %d"
          % (n_cens, 100.0 * n_cens / max(1, n_obs), n_cens_dew))

    # ---------- 输出 ----------
    p = os.path.join(args.out, "q6_obs_instances.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"cs": CS, "map_half": MAP_HALF, "wins": [w[0] for w in Q6WIN],
                   "tricky": {"min_surv": args.tricky_min_surv, "radius": args.tricky_radius,
                              "min_overlap": args.tricky_min_overlap, "censor_at_end": bool(args.censor_at_end)},
                   "orgs": orgs, "mids": mids, "match_orgs": match_orgs,
                   "cols": ["mi", "x", "y", "team", "win", "place", "destroy", "surv", "dew", "tricky",
                            "n_es", "d_min", "ovl", "cens", "n_es_any", "d_min_any", "ovl_any"],
                   "inst": inst}, f, ensure_ascii=False, separators=(",", ":"))
    print("wrote", p, "inst=", len(inst))

    p = os.path.join(args.out, "q6_observer_detail_%d.csv" % CS)
    with open(p, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["cell_x", "cell_y", "x_center", "y_center", "match_id", "org", "team",
                     "place_clock", "destroy_clock", "survival_sec", "was_dewarded", "tricky",
                     "n_enemy_sentry", "min_enemy_sentry_dist", "max_enemy_sentry_overlap_sec",
                     "censored_at_game_end",
                     "n_enemy_sentry_any", "min_enemy_sentry_dist_any", "max_enemy_sentry_overlap_sec_any"])
        wr.writerows(det_rows)
    print("wrote", p, "rows=", len(det_rows))

    stats = {"matches": len(mids), "observers": n_obs, "sentries": n_sen, "orgs": len(orgs),
             "tricky": n_tricky, "tricky_rate": round(100.0 * n_tricky / max(1, n_obs), 2),
             "dewarded": n_dew, "deward_rate": round(100.0 * n_dew / max(1, n_obs), 2),
             "observer_life": q.OBSERVER_LIFE, "max_survival": round(max_surv_o, 1),
             "over_life_rows": bad_life,
             "boundary_overlap_lt01s": n_ovl_tiny,
             "censored_at_game_end": n_cens,
             "censored_dewarded_record_after_end": n_cens_dew,
             "tricky_def": {"min_surv_s": args.tricky_min_surv, "radius": args.tricky_radius,
                            "min_overlap_s": args.tricky_min_overlap},
             "censor_at_end": bool(args.censor_at_end)}
    # 共存时长分布(仅供 owner 判断是否给"刁钻"加下限)
    ov = sorted(r[12] for r in inst if r[9] == 1)
    def _b(lo, hi):
        return sum(1 for v in ov if v >= lo and (hi is None or v < hi))
    stats["enemy_sentry_overlap_sec"] = {
        "n": len(ov), "median": (ov[len(ov) // 2] if ov else None),
        "lt5": _b(0, 5), "b5_15": _b(5, 15), "b15_30": _b(15, 30),
        "b30_60": _b(30, 60), "b60_180": _b(60, 180), "ge180": _b(180, None)}
    p = os.path.join(args.out, "q6_stats.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    print("wrote", p, stats)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
