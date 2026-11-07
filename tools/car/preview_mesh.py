#!/usr/bin/env python3
"""preview_mesh.py - the OBJ/MTL half of the vehicle preview.

Reads the authoring mesh, its uvs and the material each face names. A material
name is also the page key (`set65_id12`, `PAGE_10`, `flat`), so resolving it to
(set, texture id) lives here too - one parsing rule for geometry and textures.
"""

import os

import obj2geom


def load_mtl(path):
    """{material: {"kd": rgb, "tex": path-or-None}} for an MTL beside an OBJ."""
    mats, current = {}, None

    if not os.path.exists(path):
        return mats

    base = os.path.dirname(path)

    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.strip().split(None, 1)

            if not parts:
                continue

            if parts[0] == "newmtl":
                current = mats.setdefault(parts[1].strip(),
                                          {"kd": (0.7, 0.7, 0.7), "tex": None})
            elif current is not None and parts[0] == "Kd":
                current["kd"] = tuple(float(x) for x in parts[1].split()[:3])
            elif current is not None and parts[0] == "map_Kd":
                tex = os.path.join(base, parts[1].strip())
                current["tex"] = tex if os.path.exists(tex) else None

    return mats


def load_obj(path):
    """(vertices, uvs, {material: triangle list}, {material: mtl entry}).

    Each triangle is ((vi, ti), (vi, ti), (vi, ti)) - indices into vertices and
    uvs - grouped by the usemtl that was active when it was read.
    """
    verts, uvs, groups = [], [], {}
    mats, current = {}, None
    base = os.path.dirname(path)

    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.split()

            if not parts:
                continue

            if parts[0] == "v":
                verts.append(tuple(float(x) for x in parts[1:4]))
            elif parts[0] == "vt":
                uvs.append((float(parts[1]),
                            float(parts[2]) if len(parts) > 2 else 0.0))
            elif parts[0] == "mtllib":
                mats.update(load_mtl(os.path.join(
                    base, line.split(None, 1)[1].strip())))
            elif parts[0] == "usemtl":
                current = line.split(None, 1)[1].strip()
            elif parts[0] == "f":
                corners = []

                for token in parts[1:]:
                    fields = token.split("/")
                    vi = int(fields[0])
                    vi = vi - 1 if vi > 0 else len(verts) + vi
                    ti = None

                    if len(fields) > 1 and fields[1]:
                        ti = int(fields[1])
                        ti = ti - 1 if ti > 0 else len(uvs) + ti

                    corners.append((vi, ti))

                for i in range(1, len(corners) - 1):
                    groups.setdefault(current, []).append(
                        (corners[0], corners[i], corners[i + 1]))

    return verts, uvs, groups, mats


def material_page(name, where):
    """Material name -> (set, texture id), or None for an untextured face.

    `obj2geom.material_to_page` is the ONE rule the bake uses, so the preview
    cannot disagree with it about which page a face samples. A typo raises
    there; here it becomes a reported None so one bad material does not blank
    the whole preview.
    """
    try:
        return obj2geom.material_to_page(name, where)
    except ValueError:
        return None
