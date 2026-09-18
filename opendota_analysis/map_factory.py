# -*- coding: utf-8 -*-
"""map_factory.py - 可复用的"地图工厂"管线（地图复现层）。

目的：把从官方 Dota overview 到"可标注的成品底图"这条链路工程化，供每次地图大版本更新 /
每次生成 viewer 底图时一键复用。

管线：
  1. build_base(size)    —— 官方 overview 超分放大 + 高清增强
  2. annotate(im, ...)   —— 用 map_annotations 权威坐标 + CALIB 世界→像素，按官方 minimap 语义叠加
                            塔(绿/红方块)、野点(黄三角, 远古绿)、神符(赏金金圈/普通/智慧)、
                            肉山(红骷髅)、双生门/遗迹/泉水
  3. render(...) / __main__ —— 串起来输出成品底图 PNG

用法：
  python -m opendota_analysis.map_factory --size 4096 --out .tmp/map_factory_4096.png
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw, ImageFilter, ImageEnhance, ImageFont
from opendota_analysis import map_annotations as mann

DEFAULT_BASE = os.path.join(HERE, "assets", "dota_map_1024.png")

RADIANT = "#3fbf4f"
DIRE = "#e5484d"
CAMP_YELLOW = "#e8b64c"
ANCIENT = "#8fd14f"
BOUNTY = "#ffb020"      # 赏金符金橙
POWERUP = "#5aa0ff"     # 普通符(幻象/加速/双倍) 蓝
XPRUNE = "#6fe0c8"      # 智慧/经验符 青


def build_base(size, src=None, enhance=True):
    im = Image.open(src or DEFAULT_BASE).convert("RGB")
    im = im.resize((size, size), Image.LANCZOS)
    if enhance:
        im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=2))
        im = ImageEnhance.Contrast(im).enhance(1.12)
        im = ImageEnhance.Color(im).enhance(1.06)
    return im


def _w2p(size):
    s = size / 1024.0
    def fn(x, y):
        return (mann.CALIB_OFFX + mann.CALIB_K * x) * s, (mann.CALIB_REF_Y - mann.CALIB_K * y) * s
    return fn


def annotate(im, draw_camps=True, draw_towers=True, draw_runes=True, draw_misc=True, labels=False):
    size = im.size[0]
    w2p = _w2p(size)
    s = size / 1024.0
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("arial.ttf", max(10, int(11 * s)))
    except Exception:
        font = ImageFont.load_default()

    # neutrals: yellow triangle (green = ancient), sized by tier
    if draw_camps:
        for (x, y, nt, pt, nm) in mann.NEUTRAL_CAMPS:
            px, py = w2p(x, y)
            r = (6.5 + nt * 1.6) * s
            col = ANCIENT if nt >= 3 else ("#b9a03a" if nt >= 2 else "#c9b050" if nt >= 1 else "#d8c766")
            dr.polygon([(px, py - r), (px + r * 0.95, py + r * 0.7), (px - r * 0.95, py + r * 0.7)],
                       fill=col, outline="#0b0d12")
            if labels:
                dr.text((px + 8 * s, py - 8 * s), nm.replace("neutralcamp_", ""), fill="#fff", font=font)

    # towers / buildings: square (dire=red, radiant=green), destroyed=grey
    if draw_towers:
        for (side, lane, tier, x, y) in mann.TOWERS:
            px, py = w2p(x, y)
            col = DIRE if side == "dire" else RADIANT
            r = 5.2 * s
            dr.rectangle([px - r, py - r, px + r, py + r], fill=col, outline="#0b0d12")
            # tier pips
            for i in range(min(tier, 4)):
                d = 1.1 * s
                dr.rectangle([px - r + (i * 2.6 + 1.0) * s, py + r - 2.4 * s,
                              px - r + (i * 2.6 + 1.0) * s + d, py + r - 2.4 * s + d],
                             fill="#0b0d12")

    # runes: bounty(gold swirl), powerup(blue), xp(teal)
    if draw_runes:
        for (rt, x, y, nm) in mann.RUNE_SPAWNS:
            px, py = w2p(x, y)
            r = 5.0 * s
            col = BOUNTY if rt == "bounty" else (XPRUNE if rt == "xp" else POWERUP)
            dr.ellipse([px - r, py - r, px + r, py + r], fill=col, outline="#0b0d12")
            dr.ellipse([px - r * 0.45, py - r * 0.45, px + r * 0.45, py + r * 0.45],
                       fill="#fff", outline="#0b0d12")

    if draw_misc:
        # Roshan: red skull blob + eyes
        rx, ry = mann.ROSHAN
        px, py = w2p(rx, ry); r = 6.5 * s
        dr.ellipse([px - r, py - r, px + r, py + r], fill="#c22", outline="#0b0d12")
        dr.ellipse([px - r * 0.5, py - r * 0.35, px - r * 0.1, py + r * 0.05], fill="#fff")
        dr.ellipse([px + r * 0.1, py - r * 0.35, px + r * 0.5, py + r * 0.05], fill="#fff")
        dr.rectangle([px - r * 0.45, py + r * 0.25, px + r * 0.45, py + r * 0.75], fill="#fff")
        for (x, y, side) in mann.TWIN_GATES:
            px, py = w2p(x, y); r = 7 * s
            dr.rectangle([px - r, py - r * 0.7, px + r, py + r * 0.7], outline="#8b5cf6", width=max(2, int(2 * s)))
        for (x, y, side) in mann.FORTS:
            px, py = w2p(x, y); r = 9 * s
            dr.rectangle([px - r, py - r, px + r, py + r], outline="#ff8c00", width=max(2, int(2.5 * s)))
        for (x, y, side) in mann.FOUNTAINS:
            px, py = w2p(x, y); r = 11 * s
            dr.ellipse([px - r, py - r, px + r, py + r], outline="#00ffff", width=max(2, int(2.5 * s)))
    return im


def render(size, out, src=None, labels=False):
    im = build_base(size, src=src)
    im = annotate(im, labels=labels)
    im.save(out)
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Map factory: enhanced base + official-style annotations")
    ap.add_argument("--size", type=int, default=4096)
    ap.add_argument("--out", default=os.path.join(HERE, "..", "dist", "map_base_4096.png"))
    ap.add_argument("--src", default=None)
    ap.add_argument("--labels", action="store_true")
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    render(args.size, out, src=args.src, labels=args.labels)
    print("map_factory wrote", out, args.size, "x", args.size)


if __name__ == "__main__":
    main()
