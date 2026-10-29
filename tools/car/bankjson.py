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
    "clean", "damaged", "low", "atlas", "pages", "wheels",
    "sounds", "palette", "remap",
}
SOUND_KEYS = {"engine", "idle", "horn", "siren", "cop", "engine_pct", "idle_pct"}
WHEEL_KEYS = {"clean", "damaged", "fast"}
WHEEL_ENTRY_KEYS = {"png", "set", "island"}
WHEEL_VARIANTS = ("clean", "damaged", "fast")

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

        self.pages = []
        for page in data.get("pages") or []:
            if not isinstance(page, dict) or set(page) - {"set", "png"}:
                _fail(where, "pages", "each entry must be {set, png}")
            setid = page.get("set")
            if not isinstance(setid, int) or not 0 <= setid <= 255:
                _fail(where, "pages.set", "%r is not a page set id 0..255" % (setid,))
            self.pages.append((setid, self._file(where, "pages.png",
                                                 page.get("png"), required=True)))

        if not self.atlas and not self.pages:
            _fail(where, "atlas", "a car needs an atlas or per-page PNGs")

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
        self.engine = file_of(where, "sounds.engine", data.get("engine"),
                              required=True)
        self.idle = file_of(where, "sounds.idle", data.get("idle"))
        self.horn = file_of(where, "sounds.horn", data.get("horn"))
        self.siren = file_of(where, "sounds.siren", data.get("siren"))
        self.engine_pct = data.get("engine_pct")
        self.idle_pct = data.get("idle_pct")

        for name in ("engine_pct", "idle_pct"):
            pct = getattr(self, name)

            if pct is not None and (not isinstance(pct, (int, float))
                                    or not 10 <= pct <= 400):
                _fail(where, "sounds.%s" % name,
                      "%r is not a percentage between 10 and 400" % (pct,))

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
                    {"schema", "name", "donor", "order", "cars"}, "declare")

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
