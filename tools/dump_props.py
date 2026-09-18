# -*- coding: utf-8 -*-
"""dump_props.py v2 - inspect + export SourceIO world placeholders.
Fixes: force depsgraph update so matrix_world is valid; print the RAW custom_data
structure so we can see exactly how to read model path + transform.
"""
import bpy, json, os
from mathutils import Vector

bpy.context.view_layer.update()

def as_dict(v):
    if hasattr(v, 'to_dict') and callable(v.to_dict):
        try:
            return v.to_dict()
        except Exception:
            return None
    if isinstance(v, dict):
        return {k: as_dict(x) for k, x in v.items()}
    return v

rows = []
n = 0
details = []
for o in bpy.context.scene.objects:
    if o.type != 'EMPTY':
        continue
    n += 1
    ed = o.get('entity_data')
    raw = as_dict(ed) if ed is not None else None
    w = o.matrix_world
    loc = w.translation
    if len(details) < 3:
        details.append({'name': o.name, 'entity_data': raw, 'world_loc': [round(loc.x,3),round(loc.y,3),round(loc.z,3)]})
    rows.append({'name': o.name, 'entity_data': raw, 'world_loc': [round(loc.x,3),round(loc.y,3),round(loc.z,3)]})

print("empties:", n)
print("=== first 3 details (raw entity_data + world_loc) ===")
print(json.dumps(details, indent=1, default=str)[:3000])

OUT = r"F:\D2Rep_project\dota_replay_analyzer\dist\props_instances.json"
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(rows, open(OUT, "w"), indent=1)
print("wrote", OUT, "rows", len(rows))
