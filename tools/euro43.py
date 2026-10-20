#!/usr/bin/env python3
"""euro43.py - the headerless EURO-43 beta level stream.

The leaked Euro-43 build writes Driver 1's level as a flat sequence of
`{u32 type, u32 size}` lumps: no LUMPDESC, no DATA1/DATA2 containers. The lump
ids are the retail ones; the difference is only the container. This module is
the one place that knows that layout, so lev1.py and pages1.py can ask it for
the pieces they need instead of each growing a second reader.

Nothing here decodes a MODEL or a page - the callers pass their own decoder in
(car_groups) or use pages1.read_page on the offsets returned by page_entries.
"""
import struct

LUMP_TEXTURES = 0
LUMP_MODELS = 1
LUMP_TEXTURENAMES = 5
LUMP_MODELNAMES = 12
LUMP_PALLET = 25

KNOWN_TYPES = frozenset((
    0, 1, 2, 4, 5, 7, 8, 9, 10, 12, 15, 16, 17, 18, 20, 21, 22, 23, 24, 25,
    28, 33, 34, 35, 36, 37, 40, 41, 42, 43, 0xFF,
))

CAR_BASES = ("CIVCAR1", "CIVCAR2", "COPCAR", "PLAYERCAR1", "PLAYERCAR2",
             "PONTIAC", "TORINO", "PICKUP")


def _u32(blob, off):
    return struct.unpack_from("<I", blob, off)[0]


def _align4(n):
    return (n + 3) & ~3


def is_raw(blob):
    """True for a headerless EURO-43 stream, false for the PC LEV container."""
    if len(blob) < 8 or _u32(blob, 0) == 37:
        return False
    off = 0
    for _ in range(4):
        if off + 8 > len(blob):
            return False
        t, s = _u32(blob, off), _u32(blob, off + 4)
        if t not in KNOWN_TYPES:
            return False
        if s > len(blob) - off - 8:
            return False
        if t == 0xFF or (t == 0 and s == 0):
            return True
        off = _align4(off + 8 + s)
    return True


def lumps(blob):
    """[(type, body_offset, size)] for a raw stream, in file order."""
    out = []
    off = 0
    while off + 8 <= len(blob):
        t, s = _u32(blob, off), _u32(blob, off + 4)
        if t == 0xFF or (t == 0 and s == 0) or s > len(blob) - off - 8:
            break
        out.append((t, off + 8, s))
        off = _align4(off + 8 + s)
    return out


def find(blob, lump_type):
    """First (body, size) of `lump_type` in a raw stream, or None."""
    for t, body, size in lumps(blob):
        if t == lump_type:
            return body, size
    return None


def find_bytes(blob, lump_type):
    """The body bytes of `lump_type`, or None."""
    got = find(blob, lump_type)
    if not got:
        return None
    off, size = got
    return blob[off:off + size], size


def _variant(name, base):
    suffix = name[len(base):]
    if suffix in ("", "CLEAN", "HIGH"):
        return "clean"
    if suffix in ("DAM", "DAM_OLD"):
        return "damaged"
    if suffix == "LOW":
        return "low"
    return None


def car_groups(blob, level_models):
    """[(group_index, {variant: Model})] from the beta's named level models.

    `level_models(blob)` is the caller's decoder (lev1.level_models): it returns
    [(slot, name, Model)]. Cars are ordinary level models in this build, named
    CIVCAR1LOW / COPCARCLEAN / PLAYERCAR1DAM_OLD / ...; group them by base name
    and map the suffix onto the variants the bake already understands.
    """
    groups = {}
    order = []
    for _slot, name, model in level_models(blob):
        if not name:
            continue
        base = next((b for b in CAR_BASES if name.startswith(b)), None)
        if base is None:
            continue
        variant = _variant(name, base)
        if variant is None:
            continue
        if base not in groups:
            groups[base] = {}
            order.append(base)
        groups[base][variant] = model

    out = []
    for i, base in enumerate(order):
        variants = dict(groups[base])
        if "clean" not in variants:
            variants["clean"] = next(iter(variants.values()))
        out.append((i, variants))
    return out


def pallet_lump(blob):
    """(body_bytes, size) of LUMP_PALLET, or None."""
    return find_bytes(blob, LUMP_PALLET)


def texture_names(blob):
    """The TEXTURENAMES buffer, or b""."""
    got = find_bytes(blob, LUMP_TEXTURENAMES)
    return got[0] if got else b""


def page_entries(blob):
    """(region_ofs, entries, table) for the TEXTURES-embedded page table.

    The TEXTURES lump starts with `[tpage_amount][field4]`, then an 8-byte
    {flags, offset} table. Page **set s** begins at TEXTURES_body + 8 +
    TP[s].offset; its span is the next entry's offset (or the lump end). The
    retail permlist/speclist is not present, so every set is reported as a page
    entry and the bake's page plan picks the ones a car or wheel actually names.
    """
    got = find(blob, LUMP_TEXTURES)
    if not got:
        return 0, [], None
    body, size = got
    n = struct.unpack_from("<i", blob, body)[0]
    lump_end = body + size
    entries = []
    for s in range(n):
        _flags, off = struct.unpack_from("<II", blob, body + 8 + s * 8)
        start = body + 8 + off
        if s + 1 < n:
            _nf, nxt = struct.unpack_from("<II", blob, body + 8 + (s + 1) * 8)
            end = body + 8 + nxt
        else:
            end = lump_end
        if end > lump_end or end <= start:
            end = lump_end
        if 0 <= start < lump_end:
            entries.append((s, start, end - start))
    ti = {"tpage_amount": n, "tpage_ids": [], "nperms": len(entries),
          "permlist": [(s, z) for s, _o, z in entries],
          "nspecpages": 0, "speclist": []}
    return 0, entries, ti
