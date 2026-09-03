#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_league.py - OpenDota data pipeline for one league
(ARCHITECTURE.md section 8 step 1): league -> match list -> team-name
lookup -> per-match radiant_gold_adv -> stats.db.

Pipeline per match:
  1. skip if the match already has gold_adv rows (idempotent; --refresh to force)
  2. GET /matches/{id}: duration, start_time, radiant_win, radiant_gold_adv
  3. radiant_gold_adv empty -> POST /request/{match_id} (opendota parses the
     replay), wait, re-GET once; still empty -> leave a note and continue
     (a later run will retry)
  4. write teams dictionary (deduped) + match row + per-minute gold_adv
     (raw radiant perspective; the dire view = -value, see stats_db.gold_adv_for)

Rate limit: ~60 req/min -> default sleep 1.2 s between every API call, plus
backoff retries on HTTP 429/5xx. Resume-safe: committed per match.

Usage:
    python opendota/fetch_league.py [--league 19719] [--stats stats.db]
                                    [--sleep 1.2] [--refresh]

Prints are ASCII-only on purpose (console code-page safety).
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.opendota.com"
UA = {"User-Agent": "Mozilla/5.0 (dota-replay-analyzer pipeline)"}


def http_json(url, method="GET", body=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=UA, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8", "replace"))


def api_call(url, method="GET", body=None, retries=3):
    """Rate-limited API call with backoff on 429/5xx/transport errors."""
    for attempt in range(retries):
        try:
            status, data = http_json(url, method=method, body=body)
            if status == 200:
                return data
            # 429/5xx: backoff and retry
            print("  http %d on %s (attempt %d/%d)" %
                  (status, url.split("?")[0], attempt + 1, retries))
        except urllib.error.HTTPError as e:
            print("  HTTPError %s on %s (attempt %d/%d)" %
                  (e.code, url.split("?")[0], attempt + 1, retries))
            if e.code == 404:
                return None
        except Exception as e:
            print("  error %r on %s (attempt %d/%d)" %
                  (e, url.split("?")[0], attempt + 1, retries))
        time.sleep(3 * (attempt + 1))
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", type=int, default=19719)
    ap.add_argument("--stats", default=None)
    ap.add_argument("--sleep", type=float, default=1.2,
                    help="seconds between API calls (rate limit ~60/min)")
    ap.add_argument("--refresh", action="store_true",
                    help="re-fetch matches that already have gold_adv")
    args = ap.parse_args()

    sys.path.insert(0, ROOT)
    from opendota import stats_db as sd

    stats_path = args.stats or sd.DEFAULT_STATS
    con = sd.connect(stats_path)
    sd.ensure_schema(con)
    league = args.league
    sleep_s = max(args.sleep, 0.5)

    # --- league name (dictionary) ---
    league_name = None
    try:
        leagues = api_call("%s/api/leagues" % API)
        for l in leagues or []:
            if l.get("leagueid") == league:
                league_name = l.get("name")
                break
    except Exception as e:
        print("league list unavailable: %r" % e)
    sd.upsert_league(con, league, league_name)
    con.commit()
    print("league %d = %s" % (league, league_name or "?"))

    # --- match list ---
    matches = api_call("%s/api/leagues/%d/matches" % (API, league))
    if not isinstance(matches, list):
        print("FAILED to fetch league match list")
        return 1
    print("league %d: %d matches" % (league, len(matches)))
    time.sleep(sleep_s)

    n_processed = n_skipped = n_with_gold = n_no_gold = 0
    team_requests = 0
    gold_minutes = 0
    for i, m in enumerate(matches, 1):
        match_id = m.get("match_id")
        if not match_id:
            continue
        # --- idempotency: skip matches already holding gold data ---
        if not args.refresh and sd.match_row(con, match_id) and sd.has_gold_adv(con, match_id):
            n_skipped += 1
            continue

        # team name dictionary (deduped; team ids resolved from list + detail)
        for tid in (m.get("radiant_team_id"), m.get("dire_team_id")):
            if tid and sd.team_name(con, tid) is None:
                tm = api_call("%s/api/teams/%s" % (API, tid))
                time.sleep(sleep_s)
                if tm and tm.get("name"):
                    sd.upsert_team(con, tid, tm.get("name"))
                    team_requests += 1
                else:
                    sd.upsert_team(con, tid, None)

        detail = api_call("%s/api/matches/%s" % (API, match_id))
        time.sleep(sleep_s)
        if detail is None:
            print("  match %s: detail unavailable, skipped" % match_id)
            continue

        gold = detail.get("radiant_gold_adv")
        if gold is None or len(gold) == 0:
            # trigger opendota parsing, then retry once
            print("  match %s: radiant_gold_adv empty -> POST /request/%s"
                  % (match_id, match_id))
            api_call("%s/api/request/%s" % (API, match_id), method="POST",
                     body={"match_id": match_id})
            sd.mark_parse_requested(con, match_id)
            con.commit()
            time.sleep(max(sleep_s, 15))
            detail2 = api_call("%s/api/matches/%s" % (API, match_id))
            time.sleep(sleep_s)
            if detail2:
                detail = detail2
            gold = detail.get("radiant_gold_adv") if detail else None
            if gold is None or len(gold) == 0:
                n_no_gold += 1

        rw = detail.get("radiant_win")
        if rw is None:
            rw = m.get("radiant_win")
        sd.upsert_match(con, {
            "match_id": match_id,
            "league_id": league,
            "radiant_team_id": m.get("radiant_team_id"),
            "dire_team_id": m.get("dire_team_id"),
            "start_time": detail.get("start_time") or m.get("start_time"),
            "duration_sec": detail.get("duration"),
            "radiant_win": (1 if rw is True else 0 if rw is False else None),
            "series_id": m.get("series_id"),
            "series_type": m.get("series_type"),
            "game_mode": detail.get("game_mode"),
        }, metadata={"source_list_index": i})
        n_processed += 1

        if gold is not None and len(gold):
            n_min = sd.set_gold_adv(
                con, match_id,
                ((mn, v) for mn, v in enumerate(gold) if v is not None))
            if n_min > 0:
                n_with_gold += 1
                gold_minutes += n_min
                if i % 10 == 1:
                    print("  match %s: +%d minutes (progress %d/%d)"
                          % (match_id, n_min, i, len(matches)))
        con.commit()

    print("\nsummary: league=%d matches=%d processed=%d with_gold=%d "
          "no_gold=%d skipped=%d | team_requests=%d gold_minutes=%d"
          % (league, len(matches), n_processed, n_with_gold, n_no_gold,
             n_skipped, team_requests, gold_minutes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
