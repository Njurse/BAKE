#!/usr/bin/env python3
"""exportd2.py - export Driver 2 cars and everything needed to rebuild them.

This is the Driver 2 car suite.  It reads one D2 ``LEVELS/*.LEV`` (or a directory
of them), writes editable assets and a self-contained source package, and builds
a carhacks-loadable car-data bank (``<CITY>.LEV`` + ``<CITY>.LCF``).

Driver 1 is deliberately handled elsewhere: use ``bake.py`` / ``d1pool.py`` for
the D1 traffic-car transplant.

What a D2 car needs, and what this tool exports:

  * LUMP_CAR_MODELS (28) - all present clean / damaged / low variants, as OBJ +
    a lossless geom JSON, plus the exact engine block bytes.
  * LUMP_TEXTUREINFO (34) + the page data - only the texture sets the exported
    cars name, plus the sets carrying WHLCLEAN / WHLBOTH / WHEEL sprites.  Each
    page is written as a PNG through CLUT row 0, with every CLUT row in the JSON.
  * LUMP_PALLET (25) - the car palette records, resolved to RGB rows.
  * LUMP_TEXTURENAMES / LUMP_MODELNAMES - the name buffers the TEXINF and wheel
    lookups use.
  * LEVELS/<CITY>.LCF - the 13 CAR_COSMETICS records, decoded by field.

The master list (``index.json`` and each city's ``<CITY>.export.json``) carries
an editable ``bank_order``.  Change it, then run ``--rebuild-bank`` to put the
cars into a different order in the generated bank.

Usage:
    python exportd2.py LEVELS/CHICAGO.LEV -o out/exportd2
    python exportd2.py LEVELS/ --all -o out/exportd2
    python exportd2.py --rebuild-bank out/exportd2/index.json --city CHICAGO
    python exportd2.py --rebuild-bank out/exportd2/CHICAGO/CHICAGO.export.json \
        --order my_order.json
    python exportd2.py --selftest
"""

import argparse
import glob
import hashlib
import json
import math
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bake                # noqa: E402  (the LUMP_CAR_WHEELS record encoder)
import vag                 # noqa: E402  (PSX ADPCM, tools/common)
import carbank_wheels      # noqa: E402  (the 8-quad FT4 wheel carrier)
import cosmetics1          # noqa: E402  (LCF read/write)
import lev1                # noqa: E402  (the shared MODEL/poly decoder)
import pages1              # noqa: E402  (page RLE + CLUT decode)
import scalemodels         # noqa: E402  (the D2 car lump locator)

TOOL = "exportd2"
VERSION = 1
GAME = "D2"

SECTOR = 2048
DATA1_OFF = 2048
MAX_CAR_MODELS = 13
MODEL_DATA_AREA = 164          # count (4) + table (156) + 4-byte pad
PERM_REGION = 16
PAGE_BYTES = 0x8000
RECORD_SIZE = 236
LCF_TABLE = 52

LUMP_MODELNAMES = 12
LUMP_TEXTURENAMES = 5
LUMP_PALLET = 25
LUMP_CAR_MODELS = 28
LUMP_TEXTUREINFO = 34
LUMP_CAR_DATA = 35
LUMP_LUMPDESC = 37
# A segment Driver 2 itself never writes. It carries the wheel MODELS a bank
# names, one per variant, so an imported car can wear the imported wheel rather
# than the host level's shared one. The engine reads it back by type
# (models.c CAR_IMPORT_LUMP_WHEELS), and every other walk skips an unknown type
# by its size, so it is additive.
LUMP_CAR_WHEELS = 200
# JERICHO: the two sound segments. SOUNDS is per model - a D2 dynamic bank
# (int num_samples, SAMPLE_DATA[], then the VAG payload) that gamesnd hands
# straight to LoadSoundBankDynamic; SIRENS is 13 cop flags.
LUMP_CAR_SOUNDS = 201
LUMP_CAR_SIRENS = 202
SOUND_SAMPLES = 3            # engine, idle, then the horn/siren
# Per-sample byte budgets and a rate ceiling. See _sound_sample: the
# engine's SPU write is unchecked, so these are a safety limit, not taste.
# Retail Driver 2 car banks are the reference: their engine loop is 13392 bytes
# at 16000 Hz (measured from the log's sound diag). The cap is above that so a
# sample is never trimmed for no reason, and the rate ceiling is the same 16000
# so nothing is needlessly downsampled - the field is the recording rate, and the
# engine plays each sample at its own, exactly as retail's 16000/1486 pair does.
SOUND_SAMPLE_BYTES = 16384
SOUND_MAX_RATE = 16000
# The level to normalise a sample to, measured from the game's OWN car banks
# (sound.c sound_level_diag, CHICAGO): they sit at peak 32749-32767, i.e. 100%
# - so 90% was quieter than retail, not safer. Peak is what a mixer clips on,
# and retail runs it to full scale.
SOUND_PEAK = 0.999
# The loudness those same banks measure: rms 29-39% of full scale. Peak
# alone is not loudness - a limited take reaches 100% peak with an rms half
# again as high, which plays loud AND squashed. levelled against this, the
# Firebird's engine loop (crest 2.0, rms 49%) is pulled down ~3 dB.
SOUND_RMS = 0.34

WHEEL_DETAIL_NAMES = ("WHLCLEAN", "WHLBOTH", "WHEEL")
WHEEL_VARIANTS = ("clean", "damaged", "fast")
TEXTURED_KINDS = ("FT3", "FT4", "GT3", "GT4")


# ---------------------------------------------------------------------------
# small binary helpers
# ---------------------------------------------------------------------------
def align(n, a=4):
    return (n + a - 1) & ~(a - 1)


def _i32(blob, off):
    return struct.unpack_from("<i", blob, off)[0]


def _u32(blob, off):
    return struct.unpack_from("<I", blob, off)[0]

def _sha256(blob):
    return hashlib.sha256(blob).hexdigest()


def _rel(path, root):
    return os.path.relpath(path, root).replace("\\", "/")


def _write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=False)
        f.write("\n")


def _read_json(path):
    with open(path, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# D2 level structure
# ---------------------------------------------------------------------------
def level_game(blob):
    """'d1' / 'd2' / None, using each game's own container walk."""
    try:
        if lev1.car_models_lump(blob) is not None:
            return "d1"
    except Exception:
        pass
    try:
        if scalemodels.d2_car_lump(blob):
            return "d2"
    except Exception:
        pass
    return None


def d2_citylumps(blob):
    if len(blob) < 40:
        raise ValueError("not a level file: shorter than the citylump table")
    typ, _size = struct.unpack_from("<II", blob, 0)
    if typ != LUMP_LUMPDESC:
        raise ValueError("not a LEV file: first lump type is %d, expected %d"
                         % (typ, LUMP_LUMPDESC))
    return struct.unpack_from("<8i", blob, 8)


def d2_segments(blob):
    """[(type, body_offset, size)] for DATA1, walked the D2 way (no count)."""
    table = d2_citylumps(blob)
    data1_off, data1_size = table[0], table[1]
    end = min(len(blob), data1_off + data1_size)
    out = []
    off = data1_off + 8
    while off + 8 <= end:
        typ, size = struct.unpack_from("<ii", blob, off)
        if size < 0 or off + 8 + size > end:
            break
        out.append((typ, off + 8, size))
        off += 8 + align(size, 4)
    return out


def d2_lump(blob, lump_type):
    for typ, body, size in d2_segments(blob):
        if typ == lump_type:
            return body, size
    return None


def d2_page_base(blob):
    """The engine's pageBase: DATA1's sector start + its sector length."""
    table = d2_citylumps(blob)
    return (table[0] // SECTOR + table[1] // SECTOR) * SECTOR


# ---------------------------------------------------------------------------
# LUMP_CAR_MODELS
# ---------------------------------------------------------------------------
def d2_model_table(blob):
    """{'body','size','area','offsets'} for LUMP_CAR_MODELS."""
    got = d2_lump(blob, LUMP_CAR_MODELS)
    if not got:
        raise ValueError("no LUMP_CAR_MODELS (28) in DATA1")
    body, size = got
    if body + MODEL_DATA_AREA > len(blob):
        raise ValueError("LUMP_CAR_MODELS is shorter than its offset table")
    offsets = [struct.unpack_from("<3i", blob, body + 4 + m * 12)
               for m in range(MAX_CAR_MODELS)]
    return {"body": body, "size": size,
            "area": body + MODEL_DATA_AREA, "offsets": offsets}


def model_block_ranges(table):
    """{variant: (start, end)} relative to the data area, per model.

    A block ends at the next offset the lump uses at all - the next variant or
    the next model's clean block - exactly as the source file lays them out.
    """
    limit = table["size"] - MODEL_DATA_AREA
    used = sorted(o for tri in table["offsets"] for o in tri if o >= 0)
    out = {}
    for start in used:
        later = [o for o in used if o > start]
        end = min(later) if later else limit
        if start < 0 or start >= limit or end <= start:
            continue
        out[start] = (start, min(end, limit))
    return out


def model_blocks(blob, table, model):
    """{variant: exact block bytes} for one model."""
    c, d, l = table["offsets"][model]
    ranges = model_block_ranges(table)
    out = {}
    for name, start in (("clean", c), ("damaged", d), ("low", l)):
        if start < 0:
            continue
        if start not in ranges:
            continue
        a, b = ranges[start]
        out[name] = blob[table["area"] + a:table["area"] + b]
    return out


def decode_variants(blob, table, model):
    """{variant: Model} for a D2 car.  Damaged uses the clean poly stream."""
    c, d, l = table["offsets"][model]
    clean_off = table["area"] + c if c >= 0 else None
    out = {}
    for name, start in (("clean", c), ("damaged", d), ("low", l)):
        if start < 0:
            continue
        polys_from = clean_off if name == "damaged" else None
        out[name] = lev1.decode_model(blob, table["area"] + start,
                                      polys_from=polys_from)
    return out


# ---------------------------------------------------------------------------
# LUMP_TEXTUREINFO and pages
# ---------------------------------------------------------------------------
def split_textureinfo(body):
    """(head, perms, specs) of a TextureInfo body.

    `head` is everything through the per-page TEXINF arrays, so it can be
    copied verbatim into a bank; the perms/specs lists are rebuilt.
    """
    n = _i32(body, 0)
    if n < 0 or n > 128:
        raise ValueError("TextureInfo tpage_amount %d is out of range" % n)
    off = 8 + 8 * (n + 1)
    for _ in range(n):
        if off + 4 > len(body):
            raise ValueError("TextureInfo TEXINF table is truncated")
        cnt = _i32(body, off)
        off += 4
        if cnt < 0 or off + cnt * 8 > len(body):
            raise ValueError("TextureInfo TEXINF entry is truncated")
        off += cnt * 8
    head = bytes(body[:off])

    if off + 4 > len(body):
        raise ValueError("TextureInfo permanent list is truncated")
    nperms = _i32(body, off)
    off += 4
    if nperms < 0 or nperms > PERM_REGION or off + nperms * 8 > len(body):
        raise ValueError("TextureInfo nperms %d is out of range" % nperms)
    perms = [struct.unpack_from("<ii", body, off + i * 8)
             for i in range(nperms)]
    off += PERM_REGION * 8

    if off + 4 > len(body):
        raise ValueError("TextureInfo special list is truncated")
    nspec = _i32(body, off)
    off += 4
    if nspec < 0 or off + nspec * 8 > len(body):
        raise ValueError("TextureInfo nspecpages %d is out of range" % nspec)
    specs = [struct.unpack_from("<ii", body, off + i * 8)
             for i in range(nspec)]
    return head, perms, specs


def build_textureinfo(head, perms, specs):
    """A TextureInfo body with the original TEXINF head and filtered lists.

    The permanent list is a fixed 16-entry region in the engine; anything over
    that moves to the unbounded special list, exactly as bankfill does.
    """
    perms = list(perms)
    specs = list(specs)
    moved = []
    while len(perms) > PERM_REGION:
        moved.append(perms.pop())
    if moved:
        specs.extend(reversed(moved))

    out = bytearray(head)
    out += struct.pack("<i", len(perms))
    for s, z in perms:
        out += struct.pack("<ii", s, z)
    out += b"\x00" * ((PERM_REGION - len(perms)) * 8)
    out += struct.pack("<i", len(specs))
    for s, z in specs:
        out += struct.pack("<ii", s, z)
    return bytes(out)


def d2_texture_info(blob):
    got = d2_lump(blob, LUMP_TEXTUREINFO)
    if not got:
        raise ValueError("no LUMP_TEXTUREINFO (34) in DATA1")
    body, size = got
    body_bytes = blob[body:body + size]
    ti = pages1.parse_texture_info(body_bytes)
    return body, size, body_bytes, ti


def entries_from_lists(base, perms, specs):
    """{set: (offset, size, kind)} from the engine's list order."""
    out = {}
    cursor = base
    for kind, items in (("permanent", perms), ("special", specs)):
        for s, z in items:
            out[s] = (cursor, z, kind)
            cursor += align(z, SECTOR)
    return out


def d2_page_entries(blob, ti):
    base = d2_page_base(blob)
    entries = entries_from_lists(base, ti["permlist"], ti["speclist"])
    for s, (off, size, _kind) in entries.items():
        if off + size > len(blob):
            raise ValueError("page set %d runs past the end of %s"
                             % (s, os.path.basename(getattr(blob, "name", "level"))))
    return base, entries


def page_indexes(page):
    idx = bytearray(65536)
    k = 0
    for y in range(256):
        for b in page[y * 128:(y + 1) * 128]:
            idx[k] = b & 0xF
            idx[k + 1] = b >> 4
            k += 2
    return bytes(idx)


def texture_names(blob):
    got = d2_lump(blob, LUMP_TEXTURENAMES)
    if not got:
        return b""
    off, size = got
    return blob[off:off + size]


def model_names(blob):
    got = d2_lump(blob, LUMP_MODELNAMES)
    if not got:
        return b""
    off, size = got
    return blob[off:off + size]


def texinfo_details(ti, names):
    """[(page_index, detail dict with name)] for every TEXINF entry."""
    out = []
    for pi, recs in enumerate(ti["tpage_ids"]):
        for r in recs:
            d = dict(r)
            no = r.get("nameoffset", -1)
            if 0 <= no < len(names):
                end = names.find(b"\x00", no)
                d["name"] = (names[no:end].decode("ascii", "replace")
                             if end > no else "")
            else:
                d["name"] = None
            out.append((pi, d))
    return out


# ---------------------------------------------------------------------------
# LUMP_PALLET
# ---------------------------------------------------------------------------
def d2_palette(blob):
    """{'body','size','total_cluts','records',...} with resolved CLUT rows."""
    got = d2_lump(blob, LUMP_PALLET)
    if not got:
        return {"body": 0, "size": 0, "total_cluts": 0,
                "records": [], "end": 0}
    body, size = got
    end = body + size
    total = _i32(blob, body)
    q = body + 4
    stored = []
    records = []
    while q + 16 <= end:
        palette, texnum, tpage, clut = struct.unpack_from("<iiii", blob, q)
        if palette == -1:
            break
        start = q
        q += 16
        if clut == -1:
            if q + 32 > end:
                break
            row = blob[q:q + 32]
            q += 32
            stored.append(row)
        elif 0 <= clut < len(stored):
            row = stored[clut]
        else:
            row = None
        records.append({"palette": palette, "texnum": texnum,
                        "tpageindex": tpage, "clut_number": clut,
                        "clut": row, "raw": blob[start:q]})
    return {"body": body, "size": size, "total_cluts": total,
            "records": records, "end": q}


def build_palette_body(records, total_cluts=None):
    """A LUMP_PALLET body from records, every row INLINE, then the -1 terminator.

    This is the one D2 palette writer: `filter_palette` is this plus a selection,
    and `carbank_palette` is this fed from `palette.json`, so an edited colour
    reaches the bank by the same path the donor's rows did.

    Inline-only is deliberate. Driver 2's records may REFERENCE an earlier row
    (`clut_number`), which is how its files stay small, but a reference dangles
    the moment a row is dropped or reordered - so a rebuilt body always states
    its 16 colours. That also means the bytes are the same whether the source
    used references or not, which is what keeps an unchanged bake identical.
    """
    kept = [r for r in records if r.get("clut") is not None]
    total = total_cluts if total_cluts else len(kept)

    out = bytearray(struct.pack("<i", total))

    for r in kept:
        out += struct.pack("<iiii", r["palette"], r["texnum"],
                           r["tpageindex"], -1)
        out += r["clut"]

    out += struct.pack("<i", -1)
    return bytes(out)


def filter_palette(pal, included):
    """A palette body containing only rows for the included sets.

    Every kept record is written in the INLINE form (clut_number -1) so a
    dropped earlier record cannot shift a reused CLUT index.
    """
    kept = [r for r in pal["records"]
            if r["tpageindex"] in included and r["clut"] is not None]
    if not kept:
        return b"", 0
    # total_cluts is a CLUT count for table sizing, not a record count.  Keep
    # the source's bound; filtering only removes records.
    total = pal["total_cluts"] if pal.get("total_cluts") else len(kept)
    return build_palette_body(kept, total), len(kept)


# ---------------------------------------------------------------------------
# PNG / OBJ writers (stdlib only)
# ---------------------------------------------------------------------------
def write_png_rgb(path, w, h, pix):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += pix[y * w * 3:(y + 1) * w * 3]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


def write_png_rgba(path, w, h, pix):
    """A PNG with an alpha channel, so a preview can show what is NOT drawn.

    The page's transparent index is written 0x0000 in every CLUT row, which as a
    colour is black - so an RGB preview paints it opaque black and looks like
    art. Anyone re-baking such a preview then gets the backdrop back as paint,
    which is how a keyed-out backdrop returns one generation later.
    """
    raw = bytearray()

    for y in range(h):
        raw.append(0)
        raw += pix[y * w * 4:(y + 1) * w * 4]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


def page_png(path, rows, idx, transparent=None):
    """The page as a PNG. `transparent` names the indices the bake keyed out.

    It is passed in rather than guessed: a 0x0000 entry on a BODY page can just
    be black paint, and treating it as transparency makes a preview claim the
    body has holes. When nothing was keyed, nothing is transparent.
    """
    pal = pages1.clut_rgb(rows[0]) if rows else [(0, 0, 0)] * 16
    transparent = set(transparent or ())

    pix = bytearray(256 * 256 * 4)

    for i, v in enumerate(idx):
        if v < len(pal):
            r, g, b = pal[v]
        else:
            r = g = b = 0

        pix[i * 4:i * 4 + 4] = bytes((r, g, b, 0 if v in transparent else 255))

    write_png_rgba(path, 256, 256, pix)


def palette_png(path, records, included):
    rows = [r for r in records if r["tpageindex"] in included and r["clut"]]
    if not rows:
        return
    sw = 8
    sep = 1
    w = 16 * sw
    h = len(rows) * (sw + sep) + sep
    bg = (24, 24, 24)
    pix = bytearray(bytes(bg) * (w * h))
    for i, rec in enumerate(rows):
        y0 = sep + i * (sw + sep)
        pal = pages1.clut_rgb(rec["clut"])
        for yy in range(sw):
            base = (y0 + yy) * w
            for x in range(16):
                pix[base + x * sw:base + (x + 1) * sw] = bytes(pal[x]) * sw
    write_png_rgb(path, w, h, pix)


def _normal_key(n):
    return (round(n[0], 6), round(n[1], 6), round(n[2], 6))


def write_obj(path, model, name, mtl_name):
    """OBJ + per-material groups; the material name carries set and texture id."""
    verts = model.vertices
    vt = []
    vn = []
    vt_index = {}
    vn_index = {}
    faces = []

    for p in model.polys:
        if len(p.v) < 3:
            continue
        textured = (p.kind in TEXTURED_KINDS
                    and p.texture_set not in (0, 255))
        # OBJ has no notion of Driver's poly kinds, so the material name has to
        # carry it or the kind is lost on the way back in: an F3 (flat, no UVs)
        # read as GT3 comes back textured, sampling garbage where it had no UVs,
        # and an FT3 read as GT3 gets gouraud shading where the donor had one
        # flat normal. "flat" stays bare for an untextured flat poly so old OBJs
        # still read; everything else is suffixed with its kind.
        base = ("set%d_id%d" % (p.texture_set, p.texture_id)
                if textured else "flat")
        if p.kind in ("F3", "FT3", "FT4"):
            mat = base if (p.kind == "F3" and base == "flat") else base + "_ft"
        else:
            mat = base
        uvs = p.uv if p.uv and len(p.uv) == len(p.v) else None
        ns = model.poly_normals(p)
        if len(ns) != len(p.v):
            ns = [model._geo_normal(p)] * len(p.v)

        face = []
        for i, vi in enumerate(p.v):
            tok = str(vi + 1)
            if uvs is not None:
                uv = uvs[i]
                key = (round(uv[0] / 255.0, 6), round(1.0 - uv[1] / 255.0, 6))
                if key not in vt_index:
                    vt_index[key] = len(vt) + 1
                    vt.append(key)
                tok += "/%d" % vt_index[key]
            else:
                tok += "/"
            nk = _normal_key(ns[i])
            if nk not in vn_index:
                vn_index[nk] = len(vn) + 1
                vn.append(nk)
            tok += "/%d" % vn_index[nk]
            face.append(tok)
        faces.append((mat, face))

    with open(path, "w") as f:
        f.write("# exportd2: %s\n" % name)
        f.write("mtllib %s\n" % mtl_name)
        f.write("o %s\n" % name)
        for x, y, z in verts:
            f.write("v %d %d %d\n" % (x, y, z))
        for u, v in vt:
            f.write("vt %.6f %.6f\n" % (u, v))
        for nx, ny, nz in vn:
            f.write("vn %.6f %.6f %.6f\n" % (nx, ny, nz))
        current = None
        for mat, face in faces:
            if mat != current:
                f.write("usemtl %s\n" % mat)
                current = mat
            f.write("f %s\n" % " ".join(face))


def write_mtl(path, materials, texture_rel):
    with open(path, "w") as f:
        f.write("# exportd2 materials\n")
        for key in sorted(materials):
            if key == "flat":
                f.write("newmtl flat\nKd 0.5 0.5 0.5\n\n")
                continue
            setid, texid = materials[key]
            f.write("newmtl %s\n" % key)
            f.write("Kd 1.000 1.000 1.000\n")
            f.write("map_Kd %s/PAGE_%d.png\n" % (texture_rel, setid))
            f.write("# texture_set %d, texture_id %d\n\n" % (setid, texid))


# ---------------------------------------------------------------------------
# geometry records
# ---------------------------------------------------------------------------
def normals_for(model):
    """(face_normals, point_normals, per-poly indices, source) for a model."""
    if model.face_normals:
        face = [list(n) for n in model.face_normals]
    else:
        face = [[int(round(c * 4096 * lev1.NORMAL_SIGN))
                 for c in model._geo_normal(p)] for p in model.polys]

    if model.normals:
        return (face, [list(n) for n in model.normals],
                [list(p.n) if p.n else list(p.v) for p in model.polys],
                "model")

    acc = [[0.0, 0.0, 0.0] for _ in model.vertices]
    for p in model.polys:
        g = model._geo_normal(p)
        for vi in p.v:
            acc[vi][0] += g[0]
            acc[vi][1] += g[1]
            acc[vi][2] += g[2]
    pt = []
    for a in acc:
        a = [c * lev1.NORMAL_SIGN for c in a]
        length = (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]) ** 0.5 or 1.0
        pt.append([int(round(a[0] / length * 4096)),
                   int(round(a[1] / length * 4096)),
                   int(round(a[2] / length * 4096))])
    return face, pt, [list(p.v) for p in model.polys], "synthesised"


def geom_record(city, model_index, variant, car_id, source, model):
    vs = model.vertices
    xs = [v[0] for v in vs]
    ys = [v[1] for v in vs]
    zs = [v[2] for v in vs]
    face_n, point_n, pn, nsource = normals_for(model)
    polys = []
    sets = set()
    for k, p in enumerate(model.polys):
        if p.kind in TEXTURED_KINDS:
            sets.add(p.texture_set)
        polys.append({
            "kind": p.kind,
            "set": p.texture_set,
            "id": p.texture_id,
            "v": list(p.v),
            "n": pn[k],
            "uv": [list(u) for u in p.uv],
            "color": list(p.color),
        })
    bbox = ([min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]
            if vs else [0, 0, 0, 0, 0, 0])
    return {
        "version": 1,
        "game": GAME,
        "city": city,
        "model": model_index,
        "variant": variant,
        "id": car_id,
        "source": source,
        "scale": 1.0,
        "bounding_sphere": model.bounding_sphere,
        "verts": len(vs),
        "tris": sum(1 if len(p.v) == 3 else 2 for p in model.polys),
        "bbox_raw": bbox,
        "sets": sorted(sets),
        "normals_source": nsource,
        "face_normals": face_n,
        "point_normals": point_n,
        "vertices": [list(v) for v in vs],
        "polys": polys,
    }


# ---------------------------------------------------------------------------
# cosmetics
# ---------------------------------------------------------------------------
def cosmetics_fields(rec):
    r = cosmetics1.read_record(rec)
    anchors = ["headLight", "frontInd", "backInd", "brakeLight", "revLight",
               "policeLight", "exhaust", "smoke", "fire"]
    scalars = ["extraInfo", "powerRatio", "cbYoffset", "susCoeff", "traction",
               "wheelSize"]
    tail = ["twistRateX", "twistRateY", "twistRateZ", "mass"]
    out = {}
    for name, value in zip(anchors, r["anchors"]):
        out[name] = list(value)
    out["wheelDisp"] = [list(v) for v in r["wheelDisp"]]
    for name, value in zip(scalars, r["sA"]):
        out[name] = value
    out["cPoints"] = [list(v) for v in r["cPoints"]]
    out["colBox"] = list(r["colBox"])
    out["cog"] = list(r["cog"])
    for name, value in zip(tail, r["sB"]):
        out[name] = value
    return out


def find_lcf(lev_path):
    stem = os.path.splitext(lev_path)[0]
    for ext in (".LCF", ".lcf", ".Lcf"):
        p = stem + ext
        if os.path.isfile(p):
            return p
    return None


# ---------------------------------------------------------------------------
# one city
# ---------------------------------------------------------------------------
def export_one(lev_path, out_root, models_filter, variants, make_bank):
    with open(lev_path, "rb") as f:
        blob = f.read()
    kind = level_game(blob)
    if kind == "d1":
        raise SystemExit("%s is a Driver 1 level.  exportd2 handles Driver 2 "
                         "cars; use tools/car/bake.py (bake) / d1pool.py for "
                         "Driver 1." % lev_path)
    if kind != "d2":
        raise SystemExit("%s does not look like a Driver 2 level with "
                         "LUMP_CAR_MODELS" % lev_path)

    city = os.path.splitext(os.path.basename(lev_path))[0].upper()
    city_dir = os.path.join(out_root, city)
    models_dir = os.path.join(city_dir, "models")
    textures_dir = os.path.join(city_dir, "textures")
    raw_dir = os.path.join(city_dir, "raw")
    bank_dir = os.path.join(city_dir, "bank")
    dirs = [models_dir, textures_dir, raw_dir,
            os.path.join(raw_dir, "models"),
            os.path.join(raw_dir, "pages"),
            os.path.join(raw_dir, "cosmetics")]
    if make_bank:
        dirs.append(bank_dir)
    for d in dirs:
        os.makedirs(d, exist_ok=True)

    warnings = []
    source = {"path": os.path.abspath(lev_path),
              "size": len(blob), "sha256": _sha256(blob)}
    table = d2_model_table(blob)
    ti_body_off, ti_size, ti_bytes, ti = d2_texture_info(blob)
    names = texture_names(blob)
    details = texinfo_details(ti, names)
    page_base, page_entries = d2_page_entries(blob, ti)
    pal = d2_palette(blob)

    # --- models -----------------------------------------------------------
    cars = []
    materials = {}
    used_sets = set()
    for m in range(MAX_CAR_MODELS):
        if models_filter is not None and m not in models_filter:
            continue
        c, d, l = table["offsets"][m]
        if c < 0:
            continue
        car_id = "%s_model%d" % (city, m)
        car = {"id": car_id, "model": m, "variants": {}, "sets": [],
               "cosmetics": m}
        variants_decoded = decode_variants(blob, table, m)
        blocks = model_blocks(blob, table, m)
        for variant in variants:
            model = variants_decoded.get(variant)
            if model is None:
                continue
            if model.errors:
                warnings.append("%s %s: %d poly decode error(s): %s"
                                % (car_id, variant, len(model.errors),
                                   model.errors[:3]))
            if not model.vertices:
                warnings.append("%s %s has no vertices" % (car_id, variant))
                continue
            obj_rel = "models/%s_%s.obj" % (car_id, variant)
            geom_rel = "models/%s_%s.geom.json" % (car_id, variant)
            raw_rel = "raw/models/%s_%s.bin" % (car_id, variant)
            obj_path = os.path.join(city_dir, obj_rel)
            geom_path = os.path.join(city_dir, geom_rel)
            raw_path = os.path.join(city_dir, raw_rel)

            write_obj(obj_path, model, "%s_%s" % (car_id, variant),
                      "%s.mtl" % city)
            rec = geom_record(city, m, variant, car_id, source, model)
            _write_json(geom_path, rec)
            if variant in blocks:
                with open(raw_path, "wb") as f:
                    f.write(blocks[variant])
            else:
                warnings.append("%s %s: no raw block found" % (car_id, variant))
                raw_rel = None

            for p in model.polys:
                if (p.kind in TEXTURED_KINDS
                        and p.texture_set not in (0, 255)):
                    if p.texture_set not in (0, 255):
                        used_sets.add(p.texture_set)
                    materials.setdefault(
                        "set%d_id%d" % (p.texture_set, p.texture_id),
                        (p.texture_set, p.texture_id))
                else:
                    materials.setdefault("flat", None)

            car["variants"][variant] = {
                "obj": obj_rel, "geom": geom_rel,
                "raw": raw_rel if raw_rel else None,
                "verts": rec["verts"], "tris": rec["tris"],
                "sets": rec["sets"],
            }
            car["sets"] = sorted(set(car["sets"]) | set(rec["sets"]))
        if car["variants"]:
            cars.append(car)
        else:
            warnings.append("%s has no exportable variants" % car_id)

    write_mtl(os.path.join(models_dir, "%s.mtl" % city), materials,
              os.path.relpath(textures_dir, models_dir).replace("\\", "/"))

    # --- pages ------------------------------------------------------------
    wheel_sets = set()
    for pi, det in details:
        if det.get("name") in WHEEL_DETAIL_NAMES:
            wheel_sets.add(pi)
            used_sets.add(pi)
    missing = sorted(s for s in used_sets if s not in page_entries)
    for s in missing:
        warnings.append("set %d is named by a car/wheel detail but is not in "
                        "the level's page lists" % s)
    included_sets = sorted(s for s in used_sets if s in page_entries)

    page_json = []
    for s in included_sets:
        off, size, kind = page_entries[s]
        rows, page = pages1.read_page(blob, off, size)
        idx = page_indexes(page)
        png_rel = "textures/PAGE_%d.png" % s
        page_png(os.path.join(city_dir, png_rel), rows, idx)
        with open(os.path.join(raw_dir, "pages", "PAGE_%d.bin" % s), "wb") as f:
            f.write(blob[off:off + size])
        page_json.append({
            "set": s, "kind": kind, "offset": off, "size": size,
            "ncluts": len(rows),
            "png": png_rel,
            "cluts": [r.hex() for r in rows],
            "clut_rgb": [pages1.clut_rgb(r) for r in rows],
            "details": [d for pi, d in details if pi == s],
            "used_by": [c["id"] for c in cars if s in c.get("sets", [])]
                       or (["wheels"] if s in wheel_sets else []),
        })
        if len(rows) == 0:
            warnings.append("page set %d has no CLUT rows" % s)

    pal_sets = sorted({r["tpageindex"] for r in pal["records"]})
    palette_png(os.path.join(city_dir, "%s_palettes.png" % city),
                pal["records"], set(included_sets))

    # --- raw lump bodies --------------------------------------------------
    raw_files = {"textureinfo": "raw/textureinfo.bin",
                 "pallet": "raw/pallet.bin",
                 "texturenames": "raw/texturenames.bin",
                 "modelnames": "raw/modelnames.bin"}
    with open(os.path.join(city_dir, raw_files["textureinfo"]), "wb") as f:
        f.write(ti_bytes)
    if pal["size"]:
        with open(os.path.join(city_dir, raw_files["pallet"]), "wb") as f:
            f.write(blob[pal["body"]:pal["body"] + pal["size"]])
    if names:
        with open(os.path.join(city_dir, raw_files["texturenames"]), "wb") as f:
            f.write(names)
    mnames = model_names(blob)
    if mnames:
        with open(os.path.join(city_dir, raw_files["modelnames"]), "wb") as f:
            f.write(mnames)

    # --- cosmetics --------------------------------------------------------
    lcf_path = find_lcf(lev_path)
    cos_records = []
    cos_json_path = os.path.join(city_dir, "%s.cosmetics.json" % city)
    if lcf_path:
        with open(lcf_path, "rb") as f:
            lcf_blob = f.read()
        try:
            cos_records = cosmetics1.parse_lcf(lcf_blob)
        except Exception as e:
            warnings.append("could not parse %s: %s" % (lcf_path, e))
            cos_records = []
        if cos_records:
            with open(os.path.join(city_dir, "%s.LCF" % city), "wb") as f:
                f.write(lcf_blob)
            for i, rec in enumerate(cos_records):
                with open(os.path.join(raw_dir, "cosmetics",
                                       "model%02d.cos" % i), "wb") as f:
                    f.write(rec)
            _write_json(cos_json_path, {
                "city": city, "source": os.path.abspath(lcf_path),
                "records": [{"model": i, "offset": LCF_TABLE + i * RECORD_SIZE,
                             "fields": cosmetics_fields(rec), "raw": rec.hex()}
                            for i, rec in enumerate(cos_records)],
            })
    else:
        warnings.append("no %s.LCF beside %s" % (city, lev_path))

    # --- textures json ----------------------------------------------------
    textures_json = {
        "city": city,
        "page_base": page_base,
        "tpage_amount": ti["tpage_amount"],
        "included_sets": included_sets,
        "wheel_sets": sorted(wheel_sets),
        "pages": page_json,
        "palette": {
            "total_cluts": pal["total_cluts"],
            "sets": pal_sets,
            "records": [{"palette": r["palette"], "texnum": r["texnum"],
                         "tpageindex": r["tpageindex"],
                         "clut_number": r["clut_number"],
                         "clut": r["clut"].hex() if r["clut"] else None,
                         "clut_rgb": (pages1.clut_rgb(r["clut"])
                                      if r["clut"] else None)}
                        for r in pal["records"]],
        },
        "details": [{"page": pi, **d} for pi, d in details],
    }
    _write_json(os.path.join(city_dir, "%s.textures.json" % city),
                textures_json)

    # --- master manifest --------------------------------------------------
    bank_order = [{"slot": c["model"], "car": c["id"]} for c in cars]
    manifest = {
        "version": VERSION,
        "tool": TOOL,
        "game": GAME,
        "city": city,
        "cell_size": scalemodels.D2_CELL_SIZE,
        "source": source,
        "models": cars,
        "texture_sets": included_sets,
        "wheel_sets": sorted(wheel_sets),
        "palette_sets": pal_sets,
        "textures": "%s.textures.json" % city,
        "cosmetics": ("%s.cosmetics.json" % city) if cos_records else None,
        "lcf": ("%s.LCF" % city) if cos_records else None,
        "raw": raw_files,
        "bank_order": bank_order,
        "warnings": warnings,
    }
    manifest_path = os.path.join(city_dir, "%s.export.json" % city)
    _write_json(manifest_path, manifest)

    if make_bank:
        bank_dir = os.path.join(city_dir, "bank")
        report = build_bank(manifest, manifest_path, bank_dir, None)
        manifest["bank"] = report

    # Print a compact, useful summary.
    covered = {c["id"]: sorted(c["sets"]) for c in cars}
    print("%s: %d car(s), %d texture set(s), %d page(s), %d palette record(s)"
          % (city, len(cars), len(included_sets), len(page_json),
             len(pal["records"])))
    for cid in sorted(covered):
        print("  %-18s sets %s" % (cid, covered[cid]))
    for w in warnings:
        print("  warning: %s" % w)
    if make_bank:
        print("  bank: %s" % manifest["bank"]["lev"])
        print("  bank: %s" % manifest["bank"]["lcf"])

    return {
        "city": city,
        "dir": city,
        "manifest": _rel(manifest_path, out_root),
        "bank": (_rel(os.path.join(city_dir, "bank", "%s.LEV" % city), out_root)
                 if make_bank else None),
        "lcf": (_rel(os.path.join(city_dir, "bank", "%s.LCF" % city), out_root)
                if make_bank else None),
        "cars": [{"id": c["id"], "model": c["model"],
                  "variants": sorted(c["variants"])} for c in cars],
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# bank building
# ---------------------------------------------------------------------------
def build_models_body(blocks_by_slot):
    """A LUMP_CAR_MODELS body from {slot: {variant: bytes}}."""
    table = [[-1, -1, -1] for _ in range(MAX_CAR_MODELS)]
    data = bytearray()
    for slot in sorted(blocks_by_slot):
        if slot < 0 or slot >= MAX_CAR_MODELS:
            continue
        blocks = blocks_by_slot[slot]
        for vi, variant in enumerate(("clean", "damaged", "low")):
            block = blocks.get(variant)
            if not block:
                continue
            table[slot][vi] = len(data)
            data += block
    out = bytearray(struct.pack("<i", MAX_CAR_MODELS))
    for tri in table:
        out += struct.pack("<3i", *tri)
    if len(out) > MODEL_DATA_AREA:
        raise ValueError("model table overruns the data area")
    out += b"\x00" * (MODEL_DATA_AREA - len(out))
    out += data
    return bytes(out)


def _uv_corners(island):
    """A crop rect -> the UV corners the wheel carrier writes.

    The engine's WheelPolyRect (models.c) takes the min/max of a poly's UVs and
    then adds 1 to the maxima, so a poly names INCLUSIVE texel corners and the
    rect it derives is exclusive. A declared island is already exclusive (a 60x60
    sprite at the origin is (0,0,60,60)), so the corners are island - 1 - which is
    also a plain D1/D2 wheel poly's convention.
    """
    u0, v0, u1, v1 = island
    return (u0, v0, u1 - 1, v1 - 1)


def build_wheel_body(specs):
    """A LUMP_CAR_WHEELS body from [(variant, page set, island)] (None = absent).

    One entry per variant, in WHEEL_VARIANTS order. An absent variant is written
    as offset -1, which is the engine's "keep the host level's wheel for this
    state" - the rule bake.build_wheel_lump already carries.
    """
    geoms = []

    for spec in specs:
        if spec is None:
            geoms.append(None)
            continue

        geoms.append(carbank_wheels.wheel_geom(spec["set"],
                                              _uv_corners(spec["island"]),
                                              name=spec["variant"]))

    return bake.build_wheel_lump(geoms)


def _sound_sample(spec, warnings, label, budget=SOUND_SAMPLE_BYTES):
    """One sample: the WAV encoded to VAG blocks, with its bank header fields.

    Every knob arrives already resolved (see carbank_bake._resolved_sample), and
    they are applied in this order, because the order is audible:

        resample -> trim -> fade_out -> gain_db -> level -> budget -> encode

    Trimming before the fade means the fade lands on the new tail; the fade
    before the level means a fade cannot pull the peak down and make `level`
    scale everything else up; and the level last means `gain_db` is a relative
    trim against a fixed ceiling rather than a fight with normalisation.

    `budget` is a BYTE cap, and it is not cosmetic. LoadSoundBankDynamic writes
    the payload to the SPU at a running address and takes its size from the
    bank itself - `lsbTabs.addr += length - slength` with no bounds check - so a
    sample set bigger than the cars bank's free space writes past the end of SPU
    RAM (on this build: a host access violation, not a polite warning). Retail
    car banks are 2-4 KB per car for exactly this reason, so the bake caps each
    sample and says what it did: a 16 kHz stereo-length loop is not worth a crash.
    """
    path = spec.get("wav")

    if not path:
        return None

    if not os.path.isfile(path):
        warnings.append("sound %s: %s does not exist" % (label, path))
        return None

    samples, rate = vag.read_wav(path)
    source_rate = rate
    wanted = spec.get("rate")

    if wanted:
        rate = int(wanted)
    else:
        pct = spec.get("rate_pct") or 100

        if pct != 100:
            rate = int(rate * pct / 100)

    max_rate = int(spec.get("max_rate") or SOUND_MAX_RATE)

    if rate > max_rate:
        samples = vag.resample(samples, source_rate, max_rate)
        rate = max_rate

    rate, clamped = vag.fit_rate(rate)

    if clamped:
        samples = vag.resample(samples, source_rate, clamped)
        source_rate = clamped
        rate = clamped
        warnings.append("sound %s: %d Hz is outside the SPU range; resampled to "
                        "%d" % (label, clamped, rate))

    trim = spec.get("trim")

    if trim:
        before = len(samples)
        samples = vag.trim(samples, trim[0], trim[1], rate)

        if len(samples) != before:
            warnings.append("sound %s: trimmed %s -> %.2fs"
                            % (label, "%.2f-%.2fs" % (trim[0], trim[1]),
                               len(samples) / float(rate or 1)))

    fade = float(spec.get("fade_out") or 0.0)

    if fade:
        samples = vag.fade_out(samples, fade, rate)

    gain = float(spec.get("gain_db") or 0.0)

    if gain:
        samples = vag.gain_db(samples, gain)
        warnings.append("sound %s: %+.1f dB applied" % (label, gain))

    # A bank sample has no gain of its own: the engine's channel volume is the
    # same for every car, so an un-normalised WAV plays quietly next to retail's.
    # `normalise: false` is the escape hatch for a sample whose own level is the
    # point - a siren that must sit under the engine, a take already mastered -
    # and then gain_db is the only level control there is.
    level = spec.get("level")

    if level is None:
        level = SOUND_PEAK

    target_rms = spec.get("rms")

    if target_rms is None:
        target_rms = SOUND_RMS

    normalise = spec.get("normalise")

    if normalise is None:
        normalise = True

    samples = vag.remove_dc(samples)

    before = vag.stats(samples)

    if before["clipped"]:
        warnings.append("sound %s: the source is clipped already - %d sample(s) "
                        "at full scale, longest run %d - no level can undo that"
                        % (label, before["clipped"], before["longest_run"]))

    if normalise:
        samples, before, applied = vag.level(
            samples, peak=float(level), rms=float(target_rms or 0),
            force=bool(spec.get("force")))
    else:
        applied = 1.0

    after = vag.stats(samples)

    if applied != 1.0:
        warnings.append(
            "sound %s: levelled x%.3f (%+.1f dB) - peak %d -> %d, rms %.0f%% -> "
            "%.0f%% of full scale (retail's own samples are 29-39%% rms)"
            % (label, applied, 20.0 * math.log10(applied) if applied > 0 else 0,
               before["peak"], after["peak"],
               100.0 * before["rms"] / 32767.0, 100.0 * after["rms"] / 32767.0))
    elif after["rms"] > 0.45 * 32767.0:
        warnings.append(
            "sound %s: rms is %.0f%% of full scale against retail's 29-39%% - this "
            "sample will play loud, and with crest %.1f its transients are "
            "already flat (set a lower `rms` or `gain_db` to sit it under)"
            % (label, 100.0 * after["rms"] / 32767.0, after["crest"]))

    if 0 < after["crest"] < 2.2:
        warnings.append("sound %s: crest factor %.1f (retail 2.6-3.4) - the "
                        "source has been limited, so it will sound squashed"
                        % (label, after["crest"]))

    # 16 bytes per 28 samples: how many samples fit the byte budget
    budget = int(spec.get("sample_bytes") or budget)
    limit = (budget // vag.BLOCK_BYTES) * vag.SAMPLES_PER_BLOCK

    if limit > 0 and len(samples) > limit:
        warnings.append("sound %s: trimmed from %.2fs to %.2fs to fit %d bytes "
                        "(the SPU has no room for more)"
                        % (label, len(samples) / float(rate or 1),
                           limit / float(rate or 1), budget))
        samples = samples[:limit]

    loop_from = int(float(spec.get("loop_from") or 0) * rate)
    loop_end = None
    looping = spec.get("loop")

    if looping == "auto":
        # The SPU cannot crossfade a loop: the join has to meet itself or it
        # clicks once per cycle, which on a car engine reads as gravel. Pick the
        # block-aligned pair with the smallest join and trim the tail to it.
        found = vag.find_loop(samples, spec.get("loop_from") or 0.0, rate)

        if found is None:
            warnings.append("sound %s: too short to search for a loop point; "
                            "looping the whole buffer" % label)
            looping = True
        else:
            before = vag.seam_error(samples, int(float(spec.get("loop_from") or 0)
                                                * rate), len(samples) - 1)
            loop_from = found["start"]
            loop_end = found["end"] + 1
            warnings.append(
                "sound %s: loop %d..%d (%.3fs); the join drops from %d to %d"
                % (label, found["start"], loop_end, found["seconds"],
                   before, found["seam"]))
            looping = True

    if spec.get("loop_to"):
        loop_end = min(int(float(spec["loop_to"]) * rate),
                       len(samples) if loop_end is None else loop_end)

    data, count, rms = vag.encode(samples, loop=bool(looping),
                                  loop_from=loop_from, loop_end=loop_end)

    if rms > 4000:
        warnings.append("sound %s: encode error is high (rms %.0f) - the sample "
                        "is probably too loud or too noisy for 4-bit ADPCM"
                        % (label, rms))

    # The rate FIELD is what the game multiplies by its own pitch request, so
    # this is the knob that decides where the sample lands in play. Measured
    # against gamesnd.c: a rev sample is asked for at 1500+revs/4 over 4096, an
    # idle at 4096+revs/4 over 4096.
    pitch = float(spec.get("pitch") or 1.0)
    field = int(round(vag.rate_field(rate) * pitch))

    if pitch != 1.0:
        warnings.append("sound %s: pitch x%.2f - declared rate %d Hz plays at "
                        "%.0f Hz when the game asks %.2f"
                        % (label, pitch, rate, rate * pitch,
                           1500.0 / 4096.0))

    # the stream's real length: what decode() returns, not the length before the
    # loop-end trim (a trimmed loop would otherwise report its old duration)
    count = (len(data) // vag.BLOCK_BYTES) * vag.SAMPLES_PER_BLOCK
    measured = vag.stats(vag.decode(data, count))

    return {"data": data, "rate": rate, "loop": 1 if looping else 0,
            "samples": count, "rate_field": field,
            "level": float(level), "gain_db": gain, "pitch": pitch,
            "rms": measured["rms"], "crest": measured["crest"],
            "seam": vag.seam_error(vag.decode(data, count), loop_from,
                                   (loop_end or count) - 1) if looping else 0}


def build_sounds_body(specs, warnings):
    """LUMP_CAR_SOUNDS body + the sirens list, from the manifest's `sounds`.

    One entry per model, three samples each, exactly as
    models.c CarImportSoundEntry reads it back: `int 1, int 13, 13 offsets`,
    then per model `int 3, three SAMPLE_DATA, the VAG payload`, with addresses
    relative to the bank's own payload. The engine rejects a bank whose count is
    not 3, so a half-declared car is filled the way retail does it - the engine
    sample doubles as the idle.
    """
    models = {int(m["model"]): m for m in (specs.get("models") or [])
              if m.get("model") is not None}
    sirens = sorted(int(m) for m in (specs.get("sirens") or []))

    if not models:
        return None, sirens

    offsets = [-1] * MAX_CAR_MODELS
    body = bytearray(struct.pack("<ii", 1, MAX_CAR_MODELS))
    body += struct.pack("<%di" % MAX_CAR_MODELS, *offsets)

    for model in sorted(models):
        if not 0 <= model < MAX_CAR_MODELS:
            warnings.append("sound: model %d is outside 0..%d"
                            % (model, MAX_CAR_MODELS - 1))
            continue

        entry = models[model]
        engine = _sound_sample(entry.get("engine") or {}, warnings,
                               "model %d engine" % model)

        if engine is None:
            warnings.append("sound: model %d declares no usable engine sample"
                            % model)
            continue

        idle = _sound_sample(entry.get("idle") or {}, warnings,
                             "model %d idle" % model) or engine
        third = _sound_sample(entry.get("third") or {}, warnings,
                              "model %d horn/siren" % model)

        if third is None:
            warnings.append("sound: model %d declares no horn/siren; its third "
                            "sample repeats the engine" % model)
            third = engine

        offsets[model] = len(body)
        payload = bytearray()
        rows = []

        for sample in (engine, idle, third):
            rows.append((len(payload), len(sample["data"]), sample["loop"],
                         sample["rate_field"]))
            payload.extend(sample["data"])

        # pad the payload to a 4-byte boundary so the next entry starts aligned
        while len(payload) % 4:
            payload.append(0)

        body += struct.pack("<i", SOUND_SAMPLES)

        for row in rows:
            body += struct.pack("<IIII", *row)

        body += payload

        warnings.append("sound model %d: %d bytes - %s"
                        % (model, sum(r[1] for r in rows),
                           "; ".join(
                               "%s %d B %.2fs rms %d%% crest %.1f field %d%s%s"
                               % (name, sample["data"] and len(sample["data"]),
                                  len(sample["data"]) / (sample["rate"] / 2.0)
                                  if sample["rate"] else 0.0,
                                  100.0 * sample["rms"] / 32767.0,
                                  sample["crest"], sample["rate_field"],
                                  " loop" if sample["loop"] else "",
                                  (" seam %d" % sample["seam"])
                                  if sample["loop"] else "")
                               for name, sample in
                               (("engine", engine), ("idle", idle),
                                ("third", third)))))

    for i in range(MAX_CAR_MODELS):
        struct.pack_into("<i", body, 8 + i * 4, offsets[i])

    if all(o == -1 for o in offsets):
        return None, sirens

    return bytes(body), sirens


def build_sirens_body(sirens):
    """LUMP_CAR_SIRENS: 13 flags (bit N = model N is a cop car), padded to 16."""
    if not sirens:
        return None

    flags = bytes(1 if m in sirens else 0 for m in range(MAX_CAR_MODELS))
    return flags + bytes(3)


def _load_sounds(manifest):
    """The manifest's `sounds` block, or {} when the bank declares none."""
    specs = manifest.get("sounds") or {}

    if not isinstance(specs, dict):
        raise SystemExit("bank: `sounds` must be an object with models/sirens")

    return specs


def _load_wheels(manifest):
    """The manifest's `wheels` list, normalised to one entry per variant.

    Written by the bake (carbank_bake), so this is a reader with a shape check
    rather than a second schema: a hand-edited manifest that names an unknown
    variant or an island that is not a crop rect is refused by name.
    """
    raw = manifest.get("wheels") or []

    if isinstance(raw, dict):
        entries = [dict(spec, variant=variant) for variant, spec in raw.items()
                   if spec]
    else:
        entries = [spec for spec in raw if spec]

    by_variant = {}

    for spec in entries:
        if not isinstance(spec, dict):
            raise SystemExit("bank: a wheels entry must be an object, not %r"
                             % (spec,))

        variant = spec.get("variant")

        if variant not in WHEEL_VARIANTS:
            raise SystemExit("bank: wheel variant %r is not one of %s"
                             % (variant, ", ".join(WHEEL_VARIANTS)))

        setid = spec.get("set")
        island = spec.get("island")

        if not isinstance(setid, int) or not 1 <= setid <= 255:
            raise SystemExit("bank: wheel %s names page set %r, which is not a "
                             "texture set id" % (variant, setid))

        if (not isinstance(island, (list, tuple)) or len(island) != 4
                or not all(isinstance(v, int) for v in island)
                or island[2] <= island[0] or island[3] <= island[1]):
            raise SystemExit("bank: wheel %s has no usable crop rect (%r)"
                             % (variant, island))

        by_variant[variant] = {"variant": variant, "set": setid,
                               "island": list(island)}

    return [by_variant.get(variant) for variant in WHEEL_VARIANTS]


def assemble_bank(models_body, texinfo_body, pallet_body, page_blobs,
                  texture_names_body=None, model_names_body=None,
                  wheels_body=None, sounds_body=None, sirens_body=None):
    """A loadable car-data bank: header, table, DATA1, sector-aligned pages."""
    seg = bytearray()

    def add(typ, payload):
        seg.extend(struct.pack("<ii", typ, len(payload)))
        seg.extend(payload)
        seg.extend(b"\x00" * ((4 - len(seg) % 4) % 4))

    add(LUMP_CAR_MODELS, models_body)
    if wheels_body:
        add(LUMP_CAR_WHEELS, wheels_body)
    if sounds_body:
        add(LUMP_CAR_SOUNDS, sounds_body)
    if sirens_body:
        add(LUMP_CAR_SIRENS, sirens_body)
    add(LUMP_TEXTUREINFO, texinfo_body)
    add(LUMP_PALLET, pallet_body)
    if texture_names_body:
        add(LUMP_TEXTURENAMES, texture_names_body)
    if model_names_body:
        add(LUMP_MODELNAMES, model_names_body)

    data1 = bytearray(struct.pack("<ii", LUMP_CAR_DATA, len(seg) + 8))
    data1 += seg
    data1 += b"\x00" * ((SECTOR - len(data1) % SECTOR) % SECTOR)

    out = bytearray()
    out += struct.pack("<ii", LUMP_LUMPDESC, DATA1_OFF - 8)
    out += struct.pack("<8I", DATA1_OFF, len(data1), 0, 0, 0, 0, 0, 0)
    out += b"\x00" * (DATA1_OFF - len(out))
    out += data1
    for blob in page_blobs:
        out += blob
        out += b"\x00" * ((SECTOR - len(out) % SECTOR) % SECTOR)
    return bytes(out)


def _car_raw_blocks(car, manifest_dir, source_blob, table):
    """{variant: bytes} from the exported raw files, or the source level."""
    out = {}
    for variant, spec in car.get("variants", {}).items():
        raw_rel = spec.get("raw")
        if raw_rel:
            path = os.path.join(manifest_dir, raw_rel)
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    out[variant] = f.read()
                continue
        if source_blob is not None and table is not None:
            blocks = model_blocks(source_blob, table, car["model"])
            if variant in blocks:
                out[variant] = blocks[variant]
    return out


def _load_order(manifest, manifest_dir, order_path):
    """[(slot, car_id)] from bank_order or an explicit order file."""
    raw = None
    if order_path:
        order = _read_json(order_path)
        if isinstance(order, dict):
            raw = order.get("bank_order", order.get("order"))
        else:
            raw = order
    if raw is None:
        raw = manifest.get("bank_order", [])

    known = {c["id"] for c in manifest.get("models", [])}
    out = []
    next_slot = 0
    for entry in raw:
        if isinstance(entry, str):
            while next_slot in (5, 6, 7):
                next_slot += 1
            slot, car = next_slot, entry
            next_slot += 1
        else:
            slot = entry.get("slot")
            car = entry.get("car", entry.get("id"))
        if not isinstance(slot, int) or not 0 <= slot < MAX_CAR_MODELS:
            raise SystemExit("bank order: slot %r is outside 0..%d"
                             % (slot, MAX_CAR_MODELS - 1))
        if car not in known:
            raise SystemExit("bank order: %r is not an exported car ID" % (car,))
        if any(s == slot for s, _c in out):
            raise SystemExit("bank order: slot %d appears twice" % slot)
        out.append((slot, car))
    return sorted(out)


def build_bank(manifest, manifest_path, out_dir, order_path):
    """Build <CITY>.LEV + <CITY>.LCF from a manifest and its raw blocks."""
    manifest_dir = os.path.dirname(os.path.abspath(manifest_path))
    city = manifest["city"]
    os.makedirs(out_dir, exist_ok=True)

    source_blob = None
    source_table = None
    src = manifest.get("source", {})
    if src.get("path") and os.path.isfile(src["path"]):
        with open(src["path"], "rb") as f:
            source_blob = f.read()
        try:
            source_table = d2_model_table(source_blob)
        except Exception:
            source_table = None

    order = _load_order(manifest, manifest_dir, order_path)
    cars = {c["id"]: c for c in manifest.get("models", [])}
    blocks_by_slot = {}
    used_cars = []
    warnings = []
    for slot, car_id in order:
        car = cars[car_id]
        blocks = _car_raw_blocks(car, manifest_dir, source_blob, source_table)
        missing = [v for v in ("clean", "damaged", "low") if v not in blocks]
        if missing:
            raise SystemExit(
                "bank: slot %d (%s) is missing %s; a Driver 2 car-data model "
                "must have clean, damaged and low. Re-export with all three "
                "variants, or use --no-bank."
                % (slot, car_id, ", ".join(missing)))
        if slot in (5, 6, 7):
            warnings.append("slot %d is a model D2 ships no data for; the bank "
                            "will still write it" % slot)
        blocks_by_slot[slot] = blocks
        used_cars.append((slot, car_id, blocks))

    models_body = build_models_body(blocks_by_slot)

    # --- texture info -----------------------------------------------------
    raw = manifest.get("raw", {})
    ti_path = os.path.join(manifest_dir, raw.get("textureinfo", ""))
    if not os.path.isfile(ti_path):
        raise SystemExit("bank: missing raw textureinfo at %s" % ti_path)
    with open(ti_path, "rb") as f:
        ti_bytes = f.read()
    head, perms, specs = split_textureinfo(ti_bytes)

    included = set()
    for _slot, car_id, _blocks in used_cars:
        included.update(cars[car_id].get("sets", []))
    included = {s for s in included if s not in (0, 255)}
    wheel_specs = _load_wheels(manifest)
    wheel_sets = set(manifest.get("wheel_sets", []))
    # a declared wheel names its page, so that page is part of the bank whether or
    # not any body poly samples it
    wheel_sets.update(spec["set"] for spec in wheel_specs if spec)
    available = {s for s, _z in perms} | {s for s, _z in specs}
    missing_sets = sorted(included - available)
    if missing_sets:
        raise SystemExit(
            "bank: car texture set(s) %s are not in the level page lists; "
            "refusing to write a bank that cannot draw them"
            % " ".join(str(s) for s in missing_sets))
    missing_wheels = sorted(wheel_sets - available)
    if missing_wheels:
        raise SystemExit(
            "bank: wheel page set(s) %s are not in the level page lists; a "
            "wheel model names a page, and one the bank does not carry draws "
            "the host wheel's pixels"
            % " ".join(str(s) for s in missing_wheels))
    included = (included | wheel_sets) & available
    if not included:
        warnings.append("no texture sets resolved; the bank will have no pages")

    perms = [(s, z) for s, z in perms if s in included]
    specs = [(s, z) for s, z in specs if s in included]
    texinfo_body = build_textureinfo(head, perms, specs)

    # --- palette ----------------------------------------------------------
    pal_path = os.path.join(manifest_dir, raw.get("pallet", ""))
    pal_blob = b""
    palette_records = 0
    if os.path.isfile(pal_path):
        with open(pal_path, "rb") as f:
            pal_blob = f.read()
        pal = d2_palette_from_bytes(pal_blob)
        pal_blob, palette_records = filter_palette(pal, included)
    else:
        warnings.append("no raw palette; bank will have no car colours")

    # --- name buffers and pages ------------------------------------------
    def read_optional(key):
        p = raw.get(key)
        if not p:
            return b""
        path = os.path.join(manifest_dir, p)
        if not os.path.isfile(path):
            return b""
        with open(path, "rb") as f:
            return f.read()

    texture_names_body = read_optional("texturenames")
    model_names_body = read_optional("modelnames")

    page_blobs = []
    page_report = []
    for s, z in perms + specs:
        # The bank folder is the workspace: pages live in pages/ beside the
        # manifest. A bank made before that layout still has them under
        # raw/pages/, so read those too - with a warning, because the old
        # shape no longer matches what basebank writes.
        p = os.path.join(manifest_dir, "pages", "PAGE_%d.bin" % s)
        if not os.path.isfile(p):
            legacy = os.path.join(manifest_dir, "raw", "pages",
                                  "PAGE_%d.bin" % s)
            if os.path.isfile(legacy):
                warnings.append("set %d was read from the old raw/pages layout"
                                % s)
                p = legacy
            else:
                # A page the texture info LISTS but the workspace has no bytes
                # for used to be dropped with a warning, so the bank shipped
                # without its textures - and a car then draws the host level's
                # pages, which reads as contamination rather than as a mistake.
                raise SystemExit(
                    "bank: page set %d is listed in LUMP_TEXTUREINFO but there "
                    "is no %s (looked in %s). Bake the page first:\n"
                    "  python carbank_page.py <image> --set %d --bank %s"
                    % (s, os.path.basename(p), manifest_dir, s, manifest_dir))
        with open(p, "rb") as f:
            data = f.read()
        if len(data) != z:
            warnings.append("set %d raw page is %d bytes, expected %d"
                            % (s, len(data), z))
        page_blobs.append(data)
        page_report.append({"set": s, "size": len(data)})

    wheels_body = None
    if any(wheel_specs):
        wheels_body = build_wheel_body(wheel_specs)

    sound_specs = _load_sounds(manifest)
    sounds_body, sirens = build_sounds_body(sound_specs, warnings)
    sirens_body = build_sirens_body(sirens)

    bank_blob = assemble_bank(models_body, texinfo_body, pal_blob, page_blobs,
                              texture_names_body, model_names_body,
                              wheels_body, sounds_body, sirens_body)

    lev_path = os.path.join(out_dir, "%s.LEV" % city)
    with open(lev_path, "wb") as f:
        f.write(bank_blob)

    # --- LCF in the same slot order --------------------------------------
    records = _lcf_records(manifest, manifest_dir, cars, order)
    lcf_path = os.path.join(out_dir, "%s.LCF" % city)
    with open(lcf_path, "wb") as f:
        f.write(cosmetics1.make_lcf(records))

    report = {
        "city": city,
        "lev": _rel(lev_path, os.path.dirname(os.path.abspath(manifest_path))),
        "lcf": _rel(lcf_path, os.path.dirname(os.path.abspath(manifest_path))),
        "size": len(bank_blob),
        "order": [{"slot": s, "car": c} for s, c in order],
        "included_sets": sorted(included),
        "pages": page_report,
        "wheels": wheel_specs,
        "sounds": {"models": sorted((spec.get("model")
                                     for spec in (sound_specs.get("models") or [])),
                                    key=lambda m: (m is None, m)),
                   "sirens": sirens} if sounds_body else {},
        "palette_records": palette_records,
        "page_base": d2_page_base(bank_blob),
        "warnings": warnings,
    }
    report["checks"] = verify_bank(lev_path, lcf_path, order, cars,
                                   manifest_dir, included, wheel_specs,
                                   sound_specs)
    _write_json(os.path.join(out_dir, "bank.json"), report)
    for w in warnings:
        print("  warning: %s" % w)
    print("  bank ok: %s (%d bytes, %d cars, %d sets)"
          % (os.path.basename(lev_path), len(bank_blob), len(order),
             len(included)))
    if any(wheel_specs):
        print("  wheels: %s" % ", ".join(
            "%s set %d %s" % (s["variant"], s["set"], list(s["island"]))
            if s else "absent" for s in wheel_specs))
    return report


def d2_palette_from_bytes(pal_bytes):
    """d2_palette() for a standalone palette body (the bank source)."""
    body = pal_bytes
    end = len(body)
    total = _i32(body, 0) if len(body) >= 4 else 0
    q = 4
    stored = []
    records = []
    while q + 16 <= end:
        palette, texnum, tpage, clut = struct.unpack_from("<iiii", body, q)
        if palette == -1:
            break
        start = q
        q += 16
        if clut == -1:
            if q + 32 > end:
                break
            row = body[q:q + 32]
            q += 32
            stored.append(row)
        elif 0 <= clut < len(stored):
            row = stored[clut]
        else:
            row = None
        records.append({"palette": palette, "texnum": texnum,
                        "tpageindex": tpage, "clut_number": clut,
                        "clut": row, "raw": body[start:q]})
    return {"body": 0, "size": len(body), "total_cluts": total,
            "records": records, "end": q}


def _lcf_records(manifest, manifest_dir, cars, order):
    """13 records in destination-slot order, with a sane empty-slot fallback."""
    records = [None] * MAX_CAR_MODELS
    lcf_rel = manifest.get("lcf")
    source = []
    if lcf_rel:
        p = os.path.join(manifest_dir, lcf_rel)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                try:
                    source = cosmetics1.parse_lcf(f.read())
                except Exception:
                    source = []
    if not source:
        cos_rel = manifest.get("cosmetics")
        if cos_rel:
            p = os.path.join(manifest_dir, cos_rel)
            if os.path.isfile(p):
                doc = _read_json(p)
                source = [bytes.fromhex(r["raw"]) for r in doc.get("records", [])
                          if r.get("raw")]
    fallback = source[0] if source else b"\x00" * RECORD_SIZE
    for slot, car_id in order:
        car = cars.get(car_id)
        src_model = car.get("model") if car else None
        if src_model is not None and 0 <= src_model < len(source):
            records[slot] = source[src_model]
    for i in range(MAX_CAR_MODELS):
        if records[i] is None:
            records[i] = source[i] if i < len(source) else fallback
    return records


def verify_wheels(blob, specs):
    """Re-read the bank's LUMP_CAR_WHEELS the way the engine will.

    models.c BuildCarImportWheels is strict in a way that fails silently - an
    unusable record is simply "absent", so the wheel keeps the host texture and
    nothing says why. Every test it applies is applied here instead: 8..24 polys,
    the first eight FT4, and the page and crop rect read off poly 2.
    """
    problems = []
    wanted = [spec for spec in specs if spec]
    got = d2_lump(blob, LUMP_CAR_WHEELS)

    if not wanted:
        if got is not None:
            problems.append("the bank carries a wheel segment but nothing "
                            "declared one")
        return problems

    if got is None:
        return ["the bank has no wheel segment (%d variant(s) declared)"
                % len(wanted)]

    body, size = got
    count = struct.unpack_from("<i", blob, body)[0]

    if count != len(specs):
        problems.append("wheel segment holds %d variants, expected %d"
                        % (count, len(specs)))

    for i, spec in enumerate(specs):
        offset = (struct.unpack_from("<i", blob, body + 4 + i * 4)[0]
                  if i < count else -1)
        name = spec["variant"] if spec else "variant %d" % i

        if spec is None:
            if offset != -1:
                problems.append("wheel %s is not declared but the segment "
                                "carries it" % name)
            continue

        if offset < 0 or offset + 36 > size:
            problems.append("wheel %s offset %d is outside the segment (%d "
                            "bytes)" % (name, offset, size))
            continue

        model = lev1.decode_model(blob, body + offset)

        if model.errors:
            problems.append("wheel %s decoded with %d poly errors"
                            % (name, len(model.errors)))
            continue

        if not 8 <= len(model.polys) <= 24:
            problems.append("wheel %s has %d polys; the engine takes 8..24"
                            % (name, len(model.polys)))
            continue

        if any(p.kind != "FT4" for p in model.polys[:8]):
            problems.append("wheel %s is not the 8-quad FT4 wheel"
                            % name)
            continue

        poly = model.polys[2]

        if poly.texture_set != spec["set"]:
            problems.append("wheel %s names page set %d, not the declared %d"
                            % (name, poly.texture_set, spec["set"]))

        # WheelPolyRect: min/max of the UVs, then +1 on the maxima
        us = [u for u, _v in poly.uv]
        vs = [v for _u, v in poly.uv]
        rect = (min(us), min(vs), max(us) + 1, max(vs) + 1)

        if list(rect) != list(spec["island"]):
            problems.append("wheel %s covers %s, not the declared crop %s"
                            % (name, list(rect), list(spec["island"])))

    return problems


def verify_sounds(blob, specs):
    """Re-read the sound segments the way CarImportSoundEntry will.

    The reader rejects anything it cannot parse by returning 0, and the car then
    silently plays the host city's engine - so every field it checks is checked
    here too: a per-model table of 13 offsets, a count of exactly 3, offsets
    inside the segment, and each sample's payload inside it as well.
    """
    problems = []
    models = [spec for spec in (specs.get("models") or [])
              if spec.get("model") is not None]

    got = d2_lump(blob, LUMP_CAR_SOUNDS)

    if not models:
        if got is not None:
            problems.append("the bank carries a sound segment but nothing "
                            "declared one")
        return problems

    if got is None:
        return ["the bank has no sound segment (%d model(s) declared)"
                % len(models)]

    body, size = got

    if size < 8 + MAX_CAR_MODELS * 4:
        return ["the sound segment is %d bytes, shorter than its own table" % size]

    if struct.unpack_from("<i", blob, body)[0] != 1 or             struct.unpack_from("<i", blob, body + 4)[0] != MAX_CAR_MODELS:
        problems.append("the sound segment's header is not the per-model form "
                        "(1, %d)" % MAX_CAR_MODELS)

    for spec in models:
        model = spec["model"]
        offset = struct.unpack_from("<i", blob, body + 8 + model * 4)[0] if             0 <= model < MAX_CAR_MODELS else -1

        if offset < 0:
            problems.append("model %d has no sound entry" % model)
            continue

        if offset + 4 > size:
            problems.append("model %d sound entry runs past the segment" % model)
            continue

        count = struct.unpack_from("<i", blob, body + offset)[0]

        if count != SOUND_SAMPLES:
            problems.append("model %d sound entry holds %d samples, the engine "
                            "takes exactly %d" % (model, count, SOUND_SAMPLES))
            continue

        for i in range(count):
            address, length, _loop, rate = struct.unpack_from(
                "<IIII", blob, body + offset + 4 + i * 16)

            if offset + 4 + count * 16 + address + length > size:
                problems.append("model %d sample %d runs past the segment"
                                % (model, i))

            if not vag.MIN_RATE // 2 <= rate <= 2 * vag.MAX_RATE:
                problems.append("model %d sample %d has rate %d Hz, outside what "
                                "the SPU can play" % (model, i, rate))

    got = d2_lump(blob, LUMP_CAR_SIRENS)
    wanted = sorted(int(m) for m in (specs.get("sirens") or []))

    if wanted and got is None:
        problems.append("the bank has no siren flags (%s)" % wanted)
    elif wanted:
        body, size = got
        flagged = [m for m in range(min(MAX_CAR_MODELS, size))
                   if blob[body + m]]
        if flagged != wanted:
            problems.append("siren flags say %s, the declaration says %s"
                            % (flagged, wanted))

    return problems


def verify_bank(lev_path, lcf_path, order, cars, manifest_dir, included,
                wheel_specs=None, sound_specs=None):
    problems = []
    with open(lev_path, "rb") as f:
        blob = f.read()
    try:
        table = d2_model_table(blob)
    except Exception as e:
        return {"ok": False, "problems": ["cannot read bank model table: %s" % e]}
    try:
        ti_body, _ti_size, _ti_bytes, ti = d2_texture_info(blob)
        _base, entries = d2_page_entries(blob, ti)
    except Exception as e:
        return {"ok": False, "problems": ["cannot read bank pages: %s" % e]}

    for slot, car_id in order:
        car = cars[car_id]
        c, d, l = table["offsets"][slot]
        if c < 0:
            problems.append("slot %d (%s) has no clean block" % (slot, car_id))
            continue
        for vi, variant in enumerate(("clean", "damaged", "low")):
            if table["offsets"][slot][vi] < 0:
                problems.append("slot %d (%s) is missing its %s block"
                                % (slot, car_id, variant))
        if c >= 0:
            blocks = model_blocks(blob, table, slot)
            for variant, data in blocks.items():
                raw_rel = car.get("variants", {}).get(variant, {}).get("raw")
                if raw_rel:
                    p = os.path.join(manifest_dir, raw_rel)
                    if os.path.isfile(p):
                        with open(p, "rb") as f:
                            if f.read() != data:
                                problems.append("slot %d %s block differs from "
                                                "the exported raw block"
                                                % (slot, variant))

    for s in included:
        if s not in entries:
            problems.append("set %d is not listed in the bank" % s)
        else:
            off, size, _kind = entries[s]
            if size <= 8 or off + size > len(blob):
                problems.append("set %d page bytes are missing" % s)

    if wheel_specs is not None:
        problems.extend(verify_wheels(blob, wheel_specs))

    if sound_specs is not None:
        problems.extend(verify_sounds(blob, sound_specs))

    try:
        with open(lcf_path, "rb") as f:
            recs = cosmetics1.parse_lcf(f.read())
        if len(recs) != MAX_CAR_MODELS:
            problems.append("LCF has %d records, expected %d"
                            % (len(recs), MAX_CAR_MODELS))
    except Exception as e:
        problems.append("cannot parse generated LCF: %s" % e)

    if problems:
        print("  VERIFY FAILED:")
        for p in problems:
            print("    %s" % p)
    return {"ok": not problems, "problems": problems}


# ---------------------------------------------------------------------------
# index + rebuild CLI
# ---------------------------------------------------------------------------
def write_index(out_root, entries):
    index = {"version": VERSION, "tool": TOOL, "game": GAME,
             "cell_size": scalemodels.D2_CELL_SIZE,
             "cities": entries}
    path = os.path.join(out_root, "index.json")
    _write_json(path, index)
    return path


def rebuild_cli(index_or_manifest, out_dir, city, order_path):
    doc = _read_json(index_or_manifest)
    base = os.path.dirname(os.path.abspath(index_or_manifest))
    if "cities" in doc:
        entries = doc["cities"]
        if city:
            matches = [e for e in entries if e["city"].upper() == city.upper()]
        else:
            matches = entries
        if not matches:
            raise SystemExit("no city %r in %s" % (city, index_or_manifest))
        if len(matches) > 1:
            raise SystemExit("several cities in %s; pass --city"
                             % index_or_manifest)
        entry = matches[0]
        manifest_path = os.path.join(base, entry["manifest"])
        manifest = _read_json(manifest_path)
    else:
        manifest = doc
        manifest_path = index_or_manifest

    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(manifest_path)),
                               "bank")
    report = build_bank(manifest, manifest_path, out_dir, order_path)
    default_bank = os.path.join(os.path.dirname(os.path.abspath(manifest_path)),
                                "bank")
    if os.path.abspath(out_dir) == os.path.abspath(default_bank):
        manifest["bank"] = report
        _write_json(manifest_path, manifest)
    if not report["checks"].get("ok"):
        raise SystemExit("bank verification failed; see %s"
                         % os.path.join(out_dir, "bank.json"))
    return 0


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------
def _fake_textureinfo(tpage_amount, per_page, perms, specs):
    body = struct.pack("<ii", tpage_amount, 0)
    body += b"\x00" * (8 * (tpage_amount + 1))
    for n in per_page:
        body += struct.pack("<i", n) + b"\x00" * (8 * n)
    body += struct.pack("<i", len(perms))
    for s, z in perms:
        body += struct.pack("<ii", s, z)
    body += b"\x00" * ((PERM_REGION - len(perms)) * 8)
    body += struct.pack("<i", len(specs))
    for s, z in specs:
        body += struct.pack("<ii", s, z)
    return body


def selftest():
    fails = 0

    # textureinfo split/rebuild
    body = _fake_textureinfo(2, [1, 0], [(1, 100), (2, 200)], [(3, 300)])
    head, perms, specs = split_textureinfo(body)
    if perms != [(1, 100), (2, 200)] or specs != [(3, 300)]:
        print("FAIL: textureinfo lists")
        fails += 1
    built = build_textureinfo(head, [(1, 100)], [(3, 300)])
    ti = pages1.parse_texture_info(built)
    if ti["permlist"] != [(1, 100)] or ti["speclist"] != [(3, 300)]:
        print("FAIL: textureinfo rebuild")
        fails += 1
    else:
        print("ok: textureinfo split/rebuild")

    # page carving
    entries = entries_from_lists(1000, [(1, 3000), (2, 2048)], [(3, 100)])
    want = {1: (1000, 3000), 2: (5096, 2048), 3: (7144, 100)}
    if {k: (v[0], v[1]) for k, v in entries.items()} != want:
        print("FAIL: page carving", entries)
        fails += 1
    else:
        print("ok: page carving")

    # model block boundaries
    fake = bytearray(1000)
    table = {"body": 0, "size": 400, "area": MODEL_DATA_AREA,
             "offsets": [(0, 50, 80), (120, -1, -1)] + [(-1, -1, -1)] * 11}
    # data area starts at 164; blocks are 0..50, 50..80, 80..120
    blocks = model_blocks(bytes(fake), table, 0)
    if len(blocks.get("clean", b"")) != 50 or len(blocks.get("damaged", b"")) != 30 \
            or len(blocks.get("low", b"")) != 40:
        print("FAIL: model block ranges", {k: len(v) for k, v in blocks.items()})
        fails += 1
    else:
        print("ok: model block ranges")

    # bank offset table
    body = build_models_body({0: {"clean": b"A" * 10, "damaged": b"B" * 4,
                                  "low": b"C" * 6},
                              2: {"clean": b"D" * 8}})
    if len(body[:MODEL_DATA_AREA]) != MODEL_DATA_AREA:
        print("FAIL: model body header size")
        fails += 1
    offs = [struct.unpack_from("<3i", body, 4 + i * 12)
            for i in range(MAX_CAR_MODELS)]
    if offs[0] != (0, 10, 14) or offs[2] != (20, -1, -1):
        print("FAIL: bank offset table", offs[0], offs[2])
        fails += 1
    else:
        print("ok: bank offset table")

    print("selftest: %s" % ("FAILED" if fails else "clean"))
    return 1 if fails else 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def parse_slots(text):
    out = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Export Driver 2 cars, their pages/palettes and cosmetics, "
                    "and build a carhacks-loadable bank.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?",
                    help="a Driver 2 .LEV, or a LEVELS directory with --all")
    ap.add_argument("-o", "--out", default=None,
                    help="output directory (export default: tools/car/out/exportd2; "
                         "rebuild default: the manifest's bank directory)")
    ap.add_argument("--all", action="store_true",
                    help="process every .LEV in a directory")
    ap.add_argument("--models", default="0-12",
                    help="model slots to export (e.g. 0-12 or 1,2,3)")
    ap.add_argument("--variants", default="clean,damaged,low",
                    help="variants to export (default: clean,damaged,low)")
    ap.add_argument("--no-bank", action="store_true",
                    help="do not build the loadable <CITY>.LEV/.LCF")
    ap.add_argument("--rebuild-bank", metavar="INDEX_OR_MANIFEST",
                    help="rebuild a bank from an exported index/manifest")
    ap.add_argument("--city", help="city to use when rebuilding from index.json")
    ap.add_argument("--order", metavar="JSON",
                    help="bank_order override (list or {\"bank_order\": [...]})")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.rebuild_bank:
        return rebuild_cli(args.rebuild_bank, args.out, args.city, args.order)
    if not args.path:
        ap.print_help()
        return 0

    out_root = args.out or os.path.join(HERE, "out", "exportd2")
    models_filter = parse_slots(args.models)
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    os.makedirs(out_root, exist_ok=True)

    paths = []
    if os.path.isdir(args.path):
        if not args.all:
            raise SystemExit("%s is a directory; pass --all to process it"
                             % args.path)
        paths = sorted(glob.glob(os.path.join(args.path, "*.LEV")))
    else:
        paths = [args.path]

    entries = []
    for path in paths:
        try:
            entry = export_one(path, out_root, models_filter, variants,
                               not args.no_bank)
        except SystemExit as e:
            print("skip %s: %s" % (os.path.basename(path), e))
            continue
        entries.append(entry)

    if not entries:
        raise SystemExit("no Driver 2 car levels processed")
    index = write_index(out_root, entries)
    print("index: %s" % index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
