# -*- coding: utf-8 -*-
"""diag_scene.py - report what's actually in the current Blender scene after the
SourceIO dota.vpk import + load. Determines whether peripheral (full-map ~+-9000)
objects are present, or only the central block."""
import bpy
from mathutils import Vector

bpy.context.view_layer.update()
objs = list(bpy.context.scene.objects)
empties = [o for o in objs if o.type == 'EMPTY']
meshes = [o for o in objs if o.type == 'MESH']
print("objects:", len(objs), " empties:", len(empties), " MESH:", len(meshes))

with_pp = imported = peripheral_empty = 0
periph_e = []
for o in empties:
    ed = o.get('entity_data')
    ed = ed if isinstance(ed, dict) else {}
    if ed.get('prop_path'):
        with_pp += 1
        if ed.get('imported'):
            imported += 1
    loc = o.matrix_world.translation
    if abs(loc.x) > 1000 or abs(loc.y) > 1000:
        peripheral_empty += 1
        if len(periph_e) < 3:
            periph_e.append((o.name[:30], round(loc.x), round(loc.y)))
print("empties with prop_path:", with_pp, " imported:", imported, " peripheral(|loc|>1000):", peripheral_empty)
print("peripheral empty samples:", periph_e)

mn = [1e18]*3; mx = [-1e18]*3
for o in meshes:
    try:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            for i in range(3):
                mn[i] = min(mn[i], w[i]); mx[i] = max(mx[i], w[i])
    except Exception:
        pass
print("MESH bbox min", [round(v) for v in mn], "max", [round(v) for v in mx])
print("covers ~+-9000?", bool(mx[0] > 8000 or mn[0] < -8000 or mx[1] > 8000 or mn[1] < -8000))
