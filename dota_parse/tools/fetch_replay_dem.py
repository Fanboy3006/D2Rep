#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_replay_dem.py - download one match's replay and decode it to .dem.

Implements ARCHITECTURE.md §4.2 with the 2026-09 container update: the raw CDN
object may be bz2, zstd or already uncompressed - decode_replay() sniffs the
magic. The result is validated to start with the Source2 demo magic
(PBDEMS2\\0) before being written.

Usage:
    python fetch_replay_dem.py <match_id> [output.dem]
    python fetch_replay_dem.py <match_id> --url <replay_url> [output.dem]

The replay_url is normally resolved via the OpenDota API
(GET /matches/{id} -> replay_url). Pass --url to skip the API call.

Notes:
- python standard library only, plus `zstandard` when the object is zstd
  (see decode_replay.py docstring for install notes).
- Valve.net (Google Edge) hosts are fast; CN hosts (*.dota2.com.cn, Tengine)
  may throttle heavily - downloads retry a few times with backoff.
- Output defaults to <match_id>.dem in the current directory.
- Prints are ASCII-only on purpose (console code-page safety).
"""
import argparse
import bz2
import json
import sys
import time
import urllib.error
import urllib.request

API = "https://api.opendota.com"
UA = {"User-Agent": "Mozilla/5.0"}
MAGIC_ZSTD = b"\x28\xb5\x2f\xfd"
MAGIC_BZ2 = b"BZh"
MAGIC_DEM = b"PBDEMS2\x00"
RETRIES = 5

try:
    import zstandard
except ImportError:
    zstandard = None


def api_json(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def resolve_replay_url(match_id):
    d = api_json("%s/api/matches/%s" % (API, match_id))
    url = d.get("replay_url")
    if not url:
        print("match %s: no replay_url (expired or not parsed yet)" % match_id,
              flush=True)
        return None
    return url


def download(url):
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=900) as r:
                return r.read()
        except Exception as e:
            print("download attempt %d/%d failed: %r"
                  % (attempt, RETRIES, e), flush=True)
            if attempt < RETRIES:
                time.sleep(5 * attempt)
    return None


def decode(raw):
    if raw[:4] == MAGIC_ZSTD:
        if zstandard is None:
            return False, None, ("zstd stream but zstandard module missing - "
                                 "pip install zstandard")
        out = zstandard.ZstdDecompressor().decompress(
            raw, max_output_size=2 * 1024 * 1024 * 1024)
        note = "zstd"
    elif raw[:3] == MAGIC_BZ2:
        out = bz2.decompress(raw)
        note = "bz2"
    else:
        out = raw
        note = "uncompressed"
    if out[:8] == MAGIC_DEM:
        return True, out, note
    return False, None, ("%s container but output magic %r - not a Dota 2 "
                         "replay?" % (note, out[:8]))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("match_id", type=int)
    ap.add_argument("output", nargs="?", default=None,
                    help="default: <match_id>.dem in the current directory")
    ap.add_argument("--url", default=None,
                    help="skip the OpenDota API and use this replay_url")
    args = ap.parse_args()

    out_path = args.output or "%s.dem" % args.match_id
    url = args.url or resolve_replay_url(args.match_id)
    if not url:
        return 1
    print("match %s: %s" % (args.match_id, url), flush=True)

    t0 = time.time()
    raw = download(url)
    if raw is None:
        print("FAILED: could not download replay", flush=True)
        return 1
    print("downloaded %d bytes in %.1fs" % (len(raw), time.time() - t0), flush=True)

    ok, dem, note = decode(raw)
    if not ok:
        print("FAILED: %s" % note, flush=True)
        return 1
    with open(out_path, "wb") as f:
        f.write(dem)
    print("decoded (%s) -> %s (%d bytes, magic OK) in %.1fs"
          % (note, out_path, len(dem), time.time() - t0), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
