# -*- coding: utf-8 -*-
"""map_annotations.py - AUTHORITATIVE map annotation data derived from the real
Dota 2 (7.37) map file, NOT third-party static data.

Provenance: entities decompiled from `maps/dota_737.vpk` ->
`maps/dota/entities/default_ents.vents` (ValveResourceFormat), the compiled
default-entity keyvalues of the live in-game map. Every coordinate below is the
entity `origin` (world units) taken directly from that file, so the base image
(itself the decoded 7.37 official overview texture) and every overlay symbol
share one and the same source-of-truth: the real game map.

World space: the map is an axis-aligned square centred at (0,0). The
authoritative outer bounds come from the two `dota_minimap_boundary` entities
at (+/-9472, +/-9472) and `worldspawn._dotatilegrid_fogbounds_*` (also +/-
9472). So WORLD_SPAN = 2*9472 = 18944.
"""

# Playable square half-extent in world units (authoritative).
WORLD_SPAN = 18944.0
WORLD_HALF = WORLD_SPAN / 2.0

# (x, y, neutralType, pullType, triggerName) — neutralType 0..3, the two
# type==3 camps are the ANCIENT camps (one per side). Coordinates are the
# `npc_dota_neutral_spawner` entity origins (the spawn point inside each camp
# trigger), paired to the camp trigger by nearest distance. triggerName uses
# the real map targetname (neutralcamp_good_N / neutralcamp_evil_N).
NEUTRAL_CAMPS = [
    (3712.0, -5376.0, 0, 0, "neutralcamp_good_1"),
    (4800.0, -3776.0, 2, 3, "neutralcamp_good_2"),
    (2816.0, -3072.0, 3, 1, "neutralcamp_good_3"),   # ancient camp (radiant side)
    (512.0, -3840.0, 1, 1, "neutralcamp_good_4"),
    (128.0, -2176.0, 1, 1, "neutralcamp_good_5"),
    (-3840.0, 1125.0, 1, 0, "neutralcamp_good_7"),
    (-4928.0, -96.0, 2, 1, "neutralcamp_good_8"),
    (-2304.0, -4160.0, 1, 1, "neutralcamp_good_9"),
    (8320.0, -1088.0, 2, 0, "neutralcamp_good_10"),
    (-192.0, -7616.0, 1, 0, "neutralcamp_good_11"),
    (-2368.0, -8384.0, 3, 1, "neutralcamp_good_12"),
    (1664.0, -8448.0, 1, 0, "neutralcamp_good_13"),
    (4032.0, -8256.0, 0, 0, "neutralcamp_good_14"),
    (4800.0, -7296.0, 2, 1, "neutralcamp_good_15"),
    (-4800.0, 4032.0, 0, 0, "neutralcamp_evil_1"),
    (-3520.0, 4800.0, 0, 2, "neutralcamp_evil_2"),
    (-2496.0, 3584.0, 2, 2, "neutralcamp_evil_3"),
    (1344.0, 4224.0, 1, 1, "neutralcamp_evil_4"),
    (192.0, 2752.0, 2, 1, "neutralcamp_evil_5"),
    (-1408.0, 5056.0, 1, 1, "neutralcamp_evil_6"),
    (4352.0, 48.0, 3, 0, "neutralcamp_evil_8"),      # ancient camp (dire side)
    (3392.0, -1408.0, 2, 1, "neutralcamp_evil_9"),
    (-8576.0, 768.0, 2, 0, "neutralcamp_evil_10"),
    (320.0, 7616.0, 1, 0, "neutralcamp_evil_11"),
    (2701.1, 8307.7, 2, 0, "neutralcamp_evil_12"),
    (-1408.0, 8256.0, 1, 0, "neutralcamp_evil_13"),
    (-3456.0, 8448.0, 2, 0, "neutralcamp_evil_14"),
    (-4288.0, 7488.0, 2, 0, "neutralcamp_evil_15"),
]

# Roshan pit spawn point (single spawner for the current map). The two pit
# triggers are roshan_location (radiant) and roshan_location_2 (dire); pit
# spawn is the single npc_dota_roshan_spawner.
ROSHAN = (7872.0, -7808.0)

# --- authoritative extra map features (all world units from default_ents) ---

# npc_dota_unit_twin_gate (双生门) — one per side.
TWIN_GATES = [
    (-6144.0, 7552.0, "dire"),
    (5888.0, -7168.0, "radiant"),
]

# npc_dota_fort (ancient / 遗迹)
FORTS = [
    (-5919.99, -5351.99, "radiant"),
    (5527.99, 4999.99, "dire"),
]

# ent_dota_fountain (泉水)
FOUNTAINS = [
    (-7456.0, -6938.0, "radiant"),
    (7408.0, 6848.0, "dire"),
]

# shop / secret shop (ent_dota_shop + trigger_shop, deduped by proximity)
SHOPS = [
    (-7542.5, -6171.2),   # radiant base shop
    (-5080.1, 1948.0),    # radiant secret shop
    (6697.4, 6809.3),     # dire base shop
    (4886.1, -1207.7),    # dire secret shop
]

# npc_dota_tower — full 22 towers (side, lane, tier, x, y)
TOWERS = [
    ("radiant", "top", 1, -6336.0, 1856.0),
    ("radiant", "top", 2, -6288.0, -872.0),
    ("radiant", "top", 3, -6592.0, -3408.0),
    ("radiant", "top", 4, -5712.0, -4864.0),
    ("radiant", "mid", 1, -1544.0, -1408.0),
    ("radiant", "mid", 2, -3336.2, -2791.8),
    ("radiant", "mid", 3, -4640.0, -4144.0),
    ("radiant", "bot", 1, 4924.0, -6080.0),
    ("radiant", "bot", 2, -360.0, -6256.0),
    ("radiant", "bot", 3, -3952.0, -6112.0),
    ("radiant", "bot", 4, -5392.0, -5192.0),
    ("dire", "top", 1, -4672.0, 6016.0),
    ("dire", "top", 2, -128.0, 6016.0),
    ("dire", "top", 3, 3552.0, 5776.0),
    ("dire", "top", 4, 4944.0, 4776.0),
    ("dire", "mid", 1, 524.0, 652.0),
    ("dire", "mid", 2, 2496.0, 2112.0),
    ("dire", "mid", 3, 4272.0, 3759.0),
    ("dire", "bot", 1, 6269.3, -2240.0),
    ("dire", "bot", 2, 6400.0, 384.0),
    ("dire", "bot", 3, 6336.0, 3032.0),
    ("dire", "bot", 4, 5280.0, 4432.0),
]


def camp_by_team():
    """Neutral camps split by side (trigger 'good' ~ radiant, 'evil' ~ dire)."""
    radiant, dire = [], []
    for c in NEUTRAL_CAMPS:
        (dire if "evil" in c[4] else radiant).append(c)
    return radiant, dire
