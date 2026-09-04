#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_viewers_batch.py - Module B batch exporter.

Wraps export_match_viewer.export() over many matches from dems/db, writing one
self-contained viewer HTML per match under dist/viewers/. Parallel (threads):
each worker reads one db and base64-embeds shared assets, so the map PNG is
re-encoded per match (icons are cached under assets/hero_icons/).

Usage:
    python opendota_analysis/export_viewers_batch.py
        [--all]                    # export every parsed match (default)
        [--limit N]                # export only the first N matches (sorted)
        [--leagues 19101,19719]    # restrict to league folders
        [--out-dir dist/viewers]
        [--step N]                 # keep every Nth second (halves size at 2)
        [--workers 4]              # parallel workers
        [--no-icons]
        [--skip-existing]
"""
import argparse
import glob
import importlib.util
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))

_spec = importlib.util.spec_from_file_location(
    "export_match_viewer", os.path.join(HERE, "export_match_viewer.py"))
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="export every parsed match (default)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--every", type=int, default=0,
                    help="sample evenly: export every Nth db (cross-league pilot)")
    ap.add_argument("--leagues", default="", help="comma-separated league ids to include")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "dist", "viewers"))
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-icons", action="store_true")
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    pattern = os.path.join(ROOT, "dems", "db", "*", "*.db")
    dbs = sorted(glob.glob(pattern))
    if args.leagues:
        want = set(args.leagues.split(","))
        dbs = [d for d in dbs if os.path.basename(os.path.dirname(d)) in want]
    if args.every:
        dbs = dbs[::args.every]
    if args.limit:
        dbs = dbs[:args.limit]
    if not dbs:
        sys.exit("no dbs matched")
    os.makedirs(args.out_dir, exist_ok=True)

    def job(db):
        mid = os.path.basename(db)[:-3]
        out = os.path.join(args.out_dir, "viewer_%s.html" % mid)
        if args.skip_existing and os.path.exists(out):
            return None
        st = ev.export(mid, out=out, step=args.step,
                       no_icons=args.no_icons, quiet=True)
        return st

    t0 = time.time()
    ok = fail = 0
    total_mb = 0.0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for st in as_completed([ex.submit(job, d) for d in dbs]):
            try:
                r = st.result()
            except Exception as e:  # noqa: BLE001
                fail += 1
                print("FAIL:", e)
                continue
            if r is None:  # skipped (--skip-existing)
                continue
            ok += 1
            total_mb += r["size_mb"]
            if ok % 25 == 0 or ok <= 3:
                print("  %d/%d ok  (%.1f MB total)" % (ok, len(dbs), total_mb))
    dt = time.time() - t0
    print("DONE: ok=%d fail=%d dbs=%d in %.0fs -> %s (avg %.2f MB/viewer)"
          % (ok, fail, len(dbs), dt, args.out_dir,
             total_mb / ok if ok else 0))
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    sys.exit(main())
