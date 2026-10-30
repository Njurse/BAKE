#!/usr/bin/env python3
"""carbank_palette.py - bake palette.json into the bank's LUMP_PALLET source.

    python carbank_palette.py banks/firebird/palette.json -o banks/firebird/pallet.bin
    python carbank_palette.py banks/firebird/bank.json --check

The bank bake reads `raw.pallet` (a binary LUMP_PALLET body) and never the
`palette.json` beside it, so editing a colour in the JSON used to change
nothing. This is the missing half of that pair: it writes the body the bake
reads, from the rows the JSON describes.

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


def rows_from_pages(bank_dir, sets=None, paints=None):
    """Palette rows built from the PAGES themselves: page row 0 of each set.

    The bank's LUMP_PALLET is what paints a car's texel indices, and a page is
    quantised against its OWN 16 colours - row 0. Carrying the donor city's rows
    instead leaves every index painted by a colour chosen for different art,
    which is how a panel ends up wearing a leftover magenta or a stranger's grey.
    One record per page, and the two agree by construction.
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
            rows, _raw = pages1.read_page(blob, 0, len(blob))
        except ValueError:
            continue

        if not rows:
            continue

        spec = (paints or {}).get(page_set)

        if not spec or not spec.get("paints"):
            out.append({"palette": 0, "texnum": 0, "tpageindex": page_set,
                        "clut_number": -1, "clut": rows[0]})
            continue

        # Paint variants: lump palette p fills civ_clut column p+1, column 0 is
        # the page's own CLUT (the original colours), so variant k -> palette k.
        for k, (name, colour) in enumerate(spec["paints"]):
            row, _n = paint_row(rows[0], colour, spec.get("body"),
                                spec.get("protect") or ())
            out.append({"palette": k, "texnum": 0, "tpageindex": page_set,
                        "clut_number": -1, "clut": row, "paint": name})

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

    rows = rows_from_pages(bank_dir, wanted or None)

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
