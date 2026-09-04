# -*- coding: utf-8 -*-
"""map_annotations.py - static map annotation data (neutral camps, Roshan pit)
for the 7.33+ (twin-gate) map used by this corpus, in replay world units.

Provenance: devilesk/dota-interactive-map assets/data/723/mapdata.json
(fetched 2026-09-04) — entity coordinates dumped from the game map by
leamare/dota-map-coordinates. Verified against replay-derived tower world
positions for match 8822238357: dozens of towers match to the unit, so this
dataset shares the map layout AND the world coordinate space of our replays.
"""

# (x, y, neutralType, pullType, triggerName)
# neutralType observed values: 0..3 ; the two type==3 camps are the ANCIENT
# camps (one per side). pullType marks stack/pull behaviour, unused here.
NEUTRAL_CAMPS = [
    (-2608, -648, 2, 0, "neutralcamp_good_6"),
    (-916, 2237, 1, 0, "neutralcamp_evil_6"),
    (4452, 840, 2, 1, "neutralcamp_evil_9"),
    (3018, -4526, 0, 0, "neutralcamp_good_1"),
    (-2464, 4816, 0, 2, "neutralcamp_evil_2"),
    (-4339, 3440, 2, 2, "neutralcamp_evil_1"),
    (4800, -4288, 2, 3, "neutralcamp_good_2"),
    (-1848, -4216, 2, 1, "neutralcamp_good_5"),
    (-1864, 4432, 1, 1, "neutralcamp_evil_3"),
    (-335, -3387, 1, 1, "neutralcamp_good_4"),
    (-4967, -380, 2, 1, "neutralcamp_good_8"),
    (4195, -363, 3, 0, "neutralcamp_evil_8"),   # ancient camp (dire side)
    (-132, -2072, 1, 1, "neutralcamp_good_9"),
    (1208, 3415, 2, 1, "neutralcamp_evil_5"),
    (-11, 3667, 2, 0, "neutralcamp_evil_4"),
    (2128, -392, 1, 1, "neutralcamp_evil_7"),
    (-3968, 1285, 1, 0, "neutralcamp_good_7"),
    (1263, -5355, 3, 1, "neutralcamp_good_3"),  # ancient camp (radiant side)
]

# Roshan pit spawn point (the pit sits around this spawn; single spawner)
ROSHAN = (-2919, 2315)


def camp_by_team():
    """Neutral camps split by the side of the river they belong to
    (approximate: trigger 'good' ~ radiant, 'evil' ~ dire)."""
    radiant, dire = [], []
    for c in NEUTRAL_CAMPS:
        (dire if "evil" in c[4] else radiant).append(c)
    return radiant, dire
