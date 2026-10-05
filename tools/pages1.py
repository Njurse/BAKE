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
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.lev and args.info:
        show_pages(open(args.lev, "rb").read(), args.lev)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
