#!/usr/bin/env python3
"""jmpped_bake.py - rebuild JMPPED.LEV from the authoring project.

`project.json` describes every character; a character owns 14 parts, its three
texture page sets and one editable `model.obj`. Untouched entry-0 parts keep the
base archive bytes. A later character is always rebuilt because its materials
name its own page sets.

Run through the CLI:

    python jmpped.py bake --copy
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jmpped_core as jc
import jmpped_archive as ja
import jmpped_models as jm
import jmpped_images as ji
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
import fourbpp                 # the one 4bpp/CLUT conversion (tools/common)
import jmpped_entries as je


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
DEFAULT_PROJECT = HERE


def _abs(root: str, rel: str) -> str:
    return os.path.join(root, rel.replace("/", os.sep))


def _changed(path: str, expected: str) -> bool:
    return os.path.isfile(path) and jc.sha256_file(path) != expected


def _part_transform(part: dict, label: str):
    off = part.get("offset", [0, 0, 0])
    sc = part.get("scale", [1, 1, 1])
    try:
        if len(off) != 3 or len(sc) != 3:
            raise ValueError
        off = tuple(float(v) for v in off)
        sc = tuple(float(v) for v in sc)
    except (TypeError, ValueError):
        raise jc.JMPPEDError("%s: offset/scale must each be three numbers" % label)
    if not all(math.isfinite(v) for v in off + sc):
        raise jc.JMPPEDError("%s: offset/scale contains a non-finite number" % label)
    if any(v == 0 for v in sc):
        raise jc.JMPPEDError("%s: scale must not contain zero" % label)
    return off, sc


def _transform_is_default(transform) -> bool:
    off, sc = transform
    return off == (0.0, 0.0, 0.0) and sc == (1.0, 1.0, 1.0)


def _material(name: str | None, set_map=None):
    if name is None:
        raise jc.JMPPEDError("a face has no material; keep the exported usemtl lines")
    if name.startswith("s") and "_t" in name:
        try:
            set_s, id_s = name[1:].split("_t", 1)
            setid = int(set_s)
            if set_map:
                setid = int(set_map.get(setid, setid))
            return setid, int(id_s), (0x7F, 0x7F, 0x7F)
        except ValueError:
            pass
    if name.startswith("color_"):
        try:
            _, r, g, b = name.split("_")
            return 0, 0, (int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF)
        except ValueError:
            pass
    raise jc.JMPPEDError("unrecognised material %r; expected sSET_tID or color_R_G_B"
                         % name)


def _polys_from_obj(faces, path: str, set_map=None) -> list:
    out = []
    for fi, face in enumerate(faces):
        if len(face) not in (3, 4):
            raise jc.JMPPEDError("%s: face %d has %d corners; only triangles and "
                                 "quads are supported" % (path, fi + 1, len(face)))
        vs = [c[0] for c in face]
        if any(v < 0 for v in vs):
            raise jc.JMPPEDError("%s: face %d has an invalid vertex index"
                                 % (path, fi + 1))
        setid, texid, color = _material(face[0][2], set_map)
        uvs = []
        for _vi, uv, _mat in face:
            if uv is None:
                raise jc.JMPPEDError("%s: face %d has a corner without a UV"
                                     % (path, fi + 1))
            u = int(round(uv[0] * 255.0))
            v = int(round((1.0 - uv[1]) * 255.0))
            uvs.append((max(0, min(255, u)), max(0, min(255, v))))
        out.append(jm.Poly(kind=jc.POLY_FT3 if len(vs) == 3 else jc.POLY_FT4,
                           set=setid, id=texid, v=vs, uv=uvs, color=color))
    if not out:
        raise jc.JMPPEDError("%s has no faces" % path)
    return out


def _load_model(path: str, blender: bool, model: jm.Model,
                page_sets: set[int], label: str,
                offset=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0),
                set_map=None) -> bytes:
    if model.normals or model.point_normals:
        raise jc.JMPPEDError(
            "%s (%s): this model has normal tables; the OBJ path only rebuilds "
            "FT3/FT4 ped models. Leave the part untouched or use a model-aware "
            "tool." % (label, path))
    if any(not p.supported_for_rebuild for p in model.polys):
        raise jc.JMPPEDError(
            "%s (%s): the original model uses a poly kind this toolkit cannot "
            "rebuild; leave the part untouched" % (label, path))
    verts, faces = jm.read_obj(path, blender=blender)
    ox, oy, oz = offset
    sx, sy, sz = scale
    ints = [(int(round(x * sx + ox)),
             int(round(y * sy + oy)),
             int(round(z * sz + oz))) for x, y, z in verts]
    try:
        polys = _polys_from_obj(faces, path, set_map)
    except jc.JMPPEDError as e:
        raise jc.JMPPEDError("%s: %s" % (label, e)) from e
    for p in polys:
        if p.textured and p.set not in page_sets:
            raise jc.JMPPEDError(
                "%s: a poly references texture page %d, which this character "
                "does not carry (pages: %s)"
                % (label, p.set, ", ".join(str(s) for s in sorted(page_sets))))
    return jm.build_model_blob(model, ints, polys)


def _check_page_rect(rect, path: str):
    if len(rect) != 4:
        raise jc.JMPPEDError("%s: crop rect must be [x,y,w,h]" % path)
    x, y, w, h = (int(v) for v in rect)
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > jc.PAGE_W or y + h > jc.PAGE_H:
        raise jc.JMPPEDError("%s: crop rect %r is outside the %dx%d page"
                             % (path, rect, jc.PAGE_W, jc.PAGE_H))
    return x, y, w, h


def _load_page_rgb(path: str, cluts: list[bytes], palette_row: int) -> bytes:
    w, h, pixels = ji.load_png_rgb(path)
    if w != jc.PAGE_W or h != jc.PAGE_H:
        raise jc.JMPPEDError("%s is %dx%d, expected %dx%d"
                             % (path, w, h, jc.PAGE_W, jc.PAGE_H))
    clut = ji.clut_rgb(cluts[palette_row]) if cluts else [(0, 0, 0)] * 16
    return fourbpp.reindex_to_clut(pixels, clut)


def _load_indexed(path: str, width: int, height: int) -> bytes:
    w, h, indices = ji.load_png_indices(path)
    if w != width or h != height:
        raise jc.JMPPEDError("%s is %dx%d, expected %dx%d"
                             % (path, w, h, width, height))
    return indices


def _load_clut_strip(path: str, count: int) -> list[bytes]:
    w, h, pixels = ji.load_png_rgb(path)
    if w != jc.CLUT_ROW_ENTRIES or h != count:
        raise jc.JMPPEDError("%s is %dx%d, expected %dx%d (one row per palette)"
                             % (path, w, h, jc.CLUT_ROW_ENTRIES, count))
    return [ji.rgb_to_clut(pixels[y * w:(y + 1) * w]) for y in range(h)]


def _paletteize_crop(pixels, width: int, height: int):
    """RGB texels -> (16 colours, indices).

    The quantiser itself is tools/common/fourbpp.py: the car tools need the
    same 16-colour reduction when an edited atlas brings new colours, and a
    second copy would drift.
    """
    if len(pixels) != width * height:
        raise jc.JMPPEDError("crop has %d pixels, expected %d"
                             % (len(pixels), width * height))
    return fourbpp.paletteize(pixels, width, height)


def _apply_crop(state: dict, crop: dict, root: str, texture_ids=()) -> int:
    x, y, w, h = _check_page_rect(crop["rect"], crop.get("rgb", "crop"))
    rgb_path = _abs(root, crop["rgb"])
    idx_rel = crop.get("indexed")
    idx_path = _abs(root, idx_rel) if idx_rel else None
    rgb_changed = _changed(rgb_path, crop.get("rgb_sha256", ""))
    idx_changed = (_changed(idx_path, crop.get("indexed_sha256", ""))
                   if idx_path else False)
    if rgb_changed and idx_changed:
        raise jc.JMPPEDError("%s: both the RGB and indexed crop changed; edit one"
                             % os.path.dirname(rgb_path))
    if state["full_page_touched"]:
        if rgb_changed or idx_changed:
            raise jc.JMPPEDError(
                "%s: the full page and a per-part crop both changed; edit one"
                % state["path"])
        return 0
    if not (rgb_changed or idx_changed or state.get("new")):
        return 0

    if idx_changed:
        patch = _load_indexed(idx_path, w, h)
    else:
        _, _, pixels = ji.load_png_rgb(rgb_path)
        colors, patch = _paletteize_crop(pixels, w, h)
        rows = []
        if crop.get("palette_row") is not None:
            rows = [int(crop["palette_row"])]
        elif crop.get("texture") is not None:
            rows = [int(crop["texture"])]
        elif texture_ids:
            rows = sorted({int(t) for t in texture_ids})
        else:
            rows = [int(state["display"])]
        if not state["cluts"]:
            raise jc.JMPPEDError("%s: page has no palette table" % rgb_path)
        for row in rows:
            if row < 0 or row >= len(state["cluts"]):
                raise jc.JMPPEDError(
                    "%s: texture row %d is outside the page's %d palette rows"
                    % (rgb_path, row, len(state["cluts"])))
            state["cluts"][row] = ji.rgb_to_clut(colors)
            state["cluts_touched"] = True

    indices = bytearray(state["indices"])
    for row in range(h):
        dst = (y + row) * jc.PAGE_W + x
        src = row * w
        indices[dst:dst + w] = patch[src:src + w]
    state["indices"] = bytes(indices)
    state["touched"] = True
    return 1


def _page_state(arch: ja.Archive, page: dict, root: str) -> dict:
    page_set = int(page["page_set"])
    index = next((i for i, p in enumerate(arch.pages) if p.set == page_set), None)
    if index is None:
        raise jc.JMPPEDError("page set %d is not in the base archive" % page_set)
    base = arch.pages[index]
    indices = ji.page_indices(base)
    cluts = ji.page_cluts(base)
    display = int(page.get("palette_row", 0))
    if display < 0 or display >= len(cluts):
        display = 0
    png = _abs(root, page["png"]) if page.get("png") else None
    idx = _abs(root, page["indexed_png"])
    pal = _abs(root, page["palette_png"])
    page_changed = _changed(png, page.get("png_sha256", "")) if png else False
    idx_changed = _changed(idx, page["indexed_png_sha256"])
    pal_changed = _changed(pal, page["palette_png_sha256"])
    if page_changed and idx_changed:
        raise jc.JMPPEDError(
            "%s: both the RGB and indexed page changed; edit one of them"
            % os.path.dirname(png or idx))
    if pal_changed:
        cluts = _load_clut_strip(pal, len(cluts))
    if idx_changed:
        indices = _load_indexed(idx, jc.PAGE_W, jc.PAGE_H)
    elif page_changed:
        indices = _load_page_rgb(png, cluts, display)
    return {
        "set": page_set,
        "index": index,
        "page": base,
        "indices": indices,
        "cluts": cluts,
        "display": display,
        "touched": bool(page_changed or idx_changed or pal_changed),
        "full_page_touched": bool(page_changed or idx_changed),
        "cluts_touched": False,
        "path": png,
    }


def _entry_page_state(page: dict, arch: ja.Archive, root: str) -> dict:
    setid = int(page["page_set"])
    base_index = next((i for i, p in enumerate(arch.pages) if p.set == setid), None)
    base_page = arch.pages[base_index] if base_index is not None else None
    base_raw = arch.raw_pages[base_index] if base_index is not None else None
    if page.get("palette_rows_hex"):
        cluts = [bytes.fromhex(h) for h in page["palette_rows_hex"]]
    elif base_page is not None:
        cluts = ji.page_cluts(base_page)
    else:
        cluts = []
    indices = ji.page_indices(base_page) if base_page is not None else None
    display = int(page.get("palette_row", 0))
    if display < 0 or display >= len(cluts):
        display = 0
    png = _abs(root, page["png"]) if page.get("png") else None
    idx = _abs(root, page["indexed_png"])
    pal = _abs(root, page["palette_png"])
    page_changed = _changed(png, page.get("png_sha256", "")) if png else False
    idx_changed = _changed(idx, page["indexed_png_sha256"])
    pal_changed = _changed(pal, page["palette_png_sha256"])
    if page_changed and idx_changed:
        raise jc.JMPPEDError(
            "%s: both the RGB and indexed page changed; edit one of them"
            % os.path.dirname(png or idx))
    if not os.path.isfile(idx):
        raise jc.JMPPEDError("%s: indexed page is missing" % idx)
    if pal_changed:
        cluts = _load_clut_strip(pal, len(cluts))
    if idx_changed:
        indices = _load_indexed(idx, jc.PAGE_W, jc.PAGE_H)
    elif page_changed:
        indices = _load_page_rgb(png, cluts, display)
    elif indices is None:
        indices = _load_indexed(idx, jc.PAGE_W, jc.PAGE_H)
    touched = bool(page_changed or idx_changed or pal_changed or base_index is None)
    return {
        "set": setid,
        "index": base_index,
        "new": base_index is None,
        "page": base_page,
        "raw": base_raw,
        "indices": indices,
        "cluts": cluts,
        "display": display,
        "touched": touched,
        "full_page_touched": bool(page_changed or idx_changed),
        "cluts_touched": False,
        "path": png,
    }


def _set_map(part: dict, character: dict) -> dict[int, int]:
    raw = part.get("texture_page_map") or character.get("texture_page_map") or {}
    return {int(k): int(v) for k, v in raw.items()}


def _runtime_targets(project: str) -> list[str]:
    targets = [os.path.join(project, "JMPPED.LEV")]
    for config in ("Release", "Release_dev"):
        levels = os.path.join(REPO, "src_rebuild", "bin", config,
                              "DRIVER2", "LEVELS")
        if os.path.isdir(os.path.dirname(os.path.dirname(levels))):
            targets.append(os.path.join(levels, "JMPPED.LEV"))
    return targets


def _copy_runtime(project: str, archive: str) -> None:
    for target in _runtime_targets(project):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(archive, target)
        print("copied -> %s" % target)


def _build_character(project: str, arch: ja.Archive, character: dict,
                     index: int, use: str):
    je.validate_character({}, character)
    page_sets = {int(s) for s in character["texture_page_sets"]}
    used_slots: set[int] = set()
    models: list[jm.Model] = []
    raw_models: list[bytes] = []
    changed = 0

    for part in character["parts"]:
        slot = int(part["slot"])
        if slot < 0 or slot >= len(arch.models):
            raise jc.JMPPEDError("%s/%s maps to invalid slot %d"
                                 % (character["name"], part["part"], slot))
        if slot in used_slots:
            raise jc.JMPPEDError("%s: slot %d is claimed more than once"
                                 % (character["name"], slot))
        used_slots.add(slot)
        if part["part"] != arch.model_names[slot]:
            raise jc.JMPPEDError("%s/%s maps to slot %d, whose base model is %s"
                                 % (character["name"], part["part"], slot,
                                    arch.model_names[slot]))

        edit = _abs(project, part["obj_edit"])
        raw = _abs(project, part["obj_raw"])
        edit_changed = _changed(edit, part["obj_edit_sha256"])
        raw_changed = _changed(raw, part["obj_raw_sha256"])
        transform = _part_transform(part, "%s/%s"
                                    % (character["name"], part["part"]))
        transform_changed = not _transform_is_default(transform)
        if use == "edit":
            edit_changed = True
        elif use == "raw":
            raw_changed = True
        if edit_changed and raw_changed and use == "auto":
            raise jc.JMPPEDError(
                "%s/%s: both models/blender and models/engine changed; edit one "
                "of them" % (character["name"], part["part"]))

        force = index > 0
        if force or edit_changed or raw_changed or transform_changed:
            if force:
                path = raw if (use == "raw" or
                               (raw_changed and not edit_changed)) else edit
            elif edit_changed:
                path = edit
            elif raw_changed:
                path = raw
            else:
                path = edit
            blender = path == edit
            blob = _load_model(path, blender, arch.models[slot], page_sets,
                               "%s/%s" % (character["name"], part["part"]),
                               transform[0], transform[1],
                               _set_map(part, character))
            model = jm.parse_model(blob)
            changed += 1
            print("model %-20s rebuilt from %s/%s" %
                  (arch.model_names[slot], character["name"],
                   os.path.basename(path)))
        else:
            blob = arch.raw_models[slot]
            model = arch.models[slot]
        models.append(model)
        raw_models.append(blob)

    if used_slots != set(range(len(jc.MODEL_NAMES))):
        missing = sorted(set(range(len(jc.MODEL_NAMES))) - used_slots)
        raise jc.JMPPEDError("%s: the character does not cover slots %s"
                             % (character["name"], missing))
    return models, raw_models, changed


def _apply_character_crops(character: dict, states: dict[int, dict],
                           project: str) -> int:
    changed = 0
    seen = set()

    def apply(crop: dict, label: str, texture_ids) -> None:
        nonlocal changed
        page_set = int(crop["page_set"])
        if page_set not in states:
            raise jc.JMPPEDError(
                "%s/%s: crop references page set %d, which the character "
                "does not carry"
                % (character["name"], label, page_set))
        key = (page_set,
               tuple(int(v) for v in crop["rect"]),
               crop.get("rgb"),
               int(crop.get("palette_row", -1)))
        if key in seen:
            return
        seen.add(key)
        changed += _apply_crop(states[page_set], crop, project, texture_ids)

    for part in character["parts"]:
        for crop in part.get("crops", []):
            texture_ids = sorted({int(m["id"]) for m in part.get("materials", [])
                                  if int(m["page_set"]) == int(crop["page_set"])})
            apply(crop, part["part"], texture_ids)

    # Extra materials (glasses, accessories) own a crop just like a part:
    # without this loop their edits never reach the page, so a repaint looked
    # like "the bake did not run".
    for extra in character.get("extra_materials", []):
        texture_ids = ()
        if extra.get("texture") is not None:
            texture_ids = (int(extra["texture"]),)
        apply(extra, extra.get("material") or extra.get("part") or "extra",
              texture_ids)

    return changed


def build_v5(project: str, manifest: dict, dest: str | None,
             dry_run: bool, use: str, character: str | None,
             copy: bool) -> int:
    base_path = _abs(project, manifest["source"]["base"])
    if not os.path.isfile(base_path):
        raise jc.JMPPEDError("base archive %s is missing" % base_path)
    if jc.sha256_file(base_path) != manifest["source"]["base_sha256"]:
        raise jc.JMPPEDError("base archive hash does not match project.json")
    arch = ja.read_archive(base_path)
    if arch.version != jc.VERSION_V3 or len(arch.models) != 14:
        raise jc.JMPPEDError("base archive is v%d with %d models; expected v3/14"
                             % (arch.version, len(arch.models)))
    if character:
        je.character(manifest, character)

    characters = manifest["characters"]
    models_by_character = []
    states: dict[int, dict] = {}
    changed_models = 0
    for index, c in enumerate(characters):
        models, raw_models, changed = _build_character(project, arch, c,
                                                       index, use)
        models_by_character.append((models, raw_models))
        changed_models += changed
        for page in c["pages"]:
            st = _entry_page_state(page, arch, project)
            if st["set"] in states:
                raise jc.JMPPEDError("page set %d appears in more than one "
                                     "character" % st["set"])
            states[st["set"]] = st

    changed_crops = 0
    for c in characters:
        changed_crops += _apply_character_crops(c, states, project)

    changed_pages = 0
    out_entries = []
    for c, (models, raw_models) in zip(characters, models_by_character):
        pages = []
        raw_pages = []
        for page in c["pages"]:
            st = states[int(page["page_set"])]
            if st["touched"] or st["index"] is None:
                pages.append(ji.make_raw_page(st["set"], st["indices"],
                                              st["cluts"]))
                raw_pages.append(b"")
                changed_pages += 1
            else:
                pages.append(st["page"])
                raw_pages.append(st["raw"] or b"")
        out_entries.append(ja.Entry(c["name"], models, pages, raw_models,
                                    raw_pages))

    arch5 = ja.Archive(version=jc.VERSION_V5, model_names=jc.MODEL_NAMES,
                       models=out_entries[0].models, pages=out_entries[0].pages,
                       raw_models=out_entries[0].raw_models,
                       raw_pages=out_entries[0].raw_pages,
                       entries=out_entries)
    data = ja.archive_bytes(arch5)

    expected = None
    base_v5 = manifest["source"].get("base_v5")
    if base_v5 and len(out_entries) == 1:
        p = _abs(project, base_v5)
        if os.path.isfile(p):
            expected = open(p, "rb").read()
    unchanged = expected is not None and data == expected
    if dry_run:
        print("dry run: %d characters, %d model(s), %d page(s); %d bytes" %
              (len(out_entries), changed_models, changed_pages, len(data)))
        return 0

    dest = dest or os.path.join(project, "out", "build", "JMPPED.LEV")
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    print("wrote %s (%d bytes, %d characters, %s)" %
          (dest, len(data), len(out_entries),
           "byte-identical to base" if unchanged else
           "%d model(s), %d page(s), %d crop(s) changed" %
           (changed_models, changed_pages, changed_crops)))

    check = ja.read_archive(dest)
    if check.version != jc.VERSION_V5 or len(check.entries) != len(out_entries):
        raise jc.JMPPEDError("self-check failed: reread archive is v%d/%d "
                             "characters" % (check.version, len(check.entries)))
    if ja.archive_bytes(check) != data:
        raise jc.JMPPEDError("self-check failed: reread archive does not "
                             "re-serialize")
    if copy:
        _copy_runtime(project, dest)
    return 0


def build_v3(project: str, manifest: dict, dest: str | None,
             dry_run: bool, use: str, character: str | None,
             copy: bool) -> int:
    if len(manifest["characters"]) != 1:
        raise jc.JMPPEDError("a v3 project must have exactly one character; "
                             "this project has %d" % len(manifest["characters"]))
    base_path = _abs(project, manifest["source"]["base"])
    if not os.path.isfile(base_path):
        raise jc.JMPPEDError("base archive %s is missing" % base_path)
    if jc.sha256_file(base_path) != manifest["source"]["base_sha256"]:
        raise jc.JMPPEDError("base archive hash does not match project.json")
    arch = ja.read_archive(base_path)
    if arch.version != jc.VERSION_V3 or len(arch.models) != 14:
        raise jc.JMPPEDError("base archive is v%d with %d models; expected v3/14"
                             % (arch.version, len(arch.models)))
    c = manifest["characters"][0]
    models, raw_models, changed_models = _build_character(project, arch, c,
                                                         0, use)
    arch.models = models
    arch.raw_models = raw_models
    states = {int(p["page_set"]): _page_state(arch, p, project)
              for p in c["pages"]}
    changed_crops = _apply_character_crops(c, states, project)
    changed_pages = 0
    for st in states.values():
        if not st["touched"]:
            continue
        arch.pages[st["index"]] = ji.make_raw_page(st["set"], st["indices"],
                                                   st["cluts"])
        arch.raw_pages[st["index"]] = b""
        changed_pages += 1
        print("page set %3d rebuilt as a raw page" % st["set"])

    data = ja.archive_bytes(arch)
    original = open(base_path, "rb").read()
    if dry_run:
        print("dry run: %d model(s), %d page(s); %d bytes" %
              (changed_models, changed_pages, len(data)))
        return 0
    dest = dest or os.path.join(project, "out", "build", "JMPPED.LEV")
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    print("wrote %s (%d bytes, %s)" %
          (dest, len(data), "byte-identical to base" if data == original else
           "%d model(s), %d page(s), %d crop(s) changed" %
           (changed_models, changed_pages, changed_crops)))
    check = ja.read_archive(dest)
    if check.version != jc.VERSION_V3 or len(check.models) != 14:
        raise jc.JMPPEDError("self-check failed: reread archive is v%d/%d models"
                             % (check.version, len(check.models)))
    if ja.archive_bytes(check) != data:
        raise jc.JMPPEDError("self-check failed: reread archive does not "
                             "re-serialize")
    if copy:
        _copy_runtime(project, dest)
    return 0


def bake(project: str, dest: str | None = None, dry_run: bool = False,
         use: str = "auto", character: str | None = None,
         copy: bool = False, force_split: bool = False) -> int:
    project = os.path.abspath(project)
    import jmpped_model
    jmpped_model.split_pending(project, force=force_split)
    manifest = je.load_manifest(project)
    fmt = manifest.get("format_version")
    if fmt == jc.VERSION_V5:
        return build_v5(project, manifest, dest, dry_run, use, character, copy)
    if fmt == jc.VERSION_V3:
        return build_v3(project, manifest, dest, dry_run, use, character, copy)
    raise jc.JMPPEDError("unsupported format version %r" % fmt)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Bake the authoring project into JMPPED.LEV")
    ap.add_argument("--project", default=DEFAULT_PROJECT,
                    help="project root containing project.json")
    ap.add_argument("--dest", default=None,
                    help="archive output (default: out/build/JMPPED.LEV)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--use", choices=("auto", "edit", "raw"), default="auto")
    ap.add_argument("--character", default=None,
                    help="character name (validated; default: all)")
    ap.add_argument("--copy", action="store_true",
                    help="copy the bake to tools/ and the runtime levels")
    ap.add_argument("--force", action="store_true",
                    help="re-split the combined model even if its hash matches")
    args = ap.parse_args(argv)
    try:
        return bake(args.project, args.dest, args.dry_run, args.use,
                    args.character, args.copy, args.force)
    except jc.JMPPEDError as e:
        print("jmpped bake: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
