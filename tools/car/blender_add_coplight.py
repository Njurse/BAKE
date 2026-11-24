"""blender_add_coplight.py - drop a police-light marker at the 3D cursor.

In Blender: Scripting workspace > Text Editor > Open this file > Run Script, after
setting NAME below (or call add_coplight("COPLIGHT_R_blue_p128") from the Python
console). It adds a small wireframe cube named NAME at the 3D cursor.

Snap the cursor first (Shift+right-click on the beacon, or Shift+S > Cursor to
Selected on a vertex). Name words: a colour (red blue amber white), p<n> phase
0..255, m for mirror, s<n> size percent - see copmarkers.py.

Export the OBJ as usual, with the marker included (turn off "Selection Only" or
select it with the car), then run copmarkers.py on the car's handling.json.
The marker is left out of the baked car.
"""

import bpy
import bmesh

NAME = "COPLIGHT_L_blue"
SIZE = 4.0          # the marker's edge, in scene units: big enough to see and click


def add_coplight(name=NAME, size=SIZE):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new(name, mesh)
    obj.location = bpy.context.scene.cursor.location
    obj.display_type = "WIRE"
    bpy.context.collection.objects.link(obj)

    for other in bpy.context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


if __name__ == "__main__":
    add_coplight()
