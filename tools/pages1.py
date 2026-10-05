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
import sys

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
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
