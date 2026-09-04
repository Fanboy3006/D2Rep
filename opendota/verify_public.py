#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_public.py - lightweight end-to-end validation across every parsed
public match db under dems/db (output of opendota/parse_public.py).

Per db checks: three tables present with sane row counts (10 players, >0
snapshots, >0 events), extra/properties JSON valid, coordinates in-bounds.
Prints per-league and grand totals plus any failures.

Usage: python opendota/verify_public.py [--db-root dems/db]
"""
import argparse
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_db(path):
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
        cur = con.cursor()
        n_snap, = cur.execute("SELECT COUNT(*) FROM entity_snapshots").fetchone()
        n_ev, = cur.execute("SELECT COUNT(*) FROM game_events").fetchone()
        n_pl, = cur.execute("SELECT COUNT(*) FROM player_identity").fetchone()
        # JSON validity + coordinate bounds sampled across the whole match
        bad_json = 0
        bad_coord = 0
        for (extra, x, y) in cur.execute(
                "SELECT extra, x, y FROM entity_snapshots LIMIT 3000"):
            if extra is not None:
                try:
                    json.loads(extra)
                except Exception:
                    bad_json += 1
            if x is None or y is None or abs(x) > 20000 or abs(y) > 20000:
                bad_coord += 1
        for (props,) in cur.execute("SELECT properties FROM game_events LIMIT 3000"):
            if props is not None:
                try:
                    json.loads(props)
                except Exception:
                    bad_json += 1
        con.close()
    except Exception as e:
        return None, "open/query error: %r" % e
    issues = []
    if n_pl != 10:
        issues.append("players=%d" % n_pl)
    if n_snap <= 0:
        issues.append("snapshots=0")
    if n_ev <= 0:
        issues.append("events=0")
    if bad_json:
        issues.append("bad_json=%d" % bad_json)
    if bad_coord:
        issues.append("out_of_bounds=%d" % bad_coord)
    return (n_snap, n_ev, n_pl), " | ".join(issues) if issues else "ok"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-root", default=os.path.join(ROOT, "dems", "db"))
    args = ap.parse_args()

    per_league = {"full": {}, "event_only": {}, "bad": {}}
    fails = []
    total = [0, 0, 0]  # snap, ev, pl
    n_full = n_evonly = n_bad = 0
    for league in sorted(os.listdir(args.db_root)):
        d = os.path.join(args.db_root, league)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".db"):
                continue
            res, why = check_db(os.path.join(d, name))
            n = name[:-3]
            if res is None:
                n_bad += 1
                per_league["bad"][league] = per_league["bad"].get(league, 0) + 1
                fails.append((league, n, why))
                continue
            snap, ev, pl = res
            if snap > 0 and pl == 10:
                n_full += 1
                per_league["full"][league] = per_league["full"].get(league, 0) + 1
                total[0] += snap
                total[1] += ev
            elif snap == 0 and ev > 0 and pl == 10:
                # tick-less demo: events + identity valid, no position stream
                n_evonly += 1
                per_league["event_only"][league] = per_league["event_only"].get(league, 0) + 1
                total[1] += ev
            else:
                n_bad += 1
                per_league["bad"][league] = per_league["bad"].get(league, 0) + 1
                fails.append((league, n, why or "unexpected shape"))
    print("dbs: full=%d event_only=%d bad=%d" % (n_full, n_evonly, n_bad))
    for cat in ("full", "event_only", "bad"):
        if per_league[cat]:
            print("  %-10s %s" % (cat, dict(per_league[cat])))
    print("TOTAL snapshots=%d events=%d" % (total[0], total[1]))
    print("BAD:", len(fails))
    for f in fails[:30]:
        print("  ", f)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.exit(main())
