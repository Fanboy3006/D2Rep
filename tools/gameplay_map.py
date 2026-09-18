# -*- coding: utf-8 -*-
"""gameplay_map.py - render a FULL-MAP distribution of gameplay-affecting terrain
features (rivers/water, ramps/highground, trees/blockers, cliffs) using the world
coordinates from instance_list.json. This shows WHERE things that affect movement /
vision actually are (e.g. river speed-boost zones, high-ground ramps, tree lines).
"""
import json, io, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

il = json.load(open(r"F:\D2Rep_project\dota_replay_analyzer\dist\instance_list.json", encoding="utf-8"))
mi = il["model_instances"]

CANVAS = 2048
K, OFFX, REFY = 0.049038, 508.3019, 504.5433
s = CANVAS / 1024.0
def w2p(x, y):
    return (OFFX + K * x) * s, (REFY - K * y) * s

def classify(name):
    n = name.lower()
    if any(k in n for k in ['water', 'flow', 'river', 'reservoir', 'waterfall', 'pond', 'lake', 'stream']):
        return 'water'
    if any(k in n for k in ['ramp', 'cliff', 'highground', 'hill']):
        return 'ramp'
    if any(k in n for k in ['tree', 'oak', 'gnarly', 'forest']):
        return 'tree'
    if any(k in n for k in ['stone', 'rock', 'boulder', 'crystal']):
        return 'rock'
    if any(k in n for k in ['camp', 'ward', 'portal', 'shrine']):
        return 'structure'
    return 'other'

COL = {'water': (40, 120, 255), 'ramp': (255, 150, 0), 'tree': (40, 200, 60),
       'rock': (150, 150, 150), 'structure': (255, 80, 200), 'other': (90, 90, 90)}

img = Image.new("RGB", (CANVAS, CANVAS), (16, 18, 24))
dr = ImageDraw.Draw(img)
counts = {}
for mp, lst in mi.items():
    c = classify(mp)
    counts[c] = counts.get(c, 0) + len(lst)
    col = COL[c]
    for (tx, ty, tz) in lst:
        px, py = w2p(tx, ty)
        if 0 <= px < CANVAS and 0 <= py < CANVAS:
            r = 3
            dr.ellipse([px - r, py - r, px + r, py + r], fill=col)

try:
    font = ImageFont.truetype("arial.ttf", 22)
except Exception:
    font = ImageFont.load_default()
legend = [('water (river/flow)', COL['water']), ('ramp/highground/cliff', COL['ramp']),
          ('tree/blocker', COL['tree']), ('rock/crystal', COL['rock']),
          ('structure', COL['structure'])]
y = 12
for lab, col in legend:
    dr.rectangle([12, y - 10, 30, y + 8], fill=col)
    dr.text((36, y - 12), "%s  (%d)" % (lab, counts.get(col and {v:k for k,v in COL.items()}.get(col), counts.get('water',0))) if False else "%s" % lab, fill=(230,230,230), font=font)
    # quick count text
    real = {v:k for k,v in COL.items()}[col]
    dr.text((360, y - 12), "n=%d" % counts.get(real, 0), fill=(180,180,180), font=font)
    y += 34

out = r"F:\D2Rep_project\dota_replay_analyzer\dist\gameplay_features_map.png"
img.save(out)
print("counts:", counts)
print("saved", out, "size", img.size)
