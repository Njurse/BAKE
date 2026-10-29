#!/usr/bin/env python3
"""wav2vag.py - encode a WAV to PSX VAG/ADPCM, or decode one back.

    python wav2vag.py engine.wav -o engine.vag --loop
    python wav2vag.py engine.vag --decode -o engine.wav

`vag.py` does the work; this is the file end of it. The .VAG written here is the
standalone PSX container (48-byte "VAGp" header) so it can be checked in an
audio tool or fed to another bake. The sound a bank embeds is the raw block
stream, which is what `vag.encode` returns and what the D2 dynamic-bank format
points at - see `exportd2.build_sounds_body`.

Rate: the header carries the source's own rate when the SPU range allows it, and
the tool says so when it has to clamp or resample.
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import vag

VAG_MAGIC = b"VAGp"
VAG_VERSION = 0x00000020


def read_vag(path):
    """(raw blocks, rate) from a .VAG file, header or not."""
    with open(path, "rb") as f:
        blob = f.read()

    if blob[:4] == VAG_MAGIC and len(blob) >= 48:
        size, rate = struct.unpack_from("<II", blob, 12)
        data = blob[48:48 + size]
        return data, rate

    return blob, vag.DEFAULT_RATE


def write_vag(path, data, rate, name=""):
    header = bytearray(48)
    header[0:4] = VAG_MAGIC
    struct.pack_into("<I", header, 4, VAG_VERSION)
    struct.pack_into("<I", header, 12, len(data))
    struct.pack_into("<I", header, 16, rate)
    header[32:48] = name.encode("ascii", "replace")[:16].ljust(16, b"\x00")

    with open(path, "wb") as f:
        f.write(bytes(header))
        f.write(data)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", help="a WAV to encode, or a .VAG to decode")
    ap.add_argument("-o", "--out", required=True, help="the file to write")
    ap.add_argument("--decode", action="store_true",
                    help="read a .VAG and write a WAV")
    ap.add_argument("--rate", type=int, default=None,
                    help="override the sample rate (encode only)")
    ap.add_argument("--loop", action="store_true",
                    help="mark the sample as looping (encode only)")
    args = ap.parse_args(argv)

    if args.decode:
        data, rate = read_vag(args.source)
        samples = vag.decode(data)
        vag.write_wav(args.out, samples, rate)
        print("decoded %s -> %s (%d samples @ %d Hz)"
              % (args.source, args.out, len(samples), rate))
        return 0

    samples, rate = vag.read_wav(args.source)

    if args.rate:
        samples = vag.resample(samples, rate, args.rate)
        rate = args.rate

    rate, clamped = vag.fit_rate(rate)

    if clamped:
        samples = vag.resample(samples, clamped, rate)
        print("  rate %d is outside %d..%d; resampled to %d"
              % (clamped, vag.MIN_RATE, vag.MAX_RATE, rate))

    data, count, rms = vag.encode(samples)
    write_vag(args.out, data, rate, os.path.splitext(os.path.basename(args.out))[0])

    full = 32768.0
    print("wrote %s: %d samples @ %d Hz -> %d bytes (%d blocks)%s"
          % (args.out, count, rate, len(data), len(data) // vag.BLOCK_BYTES,
             ", looping" if args.loop else ""))
    print("  encode error: rms %.1f of %.0f (%.1f dB)"
          % (rms, full, 20 * __import__("math").log10(full / rms) if rms else 99))

    return 0


if __name__ == "__main__":
    sys.exit(main())
