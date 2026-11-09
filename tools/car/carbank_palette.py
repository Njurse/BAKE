#!/usr/bin/env python3
"""carbank_palette.py - bake palette.json into the bank's LUMP_PALLET source.

    python carbank_palette.py banks/firebird/palette.json -o banks/firebird/pallet.bin
    python carbank_palette.py banks/firebird/bank.json --check

The bank bake reads `raw.pallet` (a binary LUMP_PALLET body) and never the
`palette.json` beside it, so editing a colour in the JSON used to change
nothing. This is the missing half of that pair: it writes the body the bake
reads, from the rows the JSON describes. `pallet.bin` is the engine lump;
`palette.json` is the author view of those same rows.

`palette.json` carries each row twice - `clut` (the 32 raw bytes, hex) and `rgb`
(the same 16 colours as 8-bit triples). `rgb` WINS, because it is the one a
person edits; `clut` is kept so a row with no `rgb` still round-trips. The
conversion is `fourbpp.rgb_to_clut`, the same one every page goes through, so
`clut_rgb` -> `rgb_to_clut` is exact (`>> 3` then `<< 3` loses nothing).

Records are written INLINE (clut_number -1) with a -1 terminator - see
exportd2.build_palette_body. A reference record is resolved, never reproduced:
a reference dangles as soon as a row ahead of it is dropped or reordered.

    --check   write nothing; exit 1 if the file on disk is not what the JSON says
    --sets    keep only these texture sets (the bank filters again anyway)
"""

import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))

import exportd2
import fourbpp

ROW_BYTES = fourbpp.CLUT_ROW_BYTES


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _row_from_record(r):
    """One record's 32-byte CLUT row, honouring an edit to `rgb`.

    `clut` is the exact bytes and `rgb` the same 16 colours as 8-bit triples, so
    an untouched row is taken VERBATIM: rebuilding it from `rgb` would clear bit
    15 (STP, "semi-transparent"), which the engine reads - measured on the
    Firebird's first car row, where every entry carries it.

    When `rgb` HAS been edited the colours come from the edit and the STP bits are
    carried over from the old row, so editing a colour cannot silently make a
    semi-transparent entry opaque.
    """
    old = None

    if r.get("clut"):
        old = bytes.fromhex(r["clut"])

        if len(old) != ROW_BYTES:
            raise SystemExit("a palette row is %d bytes, expected %d"
                             % (len(old), ROW_BYTES))

    rgb = r.get("rgb")

    if not rgb:
        return old

    triples = [(int(c[0]), int(c[1]), int(c[2])) for c in rgb]

    if len(triples) != fourbpp.CLUT_ROW_ENTRIES:
        raise SystemExit("a palette row has %d colours, expected %d"
                         % (len(triples), fourbpp.CLUT_ROW_ENTRIES))

    if old is not None and [tuple(c) for c in fourbpp.clut_rgb(old)] == triples:
        return old                      # untouched: keep the exact bytes

    new = bytearray(fourbpp.rgb_to_clut(triples))

    if old is not None:
        for i in range(fourbpp.CLUT_ROW_ENTRIES):
            if struct.unpack_from("<H", old, i * 2)[0] & 0x8000:
                struct.pack_into("<H", new, i * 2,
                                 struct.unpack_from("<H", new, i * 2)[0] | 0x8000)

    return bytes(new)


def rows_from_doc(doc, sets=None):
    """palette.json -> records ready for exportd2.build_palette_body."""
    out = []

    for r in doc.get("records") or []:
        if sets and r.get("tpageindex") not in sets:
            continue

        row = _row_from_record(r)

        if row is None:
            continue

        out.append({"palette": r.get("palette", 0),
                    "texnum": r.get("texnum", 0),
                    "tpageindex": r.get("tpageindex", 0),
                    "clut_number": -1,
                    "clut": row})

    return out


def body_from_doc(doc, sets=None):
    """The LUMP_PALLET body palette.json describes."""
    return exportd2.build_palette_body(rows_from_doc(doc, sets),
                                       doc.get("total_cluts"))


def _resolve(source):
    """(palette.json, the pallet.bin it produces, the file --check inspects)."""
    path = os.path.abspath(source)

    if path.lower().endswith("bank.json"):
        base = os.path.dirname(path)
        js = os.path.join(base, "palette.json")
        lev = os.path.join(base, "pallet.bin")

        if not os.path.isfile(js):
            raise SystemExit("%s has no palette.json beside it" % path)

        # --check asks the question that matters - is the BANK's palette what the
        # JSON says - so it reads the level, not the source body (which may hold
        # rows for sets this bank does not carry)
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)

        subject = os.path.join(base, doc.get("lev") or "")

        if not doc.get("lev") or not os.path.isfile(subject):
            subject = lev

        return js, lev, subject, set(doc.get("included_sets") or [])

    return path, os.path.join(os.path.dirname(path), "pallet.bin"), \
        os.path.join(os.path.dirname(path), "pallet.bin"), None


def page_sets(path):
    """The texture sets a level (or bare bank) carries, read from its own table."""
    with open(path, "rb") as f:
        data = f.read()

    try:
        _off, _size, _bytes, ti = exportd2.d2_texture_info(data)
        _base, entries = exportd2.d2_page_entries(data, ti)
        return set(entries)
    except (ValueError, struct.error):
        return set()


def embedded_palette_body(path, sets):
    """The LUMP_PALLET rows a level (or a bare body) holds, filtered to `sets`."""
    with open(path, "rb") as f:
        data = f.read()

    try:
        got = exportd2.d2_lump(data, exportd2.LUMP_PALLET)
    except ValueError:
        got = None

    body = data if got is None else data[got[0]:got[0] + got[1]]
    pal = exportd2.d2_palette_from_bytes(body)
    return exportd2.filter_palette(pal, sets)[0], len(pal["records"])


def _is_body(rgb):
    """Default body test for paints: a mid/light blue-ish entry."""
    import colorsys
    h, l, s = colorsys.rgb_to_hls(*(c / 255.0 for c in rgb))
    return 0.40 <= h <= 0.75 and s >= 0.12 and l >= 0.30


def paint_row(row, target, body=None, protect=(), tolerance=8):
    """`row` (32 CLUT bytes) with its BODY entries recoloured toward `target`.

    Hue and saturation come from the target; lightness keeps the source's
    shading, scaled so the brightest body entry lands on the target's own
    lightness. 0x0000 (transparent), protected colours and non-body entries are
    copied verbatim, and every entry keeps its STP bit.
    """
    import colorsys

    def near(a, b):
        return all(abs(x - y) <= tolerance for x, y in zip(a, b))

    rgb = fourbpp.clut_rgb(row)
    raw = [int.from_bytes(row[i * 2:i * 2 + 2], "little") for i in range(16)]
    pick = []
    for i, c in enumerate(rgb):
        if raw[i] == 0 or any(near(c, p) for p in protect):
            continue
        if body is not None:
            if any(near(c, b) for b in body):
                pick.append(i)
        elif _is_body(c):
            pick.append(i)
    if not pick:
        return bytes(row), 0

    th, tl, ts = colorsys.rgb_to_hls(*(v / 255.0 for v in target))
    top_l = max(colorsys.rgb_to_hls(*(v / 255.0 for v in rgb[i]))[1] for i in pick) or 1.0
    top_s = max(colorsys.rgb_to_hls(*(v / 255.0 for v in rgb[i]))[2] for i in pick) or 1.0
    out = bytearray(row)
    for i in pick:
        _h, l, s = colorsys.rgb_to_hls(*(v / 255.0 for v in rgb[i]))
        nl = min(1.0, l * tl / top_l)
        ns = min(1.0, ts * (s / top_s)) if ts > 0 else 0.0
        r, g, b = (int(round(v * 255)) for v in colorsys.hls_to_rgb(th, nl, ns))
        v = fourbpp.rgb555((r, g, b)) | (raw[i] & 0x8000)
        if v == 0:
            v = 1 << 10                    # never mint the transparent key
        out[i * 2:i * 2 + 2] = v.to_bytes(2, "little")
    return bytes(out), len(pick)


def _fit_paint_image(pixels, width, height, panel):
    """Paint texels laid out on a 256x256 page.

    A full-page image is used as it stands. An image the size of `panel` is
    stamped into that rectangle. Anything else is nearest-neighbour scaled to
    the page, which is what a differently sized recolour of the whole atlas is.
    """
    import fourbpp

    page = fourbpp.PAGE_W * fourbpp.PAGE_H

    if (width, height) == (fourbpp.PAGE_W, fourbpp.PAGE_H):
        return pixels

    if panel and len(panel) == 4:
        x0, y0, x1, y1 = panel
        rw, rh = x1 - x0, y1 - y0

        if rw > 0 and rh > 0 and (width, height) == (rw, rh):
            out = [(0, 0, 0)] * page

            for y in range(rh):
                for x in range(rw):
                    out[(y0 + y) * fourbpp.PAGE_W + (x0 + x)] = pixels[y * width + x]

            return out

    out = []

    for y in range(fourbpp.PAGE_H):
        sy = min(height - 1, int(y * height / fourbpp.PAGE_H)) if height else 0

        for x in range(fourbpp.PAGE_W):
            sx = min(width - 1, int(x * width / fourbpp.PAGE_W)) if width else 0
            out.append(pixels[sy * width + sx] if pixels else (0, 0, 0))

    return out


def paint_from_image(base_row, indices, paint_pixels, mask=None, protect=(),
                     tolerance=8):
    """One CLUT row recoloured from a paletted image of the same texels.

    Index N takes the snapped colour the paint image shows on the texels that
    already use N (the mode when they disagree). 0x0000, protected colours and
    indices the image never covers stay as they are, and every entry keeps its
    STP bit. The page's indices are not rewritten: the engine still draws the
    same 4bpp, with this row in the paint column.
    """
    def near(a, b):
        return all(abs(x - y) <= tolerance for x, y in zip(a, b))

    rgb = fourbpp.clut_rgb(base_row)
    raw = [int.from_bytes(base_row[i * 2:i * 2 + 2], "little") for i in range(16)]
    votes = {}

    for i, index in enumerate(indices):
        if i >= len(paint_pixels):
            break

        if mask is not None and not mask[i]:
            continue

        votes.setdefault(index, []).append(fourbpp.snap555(paint_pixels[i]))

    colours = []

    for i, colour in enumerate(rgb):
        if raw[i] == 0 or any(near(colour, p) for p in protect):
            colours.append(colour)
            continue

        samples = votes.get(i) or []

        if not samples:
            colours.append(colour)
            continue

        colours.append(max(set(samples), key=samples.count))

    new = bytearray(fourbpp.rgb_to_clut(colours))

    for i in range(16):
        if raw[i] & 0x8000:
            word = int.from_bytes(new[i * 2:i * 2 + 2], "little") | 0x8000
            new[i * 2:i * 2 + 2] = word.to_bytes(2, "little")

        if raw[i] == 0:
            new[i * 2:i * 2 + 2] = b"\x00\x00"

    return bytes(new)


def _rect_mask(rects, length):
    import fourbpp

    mask = [False] * length

    for rect in rects or []:
        x0, y0, x1, y1 = rect

        for y in range(max(0, y0), min(fourbpp.PAGE_H, y1)):
            row = y * fourbpp.PAGE_W

            for x in range(max(0, x0), min(fourbpp.PAGE_W, x1)):
                if row + x < length:
                    mask[row + x] = True

    return mask


# civ_clut[row][texid][0..5] (cars.c). Slot 0 is the page CLUT
# (texture_cluts[set][texid], refilled when the model is built). Slots 1..5
# are LUMP_PALLET records: ProcessPalletLump stores palette k at column k+1,
# and DrawCarObject selects the column with cp->ap.palette. Five is the
# engine's paint budget; a sixth extra would clamp into slot 5.
PAINT_SLOTS = 5


def clut_rows_from_entry(entry):
    """The CLUT rows at the front of a page entry, before the decomp stream.

    A Driver 1 page is `[int npalettes][npalettes x 32-byte rows][decomp]`.
    Row i is texture id i (`texture_cluts[set][i]`). Rows the geometry never
    names can still be other body colours of a row it does name.
    """
    if not entry or len(entry) < 4:
        return []

    npal = struct.unpack_from("<i", entry, 0)[0]

    if npal < 1 or npal > 64 or 4 + npal * 32 > len(entry):
        return []

    return [bytes(entry[4 + i * 32:4 + (i + 1) * 32]) for i in range(npal)]


def _colour_words(row):
    """16 RGB555 colours, STP (bit 15) stripped. 0 stays the transparent key."""
    return [struct.unpack_from("<H", row, i * 2)[0] & 0x7FFF
            for i in range(fourbpp.CLUT_ROW_ENTRIES)]


def _shared_body_count(base_row, other_row):
    """How many non-transparent entries the two rows agree on, and how many differ."""
    shared = 0
    shared_vals = set()
    diff = 0

    for a, b in zip(_colour_words(base_row), _colour_words(other_row)):
        if a == b and a != 0:
            shared += 1
            shared_vals.add(a)
        elif a != b:
            diff += 1

    return shared, len(shared_vals), diff


def is_body_recolour(base_row, other_row):
    """True when `other_row` is the same CLUT with the body paint changed.

    Trim, glass and lights stay put (several shared entries, more than one
    distinct colour) and at least two entries move. An identical row is not a
    variant, and a lamp or detail row that shares nothing with the body is not
    one either — those stay texture ids, not paint columns.
    """
    if not base_row or not other_row or base_row == other_row:
        return False

    shared, kinds, diff = _shared_body_count(base_row, other_row)
    return shared >= 4 and kinds >= 2 and diff >= 2


def body_colour_slots(rows, used_ids=None, body_rows=None, occupied=None):
    """Page CLUT rows that belong in civ_clut paint columns.

    `rows[i]` is texture id i. Slot 0 of that id is this row, from the page.
    Each returned palette k (0..4) is the CLUT `ProcessPalletLump` writes at
    civ_clut column k+1.

    `used_ids` is the texture ids the geometry names. Only rows it does not
    name are candidates, so a lamp id is not consumed as a body paint. None
    means every later row may be a recolour of an earlier one (a page whose
    polys are not in hand).

    `body_rows` overrides detection: `[base_id, extra, extra, ...]`. The first
    index is the texture id whose page row is already civ slot 0. A list of
    just that id disables detection (no extras).

    `occupied` is `{texnum: {palette: clut}}` already emitted for this page
    (the source LUMP_PALLET). A palette-0 record that is byte-identical to the
    page row is the slot-0 colour again; it is dropped so the first real extra
    can take civ slot 1. Any other occupied palette is kept.

    Returns `(slots, info)`. `slots` is `{texnum: {palette: clut}}` for texture
    ids that gained at least one extra, and is the FULL palette map for that
    id (previous records plus the extras). `info` is
    `[{texnum, kept, overflow}, ...]` — `overflow` is extras past the five
    paint slots.
    """
    rows = list(rows or [])
    info = []

    if body_rows is not None:
        groups = {}

        if body_rows:
            base = int(body_rows[0])
            groups[base] = [int(v) for v in body_rows[1:]]
    else:
        groups = _detect_body_variants(rows, used_ids)

    slots = {}

    for base, variants in groups.items():
        if not 0 <= base < len(rows):
            continue

        have = dict((occupied or {}).get(base) or {})
        base_row = bytes(rows[base])
        extras = []

        for index in variants:
            if not 0 <= index < len(rows):
                continue

            clut = bytes(rows[index])

            if clut == base_row or clut in have.values() or clut in extras:
                continue

            extras.append(clut)

        if not extras:
            continue

        if have.get(0) == base_row:
            del have[0]

        kept = 0
        overflow = 0
        added = []

        for clut in extras:
            slot = next((p for p in range(PAINT_SLOTS) if p not in have), None)

            if slot is None:
                overflow += 1
                continue

            have[slot] = clut
            added.append(slot)
            kept += 1

        if kept:
            slots[base] = have
            info.append({"texnum": base, "kept": kept, "overflow": overflow,
                         "slots": [p + 1 for p in added]})
        elif overflow:
            info.append({"texnum": base, "kept": 0, "overflow": overflow,
                         "slots": []})

    return slots, info


def _detect_body_variants(rows, used_ids):
    """{base texnum: [variant row indices]} for recolours of a body row."""
    n = len(rows)

    if used_ids is None:
        bases = list(range(n))
        candidates = list(range(n))
    else:
        bases = [i for i in sorted(used_ids) if 0 <= i < n]
        known = set(bases)
        candidates = [i for i in range(n) if i not in known]

    groups = {}

    for index in candidates:
        best = None
        best_score = -1

        for base in bases:
            if base == index:
                continue

            if used_ids is None and base >= index:
                continue

            if not is_body_recolour(rows[base], rows[index]):
                continue

            score = _shared_body_count(rows[base], rows[index])[0]

            if best is None or score > best_score or (score == best_score and base < best):
                best = base
                best_score = score

        if best is not None:
            groups.setdefault(best, []).append(index)

    return groups


def body_paint_note(page_set, item):
    """One line naming the civ_clut columns a page's extra body CLUTs filled."""
    kept = item["kept"]
    overflow = item["overflow"]

    if kept <= 0:
        return ("set %d tex %d: %d extra body CLUT(s) do not fit "
                "(civ_clut columns are 0..5, and palette+1 clamps at 5)"
                % (page_set, item["texnum"], overflow))

    civ = ", ".join(str(s) for s in item.get("slots") or [])
    pals = ", ".join(str(s - 1) for s in item.get("slots") or [])
    text = ("set %d tex %d: %d extra body CLUT(s) -> civ_clut slot(s) %s "
            "(LUMP_PALLET palette %s; cp->ap.palette cycles them, "
            "slot 0 stays the page CLUT)"
            % (page_set, item["texnum"], kept, civ, pals))

    if overflow:
        text += (" ; %d more body CLUT(s) do not fit "
                 "(civ_clut columns are 0..5, and palette+1 clamps at 5)"
                 % overflow)

    return text


def apply_body_colour_slots(records, rows, used_ids, tpageindex, body_rows=None):
    """`(records, notes)`. Extra body CLUTs join this page's LUMP_PALLET rows.

    Records for a texture id that gained no extra are returned as they were,
    in the same order, so a page with no body variants bakes the same bytes.
    """
    occupied = {}

    for record in records:
        occupied.setdefault(record["texnum"], {})[record["palette"]] = record["clut"]

    slots, info = body_colour_slots(rows, used_ids, body_rows, occupied)

    if not slots:
        return list(records), [body_paint_note(tpageindex, item)
                               for item in info if item["overflow"]]

    out = []
    written = set()

    for record in records:
        texnum = record["texnum"]

        if texnum not in slots:
            out.append(record)
            continue

        if texnum in written:
            continue

        written.add(texnum)

        for palette, clut in sorted(slots[texnum].items()):
            nxt = dict(record)
            nxt["palette"] = palette
            nxt["clut"] = clut
            nxt["paint"] = "body%d" % (palette + 1)
            out.append(nxt)

    for texnum, have in slots.items():
        if texnum in written:
            continue

        for palette, clut in sorted(have.items()):
            out.append({"palette": palette, "texnum": texnum,
                        "tpageindex": tpageindex, "clut_number": -1,
                        "clut": clut, "paint": "body%d" % (palette + 1)})

    return out, [body_paint_note(tpageindex, item) for item in info]


def rows_from_pages(bank_dir, sets=None, paints=None, regions=None, notes=None):
    """Palette rows built from the PAGES themselves.

    A page that carries one CLUT row emits texnum 0, as before. A page that was
    split — one row per texture id — emits every row with `texnum` equal to
    that id, which is what `texture_cluts[set][id]` and `civ_clut[row][id]`
    index. Paint variants still use lump `palette` k for column k+1; an image
    paint rebuilds the row from the image instead of a hue shift.

    `regions` is `{set: [{id, rect}, ...]}` from the page plan, so an image
    paint for one id only samples that id's texels.

    A page with no declared paints still contributes its extra body CLUTs.
    Rows that are a recolour of an earlier row (or `body_rows` on the texture,
    `[base_id, extra, ...]`) fill civ_clut slots 1..5 for that texture id.
    Slot 0 stays the page's own row. More than five extras are reported in
    `notes` and not emitted. Declared `paints` already own those columns.
    """
    import pages1

    out = []

    for page_set in sorted(sets or []):
        path = os.path.join(bank_dir, "pages", "PAGE_%d.bin" % page_set)

        if not os.path.isfile(path):
            continue

        with open(path, "rb") as f:
            blob = f.read()

        try:
            rows, raw = pages1.read_page(blob, 0, len(blob))
        except ValueError:
            continue

        if not rows:
            continue

        indices = None
        spec = (paints or {}).get(page_set)
        pieces = (regions or {}).get(page_set) or []
        body_rows = None if not spec else spec.get("body_rows")

        if not spec or not spec.get("paints"):
            # one record per CLUT row the page actually stores. A single-row
            # page is exactly the old record (texnum 0, the page's row 0).
            # Extra body colours on the same page fill the paint columns of
            # the row they recolour; that row's own bytes stay civ slot 0.
            slots, info = body_colour_slots(rows, body_rows=body_rows)

            for texnum, row in enumerate(rows):
                have = slots.get(texnum)

                if not have:
                    out.append({"palette": 0, "texnum": texnum,
                                "tpageindex": page_set, "clut_number": -1,
                                "clut": row})
                    continue

                for palette, clut in sorted(have.items()):
                    out.append({"palette": palette, "texnum": texnum,
                                "tpageindex": page_set, "clut_number": -1,
                                "clut": clut, "paint": "body%d" % (palette + 1)})

            if notes is not None:
                notes.extend(body_paint_note(page_set, item) for item in info)

            continue

        if body_rows is not None and notes is not None:
            notes.append("set %d: paints fill civ_clut slots 1..5, so body_rows "
                         "is not also read" % page_set)

        # Paint variants: lump palette p fills civ_clut column p+1, column 0 is
        # the page's own CLUT (the original colours), so variant k -> palette k.
        for k, (name, paint) in enumerate(spec["paints"]):
            if isinstance(paint, (tuple, list)) and len(paint) == 3 and not isinstance(paint[0], dict):
                paint = {"rgb": tuple(paint)}

            want_id = paint.get("id") if isinstance(paint, dict) else None

            for texnum, row in enumerate(rows):
                if want_id is not None and texnum != want_id:
                    continue

                if isinstance(paint, dict) and paint.get("image"):
                    if indices is None:
                        import fourbpp
                        indices = fourbpp.decode_indices(raw)

                    import carbank_page
                    width, height, src, _alpha = carbank_page.load_pixels(paint["image"])
                    laid = _fit_paint_image(src, width, height, paint.get("panel"))
                    rects = [p["rect"] for p in pieces if p.get("id") == texnum]

                    if paint.get("panel") and not rects:
                        rects = [paint["panel"]]

                    # A split page's index N means a different colour on each
                    # row. Voting every texel into a row that does not own
                    # those texels mixes the panels. A gap row (no island)
                    # keeps the page's own colours.
                    if not rects and len(rows) > 1 and want_id is None:
                        continue

                    mask = _rect_mask(rects, len(indices)) if rects else None
                    painted = paint_from_image(row, indices, laid, mask,
                                               spec.get("protect") or ())
                else:
                    colour = paint["rgb"] if isinstance(paint, dict) else paint
                    painted, _n = paint_row(row, colour, spec.get("body"),
                                            spec.get("protect") or ())

                out.append({"palette": k, "texnum": texnum, "tpageindex": page_set,
                            "clut_number": -1, "clut": painted, "paint": name})

    return out


def palette_doc(rows, total_cluts=None):
    """The palette.json shape for a list of rows, so the two stay in step."""
    return {"total_cluts": total_cluts or len(rows),
            "sets": sorted({r["tpageindex"] for r in rows}),
            "records": [{"palette": r["palette"], "texnum": r["texnum"],
                         "tpageindex": r["tpageindex"],
                         "clut_number": -1,
                         "clut": r["clut"].hex(),
                         "rgb": [list(c) for c in fourbpp.clut_rgb(r["clut"])]}
                        for r in rows]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", help="palette.json, or the bank.json beside it")
    ap.add_argument("-o", "--out", help="the pallet.bin to write "
                                        "(default: beside the palette.json)")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 if the file on disk differs")
    ap.add_argument("--sets", metavar="N,N", help="keep only these texture sets")
    ap.add_argument("--from-pages", action="store_true",
                    help="build the rows from the PAGES' own palette (row 0) "
                         "instead of palette.json")
    args = ap.parse_args(argv)

    js, default_out, subject, bank_sets = _resolve(args.source)
    out = os.path.abspath(args.out) if args.out else default_out
    sets = None

    if args.sets:
        sets = {int(v) for v in args.sets.replace(" ", "").split(",") if v}

    bank_dir = os.path.dirname(js)

    if args.from_pages:
        return _from_pages(args, bank_dir, out, subject, bank_sets, sets)

    doc = load_json(js)

    # the sets this bank really carries come from the LEVEL, not from bank.json's
    # computed half (which a bake to a scratch folder leaves stale)
    if bank_sets is not None and subject.lower().endswith(".lev"):
        bank_sets = page_sets(subject) or bank_sets

    wanted = set(sets or [r.get("tpageindex") for r in doc.get("records") or []])
    stranded = 0

    if bank_sets:
        stranded = len([r for r in doc.get("records") or []
                        if r.get("tpageindex") in wanted
                        and r.get("tpageindex") not in bank_sets])
        wanted &= bank_sets

    rows = rows_from_doc(doc, wanted)
    body = exportd2.build_palette_body(rows, doc.get("total_cluts"))
    used = sorted({r["tpageindex"] for r in rows})

    if args.check:
        return _check(subject, js, body, rows, wanted or set(used), stranded)

    old = None

    if os.path.isfile(out):
        with open(out, "rb") as f:
            old = f.read()

    with open(out, "wb") as f:
        f.write(body)

    print("wrote %s (%d bytes: %d rows inline, sets %s)"
          % (out, len(body), len(rows), ",".join(str(s) for s in used)))

    if old is not None:
        print("  %s" % ("the row data is unchanged" if old == body else
                        "CHANGED - re-bake the bank"))

    return 0


def _from_pages(args, bank_dir, out, subject, bank_sets, sets):
    """--from-pages: the pages ARE the palette, so there is nothing to edit."""
    wanted = sets or set()

    if not wanted:
        lev = subject if os.path.isfile(subject) else             os.path.join(bank_dir, "bank.json")
        wanted = page_sets(subject) if subject.lower().endswith(".lev") else set()

        if not wanted:
            wanted = bank_sets or set()

    notes = []
    rows = rows_from_pages(bank_dir, wanted or None, notes=notes)

    for line in notes:
        print("  " + line)

    if not rows:
        raise SystemExit("no pages under %s/pages to take a palette from"
                         % bank_dir)

    body = exportd2.build_palette_body(rows, len(rows))
    used = sorted({r["tpageindex"] for r in rows})

    if args.check:
        return _check(subject, "the pages", body, rows, set(used), 0)

    with open(out, "wb") as f:
        f.write(body)

    # keep palette.json in step, so the next --check compares like with like
    doc_path = os.path.join(bank_dir, "palette.json")

    if os.path.isfile(doc_path):
        with open(doc_path, "w", encoding="utf-8") as f:
            json.dump(palette_doc(rows), f, indent=1)
            f.write(chr(10))

    print("wrote %s from the pages (%d bytes: %d row(s), sets %s)"
          % (out, len(body), len(rows), ",".join(str(s) for s in used)))
    print("  every index now paints the colour its page was quantised with")

    return 0


def _check(subject, source, body, rows, sets, stranded):
    if not os.path.isfile(subject):
        print("%s: missing (expected %d row(s))" % (subject, len(rows)))
        return 1

    have, _total = embedded_palette_body(subject, sets)

    if have != body:
        print("%s does NOT hold what %s says (%d rows in the bank, %d expected)"
              % (os.path.basename(subject), os.path.basename(str(source)),
                 len(exportd2.d2_palette_from_bytes(have)["records"]),
                 len(rows)))
        return 1

    print("%s matches %s (%d row(s), sets %s)"
          % (os.path.basename(subject), os.path.basename(str(source)),
             len(rows), ",".join(str(s) for s in sorted(sets)) or "none"))

    if stranded:
        print("  note: %d palette row(s) in the JSON are for sets this bank does "
              "not carry, so they never reach it" % stranded)

    return 0


if __name__ == "__main__":
    sys.exit(main())
