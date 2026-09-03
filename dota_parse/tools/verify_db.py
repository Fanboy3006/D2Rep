"""Independent verification of a dota_parse output database.

Usage: python verify_db.py <path-to.db> [match_id]

Cross-checks, from a *different* toolchain (python sqlite3) than the writer:
  1. row counts of the three generic tables for the match
  2. `extra` / `properties` are valid JSON and carry expected keys
  3. radiant heroes mostly stay in the negative-x quadrant and dire in the
     positive-x quadrant (fountain/coordinate sanity per ARCHITECTURE §6.6)
  4. purchase event actors / identity hero names agree with snapshot entity ids
  5. event_type distribution
"""
import json
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else r"F:\D2Rep_project\dota_replay_analyzer\8592126358.db"
MATCH = int(sys.argv[2]) if len(sys.argv) > 2 else None

con = sqlite3.connect(DB)
cur = con.cursor()

tables = ["entity_snapshots", "game_events", "player_identity"]
total = {}
for t in tables:
    q = f"SELECT COUNT(*) FROM {t}"
    total[t] = cur.execute(q).fetchone()[0]
print("total rows per table:", total)

# pick a match: the biggest one if none given
if MATCH is None:
    MATCH = cur.execute(
        "SELECT match_id FROM entity_snapshots GROUP BY match_id ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()[0]
print("verifying match_id:", MATCH)

n_snap, n_ev, n_pl = (
    cur.execute("SELECT COUNT(*) FROM entity_snapshots WHERE match_id=?", (MATCH,)).fetchone()[0],
    cur.execute("SELECT COUNT(*) FROM game_events WHERE match_id=?", (MATCH,)).fetchone()[0],
    cur.execute("SELECT COUNT(*) FROM player_identity WHERE match_id=?", (MATCH,)).fetchone()[0],
)
print(f"match rows: snapshots={n_snap} events={n_ev} identity={n_pl}")
assert n_snap > 1000 and n_ev > 0 and n_pl == 10, "unexpected row counts"

# --- JSON validity of extra / properties ---------------------------------
bad_json = 0
for col, t in (("extra", "entity_snapshots"), ("properties", "game_events")):
    for (v,) in cur.execute(f"SELECT {col} FROM {t} WHERE match_id=? LIMIT 20000", (MATCH,)):
        if v is None:
            continue
        try:
            json.loads(v)
        except Exception as e:
            bad_json += 1
            if bad_json < 5:
                print("bad json:", repr(v), e)
print("invalid json cells:", bad_json)
assert bad_json == 0

# sample extra keys
row = cur.execute(
    "SELECT entity_id, team, x, y, hp, extra FROM entity_snapshots WHERE match_id=? LIMIT 1", (MATCH,)
).fetchone()
print("sample snapshot:", row)
e = json.loads(row[5])
assert {"z", "pid", "player_slot", "class", "team_code"} <= set(e.keys()), "extra missing keys"

# --- coordinate side sanity (anchor on earliest sample = fountain) -------
# Verified fact (§6.6): radiant fountain sits in the negative coordinate
# quadrant, dire fountain in the positive one. Mid-game bboxes span the whole
# map, so we only check each entity's earliest sample.
# §4.2 note: some CDN recordings start mid-match (demo time axis offset) -
# then the fountain check does not apply; skip it when the match's first hero
# sample is late.
(t0,) = cur.execute(
    "SELECT MIN(game_time_sec) FROM entity_snapshots "
    "WHERE match_id=? AND entity_type='hero'", (MATCH,)).fetchone()
if t0 is not None and t0 >= 300:
    print(f"\nfountain-side check SKIPPED: first hero sample at t={t0}s "
          ">= 300s (mid-start recording, see ARCHITECTURE 4.2 note)")
else:
    print("\nfountain-side sanity (earliest sample per entity):")
    rows = list(cur.execute(
        """SELECT entity_id, team, game_time_sec, x, y
           FROM entity_snapshots WHERE match_id=? ORDER BY entity_id, game_time_sec""",
        (MATCH,),
    ))
    side_bad = 0
    first_of = {}
    for entity_id, team, t, x, y in rows:
        if entity_id not in first_of:
            first_of[entity_id] = (team, t, x, y)
    for entity_id, (team, t, x, y) in sorted(first_of.items()):
        if team == "radiant":
            ok = x < 0 and y < 0
        elif team == "dire":
            ok = x > 0 and y > 0
        else:
            ok = True  # unknown team (e.g. summons without player slot): no assert
        if not ok:
            side_bad += 1
        print(f"  {entity_id:<46} {team:<8} t={t:<5} x={x:9.0f} y={y:9.0f} ok={ok}")
    assert side_bad == 0, f"{side_bad} heroes start on the wrong map half"

# --- actor / identity agreement ------------------------------------------
heroes = set(r[0] for r in cur.execute(
    "SELECT hero_name FROM player_identity WHERE match_id=?", (MATCH,)))
actors = set(r[0] for r in cur.execute(
    "SELECT DISTINCT actor_id FROM game_events WHERE match_id=? AND event_type='purchase'", (MATCH,)))
entities = set(r[0] for r in cur.execute(
    """SELECT DISTINCT entity_id FROM entity_snapshots
       WHERE match_id=? AND entity_type='hero'
         AND json_extract(extra, '$.player_slot') IS NOT NULL""", (MATCH,)))
print("\nidentity heroes:", len(heroes), "| purchase actors:", len(actors),
      "| snapshot entities:", len(entities))
print("identity == snapshot entities:", heroes == entities)
print("actors subset of heroes:", actors <= heroes)
assert heroes == entities and actors <= heroes

# item names present
items = set()
for (props,) in cur.execute(
    "SELECT properties FROM game_events WHERE match_id=? AND event_type='purchase'", (MATCH,)):
    p = json.loads(props)
    items.add(p.get("item"))
print("distinct items bought:", len(items), "sample:", sorted(items)[:5])

print("\nevents by type:")
for r in cur.execute("SELECT event_type, COUNT(*) FROM game_events WHERE match_id=? GROUP BY event_type", (MATCH,)):
    print("  ", r)

# --- ward events sanity (§8 step 6 extractor) ----------------------------
# placed: every row has a position inside the map window, a team (2/3) and a
# ward_type; destroyed: authoritative target unit + reason expired/dewarded.
# Counts should sit in the "dozens" range for a normal full match (e.g. ~50-150
# placed + ~same destroyed), never 0 or thousands.
def ward_bounds():
    n = 0
    bad = 0
    for _t, x, y, props in cur.execute(
            """SELECT game_time_sec, x, y, properties
               FROM game_events WHERE match_id=? AND event_type='ward_placed'""", (MATCH,)):
        n += 1
        p = json.loads(props)
        ok = (x is not None and y is not None and abs(x) <= 20000 and abs(y) <= 20000
              and p.get("ward_type") in ("observer", "sentry")
              and p.get("team") in (2, 3))
        if not ok:
            bad += 1
            if bad <= 5:
                print("  bad placed:", _t, x, y, props)
    return n, bad

n_pl, bad_pl = ward_bounds()
n_des = cur.execute("SELECT COUNT(*) FROM game_events WHERE match_id=? AND event_type='ward_destroyed'",
                    (MATCH,)).fetchone()[0]
print(f"\nward_placed={n_pl} (invalid rows: {bad_pl}) | ward_destroyed={n_des}")
assert bad_pl == 0, "ward_placed rows with missing/out-of-range coords or bad props"
assert 10 <= n_pl <= 400, "ward_placed count out of plausible range"
assert 10 <= n_des <= 400, "ward_destroyed count out of plausible range"
print("ward_placed by type/team:")
for r in cur.execute(
        """SELECT json_extract(properties,'$.ward_type'), json_extract(properties,'$.team'), COUNT(*)
           FROM game_events WHERE match_id=? AND event_type='ward_placed'
           GROUP BY 1,2 ORDER BY 2,1""", (MATCH,)):
    print("  ", r)
print("ward_destroyed by reason/type:")
for r in cur.execute(
        """SELECT json_extract(properties,'$.ward_type'), json_extract(properties,'$.reason'), COUNT(*)
           FROM game_events WHERE match_id=? AND event_type='ward_destroyed'
           GROUP BY 1,2 ORDER BY 1,2""", (MATCH,)):
    print("  ", r)
# destroyed targets must be real ward units; self-expiry rows have no actor
bad_tgt = cur.execute(
    """SELECT COUNT(*) FROM game_events WHERE match_id=?
       AND event_type='ward_destroyed'
       AND target_id NOT IN ('npc_dota_observer_wards','npc_dota_sentry_wards')""",
    (MATCH,)).fetchone()[0]
assert bad_tgt == 0, "ward_destroyed rows with non-ward targets"

# hp coverage (informational)
withhp = cur.execute(
    "SELECT COUNT(*) FROM entity_snapshots WHERE match_id=? AND hp IS NOT NULL", (MATCH,)).fetchone()[0]
print(f"\nhp populated on {withhp}/{n_snap} snapshots ({100.0*withhp/n_snap:.1f}%)")

print("\nALL CHECKS PASSED")
