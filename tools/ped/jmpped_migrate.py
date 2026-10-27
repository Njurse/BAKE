#!/usr/bin/env python3
"""jmpped_migrate.py - move an old out/jmppeds workspace to the authoring layout.

The old workspace is one tree:

    out/jmppeds/
      v3/jmpped.json
      v3/base.LEV, base_v5.LEV
      v3/tanner/, jericho/, skeleton/
      v3/entries/<slug>/
      v3/jmpped/, v3/preview_360.gif, v3/JMPPED.LEV

The new layout is described in NAMING.md:

    project.json
    characters/<slug>/
    out/source/<level>/
    out/build/
    .state/

The migration is a pure move: it never re-hashes a file. Every `*_sha256` in
the manifest is a change detector, so it is carried over verbatim even though
the path beside it moves. The edited model is copied byte-for-byte.

Run from the toolkit directory:

    python jmpped.py migrate              # dry run, prints the plan
    python jmpped.py migrate --apply      # back up, then move
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OLD_REL = "out/jmppeds"
OLD_WORK_REL = "out/jmppeds/v3"


class MigrationError(Exception):
    pass


# --------------------------------------------------------------------------
# Paths and JSON
# --------------------------------------------------------------------------

def _norm(path: str) -> str:
    return (path or "").replace("\\", "/")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1)
        f.write("\n")


def _level_slug(source_level: str) -> str:
    name = os.path.splitext(os.path.basename(_norm(source_level)))[0]
    return name.strip().lower() or "level"


def _slug(name: str) -> str:
    keep = "".join(c.lower() if c.isalnum() else "-" for c in name.strip())
    while "--" in keep:
        keep = keep.replace("--", "-")
    return keep.strip("-")


def _under(path: str, old_prefix: str) -> str | None:
    path = _norm(path)
    prefix = old_prefix.rstrip("/") + "/"
    if not path.startswith(prefix):
        return None
    return path[len(prefix):]


def _subpath(rest: str) -> str | None:
    """Rename the insides of one character/source folder."""
    if rest.startswith("png/pages/"):
        return "textures/pages/" + rest[len("png/pages/"):]
    if rest.startswith("png/palettes/"):
        return "textures/palettes/" + rest[len("png/palettes/"):]
    if rest.startswith("png/parts/"):
        return "textures/crops/" + rest[len("png/parts/"):]
    if rest.startswith("obj/raw/"):
        return "models/engine/" + rest[len("obj/raw/"):]
    if rest.startswith("obj/edit/"):
        return "models/blender/" + rest[len("obj/edit/"):]
    if rest == "obj/bindpose_edit.obj":
        return "model.obj"
    if rest == "obj/bindpose_raw.obj":
        return "models/engine_reference.obj"
    if rest in ("obj/assembled_edit.obj", "obj/assembled_raw.obj"):
        return None
    if rest == "skeleton_raw.obj":
        return "skeleton_engine.obj"
    if rest == "skeleton_edit.obj":
        return "skeleton_blender.obj"
    if rest == "ped.json":
        return "source.json"
    return rest


def _map(path: str, old_prefix: str, new_prefix: str) -> str | None:
    rest = _under(path, old_prefix)
    if rest is None:
        raise MigrationError("%r is not under %r" % (path, old_prefix))
    rest = _subpath(rest)
    if rest is None:
        return None
    return new_prefix.rstrip("/") + "/" + rest


def _map_optional(path, old_prefix, new_prefix):
    if not path:
        return path
    return _map(path, old_prefix, new_prefix)


# --------------------------------------------------------------------------
# Record transforms
# --------------------------------------------------------------------------

def _map_page(rec: dict, mapper) -> dict:
    q = dict(rec)
    q["page_set"] = q.pop("set")
    q["source_character"] = q.pop("ped")
    q["palette_rows"] = q.pop("npalettes", 0)
    q["palette_row"] = q.pop("display_clut", 0)
    q["palette_rows_hex"] = q.pop("cluts_hex", [])
    for key in ("png", "indexed_png", "palette_png"):
        if q.get(key):
            q[key] = mapper(q[key])
    for crop in q.get("texture_pages", []):
        if crop.get("png"):
            crop["png"] = mapper(crop["png"])
    return q


def _map_materials(materials):
    out = []
    for m in materials or []:
        q = dict(m)
        if "set" in q:
            q["page_set"] = q.pop("set")
        out.append(q)
    return out


def _map_crops(crops, mapper):
    out = []
    for c in crops or []:
        q = dict(c)
        if "set" in q:
            q["page_set"] = q.pop("set")
        if "display_clut" in q:
            q["palette_row"] = q.pop("display_clut")
        if "cluts_hex" in q:
            q["palette_rows_hex"] = q.pop("cluts_hex")
        if q.get("rgb"):
            q["rgb"] = mapper(q["rgb"])
        out.append(q)
    return out


def _map_part(rec: dict, mapper, *, entry: bool) -> dict:
    q = dict(rec)
    if entry and "ped" in q:
        q["source_model"] = q.pop("ped")
    if "page_sets" in q:
        q["texture_page_sets"] = q.pop("page_sets")
    for key in ("obj_raw", "obj_edit"):
        if q.get(key):
            q[key] = mapper(q[key])
    q["materials"] = _map_materials(q.get("materials"))
    q["crops"] = _map_crops(q.get("crops"), mapper)
    return q


def _map_palette_crops(crops, mapper):
    out = []
    for c in crops or []:
        q = dict(c)
        if "set" in q:
            q["page_set"] = q.pop("set")
        if "display_clut" in q:
            q["palette_row"] = q.pop("display_clut")
        if "cluts_hex" in q:
            q["palette_rows_hex"] = q.pop("cluts_hex")
        if q.get("palette_png"):
            q["palette_png"] = mapper(q["palette_png"])
        out.append(q)
    return out


class _EntryMapper:
    """Map every path an entry references into its one character folder.

    Entry 0 also references Tanner's page set 2 and Tanner limbs, so the entry
    directory is not the only old prefix. Anything under a source model is
    copied into the character folder by the caller.
    """

    def __init__(self, old_dir: str, source_names, new_dir: str):
        self.old_prefixes = [old_dir] + [n for n in source_names if n != old_dir]
        self.new_dir = new_dir
        self.links: list[tuple[str, str]] = []

    def __call__(self, path: str) -> str | None:
        for old in self.old_prefixes:
            if _under(path, old) is not None:
                new = _map(path, old, self.new_dir)
                if new:
                    self.links.append((path, new))
                return new
        raise MigrationError("%r is not under any of %s"
                             % (path, self.old_prefixes))


def _map_entry(e: dict, source_names) -> tuple[dict, str, str, list]:
    old_dir = _norm(e["directory"])
    slug = os.path.basename(old_dir.rstrip("/"))
    new_dir = "characters/" + slug
    mapper = _EntryMapper(old_dir, source_names, new_dir)
    q = {
        "index": e.get("index"),
        "name": e["name"],
        "folder": new_dir,
        "source_character": e.get("source_ped"),
        "skeleton": e.get("variant"),
        "texture_page_sets": list(e.get("page_sets", [])),
        "pages": [_map_page(p, mapper) for p in e.get("pages", [])],
        "parts": [_map_part(s, mapper, entry=True)
                  for s in e.get("slots", [])],
        "texture_crops": _map_palette_crops(e.get("palette_crops"), mapper),
    }
    if e.get("page_set_map"):
        q["texture_page_map"] = dict(e["page_set_map"])
    bp = e.get("bindpose") or {}
    if bp.get("edit"):
        q["model"] = {
            "path": mapper(bp["edit"]),
            "sha256": bp.get("edit_sha256"),
        }
    if bp.get("raw"):
        q["engine_reference"] = {
            "path": mapper(bp["raw"]),
            "sha256": bp.get("raw_sha256"),
        }
    return q, old_dir, new_dir, mapper.links


def _map_source_model(name: str, rec: dict, level: str) -> dict:
    old_prefix = name
    new_prefix = "out/source/%s/source_models/%s" % (level, name)
    mapper = lambda path: _map(path, old_prefix, new_prefix)
    q = {
        "source_model": name,
        "label": rec.get("label"),
        "texture_page_sets": list(rec.get("pages", [])),
        "parts": [_map_part(p, mapper, entry=False)
                  for p in rec.get("parts", [])],
        "skeleton": "out/source/%s/skeleton/skeleton.json" % level,
    }
    if rec.get("note"):
        q["note"] = rec["note"]
    bp = rec.get("bindpose") or {}
    if bp.get("edit"):
        q["model"] = {
            "path": mapper(bp["edit"]),
            "sha256": bp.get("edit_sha256"),
        }
    if bp.get("raw"):
        q["engine_reference"] = {
            "path": mapper(bp["raw"]),
            "sha256": bp.get("raw_sha256"),
        }
    return q


def _map_skeleton(rec: dict, level: str) -> dict:
    prefix = "out/source/%s/skeleton" % level
    return {
        "json": prefix + "/skeleton.json",
        "json_sha256": rec.get("json_sha256"),
        "engine": prefix + "/skeleton_engine.obj",
        "engine_sha256": rec.get("raw_sha256"),
        "blender": prefix + "/skeleton_blender.obj",
        "blender_sha256": rec.get("edit_sha256"),
    }


def _top_page_prefix(rec: dict, level: str) -> tuple[str, str]:
    path = _norm(rec.get("png") or rec.get("indexed_png") or "")
    if path.startswith("entries/"):
        slug = path.split("/", 2)[1]
        return "entries/" + slug, "characters/" + slug
    if path.startswith("tanner/"):
        return "tanner", "out/source/%s/source_models/tanner" % level
    if path.startswith("jericho/"):
        return "jericho", "out/source/%s/source_models/jericho" % level
    ped = _norm(rec.get("ped", ""))
    if ped in ("tanner", "jericho"):
        return ped, "out/source/%s/source_models/%s" % (level, ped)
    raise MigrationError("cannot place page set %r (%r)" % (rec.get("set"), path))


def _map_top_page(rec: dict, level: str) -> dict:
    old_prefix, new_prefix = _top_page_prefix(rec, level)
    mapper = lambda path: _map(path, old_prefix, new_prefix)
    return _map_page(rec, mapper)


def _project_data(old: dict, level: str) -> tuple[dict, list[tuple[dict, str, str]]]:
    source = {
        "level": old.get("source_level"),
        "sha256": old.get("source_sha256"),
        "project": "out/source/%s" % level,
        "base": "out/source/%s/base.LEV" % level,
        "base_sha256": old.get("base_archive_sha256"),
        "base_v5": "out/source/%s/base_v5.LEV" % level,
        "base_v5_sha256": old.get("base_v5_archive_sha256"),
        "skeleton": _map_skeleton(old.get("skeleton", {}), level),
    }
    source_models = {
        name: _map_source_model(name, rec, level)
        for name, rec in old.get("peds", {}).items()
    }
    source_names = list(old.get("peds", {}).keys())
    entries = [_map_entry(e, source_names) for e in old.get("entries", [])]
    characters = [e[0] for e in entries]
    pages = [_map_top_page(p, level) for p in old.get("pages", [])]
    build = {
        "parts": [
            {"slot": s.get("slot"), "source_model": s.get("ped"),
             "part": s.get("part")}
            for s in old.get("bake", {}).get("slots", [])
        ]
    }
    project = {
        "project_version": 2,
        "tool": "jmpped.py",
        "migrated_from": "out/jmppeds/v3",
        "source": source,
        "format_version": old.get("archive_version"),
        "source_models": source_models,
        "characters": characters,
        "pages": pages,
        "build": build,
    }
    return project, entries


# --------------------------------------------------------------------------
# Hash audit
# --------------------------------------------------------------------------

def _hashes(data) -> list[str]:
    out: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key.endswith("_sha256") and isinstance(value, str):
                out.append(value)
            else:
                out.extend(_hashes(value))
    elif isinstance(data, list):
        for item in data:
            out.extend(_hashes(item))
    return out


def _hash_audit(old: dict, project: dict) -> None:
    old_hashes = _hashes(old)
    new_hashes = _hashes(project)
    missing = list(new_hashes)
    for value in old_hashes:
        if value in missing:
            missing.remove(value)
    if missing:
        raise MigrationError(
            "the new project contains %d hash(es) that were not in the old "
            "manifest; hashes must be carried over verbatim" % len(missing))


# --------------------------------------------------------------------------
# Filesystem
# --------------------------------------------------------------------------

def _move(src: str, dst: str) -> None:
    if not os.path.exists(src):
        raise MigrationError("missing %s" % src)
    if os.path.exists(dst):
        raise MigrationError("refusing to overwrite %s" % dst)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    shutil.move(src, dst)


def _copy_tree(src: str, dst: str) -> None:
    if not os.path.isdir(src):
        raise MigrationError("missing %s" % src)
    if os.path.exists(dst):
        raise MigrationError("refusing to overwrite %s" % dst)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    shutil.copytree(src, dst)


def _merge_move(src: str, dst: str) -> None:
    if not os.path.isdir(src):
        return
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.exists(d):
            raise MigrationError("refusing to overwrite %s" % d)
        shutil.move(s, d)
    try:
        os.rmdir(src)
    except OSError:
        pass


def _restructure(folder: str) -> None:
    """Rename obj/ and png/ inside one character or source-model folder."""
    obj = os.path.join(folder, "obj")
    png = os.path.join(folder, "png")
    models = os.path.join(folder, "models")
    textures = os.path.join(folder, "textures")

    if os.path.isdir(obj):
        if os.path.isdir(os.path.join(obj, "raw")):
            _move(os.path.join(obj, "raw"), os.path.join(models, "engine"))
        if os.path.isdir(os.path.join(obj, "edit")):
            _move(os.path.join(obj, "edit"), os.path.join(models, "blender"))
        for old, new in (("bindpose_edit.obj", "model.obj"),
                         ("bindpose_raw.obj", "models/engine_reference.obj")):
            src = os.path.join(obj, old)
            if os.path.isfile(src):
                _move(src, os.path.join(folder, new))
        # The OBJ's mtllib line is relative to the OBJ, and the model bytes must
        # not be edited, so keep the MTL beside its new OBJ name.
        for old, new in (("bindpose_edit.mtl", "bindpose_edit.mtl"),
                         ("bindpose_raw.mtl", "models/bindpose_raw.mtl")):
            src = os.path.join(obj, old)
            if os.path.isfile(src):
                _move(src, os.path.join(folder, new))
        for name in ("assembled_edit.obj", "assembled_raw.obj"):
            src = os.path.join(obj, name)
            if os.path.isfile(src):
                os.remove(src)
        try:
            os.rmdir(obj)
        except OSError:
            pass

    if os.path.isdir(png):
        for old, new in (("pages", "pages"), ("palettes", "palettes"),
                         ("parts", "crops")):
            src = os.path.join(png, old)
            if os.path.isdir(src):
                _move(src, os.path.join(textures, new))
        try:
            os.rmdir(png)
        except OSError:
            pass

    # The shared skeleton folder is passed to this helper directly, so its
    # files sit at the folder root rather than under a skeleton/ child.
    for old, new in (("skeleton_raw.obj", "skeleton_engine.obj"),
                     ("skeleton_edit.obj", "skeleton_blender.obj")):
        src = os.path.join(folder, old)
        if os.path.isfile(src):
            os.rename(src, os.path.join(folder, new))
        src = os.path.join(folder, "skeleton", old)
        if os.path.isfile(src):
            os.rename(src, os.path.join(folder, "skeleton", new))


def _backup(root: str, stamp: str) -> str:
    old_root = os.path.join(root, OLD_REL)
    dest = os.path.join(root, ".state", "backups",
                        "pre-rename-%s" % stamp)
    n = 1
    base = dest
    while os.path.exists(dest):
        dest = "%s-%d" % (base, n)
        n += 1
    os.makedirs(dest)
    shutil.copytree(old_root, os.path.join(dest, "jmppeds"))
    with open(os.path.join(dest, "README.txt"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write("Pre-rename backup of %s\n" % old_root)
        f.write("Restore by moving jmppeds/ back to out/jmppeds and deleting "
                "project.json, characters/, and the new out/source|build trees.\n")
    return dest


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------

def _check_layout(root: str) -> tuple[str, str]:
    old_root = os.path.join(root, OLD_REL)
    old_work = os.path.join(root, OLD_WORK_REL)
    manifest = os.path.join(old_work, "jmpped.json")
    project = os.path.join(root, "project.json")
    if os.path.isfile(project):
        if os.path.isdir(old_root):
            raise MigrationError(
                "both project.json and %s exist; resolve the partial migration "
                "before running this again" % OLD_REL)
        raise MigrationError("already migrated: %s exists" % project)
    if not os.path.isdir(old_root):
        raise MigrationError("no old workspace at %s" % old_root)
    if not os.path.isfile(manifest):
        raise MigrationError("old workspace has no jmpped.json at %s" % manifest)
    for name in ("characters", os.path.join("out", "source"),
                 os.path.join("out", "build")):
        if os.path.exists(os.path.join(root, name)):
            raise MigrationError(
                "%s already exists; move it away before migrating" % name)
    return old_root, old_work


def migrate(root: str, apply: bool, keep_old: bool = False,
            level: str | None = None) -> int:
    root = os.path.abspath(root)
    old_root, old_work = _check_layout(root)
    old = _load(os.path.join(old_work, "jmpped.json"))
    level = level or _level_slug(old.get("source_level", "level"))
    project, entries = _project_data(old, level)
    _hash_audit(old, project)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(root, ".state", "backups", "pre-rename-%s" % stamp)
    source_root = os.path.join(root, "out", "source", level)
    build_root = os.path.join(root, "out", "build")

    model_hashes: dict[str, str] = {}
    for e in old.get("entries", []):
        bp = e.get("bindpose") or {}
        if bp.get("edit"):
            model_hashes[bp["edit"]] = _sha256(
                os.path.join(old_work, bp["edit"].replace("/", os.sep)))
    for name, rec in old.get("peds", {}).items():
        bp = rec.get("bindpose") or {}
        if bp.get("edit"):
            model_hashes[bp["edit"]] = _sha256(
                os.path.join(old_work, bp["edit"].replace("/", os.sep)))

    print("migrate %s" % old_work)
    print("  level      : %s" % level)
    print("  backup     : %s" % backup)
    print("  characters : %d" % len(entries))
    print("  source     : %s" % source_root)
    print("  build      : %s" % build_root)
    print("  edited models preserved: %d" % len(model_hashes))
    for e, old_dir, new_dir, _links in entries:
        print("  %-18s %s -> %s" % (e["name"], old_dir, new_dir))
    if not apply:
        print("dry run: nothing moved; pass --apply to migrate")
        return 0

    made_backup = _backup(root, stamp)
    print("backed up -> %s" % made_backup)

    # Source-owned exports
    os.makedirs(source_root, exist_ok=True)
    _move(os.path.join(old_work, "base.LEV"),
          os.path.join(source_root, "base.LEV"))
    _move(os.path.join(old_work, "base_v5.LEV"),
          os.path.join(source_root, "base_v5.LEV"))
    _move(os.path.join(old_work, "skeleton"),
          os.path.join(source_root, "skeleton"))
    _restructure(os.path.join(source_root, "skeleton"))
    _move(os.path.join(old_work, "tanner"),
          os.path.join(source_root, "source_models", "tanner"))
    _restructure(os.path.join(source_root, "source_models", "tanner"))
    _move(os.path.join(old_work, "jericho"),
          os.path.join(source_root, "source_models", "jericho"))
    _restructure(os.path.join(source_root, "source_models", "jericho"))
    _copy_tree(os.path.join(source_root, "source_models", "jericho"),
               os.path.join(root, "characters", "jericho"))
    _restructure(os.path.join(root, "characters", "jericho"))

    # Imported/authored characters. A fresh source export has no custom
    # entries, so only move one when the staged workspace actually has it.
    os.makedirs(os.path.join(root, "characters"), exist_ok=True)
    custom_old = os.path.join(old_work, "entries", "jerichod3")
    if os.path.isdir(custom_old):
        _move(custom_old, os.path.join(root, "characters", "jerichod3"))
        _restructure(os.path.join(root, "characters", "jerichod3"))

    # Entry 0 also uses Tanner's page set 2 and Tanner limbs. Copy every
    # source-owned file an entry references into that character folder, so a
    # character owns all of its model, texture and crop paths.
    for entry, old_dir, _new_dir, links in entries:
        for old_path, new_path in links:
            if _under(old_path, old_dir) is not None:
                continue
            source_name = next((n for n in project["source_models"]
                                if _under(old_path, n) is not None), None)
            if source_name is None:
                continue
            src_rel = _map(old_path, source_name,
                           "out/source/%s/source_models/%s"
                           % (level, source_name))
            if src_rel is None:
                continue
            src = os.path.join(root, src_rel.replace("/", os.sep))
            dst = os.path.join(root, new_path.replace("/", os.sep))
            if os.path.isfile(dst):
                continue
            if not os.path.isfile(src):
                raise MigrationError("missing source file %s (entry %s)"
                                     % (src, entry["name"]))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)

    # Build output and generated state
    os.makedirs(build_root, exist_ok=True)
    for old_name, new_name in (("JMPPED.LEV", "JMPPED.LEV"),
                               ("preview_360.gif", "preview.gif")):
        src = os.path.join(old_work, old_name)
        if os.path.isfile(src):
            _move(src, os.path.join(build_root, new_name))
    _merge_move(os.path.join(old_root, "backups"),
                os.path.join(root, ".state", "backups"))
    for old_name, new_name in (("last_workspace.txt", "last_project.txt"),
                               ("last_source.txt", "last_source.txt")):
        src = os.path.join(old_root, old_name)
        if os.path.isfile(src):
            _move(src, os.path.join(root, ".state", new_name))

    # The old assembled reference and the duplicate legacy jerichod3 copy are
    # already in the pre-rename backup.
    for rel in ("v3/jmpped", "v3/jerichod3"):
        path = os.path.join(old_root, rel)
        if os.path.isdir(path):
            shutil.rmtree(path)

    # Write the new manifests
    project_path = os.path.join(root, "project.json")
    _save(project_path, project)
    for entry, _old_dir, new_dir, _links in entries:
        char_dir = os.path.join(root, new_dir)
        _save(os.path.join(char_dir, "character.json"), entry)
        ped = os.path.join(char_dir, "ped.json")
        if os.path.isfile(ped):
            os.remove(ped)
    for name, rec in project["source_models"].items():
        src_dir = os.path.join(source_root, "source_models", name)
        _save(os.path.join(src_dir, "source.json"), rec)
        ped = os.path.join(src_dir, "ped.json")
        if os.path.isfile(ped):
            os.remove(ped)

    os.makedirs(os.path.join(root, ".state"), exist_ok=True)
    with open(os.path.join(root, ".state", "last_project.txt"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write(root + "\n")
    with open(os.path.join(root, ".state", "last_source.txt"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write(source_root + "\n")

    if not keep_old:
        shutil.rmtree(old_root)
    else:
        print("kept old workspace at %s" % old_root)

    # Verify the move: project loads, every character model is still the same
    # bytes, and no recorded hash was invented or lost.
    check = _load(project_path)
    _hash_audit(old, check)
    for entry, _old_dir, new_dir, _links in entries:
        model = (entry.get("model") or {}).get("path")
        if not model:
            continue
        path = os.path.join(root, model.replace("/", os.sep))
        if not os.path.isfile(path):
            raise MigrationError("missing migrated model %s" % path)
        old_bp = None
        for e in old.get("entries", []):
            if e.get("name") == entry.get("name"):
                old_bp = (e.get("bindpose") or {}).get("edit")
                break
        if old_bp and _sha256(path) != model_hashes.get(old_bp):
            raise MigrationError("model bytes changed: %s" % path)
    for name, rec in project["source_models"].items():
        model = (rec.get("model") or {}).get("path")
        if not model:
            continue
        path = os.path.join(root, model.replace("/", os.sep))
        if not os.path.isfile(path):
            raise MigrationError("missing migrated source model %s" % path)
        old_bp = (old.get("peds", {}).get(name, {}).get("bindpose") or {}).get("edit")
        if old_bp and _sha256(path) != model_hashes.get(old_bp):
            raise MigrationError("source model bytes changed: %s" % path)
    print("migrated -> %s (%d characters, %d source models)" %
          (root, len(entries), len(project["source_models"])))
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Move an old JMPPED workspace")
    ap.add_argument("--root", default=HERE,
                    help="toolkit/workspace root (default: this directory)")
    ap.add_argument("--level", default=None,
                    help="source export name (default: from the old source level)")
    ap.add_argument("--apply", action="store_true",
                    help="perform the move; without it this is a dry run")
    ap.add_argument("--keep-old", action="store_true",
                    help="leave the old out/jmppeds tree in place")
    args = ap.parse_args(argv)
    try:
        return migrate(args.root, args.apply, args.keep_old, args.level)
    except MigrationError as e:
        print("jmpped migrate: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
