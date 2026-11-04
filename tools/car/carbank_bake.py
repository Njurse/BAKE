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
import io
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


def _id_at(poly, rects):
    """The CLUT row a poly draws: the plan rect its UV centroid lands in.

    The smallest containing rect wins, and a rect of the poly's own id wins a
    tie, so a panel that was not split stays on the id its material named.
    """
    uvs = poly.get("uv") or []
    fallback = rects[0]["id"]

    if not uvs:
        own = poly.get("id")
        return own if any(r["id"] == own for r in rects) else fallback

    u = sum(p[0] for p in uvs) / float(len(uvs))
    v = sum(p[1] for p in uvs) / float(len(uvs))
    hit = [r for r in rects
           if r["rect"][0] <= u < r["rect"][2] and r["rect"][1] <= v < r["rect"][3]]

    if not hit:
        own = poly.get("id")
        return own if any(r["id"] == own for r in rects) else fallback

    own_id = poly.get("id")
    own = [r for r in hit if r["id"] == own_id]
    chosen = own or hit
    chosen.sort(key=lambda r: (r["rect"][2] - r["rect"][0]) * (r["rect"][3] - r["rect"][1]))
    return chosen[0]["id"]


def car_blocks(car, scale, default_page=None, flip_y=False, flip_winding=False, colors=None,
               flip_normals=False, flip_z=False, clut_plans=None):
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

    # A page baked as one CLUT row can only be drawn with texture id 0:
    # texture_cluts[set][id] is that row, and id 17 of a one-row page is empty
    # (the car drew white). A page the splitter gave several rows keeps each
    # poly on the row its UV centroid landed in — id 13 stays 13 when that row
    # was built for it. Collapsing those ids onto 0 is what painted the lights
    # with the body palette.
    rebaked = {t.set for t in car.textures}
    plans = clut_plans or {}

    for geom in geoms.values():
        for poly in geom["polys"]:
            page_set = poly.get("set")

            if page_set not in rebaked:
                continue

            rects = plans.get(page_set)

            if rects:
                poly["id"] = _id_at(poly, rects)
            else:
                poly["id"] = 0

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


def bank_layout(bank):
    """(bank_dir, build_dir) for a loaded bank.

    The bank folder holds the author's INPUTS and nothing else: bank.json,
    cars/<id>/ (handling.json, OBJ/MTL, textures) and any shared/ art they name.
    "build_dir" (bank.json, relative to the bank folder) holds everything the
    bake GENERATES: pages/, models/, pallet.bin, textureinfo.bin,
    texturenames.bin, modelnames.bin, the built <NAME>.json, <NAME>.report.json
    and the LEV/LCF. Nothing under it is read back as an input, so deleting it
    is always safe.

    Left out, the build folder is the bank folder itself (the old flat layout).
    An old "donor_dir" key is ignored: a declared bank has no donor inputs.
    """
    bank_dir = os.path.dirname(os.path.abspath(bank.path))
    value = bank.raw.get("build_dir")

    if not value:
        return bank_dir, bank_dir

    return bank_dir, os.path.normpath(value if os.path.isabs(value)
                                      else os.path.join(bank_dir, value))


def _manifest_name(bank):
    lev_name = bank.raw.get("lev") or ""
    return (bank.raw.get("manifest")
            or os.path.splitext(os.path.basename(lev_name or "bank.LEV"))[0]
            + ".json")


def _built_manifest(bank):
    """build/<NAME>.json - what the last bake assembled. An OUTPUT: a declared
    bank never reads it back as a bake input (only --check/--audit look)."""
    _bank_dir, build_dir = bank_layout(bank)
    path = os.path.join(build_dir, _manifest_name(bank))
    return path if os.path.isfile(path) else ""


def _legacy_manifest(bank):
    """The manifest an UNDECLARED (pre-generative) bank is reproduced from.

    Such a bank has no declaration, so its manifest IS its description. It is
    looked for beside the build output, in the bank folder, and in an old
    "donor_dir" if bank.json still names one. Declared banks never come here.
    """
    bank_dir, build_dir = bank_layout(bank)
    folders = [build_dir, bank_dir]

    if bank.raw.get("donor_dir"):
        folders.append(os.path.join(bank_dir, bank.raw["donor_dir"]))

    for folder in folders:
        path = os.path.join(folder, _manifest_name(bank))
        if os.path.isfile(path):
            return path

    return ""


def _legacy_manifest_as_is(bank, gen_dir, out_dir):
    """An undeclared bank's manifest, reproduced as it stands (migration only)."""
    path = _legacy_manifest(bank)

    if not path:
        raise SystemExit("%s declares nothing and has no %s to reproduce; add a "
                         "\"declare\" block (see bank-schema.md)"
                         % (bank.path, _manifest_name(bank)))

    base = os.path.dirname(path)
    bank_dir = os.path.dirname(os.path.abspath(bank.path))

    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)

    manifest["city"] = bank.raw.get("city") or manifest.get("city")
    manifest["source"] = {"path": os.path.join(bank_dir, bank.raw.get("lev") or "")}

    def absolute(value):
        value = str(value)
        return value if os.path.isabs(value) else os.path.join(base, value)

    manifest["raw"] = {k: absolute(v) for k, v in (manifest.get("raw") or {}).items()}

    for key in ("lcf", "cosmetics"):
        if manifest.get(key):
            manifest[key] = absolute(manifest[key])

    if manifest.get("pages"):
        manifest["pages"] = [dict(page, png=absolute(page["png"]))
                             if page.get("png") else page
                             for page in manifest["pages"]]

    _sync_page_sizes(manifest, gen_dir, out_dir)
    return manifest, []


# Wheel crop rects are named in LUMP_TEXTUREINFO the way a retail level names
# them: models.c finds WHLCLEAN / WHLBOTH there when a bank has no wheel lump.
_WHEEL_DETAIL = {"clean": "WHLCLEAN", "damaged": "WHLBOTH"}


def generated_textureinfo(page_sizes, wheels=()):
    """(textureinfo, texturenames) lump bodies made from the baked pages alone.

    `page_sizes` is {set: bytes}. The bank lists every page (the engine's
    import reads only the permanent/special lists, texture.c
    ParseImportedTextureInfoForCity); tpage_positions are zero, and the only
    TEXINF details are the declared wheel islands, named in the returned name
    buffer.
    """
    sets = sorted(page_sizes)

    if sets and sets[-1] >= 128:
        raise SystemExit("bank: page set %d is past the 128 a TextureInfo lump "
                         "can carry" % sets[-1])

    tpage_amount = sets[-1] + 1 if sets else 1
    names = bytearray()
    details = {}

    for spec in wheels or ():
        name = _WHEEL_DETAIL.get(spec["variant"]) if spec else None

        if not name:
            continue

        u0, v0, u1, v1 = spec["island"]
        recs = details.setdefault(spec["set"], [])
        recs.append(struct.pack(pages1.TEXINF_FMT, len(recs), len(names),
                                u0, v0, min(255, u1 - u0), min(255, v1 - v0)))
        names += name.encode("ascii") + b"\0"

    head = bytearray(struct.pack("<ii", tpage_amount, 0))
    head += b"\0" * (8 * (tpage_amount + 1))

    for page in range(tpage_amount):
        recs = details.get(page, [])
        head += struct.pack("<i", len(recs)) + b"".join(recs)

    body = exportd2.build_textureinfo(bytes(head),
                                      [(s, page_sizes[s]) for s in sets], [])
    return body, bytes(names)


def generated_modelnames(order):
    """LUMP_MODELNAMES: one name per model slot, the car id in a filled slot."""
    slots = dict(order)
    top = max(slots) + 1 if slots else 0
    return b"".join(slots.get(i, "").encode("ascii", "replace") + b"\0"
                    for i in range(top))


def _write_generated_lumps(manifest, bank, gen_dir, wheels):
    """build/textureinfo.bin, texturenames.bin, modelnames.bin from the pages
    and the declaration - the only place these lumps come from."""
    sizes = {}

    for page in manifest.get("pages") or []:
        path = os.path.join(gen_dir, "pages", "PAGE_%d.bin" % page["set"])

        if not os.path.isfile(path):
            raise SystemExit("bank: declared page set %d was not baked (no %s)"
                             % (page["set"], path))

        sizes[page["set"]] = page["size"] = os.path.getsize(path)

    textureinfo, texturenames = generated_textureinfo(sizes, wheels)
    raw = dict(manifest.get("raw") or {})

    for key, body in (("textureinfo", textureinfo),
                      ("texturenames", texturenames),
                      ("modelnames", generated_modelnames(bank.order))):
        path = os.path.join(gen_dir, key + ".bin")

        with open(path, "wb") as f:
            f.write(body)

        raw[key] = path

    manifest["raw"] = raw


def build_manifest(bank, out_dir, scale, blocks_dir, default_page=None, flip_y=False,
                   flip_winding=False, colors=None, flip_normals=False, flip_z=False,
                   force_pages=False):
    """A manifest exportd2.build_bank can assemble, from the declaration."""
    bank_dir, gen_dir = bank_layout(bank)

    if not bank.declared:
        return _legacy_manifest_as_is(bank, gen_dir, out_dir)

    if not bank.palette_from_pages:
        raise SystemExit("%s: \"palette_from_pages\": false - a declared bank's "
                         "palette is generated from its pages; remove the key"
                         % bank.path)

    # A declared bank is GENERATED: the working manifest starts empty and is
    # built from bank.json + cars/ alone. No earlier manifest - the last
    # build's or an exported one - is read back as an input.
    name = bank.raw.get("city") or bank.name
    manifest = {"city": name, "bank_name": name, "pages": [], "raw": {}}

    # Every DECLARED page has to exist in the manifest: the assembler carves a
    # page only when something names it, so a declared-but-unlisted set is baked
    # to disk and pruned straight back out ("1 sets").
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
    _sync_declared_pages(manifest, bank, gen_dir, warnings,
                         out_dir=out_dir, force=force_pages)

    # With palette_from_pages the pages ARE the palette, so the rows are rebuilt
    # from them after the page step. Without it a re-baked page silently keeps a
    # palette row chosen for the old colour set, and every texel on that index
    # paints the wrong colour - the stray-magenta / wrong-grey failure.
    if bank.palette_from_pages:
        _sync_palette_from_pages(manifest, bank, gen_dir, warnings)

    # the DECLARATION owns the slot order
    manifest["bank_order"] = [{"slot": slot, "car": carname}
                                for slot, carname in bank.order]

    os.makedirs(blocks_dir, exist_ok=True)

    by_car = {}

    for slot, carname in bank.order:
        car = bank.cars[carname]
        blocks, car_warnings = car_blocks(
            car, scale, default_page, flip_y, flip_winding, colors,
            flip_normals, flip_z,
            clut_plans={p["set"]: p["clut_rects"]
                        for p in (manifest.get("pages") or [])
                        if p.get("clut_rects")})
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
        # an untextured flat poly (F3) samples no page; obj2geom parks it on
        # set 1, which a Driver 2 export does not carry
        model["sets"] = sorted({p["set"] for p in geom["polys"]
                                if p["set"] not in (0, 255)
                                and p.get("kind") != "F3"})

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

    _wheel_transparency_warnings(wheels, gen_dir, warnings)

    if wheel_sets:
        manifest["wheel_sets"] = sorted(wheel_sets)
    else:
        manifest.pop("wheel_sets", None)

    # textureinfo / texturenames / modelnames are generated, never copied
    _write_generated_lumps(manifest, bank, gen_dir, wheels)

    # Each car's handling.json "handling" block IS its cosmetics record (LCF):
    # wheelDisp, cPoints, colBox, mass... There is no other source.
    decl_cars = (bank.raw.get("declare") or {}).get("cars") or {}
    for model in manifest["models"]:
        ref = decl_cars.get(model["id"])
        handling = None
        if isinstance(ref, str):
            with open(os.path.join(bank_dir, ref), encoding="utf-8") as f:
                handling = json.load(f).get("handling")
        elif isinstance(ref, dict):
            handling = ref.get("handling")
        if handling:
            model["handling"] = handling
        else:
            warnings.append("%s: no \"handling\" block - its LCF record is "
                            "zeroed" % model["id"])
    manifest.pop("lcf", None)
    manifest.pop("cosmetics", None)

    return manifest, warnings


def _resolved_sample(sounds, key, role, defaults):
    """One sample spec with every knob spelled out, or None when undeclared.

    Nothing downstream is allowed to have an opinion about a default: the
    manifest carries the numbers the encoder will use, so a bank can be checked
    (and a hand-edited declaration completed) without re-reading the bake code.
    `role` decides only the one thing a sample cannot state for itself - whether
    it loops - since the third slot is a horn/siren.
    """
    spec = getattr(sounds, key)

    if not spec:
        return None

    out = {"wav": spec["wav"]}

    if spec.get("loop") is None:
        out["loop"] = role != "third"
    elif spec["loop"] == "auto":
        # "auto" survives as itself: the encoder needs to know it may search for
        # loop points, which bool() would throw away
        out["loop"] = "auto"
    else:
        out["loop"] = bool(spec["loop"])

    level = spec.get("level")
    out["level"] = float(level) if level is not None else defaults["peak"]
    rms = spec.get("rms")
    out["rms"] = (float(rms) if rms is not None
                  else float(defaults.get("rms") or 0.0))

    # a per-sample rate wins over a per-sample percentage, which wins over the
    # sounds-level percentage (engine_pct / idle_pct); each is already validated
    out["rate"] = spec.get("rate")
    out["pitch"] = float(spec.get("pitch") or 1.0)
    out["rate_pct"] = spec.get("rate_pct") or 100
    out["max_rate"] = defaults["max_rate"]
    out["sample_bytes"] = defaults["sample_bytes"]

    loop_from = spec.get("loop_from")

    if loop_from is None:
        loop_from = defaults["loop_from"]

    if key == "idle" and sounds.idle_pct:
        out["rate_pct"] = sounds.idle_pct
    elif key == "engine" and sounds.engine_pct:
        out["rate_pct"] = sounds.engine_pct

    out["loop_from"] = float(loop_from or 0.0)
    out["loop_to"] = spec.get("loop_to")
    out["force"] = bool(spec.get("force"))
    out["normalise"] = (True if spec.get("normalise") is None
                        else bool(spec.get("normalise")))
    out["gain_db"] = float(spec.get("gain_db") or 0.0)
    out["trim"] = spec.get("trim")
    out["fade_out"] = float(spec.get("fade_out") or 0.0)

    return out


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

        defaults = dict(bankjson.SOUND_DEFAULTS)

        for key in ("peak", "rms", "max_rate", "sample_bytes"):
            if getattr(sounds, key) is not None:
                defaults[key] = getattr(sounds, key)

        if sounds.loop_from:
            defaults["loop_from"] = sounds.loop_from

        entry = {"model": slot,
                 "engine": _resolved_sample(sounds, "engine", "engine", defaults),
                 "idle": _resolved_sample(sounds, "idle", "idle", defaults),
                 "third": None}

        # the bank-wide knobs travel too: verify_sounds and --complete compare
        # against what the bake actually used
        entry["peak"] = defaults["peak"]
        entry["rms"] = defaults["rms"]
        entry["max_rate"] = defaults["max_rate"]
        entry["sample_bytes"] = defaults["sample_bytes"]

        if sounds.idle:
            if sounds.idle_pct:
                entry["idle"]["rate_pct"] = sounds.idle_pct
        else:
            warnings.append("%s: no idle declared; the engine loop doubles as "
                            "the idle (as retail D1 banks do)" % carname)

        third = None

        if sounds.cop:
            sirens.append(slot)
            third = _resolved_sample(sounds, "siren", "third", defaults)

            if third is None:
                warnings.append("%s: declared a cop car with no siren"
                                % carname)
        else:
            third = _resolved_sample(sounds, "horn", "third", defaults)

            if third is None:
                warnings.append("%s: no horn declared; the third sample repeats "
                                "the engine, so the horn will sound wrong"
                                % carname)

        entry["third"] = third
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


def _file_sha256(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _uv_islands(bank, setid):
    """{texture id: [uv list, ...]} for every poly on this set, page texels.

    OBJ uvs are 0..1, v up. The engine's are 0..255, v down — the same conversion
    obj2geom uses, so a centroid tested later lands in the rect built here.
    """
    import obj2geom

    islands = {}
    cache = getattr(_uv_islands, "_cache", None)

    if cache is None:
        cache = {}
        _uv_islands._cache = cache

    for car in bank.cars.values():
        uses = any(t.set == setid for t in car.textures)

        if not uses:
            continue

        for path in (car.clean, car.damaged, car.low):
            if not path:
                continue

            if path not in cache:
                try:
                    cache[path] = obj2geom.parse_obj(path, with_materials=True)
                except OSError:
                    cache[path] = None

            parsed = cache[path]

            if not parsed:
                continue

            _verts, uvs, faces, names = parsed

            for (_vi, ti), name in zip(faces, names):
                try:
                    page = obj2geom.material_to_page(name, path)
                except ValueError:
                    continue

                if page is None or page[0] != setid:
                    continue

                pts = []

                for t in ti:
                    if 0 <= t < len(uvs):
                        u, v = uvs[t]
                        pts.append((int(round(u * 255)) & 0xFF,
                                    int(round((1.0 - v) * 255)) & 0xFF))

                if len(pts) >= 3:
                    islands.setdefault(page[1] & 0xFF, []).append(pts)

    return islands


def _page_source_hash(page, setid, sprites=None, uv_islands=None):
    """The (image hash, bake options, wheel sprites) a page was baked from.

    The sprites are part of the source: an island is composited from them, so a
    re-painted wheel image has to re-bake its page even though the page image
    itself never changed. UV islands are too: they decide which polys share a
    CLUT row, so an edited unwrap has to re-bake even when the PNG did not change.
    """
    record = {"image": os.path.basename(page.image or ""),
              "transparent": page.transparent,
              "tolerance": page.transparent_tolerance,
              "chroma": page.transparent_chroma,
              "split": getattr(page, "split", "auto"),
              "max_error": getattr(page, "max_error", None)}

    if sprites:
        record["wheels"] = [{"variant": variant,
                             "image": os.path.basename(path),
                             "sha256": _file_sha256(path),
                             "island": [int(v) for v in island]}
                            for variant, path, island in sprites]

    if page.protect:
        record["protect"] = ["%02x%02x%02x" % tuple(c) for c in page.protect]

    if page.palette:
        record["palette"] = list(page.palette)

    if uv_islands:
        record["islands"] = {str(tex_id): [list(carbank_page._bbox_uv(uvs))
                                            for uvs in lists if uvs]
                             for tex_id, lists in sorted(uv_islands.items())}

    try:
        with open(page.image, "rb") as f:
            record["sha256"] = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        record["sha256"] = None

    return record


def wheel_islands(bank):
    """{set: [(variant, image path, island)]} for every declared wheel sprite.

    The FIRST car in slot order owns the bank's wheels (see wheel_specs), so only
    that car's sprites are composited; another car's would be reported by
    wheel_specs and never reach the bank anyway.
    """
    islands = {}
    owner = None

    for _slot, carname in bank.order:
        car = bank.cars[carname]
        declared = [(v, car.wheels.get(v)) for v in WHEELS]
        declared = [(v, w) for v, w in declared if w is not None]

        if not declared:
            continue

        if owner is not None:
            continue

        owner = carname

        for variant, wheel in declared:
            if wheel.set is None or wheel.island is None or not wheel.path:
                continue

            # one island, one sprite: clean wins over fast on a shared rect
            if any(list(i) == list(wheel.island)
                   for _v, _p, i in islands.get(wheel.set, [])):
                continue

            islands.setdefault(wheel.set, []).append(
                (variant, wheel.path, wheel.island))

    return islands


def _sync_declared_pages(manifest, bank, bank_dir, warnings, out_dir=None,
                         force=False):
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
    islands = wheel_islands(bank)

    for setid, page in sorted(declared.items()):
        entry = by_set.setdefault(setid, {"set": setid})
        uv_islands = _uv_islands(bank, setid)
        source = _page_source_hash(page, setid, islands.get(setid), uv_islands)
        want = os.path.join(bank_dir, "pages", "PAGE_%d.bin" % setid)

        if (not force and entry.get("source") == source
                and os.path.isfile(want)
                and entry.get("bin") == "pages/PAGE_%d.bin" % setid):
            continue

        if not source["sha256"]:
            warnings.append("page set %d: %s cannot be read; keeping the page "
                            "on disk" % (setid, page.image))
            continue

        pixels, alpha = carbank_page._load_rgb(page.image)
        key, mask = carbank_page._parse_key(page.transparent, pixels, alpha)

        # A wheel island is CARVED, not inherited: whatever the page art carried
        # at the island is replaced by the declared sprite. Without this the
        # wheel came from the page image, so a stale pink square in the page
        # survived every rebake no matter what sprite was declared.
        sprites = islands.get(setid) or []

        if sprites:
            mask = list(mask) if mask else [False] * len(pixels)

            for variant, sprite_path, island in sprites:
                sw, sh, sprite, sprite_alpha = carbank_page.load_pixels(sprite_path)
                transparent = carbank_page.sprite_mask(sprite, sprite_alpha)
                masked = carbank_page.composite(
                    pixels, fourbpp.PAGE_W, fourbpp.PAGE_H, sprite, sw, sh,
                    island, transparent, key if key is not None else sprite[0])

                for index in masked:
                    mask[index] = True

                warnings.append("page set %d: composited the %s wheel from %s "
                                "into island %s"
                                % (setid, variant, os.path.basename(sprite_path),
                                   tuple(island)))

        pieces = None
        background = None
        use_split = (not page.palette and carbank_page.should_split(
            pixels, fourbpp.PAGE_W, fourbpp.PAGE_H, uv_islands,
            getattr(page, "split", "auto"), getattr(page, "max_error", None)))

        if use_split:
            indices, row_map, pieces = carbank_page.plan_islands(
                pixels, fourbpp.PAGE_W, fourbpp.PAGE_H, uv_islands,
                split=True)
            blob, _ordered = carbank_page.pack_indexed(
                indices, row_map, mask=mask, key=key,
                tolerance=page.transparent_tolerance,
                chroma=page.transparent_chroma, pieces=pieces)
            colours = row_map.get(min(row_map)) if row_map else []
            entry["clut_rects"] = [{"id": piece["id"], "rect": piece["rect"]}
                                   for piece in pieces]
            ncluts = (max(row_map) + 1) if row_map else 1
            warnings.append(
                "page set %d: %d texture id(s) %s — each has its own CLUT row "
                "(one 16-entry row could not hold the snapped colours)"
                % (setid, len(row_map), sorted(row_map)))
        else:
            blob, colours, indices, _error, background = carbank_page.bake_page(
                pixels, key=key, tolerance=page.transparent_tolerance, mask=mask,
                palette=page.palette, protect=page.protect,
                chroma=page.transparent_chroma)
            entry.pop("clut_rects", None)
            ncluts = 1

        os.makedirs(os.path.dirname(want), exist_ok=True)

        with open(want, "wb") as f:
            f.write(blob)

        # The preview, right here, so it can never describe a page it was not
        # baked from. It is RGBA and paints the transparent index as alpha 0:
        # an RGB preview of this page paints that index as black, and re-baking
        # one turns the keyed backdrop back into paint.
        try:
            rows, _raw = pages1.read_page(blob, 0, len(blob))
            keyed = {background["index"]} if background else set()
            regions = None

            if pieces:
                regions = [(p["rect"][0], p["rect"][1],
                            p["rect"][2] - p["rect"][0],
                            p["rect"][3] - p["rect"][1], p["id"])
                           for p in pieces]

            exportd2.page_png(os.path.join(bank_dir, "pages",
                                           "PAGE_%d.png" % setid),
                              rows, fourbpp.decode_indices(_raw),
                              transparent=keyed, regions=regions)
        except (ValueError, struct.error) as exc:
            warnings.append("page set %d: preview not written (%s)"
                            % (setid, exc))

        entry.update({"set": setid, "size": len(blob), "ncluts": ncluts,
                      "bin": "pages/PAGE_%d.bin" % setid,
                      "source": source})
        by_set[setid] = entry

        if use_split:
            pass
        elif background is None:
            warnings.append("page set %d: re-baked from %s (no transparent "
                            "colour)" % (setid, os.path.basename(page.image)))
        else:
            folded = background.get("folded") or []
            warnings.append(
                "page set %d: re-baked from %s (transparent index %d, %d "
                "texel(s)%s)"
                % (setid, os.path.basename(page.image), background["index"],
                   background["keyed"],
                   ", folded %d near-key entr%s %s"
                   % (len(folded), "y" if len(folded) == 1 else "ies",
                      folded) if folded else ""))

            # Whatever the fold did, nothing in this row may still paint the key
            try:
                rows, _raw = pages1.read_page(blob, 0, len(blob))
                stray = carbank_page.stray_key_entries(
                    rows, key, page.transparent_tolerance,
                    page.transparent_chroma, transparent=[background["index"]])

                if stray:
                    warnings.append(
                        "page set %d: %d palette entr%s still paint%s the key "
                        "#%02x%02x%02x (indices %s) - a texel landing there "
                        "draws the backdrop"
                        % (setid, len(stray), "y" if len(stray) == 1 else "ies",
                           "s" if len(stray) == 1 else "", key[0], key[1],
                           key[2], stray))
            except (ValueError, struct.error):
                pass

    manifest["pages"] = [by_set[k] for k in sorted(by_set)]


def poly_palette_use(lev_path):
    """{(set, palette index)} that some car poly actually samples.

    A poly carries a page SET and a CLUT id; it draws index `texture_id` out of
    the row chosen for that set. So a colour can be on screen without a single
    texel painting it - the poly's own index does it.
    """
    used = set()

    try:
        import lev1

        with open(lev_path, "rb") as f:
            blob = f.read()

        got = exportd2.d2_lump(blob, exportd2.LUMP_CAR_MODELS)

        if got is None:
            return used

        body, size = got
        offsets = struct.unpack_from("<13i", blob, body)

        for offset in offsets:
            if offset < 0 or offset + 36 > size:
                continue

            model = lev1.decode_model(blob, body + offset)

            for poly in model.polys:
                if poly.kind in exportd2.TEXTURED_KINDS:
                    used.add((poly.texture_set, poly.texture_id))
    except (ImportError, ValueError, struct.error, OSError):
        pass

    return used


def audit_colour(bank_dir, manifest, colour, tolerance=0, gen_dir=None):
    """Every place a colour can still reach the screen: texels, rows, polys.

    "There is still pink on the car" has exactly three answers - the texels are
    on a page, the palette row paints them, or the poly samples that entry - and
    this prints all three rather than leaving it to guesswork. The texel test
    alone once said "not on any page" while a pink wheel was on screen, because
    the wheel's row was derived at draw time.
    """
    found = []
    key = tuple(colour)
    lev_path = os.path.join(bank_dir, os.path.basename(manifest.get("lev") or ""))
    used_by_polys = poly_palette_use(lev_path) if os.path.isfile(lev_path) else set()

    for page in manifest.get("pages") or []:
        setid = page.get("set")
        path = os.path.join(gen_dir or bank_dir, "pages", "PAGE_%d.bin" % setid)

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
        hist = {}

        for index in indices:
            hist[index] = hist.get(index, 0) + 1

        # (a) palette entries that PAINT the colour, used or not: an unused
        #     magenta entry is one texel away from being pink again
        for index in range(16):
            for r, row in enumerate(rgb_rows):
                got = row[index]

                if max(abs(got[c] - key[c]) for c in range(3)) > tolerance:
                    continue

                word = struct.unpack_from("<H", rows[r], index * 2)[0] if rows else 1

                found.append({"set": setid, "index": index, "row": r,
                              "texels": hist.get(index, 0), "colour": got,
                              "word": word, "transparent": word == 0,
                              "poly": (setid, index) in used_by_polys})

        # (b) same question for the bank's palette records, which is what the
        #     engine binds for an imported car
    pallet = _pallet_of(manifest, gen_dir or bank_dir)

    if os.path.isfile(pallet):
        try:
            with open(pallet, "rb") as f:
                pal = exportd2.d2_palette_from_bytes(f.read())

            for record in pal["records"]:
                raw = record.get("raw")

                if not raw or len(raw) < 32:
                    continue

                row = fourbpp.clut_rgb(raw[:32])

                for index in range(16):
                    got = row[index]

                    if max(abs(got[c] - key[c]) for c in range(3)) > tolerance:
                        continue

                    found.append({"set": record.get("tpageindex"),
                                  "index": index,
                                  "row": record.get("palette"),
                                  "texels": None, "colour": got,
                                  "word": struct.unpack_from("<H", raw, index * 2)[0],
                                  "transparent": False,
                                  "poly": (record.get("tpageindex"),
                                           index) in used_by_polys,
                                  "palette_record": True})
        except (ValueError, struct.error):
            pass

    return found


def _pallet_of(manifest, gen_dir):
    """The pallet.bin a manifest bakes from, or the build folder's."""
    path = (manifest.get("raw") or {}).get("pallet")
    return path if path else os.path.join(gen_dir, "pallet.bin")


def check_sources(bank, manifest, bank_dir, stale_only=True, gen_dir=None):
    """What the bake would change: a stale page source, a stale palette."""
    problems = []
    gen_dir = gen_dir or bank_dir
    islands = wheel_islands(bank)

    for carname in bank.cars:
        for page in bank.cars[carname].textures:
            record = next((p for p in (manifest.get("pages") or [])
                           if p.get("set") == page.set), None)
            want = os.path.join(gen_dir, "pages", "PAGE_%d.bin" % page.set)
            source = _page_source_hash(page, page.set, islands.get(page.set))

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

        with open(_pallet_of(manifest, gen_dir), "rb") as f:
            pal_body = f.read()

        pal = exportd2.d2_palette_from_bytes(pal_body)
        lev = os.path.join(bank_dir, os.path.basename(bank.raw.get("lev") or ""))
        pages = carbank_palette.page_sets(lev) if os.path.isfile(lev) else set()
        pages = pages or {p.get("set") for p in (manifest.get("pages") or [])}
        paints = _paints_of(bank)
        regions = {p.get("set"): p.get("clut_rects") or []
                   for p in (manifest.get("pages") or [])}
        rows = carbank_palette.rows_from_pages(gen_dir, pages, paints, regions)
        bank_rows = {(r["tpageindex"], r["palette"]): r["clut"]
                     for r in pal["records"]}

        for row in rows:
            have = bank_rows.get((row["tpageindex"], row["palette"]))

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

    pallet_bin = _pallet_of(manifest, gen_dir)
    palette_json = os.path.join(os.path.dirname(pallet_bin), "palette.json")

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


def _paints_of(bank):
    """{set: {"paints", "body", "protect"}} for every page that declares paints."""
    out = {}
    for car in bank.cars.values():
        for t in car.textures:
            if getattr(t, "paints", None):
                out[t.set] = {"paints": t.paints, "body": t.paint_body,
                              "protect": list(t.protect or [])}
    return out


def _sync_palette_from_pages(manifest, bank, bank_dir, warnings):
    """pallet.bin (and palette.json) from the pages' own row 0."""
    import carbank_palette

    # the DECLARED pages, not every entry the manifest has accumulated: a stale
    # page for a set this bank no longer carries must not add a palette row
    sets = {texture.set for car in bank.cars.values()
            for texture in car.textures}
    regions = {p["set"]: p.get("clut_rects") or []
               for p in (manifest.get("pages") or [])}
    rows = carbank_palette.rows_from_pages(bank_dir, sets, _paints_of(bank),
                                           regions)

    if not rows:
        warnings.append("palette_from_pages: no pages to take a palette from")
        return

    dump = carbank_page.clut_dump_text(rows, getattr(bank, "clut_rows", 8))
    dump_path = os.path.join(bank_dir, "clut-dump.txt")

    with open(dump_path, "w", encoding="utf-8") as f:
        f.write(dump)

    overflow = [line for line in dump.splitlines() if "OVERFLOW" in line]

    if overflow:
        warnings.append("palette: %d set(s) past civ_clut's %d rows draw the "
                        "first page's colours — see %s"
                        % (len(overflow), getattr(bank, "clut_rows", 8),
                           os.path.basename(dump_path)))
    else:
        warnings.append("palette: clut map written to %s"
                        % os.path.basename(dump_path))

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

    # the bank bakes THIS file
    manifest["raw"] = dict(manifest.get("raw") or {}, pallet=path)

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
    # perms past the 16-slot region spill into specs on the next read, so a set
    # already in EITHER list is listed - else every bake re-appends (and heals)
    seen = {setid for setid, _ in perms}
    kept = [e for e in specs if not (e[0] in seen or seen.add(e[0]))]
    changed = changed or len(kept) != len(specs)
    specs = kept
    listed = {setid for setid, _ in perms} | {setid for setid, _ in specs}
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
              flip_y=False, flip_winding=False, flip_normals=False, flip_z=False,
              force_pages=False):
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
    bank_dir, gen_dir = bank_layout(bank)
    os.makedirs(gen_dir, exist_ok=True)
    # the blocks belong in the build folder's models/, so the manifest this
    # writes still describes the bank after the bake
    blocks_dir = os.path.join(gen_dir, "models")
    # Y mirror and winding are SEPARATE: reversing the winding is how an
    # inside-out car is fixed when the mesh is already the right way up.
    # poly tints come from the OBJ/MTL (neutral grey otherwise); there is no
    # source LEV to read them back from
    colors = None
    manifest, warnings = build_manifest(bank, out_dir, scale, blocks_dir, default_page, flip_y,
                                       flip_winding,
                                       colors, flip_normals, flip_z,
                                       force_pages=force_pages)

    # build_bank resolves raw/pages/PAGE_<set>.bin relative to the MANIFEST, so
    # the manifest is written beside the bank (and removed afterwards)
    # build_bank resolves raw/pages/PAGE_<set>.bin relative to the manifest, so it
    # must sit beside the bank; writing it under the CITY name keeps the workspace
    # self-describing (a later --rebuild-bank reproduces what is installed)
    manifest_path = os.path.join(gen_dir, _manifest_name(bank))

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

    # what the bank turned out to be goes in <CITY>.report.json beside the
    # built manifest. bank.json is the author's file and the bake never writes
    # it: a computed half in there churned on every bake and buried the
    # declaration under numbers nobody edits
    _write_bake_report(manifest_path, report, warnings)

    return ok, warnings


def _write_bake_report(manifest_path, report, warnings):
    """<CITY>.report.json: the bake's verdict, sizes, sets, and its notes."""
    doc = {}

    for key, value in report.items():
        if key == "declare":
            continue

        if key in ("lev", "lcf") and isinstance(value, str):
            value = os.path.basename(value)

        doc[key] = value

    doc["bake_warnings"] = list(warnings)
    path = os.path.splitext(manifest_path)[0] + ".report.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)


def _report_only(args):
    """--check / --audit: read the workspace, write nothing."""
    bank = bankjson.load(args.bank)
    bank_dir, gen_dir = bank_layout(bank)
    manifest_path = _built_manifest(bank) or _legacy_manifest(bank)

    manifest = {}

    if manifest_path and os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

    if args.check:
        problems = check_sources(bank, manifest, bank_dir, gen_dir=gen_dir)

        for problem in problems:
            print(problem)

        if not problems:
            declared = [t for c in bank.cars.values() for t in c.textures]
            print("%d declared source(s) are current" % len(declared))
            print("  (a page with no \"source\" is a preview and is never "
                  "re-baked)")

        return 1 if problems else 0

    colour = carbank_page.hex_colour(args.audit)
    found = audit_colour(bank_dir, manifest, colour, args.audit_tolerance,
                         gen_dir=gen_dir)

    print("#%02x%02x%02x:" % colour)

    if not found:
        print("  not on any page, palette row or poly")
        return 0

    for hit in found:
        where = "palette  " if hit.get("palette_record") else "page set"
        texels = ("unused" if not hit["texels"]
                  else "%d texel(s)" % hit["texels"])

        print("  %s %3d  index %2d  row %s  %-11s #%02x%02x%02x  word %04x%s"
              "%s"
              % (where, hit["set"], hit["index"], hit["row"], texels,
                 hit["colour"][0], hit["colour"][1], hit["colour"][2],
                 hit["word"], "  TRANSPARENT" if hit["transparent"] else "",
                 "  DRAWN BY A POLY" if hit.get("poly") else ""))

    if not any(hit.get("poly") for hit in found):
        print("  no poly samples any of those entries, so this colour is not "
              "currently on screen")

    return 0


SOUND_ROLES = ("engine", "idle")


def _completed_sample(role, entry, sounds):
    """One sample's long form: the path, plus every knob it did not state.

    Defaults come from the `sounds` block first (a car may set `peak` or
    `loop_from` once) and from bankjson.SOUND_SAMPLE_DEFAULTS otherwise, so what
    is written here is exactly what carbank_bake._resolved_sample would derive.
    """
    before = dict(entry) if isinstance(entry, dict) else {"wav": entry}
    out = dict(before)
    block = sounds

    if out.get("level") is None:
        out["level"] = (block.get("peak")
                        if block.get("peak") is not None
                        else bankjson.SOUND_DEFAULTS["peak"])

    if out.get("rms") is None:
        out["rms"] = (block.get("rms")
                      if block.get("rms") is not None
                      else bankjson.SOUND_DEFAULTS["rms"])

    if out.get("loop_from") is None:
        out["loop_from"] = (block.get("loop_from")
                            if block.get("loop_from") is not None
                            else bankjson.SOUND_DEFAULTS["loop_from"])

    if out.get("loop") is None:
        out["loop"] = role in SOUND_ROLES

    if out.get("loop_to") is None:
        out["loop_to"] = None

    if out.get("pitch") is None:
        out["pitch"] = bankjson.SOUND_SAMPLE_DEFAULTS["pitch"]

    for key, value in bankjson.SOUND_SAMPLE_DEFAULTS.items():
        if key not in out:
            out[key] = value

    # a rate is either an exact frequency or a percentage of the source, and the
    # percentage lives at the sounds level for the two looping samples
    pct = block.get(role + "_pct")

    if out.get("rate") is not None:
        # a frequency and a percentage would fight: the frequency wins, and the
        # percentage is left out rather than written as a contradiction
        out.pop("rate_pct", None)
    elif pct is not None:
        out["rate_pct"] = pct

    return before, out


def complete_declaration(js, out=None, dry_run=False):
    """Fill every missing sound knob in a bank.json, leaving values alone.

    A declaration may legally say `"engine": "sounds/engine.wav"` and nothing
    else - the bake fills in the rate, the level, the loop, the byte budget.
    That is convenient to write and impossible to check by reading, so this
    writes the whole set out: the short form becomes
    `{"wav": ..., "level": 0.999, "rate": null, ...}` and the block gains
    `peak` / `max_rate` / `sample_bytes`. Re-baking afterwards is a no-op, which
    is what proves the numbers written are the ones the bake used.
    """
    with io.open(js, "r", encoding="utf-8") as f:
        raw = json.load(f)

    declare = raw.get("declare") or {}
    cars = declare.get("cars") or {}
    added = []

    for carname, car in cars.items():
        if not isinstance(car, dict):   # car in its own file: not completed
            continue
        sounds = car.get("sounds")

        if not isinstance(sounds, dict):
            continue

        for key, value in bankjson.SOUND_DEFAULTS.items():
            if sounds.get(key) is None:
                sounds[key] = value
                added.append("%s.sounds.%s" % (carname, key))

        if sounds.get("cop") is None:
            sounds["cop"] = False
            added.append("%s.sounds.cop" % carname)

        cop = bool(sounds.get("cop"))

        for role in ("engine", "idle", "siren" if cop else "horn"):
            entry = sounds.get(role)

            if entry is None:
                if role == "engine":
                    added.append("%s.sounds.engine (REQUIRED - the bake skips "
                                 "this car until it is declared)" % carname)
                elif role == "idle":
                    added.append("%s.sounds.idle (optional: the engine loop "
                                 "doubles as the idle)" % carname)
                elif role == "horn":
                    added.append("%s.sounds.horn (REQUIRED for a civilian car)"
                                 % carname)
                else:
                    added.append("%s.sounds.siren (REQUIRED for a cop car)"
                                 % carname)
                continue

            before, after = _completed_sample(role, entry, sounds)

            for key in bankjson.SOUND_SAMPLE_KEYS:
                if key not in before:
                    added.append("%s.sounds.%s.%s" % (carname, role, key))

            # keep the key order stable: wav first, then the knobs
            ordered = {"wav": after["wav"]}

            for key in ("level", "rms", "force", "normalise", "gain_db",
                        "rate", "rate_pct", "pitch", "loop", "loop_from",
                        "loop_to", "trim", "fade_out"):
                if key in after:
                    ordered[key] = after[key]

            sounds[role] = ordered

    if not added:
        print("%s: every sound knob is already stated"
              % os.path.basename(js))
        return 0

    if dry_run:
        print("%s: would fill in %d entry(ies):" % (os.path.basename(js),
                                                    len(added)))

        for name in added:
            print("  " + name)

        return 0

    target = out or js

    with io.open(target, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=1)
        f.write("\n")

    print("%s: filled in %d entry(ies)" % (os.path.basename(target), len(added)))

    for name in added:
        print("  " + name)

    bank = bankjson.load(target)
    print("  re-read clean: %d car(s) declare sounds"
          % len([c for c in bank.cars.values() if c.sounds]))

    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bank", help="the bank.json with the declare block")
    ap.add_argument("-o", "--out", help="output folder for the LEV/LCF "
                                        "(default: the bank's build_dir, or the "
                                        "bank folder in the flat layout)")
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
    ap.add_argument("--force-pages", action="store_true",
                    help="re-bake every declared page even when its source, its "
                         "bake options and its wheel sprites are unchanged")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; report page/palette sources that are stale")
    ap.add_argument("--complete", action="store_true",
                    help="write every sound knob the bake would assume into "
                         "bank.json (an unchanged bake stays byte-identical)")
    ap.add_argument("--complete-out", metavar="FILE", default=None,
                    help="with --complete, write the finished declaration here "
                         "instead of over the bank.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --complete, list what would be added and stop")
    ap.add_argument("--audit", metavar="RRGGBB", default=None,
                    help="write nothing; report where a colour still lives (page, "
                         "texel count, palette index, CLUT word)")
    ap.add_argument("--audit-tolerance", type=int, default=8, metavar="N",
                    help="per-channel slack for --audit (default 8)")
    args = ap.parse_args(argv)

    if args.complete:
        return complete_declaration(os.path.abspath(args.bank),
                                    args.complete_out, args.dry_run)

    if args.check or args.audit:
        return _report_only(args)

    if args.d1:
        raise SystemExit("carbank_bake: --d1 is not implemented in this tool; "
                         "the Driver 1 pool bake is tools/car/bake.py")

    default_page = None
    if args.default_page:
        s_, i_ = args.default_page.split(":")
        default_page = (int(s_), int(i_))

    if not args.out:
        args.out = bank_layout(bankjson.load(args.bank))[1]

    ok, warnings = bake_bank(args.bank, os.path.abspath(args.out), args.scale,
                             default_page=default_page,
                             flip_y=args.flip_y, flip_winding=args.flip_winding,
                             flip_normals=args.flip_normals, flip_z=args.flip_z,
                             force_pages=args.force_pages)

    print("baked %s -> %s" % (os.path.basename(args.bank), args.out))

    for w in warnings:
        _warn(w)

    if not ok:
        print("carbank_bake: FAILED - the bank is unusable, see the check "
              "problems above", file=sys.stderr)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
