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

The knobs are the same ones a bank declares (`sounds.<name>` in bank.json) and
they are applied in the same order, so what you hear from this tool is what the
bake will put in the bank:

    python wav2vag.py engine.wav -o engine.vag --loop --loop-from 0.4 \
        --trim 0,3.2 --level 0.85 --gain-db -1.5

    --rate HZ        resample to an exact rate         (default: the source's)
    --level 0..1     target peak                      (default: 0.999, retail's)
    --force          scale even when already at level (see vag.normalise)
    --gain-db N      relative gain, applied before the level
    --trim A,B       keep only A..B seconds
    --fade-out S     linear fade over the last S seconds
    --loop           mark the sample looping
    --loop-from S    where the loop starts (never block 0 - see vag.encode)
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


def parse_trim(text):
    """`--trim 0.5,2` or `--trim 0.5` (to the end) as a [start, end] list."""
    parts = [p.strip() for p in text.split(",") if p.strip()]

    if not 1 <= len(parts) <= 2:
        raise argparse.ArgumentTypeError("expected START[,END] in seconds")

    try:
        values = [float(p) for p in parts]
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not a number of seconds" % text)

    if len(values) == 2 and values[1] <= values[0]:
        raise argparse.ArgumentTypeError("END is not after START")

    return values


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", help="a WAV to encode, or a .VAG to decode")
    ap.add_argument("-o", "--out", required=True, help="the file to write")
    ap.add_argument("--decode", action="store_true",
                    help="read a .VAG and write a WAV")
    ap.add_argument("--rate", type=int, default=None,
                    help="resample to this rate (encode only)")
    ap.add_argument("--loop", action="store_true",
                    help="mark the sample as looping (encode only)")
    ap.add_argument("--loop-from", type=float, default=0.0, metavar="S",
                    help="seconds into the sample where the loop starts")
    ap.add_argument("--level", type=float, default=None, metavar="0..1",
                    help="target peak (default %.3f, retail's own level)"
                    % vag.DEFAULT_PEAK)
    ap.add_argument("--rms", type=float, default=None, metavar="0..1",
                    help="loudness target (default %.2f; retail measures "
                         "0.29-0.39)" % vag.DEFAULT_RMS)
    ap.add_argument("--force", action="store_true",
                    help="rescale even when the sample is already at level")
    ap.add_argument("--gain-db", type=float, default=0.0, metavar="N",
                    help="relative gain, applied before --level")
    ap.add_argument("--trim", type=parse_trim, default=None, metavar="A[,B]",
                    help="keep only A..B seconds")
    ap.add_argument("--fade-out", type=float, default=0.0, metavar="S",
                    help="linear fade over the last S seconds")
    args = ap.parse_args(argv)

    if args.decode:
        data, rate = read_vag(args.source)
        samples = vag.decode(data)
        vag.write_wav(args.out, samples, rate)
        print("decoded %s -> %s (%d samples @ %d Hz)"
              % (args.source, args.out, len(samples), rate))
        return 0

    samples, rate = vag.read_wav(args.source)
    source_rate = rate

    if args.rate:
        samples = vag.resample(samples, rate, args.rate)
        rate = args.rate

    rate, clamped = vag.fit_rate(rate)

    if clamped:
        samples = vag.resample(samples, source_rate, clamped)
        print("  rate %d is outside %d..%d; resampled to %d"
              % (clamped, vag.MIN_RATE, vag.MAX_RATE, rate))

    if args.trim:
        before = len(samples)
        samples = vag.trim(samples, args.trim[0],
                           args.trim[1] if len(args.trim) > 1 else None, rate)
        print("  trimmed %.3fs -> %.3fs" % (before / float(rate),
                                           len(samples) / float(rate)))

    if args.fade_out:
        samples = vag.fade_out(samples, args.fade_out, rate)
        print("  faded the last %.3fs" % args.fade_out)

    if args.gain_db:
        samples = vag.gain_db(samples, args.gain_db)
        print("  %+.1f dB" % args.gain_db)

    samples = vag.remove_dc(samples)
    before = vag.stats(samples)
    print("  source: peak %d (%.0f%%), rms %.0f%% crest %.1f%s"
          % (before["peak"], 100.0 * before["peak"] / 32767.0,
             100.0 * before["rms"] / 32767.0, before["crest"],
             ", CLIPPED" if before["clipped"] else ""))

    level = vag.DEFAULT_PEAK if args.level is None else args.level
    target = vag.DEFAULT_RMS if args.rms is None else args.rms
    samples, before, applied = vag.level(samples, peak=level, rms=target,
                                         force=args.force)
    after = vag.stats(samples)

    if applied != 1.0:
        print("  level: x%.3f (%+.1f dB) - peak %d, rms %.0f%%"
              % (applied, 20.0 * __import__("math").log10(applied),
                 after["peak"], 100.0 * after["rms"] / 32767.0))
    else:
        print("  level: left alone (peak %.0f%%, rms %.0f%%; --force to rescale)"
              % (100.0 * after["peak"] / 32767.0,
                 100.0 * after["rms"] / 32767.0))

    loop_from = int(args.loop_from * rate)
    data, count, rms = vag.encode(samples, loop=args.loop, loop_from=loop_from)
    write_vag(args.out, data, rate, os.path.splitext(os.path.basename(args.out))[0])

    full = 32768.0
    print("wrote %s: %d samples @ %d Hz -> %d bytes (%d blocks)%s"
          % (args.out, count, rate, len(data), len(data) // vag.BLOCK_BYTES,
             ", looping from %.3fs" % args.loop_from if args.loop else ""))
    print("  encode error: rms %.1f of %.0f (%.1f dB)"
          % (rms, full, 20 * __import__("math").log10(full / rms) if rms else 99))

    return 0


if __name__ == "__main__":
    sys.exit(main())
