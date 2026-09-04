#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""map_background.py - shared map background + world->pixel mapping for the
visualization modules A/B.

Asset: a decoded Dota 2 overview/minimap image (source: community-decoded
official overview used by replay tools; 7.33+ map layout, world span
calibrated below). The PNG is checked into opendota_analysis/assets and can be
copied to any machine - no Dota 2 install or VPK tooling needed to use it.

Mapping convention (verified against replay position data, see make_overlay):
  * world coordinates x (east+) and y (north+, dire side is +y) span roughly
    [-S/2, S/2] with S = WORLD_SPAN (default 19134 for the 7.33+ map, matching
    the replay corpus analysed here; the Dota world bounds are symmetric).
  * image row 0 = top = north (+y), so:
        px = (x + S/2) / S * size
        py = (S/2 - y) / S * size
  * callers can override S/center via set_world_span() after measuring their
    own data (see calibration section below).

Usage:
    from map_background import load_map, map_to_px
    im = load_map("opendota_analysis/assets/dota_map.png")
    px, py = map_to_px(-7100.0, -6400.0, im.width)
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MAP = os.path.join(HERE, "assets", "dota_map_1024.png")

# World span across the full map image (units of world coordinate).
# 7.33+ map: 19134 (used by replay-map projects for the post-7.33 layout).
WORLD_SPAN = 19134.0


def set_world_span(span):
    global WORLD_SPAN
    WORLD_SPAN = float(span)


def load_map(path=None):
    from PIL import Image
    return Image.open(path or DEFAULT_MAP)


def map_to_px(x, y, size):
    """Convert world (x, y) to pixel (px, py) in an image of side `size`."""
    half = WORLD_SPAN / 2.0
    px = (x + half) / WORLD_SPAN * size
    py = (half - y) / WORLD_SPAN * size
    return px, py


def px_to_world(px, py, size):
    half = WORLD_SPAN / 2.0
    x = px / size * WORLD_SPAN - half
    y = half - py / size * WORLD_SPAN
    return x, y
