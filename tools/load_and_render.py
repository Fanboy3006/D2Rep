# -*- coding: utf-8 -*-
"""load_and_render.py - In the Blender scene where dota.vpk was imported via SourceIO:
  (1) select all placeholder EMPTY objects and load their REAL models (sourceio.load_placeholder),
  (2) update scene, add an orthographic TOP-DOWN camera auto-framing the whole map,
  (3) render to dist/dota_terrain_full.png.
Run this AFTER the dota world is imported (content already mounted).
NOTE: loading ~800 models can take a few minutes (watch the bottom progress bar).
"""
import bpy
from mathutils import Vector

OUT = r"F:\D2Rep_project\dota_replay_analyzer\dist\dota_terrain_full.png"
RES = 4096
RENDER_ENGINE = 'BLENDER_WORKBENCH'   # fast; change to 'BLENDER_EEVEE_NEXT' for textures

# ---- 1) load real models for all placeholder empties ----
# Skip material import to avoid SourceIO shader errors (hero.vfx NullObject texture)
try:
    bpy.context.scene.import_materials = False
except Exception:
    pass
bpy.ops.object.select_all(action='DESELECT')
targets = [o for o in bpy.context.scene.objects
           if o.type == 'EMPTY' and o.get('entity_data') and o['entity_data'].get('prop_path')
           and not o['entity_data'].get('imported')]
print("placeholders to load:", len(targets))
for o in targets:
    o.select_set(True)
if targets:
    bpy.context.view_layer.objects.active = targets[0]
    bpy.ops.sourceio.load_placeholder()
    print("done loading models")

# ---- 2) scene bounds (use EVALUATED objects so collection-instances are included) ----
bpy.context.view_layer.update()
deps = bpy.context.evaluated_depsgraph_get()
mn = Vector((1e18, 1e18, 1e18)); mx = Vector((-1e18, -1e18, -1e18))
for obj in bpy.context.scene.objects:
    try:
        ev = obj.evaluated_get(deps)
        for c in ev.bound_box:
            w = ev.matrix_world @ Vector(c)
            for i in range(3):
                mn[i] = min(mn[i], w[i]); mx[i] = max(mx[i], w[i])
    except Exception:
        pass
print("bounds min", tuple(round(v) for v in mn), "max", tuple(round(v) for v in mx))
cx, cy = (mn.x + mx.x) / 2, (mn.y + mx.y) / 2
cz = max(mx.z, mn.z)
size = max(mx.x - mn.x, mx.y - mn.y)

# ---- 3) ortho top camera + render ----
cam = bpy.data.objects.get("TopCam")
if cam is None:
    cd = bpy.data.cameras.new("TopCam"); cam = bpy.data.objects.new("TopCam", cd)
    bpy.context.collection.objects.link(cam)
cam.data.type = 'ORTHO'
cam.data.ortho_scale = max(mx.x - mn.x, mx.y - mn.y) * 1.05
cam.location = (cx, cy, cz + size * 3)
cam.rotation_euler = (0, 0, 0)
bpy.context.scene.camera = cam

sc = bpy.context.scene
# lighting: add a sun from above + a world background so Eevee render isn't black
sun = bpy.data.objects.get("TopSun")
if sun is None:
    sd = bpy.data.lights.new("TopSun", 'SUN'); sd.energy = 3.0
    sun = bpy.data.objects.new("TopSun", sd)
    bpy.context.collection.objects.link(sun)
sun.rotation_euler = (0.0, 0.0, 0.0)          # sun points down -Z -> lights the map
try:
    if sc.world is None:
        wd = bpy.data.worlds.new("World"); sc.world = wd
    if sc.world and not sc.world.use_nodes:
        sc.world.use_nodes = True
    bgn = sc.world.node_tree.nodes.get("Background")
    if bgn:
        bgn.inputs[0].default_value = (0.12, 0.12, 0.14, 1.0)
        bgn.inputs[1].default_value = 1.0
except Exception:
    pass
try:
    sc.render.engine = 'BLENDER_EEVEE_NEXT'
except Exception:
    try:
        sc.render.engine = 'BLENDER_EEVEE'
    except Exception:
        pass
sc.render.resolution_x = RES
sc.render.resolution_y = RES
sc.render.filepath = OUT
bpy.ops.render.render(write_still=True)
print("rendered ->", OUT)
