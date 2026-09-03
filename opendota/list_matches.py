#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""list_matches.py - batch-fetch match IDs for one or more leagues, by league
ID or by league-name substring (ARCHITECTURE.md step 2 tooling / match
management table source).

Resolves league(s), then for each fetches /leagues/{id}/matches and prints a
table (TSV to stdout by default, or CSV) with the fields useful for a match
management table: league_id/name, match_id, start_time (UTC), radiant_win,
series_id/series_type, radiant/dire team ids.

Usage:
    python opendota/list_matches.py --league-id 19719
    python opendota/list_matches.py --league-name "The International 2026"
    python opendota/list_matches.py --league-id 19255 --league-id 19917 \\
                                     --limit 30 --format csv --out list.csv
    python opendota/list_matches.py --league-name "TI2026" --all-hits

Notes:
- prints are ASCII-safe on purpose; --league-name is a case-insensitive
  substring match and may resolve to several leagues (--all-hits prints them
  all; otherwise prints a resolution list to stderr and uses the hits).
- rate limit: sleeps args.sleep (default 1.1 s) between requests.

Dependencies: stdlib only (network must be reachable from python).
"""
import argparse
import datetime
import json
import ssl
import sys
import time
import urllib.request

API = "https://api.opendota.com"
CTX = ssl._create_unverified_context()  # F-drive sandbox has broken CA chain
UA = {"User-Agent": "Mozilla/5.0 (dota-replay-analyzer list)"}

FIELDS = ["league_id", "league_name", "match_id", "start_time_utc", "radiant_win",
          "series_id", "series_type", "radiant_team_id", "dire_team_id"]


def http_json(path, timeout=60):
    req = urllib.request.Request(API + path, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def league_name_search(substr, leagues):
    s = substr.lower()
    return [(l.get("leagueid"), l.get("name")) for l in leagues
            if s in (l.get("name") or "").lower()]


def utc_iso(ts):
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d %H:%M")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--league-id", type=int, action="append", default=[])
    ap.add_argument("--league-name", action="append", default=[])
    ap.add_argument("--all-hits", action="store_true",
                    help="use every league matched by --league-name (default: warn + use all)")
    ap.add_argument("--limit", type=int, default=0, help="cap rows per league (0=all)")
    ap.add_argument("--since", default=None, help="only matches with start_time >= YYYY-MM-DD")
    ap.add_argument("--format", choices=["tsv", "csv"], default="tsv")
    ap.add_argument("--out", default=None, help="write table to file (default stdout)")
    ap.add_argument("--sleep", type=float, default=1.1)
    args = ap.parse_args()

    if not args.league_id and not args.league_name:
        ap.error("need --league-id and/or --league-name")

    # resolve names against the league dictionary (fetched once when needed)
    need_dict = bool(args.league_name) or bool(args.league_id)
    leagues = http_json("/api/leagues") if need_dict else []
    resolved = []
    for lid in args.league_id:
        name = next((l.get("name") for l in leagues if l.get("leagueid") == lid),
                    str(lid))
        resolved.append((lid, name or str(lid)))
    for name in args.league_name:
        hits = league_name_search(name, leagues)
        if not hits:
            print("no league matches %r" % name, file=sys.stderr)
        for lid, nm in hits:
            resolved.append((lid, nm))
    if not resolved:
        sys.exit("no leagues to fetch")

    # de-dup preserving order
    seen = set()
    resolved = [r for r in resolved if not (r[0] in seen or seen.add(r[0]))]
    if not args.all_hits:
        print("resolved leagues:", resolved, file=sys.stderr)

    delim = "," if args.format == "csv" else "\t"
    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    w = out.write
    w(delim.join(FIELDS) + "\n")

    since_ts = None
    if args.since:
        since_ts = datetime.datetime.strptime(args.since, "%Y-%m-%d").replace(
            tzinfo=datetime.UTC).timestamp()

    total = 0
    for lid, name in resolved:
        try:
            ms = http_json("/api/leagues/%d/matches" % lid)
        except Exception as e:
            print("league %s fetch failed: %r" % (lid, e), file=sys.stderr)
            continue
        ms = [m for m in ms if m.get("match_id")]
        ms.sort(key=lambda m: -(m.get("start_time") or 0))  # newest first
        n = 0
        for m in ms:
            if since_ts and (m.get("start_time") or 0) < since_ts:
                continue
            row = [str(lid), (name or "").replace(delim, " "),
                   str(m.get("match_id")),
                   utc_iso(m.get("start_time")),
                   "" if m.get("radiant_win") is None else str(bool(m.get("radiant_win"))),
                   str(m.get("series_id") or ""), str(m.get("series_type") or ""),
                   str(m.get("radiant_team_id") or ""), str(m.get("dire_team_id") or "")]
            w(delim.join(row) + "\n")
            n += 1
            total += 1
            if args.limit and n >= args.limit:
                break
        print("league %s (%s): %d matches listed (%s total)" % (
            lid, name or "?", n, len(ms)), file=sys.stderr)
        time.sleep(args.sleep)
    if out is not sys.stdout:
        out.close()
        print("wrote %d rows to %s" % (total, args.out), file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
