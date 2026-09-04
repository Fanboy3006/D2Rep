#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_download.py - resumable bulk .dem downloader for OpenDota leagues
(ARCHITECTURE.md step 2, batch machine = this F-drive machine).

Robustness design (TI2026 replays can land on the slow CN host ~0.1 MB/s and
the machine may be shut down mid-run):
* EVERY download is written incrementally to `<out>/<match_id>.dem.raw.part`.
  If the connection drops (timeout / retry / reboot), the next run resumes the
  SAME file with `Range: bytes=<bytes_so_far>-` (servers that ignore Range get
  a fresh 200 and the file restarts). A completed raw object is renamed to
  `.dem.raw`, magic-sniff decoded (bz2/zstd/raw) into `.dem` and the raw file
  is removed.
* Per-match progress is committed to `.tmp/batch_download/<league>.json` after
  every probe/download decision; re-running the SAME command resumes
  everything (done/unavailable are skipped, .part files continue).
* Each read has a stall timeout so a dead socket aborts the attempt instead of
  hanging forever; attempts are retried with backoff.

Steps per league:
  1. GET /leagues/{id}/matches -> ids (newest first)
  2. per match: GET /matches/{id} -> replay_url? none -> 'unavailable'
  3. stream-download + Range-resume -> decode -> dems/public/<league>/<id>.dem

Rate limit: ~60 API req/min -> default sleep 1.0 s + backoff on 429/5xx (the
.dem downloads themselves do not hit the OpenDota API).

Usage:
    python opendota/batch_download.py --league 19719 --league 19255 ...
    python opendota/batch_download.py --league 19719 --max-downloads 2  # trial
    python opendota/batch_download.py --league 19719 --partial-test-bytes 3000000
        # debug: stop mid-file after N bytes to exercise the resume path;
        # re-run without the flag to resume.
Dependencies: stdlib + zstandard when the object is zstd (PYTHONPATH must point
at a dir containing zstandard, see dota_parse/tools/decode_replay.py).
"""
import argparse
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "dota_parse", "tools"))
from decode_replay import decode  # noqa: E402  (magic-sniffing decoder)

API = "https://api.opendota.com"
CTX = ssl._create_unverified_context()  # F-drive sandbox has broken CA chain
UA = {"User-Agent": "Mozilla/5.0 (dota-replay-analyzer batch)"}
CHUNK = 1 << 20          # 1 MiB read chunks
STALL_S = 90             # per-read stall timeout (dead-socket detection)
STATE_DIR = os.path.join(ROOT, ".tmp", "batch_download")


# ---------------------------------------------------------------------------
# http helpers
# ---------------------------------------------------------------------------

def api_get(path, sleep_s, log):
    for attempt in range(4):
        req = urllib.request.Request(API + path, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
                data = r.read()
            time.sleep(sleep_s)
            return json.loads(data.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            log("[api] http %s on %s (attempt %d)" % (e.code, path, attempt + 1))
        except Exception as e:
            log("[api] %r on %s (attempt %d)" % (e, path, attempt + 1))
        time.sleep(3 * (attempt + 1))
    return None


def download_to_part(url, part_path, log, partial_test_bytes=0):
    """Stream the raw object into part_path with Range resume.

    Returns (state, size, note):
      "done"       - full file written to part_path
      "partial"    - partial-test-bytes reached (simulated interruption)
      "failed"     - gave up after retries; part_path keeps the progress
    """
    offset = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    for attempt in range(6):
        headers = dict(UA)
        if offset:
            headers["Range"] = "bytes=%d-" % offset
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=STALL_S, context=CTX) as r:
                status = r.getcode()
                clen = r.headers.get("Content-Length")
                if status == 200 and offset:
                    # server ignored Range: restart the file
                    log("  server ignored Range (200); restarting file")
                    offset = 0
                    with open(part_path, "wb"):
                        pass
                mode = "ab" if offset else "wb"
                got = offset
                with open(part_path, mode) as f:
                    while True:
                        chunk = r.read(CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if partial_test_bytes and got >= partial_test_bytes:
                            log("  partial-test-bytes reached (%d) - simulated drop"
                                % got)
                            return "partial", got, "simulated interruption"
                log("  download completed: %d bytes (status %s, clen %s)"
                    % (got, status, clen))
                return "done", got, "ok"
        except Exception as e:
            got = os.path.getsize(part_path) if os.path.exists(part_path) else 0
            log("  attempt %d/6 failed at offset %d: %r (resume will continue from there)"
                % (attempt + 1, got, e))
            time.sleep(6 * (attempt + 1))
    return "failed", (os.path.getsize(part_path) if os.path.exists(part_path) else 0), \
        "download failed after retries"


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def load_state(league):
    path = os.path.join(STATE_DIR, "%s.json" % league)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(league, state, log):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, "%s.json" % league)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--league", type=int, action="append", required=True)
    ap.add_argument("--dem-root", default=os.path.join(ROOT, "dems", "public"))
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--max-downloads", type=int, default=0,
                    help="stop after N completed downloads across all leagues")
    ap.add_argument("--partial-test-bytes", type=int, default=0,
                    help="debug: drop mid-file after N bytes to exercise resume")
    ap.add_argument("--stall-abort", type=int, default=240,
                    help="abort a download attempt (keep .part) if the raw file "
                         "makes no progress for this many seconds")
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    os.makedirs(args.dem_root, exist_ok=True)
    logf = open(args.log, "a", encoding="utf-8") if args.log else None

    def log(msg):
        line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
        print(line, flush=True)
        if logf:
            logf.write(line + "\n")
            logf.flush()

    def spawn_watchdog(outdir, league):
        """Watch the newest .raw.part in outdir; on true stall (no byte change
        for --stall-abort seconds) write a stall marker and os._exit so the
        run can be relaunched; the next run skips matches that stalled twice."""
        stop = threading.Event()
        last = {}

        def _watch():
            while not stop.is_set():
                time.sleep(15)
                try:
                    names = os.listdir(outdir)
                except OSError:
                    continue
                parts = [os.path.join(outdir, n) for n in names if n.endswith(".raw.part")]
                if not parts:
                    continue
                p = max(parts, key=os.path.getmtime)
                try:
                    sz = os.path.getsize(p)
                except OSError:
                    continue
                now = time.time()
                if last.get("p") == p and last.get("sz") == sz:
                    if now - last.get("t", now) >= args.stall_abort:
                        mid = os.path.basename(p).split(".")[0]
                        marker = os.path.join(STATE_DIR, "stall_%d_%s.json" % (league, mid))
                        with open(marker, "w", encoding="utf-8") as f:
                            json.dump({"time": now, "size": sz}, f)
                        log("WATCHDOG: no progress on %s for >%ds; wrote %s and "
                            "aborting for resume (part=%d bytes)"
                            % (p, args.stall_abort, marker, sz))
                        os._exit(3)
                else:
                    last["p"], last["sz"], last["t"] = p, sz, now

        t = threading.Thread(target=_watch, daemon=True)
        t.start()
        return stop

    done_total = 0
    for league in args.league:
        state = load_state(league)
        ms = api_get("/api/leagues/%d/matches" % league, args.sleep, log)
        if not isinstance(ms, list):
            log("league %d: no match list" % league)
            continue
        ids = sorted({m["match_id"] for m in ms if m.get("match_id")})
        # newest first (most likely to still be on the CDN)
        order = sorted(ids, key=lambda i: -(state.get(str(i), {}).get("start_time", 0) or 0))
        log("league %d: %d matches (state file has %d entries)" % (league, len(ids), len(state)))
        outdir = os.path.join(args.dem_root, str(league))
        os.makedirs(outdir, exist_ok=True)
        spawn_watchdog(outdir, league)
        n_done = n_unavail = n_fail = n_skip = 0
        for mid in order:
            key = str(mid)
            st = state.get(key, {})
            if st.get("status") == "done" and st.get("path") and os.path.exists(st["path"]):
                n_skip += 1
                continue
            if st.get("status") == "unavailable":
                n_unavail += 1
                continue

            # stall handling: a previous watchdog abort for this match means the
            # server connection black-holed; two stalls in a row -> unavailable
            stall_marker = os.path.join(STATE_DIR, "stall_%d_%d.json" % (league, mid))
            if os.path.exists(stall_marker):
                os.remove(stall_marker)
                attempts = st.get("attempts", 0) + 1
                state[key] = {**st, "attempts": attempts,
                              "status": "unavailable",
                              "note": "stalled twice (server unreachable)"} \
                    if attempts >= 2 else {**st, "attempts": attempts}
                if attempts >= 2:
                    n_unavail += 1
                    log("match %d stalled twice -> marked unavailable" % mid)
                    save_state(league, state, log)
                    continue
                save_state(league, state, log)

            info = api_get("/api/matches/%s" % mid, args.sleep, log)
            if info is None:
                state[key] = {"status": "unavailable", "note": "no match data"}
                n_unavail += 1
                continue
            url = (info or {}).get("replay_url") or ""
            meta = {"start_time": info.get("start_time"), "duration": info.get("duration"),
                    "lobby_type": info.get("lobby_type"), "radiant_win": info.get("radiant_win")}
            if not url:
                state[key] = {"status": "unavailable", "note": "no replay_url", **meta}
                n_unavail += 1
                save_state(league, state, log)
                continue

            part = os.path.join(outdir, "%d.dem.raw.part" % mid)
            raw = os.path.join(outdir, "%d.dem.raw" % mid)
            if os.path.exists(raw) and os.path.exists(part):
                os.remove(part)  # raw already complete; drop stale part
            log("league %d match %d: %s" % (league, mid, url.split("/")[2]))
            if not os.path.exists(raw):
                dstate, size, dnote = download_to_part(url, part, log, args.partial_test_bytes)
                if dstate == "partial":
                    st2 = state.get(key, {})
                    st2["attempts"] = st2.get("attempts", 0) + 1
                    state[key] = {**meta, **st2, "status": "failed",
                                  "note": "partial-test drop at %d bytes" % size}
                    save_state(league, state, log)
                    log("partial-test drop: resume by re-running without "
                        "--partial-test-bytes (part=%d bytes)" % size)
                    if logf:
                        logf.close()
                    return
                if dstate == "failed":
                    st2 = state.get(key, {})
                    st2["attempts"] = st2.get("attempts", 0) + 1
                    state[key] = {**meta, **st2, "status": "failed", "note": dnote}
                    n_fail += 1
                    save_state(league, state, log)
                    log("  match %d download failed after retries (part %d bytes kept)"
                        % (mid, size))
                    continue
                os.replace(part, raw)
            with open(raw, "rb") as f:
                raw_bytes = f.read()
            dec_ok, dem, note = decode(raw_bytes)
            if not dec_ok:
                st2 = state.get(key, {})
                state[key] = {**meta, **st2, "status": "failed", "note": "decode: " + note}
                n_fail += 1
                save_state(league, state, log)
                continue
            path = os.path.join(outdir, "%d.dem" % mid)
            with open(path, "wb") as f:
                f.write(dem)
            os.remove(raw)
            state[key] = {**meta, "status": "done", "path": path,
                          "raw_bytes": len(raw_bytes), "dem_bytes": len(dem),
                          "container": note.split(" ")[0]}
            n_done += 1
            done_total += 1
            save_state(league, state, log)
            log("  match %d DONE (%s, dem %d bytes) total=%d"
                % (mid, note, len(dem), done_total))
            if args.max_downloads and done_total >= args.max_downloads:
                log("--max-downloads %d reached; stopping (resume later with same command)"
                    % args.max_downloads)
                if logf:
                    logf.close()
                return
        log("league %d finished: done=%d unavailable=%d failed=%d skipped=%d"
            % (league, n_done, n_unavail, n_fail, n_skip))
    if logf:
        logf.close()
    log("ALL LEAGUES FINISHED, completed downloads=%d" % done_total)


if __name__ == "__main__":
    sys.exit(main())
