#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""intake_private.py - register non-public replays into the matches catalog
(ARCHITECTURE.md section 8 step 7, layer 1).

Workflow per .dem found in the watch dir (default dems/private/):
  1. sha256 the file (idempotency anchor for header-less replays)
  2. read the replay header via `dota_parse --info` (fast, no full parse) -
     match id, duration, players/teams (shares the §6.6-verified player_info
     decode; no manual input needed)
  3. resolve the catalog match_id:
       header has an official match id  -> that id (decimal string)
       otherwise                       -> manual_<sha256[:12]>   (private ns)
  4. register in the catalog (idempotent: re-running never double-inserts)
  5. unless --no-parse: run the full dota_parse into dems/db/<id>.db and mark
     the row parsed/failed; --note merges into metadata_json; --move relocates
     the .dem into dems/private/registered/ once the row is done.

Usage:
    python scheduler/intake_private.py [--dir dems/private] [--catalog matches.db]
        [--parse-bin dota_parse/target/release/dota_parse.exe]
        [--dll dota_parse/sqlite3.dll]
        [--no-parse] [--note TEXT] [--move]

Prints are ASCII-only on purpose (console code-page safety).
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_match_id(header_match_id, sha256_hex):
    """Catalog namespace rule: header id when it looks official, else a
    content-hash id prefixed with 'manual_' (never collides with public ids)."""
    if header_match_id is not None:
        try:
            v = int(header_match_id)
            if v > 0:
                return str(v)
        except (TypeError, ValueError):
            pass
    digest = (sha256_hex or "0")[:12]
    return "manual_%s" % digest


def sha256_of(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def run_captured(cmd, env=None):
    """Run a native exe with stdout/stderr redirected to temp files (avoids
    pipe capture issues under the dsh sandbox). Returns (rc, stdout, stderr)."""
    tmpdir = os.path.join(ROOT, ".tmp")
    os.makedirs(tmpdir, exist_ok=True)
    out_path = os.path.join(tmpdir, "intake_stdout_%d.txt" % os.getpid())
    err_path = os.path.join(tmpdir, "intake_stderr_%d.txt" % os.getpid())
    with open(out_path, "wb") as fo, open(err_path, "wb") as fe:
        proc = subprocess.run(cmd, stdout=fo, stderr=fe, env=env)
    with open(out_path, "rb") as fo:
        stdout = fo.read().decode("utf-8", "replace")
    with open(err_path, "rb") as fe:
        stderr = fe.read().decode("utf-8", "replace")
    for p in (out_path, err_path):
        try:
            os.remove(p)
        except OSError:
            pass
    return proc.returncode, stdout, stderr


def read_header(parse_bin, dem_path, dll_path):
    """dota_parse --info <dem> -> dict; raises on failure."""
    env = dict(os.environ)
    if dll_path:
        env["DOTA_PARSE_SQLITE_DLL"] = dll_path
    rc, out, err = run_captured([parse_bin, "--info", dem_path], env=env)
    if rc != 0:
        raise RuntimeError("dota_parse --info failed (rc=%d): %s"
                           % (rc, err.strip()[-800:]))
    return json.loads(out)


def parse_full(parse_bin, dem_path, db_path, dll_path):
    env = dict(os.environ)
    if dll_path:
        env["DOTA_PARSE_SQLITE_DLL"] = dll_path
    rc, out, err = run_captured([parse_bin, dem_path, db_path], env=env)
    return rc, out, err


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=os.path.join(ROOT, "dems", "private"),
                    help="watch dir with raw .dem files")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--parse-bin",
                    default=os.path.join(ROOT, "dota_parse", "target", "release",
                                         "dota_parse.exe"))
    ap.add_argument("--dll", default=os.path.join(ROOT, "dota_parse", "sqlite3.dll"))
    ap.add_argument("--no-parse", action="store_true",
                    help="only register into the catalog, do not parse")
    ap.add_argument("--note", default=None, help="free-form note -> metadata_json")
    ap.add_argument("--move", action="store_true",
                    help="move the .dem into <dir>/registered/ once done")
    args = ap.parse_args()

    sys.path.insert(0, ROOT)
    from scheduler import catalog as cat

    watch = os.path.abspath(args.dir)
    registered_dir = os.path.join(watch, "registered")
    db_dir = os.path.join(ROOT, "dems", "db")
    os.makedirs(watch, exist_ok=True)
    os.makedirs(db_dir, exist_ok=True)

    parse_bin = os.path.abspath(args.parse_bin)
    dll_path = os.path.abspath(args.dll) if args.dll else None
    if not os.path.exists(parse_bin):
        print("parse binary not found: %s" % parse_bin)
        return 2

    con = cat.connect(args.catalog)
    cat.ensure_schema(con)

    dems = sorted(
        f for f in os.listdir(watch)
        if f.lower().endswith(".dem") and os.path.isfile(os.path.join(watch, f)))
    if not dems:
        print("no .dem files in %s" % watch)
        return 0

    n_new = n_parsed = n_skipped = n_failed = 0
    for name in dems:
        dem_path = os.path.join(watch, name)
        print("\n== %s" % dem_path)
        sha = sha256_of(dem_path)
        try:
            hdr = read_header(parse_bin, dem_path, dll_path)
        except Exception as e:
            print("  SKIP  header read failed: %s" % e)
            n_failed += 1
            continue

        match_id = resolve_match_id(hdr.get("match_id"), sha)
        duration = hdr.get("duration_seconds")
        dur_s = int(round(duration)) if duration else None
        has_official = hdr.get("match_id") is not None
        print("  header: match_id=%s duration=%ss players=%d official_id=%s"
              % (match_id, dur_s, len(hdr.get("players") or []), has_official))

        metadata = {}
        if has_official:
            metadata["header_id_source"] = "official"
        else:
            metadata["header_id_source"] = "content-hash"
        if args.note:
            metadata["note"] = args.note

        row = cat.get(con, match_id)
        if row is None:
            inserted = cat.register(con, match_id=match_id, source="private",
                                    dem_path=dem_path, dem_sha256=sha,
                                    duration_sec=dur_s, metadata=metadata)
            if not inserted:
                row = cat.get(con, match_id)
            print("  REGISTERED match_id=%s" % match_id)
            n_new += 1
        else:
            print("  EXISTS  state=%s (source=%s)" % (row["parse_state"], row["source"]))
            if args.note:
                cat.set_parse_result(con, match_id, metadata_merge={"note": args.note})

        row = cat.get(con, match_id)
        if args.no_parse:
            if row["parse_state"] == "pending":
                print("  --no-parse: row left pending")
                n_skipped += 1
            else:
                print("  --no-parse: row already %s, nothing to do" % row["parse_state"])
                n_skipped += 1
            # move is still fine for a registered-only row
        elif row["parse_state"] == "parsed":
            print("  already parsed -> skip (%s)" % row["db_path"])
            n_skipped += 1
        else:
            db_path = os.path.join(db_dir, "%s.db" % match_id)
            print("  parsing -> %s ..." % db_path)
            rc, out, err = parse_full(parse_bin, dem_path, db_path, dll_path)
            if rc == 0 and os.path.exists(db_path):
                # summary line from the parser log (committed counts)
                tail = [l for l in out.splitlines() if "[db]" in l or "[done]" in l]
                for l in tail[-2:]:
                    print("   " + l.strip())
                cat.set_parse_result(con, match_id, db_path=db_path, state="parsed")
                n_parsed += 1
            else:
                print("  PARSE FAILED rc=%d: %s" % (rc, err.strip()[-600:]))
                cat.set_parse_result(con, match_id, state="failed",
                                     metadata_merge={"parse_error": err.strip()[-600:]})
                n_failed += 1
                continue

        # optional move to registered/ (visual separation of done files)
        row = cat.get(con, match_id)
        done = row["parse_state"] == "parsed" or args.no_parse
        if args.move and done:
            os.makedirs(registered_dir, exist_ok=True)
            new_path = os.path.join(registered_dir, name)
            if not os.path.exists(new_path):
                os.replace(dem_path, new_path)
                cat.update_dem_path(con, match_id, new_path)
                print("  moved to %s" % new_path)

    print("\nsummary: registered=%d parsed=%d skipped=%d failed=%d"
          % (n_new, n_parsed, n_skipped, n_failed))
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
