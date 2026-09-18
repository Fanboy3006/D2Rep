#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""q2_team_economy.py - Q2 五队分时段经济对胜率（② 数据分析/收集层）。

Outputs per team:
  ① 10分钟状态(领先/均势/落后) -> 胜率
  ② 10->20变化(转优/持平/转劣) -> 胜率     (Δ = gold_adv[20] - gold_adv[10], team view)
  ③ 转移矩阵 (10分钟状态 x 10->20变化)
  ④ match id 清单

Standards (owner 定案):
  team gold_adv@m = gold_adv[m] if team is radiant else -gold_adv[m]
  (radiant_gold_adv[m] = (radiant networth - dire networth) at minute m).
  10-min state: > +thr lead(线优) / < -thr trail(线劣) / else even(线平).
  Δ = gold_adv@20 - gold_adv@10; > +thr turn_up(转优) / < -thr turn_down(转劣) / else flat(持平).
  team_win = radiant_win if team is radiant else 1-radiant_win.

Team ids (resolved from OpenDota team names):
  XG = {8261500, 10208071}  (Xtreme Gaming; two ids across tournaments)
  VG = {726228}             (Vici Gaming)
  TS = {7119388}            (Team Spirit)
  TY = {9823272}            (Team Yandex)
  PV = {9572001}            (TEAM VISION)

Validation: XG/VG/TS use the owner's xlsx match ids (exact replication of the
owner's sample). TY/PV are discovered via /api/teams/{tid}/matches.

Usage:
    python analysis/q2_team_economy.py --populate   # network: fill stats.db
    python analysis/q2_team_economy.py --report --thr 1000
"""
import argparse
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
API = "https://api.opendota.com"
UA = {"User-Agent": "Mozilla/5.0 (dota-replay-analyzer q2)"}
STATS = os.path.join(ROOT, "stats.db")

TARGET = {
    "XG": {8261500},             # Xtreme Gaming ONLY (10208071 is a mislabeled tag, owner-corrected)
    "VG": {726228},              # Vici Gaming
    "TS": {7119388},             # Team Spirit
    "TY": {9823272},             # Team Yandex
    "PV": {9572001, 9824702},    # TeamVision = {TEAM VISION, PVISION} union (owner-fixed)
}
XLSX_SUBJECT = {"XG统计.xlsx": "XG", "VG统计.xlsx": "VG", "TS统计.xlsx": "TS"}

DDL = """
CREATE TABLE IF NOT EXISTS leagues (league_id INTEGER PRIMARY KEY, name TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS teams (team_id INTEGER PRIMARY KEY, name TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS matches (match_id INTEGER PRIMARY KEY, league_id INTEGER,
    radiant_team_id INTEGER, dire_team_id INTEGER, start_time INTEGER,
    duration_sec INTEGER, radiant_win INTEGER, series_id INTEGER, series_type INTEGER,
    game_mode INTEGER, fetched_at TEXT, parse_requested_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS gold_adv (match_id INTEGER NOT NULL, minute INTEGER NOT NULL,
    value REAL NOT NULL, PRIMARY KEY (match_id, minute));
CREATE INDEX IF NOT EXISTS idx_gold_adv_match ON gold_adv (match_id);
-- A1: 逐玩家逐分钟经济(金币)序列。来源 OpenDota /api/matches/{id} 的 players[].gold_t
-- (逐分钟金币数组)。注意：逐玩家逐分钟"净值"无来源(实体m_iNetWorth值不可读、OpenDota
-- 只有 gold_t + 末尾 net_worth)，故 A1 以金币口径交付。
CREATE TABLE IF NOT EXISTS player_econ_t (match_id INTEGER NOT NULL, player_slot INTEGER NOT NULL,
    hero_id INTEGER, minute INTEGER NOT NULL, gold REAL, xp REAL, net_worth_est REAL,
    PRIMARY KEY (match_id, player_slot, minute));
"""


def connect(db=STATS):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript(DDL)
    return con


def upsert_team(con, tid, name=None):
    if tid is None:
        return
    if name:
        con.execute("INSERT INTO teams (team_id,name) VALUES (?,?) "
                    "ON CONFLICT(team_id) DO UPDATE SET name=excluded.name", (tid, name))
    else:
        con.execute("INSERT OR IGNORE INTO teams (team_id) VALUES (?)", (tid,))


import urllib.request
import urllib.error


def http_json(path, timeout=60):
    req = urllib.request.Request(API + path, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def api(path, retries=3):
    for attempt in range(retries):
        try:
            return http_json(path)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2)
        except Exception as e:
            print("  err %s on %s" % (e, path.split("?")[0]), file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    return None


def read_xlsx_seed(xlsx):
    import openpyxl
    wb = openpyxl.load_workbook(os.path.join(ROOT, xlsx), data_only=True)
    ws = wb.worksheets[0]
    out = []  # (match_id, gold10, gold20, delta, result)
    for row in ws.iter_rows(values_only=True):
        if len(row) >= 8 and isinstance(row[1], (int, float)) and int(row[1]) > 0:
            g10 = row[4] if len(row) > 4 else None
            g20 = row[5] if len(row) > 5 else None
            dl = row[6] if len(row) > 6 else None
            res = row[7] if len(row) > 7 else None
            out.append((int(row[1]), g10, g20, dl, res))
    return out


def seed_match_ids():
    """Bootstrap match ids from owner xlsx (XG/VG/TS matches = validation set)."""
    s = {}
    for xlsx, subj in XLSX_SUBJECT.items():
        for row in read_xlsx_seed(xlsx):
            s.setdefault(row[0], []).append((subj, row[1], row[2], row[3], row[4]))
    return s


def has_gold(con, mid):
    return con.execute("SELECT 1 FROM gold_adv WHERE match_id=? LIMIT 1", (mid,)).fetchone() is not None


def fetch_match(con, mid, sleep_s=0.8):
    """Fetch /api/matches/{id}; store match row + gold_adv (idempotent)."""
    if has_gold(con, mid):
        return True
    d = api("/api/matches/%d" % mid)
    time.sleep(sleep_s)
    if not d:
        return False
    rtid = d.get("radiant_team_id")
    dtid = d.get("dire_team_id")
    rw = d.get("radiant_win")
    rw = 1 if rw is True else 0 if rw is False else None
    con.execute("""INSERT INTO matches (match_id,league_id,radiant_team_id,dire_team_id,
                   start_time,duration_sec,radiant_win,series_id,series_type,game_mode,fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(match_id) DO UPDATE SET radiant_team_id=excluded.radiant_team_id,
                       dire_team_id=excluded.dire_team_id, radiant_win=excluded.radiant_win,
                       duration_sec=excluded.duration_sec""",
                (mid, d.get("leagueid"), rtid, dtid, d.get("start_time"), d.get("duration"),
                 rw, d.get("series_id"), d.get("series_type"), d.get("game_mode")))
    for tid in (rtid, dtid):
        if tid and con.execute("SELECT 1 FROM teams WHERE team_id=?", (tid,)).fetchone() is None:
            tm = api("/api/teams/%d" % tid)
            time.sleep(sleep_s)
            upsert_team(con, tid, (tm or {}).get("name"))
    gold = d.get("radiant_gold_adv") or []
    n = 0
    for mn, v in enumerate(gold):
        if v is not None:
            con.execute("INSERT OR IGNORE INTO gold_adv (match_id,minute,value) VALUES (?,?,?)",
                        (mid, mn, v))
            n += 1
    con.commit()
    return n > 0


def discover_team_matches(con, team_ids, sleep_s=0.8):
    """Matches of a team via /api/teams/{tid}/matches (store match rows only)."""
    mids = set()
    for tid in team_ids:
        data = api("/api/teams/%d/matches" % tid)
        time.sleep(sleep_s)
        if not isinstance(data, list):
            continue
        for m in data:
            mid = m.get("match_id")
            if not mid:
                continue
            mids.add(mid)
            rw = m.get("radiant_win")
            rw = 1 if rw is True else 0 if rw is False else None
            con.execute("""INSERT INTO matches (match_id,league_id,radiant_team_id,dire_team_id,
                           start_time,duration_sec,radiant_win,fetched_at)
                           VALUES (?,?,?,?,?,?,?,datetime('now'))
                           ON CONFLICT(match_id) DO UPDATE SET radiant_win=excluded.radiant_win,
                               radiant_team_id=excluded.radiant_team_id,
                               dire_team_id=excluded.dire_team_id""",
                        (mid, m.get("leagueid"), m.get("radiant_team_id"), m.get("dire_team_id"),
                         m.get("start_time"), m.get("duration"), rw))
    con.commit()
    return mids


def has_player_econ(con, mid):
    return con.execute("SELECT 1 FROM player_econ_t WHERE match_id=? LIMIT 1", (mid,)).fetchone() is not None


# --- item cost dictionary (equipment value for net worth reconstruction) ---
_ITEM_CACHE = None
ITEM_COST_JSON = os.path.join(ROOT, ".tmp", "items_cost.json")


def load_item_costs():
    """key -> {"cost": int, "consumable": bool}. From OpenDota /api/constants/items
    (cached to .tmp/items_cost.json). Consumable = qual contains 'consumable'."""
    global _ITEM_CACHE
    if _ITEM_CACHE is not None:
        return _ITEM_CACHE
    if os.path.exists(ITEM_COST_JSON):
        with open(ITEM_COST_JSON, encoding="utf-8") as f:
            _ITEM_CACHE = json.load(f)
        return _ITEM_CACHE
    data = api("/api/constants/items")
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            c = v.get("cost")
            qual = v.get("qual") or ""
            is_cons = "consumable" in qual.lower()
            if isinstance(c, (int, float)) and c > 0:
                out[k] = {"cost": c, "consumable": is_cons}
            elif is_cons:
                out[k] = {"cost": 0, "consumable": True}
    with open(ITEM_COST_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    _ITEM_CACHE = out
    return out


def fetch_match_a1(con, mid, sleep_s=0.8):
    """Populate A1 (per-player per-minute gold/xp/estimated net worth) for a match.

    net_worth_est[minute] = gold_t[minute] + sum(costs of items bought <= minute)
    MINUS consumed items (qual=='consumable'), i.e. only "persistent" equipment
    value. This is the standard "net worth = cash + equipment-in-hand" reconstruction;
    consumed items are removed, sold-item residual stays small.
    """
    if has_player_econ(con, mid):
        return True
    costs = load_item_costs()
    d = api("/api/matches/%d" % mid)
    time.sleep(sleep_s)
    if not d:
        return False
    for pl in d.get("players") or []:
        slot = pl.get("player_slot")
        hero = pl.get("hero_id")
        gt = pl.get("gold_t") or []
        xt = pl.get("xp_t") or []
        buys = pl.get("purchase_log") or []
        ev = []
        for b in buys:
            info = costs.get(b.get("key"), {})
            c = info.get("cost", 0)
            if not info.get("consumable", False):
                if c and b.get("time") is not None:
                    ev.append((max(0, int(b.get("time"))), c))
        ev.sort()
        cum = []
        run = 0
        idx = 0
        n = max(len(gt), 1)
        for mn in range(n):
            while idx < len(ev) and ev[idx][0] <= mn:
                run += ev[idx][1]
                idx += 1
            cum.append(run)
        for mn in range(len(gt)):
            nw = (gt[mn] or 0) + (cum[mn] if mn < len(cum) else 0)
            con.execute(
                "INSERT OR IGNORE INTO player_econ_t (match_id,player_slot,hero_id,minute,gold,xp,net_worth_est) "
                "VALUES (?,?,?,?,?,?,?)",
                (mid, slot, hero, mn, gt[mn], xt[mn] if mn < len(xt) else None, nw))
    con.commit()
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--populate", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--a1", type=int, default=0, help="populate A1 (player_econ_t) for up to N matches")
    ap.add_argument("--thr", type=int, default=1000)
    args = ap.parse_args()
    con = connect()

    if args.a1:
        mids = [r[0] for r in con.execute(
            "SELECT match_id FROM matches WHERE radiant_win IS NOT NULL ORDER BY match_id")]
        done = 0
        for mid in mids:
            if done >= args.a1:
                break
            if fetch_match_a1(con, mid):
                done += 1
        print("A1 populated player_econ_t for %d matches" % done)
        return 0

    if args.populate:
        # incremental & idempotent: fetch_match skips matches already holding gold;
        # league discovery INSERT ... ON CONFLICT. Re-running after a team-id fix
        # only ADDS matches for the corrected target ids.
        seed = seed_match_ids()
        seed_ids = set(seed.keys())
        print("validation seed ids (owner xlsx):", len(seed_ids))
        all_tids = set()
        for tids in TARGET.values():
            all_tids |= tids

        # 1. fetch gold for seed matches (also stores leagueid in matches table)
        got = miss = 0
        for i, mid in enumerate(sorted(seed_ids), 1):
            if fetch_match(con, mid):
                got += 1
            else:
                miss += 1
            if i % 25 == 0:
                print("  seed progress %d/%d got=%d miss=%d" % (i, len(seed_ids), got, miss))
        print("  seed gold: got=%d miss=%d" % (got, miss))

        # 2. league set from seed matches
        q = con.execute("SELECT DISTINCT league_id FROM matches WHERE match_id IN (%s)"
                        % ",".join("?" * len(seed_ids)), tuple(seed_ids))
        leagues = [r[0] for r in q if r[0]]
        print("  leagues from seed matches:", leagues)

        # 3. for each league, discover matches involving any target team
        disc_ids = set()
        for lid in leagues:
            data = api("/api/leagues/%d/matches" % lid)
            time.sleep(0.8)
            if not isinstance(data, list):
                continue
            for m in data:
                rtid = m.get("radiant_team_id"); dtid = m.get("dire_team_id")
                if (rtid in all_tids) or (dtid in all_tids):
                    mid = m.get("match_id")
                    if mid:
                        disc_ids.add(mid)
                        rw = m.get("radiant_win")
                        rw = 1 if rw is True else 0 if rw is False else None
                        con.execute(
                            """INSERT INTO matches (match_id,league_id,radiant_team_id,dire_team_id,
                               start_time,duration_sec,radiant_win,fetched_at)
                               VALUES (?,?,?,?,?,?,?,datetime('now'))
                               ON CONFLICT(match_id) DO UPDATE SET radiant_win=excluded.radiant_win,
                                   radiant_team_id=excluded.radiant_team_id,
                                   dire_team_id=excluded.dire_team_id""",
                            (mid, lid, rtid, dtid, m.get("start_time"), m.get("duration"), rw))
        con.commit()
        print("  league-discovered team matches added:", len(disc_ids))

        # 4. fetch gold for discovered ids (skip seed already done)
        to_fetch = sorted(disc_ids - seed_ids)
        got = miss = 0
        for i, mid in enumerate(to_fetch, 1):
            if fetch_match(con, mid):
                got += 1
            else:
                miss += 1
            if i % 25 == 0:
                print("  discover progress %d/%d got=%d miss=%d" % (i, len(to_fetch), got, miss))
        print("  discover gold: got=%d miss=%d" % (got, miss))
        print("DONE populate")
        return 0

    if args.report:
        build_report(con, args.thr)
        return 0

    print("need --populate or --report")
    return 1


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def gamestate(g, thr):
    if g is None:
        return "NA"
    if g > thr:
        return "lead"
    if g < -thr:
        return "trail"
    return "even"


def delta_state(d, thr):
    if d is None:
        return "NA"
    if d > thr:
        return "turn_up"
    if d < -thr:
        return "turn_down"
    return "flat"


def classify_subject(rtid, dtid, rw, subj_tids):
    """Return (is_radiant_subject, subject_tid) if one side is subject team."""
    rs = rtid in subj_tids
    ds = dtid in subj_tids
    # handle both ids same side? choose first matching
    if rs and not ds:
        return True, rtid
    if ds and not rs:
        return False, dtid
    return None, None


def build_report(con, thr):
    print("=" * 78)
    print("Q2 五队分时段经济对胜率  (threshold=±%d)  —— 数据源 stats.db (OpenDota)" % thr)
    seed = seed_match_ids()
    rows = []  # (subject, match_id, gold10, gold20, delta, result)
    for match_id, _ in seed.items():
        # validate against owner xlsx for XG/VG/TS
        pass

    for subj in ["XG", "VG", "TS", "TY", "PV"]:
        subj_tids = TARGET[subj]
        match_rows = []
        # matches from matches table whose radiant/dire team is a subject id
        q = con.execute(
            "SELECT match_id,radiant_team_id,dire_team_id,radiant_win FROM matches")
        for m in q:
            side, _ = classify_subject(m["radiant_team_id"], m["dire_team_id"], m["radiant_win"], subj_tids)
            if side is None:
                continue
            g10 = con.execute("SELECT value FROM gold_adv WHERE match_id=? AND minute=10",
                              (m["match_id"],)).fetchone()
            g20 = con.execute("SELECT value FROM gold_adv WHERE match_id=? AND minute=20",
                              (m["match_id"],)).fetchone()
            g10v = (g10["value"] if g10 else None)
            g20v = (g20["value"] if g20 else None)
            if side is True:   # subject is radiant -> team gold = radiant view
                t10, t20 = g10v, g20v
                res = m["radiant_win"]
            else:              # subject is dire -> team gold = -radiant view
                t10 = -g10v if g10v is not None else None
                t20 = -g20v if g20v is not None else None
                res = 1 - m["radiant_win"] if m["radiant_win"] is not None else None
            if t10 is None or t20 is None:
                continue
            delta = t20 - t10
            match_rows.append({
                "match_id": m["match_id"], "gold10": t10, "gold20": t20,
                "delta": delta, "res": res,
                "s10": gamestate(t10, thr), "s_change": delta_state(delta, thr),
            })

        print("\n" + "=" * 78)
        print("TEAM %s  (matches w/ gold10+gold20=%d)" % (subj, len(match_rows)))
        # ① 10min state -> win rate
        print("\n[①] 10分钟状态 -> 胜率  (lead=%+d, trail=%+d)" % (thr, -thr))
        agg = {k: [0, 0] for k in ("lead", "even", "trail")}
        for r in match_rows:
            st = r["s10"]
            if st in agg:
                agg[st][0] += 1
                if r["res"] is not None:
                    if r["res"] == 1:
                        agg[st][1] += 1
        hdr = "  %-8s %5s %5s %8s %6s" % ("state", "win", "lose", "winrate", "n")
        print(hdr)
        tot_w = tot_n = 0
        for st in ("lead", "even", "trail"):
            n, w = agg[st]
            tot_w += w; tot_n += n
            wr = (w / n) if n else float("nan")
            print("  %-8s %5s %5s %8s %6d" % (st, w, n - w, ("%.4f" % wr), n))
        if tot_n:
            print("  %-8s %5s %5s %8s %6d" % ("ALL", tot_w, tot_n - tot_w,
                                             "%.4f" % (tot_w / tot_n), tot_n))

        # ② 10->20 change -> win rate
        print("\n[②] 10->20变化 -> 胜率  (Δ=gold20-gold10)")
        agg2 = {k: [0, 0] for k in ("turn_up", "flat", "turn_down")}
        for r in match_rows:
            st = r["s_change"]
            if st in agg2:
                agg2[st][0] += 1
                if r["res"] is not None and r["res"] == 1:
                    agg2[st][1] += 1
        for st in ("turn_up", "flat", "turn_down"):
            n, w = agg2[st]
            wr = (w / n) if n else float("nan")
            print("  %-10s %5s %5s %8s %6d" % (st, w, n - w, ("%.4f" % wr), n))

        # ③ transition matrix
        print("\n[③] 转移矩阵 (10分钟状态 × 10->20变化)  counts / row-%s" % "pct")
        states = ("lead", "even", "trail")
        changes = ("turn_up", "flat", "turn_down")
        import collections
        mat = collections.defaultdict(lambda: collections.Counter())
        row_n = collections.Counter()
        for r in match_rows:
            mat[r["s10"]][r["s_change"]] += 1
            row_n[r["s10"]] += 1
        print("  %-6s %8s %8s %8s" % ("10min", "turn_up", "flat", "turn_down"))
        for st in states:
            c = mat[st]
            n = row_n[st] or 1
            print("  %-6s %8d %8d %8d   pct=[%.3f %.3f %.3f]" % (
                st, c["turn_up"], c["flat"], c["turn_down"],
                c["turn_up"] / n, c["flat"] / n, c["turn_down"] / n))

        # ④ match id list
        print("\n[④] match ids (%d):" % len(match_rows))
        print("  " + ", ".join(str(r["match_id"]) for r in match_rows))

    # ---- owner validation (XG/VG/TS) ----
    print("\n" + "=" * 78)
    print("OWNER VALIDATION (阈值±%d) —— 逐行核对 owner xlsx vs stats.db 复算" % thr)
    for xlsx, subj in XLSX_SUBJECT.items():
        owned = read_xlsx_seed(xlsx)
        subj_tids = TARGET[subj]
        diffs = []
        for (mid, g10, g20, dl, res) in owned:
            m = con.execute("SELECT radiant_team_id,dire_team_id,radiant_win FROM matches "
                            "WHERE match_id=?", (mid,)).fetchone()
            if not m:
                diffs.append((mid, "no match row", "", g10, g20, dl, res))
                continue
            side, _ = classify_subject(m["radiant_team_id"], m["dire_team_id"], m["radiant_win"], subj_tids)
            if side is None:
                diffs.append((mid, "subject not on side", "", g10, g20, dl, res))
                continue
            g10r = con.execute("SELECT value FROM gold_adv WHERE match_id=? AND minute=10", (mid,)).fetchone()
            g20r = con.execute("SELECT value FROM gold_adv WHERE match_id=? AND minute=20", (mid,)).fetchone()
            g10v = g10r["value"] if g10r else None
            g20v = g20r["value"] if g20r else None
            t10 = g10v if side is True else (-g10v if g10v is not None else None)
            t20 = g20v if side is True else (-g20v if g20v is not None else None)
            dcalc = (t20 - t10) if (t10 is not None and t20 is not None) else None
            # result
            res_calc = m["radiant_win"] if side is True else (1 - m["radiant_win"] if m["radiant_win"] is not None else None)
            # classify diff
            reasons = []
            if t10 is not None and g10 is not None and round(t10) != round(g10):
                reasons.append("10min")
            if t20 is not None and g20 is not None and round(t20) != round(g20):
                reasons.append("20min")
            if dcalc is not None and dl is not None and round(dcalc) != round(dl):
                reasons.append("delta")
            if res is not None and res_calc is not None and \
               res_calc != (1 if str(res).strip().upper() == "W" else 0):
                reasons.append("result")
            if reasons:
                diffs.append((mid, "DIFF:" + ",".join(reasons),
                              "mine:10=%s 20=%s d=%s res=%s" % (t10, t20, dcalc, res_calc),
                              g10, g20, dl, res))
        print("\n  %s (%s): %d rows, %d differences" % (xlsx, subj, len(owned), len(diffs)))
        for d in diffs[:40]:
            print("    %s | %s | %s | owner:10=%s 20=%s d=%s res=%s" % d)


if __name__ == "__main__":
    sys.exit(main())
