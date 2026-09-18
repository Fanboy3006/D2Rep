#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""parse_all.py - 并行重解析全部 .dem（含净值/金币/野怪/守卫提取器）→ dems/db/<league>/<match>.db。
每个 dota_parse 写各自 db；已有净值(networth>=10)的库跳过。"""
import glob
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

DOTA_PARSE = os.path.join("dota_parse", "target", "release", "dota_parse.exe")
WORKERS = int(sys.argv[1]) if len(sys.argv) > 1 else 6


def has_networth(db):
    try:
        con = sqlite3.connect(db)
        n = con.execute("SELECT COUNT(DISTINCT entity_id) FROM entity_snapshots "
                        "WHERE entity_type='networth'").fetchone()[0]
        con.close()
        return n >= 10
    except Exception:
        return False


def work(dem):
    mid = os.path.basename(dem)[:-4]
    league = os.path.basename(os.path.dirname(dem))
    outdir = os.path.join("dems", "db", league)
    os.makedirs(outdir, exist_ok=True)
    db = os.path.join(outdir, mid + ".db")
    if os.path.exists(db) and has_networth(db):
        return mid, "skip"
    try:
        subprocess.run([DOTA_PARSE, dem, db, "1"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       cwd=os.path.dirname(os.path.abspath(__file__)),
                       timeout=300)
    except Exception as e:
        return mid, "fail:%s" % e
    return mid, ("ok" if os.path.exists(db) else "fail")


def main():
    dems = sorted(glob.glob(os.path.join("dems", "public", "*", "*.dem")))
    print("total dems:", len(dems), "workers:", WORKERS)
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, (mid, st) in enumerate(ex.map(work, dems), 1):
            if st not in ("ok", "skip"):
                print("  FAIL", mid, st)
            if i % 50 == 0:
                print("progress", i, "/", len(dems), "last=%s:%s" % (mid, st))
            done = i
    print("ALL DONE", done, "/", len(dems))


if __name__ == "__main__":
    sys.exit(main())
