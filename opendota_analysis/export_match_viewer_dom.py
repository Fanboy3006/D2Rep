#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_match_viewer_dom.py - Canvas-free, universally-openable match viewer.

Same data / same fold as export_match_viewer.py but rendered with plain
HTML/CSS (no Canvas2D): the map is an <img>, heroes/towers/camps/Roshan are
absolutely positioned DOM elements, the timeline is a native <input type=range>
and the interaction is plain JS. Because the initial board (positions at the
data start) is baked into the markup, the page still shows the full map +
heroes even where JavaScript is disabled (e.g. iOS Quick Look), and is fully
interactive in any real browser / WeChat's Android webview.

Usage:
    python opendota_analysis/export_match_viewer_dom.py <match_id|db>
        [--out dist/viewer_<id>_lite.html] [--map assets/dota_map_902.png]
"""
import base64
import collections
import glob
import io
import json
import os
import sqlite3
import sys
import urllib.request
import ssl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from opendota_analysis import map_annotations as mann  # noqa: E402

ICON_CACHE = os.path.join(HERE, "assets", "hero_icons")
ICON_URLS = [
    "https://cdn.dota2.com/apps/dota2/images/heroes/%s_full.png",
    "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/%s.png",
]
_SSL = ssl._create_unverified_context()
import threading
_LOCK = threading.Lock()

ABICON_CACHE = os.path.join(HERE, "assets", "ability_icons")
ABICON_URL = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/abilities/%s_lg.png"
import re as _re


def ability_key(cls):
    """CDOTA_Ability_Invoker_SunStrike -> invoker_sun_strike (used by CDN)."""
    s = cls[len("CDOTA_Ability_"):] if cls.startswith("CDOTA_Ability_") else cls
    return _re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s).lower()


def ability_icon_b64(cls):
    """Official ability icon from the Steam CDN, cached under assets/ability_icons."""
    key = ability_key(cls)
    cached = os.path.join(ABICON_CACHE, key + ".png")
    if os.path.exists(cached):
        with open(cached, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    with _LOCK:
        if os.path.exists(cached):
            with open(cached, "rb") as f:
                return base64.b64encode(f.read()).decode("ascii")
        try:
            req = urllib.request.Request(ABICON_URL % key, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
                raw = r.read()
            from PIL import Image
            im = Image.open(io.BytesIO(raw)).convert("RGBA").resize((48, 48), Image.LANCZOS)
            buf = io.BytesIO(); im.save(buf, "PNG", optimize=True)
            data = buf.getvalue()
        except Exception:
            return None
        os.makedirs(ABICON_CACHE, exist_ok=True)
        tmp = cached + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, cached)
        return base64.b64encode(data).decode("ascii")


ITEM_ICON_KEY = {"Black_King_Bar": "black_king_bar", "RefresherOrb": "refresher_orb"}


def item_icon_b64(short):
    """Active-item icon (Black_King_Bar/RefresherOrb) from Steam CDN items/."""
    key = ITEM_ICON_KEY.get(short, short.lower())
    cached = os.path.join(ABICON_CACHE, "item_" + key + ".png")
    if os.path.exists(cached):
        with open(cached, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    with _LOCK:
        if os.path.exists(cached):
            with open(cached, "rb") as f:
                return base64.b64encode(f.read()).decode("ascii")
        try:
            url = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/items/%s_lg.png" % key
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
                raw = r.read()
            from PIL import Image
            im = Image.open(io.BytesIO(raw)).convert("RGBA").resize((48, 48), Image.LANCZOS)
            buf = io.BytesIO(); im.save(buf, "PNG", optimize=True)
            data = buf.getvalue()
        except Exception:
            return None
        os.makedirs(ABICON_CACHE, exist_ok=True)
        tmp = cached + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, cached)
        return base64.b64encode(data).decode("ascii")


ITEM_LABEL = {"Black_King_Bar": "BKB", "RefresherOrb": "刷新球"}


def hero_icon_b64(npc):
    stem = npc[len("npc_dota_hero_"):] if npc.startswith("npc_dota_hero_") else npc
    cached = os.path.join(ICON_CACHE, stem + ".png")
    if os.path.exists(cached):
        with open(cached, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    with _LOCK:
        if os.path.exists(cached):
            with open(cached, "rb") as f:
                return base64.b64encode(f.read()).decode("ascii")
        raw = None
        for tpl in ICON_URLS:
            try:
                req = urllib.request.Request(tpl % stem, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
                    raw = r.read()
                break
            except Exception as e:
                print("  [icon] %s unavailable (%s)" % (stem, e), file=sys.stderr)
        if raw is None:
            return None
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(raw)).resize((128, 72), Image.LANCZOS)
            buf = io.BytesIO(); im.save(buf, "PNG", optimize=True)
            data = buf.getvalue()
        except Exception:
            return None
        os.makedirs(ICON_CACHE, exist_ok=True)
        tmp = cached + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, cached)
        return base64.b64encode(data).decode("ascii")


def find_db(match_id):
    p = os.path.join(HERE, "..", "dems", "db", "*", "%s.db" % match_id)
    hits = glob.glob(p)
    return hits[0] if hits else None


STEAM_BASE = 76561197960265728  # steam64 -> account_id (opendota / matches)


def load_official(match_id):
    """opendota match players[].name -> official player id, keyed by account_id.
    Returns {} if the cached opendota match json is unavailable."""
    p = os.path.join(HERE, "..", ".tmp", "op_match_%s.json" % match_id)
    if not os.path.exists(p):
        return {}
    try:
        m = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    d = {}
    for pl in m.get("players", []):
        acc = pl.get("account_id")
        if acc is not None:
            d[acc] = pl.get("name") or ""
    return d


def load_payload(target, step=1):
    """Return (match_id, payload, icons) with the same fold as the canvas viewer."""
    if target.endswith(".db") and os.path.exists(target):
        db = target
        match_id = os.path.basename(db)[:-3]
    else:
        db = find_db(target)
        if not db:
            raise FileNotFoundError("db not found for match %s" % target)
        match_id = target
    con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(db).replace("\\", "/"), uri=True)
    players = []
    for slot, steam, name, hero, team in con.execute(
            "SELECT player_slot, steam_id, player_name, hero_name, team_id "
            "FROM player_identity ORDER BY player_slot"):
        players.append({"slot": slot, "name": name or "", "hero": hero or "",
                        "team": team, "steam": steam})
    hero_npcs = {p["hero"] for p in players}
    series = {}
    for entity_id, t, x, y, hp, extra, team in con.execute(
            """SELECT entity_id, game_time_sec, x, y, hp, extra, team
               FROM entity_snapshots WHERE entity_type='hero'
               AND json_extract(extra,'$.player_slot') IS NOT NULL
               ORDER BY entity_id, game_time_sec"""):
        if entity_id not in hero_npcs:
            continue
        if step > 1 and t % step != 0:
            continue
        e = json.loads(extra)
        arr = series.setdefault(entity_id, [])
        arr.append([t, int(round(x)), int(round(y)), hp if hp is not None else 0,
                    e.get("hp_max") or 0, int(round(e.get("mana") or 0)),
                    int(round(e.get("mana_max") or 0))])
    max_event = con.execute("SELECT MAX(game_time_sec) FROM game_events").fetchone()[0]
    towers = {}
    for etype, sec, tid, x, y, props in con.execute(
            """SELECT event_type, game_time_sec, target_id, x, y, properties
               FROM game_events WHERE event_type IN ('building_spawn','building_destroyed')
               AND json_extract(properties,'$.kind')='tower'"""):
        p = json.loads(props)
        if p.get("team") not in (2, 3) or x is None:
            continue
        if etype == "building_spawn":
            towers.setdefault(tid, {"x": int(round(x)), "y": int(round(y)),
                                    "team": p["team"], "d": None})
        elif tid in towers:
            towers[tid]["d"] = int(sec)
    sk = {}  # hero -> {"ab": {ability:{learned,cds}}, "items": {short:{known,cds}}}
    ab_state = {}
    item_state = {}
    for etype, sec, hero, tid in con.execute(
            """SELECT event_type, game_time_sec, actor_id, target_id
               FROM game_events
               WHERE event_type IN ('ability_known','ability_learn',
                                    'ability_cd_start','ability_cd_end',
                                    'item_known','item_cd_start','item_cd_end')"""):
        if not hero or not tid:
            continue
        if etype.startswith("ability_"):
            st = ab_state.setdefault((hero, tid), {"learned": -1, "pend": None, "cds": []})
            if etype == "ability_known":
                st["known"] = sec
            elif etype == "ability_learn":
                st["learned"] = sec
            elif etype == "ability_cd_start":
                st["pend"] = sec
            elif etype == "ability_cd_end" and st["pend"] is not None:
                st["cds"].append([st["pend"], sec]); st["pend"] = None
        else:
            short = tid.split(":", 1)[1] if ":" in tid else tid
            st = item_state.setdefault((hero, short), {"known": -1, "pend": None, "cds": []})
            if etype == "item_known":
                st["known"] = sec
            elif etype == "item_cd_start":
                st["pend"] = sec
            elif etype == "item_cd_end" and st["pend"] is not None:
                st["cds"].append([st["pend"], sec]); st["pend"] = None
    for (hero, ab), st in ab_state.items():
        sk.setdefault(hero, {"ab": {}, "items": {}})["ab"][ab] = {
            "learned": st["learned"], "cds": st["cds"]}
    for (hero, short), st in item_state.items():
        sk.setdefault(hero, {"ab": {}, "items": {}})["items"][short] = {
            "known": st["known"], "cds": st["cds"]}
    # ---- active wards (observer/sentry) ----
    placed_by_k = {}
    for t, team, x, y, kind in con.execute(
            """SELECT game_time_sec, json_extract(properties,'$.team'), x, y,
                      json_extract(properties,'$.ward_type')
               FROM game_events WHERE event_type='ward_placed'"""):
        if team is None or x is None:
            continue
        placed_by_k.setdefault((int(team), kind), []).append((int(t), float(x), float(y)))
    destroyed_by_k = {}
    for t, team, kind in con.execute(
            """SELECT game_time_sec, json_extract(properties,'$.team'),
                      json_extract(properties,'$.ward_type')
               FROM game_events WHERE event_type='ward_destroyed'"""):
        if team is None:
            continue
        destroyed_by_k.setdefault((int(team), kind), []).append(int(t))
    wards = []
    for (team, kind), plist in placed_by_k.items():
        dlist = sorted(destroyed_by_k.get((team, kind), []))
        di = 0
        for (pt, x, y) in sorted(plist):
            dstr = None
            while di < len(dlist) and dlist[di] < pt:
                di += 1
            if di < len(dlist):
                dstr = dlist[di]; di += 1
            wards.append([int(round(x)), int(round(y)), int(team), kind, int(pt),
                          int(dstr) if dstr is not None else 0])
    con.close()
    towers = list(towers.values())

    raw_end = max((a[-1][0] for a in series.values() if a), default=0)
    move_end = 0
    for arr in series.values():
        for i in range(1, len(arr)):
            dt = arr[i][0] - arr[i - 1][0]
            if dt <= 0:
                continue
            dx, dy = arr[i][1] - arr[i - 1][1], arr[i][2] - arr[i - 1][2]
            if (dx * dx + dy * dy) ** .5 / dt >= 100:
                move_end = arr[i][0]
    active_end = raw_end
    last_action = max(move_end, int(max_event) if max_event else 0)
    if raw_end - last_action > 120:
        active_end = min(raw_end, last_action + 90)
    if active_end < raw_end:
        series = {eid: [r for r in a if r[0] <= active_end] for eid, a in series.items()}
        series = {eid: a for eid, a in series.items() if a}

    icons = {}
    for p in players:
        if p["hero"] and p["hero"] in series:
            b = hero_icon_b64(p["hero"])
            if b:
                icons[p["hero"]] = b
    payload = {"match_id": int(match_id), "players": players, "series": series,
               "meta": {"raw_end": raw_end, "active_end": active_end},
               "towers": towers, "camps": [[c[0], c[1], c[2]] for c in mann.NEUTRAL_CAMPS],
               "roshan": list(mann.ROSHAN), "sk": sk, "wards": wards}
    return match_id, payload, icons


WORLD = 19134.0


def pct(x, y):
    """world -> percentage inside the square board."""
    half = WORLD / 2
    return (x + half) / WORLD * 100, (half - y) / WORLD * 100


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target")
    ap.add_argument("--out", default=None)
    ap.add_argument("--map", default=os.path.join(HERE, "assets", "dota_map_1024.png"))
    ap.add_argument("--step", type=int, default=1)
    args = ap.parse_args()

    match_id, payload, icons = load_payload(args.target, args.step)
    official = load_official(match_id)

    def player_disp(p):
        acc = (p.get("steam") or 0) - STEAM_BASE
        nm = official.get(acc)
        return (nm or p["name"] or p["hero"]), acc

    def player_link(href, text):
        return '<a href="%s" target="_blank" rel="noopener">%s</a>' % (href, text)
    if args.out is None:
        args.out = os.path.join(HERE, "..", "dist", "viewer_%s_lite.html" % match_id)

    # static positions baked at data start (shows even without JS)
    t0 = min((a[0][0] for a in payload["series"].values() if a), default=0)
    hero_markup = []
    for p in payload["players"]:
        arr = payload["series"].get(p["hero"])
        if not arr:
            continue
        x, y = arr[0][1], arr[0][2]
        left, top = pct(x, y)
        ico = ('<img class="ico" src="data:image/png;base64,%s" alt="">' % icons[p["hero"]]
               if p["hero"] in icons else '')
        disp, acc = player_disp(p)
        href = 'https://www.opendota.com/players/%d' % acc if acc > 0 else None
        nm = ('<a href="%s" target="_blank" rel="noopener" title="%s">%s</a>' % (href, disp, disp)
              if href else '<span>%s</span>' % disp)
        a0 = arr[0]
        hf0 = (100.0 * a0[3] / a0[4]) if a0[4] else 0
        mf0 = (100.0 * a0[5] / a0[6]) if a0[6] else 0
        hero_markup.append(
            '<div class="hm" data-hero="%s" style="left:%.3f%%;top:%.3f%%">'
            '<span class="bars">'
            '<span class="hbar"><i style="width:%.1f%%"></i></span>'
            '<span class="mbar"><i style="width:%.1f%%"></i></span></span>%s'
            '<span class="hnm">%s</span></div>' % (
                p["hero"], left, top, hf0, mf0, ico, nm))
    tower_markup = []
    for tw in payload["towers"]:
        left, top = pct(tw["x"], tw["y"])
        tower_markup.append(
            '<div class="tw t%d%s" style="left:%.3f%%;top:%.3f%%"></div>' % (
                2 if tw["team"] == 2 else 3, " dead" if tw["d"] is not None and tw["d"] <= t0 else "",
                left, top))
    camp_markup = []
    for c in payload["camps"]:
        left, top = pct(c[0], c[1])
        camp_markup.append('<div class="camp ct%d" style="left:%.3f%%;top:%.3f%%"></div>'
                           % (c[2], left, top))
    if payload.get("roshan"):
        left, top = pct(payload["roshan"][0], payload["roshan"][1])
        camp_markup.append('<div class="camp rosh" style="left:%.3f%%;top:%.3f%%"></div>'
                           % (left, top))

    # ---- skill-cooldown chips per hero ----
    import re
    def camel(npc):
        s = npc[len("npc_dota_hero_"):] if npc.startswith("npc_dota_hero_") else npc
        return "".join(w.capitalize() for w in s.split("_"))
    hero_tokens = {camel(p["hero"]) for p in payload["players"] if p["hero"]}
    def ab_label(cls):
        lb = cls[len("CDOTA_Ability_"):] if cls.startswith("CDOTA_Ability_") else cls
        for t in hero_tokens:
            if lb.startswith(t + "_"):
                lb = lb[len(t) + 1:]
                break
        lb = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", lb)
        return " ".join(lb.replace("_", " ").split())
    def chip(hero, kind, key, icon, label, when):
        img = ('<img class="cico" src="data:image/png;base64,%s" alt="">' % icon) if icon else ''
        return ('<span class="chip" data-h="%s" data-kind="%s" data-a="%s" data-when="%s" '
                'data-label="%s" title="%s">%s<span class="ctxt">%s</span>'
                '<span class="cnum"></span></span>'
                % (hero, kind, key, when, label, label, img, label))
    bands = {2: [], 3: []}
    for p in payload["players"]:
        hero = p["hero"]
        skh = (payload.get("sk") or {}).get(hero)
        if not skh:
            continue
        h_chips = []
        for ab in sorted(skh["ab"]):
            st = skh["ab"][ab]
            h_chips.append(chip(hero, "ab", ab, ability_icon_b64(ab), ab_label(ab), st["learned"]))
        for short in sorted(skh["items"]):
            st = skh["items"][short]
            lb = ITEM_LABEL.get(short, short)
            h_chips.append(chip(hero, "item", short, item_icon_b64(short), lb, st["known"]))
        if not h_chips:
            continue
        disp, acc = player_disp(p)
        hid = ('<img class="hico" src="data:image/png;base64,%s" alt="">' % icons[hero]
               if hero in icons else '')
        name_html = (player_link('https://www.opendota.com/players/%d' % acc, disp) if acc > 0
                     else '<span>%s</span>' % disp)
        a0 = (payload["series"].get(hero) or [None])[0]
        hp0 = (a0[3] if a0 else 0); hpm0 = (a0[4] if a0 else 0); mp0 = (a0[5] if a0 else 0)
        hstats_init = '%d/%d<span class="mp">%d</span>' % (hp0, hpm0, mp0)
        block = ('<div class="hblock" data-hero="%s"><div class="hhead">%s<span class="skname">%s</span>'
                 '<span class="hstats">%s</span></div>'
                 '<div class="chips">%s</div></div>'
                 % (p["hero"], hid, name_html, hstats_init, "".join(h_chips)))
        bands[p["team"] if p["team"] in (2, 3) else 2].append(block)
    band_l = ('<div class="band left"><div class="bh">天辉</div>' + "".join(bands[2]) + '</div>')
    band_r = ('<div class="band right"><div class="bh">夜魇</div>' + "".join(bands[3]) + '</div>')

    # map image (downscale to 768 for memory friendliness everywhere)
    from PIL import Image
    im = Image.open(args.map).convert("RGB").resize((768, 768), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "PNG", optimize=True)
    map_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    data_json = json.dumps(payload, separators=(",", ":"))
    html = (TEMPLATE
            .replace("__DATA_JSON__", data_json)
            .replace("__MAP_B64__", map_b64)
            .replace("__HEROES__", "\n".join(hero_markup))
            .replace("__TOWERS__", "\n".join(tower_markup))
            .replace("__CAMPS__", "\n".join(camp_markup))
            .replace("__MATCH__", match_id)
            .replace("__T0__", str(int(t0)))
            .replace("__BANDL__", band_l)
            .replace("__BANDR__", band_r))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote %s (%.1f MB, %d heroes, %d samples)" %
          (args.out, os.path.getsize(args.out) / 1e6, len(payload["series"]),
           sum(len(v) for v in payload["series"].values())))


TEMPLATE = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dota2 复盘(通用版) · __MATCH__</title>
<style>
 body{margin:0;background:#101216;color:#dfe3ea;font-family:system-ui,sans-serif;
      padding-bottom:52px}
 .top{display:flex;align-items:center;gap:10px;padding:8px 12px;background:#171a21;
      border-bottom:1px solid #262b36;flex-wrap:wrap}
 .top h1{font-size:15px;margin:0;font-weight:600}
 .top .ver{font-size:11px;color:#8fa0b8}
 .tglbar{display:flex;gap:14px;align-items:center;justify-content:center;padding:4px 8px;
         font-size:12px;color:#cfe3ff;flex-wrap:wrap}
 .tglbar label{display:inline-flex;align-items:center;gap:4px;cursor:pointer}
 .tglbar input{accent-color:#4da3ff}
 .ward{position:absolute;width:9px;height:9px;border-radius:50%;transform:translate(-50%,-50%);
       border:1px solid #0b0d12;z-index:2}
 .ward.sentry{border-radius:1px;transform:translate(-50%,-50%) rotate(45deg)}
 .stage{display:flex;flex-direction:row;align-items:flex-start;gap:6px;padding:4px}
 .band{width:min(30vw,300px);min-width:210px;max-height:calc(100vh - 150px);
       overflow:auto;background:#12141a;border:1px solid #23262e;border-radius:10px;
       padding:6px;box-sizing:border-box}
 .band .bh{font-size:12px;color:#9fb0c8;margin:2px 4px 4px}
 .band.left .bh{color:#7bd77b}
 .band.right .bh{color:#ff8b8b}
 .hblock{margin:4px 0;padding:4px 4px 6px;border-bottom:1px solid #20242c}
 .hblock:last-child{border-bottom:none}
 .hhead{display:flex;align-items:center;gap:6px;font-size:12px;color:#cfe3ff;margin-bottom:3px}
 .hhead .hico{width:24px;height:24px;border-radius:5px}
 .hhead .skname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .hhead .skname a{color:#cfe3ff;text-decoration:none}
 .hhead .skname a:hover{text-decoration:underline}
 .hstats{margin-left:auto;font-size:11px;font-weight:600;color:#8fe89a;font-variant-numeric:tabular-nums;
         flex:none;white-space:nowrap}
 .hstats .mp{color:#6fc4f5;margin-left:7px}
 .chips{display:flex;gap:5px;flex-wrap:wrap}
 .chip{display:inline-flex;align-items:center;gap:4px;padding:2px 5px;
       border-radius:6px;background:#1f2a3a;color:#cfe3ff;font-size:11px;
       border:1px solid #2c3c55;white-space:nowrap}
 .chip .cico{width:22px;height:22px;border-radius:4px;flex:none}
 .chip .ctxt{font-size:11px}
 .chip .cnum{font-size:11px;font-weight:700}
 .chip.locked{background:#1a1d23;color:#5f6772;border-color:#262b33}
 .chip.locked .cico{filter:grayscale(1);opacity:.35}
 .chip.cd{background:#262a31;color:#7d8694;border-color:#3a3f4a}
 .chip.cd .cico{filter:grayscale(1);opacity:.45}
 .chip.cd .ctxt{display:none}
 .board{position:relative;flex:1 1 auto;min-width:0;aspect-ratio:1/1;
        max-width:min(96vw,calc(100vh - 130px));max-height:calc(100vh - 130px);
        margin:4px auto;background:#0a0c10;overflow:hidden}
 .board img{position:absolute;inset:0;width:100%;height:100%;object-fit:fill;display:block}
 .mk{position:absolute;transform:translate(-50%,-50%)}
 .camp{width:10px;height:10px;position:absolute;transform:translate(-50%,-50%);opacity:.95}
 .ct3{background:#e8b64c;transform:translate(-50%,-50%) rotate(45deg)}
 .ct2{background:#aab6c8;clip-path:polygon(50% 0,100% 100%,0 100%)}
 .ct1{background:#8394ac;clip-path:polygon(50% 0,100% 100%,0 100%);width:8px;height:8px}
 .ct0{background:#66778f;clip-path:polygon(50% 0,100% 100%,0 100%);width:7px;height:7px}
 .rosh{border:2px solid #e0655f;border-radius:50%;width:14px;height:14px;background:none}
 .tw{width:12px;height:12px;border-radius:50%;position:absolute;transform:translate(-50%,-50%);
     border:2px solid #0b0d12}
 .t2{background:#46d160}.t3{background:#ff5f57}
 .tw.dead{background:#4c525e}
 .tw.dead::after{content:"✕";color:#111;font-size:9px;position:absolute;
     left:50%;top:50%;transform:translate(-50%,-50%)}
 .hm{position:absolute;transform:translate(-50%,-50%);text-align:center;width:52px;z-index:4}
 .hm .ico{width:46px;height:26px;display:block;border:2px solid #555;border-radius:4px;
          position:relative;z-index:1}
 .hm .bars{position:absolute;left:50%;top:-6px;transform:translate(-50%,-100%);z-index:3;
           width:56px}
 .hm .hbar,.hm .mbar{display:block;height:7px;background:#0b0e14;margin:1px auto;width:56px;
     border-radius:4px;overflow:hidden;border:1px solid #000}
 .hm .hbar i{display:block;height:100%;background:linear-gradient(90deg,#27ae60,#5fe08a)}
 .hm .mbar i{display:block;height:100%;background:linear-gradient(90deg,#2980b9,#5bc0ff)}
 .hm .hnm{display:block;font-size:10px;color:#fff;text-shadow:0 0 2px #000;white-space:nowrap;
          overflow:hidden;text-overflow:ellipsis;width:64px;margin:1px auto 0;position:relative;
          z-index:2}
 .panel{width:260px;flex:none;padding:10px;border-left:1px solid #262b36}
 .panel.tgl{display:flex;gap:12px;align-items:center;font-size:12px;margin-bottom:8px}
 .panel input[type=checkbox]{accent-color:#4da3ff}
 .ctrl{position:fixed;left:0;right:0;bottom:0;z-index:20;display:flex;align-items:center;
       gap:10px;padding:6px 12px;padding-bottom:calc(6px + env(safe-area-inset-bottom,0px));
       background:#171a21;border-top:1px solid #262b36}
 .ctrl #time{min-width:92px;text-align:center;font-variant-numeric:tabular-nums;font-size:13px}
 .ctrl input[type=range]{flex:1;accent-color:#4da3ff}
 .ctrl button{width:36px;height:28px;background:#1b1f28;color:#dfe3ea;border:1px solid #2c3240;
              border-radius:6px;font-size:14px}
 .note{font-size:12px;color:#9aa2b2;padding:2px 12px 6px}
 .skills{margin:8px auto;max-width:96vw;padding:8px 12px;background:#15171d;
         border:1px solid #262b36;border-radius:10px}
 .skills .skh{font-size:12px;color:#9fb0c8;margin-bottom:6px}
 .skrow{display:flex;align-items:center;gap:8px;margin:4px 0;flex-wrap:wrap}
 .skhero{width:132px;display:flex;align-items:center;gap:6px;font-size:12px;color:#aeb9cc;
         flex:none;overflow:hidden}
 .skhero .hico{width:26px;height:26px;border-radius:5px;flex:none}
 .skhero .skname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .skhero .skname a{color:#cfe3ff;text-decoration:none}
 .skhero .skname a:hover{text-decoration:underline}
 .skchips{display:flex;gap:6px;flex-wrap:wrap}
 .chip{display:inline-flex;align-items:center;gap:5px;min-width:40px;padding:3px 6px;
       border-radius:6px;background:#1f2a3a;color:#cfe3ff;font-size:11px;
       border:1px solid #2c3c55;white-space:nowrap}
 .chip .cico{width:24px;height:24px;border-radius:4px;flex:none}
 .chip .ctxt{font-size:11px}
 .chip .cnum{font-size:12px;font-weight:700;letter-spacing:0}
 .chip.cd{background:#262a31;color:#7d8694;border-color:#3a3f4a}
 .chip.cd .cico{filter:grayscale(1);opacity:.45}
 .chip.cd .ctxt{display:none}
 @media (max-width:760px){
   .stage{flex-direction:column}
   .band{width:100%;max-height:none;order:2}
   .band.right{order:3}
   .board{order:1;max-width:96vw}
 }
</style>
</head>
<body>
<div class="top"><h1>Dota2 复盘 · 比赛 __MATCH__
  <span class="ver">兼容版 · 无需浏览器高级特性</span></h1>
</div>
<div class="note">点击"播放"或拖动时间条查看走位；若页面本身不能互动（如部分系统预览），仍会显示初始局面。</div>
<div class="tglbar">
  <label><input type="checkbox" id="tgHp" checked> 血蓝</label>
  <label><input type="checkbox" id="tgWard" checked> 眼位</label>
  <label><input type="checkbox" id="tgCamp" checked> 野点</label>
  <label><input type="checkbox" id="tgTower" checked> 塔</label>
</div>
<div class="stage">
  __BANDL__
  <div class="board"><img src="data:image/png;base64,__MAP_B64__" alt="map">
__CAMPS__<div class="wardlayer" id="wardlayer"></div>
__TOWERS____HEROES__
  </div>
  __BANDR__
</div>
<div class="note" style="text-align:center">▲野点　◆远古　◉肉山　<span style="color:#46d160">◉</span>塔=存活　<span style="color:#4c525e">✕</span>=摧毁 · 技能灰=未学/冷却中(数字为剩余秒)</div>
<div class="ctrl">
  <button id="play">▶</button>
  <span id="time">0:00</span>
  <input type="range" id="slider" min="__T0__" max="__T0__" step="1" value="__T0__">
</div>
<script>
"use strict";
var DATA = __DATA_JSON__;
var t0 = +document.getElementById('slider').min;
var tMax = t0;
var heroes = [];
(function () {
  var byHero = {};
  for (var i = 0; i < DATA.players.length; i++) {
    var p = DATA.players[i];
    var arr = DATA.series[p.hero];
    if (!arr || !arr.length) continue;
    byHero[p.hero] = arr;
    if (arr[arr.length - 1][0] > tMax) tMax = arr[arr.length - 1][0];
    var el = document.querySelector('.hm[data-hero="' + p.hero + '"]');
    if (el) heroes.push({hero: p.hero, el: el, arr: arr, team: p.team});
  }
  document.getElementById('slider').max = Math.max(tMax, t0 + 1);
  window.__data = DATA;
  var H = 2 * 0; // world half for % calc below
})();
var WORLD = 19134, half = WORLD / 2;
var cdmap = DATA.sk || {};   // hero -> { ab:{ability:{learned,cds}}, items:{short:{known,cds}} }
function pct(x, y) { return [(x + half) / WORLD * 100, (half - y) / WORLD * 100]; }
function stateAt(arr, t) {
  var lo = 0, hi = arr.length - 1;
  if (t <= arr[0][0]) return arr[0];
  if (t >= arr[hi][0]) return arr[hi];
  while (hi - lo > 1) { var m = (lo + hi) >> 1; if (arr[m][0] <= t) lo = m; else hi = m; }
  var a = arr[lo], b = arr[hi];
  if (b[0] === a[0]) return a;
  var f = (t - a[0]) / (b[0] - a[0]);
  return [t, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f,
          a[3] + (b[3] - a[3]) * f, a[4] + (b[4] - a[4]) * f,
          a[5] + (b[5] - a[5]) * f, a[6] + (b[6] - a[6]) * f];
}
function fmt(s) { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }
function draw() {
  var t = +document.getElementById('slider').value;
  document.getElementById('time').textContent = fmt(t - t0);
  var tgHp = document.getElementById('tgHp').checked;
  var tgWard = document.getElementById('tgWard').checked;
  var tgCamp = document.getElementById('tgCamp').checked;
  var tgTower = document.getElementById('tgTower').checked;
  var i, wd, xy;
  var wl = document.getElementById('wardlayer');
  wl.style.display = tgWard ? '' : 'none';
  if (tgWard) {
    var h = '';
    for (i = 0; i < (DATA.wards || []).length; i++) {
      wd = DATA.wards[i];
      if (t >= wd[4] && (wd[5] === 0 || t < wd[5])) {
        xy = pct(wd[0], wd[1]);
        var col = wd[2] === 2 ? '#46d160' : '#ff5f57';
        var cls = 'ward' + (wd[3] === 'sentry' ? ' sentry' : '');
        h += '<span class="' + cls + '" style="left:' + xy[0] + '%;top:' + xy[1] +
             '%;background:' + col + '"></span>';
      }
    }
    wl.innerHTML = h;
  }
  var cs = document.querySelectorAll('.camp');
  for (i = 0; i < cs.length; i++) cs[i].style.display = tgCamp ? '' : 'none';
  var tws = document.querySelectorAll('.tw');
  for (i = 0; i < tws.length; i++) tws[i].style.display = tgTower ? '' : 'none';
  var bars = document.querySelectorAll('.hm .bars');
  for (i = 0; i < bars.length; i++) bars[i].style.display = tgHp ? '' : 'none';
  var mks = document.querySelectorAll('.hm');
  for (var mi = 0; mi < mks.length; mi++) {
    var el = mks[mi], hero = el.getAttribute('data-hero');
    var arr = DATA.series[hero];
    if (!arr || !arr.length) continue;
    var st = stateAt(arr, t);
    var xy = pct(st[1], st[2]);
    el.style.left = xy[0] + '%'; el.style.top = xy[1] + '%';
    var hf = st[4] ? Math.min(1, st[3] / st[4]) : 0;
    var mf = st[6] ? Math.min(1, st[5] / st[6]) : 0;
    el.querySelector('.hbar i').style.width = (hf * 100) + '%';
    el.querySelector('.mbar i').style.width = (mf * 100) + '%';
  }
  for (var j = 0; j < DATA.towers.length; j++) {
    var tw = DATA.towers[j], els = document.querySelectorAll('.tw');
    if (els[j]) els[j].className = 'tw t' + (tw.team === 2 ? 2 : 3) +
      (tw.d != null && t >= tw.d ? ' dead' : '');
  }
  // per-hero side stats: current HP / mana (reads data directly, no DOM dep)
  var hblocks = document.querySelectorAll('.hblock');
  for (var bi = 0; bi < hblocks.length; bi++) {
    var hb = hblocks[bi], hero = hb.getAttribute('data-hero');
    var arr = DATA.series[hero];
    if (!arr || !arr.length) continue;
    var st = stateAt(arr, t);
    var s = hb.querySelector('.hstats');
    if (s) s.innerHTML = Math.round(st[3]) + '/' + st[4] +
      '<span class="mp">' + Math.round(st[5]) + '</span>';
  }
  // skill/item cooldown chips: locked (unlearned/unowned) / cooldown / ready
  var chips = document.querySelectorAll('.chip');
  for (var k = 0; k < chips.length; k++) {
    var c = chips[k], skh = cdmap[c.dataset.h] || {};
    var entry = c.dataset.kind === 'item' ? (skh.items || {})[c.dataset.a]
                                          : (skh.ab || {})[c.dataset.a];
    var num = c.querySelector('.cnum'), tx = c.querySelector('.ctxt');
    var when = parseInt(c.dataset.when || '-1', 10);
    var inter = null;
    if (entry) {
      var arr = entry.cds || [];
      for (var q = 0; q < arr.length; q++) {
        if (t >= arr[q][0] && t <= arr[q][1]) { inter = arr[q]; break; }
      }
    }
    if (inter) {                       // on cooldown: grey + remaining seconds
      c.classList.add('cd'); c.classList.remove('locked');
      if (num) num.textContent = Math.ceil(inter[1] - t);
      if (tx) tx.textContent = '';
    } else if (when < 0 || t < when) { // not yet learned / not yet owned
      c.classList.add('locked'); c.classList.remove('cd');
      if (num) num.textContent = '';
      if (tx) tx.textContent = c.dataset.label;
    } else {                           // ready
      c.classList.remove('cd', 'locked');
      if (num) num.textContent = '';
      if (tx) tx.textContent = c.dataset.label;
    }
  }
}
document.getElementById('slider').addEventListener('input', draw);
['tgHp', 'tgWard', 'tgCamp', 'tgTower'].forEach(function (id) {
  var el = document.getElementById(id);
  if (el) el.addEventListener('change', draw);
});
document.getElementById('play').addEventListener('click', function () {  var s = document.getElementById('slider');
  if (this._t) { clearInterval(this._t); this._t = null; this.textContent = '▶'; return; }
  this.textContent = '⏸'; var self = this;
  this._t = setInterval(function () {
    var v = +s.value + 1; if (v > s.max) v = s.min; s.value = v; draw();
  }, 100);
});
draw();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
