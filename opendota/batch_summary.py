#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_summary.py - summarize resumable batch downloads from their state
files (no network). Reads .tmp/batch_download/<league>.json for every league
given on the command line (or all found) and prints per-league and total
counts/sizes; used after opendota/batch_download.py finishes (or between
resumes) to report done/unavailable/failed.

Usage:
    python opendota/batch_summary.py --league 19719 --league 19255 ...
    python opendota/batch_summary.py            # all state files found
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, ".tmp", "batch_download")
DEM_ROOT = os.path.join(ROOT, "dems", "public")

LEAGUE_NAMES = {
    19719: "The International 2026 (TI2026 main event)",
    19255: "Premier Series", 19696: "DreamLeague Season 29",
    19101: "BLAST SLAM VII", 19785: "Esports World Cup 2026",
    19422: "ESL One Birmingham 2026", 19917: "The Games of the Future 2026",
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", type=int, action="append", default=[])
    args = ap.parse_args()

    if args.league:
        files = ["%d.json" % l for l in args.league]
    else:
        files = sorted(f for f in os.listdir(STATE_DIR) if f.endswith(".json"))
    if not files:
        print("no state files under", STATE_DIR)
        return

    grand = {"done": 0, "unavailable": 0, "failed": 0, "bytes": 0}
    for fn in files:
        league = fn.replace(".json", "")
        path = os.path.join(STATE_DIR, fn)
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        done = [v for v in state.values() if v.get("status") == "done"]
        unav = [v for v in state.values() if v.get("status") == "unavailable"]
        fail = [v for v in state.values() if v.get("status") == "failed"]
        other = len(state) - len(done) - len(unav) - len(fail)
        bytes_dl = sum(v.get("raw_bytes", 0) for v in done)
        dem_bytes = sum(os.path.getsize(v["path"]) for v in done
                        if v.get("path") and os.path.exists(v["path"]))
        name = LEAGUE_NAMES.get(int(league), "")
        print("league %-6s %-42s done=%-3d unavailable=%-3d failed=%-3d "
              "pending=%d raw=%.1fGB dem=%.1fGB" % (
                  league, name, len(done), len(unav), len(fail), other,
                  bytes_dl / 1e9, dem_bytes / 1e9))
        for v in sorted(fail, key=lambda x: x.get("start_time", 0), reverse=True):
            print("    FAILED match: %s (%s)" % (
                _find_match(state, v), v.get("note", "")))
        grand["done"] += len(done)
        grand["unavailable"] += len(unav)
        grand["failed"] += len(fail)
        grand["bytes"] += sum(v.get("raw_bytes", 0) for v in done)
    print("-" * 100)
    print("TOTAL: done=%d unavailable=%d failed=%d raw=%.1fGB" % (
        grand["done"], grand["unavailable"], grand["failed"], grand["bytes"] / 1e9))


def _find_match(state, value):
    for k, v in state.items():
        if v is value:
            return k
    return "?"


if __name__ == "__main__":
    sys.exit(main())
