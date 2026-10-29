#!/usr/bin/env python3
"""carbank_bake.py - bake an edited bank back into a Driver 2-shaped .LEV.

    python carbank_bake.py banks/firebird/bank.json -o banks/firebird/newbank
    python carbank_bake.py banks/firebird/bank.json -o ... --variants clean,damaged

The bank is a DRIVER 2 car-data bank (LUMP_CAR_MODELS 28, LUMP_PALLET 25,
LUMP_TEXTUREINFO 34), so it is assembled by exportd2.build_bank - the writer that
already reproduces FIREBIRD.LEV byte for byte. The Driver 1 pool assembler
(bake.build_file) is a different shape and is reachable only through --d1, where
the input is explicitly a Driver 1 LEV.

What comes from the declaration (bank.json's `declare` half):

  * each car's clean/damaged/low OBJ, read back with its real texture materials
  * the slot order
  * the wheel variants that name a page: each becomes a wheel MODEL in the bank
    (LUMP_CAR_WHEELS, type 200). The engine keeps the host level's wheel MESH and
    reads the imported record only for its page and crop rect, so a wheel with
    no page is left out of the segment (-1) and keeps the host texture.

A car that declares fewer than the three variants a Driver 2 model needs gets a
WARNING and has the missing ones filled with the CLEAN model. A Driver 2 model
with an empty damaged or low block does not fail to load - the engine builds a
car with no polygons in that state and draws nothing, which reads as "the car
disappeared when it got hit". Substituting clean keeps every state drawable.

Anything the declaration does not name is left exactly as the source bank had
it, and a bake of an unchanged declaration reproduces the bank byte for byte.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))

import bake
import bankjson
import carbank_import
import carbank_page
import exportd2
import fourbpp
import pages1
import struct
import exportd2

VARIANTS = ("clean", "damaged", "low")
# the wheel variants the ENGINE carries, in the order LUMP_CAR_WHEELS holds them
# (models.c CAR_IMPORT_WHEEL_CLEAN / _DAMAGED / _FAST)
WHEELS = bankjson.WHEEL_VARIANTS


def _warn(msg):
    print("carbank_bake: warning: %s" % msg, file=sys.stderr)


def _source_colors(lev_path):
    """{variant: [(r, g, b), ...]} read from the bank being replaced.

    An OBJ has nowhere to put a per-poly colour, so the round trip invents the
    neutral grey a Driver 1 flat poly carries. For a Driver 2 car the poly
    colour is a TINT that multiplies the texture, and the donor's is a real
    dark tint - re-baking with grey made the whole car brighter than the bank
    it replaced. The colours therefore come from the bank's own current polygon
    stream, matched by index (the export is 1:1).
    """
    if not lev_path or not os.path.isfile(lev_path):
        return {}

    try:
        with open(lev_path, "rb") as f:
            blob = f.read()
        table = exportd2.d2_model_table(blob)
        out = {}
        for variant in VARIANTS:
            try:
                model = exportd2.decode_variants(blob, table, 1)[variant]
            except (KeyError, IndexError, ValueError):
                continue
            out[variant] = [tuple(p.color) for p in model.polys]
        return out
    except (OSError, ValueError, struct.error) as exc:
        _warn("could not read poly colours from %s (%s); the bake will use "
              "neutral grey" % (lev_path, exc))
        return {}


def _geom_for(obj_path, scale, default_page=None, flip_y=False, flip_winding=False,
               colors=None, flip_z=False):
    """The geom record for one OBJ, with its materials and normals."""
    geom = carbank_import.import_car(obj_path, scale=scale, default_page=default_page,
                                     flip_y=flip_y, flip_winding=flip_winding,
                                     flip_z=flip_z)

    if colors and len(colors) == len(geom["polys"]):
        for poly, color in zip(geom["polys"], colors):
            poly["color"] = list(color)
    elif colors:
        _warn("%s has %d polys but the bank has %d; keeping the source "
              "colours would put them on the wrong panels, so the neutral "
              "tint is used" % (obj_path, len(geom["polys"]), len(colors)))

    return geom


def _block_for(variant, geom, clean_block, scale, flip_normals=False):
    """One raw model block from a geom record."""
    if variant == "damaged":
        if clean_block is None:
            raise SystemExit("carbank_bake: a damaged block needs its clean "
                             "block first")

        return bake.build_damaged_record(geom["vertices"], scale, clean_block)

    return bake.build_model_record(geom["vertices"], geom["polys"],
                                   geom["face_normals"], geom["point_normals"],
                                   scale, flip_normals=flip_normals)


def car_blocks(car, scale, default_page=None, flip_y=False, flip_winding=False, colors=None,
               flip_normals=False, flip_z=False):
    """{variant: block bytes} for one declared car, plus the warnings it earned.

    A missing damaged or low is filled with the CLEAN model rather than left
    empty: Driver 2 always reads all three blocks, and an empty one draws a car
    with no polygons in that state.
    """
    geoms = {}

    for variant in VARIANTS:
        path = getattr(car, variant, None) if variant != "clean" else car.clean

        if path:
            geoms[variant] = _geom_for(path, scale, default_page, flip_y, flip_winding,
                                       (colors or {}).get(variant), flip_z)

    if "clean" not in geoms:
        raise SystemExit("carbank_bake: car %r has no clean OBJ" % car.label)

    warnings = []
    blocks = {}
    blocks["clean"] = _block_for("clean", geoms["clean"], None, scale,
                                 flip_normals)

    for variant in ("damaged", "low"):
        if variant in geoms:
            blocks[variant] = _block_for(variant, geoms[variant],
                                         blocks["clean"], scale, flip_normals)
        else:
            warnings.append(
                "%s declares no %s OBJ; using the clean model for it so that "
                "state still draws" % (car.label, variant))
            blocks[variant] = blocks["clean"]

    # the low-detail block may legitimately be the clean model at a lower
    # vertex count; when it is absent that is what we just did, deliberately

    return blocks, warnings


def source_lev_of(bank):
    """The LEV whose poly colours this bake should keep.

    Prefer the untouched donor: a bank that has already been baked through the
    OBJ round trip carries the neutral grey an OBJ forces, so reading its own
    polygons would bake grey forever. The donor (or an explicitly declared
    donor.lev) is the only place the original tints survive.
    """
    bank_dir = os.path.dirname(os.path.abspath(bank.path))
    lev = bank.raw.get("lev") or ""
    stem = os.path.splitext(os.path.basename(lev))[0] if lev else ""

    declared = getattr(bank, "declared", None)
    donor_lev = getattr(declared, "donor_lev", None) if declared else None
    candidates = []
    if donor_lev:
        candidates.append(os.path.join(bank_dir, donor_lev))
    if stem:
        candidates.append(os.path.join(bank_dir, stem + ".donor.LEV"))
    candidates.append(os.path.join(bank_dir, lev))

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


def build_manifest(bank, out_dir, scale, blocks_dir, default_page=None, flip_y=False,
                   flip_winding=False, colors=None, flip_normals=False, flip_z=False):
    """A manifest exportd2.build_bank can assemble, from the declaration."""
    bank_dir = os.path.dirname(os.path.abspath(bank.path))
    lev_name = bank.raw.get("lev") or ""
    source_lev = os.path.join(bank_dir, lev_name)
    # the rebuild manifest is <CITY>.json beside the bank (FIREBIRD.json), not
    # bank.json itself: bank.json carries the declaration, that one the raw blocks
    source_manifest_path = os.path.join(
        bank_dir,
        bank.raw.get("manifest")
        or (os.path.splitext(os.path.basename(lev_name))[0] + ".json"
            if lev_name else ""))

    # start from the SOURCE manifest (it carries the page blobs, the palette
    # sets and the cosmetics record) and replace only the models and the order:
    # a bank is more than its geometry, and a cherry-picked manifest loses the
    # pages silently ("bank page omitted") and writes a bank that cannot draw
    if os.path.isfile(source_manifest_path):
        with open(source_manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    else:
        manifest = {}

    manifest["city"] = bank.raw.get("city") or bank.name
    manifest["source"] = {"path": source_lev}

    # the source manifest's paths are relative to the BANK folder; this one
    # lives in out_dir, so make them absolute rather than copy a broken path
    for key in ("raw",):
        manifest[key] = {k: (v if os.path.isabs(str(v)) else os.path.join(bank_dir, str(v)))
                         for k, v in (manifest.get(key) or {}).items()}

    if manifest.get("pages"):
        manifest["pages"] = [
            dict(page, png=(page.get("png") if not page.get("png") else
                            page["png"] if os.path.isabs(str(page["png"]))
                            else os.path.join(bank_dir, str(page["png"]))))
            for page in manifest["pages"]]
    # Every DECLARED page has to exist in the manifest: the assembler carves a
    # page only when something names it, so a declared-but-unlisted set is baked
    # to disk and pruned straight back out ("1 sets"). The wheel art lives on
    # its own sets, which the donor manifest has never heard of.
    # The manifest's pages[] is COMPUTED from the declaration's textures map:
    # the images in the car json are the source of truth and the pages record is
    # what they produced (its png is a preview, never an input).
    for carname in bank.cars:
        for texture in bank.cars[carname].textures:
            if not any(p.get("set") == texture.set
                       for p in (manifest.get("pages") or [])):
                manifest.setdefault("pages", []).append({"set": texture.set})

    # and a set the declaration no longer names stops being carried
    declared = {texture.set for car in bank.cars.values()
                for texture in car.textures}
    manifest["pages"] = [p for p in (manifest.get("pages") or [])
                         if p.get("set") in declared]

    warnings = []

    # ...and the declaration owns each page's BAKE, not just its image
    _sync_declared_pages(manifest, bank, bank_dir, warnings, out_dir=out_dir)

    # With palette_from_pages the pages ARE the palette, so the rows are rebuilt
    # from them after the page step. Without it a re-baked page silently keeps a
    # palette row chosen for the old colour set, and every texel on that index
    # paints the wrong colour - the stray-magenta / wrong-grey failure.
    if bank.palette_from_pages:
        _sync_palette_from_pages(manifest, bank, bank_dir, warnings)

    _sync_page_sizes(manifest, bank_dir, out_dir)


    # the DECLARATION owns the slot order: the source manifest still names the
    # donor ids, which no longer exist once the cars are rebuilt
    manifest["bank_order"] = [{"slot": slot, "car": carname}
                                for slot, carname in bank.order]

    os.makedirs(blocks_dir, exist_ok=True)

    by_car = {}

    for slot, carname in bank.order:
        car = bank.cars[carname]
        blocks, car_warnings = car_blocks(car, scale, default_page, flip_y, flip_winding, colors,
                                          flip_normals, flip_z)
        warnings.extend(car_warnings)

        by_car[carname] = {"id": carname, "model": slot, "sets": [],
                           "variants": {}}

        for variant, data in blocks.items():
            name = "%d_%s_%s.bin" % (slot, carname, variant)
            path = os.path.join(blocks_dir, name)

            with open(path, "wb") as f:
                f.write(data)

            by_car[carname]["variants"][variant] = {"raw": path}

    manifest["models"] = list(by_car.values())


    # the sets a car names come from its own geom, not the declaration: a car
    # may use any page the source bank carries
    for model in manifest["models"]:
        car = bank.cars[model["id"]]
        geom = _geom_for(car.clean, scale, default_page, flip_y, flip_winding, None, flip_z)
        model["sets"] = sorted({p["set"] for p in geom["polys"]
                                if p["set"] not in (0, 255)})

    # A page the DECLARATION adds but no rebuilt body model samples is a wheel
    # page. This has to run HERE, after the loop above fills model["sets"]:
    # reading it earlier is what made the first attempt treat every page as a
    # wheel page, sweep the donor's set 65 in and grow the bank to 106496 bytes.
    body_sets = set()
    for model in manifest["models"]:
        body_sets.update(model.get("sets") or [])

    declared_sets = set()
    for carname in bank.cars:
        for texture in bank.cars[carname].textures:
            declared_sets.add(texture.set)

    wheel_sets = set(declared_sets - body_sets)

    # A declared wheel that names its page becomes a wheel MODEL in the bank
    # (LUMP_CAR_WHEELS): the engine keeps the host level's wheel mesh and reads
    # the imported record only for its page and crop rect, so without a page
    # there is nothing to carry. A variant the declaration leaves out is written
    # as -1 and keeps the host level's wheel texture - the missing damaged wheel
    # on the Firebird is that case, deliberately.
    sounds = sound_specs(bank, warnings)

    if sounds:
        manifest["sounds"] = sounds
    else:
        manifest.pop("sounds", None)

    wheels = wheel_specs(bank, warnings)

    if any(wheels):
        manifest["wheels"] = wheels
    else:
        manifest.pop("wheels", None)

    # the wheel pages are part of the bank's texture info whether or not a body
    # poly samples them, so they are named here rather than inferred later
    wheel_sets.update(spec["set"] for spec in wheels if spec)

    _wheel_transparency_warnings(wheels, bank_dir, warnings)

    if wheel_sets:
        manifest["wheel_sets"] = sorted(wheel_sets)
    else:
        manifest.pop("wheel_sets", None)

    return manifest, warnings


def sound_specs(bank, warnings):
    """The manifest's `sounds` block, from the declaration's per-car sounds.

    One SOUNDS entry per model slot the declaration fills. The engine's dynamic
    bank is three samples - engine, idle, then the horn (or the cop siren) - and
    it rejects a bank whose count is not 3, so a car that declares no idle plays
    its engine loop for both, which is what retail Driver 1 banks do.
    """
    models = []
    sirens = []

    for slot, carname in bank.order:
        car = bank.cars[carname]
        sounds = car.sounds

        if sounds is None or not sounds.engine:
            continue

        entry = {"model": slot,
                 "engine": {"wav": sounds.engine,
                            "rate_pct": sounds.engine_pct or 100,
                            "loop_from": sounds.loop_from,
                            "loop": True},
                 "idle": None,
                 "third": None}

        if sounds.idle:
            entry["idle"] = {"wav": sounds.idle,
                             "rate_pct": sounds.idle_pct or 100,
                             "loop_from": sounds.loop_from,
                             "loop": True}
        else:
            warnings.append("%s: no idle declared; the engine loop doubles as "
                            "the idle (as retail D1 banks do)" % carname)

        if sounds.cop:
            sirens.append(slot)

            if sounds.siren:
                entry["third"] = {"wav": sounds.siren, "loop": False}
            else:
                warnings.append("%s: declared a cop car with no siren"
                                % carname)
        elif sounds.horn:
            entry["third"] = {"wav": sounds.horn, "loop": False}
        else:
            warnings.append("%s: no horn declared; the third sample repeats the "
                            "engine, so the horn will sound wrong" % carname)

        models.append(entry)

    if not models:
        return {}

    return {"models": models, "sirens": sirens}


def _wheel_transparency_warnings(wheels, bank_dir, warnings):
    """Say so when a wheel sprite's backdrop is OPAQUE on its page.

    The engine has no per-texel alpha: it keys the backdrop off a CLUT entry of
    0x0000 at the sprite's background index (models.c
    CarImportDeriveWheelPaletteRow only accepts a row whose entry at that index
    is 0 in its first pass). A backdrop baked as a colour - a pink sky behind the
    wheel - therefore leaves the sprite's square corners drawing on the wheel.
    The fast variant is exempt: CarImportReserveFastWheelPalette gives it its own
    row with that entry cleared.
    """
    for spec in wheels:
        if spec is None or spec["variant"] == "fast":
            continue

        path = os.path.join(bank_dir, "pages", "PAGE_%d.bin" % spec["set"])

        if not os.path.isfile(path):
            continue

        try:
            with open(path, "rb") as f:
                blob = f.read()

            rows, raw = pages1.read_page(blob, 0, len(blob))
            indices = fourbpp.decode_indices(raw)
        except (ValueError, struct.error) as exc:
            warnings.append("wheel %s: page %d cannot be read (%s), so its "
                            "transparency cannot be checked"
                            % (spec["variant"], spec["set"], exc))
            continue

        u0, v0, u1, v1 = spec["island"]

        if not rows or not (0 <= u0 < u1 <= fourbpp.PAGE_W
                            and 0 <= v0 < v1 <= fourbpp.PAGE_H):
            continue

        # the engine votes the sprite's background from its four corners
        votes = {}

        for x, y in ((u0, v0), (u1 - 1, v0), (u0, v1 - 1), (u1 - 1, v1 - 1)):
            k = indices[y * fourbpp.PAGE_W + x]
            votes[k] = votes.get(k, 0) + 1

        bg = max(votes, key=lambda k: (votes[k], -k))

        if any(struct.unpack_from("<H", row, bg * 2)[0] == 0 for row in rows):
            continue

        value = struct.unpack_from("<H", rows[0], bg * 2)[0]
        r, g, b = ((value & 31) << 3, ((value >> 5) & 31) << 3,
                   ((value >> 10) & 31) << 3)

        warnings.append(
            "wheel %s: its sprite's backdrop is index %d (#%02x%02x%02x) on page "
            "%d and no CLUT row clears it, so the square's corners draw - re-bake "
            "that page with carbank_page.py --transparent %02x%02x%02x"
            % (spec["variant"], bg, r, g, b, spec["set"], r, g, b))


def _png_size(path):
    """(width, height) straight out of a PNG's IHDR, or None.

    The declared wheel art is a PNG and the bake only needs its extent, so this
    reads the header rather than decoding a 256x256 image to measure it.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None

    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None

    return struct.unpack(">II", head[16:24])


def wheel_specs(bank, warnings):
    """One entry per wheel variant the bank should carry, or None for -1.

    The declaration is per car, but a bank carries ONE wheel set for the whole
    import (the engine reads three variants, not one per model slot), so the
    first car in slot order that declares wheels owns it and any other car's
    wheels are reported rather than silently merged.
    """
    specs = [None] * len(WHEELS)

    owner = None

    for _slot, carname in bank.order:
        car = bank.cars[carname]
        declared = [(variant, car.wheels.get(variant)) for variant in WHEELS]
        declared = [(variant, wheel) for variant, wheel in declared
                    if wheel is not None]

        if not declared:
            continue

        if owner is not None:
            warnings.append(
                "%s declares wheels too; a bank carries one wheel set for the "
                "whole import, and %s's is already it" % (carname, owner))
            continue

        owner = carname

        for variant, wheel in declared:
            if wheel.set is None:
                warnings.append(
                    "%s: wheels.%s has an image but no page set, so the bank "
                    "cannot name a page for it; that variant keeps the host "
                    "level's wheel texture" % (carname, variant))
                continue

            island = wheel.island

            if island is None:
                island = _default_island(wheel.path, carname, variant, warnings)

            if island is None:
                continue

            specs[WHEELS.index(variant)] = {"variant": variant,
                                            "set": wheel.set,
                                            "island": list(island)}

    if owner is not None:
        absent = [variant for variant, spec in zip(WHEELS, specs)
                  if spec is None]
        if absent:
            _warn("%s: the bank carries no %s wheel (offset -1), so the engine "
                  "keeps the host level's wheel for %s"
                  % (owner, "/".join(absent),
                     "it" if len(absent) == 1 else "them"))

    return specs


def _default_island(path, carname, variant, warnings):
    """The declared image's own extent at the page origin."""
    if not path:
        warnings.append(
            "%s: wheels.%s names no crop rect and no image to measure one from; "
            "declare \"island\" for that variant" % (carname, variant))
        return None

    size = _png_size(path)

    if size is None:
        warnings.append(
            "%s: wheels.%s is not a PNG, so its crop rect cannot be read from "
            "it; declare \"island\" for that variant" % (carname, variant))
        return None

    wheels_w, wheels_h = size

    if not (1 <= wheels_w <= 256 and 1 <= wheels_h <= 256):
        warnings.append(
            "%s: wheels.%s is %dx%d; a crop rect has to fit one 256x256 page"
            % (carname, variant, wheels_w, wheels_h))
        return None

    _warn("%s: wheels.%s takes its crop rect from the image, %dx%d at the page "
          "origin - declare \"island\" if the art sits elsewhere on the page"
          % (carname, variant, wheels_w, wheels_h))

    return (0, 0, wheels_w, wheels_h)


def _page_source_hash(page, setid):
    """The (image hash, bake options) a page was baked from."""
    record = {"image": os.path.basename(page.image or ""),
              "transparent": page.transparent,
              "tolerance": page.transparent_tolerance,
              "chroma": page.transparent_chroma}

    if page.protect:
        record["protect"] = ["%02x%02x%02x" % tuple(c) for c in page.protect]

    if page.palette:
        record["palette"] = list(page.palette)

    try:
        with open(page.image, "rb") as f:
            record["sha256"] = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        record["sha256"] = None

    return record


def _sync_declared_pages(manifest, bank, bank_dir, warnings, out_dir=None):
    """Bake every declared page whose IMAGE, or whose bake options, changed.

    The bank bake used to read `pages/PAGE_<set>.bin` and nothing else, so a new
    or edited atlas changed nothing until someone remembered to run
    carbank_page.py by hand - and a stale page is exactly what a "the pink is
    still there" report turns out to be. The declaration now carries the page's
    bake (transparent key, tolerance, palette, protect list) and this bakes the
    page when any of that or the image itself differs from what the manifest
    recorded last time. An unchanged declaration still produces identical bytes:
    nothing is rewritten when the hash and the options match.
    """
    declared = {}
    owner = {}

    for carname in bank.cars:
        for page in bank.cars[carname].textures:
            first = declared.get(page.set)

            if first is not None:
                if first.bake_options() != page.bake_options():
                    warnings.append(
                        "%s and %s declare page set %d differently; %s wins"
                        % (owner[page.set], carname, page.set, owner[page.set]))
                continue

            declared[page.set] = page
            owner[page.set] = carname

    by_set = {p.get("set"): p for p in (manifest.get("pages") or [])}

    for setid, page in sorted(declared.items()):
        entry = by_set.setdefault(setid, {"set": setid})
        source = _page_source_hash(page, setid)
        want = os.path.join(bank_dir, "pages", "PAGE_%d.bin" % setid)

        if (entry.get("source") == source and os.path.isfile(want)
                and entry.get("bin") == "pages/PAGE_%d.bin" % setid):
            continue

        if not source["sha256"]:
            warnings.append("page set %d: %s cannot be read; keeping the page "
                            "on disk" % (setid, page.image))
            continue

        pixels, alpha = carbank_page._load_rgb(page.image)
        key, mask = carbank_page._parse_key(page.transparent, pixels, alpha)

        blob, colours, indices, _error, background = carbank_page.bake_page(
            pixels, key=key, tolerance=page.transparent_tolerance, mask=mask,
            palette=page.palette, protect=page.protect,
            chroma=page.transparent_chroma)

        os.makedirs(os.path.dirname(want), exist_ok=True)

        with open(want, "wb") as f:
            f.write(blob)

        entry.update({"set": setid, "size": len(blob), "ncluts": 1,
                      "bin": "pages/PAGE_%d.bin" % setid,
                      "source": source})
        by_set[setid] = entry

        if background is None:
            warnings.append("page set %d: re-baked from %s (no transparent "
                            "colour)" % (setid, os.path.basename(page.image)))
        else:
            warnings.append("page set %d: re-baked from %s (transparent index "
                            "%d, %d texel(s))"
                            % (setid, os.path.basename(page.image),
                               background["index"], background["keyed"]))

    manifest["pages"] = [by_set[k] for k in sorted(by_set)]


def audit_colour(bank_dir, manifest, colour, tolerance=0):
    """Every place a colour still appears: page, texels, index, CLUT word.

    "There is still pink on the car" has exactly three answers - the texels are
    on a page, the palette row paints them, or the poly samples a page that was
    never re-baked - and this prints all three rather than leaving it to guesswork.
    """
    found = []
    key = tuple(colour)

    for page in manifest.get("pages") or []:
        setid = page.get("set")
        path = os.path.join(bank_dir, "pages", "PAGE_%d.bin" % setid)

        if not os.path.isfile(path):
            continue

        try:
            with open(path, "rb") as f:
                blob = f.read()

            rows, raw = pages1.read_page(blob, 0, len(blob))
            indices = fourbpp.decode_indices(raw)
        except (ValueError, struct.error):
            continue

        rgb_rows = [fourbpp.clut_rgb(r) for r in rows]
        hits = {}

        for i, index in enumerate(indices):
            if index in hits:
                continue

            for r, row in enumerate(rgb_rows):
                got = row[index]

                if max(abs(got[c] - key[c]) for c in range(3)) <= tolerance:
                    hits[index] = (r, got)
                    break

        for index, (row, got) in sorted(hits.items()):
            count = indices.count(index)
            word = struct.unpack_from("<H", rows[row], index * 2)[0]

            found.append({"set": setid, "index": index, "row": row,
                          "texels": count, "colour": got, "word": word,
                          "transparent": word == 0})

    return found


def check_sources(bank, manifest, bank_dir, stale_only=True):
    """What the bake would change: a stale page source, a stale palette."""
    problems = []

    for carname in bank.cars:
        for page in bank.cars[carname].textures:
            record = next((p for p in (manifest.get("pages") or [])
                           if p.get("set") == page.set), None)
            want = os.path.join(bank_dir, "pages", "PAGE_%d.bin" % page.set)
            source = _page_source_hash(page, page.set)

            if record is None:
                problems.append("page set %d has no record in the manifest"
                                % page.set)
            elif record.get("source") != source:
                problems.append("page set %d is STALE: %s (or its bake options) "
                                "changed since it was baked"
                                % (page.set, os.path.basename(page.image)))
            elif not os.path.isfile(want):
                problems.append("page set %d has no %s"
                                % (page.set, os.path.basename(want)))

    # The palette the bank carries is what PAINTS the pages' indices. If a row
    # disagrees with the page it paints, every texel on that index draws someone
    # else's colour - which is what a stray magenta or a wrong grey on a panel
    # actually is. Compare them and say so.
    try:
        import carbank_palette

        with open(os.path.join(bank_dir, "pallet.bin"), "rb") as f:
            pal_body = f.read()

        pal = exportd2.d2_palette_from_bytes(pal_body)
        lev = os.path.join(bank_dir, os.path.basename(bank.raw.get("lev") or ""))
        pages = carbank_palette.page_sets(lev) if os.path.isfile(lev) else set()
        pages = pages or {p.get("set") for p in (manifest.get("pages") or [])}
        rows = carbank_palette.rows_from_pages(bank_dir, pages)
        bank_rows = {r["tpageindex"]: r["clut"] for r in pal["records"]}

        for row in rows:
            have = bank_rows.get(row["tpageindex"])

            if have is None:
                problems.append("set %d is not in the bank's palette, so its "
                                "texels paint whatever row is picked"
                                % row["tpageindex"])
            elif have != row["clut"]:
                differing = sum(1 for i in range(16)
                                if have[i * 2:i * 2 + 2] != row["clut"][i * 2:i * 2 + 2])
                problems.append("set %d: the palette row differs from the page's "
                                "own in %d of 16 entries - the page was quantised "
                                "against a palette the bank does not carry. Run "
                                "carbank_palette.py --from-pages"
                                % (row["tpageindex"], differing))
    except (OSError, ValueError, struct.error):
        pass

    palette_json = os.path.join(bank_dir, "palette.json")
    pallet_bin = os.path.join(bank_dir, "pallet.bin")

    if os.path.isfile(palette_json) and os.path.isfile(pallet_bin):
        import carbank_palette

        doc = carbank_palette.load_json(palette_json)
        # only the rows for sets the BANK carries can reach it, so compare on
        # that intersection - carbank_palette writes exactly that view
        lev = os.path.join(bank_dir,
                           os.path.basename(bank.raw.get("lev") or ""))
        sets = carbank_palette.page_sets(lev) if os.path.isfile(lev) else set()
        sets = sets or {p.get("set") for p in (manifest.get("pages") or [])}
        rows = carbank_palette.rows_from_doc(doc, sets)
        body = exportd2.build_palette_body(rows, doc.get("total_cluts"))
        want = carbank_palette.embedded_palette_body(pallet_bin, sets)[0]

        if want != body:
            problems.append("pallet.bin is STALE: palette.json says %d row(s) "
                            "for sets %s, the file says otherwise - run "
                            "carbank_palette.py"
                            % (len(rows),
                               ",".join(str(s) for s in sorted(sets))))

    return problems


def _sync_palette_from_pages(manifest, bank, bank_dir, warnings):
    """pallet.bin (and palette.json) from the pages' own row 0."""
    import carbank_palette

    # the DECLARED pages, not every entry the manifest has accumulated: a stale
    # page for a set this bank no longer carries must not add a palette row
    sets = {texture.set for car in bank.cars.values()
            for texture in car.textures}
    rows = carbank_palette.rows_from_pages(bank_dir, sets)

    if not rows:
        warnings.append("palette_from_pages: no pages to take a palette from")
        return

    body = exportd2.build_palette_body(rows, len(rows))
    path = os.path.join(bank_dir, "pallet.bin")
    old = None

    if os.path.isfile(path):
        with open(path, "rb") as f:
            old = f.read()

    with open(path, "wb") as f:
        f.write(body)

    if old != body:
        warnings.append("palette: rebuilt %d row(s) from the pages"
                        % len(rows))

    doc_path = os.path.join(bank_dir, "palette.json")

    if os.path.isfile(doc_path):
        with open(doc_path, "w", encoding="utf-8") as f:
            json.dump(carbank_palette.palette_doc(rows), f, indent=1)


def _sync_page_sizes(manifest, bank_dir, out_dir):
    """Rebuild LUMP_TEXTUREINFO when a page on disk is not the size it claims.

    A page's size lives in the textureinfo lump, and the bank carves pages from
    it. Replacing PAGE_<set>.bin with an atlas that packs to a different size
    therefore has to restate it - otherwise the bake truncates the new page to
    the old length (measured: a 33060-byte atlas cut to 30960, and only a
    warning to show for it) and the car draws garbage from a half page.
    """
    raw = manifest.get("raw") or {}
    ti_path = raw.get("textureinfo")

    if not ti_path or not os.path.isfile(ti_path):
        return

    with open(ti_path, "rb") as f:
        ti_bytes = f.read()

    head, perms, specs = exportd2.split_textureinfo(ti_bytes)
    changed = False
    sizes = {}

    def fix(entries):
        nonlocal changed
        out = []

        for setid, size in entries:
            path = os.path.join(bank_dir, "pages", "PAGE_%d.bin" % setid)

            if os.path.isfile(path):
                actual = os.path.getsize(path)
                sizes[setid] = actual

                if actual != size:
                    changed = True
                    size = actual

            out.append((setid, size))

        return out

    perms = fix(perms)
    specs = fix(specs)

    # a page the declaration adds is not in textureinfo yet, and fix() can only
    # restate the size of one that is: append it, with the size of the file on
    # disk, or the assembler has no length to carve
    listed = {setid for setid, _ in perms}
    for page in manifest.get("pages") or []:
        setid = page.get("set")
        if setid in listed:
            continue
        path = os.path.join(bank_dir, "pages", "PAGE_%d.bin" % setid)
        if os.path.isfile(path):
            size = os.path.getsize(path)
            perms.append((setid, size))
            sizes[setid] = size
            listed.add(setid)
            changed = True

    for page in manifest.get("pages") or []:
        if page.get("set") in sizes:
            page["size"] = sizes[page["set"]]

    if not changed:
        return

    rebuilt = exportd2.build_textureinfo(head, perms, specs)

    ti_out = os.path.join(bank_dir, "textureinfo.bin")

    with open(ti_out, "wb") as f:
        f.write(rebuilt)

    manifest["raw"] = dict(raw, textureinfo=ti_out)
    _warn("page size(s) changed; rebuilt LUMP_TEXTUREINFO to match the files "
          "on disk")

def bake_bank(bank_path, out_dir, scale=0.68267, d1=None, default_page=None,
              flip_y=False, flip_winding=False, flip_normals=False, flip_z=False):
    """Bake the declared bank into out_dir. Returns (ok, warnings)."""
    if d1:
        raise SystemExit("carbank_bake: --d1 bakes a Driver 1 pool LEV with "
                         "bake.build_file; a bank is Driver 2 shaped and goes "
                         "through exportd2.build_bank")

    bank = bankjson.load(bank_path)

    if not bank.declared:
        # nothing declared: the assembly must reproduce the bank untouched
        _warn("%s declares nothing; re-baking the source bank unchanged"
              % bank_path)

    os.makedirs(out_dir, exist_ok=True)
    # the blocks belong in the workspace's models/, so the manifest this writes
    # back still describes the bank after the bake
    blocks_dir = os.path.join(os.path.dirname(os.path.abspath(bank.path)),
                              "models")

    bank_dir = os.path.dirname(os.path.abspath(bank.path))
    # Y mirror and winding are SEPARATE: reversing the winding is how an
    # inside-out car is fixed when the mesh is already the right way up.
    colors = _source_colors(source_lev_of(bank))
    manifest, warnings = build_manifest(bank, out_dir, scale, blocks_dir, default_page, flip_y,
                                       flip_winding,
                                       colors, flip_normals, flip_z)

    # build_bank resolves raw/pages/PAGE_<set>.bin relative to the MANIFEST, so
    # the manifest is written beside the bank (and removed afterwards)
    # build_bank resolves raw/pages/PAGE_<set>.bin relative to the manifest, so it
    # must sit beside the bank; writing it under the CITY name keeps the workspace
    # self-describing (a later --rebuild-bank reproduces what is installed)
    manifest_path = os.path.join(
        bank_dir,
        os.path.splitext(os.path.basename(bank.raw.get("lev") or "bank.LEV"))[0]
        + ".json")

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)

    report = exportd2.build_bank(manifest, manifest_path, out_dir, None)

    # A bakes's own checks are the only thing standing between a workshop and a
    # bank with holes in it: build_bank writes the files either way, so the
    # verdict has to reach the caller and the exit code (rebake.bat installs on
    # a 0). A page whose bytes were missing used to print "bank ok" and be
    # installed with no textures at all.
    problems = (report.get("checks") or {}).get("problems") or []
    ok = (report.get("checks") or {}).get("ok", True) and not problems

    if problems:
        warnings = list(warnings) + ["check: %s" % p for p in problems]

    # refresh the workspace's own computed half: a bake to a scratch folder used
    # to leave bank.json describing a bank that no longer exists (empty
    # included_sets, an ancient size), which every --check then compared against
    _sync_bank_report(bank_path, report)

    return ok, warnings


def _sync_bank_report(bank_path, report):
    """Write the bake's computed half into the workspace's bank.json."""
    try:
        with open(bank_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return

    for key, value in report.items():
        if key == "declare":
            continue

        # the report names the files in the SCRATCH folder it baked into; the
        # workspace's own copies have the same names
        if key in ("lev", "lcf") and isinstance(value, str):
            value = os.path.basename(value)

        doc[key] = value

    with open(bank_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)


def _report_only(args):
    """--check / --audit: read the workspace, write nothing."""
    bank = bankjson.load(args.bank)
    bank_dir = os.path.dirname(os.path.abspath(args.bank))

    manifest_path = os.path.join(
        bank_dir,
        os.path.splitext(os.path.basename(bank.raw.get("lev") or "bank.LEV"))[0]
        + ".json")

    manifest = {}

    if os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

    if args.check:
        problems = check_sources(bank, manifest, bank_dir)

        for problem in problems:
            print(problem)

        if not problems:
            declared = [t for c in bank.cars.values() for t in c.textures]
            print("%d declared source(s) are current" % len(declared))
            print("  (a page with no \"source\" is a preview and is never "
                  "re-baked)")

        return 1 if problems else 0

    colour = carbank_page.hex_colour(args.audit)
    found = audit_colour(bank_dir, manifest, colour, args.audit_tolerance)

    print("#%02x%02x%02x:" % colour)

    if not found:
        print("  not on any page")
        return 0

    for hit in found:
        print("  set %3d  index %2d  row %d  %d texel(s)  #%02x%02x%02x  word "
              "%04x%s"
              % (hit["set"], hit["index"], hit["row"], hit["texels"],
                 hit["colour"][0], hit["colour"][1], hit["colour"][2],
                 hit["word"], "  TRANSPARENT" if hit["transparent"] else ""))

    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bank", help="the bank.json with the declare block")
    ap.add_argument("-o", "--out", help="output folder (not needed for "
                                        "--check / --audit)")
    ap.add_argument("--scale", type=float, default=0.68267,
                    help="OBJ scale (default 0.68267, the D2/D1 cell ratio)")
    ap.add_argument("--default-page", metavar="SET:ID", default=None,
                    help="page for an OBJ whose materials are not set<S>_id<T> "
                         "(an author export); without it they are refused")
    ap.add_argument("--flip-z", action="store_true",
                    help="negate Z as well as Y: the pair is a 180-degree rotation about X, "
                         "which is what a Z-up mesh needs - a mirror alone is inside out")
    ap.add_argument("--flip-normals", action="store_true",
                    help="negate the stored inward normals; a mesh exported with the "
                         "opposite winding convention otherwise shades inside-out")
    ap.add_argument("--flip-y", action="store_true",
                    help="negate Y: the engine's model space is Y-down, a Blender OBJ is Y-up")
    ap.add_argument("--flip-winding", action="store_true",
                    help="reverse every face: required with --flip-y, or the car is inside-out")
    ap.add_argument("--d1", metavar="LEV",
                    help="bake a DRIVER 1 LEV instead (pool shape, bake.build_file)")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; report page/palette sources that are stale")
    ap.add_argument("--audit", metavar="RRGGBB", default=None,
                    help="write nothing; report where a colour still lives (page, "
                         "texel count, palette index, CLUT word)")
    ap.add_argument("--audit-tolerance", type=int, default=8, metavar="N",
                    help="per-channel slack for --audit (default 8)")
    args = ap.parse_args(argv)

    if args.check or args.audit:
        return _report_only(args)

    if args.d1:
        raise SystemExit("carbank_bake: --d1 is not implemented in this tool; "
                         "the Driver 1 pool bake is tools/car/bake.py")

    default_page = None
    if args.default_page:
        s_, i_ = args.default_page.split(":")
        default_page = (int(s_), int(i_))

    ok, warnings = bake_bank(args.bank, os.path.abspath(args.out), args.scale,
                             default_page=default_page,
                             flip_y=args.flip_y, flip_winding=args.flip_winding,
                             flip_normals=args.flip_normals, flip_z=args.flip_z)

    print("baked %s -> %s" % (os.path.basename(args.bank), args.out))

    for w in warnings:
        _warn(w)

    if not ok:
        print("carbank_bake: FAILED - the bank is unusable, see the check "
              "problems above", file=sys.stderr)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
