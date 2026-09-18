# -*- coding: utf-8 -*-
"""render_dota_blender.py - orthographic TOP-DOWN full-map render of the imported
Dota world (loaded in Blender via SourceIO from maps/dota.vpk).  This version
AUTO-FRAMES the whole map from its actual world bounds, so it works regardless of
the SourceIO unit scale.  Renders to dist/dota_terrain_full.png (4096x4096).

Run:   blender -b <your.blend> -P "F:\\D2Rep_project\\dota_replay_analyzer\\tools\\render_dota_blender.py"
"""
import bpy
from mathutils import Vector

OUT = r"F:\D2Rep_project\dota_replay_analyzer\dist\dota_terrain_full.png"
RES = 4096

def scene_bounds():
    deps = bpy.context.evaluated_depsgraph_get()
    mn = Vector((1e18, 1e18, 1e18)); mx = Vector((-1e18, -1e18, -1e18))
    for obj in bpy.context.scene.objects:
        if obj.type not in {'MESH', 'EMPTY', 'CURVE'}:
            continue
        try:
            ev = obj.evaluated_get(deps)
            for corner in ev.bound_box:
                w = ev.matrix_world @ Vector(corner)
                mn.x = min(mn.x, w.x); mn.y = min(mn.y, w.y); mn.z = min(mn.z, w.z)
                mx.x = max(mx.x, w.x); mx.y = max(mx.y, w.y); mx.z = max(mx.z, w.z)
        except Exception:
            pass
    return mn, mx

mn, mx = scene_bounds()
cx, cy, cz = (mn.x + mx.x) / 2, (mn.y + mx.y) / 2, (mn.z + mx.z) / 2
size = max(mx.x - mn.x, mx.y - mn.y, mx.z - mn.z)
print("scene bounds min", tuple(round(v,0) for v in mn), "max", tuple(round(v,0) for v in mx))

cam = bpy.data.objects.get("TopCam")
if cam is None:
    cd = bpy.data.cameras.new("TopCam")
    cam = bpy.data.objects.new("TopCam", cd)
    bpy.context.collection.objects.link(cam)
cam.data.type = "ORTHO"
cam.data.ortho_scale = max(mx.x - mn.x, mx.y - mn.y) * 1.05   # frame whole map
cam.location = (cx, cy, cz + size * 2)
cam.rotation_euler = (0.0, 0.0, 0.0)   # top-down (look down -Z)
bpy.context.scene.camera = cam

sc = bpy.context.scene
try:
    sc.render.engine = "BLENDER_WORKBENCH"
except Exception:
    pass
sc.render.resolution_x = RES
sc.render.resolution_y = RES
sc.render.filepath = OUT
bpy.ops.render.render(write_still=True)
print("dota_terrain rendered ->", OUT)
