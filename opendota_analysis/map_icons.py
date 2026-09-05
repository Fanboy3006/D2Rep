# -*- coding: utf-8 -*-
"""map_icons.py - vector (SVG) miniature-map icons, drawn to look like the
in-game minimap symbols (which are themselves programmatic vector glyphs, not
bitmap files). Everything is generated standalone so it scales at any zoom and
can be recoloured per team.  Used by the DOM (lite) viewer.

Each icon is produced as a self-contained SVG string; helpers also return a
data-URI blob so an <img src="data:image/svg+xml;base64,..."> works everywhere
(including mobile webviews).  The icons are intentionally simple and
high-contrast so they read at a glance on the busy map.
"""

# ---------------------------------------------------------------- palette
RADIANT = "#46d160"
DIRE = "#ff5f57"
ANCIENT = "#e8b64c"
DARK = "#0b0d12"


def _svg(w, h, body, extra=""):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d">%s</svg>' % (w, h, w, h, body))


def svg_tower(team, dead=False, tier=1):
    """A small tower/citadel glyph. team: 'radiant'|'dire'. dead->collapsed ruin."""
    c = RADIANT if team == "radiant" else DIRE
    if dead:
        body = ('<path d="M6 18 L16 18 L15 21 L7 21 Z" fill="#4c525e"/>'
                '<line x1="4" y1="18" x2="11" y2="14" stroke="#4c525e" stroke-width="2"/>')
        return _svg(22, 24, body)
    # tower: triangle spire + base, team-colored with dark outline
    body = (
        '<polygon points="11,3 15,14 7,14" fill="%s" stroke="%s" stroke-width="1.2"/>'
        '<rect x="5" y="14" width="12" height="5" rx="1" fill="%s" stroke="%s" stroke-width="1"/>'
        '<rect x="8" y="19" width="6" height="2" fill="%s"/>'
    ) % (c, DARK, c, DARK, DARK)
    return _svg(22, 24, body)


def svg_camp(ctype):
    """Neutral camp glyph by tier. ctype 0..3 (3 = ancient)."""
    if ctype >= 3:  # ancient: gold crown/gem
        body = ('<polygon points="5,15 7,7 11,11 15,7 17,15" fill="%s" stroke="%s" stroke-width="1"/>'
                '<rect x="6" y="15" width="10" height="4" rx="1" fill="%s"/>') % (ANCIENT, DARK, ANCIENT)
        return _svg(22, 22, body)
    col = ["#66778f", "#8394ac", "#aab6c8"][min(ctype, 2)]
    # single monster silhouette (varies slightly by tier) in a small camp marker
    if ctype == 2:
        body = ('<circle cx="11" cy="11" r="7" fill="%s" stroke="%s" stroke-width="1"/>'
                '<polygon points="6,15 9,9 13,9 16,15" fill="#2a3341"/>'
                '<circle cx="9" cy="8" r="1.4" fill="#12161d"/>'
                '<circle cx="13" cy="8" r="1.4" fill="#12161d"/>') % (col, DARK)
    else:
        body = ('<polygon points="7,16 9,6 13,6 15,16" fill="%s" stroke="%s" stroke-width="1"/>'
                '<circle cx="11" cy="17" r="2" fill="%s"/>') % (col, DARK, col)
    return _svg(22, 22, body)


def svg_ward(sentry=False):
    """Ward observation glyph. sentry= true -> diamond, false -> circle (observer)."""
    if sentry:
        body = ('<rect x="5" y="5" width="10" height="10" rx="1" fill="#2f6fd8" '
                'stroke="#0b0d12" stroke-width="1.2" transform="rotate(45 10 10)"/>')
    else:
        body = ('<circle cx="10" cy="10" r="6" fill="#4da3ff" stroke="#0b0d12" stroke-width="1.2"/>'
                '<circle cx="10" cy="10" r="2" fill="#cfe9ff"/>')
    return _svg(20, 20, body)


def svg_roshan():
    """Roshan: red boss skull in a ring."""
    body = ('<circle cx="11" cy="11" r="8" fill="#3a1212" stroke="#e0655f" stroke-width="1.5"/>'
            '<path d="M7 12 Q9 7 11 9 Q13 7 15 12 Q14 15 11 15 Q8 15 7 12 Z" fill="#e0655f"/>'
            '<circle cx="9" cy="11" r="1.3" fill="#3a1212"/>'
            '<circle cx="13" cy="11" r="1.3" fill="#3a1212"/>')
    return _svg(22, 22, body)


def svg_ancient(team):
    """Ancient/Fort glyph (the team's throne)."""
    c = RADIANT if team == "radiant" else DIRE
    body = (
        '<polygon points="11,2 15,7 15,14 11,18 7,14 7,7" fill="%s" stroke="%s" stroke-width="1.2"/>'
        '<polygon points="11,5 13.5,8 13.5,12 11,14.5 8.5,12 8.5,8" fill="#fff" opacity=".55"/>'
    ) % (c, DARK)
    return _svg(22, 22, body)


def svg_fountain(team):
    c = RADIANT if team == "radiant" else DIRE
    body = ('<path d="M11 4 C 14 7, 16 9, 14 13 C 12 16, 8 16, 7 13 C 5 9, 8 7, 11 4 Z" '
            'fill="#9ff0ff" stroke="%s" stroke-width="1"/>') % c
    return _svg(20, 20, body)


def svg_gate():
    """Twin gate / portal."""
    body = ('<rect x="3" y="6" width="16" height="10" rx="4" fill="#8b5cf6" stroke="#0b0d12" stroke-width="1.2"/>'
            '<path d="M7 9 L11 13 L15 9" fill="none" stroke="#e6d9ff" stroke-width="1.7"/>')
    return _svg(22, 20, body)


# ---------------------------------------------------------------- data-URI
import base64


def to_data_uri(svg_str):
    b = svg_str.encode("utf-8")
    return "data:image/svg+xml;base64," + base64.b64encode(b).decode("ascii")


ICONS = {
    "tower_radiant": to_data_uri(svg_tower("radiant")),
    "tower_dire": to_data_uri(svg_tower("dire")),
    "tower_radiant_dead": to_data_uri(svg_tower("radiant", dead=True)),
    "tower_dire_dead": to_data_uri(svg_tower("dire", dead=True)),
    "camp0": to_data_uri(svg_camp(0)),
    "camp1": to_data_uri(svg_camp(1)),
    "camp2": to_data_uri(svg_camp(2)),
    "camp3": to_data_uri(svg_camp(3)),
    "ward_obs": to_data_uri(svg_ward(False)),
    "ward_sentry": to_data_uri(svg_ward(True)),
    "roshan": to_data_uri(svg_roshan()),
    "ancient_radiant": to_data_uri(svg_ancient("radiant")),
    "ancient_dire": to_data_uri(svg_ancient("dire")),
    "fountain_radiant": to_data_uri(svg_fountain("radiant")),
    "fountain_dire": to_data_uri(svg_fountain("dire")),
    "gate": to_data_uri(svg_gate()),
}


if __name__ == "__main__":
    # quick self-test: build a legend contact sheet
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    parts = []
    order = [("tower_radiant", "T(R)"), ("tower_dire", "T(D)"), ("camp0", "C0"),
             ("camp1", "C1"), ("camp2", "C2"), ("camp3", "anci"), ("ward_obs", "W"),
             ("ward_sentry", "W§"), ("roshan", "Ros"), ("ancient_radiant", "A(R)"),
             ("fountain_radiant", "F(R)"), ("gate", "Gate")]
    for key, _lab in order:
        parts.append('<div style="display:inline-block;text-align:center;margin:10px">'
                     '<img style="width:34px;height:34px" src="%s">' % ICONS[key])
    html = '<html><body style="background:#1a1d24;color:#fff">' + "".join(parts) + "</body></html>"
    open(r"F:\D2Rep_project\dota_replay_analyzer\.tmp\icon_sheet.html", "w", encoding="utf-8").write(html)
    print("wrote icon_sheet.html with %d icons" % len(ICONS))
