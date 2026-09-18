#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""parse_all_force.py - 强制重解析全部 .dem (最新 parser, 含 t_tick), 不 skip。
用法: python parse_all_force.py [workers]
输出: dems/db/<league>/<match>.db
"""
import glob
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.abspath(__file__))
DOTA_PARSE = os.path.join(ROOT, "dota_parse", "target", "release", "dota_parse.exe")
WORKERS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
os.environ["DOTA_PARSE_SQLITE_DLL"] = os.path.join(ROOT, "dota_parse", "target", "release", "sqlite3.dll")

start_t = time.time()
n_ok = n_fail = n_total = n_skip = 0


def has_game_state(db):
    try:
        import sqlite3
        c = sqlite3.connect(db)
        n = c.execute("SELECT COUNT(*) FROM game_events WHERE event_type='game_state'").fetchone()[0]
        c.close()
        return n > 0
    except Exception:
        return False


def work(dem):
    mid = os.path.basename(dem)[:-4]
    league = os.path.basename(os.path.dirname(dem))
    outdir = os.path.join(ROOT, "dems", "db", league)
    os.makedirs(outdir, exist_ok=True)
    db = os.path.join(outdir, mid + ".db")
    if os.path.exists(db) and has_game_state(db):
        return mid, "skip"   # 已用 cle 版解析 -> 断点续跑跳过
    try:
        subprocess.run([DOTA_PARSE, dem, db, "1"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900)
    except subprocess.TimeoutExpired:
        return mid, "fail:timeout"
    except Exception as e:
        return mid, "fail:%s" % e
    return mid, ("ok" if os.path.exists(db) else "fail")


def main():
    global n_ok, n_fail, n_total, n_skip
    dems = sorted(glob.glob(os.path.join(ROOT, "dems", "public", "*", "*.dem")))
    n_total = len(dems)
    print("[force-parse] total dems:", n_total, "workers:", WORKERS, flush=True)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(work, dem): os.path.basename(dem)[:-4] for dem in dems}
        done = 0
        for fut in as_completed(futs):
            mid, st = fut.result()
            done += 1
            if st == "ok":
                n_ok += 1
            elif st == "skip":
                n_skip += 1
            else:
                n_fail += 1
                print("  FAIL", mid, st, flush=True)
            if done % 50 == 0:
                el = time.time() - start_t
                print("progress %d/%d  ok=%d skip=%d fail=%d  elapsed=%.0fs  (%.2f min)" %
                      (done, n_total, n_ok, n_skip, n_fail, el, el / 60), flush=True)
    el = time.time() - start_t
    print("ALL DONE  total=%d ok=%d skip=%d fail=%d  elapsed=%.0fs (%.2f min)" % (n_total, n_ok, n_skip, n_fail, el, el / 60), flush=True)


if __name__ == "__main__":
    main()
