#!/usr/bin/env python3
"""jmpped_workspace.py - project/source paths, last-used state and backups.

The authoring layout is:

    project.json                 the project manifest
    characters/<slug>/           authored/imported characters
    out/source/<level>/          extracted Driver 2 level (exports only)
    out/build/                   baked archive and preview (builds only)
    .state/                      last-used paths and backups (not an export)

The old layout (out/jmppeds/v3 with jmpped.json and entries/) is refused with a
pointer to `JMPPED.bat migrate`; it is never written by a normal command.
"""

from __future__ import annotations

import os
import shutil
import time

import jmpped_core as jc


HERE = os.path.dirname(os.path.abspath(__file__))
STATE_ROOT = os.path.join(HERE, ".state")
DEFAULT_PROJECT = HERE
LAST_PROJECT = os.path.join(STATE_ROOT, "last_project.txt")
LAST_SOURCE = os.path.join(STATE_ROOT, "last_source.txt")
BACKUP_ROOT = os.path.join(STATE_ROOT, "backups")
OLD_MANIFEST_REL = ("out", "jmppeds", "v3", "jmpped.json")


def old_layout(root: str) -> str | None:
    """Return the old manifest path when the given root still has it."""
    path = os.path.join(os.path.abspath(root), *OLD_MANIFEST_REL)
    return path if os.path.isfile(path) else None


def ensure_project(root: str) -> str:
    """Return project.json, or refuse an old/half-old workspace by name."""
    root = os.path.abspath(root)
    project = os.path.join(root, "project.json")
    if os.path.isfile(project):
        return project
    old = old_layout(root)
    if old:
        raise jc.JMPPEDError(
            "old workspace layout at %s; run JMPPED.bat migrate "
            "(or python jmpped.py migrate --apply)" % old)
    raise jc.JMPPEDError(
        "no project.json in %s; run JMPPED.bat extract first" % root)


def _read_last(path: str, fallback: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            value = f.read().strip()
    except OSError:
        return fallback
    if not value:
        return fallback
    return os.path.abspath(value)


def _write_last(path: str, value: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(os.path.abspath(value) + "\n")


def project_root(explicit: str | None = None) -> str:
    """Selected project root (`--project`/`--out` wins over the last-used file)."""
    if explicit:
        return os.path.abspath(explicit)
    return _read_last(LAST_PROJECT, DEFAULT_PROJECT)


def bake_root(explicit: str | None = None) -> str:
    """Compatibility name for the project root while the tools are renamed."""
    return project_root(explicit)


def source_root(explicit: str | None = None) -> str:
    """Selected source export (`--source` wins over the last-used file)."""
    if explicit:
        return os.path.abspath(explicit)
    last = _read_last(LAST_SOURCE, "")
    if last:
        return last
    root = project_root()
    project = os.path.join(root, "project.json")
    if os.path.isfile(project):
        try:
            data = jc.load_json(project)
            rel = (data.get("source") or {}).get("project")
            if rel:
                return os.path.join(root, rel.replace("/", os.sep))
        except (OSError, ValueError):
            pass
    return os.path.join(root, "out", "source")


def set_project(path: str) -> None:
    _write_last(LAST_PROJECT, path)


def set_bake(path: str) -> None:
    set_project(path)


def set_source(path: str) -> None:
    _write_last(LAST_SOURCE, path)


def backup_workspace(root: str, reason: str) -> str | None:
    """Copy project.json and characters/ before a reset/sync.

    Returns the backup directory, or None when there is no project to protect.
    out/source and out/build are generated and are not copied.
    """
    root = os.path.abspath(root)
    project = os.path.join(root, "project.json")
    if not os.path.isfile(project):
        if old_layout(root):
            raise jc.JMPPEDError(
                "old workspace layout at %s; run JMPPED.bat migrate" % root)
        return None

    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = os.path.basename(root.rstrip("\\/")) or "project"
    dest = os.path.join(BACKUP_ROOT, "%s-%s" % (name, stamp))
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(BACKUP_ROOT, "%s-%s-%d" % (name, stamp, n))
        n += 1

    os.makedirs(dest, exist_ok=True)
    shutil.copy2(project, os.path.join(dest, "project.json"))
    characters = os.path.join(root, "characters")
    if os.path.isdir(characters):
        shutil.copytree(characters, os.path.join(dest, "characters"))
    with open(os.path.join(dest, "README.txt"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write("Pre-%s backup of %s\n" % (reason, root))
        f.write("project.json and characters/ copied; out/source and "
                "out/build are generated and were not copied.\n")
    return dest


def status(root: str, source: str | None = None) -> dict:
    """Report used by the wizard and the status action."""
    root = os.path.abspath(root)
    project_path = os.path.join(root, "project.json")
    out = {
        "workspace": root,
        "exists": os.path.isdir(root),
        "manifest": os.path.isfile(project_path),
        "project": os.path.isfile(project_path),
        "entries": [],
        "characters": [],
        "model_pending": [],
        "base": None,
        "base_ok": None,
        "base_exists": None,
        "source": source or _read_last(LAST_SOURCE, ""),
        "source_exists": False,
        "source_mtime": None,
        "source_age_s": None,
        "sync_needed": None,
        "prime_needed": None,
        "migrate_required": (not os.path.isfile(project_path)
                             and old_layout(root) is not None),
    }
    if not out["project"]:
        return out

    data = jc.load_json(project_path)
    out["characters"] = [c.get("name", "?") for c in data.get("characters", [])]
    out["entries"] = out["characters"]
    try:
        import jmpped_model
        out["model_pending"] = [
            c.get("name", "?") for c in data.get("characters", [])
            if jmpped_model.model_pending(root, c, data)
        ]
    except (OSError, ValueError, jc.JMPPEDError):
        out["model_pending"] = []
    src = data.get("source") or {}
    out["base"] = src.get("base")
    if out["base"]:
        base_path = os.path.join(root, out["base"].replace("/", os.sep))
        out["base_exists"] = os.path.isfile(base_path)
        if out["base_exists"]:
            out["base_ok"] = (jc.sha256_file(base_path) ==
                              src.get("base_sha256"))
    src_path = source or src.get("project") or out["source"]
    if src_path and not os.path.isabs(src_path):
        src_path = os.path.join(root, src_path.replace("/", os.sep))
    out["source"] = src_path
    out["source_exists"] = bool(src_path and os.path.isdir(src_path))
    if out["source_exists"]:
        probe = os.path.join(src_path, "base.LEV")
        if not os.path.isfile(probe):
            probe = src_path
        out["source_mtime"] = os.path.getmtime(probe)
        out["source_age_s"] = int(time.time() - out["source_mtime"])
        out["sync_needed"] = out["source_mtime"] > os.path.getmtime(project_path)
        out["prime_needed"] = out["sync_needed"]
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="Show the selected JMPPED project and source export")
    ap.add_argument("--project", "--bake", dest="project", default=None)
    ap.add_argument("--source", default=None)
    args = ap.parse_args(argv)
    project = project_root(args.project)
    source = source_root(args.source)
    st = status(project, source)
    print("project    :", project)
    print("source     :", source,
          "(missing)" if not st["source_exists"] else "")
    print("manifest   :", "yes" if st["manifest"] else "no")
    print("characters :", ", ".join(st["characters"])
          if st["characters"] else "none")
    if st["base"]:
        print("base       :", st["base"],
              "ok" if st["base_ok"] else "MISSING/CHANGED")
    if st["source_exists"]:
        print("source age :", st["source_age_s"], "s; sync:",
              "needed" if st["sync_needed"] else "up to date")
    if st["migrate_required"]:
        print("migrate    : required (run JMPPED.bat migrate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
