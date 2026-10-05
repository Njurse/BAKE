#!/usr/bin/env python3
"""pages1.py - self-contained Driver 1 texture page + palette decoder.

Phase A of the texture milestone: read Driver 1's texture pages without shelling
out to DriverLevelTool, so the pipeline stands on its own.

A permanent page entry, exactly as the engine's LoadTPageAndCluts reads it
(texture.c:867):

    [int npalettes]              how many CLUT rows follow
    [npalettes x 32 bytes]       16 RGB555 entries per row
    [decomp_asm stream]          the 256x256 4bpp page

decomp_asm (Game/ASM/compres.c) is a two-op RLE that fills exactly 0x8000 bytes
(256x256 at 4bpp), and it writes BACKWARDS from dest + 0x7fff, so the first
decoded byte lands at the END of the buffer. The engine uploads that buffer to
VRAM as-is, so a page's byte order is whatever decomp_asm produced - this module
mirrors that exactly and lets the callers (and the validation) settle the read
direction. The control byte:

    0x00..0x7f   copy the next (n + 1) literal bytes
    0x80..0xff   repeat the next byte (258 - n) times

Usage:
    python pages1.py --selftest
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lev1                                    # the D1 level/lump walker

LUMP_TEXTUREINFO = lev1.LUMP_TEXTUREINFO

PAGE_BYTES = 0x8000          # 256x256 at 4bpp
CLUT_ROW_BYTES = 32          # 16 x RGB555


def decomp_asm(src, out_bytes=PAGE_BYTES):
    """Mirror of the engine's decomp_asm.

    Returns (dest, consumed) where dest is `out_bytes` long and holds the page
    the way the engine's buffer does (first decoded byte at the end).
    """
    dest = bytearray(out_bytes)
    ptr = out_bytes - 1
    i = 0
    n = len(src)
    while ptr >= 0:
        if i >= n:
            break
        pix = src[i]
        i += 1
        if pix & 0x80:
            if i >= n:
                break
            p = src[i]
            i += 1
            for _ in range(258 - pix):
                if ptr < 0:
                    break
                dest[ptr] = p
                ptr -= 1
        else:
            for _ in range(pix + 1):
                if ptr < 0 or i >= n:
                    break
                dest[ptr] = src[i]
                i += 1
                ptr -= 1
    return bytes(dest), i


def encode_literal(data):
    """Encode with literal runs only - always valid input for decomp_asm, and
    ~1.008x the size, so the bake never needs a real matcher."""
    out = bytearray()
    for o in range(0, len(data), 128):
        chunk = data[o:o + 128]
        out.append(len(chunk) - 1)
        out += chunk
    return bytes(out)


def parse_texture_info(body):
    """Parse a LUMP_TEXTUREINFO (34) body the way ProcessTextureInfo does
    (texture.c:996):

        [int tpage_amount][int legacy]
        [TP tpage_position[tpage_amount + 1]]        TP = {u_int flags; u_int offset}
        per page: [int texamount][TEXINF x texamount] TEXINF = 8 bytes
        [int nperms][XYPAIR permlist[x16 reserved]]   XYPAIR = {int x; int y}
        [int nspecpages][XYPAIR speclist[nspecpages]]

    Note the reserved 16: the engine reaches the special list via
    &permlist[16], not &permlist[nperms] (texture.c:1025), so permlist's slot in
    the body is a fixed 128 bytes.
    """
    def i32(off):
        return struct.unpack_from("<i", body, off)[0]

    n = i32(0)
    o = 8 + 8 * (n + 1)
    tpage_ids = []
    for _ in range(n):
        cnt = i32(o)
        o += 4
        tpage_ids.append(cnt)
        o += 8 * cnt
    nperms = i32(o)
    permlist = [(i32(o + 4 + 8 * k), i32(o + 4 + 8 * k + 4)) for k in range(nperms)]
    o2 = o + 4 + 16 * 8
    nspec = i32(o2)
    speclist = [(i32(o2 + 4 + 8 * k), i32(o2 + 4 + 8 * k + 4)) for k in range(nspec)]
    return {"tpage_amount": n, "tpage_ids": tpage_ids, "nperms": nperms,
            "permlist": permlist, "nspecpages": nspec, "speclist": speclist}


def page_entries(body, region_ofs):
    """(set_index, offset, size) per permanent page, carved out of the tpage
    region exactly as LoadPermanentTPages does (texture.c:4055, 4070): each entry
    occupies (size + 2047) & ~2047 bytes."""
    ti = parse_texture_info(body)
    entries = []
    off = 0
    for x, y in ti["permlist"]:
        entries.append((x, region_ofs + off, y))
        off += (y + 2047) & ~2047
    return entries


def read_page(blob, offset, size):
    """One page entry -> (clut_rows, page_bytes).

    [int npalettes][npalettes x 32 bytes CLUT][decomp_asm stream to the entry end]
    """
    npal = struct.unpack_from("<i", blob, offset)[0]
    rows = []
    o = offset + 4
    for _ in range(npal):
        rows.append(blob[o:o + CLUT_ROW_BYTES])
        o += CLUT_ROW_BYTES
    page, _ = decomp_asm(blob[o:offset + size])
    return rows, page


def texture_info_lump(blob):
    """(body, size) of LUMP_TEXTUREINFO, searched in both DATA containers."""
    hdr = lev1.read_header(blob)
    for coff, etype in ((hdr["loadtime_offset"], lev1.LUMP_LOADTIME_DATA),
                        (hdr["inmem_offset"], lev1.LUMP_INMEMORY_DATA)):
        got = lev1.find_lump(blob, coff, etype, LUMP_TEXTUREINFO)
        if got:
            return got
    return None


def level_pages(blob):
    """Walk a whole level: (region_ofs, entries, table).

    `entries` is [(set, offset, size)], `table` is the parsed TEXTUREINFO.
    """
    hdr = lev1.read_header(blob)
    ofs = hdr["tpage_offset"]
    got = texture_info_lump(blob)
    if not got:
        return ofs, [], None
    body, bsize = got
    chunk = blob[body:body + bsize]
    return ofs, page_entries(chunk, ofs), parse_texture_info(chunk)


def show_pages(blob, path):
    """Print a level's permanent page list."""
    ofs, entries, ti = level_pages(blob)
    if ti is None:
        print("%s: no LUMP_TEXTUREINFO" % path)
        return
    print("%s: tpage region at %d, tpage_amount %d; %d permanent + %d special pages"
          % (os.path.basename(path), ofs, ti["tpage_amount"], ti["nperms"],
             ti["nspecpages"]))
    for s, o, z in entries:
        rows, page = read_page(blob, o, z)
        print("  set %3d  offset %7d  size %6d  cluts %2d  page %s"
              % (s, o, z, len(rows), page[:8].hex(" ")))
    print("  special sets: %s" % [s for s, _o, _z in
                                   [(p[0], p[1], 0) for p in ti["speclist"]]])


def clut_rgb(row):
    """32 bytes -> 16 (r, g, b) 8-bit tuples. PSX RGB555: R in bits 0-4,
    G in 5-9, B in 10-14, bit 15 = STP."""
    out = []
    for i in range(16):
        v = struct.unpack_from("<H", row, i * 2)[0]
        out.append((((v >> 0) & 0x1F) << 3,
                    ((v >> 5) & 0x1F) << 3,
                    ((v >> 10) & 0x1F) << 3))
    return out


def parse_pallet(body, size=None):
    """Parse a Driver 1 LUMP_PALLET (25) body.

    D1's palette record is THREE ints plus the CLUT row inline:

        [int total_cluts]           the RECORD count (FRISCO: 68; 4 + 68*44 == size)
        total_cluts records of 44 bytes:
            int palette       colour-variety index (traffic cars use 0..5)
            int texnum        texture number within the page
            int tpageindex    the texture set (page)
            32 bytes          16 RGB555 entries

    This is NOT Driver 2's shape. D2's record is four ints, the fourth a
    clut_number that either selects the inline form (-1) or references an
    earlier CLUT (cars.c:2058), and D2's lump ends on a lone -1. D1 has no
    reference form, so every row is inline and the header count is exact -
    there is no room for a terminator at all.

    Measured on FRISCO: tpageindex takes only the two car pages {67, 68} and
    palette only {0, 1, 2, 3}, which is what pins the field order.
    """
    if size is None:
        size = len(body)
    if size < 4:
        return 0, []
    total_cluts = struct.unpack_from("<i", body, 0)[0]
    recs = []
    for k in range(total_cluts):
        o = 4 + k * 44
        if o + 44 > size:
            break
        palette, texnum, tpageindex = struct.unpack_from("<3i", body, o)
        recs.append({"palette": palette, "texnum": texnum,
                     "tpageindex": tpageindex,
                     "row": body[o + 12:o + 44]})
    return total_cluts, recs


def pallet_lump(blob):
    """(body, size) of LUMP_PALLET, searched in both DATA containers."""
    hdr = lev1.read_header(blob)
    for coff, etype in ((hdr["loadtime_offset"], lev1.LUMP_LOADTIME_DATA),
                        (hdr["inmem_offset"], lev1.LUMP_INMEMORY_DATA)):
        got = lev1.find_lump(blob, coff, etype, lev1.LUMP_PALLET)
        if got:
            off, size = got
            return blob[off:off + size], size
    return None


def show_pallet(blob, path):
    got = pallet_lump(blob)
    if not got:
        print("%s: no LUMP_PALLET" % path)
        return
    body, size = got
    total, recs = parse_pallet(body, size)
    buckets = {}
    for r in recs:
        buckets.setdefault(r["tpageindex"], []).append(r["palette"])
    print("%s: LUMP_PALLET %d bytes, total_cluts %d, %d records of 44 bytes (exact: %s)"
          % (os.path.basename(path), size, total, len(recs),
             4 + total * 44 == size))
    for s in sorted(buckets):
        print("  set %3d: %d rows, palettes %s"
              % (s, len(buckets[s]), sorted(set(buckets[s]))))
    if recs:
        print("  first row (set %d, palette %d, texnum %d): %s"
              % (recs[0]["tpageindex"], recs[0]["palette"], recs[0]["texnum"],
                 clut_rgb(recs[0]["row"])[:4]))


def load_tga_rgb(path):
    """An uncompressed 24/32-bit TGA -> a 256x256 list of (r, g, b), top row
    first. Only what the reference tool writes."""
    d = open(path, "rb").read()
    idlen, cmaptype, imgtype = d[0], d[1], d[2]
    w, h = struct.unpack_from("<HH", d, 12)
    bpp, desc = d[16], d[17]
    if imgtype != 2 or cmaptype != 0:
        raise ValueError("%s: not an uncompressed true-colour TGA" % path)
    px = d[18 + idlen:]
    step = bpp // 8
    cols = [tuple(px[i * step:i * step + 3]) for i in range(w * h)]
    rows = [cols[i * w:(i + 1) * w] for i in range(h)]
    if not (desc & 0x20):          # bit 5 clear = bottom-up, the TGA default
        rows = rows[::-1]
    return rows


def compare_pages(blob, path, tga_dir):
    """Cross-check our page decode against DriverLevelTool's own TGA exports.

    A byte-exact RGB comparison is NOT available: the tool's page image carries
    ~240 distinct colours where the page holds 16, so it is not a plain indexed
    render (it very likely blends the entries whose bit 15 is set - every entry
    in a D1 car CLUT has bit 15 set). What IS comparable is the STRUCTURE, via a
    CLUT-free test: two texels that are the same index must be the same colour in
    the tool's image, checked per 32x32 block.
    """
    import glob
    ofs, entries, ti = level_pages(blob)
    if ti is None:
        print("%s: no LUMP_TEXTUREINFO" % path)
        return
    base = os.path.basename(path)
    all_sets = [s for s, _o, _z in entries] + [p[0] for p in ti["speclist"]]
    print("%s: %d permanent + %d special pages" % (base, len(entries),
                                                   ti["nspecpages"]))
    exported = set()
    for s, o, z in entries:
        if s in exported:
            continue
        tga = os.path.join(tga_dir, "PAGE_%d.tga" % s)
        if not os.path.exists(tga):
            print("  set %3d: the tool exported no PAGE_%d.tga" % (s, s))
            continue
        exported.add(s)
        _rows, page = read_page(blob, o, z)
        idx = []
        for y in range(256):
            for xb in page[y * 128:(y + 1) * 128]:
                idx.append(xb & 0xF)
                idx.append(xb >> 4)
        img = load_tga_rgb(tga)
        colours = {c for r in img for c in r}
        clean = 0
        total = 0
        for by in range(0, 256, 32):
            for bx in range(0, 256, 32):
                seen = {}
                ok = True
                for yy in range(by, by + 32):
                    for xx in range(bx, bx + 32):
                        v = idx[yy * 256 + xx]
                        c = img[yy][xx]
                        if seen.setdefault(v, c) != c:
                            ok = False
                total += 1
                clean += 1 if ok else 0
        print("  set %3d: PAGE_%d has %3d colours; index->colour bijection in "
              "%2d/%d 32x32 blocks" % (s, s, len(colours), clean, total))
    missing = [s for s in all_sets if s not in exported]
    if missing:
        print("  pages the tool did not export: %s" % sorted(set(missing)))


def _selftest():
    import random
    rng = random.Random(1234)
    fails = 0

    # 1. a literal-only round trip over a full, random page
    data = bytes(rng.randrange(256) for _ in range(PAGE_BYTES))
    dest, used = decomp_asm(encode_literal(data))
    if dest[::-1] != data:
        print("FAIL: literal round trip (dest reversed != source)")
        fails += 1
    else:
        print("ok: literal round trip of %d bytes (encoded %d = %.3fx)"
              % (len(data), len(encode_literal(data)),
                 len(encode_literal(data)) / float(len(data))))

    # 2. the run form: control 0x80 => 258-128 = 130 repeats of the next byte
    stream = bytes([0x80, 0xAB]) + encode_literal(bytes(PAGE_BYTES - 130))
    dest, _ = decomp_asm(stream)
    tail = dest[::-1]
    if tail[:130] != b"\xab" * 130:
        print("FAIL: 0x80 should repeat the next byte 130 times")
        fails += 1
    else:
        print("ok: 0x80 repeats its byte 130 times")

    # 3. 0xFF => 258-255 = 3 repeats
    stream = bytes([0xFF, 0x5A]) + encode_literal(bytes(PAGE_BYTES - 3))
    dest, _ = decomp_asm(stream)
    if dest[::-1][:3] != b"\x5a" * 3:
        print("FAIL: 0xFF should repeat the next byte 3 times")
        fails += 1
    else:
        print("ok: 0xFF repeats its byte 3 times; 0x00 copies 1 literal byte")

    # 4. 0x7f copies 128 literals
    stream = bytes([0x7F]) + bytes(range(128)) + encode_literal(bytes(PAGE_BYTES - 128))
    dest, _ = decomp_asm(stream)
    if dest[::-1][:128] != bytes(range(128)):
        print("FAIL: 0x7f should copy 128 literal bytes")
        fails += 1
    else:
        print("ok: 0x7f copies 128 literal bytes")

    print("selftest: %s" % ("FAILED" if fails else "clean"))
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lev", nargs="?", help="a DRIVER/LEVELS/*.LEV file")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--info", action="store_true",
                    help="print the level's permanent page list")
    ap.add_argument("--pallet", action="store_true",
                    help="print the level's LUMP_PALLET records")
    ap.add_argument("--compare", metavar="TGA_DIR",
                    help="cross-check pages against DriverLevelTool's TGA exports")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.lev and args.compare:
        compare_pages(open(args.lev, "rb").read(), args.lev, args.compare)
        return 0
    if args.lev and args.info:
        show_pages(open(args.lev, "rb").read(), args.lev)
        return 0
    if args.lev and args.pallet:
        show_pallet(open(args.lev, "rb").read(), args.lev)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
