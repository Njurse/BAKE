#!/usr/bin/env python3
"""bankjson.py - read and validate the `declare` half of a bank's bank.json.

The schema is documented in bank-schema.md next to this file. This module is the
only reader: it fails hard, naming the file and the field, rather than warning
and guessing. A bank with no `declare` block is a donor-derived bank and loads
as "nothing declared".

    import bankjson
    bank = bankjson.load("banks/firebird/bank.json")
    if bank.declared:
        for car in bank.cars: ...
"""

import json
import os

SCHEMA = 1

CAR_KEYS = {
    "clean", "damaged", "low", "atlas", "textures", "wheels",
    "sounds", "palette", "remap",
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
              "loop_from", "peak", "max_rate", "sample_bytes"}
SOUND_SAMPLE_KEYS = {"wav", "level", "force", "normalise", "gain_db",
                     "rate", "rate_pct", "loop", "loop_from", "trim",
                     "fade_out"}

# bank-wide defaults (a car may override the per-sample ones per sample)
SOUND_DEFAULTS = {
    "engine_pct": 100,
    "idle_pct": 100,
    "loop_from": 0.0,
    "peak": 0.999,      # retail's own car banks sit at 100% (sound_level_diag)
    "max_rate": 16000,  # retail's rate, and the SPU budget is sized for it
    "sample_bytes": 16384,
}

# per-sample defaults; `loop` is decided by the sample's ROLE (the third sample
# is a one-shot horn, the other two are loops), so it is not listed here.
SOUND_SAMPLE_DEFAULTS = {
    "level": None,      # None = the bank's `peak`
    "force": False,     # scale even inside the level dead band
    "normalise": True,  # False = keep the source level; gain_db only
    "gain_db": 0.0,
    "rate": None,       # None = the source rate, capped at max_rate
    "rate_pct": 100,
    "loop_from": None,  # None = the sounds-level loop_from
    "trim": None,       # [start, end] seconds
    "fade_out": 0.0,
}
WHEEL_KEYS = {"clean", "damaged", "fast"}
WHEEL_ENTRY_KEYS = {"png", "set", "island"}
WHEEL_VARIANTS = ("clean", "damaged", "fast")

# a declared page describes its own BAKE, not just an image: the refresh path has
# to reproduce the page from the declaration alone
TEXTURE_KEYS = {"png", "transparent", "transparent_tolerance",
                "transparent_chroma", "palette", "protect"}

# A page is 256x256 texels, and the UVs that name a crop are unsigned bytes, so a
# crop rect runs 0..256 in each axis (u1/v1 EXCLUSIVE, like PIL's crop box).
PAGE_TEXELS = 256

MAX_SLOT = 12


class BankError(Exception):
    """A bank.json that cannot be used, with the file and field named."""


def _fail(where, field, why):
    raise BankError("%s: %s: %s" % (where, field, why))


def _check_keys(where, field, obj, allowed, what):
    for key in obj:
        if key not in allowed:
            _fail(where, field + "." + str(key),
                  "unknown %s key (allowed: %s)"
                  % (what, ", ".join(sorted(allowed))))


class Car(object):
    """One car's declared sources. Unset entries fall back to the donor."""

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

        self.image = file_of(where, field + ".png", data.get("png"),
                             required=True)
        self.transparent = data.get("transparent")
        self.transparent_tolerance = data.get("transparent_tolerance", 0)
        self.transparent_chroma = data.get("transparent_chroma")
        self.palette = data.get("palette")
        self.protect = data.get("protect") or []

        if (not isinstance(self.transparent_tolerance, int)
                or not 0 <= self.transparent_tolerance <= 255):
            _fail(where, field + ".transparent_tolerance",
                  "%r is not 0..255" % (self.transparent_tolerance,))

        if self.protect and not isinstance(self.protect, list):
            _fail(where, field + ".protect", "must be a list of hex colours")

        for colour in self.protect:
            _hex_colour(where, field + ".protect", colour)

        if self.palette is not None:
            if not isinstance(self.palette, list):
                _fail(where, field + ".palette",
                      "must be a list of 16 hex colours (a fixed palette)")

            if len(self.palette) != 16:
                _fail(where, field + ".palette",
                      "a fixed palette is exactly 16 colours, not %d"
                      % len(self.palette))

            for colour in self.palette:
                _hex_colour(where, field + ".palette", colour)

    def bake_options(self):
        """What the page bake needs, from the declaration alone."""
        return {"transparent": self.transparent,
                "tolerance": self.transparent_tolerance,
                "chroma": self.transparent_chroma,
                "palette": self.palette,
                "protect": list(self.protect or [])}


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

    `set` is what lets the bank carry a wheel MODEL. The engine keeps the host
    level's wheel mesh and reads the imported wheel only for its page and its
    crop rect (models.c BuildCarImportWheels: poly 2's texture_set is the page,
    its UVs the rect, and the first eight polys must all be FT4), so a wheel
    image with no page named cannot be carried at all - that variant keeps the
    host's wheel texture instead.

    `island` is (u0, v0, u1, v1) in page texels, u1/v1 EXCLUSIVE - the same
    rectangle PIL's crop() takes, so a 60x60 sprite at the page origin is
    (0, 0, 60, 60). Left unset, the bake uses the image's own size at the origin.
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
        return Wheel(file_of(where, field, data), None, None)

    if not isinstance(data, dict):
        _fail(where, field, "must be a path string or {png, set, island}")

    _check_keys(where, field, data, WHEEL_ENTRY_KEYS, "wheel")
    # png is how the crop rect is measured, so it may be left out ONLY when the
    # island is stated - a declaration read back out of a bank names the rect and
    # has no image file to point at
    path = file_of(where, field + ".png", data.get("png"))

    setid = data.get("set")

    if setid is not None and (not isinstance(setid, int)
                              or not 0 <= setid <= 255):
        _fail(where, field + ".set",
              "%r is not a page set id 0..255" % (setid,))

    island = data.get("island")

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
        rate = entry.get("rate")
        trim = entry.get("trim")
        fade = entry.get("fade_out")
        gain = entry.get("gain_db")
        pct = entry.get("rate_pct")

        if level is not None and (not isinstance(level, (int, float))
                                  or not 0.05 <= level <= 1.0):
            _fail(where, field + ".level",
                  "%r is not a target level between 0.05 and 1.0" % (level,))

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

        if entry.get("loop") is not None and not isinstance(entry["loop"], bool):
            _fail(where, field + ".loop",
                  "%r is not true or false" % (entry["loop"],))

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

        if not self.declared:
            return

        decl = raw["declare"]
        where = path

        if not isinstance(decl, dict):
            _fail(where, "declare", "must be an object")

        _check_keys(where, "declare", decl,
                    {"schema", "name", "donor", "order", "cars",
                     "palette_from_pages"}, "declare")

        # the bank's palette rows come from the pages themselves, so a page
        # re-bake cannot leave them disagreeing
        self.palette_from_pages = bool(decl.get("palette_from_pages"))
        schema = decl.get("schema", SCHEMA)

        if schema != SCHEMA:
            _fail(where, "declare.schema",
                  "version %r is not supported (this tool reads %d)"
                  % (schema, SCHEMA))

        self.name = decl.get("name") or raw.get("city") or ""

        donor = decl.get("donor") or {}

        if not isinstance(donor, dict):
            _fail(where, "declare.donor", "must be an object")

        _check_keys(where, "declare.donor", donor,
                    {"city", "model", "slot", "lev"}, "donor")
        self.donor = donor

        cars = decl.get("cars") or {}

        if not isinstance(cars, dict):
            _fail(where, "declare.cars", "must be an object")

        for name, data in cars.items():
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
            self.cars[name] = Car(label, path, data, self.base, where)

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


def load(path):
    """Read bank.json and validate its `declare` half. Raises BankError."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
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
        json.dump(raw, f, indent=1)
        f.write("\n")
