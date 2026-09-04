#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""parse_public.py - parse every downloaded public .dem under dems/public
into its own SQLite db under dems/db/<league>/<match_id>.db using the local
release dota_parse binary. Resume-safe: any match whose db already exists is
skipped; run again later for newly downloaded replays.

Usage:
    python opendota/parse_public.py [--dem-root dems/public] [--out-root dems/db]
                                    [--exe dota_parse/target/release/dota_parse.exe]
                                    [--workers 3] [--log parse_public.log]
"""
import argparse
import concurrent.futures
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dem-root", default=os.path.join(ROOT, "dems", "public"))
    ap.add_argument("--out-root", default=os.path.join(ROOT, "dems", "db"))
    ap.add_argument("--exe", default=os.path.join(ROOT, "dota_parse", "target",
                                                  "release", "dota_parse.exe"))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--force", action="store_true",
                    help="re-parse matches whose db already exists (parser upgrade)")
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    logf = open(args.log, "a", encoding="utf-8") if args.log else None

    def log(msg):
        line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
        print(line, flush=True)
        if logf:
            logf.write(line + "\n")
            logf.flush()

    jobs = []  # (league, mid, dem_path, db_path)
    for league in sorted(os.listdir(args.dem_root)):
        d = os.path.join(args.dem_root, league)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".dem"):
                continue
            mid = name[:-4]
            db = os.path.join(args.out_root, league, mid + ".db")
            jobs.append((league, mid, os.path.join(d, name), db))
    log("found %d dems" % len(jobs))

    done = ok = fail = 0
    stats = {}

    def work(job):
        league, mid, dem, db = job
        if os.path.exists(db) and not args.force:
            return (league, mid, "skip", "")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        if os.path.exists(db):  # --force: clear stale db before re-parse
            try:
                os.remove(db)
            except OSError:
                pass
        # cwd not needed: parser resolves sqlite3.dll from exe dir
        # (encoding: the parser prints raw UTF-8 player names; decode with
        # replacement so GBK-locale hosts do not crash the reader threads)
        p = subprocess.run([args.exe, dem, db, "1"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
        if p.returncode == 0:
            return (league, mid, "ok", "")
        tail = (p.stdout or "").splitlines()[-1:] + (p.stderr or "").splitlines()[-1:]
        return (league, mid, "fail", " | ".join(tail)[-200:])

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for league, mid, st, why in ex.map(work, jobs):
            done += 1
            stats[league] = stats.get(league, [0, 0])
            if st == "ok":
                ok += 1
                stats[league][0] += 1
            elif st == "fail":
                fail += 1
                stats[league][1] += 1
                log("FAIL %s/%s: %s" % (league, mid, why))
            if done % 25 == 0:
                log("progress %d/%d (ok=%d fail=%d) %.0fs"
                    % (done, len(jobs), ok, fail, time.time() - t0))
    log("DONE in %.0fs: dems=%d ok=%d fail=%d" % (time.time() - t0, len(jobs), ok, fail))
    for league, (o, f) in sorted(stats.items()):
        log("  league %s: parsed ok=%d fail=%d" % (league, o, f))
    if logf:
        logf.close()


if __name__ == "__main__":
    sys.exit(main())
