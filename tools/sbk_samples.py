#!/usr/bin/env python3
"""sbk_samples.py - decode every sample in a D1 VOICES.BLK / EURO-43 SOUND.SBK.

Both files are a directory of 16-byte (address, length, loop, rate) entries
followed by PSX VAG/ADPCM data. This writes one mono 16-bit WAV per entry and a
manifest, so a bank can be listened to and the engine/idle/horn indices chosen
before add_car_sounds.py is run.

    python sbk_samples.py SOUND.SBK --out DIR
"""
import argparse
import os
import struct
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import add_car_sounds


def decode_vag(data, frames=None):
    """PSX VAG/ADPCM -> list of signed 16-bit samples.

    16-byte blocks: (predictor << 4 | shift), flags, then 28 4-bit samples,
    low nibble first. Predictor coefficients are in 1/64 units.
    """
    coef = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))
    nframes = len(data) // 16 if frames is None else min(frames, len(data) // 16)
    out = []
    hist1 = hist2 = 0
    for f in range(nframes):
        off = f * 16
        hdr = data[off]
        pred = (hdr >> 4) & 0x0F
        shift = hdr & 0x0F
        f0, f1 = coef[pred] if pred < len(coef) else (0, 0)
        for i in range(28):
            b = data[off + 2 + (i >> 1)]
            nib = (b >> 4) if (i & 1) else (b & 0x0F)
            if nib >= 8:
                nib -= 16
            s = (nib << 12) >> shift
            s += (hist1 * f0 + hist2 * f1 + 32) >> 6
            if s > 32767:
                s = 32767
            elif s < -32768:
                s = -32768
            hist2 = hist1
            hist1 = s
            out.append(s)
    return out


def write_wav(path, samples, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(samples), *samples))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bank")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    voices = add_car_sounds.read_d1_voices(args.bank)
    if not voices:
        raise SystemExit("not a D1 bank: %s" % args.bank)
    if not os.path.isdir(args.out):
        os.makedirs(args.out)

    print("%s: %d samples (%s)" % (args.bank, len(voices["entries"]), voices["kind"]))
    lines = ["bank=%s kind=%s samples=%d" % (args.bank, voices["kind"],
                                             len(voices["entries"]))]
    for i, (addr, length, loop, rate) in enumerate(voices["entries"]):
        data = voices["vag"][addr:addr + length]
        pcm = decode_vag(data)
        name = "sample_%02d_len%05d_rate%05d_%s.wav" % (
            i, length, rate, "loop" if loop else "once")
        path = os.path.join(args.out, name)
        write_wav(path, pcm, rate)
        secs = len(pcm) / float(rate)
        flags = "%02x..%02x" % (data[1], data[-15]) if len(data) >= 16 else "?"
        line = "  %2d  len=%6d  rate=%5d  loop=%d  %6.2fs  flags=%s  %s" % (
            i, length, rate, loop, secs, flags, name)
        print(line)
        lines.append("index=%d length=%d rate=%d loop=%d seconds=%.3f flags=%s file=%s" % (
            i, length, rate, loop, secs, flags, name))

    # The current add_car_sounds mappings, so the obvious candidates are named.
    if voices["kind"] == "sbk":
        lines.append("current engine candidates: %s" % (add_car_sounds.SOUND_SBK_ENGINE,))
        lines.append("current idle candidates:   %s" % (add_car_sounds.SOUND_SBK_IDLE,))
        lines.append("current horn candidates:   %s" % (add_car_sounds.SOUND_SBK_HORN,))
    else:
        lines.append("retail engine candidates: %s" % (add_car_sounds.D1_ENGINE_SAMPLE,))
        lines.append("retail horn candidates:   %s" % (add_car_sounds.D1_HORN_SAMPLE,))
    with open(os.path.join(args.out, "manifest.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("  manifest: %s" % os.path.join(args.out, "manifest.txt"))


if __name__ == "__main__":
    sys.exit(main())
