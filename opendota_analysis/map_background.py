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
    [-S/2, S/2] with S = WORLD_SPAN (default 18944 for the 7.33+/7.37 map,
    matching the real map's authoritative dota_minimap_boundary; the Dota world
    bounds are symmetric).
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
# AUTHORITATIVE 7.37+ map: 18944 = 2*9472, taken from the real map file
# (maps/dota_737.vpk -> default_ents.vents: two dota_minimap_boundary entities
# at (+/-9472,+/-9472) and worldspawn._dotatilegrid_fogbounds_*). Matches the
# decoded official overview base image (assets/dota_map_1024.png).
WORLD_SPAN = 18944.0


def set_world_span(span):
    global WORLD_SPAN
    WORLD_SPAN = float(span)


def load_map(path=None):
    from PIL import Image
    return Image.open(path or DEFAULT_MAP)


def map_to_px(x, y, size):
    """Convert world (x, y) to pixel (px, py) in an image of side `size`.

    Uses the authoritative fountain-anchored isotropic calibration (see
    map_annotations.CALIB_*), scaled to the requested image size (constants are
    calibrated on the 1024x1024 overview)."""
    from opendota_analysis import map_annotations as mann
    s = size / 1024.0
    px = (mann.CALIB_OFFX + mann.CALIB_K * x) * s
    py = (mann.CALIB_REF_Y - mann.CALIB_K * y) * s
    return px, py


def px_to_world(px, py, size):
    from opendota_analysis import map_annotations as mann
    s = size / 1024.0
    x = ((px / s) - mann.CALIB_OFFX) / mann.CALIB_K
    y = (mann.CALIB_REF_Y - (py / s)) / mann.CALIB_K
    return x, y
