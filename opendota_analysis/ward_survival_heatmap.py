#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ward_survival_heatmap.py - Module A: Observer Ward survival / dewarded
heatmap across all parsed matches.

Pipeline:
  1. enumerate every parsed match db under dems/db
  2. extract observer ward_placed (sec, team, x, y) and ward_destroyed
     (sec, team, reason=dewarded|expired) - team comes from the enriched
     parser (combat-log target_team)
  3. per (match, team) FIFO-pair each destroyed observer with the earliest
     still-alive placed observer -> lifetime = destroy - place
     (wards still alive at game end are censored at end-of-match and counted
     as "expired" with lifetime = end - place)
  4. keep instances with lifetime >= 300 s (>=5 min), classify expired/dewarded
  5. spatial bins (default 400 world units): per bin show dewarded share,
     draw heat overlay on the shared map background
  6. outputs: PNG (assets dir / analysis output) + text summary

Usage:
    python opendota_analysis/ward_survival_heatmap.py [--db-root dems/db]
        [--min-lifetime 300] [--bin 400] [--out .tmp/ward_heat.png]
"""
import argparse
import collections
import glob
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from opendota_analysis import map_background as mb  # noqa: E402

MIN_LIFETIME = 300
BIN = 400  # world units per heatmap cell


def zone_name(x, y):
    """Coarse textual zone for a world coordinate (approximate; for summaries).
    Map: river runs bottom-left(-,-) to top-right(+,+); radiant=SW half."""
    if abs(x) < 900 and abs(y) < 900:
        return "river/mid area"
    side = "radiant" if (x + y) < 0 else "dire"
    vertical = "top" if y > 400 else "bottom"
    if abs(x) > 6200 or abs(y) > 6200:
        zone = "%s %s corner/base area" % (side, vertical)
    elif abs(x) > 3400 or abs(y) > 3400:
        zone = "%s %s outer (lane-side jungle)" % (side, vertical)
    else:
        zone = "%s %s inner jungle" % (side, vertical)
    return zone


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db-root", default=os.path.join(HERE, "..", "dems", "db"))
    ap.add_argument("--min-lifetime", type=int, default=MIN_LIFETIME)
    ap.add_argument("--bin", type=int, default=BIN)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from PIL import Image, ImageDraw  # deferred import (Pillow optional)

    half = mb.WORLD_SPAN / 2.0
    n_cells = int(round(mb.WORLD_SPAN / args.bin))
    # grid counts: [dewarded, expired] per cell
    grid = [[[0, 0] for _ in range(n_cells)] for _ in range(n_cells)]

    stats = collections.Counter()      # expired / dewarded / alive
    lifetimes = []
    n_match = 0
    dbs = sorted(glob.glob(os.path.join(args.db_root, "*", "*.db")))
    for db in dbs:
        con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(db).replace("\\", "/"),
                              uri=True)
        # end of match approx = last hero snapshot second
        end = con.execute("SELECT MAX(game_time_sec) FROM entity_snapshots").fetchone()[0]
        placed = collections.defaultdict(list)  # team -> [ (sec, x, y) ]
        for sec, team, x, y in con.execute(
                """SELECT game_time_sec, json_extract(properties,'$.team'),
                          x, y FROM game_events WHERE event_type='ward_placed'
                   AND json_extract(properties,'$.ward_type')='observer'"""):
            if team is not None:
                placed[int(team)].append((int(sec), float(x), float(y)))
        destroyed = collections.defaultdict(list)  # team -> [(sec, reason)]
        for sec, team, reason in con.execute(
                """SELECT game_time_sec, json_extract(properties,'$.team'),
                          json_extract(properties,'$.reason')
                   FROM game_events WHERE event_type='ward_destroyed'
                   AND json_extract(properties,'$.ward_type')='observer'"""):
            if team is not None:
                destroyed[int(team)].append((int(sec), reason))
        con.close()
        n_match += 1
        for team in set(placed) | set(destroyed):
            q = sorted(placed.get(team, []))
            di = 0
            destroy = sorted(destroyed.get(team, []))
            used = [False] * len(destroy)
            for (sec, x, y) in q:
                # earliest destroyed of this team with time >= placed
                match_idx = None
                for i, (dsec, reason) in enumerate(destroy):
                    if not used[i] and dsec >= sec:
                        match_idx = i
                        break
                if match_idx is not None:
                    used[match_idx] = True
                    dsec, reason = destroy[match_idx]
                    lifetime = dsec - sec
                else:
                    # survived to the end: censor at end
                    lifetime = max(0, (end or 0) - sec)
                    reason = "expired"
                if lifetime < 0:
                    lifetime = 0
                if lifetime >= args.min_lifetime:
                    stat_key = "dewarded" if reason == "dewarded" else "expired"
                    stats[stat_key] += 1
                    lifetimes.append(lifetime)
                    cx = int((x + half) / args.bin)
                    cy = int((y + half) / args.bin)
                    if 0 <= cx < n_cells and 0 <= cy < n_cells:
                        grid[cy][cx][0 if reason == "dewarded" else 1] += 1
            # leftover destroyed with no placed (approx): ignore
    total = stats["dewarded"] + stats["expired"]
    print("matches=%d observer instances >= %ds: dewarded=%d expired=%d total=%d"
          % (n_match, args.min_lifetime, stats["dewarded"], stats["expired"], total))
    if total:
        print("dewarded rate=%.1f%% expired rate=%.1f%%" %
              (100.0 * stats["dewarded"] / total, 100.0 * stats["expired"] / total))
    if lifetimes:
        import statistics
        print("lifetime median=%.0fs p95=%.0fs max=%.0fs" %
              (statistics.median(lifetimes),
               sorted(lifetimes)[int(0.95 * len(lifetimes)) - 1], max(lifetimes)))

    # render overlay
    if args.out is None:
        args.out = os.path.join(HERE, "..", ".tmp", "ward_survival_heat.png")
    im = mb.load_map().convert("RGBA")
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)
    scale = im.width / mb.WORLD_SPAN
    cellsize = args.bin * scale
    hotspots = []
    for cy in range(n_cells):
        for cx in range(n_cells):
            d, e = grid[cy][cx]
            n = d + e
            if n < 3:
                continue
            x0 = cx * cellsize
            y0 = cy * cellsize
            ratio = d / n
            # red = high deward share; blue = mostly expired; alpha by count
            alpha = min(160, 40 + 10 * n)
            col = (int(255 * ratio), 40, int(255 * (1 - ratio)), alpha)
            dr.rectangle([x0, y0, x0 + cellsize, y0 + cellsize], fill=col)
            wxc = (cx + 0.5) * args.bin - half
            wyc = half - (cy + 0.5) * args.bin
            hotspots.append((ratio, n, d, e, wxc, wyc))
    out_img = Image.alpha_composite(im, ov)
    out_img.save(args.out)
    print("heatmap ->", args.out)
    print("cells with n>=3:", len(hotspots), "top hotspots (deward share, n>=5):")
    shown = 0
    for ratio, n, d, e, wxc, wyc in sorted(hotspots, key=lambda h: -h[0]):
        if n >= 5:
            print("  dewarded %.0f%% (n=%d d=%d) @ world (%.0f, %.0f) ~ %s"
                  % (100 * ratio, n, d, wxc, wyc, zone_name(wxc, wyc)))
            shown += 1
            if shown >= 15:
                break


if __name__ == "__main__":
    sys.exit(main())
