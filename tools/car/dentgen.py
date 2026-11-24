#!/usr/bin/env python3
"""dentgen.py - a bank car's own damage table (LUMP_CAR_DENTING, 208).

Driver 2 keeps one damage table per model in LEVELS/<CITY>.DEN: for each of six
zones the vertices that crumple and the polys whose texture moves, and for each
poly a "damage level" byte that decides how far its UVs shift when dented
(denting.c). The engine used to give an IMPORTED car the HOST city's table for
the same model number - indices into a different mesh - so the wrong polys
shifted onto art drawn for another palette row. This writes each bank model's
own table, from the BAKED model (so every index is the engine's) and the BAKED
pages (so every shift is tested against the art and palette row it will read).

Zones (bcollide.c: the hit's side and front/middle/rear band; model -z is the
front, measured against the stock .DEN files):
    x > 0:  front 0, middle 5, rear 4        x <= 0:  front 1, middle 2, rear 3

The level byte D, for damage level L (1 light, 2 heavy), shifts u by
(L & ~(D ^ 1)) * 64:
    D=1  +64 light, +128 heavy        D=0  nothing light, +128 heavy
    D=3  +64 light, nothing heavy     D=2  never moves
A poly gets the D whose shifts land on damage art: texels no poly covers that
the poly's OWN palette row draws opaque. Everything else is D=2, so a car with
no damage art drawn simply does not change texture when hit.

Damage modes (handling.damage in the bank JSON, per slot):
  "auto" (default) probe the baked page for each poly: a shift is only used
         when enough of its texels land on drawable art that no other poly
         already occupies. A page with damage strips at +64/+128 gets the
         vanilla progression; a page without them simply does not change
         texture when hit, which is what an undamaged-looking panel should do.
  "d1"   every textured poly that could dent gets D=1: +64 at light damage,
         +128 at heavy, whether or not there is art there. This is the vanilla
         ASSUMPTION rather than a measurement - use it when a page carries the
         strips but the probe cannot see them (art the palette row draws as
         0x0000, or strips another poly also covers).

"d1" used to be the default, and on a custom page with no damage strips it
shifted every panel 64 texels sideways into whatever art sat there: the
"damage UVs are messed up instead of following the original progression"
report. Probing is the safer default - the worst it can do is leave a car
undented-looking, where the other way round wrecks the paintwork.

Block layout (one .DEN model block, 976 bytes): u8 zone_verts[6][50],
u8 zone_polys[6][70], u8 levels[256]; 0xFF ends a zone list.
Lump body: i32 version 1, i32 13, i32 offset[13] (0 = none), blocks.
"""

import struct

import lev1

LUMP_CAR_DENTING = 208
DENT_VERSION = 1
NUM_ZONES = 6
ZONE_VERTS = 50
ZONE_POLYS = 70
LEVELS = 256
BLOCK_BYTES = NUM_ZONES * ZONE_VERTS + NUM_ZONES * ZONE_POLYS + LEVELS   # 976
MAX_CAR_MODELS = 13
MODEL_DATA_AREA = 164
HIT_FRACTION = 0.6      # of a poly's shifted texels that must be drawable art
BAND = 0.38             # |z| beyond this share of the half-length is front/rear


def zone_of(x, z, half):
    t = BAND * half
    if z < -t:
        return 0 if x > 0 else 1
    if z > t:
        return 4 if x > 0 else 3
    return 5 if x > 0 else 2


def _hull(points):
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def texels(uvs):
    """The page texels a poly's UV polygon covers (centres inside, edges in)."""
    if not uvs:
        return []
    us = [p[0] for p in uvs]
    vs = [p[1] for p in uvs]
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    hull = _hull([(float(u), float(v)) for u, v in uvs])

    if len(hull) < 3:
        return [(u, v) for u in range(u0, u1 + 1) for v in range(v0, v1 + 1)]

    out = []
    n = len(hull)
    for v in range(v0, v1 + 1):
        for u in range(u0, u1 + 1):
            px, py = u + 0.5, v + 0.5
            inside = True
            for i in range(n):
                a, b = hull[i], hull[(i + 1) % n]
                if (b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0]) < -0.5 * (
                        abs(b[0] - a[0]) + abs(b[1] - a[1])):
                    inside = False
                    break
            if inside:
                out.append((u, v))
    return out or [(u0, v0)]


def page_from_entry(data):
    """(clut rows as lists of 16 words, 256x256 indices) of a baked page entry."""
    import pages1
    rows, packed = pages1.read_page(data, 0, len(data))
    words = [list(struct.unpack("<16H", r)) for r in rows]
    idx = bytearray(65536)
    k = 0
    for y in range(256):
        for b in packed[y * 128:(y + 1) * 128]:
            idx[k] = b & 0xF
            idx[k + 1] = b >> 4
            k += 2
    return words, bytes(idx)


def decode_clean_models(models_body):
    """{slot: lev1 Model} for every slot the bank carries a clean model for."""
    out = {}
    for slot in range(MAX_CAR_MODELS):
        c = struct.unpack_from("<i", models_body, 4 + slot * 12)[0]
        if c < 0:
            continue
        out[slot] = lev1.decode_model(models_body, MODEL_DATA_AREA + c)
    return out


def build_dent_body(models_body, pages_by_set, damage_modes=None):
    """(LUMP_CAR_DENTING body or None, {slot: summary}).

    `pages_by_set` is {set: baked page entry bytes}.
    `damage_modes` is {slot: "d1"|"auto"}; absent slots default to "auto".
    "auto": probe page art to pick D per poly (see the module docstring).
    "d1": D=1 for all textured denting polys (vanilla: +64 light, +128 heavy)."""
    if damage_modes is None:
        damage_modes = {}
    models = decode_clean_models(models_body)
    pages = {s: page_from_entry(d) for s, d in pages_by_set.items()}

    # every texel any poly of any model draws, per set: a shift onto one of these
    # would show another panel, not damage art
    covered = {s: bytearray(65536) for s in pages}
    poly_texels = {}
    for slot, m in models.items():
        for pi, p in enumerate(m.polys):
            if p.texture_set not in pages or not p.uv:
                continue
            tx = texels(p.uv)
            poly_texels[(slot, pi)] = tx
            cov = covered[p.texture_set]
            for u, v in tx:
                if 0 <= u < 256 and 0 <= v < 256:
                    cov[v * 256 + u] = 1

    def hit(p, tx, shift):
        rows, idx = pages[p.texture_set]
        row = rows[p.texture_id] if p.texture_id < len(rows) else (rows[0] if rows else None)
        if row is None or not tx or max(u for u, _ in tx) + shift > 255:
            return False
        cov = covered[p.texture_set]
        good = 0
        for u, v in tx:
            t = v * 256 + u + shift
            if not cov[t] and row[idx[t]] != 0:
                good += 1
        return good >= max(1, HIT_FRACTION * len(tx))

    blocks = {}
    report = {}
    for slot, m in models.items():
        verts = m.vertices
        if not verts:
            continue
        half = max(abs(v[2]) for v in verts) or 1

        levels = bytearray([2] * LEVELS)
        counts = {0: 0, 1: 0, 2: 0, 3: 0}
        mode = damage_modes.get(slot, "auto")
        for pi, p in enumerate(m.polys[:LEVELS]):
            tx = poly_texels.get((slot, pi))
            d = 2
            if tx:
                if mode == "auto":
                    # probe the page: only shift if damage art lands there
                    h64, h128 = hit(p, tx, 64), hit(p, tx, 128)
                    d = 1 if (h64 and h128) else 0 if h128 else 3 if h64 else 2
                else:
                    # "d1" mode: ASSUME damage art at +64/+128, the vanilla
                    # D1/D2 layout, without looking at the page. Every
                    # textured denting poly shifts: +64 light, +128 heavy.
                    d = 1
            levels[pi] = d
            counts[d] += 1

        zv = bytearray([0xFF] * (NUM_ZONES * ZONE_VERTS))
        for z in range(NUM_ZONES):
            members = [i for i, v in enumerate(verts[:255])
                       if zone_of(v[0], v[2], half) == z]
            # the most exposed first: the corner of the band the zone names
            members.sort(key=lambda i: -(abs(verts[i][2]) if z in (0, 1, 3, 4)
                                         else abs(verts[i][0])))
            for k, i in enumerate(members[:ZONE_VERTS - 1]):
                zv[z * ZONE_VERTS + k] = i

        zp = bytearray([0xFF] * (NUM_ZONES * ZONE_POLYS))
        for z in range(NUM_ZONES):
            members = []
            for pi, p in enumerate(m.polys[:255]):
                if not p.v or levels[pi] == 2:
                    continue
                cx = sum(verts[i][0] for i in p.v) / len(p.v)
                cz = sum(verts[i][2] for i in p.v) / len(p.v)
                if zone_of(cx, cz, half) == z:
                    members.append(pi)
            for k, pi in enumerate(members[:ZONE_POLYS - 1]):
                zp[z * ZONE_POLYS + k] = pi

        blocks[slot] = bytes(zv) + bytes(zp) + bytes(levels)
        report[slot] = {"polys": len(m.polys), "mode": mode,
                        "shift_64_128": counts[1],
                        "shift_128": counts[0], "shift_64": counts[3],
                        "still": counts[2]}

    if not blocks:
        return None, report

    head = 8 + 4 * MAX_CAR_MODELS
    body = bytearray(struct.pack("<ii", DENT_VERSION, MAX_CAR_MODELS))
    offsets = []
    cursor = head
    for slot in range(MAX_CAR_MODELS):
        if slot in blocks:
            offsets.append(cursor)
            cursor += BLOCK_BYTES
        else:
            offsets.append(0)
    body += struct.pack("<13i", *offsets)
    for slot in range(MAX_CAR_MODELS):
        if slot in blocks:
            body += blocks[slot]
    assert len(body) == cursor
    return bytes(body), report
