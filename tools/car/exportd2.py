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
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

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

WHEEL_DETAIL_NAMES = ("WHLCLEAN", "WHLBOTH", "WHEEL")
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
    out = bytearray(struct.pack("<i", total))
    for r in kept:
        out += struct.pack("<iiii", r["palette"], r["texnum"],
                           r["tpageindex"], -1)
        out += r["clut"]
    out += struct.pack("<i", -1)
    return bytes(out), len(kept)


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


def page_png(path, rows, idx):
    pal = pages1.clut_rgb(rows[0]) if rows else [(0, 0, 0)] * 16
    pix = bytearray(256 * 256 * 3)
    for i, v in enumerate(idx):
        if v < len(pal):
            r, g, b = pal[v]
        else:
            r = g = b = 0
        pix[i * 3:i * 3 + 3] = bytes((r, g, b))
    write_png_rgb(path, 256, 256, pix)


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
        mat = ("set%d_id%d" % (p.texture_set, p.texture_id)
               if textured else "flat")
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


def assemble_bank(models_body, texinfo_body, pallet_body, page_blobs,
                  texture_names_body=None, model_names_body=None):
    """A loadable car-data bank: header, table, DATA1, sector-aligned pages."""
    seg = bytearray()

    def add(typ, payload):
        seg.extend(struct.pack("<ii", typ, len(payload)))
        seg.extend(payload)
        seg.extend(b"\x00" * ((4 - len(seg) % 4) % 4))

    add(LUMP_CAR_MODELS, models_body)
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
    wheel_sets = set(manifest.get("wheel_sets", []))
    available = {s for s, _z in perms} | {s for s, _z in specs}
    missing_sets = sorted(included - available)
    if missing_sets:
        raise SystemExit(
            "bank: car texture set(s) %s are not in the level page lists; "
            "refusing to write a bank that cannot draw them"
            % " ".join(str(s) for s in missing_sets))
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
        p = os.path.join(manifest_dir, "raw", "pages", "PAGE_%d.bin" % s)
        if not os.path.isfile(p):
            warnings.append("missing raw page for set %d; bank page omitted" % s)
            continue
        with open(p, "rb") as f:
            data = f.read()
        if len(data) != z:
            warnings.append("set %d raw page is %d bytes, expected %d"
                            % (s, len(data), z))
        page_blobs.append(data)
        page_report.append({"set": s, "size": len(data)})

    bank_blob = assemble_bank(models_body, texinfo_body, pal_blob, page_blobs,
                              texture_names_body, model_names_body)

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
        "palette_records": palette_records,
        "page_base": d2_page_base(bank_blob),
        "warnings": warnings,
    }
    report["checks"] = verify_bank(lev_path, lcf_path, order, cars,
                                   manifest_dir, included)
    _write_json(os.path.join(out_dir, "bank.json"), report)
    for w in warnings:
        print("  warning: %s" % w)
    print("  bank ok: %s (%d bytes, %d cars, %d sets)"
          % (os.path.basename(lev_path), len(bank_blob), len(order),
             len(included)))
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


def verify_bank(lev_path, lcf_path, order, cars, manifest_dir, included):
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
