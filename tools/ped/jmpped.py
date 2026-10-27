#!/usr/bin/env python3
"""jmpped.py - the pedestrian authoring CLI.

The user-facing verbs are defined in NAMING.md:

    extract         Driver 2 level -> out/source/<level>/
    import          a character folder -> characters/<slug>/
    bake            project -> out/build/JMPPED.LEV
    preview         out/build/JMPPED.LEV -> out/build/preview.gif
    add-character   clone a character and allocate its texture pages
    sync-source     refresh source-owned assets from out/source/<level>/
    migrate         move an old out/jmppeds workspace to this layout
    status          show the project, characters and source export
    rebuild-all     extract, optional import, bake and preview

The engine still calls a selectable character an "entry" and its 14 model
positions "slots"; those names are unchanged in the archive and C API.
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jmpped_workspace as jws


# --------------------------------------------------------------------------
# Verb implementations
# --------------------------------------------------------------------------

def _status(args) -> int:
    project = jws.project_root(args.project)
    source = args.source or jws.source_root()
    st = jws.status(project, source)
    print("project    :", project)
    print("source     :", st["source"],
          "(missing)" if not st["source_exists"] else "")
    print("manifest   :", "yes" if st["manifest"] else "no")
    print("characters :", ", ".join(st["characters"])
          if st["characters"] else "none")
    pending = st.get("model_pending") or []
    print("models pending :", ", ".join(pending) if pending else "none")
    shared = []
    if st["manifest"]:
        import jmpped_entries as je
        manifest = je.load_manifest(project)
        for character in manifest.get("characters", []):
            shared.extend(character.get("shared_textures", []))
    if shared:
        print("shared textures :", ", ".join(
            "s%d_t%d" % (g["page_set"], g["texture"]) for g in shared))
    if st["base"]:
        print("base       :", st["base"],
              "ok" if st["base_ok"] else "MISSING/CHANGED")
    if st["source_exists"]:
        print("source age :", st["source_age_s"], "s; sync:",
              "needed" if st["sync_needed"] else "up to date")
    if st["migrate_required"]:
        print("migrate    : required (run JMPPED.bat migrate)")
    return 0


def _migrate(args) -> int:
    import jmpped_migrate
    return jmpped_migrate.migrate(args.root, args.apply, args.keep_old,
                                  args.level)


def _extract(args) -> int:
    import jmpped_extract
    return jmpped_extract.extract(args.source,
                                  jws.project_root(args.project),
                                  reset=args.reset, level=args.level)


def _import(args) -> int:
    import jmpped_import
    rc = jmpped_import.import_character(
        args.folder, jws.project_root(args.project), name=args.name,
        build=not args.no_build, copy=not args.no_copy, use=args.use,
        character=args.character, scene=args.scene, raw=args.raw)
    if rc:
        return rc
    if getattr(args, "preview", False):
        return _render_preview(args)
    return 0


def _bake(args) -> int:
    import jmpped_bake
    copy = args.copy and not args.no_copy
    rc = jmpped_bake.bake(jws.project_root(args.project), dest=args.dest,
                          dry_run=args.dry_run, use=args.use,
                          character=args.character, copy=copy,
                          force_split=args.force)
    if rc:
        return rc
    if getattr(args, "preview", False):
        return _render_preview(args)
    return 0


def _render_preview(args, root=None) -> int:
    import jmpped_preview
    root = root or jws.project_root(getattr(args, "project", None))
    lev = getattr(args, "lev", None) or os.path.join(
        root, "out", "build", "JMPPED.LEV")
    out = getattr(args, "out", None) or os.path.join(
        root, "out", "build", "preview.gif")
    return jmpped_preview.preview(
        lev, out, character=getattr(args, "character", None),
        frames=getattr(args, "frames", 36),
        size=getattr(args, "size", 512))


def _preview(args) -> int:
    return _render_preview(args)


def _textured(args) -> int:
    import jmpped_textured
    project = jws.project_root(args.project)
    if args.check:
        return jmpped_textured.check_uvs(project, args.character)
    if getattr(args, "combine", False) or getattr(args, "dry_run", False):
        return jmpped_textured.combine_character(
            project, args.character,
            dry_run=getattr(args, "dry_run", False))
    return jmpped_textured.export_textured(project, args.character)


def _add_character(args) -> int:
    import jmpped_add_character
    return jmpped_add_character.add_character(
        jws.project_root(args.project), args.name,
        from_character=args.from_character, slug=args.slug)


def _sync_source(args) -> int:
    import jmpped_sync_source
    return jmpped_sync_source.sync(args.source or jws.source_root(),
                                   jws.project_root(args.project))


def _rebuild_all(args) -> int:
    rc = _extract(args)
    if rc:
        return rc
    if args.folder:
        rc = _import(args)
        if rc:
            return rc
    rc = _bake(args)
    if rc:
        return rc
    return _preview(args)


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def _add_project(p) -> None:
    p.add_argument("--project", default=None,
                   help="project root (default: last-used or this directory)")


def _add_source(p) -> None:
    p.add_argument("--source", default=None,
                   help="source export or Driver 2 level (default: last-used)")


def _add_copy(p) -> None:
    p.add_argument("--copy", action="store_true",
                   help="copy the bake to tools/JMPPED.LEV and the runtime levels")
    p.add_argument("--no-copy", action="store_true",
                   help="never copy, even if --copy is passed")


def _add_build(p) -> None:
    p.add_argument("--dry-run", action="store_true",
                   help="report what would change without writing")
    p.add_argument("--use", choices=("auto", "edit", "raw"), default="auto",
                   help="which model copy the baker reads")
    p.add_argument("--character", default=None,
                   help="character name to bake (default: all)")
    p.add_argument("--dest", default=None,
                   help="archive output (default: out/build/JMPPED.LEV)")
    p.add_argument("--force", action="store_true",
                   help="re-split the combined model even if its hash matches")
    p.add_argument("--preview", action="store_true",
                   help="render out/build/preview.gif after the bake")


def _add_import(p, required: bool = True) -> None:
    p.add_argument("--folder", required=required,
                   help="character folder containing character.json; its "
                        "model.obj is split automatically")
    p.add_argument("--name", default=None, help="override the character name")
    p.add_argument("--scene", default=None,
                   help="combined model OBJ (default: the character's model.obj)")
    p.add_argument("--raw", action="store_true",
                   help="split the model into engine-space parts")
    p.add_argument("--no-build", action="store_true",
                   help="import the character but do not bake")


def _add_preview(p) -> None:
    p.add_argument("--lev", default=None,
                   help="archive to render (default: out/build/JMPPED.LEV)")
    p.add_argument("--out", default=None,
                   help="GIF output (default: out/build/preview.gif)")
    p.add_argument("--frames", type=int, default=36)
    p.add_argument("--size", type=int, default=512)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="jmpped",
        description="Author the JMPPED pedestrian archive")
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")

    mg = sub.add_parser("migrate",
                        help="move an old out/jmppeds workspace to this layout")
    mg.add_argument("--root", default=HERE,
                    help="toolkit/project root (default: this directory)")
    mg.add_argument("--level", default=None,
                    help="source export name (default: from the old source level)")
    mg.add_argument("--apply", action="store_true",
                    help="perform the move; without it this is a dry run")
    mg.add_argument("--keep-old", action="store_true",
                    help="leave the old out/jmppeds tree in place")

    st = sub.add_parser("status", help="show the project, characters and source")
    _add_project(st)
    _add_source(st)

    ex = sub.add_parser("extract", help="extract a Driver 2 level into out/source")
    _add_project(ex)
    _add_source(ex)
    ex.add_argument("--level", default=None,
                    help="source export name (default: the level filename)")
    ex.add_argument("--reset", action="store_true",
                    help="replace an existing source export (backs it up)")

    im = sub.add_parser("import", help="import a character folder")
    _add_project(im)
    _add_build(im)
    _add_import(im)
    _add_copy(im)

    bk = sub.add_parser("bake", help="bake the project into out/build/JMPPED.LEV")
    _add_project(bk)
    _add_build(bk)
    _add_copy(bk)

    pv = sub.add_parser("preview", help="render the baked archive")
    _add_project(pv)
    _add_preview(pv)
    pv.add_argument("--character", default=None,
                    help="archive character to render (default: last/custom)")

    tx = sub.add_parser("textured",
                        help="write or check the per-part textured model")
    _add_project(tx)
    tx.add_argument("--character", default=None,
                    help="character name (default: last/custom)")
    tx.add_argument("--check", action="store_true",
                    help="report UV/crop alignment instead of exporting")
    mode = tx.add_mutually_exclusive_group()
    mode.add_argument("--combine", dest="combine", action="store_true",
                      help="combine parts that share a game texture into one crop")
    mode.add_argument("--no-combine", dest="combine", action="store_false",
                      help="keep per-part crops (current behaviour)")
    tx.set_defaults(combine=False)
    tx.add_argument("--dry-run", action="store_true",
                    help="report the shared texture groups without writing")

    ac = sub.add_parser("add-character",
                        help="clone a character and allocate texture pages")
    _add_project(ac)
    ac.add_argument("--name", required=True, help="display name")
    ac.add_argument("--from-character", default="Jericho",
                    help="source character (default Jericho)")
    ac.add_argument("--slug", default=None,
                    help="folder name (default: from the display name)")

    ss = sub.add_parser("sync-source",
                        help="refresh source-owned assets from out/source")
    _add_project(ss)
    _add_source(ss)

    ra = sub.add_parser("rebuild-all",
                        help="extract, optional import, bake and preview")
    _add_project(ra)
    _add_source(ra)
    _add_build(ra)
    _add_copy(ra)
    _add_import(ra, required=False)
    _add_preview(ra)
    ra.add_argument("--level", default=None,
                    help="source export name (default: the level filename)")
    ra.add_argument("--reset", action="store_true",
                    help="replace an existing source export (backs it up)")
    ra.add_argument("--from-character", default="Jericho")
    ra.add_argument("--slug", default=None)

    args = ap.parse_args(argv)
    if not args.command:
        ap.print_help()
        return 1

    verbs = {
        "migrate": _migrate,
        "status": _status,
        "extract": _extract,
        "import": _import,
        "bake": _bake,
        "preview": _preview,
        "textured": _textured,
        "add-character": _add_character,
        "sync-source": _sync_source,
        "rebuild-all": _rebuild_all,
    }
    try:
        return verbs[args.command](args)
    except Exception as e:
        # The modules define JMPPEDError; importing it here would add a
        # dependency before the error is useful. Print the class name so a
        # refusal is still readable.
        name = type(e).__name__
        if name == "JMPPEDError":
            print("jmpped %s: %s" % (args.command, e), file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
