#!/usr/bin/env python3
"""wheelrender.py - put the wheels ON the cars for a render.

The renderers already take an "item" of plain geometry, so the cheapest correct
way to add wheels is to hand them items whose vertices and faces INCLUDE the
wheels. Nothing in preview.py / textured.py / spin.py has to learn about wheels
at all - they just draw more triangles - and all three get the feature from one
place.

Two things need care:

  * the wheel is drawn at its NATIVE size through the same bake scale as the body
    (DrawWheelObject does not scale it; wheelSize only sets the axle height), so
    the copies below scale the wheel's vertices by the body's scale and nothing
    else;
  * the wheel's own texture lives on its SOURCE level's page, not in the baked
    blob, so its texture set has to be loaded from there and RENUMBERED into a
    range the car's sets do not use - otherwise a wheel set and a car set collide
    in the renderer's page cache.
"""

import os
import math
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pages1      # noqa: E402
import wheelgeom   # noqa: E402

# Well above the 0..18 the bake renumbers the pool's own sets into.
WHEEL_SET_BASE = 200


def load_wheel(levels_dir, name="CLEANWHEEL"):
    """(Model, level, page) for the shared wheel, and the page it paints with.

    Returns None when the levels hold no such model. `page` is (index image, CLUT
    rows, transparency masks) in the shape the textured renderers use - the masks
    matter here because the wheel is a SPRITE, and its corners are the
    transparent entry.
    """
    level, model = wheelgeom.canonical_wheel(levels_dir, name)
    if model is None:
        return None
    blob = open(os.path.join(levels_dir, level + ".LEV"), "rb").read()
    wanted = sorted({p.texture_set for p in model.polys
                     if p.kind in ("FT3", "FT4", "GT3", "GT4")})
    _ofs, entries, _ti = pages1.level_pages(blob)
    ent = {s: (o, z) for s, o, z in entries}
    page = None
    for s in wanted:
        if s in ent:
            o, z = ent[s]
            rows, pg = pages1.read_page(blob, o, z)
            page = (pages1.page_indexes(pg),
                    [pages1.clut_rgb(r) for r in rows],
                    [pages1.clut_mask(r) for r in rows])
            break
    return model, level, page


def wheel_copies(model, centres, scale, set_base=WHEEL_SET_BASE, spin=0.0):
    """The wheel replicated at each centre, in render-ready form.

    Returns (verts, tris, tex_faces):
      verts/tris     for the flat renderer (preview.py), index tuples only;
      tex_faces      for the textured ones, each (indices, uvs, set, texid, None)
                     with the set renumbered to `set_base`.

    `spin` rotates each copy about its own axle (the car-local x axis, through
    that wheel's centre) by that many radians - which is what makes the tyre
    texture visibly turn rather than the car just sliding round.

    Polys on the untextured 255 set are KEPT. They look like the untextured
    marker, but they carry real uvs into the same wheel sprite, and they are what
    the wheel has to show when it is edge-on: drop them and the wheels blink out
    whenever the car turns square to the camera (measured: 0 pixels of wheel at
    yaw 0, 391 with them). Keeping them is safe because of the transparency mask -
    their corners sample the sprite's transparent entry, so they darken the body
    with tyre and punch no holes in it.
    """
    cs, sn = math.cos(spin), math.sin(spin)
    verts = []
    tris = []
    tex = []
    for cx, cy, cz in centres:
        base = len(verts)
        for (x, y, z) in model.vertices:
            dy, dz = y * scale, z * scale
            if spin:
                dy, dz = dy * cs - dz * sn, dy * sn + dz * cs
            verts.append((cx + x * scale, cy + dy, cz + dz))
        for p in model.polys:
            v = p.v
            uv = tuple(tuple(u) for u in p.uv)
            if len(v) == 3:
                tris.append((base + v[0], base + v[1], base + v[2]))
                tex.append(((base + v[0], base + v[1], base + v[2]), uv,
                            set_base, p.texture_id, None))
            elif len(v) == 4:
                for a, b, c in ((0, 1, 2), (0, 2, 3)):
                    tris.append((base + v[a], base + v[b], base + v[c]))
                    tex.append(((base + v[a], base + v[b], base + v[c]),
                                (uv[a], uv[b], uv[c]), set_base,
                                p.texture_id, None))
    return verts, tris, tex


def wheeled_item(item, wheel, centres, scale, set_base=WHEEL_SET_BASE, spin=0.0):
    """One item with its wheels attached. `item` is (label, key, verts, faces);
    the faces may be index tuples (flat) or the textured 5-tuples.

    The wheel's own indices come back relative to its copies, so both forms are
    shifted by the car's vertex count here.
    """
    label, key, verts, faces = item[0], item[1], item[2], item[3]
    wv, wtris, wtex = wheel_copies(wheel, centres, scale, set_base, spin)
    off = len(verts)
    allverts = list(verts) + list(wv)
    textured = bool(faces) and isinstance(faces[0][0], tuple)
    if textured:
        extra = [(tuple(off + i for i in ind), uv, s, tid, n)
                 for (ind, uv, s, tid, n) in wtex]
    else:
        extra = [tuple(off + i for i in t) for t in wtris]
    return (label, key, allverts, list(faces) + extra)
