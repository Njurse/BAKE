#!/usr/bin/env python3
"""jmpped_textured.py - canonical per-part textured OBJ for Blender editing.

`model_textured.obj` is the edit model. Each part is an object, each textured
face uses `PART__s<set>_t<id>`, and its UVs are 0..1 inside that material's
crop image. `model_textured.mtl` binds each material to
`textures/crops/<part>_set<set>_t<id>.png`.

The page-space `model.obj` remains the splitter/reference model and is
generated from the textured model. The textured model wins.
"""

from __future__ import annotations

import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jmpped_core as jc
import jmpped_entries as je
import jmpped_images as ji
import jmpped_models as jm

FORMAT = "textured-obj-v1"
UV_SPACE = "crop"
MATERIAL_SEP = "__s"
EPS = 0.08

# A Blender UV can sit a pixel or two outside its crop image (an extra asset
# authored at 20x8, a seam nudged while painting). Clipping that sliver to the
# image edge is lossless enough to import; treating it as a hard error made a
# character impossible to bake after a harmless UV edit. Keep a real error for a
# UV that misses the crop by more than this many pixels, which means the crop
# rect or the material mapping is actually wrong.
UV_PAD_PIXELS = 2.0


def _abs(project: str, rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(
        project, rel.replace("/", os.sep))


def _rel(project: str, path: str) -> str:
    return os.path.relpath(path, project).replace("\\", "/")


def _character(manifest: dict, name: str | None = None) -> dict:
    if name:
        return je.character(manifest, name)
    chars = manifest.get("characters") or []
    if not chars:
        raise jc.JMPPEDError("project has no characters")
    return chars[-1]


def part_material(part: str, page_set: int, texture_id: int) -> str:
    return "%s%s%d_t%d" % (part, MATERIAL_SEP, int(page_set), int(texture_id))


def parse_material(name: str | None):
    """Return (part, page_set, texture_id), or None for a flat/unknown name."""
    if not name:
        return None
    name = name.strip()
    if "." in name:
        head, tail = name.rsplit(".", 1)
        if tail.isdigit():
            name = head
    if MATERIAL_SEP not in name:
        return None
    part, rest = name.rsplit(MATERIAL_SEP, 1)
    if "_t" not in rest:
        return None
    set_s, id_s = rest.split("_t", 1)
    try:
        return part, int(set_s), int(id_s)
    except ValueError:
        return None


def _parse_page_material(name: str | None):
    if not name:
        return None
    name = name.strip()
    if "." in name:
        head, tail = name.rsplit(".", 1)
        if tail.isdigit():
            name = head
    if name.startswith("s") and "_t" in name:
        set_s, id_s = name[1:].split("_t", 1)
        try:
            return int(set_s), int(id_s)
        except ValueError:
            return None
    parsed = parse_material(name)
    if parsed is not None:
        _part, set_s, id_s = parsed
        return set_s, id_s
    return None


def _has_part_material(groups: dict) -> bool:
    for group in groups.values():
        for face in group.get("faces", []):
            if face and parse_material(face[0][2]):
                return True
    return False


def _target_set(character: dict, source_set: int) -> int:
    page_map = character.get("texture_page_map") or {}
    return int(page_map.get(str(source_set), page_map.get(source_set,
                                                           source_set)))


def _crop(character: dict, part: str, page_set: int, texture_id: int):
    for entry in character.get("parts", []):
        if entry.get("part") != part:
            continue
        for crop in entry.get("crops", []):
            if (int(crop["page_set"]) == int(page_set)
                    and int(crop["texture"]) == int(texture_id)):
                return crop
    return None


def _extra_material(character: dict, material: str | None):
    for extra in character.get("extra_materials", []):
        if extra.get("material") == material:
            return extra
    return None


def _crop_path(project: str, character: dict, crop: dict) -> str:
    return _abs(project, crop["rgb"])


def texture_groups(character: dict) -> dict:
    """Group part crops by the game texture they belong to."""
    groups: dict[tuple[int, int], dict] = {}
    for part in character.get("parts", []):
        name = part.get("part")
        for crop in part.get("crops", []):
            if "page_set" not in crop or "texture" not in crop:
                continue
            page_set = int(crop["page_set"])
            texture = int(crop["texture"])
            key = (page_set, texture)
            group = groups.get(key)
            if group is None:
                group = {
                    "page_set": page_set,
                    "texture": texture,
                    "rect": None,
                    "palette_row": int(crop.get("palette_row", texture)),
                    "members": [],
                }
                groups[key] = group
            rect = [int(v) for v in crop["rect"]]
            group["members"].append({
                "part": name,
                "rect": rect,
                "rgb": crop["rgb"],
                "palette_row": int(crop.get("palette_row", texture)),
            })
            if group["rect"] is None:
                group["rect"] = rect[:]
            else:
                x, y, w, h = group["rect"]
                nx, ny, nw, nh = rect
                x2 = max(x + w, nx + nw)
                y2 = max(y + h, ny + nh)
                x = min(x, nx)
                y = min(y, ny)
                group["rect"] = [x, y, x2 - x, y2 - y]
    folder = character.get("folder", "characters").rstrip("/")
    for (page_set, texture), group in groups.items():
        group["shared"] = len(group["members"]) > 1
        group["rgb"] = "%s/textures/crops/s%d_t%d.png" % (
            folder, page_set, texture)
    return groups


def shared_textures(character: dict) -> list[dict]:
    """Return the shared-texture metadata for groups used by >1 part."""
    result = []
    for (page_set, texture), group in sorted(texture_groups(character).items()):
        if not group["shared"]:
            continue
        result.append({
            "page_set": page_set,
            "texture": texture,
            "rect": list(group["rect"]),
            "rgb": group["rgb"],
            "palette_row": group["palette_row"],
            "members": [m["part"] for m in group["members"]],
            "source_rects": [list(m["rect"]) for m in group["members"]],
        })
    return result


def _page_state(project: str, character: dict, page_set: int):
    page = next((p for p in character.get("pages", [])
                 if int(p.get("page_set", -1)) == int(page_set)), None)
    if page is None:
        raise jc.JMPPEDError("page set %d is not in the character" % page_set)
    indexed = page.get("indexed_png")
    palette = page.get("palette_png")
    if not indexed or not palette:
        raise jc.JMPPEDError("page set %d has no indexed/palette image" % page_set)
    w, h, indices = ji.load_png_indices(_abs(project, indexed))
    if (w, h) != (jc.PAGE_W, jc.PAGE_H):
        raise jc.JMPPEDError("%s is %dx%d, expected %dx%d"
                             % (indexed, w, h, jc.PAGE_W, jc.PAGE_H))
    pw, ph, pixels = ji.load_png_rgb(_abs(project, palette))
    if pw != jc.CLUT_ROW_ENTRIES:
        raise jc.JMPPEDError("%s has %d palette entries per row, expected %d"
                             % (palette, pw, jc.CLUT_ROW_ENTRIES))
    rows = [ji.rgb_to_clut(pixels[y * pw:(y + 1) * pw])
            for y in range(ph)]
    return indices, rows


def _union_pixels(project: str, character: dict, group: dict):
    x, y, w, h = (int(v) for v in group["rect"])
    indices, rows = _page_state(project, character, int(group["page_set"]))
    row = int(group["palette_row"])
    if row < 0 or row >= len(rows):
        raise jc.JMPPEDError("texture row %d is outside page %d's %d rows"
                             % (row, group["page_set"], len(rows)))
    clut = ji.clut_rgb(rows[row])
    pixels = []
    for yy in range(h):
        base = (y + yy) * jc.PAGE_W + x
        for xx in range(w):
            pixels.append(clut[indices[base + xx]])
    for member in group["members"]:
        mx, my, mw, mh = (int(v) for v in member["rect"])
        path = _abs(project, member["rgb"])
        if not os.path.isfile(path):
            continue
        cw, ch, crop = ji.load_png_rgb(path)
        ox, oy = mx - x, my - y
        for cy in range(min(ch, h - oy)):
            for cx in range(min(cw, w - ox)):
                px, py = ox + cx, oy + cy
                if 0 <= px < w and 0 <= py < h:
                    pixels[py * w + px] = crop[cy * cw + cx]
    return pixels, w, h


def _write_rgb(path: str, pixels, width: int, height: int) -> None:
    from PIL import Image
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = bytes(c for p in pixels for c in p)
    Image.frombytes("RGB", (width, height), data).save(path)


def _backup_combine(project: str, character: dict, shared: dict) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(project, ".state", "backups", "combine-%s" % stamp)
    n = 1
    base = dest
    while os.path.exists(dest):
        dest = "%s-%d" % (base, n)
        n += 1
    os.makedirs(dest)
    model = character.get("model") or {}
    for rel in (model.get("path"), model.get("mtl"),
                character["folder"].rstrip("/") + "/character.json",
                "project.json"):
        if rel:
            src = _abs(project, rel)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(dest, os.path.basename(src)))
    crop_dir = os.path.join(dest, "crops")
    os.makedirs(crop_dir, exist_ok=True)
    for group in shared.values():
        for member in group["members"]:
            src = _abs(project, member["rgb"])
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(crop_dir,
                                               os.path.basename(src)))
    return dest


def _rewrite_model(project: str, character: dict, model_abs: str,
                   mtl_abs: str, old_crops: dict, shared: dict) -> None:
    names = set(p["part"] for p in character.get("parts", []))
    groups = jm.read_combined_obj(model_abs, names, blender=True)
    lines = ["# JMPPED textured model (shared crops)\n",
             "mtllib %s\n" % os.path.basename(mtl_abs)]
    vert_base = 1
    uv_base = 1
    for part in character["parts"]:
        name = part["part"]
        group = groups.get(name)
        if not group:
            continue
        lines.append("o %s\n" % name)
        for x, y, z in group["verts"]:
            lines.append("v %.6f %.6f %.6f\n" %
                         (x * jc.BLENDER_SCALE, z * jc.BLENDER_SCALE,
                          -y * jc.BLENDER_SCALE))
        for face in group["faces"]:
            material = face[0][2]
            parsed = parse_material(material)
            uvs = []
            for _vi, uv, _mat in face:
                if uv is None:
                    uvs.append(None)
                    continue
                if parsed is not None:
                    _p, source_set, texture = parsed
                    target_set = _target_set(character, source_set)
                    old = old_crops.get((name, target_set, texture))
                    group_rec = shared.get((target_set, texture))
                    if old is not None and group_rec is not None:
                        uv = _clip_uv(page_to_crop(
                            crop_to_page(uv, old["rect"]),
                            group_rec["rect"]))
                uvs.append(uv)
            ids = []
            for uv in uvs:
                if uv is None:
                    ids.append(None)
                else:
                    ids.append(uv_base)
                    lines.append("vt %.6f %.6f\n" % (uv[0], uv[1]))
                    uv_base += 1
            lines.append("usemtl %s\n" % material)
            corners = []
            for (vi, _uv, _mat), uid in zip(face, ids):
                gi = vert_base + vi
                corners.append("%d" % gi if uid is None
                               else "%d/%d" % (gi, uid))
            lines.append("f " + " ".join(corners) + "\n")
        vert_base += len(group["verts"])
    with open(model_abs, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(lines))


def _rewrite_mtl(project: str, character: dict, mtl_abs: str,
                 shared: dict) -> None:
    char_dir = os.path.join(project, character["folder"])
    lines = []
    current = None
    with open(mtl_abs, encoding="utf-8") as f:
        for line in f:
            if line.startswith("newmtl "):
                current = line.split(None, 1)[1].strip()
            if line.startswith("map_Kd ") and current:
                rel = None
                parsed = parse_material(current)
                if parsed is not None:
                    part, source_set, texture = parsed
                    target_set = _target_set(character, source_set)
                    group = shared.get((target_set, texture))
                    if group is not None:
                        rel = os.path.relpath(
                            _abs(project, group["rgb"]), char_dir)
                    else:
                        crop = _crop(character, part, target_set, texture)
                        if crop is not None:
                            rel = os.path.relpath(
                                _abs(project, crop["rgb"]), char_dir)
                else:
                    extra = _extra_material(character, current)
                    if extra is not None:
                        rel = os.path.relpath(
                            _abs(project, extra["rgb"]), char_dir)
                if rel is not None:
                    line = "map_Kd %s\n" % rel.replace(os.sep, "/")
            lines.append(line)
    with open(mtl_abs, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(lines))


def combine_character(project: str, character_name: str | None = None,
                      dry_run: bool = False) -> int:
    project = os.path.abspath(project)
    manifest = je.load_manifest(project)
    character = _character(manifest, character_name)
    groups = texture_groups(character)
    shared = {k: g for k, g in groups.items() if g["shared"]}
    if dry_run:
        print("shared textures:")
        for (page_set, texture), group in sorted(shared.items()):
            print("  s%d_t%d row %d rect %s members %s"
                  % (page_set, texture, group["palette_row"],
                     group["rect"],
                     ", ".join(m["part"] for m in group["members"])))
        for (page_set, texture), group in sorted(groups.items()):
            if not group["shared"]:
                print("  unique s%d_t%d row %d rect %s members %s"
                      % (page_set, texture, group["palette_row"],
                         group["rect"],
                         ", ".join(m["part"] for m in group["members"])))
        return 0
    if not shared:
        print("no shared textures to combine in %s" % character["name"])
        return 0

    model = character.get("model") or {}
    if (model.get("format") != FORMAT
            or not os.path.isfile(_abs(project, model.get("path", "")))):
        rc = export_textured(project, character.get("name"))
        if rc:
            return rc
        manifest = je.load_manifest(project)
        character = _character(manifest, character.get("name"))
        groups = texture_groups(character)
        shared = {k: g for k, g in groups.items() if g["shared"]}
        if not shared:
            print("no shared textures to combine in %s" % character["name"])
            return 0

    shared_meta = shared_textures(character)
    old_crops = {}
    for part in character["parts"]:
        for crop in part.get("crops", []):
            old_crops[(part["part"], int(crop["page_set"]),
                       int(crop["texture"]))] = dict(crop)

    backup = _backup_combine(project, character, shared)
    for (page_set, texture), group in shared.items():
        pixels, w, h = _union_pixels(project, character, group)
        path = _abs(project, group["rgb"])
        _write_rgb(path, pixels, w, h)
        print("shared s%d_t%d -> %s (%dx%d)"
              % (page_set, texture, group["rgb"], w, h))

    for part in character["parts"]:
        for crop in part.get("crops", []):
            key = (int(crop["page_set"]), int(crop["texture"]))
            group = shared.get(key)
            if group is None:
                continue
            crop["rgb"] = group["rgb"]
            crop["rect"] = list(group["rect"])
            crop["palette_row"] = group["palette_row"]
            crop["rgb_sha256"] = ""
            crop.pop("indexed", None)
            crop.pop("indexed_sha256", None)

    character["shared_textures"] = shared_meta
    model_path = _abs(project, character["model"]["path"])
    mtl_rel = (character["model"].get("mtl")
               or character["folder"].rstrip("/") + "/model_textured.mtl")
    mtl_abs = _abs(project, mtl_rel)
    _rewrite_model(project, character, model_path, mtl_abs,
                   old_crops, shared)
    _rewrite_mtl(project, character, mtl_abs, shared)

    index = next((i for i, c in enumerate(manifest.get("characters", []))
                  if c.get("name") == character.get("name")
                  or c.get("folder") == character.get("folder")), None)
    if index is None:
        raise jc.JMPPEDError("character %r is not in project.json"
                             % character.get("name", "?"))
    manifest["characters"][index] = character
    jc.save_json(os.path.join(project, "project.json"), manifest)
    jc.save_json(os.path.join(project, character["folder"], "character.json"),
                 character)
    print("combined %d shared texture(s) in %s"
          % (len(shared), character["name"]))
    print("backed up -> %s" % backup)
    return 0


def page_to_crop(uv, rect):
    x, y, w, h = (float(v) for v in rect)
    px = uv[0] * 255.0
    py = (1.0 - uv[1]) * 255.0
    return ((px - x) / w, 1.0 - (py - y) / h)


def crop_to_page(uv, rect):
    x, y, w, h = (float(v) for v in rect)
    px = x + uv[0] * w
    py = y + (1.0 - uv[1]) * h
    return (px / 255.0, 1.0 - py / 255.0)


def _inside(uv, rect=None) -> bool:
    """Is this crop-space UV inside the crop (with a small pixel margin)?

    `rect` is the crop's page-space (x, y, w, h). Without it, the historical
    fixed EPS is used by callers that only have UVs (e.g. the round-trip test).
    """
    if rect is None:
        return (-EPS <= uv[0] <= 1.0 + EPS
                and -EPS <= uv[1] <= 1.0 + EPS)
    w = max(1.0, float(rect[2]))
    h = max(1.0, float(rect[3]))
    mu = UV_PAD_PIXELS / w
    mv = UV_PAD_PIXELS / h
    return (-mu <= uv[0] <= 1.0 + mu
            and -mv <= uv[1] <= 1.0 + mv)


def _clip_uv(uv):
    return (max(0.0, min(1.0, uv[0])),
            max(0.0, min(1.0, uv[1])))


def _read_page_groups(project: str, character: dict, path: str):
    names = set(p["part"] for p in character.get("parts", [])) | {"HIPS"}
    return jm.read_combined_obj(path, names, blender=True)


def _write_textured(project: str, character: dict, groups: dict,
                    out_path: str, mtl_path: str) -> None:
    char_dir = os.path.join(project, character["folder"])
    lines = ["# JMPPED textured model (generated)\n",
             "mtllib %s\n" % os.path.basename(mtl_path)]
    materials: dict[str, tuple] = {}
    vert_base = 1
    uv_base = 1
    for part in character["parts"]:
        name = part["part"]
        group = groups.get(name)
        if not group or not group["verts"] or not group["faces"]:
            raise jc.JMPPEDError("%s: part %s has no faces in the page model"
                                 % (character["name"], name))
        lines.append("o %s\n" % name)
        for x, y, z in group["verts"]:
            lines.append("v %.6f %.6f %.6f\n" %
                         (x * jc.BLENDER_SCALE, z * jc.BLENDER_SCALE,
                          -y * jc.BLENDER_SCALE))
        for face in group["faces"]:
            material = face[0][2]
            parsed = _parse_page_material(material)
            if parsed is not None:
                source_set, texture_id = parsed
                target_set = _target_set(character, source_set)
                crop = _crop(character, name, target_set, texture_id)
                if crop is None:
                    raise jc.JMPPEDError(
                        "%s/%s: no crop for %s (page set %d, texture %d)"
                        % (character["name"], name, material, target_set,
                           texture_id))
                rect = crop["rect"]
                uvs = []
                for _vi, uv, _mat in face:
                    if uv is None:
                        raise jc.JMPPEDError(
                            "%s/%s: textured face has no UV"
                            % (character["name"], name))
                    cuv = _clip_uv(page_to_crop(uv, rect))
                    if not _inside(cuv):
                        raise jc.JMPPEDError(
                            "%s/%s: UV %r is outside crop rect %r for %s"
                            % (character["name"], name, uv, rect, material))
                    uvs.append(cuv)
                mname = part_material(name, target_set, texture_id)
                materials[mname] = ("crop", crop)
            elif material and material.startswith("color_"):
                mname = material
                uvs = [uv for _vi, uv, _mat in face]
                try:
                    _prefix, r, gr, bl = material.split("_")
                    materials[mname] = ("color", (int(r), int(gr), int(bl)))
                except ValueError:
                    materials[mname] = ("color", (200, 200, 200))
            else:
                mname = "color_127_127_127"
                uvs = [uv for _vi, uv, _mat in face]
                materials.setdefault(mname, ("color", (127, 127, 127)))
                print("warning: %s/%s: %r has no page material; using flat grey"
                      % (character["name"], name, material))

            ids = []
            for uv in uvs:
                if uv is None:
                    ids.append(None)
                else:
                    ids.append(uv_base)
                    lines.append("vt %.6f %.6f\n" % (uv[0], uv[1]))
                    uv_base += 1
            lines.append("usemtl %s\n" % mname)
            corners = []
            for (vi, _uv, _mat), uid in zip(face, ids):
                gi = vert_base + vi
                corners.append("%d" % gi if uid is None
                               else "%d/%d" % (gi, uid))
            lines.append("f " + " ".join(corners) + "\n")
        vert_base += len(group["verts"])

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(lines))
    _write_mtl(project, character, mtl_path, materials)


def _write_mtl(project: str, character: dict, path: str,
               materials: dict[str, tuple]) -> None:
    char_dir = os.path.join(project, character["folder"])
    lines = ["# JMPPED textured materials (generated)\n"]
    for name, info in materials.items():
        kind, value = info
        lines.append("newmtl %s\n" % name)
        lines.append("Ka 1.000000 1.000000 1.000000\n")
        if kind == "crop":
            lines.append("Kd 0.800000 0.800000 0.800000\n")
            crop_abs = _crop_path(project, character, value)
            rel = os.path.relpath(crop_abs, char_dir).replace(os.sep, "/")
            lines.append("map_Kd %s\n" % rel)
        else:
            r, gr, bl = value
            lines.append("Kd %.6f %.6f %.6f\n" %
                         (r / 255.0, gr / 255.0, bl / 255.0))
        lines.append("Ks 0.000000 0.000000 0.000000\n")
        lines.append("d 1.000000\n")
        lines.append("illum 1\n\n")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(lines))


def _remap(faces, verts_all):
    local_verts = []
    vmap = {}
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
    return {"verts": local_verts, "faces": local_faces}


def scan_textured(project: str, character: dict,
                  path: str | None = None):
    """Read the textured model, returning page-space groups, stats and issues."""
    model = character.get("model") or {}
    rel = path or model.get("path")
    if not rel:
        raise jc.JMPPEDError("character has no model")
    path_abs = _abs(project, rel)
    if not os.path.isfile(path_abs):
        raise jc.JMPPEDError("textured model is missing: %s" % path_abs)
    names = set(p["part"] for p in character.get("parts", []))
    verts_all = []
    uvs_all = []
    groups = {}
    stats = {}
    issues = []
    current_part = None
    current_group = None
    material = None

    with open(path_abs, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split()
            tag = p[0]
            if tag in ("o", "g"):
                current_group = p[1] if len(p) > 1 else ""
                current_part = jm._resolve_part_group(current_group, names)
                continue
            if tag == "v" and len(p) >= 4:
                x, y, z = float(p[1]), float(p[2]), float(p[3])
                verts_all.append((x / jc.BLENDER_SCALE, -z / jc.BLENDER_SCALE,
                                  y / jc.BLENDER_SCALE))
            elif tag == "vt" and len(p) >= 3:
                uvs_all.append((float(p[1]), float(p[2])))
            elif tag == "usemtl" and len(p) >= 2:
                material = p[1]
            elif tag == "f" and len(p) >= 4:
                toks = p[1:]
                if "#" in toks:
                    toks = toks[:toks.index("#")]
                if len(toks) not in (3, 4):
                    issues.append("%s:%d: face has %d corners"
                                  % (path_abs, line_no, len(toks)))
                    continue
                parsed = parse_material(material)
                extra = None if parsed is not None else _extra_material(
                    character, material)
                if parsed is not None or extra is not None:
                    if parsed is not None:
                        part, source_set, texid = parsed
                        setid = _target_set(character, source_set)
                        if current_part and current_part != part:
                            issues.append(
                                "%s:%d: material %s is for %s under group %s"
                                % (path_abs, line_no, material, part,
                                   current_group))
                        crop = _crop(character, part, setid, texid)
                        if crop is None:
                            issues.append(
                                "%s:%d: no crop for %s/%s (source set %d -> "
                                "target %d)" % (path_abs, line_no, part,
                                                material, source_set, setid))
                            continue
                    else:
                        part = extra.get("part") or current_part
                        if part is None:
                            issues.append(
                                "%s:%d: extra material %r has no part"
                                % (path_abs, line_no, material))
                            continue
                        setid = int(extra["page_set"])
                        texid = int(extra["texture"])
                        crop = {"rect": extra["rect"], "rgb": extra["rgb"],
                                "palette_row": extra.get("palette_row")}
                    rect = crop["rect"]
                    face = []
                    st = stats.setdefault(
                        (part, setid, texid),
                        {"crop": crop, "faces": 0, "outside": 0,
                         "clipped": 0,
                         "u_min": 1e9, "u_max": -1e9,
                         "v_min": 1e9, "v_max": -1e9})
                    st["faces"] += 1
                    for tok in toks:
                        bits = tok.split("/")
                        vi = int(bits[0]) - 1
                        uv = None
                        if len(bits) > 1 and bits[1]:
                            ti = int(bits[1]) - 1
                            uv = uvs_all[ti] if 0 <= ti < len(uvs_all) else None
                        if uv is None:
                            issues.append("%s:%d: textured face without a UV"
                                          % (path_abs, line_no))
                            continue
                        if not _inside(uv):
                            st["clipped"] += 1
                            if not _inside(uv, rect):
                                st["outside"] += 1
                                issues.append(
                                    "%s:%d: %s UV %r is outside the crop"
                                    % (path_abs, line_no, material, uv))
                        uv = _clip_uv(uv)
                        st["u_min"] = min(st["u_min"], uv[0])
                        st["u_max"] = max(st["u_max"], uv[0])
                        st["v_min"] = min(st["v_min"], uv[1])
                        st["v_max"] = max(st["v_max"], uv[1])
                        face.append((vi, crop_to_page(uv, rect),
                                     "s%d_t%d" % (setid, texid)))
                    if face:
                        groups.setdefault(part, []).append(face)
                elif material and material.startswith("color_"):
                    part = current_part
                    if part is None:
                        issues.append("%s:%d: flat face under unknown group %r"
                                      % (path_abs, line_no, current_group))
                        continue
                    face = []
                    for tok in toks:
                        bits = tok.split("/")
                        vi = int(bits[0]) - 1
                        uv = None
                        if len(bits) > 1 and bits[1]:
                            ti = int(bits[1]) - 1
                            uv = uvs_all[ti] if 0 <= ti < len(uvs_all) else None
                        face.append((vi, uv, material))
                    groups.setdefault(part, []).append(face)
                else:
                    issues.append("%s:%d: unrecognised material %r"
                                  % (path_abs, line_no, material))

    out = {part: _remap(faces, verts_all) for part, faces in groups.items()}
    return out, stats, issues


def read_textured(project: str, character: dict,
                  path: str | None = None) -> dict:
    groups, _stats, issues = scan_textured(project, character, path)
    if issues:
        raise jc.JMPPEDError("textured model: " + "; ".join(issues[:6]))
    return groups


def write_page_model(project: str, character: dict, groups: dict) -> None:
    """Write the generated page-space model.obj from the textured conversion."""
    model = character.setdefault("model", {})
    page_rel = model.get("page_path") or (character["folder"].rstrip("/")
                                          + "/model.obj")
    page_abs = _abs(project, page_rel)
    lines = ["# JMPPED page-space model (generated)\n"]
    vert_base = 1
    uv_base = 1
    for part in character["parts"]:
        name = part["part"]
        group = groups.get(name)
        if not group:
            continue
        lines.append("o %s\n" % name)
        for x, y, z in group["verts"]:
            lines.append("v %.6f %.6f %.6f\n" %
                         (x * jc.BLENDER_SCALE, z * jc.BLENDER_SCALE,
                          -y * jc.BLENDER_SCALE))
        for face in group["faces"]:
            ids = []
            for _vi, uv, _mat in face:
                if uv is None:
                    ids.append(None)
                else:
                    ids.append(uv_base)
                    lines.append("vt %.6f %.6f\n" % (uv[0], uv[1]))
                    uv_base += 1
            lines.append("usemtl %s\n" % (face[0][2] or "color_127_127_127"))
            corners = []
            for (vi, _uv, _mat), uid in zip(face, ids):
                gi = vert_base + vi
                corners.append("%d" % gi if uid is None
                               else "%d/%d" % (gi, uid))
            lines.append("f " + " ".join(corners) + "\n")
        vert_base += len(group["verts"])
    os.makedirs(os.path.dirname(os.path.abspath(page_abs)), exist_ok=True)
    with open(page_abs, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(lines))
    model["page_path"] = _rel(project, page_abs)
    model["page_sha256"] = jc.sha256_file(page_abs)


def _png_size(path: str):
    try:
        w, h, _pixels = ji.load_png_rgb(path)
        return w, h
    except Exception:
        w, h, _indices = ji.load_png_indices(path)
        return w, h


def check_uvs(project: str, character_name: str | None = None) -> int:
    manifest = je.load_manifest(project)
    character = _character(manifest, character_name)
    _groups, stats, issues = scan_textured(project, character)
    print("character : %s" % character["name"])
    print("model     : %s" % character.get("model", {}).get("path"))
    shared = character.get("shared_textures") or []
    if shared:
        print("shared    :")
        for group in shared:
            print("  s%d_t%d %s members=%s"
                  % (group["page_set"], group["texture"], group["rgb"],
                     ", ".join(group["members"])))
    print("%-26s %-12s %-42s %-14s %-9s %-5s %s"
          % ("material", "part", "crop", "rect", "size", "row",
             "uvs / status"))
    bad = bool(issues)
    for (part, setid, texid), st in sorted(stats.items()):
        crop = st["crop"]
        path = _crop_path(project, character, crop)
        try:
            size = _png_size(path)
        except Exception as exc:
            size = ("?", "?")
            issues.append("%s/%s: cannot read crop: %s" % (part, crop["rgb"], exc))
        rect = crop["rect"]
        rect_size = (int(rect[2]), int(rect[3]))
        status = "ok"
        if st["outside"]:
            status = "UV OUTSIDE (%d)" % st["outside"]
            bad = True
        elif st["clipped"]:
            status = "UV CLIPPED (%d)" % st["clipped"]
        if size != rect_size:
            status += " SIZE %sx%s != %dx%d" % (size[0], size[1],
                                                rect_size[0], rect_size[1])
            bad = True
        print("%-26s %-12s %-42s %-14s %-9s %-5d %s"
              % (part_material(part, setid, texid), part,
                 crop["rgb"], "%d,%d,%d,%d" % tuple(rect),
                 "%sx%s" % size,
                 int(crop.get("palette_row", crop.get("texture", 0))),
                 "u %.3f..%.3f v %.3f..%.3f %s"
                 % (st["u_min"], st["u_max"], st["v_min"], st["v_max"],
                    status)))
    for issue in issues:
        print("issue     : %s" % issue)
    if bad:
        print("texture check: FAILED (start with model_textured.mtl and the crop rect/size)")
    else:
        print("texture check: OK")
    return 1 if bad else 0


def _backup(project: str, character: dict, page_abs: str,
            char_dir: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(project, ".state", "backups",
                        "textured-export-%s" % stamp)
    n = 1
    base = dest
    while os.path.exists(dest):
        dest = "%s-%d" % (base, n)
        n += 1
    os.makedirs(dest)
    shutil.copy2(page_abs, os.path.join(dest, os.path.basename(page_abs)))
    mtl = os.path.join(char_dir, "bindpose_edit.mtl")
    if os.path.isfile(mtl):
        shutil.copy2(mtl, os.path.join(dest, "bindpose_edit.mtl"))
    return dest


def export_textured(project: str, character_name: str | None = None) -> int:
    project = os.path.abspath(project)
    manifest = je.load_manifest(project)
    character = _character(manifest, character_name)
    model = character.setdefault("model", {})
    if (model.get("format") == FORMAT
            or model.get("uv_space") == UV_SPACE):
        print("%s is already textured; checking instead of overwriting"
              % character["name"])
        return check_uvs(project, character["name"])

    page_rel = model.get("page_path") or model.get("path")
    if not page_rel:
        raise jc.JMPPEDError("%s has no page-space model" % character["name"])
    page_abs = _abs(project, page_rel)
    if not os.path.isfile(page_abs):
        raise jc.JMPPEDError("page-space model is missing: %s" % page_abs)
    char_dir = os.path.join(project, character["folder"])
    textured_abs = os.path.join(char_dir, "model_textured.obj")
    mtl_abs = os.path.join(char_dir, "model_textured.mtl")

    backup = _backup(project, character, page_abs, char_dir)
    groups = _read_page_groups(project, character, page_abs)
    if _has_part_material(groups):
        raise jc.JMPPEDError(
            "model.obj contains per-part materials; it is the localized/textured "
            "model, not the page-space model. Restore the page-space model or use "
            "model_textured.obj directly.")
    _write_textured(project, character, groups, textured_abs, mtl_abs)

    model["path"] = _rel(project, textured_abs)
    model["sha256"] = jc.sha256_file(textured_abs)
    model["uv_space"] = UV_SPACE
    model["format"] = FORMAT
    model["page_path"] = _rel(project, page_abs)
    model["page_sha256"] = jc.sha256_file(page_abs)
    model["mtl"] = _rel(project, mtl_abs)
    index = next((i for i, c in enumerate(manifest["characters"])
                  if c.get("name") == character.get("name")
                  or c.get("folder") == character.get("folder")), None)
    if index is None:
        raise jc.JMPPEDError("character %r is not in project.json"
                             % character.get("name", "?"))
    manifest["characters"][index] = character
    jc.save_json(os.path.join(project, "project.json"), manifest)
    jc.save_json(os.path.join(char_dir, "character.json"), character)
    print("wrote %s" % textured_abs)
    print("wrote %s" % mtl_abs)
    print("backed up page model -> %s" % backup)
    print("page model: %s" % page_abs)
    return 0
