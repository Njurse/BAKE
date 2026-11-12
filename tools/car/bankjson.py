#!/usr/bin/env python3
"""bankjson.py - read and validate the `declare` half of a bank's bank.json.

The schema is documented in bank-schema.md next to this file. This module is the
only reader: it fails hard, naming the file and the field, rather than warning
and guessing. A bank with no `declare` block is a legacy (pre-generative) bank
and loads as "nothing declared".

    import bankjson
    bank = bankjson.load("banks/firebird/bank.json")
    if bank.declared:
        for car in bank.cars: ...
"""

import json
import os

SCHEMA = 1

# One stock car-select portrait: a 256x256 4bpp page (the slot SetupExtraPoly
# uploads from CCARS.RAW), plus an optional 16-colour RGB555 CLUT.
PORTRAIT_BYTES = 0x8000
PORTRAIT_CLUT_BYTES = 32
# A portrait with one of these extensions is encoded at bake time (portrait.py).
# Anything else is a pre-packed 4bpp page and is copied unchanged. Same Pillow
# load a car texture uses when the art is not already a packed page.
PORTRAIT_RASTER_EXTS = (".png", ".tga", ".bmp")

CAR_KEYS = {
    "clean", "damaged", "low", "atlas", "textures", "wheels",
    "sounds", "palette", "remap", "portrait",
}
# A car's sounds: three samples (engine, idle, horn-or-siren) plus the knobs
# the bake applies to them. Every sample can be a bare path - the short form
# almost every bank uses - or an object carrying that path as `wav` plus its own
# settings, so one car can trim its loop while another leaves its alone.
#
# The values below are ALL spelling out what the bake does when a key is absent:
# carbank_bake --complete writes them into bank.json so a hand-edited bank says
# out loud what it is getting, and filling them in by hand reproduces the bake
# exactly (SOUND_DEFAULTS / SOUND_SAMPLE_DEFAULTS are the one source for both).
SOUND_KEYS = {"engine", "idle", "horn", "siren", "cop", "engine_pct", "idle_pct",
              "loop_from", "peak", "rms", "max_rate", "sample_bytes"}
SOUND_SAMPLE_KEYS = {"wav", "level", "rms", "force", "normalise", "gain_db",
                     "rate", "rate_pct", "pitch", "loop", "loop_from",
                     "loop_to", "trim", "fade_out"}

# bank-wide defaults (a car may override the per-sample ones per sample)
SOUND_DEFAULTS = {
    "engine_pct": 100,
    "idle_pct": 100,
    "loop_from": 0.0,
    "peak": 0.999,      # the ceiling: retail's own banks peak at 100%
    "rms": 0.34,        # the loudness: retail measures 29-39% rms, mid 34%
    "max_rate": 16000,  # retail's rate, and the SPU budget is sized for it
    "sample_bytes": 16384,
}

# per-sample defaults; `loop` is decided by the sample's ROLE (the third sample
# is a one-shot horn, the other two are loops), so it is not listed here.
SOUND_SAMPLE_DEFAULTS = {
    "level": None,      # None = the bank's `peak` ceiling
    "rms": None,        # None = the bank's `rms` loudness target
    "force": False,     # scale even inside the level dead band
    "normalise": True,  # False = keep the source level; gain_db only
    "gain_db": 0.0,
    "rate": None,       # None = the source rate, capped at max_rate
    "pitch": 1.0,       # the rate field only: how the GAME's pitch request
                        # lands. gamesnd.c asks 0.37x..0.62x of it for the
                        # rev sample and ~1.0x for the idle.
    "rate_pct": 100,
    "loop_from": None,  # None = the sounds-level loop_from
    "loop_to": None,    # None = the loop runs to the end of the sample
    "trim": None,       # [start, end] seconds
    "fade_out": 0.0,
}
WHEEL_KEYS = {"clean", "damaged", "fast"}
WHEEL_ENTRY_KEYS = {"png", "set", "island"}
WHEEL_VARIANTS = ("clean", "damaged", "fast")

# a declared page describes its own BAKE, not just an image: the refresh path has
# to reproduce the page from the declaration alone
TEXTURE_KEYS = {"png", "transparent", "transparent_tolerance",
                "transparent_chroma", "palette", "protect",
                "paints", "paint_body", "body_rows", "split", "max_error"}
PAINT_IMAGE_KEYS = {"image", "id", "panel"}

# A page is 256x256 texels, and the UVs that name a crop are unsigned bytes, so a
# crop rect runs 0..256 in each axis (u1/v1 EXCLUSIVE, like PIL's crop box).
PAGE_TEXELS = 256

MAX_SLOT = 12


class BankError(Exception):
    """A bank.json that cannot be used, with the file and field named."""

# Folders the BAKE writes into. A declaration that points at one of these is
# pointing at an artefact of a previous bake, not at art.
OUTPUT_DIRS = ("work", "pages", "build")

# The default `transparent_tolerance` for a page that declares a key. Rendered
# art never uses one exact backdrop colour, and the antialiased ring around a
# sprite is where the "leftover pink" lives - see carbank_page.key_mask.
DEFAULT_KEY_TOLERANCE = 96


def _check_not_output(where, field, rel, base):
    """Refuse a source under a folder the bake writes (see OUTPUT_DIRS)."""
    if not rel:
        return

    parts = [p.lower() for p in
             os.path.normpath(rel).replace("\\", "/").split("/")]

    for folder in OUTPUT_DIRS:
        if folder in parts[:-1]:
            _fail(where, field,
                  "%r is inside %s/, which the bake writes - a source there is a "
                  "previous bake's output, so an edit to it would be overwritten "
                  "and the hash would still look current. Point the declaration "
                  "at the art instead" % (rel, folder))

    """A bank.json that cannot be used, with the file and field named."""


def _fail(where, field, why):
    raise BankError("%s: %s: %s" % (where, field, why))


def _check_keys(where, field, obj, allowed, what):
    for key in obj:
        if key not in allowed:
            _fail(where, field + "." + str(key),
                  "unknown %s key (allowed: %s)"
                  % (what, ", ".join(sorted(allowed))))


def _bake_scale(where, field, value):
    """A vertex multiplier. 1 is a Driver 2 OBJ; 2048/3000 is a Driver 1 OBJ."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(where, field, "%r is not a scale" % (value,))
    if not 0.05 <= float(value) <= 16:
        _fail(where, field, "%r is outside 0.05..16" % (value,))
    return float(value)


class Car(object):
    """One car's declared sources (cars/<id>/handling.json)."""

    def __init__(self, label, path, data, base, where):
        self.label = label
        self.path = path
        self.base = base
        where = "%s: %s" % (path, label)
        self.clean = self._file(where, "clean", data.get("clean"), required=True)
        self.damaged = self._file(where, "damaged", data.get("damaged"))
        self.low = self._file(where, "low", data.get("low"))
        self.atlas = self._file(where, "atlas", data.get("atlas"))

        # TEXTURES: set id -> the image that page is baked from, plus how.
        #
        # There is no `pages` in a declaration, and that is the point: a page's
        # PNG preview is a product of the bake, so naming it here would make the
        # output an input (and re-baking from a preview is generation loss). The
        # images in the car json ARE the source of truth; the manifest's pages[]
        # is the computed record of what they produced.
        if "pages" in data:
            _fail(where, "pages",
                  "a declaration names TEXTURES, not pages: keys are set ids, "
                  "e.g. \"textures\": {\"1\": \"textures/car.png\"}. A page's "
                  "png is a bake output and is not an input.")

        self.textures = []
        textures = data.get("textures") or {}

        if not isinstance(textures, dict):
            _fail(where, "textures", "must be an object of set id -> image")

        for key, value in sorted(textures.items()):
            try:
                setid = int(str(key), 0)
            except ValueError:
                _fail(where, "textures.%s" % key,
                      "is not a page set id (keys are set ids, e.g. \"1\")")

            if not 0 <= setid <= 255:
                _fail(where, "textures.%s" % key, "is not a set id 0..255")

            self.textures.append(Texture(where, setid, value, self._file))

        self._wire_author_pages(where)

        if not self.atlas and not self.textures:
            _fail(where, "atlas", "a car needs an atlas or a textures map")

        self.wheels = {}
        wheels = data.get("wheels") or {}

        if not isinstance(wheels, dict):
            _fail(where, "wheels", "must be an object")

        _check_keys(where, "wheels", wheels, WHEEL_KEYS, "wheels")

        for variant in WHEEL_VARIANTS:
            # deliberately allowed to be absent: the engine keeps the host's
            # wheel texture for a variant the bank does not carry
            self.wheels[variant] = _wheel(where, "wheels.%s" % variant,
                                          wheels.get(variant), self._file)

        self.sounds = None
        sounds = data.get("sounds")

        if sounds is not None:
            self.sounds = Sounds(where, sounds, self._file)

        self.palette_source = None
        palette = data.get("palette") or {}

        if not isinstance(palette, dict):
            _fail(where, "palette", "must be an object")

        _check_keys(where, "palette", palette, {"source", "row"}, "palette")
        self.palette_source = self._file(where, "palette.source",
                                         palette.get("source"))
        self.palette_row = palette.get("row", 0)

        self.remap = {"set": {}, "id": {}}
        remap = data.get("remap") or {}

        if not isinstance(remap, dict):
            _fail(where, "remap", "must be an object")

        _check_keys(where, "remap", remap, {"set", "id"}, "remap")

        for kind in ("set", "id"):
            table = remap.get(kind) or {}

            if not isinstance(table, dict):
                _fail(where, "remap.%s" % kind, "must be an object")

            for src, dst in table.items():
                try:
                    self.remap[kind][int(src)] = int(dst)
                except (TypeError, ValueError):
                    _fail(where, "remap.%s" % kind,
                          "%r -> %r is not a pair of integers" % (src, dst))

        # Optional car-select portrait. Authors set this on handling.json; the
        # path is relative to that file. The bake packs the page into <stem>.RAW
        # at this car's model slot and also writes the loose page.
        #
        # A .png / .tga / .bmp is encoded here (portrait.encode_path): a mode-P
        # image already on the CARBACK portrait CLUT is packed by index, and
        # any other raster is remapped onto that CLUT. fourbpp RGB555 rounding
        # is not involved. A pre-packed page is copied as-is, and a sibling
        # "<name>.clut" of 32 bytes is its private CLUT. A raster is drawn
        # with the stock CLUT, so a sibling .clut beside one is not copied.
        # The RAW does not carry CLUTs either way.
        portrait_rel = data.get("portrait")

        if portrait_rel not in (None, ""):
            _check_not_output(where, "portrait", portrait_rel, self.base)

        self.portrait = self._file(where, "portrait", portrait_rel)
        self.portrait_clut = None
        self.portrait_page = None

        if self.portrait is not None:
            if _portrait_is_raster(self.portrait):
                import portrait

                try:
                    self.portrait_page = portrait.encode_path(self.portrait)
                except portrait.PortraitError as exc:
                    _fail(where, "portrait", str(exc))
                except ImportError:
                    _fail(where, "portrait",
                          "reading %s needs Pillow, the same loader a car "
                          "texture page uses"
                          % os.path.basename(self.portrait))
            else:
                size = os.path.getsize(self.portrait)

                if size != PORTRAIT_BYTES:
                    _fail(where, "portrait",
                          "%s is %d bytes; a pre-packed portrait is one 4bpp "
                          "page, exactly %d bytes (256x256, the top 219 rows "
                          "show). A .png, .tga, or .bmp is encoded at bake "
                          "time instead"
                          % (os.path.basename(self.portrait), size,
                             PORTRAIT_BYTES))

                clut = os.path.splitext(self.portrait)[0] + ".clut"

                if os.path.isfile(clut):
                    csize = os.path.getsize(clut)

                    if csize != PORTRAIT_CLUT_BYTES:
                        _fail(where, "portrait",
                              "%s is %d bytes; the optional CLUT beside a "
                              "portrait is %d bytes (16 little-endian RGB555 "
                              "colours)"
                              % (os.path.basename(clut), csize,
                                 PORTRAIT_CLUT_BYTES))

                    self.portrait_clut = clut

                with open(self.portrait, "rb") as src:
                    self.portrait_page = src.read()

    def _wire_author_pages(self, where):
        """Declare textures{N} for every PAGE_N material the car's OBJs use.

        An author names a Blender material PAGE_N after its image; the bake
        takes that as the whole of page N (obj2geom.material_to_page). When
        textures{} already has N, that entry wins. Otherwise textures/PAGE_N.png
        (or PAGE_N.png beside the OBJ) is wired in; with neither it is an error
        that says what to add.
        """
        import obj2geom

        have = {t.set for t in self.textures}
        used = set()

        for obj in (self.clean, self.damaged, self.low):
            if not obj:
                continue
            with open(obj, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("usemtl"):
                        page = obj2geom.page_of_author_material(
                            line[6:].strip())
                        if page is not None:
                            used.add(page)

        for page in sorted(used - have):
            for rel in ("textures/PAGE_%d.png" % page, "PAGE_%d.png" % page):
                if os.path.isfile(os.path.join(self.base, rel)):
                    self.textures.append(Texture(where, page, {"png": rel},
                                                 self._file))
                    break
            else:
                _fail(where, "textures.%d" % page,
                      "a material is named PAGE_%d but there is no "
                      "textures/PAGE_%d.png and no textures.%d entry - add "
                      "\"textures\": {\"%d\": \"textures/<your png>\"}"
                      % (page, page, page, page))

        self.textures.sort(key=lambda t: t.set)

    def _file(self, where, field, rel, required=False):
        if rel is None or rel == "":
            if required:
                _fail(where, field, "is required")
            return None

        if not isinstance(rel, str):
            _fail(where, field, "must be a path string")

        full = os.path.normpath(os.path.join(self.base, rel))

        if not os.path.isfile(full):
            _fail(where, field, "%s does not exist (looked at %s)" % (rel, full))

        return full


class Texture(object):
    """One page's image and how to bake it: `textures[set]` in the declaration.

    The image IS the declaration - there is no separate preview path, because a
    preview is what the bake writes back and re-baking from it is generation
    loss. The bake's own record of the page (its size, its palette row, the
    sha256 it was baked from, its preview PNG) lives in the manifest instead.

    A set's entry may be a path, or an object with that path as `png` plus the
    choices the bake cannot guess: which colour is the transparent backdrop, how
    wide the match is, which 16 colours to keep.
    """

    def __init__(self, where, setid, data, file_of):
        field = "textures.%d" % setid
        self.set = setid

        if isinstance(data, str):
            data = {"png": data}

        if not isinstance(data, dict):
            _fail(where, field, "must be a path or an object with a png")

        _check_keys(where, field, data, TEXTURE_KEYS, "texture")

        _check_not_output(where, field + ".png", data.get("png"), None)
        self.image = file_of(where, field + ".png", data.get("png"),
                             required=True)
        self.transparent = data.get("transparent")
        self.transparent_tolerance = data.get("transparent_tolerance")
        self.transparent_chroma = data.get("transparent_chroma")

        if self.transparent_tolerance is None:
            # A keyed page almost always wants a band, not an exact match: the
            # blend ring round a sprite is a gradient, and leaving it painted is
            # the "still a few pink pixels" report. 96 is what carbank_page's
            # own help recommends for magenta; declared values still win, and an
            # explicit 0 means "exactly this colour and nothing else".
            self.transparent_tolerance = (
                DEFAULT_KEY_TOLERANCE
                if self.transparent is not None else 0)
        self.palette = data.get("palette")
        self.protect = data.get("protect") or []

        if (not isinstance(self.transparent_tolerance, int)
                or not 0 <= self.transparent_tolerance <= 255):
            _fail(where, field + ".transparent_tolerance",
                  "%r is not 0..255" % (self.transparent_tolerance,))

        if self.protect and not isinstance(self.protect, list):
            _fail(where, field + ".protect", "must be a list of hex colours")

        self.protect = [_hex_colour(where, field + ".protect", colour)
                        for colour in self.protect]

        # Colour variants (civ_clut columns 1..4): {"name": "rrggbb", ...} in
        # column order. Column 0 is always the page's own colours. Only the
        # body entries of the page row are recoloured (paint_body, or every
        # mid/light blue by default); protect colours and 0x0000 never move.
        paints = data.get("paints") or {}
        if not isinstance(paints, dict):
            _fail(where, field + ".paints",
                  "must be an object of name -> rrggbb or {image: path}")
        if len(paints) > 5:
            _fail(where, field + ".paints",
                  "at most 5 variants (civ_clut columns 1..5), not %d" % len(paints))
        self.paints = [(str(name), _paint_spec(where, field + ".paints." + str(name),
                                               spec, file_of))
                       for name, spec in paints.items()]
        split = data.get("split", "auto")
        if split is True:
            split = "auto"
        if split is False:
            split = "off"
        if split not in ("auto", "off"):
            _fail(where, field + ".split",
                  "%r is not \"auto\" or \"off\"" % (split,))
        self.split = split
        self.max_error = data.get("max_error")
        if (self.max_error is not None and
                (not isinstance(self.max_error, int) or
                 not 0 <= self.max_error <= 255)):
            _fail(where, field + ".max_error",
                  "%r is not a channel error 0..255" % (self.max_error,))
        body = data.get("paint_body")
        if body is not None and not isinstance(body, list):
            _fail(where, field + ".paint_body", "must be a list of hex colours")
        self.paint_body = (None if body is None else
                           [_hex_colour(where, field + ".paint_body", c) for c in body])

        # Page CLUT indices that are one body colour, in swap order. The first
        # is the texture id whose row is already civ_clut slot 0. The rest fill
        # slots 1..5 (LUMP_PALLET palette 0..4). Omit it and rows that recolour
        # an earlier row are detected. A one-element list turns detection off.
        raw_rows = data.get("body_rows")
        self.body_rows = None

        if raw_rows is not None:
            if (not isinstance(raw_rows, list) or not raw_rows or
                    not all(isinstance(v, int) and not isinstance(v, bool)
                            and 0 <= v <= 31 for v in raw_rows)):
                _fail(where, field + ".body_rows",
                      "must be a list of CLUT row indices 0..31 "
                      "(first is the base texture id)")

            self.body_rows = [int(v) for v in raw_rows]

        if self.palette is not None:
            if not isinstance(self.palette, list):
                _fail(where, field + ".palette",
                      "must be a list of 16 hex colours (a fixed palette)")

            if len(self.palette) != 16:
                _fail(where, field + ".palette",
                      "a fixed palette is exactly 16 colours, not %d"
                      % len(self.palette))

            self.palette = [_hex_colour(where, field + ".palette", colour)
                            for colour in self.palette]

    def bake_options(self):
        """What the page bake needs, from the declaration alone."""
        return {"transparent": self.transparent,
                "tolerance": self.transparent_tolerance,
                "chroma": self.transparent_chroma,
                "palette": self.palette,
                "protect": list(self.protect or []),
                "split": self.split,
                "max_error": self.max_error}


def _paint_spec(where, field, spec, file_of):
    """A paint is a hex colour, or a paletted image that supplies the new CLUT.

    The image is a recolour of the same atlas (or of one panel rectangle): each
    source index takes the colour the image shows at those texels. `id` limits
    the paint to one texture id; `panel` is the exclusive rect the image covers
    when it is not a full page.
    """
    if isinstance(spec, str):
        return {"rgb": _hex_colour(where, field, spec)}

    if not isinstance(spec, dict):
        _fail(where, field, "must be a hex colour or {\"image\": path}")

    _check_keys(where, field, spec, PAINT_IMAGE_KEYS, "paint")

    if not spec.get("image"):
        _fail(where, field, "an image paint needs \"image\"")

    panel = spec.get("panel")

    if panel is not None:
        if (not isinstance(panel, (list, tuple)) or len(panel) != 4 or
                not all(isinstance(v, int) for v in panel)):
            _fail(where, field + ".panel",
                  "must be [x0, y0, x1, y1] in page texels")

    tex_id = spec.get("id")

    if tex_id is not None and (not isinstance(tex_id, int) or not 0 <= tex_id <= 31):
        _fail(where, field + ".id", "%r is not a texture id 0..31" % (tex_id,))

    return {"image": file_of(where, field + ".image", spec.get("image"),
                             required=True),
            "id": tex_id,
            "panel": None if panel is None else [int(v) for v in panel]}


def _hex_colour(where, field, text):
    """Validate '#rrggbb' / 'rrggbb' and return (r, g, b)."""
    value = str(text).strip().lower()
    digits = value[1:] if value.startswith("#") else value

    if len(digits) != 6:
        _fail(where, field, "%r is not a hex colour (rrggbb)" % (text,))

    try:
        return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        _fail(where, field, "%r is not a hex colour (rrggbb)" % (text,))


class Wheel(object):
    """One declared wheel variant: the art, the page it is cropped from, and the
    crop rect on that page.

    A car's `wheels` block is THAT car's set: each car in a bank may name its own
    (carbank_bake.wheel_specs keeps them apart and the bake writes a per-model
    LUMP_CAR_WHEELS2), and a car that names none inherits the first declared set
    in slot order.

    `set` is what lets the bank carry a wheel MODEL. The engine keeps the host
    level's wheel mesh and reads the imported wheel only for its page and its
    crop rect (models.c BuildCarImportWheels: poly 2's texture_set is the page,
    its UVs the rect, and the first eight polys must all be FT4), so a wheel
    image with no page named cannot be carried at all - that variant keeps the
    host's wheel texture instead.

    `island` is (u0, v0, u1, v1) in page texels, u1/v1 EXCLUSIVE - the same
    rectangle PIL's crop() takes, so a 60x60 sprite at the page origin is
    (0, 0, 60, 60). Left unset, the bake uses the image's own size at the origin.

    `"island": "auto"` hands the choice to the bake: it places the sprite in the
    first free rect on `set` (row-major, 8-texel steps, after the body's UV
    islands and the wheels already placed there), moves it to another page the
    bank carries when that page is full, and stops with a per-page inventory when
    nothing has room. The resolved rect is written into the manifest, so a re-bake
    and an export both keep the same coordinates.

    Whatever the source of the rect, the bake refuses ambiguity: a wheel island
    that overlaps another car's sprite, or the body art on that page, fails with
    the car, variant, page and rectangles named; two cars naming one rect with
    different art is refused too, while a single car's clean/fast pair may share
    a rect (the same tyre redrawn).
    """

    def __init__(self, path, setid, island):
        self.path = path
        self.set = setid
        self.island = island


def _wheel(where, field, data, file_of):
    """One `wheels.<variant>` entry: a path, or {png, set, island}."""
    if data is None or data == "":
        return None

    if isinstance(data, str):
        # the plain form: art but no page, so no wheel model (see Wheel)
        _check_not_output(where, field, data, None)
        return Wheel(file_of(where, field, data), None, None)

    if not isinstance(data, dict):
        _fail(where, field, "must be a path string or {png, set, island}")

    _check_keys(where, field, data, WHEEL_ENTRY_KEYS, "wheel")
    # png is how the crop rect is measured, so it may be left out ONLY when the
    # island is stated - a declaration read back out of a bank names the rect and
    # has no image file to point at
    path = file_of(where, field + ".png", data.get("png"))
    _check_not_output(where, field + ".png", data.get("png"), None)

    setid = data.get("set")

    if setid is not None and (not isinstance(setid, int)
                              or not 0 <= setid <= 255):
        _fail(where, field + ".set",
              "%r is not a page set id 0..255" % (setid,))

    island = data.get("island")

    if island == "auto":
        # "auto": the bake places this sprite in the first free rect on `set`,
        # after subtracting the body art and the wheels already placed there
        return Wheel(path, setid, "auto")

    if island is not None:
        if not isinstance(island, (list, tuple)) or len(island) != 4:
            _fail(where, field + ".island",
                  "must be four texel bounds [u0, v0, u1, v1]")

        for value in island:
            if not isinstance(value, int) or not 0 <= value <= PAGE_TEXELS:
                _fail(where, field + ".island",
                      "%r is not a texel bound 0..%d" % (value, PAGE_TEXELS))

        u0, v0, u1, v1 = island

        if u1 <= u0 or v1 <= v0:
            _fail(where, field + ".island",
                  "%r is empty (u1/v1 are exclusive, so u1 > u0 and v1 > v0)"
                  % (island,))

        island = tuple(island)

    if setid is not None and island is None and path is None:
        _fail(where, field,
              "names a page but no crop rect: give \"island\", or a \"png\" to "
              "measure one from")

    return Wheel(path, setid, island)


class Sounds(object):
    """A car's declared samples, already validated against the cop rule."""

    def __init__(self, where, data, file_of):
        if not isinstance(data, dict):
            _fail(where, "sounds", "must be an object")

        _check_keys(where, "sounds", data, SOUND_KEYS, "sounds")

        self.cop = bool(data.get("cop", False))
        self.engine = self._sample(where, "engine", data.get("engine"),
                                   file_of, required=True)
        self.idle = self._sample(where, "idle", data.get("idle"), file_of)
        self.horn = self._sample(where, "horn", data.get("horn"), file_of)
        self.siren = self._sample(where, "siren", data.get("siren"), file_of)
        self.engine_pct = data.get("engine_pct")
        self.idle_pct = data.get("idle_pct")
        # seconds into the sample where the loop starts; a car engine
        # loop with an attack otherwise repeats the attack every cycle
        self.loop_from = data.get("loop_from")
        self.peak = data.get("peak")
        self.rms = data.get("rms")
        self.max_rate = data.get("max_rate")
        self.sample_bytes = data.get("sample_bytes")

        for name in ("engine_pct", "idle_pct"):
            pct = getattr(self, name)

            if pct is not None and (not isinstance(pct, (int, float))
                                    or not 10 <= pct <= 400):
                _fail(where, "sounds.%s" % name,
                      "%r is not a percentage between 10 and 400" % (pct,))

        if self.peak is not None and (not isinstance(self.peak, (int, float))
                                      or not 0.05 <= self.peak <= 1.0):
            _fail(where, "sounds.peak",
                  "%r is not a target level between 0.05 and 1.0 - a bank "
                  "sample has no gain of its own, so this IS its volume"
                  % (self.peak,))

        if self.rms is not None and (not isinstance(self.rms, (int, float))
                                     or not 0 <= self.rms <= 1.0
                                     or 0 < self.rms < 0.05):
            _fail(where, "sounds.rms",
                  "%r is not a loudness target: 0 (peak only) or 0.05-1.0 of "
                  "full scale, where retail's own car samples measure 0.29-0.39"
                  % (self.rms,))

        if self.max_rate is not None and (not isinstance(self.max_rate, int)
                                          or not 4000 <= self.max_rate <= 44100):
            _fail(where, "sounds.max_rate",
                  "%r is not a sample rate between 4000 and 44100"
                  % (self.max_rate,))

        if self.sample_bytes is not None and (
                not isinstance(self.sample_bytes, int)
                or not 1024 <= self.sample_bytes <= 65536):
            _fail(where, "sounds.sample_bytes",
                  "%r is not a per-sample byte budget between 1024 and 65536"
                  % (self.sample_bytes,))

        if self.cop:
            if self.siren is None:
                _fail(where, "sounds.siren",
                      "a cop car needs a siren (it takes the third sample)")
            if self.horn is not None:
                _fail(where, "sounds.horn",
                      "a cop car uses the siren as its third sample; declaring "
                      "a horn as well hides which one plays")
        else:
            if self.horn is None:
                _fail(where, "sounds.horn",
                      "a civilian car needs a horn (declare sounds.cop = true "
                      "for a police car)")
            if self.siren is not None:
                _fail(where, "sounds.siren",
                      "a siren on a civilian car would never play; set "
                      "sounds.cop = true if this is a police car")

    def _sample(self, where, name, value, file_of, required=False):
        """A sample as its full spec: the path plus every knob, defaults filled.

        The short form (`"engine": "sounds/engine.wav"`) and the long form
        (`"engine": {"wav": ..., "level": 0.8}`) both end up here, so nothing
        downstream cares which one a bank used.
        """
        if value is None or value == "":
            if required:
                _fail(where, "sounds." + name, "is required")
            return None

        if isinstance(value, str):
            entry = {"wav": value}
        elif isinstance(value, dict):
            entry = dict(value)
            _check_keys(where, "sounds." + name, entry, SOUND_SAMPLE_KEYS,
                        "sample")
        else:
            _fail(where, "sounds." + name,
                  "must be a path or an object with that path as `wav`")

        if entry.get("wav") is None:
            _fail(where, "sounds.%s.wav" % name, "is required")

        field = "sounds." + name
        path = file_of(where, field + ".wav", entry["wav"], required=True)
        level = entry.get("level")
        rms = entry.get("rms")
        rate = entry.get("rate")
        trim = entry.get("trim")
        fade = entry.get("fade_out")
        gain = entry.get("gain_db")
        pct = entry.get("rate_pct")

        if level is not None and (not isinstance(level, (int, float))
                                  or not 0.05 <= level <= 1.0):
            _fail(where, field + ".level",
                  "%r is not a target level between 0.05 and 1.0" % (level,))

        if rms is not None and (not isinstance(rms, (int, float))
                                or not 0 <= rms <= 1.0
                                or 0 < rms < 0.05):
            _fail(where, field + ".rms",
                  "%r is not a loudness target: 0 (peak only) or 0.05-1.0"
                  % (rms,))

        pitch = entry.get("pitch")

        if pitch is not None and (not isinstance(pitch, (int, float))
                                  or not 0.25 <= pitch <= 4.0):
            _fail(where, field + ".pitch",
                  "%r is not a pitch factor between 0.25 and 4.0 (it scales the "
                  "declared rate, so the game's own pitch request lands where "
                  "the sample was recorded)" % (pitch,))

        if rate is not None and (not isinstance(rate, int)
                                 or not 4000 <= rate <= 44100):
            _fail(where, field + ".rate",
                  "%r is not a sample rate between 4000 and 44100" % (rate,))

        if pct is not None and (not isinstance(pct, (int, float))
                                or not 10 <= pct <= 400):
            _fail(where, field + ".rate_pct",
                  "%r is not a percentage between 10 and 400" % (pct,))
        elif pct is not None and rate is not None and pct != 100:
            _fail(where, field + ".rate",
                  "set either rate (a frequency) or rate_pct (a percentage of "
                  "the source), not both")

        if gain is not None and (not isinstance(gain, (int, float))
                                 or not -60 <= gain <= 24):
            _fail(where, field + ".gain_db",
                  "%r is not a gain between -60 and +24 dB" % (gain,))

        if fade is not None and (not isinstance(fade, (int, float))
                                 or not 0 <= fade <= 5):
            _fail(where, field + ".fade_out",
                  "%r is not a fade length between 0 and 5 seconds" % (fade,))

        if trim is not None:
            if (not isinstance(trim, list) or not 1 <= len(trim) <= 2
                    or not all(isinstance(v, (int, float)) and v >= 0
                               for v in trim)):
                _fail(where, field + ".trim",
                      "%r is not [start, end] in seconds" % (trim,))
            if len(trim) == 2 and trim[1] <= trim[0]:
                _fail(where, field + ".trim",
                      "end %r is not after start %r" % (trim[1], trim[0]))

        if entry.get("loop_from") is not None:
            lf = entry["loop_from"]
            if not isinstance(lf, (int, float)) or lf < 0:
                _fail(where, field + ".loop_from",
                      "%r is not a non-negative number of seconds" % (lf,))

        if entry.get("loop") not in (None, True, False, "auto"):
            _fail(where, field + ".loop",
                  "%r is not true, false or \"auto\" (auto picks the loop points "
                  "whose join is quietest)" % (entry["loop"],))

        if entry.get("loop_to") is not None:
            lt = entry["loop_to"]

            if not isinstance(lt, (int, float)) or lt <= 0:
                _fail(where, field + ".loop_to",
                      "%r is not a positive number of seconds" % (lt,))

        spec = dict(SOUND_SAMPLE_DEFAULTS)
        spec["wav"] = path
        spec["loop"] = entry.get("loop")   # None = decided by the sample's role

        for key in SOUND_SAMPLE_DEFAULTS:
            if key in entry:
                spec[key] = entry[key]

        return spec


class Bank(object):
    """A loaded bank.json: the computed half, plus the declared half if any."""

    def __init__(self, path, raw):
        self.path = path
        self.raw = raw
        self.base = os.path.dirname(os.path.abspath(path))
        self.declared = "declare" in raw
        self.cars = {}
        self.order = []
        # A legacy bank has no declaration to read this from, but
        # build_manifest asks every bank: without the default the bake died
        # with AttributeError before it could reproduce the bank unchanged.
        self.palette_from_pages = False
        self.clut_rows = 8
        # Opt-in relocation of wheel sprites that cannot be used as declared.
        # False keeps every explicit island exactly where the author put it.
        self.auto_textures = False
        # None: the bake keeps its Driver 1 default (2048/3000). A Driver 2
        # export sets 1, because those OBJs are already engine units.
        self.obj_scale = None

        if not self.declared:
            return

        decl = raw["declare"]
        where = path

        if not isinstance(decl, dict):
            _fail(where, "declare", "must be an object")

        _check_keys(where, "declare", decl,
                    {"schema", "name", "donor", "order", "cars",
                     "palette_from_pages", "clut_rows", "scale",
                     "auto_textures"}, "declare")

        if "scale" in decl:
            self.obj_scale = _bake_scale(where, "declare.scale", decl.get("scale"))

        # the bank's palette rows come from the pages themselves, so a page
        # re-bake cannot leave them disagreeing. A declared bank is generated
        # from its pages, so this is on unless the bank says otherwise (and the
        # bake refuses "false": there is no other palette to take).
        self.palette_from_pages = bool(decl.get("palette_from_pages", True))

        auto_textures = decl.get("auto_textures", False)

        if not isinstance(auto_textures, bool):
            _fail(where, "declare.auto_textures",
                  "%r is not true or false" % (auto_textures,))

        self.auto_textures = auto_textures
        # How many civ_clut rows this engine build actually binds. 8 is
        # carTpages[city][8]. A larger number only changes the diagnostic: the
        # bake still emits every set, and a row the engine does not have still
        # draws the first page's palette.
        rows = decl.get("clut_rows", 8)
        if not isinstance(rows, int) or not 1 <= rows <= 32:
            _fail(where, "declare.clut_rows",
                  "%r is not a row count 1..32" % (rows,))
        self.clut_rows = rows
        schema = decl.get("schema", SCHEMA)

        if schema != SCHEMA:
            _fail(where, "declare.schema",
                  "version %r is not supported (this tool reads %d)"
                  % (schema, SCHEMA))

        self.name = decl.get("name") or raw.get("city") or ""

        donor = decl.get("donor") or {}

        if not isinstance(donor, dict):
            _fail(where, "declare.donor", "must be an object")

        # a LABEL for people (where the car came from); the bake reads no file
        # of it. "lev" is accepted from older banks and ignored.
        _check_keys(where, "declare.donor", donor,
                    {"city", "model", "slot", "lev", "source", "obj_scale"},
                    "donor")
        # source / obj_scale are labels from exportd2 (driver2 vs driver2-demo,
        # and the factor already applied to the OBJ). The bake does not apply
        # obj_scale again; declare.scale is the bake multiplier.
        if "source" in donor and (not isinstance(donor["source"], str)
                                  or not donor["source"].strip()):
            _fail(where, "declare.donor.source",
                  "%r is not a source name" % (donor.get("source"),))
        if "obj_scale" in donor:
            _bake_scale(where, "declare.donor.obj_scale", donor["obj_scale"])
        self.donor = donor

        cars = decl.get("cars") or {}

        if not isinstance(cars, dict):
            _fail(where, "declare.cars", "must be an object")

        for name, data in cars.items():
            base = self.base
            if isinstance(data, str):
                # a car may live in its own file (cars/<car>/handling.json):
                # its paths are relative to that file, and keys the bake does
                # not read (handling, raw, notes) are ignored here
                carfile = os.path.normpath(os.path.join(self.base, data))
                try:
                    with open(carfile, "r", encoding="utf-8") as f:
                        filed = json.load(f)
                except (OSError, ValueError) as e:
                    _fail(where, "declare.cars.%s" % name,
                          "cannot read %s (%s)" % (data, e))
                data = {k: v for k, v in filed.items() if k in CAR_KEYS}
                base = os.path.dirname(carfile)
            if not isinstance(data, dict):
                _fail(where, "declare.cars.%s" % name, "must be an object")

            label = "declare.cars.%s" % name

            # named BEFORE the generic key check, so the message teaches the
            # replacement instead of listing allowed keys
            if "pages" in data:
                _fail(where, label + ".pages",
                      "a declaration names TEXTURES, not pages: keys are set "
                      "ids, e.g. \"textures\": {\"1\": \"textures/car.png\"}. "
                      "A page's png is a bake output (a preview) and is not an "
                      "input - re-baking from it is generation loss.")

            _check_keys(where, label, data, CAR_KEYS, "car")
            self.cars[name] = Car(label, path, data, base, where)

        seen = set()

        for entry in decl.get("order") or []:
            if not isinstance(entry, dict) or set(entry) - {"slot", "car"}:
                _fail(where, "declare.order", "each entry must be {slot, car}")

            slot = entry.get("slot")

            if not isinstance(slot, int) or not 0 <= slot <= MAX_SLOT:
                _fail(where, "declare.order.slot",
                      "%r is not a model slot 0..%d" % (slot, MAX_SLOT))

            if slot in seen:
                _fail(where, "declare.order.slot", "slot %d is listed twice" % slot)

            seen.add(slot)
            carname = entry.get("car")

            if carname not in self.cars:
                _fail(where, "declare.order.car",
                      "%r is not declared in cars (it has: %s)"
                      % (carname, ", ".join(sorted(self.cars)) or "none"))

            self.order.append((slot, carname))


def dumps_compact(obj, width=100, _ind=0):
    """JSON that stays readable: a value that fits on one line (<= width) is
    written on one line; only bigger objects/lists are broken up, 2 spaces."""
    one = json.dumps(obj)
    if len(one) + _ind <= width or not isinstance(obj, (dict, list)) or not obj:
        return one
    pad = " " * (_ind + 2)
    if isinstance(obj, dict):
        items = ["%s%s: %s" % (pad, json.dumps(k),
                               dumps_compact(v, width, _ind + 2))
                 for k, v in obj.items()]
        return "{\n" + ",\n".join(items) + "\n" + " " * _ind + "}"
    items = [pad + dumps_compact(v, width, _ind + 2) for v in obj]
    return "[\n" + ",\n".join(items) + "\n" + " " * _ind + "]"


def _portrait_is_raster(path):
    """True when the portrait is a PNG, TGA, or BMP rather than a packed page."""
    return os.path.splitext(path)[1].lower() in PORTRAIT_RASTER_EXTS


def _write_portrait_raw(path, pages):
    """One CCARS-shaped file. Page ``slot`` sits at ``slot * PORTRAIT_BYTES``.

    ``slot`` is the in-game model number (``declare.order``), the same number
    the frontend asks for and the same number as ``<stem>.portraits/<slot>.raw``.
    It is not the dense roster cursor: Miami's cop is model 1 (roster slot 0)
    and lives at offset ``0x8000``. Slots below the highest declared page that
    have no portrait are zero-filled. The frontend treats an all-zero page as
    empty and falls through to the loose file.
    """
    highest = max(pages)
    blob = bytearray((highest + 1) * PORTRAIT_BYTES)

    for slot, page in pages.items():
        start = slot * PORTRAIT_BYTES
        blob[start:start + PORTRAIT_BYTES] = page

    with open(path, "wb") as out:
        out.write(blob)


def install_portraits(bank, out_dir, stem):
    """Copy declared portraits beside the LEV the game opens.

    Writes two products from the same pages. Neither is a lump inside the LEV.

    * ``<out_dir>/<stem>.RAW`` — pages of ``0x8000`` at ``slot * 0x8000``.
      ``slot`` is the car's model number in ``order``. A later rebake with
      no portraits removes a RAW this function wrote before.
    * ``<out_dir>/<stem>.portraits/<slot>.raw`` and, when the source has one,
      ``<slot>.clut``. The loose page is the fallback when the RAW is missing
      or that page is all zero. The CLUT stays loose: the RAW is pixels only,
      same as ``CCARS.RAW``. A raster has no CLUT, and a ``<slot>.clut`` left
      from an earlier bake is removed so the stock palette draws the page.

    ``stem`` is the LEV filename without its extension (the same name
    ``build_bank`` uses). Returns ``(slots, notes)``. A portrait on a car that
    is not in ``order`` is skipped and named in ``notes`` — it has no model
    number to file it under.
    """
    written = []
    notes = []
    pages = {}

    if not getattr(bank, "declared", False) or not stem:
        return written, notes

    slots = {name: slot for slot, name in bank.order}
    dest_dir = os.path.join(out_dir, stem + ".portraits")
    raw_path = os.path.join(out_dir, stem + ".RAW")

    for name, car in bank.cars.items():
        if not getattr(car, "portrait", None):
            continue

        slot = slots.get(name)

        if slot is None:
            notes.append("%s declares a portrait but is not in order, so it "
                         "has no model number" % name)
            continue

        if not os.path.isdir(dest_dir):
            os.makedirs(dest_dir)

        # portrait_page is either the pre-packed file bytes or the page
        # encode_path already produced. Neither is re-encoded here.
        page = car.portrait_page
        dest = os.path.join(dest_dir, "%d.raw" % slot)

        with open(dest, "wb") as out:
            out.write(page)

        cdest = os.path.join(dest_dir, "%d.clut" % slot)

        if car.portrait_clut:
            with open(car.portrait_clut, "rb") as src, open(cdest, "wb") as out:
                out.write(src.read())
        elif os.path.isfile(cdest):
            # A raster, and a pre-packed page with no sibling CLUT, are drawn
            # with the stock CARBACK palette. A CLUT left from an earlier
            # bake would paint those indices through a different palette.
            os.remove(cdest)

        pages[slot] = page
        written.append(slot)

    if pages:
        _write_portrait_raw(raw_path, pages)
    elif os.path.isfile(raw_path):
        os.remove(raw_path)

    return written, notes


class _DuplicateKey(ValueError):
    """One JSON object listed the same key twice."""

    def __init__(self, key):
        ValueError.__init__(self, key)
        self.key = key


def _no_duplicate_keys(pairs):
    """Pairs -> dict, refusing a key repeated inside ONE object.

    json.load keeps the LAST of a repeated key and says nothing. That is how a
    bank lost a car: two "order" arrays meant only the second survived, so the
    car the first named vanished from the bake - and with no car left declaring
    wheels the bank silently had no wheel set either. A duplicate is a typo, not
    an instruction, so it is refused by name.
    """
    out = {}

    for key, value in pairs:
        if key in out:
            raise _DuplicateKey(key)

        out[key] = value

    return out


def load(path):
    """Read bank.json and validate its `declare` half. Raises BankError."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f, object_pairs_hook=_no_duplicate_keys)
    except _DuplicateKey as e:
        raise BankError(
            "%s: the key %r is listed twice in one object - JSON keeps only the "
            "LAST one, so the first is silently lost. Two \"order\" arrays, for "
            "instance, drop every car the second does not name (and a bank left "
            "with no car declaring wheels then has no wheel set at all)."
            % (path, e.key))
    except FileNotFoundError:
        raise BankError("%s: no such file" % path)
    except ValueError as e:
        raise BankError("%s: not valid JSON (%s)" % (path, e))

    if not isinstance(raw, dict):
        raise BankError("%s: the top level must be an object" % path)

    return Bank(path, raw)


def write_declare(path, declare):
    """Replace the `declare` block, leaving every computed key as it is.

    The bake calls this after a rebuild: the computed half is refreshed, the
    declaration written back exactly as it was read.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}

    raw["declare"] = declare

    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps_compact(raw) + "\n")
