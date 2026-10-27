#!/usr/bin/env python3
"""jmpped_models.py - model blobs, OBJ I/O and part placement."""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from jmpped_core import *


@dataclass
class Poly:
    kind: int
    set: int
    id: int
    v: list[int]
    uv: list[tuple[int, int]]
    n: list[int] = field(default_factory=list)
    color: tuple[int, int, int] = (0x7F, 0x7F, 0x7F)
    raw: bytes = b""

    @property
    def name(self) -> str:
        return POLY_NAMES.get(self.kind, "K%d" % self.kind)

    @property
    def textured(self) -> bool:
        return self.kind in TEXTURED_KINDS

    @property
    def supported_for_rebuild(self) -> bool:
        return self.kind in (POLY_FT3, POLY_FT4)


@dataclass
class Model:
    blob: bytes
    shape_flags: int
    flags2: int
    instance_number: int
    tri_verts: int
    z_bias: int
    bounding_sphere: int
    num_point_normals: int
    num_vertices: int
    num_polys: int
    vertices_offset: int
    poly_offset: int
    normals_offset: int
    point_normals_offset: int
    collision_offset: int
    vertices: list[tuple[int, int, int]] = field(default_factory=list)
    normals: list[tuple[int, int, int]] = field(default_factory=list)
    point_normals: list[tuple[int, int, int]] = field(default_factory=list)
    polys: list[Poly] = field(default_factory=list)
    collision: bytes = b""
    unsupported_polys: int = 0

    @property
    def slot_count(self) -> int:
        return len(self.polys)


def parse_model(blob: bytes) -> Model:
    if len(blob) < MODEL_HEADER_SIZE:
        raise JMPPEDError("MODEL blob is only %d bytes" % len(blob))
    h = struct.unpack_from(MODEL_HEADER_FMT, blob, 0)
    (shape_flags, flags2, instance_number, tri_verts, z_bias, bounding_sphere,
     num_point_normals, num_vertices, num_polys, vertices_offset, poly_offset,
     normals_offset, point_normals_offset, collision_offset) = h

    m = Model(blob=blob, shape_flags=shape_flags, flags2=flags2,
              instance_number=instance_number, tri_verts=tri_verts,
              z_bias=z_bias, bounding_sphere=bounding_sphere,
              num_point_normals=num_point_normals, num_vertices=num_vertices,
              num_polys=num_polys, vertices_offset=vertices_offset,
              poly_offset=poly_offset, normals_offset=normals_offset,
              point_normals_offset=point_normals_offset,
              collision_offset=collision_offset)

    if 0 <= vertices_offset and vertices_offset + num_vertices * 8 <= len(blob):
        for i in range(num_vertices):
            x, y, z, _pad = struct.unpack_from("<hhhh", blob, vertices_offset + i * 8)
            m.vertices.append((x, y, z))

    # A model with no normal table points both offsets at the poly block. Only
    # read a table when it is distinct and complete.
    normal_offset = normals_offset
    if num_polys and normal_offset != poly_offset and normal_offset >= 0:
        if normal_offset + num_polys * 8 <= len(blob):
            for i in range(num_polys):
                x, y, z, _pad = struct.unpack_from("<hhhh", blob, normal_offset + i * 8)
                m.normals.append((x, y, z))

    if num_point_normals and point_normals_offset != poly_offset and point_normals_offset >= 0:
        if point_normals_offset + num_point_normals * 8 <= len(blob):
            for i in range(num_point_normals):
                x, y, z, _pad = struct.unpack_from("<hhhh", blob, point_normals_offset + i * 8)
                m.point_normals.append((x, y, z))

    if 0 <= poly_offset < len(blob):
        p = poly_offset
        for _ in range(num_polys):
            if p >= len(blob):
                break
            kind = blob[p] & 0x1F
            step = POLY_SIZES[kind] if kind < len(POLY_SIZES) else 0
            if step <= 0 or p + step > len(blob):
                break
            rec = blob[p:p + step]
            poly = _parse_poly(rec, kind)
            if poly is None:
                m.unsupported_polys += 1
            else:
                m.polys.append(poly)
            p += step

    if collision_offset > 0 and collision_offset < len(blob):
        m.collision = blob[collision_offset:]

    return m


def _parse_poly(rec: bytes, kind: int) -> Poly | None:
    setid, texid = rec[1], rec[2]
    if kind == POLY_FT3:
        return Poly(kind=kind, set=setid, id=texid,
                    v=[rec[4], rec[5], rec[6]],
                    uv=[(rec[8], rec[9]), (rec[10], rec[11]), (rec[12], rec[13])],
                    color=tuple(rec[16:19]), raw=rec)
    if kind == POLY_FT4:
        return Poly(kind=kind, set=setid, id=texid,
                    v=[rec[4], rec[5], rec[6], rec[7]],
                    uv=[(rec[8], rec[9]), (rec[10], rec[11]),
                        (rec[12], rec[13]), (rec[14], rec[15])],
                    color=tuple(rec[16:19]), raw=rec)
    if kind == POLY_GT3:
        return Poly(kind=kind, set=setid, id=texid,
                    v=[rec[4], rec[5], rec[6]],
                    n=[rec[8], rec[9], rec[10]],
                    uv=[(rec[12], rec[13]), (rec[14], rec[15]), (rec[16], rec[17])],
                    color=tuple(rec[20:23]), raw=rec)
    if kind == POLY_GT4:
        return Poly(kind=kind, set=setid, id=texid,
                    v=[rec[4], rec[5], rec[6], rec[7]],
                    n=[rec[8], rec[9], rec[10], rec[11]],
                    uv=[(rec[12], rec[13]), (rec[14], rec[15]),
                        (rec[16], rec[17]), (rec[18], rec[19])],
                    color=tuple(rec[20:23]), raw=rec)
    return None


def _encode_poly(poly: Poly) -> bytes:
    if poly.kind not in (POLY_FT3, POLY_FT4):
        raise JMPPEDError(
            "slot poly kind %s cannot be rebuilt from OBJ; only FT3/FT4 are "
            "editable (leave this model untouched to copy it verbatim)"
            % poly.name)
    n = 3 if poly.kind == POLY_FT3 else 4
    rec = bytearray(20)
    rec[0] = poly.kind
    rec[1] = poly.set & 0xFF
    rec[2] = poly.id & 0xFF
    rec[4:4 + n] = bytes((v & 0xFF) for v in poly.v[:n])
    for i in range(n):
        u, v = poly.uv[i] if i < len(poly.uv) else (0, 0)
        rec[8 + i * 2] = u & 0xFF
        rec[9 + i * 2] = v & 0xFF
    rec[16:19] = bytes((c & 0xFF) for c in poly.color[:3])
    return bytes(rec)


def build_model_blob(model: Model,
                     vertices: Sequence[tuple[int, int, int]] | None = None,
                     polys: Sequence[Poly] | None = None) -> bytes:
    """Rebuild a MODEL from vertices/polys while preserving its non-geometry fields.

    The ped archive's models are FT3/FT4 only. A model with any other poly kind
    is refused here; the builder copies untouched models verbatim instead.
    """
    verts = list(vertices if vertices is not None else model.vertices)
    plist = list(polys if polys is not None else model.polys)
    if not plist:
        raise JMPPEDError("model has no polygons")
    for p in plist:
        if not p.supported_for_rebuild:
            raise JMPPEDError(
                "model uses unsupported poly kind %s; only FT3/FT4 are editable"
                % p.name)
        if any(i < 0 or i >= len(verts) for i in p.v):
            raise JMPPEDError("poly references a vertex outside the model")

    stream = b"".join(_encode_poly(p) for p in plist)
    nv = len(verts)
    npol = len(plist)
    vofs = MODEL_HEADER_SIZE
    nofs = vofs + nv * 8
    pnoffs = nofs
    poly_block = pnoffs
    bound = 0
    if verts:
        bound = max(max(abs(x), abs(y), abs(z)) for x, y, z in verts) + 1

    hdr = struct.pack(
        MODEL_HEADER_FMT,
        model.shape_flags, model.flags2, model.instance_number,
        model.tri_verts, model.z_bias, bound,
        0, nv, npol,
        vofs, poly_block, nofs, pnoffs,
        0)
    out = bytearray(hdr)
    for x, y, z in verts:
        out += struct.pack("<hhhh", x, y, z, 0)
    out += stream
    if model.collision:
        collision_offset = len(out)
        out += model.collision
        struct.pack_into("<i", out, 0 + 32, collision_offset)
    return bytes(out)


# --------------------------------------------------------------------------
# Wavefront OBJ helpers
# --------------------------------------------------------------------------

def write_obj(path: str, model: Model, *,
              blender: bool = False, name: str | None = None,
              offset: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> None:
    """Write one part (or the assembled scene) as a Wavefront OBJ.

    Raw mode writes engine units unchanged. Blender mode converts Y-up engine
    space to Blender Z-up, scales by BLENDER_SCALE, and keeps the same handedness.
    """
    name = name or os.path.splitext(os.path.basename(path))[0]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# JMPPED part: %s\n" % name)
        f.write("o %s\n" % name)
        for x, y, z in model.vertices:
            px, py, pz = x + offset[0], y + offset[1], z + offset[2]
            if blender:
                f.write("v %.6f %.6f %.6f\n" % (px * BLENDER_SCALE, pz * BLENDER_SCALE, -py * BLENDER_SCALE))
            else:
                f.write("v %d %d %d\n" % (round(px), round(py), round(pz)))
        # One vt per face corner keeps every original UV exact.
        uv_index = []
        for p in model.polys:
            ki = []
            for u, v in p.uv:
                ki.append(len(uv_index) + 1)
                uv_index.append((u, v))
            p._obj_uv = ki
        for i, (u, v) in enumerate(uv_index, 1):
            f.write("vt %.6f %.6f\n" % (u / 255.0, 1.0 - v / 255.0))
        for p in model.polys:
            if p.textured:
                f.write("usemtl s%d_t%d\n" % (p.set, p.id))
            else:
                f.write("usemtl color_%d_%d_%d\n" % p.color)
            # face vertices are 1-based; vt indices follow this face's corners
            f.write("f " + " ".join("%d/%d" % (v + 1, p._obj_uv[j])
                                    for j, v in enumerate(p.v)) + "\n")


def write_assembled_obj(path: str, models: Sequence[Model], names: Sequence[str],
                        offsets: dict[int, tuple[float, float, float]],
                        *, blender: bool = False) -> None:
    """Write all posed parts into one scene, each as its own OBJ object.

    The assembled scene is for looking and posing. The per-slot OBJs are the
    edit input; a face from this file is not split back into a slot by the
    builder.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    vert_base = 0
    uv_base = 0
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# JMPPED assembled pose (visual/edit reference)\n")
        f.write("# Per-slot OBJs, not this scene, are the importer's input.\n")
        for i, m in enumerate(models):
            if i not in offsets:
                continue
            f.write("o %s\n" % (names[i] if i < len(names) else "part_%d" % i))
            ox, oy, oz = offsets[i]
            for x, y, z in m.vertices:
                px, py, pz = x + ox, y + oy, z + oz
                if blender:
                    f.write("v %.6f %.6f %.6f\n" %
                            (px * BLENDER_SCALE, pz * BLENDER_SCALE, -py * BLENDER_SCALE))
                else:
                    f.write("v %d %d %d\n" % (round(px), round(py), round(pz)))
            uvs = []
            for p in m.polys:
                uvs.extend(p.uv)
            for u, v in uvs:
                f.write("vt %.6f %.6f\n" % (u / 255.0, 1.0 - v / 255.0))
            k = 0
            for p in m.polys:
                if p.textured:
                    f.write("usemtl s%d_t%d\n" % (p.set, p.id))
                else:
                    f.write("usemtl color_%d_%d_%d\n" % p.color)
                f.write("f " + " ".join("%d/%d" % (vert_base + v + 1, uv_base + k + j + 1)
                                        for j, v in enumerate(p.v)) + "\n")
                k += len(p.uv)
            vert_base += len(m.vertices)
            uv_base += len(uvs)



def read_obj(path: str, *, blender: bool = False) -> tuple[list[tuple[float, float, float]], list[list[tuple[int, tuple[int, int] | None, int | None]]]]:
    """Return vertices and faces: (v, vt, material) per corner.

    The importer resolves material names back to (texture_set, texture_id) or a
    flat colour. It is deliberately independent of the assembled scene: the
    per-slot OBJs are the edit input.
    """
    verts: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    faces: list[list[tuple[int, tuple[int, int] | None, int | None]]] = []
    material = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if parts[0] == "v" and len(parts) >= 4:
                x, y, z = (float(parts[1]), float(parts[2]), float(parts[3]))
                if blender:
                    x, y, z = x / BLENDER_SCALE, -z / BLENDER_SCALE, y / BLENDER_SCALE
                verts.append((x, y, z))
            elif parts[0] == "vt" and len(parts) >= 3:
                uvs.append((float(parts[1]), float(parts[2])))
            elif parts[0] == "usemtl" and len(parts) >= 2:
                material = parts[1]
            elif parts[0] == "f" and len(parts) >= 4:
                face = []
                for tok in parts[1:]:
                    bits = tok.split("/")
                    vi = int(bits[0]) - 1
                    ti = int(bits[1]) - 1 if len(bits) > 1 and bits[1] else None
                    face.append((vi, uvs[ti] if ti is not None and 0 <= ti < len(uvs) else None,
                                 material))
                faces.append(face)
    return verts, faces


def write_part_obj(path: str, name: str, verts, faces, *,
                   blender: bool = True) -> None:
    """Write one split part as a deterministic Wavefront OBJ.

    `verts` are engine units; `faces` are `(vertex_index, uv_or_None, material)`
    corners with local indices. UVs are written exactly as supplied (0..1), and
    one `vt` is written per face corner so the result is stable across runs.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# JMPPED part: %s\n" % name)
        f.write("o %s\n" % name)
        for x, y, z in verts:
            if blender:
                f.write("v %.6f %.6f %.6f\n" %
                        (x * BLENDER_SCALE, z * BLENDER_SCALE,
                         -y * BLENDER_SCALE))
            else:
                f.write("v %.6f %.6f %.6f\n" % (x, y, z))

        uv_ids = []
        uv_count = 0
        for face in faces:
            ids = []
            for _vi, uv, _mat in face:
                if uv is None:
                    ids.append(None)
                else:
                    uv_count += 1
                    f.write("vt %.6f %.6f\n" % (uv[0], uv[1]))
                    ids.append(uv_count)
            uv_ids.append(ids)

        for face, ids in zip(faces, uv_ids):
            material = face[0][2] if face else None
            if material is not None:
                f.write("usemtl %s\n" % material)
            corners = []
            for (vi, _uv, _mat), uv_id in zip(face, ids):
                if uv_id is None:
                    corners.append("%d" % (vi + 1))
                else:
                    corners.append("%d/%d" % (vi + 1, uv_id))
            f.write("f " + " ".join(corners) + "\n")


def _resolve_part_group(name: str, part_names: set[str]) -> str | None:
    """Map a Blender object/group name to one of the expected part names.

    Blender writes `PART`, `PART_PART` for the object and `PART_sN_tM` for a
    material subgroup, and appends `.001` when the object name is duplicated.
    `off` and unknown names return None.
    """
    name = name.strip()
    if not name:
        return None
    if "." in name:
        head, tail = name.rsplit(".", 1)
        if tail.isdigit():
            name = head
    if name in part_names:
        return name
    for part in sorted(part_names, key=len, reverse=True):
        if name.startswith(part + "_") or name.startswith(part + "."):
            return part
    return None


def read_combined_obj(path: str, part_names: Iterable[str], *,
                      blender: bool = True) -> dict[str, dict]:
    """Read a whole-character OBJ whose objects/groups are named by part.

    The returned dict is keyed by the resolved part name. Vertices are engine
    units (Blender Z-up is converted when `blender` is true), and each face is
    a list of `(vertex_index, uv_or_None, material)` corners with local vertex
    indices. Faces are deduplicated, and vertices are renumbered in first-use
    order so a re-export with only whitespace/order changes is stable.

    The object-level group and its material subgroups resolve to the same
    part; the face-less `off` group is ignored. A face under a group that does
    not resolve to one of `part_names` is refused instead of being dropped.
    """
    names = set(part_names)
    verts_all: list[tuple[float, float, float]] = []
    uvs_all: list[tuple[float, float]] = []
    groups: dict[str, list[list[tuple[int, tuple[float, float] | None,
                                        int | None]]]] = {}
    current_part = None
    current_group = None
    material = None

    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split()
            tag = p[0]
            if tag in ("o", "g"):
                current_group = p[1] if len(p) > 1 else ""
                current_part = _resolve_part_group(current_group, names)
                continue
            if tag == "v" and len(p) >= 4:
                x, y, z = float(p[1]), float(p[2]), float(p[3])
                if blender:
                    x, y, z = (x / BLENDER_SCALE, -z / BLENDER_SCALE,
                               y / BLENDER_SCALE)
                verts_all.append((x, y, z))
            elif tag == "vt" and len(p) >= 3:
                uvs_all.append((float(p[1]), float(p[2])))
            elif tag == "usemtl" and len(p) >= 2:
                material = p[1]
            elif tag == "f" and len(p) >= 4:
                toks = p[1:]
                if "#" in toks:
                    toks = toks[:toks.index("#")]
                if len(toks) not in (3, 4):
                    raise JMPPEDError(
                        "%s:%d: face has %d corners; only triangles and quads "
                        "are supported" % (path, line_no, len(toks)))
                if current_part is None:
                    raise JMPPEDError(
                        "%s:%d: face under group %r is not one of the known "
                        "parts (%s)" % (path, line_no, current_group,
                                        ", ".join(sorted(names))))
                face = []
                for tok in toks:
                    bits = tok.split("/")
                    try:
                        raw = int(bits[0])
                    except ValueError:
                        raise JMPPEDError("%s:%d: bad vertex index %r"
                                          % (path, line_no, tok)) from None
                    vi = raw - 1 if raw > 0 else len(verts_all) + raw
                    if vi < 0 or vi >= len(verts_all):
                        raise JMPPEDError("%s:%d: vertex index %r is out of "
                                          "range" % (path, line_no, tok))
                    uv = None
                    if len(bits) > 1 and bits[1]:
                        try:
                            raw_uv = int(bits[1])
                        except ValueError:
                            raise JMPPEDError("%s:%d: bad UV index %r"
                                              % (path, line_no, tok)) from None
                        ti = (raw_uv - 1 if raw_uv > 0
                              else len(uvs_all) + raw_uv)
                        if ti < 0 or ti >= len(uvs_all):
                            raise JMPPEDError("%s:%d: UV index %r is out of "
                                              "range" % (path, line_no, tok))
                        uv = uvs_all[ti]
                    face.append((vi, uv, material))
                groups.setdefault(current_part, []).append(face)

    out: dict[str, dict] = {}
    for part, faces in groups.items():
        local_verts: list[tuple[float, float, float]] = []
        vmap: dict[int, int] = {}
        local_faces = []
        seen = set()
        for face in faces:
            key = tuple((vi, uv, mat) for vi, uv, mat in face)
            if key in seen:
                continue
            seen.add(key)
            local = []
            for vi, uv, mat in face:
                if vi not in vmap:
                    vmap[vi] = len(local_verts)
                    local_verts.append(verts_all[vi])
                local.append((vmap[vi], uv, mat))
            local_faces.append(local)
        out[part] = {"verts": local_verts, "faces": local_faces}
    return out

def _size(models: Sequence[Model], i: int) -> tuple[float, float, float]:
    if i >= len(models) or not models[i].vertices:
        return (1.0, 1.0, 1.0)
    vs = models[i].vertices
    return (max(v[0] for v in vs) - min(v[0] for v in vs),
            max(v[1] for v in vs) - min(v[1] for v in vs),
            max(v[2] for v in vs) - min(v[2] for v in vs))


def part_offsets(models: Sequence[Model]) -> dict[int, tuple[float, float, float]]:
    torso = _size(models, 0)
    head = _size(models, 1)
    uw, uh, _ = _size(models, 2)
    lw, lh, _ = _size(models, 3)
    tw, th, _ = _size(models, 6)
    cw, ch, _ = _size(models, 7)
    fw, fh, _ = _size(models, 8)
    _hw, hh, _ = _size(models, 12)
    _h2w, h2h, _ = _size(models, 13)
    x_arm = torso[0] * 0.55 + uw * 0.5
    # Engine +Y is DOWN. A positive offset moves a part down the body; the head
    # is above the torso centre and the legs/hands hang below it.
    y_arm = torso[1] * 0.18
    x_leg = torso[0] * 0.22
    lower_y = y_arm + uh * 0.5 + lh * 0.5
    return {
        0: (0.0, 0.0, 0.0),
        1: (0.0, -(torso[1] * 0.5 + head[1] * 0.5), 0.0),
        2: (-x_arm, y_arm, 0.0),
        3: (-x_arm, lower_y, 0.0),
        4: (x_arm, y_arm, 0.0),
        5: (x_arm, lower_y, 0.0),
        6: (-x_leg, torso[1] * 0.5 + th * 0.5, 0.0),
        7: (-x_leg, torso[1] * 0.5 + th + ch * 0.5, 0.0),
        8: (-x_leg, torso[1] * 0.5 + th + ch + fh * 0.5, 0.0),
        9: (x_leg, torso[1] * 0.5 + th * 0.5, 0.0),
        10: (x_leg, torso[1] * 0.5 + th + cw * 0.5, 0.0),
        11: (x_leg, torso[1] * 0.5 + th + cw + fh * 0.5, 0.0),
        12: (-x_arm, lower_y + lh * 0.5 + hh * 0.5, 0.0),
        13: (x_arm, lower_y + lh * 0.5 + h2h * 0.5, 0.0),
    }
